//! Short-lived transfer of recently displayed local cover pixels. A trusted
//! application peer supplies the bytes; this is not a user file/import format.
//! No path, page identity, book name or request header is serialized. Staging
//! never grants a cache hit: the next normal request proves both source bytes
//! and the indexed derivative before any pixels enter the decoded cache.

use super::{DecodedCache, DecodedKey, RenderKey, RenderPurpose, RenderedPage, decoded};
use image::GrayImage;
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};

pub const MAX_BLOB_BYTES: usize = 2 * 1024 * 1024;
const MAX_COVERS: usize = 6;
const MAX_WIDTH: u32 = 376;
const MAX_HEIGHT: u32 = 514;
const STAGING_LIFETIME: Duration = Duration::from_secs(45);
const MAGIC: &[u8; 8] = b"AKCVR\0\0\x01";
type Fingerprint = [u8; 32];
const PREFIX: usize = MAGIC.len() + 32 + 1;
const RECORD: usize = 32 + 64 + 32 + 4 + 4;
const SEAL: usize = 32;

// The ordinary startup path pays no extra file reads, hashing or locks.
// The decoded-cache mutex owns all changes to both the slot and this hint.
static MAY_HAVE_STAGED: AtomicBool = AtomicBool::new(false);
static STAGING_GENERATION: AtomicUsize = AtomicUsize::new(0);

struct Entry {
    render: Fingerprint,
    checksum: [u8; 64],
    artifact: Fingerprint,
    image: GrayImage,
}

pub(super) struct Staged {
    namespace: PathBuf,
    expires: Instant,
    entries: Vec<Entry>,
}

impl Staged {
    pub(super) fn usage(&self) -> (u64, u64) {
        (
            self.entries.len() as u64,
            self.entries
                .iter()
                .map(|e| e.image.as_raw().capacity() as u64)
                .sum(),
        )
    }
}

fn eligible(key: &RenderKey) -> bool {
    (1..=MAX_WIDTH).contains(&key.width)
        && (1..=MAX_HEIGHT).contains(&key.height)
        && !key.vertical
        && key.offset == 0
        && key.zoom_milli == 1000
}

fn render_digest(key: &RenderKey) -> Option<Fingerprint> {
    Some(Sha256::digest(serde_json::to_vec(key).ok()?).into())
}

#[cfg(target_os = "linux")]
fn file_stamp(meta: &fs::Metadata) -> Option<Fingerprint> {
    use std::os::unix::fs::MetadataExt;
    if !meta.is_file() {
        return None;
    }
    let mut hash = Sha256::new();
    hash.update(meta.dev().to_le_bytes());
    hash.update(meta.ino().to_le_bytes());
    hash.update(meta.len().to_le_bytes());
    hash.update(meta.ctime().to_le_bytes());
    hash.update(meta.ctime_nsec().to_le_bytes());
    hash.update(meta.mtime().to_le_bytes());
    hash.update(meta.mtime_nsec().to_le_bytes());
    Some(hash.finalize().into())
}

#[cfg(not(target_os = "linux"))]
fn file_stamp(_: &fs::Metadata) -> Option<Fingerprint> {
    None
}

fn artifact_stamp(path: &Path) -> Option<Fingerprint> {
    file_stamp(&fs::symlink_metadata(path).ok()?)
}

#[cfg(target_os = "linux")]
fn namespace(data_dir: &Path) -> Option<(PathBuf, Fingerprint)> {
    use std::os::unix::fs::MetadataExt;
    let data = data_dir.canonicalize().ok()?;
    let root = data.join("cache/shared-v1").canonicalize().ok()?;
    let meta = fs::metadata(&root).ok()?;
    if !root.starts_with(data) || !meta.is_dir() {
        return None;
    }
    let mut hash = Sha256::new();
    hash.update(MAGIC);
    hash.update(env!("CARGO_PKG_VERSION").as_bytes());
    // Bind to the running executable, including replacements that reuse the
    // same package version and path. Do not reread the entire binary on launch.
    hash.update(file_stamp(&fs::metadata("/proc/self/exe").ok()?)?);
    hash.update(meta.dev().to_le_bytes());
    hash.update(meta.ino().to_le_bytes());
    hash.update(root.as_os_str().as_encoded_bytes());
    Some((root, hash.finalize().into()))
}

