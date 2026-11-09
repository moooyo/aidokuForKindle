//! On-demand storage details with real file sizes and bounded database reads.

use std::{
    collections::{HashMap, hash_map::Entry},
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
use crate::{
    cache::{CacheManager, StorageArtifact},
    sources::NetworkControl,
    store::{Store, storage_queries::StorageBookHeader},
};

const STORAGE_BATCH: usize = 128;
const RETRY_DELAY: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub(super) struct StorageBookUsage {
    pub id: String,
    pub title: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize)]
pub(super) struct StorageDetailsSnapshot {
    /// Completed chapter paths, deduplicated across every book.
    pub downloaded_bytes: u64,
    /// Unpinned managed artifacts that have no cover alias or download path.
    /// An unclassified historical alias makes both cache categories unknown.
    pub image_cache_bytes: Option<u64>,
    /// Unpinned managed artifacts with a real, classified cover alias.
    pub cover_bytes: Option<u64>,
    /// Available bytes on the filesystem containing the application's cache.
    pub free_bytes: Option<u64>,
    /// Per-book associated bytes. Shared files can contribute to several rows,
    /// so these rows must not be summed to obtain the global download total.
    pub books: Vec<StorageBookUsage>,
}

impl StorageDetailsSnapshot {
    pub(super) fn app_bytes(&self) -> Option<u64> {
        self.downloaded_bytes
            .checked_add(self.image_cache_bytes?)?
            .checked_add(self.cover_bytes?)
    }

    /// This scale is tracked application image data plus available space. It
    /// does not describe total device capacity or other applications' files.
    pub(super) fn scale_bytes(&self) -> Option<u64> {
        self.app_bytes()?.checked_add(self.free_bytes?)
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize)]
pub(super) struct StorageScanStats {
    pub book_queries: u64,
    pub chapter_queries: u64,
    pub manifest_queries: u64,
    pub cache_queries: u64,
    pub paths_observed: u64,
    pub path_resolutions: u64,
    pub file_checks: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct StorageRequest {
    generation: u64,
    sequence: u64,
}

#[derive(Debug)]
pub(super) struct StorageDetailsResult {
    request: StorageRequest,
    epoch: u64,
    snapshot: StorageDetailsSnapshot,
    pub stats: StorageScanStats,
}

#[derive(Debug, Clone, Copy)]
struct Inflight {
    job_id: u64,
    generation: u64,
}

pub(super) struct StorageDetailsTracker {
    generation: u64,
    generation_token: Arc<AtomicU64>,
    sequence: u64,
    accepted_sequence: Option<u64>,
    visible: bool,
    dirty: bool,
    snapshot: Option<StorageDetailsSnapshot>,
    inflight: Option<Inflight>,
    retry_after: Option<Instant>,
}

impl Default for StorageDetailsTracker {
    fn default() -> Self {
        Self {
            generation: 0,
            generation_token: Arc::new(AtomicU64::new(0)),
            sequence: 0,
            accepted_sequence: None,
            visible: false,
            dirty: true,
            snapshot: None,
            inflight: None,
            retry_after: None,
        }
    }
}

impl StorageDetailsTracker {
    /// Projection reads only this accepted model, never a Store or filesystem.
    pub(super) fn snapshot(&self) -> Option<&StorageDetailsSnapshot> {
        self.snapshot.as_ref()
    }

    pub(super) fn is_refreshing(&self) -> bool {
        self.visible && (self.inflight.is_some() || self.dirty && self.retry_after.is_none())
    }

    pub(super) fn refresh_failed(&self) -> bool {
        self.dirty && self.retry_after.is_some() && self.inflight.is_none()
    }

    fn invalidate(&mut self) {
        self.generation = self.generation.wrapping_add(1);
        self.generation_token
            .store(self.generation, Ordering::Release);
        self.dirty = true;
        self.retry_after = None;
    }

