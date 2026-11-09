//! Native server settings and credential-safe connection orchestration.

use super::*;

const VERIFIED_OPTION: &str = "_connection_verified";
const SECRET_MASK: &str = "••••••••";

#[derive(Default)]
pub(crate) struct ServerState {
    configurations: HashMap<String, SourceConfig>,
    connections: HashMap<String, ConnectionState>,
    editing_source: String,
    keyboard: Option<ServerKeyboard>,
    next_attempt: u64,
}

#[derive(Default)]
struct ConnectionState {
    attempt: Option<ConnectionAttempt>,
    failure: Option<ServerFailure>,
}

struct ConnectionAttempt {
    job: u64,
    sequence: u64,
    source_revision: u64,
    lifecycle_generation: u64,
    network_epoch: u64,
    catalog_generation: u64,
    catalog_filter: i32,
}

struct ServerKeyboard {
    source: String,
    source_revision: u64,
    return_panel: Option<ServerPanel>,
}

#[derive(Clone, Copy)]
enum ServerPanel {
    Settings,
    Credentials,
}

pub(crate) struct ServerConnection {
    source: String,
    sequence: u64,
    result: std::result::Result<ConnectedCatalog, ServerFailure>,
}

struct ConnectedCatalog {
    books: Vec<Book>,
    has_next: bool,
    listings: Vec<(String, String)>,
}

#[derive(Clone, Copy)]
enum ServerFailure {
    Authentication,
    AccessDenied,
    RateLimited,
    Timeout,
    InvalidResponse,
    Connection,
}

impl ServerFailure {
    fn from_error(error: &anyhow::Error) -> Self {
        if error.chain().any(|cause| {
            cause
                .downcast_ref::<reqwest::Error>()
                .is_some_and(|error| error.is_timeout())
        }) {
            return Self::Timeout;
        }
        // Keep server-supplied messages out of UI models, logs, and persistence.
        let message = error
            .chain()
            .map(ToString::to_string)
            .collect::<Vec<_>>()
            .join(" ")
            .to_ascii_lowercase();
        if message.contains("authentication failed")
            || message.contains("login did not return")
            || message.contains("configure a username")
            || message.contains("unauthorized")
            || message.contains("invalid credentials")
            || message.contains("invalid username")
            || message.contains("invalid password")
        {
            Self::Authentication
        } else if message.contains("access denied") {
            Self::AccessDenied
        } else if message.contains("too many requests") {
            Self::RateLimited
        } else if message.contains("timed out") || message.contains("timeout") {
            Self::Timeout
        } else if message.contains("incompatible api")
            || message.contains("returned a web page")
            || message.contains("graphql response has no data")
        {
            Self::InvalidResponse
        } else {
            Self::Connection
        }
    }

    fn label(self) -> String {
        tr(match self {
            Self::Authentication => "server_authentication_failed",
            Self::AccessDenied => "server_access_denied",
            Self::RateLimited => "server_rate_limited",
            Self::Timeout => "server_timeout",
            Self::InvalidResponse => "server_invalid_response",
            Self::Connection => "server_connection_failed",
        })
    }
}

fn is_server(config: &SourceConfig) -> bool {
    matches!(
        config.kind.to_ascii_lowercase().as_str(),
        "komga" | "kavita" | "suwayomi"
    )
}

fn is_fixture(config: &SourceConfig) -> bool {
    config.options.get("demo").and_then(Value::as_bool) == Some(true)
}

fn verified(config: &SourceConfig) -> bool {
    config.options.get(VERIFIED_OPTION).and_then(Value::as_bool) == Some(true)
}

fn same_server_settings(first: &SourceConfig, second: &SourceConfig) -> bool {
    first.kind.eq_ignore_ascii_case(&second.kind)
        && first.enabled == second.enabled
        && first.base_url == second.base_url
        && first.username == second.username
        && first.password == second.password
        && first.token == second.token
        && ["api_key", "auth_mode", "source_id"]
            .iter()
            .all(|key| first.options.get(*key) == second.options.get(*key))
}