#[cfg(not(target_os = "linux"))]
fn namespace(_: &Path) -> Option<(PathBuf, Fingerprint)> {
    None
}

/// Capture at most six recent foreground local cover renditions, without
/// opening the cache database or pinning a derivative across application exit.
pub fn capture(data_dir: &Path) -> Option<Vec<u8>> {
    let (root, owner) = namespace(data_dir)?;
    let mut candidates = decoded(|state| {
        state
            .entries
            .iter()
            .filter(|(key, entry)| {
                let (w, h) = entry.page.image.dimensions();
                key.namespace == root
                    && entry.used_as_local_cover
                    && eligible(&key.render)
                    && (1..=key.render.width).contains(&w)
                    && (1..=key.render.height).contains(&h)
                    && entry.page.scaled_height == 0
                    && entry.page.viewport_height == 0
            })
            .map(|(key, entry)| {
                (
                    entry.access,
                    key.render.clone(),
                    entry.checksum.clone(),
                    entry.page.cache_path.clone(),
                    entry.page.image.clone(),
                )
            })
            .collect::<Vec<_>>()
    });
    candidates.sort_unstable_by_key(|item| std::cmp::Reverse(item.0));
    let mut records = Vec::new();
    let mut length = PREFIX + SEAL;
    for (_, key, checksum, path, image) in candidates {
        let Some(artifact) = artifact_stamp(&path) else {
            continue;
        };
        let Some(render) = render_digest(&key) else {
            continue;
        };
        let Ok(checksum) = <[u8; 64]>::try_from(checksum.as_bytes()) else {
            continue;
        };
        let size = RECORD + image.as_raw().len();
        if size > MAX_BLOB_BYTES - length {
            continue;
        }
        length += size;
        records.push((render, checksum, artifact, image));
        if records.len() == MAX_COVERS {
            break;
        }
    }
    if records.is_empty() {
        return None;
    }
    let mut output = Vec::new();
    output.try_reserve_exact(length).ok()?;
    output.extend_from_slice(MAGIC);
    output.extend_from_slice(&owner);
    output.push(records.len() as u8);
    for (render, checksum, artifact, image) in records {
        output.extend_from_slice(&render);
        output.extend_from_slice(&checksum);
        output.extend_from_slice(&artifact);
        output.extend_from_slice(&image.width().to_le_bytes());
        output.extend_from_slice(&image.height().to_le_bytes());
        output.extend_from_slice(image.as_raw());
    }
    let seal = Sha256::digest(&output);
    output.extend_from_slice(&seal);
    Some(output)
}

fn take<const N: usize>(input: &mut &[u8]) -> Option<[u8; N]> {
    let (part, rest) = input.split_at_checked(N)?;
    *input = rest;
    part.try_into().ok()
}

