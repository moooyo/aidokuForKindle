//! Tracking account validation, explicit book binding, and background synchronization.

use super::*;
use crate::tracking::{
    Account, Media, PendingProgress, SearchReport, Service, SyncReport, TrackingClient,
    TrackingFailure, TrackingPairRequest, TrackingState, completed_chapter_number,
};

#[derive(Default)]
pub(crate) struct TrackingAccounts {
    state: TrackingState,
    loaded: bool,
    generation: u64,
    cancellation: Arc<AtomicU64>,
    sequence: u64,
    inflight: Option<TrackingAttempt>,
    next_poll: Option<Instant>,
    search: Option<TrackingSearch>,
    manual_flush: bool,
}

struct TrackingAttempt {
    sequence: u64,
    lifecycle: u64,
}

struct TrackingSearch {
    service: Service,
    book_id: String,
    generation: u64,
    media: Vec<Media>,
}

pub(crate) struct TrackingResult {
    sequence: u64,
    generation: u64,
    outcome: TrackingOutcome,
}

enum TrackingOutcome {
    Connected(Result<Account, TrackingFailure>),
    Searched {
        service: Service,
        book_id: String,
        report: Result<SearchReport, TrackingFailure>,
    },
    Synced {
        pending: PendingProgress,
        report: Result<SyncReport, TrackingFailure>,
    },
}

impl App {
    fn load_tracking_accounts(&mut self) -> Result<()> {
        if !self.tracking_accounts.loaded {
            let state = TrackingState::load(&self.store)?;
            self.tracking_accounts.state = state;
            self.tracking_accounts.loaded = true;
        }
        Ok(())
    }