    fn set_visible(&mut self, visible: bool) {
        if self.visible == visible {
            return;
        }
        self.visible = visible;
        // Leaving and re-entering the section create distinct requests even
        // when no file changes. Keep the last accepted model while refreshing.
        self.invalidate();
    }

    fn next_request(&mut self, now: Instant) -> Option<StorageRequest> {
        if !self.visible
            || !self.dirty
            || self.inflight.is_some()
            || self.retry_after.is_some_and(|retry| now < retry)
        {
            return None;
        }
        self.sequence = self.sequence.wrapping_add(1);
        Some(StorageRequest {
            generation: self.generation,
            sequence: self.sequence,
        })
    }

    fn started(&mut self, job_id: u64, request: StorageRequest) {
        self.inflight = Some(Inflight {
            job_id,
            generation: request.generation,
        });
    }

    fn finish(&mut self, job_id: u64, now: Instant) -> bool {
        let Some(inflight) = self.inflight.filter(|inflight| inflight.job_id == job_id) else {
            return false;
        };
        self.inflight = None;
        if self.dirty && inflight.generation == self.generation {
            self.retry_after = Some(now + RETRY_DELAY);
        }
        true
    }

    fn accept(&mut self, result: StorageDetailsResult) -> bool {
        if !self.visible
            || result.request.generation != self.generation
            || result.request.sequence != self.sequence
            || self.accepted_sequence == Some(result.request.sequence)
        {
            return false;
        }
        let changed = self.snapshot.as_ref() != Some(&result.snapshot);
        self.snapshot = Some(result.snapshot);
        self.accepted_sequence = Some(result.request.sequence);
        self.dirty = false;
        self.retry_after = None;
        changed
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
            "The storage details request was superseded"
        );
        Ok(())
    }

    fn yield_batch(&self) -> Result<()> {
        self.check()?;
        std::thread::yield_now();
        self.check()
    }
}

impl App {
    /// Call on every route/section transition, before returning control to the
    /// event loop. This is RAM-only and cancels work for a hidden section.
    pub(super) fn sync_storage_details_visibility(&mut self) {
        let visible = self.storage_details_visible();
        self.storage_details.set_visible(visible);
    }

    /// Destructive changes, completed chapters, accepted imports, cache clears,
    /// cover publication, and wake reconciliation invalidate the model. An
    /// invalidation never discards the last accepted numbers or book ranking.
    pub(super) fn invalidate_storage_details(&mut self) {
        self.storage_details.invalidate();
    }

    pub(super) fn storage_details_visible(&self) -> bool {
        self.route == "settings"
            && self.interactions.settings_section == "storage"
            && !self.shutting_down
            && !self.suspended
            && !self.resume_frame_pending
    }

    /// A separate, on-demand worker performs the first ranking pass. Existing
    /// DownloadUsage accounting remains incremental and serves its usual labels.
    pub(super) fn poll_storage_details(&mut self) -> Result<()> {
        self.sync_storage_details_visibility();
        let Some(request) = self.storage_details.next_request(Instant::now()) else {
            return Ok(());
        };
        let database = self.data_dir.join("library.sqlite3");
        let cache = self.disk_cache.clone();
        let guard = ScanGuard {
            network: self.network_control.clone(),
            epoch: self.network_control.epoch(),
            generation: Arc::clone(&self.storage_details.generation_token),
            expected_generation: request.generation,
        };
        match self.spawn_local_job(false, JobKind::StorageDetails, move || {
            scan_storage_details(&database, &cache, request, &guard).map(JobResult::StorageDetails)
        }) {
            Ok(job_id) => self.storage_details.started(job_id, request),
            Err(error) => {
                self.storage_details.retry_after = Some(Instant::now() + RETRY_DELAY);
                return Err(error).context("Start storage details accounting");
            }
        }
        Ok(())
    }

    /// Release the slot before lifecycle-stale job messages are discarded.
    pub(super) fn finish_storage_details_job(&mut self, job_id: u64) -> bool {
        self.storage_details.finish(job_id, Instant::now())
    }

