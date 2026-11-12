mod processing;

use std::{
    cell::{Cell, RefCell},
    collections::{BTreeSet, HashMap, HashSet, VecDeque},
    path::{Path, PathBuf},
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        mpsc::{self, Receiver, Sender, TryRecvError},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use anyhow::{Context, Result, anyhow, ensure};
use sha2::{Digest, Sha256};

use crate::store::download_management::{BookDownloadCandidate, BookDownloadRemovalKind};
use crate::{
    cache::{CacheLease, CacheManager},
    domain::{Book, Chapter, DownloadJob, Page, Preferences},
    sources::{FetchPriority, SourceManager, is_network_interrupted},
    store::Store,
};

const PROGRESS_INTERVAL: Duration = Duration::from_millis(100);
const COMMAND_INTERVAL: Duration = Duration::from_millis(40);

/// Return an existing, opt-in offline accelerator. This never prepares pixels
/// or changes the original page's canonical cache identity or manifest path.
pub fn prepared_page_path(
    cache: &CacheManager,
    page: &Page,
    original: &Path,
    preferences: &Preferences,
    logical_viewport: (u32, u32),
) -> Result<Option<PathBuf>> {
    processing::lookup(cache, page, original, preferences, logical_viewport)
}

#[derive(Debug, Clone)]
pub enum DownloadEvent {
    Progress(DownloadJob),
    Completed(DownloadJob),
    Failed(DownloadJob),
    StateChanged(DownloadJob),
    Removed {
        chapter_id: String,
    },
    BatchAccepted {
        pending: usize,
        paused: usize,
        completed: usize,
    },
    /// State rows committed by a bulk control. Active transfers may still drain.
    QueueChanged {
        affected: usize,
    },
    DownloadedMetadataChanged {
        chapter_id: String,
    },
    BookRemovalFinished {
        book_id: String,
        removed: usize,
        cancelled: usize,
        failed: usize,
        retained_local: usize,
        usage_changed: bool,
        error: Option<String>,
    },
    Suspended,
    Resumed {
        network_ready: bool,
    },
    Error(String),
}

#[derive(Debug, Clone, Copy)]
pub struct DownloadOptions {
    pub offline: bool,
    pub page_concurrency: usize,
}

impl Default for DownloadOptions {
    fn default() -> Self {
        Self {
            offline: false,
            page_concurrency: 2,
        }
    }
}

#[derive(Clone, Copy)]
enum DatabaseStartup {
    Initialize,
    Existing,
}

enum Command {
    Enqueue(Book, Vec<Chapter>),
    ProcessingViewport(u32, u32),
    Pause(String),
    Resume(String),
    Cancel(String),
    PauseAll,
    ResumeAll,
    CancelAll,
    RemoveCompleted(String),
    RemoveBookDownloads(String),
    RemoveOrCancel {
        expected_book_id: String,
        chapter_id: String,
    },
    Suspend(u64),
    ResumeOwned {
        epoch: u64,
        network_ready: bool,
    },
    Stop,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Control {
    Running,
    Paused,
    Cancelled,
}

type Controls = Arc<Mutex<HashMap<String, Control>>>;

struct SuspendState {
    strict: AtomicBool,
    gate_pending: AtomicBool,
    checkpoint_allowed: AtomicBool,
    network_ready: AtomicBool,
    epoch: AtomicU64,
    acknowledged: AtomicU64,
    barrier_active: AtomicBool,
    active: AtomicUsize,
    busy: AtomicUsize,
    owned: Mutex<BTreeSet<String>>,
    transition: Mutex<()>,
}

impl SuspendState {
    fn new(suspended: bool) -> Self {
        Self {
            strict: AtomicBool::new(suspended),
            gate_pending: AtomicBool::new(false),
            checkpoint_allowed: AtomicBool::new(suspended),
            network_ready: AtomicBool::new(!suspended),
            epoch: AtomicU64::new(if suspended { 1 } else { 0 }),
            acknowledged: AtomicU64::new(0),
            barrier_active: AtomicBool::new(false),
            active: AtomicUsize::new(0),
            busy: AtomicUsize::new(0),
            owned: Mutex::new(BTreeSet::new()),
            transition: Mutex::new(()),
        }
    }
}

struct ActivityGuard {
    state: Arc<SuspendState>,
    network: bool,
}

impl ActivityGuard {
    fn new(state: &Arc<SuspendState>, network: bool) -> Self {
        let counter = if network { &state.active } else { &state.busy };
        counter.fetch_add(1, Ordering::AcqRel);
        Self {
            state: state.clone(),
            network,
        }
    }
}

impl Drop for ActivityGuard {
    fn drop(&mut self) {
        let counter = if self.network {
            &self.state.active
        } else {
            &self.state.busy
        };
        counter.fetch_sub(1, Ordering::AcqRel);
    }
}

/// The controller is the only owner of the queue's SQLite connection. At most
/// two page fetches run concurrently, and every raw image comes from shared cache.
pub struct DownloadWorker {
    commands: Sender<Command>,
    pub events: Receiver<DownloadEvent>,
    controls: Controls,
    stopping: Arc<AtomicBool>,
    stopped: Arc<(Mutex<bool>, Condvar)>,
    terminal_error: Arc<Mutex<Option<String>>>,
    join_handle: Mutex<Option<JoinHandle<()>>>,
    suspend: Arc<SuspendState>,
}

impl DownloadWorker {
    pub fn start(data_dir: PathBuf, database_path: PathBuf) -> Result<Self> {
        Self::start_configured(data_dir, database_path, DownloadOptions::default())
    }

    pub fn start_with_options(
        data_dir: PathBuf,
        database_path: PathBuf,
        offline: bool,
    ) -> Result<Self> {
        Self::start_configured(
            data_dir,
            database_path,
            DownloadOptions {
                offline,
                ..DownloadOptions::default()
            },
        )
    }

    /// Initialize a standalone queue and return after its database and cache
    /// are ready. Startup failures are returned before a worker is published.
    pub fn start_configured(
        data_dir: PathBuf,
        database_path: PathBuf,
        options: DownloadOptions,
    ) -> Result<Self> {
        Self::start_internal(
            data_dir,
            database_path,
            options,
            false,
            DatabaseStartup::Initialize,
        )
    }

    pub fn start_suspended(
        data_dir: PathBuf,
        database_path: PathBuf,
        options: DownloadOptions,
    ) -> Result<Self> {
        Self::start_internal(
            data_dir,
            database_path,
            options,
            true,
            DatabaseStartup::Initialize,
        )
    }

    /// The primary Store must initialize the schema and recover the queue
    /// before this call. The same readiness acknowledgement applies here.
    pub(crate) fn start_initialized(
        data_dir: PathBuf,
        database_path: PathBuf,
        options: DownloadOptions,
        suspended: bool,
    ) -> Result<Self> {
        Self::start_internal(
            data_dir,
            database_path,
            options,
            suspended,
            DatabaseStartup::Existing,
        )
    }

    fn start_internal(
        data_dir: PathBuf,
        database_path: PathBuf,
        options: DownloadOptions,
        suspended: bool,
        database_startup: DatabaseStartup,
    ) -> Result<Self> {
        ensure!(
            (1..=2).contains(&options.page_concurrency),
            "Download page concurrency must be one or two"
        );
        let (commands, receiver) = mpsc::channel();
        let (events_sender, events) = mpsc::channel();
        let (startup_sender, startup) = mpsc::channel::<Result<()>>();
        let controls = Arc::new(Mutex::new(HashMap::new()));
        let stopping = Arc::new(AtomicBool::new(false));
        let stopped = Arc::new((Mutex::new(false), Condvar::new()));
        let terminal_error = Arc::new(Mutex::new(None));
        let suspend = Arc::new(SuspendState::new(suspended));
        let thread_controls = controls.clone();
        let thread_stopping = stopping.clone();
        let thread_stopped = stopped.clone();
        let thread_terminal_error = terminal_error.clone();
        let thread_suspend = suspend.clone();
        let join_handle = thread::Builder::new()
            .name("aidoku-downloads".into())
            .spawn(move || {
                let mut startup_sender = Some(startup_sender);
                let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    let store = match database_startup {
                        DatabaseStartup::Initialize => Store::open(&database_path),
                        DatabaseStartup::Existing => Store::open_existing_for_write(&database_path),
                    }
                    .context("Open the download database connection")?;
                    let cache = CacheManager::open(&data_dir)
                        .context("Open the shared download image cache")?;
                    *thread_suspend
                        .owned
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner) = store
                        .suspended_download_ids()
                        .context("Load persisted download suspension ownership")?;
                    // This is the only success acknowledgement. No schema,
                    // cache, or persisted ownership initialization follows it.
                    startup_sender
                        .take()
                        .expect("The startup acknowledgement is still pending")
                        .send(Ok(()))
                        .map_err(|_| anyhow!("The download startup receiver is unavailable"))?;
                    worker_loop(
                        &data_dir,
                        &store,
                        &cache,
                        &receiver,
                        &events_sender,
                        &thread_controls,
                        &thread_stopping,
                        &thread_suspend,
                        options,
                    )
                }))
                .unwrap_or_else(|_| Err(anyhow!("The download worker panicked")));
                if let Err(error) = outcome {
                    let cause = format!("{error:#}");
                    *thread_terminal_error
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(cause.clone());
                    if let Some(startup_sender) = startup_sender.take() {
                        let _ = startup_sender.send(Err(error));
                    }
                    eprintln!("aidoku: download worker failed: {cause}");
                    let _ = events_sender.send(DownloadEvent::Error(cause));
                }
                // All fetch guards and the controller's Store have been dropped.
                let (finished, changed) = &*thread_stopped;
                *finished
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner) = true;
                changed.notify_all();
            })
            .context("Start the download worker")?;
        let join_handle = await_worker_startup(startup, join_handle)?;
        Ok(Self {
            commands,
            events,
            controls,
            stopping,
            stopped,
            terminal_error,
            join_handle: Mutex::new(Some(join_handle)),
            suspend,
        })
    }

    pub fn enqueue(&self, book: &Book, chapter: &Chapter) -> Result<()> {
        self.enqueue_many(book, std::slice::from_ref(chapter))
    }

    pub fn enqueue_many(&self, book: &Book, chapters: &[Chapter]) -> Result<()> {
        ensure!(
            chapters.len() <= 20_000,
            "The download batch contains too many chapters"
        );
        if chapters.is_empty() {
            return Ok(());
        }
        let mut unique = HashSet::new();
        let mut batch = Vec::with_capacity(chapters.len());
        for chapter in chapters {
            ensure!(
                book.id == chapter.book_id
                    && book.source_id == chapter.source_id
                    && !chapter.id.is_empty(),
                "The download chapter belongs to another book"
            );
            if unique.insert(chapter.id.clone()) {
                batch.push(chapter.clone());
            }
        }
        {
            let mut controls = self
                .controls
                .lock()
                .map_err(|_| anyhow!("The download command lock is unavailable"))?;
            for chapter in &batch {
                controls
                    .entry(chapter.id.clone())
                    .and_modify(|control| {
                        if *control != Control::Paused {
                            *control = Control::Running;
                        }
                    })
                    .or_insert(Control::Running);
            }
        }
        self.send(Command::Enqueue(book.clone(), batch))
    }

    /// Reserve only untouched queue identities. Automatic look-ahead must not
    /// revive a user's paused or cancelled command that has not reached SQLite.
    pub fn enqueue_automatic(&self, book: &Book, chapters: &[Chapter]) -> Result<()> {
        ensure!(
            chapters.len() <= 3,
            "Automatic download look-ahead exceeds three chapters"
        );
        for chapter in chapters {
            ensure!(
                book.id == chapter.book_id
                    && book.source_id == chapter.source_id
                    && !chapter.id.is_empty(),
                "The automatic download chapter belongs to another book"
            );
        }
        let mut controls = self
            .controls
            .lock()
            .map_err(|_| anyhow!("The download command lock is unavailable"))?;
        let mut batch = Vec::new();
        for chapter in chapters {
            if let std::collections::hash_map::Entry::Vacant(entry) =
                controls.entry(chapter.id.clone())
            {
                entry.insert(Control::Running);
                batch.push(chapter.clone());
            }
        }
        if batch.is_empty() {
            return Ok(());
        }
        // The channel is unbounded, so publication cannot block on the worker.
        // Keep this lock until publication to preserve ordering with user input.
        if let Err(error) = self.send(Command::Enqueue(book.clone(), batch.clone())) {
            for chapter in batch {
                controls.remove(&chapter.id);
            }
            return Err(error);
        }
        Ok(())
    }

    /// Supply logical panel dimensions. Preparation uses native-resolution
    /// pixels and a rotation-independent bounding square, without upscaling.
    pub fn set_processing_viewport(&self, width: u32, height: u32) -> Result<()> {
        processing::validate_viewport((width, height))?;
        self.send(Command::ProcessingViewport(width, height))
    }

    pub fn pause(&self, chapter_id: &str) -> Result<()> {
        self.set_control(chapter_id, Control::Paused)?;
        self.send(Command::Pause(chapter_id.into()))
    }

    pub fn resume(&self, chapter_id: &str) -> Result<()> {
        self.set_control(chapter_id, Control::Running)?;
        self.send(Command::Resume(chapter_id.into()))
    }

    pub fn cancel(&self, chapter_id: &str) -> Result<()> {
        self.set_control(chapter_id, Control::Cancelled)?;
        self.send(Command::Cancel(chapter_id.into()))
    }

    pub fn pause_all(&self) -> Result<()> {
        self.send(Command::PauseAll)
    }
    pub fn resume_all(&self) -> Result<()> {
        self.send(Command::ResumeAll)
    }
    pub fn cancel_all(&self) -> Result<()> {
        self.send(Command::CancelAll)
    }

    pub fn remove_completed(&self, chapter_id: &str) -> Result<()> {
        ensure!(!chapter_id.is_empty(), "A chapter identity is required");
        self.send(Command::RemoveCompleted(chapter_id.into()))
    }

    pub fn remove_or_cancel(&self, expected_book_id: &str, chapter_id: &str) -> Result<()> {
        ensure!(
            !expected_book_id.is_empty() && !chapter_id.is_empty(),
            "Book and chapter identities are required"
        );
        self.send(Command::RemoveOrCancel {
            expected_book_id: expected_book_id.into(),
            chapter_id: chapter_id.into(),
        })
    }

    pub fn remove_book_downloads(&self, book_id: &str) -> Result<()> {
        ensure!(!book_id.is_empty(), "A book identity is required");
        self.send(Command::RemoveBookDownloads(book_id.into()))
    }

    /// Close transport admission without authorizing a durable checkpoint.
    /// The controller parks ordinary commands until checkpoint or wake arrives.
    pub fn begin_suspend_gate(&self) -> Result<()> {
        let _transition = self
            .suspend
            .transition
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        self.suspend.network_ready.store(false, Ordering::Release);
        let epoch = if self.suspend.gate_pending.load(Ordering::Acquire) {
            self.suspend.epoch.load(Ordering::Acquire)
        } else {
            self.suspend
                .epoch
                .fetch_add(1, Ordering::AcqRel)
                .wrapping_add(1)
        };
        self.suspend.gate_pending.store(true, Ordering::Release);
        self.suspend
            .checkpoint_allowed
            .store(false, Ordering::Release);
        self.suspend.barrier_active.store(false, Ordering::Release);
        self.suspend.strict.store(true, Ordering::Release);
        self.send(Command::Suspend(epoch))
    }

    pub fn pause_for_suspend(&self) -> Result<()> {
        let _transition = self
            .suspend
            .transition
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        self.suspend.network_ready.store(false, Ordering::Release);
        // A two-phase caller authorizes the gate's existing epoch. Standalone
        // legacy calls still invalidate every previously queued wake request.
        let epoch = if self.suspend.gate_pending.swap(false, Ordering::AcqRel) {
            self.suspend.epoch.load(Ordering::Acquire)
        } else {
            self.suspend
                .epoch
                .fetch_add(1, Ordering::AcqRel)
                .wrapping_add(1)
        };
        self.suspend
            .checkpoint_allowed
            .store(true, Ordering::Release);
        self.suspend.strict.store(true, Ordering::Release);
        self.send(Command::Suspend(epoch))
    }

    /// The primary queue checkpoint is committed and the controller is fenced.
    /// HTTP and temporary cache writes may still be alive until the PID is stopped.
    pub fn suspend_ready(&self) -> bool {
        self.suspend.strict.load(Ordering::Acquire)
            && self.suspend.checkpoint_allowed.load(Ordering::Acquire)
            && self.suspend.barrier_active.load(Ordering::Acquire)
            && self.suspend.acknowledged.load(Ordering::Acquire)
                == self.suspend.epoch.load(Ordering::Acquire)
    }

    pub fn resume_owned(&self, network_ready: bool) -> Result<()> {
        let _transition = self
            .suspend
            .transition
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        self.send(Command::ResumeOwned {
            epoch: self.suspend.epoch.load(Ordering::Acquire),
            network_ready,
        })?;
        // A new gate after this accepted wake must invalidate that wake even
        // when the controller has not consumed its command yet.
        self.suspend.gate_pending.store(false, Ordering::Release);
        Ok(())
    }

    pub fn active_count(&self) -> usize {
        self.suspend.active.load(Ordering::Acquire)
    }

    pub fn is_idle(&self) -> bool {
        self.active_count() == 0
            && (self.suspend.busy.load(Ordering::Acquire) == 0
                || self.suspend.barrier_active.load(Ordering::Acquire))
    }

    pub fn drain_events(&self) -> Vec<DownloadEvent> {
        self.events.try_iter().collect()
    }

    pub fn shutdown(&self) {
        self.stopping.store(true, Ordering::Release);
        let _ = self.commands.send(Command::Stop);
    }

    /// Observe controller termination without waiting for or joining a thread.
    pub fn is_stopped(&self) -> bool {
        *self
            .stopped
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Use only after the UI event loop ends. No live HTTP request is joined on timeout.
    pub fn wait_stopped(&self, timeout: Duration) -> bool {
        let started = Instant::now();
        let (finished, changed) = &*self.stopped;
        let mut finished = finished
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        while !*finished {
            let remaining = timeout.saturating_sub(started.elapsed());
            if remaining.is_zero() {
                return false;
            }
            let (guard, result) = changed
                .wait_timeout(finished, remaining)
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            finished = guard;
            if result.timed_out() && !*finished {
                return false;
            }
        }
        drop(finished);
        if let Some(handle) = self
            .join_handle
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take()
        {
            let _ = handle.join();
        }
        true
    }

    fn set_control(&self, chapter_id: &str, control: Control) -> Result<()> {
        ensure!(!chapter_id.is_empty(), "A chapter identity is required");
        self.controls
            .lock()
            .map_err(|_| anyhow!("The download command lock is unavailable"))?
            .insert(chapter_id.into(), control);
        Ok(())
    }

    fn send(&self, command: Command) -> Result<()> {
        ensure!(
            !self.stopping.load(Ordering::Acquire),
            "The download worker is shutting down"
        );
        self.commands.send(command).map_err(|_| {
            let cause = self
                .terminal_error
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            match cause.as_deref() {
                Some(cause) => anyhow!("The download worker is unavailable: {cause}"),
                None => anyhow!("The download worker is unavailable"),
            }
        })
    }
}

impl Drop for DownloadWorker {
    fn drop(&mut self) {
        self.shutdown();
        let _ = self.wait_stopped(Duration::ZERO);
    }
}

fn await_worker_startup(
    startup: Receiver<Result<()>>,
    join_handle: JoinHandle<()>,
) -> Result<JoinHandle<()>> {
    match startup.recv() {
        Ok(Ok(())) => Ok(join_handle),
        Ok(Err(error)) => {
            let _ = join_handle.join();
            Err(error.context("Initialize the download worker"))
        }
        Err(error) => {
            let message = if join_handle.join().is_err() {
                "The download worker panicked before initialization completed"
            } else {
                "The download worker stopped before initialization completed"
            };
            Err(anyhow!(error).context(message))
        }
    }
}

struct Controller<'a> {
    data_dir: &'a Path,
    store: &'a Store,
    cache: &'a CacheManager,
    commands: &'a Receiver<Command>,
    events: &'a Sender<DownloadEvent>,
    controls: &'a Controls,
    stopping: &'a AtomicBool,
    suspend: &'a Arc<SuspendState>,
    deferred: &'a RefCell<VecDeque<Command>>,
    book_removals: &'a RefCell<VecDeque<BookRemovalTask>>,
    removal_books: &'a RefCell<BTreeSet<String>>,
    removal_active: &'a RefCell<BTreeSet<String>>,
    processing_viewport: &'a Cell<(u32, u32)>,
}

#[derive(Default)]
struct ManagerSlot {
    current: Option<([u8; 32], Arc<SourceManager>)>,
}

impl ManagerSlot {
    fn get(&mut self, controller: &Controller<'_>) -> Result<Arc<SourceManager>> {
        let configurations = controller.store.sources()?;
        let fingerprint: [u8; 32] = Sha256::digest(serde_json::to_vec(&configurations)?).into();
        if let Some((previous, manager)) = &self.current {
            if *previous == fingerprint && manager.is_network_current() {
                return Ok(manager.clone());
            }
        }
        let manager = Arc::new(
            SourceManager::new(controller.data_dir, &configurations)
                .context("Create the shared download source client")?,
        );
        self.current = Some((fingerprint, manager.clone()));
        Ok(manager)
    }
}

/// Joining happens only in the controller thread. It also protects shutdown
/// acknowledgement when an unrelated SQLite error unwinds an active chapter.
#[derive(Default)]
struct Fetches {
    threads: HashMap<usize, JoinHandle<()>>,
}

impl Drop for Fetches {
    fn drop(&mut self) {
        for (_, handle) in self.threads.drain() {
            let _ = handle.join();
        }
    }
}

struct FetchResult {
    index: usize,
    path: Result<PathBuf>,
    epoch: u64,
    interrupted: bool,
}

fn owner(chapter_id: &str) -> String {
    format!("download:{chapter_id}")
}

fn is_sqlite_contention(error: &anyhow::Error) -> bool {
    error.chain().any(|cause| {
        matches!(
            cause.downcast_ref::<rusqlite::Error>(),
            Some(rusqlite::Error::SqliteFailure(failure, _))
                if matches!(
                    failure.code,
                    rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked
                )
        )
    })
}

fn wait_for_download_database(controller: &Controller<'_>) {
    let retry_at = Instant::now() + COMMAND_INTERVAL;
    loop {
        if controller.stopping.load(Ordering::Acquire) {
            return;
        }
        let remaining = retry_at.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return;
        }
        match controller.commands.recv_timeout(remaining) {
            Ok(Command::Stop) => {
                controller.suspend.strict.store(false, Ordering::Release);
                controller.stopping.store(true, Ordering::Release);
                return;
            }
            Ok(command) => controller.deferred.borrow_mut().push_back(command),
            Err(mpsc::RecvTimeoutError::Timeout) => return,
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                controller.stopping.store(true, Ordering::Release);
                return;
            }
        }
    }
}

/// Retry only a read or a complete Store transaction that rolls back on error.
/// Publish job, cache, control, and ownership changes only after a successful step.
fn retry_download_database<T>(
    controller: &Controller<'_>,
    operation: &'static str,
    mut step: impl FnMut() -> Result<T>,
) -> Result<Option<T>> {
    let mut reported = false;
    loop {
        if controller.stopping.load(Ordering::Acquire) {
            return Ok(None);
        }
        match step() {
            Ok(value) => return Ok(Some(value)),
            Err(error) if is_sqlite_contention(&error) => {
                if !reported {
                    eprintln!(
                        "aidoku: {operation} delayed by SQLite contention; retrying: {error:#}"
                    );
                    reported = true;
                }
                wait_for_download_database(controller);
            }
            Err(error) => return Err(error).context(operation),
        }
    }
}

