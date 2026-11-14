//! Read-only capture diagnostics projected from UI models and in-memory state.

use super::*;

use crate::ui::{BookItem, SettingItem, SourceItem};

fn projected_rows<T: Clone + 'static>(
    rows: slint::ModelRc<T>,
    project: impl Fn(T) -> Value,
) -> Value {
    json!(
        (0..rows.row_count())
            .filter_map(|row| rows.row_data(row))
            .map(project)
            .collect::<Vec<_>>()
    )
}

fn projected_book(item: BookItem) -> Value {
    let identifier = item.id.to_string();
    let source_id = identifier
        .split_once(':')
        .and_then(|(length, remainder)| {
            length
                .parse::<usize>()
                .ok()
                .and_then(|length| remainder.get(..length))
        })
        .unwrap_or_default()
        .to_owned();
    json!({
        "id": identifier,
        "source_id": source_id,
        "title": item.title.to_string(),
        "author": item.author.to_string(),
        "subtitle": item.subtitle.to_string(),
        "status": item.status.to_string(),
        "unread": item.unread,
        "downloaded": item.downloaded,
        "in_library": item.in_library,
        "progress": item.progress,
        "selected": item.selected,
    })
}

fn projected_setting(item: SettingItem) -> Value {
    json!({
        "id": item.id.to_string(),
        "title": item.title.to_string(),
        "value": item.value.to_string(),
        "group": item.group.to_string(),
        "enabled": item.enabled,
    })
}

fn projected_source(item: SourceItem) -> Value {
    json!({
        "id": item.id.to_string(),
        "name": item.name.to_string(),
        "display_name": item.display_name.to_string(),
        "compact_status": item.compact_status.to_string(),
        "update_subtitle": item.update_subtitle.to_string(),
        "subtitle": item.subtitle.to_string(),
        "available": item.available,
        "installed": item.installed,
        "update": item.update,
        "builtin": item.builtin,
    })
}

impl App {
    /// Report outstanding cover work without touching storage or loading assets.
    pub fn cover_work_pending(&self) -> bool {
        self.cover_inflight.is_some() || !self.cover_queue.is_empty()
    }

    /// A completed cover may still await the throttled view publication.
    pub fn capture_projection_pending(&self) -> bool {
        self.cover_work_pending() || self.background_ui_dirty
    }

