//! Bounded library views for a source, including disabled or removed sources.

use anyhow::{Result, ensure};
use rusqlite::{Connection, Transaction, TransactionBehavior, params};

use super::{Store, decode, view_queries::ViewPage};
use crate::domain::Book;

impl Store {
    /// Source configuration does not determine ownership of existing library
    /// entries. Read the total and visible window from the same snapshot.
    pub(crate) fn source_library_books_page(
        &self,
        source_id: &str,
        page: usize,
        limit: usize,
    ) -> Result<ViewPage<Book>> {
        ensure!(
            (1..=64).contains(&limit),
            "The source library page size must be between 1 and 64"
        );
        if !self.connection.is_autocommit() {
            return read_source_library(&self.connection, source_id, page, limit);
        }
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Deferred)?;
        let result = read_source_library(&transaction, source_id, page, limit)?;
        transaction.commit()?;
        Ok(result)
    }
}

fn read_source_library(
    connection: &Connection,
    source_id: &str,
    page: usize,
    limit: usize,
) -> Result<ViewPage<Book>> {
    let total = connection.query_row(
        "SELECT COUNT(*) FROM books WHERE source_id=?1 AND in_library=1",
        [source_id],
        |row| row.get::<_, usize>(0),
    )?;
    let page = page.clamp(1, total.div_ceil(limit).max(1));
    let offset = (page - 1) * limit;
    let mut statement = connection.prepare(
        "SELECT document FROM books WHERE source_id=?1 AND in_library=1
         ORDER BY title COLLATE BINARY,id LIMIT ?2 OFFSET ?3",
    )?;
    let items = statement
        .query_map(params![source_id, limit as u64, offset as u64], |row| {
            row.get::<_, String>(0)
        })?
        .map(|row| decode(row?))
        .collect::<Result<Vec<Book>>>()?;
    Ok(ViewPage { total, page, items })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::SourceConfig;

    fn book(id: &str, source: &str, title: &str, in_library: bool) -> Book {
        Book {
            id: id.into(),
            source_id: source.into(),
            remote_id: id.into(),
            title: title.into(),
            in_library,
            ..Book::default()
        }
    }

    fn populate(store: &Store) {
        store
            .save_source(&SourceConfig {
                id: "disabled".into(),
                name: "Disabled source".into(),
                kind: "komga".into(),
                enabled: false,
                ..SourceConfig::default()
            })
            .unwrap();
        for value in [
            book("a-2", "disabled", "Alpha", true),
            book("a-1", "disabled", "Alpha", true),
            book("z", "disabled", "Zulu", true),
            book("lower", "disabled", "alpha", true),
            book("not-in-library", "disabled", "AAA", false),
            book("other", "another", "Aardvark", true),
        ] {
            store.upsert_book(&value).unwrap();
        }
    }

    #[test]
    fn source_scope_disabled_configuration_and_page_bounds_use_real_library_rows() {
        let store = Store::open(":memory:").unwrap();
        populate(&store);
        assert!(!store.source("disabled").unwrap().unwrap().enabled);
        let before = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get::<_, u64>(0))
            .unwrap();
        let first = store.source_library_books_page("disabled", 0, 2).unwrap();
        assert_eq!((first.total, first.page), (4, 1));
        assert_eq!(
            first
                .items
                .iter()
                .map(|value| value.id.as_str())
                .collect::<Vec<_>>(),
            ["a-1", "a-2"]
        );
        let last = store
            .source_library_books_page("disabled", usize::MAX, 2)
            .unwrap();
        assert_eq!((last.total, last.page), (4, 2));
        assert_eq!(
            last.items
                .iter()
                .map(|value| value.title.as_str())
                .collect::<Vec<_>>(),
            ["Zulu", "alpha"]
        );
        let empty = store
            .source_library_books_page("unknown-source", 99, 64)
            .unwrap();
        assert_eq!((empty.total, empty.page, empty.items.len()), (0, 1, 0));
        assert!(store.source_library_books_page("disabled", 1, 0).is_err());
        assert!(store.source_library_books_page("disabled", 1, 65).is_err());
        let after = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get::<_, u64>(0))
            .unwrap();
        assert_eq!(after, before);
    }

    #[test]
    fn bad_documents_outside_the_selected_window_are_never_decoded() {
        let store = Store::open(":memory:").unwrap();
        populate(&store);
        // The JSON is valid for expression indexes but is not a Book record.
        store
            .connection
            .execute(
                "UPDATE books SET document='\"not a Book\"' WHERE id IN ('z','other')",
                [],
            )
            .unwrap();
        let first = store.source_library_books_page("disabled", 1, 2).unwrap();
        assert_eq!((first.total, first.items.len()), (4, 2));
        assert_eq!(first.items[0].id, "a-1");
        assert!(store.source_library_books_page("disabled", 2, 2).is_err());
        assert!(
            store
                .source_library_books_page("unknown-source", 1, 64)
                .unwrap()
                .items
                .is_empty()
        );
    }

    #[test]
    fn read_only_connections_and_existing_transactions_support_the_indexed_page() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("source-library.sqlite3");
        let store = Store::open(&path).unwrap();
        populate(&store);
        let observer = Store::open_read_only(&path).unwrap();
        assert_eq!(
            observer
                .source_library_books_page("disabled", 1, 2)
                .unwrap()
                .total,
            4
        );
        let plans=observer.connection.prepare(
            "EXPLAIN QUERY PLAN SELECT document FROM books WHERE source_id=?1 AND in_library=1 ORDER BY title COLLATE BINARY,id LIMIT ?2 OFFSET ?3",
        ).unwrap().query_map(params!["disabled",2,0],|row|row.get::<_,String>(3)).unwrap().collect::<rusqlite::Result<Vec<_>>>().unwrap();
        assert!(
            plans
                .iter()
                .any(|plan| plan.contains("books_source_library"))
        );
        assert!(!plans.iter().any(|plan| plan.contains("USE TEMP B-TREE")));
        let transaction =
            Transaction::new_unchecked(&store.connection, TransactionBehavior::Immediate).unwrap();
        super::super::write_book(&transaction, &book("new", "disabled", "Before", true)).unwrap();
        assert_eq!(
            store
                .source_library_books_page("disabled", 1, 64)
                .unwrap()
                .total,
            5
        );
        transaction.rollback().unwrap();
        assert_eq!(
            store
                .source_library_books_page("disabled", 1, 64)
                .unwrap()
                .total,
            4
        );
        assert_eq!(
            observer
                .source_library_books_page("disabled", usize::MAX, 64)
                .unwrap()
                .page,
            1
        );
    }
}
