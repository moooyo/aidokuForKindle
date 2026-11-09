use super::*;

impl App {
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
        let selected = |id: &str| {
            if self.search_scope == id || (id == "all" && self.search_scope.is_empty()) {
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
            || self.ui.upgrade().is_some_and(|ui| {
                let sources = ui.global::<State>().get_search_sources();
                (0..sources.row_count())
                    .filter_map(|index| sources.row_data(index))
                    .any(|source| {
                        source.available && source.installed && source.id.as_str() == scope_id
                    })
            });
        if !eligible {
            return Ok(());
        }
        let typing = self.modal_context == "search-scope:typing"
            || (self.modal == "keyboard" && self.keyboard_target == "search");
        let changed = self.search_scope != scope_id;
        if changed {
            self.search_scope = scope_id.into();
            self.page = 1;
        }
        self.close_search_scope_picker();
        if changed && !typing {
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
