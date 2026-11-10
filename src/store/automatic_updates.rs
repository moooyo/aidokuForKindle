//! Atomic publication of background library refreshes without reviving removed books.

use super::*;

impl Store {
    /// Recheck cancellation after SQLite acquires its write lock, including
    /// when a busy timeout allowed a lifecycle transition to happen meanwhile.
    pub(crate) fn write_automatic_update_checkpoint(
        &self,
        key: &str,
        value: &serde_json::Value,
        current_generation: impl Fn() -> bool,
    ) -> Result<()> {
        ensure!(
            current_generation(),
            "The automatic checkpoint was cancelled"
        );
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        ensure!(
            current_generation(),
            "The automatic checkpoint was cancelled"
        );
        self.write_ui_settings(key, value)?;
        ensure!(
            current_generation(),
            "The automatic checkpoint was cancelled"
        );
        transaction.commit()?;
        Ok(())
    }

    /// The caller performs provider work outside this transaction. Revalidate
    /// both persisted ownership and the cooperative generation before commit.
    pub(crate) fn publish_automatic_library_update(
        &self,
        requested: &Book,
        details: &Book,
        chapters: &[Chapter],
        current_generation: impl Fn() -> bool,
    ) -> Result<bool> {
        ensure!(
            same_book_identity(requested, details),
            "The automatic update changed its book identity"
        );
        ensure!(
            current_generation(),
            "The automatic library update was cancelled"
        );
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        ensure!(
            current_generation(),
            "The automatic library update was cancelled"
        );
        let previous = query_document::<Book, _>(
            &transaction,
            "SELECT document FROM books WHERE id=?1",
            [&requested.id],
        )?;
        let Some(previous) =
            previous.filter(|book| book.in_library && same_book_identity(book, requested))
        else {
            return Ok(false);
        };

        let mut book = details.clone();
        book.in_library = previous.in_library;
        book.category = previous.category;
        book.chapter_count = book.chapter_count.max(previous.chapter_count);
        if book.description.is_empty() {
            book.description = previous.description;
        }
        if book.author.is_empty() {
            book.author = previous.author;
        }
        if book.cover.is_empty() {
            book.cover = previous.cover;
        }
        if book.tags.is_empty() {
            book.tags = previous.tags;
        }
        for incoming in chapters {
            ensure!(
                current_generation(),
                "The automatic library update was cancelled"
            );
            ensure!(
                incoming.book_id == book.id && incoming.source_id == book.source_id,
                "The automatic chapter update belongs to another book or source"
            );
            ensure!(
                !incoming.id.is_empty()
                    && !incoming.remote_id.is_empty()
                    && incoming.number.is_finite(),
                "The automatic chapter identity or number is invalid"
            );
            let mut chapter = incoming.clone();
            if let Some(previous) = query_document::<Chapter, _>(
                &transaction,
                "SELECT document FROM chapters WHERE id=?1",
                [&chapter.id],
            )? {
                ensure!(
                    previous.book_id == chapter.book_id
                        && previous.source_id == chapter.source_id
                        && previous.remote_id == chapter.remote_id,
                    "The automatic chapter identity changed"
                );
                // Refreshing metadata cannot undo a local read/unread decision,
                // remove offline ownership, or reduce a known manifest size.
                chapter.read = previous.read;
                chapter.downloaded = previous.downloaded;
                chapter.page_count = chapter.page_count.max(previous.page_count);
            } else {
                chapter.downloaded = false;
            }
            write_chapter(&transaction, &chapter)?;
        }
        let (count, unread): (u32, u32) = transaction.query_row(
            "SELECT count(*),coalesce(sum(CASE WHEN json_extract(document,'$.read')=0 THEN 1 ELSE 0 END),0) FROM chapters WHERE book_id=?1",
            [&book.id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        book.chapter_count = book.chapter_count.max(count);
        book.unread = unread;
        write_book(&transaction, &book)?;
        ensure!(
            current_generation(),
            "The automatic library update was cancelled"
        );
        transaction.commit()?;
        Ok(true)
    }
}

fn same_book_identity(first: &Book, second: &Book) -> bool {
    first.id == second.id
        && first.source_id == second.source_id
        && first.remote_id == second.remote_id
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;

    use super::*;

    fn fixture() -> (Store, Book, Chapter) {
        let store = Store::open(":memory:").unwrap();
        let book = Book {
            id: "book".into(),
            source_id: "source".into(),
            remote_id: "remote-book".into(),
            title: "Local title".into(),
            category: "reading".into(),
            in_library: true,
            ..Book::default()
        };
        let chapter = Chapter {
            id: "chapter".into(),
            book_id: book.id.clone(),
            source_id: book.source_id.clone(),
            remote_id: "remote-chapter".into(),
            page_count: 12,
            ..Chapter::default()
        };
        store.upsert_book(&book).unwrap();
        store
            .save_chapters(&book.id, std::slice::from_ref(&chapter))
            .unwrap();
        (store, book, chapter)
    }

    #[test]
    fn a_refresh_cannot_reinsert_a_deleted_or_removed_library_book() {
        let (store, requested, chapter) = fixture();
        let mut details = requested.clone();
        details.title = "Remote title".into();
        store.set_library(&requested.id, false).unwrap();
        assert!(
            !store
                .publish_automatic_library_update(
                    &requested,
                    &details,
                    std::slice::from_ref(&chapter),
                    || true
                )
                .unwrap()
        );
        let removed = store.book(&requested.id).unwrap().unwrap();
        assert!(!removed.in_library);
        assert_eq!(removed.title, requested.title);
        store.remove_book(&requested.id).unwrap();
        assert!(
            !store
                .publish_automatic_library_update(&requested, &details, &[chapter], || true)
                .unwrap()
        );
        assert!(store.book(&requested.id).unwrap().is_none());
    }

    #[test]
    fn local_category_read_state_progress_and_preferences_survive_remote_metadata() {
        let (store, requested, chapter) = fixture();
        store.set_category(&requested.id, "").unwrap();
        let progress = Progress {
            book_id: requested.id.clone(),
            chapter_id: chapter.id.clone(),
            page: 4,
            total: 12,
            updated_at: 123,
            completed: false,
        };
        store.save_progress(&progress).unwrap();
        let preferences = serde_json::json!({"book_directions":{"book":"ltr"}});
        store
            .write_ui_settings("interaction-models", &preferences)
            .unwrap();
        let mut details = requested.clone();
        details.title = "Remote title".into();
        details.category = "completed".into();
        details.in_library = false;
        let mut incoming = chapter.clone();
        incoming.read = true;
        incoming.downloaded = true;
        incoming.page_count = 1;
        assert!(
            store
                .publish_automatic_library_update(&requested, &details, &[incoming], || true)
                .unwrap()
        );
        let actual = store.book(&requested.id).unwrap().unwrap();
        assert!(actual.in_library);
        assert_eq!(actual.category, "");
        assert_eq!(actual.title, "Remote title");
        let retained = store.chapter(&chapter.id).unwrap().unwrap();
        assert!(!retained.read);
        assert!(!retained.downloaded);
        assert_eq!(retained.page_count, 12);
        assert_eq!(store.chapter_progress(&chapter.id).unwrap(), Some(progress));
        assert_eq!(
            store.read_ui_settings("interaction-models").unwrap(),
            Some(preferences)
        );
    }

    #[test]
    fn invalid_chapters_roll_back_the_book_and_every_preceding_chapter_write() {
        let (store, requested, chapter) = fixture();
        let original = store.book(&requested.id).unwrap().unwrap();
        let mut details = requested.clone();
        details.title = "Must roll back".into();
        let mut first = chapter.clone();
        first.title = "Must also roll back".into();
        let mut foreign = chapter.clone();
        foreign.id = "another-chapter".into();
        foreign.book_id = "another-book".into();
        assert!(
            store
                .publish_automatic_library_update(&requested, &details, &[first, foreign], || true)
                .is_err()
        );
        assert_eq!(store.book(&requested.id).unwrap(), Some(original));
        assert_eq!(store.chapter(&chapter.id).unwrap(), Some(chapter));
    }

    #[test]
    fn a_generation_change_inside_the_transaction_rolls_back_before_commit() {
        let (store, requested, chapter) = fixture();
        let original = store.book(&requested.id).unwrap().unwrap();
        let calls = Cell::new(0);
        let mut details = requested.clone();
        details.title = "Cancelled update".into();
        let mut incoming = chapter.clone();
        incoming.title = "Cancelled chapter".into();
        assert!(
            store
                .publish_automatic_library_update(&requested, &details, &[incoming], || {
                    let call = calls.get() + 1;
                    calls.set(call);
                    call < 4
                })
                .is_err()
        );
        assert_eq!(calls.get(), 4);
        assert_eq!(store.book(&requested.id).unwrap(), Some(original));
        assert_eq!(store.chapter(&chapter.id).unwrap(), Some(chapter));
    }

    #[test]
    fn cancellation_rolls_back_a_checkpoint_before_commit() {
        let (store, _, _) = fixture();
        let original = serde_json::json!({"last_success":100});
        store
            .write_ui_settings("automatic-updates", &original)
            .unwrap();
        let calls = Cell::new(0);
        assert!(
            store
                .write_automatic_update_checkpoint(
                    "automatic-updates",
                    &serde_json::json!({"last_success":200}),
                    || {
                        let call = calls.get() + 1;
                        calls.set(call);
                        call < 3
                    },
                )
                .is_err()
        );
        assert_eq!(calls.get(), 3);
        assert_eq!(
            store.read_ui_settings("automatic-updates").unwrap(),
            Some(original)
        );
    }
}
