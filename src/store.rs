use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::Path,
    time::Duration,
};

use anyhow::{Context, Result, ensure};
use rusqlite::{
    Connection, OpenFlags, OptionalExtension, Transaction, TransactionBehavior, params,
};
use serde::{Serialize, de::DeserializeOwned};

use crate::domain::{
    Book, Bookmark, Chapter, DownloadJob, NavigationSession, Page, Preferences, Progress,
    SourceConfig,
};

const MAX_DOCUMENT_BYTES: usize = 8 * 1024 * 1024;

#[path = "store/automatic_updates.rs"]
mod automatic_updates;
#[path = "store/categories.rs"]
pub mod categories;
#[path = "store/download_management.rs"]
pub mod download_management;
#[path = "store/migration.rs"]
pub mod migration;
#[path = "store/page_queries.rs"]
mod page_queries;
#[path = "store/source_management.rs"]
pub mod source_management;
#[path = "store/storage_queries.rs"]
pub mod storage_queries;
#[path = "store/view_queries.rs"]
pub mod view_queries;
pub use page_queries::{CachedPathsUpdateReport, PageWindow};

#[derive(Debug, Clone, Default)]
pub struct DownloadBatchReport {
    pub jobs: Vec<DownloadJob>,
    pub downloaded_metadata_changed: Vec<String>,
}

pub struct Store {
    connection: Connection,
}