/// Cancel a wake retry when a newer suspend owns the controller fence.
fn retry_download_database_for_epoch<T>(
    controller: &Controller<'_>,
    epoch: u64,
    operation: &'static str,
    mut step: impl FnMut() -> Result<T>,
) -> Result<Option<T>> {
    let value = retry_download_database(controller, operation, || {
        if epoch != controller.suspend.epoch.load(Ordering::Acquire) {
            return Ok(None);
        }
        step().map(Some)
    })?
    .flatten();
    if epoch != controller.suspend.epoch.load(Ordering::Acquire)
        || controller.stopping.load(Ordering::Acquire)
    {
        return Ok(None);
    }
    Ok(value)
}

fn worker_loop(
    data_dir: &Path,
    store: &Store,
    cache: &CacheManager,
    commands: &Receiver<Command>,
    events: &Sender<DownloadEvent>,
    controls: &Controls,
    stopping: &AtomicBool,
    suspend: &Arc<SuspendState>,
    options: DownloadOptions,
) -> Result<()> {
    crate::imaging::with_background_decode(None, || {
        let deferred = RefCell::new(VecDeque::new());
        let book_removals = RefCell::new(VecDeque::new());
        let removal_books = RefCell::new(BTreeSet::new());
        let removal_active = RefCell::new(BTreeSet::new());
        let processing_viewport = Cell::new(processing::DEFAULT_VIEWPORT);
        let controller = Controller {
            data_dir,
            store,
            cache,
            commands,
            events,
            controls,
            stopping,
            suspend,
            deferred: &deferred,
            book_removals: &book_removals,
            removal_books: &removal_books,
            removal_active: &removal_active,
            processing_viewport: &processing_viewport,
        };
        let mut managers = ManagerSlot::default();
        let _book_compensations = BookCompensationGuard {
            controller: &controller,
        };
        loop {
            drain_commands(&controller, None)?;
            if stopping.load(Ordering::Acquire) {
                break;
            }
            let next = if options.offline || !suspend.network_ready.load(Ordering::Acquire) {
                None
            } else {
                let excluded = controller
                    .removal_books
                    .borrow()
                    .iter()
                    .cloned()
                    .collect::<Vec<_>>();
                retry_download_database(&controller, "Select the next queued download", || {
                    // A pending suspend must reach its durable checkpoint before
                    // another dequeue attempt. Ordinary commands remain deferred.
                    if suspend.strict.load(Ordering::Acquire)
                        || !suspend.network_ready.load(Ordering::Acquire)
                    {
                        return Ok(None);
                    }
                    if excluded.is_empty() {
                        store.next_download()
                    } else {
                        store.next_download_excluding_books(&excluded)
                    }
                })?
                .flatten()
            };
            if stopping.load(Ordering::Acquire) {
                break;
            }
            // Reapply command order before using a selection made after contention.
            if suspend.strict.load(Ordering::Acquire) || !controller.deferred.borrow().is_empty() {
                continue;
            }
            if let Some(mut job) = next {
                let _busy = ActivityGuard::new(suspend, false);
                if let Err(error) = run_job(
                    &controller,
                    &mut managers,
                    &mut job,
                    options.page_concurrency,
                ) {
                    suspend_barrier(&controller, Some(&job.chapter_id))
                        .context("Checkpoint suspension after a download job failed")?;
                    if store
                        .chapter(&job.chapter_id)
                        .context("Inspect the chapter after a download job failed")?
                        .is_none()
                    {
                        job.state = "cancelled".into();
                        job.error.clear();
                        cache
                            .unpin_owner(&owner(&job.chapter_id))
                            .context("Release cache ownership for a removed download chapter")?;
                        let _ = events.send(DownloadEvent::StateChanged(job));
                        continue;
                    }
                    job.state = halt_state(&controller, &job.chapter_id)
                        .unwrap_or(if is_network_interrupted(&error) {
                            "queued"
                        } else {
                            "failed"
                        })
                        .into();
                    job.error = if job.state == "failed" {
                        format!("{error:#}")
                    } else {
                        String::new()
                    };
                    store
                        .record_download_progress(&job, &[])
                        .context("Persist the state after a download job failed")?;
                    if job.state == "cancelled" && !book_removal_pending(&controller, &job.book_id)
                    {
                        cache
                            .unpin_owner(&owner(&job.chapter_id))
                            .context("Release cache ownership after a download was cancelled")?;
                    }
                    let _ = events.send(if job.state == "failed" {
                        DownloadEvent::Failed(job)
                    } else {
                        DownloadEvent::StateChanged(job)
                    });
                }
            } else {
                let interval = if controller.book_removals.borrow().is_empty() {
                    PROGRESS_INTERVAL
                } else {
                    COMMAND_INTERVAL
                };
                match commands.recv_timeout(interval) {
                    // Re-enter the suspend barrier before starting a command
                    // received while the controller was waiting for work.
                    Ok(command) => controller.deferred.borrow_mut().push_back(command),
                    Err(mpsc::RecvTimeoutError::Timeout) => {}
                    Err(mpsc::RecvTimeoutError::Disconnected) => break,
                }
            }
        }
        Ok(())
    })
}

fn drain_commands(controller: &Controller<'_>, active: Option<&str>) -> Result<()> {
    loop {
        suspend_barrier(controller, active)?;
        // A retry may return after Stop with ordinary commands still deferred.
        // Do not start another database operation once shutdown is requested.
        if controller.stopping.load(Ordering::Acquire) {
            return Ok(());
        }
        let deferred = controller.deferred.borrow_mut().pop_front();
        let command = deferred
            .map(Ok)
            .unwrap_or_else(|| controller.commands.try_recv());
        match command {
            Ok(command) => report_command(controller, command, active),
            Err(TryRecvError::Empty) => {
                tick_book_removals(controller, active)?;
                return Ok(());
            }
            Err(TryRecvError::Disconnected) => {
                controller.stopping.store(true, Ordering::Release);
                return Ok(());
            }
        }
    }
}

fn report_command(controller: &Controller<'_>, command: Command, active: Option<&str>) {
    if controller.suspend.strict.load(Ordering::Acquire)
        && !matches!(
            &command,
            Command::Suspend(_) | Command::ResumeOwned { .. } | Command::Stop
        )
    {
        controller.deferred.borrow_mut().push_back(command);
        return;
    }
    let _busy = if matches!(&command, Command::Suspend(_)) {
        None
    } else {
        Some(ActivityGuard::new(controller.suspend, false))
    };
    if let Err(error) = handle_command(controller, command, active) {
        let _ = controller
            .events
            .send(DownloadEvent::Error(format!("{error:#}")));
    }
}

fn handle_command(
    controller: &Controller<'_>,
    command: Command,
    active: Option<&str>,
) -> Result<()> {
    match command {
        Command::ProcessingViewport(width, height) => {
            processing::validate_viewport((width, height))?;
            controller.processing_viewport.set((width, height));
        }
        Command::Enqueue(book, chapters) => {
            ensure!(
                !book_removal_pending(controller, &book.id),
                "This book's downloads are being removed"
            );
            let mut metadata_changed = BTreeSet::new();
            let jobs = (|| {
                let repairs =
                    completed_replay_repairs(controller, &chapters, &mut metadata_changed)?;
                let report = controller
                    .store
                    .enqueue_download_batch_report(&book, &chapters, &repairs)?;
                metadata_changed.extend(report.downloaded_metadata_changed);
                metadata_changed.extend(repairs);
                Ok::<_, anyhow::Error>(report.jobs)
            })();
            // Replay may already have committed a wrong-reference invalidation
            // before a later verification or queue transaction fails.
            for id in metadata_changed {
                let _ = controller
                    .events
                    .send(DownloadEvent::DownloadedMetadataChanged { chapter_id: id });
            }
            let jobs = jobs?;
            let pending = jobs
                .iter()
                .filter(|job| matches!(job.state.as_str(), "queued" | "downloading"))
                .count();
            let paused = jobs.iter().filter(|job| job.state == "paused").count();
            let completed = jobs.iter().filter(|job| job.state == "completed").count();
            if jobs.len() == 1 {
                for job in jobs {
                    let _ = controller.events.send(DownloadEvent::StateChanged(job));
                }
            }
            let _ = controller.events.send(DownloadEvent::BatchAccepted {
                pending,
                paused,
                completed,
            });
        }
        Command::Pause(id) => change_state(controller, &id, "paused", active)?,
        Command::Resume(id) => change_state(controller, &id, "queued", active)?,
        Command::Cancel(id) => change_state(controller, &id, "cancelled", active)?,
        Command::PauseAll => change_all(
            controller,
            &["queued", "downloading"],
            "paused",
            Control::Paused,
            active,
        )?,
        Command::ResumeAll => change_all(
            controller,
            &["paused", "failed"],
            "queued",
            Control::Running,
            active,
        )?,
        Command::CancelAll => change_all(
            controller,
            &["queued", "downloading", "paused"],
            "cancelled",
            Control::Cancelled,
            active,
        )?,
        Command::RemoveCompleted(id) => remove_completed(controller, &id)?,
        Command::RemoveBookDownloads(book_id) => start_book_removal(controller, book_id, active)?,
        Command::RemoveOrCancel {
            expected_book_id,
            chapter_id,
        } => {
            if let Some(job) = controller.store.download(&chapter_id)? {
                ensure!(
                    job.book_id == expected_book_id,
                    "The download belongs to a different book"
                );
                if job.state == "completed" {
                    remove_completed(controller, &chapter_id)?;
                } else {
                    change_state(controller, &chapter_id, "cancelled", active)?;
                }
            }
        }
        Command::Suspend(epoch) => {
            if epoch == controller.suspend.epoch.load(Ordering::Acquire) {
                suspend_barrier(controller, active)?;
            }
        }
        Command::ResumeOwned {
            epoch,
            network_ready,
        } => resume_from_suspend(controller, epoch, network_ready)?,
        Command::Stop => {
            controller.suspend.strict.store(false, Ordering::Release);
            controller.stopping.store(true, Ordering::Release);
        }
    }
    Ok(())
}

fn refresh_suspend_ownership(controller: &Controller<'_>) -> Result<()> {
    *controller
        .suspend
        .owned
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner) =
        controller.store.suspended_download_ids()?;
    Ok(())
}

fn suspend_barrier(controller: &Controller<'_>, _active: Option<&str>) -> Result<()> {
    while controller.suspend.strict.load(Ordering::Acquire)
        && !controller.stopping.load(Ordering::Acquire)
    {
        let epoch = controller.suspend.epoch.load(Ordering::Acquire);
        if controller
            .suspend
            .checkpoint_allowed
            .load(Ordering::Acquire)
            && controller.suspend.acknowledged.load(Ordering::Acquire) != epoch
        {
            controller
                .suspend
                .barrier_active
                .store(false, Ordering::Release);
            let Some(()) = retry_download_database_for_epoch(
                controller,
                epoch,
                "Commit the download suspend checkpoint",
                || {
                    let _busy = ActivityGuard::new(controller.suspend, false);
                    controller.store.pause_downloads_for_suspend().map(|_| ())
                },
            )?
            else {
                continue;
            };
            let Some(owned) = retry_download_database_for_epoch(
                controller,
                epoch,
                "Load ownership after the download suspend checkpoint",
                || {
                    let _busy = ActivityGuard::new(controller.suspend, false);
                    controller.store.suspended_download_ids()
                },
            )?
            else {
                continue;
            };
            let _transition = controller
                .suspend
                .transition
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if epoch != controller.suspend.epoch.load(Ordering::Acquire)
                || !controller
                    .suspend
                    .checkpoint_allowed
                    .load(Ordering::Acquire)
                || controller.stopping.load(Ordering::Acquire)
            {
                continue;
            }
            *controller
                .suspend
                .owned
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner) = owned;
            {
                let _busy = ActivityGuard::new(controller.suspend, false);
                let owned = controller
                    .suspend
                    .owned
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                let mut controls = controller
                    .controls
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner);
                for id in owned.iter() {
                    controls.insert(id.clone(), Control::Paused);
                }
            }
            controller
                .suspend
                .acknowledged
                .store(epoch, Ordering::Release);
            controller
                .suspend
                .barrier_active
                .store(true, Ordering::Release);
            let _ = controller.events.send(DownloadEvent::Suspended);
        }
        // Retry waits retain every command. A wake or Stop has the same
        // priority here as when the barrier receives it directly.
        let deferred_control = {
            let mut deferred = controller.deferred.borrow_mut();
            let position = deferred
                .iter()
                .position(|command| matches!(command, Command::ResumeOwned { .. } | Command::Stop));
            position.and_then(|position| deferred.remove(position))
        };
        let command = deferred_control
            .map(Ok)
            .unwrap_or_else(|| controller.commands.recv_timeout(COMMAND_INTERVAL));
        match command {
            Ok(Command::ResumeOwned {
                epoch,
                network_ready,
            }) => {
                if let Err(error) = resume_from_suspend(controller, epoch, network_ready) {
                    let _ = controller
                        .events
                        .send(DownloadEvent::Error(format!("{error:#}")));
                }
            }
            Ok(Command::Stop) => {
                controller
                    .suspend
                    .barrier_active
                    .store(false, Ordering::Release);
                controller.suspend.strict.store(false, Ordering::Release);
                controller.stopping.store(true, Ordering::Release);
            }
            Ok(command) => controller.deferred.borrow_mut().push_back(command),
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                controller
                    .suspend
                    .barrier_active
                    .store(false, Ordering::Release);
                controller.suspend.strict.store(false, Ordering::Release);
                controller.stopping.store(true, Ordering::Release);
            }
        }
    }
    if controller.stopping.load(Ordering::Acquire) {
        controller.suspend.strict.store(false, Ordering::Release);
    }
    controller
        .suspend
        .barrier_active
        .store(false, Ordering::Release);
    Ok(())
}

fn resume_from_suspend(controller: &Controller<'_>, epoch: u64, ready: bool) -> Result<()> {
    let _busy = ActivityGuard::new(controller.suspend, false);
    {
        let _transition = controller
            .suspend
            .transition
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if epoch != controller.suspend.epoch.load(Ordering::Acquire)
            || controller.stopping.load(Ordering::Acquire)
        {
            return Ok(());
        }
        controller
            .suspend
            .gate_pending
            .store(false, Ordering::Release);
        controller
            .suspend
            .checkpoint_allowed
            .store(false, Ordering::Release);
        controller
            .suspend
            .barrier_active
            .store(false, Ordering::Release);
        controller
            .suspend
            .network_ready
            .store(false, Ordering::Release);
        if !ready {
            controller.suspend.strict.store(false, Ordering::Release);
            let _ = controller.events.send(DownloadEvent::Resumed {
                network_ready: false,
            });
            return Ok(());
        }
        // Keep the controller fenced until the durable wake and ownership read
        // succeed. The transition mutex must not be held during SQLite waits.
        controller.suspend.strict.store(true, Ordering::Release);
    }
    let Some(jobs) = retry_download_database_for_epoch(
        controller,
        epoch,
        "Commit automatic download wake ownership",
        || controller.store.resume_owned_downloads(),
    )?
    else {
        return Ok(());
    };
    let Some(owned) = retry_download_database_for_epoch(
        controller,
        epoch,
        "Load ownership after automatic download wake",
        || controller.store.suspended_download_ids(),
    )?
    else {
        return Ok(());
    };
    {
        let _transition = controller
            .suspend
            .transition
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if epoch != controller.suspend.epoch.load(Ordering::Acquire)
            || controller.stopping.load(Ordering::Acquire)
        {
            // A newer suspend will checkpoint any rows already requeued by the
            // old transaction. It must retain its strict and network fences.
            return Ok(());
        }
        *controller
            .suspend
            .owned
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = owned;
        {
            let mut controls = controller
                .controls
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            for job in &jobs {
                controls.insert(job.chapter_id.clone(), Control::Running);
            }
        }
        controller.suspend.strict.store(false, Ordering::Release);
        controller
            .suspend
            .network_ready
            .store(true, Ordering::Release);
        // Publish these events before a newer pause can acquire transition.
        for job in jobs {
            let _ = controller.events.send(DownloadEvent::StateChanged(job));
        }
        let _ = controller.events.send(DownloadEvent::Resumed {
            network_ready: true,
        });
    }
    Ok(())
}

fn completed_replay_repairs(
    controller: &Controller<'_>,
    chapters: &[Chapter],
    metadata_changed: &mut BTreeSet<String>,
) -> Result<BTreeSet<String>> {
    let mut checked = BTreeSet::new();
    let mut repairs = BTreeSet::new();
    let preferences = controller.store.preferences()?;
    for chapter in chapters {
        suspend_barrier(controller, None)?;
        if !checked.insert(chapter.id.as_str()) {
            continue;
        }
        let Some(job) = controller.store.download(&chapter.id)? else {
            continue;
        };
        if job.state != "completed" {
            continue;
        }
        let pages = controller.store.pages(&chapter.id)?;
        let mut valid =
            !pages.is_empty() && pages.len() == job.total as usize && job.completed == job.total;
        for (index, page) in pages.iter().enumerate() {
            suspend_barrier(controller, None)?;
            let Some(path) = page.local_path.as_ref() else {
                valid = false;
                continue;
            };
            match std::fs::metadata(path) {
                Ok(metadata) if metadata.is_file() => {}
                Ok(_) => {
                    valid = false;
                    continue;
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    valid = false;
                    continue;
                }
                Err(error) => {
                    return Err(error)
                        .context("Inspect the completed image before replaying its download");
                }
            }
            match controller.cache.lookup_raw(page) {
                Ok(Some(cached)) => match std::fs::canonicalize(path) {
                    Ok(current) if current == cached => {
                        if !page.url.is_empty() {
                            controller.cache.pin_raw(page, &owner(&chapter.id))?;
                        }
                        if (preferences.resize_downloads || preferences.grayscale_downloads)
                            && processing::lookup(
                                controller.cache,
                                page,
                                path,
                                &preferences,
                                controller.processing_viewport.get(),
                            )?
                            .is_none()
                        {
                            // Queue preparation against the already-pinned
                            // originals; no page needs another HTTP request.
                            repairs.insert(chapter.id.clone());
                        }
                    }
                    Ok(_) => {
                        valid = false;
                        if !page.url.is_empty() {
                            controller
                                .store
                                .invalidate_page_cache(&chapter.id, index as u32)?;
                            metadata_changed.insert(chapter.id.clone());
                        }
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => valid = false,
                    Err(error) => {
                        return Err(error)
                            .context("Resolve the completed image before replaying its download");
                    }
                },
                Ok(None) => valid = false,
                Err(error) if repairable_completed_image(&error) => valid = false,
                Err(error) => {
                    return Err(error)
                        .context("Verify the completed image before replaying its download");
                }
            }
        }
        if !valid {
            repairs.insert(chapter.id.clone());
        }
    }
    Ok(repairs)
}

fn repairable_completed_image(error: &anyhow::Error) -> bool {
    // Permission, storage, lock, and SQLite failures cannot establish that the
    // image is absent or invalid and must never silently turn into HTTP work.
    if let Some(io) = error
        .chain()
        .find_map(|cause| cause.downcast_ref::<std::io::Error>())
    {
        return matches!(
            io.kind(),
            std::io::ErrorKind::NotFound
                | std::io::ErrorKind::UnexpectedEof
                | std::io::ErrorKind::InvalidData
        );
    }
    error.chain().any(|cause| {
        matches!(
            cause.to_string().as_str(),
            "The cached image is missing or its checksum changed" | "Validate a local comic image"
        )
    })
}

fn remove_completed(controller: &Controller<'_>, chapter_id: &str) -> Result<()> {
    let job = controller
        .store
        .download(chapter_id)?
        .context("The completed download does not exist")?;
    ensure!(
        job.state == "completed",
        "Only a completed download can be removed with this action"
    );
    let chapter = controller
        .store
        .chapter(chapter_id)?
        .context("The downloaded chapter does not exist")?;
    ensure!(
        chapter.book_id == job.book_id,
        "The download belongs to a different book"
    );
    if chapter.source_id == "local" {
        controller.store.remove_completed_download(chapter_id)?;
    } else {
        let pages = controller.store.pages(chapter_id)?;
        let mut leases = Vec::new();
        let mut leased_paths = HashSet::new();
        let mut restorable = Vec::new();
        for page in &pages {
            let Some(path) = page.local_path.as_ref() else {
                continue;
            };
            match std::fs::metadata(path) {
                Ok(metadata) if metadata.is_file() => {}
                Ok(_) => continue,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(error) => {
                    return Err(error).context("Inspect the downloaded image before releasing it");
                }
            }
            restorable.push(page);
            if !leased_paths.insert(path.clone()) {
                continue;
            }
            match controller.cache.lease_path(path) {
                Ok(lease) => leases.push(lease),
                // A missing or already-invalid artifact must not prevent the
                // user from removing its completed queue record.
                Err(error) if missing_or_invalid_lease(&error) => {}
                Err(error) => {
                    return Err(error).context("Protect the downloaded image during removal");
                }
            }
        }
        let pin_owner = owner(chapter_id);
        let removal = controller
            .cache
            .unpin_owner(&pin_owner)
            .context("Release this completed download's cache pins")
            .and_then(|()| controller.store.remove_completed_download(chapter_id));
        if let Err(error) = removal {
            let mut compensation_errors = Vec::new();
            for page in restorable {
                if let Err(restore_error) = controller.cache.pin_raw(page, &pin_owner) {
                    compensation_errors.push(format!("{restore_error:#}"));
                }
            }
            if !compensation_errors.is_empty() {
                return Err(error).context(format!(
                    "Restore completed download protection: {}",
                    compensation_errors.join("; ")
                ));
            }
            return Err(error).context(
                "The completed download was retained and its cache protection was restored",
            );
        }
        // Only ordinary cache eviction may reclaim newly unpinned raw files.
        // Other download owners and active reader leases remain independent.
        drop(leases);
    }
    controller
        .controls
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .remove(chapter_id);
    let _ = controller.events.send(DownloadEvent::Removed {
        chapter_id: chapter_id.into(),
    });
    Ok(())
}

fn missing_or_invalid_lease(error: &anyhow::Error) -> bool {
    error
        .downcast_ref::<std::io::Error>()
        .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound)
        || matches!(
            error.to_string().as_str(),
            "The leased cache artifact is missing" | "The leased cache artifact is not valid"
        )
}

fn change_state(
    controller: &Controller<'_>,
    chapter_id: &str,
    state: &str,
    active: Option<&str>,
) -> Result<()> {
    change_state_with_notice(controller, chapter_id, state, active, true)
}

fn change_state_with_notice(
    controller: &Controller<'_>,
    chapter_id: &str,
    state: &str,
    active: Option<&str>,
    notify: bool,
) -> Result<()> {
    // Persist command order before a following batch action selects its states.
    // Inflight image paths are still published by the active controller only.
    let Some(mut job) = controller.store.download(chapter_id)? else {
        return Err(anyhow!("The download is not queued"));
    };
    if job.state == "completed" {
        return Ok(());
    }
    job = controller
        .store
        .set_user_download_state(chapter_id, state)?;
    let control = match state {
        "paused" => Control::Paused,
        "cancelled" => Control::Cancelled,
        _ => Control::Running,
    };
    controller
        .controls
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .insert(chapter_id.to_owned(), control);
    refresh_suspend_ownership(controller)?;
    if state == "cancelled"
        && active != Some(chapter_id)
        && !book_removal_pending(controller, &job.book_id)
    {
        controller.cache.unpin_owner(&owner(chapter_id))?;
    }
    if notify {
        let _ = controller.events.send(DownloadEvent::StateChanged(job));
    }
    Ok(())
}

fn change_all(
    controller: &Controller<'_>,
    from_states: &[&str],
    target: &str,
    control: Control,
    active: Option<&str>,
) -> Result<()> {
    let jobs = controller.store.transition_downloads(from_states, target)?;
    let affected = jobs.len();
    refresh_suspend_ownership(controller)?;
    {
        let mut controls = controller
            .controls
            .lock()
            .map_err(|_| anyhow!("The download command lock is unavailable"))?;
        for job in &jobs {
            controls.insert(job.chapter_id.clone(), control);
        }
    }
    if target == "cancelled" {
        let owners = jobs
            .iter()
            .filter(|job| {
                active != Some(job.chapter_id.as_str())
                    && !book_removal_pending(controller, &job.book_id)
            })
            .map(|job| owner(&job.chapter_id))
            .collect::<Vec<_>>();
        if !owners.is_empty() {
            controller.cache.unpin_owners(&owners)?;
        }
    }
    let _ = controller
        .events
        .send(DownloadEvent::QueueChanged { affected });
    Ok(())
}

