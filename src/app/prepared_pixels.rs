//! Worker-prepared Slint pixels with bounded reuse of immutable grayscale inputs.
//!
//! `SharedPixelBuffer` uses atomic reference counting and can cross the worker
//! channel. `Image` is constructed only when publishing on the UI thread, which
//! does not expand grayscale pixels or copy the shared RGB payload.

use std::{
    collections::{HashMap, HashSet},
    sync::{Arc, Mutex, OnceLock, Weak},
};

use image::GrayImage;
use serde::Serialize;
use slint::{Image, Rgb8Pixel, SharedPixelBuffer};

/// This budget covers retained RGB pixel payloads, not allocator bookkeeping,
/// grayscale inputs, active UI images, or in-flight worker output buffers.
const MAX_PREPARED_BYTES: usize = 12 * 1024 * 1024;
const MAX_PREPARED_ENTRIES: usize = 16;

static PREPARED: OnceLock<Mutex<PreparedCache>> = OnceLock::new();
pub(super) const MAX_NEXT_FRAME_BYTES: usize = 32 * 1024 * 1024;
static NEXT_FRAME: OnceLock<Arc<Mutex<NextFrameState>>> = OnceLock::new();

#[derive(Debug, Default)]
struct NextFrameState {
    bytes: usize,
    peak_bytes: usize,
    reservations: usize,
}

/// A separate BiliComics-style single ready-frame payload. This does not change
/// either existing 12 MiB LRU, and its pixels are counted explicitly as a new
/// budget rather than being hidden as ordinary cache ownership.
#[derive(Debug)]
pub(super) struct PreparedReservation {
    state: Arc<Mutex<NextFrameState>>,
    bytes: usize,
}

impl Drop for PreparedReservation {
    fn drop(&mut self) {
        let mut state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        state.bytes = state.bytes.saturating_sub(self.bytes);
        state.reservations = state.reservations.saturating_sub(1);
    }
}

#[derive(Debug, Clone, Copy, Serialize)]
pub(super) struct NextFrameStats {
    pub(super) budget_bytes: usize,
    pub(super) bytes: usize,
    pub(super) peak_bytes: usize,
    pub(super) reservations: usize,
}

fn next_frame_state() -> &'static Arc<Mutex<NextFrameState>> {
    NEXT_FRAME.get_or_init(|| Arc::new(Mutex::new(NextFrameState::default())))
}

fn reserve_with_state(
    state: &Arc<Mutex<NextFrameState>>,
    bytes: usize,
) -> Option<PreparedReservation> {
    if bytes == 0 || bytes > MAX_NEXT_FRAME_BYTES {
        return None;
    }
    let mut usage = state
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if usage.reservations != 0 {
        return None;
    }
    usage.bytes = bytes;
    usage.peak_bytes = usage.peak_bytes.max(bytes);
    usage.reservations = 1;
    Some(PreparedReservation {
        state: state.clone(),
        bytes,
    })
}

pub(super) fn reserve_next_frame(bytes: usize) -> Option<PreparedReservation> {
    reserve_with_state(next_frame_state(), bytes)
}

pub(super) fn next_frame_stats() -> NextFrameStats {
    let usage = next_frame_state()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    NextFrameStats {
        budget_bytes: MAX_NEXT_FRAME_BYTES,
        bytes: usage.bytes,
        peak_bytes: usage.peak_bytes,
        reservations: usage.reservations,
    }
}

/// Ready-to-publish, immutable RGB pixels. Cloning shares the pixel allocation.
#[derive(Clone, Debug)]
pub(super) struct PreparedPixels {
    pixels: SharedPixelBuffer<Rgb8Pixel>,
}

impl PreparedPixels {
    pub(super) fn width(&self) -> u32 {
        self.pixels.width()
    }

    pub(super) fn height(&self) -> u32 {
        self.pixels.height()
    }

    #[cfg(test)]
    pub(super) fn as_bytes(&self) -> &[u8] {
        self.pixels.as_bytes()
    }

    /// Call on the UI thread. Slint takes ownership of the already shared pixels.
    pub(super) fn into_image(self) -> Image {
        Image::from_rgb8(self.pixels)
    }

    fn from_grayscale(image: &GrayImage) -> Self {
        let mut pixels = SharedPixelBuffer::<Rgb8Pixel>::new(image.width(), image.height());
        for (rgb, gray) in pixels
            .make_mut_bytes()
            .chunks_exact_mut(3)
            .zip(image.as_raw())
        {
            rgb.fill(*gray);
        }
        Self { pixels }
    }

