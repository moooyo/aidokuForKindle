//! Shared, bounded disk storage for validated source and rendered artifacts.
//! Networking and request coalescing belong to the source scheduler.

use std::{
    collections::{HashMap, HashSet, VecDeque},
    fs::{self, File, Metadata, OpenOptions},
    io::Read,
    ops::{Deref, DerefMut},
    path::{Path, PathBuf},
    sync::{
        Arc, Condvar, LockResult, Mutex, MutexGuard, OnceLock, PoisonError, Weak,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant, UNIX_EPOCH},
};

use anyhow::{Context, Result, anyhow, bail, ensure};
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::domain::Page;

const MAX_METADATA: usize = 20_000;
const ACCESS_BATCH: usize = 128;
const ACCESS_INTERVAL: Duration = Duration::from_secs(2);
const CLEAR_BATCH: usize = 32;
const STORAGE_PAGE_LIMIT: usize = 128;
static NEXT_WRITE: AtomicU64 = AtomicU64::new(1);
static REGISTRY: OnceLock<Mutex<HashMap<PathBuf, Weak<CacheInner>>>> = OnceLock::new();

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct CacheConfig {
    pub max_unpinned_bytes: u64,
    pub min_free_bytes: u64,
}

impl Default for CacheConfig {
    fn default() -> Self {
        Self {
            max_unpinned_bytes: 256 * 1024 * 1024,
            min_free_bytes: 64 * 1024 * 1024,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct RenderKey {
    pub source_key: String,
    pub width: u32,
    pub height: u32,
    pub crop_borders: bool,
    pub dither: bool,
    pub contrast: i32,
    pub vertical: bool,
    pub offset: u32,
    pub zoom_milli: u32,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct CacheStats {
    pub hits: u64,
    pub misses: u64,
    pub raw_hits: u64,
    pub raw_misses: u64,
    pub processed_hits: u64,
    pub processed_misses: u64,
    pub external_hits: u64,
    pub external_validations: u64,
    pub bytes: u64,
    pub pinned_bytes: u64,
    pub pinned_entries: u64,
    pub unpinned_bytes: u64,
    pub entries: u64,
    pub evictions: u64,
    pub evicted_bytes: u64,
    pub commits: u64,
    pub corruptions: u64,
    pub checksum_bytes: u64,
    pub reserved_bytes: u64,
    pub peak_reserved_bytes: u64,
    pub writes_inflight: u64,
    pub metadata_entries: u64,
    pub leased_entries: u64,
}

/// A metadata snapshot for background storage accounting. Paths are unvalidated
/// index candidates, not cache hits; files can be absent, replaced, or linked.
/// The worker rejects symbolic links and reads actual sizes after resolution.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StorageArtifact {
    pub id: String,
    pub path: PathBuf,
    pub pinned: bool,
    /// Historical hashed aliases remain unknown until their names are supplied
    /// again. A known cover alias takes precedence over an unknown alias.
    pub cover: Option<bool>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Raw,
    Processed,
}

impl Kind {
    fn name(self) -> &'static str {
        match self {
            Self::Raw => "raw",
            Self::Processed => "processed",
        }
    }
    fn extension(self) -> &'static str {
        match self {
            Self::Raw => "raw",
            Self::Processed => "png",
        }
    }
    fn parse(value: &str) -> Result<Self> {
        match value {
            "raw" => Ok(Self::Raw),
            "processed" => Ok(Self::Processed),
            _ => bail!("Unknown cache artifact kind"),
        }
    }
}

#[derive(Debug, Clone)]
struct Entry {
    id: String,
    kind: Kind,
    key: String,
    size: u64,
    checksum: String,
    fingerprint: String,
    access: u64,
    valid: bool,
    verified: bool,
}

#[derive(Debug, Clone)]
struct Reservation {
    id: String,
    kind: Kind,
    key: String,
    target: u64,
    owner: Option<String>,
    temp: PathBuf,
}

#[derive(Debug, Clone)]
struct Validation {
    fingerprint: String,
    checksum: String,
    image_validated: bool,
}

#[derive(Debug, Clone, Copy, Default)]
struct TotalsDelta {
    entries: i128,
    bytes: i128,
    pinned_bytes: i128,
    pinned_entries: i128,
}

impl TotalsDelta {
    fn transition(before: Option<(u64, bool)>, after: Option<(u64, bool)>) -> Self {
        let contribution = |value: Option<(u64, bool)>| match value {
            Some((size, pinned)) => (
                1_i128,
                i128::from(size),
                if pinned { i128::from(size) } else { 0 },
                if pinned { 1_i128 } else { 0 },
            ),
            None => (0, 0, 0, 0),
        };
        let first = contribution(before);
        let second = contribution(after);
        Self {
            entries: second.0 - first.0,
            bytes: second.1 - first.1,
            pinned_bytes: second.2 - first.2,
            pinned_entries: second.3 - first.3,
        }
    }
}

#[derive(Debug, Clone)]
enum MetadataKey {
    Entry(String),
    External(PathBuf),
}

struct CacheState {
    database: Connection,
    config: CacheConfig,
    entries: HashMap<String, Entry>,
    external: HashMap<PathBuf, Validation>,
    metadata_order: VecDeque<MetadataKey>,
    access_dirty: HashMap<String, u64>,
    access_clock: u64,
    last_access_flush: Instant,
    reservations: HashMap<u64, Reservation>,
    leases: HashMap<String, u64>,
    stats: CacheStats,
}

#[derive(Default)]
struct LockTickets {
    next: u64,
    serving: u64,
}

/// A state owner releases the value mutex before handing its ticket to the
/// next queued caller. A maintenance batch cannot reacquire ahead of a reader,
/// pin, publication, stats request, or destructor that is already waiting.
struct FairMutex<T> {
    value: Mutex<T>,
    tickets: Mutex<LockTickets>,
    ready: Condvar,
}

struct FairMutexGuard<'a, T> {
    owner: &'a FairMutex<T>,
    value: Option<MutexGuard<'a, T>>,
    ticket: u64,
}

impl<T> FairMutex<T> {
    fn new(value: T) -> Self {
        Self {
            value: Mutex::new(value),
            tickets: Mutex::new(LockTickets::default()),
            ready: Condvar::new(),
        }
    }

    fn lock(&self) -> LockResult<FairMutexGuard<'_, T>> {
        let ticket = {
            let mut queue = self.tickets.lock().unwrap_or_else(PoisonError::into_inner);
            let ticket = queue.next;
            queue.next = queue.next.wrapping_add(1);
            while queue.serving != ticket {
                queue = self
                    .ready
                    .wait(queue)
                    .unwrap_or_else(PoisonError::into_inner);
            }
            ticket
        };
        match self.value.lock() {
            Ok(value) => Ok(FairMutexGuard {
                owner: self,
                value: Some(value),
                ticket,
            }),
            Err(error) => Err(PoisonError::new(FairMutexGuard {
                owner: self,
                value: Some(error.into_inner()),
                ticket,
            })),
        }
    }

    fn get_mut(&mut self) -> LockResult<&mut T> {
        self.value.get_mut()
    }
}

impl<T> Deref for FairMutexGuard<'_, T> {
    type Target = T;
    fn deref(&self) -> &T {
        self.value
            .as_deref()
            .expect("The cache state guard is held")
    }
}

impl<T> DerefMut for FairMutexGuard<'_, T> {
    fn deref_mut(&mut self) -> &mut T {
        self.value
            .as_deref_mut()
            .expect("The cache state guard is held")
    }
}

impl<T> Drop for FairMutexGuard<'_, T> {
    fn drop(&mut self) {
        drop(self.value.take());
        let wake = {
            let mut queue = self
                .owner
                .tickets
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            debug_assert_eq!(queue.serving, self.ticket);
            queue.serving = queue.serving.wrapping_add(1);
            queue.serving != queue.next
        };
        if wake {
            self.owner.ready.notify_all();
        }
    }
}

struct CacheInner {
    root: PathBuf,
    state: FairMutex<CacheState>,
}

#[derive(Clone)]
pub struct CacheManager {
    inner: Arc<CacheInner>,
}

impl std::fmt::Debug for CacheManager {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CacheManager")
            .field("root", &self.inner.root)
            .finish_non_exhaustive()
    }
}

#[derive(Debug)]
pub struct CacheWrite {
    cache: CacheManager,
    id: u64,
    temp: PathBuf,
    finished: bool,
}

#[derive(Debug)]
pub struct CacheLease {
    cache: CacheManager,
    id: Option<String>,
}

impl CacheManager {
    pub fn open(data_dir: impl AsRef<Path>) -> Result<Self> {
        Self::open_inner(data_dir.as_ref(), None)
    }

    pub fn open_with_config(data_dir: impl AsRef<Path>, config: CacheConfig) -> Result<Self> {
        Self::open_inner(data_dir.as_ref(), Some(config))
    }

    /// The canonical shared cache root isolates in-memory artifact identities.
    pub fn namespace(&self) -> &Path {
        &self.inner.root
    }

    /// Query filesystem capacity only from a worker, never from UI projection.
    /// No cache state lock is held during the platform filesystem call.
    pub fn available_bytes(&self) -> Result<u64> {
        available_bytes(&self.inner.root)
    }

