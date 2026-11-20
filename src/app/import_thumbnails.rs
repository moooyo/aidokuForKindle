//! Prepare only the first local page of a recently imported, offscreen book.

use super::*;
use std::{sync::Mutex, thread::JoinHandle};

const MAX_PENDING: usize = 32;
const IDLE_DELAY: Duration = Duration::from_millis(500);
const QUEUE_LIFETIME: Duration = Duration::from_secs(90);

#[derive(Clone, PartialEq, Eq)]
struct Request {
    id: String,
    cover: String,
    expires: Instant,
}

struct Running {
    request: Request,
    cancel: imaging::DecodeCancellation,
    io: Arc<Mutex<()>>,
    worker: JoinHandle<()>,
}

#[derive(Default)]
pub(super) struct ImportThumbnails {
    pending: VecDeque<Request>,
    running: Option<Running>,
    not_before: Option<Instant>,
    batch_open: bool,
    observe_visible: bool,
    observed_set: Option<Vec<String>>,
}

impl ImportThumbnails {
    pub(super) fn enqueue(&mut self, book: &Book, now: Instant) {
        if book.source_id != "local" || !book.in_library || book.cover.is_empty() {
            return;
        }
        if let Some(active) = self.running.as_ref().filter(|r| r.request.id == book.id) {
            if active.request.cover == book.cover && !active.cancel.is_cancelled() {
                return;
            }
            active.cancel.cancel();
        }
        self.pending.retain(|r| r.id != book.id && r.expires > now);
        if self.pending.len() < MAX_PENDING {
            self.pending.push_back(Request {
                id: book.id.clone(),
                cover: book.cover.clone(),
                expires: now + QUEUE_LIFETIME,
            });
            self.not_before = Some(now + IDLE_DELAY);
            self.batch_open = true;
        }
    }

    pub(super) fn note_activity(&mut self) {
        if self.pending.is_empty() && self.running.is_none() && !self.observe_visible {
            return;
        }
        self.not_before = Some(Instant::now() + IDLE_DELAY);
        self.observe_visible = false;
        self.observed_set = None;
        if let Some(active) = &self.running {
            active.cancel.cancel();
        }
    }

    pub(super) fn yield_to_publication(&mut self) {
        if !self.pending.is_empty() {
            self.not_before = Some(Instant::now() + IDLE_DELAY);
        }
    }

    pub(super) fn cancel(&mut self) {
        self.note_activity();
        self.pending.clear();
    }

    /// The cache-clear worker waits here, off the UI thread, for an in-flight
    /// publication. If it runs first, the cancelled worker cannot start later.
    pub(super) fn cancel_for_clear(&mut self) -> Option<Arc<Mutex<()>>> {
        self.cancel();
        self.running.as_ref().map(|r| r.io.clone())
    }

    pub(super) fn drained(&self) -> bool {
        self.running.as_ref().is_none_or(|r| r.worker.is_finished())
    }

    fn finish(&mut self) {
        if self.drained()
            && let Some(previous) = self.running.take()
        {
            let _ = previous.worker.join();
        }
        if self.pending.is_empty() && self.running.is_none() && self.batch_open {
            self.batch_open = false;
            crate::reader_timing::mark("import_thumbnail_queue_drained");
        }
    }

    pub(super) fn imported(&mut self, nonempty: bool) {
        self.observe_visible = nonempty && crate::reader_timing::enabled();
        self.observed_set = None;
    }
}

impl App {
    pub(super) fn poll_import_thumbnails(&mut self) {
        self.import_thumbnails.finish();
        if !self.options.startup_options.import_thumbnail_idle {
            return;
        }
        if self.import_thumbnails.pending.is_empty() || self.import_thumbnails.running.is_some() {
            return;
        }
        let now = Instant::now();
        if self.is_starting()
            || self.shutting_down
            || self.suspended
            || self.resume_frame_pending
            || self.platform.startup_render_pending()
            || self.metadata_restore_active()
            || self.route == "reader"
            || !self.modal.is_empty()
            || self.busy
            || !self.active_jobs.is_empty()
            || self.capture_projection_pending()
            || self.downloads.active_count() != 0
        {
            self.import_thumbnails.yield_to_publication();
            return;
        }
        if self.import_thumbnails.not_before.is_some_and(|t| t > now) {
            return;
        }
        // The visible worker already owns these covers, including their retries.
        let visible = &self.cover_view;
        self.import_thumbnails
            .pending
            .retain(|r| r.expires > now && !visible.contains(&r.id));
        let Some(request) = self.import_thumbnails.pending.pop_front() else {
            self.import_thumbnails.finish();
            return;
        };
        let root = self.data_dir.clone();
        let cache = self.disk_cache.clone();
        let cancel = imaging::DecodeCancellation::new();
        let io = Arc::new(Mutex::new(()));
        let work = request.clone();
        let fence = cancel.clone();
        let lock = io.clone();
        let worker = std::thread::Builder::new()
            .name("aidoku-import-cover".into())
            .spawn(move || {
                crate::reader_timing::mark("import_thumbnail_started");
                let _io = lock
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                let result = imaging::with_background_decode(Some(&fence), || {
                    prepare(&root, &cache, &work, &fence)
                });
                crate::reader_timing::mark(match result {
                    Ok(Outcome::Persisted) => "import_thumbnail_persisted",
                    Ok(Outcome::Hit) => "import_thumbnail_cache_hit",
                    _ if fence.is_cancelled() => "import_thumbnail_cancelled",
                    _ => "import_thumbnail_skipped",
                });
            });
        if let Ok(worker) = worker {
            self.import_thumbnails.running = Some(Running {
                request,
                cancel,
                io,
                worker,
            });
        }
    }