impl Store {
    #[cfg(test)]
    pub(crate) fn test_connection(&self) -> &rusqlite::Connection {
        &self.connection
    }

    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        if path != Path::new(":memory:") {
            if let Some(parent) = path
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
            {
                if !parent.exists() {
                    fs::create_dir_all(parent).context("Create application data directory")?;
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
                    }
                }
            }
        }
        let connection = Connection::open(path).context("Open application database")?;
        connection.busy_timeout(Duration::from_secs(5))?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.pragma_update(None, "recursive_triggers", "ON")?;
        // The Kindle userstore is fuse.fsp. Avoid WAL's shared-memory index
        // and the WAL-reset race in the bundled SQLite 3.50.2. Short rollback
        // transactions also keep the UI and download connections compatible.
        connection.pragma_update(None, "mmap_size", 0)?;
        connection.pragma_update(None, "journal_mode", "DELETE")?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        connection.execute_batch(
            "BEGIN IMMEDIATE;
             CREATE TABLE IF NOT EXISTS books (
                 id TEXT PRIMARY KEY,
                 source_id TEXT NOT NULL,
                 remote_id TEXT NOT NULL,
                 title TEXT NOT NULL,
                 in_library INTEGER NOT NULL,
                 category TEXT NOT NULL,
                 updated_at INTEGER NOT NULL,
                 document TEXT NOT NULL,
                 UNIQUE(source_id, remote_id)
             );
             CREATE TABLE IF NOT EXISTS chapters (
                 id TEXT PRIMARY KEY,
                 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                 source_id TEXT NOT NULL,
                 remote_id TEXT NOT NULL,
                 number REAL NOT NULL,
                 document TEXT NOT NULL,
                 UNIQUE(book_id, remote_id)
             );
             CREATE INDEX IF NOT EXISTS chapters_book ON chapters(book_id, number DESC);
             CREATE INDEX IF NOT EXISTS chapters_book_identity ON chapters(book_id, id);
             CREATE TABLE IF NOT EXISTS pages (
                 chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                 position INTEGER NOT NULL,
                 document TEXT NOT NULL,
                 PRIMARY KEY(chapter_id, position)
             );
             CREATE TABLE IF NOT EXISTS progress (
                 chapter_id TEXT PRIMARY KEY REFERENCES chapters(id) ON DELETE CASCADE,
                 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                 page INTEGER NOT NULL,
                 total INTEGER NOT NULL,
                 completed INTEGER NOT NULL,
                 updated_at INTEGER NOT NULL
             );
             CREATE INDEX IF NOT EXISTS progress_recent ON progress(updated_at DESC);
             CREATE INDEX IF NOT EXISTS progress_book_recent ON progress(book_id, updated_at DESC);
             CREATE TABLE IF NOT EXISTS bookmarks (
                 chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                 page INTEGER NOT NULL,
                 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                 created_at INTEGER NOT NULL,
                 PRIMARY KEY(chapter_id, page)
             );
             CREATE TABLE IF NOT EXISTS preferences (
                 key TEXT PRIMARY KEY,
                 document TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS sources (
                 id TEXT PRIMARY KEY,
                 document TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS downloads (
                 chapter_id TEXT PRIMARY KEY REFERENCES chapters(id) ON DELETE CASCADE,
                 book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                 state TEXT NOT NULL,
                 document TEXT NOT NULL
             );
             CREATE INDEX IF NOT EXISTS downloads_state ON downloads(state);
             CREATE INDEX IF NOT EXISTS downloads_book ON downloads(book_id);
             CREATE TABLE IF NOT EXISTS download_sleep_ownership (
                 chapter_id TEXT PRIMARY KEY REFERENCES downloads(chapter_id) ON DELETE CASCADE
             );
             CREATE INDEX IF NOT EXISTS books_library ON books(in_library, category, json_extract(document, '$.unread'));
             CREATE INDEX IF NOT EXISTS books_source_library ON books(source_id, in_library, title COLLATE BINARY, id);
             CREATE TABLE IF NOT EXISTS book_ui_metadata (
                 book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
                 added_at INTEGER NOT NULL DEFAULT 0 CHECK(added_at>=0),
                 opened_at INTEGER NOT NULL DEFAULT 0 CHECK(opened_at>=0)
             );
             CREATE INDEX IF NOT EXISTS book_ui_added ON book_ui_metadata(added_at DESC,book_id);
             CREATE INDEX IF NOT EXISTS book_ui_opened ON book_ui_metadata(opened_at DESC,book_id);
             CREATE INDEX IF NOT EXISTS chapters_downloaded_book ON chapters(book_id) WHERE json_extract(document, '$.downloaded') = 1;
             CREATE INDEX IF NOT EXISTS chapters_unread_book ON chapters(book_id) WHERE json_extract(document, '$.read') = 0;
             CREATE INDEX IF NOT EXISTS chapters_unread_date ON chapters(substr(json_extract(document, '$.date'),1,10),book_id) WHERE json_extract(document, '$.read') = 0;
             CREATE INDEX IF NOT EXISTS pages_local_path ON pages(json_extract(document, '$.local_path')) WHERE json_extract(document, '$.local_path') IS NOT NULL;
             PRAGMA user_version = 1;
             COMMIT;",
        )?;
        page_queries::ensure_page_counters(&connection)?;
        categories::ensure_categories(&connection)?;
        #[cfg(unix)]
        if path != Path::new(":memory:") {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
        }
        let store = Self { connection };
        store.recover_downloads()?;
        Ok(store)
    }

    /// Background observers must not run schema migration or startup recovery.
    pub fn open_read_only(path: impl AsRef<Path>) -> Result<Self> {
        let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)
            .context("Open application database read-only")?;
        connection.busy_timeout(Duration::from_secs(5))?;
        connection.pragma_update(None, "query_only", "ON")?;
        connection.pragma_update(None, "mmap_size", 0)?;
        Ok(Self { connection })
    }

    /// Connect only after the application's primary Store has initialized its
    /// schema. Background mutations must never recover another live worker.
    pub fn open_existing_for_write(path: impl AsRef<Path>) -> Result<Self> {
        let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_WRITE)
            .context("Open initialized application database")?;
        connection.busy_timeout(Duration::from_secs(5))?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.pragma_update(None, "recursive_triggers", "ON")?;
        connection.pragma_update(None, "mmap_size", 0)?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        let journal: String =
            connection.pragma_query_value(None, "journal_mode", |row| row.get(0))?;
        ensure!(
            journal == "delete",
            "The background database requires an initialized DELETE journal"
        );
        let ready: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_schema WHERE type='table' AND name='book_ui_metadata')
             AND EXISTS(SELECT 1 FROM sqlite_schema WHERE type='table' AND name='chapter_page_stats')
             AND (SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND tbl_name='pages' AND name IN(
                 'chapter_page_stats_insert_v1','chapter_page_stats_delete_v1','chapter_page_stats_update_v1'))=3",
            [], |row| row.get(0),
        )?;
        ensure!(
            ready,
            "The application database schema has not been initialized"
        );
        Ok(Self { connection })
    }

    /// Keyset pages are unique physical path references, not per-job byte sums.
    /// Drop each returned page's SQLite statement before doing filesystem work.
    pub fn completed_download_paths_page(
        &self,
        after: Option<&str>,
        limit: usize,
    ) -> Result<Vec<std::path::PathBuf>> {
        ensure!(
            limit <= 1_024,
            "The download storage page size exceeds its limit"
        );
        let mut statement = self.connection.prepare(
            "SELECT DISTINCT json_extract(p.document, '$.local_path') AS local_path
             FROM pages AS p INDEXED BY pages_local_path JOIN chapters AS c ON c.id=p.chapter_id
             WHERE json_extract(p.document, '$.local_path') IS NOT NULL
               AND json_extract(p.document, '$.local_path')>?1
               AND json_extract(c.document, '$.downloaded')=1
             ORDER BY local_path LIMIT ?2",
        )?;
        let paths = statement
            .query_map(params![after.unwrap_or(""), limit as i64], |row| {
                row.get::<_, String>(0)
            })?
            .map(|row| row.map(std::path::PathBuf::from))
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(paths)
    }

    pub fn completed_download_chapter_ids(&self) -> Result<Vec<String>> {
        let mut statement = self.connection.prepare(
            "SELECT id FROM chapters WHERE json_extract(document, '$.downloaded')=1 ORDER BY id",
        )?;
        Ok(statement
            .query_map([], |row| row.get::<_, String>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn completed_read_download_ids(&self, book_id: Option<&str>) -> Result<Vec<String>> {
        let mut statement = self.connection.prepare(
            "SELECT d.chapter_id FROM downloads AS d JOIN chapters AS c ON c.id=d.chapter_id
             WHERE d.state='completed' AND json_extract(c.document,'$.read')=1 AND (?1 IS NULL OR d.book_id=?1)
             ORDER BY d.rowid",
        )?;
        Ok(statement
            .query_map([book_id], |row| row.get::<_, String>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?)
    }

    pub fn upsert_book(&self, book: &Book) -> Result<()> {
        ensure!(
            !book.id.is_empty() && !book.source_id.is_empty() && !book.remote_id.is_empty(),
            "Book identity is missing"
        );
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut book = book.clone();
        if let Some(previous) = self.book(&book.id)? {
            ensure!(
                previous.source_id == book.source_id && previous.remote_id == book.remote_id,
                "Book identity belongs to another source"
            );
            book.in_library |= previous.in_library;
            book.unread = previous.unread;
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
            if !previous.category.is_empty() {
                book.category = previous.category;
            }
        }
        write_book(&transaction, &book)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn books(&self) -> Result<Vec<Book>> {
        query_documents(
            &self.connection,
            "SELECT document FROM books ORDER BY updated_at DESC, title COLLATE NOCASE, id",
            [],
        )
    }

    /// Include cached catalog entries without decoding books owned by other sources.
    pub fn books_for_source(&self, source_id: &str) -> Result<Vec<Book>> {
        query_documents(
            &self.connection,
            "SELECT document FROM books WHERE source_id = ?1
             ORDER BY updated_at DESC, title COLLATE NOCASE, id",
            [source_id],
        )
    }

    pub fn book(&self, id: &str) -> Result<Option<Book>> {
        query_document(
            &self.connection,
            "SELECT document FROM books WHERE id = ?1",
            [id],
        )
    }

    pub fn remove_book(&self, id: &str) -> Result<()> {
        self.connection
            .execute("DELETE FROM books WHERE id = ?1", [id])?;
        Ok(())
    }

    pub fn set_library(&self, id: &str, in_library: bool) -> Result<()> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut book = self.book(id)?.context("Book does not exist")?;
        book.in_library = in_library;
        write_book(&transaction, &book)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn set_category(&self, id: &str, category: &str) -> Result<()> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut book = self.book(id)?.context("Book does not exist")?;
        book.category = category.to_owned();
        write_book(&transaction, &book)?;
        transaction.commit()?;
        Ok(())
    }

    /// Unknown legacy timestamps remain zero rather than borrowing a remote
    /// update date and presenting it as a real local interaction.
    pub fn record_book_open(&self, id: &str, timestamp: i64) -> Result<()> {
        ensure!(timestamp >= 0, "The book-open timestamp is invalid");
        self.connection.execute("INSERT INTO book_ui_metadata(book_id,opened_at) VALUES(?1,?2) ON CONFLICT(book_id) DO UPDATE SET opened_at=excluded.opened_at WHERE book_ui_metadata.opened_at<excluded.opened_at", params![id,timestamp])?;
        Ok(())
    }

    pub fn record_library_added(&self, id: &str, timestamp: i64) -> Result<()> {
        ensure!(timestamp >= 0, "The library-addition timestamp is invalid");
        self.connection.execute("INSERT INTO book_ui_metadata(book_id,added_at) VALUES(?1,?2) ON CONFLICT(book_id) DO UPDATE SET added_at=excluded.added_at WHERE book_ui_metadata.added_at<>excluded.added_at", params![id,timestamp])?;
        Ok(())
    }

    /// Return the number of books with an actual chapter-state change.
    pub fn mark_books_read(&self, ids: &[String], read: bool) -> Result<usize> {
        ensure!(ids.len() <= 20_000, "The book selection is too large");
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut changed_books = 0;
        for id in ids.iter().collect::<BTreeSet<_>>() {
            let Some(mut book) = self.book(id)? else {
                continue;
            };
            let changed = transaction.execute(
                "UPDATE chapters SET document=json_set(document,'$.read',json(?1)) WHERE book_id=?2 AND json_extract(document,'$.read')<>?3",
                params![if read { "true" } else { "false" },id,read],
            )?;
            if changed == 0 {
                continue;
            }
            book.unread = transaction.query_row("SELECT count(*) FROM chapters WHERE book_id=?1 AND json_extract(document,'$.read')=0", [id], |row| row.get(0))?;
            write_book(&transaction, &book)?;
            changed_books += 1;
        }
        transaction.commit()?;
        Ok(changed_books)
    }

    pub fn save_chapters(&self, book_id: &str, chapters: &[Chapter]) -> Result<()> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut book = self.book(book_id)?.context("Book does not exist")?;
        for chapter in chapters {
            ensure!(
                chapter.book_id == book_id && chapter.source_id == book.source_id,
                "Chapter does not belong to this book and source"
            );
            ensure!(
                !chapter.id.is_empty() && !chapter.remote_id.is_empty(),
                "Chapter identity is missing"
            );
            ensure!(chapter.number.is_finite(), "Chapter number must be finite");
            let mut chapter = chapter.clone();
            if let Some(previous) = query_document::<Chapter, _>(
                &transaction,
                "SELECT document FROM chapters WHERE id = ?1",
                [&chapter.id],
            )? {
                ensure!(
                    previous.book_id == chapter.book_id
                        && previous.source_id == chapter.source_id
                        && previous.remote_id == chapter.remote_id,
                    "Chapter identity belongs to another book"
                );
                chapter.read |= previous.read;
                chapter.downloaded |= previous.downloaded;
                chapter.page_count = chapter.page_count.max(previous.page_count);
            }
            write_chapter(&transaction, &chapter)?;
        }
        let count: u32 = transaction.query_row(
            "SELECT count(*) FROM chapters WHERE book_id = ?1",
            [book_id],
            |row| row.get(0),
        )?;
        book.chapter_count = book.chapter_count.max(count);
        refresh_book_unread(&transaction, &mut book)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn chapters(&self, book_id: &str) -> Result<Vec<Chapter>> {
        query_documents(
            &self.connection,
            "SELECT document FROM chapters WHERE book_id = ?1 ORDER BY number DESC, id",
            [book_id],
        )
    }

    pub fn chapter(&self, id: &str) -> Result<Option<Chapter>> {
        query_document(
            &self.connection,
            "SELECT document FROM chapters WHERE id = ?1",
            [id],
        )
    }

    pub fn set_chapter_read(&self, id: &str, read: bool) -> Result<()> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut chapter = self.chapter(id)?.context("Chapter does not exist")?;
        chapter.read = read;
        let mut book = self
            .book(&chapter.book_id)?
            .context("Book does not exist")?;
        write_chapter(&transaction, &chapter)?;
        refresh_book_unread(&transaction, &mut book)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn set_chapter_downloaded(&self, id: &str, downloaded: bool) -> Result<()> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut chapter = self.chapter(id)?.context("Chapter does not exist")?;
        if downloaded {
            let pages = self.pages(id)?;
            ensure!(pages_are_local(&pages), "Chapter has missing page files");
        }
        chapter.downloaded = downloaded;
        write_chapter(&transaction, &chapter)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn save_pages(&self, chapter_id: &str, pages: &[Page]) -> Result<()> {
        ensure!(pages.len() <= 20_000, "Chapter contains too many pages");
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut chapter: Chapter = query_document(
            &transaction,
            "SELECT document FROM chapters WHERE id = ?1",
            [chapter_id],
        )?
        .context("Chapter does not exist")?;
        let previous: Vec<Page> = query_documents(
            &transaction,
            "SELECT document FROM pages WHERE chapter_id = ?1 ORDER BY position",
            [chapter_id],
        )?;
        let cached: BTreeMap<_, _> = previous
            .into_iter()
            .filter_map(|page| {
                if page.url.is_empty() {
                    return None;
                }
                let path = page.local_path.filter(|path| local_file_exists(path))?;
                Some(((page.url, page.headers), path))
            })
            .collect();
        let pages: Vec<Page> = pages
            .iter()
            .cloned()
            .map(|mut page| {
                if !page
                    .local_path
                    .as_ref()
                    .is_some_and(|path| local_file_exists(path))
                {
                    if let Some(path) = cached.get(&(page.url.clone(), page.headers.clone())) {
                        page.local_path = Some(path.clone());
                    }
                }
                page
            })
            .collect();
        transaction.execute("DELETE FROM pages WHERE chapter_id = ?1", [chapter_id])?;
        for (index, page) in pages.iter().enumerate() {
            ensure!(
                !page.url.is_empty() || page.local_path.is_some(),
                "Page has no image location"
            );
            transaction.execute(
                "INSERT INTO pages (chapter_id, position, document) VALUES (?1, ?2, ?3)",
                params![chapter_id, index as u32, encode(page)?],
            )?;
        }
        chapter.page_count = pages.len() as u32;
        let completed_download: bool = transaction.query_row(
            "SELECT EXISTS(SELECT 1 FROM downloads WHERE chapter_id = ?1 AND state = 'completed')",
            [chapter_id],
            |row| row.get(0),
        )?;
        chapter.downloaded = pages_are_local(&pages)
            && (chapter.source_id == "local" || chapter.downloaded || completed_download);
        write_chapter(&transaction, &chapter)?;
        transaction.commit()?;
        Ok(())
    }

    /// Explicit invalidation prevents a corrupt cache entry being preserved by
    /// a later manifest refresh. The original image file is left untouched.
    pub fn invalidate_page_cache(&self, chapter_id: &str, index: u32) -> Result<()> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut chapter: Chapter = query_document(
            &transaction,
            "SELECT document FROM chapters WHERE id = ?1",
            [chapter_id],
        )?
        .context("Chapter does not exist")?;
        if let Some(mut page) = query_document::<Page, _>(
            &transaction,
            "SELECT document FROM pages WHERE chapter_id = ?1 AND position = ?2",
            params![chapter_id, index],
        )? {
            if !page.url.is_empty() {
                page.local_path = None;
                transaction.execute(
                    "UPDATE pages SET document = ?1 WHERE chapter_id = ?2 AND position = ?3",
                    params![encode(&page)?, chapter_id, index],
                )?;
            }
        }
        chapter.downloaded = false;
        write_chapter(&transaction, &chapter)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn pages(&self, chapter_id: &str) -> Result<Vec<Page>> {
        query_documents(
            &self.connection,
            "SELECT document FROM pages WHERE chapter_id = ?1 ORDER BY position",
            [chapter_id],
        )
    }

    pub fn save_progress(&self, progress: &Progress) -> Result<()> {
        ensure!(
            progress.total > 0 && progress.page > 0 && progress.page <= progress.total,
            "Reading progress uses one-based page numbers within the chapter"
        );
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut chapter = self
            .chapter(&progress.chapter_id)?
            .context("Chapter does not exist")?;
        ensure!(
            chapter.book_id == progress.book_id,
            "Progress belongs to a different book"
        );
        // A vertical page may contain several viewports. Reaching its image
        // number does not mean that its final viewport has been displayed.
        let completed = progress.completed;
        transaction.execute(
            "INSERT OR REPLACE INTO progress (chapter_id, book_id, page, total, completed, updated_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![progress.chapter_id, progress.book_id, progress.page, progress.total, completed, progress.updated_at],
        )?;
        let read_changed = !chapter.read && completed;
        if read_changed || chapter.page_count != progress.total {
            chapter.read |= completed;
            chapter.page_count = progress.total;
            write_chapter(&transaction, &chapter)?;
        }
        if read_changed {
            let mut book = self
                .book(&progress.book_id)?
                .context("Book does not exist")?;
            refresh_book_unread(&transaction, &mut book)?;
        }
        transaction.commit()?;
        Ok(())
    }

    pub fn progress(&self, book_id: &str) -> Result<Option<Progress>> {
        self.connection.query_row("SELECT book_id, chapter_id, page, total, updated_at, completed FROM progress WHERE book_id = ?1 ORDER BY updated_at DESC, rowid DESC LIMIT 1", [book_id], read_progress).optional().map_err(Into::into)
    }

    pub fn chapter_progress(&self, chapter_id: &str) -> Result<Option<Progress>> {
        self.connection.query_row("SELECT book_id, chapter_id, page, total, updated_at, completed FROM progress WHERE chapter_id = ?1", [chapter_id], read_progress).optional().map_err(Into::into)
    }

    pub fn history(&self) -> Result<Vec<Progress>> {
        let mut statement = self.connection.prepare("SELECT book_id, chapter_id, page, total, updated_at, completed FROM progress ORDER BY updated_at DESC, rowid DESC")?;
        let records = statement
            .query_map([], read_progress)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(records)
    }

    pub fn clear_history(&self) -> Result<()> {
        self.connection.execute("DELETE FROM progress", [])?;
        Ok(())
    }

    pub fn delete_history(&self, chapter_id: &str) -> Result<()> {
        self.connection
            .execute("DELETE FROM progress WHERE chapter_id = ?1", [chapter_id])?;
        Ok(())
    }

    pub fn toggle_bookmark(&self, bookmark: &Bookmark) -> Result<bool> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let chapter = self
            .chapter(&bookmark.chapter_id)?
            .context("Chapter does not exist")?;
        ensure!(
            chapter.book_id == bookmark.book_id,
            "Bookmark belongs to a different book"
        );
        ensure!(
            bookmark.page > 0 && (chapter.page_count == 0 || bookmark.page <= chapter.page_count),
            "Bookmark page is outside this chapter"
        );
        let removed = transaction.execute(
            "DELETE FROM bookmarks WHERE chapter_id = ?1 AND page = ?2",
            params![bookmark.chapter_id, bookmark.page],
        )?;
        if removed == 0 {
            transaction.execute("INSERT INTO bookmarks (chapter_id, page, book_id, created_at) VALUES (?1, ?2, ?3, ?4)", params![bookmark.chapter_id, bookmark.page, bookmark.book_id, bookmark.created_at])?;
        }
        transaction.commit()?;
        Ok(removed == 0)
    }

    pub fn has_bookmark(&self, chapter_id: &str, page: u32) -> Result<bool> {
        Ok(self.connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM bookmarks WHERE chapter_id = ?1 AND page = ?2)",
            params![chapter_id, page],
            |row| row.get(0),
        )?)
    }

    pub fn bookmarks(&self, book_id: &str) -> Result<Vec<Bookmark>> {
        let mut statement = self.connection.prepare("SELECT book_id, chapter_id, page, created_at FROM bookmarks WHERE book_id = ?1 ORDER BY created_at DESC, page")?;
        let records = statement
            .query_map([book_id], |row| {
                Ok(Bookmark {
                    book_id: row.get(0)?,
                    chapter_id: row.get(1)?,
                    page: row.get(2)?,
                    created_at: row.get(3)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(records)
    }

    pub fn preferences(&self) -> Result<Preferences> {
        Ok(query_document(
            &self.connection,
            "SELECT document FROM preferences WHERE key = 'application'",
            [],
        )?
        .unwrap_or_default())
    }

    pub fn session(&self) -> Result<Option<NavigationSession>> {
        query_document(
            &self.connection,
            "SELECT document FROM preferences WHERE key = 'navigation-session'",
            [],
        )?
        .map(normalize_session)
        .transpose()
    }

    pub fn save_session(&self, session: &NavigationSession) -> Result<()> {
        let session = normalize_session(session.clone())?;
        self.connection.execute(
            "INSERT INTO preferences(key, document) VALUES('navigation-session', ?1)
             ON CONFLICT(key) DO UPDATE SET document=excluded.document WHERE preferences.document<>excluded.document",
            [encode(&session)?],
        )?;
        Ok(())
    }

    pub fn read_ui_settings(&self, key: &str) -> Result<Option<serde_json::Value>> {
        let key = ui_settings_key(key)?;
        let document = self
            .connection
            .query_row(
                "SELECT document FROM preferences WHERE key=?1",
                [&key],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        document
            .map(|document| {
                ensure!(
                    document.len() <= 1024 * 1024,
                    "The UI settings exceed their storage limit"
                );
                serde_json::from_str(&document).context("Decode UI settings")
            })
            .transpose()
    }

    pub fn write_ui_settings(&self, key: &str, value: &serde_json::Value) -> Result<()> {
        let key = ui_settings_key(key)?;
        let document = serde_json::to_string(value)?;
        ensure!(
            document.len() <= 1024 * 1024,
            "The UI settings exceed their storage limit"
        );
        self.connection.execute("INSERT INTO preferences(key,document) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET document=excluded.document WHERE preferences.document<>excluded.document", params![key, document])?;
        Ok(())
    }

    pub fn save_preferences(&self, preferences: &Preferences) -> Result<()> {
        ensure!(
            matches!(
                preferences.reading_direction.as_str(),
                "rtl" | "ltr" | "vertical" | "vertical-pages"
            ),
            "Unknown reading direction"
        );
        ensure!(
            matches!(preferences.refresh_every, 0 | 1 | 3 | 6 | 10),
            "Unsupported full-refresh interval"
        );
        ensure!(
            preferences.brightness <= 24 && preferences.warmth <= 24,
            "Light values must be between 0 and 24"
        );
        ensure!(preferences.footer_mode < 4, "Unknown reader footer mode");
        ensure!(
            (-100..=100).contains(&preferences.contrast),
            "Contrast is outside its supported range"
        );
        self.connection.execute("INSERT INTO preferences (key, document) VALUES ('application', ?1) ON CONFLICT(key) DO UPDATE SET document = excluded.document WHERE preferences.document<>excluded.document", [encode(preferences)?])?;
        Ok(())
    }

    pub fn sources(&self) -> Result<Vec<SourceConfig>> {
        query_documents(
            &self.connection,
            "SELECT document FROM sources ORDER BY id",
            [],
        )
    }

    pub fn source(&self, id: &str) -> Result<Option<SourceConfig>> {
        query_document(
            &self.connection,
            "SELECT document FROM sources WHERE id = ?1",
            [id],
        )
    }

    pub fn save_source(&self, source: &SourceConfig) -> Result<()> {
        ensure!(
            !source.id.is_empty() && !source.kind.is_empty(),
            "Source identity and kind are required"
        );
        self.connection.execute("INSERT INTO sources (id, document) VALUES (?1, ?2) ON CONFLICT(id) DO UPDATE SET document = excluded.document", params![source.id, encode(source)?])?;
        Ok(())
    }

    pub fn delete_source(&self, id: &str) -> Result<()> {
        self.connection
            .execute("DELETE FROM sources WHERE id = ?1", [id])?;
        Ok(())
    }

    pub fn save_download(&self, job: &DownloadJob) -> Result<()> {
        ensure!(
            matches!(
                job.state.as_str(),
                "queued" | "downloading" | "paused" | "cancelled" | "completed" | "failed"
            ),
            "Unknown download state"
        );
        ensure!(
            job.completed <= job.total,
            "Downloaded page count exceeds the chapter total"
        );
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut chapter = self
            .chapter(&job.chapter_id)?
            .context("Chapter does not exist")?;
        ensure!(
            chapter.book_id == job.book_id,
            "Download belongs to a different book"
        );
        if job.state == "completed" {
            let pages = self.pages(&job.chapter_id)?;
            ensure!(
                job.total > 0
                    && job.completed == job.total
                    && pages.len() == job.total as usize
                    && pages_are_local(&pages),
                "Download cannot complete before all page files are saved"
            );
            chapter.downloaded = true;
        }
        transaction.execute("INSERT INTO downloads (chapter_id, book_id, state, document) VALUES (?1, ?2, ?3, ?4) ON CONFLICT(chapter_id) DO UPDATE SET book_id = excluded.book_id, state = excluded.state, document = excluded.document", params![job.chapter_id, job.book_id, job.state, encode(job)?])?;
        write_chapter(&transaction, &chapter)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn downloads(&self) -> Result<Vec<DownloadJob>> {
        query_documents(
            &self.connection,
            "SELECT document FROM downloads ORDER BY rowid",
            [],
        )
    }

    pub fn download_count(&self) -> Result<usize> {
        let count: u64 =
            self.connection
                .query_row("SELECT count(*) FROM downloads", [], |row| row.get(0))?;
        usize::try_from(count).context("The download count exceeds this device's range")
    }

    pub fn downloads_page(&self, offset: usize, limit: usize) -> Result<Vec<DownloadJob>> {
        ensure!(limit <= 1_000, "The download page size exceeds its limit");
        let offset = i64::try_from(offset).context("The download offset exceeds SQLite's range")?;
        query_documents(
            &self.connection,
            "SELECT document FROM downloads ORDER BY rowid LIMIT ?1 OFFSET ?2",
            params![limit as i64, offset],
        )
    }

    pub fn has_unfinished_downloads(&self) -> Result<bool> {
        self.connection.query_row("SELECT EXISTS(SELECT 1 FROM downloads WHERE state NOT IN ('completed','cancelled'))", [], |row| row.get(0)).map_err(Into::into)
    }

    pub fn download_state_counts(&self) -> Result<BTreeMap<String, usize>> {
        let mut counts = [
            "queued",
            "downloading",
            "paused",
            "cancelled",
            "completed",
            "failed",
        ]
        .into_iter()
        .map(|state| (state.to_owned(), 0usize))
        .collect::<BTreeMap<_, _>>();
        let mut statement = self
            .connection
            .prepare("SELECT state, count(*) FROM downloads GROUP BY state")?;
        let rows = statement.query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, u64>(1)?))
        })?;
        for row in rows {
            let (state, count) = row?;
            counts.insert(
                state,
                usize::try_from(count).context("The download count exceeds this device's range")?,
            );
        }
        Ok(counts)
    }

    /// State changes are serialized with progress writes so global controls do
    /// not require one durable transaction per chapter.
    pub fn transition_downloads(
        &self,
        from_states: &[&str],
        target_state: &str,
    ) -> Result<Vec<DownloadJob>> {
        ensure!(
            matches!(target_state, "queued" | "paused" | "cancelled"),
            "The requested queue transition is invalid"
        );
        ensure!(
            from_states.len() <= 6
                && from_states.iter().all(|state| matches!(
                    *state,
                    "queued" | "downloading" | "paused" | "cancelled" | "completed" | "failed"
                )),
            "The queue transition contains an invalid source state"
        );
        if from_states.is_empty() {
            return Ok(Vec::new());
        }
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let placeholders = std::iter::repeat_n("?", from_states.len())
            .collect::<Vec<_>>()
            .join(",");
        let sql = format!(
            "SELECT document FROM downloads WHERE state IN ({placeholders}) ORDER BY rowid"
        );
        let mut jobs: Vec<DownloadJob> = query_documents(
            &transaction,
            &sql,
            rusqlite::params_from_iter(from_states.iter().copied()),
        )?;
        for job in &mut jobs {
            job.state = target_state.to_owned();
            job.error.clear();
            write_download(&transaction, job)?;
        }
        if target_state == "paused" {
            // An explicit Pause All also owns jobs that were already paused by
            // sleep, so reconnecting must not undo the user's choice.
            transaction.execute("DELETE FROM download_sleep_ownership", [])?;
        } else {
            for job in &jobs {
                transaction.execute(
                    "DELETE FROM download_sleep_ownership WHERE chapter_id=?1",
                    [&job.chapter_id],
                )?;
            }
        }
        transaction.commit()?;
        Ok(jobs)
    }

    pub fn suspended_download_ids(&self) -> Result<BTreeSet<String>> {
        let mut statement = self
            .connection
            .prepare("SELECT chapter_id FROM download_sleep_ownership ORDER BY chapter_id")?;
        statement
            .query_map([], |row| row.get::<_, String>(0))?
            .collect::<rusqlite::Result<BTreeSet<_>>>()
            .map_err(Into::into)
    }

    pub fn has_suspended_downloads(&self) -> Result<bool> {
        self.connection
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM download_sleep_ownership)",
                [],
                |row| row.get(0),
            )
            .map_err(Into::into)
    }

    pub fn pause_downloads_for_suspend(&self) -> Result<Vec<DownloadJob>> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut jobs: Vec<DownloadJob> = query_documents(
            &transaction,
            "SELECT document FROM downloads WHERE state IN ('queued','downloading') ORDER BY rowid",
            [],
        )?;
        for job in &mut jobs {
            transaction.execute(
                "INSERT OR IGNORE INTO download_sleep_ownership(chapter_id) VALUES(?1)",
                [&job.chapter_id],
            )?;
            job.state = "paused".into();
            job.error.clear();
            write_download(&transaction, job)?;
        }
        transaction.commit()?;
        Ok(jobs)
    }

    pub fn resume_owned_downloads(&self) -> Result<Vec<DownloadJob>> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut jobs: Vec<DownloadJob> = query_documents(
            &transaction,
            "SELECT downloads.document FROM downloads JOIN download_sleep_ownership USING(chapter_id) WHERE downloads.state='paused' ORDER BY downloads.rowid",
            [],
        )?;
        for job in &mut jobs {
            job.state = "queued".into();
            job.error.clear();
            write_download(&transaction, job)?;
        }
        transaction.execute("DELETE FROM download_sleep_ownership", [])?;
        transaction.commit()?;
        Ok(jobs)
    }

    /// A deliberate user control atomically revokes automatic wake ownership.
    pub fn set_user_download_state(&self, chapter_id: &str, state: &str) -> Result<DownloadJob> {
        ensure!(
            matches!(state, "queued" | "paused" | "cancelled"),
            "The requested queue state is invalid"
        );
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut job = self
            .download(chapter_id)?
            .context("The download is not queued")?;
        ensure!(
            job.state != "completed",
            "A completed download requires an explicit removal action"
        );
        job.state = state.into();
        job.error.clear();
        write_download(&transaction, &job)?;
        transaction.execute(
            "DELETE FROM download_sleep_ownership WHERE chapter_id=?1",
            [chapter_id],
        )?;
        transaction.commit()?;
        Ok(job)
    }

    pub fn download(&self, chapter_id: &str) -> Result<Option<DownloadJob>> {
        query_document(
            &self.connection,
            "SELECT document FROM downloads WHERE chapter_id = ?1",
            [chapter_id],
        )
    }

    pub fn next_download(&self) -> Result<Option<DownloadJob>> {
        query_document(
            &self.connection,
            "SELECT document FROM downloads WHERE state = 'queued' ORDER BY rowid LIMIT 1",
            [],
        )
    }

    /// Persist one user selection in a single transaction. Existing active or
    /// paused work keeps its state; duplicate chapter identities are coalesced.
    pub fn enqueue_download_batch(
        &self,
        book: &Book,
        chapters: &[Chapter],
    ) -> Result<Vec<DownloadJob>> {
        self.enqueue_download_batch_with_repairs(book, chapters, &BTreeSet::new())
    }

    /// Completed selections stay complete. The controller explicitly identifies
    /// repairs after checking the local manifest, image checksums, and pins.
    pub fn enqueue_download_batch_with_repairs(
        &self,
        book: &Book,
        chapters: &[Chapter],
        repair_completed: &BTreeSet<String>,
    ) -> Result<Vec<DownloadJob>> {
        self.enqueue_download_batch_report(book, chapters, repair_completed)
            .map(|report| report.jobs)
    }

    pub fn enqueue_download_batch_report(
        &self,
        book: &Book,
        chapters: &[Chapter],
        repair_completed: &BTreeSet<String>,
    ) -> Result<DownloadBatchReport> {
        ensure!(
            chapters.len() <= 20_000,
            "The download selection is too large"
        );
        ensure!(
            !book.id.is_empty() && !book.source_id.is_empty() && !book.remote_id.is_empty(),
            "Book identity is missing"
        );
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut book = book.clone();
        let previous_book = self.book(&book.id)?;
        if let Some(previous) = previous_book.clone() {
            ensure!(
                previous.source_id == book.source_id && previous.remote_id == book.remote_id,
                "Book identity belongs to another source"
            );
            book.in_library |= previous.in_library;
            book.unread = previous.unread;
            book.chapter_count = book.chapter_count.max(previous.chapter_count);
            if !previous.category.is_empty() {
                book.category = previous.category;
            }
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
        }
        let initial_book = book.clone();
        if previous_book.is_none() {
            write_book(&transaction, &book)?;
        }
        let mut chapters_added = false;
        let mut unread_changed = false;
        let mut seen = BTreeSet::new();
        let mut jobs = Vec::new();
        let mut metadata_changed = BTreeSet::new();
        for incoming in chapters {
            ensure!(
                incoming.book_id == book.id && incoming.source_id == book.source_id,
                "Chapter does not belong to this book and source"
            );
            ensure!(
                !incoming.id.is_empty()
                    && !incoming.remote_id.is_empty()
                    && incoming.number.is_finite(),
                "Chapter identity or number is invalid"
            );
            if !seen.insert(incoming.id.clone()) {
                continue;
            }
            let mut chapter = incoming.clone();
            let previous_chapter = self.chapter(&chapter.id)?;
            if let Some(previous) = previous_chapter.as_ref() {
                ensure!(
                    previous.book_id == chapter.book_id
                        && previous.source_id == chapter.source_id
                        && previous.remote_id == chapter.remote_id,
                    "Chapter identity belongs to another book"
                );
                chapter.read |= previous.read;
                chapter.downloaded |= previous.downloaded;
                chapter.page_count = chapter.page_count.max(previous.page_count);
            }
            let previous_job = self.download(&chapter.id)?;
            let mut job = previous_job.clone().unwrap_or_else(|| DownloadJob {
                chapter_id: chapter.id.clone(),
                book_id: book.id.clone(),
                total: chapter.page_count,
                ..DownloadJob::default()
            });
            ensure!(
                job.book_id == book.id,
                "Download identity belongs to another book"
            );
            if job.state == "completed" && !repair_completed.contains(&chapter.id) {
                chapter.downloaded = true;
            } else {
                if job.state == "completed" {
                    job.state = "queued".into();
                    job.completed = 0;
                    chapter.downloaded = false;
                } else if !matches!(job.state.as_str(), "queued" | "downloading" | "paused") {
                    job.state = "queued".into();
                }
                job.total = job.total.max(chapter.page_count);
                job.completed = job.completed.min(job.total);
            }
            job.error.clear();
            if previous_chapter
                .as_ref()
                .is_some_and(|previous| previous.downloaded != chapter.downloaded)
                || previous_job.as_ref().is_some_and(|previous| {
                    previous.state == "completed" && repair_completed.contains(&chapter.id)
                })
            {
                metadata_changed.insert(chapter.id.clone());
            }
            chapters_added |= previous_chapter.is_none();
            unread_changed |= previous_chapter
                .as_ref()
                .is_none_or(|previous| previous.read != chapter.read);
            if previous_chapter.as_ref() != Some(&chapter) {
                write_chapter(&transaction, &chapter)?;
            }
            if previous_job.as_ref() != Some(&job) {
                write_download(&transaction, &job)?;
            }
            jobs.push(job);
        }
        if chapters_added {
            let count: u32 = transaction.query_row(
                "SELECT count(*) FROM chapters WHERE book_id = ?1",
                [&book.id],
                |row| row.get(0),
            )?;
            book.chapter_count = book.chapter_count.max(count);
        }
        if unread_changed {
            book.unread = transaction.query_row("SELECT count(*) FROM chapters WHERE book_id = ?1 AND json_extract(document, '$.read')=0", [&book.id], |row| row.get(0))?;
        }
        if previous_book.as_ref().unwrap_or(&initial_book) != &book {
            write_book(&transaction, &book)?;
        }
        transaction.commit()?;
        Ok(DownloadBatchReport {
            jobs,
            downloaded_metadata_changed: metadata_changed.into_iter().collect(),
        })
    }

    /// Commit only pages that changed since the preceding progress update.
    /// The caller validates and pins the files before publishing their paths.
    pub fn record_download_progress(
        &self,
        job: &DownloadJob,
        updates: &[(u32, Page)],
    ) -> Result<()> {
        ensure!(
            matches!(
                job.state.as_str(),
                "queued" | "downloading" | "paused" | "cancelled" | "completed" | "failed"
            ),
            "Unknown download state"
        );
        ensure!(
            job.completed <= job.total,
            "Downloaded page count exceeds the chapter total"
        );
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut chapter = self
            .chapter(&job.chapter_id)?
            .context("Chapter does not exist")?;
        ensure!(
            chapter.book_id == job.book_id,
            "Download belongs to a different book"
        );
        for (index, page) in updates {
            ensure!(
                *index < job.total,
                "The download page is outside its manifest"
            );
            let previous: Page = query_document(
                &transaction,
                "SELECT document FROM pages WHERE chapter_id = ?1 AND position = ?2",
                params![job.chapter_id, index],
            )?
            .context("The download manifest changed")?;
            ensure!(
                previous.url == page.url && previous.headers == page.headers,
                "The download manifest changed while fetching an image"
            );
            ensure!(
                page.local_path
                    .as_ref()
                    .is_some_and(|path| local_file_exists(path)),
                "The downloaded page file is missing"
            );
            transaction.execute(
                "UPDATE pages SET document = ?1 WHERE chapter_id = ?2 AND position = ?3",
                params![encode(page)?, job.chapter_id, index],
            )?;
        }
        let mapped = page_queries::mapped_count(&transaction, &job.chapter_id)?;
        let mut job = job.clone();
        if job.state != "completed" {
            job.completed = job.completed.min(mapped);
        }
        if job.state == "completed" {
            let pages = self.pages(&job.chapter_id)?;
            ensure!(
                job.total > 0
                    && job.completed == job.total
                    && pages.len() == job.total as usize
                    && pages_are_local(&pages),
                "Download cannot complete before all page files are saved"
            );
            chapter.downloaded = true;
            write_chapter(&transaction, &chapter)?;
        }
        write_download(&transaction, &job)?;
        transaction.commit()?;
        Ok(())
    }

    pub fn delete_download(&self, chapter_id: &str) -> Result<()> {
        self.connection
            .execute("DELETE FROM downloads WHERE chapter_id = ?1", [chapter_id])?;
        Ok(())
    }

    /// Remove an explicitly selected completed queue record. Image ownership
    /// belongs to the shared cache; this transaction never removes image files.
    pub fn remove_completed_download(&self, chapter_id: &str) -> Result<()> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let job = self
            .download(chapter_id)?
            .context("The completed download does not exist")?;
        ensure!(
            job.state == "completed",
            "Only a completed download can be removed with this action"
        );
        let mut chapter = self
            .chapter(chapter_id)?
            .context("The downloaded chapter does not exist")?;
        ensure!(
            chapter.book_id == job.book_id,
            "The download belongs to a different book"
        );
        if chapter.source_id != "local" {
            chapter.downloaded = false;
            write_chapter(&transaction, &chapter)?;
        }
        transaction.execute(
            "DELETE FROM downloads WHERE chapter_id = ?1 AND state = 'completed'",
            [chapter_id],
        )?;
        transaction.commit()?;
        Ok(())
    }

    fn recover_downloads(&self) -> Result<()> {
        self.connection.execute(
            "UPDATE downloads SET state='queued', document=json_set(document, '$.state', 'queued') WHERE state='downloading'",
            [],
        )?;
        Ok(())
    }
}

