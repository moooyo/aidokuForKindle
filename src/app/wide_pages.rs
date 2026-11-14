//! Source-page-preserving navigation frames for landscape artwork.
//!
//! The normal renderer prepares a double-width page before a worker selects a
//! half. This keeps each half at viewport resolution and reuses the existing
//! raw validation, versioned Raster key, decoder budget and prefetch scheduler.

use std::{
    path::Path,
    sync::{Arc, Mutex, OnceLock, Weak},
};

use anyhow::{Context, Result, ensure};
use image::{GrayImage, ImageReader};

use crate::{
    cache::CacheManager,
    domain::{Page, Preferences},
    imaging::{DecodeCancellation, ImageOptions},
    render_cache::{self, RenderedPage},
};

const MAX_RENDER_SIDE: u32 = 8_192;
/// This budget covers retained half-page grayscale buffers only. The underlying
/// full-page render cache and live UI textures have their own distinct bounds.
const HALF_CACHE_BYTES: usize = 6 * 1024 * 1024;
const HALF_CACHE_ENTRIES: usize = 8;
static HALVES: OnceLock<Mutex<HalfCache>> = OnceLock::new();

pub(super) struct WidePage {
    pub rendered: RenderedPage,
    pub wide: bool,
    /// Zero and one are reading-order positions, independent of physical side.
    pub half: u8,
}

pub(super) fn render(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    preferences: &Preferences,
    half: u8,
) -> Result<WidePage> {
    ensure!(
        zoom.is_finite() && (1.0..=4.0).contains(&zoom),
        "The rendered zoom must be between one and four"
    );
    render_page(
        cache,
        page,
        raw,
        options,
        vertical,
        offset,
        zoom,
        preferences,
        half,
        None,
        false,
    )
}

pub(super) fn prefetch(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    preferences: &Preferences,
    half: u8,
    cancellation: &DecodeCancellation,
) -> Result<WidePage> {
    render_page(
        cache,
        page,
        raw,
        options,
        vertical,
        offset,
        zoom,
        preferences,
        half,
        Some(cancellation),
        false,
    )
}

pub(super) fn prefetch_next(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    preferences: &Preferences,
    half: u8,
    cancellation: &DecodeCancellation,
) -> Result<WidePage> {
    render_page(
        cache,
        page,
        raw,
        options,
        vertical,
        offset,
        zoom,
        preferences,
        half,
        Some(cancellation),
        true,
    )
}

fn render_page(
    cache: &CacheManager,
    page: &Page,
    raw: &Path,
    options: &ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    preferences: &Preferences,
    half: u8,
    cancellation: Option<&DecodeCancellation>,
    private_next: bool,
) -> Result<WidePage> {
    ensure!(
        zoom.is_finite() && (1.0..=4.0).contains(&zoom),
        "The rendered zoom must be between one and four"
    );
    if matches!(
        preferences.reader_fit.as_str(),
        "original" | "page" | "height" | "width"
    ) && !vertical
        && !preferences.spread
        && !preferences.split_wide
        && zoom == 1.0
    {
        let rendered = render_cache::render_fitted_reader(
            cache,
            page,
            raw,
            options,
            crate::imaging::ReaderFit::from_key(&preferences.reader_fit),
            cancellation,
            private_next,
        )?;
        return Ok(WidePage {
            rendered,
            wide: false,
            half: 0,
        });
    }
    if let Some(cancellation) = cancellation {
        cancellation.check()?;
    }
    let mut target = options.clone();
    let wide = split_is_enabled(preferences, vertical)
        && options.width <= MAX_RENDER_SIDE / 2
        && source_is_wide(raw)?;
    if wide {
        target.width *= 2;
    }
    // Zoom belongs to the selected half. Applying it to the full source first
    // would discard the outside edges before either half was selected.
    let render_zoom = if wide { 1.0 } else { zoom };
    let mut rendered = if let Some(cancellation) = cancellation {
        if private_next {
            render_cache::prefetch_next_raster_cancellable(
                cache,
                page,
                raw,
                &target,
                vertical,
                offset,
                render_zoom,
                cancellation,
            )?
        } else {
            render_cache::prefetch_raster_cancellable(
                cache,
                page,
                raw,
                &target,
                vertical,
                offset,
                render_zoom,
                cancellation,
            )?
        }
    } else {
        render_cache::render_raster(cache, page, raw, &target, vertical, offset, render_zoom)?
    };
    if let Some(cancellation) = cancellation {
        cancellation.check()?;
    }
    // Cropping a mostly blank source can leave a one-pixel image. It has only
    // one displayable frame even when its original header described a spread.
    if !wide || rendered.image.width() < 2 {
        return Ok(WidePage {
            rendered,
            wide: false,
            half: 0,
        });
    }
    let half = half.min(1);
    let right = (preferences.reading_direction == "rtl") == (half == 0);
    let zoom_milli = (zoom * 1_000.0).round() as u32;
    let source = rendered.image.clone();
    let prepared = {
        let mut halves = HALVES
            .get_or_init(|| Mutex::new(HalfCache::new(HALF_CACHE_BYTES, HALF_CACHE_ENTRIES)))
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        halves.lookup(&source, right, zoom_milli)
    };
    rendered.image = if let Some(image) = prepared {
        image
    } else {
        let image = Arc::new(crop_half(&source, right, zoom_milli));
        let mut halves = HALVES
            .get_or_init(|| Mutex::new(HalfCache::new(HALF_CACHE_BYTES, HALF_CACHE_ENTRIES)))
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        halves.remember(&source, right, zoom_milli, image)
    };
    if let Some(cancellation) = cancellation {
        cancellation.check()?;
    }
    Ok(WidePage {
        rendered,
        wide: true,
        half,
    })
}