    pub(super) fn byte_len(&self) -> usize {
        self.pixels.as_bytes().len()
    }

    /// Compare the immutable shared payload, rather than equal pixel contents.
    pub(super) fn same_allocation(&self, other: &Self) -> bool {
        self.allocation_key() == other.allocation_key()
    }

    fn allocation_key(&self) -> (usize, usize) {
        let bytes = self.pixels.as_bytes();
        (bytes.as_ptr() as usize, bytes.len())
    }
}

/// Count each live shared RGB payload once, including clones in one frame.
/// Saturation makes an impossible aggregate fail the ordinary slot admission.
pub(super) fn unique_prepared_bytes(pixels: &[&PreparedPixels]) -> usize {
    let mut allocations = HashSet::new();
    let mut bytes = 0_usize;
    for pixels in pixels {
        if allocations.insert(pixels.allocation_key()) {
            bytes = bytes.saturating_add(pixels.byte_len());
        }
    }
    bytes
}

struct PreparedEntry {
    source: Weak<GrayImage>,
    pixels: PreparedPixels,
    access: u64,
}

struct PreparedCache {
    entries: HashMap<usize, PreparedEntry>,
    bytes: usize,
    access: u64,
    byte_limit: usize,
    entry_limit: usize,
}

impl PreparedCache {
    fn new(byte_limit: usize, entry_limit: usize) -> Self {
        Self {
            entries: HashMap::new(),
            bytes: 0,
            access: 0,
            byte_limit,
            entry_limit,
        }
    }

    fn remove(&mut self, identity: usize) {
        if let Some(entry) = self.entries.remove(&identity) {
            self.bytes -= entry.pixels.byte_len();
        }
    }

    fn remove_expired(&mut self) {
        let bytes = &mut self.bytes;
        self.entries.retain(|_, entry| {
            if entry.source.strong_count() == 0 {
                *bytes -= entry.pixels.byte_len();
                false
            } else {
                true
            }
        });
    }

    fn lookup(&mut self, image: &Arc<GrayImage>) -> Option<PreparedPixels> {
        self.remove_expired();
        let identity = Arc::as_ptr(image) as usize;
        let entry = self.entries.get(&identity)?;
        // An address alone is not an image identity. A successful Weak upgrade
        // and Arc comparison also reject expired inputs and replaced buffers.
        if !entry
            .source
            .upgrade()
            .is_some_and(|source| Arc::ptr_eq(&source, image))
        {
            self.remove(identity);
            return None;
        }
        self.access = self.access.saturating_add(1);
        let entry = self.entries.get_mut(&identity)?;
        entry.access = self.access;
        Some(entry.pixels.clone())
    }

    fn remember(&mut self, image: &Arc<GrayImage>, pixels: PreparedPixels) {
        self.remove_expired();
        let identity = Arc::as_ptr(image) as usize;
        self.remove(identity);
        let bytes = pixels.byte_len();
        if bytes == 0 || bytes > self.byte_limit || self.entry_limit == 0 {
            return;
        }
        while bytes > self.byte_limit - self.bytes || self.entries.len() >= self.entry_limit {
            let Some(oldest) = self
                .entries
                .iter()
                .min_by_key(|(_, entry)| entry.access)
                .map(|(identity, _)| *identity)
            else {
                break;
            };
            self.remove(oldest);
        }
        self.access = self.access.saturating_add(1);
        self.bytes += bytes;
        self.entries.insert(
            identity,
            PreparedEntry {
                source: Arc::downgrade(image),
                pixels,
                access: self.access,
            },
        );
    }
}

/// Call from a background worker after rendering. Warm calls clone only a shared
/// buffer; a miss expands the grayscale output before it reaches the UI thread.
pub(super) fn prepare(image: Arc<GrayImage>) -> PreparedPixels {
    let cache = PREPARED
        .get_or_init(|| Mutex::new(PreparedCache::new(MAX_PREPARED_BYTES, MAX_PREPARED_ENTRIES)));
    prepare_with_cache(cache, image)
}

