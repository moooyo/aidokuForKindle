//! Nonblocking worker drain before a checked metadata database restore.

use super::*;
use crate::backup::{PreparedRestore, RestoreReport};

pub(super) struct RestoreResult {
    pub report: RestoreReport,
    pub missing_downloads: usize,
}

#[derive(Default)]
pub(super) enum RestoreRuntime {
    #[default]
    Idle,
    Draining(PreparedRestore),
    Applying(u64),
    Restarting,
    Failed,
}

impl App {
    pub(super) fn metadata_restore_active(&self) -> bool {
        !matches!(self.metadata_restore, RestoreRuntime::Idle)
    }

    /// The caller must be the current, explicit restore-confirmation action.
    /// Existing detached jobs are retained in `active_jobs` until their result
    /// arrives, even when the ordinary lifecycle code discards their payload.
    pub(super) fn begin_metadata_restore(&mut self) -> Result<()> {
        anyhow::ensure!(
            self.modal_context == "backup-restore-confirm"
                && !self.metadata_restore_active()
                && !self.suspended
                && !self.shutting_down,
            "The checked metadata restore confirmation is no longer active"
        );
        self.save_reader_progress()?;
        self.checkpoint_navigation(true)?;
        let prepared = self.data_management.take_pending_restore()?;
        self.cancel_pending_user_network();
        self.cancel_sensitive_keyboard();
        self.cancel_migration();
        self.cancel_tracking_work();
        self.invalidate_automatic_updates();
        self.invalidate_reader_work();
        self.lifecycle_generation = self.lifecycle_generation.wrapping_add(1);
        self.source_images_changed();
        self.network_control.suspend();
        self.downloads.shutdown();
        self.pairing = None;
        self.metadata_restore = RestoreRuntime::Draining(prepared);
        self.modal.clear();
        self.modal_context.clear();
        self.busy = true;
        self.message = tr("loading");
        if let Some(ui) = self.ui.upgrade() {
            ui.global::<State>().set_busy(true);
            ui.global::<State>()
                .set_message(self.message.clone().into());
        }
        Ok(())
    }

    /// Polling never waits for a request or joins a live worker. Old results
    /// are discarded after their operation has released its database handles.
    pub(super) fn poll_metadata_restore(&mut self) {
        while let Ok(message) = self.job_rx.try_recv() {
            self.active_jobs.remove(&message.id);
            self.pending.remove(&message.id);
            self.data_management.finish_job(message.id);
            self.finish_automatic_job(message.id, message.result.is_err());
            self.finish_usage_job(message.id);
            self.finish_storage_details_job(message.id);
            if matches!(self.metadata_restore, RestoreRuntime::Applying(id) if id == message.id) {
                let result = match message.result {
                    Ok(JobResult::MetadataRestore(result)) => result,
                    Ok(_) => Err(anyhow::anyhow!(
                        "The metadata restore worker returned an unrelated result"
                    )),
                    Err(error) => Err(error),
                };
                if let Err(error) = self.finish_metadata_restore(result) {
                    self.show_error(error);
                    let _ = self.sync_view();
                    self.platform.refresh(RefreshKind::Ui);
                }
                return;
            }
        }
        // The download acknowledgement is published after all fetch guards and
        // the controller's SQLite connection have been dropped.
        if !matches!(self.metadata_restore, RestoreRuntime::Draining(_))
            || !self.active_jobs.is_empty()
            || !self.downloads.is_stopped()
        {
            return;
        }
        let RestoreRuntime::Draining(prepared) = std::mem::take(&mut self.metadata_restore) else {
            return;
        };
        // No worker can use a retained source manager after the drain barrier.
        self.source_manager_pool = Arc::new(source_jobs::SourceManagerPool::default());
        let manager = self.data_management.manager.clone();
        let data_dir = self.data_dir.clone();
        match self.spawn_local_job(true, JobKind::MetadataRestore, move || {
            Ok(JobResult::MetadataRestore(restore_metadata(
                &manager, prepared, &data_dir,
            )))
        }) {
            Ok(id) => self.metadata_restore = RestoreRuntime::Applying(id),
            Err(error) => {
                self.metadata_restore = RestoreRuntime::Restarting;
                if let Err(restart_error) = self.restart_after_metadata_restore() {
                    self.metadata_restore = RestoreRuntime::Failed;
                    eprintln!("aidoku: restore worker restart: {restart_error:#}");
                } else {
                    self.metadata_restore = RestoreRuntime::Idle;
                }
                self.show_error(error);
                let _ = self.sync_view();
                self.platform.refresh(RefreshKind::Ui);
            }
        }
    }

