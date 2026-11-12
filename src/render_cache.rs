//! Bounded screen-sized grayscale rendering for background workers.
//!
//! The raw artifact remains canonical. A processed PNG is a disposable derivative
//! containing its own geometry metadata. Reader frames use BiliComics' in-memory
//! Raster preparation and never wait for derivative encoding or publication.
//! A shared decoded LRU also preserves
//! prefetched pixels, so a resident viewport needs neither source nor PNG
//! decoding. Network scheduling and raw-file validation belong to the source
//! manager, while this module coalesces identical cold renders.

use std::{
    collections::HashMap,
    fs::{self, File, OpenOptions},
    io::{BufReader, BufWriter, Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard, OnceLock, TryLockError, Weak},
    time::Duration,
};

use anyhow::{Context, Result, anyhow, ensure};
use image::GrayImage;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::{
    cache::{CacheLease, CacheManager, CacheWrite, RenderKey},
    domain::Page,
    imaging::{self, ImageOptions, MAX_DECODE_BYTES, MAX_PAGE_BYTES, MAX_PAGE_PIXELS},
};

const FORMAT_VERSION: u8 = 1;
const METADATA_KEY: &str = "AidokuRender";
const MAX_METADATA_BYTES: usize = 2_048;
const MAX_TARGET_SIDE: u32 = 8_192;
const WRITE_BUFFER_BYTES: usize = 64 * 1024;
const READER_RASTER_VERSION: &str = "bili-triangle-bayer-raster-v1";
/// Retained grayscale buffers only. Live readers can keep an evicted Arc alive;
/// diagnostics report those output buffers separately from the retained budget.
pub const DECODED_CACHE_BYTES: usize = 12 * 1024 * 1024;
const MAX_DECODED_ENTRIES: usize = 16;

type RenderGate = Arc<Mutex<()>>;
static RENDER_GATES: OnceLock<Mutex<HashMap<DecodedKey, Weak<Mutex<()>>>>> = OnceLock::new();
static DECODED: OnceLock<Mutex<DecodedCache>> = OnceLock::new();

#[cfg(test)]
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct RenderWork {
    processed_decodes: u64,
    output_allocations: u64,
    output_allocated_bytes: u64,
    staged_validations: u64,
    prefetch_memory_hits: u64,
}

#[cfg(test)]
thread_local! {
    static RENDER_WORK: std::cell::Cell<RenderWork> = std::cell::Cell::new(RenderWork::default());
}

#[cfg(test)]
fn count_work(update: impl FnOnce(&mut RenderWork)) {
    RENDER_WORK.with(|work| {
        let mut current = work.get();
        update(&mut current);
        work.set(current);
    });
}

#[derive(Debug, Clone)]
pub struct RenderedPage {
    pub image: Arc<GrayImage>,
    pub cache_path: PathBuf,
    pub scaled_height: u32,
    pub viewport_height: u32,
    pub cache_hit: bool,
    pub memory_hit: bool,
    /// The source identity that produced these pixels, never a later snapshot.
    pub source_proof: Option<SourceProof>,
    /// Full-raster pixels and their sealed key before any caller selects a half.
    pub retained_raster: Option<RetainedRaster>,
}

/// A completed Reader raster that can move from the bounded next-frame slot
/// into the existing grayscale LRU. Its key and pixels cannot be substituted
/// independently, including when a caller crops the public display image.
#[derive(Clone)]
pub struct RetainedRaster {
    key: DecodedKey,
    image: Arc<GrayImage>,
    cache_path: PathBuf,
    scaled_height: u32,
    viewport_height: u32,
    proof: SourceProof,
    source: Option<(SourceKey, imaging::ReaderSource)>,
}

impl std::fmt::Debug for RetainedRaster {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RetainedRaster")
            .field("dimensions", &self.image.dimensions())
            .field("retained_bytes", &self.retained_bytes())
            .finish_non_exhaustive()
    }
}

impl RetainedRaster {
    fn new(
        key: DecodedKey,
        page: &RenderedPage,
        proof: SourceProof,
        mut source: Option<(SourceKey, imaging::ReaderSource)>,
    ) -> Self {
        if source.as_ref().is_some_and(|(_, source)| {
            source.allocation_bytes()
                > DECODED_CACHE_BYTES.saturating_sub(page.image.as_raw().capacity())
        }) {
            // A source that cannot be admitted with its viewport has no reuse
            // benefit in this pool. Do not retain it in the next-frame budget.
            source = None;
        }
        Self {
            key,
            image: page.image.clone(),
            cache_path: page.cache_path.clone(),
            scaled_height: page.scaled_height,
            viewport_height: page.viewport_height,
            proof,
            source,
        }
    }

    pub fn retained_bytes(&self) -> usize {
        Self::unique_retained_bytes(std::slice::from_ref(self))
    }

    /// Count each immutable pixel allocation once across a spread or clones.
    pub fn unique_retained_bytes(rasters: &[Self]) -> usize {
        let mut allocations = HashMap::new();
        for raster in rasters {
            allocations.insert(
                Arc::as_ptr(&raster.image) as usize,
                raster.image.as_raw().capacity(),
            );
            if let Some((_, source)) = &raster.source {
                allocations.insert(source.allocation_identity(), source.allocation_bytes());
            }
        }
        allocations
            .values()
            .copied()
            .fold(0_usize, usize::saturating_add)
    }

    /// Move valid pixels into the history LRU without source I/O. The caller
    /// must freshly verify every frame SourceProof before publishing the frame.
    /// Cache admission itself grants no permission to skip future verification.
    pub fn promote(&self, cache: &CacheManager) -> Result<()> {
        ensure!(
            self.key.namespace == cache.namespace(),
            "The retained Raster belongs to another data directory"
        );
        decoded(|state| self.promote_into(state));
        Ok(())
    }

    fn promote_into(&self, state: &mut DecodedCache) {
        let page = RenderedPage {
            image: self.image.clone(),
            cache_path: self.cache_path.clone(),
            scaled_height: self.scaled_height,
            viewport_height: self.viewport_height,
            cache_hit: true,
            memory_hit: true,
            source_proof: Some(self.proof.clone()),
            retained_raster: None,
        };
        if let Some((key, source)) = &self.source {
            state.remember_source(key.clone(), source.clone(), self.image.as_raw().capacity());
        }
        state.remember(
            self.key.clone(),
            &page,
            self.proof.checksum.clone(),
            RenderPurpose::Foreground,
        );
    }
}

/// A sealed identity of the encoded source used to prepare a complete frame.
/// Metadata alone cannot validate a mutable local file. A ready-frame worker
/// must call verify() before reusing its pixels for a later Reader request.
#[derive(Debug, Clone)]
pub struct SourceProof {
    requested_path: PathBuf,
    source_path: PathBuf,
    checksum: String,
    bytes: u64,
    page: Page,
    page_identity: String,
}

impl SourceProof {
    pub fn source_path(&self) -> &Path {
        &self.source_path
    }

    pub fn matches_page(&self, page: &Page) -> bool {
        self.page_identity == CacheManager::page_key(page)
    }

    /// Keep a managed source from being evicted while a bounded next frame owns
    /// it. External source files remain mutable and still require verify().
    pub fn lease_source(&self, cache: &CacheManager) -> Result<CacheLease> {
        cache.lease_path(&self.source_path)
    }

    pub fn verify(&self, cache: &CacheManager) -> Result<bool> {
        imaging::check_decode_cancelled()?;
        let Ok(path) = fs::canonicalize(&self.requested_path) else {
            return Ok(false);
        };
        if path != self.source_path || !self.matches_page(&self.page) {
            return Ok(false);
        }
        let Ok(metadata) = fs::metadata(&path) else {
            return Ok(false);
        };
        if !metadata.is_file() || metadata.len() != self.bytes {
            return Ok(false);
        }
        // CacheManager freshly hashes external bytes and applies its validated
        // private-artifact identity checks to atomically published cache files.
        let Ok(checksum) = cache.fingerprint(&path) else {
            return Ok(false);
        };
        imaging::check_decode_cancelled()?;
        Ok(checksum == self.checksum
            && self.matches_page(&self.page)
            && fs::canonicalize(&self.requested_path)
                .is_ok_and(|current| current == self.source_path))
    }

    fn for_source(page: &Page, raw: &Path, checksum: &str, bytes: u64) -> Result<Self> {
        let requested_path =
            std::path::absolute(raw).context("Resolve the requested Reader source path")?;
        Ok(Self {
            source_path: fs::canonicalize(&requested_path)
                .context("Resolve the prepared Reader source")?,
            requested_path,
            checksum: checksum.into(),
            bytes,
            page: page.clone(),
            page_identity: CacheManager::page_key(page),
        })
    }
}

