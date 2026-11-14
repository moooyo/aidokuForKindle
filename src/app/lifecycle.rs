use super::retained_scene::RetainedReaderScene;
use super::*;
use crate::domain::SearchSession;

impl App {
    /// Close application and transport admission, then project prepared memory.
    /// The caller must return to the event loop before checkpointing a cover.
    pub fn begin_sleep_transition(&mut self, show_cover: bool) -> Result<bool> {
        if self.suspended {
            if show_cover && self.sleep_cover_enabled() {
                self.show_sleep_cover()?;
                return Ok(true);
            }
            self.hide_sleep_cover();
            return Ok(false);
        }
        anyhow::ensure!(
            !self.metadata_restore_active(),
            "Metadata restoration must finish before the application can suspend"
        );
        crate::reader_timing::mark("suspend_requested");
        crate::reader_timing::cancel_action("lifecycle");
        crate::reader_timing::cancel_native("lifecycle");
        self.platform.cancel_reader_frame();
        self.pending_wide_navigation = 0;
        self.cancel_pending_user_network();
        self.invalidate_automatic_updates();
        self.cancel_tracking_work();
        self.cancel_sensitive_keyboard();
        self.clear_reader_recovery();
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_reader_input_epoch(state.get_reader_input_epoch().wrapping_add(1));
        }
        self.resume_reader_repair = self.route == "reader"
            && self.displayed_reader.as_ref().is_none_or(|frame| {
                !same_reader_render(&frame.preferences, &self.effective_reader_preferences())
                    || frame.hardware_dither
                        != self
                            .effective_reader_preferences()
                            .uses_hardware_dither(self.platform.hardware_y4_available())
            });
        self.suspended = true;
        self.cancel_sleep_cover_preparation();
        self.network_control.begin_suspend_gate();
        self.downloads.begin_suspend_gate()?;
        self.sync_storage_details_visibility();
        self.suspend_checkpoint_ready = false;
        self.suspend_checkpoint_started = false;
        self.lifecycle_generation = self.lifecycle_generation.wrapping_add(1);
        self.resume_network_pending = false;
        self.resume_frame_pending = false;
        self.network_probe_inflight = false;
        self.network_probe_rx = None;
        self.gate_reader_work_for_sleep();
        self.reader_inflight = None;
        self.prefetch_inflight = None;
        self.cover_inflight = None;
        self.cover_queue.clear();
        self.cover_pending.clear();
        self.cover_view.clear();
        self.pending.clear();
        for group in &mut self.search_results {
            if group.pending {
                group.pending = false;
                group.error = Some(tr("request_timeout"));
            }
        }
        self.busy = false;
        self.message.clear();
        self.message_until = None;
        if show_cover && self.sleep_cover_enabled() {
            self.show_sleep_cover()?;
            return Ok(true);
        }
        self.hide_sleep_cover();
        Ok(false)
    }

    /// Run only after checked cover presentation, or during a stock handoff.
    pub fn complete_sleep_checkpoint(&mut self) -> Result<()> {
        if !self.suspended {
            self.begin_sleep_transition(false)?;
        }
        if self.suspend_checkpoint_ready {
            return Ok(());
        }
        // A ready frame can own disk-cache leases whose destructors prune the
        // cache. Keep them through cover presentation and release them now.
        self.clear_reader_next_frame();
        if !self.suspend_checkpoint_started {
            // This strong fence can wait for an earlier checksum or sync. It
            // deliberately occurs after the prepared cover has been presented.
            self.network_control.suspend();
            self.downloads.pause_for_suspend()?;
            self.suspend_checkpoint_started = true;
            let _ = self
                .data_management
                .logs
                .record("app.suspend", logging::LogOutcome::Success);
        }
        self.restore_displayed_reader()?;
        if self.route == "reader" {
            self.modal.clear();
            self.modal_context.clear();
        }
        let _ = self.platform.abort_outbound_network_for_suspend()?;
        if !self.is_starting() {
            self.save_reader_progress()?;
            self.checkpoint_navigation(true)?;
        }
        self.suspend_checkpoint_ready = true;
        crate::reader_timing::mark("suspend_checkpoint_ready");
        Ok(())
    }

    /// Compatibility path for callers that require the normal stock handoff.
    pub fn prepare_suspend(&mut self) -> Result<()> {
        self.begin_sleep_transition(false)?;
        self.complete_sleep_checkpoint()
    }

    /// This is a controller barrier, not a wait for a disconnected HTTP socket.
    pub fn suspend_ready(&mut self) -> Result<bool> {
        Ok(self.suspended
            && self.suspend_checkpoint_ready
            && self.downloads.suspend_ready()
            && self.platform.device_policies_restored())
    }

    /// Resume the retained scene before checking Wi-Fi or starting a decoder.
    pub fn resume_after_sleep(&mut self) -> Result<()> {
        self.hide_sleep_cover();
        if !self.suspended {
            return Ok(());
        }
        crate::reader_timing::mark("resume_requested");
        self.network_control.resume();
        self.downloads
            .resume_owned(self.options.offline || self.options.demo)?;
        self.suspended = false;
        let _ = self
            .data_management
            .logs
            .record("app.resume", logging::LogOutcome::Success);
        self.suspend_checkpoint_ready = false;
        self.suspend_checkpoint_started = false;
        self.resume_frame_pending = true;
        self.invalidate_storage_details();
        let needs_first_image = self.route == "reader" && self.displayed_reader.is_none();
        self.busy = needs_first_image;
        self.resume_network_pending = !self.options.offline && !self.options.demo;
        self.next_network_probe = Instant::now() + Duration::from_millis(750);
        self.platform.invalidate_status_cache();
        if let Some(reader) = &mut self.reader {
            reader.menu_open = false;
        }
        if self.route == "reader" {
            self.project_retained_reader_scene();
        }
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_message("".into());
            state.set_busy(needs_first_image);
            if !self.options.demo && self.route != "reader" {
                state.set_time(chrono::Local::now().format("%H:%M").to_string().into());
            }
        }
        // The native coordinator repaints the retained RGB scene. Re-decoding
        // would add latency and could replace the page before it is restored.
        if self.route == "reader" && self.displayed_reader.is_none() {
            self.reader_dirty = true;
        }
        Ok(())
    }

    /// Close retained chrome and restore image identity before native ownership
    /// returns. Metadata and device observations wait until after that frame.
    fn project_retained_reader_scene(&mut self) {
        let preferences = self.effective_reader_preferences();
        let scene = RetainedReaderScene::from_memory(
            self.displayed_reader.as_ref().map(|frame| &frame.session),
            self.reader.as_ref(),
            &preferences,
        );
        if let Some(book_id) = scene.book_id.as_ref() {
            self.current_book_id = Some(book_id.clone());
        }
        self.page = 1;
        self.modal.clear();
        self.modal_context.clear();
        let Some(ui) = self.ui.upgrade() else {
            return;
        };
        let state = ui.global::<State>();
        state.set_route("reader".into());
        state.set_reader_menu(false);
        state.set_modal("".into());
        state.set_summary_is_book(false);
        state.set_busy(!scene.image_ready);
        state.set_message("".into());
        state.set_reader_image_ready(scene.image_ready);
        state.set_current_chapter_id(scene.chapter_id.into());
        state.set_current_chapter_number(scene.chapter_number.into());
        state.set_current_chapter(scene.chapter_title.into());
        state.set_reader_page(scene.page as i32);
        state.set_reader_total(scene.total as i32);
        state.set_landscape(scene.landscape);
        state.set_spread(scene.spread);
        state.set_cover_alone(
            self.displayed_reader
                .as_ref()
                .map_or(preferences.cover_alone, |frame| {
                    frame.preferences.cover_alone
                }),
        );
        state.set_reading_direction(scene.reading_direction.into());
        state.set_page(1);
        state.set_page_count(1);
        // Images, viewport offsets, zoom, collection models and cached book
        // metadata stay resident. Opening the menu refreshes their metadata.
    }

    pub(super) fn restore_displayed_reader(&mut self) -> Result<()> {
        if self.route != "reader" {
            return Ok(());
        }
        let Some(saved) = self.displayed_navigation_reader() else {
            if let Some(reader) = &mut self.reader {
                reader.menu_open = false;
            }
            return Ok(());
        };
        let chapters = match self.reader.as_ref().filter(|reader| {
            reader
                .chapters()
                .iter()
                .any(|chapter| chapter.id == saved.chapter_id)
        }) {
            Some(reader) => reader.chapters().to_vec(),
            None => self.store.chapters(&saved.book_id)?,
        };
        if !chapters
            .iter()
            .any(|chapter| chapter.id == saved.chapter_id)
        {
            return Ok(());
        }
        let mut reader = ReaderState::new(
            chapters,
            &saved.chapter_id,
            saved.page,
            &self.effective_reader_preferences(),
        )?;
        if saved.total > 0 {
            reader.set_total(saved.total);
        }
        reader.page = saved.page.clamp(1, reader.total.max(1));
        if saved.scaled_height > 0 {
            reader.set_vertical_dimensions(saved.scaled_height, saved.viewport_height.max(1));
        }
        reader.vertical_offset = saved.vertical_offset;
        reader.zoom = saved.zoom;
        reader.set_wide_page(saved.wide_page, saved.wide_half);
        reader.menu_open = false;
        self.current_book_id = Some(saved.book_id.clone());
        self.reader = Some(reader);
        if let Some(displayed) = &mut self.displayed_reader {
            displayed.session = saved;
        }
        Ok(())
    }

    /// Keep the last accepted page while retaining the layout already applied to
    /// the scene. The pixel pipeline is repaired only after the wake repaint.
    fn displayed_navigation_reader(&self) -> Option<ReaderSession> {
        let displayed = self.displayed_reader.as_ref()?;
        let mut saved = displayed.session.clone();
        let effective = self.effective_reader_preferences();
        saved.landscape = effective.landscape;
        saved.spread = effective.spread;
        saved.menu_open = false;
        if displayed.preferences.reading_direction != effective.reading_direction {
            saved.vertical_offset = 0;
            saved.scaled_height = 0;
            saved.viewport_height = 0;
        }
        Some(saved)
    }

    /// Called only after the retained native RGB scene has been repainted. The
    /// desktop lifecycle harness supplies the equivalent frame acknowledgement.
    pub fn finish_resume_frame(&mut self) -> Result<()> {
        if self.suspended {
            return Ok(());
        }
        crate::reader_timing::mark("retained_resume_frame_acknowledged");
        self.resume_frame_pending = false;
        // A fast wake can precede checkpoint permission. Release its obsolete
        // next frame only after the retained foreground frame is visible.
        self.clear_reader_next_frame();
        if self.route == "reader" && !self.options.demo {
            if let Some(ui) = self.ui.upgrade() {
                ui.global::<State>()
                    .set_time(chrono::Local::now().format("%H:%M").to_string().into());
            }
        }
        if self.route != "reader" || self.reader_inflight.is_some() {
            return Ok(());
        }
        if self.resume_reader_repair || self.reader_dirty || self.displayed_reader.is_none() {
            let result = self.request_reader_images().and_then(|()| self.sync_view());
            if let Err(error) = result {
                if let Some(target) = self.reader_target() {
                    self.recover_reader_failure(target, error);
                } else {
                    self.show_reader_action_error(error);
                }
            }
        }
        Ok(())
    }

    pub(crate) fn checkpoint_navigation(&mut self, force: bool) -> Result<()> {
        if !force && self.last_session_save.elapsed() < Duration::from_secs(1) {
            return Ok(());
        }
        let session = self.navigation_session();
        self.store.save_session(&session)?;
        self.last_session_save = Instant::now();
        Ok(())
    }

    fn navigation_session(&self) -> NavigationSession {
        let reader = if self.prefs.incognito || self.route != "reader" {
            None
        } else {
            self.displayed_navigation_reader().or_else(|| {
                self.reader.as_ref().map(|reader| ReaderSession {
                    book_id: reader.current_chapter().book_id.clone(),
                    chapter_id: reader.current_chapter().id.clone(),
                    page: reader.page,
                    total: reader.total,
                    vertical_offset: reader.vertical_offset,
                    zoom: reader.zoom,
                    wide_half: reader.wide_half,
                    wide_page: reader.is_wide_page(),
                    landscape: reader.landscape,
                    spread: reader.spread,
                    menu_open: false,
                    scaled_height: reader.image_height,
                    viewport_height: reader.viewport_height,
                })
            })
        };
        let panel_route = match (self.modal.as_str(), self.modal_context.as_str()) {
            ("downloads", _) => "downloads",
            ("summary", "summary") => "summary",
            ("summary", "about") => "about",
            ("summary", "cache-info") => "cache-info",
            _ => "",
        };
        let private_reader = self.prefs.incognito && self.route == "reader";
        NavigationSession {
            version: 1,
            route: if private_reader {
                "library".into()
            } else {
                self.route.clone()
            },
            page: self.page,
            return_route: self.return_route.clone(),
            current_book_id: if private_reader {
                None
            } else {
                reader
                    .as_ref()
                    .map(|reader| reader.book_id.clone())
                    .or_else(|| self.current_book_id.clone())
            },
            current_source: self.current_source.clone(),
            library_filter: self.library_filter,
            source_filter: self.source_filter,
            catalog_filter: self.catalog_filter,
            history_filter: self.history_filter,
            newest_first: self
                .ui
                .upgrade()
                .is_none_or(|ui| ui.global::<State>().get_newest_first()),
            sort_title: self.sort_title,
            unread_only: self.unread_only,
            downloaded_only: self.downloaded_only,
            catalog_unread_only: self.catalog_unread_only,
            catalog_downloaded_only: self.catalog_downloaded_only,
            catalog_remote_page: self.catalog_remote_page,
            catalog_has_more: self.catalog_has_more,
            catalog_query: self.catalog_query.clone(),
            catalog_book_ids: self.catalog.iter().map(|book| book.id.clone()).collect(),
            catalog_listings: self.catalog_listings.clone(),
            query: self.query.clone(),
            search_scope: self.search_scope.clone(),
            search_groups: self
                .search_results
                .iter()
                .map(|group| SearchSession {
                    id: group.id.clone(),
                    title: group.title.clone(),
                    book_ids: group.books.iter().map(|book| book.id.clone()).collect(),
                    failed: group.error.is_some(),
                    has_next: group.has_next,
                })
                .collect(),
            selection_mode: self.selection_mode,
            selected: self.selected.iter().cloned().collect(),
            selected_chapters: self.selected_chapters.iter().cloned().collect(),
            panel_route: if private_reader {
                String::new()
            } else {
                panel_route.into()
            },
            panel_page: self.panel_page,
            reader,
        }
    }

    pub(crate) fn restore_saved_session(&mut self) -> Result<()> {
        crate::reader_timing::cancel_action("restored");
        let Some(saved) = self.store.session()? else {
            self.network_control.resume();
            self.downloads
                .resume_owned(self.options.offline || self.options.demo)?;
            return Ok(());
        };
        self.lifecycle_generation = self.lifecycle_generation.wrapping_add(1);
        self.invalidate_automatic_updates();
        self.invalidate_reader_work();
        self.reader_inflight = None;
        self.prefetch_inflight = None;
        self.pending.clear();
        self.busy = false;
        self.route = normalize_route(&saved.route).into();
        self.return_route = normalize_route(&saved.return_route).into();
        self.page = saved.page.max(1);
        self.current_book_id = saved.current_book_id.clone();
        self.current_source = saved.current_source;
        self.library_filter = saved.library_filter;
        self.source_filter = saved.source_filter;
        self.catalog_filter = saved.catalog_filter;
        self.history_filter = saved.history_filter;
        self.sort_title = saved.sort_title;
        self.unread_only = saved.unread_only;
        self.downloaded_only = saved.downloaded_only;
        self.catalog_unread_only = saved.catalog_unread_only;
        self.catalog_downloaded_only = saved.catalog_downloaded_only;
        self.catalog_remote_page = saved.catalog_remote_page;
        self.catalog_has_more = saved.catalog_has_more;
        self.catalog_query = saved.catalog_query;
        self.catalog.clear();
        for id in saved.catalog_book_ids {
            if let Some(book) = self.store.book(&id)? {
                self.catalog.push(book);
            }
        }
        if !saved.catalog_listings.is_empty() {
            self.catalog_listings = saved.catalog_listings;
        }
        self.query = saved.query;
        self.search_scope = saved.search_scope;
        self.search_results.clear();
        for group in saved.search_groups {
            let mut books = Vec::new();
            for id in group.book_ids {
                if let Some(book) = self.store.book(&id)? {
                    books.push(book);
                }
            }
            self.search_results.push(SearchResult {
                id: group.id,
                title: group.title,
                books,
                error: group.failed.then(|| tr("request_timeout")),
                has_next: group.has_next,
                pending: false,
            });
        }
        self.selection_mode = saved.selection_mode;
        self.selected = saved.selected.into_iter().collect();
        self.selected_chapters = saved.selected_chapters.into_iter().collect();
        if let Some(ui) = self.ui.upgrade() {
            ui.global::<State>().set_newest_first(saved.newest_first);
        }
        self.network_control.resume();
        self.downloads
            .resume_owned(self.options.offline || self.options.demo)?;
        if self.route == "reader" {
            if let Some(reader) = saved.reader {
                if self.store.chapter(&reader.chapter_id)?.is_some() {
                    self.prefs.landscape = reader.landscape;
                    self.sync_runtime_geometry()?;
                    self.load_reader_target(
                        ReaderTarget::new(
                            &reader.chapter_id,
                            reader.page,
                            reader.vertical_offset,
                            reader.zoom,
                        )
                        .with_wide_half(reader.wide_half),
                    )?;
                } else {
                    self.route = "library".into();
                }
            } else {
                self.route = "library".into();
            }
        }
        match saved.panel_route.as_str() {
            "downloads" => {
                self.open_downloads()?;
                self.panel_page = saved.panel_page;
                self.refresh_download_rows()?;
            }
            "summary" => {
                self.open_summary()?;
                self.panel_page = saved
                    .panel_page
                    .min(self.panel_pages.len().saturating_sub(1));
            }
            "about" => self.open_setting("about")?,
            "cache-info" => self.choose_panel("cache-info")?,
            _ => {}
        }
        self.resume_network_pending = !self.options.offline && !self.options.demo;
        self.next_network_probe = Instant::now();
        Ok(())
    }

    pub(crate) fn poll_network_resume(&mut self) {
        if !self.resume_network_pending || self.suspended {
            return;
        }
        let observed = self
            .network_probe_rx
            .as_ref()
            .map(|receiver| receiver.try_recv());
        match observed {
            Some(Ok((generation, ready))) => {
                self.network_probe_rx = None;
                self.network_probe_inflight = false;
                if generation == self.lifecycle_generation && ready {
                    if let Err(error) = self.downloads.resume_owned(true) {
                        eprintln!("aidoku: resume downloads: {error:#}");
                    }
                    self.resume_network_pending = false;
                    self.cover_view.clear();
                    self.cover_retry_after.clear();
                    if !self.resume_frame_pending
                        && self.route == "reader"
                        && (self.displayed_reader.is_none() || self.resume_reader_repair)
                    {
                        let retry = if self.reader_recovery.is_some() {
                            self.retry_reader_recovery()
                        } else {
                            self.request_reader_images()
                        };
                        if let Err(error) = retry {
                            self.show_error(error);
                        }
                    }
                    if !self.resume_frame_pending && self.prefetch_dirty {
                        if let Err(error) = self.start_reader_prefetch() {
                            eprintln!("aidoku: wake read-ahead: {error:#}");
                        }
                    }
                } else {
                    self.next_network_probe = Instant::now() + Duration::from_secs(2);
                }
            }
            Some(Err(mpsc::TryRecvError::Disconnected)) => {
                self.network_probe_rx = None;
                self.network_probe_inflight = false;
                self.next_network_probe = Instant::now() + Duration::from_secs(2);
            }
            _ => {}
        }
        if !self.resume_network_pending
            || self.network_probe_inflight
            || Instant::now() < self.next_network_probe
        {
            return;
        }
        let (sender, receiver) = mpsc::channel();
        let native = self.platform.is_kindle();
        let generation = self.lifecycle_generation;
        self.network_probe_rx = Some(receiver);
        self.network_probe_inflight = true;
        if let Err(error) = std::thread::Builder::new()
            .name("aidoku-wake-network".into())
            .spawn(move || {
                let _ = sender.send((generation, crate::platform::probe_network_ready(native)));
            })
        {
            self.network_probe_inflight = false;
            self.network_probe_rx = None;
            self.next_network_probe = Instant::now() + Duration::from_secs(2);
            eprintln!("aidoku: start network observation: {error}");
        }
    }

    /// Lifecycle diagnostics deliberately avoid SQL, device probes and decoding.
    pub fn lifecycle_snapshot(&self) -> Value {
        json!({
            "route":self.route,"page":self.page,"current_book":self.current_book_id,"source_id":self.current_source,
            "library_filter":self.library_filter,"source_filter":self.source_filter,"catalog_filter":self.catalog_filter,"history_filter":self.history_filter,
            "reader_page":self.displayed_reader.as_ref().map(|frame| frame.session.page),
            "current_chapter":self.displayed_reader.as_ref().map(|frame| &frame.session.chapter_id),
            "reader_offset":self.displayed_reader.as_ref().map(|frame| frame.session.vertical_offset),
            "reader_zoom":self.displayed_reader.as_ref().map(|frame| frame.session.zoom),
            "landscape":self.prefs.landscape,"spread":self.prefs.spread,"suspended":self.suspended,
            "rotation_degrees":self.platform.display_rotation_degrees(),
            "reader_menu":self.reader.as_ref().map(|reader| reader.menu_open),
            "lifecycle_generation":self.lifecycle_generation,"network_resume_pending":self.resume_network_pending,"reader_frame_pending":self.resume_frame_pending,"reader_repair_pending":self.resume_reader_repair,"reader_image_jobs_started":self.reader_image_jobs_started,"displayed_reader":self.displayed_reader,
        })
    }
}

