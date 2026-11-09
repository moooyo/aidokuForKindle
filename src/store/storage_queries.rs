//! Bounded keyset readers for background storage accounting.

use std::path::PathBuf;

use anyhow::{Result, ensure};
use rusqlite::params;

use super::Store;

const MAX_STORAGE_PAGE: usize = 128;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StorageBookHeader {
    pub id: String,
    pub title: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StorageChapterHeader {
    pub id: String,
    pub downloaded: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoragePathReference {
    pub position: i64,
    pub path: Option<PathBuf>,
}

impl Store {
    /// Enumerate headers rather than retaining decoded books or every path/book
    /// association. The primary-key cursor visits each book at most once.
    pub fn storage_books_page(
        &self,
        after: Option<&str>,
        limit: usize,
    ) -> Result<Vec<StorageBookHeader>> {
        ensure!(
            limit <= MAX_STORAGE_PAGE,
            "The storage book window exceeds 128 rows"
        );
        let mut statement = self
            .connection
            .prepare("SELECT id,title FROM books WHERE id>?1 ORDER BY id LIMIT ?2")?;
        Ok(statement
            .query_map(params![after.unwrap_or(""), limit as i64], |row| {
                Ok(StorageBookHeader {
                    id: row.get(0)?,
                    title: row.get(1)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Include non-downloaded headers so a long run of such chapters cannot
    /// turn one bounded request into a scan of the whole book. No manifest or
    /// filesystem work runs while this method's statement holds a read lock.
    pub fn storage_chapters_page(
        &self,
        book_id: &str,
        after: Option<&str>,
        limit: usize,
    ) -> Result<Vec<StorageChapterHeader>> {
        ensure!(
            limit <= MAX_STORAGE_PAGE,
            "The storage chapter window exceeds 128 rows"
        );
        let mut statement = self.connection.prepare(
            "SELECT id,COALESCE(json_extract(document,'$.downloaded'),0)=1
             FROM chapters INDEXED BY chapters_book_identity
             WHERE book_id=?1 AND id>?2 ORDER BY id LIMIT ?3",
        )?;
        Ok(statement
            .query_map(params![book_id, after.unwrap_or(""), limit as i64], |row| {
                Ok(StorageChapterHeader {
                    id: row.get(0)?,
                    downloaded: row.get(1)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Limit manifest positions, including unmapped pages, instead of filtering
    /// first. Returned rows own their paths and release the SQLite statement
    /// before the caller resolves a path or reads its metadata.
    pub fn storage_manifest_page(
        &self,
        chapter_id: &str,
        after: Option<i64>,
        limit: usize,
    ) -> Result<Vec<StoragePathReference>> {
        ensure!(
            limit <= MAX_STORAGE_PAGE,
            "The storage manifest window exceeds 128 rows"
        );
        let mut statement = self.connection.prepare(
            "SELECT position,json_extract(document,'$.local_path') FROM pages
             WHERE chapter_id=?1 AND position>?2 ORDER BY position LIMIT ?3",
        )?;
        Ok(statement
            .query_map(
                params![chapter_id, after.unwrap_or(-1), limit as i64],
                |row| {
                    Ok(StoragePathReference {
                        position: row.get(0)?,
                        path: row.get::<_, Option<String>>(1)?.map(PathBuf::from),
                    })
                },
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{Book, Chapter, Page};

    fn add_book(store: &Store, id: &str) {
        store
            .upsert_book(&Book {
                id: id.into(),
                source_id: "source".into(),
                remote_id: id.into(),
                title: format!("Book {id}"),
                ..Book::default()
            })
            .unwrap();
    }

    #[test]
    fn windows_are_bounded_and_keep_unmapped_positions_and_undownloaded_headers() {
        let store = Store::open(":memory:").unwrap();
        add_book(&store, "b");
        add_book(&store, "a");
        let books = store.storage_books_page(None, 1).unwrap();
        assert_eq!(books[0].id, "a");
        assert_eq!(store.storage_books_page(Some("a"), 1).unwrap()[0].id, "b");
        let chapters: Vec<_> = (0..260)
            .map(|index| Chapter {
                id: format!("chapter-{index:04}"),
                source_id: "source".into(),
                remote_id: format!("chapter-{index:04}"),
                book_id: "a".into(),
                ..Chapter::default()
            })
            .collect();
        store.save_chapters("a", &chapters).unwrap();
        let first = store.storage_chapters_page("a", None, 128).unwrap();
        assert_eq!(first.len(), 128);
        assert!(first.iter().all(|chapter| !chapter.downloaded));
        let second = store
            .storage_chapters_page("a", Some(&first.last().unwrap().id), 128)
            .unwrap();
        assert_eq!(second.len(), 128);
        assert_eq!(second[0].id, "chapter-0128");
        let pages: Vec<_> = (0..260)
            .map(|index| Page {
                url: format!("https://example.test/{index}.png"),
                local_path: (index == 259).then(|| PathBuf::from("last.png")),
                ..Page::default()
            })
            .collect();
        store.save_pages(&chapters[0].id, &pages).unwrap();
        let first = store
            .storage_manifest_page(&chapters[0].id, None, 128)
            .unwrap();
        assert_eq!(first.len(), 128);
        assert!(first.iter().all(|page| page.path.is_none()));
        let second = store
            .storage_manifest_page(&chapters[0].id, Some(first.last().unwrap().position), 128)
            .unwrap();
        assert_eq!(second.len(), 128);
        let tail = store
            .storage_manifest_page(&chapters[0].id, Some(second.last().unwrap().position), 128)
            .unwrap();
        assert_eq!(tail.len(), 4);
        assert_eq!(tail.last().unwrap().path, Some(PathBuf::from("last.png")));
        assert!(store.storage_books_page(None, 129).is_err());
        assert!(store.storage_chapters_page("a", None, 129).is_err());
        assert!(
            store
                .storage_manifest_page(&chapters[0].id, None, 129)
                .is_err()
        );
    }

    #[test]
    fn returning_a_batch_releases_its_source_read_lock() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("library.sqlite3");
        let writer = Store::open(&database).unwrap();
        add_book(&writer, "book");
        let reader = Store::open_read_only(&database).unwrap();
        assert_eq!(reader.storage_books_page(None, 128).unwrap().len(), 1);
        writer.set_library("book", true).unwrap();
        assert!(writer.book("book").unwrap().unwrap().in_library);
    }
}