    /// Copy at most 128 indexed artifacts after the last returned ID. Each page
    /// is a separate snapshot; concurrent publications can change later pages.
    /// Invalid or missing artifacts remain visible for real file accounting.
    pub fn storage_artifacts_page(
        &self,
        after: Option<&str>,
        limit: usize,
    ) -> Result<Vec<StorageArtifact>> {
        let limit = limit.min(STORAGE_PAGE_LIMIT);
        if limit == 0 {
            return Ok(Vec::new());
        }
        let rows = {
            let state = self.lock()?;
            let mut statement = state.database.prepare(
                "SELECT a.id,a.kind,a.cache_key,
                        a.kind='raw' AND EXISTS(SELECT 1 FROM pins WHERE raw_key=a.cache_key),
                        CASE WHEN EXISTS(SELECT 1 FROM aliases WHERE artifact_id=a.id AND cover=1)
                             THEN 1
                             WHEN EXISTS(SELECT 1 FROM aliases WHERE artifact_id=a.id AND cover IS NULL)
                             THEN NULL ELSE 0 END
                 FROM artifacts AS a WHERE a.id>?1 ORDER BY a.id LIMIT ?2",
            )?;
            statement
                .query_map(params![after.unwrap_or_default(), limit as u64], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, bool>(3)?,
                        row.get::<_, Option<bool>>(4)?,
                    ))
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?
        };
        rows.into_iter()
            .map(|(id, kind, key, pinned, cover)| {
                let kind = Kind::parse(&kind)?;
                ensure!(
                    valid_key(&key) && id == cache_id(kind, &key),
                    "The cache index contains an invalid artifact identity"
                );
                // generated_path() validates the filesystem. Storage accounting
                // intentionally defers every such operation to its worker.
                let path = self
                    .inner
                    .root
                    .join(kind.name())
                    .join(&key[..2])
                    .join(format!("{key}.{}", kind.extension()));
                Ok(StorageArtifact {
                    id,
                    path,
                    pinned,
                    cover,
                })
            })
            .collect()
    }

    fn open_inner(data_dir: &Path, config: Option<CacheConfig>) -> Result<Self> {
        fs::create_dir_all(data_dir).context("Create the application cache parent")?;
        let data_dir =
            fs::canonicalize(data_dir).context("Resolve the application cache parent")?;
        let registry = REGISTRY.get_or_init(|| Mutex::new(HashMap::new()));
        let mut registry = registry
            .lock()
            .map_err(|_| anyhow!("The disk cache registry is unavailable"))?;
        registry.retain(|_, value| value.strong_count() > 0);
        if let Some(inner) = registry.get(&data_dir).and_then(Weak::upgrade) {
            let manager = Self { inner };
            if let Some(config) = config {
                manager.set_config(config)?;
            }
            return Ok(manager);
        }
        let requested_root = data_dir.join("cache/shared-v1");
        fs::create_dir_all(&requested_root).context("Create the shared artifact cache")?;
        let root =
            fs::canonicalize(&requested_root).context("Resolve the shared artifact cache")?;
        ensure!(
            root.starts_with(&data_dir),
            "The cache root escapes its application directory"
        );
        for directory in ["raw", "processed", "temp"] {
            let path = root.join(directory);
            fs::create_dir_all(&path).context("Create an artifact cache directory")?;
            ensure!(
                fs::canonicalize(&path)? == path,
                "An artifact cache directory is a symbolic link"
            );
        }
        let index = root.join("index.sqlite3");
        if let Ok(metadata) = fs::symlink_metadata(&index) {
            ensure!(
                metadata.is_file() && !metadata.file_type().is_symlink(),
                "The cache index must be a regular file"
            );
        }
        let mut database = Connection::open(&index).context("Open the shared cache index")?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&index, fs::Permissions::from_mode(0o600))
                .context("Restrict the cache index permissions")?;
        }
        database.busy_timeout(Duration::from_secs(5))?;
        database
            .execute_batch(
                "PRAGMA journal_mode=DELETE;
             PRAGMA synchronous=FULL;
             PRAGMA foreign_keys=ON;
             PRAGMA mmap_size=0;
             CREATE TABLE IF NOT EXISTS artifacts (
                 id TEXT PRIMARY KEY, kind TEXT NOT NULL, cache_key TEXT NOT NULL,
                 size INTEGER NOT NULL CHECK(size >= 0), checksum TEXT NOT NULL,
                 fingerprint TEXT NOT NULL, last_access INTEGER NOT NULL,
                 valid INTEGER NOT NULL DEFAULT 1
             );
             CREATE INDEX IF NOT EXISTS artifact_lru ON artifacts(last_access);
             CREATE TABLE IF NOT EXISTS aliases (
                 alias_key TEXT PRIMARY KEY,
                 artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
                 cover INTEGER CHECK(cover IN (0,1))
             );
             CREATE INDEX IF NOT EXISTS alias_artifact ON aliases(artifact_id);
             CREATE TABLE IF NOT EXISTS pins (
                 raw_key TEXT NOT NULL, owner TEXT NOT NULL,
                 process_id INTEGER NOT NULL DEFAULT 0,
                 PRIMARY KEY(raw_key, owner)
             );
             CREATE INDEX IF NOT EXISTS pin_owner ON pins(owner);",
            )
            .context("Initialize the shared cache index")?;
        initialize_alias_classification(&mut database)?;
        let pin_columns = {
            let mut statement = database.prepare("PRAGMA table_info(pins)")?;
            statement
                .query_map([], |row| row.get::<_, String>(1))?
                .collect::<rusqlite::Result<Vec<_>>>()?
        };
        if !pin_columns.iter().any(|name| name == "process_id") {
            database.execute(
                "ALTER TABLE pins ADD COLUMN process_id INTEGER NOT NULL DEFAULT 0",
                [],
            )?;
        }
        let access_clock = database.query_row(
            "SELECT COALESCE(MAX(last_access), 0) FROM artifacts",
            [],
            |row| row.get::<_, u64>(0),
        )?;
        let mut state = CacheState {
            database,
            config: config.unwrap_or_default(),
            entries: HashMap::new(),
            external: HashMap::new(),
            metadata_order: VecDeque::new(),
            access_dirty: HashMap::new(),
            access_clock,
            last_access_flush: Instant::now(),
            reservations: HashMap::new(),
            leases: HashMap::new(),
            stats: CacheStats::default(),
        };
        reconcile(&root, &mut state)?;
        state.refresh_totals()?;
        prune_budget(&root, &mut state)?;
        let inner = Arc::new(CacheInner {
            root,
            state: FairMutex::new(state),
        });
        registry.insert(data_dir, Arc::downgrade(&inner));
        Ok(Self { inner })
    }

    /// Explicit policy updates never reset an existing manager through open().
    /// Pruning is deferred until prune() or the next reservation.
    pub fn set_config(&self, config: CacheConfig) -> Result<()> {
        self.lock()?.config = config;
        Ok(())
    }

    /// Remote identity is URL plus canonical HTTP headers. URL-less local files
    /// include their canonical path and metadata fingerprint.
    pub fn page_key(page: &Page) -> String {
        let mut hash = Sha256::new();
        hash_part(&mut hash, page.url.as_bytes());
        let mut headers = page
            .headers
            .iter()
            .map(|(name, value)| (name.to_ascii_lowercase(), value))
            .collect::<Vec<_>>();
        headers.sort_by(|first, second| first.0.cmp(&second.0).then_with(|| first.1.cmp(second.1)));
        for (name, value) in headers {
            hash_part(&mut hash, name.as_bytes());
            hash_part(&mut hash, value.as_bytes());
        }
        if let Some(path) = page.local_path.as_ref().filter(|_| page.url.is_empty()) {
            let canonical = fs::canonicalize(path).unwrap_or_else(|_| absolute_path(path));
            hash_path(&mut hash, &canonical);
            if let Ok(metadata) = fs::metadata(&canonical) {
                hash_part(&mut hash, metadata_fingerprint(&metadata).as_bytes());
            }
        }
        format!("{:x}", hash.finalize())
    }

    pub fn lookup_raw(&self, page: &Page) -> Result<Option<PathBuf>> {
        if let Some(path) = &page.local_path {
            if self.managed_id(path).is_none() && path.is_file() {
                self.validate_path(path)?;
                let canonical = fs::canonicalize(path).context("Resolve the local comic page")?;
                let mut state = self.lock()?;
                state.stats.hits += 1;
                state.stats.raw_hits += 1;
                state.stats.external_hits += 1;
                return Ok(Some(canonical));
            }
        }
        self.lookup(Kind::Raw, &self.raw_key(page))
    }

    pub fn begin_raw(
        &self,
        page: &Page,
        pin_owner: Option<&str>,
        expected_bytes: u64,
    ) -> Result<CacheWrite> {
        self.begin(Kind::Raw, self.raw_key(page), pin_owner, expected_bytes)
    }

    pub fn lookup_processed(&self, key: &RenderKey) -> Result<Option<PathBuf>> {
        self.lookup(Kind::Processed, &render_key(key)?)
    }

    /// Return the path and checksum from the same validated artifact snapshot.
    /// Callers needing retention must also hold a short-lived cache lease.
    pub fn lookup_processed_with_fingerprint(
        &self,
        key: &RenderKey,
    ) -> Result<Option<(PathBuf, String)>> {
        self.lookup_with_fingerprint(Kind::Processed, &render_key(key)?)
    }

    pub fn begin_processed(&self, key: &RenderKey, expected_bytes: u64) -> Result<CacheWrite> {
        self.begin(Kind::Processed, render_key(key)?, None, expected_bytes)
    }

    /// Alias names are hashed before persistence because a cover URI can contain
    /// credentials. Aliases grant no extra retention beyond the artifact itself.
    pub fn set_alias(&self, alias: &str, path: &Path) -> Result<()> {
        let canonical = fs::canonicalize(path).context("Resolve an aliased cache artifact")?;
        let (kind, key) = self
            .managed_id(&canonical)
            .context("An alias must refer to a managed cache artifact")?;
        let _lease = self.lease_path(&canonical)?;
        ensure!(
            self.lookup(kind, &key)?.is_some(),
            "An alias cannot refer to an invalid cache artifact"
        );
        self.lock()?.database.execute(
            "INSERT INTO aliases(alias_key,artifact_id,cover) VALUES(?1,?2,?3)
             ON CONFLICT(alias_key) DO UPDATE SET artifact_id=excluded.artifact_id,cover=excluded.cover
             WHERE aliases.artifact_id<>excluded.artifact_id OR aliases.cover IS NOT excluded.cover",
            params![
                alias_key(alias),
                cache_id(kind, &key),
                alias_is_cover(alias),
            ],
        )?;
        Ok(())
    }

    pub fn lookup_alias(&self, alias: &str) -> Result<Option<PathBuf>> {
        let artifact = {
            let state = self.lock()?;
            let alias_hash = alias_key(alias);
            let artifact = state.database.query_row(
                "SELECT artifacts.kind,artifacts.cache_key,aliases.cover FROM aliases JOIN artifacts ON artifacts.id=aliases.artifact_id
                 WHERE aliases.alias_key=?1 AND artifacts.valid=1",
                [&alias_hash], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, Option<bool>>(2)?)),
            ).optional()?;
            if artifact
                .as_ref()
                .is_some_and(|(_, _, cover)| cover.is_none())
            {
                // A supplied original name can classify a historical hash once.
                // Known warm aliases take only the existing read path.
                state.database.execute(
                    "UPDATE aliases SET cover=?1 WHERE alias_key=?2 AND cover IS NULL",
                    params![alias_is_cover(alias), alias_hash],
                )?;
            }
            artifact.map(|(kind, key, _)| (kind, key))
        };
        let Some((kind, key)) = artifact else {
            return Ok(None);
        };
        let kind = Kind::parse(&kind)?;
        let path = generated_path(&self.inner.root, kind, &key, false)?;
        let Ok(_lease) = self.lease_path(&path) else {
            return Ok(None);
        };
        self.lookup(kind, &key)
    }

    pub fn invalidate_processed(&self, key: &RenderKey) -> Result<()> {
        let entry = self
            .lock()?
            .load_entry(&cache_id(Kind::Processed, &render_key(key)?))?;
        if let Some(entry) = entry {
            self.invalidate(&entry)?;
        }
        Ok(())
    }

    pub fn invalidate_raw(&self, page: &Page) -> Result<()> {
        let entry = self
            .lock()?
            .load_entry(&cache_id(Kind::Raw, &self.raw_key(page)))?;
        if let Some(entry) = entry {
            self.invalidate(&entry)?;
        }
        Ok(())
    }

    fn begin(
        &self,
        kind: Kind,
        key: String,
        owner: Option<&str>,
        expected: u64,
    ) -> Result<CacheWrite> {
        if let Some(owner) = owner {
            ensure!(
                !owner.is_empty() && owner.len() <= 4096,
                "A cache pin owner must contain 1 to 4096 bytes"
            );
        }
        let id = NEXT_WRITE.fetch_add(1, Ordering::Relaxed);
        let temp = self
            .inner
            .root
            .join("temp")
            .join(format!("{}-{id}-{key}.part", std::process::id()));
        let reservation = Reservation {
            id: cache_id(kind, &key),
            kind,
            key,
            target: expected,
            owner: owner.map(str::to_owned),
            temp: temp.clone(),
        };
        let mut state = self.lock()?;
        state.reservations.insert(id, reservation);
        if let Err(error) = self.ensure_capacity_locked(&mut state) {
            state.reservations.remove(&id);
            return Err(error);
        }
        let creation = (|| -> Result<()> {
            ensure_safe_directory(&self.inner.root.join("temp"))?;
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temp)
                .context("Create a unique cache staging file")?;
            Ok(())
        })();
        if let Err(error) = creation {
            state.reservations.remove(&id);
            state.update_reservations();
            return Err(error);
        }
        state.update_reservations();
        Ok(CacheWrite {
            cache: self.clone(),
            id,
            temp,
            finished: false,
        })
    }

    fn lookup(&self, kind: Kind, key: &str) -> Result<Option<PathBuf>> {
        Ok(self
            .lookup_with_fingerprint(kind, key)?
            .map(|(path, _)| path))
    }

    fn lookup_with_fingerprint(&self, kind: Kind, key: &str) -> Result<Option<(PathBuf, String)>> {
        let id = cache_id(kind, key);
        for _ in 0..2 {
            let entry = { self.lock()?.load_entry(&id)? };
            let Some(mut entry) = entry else {
                self.record_miss(kind)?;
                return Ok(None);
            };
            if !entry.valid {
                self.record_miss(kind)?;
                return Ok(None);
            }
            let path = generated_path(&self.inner.root, kind, key, false)?;
            let fingerprint = match safe_file_fingerprint(&self.inner.root, &path) {
                Ok(Some(value)) => value,
                Ok(None) => {
                    self.invalidate(&entry)?;
                    self.record_miss(kind)?;
                    return Ok(None);
                }
                Err(error) => {
                    self.record_miss(kind)?;
                    return Err(error);
                }
            };
            if entry.verified && fingerprint == entry.fingerprint {
                let mut state = self.lock()?;
                if state
                    .load_entry(&id)?
                    .is_some_and(|current| current.checksum == entry.checksum && current.valid)
                {
                    state.touch(&id)?;
                    record_hit(&mut state.stats, kind);
                    return Ok(Some((path, entry.checksum)));
                }
                continue;
            }
            let (checksum, size) = checksum_file(&path)?;
            let after = safe_file_fingerprint(&self.inner.root, &path)?;
            if after.as_deref() != Some(&fingerprint) {
                continue;
            }
            let mut state = self.lock()?;
            state.stats.checksum_bytes = state.stats.checksum_bytes.saturating_add(size);
            if !state
                .load_entry(&id)?
                .is_some_and(|current| current.checksum == entry.checksum && current.valid)
            {
                continue;
            }
            if checksum != entry.checksum || size != entry.size {
                drop(state);
                self.invalidate(&entry)?;
                self.record_miss(kind)?;
                return Ok(None);
            }
            entry.fingerprint = fingerprint;
            entry.verified = true;
            state.remember_entry(entry);
            state.touch(&id)?;
            record_hit(&mut state.stats, kind);
            return Ok(Some((path, checksum)));
        }
        self.record_miss(kind)?;
        Ok(None)
    }

    pub fn pin_raw(&self, page: &Page, owner: &str) -> Result<()> {
        ensure!(
            !owner.is_empty() && owner.len() <= 4096,
            "A cache pin owner must contain 1 to 4096 bytes"
        );
        if let Some(path) = &page.local_path {
            if self.managed_id(path).is_none() && path.is_file() {
                return self.validate_path(path);
            }
        }
        let key = self.raw_key(page);
        let process_id = pin_process_id(owner);
        let mut state = self.lock()?;
        let existing = state
            .database
            .query_row(
                "SELECT process_id FROM pins WHERE raw_key=?1 AND owner=?2",
                params![key, owner],
                |row| row.get::<_, u32>(0),
            )
            .optional()?;
        if existing == Some(process_id) {
            return Ok(());
        }
        let delta = {
            let transaction = state
                .database
                .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let current = transaction
                .query_row(
                    "SELECT process_id FROM pins WHERE raw_key=?1 AND owner=?2",
                    params![key, owner],
                    |row| row.get::<_, u32>(0),
                )
                .optional()?;
            if current == Some(process_id) {
                return Ok(());
            }
            let was_pinned = transaction.query_row(
                "SELECT EXISTS(SELECT 1 FROM pins WHERE raw_key=?1)",
                [&key],
                |row| row.get::<_, bool>(0),
            )?;
            let size = if was_pinned {
                None
            } else {
                transaction
                    .query_row(
                        "SELECT size FROM artifacts WHERE id=?1",
                        [cache_id(Kind::Raw, &key)],
                        |row| row.get::<_, u64>(0),
                    )
                    .optional()?
            };
            transaction.execute(
                "INSERT INTO pins(raw_key,owner,process_id) VALUES(?1,?2,?3)
                 ON CONFLICT(raw_key,owner) DO UPDATE SET process_id=excluded.process_id",
                params![key, owner, process_id],
            )?;
            transaction
                .commit()
                .context("Protect the downloaded cache artifact")?;
            size.map(|size| TotalsDelta {
                pinned_bytes: i128::from(size),
                pinned_entries: 1,
                ..TotalsDelta::default()
            })
            .unwrap_or_default()
        };
        state.apply_delta(delta)
    }

    pub fn unpin_owner(&self, owner: &str) -> Result<()> {
        self.unpin_owners(&[owner.to_owned()])
    }

    pub fn unpin_owners(&self, owners: &[String]) -> Result<()> {
        if owners.is_empty() {
            return Ok(());
        }
        let owners_set = owners.iter().map(String::as_str).collect::<HashSet<_>>();
        let mut state = self.lock()?;
        let delta = {
            let transaction = state
                .database
                .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let mut delta = TotalsDelta::default();
            {
                let mut statement = transaction.prepare("DELETE FROM pins WHERE owner=?1")?;
                let mut contribution = transaction.prepare(
                    "SELECT COALESCE(SUM(a.size),0),COUNT(a.id) FROM pins AS p INDEXED BY pin_owner
                     JOIN artifacts AS a ON a.id='raw:'||p.raw_key WHERE p.owner=?1 AND NOT EXISTS(
                         SELECT 1 FROM pins AS remaining WHERE remaining.raw_key=p.raw_key AND remaining.owner<>p.owner)",
                )?;
                for owner in owners_set.iter().copied() {
                    let (bytes, entries) = contribution.query_row([owner], |row| {
                        Ok((row.get::<_, u64>(0)?, row.get::<_, u64>(1)?))
                    })?;
                    statement.execute([owner])?;
                    delta.pinned_bytes -= i128::from(bytes);
                    delta.pinned_entries -= i128::from(entries);
                }
            }
            transaction
                .commit()
                .context("Release the download cache pins")?;
            delta
        };
        for value in state.reservations.values_mut() {
            if value
                .owner
                .as_deref()
                .is_some_and(|owner| owners_set.contains(owner))
            {
                value.owner = None;
            }
        }
        state.apply_delta(delta)?;
        // Releasing protection must succeed even when a low-space policy is
        // currently unsatisfiable. Reclaim only the newly unpinned cache budget.
        prune_budget(&self.inner.root, &mut state)?;
        Ok(())
    }

    pub fn clear_unpinned(&self) -> Result<CacheStats> {
        let cutoff = {
            let mut state = self.lock()?;
            state.flush_access(true)?;
            state.access_clock
        };
        loop {
            let removed = {
                let mut state = self.lock()?;
                evict_batch(&self.inner.root, &mut state, CLEAR_BATCH, Some(cutoff))?
            };
            if removed == 0 {
                break;
            }
            std::thread::yield_now();
        }
        Ok(self.stats())
    }

    /// Explicit image validation is separate from raw lookup: raw artifacts may
    /// be validated opaque ZIP archives supplied by the source host.
    pub fn validate_path(&self, path: &Path) -> Result<()> {
        let canonical = fs::canonicalize(path).context("Resolve an image for validation")?;
        if let Some((kind, key)) = self.managed_id(path) {
            ensure!(
                self.lookup(kind, &key)?.is_some(),
                "The cached image is missing or its checksum changed"
            );
        }
        let fingerprint = metadata_fingerprint(&fs::metadata(&canonical)?);
        {
            let state = self.lock()?;
            if state
                .external
                .get(&canonical)
                .is_some_and(|value| value.fingerprint == fingerprint && value.image_validated)
            {
                return Ok(());
            }
        }
        crate::imaging::validate_file(&canonical).context("Validate a local comic image")?;
        let (checksum, size) = checksum_file(&canonical)?;
        ensure!(
            metadata_fingerprint(&fs::metadata(&canonical)?) == fingerprint,
            "The local image changed during validation"
        );
        let mut state = self.lock()?;
        state.stats.external_validations += 1;
        state.stats.checksum_bytes = state.stats.checksum_bytes.saturating_add(size);
        state.remember_external(
            canonical,
            Validation {
                fingerprint,
                checksum,
                image_validated: true,
            },
        );
        Ok(())
    }

    pub fn fingerprint(&self, path: &Path) -> Result<String> {
        if let Some((kind, key)) = self.managed_id(path) {
            if self.lookup(kind, &key)?.is_some() {
                if let Some(entry) = self.lock()?.load_entry(&cache_id(kind, &key))? {
                    return Ok(entry.checksum);
                }
            }
            bail!("The cached source is missing or invalid");
        }
        let canonical = fs::canonicalize(path).context("Resolve a source fingerprint")?;
        let fingerprint = metadata_fingerprint(&fs::metadata(&canonical)?);
        if let Some(value) = self
            .lock()?
            .external
            .get(&canonical)
            .filter(|value| value.fingerprint == fingerprint)
        {
            return Ok(value.checksum.clone());
        }
        let (checksum, size) = checksum_file(&canonical)?;
        ensure!(
            metadata_fingerprint(&fs::metadata(&canonical)?) == fingerprint,
            "The source changed during fingerprinting"
        );
        let mut state = self.lock()?;
        state.stats.checksum_bytes = state.stats.checksum_bytes.saturating_add(size);
        state.remember_external(
            canonical,
            Validation {
                fingerprint,
                checksum: checksum.clone(),
                image_validated: false,
            },
        );
        Ok(checksum)
    }

    pub fn stats(&self) -> CacheStats {
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut stats = state.stats.clone();
        stats.reserved_bytes = state.reservations.values().map(|value| value.target).sum();
        stats.writes_inflight = state.reservations.len() as u64;
        stats.metadata_entries = (state.entries.len() + state.external.len()) as u64;
        stats.leased_entries = state.leases.len() as u64;
        stats
    }

    pub fn lease_path(&self, path: &Path) -> Result<CacheLease> {
        let id = if let Some((kind, key)) = self.managed_id(path) {
            safe_file_fingerprint(&self.inner.root, path)?
                .context("The leased cache artifact is missing")?;
            let id = cache_id(kind, &key);
            let mut state = self.lock()?;
            ensure!(
                state.load_entry(&id)?.is_some_and(|entry| entry.valid),
                "The leased cache artifact is not valid"
            );
            *state.leases.entry(id.clone()).or_default() += 1;
            Some(id)
        } else {
            ensure!(path.is_file(), "The leased source file is missing");
            None
        };
        Ok(CacheLease {
            cache: self.clone(),
            id,
        })
    }

    pub fn prune(&self) -> Result<()> {
        let mut state = self.lock()?;
        state.flush_access(true)?;
        self.ensure_capacity_locked(&mut state)
    }

    fn ensure_capacity_locked(&self, state: &mut CacheState) -> Result<()> {
        let mut flushed = false;
        loop {
            let (projected, remaining) = projected_usage(state)?;
            let free = available_bytes(&self.inner.root)?;
            let required = state.config.min_free_bytes.saturating_add(remaining);
            if projected <= state.config.max_unpinned_bytes && free >= required {
                return Ok(());
            }
            if !flushed {
                // Decide whether the reservation is attainable before deleting
                // any committed data. Pins, leases, and in-flight replacements
                // cannot contribute space to this plan. Filesystem capacity is
                // still a snapshot, not an atomic promise against other writers.
                let (reclaimable_budget, reclaimable_disk) =
                    reclaimable_space(&self.inner.root, state)?;
                ensure!(
                    projected.saturating_sub(state.config.max_unpinned_bytes) <= reclaimable_budget
                        && required.saturating_sub(free) <= reclaimable_disk,
                    "The artifact cache cannot reserve enough space without deleting pinned or leased downloads"
                );
                state.flush_access(true)?;
                flushed = true;
            }
            if !evict_one(&self.inner.root, state)? {
                bail!(
                    "The artifact cache cannot reserve enough space without deleting pinned downloads"
                );
            }
        }
    }

    fn invalidate(&self, entry: &Entry) -> Result<()> {
        let mut state = self.lock()?;
        if state.access_dirty.contains_key(&entry.id)
            && state.entries.get(&entry.id).is_some_and(|current| {
                current.checksum == entry.checksum && current.fingerprint == entry.fingerprint
            })
        {
            state.flush_access(true)?;
        }
        let writing = state
            .reservations
            .values()
            .any(|value| value.id == entry.id);
        let leased = state.leases.contains_key(&entry.id);
        let (removed, size) = {
            let transaction = state
                .database
                .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
            let current = transaction.query_row(
                "SELECT size,valid FROM artifacts WHERE id=?1 AND checksum=?2 AND fingerprint=?3",
                params![entry.id, entry.checksum, entry.fingerprint], |row| Ok((row.get::<_, u64>(0)?, row.get::<_, bool>(1)?)),
            ).optional()?;
            let Some((size, valid)) = current else {
                return Ok(());
            };
            let pinned = entry.kind == Kind::Raw
                && transaction.query_row(
                    "SELECT EXISTS(SELECT 1 FROM pins WHERE raw_key=?1)",
                    [&entry.key],
                    |row| row.get::<_, bool>(0),
                )?;
            let removed = !pinned && !writing && !leased;
            if removed {
                let path = generated_path(&self.inner.root, entry.kind, &entry.key, false)?;
                remove_generated(&self.inner.root, &path)?;
                let changed = transaction.execute(
                    "DELETE FROM artifacts WHERE id=?1 AND checksum=?2 AND fingerprint=?3",
                    params![entry.id, entry.checksum, entry.fingerprint],
                )?;
                ensure!(
                    changed == 1,
                    "The invalid cache artifact changed before removal"
                );
            } else if valid {
                transaction.execute("UPDATE artifacts SET valid=0 WHERE id=?1", [&entry.id])?;
            }
            transaction
                .commit()
                .context("Discard the invalid cache artifact")?;
            (removed, size)
        };
        state.stats.corruptions += 1;
        if !removed {
            if let Some(value) = state.entries.get_mut(&entry.id) {
                value.valid = false;
                value.verified = false;
            }
        } else {
            state.entries.remove(&entry.id);
            state.access_dirty.remove(&entry.id);
            state.apply_delta(TotalsDelta::transition(Some((size, false)), None))?;
        }
        Ok(())
    }

    fn record_miss(&self, kind: Kind) -> Result<()> {
        let mut state = self.lock()?;
        state.stats.misses += 1;
        match kind {
            Kind::Raw => state.stats.raw_misses += 1,
            Kind::Processed => state.stats.processed_misses += 1,
        }
        Ok(())
    }

    fn managed_id(&self, path: &Path) -> Option<(Kind, String)> {
        let relative = path.strip_prefix(&self.inner.root).ok()?;
        let parts = relative
            .components()
            .map(|part| part.as_os_str().to_str())
            .collect::<Option<Vec<_>>>()?;
        if parts.len() != 3 {
            return None;
        }
        let kind = Kind::parse(parts[0]).ok()?;
        let key = parts[2].strip_suffix(&format!(".{}", kind.extension()))?;
        if valid_key(key) && parts[1] == &key[..2] {
            Some((kind, key.to_owned()))
        } else {
            None
        }
    }

    fn raw_key(&self, page: &Page) -> String {
        if page.url.is_empty() {
            if let Some((Kind::Raw, key)) = page
                .local_path
                .as_ref()
                .and_then(|path| self.managed_id(path))
            {
                return key;
            }
        }
        Self::page_key(page)
    }

    fn lock(&self) -> Result<FairMutexGuard<'_, CacheState>> {
        self.inner
            .state
            .lock()
            .map_err(|_| anyhow!("The shared disk cache state is unavailable"))
    }
}