    pub(super) fn accept_storage_details(&mut self, result: StorageDetailsResult) -> bool {
        self.sync_storage_details_visibility();
        if !self.storage_details_visible()
            || self.network_control.check_epoch(result.epoch).is_err()
        {
            return false;
        }
        let changed = self.storage_details.accept(result);
        if changed {
            self.background_ui_dirty = true;
        }
        changed
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FileUse {
    Download,
    Image,
    Cover,
    Unknown,
}

#[derive(Debug)]
struct FileRecord {
    bytes: u64,
    last_book: u64,
    usage: FileUse,
}

#[derive(Debug, Default)]
struct FileIndex {
    files: HashMap<PathBuf, FileRecord>,
    download_bytes: u64,
}

impl FileIndex {
    fn observe_download(
        &mut self,
        path: PathBuf,
        book_sequence: u64,
        stats: &mut StorageScanStats,
        guard: &ScanGuard,
    ) -> Result<u64> {
        guard.check()?;
        stats.paths_observed += 1;
        if let Some(record) = self.files.get_mut(&path) {
            return Ok(book_contribution(record, book_sequence));
        }
        let Some(canonical) = resolve_path(&path, stats, guard)? else {
            return Ok(0);
        };
        if let Some(record) = self.files.get_mut(&canonical) {
            return Ok(book_contribution(record, book_sequence));
        }
        let Some(bytes) = inspect_file(&canonical, stats, guard)? else {
            return Ok(0);
        };
        if bytes == 0 || bytes > crate::imaging::MAX_PAGE_BYTES {
            return Ok(0);
        }
        self.download_bytes = self.download_bytes.saturating_add(bytes);
        self.files.insert(
            canonical,
            FileRecord {
                bytes,
                last_book: book_sequence,
                usage: FileUse::Download,
            },
        );
        Ok(bytes)
    }

    fn observe_cache(
        &mut self,
        artifact: StorageArtifact,
        stats: &mut StorageScanStats,
        guard: &ScanGuard,
    ) -> Result<()> {
        guard.check()?;
        // Pins may include an unfinished download or a transient archive. They
        // are never presented as evictable image or cover cache bytes.
        if artifact.pinned {
            return Ok(());
        }
        let usage = match artifact.cover {
            Some(true) => FileUse::Cover,
            Some(false) => FileUse::Image,
            None => FileUse::Unknown,
        };
        let Some((canonical, bytes)) = inspect_cache_file(&artifact.path, stats, guard)? else {
            return Ok(());
        };
        match self.files.entry(canonical) {
            Entry::Occupied(mut entry) => merge_cache_use(entry.get_mut(), usage),
            Entry::Vacant(entry) => {
                entry.insert(FileRecord {
                    bytes,
                    last_book: 0,
                    usage,
                });
            }
        }
        Ok(())
    }

    fn cache_bytes(&self, guard: &ScanGuard) -> Result<(Option<u64>, Option<u64>)> {
        let mut images = 0_u64;
        let mut covers = 0_u64;
        let mut known = true;
        for (index, record) in self.files.values().enumerate() {
            if index % STORAGE_BATCH == 0 {
                guard.check()?;
            }
            match record.usage {
                FileUse::Download => {}
                FileUse::Image => images = images.saturating_add(record.bytes),
                FileUse::Cover => covers = covers.saturating_add(record.bytes),
                FileUse::Unknown => known &= record.bytes == 0,
            }
        }
        guard.check()?;
        Ok(if known {
            (Some(images), Some(covers))
        } else {
            (None, None)
        })
    }
}

fn book_contribution(record: &mut FileRecord, book_sequence: u64) -> u64 {
    if record.last_book == book_sequence {
        return 0;
    }
    record.last_book = book_sequence;
    record.bytes
}

fn merge_cache_use(record: &mut FileRecord, usage: FileUse) {
    record.usage = match (record.usage, usage) {
        (FileUse::Download, _) => FileUse::Download,
        (FileUse::Cover, _) | (_, FileUse::Cover) => FileUse::Cover,
        (FileUse::Unknown, _) | (_, FileUse::Unknown) => FileUse::Unknown,
        _ => FileUse::Image,
    };
}

fn resolve_path(
    path: &Path,
    stats: &mut StorageScanStats,
    guard: &ScanGuard,
) -> Result<Option<PathBuf>> {
    guard.check()?;
    stats.path_resolutions += 1;
    let resolved = fs::canonicalize(path);
    guard.check()?;
    match resolved {
        Ok(path) => Ok(Some(path)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => {
            Err(error).with_context(|| format!("Resolve storage image {}", path.display()))
        }
    }
}

fn inspect_file(
    path: &Path,
    stats: &mut StorageScanStats,
    guard: &ScanGuard,
) -> Result<Option<u64>> {
    guard.check()?;
    stats.file_checks += 1;
    let inspected = fs::metadata(path);
    guard.check()?;
    match inspected {
        Ok(metadata) => Ok(metadata.is_file().then(|| metadata.len())),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => {
            Err(error).with_context(|| format!("Inspect storage image {}", path.display()))
        }
    }
}

fn inspect_cache_file(
    path: &Path,
    stats: &mut StorageScanStats,
    guard: &ScanGuard,
) -> Result<Option<(PathBuf, u64)>> {
    guard.check()?;
    stats.file_checks += 1;
    let inspected = fs::symlink_metadata(path);
    guard.check()?;
    let metadata = match inspected {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => {
            return Err(error)
                .with_context(|| format!("Inspect cache storage image {}", path.display()));
        }
    };
    ensure!(
        !metadata.file_type().is_symlink(),
        "A cache storage image was replaced by a symbolic link"
    );
    if !metadata.is_file() {
        return Ok(None);
    }
    let Some(canonical) = resolve_path(path, stats, guard)? else {
        return Ok(None);
    };
    // CacheManager constructs candidates below an already canonical root. A
    // different resolved identity also rejects a replaced parent directory.
    ensure!(
        canonical == path,
        "A cache storage image directory was replaced by a symbolic link"
    );
    Ok(Some((canonical, metadata.len())))
}

fn scan_storage_details(
    database: &Path,
    cache: &CacheManager,
    request: StorageRequest,
    guard: &ScanGuard,
) -> Result<StorageDetailsResult> {
    guard.check()?;
    let mut stats = StorageScanStats::default();
    let (mut index, books) = scan_downloaded_books(database, guard, &mut stats)?;
    let mut cursor = None;
    loop {
        guard.check()?;
        stats.cache_queries += 1;
        // This API copies at most 128 headers and drops the cache lock and its
        // SQLite statement before any canonicalize() or metadata() operation.
        let artifacts = cache.storage_artifacts_page(cursor.as_deref(), STORAGE_BATCH)?;
        guard.check()?;
        if artifacts.is_empty() {
            break;
        }
        cursor = artifacts.last().map(|artifact| artifact.id.clone());
        let final_batch = artifacts.len() < STORAGE_BATCH;
        for artifact in artifacts {
            index.observe_cache(artifact, &mut stats, guard)?;
        }
        guard.yield_batch()?;
        if final_batch {
            break;
        }
    }
    let (image_cache_bytes, cover_bytes) = index.cache_bytes(guard)?;
    guard.check()?;
    let free_bytes = Some(
        cache
            .available_bytes()
            .context("Observe storage available space")?,
    );
    guard.check()?;
    Ok(StorageDetailsResult {
        request,
        epoch: guard.epoch,
        snapshot: StorageDetailsSnapshot {
            downloaded_bytes: index.download_bytes,
            image_cache_bytes,
            cover_bytes,
            free_bytes,
            books,
        },
        stats,
    })
}

fn scan_downloaded_books(
    database: &Path,
    guard: &ScanGuard,
    stats: &mut StorageScanStats,
) -> Result<(FileIndex, Vec<StorageBookUsage>)> {
    guard.check()?;
    let store = Store::open_read_only(database)?;
    guard.check()?;
    let mut index = FileIndex::default();
    let mut books = Vec::new();
    let mut cursor = None;
    let mut book_sequence = 0_u64;
    loop {
        guard.check()?;
        stats.book_queries += 1;
        let headers = store.storage_books_page(cursor.as_deref(), STORAGE_BATCH)?;
        guard.check()?;
        if headers.is_empty() {
            break;
        }
        cursor = headers.last().map(|book| book.id.clone());
        let final_batch = headers.len() < STORAGE_BATCH;
        for book in headers {
            guard.check()?;
            book_sequence += 1;
            let bytes = scan_book(&store, &book, book_sequence, &mut index, stats, guard)?;
            if bytes > 0 {
                books.push(StorageBookUsage {
                    id: book.id,
                    title: book.title,
                    bytes,
                });
            }
        }
        guard.yield_batch()?;
        if final_batch {
            break;
        }
    }
    guard.check()?;
    books.sort_by(|first, second| {
        second
            .bytes
            .cmp(&first.bytes)
            .then_with(|| first.title.cmp(&second.title))
            .then_with(|| first.id.cmp(&second.id))
    });
    guard.check()?;
    Ok((index, books))
}

fn scan_book(
    store: &Store,
    book: &StorageBookHeader,
    book_sequence: u64,
    index: &mut FileIndex,
    stats: &mut StorageScanStats,
    guard: &ScanGuard,
) -> Result<u64> {
    let mut total = 0_u64;
    let mut cursor = None;
    loop {
        guard.check()?;
        stats.chapter_queries += 1;
        let chapters = store.storage_chapters_page(&book.id, cursor.as_deref(), STORAGE_BATCH)?;
        guard.check()?;
        if chapters.is_empty() {
            break;
        }
        cursor = chapters.last().map(|chapter| chapter.id.clone());
        let final_batch = chapters.len() < STORAGE_BATCH;
        for chapter in chapters {
            guard.check()?;
            if !chapter.downloaded {
                continue;
            }
            let mut position = None;
            loop {
                guard.check()?;
                stats.manifest_queries += 1;
                let pages = store.storage_manifest_page(&chapter.id, position, STORAGE_BATCH)?;
                guard.check()?;
                if pages.is_empty() {
                    break;
                }
                position = pages.last().map(|page| page.position);
                let final_manifest_batch = pages.len() < STORAGE_BATCH;
                for page in pages {
                    if let Some(path) = page.path {
                        total = total.saturating_add(index.observe_download(
                            path,
                            book_sequence,
                            stats,
                            guard,
                        )?);
                    }
                }
                guard.yield_batch()?;
                if final_manifest_batch {
                    break;
                }
            }
        }
        guard.yield_batch()?;
        if final_batch {
            break;
        }
    }
    Ok(total)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{Book, Chapter, Page};

    fn guard_for(tracker: &StorageDetailsTracker, root: &Path) -> ScanGuard {
        let network = NetworkControl::open(root).unwrap();
        ScanGuard {
            epoch: network.epoch(),
            network,
            generation: Arc::clone(&tracker.generation_token),
            expected_generation: tracker.generation,
        }
    }

    fn result_for(request: StorageRequest, bytes: u64) -> StorageDetailsResult {
        StorageDetailsResult {
            request,
            epoch: 0,
            snapshot: StorageDetailsSnapshot {
                downloaded_bytes: bytes,
                image_cache_bytes: Some(0),
                cover_bytes: Some(0),
                free_bytes: Some(100),
                books: Vec::new(),
            },
            stats: StorageScanStats::default(),
        }
    }

    fn add_local_chapter(
        store: &Store,
        book_id: &str,
        title: &str,
        chapter_id: &str,
        paths: &[PathBuf],
    ) {
        if store.book(book_id).unwrap().is_none() {
            store
                .upsert_book(&Book {
                    id: book_id.into(),
                    source_id: "local".into(),
                    remote_id: book_id.into(),
                    title: title.into(),
                    in_library: true,
                    ..Book::default()
                })
                .unwrap();
        }
        store
            .save_chapters(
                book_id,
                &[Chapter {
                    id: chapter_id.into(),
                    source_id: "local".into(),
                    remote_id: chapter_id.into(),
                    book_id: book_id.into(),
                    ..Chapter::default()
                }],
            )
            .unwrap();
        store
            .save_pages(
                chapter_id,
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

    fn artifact(id: &str, path: &Path, pinned: bool, cover: Option<bool>) -> StorageArtifact {
        StorageArtifact {
            id: id.into(),
            path: fs::canonicalize(path).unwrap_or_else(|_| path.into()),
            pinned,
            cover,
        }
    }

    #[test]
    fn ranking_uses_actual_bytes_and_deduplicates_shared_and_missing_paths() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("library.sqlite3");
        let store = Store::open(&database).unwrap();
        let shared = directory.path().join("shared.bin");
        let large = directory.path().join("large.bin");
        let missing = directory.path().join("missing.bin");
        let ignored = directory.path().join("ignored.bin");
        fs::write(&shared, [1; 53]).unwrap();
        fs::write(&large, [2; 91]).unwrap();
        fs::write(&missing, [3; 30]).unwrap();
        fs::write(&ignored, [4; 200]).unwrap();
        fs::create_dir(directory.path().join("alias")).unwrap();
        let alias = directory.path().join("alias").join("..").join("shared.bin");
        add_local_chapter(
            &store,
            "small",
            "More pages",
            "small-one",
            &[shared.clone(), alias, shared.clone(), missing.clone()],
        );
        add_local_chapter(
            &store,
            "small",
            "More pages",
            "small-two",
            std::slice::from_ref(&shared),
        );
        add_local_chapter(
            &store,
            "large",
            "Fewer pages",
            "large-one",
            &[large, shared],
        );
        add_local_chapter(&store, "ignored", "Unfinished", "ignored-one", &[ignored]);
        store.set_chapter_downloaded("ignored-one", false).unwrap();
        fs::remove_file(missing).unwrap();
        let tracker = StorageDetailsTracker::default();
        let guard = guard_for(&tracker, directory.path());
        let mut stats = StorageScanStats::default();
        let (index, books) = scan_downloaded_books(&database, &guard, &mut stats).unwrap();
        assert_eq!(index.download_bytes, 144);
        assert_eq!(index.files.len(), 2);
        assert_eq!(stats.file_checks, 2);
        assert_eq!(books.len(), 2);
        assert_eq!((books[0].id.as_str(), books[0].bytes), ("large", 144));
        assert_eq!((books[1].id.as_str(), books[1].bytes), ("small", 53));
        assert_eq!(books[0].title, "Fewer pages");
        assert!(books.iter().map(|book| book.bytes).sum::<u64>() > index.download_bytes);
    }

    #[test]
    fn a_large_manifest_is_read_in_bounded_windows_without_recounting_a_path() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("library.sqlite3");
        let store = Store::open(&database).unwrap();
        let image = directory.path().join("shared.bin");
        fs::write(&image, [1; 19]).unwrap();
        add_local_chapter(&store, "book", "Book", "chapter", &vec![image; 260]);
        let tracker = StorageDetailsTracker::default();
        let guard = guard_for(&tracker, directory.path());
        let mut stats = StorageScanStats::default();
        let (index, books) = scan_downloaded_books(&database, &guard, &mut stats).unwrap();
        assert_eq!(stats.book_queries, 1);
        assert_eq!(stats.chapter_queries, 1);
        assert_eq!(stats.manifest_queries, 3);
        assert_eq!(stats.paths_observed, 260);
        assert_eq!(stats.file_checks, 1);
        assert_eq!(index.download_bytes, 19);
        assert_eq!(books[0].bytes, 19);
    }

    #[test]
    fn cache_categories_exclude_downloads_pins_and_duplicate_alias_targets() {
        let directory = tempfile::tempdir().unwrap();
        let download = directory.path().join("download.bin");
        let image = directory.path().join("image.bin");
        let cover = directory.path().join("cover.bin");
        let pinned = directory.path().join("pinned.bin");
        let unknown = directory.path().join("unknown.bin");
        fs::write(&download, [1; 10]).unwrap();
        fs::write(&image, [1; 30]).unwrap();
        fs::write(&cover, [1; 20]).unwrap();
        fs::write(&pinned, [1; 200]).unwrap();
        fs::write(&unknown, [1; 7]).unwrap();
        let tracker = StorageDetailsTracker::default();
        let guard = guard_for(&tracker, directory.path());
        let mut stats = StorageScanStats::default();
        let mut index = FileIndex::default();
        index
            .observe_download(download.clone(), 1, &mut stats, &guard)
            .unwrap();
        for item in [
            artifact("download", &download, false, Some(true)),
            artifact("image", &image, false, Some(false)),
            artifact("image-again", &image, false, Some(false)),
            artifact("cover", &cover, false, Some(true)),
            artifact("cover-again", &cover, false, Some(true)),
            artifact("pinned", &pinned, true, None),
            artifact(
                "missing",
                &directory.path().join("missing.bin"),
                false,
                None,
            ),
        ] {
            index.observe_cache(item, &mut stats, &guard).unwrap();
        }
        assert_eq!(index.download_bytes, 10);
        assert_eq!(index.cache_bytes(&guard).unwrap(), (Some(30), Some(20)));
        index
            .observe_cache(
                artifact("unknown", &unknown, false, None),
                &mut stats,
                &guard,
            )
            .unwrap();
        assert_eq!(index.cache_bytes(&guard).unwrap(), (None, None));
        // A classified cover alias identifies the shared target even when
        // another old alias for that same artifact remains unclassified.
        index
            .observe_cache(
                artifact("known-cover", &unknown, false, Some(true)),
                &mut stats,
                &guard,
            )
            .unwrap();
        assert_eq!(index.cache_bytes(&guard).unwrap(), (Some(30), Some(27)));
    }

    #[test]
    fn section_and_generation_changes_reject_old_results_and_keep_last_good_data() {
        let mut tracker = StorageDetailsTracker::default();
        let now = Instant::now();
        assert!(tracker.next_request(now).is_none());
        tracker.set_visible(true);
        let initial = tracker.next_request(now).unwrap();
        assert!(tracker.accept(result_for(initial, 40)));
        tracker.invalidate();
        let stale = tracker.next_request(now).unwrap();
        tracker.started(7, stale);
        tracker.set_visible(false);
        assert!(!tracker.accept(result_for(stale, 90)));
        assert_eq!(tracker.snapshot().unwrap().downloaded_bytes, 40);
        assert!(tracker.next_request(now).is_none());
        tracker.set_visible(true);
        assert!(tracker.next_request(now).is_none());
        assert!(!tracker.finish(8, now));
        assert!(tracker.finish(7, now));
        let current = tracker.next_request(now).unwrap();
        assert!(!tracker.accept(result_for(stale, 90)));
        assert!(tracker.accept(result_for(current, 60)));
        assert!(!tracker.accept(result_for(current, 10)));
        assert_eq!(tracker.snapshot().unwrap().downloaded_bytes, 60);
        assert!(tracker.next_request(now).is_none());
    }

    #[test]
    fn failures_leave_the_request_dirty_and_delay_only_the_matching_generation() {
        let mut tracker = StorageDetailsTracker::default();
        tracker.set_visible(true);
        let now = Instant::now();
        let request = tracker.next_request(now).unwrap();
        tracker.started(1, request);
        assert!(tracker.finish(1, now));
        assert!(tracker.next_request(now).is_none());
        let retry = tracker.next_request(now + RETRY_DELAY).unwrap();
        tracker.started(2, retry);
        tracker.invalidate();
        assert!(tracker.finish(2, now));
        assert!(tracker.next_request(now).is_some());
    }

    #[test]
    fn refresh_state_is_projected_from_ram_during_initial_work_failures_and_retries() {
        let mut tracker = StorageDetailsTracker::default();
        let now = Instant::now();
        assert!(!tracker.is_refreshing());
        assert!(!tracker.refresh_failed());
        tracker.set_visible(true);
        assert!(tracker.is_refreshing());
        let request = tracker.next_request(now).unwrap();
        tracker.started(1, request);
        assert!(tracker.is_refreshing());
        assert!(!tracker.refresh_failed());
        tracker.finish(1, now);
        assert!(!tracker.is_refreshing());
        assert!(tracker.refresh_failed());
        let retry = tracker.next_request(now + RETRY_DELAY).unwrap();
        tracker.started(2, retry);
        assert!(tracker.is_refreshing());
        assert!(!tracker.refresh_failed());
        tracker.finish(2, now);
        tracker.accept(result_for(retry, 40));
        assert!(!tracker.is_refreshing());
        assert!(!tracker.refresh_failed());
    }

    #[cfg(unix)]
    #[test]
    fn cache_candidates_reject_symbolic_links_and_replaced_parent_directories() {
        let directory = tempfile::tempdir().unwrap();
        let root = fs::canonicalize(directory.path()).unwrap();
        let actual_directory = root.join("actual");
        fs::create_dir(&actual_directory).unwrap();
        let actual_file = actual_directory.join("image.bin");
        fs::write(&actual_file, [1; 19]).unwrap();
        let file_link = root.join("file-link.bin");
        let directory_link = root.join("directory-link");
        std::os::unix::fs::symlink(&actual_file, &file_link).unwrap();
        std::os::unix::fs::symlink(&actual_directory, &directory_link).unwrap();
        let tracker = StorageDetailsTracker::default();
        let guard = guard_for(&tracker, &root);
        let mut index = FileIndex::default();
        let mut stats = StorageScanStats::default();
        for path in [file_link, directory_link.join("image.bin")] {
            let error = index
                .observe_cache(
                    StorageArtifact {
                        id: "candidate".into(),
                        path,
                        pinned: false,
                        cover: Some(false),
                    },
                    &mut stats,
                    &guard,
                )
                .unwrap_err();
            assert!(error.to_string().contains("symbolic link"));
        }
        assert!(index.files.is_empty());
        assert_eq!(index.cache_bytes(&guard).unwrap(), (Some(0), Some(0)));
    }

    #[test]
    fn cancellation_is_checked_before_opening_the_database() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("not-created.sqlite");
        let mut tracker = StorageDetailsTracker::default();
        let guard = guard_for(&tracker, directory.path());
        tracker.invalidate();
        let error =
            scan_downloaded_books(&database, &guard, &mut StorageScanStats::default()).unwrap_err();
        assert!(error.to_string().contains("superseded"));
        let guard = guard_for(&tracker, directory.path());
        guard.network.suspend();
        let error =
            scan_downloaded_books(&database, &guard, &mut StorageScanStats::default()).unwrap_err();
        assert!(crate::sources::is_network_interrupted(&error));
        assert!(!database.exists());
    }

    #[test]
    fn the_bar_scale_requires_known_categories_and_includes_available_space() {
        let mut snapshot = StorageDetailsSnapshot {
            downloaded_bytes: 10,
            image_cache_bytes: Some(20),
            cover_bytes: Some(5),
            free_bytes: Some(100),
            books: Vec::new(),
        };
        assert_eq!(snapshot.app_bytes(), Some(35));
        assert_eq!(snapshot.scale_bytes(), Some(135));
        snapshot.free_bytes = None;
        assert_eq!(snapshot.app_bytes(), Some(35));
        assert_eq!(snapshot.scale_bytes(), None);
        snapshot.free_bytes = Some(100);
        snapshot.cover_bytes = None;
        assert_eq!(snapshot.app_bytes(), None);
        assert_eq!(snapshot.scale_bytes(), None);
    }
}
