//! Source-scoped library selection before the existing single-book migration flow.

use super::*;

const SOURCE_MIGRATION_PREFIX: &str = "source-migration:";
const EMPTY_CHOICE_ID: &str = "source-migration-empty";

impl App {
    pub(crate) fn open_source_migration(&mut self, source_id: &str) -> Result<()> {
        self.load_source_migration_page(source_id, 1)
    }

    pub(crate) fn source_migration_page_size(&self) -> usize {
        if self.platform.profile().is_scribe() {
            8
        } else {
            5
        }
    }

    pub(crate) fn turn_source_migration_page(&mut self, delta: i32) -> Result<()> {
        anyhow::ensure!(
            self.modal == "choices",
            "The source migration selector is not open"
        );
        let source_id = self
            .modal_context
            .strip_prefix(SOURCE_MIGRATION_PREFIX)
            .filter(|id| !id.is_empty())
            .context("The source migration scope is missing")?
            .to_owned();
        let count = self
            .choice_total
            .div_ceil(self.source_migration_page_size())
            .max(1);
        let page = (self.panel_page as i64 + i64::from(delta))
            .clamp(0, count.saturating_sub(1) as i64) as usize;
        self.load_source_migration_page(&source_id, page + 1)
    }

    pub(crate) fn choose_source_migration_book(&mut self, book_id: &str) -> Result<()> {
        anyhow::ensure!(
            self.modal == "choices",
            "The source migration selector is not open"
        );
        let source_id = self
            .modal_context
            .strip_prefix(SOURCE_MIGRATION_PREFIX)
            .filter(|id| !id.is_empty())
            .context("The source migration scope is missing")?
            .to_owned();
        anyhow::ensure!(
            self.store.source(&source_id)?.is_some(),
            "The source selected for migration is no longer installed"
        );
        let book = self
            .store
            .book(book_id)?
            .context("The manga selected for migration is missing")?;
        anyhow::ensure!(
            book.in_library && book.source_id == source_id,
            "The selected manga is not in this source's library"
        );

        // Opening details normally refreshes the origin remotely and records an open.
        // This transition uses the validated stored book and leaves migration to do
        // its existing target-only search and guarded commit.
        self.invalidate_reader_work();
        self.clear_reader_recovery();
        self.reader = None;
        self.displayed_reader = None;
        self.displayed_image_leases.clear();
        self.committed_progress = None;
        self.committed_progress_dirty = false;
        self.resume_reader_repair = false;
        self.resume_frame_pending = false;
        self.selection_mode = false;
        self.selected.clear();
        self.selected_chapters.clear();
        self.cover_view.clear();
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_page_jump_open(false);
            state.set_reader_image(Image::default());
            state.set_reader_image_next(Image::default());
            state.set_reader_image_ready(false);
        }
        self.current_book_id = Some(book.id);
        self.current_source = source_id;
        self.return_route = "catalog".into();
        self.route = "detail".into();
        self.page = 1;
        self.open_migration()
    }

    fn load_source_migration_page(&mut self, source_id: &str, page: usize) -> Result<()> {
        let source = self
            .store
            .source(source_id)?
            .context("The source selected for migration is not installed")?;
        let books = self.store.source_library_books_page(
            source_id,
            page,
            self.source_migration_page_size(),
        )?;
        let rows = if books.total == 0 {
            vec![(
                EMPTY_CHOICE_ID.into(),
                tr("source_migration_empty"),
                String::new(),
            )]
        } else {
            books
                .items
                .into_iter()
                .map(|book| (book.id, book.title, book.author))
                .collect()
        };
        let source_name = if source.name.trim().is_empty() {
            source_id
        } else {
            source.name.as_str()
        };
        self.show_choices(
            &format!("{} · {source_name}", tr("source_migration_title")),
            &format!("{SOURCE_MIGRATION_PREFIX}{source_id}"),
            rows,
        )?;
        self.choice_total = books.total;
        self.panel_page = books.page.saturating_sub(1);
        if books.total == 0 {
            self.choice_disabled.insert(EMPTY_CHOICE_ID.into());
        }
        if let Some(ui) = self.ui.upgrade() {
            ui.global::<State>().set_page_jump_open(false);
        }
        self.sync_choice_rows();
        Ok(())
    }
}