    /// Trace only a driver-accepted frame for the imported Library selection.
    pub fn observe_import_presentation(&mut self, frame: crate::platform::FrameCheckpoint) {
        if !self.import_thumbnails.observe_visible || !frame.submitted {
            return;
        }
        let Some(covers) = self.startup_cover_projection() else {
            self.import_thumbnails.imported(false);
            return;
        };
        let visible = self.visible_cover_ids();
        let initial = self
            .import_thumbnails
            .observed_set
            .get_or_insert_with(|| visible.clone());
        if visible != *initial {
            self.import_thumbnails.imported(false);
        } else if covers.0 > 0 && covers.0 == covers.1 && !covers.2 {
            crate::reader_timing::startup_frame(
                "import_visible_covers_settled",
                frame,
                Some(covers),
            );
            self.import_thumbnails.imported(false);
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Outcome {
    Persisted,
    Hit,
    Skipped,
}

fn prepare(
    data: &Path,
    cache: &crate::cache::CacheManager,
    request: &Request,
    cancellation: &imaging::DecodeCancellation,
) -> Result<Outcome> {
    if Instant::now() >= request.expires || !cache.has_idle_thumbnail_headroom()? {
        return Ok(Outcome::Skipped);
    }
    let store = Store::open_read_only_with_timeout(data.join("library.sqlite3"), Duration::ZERO)?;
    let Some(book) = store
        .book(&request.id)?
        .filter(|b| b.in_library && b.source_id == "local" && b.cover == request.cover)
    else {
        return Ok(Outcome::Skipped);
    };
    drop(store);
    let path = PathBuf::from(&book.cover);
    let content = fs::canonicalize(data.join("local/content"))?;
    let raw = fs::canonicalize(&path)?;
    let revision = raw
        .parent()
        .and_then(Path::file_name)
        .and_then(|v| v.to_str());
    if book.remote_id.len() != 64
        || !book.remote_id.bytes().all(|b| b.is_ascii_hexdigit())
        || raw != path
        || !revision.is_some_and(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
        || raw.parent().and_then(Path::parent) != Some(content.join(&book.remote_id).as_path())
        || raw.file_stem().and_then(|v| v.to_str()) != Some("000001")
    {
        return Ok(Outcome::Skipped);
    }
    cancellation.check()?;
    let options = cover_jobs::cover_options(376, 514);
    let page = Page {
        local_path: Some(path),
        ..Page::default()
    };
    let rendered = crate::render_cache::render_import_thumbnail(cache, &page, &raw, &options)?;
    cancellation.check()?;
    if rendered.cache_path == raw {
        return Ok(Outcome::Skipped);
    }
    let Some(alias) = cover_jobs::local_cover_render_alias(&book, &raw, &rendered, &options) else {
        return Ok(Outcome::Skipped);
    };
    cache.ensure_processed_cover_alias(&alias, &rendered.cache_path)?;
    Ok(if rendered.cache_hit {
        Outcome::Hit
    } else {
        Outcome::Persisted
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn book(id: usize, cover: &str) -> Book {
        Book {
            id: id.to_string(),
            cover: cover.into(),
            source_id: "local".into(),
            in_library: true,
            ..Default::default()
        }
    }

    #[test]
    fn repeated_revisions_replace_pending_work_and_the_queue_is_bounded() {
        let mut jobs = ImportThumbnails::default();
        let now = Instant::now();
        for id in 0..(MAX_PENDING + 15) {
            jobs.enqueue(&book(id, "revision-a"), now);
        }
        jobs.enqueue(&book(0, "revision-b"), now);
        jobs.enqueue(&book(0, "revision-b"), now);
        assert_eq!(jobs.pending.len(), MAX_PENDING);
        let first = jobs
            .pending
            .iter()
            .filter(|r| r.id == "0")
            .collect::<Vec<_>>();
        assert_eq!(first.len(), 1);
        assert_eq!(first[0].cover, "revision-b");
        jobs.enqueue(&book(MAX_PENDING, "another"), now + QUEUE_LIFETIME);
        assert_eq!(jobs.pending.len(), 1);
        jobs.cancel();
        assert!(jobs.pending.is_empty());
    }

    #[test]
    fn clearing_and_reimporting_cannot_leave_an_old_publisher_after_clear() {
        let now = Instant::now();
        let mut jobs = ImportThumbnails::default();
        let book = book(1, "old-revision");
        jobs.enqueue(&book, now);
        let old = jobs.pending.pop_front().unwrap();
        let io = Arc::new(Mutex::new(()));
        let lock = io.clone();
        let cancel = imaging::DecodeCancellation::new();
        let (entered, working) = mpsc::channel();
        let (release, proceed) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            let _publishing = lock.lock().unwrap();
            entered.send(()).unwrap();
            proceed.recv().unwrap();
        });
        jobs.running = Some(Running { request: old, cancel: cancel.clone(), io, worker });
        working.recv_timeout(Duration::from_secs(2)).unwrap();
        jobs.enqueue(&book, now);
        assert!(jobs.pending.is_empty() && !cancel.is_cancelled());
        jobs.enqueue(&Book { cover: "new-revision".into(), ..book }, now);
        assert!(cancel.is_cancelled() && jobs.pending.len() == 1);
        let clear = jobs.cancel_for_clear().unwrap();
        assert!(jobs.pending.is_empty() && !jobs.drained());
        assert!(clear.try_lock().is_err());
        release.send(()).unwrap();
        let _only_after_publisher_exits = clear.lock().unwrap();
        jobs.running.take().unwrap().worker.join().unwrap();
        jobs.finish();
        assert!(jobs.drained() && !jobs.batch_open);
    }

    #[test]
    fn import_thumbnail_reopens_as_a_valid_cover_and_respects_cancellation_and_pressure()
    -> Result<()> {
        let dir = tempfile::tempdir()?;
        let input = dir.path().join("selected");
        fs::create_dir(&input)?;
        image::GrayImage::from_fn(81, 109, |x, y| {
            image::Luma([((x * 31 + y * 7) % 256) as u8])
        })
        .save(input.join("cover.png"))?;
        let imported = LocalLibrary::new(dir.path().join("local"))?.import(&input)?;
        let db = Store::open(dir.path().join("library.sqlite3"))?;
        db.upsert_book(&imported.book)?;
        let req = Request {
            id: imported.book.id.clone(),
            cover: imported.book.cover.clone(),
            expires: Instant::now() + QUEUE_LIFETIME,
        };
        let stop = imaging::DecodeCancellation::new();
        let cache = crate::cache::CacheManager::open_with_config(
            dir.path(),
            crate::cache::CacheConfig {
                max_unpinned_bytes: 0,
                min_free_bytes: 0,
            },
        )?;
        let run = |token: &imaging::DecodeCancellation| {
            imaging::with_background_decode(Some(token), || {
                prepare(dir.path(), &cache, &req, token)
            })
        };
        assert!(matches!(run(&stop)?, Outcome::Skipped));
        assert_eq!(cache.stats().commits, 0);
        cache.set_config(Default::default())?;
        let cancelled = imaging::DecodeCancellation::new();
        cancelled.cancel();
        assert!(imaging::is_decode_cancelled(
            &run(&cancelled).err().unwrap()
        ));
        assert_eq!(cache.stats().commits, 0);
        assert!(matches!(run(&stop)?, Outcome::Persisted));
        assert!(matches!(run(&stop)?, Outcome::Hit));
        let raw = PathBuf::from(&req.cover);
        let page = Page {
            local_path: Some(raw.clone()),
            ..Default::default()
        };
        let options = cover_jobs::cover_options(376, 514);
        crate::render_cache::clear_memory(&cache);
        let hit = crate::render_cache::render_local_cover(&cache, &page, &raw, &options)?;
        assert!(hit.cache_hit && !hit.memory_hit);
        assert_eq!(hit.image.as_ref(), &imaging::render_page(&raw, &options)?);
        let alias =
            cover_jobs::local_cover_render_alias(&imported.book, &raw, &hit, &options).unwrap();
        assert_eq!(cache.lookup_alias(&alias)?, Some(hit.cache_path));
        db.set_library(&req.id, false)?;
        assert!(matches!(run(&stop)?, Outcome::Skipped));
        Ok(())
    }
}
