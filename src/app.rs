mod actions;
mod cover_jobs;
mod diagnostics;
mod download_usage;
mod interaction_models;
mod lifecycle;
mod migration;
mod orientation;
mod reader_pipeline;
mod reader_recovery;
mod retained_scene;
mod search_scope;
mod server_configuration;
mod source_jobs;
mod source_migration;
mod storage_details;
mod view;

use interaction_models::InteractionState;
use reader_recovery::{ReaderRecovery, ReaderTarget};

use std::{
    cell::RefCell,
    collections::{HashMap, HashSet, VecDeque},
    fs,
    path::{Path, PathBuf},
    rc::Rc,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail};
use image::GrayImage;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use slint::{ComponentHandle, Image, Model};

use crate::{
    domain::{
        Book, CachedPageUpdate as CachedPage, Chapter, NavigationSession, Page, Preferences,
        Progress, ReaderSession, SourceConfig, cached_page_identity,
    },
    downloads::{DownloadEvent, DownloadWorker},
    imaging::{self, ImageOptions},
    input::KeyboardInput,
    local::{ImportedBook, LocalLibrary},
    pairing::{PairingEvent, PairingServer},
    platform::{Platform, RefreshKind},
    reader::{ReaderState, ReaderUpdate, RefreshRequest},
    sources::{FetchPriority, Repository, RepositorySource, SourceManager, SourceSetting},
    store::Store,
    tr,
    ui::{AppWindow, Commands, State},
};

#[derive(Debug, Clone)]
pub struct AppOptions {
    pub data_dir: PathBuf,
    pub demo: bool,
    pub offline: bool,
    pub initial_screen: String,
    pub landscape: bool,
    pub resume: bool,
}

#[derive(Debug, Clone, serde::Serialize)]
pub(crate) struct DisplayedReader {
    pub session: ReaderSession,
    pub preferences: Preferences,
    pub first_path: PathBuf,
    pub second_path: Option<PathBuf>,
}

#[derive(Debug, Clone)]
pub(crate) struct SearchResult {
    pub id: String,
    pub title: String,
    pub books: Vec<Book>,
    pub error: Option<String>,
    pub has_next: bool,
    pub pending: bool,
}

enum JobResult {
    Catalog {
        source: String,
        books: Vec<Book>,
        remote_page: u32,
        has_next: bool,
        listings: Vec<(String, String)>,
    },
    Detail {
        book: Book,
        chapters: Vec<Chapter>,
    },
    Pages {
        chapter_id: String,
        page: u32,
        pages: Vec<Page>,
    },
    ReaderImage {
        chapter_id: String,
        page: u32,
        offset: u32,
        first: Arc<GrayImage>,
        second: Option<Arc<GrayImage>>,
        scaled_height: u32,
        viewport_height: u32,
        preferences: Preferences,
        first_path: PathBuf,
        second_path: Option<PathBuf>,
        paths: Vec<CachedPage>,
    },
    Prefetched {
        chapter_id: String,
        paths: Vec<CachedPage>,
    },
    Cover {
        id: String,
        uri: String,
        path: PathBuf,
        image: Arc<GrayImage>,
    },
    DownloadUsage(download_usage::UsageResult),
    StorageDetails(storage_details::StorageDetailsResult),
    Search(SearchResult),
    Imported(Vec<ImportedBook>),
    Repository(Repository),
    Installed(SourceConfig),
    SourcesInstalled {
        installed: Vec<SourceConfig>,
        errors: Vec<String>,
    },
    SourceOptionSaved(SourceConfig),
    InteractionDone(String),
    Settings(Vec<SourceSetting>),
    MigrationCandidates(migration::CandidateResult),
    MigrationPrepared(migration::PreparedMigration),
    ServerConnected(server_configuration::ServerConnection),
    Updated(Vec<(Book, Vec<Chapter>)>),
    CacheCleared,
}

#[derive(Debug, Clone)]
enum JobKind {
    General,
    Chapter,
    Catalog,
    Search,
    Reader,
    Prefetch,
    Cover(String),
    DownloadUsage,
    StorageDetails,
    Migration,
    ServerConnection,
}

#[derive(Debug, Clone)]
struct JobContext {
    kind: JobKind,
    reader_target: Option<ReaderTarget>,
    reader_generation: u64,
    reader_refresh: RefreshRequest,
    search_generation: u64,
    catalog_generation: u64,
    query: String,
    search_scope: String,
    source: String,
    catalog_filter: i32,
    source_revision: u64,
    lifecycle_generation: u64,
}

struct JobMessage {
    id: u64,
    foreground: bool,
    source_dependent: bool,
    context: JobContext,
    result: Result<JobResult>,
}

pub struct App {
    pub(crate) ui: slint::Weak<AppWindow>,
    pub(crate) options: AppOptions,
    pub(crate) platform: Rc<Platform>,
    pub(crate) data_dir: PathBuf,
    pub(crate) store: Store,
    pub(crate) disk_cache: crate::cache::CacheManager,
    network_control: crate::sources::NetworkControl,
    pub(crate) prefs: Preferences,
    pub(crate) interactions: InteractionState,
    pub(crate) migration: migration::MigrationState,
    pub(crate) server_configuration: server_configuration::ServerState,
    pub(crate) route: String,
    pub(crate) return_route: String,
    pub(crate) page: usize,
    pub(crate) library_filter: i32,
    pub(crate) source_filter: i32,
    pub(crate) catalog_filter: i32,
    pub(crate) history_filter: i32,
    pub(crate) sort_title: bool,
    pub(crate) unread_only: bool,
    pub(crate) downloaded_only: bool,
    pub(crate) catalog_unread_only: bool,
    pub(crate) catalog_downloaded_only: bool,
    pub(crate) selection_mode: bool,
    pub(crate) selected: HashSet<String>,
    pub(crate) selected_chapters: HashSet<String>,
    pub(crate) current_book_id: Option<String>,
    pub(crate) current_source: String,
    pub(crate) catalog: Vec<Book>,
    pub(crate) catalog_remote_page: u32,
    pub(crate) catalog_has_more: bool,
    pub(crate) catalog_query: Option<String>,
    pub(crate) catalog_listings: Vec<(String, String)>,
    pub(crate) available: Vec<RepositorySource>,
    pub(crate) source_settings: Vec<SourceSetting>,
    pub(crate) query: String,
    pub(crate) search_scope: String,
    pub(crate) search_results: Vec<SearchResult>,
    pub(crate) reader: Option<ReaderState>,
    pub(crate) displayed_reader: Option<DisplayedReader>,
    reader_recovery: Option<ReaderRecovery>,
    displayed_image_leases: Vec<crate::cache::CacheLease>,
    resume_reader_repair: bool,
    resume_frame_pending: bool,
    reader_image_jobs_started: u64,
    pub(crate) suspended: bool,
    suspend_checkpoint_ready: bool,
    pub(crate) cover_cache: HashMap<(String, PathBuf), Image>,
    pub(crate) cover_paths: HashMap<String, PathBuf>,
    pub(crate) cover_uris: HashMap<String, String>,
    pub(crate) modal: String,
    pub(crate) modal_context: String,
    pub(crate) keyboard: KeyboardInput,
    pub(crate) keyboard_target: String,
    pub(crate) panel_pages: Vec<String>,
    pub(crate) panel_page: usize,
    pub(crate) choice_rows: Vec<(String, String, String)>,
    pub(crate) choice_total: usize,
    pub(crate) choice_disabled: HashSet<String>,
    pub(crate) busy: bool,
    pub(crate) message: String,
    message_until: Option<Instant>,
    pairing: Option<PairingServer>,
    downloads: DownloadWorker,
    book_removal_completions: u64,
    last_book_removal: Option<Value>,
    job_tx: Sender<JobMessage>,
    job_rx: Receiver<JobMessage>,
    next_job: u64,
    pending: HashSet<u64>,
    cover_pending: HashSet<String>,
    cover_queue: VecDeque<Book>,
    cover_inflight: Option<u64>,
    cover_view: Vec<String>,
    cover_retry_after: HashMap<String, Instant>,
    source_revision: u64,
    source_configuration_revision: Arc<AtomicU64>,
    source_configurations: Option<Arc<Vec<SourceConfig>>>,
    source_manager_pool: Arc<source_jobs::SourceManagerPool>,
    foreground_ready_pending: Arc<AtomicBool>,
    foreground_ready_posts: Arc<AtomicU64>,
    pub(crate) cached_download_bytes: Option<u64>,
    download_usage: download_usage::UsageTracker,
    storage_details: storage_details::StorageDetailsTracker,
    background_ui_dirty: bool,
    download_ui_dirty: bool,
    next_background_ui: Instant,
    lifecycle_generation: u64,
    resume_network_pending: bool,
    network_probe_inflight: bool,
    network_probe_rx: Option<Receiver<(u64, bool)>>,
    next_network_probe: Instant,
    last_session_save: Instant,
    committed_progress: Option<Progress>,
    committed_progress_dirty: bool,
    reader_generation: u64,
    chapter_inflight: Option<u64>,
    reader_inflight: Option<u64>,
    reader_dirty: bool,
    reader_refresh: RefreshRequest,
    frame_refresh: Option<RefreshKind>,
    prefetch_inflight: Option<u64>,
    prefetch_dirty: bool,
    search_generation: u64,
    catalog_generation: u64,
    shutting_down: bool,
}