/// Reader workers retain BiliComics' cooperative raster boundaries while
/// expanding the already quantized grayscale frame into Slint's shared RGB.
pub(super) fn prepare_cancellable(image: Arc<GrayImage>) -> anyhow::Result<PreparedPixels> {
    crate::imaging::check_decode_cancelled()?;
    let cache = PREPARED
        .get_or_init(|| Mutex::new(PreparedCache::new(MAX_PREPARED_BYTES, MAX_PREPARED_ENTRIES)));
    if let Some(prepared) = cache
        .lock()
        .expect("Prepared pixel cache lock")
        .lookup(&image)
    {
        return Ok(prepared);
    }
    let mut pixels = SharedPixelBuffer::<Rgb8Pixel>::new(image.width(), image.height());
    let length = image.width() as usize * image.height() as usize;
    let output = pixels.make_mut_bytes();
    for (block, values) in image.as_raw()[..length].chunks(256).enumerate() {
        crate::imaging::check_decode_cancelled()?;
        let first = block * 256 * 3;
        for (rgb, gray) in output[first..first + values.len() * 3]
            .chunks_exact_mut(3)
            .zip(values)
        {
            rgb.fill(*gray);
        }
    }
    crate::imaging::check_decode_cancelled()?;
    let prepared = PreparedPixels { pixels };
    let mut state = cache.lock().expect("Prepared pixel cache lock");
    if let Some(existing) = state.lookup(&image) {
        return Ok(existing);
    }
    state.remember(&image, prepared.clone());
    Ok(prepared)
}

