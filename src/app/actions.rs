use super::*;
#[path = "text_pagination.rs"]
mod text_pagination;
use crate::{
    domain::Bookmark,
    ui::{DownloadItem, FilterOption, SettingItem, SourcePreferenceGroup},
};

const BUNDLED_LICENSE_TEXT: &str = include_str!("../../assets/licenses/GPL-3.0-or-later.txt");

pub(super) enum UserNetworkIntent {
    Pairing,
    Reader(ReaderTarget),
    ReaderLoading,
}

pub(super) struct PendingUserNetwork {
    intent: UserNetworkIntent,
    expires: Instant,
}

impl App {
    pub fn dispatch(&mut self, section: &str, action: &str, value: &str) -> Result<()> {
        match self.dispatch_action(section, action, value) {
            Err(error)
                if self.route == "reader"
                    && matches!(
                        section,
                        "reader" | "panel" | "settings" | "app" | "lifecycle"
                    ) =>
            {
                self.show_reader_action_error(error);
                Ok(())
            }
            result => result,
        }
    }

    fn dispatch_action(&mut self, section: &str, action: &str, value: &str) -> Result<()> {
        if self.metadata_restore_active() && !(section == "app" && action == "exit") {
            return Ok(());
        }
        if self.suspended && section != "lifecycle" && !(section == "app" && action == "exit") {
            return Ok(());
        }
        if section != "lifecycle" {
            self.platform.note_user_activity();
        }
        if section == "navigation"
            || action == "back"
                && matches!(
                    section,
                    "app" | "reader" | "detail" | "settings" | "sources"
                )
            || section == "reader"
                && matches!(
                    action,
                    "next" | "previous" | "next-chapter" | "previous-chapter" | "seek"
                )
            || section == "panel" && action == "close"
        {
            self.cancel_pending_user_network();
            if section == "navigation"
                || action == "back"
                || section == "panel" && action == "close"
            {
                self.pending_wide_navigation = 0;
            }
        }
        let picker_open = self.modal_context.starts_with("search-scope:")
            || self.modal_context.starts_with("source-migration:");
        let transient_picker_action = section == "search" && action == "source-picker"
            || section == "sources" && action == "migrate"
            || picker_open
                && (section == "panel" && matches!(action, "close" | "page")
                    || section == "app" && matches!(action, "back" | "left" | "right"));
        if section == "app"
            && action == "back"
            && self
                .ui
                .upgrade()
                .is_some_and(|ui| ui.global::<State>().get_page_jump_open())
        {
            self.close_page_jump();
            self.platform.refresh(RefreshKind::Ui);
            return Ok(());
        }
        match section {
            "lifecycle" => match action {
                "suspend" => self.prepare_suspend()?,
                "resume" => self.resume_after_sleep()?,
                "frame-displayed" => self.finish_resume_frame()?,
                _ => {}
            },
            "navigation" => {
                if self.modal_context == "migration" {
                    self.migration_action("cancel", "")?;
                }
                if self.modal == "keyboard" {
                    self.keyboard_action("cancel", "")?;
                }
                self.clear_reader_recovery();
                self.invalidate_reader_work();
                self.invalidate_search_work();
                for group in &mut self.search_results {
                    if group.pending {
                        group.pending = false;
                        group.error = Some(tr("search_cancelled"));
                    }
                }
                self.modal.clear();
                self.modal_context.clear();
                self.close_page_jump();
                self.selection_mode = false;
                self.selected.clear();
                self.selected_chapters.clear();
                self.page = 1;
                self.route = match value {
                    "1" if self.platform.profile().is_scribe() => "catalog",
                    "1" => "sources",
                    "2" => "search",
                    "3" => "history",
                    "4" => "settings",
                    _ => "library",
                }
                .into();
                if matches!(self.route.as_str(), "sources" | "catalog")
                    && self.platform.profile().is_scribe()
                    && self.catalog.is_empty()
                    && (self.options.demo || !self.options.offline)
                    && (!self.options.demo || self.catalog_query.is_none())
                {
                    let ready = self
                        .store
                        .source(&self.current_source)?
                        .is_some_and(|source| {
                            source.enabled
                                && (self.options.demo
                                    || !matches!(
                                        source.kind.as_str(),
                                        "komga" | "kavita" | "suwayomi"
                                    )
                                    || !source.base_url.is_empty())
                        });
                    if ready {
                        self.fetch_catalog(false)?;
                    }
                }
                if self.route == "settings" {
                    self.refresh_setting_details()?;
                }
            }
            "library" => match action {
                "open" => self.open_book(value)?,
                "continue" => self.resume_reader()?,
                "filter" => {
                    self.library_filter = value.parse::<i32>()?.clamp(0, 4);
                    self.page = 1;
                }
                "filter-menu" => self.open_library_filter()?,
                "sort" => self.set_library_sort(value)?,
                "sort-direction" => {
                    self.interactions.library_ascending = value == "ascending";
                    self.interactions.save(&self.store)?;
                    self.page = 1;
                }
                "cycle-filter" => self.cycle_library_filter(value)?,
                "more" => self.open_library_more()?,
                "select" => {
                    self.selection_mode = true;
                    if !self.selected.insert(value.into()) {
                        self.selected.remove(value);
                    }
                }
                "select-page" => self.select_visible_books(),
                "finish-selection" => {
                    self.selected.clear();
                    self.selection_mode = false;
                }
                "batch-read" => self.mark_selected_books_read()?,
                "batch-download" => self.download_selected_books()?,
                "batch-category" => self.choose_panel("category")?,
                "batch-remove" => self.remove_selected_books()?,
                "refresh" => self.refresh_library()?,
                _ => {}
            },
            "detail" => match action {
                "back" => self.back()?,
                "continue" => self.resume_reader()?,
                "favorite" => {
                    let book = self.current_book()?;
                    self.store.set_library(&book.id, !book.in_library)?;
                    if !book.in_library {
                        self.store
                            .record_library_added(&book.id, chrono::Utc::now().timestamp())?;
                    }
                }
                "sort" => {
                    if let Some(ui) = self.ui.upgrade() {
                        let state = ui.global::<State>();
                        state.set_newest_first(!state.get_newest_first());
                    }
                    self.page = 1;
                }
                "summary" => self.open_summary()?,
                "open-chapter" => {
                    let page = self
                        .store
                        .chapter_progress(value)?
                        .filter(|progress| !progress.completed)
                        .map(|progress| progress.page)
                        .unwrap_or(1);
                    self.load_chapter(value, page)?;
                }
                "download" => self.enqueue_chapter(value)?,
                "cancel-download" => self.downloads.cancel(value)?,
                "remove-download" => self.confirm_remove_chapter_download(value)?,
                "remove-all-downloads" => self.confirm_remove_book_downloads()?,
                "download-next" => self.download_next_five()?,
                "batch-download" => self.open_batch_downloads()?,
                "select-chapter" => {
                    let chapter = self
                        .store
                        .chapter(value)?
                        .context("The selected chapter is missing")?;
                    if chapter.book_id != self.current_book()?.id {
                        bail!("The chapter belongs to another manga");
                    }
                    if !self.selected_chapters.insert(value.into()) {
                        self.selected_chapters.remove(value);
                    }
                }
                "select-all-chapters" => {
                    self.selected_chapters = self
                        .store
                        .chapters(&self.current_book()?.id)?
                        .into_iter()
                        .map(|chapter| chapter.id)
                        .collect();
                }
                "cancel-selection" => self.selected_chapters.clear(),
                "download-selected" => self.download_selected_chapters()?,
                "track" => self.open_tracking_choices()?,
                "migrate" => {
                    self.open_migration()?;
                }
                "reload" => self.reload_detail()?,
                "more" => self.open_detail_more()?,
                _ => {}
            },
            "reader" => self.reader_action(action, value)?,
            "migration" => self.migration_action(action, value)?,
            "sources" => match action {
                "installed-page" => {
                    self.close_page_jump();
                    if let Some(ui) = self.ui.upgrade() {
                        let state = ui.global::<State>();
                        state.set_installed_source_page(
                            (state.get_installed_source_page() + value.parse::<i32>()?)
                                .clamp(1, state.get_installed_source_page_count().max(1)),
                        );
                    }
                }
                "manage" => {
                    self.route = "sources".into();
                    self.source_filter = i32::from(self.platform.profile().is_scribe());
                    self.page = 1;
                    self.modal.clear();
                    self.modal_context.clear();
                }
                "back" => {
                    self.route = if self.platform.profile().is_scribe() {
                        "catalog"
                    } else {
                        "library"
                    }
                    .into();
                    self.source_filter = 0;
                    self.page = 1;
                }
                "language" => {
                    if !matches!(value, "all" | "zh-Hans" | "zh-Hant" | "en" | "ja") {
                        bail!("Unknown source language filter");
                    }
                    self.interactions.source_language = value.into();
                    self.interactions.save(&self.store)?;
                    self.page = 1;
                }
                "filter" => {
                    self.source_filter = value.parse::<i32>()?.clamp(0, 2);
                    self.page = 1;
                }
                "open" => self.open_source(value)?,
                "migrate" => self.open_source_migration(value)?,
                "add" => {
                    self.open_pairing()?;
                    if let Some(ui) = self.ui.upgrade() {
                        ui.global::<State>()
                            .set_modal_title(tr("add_source").into());
                    }
                }
                "list" => self.open_repository_list()?,
                "picker" => {
                    let rows = self
                        .store
                        .sources()?
                        .into_iter()
                        .map(|source| (format!("source:{}", source.id), source.name, source.kind))
                        .collect();
                    self.show_choices(&tr("source"), "source-picker", rows)?;
                }
                "uninstall" => {
                    if !matches!(value, "local" | "komga" | "kavita" | "suwayomi") {
                        self.store.delete_source(value)?;
                        self.notify(tr("removed"));
                    }
                }
                "install" | "update" => self.install_source(value)?,
                "update-all" => self.update_all_sources()?,
                "configure" => self.open_pairing()?,
                "edit-address" | "edit-credential" | "test-connect" => {
                    self.server_action(action, value)?
                }
                _ => {}
            },
            "catalog" => match action {
                "open" => self.open_book(value)?,
                "back" => {
                    self.route = "sources".into();
                    self.page = 1;
                }
                "filter" => {
                    self.catalog_filter = value
                        .parse::<i32>()?
                        .clamp(0, self.catalog_listings.len().saturating_sub(1) as i32);
                    self.catalog_query = None;
                    self.page = 1;
                    self.fetch_catalog(false)?;
                }
                "search" => {
                    self.invalidate_catalog_work();
                    self.search_scope = self.current_source.clone();
                    self.route = "search".into();
                    let query = self.catalog_query.clone().unwrap_or_default();
                    self.open_keyboard("search", &query)?;
                }
                "load-more" => {
                    if self.catalog_has_more && !self.busy {
                        self.fetch_catalog(true)?;
                    }
                }
                "settings" => {
                    if self
                        .store
                        .source(&self.current_source)?
                        .is_some_and(|source| {
                            matches!(source.kind.as_str(), "komga" | "kavita" | "suwayomi")
                        })
                    {
                        let source = self.current_source.clone();
                        self.server_action("settings", &source)?;
                    } else {
                        self.load_source_settings()?;
                    }
                }
                "filter-menu" => self.open_catalog_filter()?,
                "filter-choice" => self.choose_catalog_filter(value)?,
                "reset-filter" => {
                    self.catalog_unread_only = false;
                    self.catalog_downloaded_only = false;
                    self.interactions
                        .catalog_filters
                        .remove(&self.current_source);
                    self.interactions.save(&self.store)?;
                    self.refresh_catalog_filter();
                }
                "apply-filter" => {
                    self.modal.clear();
                    self.modal_context.clear();
                    self.page = 1;
                }
                _ => {}
            },
            "search" => match action {
                "source-picker" => self.open_search_scope_picker()?,
                "edit" => {
                    let query = self.query.clone();
                    self.open_keyboard("search", &query)?;
                }
                "query" => {
                    self.query = value.into();
                    self.run_search()?;
                }
                "clear" => {
                    self.query.clear();
                    self.search_results.clear();
                    if self.modal == "keyboard" && self.keyboard_target == "search" {
                        self.keyboard.set_text("");
                    }
                }
                "cancel" => {
                    self.invalidate_search_work();
                    self.modal.clear();
                    self.query.clear();
                    self.search_results.clear();
                    self.route = "library".into();
                }
                "source" => self.choose_search_scope(value)?,
                "all" => {
                    if value == "library" {
                        let rows = self
                            .search_results
                            .iter()
                            .find(|group| group.id == "library")
                            .map(|group| {
                                group
                                    .books
                                    .iter()
                                    .map(|book| {
                                        (
                                            format!("open-book:{}", book.id),
                                            book.title.clone(),
                                            book.author.clone(),
                                        )
                                    })
                                    .collect()
                            })
                            .unwrap_or_default();
                        self.show_choices(&tr("library_matches"), "library-matches", rows)?;
                    } else {
                        if let Some(group) =
                            self.search_results.iter().find(|group| group.id == value)
                        {
                            self.catalog = group.books.clone();
                            self.current_source = group.id.clone();
                            self.catalog_remote_page = 1;
                            self.catalog_has_more = group.has_next;
                            self.catalog_query = Some(self.query.clone());
                            self.route = "catalog".into();
                            self.page = 1;
                        }
                    }
                }
                "retry" => self.search_one(value)?,
                "open" => {
                    self.modal.clear();
                    self.modal_context.clear();
                    self.open_book(value)?;
                }
                _ => {}
            },
            "history" => match action {
                "download" => self.toggle_history_download(value)?,
                "download-all" => self.download_history_updates()?,
                "filter" => {
                    self.history_filter = value.parse::<i32>()?.clamp(0, 1);
                    self.page = 1;
                }
                "open" => {
                    let progress = self.store.chapter_progress(value)?;
                    let chapter = self
                        .store
                        .chapter(value)?
                        .context("The history chapter is missing")?;
                    self.current_book_id = Some(chapter.book_id);
                    self.return_route = "history".into();
                    self.load_chapter(value, progress.map(|progress| progress.page).unwrap_or(1))?;
                }
                "delete" => {
                    self.store.delete_history(value)?;
                }
                "clear" => self.show_choices(
                    &tr("clear_history"),
                    "clear-history",
                    vec![
                        (
                            "clear-history-confirm".into(),
                            tr("delete_history_confirm"),
                            String::new(),
                        ),
                        ("close".into(), tr("cancel"), String::new()),
                    ],
                )?,
                "incognito" => {
                    self.prefs.incognito = !self.prefs.incognito;
                    self.save_preferences_with_render(false)?;
                }
                _ => {}
            },
            "settings" => match action {
                "open" => self.open_setting(value)?,
                "back" => {
                    self.close_page_jump();
                    self.interactions.settings_section.clear();
                    self.interactions.save(&self.store)?;
                    self.modal.clear();
                    self.modal_context.clear();
                }
                "manage-sources" => {
                    self.route = "sources".into();
                    self.source_filter = i32::from(self.platform.profile().is_scribe());
                    self.page = 1;
                    self.modal.clear();
                    self.modal_context.clear();
                }
                "detail" => match value {
                    "license" => self.open_license()?,
                    "download-path" => self.open_download_path()?,
                    value if value.starts_with("storage-book:") => {
                        self.open_storage_book(
                            value
                                .strip_prefix("storage-book:")
                                .context("The storage management target is missing")?,
                        )?;
                    }
                    value if value.starts_with("repository-remove:") => {
                        self.remove_repository(value.trim_start_matches("repository-remove:"))?;
                        self.refresh_setting_details()?;
                    }
                    "downloads" => self.open_downloads()?,
                    "clear-cache" => self.choose_panel("clear-cache")?,
                    "remove-read-downloads" => self.confirm_remove_read_downloads(false)?,
                    "add-repository" => self.open_keyboard("repository", "")?,
                    "manage-sources" => {
                        self.route = "sources".into();
                        self.source_filter = i32::from(self.platform.profile().is_scribe());
                        self.page = 1;
                    }
                    _ => {}
                },
                "detail-page" => {
                    self.close_page_jump();
                    if let Some(ui) = self.ui.upgrade() {
                        let state = ui.global::<State>();
                        state.set_settings_details_page(
                            (state.get_settings_details_page() + value.parse::<i32>()?)
                                .clamp(1, state.get_settings_details_page_count().max(1)),
                        );
                    }
                    self.refresh_setting_details()?;
                }
                "set" => {
                    let (key, value) = value
                        .split_once('=')
                        .context("Use key=value for a preference")?;
                    self.set_preference(key, value)?;
                }
                _ => {
                    if !self.category_action(action, value)?
                        && !self.data_management_action(action, value)?
                    {
                        self.tracking_action(action, value)?;
                    }
                }
            },
            "keyboard" => self.keyboard_action(action, value)?,
            "panel" => match action {
                "close" => {
                    if self.modal_context.starts_with("search-scope:") {
                        self.close_search_scope_picker();
                    } else {
                        if self.modal_context == "migration" {
                            self.migration_action("cancel", "")?;
                        }
                        if self.modal_context == "reader-recovery" {
                            self.leave_reader_recovery()?;
                        } else if self.modal_context == "reader-action-error" {
                            self.dismiss_reader_action_error();
                        } else {
                            self.modal.clear();
                            self.modal_context.clear();
                        }
                        if self.route == "reader" {
                            if let Some(reader) = &mut self.reader {
                                reader.menu_open = true;
                            }
                        }
                    }
                }
                "choose" => self.choose_panel(value)?,
                "page" => self.turn_panel_page(value.parse::<i32>()?)?,
                "adjust" => {
                    let (key, delta) = value
                        .split_once(':')
                        .context("Invalid lighting adjustment")?;
                    let delta = delta.parse::<i16>()?;
                    match key {
                        "brightness" if self.platform.profile().has_frontlight() => {
                            self.prefs.brightness =
                                (self.prefs.brightness as i16 + delta).clamp(0, 24) as u8
                        }
                        "warmth" if self.platform.profile().has_warmth() => {
                            self.prefs.warmth =
                                (self.prefs.warmth as i16 + delta).clamp(0, 24) as u8
                        }
                        _ => {}
                    }
                    self.save_preferences_with_render(false)?;
                }
                _ => {}
            },
            "local" if action == "import" => self.import_local(Path::new(value))?,
            "repository" if action == "add" => self.fetch_repository(value)?,
            "downloads" => match action {
                "pause" => self.downloads.pause(value)?,
                "resume" => self.downloads.resume(value)?,
                "cancel" => self.downloads.cancel(value)?,
                "pause-all" => self.downloads.pause_all()?,
                "resume-all" => self.downloads.resume_all()?,
                "cancel-all" => self.downloads.cancel_all()?,
                "pause-toggle" => {
                    let states = self.store.download_state_counts()?;
                    if states.get("queued").copied().unwrap_or(0)
                        + states.get("downloading").copied().unwrap_or(0)
                        > 0
                    {
                        self.downloads.pause_all()?;
                    } else {
                        self.downloads.resume_all()?;
                    }
                    self.refresh_download_rows()?;
                }
                "open" => self.open_downloads()?,
                _ => {}
            },
            "page" if action == "jump-toggle" => {
                if let Some(ui) = self.ui.upgrade() {
                    let state = ui.global::<State>();
                    let context = match value {
                        "panel" => "panel",
                        "settings-details" => "settings-details",
                        "installed-sources" => "installed-sources",
                        _ => "page",
                    };
                    let count = if context == "panel" {
                        state.get_panel_page_count()
                    } else if context == "settings-details" {
                        state.get_settings_details_page_count()
                    } else if context == "installed-sources" {
                        state.get_installed_source_page_count()
                    } else {
                        state.get_page_count()
                    };
                    if count > 1 {
                        let open = state.get_page_jump_open()
                            && state.get_page_jump_context().as_str() == context;
                        state.set_page_jump_context(context.into());
                        state.set_page_jump_open(!open);
                    }
                }
                self.platform.refresh(RefreshKind::Ui);
                return Ok(());
            }
            "page" if action == "jump-close" => {
                self.close_page_jump();
                self.platform.refresh(RefreshKind::Ui);
                return Ok(());
            }
            "page" if action == "turn" => {
                self.close_page_jump();
                if self.route == "sources"
                    && !self.platform.profile().is_scribe()
                    && self.source_filter == 0
                {
                    if let Some(ui) = self.ui.upgrade() {
                        let state = ui.global::<State>();
                        state.set_installed_source_page(
                            (state.get_installed_source_page() + value.parse::<i32>()?)
                                .clamp(1, state.get_installed_source_page_count().max(1)),
                        );
                    }
                } else {
                    let count = self
                        .ui
                        .upgrade()
                        .map(|ui| ui.global::<State>().get_page_count())
                        .unwrap_or(1)
                        .max(1) as usize;
                    self.page =
                        (self.page as i64 + value.parse::<i64>()?).clamp(1, count as i64) as usize;
                    if self.route == "catalog" {
                        if self.catalog_has_more && self.page == count && !self.busy {
                            self.fetch_catalog(true)?;
                        }
                    }
                }
            }
            "app" => match action {
                "back" => self.back()?,
                "dismiss" => {
                    self.message.clear();
                    self.message_until = None;
                }
                "left" | "right" => {
                    if !self.modal.is_empty() {
                        if matches!(self.modal.as_str(), "choices" | "downloads" | "summary") {
                            self.turn_panel_page(if action == "left" { -1 } else { 1 })?;
                        }
                    } else if self.route == "reader" {
                        let direction = self.effective_reader_preferences().reading_direction;
                        self.reader_action(
                            if action == "left" && direction == "rtl"
                                || action == "right" && direction != "rtl"
                            {
                                "next"
                            } else {
                                "previous"
                            },
                            "",
                        )?;
                    } else {
                        self.dispatch("page", "turn", if action == "left" { "-1" } else { "1" })?;
                    }
                }
                "exit" => {
                    self.platform.announce_exit_intent()?;
                    self.shutdown();
                    slint::quit_event_loop()?;
                }
                _ => {}
            },
            _ => {}
        }
        if section == "panel"
            && action == "choose"
            && value != "show-tap-zones"
            && self.modal.is_empty()
            && self.route == "reader"
        {
            if let Some(reader) = &mut self.reader {
                reader.menu_open = false;
            }
        }
        self.poll_device_policies();
        self.prepare_demo_catalog_route()?;
        self.sync_view()?;
        if !transient_picker_action {
            self.checkpoint_navigation(false)?;
        }
        self.platform.refresh(RefreshKind::Ui);
        Ok(())
    }