impl App {
    pub fn gesture_context(&self) -> (&str, &str) {
        (&self.route, &self.modal)
    }

    pub fn attach(
        ui: &AppWindow,
        options: AppOptions,
        platform: Rc<Platform>,
    ) -> Result<Rc<RefCell<Self>>> {
        let data_dir = if options.demo {
            options.data_dir.join("demo")
        } else {
            options.data_dir.clone()
        };
        fs::create_dir_all(&data_dir).context("Create the application data directory")?;
        let database = data_dir.join("library.sqlite3");
        let store = Store::open(&database)?;
        let disk_cache = crate::cache::CacheManager::open(&data_dir)?;
        let network_control = crate::sources::NetworkControl::open(&data_dir)?;
        if options.resume {
            network_control.suspend();
        }
        if options.demo {
            crate::demo::seed_for_screen(
                &store,
                &data_dir,
                normalize_route(&options.initial_screen),
            )?;
        }
        if store.sources()?.is_empty() {
            for source in default_sources() {
                store.save_source(&source)?;
            }
        }
        let mut prefs = store.preferences()?;
        if options.landscape {
            prefs.landscape = true;
            prefs.reverse_landscape = false;
        }
        if !platform.profile().has_frontlight() {
            prefs.brightness = 0;
            prefs.warmth = 0;
        }
        let initial_rotation = orientation::reader_display_rotation_degrees(
            normalize_route(&options.initial_screen),
            &prefs,
        );
        let initial_landscape = matches!(initial_rotation, 90 | 270);
        let (width, height) = platform.runtime_logical_size(initial_landscape);
        ui.set_canvas_width(width as f32);
        ui.set_canvas_height(height as f32);
        platform.configure_window_rotation(ui.window(), initial_rotation)?;
        ui.global::<State>().set_landscape(initial_landscape);
        platform.apply_preferences(&prefs)?;
        let download_options = crate::downloads::DownloadOptions {
            offline: options.offline || options.demo,
            ..crate::downloads::DownloadOptions::default()
        };
        let downloads = DownloadWorker::start_initialized(
            data_dir.clone(),
            database,
            download_options,
            options.resume,
        )?;
        let (job_tx, job_rx) = mpsc::channel();
        let route = normalize_route(&options.initial_screen).to_owned();
        let current_book_id = if options.demo {
            Some(crate::demo::primary_id())
        } else {
            store.library_continuation(None)?
        };
        let resume_network_pending = options.resume && !options.offline && !options.demo;
        let interactions = InteractionState::load(&store)?;
        let mut app = Self {
            ui: ui.as_weak(),
            options,
            platform,
            data_dir,
            store,
            disk_cache,
            network_control,
            prefs,
            interactions,
            migration: migration::MigrationState::default(),
            server_configuration: server_configuration::ServerState::default(),
            route,
            return_route: "library".into(),
            page: 1,
            library_filter: 0,
            source_filter: 0,
            catalog_filter: 0,
            history_filter: 0,
            sort_title: false,
            unread_only: false,
            downloaded_only: false,
            catalog_unread_only: false,
            catalog_downloaded_only: false,
            selection_mode: false,
            selected: HashSet::new(),
            selected_chapters: HashSet::new(),
            current_book_id,
            current_source: "mangadex".into(),
            catalog: Vec::new(),
            catalog_remote_page: 1,
            catalog_has_more: false,
            catalog_query: None,
            catalog_listings: vec![
                ("popular".into(), tr("popular")),
                ("latest".into(), tr("recently_updated")),
                ("new".into(), tr("newly_added")),
            ],
            available: Vec::new(),
            source_settings: Vec::new(),
            query: String::new(),
            search_scope: "all".into(),
            search_results: Vec::new(),
            reader: None,
            displayed_reader: None,
            reader_recovery: None,
            displayed_image_leases: Vec::new(),
            resume_reader_repair: false,
            resume_frame_pending: false,
            reader_image_jobs_started: 0,
            suspended: false,
            suspend_checkpoint_ready: false,
            cover_cache: HashMap::new(),
            cover_paths: HashMap::new(),
            cover_uris: HashMap::new(),
            modal: String::new(),
            modal_context: String::new(),
            keyboard: KeyboardInput::default(),
            keyboard_target: String::new(),
            panel_pages: Vec::new(),
            panel_page: 0,
            choice_rows: Vec::new(),
            choice_total: 0,
            choice_disabled: HashSet::new(),
            busy: false,
            message: String::new(),
            message_until: None,
            pairing: None,
            downloads,
            book_removal_completions: 0,
            last_book_removal: None,
            job_tx,
            job_rx,
            next_job: 1,
            pending: HashSet::new(),
            cover_pending: HashSet::new(),
            cover_queue: VecDeque::new(),
            cover_inflight: None,
            cover_view: Vec::new(),
            cover_retry_after: HashMap::new(),
            source_revision: 0,
            source_configuration_revision: Arc::new(AtomicU64::new(0)),
            source_configurations: None,
            source_manager_pool: Arc::new(source_jobs::SourceManagerPool::default()),
            foreground_ready_pending: Arc::new(AtomicBool::new(false)),
            foreground_ready_posts: Arc::new(AtomicU64::new(0)),
            cached_download_bytes: None,
            download_usage: download_usage::UsageTracker::default(),
            storage_details: storage_details::StorageDetailsTracker::default(),
            background_ui_dirty: false,
            download_ui_dirty: false,
            next_background_ui: Instant::now(),
            lifecycle_generation: 0,
            resume_network_pending,
            network_probe_inflight: false,
            network_probe_rx: None,
            next_network_probe: Instant::now(),
            last_session_save: Instant::now(),
            committed_progress: None,
            committed_progress_dirty: false,
            reader_generation: 0,
            chapter_inflight: None,
            reader_inflight: None,
            reader_dirty: false,
            reader_refresh: RefreshRequest::None,
            frame_refresh: None,
            prefetch_inflight: None,
            prefetch_dirty: false,
            search_generation: 0,
            catalog_generation: 0,
            shutting_down: false,
        };
        if app.options.demo {
            app.available = crate::demo::available_sources();
            if app.route == "search" {
                app.query = demo_query();
                app.run_search()?;
            }
        }
        if !app.options.resume && app.route == "reader" {
            app.route = "detail".into();
            app.resume_reader()?;
        }
        if !app.options.resume && app.options.initial_screen == "downloads" {
            app.route = "settings".into();
            app.open_downloads()?;
        }
        if app.options.resume {
            app.restore_saved_session()?;
        }
        app.prepare_demo_catalog_route()?;
        app.sync_view()?;
        if app.route == "settings" {
            app.refresh_setting_details()?;
        }
        if app.refresh_visible_covers()? {
            app.sync_view()?;
        }
        let application = Rc::new(RefCell::new(app));
        let weak = Rc::downgrade(&application);
        ui.global::<Commands>()
            .on_action(move |section, action, value| {
                if let Some(application) = weak.upgrade() {
                    let mut application = application.borrow_mut();
                    if let Err(error) =
                        application.dispatch(section.as_str(), action.as_str(), value.as_str())
                    {
                        application.show_error(error);
                    }
                }
            });
        let weak = Rc::downgrade(&application);
        ui.global::<Commands>().on_refresh(move |kind| {
            if let Some(application) = weak.upgrade() {
                let application = application.borrow();
                application.platform.refresh(match kind.as_str() {
                    "fast" => RefreshKind::Fast,
                    "partial" => RefreshKind::Partial,
                    "full" => RefreshKind::Full,
                    _ => RefreshKind::Ui,
                });
            }
        });
        Ok(application)
    }

