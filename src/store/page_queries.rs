//! Indexed manifest windows and incremental cache-reference updates.
//! Persistent triggers keep page totals compatible with older Store writers.

use anyhow::{Context, Result, ensure};
use rusqlite::{Connection, OptionalExtension, Transaction, TransactionBehavior, params};

use super::{Store, decode, encode, local_file_exists};
use crate::domain::{CachedPageUpdate, Page, cached_page_identity};

const MAX_WINDOW: usize = 64;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PageWindow {
    pub offset: usize,
    pub total: usize,
    pub pages: Vec<Page>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CachedPathsUpdateReport {
    pub current: bool,
    pub changed: bool,
    pub affects_downloaded: bool,
}

impl Store {
    pub fn has_pages(&self, chapter_id: &str) -> Result<bool> {
        Ok(self.connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM pages WHERE chapter_id=?1)",
            [chapter_id],
            |row| row.get(0),
        )?)
    }

    /// Fetch at most 64 contiguous positions using the manifest primary key.
    /// The counter and rows share a snapshot when another connection replaces
    /// the manifest. A zero limit reads only the stored total.
    pub fn page_window(&self, chapter_id: &str, offset: usize, limit: usize) -> Result<PageWindow> {
        ensure!(limit <= MAX_WINDOW, "The manifest window exceeds 64 pages");
        if limit == 0 || !self.connection.is_autocommit() {
            return read_window(&self.connection, chapter_id, offset, limit);
        }
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Deferred)?;
        let window = read_window(&transaction, chapter_id, offset, limit)?;
        transaction.commit()?;
        Ok(window)
    }

    /// Publish only changed cache paths for the exact manifest identities that
    /// were fetched. A derivative evicted after rendering is not a stale frame.
    /// Chapter download flags and book unread totals belong to other operations.
    pub fn update_cached_paths(
        &self,
        chapter_id: &str,
        updates: &[CachedPageUpdate],
    ) -> Result<bool> {
        Ok(self
            .update_cached_paths_report(chapter_id, updates)?
            .current)
    }

    pub fn update_cached_paths_report(
        &self,
        chapter_id: &str,
        updates: &[CachedPageUpdate],
    ) -> Result<CachedPathsUpdateReport> {
        let mut report = CachedPathsUpdateReport {
            current: true,
            ..CachedPathsUpdateReport::default()
        };
        if updates.is_empty() {
            return Ok(report);
        }
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        for update in updates {
            let Ok(position) = i64::try_from(update.index) else {
                report.current = false;
                continue;
            };
            let document = transaction
                .query_row(
                    "SELECT document FROM pages WHERE chapter_id=?1 AND position=?2",
                    params![chapter_id, position],
                    |row| row.get::<_, String>(0),
                )
                .optional()?;
            let Some(document) = document else {
                report.current = false;
                continue;
            };
            let mut page: Page = decode(document)?;
            if cached_page_identity(&page, update.index) != update.identity {
                report.current = false;
                continue;
            }
            if page.local_path.as_deref() == Some(update.path.as_path())
                || !local_file_exists(&update.path)
            {
                continue;
            }
            page.local_path = Some(update.path.clone());
            report.changed |= transaction.execute(
                "UPDATE pages SET document=?1 WHERE chapter_id=?2 AND position=?3",
                params![encode(&page)?, chapter_id, position],
            )? > 0;
        }
        if report.changed {
            report.affects_downloaded = transaction.query_row(
                "SELECT COALESCE(json_extract(document,'$.downloaded'),0) FROM chapters WHERE id=?1",
                [chapter_id], |row| row.get::<_, bool>(0),
            ).optional()?.unwrap_or(false);
        }
        transaction.commit()?;
        Ok(report)
    }
}