    fn turn_panel_page(&mut self, delta: i32) -> Result<()> {
        self.close_page_jump();
        if self.modal_context.starts_with("source-migration:") {
            return self.turn_source_migration_page(delta);
        }
        if self.modal_context == "migration" {
            return self.migration_action("page", &delta.to_string());
        }
        let count = if matches!(self.modal.as_str(), "choices" | "downloads") {
            self.choice_total.div_ceil(self.choice_page_size()).max(1)
        } else {
            self.panel_pages.len().max(1)
        };
        self.panel_page = (self.panel_page as i64 + i64::from(delta))
            .clamp(0, count.saturating_sub(1) as i64) as usize;
        if self.modal == "downloads" {
            self.refresh_download_rows()?;
        } else if self.modal == "choices" {
            self.sync_choice_rows();
        }
        Ok(())
    }

    pub(crate) fn choice_page_size(&self) -> usize {
        if self.modal_context.starts_with("search-scope:") {
            return self.search_scope_page_size();
        }
        if self.modal_context.starts_with("source-migration:") {
            return self.source_migration_page_size();
        }
        if self.modal == "downloads" {
            return 3;
        }
        match self.modal_context.as_str() {
            "reader-toc" => self.reader_toc_page_size(),
            "repository-list" => {
                if self.platform.profile().is_scribe() {
                    6
                } else {
                    4
                }
            }
            "source-picker" => {
                if self.platform.profile().is_scribe() {
                    10
                } else {
                    7
                }
            }
            "source-settings" => {
                if self.platform.profile().is_scribe() {
                    5
                } else {
                    3
                }
            }
            "migration" => 3,
            _ => 8,
        }
    }

    fn close_page_jump(&self) {
        if let Some(ui) = self.ui.upgrade() {
            ui.global::<State>().set_page_jump_open(false);
        }
    }

    pub(crate) fn cancel_sensitive_keyboard(&mut self) {
        if self.modal != "keyboard" {
            return;
        }
        let secret = self.server_keyboard_is_secret()
            || self
                .keyboard_target
                .strip_prefix("sourceopt:")
                .is_some_and(|key| {
                    self.source_settings
                        .iter()
                        .any(|setting| setting.key == key && setting.kind == "secure-text")
                });
        if secret {
            self.clear_server_keyboard();
            self.keyboard.set_text("");
            self.keyboard_target.clear();
            self.modal.clear();
            self.modal_context.clear();
        }
    }

