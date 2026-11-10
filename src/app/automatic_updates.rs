//! Throttled library refreshes and bounded, opt-in chapter downloads.

use std::{collections::VecDeque, time::Duration};

use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};

use super::*;

const CHECK_INTERVAL: Duration = Duration::from_secs(30);
const RETRY_SECONDS: i64 = 15 * 60;
const CHARGING_DEBOUNCE_SECONDS: i64 = 15 * 60;
const SCHEDULE_KEY: &str = "automatic-updates";

/// Unknown hardware state never satisfies an explicitly enabled restriction.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(super) struct PolicyEnvironment {
    pub wifi_connected: Option<bool>,
    pub charging: Option<bool>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
struct ScheduleCheckpoint {
    last_success: i64,
    last_charging_success: i64,
    last_attempt: i64,
    last_charging_state: Option<bool>,
    charging_pending: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct UpdatePolicy {
    interval_seconds: Option<i64>,
    wifi_only: bool,
    charging_only: bool,
    skip_completed: bool,
    charging_updates: bool,
}

impl UpdatePolicy {
    fn from_preferences(preferences: &Preferences) -> Self {
        Self {
            interval_seconds: match preferences.auto_update_interval.as_str() {
                "daily" => Some(24 * 60 * 60),
                "weekly" => Some(7 * 24 * 60 * 60),
                _ => None,
            },
            wifi_only: preferences.update_wifi_only,
            charging_only: preferences.update_charging_only,
            skip_completed: preferences.update_skip_completed,
            charging_updates: preferences.charging_updates,
        }
    }

    fn enabled(&self) -> bool {
        self.interval_seconds.is_some() || self.charging_updates
    }

    fn permits(&self, environment: PolicyEnvironment) -> bool {
        (!self.wifi_only || environment.wifi_connected == Some(true))
            && (!self.charging_only || environment.charging == Some(true))
    }