fn read_window(
    connection: &Connection,
    chapter_id: &str,
    offset: usize,
    limit: usize,
) -> Result<PageWindow> {
    let total = connection
        .query_row(
            "SELECT total FROM chapter_page_stats WHERE chapter_id=?1",
            [chapter_id],
            |row| row.get::<_, u32>(0),
        )
        .optional()?
        .unwrap_or(0) as usize;
    let mut window = PageWindow {
        offset,
        total,
        pages: Vec::new(),
    };
    if limit == 0 || offset >= total {
        return Ok(window);
    }
    let expected = limit.min(total - offset);
    let end = offset + expected;
    let mut statement = connection.prepare(
        "SELECT position,document FROM pages WHERE chapter_id=?1 AND position>=?2 AND position<?3 ORDER BY position LIMIT ?4",
    )?;
    let rows = statement.query_map(
        params![chapter_id, offset as u64, end as u64, expected as u64],
        |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?)),
    )?;
    for row in rows {
        let (position, document) = row?;
        ensure!(
            position == (offset + window.pages.len()) as i64,
            "The manifest has a gap or reordered page positions"
        );
        window.pages.push(decode(document)?);
    }
    ensure!(
        window.pages.len() == expected,
        "The manifest window does not match its stored page total"
    );
    Ok(window)
}

pub(super) fn mapped_count(connection: &Connection, chapter_id: &str) -> Result<u32> {
    Ok(connection
        .query_row(
            "SELECT mapped FROM chapter_page_stats WHERE chapter_id=?1",
            [chapter_id],
            |row| row.get(0),
        )
        .optional()?
        .unwrap_or(0))
}

fn counters_ready(connection: &Connection) -> Result<bool> {
    Ok(connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_schema WHERE type='table' AND name='chapter_page_stats')
         AND (SELECT COUNT(*) FROM sqlite_schema WHERE type='trigger' AND tbl_name='pages' AND name IN(
             'chapter_page_stats_insert_v1','chapter_page_stats_delete_v1','chapter_page_stats_update_v1'))=3",
        [], |row| row.get(0),
    )?)
}