    pub(crate) fn sync_interaction_state(&self) {
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_modal_context(self.modal_context.clone().into());
            state.set_reverse_landscape(self.prefs.reverse_landscape);
            state.set_global_reading_direction(self.prefs.reading_direction.clone().into());
            state.set_reader_default_direction(
                self.effective_reader_preferences().reading_direction.into(),
            );
            state.set_reader_panel(if self.route == "reader" && !self.modal.is_empty() {
                match self.modal_context.as_str() {
                    "reader-mode" => "mode",
                    "reader-picture" => "picture",
                    "reader-brightness" => "brightness",
                    "reader-orientation" => "orientation",
                    "reader-layout" => "layout",
                    "reader-more" => "more",
                    "reader-toc" => "toc",
                    _ => "",
                }
                .into()
            } else {
                "".into()
            });
            state.set_flash_on_close(self.prefs.flash_on_close);
            state.set_crop_borders(self.prefs.crop_borders);
            state.set_dither(self.prefs.dither);
            state.set_contrast(self.prefs.contrast / 10);
            state.set_keep_awake(self.prefs.keep_awake);
            state.set_library_sort(self.interactions.library_sort.id().into());
            state.set_source_language(self.interactions.source_language.clone().into());
            state.set_source_language_index(match self.interactions.source_language.as_str() {
                "zh-Hans" => 1,
                "zh-Hant" => 2,
                "en" => 3,
                "ja" => 4,
                _ => 0,
            });
            state.set_library_ascending(self.interactions.library_ascending);
            state.set_catalog_active_filter_count(
                self.interactions
                    .catalog_filters
                    .get(&self.current_source)
                    .map(|filter| {
                        usize::from(filter.sort != "relevance")
                            + filter.statuses.len()
                            + filter.tags.values().filter(|value| **value != 0).count()
                    })
                    .unwrap_or(0)
                    .saturating_add(usize::from(self.catalog_unread_only))
                    .saturating_add(usize::from(self.catalog_downloaded_only))
                    .min(i32::MAX as usize) as i32,
            );
            state.set_page_layout_mode(self.interactions.page_layout_mode.clone().into());
            state.set_remember_book_direction(self.interactions.remember_book_direction);
            state.set_library_filters(model(
                self.interactions
                    .library_filters
                    .iter()
                    .map(|value| i32::from(*value)),
            ));
            state.set_reader_toc_tab(self.interactions.reader_toc_tab.clone().into());
            state.set_show_tap_zones(self.interactions.show_tap_zones);
            state.set_settings_section(
                if self.platform.profile().is_scribe()
                    && self.interactions.settings_section.is_empty()
                {
                    "screen-refresh".into()
                } else {
                    self.interactions.settings_section.clone().into()
                },
            );
            let current = self
                .displayed_reader
                .as_ref()
                .map(|frame| frame.session.chapter_id.as_str())
                .or_else(|| {
                    self.reader
                        .as_ref()
                        .map(|reader| reader.current_chapter().id.as_str())
                });
            let adjacent = self.reader.as_ref().and_then(|reader| {
                let chapters = reader.chapters();
                let index = chapters
                    .iter()
                    .position(|chapter| Some(chapter.id.as_str()) == current)?;
                Some((
                    chapters
                        .get(index + 1)
                        .map(|chapter| chapter.number.to_string()),
                    index
                        .checked_sub(1)
                        .and_then(|previous| chapters.get(previous))
                        .map(|chapter| chapter.number.to_string()),
                ))
            });
            let (next, previous) = adjacent.unwrap_or_default();
            state.set_reader_has_next_chapter(next.is_some());
            state.set_reader_has_previous_chapter(previous.is_some());
            state.set_reader_next_chapter_number(next.unwrap_or_default().into());
            state.set_reader_previous_chapter_number(previous.unwrap_or_default().into());
        }
    }

    fn set_library_sort(&mut self, value: &str) -> Result<()> {
        self.interactions.library_sort =
            interaction_models::LibrarySort::parse(value).context("Unknown library sort order")?;
        self.sort_title = self.interactions.library_sort == interaction_models::LibrarySort::Title;
        self.interactions.save(&self.store)?;
        self.page = 1;
        Ok(())
    }

    fn cycle_library_filter(&mut self, value: &str) -> Result<()> {
        let index = match value {
            "downloaded" => 0,
            "unread" => 1,
            "tracked" => 2,
            "completed" => 3,
            _ => bail!("Unknown library filter"),
        };
        let filter = &mut self.interactions.library_filters[index];
        *filter = match *filter {
            0 => 1,
            1 => -1,
            _ => 0,
        };
        self.downloaded_only = self.interactions.library_filters[0] == 1;
        self.unread_only = self.interactions.library_filters[1] == 1;
        self.interactions.save(&self.store)?;
        self.page = 1;
        Ok(())
    }

    fn select_visible_books(&mut self) {
        self.selection_mode = true;
        if let Some(ui) = self.ui.upgrade() {
            let books = ui.global::<State>().get_books();
            for index in 0..books.row_count() {
                if let Some(book) = books.row_data(index) {
                    self.selected.insert(book.id.to_string());
                }
            }
        }
    }

    fn mark_selected_books_read(&mut self) -> Result<()> {
        let ids: Vec<_> = self.selected.iter().cloned().collect();
        if ids.is_empty() {
            return Ok(());
        }
        let database = self.data_dir.join("library.sqlite3");
        self.spawn_local_job(true, JobKind::General, move || {
            let store = Store::open_existing_for_write(database)?;
            store.mark_books_read(&ids, true)?;
            Ok(JobResult::InteractionDone(tr("marked_read")))
        })?;
        self.selected.clear();
        self.selection_mode = false;
        Ok(())
    }

    fn download_selected_books(&mut self) -> Result<()> {
        if self.options.offline && !self.options.demo {
            bail!("{}", tr("offline_help"));
        }
        for id in &self.selected {
            let Some(book) = self.store.book(id)? else {
                continue;
            };
            let chapters: Vec<_> = self
                .store
                .chapters(id)?
                .into_iter()
                .filter(|chapter| !chapter.read && !chapter.downloaded)
                .collect();
            if !chapters.is_empty() {
                self.downloads.enqueue_many(&book, &chapters)?;
            }
        }
        self.selected.clear();
        self.selection_mode = false;
        self.open_downloads()
    }

    fn remove_selected_books(&mut self) -> Result<()> {
        for id in &self.selected {
            self.store.set_library(id, false)?;
        }
        self.selected.clear();
        self.selection_mode = false;
        self.modal.clear();
        Ok(())
    }

    fn confirm_remove_chapter_download(&mut self, id: &str) -> Result<()> {
        let chapter = self
            .store
            .chapter(id)?
            .context("The selected chapter is missing")?;
        if self.route == "detail" && chapter.book_id != self.current_book()?.id {
            bail!("The chapter belongs to another manga");
        }
        self.show_choices(
            &tr("remove_download"),
            &format!("chapter-download:{id}"),
            vec![
                (
                    "chapter-remove-confirm".into(),
                    tr("remove_download_confirm"),
                    String::new(),
                ),
                ("close".into(), tr("cancel"), String::new()),
            ],
        )
    }

    fn toggle_history_download(&mut self, id: &str) -> Result<()> {
        match self.store.download(id)?.map(|job| job.state).as_deref() {
            Some("completed") => self.confirm_remove_chapter_download(id),
            Some("queued" | "downloading" | "paused") => self.downloads.cancel(id),
            _ => self.enqueue_chapter(id),
        }
    }

    fn download_history_updates(&mut self) -> Result<()> {
        if self.options.offline && !self.options.demo {
            bail!("{}", tr("offline_help"));
        }
        let day = if self.options.demo {
            crate::demo::fixture_date()
        } else {
            chrono::Local::now().date_naive()
        }
        .format("%Y-%m-%d")
        .to_string();
        let mut page = 1;
        loop {
            let visible =
                self.store
                    .daily_updates_view_page(&day, self.options.demo, false, page, 100)?;
            let page_count = visible.total.div_ceil(100).max(1);
            let mut books: std::collections::BTreeMap<String, (Book, Vec<Chapter>)> =
                Default::default();
            for (book, chapter) in visible.items {
                if !chapter.downloaded {
                    books
                        .entry(book.id.clone())
                        .or_insert_with(|| (book, Vec::new()))
                        .1
                        .push(chapter);
                }
            }
            for (book, chapters) in books.into_values() {
                self.downloads.enqueue_many(&book, &chapters)?;
            }
            if page >= page_count {
                break;
            }
            page += 1;
        }
        self.open_downloads()
    }

    fn confirm_remove_read_downloads(&mut self, current_book: bool) -> Result<()> {
        let book_id = current_book
            .then(|| self.current_book())
            .transpose()?
            .map(|book| book.id);
        let count = self
            .store
            .completed_read_download_ids(book_id.as_deref())?
            .len();
        let context = book_id
            .map(|id| format!("remove-read-downloads:book:{id}"))
            .unwrap_or_else(|| "remove-read-downloads:library".into());
        self.show_choices(
            &tr("remove_read_downloads"),
            &context,
            vec![
                (
                    "remove-read-downloads-confirm".into(),
                    tr("remove_read_downloads"),
                    format!("{count} {}", tr("chapters_unit")),
                ),
                ("close".into(), tr("cancel"), String::new()),
            ],
        )
    }

    fn confirm_remove_book_downloads(&mut self) -> Result<()> {
        let book = self.current_book()?;
        let count = self.store.book_download_count(&book.id)?;
        self.show_choices(
            &tr("delete_all_downloads"),
            &format!("book-downloads:{}", book.id),
            vec![
                (
                    "book-downloads-remove-confirm".into(),
                    tr("book_downloads_remove_confirm").replace("{count}", &count.to_string()),
                    String::new(),
                ),
                ("close".into(), tr("cancel"), String::new()),
            ],
        )?;
        if count == 0 {
            self.choice_disabled
                .insert("book-downloads-remove-confirm".into());
            self.sync_choice_rows();
        }
        Ok(())
    }

    fn open_storage_book(&mut self, id: &str) -> Result<()> {
        let book = self
            .store
            .book(id)?
            .context("The stored manga is missing")?;
        self.clear_reader_recovery();
        self.invalidate_reader_work();
        self.modal.clear();
        self.modal_context.clear();
        self.close_page_jump();
        self.selection_mode = false;
        self.selected.clear();
        self.selected_chapters.clear();
        self.current_book_id = Some(book.id);
        self.return_route = "settings".into();
        self.route = "detail".into();
        self.page = 1;
        Ok(())
    }

    fn enqueue_custom_range(&mut self, value: &str) -> Result<()> {
        if self.options.offline && !self.options.demo {
            bail!("{}", tr("offline_help"));
        }
        let normalized = value.replace(['–', '—', '~', '〜'], "-");
        let (start, end) = normalized
            .split_once('-')
            .context(tr("chapter_range_format"))?;
        let start: f64 = start.trim().parse().context(tr("chapter_range_format"))?;
        let end: f64 = end.trim().parse().context(tr("chapter_range_format"))?;
        if !start.is_finite() || !end.is_finite() || start < 0.0 || start > end {
            bail!("{}", tr("chapter_range_format"));
        }
        let book = self.current_book()?;
        let mut chapters: Vec<_> = self
            .store
            .chapters(&book.id)?
            .into_iter()
            .filter(|chapter| {
                chapter.number >= start && chapter.number <= end && !chapter.downloaded
            })
            .collect();
        chapters.sort_by(|first, second| first.number.total_cmp(&second.number));
        if chapters.is_empty() {
            self.notify(tr("no_downloads_needed"));
            return Ok(());
        }
        self.downloads.enqueue_many(&book, &chapters)?;
        self.open_downloads()
    }

    fn open_book(&mut self, id: &str) -> Result<()> {
        if self.selection_mode {
            if !self.selected.insert(id.into()) {
                self.selected.remove(id);
            }
            return Ok(());
        }
        self.invalidate_reader_work();
        self.clear_reader_recovery();
        self.selected_chapters.clear();
        let book = self
            .store
            .book(id)?
            .context("The selected manga is missing")?;
        self.return_route = self.route.clone();
        self.current_book_id = Some(id.into());
        if !self.prefs.incognito {
            self.store
                .record_book_open(id, chrono::Utc::now().timestamp())?;
        }
        self.page = 1;
        self.route = "detail".into();
        if !self.options.demo && !self.options.offline && book.source_id != "local" {
            self.spawn_job(true, move |manager| {
                let details = manager.details(&book)?;
                let chapters = manager.chapters(&details)?;
                Ok(JobResult::Detail {
                    book: details,
                    chapters,
                })
            })?;
        }
        Ok(())
    }

    pub(crate) fn resume_reader(&mut self) -> Result<()> {
        let book = self.current_book()?;
        if let Some(progress) = self.store.progress(&book.id)? {
            return self.load_chapter(&progress.chapter_id, progress.page);
        }
        let chapter = self
            .store
            .first_unread_chapter_or_first(&book.id)?
            .context(tr("chapters_empty"))?;
        self.load_chapter(&chapter.id, 1)
    }

    pub(crate) fn load_chapter(&mut self, id: &str, page: u32) -> Result<()> {
        self.pending_wide_navigation = 0;
        self.load_reader_target(ReaderTarget::new(id, page, 0, 1.0))
    }

    pub(super) fn load_reader_target(&mut self, target: ReaderTarget) -> Result<()> {
        let result = self.begin_reader_target(&target);
        if let Err(error) = result {
            self.recover_reader_failure(target, error);
        }
        Ok(())
    }

    fn begin_reader_target(&mut self, target: &ReaderTarget) -> Result<()> {
        self.invalidate_reader_work();
        let chapter = self
            .store
            .chapter(&target.chapter_id)?
            .context("This chapter is missing")?;
        let retrying = self
            .reader_recovery
            .as_ref()
            .is_some_and(|recovery| recovery.retrying);
        if !self.options.offline
            && !self.options.demo
            && chapter.source_id != "local"
            && (!chapter.downloaded || retrying)
            && self.platform.network_policy_blocked()
        {
            self.request_user_network(UserNetworkIntent::Reader(target.clone()));
            return Ok(());
        }
        if self.store.has_pages(&target.chapter_id)? {
            return self.begin_reader_image(target);
        }
        if self.options.offline {
            bail!("{}", tr("chapter_unavailable"));
        }
        let book = self
            .store
            .book(&chapter.book_id)?
            .context("This manga is missing")?;
        let page = target.page;
        self.spawn_chapter_job(target.clone(), move |manager| {
            let pages = manager.pages(&book, &chapter)?;
            Ok(JobResult::Pages {
                chapter_id: chapter.id,
                page,
                pages,
            })
        })
    }

    pub(crate) fn enter_reader(&mut self, chapter_id: &str, page: u32) -> Result<()> {
        self.enter_reader_at(chapter_id, page, 0, 1.0)
    }

    pub(crate) fn enter_reader_at(
        &mut self,
        chapter_id: &str,
        page: u32,
        vertical_offset: u32,
        zoom: f32,
    ) -> Result<()> {
        let target = ReaderTarget::new(chapter_id, page, vertical_offset, zoom);
        self.enter_reader_target(target)
    }

    pub(super) fn enter_reader_target(&mut self, target: ReaderTarget) -> Result<()> {
        if let Err(error) = self.begin_reader_image(&target) {
            self.recover_reader_failure(target, error);
        }
        Ok(())
    }

    fn begin_reader_image(&mut self, target: &ReaderTarget) -> Result<()> {
        let chapter_id = &target.chapter_id;
        let chapter = self
            .store
            .chapter(chapter_id)?
            .context("The requested chapter is missing")?;
        let pages = self.store.page_window(chapter_id, 0, 1)?;
        if pages.total == 0 {
            bail!("{}", tr("chapters_empty"));
        }
        self.current_book_id = Some(chapter.book_id.clone());
        if self
            .displayed_reader
            .as_ref()
            .is_some_and(|frame| frame.session.book_id != chapter.book_id)
        {
            self.displayed_reader = None;
            self.displayed_image_leases.clear();
            if let Some(ui) = self.ui.upgrade() {
                ui.global::<State>().set_reader_image(Image::default());
                ui.global::<State>().set_reader_image_next(Image::default());
            }
        }
        let mut reader = ReaderState::new(
            self.store.chapters(&chapter.book_id)?,
            chapter_id,
            target.page,
            &self.effective_reader_preferences(),
        )?;
        reader.set_total(pages.total as u32);
        reader.page = target.page.clamp(1, reader.total.max(1));
        reader.vertical_offset = target.vertical_offset;
        reader.zoom = target.zoom.clamp(1.0, 4.0);
        reader.wide_half = target.wide_half.min(1);
        reader.menu_open = false;
        self.reader = Some(reader);
        self.route = "reader".into();
        if !self
            .reader_recovery
            .as_ref()
            .is_some_and(|recovery| recovery.retrying)
        {
            self.modal.clear();
        }
        self.request_reader_images()?;
        Ok(())
    }

    fn reader_action(&mut self, action: &str, value: &str) -> Result<()> {
        if matches!(
            action,
            "seek" | "next-chapter" | "previous-chapter" | "back"
        ) {
            self.pending_wide_navigation = 0;
        }
        if action == "toc-tab" {
            if !matches!(value, "chapters" | "bookmarks") {
                bail!("Unknown reader table-of-contents tab");
            }
            self.interactions.reader_toc_tab = value.into();
            self.interactions.save(&self.store)?;
            return self.open_reader_toc();
        }
        if action == "tap-zones" {
            self.interactions.show_tap_zones = !self.interactions.show_tap_zones;
            self.interactions.save(&self.store)?;
            self.modal.clear();
            self.modal_context.clear();
            if let Some(reader) = self.reader.as_mut() {
                reader.menu_open = true;
            }
            return Ok(());
        }
        if action == "back" {
            self.clear_reader_recovery();
            self.invalidate_reader_work();
            self.displayed_image_leases.clear();
            self.save_reader_progress()?;
            self.route = "detail".into();
            self.modal.clear();
            self.platform.refresh(RefreshKind::Full);
            return Ok(());
        }
        if matches!(
            action,
            "mode"
                | "picture"
                | "brightness"
                | "orientation"
                | "layout"
                | "more"
                | "toc"
                | "page-actions"
        ) {
            return self.open_reader_panel(action);
        }
        if action == "bookmark" {
            if self
                .ui
                .upgrade()
                .is_some_and(|ui| !ui.global::<State>().get_reader_image_ready())
            {
                bail!("{}", tr("bookmark_page_not_ready"));
            }
            let session = self.displayed_reader_session()?;
            self.store.toggle_bookmark(&Bookmark {
                book_id: session.book_id,
                chapter_id: session.chapter_id,
                page: session.page,
                created_at: chrono::Utc::now().timestamp(),
            })?;
            if self.modal_context == "reader-toc" && self.interactions.reader_toc_tab == "bookmarks"
            {
                self.open_reader_toc()?;
            }
            return Ok(());
        }
        let delta = match action {
            "next" => Some(1),
            "previous" => Some(-1),
            "tap" => {
                let (x, y) = value
                    .split_once(',')
                    .context("Use normalized x,y coordinates")?;
                let (x, y) = (x.parse::<f32>()?, y.parse::<f32>()?);
                self.reader
                    .as_ref()
                    .and_then(|reader| reader.tap_page_delta(x, y))
            }
            _ => None,
        };
        if delta.is_some_and(|delta| self.defer_wide_navigation(delta)) {
            return Ok(());
        }
        let Some(reader) = self.reader.as_mut() else {
            return Ok(());
        };
        let update = match action {
            "menu" => reader.toggle_menu(),
            "next" if !reader.menu_open => reader.next_page(),
            "previous" if !reader.menu_open => reader.previous_page(),
            "next-chapter" => {
                reader.menu_open = false;
                reader.next_chapter()
            }
            "previous-chapter" => {
                reader.menu_open = false;
                reader.previous_chapter()
            }
            "seek" => {
                reader.menu_open = false;
                reader.jump_to_page(value.parse::<u32>()?)
            }
            "footer" => {
                reader.footer_mode = (reader.footer_mode + 1) % 4;
                self.prefs.footer_mode = reader.footer_mode;
                self.store.save_preferences(&self.prefs)?;
                return Ok(());
            }
            "zoom" => reader.set_zoom(if reader.zoom > 1.0 { 1.0 } else { 2.0 }),
            "tap" => {
                let (x, y) = value
                    .split_once(',')
                    .context("Use normalized x,y coordinates")?;
                reader.tap(x.parse()?, y.parse()?)
            }
            _ => return Ok(()),
        };
        self.apply_reader_update(update)
    }

    /// Unknown source geometry cannot determine whether a turn means the next
    /// half or the next source page. Keep a bounded signed intent until the
    /// current frame establishes that geometry.
    fn defer_wide_navigation(&mut self, delta: i8) -> bool {
        if !self.prefs.split_wide {
            return false;
        }
        let preferences = self.effective_reader_preferences();
        if !preferences.split_wide
            || preferences.spread
            || preferences.reading_direction == "vertical"
            || self.reader_recovery.is_some()
            || (self.reader_inflight.is_none()
                && !self.reader_dirty
                && self.chapter_inflight.is_none())
        {
            return false;
        }
        if self.reader.as_ref().is_none_or(|reader| reader.menu_open) {
            return false;
        }
        self.pending_wide_navigation = self
            .pending_wide_navigation
            .saturating_add(delta)
            .clamp(-16, 16);
        true
    }

    fn take_pending_wide_update(&mut self) -> Option<ReaderUpdate> {
        if self.pending_wide_navigation == 0 {
            return None;
        }
        let reader = self.reader.as_mut()?;
        let direction = self.pending_wide_navigation.signum();
        self.pending_wide_navigation -= direction;
        let update = if direction > 0 {
            reader.next_page()
        } else {
            reader.previous_page()
        };
        if update.action == crate::reader::ReaderAction::None {
            self.pending_wide_navigation = 0;
        }
        Some(update)
    }

    pub(super) fn replay_pending_wide_navigation(&mut self) -> Result<bool> {
        if !self.modal.is_empty() || self.reader.as_ref().is_some_and(|reader| reader.menu_open) {
            self.pending_wide_navigation = 0;
            return Ok(false);
        }
        let Some(update) = self.take_pending_wide_update() else {
            return Ok(false);
        };
        let changed = matches!(
            update.action,
            crate::reader::ReaderAction::PageChanged | crate::reader::ReaderAction::ChapterChanged
        );
        self.apply_reader_update(update)?;
        Ok(changed)
    }

    fn back(&mut self) -> Result<()> {
        if self.modal_context.starts_with("search-scope:") {
            self.close_search_scope_picker();
            return Ok(());
        }
        if self.modal_context == "migration" {
            return self.migration_action("cancel", "");
        }
        if self.modal_context == "source-settings-loading" {
            self.modal_context.clear();
            return Ok(());
        }
        if self
            .ui
            .upgrade()
            .is_some_and(|ui| ui.global::<State>().get_page_jump_open())
        {
            self.close_page_jump();
            return Ok(());
        }
        if self.modal_context == "reader-recovery" {
            return self.leave_reader_recovery();
        }
        if self.modal_context == "reader-action-error" {
            self.dismiss_reader_action_error();
            return Ok(());
        }
        if self.modal == "keyboard" {
            return self.keyboard_action("cancel", "");
        }
        if !self.modal.is_empty() {
            self.modal.clear();
            self.modal_context.clear();
            if self.route == "reader" {
                if let Some(reader) = &mut self.reader {
                    reader.menu_open = true;
                }
            }
            return Ok(());
        }
        if self.route == "settings"
            && !self.platform.profile().is_scribe()
            && !self.interactions.settings_section.is_empty()
        {
            self.interactions.settings_section.clear();
            self.interactions.save(&self.store)?;
            return Ok(());
        }
        if self.route == "reader" {
            if self.reader.as_ref().is_some_and(|reader| reader.menu_open) {
                return self.reader_action("menu", "");
            }
            return self.reader_action("back", "");
        }
        self.invalidate_reader_work();
        self.route = match self.route.as_str() {
            "detail" => self.return_route.clone(),
            "catalog" => "sources".into(),
            _ => "library".into(),
        };
        self.page = 1;
        self.selection_mode = false;
        Ok(())
    }

    fn reload_detail(&mut self) -> Result<()> {
        let book = self.current_book()?;
        if self.options.demo || self.options.offline || book.source_id == "local" {
            self.notify(tr("saved"));
            return Ok(());
        }
        self.spawn_job(true, move |manager| {
            let book = manager.details(&book)?;
            let chapters = manager.chapters(&book)?;
            Ok(JobResult::Detail { book, chapters })
        })
    }

    fn open_source(&mut self, id: &str) -> Result<()> {
        let source = self
            .store
            .source(id)?
            .context("This source is not installed")?;
        if !source.enabled {
            self.current_source = id.into();
            self.route = "catalog".into();
            self.catalog.clear();
            self.catalog_has_more = false;
            self.modal.clear();
            self.modal_context.clear();
            self.page = 1;
            return Ok(());
        }
        if !self.options.demo
            && matches!(source.kind.as_str(), "komga" | "kavita" | "suwayomi")
            && source.base_url.is_empty()
        {
            self.current_source = id.into();
            if self.platform.profile().is_scribe() {
                self.route = "catalog".into();
                self.catalog.clear();
                self.catalog_has_more = false;
                self.modal.clear();
                self.modal_context.clear();
                self.page = 1;
                return Ok(());
            }
            return self.open_pairing();
        }
        self.current_source = id.into();
        self.catalog_listings = remembered_listings(&source, self.options.demo);
        self.page = 1;
        self.catalog_filter = 0;
        self.catalog_query = None;
        self.route = "catalog".into();
        self.modal.clear();
        self.modal_context.clear();
        self.fetch_catalog(false)
    }

    /// Delay demo writes until Browse is visible, preserving restored search results.
    pub(super) fn prepare_demo_catalog_route(&mut self) -> Result<()> {
        if !self.options.demo
            || self.route != "catalog"
            || !self.catalog.is_empty()
            || self.catalog_query.is_some()
        {
            return Ok(());
        }
        if self
            .store
            .source(&self.current_source)?
            .is_some_and(|source| source.enabled)
        {
            self.fetch_catalog(false)?;
        }
        Ok(())
    }

    fn fetch_catalog(&mut self, append: bool) -> Result<()> {
        self.invalidate_catalog_work();
        if self.options.offline || self.options.demo || self.current_source == "local" {
            if let Some(config) = self.store.source(&self.current_source)? {
                self.catalog_listings = remembered_listings(&config, self.options.demo);
            }
        }
        if self.options.demo {
            self.catalog = crate::demo::catalog(
                &self.store,
                &self.data_dir,
                self.platform.profile().is_scribe(),
            )?;
            self.catalog_has_more = false;
            return Ok(());
        }
        if self.current_source == "local" {
            self.catalog = self.store.books_for_source("local")?;
            self.catalog_has_more = false;
            return Ok(());
        }
        if self.options.offline {
            self.catalog = self.store.books_for_source(&self.current_source)?;
            self.catalog_has_more = false;
            return Ok(());
        }
        let source = self.current_source.clone();
        let listing_index = self.catalog_filter.max(0) as usize;
        let query = self.catalog_query.clone();
        let remote_page = if append {
            self.catalog_remote_page + 1
        } else {
            self.catalog.clear();
            self.catalog_remote_page = 0;
            self.catalog_has_more = true;
            self.page = 1;
            1
        };
        self.spawn_catalog_job(move |manager| {
            let listings = manager.listings(&source)?;
            let sort = listings
                .get(listing_index)
                .or_else(|| listings.first())
                .map(|listing| listing.0.as_str())
                .unwrap_or("popular");
            let result = if let Some(query) = query {
                manager.search_page(&source, &query, remote_page)?
            } else {
                manager.browse_page(&source, remote_page, sort)?
            };
            Ok(JobResult::Catalog {
                source,
                books: result.books,
                has_next: result.has_next,
                remote_page,
                listings,
            })
        })
    }

    pub(crate) fn run_search(&mut self) -> Result<()> {
        self.invalidate_search_work();
        self.page = 1;
        self.search_results.clear();
        if self.query.trim().is_empty() {
            return Ok(());
        }
        if self.search_scope == "library" {
            self.search_results.push(SearchResult {
                id: "library".into(),
                title: tr("library_matches"),
                books: self.store.library_search_matches(&self.query, 12)?,
                error: None,
                pending: false,
                has_next: false,
            });
            return Ok(());
        }
        if self.options.demo {
            self.search_results = crate::demo::search(&self.store, &self.data_dir, &self.query)?
                .into_iter()
                .filter(|group| self.search_scope == "all" || group.id == self.search_scope)
                .map(|group| SearchResult {
                    id: group.id,
                    title: group.title,
                    books: group.books,
                    error: group.error,
                    pending: false,
                    has_next: false,
                })
                .collect();
            return Ok(());
        }
        let scope = self.search_scope.clone();
        let configs = self.store.sources()?;
        for source in configs
            .into_iter()
            .filter(|source| source.enabled && (scope == "all" || source.id == scope))
        {
            if source.kind == "local" || self.options.offline {
                let books = self
                    .store
                    .source_search_matches(&self.query, &source.id, 12)?;
                self.search_results.push(SearchResult {
                    id: source.id,
                    title: source.name,
                    books,
                    error: None,
                    pending: false,
                    has_next: false,
                });
            } else if source.kind == "mangadex"
                || source.kind == "wasm"
                || !source.base_url.is_empty()
            {
                self.search_one(&source.id)?;
            }
        }
        Ok(())
    }

    fn search_one(&mut self, id: &str) -> Result<()> {
        if self.options.demo {
            return self.run_search();
        }
        let source = self
            .store
            .source(id)?
            .context("The search source is not installed")?;
        let query = self.query.clone();
        if let Some(result) = self
            .search_results
            .iter_mut()
            .find(|result| result.id == source.id)
        {
            if result.pending {
                return Ok(());
            }
            result.pending = true;
        } else {
            self.search_results.push(SearchResult {
                id: source.id.clone(),
                title: source.name.clone(),
                books: Vec::new(),
                error: None,
                pending: true,
                has_next: false,
            });
        }
        self.spawn_search_job(source.id.clone(), move |manager| {
            let (books, has_next, error) = match manager.search_page(&source.id, &query, 1) {
                Ok(result) => (result.books, result.has_next, None),
                Err(error) => (Vec::new(), false, Some(format!("{error:#}"))),
            };
            Ok(JobResult::Search(SearchResult {
                id: source.id,
                title: source.name,
                books,
                error,
                pending: false,
                has_next,
            }))
        })
    }

    pub(crate) fn import_local(&mut self, path: &Path) -> Result<()> {
        let path = path.to_path_buf();
        let root = self.data_dir.clone();
        self.spawn_job(true, move |_| {
            let library = LocalLibrary::new(root.join("local"))?;
            Ok(JobResult::Imported(library.scan(path)?))
        })
    }

    pub(crate) fn fetch_repository(&mut self, url: &str) -> Result<()> {
        if self.options.offline {
            bail!("{}", tr("offline_help"));
        }
        let url = url.to_owned();
        self.spawn_job(true, move |manager| {
            Ok(JobResult::Repository(manager.fetch_repository(&url)?))
        })
    }

    pub(crate) fn record_repository(
        &mut self,
        repository: &crate::sources::Repository,
    ) -> Result<()> {
        if let Some(record) = self
            .interactions
            .repositories
            .iter_mut()
            .find(|record| record.url == repository.url)
        {
            record.name = repository.name.clone();
        } else {
            self.interactions
                .repositories
                .push(interaction_models::RepositoryRecord {
                    name: repository.name.clone(),
                    url: repository.url.clone(),
                });
        }
        self.interactions.active_repository_url = repository.url.clone();
        self.interactions.save(&self.store)?;
        if self.route == "settings" && self.interactions.settings_section == "source-lists" {
            self.refresh_setting_details()?;
        }
        Ok(())
    }

    fn remove_repository(&mut self, url: &str) -> Result<()> {
        self.interactions
            .repositories
            .retain(|repository| repository.url != url);
        if self.interactions.active_repository_url == url {
            self.interactions.active_repository_url.clear();
            self.available.clear();
        }
        self.interactions.save(&self.store)
    }

    fn open_repository_list(&mut self) -> Result<()> {
        let rows: Vec<_> = self
            .interactions
            .repositories
            .iter()
            .map(|repository| {
                (
                    format!("repository-remove:{}", repository.url),
                    repository.name.clone(),
                    repository.url.clone(),
                )
            })
            .collect();
        self.show_choices(&tr("source_list"), "repository-list", rows)
    }

    pub(crate) fn refresh_setting_details(&self) -> Result<()> {
        if self.route != "settings" {
            return Ok(());
        }
        let item = |id: &str, title: &str, value: String, enabled: bool| SettingItem {
            id: id.into(),
            title: tr(title).into(),
            value: value.into(),
            group: "".into(),
            enabled,
        };
        let (rows, status) = match self.interactions.settings_section.as_str() {
            "storage" => {
                let cache = self.disk_cache.stats();
                (
                    vec![
                        item(
                            "downloads",
                            "downloaded_chapters",
                            self.cached_download_bytes
                                .map(readable_bytes)
                                .unwrap_or_else(|| "—".into()),
                            true,
                        ),
                        item("", "cache_usage", readable_bytes(cache.bytes), false),
                        item(
                            "",
                            "free",
                            self.ui
                                .upgrade()
                                .map(|ui| ui.global::<State>().get_free_space().to_string())
                                .unwrap_or_else(|| "—".into()),
                            false,
                        ),
                    ],
                    String::new(),
                )
            }
            "categories" => (self.category_setting_items()?, String::new()),
            "source-lists" => (
                self.interactions
                    .repositories
                    .iter()
                    .map(|repository| SettingItem {
                        id: format!("repository-remove:{}", repository.url).into(),
                        title: repository.name.clone().into(),
                        value: repository.url.clone().into(),
                        group: "repository".into(),
                        enabled: true,
                    })
                    .collect(),
                String::new(),
            ),
            "about" => (
                vec![
                    item("", "version", env!("CARGO_PKG_VERSION").into(), false),
                    item(
                        "",
                        "device_label",
                        self.platform.profile().name().into(),
                        false,
                    ),
                    item("license", "license", "GPL-3.0-or-later".into(), true),
                    item("", "project_home", "—".into(), false),
                ],
                String::new(),
            ),
            "downloads" => (
                vec![item(
                    "",
                    "destination",
                    self.data_dir.to_string_lossy().into_owned(),
                    false,
                )],
                String::new(),
            ),
            "backup" | "logs" => self
                .data_management_setting_items(self.interactions.settings_section.as_str())
                .unwrap_or_default(),
            "tracking" | "auto-update" => (Vec::new(), String::new()),
            "pen" => (Vec::new(), tr("unavailable")),
            _ => (Vec::new(), String::new()),
        };
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            let page_size = if self.platform.profile().is_scribe() {
                8
            } else {
                6
            };
            let page_count = rows.len().div_ceil(page_size).max(1);
            let page = (state.get_settings_details_page() as usize).clamp(1, page_count);
            state.set_settings_details_page(page as i32);
            state.set_settings_details_page_count(page_count as i32);
            state.set_settings_details(model(
                rows.into_iter()
                    .skip((page - 1) * page_size)
                    .take(page_size),
            ));
            state.set_settings_status(status.into());
        }
        Ok(())
    }

    fn install_source(&mut self, id: &str) -> Result<()> {
        let source = self
            .available
            .iter()
            .find(|source| source.id == id)
            .cloned()
            .context("Refresh the repository before installing a source")?;
        if self.options.demo {
            let mut config = self.store.source(id)?.unwrap_or_else(|| SourceConfig {
                id: source.id.clone(),
                name: source.name.clone(),
                kind: "demo".into(),
                enabled: true,
                languages: source.languages.clone(),
                ..SourceConfig::default()
            });
            config.version = source.version;
            config.options.insert("demo".into(), Value::Bool(true));
            config
                .options
                .insert("update_available".into(), Value::Bool(false));
            self.store.save_source(&config)?;
            self.source_images_changed();
            self.source_filter = 0;
            self.page = 1;
            self.notify(tr("saved"));
            return Ok(());
        }
        self.spawn_job(true, move |manager| {
            Ok(JobResult::Installed(
                manager.install_repository_source(&source)?,
            ))
        })
    }

    fn update_all_sources(&mut self) -> Result<()> {
        let installed = self.store.sources()?;
        let updates: Vec<_> = self
            .available
            .iter()
            .filter(|available| {
                installed
                    .iter()
                    .any(|source| source.id == available.id && source.version < available.version)
            })
            .cloned()
            .collect();
        if updates.is_empty() {
            self.notify(tr("no_updates"));
            return Ok(());
        }
        if self.options.demo {
            for source in updates {
                self.install_source(&source.id)?;
            }
            return Ok(());
        }
        self.spawn_job(true, move |manager| {
            let mut installed = Vec::new();
            let mut errors = Vec::new();
            for source in updates {
                match manager.install_repository_source(&source) {
                    Ok(config) => installed.push(config),
                    Err(error) => errors.push(format!("{}: {error:#}", source.name)),
                }
            }
            Ok(JobResult::SourcesInstalled { installed, errors })
        })
    }

    pub(crate) fn load_source_settings(&mut self) -> Result<()> {
        let source = self.current_source.clone();
        self.modal.clear();
        self.modal_context = "source-settings-loading".into();
        let result = self.spawn_job(true, move |manager| {
            Ok(JobResult::Settings(manager.source_settings(&source)?))
        });
        if result.is_err() {
            self.modal_context.clear();
        }
        result
    }

    pub(crate) fn open_source_settings(&mut self) -> Result<()> {
        let rows = self
            .source_settings
            .iter()
            .map(|setting| {
                (
                    format!("sourceopt:{}", setting.key),
                    setting.title.clone(),
                    if setting.kind == "secure-text"
                        && setting
                            .value
                            .as_str()
                            .is_some_and(|value| !value.is_empty())
                    {
                        "••••••••".into()
                    } else {
                        display_value(&setting.value)
                    },
                )
            })
            .collect();
        self.show_choices(&tr("source_preferences"), "source-settings", rows)
    }

    fn enqueue_chapter(&mut self, id: &str) -> Result<()> {
        if self.options.offline && !self.options.demo {
            bail!("{}", tr("offline_help"));
        }
        let chapter = self
            .store
            .chapter(id)?
            .context("The download chapter is missing")?;
        let book = self
            .store
            .book(&chapter.book_id)?
            .context("The download manga is missing")?;
        self.downloads.enqueue(&book, &chapter)?;
        Ok(())
    }

    fn download_next_five(&mut self) -> Result<()> {
        self.enqueue_batch("next-five")
    }

    fn open_batch_downloads(&mut self) -> Result<()> {
        self.show_choices(
            &tr("batch_download"),
            "batch-download",
            vec![
                (
                    "batch:next-five".into(),
                    tr("download_next_five"),
                    String::new(),
                ),
                ("batch:unread".into(), tr("download_unread"), String::new()),
                ("batch:custom".into(), tr("custom_range"), String::new()),
                (
                    "batch:remove-read".into(),
                    tr("remove_read_downloads"),
                    String::new(),
                ),
            ],
        )
    }

    fn download_selected_chapters(&mut self) -> Result<()> {
        if self.options.offline && !self.options.demo {
            bail!("{}", tr("offline_help"));
        }
        let book = self.current_book()?;
        let mut chapters: Vec<_> = self
            .store
            .chapters(&book.id)?
            .into_iter()
            .filter(|chapter| self.selected_chapters.contains(&chapter.id))
            .collect();
        chapters.sort_by(|left, right| left.number.total_cmp(&right.number));
        if chapters.is_empty() {
            self.notify(tr("no_downloads_needed"));
            return Ok(());
        }
        self.downloads.enqueue_many(&book, &chapters)?;
        self.selected_chapters.clear();
        Ok(())
    }

    fn enqueue_batch(&mut self, kind: &str) -> Result<()> {
        if self.options.offline && !self.options.demo {
            bail!("{}", tr("offline_help"));
        }
        let book = self.current_book()?;
        let mut chapters = self.store.chapters(&book.id)?;
        chapters.sort_by(|first, second| first.number.total_cmp(&second.number));
        let after = self
            .store
            .progress(&book.id)?
            .and_then(|progress| {
                chapters
                    .iter()
                    .find(|chapter| chapter.id == progress.chapter_id)
                    .map(|chapter| chapter.number)
            })
            .unwrap_or(f64::NEG_INFINITY);
        let count = if kind == "next-five" {
            5
        } else if kind == "next-ten" {
            10
        } else {
            usize::MAX
        };
        let selected: Vec<_> = chapters
            .into_iter()
            .filter(|chapter| match kind {
                "all" => true,
                "unread" => !chapter.read,
                _ => chapter.number > after && !chapter.downloaded,
            })
            .take(count)
            .collect();
        self.modal.clear();
        if selected.is_empty() {
            self.notify(tr("no_downloads_needed"));
            return Ok(());
        }
        self.downloads.enqueue_many(&book, &selected)?;
        Ok(())
    }

    fn refresh_library(&mut self) -> Result<()> {
        self.invalidate_automatic_updates();
        if self.options.demo {
            self.notify(tr("library_update_demo"));
            return Ok(());
        }
        if self.options.offline {
            self.notify(tr("library_update_offline"));
            return Ok(());
        }
        let sources = self.store.sources()?;
        let books: Vec<_> = self
            .store
            .books()?
            .into_iter()
            .filter(|book| {
                book.in_library
                    && sources.iter().any(|source| {
                        source.id == book.source_id
                            && source.enabled
                            && source.kind != "local"
                            && (!matches!(source.kind.as_str(), "komga" | "kavita" | "suwayomi")
                                || !source.base_url.trim().is_empty())
                    })
            })
            .collect();
        if books.is_empty() {
            self.notify(tr("library_update_no_targets"));
            return Ok(());
        }
        self.spawn_job(true, move |manager| {
            let mut updates = Vec::new();
            for book in books {
                let details = manager.details(&book)?;
                let chapters = manager.chapters(&details)?;
                updates.push((details, chapters));
            }
            Ok(JobResult::Updated(updates))
        })
    }

    pub(crate) fn open_downloads(&mut self) -> Result<()> {
        self.modal = "downloads".into();
        self.modal_context = "downloads".into();
        self.panel_page = 0;
        if let Some(ui) = self.ui.upgrade() {
            ui.global::<State>().set_modal_title(tr("downloads").into());
        }
        self.refresh_download_rows()
    }

    fn open_storage(&mut self) -> Result<()> {
        let stats = self.disk_cache.stats();
        self.show_choices(
            &tr("storage"),
            "storage",
            vec![
                ("downloads".into(), tr("downloads"), String::new()),
                (
                    "cache-info".into(),
                    tr("cache_usage"),
                    format!("{} MB", stats.bytes / 1_000_000),
                ),
                (
                    "clear-cache".into(),
                    tr("clear_cache"),
                    tr("cache_protected"),
                ),
            ],
        )
    }

    pub(crate) fn refresh_download_rows(&mut self) -> Result<()> {
        let count = self.store.download_count()?;
        let states = self.store.download_state_counts()?;
        let state_count = |state: &str| states.get(state).copied().unwrap_or(0);
        self.choice_disabled.clear();
        if state_count("queued") + state_count("downloading") == 0 {
            self.choice_disabled.insert("downloads-pause-all".into());
        }
        if state_count("paused") + state_count("failed") == 0 {
            self.choice_disabled.insert("downloads-resume-all".into());
        }
        if state_count("queued") + state_count("downloading") + state_count("paused") == 0 {
            self.choice_disabled.insert("downloads-cancel-all".into());
        }
        self.choice_total = count;
        self.panel_page = self.panel_page.min(count.div_ceil(3).max(1) - 1);
        let mut rows = vec![
            (
                "downloads-pause-all".to_owned(),
                tr("pause_all"),
                String::new(),
            ),
            (
                "downloads-resume-all".to_owned(),
                tr("resume_all"),
                String::new(),
            ),
            (
                "downloads-cancel-all".to_owned(),
                tr("cancel_all"),
                String::new(),
            ),
        ];
        let jobs = self.store.downloads_page(self.panel_page * 3, 3)?;
        let mut queue = Vec::with_capacity(jobs.len());
        for job in jobs {
            let book = self.store.book(&job.book_id)?;
            let chapter = self.store.chapter(&job.chapter_id)?;
            let title = book
                .as_ref()
                .map(|book| book.title.clone())
                .unwrap_or_default();
            let chapter_label = chapter
                .as_ref()
                .map(|chapter| {
                    format!(
                        "{}{}{} · {}",
                        tr("chapter_prefix"),
                        chapter.number,
                        tr("chapter_suffix"),
                        chapter.title,
                    )
                })
                .unwrap_or_default();
            let cover = book
                .as_ref()
                .and_then(|book| {
                    self.cover_paths
                        .get(&book.id)
                        .filter(|_| self.cover_uris.get(&book.id) == Some(&book.cover))
                        .and_then(|path| self.cover_cache.get(&(book.id.clone(), path.clone())))
                        .or_else(|| {
                            self.cover_cache
                                .get(&(book.id.clone(), PathBuf::from(&book.cover)))
                        })
                        .cloned()
                })
                .unwrap_or_default();
            queue.push(DownloadItem {
                id: job.chapter_id.clone().into(),
                book_id: job.book_id.clone().into(),
                title: title.clone().into(),
                chapter: chapter_label.into(),
                cover,
                progress: job.completed as f32 / job.total.max(1) as f32,
                state: job.state.clone().into(),
                status: download_state(&job.state).into(),
                completed: job.completed.min(i32::MAX as u32) as i32,
                total: job.total.min(i32::MAX as u32) as i32,
            });
            rows.push((
                format!("job:{}", job.chapter_id),
                format!(
                    "{} · {}",
                    title,
                    chapter
                        .map(|chapter| chapter.number.to_string())
                        .unwrap_or_default()
                ),
                format!(
                    "{} · {}/{}",
                    download_state(&job.state),
                    job.completed,
                    job.total
                ),
            ));
        }
        if count == 0 {
            rows.push(("close".into(), tr("no_downloads"), String::new()));
        }
        self.choice_rows = rows;
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_download_queue(model(queue));
            state.set_download_queue_count(count.min(i32::MAX as usize) as i32);
            state.set_downloads_paused(
                state_count("queued") + state_count("downloading") == 0
                    && state_count("paused") + state_count("failed") > 0,
            );
            state.set_download_queue_can_pause(
                state_count("queued") + state_count("downloading") > 0,
            );
            state.set_download_queue_can_resume(state_count("paused") + state_count("failed") > 0);
        }
        self.sync_choice_rows();
        Ok(())
    }

    pub(super) fn open_pairing(&mut self) -> Result<()> {
        self.platform.request_network_for_user();
        if !self.platform.user_network_ready() {
            self.request_user_network(UserNetworkIntent::Pairing);
            return Ok(());
        }
        let result = self.start_pairing_server();
        self.platform.finish_user_network_request();
        result
    }

    fn start_pairing_server(&mut self) -> Result<()> {
        if self.pairing.as_ref().is_none_or(|server| !server.active()) {
            self.pairing = Some(PairingServer::start(self.data_dir.clone())?);
        }
        let server = self
            .pairing
            .as_ref()
            .context("Pairing service did not start")?;
        let code =
            qrcode::QrCode::new(format!("{}?token={}", server.url(), server.token()).as_bytes())?;
        let module = 6u32;
        let side = (code.width() as u32 + 8) * module;
        let mut image = GrayImage::from_pixel(side, side, image::Luma([255]));
        for y in 0..code.width() {
            for x in 0..code.width() {
                if code[(x, y)] == qrcode::Color::Dark {
                    for dy in 0..module {
                        for dx in 0..module {
                            image.put_pixel(
                                (x as u32 + 4) * module + dx,
                                (y as u32 + 4) * module + dy,
                                image::Luma([0]),
                            );
                        }
                    }
                }
            }
        }
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_pairing_code(gray_image(image));
            state.set_pairing_url(server.url().into());
            state.set_pairing_token(server.code().into());
            state.set_modal_title(tr("pairing").into());
        }
        self.modal = "pairing".into();
        self.modal_context = "pairing".into();
        Ok(())
    }

    fn request_user_network(&mut self, intent: UserNetworkIntent) {
        self.platform.request_network_for_user();
        self.pending_user_network = Some(PendingUserNetwork {
            intent,
            expires: Instant::now() + Duration::from_secs(45),
        });
        self.notify(tr("loading"));
    }

    pub(super) fn cancel_pending_user_network(&mut self) {
        if self.pending_user_network.take().is_some() {
            self.platform.finish_user_network_request();
        }
    }

    pub(super) fn poll_pending_user_network(&mut self) -> Result<()> {
        let Some(pending) = self.pending_user_network.as_ref() else {
            return Ok(());
        };
        if self.suspended || self.shutting_down || self.metadata_restore_active() {
            self.cancel_pending_user_network();
            return Ok(());
        }
        if Instant::now() >= pending.expires {
            self.cancel_pending_user_network();
            bail!("{}", tr("offline_help"));
        }
        if matches!(pending.intent, UserNetworkIntent::ReaderLoading) {
            if self.chapter_inflight.is_none()
                && self.reader_inflight.is_none()
                && !self.reader_dirty
            {
                self.cancel_pending_user_network();
            }
            return Ok(());
        }
        if !self.platform.user_network_ready() {
            return Ok(());
        }
        let pending = self.pending_user_network.take().unwrap();
        match pending.intent {
            UserNetworkIntent::Pairing => {
                let result = self.start_pairing_server();
                self.platform.finish_user_network_request();
                result
            }
            UserNetworkIntent::Reader(target) => {
                self.pending_user_network = Some(PendingUserNetwork {
                    intent: UserNetworkIntent::ReaderLoading,
                    expires: pending.expires,
                });
                self.load_reader_target(target)
            }
            UserNetworkIntent::ReaderLoading => Ok(()),
        }
    }

    pub(super) fn open_keyboard(&mut self, target: &str, value: &str) -> Result<()> {
        if !target.starts_with("server-") {
            self.clear_server_keyboard();
        }
        self.keyboard.set_text(value);
        self.keyboard
            .set_mode(if matches!(target, "search" | "source-query") {
                "pinyin"
            } else {
                "english"
            });
        self.keyboard_target = target.into();
        self.modal = "keyboard".into();
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_keyboard_mode(
                if matches!(target, "search" | "source-query") {
                    "pinyin"
                } else {
                    "english"
                }
                .into(),
            );
            state.set_modal_title(
                if target == "repository" {
                    tr("repository_url")
                } else if target == "local" {
                    tr("import_path")
                } else if target == "batch-range" {
                    tr("chapter_range_format")
                } else {
                    tr("search")
                }
                .into(),
            );
        }
        Ok(())
    }

    fn keyboard_action(&mut self, action: &str, value: &str) -> Result<()> {
        match action {
            "key" => {
                self.keyboard.key(value);
            }
            "space" => {
                self.keyboard.key(" ");
            }
            "backspace" => self.keyboard.backspace(),
            "candidate" => {
                self.keyboard.choose(value);
            }
            "candidate-page" => self.keyboard.candidate_page(value.parse()?),
            "mode" => {
                self.keyboard.set_mode(value);
                if let Some(ui) = self.ui.upgrade() {
                    ui.global::<State>().set_keyboard_mode(value.into());
                }
            }
            "cancel" => {
                self.modal.clear();
                self.clear_server_keyboard();
                self.keyboard.set_text("");
                self.keyboard_target.clear();
            }
            "hardware" => {
                if matches!(value, "\u{8}" | "\u{7f}") {
                    self.keyboard.backspace();
                } else if matches!(value, "\n" | "\r") {
                    return self.keyboard_action("done", "");
                } else {
                    self.keyboard.key(value);
                }
            }
            "done" => {
                let text = self.keyboard.commit();
                self.modal.clear();
                let target = self.keyboard_target.clone();
                if self.category_keyboard_submit(&target, &text)?
                    || self.tracking_keyboard_submit(&target, &text)?
                {
                    return Ok(());
                }
                if self.save_server_keyboard(&target, &text)? {
                    return Ok(());
                }
                self.keyboard.set_text("");
                self.keyboard_target.clear();
                match target.as_str() {
                    "search" => {
                        self.query = text;
                        self.run_search()?;
                    }
                    "source-query" => {
                        self.query = text;
                        self.search_scope = self.current_source.clone();
                        self.route = "search".into();
                        self.run_search()?;
                    }
                    "repository" => self.fetch_repository(&text)?,
                    "batch-range" => self.enqueue_custom_range(&text)?,
                    "local" => self.import_local(Path::new(&text))?,
                    target if target.starts_with("sourceopt:") => {
                        let key = target.trim_start_matches("sourceopt:").to_owned();
                        self.save_source_option(&key, Value::String(text))?;
                    }
                    _ => {}
                }
            }
            _ => {}
        }
        Ok(())
    }

    pub(super) fn show_choices(
        &mut self,
        title: &str,
        context: &str,
        rows: Vec<(String, String, String)>,
    ) -> Result<()> {
        self.modal = "choices".into();
        self.modal_context = context.into();
        self.choice_rows = rows;
        self.choice_total = self.choice_rows.len();
        self.choice_disabled.clear();
        self.panel_page = 0;
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_modal_title(title.into());
        }
        self.sync_choice_rows();
        Ok(())
    }

    pub(crate) fn sync_choice_rows(&self) {
        if let Some(ui) = self.ui.upgrade() {
            let page_size = self.choice_page_size();
            if self.modal_context == "source-settings" {
                ui.global::<State>().set_source_preference_groups(model(
                    self.source_settings
                        .iter()
                        .skip(self.panel_page * page_size)
                        .take(page_size)
                        .map(|setting| SourcePreferenceGroup {
                            id: format!("sourceopt:{}", setting.key).into(),
                            title: setting.title.clone().into(),
                            kind: setting.kind.clone().into(),
                            value: if setting.kind == "secure-text" {
                                "••••".into()
                            } else {
                                display_value(&setting.value).into()
                            },
                            options: model(setting.options.iter().enumerate().map(
                                |(index, option)| {
                                    SettingItem {
                                        id: format!("source-inline:{}:{index}", setting.key).into(),
                                        title: option.clone().into(),
                                        value: check(if setting.kind == "multi-select" {
                                            setting.value.as_array().is_some_and(|values| {
                                                values.iter().any(|value| {
                                                    value.as_str() == Some(option.as_str())
                                                })
                                            })
                                        } else if setting.kind == "segment" {
                                            setting.value.as_u64() == Some(index as u64)
                                        } else {
                                            setting.value.as_str() == Some(option.as_str())
                                        })
                                        .into(),
                                        group: "option".into(),
                                        enabled: !setting.kind.starts_with("disabled:"),
                                    }
                                },
                            )),
                            enabled: !setting.kind.starts_with("disabled:")
                                && matches!(
                                    setting.kind.as_str(),
                                    "switch"
                                        | "select"
                                        | "multi-select"
                                        | "segment"
                                        | "picker"
                                        | "stepper"
                                        | "text"
                                        | "secure-text"
                                ),
                        }),
                ));
            }
            if self.modal_context == "source-picker" {
                let sources = ui.global::<State>().get_sources();
                let visible: Vec<_> = (0..sources.row_count())
                    .skip(self.panel_page * page_size)
                    .take(page_size)
                    .filter_map(|index| sources.row_data(index))
                    .collect();
                ui.global::<State>().set_picker_builtin_count(
                    visible.iter().filter(|source| source.builtin).count() as i32,
                );
                ui.global::<State>().set_picker_sources(model(visible));
            }
            ui.global::<State>().set_modal_items(model(
                self.choice_rows
                    .iter()
                    .skip(
                        if self.modal == "downloads"
                            || self.modal_context.starts_with("source-migration:")
                        {
                            0
                        } else {
                            self.panel_page * page_size
                        },
                    )
                    .take(page_size)
                    .map(|(id, title, value)| SettingItem {
                        id: id.clone().into(),
                        title: title.clone().into(),
                        value: value.clone().into(),
                        group: if self.modal_context == "reader-toc" {
                            id.strip_prefix("chapter:")
                                .and_then(|id| {
                                    self.reader.as_ref().and_then(|reader| {
                                        reader
                                            .chapters()
                                            .iter()
                                            .find(|chapter| chapter.id == id)
                                            .map(|chapter| {
                                                if chapter.id == reader.current_chapter().id {
                                                    "current"
                                                } else if chapter.read {
                                                    "read"
                                                } else if chapter.downloaded {
                                                    "downloaded"
                                                } else {
                                                    ""
                                                }
                                            })
                                    })
                                })
                                .unwrap_or("")
                                .into()
                        } else {
                            "".into()
                        },
                        enabled: !self.choice_disabled.contains(id),
                    }),
            ));
        }
    }

    pub(crate) fn open_summary(&mut self) -> Result<()> {
        let text = self.current_book()?.description;
        self.panel_pages = text_pagination::paginate_text(
            &text,
            if self.platform.profile().is_scribe() {
                700
            } else {
                320
            },
        );
        self.panel_page = 0;
        self.modal = "summary".into();
        self.modal_context = "summary".into();
        if let Some(ui) = self.ui.upgrade() {
            ui.global::<State>()
                .set_modal_title(tr("summary_tags").into());
        }
        Ok(())
    }

    fn open_license(&mut self) -> Result<()> {
        self.panel_pages = license_pages(self.platform.profile().is_scribe());
        self.panel_page = 0;
        self.modal = "summary".into();
        self.modal_context = "license".into();
        if let Some(ui) = self.ui.upgrade() {
            ui.global::<State>()
                .set_modal_title(format!("{} · GPL-3.0-or-later", tr("license")).into());
        }
        Ok(())
    }

    fn open_download_path(&mut self) -> Result<()> {
        self.panel_pages = text_pagination::paginate_text(
            &self.data_dir.to_string_lossy(),
            if self.platform.profile().is_scribe() {
                700
            } else {
                320
            },
        );
        self.panel_page = 0;
        self.modal = "summary".into();
        self.modal_context = "download-path".into();
        if let Some(ui) = self.ui.upgrade() {
            ui.global::<State>()
                .set_modal_title(tr("view_full_path").into());
        }
        Ok(())
    }

    fn open_library_filter(&mut self) -> Result<()> {
        self.show_choices(
            &tr("sort_and_filter"),
            "library-filter",
            interaction_models::LibrarySort::ALL
                .into_iter()
                .map(|sort| {
                    (
                        format!("library-sort:{}", sort.id()),
                        tr(sort.label()),
                        check(self.interactions.library_sort == sort),
                    )
                })
                .collect(),
        )
    }

    fn open_catalog_filter(&mut self) -> Result<()> {
        self.show_choices(&tr("filter"), "catalog-filter", Vec::new())?;
        self.refresh_catalog_filter();
        Ok(())
    }

    fn choose_catalog_filter(&mut self, value: &str) -> Result<()> {
        let filter = self
            .interactions
            .catalog_filters
            .entry(self.current_source.clone())
            .or_default();
        if let Some(sort) = value.strip_prefix("sort:") {
            if !matches!(sort, "relevance" | "latest") {
                bail!("The source does not expose this sort value");
            }
            filter.sort = sort.into();
        } else if let Some(status) = value.strip_prefix("status:") {
            if !matches!(status, "ongoing" | "completed" | "hiatus" | "cancelled") {
                bail!("Unknown source series status");
            }
            if filter.statuses.iter().any(|value| value == status) {
                filter.statuses.retain(|value| value != status);
            } else {
                filter.statuses.push(status.into());
            }
        } else if let Some(tag) = value.strip_prefix("tag:") {
            if !self
                .catalog
                .iter()
                .any(|book| book.tags.iter().any(|value| value == tag))
            {
                bail!("The selected tag is not present in the loaded catalog");
            }
            let state = filter.tags.entry(tag.into()).or_default();
            *state = match *state {
                0 => 1,
                1 => -1,
                _ => 0,
            };
        } else {
            bail!("Unknown source filter");
        }
        self.interactions.save(&self.store)?;
        self.page = 1;
        self.refresh_catalog_filter();
        Ok(())
    }

    fn refresh_catalog_filter(&self) {
        let filter = self
            .interactions
            .catalog_filters
            .get(&self.current_source)
            .cloned()
            .unwrap_or_default();
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_catalog_sort_options(model(
                [
                    ("relevance", "sort_relevance", true),
                    ("latest", "sort_latest", true),
                    ("follows", "sort_follows", false),
                    ("rating", "sort_rating", false),
                ]
                .into_iter()
                .map(|(id, label, enabled)| SettingItem {
                    id: format!("sort:{id}").into(),
                    title: tr(label).into(),
                    value: check(filter.sort == id).into(),
                    group: "sort".into(),
                    enabled,
                }),
            ));
            state.set_catalog_status_options(model(
                [
                    ("ongoing", "ongoing"),
                    ("completed", "completed"),
                    ("hiatus", "paused"),
                    ("cancelled", "discontinued"),
                ]
                .into_iter()
                .map(|(id, label)| SettingItem {
                    id: format!("status:{id}").into(),
                    title: tr(label).into(),
                    value: check(filter.statuses.iter().any(|value| value == id)).into(),
                    group: "status".into(),
                    enabled: true,
                }),
            ));
            let tags: std::collections::BTreeSet<_> = self
                .catalog
                .iter()
                .flat_map(|book| book.tags.iter().cloned())
                .collect();
            state.set_catalog_tag_options(model(tags.into_iter().map(|tag| FilterOption {
                id: format!("tag:{tag}").into(),
                title: tag.clone().into(),
                state: i32::from(filter.tags.get(&tag).copied().unwrap_or(0)),
            })));
            state.set_catalog_filter_result_count(
                self.catalog
                    .iter()
                    .filter(|book| filter.matches(book))
                    .count()
                    .min(i32::MAX as usize) as i32,
            );
        }
    }

    fn open_library_more(&mut self) -> Result<()> {
        let mut rows = vec![
            ("downloads".into(), tr("downloads"), String::new()),
            ("selection".into(), tr("multiple_selection"), String::new()),
            (
                "manage-categories".into(),
                tr("manage_categories"),
                String::new(),
            ),
        ];
        if !self.platform.profile().is_scribe() {
            rows.insert(0, ("refresh".into(), tr("refresh_library"), String::new()));
        }
        self.show_choices(&tr("more"), "library-more", rows)
    }

    fn open_detail_more(&mut self) -> Result<()> {
        let book = self.current_book()?;
        let large = self.platform.profile().is_scribe();
        let manageable = if large {
            self.store.book_download_count(&book.id)?
        } else {
            0
        };
        let downloaded = if large {
            self.store.chapter_view_summary(&book.id, None)?.downloaded
        } else {
            0
        };
        let mut rows = vec![
            ("reload".into(), tr("reload_chapters"), String::new()),
            ("migrate".into(), tr("migrate_source"), String::new()),
            ("category".into(), tr("move_category"), String::new()),
            (
                "screensaver".into(),
                tr("set_screensaver"),
                tr("unavailable"),
            ),
        ];
        if manageable > 0 || downloaded > 0 {
            rows.push((
                "remove-all-downloads".into(),
                tr("delete_all_downloads"),
                if manageable == 0 {
                    tr("local_import_protected")
                } else {
                    String::new()
                },
            ));
        }
        self.show_choices(&tr("more"), "detail-more", rows)?;
        self.choice_disabled.insert("screensaver".into());
        if manageable == 0 {
            self.choice_disabled.insert("remove-all-downloads".into());
        }
        self.sync_choice_rows();
        Ok(())
    }

    fn open_reader_panel(&mut self, panel: &str) -> Result<()> {
        self.pending_wide_navigation = 0;
        let effective = self.effective_reader_preferences();
        let context = match panel {
            "mode" => "reader-mode",
            "picture" => "reader-picture",
            "brightness" => "reader-brightness",
            "orientation" => "reader-orientation",
            "layout" => "reader-layout",
            "more" => "reader-more",
            "toc" => "reader-toc",
            _ => "",
        };
        if !context.is_empty() && !self.modal.is_empty() && self.modal_context == context {
            self.modal.clear();
            self.modal_context.clear();
            return Ok(());
        }
        match panel {
            "mode" => self.show_choices(
                &tr("reader_mode"),
                "reader-mode",
                vec![
                    (
                        "direction:rtl".into(),
                        tr("rtl"),
                        check(effective.reading_direction == "rtl"),
                    ),
                    (
                        "direction:ltr".into(),
                        tr("ltr"),
                        check(effective.reading_direction == "ltr"),
                    ),
                    (
                        "direction:vertical-pages".into(),
                        tr("vertical_pages"),
                        check(effective.reading_direction == "vertical-pages"),
                    ),
                    (
                        "direction:vertical".into(),
                        tr("webtoon_scroll"),
                        check(effective.reading_direction == "vertical"),
                    ),
                ],
            ),
            "picture" => {
                self.open_setting("image-processing")?;
                self.modal_context = "reader-picture".into();
                Ok(())
            }
            "brightness" => {
                if self.platform.profile().has_frontlight() {
                    self.modal = "brightness".into();
                    self.modal_context = "reader-brightness".into();
                    if let Some(ui) = self.ui.upgrade() {
                        ui.global::<State>()
                            .set_modal_title(tr("reader_brightness_title").into());
                    }
                }
                Ok(())
            }
            "orientation" => self.show_choices(
                &tr("screen_orientation"),
                "reader-orientation",
                vec![
                    (
                        "portrait".into(),
                        tr("portrait"),
                        check(!self.prefs.landscape),
                    ),
                    (
                        "landscape".into(),
                        tr("landscape"),
                        check(self.prefs.landscape && !self.prefs.reverse_landscape),
                    ),
                    (
                        "reverse-landscape".into(),
                        tr("reverse_landscape"),
                        check(self.prefs.landscape && self.prefs.reverse_landscape),
                    ),
                ],
            ),
            "layout" => self.show_choices(
                &tr("page_layout"),
                "reader-layout",
                vec![
                    (
                        "layout:single".into(),
                        tr("single_page"),
                        check(self.interactions.page_layout_mode == "single"),
                    ),
                    (
                        "layout:double".into(),
                        tr("double_page"),
                        check(self.interactions.page_layout_mode == "double"),
                    ),
                    (
                        "layout:automatic".into(),
                        tr("automatic"),
                        check(self.interactions.page_layout_mode == "automatic"),
                    ),
                ],
            ),
            "toc" => self.open_reader_toc(),
            "page-actions" => self.show_choices(
                &tr("page_actions"),
                "page-actions",
                vec![
                    ("save-page".into(), tr("save_page"), String::new()),
                    ("set-cover".into(), tr("set_cover"), String::new()),
                    ("reload-page".into(), tr("reload"), String::new()),
                ],
            ),
            "more" => self.show_choices(
                &tr("more"),
                "reader-more",
                vec![
                    (
                        "show-tap-zones".into(),
                        tr("show_tap_zones"),
                        check(self.interactions.show_tap_zones),
                    ),
                    (
                        "reverse-tap".into(),
                        tr("reverse_tap"),
                        check(
                            self.prefs.reverse_taps
                                || matches!(self.prefs.tap_layout.as_str(), "reverse" | "reversed"),
                        ),
                    ),
                    ("refresh-now".into(), tr("manual_refresh"), String::new()),
                ],
            ),
            _ => Ok(()),
        }
    }

    pub(crate) fn reader_toc_page_size(&self) -> usize {
        if self.platform.profile().is_scribe() {
            6
        } else {
            5
        }
    }

    fn open_reader_toc(&mut self) -> Result<()> {
        let book = self.current_book()?;
        let rows: Vec<(String, String, String)> = if self.interactions.reader_toc_tab == "bookmarks"
        {
            self.store
                .bookmarks(&book.id)?
                .into_iter()
                .map(|bookmark| {
                    (
                        format!("bookmark:{}|{}", bookmark.chapter_id, bookmark.page),
                        self.reader
                            .as_ref()
                            .and_then(|reader| {
                                reader
                                    .chapters()
                                    .iter()
                                    .find(|chapter| chapter.id == bookmark.chapter_id)
                            })
                            .map(|chapter| {
                                format!(
                                    "{}{}{} · {}",
                                    tr("chapter_prefix"),
                                    chapter.number,
                                    tr("chapter_suffix"),
                                    chapter.title
                                )
                            })
                            .unwrap_or_else(|| tr("bookmark")),
                        format!("{} {}", bookmark.page, tr("pages_unit")),
                    )
                })
                .collect()
        } else {
            self.store
                .chapters(&book.id)?
                .into_iter()
                .map(|chapter| {
                    let value = if chapter.id
                        == self
                            .ui
                            .upgrade()
                            .map(|ui| ui.global::<State>().get_current_chapter_id().to_string())
                            .unwrap_or_default()
                    {
                        self.ui
                            .upgrade()
                            .map(|ui| {
                                let state = ui.global::<State>();
                                format!(
                                    "{} / {}",
                                    state.get_reader_page(),
                                    state.get_reader_total()
                                )
                            })
                            .unwrap_or_default()
                    } else if chapter.read {
                        tr("read")
                    } else if chapter.downloaded {
                        tr("downloaded")
                    } else {
                        String::new()
                    };
                    (
                        format!("chapter:{}", chapter.id),
                        format!(
                            "{}{}{} · {}",
                            tr("chapter_prefix"),
                            chapter.number,
                            tr("chapter_suffix"),
                            chapter.title
                        ),
                        value,
                    )
                })
                .collect()
        };
        let displayed = self
            .displayed_reader
            .as_ref()
            .map(|frame| (frame.session.chapter_id.clone(), frame.session.page))
            .or_else(|| {
                self.reader
                    .as_ref()
                    .map(|reader| (reader.current_chapter().id.clone(), reader.page))
            });
        let target = displayed.map(|(chapter, page)| {
            if self.interactions.reader_toc_tab == "bookmarks" {
                format!("bookmark:{chapter}|{page}")
            } else {
                format!("chapter:{chapter}")
            }
        });
        let page = target
            .and_then(|target| rows.iter().position(|(id, _, _)| id == &target))
            .map(|index| index / self.reader_toc_page_size())
            .unwrap_or(0);
        self.show_choices(&tr("table_of_contents"), "reader-toc", rows)?;
        self.panel_page = page.min(
            self.choice_total
                .div_ceil(self.reader_toc_page_size())
                .max(1)
                - 1,
        );
        self.sync_choice_rows();
        Ok(())
    }

    pub(crate) fn open_setting(&mut self, id: &str) -> Result<()> {
        self.close_page_jump();
        if self.route == "settings"
            && matches!(
                id,
                "screen-refresh"
                    | "frontlight-warmth"
                    | "wifi-sleep-exit"
                    | "storage"
                    | "default-direction"
                    | "tap-regions"
                    | "image-processing"
                    | "spread"
                    | "categories"
                    | "auto-update"
                    | "source-lists"
                    | "downloads"
                    | "tracking"
                    | "backup"
                    | "language"
                    | "logs"
                    | "about"
                    | "pen"
            )
        {
            self.interactions.settings_section = id.into();
            self.interactions.save(&self.store)?;
            self.modal.clear();
            self.modal_context.clear();
            if let Some(ui) = self.ui.upgrade() {
                ui.global::<State>().set_settings_details_page(1);
            }
            self.refresh_setting_details()?;
            return Ok(());
        }
        match id {
            "screen-refresh" => self.show_choices(
                &tr("screen_refresh"),
                "refresh",
                vec![
                    (
                        "refresh:1".into(),
                        "1".into(),
                        check(self.prefs.refresh_every == 1),
                    ),
                    (
                        "refresh:3".into(),
                        "3".into(),
                        check(self.prefs.refresh_every == 3),
                    ),
                    (
                        "refresh:6".into(),
                        "6".into(),
                        check(self.prefs.refresh_every == 6),
                    ),
                    (
                        "refresh:10".into(),
                        "10".into(),
                        check(self.prefs.refresh_every == 10),
                    ),
                    (
                        "refresh:0".into(),
                        tr("only_chapter"),
                        check(self.prefs.refresh_every == 0),
                    ),
                    (
                        "flash-close".into(),
                        tr("flash_close"),
                        check(self.prefs.flash_on_close),
                    ),
                ],
            ),
            "frontlight-warmth" => self.open_reader_panel("brightness"),
            "wifi-sleep-exit" => self.show_choices(
                &tr("wifi_sleep_exit"),
                "device",
                vec![
                    (
                        "keep-awake".into(),
                        tr("keep_awake"),
                        check(self.prefs.keep_awake),
                    ),
                    ("pairing".into(), tr("pairing"), String::new()),
                    ("exit".into(), tr("exit"), String::new()),
                ],
            ),
            "storage" => self.open_storage(),
            "downloads" => self.open_downloads(),
            "default-direction" => self.open_reader_panel("mode"),
            "tap-regions" => self.show_choices(
                &tr("tap_regions"),
                "tap",
                vec![
                    (
                        "tap:thirds".into(),
                        tr("thirds"),
                        check(matches!(
                            self.prefs.tap_layout.as_str(),
                            "thirds" | "reverse" | "reversed"
                        )),
                    ),
                    (
                        "tap:l-shaped".into(),
                        tr("tap_l_shape"),
                        check(self.prefs.tap_layout == "l-shaped"),
                    ),
                    (
                        "tap:swipe-only".into(),
                        tr("swipe_only"),
                        check(self.prefs.tap_layout == "swipe-only"),
                    ),
                    (
                        "reverse-tap".into(),
                        tr("reverse_tap"),
                        check(
                            self.prefs.reverse_taps
                                || matches!(self.prefs.tap_layout.as_str(), "reverse" | "reversed"),
                        ),
                    ),
                ],
            ),
            "image-processing" => self.show_choices(
                &tr("image_processing"),
                "image",
                vec![
                    (
                        "crop".into(),
                        tr("crop_borders"),
                        check(self.prefs.crop_borders),
                    ),
                    ("dither".into(), tr("dither"), check(self.prefs.dither)),
                    (
                        "contrast:-20".into(),
                        format!("{} -20", tr("contrast")),
                        check(self.prefs.contrast == -20),
                    ),
                    (
                        "contrast:0".into(),
                        format!("{} 0", tr("contrast")),
                        check(self.prefs.contrast == 0),
                    ),
                    (
                        "contrast:20".into(),
                        format!("{} +20", tr("contrast")),
                        check(self.prefs.contrast == 20),
                    ),
                ],
            ),
            "rotation" => self.open_reader_panel("orientation"),
            "night-mode" => {
                self.prefs.night_mode = !self.prefs.night_mode;
                self.save_preferences()
            }
            "double-tap-zoom" => {
                self.prefs.double_tap_zoom = !self.prefs.double_tap_zoom;
                self.save_preferences()
            }
            "tracking" => self.show_choices(
                &tr("tracking"),
                "tracking",
                vec![("pairing".into(), tr("configure"), tr("tracking_help"))],
            ),
            "about" => {
                self.modal_context = "about".into();
                self.panel_pages = vec![format!(
                    "Aidoku for Kindle {}\n{}\nGPL-3.0-or-later",
                    env!("CARGO_PKG_VERSION"),
                    if self.options.demo {
                        tr("demo_notice")
                    } else {
                        self.platform.profile().name().into()
                    }
                )];
                self.panel_page = 0;
                self.modal = "summary".into();
                if let Some(ui) = self.ui.upgrade() {
                    ui.global::<State>().set_modal_title(tr("about").into());
                }
                Ok(())
            }
            _ => Ok(()),
        }
    }

    pub(crate) fn choose_panel(&mut self, id: &str) -> Result<()> {
        if id == "data-restore-confirm" {
            return self.begin_metadata_restore();
        }
        if self.tracking_choice(id)? || self.data_management_action(id, "")? {
            return Ok(());
        }
        if self.modal == "choices" && self.modal_context.starts_with("source-migration:") {
            return self.choose_source_migration_book(id);
        }
        if self.modal == "choices" && self.modal_context.starts_with("search-scope:") {
            return self.choose_search_scope(id);
        }
        if let Some(action) = id.strip_prefix("server-config:") {
            return self.server_action(action, "");
        }
        match id {
            "reader-retry" if self.modal_context == "reader-recovery" => {
                return self.retry_reader_recovery();
            }
            "reader-recovery-back" if self.modal_context == "reader-recovery" => {
                return self.leave_reader_recovery();
            }
            "reader-action-dismiss" if self.modal_context == "reader-action-error" => {
                self.dismiss_reader_action_error();
                return Ok(());
            }
            _ => {}
        }
        if let Some(key) = id.strip_prefix("sourceopt:") {
            return self.edit_source_option(&key.to_owned());
        }
        if let Some(value) = id.strip_prefix("source-inline:") {
            let (key, index) = value
                .rsplit_once(':')
                .context("Invalid source option selection")?;
            let setting = self
                .source_settings
                .iter()
                .find(|setting| setting.key == key)
                .cloned()
                .context("The source setting no longer exists")?;
            let index: usize = index.parse()?;
            let option = setting
                .options
                .get(index)
                .context("Unknown source option")?;
            let value = if setting.kind == "segment" {
                json!(index)
            } else if setting.kind == "multi-select" {
                let mut selected: Vec<String> = setting
                    .value
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect();
                if selected.contains(option) {
                    selected.retain(|value| value != option);
                } else {
                    selected.push(option.clone());
                }
                json!(selected)
            } else {
                json!(option)
            };
            return self.save_source_option(key, value);
        }
        if let Some(index) = id.strip_prefix("sourcevalue:") {
            let setting = self
                .source_settings
                .iter()
                .find(|setting| format!("sourcevalue:{}", setting.key) == self.modal_context)
                .cloned()
                .context("No source option is selected")?;
            let index: usize = index.parse()?;
            let value = if setting.kind == "segment" {
                json!(index)
            } else if setting.kind == "multi-select" {
                let option = setting
                    .options
                    .get(index)
                    .context("Unknown source option")?;
                let mut selected: Vec<String> = setting
                    .value
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect();
                if selected.iter().any(|value| value == option) {
                    selected.retain(|value| value != option);
                } else {
                    selected.push(option.clone());
                }
                json!(selected)
            } else {
                json!(
                    setting
                        .options
                        .get(index)
                        .context("Unknown source option")?
                )
            };
            self.save_source_option(&setting.key, value)?;
            if setting.kind == "multi-select" {
                self.edit_source_option(&setting.key)?;
            }
            return Ok(());
        }
        if let Some(source) = id.strip_prefix("source:") {
            self.modal.clear();
            return self.open_source(source);
        }
        if let Some(book) = id.strip_prefix("open-book:") {
            self.modal.clear();
            self.modal_context.clear();
            return self.open_book(book);
        }
        if let Some(url) = id.strip_prefix("repository-remove:") {
            self.remove_repository(url)?;
            return self.open_repository_list();
        }
        if let Some(chapter) = id.strip_prefix("chapter:") {
            self.modal.clear();
            let page = self
                .store
                .chapter_progress(chapter)?
                .filter(|progress| !progress.completed)
                .map(|progress| progress.page)
                .unwrap_or(1);
            return self.load_chapter(chapter, page);
        }
        if let Some(kind) = id.strip_prefix("batch:") {
            if kind == "custom" {
                return self.open_keyboard("batch-range", "");
            }
            if kind == "remove-read" {
                return self.confirm_remove_read_downloads(true);
            }
            return self.enqueue_batch(kind);
        }
        if let Some(job) = id.strip_prefix("job:") {
            let record = self
                .store
                .download(job)?
                .context("The download no longer exists")?;
            self.modal_context = format!("job:{job}");
            if record.state == "completed" {
                return self.show_choices(
                    &tr("downloads"),
                    &format!("job:{job}"),
                    vec![
                        (
                            format!("chapter:{job}"),
                            tr("continue_reading"),
                            String::new(),
                        ),
                        ("job-remove".into(), tr("remove_download"), String::new()),
                        ("close".into(), tr("cancel"), String::new()),
                    ],
                );
            }
            let mut rows = Vec::new();
            if matches!(record.state.as_str(), "queued" | "downloading") {
                rows.push(("job-pause".into(), tr("pause_download"), String::new()));
            }
            if matches!(record.state.as_str(), "paused" | "failed" | "cancelled") {
                rows.push((
                    "job-resume".into(),
                    tr(if record.state == "failed" {
                        "retry"
                    } else {
                        "resume_download"
                    }),
                    String::new(),
                ));
            }
            if record.state != "cancelled" {
                rows.push(("job-cancel".into(), tr("cancel_download"), String::new()));
            }
            return self.show_choices(&tr("downloads"), &format!("job:{job}"), rows);
        }
        if let Some(value) = id.strip_prefix("direction:") {
            self.modal.clear();
            return self.set_preference("reading-direction", value);
        }
        if let Some(value) = id.strip_prefix("layout:") {
            self.modal.clear();
            return self.set_preference("page-layout-mode", value);
        }
        if let Some(value) = id.strip_prefix("library-sort:") {
            self.set_library_sort(value)?;
            return self.open_library_filter();
        }
        if let Some(value) = id.strip_prefix("tap:") {
            self.modal.clear();
            return self.set_preference("tap-layout", value);
        }
        if let Some(value) = id.strip_prefix("refresh:") {
            self.prefs.refresh_every = value.parse()?;
            self.modal.clear();
            return self.save_preferences();
        }
        if let Some(value) = id.strip_prefix("contrast:") {
            self.prefs.contrast = value.parse()?;
            self.modal.clear();
            return self.save_preferences();
        }
        if let Some(value) = id.strip_prefix("category:") {
            let ids: Vec<_> = if self.selected.is_empty() {
                self.current_book_id.iter().cloned().collect()
            } else {
                self.selected.iter().cloned().collect()
            };
            for id in ids {
                self.store.set_category(&id, value)?;
            }
            self.modal.clear();
            self.selected.clear();
            self.selection_mode = false;
            return Ok(());
        }
        if let Some(value) = id.strip_prefix("bookmark:") {
            let (chapter, page) = value.rsplit_once('|').context("Invalid bookmark")?;
            self.modal.clear();
            return self.load_chapter(chapter, page.parse()?);
        }
        match id {
            "close" => self.modal.clear(),
            "import" => self.open_keyboard("local", "")?,
            "pairing" => self.open_pairing()?,
            "repository" => self.open_keyboard("repository", "")?,
            "tracking-settings" => {
                self.modal.clear();
                self.modal_context.clear();
                self.route = "settings".into();
                self.open_setting("tracking")?;
            }
            "downloads" => self.open_downloads()?,
            "clear-cache" => {
                self.modal.clear();
                self.spawn_job(true, |manager| {
                    manager.cache().clear_unpinned()?;
                    Ok(JobResult::CacheCleared)
                })?;
            }
            "cache-info" => {
                let stats = self.disk_cache.stats();
                let limits = crate::cache::CacheConfig::default();
                self.panel_pages = vec![format!(
                    "{}: {} MB\n{}: {} MB\n{}: {} MiB\n{}: {} MiB\n\n{}",
                    tr("cache_usage"),
                    stats.bytes / 1_000_000,
                    tr("cache_pinned"),
                    stats.pinned_bytes / 1_000_000,
                    tr("cache_limit"),
                    limits.max_unpinned_bytes / 1_048_576,
                    tr("cache_reserved"),
                    limits.min_free_bytes / 1_048_576,
                    tr("cache_protected")
                )];
                self.panel_page = 0;
                self.modal = "summary".into();
                self.modal_context = "cache-info".into();
                if let Some(ui) = self.ui.upgrade() {
                    ui.global::<State>()
                        .set_modal_title(tr("cache_usage").into());
                }
            }
            "downloads-pause-all" => {
                self.downloads.pause_all()?;
                self.download_ui_dirty = true;
            }
            "downloads-resume-all" => {
                self.downloads.resume_all()?;
                self.download_ui_dirty = true;
            }
            "downloads-cancel-all" => {
                self.downloads.cancel_all()?;
                self.download_ui_dirty = true;
            }
            "refresh" => {
                self.modal.clear();
                self.refresh_library()?;
            }
            "selection" => {
                self.selected.clear();
                self.selection_mode = true;
                self.modal.clear();
            }
            "manage-categories" => {
                self.route = "settings".into();
                self.open_setting("categories")?;
            }
            "reload" => {
                self.modal.clear();
                self.reload_detail()?;
            }
            "download-next" => {
                self.modal.clear();
                self.download_next_five()?;
            }
            "batch-download" => self.open_batch_downloads()?,
            "remove-all-downloads" => self.confirm_remove_book_downloads()?,
            "migrate" => self.open_migration()?,
            "migration-search" => {
                self.migration_action("retry", "")?;
            }
            "sort-title" => {
                self.set_library_sort("title")?;
                self.modal.clear();
                self.page = 1;
            }
            "sort-recent" => {
                self.set_library_sort("recent-read")?;
                self.modal.clear();
                self.page = 1;
            }
            "unread-only" => {
                self.cycle_library_filter("unread")?;
                self.modal.clear();
                self.page = 1;
            }
            "downloaded-only" => {
                self.cycle_library_filter("downloaded")?;
                self.modal.clear();
                self.page = 1;
            }
            "catalog-unread" => {
                self.catalog_unread_only = !self.catalog_unread_only;
                self.modal.clear();
                self.page = 1;
            }
            "catalog-downloaded" => {
                self.catalog_downloaded_only = !self.catalog_downloaded_only;
                self.modal.clear();
                self.page = 1;
            }
            "select-all" => {
                self.select_visible_books();
                self.modal.clear();
            }
            "remove-selected" => self.remove_selected_books()?,
            "chapter-remove-confirm" => {
                if let Some(chapter) = self.modal_context.strip_prefix("chapter-download:") {
                    self.downloads.remove_completed(chapter)?;
                }
                self.modal.clear();
                self.modal_context.clear();
            }
            "book-downloads-remove-confirm" => {
                if let Some(book_id) = self.modal_context.strip_prefix("book-downloads:") {
                    let book_id = book_id.to_owned();
                    if self.route == "detail"
                        && self.current_book_id.as_deref() == Some(book_id.as_str())
                        && self.store.book_download_count(&book_id)? > 0
                    {
                        self.downloads.remove_book_downloads(&book_id)?;
                    }
                    self.modal.clear();
                    self.modal_context.clear();
                }
            }
            "remove-read-downloads-confirm" => {
                let context = self.modal_context.clone();
                if context == "remove-read-downloads:library"
                    || context.starts_with("remove-read-downloads:book:")
                {
                    let book_id = context.strip_prefix("remove-read-downloads:book:");
                    for chapter in self.store.completed_read_download_ids(book_id)? {
                        self.downloads.remove_completed(&chapter)?;
                    }
                    self.modal.clear();
                    self.modal_context.clear();
                    self.notify(tr("download_removal_queued"));
                }
            }
            "remove-book" => self.show_choices(
                &tr("remove"),
                "remove-book",
                vec![
                    (
                        "remove-book-confirm".into(),
                        tr("remove_book_confirm"),
                        String::new(),
                    ),
                    ("close".into(), tr("cancel"), String::new()),
                ],
            )?,
            "remove-book-confirm" => {
                let book = self.current_book()?;
                self.store.set_library(&book.id, false)?;
                self.modal.clear();
            }
            "category" => {
                self.show_choices(&tr("category"), "category", self.category_choice_rows()?)?
            }
            "bookmarks" => {
                let book = self.current_book()?;
                let rows = self
                    .store
                    .bookmarks(&book.id)?
                    .into_iter()
                    .map(|bookmark| {
                        (
                            format!("bookmark:{}|{}", bookmark.chapter_id, bookmark.page),
                            format!("{} {}", tr("bookmark"), bookmark.page),
                            String::new(),
                        )
                    })
                    .collect();
                self.show_choices(&tr("bookmark"), "bookmarks", rows)?;
            }
            "clear-history-confirm" => {
                self.store.clear_history()?;
                self.modal.clear();
            }
            "job-pause" | "job-resume" | "job-cancel" => {
                if let Some(chapter) = self.modal_context.strip_prefix("job:") {
                    match id {
                        "job-pause" => self.downloads.pause(chapter)?,
                        "job-resume" => self.downloads.resume(chapter)?,
                        _ => self.downloads.cancel(chapter)?,
                    }
                }
                self.open_downloads()?;
            }
            "job-remove" => {
                let context = self.modal_context.clone();
                self.show_choices(
                    &tr("remove_download"),
                    &context,
                    vec![
                        (
                            "job-remove-confirm".into(),
                            tr("remove_download_confirm"),
                            String::new(),
                        ),
                        ("close".into(), tr("cancel"), String::new()),
                    ],
                )?;
            }
            "job-remove-confirm" => {
                if let Some(chapter) = self.modal_context.strip_prefix("job:") {
                    self.downloads.remove_completed(chapter)?;
                }
                self.open_downloads()?;
            }
            "portrait" | "landscape" | "reverse-landscape" => {
                self.modal.clear();
                self.set_preference("orientation", id)?;
            }
            "spread" | "single" => {
                self.modal.clear();
                self.set_preference(
                    "page-layout-mode",
                    if id == "spread" { "double" } else { "single" },
                )?;
            }
            "show-tap-zones" => self.reader_action("tap-zones", "")?,
            "reverse-tap" => {
                let reverse = self.prefs.reverse_taps
                    || matches!(self.prefs.tap_layout.as_str(), "reverse" | "reversed");
                self.set_preference("reverse-taps", if reverse { "false" } else { "true" })?;
            }
            "night-mode" => {
                self.prefs.night_mode = !self.prefs.night_mode;
                self.save_preferences()?;
                self.modal.clear();
            }
            "incognito" => {
                self.prefs.incognito = !self.prefs.incognito;
                self.save_preferences()?;
                self.modal.clear();
            }
            "double-tap-zoom" => {
                self.prefs.double_tap_zoom = !self.prefs.double_tap_zoom;
                self.save_preferences()?;
                self.modal.clear();
            }
            "keep-awake" => {
                self.prefs.keep_awake = !self.prefs.keep_awake;
                self.save_preferences()?;
                self.modal.clear();
            }
            "flash-close" => {
                self.prefs.flash_on_close = !self.prefs.flash_on_close;
                self.save_preferences()?;
                self.modal.clear();
            }
            "crop" => {
                self.prefs.crop_borders = !self.prefs.crop_borders;
                self.save_preferences()?;
                self.modal.clear();
            }
            "dither" => {
                self.prefs.dither = !self.prefs.dither;
                self.save_preferences()?;
                self.modal.clear();
            }
            "refresh-now" => {
                self.modal.clear();
                self.platform.refresh(RefreshKind::Full);
            }
            "save-page" | "set-cover" => {
                self.save_page(id == "set-cover")?;
                self.modal.clear();
            }
            "reload-page" => {
                let session = self.displayed_reader_session()?;
                self.modal.clear();
                self.enter_reader_at(
                    &session.chapter_id,
                    session.page,
                    session.vertical_offset,
                    session.zoom,
                )?;
            }
            "exit-reader" => self.reader_action("back", "")?,
            "exit" => {
                self.platform.announce_exit_intent()?;
                self.shutdown();
                slint::quit_event_loop()?;
            }
            _ => {}
        }
        Ok(())
    }

    fn edit_source_option(&mut self, key: &str) -> Result<()> {
        let setting = self
            .source_settings
            .iter()
            .find(|setting| setting.key == key)
            .cloned()
            .context("This source setting no longer exists")?;
        match setting.kind.as_str() {
            "switch" => {
                self.save_source_option(key, json!(!setting.value.as_bool().unwrap_or(false)))
            }
            "select" | "multi-select" | "segment" | "picker" => {
                let rows = setting
                    .options
                    .iter()
                    .enumerate()
                    .map(|(index, option)| {
                        (
                            format!("sourcevalue:{index}"),
                            option.clone(),
                            check(if setting.kind == "multi-select" {
                                setting.value.as_array().is_some_and(|values| {
                                    values.iter().any(|value| value.as_str() == Some(option))
                                })
                            } else if setting.kind == "segment" {
                                setting.value.as_u64() == Some(index as u64)
                            } else {
                                setting.value.as_str() == Some(option)
                            }),
                        )
                    })
                    .collect();
                self.show_choices(&setting.title, &format!("sourcevalue:{key}"), rows)
            }
            "stepper" => {
                self.save_source_option(key, json!(setting.value.as_f64().unwrap_or(0.0) + 1.0))
            }
            "text" | "secure-text" => self.open_keyboard(
                &format!("sourceopt:{key}"),
                setting.value.as_str().unwrap_or(""),
            ),
            _ => {
                self.notify(tr("source_unsupported"));
                Ok(())
            }
        }
    }

    fn save_source_option(&mut self, key: &str, value: Value) -> Result<()> {
        let source = self.current_source.clone();
        let key = key.to_owned();
        self.spawn_job(true, move |manager| {
            Ok(JobResult::SourceOptionSaved(
                manager.with_source_option(&source, &key, value)?,
            ))
        })
    }

    fn save_page(&mut self, cover: bool) -> Result<()> {
        let session = self.displayed_reader_session()?;
        let page = self
            .store
            .page_window(
                &session.chapter_id,
                session.page.saturating_sub(1) as usize,
                1,
            )?
            .pages
            .into_iter()
            .next()
            .context("The page is not cached")?;
        let source = page
            .local_path
            .context("The page must finish loading first")?;
        let directory = self.data_dir.join(if cover { "covers" } else { "exports" });
        fs::create_dir_all(&directory)?;
        let path = directory.join(format!(
            "{:x}-{}.png",
            Sha256::digest(session.chapter_id.as_bytes()),
            session.page
        ));
        imaging::render_page(source, &ImageOptions::default())?.save(&path)?;
        if cover {
            let mut book = self
                .store
                .book(&session.book_id)?
                .context("The displayed manga is missing")?;
            book.cover = path.to_string_lossy().into();
            self.store.upsert_book(&book)?;
            self.cover_cache
                .retain(|(cached_id, _), _| cached_id != &book.id);
            self.cover_paths.remove(&book.id);
        }
        self.notify(tr("saved"));
        Ok(())
    }

    fn save_preferences(&mut self) -> Result<()> {
        self.save_preferences_with_render(true)
    }

    fn save_preferences_with_render(&mut self, render_images: bool) -> Result<()> {
        self.store.save_preferences(&self.prefs)?;
        self.platform.apply_preferences(&self.prefs)?;
        self.sync_runtime_geometry()?;
        let effective = self.effective_reader_preferences();
        if let Some(reader) = &mut self.reader {
            reader.apply_preferences(&effective);
            if self.route == "reader" && self.modal.is_empty() {
                reader.menu_open = false;
            }
        }
        if self.route == "reader" && render_images {
            self.request_reader_images()?;
        }
        Ok(())
    }

    fn set_preference(&mut self, key: &str, value: &str) -> Result<()> {
        let before = self.effective_reader_preferences();
        let mut interaction_changed = false;
        if !extended_preferences::set(&mut self.prefs, key, value)? {
            match key {
                "reading_direction" | "reading-direction"
                    if matches!(value, "rtl" | "ltr" | "vertical" | "vertical-pages") =>
                {
                    if self.route == "reader" && self.interactions.remember_book_direction {
                        if let Some(id) = self.current_book_id.as_ref() {
                            self.interactions
                                .book_directions
                                .insert(id.clone(), value.into());
                            interaction_changed = true;
                        } else {
                            self.prefs.reading_direction = value.into();
                        }
                    } else {
                        self.prefs.reading_direction = value.into();
                    }
                }
                "tap_layout" | "tap-layout"
                    if matches!(
                        value,
                        "thirds" | "l-shaped" | "swipe-only" | "reverse" | "reversed"
                    ) =>
                {
                    let reverse = matches!(self.prefs.tap_layout.as_str(), "reverse" | "reversed");
                    if matches!(value, "reverse" | "reversed") {
                        self.prefs.tap_layout = "thirds".into();
                        self.prefs.reverse_taps = true;
                    } else {
                        self.prefs.tap_layout = value.into();
                        self.prefs.reverse_taps |= reverse;
                    }
                }
                "reverse_taps" | "reverse-taps" => {
                    let reverse: bool = value.parse()?;
                    if matches!(self.prefs.tap_layout.as_str(), "reverse" | "reversed") {
                        self.prefs.tap_layout = "thirds".into();
                    }
                    self.prefs.reverse_taps = reverse;
                }
                "cover_alone" | "cover-alone" => self.prefs.cover_alone = value.parse()?,
                "refresh_every" | "refresh-every" => {
                    let count = value.parse()?;
                    if ![0, 1, 3, 6, 10].contains(&count) {
                        bail!("Unsupported refresh interval");
                    }
                    self.prefs.refresh_every = count;
                }
                "incognito" => self.prefs.incognito = value.parse()?,
                "night_mode" | "night-mode" => self.prefs.night_mode = value.parse()?,
                "crop_borders" | "crop-borders" => self.prefs.crop_borders = value.parse()?,
                "dither" => self.prefs.dither = value.parse()?,
                "contrast" => {
                    let contrast: i32 = value.parse()?;
                    if !(-2..=2).contains(&contrast) {
                        bail!("Unsupported contrast step");
                    }
                    self.prefs.contrast = contrast * 10;
                }
                "flash_on_close" | "flash-on-close" => self.prefs.flash_on_close = value.parse()?,
                "landscape" => {
                    self.prefs.landscape = value.parse()?;
                    self.prefs.reverse_landscape = false;
                }
                "reverse_landscape" | "reverse-landscape" => {
                    self.prefs.reverse_landscape = value.parse()?;
                    if self.prefs.reverse_landscape {
                        self.prefs.landscape = true;
                    }
                }
                "orientation"
                    if matches!(value, "portrait" | "landscape" | "reverse-landscape") =>
                {
                    self.prefs.landscape = value != "portrait";
                    self.prefs.reverse_landscape = value == "reverse-landscape";
                }
                "spread" => {
                    self.prefs.spread = value.parse()?;
                    self.interactions.page_layout_mode = if self.prefs.spread {
                        "double"
                    } else {
                        "single"
                    }
                    .into();
                    interaction_changed = true;
                }
                "page_layout_mode" | "page-layout-mode"
                    if matches!(value, "single" | "double" | "automatic") =>
                {
                    self.interactions.page_layout_mode = value.into();
                    self.prefs.spread = value != "single";
                    interaction_changed = true;
                }
                "remember_book_direction" | "remember-book-direction" => {
                    self.interactions.remember_book_direction = value.parse()?;
                    interaction_changed = true;
                }
                "show_tap_zones" | "show-tap-zones" => {
                    self.interactions.show_tap_zones = value.parse()?;
                    interaction_changed = true;
                }
                "double_tap_zoom" | "double-tap-zoom" => {
                    self.prefs.double_tap_zoom = value.parse()?
                }
                "keep_awake" | "keep-awake" => self.prefs.keep_awake = value.parse()?,
                "brightness" if self.platform.profile().has_frontlight() => {
                    self.prefs.brightness = value.parse::<u8>()?.min(24)
                }
                "warmth" if self.platform.profile().has_warmth() => {
                    self.prefs.warmth = value.parse::<u8>()?.min(24)
                }
                _ => bail!("Unknown or unsupported preference {key}"),
            }
        }
        if interaction_changed {
            self.interactions.save(&self.store)?;
        }
        let after = self.effective_reader_preferences();
        if before.write_logs != after.write_logs {
            self.data_management.logs.set_enabled(after.write_logs)?;
        }
        if before.tracking_auto_sync && !after.tracking_auto_sync {
            self.cancel_tracking_work();
        }
        if before.auto_update_interval != after.auto_update_interval
            || before.update_wifi_only != after.update_wifi_only
            || before.update_charging_only != after.update_charging_only
            || before.update_skip_completed != after.update_skip_completed
            || before.charging_updates != after.charging_updates
            || before.ahead_chapters != after.ahead_chapters
        {
            self.invalidate_automatic_updates();
        }
        let input_changed = before.reading_direction != after.reading_direction
            || before.tap_layout != after.tap_layout
            || before.reverse_taps != after.reverse_taps
            || before.double_tap_zoom != after.double_tap_zoom
            || before.landscape != after.landscape
            || before.reverse_landscape != after.reverse_landscape
            || before.spread != after.spread
            || before.cover_alone != after.cover_alone
            || before.split_wide != after.split_wide;
        if input_changed {
            self.pending_wide_navigation = 0;
            if let Some(ui) = self.ui.upgrade() {
                let state = ui.global::<State>();
                state.set_reader_input_epoch(state.get_reader_input_epoch().wrapping_add(1));
            }
        }
        let render_images = before.reading_direction != after.reading_direction
            || before.landscape != after.landscape
            || before.spread != after.spread
            || before.cover_alone != after.cover_alone
            || before.split_wide != after.split_wide
            || before.crop_borders != after.crop_borders
            || before.dither != after.dither
            || before.contrast != after.contrast;
        self.save_preferences_with_render(render_images)
    }
}