fn credential_mask(config: &SourceConfig) -> String {
    let has_credential = !config.username.is_empty()
        || !config.password.is_empty()
        || !config.token.is_empty()
        || config
            .options
            .get("api_key")
            .and_then(Value::as_str)
            .is_some_and(|value| !value.is_empty());
    if has_credential {
        SECRET_MASK.into()
    } else {
        String::new()
    }
}

fn mask(value: &str) -> String {
    if value.is_empty() {
        String::new()
    } else {
        SECRET_MASK.into()
    }
}

fn server_address(value: &str) -> Result<String> {
    let value = value.trim();
    if value.is_empty() {
        return Ok(String::new());
    }
    let parsed = url::Url::parse(value).map_err(|_| anyhow::anyhow!(tr("server_invalid_url")))?;
    if value.len() > 2048
        || !matches!(parsed.scheme(), "http" | "https")
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.fragment().is_some()
        || parsed.query().is_some()
        || value.chars().any(char::is_control)
    {
        bail!("{}", tr("server_invalid_url"));
    }
    Ok(parsed.to_string())
}

fn credential_text(value: &str, limit: usize, trim: bool) -> Result<String> {
    let value = if trim { value.trim() } else { value };
    if value.len() > limit || value.chars().any(char::is_control) {
        bail!("{}", tr("server_invalid_credential"));
    }
    Ok(value.into())
}

impl App {
    /// Refresh the settings snapshot from the projection's existing source read.
    pub(crate) fn capture_server_configurations(&mut self, sources: &[SourceConfig]) {
        for config in sources.iter().filter(|source| is_server(source)) {
            if self
                .server_configuration
                .configurations
                .get(&config.id)
                .is_some_and(|previous| !same_server_settings(previous, config))
            {
                self.server_configuration.connections.remove(&config.id);
            }
        }
        self.server_configuration.configurations = sources
            .iter()
            .filter(|source| is_server(source))
            .map(|source| (source.id.clone(), source.clone()))
            .collect();
        self.server_configuration.connections.retain(|source, _| {
            self.server_configuration
                .configurations
                .contains_key(source)
        });
    }

    /// Record an accepted HTTP result without changing the source generation.
    pub(crate) fn record_server_http_success(
        &mut self,
        source: &str,
        source_revision: u64,
    ) -> Result<()> {
        if self.options.demo
            || self.options.offline
            || self.suspended
            || source_revision != self.source_revision
        {
            return Ok(());
        }
        let Some(mut config) = self.store.source(source)? else {
            return Ok(());
        };
        if !is_server(&config)
            || is_fixture(&config)
            || !config.enabled
            || config.base_url.is_empty()
        {
            return Ok(());
        }
        let requested_configuration = self
            .source_configurations
            .as_ref()
            .and_then(|sources| sources.iter().find(|requested| requested.id == source));
        if !requested_configuration
            .is_some_and(|requested| same_server_settings(requested, &config))
        {
            return Ok(());
        }
        if !verified(&config) {
            config
                .options
                .insert(VERIFIED_OPTION.into(), Value::Bool(true));
            self.store.save_source(&config)?;
        }
        self.server_configuration
            .configurations
            .insert(config.id.clone(), config);
        if let Some(connection) = self.server_configuration.connections.get_mut(source) {
            connection.failure = None;
        }
        self.sync_server_configuration_state();
        self.refresh_server_test_choice();
        Ok(())
    }

    pub(crate) fn server_keyboard_is_secret(&self) -> bool {
        matches!(
            self.keyboard_target.as_str(),
            "server-password" | "server-api-key" | "server-token"
        )
    }

    pub(crate) fn clear_server_keyboard(&mut self) {
        self.server_configuration.keyboard = None;
        if self.keyboard_target.starts_with("server-") {
            self.keyboard.set_text("");
            self.keyboard_target.clear();
        }
    }