/// Backfill once, atomically publishing all three triggers as the version marker.
/// A second initializer rechecks after acquiring the SQLite writer lock.
pub(super) fn ensure_page_counters(connection: &Connection) -> Result<()> {
    if counters_ready(connection)? {
        return Ok(());
    }
    let transaction = Transaction::new_unchecked(connection, TransactionBehavior::Immediate)?;
    if counters_ready(&transaction)? {
        return Ok(());
    }
    transaction.execute_batch(
        "DROP TRIGGER IF EXISTS chapter_page_stats_insert_v1;
         DROP TRIGGER IF EXISTS chapter_page_stats_delete_v1;
         DROP TRIGGER IF EXISTS chapter_page_stats_update_v1;
         CREATE TABLE IF NOT EXISTS chapter_page_stats (
             chapter_id TEXT NOT NULL PRIMARY KEY REFERENCES chapters(id) ON DELETE CASCADE,
             total INTEGER NOT NULL CHECK(total>=0),
             mapped INTEGER NOT NULL CHECK(mapped>=0 AND mapped<=total)
         );
         DELETE FROM chapter_page_stats;
         INSERT INTO chapter_page_stats(chapter_id,total,mapped)
             SELECT chapter_id,COUNT(*),SUM(json_extract(document,'$.local_path') IS NOT NULL)
             FROM pages GROUP BY chapter_id;
         CREATE TRIGGER chapter_page_stats_insert_v1 AFTER INSERT ON pages BEGIN
             INSERT INTO chapter_page_stats(chapter_id,total,mapped)
                 VALUES(NEW.chapter_id,1,json_extract(NEW.document,'$.local_path') IS NOT NULL)
                 ON CONFLICT(chapter_id) DO UPDATE SET total=total+1,mapped=mapped+excluded.mapped;
         END;
         CREATE TRIGGER chapter_page_stats_delete_v1 AFTER DELETE ON pages BEGIN
             UPDATE chapter_page_stats SET total=total-1,
                 mapped=mapped-(json_extract(OLD.document,'$.local_path') IS NOT NULL)
                 WHERE chapter_id=OLD.chapter_id;
         END;
         CREATE TRIGGER chapter_page_stats_update_v1 AFTER UPDATE OF chapter_id,document ON pages
         WHEN OLD.chapter_id<>NEW.chapter_id OR
              (json_extract(OLD.document,'$.local_path') IS NOT NULL)<>(json_extract(NEW.document,'$.local_path') IS NOT NULL)
         BEGIN
             UPDATE chapter_page_stats SET mapped=mapped+
                 (json_extract(NEW.document,'$.local_path') IS NOT NULL)-(json_extract(OLD.document,'$.local_path') IS NOT NULL)
                 WHERE chapter_id=NEW.chapter_id AND OLD.chapter_id=NEW.chapter_id;
             UPDATE chapter_page_stats SET total=total-1,
                 mapped=mapped-(json_extract(OLD.document,'$.local_path') IS NOT NULL)
                 WHERE chapter_id=OLD.chapter_id AND OLD.chapter_id<>NEW.chapter_id;
             INSERT INTO chapter_page_stats(chapter_id,total,mapped)
                 SELECT NEW.chapter_id,1,json_extract(NEW.document,'$.local_path') IS NOT NULL
                 WHERE OLD.chapter_id<>NEW.chapter_id
                 ON CONFLICT(chapter_id) DO UPDATE SET total=total+1,mapped=mapped+excluded.mapped;
         END;",
    ).context("Migrate the manifest page counters")?;
    transaction.commit()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::{
        path::{Path, PathBuf},
        sync::{Arc, Barrier},
    };

    use super::*;
    use crate::domain::{Book, Chapter};

    fn legacy(path: &Path) -> Connection {
        let connection = Connection::open(path).unwrap();
        connection
            .busy_timeout(std::time::Duration::from_secs(10))
            .unwrap();
        connection
            .pragma_update(None, "foreign_keys", "ON")
            .unwrap();
        connection
            .pragma_update(None, "recursive_triggers", "OFF")
            .unwrap();
        connection.execute_batch(
            "CREATE TABLE IF NOT EXISTS chapters(id TEXT NOT NULL PRIMARY KEY);
             CREATE TABLE IF NOT EXISTS pages(chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                 position INTEGER NOT NULL,document TEXT NOT NULL,PRIMARY KEY(chapter_id,position));
             INSERT OR IGNORE INTO chapters(id) VALUES('a'),('b');",
        ).unwrap();
        connection
    }

    fn page(index: usize, mapped: bool) -> Page {
        Page {
            url: format!("https://example.test/{index}.png"),
            local_path: mapped.then(|| PathBuf::from("legacy-reference.png")),
            ..Page::default()
        }
    }

    fn insert(connection: &Connection, chapter: &str, position: usize, value: &Page) {
        connection
            .execute(
                "INSERT INTO pages(chapter_id,position,document) VALUES(?1,?2,?3)",
                params![chapter, position as u64, encode(value).unwrap()],
            )
            .unwrap();
    }

    fn stats(connection: &Connection, chapter: &str) -> (u32, u32) {
        connection
            .query_row(
                "SELECT total,mapped FROM chapter_page_stats WHERE chapter_id=?1",
                [chapter],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()
            .unwrap()
            .unwrap_or((0, 0))
    }

    fn changes(connection: &Connection) -> u64 {
        connection
            .query_row("SELECT total_changes()", [], |row| row.get(0))
            .unwrap()
    }

    #[test]
    fn migration_backfills_a_legacy_manifest_once() {
        let connection = legacy(Path::new(":memory:"));
        insert(&connection, "a", 0, &page(0, false));
        insert(&connection, "a", 1, &page(1, true));
        ensure_page_counters(&connection).unwrap();
        assert_eq!(stats(&connection, "a"), (2, 1));
        assert_eq!(mapped_count(&connection, "b").unwrap(), 0);
        let before = changes(&connection);
        ensure_page_counters(&connection).unwrap();
        assert_eq!(changes(&connection), before);
        assert!(counters_ready(&connection).unwrap());
    }

    #[test]
    fn an_incomplete_trigger_marker_rebuilds_counts_before_becoming_ready() {
        let connection = legacy(Path::new(":memory:"));
        insert(&connection, "a", 0, &page(0, false));
        ensure_page_counters(&connection).unwrap();
        connection
            .execute_batch("DROP TRIGGER chapter_page_stats_insert_v1")
            .unwrap();
        insert(&connection, "a", 1, &page(1, true));
        assert_eq!(stats(&connection, "a"), (1, 0));
        ensure_page_counters(&connection).unwrap();
        assert_eq!(stats(&connection, "a"), (2, 1));
        insert(&connection, "a", 2, &page(2, false));
        assert_eq!(stats(&connection, "a"), (3, 1));
        assert!(counters_ready(&connection).unwrap());
    }

    #[test]
    fn two_connections_initialize_the_same_counter_version_without_double_counting() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("legacy.sqlite3");
        let connection = legacy(&path);
        for index in 0..40 {
            insert(&connection, "a", index, &page(index, index % 2 == 0));
        }
        drop(connection);
        let ready = Arc::new(Barrier::new(3));
        let mut threads = Vec::new();
        for _ in 0..2 {
            let path = path.clone();
            let ready = ready.clone();
            threads.push(std::thread::spawn(move || {
                let connection = Connection::open(path).unwrap();
                connection
                    .busy_timeout(std::time::Duration::from_secs(10))
                    .unwrap();
                connection
                    .pragma_update(None, "foreign_keys", "ON")
                    .unwrap();
                ready.wait();
                ensure_page_counters(&connection).unwrap();
                stats(&connection, "a")
            }));
        }
        ready.wait();
        for thread in threads {
            assert_eq!(thread.join().unwrap(), (40, 20));
        }
        let connection = Connection::open(path).unwrap();
        assert_eq!(stats(&connection, "a"), (40, 20));
    }

    #[test]
    fn an_already_open_legacy_writer_maintains_counts_after_migration() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("legacy.sqlite3");
        let writer = legacy(&path);
        insert(&writer, "a", 0, &page(0, false));
        let migration = Connection::open(&path).unwrap();
        ensure_page_counters(&migration).unwrap();
        insert(&writer, "a", 1, &page(1, true));
        assert_eq!(stats(&writer, "a"), (2, 1));
        writer
            .execute(
                "UPDATE pages SET document=?1 WHERE chapter_id='a' AND position=0",
                [encode(&page(0, true)).unwrap()],
            )
            .unwrap();
        assert_eq!(stats(&writer, "a"), (2, 2));
        writer
            .execute(
                "UPDATE pages SET chapter_id='b',position=0 WHERE chapter_id='a' AND position=1",
                [],
            )
            .unwrap();
        assert_eq!(stats(&writer, "a"), (1, 1));
        assert_eq!(stats(&writer, "b"), (1, 1));
        writer
            .execute("DELETE FROM pages WHERE chapter_id='a'", [])
            .unwrap();
        assert_eq!(stats(&writer, "a"), (0, 0));
        insert(&writer, "a", 0, &page(0, false));
        assert_eq!(stats(&writer, "a"), (1, 0));
    }

    #[test]
    fn counters_and_pages_roll_back_together() {
        let connection = legacy(Path::new(":memory:"));
        insert(&connection, "a", 0, &page(0, false));
        insert(&connection, "a", 1, &page(1, true));
        ensure_page_counters(&connection).unwrap();
        let transaction =
            Transaction::new_unchecked(&connection, TransactionBehavior::Immediate).unwrap();
        insert(&transaction, "a", 2, &page(2, true));
        transaction
            .execute(
                "UPDATE pages SET chapter_id='b',position=0 WHERE chapter_id='a' AND position=1",
                [],
            )
            .unwrap();
        transaction
            .execute("DELETE FROM pages WHERE chapter_id='a' AND position=0", [])
            .unwrap();
        assert_eq!(stats(&transaction, "a"), (1, 1));
        assert_eq!(stats(&transaction, "b"), (1, 1));
        transaction.rollback().unwrap();
        assert_eq!(stats(&connection, "a"), (2, 1));
        assert_eq!(stats(&connection, "b"), (0, 0));
    }

    #[test]
    fn recursive_replace_and_update_replace_keep_row_contributions_correct() {
        let connection = legacy(Path::new(":memory:"));
        ensure_page_counters(&connection).unwrap();
        connection
            .pragma_update(None, "recursive_triggers", "ON")
            .unwrap();
        insert(&connection, "a", 0, &page(0, false));
        insert(&connection, "a", 1, &page(1, true));
        insert(&connection, "b", 0, &page(2, true));
        connection
            .execute(
                "INSERT OR REPLACE INTO pages(chapter_id,position,document) VALUES('a',0,?1)",
                [encode(&page(0, true)).unwrap()],
            )
            .unwrap();
        assert_eq!(stats(&connection, "a"), (2, 2));
        connection.execute("UPDATE OR REPLACE pages SET position=1,document=?1 WHERE chapter_id='a' AND position=0",[encode(&page(0,false)).unwrap()]).unwrap();
        assert_eq!(stats(&connection, "a"), (1, 0));
        connection.execute("UPDATE OR REPLACE pages SET chapter_id='b',position=0 WHERE chapter_id='a' AND position=1",[]).unwrap();
        assert_eq!(stats(&connection, "a"), (0, 0));
        assert_eq!(stats(&connection, "b"), (1, 0));
        connection
            .execute("DELETE FROM chapters WHERE id='b'", [])
            .unwrap();
        assert_eq!(mapped_count(&connection, "b").unwrap(), 0);
        assert_eq!(
            connection
                .query_row(
                    "SELECT COUNT(*) FROM chapter_page_stats WHERE chapter_id='b'",
                    [],
                    |row| row.get::<_, u32>(0)
                )
                .unwrap(),
            0
        );
    }

    #[test]
    fn windows_decode_only_the_requested_indexed_range() {
        let connection = legacy(Path::new(":memory:"));
        let transaction =
            Transaction::new_unchecked(&connection, TransactionBehavior::Immediate).unwrap();
        for index in 0..2_000 {
            insert(&transaction, "a", index, &page(index, false));
        }
        transaction.commit().unwrap();
        ensure_page_counters(&connection).unwrap();
        connection.execute("UPDATE pages SET document='\"not a Page document\"' WHERE chapter_id='a' AND position=1999",[]).unwrap();
        let store = Store { connection };
        let before = changes(&store.connection);
        let window = store.page_window("a", 1_000, 3).unwrap();
        assert_eq!(
            (window.offset, window.total, window.pages.len()),
            (1_000, 2_000, 3)
        );
        assert_eq!(
            window.pages,
            vec![page(1_000, false), page(1_001, false), page(1_002, false)]
        );
        assert!(store.has_pages("a").unwrap());
        assert!(!store.has_pages("b").unwrap());
        assert_eq!(store.page_window("a", 0, 0).unwrap().total, 2_000);
        assert!(
            store
                .page_window("a", usize::MAX, 64)
                .unwrap()
                .pages
                .is_empty()
        );
        assert!(store.page_window("a", 0, 65).is_err());
        assert!(store.page_window("a", 1_999, 1).is_err());
        assert_eq!(changes(&store.connection), before);
    }

    #[test]
    fn gaps_fail_instead_of_shifting_the_visible_page() {
        let connection = legacy(Path::new(":memory:"));
        insert(&connection, "a", 0, &page(0, false));
        insert(&connection, "a", 2, &page(2, false));
        ensure_page_counters(&connection).unwrap();
        let store = Store { connection };
        assert!(store.page_window("a", 0, 2).is_err());
        assert!(store.page_window("a", 1, 1).is_err());
        assert_eq!(
            store.page_window("b", 0, 64).unwrap(),
            PageWindow::default()
        );
    }

    fn real_store() -> Store {
        let store = Store::open(":memory:").unwrap();
        store
            .upsert_book(&Book {
                id: "book".into(),
                source_id: "fixture".into(),
                remote_id: "book".into(),
                title: "Book".into(),
                ..Book::default()
            })
            .unwrap();
        store
            .save_chapters(
                "book",
                &[Chapter {
                    id: "chapter".into(),
                    book_id: "book".into(),
                    source_id: "fixture".into(),
                    remote_id: "chapter".into(),
                    number: 1.0,
                    ..Chapter::default()
                }],
            )
            .unwrap();
        store
            .save_pages(
                "chapter",
                &(0..4).map(|index| page(index, false)).collect::<Vec<_>>(),
            )
            .unwrap();
        store
    }

    #[test]
    fn cached_path_updates_are_incremental_and_preserve_book_and_download_flags() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("cached.png");
        std::fs::write(&path, b"cached image bytes").unwrap();
        let store = real_store();
        let before_book = store.book("book").unwrap();
        let before_chapter = store.chapter("chapter").unwrap();
        let update = CachedPageUpdate {
            index: 1,
            identity: cached_page_identity(&page(1, false), 1),
            path: path.clone(),
        };
        assert_eq!(
            store
                .update_cached_paths_report("chapter", &[update.clone()])
                .unwrap(),
            CachedPathsUpdateReport {
                current: true,
                changed: true,
                affects_downloaded: false
            }
        );
        assert_eq!(mapped_count(&store.connection, "chapter").unwrap(), 1);
        assert_eq!(store.book("book").unwrap(), before_book);
        assert_eq!(store.chapter("chapter").unwrap(), before_chapter);
        let before = changes(&store.connection);
        assert_eq!(
            store
                .update_cached_paths_report("chapter", &[update])
                .unwrap(),
            CachedPathsUpdateReport {
                current: true,
                changed: false,
                affects_downloaded: false
            }
        );
        assert_eq!(changes(&store.connection), before);
        assert_eq!(
            store.page_window("chapter", 1, 1).unwrap().pages[0].local_path,
            Some(path.clone())
        );
        store.remove_book("book").unwrap();
        assert_eq!(mapped_count(&store.connection, "chapter").unwrap(), 0);
        assert!(path.exists());
    }

    #[test]
    fn only_a_changed_path_in_a_downloaded_chapter_invalidates_download_usage() {
        let directory = tempfile::tempdir().unwrap();
        let first = directory.path().join("first.png");
        let second = directory.path().join("second.png");
        std::fs::write(&first, b"first cached image").unwrap();
        std::fs::write(&second, b"second cached image").unwrap();
        let store = real_store();
        let updates = (0..4)
            .map(|index| CachedPageUpdate {
                index,
                identity: cached_page_identity(&page(index, false), index),
                path: first.clone(),
            })
            .collect::<Vec<_>>();
        store.update_cached_paths("chapter", &updates).unwrap();
        store.set_chapter_downloaded("chapter", true).unwrap();
        let before = store.chapter("chapter").unwrap();
        let update = CachedPageUpdate {
            index: 2,
            identity: cached_page_identity(&page(2, false), 2),
            path: second.clone(),
        };
        assert_eq!(
            store
                .update_cached_paths_report("chapter", &[update.clone()])
                .unwrap(),
            CachedPathsUpdateReport {
                current: true,
                changed: true,
                affects_downloaded: true
            }
        );
        assert_eq!(store.chapter("chapter").unwrap(), before);
        let before = changes(&store.connection);
        assert_eq!(
            store
                .update_cached_paths_report("chapter", &[update])
                .unwrap(),
            CachedPathsUpdateReport {
                current: true,
                changed: false,
                affects_downloaded: false
            }
        );
        assert_eq!(changes(&store.connection), before);
        store
            .upsert_book(&Book {
                id: "local-book".into(),
                source_id: "local".into(),
                remote_id: "local-book".into(),
                ..Book::default()
            })
            .unwrap();
        store
            .save_chapters(
                "local-book",
                &[Chapter {
                    id: "local-chapter".into(),
                    book_id: "local-book".into(),
                    source_id: "local".into(),
                    remote_id: "local-chapter".into(),
                    ..Chapter::default()
                }],
            )
            .unwrap();
        let local = Page {
            local_path: Some(first),
            ..Page::default()
        };
        store.save_pages("local-chapter", &[local.clone()]).unwrap();
        let update = CachedPageUpdate {
            index: 0,
            identity: cached_page_identity(&local, 0),
            path: second,
        };
        assert_eq!(
            store
                .update_cached_paths_report("local-chapter", &[update])
                .unwrap(),
            CachedPathsUpdateReport {
                current: true,
                changed: true,
                affects_downloaded: true
            }
        );
    }

    #[test]
    fn stale_missing_and_evicted_updates_have_distinct_outcomes() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("cached.png");
        std::fs::write(&path, b"cached image bytes").unwrap();
        let store = real_store();
        let updates = [
            CachedPageUpdate {
                index: 0,
                identity: cached_page_identity(&page(0, false), 0),
                path: path.clone(),
            },
            CachedPageUpdate {
                index: 1,
                identity: "stale manifest identity".into(),
                path: path.clone(),
            },
            CachedPageUpdate {
                index: 20,
                identity: cached_page_identity(&page(20, false), 20),
                path: path.clone(),
            },
        ];
        assert!(!store.update_cached_paths("chapter", &updates).unwrap());
        assert_eq!(mapped_count(&store.connection, "chapter").unwrap(), 1);
        assert!(
            store.page_window("chapter", 1, 1).unwrap().pages[0]
                .local_path
                .is_none()
        );
        let missing = directory.path().join("already-evicted.png");
        let evicted = CachedPageUpdate {
            index: 2,
            identity: cached_page_identity(&page(2, false), 2),
            path: missing,
        };
        let before = changes(&store.connection);
        assert!(store.update_cached_paths("chapter", &[evicted]).unwrap());
        assert_eq!(changes(&store.connection), before);
        assert_eq!(mapped_count(&store.connection, "chapter").unwrap(), 1);
    }
}