/// Counters cover retained grayscale outputs and immutable vertical sources,
/// not decoder scratch space, the UI's RGB buffer, or total resident memory.
#[derive(Debug, Clone, Default, Serialize)]
pub struct RenderStats {
    pub foreground_requests: u64,
    pub prefetch_requests: u64,
    pub alias_requests: u64,
    pub memory_hits: u64,
    pub prefetch_memory_hits: u64,
    pub disk_hits: u64,
    pub cold_renders: u64,
    pub raster_requests: u64,
    pub raster_cold_renders: u64,
    pub raster_memory_hits: u64,
    pub raster_persistence_skips: u64,
    pub source_memory_hits: u64,
    pub source_cold_decodes: u64,
    pub source_memory_entries: u64,
    pub source_memory_bytes: u64,
    pub source_memory_evictions: u64,
    pub source_oversized_skips: u64,
    /// Full-output processed PNG decode attempts. Row validation is separate.
    pub processed_decodes: u64,
    pub processed_decode_failures: u64,
    /// Bytes allocated for processed PNG output buffers, including failed reads.
    pub processed_decoded_bytes: u64,
    /// Row-based staged PNG validation, including inflate and final chunk checks.
    pub staged_validations: u64,
    pub output_allocations: u64,
    pub output_allocated_bytes: u64,
    pub memory_bytes: u64,
    pub peak_memory_bytes: u64,
    pub memory_entries: u64,
    pub memory_evictions: u64,
    pub memory_invalidations: u64,
    pub oversized_skips: u64,
    pub live_output_bytes: u64,
    pub peak_live_output_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct DecodedKey {
    namespace: PathBuf,
    render: RenderKey,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct SourceKey {
    namespace: PathBuf,
    checksum: String,
    encoded_bytes: u64,
}

struct SourceEntry {
    source: imaging::ReaderSource,
    bytes: usize,
    access: u64,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum RenderPurpose {
    Foreground,
    Prefetch,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum RenderMode {
    Persistent,
    ReaderRaster,
    ReaderNextFrame,
}

impl RenderMode {
    fn is_reader(self) -> bool {
        self != Self::Persistent
    }
}

struct DecodedEntry {
    page: RenderedPage,
    checksum: String,
    bytes: usize,
    access: u64,
    prefetched: bool,
}

struct LiveOutput {
    image: Weak<GrayImage>,
    bytes: usize,
}

#[derive(Default)]
struct DecodedCache {
    entries: HashMap<DecodedKey, DecodedEntry>,
    sources: HashMap<SourceKey, SourceEntry>,
    bytes: usize,
    access: u64,
    live: Vec<LiveOutput>,
    stats: RenderStats,
}

impl DecodedCache {
    fn remove_source(&mut self, key: &SourceKey) -> bool {
        if let Some(entry) = self.sources.remove(key) {
            self.bytes -= entry.bytes;
            return true;
        }
        false
    }

    fn evict_oldest(&mut self) -> bool {
        let output = self
            .entries
            .iter()
            .min_by_key(|(_, entry)| entry.access)
            .map(|(key, entry)| (key.clone(), entry.access));
        let source = self
            .sources
            .iter()
            .min_by_key(|(_, entry)| entry.access)
            .map(|(key, entry)| (key.clone(), entry.access));
        match (output, source) {
            (Some((_, access)), Some((source, source_access))) if source_access < access => {
                self.remove_source(&source);
                self.stats.source_memory_evictions += 1;
            }
            (Some((key, _)), _) => {
                self.remove(&key);
            }
            (None, Some((key, _))) => {
                self.remove_source(&key);
                self.stats.source_memory_evictions += 1;
            }
            (None, None) => return false,
        }
        self.stats.memory_evictions += 1;
        true
    }

    fn lookup_source(&mut self, key: &SourceKey) -> Option<imaging::ReaderSource> {
        let entry = self.sources.get_mut(key)?;
        self.access = self.access.saturating_add(1);
        entry.access = self.access;
        self.stats.source_memory_hits += 1;
        Some(entry.source.clone())
    }

    fn peek_source(&mut self, key: &SourceKey) -> Option<imaging::ReaderSource> {
        let source = self.sources.get(key)?.source.clone();
        self.stats.source_memory_hits += 1;
        Some(source)
    }

    fn remember_source(
        &mut self,
        key: SourceKey,
        source: imaging::ReaderSource,
        viewport_bytes: usize,
    ) {
        let bytes = source.allocation_bytes();
        let (width, height) = source.dimensions();
        if width == 0
            || height == 0
            || u64::from(width) * u64::from(height) > MAX_PAGE_PIXELS
            || key.encoded_bytes == 0
            || key.encoded_bytes > MAX_PAGE_BYTES
            || bytes > DECODED_CACHE_BYTES.saturating_sub(viewport_bytes)
        {
            self.stats.source_oversized_skips += 1;
            return;
        }
        self.remove_source(&key);
        while bytes > DECODED_CACHE_BYTES.saturating_sub(self.bytes)
            || self.entries.len() + self.sources.len() >= MAX_DECODED_ENTRIES
        {
            if !self.evict_oldest() {
                break;
            }
        }
        self.access = self.access.saturating_add(1);
        self.bytes += bytes;
        self.stats.peak_memory_bytes = self.stats.peak_memory_bytes.max(self.bytes as u64);
        self.sources.insert(
            key,
            SourceEntry {
                source,
                bytes,
                access: self.access,
            },
        );
    }

    fn remove(&mut self, key: &DecodedKey) -> bool {
        if let Some(entry) = self.entries.remove(key) {
            self.bytes -= entry.bytes;
            return true;
        }
        false
    }

    fn invalidate(&mut self, key: &DecodedKey) {
        if self.remove(key) {
            self.stats.memory_invalidations += 1;
        }
    }

    fn lookup(
        &mut self,
        key: &DecodedKey,
        path: &Path,
        checksum: &str,
        purpose: RenderPurpose,
    ) -> Option<RenderedPage> {
        if self
            .entries
            .get(key)
            .is_some_and(|entry| entry.page.cache_path != path || entry.checksum != checksum)
        {
            self.invalidate(key);
        }
        let entry = self.entries.get_mut(key)?;
        self.access = self.access.saturating_add(1);
        entry.access = self.access;
        self.stats.memory_hits += 1;
        if entry.prefetched && purpose == RenderPurpose::Foreground {
            self.stats.prefetch_memory_hits += 1;
            #[cfg(test)]
            count_work(|work| work.prefetch_memory_hits += 1);
            entry.prefetched = false;
        }
        let mut page = entry.page.clone();
        page.cache_hit = true;
        page.memory_hit = true;
        Some(page)
    }

    fn peek(&mut self, key: &DecodedKey, path: &Path, checksum: &str) -> Option<RenderedPage> {
        if self
            .entries
            .get(key)
            .is_some_and(|entry| entry.page.cache_path != path || entry.checksum != checksum)
        {
            self.invalidate(key);
        }
        let mut page = self.entries.get(key)?.page.clone();
        self.stats.memory_hits += 1;
        // Speculation reuses pixels without changing the displayed-history LRU.
        page.cache_hit = true;
        page.memory_hit = true;
        Some(page)
    }

    fn remember(
        &mut self,
        key: DecodedKey,
        page: &RenderedPage,
        checksum: String,
        purpose: RenderPurpose,
    ) {
        self.remove(&key);
        let bytes = page.image.as_raw().capacity();
        if bytes > DECODED_CACHE_BYTES {
            self.stats.oversized_skips += 1;
            return;
        }
        while self.bytes + bytes > DECODED_CACHE_BYTES
            || self.entries.len() + self.sources.len() >= MAX_DECODED_ENTRIES
        {
            if !self.evict_oldest() {
                break;
            }
        }
        self.access = self.access.saturating_add(1);
        self.bytes += bytes;
        self.stats.peak_memory_bytes = self.stats.peak_memory_bytes.max(self.bytes as u64);
        let mut page = page.clone();
        // A next-frame handle can also own its unrotated source. Cached output
        // entries retain only their counted output Arc; sources have their own
        // counted entry in this same pool.
        page.retained_raster = None;
        self.entries.insert(
            key,
            DecodedEntry {
                page,
                checksum,
                bytes,
                access: self.access,
                prefetched: purpose == RenderPurpose::Prefetch,
            },
        );
    }

    fn track_output(&mut self, image: &Arc<GrayImage>) {
        let bytes = image.as_raw().capacity();
        self.stats.output_allocations += 1;
        self.stats.output_allocated_bytes += bytes as u64;
        #[cfg(test)]
        count_work(|work| {
            work.output_allocations += 1;
            work.output_allocated_bytes += bytes as u64;
        });
        self.live.push(LiveOutput {
            image: Arc::downgrade(image),
            bytes,
        });
        let live_bytes = self.live_output_bytes();
        self.stats.peak_live_output_bytes = self.stats.peak_live_output_bytes.max(live_bytes);
    }

    fn live_output_bytes(&mut self) -> u64 {
        self.live.retain(|entry| entry.image.strong_count() > 0);
        self.live.iter().map(|entry| entry.bytes as u64).sum()
    }

    fn snapshot(&mut self) -> RenderStats {
        let live_bytes = self.live_output_bytes();
        let mut stats = self.stats.clone();
        stats.memory_bytes = self.bytes as u64;
        stats.memory_entries = (self.entries.len() + self.sources.len()) as u64;
        stats.source_memory_entries = self.sources.len() as u64;
        stats.source_memory_bytes = self.sources.values().map(|entry| entry.bytes as u64).sum();
        stats.live_output_bytes = live_bytes;
        stats
    }
}

fn decoded<T>(operation: impl FnOnce(&mut DecodedCache) -> T) -> T {
    let mut state = DECODED
        .get_or_init(|| Mutex::new(DecodedCache::default()))
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    operation(&mut state)
}

pub fn stats() -> RenderStats {
    decoded(DecodedCache::snapshot)
}

/// Release retained derivatives for one data directory. Existing reader Arcs
/// remain valid, and no disk artifact or download pin is changed.
pub fn clear_memory(cache: &CacheManager) {
    decoded(|state| {
        let keys = state
            .entries
            .keys()
            .filter(|key| key.namespace == cache.namespace())
            .cloned()
            .collect::<Vec<_>>();
        for key in keys {
            state.invalidate(&key);
        }
        let sources = state
            .sources
            .keys()
            .filter(|key| key.namespace == cache.namespace())
            .cloned()
            .collect::<Vec<_>>();
        for key in sources {
            if state.remove_source(&key) {
                state.stats.memory_invalidations += 1;
            }
        }
    });
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Metadata {
    version: u8,
    key: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    render_key: Option<RenderKey>,
    scaled_height: u32,
    viewport_height: u32,
}

/// Recover a processed cover in a background worker when its canonical source
/// is unavailable. The caller must namespace the alias by the complete source
/// configuration and cover URI, and try a present source before this fallback.
pub fn load_alias(
    cache: &CacheManager,
    alias: &str,
    options: &ImageOptions,
) -> Result<Option<RenderedPage>> {
    decoded(|state| state.stats.alias_requests += 1);
    check_options(options)?;
    let Some(path) = cache.lookup_alias(alias)? else {
        return Ok(None);
    };
    let Ok(lease) = cache.lease_path(&path) else {
        return Ok(None);
    };
    // Older derivatives have only a key digest. They remain valid when the
    // canonical source supplies the key, but cannot independently prove cover
    // options after that source has been evicted.
    let metadata = match processed_reader(&path, None) {
        Ok((_, metadata)) => metadata,
        Err(_) => {
            invalidate_decoded_path(cache, &path);
            return Ok(None);
        }
    };
    let key = metadata
        .render_key
        .context("The processed alias has no independent rendering key")?;
    if key.width != options.width
        || key.height != options.height
        || key.crop_borders != options.crop_borders
        || key.dither != options.dither
        || key.contrast != options.contrast
        || key.vertical
        || key.offset != 0
        || key.zoom_milli != 1_000
    {
        return Ok(None);
    }
    let Some((checked_path, checksum)) = cache.lookup_processed_with_fingerprint(&key)? else {
        invalidate_decoded_path(cache, &path);
        return Ok(None);
    };
    // Aliases can also name raw artifacts. Only the exact validated processed
    // artifact for the in-band key is eligible for this source-free path.
    if checked_path != path {
        return Ok(None);
    }
    let decoded_key = DecodedKey {
        namespace: cache.namespace().to_path_buf(),
        render: key.clone(),
    };
    if let Some(page) =
        decoded(|state| state.lookup(&decoded_key, &path, &checksum, RenderPurpose::Foreground))
    {
        return Ok(Some(page));
    }
    let rendered = decode_processed(&path, &key);
    if let Ok(page) = &rendered {
        decoded(|state| {
            state.stats.disk_hits += 1;
            state.remember(
                decoded_key.clone(),
                page,
                checksum,
                RenderPurpose::Foreground,
            );
        });
    }
    drop(lease);
    match rendered {
        Ok(page) => Ok(Some(page)),
        Err(error) if imaging::is_decode_cancelled(&error) => Err(error),
        Err(_) => {
            decoded(|state| state.invalidate(&decoded_key));
            cache.invalidate_processed(&key)?;
            Ok(None)
        }
    }
}

fn invalidate_decoded_path(cache: &CacheManager, path: &Path) {
    decoded(|state| {
        let keys = state
            .entries
            .iter()
            .filter(|(key, entry)| {
                key.namespace == cache.namespace() && entry.page.cache_path == path
            })
            .map(|(key, _)| key.clone())
            .collect::<Vec<_>>();
        for key in keys {
            state.invalidate(&key);
        }
    });
}

/// Render in a background worker. Resident hits reuse the prepared pixel Arc;
/// disk hits decode only the bounded processed PNG.
pub fn render(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
) -> Result<RenderedPage> {
    render_with_source(
        cache,
        page,
        raw,
        options,
        vertical,
        offset,
        zoom,
        read_source,
    )
}

/// Prepare the Reader's BiliComics Raster in memory. The canonical source and a
/// proof tied to its decoded bytes accompany the frame; no processed PNG is
/// encoded, inflated again or committed before display.
pub fn render_raster(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
) -> Result<RenderedPage> {
    render_for_purpose(
        cache,
        page,
        raw,
        options,
        vertical,
        offset,
        zoom,
        RenderPurpose::Foreground,
        RenderMode::ReaderRaster,
        read_source,
    )
}

pub fn prefetch_raster(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
) -> Result<RenderedPage> {
    imaging::with_background_decode(None, || {
        render_for_purpose(
            cache,
            page,
            raw,
            options,
            vertical,
            offset,
            zoom,
            RenderPurpose::Prefetch,
            RenderMode::ReaderRaster,
            read_source,
        )
    })
}

pub fn prefetch_raster_cancellable(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    cancellation: &imaging::DecodeCancellation,
) -> Result<RenderedPage> {
    imaging::with_background_decode(Some(cancellation), || {
        render_for_purpose(
            cache,
            page,
            raw,
            options,
            vertical,
            offset,
            zoom,
            RenderPurpose::Prefetch,
            RenderMode::ReaderRaster,
            read_source,
        )
    })
}

/// Prepare the nearest private NextFrame without admitting its future pixels
/// to the displayed-history LRU. The slot accounts for its returned retention
/// handle; accepting the frame promotes that handle after fresh proof checks.
pub fn prefetch_next_raster_cancellable(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    cancellation: &imaging::DecodeCancellation,
) -> Result<RenderedPage> {
    imaging::with_background_decode(Some(cancellation), || {
        render_for_purpose(
            cache,
            page,
            raw,
            options,
            vertical,
            offset,
            zoom,
            RenderPurpose::Prefetch,
            RenderMode::ReaderNextFrame,
            read_source,
        )
    })
}

/// Prepare the same output as render(), preserving it for later display when it
/// fits the decoded budget. The return value also allows explicit pinning by a
/// bounded caller; the LRU itself never leases the disk artifact.
pub fn prefetch(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
) -> Result<RenderedPage> {
    imaging::with_background_decode(None, || {
        render_for_purpose(
            cache,
            page,
            raw,
            options,
            vertical,
            offset,
            zoom,
            RenderPurpose::Prefetch,
            RenderMode::Persistent,
            read_source,
        )
    })
}

/// Stop obsolete preparation before decoding or while waiting for its shared
/// budget. A completed valid image can still be published for a new reader.
pub fn prefetch_cancellable(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    cancellation: &imaging::DecodeCancellation,
) -> Result<RenderedPage> {
    imaging::with_background_decode(Some(cancellation), || {
        render_for_purpose(
            cache,
            page,
            raw,
            options,
            vertical,
            offset,
            zoom,
            RenderPurpose::Prefetch,
            RenderMode::Persistent,
            read_source,
        )
    })
}

fn render_with_source(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    source: impl FnOnce(&Path) -> Result<Vec<u8>>,
) -> Result<RenderedPage> {
    render_for_purpose(
        cache,
        page,
        raw,
        options,
        vertical,
        offset,
        zoom,
        RenderPurpose::Foreground,
        RenderMode::Persistent,
        source,
    )
}

fn render_for_purpose(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    purpose: RenderPurpose,
    mode: RenderMode,
    source: impl FnOnce(&Path) -> Result<Vec<u8>>,
) -> Result<RenderedPage> {
    imaging::check_decode_cancelled()?;
    decoded(|state| match purpose {
        RenderPurpose::Foreground => state.stats.foreground_requests += 1,
        RenderPurpose::Prefetch => state.stats.prefetch_requests += 1,
    });
    if mode.is_reader() {
        decoded(|state| state.stats.raster_requests += 1);
    }
    check_options(options)?;
    let zoom_milli = normalize_zoom(zoom)?;
    let _raw_lease = cache.lease_path(raw)?;
    let metadata = fs::metadata(raw).context("Read the raw comic metadata")?;
    ensure!(
        metadata.is_file() && metadata.len() > 0 && metadata.len() <= MAX_PAGE_BYTES,
        "The raw comic is empty or exceeds the compressed-image limit"
    );
    let source_checksum = cache.fingerprint(raw)?;
    let source_proof = SourceProof::for_source(page, raw, &source_checksum, metadata.len())?;
    let source_key = format!("{}:{source_checksum}", CacheManager::page_key(page));
    let key = RenderKey {
        source_key: if mode.is_reader() {
            format!("{READER_RASTER_VERSION}:{source_key}")
        } else {
            source_key
        },
        width: options.width,
        height: options.height,
        crop_borders: options.crop_borders,
        dither: options.dither,
        contrast: options.contrast,
        vertical,
        offset,
        zoom_milli,
    };
    let decoded_key = DecodedKey {
        namespace: cache.namespace().to_path_buf(),
        render: key.clone(),
    };
    let lookup_frame = || -> Result<Option<RenderedPage>> {
        if mode.is_reader() {
            let frame = decoded(|state| {
                if mode == RenderMode::ReaderNextFrame {
                    state.peek(&decoded_key, raw, &source_checksum)
                } else {
                    state.lookup(&decoded_key, raw, &source_checksum, purpose)
                }
            });
            if frame.is_some() {
                decoded(|state| state.stats.raster_memory_hits += 1);
            }
            Ok(frame)
        } else {
            lookup(cache, &decoded_key, purpose)
        }
    };
    if let Some(mut rendered) = lookup_frame()? {
        if rendered.source_proof.is_none() {
            rendered.source_proof = Some(source_proof);
        }
        if mode == RenderMode::ReaderNextFrame {
            attach_retained_raster(&decoded_key, &mut rendered, None);
        }
        return Ok(rendered);
    }

    let gate = render_gate(&decoded_key)?;
    let _render_guard = lock_render_gate(&gate)?;
    if let Some(mut rendered) = lookup_frame()? {
        if rendered.source_proof.is_none() {
            rendered.source_proof = Some(source_proof);
        }
        if mode == RenderMode::ReaderNextFrame {
            attach_retained_raster(&decoded_key, &mut rendered, None);
        }
        return Ok(rendered);
    }

    imaging::check_decode_cancelled()?;
    let bytes = source(raw)?;
    ensure!(
        !bytes.is_empty() && bytes.len() as u64 <= MAX_PAGE_BYTES,
        "The raw comic changed beyond the compressed-image limit"
    );
    ensure!(
        format!("{:x}", Sha256::digest(&bytes)) == source_checksum,
        "The decoded source bytes do not match the prepared image identity"
    );
    ensure!(
        cache.fingerprint(raw)? == source_checksum,
        "The source image changed while preparing the rendered page"
    );
    let mut retained_source = None;
    let (image, scaled_height, viewport_height) = if vertical {
        let window = if mode.is_reader() {
            // BiliComics retains an immutable unrotated grayscale source for
            // adjacent windows. Share this module's existing 12 MiB pool: an
            // ordinary page never inserts its much larger source bitmap.
            let source_key = SourceKey {
                namespace: cache.namespace().to_path_buf(),
                checksum: source_checksum.clone(),
                encoded_bytes: bytes.len() as u64,
            };
            let source = decoded(|state| {
                if mode == RenderMode::ReaderNextFrame {
                    state.peek_source(&source_key)
                } else {
                    state.lookup_source(&source_key)
                }
            });
            let cold = source.is_none();
            let (window, source) =
                imaging::render_vertical_raster_with_source(&bytes, options, offset, source)?;
            if let Some(source) = source {
                if cold {
                    decoded(|state| state.stats.source_cold_decodes += 1);
                }
                retained_source = Some((source_key, source));
            }
            window
        } else {
            imaging::render_vertical_window_with_geometry(&bytes, options, offset)?
        };
        (window.image, window.scaled_height, window.viewport_height)
    } else {
        let image = if mode.is_reader() {
            imaging::decode_reader_raster(&bytes, options)?
        } else {
            imaging::decode_page(&bytes, options)?
        };
        (image, 0, 0)
    };
    drop(bytes);
    let image = Arc::new(zoom_image(image, zoom_milli));
    decoded(|state| {
        state.stats.cold_renders += 1;
        state.track_output(&image);
    });
    if mode.is_reader() {
        let mut rendered = RenderedPage {
            image,
            cache_path: raw.to_path_buf(),
            scaled_height,
            viewport_height,
            cache_hit: false,
            memory_hit: false,
            source_proof: Some(source_proof),
            retained_raster: None,
        };
        decoded(|state| {
            state.stats.raster_cold_renders += 1;
            state.stats.raster_persistence_skips += 1;
        });
        if mode == RenderMode::ReaderNextFrame {
            attach_retained_raster(&decoded_key, &mut rendered, retained_source);
            return Ok(rendered);
        }
        decoded(|state| {
            if let Some((key, source)) = retained_source {
                state.remember_source(key, source, rendered.image.as_raw().capacity());
            }
            state.remember(decoded_key, &rendered, source_checksum, purpose);
        });
        return Ok(rendered);
    }
    let metadata = Metadata {
        version: FORMAT_VERSION,
        key: key_digest(&key)?,
        render_key: Some(key.clone()),
        scaled_height,
        viewport_height,
    };
    // Completing a bounded, valid output is useful even if the original reader
    // moved to that page meanwhile. Finish its durable publication without the
    // obsolete caller's fence, then let that caller stop before its next frame.
    let (cache_path, persisted) =
        match imaging::without_decode_cancellation(|| persist(cache, &key, &image, &metadata)) {
            Ok(path) => (path, true),
            Err(error) => {
                // A derivative is an optimization, never a prerequisite for reading
                // an already validated local or downloaded source image.
                eprintln!(
                    "aidoku: processed page temporarily uncached; using rendered pixels: {error:#}"
                );
                (raw.to_path_buf(), false)
            }
        };
    let rendered = RenderedPage {
        image,
        cache_path,
        scaled_height,
        viewport_height,
        cache_hit: false,
        memory_hit: false,
        source_proof: Some(source_proof),
        retained_raster: None,
    };
    if persisted {
        remember(cache, decoded_key, &rendered, purpose);
    }
    Ok(rendered)
}

fn attach_retained_raster(
    key: &DecodedKey,
    page: &mut RenderedPage,
    source: Option<(SourceKey, imaging::ReaderSource)>,
) {
    let Some(proof) = page.source_proof.clone() else {
        return;
    };
    let source = source.or_else(|| {
        if !key.render.vertical {
            return None;
        }
        let source_key = SourceKey {
            namespace: key.namespace.clone(),
            checksum: proof.checksum.clone(),
            encoded_bytes: proof.bytes,
        };
        decoded(|state| {
            state
                .sources
                .get(&source_key)
                .map(|entry| (source_key.clone(), entry.source.clone()))
        })
    });
    page.retained_raster = Some(RetainedRaster::new(key.clone(), page, proof, source));
}

fn lock_render_gate(gate: &Mutex<()>) -> Result<MutexGuard<'_, ()>> {
    loop {
        imaging::check_decode_cancelled()?;
        match gate.try_lock() {
            Ok(guard) => return Ok(guard),
            Err(TryLockError::Poisoned(_)) => {
                return Err(anyhow!("The page-render gate is unavailable"));
            }
            Err(TryLockError::WouldBlock) => {
                std::thread::park_timeout(Duration::from_millis(5));
            }
        }
    }
}

fn persist(
    cache: &CacheManager,
    key: &RenderKey,
    image: &GrayImage,
    metadata: &Metadata,
) -> Result<PathBuf> {
    let mut write = cache.begin_processed(key, 0)?;
    encode(&mut write, image, metadata)?;
    // Validate the final chunks, grayscale depth, bounded allocation, and
    // in-band metadata before the atomic publication boundary.
    validate_processed(write.temp_path(), key, image.dimensions())?;
    write.commit()
}

fn remember(cache: &CacheManager, key: DecodedKey, page: &RenderedPage, purpose: RenderPurpose) {
    // A best-effort derivative can disappear immediately after publication.
    // Only retain pixels that still have a validated, restorable disk source.
    let Ok(_lease) = cache.lease_path(&page.cache_path) else {
        return;
    };
    let Ok(Some((path, checksum))) = cache.lookup_processed_with_fingerprint(&key.render) else {
        return;
    };
    if path == page.cache_path {
        decoded(|state| state.remember(key, page, checksum, purpose));
    }
}

fn lookup(
    cache: &CacheManager,
    key: &DecodedKey,
    purpose: RenderPurpose,
) -> Result<Option<RenderedPage>> {
    imaging::check_decode_cancelled()?;
    // A resident entry already knows its path. Lease it before the validating
    // lookup, avoiding a second index lookup while keeping clear() effective.
    let candidate = decoded(|state| {
        state
            .entries
            .get(key)
            .map(|entry| entry.page.cache_path.clone())
    });
    let resident_lease =
        candidate.and_then(|path| cache.lease_path(&path).ok().map(|lease| (path, lease)));
    let (path, checksum) = match cache.lookup_processed_with_fingerprint(&key.render) {
        Ok(Some(artifact)) => artifact,
        Ok(None) => {
            decoded(|state| state.invalidate(key));
            return Ok(None);
        }
        Err(error) => {
            decoded(|state| state.invalidate(key));
            eprintln!(
                "aidoku: processed page lookup unavailable; rebuilding from source: {error:#}"
            );
            return Ok(None);
        }
    };
    let lease = match resident_lease {
        Some((candidate, lease)) if candidate == path => lease,
        _ => match cache.lease_path(&path) {
            Ok(lease) => lease,
            Err(_) => {
                decoded(|state| state.invalidate(key));
                return Ok(None);
            }
        },
    };
    if let Some(page) = decoded(|state| state.lookup(key, &path, &checksum, purpose)) {
        return Ok(Some(page));
    }
    let decoded_page = decode_processed(&path, &key.render);
    if let Ok(page) = &decoded_page {
        decoded(|state| {
            state.stats.disk_hits += 1;
            state.remember(key.clone(), page, checksum, purpose);
        });
    }
    drop(lease);
    match decoded_page {
        Ok(image) => Ok(Some(image)),
        Err(error) if imaging::is_decode_cancelled(&error) => Err(error),
        Err(_) => {
            // Only the disposable derivative is invalidated. Download pins,
            // original images, and user-provided files remain independent.
            decoded(|state| state.invalidate(key));
            if let Err(error) = cache.invalidate_processed(&key.render) {
                eprintln!("aidoku: invalid processed page could not be discarded: {error:#}");
            }
            Ok(None)
        }
    }
}

fn check_options(options: &ImageOptions) -> Result<()> {
    ensure!(
        options.width > 0
            && options.height > 0
            && options.width <= MAX_TARGET_SIDE
            && options.height <= MAX_TARGET_SIDE
            && u64::from(options.width) * u64::from(options.height) <= MAX_PAGE_PIXELS,
        "The rendered viewport exceeds the image allocation limit"
    );
    Ok(())
}

fn normalize_zoom(zoom: f32) -> Result<u32> {
    ensure!(
        zoom.is_finite() && (1.0..=4.0).contains(&zoom),
        "The rendered zoom must be between one and four"
    );
    Ok((zoom * 1_000.0).round() as u32)
}

fn key_digest(key: &RenderKey) -> Result<String> {
    Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(key)?)))
}

fn render_gate(key: &DecodedKey) -> Result<RenderGate> {
    let mut gates = RENDER_GATES
        .get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .map_err(|_| anyhow!("The page-render registry is unavailable"))?;
    gates.retain(|_, gate| gate.strong_count() > 0);
    if let Some(gate) = gates.get(key).and_then(Weak::upgrade) {
        return Ok(gate);
    }
    let gate = Arc::new(Mutex::new(()));
    gates.insert(key.clone(), Arc::downgrade(&gate));
    Ok(gate)
}

fn read_source(path: &Path) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    File::open(path)
        .context("Open the source comic for rendering")?
        .take(MAX_PAGE_BYTES + 1)
        .read_to_end(&mut bytes)
        .context("Read the source comic for rendering")?;
    ensure!(
        bytes.len() as u64 <= MAX_PAGE_BYTES,
        "The source comic exceeds the compressed-image limit"
    );
    Ok(bytes)
}