fn parse(data_dir: &Path, blob: &[u8]) -> Option<Staged> {
    if !(PREFIX + RECORD + 1 + SEAL..=MAX_BLOB_BYTES).contains(&blob.len()) {
        return None;
    }
    let (body, seal) = blob.split_at(blob.len() - SEAL);
    let mut input = body;
    if &take::<8>(&mut input)? != MAGIC {
        return None;
    }
    let (root, owner) = namespace(data_dir)?;
    if take::<32>(&mut input)? != owner {
        return None;
    }
    let count = usize::from(take::<1>(&mut input)?[0]);
    if !(1..=MAX_COVERS).contains(&count) || Sha256::digest(body)[..] != *seal {
        return None;
    }
    let mut entries = Vec::<Entry>::with_capacity(count);
    for _ in 0..count {
        let render = take(&mut input)?;
        let checksum = take(&mut input)?;
        let artifact = take(&mut input)?;
        let w = u32::from_le_bytes(take(&mut input)?);
        let h = u32::from_le_bytes(take(&mut input)?);
        if !(1..=MAX_WIDTH).contains(&w)
            || !(1..=MAX_HEIGHT).contains(&h)
            || entries.iter().any(|entry| entry.render == render)
            || !checksum
                .iter()
                .all(|c: &u8| c.is_ascii_digit() || (b'a'..=b'f').contains(c))
        {
            return None;
        }
        let (pixels, rest) = input.split_at_checked((w as usize).checked_mul(h as usize)?)?;
        input = rest;
        let mut owned = Vec::new();
        owned.try_reserve_exact(pixels.len()).ok()?;
        owned.extend_from_slice(pixels);
        entries.push(Entry {
            render,
            checksum,
            artifact,
            image: GrayImage::from_raw(w, h, owned)?,
        });
    }
    input.is_empty().then(|| Staged {
        namespace: root,
        expires: Instant::now() + STAGING_LIFETIME,
        entries,
    })
}

/// Stage bytes obtained from the application-owned, authenticated handoff.
/// A positive count means candidates are available, not that any cover hit.
/// The transport must enforce MAX_BLOB_BYTES before reading its payload.
pub fn install(data_dir: &Path, blob: Vec<u8>) -> usize {
    let generation = STAGING_GENERATION
        .fetch_add(1, Ordering::AcqRel)
        .wrapping_add(1);
    let staged = parse(data_dir, &blob);
    let count = staged.as_ref().map_or(0, |v| v.entries.len());
    decoded(|state| {
        if STAGING_GENERATION.load(Ordering::Acquire) != generation {
            return 0;
        }
        state.staged_covers = staged;
        MAY_HAVE_STAGED.store(count != 0, Ordering::Release);
        count
    })
}

fn empty_slot(state: &mut DecodedCache) {
    state.staged_covers = None;
    MAY_HAVE_STAGED.store(false, Ordering::Release);
}

/// Discard this process's handoff slot and reject an installation that was
/// already parsing it. Ordinary decoded pages and on-disk files are untouched.
pub fn clear() {
    decoded(|state| {
        STAGING_GENERATION.fetch_add(1, Ordering::AcqRel);
        empty_slot(state);
    });
}

pub(super) fn discard_namespace(state: &mut DecodedCache, namespace: &Path) {
    if state
        .staged_covers
        .as_ref()
        .is_some_and(|s| s.namespace == namespace)
    {
        empty_slot(state);
    }
}

fn live_slot<'a>(state: &'a mut DecodedCache, namespace: &Path) -> Option<&'a mut Staged> {
    if Instant::now() >= state.staged_covers.as_ref()?.expires {
        empty_slot(state);
        return None;
    }
    state
        .staged_covers
        .as_mut()
        .filter(|s| s.namespace == namespace)
}

/// A hint may skip speculative work for this exact source and rendition. It
/// never consumes or publishes the pixels; the actual request still validates
/// the indexed derivative and current source. A late handoff never blocks a
/// decoder that has already started in the short speculation window.
pub(super) fn warm_hint(namespace: &Path, key: &RenderKey, path: &Path, checksum: &str) -> bool {
    if !MAY_HAVE_STAGED.load(Ordering::Acquire) {
        return false;
    }
    let Some(render) = render_digest(key) else {
        return false;
    };
    let expected = decoded(|state| {
        live_slot(state, namespace)?
            .entries
            .iter()
            .find(|e| e.render == render && e.checksum == checksum.as_bytes())
            .map(|e| e.artifact)
    });
    if expected.is_some_and(|stamp| artifact_stamp(path) == Some(stamp)) {
        crate::reader_timing::mark("startup_cover_warm_skipped_resident");
        true
    } else {
        false
    }
}