    /// Project only masked credentials; this method never accesses SQLite.
    pub(crate) fn sync_server_configuration_state(&self) {
        let Some(config) = self
            .server_configuration
            .configurations
            .get(&self.current_source)
        else {
            return;
        };
        let Some(ui) = self.ui.upgrade() else {
            return;
        };
        let state = ui.global::<State>();
        state.set_source_server_address(config.base_url.clone().into());
        state.set_source_server_credential(credential_mask(config).into());
        state.set_source_connection_status(self.server_connection_status(config).into());
        state.set_source_needs_setup(
            !self.options.demo
                && !is_fixture(config)
                && (config.base_url.is_empty() || !verified(config)),
        );
    }

    fn server_connection_status(&self, config: &SourceConfig) -> String {
        let connection = self.server_configuration.connections.get(&config.id);
        let checking = connection
            .and_then(|connection| connection.attempt.as_ref())
            .is_some_and(|attempt| {
                self.server_attempt_is_current(attempt) && self.pending.contains(&attempt.job)
            });
        if checking {
            tr("server_connecting")
        } else if let Some(failure) = connection.and_then(|connection| connection.failure) {
            failure.label()
        } else if verified(config) {
            tr("connected")
        } else if config.base_url.is_empty() {
            tr("not_configured")
        } else {
            tr("server_unverified")
        }
    }

    fn refresh_server_test_choice(&mut self) {
        let Some(config) = self
            .server_configuration
            .configurations
            .get(&self.current_source)
        else {
            return;
        };
        let status = self.server_connection_status(config);
        if self.modal == "choices" && self.modal_context == "server-configuration" {
            if let Some((_, _, value)) = self
                .choice_rows
                .iter_mut()
                .find(|(id, _, _)| id == "server-config:test-connect")
            {
                *value = status;
            }
            self.sync_choice_rows();
        }
    }

    pub(crate) fn server_action(&mut self, action: &str, value: &str) -> Result<()> {
        let source = if value.is_empty() {
            if self.server_configuration.editing_source.is_empty() {
                self.current_source.clone()
            } else {
                self.server_configuration.editing_source.clone()
            }
        } else {
            value.to_owned()
        };
        if source != self.current_source {
            bail!("{}", tr("request_superseded"));
        }
        let config = self
            .server_configuration
            .configurations
            .get(&source)
            .cloned()
            .context("The native server configuration is unavailable")?;
        self.server_configuration.editing_source = source.clone();
        match action {
            "settings" => self.show_server_panel(&source, ServerPanel::Settings),
            "edit-credential" => self.show_server_panel(&source, ServerPanel::Credentials),
            "edit-address" => {
                self.open_server_keyboard("server-address", &config.base_url, &source)
            }
            "edit-username" => {
                self.open_server_keyboard("server-username", &config.username, &source)
            }
            "edit-password" => {
                self.open_server_keyboard("server-password", &config.password, &source)
            }
            "edit-api-key" if !config.kind.eq_ignore_ascii_case("suwayomi") => {
                let key = if config.kind.eq_ignore_ascii_case("kavita") {
                    config
                        .options
                        .get("api_key")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                } else {
                    &config.token
                };
                self.open_server_keyboard("server-api-key", key, &source)
            }
            "edit-token" if !config.kind.eq_ignore_ascii_case("komga") => {
                self.open_server_keyboard("server-token", &config.token, &source)
            }
            "edit-source-id" if config.kind.eq_ignore_ascii_case("suwayomi") => {
                let id = config
                    .options
                    .get("source_id")
                    .map(|value| match value {
                        Value::String(value) => value.clone(),
                        Value::Number(value) => value.to_string(),
                        _ => String::new(),
                    })
                    .unwrap_or_default();
                self.open_server_keyboard("server-source-id", &id, &source)
            }
            "auth-basic" | "auth-session" if config.kind.eq_ignore_ascii_case("suwayomi") => {
                let mut config = config;
                config.token.clear();
                config.options.remove("api_key");
                config.options.insert(
                    "auth_mode".into(),
                    json!(if action == "auth-basic" {
                        "basic"
                    } else {
                        "session"
                    }),
                );
                self.persist_server_configuration(config)?;
                self.show_server_panel(&source, ServerPanel::Credentials)
            }
            "clear-credential" => {
                let mut config = config;
                config.username.clear();
                config.password.clear();
                config.token.clear();
                config.options.remove("api_key");
                self.persist_server_configuration(config)?;
                self.show_server_panel(&source, ServerPanel::Credentials)
            }
            "test-connect" => self.test_server_connection(&source),
            _ => Ok(()),
        }
    }

