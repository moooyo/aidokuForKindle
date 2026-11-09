use super::storage_details::StorageDetailsSnapshot;
use super::*;

use crate::domain::DownloadJob;
use crate::store::view_queries::{
    BookViewMetadata, LibraryCounts, LibraryQuery, detail_chapter_page_count,
};
use crate::ui::{BookItem, ChapterItem, HistoryItem, SearchGroup, SettingItem, SourceItem};
use chrono::{Datelike, TimeZone};
use slint::Model;

#[derive(Debug, PartialEq)]
struct ReaderViewMetadata {
    book: Option<Book>,
    progress: Option<Progress>,
    bookmarked: bool,
    next_downloaded: bool,
    projection: ReaderViewProjection,
}

#[derive(Debug, PartialEq)]
struct ReaderViewProjection {
    book_id: String,
    chapter_id: String,
    chapter_number: String,
    chapter_title: String,
    chapter_read: bool,
    image_page: u32,
    label_page: u32,
    total: u32,
}

impl App {
    pub(crate) fn sync_view(&mut self) -> Result<()> {
        self.sync_storage_details_visibility();
        self.sync_runtime_geometry()?;
        let Some(ui) = self.ui.upgrade() else {
            return Ok(());
        };
        let state = ui.global::<State>();
        self.sync_base_view(&state);
        if self.route == "reader" {
            self.sync_reader_view(&state)?;
            self.page = 1;
            state.set_page(1);
            state.set_page_count(1);
            self.sync_overlay_view(&state);
            return Ok(());
        }
        let large = self.platform.profile().is_scribe();
        let show_library = self.route == "library";
        let show_catalog = self.route == "catalog";
        let show_history = self.route == "history";
        let show_search = self.route == "search";
        let show_settings = self.route == "settings";
        let show_current_cover = show_library || self.route == "detail";
        let context = load_non_reader_context(&self.store, &self.route, large, self.options.demo)?;
        if context.has_source_snapshot {
            self.capture_server_configurations(&context.sources);
            self.sync_server_configuration_state();
        }
        let sources = context.sources;
        let today = view_today(self.options.demo);
        if self.route == "library" && !self.selection_mode {
            self.current_book_id = self
                .store
                .library_continuation(self.current_book_id.as_deref())?;
        }

        if let Some(counts) = context.library_counts {
            state.set_library_count(counts.total as i32);
            state.set_reading_count(counts.reading as i32);
            state.set_planned_count(counts.planned as i32);
            state.set_completed_count(counts.completed as i32);
            state.set_updated_count(if self.options.demo {
                counts.total.min(7) as i32
            } else {
                counts.updated as i32
            });
        }
        if show_history || show_library && large {
            state.set_chapter_update_count(context.daily_update_count as i32);
        }
        if show_history {
            state.set_weekly_read_count(context.weekly_read_count as i32);
        }
        state.set_library_filter(self.library_filter);
        state.set_source_filter(self.source_filter);
        state.set_catalog_filter(self.catalog_filter);
        state.set_catalog_has_more(self.catalog_has_more);
        let catalog_tabs: Vec<_> = if self.options.demo {
            ["popular", "latest", "new"]
                .into_iter()
                .map(|id| listing_label(id, id).into())
                .collect()
        } else {
            self.catalog_listings
                .iter()
                .map(|(id, label)| listing_label(id, label).into())
                .collect()
        };
        state.set_catalog_tabs(model(catalog_tabs));
        state.set_history_filter(self.history_filter);
        state.set_selection_mode(self.selection_mode);
        state.set_chapter_selection_mode(!self.selected_chapters.is_empty());
        state.set_selected_chapter_count(self.selected_chapters.len() as i32);
        let mut selected: Vec<_> = self.selected.iter().cloned().collect();
        selected.sort();
        state.set_selected_books(model(selected.into_iter().map(Into::into)));

        let status = self.platform.status();
        let (time, battery, wifi) = if self.options.demo {
            let minute = if large {
                match self.route.as_str() {
                    "library" => 52,
                    "detail" => 53,
                    "sources" | "catalog" => 54,
                    "reader" => 55,
                    "search" => 49,
                    "history" => 50,
                    _ => 51,
                }
            } else {
                match self.route.as_str() {
                    "library" => 44,
                    "detail" => 45,
                    "reader" => 46,
                    "sources" => 47,
                    "catalog" => 48,
                    "search" => 49,
                    "history" => 50,
                    _ => 51,
                }
            };
            (
                format!("18:{minute:02}"),
                if large {
                    72
                } else if minute <= 46 {
                    87
                } else {
                    86
                },
                true,
            )
        } else {
            (status.time, status.battery as i32, status.wifi)
        };
        state.set_time(time.clone().into());
        state.set_battery(battery);
        state.set_wifi(wifi);
        state.set_free_space(if self.options.demo {
            "9.4 GB".into()
        } else {
            format_bytes(status.free_bytes).into()
        });
        if show_library && large {
            state.set_library_download_size(if self.options.demo {
                "3.1 GB".into()
            } else {
                self.view_download_size_label().into()
            });
        }

        let detail_page_count = if show_current_cover {
            self.sync_current_book_view(&state, &sources, &time, large)?
        } else {
            1
        };
        let mut library_page_count = 1;
        if show_library {
            let library_page_size = if large { 18 } else { 8 };
            let visible = self.store.library_view_page(
                LibraryQuery {
                    category: self.library_filter,
                    unread_only: false,
                    downloaded_only: false,
                    sort_title: false,
                    demo_order: self.options.demo,
                    sort_key: Some(self.interactions.library_sort.id()),
                    ascending: self.interactions.library_ascending,
                    filters: self.interactions.library_filters,
                },
                self.page,
                library_page_size,
            )?;
            self.page = visible.page;
            library_page_count = visible.total.div_ceil(library_page_size).max(1);
            let mut rows = Vec::with_capacity(visible.items.len());
            for book in &visible.items {
                let mut display_book = book.clone();
                if self.options.demo {
                    if let Some(path) = crate::demo::library_cover(&self.data_dir, book, large) {
                        display_book.cover = path.to_string_lossy().into_owned();
                    }
                }
                rows.push(self.view_book(&display_book, true)?);
            }
            resolve_book_title_collisions(&mut rows);
            state.set_books(model(rows));
        }

        if show_library && large {
            let mut updates = Vec::new();
            let daily_updates = self.store.daily_updates_view_page(
                &today.format("%Y-%m-%d").to_string(),
                self.options.demo,
                true,
                1,
                3,
            )?;
            for (book, chapter) in &daily_updates.items {
                let mut row = self.view_book(book, false)?;
                row.subtitle = format!(
                    "{}{}{}",
                    tr("chapter_prefix"),
                    number(chapter.number),
                    tr("chapter_suffix")
                )
                .into();
                updates.push(row);
            }
            state.set_updates(model(updates));
        }

        let mut source_page_count = 1;
        let mut available_page_count = 1;
        if matches!(self.route.as_str(), "sources" | "catalog") {
            let source_update_count = sources
                .iter()
                .filter(|source| source_update(source, &self.available))
                .count();
            let builtin_count = sources
                .iter()
                .filter(|source| source_is_builtin(source))
                .count();
            state.set_installed_count(sources.len() as i32);
            state.set_builtin_source_count(builtin_count as i32);
            state.set_extension_source_count(sources.len().saturating_sub(builtin_count) as i32);
            state.set_source_update_count(source_update_count as i32);
            let installed_rows: Vec<_> = sources
                .iter()
                .map(|source| source_row(source, &self.available))
                .collect();
            let installed_window = installed_source_window(
                installed_rows.len(),
                builtin_count,
                self.platform
                    .runtime_logical_size(self.display_landscape())
                    .1,
                large,
                state.get_installed_source_page().max(1) as usize,
            );
            state.set_installed_sources(model(
                installed_rows
                    .iter()
                    .skip(installed_window.offset)
                    .take(installed_window.limit)
                    .cloned(),
            ));
            state.set_installed_source_page(installed_window.page as i32);
            state.set_installed_source_page_count(installed_window.page_count as i32);
            let update_rows = installed_rows
                .iter()
                .filter(|source| source.update)
                .cloned()
                .collect::<Vec<_>>();
            source_page_count = update_rows.len().div_ceil(7).max(1);
            state.set_sources(model(
                if !large && self.route == "sources" && self.source_filter == 2 {
                    page_window(&update_rows, self.page.clamp(1, source_page_count), 7).to_vec()
                } else {
                    installed_rows
                },
            ));
            let available_rows: Vec<_> = self
                .available
                .iter()
                .filter(|source| {
                    source_matches_language(&source.languages, &self.interactions.source_language)
                })
                .map(|source| {
                    let installed = sources.iter().find(|installed| installed.id == source.id);
                    let subtitle = format!(
                        "{} · v{}",
                        language_label(&source.languages),
                        source.version
                    );
                    let compact_status = installed
                        .filter(|installed| !installed.enabled || source_is_builtin(installed))
                        .map(|installed| source_compact_status(installed, &subtitle))
                        .unwrap_or_else(|| subtitle.clone());
                    SourceItem {
                        id: source.id.clone().into(),
                        name: source.name.clone().into(),
                        display_name: source_display_name(&source.name, true).into(),
                        subtitle: subtitle.into(),
                        compact_status: compact_status.into(),
                        update_subtitle: installed
                            .map(|installed| source_update_subtitle(installed, &self.available))
                            .unwrap_or_default()
                            .into(),
                        initial: source_initial(&source.id, &source.name).into(),
                        available: true,
                        installed: installed.is_some(),
                        update: installed.is_some_and(|installed| {
                            source_update(installed, std::slice::from_ref(source))
                        }),
                        builtin: false,
                    }
                })
                .collect();
            state.set_available_count(available_rows.len() as i32);
            let available_rows = if large && self.source_filter == 2 {
                available_rows
                    .into_iter()
                    .filter(|source| source.update)
                    .collect::<Vec<_>>()
            } else {
                available_rows
            };
            let available_page_size = if large { 10 } else { 7 };
            available_page_count = available_rows.len().div_ceil(available_page_size).max(1);
            let available_page = if self.route == "sources" && (self.source_filter == 1 || large) {
                self.page.clamp(1, available_page_count)
            } else {
                1
            };
            state.set_available_sources(model(
                page_window(&available_rows, available_page, available_page_size)
                    .iter()
                    .cloned(),
            ));
            state.set_source_id(self.current_source.clone().into());
            let source = sources
                .iter()
                .find(|source| source.id == self.current_source);
            state.set_source_initial(
                source
                    .map(|source| source_initial(&source.id, &source.name))
                    .unwrap_or_default()
                    .into(),
            );
            state.set_source_available(source.is_some_and(|source| source.enabled));
            state.set_source_builtin(source.is_some_and(source_is_builtin));
            state.set_source_needs_setup(source.is_some_and(|source| {
                matches!(source.kind.as_str(), "komga" | "kavita" | "suwayomi")
                    && !self.options.demo
                    && !source
                        .options
                        .get("demo")
                        .and_then(Value::as_bool)
                        .unwrap_or(false)
                    && (source.base_url.trim().is_empty()
                        || !source
                            .options
                            .get("_connection_verified")
                            .and_then(Value::as_bool)
                            .unwrap_or(false))
            }));
            state.set_source_title(
                source
                    .map(|source| source.name.as_str())
                    .unwrap_or(&self.current_source)
                    .into(),
            );
            state.set_source_subtitle(
                source
                    .map(|source| {
                        let row = source_row(source, &self.available);
                        if large && row.update {
                            format!("{} · {}", row.subtitle, tr("updates_available"))
                        } else {
                            row.subtitle.to_string()
                        }
                    })
                    .unwrap_or_default()
                    .into(),
            );
        }

        let mut catalog_page_count = 1;
        if show_catalog {
            let mut catalog: Vec<&Book> = self.catalog.iter().collect();
            let mut active_filters =
                usize::from(self.catalog_unread_only) + usize::from(self.catalog_downloaded_only);
            let flags = if self.catalog_unread_only || self.catalog_downloaded_only {
                self.store.catalog_book_flags(
                    &catalog
                        .iter()
                        .map(|book| book.id.as_str())
                        .collect::<Vec<_>>(),
                )?
            } else {
                HashMap::new()
            };
            if self.catalog_unread_only {
                catalog.retain(|book| {
                    flags
                        .get(&book.id)
                        .map(|stored| stored.unread)
                        .unwrap_or(book.unread)
                        > 0
                });
            }
            if self.catalog_downloaded_only {
                catalog.retain(|book| flags.get(&book.id).is_some_and(|entry| entry.downloaded));
            }
            if let Some(filters) = self.interactions.catalog_filters.get(&self.current_source) {
                catalog.retain(|book| filters.matches(book));
                filters.sort_books(&mut catalog);
                active_filters += usize::from(filters.sort != "relevance")
                    + filters.statuses.len()
                    + filters.tags.values().filter(|state| **state != 0).count();
            }
            state.set_catalog_active_filter_count(active_filters as i32);
            state.set_catalog_result_count(catalog.len() as i32);
            let catalog_page_size = if large { 15 } else { 5 };
            catalog_page_count = catalog.len().div_ceil(catalog_page_size).max(1)
                + usize::from(self.catalog_has_more);
            let catalog_page = if self.route == "catalog"
                || self.route == "sources" && large && self.source_filter == 0
            {
                self.page.clamp(1, catalog_page_count)
            } else {
                1
            };
            let mut catalog_rows = Vec::new();
            for book in page_window(&catalog, catalog_page, catalog_page_size) {
                let mut row = self.view_book(*book, true)?;
                if let Some(relative) = relative_date(book.updated_at, self.options.demo) {
                    row.status = format!("{} · {relative}", row.status).into();
                }
                catalog_rows.push(row);
            }
            resolve_book_title_collisions(&mut catalog_rows);
            state.set_catalog(model(catalog_rows));
        }

        let mut history_page_count = 1;
        if show_history {
            let history_page_size = if large { 8 } else { 5 };
            let mut rows = Vec::new();
            if self.history_filter == 0 {
                let visible = self.store.history_layout_view_page(
                    self.page,
                    large,
                    self.platform
                        .runtime_logical_size(self.display_landscape())
                        .1,
                    self.options.demo,
                )?;
                self.page = visible.page;
                history_page_count = visible.page_count;
                for entry in visible.items {
                    let progress = entry.progress;
                    let book = entry.book;
                    let chapter = entry.chapter;
                    let download = load_chapter_download_projection(&self.store, &chapter)?;
                    let instant = local_instant(progress.updated_at, self.options.demo);
                    let progress_text = if progress.completed {
                        tr("finished_reading")
                    } else {
                        format!(
                            "{} / {} {}",
                            progress.page,
                            progress.total,
                            tr("pages_unit")
                        )
                    };
                    let item = HistoryItem {
                        id: book.id.clone().into(),
                        chapter_id: chapter.id.clone().into(),
                        title: book.title.clone().into(),
                        subtitle: chapter_label(&chapter).into(),
                        progress: format!("{progress_text} · {}", instant.format("%H:%M")).into(),
                        date: history_date(instant.date_naive(), self.options.demo).into(),
                        date_label: history_date_label(instant.date_naive(), self.options.demo)
                            .into(),
                        date_value: instant.format("%m · %d").to_string().into(),
                        cover: Image::default(),
                        fraction: progress.page as f32 / progress.total.max(1) as f32,
                        completed: progress.completed,
                        download_state: download.state.into(),
                        download_progress: download.progress,
                        ..HistoryItem::default()
                    };
                    rows.push((item, book));
                }
            } else {
                let visible = self.store.daily_updates_view_page(
                    &today.format("%Y-%m-%d").to_string(),
                    self.options.demo,
                    false,
                    self.page,
                    history_page_size,
                )?;
                self.page = visible.page;
                history_page_count = visible.total.div_ceil(history_page_size).max(1);
                for (book, chapter) in visible.items {
                    let download = load_chapter_download_projection(&self.store, &chapter)?;
                    let item = HistoryItem {
                        id: book.id.clone().into(),
                        chapter_id: chapter.id.clone().into(),
                        title: book.title.clone().into(),
                        subtitle: chapter_label(&chapter).into(),
                        progress: tr("unread").into(),
                        date: history_date(today, self.options.demo).into(),
                        date_label: history_date_label(today, self.options.demo).into(),
                        date_value: today.format("%m · %d").to_string().into(),
                        cover: Image::default(),
                        fraction: 0.0,
                        completed: false,
                        download_state: download.state.into(),
                        download_progress: download.progress,
                        ..HistoryItem::default()
                    };
                    rows.push((item, book));
                }
            }
            let mut visible_history = Vec::with_capacity(rows.len());
            for (mut item, book) in rows {
                let mut display_book = book.clone();
                if self.options.demo {
                    if let Some(path) = crate::demo::history_cover(&self.data_dir, &book) {
                        display_book.cover = path.to_string_lossy().into_owned();
                    }
                }
                item.cover = self.view_cover(&display_book);
                visible_history.push(item);
            }
            apply_history_layout(&mut visible_history, large);
            state.set_history(model(visible_history));
        }
        state.set_query(self.query.clone().into());
        state.set_search_scope(self.search_scope.clone().into());
        let eligible: Vec<_> = sources
            .iter()
            .filter(|source| {
                source.enabled
                    && (source.kind == "local"
                        || (self.options.offline && !self.options.demo)
                        || source.kind == "mangadex"
                        || source.kind == "wasm"
                        || !source.base_url.is_empty())
            })
            .collect();
        let searched = self
            .search_results
            .iter()
            .filter(|result| !result.pending)
            .count();
        let found = self
            .search_results
            .iter()
            .filter(|result| !result.books.is_empty())
            .count();
        state.set_search_source_count(eligible.len() as i32);
        let mut search_sources = eligible
            .iter()
            .enumerate()
            .map(|(index, source)| {
                let mut row = source_row(source, &self.available);
                if large && index < 3 {
                    row.display_name =
                        source_display_name_with_budget(&source.name, 14 * 4, 4).into();
                }
                row
            })
            .collect::<Vec<_>>();
        if large {
            let shortcut_count = search_sources.len().min(3);
            resolve_source_name_collisions(&mut search_sources[..shortcut_count], 14 * 4);
        }
        state.set_search_sources(model(search_sources));
        let mut local_matches = Vec::new();
        if show_search
            && large
            && (self.modal == "keyboard" || self.modal_context == "search-scope:typing")
            && self.keyboard_target == "search"
            && !self.keyboard.text().trim().is_empty()
        {
            for book in self
                .store
                .library_search_matches(self.keyboard.text().trim(), 2)?
            {
                let metadata = self.store.book_view_metadata(&book.id)?;
                let mut row = self.book_item(&book, &metadata, true);
                row.subtitle = if let Some(progress) = metadata.progress.as_ref() {
                    self.store
                        .chapter(&progress.chapter_id)?
                        .map(|chapter| {
                            format!(
                                "{} · {} / {} {}",
                                chapter_label(&chapter),
                                progress.page,
                                progress.total,
                                tr("pages_unit"),
                            )
                        })
                        .unwrap_or_else(|| {
                            format!(
                                "{} / {} {}",
                                progress.page,
                                progress.total,
                                tr("pages_unit")
                            )
                        })
                } else {
                    let source = sources.iter().find(|source| source.id == book.source_id);
                    format!(
                        "{} · {}",
                        source
                            .map(|source| source.name.as_str())
                            .unwrap_or(&book.source_id),
                        status_label(&book.status)
                    )
                }
                .into();
                local_matches.push(row);
            }
        }
        resolve_book_title_collisions(&mut local_matches);
        state.set_search_local_matches(model(local_matches));
        state.set_search_status(
            if self.query.is_empty() {
                String::new()
            } else if self.search_scope == "library" {
                format!(
                    "{}{}{} · {} · {} {}",
                    tr("opening_quote"),
                    self.query,
                    tr("closing_quote"),
                    tr("library"),
                    self.search_results
                        .iter()
                        .map(|result| result.books.len())
                        .sum::<usize>(),
                    tr("books_unit")
                )
            } else {
                format!(
                    "{}{}{} · {} {} {} · {} {}",
                    tr("opening_quote"),
                    self.query,
                    tr("closing_quote"),
                    tr("searched"),
                    searched,
                    tr("sources_unit"),
                    found,
                    tr("sources_with_results")
                )
            }
            .into(),
        );
        let mut search_page_count = 1;
        if show_search {
            let results = self.search_results.clone();
            let mut groups = Vec::new();
            for result in results {
                let mut books = Vec::new();
                for book in result.books.iter().take(if large { 6 } else { 3 }) {
                    // Off-page search groups need layout data, not database or image work.
                    let mut row = self.book_item(book, &BookViewMetadata::default(), false);
                    row.subtitle = if status_label(&book.status) == tr("completed") {
                        tr("completed")
                    } else if book.status.contains('\u{5377}') {
                        book.status.clone()
                    } else {
                        format!("{} {}", book.chapter_count, tr("chapters_unit"))
                    }
                    .into();
                    books.push(row);
                }
                let unsupported = result.error.as_ref().is_some_and(|error| {
                    let error = error.to_ascii_lowercase();
                    error.contains("unsupported")
                        || error.contains("does not support")
                        || error.contains("requires javascript")
                        || error.contains("browser verification")
                });
                let timeout = result.error.as_ref().is_some_and(|error| {
                    let error = error.to_ascii_lowercase();
                    error.contains("timeout") || error.contains("timed out")
                });
                groups.push(SearchGroup {
                    id: result.id.into(),
                    title: result.title.into(),
                    status: if result.pending {
                        tr("searching")
                    } else {
                        result
                            .error
                            .as_ref()
                            .map(|_| {
                                tr(if unsupported {
                                    "source_unsupported"
                                } else if timeout {
                                    "request_timeout"
                                } else {
                                    "error"
                                })
                            })
                            .unwrap_or_else(|| {
                                if result.books.is_empty() {
                                    tr("no_results")
                                } else {
                                    String::new()
                                }
                            })
                    }
                    .into(),
                    error: result.error.is_some(),
                    pending: result.pending,
                    error_detail: if unsupported {
                        tr("source_unsupported")
                    } else {
                        tr("network_error")
                    }
                    .into(),
                    count: result.books.len() as i32,
                    books: model(books),
                });
            }
            let group_pages = search_pages(
                &groups,
                self.platform
                    .runtime_logical_size(self.display_landscape())
                    .1,
                large,
            );
            search_page_count = group_pages.len().max(1);
            let search_page = if self.route == "search" {
                self.page.clamp(1, search_page_count)
            } else {
                1
            };
            let mut visible_groups = group_pages
                .get(search_page - 1)
                .cloned()
                .unwrap_or_default();
            for group in &mut visible_groups {
                let mut books = Vec::new();
                for index in 0..group.books.row_count() {
                    let Some(mut item) = group.books.row_data(index) else {
                        continue;
                    };
                    let book = self
                        .search_results
                        .iter()
                        .find(|result| result.id == group.id.as_str())
                        .and_then(|result| {
                            result.books.iter().find(|book| book.id == item.id.as_str())
                        })
                        .cloned();
                    if let Some(book) = book {
                        let mut visible = self.view_book(&book, true)?;
                        visible.subtitle = item.subtitle;
                        item = visible;
                    }
                    books.push(item);
                }
                resolve_book_title_collisions(&mut books);
                group.books = model(books);
            }
            state.set_search_groups(model(visible_groups));
        }

        let settings_page_count = if large { 1 } else { 2 };
        if show_settings {
            if self.interactions.settings_section == "storage" {
                self.sync_storage_details_view(&state);
            }
            let settings = self.view_settings()?;
            let settings_page = self.page.clamp(1, settings_page_count);
            state.set_settings(model(if large || settings_page == 1 {
                settings.0
            } else {
                settings.1
            }));
        }
        let page_count = match self.route.as_str() {
            "library" => library_page_count,
            "detail" => detail_page_count,
            "sources" if self.source_filter == 1 => available_page_count,
            "sources" if self.source_filter == 2 && large => available_page_count,
            "sources" if self.source_filter == 2 => source_page_count,
            "sources" if large => available_page_count,
            "sources" => 1,
            "catalog" => catalog_page_count,
            "history" => history_page_count,
            "search" => search_page_count,
            "settings" => settings_page_count,
            _ => 1,
        }
        .max(1);
        self.page = self.page.clamp(1, page_count);
        state.set_page(self.page as i32);
        state.set_page_count(page_count as i32);
        self.sync_overlay_view(&state);
        Ok(())
    }

