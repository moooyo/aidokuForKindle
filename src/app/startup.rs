//! Background-owned metadata preparation and yielding UI attachment.
//!
//! The preparation/attachment boundary follows the sibling BiliComics startup
//! implementation. No Slint object or Platform is sent to a worker.

use super::*;
use crate::platform::DeviceProfile;

pub struct PreparedApp {
    pub(super) options: AppOptions,
    pub(super) data_dir: PathBuf,
    pub(super) store: Store,
    pub(super) disk_cache: crate::cache::CacheManager,
    pub(super) network_control: crate::sources::NetworkControl,
    pub(super) prefs: Preferences,
    pub(super) current_book_id: Option<String>,
    pub(super) interactions: InteractionState,
    pub(super) data_management: data_management::DataManagementState,
    pub(super) downloads: DownloadWorker,
}

pub struct StartupCoverWarm {
    cache: crate::cache::CacheManager,
    paths: Vec<PathBuf>,
}

impl StartupCoverWarm {
    fn select(
        store: &Store,
        cache: &crate::cache::CacheManager,
        current: Option<&str>,
        interactions: &InteractionState,
        limit: u8,
        allowed: &impl Fn() -> bool,
    ) -> Option<Self> {
        let current = current?;
        let limit = usize::from(limit.min(2));
        if limit == 0 || !allowed() || interactions.library_filters != [0; 4] {
            return None;
        }
        // The required continuation read already owns this short read snapshot.
        // Capture at most two paths before the downloads writer can contend;
        // never retain the connection, a transaction, or a full Book for prewarm.
        let mut books = Vec::new();
        match store.book(current) {
            Ok(Some(book)) if book.in_library => books.push(book),
            Ok(_) => (),
            Err(_) => {
                crate::reader_timing::mark("startup_cover_warm_selection_failed");
                return None;
            }
        }
        if allowed() && books.len() < limit {
            let extra = (|| -> Result<Vec<Book>> {
                if !store.small_library_for_startup_warm()? {
                    return Ok(Vec::new());
                }
                Ok(store
                    .library_view_page(
                        crate::store::view_queries::LibraryQuery {
                            sort_key: Some(interactions.library_sort.id()),
                            ascending: interactions.library_ascending,
                            ..Default::default()
                        },
                        1,
                        limit,
                    )?
                    .items)
            })();
            match extra {
                Ok(rows) => {
                    for book in rows {
                        if !books.iter().any(|other| other.id == book.id) {
                            books.push(book);
                        }
                    }
                }
                Err(_) => crate::reader_timing::mark("startup_cover_warm_selection_failed"),
            }
        }
        let paths = books
            .into_iter()
            .take(limit)
            .filter(|b| b.source_id == "local")
            .filter_map(|book| {
                cover_jobs::local_cover_path(&book.cover, true)
                    .ok()
                    .flatten()
            })
            .filter(|path| path.is_absolute())
            .collect::<Vec<_>>();
        if paths.is_empty() {
            crate::reader_timing::mark("startup_cover_warm_no_selection");
            return None;
        }
        Some(Self {
            cache: cache.clone(),
            paths,
        })
    }

    pub fn run(self, allowed: impl Fn() -> bool) {
        crate::reader_timing::mark("startup_cover_warm_plan");
        let options = cover_jobs::cover_options(376, 514);
        for path in self.paths {
            if !allowed() {
                crate::reader_timing::mark("startup_cover_warm_stopped");
                break;
            }
            crate::reader_timing::mark("startup_cover_warm_attempt");
            crate::reader_timing::mark(
                match crate::render_cache::warm_cached_local_cover(
                    &self.cache,
                    path,
                    &options,
                    &allowed,
                ) {
                    Ok(true) => "startup_cover_warm_hit",
                    Ok(false) => "startup_cover_warm_image_unavailable",
                    Err(_) => "startup_cover_warm_image_failed",
                },
            );
        }
    }
}

impl PreparedApp {
    /// Cancel an unattached controller without blocking the startup scene.
    pub fn shutdown(&self) {
        self.downloads.shutdown();
    }

    /// Drain it only after the event loop, or on its preparation worker.
    pub fn finish_shutdown(&self, timeout: Duration) -> bool {
        self.downloads.wait_stopped(timeout)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) enum AttachStage {
    Route,
    Catalog,
    View,
    Settings,
    Covers,
    Ready,
}

impl App {
    /// Prepare single-owner metadata and dormant services after backend setup.
    /// The SQLite connection is moved to the UI owner once, never shared.
    pub fn prepare(options: AppOptions, profile: DeviceProfile) -> Result<PreparedApp> {
        Self::prepare_with_cover_hints(options, profile, 0, || false).map(|(app, _)| app)
    }