fn encode<T: Serialize>(value: &T) -> Result<String> {
    let document = serde_json::to_string(value)?;
    ensure!(
        document.len() <= MAX_DOCUMENT_BYTES,
        "Application record is too large"
    );
    Ok(document)
}

fn ui_settings_key(key: &str) -> Result<String> {
    ensure!(
        !key.is_empty()
            && key.len() <= 64
            && key
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-'),
        "The UI settings key is invalid"
    );
    Ok(format!("ui:{key}"))
}

fn normalize_session(mut session: NavigationSession) -> Result<NavigationSession> {
    ensure!(
        session.version == 1,
        "This navigation checkpoint uses an unsupported version"
    );
    let route = |value: &str| {
        matches!(
            value,
            "library"
                | "sources"
                | "catalog"
                | "search"
                | "history"
                | "settings"
                | "detail"
                | "reader"
                | "downloads"
        )
    };
    if !route(&session.route) {
        session.route = "library".into();
    }
    if !route(&session.return_route) {
        session.return_route = "library".into();
    }
    session.page = session.page.max(1);
    session.catalog_remote_page = session.catalog_remote_page.max(1);
    session.library_filter = session.library_filter.max(0);
    session.source_filter = session.source_filter.max(0);
    session.catalog_filter = session.catalog_filter.max(0);
    session.history_filter = session.history_filter.max(0);
    session.selected.sort();
    session.selected.dedup();
    session.selected_chapters.sort();
    session.selected_chapters.dedup();
    // Only public, reconstructible panels are durable. In particular keyboard
    // input, pairing nonces, and source credential editors never enter storage.
    if !matches!(
        session.panel_route.as_str(),
        "" | "downloads" | "summary" | "about" | "cache-info"
    ) {
        session.panel_route.clear();
        session.panel_page = 0;
    }
    if let Some(reader) = &mut session.reader {
        ensure!(
            reader.zoom.is_finite(),
            "The reader checkpoint has an invalid zoom"
        );
        reader.page = reader.page.max(1);
        if reader.total > 0 {
            reader.page = reader.page.min(reader.total);
        }
        if reader.scaled_height > 0 && reader.viewport_height > 0 {
            reader.vertical_offset = reader
                .vertical_offset
                .min(reader.scaled_height.saturating_sub(reader.viewport_height));
        }
        reader.zoom = reader.zoom.clamp(1.0, 4.0);
    }
    Ok(session)
}