    fn sync_base_view(&self, state: &State<'_>) {
        self.sync_interaction_state();
        self.sync_migration_state();
        self.sync_server_configuration_state();
        let profile = self.platform.profile();
        state.set_large_screen(profile.is_scribe());
        state.set_landscape(self.display_landscape());
        state.set_route(self.route.clone().into());
        state.set_reader_image_ready(self.displayed_reader.is_some());
        state.set_active_tab(match self.route.as_str() {
            "sources" | "catalog" => 1,
            "search" => 2,
            "history" => 3,
            "settings" => 4,
            _ => 0,
        });
        state.set_modal(self.modal.clone().into());
        state.set_summary_is_book(self.modal == "summary" && self.modal_context == "summary");
        state.set_message(self.message.clone().into());
        state.set_busy(self.busy);
        state.set_demo(self.options.demo);
        state.set_version(env!("CARGO_PKG_VERSION").into());
        state.set_has_frontlight(profile.has_frontlight());
        state.set_has_warmth(profile.has_warmth());
        state.set_has_orientation(profile.has_orientation());
        state.set_device_name(profile.name().into());
        state.set_device_description(
            tr(&format!(
                "device_{}_description",
                profile.id().replace('-', "_")
            ))
            .into(),
        );
        let effective = self.effective_reader_preferences();
        state.set_reading_direction(if self.route == "reader" {
            effective.reading_direction.clone().into()
        } else {
            self.prefs.reading_direction.clone().into()
        });
        state.set_tap_layout(self.prefs.tap_layout.clone().into());
        state.set_reverse_taps(
            self.prefs.reverse_taps
                || matches!(self.prefs.tap_layout.as_str(), "reverse" | "reversed"),
        );
        state.set_double_tap_zoom(self.prefs.double_tap_zoom);
        state.set_spread(effective.spread);
        let mut landscape = self.prefs.clone();
        landscape.landscape = true;
        landscape.reading_direction = effective.reading_direction;
        state.set_reader_landscape_spread(
            effective_preferences(
                &landscape,
                profile.is_scribe(),
                &self.interactions.page_layout_mode,
            )
            .spread,
        );
        state.set_cover_alone(self.prefs.cover_alone);
        state.set_incognito(self.prefs.incognito);
        state.set_night_mode(self.prefs.night_mode);
        state.set_brightness(self.prefs.brightness as i32);
        state.set_warmth(self.prefs.warmth as i32);
        state.set_refresh_every(self.prefs.refresh_every as i32);
    }

    fn sync_current_book_view(
        &mut self,
        state: &State<'_>,
        sources: &[SourceConfig],
        time: &str,
        large: bool,
    ) -> Result<usize> {
        let metadata = self
            .current_book_id
            .as_deref()
            .map(|id| self.store.book_view_metadata(id))
            .transpose()?
            .unwrap_or_default();
        let Some(book) = metadata.book.as_ref() else {
            state.set_current_book(BookItem::default());
            state.set_book_description(Default::default());
            state.set_book_tags(Default::default());
            state.set_book_tag_items(model(Vec::<slint::SharedString>::new()));
            state.set_book_metadata(Default::default());
            state.set_book_details(model(Vec::<SettingItem>::new()));
            state.set_chapters(model(Vec::<ChapterItem>::new()));
            state.set_chapter_count(0);
            state.set_unread_count(0);
            state.set_downloaded_count(0);
            clear_current_chapter_view(state);
            return Ok(1);
        };
        let mut display_book = book.clone();
        if self.options.demo && self.route == "library" {
            if let Some(path) = crate::demo::library_cover(&self.data_dir, book, large) {
                display_book.cover = path.to_string_lossy().into_owned();
            }
        }
        state.set_current_book(self.book_item(&display_book, &metadata, true));
        state.set_book_description(description_preview(&book.description).into());
        state.set_book_tags(book.tags.join(" · ").into());
        state.set_book_tag_items(model(book.tags.iter().cloned().map(Into::into)));
        if self.route == "detail" {
            let source = sources.iter().find(|source| source.id == book.source_id);
            let source_name = source
                .map(|source| source.name.as_str())
                .unwrap_or(&book.source_id);
            let language = if self.options.demo {
                tr(if large { "chinese_short" } else { "chinese" })
            } else {
                source
                    .map(|source| language_label(&source.languages))
                    .unwrap_or_default()
            };
            state.set_book_metadata(
                if large {
                    format!(
                        "{} · {} {} · {} · {}",
                        status_label(&book.status),
                        book.chapter_count,
                        tr("chapters_unit"),
                        source_name,
                        language
                    )
                } else {
                    format!(
                        "{} · {} · {}",
                        status_label(&book.status),
                        source_name,
                        language
                    )
                }
                .into(),
            );
        }
        let progress = metadata.progress.as_ref();
        let mut selected_chapter = self
            .reader
            .as_ref()
            .map(|reader| reader.current_chapter().clone())
            .filter(|chapter| chapter.book_id == book.id);
        if selected_chapter.is_none() {
            if let Some(progress) = progress {
                selected_chapter = self.store.chapter(&progress.chapter_id)?;
            }
        }
        if selected_chapter.is_none() {
            selected_chapter = self.store.first_unread_chapter_or_first(&book.id)?;
        }
        if let Some(chapter) = selected_chapter.as_ref() {
            state.set_current_chapter_id(chapter.id.clone().into());
            state.set_current_chapter_number(number(chapter.number).into());
            state.set_current_chapter(chapter.title.clone().into());
            let reader = self
                .reader
                .as_ref()
                .filter(|reader| reader.current_chapter().id == chapter.id);
            let page = reader
                .map(|reader| reader.pending_page.unwrap_or(reader.page))
                .or_else(|| progress.map(|progress| progress.page))
                .unwrap_or(1);
            let total = reader
                .map(|reader| reader.total)
                .or_else(|| progress.map(|progress| progress.total))
                .unwrap_or(chapter.page_count.max(1));
            state.set_reader_page(page as i32);
            state.set_reader_total(total as i32);
            state.set_reader_menu(reader.is_some_and(|reader| reader.menu_open));
            state.set_reader_bookmarked(self.store.has_bookmark(
                &chapter.id,
                reader.map(|reader| reader.page).unwrap_or(page),
            )?);
            state.set_next_downloaded(
                self.store
                    .next_chapter_downloaded(&book.id, chapter.number)?,
            );
            let mode = reader
                .map(|reader| reader.footer_mode)
                .unwrap_or(self.prefs.footer_mode);
            state.set_reader_footer(
                match mode % 4 {
                    1 => format!(
                        "{} {} {}",
                        tr("chapter_remaining"),
                        total.saturating_sub(page),
                        tr("pages_unit")
                    ),
                    2 => time.to_owned(),
                    3 => String::new(),
                    _ => format!("{page} / {total}"),
                }
                .into(),
            );
        } else {
            clear_current_chapter_view(state);
        }
        if self.route != "detail" {
            return Ok(1);
        }
        let summary = self.store.chapter_view_summary(
            &book.id,
            progress
                .filter(|progress| !progress.completed)
                .map(|progress| progress.chapter_id.as_str()),
        )?;
        state.set_chapter_count(book.chapter_count.max(summary.total as u32) as i32);
        state.set_downloaded_count(summary.downloaded as i32);
        state.set_unread_count(summary.unread as i32);
        let source = sources.iter().find(|source| source.id == book.source_id);
        let detail = |title: &str, value: String| SettingItem {
            title: tr(title).into(),
            value: value.into(),
            ..SettingItem::default()
        };
        state.set_book_details(model([
            detail(
                "source",
                source
                    .map(|source| source.name.clone())
                    .unwrap_or_else(|| book.source_id.clone()),
            ),
            detail(
                "latest_update",
                if book.updated_at > 0
                    && chrono::Utc
                        .timestamp_opt(book.updated_at, 0)
                        .single()
                        .is_some()
                {
                    local_instant(book.updated_at, self.options.demo)
                        .format("%Y · %m · %d")
                        .to_string()
                } else {
                    "—".into()
                },
            ),
            detail(
                "local_usage",
                format!("{} {} · —", summary.downloaded, tr("chapters_unit")),
            ),
        ]));
        let visible = self.store.detail_chapters_view_page(
            &book.id,
            state.get_newest_first(),
            summary.total,
            self.page,
            large,
        )?;
        self.page = visible.page;
        let mut rows = Vec::with_capacity(visible.items.len());
        for chapter in &visible.items {
            let current = progress
                .is_some_and(|progress| progress.chapter_id == chapter.id && !progress.completed);
            let download = load_chapter_download_projection(&self.store, chapter)?;
            rows.push(ChapterItem {
                id: chapter.id.clone().into(),
                number: number(chapter.number).into(),
                title: chapter.title.clone().into(),
                date: chapter_date(&chapter.date, large).into(),
                read: chapter.read,
                downloaded: chapter.downloaded,
                download_state: download.state.into(),
                download_progress: download.progress,
                current,
                selected: self.selected_chapters.contains(&chapter.id),
                progress: if current {
                    progress
                        .map(|progress| format!("{} / {}", progress.page, progress.total))
                        .unwrap_or_default()
                        .into()
                } else {
                    Default::default()
                },
            });
        }
        state.set_chapters(model(rows));
        Ok(detail_chapter_page_count(visible.total, large))
    }