fn license_pages(large_screen: bool) -> Vec<String> {
    text_pagination::paginate_text(BUNDLED_LICENSE_TEXT, if large_screen { 700 } else { 320 })
}

fn check(value: bool) -> String {
    if value { "✓".into() } else { String::new() }
}

fn readable_bytes(bytes: u64) -> String {
    if bytes >= 1_000_000_000 {
        format!("{:.1} GB", bytes as f64 / 1_000_000_000.0)
    } else if bytes >= 1_000_000 {
        format!("{:.1} MB", bytes as f64 / 1_000_000.0)
    } else if bytes >= 1_000 {
        format!("{:.1} KB", bytes as f64 / 1_000.0)
    } else {
        format!("{bytes} B")
    }
}

fn remembered_listings(config: &SourceConfig, demo: bool) -> Vec<(String, String)> {
    if let Some(value) = config.options.get("_cached_listings") {
        if let Ok(listings) = serde_json::from_value::<Vec<(String, String)>>(value.clone()) {
            if !listings.is_empty() {
                return listings;
            }
        }
    }
    if config.kind == "wasm" && !demo {
        return vec![("cached".into(), tr("all"))];
    }
    vec![
        ("popular".into(), tr("popular")),
        ("latest".into(), tr("recently_updated")),
        (
            if config.kind == "mangadex" {
                "new"
            } else {
                "title"
            }
            .into(),
            tr(if config.kind == "mangadex" {
                "newly_added"
            } else {
                "sort_title"
            }),
        ),
    ]
}
fn display_value(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        Value::Null => String::new(),
        _ => value.to_string(),
    }
}
fn download_state(value: &str) -> String {
    tr(match value {
        "queued" => "queued",
        "downloading" => "downloading",
        "completed" => "download_complete",
        "failed" => "download_failed",
        "paused" => "pause_download",
        _ => "cancel_download",
    })
}

