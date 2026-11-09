//! Background accounting for completed local chapter images.

use std::{
    collections::{BTreeSet, HashMap, HashSet},
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

use anyhow::{Context, Result, ensure};

use super::{App, JobKind, JobResult};
use crate::{sources::NetworkControl, store::Store};

const PATH_BATCH: usize = 128;
const MANIFEST_BATCH: usize = 64;
const CHAPTER_BATCH: usize = 8;
const RETRY_DELAY: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize)]
pub(super) struct UsageScanStats {
    pub path_queries: u64,
    pub chapter_queries: u64,
    pub manifest_windows: u64,
    pub paths_observed: u64,
    pub file_checks: u64,
}

#[derive(Debug, Default)]
struct UsageSnapshot {
    files: HashMap<PathBuf, u64>,
    completed_chapters: HashSet<String>,
    total: u64,
}

impl UsageSnapshot {
    fn add_files(&mut self, files: HashMap<PathBuf, u64>) {
        for (path, size) in files {
            let previous = self.files.insert(path, size).unwrap_or_default();
            self.total = self.total.saturating_sub(previous).saturating_add(size);
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum RequestKind {
    Rebuild,
    Append(Vec<String>),
}

#[derive(Debug, Clone)]
struct UsageRequest {
    generation: u64,
    sequence: u64,
    kind: RequestKind,
}

#[derive(Debug)]
pub(super) struct UsageResult {
    generation: u64,
    sequence: u64,
    epoch: u64,
    kind: RequestKind,
    snapshot: UsageSnapshot,
    pub stats: UsageScanStats,
}

#[derive(Debug, Clone, Copy)]
struct Inflight {
    job_id: u64,
    generation: u64,
}

/// A pending completion remains queued until its result is accepted. Failed or
/// lifecycle-stale workers can therefore release their slot without losing it.
pub(super) struct UsageTracker {
    generation: u64,
    generation_token: Arc<AtomicU64>,
    sequence: u64,
    accepted_sequence: Option<u64>,
    snapshot: Option<Arc<UsageSnapshot>>,
    rebuild: bool,
    pending_chapters: BTreeSet<String>,
    inflight: Option<Inflight>,
    retry_after: Option<Instant>,
}

impl Default for UsageTracker {
    fn default() -> Self {
        Self {
            generation: 0,
            generation_token: Arc::new(AtomicU64::new(0)),
            sequence: 0,
            accepted_sequence: None,
            snapshot: None,
            rebuild: true,
            pending_chapters: BTreeSet::new(),
            inflight: None,
            retry_after: None,
        }
    }
}

impl UsageTracker {
    fn invalidate(&mut self) {
        self.generation = self.generation.wrapping_add(1);
        self.generation_token
            .store(self.generation, Ordering::Release);
        self.rebuild = true;
        self.pending_chapters.clear();
        self.retry_after = None;
    }

    fn queue_completed(&mut self, chapter_id: &str) {
        if chapter_id.is_empty() {
            return;
        }
        if !self.rebuild
            && self
                .snapshot
                .as_ref()
                .is_some_and(|snapshot| snapshot.completed_chapters.contains(chapter_id))
        {
            return;
        }
        self.pending_chapters.insert(chapter_id.to_owned());
    }

    fn next_request(&mut self, now: Instant) -> Option<UsageRequest> {
        if self.inflight.is_some() || self.retry_after.is_some_and(|retry| now < retry) {
            return None;
        }
        let kind = if self.rebuild {
            RequestKind::Rebuild
        } else {
            let chapters: Vec<_> = self
                .pending_chapters
                .iter()
                .take(CHAPTER_BATCH)
                .cloned()
                .collect();
            if chapters.is_empty() {
                return None;
            }
            RequestKind::Append(chapters)
        };
        self.sequence = self.sequence.wrapping_add(1);
        Some(UsageRequest {
            generation: self.generation,
            sequence: self.sequence,
            kind,
        })
    }

    fn started(&mut self, job_id: u64, generation: u64) {
        self.inflight = Some(Inflight { job_id, generation });
    }

    fn finish(&mut self, job_id: u64, now: Instant) -> bool {
        let Some(inflight) = self.inflight.filter(|inflight| inflight.job_id == job_id) else {
            return false;
        };
        self.inflight = None;
        if inflight.generation == self.generation {
            self.retry_after = Some(now + RETRY_DELAY);
        }
        true
    }

    fn accept(&mut self, result: UsageResult) -> Option<u64> {
        if result.generation != self.generation
            || result.sequence != self.sequence
            || self.accepted_sequence == Some(result.sequence)
        {
            return None;
        }
        match result.kind {
            RequestKind::Rebuild => {
                self.pending_chapters
                    .retain(|chapter| !result.snapshot.completed_chapters.contains(chapter));
                self.snapshot = Some(Arc::new(result.snapshot));
                self.rebuild = false;
            }
            RequestKind::Append(chapters) => {
                if self.rebuild {
                    return None;
                }
                // The worker drops its shared index before sending a result.
                // No existing file map is copied when merging a completion.
                let snapshot = Arc::get_mut(self.snapshot.as_mut()?)?;
                for chapter in chapters {
                    self.pending_chapters.remove(&chapter);
                }
                snapshot
                    .completed_chapters
                    .extend(result.snapshot.completed_chapters);
                snapshot.add_files(result.snapshot.files);
            }
        }
        self.accepted_sequence = Some(result.sequence);
        self.retry_after = None;
        self.snapshot.as_ref().map(|snapshot| snapshot.total)
    }
}

struct ScanGuard {
    network: NetworkControl,
    epoch: u64,
    generation: Arc<AtomicU64>,
    expected_generation: u64,
}

impl ScanGuard {
    fn check(&self) -> Result<()> {
        self.network.check_epoch(self.epoch)?;
        ensure!(
            self.generation.load(Ordering::Acquire) == self.expected_generation,
            "The offline storage request was superseded"
        );
        Ok(())
    }
}

impl App {
    /// Keep the last accepted value visible while a destructive change rebuilds
    /// the distinct-path index. This method performs no database or file work.
    pub(super) fn invalidate_download_usage(&mut self) {
        self.download_usage.invalidate();
        self.invalidate_storage_details();
    }

    pub(super) fn queue_completed_download_usage(&mut self, chapter_id: &str) {
        self.download_usage.queue_completed(chapter_id);
    }

    /// Schedule accounting only after its capacity label becomes relevant.
    pub(super) fn poll_download_usage(&mut self) -> Result<()> {
        if self.options.demo
            || self.shutting_down
            || self.suspended
            || self.resume_frame_pending
            || !self.download_usage_visible()
        {
            return Ok(());
        }
        let Some(request) = self.download_usage.next_request(Instant::now()) else {
            return Ok(());
        };
        let generation = request.generation;
        let known = match &request.kind {
            RequestKind::Rebuild => None,
            RequestKind::Append(_) => self.download_usage.snapshot.as_ref().map(Arc::clone),
        };
        let database = self.data_dir.join("library.sqlite3");
        let guard = ScanGuard {
            network: self.network_control.clone(),
            epoch: self.network_control.epoch(),
            generation: Arc::clone(&self.download_usage.generation_token),
            expected_generation: generation,
        };
        match self.spawn_local_job(false, JobKind::DownloadUsage, move || {
            scan_usage(&database, request, &guard, known.as_deref()).map(JobResult::DownloadUsage)
        }) {
            Ok(job_id) => self.download_usage.started(job_id, generation),
            Err(error) => {
                self.download_usage.retry_after = Some(Instant::now() + RETRY_DELAY);
                return Err(error).context("Start offline storage accounting");
            }
        }
        Ok(())
    }

    /// Call before discarding messages from an earlier lifecycle generation.
    pub(super) fn finish_usage_job(&mut self, job_id: u64) -> bool {
        self.download_usage.finish(job_id, Instant::now())
    }

    pub(super) fn accept_download_usage(&mut self, result: UsageResult) -> bool {
        if self.suspended
            || self.resume_frame_pending
            || self.network_control.check_epoch(result.epoch).is_err()
        {
            return false;
        }
        let Some(total) = self.download_usage.accept(result) else {
            return false;
        };
        let changed = self.cached_download_bytes != Some(total);
        self.cached_download_bytes = Some(total);
        if changed && self.download_usage_visible() {
            self.background_ui_dirty = true;
        }
        changed
    }

    fn download_usage_visible(&self) -> bool {
        self.route == "settings" || self.route == "library" && self.platform.profile().is_scribe()
    }
}

fn scan_usage(
    database: &Path,
    request: UsageRequest,
    guard: &ScanGuard,
    known: Option<&UsageSnapshot>,
) -> Result<UsageResult> {
    guard.check()?;
    let store = Store::open_read_only(database)?;
    guard.check()?;
    let mut snapshot = UsageSnapshot::default();
    let mut stats = UsageScanStats::default();
    match &request.kind {
        RequestKind::Rebuild => {
            guard.check()?;
            stats.chapter_queries += 1;
            snapshot.completed_chapters = store
                .completed_download_chapter_ids()?
                .into_iter()
                .collect();
            guard.check()?;
            let mut cursor = None;
            loop {
                guard.check()?;
                stats.path_queries += 1;
                let paths = store.completed_download_paths_page(cursor.as_deref(), PATH_BATCH)?;
                guard.check()?;
                if paths.is_empty() {
                    break;
                }
                // The keyset cursor uses the stored path, not its canonical
                // filesystem identity. The query has already dropped its read
                // statement before this batch performs any filesystem work.
                cursor = paths.last().map(|path| path.to_string_lossy().into_owned());
                let final_batch = paths.len() < PATH_BATCH;
                collect_files(paths, &mut snapshot, None, None, &mut stats, guard)?;
                if final_batch {
                    break;
                }
                std::thread::yield_now();
            }
        }
        RequestKind::Append(chapters) => {
            let mut observed = HashSet::new();
            for chapter_id in chapters {
                guard.check()?;
                stats.chapter_queries += 1;
                if !store
                    .chapter(chapter_id)?
                    .is_some_and(|chapter| chapter.downloaded)
                {
                    continue;
                }
                guard.check()?;
                snapshot.completed_chapters.insert(chapter_id.clone());
                let mut offset = 0;
                loop {
                    guard.check()?;
                    stats.manifest_windows += 1;
                    let window = store.page_window(chapter_id, offset, MANIFEST_BATCH)?;
                    guard.check()?;
                    let count = window.pages.len();
                    let paths = window.pages.into_iter().filter_map(|page| page.local_path);
                    collect_files(
                        paths,
                        &mut snapshot,
                        Some(&mut observed),
                        known,
                        &mut stats,
                        guard,
                    )?;
                    offset += count;
                    if count == 0 || offset >= window.total {
                        break;
                    }
                    std::thread::yield_now();
                }
            }
        }
    }
    guard.check()?;
    Ok(UsageResult {
        generation: request.generation,
        sequence: request.sequence,
        epoch: guard.epoch,
        kind: request.kind,
        snapshot,
        stats,
    })
}

fn collect_files(
    paths: impl IntoIterator<Item = PathBuf>,
    snapshot: &mut UsageSnapshot,
    mut observed: Option<&mut HashSet<PathBuf>>,
    known: Option<&UsageSnapshot>,
    stats: &mut UsageScanStats,
    guard: &ScanGuard,
) -> Result<()> {
    for path in paths {
        guard.check()?;
        stats.paths_observed += 1;
        if let Some(observed) = observed.as_mut() {
            if !observed.insert(path.clone()) {
                continue;
            }
        }
        if known.is_some_and(|snapshot| snapshot.files.contains_key(&path)) {
            continue;
        }
        let canonical = match fs::canonicalize(&path) {
            Ok(path) => path,
            Err(error) if error.kind() == ErrorKind::NotFound => continue,
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("Resolve offline image {}", path.display()));
            }
        };
        if snapshot.files.contains_key(&canonical)
            || known.is_some_and(|snapshot| snapshot.files.contains_key(&canonical))
        {
            continue;
        }
        guard.check()?;
        stats.file_checks += 1;
        let metadata = match fs::metadata(&canonical) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == ErrorKind::NotFound => continue,
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("Inspect offline image {}", canonical.display()));
            }
        };
        let size = metadata.len();
        if metadata.is_file() && size > 0 && size <= crate::imaging::MAX_PAGE_BYTES {
            snapshot.total = snapshot.total.saturating_add(size);
            snapshot.files.insert(canonical, size);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{Book, Chapter, DownloadJob, Page};

    fn guard_for(tracker: &UsageTracker, root: &Path) -> ScanGuard {
        let network = NetworkControl::open(root).unwrap();
        ScanGuard {
            epoch: network.epoch(),
            network,
            generation: Arc::clone(&tracker.generation_token),
            expected_generation: tracker.generation,
        }
    }

    fn result_for(request: &UsageRequest, files: &[(&str, u64)], chapters: &[&str]) -> UsageResult {
        let mut snapshot = UsageSnapshot {
            completed_chapters: chapters.iter().map(|chapter| (*chapter).into()).collect(),
            ..UsageSnapshot::default()
        };
        snapshot.add_files(
            files
                .iter()
                .map(|(path, size)| (PathBuf::from(*path), *size))
                .collect(),
        );
        UsageResult {
            generation: request.generation,
            sequence: request.sequence,
            epoch: 0,
            kind: request.kind.clone(),
            snapshot,
            stats: UsageScanStats::default(),
        }
    }

    fn add_local_chapter(store: &Store, id: &str, paths: &[PathBuf]) {
        if store.book("book").unwrap().is_none() {
            store
                .upsert_book(&Book {
                    id: "book".into(),
                    source_id: "local".into(),
                    remote_id: "book".into(),
                    title: "Local book".into(),
                    in_library: true,
                    ..Book::default()
                })
                .unwrap();
        }
        store
            .save_chapters(
                "book",
                &[Chapter {
                    id: id.into(),
                    source_id: "local".into(),
                    remote_id: id.into(),
                    book_id: "book".into(),
                    ..Chapter::default()
                }],
            )
            .unwrap();
        store
            .save_pages(
                id,
                &paths
                    .iter()
                    .map(|path| Page {
                        local_path: Some(path.clone()),
                        ..Page::default()
                    })
                    .collect::<Vec<_>>(),
            )
            .unwrap();
    }

    #[test]
    fn completed_chapters_append_without_double_counting_shared_paths() {
        let mut tracker = UsageTracker::default();
        let initial = tracker.next_request(Instant::now()).unwrap();
        assert_eq!(
            tracker.accept(result_for(&initial, &[("shared", 100)], &["one"])),
            Some(100)
        );
        tracker.queue_completed("one");
        assert!(tracker.next_request(Instant::now()).is_none());
        tracker.queue_completed("two");
        tracker.queue_completed("two");
        let append = tracker.next_request(Instant::now()).unwrap();
        assert_eq!(append.kind, RequestKind::Append(vec!["two".into()]));
        assert_eq!(
            tracker.accept(result_for(
                &append,
                &[("shared", 100), ("new", 60)],
                &["two"]
            )),
            Some(160)
        );
        assert_eq!(tracker.snapshot.as_ref().unwrap().files.len(), 2);
        tracker.queue_completed("two");
        assert!(tracker.next_request(Instant::now()).is_none());
    }

    #[test]
    fn rebuild_keeps_the_last_value_and_rejects_a_stale_generation() {
        let mut tracker = UsageTracker::default();
        let initial = tracker.next_request(Instant::now()).unwrap();
        assert_eq!(
            tracker.accept(result_for(&initial, &[("old", 500)], &["old"])),
            Some(500)
        );
        tracker.queue_completed("next");
        let old_append = tracker.next_request(Instant::now()).unwrap();
        let stale = result_for(&old_append, &[("late", 50)], &["next"]);
        tracker.invalidate();
        assert_eq!(tracker.snapshot.as_ref().unwrap().total, 500);
        assert_eq!(tracker.accept(stale), None);
        let rebuild = tracker.next_request(Instant::now()).unwrap();
        assert_eq!(rebuild.kind, RequestKind::Rebuild);
        assert_eq!(
            tracker.accept(result_for(&rebuild, &[("current", 300)], &["current"])),
            Some(300)
        );
        assert!(
            !tracker
                .snapshot
                .as_ref()
                .unwrap()
                .files
                .contains_key(Path::new("old"))
        );
    }

    #[test]
    fn a_completion_arriving_during_rebuild_remains_pending() {
        let mut tracker = UsageTracker::default();
        let rebuild = tracker.next_request(Instant::now()).unwrap();
        tracker.queue_completed("late");
        tracker.queue_completed("included");
        tracker
            .accept(result_for(&rebuild, &[("included", 10)], &["included"]))
            .unwrap();
        let append = tracker.next_request(Instant::now()).unwrap();
        assert_eq!(append.kind, RequestKind::Append(vec!["late".into()]));
    }

    #[test]
    fn only_a_matching_job_releases_the_slot_and_failures_keep_the_request() {
        let mut tracker = UsageTracker::default();
        let now = Instant::now();
        let request = tracker.next_request(now).unwrap();
        tracker.started(7, request.generation);
        assert!(!tracker.finish(8, now));
        assert!(tracker.next_request(now + RETRY_DELAY).is_none());
        assert!(tracker.finish(7, now));
        assert!(tracker.next_request(now).is_none());
        assert_eq!(
            tracker.next_request(now + RETRY_DELAY).unwrap().kind,
            RequestKind::Rebuild
        );
    }

    #[test]
    fn full_scan_deduplicates_canonical_aliases_and_excludes_missing_files() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("library.sqlite3");
        let store = Store::open(&database).unwrap();
        let image = directory.path().join("page.bin");
        fs::write(&image, [7; 53]).unwrap();
        fs::create_dir(directory.path().join("alias")).unwrap();
        let alias = directory.path().join("alias").join("..").join("page.bin");
        add_local_chapter(&store, "one", std::slice::from_ref(&image));
        add_local_chapter(&store, "two", &[alias, image.clone()]);
        let mut tracker = UsageTracker::default();
        let request = tracker.next_request(Instant::now()).unwrap();
        let guard = guard_for(&tracker, directory.path());
        let result = scan_usage(&database, request, &guard, None).unwrap();
        assert_eq!(result.snapshot.total, 53);
        assert_eq!(result.snapshot.files.len(), 1);
        assert_eq!(result.stats.file_checks, 1);
        assert_eq!(tracker.accept(result), Some(53));
        fs::remove_file(image).unwrap();
        tracker.invalidate();
        assert_eq!(tracker.snapshot.as_ref().unwrap().total, 53);
        let request = tracker.next_request(Instant::now()).unwrap();
        let guard = guard_for(&tracker, directory.path());
        let result = scan_usage(&database, request, &guard, None).unwrap();
        assert_eq!(result.snapshot.total, 0);
        assert_eq!(tracker.accept(result), Some(0));
    }

    #[test]
    fn an_incremental_completion_reads_only_its_new_manifest() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("library.sqlite3");
        let store = Store::open(&database).unwrap();
        let mut old_paths = Vec::new();
        for index in 0..257 {
            let path = directory.path().join(format!("old-{index:04}.bin"));
            fs::write(&path, [1; 16]).unwrap();
            old_paths.push(path);
        }
        add_local_chapter(&store, "old", &old_paths);
        let mut tracker = UsageTracker::default();
        let request = tracker.next_request(Instant::now()).unwrap();
        let guard = guard_for(&tracker, directory.path());
        let initial = scan_usage(&database, request, &guard, None).unwrap();
        assert_eq!(initial.stats.paths_observed, 257);
        assert_eq!(initial.stats.path_queries, 3);
        tracker.accept(initial).unwrap();
        let next_path = directory.path().join("next.bin");
        fs::write(&next_path, [2; 31]).unwrap();
        add_local_chapter(&store, "next", &[old_paths[0].clone(), next_path]);
        tracker.queue_completed("next");
        let request = tracker.next_request(Instant::now()).unwrap();
        let guard = guard_for(&tracker, directory.path());
        let append = scan_usage(&database, request, &guard, tracker.snapshot.as_deref()).unwrap();
        assert_eq!(append.stats.path_queries, 0);
        assert_eq!(append.stats.chapter_queries, 1);
        assert_eq!(append.stats.manifest_windows, 1);
        assert_eq!(append.stats.paths_observed, 2);
        assert_eq!(append.stats.file_checks, 1);
        assert_eq!(tracker.accept(append), Some(257 * 16 + 31));
    }

    #[test]
    fn cancellation_is_checked_before_opening_a_database() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("does-not-exist.sqlite");
        let mut tracker = UsageTracker::default();
        let request = tracker.next_request(Instant::now()).unwrap();
        let guard = guard_for(&tracker, directory.path());
        guard.network.suspend();
        let error = scan_usage(&database, request, &guard, None).unwrap_err();
        assert!(crate::sources::is_network_interrupted(&error));
        guard.network.resume();
        let request = tracker.next_request(Instant::now()).unwrap();
        let guard = guard_for(&tracker, directory.path());
        tracker.invalidate();
        let error = scan_usage(&database, request, &guard, None).unwrap_err();
        assert!(error.to_string().contains("superseded"));
        assert!(!database.exists());
    }

    fn insert_benchmark_chapters(
        store: &Store,
        book: &Book,
        chapters: &[(String, Vec<PathBuf>)],
    ) -> Result<()> {
        let transaction = rusqlite::Transaction::new_unchecked(
            store.test_connection(),
            rusqlite::TransactionBehavior::Immediate,
        )?;
        {
            let mut chapter_insert = transaction.prepare(
                "INSERT INTO chapters(id,book_id,source_id,remote_id,number,document)
                 VALUES(?1,?2,?3,?4,?5,?6)",
            )?;
            let mut page_insert = transaction
                .prepare("INSERT INTO pages(chapter_id,position,document) VALUES(?1,?2,?3)")?;
            let mut download_insert = transaction.prepare(
                "INSERT INTO downloads(chapter_id,book_id,state,document)
                 VALUES(?1,?2,'completed',?3)",
            )?;
            for (index, (id, paths)) in chapters.iter().enumerate() {
                let chapter = Chapter {
                    id: id.clone(),
                    book_id: book.id.clone(),
                    source_id: book.source_id.clone(),
                    remote_id: id.clone(),
                    number: index as f64 + 1.0,
                    page_count: paths.len() as u32,
                    downloaded: true,
                    ..Chapter::default()
                };
                chapter_insert.execute(rusqlite::params![
                    chapter.id,
                    chapter.book_id,
                    chapter.source_id,
                    chapter.remote_id,
                    chapter.number,
                    serde_json::to_string(&chapter)?,
                ])?;
                for (position, path) in paths.iter().enumerate() {
                    let page = Page {
                        local_path: Some(path.clone()),
                        ..Page::default()
                    };
                    page_insert.execute(rusqlite::params![
                        chapter.id,
                        position as u32,
                        serde_json::to_string(&page)?,
                    ])?;
                }
                let job = DownloadJob {
                    chapter_id: chapter.id.clone(),
                    book_id: chapter.book_id.clone(),
                    state: "completed".into(),
                    completed: chapter.page_count,
                    total: chapter.page_count,
                    ..DownloadJob::default()
                };
                download_insert.execute(rusqlite::params![
                    job.chapter_id,
                    job.book_id,
                    serde_json::to_string(&job)?,
                ])?;
            }
        }
        transaction.commit()?;
        Ok(())
    }

    fn write_benchmark_record(
        output: &mut Option<fs::File>,
        record: &serde_json::Value,
    ) -> Result<()> {
        use std::io::Write;

        if let Some(output) = output {
            serde_json::to_writer(&mut *output, record)?;
            output.write_all(b"\n")?;
            output.flush()?;
        } else {
            println!("{record}");
        }
        Ok(())
    }

    /// Fixture construction and output are deliberately outside each timing.
    /// This measures the production observer and merge, never the UI, source
    /// dispatcher, image decoder, power lifecycle, or fixture write latency.
    #[test]
    #[ignore = "Requires an exclusive remote performance window and real filesystem fixtures"]
    fn benchmark_completed_download_usage_production() -> Result<()> {
        let samples = match std::env::var("AIDOKU_USAGE_BENCH_SAMPLES") {
            Ok(value) => value
                .parse::<usize>()
                .context("Parse the usage benchmark sample count")?,
            Err(std::env::VarError::NotPresent) => 5,
            Err(error) => return Err(error).context("Read the usage benchmark sample count"),
        };
        ensure!(
            (1..=64).contains(&samples),
            "The usage benchmark sample count must be between 1 and 64"
        );
        let fixture_root = std::env::var_os("AIDOKU_USAGE_BENCH_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        fs::create_dir_all(&fixture_root).context("Create the usage benchmark fixture root")?;
        let output_path = std::env::var_os("AIDOKU_USAGE_BENCH_OUTPUT").map(PathBuf::from);
        let mut output = output_path
            .as_ref()
            .map(|path| {
                fs::OpenOptions::new()
                    .create_new(true)
                    .write(true)
                    .open(path)
                    .with_context(|| {
                        format!("Create the fresh benchmark report {}", path.display())
                    })
            })
            .transpose()?;
        let scope = serde_json::json!({
            "source": "src/app/download_usage.rs",
            "collector": "production scan_usage with Store::open_read_only and NetworkControl epoch/generation guards",
            "duration_ns": "Production scan only, including read-only connection open, queries, canonicalization, and file metadata",
            "merge_duration_ns": "Production UsageTracker::accept with no worker-held snapshot reference",
            "excluded": ["fixture creation", "fixture mutation", "fixture deletion", "report serialization", "guard creation", "source dispatch", "thread creation", "UI", "decoding", "wake repaint"],
            "cache_state": "Newly created fixture; operating-system file caches are not dropped",
            "file_checks_counter": "fs::metadata calls; canonicalization calls are not separately counted",
            "fixture_builder": "Batched test-only inserts on a production Store schema with production page-counter triggers and real PNG files",
            "aggregation": "Individual samples only; no percentile claims"
        });
        for scale in [1_000_usize, 5_000] {
            for sample in 1..=samples {
                let prefix = format!("aidoku-usage-{scale}-{sample}-");
                let directory = tempfile::Builder::new()
                    .prefix(&prefix)
                    .tempdir_in(&fixture_root)?;
                let database = directory.path().join("library.sqlite3");
                let files = directory.path().join("images");
                fs::create_dir(&files)?;
                let template = directory.path().join("template.png");
                image::GrayImage::from_pixel(8, 8, image::Luma([127])).save(&template)?;
                let image_bytes = fs::read(&template)?;
                fs::remove_file(template)?;
                let image_size = image_bytes.len() as u64;
                let shared = files.join("base-00000.png");
                let mut missing = Vec::new();
                let mut old_chapters = Vec::with_capacity(scale);
                for index in 0..scale {
                    let path = files.join(format!("base-{index:05}.png"));
                    fs::write(&path, &image_bytes)?;
                    let mut paths = vec![path, shared.clone()];
                    if index % 100 == 0 {
                        let absent = files.join(format!("missing-{index:05}.png"));
                        fs::write(&absent, &image_bytes)?;
                        paths.push(absent.clone());
                        missing.push(absent);
                    }
                    old_chapters.push((format!("base-{index:05}"), paths));
                }
                let store = Store::open(&database)?;
                let book = Book {
                    id: "usage-benchmark".into(),
                    source_id: "benchmark".into(),
                    remote_id: "usage-benchmark".into(),
                    title: "Completed image usage benchmark".into(),
                    chapter_count: scale as u32,
                    unread: scale as u32,
                    in_library: true,
                    ..Book::default()
                };
                store.upsert_book(&book)?;
                insert_benchmark_chapters(&store, &book, &old_chapters)?;
                // Simulate images disappearing after their chapters completed.
                // Their references remain in the manifest and must be skipped.
                for path in &missing {
                    fs::remove_file(path)?;
                }
                let old_page_rows = scale * 2 + missing.len();
                let unique_stored_paths = scale + missing.len();
                let initial_bytes = scale as u64 * image_size;
                let mut tracker = UsageTracker::default();
                let request = tracker
                    .next_request(Instant::now())
                    .context("Queue the initial usage benchmark scan")?;
                let guard = guard_for(&tracker, directory.path());

                let scan_start = Instant::now();
                let rebuilt = scan_usage(&database, request, &guard, None)?;
                let scan_duration = scan_start.elapsed();
                let stats = rebuilt.stats;
                let observed_files = rebuilt.snapshot.files.len();
                let observed_chapters = rebuilt.snapshot.completed_chapters.len();
                let merge_start = Instant::now();
                let total = tracker
                    .accept(rebuilt)
                    .context("Accept the initial usage benchmark scan")?;
                let merge_duration = merge_start.elapsed();
                let rebuild_checks = total == initial_bytes
                    && observed_files == scale
                    && observed_chapters == scale
                    && stats.paths_observed == unique_stored_paths as u64
                    && stats.file_checks == scale as u64
                    && stats.path_queries == unique_stored_paths.div_ceil(PATH_BATCH) as u64
                    && stats.chapter_queries == 1
                    && stats.manifest_windows == 0;
                write_benchmark_record(
                    &mut output,
                    &serde_json::json!({
                        "schemaVersion": 1,
                        "benchmark": "completed_download_usage",
                        "scale": scale,
                        "sample": sample,
                        "samples": samples,
                        "kind": "rebuild",
                        "duration_ns": scan_duration.as_nanos() as u64,
                        "merge_duration_ns": merge_duration.as_nanos() as u64,
                        "stats": stats,
                        "totalbytes": total,
                        "checks_match": rebuild_checks,
                        "fixture": {
                            "completed_chapters": scale,
                            "manifest_page_rows": old_page_rows,
                            "unique_stored_paths": unique_stored_paths,
                            "unique_existing_paths": scale,
                            "unique_existing_bytes": initial_bytes,
                            "duplicate_path_references": scale,
                            "missing_paths_removed_after_completion": missing.len(),
                            "keyset_batch": PATH_BATCH
                        },
                        "source_work_scope": scope
                    }),
                )?;
                ensure!(
                    rebuild_checks,
                    "The production rebuild benchmark checks failed"
                );

                // Every sample gets eight newly completed chapters and its own
                // baseline snapshot. Repeats never measure an empty no-op.
                let mut new_chapters = Vec::with_capacity(CHAPTER_BATCH);
                for index in 0..CHAPTER_BATCH {
                    let path = files.join(format!("append-{index:02}.png"));
                    fs::write(&path, &image_bytes)?;
                    new_chapters.push((format!("append-{index:02}"), vec![shared.clone(), path]));
                }
                insert_benchmark_chapters(&store, &book, &new_chapters)?;
                for (chapter_id, _) in &new_chapters {
                    tracker.queue_completed(chapter_id);
                }
                let request = tracker
                    .next_request(Instant::now())
                    .context("Queue the incremental usage benchmark scan")?;
                let guard = guard_for(&tracker, directory.path());
                let snapshot_identity = Arc::as_ptr(tracker.snapshot.as_ref().unwrap());
                let known = Arc::clone(tracker.snapshot.as_ref().unwrap());
                let shared_snapshot_identity =
                    Arc::ptr_eq(tracker.snapshot.as_ref().unwrap(), &known);

                let scan_start = Instant::now();
                let appended = scan_usage(&database, request, &guard, Some(known.as_ref()))?;
                let scan_duration = scan_start.elapsed();
                let stats = appended.stats;
                let observed_new_files = appended.snapshot.files.len();
                let observed_new_chapters = appended.snapshot.completed_chapters.len();
                // The production FnOnce drops this reference before tx.send.
                drop(known);
                let merge_start = Instant::now();
                let total = tracker
                    .accept(appended)
                    .context("Accept the incremental usage benchmark scan")?;
                let merge_duration = merge_start.elapsed();
                let snapshot_identity_preserved = std::ptr::eq(
                    snapshot_identity,
                    Arc::as_ptr(tracker.snapshot.as_ref().unwrap()),
                );
                let expected_total = (scale + CHAPTER_BATCH) as u64 * image_size;
                let append_checks = total == expected_total
                    && observed_new_files == CHAPTER_BATCH
                    && observed_new_chapters == CHAPTER_BATCH
                    && stats.path_queries == 0
                    && stats.chapter_queries == CHAPTER_BATCH as u64
                    && stats.manifest_windows == CHAPTER_BATCH as u64
                    && stats.paths_observed == (CHAPTER_BATCH * 2) as u64
                    && stats.file_checks == CHAPTER_BATCH as u64
                    && tracker.pending_chapters.is_empty()
                    && shared_snapshot_identity
                    && snapshot_identity_preserved;
                write_benchmark_record(
                    &mut output,
                    &serde_json::json!({
                        "schemaVersion": 1,
                        "benchmark": "completed_download_usage",
                        "scale": scale,
                        "sample": sample,
                        "samples": samples,
                        "kind": "append8",
                        "duration_ns": scan_duration.as_nanos() as u64,
                        "merge_duration_ns": merge_duration.as_nanos() as u64,
                        "stats": stats,
                        "totalbytes": total,
                        "checks_match": append_checks,
                        "shared_snapshot_identity": shared_snapshot_identity,
                        "snapshot_identity_preserved": snapshot_identity_preserved,
                        "fixture": {
                            "existing_completed_chapters": scale,
                            "existing_unique_paths": scale,
                            "existing_unique_bytes": initial_bytes,
                            "new_completed_chapters": CHAPTER_BATCH,
                            "new_manifest_page_rows": CHAPTER_BATCH * 2,
                            "new_unique_existing_paths": CHAPTER_BATCH,
                            "new_unique_existing_bytes": CHAPTER_BATCH as u64 * image_size,
                            "new_references_to_existing_shared_path": CHAPTER_BATCH,
                            "unique_existing_paths_after": scale + CHAPTER_BATCH,
                            "unique_existing_bytes_after": expected_total,
                            "manifest_window": MANIFEST_BATCH
                        },
                        "source_work_scope": scope
                    }),
                )?;
                ensure!(
                    append_checks,
                    "The production append benchmark checks failed"
                );
            }
        }
        if let Some(path) = output_path {
            println!("aidoku usage benchmark report: {}", path.display());
        }
        Ok(())
    }
}