fn same_reader_render(first: &Preferences, second: &Preferences) -> bool {
    first.landscape == second.landscape
        && first.spread == second.spread
        && first.cover_alone == second.cover_alone
        && first.split_wide == second.split_wide
        && first.reading_direction == second.reading_direction
        && first.crop_borders == second.crop_borders
        && first.dither == second.dither
        && first.normalized_dither_method() == second.normalized_dither_method()
        && first.contrast == second.contrast
        && first.reader_fit == second.reader_fit
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::platform::DeviceProfile;

    #[test]
    fn retained_wide_halves_restore_navigation_without_decoding_again() {
        for half in [0_u8, 1] {
            let mut fixture =
                super::super::tests::DemoRouteFixture::new("settings", DeviceProfile::Pw6, false);
            let book = Book {
                id: "wide:book".into(),
                source_id: "local".into(),
                remote_id: "book".into(),
                title: "Retained wide page".into(),
                in_library: true,
                ..Book::default()
            };
            let chapter = Chapter {
                id: "wide:chapter".into(),
                book_id: book.id.clone(),
                source_id: "local".into(),
                remote_id: "chapter".into(),
                number: 1.0,
                page_count: 2,
                ..Chapter::default()
            };
            fixture.app.store.upsert_book(&book).unwrap();
            fixture
                .app
                .store
                .save_chapters(&book.id, &[chapter.clone()])
                .unwrap();
            fixture.app.prefs.split_wide = true;
            fixture.app.prefs.spread = false;
            fixture.app.interactions.page_layout_mode = "single".into();
            fixture.app.current_book_id = Some(book.id.clone());
            fixture.app.route = "reader".into();
            fixture.app.displayed_reader = Some(DisplayedReader {
                session: ReaderSession {
                    book_id: book.id,
                    chapter_id: chapter.id,
                    page: 1,
                    total: 2,
                    spread: false,
                    wide_page: true,
                    wide_half: half,
                    ..ReaderSession::default()
                },
                preferences: fixture.app.effective_reader_preferences(),
                hardware_dither: false,
                first_path: PathBuf::from("retained-artwork.png"),
                second_path: None,
            });
            fixture.app.restore_displayed_reader().unwrap();
            let reader = fixture.app.reader.as_mut().unwrap();
            assert!(reader.is_wide_page());
            assert_eq!(reader.wide_half, half);
            reader.next_page();
            assert_eq!(
                (reader.page, reader.wide_half),
                if half == 0 { (1, 1) } else { (2, 0) }
            );
            assert_eq!(fixture.app.reader_image_jobs_started, 0);
        }
    }

    #[test]
    fn changing_wide_split_requires_repair_after_retained_resume() {
        let before = Preferences::default();
        let after = Preferences {
            split_wide: true,
            ..before.clone()
        };
        assert!(!same_reader_render(&before, &after));
    }

    #[test]
    fn changing_dither_method_requires_repair_without_replacing_retained_metadata() {
        let before = Preferences::default();
        let after = Preferences {
            dither_method: "hardware".into(),
            ..before.clone()
        };
        assert!(!same_reader_render(&before, &after));
        let unknown = Preferences {
            dither_method: "future-method".into(),
            ..before.clone()
        };
        assert!(same_reader_render(&before, &unknown));
    }
}