    pub(super) fn finish_metadata_restore(&mut self, result: Result<RestoreResult>) -> Result<()> {
        self.metadata_restore = RestoreRuntime::Restarting;
        if let Err(error) = self.restart_after_metadata_restore() {
            self.metadata_restore = RestoreRuntime::Failed;
            self.busy = false;
            return Err(error).context("Restart application workers after restoring metadata");
        }
        self.metadata_restore = RestoreRuntime::Idle;
        match result {
            Ok(restored) => {
                let _ = self
                    .data_management
                    .logs
                    .record("backup.restored", logging::LogOutcome::Success);
                self.accept_data_management(
                    data_management::DataManagementResult::RestoreApplied(restored.report),
                )?;
                if restored.missing_downloads > 0 {
                    self.notify(
                        tr("metadata_restore_missing_downloads")
                            .replace("{count}", &restored.missing_downloads.to_string()),
                    );
                }
            }
            Err(error) => {
                let _ = self
                    .data_management
                    .logs
                    .record("backup.restored", logging::LogOutcome::Failure);
                self.show_error(error);
            }
        }
        self.sync_view()?;
        self.platform.refresh(RefreshKind::Full);
        Ok(())
    }

    fn restart_after_metadata_restore(&mut self) -> Result<()> {
        self.prefs = self.store.preferences()?;
        self.interactions = InteractionState::load(&self.store)?;
        self.data_management
            .logs
            .set_enabled(self.prefs.write_logs)?;
        self.automatic_updates = automatic_updates::AutomaticUpdates::default();
        self.tracking_accounts = tracking_accounts::TrackingAccounts::default();
        self.source_images_changed();
        self.source_manager_pool = Arc::new(source_jobs::SourceManagerPool::default());
        self.network_control.resume();
        let downloads = DownloadWorker::start_initialized(
            self.data_dir.clone(),
            self.data_dir.join("library.sqlite3"),
            crate::downloads::DownloadOptions {
                offline: self.options.offline || self.options.demo,
                ..crate::downloads::DownloadOptions::default()
            },
            false,
        )?;
        let (width, height) = self.platform.runtime_logical_size(self.prefs.landscape);
        downloads.set_processing_viewport(width, height)?;
        self.downloads = downloads;
        self.reader = None;
        self.displayed_reader = None;
        self.displayed_image_leases.clear();
        self.reader_recovery = None;
        self.committed_progress = None;
        self.committed_progress_dirty = false;
        self.reader_inflight = None;
        self.chapter_inflight = None;
        self.prefetch_inflight = None;
        self.cover_inflight = None;
        self.pending.clear();
        self.busy = false;
        self.resume_reader_repair = false;
        self.resume_frame_pending = false;
        self.resume_network_pending = false;
        self.network_probe_inflight = false;
        self.network_probe_rx = None;
        self.route = "settings".into();
        self.return_route = "library".into();
        self.current_book_id = self.store.library_continuation(None)?;
        self.page = 1;
        self.selected.clear();
        self.selected_chapters.clear();
        self.selection_mode = false;
        self.search_results.clear();
        self.catalog.clear();
        self.invalidate_download_usage();
        self.invalidate_storage_details();
        self.platform.apply_preferences(&self.prefs)?;
        self.sync_runtime_geometry()?;
        self.poll_device_policies();
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_reader_image(Image::default());
            state.set_reader_image_next(Image::default());
            state.set_reader_image_ready(false);
        }
        Ok(())
    }
}