    pub fn poll(&mut self) {
        if self.shutting_down || self.suspended || self.resume_frame_pending {
            return;
        }
        self.poll_network_resume();
        self.platform
            .set_download_activity(self.downloads.active_count() > 0);
        let mut changed = false;
        while let Ok(message) = self.job_rx.try_recv() {
            self.pending.remove(&message.id);
            self.busy = !self.pending.is_empty();
            if matches!(message.context.kind, JobKind::Migration)
                && !self.migration_job_is_active(message.id)
            {
                continue;
            }
            changed |= self.finish_migration_job(message.id, message.result.as_ref().err());
            self.finish_usage_job(message.id);
            if self.finish_storage_details_job(message.id) && self.storage_details_visible() {
                self.background_ui_dirty = true;
            }
            self.busy = !self.pending.is_empty();
            if message.context.lifecycle_generation != self.lifecycle_generation {
                continue;
            }
            let reader_job = matches!(&message.context.kind, JobKind::Reader);
            let prefetch_job = matches!(&message.context.kind, JobKind::Prefetch);
            if matches!(message.context.kind, JobKind::Chapter)
                && self.chapter_inflight == Some(message.id)
            {
                self.chapter_inflight = None;
            }
            if matches!(&message.context.kind, JobKind::Cover(_))
                && self.cover_inflight == Some(message.id)
            {
                self.cover_inflight = None;
            }
            if reader_job && self.reader_inflight == Some(message.id) {
                self.reader_inflight = None;
            }
            if prefetch_job && self.prefetch_inflight == Some(message.id) {
                self.prefetch_inflight = None;
            }
            if message.foreground {
                self.busy = !self.pending.is_empty();
            }
            let superseded = message.source_dependent
                && (message.context.source_revision != self.source_revision
                    || message
                        .result
                        .as_ref()
                        .err()
                        .is_some_and(source_jobs::is_superseded));
            if superseded {
                self.retry_superseded_job(&message.context);
            } else {
                match message.result {
                    Ok(result) => {
                        if let Err(error) = self.complete_job(result, &message.context) {
                            if matches!(message.context.kind, JobKind::Reader | JobKind::Chapter) {
                                self.reader_job_failed(&message.context, error);
                            } else if matches!(message.context.kind, JobKind::Search) {
                                self.fail_search_group(&message.context, &error.to_string());
                            } else if message.foreground {
                                self.show_error(error);
                            } else {
                                eprintln!("aidoku: background result: {error:#}");
                            }
                        }
                    }
                    Err(error) => {
                        if self.server_connection_job_failed(message.id, &error) {
                        } else if matches!(message.context.kind, JobKind::ServerConnection) {
                            eprintln!("aidoku: a superseded server connection was discarded");
                        } else if let JobKind::Cover(id) = &message.context.kind {
                            self.cover_pending.remove(id);
                            self.cover_retry_after
                                .insert(id.clone(), Instant::now() + Duration::from_secs(30));
                            eprintln!("aidoku: cover {id}: {error:#}");
                        } else if prefetch_job {
                            eprintln!("aidoku: read-ahead: {error:#}");
                        } else if matches!(
                            message.context.kind,
                            JobKind::DownloadUsage | JobKind::StorageDetails
                        ) {
                            eprintln!("aidoku: offline storage accounting: {error:#}");
                        } else if matches!(message.context.kind, JobKind::Search) {
                            self.fail_search_group(&message.context, &error.to_string());
                        } else if matches!(message.context.kind, JobKind::Reader | JobKind::Chapter)
                        {
                            self.reader_job_failed(&message.context, error);
                        } else if self.job_context_is_current(&message.context) {
                            self.show_error(error);
                        }
                    }
                }
            }
            if reader_job && self.reader_dirty {
                if let Err(error) = self.start_reader_image_job() {
                    self.reader_dirty = false;
                    self.show_error(error);
                }
            }
            if prefetch_job && self.prefetch_dirty {
                if let Err(error) = self.start_reader_prefetch() {
                    self.prefetch_dirty = false;
                    eprintln!("aidoku: read-ahead: {error:#}");
                }
            }
            if matches!(&message.context.kind, JobKind::Cover(_)) {
                self.background_ui_dirty = true;
                self.invalidate_storage_details();
            } else if !prefetch_job
                && !matches!(
                    message.context.kind,
                    JobKind::DownloadUsage | JobKind::StorageDetails
                )
            {
                changed = true;
            }
        }
        self.reconcile_server_connection_state();
        for event in self.downloads.drain_events() {
            match event {
                DownloadEvent::BatchAccepted {
                    pending, paused, ..
                } => {
                    self.notify(tr(if pending > 0 {
                        "queued"
                    } else if paused > 0 {
                        "downloads_paused"
                    } else {
                        "no_downloads_needed"
                    }));
                    changed = true;
                }
                DownloadEvent::Completed(job) => {
                    self.queue_completed_download_usage(&job.chapter_id);
                    self.invalidate_storage_details();
                    if self
                        .store
                        .has_unfinished_downloads()
                        .is_ok_and(|pending| !pending)
                    {
                        self.notify(tr("download_complete"));
                    }
                    changed = true;
                }
                DownloadEvent::Failed(job) => {
                    self.notify(format!("{}: {}", tr("download_failed"), job.error));
                    changed = true;
                }
                DownloadEvent::Error(error) => {
                    self.notify(format!("{}: {error}", tr("download_failed")));
                    changed = true;
                }
                DownloadEvent::BookRemovalFinished {
                    book_id,
                    removed,
                    cancelled,
                    failed,
                    retained_local,
                    usage_changed,
                    error,
                } => {
                    self.book_removal_completions = self.book_removal_completions.saturating_add(1);
                    self.last_book_removal = Some(json!({
                        "book_id": book_id, "completion_sequence": self.book_removal_completions,
                        "removed": removed, "cancelled": cancelled, "failed": failed,
                        "retained_local": retained_local, "usage_changed": usage_changed,
                        "had_error": error.is_some(), "finalized": true,
                    }));
                    if usage_changed {
                        self.invalidate_download_usage();
                    } else if removed + cancelled > 0 {
                        self.invalidate_storage_details();
                    }
                    if let Some(error) = error {
                        eprintln!("aidoku: book download cleanup: {error}");
                    }
                    let status = if failed > 0 {
                        "download_cleanup_partial"
                    } else if removed + cancelled + retained_local == 0 {
                        "download_cleanup_empty"
                    } else {
                        "download_cleanup_complete"
                    };
                    self.notify(format!(
                        "{} · {} {} · {} {} · {} {} · {} {}",
                        tr(status),
                        removed,
                        tr("download_cleanup_removed"),
                        cancelled,
                        tr("download_cleanup_cancelled"),
                        retained_local,
                        tr("download_cleanup_retained_local"),
                        failed,
                        tr("download_cleanup_failed")
                    ));
                    changed = true;
                }
                DownloadEvent::Removed { .. } | DownloadEvent::DownloadedMetadataChanged { .. } => {
                    self.invalidate_download_usage();
                    self.background_ui_dirty = true;
                }
                DownloadEvent::QueueChanged { .. }
                | DownloadEvent::StateChanged(_)
                | DownloadEvent::Progress(_) => {
                    self.background_ui_dirty = true;
                }
                _ => {}
            }
            self.download_ui_dirty = true;
        }
        let pairing_events: Vec<_> = self
            .pairing
            .as_ref()
            .map(|server| server.events.try_iter().collect())
            .unwrap_or_default();
        for event in pairing_events {
            let result = match event {
                PairingEvent::Source(source) => self.store.save_source(&source).map(|()| {
                    self.source_images_changed();
                    self.notify(tr("saved"));
                }),
                PairingEvent::Repository(url) => self.fetch_repository(&url),
                PairingEvent::Import(path) => self.import_local(&path),
            };
            if let Err(error) = result {
                self.show_error(error);
            }
            changed = true;
        }
        if self
            .message_until
            .is_some_and(|deadline| Instant::now() >= deadline)
        {
            self.message.clear();
            self.message_until = None;
            changed = true;
        }
        let background_due = Instant::now() >= self.next_background_ui;
        if background_due && self.download_ui_dirty {
            if self.modal == "downloads" {
                if let Err(error) = self.refresh_download_rows() {
                    self.show_error(error);
                }
                changed = true;
            }
            self.download_ui_dirty = false;
        }
        if changed || background_due && self.background_ui_dirty {
            if let Err(error) = self.sync_view() {
                eprintln!("aidoku: update view: {error:#}");
            }
            self.platform
                .refresh(self.frame_refresh.take().unwrap_or(RefreshKind::Ui));
            self.background_ui_dirty = false;
            self.next_background_ui = Instant::now() + Duration::from_millis(300);
        }
        match self.refresh_visible_covers() {
            Ok(true) => {
                self.background_ui_dirty = true;
            }
            Ok(false) => {}
            Err(error) => eprintln!("aidoku: queue visible covers: {error:#}"),
        }
        if let Err(error) = self.poll_download_usage() {
            eprintln!("aidoku: queue offline storage accounting: {error:#}");
        }
        if let Err(error) = self.poll_storage_details() {
            eprintln!("aidoku: queue storage details accounting: {error:#}");
        }
    }