#[derive(Default)]
struct BookRemovalTask {
    book_id: String,
    cursor: Option<String>,
    candidates: VecDeque<BookDownloadCandidate>,
    exhausted: bool,
    current: Option<BookChapterRemoval>,
    waiting_active: Option<String>,
    active_ids: BTreeSet<String>,
    finished: HashSet<String>,
    removed: usize,
    cancelled: usize,
    failed: usize,
    retained_local: usize,
    usage_changed: bool,
    errors: Vec<String>,
}

struct BookChapterRemoval {
    candidate: BookDownloadCandidate,
    offset: usize,
    protecting: bool,
    unpinned: bool,
    leases: Vec<CacheLease>,
    paths: HashSet<PathBuf>,
    failure: Option<String>,
    compensation_errors: Vec<String>,
    restore_paths: VecDeque<PathBuf>,
}

enum BookChapterStep {
    Pending,
    Done(BookDownloadRemovalKind, bool),
    Failed(String),
}

impl BookRemovalTask {
    fn fail(&mut self, chapter_id: &str, error: impl std::fmt::Display) {
        if !self.finished.insert(chapter_id.to_owned()) {
            return;
        }
        self.failed += 1;
        if self.errors.len() < 8 {
            self.errors.push(
                format!("{chapter_id}: {error}")
                    .chars()
                    .take(2048)
                    .collect(),
            );
        }
    }

    fn complete_chapter(&mut self, chapter_id: &str, kind: BookDownloadRemovalKind, usage: bool) {
        if !self.finished.insert(chapter_id.to_owned()) {
            return;
        }
        match kind {
            BookDownloadRemovalKind::Noop => {}
            BookDownloadRemovalKind::Removed => self.removed += 1,
            BookDownloadRemovalKind::Cancelled => self.cancelled += 1,
            BookDownloadRemovalKind::RetainedLocal => self.retained_local += 1,
        }
        self.usage_changed |= usage;
    }
}

fn book_removal_pending(controller: &Controller<'_>, book_id: &str) -> bool {
    controller.removal_books.borrow().contains(book_id)
}

fn start_book_removal(
    controller: &Controller<'_>,
    book_id: String,
    active: Option<&str>,
) -> Result<()> {
    if !controller
        .removal_books
        .borrow_mut()
        .insert(book_id.clone())
    {
        return Ok(());
    }
    let mut task = BookRemovalTask {
        book_id,
        ..BookRemovalTask::default()
    };
    let inspection = (|| -> Result<()> {
        if let Some(id) = active {
            let owner = controller.store.download(id)?.map(|job| job.book_id);
            let belongs = if let Some(owner) = owner {
                owner == task.book_id
            } else {
                controller
                    .store
                    .chapter(id)?
                    .is_some_and(|chapter| chapter.book_id == task.book_id)
            };
            if belongs {
                task.active_ids.insert(id.to_owned());
                task.waiting_active = Some(id.to_owned());
                controller.removal_active.borrow_mut().insert(id.to_owned());
                controller
                    .controls
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .insert(id.to_owned(), Control::Cancelled);
                if let Some(job) = controller.store.download(id)? {
                    if job.state != "completed" {
                        if let Err(error) =
                            change_state_with_notice(controller, id, "cancelled", active, false)
                        {
                            task.fail(id, format!("Cancel the active download: {error:#}"));
                        }
                    }
                }
            }
        }
        Ok(())
    })();
    if let Err(error) = inspection {
        if let Some(id) = active {
            task.active_ids.insert(id.to_owned());
            task.waiting_active = Some(id.to_owned());
            task.fail(id, format!("Inspect the active download: {error:#}"));
        } else {
            task.fail(
                "book-index",
                format!("Inspect the download queue: {error:#}"),
            );
        }
    }
    controller.book_removals.borrow_mut().push_back(task);
    Ok(())
}

/// One tick performs at most four metadata/window steps. A single step never
/// decodes a bitmap or materializes a book's complete chapter/page documents.
fn tick_book_removals(controller: &Controller<'_>, active: Option<&str>) -> Result<()> {
    let started = Instant::now();
    for _ in 0..4 {
        if controller.stopping.load(Ordering::Acquire) {
            break;
        }
        suspend_barrier(controller, active)?;
        let Some(mut task) = controller.book_removals.borrow_mut().pop_front() else {
            break;
        };
        let _busy = ActivityGuard::new(controller.suspend, false);
        let outcome = advance_book_removal(controller, &mut task, active);
        if let Err(error) = outcome {
            task.fail(
                "book-index",
                format!("Read the book download queue: {error:#}"),
            );
            task.exhausted = true;
            task.candidates.clear();
        }
        let done = task.exhausted
            && task.candidates.is_empty()
            && task.current.is_none()
            && task.waiting_active.is_none()
            && task.active_ids.iter().all(|id| active != Some(id.as_str()));
        if done {
            controller.removal_books.borrow_mut().remove(&task.book_id);
            for id in &task.active_ids {
                controller.removal_active.borrow_mut().remove(id);
            }
            let error = if task.errors.is_empty() {
                None
            } else {
                let mut summary = task.errors.join("; ");
                if task.failed > task.errors.len() {
                    summary.push_str(&format!(
                        "; {} additional failures",
                        task.failed - task.errors.len()
                    ));
                }
                Some(summary)
            };
            let _ = controller.events.send(DownloadEvent::BookRemovalFinished {
                book_id: task.book_id,
                removed: task.removed,
                cancelled: task.cancelled,
                failed: task.failed,
                retained_local: task.retained_local,
                usage_changed: task.usage_changed,
                error,
            });
        } else {
            controller.book_removals.borrow_mut().push_back(task);
        }
        if started.elapsed() >= Duration::from_millis(20) {
            break;
        }
    }
    Ok(())
}

fn advance_book_removal(
    controller: &Controller<'_>,
    task: &mut BookRemovalTask,
    active: Option<&str>,
) -> Result<()> {
    if let Some(mut current) = task.current.take() {
        let id = current.candidate.chapter_id.clone();
        match advance_book_chapter(controller, &task.book_id, &mut current, active) {
            Ok(BookChapterStep::Pending) => task.current = Some(current),
            Ok(BookChapterStep::Done(kind, usage)) => {
                task.complete_chapter(&id, kind, usage);
                controller
                    .controls
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .remove(&id);
                controller.removal_active.borrow_mut().remove(&id);
            }
            Ok(BookChapterStep::Failed(error)) => task.fail(&id, error),
            Err(error) if current.unpinned => {
                if current.failure.is_none() {
                    current.failure = Some(format!("{error:#}"));
                    current.protecting = false;
                    current.offset = 0;
                    current.restore_paths = std::mem::take(&mut current.paths)
                        .into_iter()
                        .filter(|path| managed_raw_removal_path(controller.cache, path))
                        .collect();
                } else if current.compensation_errors.len() < 4 {
                    current
                        .compensation_errors
                        .push(format!("Resume cache protection: {error:#}"));
                }
                task.current = Some(current);
            }
            Err(error) => task.fail(&id, format!("Protect the downloaded chapter: {error:#}")),
        }
        return Ok(());
    }

    let candidate = if task
        .waiting_active
        .as_deref()
        .is_some_and(|id| active != Some(id))
    {
        let id = task
            .waiting_active
            .take()
            .expect("The active chapter is present");
        if task.finished.contains(&id) {
            return Ok(());
        }
        match controller.store.book_download_candidate(&task.book_id, &id) {
            Ok(candidate) => candidate,
            Err(error) => {
                task.fail(&id, format!("Inspect the drained chapter: {error:#}"));
                return Ok(());
            }
        }
    } else {
        if task.candidates.is_empty() && !task.exhausted {
            task.candidates = controller
                .store
                .book_download_candidates(&task.book_id, task.cursor.as_deref(), 128)?
                .into();
            if task.candidates.is_empty() {
                task.exhausted = true;
            }
        }
        let Some(candidate) = task.candidates.pop_front() else {
            return Ok(());
        };
        task.cursor = Some(candidate.chapter_id.clone());
        if task.finished.contains(&candidate.chapter_id) {
            return Ok(());
        }
        if active == Some(candidate.chapter_id.as_str()) {
            task.active_ids.insert(candidate.chapter_id.clone());
            task.waiting_active = Some(candidate.chapter_id.clone());
            controller
                .removal_active
                .borrow_mut()
                .insert(candidate.chapter_id.clone());
            if let Err(error) = change_state_with_notice(
                controller,
                &candidate.chapter_id,
                "cancelled",
                active,
                false,
            ) {
                task.fail(
                    &candidate.chapter_id,
                    format!("Cancel the active chapter: {error:#}"),
                );
            }
            return Ok(());
        }
        match controller
            .store
            .book_download_candidate(&task.book_id, &candidate.chapter_id)
        {
            Ok(candidate) => candidate,
            Err(error) => {
                task.fail(
                    &candidate.chapter_id,
                    format!("Inspect the downloaded chapter: {error:#}"),
                );
                return Ok(());
            }
        }
    };
    let Some(mut candidate) = candidate else {
        return Ok(());
    };
    let id = candidate.chapter_id.clone();
    if candidate
        .job_state
        .as_deref()
        .is_some_and(|state| !matches!(state, "completed" | "cancelled"))
    {
        if let Err(error) = change_state_with_notice(controller, &id, "cancelled", active, false) {
            task.fail(&id, format!("Cancel the downloaded chapter: {error:#}"));
            return Ok(());
        }
        match controller.store.book_download_candidate(&task.book_id, &id) {
            Ok(Some(updated)) => candidate = updated,
            Ok(None) => return Ok(()),
            Err(error) => {
                task.fail(&id, format!("Inspect the cancelled chapter: {error:#}"));
                return Ok(());
            }
        }
    }
    if candidate.source_id == "local" || (candidate.job_state.is_none() && !candidate.downloaded) {
        match controller
            .store
            .finish_book_download_removal(&task.book_id, &id)
        {
            Ok(report) => task.complete_chapter(&id, report.kind, report.usage_changed),
            Err(error) => task.fail(&id, format!("Remove the local queue record: {error:#}")),
        }
    } else {
        task.current = Some(BookChapterRemoval {
            candidate,
            offset: 0,
            protecting: true,
            unpinned: false,
            leases: Vec::new(),
            paths: HashSet::new(),
            failure: None,
            compensation_errors: Vec::new(),
            restore_paths: VecDeque::new(),
        });
    }
    Ok(())
}

fn advance_book_chapter(
    controller: &Controller<'_>,
    book_id: &str,
    current: &mut BookChapterRemoval,
    active: Option<&str>,
) -> Result<BookChapterStep> {
    let id = &current.candidate.chapter_id;
    if current.failure.is_some() {
        for _ in 0..64 {
            suspend_barrier(controller, active)?;
            let Some(path) = current.restore_paths.pop_front() else {
                break;
            };
            if path.is_file() {
                let page = Page {
                    local_path: Some(path),
                    ..Page::default()
                };
                if let Err(error) = controller.cache.pin_raw(&page, &owner(id)) {
                    if current.compensation_errors.len() < 4 {
                        current.compensation_errors.push(format!("{error:#}"));
                    }
                }
            }
        }
        if !current.restore_paths.is_empty() {
            return Ok(BookChapterStep::Pending);
        }
        current.unpinned = false;
        let error = current
            .failure
            .take()
            .expect("The compensation retains its original error");
        let detail = if current.compensation_errors.is_empty() {
            error
        } else {
            format!(
                "{error}; restore cache protection: {}",
                current.compensation_errors.join("; ")
            )
        };
        return Ok(BookChapterStep::Failed(detail));
    }
    let window = controller.store.page_window(id, current.offset, 64)?;
    for page in &window.pages {
        suspend_barrier(controller, active)?;
        let Some(path) = page.local_path.as_ref() else {
            continue;
        };
        match std::fs::metadata(path) {
            Ok(metadata) if metadata.is_file() => {}
            Ok(_) => continue,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => {
                return Err(error).context("Inspect the downloaded image during book removal");
            }
        }
        if current.paths.insert(path.clone()) {
            match controller.cache.lease_path(path) {
                Ok(lease) => current.leases.push(lease),
                Err(error) if missing_or_invalid_lease(&error) => {}
                Err(error) => {
                    return Err(error).context("Lease the downloaded image during book removal");
                }
            }
        }
    }
    current.offset = current.offset.saturating_add(window.pages.len());
    if current.offset < window.total && !window.pages.is_empty() {
        return Ok(BookChapterStep::Pending);
    }
    if current.protecting {
        current.protecting = false;
        return Ok(BookChapterStep::Pending);
    }
    suspend_barrier(controller, active)?;
    current.unpinned = true;
    controller
        .cache
        .unpin_owner(&owner(id))
        .context("Release this book chapter's download owner")?;
    let report = controller.store.finish_book_download_removal(book_id, id)?;
    current.unpinned = false;
    Ok(BookChapterStep::Done(report.kind, report.usage_changed))
}

fn managed_raw_removal_path(cache: &CacheManager, path: &Path) -> bool {
    let root = cache.namespace().join("raw");
    let Ok(relative) = path.strip_prefix(root) else {
        return false;
    };
    let mut parts = relative.components();
    let (Some(shard), Some(file), None) = (parts.next(), parts.next(), parts.next()) else {
        return false;
    };
    let (Some(shard), Some(file)) = (shard.as_os_str().to_str(), file.as_os_str().to_str()) else {
        return false;
    };
    let Some(key) = file.strip_suffix(".raw") else {
        return false;
    };
    key.len() == 64 && key.bytes().all(|byte| byte.is_ascii_hexdigit()) && shard == &key[..2]
}

/// A shutdown never drops rollback leases before making the same best-effort
/// pin compensation as a single completed-download removal.
fn finish_book_compensations(controller: &Controller<'_>) {
    let tasks = std::mem::take(&mut *controller.book_removals.borrow_mut());
    for mut task in tasks {
        let Some(mut current) = task.current.take().filter(|value| value.unpinned) else {
            continue;
        };
        // The controller is already leaving, possibly because the primary DB
        // checkpoint failed. Compensation needs cache paths, not that DB.
        for path in current.restore_paths.drain(..) {
            if path.is_file() {
                let page = Page {
                    local_path: Some(path),
                    ..Page::default()
                };
                if let Err(error) = controller
                    .cache
                    .pin_raw(&page, &owner(&current.candidate.chapter_id))
                {
                    eprintln!("aidoku: restore book download pins: {error:#}");
                }
            }
        }
    }
}

struct BookCompensationGuard<'a, 'b> {
    controller: &'a Controller<'b>,
}

impl Drop for BookCompensationGuard<'_, '_> {
    fn drop(&mut self) {
        finish_book_compensations(self.controller);
    }
}
fn halt_state(controller: &Controller<'_>, chapter_id: &str) -> Option<&'static str> {
    if controller.removal_active.borrow().contains(chapter_id) {
        return Some("cancelled");
    }
    match controller
        .controls
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .get(chapter_id)
    {
        Some(Control::Paused) => Some("paused"),
        Some(Control::Cancelled) => Some("cancelled"),
        _ if controller.stopping.load(Ordering::Acquire) => Some("queued"),
        _ if !controller.suspend.network_ready.load(Ordering::Acquire) => {
            if controller
                .suspend
                .owned
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .contains(chapter_id)
            {
                Some("paused")
            } else {
                Some("queued")
            }
        }
        _ => None,
    }
}

fn sync_control(
    controller: &Controller<'_>,
    job: &mut DownloadJob,
    dirty: &mut Vec<(u32, Page)>,
) -> Result<Option<&'static str>> {
    drain_commands(controller, Some(&job.chapter_id))?;
    let halt = halt_state(controller, &job.chapter_id);
    let desired = halt.unwrap_or("downloading");
    if job.state != desired {
        job.state = desired.into();
        job.error.clear();
        controller.store.record_download_progress(job, dirty)?;
        dirty.clear();
        let _ = controller
            .events
            .send(DownloadEvent::StateChanged(job.clone()));
    }
    Ok(halt)
}

fn finish_halt(
    controller: &Controller<'_>,
    job: &mut DownloadJob,
    dirty: &mut Vec<(u32, Page)>,
) -> Result<()> {
    job.state = halt_state(controller, &job.chapter_id)
        .unwrap_or("queued")
        .into();
    job.error.clear();
    controller.store.record_download_progress(job, dirty)?;
    dirty.clear();
    if job.state == "cancelled" && !book_removal_pending(controller, &job.book_id) {
        controller.cache.unpin_owner(&owner(&job.chapter_id))?;
    }
    let _ = controller
        .events
        .send(DownloadEvent::StateChanged(job.clone()));
    Ok(())
}

fn load_manifest(
    controller: &Controller<'_>,
    manager: Arc<SourceManager>,
    book: Book,
    chapter: Chapter,
    job: &mut DownloadJob,
) -> Result<Option<Vec<Page>>> {
    let (sender, receiver) = mpsc::channel();
    let epoch = controller.suspend.epoch.load(Ordering::Acquire);
    let suspend = controller.suspend.clone();
    let activity = ActivityGuard::new(controller.suspend, true);
    let handle = thread::Builder::new()
        .name("aidoku-download-manifest".into())
        .spawn(move || {
            let _activity = activity;
            let interrupted = epoch != suspend.epoch.load(Ordering::Acquire)
                || !suspend.network_ready.load(Ordering::Acquire)
                || suspend.strict.load(Ordering::Acquire);
            let result = if interrupted {
                Err(anyhow!("The page-list request was paused for suspend"))
            } else {
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    manager.pages(&book, &chapter)
                }))
                .unwrap_or_else(|_| Err(anyhow!("The chapter page-list request panicked")))
            };
            let _ = sender.send((epoch, interrupted, result));
        })
        .context("Start the chapter page-list request")?;
    let mut fetches = Fetches::default();
    fetches.threads.insert(usize::MAX, handle);
    let mut dirty = Vec::new();
    loop {
        sync_control(controller, job, &mut dirty)?;
        match receiver.recv_timeout(COMMAND_INTERVAL) {
            Ok((epoch, interrupted, result)) => {
                if let Some(handle) = fetches.threads.remove(&usize::MAX) {
                    let _ = handle.join();
                }
                suspend_barrier(controller, Some(&job.chapter_id))?;
                if result.as_ref().is_err_and(|error| {
                    interrupted
                        || epoch != controller.suspend.epoch.load(Ordering::Acquire)
                        || is_network_interrupted(error)
                }) {
                    finish_halt(controller, job, &mut dirty)?;
                    return Ok(None);
                }
                if halt_state(controller, &job.chapter_id).is_some()
                    && (result.is_err()
                        || controller.stopping.load(Ordering::Acquire)
                        || halt_state(controller, &job.chapter_id) == Some("cancelled"))
                {
                    finish_halt(controller, job, &mut dirty)?;
                    return Ok(None);
                }
                return result.map(Some).context("Load the chapter page list");
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                return Err(anyhow!("The chapter page-list worker stopped unexpectedly"));
            }
        }
    }
}

fn run_job(
    controller: &Controller<'_>,
    managers: &mut ManagerSlot,
    job: &mut DownloadJob,
    concurrency: usize,
) -> Result<()> {
    let mut dirty = Vec::new();
    job.completed = 0;
    if sync_control(controller, job, &mut dirty)?.is_some() {
        return finish_halt(controller, job, &mut dirty);
    }
    let book = controller
        .store
        .book(&job.book_id)?
        .context("The queued book no longer exists")?;
    let chapter = controller
        .store
        .chapter(&job.chapter_id)?
        .context("The queued chapter no longer exists")?;
    let mut pages = controller.store.pages(&chapter.id)?;
    if pages.is_empty() {
        let Some(mut loaded) = load_manifest(
            controller,
            managers.get(controller)?,
            book,
            chapter.clone(),
            job,
        )?
        else {
            return Ok(());
        };
        ensure!(
            !loaded.is_empty() && loaded.len() <= 20_000,
            "The source returned an empty or oversized chapter"
        );
        for page in &mut loaded {
            suspend_barrier(controller, Some(&job.chapter_id))?;
            if page
                .local_path
                .as_ref()
                .is_some_and(|path| controller.cache.validate_path(path).is_err())
            {
                page.local_path = None;
            }
        }
        controller.store.save_pages(&chapter.id, &loaded)?;
        pages = loaded;
    }
    ensure!(
        !pages.is_empty() && pages.len() <= 20_000,
        "The chapter has an invalid page manifest"
    );
    let durable_owner = owner(&chapter.id);
    let mut verified = vec![false; pages.len()];
    let mut waiting = VecDeque::new();
    job.total = pages.len() as u32;
    job.completed = 0;
    for (index, page) in pages.iter_mut().enumerate() {
        if sync_control(controller, job, &mut dirty)?.is_some() {
            return finish_halt(controller, job, &mut dirty);
        }
        if let Some(path) = page.local_path.as_ref() {
            if controller.cache.validate_path(path).is_ok() {
                verified[index] = true;
                job.completed += 1;
                if !page.url.is_empty() {
                    controller.cache.pin_raw(page, &durable_owner)?;
                }
                continue;
            }
            controller
                .store
                .set_chapter_downloaded(&chapter.id, false)?;
            if !page.url.is_empty() {
                controller
                    .store
                    .invalidate_page_cache(&chapter.id, index as u32)?;
                page.local_path = None;
            }
        }
        ensure!(
            !page.url.is_empty(),
            "A local comic page is missing or cannot be decoded; import the original comic again"
        );
        waiting.push_back(index);
    }
    controller.store.record_download_progress(job, &[])?;
    let _ = controller.events.send(DownloadEvent::Progress(job.clone()));
    if waiting.is_empty() {
        return complete(controller, job, &pages, &mut dirty);
    }

    let mut manager = managers.get(controller)?;
    let (sender, receiver) = mpsc::channel::<FetchResult>();
    let mut fetches = Fetches::default();
    let mut failure = None;
    let mut run_epoch = controller.suspend.epoch.load(Ordering::Acquire);
    let mut last_flush = Instant::now();
    loop {
        let halt = sync_control(controller, job, &mut dirty)?;
        let current_epoch = controller.suspend.epoch.load(Ordering::Acquire);
        if current_epoch != run_epoch {
            failure = None;
            for (index, ready) in verified.iter().enumerate() {
                if !*ready && !fetches.threads.contains_key(&index) && !waiting.contains(&index) {
                    waiting.push_back(index);
                }
            }
            run_epoch = current_epoch;
        }
        if halt.is_none() && failure.is_none() && !manager.network_control().is_suspended() {
            if !manager.is_network_current() {
                manager = managers.get(controller)?;
            }
            while fetches.threads.len() < concurrency {
                if halt_state(controller, &job.chapter_id).is_some() {
                    break;
                }
                let Some(index) = waiting.pop_front() else {
                    break;
                };
                let request = pages[index].clone();
                let manager = manager.clone();
                let sender = sender.clone();
                let pin_owner = durable_owner.clone();
                let source_id = chapter.source_id.clone();
                let epoch = run_epoch;
                let suspend = controller.suspend.clone();
                let controls = controller.controls.clone();
                let chapter_id = chapter.id.clone();
                let activity = ActivityGuard::new(controller.suspend, true);
                match thread::Builder::new()
                    .name("aidoku-download-page".into())
                    .spawn(move || {
                        let _activity = activity;
                        let interrupted = epoch != suspend.epoch.load(Ordering::Acquire)
                            || !suspend.network_ready.load(Ordering::Acquire)
                            || suspend.strict.load(Ordering::Acquire)
                            || matches!(
                                controls
                                    .lock()
                                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                                    .get(&chapter_id),
                                Some(Control::Paused | Control::Cancelled)
                            );
                        let path = if interrupted {
                            Err(anyhow!(
                                "The image request was paused or cancelled before dispatch"
                            ))
                        } else {
                            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                                crate::imaging::with_background_decode(None, || {
                                    manager.fetch_page_for_source(
                                        &source_id,
                                        &request,
                                        FetchPriority::Background,
                                        Some(&pin_owner),
                                    )
                                })
                            }))
                            .unwrap_or_else(|_| Err(anyhow!("The comic page request panicked")))
                        };
                        let _ = sender.send(FetchResult {
                            index,
                            path,
                            epoch,
                            interrupted,
                        });
                    }) {
                    Ok(handle) => {
                        fetches.threads.insert(index, handle);
                    }
                    Err(error) => {
                        failure = Some(anyhow!(error).context("Start a background page fetch"));
                        break;
                    }
                }
            }
        }
        if fetches.threads.is_empty() {
            if halt.is_some() {
                return finish_halt(controller, job, &mut dirty);
            }
            if let Some(error) = failure {
                flush_progress(controller, job, &mut dirty)?;
                return Err(error);
            }
            if waiting.is_empty() {
                return complete(controller, job, &pages, &mut dirty);
            }
        }
        match receiver.recv_timeout(COMMAND_INTERVAL) {
            Ok(result) => {
                accept_result(
                    controller,
                    job,
                    &mut pages,
                    &mut verified,
                    &mut dirty,
                    &mut fetches,
                    &mut failure,
                    &mut waiting,
                    result,
                );
                while let Ok(result) = receiver.try_recv() {
                    accept_result(
                        controller,
                        job,
                        &mut pages,
                        &mut verified,
                        &mut dirty,
                        &mut fetches,
                        &mut failure,
                        &mut waiting,
                        result,
                    );
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                failure = Some(anyhow!("The page fetch channel closed unexpectedly"));
            }
        }
        if !dirty.is_empty()
            && (dirty.len() >= 2 || last_flush.elapsed() >= PROGRESS_INTERVAL)
            && !(fetches.threads.is_empty() && waiting.is_empty() && failure.is_none())
        {
            flush_progress(controller, job, &mut dirty)?;
            last_flush = Instant::now();
        }
    }
}