fn decode<T: DeserializeOwned>(document: String) -> Result<T> {
    ensure!(
        document.len() <= MAX_DOCUMENT_BYTES,
        "Stored application record is too large"
    );
    serde_json::from_str(&document).context("Decode stored application record")
}

fn query_document<T: DeserializeOwned, P: rusqlite::Params>(
    connection: &Connection,
    sql: &str,
    parameters: P,
) -> Result<Option<T>> {
    connection
        .query_row(sql, parameters, |row| row.get::<_, String>(0))
        .optional()?
        .map(decode)
        .transpose()
}

fn query_documents<T: DeserializeOwned, P: rusqlite::Params>(
    connection: &Connection,
    sql: &str,
    parameters: P,
) -> Result<Vec<T>> {
    let mut statement = connection.prepare(sql)?;
    statement
        .query_map(parameters, |row| row.get::<_, String>(0))?
        .map(|row| decode(row?))
        .collect()
}

fn write_book(connection: &Connection, book: &Book) -> Result<()> {
    connection.execute(
        "INSERT INTO books (id, source_id, remote_id, title, in_library, category, updated_at, document) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
         ON CONFLICT(id) DO UPDATE SET title = excluded.title, in_library = excluded.in_library, category = excluded.category, updated_at = excluded.updated_at, document = excluded.document",
        params![book.id, book.source_id, book.remote_id, book.title, book.in_library, book.category, book.updated_at, encode(book)?],
    )?;
    Ok(())
}