    fn fail_search_group(&mut self, context: &JobContext, error: &str) {
        if !self.job_context_is_current(context) {
            return;
        }
        if let Some(group) = self
            .search_results
            .iter_mut()
            .find(|group| group.id == context.source)
        {
            group.pending = false;
            group.error = Some(error.to_owned());
        }
    }

    fn retry_superseded_job(&mut self, context: &JobContext) {
        match &context.kind {
            JobKind::Reader if self.route == "reader" => self.reader_dirty = true,
            JobKind::Chapter if self.route == "reader" && self.job_context_is_current(context) => {
                if let Some(target) = self
                    .reader_target()
                    .or_else(|| context.reader_target.clone())
                {
                    if let Err(error) = self.load_reader_target(target.clone()) {
                        self.recover_reader_failure(target, error);
                    }
                }
            }
            JobKind::Prefetch => self.prefetch_dirty = true,
            JobKind::Cover(id) => {
                self.cover_pending.remove(id);
                self.cover_view.clear();
            }
            JobKind::Search => self.fail_search_group(context, &tr("request_superseded")),
            JobKind::ServerConnection => self.reconcile_server_connection_state(),
            _ if self.job_context_is_current(context) => self.notify(tr("request_superseded")),
            _ => {}
        }
    }

    pub(crate) fn source_images_changed(&mut self) {
        self.source_revision = self.source_revision.wrapping_add(1);
        self.source_configurations = None;
        self.source_configuration_revision
            .store(self.source_revision, Ordering::Release);
        self.cover_cache.clear();
        self.cover_paths.clear();
        self.cover_uris.clear();
        self.cover_pending.clear();
        self.cover_queue.clear();
        self.cover_view.clear();
        self.cover_retry_after.clear();
    }

    fn job_context_is_current(&self, context: &JobContext) -> bool {
        if self.suspended || context.lifecycle_generation != self.lifecycle_generation {
            return false;
        }
        match context.kind {
            JobKind::Reader | JobKind::Chapter => {
                context.reader_generation == self.reader_generation
            }
            JobKind::Search => {
                context.search_generation == self.search_generation
                    && context.query == self.query
                    && context.search_scope == self.search_scope
            }
            JobKind::Catalog => {
                context.catalog_generation == self.catalog_generation
                    && context.source == self.current_source
                    && context.catalog_filter == self.catalog_filter
            }
            JobKind::Cover(_) => context.source_revision == self.source_revision,
            _ => true,
        }
    }

    fn complete_job(&mut self, result: JobResult, context: &JobContext) -> Result<()> {
        if !self.job_context_is_current(context) {
            return Ok(());
        }
        match result {
            JobResult::Catalog {
                source,
                books,
                remote_page,
                has_next,
                listings,
            } => {
                if context.catalog_generation != self.catalog_generation
                    || context.source != self.current_source
                    || context.catalog_filter != self.catalog_filter
                    || source != self.current_source
                {
                    return Ok(());
                }
                self.record_server_http_success(&source, context.source_revision)?;
                for book in &books {
                    self.store.upsert_book(book)?;
                }
                if remote_page == 1 {
                    self.catalog = books.clone();
                } else {
                    let mut known: HashSet<_> =
                        self.catalog.iter().map(|book| book.id.clone()).collect();
                    self.catalog.extend(
                        books
                            .iter()
                            .filter(|book| known.insert(book.id.clone()))
                            .cloned(),
                    );
                }
                self.catalog_remote_page = remote_page;
                self.catalog_has_more = has_next;
                self.catalog_listings = listings;
                if !self.options.demo {
                    if let Some(mut config) = self.store.source(&source)? {
                        config
                            .options
                            .insert("_cached_listings".into(), json!(self.catalog_listings));
                        self.store.save_source(&config)?;
                    }
                }
                self.catalog_filter = self
                    .catalog_filter
                    .min(self.catalog_listings.len().saturating_sub(1) as i32);
                self.request_covers(&books)?;
            }
            JobResult::Detail { book, chapters } => {
                self.store.upsert_book(&book)?;
                self.store.save_chapters(&book.id, &chapters)?;
                self.invalidate_storage_details();
                self.request_covers(&[book])?;
            }
            JobResult::Pages {
                chapter_id,
                page,
                pages,
            } => {
                if context.reader_generation != self.reader_generation {
                    return Ok(());
                }
                let counted = self
                    .store
                    .chapter(&chapter_id)?
                    .is_some_and(|chapter| chapter.downloaded);
                self.store.save_pages(&chapter_id, &pages)?;
                if counted
                    || self
                        .store
                        .chapter(&chapter_id)?
                        .is_some_and(|chapter| chapter.downloaded)
                {
                    self.invalidate_download_usage();
                }
                let target = context
                    .reader_target
                    .clone()
                    .unwrap_or_else(|| ReaderTarget::new(&chapter_id, page, 0, 1.0));
                self.enter_reader_at(
                    &chapter_id,
                    target.page,
                    target.vertical_offset,
                    target.zoom,
                )?;
            }
            JobResult::ReaderImage {
                chapter_id,
                page,
                offset,
                first,
                second,
                scaled_height,
                viewport_height,
                preferences,
                first_path,
                second_path,
                paths,
            } => {
                let raw_paths = paths
                    .iter()
                    .map(|cached| cached.path.clone())
                    .collect::<Vec<_>>();
                let manifest_current = self.save_cached_paths(&chapter_id, paths)?;
                if context.reader_generation != self.reader_generation || self.route != "reader" {
                    return Ok(());
                }
                if !manifest_current {
                    self.reader_generation = self.reader_generation.wrapping_add(1);
                    self.reader_dirty = true;
                    self.start_reader_image_job()?;
                    return Ok(());
                }
                let mut rerender = false;
                let mut accepted = false;
                if let Some(reader) = &mut self.reader {
                    if reader.current_chapter().id == chapter_id
                        && reader.page == page
                        && reader.vertical_offset == offset
                    {
                        if preferences.reading_direction == "vertical" {
                            reader.set_vertical_dimensions(scaled_height, viewport_height);
                            rerender = reader.vertical_offset != offset;
                        }
                        accepted = !rerender;
                    }
                }
                if rerender {
                    // Resolving a previous page's bottom or a cropped image can
                    // change the requested offset. Publish only that resolved frame.
                    self.reader_generation = self.reader_generation.wrapping_add(1);
                    self.reader_dirty = true;
                    self.start_reader_image_job()?;
                } else if accepted {
                    if let Some(ui) = self.ui.upgrade() {
                        let mut leases = Vec::new();
                        for path in std::iter::once(&first_path)
                            .chain(second_path.iter())
                            .chain(raw_paths.iter())
                        {
                            if let Ok(lease) = self.disk_cache.lease_path(path) {
                                leases.push(lease);
                            }
                        }
                        self.displayed_image_leases = leases;
                        ui.global::<State>().set_reader_image(gray_image(first));
                        ui.global::<State>()
                            .set_reader_image_next(second.map(gray_image).unwrap_or_default());
                        if let Some(reader) = self.reader.as_ref() {
                            self.displayed_reader = Some(DisplayedReader {
                                session: ReaderSession {
                                    book_id: reader.current_chapter().book_id.clone(),
                                    chapter_id: reader.current_chapter().id.clone(),
                                    page: reader.page,
                                    total: reader.total,
                                    vertical_offset: reader.vertical_offset,
                                    zoom: reader.zoom,
                                    landscape: reader.landscape,
                                    spread: reader.spread,
                                    menu_open: false,
                                    scaled_height,
                                    viewport_height,
                                },
                                preferences,
                                first_path,
                                second_path,
                            });
                            self.resume_reader_repair = false;
                        }
                        if self.reader_recovery.is_some()
                            || self.modal_context == "reader-action-error"
                        {
                            self.clear_reader_recovery();
                            if let Some(reader) = &mut self.reader {
                                reader.menu_open = false;
                            }
                        }
                        self.committed_progress = if self.prefs.incognito {
                            None
                        } else {
                            self.reader
                                .as_ref()
                                .map(|reader| reader.progress(chrono::Utc::now().timestamp()))
                        };
                        self.committed_progress_dirty = self.committed_progress.is_some();
                        self.frame_refresh = Some(refresh_kind(context.reader_refresh));
                        self.reader_refresh = RefreshRequest::None;
                        self.save_reader_progress()?;
                        self.prefetch_dirty = true;
                        if let Err(error) = self.start_reader_prefetch() {
                            self.prefetch_dirty = false;
                            eprintln!("aidoku: read-ahead: {error:#}");
                        }
                    }
                }
            }
            JobResult::Prefetched { chapter_id, paths } => {
                self.save_cached_paths(&chapter_id, paths)?;
            }
            JobResult::Cover {
                id,
                uri,
                path,
                image,
            } => self.accept_cover(context, id, uri, path, image)?,
            JobResult::DownloadUsage(result) => {
                if self.accept_download_usage(result) && self.route == "settings" {
                    self.refresh_setting_details()?;
                }
            }
            JobResult::StorageDetails(result) => {
                if self.accept_storage_details(result) {
                    self.refresh_setting_details()?;
                }
            }
            JobResult::MigrationCandidates(result) => self.accept_migration_candidates(result)?,
            JobResult::MigrationPrepared(result) => self.accept_prepared_migration(result)?,
            JobResult::ServerConnected(result) => self.complete_server_connection(result)?,
            JobResult::Search(mut group) => {
                if context.search_generation != self.search_generation
                    || context.query != self.query
                    || context.search_scope != self.search_scope
                    || self.route != "search"
                {
                    return Ok(());
                }
                if group.error.is_none() && !group.pending && group.id == context.source {
                    self.record_server_http_success(&group.id, context.source_revision)?;
                }
                for book in &group.books {
                    self.store.upsert_book(book)?;
                }
                group.pending = false;
                self.request_covers(&group.books)?;
                if let Some(previous) = self
                    .search_results
                    .iter_mut()
                    .find(|previous| previous.id == group.id)
                {
                    *previous = group;
                } else {
                    self.search_results.push(group);
                }
            }
            JobResult::Imported(imports) => {
                let mut first = None;
                for imported in imports {
                    first.get_or_insert(imported.book.id.clone());
                    self.store.upsert_book(&imported.book)?;
                    self.store
                        .save_chapters(&imported.book.id, &imported.chapters)?;
                    for (id, pages) in imported.pages {
                        self.store.save_pages(&id, &pages)?;
                    }
                }
                self.invalidate_download_usage();
                self.current_book_id = first;
                self.route = "library".into();
                self.page = 1;
                self.modal.clear();
                self.notify(tr("saved"));
            }
            JobResult::Repository(repository) => {
                self.record_repository(&repository)?;
                self.available = repository.sources;
                self.route = "sources".into();
                self.source_filter = 1;
                self.page = 1;
                self.modal.clear();
            }
            JobResult::Installed(source) => {
                self.store.save_source(&source)?;
                self.source_images_changed();
                self.source_filter = 0;
                self.notify(tr("saved"));
            }
            JobResult::SourcesInstalled { installed, errors } => {
                let installed_count = installed.len();
                for source in installed {
                    self.store.save_source(&source)?;
                }
                if installed_count > 0 {
                    self.source_images_changed();
                }
                self.source_filter = 0;
                self.page = 1;
                if errors.is_empty() {
                    self.notify(tr("saved"));
                } else {
                    for error in &errors {
                        eprintln!("aidoku: source update: {error}");
                    }
                    self.notify(format!(
                        "{}: {}; {}: {}",
                        tr("saved"),
                        installed_count,
                        tr("error"),
                        errors.len()
                    ));
                }
            }
            JobResult::SourceOptionSaved(source) => {
                let reopen = source.id == self.current_source
                    && matches!(self.route.as_str(), "sources" | "catalog")
                    && (self.modal_context == "source-settings"
                        || self.modal_context.starts_with("sourcevalue:"));
                self.store.save_source(&source)?;
                self.source_images_changed();
                if reopen {
                    self.load_source_settings()?;
                }
            }
            JobResult::InteractionDone(message) => self.notify(message),
            JobResult::Settings(settings) => {
                if context.source == self.current_source
                    && matches!(self.route.as_str(), "sources" | "catalog")
                    && self.modal_context == "source-settings-loading"
                {
                    self.source_settings = settings;
                    self.open_source_settings()?;
                }
            }
            JobResult::Updated(updates) => {
                let checked = updates.len();
                for (book, chapters) in updates {
                    self.store.upsert_book(&book)?;
                    self.store.save_chapters(&book.id, &chapters)?;
                }
                self.invalidate_storage_details();
                self.notify(if checked > 0 {
                    tr("library_update_complete").replace("{count}", &checked.to_string())
                } else {
                    tr("library_update_no_targets")
                });
            }
            JobResult::CacheCleared => {
                crate::render_cache::clear_memory(&self.disk_cache);
                self.invalidate_storage_details();
                self.source_images_changed();
                self.modal.clear();
                self.notify(tr("saved"));
            }
        }
        Ok(())
    }