fn restore_metadata(
    manager: &crate::backup::BackupManager,
    prepared: PreparedRestore,
    data_dir: &Path,
) -> Result<RestoreResult> {
    let report = manager.apply_restore(prepared)?;
    let store = Store::open_existing_for_write(&data_dir.join("library.sqlite3"))?;
    let cache = crate::cache::CacheManager::open(data_dir)?;
    let missing_downloads = reconcile_restored_downloads(&store, &cache)
        .context("Metadata was restored, but the offline image check could not finish")?;
    Ok(RestoreResult {
        report,
        missing_downloads,
    })
}

/// A metadata snapshot cannot recreate images removed after its creation.
/// Protect valid originals, preserve unrelated owners, and downgrade only
/// chapters whose manifest can no longer establish a complete offline copy.
fn reconcile_restored_downloads(
    store: &Store,
    cache: &crate::cache::CacheManager,
) -> Result<usize> {
    let mut missing = 0;
    for chapter_id in store.completed_download_chapter_ids()? {
        let pages = store.pages(&chapter_id)?;
        let mut valid = !pages.is_empty();
        let mut completed = 0_u32;
        for (index, page) in pages.iter().enumerate() {
            let Some(path) = page.local_path.as_ref() else {
                valid = false;
                continue;
            };
            let resolved = match cache.lookup_raw(page) {
                Ok(path) => path,
                Err(error) if invalid_image(&error) => None,
                Err(error) => return Err(error),
            };
            let current = match fs::canonicalize(path) {
                Ok(path) => Some(path),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
                Err(error) => return Err(error).context("Resolve a restored offline image"),
            };
            let matches = resolved
                .as_ref()
                .zip(current.as_ref())
                .is_some_and(|(cached, path)| path == cached);
            if matches {
                cache.pin_raw(page, &format!("download:{chapter_id}"))?;
                completed = completed.saturating_add(1);
            } else {
                valid = false;
                if !page.url.is_empty() {
                    store.invalidate_page_cache(&chapter_id, index as u32)?;
                }
            }
        }
        if let Some(mut job) = store.download(&chapter_id)? {
            valid &= job.total as usize == pages.len() && job.completed == job.total;
            if !valid {
                job.state = "paused".into();
                job.total = pages.len() as u32;
                job.completed = completed.min(job.total);
                job.error.clear();
                store.save_download(&job)?;
            }
        }
        if !valid {
            store.set_chapter_downloaded(&chapter_id, false)?;
            missing += 1;
        }
    }
    Ok(missing)
}

