//! Authenticated manga tracking with explicit mappings and a durable retry queue.

use std::{
    collections::BTreeMap,
    fmt,
    io::Read,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};

use reqwest::{
    StatusCode,
    blocking::{Client, Response},
    redirect::Policy,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::store::Store;

const SETTINGS_KEY: &str = "tracking-v1";
const MAX_RESPONSE_BYTES: u64 = 1024 * 1024;
const MAX_PENDING: usize = 1024;
const MAX_BINDINGS: usize = 4096;
const MAX_PROGRESS: u32 = i32::MAX as u32;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Service {
    AniList,
    MyAnimeList,
}

impl Service {
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "anilist" => Some(Self::AniList),
            "myanimelist" => Some(Self::MyAnimeList),
            _ => None,
        }
    }

    pub fn id(self) -> &'static str {
        match self {
            Self::AniList => "anilist",
            Self::MyAnimeList => "myanimelist",
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::AniList => "AniList",
            Self::MyAnimeList => "MyAnimeList",
        }
    }
}

/// Credentials enter only through the authenticated, expiring pairing session.
/// OAuth client registrations belong to the user; no application ID is invented.
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrackingPairRequest {
    pub service: Service,
    #[serde(default)]
    pub access_token: String,
    #[serde(default)]
    pub refresh_token: String,
    #[serde(default)]
    pub client_id: String,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub code_verifier: String,
    #[serde(default)]
    pub redirect_uri: String,
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub returned_state: String,
    pub expires_in: Option<u64>,
}

impl fmt::Debug for TrackingPairRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("TrackingPairRequest")
            .field("service", &self.service)
            .field("credentials", &"[redacted]")
            .finish()
    }
}

impl TrackingPairRequest {
    pub fn validate(&self) -> Result<(), TrackingFailure> {
        if self.access_token.is_empty() == self.code.is_empty() {
            return Err(TrackingFailure::InvalidConfiguration);
        }
        if !self.access_token.is_empty() {
            check_secret(&self.access_token)?;
            if !self.refresh_token.is_empty() {
                check_secret(&self.refresh_token)?;
                check_client_id(&self.client_id)?;
                if self.service != Service::MyAnimeList {
                    return Err(TrackingFailure::InvalidConfiguration);
                }
            }
        } else {
            if self.service != Service::MyAnimeList {
                return Err(TrackingFailure::InvalidConfiguration);
            }
            check_client_id(&self.client_id)?;
            if self.code.is_empty()
                || self.code.len() > 2048
                || self.code.chars().any(char::is_control)
                || !(43..=128).contains(&self.code_verifier.len())
                || !self
                    .code_verifier
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"-._~".contains(&byte))
                || self.state.len() < 32
                || self.state.len() > 128
                || !constant_time_equal(self.state.as_bytes(), self.returned_state.as_bytes())
            {
                return Err(TrackingFailure::InvalidConfiguration);
            }
            check_redirect(&self.redirect_uri)?;
        }
        if self
            .expires_in
            .is_some_and(|seconds| seconds == 0 || seconds > 366 * 24 * 3600)
        {
            return Err(TrackingFailure::InvalidConfiguration);
        }
        Ok(())
    }
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Account {
    pub service: Service,
    pub user_id: u64,
    pub user_name: String,
    access_token: String,
    #[serde(default)]
    refresh_token: String,
    #[serde(default)]
    client_id: String,
    pub expires_at: Option<i64>,
}