    pub fn prepare_with_cover_hints(
        options: AppOptions,
        profile: DeviceProfile,
        warm_limit: u8,
        allowed: impl Fn() -> bool,
    ) -> Result<(PreparedApp, Option<StartupCoverWarm>)> {
        let data_dir = if options.demo {
            options.data_dir.join("demo")
        } else {
            options.data_dir.clone()
        };
        fs::create_dir_all(&data_dir).context("Create the application data directory")?;
        let database = data_dir.join("library.sqlite3");
        let store = Store::open(&database)?;
        let disk_cache = crate::cache::CacheManager::open(&data_dir)?;
        crate::resident::prepare(&options);
        let network_control = crate::sources::NetworkControl::open(&data_dir)?;
        // Preparation must also be safe when powerd parks an unfinished boot.
        // The UI opens this gate only while it owns an active display.
        network_control.suspend();
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
        if !profile.has_frontlight() {
            prefs.brightness = 0;
            prefs.warmth = 0;
        }
        let metadata = |with_hints: bool| -> Result<_> {
            let current = if options.demo {
                Some(crate::demo::primary_id())
            } else {
                store.library_continuation(None)?
            };
            let interactions = InteractionState::load(&store)?;
            let warm = if with_hints {
                StartupCoverWarm::select(
                    &store,
                    &disk_cache,
                    current.as_deref(),
                    &interactions,
                    warm_limit,
                    &allowed,
                )
            } else {
                None
            };
            Ok((current, interactions, warm))
        };
        let (current_book_id, interactions, warm) = if warm_limit != 0
            && !options.demo
            && !options.resume
            && options.initial_screen == "library"
            && allowed()
        {
            store.with_startup_read_snapshot(metadata)?
        } else {
            metadata(false)?
        };
        let data_management =
            data_management::DataManagementState::open(&data_dir, prefs.write_logs)?;
        let downloads = DownloadWorker::start_initialized(
            data_dir.clone(),
            database,
            crate::downloads::DownloadOptions {
                offline: options.offline || options.demo,
                ..crate::downloads::DownloadOptions::default()
            },
            true,
        )?;
        crate::reader_timing::mark("application_prepared");
        Ok((
            PreparedApp {
                options,
                data_dir,
                store,
                disk_cache,
                network_control,
                prefs,
                current_book_id,
                interactions,
                data_management,
                downloads,
            },
            warm,
        ))
    }

    pub fn is_starting(&self) -> bool {
        self.startup_stage.is_some()
    }

    /// Coalesce cheap stages, yielding after a slow stage or blocked lifecycle.
    /// The budget is checked between stages; an individual stage is not preempted.
    pub fn advance_startup(&mut self) -> Result<bool> {
        let started = Instant::now();
        loop {
            let previous = self.startup_stage;
            if self.advance_startup_stage()? {
                return Ok(true);
            }
            if self.startup_stage == previous || started.elapsed() >= Duration::from_millis(4) {
                return Ok(false);
            }
        }
    }