    fn sync_reader_view(&self, state: &State<'_>) -> Result<()> {
        let Some(reader) = self.reader.as_ref() else {
            state.set_current_book(BookItem::default());
            state.set_current_chapter_id(Default::default());
            state.set_current_chapter_number(Default::default());
            state.set_current_chapter(Default::default());
            state.set_reader_page(1);
            state.set_reader_total(1);
            state.set_reader_menu(false);
            state.set_reader_bookmarked(false);
            state.set_next_downloaded(false);
            state.set_reader_footer(Default::default());
            return Ok(());
        };
        let metadata = load_reader_metadata(
            &self.store,
            reader,
            self.displayed_reader.as_ref().map(|frame| &frame.session),
            self.committed_progress.as_ref(),
        )?;
        let projection = &metadata.projection;
        let page = projection.label_page;
        let total = projection.total;
        state.set_current_chapter_id(projection.chapter_id.clone().into());
        state.set_current_chapter_number(projection.chapter_number.clone().into());
        state.set_current_chapter(projection.chapter_title.clone().into());
        state.set_reader_page(page as i32);
        state.set_reader_total(total as i32);
        state.set_reader_menu(reader.menu_open);
        state.set_reader_bookmarked(metadata.bookmarked);
        state.set_next_downloaded(metadata.next_downloaded);
        if self.options.demo {
            let large = self.platform.profile().is_scribe();
            state.set_time(if large { "18:55" } else { "18:46" }.into());
            state.set_battery(if large { 72 } else { 87 });
            state.set_wifi(true);
            state.set_free_space("9.4 GB".into());
        } else {
            // Opening the menu refreshes the platform observation explicitly.
            // Reading its cache here performs no device I/O or keep-awake writes.
            let status = self.platform.cached_status();
            state.set_time(status.time.into());
            state.set_battery(status.battery as i32);
            state.set_wifi(status.wifi);
            state.set_free_space(format_bytes(status.free_bytes).into());
        }
        state.set_reader_footer(
            match reader.footer_mode % 4 {
                1 => format!(
                    "{} {} {}",
                    tr("chapter_remaining"),
                    total.saturating_sub(page),
                    tr("pages_unit")
                ),
                2 => state.get_time().to_string(),
                3 => String::new(),
                _ => format!("{page} / {total}"),
            }
            .into(),
        );
        if let Some(book) = metadata.book.as_ref() {
            let previous = state.get_current_book();
            let progress = metadata.progress.as_ref();
            state.set_current_book(BookItem {
                id: book.id.clone().into(),
                title: book.title.clone().into(),
                display_title: book_display_title(&book.title).into(),
                author: book.author.clone().into(),
                subtitle: book
                    .tags
                    .iter()
                    .take(2)
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(" · ")
                    .into(),
                status: format!(
                    "{} · {} {}",
                    status_label(&book.status),
                    book.chapter_count.max(reader.chapters().len() as u32),
                    tr("chapters_unit")
                )
                .into(),
                cover: if previous.id.as_str() == book.id {
                    previous.cover
                } else {
                    Image::default()
                },
                unread: book.unread as i32,
                downloaded: metadata.next_downloaded
                    || reader.chapters().iter().any(|entry| entry.downloaded),
                in_library: book.in_library,
                progress: book_cover_progress(progress),
                selected: self.selected.contains(&book.id),
            });
            state.set_book_description(description_preview(&book.description).into());
            state.set_book_tags(book.tags.join(" · ").into());
            state.set_book_tag_items(model(book.tags.iter().cloned().map(Into::into)));
            state.set_chapter_count(book.chapter_count.max(reader.chapters().len() as u32) as i32);
            state.set_unread_count(book.unread.saturating_sub(u32::from(
                !projection.chapter_read && progress.is_some_and(|progress| !progress.completed),
            )) as i32);
            state.set_downloaded_count(
                reader
                    .chapters()
                    .iter()
                    .filter(|entry| entry.downloaded)
                    .count() as i32,
            );
        } else {
            state.set_current_book(BookItem::default());
        }
        // Reader images belong to the image worker. Metadata synchronization
        // must never clear, decode, or replace a committed image.
        Ok(())
    }

    fn sync_overlay_view(&mut self, state: &State<'_>) {
        let secure_input = self.server_keyboard_is_secret()
            || self
                .keyboard_target
                .strip_prefix("sourceopt:")
                .is_some_and(|key| {
                    self.source_settings
                        .iter()
                        .any(|setting| setting.key == key && setting.kind == "secure-text")
                });
        state.set_keyboard_text(
            if secure_input {
                "•".repeat(self.keyboard.text().chars().count())
            } else {
                self.keyboard.text().into()
            }
            .into(),
        );
        state.set_keyboard_target(self.keyboard_target.clone().into());
        state.set_keyboard_composition(if secure_input {
            Default::default()
        } else {
            self.keyboard.composition().into()
        });
        state.set_keyboard_candidates(model(if secure_input {
            Vec::new()
        } else {
            self.keyboard
                .candidates()
                .into_iter()
                .map(Into::into)
                .collect::<Vec<_>>()
        }));
        let (candidate_page, candidate_page_count) = if secure_input {
            (1, 1)
        } else {
            self.keyboard.candidate_pagination()
        };
        state.set_keyboard_candidate_page(candidate_page as i32);
        state.set_keyboard_candidate_page_count(candidate_page_count as i32);
        let panel_page_count = if matches!(self.modal.as_str(), "choices" | "downloads") {
            self.choice_total.div_ceil(self.choice_page_size()).max(1)
        } else if self.modal == "summary" {
            self.panel_pages.len().max(1)
        } else {
            1
        };
        self.panel_page = self.panel_page.min(panel_page_count - 1);
        if self.modal == "summary" {
            state.set_panel_text(
                self.panel_pages
                    .get(self.panel_page)
                    .cloned()
                    .unwrap_or_default()
                    .into(),
            );
        }
        state.set_panel_page(self.panel_page as i32 + 1);
        state.set_panel_page_count(panel_page_count as i32);
    }

    pub(super) fn cover_book_for_view(&self, id: &str) -> Result<Option<Book>> {
        let visible = match self.route.as_str() {
            "catalog" | "sources" => self.catalog.iter().find(|book| book.id == id).cloned(),
            "search" => self
                .search_results
                .iter()
                .flat_map(|group| &group.books)
                .find(|book| book.id == id)
                .cloned(),
            _ => None,
        };
        let mut book = match visible {
            Some(book) => book,
            None => match self.store.book(id)? {
                Some(book) => book,
                None => return Ok(None),
            },
        };
        if self.options.demo {
            let path = match self.route.as_str() {
                "library" => crate::demo::library_cover(
                    &self.data_dir,
                    &book,
                    self.platform.profile().is_scribe(),
                ),
                "history" => crate::demo::history_cover(&self.data_dir, &book),
                _ => None,
            };
            if let Some(path) = path {
                book.cover = path.to_string_lossy().into_owned();
            }
        }
        Ok(Some(book))
    }

    fn view_cover(&self, book: &Book) -> Image {
        let cached_path = self
            .cover_paths
            .get(&book.id)
            .filter(|_| self.cover_uris.get(&book.id) == Some(&book.cover));
        cached_path
            .and_then(|path| self.cover_cache.get(&(book.id.clone(), path.clone())))
            .or_else(|| {
                self.cover_cache
                    .get(&(book.id.clone(), PathBuf::from(&book.cover)))
            })
            .cloned()
            .unwrap_or_default()
    }

    fn view_book(&self, book: &Book, load_cover: bool) -> Result<BookItem> {
        let metadata = self.store.book_view_metadata(&book.id)?;
        Ok(self.book_item(book, &metadata, load_cover))
    }

    fn book_item(&self, book: &Book, metadata: &BookViewMetadata, load_cover: bool) -> BookItem {
        let source = metadata.book.as_ref().unwrap_or(book);
        BookItem {
            id: book.id.clone().into(),
            title: book.title.clone().into(),
            display_title: book_display_title(&book.title).into(),
            author: book.author.clone().into(),
            subtitle: book
                .tags
                .iter()
                .take(2)
                .cloned()
                .collect::<Vec<_>>()
                .join(" · ")
                .into(),
            status: format!(
                "{} · {} {}",
                status_label(&book.status),
                book.chapter_count.max(source.chapter_count),
                tr("chapters_unit")
            )
            .into(),
            cover: if load_cover {
                self.view_cover(book)
            } else {
                Image::default()
            },
            unread: source.unread as i32,
            downloaded: metadata.downloaded,
            in_library: source.in_library,
            progress: book_cover_progress(metadata.progress.as_ref()),
            selected: self.selected.contains(&book.id),
        }
    }
    fn view_download_size_label(&self) -> String {
        self.cached_download_bytes
            .map(format_bytes)
            .unwrap_or_else(|| "—".into())
    }

    fn sync_storage_details_view(&self, state: &State<'_>) {
        let projection = storage_view_projection(
            self.storage_details.snapshot(),
            self.storage_details.is_refreshing(),
            self.storage_details.refresh_failed(),
        );
        state.set_storage_ready(projection.ready);
        state.set_storage_loading(projection.loading);
        state.set_storage_status_code(projection.status_code.into());
        state.set_storage_status(projection.status.into());
        state.set_storage_scale_known(projection.scale_known);
        state.set_storage_downloaded_known(projection.downloaded.known);
        state.set_storage_downloaded_text(projection.downloaded.text.into());
        state.set_storage_downloaded_fraction(projection.downloaded.fraction);
        state.set_storage_image_cache_known(projection.image_cache.known);
        state.set_storage_image_cache_text(projection.image_cache.text.into());
        state.set_storage_image_cache_fraction(projection.image_cache.fraction);
        state.set_storage_covers_known(projection.covers.known);
        state.set_storage_covers_text(projection.covers.text.into());
        state.set_storage_covers_fraction(projection.covers.fraction);
        state.set_storage_free_known(projection.free.known);
        state.set_free_space(projection.free.text.clone().into());
        state.set_storage_free_text(projection.free.text.into());
        state.set_storage_free_fraction(projection.free.fraction);
        state.set_storage_book_count(projection.book_count.min(i32::MAX as usize) as i32);
        state.set_storage_books(model(projection.books));
        state.set_storage_books_status(projection.books_status.into());
    }

    fn view_settings(&self) -> Result<(Vec<SettingItem>, Vec<SettingItem>)> {
        let large = self.platform.profile().is_scribe();
        let item = |id: &str, title: &str, value: String, group: &str, enabled: bool| SettingItem {
            id: id.into(),
            title: tr(title).into(),
            value: value.into(),
            group: group.into(),
            enabled,
        };
        let refresh = if self.prefs.refresh_every == 0 {
            tr("only_chapter")
        } else {
            format!(
                "{} {} {} · {}",
                tr("every"),
                self.prefs.refresh_every,
                tr("pages_unit"),
                tr("balanced")
            )
        };
        let mut first = vec![item(
            "screen-refresh",
            "screen_refresh",
            refresh,
            "device",
            true,
        )];
        if self.platform.profile().has_frontlight() {
            first.push(item(
                "frontlight-warmth",
                "frontlight_warmth",
                if self.platform.profile().has_warmth() {
                    format!(
                        "{} {} · {} {}",
                        tr("brightness"),
                        self.prefs.brightness,
                        tr("warmth"),
                        self.prefs.warmth
                    )
                } else {
                    format!("{} {}", tr("brightness"), self.prefs.brightness)
                },
                "device",
                true,
            ));
        }
        first.push(item(
            "wifi-sleep-exit",
            "wifi_sleep_exit",
            if self.prefs.keep_awake {
                tr("keep_awake")
            } else {
                tr("allow_sleep")
            },
            "device",
            true,
        ));
        first.push(item(
            "storage",
            "storage",
            format!(
                "{} {}",
                tr("used"),
                if self.options.demo {
                    "2.1 GB".into()
                } else {
                    self.view_download_size_label()
                }
            ),
            "device",
            true,
        ));
        first.push(item(
            "default-direction",
            if large {
                "settings_reading_direction"
            } else {
                "default_direction"
            },
            tr(match self.prefs.reading_direction.as_str() {
                "vertical-pages" => "vertical_pages",
                "vertical" => "webtoon_scroll",
                direction => direction,
            }),
            "reading",
            true,
        ));
        first.push(item(
            "tap-regions",
            "tap_regions",
            {
                let base = tr(match self.prefs.tap_layout.as_str() {
                    "l-shaped" => "tap_l_shape",
                    "swipe-only" => "swipe_only",
                    _ => "thirds",
                });
                if self.prefs.reverse_taps
                    || matches!(self.prefs.tap_layout.as_str(), "reverse" | "reversed")
                {
                    format!("{} · {}", base, tr("reverse"))
                } else {
                    base
                }
            },
            "reading",
            true,
        ));
        first.push(item(
            "image-processing",
            "image_processing",
            [
                self.prefs.crop_borders.then(|| tr("crop_borders")),
                self.prefs.dither.then(|| tr("dither")),
            ]
            .into_iter()
            .flatten()
            .collect::<Vec<_>>()
            .join(" · "),
            "reading",
            true,
        ));
        if large {
            first.push(item(
                "spread",
                "spread_and_landscape",
                tr(if self.prefs.spread {
                    "auto_spread"
                } else {
                    "keep_single"
                }),
                "reading",
                true,
            ));
        }
        // Pen navigation needs a runtime capability instead of a device-name guess.
        let mut second = vec![
            item("categories", "category", String::new(), "library", true),
            item(
                "auto-update",
                "automatic_updates",
                tr("unavailable"),
                "library",
                false,
            ),
            item(
                "source-lists",
                "source_list",
                self.interactions.repositories.len().to_string(),
                "sources-data",
                true,
            ),
            item(
                "downloads",
                "download",
                self.store.download_count()?.to_string(),
                "sources-data",
                true,
            ),
            item(
                "tracking",
                "track",
                tr("not_configured"),
                "sources-data",
                false,
            ),
            item(
                "backup",
                "backup_restore",
                tr("unavailable"),
                "sources-data",
                false,
            ),
            item(
                "language",
                "interface_language",
                tr("chinese"),
                "general",
                true,
            ),
            item(
                "logs",
                "logs_diagnostics",
                tr("unavailable"),
                "general",
                false,
            ),
            item(
                "about",
                "about",
                env!("CARGO_PKG_VERSION").into(),
                "general",
                true,
            ),
        ];
        if large {
            for row in &mut second {
                row.group = if row.id.as_str() == "backup" || row.group.as_str() == "general" {
                    "general".into()
                } else {
                    "library-sources".into()
                };
            }
            first.extend(second);
            return Ok((first, Vec::new()));
        }
        Ok((first, second))
    }
}

#[derive(Debug, Default)]
struct NonReaderViewContext {
    sources: Vec<SourceConfig>,
    has_source_snapshot: bool,
    library_counts: Option<LibraryCounts>,
    daily_update_count: usize,
    weekly_read_count: usize,
}

#[derive(Debug, PartialEq)]
struct StorageValueProjection {
    known: bool,
    text: String,
    fraction: f32,
}

struct StorageViewProjection {
    ready: bool,
    loading: bool,
    status_code: &'static str,
    status: String,
    scale_known: bool,
    downloaded: StorageValueProjection,
    image_cache: StorageValueProjection,
    covers: StorageValueProjection,
    free: StorageValueProjection,
    book_count: usize,
    books: Vec<SettingItem>,
    books_status: String,
}

fn storage_bytes_text(bytes: Option<u64>) -> String {
    match bytes {
        None => "—".into(),
        Some(bytes) if bytes < 1_000 => format!("{bytes} B"),
        Some(bytes) if bytes < 1_000_000 => format!("{:.1} KB", bytes as f64 / 1_000.0),
        Some(bytes) if bytes < 1_000_000_000 => format!("{:.1} MB", bytes as f64 / 1_000_000.0),
        Some(bytes) => format!("{:.1} GB", bytes as f64 / 1_000_000_000.0),
    }
}

fn storage_value_projection(bytes: Option<u64>, scale: Option<u64>) -> StorageValueProjection {
    let fraction = match (bytes, scale) {
        (Some(bytes), Some(scale)) if scale > 0 => {
            (bytes as f64 / scale as f64).clamp(0.0, 1.0) as f32
        }
        _ => 0.0,
    };
    StorageValueProjection {
        known: bytes.is_some(),
        text: storage_bytes_text(bytes),
        fraction,
    }
}