fn write_chapter(connection: &Connection, chapter: &Chapter) -> Result<()> {
    connection.execute(
        "INSERT INTO chapters (id, book_id, source_id, remote_id, number, document) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
         ON CONFLICT(id) DO UPDATE SET number = excluded.number, document = excluded.document",
        params![chapter.id, chapter.book_id, chapter.source_id, chapter.remote_id, chapter.number, encode(chapter)?],
    )?;
    Ok(())
}

fn write_download(connection: &Connection, job: &DownloadJob) -> Result<()> {
    connection.execute("INSERT INTO downloads (chapter_id, book_id, state, document) VALUES (?1, ?2, ?3, ?4) ON CONFLICT(chapter_id) DO UPDATE SET book_id = excluded.book_id, state = excluded.state, document = excluded.document", params![job.chapter_id, job.book_id, job.state, encode(job)?])?;
    Ok(())
}

fn refresh_book_unread(transaction: &Transaction<'_>, book: &mut Book) -> Result<()> {
    let chapters: Vec<Chapter> = query_documents(
        transaction,
        "SELECT document FROM chapters WHERE book_id = ?1",
        [&book.id],
    )?;
    book.unread = chapters.iter().filter(|chapter| !chapter.read).count() as u32;
    write_book(transaction, book)
}

fn pages_are_local(pages: &[Page]) -> bool {
    !pages.is_empty()
        && pages.iter().all(|page| {
            page.local_path
                .as_ref()
                .is_some_and(|path| local_file_exists(path))
        })
}

fn local_file_exists(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|metadata| {
        metadata.is_file() && metadata.len() > 0 && metadata.len() <= crate::imaging::MAX_PAGE_BYTES
    })
}