impl CacheWrite {
    pub fn temp_path(&self) -> &Path {
        &self.temp
    }

    /// Reserve the complete size after the next stream chunk, not just its delta.
    pub fn ensure_capacity(&mut self, total_bytes: u64) -> Result<()> {
        let mut state = self.cache.lock()?;
        let reservation = state
            .reservations
            .get_mut(&self.id)
            .context("The cache write has expired")?;
        let previous = reservation.target;
        reservation.target = reservation.target.max(total_bytes);
        if let Err(error) = self.cache.ensure_capacity_locked(&mut state) {
            if let Some(value) = state.reservations.get_mut(&self.id) {
                value.target = previous;
            }
            return Err(error);
        }
        state.update_reservations();
        Ok(())
    }

    /// The caller validates the artifact format before committing. Raw storage
    /// intentionally also accepts checked archives, while this boundary verifies
    /// bytes, synchronizes them, and atomically publishes the generated leaf.
    pub fn commit(mut self) -> Result<PathBuf> {
        ensure_safe_directory(&self.cache.inner.root.join("temp"))?;
        let metadata =
            fs::symlink_metadata(&self.temp).context("Read the staged cache artifact")?;
        ensure!(
            metadata.is_file() && !metadata.file_type().is_symlink(),
            "The staged artifact is not a regular file"
        );
        ensure!(metadata.len() > 0, "An empty artifact cannot be committed");
        {
            let mut state = self.cache.lock()?;
            state
                .reservations
                .get_mut(&self.id)
                .context("The cache write has expired")?
                .target = metadata.len();
            self.cache.ensure_capacity_locked(&mut state)?;
            state.update_reservations();
        }
        let file = OpenOptions::new().read(true).write(true).open(&self.temp)?;
        file.sync_all()
            .context("Synchronize the staged cache artifact")?;
        drop(file);
        let staged_path =
            fs::canonicalize(&self.temp).context("Resolve the staged cache artifact")?;
        ensure!(
            staged_path == self.temp,
            "The staged cache artifact was replaced by a symbolic link"
        );
        let staged_metadata = fs::symlink_metadata(&staged_path)?;
        ensure!(
            staged_metadata.is_file() && !staged_metadata.file_type().is_symlink(),
            "The staged artifact is not a regular file"
        );
        let staged_fingerprint = metadata_fingerprint(&staged_metadata);
        let (checksum, size) = checksum_file(&self.temp)?;
        ensure!(
            metadata_fingerprint(&fs::symlink_metadata(&self.temp)?) == staged_fingerprint,
            "The staged artifact changed during its checksum"
        );
        let mut state = self.cache.lock()?;
        let image_proof = state.external.get(&staged_path).is_some_and(|memo| {
            memo.image_validated
                && memo.fingerprint == staged_fingerprint
                && memo.checksum == checksum
        });
        let reservation = state
            .reservations
            .get(&self.id)
            .cloned()
            .context("The cache write has expired")?;
        self.cache.ensure_capacity_locked(&mut state)?;
        let destination = generated_path(
            &self.cache.inner.root,
            reservation.kind,
            &reservation.key,
            true,
        )?;
        let access = state.next_access();
        let CacheState {
            database,
            entries,
            access_dirty,
            ..
        } = &mut *state;
        let transaction =
            database.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        // Keep access hints pending until this publication commits. A rollback
        // must preserve them for the next flush, including replacement entries.
        persist_access(&transaction, access_dirty, entries)?;
        let before = transaction.query_row(
            "SELECT size,kind='raw' AND EXISTS(SELECT 1 FROM pins WHERE raw_key=artifacts.cache_key)
             FROM artifacts WHERE id=?1", [&reservation.id], |row| Ok((row.get::<_, u64>(0)?, row.get::<_, bool>(1)?)),
        ).optional()?;
        ensure!(
            metadata_fingerprint(&fs::symlink_metadata(&self.temp)?) == staged_fingerprint,
            "The staged artifact changed before publication"
        );
        atomic_replace(&self.temp, &destination).context("Publish the cached artifact")?;
        sync_directory(
            destination
                .parent()
                .context("The cache artifact has no directory")?,
        )?;
        let published_metadata = fs::metadata(&destination)?;
        let fingerprint = metadata_fingerprint(&published_metadata);
        let migrate_image_proof =
            image_proof && same_file_identity(&staged_metadata, &published_metadata);
        transaction.execute(
            "INSERT INTO artifacts(id,kind,cache_key,size,checksum,fingerprint,last_access,valid)
             VALUES(?1,?2,?3,?4,?5,?6,?7,1)
             ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,cache_key=excluded.cache_key,size=excluded.size,
             checksum=excluded.checksum,fingerprint=excluded.fingerprint,last_access=excluded.last_access,valid=1",
            params![reservation.id, reservation.kind.name(), reservation.key, size, checksum, fingerprint, access],
        )?;
        if let Some(owner) = &reservation.owner {
            transaction.execute(
                "INSERT INTO pins(raw_key,owner,process_id) VALUES(?1,?2,?3)
                 ON CONFLICT(raw_key,owner) DO UPDATE SET process_id=excluded.process_id",
                params![reservation.key, owner, pin_process_id(owner)],
            )?;
        }
        let pinned = reservation.kind == Kind::Raw
            && transaction.query_row(
                "SELECT EXISTS(SELECT 1 FROM pins WHERE raw_key=?1)",
                [&reservation.key],
                |row| row.get::<_, bool>(0),
            )?;
        transaction
            .commit()
            .context("Persist the cache artifact and download pins")?;
        state.access_dirty.clear();
        state.last_access_flush = Instant::now();
        state.reservations.remove(&self.id);
        state.external.remove(&staged_path);
        if migrate_image_proof {
            state.remember_external(
                destination.clone(),
                Validation {
                    fingerprint: fingerprint.clone(),
                    checksum: checksum.clone(),
                    image_validated: true,
                },
            );
        }
        state.remember_entry(Entry {
            id: reservation.id,
            kind: reservation.kind,
            key: reservation.key,
            size,
            checksum,
            fingerprint,
            access,
            valid: true,
            verified: true,
        });
        state.stats.commits += 1;
        state.stats.checksum_bytes = state.stats.checksum_bytes.saturating_add(size);
        state.apply_delta(TotalsDelta::transition(before, Some((size, pinned))))?;
        state.update_reservations();
        self.finished = true;
        Ok(destination)
    }
}