fn storage_view_projection(
    snapshot: Option<&StorageDetailsSnapshot>,
    refreshing: bool,
    refresh_failed: bool,
) -> StorageViewProjection {
    let scale = snapshot.and_then(StorageDetailsSnapshot::scale_bytes);
    let status_code = if refreshing {
        "loading"
    } else if snapshot.is_none() || refresh_failed {
        "unknown"
    } else if scale.is_none() {
        "partial"
    } else {
        "ready"
    };
    let status = match status_code {
        "loading" => tr("storage_calculating"),
        "unknown" => tr("storage_unknown"),
        "partial" => tr("storage_partial"),
        _ => String::new(),
    };
    let books = snapshot
        .map(|snapshot| {
            snapshot
                .books
                .iter()
                .take(5)
                .map(|book| SettingItem {
                    id: format!("storage-book:{}", book.id).into(),
                    title: book.title.clone().into(),
                    value: storage_bytes_text(Some(book.bytes)).into(),
                    group: "storage-book".into(),
                    enabled: true,
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let books_status = if !books.is_empty() {
        String::new()
    } else if refreshing {
        tr("storage_calculating")
    } else if snapshot.is_none() {
        tr("storage_unknown")
    } else {
        tr("storage_books_empty")
    };
    StorageViewProjection {
        ready: snapshot.is_some(),
        loading: refreshing,
        status_code,
        status,
        scale_known: scale.is_some(),
        downloaded: storage_value_projection(
            snapshot.map(|snapshot| snapshot.downloaded_bytes),
            scale,
        ),
        image_cache: storage_value_projection(
            snapshot.and_then(|snapshot| snapshot.image_cache_bytes),
            scale,
        ),
        covers: storage_value_projection(snapshot.and_then(|snapshot| snapshot.cover_bytes), scale),
        free: storage_value_projection(snapshot.and_then(|snapshot| snapshot.free_bytes), scale),
        book_count: snapshot.map(|snapshot| snapshot.books.len()).unwrap_or(0),
        books,
        books_status,
    }
}

#[derive(Debug, PartialEq, Eq)]
struct InstalledSourceWindow {
    page: usize,
    page_count: usize,
    offset: usize,
    limit: usize,
}

fn installed_source_window(
    total: usize,
    builtin_count: usize,
    logical_height: u32,
    large: bool,
    requested_page: usize,
) -> InstalledSourceWindow {
    // Double design pixels preserve the half-pixel header boundary exactly.
    let (reserved, top, group_height, row_height, helper_height, pager_height) = if large {
        (555, 16, 92, 136, 158, 136)
    } else {
        (449, 0, 76, 116, 178, 104)
    };
    let pane_height = (u64::from(logical_height) * 2).saturating_sub(reserved);
    let groups = u64::from(builtin_count > 0) + u64::from(total > builtin_count);
    let fixed_height = top + groups * group_height + helper_height;
    let all_height = fixed_height.saturating_add((total as u64).saturating_mul(row_height));
    let limit = if all_height <= pane_height {
        total
    } else {
        (pane_height.saturating_sub(fixed_height + pager_height) / row_height).max(1) as usize
    };
    let page_count = if limit == 0 {
        1
    } else {
        total.div_ceil(limit).max(1)
    };
    let page = requested_page.clamp(1, page_count);
    InstalledSourceWindow {
        page,
        page_count,
        offset: (page - 1) * limit,
        limit,
    }
}

fn clear_current_chapter_view(state: &State<'_>) {
    state.set_current_chapter_id(Default::default());
    state.set_current_chapter_number(Default::default());
    state.set_current_chapter(Default::default());
    state.set_reader_page(1);
    state.set_reader_total(1);
    state.set_reader_menu(false);
    state.set_reader_bookmarked(false);
    state.set_next_downloaded(false);
    state.set_reader_footer(Default::default());
}

#[derive(Debug, PartialEq)]
struct ChapterDownloadProjection {
    state: String,
    progress: f32,
}

fn load_chapter_download_projection(
    store: &Store,
    chapter: &Chapter,
) -> Result<ChapterDownloadProjection> {
    // Each visible row performs one primary-key lookup, independent of queue size.
    let job = store.download(&chapter.id)?;
    Ok(chapter_download_projection(chapter, job.as_ref()))
}

fn chapter_download_projection(
    chapter: &Chapter,
    job: Option<&DownloadJob>,
) -> ChapterDownloadProjection {
    if let Some(job) = job.filter(|job| {
        !chapter.downloaded || matches!(job.state.as_str(), "queued" | "downloading" | "paused")
    }) {
        return ChapterDownloadProjection {
            state: job.state.clone(),
            progress: if job.total == 0 {
                0.0
            } else {
                (job.completed as f32 / job.total as f32).clamp(0.0, 1.0)
            },
        };
    }
    ChapterDownloadProjection {
        state: if chapter.downloaded {
            "completed"
        } else {
            "available"
        }
        .into(),
        progress: if chapter.downloaded { 1.0 } else { 0.0 },
    }
}

fn apply_history_layout(rows: &mut [HistoryItem], large: bool) {
    let columns = if large { 2 } else { 1 };
    let (header_height, row_height) = if large { (60, 124) } else { (44, 76) };
    let mut group_start = 0;
    let mut group_offset = 0;
    while group_start < rows.len() {
        let group_end = (group_start + 1..rows.len())
            .find(|index| rows[*index].date != rows[group_start].date)
            .unwrap_or(rows.len());
        let group_rows = (group_end - group_start).div_ceil(columns);
        for (index, row) in rows[group_start..group_end].iter_mut().enumerate() {
            row.group_start = index == 0;
            row.column = (index % columns) as i32;
            row.row_index = (index / columns) as i32;
            row.group_row_count = group_rows as i32;
            row.group_offset = group_offset;
        }
        group_offset += header_height + row_height * group_rows as i32;
        group_start = group_end;
    }
}

fn load_non_reader_context(
    store: &Store,
    route: &str,
    large: bool,
    demo: bool,
) -> Result<NonReaderViewContext> {
    let mut context = NonReaderViewContext::default();
    if matches!(route, "sources" | "catalog" | "search" | "detail") {
        context.sources = store.sources()?;
        context.has_source_snapshot = true;
        context.sources.sort_by(|left, right| {
            source_order(left)
                .cmp(&source_order(right))
                .then_with(|| left.name.cmp(&right.name))
        });
    }
    if route == "library" {
        context.library_counts = Some(store.library_counts()?);
    }
    if route == "history" || route == "library" && large {
        let today = view_today(demo);
        context.daily_update_count =
            store.daily_update_count(&today.format("%Y-%m-%d").to_string())?;
        if route == "history" {
            let (start, end) = view_week_bounds(today, demo);
            context.weekly_read_count = store.weekly_read_count(start, end)?;
        }
    }
    Ok(context)
}

fn view_week_bounds(today: chrono::NaiveDate, demo: bool) -> (i64, i64) {
    let midnight = |date: chrono::NaiveDate| {
        let value = date.and_hms_opt(0, 0, 0).expect("Valid calendar midnight");
        if demo {
            chrono::FixedOffset::east_opt(8 * 3600)
                .expect("Fixed demo time zone")
                .from_local_datetime(&value)
                .earliest()
                .expect("Fixed offset midnight")
                .timestamp()
        } else {
            // Some time zones skip midnight at a daylight-saving boundary.
            // The first valid minute still bounds the same local calendar day.
            (0..=1_440)
                .find_map(|minutes| {
                    chrono::Local
                        .from_local_datetime(&(value + chrono::Duration::minutes(minutes)))
                        .earliest()
                })
                .expect("A local calendar day has a valid start")
                .timestamp()
        }
    };
    (
        midnight(today - chrono::Duration::days(6)),
        midnight(today + chrono::Duration::days(1)),
    )
}

fn reader_view_projection(
    reader: &ReaderState,
    displayed: Option<&ReaderSession>,
) -> (ReaderViewProjection, Option<usize>) {
    let requested = reader.current_chapter();
    let (book_id, chapter_id, image_page, total) = if let Some(displayed) = displayed {
        (
            displayed.book_id.as_str(),
            displayed.chapter_id.as_str(),
            displayed.page,
            displayed.total,
        )
    } else {
        (
            requested.book_id.as_str(),
            requested.id.as_str(),
            reader.page,
            reader.total,
        )
    };
    let same_chapter = requested.book_id == book_id && requested.id == chapter_id;
    let chapter_index = if same_chapter {
        Some(reader.chapter_index())
    } else {
        reader
            .chapters()
            .iter()
            .position(|chapter| chapter.book_id == book_id && chapter.id == chapter_id)
    };
    let chapter = chapter_index.map(|index| &reader.chapters()[index]);
    let total = total.max(1);
    let image_page = image_page.clamp(1, total);
    let label_page = if same_chapter {
        reader.pending_page.unwrap_or(image_page).clamp(1, total)
    } else {
        image_page
    };
    (
        ReaderViewProjection {
            book_id: book_id.into(),
            chapter_id: chapter_id.into(),
            chapter_number: chapter
                .map(|chapter| number(chapter.number))
                .unwrap_or_default(),
            chapter_title: chapter
                .map(|chapter| chapter.title.clone())
                .unwrap_or_else(|| chapter_id.into()),
            chapter_read: chapter.is_some_and(|chapter| chapter.read),
            image_page,
            label_page,
            total,
        },
        chapter_index,
    )
}

fn load_reader_metadata(
    store: &Store,
    reader: &ReaderState,
    displayed: Option<&ReaderSession>,
    committed: Option<&Progress>,
) -> Result<ReaderViewMetadata> {
    let (projection, chapter_index) = reader_view_projection(reader, displayed);
    let book = store.book(&projection.book_id)?;
    let progress = if let Some(progress) = committed.filter(|progress| {
        progress.book_id == projection.book_id && progress.chapter_id == projection.chapter_id
    }) {
        Some(progress.clone())
    } else {
        // The chapter primary key keeps a cache miss independent of library and
        // history size, unlike the unbounded most-recent history query.
        store
            .chapter_progress(&projection.chapter_id)?
            .filter(|progress| progress.book_id == projection.book_id)
    };
    let bookmarked = store.has_bookmark(&projection.chapter_id, projection.image_page)?;
    // ReaderState advances to the next sorted row, including duplicate chapter
    // numbers. Use the same successor instead of skipping to a greater number.
    let next = chapter_index.and_then(|index| reader.chapters().get(index + 1));
    let next_downloaded = if let Some(next) = next {
        // A download can finish while this reading session stays open. Read only
        // its successor instead of reloading every chapter to update the menu.
        store
            .chapter(&next.id)?
            .is_some_and(|chapter| chapter.downloaded)
    } else {
        false
    };
    Ok(ReaderViewMetadata {
        book,
        progress,
        bookmarked,
        next_downloaded,
        projection,
    })
}

fn page_window<T>(items: &[T], page: usize, size: usize) -> &[T] {
    let start = page.saturating_sub(1).saturating_mul(size).min(items.len());
    &items[start..(start + size).min(items.len())]
}

fn number(value: f64) -> String {
    if value.fract().abs() < f64::EPSILON {
        format!("{value:.0}")
    } else {
        format!("{value}")
    }
}

fn chapter_label(chapter: &Chapter) -> String {
    format!(
        "{}{}{}{}",
        tr("chapter_prefix"),
        number(chapter.number),
        tr("chapter_suffix"),
        if chapter.title.is_empty() {
            String::new()
        } else {
            format!(" · {}", chapter.title)
        }
    )
}

fn status_label(status: &str) -> String {
    match status.trim().to_ascii_lowercase().as_str() {
        "ongoing" => tr("ongoing"),
        "completed" | "complete" | "ended" | "finished" => tr("completed"),
        "hiatus" | "paused" | "on_hold" | "onhold" => tr("paused"),
        "cancelled" | "canceled" | "abandoned" => tr("discontinued"),
        "unknown" | "" => tr("unknown_status"),
        _ => status.to_owned(),
    }
}

fn description_preview(description: &str) -> &str {
    let end = description
        .char_indices()
        .nth(2_048)
        .map(|(index, _)| index)
        .unwrap_or(description.len());
    &description[..end]
}

fn book_cover_progress(progress: Option<&Progress>) -> f32 {
    match progress {
        Some(progress) if progress.completed => 1.0,
        Some(progress) if progress.page > 1 => {
            (progress.page as f32 / progress.total.max(1) as f32).clamp(0.0, 1.0)
        }
        _ => 0.0,
    }
}

fn format_bytes(bytes: u64) -> String {
    if bytes >= 1_000_000_000 {
        format!("{:.1} GB", bytes as f64 / 1_000_000_000.0)
    } else if bytes >= 1_000_000 {
        format!("{:.1} MB", bytes as f64 / 1_000_000.0)
    } else {
        format!("{} KB", bytes / 1_000)
    }
}

fn source_order(source: &SourceConfig) -> usize {
    match source.kind.as_str() {
        "local" => 0,
        "komga" => 1,
        "kavita" => 2,
        "suwayomi" => 3,
        "mangadex" => 4,
        _ => 5,
    }
}

fn listing_label(id: &str, original: &str) -> String {
    match id {
        "popular" => tr("popular"),
        "latest" => tr("recently_updated"),
        "new" => tr("newly_added"),
        "title" => tr("sort_title"),
        _ => original.to_owned(),
    }
}

fn source_update(source: &SourceConfig, available: &[RepositorySource]) -> bool {
    source
        .options
        .get("update_available")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        || available
            .iter()
            .any(|candidate| candidate.id == source.id && candidate.version > source.version)
}

fn source_is_builtin(source: &SourceConfig) -> bool {
    matches!(
        source.kind.as_str(),
        "local" | "komga" | "kavita" | "suwayomi"
    )
}

fn source_matches_language(languages: &[String], selected: &str) -> bool {
    if selected == "all" {
        return true;
    }
    languages.iter().any(|language| {
        let language = language.to_ascii_lowercase();
        match selected {
            "zh-Hans" => {
                language == "zh"
                    || language.starts_with("zh-hans")
                    || matches!(language.as_str(), "zh-cn" | "zh-sg")
            }
            "zh-Hant" => {
                language.starts_with("zh-hant")
                    || matches!(language.as_str(), "zh-tw" | "zh-hk" | "zh-mo")
            }
            "en" => language == "en" || language.starts_with("en-"),
            "ja" => language == "ja" || language.starts_with("ja-"),
            _ => language.eq_ignore_ascii_case(selected),
        }
    })
}

fn source_initial(id: &str, name: &str) -> String {
    match id {
        "local" => tr("local_initial"),
        "komga" => "Ko".into(),
        "kavita" => "Ka".into(),
        "suwayomi" => "Su".into(),
        "mangadex" => "MD".into(),
        "demo-a" => "A".into(),
        "demo-b" => "B".into(),
        _ => name.chars().take(2).collect(),
    }
}

fn language_label(languages: &[String]) -> String {
    if languages.len() > 1 {
        tr("multilingual")
    } else if languages
        .first()
        .is_some_and(|language| language.starts_with("zh"))
    {
        tr("chinese")
    } else {
        languages
            .first()
            .cloned()
            .unwrap_or_else(|| tr("multilingual"))
    }
}

fn source_update_subtitle(source: &SourceConfig, available: &[RepositorySource]) -> String {
    available
        .iter()
        .find(|candidate| candidate.id == source.id)
        .filter(|candidate| candidate.version > source.version)
        .map(|candidate| format!("v{} → v{}", source.version, candidate.version))
        .unwrap_or_default()
}

fn source_name_units(character: char) -> usize {
    match character {
        'M' | 'W' | 'm' | 'w' | '@' | '%' => 8,
        'A'..='Z' | '0'..='9' => 6,
        value if value.is_ascii() => 4,
        _ => 8,
    }
}

fn source_name_prefix_end(name: &str, budget: usize) -> usize {
    let mut end = 0;
    let mut width = 0;
    for (index, character) in name.char_indices() {
        let next = width + source_name_units(character);
        if next > budget {
            break;
        }
        width = next;
        end = index + character.len_utf8();
    }
    end
}

fn source_number_run(name: &str, hidden_start: usize, hidden_end: usize) -> Option<&str> {
    let mut run_start = None;
    let mut run_length = 0usize;
    let mut longest = None;
    let mut longest_length = 0usize;
    for (index, character) in name
        .char_indices()
        .chain(std::iter::once((name.len(), '\0')))
    {
        if character.is_numeric() {
            run_start.get_or_insert(index);
            run_length += 1;
        } else if let Some(start) = run_start.take() {
            if run_length >= longest_length && start < hidden_end && index > hidden_start {
                longest = Some(&name[start..index.min(hidden_end)]);
                longest_length = run_length;
            }
            run_length = 0;
        }
    }
    longest
}

fn source_display_name(name: &str, available: bool) -> String {
    source_display_name_with_budget(name, (if available { 24 } else { 20 }) * 4, 8)
}

fn source_display_name_with_budget(name: &str, budget: usize, tail_length: usize) -> String {
    // Quarter units approximate Latin width without font or image work.
    let fits = name
        .chars()
        .try_fold(0usize, |width, character| {
            let next = width + source_name_units(character);
            (next <= budget).then_some(next)
        })
        .is_some();
    if fits {
        return name.to_owned();
    }
    let tail_start = name
        .char_indices()
        .rev()
        .take(tail_length)
        .last()
        .map(|(index, _)| index)
        .unwrap_or(0);
    let tail = &name[tail_start..];
    let tail_width = tail.chars().map(source_name_units).sum::<usize>();
    let head_end =
        source_name_prefix_end(&name[..tail_start], budget.saturating_sub(tail_width + 8));
    if let Some(number) = source_number_run(name, head_end, tail_start) {
        let first_width = name.chars().next().map(source_name_units).unwrap_or(0);
        let number_budget = budget.saturating_sub(tail_width + 16 + first_width);
        let mut number_start = number.len();
        let mut number_width = 0;
        for (index, character) in number.char_indices().rev() {
            let next = number_width + source_name_units(character);
            if next > number_budget {
                break;
            }
            number_width = next;
            number_start = index;
        }
        if number_start < number.len() {
            let head_end = source_name_prefix_end(
                &name[..tail_start],
                budget.saturating_sub(tail_width + number_width + 16),
            );
            return format!("{}…{}…{tail}", &name[..head_end], &number[number_start..]);
        }
    }
    format!("{}…{tail}", &name[..head_end])
}

const BOOK_DISPLAY_TITLE_BUDGET: usize = 12 * 4;

pub(super) fn book_display_title(name: &str) -> String {
    if let Some(number) = source_number_run(name, 0, name.len()) {
        if number.chars().map(source_name_units).sum::<usize>() <= BOOK_DISPLAY_TITLE_BUDGET {
            return token_first_display_name(name, number, BOOK_DISPLAY_TITLE_BUDGET);
        }
    }
    let baseline = source_display_name_with_budget(name, BOOK_DISPLAY_TITLE_BUDGET, 4);
    if baseline != name {
        let tail_start = name
            .char_indices()
            .rev()
            .take(4)
            .last()
            .map(|(index, _)| index)
            .unwrap_or(0);
        let tail = &name[tail_start..];
        if !tail.is_ascii() {
            return token_first_display_name(name, tail, BOOK_DISPLAY_TITLE_BUDGET);
        }
    }
    baseline
}

fn token_first_display_name(name: &str, token: &str, budget: usize) -> String {
    let token_width = token.chars().map(source_name_units).sum::<usize>();
    if token_width > budget {
        let end = source_name_prefix_end(token, budget.saturating_sub(8));
        return format!("{}…", token[..end].trim_end());
    }
    let context = match name.find(token) {
        Some(0) => &name[token.len()..],
        Some(start) => &name[..start],
        None => "",
    }
    .trim();
    let end = source_name_prefix_end(context, budget.saturating_sub(token_width + 8));
    let context = context[..end].trim_end();
    if context.is_empty() {
        token.to_owned()
    } else {
        format!("{token}…{context}")
    }
}

fn shared_name_edges(names: &[&str]) -> (usize, usize) {
    let first = names[0];
    let mut prefix = first.len();
    for name in &names[1..] {
        prefix = first[..prefix]
            .chars()
            .zip(name.chars())
            .take_while(|(left, right)| left == right)
            .map(|(character, _)| character.len_utf8())
            .sum();
    }
    let mut suffix = first.len() - prefix;
    for name in &names[1..] {
        suffix = first[first.len() - suffix..]
            .chars()
            .rev()
            .zip(name[prefix..].chars().rev())
            .take_while(|(left, right)| left == right)
            .map(|(character, _)| character.len_utf8())
            .sum();
    }
    (prefix, suffix)
}

fn distinguishing_name_token(name: &str, prefix: usize, suffix: usize, budget: usize) -> &str {
    let core_end = name.len() - suffix;
    let mut start = prefix;
    let mut end = core_end;
    if name[..start]
        .chars()
        .next_back()
        .is_some_and(char::is_alphanumeric)
        && name[start..]
            .chars()
            .next()
            .is_some_and(char::is_alphanumeric)
    {
        start = name[..start]
            .char_indices()
            .rev()
            .find(|(_, character)| !character.is_alphanumeric())
            .map(|(index, character)| index + character.len_utf8())
            .unwrap_or(0);
    }
    if name[..end]
        .chars()
        .next_back()
        .is_some_and(char::is_alphanumeric)
        && name[end..]
            .chars()
            .next()
            .is_some_and(char::is_alphanumeric)
    {
        end = name[end..]
            .char_indices()
            .find(|(_, character)| !character.is_alphanumeric())
            .map(|(index, _)| end + index)
            .unwrap_or(name.len());
    }
    let word = name[start..end].trim();
    if !word.is_empty() && word.chars().map(source_name_units).sum::<usize>() <= budget {
        return word;
    }
    let core = name[prefix..core_end].trim();
    if core.is_empty() { name } else { core }
}

fn resolve_display_name_collisions(
    names: &[&str],
    labels: &mut [String],
    budget: usize,
    leading_token_only: bool,
) {
    // Only already-visible sibling rows are inspected, never the complete library.
    let mut baseline_groups = HashMap::<String, Vec<usize>>::new();
    for (index, label) in labels.iter().enumerate() {
        let key = if leading_token_only {
            label.split('…').next().unwrap_or(label)
        } else {
            label
        };
        baseline_groups
            .entry(key.to_owned())
            .or_default()
            .push(index);
    }
    let mut original_collision_members = vec![false; names.len()];
    for indices in baseline_groups.into_values() {
        let distinct_names = indices
            .iter()
            .map(|index| names[*index])
            .collect::<HashSet<_>>();
        if distinct_names.len() > 1 {
            for index in indices {
                original_collision_members[index] = true;
            }
        }
    }
    // Other baselines are reserved: they may inform a new token but cannot be rewritten.
    for _ in 0..names.len() {
        let mut groups = HashMap::<String, Vec<usize>>::new();
        for (index, label) in labels.iter().enumerate() {
            let key = if leading_token_only {
                label.split('…').next().unwrap_or(label)
            } else {
                label
            };
            groups.entry(key.to_owned()).or_default().push(index);
        }
        let mut changed = false;
        for indices in groups.into_values().filter(|indices| indices.len() > 1) {
            let mut seen = HashSet::new();
            let siblings = indices
                .iter()
                .map(|index| names[*index])
                .filter(|name| seen.insert(*name))
                .collect::<Vec<_>>();
            if siblings.len() < 2 {
                continue;
            }
            let (prefix, suffix) = shared_name_edges(&siblings);
            for index in indices {
                if !original_collision_members[index] {
                    continue;
                }
                let token = distinguishing_name_token(names[index], prefix, suffix, budget);
                let label = token_first_display_name(names[index], token, budget);
                if labels[index] != label {
                    labels[index] = label;
                    changed = true;
                }
            }
        }
        if !changed {
            break;
        }
    }
}

fn resolve_source_name_collisions(rows: &mut [SourceItem], budget: usize) {
    let names = rows.iter().map(|row| row.name.as_str()).collect::<Vec<_>>();
    let mut labels = rows
        .iter()
        .map(|row| row.display_name.to_string())
        .collect::<Vec<_>>();
    resolve_display_name_collisions(&names, &mut labels, budget, false);
    for (row, label) in rows.iter_mut().zip(labels) {
        row.display_name = label.into();
    }
}

fn resolve_book_title_collisions(rows: &mut [BookItem]) {
    let names = rows
        .iter()
        .map(|row| row.title.as_str())
        .collect::<Vec<_>>();
    let mut labels = rows
        .iter()
        .map(|row| row.display_title.to_string())
        .collect::<Vec<_>>();
    // A narrow slot may elide optional context after the leading identity token.
    resolve_display_name_collisions(&names, &mut labels, BOOK_DISPLAY_TITLE_BUDGET, true);
    for (row, label) in rows.iter_mut().zip(labels) {
        row.display_title = label.into();
    }
}

fn source_compact_status(source: &SourceConfig, subtitle: &str) -> String {
    if !source.enabled {
        let label = tr("unavailable");
        let subtitle = source
            .options
            .get("disabled_reason")
            .and_then(Value::as_str)
            .filter(|reason| !reason.trim().is_empty())
            .unwrap_or(subtitle);
        if subtitle.trim().is_empty() || subtitle == label {
            return label;
        }
        return if subtitle.starts_with(label.as_str()) {
            subtitle.to_owned()
        } else {
            format!("{label} · {subtitle}")
        };
    }
    if matches!(source.kind.as_str(), "komga" | "kavita" | "suwayomi") {
        // Explicit fixture records retain their authored connection state.
        if source.options.get("demo").and_then(Value::as_bool) == Some(true)
            && source.options.get("connected").and_then(Value::as_bool) == Some(true)
        {
            return tr("connected");
        }
        return tr(if source.base_url.trim().is_empty() {
            "not_configured"
        } else if source
            .options
            .get("_connection_verified")
            .and_then(Value::as_bool)
            == Some(true)
        {
            "connected"
        } else {
            "configured"
        });
    }
    subtitle.to_owned()
}

fn source_row(source: &SourceConfig, available: &[RepositorySource]) -> SourceItem {
    let update = source_update(source, available);
    let builtin = source_is_builtin(source);
    let subtitle = if !source.enabled {
        source
            .options
            .get("disabled_reason")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .unwrap_or_else(|| tr("unavailable"))
    } else if source.kind == "local" {
        tr("local_formats")
    } else if builtin {
        format!(
            "{} · {}",
            tr("self_hosted"),
            if source
                .options
                .get("connected")
                .and_then(Value::as_bool)
                .unwrap_or(false)
            {
                tr("connected")
            } else if source.base_url.is_empty() {
                tr("not_configured")
            } else {
                tr("configured")
            }
        )
    } else {
        format!(
            "{} · v{}",
            language_label(&source.languages),
            source.version
        )
    };
    SourceItem {
        id: source.id.clone().into(),
        name: source.name.clone().into(),
        display_name: source_display_name(&source.name, source.enabled).into(),
        compact_status: source_compact_status(source, &subtitle).into(),
        subtitle: subtitle.into(),
        update_subtitle: source_update_subtitle(source, available).into(),
        initial: source_initial(&source.id, &source.name).into(),
        available: source.enabled,
        installed: true,
        update,
        builtin,
    }
}

fn chapter_date(value: &str, large: bool) -> String {
    if let Ok(date) =
        chrono::NaiveDate::parse_from_str(value.get(..10).unwrap_or(value), "%Y-%m-%d")
    {
        return date
            .format(if large { "%Y · %m · %d" } else { "%m · %d" })
            .to_string();
    }
    value.to_owned()
}

#[cfg(test)]
fn chapter_day(value: &str) -> Option<chrono::NaiveDate> {
    chrono::NaiveDate::parse_from_str(value.get(..10).unwrap_or(value), "%Y-%m-%d").ok()
}

fn relative_date(timestamp: i64, demo: bool) -> Option<String> {
    if timestamp <= 0 || chrono::Utc.timestamp_opt(timestamp, 0).single().is_none() {
        return None;
    }
    let days = (view_today(demo) - local_instant(timestamp, demo).date_naive()).num_days();
    match days {
        0 => Some(tr("today")),
        1.. => Some(format!("{days} {}", tr("days_ago"))),
        _ => None,
    }
}

fn local_instant(timestamp: i64, demo: bool) -> chrono::DateTime<chrono::FixedOffset> {
    let instant = chrono::Utc
        .timestamp_opt(timestamp, 0)
        .single()
        .unwrap_or_else(chrono::Utc::now);
    if demo {
        instant
            .with_timezone(&chrono::FixedOffset::east_opt(8 * 3600).expect("Fixed demo time zone"))
    } else {
        instant.with_timezone(&chrono::Local).fixed_offset()
    }
}

fn history_date(date: chrono::NaiveDate, demo: bool) -> String {
    format!(
        "{} {}",
        history_date_label(date, demo),
        date.format("%m · %d")
    )
}

fn history_date_label(date: chrono::NaiveDate, demo: bool) -> String {
    let today = view_today(demo);
    if date == today {
        tr("today")
    } else if date == today - chrono::Duration::days(1) {
        tr("yesterday")
    } else {
        tr(&format!(
            "weekday_{}",
            date.weekday().num_days_from_sunday()
        ))
    }
}

fn view_today(demo: bool) -> chrono::NaiveDate {
    if demo {
        chrono::NaiveDate::from_ymd_opt(2026, 11, 4).expect("Fixed demo date")
    } else {
        chrono::Local::now().date_naive()
    }
}

fn search_pages(groups: &[SearchGroup], height: u32, large: bool) -> Vec<Vec<SearchGroup>> {
    let content = height as f32
        - if large {
            36.0 + 159.0 + 50.0 + 80.0
        } else {
            28.0 + 119.0 + 36.0 + 72.0
        };
    let cost = |group: &SearchGroup| {
        if group.books.row_count() > 0 {
            if large { 286.0 } else { 217.0 }
        } else {
            if large { 68.0 } else { 52.0 }
        }
    };
    if groups.iter().map(cost).sum::<f32>() <= content {
        return vec![groups.to_vec()];
    }
    let limit = (content - if large { 68.0 } else { 52.0 }).max(100.0);
    let mut pages = Vec::new();
    let mut page = Vec::new();
    let mut used = 0.0;
    for group in groups {
        let next = cost(group);
        if !page.is_empty() && used + next > limit {
            pages.push(std::mem::take(&mut page));
            used = 0.0;
        }
        page.push(group.clone());
        used += next;
    }
    if !page.is_empty() {
        pages.push(page);
    }
    if pages.is_empty() {
        pages.push(Vec::new());
    }
    pages
}

#[cfg(test)]
mod localization_tests {
    use super::*;

    #[test]
    fn storage_labels_keep_unknown_zero_and_sparse_file_sizes_distinct() {
        assert_eq!(storage_bytes_text(None), "—");
        assert_eq!(storage_bytes_text(Some(0)), "0 B");
        assert_eq!(storage_bytes_text(Some(1)), "1 B");
        assert_eq!(storage_bytes_text(Some(1_500)), "1.5 KB");
        let unknown = storage_value_projection(None, Some(100));
        assert!(!unknown.known);
        assert_eq!(unknown.fraction, 0.0);
        assert_eq!(storage_value_projection(Some(1), None).fraction, 0.0);
        assert_eq!(storage_value_projection(Some(1), Some(2)).fraction, 0.5);
        assert_eq!(storage_value_projection(Some(0), Some(0)).fraction, 0.0);
    }

    #[test]
    fn detail_description_preview_bounds_unicode_and_preserves_the_full_summary_source() {
        let short = "A short description with a \u{1f4d6}";
        assert_eq!(description_preview(short), short);
        let book = Book {
            description: "\u{1f4d6}\u{4e66}\u{6587}".repeat(1_024),
            ..Book::default()
        };
        let full_length = book.description.len();
        let preview = description_preview(&book.description);
        assert_eq!(preview.chars().count(), 2_048);
        assert!(preview.ends_with('\u{4e66}'));
        assert!(book.description.starts_with(preview));
        assert_eq!(book.description.len(), full_length);
        assert_eq!(book.description.chars().count(), 3_072);
        assert!(book.description.ends_with('\u{6587}'));
        assert_eq!(description_preview(""), "");
    }

    #[test]
    fn cover_progress_starts_after_the_first_page_and_preserves_completion() {
        assert_eq!(book_cover_progress(None), 0.0);
        for (page, total, completed, expected) in [
            (0, 40, false, 0.0),
            (1, 40, false, 0.0),
            (2, 40, false, 0.05),
            (9, 40, false, 0.225),
            (12, 40, false, 0.3),
            (24, 40, false, 0.6),
            (32, 40, false, 0.8),
            (40, 40, false, 1.0),
            (1, 40, true, 1.0),
            (40, 40, true, 1.0),
        ] {
            let progress = Progress {
                page,
                total,
                completed,
                ..Progress::default()
            };
            assert_eq!(book_cover_progress(Some(&progress)), expected);
        }
    }

    #[test]
    fn provider_status_enums_are_localized_without_case_leaks() {
        for value in ["ongoing", "ONGOING", " Ongoing "] {
            assert_eq!(status_label(value), tr("ongoing"));
        }
        for value in ["COMPLETED", "ENDED", "finished"] {
            assert_eq!(status_label(value), tr("completed"));
        }
        for value in ["HIATUS", "on_hold", "Paused"] {
            assert_eq!(status_label(value), tr("paused"));
        }
        assert_eq!(status_label("CANCELLED"), tr("discontinued"));
        let localized = tr("ongoing");
        assert_eq!(status_label(&localized), localized);
    }

    #[test]
    fn installed_source_pagers_appear_only_when_the_real_groups_overflow() {
        assert_eq!(
            installed_source_window(7, 4, 824, false, 1),
            InstalledSourceWindow {
                page: 1,
                page_count: 1,
                offset: 0,
                limit: 7,
            }
        );
        assert_eq!(
            installed_source_window(8, 4, 824, false, 2),
            InstalledSourceWindow {
                page: 2,
                page_count: 2,
                offset: 6,
                limit: 6,
            }
        );
        assert_eq!(installed_source_window(8, 8, 824, false, 2).page_count, 1);
        assert_eq!(installed_source_window(11, 4, 1240, true, 1).page_count, 1);
        assert_eq!(
            installed_source_window(12, 4, 1240, true, usize::MAX),
            InstalledSourceWindow {
                page: 2,
                page_count: 2,
                offset: 10,
                limit: 10,
            }
        );
        assert_eq!(installed_source_window(12, 12, 1240, true, 1).page_count, 1);
        assert_eq!(
            installed_source_window(12, 4, 1320, true, 2),
            InstalledSourceWindow {
                page: 1,
                page_count: 1,
                offset: 0,
                limit: 12,
            }
        );
        assert_eq!(installed_source_window(0, 0, 824, false, 0).page_count, 1);
    }

    #[test]
    fn history_date_groups_restart_columns_after_an_odd_group() {
        let rows = ["today", "today", "today", "yesterday", "yesterday"]
            .into_iter()
            .map(|date| HistoryItem {
                date: date.into(),
                ..HistoryItem::default()
            })
            .collect::<Vec<_>>();
        let mut scribe = rows.clone();
        apply_history_layout(&mut scribe, true);
        assert_eq!(
            scribe.iter().map(|row| row.column).collect::<Vec<_>>(),
            vec![0, 1, 0, 0, 1]
        );
        assert_eq!(
            scribe.iter().map(|row| row.row_index).collect::<Vec<_>>(),
            vec![0, 0, 1, 0, 0]
        );
        assert_eq!(
            scribe.iter().map(|row| row.group_start).collect::<Vec<_>>(),
            vec![true, false, false, true, false]
        );
        assert_eq!(
            scribe
                .iter()
                .map(|row| row.group_offset)
                .collect::<Vec<_>>(),
            vec![0, 0, 0, 308, 308]
        );
        assert_eq!(
            (scribe[0].group_row_count, scribe[3].group_row_count),
            (2, 1)
        );

        let mut paperwhite = rows;
        apply_history_layout(&mut paperwhite, false);
        assert!(paperwhite.iter().all(|row| row.column == 0));
        assert_eq!(
            paperwhite
                .iter()
                .map(|row| row.row_index)
                .collect::<Vec<_>>(),
            vec![0, 1, 2, 0, 1]
        );
        assert_eq!(paperwhite[3].group_offset, 272);
    }

    #[test]
    fn search_groups_fit_each_profile_and_keep_their_source_order() {
        let groups = ["first", "second", "third"]
            .into_iter()
            .map(|id| SearchGroup {
                id: id.into(),
                books: model([BookItem::default()]),
                ..SearchGroup::default()
            })
            .collect::<Vec<_>>();
        let paperwhite = search_pages(&groups, 824, false);
        assert_eq!(
            paperwhite.iter().map(Vec::len).collect::<Vec<_>>(),
            vec![2, 1]
        );
        assert_eq!(
            paperwhite
                .iter()
                .flatten()
                .map(|group| group.id.as_str())
                .collect::<Vec<_>>(),
            vec!["first", "second", "third"]
        );
        assert_eq!(search_pages(&groups, 1240, true).len(), 1);
    }

    #[test]
    fn source_language_filters_match_provider_language_codes() {
        for language in ["zh", "zh-CN", "zh-Hans", "zh-SG"] {
            assert!(source_matches_language(&[language.into()], "zh-Hans"));
            assert!(!source_matches_language(&[language.into()], "zh-Hant"));
        }
        for language in ["zh-TW", "zh-HK", "zh-Hant"] {
            assert!(source_matches_language(&[language.into()], "zh-Hant"));
            assert!(!source_matches_language(&[language.into()], "zh-Hans"));
        }
        assert!(source_matches_language(
            &["en-US".into(), "ja".into()],
            "en"
        ));
        assert!(source_matches_language(
            &["en-US".into(), "ja".into()],
            "ja"
        ));
        assert!(source_matches_language(&[], "all"));
        assert!(!source_matches_language(&[], "en"));
    }

    #[test]
    fn source_update_versions_follow_the_first_selected_repository_candidate() {
        let installed = SourceConfig {
            id: "versioned-source".into(),
            name: "Fixture source".into(),
            kind: "wasm".into(),
            version: 12,
            enabled: true,
            languages: vec!["en".into()],
            ..SourceConfig::default()
        };
        let candidate = |id: &str, version| RepositorySource {
            id: id.into(),
            name: "Fixture source".into(),
            version,
            languages: vec!["en".into()],
            download_url: String::new(),
            icon_url: String::new(),
            base_url: String::new(),
        };
        let available = [
            candidate("another-source", 99),
            candidate(&installed.id, 13),
            candidate(&installed.id, 20),
        ];
        assert_eq!(source_update_subtitle(&installed, &available), "v12 → v13");
        let row = source_row(&installed, &available);
        assert_eq!(row.update_subtitle.as_str(), "v12 → v13");
        assert!(row.subtitle.as_str().ends_with("v12"));
        for first_version in [11, 12] {
            assert!(
                source_update_subtitle(
                    &installed,
                    &[
                        candidate(&installed.id, first_version),
                        candidate(&installed.id, 20),
                    ]
                )
                .is_empty()
            );
        }
        assert!(source_update_subtitle(&installed, &[]).is_empty());
    }

    #[test]
    fn compact_source_names_keep_middle_numbers_and_unicode_tail_identifiers() {
        let names = (0..25)
            .map(|index| {
                format!("Configured local fixture {index:02} with a deliberately long source name")
            })
            .collect::<Vec<_>>();
        for available in [true, false] {
            let labels = names
                .iter()
                .map(|name| source_display_name(name, available))
                .collect::<Vec<_>>();
            assert_eq!(
                labels
                    .iter()
                    .collect::<std::collections::HashSet<_>>()
                    .len(),
                names.len()
            );
            for (index, label) in labels.iter().enumerate() {
                assert!(label.starts_with("Con"));
                assert!(label.contains(format!("{index:02}").as_str()));
                assert!(label.ends_with("rce name"));
                assert!(
                    label.chars().map(source_name_units).sum::<usize>()
                        <= (if available { 24 } else { 20 }) * 4
                );
            }
        }
        let tail = "\u{1f4d6}".repeat(8);
        let unicode_name = format!("{}{tail}", "\u{4e66}".repeat(30));
        for available in [true, false] {
            let label = source_display_name(&unicode_name, available);
            assert!(label.starts_with('\u{4e66}'));
            assert!(label.ends_with(tail.as_str()));
        }
        assert_eq!(source_display_name("Short source", true), "Short source");
        assert_eq!(
            source_display_name("A1234567890 x 22222 abcdefgh", true),
            "A…4567890…abcdefgh"
        );
        let source = SourceConfig {
            name: names[0].clone(),
            enabled: true,
            ..SourceConfig::default()
        };
        let row = source_row(&source, &[]);
        assert_eq!(row.name.as_str(), source.name.as_str());
        assert_ne!(row.display_name.as_str(), row.name.as_str());
    }

    #[test]
    fn search_shortcut_names_keep_middle_tokens_and_unicode_suffixes() {
        let labels = (0..25)
            .map(|index| {
                let name = format!(
                    "Configured local fixture {index:02} with a deliberately long source name"
                );
                let label = source_display_name_with_budget(&name, 14 * 4, 4);
                assert_eq!(label, format!("C…{index:02}…name"));
                label
            })
            .collect::<std::collections::HashSet<_>>();
        assert_eq!(labels.len(), 25);
        let tail = "\u{1f4d6}".repeat(4);
        let unicode_name = format!("{}{tail}", "\u{4e66}".repeat(30));
        let label = source_display_name_with_budget(&unicode_name, 14 * 4, 4);
        assert!(label.starts_with('\u{4e66}'));
        assert!(label.ends_with(tail.as_str()));
        for name in ["Short source", "MangaDex", "\u{4e66}\u{6e90}"] {
            assert_eq!(source_display_name_with_budget(name, 14 * 4, 4), name);
        }
    }

    #[test]
    fn colliding_search_shortcuts_expose_real_middle_words() {
        for (names, tokens) in [
            (
                vec![
                    "Actual empty fixture",
                    "Actual error fixture",
                    "Actual fast fixture",
                ],
                vec!["empty", "error", "fast"],
            ),
            (
                vec!["Actual slow fixture", "Actual next fixture"],
                vec!["slow", "next"],
            ),
        ] {
            let mut rows = names
                .iter()
                .map(|name| SourceItem {
                    name: (*name).into(),
                    display_name: source_display_name_with_budget(name, 14 * 4, 4).into(),
                    ..SourceItem::default()
                })
                .collect::<Vec<_>>();
            assert!(
                rows.windows(2)
                    .all(|pair| pair[0].display_name == pair[1].display_name)
            );
            resolve_source_name_collisions(&mut rows, 14 * 4);
            for ((row, name), token) in rows.iter().zip(names).zip(tokens) {
                assert_eq!(row.name.as_str(), name);
                assert!(row.display_name.as_str().starts_with(token));
                assert!(
                    row.display_name
                        .chars()
                        .map(source_name_units)
                        .sum::<usize>()
                        <= 14 * 4
                );
            }
        }
    }

    #[test]
    fn source_collision_refinement_reserves_noncolliding_baselines() {
        let names = [
            "ABCD empty fixture",
            "ABCD error fixture",
            "errorWith a deliberately long ABCD",
        ];
        let mut rows = names
            .iter()
            .map(|name| SourceItem {
                name: (*name).into(),
                display_name: source_display_name_with_budget(name, 14 * 4, 4).into(),
                ..SourceItem::default()
            })
            .collect::<Vec<_>>();
        assert_eq!(rows[0].display_name, rows[1].display_name);
        assert_ne!(rows[1].display_name, rows[2].display_name);
        let reserved = rows[2].display_name.clone();
        resolve_source_name_collisions(&mut rows, 14 * 4);
        assert_eq!(
            rows[2].display_name.as_str().as_bytes(),
            reserved.as_str().as_bytes()
        );
        assert_eq!(
            rows.iter()
                .map(|row| row.display_name.as_str())
                .collect::<HashSet<_>>()
                .len(),
            3
        );
        assert!(rows[0].display_name.as_str().starts_with("empty"));
        assert!(rows[1].display_name.as_str().contains("error"));
        assert_eq!(
            rows.iter().map(|row| row.name.as_str()).collect::<Vec<_>>(),
            names.to_vec()
        );
    }

    #[test]
    fn shortcut_collision_refinement_keeps_noncolliding_numeric_and_unicode_baselines() {
        let mut names = (0..3)
            .map(|index| {
                format!("Configured local fixture {index:02} with a deliberately long source name")
            })
            .collect::<Vec<_>>();
        names.push(format!(
            "{}{}",
            "\u{4e66}".repeat(30),
            "\u{1f4d6}".repeat(4)
        ));
        names.push("MangaDex".into());
        let mut rows = names
            .iter()
            .map(|name| SourceItem {
                name: name.as_str().into(),
                display_name: source_display_name_with_budget(name, 14 * 4, 4).into(),
                ..SourceItem::default()
            })
            .collect::<Vec<_>>();
        let before = rows
            .iter()
            .map(|row| row.display_name.clone())
            .collect::<Vec<_>>();
        resolve_source_name_collisions(&mut rows, 14 * 4);
        assert_eq!(
            rows.iter()
                .map(|row| row.display_name.clone())
                .collect::<Vec<_>>(),
            before
        );
        assert_eq!(
            rows.iter().map(|row| row.name.as_str()).collect::<Vec<_>>(),
            names.iter().map(String::as_str).collect::<Vec<_>>()
        );
    }

    #[test]
    fn collision_tokens_preserve_utf8_boundaries_inside_long_shared_words() {
        let prefix = "\u{4e66}".repeat(30);
        let names = [
            format!("{prefix}\u{7a7a} shared fixture"),
            format!("{prefix}\u{9519} shared fixture"),
        ];
        let mut rows = names
            .iter()
            .map(|name| SourceItem {
                name: name.as_str().into(),
                display_name: source_display_name_with_budget(name, 14 * 4, 4).into(),
                ..SourceItem::default()
            })
            .collect::<Vec<_>>();
        assert_eq!(rows[0].display_name, rows[1].display_name);
        resolve_source_name_collisions(&mut rows, 14 * 4);
        assert!(rows[0].display_name.as_str().starts_with('\u{7a7a}'));
        assert!(rows[1].display_name.as_str().starts_with('\u{9519}'));
        assert!(rows.iter().all(|row| {
            row.display_name
                .chars()
                .map(source_name_units)
                .sum::<usize>()
                <= 14 * 4
        }));
    }

    #[test]
    fn book_fallbacks_expose_complete_actual_series_numbers_on_visible_pages() {
        let mut rows = (0..40)
            .map(|index| {
                let title = format!("Local fixture series {index:03}");
                BookItem {
                    id: format!("series-{index:03}").into(),
                    display_title: book_display_title(&title).into(),
                    title: title.into(),
                    ..BookItem::default()
                }
            })
            .collect::<Vec<_>>();
        for page in rows.chunks_mut(15) {
            resolve_book_title_collisions(page);
        }
        for (index, row) in rows.iter().enumerate() {
            assert_eq!(
                row.title.as_str(),
                format!("Local fixture series {index:03}")
            );
            assert!(
                row.display_title
                    .as_str()
                    .starts_with(format!("{index:03}").as_str())
            );
            assert!(
                row.display_title
                    .chars()
                    .map(source_name_units)
                    .sum::<usize>()
                    <= BOOK_DISPLAY_TITLE_BUDGET
            );
        }
        assert_eq!(
            rows.iter()
                .map(|row| row.display_title.as_str())
                .collect::<HashSet<_>>()
                .len(),
            40
        );
    }

    #[test]
    fn book_fallback_collisions_use_real_words_and_do_not_invent_duplicate_title_ids() {
        let names = ["Local fixture series 000", "Local fixture volume 000"];
        let mut rows = names
            .iter()
            .enumerate()
            .map(|(index, title)| BookItem {
                id: format!("different-book-{index}").into(),
                title: (*title).into(),
                display_title: book_display_title(title).into(),
                ..BookItem::default()
            })
            .collect::<Vec<_>>();
        assert_eq!(rows[0].display_title, rows[1].display_title);
        resolve_book_title_collisions(&mut rows);
        assert!(rows[0].display_title.as_str().starts_with("series"));
        assert!(rows[1].display_title.as_str().starts_with("volume"));
        assert_eq!(
            rows.iter()
                .map(|row| row.title.as_str())
                .collect::<Vec<_>>(),
            names.to_vec()
        );
        rows[1].title = rows[0].title.clone();
        rows[1].display_title = book_display_title(rows[1].title.as_str()).into();
        rows[0].display_title = book_display_title(rows[0].title.as_str()).into();
        resolve_book_title_collisions(&mut rows);
        assert_eq!(rows[0].display_title, rows[1].display_title);
        assert_ne!(rows[0].id, rows[1].id);
    }

    #[test]
    fn book_display_projection_retains_the_complete_actual_title() -> Result<()> {
        let fixture = crate::app::tests::DemoRouteFixture::new(
            "library",
            crate::platform::DeviceProfile::Scribe,
            true,
        );
        let mut book = fixture.app.store.books()?.remove(0);
        book.title = "Local fixture series 016".into();
        let row = fixture
            .app
            .book_item(&book, &BookViewMetadata::default(), false);
        assert_eq!(row.title.as_str(), book.title);
        assert!(row.display_title.as_str().starts_with("016"));
        assert_ne!(row.display_title.as_str(), row.title.as_str());
        Ok(())
    }

    #[test]
    fn compact_source_status_distinguishes_configuration_from_verified_connections() {
        let mut source = SourceConfig {
            id: "native-source".into(),
            name: "Native source".into(),
            kind: "komga".into(),
            enabled: true,
            ..SourceConfig::default()
        };
        source.options.insert("connected".into(), Value::Bool(true));
        assert_eq!(
            source_compact_status(&source, "Self-hosted"),
            tr("not_configured")
        );
        source.options.insert("demo".into(), Value::Bool(true));
        assert_eq!(
            source_compact_status(&source, "Self-hosted"),
            tr("connected")
        );
        source.options.remove("demo");
        source.base_url = "https://server.example.test/".into();
        assert_eq!(
            source_compact_status(&source, "Self-hosted"),
            tr("configured")
        );
        source
            .options
            .insert("_connection_verified".into(), Value::Bool(true));
        assert_eq!(
            source_compact_status(&source, "Self-hosted"),
            tr("connected")
        );
        source.enabled = false;
        source.options.insert(
            "disabled_reason".into(),
            Value::String("Browser verification required".into()),
        );
        let row = source_row(&source, &[]);
        assert_eq!(row.subtitle.as_str(), "Browser verification required");
        assert!(
            row.compact_status
                .as_str()
                .starts_with(tr("unavailable").as_str())
        );
        assert!(
            row.compact_status
                .as_str()
                .contains("Browser verification required")
        );
    }
}

#[cfg(test)]
mod demo_unread_projection_tests {
    use super::*;
    use crate::{app::tests::DemoRouteFixture, platform::DeviceProfile};

    fn projected_library(app: &App) -> Result<Vec<BookItem>> {
        let query = LibraryQuery {
            category: app.library_filter,
            demo_order: app.options.demo,
            sort_key: Some(app.interactions.library_sort.id()),
            ascending: app.interactions.library_ascending,
            filters: app.interactions.library_filters,
            ..LibraryQuery::default()
        };
        let page_size = if app.platform.profile().is_scribe() {
            18
        } else {
            8
        };
        let first = app.store.library_view_page(query, 1, page_size)?;
        let total = first.total;
        let mut books = first.items;
        for page in 2..=total.div_ceil(page_size) {
            books.extend(app.store.library_view_page(query, page, page_size)?.items);
        }
        assert_eq!(books.len(), total);
        books
            .iter()
            .map(|book| app.view_book(book, false))
            .collect()
    }

    #[test]
    fn demo_unread_filters_and_sorting_project_persisted_counts() -> Result<()> {
        for profile in [DeviceProfile::Pw5, DeviceProfile::Scribe] {
            let mut fixture = DemoRouteFixture::new("library", profile, true);
            let persisted: HashMap<_, _> = fixture
                .app
                .store
                .books()?
                .into_iter()
                .filter(|book| book.in_library)
                .map(|book| (book.id, book.unread as i32))
                .collect();
            assert!(persisted.values().any(|unread| *unread == 0));
            assert!(persisted.values().any(|unread| *unread > 0));
            fixture.app.dispatch("library", "sort", "unread-count")?;
            for filter in [0_i8, 1, -1] {
                if filter != 0 {
                    fixture.app.dispatch("library", "cycle-filter", "unread")?;
                }
                assert_eq!(fixture.app.interactions.library_filters[1], filter);
                let expected: HashMap<_, _> = persisted
                    .iter()
                    .filter(|(_, unread)| match filter {
                        1 => **unread > 0,
                        -1 => **unread == 0,
                        _ => true,
                    })
                    .map(|(id, unread)| (id.clone(), *unread))
                    .collect();
                assert!(!expected.is_empty());
                for ascending in [true, false] {
                    fixture.app.dispatch(
                        "library",
                        "sort-direction",
                        if ascending { "ascending" } else { "descending" },
                    )?;
                    let rows = projected_library(&fixture.app)?;
                    let actual: HashMap<_, _> = rows
                        .iter()
                        .map(|row| (row.id.to_string(), row.unread))
                        .collect();
                    assert_eq!(actual.len(), rows.len());
                    assert_eq!(actual, expected);
                    let mut expected_counts = expected.values().copied().collect::<Vec<_>>();
                    expected_counts.sort_unstable();
                    if !ascending {
                        expected_counts.reverse();
                    }
                    assert_eq!(
                        rows.iter().map(|row| row.unread).collect::<Vec<_>>(),
                        expected_counts,
                    );
                }
            }
        }
        Ok(())
    }

    #[test]
    fn demo_mark_read_projects_current_metadata_over_a_stale_book() -> Result<()> {
        let mut fixture = DemoRouteFixture::new("library", DeviceProfile::Scribe, true);
        let stale = fixture
            .app
            .store
            .book(&crate::demo::primary_id())?
            .context("The primary demo book is missing")?;
        assert!(stale.unread > 0);
        assert_eq!(
            fixture.app.view_book(&stale, false)?.unread,
            stale.unread as i32,
        );
        fixture.app.dispatch("library", "select", &stale.id)?;
        fixture.app.dispatch("library", "batch-read", "")?;
        let completion = fixture.app.job_rx.recv_timeout(Duration::from_secs(10))?;
        let result = completion.result?;
        assert!(matches!(&result, JobResult::InteractionDone(_)));
        fixture.app.complete_job(result, &completion.context)?;
        assert!(
            fixture
                .app
                .store
                .chapters(&stale.id)?
                .iter()
                .all(|chapter| chapter.read)
        );
        assert_eq!(fixture.app.store.book(&stale.id)?.unwrap().unread, 0);
        let metadata = fixture.app.store.book_view_metadata(&stale.id)?;
        assert_eq!(fixture.app.book_item(&stale, &metadata, false).unread, 0);
        assert_eq!(fixture.app.view_book(&stale, false)?.unread, 0);
        Ok(())
    }
}

#[cfg(test)]
mod reader_metadata_tests {
    use super::*;
    use crate::domain::Bookmark;
    use std::{
        cell::RefCell,
        collections::BTreeMap,
        ffi::{CStr, c_int, c_void},
        hint::black_box,
    };

    use rusqlite::{Connection, ffi, params};
    use serde::Serialize;
    use sha2::{Digest, Sha256};
    use tempfile::TempDir;

    const BASELINE_VIEW_SHA256: &str =
        "0a3f9fd72916860378aa267d3b4ef95e1f3d52acd5a08ca7cd17795564b20bd2";
    const CHAPTERS_PER_BOOK: usize = 12;
    const CURRENT_CHAPTER: usize = 4;
    const CURRENT_PAGE: u32 = 5;
    const TOTAL_PAGES: u32 = 32;

    struct ReaderFixture {
        store: Store,
        reader: ReaderState,
        displayed: ReaderSession,
        committed: Progress,
        _directory: TempDir,
    }

    fn fixture(book_count: usize) -> Result<ReaderFixture> {
        let directory = tempfile::tempdir()?;
        let store = Store::open(directory.path().join("reader.sqlite3"))?;
        store.save_source(&SourceConfig {
            id: "local".into(),
            name: "Local fixture".into(),
            kind: "local".into(),
            enabled: true,
            ..SourceConfig::default()
        })?;
        let connection = store.test_connection();
        let transaction = connection.unchecked_transaction()?;
        let today = view_today(false).format("%Y-%m-%d").to_string();
        let now = chrono::Utc::now().timestamp();
        let oldest = now - (book_count * 16) as i64;
        let latest = now;
        {
            let mut books = transaction.prepare(
                "INSERT INTO books (id,source_id,remote_id,title,in_library,category,updated_at,document) VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
            )?;
            let mut chapters = transaction.prepare(
                "INSERT INTO chapters (id,book_id,source_id,remote_id,number,document) VALUES (?1,?2,?3,?4,?5,?6)",
            )?;
            let mut progress = transaction.prepare(
                "INSERT INTO progress (chapter_id,book_id,page,total,completed,updated_at) VALUES (?1,?2,?3,?4,?5,?6)",
            )?;
            for index in 0..book_count {
                let book = Book {
                    id: format!("reader-book-{index:05}"),
                    source_id: "local".into(),
                    remote_id: index.to_string(),
                    title: format!("Reader performance fixture {index:05}"),
                    author: "Fixture author".into(),
                    description: "A deterministic metadata fixture with indexed identities, persisted reading history, and daily chapter updates.".into(),
                    tags: vec!["Adventure".into(), "Mystery".into()],
                    status: "ongoing".into(),
                    cover: String::new(),
                    chapter_count: CHAPTERS_PER_BOOK as u32,
                    unread: (CHAPTERS_PER_BOOK - CURRENT_CHAPTER + 1) as u32,
                    category: "reading".into(),
                    in_library: true,
                    updated_at: now,
                };
                books.execute(params![
                    book.id,
                    book.source_id,
                    book.remote_id,
                    book.title,
                    book.in_library,
                    book.category,
                    book.updated_at,
                    serde_json::to_string(&book)?,
                ])?;
                for number in 1..=CHAPTERS_PER_BOOK {
                    let chapter = Chapter {
                        id: format!("{}-chapter-{number:02}", book.id),
                        book_id: book.id.clone(),
                        source_id: book.source_id.clone(),
                        remote_id: number.to_string(),
                        number: number as f64,
                        title: format!("Chapter {number}"),
                        date: today.clone(),
                        page_count: TOTAL_PAGES,
                        downloaded: false,
                        read: number < CURRENT_CHAPTER,
                    };
                    chapters.execute(params![
                        chapter.id,
                        chapter.book_id,
                        chapter.source_id,
                        chapter.remote_id,
                        chapter.number,
                        serde_json::to_string(&chapter)?,
                    ])?;
                    if number <= CURRENT_CHAPTER {
                        let updated_at = if index == 0 && number == CURRENT_CHAPTER {
                            latest
                        } else {
                            oldest + (index * 16 + number) as i64
                        };
                        progress.execute(params![
                            chapter.id,
                            book.id,
                            if chapter.read {
                                TOTAL_PAGES
                            } else {
                                CURRENT_PAGE
                            },
                            TOTAL_PAGES,
                            chapter.read,
                            updated_at,
                        ])?;
                    }
                }
            }
        }
        transaction.commit()?;
        let book_id = "reader-book-00000";
        let chapter_id = format!("{book_id}-chapter-{CURRENT_CHAPTER:02}");
        let reader = ReaderState::new(
            store.chapters(book_id)?,
            &chapter_id,
            CURRENT_PAGE,
            &Preferences::default(),
        )?;
        let displayed = ReaderSession {
            book_id: book_id.into(),
            chapter_id: chapter_id.clone(),
            page: reader.page,
            total: reader.total,
            ..ReaderSession::default()
        };
        let committed = store
            .chapter_progress(&chapter_id)?
            .context("Fixture progress is missing")?;
        store.toggle_bookmark(&Bookmark {
            book_id: book_id.into(),
            chapter_id,
            page: CURRENT_PAGE,
            created_at: latest,
        })?;
        // Complete the successor after the session snapshot was created. Both
        // strategies must observe the worker's persisted download update.
        let successor = format!("{book_id}-chapter-{:02}", CURRENT_CHAPTER + 1);
        let image_path = directory.path().join("downloaded-successor.png");
        image::GrayImage::from_pixel(2, 2, image::Luma([255])).save(&image_path)?;
        let pages: Vec<_> = (1..=TOTAL_PAGES)
            .map(|page| Page {
                url: format!("local://reader-benchmark/successor/{page}"),
                local_path: Some(image_path.clone()),
                ..Page::default()
            })
            .collect();
        store.save_pages(&successor, &pages)?;
        store.set_chapter_downloaded(&successor, true)?;
        Ok(ReaderFixture {
            _directory: directory,
            store,
            reader,
            displayed,
            committed,
        })
    }

    #[derive(Debug, Clone, Default, Serialize)]
    struct SqlStatistics {
        statements: usize,
        read_statements: usize,
        write_statements: usize,
        full_scan_steps: u64,
        virtual_machine_steps: u64,
        by_sql: BTreeMap<String, usize>,
    }

    struct SqlTrace<'a> {
        connection: &'a Connection,
        statistics: Box<RefCell<SqlStatistics>>,
    }

    unsafe extern "C" fn trace_callback(
        event: u32,
        context: *mut c_void,
        statement: *mut c_void,
        _: *mut c_void,
    ) -> c_int {
        if context.is_null() || statement.is_null() {
            return 0;
        }
        let cell = unsafe { &*context.cast::<RefCell<SqlStatistics>>() };
        let Ok(mut statistics) = cell.try_borrow_mut() else {
            return 0;
        };
        let statement = statement.cast::<ffi::sqlite3_stmt>();
        if event == ffi::SQLITE_TRACE_STMT as u32 {
            statistics.statements += 1;
            if unsafe { ffi::sqlite3_stmt_readonly(statement) } != 0 {
                statistics.read_statements += 1;
            } else {
                statistics.write_statements += 1;
            }
            let sql = unsafe { ffi::sqlite3_sql(statement) };
            if !sql.is_null() {
                let sql = unsafe { CStr::from_ptr(sql) }.to_string_lossy();
                *statistics.by_sql.entry(sql.into_owned()).or_default() += 1;
            }
            unsafe {
                ffi::sqlite3_stmt_status(statement, ffi::SQLITE_STMTSTATUS_FULLSCAN_STEP, 1);
                ffi::sqlite3_stmt_status(statement, ffi::SQLITE_STMTSTATUS_VM_STEP, 1);
            }
        } else if event == ffi::SQLITE_TRACE_PROFILE as u32 {
            statistics.full_scan_steps += unsafe {
                ffi::sqlite3_stmt_status(statement, ffi::SQLITE_STMTSTATUS_FULLSCAN_STEP, 0)
            } as u64;
            statistics.virtual_machine_steps +=
                unsafe { ffi::sqlite3_stmt_status(statement, ffi::SQLITE_STMTSTATUS_VM_STEP, 0) }
                    as u64;
        }
        0
    }

    impl<'a> SqlTrace<'a> {
        fn new(connection: &'a Connection) -> Result<Self> {
            let trace = Self {
                connection,
                statistics: Box::default(),
            };
            let code = unsafe {
                ffi::sqlite3_trace_v2(
                    connection.handle(),
                    (ffi::SQLITE_TRACE_STMT | ffi::SQLITE_TRACE_PROFILE) as u32,
                    Some(trace_callback),
                    trace.statistics.as_ref() as *const RefCell<SqlStatistics> as *mut c_void,
                )
            };
            anyhow::ensure!(code == ffi::SQLITE_OK, "Install SQLite statement tracing");
            Ok(trace)
        }

        fn statistics(&self) -> SqlStatistics {
            self.statistics.borrow().clone()
        }
    }

    impl Drop for SqlTrace<'_> {
        fn drop(&mut self) {
            // SQLite must release the callback before its boxed context drops.
            unsafe {
                ffi::sqlite3_trace_v2(self.connection.handle(), 0, None, std::ptr::null_mut());
            }
        }
    }

    fn collect_sql(
        store: &Store,
        operation: impl FnOnce() -> Result<ReaderViewMetadata>,
    ) -> Result<(ReaderViewMetadata, SqlStatistics)> {
        let trace = SqlTrace::new(store.test_connection())?;
        let metadata = operation()?;
        let statistics = trace.statistics();
        drop(trace);
        Ok((metadata, statistics))
    }

    #[test]
    fn non_reader_routes_do_not_scale_with_unrelated_library_or_history() -> Result<()> {
        for book_count in [8, 2_048] {
            let fixture = fixture(book_count)?;
            for route in ["settings", "sources", "catalog", "search"] {
                let trace = SqlTrace::new(fixture.store.test_connection())?;
                let context = load_non_reader_context(&fixture.store, route, true, false)?;
                let statistics = trace.statistics();
                drop(trace);
                assert!(context.library_counts.is_none());
                assert_eq!(context.has_source_snapshot, route != "settings");
                assert_eq!(context.daily_update_count, 0);
                assert_eq!(context.weekly_read_count, 0);
                assert_eq!(statistics.read_statements, usize::from(route != "settings"));
                assert_eq!(statistics.write_statements, 0);
                assert_eq!(statistics.full_scan_steps, 0);
                assert!(statistics.by_sql.keys().all(|sql| !sql.contains("books")
                    && !sql.contains("chapters")
                    && !sql.contains("progress")));
            }
        }
        Ok(())
    }

    #[test]
    fn visible_chapter_download_states_do_not_read_unrelated_queue_documents() -> Result<()> {
        for unrelated in [0, 4_096] {
            let store = Store::open(":memory:")?;
            let book = Book {
                id: "download-view-book".into(),
                source_id: "local".into(),
                remote_id: "download-view-book".into(),
                title: "Download view fixture".into(),
                in_library: true,
                category: "reading".into(),
                chapter_count: (unrelated + 6) as u32,
                ..Book::default()
            };
            let transaction = store.test_connection().unchecked_transaction()?;
            transaction.execute(
                "INSERT INTO books (id,source_id,remote_id,title,in_library,category,updated_at,document)
                 VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
                params![book.id, book.source_id, book.remote_id, book.title, book.in_library,
                    book.category, book.updated_at, serde_json::to_string(&book)?],
            )?;
            for index in 0..unrelated + 6 {
                let chapter = Chapter {
                    id: format!("download-view-{index:05}"),
                    book_id: book.id.clone(),
                    source_id: "local".into(),
                    remote_id: index.to_string(),
                    number: index as f64,
                    downloaded: index == 3,
                    ..Chapter::default()
                };
                transaction.execute(
                    "INSERT INTO chapters (id,book_id,source_id,remote_id,number,document)
                     VALUES (?1,?2,?3,?4,?5,?6)",
                    params![
                        chapter.id,
                        chapter.book_id,
                        chapter.source_id,
                        chapter.remote_id,
                        chapter.number,
                        serde_json::to_string(&chapter)?
                    ],
                )?;
                if index == 5 {
                    continue;
                }
                let (state, completed, total) = match index {
                    0 => ("downloading", 3, 12),
                    1 => ("queued", 0, 0),
                    2 => ("paused", 4, 16),
                    3 => ("completed", 2, 2),
                    4 => ("failed", 1, 5),
                    _ => ("queued", 0, 0),
                };
                let job = DownloadJob {
                    chapter_id: chapter.id.clone(),
                    book_id: book.id.clone(),
                    state: state.into(),
                    completed,
                    total,
                    ..DownloadJob::default()
                };
                let document = if index < 6 {
                    serde_json::to_string(&job)?
                } else {
                    "{}".into()
                };
                transaction.execute(
                    "INSERT INTO downloads (chapter_id,book_id,state,document) VALUES (?1,?2,?3,?4)",
                    params![chapter.id, book.id, state, document],
                )?;
            }
            transaction.commit()?;
            let visible =
                store.detail_chapters_view_page(&book.id, false, unrelated + 6, 1, false)?;
            let trace = SqlTrace::new(store.test_connection())?;
            let projections = visible
                .items
                .iter()
                .map(|chapter| load_chapter_download_projection(&store, chapter))
                .collect::<Result<Vec<_>>>()?;
            let statistics = trace.statistics();
            drop(trace);
            assert_eq!(statistics.read_statements, 6);
            assert_eq!(statistics.write_statements, 0);
            assert_eq!(statistics.full_scan_steps, 0);
            assert_eq!(
                projections,
                vec![
                    ChapterDownloadProjection {
                        state: "downloading".into(),
                        progress: 0.25
                    },
                    ChapterDownloadProjection {
                        state: "queued".into(),
                        progress: 0.0
                    },
                    ChapterDownloadProjection {
                        state: "paused".into(),
                        progress: 0.25
                    },
                    ChapterDownloadProjection {
                        state: "completed".into(),
                        progress: 1.0
                    },
                    ChapterDownloadProjection {
                        state: "failed".into(),
                        progress: 0.2
                    },
                    ChapterDownloadProjection {
                        state: "available".into(),
                        progress: 0.0
                    },
                ]
            );
            if unrelated > 0 {
                assert!(store.downloads().is_err());
            }
        }
        Ok(())
    }

    // Reproduce the metadata reads performed by sync_view and view_book for
    // route="reader" in the archived baseline source identified above. Slint
    // setters, layout, image work, and platform status I/O are excluded from both
    // measurements, so these numbers are not end-to-end UI or input latency.
    fn legacy_reader_metadata(store: &Store, reader: &ReaderState) -> Result<ReaderViewMetadata> {
        let mut sources = store.sources()?;
        sources.sort_by(|left, right| {
            source_order(left)
                .cmp(&source_order(right))
                .then_with(|| left.name.cmp(&right.name))
        });
        let all_books = store.books()?;
        let history = store.history()?;
        let library: Vec<_> = all_books
            .iter()
            .filter(|book| book.in_library)
            .cloned()
            .collect();
        let today = view_today(false);
        let week_start = today - chrono::Duration::days(6);
        let weekly_chapters: HashSet<_> = history
            .iter()
            .filter(|progress| {
                let date = local_instant(progress.updated_at, false).date_naive();
                date >= week_start && date <= today
            })
            .map(|progress| progress.chapter_id.as_str())
            .collect();
        let mut daily_updates = Vec::new();
        for book in &library {
            for chapter in store
                .chapters(&book.id)?
                .into_iter()
                .filter(|chapter| !chapter.read && chapter_day(&chapter.date) == Some(today))
            {
                daily_updates.push((book.clone(), chapter));
            }
        }
        daily_updates.sort_by(|(left_book, left_chapter), (right_book, right_chapter)| {
            right_chapter
                .date
                .cmp(&left_chapter.date)
                .then_with(|| right_book.updated_at.cmp(&left_book.updated_at))
                .then_with(|| left_book.id.cmp(&right_book.id))
                .then_with(|| right_chapter.number.total_cmp(&left_chapter.number))
        });
        let mut recent = HashMap::new();
        for progress in &history {
            recent
                .entry(progress.book_id.clone())
                .or_insert_with(|| progress.clone());
        }
        let chapter = reader.current_chapter();
        let book = all_books
            .iter()
            .find(|book| book.id == chapter.book_id)
            .cloned();
        let progress = recent.get(&chapter.book_id).cloned();
        let stored = store.book(&chapter.book_id)?;
        let card_progress = store.progress(&chapter.book_id)?;
        let card_chapters = store.chapters(&chapter.book_id)?;
        let chapters = store.chapters(&chapter.book_id)?;
        let bookmarked = store.has_bookmark(&chapter.id, reader.page)?;
        let next_downloaded = chapters
            .iter()
            .filter(|entry| entry.number > chapter.number)
            .min_by(|left, right| left.number.total_cmp(&right.number))
            .is_some_and(|entry| entry.downloaded);
        black_box((
            sources,
            library,
            weekly_chapters,
            daily_updates,
            stored,
            card_progress,
            card_chapters,
        ));
        Ok(ReaderViewMetadata {
            book,
            progress,
            bookmarked,
            next_downloaded,
            projection: reader_view_projection(reader, None).0,
        })
    }

    #[test]
    fn reader_metadata_uses_current_chapter_and_committed_image_for_bookmarks() -> Result<()> {
        let mut fixture = fixture(2)?;
        fixture.reader.pending_page = Some(CURRENT_PAGE + 1);
        let unrelated = Progress {
            chapter_id: "a-different-chapter".into(),
            page: 27,
            ..fixture.committed.clone()
        };
        let (metadata, statistics) = collect_sql(&fixture.store, || {
            load_reader_metadata(
                &fixture.store,
                &fixture.reader,
                Some(&fixture.displayed),
                Some(&unrelated),
            )
        })?;
        assert_eq!(metadata.progress.as_ref(), Some(&fixture.committed));
        assert!(metadata.bookmarked);
        assert!(metadata.next_downloaded);
        assert_eq!(statistics.read_statements, 4);
        assert_eq!(statistics.write_statements, 0);
        assert_eq!(statistics.full_scan_steps, 0);
        fixture.store.toggle_bookmark(&Bookmark {
            book_id: fixture.committed.book_id.clone(),
            chapter_id: fixture.committed.chapter_id.clone(),
            page: CURRENT_PAGE,
            created_at: fixture.committed.updated_at,
        })?;
        fixture.store.toggle_bookmark(&Bookmark {
            book_id: fixture.committed.book_id.clone(),
            chapter_id: fixture.committed.chapter_id.clone(),
            page: CURRENT_PAGE + 1,
            created_at: fixture.committed.updated_at,
        })?;
        assert!(
            !load_reader_metadata(
                &fixture.store,
                &fixture.reader,
                Some(&fixture.displayed),
                None,
            )?
            .bookmarked
        );
        Ok(())
    }

    #[test]
    fn reader_metadata_observes_download_changes_without_reloading_session() -> Result<()> {
        let fixture = fixture(2)?;
        let next_id = format!("reader-book-00000-chapter-{:02}", CURRENT_CHAPTER + 1);
        assert!(
            !fixture
                .reader
                .chapters()
                .iter()
                .find(|chapter| chapter.id == next_id)
                .unwrap()
                .downloaded
        );
        let (metadata, statistics) = collect_sql(&fixture.store, || {
            load_reader_metadata(
                &fixture.store,
                &fixture.reader,
                Some(&fixture.displayed),
                Some(&fixture.committed),
            )
        })?;
        assert!(metadata.next_downloaded);
        assert_eq!(statistics.read_statements, 3);
        assert_eq!(statistics.write_statements, 0);
        assert_eq!(statistics.full_scan_steps, 0);
        fixture.store.set_chapter_downloaded(&next_id, false)?;
        assert!(
            !load_reader_metadata(
                &fixture.store,
                &fixture.reader,
                Some(&fixture.displayed),
                Some(&fixture.committed),
            )?
            .next_downloaded
        );
        Ok(())
    }

    #[test]
    fn reader_metadata_follows_displayed_frame_while_another_page_or_chapter_loads() -> Result<()> {
        let mut fixture = fixture(2)?;
        fixture.displayed.page = 7;
        fixture.displayed.total = 27;
        fixture.committed.page = 7;
        fixture.committed.total = 27;
        fixture.store.save_progress(&fixture.committed)?;
        fixture.store.toggle_bookmark(&Bookmark {
            book_id: fixture.displayed.book_id.clone(),
            chapter_id: fixture.displayed.chapter_id.clone(),
            page: 7,
            created_at: fixture.committed.updated_at,
        })?;
        fixture.reader.jump_to_page(8);
        let (metadata, statistics) = collect_sql(&fixture.store, || {
            load_reader_metadata(
                &fixture.store,
                &fixture.reader,
                Some(&fixture.displayed),
                Some(&fixture.committed),
            )
        })?;
        assert_eq!(fixture.reader.page, 8);
        assert_eq!(metadata.projection.book_id, fixture.displayed.book_id);
        assert_eq!(metadata.projection.chapter_id, fixture.displayed.chapter_id);
        assert_eq!(metadata.projection.chapter_number, "4");
        assert_eq!(metadata.projection.chapter_title, "Chapter 4");
        assert_eq!(metadata.projection.image_page, 7);
        assert_eq!(metadata.projection.label_page, 7);
        assert_eq!(metadata.projection.total, 27);
        assert!(metadata.bookmarked);
        assert!(metadata.next_downloaded);
        assert_eq!(statistics.read_statements, 3);
        assert_eq!(statistics.write_statements, 0);
        assert_eq!(statistics.full_scan_steps, 0);

        fixture.reader.preview_page(9);
        let metadata = load_reader_metadata(
            &fixture.store,
            &fixture.reader,
            Some(&fixture.displayed),
            Some(&fixture.committed),
        )?;
        assert_eq!(metadata.projection.image_page, 7);
        assert_eq!(metadata.projection.label_page, 9);
        assert!(metadata.bookmarked);

        fixture.reader.next_chapter();
        fixture.reader.preview_page(9);
        let requested_progress = fixture.reader.progress(fixture.committed.updated_at);
        let (metadata, statistics) = collect_sql(&fixture.store, || {
            load_reader_metadata(
                &fixture.store,
                &fixture.reader,
                Some(&fixture.displayed),
                Some(&requested_progress),
            )
        })?;
        assert_ne!(
            fixture.reader.current_chapter().id,
            fixture.displayed.chapter_id
        );
        assert_eq!(metadata.projection.chapter_id, fixture.displayed.chapter_id);
        assert_eq!(metadata.projection.chapter_number, "4");
        assert_eq!(metadata.projection.chapter_title, "Chapter 4");
        assert_eq!(metadata.projection.image_page, 7);
        assert_eq!(metadata.projection.label_page, 7);
        assert_eq!(metadata.projection.total, 27);
        assert_eq!(metadata.progress.as_ref(), Some(&fixture.committed));
        assert!(metadata.bookmarked);
        assert!(metadata.next_downloaded);
        assert_eq!(statistics.read_statements, 4);
        assert_eq!(statistics.write_statements, 0);
        assert_eq!(statistics.full_scan_steps, 0);

        let metadata = load_reader_metadata(&fixture.store, &fixture.reader, None, None)?;
        assert_eq!(
            metadata.projection.chapter_id,
            fixture.reader.current_chapter().id
        );
        assert_eq!(metadata.projection.image_page, fixture.reader.page);
        assert_eq!(metadata.projection.label_page, 9);
        assert_eq!(metadata.projection.total, fixture.reader.total);
        Ok(())
    }

    #[test]
    fn reader_metadata_uses_the_actual_successor_when_chapter_numbers_match() -> Result<()> {
        let fixture = fixture(2)?;
        let mut duplicate = fixture.reader.current_chapter().clone();
        duplicate.id.push_str("-duplicate");
        duplicate.remote_id = "duplicate-number".into();
        duplicate.title = "Duplicate-number successor".into();
        fixture
            .store
            .save_chapters(&duplicate.book_id, std::slice::from_ref(&duplicate))?;
        let mut reader = ReaderState::new(
            fixture.store.chapters(&fixture.displayed.book_id)?,
            &fixture.displayed.chapter_id,
            fixture.displayed.page,
            &Preferences::default(),
        )?;
        let (metadata, statistics) = collect_sql(&fixture.store, || {
            load_reader_metadata(
                &fixture.store,
                &reader,
                Some(&fixture.displayed),
                Some(&fixture.committed),
            )
        })?;
        assert!(!metadata.next_downloaded);
        assert_eq!(statistics.read_statements, 3);
        assert_eq!(statistics.write_statements, 0);
        assert_eq!(statistics.full_scan_steps, 0);
        reader.next_chapter();
        assert_eq!(reader.current_chapter().id, duplicate.id);
        Ok(())
    }

    fn menu_sample(
        fixture: &ReaderFixture,
        reader: &mut ReaderState,
        strategy: usize,
    ) -> Result<(u64, ReaderViewMetadata)> {
        let start = Instant::now();
        black_box(reader.toggle_menu());
        let metadata = match strategy {
            0 => legacy_reader_metadata(&fixture.store, reader)?,
            1 => load_reader_metadata(
                &fixture.store,
                reader,
                Some(&fixture.displayed),
                Some(&fixture.committed),
            )?,
            _ => load_reader_metadata(&fixture.store, reader, Some(&fixture.displayed), None)?,
        };
        let elapsed = start.elapsed().as_nanos() as u64;
        Ok((elapsed, black_box(metadata)))
    }

    fn latency(samples: &[u64]) -> Value {
        let mut sorted = samples.to_vec();
        sorted.sort_unstable();
        let quantile = |percent: usize| {
            sorted[(sorted.len() * percent).div_ceil(100).saturating_sub(1)] as f64 / 1_000.0
        };
        json!({
            "samples": sorted.len(),
            "p50_us": quantile(50),
            "p95_us": quantile(95),
            "min_us": sorted[0] as f64 / 1_000.0,
            "max_us": sorted[sorted.len() - 1] as f64 / 1_000.0,
            "raw_ns": samples,
        })
    }

    #[test]
    #[ignore = "Run only on the designated remote worker with --release --ignored --nocapture"]
    fn reader_metadata_scaling_benchmark() -> Result<()> {
        anyhow::ensure!(
            !cfg!(debug_assertions),
            "Use --release for comparable metadata timings"
        );
        let sample_count = 41;
        let mut scales = Vec::new();
        for book_count in [1_000, 5_000] {
            let fixture = fixture(book_count)?;
            let (baseline, baseline_sql) = collect_sql(&fixture.store, || {
                legacy_reader_metadata(&fixture.store, &fixture.reader)
            })?;
            let (warm, warm_sql) = collect_sql(&fixture.store, || {
                load_reader_metadata(
                    &fixture.store,
                    &fixture.reader,
                    Some(&fixture.displayed),
                    Some(&fixture.committed),
                )
            })?;
            let (cold, cold_sql) = collect_sql(&fixture.store, || {
                load_reader_metadata(
                    &fixture.store,
                    &fixture.reader,
                    Some(&fixture.displayed),
                    None,
                )
            })?;
            assert_eq!(warm, baseline);
            assert_eq!(cold, baseline);
            assert_eq!(baseline_sql.read_statements, book_count + 8);
            assert_eq!(warm_sql.read_statements, 3);
            assert_eq!(cold_sql.read_statements, 4);
            assert_eq!(warm_sql.full_scan_steps, 0);
            assert_eq!(cold_sql.full_scan_steps, 0);
            assert_eq!(warm_sql.write_statements, 0);
            assert_eq!(cold_sql.write_statements, 0);
            let mut readers = [
                fixture.reader.clone(),
                fixture.reader.clone(),
                fixture.reader.clone(),
            ];
            for _ in 0..3 {
                for (strategy, reader) in readers.iter_mut().enumerate() {
                    let (_, result) = menu_sample(&fixture, reader, strategy)?;
                    assert_eq!(result, baseline);
                }
            }
            let mut samples = [Vec::new(), Vec::new(), Vec::new()];
            for round in 0..sample_count {
                for offset in 0..3 {
                    // Rotate measurement order; each strategy executes the same
                    // menu action against the same already-cached database.
                    let strategy = (round + offset) % 3;
                    let (elapsed, result) =
                        menu_sample(&fixture, &mut readers[strategy], strategy)?;
                    assert_eq!(result, baseline);
                    samples[strategy].push(elapsed);
                }
            }
            scales.push(json!({
                "books": book_count,
                "chapters": book_count * CHAPTERS_PER_BOOK,
                "history_rows": book_count * CURRENT_CHAPTER,
                "legacy": { "sql": baseline_sql, "latency": latency(&samples[0]) },
                "committed_progress_hit": { "sql": warm_sql, "latency": latency(&samples[1]) },
                "committed_progress_miss": { "sql": cold_sql, "latency": latency(&samples[2]) },
                "results_equal": true,
            }));
        }
        let report = json!({
            "schema_version": 1,
            "measurement": "Reader menu metadata collection; excludes Slint setters, layout, image work, and platform status; not end-to-end UI latency",
            "baseline_view_sha256": BASELINE_VIEW_SHA256,
            "current_view_sha256": format!("{:x}", Sha256::digest(include_bytes!("view.rs"))),
            "build_profile": "release",
            "sql_tracing_during_timing": false,
            "fixture_seed_during_timing": false,
            "cached_database": true,
            "chapters_per_book": CHAPTERS_PER_BOOK,
            "samples_per_strategy": sample_count,
            "scales": scales,
        });
        if let Some(path) = std::env::var_os("AIDOKU_READER_BENCH_OUTPUT") {
            let path = PathBuf::from(path);
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(path, serde_json::to_vec_pretty(&report)?)?;
        }
        println!(
            "READER_METADATA_BENCHMARK={}",
            serde_json::to_string(&report)?
        );
        Ok(())
    }
}