    pub(crate) fn effective_reader_preferences(&self) -> Preferences {
        let mut preferences = self.prefs.clone();
        if self.interactions.remember_book_direction {
            if let Some(direction) = self
                .current_book_id
                .as_ref()
                .and_then(|id| self.interactions.book_directions.get(id))
            {
                preferences.reading_direction = direction.clone();
            }
        }
        effective_preferences(
            &preferences,
            self.platform.profile().is_scribe(),
            &self.interactions.page_layout_mode,
        )
    }

    pub(crate) fn invalidate_reader_work(&mut self) {
        self.reader_generation = self.reader_generation.wrapping_add(1);
        self.reader_dirty = false;
        self.reader_refresh = RefreshRequest::None;
        self.frame_refresh = None;
        self.prefetch_dirty = false;
        if let Some(id) = self.reader_inflight {
            self.pending.remove(&id);
        }
        if let Some(id) = self.chapter_inflight.take() {
            self.pending.remove(&id);
        }
        self.busy = !self.pending.is_empty();
    }

    pub(crate) fn invalidate_search_work(&mut self) {
        self.search_generation = self.search_generation.wrapping_add(1);
    }

    pub(crate) fn invalidate_catalog_work(&mut self) {
        self.catalog_generation = self.catalog_generation.wrapping_add(1);
    }

    fn current_book(&self) -> Result<Book> {
        self.store
            .book(
                self.current_book_id
                    .as_deref()
                    .context("No manga is selected")?,
            )?
            .context("The selected manga is missing")
    }

    fn save_reader_progress(&mut self) -> Result<()> {
        if self.prefs.incognito || !self.committed_progress_dirty {
            return Ok(());
        }
        if let Some(progress) = &self.committed_progress {
            self.store.save_progress(progress)?;
        }
        self.committed_progress_dirty = false;
        Ok(())
    }

    fn apply_reader_update(&mut self, update: ReaderUpdate) -> Result<()> {
        use crate::reader::ReaderAction;
        if update.action == ReaderAction::MenuOpened {
            let _ = self.platform.status();
        }
        if matches!(
            update.action,
            ReaderAction::PageChanged | ReaderAction::ChapterChanged | ReaderAction::ZoomChanged
        ) {
            let target = self.reader_target().context("No reader")?;
            let result = (|| {
                if !self.store.has_pages(&target.chapter_id)? {
                    self.load_reader_target(target.clone())
                } else {
                    self.request_reader_images_with_refresh(update.refresh)
                }
            })();
            if let Err(error) = result {
                self.recover_reader_failure(target, error);
            }
            return Ok(());
        }
        if update.refresh != RefreshRequest::None {
            self.platform.refresh(refresh_kind(update.refresh));
        }
        Ok(())
    }

    pub(crate) fn notify(&mut self, text: String) {
        self.message = text;
        self.message_until = Some(Instant::now() + Duration::from_millis(2200));
    }
    fn show_error(&mut self, error: anyhow::Error) {
        eprintln!("aidoku: {error:#}");
        self.notify(format!("{}: {error}", tr("error")));
        self.busy = !self.pending.is_empty();
        if let Err(error) = self.sync_view() {
            eprintln!("aidoku: {error:#}");
        }
    }

