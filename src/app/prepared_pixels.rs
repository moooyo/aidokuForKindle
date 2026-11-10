//! Worker-prepared Slint pixels with bounded reuse of immutable grayscale inputs.
//!
//! `SharedPixelBuffer` uses atomic reference counting and can cross the worker
//! channel. `Image` is constructed only when publishing on the UI thread, which
//! does not expand grayscale pixels or copy the shared RGB payload.

use std::{
    collections::HashMap,
    sync::{Arc, Mutex, OnceLock, Weak},
};

use image::GrayImage;
use slint::{Image, Rgb8Pixel, SharedPixelBuffer};

/// This budget covers retained RGB pixel payloads, not allocator bookkeeping,
/// grayscale inputs, active UI images, or in-flight worker output buffers.
const MAX_PREPARED_BYTES: usize = 12 * 1024 * 1024;
const MAX_PREPARED_ENTRIES: usize = 16;

static PREPARED: OnceLock<Mutex<PreparedCache>> = OnceLock::new();

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

    fn byte_len(&self) -> usize {
        self.pixels.as_bytes().len()
    }
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