impl fmt::Debug for Account {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Account")
            .field("service", &self.service)
            .field("user_id", &self.user_id)
            .field("credentials", &"[redacted]")
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Media {
    pub id: u32,
    pub title: String,
    pub chapters: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Binding {
    pub service: Service,
    pub user_id: u64,
    pub media: Media,
    #[serde(default)]
    pub last_synced_progress: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PendingProgress {
    pub id: u64,
    pub service: Service,
    pub book_id: String,
    pub user_id: u64,
    pub media_id: u32,
    pub progress: u32,
    pub attempts: u8,
    pub next_attempt: i64,
    pub auth_required: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TrackingState {
    pub accounts: BTreeMap<Service, Account>,
    pub bindings: BTreeMap<String, Vec<Binding>>,
    pub pending: Vec<PendingProgress>,
    pub service_ready_at: BTreeMap<Service, i64>,
    next_id: u64,
}

impl TrackingState {
    pub fn load(store: &Store) -> anyhow::Result<Self> {
        let state: Self = store
            .read_ui_settings(SETTINGS_KEY)?
            .map(serde_json::from_value)
            .transpose()?
            .unwrap_or_default();
        anyhow::ensure!(
            state.pending.len() <= MAX_PENDING && state.bindings.len() <= MAX_BINDINGS,
            "The tracking state exceeds its limit"
        );
        for (service, account) in &state.accounts {
            anyhow::ensure!(
                *service == account.service && account.user_id > 0,
                "The tracking account identity is invalid"
            );
            check_secret(&account.access_token)?;
        }
        Ok(state)
    }

    pub fn save(&self, store: &Store) -> anyhow::Result<()> {
        store.write_ui_settings(SETTINGS_KEY, &serde_json::to_value(self)?)
    }

    pub fn connect(&mut self, account: Account) {
        let service = account.service;
        if self
            .accounts
            .get(&service)
            .is_some_and(|old| old.user_id != account.user_id)
        {
            self.remove_service_bindings(service);
        }
        self.pending
            .retain(|pending| pending.service != service || pending.user_id == account.user_id);
        for pending in &mut self.pending {
            if pending.service == service {
                pending.auth_required = false;
                pending.next_attempt = 0;
            }
        }
        self.accounts.insert(service, account);
    }

    pub fn disconnect(&mut self, service: Service) {
        self.accounts.remove(&service);
        self.pending.retain(|pending| pending.service != service);
        self.remove_service_bindings(service);
    }

    fn remove_service_bindings(&mut self, service: Service) {
        self.bindings.retain(|_, bindings| {
            bindings.retain(|binding| binding.service != service);
            !bindings.is_empty()
        });
    }

    pub fn bind(
        &mut self,
        book_id: &str,
        service: Service,
        media: Media,
    ) -> Result<(), TrackingFailure> {
        let account = self
            .accounts
            .get(&service)
            .ok_or(TrackingFailure::Unauthorized)?;
        if book_id.is_empty() || book_id.len() > 4096 || media.id == 0 || media.id > i32::MAX as u32
        {
            return Err(TrackingFailure::InvalidConfiguration);
        }
        if !self.bindings.contains_key(book_id) && self.bindings.len() >= MAX_BINDINGS {
            return Err(TrackingFailure::QueueFull);
        }
        self.pending
            .retain(|pending| pending.book_id != book_id || pending.service != service);
        let bindings = self.bindings.entry(book_id.to_owned()).or_default();
        bindings.retain(|binding| binding.service != service);
        bindings.push(Binding {
            service,
            user_id: account.user_id,
            media,
            last_synced_progress: 0,
        });
        Ok(())
    }

    pub fn unbind(&mut self, book_id: &str, service: Service) {
        if let Some(bindings) = self.bindings.get_mut(book_id) {
            bindings.retain(|binding| binding.service != service);
        }
        if self.bindings.get(book_id).is_some_and(Vec::is_empty) {
            self.bindings.remove(book_id);
        }
        self.pending
            .retain(|pending| pending.book_id != book_id || pending.service != service);
    }

    pub fn enqueue(
        &mut self,
        book_id: &str,
        chapters: u32,
        now: i64,
    ) -> Result<bool, TrackingFailure> {
        if chapters == 0 || chapters > MAX_PROGRESS {
            return Ok(false);
        }
        let bindings = self.bindings.get(book_id).cloned().unwrap_or_default();
        let mut changed = false;
        for binding in bindings {
            if chapters <= binding.last_synced_progress {
                continue;
            }
            if !self
                .accounts
                .get(&binding.service)
                .is_some_and(|account| account.user_id == binding.user_id)
            {
                continue;
            }
            if let Some(pending) = self.pending.iter_mut().find(|pending| {
                pending.book_id == book_id
                    && pending.service == binding.service
                    && pending.media_id == binding.media.id
                    && pending.user_id == binding.user_id
            }) {
                if chapters > pending.progress {
                    pending.progress = chapters;
                    if pending.attempts == 0 {
                        pending.next_attempt = now;
                    }
                    changed = true;
                }
                continue;
            }
            if self.pending.len() >= MAX_PENDING {
                return Err(TrackingFailure::QueueFull);
            }
            self.next_id = self.next_id.saturating_add(1);
            self.pending.push(PendingProgress {
                id: self.next_id,
                service: binding.service,
                book_id: book_id.to_owned(),
                user_id: binding.user_id,
                media_id: binding.media.id,
                progress: chapters,
                attempts: 0,
                next_attempt: now,
                auth_required: false,
            });
            changed = true;
        }
        Ok(changed)
    }

    pub fn finish(&mut self, sent: &PendingProgress, actual_progress: u32) {
        if let Some(binding) = self.bindings.get_mut(&sent.book_id).and_then(|bindings| {
            bindings.iter_mut().find(|binding| {
                binding.service == sent.service
                    && binding.user_id == sent.user_id
                    && binding.media.id == sent.media_id
            })
        }) {
            binding.last_synced_progress = binding
                .last_synced_progress
                .max(sent.progress)
                .max(actual_progress);
        }
        self.pending.retain(|pending| {
            pending.id != sent.id
                || (pending.progress > sent.progress && pending.progress > actual_progress)
        });
    }

    pub fn fail(&mut self, sent: &PendingProgress, failure: &TrackingFailure, now: i64) {
        if let Some(pending) = self
            .pending
            .iter_mut()
            .find(|pending| pending.id == sent.id)
        {
            pending.attempts = pending.attempts.saturating_add(1);
            pending.auth_required = matches!(failure, TrackingFailure::Unauthorized);
            let backoff = 15_u64
                .saturating_mul(1_u64 << pending.attempts.min(8))
                .min(3600);
            let delay = match failure {
                TrackingFailure::RateLimited { retry_after } => backoff.max(*retry_after),
                _ => backoff,
            };
            pending.next_attempt = now.saturating_add(delay.min(86400) as i64);
            if matches!(failure, TrackingFailure::RateLimited { .. }) {
                self.service_ready_at
                    .insert(sent.service, pending.next_attempt);
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TrackingFailure {
    InvalidConfiguration,
    Unauthorized,
    RateLimited { retry_after: u64 },
    Network,
    InvalidResponse,
    Unavailable,
    QueueFull,
}

impl fmt::Display for TrackingFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidConfiguration => "The tracking configuration is invalid",
            Self::Unauthorized => "Tracking authorization is required",
            Self::RateLimited { .. } => "The tracking service rate limit was reached",
            Self::Network => "The tracking service could not be reached",
            Self::InvalidResponse => "The tracking service returned an invalid response",
            Self::Unavailable => "The tracking service is unavailable",
            Self::QueueFull => "The tracking queue is full",
        })
    }
}

impl std::error::Error for TrackingFailure {}

impl TrackingFailure {
    pub fn label(&self) -> &'static str {
        match self {
            Self::Unauthorized => "tracking_auth_failed",
            Self::RateLimited { .. } => "tracking_rate_limited",
            Self::InvalidConfiguration => "tracking_invalid_mapping",
            _ => "tracking_request_failed",
        }
    }
}

#[derive(Clone)]
pub struct TrackingClient {
    client: Client,
    anilist: String,
    mal_api: String,
    mal_token: String,
    fence: Option<TrackingFence>,
}

#[derive(Clone)]
struct TrackingFence {
    network: crate::sources::NetworkControl,
    epoch: u64,
    cancellation: Arc<AtomicU64>,
    generation: u64,
}

#[derive(Debug)]
pub struct SearchReport {
    pub account: Account,
    pub result: Result<Vec<Media>, TrackingFailure>,
}

#[derive(Debug)]
pub struct SyncReport {
    pub account: Account,
    pub result: Result<u32, TrackingFailure>,
}

impl TrackingClient {
    pub fn new() -> Result<Self, TrackingFailure> {
        Ok(Self {
            client: Client::builder()
                .connect_timeout(Duration::from_secs(5))
                .timeout(Duration::from_secs(15))
                .redirect(Policy::none())
                .user_agent(concat!("AidokuKindle/", env!("CARGO_PKG_VERSION")))
                .build()
                .map_err(|_| TrackingFailure::Network)?,
            anilist: "https://graphql.anilist.co".into(),
            mal_api: "https://api.myanimelist.net/v2".into(),
            mal_token: "https://myanimelist.net/v1/oauth2/token".into(),
            fence: None,
        })
    }

    pub fn with_fence(
        mut self,
        network: crate::sources::NetworkControl,
        cancellation: Arc<AtomicU64>,
        generation: u64,
    ) -> Self {
        let epoch = network.epoch();
        self.fence = Some(TrackingFence {
            network,
            epoch,
            cancellation,
            generation,
        });
        self
    }

    fn check_fence(&self) -> Result<(), TrackingFailure> {
        if self.fence.as_ref().is_some_and(|fence| {
            fence.network.check_epoch(fence.epoch).is_err()
                || fence.cancellation.load(Ordering::Acquire) != fence.generation
        }) {
            return Err(TrackingFailure::Network);
        }
        Ok(())
    }

    pub fn connect(
        &self,
        request: &TrackingPairRequest,
        now: i64,
    ) -> Result<Account, TrackingFailure> {
        request.validate()?;
        let mut account = Account {
            service: request.service,
            user_id: 0,
            user_name: String::new(),
            access_token: request.access_token.clone(),
            refresh_token: request.refresh_token.clone(),
            client_id: request.client_id.clone(),
            expires_at: request
                .expires_in
                .map(|seconds| now.saturating_add(seconds as i64)),
        };
        if !request.code.is_empty() {
            let mut form = vec![
                ("client_id", request.client_id.as_str()),
                ("grant_type", "authorization_code"),
                ("code", request.code.as_str()),
                ("code_verifier", request.code_verifier.as_str()),
            ];
            if !request.redirect_uri.is_empty() {
                form.push(("redirect_uri", &request.redirect_uri));
            }
            let token = self.request_json(self.client.post(&self.mal_token).form(&form))?;
            apply_token(&mut account, token, now)?;
        }
        let (id, name) = self.viewer(&account)?;
        account.user_id = id;
        account.user_name = name;
        Ok(account)
    }

    pub fn search(
        &self,
        account: &Account,
        query: &str,
        now: i64,
    ) -> Result<SearchReport, TrackingFailure> {
        let query = query.trim();
        if query.is_empty() || query.len() > 256 || query.chars().any(char::is_control) {
            return Err(TrackingFailure::InvalidConfiguration);
        }
        let (account, result) = self.authorized(account, now, |account| {
            match account.service {
                Service::AniList => {
                    let value = self.graphql(account,
                        "query ($search:String!){Page(perPage:10){media(search:$search,type:MANGA){id title{romaji english native} chapters}}}",
                        json!({"search": query}))?;
                    value.pointer("/Page/media").and_then(Value::as_array)
                        .ok_or(TrackingFailure::InvalidResponse)?.iter().map(anilist_media).collect()
                }
                Service::MyAnimeList => {
                    let value = self.request_json(self.client.get(format!("{}/manga", self.mal_api))
                        .bearer_auth(&account.access_token)
                        .query(&[("q", query), ("limit", "10"), ("fields", "num_chapters")]))?;
                    value.get("data").and_then(Value::as_array).ok_or(TrackingFailure::InvalidResponse)?
                        .iter().map(|value| mal_media(value.get("node").ok_or(TrackingFailure::InvalidResponse)?)).collect()
                }
            }
        });
        Ok(SearchReport { account, result })
    }

    /// Fetch current progress first and never lower the observed remote progress.
    /// These APIs offer no compare-and-swap against concurrent external clients.
    /// Only the progress field is written; ratings, dates and status are preserved.
    pub fn sync(
        &self,
        account: &Account,
        pending: &PendingProgress,
        now: i64,
    ) -> Result<SyncReport, TrackingFailure> {
        if pending.service != account.service
            || pending.user_id != account.user_id
            || pending.media_id == 0
            || pending.media_id > i32::MAX as u32
            || pending.progress > MAX_PROGRESS
        {
            return Err(TrackingFailure::InvalidConfiguration);
        }
        let (account, result) = self.authorized(account, now, |account| {
            match account.service {
                Service::AniList => {
                    let value = self.graphql(account,
                        "query ($id:Int!){Media(id:$id,type:MANGA){id chapters mediaListEntry{id progress}}}",
                        json!({"id": pending.media_id}))?;
                    let media = value.get("Media").filter(|value| value.get("id").and_then(Value::as_u64) == Some(pending.media_id as u64))
                        .ok_or(TrackingFailure::InvalidResponse)?;
                    let entry = media.get("mediaListEntry").ok_or(TrackingFailure::InvalidResponse)?;
                    let (entry_id, current) = if entry.is_null() {
                        (None, 0)
                    } else {
                        let id = entry.get("id").and_then(Value::as_u64).filter(|id| *id > 0 && *id <= i32::MAX as u64)
                            .ok_or(TrackingFailure::InvalidResponse)?;
                        let progress = entry.get("progress").and_then(Value::as_u64).filter(|progress| *progress <= MAX_PROGRESS as u64)
                            .ok_or(TrackingFailure::InvalidResponse)?;
                        (Some(id), progress)
                    };
                    let target = bounded_progress(pending.progress, reported_total(media.get("chapters"))?);
                    if current >= target as u64 { return u32::try_from(current).map_err(|_| TrackingFailure::InvalidResponse); }
                    let value = self.graphql(account,
                        "mutation ($id:Int,$mediaId:Int!,$progress:Int!){SaveMediaListEntry(id:$id,mediaId:$mediaId,progress:$progress){id mediaId progress}}",
                        json!({"id": entry_id, "mediaId": pending.media_id, "progress": target}))?;
                    let returned = value.get("SaveMediaListEntry").ok_or(TrackingFailure::InvalidResponse)?;
                    let returned_id = returned.get("id").and_then(Value::as_u64).filter(|id| *id > 0).ok_or(TrackingFailure::InvalidResponse)?;
                    if entry_id.is_some_and(|id| id != returned_id)
                        || returned.get("mediaId").and_then(Value::as_u64) != Some(pending.media_id as u64)
                    { return Err(TrackingFailure::InvalidResponse); }
                    confirmed_progress(value.pointer("/SaveMediaListEntry/progress"), target)
                }
                Service::MyAnimeList => {
                    let value = self.request_json(self.client.get(format!("{}/manga/{}", self.mal_api, pending.media_id))
                        .bearer_auth(&account.access_token)
                        .query(&[("fields", "num_chapters,my_list_status")]))?;
                    if value.get("id").and_then(Value::as_u64) != Some(pending.media_id as u64) {
                        return Err(TrackingFailure::InvalidResponse);
                    }
                    let current = match value.get("my_list_status") {
                        None | Some(Value::Null) => 0,
                        Some(status) => status.get("num_chapters_read").and_then(Value::as_u64)
                            .filter(|progress| *progress <= MAX_PROGRESS as u64).ok_or(TrackingFailure::InvalidResponse)?,
                    };
                    let target = bounded_progress(pending.progress, reported_total(value.get("num_chapters"))?);
                    if current >= target as u64 { return u32::try_from(current).map_err(|_| TrackingFailure::InvalidResponse); }
                    let value = self.request_json(self.client.patch(format!("{}/manga/{}/my_list_status", self.mal_api, pending.media_id))
                        .bearer_auth(&account.access_token).form(&[("num_chapters_read", target.to_string())]))?;
                    confirmed_progress(value.get("num_chapters_read"), target)
                }
            }
        });
        Ok(SyncReport { account, result })
    }

    fn authorized<T>(
        &self,
        original: &Account,
        now: i64,
        operation: impl Fn(&Account) -> Result<T, TrackingFailure>,
    ) -> (Account, Result<T, TrackingFailure>) {
        let mut account = original.clone();
        let can_refresh = account.service == Service::MyAnimeList
            && !account.refresh_token.is_empty()
            && !account.client_id.is_empty();
        let mut refreshed = false;
        if can_refresh
            && account
                .expires_at
                .is_some_and(|expiry| expiry <= now.saturating_add(30))
        {
            account = match self.refresh(&account, now) {
                Ok(account) => account,
                Err(error) => return (account, Err(error)),
            };
            refreshed = true;
        }
        match operation(&account) {
            Ok(value) => (account, Ok(value)),
            Err(TrackingFailure::Unauthorized) if can_refresh && !refreshed => {
                account = match self.refresh(&account, now) {
                    Ok(account) => account,
                    Err(error) => return (account, Err(error)),
                };
                let result = operation(&account);
                (account, result)
            }
            Err(error) => (account, Err(error)),
        }
    }

    fn refresh(&self, original: &Account, now: i64) -> Result<Account, TrackingFailure> {
        let token = self.request_json(self.client.post(&self.mal_token).form(&[
            ("client_id", original.client_id.as_str()),
            ("grant_type", "refresh_token"),
            ("refresh_token", original.refresh_token.as_str()),
        ]))?;
        let mut account = original.clone();
        apply_token(&mut account, token, now)?;
        Ok(account)
    }

    fn viewer(&self, account: &Account) -> Result<(u64, String), TrackingFailure> {
        let value = match account.service {
            Service::AniList => self
                .graphql(account, "query {Viewer{id name}}", json!({}))?
                .get("Viewer")
                .cloned()
                .ok_or(TrackingFailure::InvalidResponse)?,
            Service::MyAnimeList => self.request_json(
                self.client
                    .get(format!("{}/users/@me", self.mal_api))
                    .bearer_auth(&account.access_token),
            )?,
        };
        let id = value
            .get("id")
            .and_then(Value::as_u64)
            .filter(|id| *id > 0)
            .ok_or(TrackingFailure::InvalidResponse)?;
        let name = value
            .get("name")
            .and_then(Value::as_str)
            .filter(|name| !name.is_empty() && name.len() <= 256)
            .ok_or(TrackingFailure::InvalidResponse)?;
        Ok((
            id,
            name.chars()
                .filter(|character| !character.is_control())
                .collect(),
        ))
    }

    fn graphql(
        &self,
        account: &Account,
        query: &str,
        variables: Value,
    ) -> Result<Value, TrackingFailure> {
        let value = self.request_json(
            self.client
                .post(&self.anilist)
                .bearer_auth(&account.access_token)
                .json(&json!({"query": query, "variables": variables})),
        )?;
        if let Some(errors) = value
            .get("errors")
            .and_then(Value::as_array)
            .filter(|errors| !errors.is_empty())
        {
            if errors
                .iter()
                .any(|error| error.get("status").and_then(Value::as_u64) == Some(401))
            {
                return Err(TrackingFailure::Unauthorized);
            }
            if errors
                .iter()
                .any(|error| error.get("status").and_then(Value::as_u64) == Some(429))
            {
                return Err(TrackingFailure::RateLimited { retry_after: 60 });
            }
            return Err(TrackingFailure::InvalidResponse);
        }
        value
            .get("data")
            .filter(|value| value.is_object())
            .cloned()
            .ok_or(TrackingFailure::InvalidResponse)
    }

    fn request_json(
        &self,
        request: reqwest::blocking::RequestBuilder,
    ) -> Result<Value, TrackingFailure> {
        self.check_fence()?;
        let response = request
            .header("Accept", "application/json")
            .send()
            .map_err(|_| TrackingFailure::Network)?;
        self.check_fence()?;
        let result = response_json(response);
        self.check_fence()?;
        result
    }
}

fn response_json(response: Response) -> Result<Value, TrackingFailure> {
    match response.status() {
        StatusCode::UNAUTHORIZED => return Err(TrackingFailure::Unauthorized),
        StatusCode::TOO_MANY_REQUESTS => {
            let delay = response
                .headers()
                .get("retry-after")
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(60);
            return Err(TrackingFailure::RateLimited {
                retry_after: delay.clamp(1, 86400),
            });
        }
        status if !status.is_success() => {
            return Err(
                if status.is_server_error() || status == StatusCode::FORBIDDEN {
                    TrackingFailure::Unavailable
                } else {
                    TrackingFailure::InvalidResponse
                },
            );
        }
        _ => {}
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_RESPONSE_BYTES)
    {
        return Err(TrackingFailure::InvalidResponse);
    }
    let mut bytes = Vec::new();
    response
        .take(MAX_RESPONSE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| TrackingFailure::Network)?;
    if bytes.len() as u64 > MAX_RESPONSE_BYTES {
        return Err(TrackingFailure::InvalidResponse);
    }
    serde_json::from_slice(&bytes).map_err(|_| TrackingFailure::InvalidResponse)
}

fn check_secret(value: &str) -> Result<(), TrackingFailure> {
    if value.is_empty() || value.len() > 8192 || !value.bytes().all(|byte| byte.is_ascii_graphic())
    {
        return Err(TrackingFailure::InvalidConfiguration);
    }
    Ok(())
}

fn check_client_id(value: &str) -> Result<(), TrackingFailure> {
    if value.is_empty()
        || value.len() > 128
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_".contains(&byte))
    {
        return Err(TrackingFailure::InvalidConfiguration);
    }
    Ok(())
}

fn check_redirect(value: &str) -> Result<(), TrackingFailure> {
    if value.is_empty() {
        return Ok(());
    }
    let url = url::Url::parse(value).map_err(|_| TrackingFailure::InvalidConfiguration)?;
    if value.len() > 2048
        || !matches!(url.scheme(), "https" | "http")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(TrackingFailure::InvalidConfiguration);
    }
    Ok(())
}

fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    left.len() == right.len()
        && left
            .iter()
            .zip(right)
            .fold(0_u8, |difference, (a, b)| difference | (a ^ b))
            == 0
}

fn apply_token(account: &mut Account, value: Value, now: i64) -> Result<(), TrackingFailure> {
    let token = value
        .get("access_token")
        .and_then(Value::as_str)
        .ok_or(TrackingFailure::InvalidResponse)?;
    check_secret(token).map_err(|_| TrackingFailure::InvalidResponse)?;
    if value
        .get("token_type")
        .and_then(Value::as_str)
        .is_some_and(|kind| !kind.eq_ignore_ascii_case("bearer"))
    {
        return Err(TrackingFailure::InvalidResponse);
    }
    account.access_token = token.to_owned();
    if let Some(refresh) = value.get("refresh_token").and_then(Value::as_str) {
        check_secret(refresh).map_err(|_| TrackingFailure::InvalidResponse)?;
        account.refresh_token = refresh.to_owned();
    }
    let expiry = value
        .get("expires_in")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0 && *value <= i64::MAX as u64)
        .ok_or(TrackingFailure::InvalidResponse)?;
    account.expires_at = Some(now.saturating_add(expiry as i64));
    Ok(())
}

