//! Conservative, atomic migration of a library entry to a selected source.
//! Source discovery and optional download removal are separate operations.

use std::collections::{BTreeMap, BTreeSet};

use anyhow::{Context, Result, ensure};
use rusqlite::{Transaction, TransactionBehavior, params};
use serde::Serialize;

use super::{Store, query_documents, write_book, write_chapter};
use crate::domain::{Book, Chapter, DownloadJob};

#[path = "migration/matcher.rs"]
mod matcher;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum ChapterMatchReason {
    NumberAndTitle,
    UniqueTitle,
    UniqueNumber,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ChapterMigrationMatch {
    pub origin_chapter_id: String,
    pub target_chapter_id: String,
    pub reason: ChapterMatchReason,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct MigrationPlan {
    pub matches: Vec<ChapterMigrationMatch>,
    pub unmatched_origin_chapter_ids: Vec<String>,
    pub ambiguous_origin_chapter_ids: Vec<String>,
    pub resume_target_chapter_id: Option<String>,
    pub needs_target_page_count: bool,
}

pub(super) struct ChapterMatchResult {
    pub(super) matches: Vec<ChapterMigrationMatch>,
    pub(super) unmatched_origin_chapter_ids: Vec<String>,
    pub(super) ambiguous_origin_chapter_ids: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MigrationOptions {
    pub preserve_read: bool,
    pub preserve_category: bool,
}

impl Default for MigrationOptions {
    fn default() -> Self {
        Self {
            preserve_read: true,
            preserve_category: true,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct MigrationReport {
    pub book: Book,
    pub source_downloads: Vec<DownloadJob>,
    pub matched: usize,
    pub read_copied: usize,
    pub unmatched: usize,
    pub ambiguous: usize,
    pub progress_copied: bool,
    pub progress_page_reset: bool,
    pub progress_conflict: bool,
    pub category_copied: bool,
    pub category_conflict: bool,
    pub tracking_copied: bool,
}

impl Store {
    pub fn preview_book_migration(
        &self,
        origin_id: &str,
        target: &Book,
        chapters: &[Chapter],
    ) -> Result<MigrationPlan> {
        if !self.connection.is_autocommit() {
            return preview(self, origin_id, target, chapters);
        }
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Deferred)?;
        let plan = preview(self, origin_id, target, chapters)?;
        transaction.commit()?;
        Ok(plan)
    }

    /// Preserve the origin's records, history, page manifests, jobs, and pins.
    /// A caller may issue book-scoped cleanup commands only after this commits.
    pub fn migrate_book(
        &self,
        origin_id: &str,
        target: &Book,
        chapters: &[Chapter],
        options: &MigrationOptions,
    ) -> Result<MigrationReport> {
        let transaction =
            Transaction::new_unchecked(&self.connection, TransactionBehavior::Immediate)?;
        let mut origin = self
            .book(origin_id)?
            .context("The origin manga no longer exists")?;
        validate_target(self, &origin, target, chapters)?;
        let plan = preview(self, origin_id, target, chapters)?;
        let origin_chapters = self.chapters(origin_id)?;
        let origin_progress = self.progress(origin_id)?;
        let target_progress = self.progress(&target.id)?;
        if options.preserve_read {
            if origin_chapters.iter().any(|chapter| chapter.read) || origin_progress.is_some() {
                ensure!(
                    !plan.matches.is_empty(),
                    "No unambiguous chapter matches were found; disable reading-state preservation to migrate only the book"
                );
            }
            if origin_progress.is_some() {
                ensure!(
                    plan.resume_target_chapter_id.is_some(),
                    "The current reading chapter cannot be matched safely; disable reading-state preservation to migrate only the book"
                );
            }
            ensure!(
                !plan.needs_target_page_count,
                "The target reading chapter needs a verified page count before progress can be migrated"
            );
        }
        let existing_target = self.book(&target.id)?;
        let mut destination = target.clone();
        destination.in_library =
            origin.in_library || existing_target.as_ref().is_some_and(|book| book.in_library);
        if let Some(previous) = &existing_target {
            destination.category = previous.category.clone();
            destination.chapter_count = destination.chapter_count.max(previous.chapter_count);
            if destination.author.is_empty() {
                destination.author = previous.author.clone();
            }
            if destination.description.is_empty() {
                destination.description = previous.description.clone();
            }
            if destination.cover.is_empty() {
                destination.cover = previous.cover.clone();
            }
            if destination.tags.is_empty() {
                destination.tags = previous.tags.clone();
            }
        } else {
            destination.category.clear();
        }
        let mut category_copied = false;
        let mut category_conflict = false;
        if options.preserve_category && !origin.category.is_empty() {
            if destination.category.is_empty() || destination.category == origin.category {
                destination.category = origin.category.clone();
                category_copied = true;
            } else {
                category_conflict = true;
            }
        }
        write_book(&transaction, &destination)?;
        let origin_by_id = origin_chapters
            .iter()
            .map(|chapter| (chapter.id.as_str(), chapter))
            .collect::<BTreeMap<_, _>>();
        let matched = plan
            .matches
            .iter()
            .map(|pair| {
                (
                    pair.target_chapter_id.as_str(),
                    pair.origin_chapter_id.as_str(),
                )
            })
            .collect::<BTreeMap<_, _>>();
        let mut read_copied = 0;
        for incoming in chapters {
            let mut chapter = incoming.clone();
            // Source metadata cannot assert local reading or file ownership.
            // Retain these flags only from committed destination records.
            chapter.read = false;
            chapter.downloaded = false;
            if let Some(previous) = self.chapter(&chapter.id)? {
                chapter.read = previous.read;
                chapter.downloaded = previous.downloaded;
                chapter.page_count = chapter.page_count.max(previous.page_count);
            }
            if options.preserve_read
                && matched
                    .get(chapter.id.as_str())
                    .and_then(|id| origin_by_id.get(id))
                    .is_some_and(|chapter| chapter.read)
            {
                chapter.read = true;
                read_copied += 1;
            }
            write_chapter(&transaction, &chapter)?;
        }
        let mut progress_copied = false;
        let progress_conflict =
            options.preserve_read && origin_progress.is_some() && target_progress.is_some();
        if options.preserve_read && target_progress.is_none() {
            if let (Some(progress), Some(chapter_id)) =
                (&origin_progress, &plan.resume_target_chapter_id)
            {
                let chapter = chapters
                    .iter()
                    .find(|chapter| &chapter.id == chapter_id)
                    .context("The migration resume chapter disappeared")?;
                let total = target_page_count(self, chapter)?;
                ensure!(
                    total > 0,
                    "The target reading chapter has no verified page count"
                );
                transaction.execute(
                    "UPDATE chapters SET document=json_set(document,'$.page_count',?1) WHERE id=?2 AND json_extract(document,'$.page_count')<>?1",
                    params![total, chapter_id],
                )?;
                // Chapter identity can be established; cross-source page order
                // cannot. Keep the old exact location and resume this chapter
                // at its first page without inventing a completed read event.
                transaction.execute("INSERT INTO progress(chapter_id,book_id,page,total,completed,updated_at) VALUES(?1,?2,1,?3,0,?4)", params![chapter_id,destination.id,total,progress.updated_at])?;
                progress_copied = true;
            }
        }
        destination.chapter_count = destination.chapter_count.max(transaction.query_row(
            "SELECT count(*) FROM chapters WHERE book_id=?1",
            [&destination.id],
            |row| row.get::<_, u32>(0),
        )?);
        destination.unread = transaction.query_row(
            "SELECT count(*) FROM chapters WHERE book_id=?1 AND json_extract(document,'$.read')=0",
            [&destination.id],
            |row| row.get(0),
        )?;
        write_book(&transaction, &destination)?;
        if origin.in_library {
            origin.in_library = false;
            write_book(&transaction, &origin)?;
            transaction.execute("INSERT INTO book_ui_metadata(book_id,added_at) SELECT ?1,added_at FROM book_ui_metadata WHERE book_id=?2 ON CONFLICT(book_id) DO UPDATE SET added_at=excluded.added_at WHERE book_ui_metadata.added_at=0", params![destination.id,origin.id])?;
        }
        let source_downloads = query_documents(
            &transaction,
            "SELECT document FROM downloads WHERE book_id=?1 ORDER BY rowid",
            [origin_id],
        )?;
        transaction.commit()?;
        Ok(MigrationReport {
            book: destination,
            source_downloads,
            matched: plan.matches.len(),
            read_copied,
            unmatched: plan.unmatched_origin_chapter_ids.len(),
            ambiguous: plan.ambiguous_origin_chapter_ids.len(),
            progress_copied,
            progress_page_reset: progress_copied,
            progress_conflict,
            category_copied,
            category_conflict,
            tracking_copied: false,
        })
    }
}

fn validate_target(
    store: &Store,
    origin: &Book,
    target: &Book,
    chapters: &[Chapter],
) -> Result<()> {
    ensure!(
        origin.id != target.id && origin.source_id != target.source_id,
        "Select a manga from a different source"
    );
    ensure!(
        !target.id.is_empty() && !target.source_id.is_empty() && !target.remote_id.is_empty(),
        "The migration target has no source identity"
    );
    ensure!(
        !chapters.is_empty() && chapters.len() <= 20_000,
        "The target chapter list is empty or too large"
    );
    if let Some(previous) = store.book(&target.id)? {
        ensure!(
            previous.source_id == target.source_id && previous.remote_id == target.remote_id,
            "The target identity belongs to another manga"
        );
    }
    let mut ids = BTreeSet::new();
    let mut remote_ids = BTreeSet::new();
    for chapter in chapters {
        ensure!(
            chapter.book_id == target.id
                && chapter.source_id == target.source_id
                && !chapter.id.is_empty()
                && !chapter.remote_id.is_empty()
                && chapter.number.is_finite(),
            "A target chapter has an invalid manga identity or number"
        );
        ensure!(
            ids.insert(&chapter.id) && remote_ids.insert(&chapter.remote_id),
            "The target chapter list contains duplicate identities"
        );
        if let Some(previous) = store.chapter(&chapter.id)? {
            ensure!(
                previous.book_id == target.id
                    && previous.source_id == target.source_id
                    && previous.remote_id == chapter.remote_id,
                "A target chapter identity belongs to another manga"
            );
        }
    }
    Ok(())
}

fn preview(
    store: &Store,
    origin_id: &str,
    target: &Book,
    chapters: &[Chapter],
) -> Result<MigrationPlan> {
    let origin = store
        .book(origin_id)?
        .context("The origin manga no longer exists")?;
    validate_target(store, &origin, target, chapters)?;
    let matched = matcher::match_chapters(&store.chapters(origin_id)?, chapters);
    let progress = store.progress(origin_id)?;
    let resume_target_chapter_id = progress
        .as_ref()
        .and_then(|progress| {
            matched
                .matches
                .iter()
                .find(|pair| pair.origin_chapter_id == progress.chapter_id)
        })
        .map(|pair| pair.target_chapter_id.clone());
    let needs_target_page_count = if store.progress(&target.id)?.is_none() {
        resume_target_chapter_id
            .as_ref()
            .and_then(|id| chapters.iter().find(|chapter| &chapter.id == id))
            .map(|chapter| target_page_count(store, chapter).map(|total| total == 0))
            .transpose()?
            .unwrap_or(false)
    } else {
        false
    };
    Ok(MigrationPlan {
        matches: matched.matches,
        unmatched_origin_chapter_ids: matched.unmatched_origin_chapter_ids,
        ambiguous_origin_chapter_ids: matched.ambiguous_origin_chapter_ids,
        resume_target_chapter_id,
        needs_target_page_count,
    })
}

fn target_page_count(store: &Store, chapter: &Chapter) -> Result<u32> {
    let cached = store.page_window(&chapter.id, 0, 0)?.total;
    Ok(if cached > 0 {
        u32::try_from(cached).context("The target page count exceeds this device's range")?
    } else {
        chapter.page_count
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{Page, Progress};

    fn book(id: &str, source: &str) -> Book {
        Book {
            id: id.into(),
            source_id: source.into(),
            remote_id: id.into(),
            title: "A selected manga".into(),
            in_library: true,
            ..Book::default()
        }
    }

    fn chapter(book: &Book, suffix: &str, number: f64, title: &str, total: u32) -> Chapter {
        Chapter {
            id: format!("{}-{suffix}", book.id),
            book_id: book.id.clone(),
            source_id: book.source_id.clone(),
            remote_id: suffix.into(),
            number,
            title: title.into(),
            page_count: total,
            ..Chapter::default()
        }
    }

    fn progress(book: &Book, chapter: &Chapter, page: u32, total: u32) -> Progress {
        Progress {
            book_id: book.id.clone(),
            chapter_id: chapter.id.clone(),
            page,
            total,
            updated_at: 100,
            completed: false,
        }
    }

    #[test]
    fn migration_matches_reordered_chapters_and_retains_origin_downloads_and_history() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path().join("library.sqlite3")).unwrap();
        let mut origin = book("origin", "old-source");
        origin.category = "Favorites".into();
        store.upsert_book(&origin).unwrap();
        store.record_library_added(&origin.id, 40).unwrap();
        store.record_book_open(&origin.id, 90).unwrap();
        let mut first = chapter(&origin, "one", 1.0, "North", 1);
        first.read = true;
        let second = chapter(&origin, "two", 2.0, "South", 5);
        store
            .save_chapters(&origin.id, &[first.clone(), second.clone()])
            .unwrap();
        let path = directory.path().join("retained.png");
        image::GrayImage::new(1, 1).save(&path).unwrap();
        let pages = vec![Page {
            local_path: Some(path.clone()),
            ..Page::default()
        }];
        store.save_pages(&first.id, &pages).unwrap();
        let complete = DownloadJob {
            chapter_id: first.id.clone(),
            book_id: origin.id.clone(),
            state: "completed".into(),
            completed: 1,
            total: 1,
            error: String::new(),
        };
        let paused = DownloadJob {
            chapter_id: second.id.clone(),
            book_id: origin.id.clone(),
            state: "paused".into(),
            total: 5,
            ..DownloadJob::default()
        };
        store.save_download(&complete).unwrap();
        store.save_download(&paused).unwrap();
        store
            .connection
            .execute(
                "INSERT INTO download_sleep_ownership(chapter_id) VALUES(?1)",
                [&second.id],
            )
            .unwrap();
        let old_progress = progress(&origin, &second, 3, 5);
        store.save_progress(&old_progress).unwrap();
        let other = book("other", "old-source");
        store.upsert_book(&other).unwrap();
        let other_chapter = chapter(&other, "one", 1.0, "Other", 1);
        store
            .save_chapters(&other.id, &[other_chapter.clone()])
            .unwrap();
        store
            .save_download(&DownloadJob {
                chapter_id: other_chapter.id,
                book_id: other.id,
                state: "queued".into(),
                total: 1,
                ..DownloadJob::default()
            })
            .unwrap();

        let target = book("target", "new-source");
        let target_first = chapter(&target, "one", 1.0, "North", 3);
        let target_second = chapter(&target, "two", 2.0, "South", 9);
        let report = store
            .migrate_book(
                &origin.id,
                &target,
                &[target_second.clone(), target_first.clone()],
                &MigrationOptions::default(),
            )
            .unwrap();

        assert_eq!(
            (
                report.matched,
                report.read_copied,
                report.unmatched,
                report.ambiguous
            ),
            (2, 1, 0, 0)
        );
        assert!(report.progress_copied && report.progress_page_reset && report.category_copied);
        assert!(!report.tracking_copied);
        assert_eq!(report.book.category, "Favorites");
        assert!(report.book.in_library);
        assert_eq!(report.book.unread, 1);
        assert_eq!(
            report.source_downloads,
            vec![complete.clone(), paused.clone()]
        );
        assert!(!store.book(&origin.id).unwrap().unwrap().in_library);
        assert!(store.chapter(&target_first.id).unwrap().unwrap().read);
        assert!(!store.chapter(&target_second.id).unwrap().unwrap().read);
        assert_eq!(store.progress(&origin.id).unwrap(), Some(old_progress));
        let migrated_progress = store.progress(&target.id).unwrap().unwrap();
        assert_eq!(
            (
                migrated_progress.chapter_id,
                migrated_progress.page,
                migrated_progress.total,
                migrated_progress.updated_at,
                migrated_progress.completed
            ),
            (target_second.id, 1, 9, 100, false)
        );
        assert_eq!(store.pages(&first.id).unwrap(), pages);
        assert_eq!(store.download(&first.id).unwrap(), Some(complete));
        assert_eq!(store.download(&second.id).unwrap(), Some(paused));
        assert!(path.exists());
        assert_eq!(
            store
                .connection
                .query_row(
                    "SELECT count(*) FROM download_sleep_ownership WHERE chapter_id=?1",
                    [&second.id],
                    |row| row.get::<_, usize>(0)
                )
                .unwrap(),
            1
        );
        assert_eq!(
            store.completed_read_download_ids(Some(&origin.id)).unwrap(),
            vec![first.id]
        );
        assert!(
            store
                .completed_read_download_ids(Some(&target.id))
                .unwrap()
                .is_empty()
        );
        let dates = store
            .connection
            .query_row(
                "SELECT added_at,opened_at FROM book_ui_metadata WHERE book_id=?1",
                [&target.id],
                |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?)),
            )
            .unwrap();
        assert_eq!(dates, (40, 0));
    }

    #[test]
    fn unknown_page_count_and_ambiguous_resume_roll_back_instead_of_guessing() {
        let store = Store::open(":memory:").unwrap();
        let origin = book("origin", "old-source");
        store.upsert_book(&origin).unwrap();
        let old = chapter(&origin, "one", 1.0, "Chapter 1", 8);
        store.save_chapters(&origin.id, &[old.clone()]).unwrap();
        let old_progress = progress(&origin, &old, 4, 8);
        store.save_progress(&old_progress).unwrap();
        let target = book("target", "new-source");
        let next = chapter(&target, "one", 1.0, "Chapter 1", 0);
        let plan = store
            .preview_book_migration(&origin.id, &target, &[next.clone()])
            .unwrap();
        assert_eq!(
            plan.resume_target_chapter_id.as_deref(),
            Some(next.id.as_str())
        );
        assert!(plan.needs_target_page_count);
        let error = store
            .migrate_book(
                &origin.id,
                &target,
                &[next.clone()],
                &MigrationOptions::default(),
            )
            .unwrap_err();
        assert!(error.to_string().contains("verified page count"));
        assert!(store.book(&target.id).unwrap().is_none());
        assert!(store.book(&origin.id).unwrap().unwrap().in_library);

        let mut next = next;
        next.page_count = 8;
        let duplicate = chapter(&target, "duplicate", 1.0, "Chapter 1", 8);
        let chapters = [next, duplicate];
        let plan = store
            .preview_book_migration(&origin.id, &target, &chapters)
            .unwrap();
        assert_eq!(plan.ambiguous_origin_chapter_ids, vec![old.id.clone()]);
        assert!(
            store
                .migrate_book(&origin.id, &target, &chapters, &MigrationOptions::default())
                .is_err()
        );
        assert!(store.book(&target.id).unwrap().is_none());
        assert_eq!(store.progress(&origin.id).unwrap(), Some(old_progress));
        let report = store
            .migrate_book(
                &origin.id,
                &target,
                &chapters,
                &MigrationOptions {
                    preserve_read: false,
                    preserve_category: false,
                },
            )
            .unwrap();
        assert_eq!(report.matched, 0);
        assert!(!report.progress_copied);
        assert!(store.progress(&target.id).unwrap().is_none());
    }

    #[test]
    fn existing_destination_progress_category_and_read_flags_are_not_overwritten() {
        let store = Store::open(":memory:").unwrap();
        let mut origin = book("origin", "old-source");
        origin.category = "Origin category".into();
        store.upsert_book(&origin).unwrap();
        let mut old = chapter(&origin, "one", 1.0, "North", 8);
        old.read = true;
        store.save_chapters(&origin.id, &[old.clone()]).unwrap();
        store.save_progress(&progress(&origin, &old, 4, 8)).unwrap();
        let mut target = book("target", "new-source");
        target.category = "Existing category".into();
        store.upsert_book(&target).unwrap();
        let first = chapter(&target, "one", 1.0, "North", 0);
        let mut second = chapter(&target, "two", 2.0, "South", 3);
        second.read = true;
        store
            .save_chapters(&target.id, &[first.clone(), second.clone()])
            .unwrap();
        let mut destination_progress = progress(&target, &second, 2, 3);
        destination_progress.updated_at = 250;
        store.save_progress(&destination_progress).unwrap();
        second.read = false;
        let report = store
            .migrate_book(
                &origin.id,
                &target,
                &[first.clone(), second.clone()],
                &MigrationOptions::default(),
            )
            .unwrap();
        assert!(report.progress_conflict && report.category_conflict);
        assert!(!report.progress_copied && !report.category_copied);
        assert_eq!(report.book.category, "Existing category");
        assert_eq!(
            store.progress(&target.id).unwrap(),
            Some(destination_progress)
        );
        assert!(store.chapter(&first.id).unwrap().unwrap().read);
        assert!(store.chapter(&second.id).unwrap().unwrap().read);
    }

    #[test]
    fn committed_target_manifest_count_takes_precedence_over_stale_source_metadata() {
        let store = Store::open(":memory:").unwrap();
        let origin = book("origin", "old-source");
        store.upsert_book(&origin).unwrap();
        let old = chapter(&origin, "one", 1.0, "North", 8);
        store.save_chapters(&origin.id, &[old.clone()]).unwrap();
        store.save_progress(&progress(&origin, &old, 4, 8)).unwrap();
        let target = book("target", "new-source");
        store.upsert_book(&target).unwrap();
        let next = chapter(&target, "one", 1.0, "North", 99);
        store.save_chapters(&target.id, &[next.clone()]).unwrap();
        store
            .save_pages(
                &next.id,
                &[
                    Page {
                        url: "https://example.invalid/1.png".into(),
                        ..Page::default()
                    },
                    Page {
                        url: "https://example.invalid/2.png".into(),
                        ..Page::default()
                    },
                ],
            )
            .unwrap();
        let report = store
            .migrate_book(&origin.id, &target, &[next], &MigrationOptions::default())
            .unwrap();
        assert!(report.progress_copied);
        let migrated = store.progress(&target.id).unwrap().unwrap();
        assert_eq!((migrated.page, migrated.total), (1, 2));
        assert_eq!(
            store
                .chapter(&migrated.chapter_id)
                .unwrap()
                .unwrap()
                .page_count,
            2
        );
    }

    #[test]
    fn failure_after_destination_writes_rolls_back_the_entire_migration() {
        let store = Store::open(":memory:").unwrap();
        let origin = book("origin", "old-source");
        store.upsert_book(&origin).unwrap();
        let old = chapter(&origin, "one", 1.0, "North", 8);
        store.save_chapters(&origin.id, &[old]).unwrap();
        store.connection.execute_batch("CREATE TEMP TRIGGER reject_origin_removal BEFORE UPDATE ON books WHEN OLD.id='origin' AND NEW.in_library=0 BEGIN SELECT RAISE(ABORT,'injected migration failure'); END;").unwrap();
        let target = book("target", "new-source");
        let next = chapter(&target, "one", 1.0, "North", 5);
        let error = store
            .migrate_book(
                &origin.id,
                &target,
                &[next.clone()],
                &MigrationOptions::default(),
            )
            .unwrap_err();
        assert!(error.to_string().contains("injected migration failure"));
        assert!(store.book(&target.id).unwrap().is_none());
        assert!(store.chapter(&next.id).unwrap().is_none());
        assert!(store.book(&origin.id).unwrap().unwrap().in_library);
    }

    #[test]
    fn commit_rechecks_identity_and_mapping_after_a_preview_on_another_connection() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("library.sqlite3");
        let store = Store::open(&database).unwrap();
        let origin = book("origin", "old-source");
        store.upsert_book(&origin).unwrap();
        let old = chapter(&origin, "one", 1.0, "North", 8);
        store.save_chapters(&origin.id, &[old.clone()]).unwrap();
        store.save_progress(&progress(&origin, &old, 3, 8)).unwrap();
        let target = book("target", "new-source");
        let next = chapter(&target, "one", 1.0, "North", 5);
        assert_eq!(
            store
                .preview_book_migration(&origin.id, &target, &[next.clone()])
                .unwrap()
                .matches
                .len(),
            1
        );
        let other = Store::open_existing_for_write(&database).unwrap();
        let mut changed = old;
        changed.title = "An unrelated chapter".into();
        other.save_chapters(&origin.id, &[changed]).unwrap();
        assert!(
            store
                .migrate_book(&origin.id, &target, &[next], &MigrationOptions::default())
                .is_err()
        );
        assert!(store.book(&target.id).unwrap().is_none());
        assert!(store.book(&origin.id).unwrap().unwrap().in_library);
    }

    #[test]
    fn chapter_identity_collisions_cannot_overwrite_the_origin() {
        let store = Store::open(":memory:").unwrap();
        let origin = book("origin", "old-source");
        store.upsert_book(&origin).unwrap();
        let old = chapter(&origin, "one", 1.0, "North", 8);
        store.save_chapters(&origin.id, &[old.clone()]).unwrap();
        let target = book("target", "new-source");
        let mut next = chapter(&target, "one", 1.0, "North", 5);
        next.id = old.id.clone();
        assert!(
            store
                .migrate_book(&origin.id, &target, &[next], &MigrationOptions::default())
                .is_err()
        );
        assert_eq!(store.chapter(&old.id).unwrap(), Some(old));
        assert!(store.book(&target.id).unwrap().is_none());
        assert!(store.book(&origin.id).unwrap().unwrap().in_library);
    }

    #[test]
    fn incoming_metadata_cannot_claim_unverified_local_downloads_or_reads() {
        let store = Store::open(":memory:").unwrap();
        let origin = book("origin", "old-source");
        store.upsert_book(&origin).unwrap();
        let old = chapter(&origin, "one", 1.0, "North", 8);
        store.save_chapters(&origin.id, &[old]).unwrap();
        let target = book("target", "new-source");
        let mut next = chapter(&target, "one", 1.0, "North", 5);
        next.read = true;
        next.downloaded = true;
        store
            .migrate_book(
                &origin.id,
                &target,
                &[next.clone()],
                &MigrationOptions::default(),
            )
            .unwrap();
        let committed = store.chapter(&next.id).unwrap().unwrap();
        assert!(!committed.read && !committed.downloaded);
        assert!(store.pages(&next.id).unwrap().is_empty());
    }
}