fn invalid_image(error: &anyhow::Error) -> bool {
    error.chain().any(|cause| {
        cause.is::<image::ImageError>()
            || cause.is::<png::DecodingError>()
            || cause
                .downcast_ref::<std::io::Error>()
                .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{domain::DownloadJob, platform::DeviceProfile};

    fn fixture() -> super::super::tests::DemoRouteFixture {
        super::super::tests::DemoRouteFixture::new("settings", DeviceProfile::Pw6, false)
    }

    fn prepare_restore(app: &mut App) {
        let backup = app.data_management.manager.create().unwrap();
        app.data_management.pending_restore = Some(
            app.data_management
                .manager
                .prepare_restore(&backup.id)
                .unwrap(),
        );
        app.modal_context = "backup-restore-confirm".into();
    }

    #[test]
    fn a_checkpoint_failure_keeps_the_checked_restore_for_review() {
        let mut fixture = fixture();
        prepare_restore(&mut fixture.app);
        fixture
            .app
            .store
            .test_connection()
            .execute_batch(
                "CREATE TRIGGER reject_restore_checkpoint BEFORE INSERT ON preferences
             WHEN NEW.key='navigation-session'
             BEGIN SELECT RAISE(FAIL,'Injected checkpoint failure'); END;",
            )
            .unwrap();
        assert!(fixture.app.begin_metadata_restore().is_err());
        assert!(fixture.app.data_management.pending_restore.is_some());
        assert!(!fixture.app.metadata_restore_active());
    }

    #[test]
    fn restoration_waits_for_every_detached_job_before_writing() {
        let mut fixture = fixture();
        let original = fixture.app.prefs.brightness;
        prepare_restore(&mut fixture.app);
        fixture.app.prefs.brightness = 1;
        fixture
            .app
            .store
            .save_preferences(&fixture.app.prefs)
            .unwrap();
        fixture.app.active_jobs.insert(u64::MAX);
        fixture.app.begin_metadata_restore().unwrap();
        let started = Instant::now();
        while !fixture.app.downloads.is_stopped() && started.elapsed() < Duration::from_secs(3) {
            std::thread::sleep(Duration::from_millis(5));
        }
        fixture.app.poll_metadata_restore();
        assert!(matches!(
            fixture.app.metadata_restore,
            RestoreRuntime::Draining(_)
        ));
        assert_eq!(fixture.app.store.preferences().unwrap().brightness, 1);
        fixture.app.active_jobs.remove(&u64::MAX);
        let started = Instant::now();
        while fixture.app.metadata_restore_active() && started.elapsed() < Duration::from_secs(5) {
            fixture.app.poll();
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(!fixture.app.metadata_restore_active());
        assert_eq!(
            fixture.app.store.preferences().unwrap().brightness,
            original
        );
        assert_eq!(fixture.app.prefs.brightness, original);
        assert!(!fixture.app.downloads.is_stopped());
    }

    #[test]
    fn a_failed_restart_retains_the_fence_and_shutdown_does_not_save_old_preferences() {
        let mut fixture = fixture();
        let original = fixture.app.store.preferences().unwrap();
        fixture
            .app
            .store
            .write_ui_settings("interaction-v1", &json!({"book_directions": 7}))
            .unwrap();
        fixture.app.metadata_restore = RestoreRuntime::Applying(1);
        assert!(
            fixture
                .app
                .finish_metadata_restore(Err(anyhow::anyhow!("Injected restore failure")))
                .is_err()
        );
        assert!(matches!(
            fixture.app.metadata_restore,
            RestoreRuntime::Failed
        ));
        assert!(
            fixture
                .app
                .spawn_local_job(false, JobKind::General, || Ok(JobResult::CacheCleared))
                .is_err()
        );
        fixture.app.prefs.brightness = 1;
        fixture.app.shutdown();
        assert_eq!(fixture.app.store.preferences().unwrap(), original);
    }

    #[test]
    fn missing_restored_images_downgrade_completed_downloads_without_fetching() {
        let fixture = fixture();
        let book = Book {
            id: "restore:book".into(),
            source_id: "local".into(),
            remote_id: "book".into(),
            title: "Offline restore fixture".into(),
            in_library: true,
            ..Book::default()
        };
        let chapter = Chapter {
            id: "restore:chapter".into(),
            book_id: book.id.clone(),
            source_id: "local".into(),
            remote_id: "chapter".into(),
            ..Chapter::default()
        };
        fixture.app.store.upsert_book(&book).unwrap();
        fixture
            .app
            .store
            .save_chapters(&book.id, &[chapter.clone()])
            .unwrap();
        let path = fixture.app.data_dir.join("offline.png");
        GrayImage::from_pixel(8, 8, image::Luma([70]))
            .save(&path)
            .unwrap();
        fixture
            .app
            .store
            .save_pages(
                &chapter.id,
                &[Page {
                    local_path: Some(path.clone()),
                    ..Page::default()
                }],
            )
            .unwrap();
        fixture
            .app
            .store
            .save_download(&DownloadJob {
                chapter_id: chapter.id.clone(),
                book_id: book.id.clone(),
                state: "completed".into(),
                total: 1,
                completed: 1,
                error: String::new(),
            })
            .unwrap();
        fs::remove_file(&path).unwrap();
        assert_eq!(
            reconcile_restored_downloads(&fixture.app.store, &fixture.app.disk_cache).unwrap(),
            1
        );
        assert!(
            !fixture
                .app
                .store
                .chapter(&chapter.id)
                .unwrap()
                .unwrap()
                .downloaded
        );
        let job = fixture.app.store.download(&chapter.id).unwrap().unwrap();
        assert_eq!(
            (job.state.as_str(), job.completed, job.total),
            ("paused", 0, 1)
        );
    }
}