fn read_progress(row: &rusqlite::Row<'_>) -> rusqlite::Result<Progress> {
    Ok(Progress {
        book_id: row.get(0)?,
        chapter_id: row.get(1)?,
        page: row.get(2)?,
        total: row.get(3)?,
        updated_at: row.get(4)?,
        completed: row.get(5)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_books_keep_catalog_order_and_cached_entries_without_decoding_other_sources() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("source-catalog.sqlite3");
        let store = Store::open(&database).unwrap();
        for (id, source_id, title, updated_at, in_library) in [
            ("old", "selected", "Aardvark", 10, true),
            ("b", "selected", "alpha", 20, true),
            ("a", "selected", "ALPHA", 20, false),
            ("z", "selected", "Zulu", 20, false),
            ("recent", "selected", "Zulu", 30, false),
            ("other", "another", "First elsewhere", 40, true),
        ] {
            store
                .upsert_book(&Book {
                    id: id.into(),
                    source_id: source_id.into(),
                    remote_id: id.into(),
                    title: title.into(),
                    updated_at,
                    in_library,
                    ..Book::default()
                })
                .unwrap();
        }
        let expected: Vec<_> = store
            .books()
            .unwrap()
            .into_iter()
            .filter(|book| book.source_id == "selected")
            .collect();
        assert_eq!(
            expected
                .iter()
                .map(|book| book.id.as_str())
                .collect::<Vec<_>>(),
            ["recent", "a", "b", "z", "old"]
        );
        assert_eq!(store.books_for_source("selected").unwrap(), expected);
        assert_eq!(expected.iter().filter(|book| !book.in_library).count(), 3);

        // Valid JSON outside this source must never be decoded as a Book.
        store
            .connection
            .execute(
                "UPDATE books SET document='\"not a Book\"' WHERE id='other'",
                [],
            )
            .unwrap();
        assert!(store.books().is_err());
        assert_eq!(store.books_for_source("selected").unwrap(), expected);
        assert!(store.books_for_source("missing").unwrap().is_empty());
        let observer = Store::open_read_only(&database).unwrap();
        assert_eq!(observer.books_for_source("selected").unwrap(), expected);
        assert!(observer.books_for_source("missing").unwrap().is_empty());
    }

    #[test]
    fn existing_write_connections_do_not_create_schema_or_recover_live_downloads() {
        let directory = tempfile::tempdir().unwrap();
        let missing = directory.path().join("missing.sqlite");
        assert!(Store::open_existing_for_write(&missing).is_err());
        assert!(!missing.exists());
        let database = directory.path().join("live.sqlite");
        let store = Store::open(&database).unwrap();
        let (book, chapter) = fixture(&store, "network");
        store
            .save_download(&DownloadJob {
                chapter_id: chapter.id.clone(),
                book_id: book.id.clone(),
                state: "downloading".into(),
                total: 4,
                ..DownloadJob::default()
            })
            .unwrap();
        let background = Store::open_existing_for_write(&database).unwrap();
        assert_eq!(
            background.download(&chapter.id).unwrap().unwrap().state,
            "downloading"
        );
        assert_eq!(
            background
                .mark_books_read(&[book.id.clone()], true)
                .unwrap(),
            1
        );
        assert!(store.chapter(&chapter.id).unwrap().unwrap().read);
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "downloading"
        );
        let uninitialized = directory.path().join("uninitialized.sqlite");
        let connection = Connection::open(&uninitialized).unwrap();
        connection
            .execute_batch(
                "CREATE TABLE retained(value TEXT); INSERT INTO retained VALUES('kept');",
            )
            .unwrap();
        assert!(Store::open_existing_for_write(&uninitialized).is_err());
        assert_eq!(
            connection
                .query_row(
                    "SELECT count(*) FROM sqlite_schema WHERE name='books'",
                    [],
                    |row| row.get::<_, usize>(0)
                )
                .unwrap(),
            0
        );
        assert_eq!(
            connection
                .query_row("SELECT value FROM retained", [], |row| row
                    .get::<_, String>(0))
                .unwrap(),
            "kept"
        );
    }

    #[test]
    fn unchanged_preferences_skip_writes_and_vertical_pages_remains_a_valid_direction() {
        let store = Store::open(":memory:").unwrap();
        let preferences = Preferences {
            reading_direction: "vertical-pages".into(),
            ..Preferences::default()
        };
        store.save_preferences(&preferences).unwrap();
        let before: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        store.save_preferences(&preferences).unwrap();
        let after: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        assert_eq!(after, before);
        assert_eq!(store.preferences().unwrap(), preferences);
    }

    #[test]
    fn ui_settings_are_bounded_namespaced_and_skip_unchanged_values() {
        let store = Store::open(":memory:").unwrap();
        let preferences = store.preferences().unwrap();
        assert!(store.read_ui_settings("interaction-v1").unwrap().is_none());
        let value = serde_json::json!({"sort":"title","unread":"exclude"});
        store.write_ui_settings("interaction-v1", &value).unwrap();
        assert_eq!(
            store.read_ui_settings("interaction-v1").unwrap(),
            Some(value.clone())
        );
        let before: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        store.write_ui_settings("interaction-v1", &value).unwrap();
        let after: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        assert_eq!(before, after);
        store
            .write_ui_settings("application", &serde_json::json!({"brightness":1}))
            .unwrap();
        assert_eq!(store.preferences().unwrap(), preferences);
        assert!(store.write_ui_settings("../application", &value).is_err());
        assert!(
            store
                .write_ui_settings(
                    "interaction-v1",
                    &serde_json::json!("x".repeat(1024 * 1024))
                )
                .is_err()
        );
        assert_eq!(
            store.read_ui_settings("interaction-v1").unwrap(),
            Some(value)
        );
    }

    #[test]
    fn bulk_read_changes_are_atomic_and_do_not_rewrite_progress_or_downloads() {
        let store = Store::open(":memory:").unwrap();
        let (first, first_chapter) = fixture(&store, "first");
        let (second, second_chapter) = fixture(&store, "second");
        store
            .save_progress(&Progress {
                book_id: first.id.clone(),
                chapter_id: first_chapter.id.clone(),
                page: 2,
                total: 4,
                updated_at: 20,
                completed: false,
            })
            .unwrap();
        store
            .save_download(&DownloadJob {
                book_id: first.id.clone(),
                chapter_id: first_chapter.id.clone(),
                state: "paused".into(),
                total: 4,
                ..DownloadJob::default()
            })
            .unwrap();
        let ids = vec![first.id.clone(), second.id.clone(), first.id.clone()];
        assert_eq!(store.mark_books_read(&ids, true).unwrap(), 2);
        assert!(store.chapter(&first_chapter.id).unwrap().unwrap().read);
        assert!(store.chapter(&second_chapter.id).unwrap().unwrap().read);
        assert_eq!(store.book(&first.id).unwrap().unwrap().unread, 0);
        assert_eq!(store.progress(&first.id).unwrap().unwrap().page, 2);
        assert_eq!(
            store.download(&first_chapter.id).unwrap().unwrap().state,
            "paused"
        );
        let before: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        assert_eq!(store.mark_books_read(&ids, true).unwrap(), 0);
        let after: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        assert_eq!(before, after);
        assert_eq!(store.mark_books_read(&ids, false).unwrap(), 2);
        assert_eq!(store.book(&second.id).unwrap().unwrap().unread, 1);
    }

    #[test]
    fn local_interaction_dates_are_explicit_and_never_derived_from_remote_dates() {
        let store = Store::open(":memory:").unwrap();
        let (book, _) = fixture(&store, "network");
        assert_eq!(
            store
                .connection
                .query_row("SELECT count(*) FROM book_ui_metadata", [], |row| row
                    .get::<_, usize>(0))
                .unwrap(),
            0
        );
        store.record_library_added(&book.id, 10).unwrap();
        store.record_book_open(&book.id, 20).unwrap();
        store.record_book_open(&book.id, 15).unwrap();
        let dates = store
            .connection
            .query_row(
                "SELECT added_at,opened_at FROM book_ui_metadata WHERE book_id=?1",
                [&book.id],
                |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)),
            )
            .unwrap();
        assert_eq!(dates, (10, 20));
        assert!(store.record_book_open(&book.id, -1).is_err());
    }

    #[test]
    fn repeating_an_unchanged_paused_batch_has_no_data_writes() {
        let store = Store::open(":memory:").unwrap();
        let (book, chapter) = fixture(&store, "network");
        store
            .enqueue_download_batch(&book, &[chapter.clone()])
            .unwrap();
        store
            .set_user_download_state(&chapter.id, "paused")
            .unwrap();
        let before: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        let jobs = store
            .enqueue_download_batch(&book, &[chapter.clone(), chapter.clone()])
            .unwrap();
        let after: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].state, "paused");
        assert_eq!(after, before);
        assert_eq!(store.book(&book.id).unwrap().unwrap().unread, 1);
    }

    #[test]
    fn fixed_download_paths_are_keyset_paged_without_double_counting_shared_files() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("storage.sqlite");
        let store = Store::open(&database).unwrap();
        let (book, first) = fixture(&store, "network");
        let second = Chapter {
            id: "second".into(),
            remote_id: "second".into(),
            ..first.clone()
        };
        let transient = Chapter {
            id: "transient".into(),
            remote_id: "transient".into(),
            ..first.clone()
        };
        store
            .save_chapters(&book.id, &[second.clone(), transient.clone()])
            .unwrap();
        let one = directory.path().join("one.png");
        let two = directory.path().join("two.png");
        image::GrayImage::from_pixel(2, 3, image::Luma([17]))
            .save(&one)
            .unwrap();
        image::GrayImage::from_pixel(2, 3, image::Luma([34]))
            .save(&two)
            .unwrap();
        for chapter in [&first, &second] {
            store
                .save_pages(
                    &chapter.id,
                    &[Page {
                        url: "https://example.test/shared.png".into(),
                        local_path: Some(one.clone()),
                        ..Page::default()
                    }],
                )
                .unwrap();
            store
                .save_download(&DownloadJob {
                    chapter_id: chapter.id.clone(),
                    book_id: book.id.clone(),
                    state: "completed".into(),
                    completed: 1,
                    total: 1,
                    ..DownloadJob::default()
                })
                .unwrap();
        }
        store
            .save_pages(
                &transient.id,
                &[Page {
                    url: "https://example.test/transient.png".into(),
                    local_path: Some(two.clone()),
                    ..Page::default()
                }],
            )
            .unwrap();
        store
            .save_download(&DownloadJob {
                chapter_id: transient.id.clone(),
                book_id: book.id.clone(),
                state: "downloading".into(),
                total: 1,
                ..DownloadJob::default()
            })
            .unwrap();
        let (local_book, local_chapter) = fixture(&store, "local");
        store
            .save_pages(
                &local_chapter.id,
                &[Page {
                    local_path: Some(two.clone()),
                    ..Page::default()
                }],
            )
            .unwrap();
        let observer = Store::open_read_only(&database).unwrap();
        let page1 = observer.completed_download_paths_page(None, 1).unwrap();
        let page2 = observer
            .completed_download_paths_page(page1[0].to_str(), 1)
            .unwrap();
        assert_eq!(page1, vec![one.clone()]);
        assert_eq!(page2, vec![two.clone()]);
        assert!(
            observer
                .completed_download_paths_page(page2[0].to_str(), 1)
                .unwrap()
                .is_empty()
        );
        assert_eq!(observer.completed_download_chapter_ids().unwrap().len(), 3);
        assert_eq!(
            store.download(&transient.id).unwrap().unwrap().state,
            "downloading"
        );
        assert!(observer.set_library(&local_book.id, false).is_err());
        // The observer leaves no read transaction open across filesystem work.
        store
            .set_user_download_state(&transient.id, "paused")
            .unwrap();
        assert_eq!(
            std::fs::metadata(&page1[0]).unwrap().len()
                + std::fs::metadata(&page2[0]).unwrap().len(),
            std::fs::metadata(one).unwrap().len() + std::fs::metadata(two).unwrap().len()
        );
    }

    #[test]
    fn navigation_checkpoints_round_trip_non_reader_routes_and_reader_geometry() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("session.sqlite");
        let store = Store::open(&path).unwrap();
        assert!(store.session().unwrap().is_none());
        for route in [
            "library", "sources", "catalog", "search", "history", "settings", "detail", "reader",
        ] {
            let session = NavigationSession {
                route: route.into(),
                page: 5,
                return_route: "history".into(),
                query: "A saved query".into(),
                search_scope: "komga".into(),
                newest_first: false,
                catalog_book_ids: vec!["book-z".into(), "book-a".into()],
                catalog_listings: vec![("popular".into(), "Popular".into())],
                search_groups: vec![crate::domain::SearchSession {
                    id: "source".into(),
                    title: "A public source".into(),
                    book_ids: vec!["book-z".into(), "book-a".into()],
                    failed: true,
                    has_next: true,
                }],
                reader: Some(crate::domain::ReaderSession {
                    book_id: "book".into(),
                    chapter_id: "chapter".into(),
                    page: 12,
                    total: 40,
                    vertical_offset: 680,
                    scaled_height: 2400,
                    viewport_height: 800,
                    zoom: 2.0,
                    landscape: true,
                    ..crate::domain::ReaderSession::default()
                }),
                ..NavigationSession::default()
            };
            store.save_session(&session).unwrap();
            assert_eq!(store.session().unwrap().unwrap(), session);
        }
        drop(store);
        let session = Store::open(path).unwrap().session().unwrap().unwrap();
        assert_eq!(session.route, "reader");
        assert_eq!(session.reader.unwrap().vertical_offset, 680);
    }

    #[test]
    fn navigation_defaults_and_public_panels_do_not_persist_secret_inputs() {
        let store = Store::open(":memory:").unwrap();
        let defaults: NavigationSession = serde_json::from_str("{}").unwrap();
        assert_eq!(defaults.version, 1);
        assert_eq!(defaults.route, "library");
        let session: NavigationSession = serde_json::from_str(r#"{"route":"settings","panel_route":"pairing","password":"secret","keyboard":"secret","pairing_nonce":"secret"}"#).unwrap();
        store.save_session(&session).unwrap();
        let saved = store.session().unwrap().unwrap();
        assert!(saved.panel_route.is_empty());
        let document: String = store
            .connection
            .query_row(
                "SELECT document FROM preferences WHERE key='navigation-session'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(!document.contains("secret"));
        assert!(!document.contains("password"));
        let before: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        store.save_session(&saved).unwrap();
        let after: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        assert_eq!(before, after);
        let future = NavigationSession {
            version: 99,
            ..saved.clone()
        };
        assert!(store.save_session(&future).is_err());
        assert_eq!(store.session().unwrap().unwrap(), saved);
    }

    #[test]
    fn repeated_page_progress_only_recalculates_unread_when_the_chapter_first_completes() {
        let store = Store::open(":memory:").unwrap();
        let (book, chapter) = fixture(&store, "local");
        let mut progress = Progress {
            book_id: book.id.clone(),
            chapter_id: chapter.id.clone(),
            page: 2,
            total: 4,
            completed: false,
            updated_at: 20,
        };
        let before: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        store.save_progress(&progress).unwrap();
        let after: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        assert_eq!(after - before, 1);
        progress.page = 4;
        progress.completed = true;
        store.save_progress(&progress).unwrap();
        assert_eq!(store.book(&book.id).unwrap().unwrap().unread, 0);
        let before: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        store.save_progress(&progress).unwrap();
        let after: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        assert_eq!(after - before, 1);
    }

    #[test]
    fn suspend_ownership_survives_restart_and_manual_pause_is_not_auto_resumed() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("sleep.sqlite");
        let store = Store::open(&path).unwrap();
        let (book, first) = fixture(&store, "network");
        let chapters = [
            first.clone(),
            Chapter {
                id: "second".into(),
                remote_id: "second".into(),
                ..first.clone()
            },
            Chapter {
                id: "manual".into(),
                remote_id: "manual".into(),
                ..first
            },
        ];
        store.enqueue_download_batch(&book, &chapters).unwrap();
        store
            .set_user_download_state(&chapters[2].id, "paused")
            .unwrap();
        let changed = store.pause_downloads_for_suspend().unwrap();
        assert_eq!(changed.len(), 2);
        assert_eq!(store.suspended_download_ids().unwrap().len(), 2);
        store
            .set_user_download_state(&chapters[0].id, "paused")
            .unwrap();
        drop(store);
        let store = Store::open(&path).unwrap();
        assert!(store.has_suspended_downloads().unwrap());
        let resumed = store.resume_owned_downloads().unwrap();
        assert_eq!(resumed.len(), 1);
        assert_eq!(resumed[0].chapter_id, chapters[1].id);
        assert_eq!(
            store.download(&chapters[0].id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(
            store.download(&chapters[2].id).unwrap().unwrap().state,
            "paused"
        );
        assert!(!store.has_suspended_downloads().unwrap());
        store.pause_downloads_for_suspend().unwrap();
        store
            .transition_downloads(&["queued", "downloading"], "paused")
            .unwrap();
        assert!(!store.has_suspended_downloads().unwrap());
    }

    #[test]
    fn persistent_databases_use_durable_rollback_journals_without_mapped_io() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path().join("state.sqlite")).unwrap();
        let journal: String = store
            .connection
            .pragma_query_value(None, "journal_mode", |row| row.get(0))
            .unwrap();
        let synchronous: i64 = store
            .connection
            .pragma_query_value(None, "synchronous", |row| row.get(0))
            .unwrap();
        let mapped_bytes: i64 = store
            .connection
            .pragma_query_value(None, "mmap_size", |row| row.get(0))
            .unwrap();
        assert_eq!(journal, "delete");
        assert_eq!(synchronous, 2);
        assert_eq!(mapped_bytes, 0);
    }

    #[test]
    fn opening_a_healthy_wal_database_preserves_its_committed_data() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("state.sqlite");
        {
            let connection = Connection::open(&path).unwrap();
            connection
                .pragma_update(None, "journal_mode", "WAL")
                .unwrap();
            connection.execute_batch("CREATE TABLE retained_data (value TEXT NOT NULL); INSERT INTO retained_data VALUES ('retained')").unwrap();
        }
        let store = Store::open(&path).unwrap();
        let value: String = store
            .connection
            .query_row("SELECT value FROM retained_data", [], |row| row.get(0))
            .unwrap();
        let journal: String = store
            .connection
            .pragma_query_value(None, "journal_mode", |row| row.get(0))
            .unwrap();
        assert_eq!(value, "retained");
        assert_eq!(journal, "delete");
    }

    #[test]
    fn a_corrupt_database_is_reported_without_replacing_its_original_bytes() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("state.sqlite");
        let original = vec![b'x'; 4_096];
        fs::write(&path, &original).unwrap();
        assert!(Store::open(&path).is_err());
        assert_eq!(fs::read(path).unwrap(), original);
    }

    fn fixture(store: &Store, source: &str) -> (Book, Chapter) {
        let book = Book {
            id: crate::domain::compound_id(source, "series"),
            source_id: source.into(),
            remote_id: "series".into(),
            title: "A book".into(),
            in_library: true,
            ..Book::default()
        };
        let chapter = Chapter {
            id: crate::domain::compound_id(source, "chapter"),
            book_id: book.id.clone(),
            source_id: source.into(),
            remote_id: "chapter".into(),
            number: 1.0,
            page_count: 4,
            ..Chapter::default()
        };
        store.upsert_book(&book).unwrap();
        store
            .save_chapters(&book.id, std::slice::from_ref(&chapter))
            .unwrap();
        (book, chapter)
    }

    #[test]
    fn source_identity_and_progress_remain_separate() {
        let store = Store::open(":memory:").unwrap();
        let (first, chapter) = fixture(&store, "first");
        let (second, _) = fixture(&store, "second");
        store
            .save_progress(&Progress {
                book_id: first.id.clone(),
                chapter_id: chapter.id.clone(),
                page: 4,
                total: 4,
                updated_at: 20,
                completed: true,
            })
            .unwrap();
        assert!(store.progress(&second.id).unwrap().is_none());
        assert_eq!(store.book(&first.id).unwrap().unwrap().unread, 0);
        assert!(store.chapter(&chapter.id).unwrap().unwrap().read);
        let mut invalid = first.clone();
        invalid.source_id = "second".into();
        assert!(store.upsert_book(&invalid).is_err());
    }

    #[test]
    fn refresh_preserves_local_state_and_changes_are_transactional() {
        let store = Store::open(":memory:").unwrap();
        let (book, chapter) = fixture(&store, "local");
        store.set_category(&book.id, "reading").unwrap();
        store.set_chapter_read(&chapter.id, true).unwrap();
        let mut refresh = book.clone();
        refresh.in_library = false;
        refresh.title = "Updated title".into();
        store.upsert_book(&refresh).unwrap();
        store
            .save_chapters(&book.id, std::slice::from_ref(&chapter))
            .unwrap();
        let saved = store.book(&book.id).unwrap().unwrap();
        assert!(saved.in_library);
        assert_eq!(saved.category, "reading");
        assert!(store.chapter(&chapter.id).unwrap().unwrap().read);
        let new = Chapter {
            id: "new".into(),
            remote_id: "new".into(),
            ..chapter.clone()
        };
        let invalid = Chapter {
            id: "bad".into(),
            book_id: "another".into(),
            ..chapter
        };
        assert!(store.save_chapters(&book.id, &[new, invalid]).is_err());
        assert!(store.chapter("new").unwrap().is_none());
    }

    #[test]
    fn preferences_bookmarks_and_queue_survive_restart() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("state.sqlite");
        let (book, chapter);
        {
            let store = Store::open(&database).unwrap();
            (book, chapter) = fixture(&store, "local");
            let preferences = Preferences {
                incognito: true,
                reading_direction: "ltr".into(),
                ..Preferences::default()
            };
            store.save_preferences(&preferences).unwrap();
            assert!(
                store
                    .toggle_bookmark(&Bookmark {
                        book_id: book.id.clone(),
                        chapter_id: chapter.id.clone(),
                        page: 2,
                        created_at: 10
                    })
                    .unwrap()
            );
            store
                .save_download(&DownloadJob {
                    book_id: book.id.clone(),
                    chapter_id: chapter.id.clone(),
                    state: "downloading".into(),
                    completed: 1,
                    total: 4,
                    error: String::new(),
                })
                .unwrap();
        }
        let reopened = Store::open(&database).unwrap();
        assert!(reopened.preferences().unwrap().incognito);
        assert!(reopened.has_bookmark(&chapter.id, 2).unwrap());
        assert_eq!(reopened.downloads().unwrap()[0].state, "queued");
        assert!(
            !reopened
                .toggle_bookmark(&Bookmark {
                    book_id: book.id,
                    chapter_id: chapter.id,
                    page: 2,
                    created_at: 20
                })
                .unwrap()
        );
    }

    #[test]
    fn completed_download_requires_real_files() {
        let store = Store::open(":memory:").unwrap();
        let (book, chapter) = fixture(&store, "network");
        let mut job = DownloadJob {
            book_id: book.id,
            chapter_id: chapter.id.clone(),
            state: "completed".into(),
            completed: 1,
            total: 1,
            error: String::new(),
        };
        assert!(store.save_download(&job).is_err());
        let directory = tempfile::tempdir().unwrap();
        let image = directory.path().join("page.png");
        fs::write(&image, b"image data").unwrap();
        store
            .save_pages(
                &chapter.id,
                &[Page {
                    local_path: Some(image),
                    ..Page::default()
                }],
            )
            .unwrap();
        store.save_download(&job).unwrap();
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        job.completed = 2;
        assert!(store.save_download(&job).is_err());
    }

    #[test]
    fn progress_resumes_the_last_visit_even_with_equal_timestamps() {
        let store = Store::open(":memory:").unwrap();
        let (book, first) = fixture(&store, "local");
        let second = Chapter {
            id: "second".into(),
            remote_id: "second".into(),
            number: 2.0,
            ..first.clone()
        };
        store.save_chapters(&book.id, &[second.clone()]).unwrap();
        for chapter in [&first, &second, &first] {
            store
                .save_progress(&Progress {
                    book_id: book.id.clone(),
                    chapter_id: chapter.id.clone(),
                    page: 2,
                    total: 4,
                    updated_at: 10,
                    completed: false,
                })
                .unwrap();
        }
        assert_eq!(
            store.progress(&book.id).unwrap().unwrap().chapter_id,
            first.id
        );
        assert_eq!(store.history().unwrap().len(), 2);
    }

    #[test]
    fn deleting_one_history_entry_keeps_read_state_pages_and_other_visits() {
        let store = Store::open(":memory:").unwrap();
        let (book, first) = fixture(&store, "local");
        let second = Chapter {
            id: "second".into(),
            remote_id: "second".into(),
            number: 2.0,
            ..first.clone()
        };
        store.save_chapters(&book.id, &[second.clone()]).unwrap();
        let directory = tempfile::tempdir().unwrap();
        let image = directory.path().join("page.png");
        fs::write(&image, b"saved page bytes").unwrap();
        store
            .save_pages(
                &first.id,
                &[Page {
                    local_path: Some(image.clone()),
                    ..Page::default()
                }],
            )
            .unwrap();
        for chapter in [&first, &second] {
            store
                .save_progress(&Progress {
                    book_id: book.id.clone(),
                    chapter_id: chapter.id.clone(),
                    page: 1,
                    total: 1,
                    updated_at: 20,
                    completed: true,
                })
                .unwrap();
        }
        store.delete_history(&first.id).unwrap();
        assert!(store.chapter_progress(&first.id).unwrap().is_none());
        assert!(store.chapter_progress(&second.id).unwrap().is_some());
        assert!(store.chapter(&first.id).unwrap().unwrap().read);
        assert!(store.chapter(&first.id).unwrap().unwrap().downloaded);
        assert_eq!(
            store.pages(&first.id).unwrap()[0].local_path,
            Some(image.clone())
        );
        assert!(image.is_file());
        assert_eq!(store.history().unwrap().len(), 1);
    }

    #[test]
    fn late_manifests_keep_only_cache_entries_with_the_same_url_and_headers() {
        let store = Store::open(":memory:").unwrap();
        let (_, chapter) = fixture(&store, "network");
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("page.png");
        fs::write(&path, b"verified cached page").unwrap();
        let original = Page {
            url: "https://example.test/1.png".into(),
            headers: [("Referer".into(), "https://example.test/book".into())].into(),
            local_path: Some(path.clone()),
        };
        store.save_pages(&chapter.id, &[original.clone()]).unwrap();
        store.set_chapter_downloaded(&chapter.id, true).unwrap();
        let mut manifest = original.clone();
        manifest.local_path = None;
        store.save_pages(&chapter.id, &[manifest.clone()]).unwrap();
        assert_eq!(store.pages(&chapter.id).unwrap()[0].local_path, Some(path));
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        store.invalidate_page_cache(&chapter.id, 0).unwrap();
        store.save_pages(&chapter.id, &[manifest.clone()]).unwrap();
        assert!(store.pages(&chapter.id).unwrap()[0].local_path.is_none());
        assert!(!store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        store.save_pages(&chapter.id, &[original]).unwrap();
        manifest
            .headers
            .insert("Referer".into(), "https://example.test/another-book".into());
        store.save_pages(&chapter.id, &[manifest]).unwrap();
        assert!(store.pages(&chapter.id).unwrap()[0].local_path.is_none());
    }

    #[test]
    fn reading_progress_and_download_updates_do_not_erase_each_other() {
        use std::{
            sync::{Arc, Barrier},
            thread,
        };
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("concurrent.sqlite");
        let store = Store::open(&database).unwrap();
        let (book, chapter) = fixture(&store, "network");
        let path = directory.path().join("page.png");
        fs::write(&path, b"verified cached page").unwrap();
        store
            .save_pages(
                &chapter.id,
                &[Page {
                    url: "https://example.test/1.png".into(),
                    local_path: Some(path.clone()),
                    ..Page::default()
                }],
            )
            .unwrap();
        store.set_chapter_downloaded(&chapter.id, true).unwrap();
        let barrier = Arc::new(Barrier::new(2));
        let worker_database = database.clone();
        let worker_chapter = chapter.clone();
        let worker_barrier = barrier.clone();
        let worker = thread::spawn(move || {
            let store = Store::open(worker_database).unwrap();
            worker_barrier.wait();
            for _ in 0..50 {
                store
                    .save_pages(
                        &worker_chapter.id,
                        &[Page {
                            url: "https://example.test/1.png".into(),
                            local_path: Some(path.clone()),
                            ..Page::default()
                        }],
                    )
                    .unwrap();
            }
        });
        barrier.wait();
        for updated_at in 1..=50 {
            store
                .save_progress(&Progress {
                    book_id: book.id.clone(),
                    chapter_id: chapter.id.clone(),
                    page: 1,
                    total: 1,
                    updated_at,
                    completed: true,
                })
                .unwrap();
        }
        worker.join().unwrap();
        let result = store.chapter(&chapter.id).unwrap().unwrap();
        assert!(result.read);
        assert!(result.downloaded);
    }

    #[test]
    fn the_last_image_is_not_completed_until_the_reader_reaches_its_bottom() {
        let store = Store::open(":memory:").unwrap();
        let (book, chapter) = fixture(&store, "local");
        let mut progress = Progress {
            book_id: book.id.clone(),
            chapter_id: chapter.id.clone(),
            page: 1,
            total: 1,
            updated_at: 20,
            completed: false,
        };
        store.save_progress(&progress).unwrap();
        assert!(!store.chapter(&chapter.id).unwrap().unwrap().read);
        assert!(
            !store
                .chapter_progress(&chapter.id)
                .unwrap()
                .unwrap()
                .completed
        );
        progress.completed = true;
        store.save_progress(&progress).unwrap();
        assert!(store.chapter(&chapter.id).unwrap().unwrap().read);
    }

    #[test]
    fn a_batch_is_atomic_deduplicated_and_keeps_paused_work_paused() {
        let store = Store::open(":memory:").unwrap();
        let (book, first) = fixture(&store, "network");
        store
            .save_download(&DownloadJob {
                chapter_id: first.id.clone(),
                book_id: book.id.clone(),
                state: "paused".into(),
                total: 4,
                ..DownloadJob::default()
            })
            .unwrap();
        let second = Chapter {
            id: "second".into(),
            remote_id: "second".into(),
            number: 2.0,
            ..first.clone()
        };
        let jobs = store
            .enqueue_download_batch(&book, &[first.clone(), second.clone(), second.clone()])
            .unwrap();
        assert_eq!(jobs.len(), 2);
        assert_eq!(jobs[0].state, "paused");
        assert_eq!(store.downloads().unwrap().len(), 2);
        assert_eq!(
            store.next_download().unwrap().unwrap().chapter_id,
            second.id
        );
        let mut changed_book = book.clone();
        changed_book.title = "An uncommitted title".into();
        let invalid = Chapter {
            id: "invalid".into(),
            source_id: "another".into(),
            ..second
        };
        assert!(
            store
                .enqueue_download_batch(&changed_book, &[invalid])
                .is_err()
        );
        assert_eq!(store.book(&book.id).unwrap().unwrap().title, book.title);
        assert!(store.chapter("invalid").unwrap().is_none());
    }

    #[test]
    fn progress_writes_only_changed_pages_and_rejects_stale_page_identities() {
        let store = Store::open(":memory:").unwrap();
        let (book, chapter) = fixture(&store, "network");
        let mut pages = vec![
            Page {
                url: "https://example.test/1.png".into(),
                ..Page::default()
            },
            Page {
                url: "https://example.test/2.png".into(),
                ..Page::default()
            },
        ];
        store.save_pages(&chapter.id, &pages).unwrap();
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("one.png");
        fs::write(&file, b"validated source image").unwrap();
        pages[0].local_path = Some(file.clone());
        let job = DownloadJob {
            chapter_id: chapter.id.clone(),
            book_id: book.id,
            state: "downloading".into(),
            completed: 1,
            total: 2,
            ..DownloadJob::default()
        };
        let before: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        store
            .record_download_progress(&job, &[(0, pages[0].clone())])
            .unwrap();
        let after: i64 = store
            .connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap();
        // One page row, one mapped-reference counter, and one job row change.
        assert_eq!(after - before, 3);
        assert_eq!(store.pages(&chapter.id).unwrap().len(), 2);
        assert!(store.pages(&chapter.id).unwrap()[1].local_path.is_none());
        let stale = Page {
            url: "https://example.test/a-replaced-image.png".into(),
            local_path: Some(file.clone()),
            ..Page::default()
        };
        assert!(store.record_download_progress(&job, &[(1, stale)]).is_err());
        let mut complete = job;
        complete.completed = 2;
        complete.state = "completed".into();
        assert!(store.record_download_progress(&complete, &[]).is_err());
        pages[1].local_path = Some(file);
        store
            .record_download_progress(&complete, &[(1, pages[1].clone())])
            .unwrap();
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
    }

    #[test]
    fn transient_reading_files_are_not_reported_as_pinned_offline_downloads() {
        let store = Store::open(":memory:").unwrap();
        let (_, chapter) = fixture(&store, "network");
        let directory = tempfile::tempdir().unwrap();
        let file = directory.path().join("reading.png");
        fs::write(&file, b"validated reading cache").unwrap();
        store
            .save_pages(
                &chapter.id,
                &[Page {
                    url: "https://example.test/1.png".into(),
                    local_path: Some(file),
                    ..Page::default()
                }],
            )
            .unwrap();
        assert!(!store.chapter(&chapter.id).unwrap().unwrap().downloaded);
    }

    #[test]
    fn completed_batches_keep_their_state_until_a_verified_repair_is_requested() {
        let store = Store::open(":memory:").unwrap();
        let (book, chapter) = fixture(&store, "network");
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("completed.png");
        image::GrayImage::from_pixel(2, 3, image::Luma([17]))
            .save(&path)
            .unwrap();
        store
            .save_pages(
                &chapter.id,
                &[Page {
                    url: "https://example.test/1.png".into(),
                    local_path: Some(path),
                    ..Page::default()
                }],
            )
            .unwrap();
        store
            .save_download(&DownloadJob {
                chapter_id: chapter.id.clone(),
                book_id: book.id.clone(),
                state: "completed".into(),
                completed: 1,
                total: 1,
                ..DownloadJob::default()
            })
            .unwrap();
        let jobs = store
            .enqueue_download_batch(&book, &[chapter.clone(), chapter.clone()])
            .unwrap();
        assert_eq!(jobs.len(), 1);
        assert_eq!(
            (jobs[0].state.as_str(), jobs[0].completed, jobs[0].total),
            ("completed", 1, 1)
        );
        assert!(store.next_download().unwrap().is_none());
        let repaired = store
            .enqueue_download_batch_with_repairs(
                &book,
                &[chapter.clone()],
                &BTreeSet::from([chapter.id.clone()]),
            )
            .unwrap();
        assert_eq!(repaired[0].state, "queued");
        assert_eq!(repaired[0].completed, 0);
        assert!(!store.chapter(&chapter.id).unwrap().unwrap().downloaded);
    }

    #[test]
    fn download_state_counts_are_grouped_without_loading_job_documents() {
        let store = Store::open(":memory:").unwrap();
        let empty = store.download_state_counts().unwrap();
        assert_eq!(empty.len(), 6);
        assert!(empty.values().all(|count| *count == 0));
        let (book, first) = fixture(&store, "network");
        let chapters = [
            first.clone(),
            Chapter {
                id: "second".into(),
                remote_id: "second".into(),
                ..first.clone()
            },
            Chapter {
                id: "third".into(),
                remote_id: "third".into(),
                ..first
            },
        ];
        store.enqueue_download_batch(&book, &chapters).unwrap();
        for (index, state) in [(1, "paused"), (2, "failed")] {
            let mut job = store.download(&chapters[index].id).unwrap().unwrap();
            job.state = state.into();
            store.save_download(&job).unwrap();
        }
        let counts = store.download_state_counts().unwrap();
        assert_eq!(counts["queued"], 1);
        assert_eq!(counts["paused"], 1);
        assert_eq!(counts["failed"], 1);
        assert_eq!(counts["completed"], 0);
        assert_eq!(
            counts.values().sum::<usize>(),
            store.download_count().unwrap()
        );
    }
}