fn zoom_image(image: GrayImage, zoom_milli: u32) -> GrayImage {
    if zoom_milli == 1_000 {
        return image;
    }
    let width = (u64::from(image.width()) * 1_000 / u64::from(zoom_milli)).max(1) as u32;
    let height = (u64::from(image.height()) * 1_000 / u64::from(zoom_milli)).max(1) as u32;
    image::imageops::crop_imm(
        &image,
        (image.width() - width) / 2,
        (image.height() - height) / 2,
        width,
        height,
    )
    .to_image()
}

struct ReservedWriter<'a> {
    file: File,
    reservation: &'a mut CacheWrite,
    written: u64,
}

impl Write for ReservedWriter<'_> {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        let target = self
            .written
            .checked_add(bytes.len() as u64)
            .ok_or_else(|| std::io::Error::other("The processed PNG length overflowed"))?;
        if target > MAX_PAGE_BYTES {
            return Err(std::io::Error::other(
                "The processed PNG exceeds the artifact limit",
            ));
        }
        self.reservation
            .ensure_capacity(target)
            .map_err(|error| std::io::Error::other(error.to_string()))?;
        let count = self.file.write(bytes)?;
        self.written += count as u64;
        Ok(count)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.file.flush()
    }
}

fn encode(write: &mut CacheWrite, image: &GrayImage, metadata: &Metadata) -> Result<()> {
    encode_with_compression(write, image, metadata, None)
}

fn encode_with_compression(
    write: &mut CacheWrite,
    image: &GrayImage,
    metadata: &Metadata,
    compression: Option<png::Compression>,
) -> Result<()> {
    let file = OpenOptions::new()
        .write(true)
        .truncate(true)
        .open(write.temp_path())
        .context("Open the reserved processed artifact")?;
    let sink = ReservedWriter {
        file,
        reservation: write,
        written: 0,
    };
    let mut output = BufWriter::with_capacity(WRITE_BUFFER_BYTES, sink);
    let mut encoder = png::Encoder::new(&mut output, image.width(), image.height());
    encoder.set_color(png::ColorType::Grayscale);
    encoder.set_depth(png::BitDepth::Eight);
    // Cover and download derivatives retain their established durable codec.
    // Reader Raster frames never enter this encoder. The optional compression
    // parameter supports an isolated, lossless comparison in the opt-in profile.
    if let Some(compression) = compression {
        encoder.set_compression(compression);
    }
    encoder
        .add_text_chunk(METADATA_KEY.into(), serde_json::to_string(metadata)?)
        .context("Write the rendered geometry metadata")?;
    let mut writer = encoder
        .write_header()
        .context("Write the processed PNG header")?;
    writer
        .write_image_data(image.as_raw())
        .context("Encode the processed grayscale pixels")?;
    writer.finish().context("Finish the processed PNG")?;
    output
        .flush()
        .context("Flush the reserved processed artifact")?;
    Ok(())
}