#[cfg(test)]
mod license_tests {
    use super::*;

    #[test]
    fn license_view_pages_cover_the_complete_project_license() {
        let project_license = include_str!("../../LICENSE");
        assert_eq!(BUNDLED_LICENSE_TEXT, project_license);

        for large_screen in [false, true] {
            let pages = license_pages(large_screen);
            assert!(pages.len() > 1);
            assert!(pages.iter().all(|page| !page.is_empty()));
            assert_eq!(pages.concat(), project_license);
        }
    }
}

#[cfg(test)]
mod offline_listing_tests {
    use super::*;

    #[test]
    fn offline_catalog_preserves_provider_defined_listing_ids_and_names() {
        let mut config = SourceConfig {
            kind: "wasm".into(),
            ..SourceConfig::default()
        };
        let cached = vec![(
            "featured-special".to_owned(),
            "Featured collection".to_owned(),
        )];
        config
            .options
            .insert("_cached_listings".into(), json!(cached));
        assert_eq!(remembered_listings(&config, false), cached);
        config.options.clear();
        assert_eq!(
            remembered_listings(&config, false),
            vec![("cached".into(), tr("all"))]
        );
        config.kind = "komga".into();
        assert_eq!(remembered_listings(&config, false)[2].0, "title");
    }
}