    fn show_server_panel(&mut self, source: &str, panel: ServerPanel) -> Result<()> {
        let config = self
            .server_configuration
            .configurations
            .get(source)
            .context("The native server configuration is unavailable")?;
        let row = |action: &str, label: &str, value: String| {
            (format!("server-config:{action}"), tr(label), value)
        };
        let (title, context, mut rows) = match panel {
            ServerPanel::Settings => (
                "source_preferences",
                "server-configuration",
                vec![
                    row("edit-address", "server_url", config.base_url.clone()),
                    row(
                        "edit-credential",
                        "server_credentials",
                        credential_mask(config),
                    ),
                ],
            ),
            ServerPanel::Credentials => {
                let mut rows = vec![
                    row("edit-username", "username", config.username.clone()),
                    row("edit-password", "password", mask(&config.password)),
                ];
                if !config.kind.eq_ignore_ascii_case("suwayomi") {
                    let key = if config.kind.eq_ignore_ascii_case("kavita") {
                        config
                            .options
                            .get("api_key")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                    } else {
                        &config.token
                    };
                    rows.push(row("edit-api-key", "api_key", mask(key)));
                }
                if !config.kind.eq_ignore_ascii_case("komga") {
                    rows.push(row("edit-token", "server_token", mask(&config.token)));
                }
                if config.kind.eq_ignore_ascii_case("suwayomi") {
                    let basic = matches!(
                        config.options.get("auth_mode").and_then(Value::as_str),
                        Some("basic" | "basic_auth")
                    );
                    rows.push(row(
                        "auth-basic",
                        "server_basic_auth",
                        if basic { "✓" } else { "" }.into(),
                    ));
                    rows.push(row(
                        "auth-session",
                        "server_session_auth",
                        if !basic { "✓" } else { "" }.into(),
                    ));
                }
                rows.push(row(
                    "clear-credential",
                    "server_clear_credentials",
                    String::new(),
                ));
                ("server_credentials", "server-credentials", rows)
            }
        };
        if matches!(panel, ServerPanel::Settings) {
            if config.kind.eq_ignore_ascii_case("suwayomi") {
                let id = config
                    .options
                    .get("source_id")
                    .map(|value| match value {
                        Value::String(value) => value.clone(),
                        Value::Number(value) => value.to_string(),
                        _ => String::new(),
                    })
                    .unwrap_or_default();
                rows.push(row("edit-source-id", "server_source_id", id));
            }
            rows.push(row(
                "test-connect",
                "test_and_connect",
                self.server_connection_status(config),
            ));
        }
        self.show_choices(&tr(title), context, rows)
    }