fn split_is_enabled(preferences: &Preferences, vertical: bool) -> bool {
    preferences.split_wide && !preferences.spread && !vertical
}

fn source_is_wide(raw: &Path) -> Result<bool> {
    let (width, height) = ImageReader::open(raw)
        .context("Open the source page header for wide-page layout")?
        .with_guessed_format()
        .context("Identify the wide-page source format")?
        .into_dimensions()
        .context("Read the wide-page source dimensions")?;
    Ok(width > height && height > 0)
}

fn crop_half(source: &GrayImage, right: bool, zoom_milli: u32) -> GrayImage {
    let middle = source.width() / 2;
    let (left, half_width) = if right {
        (middle, source.width() - middle)
    } else {
        (0, middle)
    };
    let width = (u64::from(half_width) * 1_000 / u64::from(zoom_milli)).max(1) as u32;
    let height = (u64::from(source.height()) * 1_000 / u64::from(zoom_milli)).max(1) as u32;
    image::imageops::crop_imm(
        source,
        left + (half_width - width) / 2,
        (source.height() - height) / 2,
        width,
        height,
    )
    .to_image()
}

struct HalfEntry {
    source: Weak<GrayImage>,
    right: bool,
    zoom_milli: u32,
    image: Arc<GrayImage>,
    bytes: usize,
    access: u64,
}

struct HalfCache {
    entries: Vec<HalfEntry>,
    bytes: usize,
    clock: u64,
    byte_limit: usize,
    entry_limit: usize,
}

impl HalfCache {
    fn new(byte_limit: usize, entry_limit: usize) -> Self {
        Self {
            entries: Vec::new(),
            bytes: 0,
            clock: 0,
            byte_limit,
            entry_limit,
        }
    }

    fn prune(&mut self) {
        self.entries.retain(|entry| entry.source.strong_count() > 0);
        self.bytes = self.entries.iter().map(|entry| entry.bytes).sum();
    }

    fn lookup(
        &mut self,
        source: &Arc<GrayImage>,
        right: bool,
        zoom_milli: u32,
    ) -> Option<Arc<GrayImage>> {
        self.prune();
        self.clock = self.clock.saturating_add(1);
        let entry = self.entries.iter_mut().find(|entry| {
            entry.right == right
                && entry.zoom_milli == zoom_milli
                && entry
                    .source
                    .upgrade()
                    .is_some_and(|candidate| Arc::ptr_eq(&candidate, source))
        })?;
        entry.access = self.clock;
        Some(entry.image.clone())
    }