impl Drop for CacheWrite {
    fn drop(&mut self) {
        if self.finished {
            return;
        }
        if let Ok(mut state) = self.cache.inner.state.lock() {
            state.reservations.remove(&self.id);
            state.update_reservations();
            if let Err(error) = prune_budget(&self.cache.inner.root, &mut state) {
                eprintln!("aidoku: release cache reservation: {error:#}");
            }
        }
        if ensure_safe_directory(&self.cache.inner.root.join("temp")).is_ok() {
            if let Ok(metadata) = fs::symlink_metadata(&self.temp) {
                if metadata.is_file() && !metadata.file_type().is_symlink() {
                    let _ = fs::remove_file(&self.temp);
                }
            }
        }
    }
}

impl Drop for CacheLease {
    fn drop(&mut self) {
        let Some(id) = &self.id else {
            return;
        };
        if let Ok(mut state) = self.cache.inner.state.lock() {
            if let Some(count) = state.leases.get_mut(id) {
                *count = count.saturating_sub(1);
                if *count == 0 {
                    state.leases.remove(id);
                }
            }
            if let Err(error) = prune_budget(&self.cache.inner.root, &mut state) {
                eprintln!("aidoku: release cache lease: {error:#}");
            }
        }
    }
}

impl CacheState {
    fn apply_delta(&mut self, delta: TotalsDelta) -> Result<()> {
        let adjust = |current: u64, change: i128| -> Result<u64> {
            u64::try_from(i128::from(current) + change)
                .context("The cache totals delta is inconsistent")
        };
        let entries = adjust(self.stats.entries, delta.entries)?;
        let bytes = adjust(self.stats.bytes, delta.bytes)?;
        let pinned_bytes = adjust(self.stats.pinned_bytes, delta.pinned_bytes)?;
        let pinned_entries = adjust(self.stats.pinned_entries, delta.pinned_entries)?;
        let unpinned_bytes = bytes
            .checked_sub(pinned_bytes)
            .context("The cache pin totals exceed artifact bytes")?;
        self.stats.entries = entries;
        self.stats.bytes = bytes;
        self.stats.pinned_bytes = pinned_bytes;
        self.stats.pinned_entries = pinned_entries;
        self.stats.unpinned_bytes = unpinned_bytes;
        Ok(())
    }

    fn refresh_totals(&mut self) -> Result<()> {
        let (entries, bytes, pinned, pinned_entries) = self.database.query_row(
            "SELECT COUNT(*),COALESCE(SUM(size),0),COALESCE(SUM(CASE WHEN kind='raw' AND EXISTS(
                 SELECT 1 FROM pins WHERE raw_key=artifacts.cache_key) THEN size ELSE 0 END),0),
                 COALESCE(SUM(CASE WHEN kind='raw' AND EXISTS(SELECT 1 FROM pins WHERE raw_key=artifacts.cache_key) THEN 1 ELSE 0 END),0) FROM artifacts",
            [], |row| Ok((row.get::<_, u64>(0)?, row.get::<_, u64>(1)?, row.get::<_, u64>(2)?, row.get::<_, u64>(3)?)),
        )?;
        self.stats.entries = entries;
        self.stats.bytes = bytes;
        self.stats.pinned_bytes = pinned;
        self.stats.pinned_entries = pinned_entries;
        self.stats.unpinned_bytes = bytes.saturating_sub(pinned);
        Ok(())
    }

    fn pinned(&self, key: &str) -> Result<bool> {
        self.database
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM pins WHERE raw_key=?1)",
                [key],
                |row| row.get(0),
            )
            .map_err(Into::into)
    }

    fn load_entry(&mut self, id: &str) -> Result<Option<Entry>> {
        if let Some(entry) = self.entries.get(id) {
            return Ok(Some(entry.clone()));
        }
        let entry = self.database.query_row(
            "SELECT id,kind,cache_key,size,checksum,fingerprint,last_access,valid FROM artifacts WHERE id=?1", [id],
            |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?, row.get::<_, u64>(3)?, row.get::<_, String>(4)?, row.get::<_, String>(5)?, row.get::<_, u64>(6)?, row.get::<_, bool>(7)?)),
        ).optional()?;
        let Some((id, kind, key, size, checksum, fingerprint, access, valid)) = entry else {
            return Ok(None);
        };
        ensure!(
            valid_key(&key),
            "The cache index contains an invalid artifact key"
        );
        let entry = Entry {
            id,
            kind: Kind::parse(&kind)?,
            key,
            size,
            checksum,
            fingerprint,
            access,
            valid,
            verified: false,
        };
        self.remember_entry(entry.clone());
        Ok(Some(entry))
    }

    fn remember_entry(&mut self, entry: Entry) {
        if !self.entries.contains_key(&entry.id) {
            self.metadata_order
                .push_back(MetadataKey::Entry(entry.id.clone()));
        }
        self.entries.insert(entry.id.clone(), entry);
        self.trim_metadata();
    }

    fn remember_external(&mut self, path: PathBuf, value: Validation) {
        if !self.external.contains_key(&path) {
            self.metadata_order
                .push_back(MetadataKey::External(path.clone()));
        }
        self.external.insert(path, value);
        self.trim_metadata();
    }

    fn trim_metadata(&mut self) {
        while self.metadata_order.len() > MAX_METADATA {
            match self.metadata_order.pop_front() {
                Some(MetadataKey::Entry(id)) => {
                    self.entries.remove(&id);
                }
                Some(MetadataKey::External(path)) => {
                    self.external.remove(&path);
                }
                None => break,
            }
        }
    }

    fn next_access(&mut self) -> u64 {
        self.access_clock = self.access_clock.saturating_add(1).min(i64::MAX as u64);
        self.access_clock
    }

    fn touch(&mut self, id: &str) -> Result<()> {
        let access = self.next_access();
        if let Some(value) = self.entries.get_mut(id) {
            value.access = access;
        }
        self.access_dirty.insert(id.to_owned(), access);
        if self.access_dirty.len() >= ACCESS_BATCH
            || self.last_access_flush.elapsed() >= ACCESS_INTERVAL
        {
            self.flush_access(false)?;
        }
        Ok(())
    }

    fn flush_access(&mut self, force: bool) -> Result<()> {
        if self.access_dirty.is_empty()
            || !force
                && self.access_dirty.len() < ACCESS_BATCH
                && self.last_access_flush.elapsed() < ACCESS_INTERVAL
        {
            return Ok(());
        }
        let transaction = self.database.unchecked_transaction()?;
        persist_access(&transaction, &self.access_dirty, &self.entries)?;
        transaction.commit()?;
        self.access_dirty.clear();
        self.last_access_flush = Instant::now();
        Ok(())
    }

    fn update_reservations(&mut self) {
        self.stats.reserved_bytes = self
            .reservations
            .values()
            .fold(0_u64, |sum, value| sum.saturating_add(value.target));
        self.stats.peak_reserved_bytes = self
            .stats
            .peak_reserved_bytes
            .max(self.stats.reserved_bytes);
        self.stats.writes_inflight = self.reservations.len() as u64;
    }
}

fn persist_access(
    database: &Connection,
    access_dirty: &HashMap<String, u64>,
    entries: &HashMap<String, Entry>,
) -> Result<()> {
    for (id, access) in access_dirty {
        if let Some(entry) = entries.get(id) {
            database.execute(
                "UPDATE artifacts SET last_access=?1,fingerprint=?2 WHERE id=?3 AND checksum=?4",
                params![access, entry.fingerprint, id, entry.checksum],
            )?;
        } else {
            database.execute(
                "UPDATE artifacts SET last_access=?1 WHERE id=?2",
                params![access, id],
            )?;
        }
    }
    Ok(())
}

impl Drop for CacheInner {
    fn drop(&mut self) {
        let state = self
            .state
            .get_mut()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let _ = state.flush_access(true);
    }
}

fn alias_classification_exists(database: &Connection) -> Result<bool> {
    let mut statement = database.prepare("PRAGMA table_info(aliases)")?;
    for name in statement.query_map([], |row| row.get::<_, String>(1))? {
        if name? == "cover" {
            return Ok(true);
        }
    }
    Ok(false)
}

fn initialize_alias_classification(database: &mut Connection) -> Result<()> {
    const INDEX: &str =
        "CREATE INDEX IF NOT EXISTS alias_artifact_cover ON aliases(artifact_id,cover)";
    if alias_classification_exists(database)? {
        database.execute_batch(INDEX)?;
        return Ok(());
    }
    let transaction =
        database.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    // Another process may have migrated while this opener waited for the writer
    // lock. Existing aliases keep NULL because hashes cannot recover their use.
    if !alias_classification_exists(&transaction)? {
        transaction.execute(
            "ALTER TABLE aliases ADD COLUMN cover INTEGER CHECK(cover IN (0,1))",
            [],
        )?;
    }
    transaction.execute_batch(INDEX)?;
    transaction
        .commit()
        .context("Classify new cache aliases without guessing historical names")
}

fn projected_usage(state: &mut CacheState) -> Result<(u64, u64)> {
    let reservations = state.reservations.values().cloned().collect::<Vec<_>>();
    let mut groups: HashMap<String, (Kind, String, u64)> = HashMap::new();
    let mut remaining = 0_u64;
    for value in reservations {
        let written = fs::metadata(&value.temp)
            .map(|metadata| metadata.len())
            .unwrap_or(0);
        remaining = remaining.saturating_add(value.target.saturating_sub(written));
        let group = groups
            .entry(value.id.clone())
            .or_insert((value.kind, value.key, 0));
        // A pending pin cannot shelter another unpinned writer, and a pending
        // smaller replacement cannot lend quota before it is actually committed.
        if value.owner.is_none() {
            group.2 = group.2.max(value.target);
        }
    }
    let mut projected = state.stats.unpinned_bytes;
    for (id, (kind, key, target)) in groups {
        if kind == Kind::Raw && state.pinned(&key)? {
            continue;
        }
        let existing_size = state.load_entry(&id)?.map(|value| value.size).unwrap_or(0);
        projected = projected.saturating_add(target.saturating_sub(existing_size));
    }
    Ok((projected, remaining))
}

fn reclaimable_space(root: &Path, state: &CacheState) -> Result<(u64, u64)> {
    let active = state
        .reservations
        .values()
        .map(|value| value.id.as_str())
        .chain(state.leases.keys().map(String::as_str))
        .collect::<HashSet<_>>();
    let mut budget = 0_u64;
    let mut disk = 0_u64;
    let mut after = String::new();
    loop {
        let rows = {
            let mut statement = state.database.prepare(
                "SELECT id,kind,cache_key,size FROM artifacts WHERE id>?1 AND NOT(kind='raw' AND EXISTS(
                     SELECT 1 FROM pins WHERE raw_key=artifacts.cache_key)) ORDER BY id LIMIT 128",
            )?;
            statement
                .query_map([&after], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, u64>(3)?,
                    ))
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?
        };
        if rows.is_empty() {
            break;
        }
        for (id, kind, key, size) in &rows {
            if active.contains(id.as_str()) {
                continue;
            }
            let path = generated_path(root, Kind::parse(kind)?, key, false)?;
            budget = budget.saturating_add(*size);
            match fs::symlink_metadata(&path) {
                Ok(metadata) => {
                    ensure!(
                        metadata.is_file() && !metadata.file_type().is_symlink(),
                        "A reclaimable artifact must be a regular file"
                    );
                    disk = disk.saturating_add(allocated_bytes(&metadata));
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(error).context("Plan the reclaimable cache capacity"),
            }
        }
        after = rows
            .last()
            .context("The cache reclamation batch is missing")?
            .0
            .clone();
    }
    Ok((budget, disk))
}

fn allocated_bytes(metadata: &Metadata) -> u64 {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        metadata.blocks().saturating_mul(512)
    }
    #[cfg(not(unix))]
    {
        metadata.len()
    }
}

fn evict_one(root: &Path, state: &mut CacheState) -> Result<bool> {
    Ok(evict_batch(root, state, 1, None)? != 0)
}

fn evict_batch(
    root: &Path,
    state: &mut CacheState,
    limit: usize,
    cutoff: Option<u64>,
) -> Result<usize> {
    if limit == 0 {
        return Ok(0);
    }
    let active = state
        .reservations
        .values()
        .map(|value| value.id.clone())
        .chain(state.leases.keys().cloned())
        .collect::<HashSet<_>>();
    let ceiling = cutoff.unwrap_or(i64::MAX as u64);
    let transaction = state
        .database
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let mut selected = Vec::with_capacity(limit);
    let mut offset = 0_u64;
    loop {
        let candidates = {
            let mut statement = transaction.prepare(
                "SELECT id,kind,cache_key,size,checksum,fingerprint,last_access FROM artifacts WHERE last_access<=?1
                 AND NOT(kind='raw' AND EXISTS(SELECT 1 FROM pins WHERE raw_key=artifacts.cache_key))
                 ORDER BY last_access,id LIMIT 64 OFFSET ?2",
            )?;
            statement
                .query_map(params![ceiling, offset], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, u64>(3)?,
                        row.get::<_, String>(4)?,
                        row.get::<_, String>(5)?,
                        row.get::<_, u64>(6)?,
                    ))
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?
        };
        let count = candidates.len();
        for candidate in candidates {
            if active.contains(&candidate.0) {
                continue;
            }
            if cutoff.is_some()
                && (state
                    .access_dirty
                    .get(&candidate.0)
                    .is_some_and(|access| *access > ceiling)
                    || state
                        .entries
                        .get(&candidate.0)
                        .is_some_and(|entry| entry.access > ceiling))
            {
                continue;
            }
            selected.push(candidate);
            if selected.len() == limit {
                break;
            }
        }
        if selected.len() == limit || count < 64 {
            break;
        }
        offset += 64;
    }
    if selected.is_empty() {
        return Ok(0);
    }
    let mut delta = TotalsDelta::default();
    let mut bytes = 0_u64;
    for (id, kind, key, size, checksum, fingerprint, access) in &selected {
        let path = generated_path(root, Kind::parse(kind)?, key, false)?;
        remove_generated(root, &path)?;
        let changed = transaction.execute(
            "DELETE FROM artifacts WHERE id=?1 AND checksum=?2 AND fingerprint=?3 AND last_access=?4
             AND NOT(kind='raw' AND EXISTS(SELECT 1 FROM pins WHERE raw_key=artifacts.cache_key))",
            params![id,checksum,fingerprint,access],
        )?;
        ensure!(
            changed == 1,
            "A selected cache artifact changed before removal"
        );
        delta.entries -= 1;
        delta.bytes -= i128::from(*size);
        bytes = bytes.saturating_add(*size);
    }
    transaction
        .commit()
        .context("Remove the unpinned cache artifact batch")?;
    state.apply_delta(delta)?;
    for (id, _, _, _, _, _, _) in &selected {
        state.entries.remove(id);
        state.access_dirty.remove(id);
    }
    state.stats.evictions += selected.len() as u64;
    state.stats.evicted_bytes = state.stats.evicted_bytes.saturating_add(bytes);
    Ok(selected.len())
}

fn prune_budget(root: &Path, state: &mut CacheState) -> Result<()> {
    if state.stats.unpinned_bytes <= state.config.max_unpinned_bytes {
        return Ok(());
    }
    state.flush_access(true)?;
    while state.stats.unpinned_bytes > state.config.max_unpinned_bytes && evict_one(root, state)? {}
    Ok(())
}