    fn open_server_keyboard(&mut self, target: &str, text: &str, source: &str) -> Result<()> {
        let return_panel = match (self.modal.as_str(), self.modal_context.as_str()) {
            ("choices", "server-configuration") => Some(ServerPanel::Settings),
            ("choices", "server-credentials") => Some(ServerPanel::Credentials),
            _ => None,
        };
        if let Some(attempt) = self
            .server_configuration
            .connections
            .get_mut(source)
            .and_then(|connection| connection.attempt.take())
        {
            self.pending.remove(&attempt.job);
            self.busy = !self.pending.is_empty();
        }
        self.server_configuration.keyboard = Some(ServerKeyboard {
            source: source.into(),
            source_revision: self.source_revision,
            return_panel,
        });
        self.keyboard.set_text(text);
        self.keyboard.set_mode("english");
        self.keyboard_target = target.into();
        self.modal = "keyboard".into();
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_keyboard_mode("english".into());
            state.set_modal_title(
                tr(match target {
                    "server-address" => "server_url",
                    "server-username" => "username",
                    "server-password" => "password",
                    "server-api-key" => "api_key",
                    "server-token" => "server_token",
                    _ => "server_source_id",
                })
                .into(),
            );
        }
        Ok(())
    }

    pub(crate) fn save_server_keyboard(&mut self, target: &str, text: &str) -> Result<bool> {
        if !matches!(
            target,
            "server-address"
                | "server-username"
                | "server-password"
                | "server-api-key"
                | "server-token"
                | "server-source-id"
        ) {
            return Ok(false);
        }
        self.keyboard.set_text("");
        self.keyboard_target.clear();
        let keyboard = self
            .server_configuration
            .keyboard
            .take()
            .context("The native server input is no longer active")?;
        if keyboard.source != self.current_source
            || keyboard.source_revision != self.source_revision
        {
            bail!("{}", tr("request_superseded"));
        }
        let mut config = self
            .server_configuration
            .configurations
            .get(&keyboard.source)
            .cloned()
            .context("The native server configuration is unavailable")?;
        match target {
            "server-address" => config.base_url = server_address(text)?,
            "server-username" => {
                config.username = credential_text(text, 1024, true)?;
                config.token.clear();
                config.options.remove("api_key");
            }
            "server-password" => {
                config.password = credential_text(text, 4096, false)?;
                config.token.clear();
                config.options.remove("api_key");
            }
            "server-api-key" => {
                let key = credential_text(text, 16384, true)?;
                config.username.clear();
                config.password.clear();
                if config.kind.eq_ignore_ascii_case("kavita") {
                    config.token.clear();
                    config.options.insert("api_key".into(), json!(key));
                } else if config.kind.eq_ignore_ascii_case("komga") {
                    config.token = key;
                    config.options.remove("api_key");
                } else {
                    bail!("{}", tr("server_invalid_credential"));
                }
            }
            "server-token" => {
                if config.kind.eq_ignore_ascii_case("komga") {
                    bail!("{}", tr("server_invalid_credential"));
                }
                config.token = credential_text(text, 16384, true)?;
                config.username.clear();
                config.password.clear();
                config.options.remove("api_key");
            }
            "server-source-id" => {
                let id = text.trim();
                if !config.kind.eq_ignore_ascii_case("suwayomi")
                    || id.len() > 128
                    || (!id.is_empty() && id.parse::<i64>().is_err())
                {
                    bail!("{}", tr("server_invalid_source_id"));
                }
                config.options.insert("source_id".into(), json!(id));
            }
            _ => unreachable!(),
        }
        self.persist_server_configuration(config)?;
        if let Some(panel) = keyboard.return_panel {
            self.show_server_panel(&keyboard.source, panel)?;
        }
        Ok(true)
    }

    fn persist_server_configuration(&mut self, mut config: SourceConfig) -> Result<()> {
        config
            .options
            .insert(VERIFIED_OPTION.into(), Value::Bool(false));
        self.store.save_source(&config)?;
        self.source_images_changed();
        if config.id == self.current_source {
            self.invalidate_catalog_work();
            self.catalog.clear();
            self.catalog_remote_page = 1;
            self.catalog_has_more = false;
            self.catalog_query = None;
            self.catalog_filter = 0;
            self.page = 1;
        }
        self.server_configuration.connections.remove(&config.id);
        self.server_configuration
            .configurations
            .insert(config.id.clone(), config);
        self.sync_server_configuration_state();
        self.notify(tr("saved"));
        Ok(())
    }

    fn test_server_connection(&mut self, source: &str) -> Result<()> {
        if self.options.offline || self.options.demo {
            bail!("{}", tr("offline_help"));
        }
        let config = self
            .server_configuration
            .configurations
            .get(source)
            .context("The native server configuration is unavailable")?;
        if is_fixture(config) {
            bail!("{}", tr("offline_help"));
        }
        if server_address(&config.base_url)?.is_empty() {
            bail!("{}", tr("server_invalid_url"));
        }
        if config.kind.eq_ignore_ascii_case("suwayomi") {
            let valid = config.options.get("source_id").is_some_and(|id| match id {
                Value::String(id) => !id.is_empty() && id.parse::<i64>().is_ok(),
                Value::Number(id) => id.as_i64().is_some(),
                _ => false,
            });
            if !valid {
                bail!("{}", tr("server_invalid_source_id"));
            }
        }
        if self
            .server_configuration
            .connections
            .get(source)
            .and_then(|state| state.attempt.as_ref())
            .is_some_and(|attempt| {
                self.server_attempt_is_current(attempt) && self.pending.contains(&attempt.job)
            })
        {
            return Ok(());
        }
        self.server_configuration.next_attempt =
            self.server_configuration.next_attempt.wrapping_add(1);
        let sequence = self.server_configuration.next_attempt;
        let source_id = source.to_owned();
        let sort = self
            .catalog_listings
            .get(self.catalog_filter.max(0) as usize)
            .map(|(id, _)| id.clone())
            .unwrap_or_else(|| "popular".into());
        let job = self.spawn_job_kind(true, JobKind::ServerConnection, move |manager| {
            let result = manager
                .browse_page(&source_id, 1, &sort)
                .map(|page| ConnectedCatalog {
                    books: page.books,
                    has_next: page.has_next,
                    listings: manager.listings(&source_id).unwrap_or_default(),
                })
                .map_err(|error| ServerFailure::from_error(&error));
            Ok(JobResult::ServerConnected(ServerConnection {
                source: source_id,
                sequence,
                result,
            }))
        })?;
        let attempt = ConnectionAttempt {
            job,
            sequence,
            source_revision: self.source_revision,
            lifecycle_generation: self.lifecycle_generation,
            network_epoch: self.network_control.epoch(),
            catalog_generation: self.catalog_generation,
            catalog_filter: self.catalog_filter,
        };
        self.server_configuration.connections.insert(
            source.into(),
            ConnectionState {
                attempt: Some(attempt),
                failure: None,
            },
        );
        self.sync_server_configuration_state();
        self.refresh_server_test_choice();
        Ok(())
    }

    fn server_attempt_is_current(&self, attempt: &ConnectionAttempt) -> bool {
        !self.suspended
            && attempt.source_revision == self.source_revision
            && attempt.lifecycle_generation == self.lifecycle_generation
            && attempt.network_epoch == self.network_control.epoch()
    }

    /// Clear discarded, suspended, and failed-to-start requests without a toast.
    pub(crate) fn reconcile_server_connection_state(&mut self) {
        let suspended = self.suspended;
        let revision = self.source_revision;
        let lifecycle = self.lifecycle_generation;
        let epoch = self.network_control.epoch();
        let mut changed = false;
        for connection in self.server_configuration.connections.values_mut() {
            if connection.attempt.as_ref().is_some_and(|attempt| {
                suspended
                    || attempt.source_revision != revision
                    || attempt.lifecycle_generation != lifecycle
                    || attempt.network_epoch != epoch
                    || !self.pending.contains(&attempt.job)
            }) {
                connection.attempt = None;
                connection.failure = None;
                changed = true;
            }
        }
        if changed {
            self.refresh_server_test_choice();
        }
    }

    /// Consume pool setup failures using the same redacted status as HTTP errors.
    pub(crate) fn server_connection_job_failed(&mut self, job: u64, error: &anyhow::Error) -> bool {
        let mut source = None;
        for (id, connection) in &mut self.server_configuration.connections {
            if connection
                .attempt
                .as_ref()
                .is_some_and(|attempt| attempt.job == job)
            {
                connection.attempt = None;
                connection.failure = None;
                source = Some(id.clone());
                break;
            }
        }
        let Some(source) = source else {
            return false;
        };
        if !source_jobs::is_superseded(error) {
            let failure = ServerFailure::from_error(error);
            if self.record_server_failure(&source, failure).is_err() {
                self.notify(failure.label());
            }
        }
        self.sync_server_configuration_state();
        self.refresh_server_test_choice();
        true
    }

    fn record_server_failure(&mut self, source: &str, failure: ServerFailure) -> Result<()> {
        self.server_configuration
            .connections
            .entry(source.into())
            .or_default()
            .failure = Some(failure);
        if let Some(mut config) = self
            .server_configuration
            .configurations
            .get(source)
            .cloned()
            && verified(&config)
        {
            config
                .options
                .insert(VERIFIED_OPTION.into(), Value::Bool(false));
            self.server_configuration
                .configurations
                .insert(config.id.clone(), config.clone());
            self.store.save_source(&config)?;
            self.source_images_changed();
        }
        self.sync_server_configuration_state();
        self.refresh_server_test_choice();
        self.notify(failure.label());
        Ok(())
    }

    pub(crate) fn complete_server_connection(
        &mut self,
        connection: ServerConnection,
    ) -> Result<()> {
        let attempt = self
            .server_configuration
            .connections
            .get_mut(&connection.source)
            .and_then(|state| {
                if state
                    .attempt
                    .as_ref()
                    .is_some_and(|attempt| attempt.sequence == connection.sequence)
                {
                    state.attempt.take()
                } else {
                    None
                }
            });
        let Some(attempt) = attempt else {
            return Ok(());
        };
        if !self.server_attempt_is_current(&attempt) {
            return Ok(());
        }
        let Some(mut config) = self
            .server_configuration
            .configurations
            .get(&connection.source)
            .cloned()
        else {
            return Ok(());
        };
        match connection.result {
            Err(failure) => {
                self.record_server_failure(&connection.source, failure)?;
            }
            Ok(catalog) => {
                config
                    .options
                    .insert(VERIFIED_OPTION.into(), Value::Bool(true));
                config
                    .options
                    .insert("_cached_listings".into(), json!(catalog.listings));
                self.store.save_source(&config)?;
                self.source_images_changed();
                self.server_configuration
                    .configurations
                    .insert(config.id.clone(), config);
                let showing_catalog = self.route == "catalog"
                    || (self.route == "sources"
                        && self.platform.profile().is_scribe()
                        && self.source_filter == 0);
                if showing_catalog
                    && self.current_source == connection.source
                    && self.catalog_generation == attempt.catalog_generation
                    && self.catalog_filter == attempt.catalog_filter
                {
                    for book in &catalog.books {
                        self.store.upsert_book(book)?;
                    }
                    self.catalog = catalog.books.clone();
                    self.catalog_remote_page = 1;
                    self.catalog_has_more = catalog.has_next;
                    self.catalog_listings = catalog.listings;
                    self.catalog_filter = self
                        .catalog_filter
                        .min(self.catalog_listings.len().saturating_sub(1) as i32);
                    self.page = 1;
                    self.request_covers(&catalog.books)?;
                    if self.modal == "choices"
                        && matches!(
                            self.modal_context.as_str(),
                            "server-configuration" | "server-credentials"
                        )
                    {
                        self.modal.clear();
                    }
                }
                self.sync_server_configuration_state();
                self.notify(tr("connected"));
            }
        }
        Ok(())
    }
}