    /// Extend capture evidence without querying storage, files, or a provider.
    pub(super) fn extend_diagnostics_snapshot(&self, snapshot: &mut Value) {
        let Some(ui) = self.ui.upgrade() else {
            snapshot["diagnostics_available"] = json!(false);
            return;
        };
        let state = ui.global::<State>();
        let secret_input = self.server_keyboard_is_secret()
            || self
                .source_settings
                .iter()
                .find(|setting| format!("sourceopt:{}", setting.key) == self.keyboard_target)
                .is_some_and(|setting| setting.kind == "secure-text");
        snapshot["diagnostics_available"] = json!(true);
        snapshot["diagnostics_schema_version"] = json!(1);
        snapshot["performance_timing_enabled"] = json!(crate::reader_timing::enabled());
        snapshot["interface_language"] = json!(
            ui.global::<crate::ui::Strings>()
                .get_locale_code()
                .to_string()
        );
        snapshot["interface_library_label"] =
            json!(ui.global::<crate::ui::Strings>().get_library().to_string());
        snapshot["reader_wide_page"] =
            json!(self.reader.as_ref().map(|reader| reader.is_wide_page()));
        snapshot["reader_wide_half"] = json!(self.reader.as_ref().map(|reader| reader.wide_half));
        snapshot["pending_wide_navigation"] = json!(self.pending_wide_navigation);
        snapshot["cover_work_pending"] = json!(self.cover_work_pending());
        snapshot["capture_projection_pending"] = json!(self.capture_projection_pending());
        snapshot["cover_inflight"] = json!(self.cover_inflight.is_some());
        snapshot["cover_queued_count"] = json!(self.cover_queue.len());
        snapshot["cover_pending_count"] = json!(self.cover_pending.len());
        snapshot["cover_visible_count"] = json!(self.cover_view.len());
        snapshot["cover_visible_failure_count"] = json!(
            self.cover_view
                .iter()
                .filter(|id| self.cover_retry_after.contains_key(*id))
                .count()
        );
        snapshot["display_landscape"] = json!(state.get_landscape());
        snapshot["reader_landscape_spread"] = json!(state.get_reader_landscape_spread());
        snapshot["display_rotation_degrees"] = json!(self.platform.display_rotation_degrees());
        snapshot["cover_alone"] = json!(self.prefs.cover_alone);
        snapshot["reverse_landscape"] = json!(self.prefs.reverse_landscape);
        snapshot["visible_cover_alone"] = json!(state.get_cover_alone());
        snapshot["visible_reverse_landscape"] = json!(state.get_reverse_landscape());
        snapshot["visible_reverse_taps"] = json!(state.get_reverse_taps());
        snapshot["reverse_taps"] = json!(
            self.prefs.reverse_taps
                || matches!(self.prefs.tap_layout.as_str(), "reverse" | "reversed")
        );
        snapshot["reader_visible_pages"] = json!(
            self.reader
                .as_ref()
                .map(|reader| reader.visible_pages().collect::<Vec<_>>())
                .unwrap_or_default()
        );
        snapshot["storage_details"] = json!(self.storage_details.snapshot());
        snapshot["storage_ready"] = json!(state.get_storage_ready());
        snapshot["storage_loading"] = json!(state.get_storage_loading());
        snapshot["storage_status_code"] = json!(state.get_storage_status_code().to_string());
        snapshot["storage_status"] = json!(state.get_storage_status().to_string());
        snapshot["storage_scale_known"] = json!(state.get_storage_scale_known());
        snapshot["storage_categories"] = json!({
            "downloaded": {"known":state.get_storage_downloaded_known(), "text":state.get_storage_downloaded_text().to_string(), "fraction":state.get_storage_downloaded_fraction()},
            "image_cache": {"known":state.get_storage_image_cache_known(), "text":state.get_storage_image_cache_text().to_string(), "fraction":state.get_storage_image_cache_fraction()},
            "covers": {"known":state.get_storage_covers_known(), "text":state.get_storage_covers_text().to_string(), "fraction":state.get_storage_covers_fraction()},
            "free": {"known":state.get_storage_free_known(), "text":state.get_storage_free_text().to_string(), "fraction":state.get_storage_free_fraction()},
        });
        snapshot["storage_books"] = projected_rows(state.get_storage_books(), projected_setting);
        snapshot["storage_book_count"] = json!(state.get_storage_book_count());
        snapshot["storage_books_status"] = json!(state.get_storage_books_status().to_string());
        snapshot["book_removal_completions"] = json!(self.book_removal_completions);
        snapshot["last_book_removal"] = json!(self.last_book_removal);
        snapshot["large_screen"] = json!(state.get_large_screen());
        snapshot["settings_section"] = json!(state.get_settings_section().to_string());
        snapshot["detail_filter"] = json!(state.get_detail_filter());
        snapshot["bookmark_edit"] = json!(state.get_bookmark_edit());
        snapshot["detail_bookmarks"] =
            projected_rows(state.get_detail_bookmarks(), projected_setting);
        snapshot["settings_details"] =
            projected_rows(state.get_settings_details(), projected_setting);
        snapshot["settings_details_page"] = json!(state.get_settings_details_page());
        snapshot["settings_details_page_count"] = json!(state.get_settings_details_page_count());
        snapshot["settings_status"] = json!(state.get_settings_status().to_string());
        snapshot["page_jump_open"] = json!(state.get_page_jump_open());
        snapshot["page_jump_context"] = json!(state.get_page_jump_context().to_string());

        snapshot["library_filter"] = json!(state.get_library_filter());
        snapshot["library_sort"] = json!(state.get_library_sort().to_string());
        snapshot["library_ascending"] = json!(state.get_library_ascending());
        snapshot["library_filters"] =
            projected_rows(state.get_library_filters(), |value| json!(value));
        snapshot["selection_mode"] = json!(state.get_selection_mode());
        snapshot["visible_books"] = projected_rows(state.get_books(), projected_book);
        snapshot["reading_count"] = json!(state.get_reading_count());
        snapshot["planned_count"] = json!(state.get_planned_count());
        snapshot["completed_count"] = json!(state.get_completed_count());
        snapshot["updated_count"] = json!(state.get_updated_count());
        snapshot["chapter_update_count"] = json!(state.get_chapter_update_count());
        snapshot["library_download_size"] = json!(state.get_library_download_size().to_string());

        snapshot["visible_chapters"] = projected_rows(state.get_chapters(), |item| {
            json!({
                "id": item.id.to_string(), "number": item.number.to_string(),
                "title": item.title.to_string(), "date": item.date.to_string(),
                "read": item.read, "downloaded": item.downloaded,
                "download_state": item.download_state.to_string(),
                "download_progress": item.download_progress,
                "current": item.current, "progress": item.progress.to_string(),
                "selected": item.selected,
            })
        });
        snapshot["chapter_count"] = json!(state.get_chapter_count());
        snapshot["unread_count"] = json!(state.get_unread_count());
        snapshot["downloaded_count"] = json!(state.get_downloaded_count());
        snapshot["newest_first"] = json!(state.get_newest_first());
        snapshot["chapter_selection_mode"] = json!(state.get_chapter_selection_mode());
        snapshot["selected_chapter_count"] = json!(state.get_selected_chapter_count());
        snapshot["book_tag_items"] =
            projected_rows(state.get_book_tag_items(), |value| json!(value.to_string()));
        snapshot["book_details"] = projected_rows(state.get_book_details(), projected_setting);
        snapshot["panel_text"] = json!(state.get_panel_text().to_string());

        snapshot["source_filter"] = json!(state.get_source_filter());
        snapshot["source_language"] = json!(state.get_source_language().to_string());
        snapshot["source_available"] = json!(state.get_source_available());
        snapshot["source_builtin"] = json!(state.get_source_builtin());
        snapshot["source_needs_setup"] = json!(state.get_source_needs_setup());
        snapshot["source_server_address"] = json!(state.get_source_server_address().to_string());
        // The configuration projection already replaces every credential with a mask.
        snapshot["source_server_credential"] =
            json!(state.get_source_server_credential().to_string());
        snapshot["source_connection_status"] =
            json!(state.get_source_connection_status().to_string());
        snapshot["visible_sources"] = projected_rows(state.get_sources(), projected_source);
        snapshot["available_sources"] =
            projected_rows(state.get_available_sources(), projected_source);
        snapshot["installed_sources"] =
            projected_rows(state.get_installed_sources(), projected_source);
        snapshot["picker_sources"] = projected_rows(state.get_picker_sources(), projected_source);
        snapshot["installed_source_page"] = json!(state.get_installed_source_page());
        snapshot["installed_source_page_count"] = json!(state.get_installed_source_page_count());
        snapshot["installed_count"] = json!(state.get_installed_count());
        snapshot["available_count"] = json!(state.get_available_count());
        snapshot["source_update_count"] = json!(state.get_source_update_count());
        snapshot["source_preference_groups"] =
            projected_rows(state.get_source_preference_groups(), |item| {
                let secure = item.kind.as_str() == "secure-text";
                json!({
                    "id": item.id.to_string(), "title": item.title.to_string(),
                    "kind": item.kind.to_string(), "enabled": item.enabled,
                    "value": if secure { String::new() } else { item.value.to_string() },
                    "redacted": secure,
                    "options": projected_rows(item.options, projected_setting),
                })
            });
        snapshot["visible_catalog"] = projected_rows(state.get_catalog(), projected_book);
        snapshot["catalog_filter"] = json!(state.get_catalog_filter());
        snapshot["catalog_result_count"] = json!(state.get_catalog_result_count());
        snapshot["catalog_filter_result_count"] = json!(state.get_catalog_filter_result_count());
        snapshot["catalog_active_filter_count"] = json!(state.get_catalog_active_filter_count());
        snapshot["catalog_sort_options"] =
            projected_rows(state.get_catalog_sort_options(), projected_setting);
        snapshot["catalog_status_options"] =
            projected_rows(state.get_catalog_status_options(), projected_setting);
        snapshot["catalog_tag_options"] = projected_rows(state.get_catalog_tag_options(), |item| {
            json!({
                "id": item.id.to_string(), "title": item.title.to_string(), "state": item.state,
            })
        });

        snapshot["history_filter"] = json!(state.get_history_filter());
        snapshot["visible_history"] = projected_rows(state.get_history(), |item| {
            json!({
                "id": item.id.to_string(), "chapter_id": item.chapter_id.to_string(),
                "title": item.title.to_string(), "subtitle": item.subtitle.to_string(),
                "progress": item.progress.to_string(), "date": item.date.to_string(),
                "date_label": item.date_label.to_string(), "date_value": item.date_value.to_string(),
                "fraction": item.fraction, "completed": item.completed,
                "group_start": item.group_start, "column": item.column,
                "row_index": item.row_index, "group_row_count": item.group_row_count,
                "group_offset": item.group_offset,
                "download_state": item.download_state.to_string(),
                "download_progress": item.download_progress,
            })
        });
        snapshot["search_groups"] = projected_rows(state.get_search_groups(), |item| {
            json!({
                "id": item.id.to_string(), "title": item.title.to_string(),
                "status": item.status.to_string(), "error": item.error,
                "pending": item.pending, "error_detail": item.error_detail.to_string(),
                "count": item.count, "books": projected_rows(item.books, projected_book),
            })
        });
        snapshot["search_group_states"] = json!(
            self.search_results
                .iter()
                .map(|group| {
                    let timeout = group.error.as_ref().is_some_and(|error| {
                        let error = error.to_ascii_lowercase();
                        error.contains("timeout") || error.contains("timed out")
                    });
                    json!({
                        "id": group.id, "title": group.title, "pending": group.pending,
                        "error": group.error.is_some(), "timeout": timeout,
                        "count": group.books.len(), "has_next": group.has_next,
                        "book_ids": group.books.iter().map(|book| &book.id).collect::<Vec<_>>(),
                    })
                })
                .collect::<Vec<_>>()
        );
        snapshot["search_sources"] = projected_rows(state.get_search_sources(), projected_source);
        snapshot["search_local_matches"] =
            projected_rows(state.get_search_local_matches(), projected_book);
        snapshot["search_status"] = json!(state.get_search_status().to_string());
        snapshot["keyboard_target"] = json!(state.get_keyboard_target().to_string());
        snapshot["keyboard_mode"] = json!(state.get_keyboard_mode().to_string());
        snapshot["keyboard_is_secret"] = json!(secret_input);
        snapshot["keyboard_text"] = json!(if secret_input {
            "\u{2022}".repeat(self.keyboard.text().chars().count())
        } else {
            state.get_keyboard_text().to_string()
        });
        snapshot["keyboard_composition"] = json!(if secret_input {
            String::new()
        } else {
            state.get_keyboard_composition().to_string()
        });
        snapshot["keyboard_candidates"] = if secret_input {
            json!([])
        } else {
            projected_rows(state.get_keyboard_candidates(), |value| {
                json!(value.to_string())
            })
        };
        snapshot["keyboard_candidate_page"] = json!(if secret_input {
            1
        } else {
            state.get_keyboard_candidate_page()
        });
        snapshot["keyboard_candidate_page_count"] = json!(if secret_input {
            1
        } else {
            state.get_keyboard_candidate_page_count()
        });

        snapshot["download_queue"] = projected_rows(state.get_download_queue(), |item| {
            json!({
                "id": item.id.to_string(), "book_id": item.book_id.to_string(),
                "title": item.title.to_string(), "chapter": item.chapter.to_string(),
                "progress": item.progress, "state": item.state.to_string(),
                "status": item.status.to_string(), "completed": item.completed, "total": item.total,
            })
        });
        snapshot["download_queue_count"] = json!(state.get_download_queue_count());
        snapshot["downloads_paused"] = json!(state.get_downloads_paused());
        snapshot["download_queue_can_pause"] = json!(state.get_download_queue_can_pause());
        snapshot["download_queue_can_resume"] = json!(state.get_download_queue_can_resume());

        snapshot["migration_candidates"] =
            projected_rows(state.get_migration_candidates(), projected_book);
        snapshot["migration_selected_id"] = json!(state.get_migration_selected_id().to_string());
        snapshot["migration_available"] = json!(state.get_migration_available());
        snapshot["migration_loading"] = json!(state.get_migration_loading());
        snapshot["migration_committing"] = json!(state.get_migration_committing());
        snapshot["migration_preserve_read"] = json!(state.get_migration_preserve_read());
        snapshot["migration_preserve_category"] = json!(state.get_migration_preserve_category());
        snapshot["migration_remove_downloads"] = json!(state.get_migration_remove_downloads());
        snapshot["migration_can_start"] = json!(state.get_migration_can_start());
        snapshot["migration_status"] = json!(state.get_migration_status().to_string());

        snapshot["reader_panel"] = json!(state.get_reader_panel().to_string());
        snapshot["reader_toc_tab"] = json!(state.get_reader_toc_tab().to_string());
        snapshot["show_tap_zones"] = json!(state.get_show_tap_zones());
        snapshot["reader_image_ready"] = json!(state.get_reader_image_ready());
        snapshot["reader_image_height"] =
            json!(self.reader.as_ref().map(|reader| reader.image_height));
        snapshot["reader_viewport_height"] =
            json!(self.reader.as_ref().map(|reader| reader.viewport_height));
        snapshot["reading_direction"] = json!(state.get_reading_direction().to_string());
        snapshot["page_layout_mode"] = json!(state.get_page_layout_mode().to_string());
        snapshot["remember_book_direction"] = json!(state.get_remember_book_direction());
        snapshot["orientation_mode"] = json!(state.get_orientation_mode().to_string());
        snapshot["orientation_locked"] = json!(state.get_orientation_locked());
        snapshot["reader_has_next_chapter"] = json!(state.get_reader_has_next_chapter());
        snapshot["reader_has_previous_chapter"] = json!(state.get_reader_has_previous_chapter());
        snapshot["capability_declaration"] = json!({
            "scope": "selected design profile and implemented application controls",
            "profile": self.platform.profile().id(),
            "backend": if self.platform.is_kindle() { "kindle" } else { "desktop" },
            "physical_hardware_qualified": false,
            "frontlight_control_exposed": state.get_has_frontlight(),
            "warmth_control_exposed": state.get_has_warmth(),
            "orientation_profile_declared": state.get_has_orientation(),
            "software_dither": true,
            "hardware_dither_qualified": false,
            "pen_input_implemented": false,
            "tracking_authorization_implemented": true,
            "tracking_authorization_scope": "Configured token or official OAuth credentials; account acceptance requires separate credentials and live-service checks",
            "server_configuration_implemented": true,
            "pairing_endpoint_running": self.pairing.is_some(),
        });
    }
}