fn anilist_media(value: &Value) -> Result<Media, TrackingFailure> {
    let id = value
        .get("id")
        .and_then(Value::as_u64)
        .filter(|id| *id > 0 && *id <= i32::MAX as u64)
        .ok_or(TrackingFailure::InvalidResponse)? as u32;
    let title = ["english", "romaji", "native"]
        .iter()
        .find_map(|key| {
            value
                .get("title")
                .and_then(|title| title.get(*key))
                .and_then(Value::as_str)
                .filter(|title| !title.is_empty())
        })
        .ok_or(TrackingFailure::InvalidResponse)?;
    checked_media(id, title, value.get("chapters").and_then(Value::as_u64))
}

fn mal_media(value: &Value) -> Result<Media, TrackingFailure> {
    let id = value
        .get("id")
        .and_then(Value::as_u64)
        .filter(|id| *id > 0 && *id <= i32::MAX as u64)
        .ok_or(TrackingFailure::InvalidResponse)? as u32;
    let title = value
        .get("title")
        .and_then(Value::as_str)
        .ok_or(TrackingFailure::InvalidResponse)?;
    checked_media(id, title, value.get("num_chapters").and_then(Value::as_u64))
}

fn checked_media(id: u32, title: &str, chapters: Option<u64>) -> Result<Media, TrackingFailure> {
    if title.is_empty() || title.len() > 2048 {
        return Err(TrackingFailure::InvalidResponse);
    }
    Ok(Media {
        id,
        title: title
            .chars()
            .filter(|character| !character.is_control())
            .collect(),
        chapters: chapters
            .filter(|count| *count > 0 && *count <= MAX_PROGRESS as u64)
            .map(|count| count as u32),
    })
}