fn accept_result(
    controller: &Controller<'_>,
    job: &mut DownloadJob,
    pages: &mut [Page],
    verified: &mut [bool],
    dirty: &mut Vec<(u32, Page)>,
    fetches: &mut Fetches,
    failure: &mut Option<anyhow::Error>,
    waiting: &mut VecDeque<usize>,
    result: FetchResult,
) {
    if let Some(handle) = fetches.threads.remove(&result.index) {
        let _ = handle.join();
    }
    match result.path.and_then(|path| {
        crate::imaging::with_background_decode(None, || controller.cache.validate_path(&path))?;
        Ok(path)
    }) {
        Ok(path) => {
            if let Some(page) = pages.get_mut(result.index) {
                page.local_path = Some(path);
                if !verified[result.index] {
                    verified[result.index] = true;
                    job.completed += 1;
                }
                dirty.push((result.index as u32, page.clone()));
            }
        }
        Err(error)
            if result.interrupted
                || result.epoch != controller.suspend.epoch.load(Ordering::Acquire)
                || is_network_interrupted(&error) =>
        {
            if !waiting.contains(&result.index) {
                waiting.push_back(result.index);
            }
        }
        Err(error) => {
            if failure.is_none() {
                *failure = Some(error.context("Download a chapter image"));
            }
        }
    }
}

fn flush_progress(
    controller: &Controller<'_>,
    job: &mut DownloadJob,
    dirty: &mut Vec<(u32, Page)>,
) -> Result<()> {
    suspend_barrier(controller, Some(&job.chapter_id))?;
    if let Some(state) = halt_state(controller, &job.chapter_id) {
        job.state = state.into();
        job.error.clear();
    }
    controller.store.record_download_progress(job, dirty)?;
    dirty.clear();
    let _ = controller.events.send(DownloadEvent::Progress(job.clone()));
    Ok(())
}

fn complete(
    controller: &Controller<'_>,
    job: &mut DownloadJob,
    pages: &[Page],
    dirty: &mut Vec<(u32, Page)>,
) -> Result<()> {
    ensure!(!pages.is_empty(), "An empty chapter cannot be downloaded");
    for (index, page) in pages.iter().enumerate() {
        if sync_control(controller, job, dirty)?.is_some() {
            return finish_halt(controller, job, dirty);
        }
        let path = page
            .local_path
            .as_ref()
            .context("A downloaded page is not saved locally")?;
        if let Err(error) = controller.cache.validate_path(path) {
            controller
                .store
                .set_chapter_downloaded(&job.chapter_id, false)?;
            if !page.url.is_empty() {
                controller
                    .store
                    .invalidate_page_cache(&job.chapter_id, index as u32)?;
            }
            return Err(error).context("A downloaded image is missing or invalid");
        }
    }
    if !prepare_downloaded_pages(controller, job, pages, dirty)? {
        return finish_halt(controller, job, dirty);
    }
    if sync_control(controller, job, dirty)?.is_some() {
        return finish_halt(controller, job, dirty);
    }
    job.total = pages.len() as u32;
    job.completed = job.total;
    job.state = "completed".into();
    job.error.clear();
    controller.store.record_download_progress(job, dirty)?;
    dirty.clear();
    let _ = controller
        .events
        .send(DownloadEvent::Completed(job.clone()));
    Ok(())
}