    fn advance_startup_stage(&mut self) -> Result<bool> {
        let Some(stage) = self.startup_stage else {
            return Ok(true);
        };
        if self.suspended || self.shutting_down || self.resume_frame_pending {
            return Ok(false);
        }
        match stage {
            AttachStage::Route => {
                crate::reader_timing::mark("startup_route_started");
                if !self.options.resume {
                    self.network_control.resume();
                }
                if self.options.demo && self.route == "search" {
                    self.query = demo_query();
                    self.run_search()?;
                }
                if !self.options.resume && self.route == "reader" {
                    self.route = "detail".into();
                    self.resume_reader()?;
                }
                if !self.options.resume && self.options.initial_screen == "downloads" {
                    self.route = "settings".into();
                    self.open_downloads()?;
                }
                if self.options.resume {
                    self.restore_saved_session()?;
                }
                self.startup_stage = Some(AttachStage::Catalog);
                crate::reader_timing::mark("startup_route_attached");
            }
            AttachStage::Catalog => {
                self.prepare_demo_catalog_route()?;
                self.startup_stage = Some(AttachStage::View);
                crate::reader_timing::mark("startup_catalog_attached");
            }
            AttachStage::View => {
                crate::reader_timing::mark("startup_view_started");
                self.sync_view()?;
                self.startup_stage = Some(AttachStage::Settings);
                crate::reader_timing::mark("startup_view_attached");
            }
            AttachStage::Settings => {
                if self.route == "settings" {
                    self.refresh_setting_details()?;
                }
                self.startup_stage = Some(AttachStage::Covers);
            }
            AttachStage::Covers => {
                if self.refresh_visible_covers()? {
                    self.sync_view()?;
                }
                self.startup_stage = Some(AttachStage::Ready);
            }
            AttachStage::Ready => {
                if !self.options.resume {
                    self.downloads.resume_owned(true)?;
                }
                self.startup_stage = None;
                return Ok(true);
            }
        }
        Ok(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn warm_paths_are_bounded_and_selected_on_the_primary_snapshot() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let database = directory.path().join("library.sqlite3");
        let store = Store::open(&database)?;
        let cache = crate::cache::CacheManager::open(directory.path())?;
        for (i, id) in ["one", "two", "three"].into_iter().enumerate() {
            store.upsert_book(&Book {
                id: id.into(),
                source_id: "local".into(),
                remote_id: id.into(),
                title: id.into(),
                in_library: true,
                cover: directory.path().join(id).to_string_lossy().into(),
                updated_at: i as i64,
                ..Default::default()
            })?;
        }
        let other_writer = rusqlite::Connection::open(&database)?;
        other_writer.busy_timeout(Duration::ZERO)?;
        let warm = store.with_startup_read_snapshot(|snapshot| {
            assert!(snapshot);
            let current = store.library_continuation(None)?;
            let mut view = InteractionState::load(&store)?;
            // A writer may already hold a reserved lock while the main reader
            // is selecting. No new SQLite connection is needed by the worker.
            other_writer.execute_batch("BEGIN IMMEDIATE")?;
            let select = |view: &InteractionState, n: u8, allowed: bool| {
                StartupCoverWarm::select(&store, &cache, current.as_deref(), view, n, &|| allowed)
            };
            assert!(select(&view, 0, true).is_none());
            assert!(select(&view, 2, false).is_none());
            let warm = select(&view, 9, true).expect("initial local cover paths");
            assert_eq!(warm.paths.len(), 2);
            assert_eq!(
                warm.paths[0],
                directory.path().join(current.as_deref().unwrap())
            );
            assert_ne!(warm.paths[0], warm.paths[1]);
            view.library_filters[0] = 1;
            assert!(select(&view, 2, true).is_none());
            Ok(warm)
        })?;
        other_writer.execute_batch("ROLLBACK; BEGIN EXCLUSIVE")?;
        warm.run(|| false);
        other_writer.execute_batch("ROLLBACK")?;
        Ok(())
    }

    #[test]
    fn nested_metadata_snapshot_does_not_change_the_connection_busy_policy() -> Result<()> {
        let store = Store::open(":memory:")?;
        let connection = store.test_connection();
        connection.busy_timeout(Duration::from_millis(83))?;
        let outer = connection.unchecked_transaction()?;
        assert!(!store.with_startup_read_snapshot(|active| Ok(active))?);
        outer.rollback()?;
        let timeout: u32 = connection.pragma_query_value(None, "busy_timeout", |row| row.get(0))?;
        assert_eq!(timeout, 83);
        Ok(())
    }

    #[test]
    fn prepared_metadata_can_move_from_the_worker_to_the_ui_thread() {
        fn require_send<T: Send>() {}
        require_send::<PreparedApp>();
    }

    #[test]
    fn preparation_keeps_download_and_source_work_dormant() {
        let directory = tempfile::tempdir().unwrap();
        let prepared = App::prepare(
            AppOptions {
                data_dir: directory.path().into(),
                startup_options: Default::default(),
                demo: false,
                offline: true,
                initial_screen: "library".into(),
                landscape: false,
                resume: false,
            },
            DeviceProfile::Pw5,
        )
        .unwrap();
        assert!(prepared.network_control.is_suspended());
        assert_eq!(prepared.downloads.active_count(), 0);
        prepared.downloads.shutdown();
        assert!(prepared.downloads.wait_stopped(Duration::from_secs(2)));
    }

    #[test]
    fn early_boot_exit_or_sleep_preserves_the_existing_navigation_checkpoint() {
        use crate::app::tests::DemoRouteFixture;

        for suspend in [false, true] {
            let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw5, false);
            let saved = NavigationSession {
                route: "history".into(),
                page: 7,
                ..NavigationSession::default()
            };
            fixture.app.store.save_session(&saved).unwrap();
            let mut saved_preferences = fixture.app.prefs.clone();
            saved_preferences.night_mode = !saved_preferences.night_mode;
            fixture
                .app
                .store
                .save_preferences(&saved_preferences)
                .unwrap();
            fixture.app.startup_stage = Some(AttachStage::Route);
            if suspend {
                fixture.app.prepare_suspend().unwrap();
            }
            fixture.app.shutdown();
            assert_eq!(fixture.app.store.session().unwrap(), Some(saved));
            assert_eq!(
                serde_json::to_value(fixture.app.store.preferences().unwrap()).unwrap(),
                serde_json::to_value(saved_preferences).unwrap(),
            );
        }
    }
}