fn render_key(key: &RenderKey) -> Result<String> {
    Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(key)?)))
}
fn alias_key(alias: &str) -> String {
    format!("{:x}", Sha256::digest(alias.as_bytes()))
}
fn alias_is_cover(alias: &str) -> bool {
    alias.starts_with("cover-source-v2:") || alias.starts_with("cover-render-v2:")
}
fn cache_id(kind: Kind, key: &str) -> String {
    format!("{}:{key}", kind.name())
}
fn valid_key(key: &str) -> bool {
    key.len() == 64
        && key
            .bytes()
            .all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase())
}

fn generated_path(root: &Path, kind: Kind, key: &str, create: bool) -> Result<PathBuf> {
    ensure!(
        valid_key(key),
        "An artifact cache key must be a lowercase SHA256 digest"
    );
    let directory = root.join(kind.name());
    ensure_safe_directory(&directory)?;
    let bucket = directory.join(&key[..2]);
    if create {
        fs::create_dir_all(&bucket).context("Create an artifact cache bucket")?;
    }
    if bucket.exists() {
        ensure_safe_directory(&bucket)?;
    }
    let path = bucket.join(format!("{key}.{}", kind.extension()));
    if path.exists() || fs::symlink_metadata(&path).is_ok() {
        safe_file_fingerprint(root, &path)?;
    }
    Ok(path)
}

fn ensure_safe_directory(path: &Path) -> Result<()> {
    ensure!(
        fs::canonicalize(path).context("Resolve an artifact directory")? == path,
        "An artifact directory was replaced by a symbolic link"
    );
    ensure!(
        fs::metadata(path)?.is_dir(),
        "An artifact directory is not a directory"
    );
    Ok(())
}

fn safe_file_fingerprint(root: &Path, path: &Path) -> Result<Option<String>> {
    ensure!(
        path.starts_with(root),
        "An artifact path escapes the cache root"
    );
    let Some(parent) = path.parent() else {
        bail!("The artifact path has no parent");
    };
    if !parent.exists() {
        return Ok(None);
    }
    ensure_safe_directory(parent)?;
    let metadata = match fs::symlink_metadata(path) {
        Ok(value) => value,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("Read an artifact fingerprint"),
    };
    ensure!(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        "A generated artifact must be a regular file"
    );
    ensure!(
        fs::canonicalize(path)?.starts_with(root),
        "The resolved artifact path escapes the cache root"
    );
    Ok(Some(metadata_fingerprint(&metadata)))
}

fn remove_generated(root: &Path, path: &Path) -> Result<()> {
    if safe_file_fingerprint(root, path)?.is_some() {
        fs::remove_file(path).context("Remove an unpinned cache artifact")?;
    }
    Ok(())
}

fn metadata_fingerprint(metadata: &Metadata) -> String {
    let modified = metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
        .map(|value| value.as_nanos())
        .unwrap_or(0);
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        format!(
            "{}:{modified}:{}:{}:{}:{}",
            metadata.len(),
            metadata.dev(),
            metadata.ino(),
            metadata.ctime(),
            metadata.ctime_nsec()
        )
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        format!(
            "{}:{modified}:{}:{}",
            metadata.len(),
            metadata.creation_time(),
            metadata.file_attributes()
        )
    }
    #[cfg(not(any(unix, windows)))]
    {
        format!("{}:{modified}", metadata.len())
    }
}

fn same_file_identity(first: &Metadata, second: &Metadata) -> bool {
    if first.len() != second.len() || !first.is_file() || !second.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        first.dev() == second.dev() && first.ino() == second.ino()
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        first.creation_time() == second.creation_time()
            && first.last_write_time() == second.last_write_time()
    }
    #[cfg(not(any(unix, windows)))]
    {
        first.modified().ok() == second.modified().ok()
    }
}

fn checksum_file(path: &Path) -> Result<(String, u64)> {
    let mut file = File::open(path).context("Open an artifact checksum")?;
    let mut hash = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    let mut total = 0_u64;
    loop {
        let read = file
            .read(&mut buffer)
            .context("Read an artifact checksum")?;
        if read == 0 {
            break;
        }
        hash.update(&buffer[..read]);
        total = total.saturating_add(read as u64);
    }
    Ok((format!("{:x}", hash.finalize()), total))
}

fn hash_part(hash: &mut Sha256, value: &[u8]) {
    hash.update((value.len() as u64).to_le_bytes());
    hash.update(value);
}
fn hash_path(hash: &mut Sha256, path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        hash_part(hash, path.as_os_str().as_bytes());
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        let bytes = path
            .as_os_str()
            .encode_wide()
            .flat_map(u16::to_le_bytes)
            .collect::<Vec<_>>();
        hash_part(hash, &bytes);
    }
    #[cfg(not(any(unix, windows)))]
    {
        hash_part(hash, path.to_string_lossy().as_bytes());
    }
}

fn absolute_path(path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_default().join(path)
    }
}
fn record_hit(stats: &mut CacheStats, kind: Kind) {
    stats.hits += 1;
    match kind {
        Kind::Raw => stats.raw_hits += 1,
        Kind::Processed => stats.processed_hits += 1,
    }
}

fn reconcile(root: &Path, state: &mut CacheState) -> Result<()> {
    // The SQLite writer lock prevents removing a leaf another process has just
    // published but has not yet indexed. Staging files are never swept here.
    state.database.execute_batch("BEGIN IMMEDIATE")?;
    let result = (|| -> Result<()> {
        cleanup_process_files(root, &state.database)?;
        for kind in [Kind::Raw, Kind::Processed] {
            for bucket in fs::read_dir(root.join(kind.name()))? {
                let bucket = bucket?;
                let name = bucket.file_name();
                let name = name.to_string_lossy();
                if name.len() != 2
                    || !name
                        .bytes()
                        .all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase())
                    || !bucket.file_type()?.is_dir()
                {
                    continue;
                }
                ensure_safe_directory(&bucket.path())?;
                for leaf in fs::read_dir(bucket.path())? {
                    let leaf = leaf?;
                    if !leaf.file_type()?.is_file() {
                        continue;
                    }
                    let name = leaf.file_name();
                    let name = name.to_string_lossy();
                    let Some(key) = name.strip_suffix(&format!(".{}", kind.extension())) else {
                        continue;
                    };
                    if !valid_key(key) || &key[..2] != bucket.file_name().to_string_lossy().as_ref()
                    {
                        continue;
                    }
                    let id = cache_id(kind, key);
                    let indexed = state.database.query_row(
                        "SELECT EXISTS(SELECT 1 FROM artifacts WHERE id=?1)",
                        [&id],
                        |row| row.get::<_, bool>(0),
                    )?;
                    if indexed {
                        continue;
                    }
                    if kind == Kind::Raw && state.pinned(key)? {
                        let metadata = fs::metadata(leaf.path())?;
                        state.database.execute("INSERT INTO artifacts(id,kind,cache_key,size,checksum,fingerprint,last_access,valid) VALUES(?1,'raw',?2,?3,'',?4,0,0)", params![id,key,metadata.len(),metadata_fingerprint(&metadata)])?;
                    } else {
                        remove_generated(root, &leaf.path())?;
                    }
                }
            }
        }
        Ok(())
    })();
    match result {
        Ok(()) => {
            state.database.execute_batch("COMMIT")?;
            Ok(())
        }
        Err(error) => {
            let _ = state.database.execute_batch("ROLLBACK");
            Err(error)
        }
    }
}

fn pin_process_id(owner: &str) -> u32 {
    if owner.starts_with("transient:") {
        std::process::id()
    } else {
        0
    }
}

fn orphan_process(pid: u32) -> bool {
    // A newly constructed CacheInner has no live writer or pin guard in this
    // process: those guards would retain its Arc in the registry.
    pid > 0 && (pid == std::process::id() || process_is_dead(pid))
}

fn cleanup_process_files(root: &Path, database: &Connection) -> Result<()> {
    let mut after = (String::new(), -1_i64);
    loop {
        let rows = {
            let mut statement = database.prepare(
                "SELECT DISTINCT owner,process_id FROM pins WHERE substr(owner,1,10)='transient:'
                 AND (owner>?1 OR (owner=?1 AND process_id>?2)) ORDER BY owner,process_id LIMIT 128",
            )?;
            statement
                .query_map(params![after.0, after.1], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
                })?
                .collect::<rusqlite::Result<Vec<_>>>()?
        };
        if rows.is_empty() {
            break;
        }
        for (owner, process_id) in &rows {
            let pid = u32::try_from(*process_id)
                .ok()
                .filter(|pid| *pid > 0)
                .or_else(|| {
                    owner
                        .strip_prefix("transient:archive:")?
                        .split(':')
                        .next()?
                        .parse()
                        .ok()
                });
            if pid.is_some_and(orphan_process) {
                database.execute("DELETE FROM pins WHERE owner=?1 AND process_id=?2 AND substr(owner,1,10)='transient:'", params![owner, process_id])?;
            }
        }
        after = rows
            .last()
            .cloned()
            .context("The transient pin batch is missing")?;
    }
    let directory = root.join("temp");
    ensure_safe_directory(&directory)?;
    for leaf in fs::read_dir(&directory)? {
        let leaf = leaf?;
        if !leaf.file_type()?.is_file() {
            continue;
        }
        let name = leaf.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        let Some(pid) = temporary_process(name) else {
            continue;
        };
        if !orphan_process(pid) {
            continue;
        }
        let path = leaf.path();
        ensure!(
            fs::canonicalize(&path)? == path && path.parent() == Some(directory.as_path()),
            "A staging artifact escapes its cache directory"
        );
        ensure_safe_directory(&directory)?;
        fs::remove_file(path).context("Remove an abandoned cache staging file")?;
    }
    Ok(())
}

fn temporary_process(name: &str) -> Option<u32> {
    let mut parts = name.strip_suffix(".part")?.split('-');
    let pid_text = parts.next()?;
    let counter_text = parts.next()?;
    let key = parts.next()?;
    if parts.next().is_some() || !valid_key(key) {
        return None;
    }
    let pid = pid_text.parse::<u32>().ok()?;
    let counter = counter_text.parse::<u64>().ok()?;
    if pid == 0
        || counter == 0
        || pid.to_string() != pid_text
        || counter.to_string() != counter_text
    {
        return None;
    }
    Some(pid)
}

#[cfg(unix)]
fn process_is_dead(pid: u32) -> bool {
    let Ok(pid) = libc::pid_t::try_from(pid) else {
        return false;
    };
    if unsafe { libc::kill(pid, 0) } == 0 {
        return false;
    }
    std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}

#[cfg(windows)]
fn process_is_dead(pid: u32) -> bool {
    let process = unsafe { OpenProcess(0x1000, 0, pid) };
    if process.is_null() {
        return std::io::Error::last_os_error().raw_os_error() == Some(87);
    }
    let mut exit_code = 259_u32;
    let queried = unsafe { GetExitCodeProcess(process, &mut exit_code) };
    unsafe {
        CloseHandle(process);
    }
    queried != 0 && exit_code != 259
}

#[cfg(not(any(unix, windows)))]
fn process_is_dead(_pid: u32) -> bool {
    false
}

#[cfg(unix)]
fn available_bytes(path: &Path) -> Result<u64> {
    use std::{ffi::CString, os::unix::ffi::OsStrExt};
    let path =
        CString::new(path.as_os_str().as_bytes()).context("Encode the cache filesystem path")?;
    let mut status = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    // statvfs writes the complete structure only when it returns zero.
    if unsafe { libc::statvfs(path.as_ptr(), status.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error()).context("Read cache filesystem capacity");
    }
    let status = unsafe { status.assume_init() };
    Ok((status.f_bavail as u64).saturating_mul(status.f_frsize as u64))
}