/// Called only after a normal local-cover request has hashed the source and
/// leased its validated derivative. An eviction, replacement or changed render
/// option falls through to the regular path, including reconstruction.
pub(super) fn take_verified(key: &DecodedKey, path: &Path, checksum: &str) -> Option<RenderedPage> {
    if !MAY_HAVE_STAGED.load(Ordering::Acquire) || !eligible(&key.render) {
        return None;
    }
    let render = render_digest(&key.render)?;
    let artifact = artifact_stamp(path)?;
    decoded(|state| {
        let staged = live_slot(state, &key.namespace)?;
        let idx = staged
            .entries
            .iter()
            .position(|entry| entry.render == render)?;
        let entry = staged.entries.swap_remove(idx);
        if staged.entries.is_empty() {
            empty_slot(state);
        }
        if entry.artifact != artifact
            || entry.checksum != checksum.as_bytes()
            || entry.image.width() > key.render.width
            || entry.image.height() > key.render.height
        {
            return None;
        }
        let page = RenderedPage {
            image: Arc::new(entry.image),
            cache_path: path.to_owned(),
            cache_hit: true,
            memory_hit: true,
            scaled_height: 0,
            viewport_height: 0,
            source_proof: None,
            retained_raster: None,
        };
        state.track_output(&page.image);
        state.stats.memory_hits += 1;
        state.stats.resident_cover_hits += 1;
        state.remember(
            key.clone(),
            &page,
            checksum.to_owned(),
            RenderPurpose::LocalCover,
        );
        Some(page)
    })
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use crate::{
        cache::{CacheConfig, CacheManager},
        domain::Page,
        imaging::ImageOptions,
    };
    use std::{
        fs::File,
        io::{Read, Write},
        process::{Command, Stdio},
        sync::MutexGuard,
    };

    struct Fixture {
        _lock: MutexGuard<'static, ()>,
        _dir: tempfile::TempDir,
        data: PathBuf,
        raw: PathBuf,
        cache: CacheManager,
    }

    fn write_cover(path: &Path, shade: u8) {
        // Changing the palette preserves encoded length and image dimensions.
        let mut png = png::Encoder::new(File::create(path).unwrap(), 48, 64);
        png.set_color(png::ColorType::Indexed);
        png.set_depth(png::BitDepth::Eight);
        png.set_palette(vec![shade, shade, shade, 200, 210, 220]);
        let mut out = png.write_header().unwrap();
        out.write_image_data(&[0; 48 * 64]).unwrap();
        out.finish().unwrap();
    }

    fn open(data: &Path) -> CacheManager {
        CacheManager::open_with_config(
            data,
            CacheConfig {
                max_unpinned_bytes: 128 << 20,
                min_free_bytes: 0,
            },
        )
        .unwrap()
    }

    fn opts() -> ImageOptions {
        ImageOptions {
            width: 376,
            height: 514,
            crop_borders: false,
            dither: false,
            ..Default::default()
        }
    }

    fn cover(cache: &CacheManager, raw: &Path, opts: &ImageOptions) -> RenderedPage {
        let page = Page {
            local_path: Some(raw.to_owned()),
            ..Default::default()
        };
        super::super::render_local_cover(cache, &page, raw, opts).unwrap()
    }

    fn decodes() -> u64 {
        super::super::RENDER_WORK.with(|v| v.get().processed_decodes)
    }

    impl Fixture {
        fn new() -> Self {
            let lock = super::super::tests::TEST_RENDER_LOCK
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let dir = tempfile::tempdir().unwrap();
            let raw = dir.path().join("private-user-book-name.png");
            write_cover(&raw, 35);
            let data = dir.path().join("my-data");
            let cache = open(&data);
            Self {
                _lock: lock,
                _dir: dir,
                data,
                raw,
                cache,
            }
        }

        fn snapshot(&self) -> Vec<u8> {
            let blob = capture(&self.data).unwrap();
            super::super::clear_memory(&self.cache);
            blob
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            super::super::clear_memory(&self.cache);
        }
    }

    #[test]
    fn fresh_process_skips_one_png_decode_for_identical_verified_pixels() {
        const CHILD_DATA: &str = "AIDOKU_TEST_RESIDENT_PIXEL_DATA";
        if let Some(data) = std::env::var_os(CHILD_DATA) {
            let data = PathBuf::from(data);
            let cache = open(&data);
            let raw = data.parent().unwrap().join("private-user-book-name.png");
            let mut blob = Vec::new();
            std::io::stdin()
                .lock()
                .take((MAX_BLOB_BYTES + 1) as u64)
                .read_to_end(&mut blob)
                .unwrap();
            if std::env::var_os("AIDOKU_TEST_RESIDENT_REPLACED_BINARY").is_some() {
                assert_eq!(install(&data, blob), 0);
                let page = cover(&cache, &raw, &opts());
                assert!(page.cache_hit && !page.memory_hit);
                assert_eq!(decodes(), 1);
                eprintln!(
                    "Replaced executable with the same package version and bytes: staged entries=0, normal PNG decodes=1"
                );
                return;
            }
            assert_eq!(install(&data, blob), 1);
            assert_eq!(super::super::stats().resident_staged_entries, 1);
            assert!(super::super::stats().resident_staged_bytes <= (MAX_WIDTH * MAX_HEIGHT) as u64);
            let before = cache.stats().checksum_bytes;
            let (page, events) =
                crate::reader_timing::capture_test_events(|| cover(&cache, &raw, &opts()));
            assert!(
                events
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|e| e["name"] == "cover_resident_pixels_hit")
            );
            assert!(page.cache_hit && page.memory_hit);
            let checked = cache.stats().checksum_bytes - before;
            assert!(checked >= fs::metadata(&raw).unwrap().len());
            assert!(page.source_proof.as_ref().unwrap().verify(&cache).unwrap());
            assert_eq!(
                format!("{:x}", Sha256::digest(page.image.as_raw())),
                std::env::var("AIDOKU_TEST_RESIDENT_PIXEL_HASH").unwrap()
            );
            assert_eq!(decodes(), 0);
            assert_eq!(super::super::stats().resident_cover_hits, 1);
            assert_eq!(super::super::stats().resident_staged_bytes, 0);
            super::super::clear_memory(&cache);
            let disk = cover(&cache, &raw, &opts());
            assert!(disk.cache_hit && !disk.memory_hit);
            assert_eq!(page.image, disk.image);
            assert_eq!(decodes(), 1);
            eprintln!(
                "True process handoff: source/derivative bytes checked={checked}; PNG decodes=0 on resident hit, 1 on equivalent disk hit"
            );
            return;
        }
        let f = Fixture::new();
        let original = cover(&f.cache, &f.raw, &opts());
        let blob = f.snapshot();
        assert!(blob.len() <= MAX_BLOB_BYTES);
        let secret = b"private-user-book-name";
        assert!(!blob.windows(secret.len()).any(|window| window == secret));
        let exe = std::env::current_exe().unwrap();
        let replacement = f._dir.path().join("new-application-package");
        fs::copy(&exe, &replacement).unwrap();
        for binary in [&exe, &replacement] {
            let mut command = Command::new(binary);
            if binary == &replacement {
                command.env("AIDOKU_TEST_RESIDENT_REPLACED_BINARY", "1");
            }
            let mut child = command
            .args(["--exact", "render_cache::resident::tests::fresh_process_skips_one_png_decode_for_identical_verified_pixels", "--nocapture"])
            .env(CHILD_DATA, &f.data)
            .env("AIDOKU_TEST_RESIDENT_PIXEL_HASH", format!("{:x}", Sha256::digest(original.image.as_raw())))
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
            child.stdin.take().unwrap().write_all(&blob).unwrap();
            let output = child.wait_with_output().unwrap();
            assert!(
                output.status.success(),
                "{}\n{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
            eprint!("{}", String::from_utf8_lossy(&output.stderr));
        }
    }

    #[test]
    fn changed_source_bytes_or_render_option_cannot_reuse_a_cover() {
        let f = Fixture::new();
        let original = cover(&f.cache, &f.raw, &opts());
        assert_eq!(install(&f.data, f.snapshot()), 1);
        let changed_options = ImageOptions {
            contrast: 13,
            ..opts()
        };
        assert!(!cover(&f.cache, &f.raw, &changed_options).memory_hit);
        let original_meta = fs::metadata(&f.raw).unwrap();
        write_cover(&f.raw, 220);
        File::options()
            .write(true)
            .open(&f.raw)
            .unwrap()
            .set_times(fs::FileTimes::new().set_modified(original_meta.modified().unwrap()))
            .unwrap();
        assert_eq!(original_meta.len(), fs::metadata(&f.raw).unwrap().len());
        let changed = cover(&f.cache, &f.raw, &opts());
        assert!(!changed.cache_hit && !changed.memory_hit);
        assert_ne!(original.image, changed.image);
    }

    #[test]
    fn replaced_corrupt_or_cleared_derivative_loses_eligibility() {
        let f = Fixture::new();
        let first = cover(&f.cache, &f.raw, &opts());
        assert_eq!(install(&f.data, f.snapshot()), 1);
        let replacement = first.cache_path.with_extension("replacement");
        fs::copy(&first.cache_path, &replacement).unwrap();
        fs::rename(replacement, &first.cache_path).unwrap();
        let before = decodes();
        let disk = cover(&f.cache, &f.raw, &opts());
        assert!(disk.cache_hit && !disk.memory_hit);
        assert_eq!(decodes() - before, 1);
        assert_eq!(disk.image, first.image);

        assert_eq!(install(&f.data, f.snapshot()), 1);
        fs::write(&disk.cache_path, b"corrupt derivative").unwrap();
        let repaired = cover(&f.cache, &f.raw, &opts());
        assert!(!repaired.cache_hit && !repaired.memory_hit);
        assert_eq!(repaired.image, first.image);

        assert_eq!(install(&f.data, f.snapshot()), 1);
        f.cache.clear_unpinned().unwrap();
        let rebuilt = cover(&f.cache, &f.raw, &opts());
        assert!(!rebuilt.cache_hit && !rebuilt.memory_hit);
        assert_eq!(rebuilt.image, first.image);
    }

    fn reseal(body: &[u8]) -> Vec<u8> {
        let mut out = body.to_vec();
        out.extend_from_slice(&Sha256::digest(body));
        out
    }

    #[test]
    fn malformed_wrong_version_and_other_namespace_blobs_never_publish() {
        let f = Fixture::new();
        let _ = cover(&f.cache, &f.raw, &opts());
        let blob = f.snapshot();
        let other_data = f.data.join("other");
        let _other = open(&other_data);
        assert_eq!(install(&other_data, blob.clone()), 0);
        let mut corrupt = blob.clone();
        corrupt[PREFIX + RECORD] ^= 1;
        assert_eq!(install(&f.data, corrupt), 0);
        let mut invalid = vec![vec![], vec![0; MAX_BLOB_BYTES + 1]];
        let body = &blob[..blob.len() - SEAL];
        for (index, byte) in [
            (7, 0),
            (8, body[8] ^ 1),
            (PREFIX - 1, 0),
            (PREFIX - 1, 7),
            (PREFIX + 32, b'/'),
        ] {
            let mut bad = body.to_vec();
            bad[index] = byte;
            invalid.push(reseal(&bad));
        }
        for w in [0_u32, 377, u32::MAX] {
            let mut bad = body.to_vec();
            bad[PREFIX + RECORD - 8..PREFIX + RECORD - 4].copy_from_slice(&w.to_le_bytes());
            invalid.push(reseal(&bad));
        }
        invalid.push(reseal(&body[..body.len() - 1]));
        let mut trailing = body.to_vec();
        trailing.push(0);
        invalid.push(reseal(&trailing));
        let mut duplicate = body.to_vec();
        duplicate[PREFIX - 1] = 2;
        duplicate.extend_from_slice(&body[PREFIX..]);
        invalid.push(reseal(&duplicate));
        for bad in invalid {
            assert_eq!(install(&f.data, bad), 0);
        }
        assert!(!cover(&f.cache, &f.raw, &opts()).memory_hit);
    }

    #[test]
    fn speculative_warm_only_yields_to_the_matching_installed_cover() {
        let f = Fixture::new();
        let _ = cover(&f.cache, &f.raw, &opts());
        let other = f._dir.path().join("another.png");
        write_cover(&other, 120);
        let other_page = Page {
            local_path: Some(other.clone()),
            ..Default::default()
        };
        super::super::render(&f.cache, &other_page, &other, &opts(), false, 0, 1.0).unwrap();
        let blob = f.snapshot();
        assert_eq!(install(&f.data, blob.clone()), 1);
        let warm = |path| {
            super::super::warm_cached_local_cover(&f.cache, path, &opts(), &|| true).unwrap()
        };
        let (warmed, events) = crate::reader_timing::capture_test_events(|| warm(f.raw.clone()));
        assert!(!warmed);
        assert!(
            events
                .as_array()
                .unwrap()
                .iter()
                .any(|e| e["name"] == "startup_cover_warm_skipped_resident")
        );
        assert_eq!(super::super::stats().resident_staged_entries, 1);
        assert!(warm(other.clone()));
        assert!(cover(&f.cache, &other, &opts()).memory_hit);
        assert!(cover(&f.cache, &f.raw, &opts()).memory_hit);
        super::super::clear_memory(&f.cache);
        assert_eq!(install(&f.data, blob), 1);
        clear();
        assert!(warm(f.raw.clone()));
        assert_eq!(super::super::stats().resident_staged_entries, 0);
    }

    #[test]
    fn only_six_recent_foreground_local_covers_fit_and_expiration_clears_the_slot() {
        let f = Fixture::new();
        let raw_page = Page {
            local_path: Some(f.raw.clone()),
            ..Default::default()
        };
        super::super::render(&f.cache, &raw_page, &f.raw, &opts(), false, 0, 1.0).unwrap();
        let _ = cover(
            &f.cache,
            &f.raw,
            &ImageOptions {
                width: 612,
                height: 840,
                ..opts()
            },
        );
        assert!(capture(&f.data).is_none());
        let mut paths = Vec::new();
        for number in 0..8 {
            let raw = f.raw.with_file_name(format!("local-{number}.png"));
            write_cover(&raw, 20 * number);
            let _ = cover(&f.cache, &raw, &opts());
            paths.push(raw);
        }
        let blob = f.snapshot();
        assert!(blob.len() <= MAX_BLOB_BYTES);
        assert_eq!(install(&f.data, blob.clone()), 6);
        for (index, raw) in paths.iter().enumerate() {
            assert_eq!(cover(&f.cache, raw, &opts()).memory_hit, index >= 2);
        }
        super::super::clear_memory(&f.cache);
        assert_eq!(install(&f.data, blob.clone()), 6);
        decoded(|state| state.staged_covers.as_mut().unwrap().expires = Instant::now());
        assert!(!cover(&f.cache, &paths[7], &opts()).memory_hit);
        assert!(!MAY_HAVE_STAGED.load(Ordering::Acquire));
        assert_eq!(install(&f.data, blob), 6);
        super::super::clear_memory(&f.cache);
        assert!(!cover(&f.cache, &paths[7], &opts()).memory_hit);
    }
}