type ProcessedReader = png::Reader<BufReader<File>>;

fn processed_reader(
    path: &Path,
    expected: Option<&RenderKey>,
) -> Result<(ProcessedReader, Metadata)> {
    let file = File::open(path).context("Open the processed PNG")?;
    ensure!(
        file.metadata()?.len() <= MAX_PAGE_BYTES,
        "The processed PNG exceeds the compressed-image limit"
    );
    let decoder = png::Decoder::new_with_limits(
        BufReader::new(file),
        png::Limits {
            bytes: MAX_DECODE_BYTES as usize,
        },
    );
    let reader = decoder
        .read_info()
        .context("Read the processed PNG header")?;
    let info = reader.info();
    ensure!(
        info.width > 0
            && info.height > 0
            && info.width <= MAX_TARGET_SIDE
            && info.height <= MAX_TARGET_SIDE
            && u64::from(info.width) * u64::from(info.height) <= MAX_PAGE_PIXELS
            && info.color_type == png::ColorType::Grayscale
            && info.bit_depth == png::BitDepth::Eight
            && !info.interlaced
            && info.animation_control.is_none(),
        "The processed cache entry is not a bounded eight-bit grayscale PNG"
    );
    let mut chunks = info
        .uncompressed_latin1_text
        .iter()
        .filter(|chunk| chunk.keyword == METADATA_KEY);
    let chunk = chunks
        .next()
        .context("The processed PNG has no geometry metadata")?;
    ensure!(
        chunk.text.len() <= MAX_METADATA_BYTES && chunks.next().is_none(),
        "The processed PNG geometry metadata is ambiguous or oversized"
    );
    let metadata: Metadata =
        serde_json::from_str(&chunk.text).context("Decode the processed geometry metadata")?;
    let key = match expected {
        Some(key) => {
            ensure!(
                metadata
                    .render_key
                    .as_ref()
                    .is_none_or(|stored| stored == key),
                "The processed PNG contains an inconsistent rendering key"
            );
            key
        }
        None => metadata
            .render_key
            .as_ref()
            .context("The processed PNG cannot independently prove its rendering options")?,
    };
    ensure!(
        metadata.version == FORMAT_VERSION && metadata.key == key_digest(key)?,
        "The processed PNG belongs to a different rendering configuration"
    );
    ensure!(
        info.width <= key.width && info.height <= key.height,
        "The processed PNG exceeds its rendering dimensions"
    );
    if key.vertical {
        ensure!(
            metadata.scaled_height > 0
                && metadata.viewport_height == key.height.min(metadata.scaled_height),
            "The processed vertical PNG has invalid complete-strip geometry"
        );
    } else {
        ensure!(
            metadata.scaled_height == 0 && metadata.viewport_height == 0,
            "The processed page unexpectedly contains vertical-strip geometry"
        );
    }
    let size = reader
        .output_buffer_size()
        .context("The processed PNG output size overflowed")?;
    ensure!(
        size as u64 <= MAX_PAGE_PIXELS,
        "The processed PNG exceeds its grayscale allocation limit"
    );
    Ok((reader, metadata))
}

fn validate_processed(path: &Path, key: &RenderKey, dimensions: (u32, u32)) -> Result<()> {
    let _decode_guard = imaging::decode_permit()?;
    decoded(|state| state.stats.staged_validations += 1);
    #[cfg(test)]
    count_work(|work| work.staged_validations += 1);
    let (mut reader, _) = processed_reader(path, Some(key))?;
    let (width, height) = (reader.info().width, reader.info().height);
    ensure!(
        (width, height) == dimensions,
        "The staged rendered image has unexpected dimensions"
    );
    // Validate the encoded data and final chunks without allocating a second
    // complete viewport while the freshly rendered pixels are still live.
    let mut rows = 0;
    while let Some(row) = reader
        .next_row()
        .context("Validate the staged processed PNG row")?
    {
        ensure!(
            rows < height && row.data().len() == width as usize,
            "The staged processed PNG contains an invalid grayscale row"
        );
        rows += 1;
    }
    ensure!(
        rows == height,
        "The staged processed PNG ended before its final row"
    );
    reader
        .finish()
        .context("Validate the final staged processed PNG chunks")?;
    Ok(())
}

fn decode_processed(path: &Path, key: &RenderKey) -> Result<RenderedPage> {
    let _decode_guard = imaging::decode_permit()?;
    decoded(|state| state.stats.processed_decodes += 1);
    #[cfg(test)]
    count_work(|work| work.processed_decodes += 1);
    let result = decode_processed_inner(path, key);
    if result.is_err() {
        decoded(|state| state.stats.processed_decode_failures += 1);
    }
    result
}