    pub fn snapshot(&self) -> Value {
        let reader = self.reader.as_ref();
        let ui = self.ui.upgrade();
        let visible_setting_ids = ui
            .as_ref()
            .map(|ui| {
                let settings = ui.global::<State>().get_settings();
                (0..settings.row_count())
                    .filter_map(|row| settings.row_data(row))
                    .map(|item| item.id.to_string())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        let profile = self.platform.profile();
        let mut snapshot = json!({
            "route": self.route, "page": self.page, "page_count": self.ui.upgrade().map(|ui| ui.global::<State>().get_page_count()).unwrap_or(1),
            "current_book": self.current_book_id, "current_chapter": reader.map(|reader| &reader.current_chapter().id),
            "reader_page": reader.map(|reader| reader.page), "reader_total": reader.map(|reader| reader.total), "menu": reader.map(|reader| reader.menu_open),
            "reader_offset": reader.map(|reader| reader.vertical_offset), "reader_zoom": reader.map(|reader| reader.zoom),
            "reader_generation": self.reader_generation, "reader_inflight": self.reader_inflight.is_some(), "reader_dirty": self.reader_dirty,
            "committed_reader_page": self.committed_progress.as_ref().map(|progress| progress.page),
            "reader_bookmarked": ui.as_ref().is_some_and(|ui| ui.global::<State>().get_reader_bookmarked()),
            "library_count": self.store.books().unwrap_or_default().iter().filter(|book| book.in_library).count(),
            "history_count": self.store.history().unwrap_or_default().len(), "bookmark_count": self.current_book_id.as_ref().map(|id| self.store.bookmarks(id).unwrap_or_default().len()).unwrap_or(0),
            "preferences": self.prefs, "downloads": self.store.downloads().unwrap_or_default(), "modal": self.modal,
            "query": self.query, "search_scope": self.search_scope, "busy": self.busy, "message": self.message,
            "catalog_count": self.catalog.len(), "catalog_has_more": self.catalog_has_more, "catalog_remote_page": self.catalog_remote_page,
            "search_result_count": self.search_results.iter().map(|group| group.books.len()).sum::<usize>(), "search_group_count": self.search_results.len(),
            "visible_setting_ids": visible_setting_ids, "has_frontlight": profile.has_frontlight(), "has_warmth": profile.has_warmth(), "has_orientation": profile.has_orientation(),
            "demo": self.options.demo, "offline": self.options.offline, "database": self.data_dir.join("library.sqlite3"),
            "selected": self.selected, "source_id": self.current_source, "cover_count": self.cover_paths.len(),
            "selected_chapters": self.selected_chapters,
        });
        snapshot["cache"] = json!(self.disk_cache.stats());
        snapshot["render_cache"] = json!(crate::render_cache::stats());
        snapshot["source_manager_builds"] = json!(self.source_manager_pool.build_count());
        snapshot["foreground_ready_posts"] =
            json!(self.foreground_ready_posts.load(Ordering::Relaxed));
        snapshot["cached_download_bytes"] = json!(self.cached_download_bytes);
        snapshot["visible_reader_page"] =
            json!(ui.as_ref().map(|ui| ui.global::<State>().get_reader_page()));
        snapshot["visible_reader_total"] = json!(
            ui.as_ref()
                .map(|ui| ui.global::<State>().get_reader_total())
        );
        snapshot["visible_reader_chapter_id"] = json!(
            ui.as_ref()
                .map(|ui| ui.global::<State>().get_current_chapter_id().to_string())
        );
        snapshot["displayed_reader"] = json!(self.displayed_reader);
        snapshot["reader_recovery"] = json!(self.reader_recovery);
        snapshot["reader_effective"] = json!(self.effective_reader_preferences());
        snapshot["suspended"] = json!(self.suspended);
        snapshot["lifecycle"] = self.lifecycle_snapshot();
        snapshot["panel_page"] = json!(self.panel_page + 1);
        snapshot["panel_page_count"] = json!(
            ui.as_ref()
                .map(|ui| ui.global::<State>().get_panel_page_count())
                .unwrap_or(1)
        );
        snapshot["modal_context"] = json!(self.modal_context);
        snapshot["summary_is_book"] = json!(
            ui.as_ref()
                .is_some_and(|ui| ui.global::<State>().get_summary_is_book())
        );
        snapshot["book_tags"] = json!(
            ui.as_ref()
                .map(|ui| ui.global::<State>().get_book_tags().to_string())
                .unwrap_or_default()
        );
        snapshot["modal_items"] = json!(ui.as_ref().map(|ui| {
            let rows = ui.global::<State>().get_modal_items();
            (0..rows.row_count()).filter_map(|row| rows.row_data(row)).map(|row| json!({"id":row.id.to_string(), "enabled":row.enabled, "title":row.title.to_string(), "value":row.value.to_string()})).collect::<Vec<_>>()
        }).unwrap_or_default());
        snapshot["catalog_tabs"] = json!(self.catalog_listings);
        snapshot["modal_item_ids"] = json!(
            ui.as_ref()
                .map(|ui| {
                    let rows = ui.global::<State>().get_modal_items();
                    (0..rows.row_count())
                        .filter_map(|row| rows.row_data(row))
                        .map(|row| row.id.to_string())
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default()
        );
        self.extend_diagnostics_snapshot(&mut snapshot);
        snapshot
    }

    pub fn shutdown(&mut self) {
        if self.shutting_down {
            return;
        }
        self.shutting_down = true;
        self.sync_storage_details_visibility();
        let _ = self.save_reader_progress();
        let _ = self.checkpoint_navigation(true);
        let _ = self.store.save_preferences(&self.prefs);
        self.downloads.shutdown();
        self.pairing = None;
    }

    pub fn finish_shutdown(&self) -> bool {
        self.downloads.wait_stopped(Duration::from_secs(2))
    }
}

impl Drop for App {
    fn drop(&mut self) {
        self.shutdown();
    }
}

pub(crate) fn default_sources() -> Vec<SourceConfig> {
    [
        ("local", "local", tr("local_files")),
        ("komga", "komga", "Komga".into()),
        ("kavita", "kavita", "Kavita".into()),
        ("suwayomi", "suwayomi", "Suwayomi".into()),
        ("mangadex", "mangadex", "MangaDex".into()),
    ]
    .into_iter()
    .map(|(id, kind, name)| SourceConfig {
        id: id.into(),
        name,
        kind: kind.into(),
        enabled: true,
        ..SourceConfig::default()
    })
    .collect()
}

pub(crate) fn normalize_route(route: &str) -> &str {
    match route {
        "browse" | "sources" => "sources",
        "catalog" | "search" | "history" | "settings" | "detail" | "reader" => route,
        "downloads" => "settings",
        _ => "library",
    }
}
pub(crate) fn demo_query() -> String {
    crate::demo::primary_id();
    tr("demo_search_query")
}

pub(crate) fn gray_image(image: impl std::borrow::Borrow<GrayImage>) -> Image {
    let image = image.borrow();
    let mut pixels =
        slint::SharedPixelBuffer::<slint::Rgb8Pixel>::new(image.width(), image.height());
    for (rgb, gray) in pixels
        .make_mut_bytes()
        .chunks_exact_mut(3)
        .zip(image.as_raw())
    {
        rgb.fill(*gray);
    }
    Image::from_rgb8(pixels)
}

pub(crate) fn model<T: Clone + 'static>(values: impl IntoIterator<Item = T>) -> slint::ModelRc<T> {
    Rc::new(slint::VecModel::from(
        values.into_iter().collect::<Vec<_>>(),
    ))
    .into()
}

fn cover_alias(id: &str, uri: &str) -> String {
    let mut hash = Sha256::new();
    hash.update((id.len() as u64).to_le_bytes());
    hash.update(id.as_bytes());
    hash.update(uri.as_bytes());
    format!("cover:{:x}", hash.finalize())
}

fn effective_preferences(preferences: &Preferences, scribe: bool, layout: &str) -> Preferences {
    let mut effective = preferences.clone();
    effective.spread = !matches!(
        effective.reading_direction.as_str(),
        "vertical" | "vertical-pages"
    ) && match layout {
        "single" => false,
        "double" => true,
        _ => scribe && effective.landscape && effective.spread,
    };
    effective
}

fn refresh_kind(refresh: RefreshRequest) -> RefreshKind {
    match refresh {
        RefreshRequest::Fast => RefreshKind::Fast,
        RefreshRequest::Partial => RefreshKind::Partial,
        RefreshRequest::Full => RefreshKind::Full,
        _ => RefreshKind::Ui,
    }
}

fn merge_refresh(first: RefreshRequest, second: RefreshRequest) -> RefreshRequest {
    let rank = |refresh| match refresh {
        RefreshRequest::None => 0,
        RefreshRequest::Fast => 1,
        RefreshRequest::Ui => 2,
        RefreshRequest::Partial => 3,
        RefreshRequest::Full => 4,
    };
    if rank(first) >= rank(second) {
        first
    } else {
        second
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::platform::DeviceProfile;

    pub(super) struct DemoRouteFixture {
        pub(super) app: App,
        _directory: tempfile::TempDir,
    }

    impl DemoRouteFixture {
        pub(super) fn new(route: &str, profile: DeviceProfile, demo: bool) -> Self {
            let directory = tempfile::tempdir().unwrap();
            let data_dir = directory.path().join(if demo { "demo" } else { "user" });
            fs::create_dir_all(&data_dir).unwrap();
            let database = data_dir.join("library.sqlite3");
            let store = Store::open(&database).unwrap();
            let disk_cache = crate::cache::CacheManager::open(&data_dir).unwrap();
            let network_control = crate::sources::NetworkControl::open(&data_dir).unwrap();
            if demo {
                crate::demo::seed_for_screen(&store, &data_dir, normalize_route(route)).unwrap();
            } else {
                for source in default_sources() {
                    store.save_source(&source).unwrap();
                }
            }
            let prefs = store.preferences().unwrap();
            let interactions = InteractionState::load(&store).unwrap();
            let downloads = DownloadWorker::start_initialized(
                data_dir.clone(),
                database,
                crate::downloads::DownloadOptions {
                    offline: true,
                    ..crate::downloads::DownloadOptions::default()
                },
                false,
            )
            .unwrap();
            let (job_tx, job_rx) = mpsc::channel();
            // These route/controller checks never construct a Slint window or
            // install a process-global platform. Actual window startup remains
            // part of the frozen-application remote capture checks.
            let mut app = App {
                ui: slint::Weak::default(),
                options: AppOptions {
                    data_dir: directory.path().to_path_buf(),
                    demo,
                    offline: true,
                    initial_screen: route.into(),
                    landscape: false,
                    resume: false,
                },
                platform: Rc::new(Platform::new(profile, false).unwrap()),
                data_dir,
                store,
                disk_cache,
                network_control,
                prefs,
                interactions,
                migration: migration::MigrationState::default(),
                server_configuration: server_configuration::ServerState::default(),
                route: normalize_route(route).into(),
                return_route: "library".into(),
                page: 1,
                library_filter: 0,
                source_filter: 0,
                catalog_filter: 0,
                history_filter: 0,
                sort_title: false,
                unread_only: false,
                downloaded_only: false,
                catalog_unread_only: false,
                catalog_downloaded_only: false,
                selection_mode: false,
                selected: HashSet::new(),
                selected_chapters: HashSet::new(),
                current_book_id: demo.then(crate::demo::primary_id),
                current_source: "mangadex".into(),
                catalog: Vec::new(),
                catalog_remote_page: 1,
                catalog_has_more: false,
                catalog_query: None,
                catalog_listings: vec![
                    ("popular".into(), tr("popular")),
                    ("latest".into(), tr("recently_updated")),
                    ("new".into(), tr("newly_added")),
                ],
                available: if demo {
                    crate::demo::available_sources()
                } else {
                    Vec::new()
                },
                source_settings: Vec::new(),
                query: String::new(),
                search_scope: "all".into(),
                search_results: Vec::new(),
                reader: None,
                displayed_reader: None,
                reader_recovery: None,
                displayed_image_leases: Vec::new(),
                resume_reader_repair: false,
                resume_frame_pending: false,
                reader_image_jobs_started: 0,
                suspended: false,
                suspend_checkpoint_ready: false,
                cover_cache: HashMap::new(),
                cover_paths: HashMap::new(),
                cover_uris: HashMap::new(),
                modal: String::new(),
                modal_context: String::new(),
                keyboard: KeyboardInput::default(),
                keyboard_target: String::new(),
                panel_pages: Vec::new(),
                panel_page: 0,
                choice_rows: Vec::new(),
                choice_total: 0,
                choice_disabled: HashSet::new(),
                busy: false,
                message: String::new(),
                message_until: None,
                pairing: None,
                downloads,
                book_removal_completions: 0,
                last_book_removal: None,
                job_tx,
                job_rx,
                next_job: 1,
                pending: HashSet::new(),
                cover_pending: HashSet::new(),
                cover_queue: VecDeque::new(),
                cover_inflight: None,
                cover_view: Vec::new(),
                cover_retry_after: HashMap::new(),
                source_revision: 0,
                source_configuration_revision: Arc::new(AtomicU64::new(0)),
                source_configurations: None,
                source_manager_pool: Arc::new(source_jobs::SourceManagerPool::default()),
                foreground_ready_pending: Arc::new(AtomicBool::new(false)),
                foreground_ready_posts: Arc::new(AtomicU64::new(0)),
                cached_download_bytes: None,
                download_usage: download_usage::UsageTracker::default(),
                storage_details: storage_details::StorageDetailsTracker::default(),
                background_ui_dirty: false,
                download_ui_dirty: false,
                next_background_ui: Instant::now(),
                lifecycle_generation: 0,
                resume_network_pending: false,
                network_probe_inflight: false,
                network_probe_rx: None,
                next_network_probe: Instant::now(),
                last_session_save: Instant::now(),
                committed_progress: None,
                committed_progress_dirty: false,
                reader_generation: 0,
                chapter_inflight: None,
                reader_inflight: None,
                reader_dirty: false,
                reader_refresh: RefreshRequest::None,
                frame_refresh: None,
                prefetch_inflight: None,
                prefetch_dirty: false,
                search_generation: 0,
                catalog_generation: 0,
                shutting_down: false,
            };
            app.prepare_demo_catalog_route().unwrap();
            Self {
                app,
                _directory: directory,
            }
        }

        fn generated_books(&self) -> usize {
            self.app
                .store
                .test_connection()
                .query_row(
                    "SELECT COUNT(*) FROM books WHERE remote_id LIKE 'catalog-%'",
                    [],
                    |row| row.get(0),
                )
                .unwrap()
        }

        fn retained_state(&self) -> (Value, Vec<u8>) {
            (
                json!({
                    "history": self.app.store.history().unwrap(),
                    "bookmarks": self.app.store.bookmarks(&crate::demo::primary_id()).unwrap(),
                }),
                fs::read(self.app.data_dir.join(".demo-v1-seeded")).unwrap(),
            )
        }

        fn assert_persisted_catalog(&self, generated: usize, volumes: usize) {
            assert_eq!(self.app.catalog.len(), 128);
            assert_eq!(self.generated_books(), generated);
            let connection = self.app.store.test_connection();
            let actual_volumes: usize = connection
                .query_row(
                    "SELECT COUNT(*) FROM books WHERE remote_id LIKE 'catalog-volume-%'",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(actual_volumes, volumes);
            let actual_pages: usize = connection
                .query_row(
                    "SELECT COUNT(*) FROM pages p JOIN chapters c ON c.id=p.chapter_id
                     JOIN books b ON b.id=c.book_id WHERE b.remote_id LIKE 'catalog-%'",
                    [],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(actual_pages, generated * 40);
            for book in &self.app.catalog {
                assert!(self.app.store.book(&book.id).unwrap().is_some());
            }
            let journal: String = connection
                .pragma_query_value(None, "journal_mode", |row| row.get(0))
                .unwrap();
            let synchronous: u32 = connection
                .pragma_query_value(None, "synchronous", |row| row.get(0))
                .unwrap();
            assert_eq!(journal, "delete");
            assert_eq!(synchronous, 2);
        }
    }

    impl Drop for DemoRouteFixture {
        fn drop(&mut self) {
            self.app.shutdown();
            let _ = self.app.finish_shutdown();
        }
    }

    #[test]
    fn demo_reader_and_management_routes_do_not_create_hidden_catalog_records() {
        for (profile, initial) in [
            (DeviceProfile::Pw5, "reader"),
            (DeviceProfile::Scribe, "sources"),
        ] {
            let mut fixture = DemoRouteFixture::new(initial, profile, true);
            assert_eq!(fixture.app.store.books().unwrap().len(), 46);
            for route in [
                "reader", "library", "sources", "history", "settings", "detail",
            ] {
                fixture.app.route = route.into();
                fixture.app.prepare_demo_catalog_route().unwrap();
                assert!(
                    fixture.app.catalog.is_empty(),
                    "Unexpected catalog on {route}"
                );
                assert_eq!(fixture.generated_books(), 0, "Unexpected rows on {route}");
            }
            assert_eq!(fixture.app.store.downloads().unwrap().len(), 9);
        }
    }

    #[test]
    fn demo_offline_browse_creates_the_real_catalog_only_after_its_route_is_opened() {
        for profile in [DeviceProfile::Pw5, DeviceProfile::Scribe] {
            let mut fixture = DemoRouteFixture::new("library", profile, true);
            let before = fixture.retained_state();
            assert_eq!(fixture.generated_books(), 0);
            fixture.app.dispatch("navigation", "open", "1").unwrap();
            if !profile.is_scribe() {
                assert_eq!(fixture.app.route, "sources");
                assert!(fixture.app.catalog.is_empty());
                assert_eq!(fixture.generated_books(), 0);
                fixture.app.dispatch("sources", "open", "mangadex").unwrap();
            }
            assert_eq!(fixture.app.route, "catalog");
            fixture.assert_persisted_catalog(
                if profile.is_scribe() { 125 } else { 126 },
                if profile.is_scribe() { 118 } else { 123 },
            );
            assert_eq!(fixture.retained_state(), before);
            let ids = fixture
                .app
                .catalog
                .iter()
                .map(|book| book.id.clone())
                .collect::<Vec<_>>();
            fixture.app.prepare_demo_catalog_route().unwrap();
            assert_eq!(
                fixture
                    .app
                    .catalog
                    .iter()
                    .map(|book| book.id.clone())
                    .collect::<Vec<_>>(),
                ids
            );
        }
    }

    #[test]
    fn demo_source_management_and_detail_back_prepare_an_unloaded_catalog() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Scribe, true);
        fixture
            .app
            .dispatch("settings", "manage-sources", "")
            .unwrap();
        assert_eq!(fixture.app.route, "sources");
        assert_eq!(fixture.generated_books(), 0);
        fixture.app.dispatch("sources", "back", "").unwrap();
        assert_eq!(fixture.app.route, "catalog");
        fixture.assert_persisted_catalog(125, 118);

        fixture.app.route = "detail".into();
        fixture.app.return_route = "catalog".into();
        fixture.app.catalog.clear();
        fixture.app.dispatch("detail", "back", "").unwrap();
        assert_eq!(fixture.app.route, "catalog");
        fixture.assert_persisted_catalog(125, 118);
    }

    #[test]
    fn demo_direct_catalog_source_picker_and_tabs_keep_the_persisted_catalog() {
        let mut fixture = DemoRouteFixture::new("catalog", DeviceProfile::Scribe, true);
        fixture.assert_persisted_catalog(125, 118);
        fixture.app.dispatch("sources", "picker", "").unwrap();
        fixture
            .app
            .dispatch("panel", "choose", "source:komga")
            .unwrap();
        assert_eq!(fixture.app.current_source, "komga");
        fixture.assert_persisted_catalog(125, 118);
        fixture.app.dispatch("catalog", "filter", "2").unwrap();
        assert_eq!(fixture.app.catalog_filter, 2);
        fixture.assert_persisted_catalog(125, 118);
        fixture
            .app
            .dispatch("panel", "choose", "source:demo-b")
            .unwrap();
        assert_eq!(fixture.app.current_source, "demo-b");
        assert!(fixture.app.catalog.is_empty());
        assert_eq!(fixture.generated_books(), 125);
    }

    #[test]
    fn demo_disabled_sources_and_real_search_results_are_not_replaced_by_a_catalog() {
        let mut fixture = DemoRouteFixture::new("search", DeviceProfile::Scribe, true);
        fixture.app.dispatch("sources", "open", "demo-b").unwrap();
        assert_eq!(fixture.app.current_source, "demo-b");
        assert!(fixture.app.catalog.is_empty());
        assert_eq!(fixture.generated_books(), 0);

        fixture.app.route = "search".into();
        fixture.app.current_source = "mangadex".into();
        fixture.app.query = demo_query();
        fixture.app.run_search().unwrap();
        let partial = fixture
            .app
            .search_results
            .iter()
            .find(|group| !group.books.is_empty() && group.books.len() < 128)
            .unwrap();
        let scope = partial.id.clone();
        let expected = partial
            .books
            .iter()
            .map(|book| book.id.clone())
            .collect::<Vec<_>>();
        fixture.app.dispatch("search", "all", &scope).unwrap();
        assert_eq!(fixture.app.route, "catalog");
        assert!(fixture.app.catalog_query.is_some());
        assert_eq!(
            fixture
                .app
                .catalog
                .iter()
                .map(|book| book.id.clone())
                .collect::<Vec<_>>(),
            expected
        );
        assert_eq!(fixture.generated_books(), 0);

        let empty = fixture
            .app
            .search_results
            .iter()
            .find(|group| group.books.is_empty() && group.error.is_none())
            .unwrap()
            .id
            .clone();
        fixture.app.dispatch("search", "all", &empty).unwrap();
        assert_eq!(fixture.app.route, "catalog");
        assert!(fixture.app.catalog_query.is_some());
        assert!(fixture.app.catalog.is_empty());
        assert_eq!(fixture.generated_books(), 0);
        assert!(fixture.app.store.source(&empty).unwrap().unwrap().enabled);
        let query = fixture.app.catalog_query.clone();
        fixture.app.dispatch("navigation", "open", "1").unwrap();
        assert_eq!(fixture.app.route, "catalog");
        assert_eq!(fixture.app.catalog_query, query);
        assert!(fixture.app.catalog.is_empty());
        assert_eq!(fixture.generated_books(), 0);
        fixture.app.dispatch("sources", "manage", "").unwrap();
        fixture.app.dispatch("sources", "back", "").unwrap();
        assert_eq!(fixture.app.route, "catalog");
        assert_eq!(fixture.app.catalog_query, query);
        assert!(fixture.app.catalog.is_empty());
        assert_eq!(fixture.generated_books(), 0);
    }

    #[test]
    fn demo_restored_catalog_routes_preserve_empty_searches_and_existing_results() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Scribe, true);
        fixture
            .app
            .store
            .save_session(&NavigationSession {
                route: "catalog".into(),
                ..NavigationSession::default()
            })
            .unwrap();
        fixture.app.restore_saved_session().unwrap();
        fixture.app.prepare_demo_catalog_route().unwrap();
        fixture.assert_persisted_catalog(125, 118);
        let retained = fixture
            .app
            .catalog
            .iter()
            .take(3)
            .map(|book| book.id.clone())
            .collect::<Vec<_>>();
        for ids in [retained.clone(), Vec::new()] {
            fixture
                .app
                .store
                .save_session(&NavigationSession {
                    route: "catalog".into(),
                    catalog_query: Some("Saved real search".into()),
                    catalog_book_ids: ids.clone(),
                    ..NavigationSession::default()
                })
                .unwrap();
            fixture.app.restore_saved_session().unwrap();
            fixture.app.prepare_demo_catalog_route().unwrap();
            assert_eq!(fixture.app.route, "catalog");
            assert_eq!(
                fixture.app.catalog_query.as_deref(),
                Some("Saved real search")
            );
            assert_eq!(
                fixture
                    .app
                    .catalog
                    .iter()
                    .map(|book| book.id.clone())
                    .collect::<Vec<_>>(),
                ids
            );
            assert_eq!(fixture.generated_books(), 125);
            assert!(
                fixture
                    .app
                    .store
                    .source(&fixture.app.current_source)
                    .unwrap()
                    .unwrap()
                    .enabled
            );
            fixture.app.dispatch("navigation", "open", "1").unwrap();
            assert_eq!(fixture.app.route, "catalog");
            assert_eq!(
                fixture.app.catalog_query.as_deref(),
                Some("Saved real search")
            );
            assert_eq!(
                fixture
                    .app
                    .catalog
                    .iter()
                    .map(|book| book.id.clone())
                    .collect::<Vec<_>>(),
                ids
            );
            assert_eq!(fixture.generated_books(), 125);
        }
    }

    #[test]
    fn non_demo_catalog_route_preparation_does_not_create_demo_records() {
        let mut fixture = DemoRouteFixture::new("catalog", DeviceProfile::Scribe, false);
        fixture.app.prepare_demo_catalog_route().unwrap();
        assert!(fixture.app.catalog.is_empty());
        assert!(fixture.app.store.books().unwrap().is_empty());
        assert_eq!(fixture.generated_books(), 0);
    }

    #[test]
    fn coalescing_page_turns_preserves_the_required_full_refresh() {
        let requests = [
            RefreshRequest::Partial,
            RefreshRequest::Full,
            RefreshRequest::Partial,
        ];
        let result = requests
            .into_iter()
            .fold(RefreshRequest::None, merge_refresh);
        assert_eq!(result, RefreshRequest::Full);
    }

    #[test]
    fn automatic_paperwhite_layout_keeps_single_page_stepping() {
        let preferences = Preferences {
            landscape: true,
            spread: true,
            ..Preferences::default()
        };
        assert!(!effective_preferences(&preferences, false, "automatic").spread);
        assert!(effective_preferences(&preferences, true, "automatic").spread);
        assert!(effective_preferences(&preferences, false, "double").spread);
        assert!(!effective_preferences(&preferences, true, "single").spread);
    }

    #[test]
    fn vertical_reading_temporarily_disables_spreads() {
        let preferences = Preferences {
            landscape: true,
            spread: true,
            reading_direction: "vertical".into(),
            ..Preferences::default()
        };
        assert!(!effective_preferences(&preferences, true, "automatic").spread);
        assert!(!effective_preferences(&preferences, false, "double").spread);
        let whole_page = Preferences {
            reading_direction: "vertical-pages".into(),
            ..preferences.clone()
        };
        assert!(!effective_preferences(&whole_page, true, "double").spread);
        assert!(preferences.spread);
    }
}