fn bounded_progress(progress: u32, total: Option<u64>) -> u32 {
    total
        .filter(|total| *total > 0 && *total <= MAX_PROGRESS as u64)
        .map_or(progress, |total| progress.min(total as u32))
}

fn reported_total(value: Option<&Value>) -> Result<Option<u64>, TrackingFailure> {
    match value {
        Some(Value::Null) => Ok(None),
        Some(value) => value
            .as_u64()
            .filter(|total| *total <= MAX_PROGRESS as u64)
            .map(|total| (total > 0).then_some(total))
            .ok_or(TrackingFailure::InvalidResponse),
        None => Err(TrackingFailure::InvalidResponse),
    }
}

fn confirmed_progress(value: Option<&Value>, expected: u32) -> Result<u32, TrackingFailure> {
    let progress = value
        .and_then(Value::as_u64)
        .filter(|value| *value >= expected as u64 && *value <= MAX_PROGRESS as u64)
        .ok_or(TrackingFailure::InvalidResponse)?;
    Ok(progress as u32)
}

/// Fractional and special chapter numbers are ambiguous across catalogs.
/// Automatic sync accepts only positive integral chapter numbers.
pub fn completed_chapter_number(number: f64) -> Option<u32> {
    (number.is_finite() && number >= 1.0 && number <= MAX_PROGRESS as f64 && number.fract() == 0.0)
        .then_some(number as u32)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::Write,
        net::{TcpListener, TcpStream},
        sync::{Arc, Mutex},
        thread::{self, JoinHandle},
        time::Instant,
    };

    fn account(service: Service, user_id: u64) -> Account {
        Account {
            service,
            user_id,
            user_name: "reader".into(),
            access_token: "access-token".into(),
            refresh_token: String::new(),
            client_id: String::new(),
            expires_at: None,
        }
    }

    fn token_request(service: Service) -> TrackingPairRequest {
        TrackingPairRequest {
            service,
            access_token: "access-token".into(),
            refresh_token: String::new(),
            client_id: String::new(),
            code: String::new(),
            code_verifier: String::new(),
            redirect_uri: String::new(),
            state: String::new(),
            returned_state: String::new(),
            expires_in: None,
        }
    }

    fn mapped_state() -> TrackingState {
        let mut state = TrackingState::default();
        state.connect(account(Service::AniList, 1));
        state
            .bind(
                "book",
                Service::AniList,
                Media {
                    id: 12,
                    title: "Manga".into(),
                    chapters: None,
                },
            )
            .unwrap();
        state
    }

    #[test]
    fn mappings_queue_and_credentials_survive_restart_without_debug_leaks() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("library.sqlite3");
        let store = Store::open(&database).unwrap();
        let mut state = mapped_state();
        state.enqueue("book", 3, 10).unwrap();
        state.save(&store).unwrap();
        drop(store);
        let store = Store::open(&database).unwrap();
        let restored = TrackingState::load(&store).unwrap();
        assert_eq!(restored.pending[0].progress, 3);
        assert_eq!(restored.bindings["book"][0].media.id, 12);
        assert!(!format!("{:?}", restored.accounts).contains("access-token"));
        assert!(!format!("{:?}", token_request(Service::AniList)).contains("access-token"));
    }

    #[test]
    fn queue_coalesces_progress_preserves_newer_work_and_does_not_repeat_synced_work() {
        let mut state = mapped_state();
        assert!(state.enqueue("book", 4, 10).unwrap());
        let sent = state.pending[0].clone();
        assert!(!state.enqueue("book", 2, 11).unwrap());
        assert!(state.enqueue("book", 5, 12).unwrap());
        assert_eq!(state.pending.len(), 1);
        state.finish(&sent, 4);
        assert_eq!(state.pending[0].progress, 5);
        let sent = state.pending[0].clone();
        state.finish(&sent, 5);
        assert!(state.pending.is_empty());
        assert!(!state.enqueue("book", 4, 13).unwrap());
        assert!(!state.enqueue("book", 5, 14).unwrap());
        assert!(state.enqueue("book", 6, 15).unwrap());
    }

    #[test]
    fn clamped_remote_total_finishes_the_sent_item_and_account_changes_discard_old_work() {
        let mut state = mapped_state();
        state.enqueue("book", 30, 0).unwrap();
        let sent = state.pending[0].clone();
        state.finish(&sent, 20);
        assert!(state.pending.is_empty());
        state.enqueue("book", 31, 0).unwrap();
        state.connect(account(Service::AniList, 2));
        assert!(state.pending.is_empty());
        assert!(state.bindings.is_empty());
        state.disconnect(Service::AniList);
        assert!(state.accounts.is_empty());
    }

    #[test]
    fn authorization_blocks_until_reconnected_and_rate_limits_back_off() {
        let mut state = mapped_state();
        state.enqueue("book", 1, 0).unwrap();
        let sent = state.pending[0].clone();
        state.fail(
            &sent,
            &TrackingFailure::RateLimited { retry_after: 600 },
            100,
        );
        assert_eq!(state.pending[0].next_attempt, 700);
        assert_eq!(state.service_ready_at[&Service::AniList], 700);
        state.enqueue("book", 2, 101).unwrap();
        assert_eq!(state.pending[0].next_attempt, 700);
        state.fail(&sent, &TrackingFailure::Unauthorized, 101);
        assert!(state.pending[0].auth_required);
        state.connect(account(Service::AniList, 1));
        assert!(!state.pending[0].auth_required);
        assert_eq!(state.pending[0].next_attempt, 0);
    }

    #[test]
    fn only_integral_positive_chapter_numbers_are_automatically_mapped() {
        assert_eq!(completed_chapter_number(12.0), Some(12));
        for value in [f64::NAN, f64::INFINITY, -1.0, 0.0, 0.5, 12.5] {
            assert_eq!(completed_chapter_number(value), None);
        }
    }

    #[test]
    fn malformed_tokens_verifiers_and_oauth_state_are_rejected() {
        let mut request = token_request(Service::AniList);
        request.access_token = "token\r\nAuthorization: injected".into();
        assert!(request.validate().is_err());
        let mut request = token_request(Service::MyAnimeList);
        request.access_token.clear();
        request.client_id = "registered-client".into();
        request.code = "authorization-code".into();
        request.code_verifier = "v".repeat(64);
        request.state = "s".repeat(64);
        request.returned_state = "different-state".into();
        assert!(request.validate().is_err());
        request.returned_state = request.state.clone();
        assert!(request.validate().is_ok());
        request.code_verifier = "short".into();
        assert!(request.validate().is_err());
    }

    #[derive(Debug)]
    struct Recorded {
        method: String,
        target: String,
        headers: String,
        body: Vec<u8>,
    }

    fn receive(stream: &mut TcpStream) -> Recorded {
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut bytes = Vec::new();
        let split = loop {
            if let Some(position) = bytes.windows(4).position(|bytes| bytes == b"\r\n\r\n") {
                break position + 4;
            }
            let mut chunk = [0; 1024];
            let amount = stream.read(&mut chunk).unwrap();
            assert!(amount > 0);
            bytes.extend_from_slice(&chunk[..amount]);
        };
        let headers = String::from_utf8(bytes[..split].to_vec()).unwrap();
        let length: usize = headers
            .lines()
            .find_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.eq_ignore_ascii_case("content-length")
                    .then(|| value.trim().parse().unwrap())
            })
            .unwrap_or(0);
        while bytes.len() < split + length {
            let mut chunk = [0; 1024];
            let amount = stream.read(&mut chunk).unwrap();
            assert!(amount > 0);
            bytes.extend_from_slice(&chunk[..amount]);
        }
        let mut first = headers.lines().next().unwrap().split_whitespace();
        let method = first.next().unwrap().to_owned();
        let target = first.next().unwrap().to_owned();
        Recorded {
            method,
            target,
            headers,
            body: bytes[split..split + length].to_vec(),
        }
    }

    fn fixture(
        responses: Vec<(u16, Value)>,
    ) -> (TrackingClient, Arc<Mutex<Vec<Recorded>>>, JoinHandle<()>) {
        fixture_with_cancellation(responses, None)
    }

    fn fixture_with_cancellation(
        responses: Vec<(u16, Value)>,
        cancellation: Option<Arc<AtomicU64>>,
    ) -> (TrackingClient, Arc<Mutex<Vec<Recorded>>>, JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let recorded = Arc::new(Mutex::new(Vec::new()));
        let records = recorded.clone();
        let worker = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(15);
            for (status, value) in responses {
                let mut stream = loop {
                    match listener.accept() {
                        Ok((stream, _)) => break stream,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            assert!(
                                Instant::now() < deadline,
                                "The tracking fixture did not receive its expected request"
                            );
                            thread::sleep(Duration::from_millis(5));
                        }
                        Err(error) => panic!("The tracking fixture failed: {error}"),
                    }
                };
                let request = receive(&mut stream);
                records.lock().unwrap().push(request);
                if let Some(cancellation) = &cancellation {
                    cancellation.store(1, Ordering::Release);
                }
                let body = serde_json::to_vec(&value).unwrap();
                write!(stream, "HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).unwrap();
                stream.write_all(&body).unwrap();
            }
        });
        let client = TrackingClient {
            client: Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(5))
                .redirect(Policy::none())
                .build()
                .unwrap(),
            anilist: format!("{base}/graphql"),
            mal_api: format!("{base}/v2"),
            mal_token: format!("{base}/token"),
            fence: None,
        };
        (client, recorded, worker)
    }

    #[test]
    fn anilist_validates_searches_and_preserves_remote_progress_before_mutating() {
        let (client, recorded, worker) = fixture(vec![
            (200, json!({"data":{"Viewer":{"id":1,"name":"reader"}}})),
            (
                200,
                json!({"data":{"Page":{"media":[{"id":12,"title":{"english":"Manga"},"chapters":100}]}}}),
            ),
            (
                200,
                json!({"data":{"Media":{"id":12,"chapters":100,"mediaListEntry":{"id":44,"progress":7}}}}),
            ),
            (
                200,
                json!({"data":{"Media":{"id":12,"chapters":100,"mediaListEntry":{"id":44,"progress":7}}}}),
            ),
            (
                200,
                json!({"data":{"SaveMediaListEntry":{"id":44,"mediaId":12,"progress":9}}}),
            ),
        ]);
        let account = client.connect(&token_request(Service::AniList), 0).unwrap();
        assert_eq!(account.user_id, 1);
        assert_eq!(
            client.search(&account, "Manga", 0).unwrap().result.unwrap()[0].id,
            12
        );
        let mut state = mapped_state();
        state.enqueue("book", 5, 0).unwrap();
        let mut pending = state.pending[0].clone();
        assert_eq!(
            client.sync(&account, &pending, 0).unwrap().result.unwrap(),
            7
        );
        pending.progress = 9;
        assert_eq!(
            client.sync(&account, &pending, 0).unwrap().result.unwrap(),
            9
        );
        worker.join().unwrap();
        let recorded = recorded.lock().unwrap();
        assert!(recorded.iter().all(|request| {
            request.method == "POST"
                && request.target == "/graphql"
                && request
                    .headers
                    .to_ascii_lowercase()
                    .contains("authorization: bearer access-token")
        }));
        let update: Value = serde_json::from_slice(&recorded[4].body).unwrap();
        assert_eq!(update["variables"]["id"], 44);
        assert_eq!(update["variables"]["mediaId"], 12);
        assert_eq!(update["variables"]["progress"], 9);
        assert!(
            !update["variables"]
                .as_object()
                .unwrap()
                .contains_key("status")
        );
    }

    #[test]
    fn mal_uses_native_pkce_refresh_rotation_and_patch_without_overwriting_status() {
        let (client, recorded, worker) = fixture(vec![
            (
                200,
                json!({"access_token":"first-token","refresh_token":"first-refresh","token_type":"Bearer","expires_in":60}),
            ),
            (200, json!({"id":99,"name":"reader"})),
            (
                200,
                json!({"access_token":"rotated-token","refresh_token":"rotated-refresh","token_type":"Bearer","expires_in":3600}),
            ),
            (
                200,
                json!({"id":12,"title":"Manga","num_chapters":100,"my_list_status":{"num_chapters_read":3,"status":"on_hold"}}),
            ),
            (200, json!({"num_chapters_read":4,"status":"on_hold"})),
        ]);
        let mut request = token_request(Service::MyAnimeList);
        request.access_token.clear();
        request.client_id = "registered-client".into();
        request.code = "official-code".into();
        request.code_verifier = "v".repeat(64);
        request.state = "s".repeat(64);
        request.returned_state = request.state.clone();
        let account = client.connect(&request, 100).unwrap();
        let pending = PendingProgress {
            id: 1,
            service: Service::MyAnimeList,
            book_id: "book".into(),
            user_id: 99,
            media_id: 12,
            progress: 4,
            attempts: 0,
            next_attempt: 0,
            auth_required: false,
        };
        let report = client.sync(&account, &pending, 200).unwrap();
        assert_eq!(report.result.unwrap(), 4);
        assert_eq!(report.account.refresh_token, "rotated-refresh");
        worker.join().unwrap();
        let recorded = recorded.lock().unwrap();
        let code_form: BTreeMap<_, _> = url::form_urlencoded::parse(&recorded[0].body)
            .into_owned()
            .collect();
        assert_eq!(code_form["grant_type"], "authorization_code");
        assert_eq!(code_form["code_verifier"], "v".repeat(64));
        assert!(!code_form.contains_key("client_secret"));
        let refresh: BTreeMap<_, _> = url::form_urlencoded::parse(&recorded[2].body)
            .into_owned()
            .collect();
        assert_eq!(refresh["grant_type"], "refresh_token");
        assert_eq!(refresh["refresh_token"], "first-refresh");
        assert_eq!(recorded[4].method, "PATCH");
        assert_eq!(recorded[4].target, "/v2/manga/12/my_list_status");
        assert!(
            recorded[4]
                .headers
                .to_ascii_lowercase()
                .contains("authorization: bearer rotated-token")
        );
        let update: BTreeMap<_, _> = url::form_urlencoded::parse(&recorded[4].body)
            .into_owned()
            .collect();
        assert_eq!(update.len(), 1);
        assert_eq!(update["num_chapters_read"], "4");
    }

    #[test]
    fn graphql_errors_and_unconfirmed_mutations_are_not_accepted_as_success() {
        let (client, _, worker) = fixture(vec![(
            200,
            json!({"errors":[{"status":401,"message":"Do not expose this response"}]}),
        )]);
        let error = client
            .connect(&token_request(Service::AniList), 0)
            .unwrap_err();
        assert_eq!(error, TrackingFailure::Unauthorized);
        assert!(!error.to_string().contains("Do not expose"));
        worker.join().unwrap();
        assert!(confirmed_progress(Some(&json!(3)), 4).is_err());
        assert!(confirmed_progress(None, 4).is_err());
    }

    #[test]
    fn cancellation_after_the_progress_read_prevents_the_following_remote_write() {
        let cancellation = Arc::new(AtomicU64::new(0));
        let (client, recorded, worker) = fixture_with_cancellation(
            vec![(
                200,
                json!({"data":{"Media":{"id":12,"chapters":100,"mediaListEntry":{"id":44,"progress":0}}}}),
            )],
            Some(cancellation.clone()),
        );
        let directory = tempfile::tempdir().unwrap();
        let control = crate::sources::NetworkControl::open(directory.path()).unwrap();
        let client = client.with_fence(control, cancellation, 0);
        let mut state = mapped_state();
        state.enqueue("book", 5, 0).unwrap();
        let report = client
            .sync(&account(Service::AniList, 1), &state.pending[0], 0)
            .unwrap();
        assert_eq!(report.result.unwrap_err(), TrackingFailure::Network);
        worker.join().unwrap();
        let recorded = recorded.lock().unwrap();
        assert_eq!(recorded.len(), 1);
        let request: Value = serde_json::from_slice(&recorded[0].body).unwrap();
        assert!(request["query"].as_str().unwrap().starts_with("query"));
    }

    #[test]
    fn rotated_refresh_credentials_are_retained_even_when_the_following_operation_fails() {
        let (client, _, worker) = fixture(vec![
            (
                200,
                json!({"access_token":"rotated-token","refresh_token":"rotated-refresh","token_type":"Bearer","expires_in":3600}),
            ),
            (503, json!({"error":"unavailable"})),
        ]);
        let mut account = account(Service::MyAnimeList, 99);
        account.refresh_token = "old-refresh".into();
        account.client_id = "registered-client".into();
        account.expires_at = Some(10);
        let pending = PendingProgress {
            id: 1,
            service: Service::MyAnimeList,
            book_id: "book".into(),
            user_id: 99,
            media_id: 12,
            progress: 4,
            attempts: 0,
            next_attempt: 0,
            auth_required: false,
        };
        let report = client.sync(&account, &pending, 100).unwrap();
        assert_eq!(report.result.unwrap_err(), TrackingFailure::Unavailable);
        assert_eq!(report.account.refresh_token, "rotated-refresh");
        worker.join().unwrap();
    }

    #[test]
    fn incomplete_existing_list_entries_never_trigger_a_progress_write() {
        let (client, recorded, worker) = fixture(vec![
            (
                200,
                json!({"data":{"Media":{"id":12,"chapters":100,"mediaListEntry":{"id":44}}}}),
            ),
            (
                200,
                json!({"id":12,"num_chapters":100,"my_list_status":{"status":"reading"}}),
            ),
        ]);
        let mut state = mapped_state();
        state.enqueue("book", 5, 0).unwrap();
        let pending = state.pending[0].clone();
        assert_eq!(
            client
                .sync(&account(Service::AniList, 1), &pending, 0)
                .unwrap()
                .result
                .unwrap_err(),
            TrackingFailure::InvalidResponse
        );
        let mut mal_pending = pending;
        mal_pending.service = Service::MyAnimeList;
        assert_eq!(
            client
                .sync(&account(Service::MyAnimeList, 1), &mal_pending, 0)
                .unwrap()
                .result
                .unwrap_err(),
            TrackingFailure::InvalidResponse
        );
        worker.join().unwrap();
        assert_eq!(recorded.lock().unwrap().len(), 2);
    }
}