    fn due(
        &self,
        checkpoint: ScheduleCheckpoint,
        now: i64,
        charging_pending: bool,
        environment: PolicyEnvironment,
    ) -> DueReasons {
        if !self.permits(environment)
            || !elapsed_at_least(now, checkpoint.last_attempt, RETRY_SECONDS)
        {
            return DueReasons::default();
        }
        DueReasons {
            scheduled: self
                .interval_seconds
                .is_some_and(|interval| elapsed_at_least(now, checkpoint.last_success, interval)),
            charging: self.charging_updates
                && charging_pending
                && environment.charging == Some(true)
                && elapsed_at_least(
                    now,
                    checkpoint.last_charging_success,
                    CHARGING_DEBOUNCE_SECONDS,
                ),
        }
    }
}

fn elapsed_at_least(now: i64, previous: i64, seconds: i64) -> bool {
    // A corrected wall clock must not postpone work until a stale future date.
    // Once attempted, the checkpoint uses the corrected clock for later retries.
    previous <= 0 || now >= previous.saturating_add(seconds) || previous > now
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct DueReasons {
    scheduled: bool,
    charging: bool,
}

impl DueReasons {
    fn any(self) -> bool {
        self.scheduled || self.charging
    }
}

struct UpdateRun {
    sequence: u64,
    generation: u64,
    start_persisted: bool,
    manager: Option<Arc<SourceManager>>,
    policy: UpdatePolicy,
    reasons: DueReasons,
    pending: Option<VecDeque<Book>>,
    checked: usize,
    failed: bool,
}

#[derive(Debug, Clone, Copy)]
struct InflightUpdate {
    id: u64,
    generation: u64,
    sequence: u64,
    kind: UpdateWork,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum UpdateWork {
    CheckpointLoad,
    CheckpointSave,
    Targets,
    Book,
}

#[derive(Debug, Clone, Copy)]
enum CheckpointPurpose {
    Observation,
    BeginRun,
    FinishRun { charging_edge: Option<u64> },
}

#[derive(Clone)]
struct UpdateGuard {
    generation: Arc<AtomicU64>,
    expected: u64,
}

impl UpdateGuard {
    fn is_current(&self) -> bool {
        self.generation.load(Ordering::Acquire) == self.expected
    }

    fn check(&self) -> Result<()> {
        ensure!(
            self.is_current(),
            "The automatic update belongs to an earlier application generation"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct AheadContext {
    book_id: String,
    chapter_id: String,
    count: u8,
    source_revision: u64,
}

/// Each refresh has one worker at a time and publishes one book per message.
/// The UI never enumerates the library or performs provider HTTP requests.
pub(super) struct AutomaticUpdates {
    checkpoint: ScheduleCheckpoint,
    loaded: bool,
    generation: u64,
    generation_token: Arc<AtomicU64>,
    sequence: u64,
    next_check: Instant,
    poll_retry_after: Option<Instant>,
    checkpoint_dirty: bool,
    manual_success: Option<i64>,
    observations_known: bool,
    charging_edge_sequence: u64,
    last_charging: Option<bool>,
    charging_pending: bool,
    run: Option<UpdateRun>,
    inflight: Option<InflightUpdate>,
    ahead_context: Option<AheadContext>,
    ahead_inflight: Option<u64>,
    ahead_retry: Option<Instant>,
}

impl Default for AutomaticUpdates {
    fn default() -> Self {
        Self {
            checkpoint: ScheduleCheckpoint::default(),
            loaded: false,
            generation: 0,
            generation_token: Arc::new(AtomicU64::new(0)),
            sequence: 0,
            next_check: Instant::now(),
            poll_retry_after: None,
            checkpoint_dirty: false,
            manual_success: None,
            observations_known: false,
            charging_edge_sequence: 0,
            last_charging: None,
            charging_pending: false,
            run: None,
            inflight: None,
            ahead_context: None,
            ahead_inflight: None,
            ahead_retry: None,
        }
    }
}

pub(super) struct UpdateResult {
    generation: u64,
    sequence: u64,
    payload: UpdatePayload,
}

enum UpdatePayload {
    CheckpointLoaded(ScheduleCheckpoint),
    CheckpointSaved {
        checkpoint: ScheduleCheckpoint,
        purpose: CheckpointPurpose,
        manual_success: Option<i64>,
    },
    Targets {
        books: Vec<Book>,
        manager: Option<Arc<SourceManager>>,
    },
    BookCommitted(bool),
}

pub(super) struct AheadResult {
    generation: u64,
    context: AheadContext,
    book: Book,
    chapters: Vec<Chapter>,
}

impl AutomaticUpdates {
    fn restore_checkpoint(&mut self, checkpoint: ScheduleCheckpoint) {
        let keep_observations = self.observations_known && self.checkpoint_dirty;
        self.checkpoint = checkpoint;
        if keep_observations {
            self.charging_pending |= checkpoint.charging_pending;
        } else {
            self.last_charging = checkpoint.last_charging_state;
            self.charging_pending = checkpoint.charging_pending;
        }
        if self.charging_pending && self.charging_edge_sequence == 0 {
            self.charging_edge_sequence = 1;
        }
        self.loaded = true;
        self.observations_known = true;
        self.refresh_checkpoint_dirty();
    }

    fn pending_checkpoint(&self) -> ScheduleCheckpoint {
        ScheduleCheckpoint {
            last_success: self.manual_success.unwrap_or(self.checkpoint.last_success),
            last_charging_state: self.last_charging,
            charging_pending: self.charging_pending,
            ..self.checkpoint
        }
    }

    fn refresh_checkpoint_dirty(&mut self) {
        self.checkpoint_dirty = self.last_charging != self.checkpoint.last_charging_state
            || self.charging_pending != self.checkpoint.charging_pending;
    }

    fn accept_saved_checkpoint(
        &mut self,
        checkpoint: ScheduleCheckpoint,
        purpose: CheckpointPurpose,
        manual_success: Option<i64>,
    ) -> Option<usize> {
        self.checkpoint = checkpoint;
        if self.manual_success == manual_success {
            self.manual_success = None;
        }
        let completed = match purpose {
            CheckpointPurpose::Observation => None,
            CheckpointPurpose::BeginRun => {
                if let Some(run) = self.run.as_mut() {
                    run.start_persisted = true;
                }
                None
            }
            CheckpointPurpose::FinishRun { charging_edge } => {
                if charging_edge == Some(self.charging_edge_sequence) {
                    self.charging_pending = false;
                }
                self.next_check = Instant::now() + CHECK_INTERVAL;
                self.run.take().map(|run| run.checked)
            }
        };
        // An observation that arrived after this worker's snapshot stays dirty.
        // In particular, completing one charging event cannot consume a new one.
        self.refresh_checkpoint_dirty();
        completed
    }

    fn guard(&self) -> UpdateGuard {
        UpdateGuard {
            generation: self.generation_token.clone(),
            expected: self.generation,
        }
    }

    fn begin_poll_attempt(&mut self, now: Instant) -> bool {
        if self.poll_retry_after.is_some_and(|retry| now < retry) {
            return false;
        }
        // Arm before the first fallible operation. An early read, parse, write,
        // or thread-start failure therefore cannot retry on every UI poll.
        self.poll_retry_after = Some(now + CHECK_INTERVAL);
        true
    }

    fn finish_poll_attempt(&mut self, succeeded: bool) {
        if succeeded {
            self.poll_retry_after = None;
        }
    }

    fn begin_schedule_check(&mut self, now: Instant) -> bool {
        if now < self.next_check {
            return false;
        }
        self.next_check = now + CHECK_INTERVAL;
        true
    }

    fn record_charging_observation(&mut self, charging: Option<bool>) {
        let previous_charging = self.last_charging;
        let previous_pending = self.charging_pending;
        self.observe_charging(charging);
        if previous_charging != self.last_charging || previous_pending != self.charging_pending {
            self.checkpoint_dirty = true;
        }
    }

    fn observe_charging(&mut self, charging: Option<bool>) {
        if let Some(charging) = charging {
            if charging && self.last_charging != Some(true) {
                self.charging_pending = true;
                self.charging_edge_sequence = self.charging_edge_sequence.wrapping_add(1);
            }
            self.last_charging = Some(charging);
        }
    }

    fn matches_result(&self, result: &UpdateResult) -> bool {
        self.run.as_ref().is_some_and(|run| {
            run.generation == result.generation
                && run.sequence == result.sequence
                && self.generation == result.generation
        })
    }

    fn finish_job(&mut self, id: u64, failed: bool) -> bool {
        if let Some(inflight) = self.inflight.filter(|inflight| inflight.id == id) {
            self.inflight = None;
            if failed {
                let retry = Instant::now() + CHECK_INTERVAL;
                self.poll_retry_after = Some(retry);
                self.next_check = retry;
            }
            if failed
                && matches!(inflight.kind, UpdateWork::Targets | UpdateWork::Book)
                && let Some(run) = self.run.as_mut().filter(|run| {
                    run.generation == inflight.generation && run.sequence == inflight.sequence
                })
            {
                run.failed = true;
                run.pending = Some(VecDeque::new());
            }
            return true;
        }
        if self.ahead_inflight == Some(id) {
            self.ahead_inflight = None;
            if failed {
                self.ahead_context = None;
                self.ahead_retry = Some(Instant::now() + Duration::from_secs(30));
            }
            return true;
        }
        false
    }

    fn invalidate(&mut self) {
        self.generation = self.generation.wrapping_add(1);
        self.generation_token
            .store(self.generation, Ordering::Release);
        self.loaded = false;
        self.run = None;
        self.ahead_context = None;
        self.next_check = Instant::now();
        // Keep occupied slots until their old workers finish. Starting another
        // worker here could multiply provider traffic across quick wake cycles.
    }
}

impl App {
    /// Call before dropping lifecycle-stale/source-stale job messages.
    pub(super) fn finish_automatic_job(&mut self, id: u64, failed: bool) -> bool {
        self.automatic_updates.finish_job(id, failed)
    }

    /// Call on sleep, restore, source reconfiguration, and manual refresh start.
    pub(super) fn invalidate_automatic_updates(&mut self) {
        self.automatic_updates.invalidate();
    }

    /// Record the intent in RAM. The optional scheduler persists it through its
    /// tracked worker, never through an application-poll database operation.
    pub(super) fn record_manual_library_update_success(&mut self) -> Result<()> {
        self.automatic_updates.manual_success = Some(chrono::Utc::now().timestamp().max(1));
        self.automatic_updates.next_check = Instant::now();
        Ok(())
    }

    /// The caller obtains hardware policy observations outside the 50 ms loop.
    pub(super) fn poll_automatic_updates(&mut self, environment: PolicyEnvironment) -> Result<()> {
        if self.shutting_down
            || self.suspended
            || self.resume_frame_pending
            || self.resume_network_pending
            || self.options.offline
            || self.options.demo
            || self.network_control.is_suspended()
            || self.platform.network_policy_blocked()
        {
            return Ok(());
        }
        let policy = UpdatePolicy::from_preferences(&self.prefs);
        if policy.enabled() && self.automatic_updates.observations_known {
            // Observation is RAM-only, including during an I/O failure cooldown.
            // Keep a rising edge latched even if the device unplugs before retry.
            self.automatic_updates
                .record_charging_observation(environment.charging);
        }
        if !self.automatic_updates.begin_poll_attempt(Instant::now()) {
            return Ok(());
        }
        let outcome = self.poll_automatic_updates_inner(environment, policy);
        self.automatic_updates.finish_poll_attempt(outcome.is_ok());
        outcome
    }

    fn poll_automatic_updates_inner(
        &mut self,
        environment: PolicyEnvironment,
        policy: UpdatePolicy,
    ) -> Result<()> {
        if self
            .automatic_updates
            .run
            .as_ref()
            .is_some_and(|run| run.policy != policy)
        {
            self.invalidate_automatic_updates();
        }
        if !policy.enabled() || self.automatic_updates.inflight.is_some() {
            return Ok(());
        }
        if !self.automatic_updates.loaded {
            if self.automatic_updates.begin_schedule_check(Instant::now()) {
                self.start_automatic_checkpoint_load()?;
            }
            return Ok(());
        }
        // Regular checkpoint I/O has its own cadence. Active runs may still
        // advance one book per completion without re-reading/writing each poll.
        let check_due =
            policy.enabled() && self.automatic_updates.begin_schedule_check(Instant::now());
        if self.automatic_updates.run.is_some() {
            if check_due
                && self.automatic_updates.checkpoint_dirty
                && self
                    .automatic_updates
                    .run
                    .as_ref()
                    .is_some_and(|run| run.start_persisted)
            {
                return self.start_automatic_checkpoint_save(
                    self.automatic_updates.pending_checkpoint(),
                    CheckpointPurpose::Observation,
                );
            }
            if policy.permits(environment) {
                if policy.charging_updates
                    && self.automatic_updates.charging_pending
                    && environment.charging == Some(true)
                    && let Some(run) = self.automatic_updates.run.as_mut()
                {
                    run.reasons.charging = true;
                }
                self.drive_automatic_update_run()?;
            }
            return Ok(());
        }
        if !check_due {
            return Ok(());
        }
        let now = chrono::Utc::now().timestamp().max(1);
        let reasons = policy.due(
            self.automatic_updates.pending_checkpoint(),
            now,
            self.automatic_updates.charging_pending,
            environment,
        );
        if !reasons.any() {
            if self.automatic_updates.checkpoint_dirty
                || self.automatic_updates.manual_success.is_some()
            {
                self.start_automatic_checkpoint_save(
                    self.automatic_updates.pending_checkpoint(),
                    CheckpointPurpose::Observation,
                )?;
            }
            return Ok(());
        }
        self.automatic_updates.sequence = self.automatic_updates.sequence.wrapping_add(1);
        self.automatic_updates.run = Some(UpdateRun {
            sequence: self.automatic_updates.sequence,
            generation: self.automatic_updates.generation,
            start_persisted: false,
            manager: None,
            policy,
            reasons,
            pending: None,
            checked: 0,
            failed: false,
        });
        self.drive_automatic_update_run()
    }

    fn start_automatic_checkpoint_load(&mut self) -> Result<()> {
        let database = self.data_dir.join("library.sqlite3");
        let guard = self.automatic_updates.guard();
        let generation = self.automatic_updates.generation;
        let sequence = self.automatic_updates.sequence;
        let id = self.spawn_local_job(false, JobKind::AutomaticUpdate, move || {
            let checkpoint = read_schedule_checkpoint(&database, &guard)?;
            Ok(JobResult::AutomaticUpdate(UpdateResult {
                generation,
                sequence,
                payload: UpdatePayload::CheckpointLoaded(checkpoint),
            }))
        })?;
        self.automatic_updates.inflight = Some(InflightUpdate {
            id,
            generation,
            sequence,
            kind: UpdateWork::CheckpointLoad,
        });
        Ok(())
    }

    fn start_automatic_checkpoint_save(
        &mut self,
        checkpoint: ScheduleCheckpoint,
        purpose: CheckpointPurpose,
    ) -> Result<()> {
        let database = self.data_dir.join("library.sqlite3");
        let guard = self.automatic_updates.guard();
        let generation = self.automatic_updates.generation;
        let sequence = self.automatic_updates.sequence;
        let manual_success = self.automatic_updates.manual_success;
        let id = self.spawn_local_job(false, JobKind::AutomaticUpdate, move || {
            write_schedule_checkpoint(&database, &guard, checkpoint)?;
            Ok(JobResult::AutomaticUpdate(UpdateResult {
                generation,
                sequence,
                payload: UpdatePayload::CheckpointSaved {
                    checkpoint,
                    purpose,
                    manual_success,
                },
            }))
        })?;
        self.automatic_updates.inflight = Some(InflightUpdate {
            id,
            generation,
            sequence,
            kind: UpdateWork::CheckpointSave,
        });
        Ok(())
    }

    fn drive_automatic_update_run(&mut self) -> Result<()> {
        if self.automatic_updates.inflight.is_some() {
            return Ok(());
        }
        let Some(run) = self.automatic_updates.run.as_mut() else {
            return Ok(());
        };
        if !run.start_persisted {
            let mut checkpoint = self.automatic_updates.pending_checkpoint();
            checkpoint.last_attempt = chrono::Utc::now().timestamp().max(1);
            return self.start_automatic_checkpoint_save(checkpoint, CheckpointPurpose::BeginRun);
        }
        let generation = run.generation;
        let sequence = run.sequence;
        let skip_completed = run.policy.skip_completed;
        let manager = run.manager.clone();
        let next = match run.pending.as_mut() {
            None => None,
            Some(pending) => {
                let Some(book) = pending.pop_front() else {
                    return self.finish_automatic_update_run();
                };
                Some(book)
            }
        };
        let collecting = next.is_none();
        let guard = self.automatic_updates.guard();
        let database = self.data_dir.join("library.sqlite3");
        let result = if let Some(requested) = next {
            self.spawn_local_job(false, JobKind::AutomaticUpdate, move || {
                guard.check()?;
                let manager = manager.context("The automatic update source manager is missing")?;
                let details = manager.details(&requested)?;
                ensure!(
                    same_book_identity(&requested, &details),
                    "The provider changed the library book identity"
                );
                guard.check()?;
                let chapters = manager.chapters(&details)?;
                guard.check()?;
                let store = Store::open_existing_for_write(&database)?;
                let committed = store.publish_automatic_library_update(
                    &requested,
                    &details,
                    &chapters,
                    || guard.is_current(),
                )?;
                guard.check()?;
                Ok(JobResult::AutomaticUpdate(UpdateResult {
                    generation,
                    sequence,
                    payload: UpdatePayload::BookCommitted(committed),
                }))
            })
        } else {
            let data_dir = self.data_dir.clone();
            self.spawn_local_job(false, JobKind::AutomaticUpdate, move || {
                guard.check()?;
                let store = Store::open_read_only(&database)?;
                let sources = store.sources()?;
                let books: Vec<_> = store
                    .books()?
                    .into_iter()
                    .filter(|book| update_target(book, &sources, skip_completed))
                    .collect();
                guard.check()?;
                let manager = if books.is_empty() {
                    None
                } else {
                    Some(Arc::new(SourceManager::new(&data_dir, &sources)?))
                };
                guard.check()?;
                Ok(JobResult::AutomaticUpdate(UpdateResult {
                    generation,
                    sequence,
                    payload: UpdatePayload::Targets { books, manager },
                }))
            })
        };
        match result {
            Ok(id) => {
                self.automatic_updates.inflight = Some(InflightUpdate {
                    id,
                    generation,
                    sequence,
                    kind: if collecting {
                        UpdateWork::Targets
                    } else {
                        UpdateWork::Book
                    },
                });
                Ok(())
            }
            Err(error) => {
                if let Some(run) = self.automatic_updates.run.as_mut() {
                    run.failed = true;
                    if collecting {
                        run.pending = Some(VecDeque::new());
                    }
                }
                Err(error)
            }
        }
    }

    fn finish_automatic_update_run(&mut self) -> Result<()> {
        let Some(run) = self.automatic_updates.run.as_ref() else {
            return Ok(());
        };
        if run.failed {
            eprintln!(
                "aidoku: automatic library update was incomplete; {} books were committed",
                run.checked
            );
            self.automatic_updates.run = None;
            self.automatic_updates.next_check = Instant::now() + CHECK_INTERVAL;
            return Ok(());
        }
        let mut checkpoint = self.automatic_updates.pending_checkpoint();
        checkpoint.last_success = chrono::Utc::now().timestamp().max(1);
        let charging_edge = if run.reasons.charging {
            checkpoint.last_charging_success = checkpoint.last_success;
            checkpoint.charging_pending = false;
            Some(self.automatic_updates.charging_edge_sequence)
        } else {
            None
        };
        self.start_automatic_checkpoint_save(
            checkpoint,
            CheckpointPurpose::FinishRun { charging_edge },
        )
    }

    /// Accept only RAM state and committed counts; every database operation is
    /// performed by the tracked background worker before this message arrives.
    pub(super) fn accept_automatic_update(&mut self, result: UpdateResult) -> Result<()> {
        if result.generation != self.automatic_updates.generation
            || result.sequence != self.automatic_updates.sequence
            || (matches!(
                &result.payload,
                UpdatePayload::Targets { .. } | UpdatePayload::BookCommitted(_)
            ) && !self.automatic_updates.matches_result(&result))
        {
            return Ok(());
        }
        match result.payload {
            UpdatePayload::CheckpointLoaded(checkpoint) => {
                self.automatic_updates.restore_checkpoint(checkpoint);
                self.automatic_updates.next_check = Instant::now();
            }
            UpdatePayload::CheckpointSaved {
                checkpoint,
                purpose,
                manual_success,
            } => {
                if let Some(checked) = self.automatic_updates.accept_saved_checkpoint(
                    checkpoint,
                    purpose,
                    manual_success,
                ) {
                    self.invalidate_storage_details();
                    self.background_ui_dirty = true;
                    if checked > 0 {
                        self.notify(
                            tr("library_update_complete").replace("{count}", &checked.to_string()),
                        );
                    }
                }
            }
            UpdatePayload::Targets { books, manager } => {
                if let Some(run) = self.automatic_updates.run.as_mut() {
                    run.pending = Some(books.into());
                    run.manager = manager;
                }
            }
            UpdatePayload::BookCommitted(committed) => {
                if committed && let Some(run) = self.automatic_updates.run.as_mut() {
                    run.checked = run.checked.saturating_add(1);
                    self.background_ui_dirty = true;
                }
            }
        }
        Ok(())
    }

    /// Call after publishing a Reader frame, or from the regular idle poll.
    pub(super) fn queue_reader_ahead_chapters(&mut self) -> Result<()> {
        if self.route != "reader" || self.prefs.ahead_chapters == 0 {
            self.automatic_updates.ahead_context = None;
        }
        if self.shutting_down
            || self.suspended
            || self.resume_frame_pending
            || self.resume_network_pending
            || self.options.offline
            || self.options.demo
            || self.network_control.is_suspended()
            || self.route != "reader"
            || self.platform.network_policy_blocked()
            || self.prefs.ahead_chapters == 0
            || self.automatic_updates.ahead_inflight.is_some()
            || self
                .automatic_updates
                .ahead_retry
                .is_some_and(|retry| Instant::now() < retry)
        {
            return Ok(());
        }
        let Some(reader) = self.reader.as_ref() else {
            return Ok(());
        };
        let current = reader.current_chapter();
        if current.source_id == "local"
            || !self.displayed_reader.as_ref().is_some_and(|displayed| {
                displayed.session.chapter_id == current.id
                    && displayed.session.book_id == current.book_id
            })
        {
            return Ok(());
        }
        let context = AheadContext {
            book_id: current.book_id.clone(),
            chapter_id: current.id.clone(),
            count: self.prefs.ahead_chapters.min(3),
            source_revision: self.source_revision,
        };
        if self.automatic_updates.ahead_context.as_ref() == Some(&context) {
            return Ok(());
        }
        let expected_current = current.clone();
        // ReaderState already supplies the exact (number, id) successor order.
        let candidates: Vec<_> = reader
            .chapters()
            .iter()
            .skip(reader.chapter_index().saturating_add(1))
            .take(usize::from(context.count))
            .cloned()
            .collect();
        let database = self.data_dir.join("library.sqlite3");
        let generation = self.automatic_updates.generation;
        let worker_context = context.clone();
        let id = self.spawn_local_job(false, JobKind::AheadDownload, move || {
            let store = Store::open_read_only(&database)?;
            let book = store
                .book(&expected_current.book_id)?
                .context("The Reader book no longer exists")?;
            let chapter = store
                .chapter(&expected_current.id)?
                .context("The Reader chapter no longer exists")?;
            ensure!(
                same_chapter_identity(&chapter, &expected_current),
                "The Reader chapter identity changed"
            );
            let chapters = ahead_download_candidates(&store, &book, &candidates)?;
            Ok(JobResult::AheadDownloads(AheadResult {
                generation,
                context: worker_context,
                book,
                chapters,
            }))
        })?;
        self.automatic_updates.ahead_context = Some(context);
        self.automatic_updates.ahead_inflight = Some(id);
        self.automatic_updates.ahead_retry = None;
        Ok(())
    }

    pub(super) fn accept_ahead_downloads(&mut self, result: AheadResult) -> Result<()> {
        if result.generation != self.automatic_updates.generation
            || self.automatic_updates.ahead_context.as_ref() != Some(&result.context)
            || self.source_revision != result.context.source_revision
            || self.prefs.ahead_chapters.min(3) != result.context.count
            || self.route != "reader"
            || self.options.offline
            || self.options.demo
            || !self.reader.as_ref().is_some_and(|reader| {
                reader.current_chapter().id == result.context.chapter_id
                    && reader.current_chapter().book_id == result.context.book_id
            })
        {
            return Ok(());
        }
        let Some(book) = self
            .store
            .book(&result.book.id)?
            .filter(|book| same_book_identity(book, &result.book))
        else {
            return Ok(());
        };
        // Recheck at most three rows so a user pause/cancel between collection
        // and publication is never converted into an automatic resume.
        let chapters = ahead_download_candidates(&self.store, &book, &result.chapters)?;
        self.downloads.enqueue_automatic(&book, &chapters)
    }
}

fn read_schedule_checkpoint(database: &Path, guard: &UpdateGuard) -> Result<ScheduleCheckpoint> {
    guard.check()?;
    let store = Store::open_read_only(database)?;
    guard.check()?;
    let checkpoint = store
        .read_ui_settings(SCHEDULE_KEY)?
        .map(serde_json::from_value)
        .transpose()
        .context("Read the automatic library update schedule")?
        .unwrap_or_default();
    guard.check()?;
    Ok(checkpoint)
}

fn write_schedule_checkpoint(
    database: &Path,
    guard: &UpdateGuard,
    checkpoint: ScheduleCheckpoint,
) -> Result<()> {
    guard.check()?;
    let store = Store::open_existing_for_write(database)?;
    guard.check()?;
    store.write_automatic_update_checkpoint(
        SCHEDULE_KEY,
        &serde_json::to_value(checkpoint)?,
        || guard.is_current(),
    )?;
    guard.check()?;
    Ok(())
}

fn same_book_identity(first: &Book, second: &Book) -> bool {
    first.id == second.id
        && first.source_id == second.source_id
        && first.remote_id == second.remote_id
}

fn same_chapter_identity(first: &Chapter, second: &Chapter) -> bool {
    first.id == second.id
        && first.book_id == second.book_id
        && first.source_id == second.source_id
        && first.remote_id == second.remote_id
}

fn update_target(book: &Book, sources: &[SourceConfig], skip_completed: bool) -> bool {
    book.in_library
        && (!skip_completed || !completed_status(&book.status))
        && sources.iter().any(|source| {
            source.id == book.source_id
                && source.enabled
                && source.kind != "local"
                && (!matches!(source.kind.as_str(), "komga" | "kavita" | "suwayomi")
                    || !source.base_url.trim().is_empty())
        })
}

fn completed_status(status: &str) -> bool {
    matches!(
        status.trim().to_ascii_lowercase().as_str(),
        "completed" | "complete" | "ended" | "finished"
    ) || status.trim() == tr("completed")
}

fn ahead_download_candidates(
    store: &Store,
    book: &Book,
    candidates: &[Chapter],
) -> Result<Vec<Chapter>> {
    let mut selected = Vec::with_capacity(candidates.len().min(3));
    for candidate in candidates.iter().take(3) {
        let Some(chapter) = store.chapter(&candidate.id)? else {
            continue;
        };
        if chapter.book_id == book.id
            && chapter.source_id == book.source_id
            && same_chapter_identity(&chapter, candidate)
            && !chapter.downloaded
            && store.download(&chapter.id)?.is_none()
        {
            selected.push(chapter);
        }
    }
    Ok(selected)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> UpdatePolicy {
        UpdatePolicy {
            interval_seconds: Some(24 * 60 * 60),
            wifi_only: false,
            charging_only: false,
            skip_completed: false,
            charging_updates: false,
        }
    }

    #[test]
    fn an_early_checkpoint_parse_failure_waits_before_retrying_any_io() {
        let start = Instant::now();
        let mut state = AutomaticUpdates::default();
        state.next_check = start;
        let mut attempts = 0;
        for poll in 0..600 {
            let now = start + Duration::from_millis(poll * 50);
            if !state.begin_poll_attempt(now) {
                continue;
            }
            assert!(state.begin_schedule_check(now));
            attempts += 1;
            // Parsing fails before loaded can be set or a run can be created.
            let outcome = serde_json::from_str::<ScheduleCheckpoint>("{");
            assert!(outcome.is_err());
            state.finish_poll_attempt(outcome.is_ok());
        }
        assert_eq!(attempts, 1);
        assert!(!state.loaded);
        assert!(state.run.is_none());
        assert!(state.begin_poll_attempt(start + CHECK_INTERVAL));
        assert!(state.begin_schedule_check(start + CHECK_INTERVAL));
        state.finish_poll_attempt(true);
        // A successful checkpoint poll releases the outer gate so an active
        // run can advance promptly, while normal checkpoint I/O stays throttled.
        assert!(state.begin_poll_attempt(start + CHECK_INTERVAL + Duration::from_millis(50)));
        assert!(!state.begin_schedule_check(start + CHECK_INTERVAL + Duration::from_millis(50)));
    }

    #[test]
    fn failed_checkpoint_writes_keep_charging_edges_in_ram_during_cooldown() {
        let start = Instant::now();
        let mut state = AutomaticUpdates::default();
        state.restore_checkpoint(ScheduleCheckpoint {
            last_success: 100,
            last_charging_state: Some(false),
            ..ScheduleCheckpoint::default()
        });
        assert!(state.begin_poll_attempt(start));
        state.record_charging_observation(Some(true));
        assert!(state.checkpoint_dirty);
        state.finish_poll_attempt(false);
        assert!(!state.begin_poll_attempt(start + Duration::from_millis(50)));
        // Losing charging before the retry must not erase the observed event.
        state.record_charging_observation(Some(false));
        assert!(state.charging_pending);
        assert_eq!(state.last_charging, Some(false));
        assert_eq!(state.checkpoint.last_charging_state, Some(false));
        assert!(!state.checkpoint.charging_pending);
        assert!(state.pending_checkpoint().charging_pending);
        assert_eq!(state.pending_checkpoint().last_success, 100);
        assert!(state.checkpoint_dirty);
        assert!(state.begin_poll_attempt(start + CHECK_INTERVAL));
    }

    #[test]
    fn background_checkpoint_helpers_reject_cancelled_writes_and_merge_dirty_reload() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("library.sqlite3");
        let _primary = Store::open(&database).unwrap();
        let mut state = AutomaticUpdates::default();
        let checkpoint = ScheduleCheckpoint {
            last_success: 100,
            last_charging_state: Some(false),
            ..ScheduleCheckpoint::default()
        };
        let old_guard = state.guard();
        write_schedule_checkpoint(&database, &old_guard, checkpoint).unwrap();
        assert_eq!(
            read_schedule_checkpoint(&database, &old_guard).unwrap(),
            checkpoint
        );
        state.restore_checkpoint(checkpoint);
        state.record_charging_observation(Some(true));
        state.invalidate();
        state.record_charging_observation(Some(false));
        assert!(!state.loaded);
        assert!(
            write_schedule_checkpoint(&database, &old_guard, ScheduleCheckpoint::default())
                .is_err()
        );
        assert!(read_schedule_checkpoint(&database, &old_guard).is_err());
        let actual = read_schedule_checkpoint(&database, &state.guard()).unwrap();
        assert_eq!(actual, checkpoint);
        state.restore_checkpoint(actual);
        assert_eq!(state.last_charging, Some(false));
        assert!(state.charging_pending);
        assert!(state.checkpoint_dirty);
        assert_eq!(state.pending_checkpoint().last_success, 100);
    }

    #[test]
    fn failed_checkpoint_workers_do_not_start_network_work_or_advance_success() {
        let mut state = AutomaticUpdates::default();
        state.restore_checkpoint(ScheduleCheckpoint {
            last_success: 100,
            ..ScheduleCheckpoint::default()
        });
        state.run = Some(UpdateRun {
            sequence: 1,
            generation: 0,
            start_persisted: false,
            manager: None,
            policy: policy(),
            reasons: DueReasons {
                scheduled: true,
                charging: false,
            },
            pending: None,
            checked: 0,
            failed: false,
        });
        state.inflight = Some(InflightUpdate {
            id: 8,
            generation: 0,
            sequence: 1,
            kind: UpdateWork::CheckpointSave,
        });
        assert!(state.finish_job(8, true));
        let now = Instant::now();
        assert!(!state.begin_poll_attempt(now + Duration::from_secs(29)));
        assert!(!state.run.as_ref().unwrap().start_persisted);
        assert!(!state.run.as_ref().unwrap().failed);
        assert_eq!(state.checkpoint.last_success, 100);
        let committed = ScheduleCheckpoint {
            last_attempt: 200,
            ..state.checkpoint
        };
        state.accept_saved_checkpoint(committed, CheckpointPurpose::BeginRun, None);
        assert!(state.run.as_ref().unwrap().start_persisted);
        assert_eq!(state.checkpoint.last_attempt, 200);
        assert_eq!(state.checkpoint.last_success, 100);
    }

    #[test]
    fn a_completed_checkpoint_ack_cannot_consume_a_later_charging_edge() {
        let mut state = AutomaticUpdates::default();
        state.restore_checkpoint(ScheduleCheckpoint {
            last_charging_state: Some(false),
            ..ScheduleCheckpoint::default()
        });
        state.record_charging_observation(Some(true));
        let consumed = state.charging_edge_sequence;
        let checkpoint = ScheduleCheckpoint {
            last_success: 300,
            last_charging_success: 300,
            charging_pending: false,
            ..state.pending_checkpoint()
        };
        // A second plug-in happens while the first event's worker is writing.
        state.record_charging_observation(Some(false));
        state.record_charging_observation(Some(true));
        state.accept_saved_checkpoint(
            checkpoint,
            CheckpointPurpose::FinishRun {
                charging_edge: Some(consumed),
            },
            None,
        );
        assert_eq!(state.checkpoint.last_success, 300);
        assert!(!state.checkpoint.charging_pending);
        assert!(state.charging_pending);
        assert!(state.checkpoint_dirty);
        assert!(state.pending_checkpoint().charging_pending);
    }

    #[test]
    fn daily_and_weekly_schedules_use_persisted_success_and_retry_timestamps() {
        let now = 2_000_000;
        let mut checkpoint = ScheduleCheckpoint {
            last_success: now - 86_399,
            ..ScheduleCheckpoint::default()
        };
        assert!(
            !policy()
                .due(checkpoint, now, false, PolicyEnvironment::default())
                .any()
        );
        checkpoint.last_success -= 1;
        assert!(
            policy()
                .due(checkpoint, now, false, PolicyEnvironment::default())
                .scheduled
        );
        checkpoint.last_attempt = now - RETRY_SECONDS + 1;
        assert!(
            !policy()
                .due(checkpoint, now, false, PolicyEnvironment::default())
                .any()
        );
        checkpoint.last_attempt -= 1;
        let mut weekly = policy();
        weekly.interval_seconds = Some(7 * 86_400);
        assert!(
            !weekly
                .due(checkpoint, now, false, PolicyEnvironment::default())
                .any()
        );
        checkpoint.last_success = now - 7 * 86_400;
        assert!(
            weekly
                .due(checkpoint, now, false, PolicyEnvironment::default())
                .scheduled
        );
        let restored: ScheduleCheckpoint =
            serde_json::from_value(serde_json::to_value(checkpoint).unwrap()).unwrap();
        assert_eq!(restored, checkpoint);
    }

    #[test]
    fn unknown_wifi_or_charging_never_bypasses_a_requested_restriction() {
        let mut restricted = policy();
        restricted.wifi_only = true;
        restricted.charging_only = true;
        for environment in [
            PolicyEnvironment::default(),
            PolicyEnvironment {
                wifi_connected: Some(true),
                charging: None,
            },
            PolicyEnvironment {
                wifi_connected: Some(false),
                charging: Some(true),
            },
        ] {
            assert!(!restricted.permits(environment));
            assert!(
                !restricted
                    .due(ScheduleCheckpoint::default(), 100, true, environment)
                    .any()
            );
        }
        assert!(restricted.permits(PolicyEnvironment {
            wifi_connected: Some(true),
            charging: Some(true),
        }));
    }

    #[test]
    fn charging_triggers_are_edges_with_a_persistent_debounce() {
        let mut state = AutomaticUpdates::default();
        state.observe_charging(Some(true));
        assert!(state.charging_pending);
        state.charging_pending = false;
        state.observe_charging(None);
        state.observe_charging(Some(true));
        assert!(!state.charging_pending);
        state.observe_charging(Some(false));
        state.observe_charging(Some(true));
        assert!(state.charging_pending);
        let mut charging = policy();
        charging.interval_seconds = None;
        charging.charging_updates = true;
        let environment = PolicyEnvironment {
            charging: Some(true),
            ..PolicyEnvironment::default()
        };
        let checkpoint = ScheduleCheckpoint {
            last_charging_success: 100,
            ..ScheduleCheckpoint::default()
        };
        assert!(
            !charging
                .due(
                    checkpoint,
                    100 + CHARGING_DEBOUNCE_SECONDS - 1,
                    true,
                    environment
                )
                .any()
        );
        assert!(
            charging
                .due(
                    checkpoint,
                    100 + CHARGING_DEBOUNCE_SECONDS,
                    true,
                    environment
                )
                .charging
        );
    }

    #[test]
    fn restarting_while_still_charging_does_not_invent_another_edge() {
        let checkpoint = ScheduleCheckpoint {
            last_charging_state: Some(true),
            charging_pending: false,
            ..ScheduleCheckpoint::default()
        };
        let stored = serde_json::to_string(&checkpoint).unwrap();
        let mut restarted = AutomaticUpdates::default();
        restarted.restore_checkpoint(serde_json::from_str(&stored).unwrap());
        restarted.observe_charging(None);
        restarted.observe_charging(Some(true));
        assert!(!restarted.charging_pending);
        restarted.observe_charging(Some(false));
        restarted.observe_charging(Some(true));
        assert!(restarted.charging_pending);

        let failed_attempt = ScheduleCheckpoint {
            charging_pending: true,
            ..checkpoint
        };
        restarted.restore_checkpoint(failed_attempt);
        restarted.observe_charging(Some(true));
        assert!(restarted.charging_pending);
    }

    #[test]
    fn invalidation_rejects_old_results_but_keeps_an_occupied_worker_slot() {
        let mut state = AutomaticUpdates::default();
        state.run = Some(UpdateRun {
            sequence: 8,
            generation: 0,
            start_persisted: true,
            manager: None,
            policy: policy(),
            reasons: DueReasons {
                scheduled: true,
                charging: false,
            },
            pending: None,
            checked: 0,
            failed: false,
        });
        state.inflight = Some(InflightUpdate {
            id: 21,
            generation: 0,
            sequence: 8,
            kind: UpdateWork::Targets,
        });
        let result = UpdateResult {
            generation: 0,
            sequence: 8,
            payload: UpdatePayload::BookCommitted(false),
        };
        assert!(state.matches_result(&result));
        state.invalidate();
        assert!(!state.matches_result(&result));
        assert_eq!(state.inflight.unwrap().id, 21);
        assert!(!state.finish_job(20, false));
        assert!(state.finish_job(21, false));
        assert!(state.inflight.is_none());
    }

    #[test]
    fn failed_collection_does_not_advance_the_success_checkpoint() {
        let mut state = AutomaticUpdates::default();
        state.checkpoint.last_success = 101;
        state.checkpoint.last_attempt = 200;
        state.run = Some(UpdateRun {
            sequence: 1,
            generation: 0,
            start_persisted: true,
            manager: None,
            policy: policy(),
            reasons: DueReasons {
                scheduled: true,
                charging: false,
            },
            pending: None,
            checked: 0,
            failed: false,
        });
        state.inflight = Some(InflightUpdate {
            id: 1,
            generation: 0,
            sequence: 1,
            kind: UpdateWork::Targets,
        });
        assert!(state.finish_job(1, true));
        assert!(state.run.as_ref().unwrap().failed);
        assert!(
            state
                .run
                .as_ref()
                .unwrap()
                .pending
                .as_ref()
                .unwrap()
                .is_empty()
        );
        assert_eq!(state.checkpoint.last_success, 101);
        assert_eq!(state.checkpoint.last_attempt, 200);
    }

    #[test]
    fn target_selection_respects_library_membership_source_setup_and_completed_status() {
        let source = SourceConfig {
            id: "source".into(),
            kind: "komga".into(),
            base_url: "https://example.invalid".into(),
            enabled: true,
            ..SourceConfig::default()
        };
        let mut book = Book {
            source_id: source.id.clone(),
            in_library: true,
            status: "ONGOING".into(),
            ..Book::default()
        };
        assert!(update_target(&book, std::slice::from_ref(&source), true));
        book.status = " Finished ".into();
        assert!(!update_target(&book, std::slice::from_ref(&source), true));
        assert!(update_target(&book, std::slice::from_ref(&source), false));
        book.in_library = false;
        assert!(!update_target(&book, std::slice::from_ref(&source), false));
        book.in_library = true;
        let mut missing_setup = source.clone();
        missing_setup.base_url.clear();
        assert!(!update_target(&book, &[missing_setup], false));
        let mut disabled = source.clone();
        disabled.enabled = false;
        assert!(!update_target(&book, &[disabled], false));
        let mut local = source;
        local.kind = "local".into();
        assert!(!update_target(&book, &[local], false));
    }

    #[test]
    fn ahead_downloads_preserve_user_pause_cancel_and_existing_jobs() {
        let store = Store::open(":memory:").unwrap();
        let book = Book {
            id: "book".into(),
            source_id: "source".into(),
            remote_id: "remote-book".into(),
            ..Book::default()
        };
        store.upsert_book(&book).unwrap();
        let chapters: Vec<_> = (0..5)
            .map(|index| Chapter {
                id: format!("chapter-{index}"),
                book_id: book.id.clone(),
                source_id: book.source_id.clone(),
                remote_id: format!("remote-{index}"),
                number: f64::from(index),
                ..Chapter::default()
            })
            .collect();
        store.save_chapters(&book.id, &chapters).unwrap();
        for (index, state) in [(0, "paused"), (1, "cancelled"), (2, "queued")] {
            store
                .save_download(&crate::domain::DownloadJob {
                    chapter_id: chapters[index].id.clone(),
                    book_id: book.id.clone(),
                    state: state.into(),
                    ..crate::domain::DownloadJob::default()
                })
                .unwrap();
        }
        assert!(
            ahead_download_candidates(&store, &book, &chapters)
                .unwrap()
                .is_empty()
        );
        let selected = ahead_download_candidates(&store, &book, &chapters[3..]).unwrap();
        assert_eq!(selected.len(), 2);
        assert_eq!(selected[0].id, chapters[3].id);
        assert_eq!(selected[1].id, chapters[4].id);
        assert_eq!(
            store.download(&chapters[0].id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(
            store.download(&chapters[1].id).unwrap().unwrap().state,
            "cancelled"
        );
    }

    #[test]
    fn ahead_download_selection_rejects_changed_or_foreign_chapter_identity() {
        let store = Store::open(":memory:").unwrap();
        let book = Book {
            id: "book".into(),
            source_id: "source".into(),
            remote_id: "remote-book".into(),
            ..Book::default()
        };
        store.upsert_book(&book).unwrap();
        let chapter = Chapter {
            id: "chapter".into(),
            book_id: book.id.clone(),
            source_id: book.source_id.clone(),
            remote_id: "remote-chapter".into(),
            ..Chapter::default()
        };
        store
            .save_chapters(&book.id, std::slice::from_ref(&chapter))
            .unwrap();
        let mut changed = chapter.clone();
        changed.remote_id = "old-remote-chapter".into();
        assert!(
            ahead_download_candidates(&store, &book, &[changed])
                .unwrap()
                .is_empty()
        );
        let mut foreign = book.clone();
        foreign.id = "another-book".into();
        assert!(
            ahead_download_candidates(&store, &foreign, std::slice::from_ref(&chapter))
                .unwrap()
                .is_empty()
        );
        assert_eq!(
            ahead_download_candidates(&store, &book, &[chapter])
                .unwrap()
                .len(),
            1
        );
    }
}