fn prepare_downloaded_pages(
    controller: &Controller<'_>,
    job: &mut DownloadJob,
    pages: &[Page],
    dirty: &mut Vec<(u32, Page)>,
) -> Result<bool> {
    let preferences = controller.store.preferences()?;
    let settings = processing::ProcessingSettings::from_preferences(
        &preferences,
        controller.processing_viewport.get(),
    )?;
    if !settings.enabled() {
        return Ok(true);
    }
    let mut failures = 0usize;
    let mut last_error = String::new();
    for (index, page) in pages.iter().enumerate() {
        if sync_control(controller, job, dirty)?.is_some() {
            return Ok(false);
        }
        let original = page
            .local_path
            .clone()
            .context("The offline preparation original is missing")?;
        let page = page.clone();
        let cache = controller.cache.clone();
        let pin_owner = owner(&job.chapter_id);
        let chapter_id = job.chapter_id.clone();
        let controls = controller.controls.clone();
        let suspend = controller.suspend.clone();
        let epoch = suspend.epoch.load(Ordering::Acquire);
        let cancellation = crate::imaging::DecodeCancellation::new();
        let worker_cancellation = cancellation.clone();
        let (sender, receiver) = mpsc::channel();
        let activity = ActivityGuard::new(controller.suspend, false);
        let handle = thread::Builder::new()
            .name("aidoku-download-prepare".into())
            .spawn(move || {
                let _activity = activity;
                let stopped = || {
                    epoch != suspend.epoch.load(Ordering::Acquire)
                        || suspend.strict.load(Ordering::Acquire)
                        || matches!(
                            controls
                                .lock()
                                .unwrap_or_else(std::sync::PoisonError::into_inner)
                                .get(&chapter_id),
                            Some(Control::Paused | Control::Cancelled)
                        )
                };
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    crate::imaging::with_background_decode(Some(&worker_cancellation), || {
                        processing::prepare(
                            &cache,
                            &page,
                            &original,
                            settings,
                            &pin_owner,
                            &worker_cancellation,
                            stopped,
                        )
                    })
                }))
                .unwrap_or_else(|_| Err(anyhow!("The offline image preparation worker panicked")));
                let _ = sender.send(result);
            })
            .context("Start offline image preparation")?;
        let mut joining = Fetches::default();
        joining.threads.insert(index, handle);
        let outcome = loop {
            // Cancel before entering a sleep barrier, which intentionally parks
            // the controller until wake. The worker also checks epoch and input
            // state itself before it is permitted to publish the prepared file.
            if controller.suspend.strict.load(Ordering::Acquire)
                || epoch != controller.suspend.epoch.load(Ordering::Acquire)
                || halt_state(controller, &job.chapter_id).is_some()
            {
                cancellation.cancel();
            }
            let halt = sync_control(controller, job, dirty)?;
            if halt.is_some() {
                cancellation.cancel();
            }
            match receiver.recv_timeout(COMMAND_INTERVAL) {
                Ok(result) => break result,
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    break Err(anyhow!(
                        "The offline preparation worker stopped unexpectedly"
                    ));
                }
            }
        };
        drop(joining);
        if cancellation.is_cancelled() {
            return Ok(false);
        }
        if let Err(error) = outcome {
            failures += 1;
            last_error = format!("{error:#}");
        }
    }
    if failures > 0 {
        // Optional accelerators must not turn a verified offline original into
        // a failed download. The user receives a truthful, nonfatal explanation.
        let _ = controller.events.send(DownloadEvent::Error(format!(
            "Offline originals were retained; preparation failed for {failures} image(s): {last_error}"
        )));
    }
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn startup_failures_are_returned_synchronously_and_can_be_retried() {
        let directory = tempfile::tempdir().unwrap();
        let invalid_database = directory.path().join("database-is-a-directory");
        std::fs::create_dir(&invalid_database).unwrap();
        let error = DownloadWorker::start_with_options(
            directory.path().to_path_buf(),
            invalid_database,
            true,
        )
        .err()
        .expect("The database initialization must fail");
        let error = format!("{error:#}");
        assert!(
            error.contains("Open the download database connection"),
            "{error}"
        );
        assert!(error.contains("Open application database"), "{error}");
        assert!(!directory.path().join("cache").exists());

        let database = directory.path().join("retry.sqlite");
        let invalid_index = directory.path().join("cache/shared-v1/index.sqlite3");
        std::fs::create_dir_all(&invalid_index).unwrap();
        let error = DownloadWorker::start_with_options(
            directory.path().to_path_buf(),
            database.clone(),
            true,
        )
        .err()
        .expect("The cache initialization must fail");
        let error = format!("{error:#}");
        assert!(
            error.contains("Open the shared download image cache"),
            "{error}"
        );
        assert!(
            error.contains("The cache index must be a regular file"),
            "{error}"
        );
        let connection = rusqlite::Connection::open(&database).unwrap();
        connection.busy_timeout(Duration::ZERO).unwrap();
        connection
            .execute_batch("BEGIN EXCLUSIVE; ROLLBACK;")
            .unwrap();
        drop(connection);
        std::fs::remove_dir(invalid_index).unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        stop(&worker);
    }

    #[test]
    fn standalone_startup_waits_for_the_cache_and_recovers_before_returning() {
        for recover_saved_queue in [false, true] {
            let directory = tempfile::tempdir().unwrap();
            let database = if recover_saved_queue {
                local_fixture(directory.path(), "downloading", true).0
            } else {
                directory.path().join("state.sqlite")
            };
            let index_path = directory.path().join("cache/shared-v1/index.sqlite3");
            std::fs::create_dir_all(index_path.parent().unwrap()).unwrap();
            let blocker = rusqlite::Connection::open(&index_path).unwrap();
            blocker
                .execute_batch("CREATE TABLE startup_lock(value INTEGER); BEGIN EXCLUSIVE;")
                .unwrap();
            let (entered_sender, entered) = mpsc::channel();
            let (ready_sender, ready) = mpsc::channel();
            let data_dir = directory.path().to_path_buf();
            let startup_database = database.clone();
            let starter = thread::spawn(move || {
                entered_sender.send(()).unwrap();
                let result = DownloadWorker::start_with_options(data_dir, startup_database, true);
                assert!(ready_sender.send(result).is_ok());
            });
            entered.recv_timeout(Duration::from_secs(5)).unwrap();
            // The real cache transaction prevents initialization from finishing.
            // A successful worker must remain unpublished until it is released.
            let premature = ready.recv_timeout(Duration::from_millis(100));
            blocker.execute_batch("ROLLBACK;").unwrap();
            let (waited, result) = match premature {
                Ok(result) => (false, result),
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    (true, ready.recv_timeout(Duration::from_secs(10)).unwrap())
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => panic!("The startup caller stopped"),
            };
            starter.join().unwrap();
            let worker = result.unwrap();
            let store = Store::open_read_only(&database).unwrap();
            assert!(store.suspended_download_ids().unwrap().is_empty());
            let stats: usize = store
                .test_connection()
                .query_row("SELECT count(*) FROM chapter_page_stats", [], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(stats, if recover_saved_queue { 1 } else { 0 });
            if recover_saved_queue {
                assert_eq!(
                    store.download("local:chapter").unwrap().unwrap().state,
                    "queued"
                );
            }
            let index = rusqlite::Connection::open_with_flags(
                &index_path,
                rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
            )
            .unwrap();
            let artifacts: usize = index
                .query_row("SELECT count(*) FROM artifacts", [], |row| row.get(0))
                .unwrap();
            assert_eq!(artifacts, 0);
            stop(&worker);
            assert!(
                waited,
                "Startup returned before the cache lock was released"
            );
        }
    }

    #[test]
    fn initialized_startup_preserves_a_live_queue_and_loads_sleep_ownership() {
        let directory = tempfile::tempdir().unwrap();
        let (database, book, chapter) = local_fixture(directory.path(), "downloading", true);
        let store = Store::open_existing_for_write(&database).unwrap();
        let automatic = Chapter {
            id: "local:automatic".into(),
            remote_id: "automatic".into(),
            ..chapter.clone()
        };
        store
            .save_chapters(&book.id, &[chapter.clone(), automatic.clone()])
            .unwrap();
        store
            .save_download(&DownloadJob {
                chapter_id: automatic.id.clone(),
                book_id: book.id.clone(),
                state: "paused".into(),
                ..DownloadJob::default()
            })
            .unwrap();
        store
            .test_connection()
            .execute(
                "INSERT INTO download_sleep_ownership(chapter_id) VALUES(?1)",
                [&automatic.id],
            )
            .unwrap();
        let _cache = CacheManager::open(directory.path()).unwrap();
        // The initialized path can read an application-owned write transaction;
        // rerunning schema migration or recovery would require another writer.
        store
            .test_connection()
            .execute_batch("BEGIN IMMEDIATE;")
            .unwrap();
        let result = DownloadWorker::start_initialized(
            directory.path().to_path_buf(),
            database,
            DownloadOptions {
                offline: true,
                ..DownloadOptions::default()
            },
            false,
        );
        store.test_connection().execute_batch("ROLLBACK;").unwrap();
        let worker = result.unwrap();
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "downloading"
        );
        assert_eq!(
            *worker.suspend.owned.lock().unwrap(),
            BTreeSet::from([automatic.id.clone()])
        );
        assert_eq!(store.suspended_download_ids().unwrap().len(), 1);
        assert_eq!(
            store.download(&automatic.id).unwrap().unwrap().state,
            "paused"
        );
        stop(&worker);
    }

    #[test]
    fn failed_startup_acknowledgements_join_threads_before_returning() {
        for exit in ["failure", "disconnect", "panic"] {
            let (startup_sender, startup) = mpsc::channel();
            let (entered_sender, entered) = mpsc::channel();
            let (release_sender, release) = mpsc::channel();
            let worker = thread::spawn(move || {
                if exit == "failure" {
                    startup_sender
                        .send(Err(anyhow!("Test startup failure")))
                        .unwrap();
                }
                drop(startup_sender);
                entered_sender.send(()).unwrap();
                release.recv().unwrap();
                assert_ne!(exit, "panic", "Test startup panic");
            });
            let (returned_sender, returned) = mpsc::channel();
            let waiter = thread::spawn(move || {
                let result = await_worker_startup(startup, worker);
                returned_sender
                    .send(result.err().map(|error| format!("{error:#}")))
                    .unwrap();
            });
            entered.recv_timeout(Duration::from_secs(5)).unwrap();
            let premature = returned.recv_timeout(Duration::from_millis(100));
            release_sender.send(()).unwrap();
            let (waited, error) = match premature {
                Ok(error) => (false, error),
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    (true, returned.recv_timeout(Duration::from_secs(5)).unwrap())
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => panic!("The startup waiter stopped"),
            };
            waiter.join().unwrap();
            assert!(waited, "Startup returned while its failed thread was alive");
            let error = error.expect("Startup must not succeed without readiness");
            let expected = match exit {
                "failure" => "Test startup failure",
                "panic" => "panicked before initialization completed",
                _ => "stopped before initialization completed",
            };
            assert!(error.contains(expected), "{error}");
        }
    }

    #[test]
    fn a_runtime_failure_preserves_its_event_and_later_command_diagnostics() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("runtime-failure.sqlite");
        let worker = DownloadWorker::start_with_options(
            directory.path().to_path_buf(),
            database.clone(),
            true,
        )
        .unwrap();
        let connection = rusqlite::Connection::open(database).unwrap();
        connection
            .execute_batch("DROP TABLE download_sleep_ownership;")
            .unwrap();
        let _ = worker.pause_for_suspend();
        assert!(worker.wait_stopped(Duration::from_secs(3)));
        let error = worker.pause_for_suspend().unwrap_err().to_string();
        assert!(
            error.contains("The download worker is unavailable"),
            "{error}"
        );
        let saved = worker.terminal_error.lock().unwrap().clone().unwrap();
        assert!(error.ends_with(&saved));
        assert!(
            saved.contains("no such table: download_sleep_ownership"),
            "{saved}"
        );
        assert!(
            worker
                .events
                .try_iter()
                .any(|event| { matches!(event, DownloadEvent::Error(cause) if cause == saved) })
        );
        worker.shutdown();
        let stopping = worker.pause_for_suspend().unwrap_err().to_string();
        assert!(stopping.contains("shutting down"), "{stopping}");
        assert!(!stopping.contains("unavailable"), "{stopping}");
    }
    #[derive(Debug)]
    enum SqliteBusyObservation {
        Entered(usize),
        Exhausted { attempt: usize, waited: Duration },
    }

    struct SqliteBusyObserver {
        events: Sender<SqliteBusyObservation>,
        attempt: usize,
        started: Instant,
    }

    thread_local! {
        static SQLITE_BUSY_OBSERVER: RefCell<Option<SqliteBusyObserver>> = const {
            RefCell::new(None)
        };
    }

    fn observe_sqlite_busy(retries: i32) -> bool {
        let retry = SQLITE_BUSY_OBSERVER.with(|state| {
            let mut state = state.borrow_mut();
            let Some(observer) = state.as_mut() else {
                return false;
            };
            if retries == 0 {
                observer.attempt += 1;
                observer.started = Instant::now();
                let _ = observer
                    .events
                    .send(SqliteBusyObservation::Entered(observer.attempt));
            }
            let waited = observer.started.elapsed();
            if waited >= Duration::from_secs(5) {
                let _ = observer.events.send(SqliteBusyObservation::Exhausted {
                    attempt: observer.attempt,
                    waited,
                });
                return false;
            }
            true
        });
        if retry {
            thread::sleep(Duration::from_millis(10));
        }
        retry
    }

    fn observed_runtime_worker(
        data_dir: PathBuf,
        database_path: PathBuf,
    ) -> (DownloadWorker, Receiver<SqliteBusyObservation>) {
        observed_runtime_worker_with_options(data_dir, database_path, DownloadOptions::default())
    }

    fn observed_runtime_worker_with_options(
        data_dir: PathBuf,
        database_path: PathBuf,
        options: DownloadOptions,
    ) -> (DownloadWorker, Receiver<SqliteBusyObservation>) {
        let (commands, receiver) = mpsc::channel();
        let (events_sender, events) = mpsc::channel();
        let (startup_sender, startup) = mpsc::channel::<Result<()>>();
        let (observed_sender, observed) = mpsc::channel();
        let controls = Arc::new(Mutex::new(HashMap::new()));
        let stopping = Arc::new(AtomicBool::new(false));
        let stopped = Arc::new((Mutex::new(false), Condvar::new()));
        let terminal_error = Arc::new(Mutex::new(None));
        let suspend = Arc::new(SuspendState::new(false));
        let thread_controls = controls.clone();
        let thread_stopping = stopping.clone();
        let thread_stopped = stopped.clone();
        let thread_terminal_error = terminal_error.clone();
        let thread_suspend = suspend.clone();
        let join_handle = thread::Builder::new()
            .name("aidoku-busy-test".into())
            .spawn(move || {
                let mut startup_sender = Some(startup_sender);
                let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    let store = Store::open_existing_for_write(&database_path)?;
                    let cache = CacheManager::open(&data_dir)?;
                    *thread_suspend.owned.lock().unwrap() = store.suspended_download_ids()?;
                    SQLITE_BUSY_OBSERVER.with(|state| {
                        *state.borrow_mut() = Some(SqliteBusyObserver {
                            events: observed_sender,
                            attempt: 0,
                            started: Instant::now(),
                        });
                    });
                    // Observe real SQLite contention with the same five-second
                    // wait as the production connection. Returning false causes
                    // SQLite itself to return BUSY; no SQL error is mocked.
                    store
                        .test_connection()
                        .busy_handler(Some(observe_sqlite_busy))?;
                    startup_sender
                        .take()
                        .unwrap()
                        .send(Ok(()))
                        .map_err(|_| anyhow!("The observed startup receiver stopped"))?;
                    // Exercise the unchanged production loop and public command
                    // methods. Startup-wrapper behavior has separate tests.
                    worker_loop(
                        &data_dir,
                        &store,
                        &cache,
                        &receiver,
                        &events_sender,
                        &thread_controls,
                        &thread_stopping,
                        &thread_suspend,
                        options,
                    )
                }))
                .unwrap_or_else(|_| Err(anyhow!("The observed download worker panicked")));
                if let Err(error) = outcome {
                    let cause = format!("{error:#}");
                    *thread_terminal_error.lock().unwrap() = Some(cause.clone());
                    if let Some(startup_sender) = startup_sender.take() {
                        let _ = startup_sender.send(Err(error));
                    }
                    let _ = events_sender.send(DownloadEvent::Error(cause));
                }
                SQLITE_BUSY_OBSERVER.with(|state| {
                    let _ = state.borrow_mut().take();
                });
                // worker_loop has returned and both connections have closed.
                let (finished, changed) = &*thread_stopped;
                *finished.lock().unwrap() = true;
                changed.notify_all();
            })
            .unwrap();
        let join_handle = await_worker_startup(startup, join_handle).unwrap();
        (
            DownloadWorker {
                commands,
                events,
                controls,
                stopping,
                stopped,
                terminal_error,
                join_handle: Mutex::new(Some(join_handle)),
                suspend,
            },
            observed,
        )
    }

    fn next_busy_observation(
        worker: &DownloadWorker,
        observed: &Receiver<SqliteBusyObservation>,
    ) -> SqliteBusyObservation {
        observed
            .recv_timeout(Duration::from_secs(10))
            .unwrap_or_else(|error| {
                panic!(
                    "The SQLite observation stopped: {error:?}; terminal error: {:?}; \
                     pending events: {:?}",
                    *worker.terminal_error.lock().unwrap(),
                    worker.drain_events()
                )
            })
    }

    fn expect_busy_entered(
        worker: &DownloadWorker,
        observed: &Receiver<SqliteBusyObservation>,
        attempt: usize,
    ) {
        assert!(
            matches!(
                next_busy_observation(worker, observed),
                SqliteBusyObservation::Entered(actual) if actual == attempt
            ),
            "SQLite did not start contention attempt {attempt}"
        );
    }

    fn expect_busy_exhausted(
        worker: &DownloadWorker,
        observed: &Receiver<SqliteBusyObservation>,
        attempt: usize,
    ) {
        match next_busy_observation(worker, observed) {
            SqliteBusyObservation::Exhausted {
                attempt: actual,
                waited,
            } => {
                assert_eq!(actual, attempt);
                assert!(waited >= Duration::from_secs(5), "{waited:?}");
            }
            other => panic!("SQLite did not exhaust attempt {attempt}: {other:?}"),
        }
    }

    #[test]
    fn sqlite_contention_idle_retry_preserves_a_batch_and_pause_all() {
        let directory = tempfile::tempdir().unwrap();
        let (database, book, chapter) = local_fixture(directory.path(), "paused", true);
        let (worker, observed) =
            observed_runtime_worker(directory.path().to_path_buf(), database.clone());
        // This event is emitted by the runtime loop, after initialization.
        worker.pause_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 0);

        let blocker = rusqlite::Connection::open(&database).unwrap();
        blocker.busy_timeout(Duration::from_secs(5)).unwrap();
        blocker.execute_batch("BEGIN EXCLUSIVE;").unwrap();
        expect_busy_entered(&worker, &observed, 1);
        let chapters = [0, 1].map(|index| Chapter {
            id: format!("local:busy-batch-{index}"),
            remote_id: format!("busy-batch-{index}"),
            number: index as f64 + 2.0,
            ..chapter.clone()
        });
        worker.enqueue_many(&book, &chapters).unwrap();
        worker.pause_all().unwrap();
        expect_busy_exhausted(&worker, &observed, 1);
        // A new attempt under the held lock proves the first real SQLite call
        // returned BUSY and the same runtime loop continued.
        expect_busy_entered(&worker, &observed, 2);
        assert!(worker.terminal_error.lock().unwrap().is_none());
        blocker.execute_batch("ROLLBACK;").unwrap();

        assert_eq!(wait_batch_without_rows(&worker), (2, 0, 0));
        assert_eq!(wait_queue_changed(&worker), 2);
        let store = Store::open_read_only(&database).unwrap();
        assert_eq!(store.download_count().unwrap(), 3);
        for chapter in &chapters {
            assert_eq!(
                store.download(&chapter.id).unwrap().unwrap().state,
                "paused"
            );
        }
        // A later real batch is accepted by this same worker and preserves the
        // explicit pause rather than restarting either selected chapter.
        worker.enqueue_many(&book, &chapters).unwrap();
        assert_eq!(wait_batch_without_rows(&worker), (0, 2, 0));
        assert_eq!(store.page_window(&chapter.id, 0, 64).unwrap().total, 1);
        assert!(store.suspended_download_ids().unwrap().is_empty());
        assert_eq!(worker.active_count(), 0);
        assert_eq!(
            CacheManager::open(directory.path())
                .unwrap()
                .stats()
                .pinned_entries,
            0
        );
        stop(&worker);
        assert!(worker.terminal_error.lock().unwrap().is_none());
    }

    #[test]
    fn sqlite_contention_suspend_checkpoint_retries_and_retains_a_queued_wake() {
        let directory = tempfile::tempdir().unwrap();
        let (database, book, chapter) = local_fixture(directory.path(), "paused", true);
        let (worker, observed) =
            observed_runtime_worker(directory.path().to_path_buf(), database.clone());
        worker.pause_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 0);
        let blocker = rusqlite::Connection::open(&database).unwrap();
        blocker.busy_timeout(Duration::from_secs(5)).unwrap();
        blocker.execute_batch("BEGIN EXCLUSIVE;").unwrap();
        // Keep this queued row private under the real lock until the suspend
        // fence is set, so the worker cannot start this chapter.
        assert_eq!(
            blocker
                .execute(
                    "UPDATE downloads SET state='queued', \
                     document=json_set(document, '$.state', 'queued') WHERE chapter_id=?1",
                    [&chapter.id],
                )
                .unwrap(),
            1
        );
        expect_busy_entered(&worker, &observed, 1);
        worker.pause_for_suspend().unwrap();
        expect_busy_exhausted(&worker, &observed, 1);
        expect_busy_entered(&worker, &observed, 2);
        // The wake is accepted while the checkpoint transaction is still locked.
        worker.resume_owned(false).unwrap();
        expect_busy_exhausted(&worker, &observed, 2);
        expect_busy_entered(&worker, &observed, 3);
        blocker.execute_batch("COMMIT;").unwrap();

        assert!(matches!(
            worker.events.recv_timeout(Duration::from_secs(5)).unwrap(),
            DownloadEvent::Suspended
        ));
        assert!(matches!(
            worker.events.recv_timeout(Duration::from_secs(5)).unwrap(),
            DownloadEvent::Resumed {
                network_ready: false
            }
        ));
        let store = Store::open_read_only(&database).unwrap();
        assert!(!worker.suspend_ready());
        assert!(!worker.suspend.strict.load(Ordering::Acquire));
        assert!(!worker.suspend.network_ready.load(Ordering::Acquire));
        assert_eq!(
            store.suspended_download_ids().unwrap(),
            BTreeSet::from([chapter.id.clone()])
        );
        assert_eq!(
            *worker.suspend.owned.lock().unwrap(),
            BTreeSet::from([chapter.id.clone()])
        );
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "paused"
        );
        let chapters = [
            chapter.clone(),
            Chapter {
                id: "local:after-busy-suspend".into(),
                remote_id: "after-busy-suspend".into(),
                number: 2.0,
                ..chapter.clone()
            },
        ];
        worker.enqueue_many(&book, &chapters).unwrap();
        assert_eq!(wait_batch_without_rows(&worker), (1, 1, 0));
        worker.pause_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 1);
        assert!(store.suspended_download_ids().unwrap().is_empty());
        for chapter in &chapters {
            assert_eq!(
                store.download(&chapter.id).unwrap().unwrap().state,
                "paused"
            );
        }
        assert_eq!(store.page_window(&chapter.id, 0, 64).unwrap().total, 1);
        assert_eq!(worker.active_count(), 0);
        stop(&worker);
        assert!(worker.terminal_error.lock().unwrap().is_none());
    }

    #[test]
    fn sqlite_contention_idle_retry_stops_while_the_lock_is_held() {
        let directory = tempfile::tempdir().unwrap();
        let (database, _, _) = local_fixture(directory.path(), "paused", true);
        let (worker, observed) =
            observed_runtime_worker(directory.path().to_path_buf(), database.clone());
        worker.pause_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 0);
        let blocker = rusqlite::Connection::open(&database).unwrap();
        blocker.busy_timeout(Duration::from_secs(5)).unwrap();
        blocker.execute_batch("BEGIN EXCLUSIVE;").unwrap();
        expect_busy_entered(&worker, &observed, 1);
        expect_busy_exhausted(&worker, &observed, 1);
        expect_busy_entered(&worker, &observed, 2);
        worker.shutdown();
        // An in-progress SQLite call can consume its existing five-second busy
        // wait. The retry loop must then stop without needing the lock released.
        assert!(worker.wait_stopped(Duration::from_secs(8)));
        assert!(worker.terminal_error.lock().unwrap().is_none());
        assert!(worker.join_handle.lock().unwrap().is_none());
        blocker.execute_batch("ROLLBACK;").unwrap();
    }

    struct SuspendedRuntimeFixture {
        database: PathBuf,
        store: Store,
        book: Book,
        automatic: Chapter,
        explicit: Chapter,
        page: Page,
        worker: DownloadWorker,
        observed: Receiver<SqliteBusyObservation>,
    }

    fn suspended_runtime_fixture(directory: &Path) -> SuspendedRuntimeFixture {
        let (database, book, automatic) = local_fixture(directory, "queued", true);
        let store = Store::open_existing_for_write(&database).unwrap();
        let explicit = Chapter {
            id: "local:explicit-busy-pause".into(),
            remote_id: "explicit-busy-pause".into(),
            number: 2.0,
            ..automatic.clone()
        };
        let local_pages = store.pages(&automatic.id).unwrap();
        store
            .save_chapters(&book.id, &[automatic.clone(), explicit.clone()])
            .unwrap();
        store.save_pages(&explicit.id, &local_pages).unwrap();
        store
            .save_download(&DownloadJob {
                chapter_id: explicit.id.clone(),
                book_id: book.id.clone(),
                state: "paused".into(),
                total: 1,
                ..DownloadJob::default()
            })
            .unwrap();
        let cache = CacheManager::open(directory).unwrap();
        let page = cached_test_page(
            &cache,
            "https://fixture.invalid/busy-preserved.png".into(),
            &owner(&automatic.id),
        );
        store
            .save_pages(&automatic.id, std::slice::from_ref(&page))
            .unwrap();
        let (worker, observed) = observed_runtime_worker_with_options(
            directory.to_path_buf(),
            database.clone(),
            DownloadOptions {
                offline: true,
                ..DownloadOptions::default()
            },
        );
        // Complete the real checkpoint before testing any locked wake or Stop.
        // Offline only prevents dequeue/fetch; wake transactions still execute.
        worker.pause_for_suspend().unwrap();
        assert!(matches!(
            worker.events.recv_timeout(Duration::from_secs(5)).unwrap(),
            DownloadEvent::Suspended
        ));
        wait_suspend_ready(&worker);
        assert_eq!(
            store.suspended_download_ids().unwrap(),
            BTreeSet::from([automatic.id.clone()])
        );
        assert_eq!(
            store.download(&automatic.id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(
            store.download(&explicit.id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(cache.stats().pinned_entries, 1);
        SuspendedRuntimeFixture {
            database,
            store,
            book,
            automatic,
            explicit,
            page,
            worker,
            observed,
        }
    }

    fn assert_busy_fixture_cache_preserved(
        directory: &Path,
        store: &Store,
        automatic: &Chapter,
        explicit: &Chapter,
        page: &Page,
    ) {
        for chapter in [automatic, explicit] {
            assert_eq!(store.page_window(&chapter.id, 0, 64).unwrap().total, 1);
            let counts: (i64, i64) = store
                .test_connection()
                .query_row(
                    "SELECT total,mapped FROM chapter_page_stats WHERE chapter_id=?1",
                    [&chapter.id],
                    |row| Ok((row.get(0)?, row.get(1)?)),
                )
                .unwrap();
            assert_eq!(counts, (1, 1));
        }
        let cache = CacheManager::open(directory).unwrap();
        assert_eq!(cache.stats().pinned_entries, 1);
        assert_eq!(cache.lookup_raw(page).unwrap(), page.local_path.clone());
        assert!(page.local_path.as_ref().unwrap().is_file());
    }

    #[test]
    fn sqlite_contention_ready_wake_retries_ownership_and_preserves_command_order() {
        let directory = tempfile::tempdir().unwrap();
        let SuspendedRuntimeFixture {
            database,
            store,
            book,
            automatic,
            explicit,
            page,
            worker,
            observed,
        } = suspended_runtime_fixture(directory.path());
        let blocker = rusqlite::Connection::open(&database).unwrap();
        blocker.busy_timeout(Duration::from_secs(5)).unwrap();
        blocker.execute_batch("BEGIN EXCLUSIVE;").unwrap();
        worker.resume_owned(true).unwrap();
        expect_busy_entered(&worker, &observed, 1);
        let chapters = [0, 1].map(|index| Chapter {
            id: format!("local:busy-wake-batch-{index}"),
            remote_id: format!("busy-wake-batch-{index}"),
            number: index as f64 + 3.0,
            ..automatic.clone()
        });
        worker.enqueue_many(&book, &chapters).unwrap();
        worker.pause(&chapters[0].id).unwrap();
        expect_busy_exhausted(&worker, &observed, 1);
        expect_busy_entered(&worker, &observed, 2);
        blocker.execute_batch("ROLLBACK;").unwrap();

        match worker.events.recv_timeout(Duration::from_secs(5)).unwrap() {
            DownloadEvent::StateChanged(job) => {
                assert_eq!(job.chapter_id, automatic.id);
                assert_eq!(job.state, "queued");
            }
            other => panic!("The automatic wake did not publish its queued job: {other:?}"),
        }
        assert!(matches!(
            worker.events.recv_timeout(Duration::from_secs(5)).unwrap(),
            DownloadEvent::Resumed {
                network_ready: true
            }
        ));
        assert_eq!(wait_batch_without_rows(&worker), (2, 0, 0));
        assert_eq!(wait_for(&worker, "paused").chapter_id, chapters[0].id);
        assert_eq!(
            store.download(&automatic.id).unwrap().unwrap().state,
            "queued"
        );
        assert_eq!(
            store.download(&explicit.id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(
            store.download(&chapters[0].id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(
            store.download(&chapters[1].id).unwrap().unwrap().state,
            "queued"
        );
        assert_eq!(store.download_count().unwrap(), 4);
        assert!(store.suspended_download_ids().unwrap().is_empty());
        assert!(worker.suspend.owned.lock().unwrap().is_empty());
        assert!(!worker.suspend.strict.load(Ordering::Acquire));
        assert!(worker.suspend.network_ready.load(Ordering::Acquire));
        worker
            .enqueue_many(&book, &[automatic.clone(), explicit.clone()])
            .unwrap();
        assert_eq!(wait_batch_without_rows(&worker), (1, 1, 0));
        assert_busy_fixture_cache_preserved(directory.path(), &store, &automatic, &explicit, &page);
        assert_eq!(worker.active_count(), 0);
        stop(&worker);
        assert!(worker.terminal_error.lock().unwrap().is_none());
    }

    #[test]
    fn sqlite_contention_old_wake_cannot_release_a_new_suspend_epoch() {
        let directory = tempfile::tempdir().unwrap();
        let SuspendedRuntimeFixture {
            database,
            store,
            automatic,
            explicit,
            page,
            worker,
            observed,
            ..
        } = suspended_runtime_fixture(directory.path());
        let old_epoch = worker.suspend.epoch.load(Ordering::Acquire);
        let blocker = rusqlite::Connection::open(&database).unwrap();
        blocker.busy_timeout(Duration::from_secs(5)).unwrap();
        blocker.execute_batch("BEGIN EXCLUSIVE;").unwrap();
        worker.resume_owned(true).unwrap();
        expect_busy_entered(&worker, &observed, 1);
        expect_busy_exhausted(&worker, &observed, 1);
        expect_busy_entered(&worker, &observed, 2);

        // Move the worker to a caller thread so a transition mutex held across
        // SQLite retry cannot hang this test while the main thread owns the lock.
        let (paused_sender, paused) = mpsc::channel();
        let pauser = thread::spawn(move || {
            let result = worker.pause_for_suspend();
            assert!(paused_sender.send((worker, result)).is_ok());
        });
        let (worker, paused_result) = match paused.recv_timeout(Duration::from_secs(1)) {
            Ok(result) => result,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                blocker.execute_batch("ROLLBACK;").unwrap();
                let (worker, result) = paused.recv_timeout(Duration::from_secs(10)).unwrap();
                pauser.join().unwrap();
                result.unwrap();
                stop(&worker);
                panic!("The transition mutex stayed locked across the SQLite retry");
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                blocker.execute_batch("ROLLBACK;").unwrap();
                pauser.join().unwrap();
                panic!("The new suspend caller stopped");
            }
        };
        pauser.join().unwrap();
        paused_result.unwrap();
        let new_epoch = worker.suspend.epoch.load(Ordering::Acquire);
        assert_eq!(new_epoch, old_epoch.wrapping_add(1));
        blocker.execute_batch("ROLLBACK;").unwrap();

        assert!(matches!(
            worker.events.recv_timeout(Duration::from_secs(5)).unwrap(),
            DownloadEvent::Suspended
        ));
        wait_suspend_ready(&worker);
        assert_eq!(
            worker.suspend.acknowledged.load(Ordering::Acquire),
            new_epoch
        );
        assert!(worker.suspend.strict.load(Ordering::Acquire));
        assert!(!worker.suspend.network_ready.load(Ordering::Acquire));
        assert_eq!(
            store.suspended_download_ids().unwrap(),
            BTreeSet::from([automatic.id.clone()])
        );
        assert_eq!(
            *worker.suspend.owned.lock().unwrap(),
            BTreeSet::from([automatic.id.clone()])
        );
        assert!(
            worker
                .controls
                .lock()
                .unwrap()
                .get(&automatic.id)
                .is_some_and(|control| *control == Control::Paused)
        );
        assert_eq!(
            store.download(&automatic.id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(
            store.download(&explicit.id).unwrap().unwrap().state,
            "paused"
        );
        assert!(!worker.drain_events().iter().any(|event| matches!(
            event,
            DownloadEvent::Resumed {
                network_ready: true
            }
        )));
        assert_busy_fixture_cache_preserved(directory.path(), &store, &automatic, &explicit, &page);
        stop(&worker);
        assert!(worker.terminal_error.lock().unwrap().is_none());
    }

    #[test]
    fn sqlite_contention_checkpoint_stop_does_not_process_a_deferred_batch() {
        let directory = tempfile::tempdir().unwrap();
        let SuspendedRuntimeFixture {
            database,
            store,
            book,
            automatic,
            explicit,
            page,
            worker,
            observed,
        } = suspended_runtime_fixture(directory.path());
        let acknowledged = worker.suspend.acknowledged.load(Ordering::Acquire);
        let blocker = rusqlite::Connection::open(&database).unwrap();
        blocker.busy_timeout(Duration::from_secs(5)).unwrap();
        blocker.execute_batch("BEGIN EXCLUSIVE;").unwrap();
        worker.pause_for_suspend().unwrap();
        expect_busy_entered(&worker, &observed, 1);
        let chapters = [0, 1].map(|index| Chapter {
            id: format!("local:busy-stop-batch-{index}"),
            remote_id: format!("busy-stop-batch-{index}"),
            number: index as f64 + 3.0,
            ..automatic.clone()
        });
        worker.enqueue_many(&book, &chapters).unwrap();
        expect_busy_exhausted(&worker, &observed, 1);
        // The batch has passed through the retry wait before attempt two starts.
        expect_busy_entered(&worker, &observed, 2);
        let started = Instant::now();
        worker.shutdown();
        let stopped = worker.wait_stopped(Duration::from_secs(8));
        let elapsed = started.elapsed();
        if !stopped {
            // Release only for failed-test cleanup; a passing run stops while
            // the real EXCLUSIVE lock is still held.
            blocker.execute_batch("ROLLBACK;").unwrap();
            assert!(worker.wait_stopped(Duration::from_secs(8)));
        }
        assert!(stopped, "Stop continued into deferred database work");
        assert!(
            elapsed <= Duration::from_secs(5) + COMMAND_INTERVAL + Duration::from_secs(1),
            "Stop exceeded one SQLite busy wait and a short scheduling margin: {elapsed:?}"
        );
        assert!(worker.terminal_error.lock().unwrap().is_none());
        assert!(worker.join_handle.lock().unwrap().is_none());
        assert_eq!(
            worker.suspend.acknowledged.load(Ordering::Acquire),
            acknowledged
        );
        assert!(!worker.drain_events().iter().any(|event| {
            matches!(
                event,
                DownloadEvent::BatchAccepted { .. } | DownloadEvent::Error(_)
            )
        }));
        assert!(!observed.try_iter().any(|event| {
            matches!(event, SqliteBusyObservation::Entered(attempt) if attempt > 2)
        }));
        blocker.execute_batch("ROLLBACK;").unwrap();

        assert_eq!(store.download_count().unwrap(), 2);
        assert_eq!(
            store.download(&automatic.id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(
            store.download(&explicit.id).unwrap().unwrap().state,
            "paused"
        );
        for chapter in &chapters {
            assert!(store.chapter(&chapter.id).unwrap().is_none());
            assert!(store.download(&chapter.id).unwrap().is_none());
        }
        assert_eq!(
            store.suspended_download_ids().unwrap(),
            BTreeSet::from([automatic.id.clone()])
        );
        assert_eq!(
            *worker.suspend.owned.lock().unwrap(),
            BTreeSet::from([automatic.id.clone()])
        );
        assert_busy_fixture_cache_preserved(directory.path(), &store, &automatic, &explicit, &page);
    }

    use crate::domain::SourceConfig;
    use std::{
        collections::BTreeMap,
        fs,
        io::{BufRead, BufReader, Cursor, Write},
        net::{TcpListener, TcpStream},
    };

    #[derive(Clone)]
    struct HttpRecord {
        path: String,
        headers: String,
    }

    struct HttpFixture {
        base_url: String,
        records: Arc<Mutex<Vec<HttpRecord>>>,
        maximum_active: Arc<std::sync::atomic::AtomicUsize>,
        stopping: Arc<AtomicBool>,
        listener: Option<JoinHandle<()>>,
        handlers: Arc<Mutex<Vec<JoinHandle<()>>>>,
        image_gate: Arc<(Mutex<bool>, Condvar)>,
        failures: Arc<AtomicUsize>,
    }

    impl HttpFixture {
        fn new(page_count: usize, image_delay: Duration) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.set_nonblocking(true).unwrap();
            let base_url = format!("http://{}", listener.local_addr().unwrap());
            let records = Arc::new(Mutex::new(Vec::new()));
            let active = Arc::new(std::sync::atomic::AtomicUsize::new(0));
            let maximum_active = Arc::new(std::sync::atomic::AtomicUsize::new(0));
            let stopping = Arc::new(AtomicBool::new(false));
            let handlers = Arc::new(Mutex::new(Vec::new()));
            let image_gate = Arc::new((Mutex::new(true), Condvar::new()));
            let failures = Arc::new(AtomicUsize::new(0));
            let thread_records = records.clone();
            let thread_active = active.clone();
            let thread_maximum = maximum_active.clone();
            let thread_stopping = stopping.clone();
            let thread_handlers = handlers.clone();
            let thread_gate = image_gate.clone();
            let thread_failures = failures.clone();
            let mut image = Cursor::new(Vec::new());
            image::GrayImage::from_pixel(8, 12, image::Luma([85]))
                .write_to(&mut image, image::ImageFormat::Png)
                .unwrap();
            let image = Arc::new(image.into_inner());
            let listener_thread = thread::spawn(move || {
                while !thread_stopping.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((stream, _)) => {
                            let records = thread_records.clone();
                            let active = thread_active.clone();
                            let maximum = thread_maximum.clone();
                            let image = image.clone();
                            let image_gate = thread_gate.clone();
                            let failures = thread_failures.clone();
                            let handle = thread::spawn(move || {
                                serve(
                                    stream,
                                    page_count,
                                    image_delay,
                                    &image,
                                    &records,
                                    &active,
                                    &maximum,
                                    &image_gate,
                                    &failures,
                                )
                            });
                            thread_handlers.lock().unwrap().push(handle);
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(2))
                        }
                        Err(error) => panic!("HTTP fixture failed: {error}"),
                    }
                }
            });
            Self {
                base_url,
                records,
                maximum_active,
                stopping,
                listener: Some(listener_thread),
                handlers,
                image_gate,
                failures,
            }
        }

        fn records(&self) -> Vec<HttpRecord> {
            self.records.lock().unwrap().clone()
        }
        fn image_count(&self) -> usize {
            self.records()
                .iter()
                .filter(|record| !record.path.ends_with("/pages"))
                .count()
        }
        fn manifest_count(&self) -> usize {
            self.records()
                .iter()
                .filter(|record| record.path.ends_with("/pages"))
                .count()
        }
        fn hold_images(&self) {
            *self.image_gate.0.lock().unwrap() = false;
        }
        fn release_images(&self) {
            *self.image_gate.0.lock().unwrap() = true;
            self.image_gate.1.notify_all();
        }
        fn fail_next_images(&self, count: usize) {
            self.failures.store(count, Ordering::Release);
        }
        fn wait_for_images(&self, count: usize) {
            let deadline = Instant::now() + Duration::from_secs(10);
            while Instant::now() < deadline {
                if self.image_count() >= count {
                    return;
                }
                thread::sleep(Duration::from_millis(5));
            }
            panic!("Timed out waiting for {count} image requests");
        }
    }

    impl Drop for HttpFixture {
        fn drop(&mut self) {
            self.release_images();
            self.stopping.store(true, Ordering::Release);
            if let Some(listener) = self.listener.take() {
                listener.join().unwrap();
            }
            let handlers = std::mem::take(&mut *self.handlers.lock().unwrap());
            for handle in handlers {
                handle.join().unwrap();
            }
        }
    }

    fn serve(
        mut stream: TcpStream,
        page_count: usize,
        delay: Duration,
        image: &[u8],
        records: &Mutex<Vec<HttpRecord>>,
        active: &std::sync::atomic::AtomicUsize,
        maximum: &std::sync::atomic::AtomicUsize,
        image_gate: &(Mutex<bool>, Condvar),
        failures: &AtomicUsize,
    ) {
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut reader = BufReader::new(stream.try_clone().unwrap());
        let mut line = String::new();
        reader.read_line(&mut line).unwrap();
        let path = line.split_whitespace().nth(1).unwrap().to_owned();
        let mut headers = String::new();
        loop {
            let mut line = String::new();
            if reader.read_line(&mut line).unwrap() == 0 || line == "\r\n" {
                break;
            }
            headers.push_str(&line);
        }
        let is_manifest = path.ends_with("/pages");
        if !is_manifest {
            let count = active.fetch_add(1, Ordering::AcqRel) + 1;
            maximum.fetch_max(count, Ordering::AcqRel);
        }
        records.lock().unwrap().push(HttpRecord { path, headers });
        let metadata;
        let mut failed = false;
        let (content_type, body): (&str, &[u8]) = if is_manifest {
            metadata = serde_json::to_vec(
                &(1..=page_count)
                    .map(|number| serde_json::json!({"number":number,"mediaType":"image/png"}))
                    .collect::<Vec<_>>(),
            )
            .unwrap();
            ("application/json", &metadata)
        } else {
            let mut allowed = image_gate.0.lock().unwrap();
            while !*allowed {
                allowed = image_gate.1.wait(allowed).unwrap();
            }
            drop(allowed);
            thread::sleep(delay);
            failed = failures
                .fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
                    count.checked_sub(1)
                })
                .is_ok();
            if failed {
                ("text/plain", b"fixture network unavailable")
            } else {
                ("image/png", image)
            }
        };
        let status = if failed {
            "503 Service Unavailable"
        } else {
            "200 OK"
        };
        if write!(stream, "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).is_ok() { let _ = stream.write_all(body); }
        if !is_manifest {
            active.fetch_sub(1, Ordering::AcqRel);
        }
    }

    fn network_fixture(
        directory: &Path,
        server: &HttpFixture,
        chapter_count: usize,
        page_count: u32,
    ) -> (PathBuf, Store, Book, Vec<Chapter>) {
        let database = directory.join("network.sqlite");
        let store = Store::open(&database).unwrap();
        store
            .save_source(&SourceConfig {
                id: "komga".into(),
                kind: "komga".into(),
                name: "Fixture server".into(),
                base_url: server.base_url.clone(),
                token: "fixture-token".into(),
                enabled: true,
                ..SourceConfig::default()
            })
            .unwrap();
        let book = Book {
            id: "komga:series".into(),
            source_id: "komga".into(),
            remote_id: "series".into(),
            title: "Network comic".into(),
            ..Book::default()
        };
        let chapters = (0..chapter_count)
            .map(|index| Chapter {
                id: format!("komga:chapter-{index}"),
                book_id: book.id.clone(),
                source_id: book.source_id.clone(),
                remote_id: format!("chapter-{index}"),
                number: index as f64 + 1.0,
                page_count,
                ..Chapter::default()
            })
            .collect::<Vec<_>>();
        store.upsert_book(&book).unwrap();
        store.save_chapters(&book.id, &chapters).unwrap();
        (database, store, book, chapters)
    }

    fn manual_pages(server: &HttpFixture, chapter: &Chapter, count: u32) -> Vec<Page> {
        (1..=count)
            .map(|number| Page {
                url: format!(
                    "{}/api/v1/books/{}/pages/{number}",
                    server.base_url, chapter.remote_id
                ),
                headers: BTreeMap::from([("X-API-Key".into(), "fixture-token".into())]),
                local_path: None,
            })
            .collect()
    }

    fn local_fixture(directory: &Path, state: &str, valid_image: bool) -> (PathBuf, Book, Chapter) {
        let database = directory.join("state.sqlite");
        let store = Store::open(&database).unwrap();
        let book = Book {
            id: "local:book".into(),
            source_id: "local".into(),
            remote_id: "book".into(),
            title: "Local comic".into(),
            in_library: true,
            ..Book::default()
        };
        let chapter = Chapter {
            id: "local:chapter".into(),
            book_id: book.id.clone(),
            source_id: "local".into(),
            remote_id: "chapter".into(),
            number: 1.0,
            page_count: 1,
            ..Chapter::default()
        };
        store.upsert_book(&book).unwrap();
        store
            .save_chapters(&book.id, std::slice::from_ref(&chapter))
            .unwrap();
        let page_path = directory.join("page.png");
        if valid_image {
            image::GrayImage::from_pixel(2, 3, image::Luma([17]))
                .save(&page_path)
                .unwrap();
        } else {
            fs::write(&page_path, b"broken image").unwrap();
        }
        store
            .save_pages(
                &chapter.id,
                &[Page {
                    local_path: Some(page_path),
                    ..Page::default()
                }],
            )
            .unwrap();
        store
            .save_download(&DownloadJob {
                chapter_id: chapter.id.clone(),
                book_id: book.id.clone(),
                state: state.into(),
                total: 1,
                completed: 0,
                error: String::new(),
            })
            .unwrap();
        (database, book, chapter)
    }

    fn wait_for(worker: &DownloadWorker, state: &str) -> DownloadJob {
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline {
            match worker.events.recv_timeout(Duration::from_millis(200)) {
                Ok(
                    DownloadEvent::Progress(job)
                    | DownloadEvent::Completed(job)
                    | DownloadEvent::Failed(job)
                    | DownloadEvent::StateChanged(job),
                ) if job.state == state => return job,
                Ok(DownloadEvent::Error(error)) => panic!("Worker failed: {error}"),
                _ => {}
            }
        }
        panic!("Timed out waiting for download state {state}");
    }

    fn stop(worker: &DownloadWorker) {
        worker.shutdown();
        assert!(worker.wait_stopped(Duration::from_secs(3)));
    }

    fn wait_batch(worker: &DownloadWorker) -> (usize, usize, usize) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            match worker.events.recv_timeout(Duration::from_millis(100)) {
                Ok(DownloadEvent::BatchAccepted {
                    pending,
                    paused,
                    completed,
                }) => return (pending, paused, completed),
                Ok(DownloadEvent::Error(error)) => panic!("Worker failed: {error}"),
                _ => {}
            }
        }
        panic!("Timed out waiting for an accepted download batch");
    }

    fn wait_batch_without_rows(worker: &DownloadWorker) -> (usize, usize, usize) {
        match worker.events.recv_timeout(Duration::from_secs(5)).unwrap() {
            DownloadEvent::BatchAccepted {
                pending,
                paused,
                completed,
            } => (pending, paused, completed),
            other => {
                panic!("A bulk enqueue emitted an individual or unexpected payload: {other:?}")
            }
        }
    }

    fn wait_queue_changed(worker: &DownloadWorker) -> usize {
        match worker.events.recv_timeout(Duration::from_secs(5)).unwrap() {
            DownloadEvent::QueueChanged { affected } => affected,
            other => {
                panic!("A bulk control emitted an individual or unexpected payload: {other:?}")
            }
        }
    }

    fn metadata_until_batch(worker: &DownloadWorker) -> (BTreeSet<String>, (usize, usize, usize)) {
        let mut changed = BTreeSet::new();
        loop {
            match worker.events.recv_timeout(Duration::from_secs(5)).unwrap() {
                DownloadEvent::DownloadedMetadataChanged { chapter_id } => assert!(
                    changed.insert(chapter_id),
                    "A chapter was invalidated more than once for the same command"
                ),
                DownloadEvent::BatchAccepted {
                    pending,
                    paused,
                    completed,
                } => return (changed, (pending, paused, completed)),
                DownloadEvent::StateChanged(_) => {}
                other => panic!("Unexpected enqueue event: {other:?}"),
            }
        }
    }

    fn metadata_until_error(worker: &DownloadWorker) -> (BTreeSet<String>, String) {
        let mut changed = BTreeSet::new();
        loop {
            match worker.events.recv_timeout(Duration::from_secs(5)).unwrap() {
                DownloadEvent::DownloadedMetadataChanged { chapter_id } => assert!(
                    changed.insert(chapter_id),
                    "A partial failure invalidated a chapter twice"
                ),
                DownloadEvent::Error(error) => return (changed, error),
                other => panic!("A failed enqueue unexpectedly published: {other:?}"),
            }
        }
    }

    fn wait_suspend_ready(worker: &DownloadWorker) {
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if worker.suspend_ready() {
                return;
            }
            thread::sleep(Duration::from_millis(5));
        }
        panic!("The queue did not acknowledge its suspend checkpoint");
    }

    fn wait_active(worker: &DownloadWorker, expected: usize) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if worker.active_count() == expected {
                return;
            }
            thread::sleep(Duration::from_millis(5));
        }
        panic!("The download active count did not become {expected}");
    }

    #[test]
    fn sleep_gate_authorizes_its_existing_epoch_only_after_explicit_checkpoint() {
        let directory = tempfile::tempdir().unwrap();
        let (database, _, chapter) = local_fixture(directory.path(), "queued", true);
        let (worker, observed) = observed_runtime_worker_with_options(
            directory.path().to_path_buf(),
            database.clone(),
            DownloadOptions {
                offline: true,
                ..DownloadOptions::default()
            },
        );
        let blocker = rusqlite::Connection::open(&database).unwrap();
        blocker.execute_batch("BEGIN EXCLUSIVE;").unwrap();
        worker.begin_suspend_gate().unwrap();
        let epoch = worker.suspend.epoch.load(Ordering::Acquire);
        worker.begin_suspend_gate().unwrap();
        assert_eq!(worker.suspend.epoch.load(Ordering::Acquire), epoch);
        assert!(!worker.suspend_ready());
        assert!(!worker.suspend.checkpoint_allowed.load(Ordering::Acquire));
        assert!(observed.recv_timeout(Duration::from_millis(100)).is_err());
        assert!(worker.events.try_recv().is_err());

        worker.pause_for_suspend().unwrap();
        assert_eq!(worker.suspend.epoch.load(Ordering::Acquire), epoch);
        expect_busy_entered(&worker, &observed, 1);
        blocker.execute_batch("ROLLBACK;").unwrap();
        assert!(matches!(
            worker.events.recv_timeout(Duration::from_secs(5)).unwrap(),
            DownloadEvent::Suspended
        ));
        wait_suspend_ready(&worker);
        let store = Store::open_read_only(&database).unwrap();
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(
            store.suspended_download_ids().unwrap(),
            BTreeSet::from([chapter.id])
        );
        stop(&worker);
    }

    #[test]
    fn sleep_gate_accepts_a_wake_without_checkpointing_a_locked_database() {
        let directory = tempfile::tempdir().unwrap();
        let (database, _, _) = local_fixture(directory.path(), "paused", true);
        let (worker, observed) = observed_runtime_worker_with_options(
            directory.path().to_path_buf(),
            database.clone(),
            DownloadOptions {
                offline: true,
                ..DownloadOptions::default()
            },
        );
        let blocker = rusqlite::Connection::open(&database).unwrap();
        blocker.execute_batch("BEGIN EXCLUSIVE;").unwrap();
        worker.begin_suspend_gate().unwrap();
        worker.resume_owned(false).unwrap();
        assert!(matches!(
            worker.events.recv_timeout(Duration::from_secs(2)).unwrap(),
            DownloadEvent::Resumed {
                network_ready: false
            }
        ));
        assert!(!worker.suspend_ready());
        assert!(!worker.suspend.strict.load(Ordering::Acquire));
        assert!(observed.try_recv().is_err());
        stop(&worker);
        blocker.execute_batch("ROLLBACK;").unwrap();
    }

    #[test]
    fn sleep_gate_defers_ordinary_commands_and_stops_without_a_database_checkpoint() {
        let directory = tempfile::tempdir().unwrap();
        let (database, book, chapter) = local_fixture(directory.path(), "paused", true);
        let (worker, observed) = observed_runtime_worker_with_options(
            directory.path().to_path_buf(),
            database.clone(),
            DownloadOptions {
                offline: true,
                ..DownloadOptions::default()
            },
        );
        let queued = Chapter {
            id: "local:gate-deferred".into(),
            remote_id: "gate-deferred".into(),
            ..chapter
        };
        let blocker = rusqlite::Connection::open(&database).unwrap();
        blocker.execute_batch("BEGIN EXCLUSIVE;").unwrap();
        worker.begin_suspend_gate().unwrap();
        worker.enqueue(&book, &queued).unwrap();
        worker.pause_all().unwrap();
        assert!(observed.recv_timeout(Duration::from_millis(100)).is_err());
        assert!(worker.events.try_recv().is_err());
        worker.shutdown();
        assert!(worker.wait_stopped(Duration::from_secs(2)));
        assert!(worker.terminal_error.lock().unwrap().is_none());
        assert!(observed.try_recv().is_err());
        assert!(worker.events.try_recv().is_err());
        blocker.execute_batch("ROLLBACK;").unwrap();
        let store = Store::open_read_only(&database).unwrap();
        assert!(store.download(&queued.id).unwrap().is_none());
        assert!(store.suspended_download_ids().unwrap().is_empty());
    }

    #[test]
    fn sleep_checkpoint_does_not_wait_for_http_and_defers_user_commands_fifo() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(3, Duration::ZERO);
        server.hold_images();
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 3);
        let chapter = &chapters[0];
        store
            .save_pages(&chapter.id, &manual_pages(&server, chapter, 3))
            .unwrap();
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue(&book, chapter).unwrap();
        server.wait_for_images(2);
        worker.pause_for_suspend().unwrap();
        wait_suspend_ready(&worker);
        assert!(worker.active_count() > 0);
        assert!(!worker.is_idle());
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "paused"
        );
        assert!(
            store
                .suspended_download_ids()
                .unwrap()
                .contains(&chapter.id)
        );
        worker.pause(&chapter.id).unwrap();
        worker.resume(&chapter.id).unwrap();
        worker.cancel(&chapter.id).unwrap();
        server.release_images();
        wait_active(&worker, 0);
        assert!(worker.suspend_ready());
        assert!(worker.is_idle());
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "paused"
        );
        assert!(
            store
                .pages(&chapter.id)
                .unwrap()
                .iter()
                .all(|page| page.local_path.is_none())
        );
        worker.resume_owned(true).unwrap();
        wait_for(&worker, "cancelled");
        stop(&worker);
        assert_eq!(server.image_count(), 2);
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "cancelled"
        );
        assert!(!store.has_suspended_downloads().unwrap());
        assert_eq!(
            CacheManager::open(directory.path())
                .unwrap()
                .stats()
                .pinned_entries,
            0
        );
    }

    #[test]
    fn sleep_start_suspended_and_wifi_wake_restore_only_automatic_pause_ownership() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 3, 1);
        store.enqueue_download_batch(&book, &chapters).unwrap();
        store
            .set_user_download_state(&chapters[1].id, "paused")
            .unwrap();
        let worker = DownloadWorker::start_suspended(
            directory.path().to_path_buf(),
            database,
            DownloadOptions::default(),
        )
        .unwrap();
        wait_suspend_ready(&worker);
        assert_eq!(server.records().len(), 0);
        assert_eq!(worker.active_count(), 0);
        assert!(worker.is_idle());
        assert_eq!(
            store.suspended_download_ids().unwrap(),
            BTreeSet::from([chapters[0].id.clone(), chapters[2].id.clone()])
        );
        worker.pause(&chapters[2].id).unwrap();
        worker.resume_owned(false).unwrap();
        assert_eq!(wait_for(&worker, "paused").chapter_id, chapters[2].id);
        assert!(!worker.suspend_ready());
        assert_eq!(server.records().len(), 0);
        assert_eq!(
            store.suspended_download_ids().unwrap(),
            BTreeSet::from([chapters[0].id.clone()])
        );
        worker.resume_owned(true).unwrap();
        assert_eq!(wait_for(&worker, "completed").chapter_id, chapters[0].id);
        stop(&worker);
        assert_eq!(
            store.download(&chapters[1].id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(
            store.download(&chapters[2].id).unwrap().unwrap().state,
            "paused"
        );
        assert_eq!(server.image_count(), 1);
        assert!(
            server
                .records()
                .iter()
                .all(|record| record.path.contains("chapter-0"))
        );
    }

    #[test]
    fn sleep_old_http_failures_retry_with_a_current_manager_without_marking_failed() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(2, Duration::ZERO);
        server.hold_images();
        server.fail_next_images(2);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 2);
        let chapter = &chapters[0];
        store
            .save_pages(&chapter.id, &manual_pages(&server, chapter, 2))
            .unwrap();
        let network = crate::sources::NetworkControl::open(directory.path()).unwrap();
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue(&book, chapter).unwrap();
        server.wait_for_images(2);
        worker.begin_suspend_gate().unwrap();
        let gate_epoch = network.begin_suspend_gate();
        assert!(!worker.suspend_ready());
        worker.pause_for_suspend().unwrap();
        assert_eq!(network.suspend(), gate_epoch);
        wait_suspend_ready(&worker);
        worker.resume_owned(false).unwrap();
        network.resume();
        worker.resume_owned(true).unwrap();
        server.release_images();
        let deadline = Instant::now() + Duration::from_secs(20);
        let mut completed = false;
        while Instant::now() < deadline {
            match worker.events.recv_timeout(Duration::from_millis(200)) {
                Ok(DownloadEvent::Completed(job)) => {
                    assert_eq!(job.chapter_id, chapter.id);
                    completed = true;
                    break;
                }
                Ok(DownloadEvent::Failed(job)) => {
                    panic!("A sleep interruption became a failed job: {}", job.error)
                }
                Ok(DownloadEvent::Error(error)) => panic!("The sleep retry failed: {error}"),
                _ => {}
            }
        }
        assert!(completed, "The old epoch was not retried after wake");
        stop(&worker);
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "completed"
        );
        assert_eq!(server.image_count(), 4);
        assert_eq!(
            CacheManager::open(directory.path())
                .unwrap()
                .stats()
                .pinned_entries,
            2
        );
    }

    #[test]
    fn sleep_stale_queued_wake_never_releases_a_new_suspend_epoch() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 1);
        store.enqueue_download_batch(&book, &chapters).unwrap();
        let worker = DownloadWorker::start_suspended(
            directory.path().to_path_buf(),
            database,
            DownloadOptions {
                offline: true,
                page_concurrency: 2,
            },
        )
        .unwrap();
        wait_suspend_ready(&worker);
        for _ in 0..4 {
            worker.resume_owned(true).unwrap();
            worker.pause_for_suspend().unwrap();
            wait_suspend_ready(&worker);
            assert!(!worker.suspend.network_ready.load(Ordering::Acquire));
            assert_eq!(
                store.download(&chapters[0].id).unwrap().unwrap().state,
                "paused"
            );
        }
        stop(&worker);
        assert_eq!(server.records().len(), 0);
    }

    fn wait_removed(worker: &DownloadWorker, chapter_id: &str) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            match worker.events.recv_timeout(Duration::from_millis(100)) {
                Ok(DownloadEvent::Removed {
                    chapter_id: removed,
                }) if removed == chapter_id => return,
                Ok(DownloadEvent::Error(error)) => panic!("Worker failed: {error}"),
                _ => {}
            }
        }
        panic!("Timed out waiting for removal of {chapter_id}");
    }

    fn wait_book_removed(
        worker: &DownloadWorker,
        expected: &str,
        active: bool,
    ) -> (usize, usize, usize, usize, bool, Option<String>) {
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline {
            match worker.events.recv_timeout(Duration::from_millis(100)) {
                Ok(DownloadEvent::BookRemovalFinished {
                    book_id,
                    removed,
                    cancelled,
                    failed,
                    retained_local,
                    usage_changed,
                    error,
                }) => {
                    assert_eq!(book_id, expected);
                    return (
                        removed,
                        cancelled,
                        failed,
                        retained_local,
                        usage_changed,
                        error,
                    );
                }
                Ok(
                    DownloadEvent::Progress(_)
                    | DownloadEvent::StateChanged(_)
                    | DownloadEvent::BatchAccepted { .. }
                    | DownloadEvent::Suspended
                    | DownloadEvent::Resumed { .. },
                ) if active => {}
                Ok(other) => {
                    panic!("A book cleanup emitted an individual or unexpected event: {other:?}")
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(error) => panic!("Book cleanup stopped: {error}"),
            }
        }
        panic!("Timed out waiting for book cleanup");
    }

    #[test]
    fn book_removal_cleans_all_managed_states_and_legacy_flags_preserving_shared_owners() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 5, 2);
        let cache = CacheManager::open(directory.path()).unwrap();
        let mut cached = Vec::new();
        for (index, chapter) in chapters.iter().enumerate() {
            let page = cached_test_page(
                &cache,
                manual_pages(&server, chapter, 1)[0].url.clone(),
                &owner(&chapter.id),
            );
            store
                .save_pages(&chapter.id, std::slice::from_ref(&page))
                .unwrap();
            if index == 0 {
                completed_test_job(&store, chapter, std::slice::from_ref(&page));
            } else if index == 4 {
                store.set_chapter_downloaded(&chapter.id, true).unwrap();
            } else {
                store
                    .save_download(&DownloadJob {
                        chapter_id: chapter.id.clone(),
                        book_id: book.id.clone(),
                        state: ["", "queued", "failed", "cancelled"][index].into(),
                        total: 1,
                        completed: 1,
                        error: String::new(),
                    })
                    .unwrap();
            }
            cached.push(page);
        }
        let other_book = Book {
            id: "other-book".into(),
            remote_id: "other-series".into(),
            ..book.clone()
        };
        store.upsert_book(&other_book).unwrap();
        let other = Chapter {
            id: "other-chapter".into(),
            remote_id: "other-chapter".into(),
            book_id: other_book.id.clone(),
            ..chapters[0].clone()
        };
        store
            .save_chapters(&other_book.id, std::slice::from_ref(&other))
            .unwrap();
        cache.pin_raw(&cached[0], &owner(&other.id)).unwrap();
        completed_test_job(&store, &other, std::slice::from_ref(&cached[0]));
        assert_eq!(store.book_download_count(&book.id).unwrap(), 5);
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        worker.remove_book_downloads(&book.id).unwrap();
        assert_eq!(
            wait_book_removed(&worker, &book.id, false),
            (2, 3, 0, 0, true, None)
        );
        assert_eq!(store.book_download_count(&book.id).unwrap(), 0);
        for chapter in &chapters {
            assert!(store.download(&chapter.id).unwrap().is_none());
            assert!(!store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        }
        assert!(store.chapter(&other.id).unwrap().unwrap().downloaded);
        assert_eq!(
            store.download(&other.id).unwrap().unwrap().state,
            "completed"
        );
        assert!(cached[0].local_path.as_ref().unwrap().is_file());
        assert_eq!(cache.stats().pinned_entries, 1);
        assert!(
            worker
                .events
                .recv_timeout(Duration::from_millis(100))
                .is_err()
        );
        stop(&worker);
        assert_eq!(server.records().len(), 0);
    }

    #[test]
    fn book_removal_preserves_local_imports_and_has_an_idempotent_noop_result() {
        let directory = tempfile::tempdir().unwrap();
        let (database, book, chapter) = local_fixture(directory.path(), "paused", true);
        let store = Store::open(&database).unwrap();
        let mut job = store.download(&chapter.id).unwrap().unwrap();
        job.state = "completed".into();
        job.completed = job.total;
        store.save_download(&job).unwrap();
        let page = store.pages(&chapter.id).unwrap()[0]
            .local_path
            .clone()
            .unwrap();
        let bytes = fs::read(&page).unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        worker.remove_book_downloads(&book.id).unwrap();
        assert_eq!(
            wait_book_removed(&worker, &book.id, false),
            (0, 0, 0, 1, false, None)
        );
        assert_eq!(store.book_download_count(&book.id).unwrap(), 0);
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        assert_eq!(fs::read(&page).unwrap(), bytes);
        worker.remove_book_downloads(&book.id).unwrap();
        assert_eq!(
            wait_book_removed(&worker, &book.id, false),
            (0, 0, 0, 0, false, None)
        );
        assert_eq!(fs::read(page).unwrap(), bytes);
        stop(&worker);
    }

    #[test]
    fn book_removal_continues_after_primary_failures_and_restores_the_original_raw_pin_key() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 3, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let failed = cached_test_page(
            &cache,
            manual_pages(&server, &chapters[0], 1)[0].url.clone(),
            &owner(&chapters[0].id),
        );
        let success = cached_test_page(
            &cache,
            manual_pages(&server, &chapters[1], 1)[0].url.clone(),
            &owner(&chapters[1].id),
        );
        completed_test_job(&store, &chapters[0], std::slice::from_ref(&failed));
        completed_test_job(&store, &chapters[1], &[success]);
        let external = directory.path().join("external-original.png");
        image::GrayImage::from_pixel(8, 12, image::Luma([17]))
            .save(&external)
            .unwrap();
        let original = fs::read(&external).unwrap();
        store
            .save_pages(
                &chapters[2].id,
                &[Page {
                    local_path: Some(external.clone()),
                    ..Page::default()
                }],
            )
            .unwrap();
        store.set_chapter_downloaded(&chapters[2].id, true).unwrap();
        let primary = rusqlite::Connection::open(&database).unwrap();
        primary.execute_batch("CREATE TRIGGER reject_book_job BEFORE DELETE ON downloads WHEN OLD.chapter_id='komga:chapter-0' BEGIN SELECT RAISE(ABORT,'fixture book job failure'); END; CREATE TRIGGER reject_book_legacy BEFORE UPDATE ON chapters WHEN OLD.id='komga:chapter-2' AND json_extract(NEW.document,'$.downloaded')=0 BEGIN SELECT RAISE(ABORT,'fixture legacy failure'); END;").unwrap();
        cache
            .set_config(crate::cache::CacheConfig {
                max_unpinned_bytes: 0,
                min_free_bytes: 0,
            })
            .unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        worker.remove_book_downloads(&book.id).unwrap();
        let (removed, cancelled, failures, local, changed, error) =
            wait_book_removed(&worker, &book.id, false);
        assert_eq!(
            (removed, cancelled, failures, local, changed),
            (1, 0, 2, 0, true)
        );
        let error = error.unwrap();
        assert!(
            error.contains("fixture book job failure") && error.contains("fixture legacy failure")
        );
        assert!(store.download(&chapters[0].id).unwrap().is_some());
        assert!(store.chapter(&chapters[0].id).unwrap().unwrap().downloaded);
        assert!(failed.local_path.as_ref().unwrap().is_file());
        assert!(store.download(&chapters[1].id).unwrap().is_none());
        assert!(store.chapter(&chapters[2].id).unwrap().unwrap().downloaded);
        assert_eq!(fs::read(external).unwrap(), original);
        let index = rusqlite::Connection::open(cache.namespace().join("index.sqlite3")).unwrap();
        let pinned: bool = index
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM pins WHERE raw_key=?1 AND owner=?2)",
                rusqlite::params![CacheManager::page_key(&failed), owner(&chapters[0].id)],
                |row| row.get(0),
            )
            .unwrap();
        assert!(pinned);
        assert_eq!(cache.stats().external_validations, 0);
        stop(&worker);
    }

    #[test]
    fn book_removal_waits_for_active_drain_and_obeys_the_strict_suspend_barrier() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(5, Duration::ZERO);
        server.hold_images();
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 2, 5);
        let cache = CacheManager::open(directory.path()).unwrap();
        let first = cached_test_page(
            &cache,
            manual_pages(&server, &chapters[0], 5)[0].url.clone(),
            &owner(&chapters[0].id),
        );
        let mut active_pages = manual_pages(&server, &chapters[0], 5);
        active_pages[0] = first;
        store.save_pages(&chapters[0].id, &active_pages).unwrap();
        let legacy = cached_test_page(
            &cache,
            manual_pages(&server, &chapters[1], 1)[0].url.clone(),
            &owner(&chapters[1].id),
        );
        store
            .save_pages(&chapters[1].id, &vec![legacy.clone(); 130])
            .unwrap();
        store.set_chapter_downloaded(&chapters[1].id, true).unwrap();
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue(&book, &chapters[0]).unwrap();
        server.wait_for_images(2);
        worker.remove_book_downloads(&book.id).unwrap();
        wait_for(&worker, "cancelled");
        worker.pause_for_suspend().unwrap();
        wait_suspend_ready(&worker);
        let checkpoint = store.book_download_count(&book.id).unwrap();
        server.release_images();
        wait_active(&worker, 0);
        assert_eq!(store.book_download_count(&book.id).unwrap(), checkpoint);
        for event in worker.drain_events() {
            assert!(!matches!(event, DownloadEvent::BookRemovalFinished { .. }));
            assert!(!matches!(event, DownloadEvent::Error(_)));
        }
        worker.resume_owned(false).unwrap();
        assert_eq!(
            wait_book_removed(&worker, &book.id, true),
            (1, 1, 0, 0, true, None)
        );
        assert_eq!(store.book_download_count(&book.id).unwrap(), 0);
        assert!(legacy.local_path.as_ref().unwrap().is_file());
        assert_eq!(cache.stats().pinned_entries, 0);
        stop(&worker);
        assert_eq!(server.image_count(), 2);
    }

    fn wait_error(worker: &DownloadWorker) -> String {
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if let Ok(DownloadEvent::Error(error)) =
                worker.events.recv_timeout(Duration::from_millis(100))
            {
                return error;
            }
        }
        panic!("Timed out waiting for a download command error");
    }

    fn cached_test_page(cache: &CacheManager, url: String, pin_owner: &str) -> Page {
        let mut bytes = Cursor::new(Vec::new());
        image::GrayImage::from_pixel(8, 12, image::Luma([85]))
            .write_to(&mut bytes, image::ImageFormat::Png)
            .unwrap();
        let mut page = Page {
            url,
            ..Page::default()
        };
        let write = cache
            .begin_raw(&page, Some(pin_owner), bytes.get_ref().len() as u64)
            .unwrap();
        fs::write(write.temp_path(), bytes.get_ref()).unwrap();
        page.local_path = Some(write.commit().unwrap());
        page
    }

    fn completed_test_job(store: &Store, chapter: &Chapter, pages: &[Page]) -> DownloadJob {
        store.save_pages(&chapter.id, pages).unwrap();
        let total = pages.len() as u32;
        let job = DownloadJob {
            chapter_id: chapter.id.clone(),
            book_id: chapter.book_id.clone(),
            state: "completed".into(),
            total,
            completed: total,
            error: String::new(),
        };
        store.save_download(&job).unwrap();
        job
    }

    #[test]
    fn downloaded_metadata_events_cover_only_real_replay_changes_and_partial_failures() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 5, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let pages = chapters
            .iter()
            .map(|chapter| {
                let page = cached_test_page(
                    &cache,
                    manual_pages(&server, chapter, 1)[0].url.clone(),
                    &owner(&chapter.id),
                );
                completed_test_job(&store, chapter, std::slice::from_ref(&page));
                page
            })
            .collect::<Vec<_>>();
        let other = cached_test_page(
            &cache,
            format!("{}/protected-other.png", server.base_url),
            "reader:protected",
        );
        let worker = DownloadWorker::start_with_options(
            directory.path().to_path_buf(),
            database.clone(),
            true,
        )
        .unwrap();

        worker.enqueue(&book, &chapters[0]).unwrap();
        assert_eq!(metadata_until_batch(&worker), (BTreeSet::new(), (0, 0, 1)));
        store
            .set_chapter_downloaded(&chapters[0].id, false)
            .unwrap();
        worker.enqueue(&book, &chapters[0]).unwrap();
        assert_eq!(
            metadata_until_batch(&worker),
            (BTreeSet::from([chapters[0].id.clone()]), (0, 0, 1))
        );
        assert!(store.chapter(&chapters[0].id).unwrap().unwrap().downloaded);

        fs::remove_file(pages[1].local_path.as_ref().unwrap()).unwrap();
        fs::write(
            pages[2].local_path.as_ref().unwrap(),
            b"corrupt cached image",
        )
        .unwrap();
        let wrong = Page {
            local_path: other.local_path.clone(),
            ..pages[3].clone()
        };
        completed_test_job(&store, &chapters[3], &[wrong]);
        worker
            .enqueue_many(
                &book,
                &[
                    chapters[1].clone(),
                    chapters[2].clone(),
                    chapters[3].clone(),
                    chapters[3].clone(),
                ],
            )
            .unwrap();
        let expected = BTreeSet::from([
            chapters[1].id.clone(),
            chapters[2].id.clone(),
            chapters[3].id.clone(),
        ]);
        assert_eq!(metadata_until_batch(&worker), (expected, (3, 0, 0)));
        for chapter in &chapters[1..4] {
            assert_eq!(
                store.download(&chapter.id).unwrap().unwrap().state,
                "queued"
            );
            assert!(!store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        }

        let wrong = Page {
            local_path: other.local_path.clone(),
            ..pages[4].clone()
        };
        completed_test_job(&store, &chapters[4], std::slice::from_ref(&wrong));
        let primary = rusqlite::Connection::open(&database).unwrap();
        primary.execute_batch("CREATE TRIGGER reject_usage_enqueue BEFORE UPDATE ON downloads WHEN NEW.state='queued' BEGIN SELECT RAISE(ABORT, 'fixture queue transaction rejected'); END;").unwrap();
        worker.enqueue(&book, &chapters[4]).unwrap();
        let (changed, error) = metadata_until_error(&worker);
        assert_eq!(changed, BTreeSet::from([chapters[4].id.clone()]));
        assert!(error.contains("fixture queue transaction rejected"));
        assert_eq!(
            store.download(&chapters[4].id).unwrap().unwrap().state,
            "completed"
        );
        assert!(!store.chapter(&chapters[4].id).unwrap().unwrap().downloaded);
        assert!(
            store.pages(&chapters[4].id).unwrap()[0]
                .local_path
                .is_none()
        );
        primary
            .execute_batch("DROP TRIGGER reject_usage_enqueue;")
            .unwrap();
        drop(primary);

        completed_test_job(&store, &chapters[4], &[wrong]);
        cache.unpin_owner(&owner(&chapters[0].id)).unwrap();
        let index =
            rusqlite::Connection::open(directory.path().join("cache/shared-v1/index.sqlite3"))
                .unwrap();
        index.execute_batch("CREATE TRIGGER reject_usage_pin BEFORE INSERT ON pins BEGIN SELECT RAISE(ABORT, 'fixture cache pin rejected'); END;").unwrap();
        worker
            .enqueue_many(&book, &[chapters[4].clone(), chapters[0].clone()])
            .unwrap();
        let (changed, error) = metadata_until_error(&worker);
        assert_eq!(changed, BTreeSet::from([chapters[4].id.clone()]));
        assert!(error.contains("fixture cache pin rejected"));
        assert!(
            store.pages(&chapters[4].id).unwrap()[0]
                .local_path
                .is_none()
        );
        assert_eq!(
            store.download(&chapters[4].id).unwrap().unwrap().state,
            "completed"
        );
        assert!(store.chapter(&chapters[0].id).unwrap().unwrap().downloaded);
        index
            .execute_batch("DROP TRIGGER reject_usage_pin;")
            .unwrap();
        drop(index);
        assert!(other.local_path.as_ref().unwrap().is_file());
        assert_eq!(server.records().len(), 0);
        stop(&worker);
    }

    #[test]
    fn completed_replay_keeps_valid_jobs_complete_without_http_or_managed_image_decode() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let chapter = &chapters[0];
        let page = cached_test_page(
            &cache,
            manual_pages(&server, chapter, 1)[0].url.clone(),
            &owner(&chapter.id),
        );
        let previous = completed_test_job(&store, chapter, std::slice::from_ref(&page));
        cache.unpin_owner(&owner(&chapter.id)).unwrap();
        store.set_chapter_downloaded(&chapter.id, false).unwrap();
        drop(cache);
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        let incoming = Chapter {
            page_count: 99,
            ..chapter.clone()
        };
        worker
            .enqueue_many(&book, &[incoming.clone(), incoming])
            .unwrap();
        match worker.events.recv_timeout(Duration::from_secs(5)).unwrap() {
            DownloadEvent::DownloadedMetadataChanged { chapter_id } => {
                assert_eq!(chapter_id, chapter.id)
            }
            other => panic!("A restored completed flag was not reported precisely: {other:?}"),
        }
        match worker.events.recv_timeout(Duration::from_secs(5)).unwrap() {
            DownloadEvent::StateChanged(job) => assert_eq!(job, previous),
            other => panic!("A completed replay unexpectedly ran work: {other:?}"),
        }
        assert_eq!(wait_batch(&worker), (0, 0, 1));
        assert!(
            worker
                .events
                .recv_timeout(Duration::from_millis(100))
                .is_err()
        );
        assert_eq!(store.download(&chapter.id).unwrap().unwrap(), previous);
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        let cache = CacheManager::open(directory.path()).unwrap();
        assert_eq!(cache.stats().pinned_entries, 1);
        assert_eq!(cache.stats().external_validations, 0);
        assert_eq!(server.records().len(), 0);
        assert!(!directory.path().join("sources").exists());
        stop(&worker);
    }

    #[test]
    fn completed_replay_repairs_missing_and_corrupted_raw_images() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 2, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let mut pages = Vec::new();
        for chapter in &chapters {
            let page = cached_test_page(
                &cache,
                manual_pages(&server, chapter, 1)[0].url.clone(),
                &owner(&chapter.id),
            );
            completed_test_job(&store, chapter, std::slice::from_ref(&page));
            pages.push(page);
        }
        fs::remove_file(pages[0].local_path.as_ref().unwrap()).unwrap();
        fs::write(
            pages[1].local_path.as_ref().unwrap(),
            b"corrupted cached image",
        )
        .unwrap();
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue_many(&book, &chapters).unwrap();
        let mut completed = HashSet::new();
        while completed.len() < 2 {
            completed.insert(wait_for(&worker, "completed").chapter_id);
        }
        stop(&worker);
        assert_eq!(server.image_count(), 2);
        assert_eq!(server.manifest_count(), 0);
        for chapter in &chapters {
            let saved = store.pages(&chapter.id).unwrap();
            assert!(cache.lookup_raw(&saved[0]).unwrap().is_some());
            assert_eq!(
                store.download(&chapter.id).unwrap().unwrap().state,
                "completed"
            );
            assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        }
    }

    #[test]
    fn completed_replay_mixed_selection_deduplicates_valid_repair_and_new_work() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 3, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        for (index, chapter) in chapters.iter().take(2).enumerate() {
            let page = cached_test_page(
                &cache,
                manual_pages(&server, chapter, 1)[0].url.clone(),
                &owner(&chapter.id),
            );
            completed_test_job(&store, chapter, std::slice::from_ref(&page));
            if index == 1 {
                fs::remove_file(page.local_path.as_ref().unwrap()).unwrap();
            }
        }
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        let duplicate_selection = chapters
            .iter()
            .chain(chapters.iter())
            .cloned()
            .collect::<Vec<_>>();
        worker.enqueue_many(&book, &duplicate_selection).unwrap();
        assert_eq!(wait_batch(&worker), (2, 0, 1));
        assert_eq!(
            store.download(&chapters[0].id).unwrap().unwrap().state,
            "completed"
        );
        let mut completed = HashSet::from([chapters[0].id.clone()]);
        while completed.len() < 3 {
            completed.insert(wait_for(&worker, "completed").chapter_id);
        }
        stop(&worker);
        assert_eq!(store.downloads().unwrap().len(), 3);
        assert_eq!(server.image_count(), 2);
        assert_eq!(server.manifest_count(), 1);
        assert!(
            server
                .records()
                .iter()
                .all(|record| !record.path.contains("chapter-0"))
        );
        assert!(
            store
                .downloads()
                .unwrap()
                .iter()
                .all(|job| job.state == "completed" && job.completed == 1 && job.total == 1)
        );
    }

    #[test]
    fn completed_replay_relinks_a_wrong_managed_raw_identity_without_http() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let chapter = &chapters[0];
        let correct = cached_test_page(
            &cache,
            manual_pages(&server, chapter, 1)[0].url.clone(),
            &owner(&chapter.id),
        );
        let other = cached_test_page(
            &cache,
            format!("{}/another-identity.png", server.base_url),
            "download:another-owner",
        );
        let wrong = Page {
            local_path: other.local_path.clone(),
            ..correct.clone()
        };
        completed_test_job(&store, chapter, std::slice::from_ref(&wrong));
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue(&book, chapter).unwrap();
        wait_for(&worker, "completed");
        stop(&worker);
        assert_eq!(store.pages(&chapter.id).unwrap(), vec![correct]);
        assert!(other.local_path.as_ref().unwrap().is_file());
        assert_eq!(server.records().len(), 0);
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
    }

    #[test]
    fn remove_or_cancel_uses_completed_controller_state_instead_of_an_old_pending_snapshot() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 1);
        let chapter = &chapters[0];
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue(&book, chapter).unwrap();
        let old_snapshot = wait_for(&worker, "queued");
        assert_eq!(old_snapshot.book_id, book.id);
        wait_for(&worker, "completed");
        let saved = store.pages(&chapter.id).unwrap();
        worker
            .remove_or_cancel(&old_snapshot.book_id, &old_snapshot.chapter_id)
            .unwrap();
        wait_removed(&worker, &chapter.id);
        assert!(store.download(&chapter.id).unwrap().is_none());
        assert!(!store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        assert!(saved[0].local_path.as_ref().unwrap().is_file());
        worker.remove_or_cancel(&book.id, &chapter.id).unwrap();
        worker.cancel_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 0);
        stop(&worker);
        assert_eq!(server.image_count(), 1);
        assert_eq!(server.manifest_count(), 1);
    }

    #[test]
    fn remove_or_cancel_rejects_a_wrong_book_without_touching_completed_data_or_pins() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, _, chapters) = network_fixture(directory.path(), &server, 1, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let chapter = &chapters[0];
        let page = cached_test_page(
            &cache,
            manual_pages(&server, chapter, 1)[0].url.clone(),
            &owner(&chapter.id),
        );
        let previous = completed_test_job(&store, chapter, std::slice::from_ref(&page));
        let bytes = fs::read(page.local_path.as_ref().unwrap()).unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        worker
            .remove_or_cancel("a-different-book", &chapter.id)
            .unwrap();
        assert!(wait_error(&worker).contains("different book"));
        assert_eq!(store.download(&chapter.id).unwrap().unwrap(), previous);
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        assert_eq!(fs::read(page.local_path.as_ref().unwrap()).unwrap(), bytes);
        assert_eq!(cache.stats().pinned_entries, 1);
        stop(&worker);
        assert_eq!(server.records().len(), 0);
    }

    #[test]
    fn remove_or_cancel_cancels_pending_and_failed_jobs_preserving_other_pin_owners() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(2, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 2, 2);
        let cache = CacheManager::open(directory.path()).unwrap();
        for (index, chapter) in chapters.iter().enumerate() {
            let cached = cached_test_page(
                &cache,
                manual_pages(&server, chapter, 2)[0].url.clone(),
                &owner(&chapter.id),
            );
            let mut pages = manual_pages(&server, chapter, 2);
            pages[0] = cached.clone();
            store.save_pages(&chapter.id, &pages).unwrap();
            store
                .save_download(&DownloadJob {
                    chapter_id: chapter.id.clone(),
                    book_id: book.id.clone(),
                    state: if index == 0 {
                        "queued".into()
                    } else {
                        "failed".into()
                    },
                    total: 2,
                    completed: 1,
                    error: String::new(),
                })
                .unwrap();
            if index == 0 {
                cache.pin_raw(&cached, "reader:protected").unwrap();
            }
        }
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        for chapter in &chapters {
            worker.remove_or_cancel(&book.id, &chapter.id).unwrap();
            assert_eq!(wait_for(&worker, "cancelled").chapter_id, chapter.id);
            assert_eq!(
                store.download(&chapter.id).unwrap().unwrap().state,
                "cancelled"
            );
            assert!(!store.chapter(&chapter.id).unwrap().unwrap().downloaded);
            assert!(
                store.pages(&chapter.id).unwrap()[0]
                    .local_path
                    .as_ref()
                    .unwrap()
                    .is_file()
            );
        }
        stop(&worker);
        assert_eq!(cache.stats().pinned_entries, 1);
        assert_eq!(server.records().len(), 0);
    }

    #[test]
    fn removing_completed_download_keeps_other_owners_and_active_reader_leases() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, _, chapters) = network_fixture(directory.path(), &server, 2, 2);
        let cache = CacheManager::open(directory.path()).unwrap();
        let first_owner = owner(&chapters[0].id);
        let shared = cached_test_page(
            &cache,
            format!("{}/shared.png", server.base_url),
            &first_owner,
        );
        let leased = cached_test_page(
            &cache,
            format!("{}/leased.png", server.base_url),
            &first_owner,
        );
        cache.pin_raw(&shared, &owner(&chapters[1].id)).unwrap();
        store
            .save_pages(&chapters[0].id, &[shared.clone(), leased.clone()])
            .unwrap();
        store
            .save_pages(&chapters[1].id, std::slice::from_ref(&shared))
            .unwrap();
        for (index, chapter) in chapters.iter().enumerate() {
            let total = if index == 0 { 2 } else { 1 };
            store
                .save_download(&DownloadJob {
                    chapter_id: chapter.id.clone(),
                    book_id: chapter.book_id.clone(),
                    state: "completed".into(),
                    total,
                    completed: total,
                    error: String::new(),
                })
                .unwrap();
        }
        let reader_lease = cache
            .lease_path(leased.local_path.as_ref().unwrap())
            .unwrap();
        cache
            .set_config(crate::cache::CacheConfig {
                max_unpinned_bytes: 0,
                min_free_bytes: 0,
            })
            .unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        worker.remove_completed(&chapters[0].id).unwrap();
        wait_removed(&worker, &chapters[0].id);
        assert!(store.download(&chapters[0].id).unwrap().is_none());
        assert!(!store.chapter(&chapters[0].id).unwrap().unwrap().downloaded);
        assert!(store.download(&chapters[1].id).unwrap().is_some());
        assert!(store.chapter(&chapters[1].id).unwrap().unwrap().downloaded);
        assert!(shared.local_path.as_ref().unwrap().is_file());
        assert!(leased.local_path.as_ref().unwrap().is_file());
        assert_eq!(cache.stats().pinned_entries, 1);
        drop(reader_lease);
        cache.prune().unwrap();
        assert!(shared.local_path.as_ref().unwrap().is_file());
        assert!(!leased.local_path.as_ref().unwrap().exists());
        stop(&worker);
    }

    #[test]
    fn removal_rejects_unfinished_jobs_and_preserves_a_completed_local_import() {
        let directory = tempfile::tempdir().unwrap();
        let (database, _, chapter) = local_fixture(directory.path(), "paused", true);
        let store = Store::open(&database).unwrap();
        let original = store.pages(&chapter.id).unwrap()[0]
            .local_path
            .clone()
            .unwrap();
        let original_bytes = fs::read(&original).unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        worker.remove_completed(&chapter.id).unwrap();
        assert!(wait_error(&worker).contains("Only a completed"));
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "paused"
        );
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        let mut job = store.download(&chapter.id).unwrap().unwrap();
        job.state = "completed".into();
        job.completed = job.total;
        store.save_download(&job).unwrap();
        worker.remove_completed(&chapter.id).unwrap();
        wait_removed(&worker, &chapter.id);
        assert!(store.download(&chapter.id).unwrap().is_none());
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        assert_eq!(fs::read(original).unwrap(), original_bytes);
        stop(&worker);
    }

    #[test]
    fn failed_primary_database_removal_restores_download_pins_before_releasing_leases() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, _, chapters) = network_fixture(directory.path(), &server, 1, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let chapter = &chapters[0];
        let page = cached_test_page(
            &cache,
            format!("{}/protected.png", server.base_url),
            &owner(&chapter.id),
        );
        store
            .save_pages(&chapter.id, std::slice::from_ref(&page))
            .unwrap();
        store
            .save_download(&DownloadJob {
                chapter_id: chapter.id.clone(),
                book_id: chapter.book_id.clone(),
                state: "completed".into(),
                total: 1,
                completed: 1,
                error: String::new(),
            })
            .unwrap();
        let ddl = rusqlite::Connection::open(&database).unwrap();
        ddl.execute_batch("CREATE TRIGGER reject_download_removal BEFORE DELETE ON downloads BEGIN SELECT RAISE(ABORT, 'fixture removal rejected'); END;").unwrap();
        cache
            .set_config(crate::cache::CacheConfig {
                max_unpinned_bytes: 0,
                min_free_bytes: 0,
            })
            .unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        worker.remove_completed(&chapter.id).unwrap();
        assert!(wait_error(&worker).contains("fixture removal rejected"));
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "completed"
        );
        assert!(store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        assert!(page.local_path.as_ref().unwrap().is_file());
        assert_eq!(cache.stats().pinned_entries, 1);
        assert_eq!(cache.stats().leased_entries, 0);
        ddl.execute_batch("DROP TRIGGER reject_download_removal;")
            .unwrap();
        drop(ddl);
        worker.remove_completed(&chapter.id).unwrap();
        wait_removed(&worker, &chapter.id);
        assert!(store.download(&chapter.id).unwrap().is_none());
        stop(&worker);
    }

    #[test]
    fn recovered_queue_finishes_only_after_validating_saved_pages() {
        let directory = tempfile::tempdir().unwrap();
        let (database, _, chapter) = local_fixture(directory.path(), "downloading", true);
        let worker =
            DownloadWorker::start(directory.path().to_path_buf(), database.clone()).unwrap();
        let job = wait_for(&worker, "completed");
        assert_eq!((job.completed, job.total), (1, 1));
        stop(&worker);
        assert!(
            Store::open(database)
                .unwrap()
                .chapter(&chapter.id)
                .unwrap()
                .unwrap()
                .downloaded
        );
    }

    #[test]
    fn corrupt_saved_images_never_become_completed_downloads() {
        let directory = tempfile::tempdir().unwrap();
        let (database, _, chapter) = local_fixture(directory.path(), "queued", false);
        let worker =
            DownloadWorker::start(directory.path().to_path_buf(), database.clone()).unwrap();
        assert!(!wait_for(&worker, "failed").error.is_empty());
        stop(&worker);
        assert!(
            !Store::open(database)
                .unwrap()
                .chapter(&chapter.id)
                .unwrap()
                .unwrap()
                .downloaded
        );
    }

    #[test]
    fn paused_queue_survives_startup_and_resumes_explicitly() {
        let directory = tempfile::tempdir().unwrap();
        let (database, _, chapter) = local_fixture(directory.path(), "paused", true);
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        assert!(
            worker
                .events
                .recv_timeout(Duration::from_millis(120))
                .is_err()
        );
        worker.resume(&chapter.id).unwrap();
        assert_eq!(wait_for(&worker, "completed").completed, 1);
        stop(&worker);
    }

    #[test]
    fn cancelling_a_paused_job_preserves_the_original_local_image() {
        let directory = tempfile::tempdir().unwrap();
        let (database, _, chapter) = local_fixture(directory.path(), "paused", true);
        let worker =
            DownloadWorker::start(directory.path().to_path_buf(), database.clone()).unwrap();
        worker.cancel(&chapter.id).unwrap();
        wait_for(&worker, "cancelled");
        stop(&worker);
        let store = Store::open(database).unwrap();
        assert_eq!(store.downloads().unwrap()[0].state, "cancelled");
        assert!(
            store.pages(&chapter.id).unwrap()[0]
                .local_path
                .as_ref()
                .unwrap()
                .is_file()
        );
    }

    #[test]
    fn network_page_concurrency_is_bounded_at_two_or_the_selected_baseline() {
        for concurrency in [1, 2] {
            let directory = tempfile::tempdir().unwrap();
            let server = HttpFixture::new(4, Duration::from_millis(90));
            let (database, store, book, chapters) =
                network_fixture(directory.path(), &server, 1, 4);
            let worker = DownloadWorker::start_configured(
                directory.path().to_path_buf(),
                database,
                DownloadOptions {
                    offline: false,
                    page_concurrency: concurrency,
                },
            )
            .unwrap();
            worker.enqueue(&book, &chapters[0]).unwrap();
            assert_eq!(wait_for(&worker, "completed").completed, 4);
            stop(&worker);
            assert_eq!(server.maximum_active.load(Ordering::Acquire), concurrency);
            assert_eq!(server.image_count(), 4);
            assert_eq!(server.manifest_count(), 1);
            assert!(server.records().iter().all(|record| {
                record
                    .headers
                    .to_ascii_lowercase()
                    .contains("x-api-key: fixture-token")
            }));
            assert!(
                store
                    .pages(&chapters[0].id)
                    .unwrap()
                    .iter()
                    .all(|page| page.local_path.as_ref().is_some_and(|path| path.is_file()))
            );
            assert!(!directory.path().join("downloads").exists());
            assert!(
                CacheManager::open(directory.path())
                    .unwrap()
                    .stats()
                    .pinned_bytes
                    > 0
            );
        }
    }

    #[test]
    fn repeated_bulk_selection_queues_six_chapters_once_and_batches_their_pages() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(3, Duration::from_millis(15));
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 6, 3);
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        let repeated = chapters
            .iter()
            .chain(chapters.iter())
            .cloned()
            .collect::<Vec<_>>();
        worker.enqueue_many(&book, &repeated).unwrap();
        worker.enqueue_many(&book, &repeated).unwrap();
        let mut completed = HashSet::new();
        while completed.len() < 6 {
            completed.insert(wait_for(&worker, "completed").chapter_id);
        }
        stop(&worker);
        assert_eq!(store.downloads().unwrap().len(), 6);
        assert_eq!(server.manifest_count(), 6);
        assert_eq!(server.image_count(), 18);
        assert!(server.maximum_active.load(Ordering::Acquire) <= 2);
    }

    #[test]
    fn resume_reuses_the_saved_manifest_and_shared_raw_without_new_http() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(3, Duration::from_millis(10));
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 3);
        let chapter = &chapters[0];
        store
            .save_pages(&chapter.id, &manual_pages(&server, chapter, 3))
            .unwrap();
        let worker =
            DownloadWorker::start(directory.path().to_path_buf(), database.clone()).unwrap();
        worker.enqueue(&book, chapter).unwrap();
        wait_for(&worker, "completed");
        stop(&worker);
        let original = store.pages(&chapter.id).unwrap();
        assert_eq!((server.manifest_count(), server.image_count()), (0, 3));
        store.invalidate_page_cache(&chapter.id, 0).unwrap();
        let mut job = store.download(&chapter.id).unwrap().unwrap();
        job.state = "paused".into();
        job.completed = 0;
        store.save_download(&job).unwrap();
        let resumed = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        resumed.resume(&chapter.id).unwrap();
        wait_for(&resumed, "completed");
        stop(&resumed);
        assert_eq!((server.manifest_count(), server.image_count()), (0, 3));
        assert_eq!(store.pages(&chapter.id).unwrap(), original);
    }

    #[test]
    fn duplicate_page_identity_uses_one_http_request_and_one_raw_file() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(3, Duration::from_millis(60));
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 3);
        let request = Page {
            url: format!("{}/same.png", server.base_url),
            ..Page::default()
        };
        store
            .save_pages(
                &chapters[0].id,
                &[request.clone(), request.clone(), request],
            )
            .unwrap();
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue(&book, &chapters[0]).unwrap();
        wait_for(&worker, "completed");
        stop(&worker);
        assert_eq!(server.image_count(), 1);
        let paths = store
            .pages(&chapters[0].id)
            .unwrap()
            .into_iter()
            .map(|page| page.local_path.unwrap())
            .collect::<HashSet<_>>();
        assert_eq!(paths.len(), 1);
    }

    #[test]
    fn cancelling_drains_inflight_images_without_dispatching_more_or_blocking_shutdown() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(6, Duration::ZERO);
        server.hold_images();
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 6);
        store
            .save_pages(&chapters[0].id, &manual_pages(&server, &chapters[0], 6))
            .unwrap();
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue(&book, &chapters[0]).unwrap();
        server.wait_for_images(2);
        worker.cancel(&chapters[0].id).unwrap();
        wait_for(&worker, "cancelled");
        worker.shutdown();
        let started = Instant::now();
        assert!(!worker.wait_stopped(Duration::from_millis(10)));
        assert!(started.elapsed() < Duration::from_secs(1));
        server.release_images();
        assert!(worker.wait_stopped(Duration::from_secs(3)));
        assert_eq!(server.image_count(), 2);
        assert_eq!(
            store.download(&chapters[0].id).unwrap().unwrap().state,
            "cancelled"
        );
        let cache_stats = CacheManager::open(directory.path()).unwrap().stats();
        assert_eq!(cache_stats.pinned_bytes, 0);
        assert_eq!(cache_stats.pinned_entries, 0);
        assert_eq!(cache_stats.entries, 2);
    }

    #[test]
    fn repeating_a_bulk_selection_does_not_resume_a_paused_active_chapter() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(4, Duration::ZERO);
        server.hold_images();
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 4);
        store
            .save_pages(&chapters[0].id, &manual_pages(&server, &chapters[0], 4))
            .unwrap();
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue(&book, &chapters[0]).unwrap();
        server.wait_for_images(2);
        worker.pause(&chapters[0].id).unwrap();
        wait_for(&worker, "paused");
        worker.enqueue_many(&book, &chapters).unwrap();
        wait_for(&worker, "paused");
        server.release_images();
        while wait_for(&worker, "paused").completed != 2 {}
        assert_eq!(server.image_count(), 2);
        assert_eq!(
            store.download(&chapters[0].id).unwrap().unwrap().state,
            "paused"
        );
        assert!(
            CacheManager::open(directory.path())
                .unwrap()
                .stats()
                .pinned_bytes
                > 0
        );
        worker.resume(&chapters[0].id).unwrap();
        assert_eq!(wait_for(&worker, "completed").completed, 4);
        stop(&worker);
        assert_eq!(server.image_count(), 4);
        assert_eq!(server.manifest_count(), 0);
    }

    #[test]
    fn offline_worker_persists_commands_without_constructing_sources_or_using_http() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(2, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 2);
        store.enqueue_download_batch(&book, &chapters).unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        worker.resume(&chapters[0].id).unwrap();
        wait_for(&worker, "queued");
        worker.enqueue_many(&book, &chapters).unwrap();
        wait_for(&worker, "queued");
        assert_eq!(wait_batch(&worker), (1, 0, 0));
        assert!(
            worker
                .events
                .recv_timeout(Duration::from_millis(120))
                .is_err()
        );
        assert_eq!(store.downloads().unwrap()[0].state, "queued");
        assert_eq!(server.records().len(), 0);
        assert!(!directory.path().join("sources").exists());
        worker.pause(&chapters[0].id).unwrap();
        wait_for(&worker, "paused");
        worker.resume(&chapters[0].id).unwrap();
        wait_for(&worker, "queued");
        worker.cancel(&chapters[0].id).unwrap();
        wait_for(&worker, "cancelled");
        stop(&worker);
    }

    #[test]
    fn batch_acceptance_reports_deduplicated_persisted_states_without_claiming_new_work() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 3, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let page = cached_test_page(
            &cache,
            manual_pages(&server, &chapters[0], 1)[0].url.clone(),
            &owner(&chapters[0].id),
        );
        completed_test_job(&store, &chapters[0], &[page]);
        store
            .save_download(&DownloadJob {
                chapter_id: chapters[1].id.clone(),
                book_id: book.id.clone(),
                state: "paused".into(),
                total: 1,
                ..DownloadJob::default()
            })
            .unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        let repeated = chapters
            .iter()
            .chain(chapters.iter())
            .cloned()
            .collect::<Vec<_>>();
        worker.enqueue_many(&book, &repeated).unwrap();
        assert_eq!(wait_batch(&worker), (1, 1, 1));
        let counts = store.download_state_counts().unwrap();
        assert_eq!(
            (counts["queued"], counts["paused"], counts["completed"]),
            (1, 1, 1)
        );
        worker
            .enqueue_many(&book, &[chapters[0].clone(), chapters[0].clone()])
            .unwrap();
        assert_eq!(wait_batch(&worker), (0, 0, 1));
        worker
            .enqueue_many(&book, &[chapters[1].clone(), chapters[1].clone()])
            .unwrap();
        assert_eq!(wait_batch(&worker), (0, 1, 0));
        worker.enqueue_many(&book, &[]).unwrap();
        assert!(
            worker
                .events
                .recv_timeout(Duration::from_millis(100))
                .is_err()
        );
        assert_eq!(server.records().len(), 0);
        stop(&worker);
    }

    #[test]
    fn bulk_enqueue_events_report_mixed_noop_selection_without_individual_payloads() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 3, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let page = cached_test_page(
            &cache,
            manual_pages(&server, &chapters[0], 1)[0].url.clone(),
            &owner(&chapters[0].id),
        );
        completed_test_job(&store, &chapters[0], &[page]);
        store
            .save_download(&DownloadJob {
                chapter_id: chapters[1].id.clone(),
                book_id: book.id.clone(),
                state: "paused".into(),
                total: 1,
                ..DownloadJob::default()
            })
            .unwrap();
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        let selection = chapters
            .iter()
            .chain(chapters.iter())
            .cloned()
            .collect::<Vec<_>>();
        worker.enqueue_many(&book, &selection).unwrap();
        assert_eq!(wait_batch_without_rows(&worker), (1, 1, 1));
        let committed = store.downloads().unwrap();
        let counts = store.download_state_counts().unwrap();
        assert_eq!(
            (counts["queued"], counts["paused"], counts["completed"]),
            (1, 1, 1)
        );
        assert!(
            worker
                .events
                .recv_timeout(Duration::from_millis(100))
                .is_err()
        );
        worker.enqueue_many(&book, &selection).unwrap();
        assert_eq!(wait_batch_without_rows(&worker), (1, 1, 1));
        assert_eq!(store.downloads().unwrap(), committed);
        assert!(
            worker
                .events
                .recv_timeout(Duration::from_millis(100))
                .is_err()
        );
        assert_eq!(server.records().len(), 0);
        stop(&worker);
    }

    #[test]
    fn bulk_cancel_event_commits_state_while_active_pins_survive_until_drain() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(3, Duration::ZERO);
        server.hold_images();
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 3);
        let chapter = &chapters[0];
        let cache = CacheManager::open(directory.path()).unwrap();
        let cached = cached_test_page(
            &cache,
            manual_pages(&server, chapter, 3)[0].url.clone(),
            &owner(&chapter.id),
        );
        let mut pages = manual_pages(&server, chapter, 3);
        pages[0] = cached;
        store.save_pages(&chapter.id, &pages).unwrap();
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.enqueue(&book, chapter).unwrap();
        server.wait_for_images(2);
        worker.drain_events();
        worker.cancel_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 1);
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "cancelled"
        );
        assert!(worker.active_count() > 0);
        assert_eq!(cache.stats().pinned_entries, 1);
        server.release_images();
        stop(&worker);
        assert_eq!(
            store.download(&chapter.id).unwrap().unwrap().state,
            "cancelled"
        );
        assert!(!store.chapter(&chapter.id).unwrap().unwrap().downloaded);
        assert_eq!(cache.stats().pinned_entries, 0);
        assert_eq!(server.image_count(), 2);
    }

    #[test]
    fn offline_bulk_actions_preserve_finished_jobs_and_other_cache_pin_owners() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 6, 1);
        let cache = CacheManager::open(directory.path()).unwrap();
        let mut png = Cursor::new(Vec::new());
        image::GrayImage::from_pixel(8, 12, image::Luma([85]))
            .write_to(&mut png, image::ImageFormat::Png)
            .unwrap();
        for (index, (chapter, state)) in chapters
            .iter()
            .zip([
                "queued",
                "downloading",
                "paused",
                "failed",
                "completed",
                "cancelled",
            ])
            .enumerate()
        {
            let mut page = manual_pages(&server, chapter, 1).remove(0);
            let pin_owner = owner(&chapter.id);
            let write = cache
                .begin_raw(
                    &page,
                    (index != 5).then_some(pin_owner.as_str()),
                    png.get_ref().len() as u64,
                )
                .unwrap();
            fs::write(write.temp_path(), png.get_ref()).unwrap();
            page.local_path = Some(write.commit().unwrap());
            store
                .save_pages(&chapter.id, std::slice::from_ref(&page))
                .unwrap();
            store
                .save_download(&DownloadJob {
                    chapter_id: chapter.id.clone(),
                    book_id: book.id.clone(),
                    state: state.into(),
                    completed: 1,
                    total: 1,
                    error: String::new(),
                })
                .unwrap();
            if index == 0 {
                cache.pin_raw(&page, "reader:protected").unwrap();
            }
        }
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        worker.pause_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 2);
        assert_eq!(
            store
                .downloads()
                .unwrap()
                .iter()
                .map(|job| job.state.as_str())
                .collect::<Vec<_>>(),
            [
                "paused",
                "paused",
                "paused",
                "failed",
                "completed",
                "cancelled"
            ]
        );
        worker.pause_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 0);
        worker.resume_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 4);
        let resumed = store.download_state_counts().unwrap();
        assert_eq!(
            (
                resumed["queued"],
                resumed["paused"],
                resumed["failed"],
                resumed["completed"],
                resumed["cancelled"]
            ),
            (4, 0, 0, 1, 1)
        );
        let mut failed = store.download(&chapters[3].id).unwrap().unwrap();
        failed.state = "failed".into();
        store.save_download(&failed).unwrap();
        worker.cancel_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 3);
        worker.cancel_all().unwrap();
        assert_eq!(wait_queue_changed(&worker), 0);
        stop(&worker);
        assert_eq!(
            store
                .downloads()
                .unwrap()
                .iter()
                .map(|job| job.state.as_str())
                .collect::<Vec<_>>(),
            [
                "cancelled",
                "cancelled",
                "cancelled",
                "failed",
                "completed",
                "cancelled"
            ]
        );
        assert_eq!(cache.stats().pinned_entries, 3);
        cache.unpin_owner("reader:protected").unwrap();
        assert_eq!(cache.stats().pinned_entries, 2);
        assert_eq!(server.records().len(), 0);
    }

    #[test]
    fn shutdown_acknowledges_after_the_sqlite_connection_is_closed() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("shutdown.sqlite");
        let worker = DownloadWorker::start_with_options(
            directory.path().to_path_buf(),
            database.clone(),
            true,
        )
        .unwrap();
        stop(&worker);
        assert!(worker.join_handle.lock().unwrap().is_none());
        let connection = rusqlite::Connection::open(database).unwrap();
        connection.busy_timeout(Duration::ZERO).unwrap();
        connection
            .execute_batch("BEGIN EXCLUSIVE; ROLLBACK;")
            .unwrap();
        assert!(worker.wait_stopped(Duration::ZERO));
    }

    #[test]
    fn waiting_for_a_running_worker_obeys_the_timeout_without_stopping_it() {
        let directory = tempfile::tempdir().unwrap();
        let worker = DownloadWorker::start_with_options(
            directory.path().to_path_buf(),
            directory.path().join("waiting.sqlite"),
            true,
        )
        .unwrap();
        let started = Instant::now();
        assert!(!worker.wait_stopped(Duration::from_millis(10)));
        assert!(started.elapsed() < Duration::from_secs(1));
        stop(&worker);
    }

    #[test]
    fn invalid_download_concurrency_is_rejected_before_spawning_a_worker() {
        let directory = tempfile::tempdir().unwrap();
        for page_concurrency in [0, 3] {
            assert!(
                DownloadWorker::start_configured(
                    directory.path().to_path_buf(),
                    directory.path().join("invalid.sqlite"),
                    DownloadOptions {
                        offline: false,
                        page_concurrency
                    }
                )
                .is_err()
            );
        }
    }

    #[test]
    fn prepared_downloads_keep_canonical_manifests_and_reuse_after_restart() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(2, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 2);
        let preferences = Preferences {
            resize_downloads: true,
            grayscale_downloads: true,
            ..Preferences::default()
        };
        store.save_preferences(&preferences).unwrap();
        let worker =
            DownloadWorker::start(directory.path().to_path_buf(), database.clone()).unwrap();
        worker.set_processing_viewport(2, 3).unwrap();
        worker.enqueue(&book, &chapters[0]).unwrap();
        wait_for(&worker, "completed");
        let cache = CacheManager::open(directory.path()).unwrap();
        let pages = store.pages(&chapters[0].id).unwrap();
        assert_eq!(pages.len(), 2);
        let mut prepared = Vec::new();
        for page in &pages {
            let original = page.local_path.as_ref().unwrap();
            assert_eq!(cache.lookup_raw(page).unwrap().as_ref(), Some(original));
            let copy = prepared_page_path(&cache, page, original, &preferences, (2, 3))
                .unwrap()
                .unwrap();
            assert_ne!(copy, *original);
            let image = image::ImageReader::open(&copy)
                .unwrap()
                .with_guessed_format()
                .unwrap()
                .decode()
                .unwrap();
            assert_eq!((image.width(), image.height()), (4, 6));
            assert_eq!(image.color(), image::ColorType::L8);
            prepared.push(copy);
        }
        assert_eq!(cache.stats().pinned_entries, 4);
        let requests = server.records().len();
        stop(&worker);
        assert!(worker.is_stopped());

        let restarted = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        restarted.set_processing_viewport(3, 2).unwrap();
        restarted.enqueue(&book, &chapters[0]).unwrap();
        assert_eq!(wait_batch(&restarted), (0, 0, 1));
        assert_eq!(server.records().len(), requests);
        for (page, expected) in pages.iter().zip(prepared) {
            assert_eq!(
                prepared_page_path(
                    &cache,
                    page,
                    page.local_path.as_ref().unwrap(),
                    &preferences,
                    (3, 2)
                )
                .unwrap(),
                Some(expected)
            );
        }
        stop(&restarted);
    }

    #[test]
    fn enabling_offline_preparation_requeues_existing_originals_without_http() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 1, 1);
        let worker = DownloadWorker::start(directory.path().to_path_buf(), database).unwrap();
        worker.set_processing_viewport(2, 3).unwrap();
        worker.enqueue(&book, &chapters[0]).unwrap();
        wait_for(&worker, "completed");
        let original_pages = store.pages(&chapters[0].id).unwrap();
        let requests = server.records().len();
        let preferences = Preferences {
            grayscale_downloads: true,
            ..Preferences::default()
        };
        store.save_preferences(&preferences).unwrap();
        worker.enqueue(&book, &chapters[0]).unwrap();
        wait_for(&worker, "completed");
        assert_eq!(store.pages(&chapters[0].id).unwrap(), original_pages);
        assert_eq!(server.records().len(), requests);
        let cache = CacheManager::open(directory.path()).unwrap();
        let page = &original_pages[0];
        assert!(
            prepared_page_path(
                &cache,
                page,
                page.local_path.as_ref().unwrap(),
                &preferences,
                (2, 3)
            )
            .unwrap()
            .is_some()
        );
        stop(&worker);
    }

    #[test]
    fn automatic_enqueue_cannot_replace_uncommitted_user_controls() {
        let directory = tempfile::tempdir().unwrap();
        let server = HttpFixture::new(1, Duration::ZERO);
        let (database, store, book, chapters) = network_fixture(directory.path(), &server, 3, 1);
        let worker =
            DownloadWorker::start_with_options(directory.path().to_path_buf(), database, true)
                .unwrap();
        {
            let mut controls = worker.controls.lock().unwrap();
            controls.insert(chapters[0].id.clone(), Control::Paused);
            controls.insert(chapters[1].id.clone(), Control::Cancelled);
        }
        worker.enqueue_automatic(&book, &chapters).unwrap();
        assert_eq!(wait_batch(&worker), (1, 0, 0));
        assert!(store.download(&chapters[0].id).unwrap().is_none());
        assert!(store.download(&chapters[1].id).unwrap().is_none());
        assert_eq!(
            store.download(&chapters[2].id).unwrap().unwrap().state,
            "queued"
        );
        {
            let controls = worker.controls.lock().unwrap();
            assert!(matches!(
                controls.get(&chapters[0].id),
                Some(Control::Paused)
            ));
            assert!(matches!(
                controls.get(&chapters[1].id),
                Some(Control::Cancelled)
            ));
        }
        assert!(server.records().is_empty());
        stop(&worker);
    }
}