fn prepare_with_cache(cache: &Mutex<PreparedCache>, image: Arc<GrayImage>) -> PreparedPixels {
    if let Some(prepared) = cache
        .lock()
        .expect("Prepared pixel cache lock")
        .lookup(&image)
    {
        return prepared;
    }
    // Keep conversion outside the cache lock, so another ready worker is not
    // delayed by an unrelated large image. Workers never mutate cached pixels.
    let prepared = PreparedPixels::from_grayscale(&image);
    let mut cache = cache.lock().expect("Prepared pixel cache lock");
    if let Some(existing) = cache.lookup(&image) {
        return existing;
    }
    cache.remember(&image, prepared.clone());
    prepared
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(values: &[u8]) -> Arc<GrayImage> {
        Arc::new(GrayImage::from_raw(values.len() as u32, 1, values.to_vec()).unwrap())
    }

    fn address(pixels: &PreparedPixels) -> *const Rgb8Pixel {
        pixels.pixels.as_slice().as_ptr()
    }

    #[test]
    fn worker_pixels_are_send_and_sync() {
        fn require_send_sync<T: Send + Sync>() {}
        require_send_sync::<PreparedPixels>();
        require_send_sync::<PreparedReservation>();
    }

    #[test]
    fn next_frame_reservation_covers_scribe_and_releases_on_drop() {
        let state = Arc::new(Mutex::new(NextFrameState::default()));
        let scribe_rgb = 1_860 * 2_480 * 3;
        assert!(scribe_rgb > MAX_PREPARED_BYTES);
        let reservation = reserve_with_state(&state, scribe_rgb).unwrap();
        assert_eq!(state.lock().unwrap().bytes, scribe_rgb);
        assert!(reserve_with_state(&state, 1).is_none());
        drop(reservation);
        assert_eq!(state.lock().unwrap().bytes, 0);
        assert_eq!(state.lock().unwrap().reservations, 0);
        assert!(reserve_with_state(&state, MAX_NEXT_FRAME_BYTES + 1).is_none());
        let reservation = reserve_with_state(&state, MAX_NEXT_FRAME_BYTES).unwrap();
        assert_eq!(state.lock().unwrap().peak_bytes, MAX_NEXT_FRAME_BYTES);
        drop(reservation);
        assert_eq!(state.lock().unwrap().bytes, 0);
    }

    #[test]
    fn unique_rgb_payload_bytes_count_shared_clones_once() {
        let pixels =
            PreparedPixels::from_grayscale(&GrayImage::from_raw(3, 1, vec![17, 68, 255]).unwrap());
        let first_clone = pixels.clone();
        let second_clone = first_clone.clone();
        assert!(pixels.same_allocation(&first_clone));
        assert!(first_clone.same_allocation(&second_clone));
        assert_eq!(
            unique_prepared_bytes(&[&pixels, &first_clone, &second_clone]),
            9
        );
        assert_eq!(unique_prepared_bytes(&[]), 0);
    }

    #[test]
    fn unique_rgb_payload_bytes_count_equal_but_distinct_allocations() {
        let image = GrayImage::from_raw(3, 1, vec![17, 68, 255]).unwrap();
        let first = PreparedPixels::from_grayscale(&image);
        let second = PreparedPixels::from_grayscale(&image);
        let second_clone = second.clone();
        assert_eq!(first.as_bytes(), second.as_bytes());
        assert!(!first.same_allocation(&second));
        assert!(second.same_allocation(&second_clone));
        assert_eq!(unique_prepared_bytes(&[&first, &second, &second_clone]), 18);
    }

    #[test]
    fn next_frame_reservation_counts_scribe_rgb_and_gray_payloads_together() {
        let state = Arc::new(Mutex::new(NextFrameState::default()));
        let image = GrayImage::from_pixel(1_860, 2_480, image::Luma([68]));
        let pixels = PreparedPixels::from_grayscale(&image);
        let shared_pixels = pixels.clone();
        let retained_bytes = unique_prepared_bytes(&[&pixels, &shared_pixels])
            .checked_add(image.as_raw().capacity())
            .unwrap();
        assert_eq!(retained_bytes, 1_860 * 2_480 * 4);
        assert!(retained_bytes > MAX_PREPARED_BYTES);
        assert!(retained_bytes < MAX_NEXT_FRAME_BYTES);
        let reservation = reserve_with_state(&state, retained_bytes).unwrap();
        assert_eq!(state.lock().unwrap().bytes, retained_bytes);
        assert!(reserve_with_state(&state, retained_bytes).is_none());
        drop(reservation);
        assert_eq!(state.lock().unwrap().bytes, 0);
        assert_eq!(state.lock().unwrap().reservations, 0);
        assert!(retained_bytes * 2 > MAX_NEXT_FRAME_BYTES);
        assert!(reserve_with_state(&state, retained_bytes * 2).is_none());
        assert_eq!(state.lock().unwrap().bytes, 0);
        let reservation = reserve_with_state(&state, retained_bytes).unwrap();
        assert_eq!(state.lock().unwrap().peak_bytes, retained_bytes);
        drop(reservation);
        assert_eq!(state.lock().unwrap().bytes, 0);
    }

    #[test]
    fn cancellable_reader_pixels_match_the_existing_expansion() {
        let source = Arc::new(GrayImage::from_raw(2, 2, vec![0, 68, 127, 255, 13, 27]).unwrap());
        let expected = PreparedPixels::from_grayscale(&source);
        let actual = prepare_cancellable(source).unwrap();
        assert_eq!(actual.width(), expected.width());
        assert_eq!(actual.height(), expected.height());
        assert_eq!(actual.as_bytes(), expected.as_bytes());
    }

    #[test]
    fn grayscale_expansion_preserves_dimensions_and_pixel_values() {
        let pixels = PreparedPixels::from_grayscale(
            &GrayImage::from_raw(2, 2, vec![0, 68, 127, 255]).unwrap(),
        );
        assert_eq!((pixels.width(), pixels.height()), (2, 2));
        assert_eq!(
            pixels.pixels.as_bytes(),
            [0, 0, 0, 68, 68, 68, 127, 127, 127, 255, 255, 255]
        );
    }

    #[test]
    fn preparing_the_same_immutable_input_reuses_the_rgb_allocation() {
        let cache = Mutex::new(PreparedCache::new(24, 2));
        let source = input(&[17, 200]);
        let first = prepare_with_cache(&cache, source.clone());
        let second = prepare_with_cache(&cache, source);
        assert_eq!(address(&first), address(&second));
        assert_eq!(cache.lock().unwrap().bytes, 6);
    }

    #[test]
    fn distinct_inputs_do_not_share_pixels_even_with_equal_dimensions() {
        let cache = Mutex::new(PreparedCache::new(24, 2));
        let first = prepare_with_cache(&cache, input(&[17, 200]));
        let second = prepare_with_cache(&cache, input(&[200, 17]));
        assert_ne!(address(&first), address(&second));
        assert_eq!(first.pixels.as_bytes(), [17, 17, 17, 200, 200, 200]);
        assert_eq!(second.pixels.as_bytes(), [200, 200, 200, 17, 17, 17]);
    }

    #[test]
    fn least_recently_used_pixels_are_evicted_without_affecting_live_output() {
        let cache = Mutex::new(PreparedCache::new(24, 2));
        let a = input(&[1, 2]);
        let b = input(&[3, 4]);
        let c = input(&[5, 6]);
        let a_pixels = prepare_with_cache(&cache, a.clone());
        let b_pixels = prepare_with_cache(&cache, b.clone());
        let a_hit = prepare_with_cache(&cache, a.clone());
        let _ = prepare_with_cache(&cache, c.clone());
        let mut cache = cache.lock().unwrap();
        assert!(cache.lookup(&a).is_some());
        assert!(cache.lookup(&b).is_none());
        assert!(cache.lookup(&c).is_some());
        assert_eq!(cache.entries.len(), 2);
        assert_eq!(cache.bytes, 12);
        assert_eq!(address(&a_pixels), address(&a_hit));
        assert_eq!(b_pixels.pixels.as_bytes(), [3, 3, 3, 4, 4, 4]);
    }

    #[test]
    fn payload_budget_is_enforced_separately_from_entry_budget() {
        let cache = Mutex::new(PreparedCache::new(12, 16));
        let a = input(&[1, 2]);
        let b = input(&[3, 4]);
        let c = input(&[5, 6]);
        let _ = prepare_with_cache(&cache, a.clone());
        let _ = prepare_with_cache(&cache, b.clone());
        let _ = prepare_with_cache(&cache, c.clone());
        let mut cache = cache.lock().unwrap();
        assert_eq!(cache.bytes, 12);
        assert_eq!(cache.entries.len(), 2);
        assert!(cache.lookup(&a).is_none());
        assert!(cache.lookup(&b).is_some());
        assert!(cache.lookup(&c).is_some());
    }

    #[test]
    fn oversized_output_is_returned_without_becoming_resident() {
        let cache = Mutex::new(PreparedCache::new(3, 16));
        let image = input(&[17, 200]);
        let prepared = prepare_with_cache(&cache, image);
        assert_eq!(prepared.pixels.as_bytes(), [17, 17, 17, 200, 200, 200]);
        let cache = cache.lock().unwrap();
        assert_eq!(cache.bytes, 0);
        assert!(cache.entries.is_empty());
    }

    #[test]
    fn cache_does_not_keep_grayscale_sources_alive() {
        let cache = Mutex::new(PreparedCache::new(12, 16));
        let source = input(&[17, 200]);
        let weak = Arc::downgrade(&source);
        let pixels = prepare_with_cache(&cache, source);
        assert!(weak.upgrade().is_none());
        let replacement = input(&[3, 4]);
        let _ = prepare_with_cache(&cache, replacement.clone());
        let cache = cache.lock().unwrap();
        assert_eq!(cache.bytes, 6);
        assert_eq!(cache.entries.len(), 1);
        assert_eq!(pixels.pixels.as_bytes(), [17, 17, 17, 200, 200, 200]);
    }

    #[test]
    fn making_a_uniquely_owned_source_mutable_cannot_reuse_stale_pixels() {
        let cache = Mutex::new(PreparedCache::new(12, 16));
        let mut source = input(&[17, 200]);
        let old_pixels = prepare_with_cache(&cache, source.clone());
        Arc::make_mut(&mut source).put_pixel(0, 0, image::Luma([33]));
        let new_pixels = prepare_with_cache(&cache, source);
        assert_ne!(address(&old_pixels), address(&new_pixels));
        assert_eq!(new_pixels.pixels.as_bytes(), [33, 33, 33, 200, 200, 200]);
        assert_eq!(old_pixels.pixels.as_bytes(), [17, 17, 17, 200, 200, 200]);
    }

    #[test]
    fn simultaneous_preparations_publish_one_cached_allocation() {
        let cache = Arc::new(Mutex::new(PreparedCache::new(12, 16)));
        let source = input(&[17, 200]);
        let barrier = Arc::new(std::sync::Barrier::new(3));
        let workers = (0..2)
            .map(|_| {
                let cache = cache.clone();
                let source = source.clone();
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    prepare_with_cache(&cache, source)
                })
            })
            .collect::<Vec<_>>();
        barrier.wait();
        let mut prepared = workers.into_iter().map(|worker| worker.join().unwrap());
        let first = prepared.next().unwrap();
        let second = prepared.next().unwrap();
        assert_eq!(address(&first), address(&second));
        assert_eq!(cache.lock().unwrap().bytes, 6);
    }
}