#[cfg(test)]
mod followup_command_tests {
    use super::*;
    use crate::{app::tests::DemoRouteFixture, platform::DeviceProfile};

    fn finish_data_operation(app: &mut App) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while app.data_management.inflight.is_some() && Instant::now() < deadline {
            app.poll();
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(
            app.data_management.inflight.is_none(),
            "The data operation did not finish"
        );
    }

    #[test]
    fn a_turn_before_wide_geometry_is_ready_enters_the_second_half() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw6, false);
        let app = &mut fixture.app;
        app.route = "reader".into();
        app.prefs.split_wide = true;
        app.interactions.page_layout_mode = "single".into();
        let chapter = Chapter {
            id: "pending-wide".into(),
            book_id: "book".into(),
            ..Chapter::default()
        };
        let mut reader = ReaderState::new(
            vec![chapter],
            "pending-wide",
            1,
            &app.effective_reader_preferences(),
        )
        .unwrap();
        reader.set_total(2);
        app.reader = Some(reader);
        app.reader_inflight = Some(42);
        app.reader_action("tap", "0.1,0.5").unwrap();
        assert_eq!(app.reader.as_ref().unwrap().page, 1);
        assert_eq!(app.pending_wide_navigation, 1);
        app.reader.as_mut().unwrap().set_wide_page(true, 0);
        let update = app.take_pending_wide_update().unwrap();
        assert_eq!(update.action, crate::reader::ReaderAction::PageChanged);
        let reader = app.reader.as_ref().unwrap();
        assert_eq!((reader.page, reader.wide_half), (1, 1));
        assert_eq!(app.pending_wide_navigation, 0);
    }

    #[test]
    fn pending_wide_turns_are_bounded_and_opposite_input_cancels() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw6, false);
        let app = &mut fixture.app;
        app.prefs.split_wide = true;
        app.interactions.page_layout_mode = "single".into();
        let chapter = Chapter {
            id: "pending-wide".into(),
            ..Chapter::default()
        };
        app.reader = Some(
            ReaderState::new(
                vec![chapter],
                "pending-wide",
                1,
                &app.effective_reader_preferences(),
            )
            .unwrap(),
        );
        app.reader_inflight = Some(42);
        assert!(app.defer_wide_navigation(1));
        assert!(app.defer_wide_navigation(-1));
        assert_eq!(app.pending_wide_navigation, 0);
        for _ in 0..100 {
            assert!(app.defer_wide_navigation(1));
        }
        assert_eq!(app.pending_wide_navigation, 16);
        app.reader.as_mut().unwrap().menu_open = true;
        assert!(!app.defer_wide_navigation(1));
        assert!(!app.replay_pending_wide_navigation().unwrap());
        assert_eq!(app.pending_wide_navigation, 0);
    }

    #[test]
    fn leaving_the_reader_cancels_a_deferred_network_destination() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw6, false);
        fixture.app.route = "reader".into();
        fixture.app.pending_user_network = Some(PendingUserNetwork {
            intent: UserNetworkIntent::Reader(ReaderTarget::new("deferred-chapter", 1, 0, 1.0)),
            expires: Instant::now() + Duration::from_secs(45),
        });
        fixture.app.dispatch("reader", "back", "").unwrap();
        assert!(fixture.app.pending_user_network.is_none());
        assert_eq!(fixture.app.route, "detail");
    }

    #[test]
    fn settings_keyboard_and_category_choices_edit_the_actual_book() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw6, false);
        let app = &mut fixture.app;
        let book = Book {
            id: "command-book".into(),
            source_id: "local".into(),
            remote_id: "command-book".into(),
            title: "Command fixture".into(),
            in_library: true,
            ..Book::default()
        };
        app.store.upsert_book(&book).unwrap();
        app.current_book_id = Some(book.id.clone());
        app.dispatch("settings", "category-create", "").unwrap();
        app.keyboard.set_text("Favorites");
        app.dispatch("keyboard", "done", "").unwrap();
        assert!(app.modal.is_empty());
        app.dispatch("panel", "choose", "category").unwrap();
        assert!(
            app.choice_rows
                .iter()
                .any(|row| row.0 == "category:Favorites")
        );
        app.dispatch("panel", "choose", "category:Favorites")
            .unwrap();
        assert_eq!(
            app.store.book(&book.id).unwrap().unwrap().category,
            "Favorites"
        );
        app.dispatch("settings", "category-rename", "Favorites")
            .unwrap();
        app.keyboard.set_text("Archive");
        app.dispatch("keyboard", "done", "").unwrap();
        assert_eq!(
            app.store.book(&book.id).unwrap().unwrap().category,
            "Archive"
        );
    }

    #[test]
    fn extended_settings_commands_persist_and_reject_invalid_values() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Scribe, false);
        let app = &mut fixture.app;
        for setting in [
            "ahead-chapters=2",
            "resize-downloads=true",
            "split-wide=true",
            "write-logs=true",
        ] {
            app.dispatch("settings", "set", setting).unwrap();
        }
        let preferences = app.store.preferences().unwrap();
        assert_eq!(preferences.ahead_chapters, 2);
        assert!(preferences.resize_downloads && preferences.split_wide && preferences.write_logs);
        assert!(app.dispatch("settings", "set", "ahead-chapters=4").is_err());
        assert_eq!(app.store.preferences().unwrap(), preferences);
    }

    #[test]
    fn backup_and_log_buttons_complete_real_background_file_operations() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw5, false);
        let app = &mut fixture.app;
        app.dispatch("settings", "set", "write-logs=true").unwrap();
        app.dispatch("settings", "backup-create", "").unwrap();
        finish_data_operation(app);
        assert_eq!(app.data_management.backups.len(), 1);
        let info = &app.data_management.backups[0];
        assert!(
            app.data_management
                .manager
                .prepare_restore(&info.id)
                .is_ok()
        );
        app.dispatch("settings", "logs-export", "").unwrap();
        finish_data_operation(app);
        let export = app.data_management.last_export.as_ref().unwrap();
        let records = fs::read_to_string(export).unwrap();
        assert!(records.contains("backup.created"));
        assert!(!records.contains("access_token"));
    }
}