#[cfg(windows)]
fn available_bytes(path: &Path) -> Result<u64> {
    use std::os::windows::ffi::OsStrExt;
    let path = path
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let mut available = 0_u64;
    if unsafe {
        GetDiskFreeSpaceExW(
            path.as_ptr(),
            &mut available,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(std::io::Error::last_os_error()).context("Read cache filesystem capacity");
    }
    Ok(available)
}

#[cfg(not(any(unix, windows)))]
fn available_bytes(_path: &Path) -> Result<u64> {
    bail!("Disk capacity reporting is unavailable on this platform")
}

#[cfg(unix)]
fn atomic_replace(source: &Path, destination: &Path) -> Result<()> {
    fs::rename(source, destination).context("Atomically rename the cache artifact")
}

#[cfg(windows)]
fn atomic_replace(source: &Path, destination: &Path) -> Result<()> {
    use std::os::windows::ffi::OsStrExt;
    let source = source
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let destination = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    if unsafe { MoveFileExW(source.as_ptr(), destination.as_ptr(), 0x1 | 0x8) } == 0 {
        return Err(std::io::Error::last_os_error())
            .context("Atomically replace the cache artifact");
    }
    Ok(())
}

#[cfg(not(any(unix, windows)))]
fn atomic_replace(source: &Path, destination: &Path) -> Result<()> {
    fs::rename(source, destination).context("Publish the cache artifact")
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<()> {
    File::open(path)?
        .sync_all()
        .context("Synchronize the cache directory")
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<()> {
    Ok(())
}

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetDiskFreeSpaceExW(
        directory: *const u16,
        available: *mut u64,
        total: *mut u64,
        free: *mut u64,
    ) -> i32;
    fn MoveFileExW(source: *const u16, destination: *const u16, flags: u32) -> i32;
    fn OpenProcess(access: u32, inherit: i32, process_id: u32) -> *mut std::ffi::c_void;
    fn GetExitCodeProcess(process: *mut std::ffi::c_void, exit_code: *mut u32) -> i32;
    fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Default)]
    struct CommitProbe {
        attempts: AtomicU64,
        reject_next: AtomicU64,
    }

    struct CommitObserver {
        cache: CacheManager,
        probe: Box<CommitProbe>,
    }

    impl CommitObserver {
        fn attach(cache: &CacheManager) -> Self {
            let mut probe = Box::<CommitProbe>::default();
            let context = (&mut *probe as *mut CommitProbe).cast();
            let state = cache.lock().unwrap();
            // The boxed context stays at a stable address until Drop removes
            // this hook. The callback only reads atomics and never enters SQL.
            unsafe {
                rusqlite::ffi::sqlite3_commit_hook(
                    state.database.handle(),
                    Some(observe_commit),
                    context,
                );
            }
            drop(state);
            Self {
                cache: cache.clone(),
                probe,
            }
        }

        fn attempts(&self) -> u64 {
            self.probe.attempts.load(Ordering::Relaxed)
        }

        fn reject_next(&self) {
            self.probe.reject_next.store(1, Ordering::Relaxed);
        }
    }

    impl Drop for CommitObserver {
        fn drop(&mut self) {
            let state = self
                .cache
                .inner
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            // Remove the callback before its boxed context is released, also
            // when an assertion unwinds through this observer.
            unsafe {
                rusqlite::ffi::sqlite3_commit_hook(
                    state.database.handle(),
                    None,
                    std::ptr::null_mut(),
                );
            }
        }
    }

    unsafe extern "C" fn observe_commit(context: *mut std::ffi::c_void) -> std::ffi::c_int {
        // CommitObserver retains the live allocation for every installed hook.
        let probe = unsafe { &*context.cast::<CommitProbe>() };
        probe.attempts.fetch_add(1, Ordering::Relaxed);
        i32::from(probe.reject_next.swap(0, Ordering::Relaxed) != 0)
    }

    fn page(name: &str) -> Page {
        Page {
            url: format!("https://example.test/{name}"),
            ..Page::default()
        }
    }
    fn manager(directory: &tempfile::TempDir, budget: u64) -> CacheManager {
        CacheManager::open_with_config(
            directory.path(),
            CacheConfig {
                max_unpinned_bytes: budget,
                min_free_bytes: 0,
            },
        )
        .unwrap()
    }
    fn publish(cache: &CacheManager, page: &Page, bytes: &[u8], owner: Option<&str>) -> PathBuf {
        let write = cache.begin_raw(page, owner, bytes.len() as u64).unwrap();
        fs::write(write.temp_path(), bytes).unwrap();
        write.commit().unwrap()
    }
    fn render(source: &str) -> RenderKey {
        RenderKey {
            source_key: source.into(),
            width: 100,
            height: 200,
            crop_borders: true,
            dither: true,
            contrast: 0,
            vertical: false,
            offset: 0,
            zoom_milli: 1000,
        }
    }

    fn image_bytes(marker: &str) -> Vec<u8> {
        let mut bytes = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut bytes, 8, 8);
            encoder.set_color(png::ColorType::Grayscale);
            encoder.set_depth(png::BitDepth::Eight);
            encoder
                .add_text_chunk("Proof".into(), marker.into())
                .unwrap();
            let mut writer = encoder.write_header().unwrap();
            writer.write_image_data(&[17; 64]).unwrap();
            writer.finish().unwrap();
        }
        bytes
    }

    fn assert_totals_match_index(cache: &CacheManager) {
        let stats = cache.stats();
        let expected = cache.lock().unwrap().database.query_row(
            "SELECT COUNT(*),COALESCE(SUM(size),0),COALESCE(SUM(CASE WHEN kind='raw' AND EXISTS(
                 SELECT 1 FROM pins WHERE raw_key=artifacts.cache_key) THEN size ELSE 0 END),0),
                 COALESCE(SUM(CASE WHEN kind='raw' AND EXISTS(SELECT 1 FROM pins WHERE raw_key=artifacts.cache_key) THEN 1 ELSE 0 END),0) FROM artifacts",
            [], |row| Ok((row.get::<_, u64>(0)?,row.get::<_, u64>(1)?,row.get::<_, u64>(2)?,row.get::<_, u64>(3)?)),
        ).unwrap();
        assert_eq!(
            (
                stats.entries,
                stats.bytes,
                stats.pinned_bytes,
                stats.pinned_entries
            ),
            expected
        );
        assert_eq!(stats.unpinned_bytes, stats.bytes - stats.pinned_bytes);
    }

    fn wait_for_tickets<T>(lock: &FairMutex<T>, expected_next: u64) {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            let next = lock
                .tickets
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .next;
            if next >= expected_next {
                return;
            }
            assert!(
                Instant::now() < deadline,
                "The cache caller did not join the ticket queue"
            );
            std::thread::yield_now();
        }
    }

    #[test]
    fn a_requeued_maintenance_owner_cannot_overtake_waiting_callers() {
        let lock = Arc::new(FairMutex::new(Vec::new()));
        let held = lock.lock().unwrap();
        let mut workers = Vec::new();
        for index in 0..3 {
            let other = lock.clone();
            workers.push(std::thread::spawn(move || {
                other.lock().unwrap().push(index);
            }));
            wait_for_tickets(&lock, index as u64 + 2);
        }
        drop(held);
        let maintenance = lock.lock().unwrap();
        assert_eq!(&*maintenance, &[0, 1, 2]);
        drop(maintenance);
        for worker in workers {
            worker.join().unwrap();
        }
    }

    #[test]
    fn public_stats_pins_and_guard_drops_share_the_fair_queue() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let request = page("fair-queue");
        let path = publish(&cache, &request, &[1; 20], None);
        let lease = cache.lease_path(&path).unwrap();
        let abandoned = cache.begin_raw(&page("abandoned"), None, 20).unwrap();
        let staged = abandoned.temp_path().to_path_buf();
        let mut held = cache.lock().unwrap();
        held.stats.hits = 123;
        let first = cache
            .inner
            .state
            .tickets
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .next;
        let stats_cache = cache.clone();
        let stats_worker = std::thread::spawn(move || stats_cache.stats());
        wait_for_tickets(&cache.inner.state, first + 1);
        let pin_cache = cache.clone();
        let pin_request = request.clone();
        let pin_worker =
            std::thread::spawn(move || pin_cache.pin_raw(&pin_request, "download:queued").unwrap());
        wait_for_tickets(&cache.inner.state, first + 2);
        let lease_worker = std::thread::spawn(move || drop(lease));
        wait_for_tickets(&cache.inner.state, first + 3);
        let write_worker = std::thread::spawn(move || drop(abandoned));
        wait_for_tickets(&cache.inner.state, first + 4);
        drop(held);
        let maintenance = cache.lock().unwrap();
        assert_eq!(maintenance.stats.pinned_entries, 1);
        assert!(maintenance.leases.is_empty() && maintenance.reservations.is_empty());
        drop(maintenance);
        assert_eq!(stats_worker.join().unwrap().hits, 123);
        pin_worker.join().unwrap();
        lease_worker.join().unwrap();
        write_worker.join().unwrap();
        assert!(!staged.exists());
        assert_totals_match_index(&cache);
    }

    #[test]
    fn a_publication_can_finish_between_repeated_maintenance_turns() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let request = page("queued-publication");
        let write = cache
            .begin_raw(&request, Some("download:queued-publication"), 3)
            .unwrap();
        fs::write(write.temp_path(), b"new").unwrap();
        let write_id = write.id;
        let held = cache.lock().unwrap();
        let first = cache
            .inner
            .state
            .tickets
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .next;
        let publication = std::thread::spawn(move || write.commit().unwrap());
        wait_for_tickets(&cache.inner.state, first + 1);
        drop(held);
        let maintenance = cache.lock().unwrap();
        if maintenance.reservations.contains_key(&write_id) {
            wait_for_tickets(&cache.inner.state, maintenance.ticket.wrapping_add(2));
        }
        drop(maintenance);
        let next_batch = cache.lock().unwrap();
        assert!(!next_batch.reservations.contains_key(&write_id));
        assert_eq!(next_batch.stats.pinned_entries, 1);
        drop(next_batch);
        let path = publication.join().unwrap();
        assert_eq!(cache.lookup_raw(&request).unwrap(), Some(path));
        assert_totals_match_index(&cache);
    }

    #[test]
    fn an_existing_pin_is_a_true_sql_noop() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let request = page("pin-noop");
        publish(&cache, &request, &[1; 20], None);
        cache.pin_raw(&request, "download:keep").unwrap();
        let changes = || {
            cache
                .lock()
                .unwrap()
                .database
                .query_row("SELECT total_changes()", [], |row| row.get::<_, u64>(0))
                .unwrap()
        };
        let before = changes();
        for _ in 0..1_000 {
            cache.pin_raw(&request, "download:keep").unwrap();
        }
        assert_eq!(changes(), before);
        assert_totals_match_index(&cache);
    }

    #[test]
    fn publication_persists_pending_access_and_pins_with_one_commit() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let first = page("hot-before-publication");
        publish(&cache, &first, b"retained bytes", None);
        let first_id = cache_id(Kind::Raw, &CacheManager::page_key(&first));
        let next = page("single-transaction-publication");
        let write = cache
            .begin_raw(&next, Some("download:single-transaction"), 9)
            .unwrap();
        fs::write(write.temp_path(), b"new bytes").unwrap();
        let expected_access = {
            let mut state = cache.lock().unwrap();
            state.last_access_flush = Instant::now();
            state.touch(&first_id).unwrap();
            state.access_dirty[&first_id]
        };
        let observer = CommitObserver::attach(&cache);
        write.commit().unwrap();
        assert_eq!(observer.attempts(), 1);
        {
            let state = cache.lock().unwrap();
            let persisted_access = state
                .database
                .query_row(
                    "SELECT last_access FROM artifacts WHERE id=?1",
                    [&first_id],
                    |row| row.get::<_, u64>(0),
                )
                .unwrap();
            assert_eq!(persisted_access, expected_access);
            assert!(state.access_dirty.is_empty());
            assert_eq!(state.stats.pinned_entries, 1);
            assert_eq!(state.stats.commits, 2);
        }
        assert_totals_match_index(&cache);
    }

    #[test]
    fn same_content_replacement_does_not_restore_an_old_access_fingerprint() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let request = page("same-content-replacement");
        let path = publish(&cache, &request, b"identical bytes", None);
        let id = cache_id(Kind::Raw, &CacheManager::page_key(&request));
        let write = cache.begin_raw(&request, None, 15).unwrap();
        fs::write(write.temp_path(), b"identical bytes").unwrap();
        let modified = fs::metadata(&path).unwrap().modified().unwrap() + Duration::from_secs(3);
        OpenOptions::new()
            .write(true)
            .open(write.temp_path())
            .unwrap()
            .set_times(fs::FileTimes::new().set_modified(modified))
            .unwrap();
        let pending_access = {
            let mut state = cache.lock().unwrap();
            state.last_access_flush = Instant::now();
            state.touch(&id).unwrap();
            state.access_dirty[&id]
        };
        let observer = CommitObserver::attach(&cache);
        assert_eq!(write.commit().unwrap(), path);
        assert_eq!(observer.attempts(), 1);
        let expected_fingerprint = safe_file_fingerprint(&cache.inner.root, &path)
            .unwrap()
            .unwrap();
        {
            let state = cache.lock().unwrap();
            let persisted = state
                .database
                .query_row(
                    "SELECT last_access,fingerprint FROM artifacts WHERE id=?1",
                    [&id],
                    |row| Ok((row.get::<_, u64>(0)?, row.get::<_, String>(1)?)),
                )
                .unwrap();
            assert!(persisted.0 > pending_access);
            assert_eq!(persisted.0, state.entries[&id].access);
            assert_eq!(persisted.1, expected_fingerprint);
            assert!(state.access_dirty.is_empty());
        }
        assert_totals_match_index(&cache);
    }

    #[test]
    fn a_failed_download_pin_rolls_back_publication_and_pending_access() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let hot = page("hot-before-failed-pin");
        publish(&cache, &hot, b"preserved bytes", None);
        let id = cache_id(Kind::Raw, &CacheManager::page_key(&hot));
        let failed = page("failed-pin-publication");
        let failed_id = cache_id(Kind::Raw, &CacheManager::page_key(&failed));
        let write = cache
            .begin_raw(&failed, Some("download:fail-publication"), 9)
            .unwrap();
        fs::write(write.temp_path(), b"new bytes").unwrap();
        let (persisted_access, pending, last_flush, before) = {
            let mut state = cache.lock().unwrap();
            state
                .database
                .execute_batch(
                    "CREATE TEMP TRIGGER fail_publication_pin BEFORE INSERT ON pins
                 WHEN NEW.owner='download:fail-publication'
                 BEGIN SELECT RAISE(ABORT,'injected publication pin failure'); END",
                )
                .unwrap();
            let persisted = state
                .database
                .query_row(
                    "SELECT last_access FROM artifacts WHERE id=?1",
                    [&id],
                    |row| row.get::<_, u64>(0),
                )
                .unwrap();
            state.last_access_flush = Instant::now();
            state.touch(&id).unwrap();
            (
                persisted,
                state.access_dirty.clone(),
                state.last_access_flush,
                state.stats.clone(),
            )
        };
        assert!(write.commit().is_err());
        {
            let state = cache.lock().unwrap();
            assert_eq!(state.access_dirty, pending);
            assert_eq!(state.last_access_flush, last_flush);
            assert_eq!(state.stats.commits, before.commits);
            assert_eq!(state.stats.entries, before.entries);
            assert_eq!(state.stats.bytes, before.bytes);
            assert_eq!(state.stats.pinned_entries, before.pinned_entries);
            assert!(state.reservations.is_empty());
            let persisted = state
                .database
                .query_row(
                    "SELECT last_access FROM artifacts WHERE id=?1",
                    [&id],
                    |row| row.get::<_, u64>(0),
                )
                .unwrap();
            assert_eq!(persisted, persisted_access);
            let indexed = state
                .database
                .query_row(
                    "SELECT EXISTS(SELECT 1 FROM artifacts WHERE id=?1)",
                    [&failed_id],
                    |row| row.get::<_, bool>(0),
                )
                .unwrap();
            assert!(!indexed);
            let pinned = state
                .database
                .query_row(
                    "SELECT EXISTS(SELECT 1 FROM pins WHERE owner='download:fail-publication')",
                    [],
                    |row| row.get::<_, bool>(0),
                )
                .unwrap();
            assert!(!pinned);
        }
        assert!(cache.lookup_raw(&failed).unwrap().is_none());
        assert_totals_match_index(&cache);
        // The atomic file publication preceded the rejected SQL statement.
        // Reopening may remove that unindexed leaf without inventing a pin.
        drop(cache);
        let cache = manager(&directory, 4096);
        assert!(cache.lookup_raw(&failed).unwrap().is_none());
        assert_eq!(cache.stats().entries, 1);
        assert_eq!(cache.stats().pinned_entries, 0);
        assert_totals_match_index(&cache);
    }

    #[test]
    fn a_rejected_commit_retains_access_hints_for_the_next_publication() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let hot = page("hot-before-rejected-commit");
        publish(&cache, &hot, b"preserved bytes", None);
        let id = cache_id(Kind::Raw, &CacheManager::page_key(&hot));
        let failed = page("rejected-commit");
        let write = cache
            .begin_raw(&failed, Some("download:rejected-commit"), 9)
            .unwrap();
        fs::write(write.temp_path(), b"new bytes").unwrap();
        let (pending, last_flush) = {
            let mut state = cache.lock().unwrap();
            state.last_access_flush = Instant::now();
            state.touch(&id).unwrap();
            (state.access_dirty.clone(), state.last_access_flush)
        };
        let observer = CommitObserver::attach(&cache);
        observer.reject_next();
        assert!(write.commit().is_err());
        assert_eq!(observer.attempts(), 1);
        {
            let state = cache.lock().unwrap();
            assert_eq!(state.access_dirty, pending);
            assert_eq!(state.last_access_flush, last_flush);
            assert_eq!(state.stats.commits, 1);
            assert_eq!(state.stats.entries, 1);
            assert_eq!(state.stats.pinned_entries, 0);
            let persisted = state
                .database
                .query_row(
                    "SELECT last_access FROM artifacts WHERE id=?1",
                    [&id],
                    |row| row.get::<_, u64>(0),
                )
                .unwrap();
            assert!(persisted < pending[&id]);
        }
        publish(&cache, &page("successful-retry"), b"retried bytes", None);
        assert_eq!(observer.attempts(), 2);
        {
            let state = cache.lock().unwrap();
            assert!(state.access_dirty.is_empty());
            let persisted = state
                .database
                .query_row(
                    "SELECT last_access FROM artifacts WHERE id=?1",
                    [&id],
                    |row| row.get::<_, u64>(0),
                )
                .unwrap();
            assert_eq!(persisted, pending[&id]);
        }
        assert_totals_match_index(&cache);
    }

    #[test]
    fn processed_fingerprints_share_a_validated_snapshot_and_isolate_namespaces() {
        let directory = tempfile::tempdir().unwrap();
        let other_directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let other = manager(&other_directory, 4096);
        assert_eq!(
            cache.namespace(),
            fs::canonicalize(directory.path().join("cache/shared-v1")).unwrap()
        );
        assert_ne!(cache.namespace(), other.namespace());
        let key = render("processed-fingerprint");
        let bytes = image_bytes("processed");
        let write = cache.begin_processed(&key, bytes.len() as u64).unwrap();
        fs::write(write.temp_path(), &bytes).unwrap();
        let path = write.commit().unwrap();
        let checksum = checksum_file(&path).unwrap().0;
        let before = cache.stats();
        assert_eq!(
            cache.lookup_processed_with_fingerprint(&key).unwrap(),
            Some((path.clone(), checksum)),
        );
        assert_eq!(cache.stats().processed_hits, before.processed_hits + 1);
        fs::write(&path, b"corrupt").unwrap();
        assert!(
            cache
                .lookup_processed_with_fingerprint(&key)
                .unwrap()
                .is_none()
        );
        assert_eq!(cache.stats().corruptions, before.corruptions + 1);
        assert_totals_match_index(&cache);
    }

    #[test]
    fn incremental_totals_follow_replacements_and_the_last_pin_owner() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let first = page("first");
        publish(&cache, &first, &[1; 10], None);
        assert_totals_match_index(&cache);
        cache.pin_raw(&first, "download:a").unwrap();
        cache.pin_raw(&first, "download:b").unwrap();
        cache.unpin_owner("download:a").unwrap();
        assert_eq!(cache.stats().pinned_bytes, 10);
        assert_totals_match_index(&cache);
        publish(&cache, &first, &[2; 17], None);
        assert_eq!(cache.stats().pinned_bytes, 17);
        assert_totals_match_index(&cache);
        let second = page("pre-pinned");
        cache.pin_raw(&second, "download:c").unwrap();
        publish(&cache, &second, &[3; 23], None);
        assert_eq!(cache.stats().pinned_entries, 2);
        assert_totals_match_index(&cache);
        cache
            .unpin_owners(&[
                "download:b".into(),
                "download:b".into(),
                "download:c".into(),
            ])
            .unwrap();
        assert_eq!(cache.stats().pinned_entries, 0);
        assert_totals_match_index(&cache);
        cache.invalidate_raw(&first).unwrap();
        cache.invalidate_raw(&first).unwrap();
        assert_totals_match_index(&cache);
        let key = render("source:totals");
        let write = cache.begin_processed(&key, 8).unwrap();
        fs::write(write.temp_path(), b"rendered").unwrap();
        write.commit().unwrap();
        assert_totals_match_index(&cache);
        cache.invalidate_processed(&key).unwrap();
        assert_totals_match_index(&cache);
        cache.clear_unpinned().unwrap();
        assert_eq!(cache.stats().bytes, 0);
        assert_totals_match_index(&cache);
    }

    #[test]
    fn clear_batches_preserve_new_versions_pins_leases_and_reservations() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let requests = ["a", "b", "c", "d", "f", "g"].map(page);
        let paths = requests
            .iter()
            .map(|request| publish(&cache, request, &[1; 20], None))
            .collect::<Vec<_>>();
        let root = cache.inner.root.clone();
        let cutoff = cache.lock().unwrap().access_clock;
        {
            let mut state = cache.lock().unwrap();
            assert_eq!(evict_batch(&root, &mut state, 1, Some(cutoff)).unwrap(), 1);
        }
        cache.pin_raw(&requests[1], "download:new-pin").unwrap();
        let lease = cache.lease_path(&paths[2]).unwrap();
        let replacement = publish(&cache, &requests[3], &[2; 30], None);
        let pending = cache.begin_raw(&requests[5], None, 20).unwrap();
        let added = publish(&cache, &page("new-after-clear-start"), &[3; 25], None);
        {
            let mut state = cache.lock().unwrap();
            assert_eq!(
                evict_batch(&root, &mut state, CLEAR_BATCH, Some(cutoff)).unwrap(),
                1
            );
        }
        assert!(!paths[0].exists() && !paths[4].exists());
        for path in [&paths[1], &paths[2], &paths[5], &replacement, &added] {
            assert!(path.exists());
        }
        assert_eq!(fs::read(replacement).unwrap(), [2; 30]);
        assert_totals_match_index(&cache);
        drop(pending);
        drop(lease);
    }

    #[test]
    fn a_failed_delete_batch_does_not_apply_uncommitted_counter_deltas() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let first = page("first-delete");
        let second = page("second-delete");
        publish(&cache, &first, &[1; 20], None);
        publish(&cache, &second, &[2; 20], None);
        let before = cache.stats();
        let root = cache.inner.root.clone();
        let sql = format!(
            "CREATE TEMP TRIGGER fail_cache_delete BEFORE DELETE ON artifacts WHEN OLD.cache_key='{}' BEGIN SELECT RAISE(ABORT,'injected delete failure'); END",
            CacheManager::page_key(&second)
        );
        {
            let mut state = cache.lock().unwrap();
            state.database.execute_batch(&sql).unwrap();
            assert!(evict_batch(&root, &mut state, CLEAR_BATCH, None).is_err());
            state
                .database
                .execute_batch("DROP TRIGGER fail_cache_delete")
                .unwrap();
        }
        assert_eq!(
            (
                cache.stats().entries,
                cache.stats().bytes,
                cache.stats().evictions
            ),
            (before.entries, before.bytes, before.evictions)
        );
        assert_totals_match_index(&cache);
        assert!(cache.lookup_raw(&first).unwrap().is_none());
        assert_totals_match_index(&cache);
        cache.clear_unpinned().unwrap();
        assert_eq!(cache.stats().entries, 0);
        assert_totals_match_index(&cache);
    }

    #[test]
    fn invalidation_accepts_a_verified_but_not_yet_persisted_fingerprint() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let request = page("touched");
        let path = publish(&cache, &request, b"unchanged bytes", None);
        let modified = fs::metadata(&path).unwrap().modified().unwrap() + Duration::from_secs(3);
        OpenOptions::new()
            .write(true)
            .open(&path)
            .unwrap()
            .set_times(fs::FileTimes::new().set_modified(modified))
            .unwrap();
        assert!(cache.lookup_raw(&request).unwrap().is_some());
        cache.invalidate_raw(&request).unwrap();
        assert!(!path.exists());
        assert_totals_match_index(&cache);
    }

    #[test]
    fn committing_a_validated_image_preserves_the_validation_proof() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let image = image_bytes("original");
        let write = cache
            .begin_raw(&page("image"), None, image.len() as u64)
            .unwrap();
        fs::write(write.temp_path(), &image).unwrap();
        cache.validate_path(write.temp_path()).unwrap();
        let validated = cache.stats();
        let canonical = write.commit().unwrap();
        let committed = cache.stats();
        assert_eq!(
            committed.external_validations,
            validated.external_validations
        );
        assert_eq!(
            committed.checksum_bytes,
            validated.checksum_bytes + image.len() as u64
        );
        cache.validate_path(&canonical).unwrap();
        let repeated = cache.stats();
        assert_eq!(
            repeated.external_validations,
            committed.external_validations
        );
        assert_eq!(repeated.checksum_bytes, committed.checksum_bytes);
    }

    #[test]
    fn opaque_checksums_do_not_become_image_validation_proofs() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let write = cache.begin_raw(&page("archive"), None, 12).unwrap();
        fs::write(write.temp_path(), b"opaque bytes").unwrap();
        cache.fingerprint(write.temp_path()).unwrap();
        let canonical = write.commit().unwrap();
        assert!(cache.validate_path(&canonical).is_err());
        assert!(
            !cache
                .lock()
                .unwrap()
                .external
                .get(&canonical)
                .is_some_and(|memo| memo.image_validated)
        );
    }

    #[test]
    fn changed_temp_bytes_cannot_inherit_a_matching_metadata_proof() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let first = image_bytes("first");
        let second = image_bytes("other");
        assert_eq!(first.len(), second.len());
        let write = cache
            .begin_raw(&page("image"), None, first.len() as u64)
            .unwrap();
        fs::write(write.temp_path(), &first).unwrap();
        cache.validate_path(write.temp_path()).unwrap();
        let original_time = fs::metadata(write.temp_path()).unwrap().modified().unwrap();
        fs::write(write.temp_path(), &second).unwrap();
        OpenOptions::new()
            .write(true)
            .open(write.temp_path())
            .unwrap()
            .set_times(fs::FileTimes::new().set_modified(original_time))
            .unwrap();
        // Model a filesystem whose timestamp precision cannot distinguish the
        // overwrite. The independently recomputed checksum must still reject it.
        let fingerprint = metadata_fingerprint(&fs::metadata(write.temp_path()).unwrap());
        cache
            .lock()
            .unwrap()
            .external
            .get_mut(write.temp_path())
            .unwrap()
            .fingerprint = fingerprint;
        let canonical = write.commit().unwrap();
        let before = cache.stats();
        assert!(
            !cache
                .lock()
                .unwrap()
                .external
                .get(&canonical)
                .is_some_and(|memo| memo.image_validated)
        );
        cache.validate_path(&canonical).unwrap();
        let after = cache.stats();
        assert_eq!(after.external_validations, before.external_validations + 1);
        assert_eq!(
            after.checksum_bytes,
            before.checksum_bytes + second.len() as u64
        );
    }

    #[test]
    fn impossible_space_policy_does_not_evict_committed_files_first() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 120);
        let pinned = publish(&cache, &page("pinned"), &[1; 50], Some("download:keep"));
        let ordinary = publish(&cache, &page("ordinary"), &[2; 40], None);
        let leased = publish(&cache, &page("leased"), &[3; 50], None);
        let lease = cache.lease_path(&leased).unwrap();
        let original = [&pinned, &ordinary, &leased].map(|path| checksum_file(path).unwrap().0);
        let before = cache.stats();
        cache
            .set_config(CacheConfig {
                max_unpinned_bytes: 50,
                min_free_bytes: 0,
            })
            .unwrap();
        assert!(cache.begin_raw(&page("blocked-budget"), None, 30).is_err());
        assert_eq!(cache.stats().entries, before.entries);
        assert_eq!(cache.stats().evictions, before.evictions);
        assert_eq!(
            [&pinned, &ordinary, &leased].map(|path| checksum_file(path).unwrap().0),
            original
        );
        cache
            .set_config(CacheConfig {
                max_unpinned_bytes: 120,
                min_free_bytes: u64::MAX,
            })
            .unwrap();
        assert!(cache.begin_raw(&page("blocked-disk"), None, 1).is_err());
        assert_eq!(cache.stats().entries, before.entries);
        assert_eq!(cache.stats().evictions, before.evictions);
        assert_eq!(
            [&pinned, &ordinary, &leased].map(|path| checksum_file(path).unwrap().0),
            original
        );
        cache
            .set_config(CacheConfig {
                max_unpinned_bytes: 120,
                min_free_bytes: 0,
            })
            .unwrap();
        drop(lease);
    }

    #[test]
    fn clones_and_reopens_share_reservations_without_resetting_policy() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 100);
        let other = CacheManager::open(directory.path().join(".")).unwrap();
        assert!(Arc::ptr_eq(&cache.inner, &other.inner));
        let first = cache.begin_raw(&page("first"), None, 60).unwrap();
        assert!(other.begin_raw(&page("second"), None, 60).is_err());
        drop(first);
        let second = other.begin_raw(&page("second"), None, 60).unwrap();
        let staged = second.temp_path().to_path_buf();
        drop(second);
        assert!(!staged.exists());
        assert_eq!(cache.stats().writes_inflight, 0);
    }

    #[test]
    fn remote_keys_canonicalize_header_names_and_ignore_local_hints() {
        let mut first = page("image");
        first
            .headers
            .insert("Authorization".into(), "Bearer one".into());
        let mut second = page("image");
        second
            .headers
            .insert("authorization".into(), "Bearer one".into());
        second.local_path = Some(PathBuf::from("an-old-local-cache-file"));
        assert_eq!(
            CacheManager::page_key(&first),
            CacheManager::page_key(&second)
        );
        second
            .headers
            .insert("authorization".into(), "Bearer two".into());
        assert_ne!(
            CacheManager::page_key(&first),
            CacheManager::page_key(&second)
        );
    }

    #[test]
    fn local_keys_include_the_complete_canonical_identity() {
        let directory = tempfile::tempdir().unwrap();
        let first = directory.path().join("a");
        let second = directory.path().join("b");
        fs::write(&first, b"same").unwrap();
        fs::write(&second, b"same").unwrap();
        let first = Page {
            local_path: Some(first),
            ..Page::default()
        };
        let second = Page {
            local_path: Some(second),
            ..Page::default()
        };
        assert_ne!(
            CacheManager::page_key(&first),
            CacheManager::page_key(&second)
        );
    }

    #[test]
    fn lru_uses_recent_hits_before_evicting_a_page() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 80);
        let first = page("first");
        let second = page("second");
        let third = page("third");
        publish(&cache, &first, &[1; 40], None);
        publish(&cache, &second, &[2; 40], None);
        assert!(cache.lookup_raw(&first).unwrap().is_some());
        publish(&cache, &third, &[3; 40], None);
        assert!(cache.lookup_raw(&first).unwrap().is_some());
        assert!(cache.lookup_raw(&second).unwrap().is_none());
        assert_eq!(cache.stats().bytes, 80);
        assert_eq!(cache.stats().evictions, 1);
    }

    #[test]
    fn pending_replacement_cannot_lend_uncommitted_shrinking_quota() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 100);
        let first = page("first");
        publish(&cache, &first, &[1; 80], None);
        publish(&cache, &page("second"), &[2; 20], None);
        let replacement = cache.begin_raw(&first, None, 20).unwrap();
        assert!(cache.begin_raw(&page("third"), None, 60).is_err());
        drop(replacement);
        assert!(cache.stats().unpinned_bytes <= 100);
    }

    #[test]
    fn cancelled_active_writes_do_not_recreate_download_pins() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 100);
        let write = cache
            .begin_raw(&page("first"), Some("download:cancelled"), 20)
            .unwrap();
        fs::write(write.temp_path(), [1; 20]).unwrap();
        cache.unpin_owners(&["download:cancelled".into()]).unwrap();
        write.commit().unwrap();
        assert_eq!(cache.stats().pinned_entries, 0);
    }

    #[test]
    fn low_space_and_clear_preserve_pinned_and_leased_files() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 100);
        let pinned = page("pinned");
        let path = publish(&cache, &pinned, &[1; 80], Some("download:keep"));
        let checksum = cache.fingerprint(&path).unwrap();
        let other = page("leased");
        let leased_path = publish(&cache, &other, &[2; 60], None);
        let lease = cache.lease_path(&leased_path).unwrap();
        cache
            .set_config(CacheConfig {
                max_unpinned_bytes: 100,
                min_free_bytes: u64::MAX,
            })
            .unwrap();
        assert!(cache.begin_raw(&page("cannot-fit"), None, 1).is_err());
        cache.clear_unpinned().unwrap();
        assert!(path.exists() && leased_path.exists());
        assert_eq!(cache.fingerprint(&path).unwrap(), checksum);
        drop(lease);
        cache.clear_unpinned().unwrap();
        assert!(!leased_path.exists());
        assert!(path.exists());
    }

    #[test]
    fn pinned_corruption_is_missed_and_atomic_refetch_keeps_owners() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 100);
        let request = page("pinned");
        let path = publish(&cache, &request, &[1; 20], Some("download:keep"));
        fs::write(&path, [2; 21]).unwrap();
        assert!(cache.lookup_raw(&request).unwrap().is_none());
        assert!(path.exists());
        let replacement = publish(&cache, &request, &[3; 22], None);
        assert_eq!(path, replacement);
        assert!(cache.lookup_raw(&request).unwrap().is_some());
        assert_eq!(cache.stats().pinned_entries, 1);
    }

    #[test]
    fn processed_artifacts_have_complete_render_identity() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 100);
        let key = render("source:checksum");
        let write = cache.begin_processed(&key, 80).unwrap();
        fs::write(write.temp_path(), b"checked-artifact").unwrap();
        let path = write.commit().unwrap();
        assert_eq!(cache.stats().bytes, 16);
        assert_eq!(cache.lookup_processed(&key).unwrap(), Some(path.clone()));
        let mut changed = key.clone();
        changed.offset = 10;
        assert!(cache.lookup_processed(&changed).unwrap().is_none());
        cache.invalidate_processed(&key).unwrap();
        assert!(!path.exists());
    }

    #[test]
    fn restart_reclaims_process_temporaries_and_retains_download_pins() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 100);
        publish(&cache, &page("download"), &[1; 20], Some("download:keep"));
        publish(
            &cache,
            &page("archive"),
            &[2; 20],
            Some(&format!("transient:archive:{}:item:1", std::process::id())),
        );
        let temp = cache.inner.root.join("temp").join(format!(
            "{}-999-{}.part",
            std::process::id(),
            "a".repeat(64)
        ));
        fs::write(&temp, b"abandoned").unwrap();
        let unrelated = cache.inner.root.join("temp/notes.txt");
        fs::write(&unrelated, b"keep").unwrap();
        drop(cache);
        let cache = manager(&directory, 100);
        assert!(!temp.exists());
        assert!(unrelated.exists());
        assert_eq!(cache.stats().pinned_entries, 1);
        cache.unpin_owners(&["download:keep".into()]).unwrap();
        assert_eq!(cache.stats().pinned_entries, 0);
    }

    #[test]
    fn storage_alias_classes_preserve_unknowns_and_follow_cover_reassignments() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let first = page("classified-source");
        let first_path = publish(&cache, &first, b"source", Some("download:classification"));
        let first_id = cache_id(Kind::Raw, &CacheManager::page_key(&first));
        let second_path = publish(&cache, &page("classified-other"), b"other", None);
        let second_id = cache_id(
            Kind::Raw,
            &CacheManager::page_key(&page("classified-other")),
        );
        let rendered_key = render("classified-cover");
        let write = cache.begin_processed(&rendered_key, 8).unwrap();
        fs::write(write.temp_path(), b"rendered").unwrap();
        let rendered_path = write.commit().unwrap();
        let rendered_id = cache_id(Kind::Processed, &render_key(&rendered_key).unwrap());
        let classes = || {
            cache
                .storage_artifacts_page(None, 128)
                .unwrap()
                .into_iter()
                .map(|artifact| (artifact.id, (artifact.pinned, artifact.cover)))
                .collect::<HashMap<_, _>>()
        };
        assert_eq!(classes()[&first_id], (true, Some(false)));
        cache
            .set_alias("cover-source-v2:classified", &first_path)
            .unwrap();
        cache
            .set_alias("cover-render-v2:classified", &rendered_path)
            .unwrap();
        cache
            .set_alias("not-cover-source-v2:classified", &second_path)
            .unwrap();
        let old_alias = "historical-private-alias";
        cache
            .lock()
            .unwrap()
            .database
            .execute(
                "INSERT INTO aliases(alias_key,artifact_id) VALUES(?1,?2)",
                params![alias_key(old_alias), second_id],
            )
            .unwrap();
        assert_eq!(classes()[&first_id], (true, Some(true)));
        assert_eq!(classes()[&rendered_id], (false, Some(true)));
        assert_eq!(classes()[&second_id], (false, None));
        // A known cover proves classification even beside an old hashed alias.
        cache
            .set_alias("cover-source-v2:classified", &second_path)
            .unwrap();
        assert_eq!(classes()[&first_id], (true, Some(false)));
        assert_eq!(classes()[&second_id], (false, Some(true)));
        cache
            .set_alias("cover-source-v2:classified", &first_path)
            .unwrap();
        assert_eq!(classes()[&second_id], (false, None));
        cache.set_alias(old_alias, &second_path).unwrap();
        assert_eq!(classes()[&second_id], (false, Some(false)));
        assert_totals_match_index(&cache);
    }

    #[test]
    fn historical_alias_schema_migrates_without_guessing_cover_names() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let path = publish(
            &cache,
            &page("legacy-cover"),
            b"legacy bytes",
            Some("download:legacy"),
        );
        let alias = "cover-source-v2:private-legacy-identity";
        cache.set_alias(alias, &path).unwrap();
        let index = cache.namespace().join("index.sqlite3");
        drop(cache);
        {
            let database = Connection::open(&index).unwrap();
            database
                .execute_batch(
                    "BEGIN IMMEDIATE;
                 DROP INDEX alias_artifact_cover;
                 ALTER TABLE aliases RENAME TO aliases_with_classification;
                 CREATE TABLE aliases (
                     alias_key TEXT PRIMARY KEY,
                     artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE
                 );
                 INSERT INTO aliases(alias_key,artifact_id)
                     SELECT alias_key,artifact_id FROM aliases_with_classification;
                 DROP TABLE aliases_with_classification;
                 CREATE INDEX alias_artifact ON aliases(artifact_id);
                 COMMIT;",
                )
                .unwrap();
        }
        let cache = manager(&directory, 4096);
        let artifacts = cache.storage_artifacts_page(None, 128).unwrap();
        assert_eq!(artifacts.len(), 1);
        assert_eq!(artifacts[0].path, path);
        assert!(artifacts[0].pinned);
        assert_eq!(artifacts[0].cover, None);
        assert_eq!(fs::read(&path).unwrap(), b"legacy bytes");
        {
            let state = cache.lock().unwrap();
            assert!(alias_classification_exists(&state.database).unwrap());
            let stored = state
                .database
                .query_row("SELECT alias_key,cover FROM aliases", [], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, Option<bool>>(1)?))
                })
                .unwrap();
            assert_eq!(stored, (alias_key(alias), None));
            assert!(!stored.0.contains("private-legacy-identity"));
            let index_exists = state.database.query_row(
                "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='index' AND name='alias_artifact_cover')", [],
                |row| row.get::<_, bool>(0),
            ).unwrap();
            assert!(index_exists);
            state
                .database
                .execute_batch(
                    "CREATE TEMP TABLE observed_alias_updates(count INTEGER NOT NULL);
                 INSERT INTO observed_alias_updates VALUES(0);
                 CREATE TEMP TRIGGER observe_alias_updates AFTER UPDATE ON aliases
                 BEGIN UPDATE observed_alias_updates SET count=count+1; END;",
                )
                .unwrap();
        }
        assert_eq!(cache.lookup_alias(alias).unwrap(), Some(path.clone()));
        assert_eq!(
            cache.storage_artifacts_page(None, 1).unwrap()[0].cover,
            Some(true)
        );
        let promoted_changes = cache
            .lock()
            .unwrap()
            .database
            .query_row("SELECT count FROM observed_alias_updates", [], |row| {
                row.get::<_, u64>(0)
            })
            .unwrap();
        assert_eq!(promoted_changes, 1);
        assert_eq!(cache.lookup_alias(alias).unwrap(), Some(path.clone()));
        let warm_changes = cache
            .lock()
            .unwrap()
            .database
            .query_row("SELECT count FROM observed_alias_updates", [], |row| {
                row.get::<_, u64>(0)
            })
            .unwrap();
        assert_eq!(warm_changes, promoted_changes);
        drop(cache);
        let cache = manager(&directory, 4096);
        assert_eq!(
            cache.storage_artifacts_page(None, 1).unwrap()[0].cover,
            Some(true)
        );
        assert_totals_match_index(&cache);
    }

    #[test]
    fn storage_pages_are_bounded_keyset_reads_without_access_or_sql_writes() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        {
            let mut state = cache.lock().unwrap();
            let transaction = state.database.transaction().unwrap();
            for index in 0..131 {
                let key = format!("{index:064x}");
                transaction.execute(
                    "INSERT INTO artifacts(id,kind,cache_key,size,checksum,fingerprint,last_access,valid)
                     VALUES(?1,'raw',?2,1,'','',0,?3)",
                    params![cache_id(Kind::Raw, &key), key, index % 2 == 0],
                ).unwrap();
            }
            transaction.commit().unwrap();
            state.refresh_totals().unwrap();
        }
        let before = cache.stats();
        let changes_before = cache
            .lock()
            .unwrap()
            .database
            .query_row("SELECT total_changes()", [], |row| row.get::<_, u64>(0))
            .unwrap();
        let observer = CommitObserver::attach(&cache);
        assert!(cache.storage_artifacts_page(None, 0).unwrap().is_empty());
        let first = cache.storage_artifacts_page(None, usize::MAX).unwrap();
        assert_eq!(first.len(), 128);
        assert!(first.windows(2).all(|pair| pair[0].id < pair[1].id));
        assert!(
            first
                .iter()
                .all(|artifact| !artifact.pinned && artifact.cover == Some(false))
        );
        let after = first.last().unwrap().id.as_str();
        let second = cache.storage_artifacts_page(Some(after), 128).unwrap();
        assert_eq!(second.len(), 3);
        assert!(second.iter().all(|artifact| artifact.id.as_str() > after));
        assert!(
            cache
                .storage_artifacts_page(Some(&second.last().unwrap().id), 128)
                .unwrap()
                .is_empty()
        );
        assert_eq!(cache.storage_artifacts_page(None, 1).unwrap().len(), 1);
        assert_eq!(observer.attempts(), 0);
        let after = cache.stats();
        assert_eq!(after.hits, before.hits);
        assert_eq!(after.misses, before.misses);
        assert_eq!(after.metadata_entries, before.metadata_entries);
        {
            let state = cache.lock().unwrap();
            let changes = state
                .database
                .query_row("SELECT total_changes()", [], |row| row.get::<_, u64>(0))
                .unwrap();
            assert_eq!(changes, changes_before);
            assert!(state.access_dirty.is_empty());
        }
        assert_totals_match_index(&cache);
    }

    #[test]
    fn storage_snapshots_do_not_validate_artifact_directories_or_files() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let path = publish(
            &cache,
            &page("storage-candidate-only"),
            b"candidate bytes",
            None,
        );
        let bucket = path.parent().unwrap();
        fs::remove_file(&path).unwrap();
        fs::remove_dir(bucket).unwrap();
        fs::write(bucket, b"not an artifact directory").unwrap();
        let artifacts = cache.storage_artifacts_page(None, 128).unwrap();
        assert_eq!(artifacts.len(), 1);
        assert_eq!(artifacts[0].path, path);
        assert_eq!(artifacts[0].cover, Some(false));
        assert_eq!(fs::read(bucket).unwrap(), b"not an artifact directory");
        assert_eq!(cache.stats().corruptions, 0);
    }

    #[test]
    fn storage_snapshots_reject_invalid_index_paths_before_joining() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 4096);
        let request = page("invalid-storage-key");
        publish(&cache, &request, b"original bytes", None);
        let id = cache_id(Kind::Raw, &CacheManager::page_key(&request));
        cache
            .lock()
            .unwrap()
            .database
            .execute(
                "UPDATE artifacts SET cache_key='../outside' WHERE id=?1",
                [&id],
            )
            .unwrap();
        assert!(cache.storage_artifacts_page(None, 128).is_err());
        cache
            .lock()
            .unwrap()
            .database
            .execute(
                "UPDATE artifacts SET cache_key=?1 WHERE id=?2",
                params!["0".repeat(64), id],
            )
            .unwrap();
        assert!(cache.storage_artifacts_page(None, 128).is_err());
    }

    #[test]
    fn hashed_aliases_survive_reopen_without_persisting_cover_credentials() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 100);
        let path = publish(&cache, &page("cover"), b"cover bytes", None);
        let alias = "book:1:https://example.test/cover?token=private-alias-token";
        cache.set_alias(alias, &path).unwrap();
        drop(cache);
        let cache = manager(&directory, 100);
        assert_eq!(cache.lookup_alias(alias).unwrap(), Some(path));
        let stored = cache
            .lock()
            .unwrap()
            .database
            .query_row("SELECT alias_key FROM aliases", [], |row| {
                row.get::<_, String>(0)
            })
            .unwrap();
        assert_eq!(stored, alias_key(alias));
        assert!(!stored.contains("private-alias-token"));
    }

    #[test]
    fn aliases_follow_lru_and_clear_without_retaining_or_deleting_external_files() {
        let directory = tempfile::tempdir().unwrap();
        let cache = manager(&directory, 8);
        let external = directory.path().join("user-cover.png");
        fs::write(&external, b"user data").unwrap();
        assert!(cache.set_alias("external", &external).is_err());
        let first = publish(&cache, &page("first-cover"), b"four", None);
        cache.set_alias("first", &first).unwrap();
        let second = publish(&cache, &page("second-cover"), b"eight888", None);
        assert!(cache.lookup_alias("first").unwrap().is_none());
        cache.set_alias("second", &second).unwrap();
        let lease = cache.lease_path(&second).unwrap();
        cache.clear_unpinned().unwrap();
        assert!(cache.lookup_alias("second").unwrap().is_some());
        drop(lease);
        cache.clear_unpinned().unwrap();
        assert!(cache.lookup_alias("second").unwrap().is_none());
        assert!(external.exists());
    }

    #[cfg(unix)]
    #[test]
    fn a_linked_index_cannot_modify_a_database_outside_the_cache() {
        use std::os::unix::fs::symlink;
        let directory = tempfile::tempdir().unwrap();
        let outside = directory.path().join("library.sqlite3");
        fs::write(&outside, b"original database bytes").unwrap();
        let root = directory.path().join("cache/shared-v1");
        fs::create_dir_all(&root).unwrap();
        symlink(&outside, root.join("index.sqlite3")).unwrap();
        assert!(CacheManager::open(directory.path()).is_err());
        assert_eq!(fs::read(outside).unwrap(), b"original database bytes");
    }
}