fn decode_processed_inner(path: &Path, key: &RenderKey) -> Result<RenderedPage> {
    let (mut reader, metadata) = processed_reader(path, Some(key))?;
    let size = reader
        .output_buffer_size()
        .context("The processed PNG output size overflowed")?;
    decoded(|state| state.stats.processed_decoded_bytes += size as u64);
    let mut pixels = vec![0_u8; size];
    let frame = reader
        .next_frame(&mut pixels)
        .context("Decode the processed PNG pixels")?;
    ensure!(
        frame.color_type == png::ColorType::Grayscale
            && frame.bit_depth == png::BitDepth::Eight
            && frame.buffer_size() == frame.width as usize * frame.height as usize,
        "The processed PNG frame is not an eight-bit grayscale image"
    );
    pixels.truncate(frame.buffer_size());
    let image = GrayImage::from_raw(frame.width, frame.height, pixels)
        .context("The processed PNG pixel buffer has invalid dimensions")?;
    reader
        .finish()
        .context("Validate the final processed PNG chunks")?;
    let image = Arc::new(image);
    decoded(|state| state.track_output(&image));
    Ok(RenderedPage {
        image,
        cache_path: path.to_path_buf(),
        scaled_height: metadata.scaled_height,
        viewport_height: metadata.viewport_height,
        cache_hit: true,
        memory_hit: false,
        source_proof: None,
        retained_raster: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cache::CacheConfig;
    use image::{ImageFormat, Luma};
    use std::{
        cell::Cell,
        sync::{
            Barrier, MutexGuard,
            atomic::{AtomicUsize, Ordering},
        },
        time::{Duration, Instant},
    };
    use tempfile::TempDir;

    static TEST_RENDER_LOCK: Mutex<()> = Mutex::new(());

    struct Fixture {
        _render_guard: MutexGuard<'static, ()>,
        directory: TempDir,
        cache: CacheManager,
        raw: PathBuf,
        page: Page,
    }

    impl Fixture {
        fn new(image: GrayImage) -> Self {
            let fixture = Self::empty();
            image
                .save_with_format(&fixture.raw, ImageFormat::Png)
                .unwrap();
            fixture
        }

        fn managed(image: GrayImage) -> Self {
            let mut fixture = Self::empty();
            let write = fixture.cache.begin_raw(&fixture.page, None, 0).unwrap();
            image
                .save_with_format(write.temp_path(), ImageFormat::Png)
                .unwrap();
            fixture.cache.validate_path(write.temp_path()).unwrap();
            fixture.raw = write.commit().unwrap();
            fixture.page.local_path = Some(fixture.raw.clone());
            fixture
        }

        fn empty() -> Self {
            let render_guard = TEST_RENDER_LOCK
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let directory = tempfile::tempdir().unwrap();
            let raw = directory.path().join("original.png");
            let cache = CacheManager::open_with_config(
                directory.path().join("state"),
                CacheConfig {
                    max_unpinned_bytes: 128 * 1024 * 1024,
                    min_free_bytes: 0,
                },
            )
            .unwrap();
            let page = Page {
                url: "https://example.test/comic/page.png".into(),
                local_path: Some(raw.clone()),
                ..Page::default()
            };
            Self {
                _render_guard: render_guard,
                directory,
                cache,
                raw,
                page,
            }
        }

        fn key(&self, options: &ImageOptions, vertical: bool, offset: u32, zoom: f32) -> RenderKey {
            RenderKey {
                source_key: format!(
                    "{}:{}",
                    CacheManager::page_key(&self.page),
                    self.cache.fingerprint(&self.raw).unwrap()
                ),
                width: options.width,
                height: options.height,
                crop_borders: options.crop_borders,
                dither: options.dither,
                contrast: options.contrast,
                vertical,
                offset,
                zoom_milli: normalize_zoom(zoom).unwrap(),
            }
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            clear_memory(&self.cache);
        }
    }

    fn options() -> ImageOptions {
        ImageOptions {
            width: 96,
            height: 144,
            crop_borders: false,
            dither: false,
            contrast: 0,
        }
    }

    fn artwork() -> GrayImage {
        GrayImage::from_fn(96, 384, |x, y| {
            Luma([if x < 8 || x >= 88 || y < 12 || y >= 372 {
                255
            } else {
                ((x * 7 + y * 13) % 247) as u8
            }])
        })
    }

    #[test]
    fn reader_raster_reuses_prefetched_pixels_without_processed_publication() {
        let fixture = Fixture::new(artwork());
        let original = fs::read(&fixture.raw).unwrap();
        let expected = imaging::decode_reader_raster(&original, &options()).unwrap();
        let prepared = prefetch_raster(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        assert_eq!(*prepared.image, expected);
        assert_eq!(prepared.cache_path, fixture.raw);
        assert!(!prepared.cache_hit);
        assert!(
            prepared
                .source_proof
                .as_ref()
                .unwrap()
                .verify(&fixture.cache)
                .unwrap()
        );
        assert_eq!(fixture.cache.stats().commits, 0);
        assert_eq!(fixture.cache.stats().entries, 0);
        RENDER_WORK.with(|work| work.set(RenderWork::default()));
        let foreground = render_for_purpose(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            RenderPurpose::Foreground,
            RenderMode::ReaderRaster,
            |_| panic!("A ready Raster must not read or decode its original again"),
        )
        .unwrap();
        assert!(foreground.memory_hit && foreground.cache_hit);
        assert!(Arc::ptr_eq(&prepared.image, &foreground.image));
        assert_eq!(
            RENDER_WORK.with(std::cell::Cell::get),
            RenderWork {
                prefetch_memory_hits: 1,
                ..RenderWork::default()
            }
        );
        assert_eq!(fixture.cache.stats().commits, 0);
        assert_eq!(fs::read(&fixture.raw).unwrap(), original);
    }

    #[test]
    fn private_next_raster_stays_out_of_history_until_promoted() {
        let fixture = Fixture::new(artwork());
        let cancellation = imaging::DecodeCancellation::new();
        let prepared = prefetch_next_raster_cancellable(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            &cancellation,
        )
        .unwrap();
        assert!(prepared.retained_raster.is_some());
        assert!(decoded(|state| !state
            .entries
            .keys()
            .any(|key| key.namespace == fixture.cache.namespace())));
        assert!(
            prepared
                .source_proof
                .as_ref()
                .unwrap()
                .verify(&fixture.cache)
                .unwrap()
        );
        let retained = prepared.retained_raster.as_ref().unwrap();
        assert_eq!(
            RetainedRaster::unique_retained_bytes(&[retained.clone(), retained.clone()]),
            prepared.image.as_raw().capacity()
        );
        retained.promote(&fixture.cache).unwrap();
        let foreground = render_for_purpose(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            RenderPurpose::Foreground,
            RenderMode::ReaderRaster,
            |_| panic!("A promoted full Raster must not decode its original"),
        )
        .unwrap();
        assert!(foreground.memory_hit);
        assert!(foreground.retained_raster.is_none());
        assert!(Arc::ptr_eq(&foreground.image, &prepared.image));
        assert!(decoded(|state| state
            .entries
            .values()
            .all(|entry| entry.page.retained_raster.is_none())));
        assert_eq!(fixture.cache.stats().commits, 0);
    }

    #[test]
    fn reader_raster_version_isolated_from_persistent_rendering() {
        let fixture = Fixture::new(artwork());
        let mut settings = options();
        settings.dither = true;
        let persistent = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &settings,
            false,
            0,
            1.0,
        )
        .unwrap();
        let committed = fixture.cache.stats().commits;
        let raster = render_raster(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &settings,
            false,
            0,
            1.0,
        )
        .unwrap();
        assert_eq!(fixture.cache.stats().commits, committed);
        assert_ne!(persistent.cache_path, raster.cache_path);
        assert_eq!(raster.cache_path, fixture.raw);
        assert_eq!(
            *raster.image,
            imaging::decode_reader_raster(&fs::read(&fixture.raw).unwrap(), &settings).unwrap()
        );
        assert_ne!(persistent.image, raster.image);
        clear_memory(&fixture.cache);
        let restored = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &settings,
            false,
            0,
            1.0,
        )
        .unwrap();
        assert!(restored.cache_hit);
        assert_eq!(persistent.image, restored.image);
        let rebuilt = render_raster(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &settings,
            false,
            0,
            1.0,
        )
        .unwrap();
        assert!(!rebuilt.cache_hit);
        assert_eq!(raster.image, rebuilt.image);
    }

    #[test]
    fn reader_raster_proof_rejects_source_changes_and_removal() {
        let fixture = Fixture::new(artwork());
        let raster = render_raster(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        let proof = raster.source_proof.unwrap();
        assert_eq!(proof.source_path(), fs::canonicalize(&fixture.raw).unwrap());
        assert!(proof.matches_page(&fixture.page));
        assert!(proof.verify(&fixture.cache).unwrap());
        let metadata = fs::metadata(&fixture.raw).unwrap();
        let mut changed = fs::read(&fixture.raw).unwrap();
        let middle = changed.len() / 2;
        changed[middle] ^= 0x40;
        fs::write(&fixture.raw, changed).unwrap();
        let file = File::options().write(true).open(&fixture.raw).unwrap();
        file.set_times(fs::FileTimes::new().set_modified(metadata.modified().unwrap()))
            .unwrap();
        assert!(!proof.verify(&fixture.cache).unwrap());
        fs::remove_file(&fixture.raw).unwrap();
        assert!(!proof.verify(&fixture.cache).unwrap());
    }

    #[cfg(unix)]
    #[test]
    fn reader_raster_proof_rejects_retargeted_raw_alias() {
        let fixture = Fixture::new(artwork());
        let alias = fixture.directory.path().join("reader-alias.png");
        let replacement = fixture.directory.path().join("replacement.png");
        GrayImage::from_pixel(96, 384, Luma([153]))
            .save(&replacement)
            .unwrap();
        std::os::unix::fs::symlink(&fixture.raw, &alias).unwrap();
        let page = Page {
            url: "https://example.test/reader-alias.png".into(),
            local_path: Some(alias.clone()),
            ..Page::default()
        };
        let raster =
            render_raster(&fixture.cache, &page, &alias, &options(), false, 0, 1.0).unwrap();
        let proof = raster.source_proof.unwrap();
        assert!(proof.verify(&fixture.cache).unwrap());
        fs::remove_file(&alias).unwrap();
        std::os::unix::fs::symlink(&replacement, &alias).unwrap();
        assert!(proof.matches_page(&page));
        assert!(!proof.verify(&fixture.cache).unwrap());
    }

    #[test]
    fn vertical_raster_cache_reuses_sources_and_rejects_changed_encoded_identity() {
        let fixture = Fixture::new(GrayImage::from_fn(64, 500, |x, y| {
            Luma([((x * 3 + y / 2) % 247) as u8])
        }));
        let settings = ImageOptions {
            width: 128,
            height: 100,
            ..options()
        };
        let first = render_raster(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &settings,
            true,
            0,
            1.0,
        )
        .unwrap();
        let source_key = SourceKey {
            namespace: fixture.cache.namespace().to_path_buf(),
            checksum: fixture.cache.fingerprint(&fixture.raw).unwrap(),
            encoded_bytes: fs::metadata(&fixture.raw).unwrap().len(),
        };
        let source = decoded(|state| state.sources.get(&source_key).unwrap().source.clone());
        let second = render_raster(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &settings,
            true,
            85,
            1.0,
        )
        .unwrap();
        let reused = decoded(|state| state.sources.get(&source_key).unwrap().source.clone());
        assert!(source.same_allocation(&reused));
        assert_ne!(first.image, second.image);
        assert_eq!(first.scaled_height, second.scaled_height);
        GrayImage::from_fn(64, 500, |x, y| Luma([((x * 11 + y / 7 + 37) % 247) as u8]))
            .save(&fixture.raw)
            .unwrap();
        let replacement = render_raster(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &settings,
            true,
            85,
            1.0,
        )
        .unwrap();
        assert_ne!(replacement.image, second.image);
        assert!(!second.source_proof.unwrap().verify(&fixture.cache).unwrap());
        assert!(
            replacement
                .source_proof
                .as_ref()
                .unwrap()
                .verify(&fixture.cache)
                .unwrap()
        );
        let expected = imaging::render_vertical_raster_with_geometry(
            &fs::read(&fixture.raw).unwrap(),
            &settings,
            85,
        )
        .unwrap();
        assert_eq!(*replacement.image, expected.image);
        assert_eq!(fixture.cache.stats().commits, 0);
        assert!(decoded(|state| state.bytes <= DECODED_CACHE_BYTES));
        clear_memory(&fixture.cache);
        assert!(decoded(|state| !state
            .sources
            .keys()
            .any(|key| key.namespace == fixture.cache.namespace())));
    }

    #[test]
    fn source_and_viewport_arcs_share_one_existing_decoded_budget() {
        let mut memory = DecodedCache::default();
        let (old_key, old) = decoded_fixture("old-viewport", GrayImage::new(2_048, 2_048));
        memory.remember(
            old_key.clone(),
            &old,
            "old".into(),
            RenderPurpose::Foreground,
        );
        let mut allocation = Vec::with_capacity(8 * 1024 * 1024);
        allocation.push(17);
        let source =
            imaging::ReaderSource::from_test_image(GrayImage::from_raw(1, 1, allocation).unwrap());
        let source_key = SourceKey {
            namespace: PathBuf::from("memory-test"),
            checksum: "sealed-source".into(),
            encoded_bytes: 1,
        };
        let (new_key, viewport) = decoded_fixture("new-viewport", GrayImage::new(2_048, 2_048));
        memory.remember_source(
            source_key.clone(),
            source.clone(),
            viewport.image.as_raw().capacity(),
        );
        memory.remember(
            new_key.clone(),
            &viewport,
            "new".into(),
            RenderPurpose::Foreground,
        );
        assert!(!memory.entries.contains_key(&old_key));
        assert!(memory.entries.contains_key(&new_key));
        assert!(source.same_allocation(&memory.sources[&source_key].source));
        assert_eq!(memory.bytes, DECODED_CACHE_BYTES);
        memory.remember_source(
            source_key.clone(),
            source,
            viewport.image.as_raw().capacity(),
        );
        assert_eq!(memory.bytes, DECODED_CACHE_BYTES);
        assert_eq!(memory.sources.len(), 1);
        let mut allocation = Vec::with_capacity(DECODED_CACHE_BYTES + 1);
        allocation.push(34);
        let oversized =
            imaging::ReaderSource::from_test_image(GrayImage::from_raw(1, 1, allocation).unwrap());
        memory.remember_source(
            SourceKey {
                checksum: "oversized".into(),
                ..source_key
            },
            oversized,
            0,
        );
        assert_eq!(memory.bytes, DECODED_CACHE_BYTES);
        assert_eq!(memory.sources.len(), 1);
        assert_eq!(memory.stats.source_oversized_skips, 1);
    }

    fn profile_persistence(
        cache: &CacheManager,
        key: &RenderKey,
        image: &GrayImage,
        metadata: &Metadata,
        previous: bool,
    ) -> Result<(PathBuf, serde_json::Value)> {
        use crate::imaging::fixture_profile::elapsed_ns;

        let total = Instant::now();
        let mut write = cache.begin_processed(key, 0)?;
        let reservation_ns = elapsed_ns(total);
        let started = Instant::now();
        if previous {
            encode(&mut write, image, metadata)?;
        } else {
            encode_with_compression(&mut write, image, metadata, Some(png::Compression::Fastest))?;
        }
        let encode_ns = elapsed_ns(started);
        let started = Instant::now();
        validate_processed(write.temp_path(), key, image.dimensions())?;
        let complete_validation_ns = elapsed_ns(started);
        let started = Instant::now();
        let path = write.commit()?;
        let commit_ns = elapsed_ns(started);
        let total_ns = elapsed_ns(total);
        let bytes = fs::metadata(&path)?.len();
        Ok((
            path,
            serde_json::json!({
                "reservation_ns": reservation_ns,
                "encode_ns": encode_ns,
                "complete_validation_ns": complete_validation_ns,
                "commit_ns": commit_ns,
                "total_ns": total_ns,
                "png_bytes": bytes,
            }),
        ))
    }

    #[test]
    #[ignore = "Run only on the remote worker with a release build and real fixture manifest"]
    fn real_reader_processed_png_profile() -> Result<()> {
        use crate::imaging::fixture_profile::{digest, load, save};
        use serde_json::json;

        let input = load()?;
        let _guard = TEST_RENDER_LOCK
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut cases = Vec::new();
        for case in &input.cases {
            let bytes = fs::read(&case.path)?;
            let image = imaging::decode_page(&bytes, &case.options())?;
            let page = Page {
                local_path: Some(case.path.clone()),
                ..Page::default()
            };
            let key = RenderKey {
                source_key: format!("{}:{}", CacheManager::page_key(&page), digest(&bytes)),
                width: case.target_width,
                height: case.target_height,
                crop_borders: case.crop_borders,
                dither: case.dither,
                contrast: case.contrast,
                vertical: false,
                offset: 0,
                zoom_milli: 1_000,
            };
            let metadata = Metadata {
                version: FORMAT_VERSION,
                key: key_digest(&key)?,
                render_key: Some(key.clone()),
                scaled_height: 0,
                viewport_height: 0,
            };
            let directory = tempfile::tempdir()?;
            // Both variants use the ordinary production disk policy. The
            // fixture never increases a cache, source or decoder memory limit.
            let caches = [
                CacheManager::open(directory.path().join("previous"))?,
                CacheManager::open(directory.path().join("production"))?,
            ];
            for (index, cache) in caches.iter().enumerate() {
                let (path, _) = profile_persistence(cache, &key, &image, &metadata, index == 0)?;
                let restored = decode_processed_inner(&path, &key)?;
                ensure!(
                    *restored.image == image,
                    "A warmed processed PNG differs for {}",
                    case.name
                );
            }
            let mut previous = Vec::new();
            let mut production = Vec::new();
            let mut order = Vec::new();
            for sample in 0..input.samples {
                let pair = if sample % 2 == 0 { [0, 1] } else { [1, 0] };
                order.push(if pair[0] == 0 { "AB" } else { "BA" });
                for variant in pair {
                    let (path, timing) = profile_persistence(
                        &caches[variant],
                        &key,
                        &image,
                        &metadata,
                        variant == 0,
                    )?;
                    // This full decode is outside the timing window, after the
                    // production validator and durable commit already finished.
                    let restored = decode_processed_inner(&path, &key)?;
                    ensure!(
                        *restored.image == image
                            && restored.scaled_height == 0
                            && restored.viewport_height == 0,
                        "A sampled processed PNG differs for {}",
                        case.name
                    );
                    if variant == 0 {
                        previous.push(timing);
                    } else {
                        production.push(timing);
                    }
                }
            }
            cases.push(json!({
                "case": case,
                "source_sha256": digest(&bytes),
                "source_bytes": bytes.len(),
                "pixel_sha256": digest(image.as_raw()),
                "output_dimensions": image.dimensions(),
                "pixel_equal": true,
                "order": order,
                "previous": previous,
                "production": production,
            }));
        }
        save(
            &input,
            "processed-png",
            &json!({
                "schema_version": 1,
                "scope": "Release processed PNG encoding, complete staged validation and durable commit on the remote host; excludes source decoding, UI and native display",
                "comparison": "Unchanged persistent codec versus the isolated Fastest candidate; the production Reader bypasses both, and covers/downloads retain the default codec",
                "samples_per_variant": input.samples,
                "disk_policy": CacheConfig::default(),
                "decoded_output_budget": DECODED_CACHE_BYTES,
                "cases": cases,
            }),
        )
    }

    #[test]
    fn cancelled_prefetch_does_not_remove_a_valid_processed_page() {
        let fixture = Fixture::new(artwork());
        let prepared = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        let expected = fs::read(&prepared.cache_path).unwrap();
        clear_memory(&fixture.cache);
        let cancellation = imaging::DecodeCancellation::new();
        cancellation.cancel();
        let error = prefetch_cancellable(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            &cancellation,
        )
        .unwrap_err();
        assert!(imaging::is_decode_cancelled(&error));
        assert_eq!(fs::read(&prepared.cache_path).unwrap(), expected);
        let displayed = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        assert!(displayed.cache_hit && !displayed.memory_hit);
        assert_eq!(displayed.image, prepared.image);
    }

    #[test]
    fn cancelled_alias_decode_propagates_without_invalidating_its_png() {
        let fixture = Fixture::new(artwork());
        let prepared = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        fixture
            .cache
            .set_alias("cancelled-cover", &prepared.cache_path)
            .unwrap();
        let expected = fs::read(&prepared.cache_path).unwrap();
        clear_memory(&fixture.cache);
        let cancellation = imaging::DecodeCancellation::new();
        let error = imaging::with_background_decode(Some(&cancellation), || {
            cancellation.cancel();
            load_alias(&fixture.cache, "cancelled-cover", &options())
        })
        .unwrap_err();
        assert!(imaging::is_decode_cancelled(&error));
        assert_eq!(fs::read(&prepared.cache_path).unwrap(), expected);
        let displayed = load_alias(&fixture.cache, "cancelled-cover", &options())
            .unwrap()
            .unwrap();
        assert!(displayed.cache_hit && !displayed.memory_hit);
        assert_eq!(displayed.image, prepared.image);
    }

    #[test]
    fn warm_external_render_rehashes_the_source_without_loading_its_pixels() {
        let fixture = Fixture::new(artwork());
        let reads = Cell::new(0);
        let started = Instant::now();
        let cold = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |path| {
                reads.set(reads.get() + 1);
                read_source(path)
            },
        )
        .unwrap();
        let cold_time = started.elapsed();
        let checksummed = fixture.cache.stats().checksum_bytes;
        let source_bytes = fs::metadata(&fixture.raw).unwrap().len();
        RENDER_WORK.with(|work| work.set(RenderWork::default()));
        let started = Instant::now();
        let warm = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |_| panic!("A processed hit must not load the original for decoding"),
        )
        .unwrap();
        let warm_time = started.elapsed();
        assert!(!cold.cache_hit);
        assert!(warm.cache_hit);
        assert!(warm.memory_hit);
        assert!(Arc::ptr_eq(&cold.image, &warm.image));
        assert_eq!(cold.image, warm.image);
        assert_eq!(cold.cache_path, warm.cache_path);
        assert!(cold.cache_path.is_file());
        assert_eq!(fs::canonicalize(&cold.cache_path).unwrap(), cold.cache_path);
        assert_eq!(reads.get(), 1);
        assert_eq!(
            fixture.cache.stats().checksum_bytes,
            checksummed + source_bytes
        );
        assert_eq!(
            RENDER_WORK.with(std::cell::Cell::get),
            RenderWork::default()
        );
        assert_eq!(fixture.cache.stats().commits, 1);
        eprintln!(
            "Processed render cold={cold_time:?}, warm={warm_time:?}, source_checksum_bytes={source_bytes}, original_pixel_reads={}",
            reads.get()
        );

        let key = fixture.key(&options(), false, 0, 1.0);
        let path = fixture.cache.lookup_processed(&key).unwrap().unwrap();
        let reader = png::Decoder::new(BufReader::new(File::open(path).unwrap()))
            .read_info()
            .unwrap();
        assert_eq!(reader.info().color_type, png::ColorType::Grayscale);
        assert_eq!(reader.info().bit_depth, png::BitDepth::Eight);
        assert!(
            reader
                .info()
                .uncompressed_latin1_text
                .iter()
                .any(|chunk| chunk.keyword == METADATA_KEY)
        );
    }

    #[test]
    fn warm_managed_render_preserves_pixels_without_loading_or_hashing_the_original() {
        let fixture = Fixture::managed(artwork());
        let cold = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        let checksummed = fixture.cache.stats().checksum_bytes;
        RENDER_WORK.with(|work| work.set(RenderWork::default()));
        let warm = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |_| panic!("A managed processed hit must not load the original for decoding"),
        )
        .unwrap();
        assert!(!cold.cache_hit);
        assert!(warm.cache_hit && warm.memory_hit);
        assert!(Arc::ptr_eq(&cold.image, &warm.image));
        assert_eq!(cold.image, warm.image);
        assert_eq!(cold.cache_path, warm.cache_path);
        assert_eq!(fixture.cache.stats().checksum_bytes, checksummed);
        assert_eq!(
            RENDER_WORK.with(std::cell::Cell::get),
            RenderWork::default()
        );
        assert_eq!(fixture.cache.stats().commits, 2);
    }

    #[test]
    fn prefetch_retains_the_exact_pixels_for_foreground_display() {
        let fixture = Fixture::new(GrayImage::from_fn(1_272, 1_696, |x, y| {
            Luma([((x * 7 + y * 13) % 247) as u8])
        }));
        let options = ImageOptions {
            width: 1_272,
            height: 1_696,
            crop_borders: false,
            dither: true,
            contrast: 0,
        };
        let before = stats();
        RENDER_WORK.with(|work| work.set(RenderWork::default()));
        let started = Instant::now();
        let prepared = prefetch(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options,
            false,
            0,
            1.0,
        )
        .unwrap();
        let prefetch_time = started.elapsed();
        let pixels = Arc::downgrade(&prepared.image);
        let path = prepared.cache_path.clone();
        drop(prepared);
        let after_prefetch = stats();
        let prefetch_work = RENDER_WORK.with(std::cell::Cell::get);
        assert_eq!(
            prefetch_work,
            RenderWork {
                output_allocations: 1,
                output_allocated_bytes: 1_272 * 1_696,
                staged_validations: 1,
                ..RenderWork::default()
            }
        );
        RENDER_WORK.with(|work| work.set(RenderWork::default()));
        let started = Instant::now();
        let displayed = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options,
            false,
            0,
            1.0,
            |_| panic!("A prefetched resident viewport must not load the source"),
        )
        .unwrap();
        let display_time = started.elapsed();
        let mut resident_times = vec![display_time];
        for _ in 1..64 {
            let started = Instant::now();
            let repeated = render_with_source(
                &fixture.cache,
                &fixture.page,
                &fixture.raw,
                &options,
                false,
                0,
                1.0,
                |_| panic!("A repeated resident viewport must not load the source"),
            )
            .unwrap();
            resident_times.push(started.elapsed());
            assert!(repeated.memory_hit);
            assert!(Arc::ptr_eq(&displayed.image, &repeated.image));
        }
        resident_times.sort_unstable();
        let after_display = stats();
        assert_eq!(
            RENDER_WORK.with(std::cell::Cell::get),
            RenderWork {
                prefetch_memory_hits: 1,
                ..RenderWork::default()
            }
        );
        assert!(displayed.cache_hit && displayed.memory_hit);
        assert!(Arc::ptr_eq(&pixels.upgrade().unwrap(), &displayed.image));
        assert_eq!(displayed.cache_path, path);
        assert!(path.is_file());
        assert_eq!(fixture.cache.stats().leased_entries, 0);
        eprintln!(
            "Kindle viewport=1272x1696; prefetch={prefetch_time:?}, resident_p50={:?}, resident_p95={:?}, resident_samples={}; prefetch output_allocations={}, output_allocated_bytes={}, processed_decodes={}, staged_validations={}; resident output_allocations={}, output_allocated_bytes={}, processed_decodes={}, prefetch_memory_hits={}",
            resident_times[resident_times.len() / 2],
            resident_times[(resident_times.len() * 95).div_ceil(100) - 1],
            resident_times.len(),
            after_prefetch.output_allocations - before.output_allocations,
            after_prefetch.output_allocated_bytes - before.output_allocated_bytes,
            after_prefetch.processed_decodes - before.processed_decodes,
            after_prefetch.staged_validations - before.staged_validations,
            after_display.output_allocations - after_prefetch.output_allocations,
            after_display.output_allocated_bytes - after_prefetch.output_allocated_bytes,
            after_display.processed_decodes - after_prefetch.processed_decodes,
            after_display.prefetch_memory_hits - after_prefetch.prefetch_memory_hits,
        );
    }

    #[test]
    fn releasing_resident_pixels_still_allows_a_validated_disk_hit() {
        let fixture = Fixture::new(artwork());
        let first = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        clear_memory(&fixture.cache);
        let disk = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |_| panic!("Evicting decoded pixels must not evict the processed artifact"),
        )
        .unwrap();
        assert!(disk.cache_hit && !disk.memory_hit);
        assert!(!Arc::ptr_eq(&first.image, &disk.image));
        assert_eq!(first.image, disk.image);
        assert_eq!(first.cache_path, disk.cache_path);
        let resident = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |_| panic!("The disk hit should have repopulated resident pixels"),
        )
        .unwrap();
        assert!(resident.memory_hit);
        assert!(Arc::ptr_eq(&disk.image, &resident.image));
    }

    #[test]
    fn identical_render_keys_in_distinct_data_directories_do_not_share_pixels() {
        let fixture = Fixture::new(artwork());
        let first = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        let second_cache = CacheManager::open_with_config(
            fixture.directory.path().join("second-state"),
            CacheConfig {
                max_unpinned_bytes: 128 * 1024 * 1024,
                min_free_bytes: 0,
            },
        )
        .unwrap();
        let reads = Cell::new(0);
        let second = render_with_source(
            &second_cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |path| {
                reads.set(reads.get() + 1);
                read_source(path)
            },
        )
        .unwrap();
        assert_eq!(reads.get(), 1);
        assert!(!second.cache_hit && !second.memory_hit);
        assert!(!Arc::ptr_eq(&first.image, &second.image));
        assert_eq!(first.image, second.image);
        assert_ne!(first.cache_path, second.cache_path);
        clear_memory(&second_cache);
    }

    #[test]
    fn cleared_and_missing_derivatives_invalidate_resident_pixels() {
        let fixture = Fixture::new(artwork());
        let first = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        assert_eq!(fixture.cache.stats().leased_entries, 0);
        fixture.cache.clear_unpinned().unwrap();
        assert!(!first.cache_path.exists());
        let reads = Cell::new(0);
        let rebuilt = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |path| {
                reads.set(reads.get() + 1);
                read_source(path)
            },
        )
        .unwrap();
        assert!(!rebuilt.cache_hit && !rebuilt.memory_hit);
        assert!(!Arc::ptr_eq(&first.image, &rebuilt.image));
        assert_eq!(first.image, rebuilt.image);
        assert!(rebuilt.cache_path.is_file());
        fs::remove_file(&rebuilt.cache_path).unwrap();
        let missing = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |path| {
                reads.set(reads.get() + 1);
                read_source(path)
            },
        )
        .unwrap();
        assert!(!missing.cache_hit && !missing.memory_hit);
        assert_eq!(reads.get(), 2);
        assert_eq!(first.image, missing.image);
        assert!(missing.cache_path.is_file());
    }

    #[test]
    fn replacement_artifact_checksum_prevents_reusing_an_old_pixel_arc() {
        let fixture = Fixture::new(artwork());
        let first = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        let key = fixture.key(&options(), false, 0, 1.0);
        let replacement = GrayImage::from_pixel(32, 48, Luma([51]));
        let metadata = Metadata {
            version: FORMAT_VERSION,
            key: key_digest(&key).unwrap(),
            render_key: Some(key.clone()),
            scaled_height: 0,
            viewport_height: 0,
        };
        persist(&fixture.cache, &key, &replacement, &metadata).unwrap();
        let displayed = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |_| panic!("A valid replacement derivative should use the processed disk path"),
        )
        .unwrap();
        assert!(displayed.cache_hit && !displayed.memory_hit);
        assert!(!Arc::ptr_eq(&first.image, &displayed.image));
        assert_eq!(*displayed.image, replacement);
        assert_eq!(first.cache_path, displayed.cache_path);
    }

    fn decoded_fixture(name: &str, image: GrayImage) -> (DecodedKey, RenderedPage) {
        let key = DecodedKey {
            namespace: PathBuf::from("memory-test"),
            render: RenderKey {
                source_key: name.into(),
                width: image.width(),
                height: image.height(),
                crop_borders: false,
                dither: false,
                contrast: 0,
                vertical: false,
                offset: 0,
                zoom_milli: 1_000,
            },
        };
        let page = RenderedPage {
            image: Arc::new(image),
            cache_path: PathBuf::from(format!("{name}.png")),
            scaled_height: 0,
            viewport_height: 0,
            cache_hit: false,
            memory_hit: false,
            source_proof: None,
            retained_raster: None,
        };
        (key, page)
    }

    fn retained_fixture(
        name: &str,
        image: GrayImage,
    ) -> (DecodedKey, RenderedPage, RetainedRaster) {
        let (key, mut page) = decoded_fixture(name, image);
        // This fixture isolates cache ownership and admission. Codec and fresh
        // filesystem proof validation are exercised by the real-source tests.
        let source_page = Page {
            local_path: Some(page.cache_path.clone()),
            ..Page::default()
        };
        let proof = SourceProof {
            requested_path: page.cache_path.clone(),
            source_path: page.cache_path.clone(),
            checksum: format!("source-{name}"),
            bytes: 1,
            page_identity: CacheManager::page_key(&source_page),
            page: source_page,
        };
        page.source_proof = Some(proof.clone());
        let retained = RetainedRaster::new(key.clone(), &page, proof, None);
        (key, page, retained)
    }

    #[test]
    fn private_native_next_frame_preserves_previous_history_until_consumption() {
        let mut history = DecodedCache::default();
        let (key1, page1, first) =
            retained_fixture("page-one", GrayImage::from_pixel(1_753, 2_480, Luma([34])));
        let (_, _, second) =
            retained_fixture("page-two", GrayImage::from_pixel(1_753, 2_480, Luma([68])));
        let (key3, _, third) = retained_fixture(
            "page-three",
            GrayImage::from_pixel(1_753, 2_480, Luma([102])),
        );
        let bytes = first.retained_bytes();
        assert_eq!(bytes, 4_347_440);
        assert!(bytes * 3 > DECODED_CACHE_BYTES);
        first.promote_into(&mut history);
        second.promote_into(&mut history);
        // The private third frame already exists, but its owner is the 32 MiB
        // next-frame slot rather than the two-entry grayscale history here.
        assert!(!history.entries.contains_key(&key3));
        assert_eq!(history.bytes, bytes * 2);
        let previous = history
            .lookup(
                &key1,
                &page1.cache_path,
                &first.proof.checksum,
                RenderPurpose::Foreground,
            )
            .unwrap();
        assert!(previous.memory_hit);
        assert!(Arc::ptr_eq(&previous.image, &first.image));
        assert_eq!(third.retained_bytes(), bytes);

        let mut sequential = DecodedCache::default();
        first.promote_into(&mut sequential);
        second.promote_into(&mut sequential);
        third.promote_into(&mut sequential);
        assert_eq!(sequential.bytes, bytes * 2);
        assert!(!sequential.entries.contains_key(&key1));
        let previous = sequential
            .lookup(
                &second.key,
                &second.cache_path,
                &second.proof.checksum,
                RenderPurpose::Foreground,
            )
            .unwrap();
        assert!(previous.memory_hit);
        assert!(Arc::ptr_eq(&previous.image, &second.image));
        // A deeper return to page one is an ordinary bounded-cache miss.
        assert!(
            sequential
                .lookup(
                    &key1,
                    &page1.cache_path,
                    &first.proof.checksum,
                    RenderPurpose::Foreground
                )
                .is_none()
        );
    }

    #[test]
    fn retained_full_raster_key_cannot_be_replaced_by_a_display_half() {
        let mut history = DecodedCache::default();
        let (key, mut public_page, retained) = retained_fixture(
            "full-wide",
            GrayImage::from_fn(7, 4, |x, y| Luma([(x + y * 17) as u8])),
        );
        public_page.image =
            Arc::new(image::imageops::crop_imm(&*public_page.image, 0, 0, 3, 4).to_image());
        assert_eq!(public_page.image.dimensions(), (3, 4));
        retained.promote_into(&mut history);
        let restored = history
            .lookup(
                &key,
                &public_page.cache_path,
                &retained.proof.checksum,
                RenderPurpose::Foreground,
            )
            .unwrap();
        assert!(restored.memory_hit);
        assert_eq!(restored.image.dimensions(), (7, 4));
        assert!(Arc::ptr_eq(&restored.image, &retained.image));
        assert_eq!(history.bytes, 28);
    }

    #[test]
    fn retained_gray_accounting_deduplicates_output_and_vertical_source_allocations() {
        let (key1, page1, first) = retained_fixture("vertical-one", GrayImage::new(2, 2));
        let (key2, page2, second) = retained_fixture("vertical-two", GrayImage::new(2, 2));
        let source = imaging::ReaderSource::from_test_image(GrayImage::new(4, 4));
        let source_key = SourceKey {
            namespace: PathBuf::from("memory-test"),
            checksum: "shared-vertical-source".into(),
            encoded_bytes: 1,
        };
        let first = RetainedRaster::new(
            key1,
            &page1,
            first.proof,
            Some((source_key.clone(), source.clone())),
        );
        let second = RetainedRaster::new(
            key2,
            &page2,
            second.proof,
            Some((source_key.clone(), source)),
        );
        assert_eq!(first.retained_bytes(), 20);
        assert_eq!(
            RetainedRaster::unique_retained_bytes(&[first.clone(), first.clone()]),
            20
        );
        assert_eq!(
            RetainedRaster::unique_retained_bytes(&[first.clone(), second]),
            24
        );
        let mut history = DecodedCache::default();
        first.promote_into(&mut history);
        assert_eq!(history.bytes, 20);
        assert!(
            history
                .entries
                .values()
                .all(|entry| entry.page.retained_raster.is_none())
        );
        history.remove_source(&source_key);
        assert_eq!(history.bytes, 4);
    }

    #[test]
    fn speculative_resident_peek_does_not_reorder_foreground_history() {
        let mut history = DecodedCache::default();
        let (key1, page1, first) = retained_fixture("peek-one", GrayImage::new(2, 2));
        let (_, _, second) = retained_fixture("peek-two", GrayImage::new(2, 2));
        first.promote_into(&mut history);
        second.promote_into(&mut history);
        let before = history.entries[&key1].access;
        let peeked = history
            .peek(&key1, &page1.cache_path, &first.proof.checksum)
            .unwrap();
        assert!(peeked.memory_hit);
        assert_eq!(history.entries[&key1].access, before);
        assert!(history.entries[&key1].access < history.entries[&second.key].access);
        let source_key = SourceKey {
            namespace: PathBuf::from("memory-test"),
            checksum: "peek-source".into(),
            encoded_bytes: 1,
        };
        history.remember_source(
            source_key.clone(),
            imaging::ReaderSource::from_test_image(GrayImage::new(4, 4)),
            4,
        );
        let before = history.sources[&source_key].access;
        assert!(history.peek_source(&source_key).is_some());
        assert_eq!(history.sources[&source_key].access, before);
    }

    #[test]
    fn decoded_lru_bounds_retained_bytes_and_reports_live_evicted_outputs() {
        let mut memory = DecodedCache::default();
        let mut pages = (0..4)
            .map(|index| decoded_fixture(&format!("page-{index}"), GrayImage::new(2_048, 2_048)))
            .collect::<Vec<_>>();
        for (key, page) in pages.iter().take(3) {
            memory.track_output(&page.image);
            memory.remember(key.clone(), page, "same".into(), RenderPurpose::Prefetch);
        }
        let (key, page) = &pages[0];
        let reused = memory
            .lookup(key, &page.cache_path, "same", RenderPurpose::Foreground)
            .unwrap();
        assert!(Arc::ptr_eq(&page.image, &reused.image));
        assert_eq!(memory.stats.prefetch_memory_hits, 1);
        drop(reused);
        let (key, page) = &pages[3];
        memory.track_output(&page.image);
        memory.remember(key.clone(), page, "same".into(), RenderPurpose::Prefetch);
        assert!(memory.entries.contains_key(&pages[0].0));
        assert!(!memory.entries.contains_key(&pages[1].0));
        let retained = memory.snapshot();
        assert_eq!(retained.memory_bytes, DECODED_CACHE_BYTES as u64);
        assert_eq!(retained.memory_entries, 3);
        assert_eq!(retained.memory_evictions, 1);
        assert_eq!(retained.live_output_bytes, 16 * 1024 * 1024);
        pages.clear();
        assert_eq!(
            memory.snapshot().live_output_bytes,
            DECODED_CACHE_BYTES as u64
        );
        let keys = memory.entries.keys().cloned().collect::<Vec<_>>();
        for key in keys {
            memory.invalidate(&key);
        }
        assert_eq!(memory.snapshot().live_output_bytes, 0);
        assert_eq!(memory.snapshot().memory_bytes, 0);
    }

    #[test]
    fn oversized_and_tiny_outputs_cannot_make_decoded_retention_unbounded() {
        let mut memory = DecodedCache::default();
        let (large_key, large) = decoded_fixture("large", GrayImage::new(4_096, 4_096));
        memory.remember(
            large_key.clone(),
            &large,
            "large".into(),
            RenderPurpose::Foreground,
        );
        assert!(!memory.entries.contains_key(&large_key));
        assert_eq!(memory.stats.oversized_skips, 1);
        for index in 0..MAX_DECODED_ENTRIES + 1 {
            let (key, page) = decoded_fixture(&format!("tiny-{index}"), GrayImage::new(1, 1));
            memory.remember(key, &page, "tiny".into(), RenderPurpose::Prefetch);
        }
        assert_eq!(memory.entries.len(), MAX_DECODED_ENTRIES);
        assert!(memory.bytes <= DECODED_CACHE_BYTES);
        assert_eq!(memory.stats.memory_evictions, 1);
    }

    #[test]
    fn staged_row_validation_rejects_missing_final_chunks() {
        let fixture = Fixture::new(artwork());
        let key = fixture.key(&options(), false, 0, 1.0);
        let image = GrayImage::from_pixel(32, 48, Luma([102]));
        let metadata = Metadata {
            version: FORMAT_VERSION,
            key: key_digest(&key).unwrap(),
            render_key: Some(key.clone()),
            scaled_height: 0,
            viewport_height: 0,
        };
        let mut write = fixture.cache.begin_processed(&key, 0).unwrap();
        encode(&mut write, &image, &metadata).unwrap();
        validate_processed(write.temp_path(), &key, image.dimensions()).unwrap();
        let file = File::options().write(true).open(write.temp_path()).unwrap();
        file.set_len(file.metadata().unwrap().len() - 12).unwrap();
        assert!(validate_processed(write.temp_path(), &key, image.dimensions()).is_err());
        assert_eq!(fixture.cache.stats().commits, 0);
    }

    #[test]
    fn processed_cover_alias_survives_raw_eviction_and_reuses_resident_pixels() {
        let fixture = Fixture::new(artwork());
        let page = Page {
            url: "https://example.test/cover.png".into(),
            ..Page::default()
        };
        let bytes = fs::read(&fixture.raw).unwrap();
        let write = fixture
            .cache
            .begin_raw(&page, None, bytes.len() as u64)
            .unwrap();
        fs::write(write.temp_path(), bytes).unwrap();
        let raw = write.commit().unwrap();
        let prepared = render(&fixture.cache, &page, &raw, &options(), false, 0, 1.0).unwrap();
        fixture
            .cache
            .set_alias("cover-source-v2:test", &raw)
            .unwrap();
        fixture
            .cache
            .set_alias("cover-render-v2:test", &prepared.cache_path)
            .unwrap();
        // Keep the recently used cover during this eviction pass while its
        // unpinned canonical raw artifact is removed.
        let keep_cover = fixture.cache.lease_path(&prepared.cache_path).unwrap();
        fixture.cache.clear_unpinned().unwrap();
        drop(keep_cover);
        assert!(!raw.exists());
        assert!(prepared.cache_path.is_file());
        assert!(
            fixture
                .cache
                .lookup_alias("cover-source-v2:test")
                .unwrap()
                .is_none()
        );
        let resident = load_alias(&fixture.cache, "cover-render-v2:test", &options())
            .unwrap()
            .unwrap();
        assert!(resident.cache_hit && resident.memory_hit);
        assert!(Arc::ptr_eq(&prepared.image, &resident.image));
        clear_memory(&fixture.cache);
        let disk = load_alias(&fixture.cache, "cover-render-v2:test", &options())
            .unwrap()
            .unwrap();
        assert!(disk.cache_hit && !disk.memory_hit);
        assert_eq!(disk.image, prepared.image);
        assert_eq!(disk.cache_path, prepared.cache_path);
        assert_eq!(fixture.cache.stats().leased_entries, 0);
    }

    #[test]
    fn cover_alias_requires_matching_options_and_a_real_processed_artifact() {
        let fixture = Fixture::new(artwork());
        let baseline = options();
        let prepared = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &baseline,
            false,
            0,
            1.0,
        )
        .unwrap();
        fixture
            .cache
            .set_alias("cover", &prepared.cache_path)
            .unwrap();
        let mut variants = Vec::new();
        let mut changed = baseline.clone();
        changed.width -= 1;
        variants.push(changed);
        let mut changed = baseline.clone();
        changed.height -= 1;
        variants.push(changed);
        let mut changed = baseline.clone();
        changed.crop_borders = !changed.crop_borders;
        variants.push(changed);
        let mut changed = baseline.clone();
        changed.dither = !changed.dither;
        variants.push(changed);
        let mut changed = baseline.clone();
        changed.contrast += 1;
        variants.push(changed);
        for variant in variants {
            assert!(
                load_alias(&fixture.cache, "cover", &variant)
                    .unwrap()
                    .is_none()
            );
        }
        for (vertical, offset, zoom) in [(true, 20, 1.0), (false, 0, 2.0)] {
            let other = render(
                &fixture.cache,
                &fixture.page,
                &fixture.raw,
                &baseline,
                vertical,
                offset,
                zoom,
            )
            .unwrap();
            fixture
                .cache
                .set_alias("non-cover", &other.cache_path)
                .unwrap();
            assert!(
                load_alias(&fixture.cache, "non-cover", &baseline)
                    .unwrap()
                    .is_none()
            );
        }
        // A raw artifact can contain a valid processed PNG byte-for-byte; its
        // alias still cannot substitute for the indexed processed artifact.
        let raw_page = Page {
            url: "https://example.test/disguised-raw-cover.png".into(),
            ..Page::default()
        };
        let bytes = fs::read(&prepared.cache_path).unwrap();
        let write = fixture
            .cache
            .begin_raw(&raw_page, None, bytes.len() as u64)
            .unwrap();
        fs::write(write.temp_path(), bytes).unwrap();
        let raw = write.commit().unwrap();
        fixture.cache.set_alias("raw-alias", &raw).unwrap();
        assert!(
            load_alias(&fixture.cache, "raw-alias", &baseline)
                .unwrap()
                .is_none()
        );
        assert!(
            load_alias(&fixture.cache, "cover", &baseline)
                .unwrap()
                .is_some()
        );
    }

    #[test]
    fn cleared_missing_or_corrupt_cover_aliases_never_return_resident_pixels() {
        let fixture = Fixture::new(artwork());
        let original = fs::read(&fixture.raw).unwrap();
        for damage in 0..3 {
            let prepared = render(
                &fixture.cache,
                &fixture.page,
                &fixture.raw,
                &options(),
                false,
                0,
                1.0,
            )
            .unwrap();
            fixture
                .cache
                .set_alias("cover", &prepared.cache_path)
                .unwrap();
            match damage {
                0 => {
                    fixture.cache.clear_unpinned().unwrap();
                }
                1 => fs::remove_file(&prepared.cache_path).unwrap(),
                _ => {
                    let bytes = fs::read(&prepared.cache_path).unwrap();
                    fs::write(&prepared.cache_path, &bytes[..bytes.len() - 12]).unwrap();
                }
            }
            assert!(
                load_alias(&fixture.cache, "cover", &options())
                    .unwrap()
                    .is_none()
            );
            assert_eq!(fs::read(&fixture.raw).unwrap(), original);
            assert_eq!(fixture.cache.stats().leased_entries, 0);
        }
    }

    #[test]
    fn indexed_cover_alias_still_validates_embedded_key_and_final_png_chunks() {
        let fixture = Fixture::new(artwork());
        let original = fs::read(&fixture.raw).unwrap();
        for invalid_key in [true, false] {
            let prepared = render(
                &fixture.cache,
                &fixture.page,
                &fixture.raw,
                &options(),
                false,
                0,
                1.0,
            )
            .unwrap();
            let key = fixture.key(&options(), false, 0, 1.0);
            let mut write = fixture.cache.begin_processed(&key, 0).unwrap();
            if invalid_key {
                let mut embedded_key = key.clone();
                embedded_key.width -= 1;
                let metadata = Metadata {
                    version: FORMAT_VERSION,
                    key: key_digest(&key).unwrap(),
                    render_key: Some(embedded_key),
                    scaled_height: 0,
                    viewport_height: 0,
                };
                encode(&mut write, &prepared.image, &metadata).unwrap();
            } else {
                let bytes = fs::read(&prepared.cache_path).unwrap();
                fs::write(write.temp_path(), &bytes[..bytes.len() - 12]).unwrap();
            }
            // The cache publication layer checks storage identity; format
            // validation remains this module's responsibility even when a
            // caller publishes a byte-coherent but invalid derivative.
            let path = write.commit().unwrap();
            fixture.cache.set_alias("invalid-cover", &path).unwrap();
            assert!(
                load_alias(&fixture.cache, "invalid-cover", &options())
                    .unwrap()
                    .is_none()
            );
            assert_eq!(fs::read(&fixture.raw).unwrap(), original);
            assert_eq!(fixture.cache.stats().leased_entries, 0);
        }
    }

    #[test]
    fn cover_aliases_cannot_cross_data_namespaces() {
        let fixture = Fixture::new(artwork());
        let prepared = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        fixture
            .cache
            .set_alias("same-cover", &prepared.cache_path)
            .unwrap();
        let other_cache = CacheManager::open_with_config(
            fixture.directory.path().join("other-state"),
            CacheConfig {
                max_unpinned_bytes: 128 * 1024 * 1024,
                min_free_bytes: 0,
            },
        )
        .unwrap();
        assert!(
            load_alias(&other_cache, "same-cover", &options())
                .unwrap()
                .is_none()
        );
        let other = render(
            &other_cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        other_cache
            .set_alias("same-cover", &other.cache_path)
            .unwrap();
        let loaded = load_alias(&other_cache, "same-cover", &options())
            .unwrap()
            .unwrap();
        assert!(Arc::ptr_eq(&other.image, &loaded.image));
        assert!(!Arc::ptr_eq(&prepared.image, &loaded.image));
        assert_ne!(prepared.cache_path, loaded.cache_path);
        clear_memory(&other_cache);
    }

    #[test]
    fn digest_only_legacy_metadata_needs_the_source_supplied_render_key() {
        let fixture = Fixture::new(artwork());
        let key = fixture.key(&options(), false, 0, 1.0);
        let image = GrayImage::from_pixel(32, 48, Luma([102]));
        let metadata = Metadata {
            version: FORMAT_VERSION,
            key: key_digest(&key).unwrap(),
            render_key: None,
            scaled_height: 0,
            viewport_height: 0,
        };
        let path = persist(&fixture.cache, &key, &image, &metadata).unwrap();
        fixture.cache.set_alias("legacy-cover", &path).unwrap();
        assert!(
            load_alias(&fixture.cache, "legacy-cover", &options())
                .unwrap()
                .is_none()
        );
        let known_source = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
            |_| panic!("A legacy artifact remains valid when its raw source supplies the key"),
        )
        .unwrap();
        assert!(known_source.cache_hit && !known_source.memory_hit);
        assert_eq!(*known_source.image, image);
    }

    #[test]
    fn every_visual_option_and_vertical_offset_has_an_independent_artifact() {
        let fixture = Fixture::new(artwork());
        let baseline = options();
        let first = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &baseline,
            false,
            0,
            1.0,
        )
        .unwrap();
        let mut variants = Vec::new();
        let mut changed = baseline.clone();
        changed.width = 80;
        variants.push((changed, false, 0, 1.0));
        let mut changed = baseline.clone();
        changed.height = 120;
        variants.push((changed, false, 0, 1.0));
        let mut changed = baseline.clone();
        changed.crop_borders = true;
        variants.push((changed, false, 0, 1.0));
        let mut changed = baseline.clone();
        changed.dither = true;
        variants.push((changed, false, 0, 1.0));
        let mut changed = baseline.clone();
        changed.contrast = 20;
        variants.push((changed, false, 0, 1.0));
        variants.push((baseline.clone(), false, 0, 2.0));
        variants.push((baseline.clone(), true, 0, 1.0));
        variants.push((baseline.clone(), true, 100, 1.0));
        for (options, vertical, offset, zoom) in &variants {
            let cold = render(
                &fixture.cache,
                &fixture.page,
                &fixture.raw,
                options,
                *vertical,
                *offset,
                *zoom,
            )
            .unwrap();
            assert!(
                !cold.cache_hit,
                "A changed rendering parameter reused another artifact"
            );
            let warm = render_with_source(
                &fixture.cache,
                &fixture.page,
                &fixture.raw,
                options,
                *vertical,
                *offset,
                *zoom,
                |_| panic!("A repeated visual configuration read its original"),
            )
            .unwrap();
            assert!(warm.cache_hit);
            assert_eq!(warm.image, cold.image);
            assert_eq!(warm.cache_path, cold.cache_path);
        }
        let original = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &baseline,
            false,
            0,
            1.0,
        )
        .unwrap();
        assert!(original.cache_hit);
        assert_eq!(original.image, first.image);
        assert_eq!(fixture.cache.stats().commits, variants.len() as u64 + 1);
    }

    #[test]
    fn source_bytes_and_authenticated_page_identity_do_not_reuse_stale_pixels() {
        let fixture = Fixture::new(GrayImage::from_pixel(32, 48, Luma([0])));
        let original_time = fs::metadata(&fixture.raw).unwrap().modified().unwrap();
        let original_hash = fixture.cache.fingerprint(&fixture.raw).unwrap();
        let original = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        GrayImage::from_pixel(32, 48, Luma([255]))
            .save_with_format(&fixture.raw, ImageFormat::Png)
            .unwrap();
        File::options()
            .write(true)
            .open(&fixture.raw)
            .unwrap()
            .set_times(fs::FileTimes::new().set_modified(original_time))
            .unwrap();
        let changed_hash = fixture.cache.fingerprint(&fixture.raw).unwrap();
        assert_ne!(original_hash, changed_hash);
        let changed = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        assert!(!changed.cache_hit);
        assert_ne!(original.image, changed.image);
        let mut authenticated = fixture.page.clone();
        authenticated
            .headers
            .insert("Authorization".into(), "Bearer second-account".into());
        let second = render(
            &fixture.cache,
            &authenticated,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        assert!(!second.cache_hit);
        assert_eq!(second.image, changed.image);
    }

    #[test]
    fn a_corrupt_derivative_is_rebuilt_without_modifying_the_original() {
        let fixture = Fixture::new(artwork());
        let original = fs::read(&fixture.raw).unwrap();
        let first = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        let key = fixture.key(&options(), false, 0, 1.0);
        let path = fixture.cache.lookup_processed(&key).unwrap().unwrap();
        fs::write(&path, b"not a PNG").unwrap();
        let rebuilt = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        assert!(!rebuilt.cache_hit);
        assert_eq!(first.image, rebuilt.image);
        assert_eq!(fs::read(&fixture.raw).unwrap(), original);
        assert!(fixture.cache.stats().corruptions >= 1);
        assert!(
            render(
                &fixture.cache,
                &fixture.page,
                &fixture.raw,
                &options(),
                false,
                0,
                1.0
            )
            .unwrap()
            .cache_hit
        );
    }

    #[test]
    fn coherent_but_missing_geometry_metadata_is_a_disposable_old_artifact() {
        let fixture = Fixture::new(artwork());
        let key = fixture.key(&options(), false, 0, 1.0);
        let mut write = fixture.cache.begin_processed(&key, 0).unwrap();
        let image = GrayImage::from_pixel(32, 48, Luma([0]));
        image
            .save_with_format(write.temp_path(), ImageFormat::Png)
            .unwrap();
        write
            .ensure_capacity(fs::metadata(write.temp_path()).unwrap().len())
            .unwrap();
        write.commit().unwrap();
        let result = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        assert!(!result.cache_hit);
        assert_ne!(*result.image, image);
        assert!(
            render(
                &fixture.cache,
                &fixture.page,
                &fixture.raw,
                &options(),
                false,
                0,
                1.0
            )
            .unwrap()
            .cache_hit
        );
    }

    #[test]
    fn concurrent_cold_renders_share_one_source_decode_and_publication() {
        let fixture = Fixture::new(artwork());
        let barrier = Arc::new(Barrier::new(2));
        let reads = Arc::new(AtomicUsize::new(0));
        let workers = (0..2)
            .map(|_| {
                let cache = fixture.cache.clone();
                let page = fixture.page.clone();
                let raw = fixture.raw.clone();
                let barrier = barrier.clone();
                let reads = reads.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    render_with_source(&cache, &page, &raw, &options(), false, 0, 1.0, |path| {
                        reads.fetch_add(1, Ordering::SeqCst);
                        std::thread::sleep(Duration::from_millis(25));
                        read_source(path)
                    })
                    .unwrap()
                })
            })
            .collect::<Vec<_>>();
        let results = workers
            .into_iter()
            .map(|worker| worker.join().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(reads.load(Ordering::SeqCst), 1);
        assert_eq!(results.iter().filter(|result| result.cache_hit).count(), 1);
        assert_eq!(results[0].image, results[1].image);
        assert_eq!(fixture.cache.stats().commits, 1);
    }

    fn write_long_png(path: &Path) {
        let mut encoder = png::Encoder::new(File::create(path).unwrap(), 1_024, 20_000);
        encoder.set_color(png::ColorType::Grayscale);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().unwrap();
        {
            let mut rows = writer.stream_writer().unwrap();
            for y in 0..20_000 {
                let gray = if (1_000..19_000).contains(&y) {
                    ((y / 80) % 15 * 17) as u8
                } else {
                    255
                };
                rows.write_all(&[gray; 1_024]).unwrap();
            }
            rows.finish().unwrap();
        }
        writer.finish().unwrap();
    }

    #[test]
    fn warm_second_external_long_png_viewport_rehashes_source_without_decoding_the_strip() {
        let fixture = Fixture::empty();
        write_long_png(&fixture.raw);
        let options = ImageOptions {
            width: 256,
            height: 512,
            crop_borders: true,
            dither: true,
            contrast: 0,
        };
        let first = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options,
            true,
            0,
            1.0,
        )
        .unwrap();
        let second = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options,
            true,
            435,
            1.0,
        )
        .unwrap();
        assert!(second.scaled_height > 3_000);
        assert_eq!(second.viewport_height, 512);
        assert_eq!(first.scaled_height, second.scaled_height);
        assert_ne!(first.image, second.image);
        let checksummed = fixture.cache.stats().checksum_bytes;
        let source_bytes = fs::metadata(&fixture.raw).unwrap().len();
        RENDER_WORK.with(|work| work.set(RenderWork::default()));
        let started = Instant::now();
        let warm = render_with_source(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options,
            true,
            435,
            1.0,
            |_| panic!("A warm second viewport must not decode the original long PNG"),
        )
        .unwrap();
        assert!(warm.cache_hit);
        assert_eq!(second.image, warm.image);
        assert_eq!(second.scaled_height, warm.scaled_height);
        assert_eq!(second.viewport_height, warm.viewport_height);
        assert_eq!(
            fixture.cache.stats().checksum_bytes,
            checksummed + source_bytes
        );
        assert_eq!(
            RENDER_WORK.with(std::cell::Cell::get),
            RenderWork::default()
        );
        eprintln!(
            "Warm second long-PNG viewport={:?}; complete_height={}, source_checksum_bytes={source_bytes}, original_pixel_reads=0",
            started.elapsed(),
            warm.scaled_height
        );
    }

    #[test]
    fn insufficient_processed_quota_returns_readable_pixels_without_a_partial_artifact() {
        let fixture = Fixture::new(artwork());
        let original = fs::read(&fixture.raw).unwrap();
        let expected = imaging::decode_page(&original, &options()).unwrap();
        fixture
            .cache
            .set_config(CacheConfig {
                max_unpinned_bytes: 32,
                min_free_bytes: 0,
            })
            .unwrap();
        let result = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        assert_eq!(*result.image, expected);
        assert!(!result.cache_hit);
        assert_eq!(result.cache_path, fixture.raw);
        assert_eq!(fixture.cache.stats().commits, 0);
        assert_eq!(fixture.cache.stats().writes_inflight, 0);
        assert_eq!(fixture.cache.stats().reserved_bytes, 0);
        assert_eq!(
            fs::read_dir(fixture.directory.path().join("state/cache/shared-v1/temp"))
                .unwrap()
                .count(),
            0
        );
        assert_eq!(fs::read(&fixture.raw).unwrap(), original);
    }

    #[test]
    fn low_free_space_preserves_pinned_sources_and_existing_leased_derivatives() {
        let fixture = Fixture::new(artwork());
        let original = fs::read(&fixture.raw).unwrap();
        let existing = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &options(),
            false,
            0,
            1.0,
        )
        .unwrap();
        let existing_bytes = fs::read(&existing.cache_path).unwrap();
        let _existing_lease = fixture.cache.lease_path(&existing.cache_path).unwrap();
        let pinned_page = Page {
            url: "https://example.test/downloaded.png".into(),
            ..Page::default()
        };
        let pinned_write = fixture
            .cache
            .begin_raw(
                &pinned_page,
                Some("downloaded-chapter"),
                original.len() as u64,
            )
            .unwrap();
        fs::write(pinned_write.temp_path(), &original).unwrap();
        let pinned_path = pinned_write.commit().unwrap();
        let commits = fixture.cache.stats().commits;
        fixture
            .cache
            .set_config(CacheConfig {
                max_unpinned_bytes: 128 * 1024 * 1024,
                min_free_bytes: u64::MAX,
            })
            .unwrap();
        let mut changed = options();
        changed.contrast = 20;
        let result = render(
            &fixture.cache,
            &fixture.page,
            &fixture.raw,
            &changed,
            false,
            0,
            1.0,
        )
        .unwrap();
        assert_eq!(
            *result.image,
            imaging::decode_page(&original, &changed).unwrap()
        );
        assert!(!result.cache_hit);
        assert_eq!(result.cache_path, fixture.raw);
        assert_eq!(fs::read(&pinned_path).unwrap(), original);
        assert_eq!(fs::read(&existing.cache_path).unwrap(), existing_bytes);
        assert_eq!(fs::read(&fixture.raw).unwrap(), original);
        assert_eq!(fixture.cache.stats().commits, commits);
        assert_eq!(fixture.cache.stats().writes_inflight, 0);
        assert_eq!(fixture.cache.stats().reserved_bytes, 0);
        assert_eq!(
            fs::read_dir(fixture.directory.path().join("state/cache/shared-v1/temp"))
                .unwrap()
                .count(),
            0
        );
    }

    #[test]
    fn cache_write_fallback_never_accepts_a_damaged_original() {
        let fixture = Fixture::new(artwork());
        fixture
            .cache
            .set_config(CacheConfig {
                max_unpinned_bytes: 32,
                min_free_bytes: 0,
            })
            .unwrap();
        fs::write(&fixture.raw, b"damaged source image").unwrap();
        assert!(
            render(
                &fixture.cache,
                &fixture.page,
                &fixture.raw,
                &options(),
                false,
                0,
                1.0
            )
            .is_err()
        );
        assert_eq!(fixture.cache.stats().commits, 0);
        assert_eq!(fixture.cache.stats().writes_inflight, 0);
    }
}
