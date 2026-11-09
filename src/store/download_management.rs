//! Scalar queue inspection and guarded metadata removal for one book.

use anyhow::{Context, Result, ensure};
use rusqlite::{Connection, OptionalExtension, Transaction, TransactionBehavior, params};

use super::{Store, query_document};
use crate::domain::DownloadJob;

#[derive(Debug, Clone)]
pub struct BookDownloadCandidate {
    pub chapter_id: String,
    pub source_id: String,
    pub downloaded: bool,
    pub job_state: Option<String>,
    pub job_book_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BookDownloadRemovalKind {
    Noop,
    Removed,
    Cancelled,
    RetainedLocal,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BookDownloadRemoval {
    pub kind: BookDownloadRemovalKind,
    pub usage_changed: bool,
}

impl Store {
    /// Include terminal jobs so their durable cache owners can be released.
    pub fn book_download_count(&self, book_id: &str) -> Result<usize> {
        let count: u64 = self.connection.query_row(
            "SELECT count(*) FROM chapters c
             LEFT JOIN downloads d ON d.chapter_id = c.id
             WHERE c.book_id = ?1 AND (d.chapter_id IS NOT NULL OR
                 (c.source_id <> 'local' AND json_extract(c.document, '$.downloaded') = 1))",
            [book_id],
            |row| row.get(0),
        )?;
        usize::try_from(count).context("The book download count exceeds this device's range")
    }

    /// Read a bounded keyset without decoding chapter or download documents.
    pub fn book_download_candidates(
        &self,
        book_id: &str,
        after_id: Option<&str>,
        limit: usize,
    ) -> Result<Vec<BookDownloadCandidate>> {
        ensure!(
            limit <= 128,
            "The book download page size exceeds its limit"
        );
        let mut statement = self.connection.prepare(
            "SELECT c.id, c.source_id,
                 COALESCE(json_extract(c.document, '$.downloaded') = 1, 0),
                 d.state, d.book_id
             FROM chapters c LEFT JOIN downloads d ON d.chapter_id = c.id
             WHERE c.book_id = ?1 AND c.id COLLATE BINARY > ?2
                 AND (d.chapter_id IS NOT NULL OR
                     (c.source_id <> 'local' AND json_extract(c.document, '$.downloaded') = 1))
             ORDER BY c.id COLLATE BINARY LIMIT ?3",
        )?;
        let candidates = statement
            .query_map(
                params![book_id, after_id.unwrap_or(""), limit as i64],
                read_candidate,
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(candidates)
    }

    /// Recheck both identities immediately before acting on a queued candidate.
    pub fn book_download_candidate(
        &self,
        book_id: &str,
        chapter_id: &str,
    ) -> Result<Option<BookDownloadCandidate>> {
        guarded_candidate(&self.connection, book_id, chapter_id)
    }

    pub fn next_download_excluding_books(
        &self,
        book_ids: &[String],
    ) -> Result<Option<DownloadJob>> {
        if book_ids.is_empty() {
            return self.next_download();
        }
        let placeholders = std::iter::repeat_n("?", book_ids.len())
            .collect::<Vec<_>>()
            .join(",");
        let sql = format!(
            "SELECT document FROM downloads WHERE state = 'queued'
             AND book_id NOT IN ({placeholders}) ORDER BY rowid LIMIT 1"
        );
        query_document(
            &self.connection,
            &sql,
            rusqlite::params_from_iter(book_ids.iter()),
        )
    }

    /// Publish metadata removal only after the actor has protected shared files.
    pub fn finish_book_download_removal(
        &self,
        book_id: &str,
        chapter_id: &str,
    ) -> Result<BookDownloadRemoval> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let Some(candidate) = guarded_candidate(&transaction, book_id, chapter_id)? else {
            transaction.commit()?;
            return Ok(BookDownloadRemoval {
                kind: BookDownloadRemovalKind::Noop,
                usage_changed: false,
            });
        };
        let local = candidate.source_id == "local";
        let kind = match candidate.job_state.as_deref() {
            Some(state @ ("completed" | "cancelled")) => {
                let deleted = transaction.execute(
                    "DELETE FROM downloads WHERE chapter_id = ?1 AND book_id = ?2
                     AND state IN ('completed', 'cancelled')",
                    params![chapter_id, book_id],
                )?;
                ensure!(
                    deleted == 1,
                    "The download changed before its removal committed"
                );
                if local {
                    BookDownloadRemovalKind::RetainedLocal
                } else if state == "completed" {
                    BookDownloadRemovalKind::Removed
                } else {
                    BookDownloadRemovalKind::Cancelled
                }
            }
            Some(_) => anyhow::bail!("Cancel an unfinished download before removing its metadata"),
            None if !local && candidate.downloaded => BookDownloadRemovalKind::Removed,
            None => BookDownloadRemovalKind::Noop,
        };
        let usage_changed = !local && candidate.downloaded;
        if usage_changed {
            let updated = transaction.execute(
                "UPDATE chapters SET document = json_set(document, '$.downloaded', json('false'))
                 WHERE id = ?1 AND book_id = ?2 AND source_id <> 'local'
                     AND json_extract(document, '$.downloaded') = 1",
                params![chapter_id, book_id],
            )?;
            ensure!(
                updated == 1,
                "The downloaded chapter changed before its removal committed"
            );
        }
        transaction.commit()?;
        Ok(BookDownloadRemoval {
            kind,
            usage_changed,
        })
    }
}

fn read_candidate(row: &rusqlite::Row<'_>) -> rusqlite::Result<BookDownloadCandidate> {
    Ok(BookDownloadCandidate {
        chapter_id: row.get(0)?,
        source_id: row.get(1)?,
        downloaded: row.get(2)?,
        job_state: row.get(3)?,
        job_book_id: row.get(4)?,
    })
}

fn guarded_candidate(
    connection: &Connection,
    book_id: &str,
    chapter_id: &str,
) -> Result<Option<BookDownloadCandidate>> {
    let row = connection
        .query_row(
            "SELECT c.id, c.source_id,
                 COALESCE(json_extract(c.document, '$.downloaded') = 1, 0),
                 d.state, d.book_id, c.book_id
             FROM chapters c LEFT JOIN downloads d ON d.chapter_id = c.id
             WHERE c.id = ?1",
            [chapter_id],
            |row| Ok((read_candidate(row)?, row.get::<_, String>(5)?)),
        )
        .optional()?;
    let Some((candidate, chapter_book_id)) = row else {
        return Ok(None);
    };
    ensure!(
        chapter_book_id == book_id,
        "The chapter belongs to a different book"
    );
    ensure!(
        candidate
            .job_book_id
            .as_deref()
            .is_none_or(|id| id == book_id),
        "The download belongs to a different book"
    );
    Ok(Some(candidate))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::Book;

    fn fixture() -> Store {
        let store = Store::open(":memory:").unwrap();
        for id in ["book", "other"] {
            store
                .upsert_book(&Book {
                    id: id.into(),
                    source_id: "network".into(),
                    remote_id: id.into(),
                    title: id.into(),
                    ..Book::default()
                })
                .unwrap();
        }
        store
    }

    fn chapter(store: &Store, id: &str, book_id: &str, source: &str, downloaded: bool) {
        // These documents deliberately lack the fields needed to decode Chapter.
        let document = serde_json::json!({"downloaded": downloaded, "read": false}).to_string();
        store
            .connection
            .execute(
                "INSERT INTO chapters(id, book_id, source_id, remote_id, number, document)
             VALUES(?1, ?2, ?3, ?1, 1, ?4)",
                params![id, book_id, source, document],
            )
            .unwrap();
    }

    fn job(store: &Store, id: &str, book_id: &str, state: &str) {
        let document = serde_json::to_string(&DownloadJob {
            chapter_id: id.into(),
            book_id: book_id.into(),
            state: state.into(),
            total: 1,
            completed: u32::from(state == "completed"),
            error: String::new(),
        })
        .unwrap();
        store.connection.execute(
            "INSERT INTO downloads(chapter_id, book_id, state, document) VALUES(?1, ?2, ?3, ?4)",
            params![id, book_id, state, document],
        ).unwrap();
    }

    #[test]
    fn scalar_count_and_binary_keysets_include_terminal_jobs_without_decoding_documents() {
        let store = fixture();
        let mut expected = Vec::new();
        for (index, state) in [
            "queued",
            "downloading",
            "paused",
            "failed",
            "completed",
            "cancelled",
        ]
        .into_iter()
        .enumerate()
        {
            let id = format!("job-{index:03}");
            chapter(&store, &id, "book", "network", state == "completed");
            job(&store, &id, "book", state);
            expected.push(id);
        }
        chapter(&store, "local-job", "book", "local", true);
        job(&store, "local-job", "book", "completed");
        expected.push("local-job".into());
        for id in ["legacy", "A", "a"] {
            chapter(&store, id, "book", "network", true);
            expected.push(id.into());
        }
        for index in 0..130 {
            let id = format!("extra-{index:03}");
            chapter(&store, &id, "book", "network", true);
            expected.push(id);
        }
        chapter(&store, "local-original", "book", "local", true);
        chapter(&store, "uncached", "book", "network", false);
        chapter(&store, "other-job", "other", "network", false);
        job(&store, "other-job", "other", "queued");
        assert_eq!(
            store
                .next_download_excluding_books(&[])
                .unwrap()
                .unwrap()
                .chapter_id,
            "job-000"
        );
        assert_eq!(
            store
                .next_download_excluding_books(&["book".into()])
                .unwrap()
                .unwrap()
                .chapter_id,
            "other-job"
        );
        assert!(
            store
                .next_download_excluding_books(&["book".into(), "other".into()])
                .unwrap()
                .is_none()
        );

        // Scalar inventory must not deserialize download documents either.
        store
            .connection
            .execute("UPDATE downloads SET document = '{}'", [])
            .unwrap();
        expected.sort();
        assert_eq!(store.book_download_count("book").unwrap(), expected.len());
        assert_eq!(store.book_download_count("other").unwrap(), 1);
        let first = store.book_download_candidates("book", None, 128).unwrap();
        assert_eq!(first.len(), 128);
        let second = store
            .book_download_candidates("book", Some(&first.last().unwrap().chapter_id), 128)
            .unwrap();
        assert!(!second.is_empty());
        assert!(
            store
                .book_download_candidates("book", Some(&second.last().unwrap().chapter_id), 128)
                .unwrap()
                .is_empty()
        );
        let actual = first
            .into_iter()
            .chain(second)
            .map(|entry| entry.chapter_id)
            .collect::<Vec<_>>();
        assert_eq!(actual, expected);
        assert!(store.book_download_candidates("book", None, 129).is_err());
        assert!(
            store
                .book_download_candidates("book", None, 0)
                .unwrap()
                .is_empty()
        );
        assert!(
            store
                .book_download_candidate("book", "local-original")
                .unwrap()
                .unwrap()
                .downloaded
        );
    }

    #[test]
    fn live_guards_and_unfinished_states_reject_removal_without_writes() {
        let store = fixture();
        chapter(&store, "foreign-chapter", "other", "network", true);
        job(&store, "foreign-chapter", "other", "cancelled");
        assert!(
            store
                .book_download_candidate("book", "foreign-chapter")
                .is_err()
        );
        assert!(
            store
                .finish_book_download_removal("book", "foreign-chapter")
                .is_err()
        );
        chapter(&store, "foreign-job", "book", "network", true);
        job(&store, "foreign-job", "other", "completed");
        assert!(
            store
                .book_download_candidate("book", "foreign-job")
                .is_err()
        );
        assert!(
            store
                .finish_book_download_removal("book", "foreign-job")
                .is_err()
        );
        for state in ["queued", "downloading", "paused", "failed"] {
            chapter(&store, state, "book", "network", true);
            job(&store, state, "book", state);
            assert!(store.finish_book_download_removal("book", state).is_err());
            let candidate = store
                .book_download_candidate("book", state)
                .unwrap()
                .unwrap();
            assert_eq!(candidate.job_state.as_deref(), Some(state));
            assert!(candidate.downloaded);
        }
        let retained: usize = store
            .connection
            .query_row("SELECT count(*) FROM downloads", [], |row| row.get(0))
            .unwrap();
        assert_eq!(retained, 6);
        assert!(
            store
                .book_download_candidate("book", "missing")
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn removal_preserves_local_manifests_and_rolls_back_cancelled_job_cleanup_atomically() {
        let store = fixture();
        for (id, source, downloaded, state, kind, usage_changed) in [
            (
                "completed",
                "network",
                true,
                Some("completed"),
                BookDownloadRemovalKind::Removed,
                true,
            ),
            (
                "cancelled",
                "network",
                false,
                Some("cancelled"),
                BookDownloadRemovalKind::Cancelled,
                false,
            ),
            (
                "local-completed",
                "local",
                true,
                Some("completed"),
                BookDownloadRemovalKind::RetainedLocal,
                false,
            ),
            (
                "local-cancelled",
                "local",
                true,
                Some("cancelled"),
                BookDownloadRemovalKind::RetainedLocal,
                false,
            ),
            (
                "legacy",
                "network",
                true,
                None,
                BookDownloadRemovalKind::Removed,
                true,
            ),
            (
                "local-original",
                "local",
                true,
                None,
                BookDownloadRemovalKind::Noop,
                false,
            ),
            (
                "uncached",
                "network",
                false,
                None,
                BookDownloadRemovalKind::Noop,
                false,
            ),
        ] {
            chapter(&store, id, "book", source, downloaded);
            if let Some(state) = state {
                job(&store, id, "book", state);
            }
            let original_page = r#"{"original":"page"}"#;
            store
                .connection
                .execute(
                    "INSERT INTO pages(chapter_id, position, document) VALUES(?1, 0, ?2)",
                    params![id, original_page],
                )
                .unwrap();
            let removed = store.finish_book_download_removal("book", id).unwrap();
            assert_eq!(
                removed,
                BookDownloadRemoval {
                    kind,
                    usage_changed
                }
            );
            let candidate = store.book_download_candidate("book", id).unwrap().unwrap();
            assert!(candidate.job_state.is_none());
            assert_eq!(candidate.downloaded, source == "local" && downloaded);
            let flag_type: String = store
                .connection
                .query_row(
                    "SELECT json_type(document, '$.downloaded') FROM chapters WHERE id=?1",
                    [id],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(
                flag_type,
                if candidate.downloaded {
                    "true"
                } else {
                    "false"
                }
            );
            let page: String = store
                .connection
                .query_row(
                    "SELECT document FROM pages WHERE chapter_id=?1",
                    [id],
                    |row| row.get(0),
                )
                .unwrap();
            assert_eq!(page, original_page);
        }

        chapter(&store, "rollback", "book", "network", true);
        job(&store, "rollback", "book", "cancelled");
        store
            .connection
            .execute(
                "INSERT INTO download_sleep_ownership(chapter_id) VALUES('rollback')",
                [],
            )
            .unwrap();
        store.connection.execute_batch("CREATE TRIGGER reject_book_download_removal BEFORE UPDATE OF document ON chapters WHEN OLD.id='rollback' BEGIN SELECT RAISE(ABORT, 'fixture metadata removal rejected'); END;").unwrap();
        assert!(
            store
                .finish_book_download_removal("book", "rollback")
                .is_err()
        );
        let retained = store
            .book_download_candidate("book", "rollback")
            .unwrap()
            .unwrap();
        assert_eq!(retained.job_state.as_deref(), Some("cancelled"));
        assert!(retained.downloaded);
        let owners: usize = store
            .connection
            .query_row(
                "SELECT count(*) FROM download_sleep_ownership WHERE chapter_id='rollback'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(owners, 1);
        store
            .connection
            .execute_batch("DROP TRIGGER reject_book_download_removal")
            .unwrap();
        assert_eq!(
            store
                .finish_book_download_removal("book", "rollback")
                .unwrap(),
            BookDownloadRemoval {
                kind: BookDownloadRemovalKind::Cancelled,
                usage_changed: true
            }
        );
        let owners: usize = store
            .connection
            .query_row(
                "SELECT count(*) FROM download_sleep_ownership WHERE chapter_id='rollback'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(owners, 0);
        assert_eq!(
            store
                .finish_book_download_removal("book", "missing")
                .unwrap()
                .kind,
            BookDownloadRemovalKind::Noop
        );
    }
}
