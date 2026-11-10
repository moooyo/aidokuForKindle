//! Persistent categories with atomic updates of indexed and serialized book data.

use std::{error::Error, fmt};

use anyhow::Result;
use rusqlite::{Connection, Transaction, TransactionBehavior, params};

use super::Store;

const MAX_CATEGORY_CHARS: usize = 64;
const MAX_CATEGORY_BYTES: usize = 256;
const BUILTIN_IDS: [&str; 3] = ["reading", "planned", "completed"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Category {
    pub id: String,
    pub name: String,
    pub builtin: bool,
    pub book_count: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CategoryError {
    InvalidName,
    DuplicateName,
    MissingCategory,
}

impl fmt::Display for CategoryError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidName => "A category name must contain 1 to 64 visible characters",
            Self::DuplicateName => "A category with this name already exists",
            Self::MissingCategory => "The category no longer exists",
        })
    }
}

impl Error for CategoryError {}

pub(super) fn ensure_categories(connection: &Connection) -> Result<()> {
    let transaction = Transaction::new_unchecked(connection, TransactionBehavior::Immediate)?;
    transaction.execute_batch(
        "CREATE TABLE IF NOT EXISTS categories (
             id TEXT PRIMARY KEY,
             name TEXT NOT NULL,
             builtin INTEGER NOT NULL DEFAULT 0 CHECK(builtin IN (0,1))
         );
         INSERT OR IGNORE INTO categories(id,name,builtin) VALUES
             ('reading','reading',1),('planned','planned',1),('completed','completed',1);
         INSERT OR IGNORE INTO categories(id,name,builtin)
             SELECT DISTINCT category,category,0 FROM books
             WHERE category<>'' AND category NOT IN ('reading','planned','completed');",
    )?;
    transaction.commit()?;
    Ok(())
}