    fn remember(
        &mut self,
        source: &Arc<GrayImage>,
        right: bool,
        zoom_milli: u32,
        image: Arc<GrayImage>,
    ) -> Arc<GrayImage> {
        if let Some(existing) = self.lookup(source, right, zoom_milli) {
            return existing;
        }
        let bytes = image.as_raw().capacity();
        if bytes > self.byte_limit || self.entry_limit == 0 {
            return image;
        }
        while !self.entries.is_empty()
            && (self.bytes.saturating_add(bytes) > self.byte_limit
                || self.entries.len() >= self.entry_limit)
        {
            let oldest = self
                .entries
                .iter()
                .enumerate()
                .min_by_key(|(_, entry)| entry.access)
                .map(|(index, _)| index)
                .unwrap();
            self.bytes -= self.entries.swap_remove(oldest).bytes;
        }
        self.clock = self.clock.saturating_add(1);
        self.entries.push(HalfEntry {
            source: Arc::downgrade(source),
            right,
            zoom_milli,
            image: image.clone(),
            bytes,
            access: self.clock,
        });
        self.bytes += bytes;
        image
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn numbered_page() -> Arc<GrayImage> {
        Arc::new(GrayImage::from_fn(7, 4, |x, y| {
            image::Luma([(x + y * 10) as u8])
        }))
    }

    #[test]
    fn both_halves_cover_every_column_of_an_odd_width_page() {
        let source = numbered_page();
        let left = crop_half(&source, false, 1_000);
        let right = crop_half(&source, true, 1_000);
        assert_eq!(left.dimensions(), (3, 4));
        assert_eq!(right.dimensions(), (4, 4));
        for y in 0..4 {
            for x in 0..7 {
                let actual = if x < 3 {
                    left.get_pixel(x, y)
                } else {
                    right.get_pixel(x - 3, y)
                };
                assert_eq!(actual, source.get_pixel(x, y));
            }
        }
    }

    #[test]
    fn zoom_is_centered_in_each_half_rather_than_the_whole_spread() {
        let source = numbered_page();
        let left = crop_half(&source, false, 2_000);
        let right = crop_half(&source, true, 2_000);
        assert_eq!(left.dimensions(), (1, 2));
        assert_eq!(right.dimensions(), (2, 2));
        assert_eq!(*left.get_pixel(0, 0), *source.get_pixel(1, 1));
        assert_eq!(*right.get_pixel(0, 0), *source.get_pixel(4, 1));
    }

    #[test]
    fn split_mode_does_not_change_spreads_or_continuous_strips() {
        let mut preferences = Preferences {
            split_wide: true,
            spread: false,
            ..Preferences::default()
        };
        assert!(split_is_enabled(&preferences, false));
        assert!(!split_is_enabled(&preferences, true));
        preferences.spread = true;
        assert!(!split_is_enabled(&preferences, false));
    }

    #[test]
    fn repeated_half_preparation_reuses_pixels_and_separates_side_and_zoom() {
        let source = numbered_page();
        let mut cache = HalfCache::new(1_024, 8);
        let first = cache.remember(
            &source,
            false,
            1_000,
            Arc::new(crop_half(&source, false, 1_000)),
        );
        assert!(Arc::ptr_eq(
            &cache.lookup(&source, false, 1_000).unwrap(),
            &first
        ));
        assert!(cache.lookup(&source, true, 1_000).is_none());
        assert!(cache.lookup(&source, false, 2_000).is_none());
        let different_source = numbered_page();
        assert!(cache.lookup(&different_source, false, 1_000).is_none());
    }

    #[test]
    fn half_cache_obeys_byte_budget_and_releases_dead_sources() {
        let source = numbered_page();
        let mut cache = HalfCache::new(20, 8);
        cache.remember(
            &source,
            false,
            1_000,
            Arc::new(crop_half(&source, false, 1_000)),
        );
        cache.remember(
            &source,
            true,
            1_000,
            Arc::new(crop_half(&source, true, 1_000)),
        );
        assert!(cache.bytes <= 20);
        assert_eq!(cache.entries.len(), 1);
        drop(source);
        cache.prune();
        assert_eq!(cache.bytes, 0);
        assert!(cache.entries.is_empty());
    }

    #[test]
    fn an_oversized_half_is_returned_without_resident_retention() {
        let source = numbered_page();
        let mut cache = HalfCache::new(4, 2);
        let image = Arc::new(crop_half(&source, true, 1_000));
        let returned = cache.remember(&source, true, 1_000, image.clone());
        assert!(Arc::ptr_eq(&image, &returned));
        assert!(cache.entries.is_empty());
        assert_eq!(cache.bytes, 0);
    }

    #[test]
    fn next_raw_half_retains_and_promotes_the_full_raster_key() {
        let directory = tempfile::tempdir().unwrap();
        let raw = directory.path().join("wide-source.png");
        GrayImage::from_fn(7, 4, |x, y| image::Luma([(x + y * 17) as u8]))
            .save(&raw)
            .unwrap();
        let page = Page {
            local_path: Some(raw.clone()),
            ..Page::default()
        };
        let cache = CacheManager::open_with_config(
            directory.path().join("state"),
            crate::cache::CacheConfig {
                max_unpinned_bytes: 128 * 1024 * 1024,
                min_free_bytes: 0,
            },
        )
        .unwrap();
        let preferences = Preferences {
            split_wide: true,
            spread: false,
            reading_direction: "rtl".into(),
            ..Preferences::default()
        };
        let options = ImageOptions {
            width: 4,
            height: 4,
            crop_borders: false,
            dither: false,
            hardware_dither: true,
            contrast: 0,
        };
        let cancellation = DecodeCancellation::new();
        let prepared = prefetch_next(
            &cache,
            &page,
            &raw,
            &options,
            false,
            0,
            1.0,
            &preferences,
            0,
            &cancellation,
        )
        .unwrap();
        assert!(prepared.wide);
        assert_eq!(prepared.rendered.image.dimensions(), (4, 4));
        assert!(
            prepared
                .rendered
                .image
                .as_raw()
                .iter()
                .any(|sample| sample % 17 != 0)
        );
        let retained = prepared.rendered.retained_raster.unwrap();
        assert_eq!(retained.retained_bytes(), 28);
        assert!(
            prepared
                .rendered
                .source_proof
                .unwrap()
                .verify(&cache)
                .unwrap()
        );
        retained.promote(&cache).unwrap();
        let full_options = ImageOptions {
            width: 8,
            ..options
        };
        let full =
            render_cache::render_raster(&cache, &page, &raw, &full_options, false, 0, 1.0).unwrap();
        assert!(full.memory_hit);
        assert_eq!(full.image.dimensions(), (7, 4));
        assert_eq!(cache.stats().commits, 0);
        render_cache::clear_memory(&cache);
    }
}