    pub(crate) fn sync_tracking_accounts_state(&self) {
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            let account = |service| self.tracking_accounts.state.accounts.get(&service);
            state.set_anilist_connected(account(Service::AniList).is_some());
            state.set_myanimelist_connected(account(Service::MyAnimeList).is_some());
            state.set_anilist_status(
                account(Service::AniList)
                    .map(|account| account.user_name.clone())
                    .unwrap_or_else(|| tr("not_configured"))
                    .into(),
            );
            state.set_myanimelist_status(
                account(Service::MyAnimeList)
                    .map(|account| account.user_name.clone())
                    .unwrap_or_else(|| tr("not_configured"))
                    .into(),
            );
            state.set_tracking_busy(self.tracking_accounts.inflight.is_some());
        }
    }

    pub(crate) fn tracking_action(&mut self, action: &str, value: &str) -> Result<bool> {
        if !matches!(
            action,
            "tracking-connect"
                | "tracking-disconnect"
                | "tracking-bind"
                | "tracking-select"
                | "tracking-unbind"
                | "tracking-sync-now"
        ) {
            return Ok(false);
        }
        self.load_tracking_accounts()?;
        match action {
            "tracking-connect" => {
                if Service::parse(value).is_none() {
                    self.notify(tr("tracking_invalid_mapping"));
                } else {
                    self.open_pairing()?;
                    self.notify(tr("tracking_configuration_help"));
                }
            }
            "tracking-disconnect" => {
                if let Some(service) = Service::parse(value) {
                    self.cancel_tracking_work();
                    self.tracking_accounts.state.disconnect(service);
                    self.tracking_accounts.state.save(&self.store)?;
                    self.tracking_accounts.search = None;
                    self.notify(tr("saved"));
                }
            }
            "tracking-bind" => {
                let Some(service) = Service::parse(value) else {
                    return Ok(true);
                };
                if !self.tracking_accounts.state.accounts.contains_key(&service) {
                    self.notify(tr("tracking_no_account"));
                    self.open_pairing()?;
                } else if let Some(book_id) = self.current_book_id.clone() {
                    if let Some(book) = self.store.book(&book_id)? {
                        self.open_keyboard(
                            &format!("tracking-search:{}:{book_id}", service.id()),
                            &book.title,
                        )?;
                        if let Some(ui) = self.ui.upgrade() {
                            ui.global::<State>()
                                .set_modal_title(tr("tracking_search").into());
                        }
                    }
                }
            }
            "tracking-select" => self.select_tracking_media(value)?,
            "tracking-unbind" => {
                if let (Some(service), Some(book_id)) =
                    (Service::parse(value), self.current_book_id.clone())
                {
                    self.cancel_tracking_work();
                    self.tracking_accounts.state.unbind(&book_id, service);
                    self.tracking_accounts.state.save(&self.store)?;
                    self.open_tracking_choices()?;
                }
            }
            "tracking-sync-now" => {
                self.queue_current_tracking_progress()?;
                self.tracking_accounts.manual_flush = true;
                for pending in &mut self.tracking_accounts.state.pending {
                    if !pending.auth_required {
                        pending.next_attempt = 0;
                    }
                }
                self.tracking_accounts.state.save(&self.store)?;
                self.tracking_accounts.next_poll = None;
                self.poll_tracking()?;
            }
            _ => {}
        }
        self.sync_tracking_accounts_state();
        Ok(true)
    }

    pub(crate) fn tracking_choice(&mut self, id: &str) -> Result<bool> {
        for action in [
            "tracking-connect",
            "tracking-disconnect",
            "tracking-bind",
            "tracking-select",
            "tracking-unbind",
        ] {
            if let Some(value) = id.strip_prefix(&format!("{action}:")) {
                return self.tracking_action(action, value);
            }
        }
        if id == "tracking-sync-now" {
            return self.tracking_action(id, "");
        }
        Ok(false)
    }

    pub(crate) fn open_tracking_choices(&mut self) -> Result<()> {
        self.load_tracking_accounts()?;
        let mut rows = Vec::new();
        let bindings = self
            .current_book_id
            .as_ref()
            .and_then(|id| self.tracking_accounts.state.bindings.get(id));
        for service in [Service::AniList, Service::MyAnimeList] {
            if let Some(account) = self.tracking_accounts.state.accounts.get(&service) {
                let binding = bindings.and_then(|bindings| {
                    bindings.iter().find(|binding| {
                        binding.service == service && binding.user_id == account.user_id
                    })
                });
                rows.push((
                    format!("tracking-bind:{}", service.id()),
                    format!("{} · {}", service.name(), tr("tracking_search")),
                    binding
                        .map(|binding| binding.media.title.clone())
                        .unwrap_or_else(|| tr("tracking_not_bound")),
                ));
                if binding.is_some() {
                    rows.push((
                        format!("tracking-unbind:{}", service.id()),
                        format!("{} · {}", service.name(), tr("tracking_unbind")),
                        String::new(),
                    ));
                }
            } else {
                rows.push((
                    format!("tracking-connect:{}", service.id()),
                    format!("{} · {}", service.name(), tr("configure")),
                    tr("tracking_token_required"),
                ));
            }
        }
        rows.push((
            "tracking-sync-now".into(),
            tr("tracking_sync_now"),
            format!("{}", self.tracking_accounts.state.pending.len()),
        ));
        self.show_choices(&tr("tracking"), "tracking", rows)
    }

    pub(crate) fn tracking_keyboard_submit(&mut self, target: &str, text: &str) -> Result<bool> {
        let Some(target) = target.strip_prefix("tracking-search:") else {
            return Ok(false);
        };
        let Some((service, book_id)) = target.split_once(':') else {
            return Ok(true);
        };
        let Some(service) = Service::parse(service) else {
            return Ok(true);
        };
        self.keyboard.set_text("");
        self.keyboard_target.clear();
        self.modal.clear();
        self.modal_context.clear();
        self.load_tracking_accounts()?;
        if self.options.offline
            || self.options.demo
            || self.suspended
            || self.resume_network_pending
        {
            self.notify(tr("offline"));
            return Ok(true);
        }
        if self.tracking_accounts.inflight.is_some() {
            self.notify(tr("loading"));
            return Ok(true);
        }
        let Some(account) = self.tracking_accounts.state.accounts.get(&service).cloned() else {
            self.notify(tr("tracking_no_account"));
            return Ok(true);
        };
        if self.store.book(book_id)?.is_none() {
            return Ok(true);
        }
        let query = text.trim().to_owned();
        let book_id = book_id.to_owned();
        let client = self.tracking_client()?;
        let (sequence, generation) = self.next_tracking_attempt();
        self.spawn_local_job(true, JobKind::Tracking, move || {
            let report = client.search(&account, &query, chrono::Utc::now().timestamp());
            Ok(JobResult::Tracking(TrackingResult {
                sequence,
                generation,
                outcome: TrackingOutcome::Searched {
                    service,
                    book_id,
                    report,
                },
            }))
        })?;
        self.mark_tracking_attempt(sequence);
        self.show_choices(
            &tr("tracking_search"),
            "tracking-search-loading",
            vec![("tracking-wait".into(), tr("loading"), String::new())],
        )?;
        self.choice_disabled.insert("tracking-wait".into());
        self.sync_choice_rows();
        Ok(true)
    }

    fn select_tracking_media(&mut self, value: &str) -> Result<()> {
        let Some((service, id)) = value.split_once(':') else {
            return Ok(());
        };
        let Some(service) = Service::parse(service) else {
            return Ok(());
        };
        let Ok(id) = id.parse::<u32>() else {
            return Ok(());
        };
        let Some(search) = self.tracking_accounts.search.as_ref() else {
            return Ok(());
        };
        if search.service != service
            || search.generation != self.tracking_accounts.generation
            || self.current_book_id.as_deref() != Some(search.book_id.as_str())
            || self.store.book(&search.book_id)?.is_none()
        {
            return Ok(());
        }
        let Some(media) = search.media.iter().find(|media| media.id == id).cloned() else {
            return Ok(());
        };
        let book_id = search.book_id.clone();
        self.cancel_tracking_work();
        self.tracking_accounts
            .state
            .bind(&book_id, service, media)?;
        self.tracking_accounts.state.save(&self.store)?;
        self.tracking_accounts.search = None;
        self.notify(tr("tracking_bound"));
        self.open_tracking_choices()
    }

    pub(crate) fn connect_tracking(&mut self, request: TrackingPairRequest) -> Result<()> {
        self.load_tracking_accounts()?;
        if let Err(failure) = request.validate() {
            self.notify(tr(failure.label()));
            return Ok(());
        }
        if self.options.offline
            || self.options.demo
            || self.suspended
            || self.resume_network_pending
        {
            self.notify(tr("offline"));
            return Ok(());
        }
        if self.tracking_accounts.inflight.is_some() {
            self.notify(tr("loading"));
            return Ok(());
        }
        let client = self.tracking_client()?;
        let (sequence, generation) = self.next_tracking_attempt();
        self.spawn_local_job(true, JobKind::Tracking, move || {
            let result = client.connect(&request, chrono::Utc::now().timestamp());
            Ok(JobResult::Tracking(TrackingResult {
                sequence,
                generation,
                outcome: TrackingOutcome::Connected(result),
            }))
        })?;
        self.mark_tracking_attempt(sequence);
        Ok(())
    }

    fn next_tracking_attempt(&mut self) -> (u64, u64) {
        self.tracking_accounts.sequence = self.tracking_accounts.sequence.wrapping_add(1);
        (
            self.tracking_accounts.sequence,
            self.tracking_accounts.generation,
        )
    }

    fn tracking_client(&self) -> Result<TrackingClient> {
        Ok(TrackingClient::new()
            .map_err(|failure| anyhow::anyhow!(tr(failure.label())))?
            .with_fence(
                self.network_control.clone(),
                self.tracking_accounts.cancellation.clone(),
                self.tracking_accounts.generation,
            ))
    }

    pub(crate) fn cancel_tracking_work(&mut self) {
        self.tracking_accounts.generation = self.tracking_accounts.generation.wrapping_add(1);
        self.tracking_accounts
            .cancellation
            .store(self.tracking_accounts.generation, Ordering::Release);
        self.tracking_accounts.search = None;
        self.sync_tracking_accounts_state();
    }

    fn mark_tracking_attempt(&mut self, sequence: u64) {
        self.tracking_accounts.inflight = Some(TrackingAttempt {
            sequence,
            lifecycle: self.lifecycle_generation,
        });
        self.sync_tracking_accounts_state();
    }

    pub(crate) fn accept_tracking_result(&mut self, result: TrackingResult) -> Result<()> {
        if !self
            .tracking_accounts
            .inflight
            .as_ref()
            .is_some_and(|attempt| attempt.sequence == result.sequence)
        {
            return Ok(());
        }
        self.tracking_accounts.inflight = None;
        if result.generation != self.tracking_accounts.generation {
            self.sync_tracking_accounts_state();
            return Ok(());
        }
        match result.outcome {
            TrackingOutcome::Connected(result) => match result {
                Ok(account) => {
                    self.tracking_accounts.state.connect(account);
                    self.tracking_accounts.state.save(&self.store)?;
                    self.notify(tr("connected"));
                }
                Err(failure) => self.notify(tr(failure.label())),
            },
            TrackingOutcome::Searched {
                service,
                book_id,
                report,
            } => {
                let report = match report {
                    Ok(report) => report,
                    Err(failure) => {
                        if self.modal_context == "tracking-search-loading" {
                            self.modal.clear();
                            self.modal_context.clear();
                        }
                        self.notify(tr(failure.label()));
                        self.sync_tracking_accounts_state();
                        return Ok(());
                    }
                };
                self.retain_refreshed_account(&report.account);
                self.tracking_accounts.state.save(&self.store)?;
                match report.result {
                    Ok(media)
                        if self.current_book_id.as_deref() == Some(book_id.as_str())
                            && self.store.book(&book_id)?.is_some()
                            && self.modal_context == "tracking-search-loading" =>
                    {
                        if media.is_empty() {
                            self.modal.clear();
                            self.modal_context.clear();
                            self.notify(tr("no_results"));
                            self.sync_tracking_accounts_state();
                            return Ok(());
                        }
                        let rows = media
                            .iter()
                            .map(|media| {
                                (
                                    format!("tracking-select:{}:{}", service.id(), media.id),
                                    media.title.clone(),
                                    format!("{} · {}", service.name(), media.id),
                                )
                            })
                            .collect();
                        self.tracking_accounts.search = Some(TrackingSearch {
                            service,
                            book_id,
                            generation: self.tracking_accounts.generation,
                            media,
                        });
                        self.show_choices(&tr("tracking_search"), "tracking-results", rows)?;
                    }
                    Err(failure) => {
                        if self.modal_context == "tracking-search-loading" {
                            self.modal.clear();
                            self.modal_context.clear();
                        }
                        self.notify(tr(failure.label()));
                    }
                    _ => {}
                }
            }
            TrackingOutcome::Synced { pending, report } => {
                let result = match report {
                    Ok(report) => {
                        self.retain_refreshed_account(&report.account);
                        report.result
                    }
                    Err(failure) => Err(failure),
                };
                match result {
                    Ok(progress) => {
                        self.tracking_accounts.state.finish(&pending, progress);
                        self.tracking_accounts.state.service_ready_at.insert(
                            pending.service,
                            chrono::Utc::now().timestamp().saturating_add(6),
                        );
                        self.notify(tr("tracking_sync_complete"));
                    }
                    Err(failure) => {
                        if self.prefs.tracking_queue_offline {
                            self.tracking_accounts.state.fail(
                                &pending,
                                &failure,
                                chrono::Utc::now().timestamp(),
                            );
                        } else {
                            self.tracking_accounts
                                .state
                                .pending
                                .retain(|entry| entry.id != pending.id);
                        }
                        self.notify(tr(failure.label()));
                    }
                }
                self.tracking_accounts.state.save(&self.store)?;
            }
        }
        self.sync_tracking_accounts_state();
        Ok(())
    }

    fn retain_refreshed_account(&mut self, account: &Account) {
        if self
            .tracking_accounts
            .state
            .accounts
            .get(&account.service)
            .is_some_and(|current| current.user_id == account.user_id)
        {
            self.tracking_accounts
                .state
                .accounts
                .insert(account.service, account.clone());
        }
    }

    pub(crate) fn poll_tracking(&mut self) -> Result<()> {
        if self
            .tracking_accounts
            .next_poll
            .is_some_and(|deadline| Instant::now() < deadline)
        {
            return Ok(());
        }
        self.tracking_accounts.next_poll = Some(Instant::now() + Duration::from_secs(1));
        self.load_tracking_accounts()?;
        if self
            .tracking_accounts
            .inflight
            .as_ref()
            .is_some_and(|attempt| attempt.lifecycle != self.lifecycle_generation)
        {
            self.tracking_accounts.inflight = None;
        }
        self.sync_tracking_accounts_state();
        if self.suspended
            || self.resume_network_pending
            || self.options.offline
            || self.options.demo
            || self.platform.network_policy_blocked()
            || self.tracking_accounts.inflight.is_some()
            || (!self.prefs.tracking_auto_sync && !self.tracking_accounts.manual_flush)
        {
            return Ok(());
        }
        let now = chrono::Utc::now().timestamp();
        let pending = self
            .tracking_accounts
            .state
            .pending
            .iter()
            .find(|pending| {
                !pending.auth_required
                    && pending.next_attempt <= now
                    && self
                        .tracking_accounts
                        .state
                        .service_ready_at
                        .get(&pending.service)
                        .is_none_or(|deadline| *deadline <= now)
                    && self
                        .tracking_accounts
                        .state
                        .accounts
                        .get(&pending.service)
                        .is_some_and(|account| account.user_id == pending.user_id)
            })
            .cloned();
        let Some(pending) = pending else {
            if self.tracking_accounts.state.pending.is_empty() {
                self.tracking_accounts.manual_flush = false;
            }
            return Ok(());
        };
        if self.store.book(&pending.book_id)?.is_none() {
            self.tracking_accounts
                .state
                .unbind(&pending.book_id, pending.service);
            self.tracking_accounts.state.save(&self.store)?;
            return Ok(());
        }
        let account = self.tracking_accounts.state.accounts[&pending.service].clone();
        let client = self.tracking_client()?;
        let (sequence, generation) = self.next_tracking_attempt();
        self.spawn_local_job(false, JobKind::Tracking, move || {
            let report = client.sync(&account, &pending, chrono::Utc::now().timestamp());
            Ok(JobResult::Tracking(TrackingResult {
                sequence,
                generation,
                outcome: TrackingOutcome::Synced { pending, report },
            }))
        })?;
        self.mark_tracking_attempt(sequence);
        Ok(())
    }

    pub(crate) fn queue_displayed_tracking_progress(&mut self) -> Result<()> {
        if !self.prefs.tracking_auto_sync || self.prefs.incognito {
            return Ok(());
        }
        let chapter_id = self
            .displayed_reader
            .as_ref()
            .map(|displayed| displayed.session.chapter_id.clone());
        let Some(chapter_id) = chapter_id else {
            return Ok(());
        };
        let Some(progress) = self
            .committed_progress
            .as_ref()
            .filter(|progress| progress.chapter_id == chapter_id && progress.completed)
        else {
            return Ok(());
        };
        let book_id = progress.book_id.clone();
        let Some(chapter) = self.store.chapter(&chapter_id)? else {
            return Ok(());
        };
        let Some(number) = completed_chapter_number(chapter.number) else {
            return Ok(());
        };
        self.enqueue_tracking_progress(&book_id, number)
    }

    fn queue_current_tracking_progress(&mut self) -> Result<()> {
        let Some(book_id) = self.current_book_id.clone() else {
            return Ok(());
        };
        let number = self
            .store
            .chapters(&book_id)?
            .into_iter()
            .filter(|chapter| chapter.read)
            .filter_map(|chapter| completed_chapter_number(chapter.number))
            .max();
        if let Some(number) = number {
            self.enqueue_tracking_progress(&book_id, number)?;
        }
        Ok(())
    }

    fn enqueue_tracking_progress(&mut self, book_id: &str, number: u32) -> Result<()> {
        self.load_tracking_accounts()?;
        if !self.prefs.tracking_queue_offline
            && (self.options.offline || self.options.demo || self.resume_network_pending)
        {
            return Ok(());
        }
        if self
            .tracking_accounts
            .state
            .enqueue(book_id, number, chrono::Utc::now().timestamp())?
        {
            self.tracking_accounts.state.save(&self.store)?;
            self.tracking_accounts.next_poll = None;
        }
        Ok(())
    }
}