impl Store {
    /// Read only the three semantic labels without counting library membership.
    pub fn builtin_category_names(&self) -> Result<Vec<(String, String)>> {
        let mut statement = self
            .connection
            .prepare("SELECT id,name FROM categories WHERE builtin=1 ORDER BY id")?;
        Ok(statement
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Include legacy category references written after startup without changing
    /// the database from a read-only projection.
    pub fn categories(&self) -> Result<Vec<Category>> {
        read_categories(&self.connection)
    }

    pub fn uncategorized_book_count(&self) -> Result<u64> {
        Ok(self.connection.query_row(
            "SELECT count(*) FROM books WHERE in_library=1 AND category=''",
            [],
            |row| row.get(0),
        )?)
    }

    /// Empty categories are durable records, independent of book membership.
    pub fn create_category(&self, name: &str) -> Result<Category> {
        let name = normalized_name(name)?;
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let categories = read_categories(&transaction)?;
        ensure_available(&categories, None, &name)?;
        transaction.execute(
            "INSERT INTO categories(id,name,builtin) VALUES(?1,?1,0)",
            [&name],
        )?;
        transaction.commit()?;
        Ok(Category {
            id: name.clone(),
            name,
            builtin: false,
            book_count: 0,
        })
    }

    /// Built-in IDs are semantic filter keys and must remain stable. Custom
    /// IDs retain the legacy name-as-ID format, including in serialized books.
    pub fn rename_category(&self, id: &str, name: &str) -> Result<Category> {
        let name = normalized_name(name)?;
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let categories = read_categories(&transaction)?;
        let category = categories
            .iter()
            .find(|category| category.id == id)
            .ok_or(CategoryError::MissingCategory)?;
        ensure_available(&categories, Some(id), &name)?;
        let new_id = if category.builtin { id } else { &name };
        if category.name == name && category.id == new_id {
            let unchanged = category.clone();
            transaction.commit()?;
            return Ok(unchanged);
        }
        transaction.execute(
            "INSERT INTO categories(id,name,builtin) VALUES(?1,?2,?3)
             ON CONFLICT(id) DO UPDATE SET name=excluded.name,builtin=excluded.builtin",
            params![new_id, name, category.builtin],
        )?;
        if new_id != id {
            transaction.execute(
                "UPDATE books SET category=?2,document=json_set(document,'$.category',?2)
                 WHERE category=?1",
                params![id, new_id],
            )?;
            transaction.execute("DELETE FROM categories WHERE id=?1", [id])?;
        }
        let renamed = Category {
            id: new_id.to_owned(),
            name,
            builtin: category.builtin,
            book_count: category.book_count,
        };
        transaction.commit()?;
        Ok(renamed)
    }
}

fn normalized_name(name: &str) -> Result<String> {
    let name = name.trim();
    if name.is_empty()
        || name.chars().count() > MAX_CATEGORY_CHARS
        || name.len() > MAX_CATEGORY_BYTES
        || name.chars().any(char::is_control)
        || name.chars().all(char::is_whitespace)
    {
        return Err(CategoryError::InvalidName.into());
    }
    Ok(name.to_owned())
}

fn ensure_available(categories: &[Category], except: Option<&str>, name: &str) -> Result<()> {
    let key = name.to_lowercase();
    if categories.iter().any(|category| {
        Some(category.id.as_str()) != except
            && (category.name.to_lowercase() == key || category.id.to_lowercase() == key)
    }) {
        return Err(CategoryError::DuplicateName.into());
    }
    Ok(())
}

fn read_categories(connection: &Connection) -> Result<Vec<Category>> {
    let mut statement = connection.prepare(
        "WITH known(id,name,builtin) AS (
             SELECT id,name,builtin FROM categories
             UNION ALL
             SELECT DISTINCT b.category,b.category,0 FROM books AS b
             WHERE b.category<>'' AND NOT EXISTS(
                 SELECT 1 FROM categories AS c WHERE c.id=b.category)
         )
         SELECT k.id,k.name,k.builtin,
             (SELECT count(*) FROM books AS b WHERE b.in_library=1 AND b.category=k.id)
         FROM known AS k
         ORDER BY CASE k.id WHEN 'reading' THEN 0 WHEN 'planned' THEN 1
             WHEN 'completed' THEN 2 ELSE 3 END,k.name COLLATE NOCASE,k.id",
    )?;
    Ok(statement
        .query_map([], |row| {
            let id: String = row.get(0)?;
            Ok(Category {
                builtin: BUILTIN_IDS.contains(&id.as_str()),
                id,
                name: row.get(1)?,
                book_count: row.get(3)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::Book;

    fn book(id: &str, category: &str, in_library: bool) -> Book {
        Book {
            id: id.into(),
            source_id: "source".into(),
            remote_id: id.into(),
            title: format!("Book {id}"),
            category: category.into(),
            in_library,
            unread: 3,
            ..Book::default()
        }
    }

    #[test]
    fn empty_categories_and_renamed_builtin_labels_survive_reopening() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("library.sqlite3");
        {
            let store = Store::open(&path).unwrap();
            let empty = store.create_category("  Favorites  ").unwrap();
            assert_eq!(empty.id, "Favorites");
            assert_eq!(empty.book_count, 0);
            store
                .rename_category("reading", "Currently reading")
                .unwrap();
        }
        let store = Store::open(&path).unwrap();
        let categories = store.categories().unwrap();
        assert_eq!(categories.len(), 4);
        assert_eq!(categories[0].id, "reading");
        assert_eq!(categories[0].name, "Currently reading");
        assert!(categories[0].builtin);
        assert_eq!(categories[3].name, "Favorites");
        assert_eq!(categories[3].book_count, 0);
    }

    #[test]
    fn custom_rename_updates_all_indexed_and_serialized_references_atomically() {
        let store = Store::open(":memory:").unwrap();
        store.create_category("Favorites").unwrap();
        for value in [
            book("one", "Favorites", true),
            book("two", "Favorites", true),
            book("catalog", "Favorites", false),
            book("unclassified", "", true),
            book("other", "reading", true),
        ] {
            store.upsert_book(&value).unwrap();
        }
        let renamed = store.rename_category("Favorites", "Archive").unwrap();
        assert_eq!(renamed.book_count, 2);
        assert_eq!(renamed.id, "Archive");
        for id in ["one", "two", "catalog"] {
            let saved = store.book(id).unwrap().unwrap();
            assert_eq!(saved.category, "Archive");
            assert_eq!(saved.unread, 3);
            let indexed: String = store
                .connection
                .query_row("SELECT category FROM books WHERE id=?1", [id], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(indexed, saved.category);
        }
        assert_eq!(store.uncategorized_book_count().unwrap(), 1);
        let categories = store.categories().unwrap();
        assert!(!categories.iter().any(|category| category.id == "Favorites"));
        assert_eq!(
            categories
                .iter()
                .find(|category| category.id == "Archive")
                .unwrap()
                .book_count,
            2
        );
        assert_eq!(store.book("other").unwrap().unwrap().category, "reading");
    }

    #[test]
    fn builtin_rename_keeps_filter_semantics_and_custom_names_are_not_hidden() {
        let store = Store::open(":memory:").unwrap();
        store.upsert_book(&book("one", "reading", true)).unwrap();
        store
            .upsert_book(&book("legacy", "Imported category", true))
            .unwrap();
        let renamed = store.rename_category("reading", "In progress").unwrap();
        assert_eq!(renamed.id, "reading");
        assert_eq!(store.library_counts().unwrap().reading, 1);
        assert_eq!(store.book("one").unwrap().unwrap().category, "reading");
        assert!(
            store
                .categories()
                .unwrap()
                .iter()
                .any(|category| { category.id == "Imported category" && category.book_count == 1 })
        );
        let imported = store
            .rename_category("Imported category", "Renamed import")
            .unwrap();
        assert_eq!(imported.id, "Renamed import");
        assert_eq!(
            store.book("legacy").unwrap().unwrap().category,
            "Renamed import"
        );
    }

    #[test]
    fn invalid_or_duplicate_names_do_not_change_membership() {
        let store = Store::open(":memory:").unwrap();
        store.create_category("Ärchive").unwrap();
        store.upsert_book(&book("one", "Ärchive", true)).unwrap();
        for name in ["", "   ", "bad\nname", &"x".repeat(65)] {
            assert_eq!(
                store
                    .create_category(name)
                    .unwrap_err()
                    .downcast::<CategoryError>()
                    .unwrap(),
                CategoryError::InvalidName
            );
        }
        for name in ["ärchive", "READING", " Ärchive "] {
            assert_eq!(
                store
                    .create_category(name)
                    .unwrap_err()
                    .downcast::<CategoryError>()
                    .unwrap(),
                CategoryError::DuplicateName
            );
        }
        assert!(store.rename_category("Ärchive", "planned").is_err());
        assert!(store.rename_category("missing", "New category").is_err());
        assert_eq!(store.book("one").unwrap().unwrap().category, "Ärchive");
        assert_eq!(store.categories().unwrap().len(), 4);
    }

    #[test]
    fn failed_membership_update_rolls_back_category_and_all_references() {
        let store = Store::open(":memory:").unwrap();
        store.create_category("Original").unwrap();
        let originals = [book("one", "Original", true), book("two", "Original", true)];
        for value in &originals {
            store.upsert_book(value).unwrap();
        }
        // FAIL retains earlier row updates within this SQL statement. The
        // enclosing transaction must roll those updates and the new catalog
        // record back when a later row fails, regardless of SQLite's row order.
        store
            .connection
            .execute_batch(
                "CREATE TRIGGER reject_later_category_update
                 BEFORE UPDATE OF category ON books
                 WHEN NEW.category='Replacement'
                     AND EXISTS(SELECT 1 FROM books WHERE category='Replacement')
                 BEGIN
                     SELECT RAISE(FAIL,'Injected category update failure');
                 END;",
            )
            .unwrap();
        let error = store
            .rename_category("Original", "Replacement")
            .unwrap_err();
        assert!(format!("{error:#}").contains("Injected category update failure"));
        let categories = store.categories().unwrap();
        assert!(
            categories
                .iter()
                .any(|category| category.id == "Original" && category.book_count == 2)
        );
        assert!(
            !categories
                .iter()
                .any(|category| category.id == "Replacement")
        );
        for original in &originals {
            assert_eq!(store.book(&original.id).unwrap().as_ref(), Some(original));
            let indexed: String = store
                .connection
                .query_row(
                    "SELECT category FROM books WHERE id=?1",
                    [&original.id],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(indexed, "Original");
        }
    }

    #[test]
    fn legacy_membership_is_imported_without_losing_empty_categories() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("library.sqlite3");
        {
            let store = Store::open(&path).unwrap();
            store.create_category("Empty").unwrap();
            store.upsert_book(&book("legacy", "Legacy", true)).unwrap();
        }
        let store = Store::open(&path).unwrap();
        let categories = store.categories().unwrap();
        assert!(
            categories
                .iter()
                .any(|category| category.id == "Empty" && category.book_count == 0)
        );
        assert!(
            categories
                .iter()
                .any(|category| category.id == "Legacy" && category.book_count == 1)
        );
        assert!(store.create_category("legacy").is_err());
    }
}
