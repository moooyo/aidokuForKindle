use super::*;

#[derive(Default)]
pub(super) struct SearchNavigation {
    from_catalog: Option<SearchOrigin>,
    showing_all: Option<SearchResultsReturn>,
    draft: Option<SearchDraft>,
}

struct SearchOrigin {
    catalog_page: usize,
    query: String,
    scope: String,
    results: Vec<SearchResult>,
}

struct SearchResultsReturn {
    search_page: usize,
    source: String,
    books: Vec<Book>,
    remote_page: u32,
    has_more: bool,
    query: Option<String>,
    listings: Vec<(String, String)>,
    listing_filter: i32,
}

struct SearchDraft {
    scope: String,
    page: usize,
}

impl App {
    pub(super) fn open_catalog_search(&mut self) -> Result<()> {
        let Some(source) = self.store.source(&self.current_source)? else {
            return Ok(());
        };
        if !self.searchable_source(&source) {
            return Ok(());
        }
        self.invalidate_search_work();
        for result in &mut self.search_results {
            if result.pending {
                result.pending = false;
                result.error = Some(tr("search_cancelled"));
            }
        }
        self.search_navigation.from_catalog = Some(SearchOrigin {
            catalog_page: self.page,
            query: std::mem::take(&mut self.query),
            scope: std::mem::replace(&mut self.search_scope, self.current_source.clone()),
            results: std::mem::take(&mut self.search_results),
        });
        self.route = "search".into();
        self.page = 1;
        let query = self.catalog_query.clone().unwrap_or_default();
        self.open_keyboard("search", &query)
    }

    pub(super) fn return_to_catalog_from_search(&mut self) -> bool {
        let Some(origin) = self.search_navigation.from_catalog.take() else {
            return false;
        };
        self.invalidate_search_work();
        self.query = origin.query;
        self.search_scope = origin.scope;
        self.search_results = origin.results;
        self.route = "catalog".into();
        self.page = origin.catalog_page;
        true
    }

    pub(super) fn open_all_search_results(&mut self, id: &str) {
        let Some(group) = self.search_results.iter().find(|group| group.id == id) else {
            return;
        };
        let books = group.books.clone();
        let has_more = group.has_next;
        self.invalidate_catalog_work();
        if self.search_navigation.showing_all.is_none() {
            self.search_navigation.showing_all = Some(SearchResultsReturn {
                search_page: self.page,
                source: self.current_source.clone(),
                books: std::mem::take(&mut self.catalog),
                remote_page: self.catalog_remote_page,
                has_more: self.catalog_has_more,
                query: self.catalog_query.take(),
                listings: std::mem::take(&mut self.catalog_listings),
                listing_filter: self.catalog_filter,
            });
        }
        self.catalog = books;
        self.current_source = id.into();
        self.catalog_remote_page = 1;
        self.catalog_has_more = has_more;
        self.catalog_query = Some(self.query.clone());
        self.catalog_filter = 0;
        self.route = "catalog".into();
        self.page = 1;
    }

    pub(super) fn showing_all_search_results(&self) -> bool {
        self.search_navigation.showing_all.is_some()
    }

    pub(super) fn search_result_context_active(&self) -> bool {
        self.route == "search"
            || self.route == "detail" && self.return_route == "search"
            || self.showing_all_search_results()
    }

    pub(super) fn return_to_search_results(&mut self) -> bool {
        let Some(previous) = self.search_navigation.showing_all.take() else {
            return false;
        };
        self.invalidate_catalog_work();
        self.current_source = previous.source;
        self.catalog = previous.books;
        self.catalog_remote_page = previous.remote_page;
        self.catalog_has_more = previous.has_more;
        self.catalog_query = previous.query;
        self.catalog_listings = previous.listings;
        self.catalog_filter = previous.listing_filter;
        self.route = "search".into();
        self.page = previous.search_page;
        true
    }

    pub(super) fn begin_search_draft(&mut self) {
        self.search_navigation.draft = Some(SearchDraft {
            scope: self.search_scope.clone(),
            page: self.page,
        });
    }

    pub(super) fn finish_search_draft(&mut self, submit: bool) {
        if let Some(draft) = self.search_navigation.draft.take() {
            if submit {
                self.search_scope = draft.scope;
            } else {
                self.page = draft.page;
            }
        }
    }

    pub(super) fn visible_search_scope(&self) -> &str {
        self.search_navigation
            .draft
            .as_ref()
            .map_or(&self.search_scope, |draft| &draft.scope)
    }

    pub(super) fn searchable_source(&self, source: &SourceConfig) -> bool {
        use super::server_configuration::{is_server, source_requires_address};
        source.enabled
            && (matches!(
                source.kind.to_ascii_lowercase().as_str(),
                "local" | "mangadex" | "wasm"
            ) || is_server(source) && !source_requires_address(source))
    }

    pub(crate) fn search_scope_page_size(&self) -> usize {
        if self.platform.profile().is_scribe() {
            8
        } else {
            6
        }
    }

    pub(crate) fn open_search_scope_picker(&mut self) -> Result<()> {
        if self.route != "search" {
            return Ok(());
        }
        let Some(ui) = self.ui.upgrade() else {
            return Ok(());
        };
        let sources = ui.global::<State>().get_search_sources();
        let scope = self.visible_search_scope();
        let selected = |id: &str| {
            if scope == id || (id == "all" && scope.is_empty()) {
                "✓".to_owned()
            } else {
                String::new()
            }
        };
        let mut rows = vec![
            ("all".into(), tr("all_sources"), selected("all")),
            ("library".into(), tr("library"), selected("library")),
        ];
        rows.extend(
            (0..sources.row_count())
                .filter_map(|index| sources.row_data(index))
                .filter(|source| source.available && source.installed)
                .map(|source| {
                    (
                        source.id.to_string(),
                        source.name.to_string(),
                        selected(source.id.as_str()),
                    )
                }),
        );
        let context = if self.modal_context == "search-scope:typing"
            || (self.modal == "keyboard" && self.keyboard_target == "search")
        {
            "search-scope:typing"
        } else {
            "search-scope:results"
        };
        // The existing keyboard remains intact while the choices overlay is open.
        self.show_choices(&tr("search_scope_title"), context, rows)
    }

    pub(crate) fn choose_search_scope(&mut self, scope_id: &str) -> Result<()> {
        let scope_id = if scope_id.is_empty() { "all" } else { scope_id };
        let eligible = matches!(scope_id, "all" | "library")
            || self
                .store
                .source(scope_id)?
                .is_some_and(|source| self.searchable_source(&source));
        if !eligible {
            return Ok(());
        }
        self.close_search_scope_picker();
        if let Some(draft) = &mut self.search_navigation.draft {
            draft.scope = scope_id.into();
        } else if self.search_scope != scope_id {
            self.search_scope = scope_id.into();
            self.run_search()?;
        }
        Ok(())
    }

    pub(crate) fn close_search_scope_picker(&mut self) {
        if !self.modal_context.starts_with("search-scope:") {
            return;
        }
        let typing = self.modal_context == "search-scope:typing";
        self.modal = if typing {
            "keyboard".into()
        } else {
            String::new()
        };
        self.modal_context.clear();
        self.choice_rows.clear();
        self.choice_total = 0;
        self.choice_disabled.clear();
        self.panel_page = 0;
        if typing {
            if let Some(ui) = self.ui.upgrade() {
                ui.global::<State>().set_modal_title(tr("search").into());
            }
        }
        self.sync_choice_rows();
    }
}
