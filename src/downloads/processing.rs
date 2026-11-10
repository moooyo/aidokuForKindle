//! Optional, durable offline accelerators. Canonical originals remain intact.

use std::{
    fs,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result, ensure};
use image::{DynamicImage, ImageFormat, ImageReader, Limits, imageops::FilterType};
use serde::{Deserialize, Serialize};

use crate::{
    cache::CacheManager,
    domain::{Page, Preferences},
    imaging::{self, DecodeCancellation, MAX_DECODE_BYTES, MAX_PAGE_BYTES, MAX_PAGE_PIXELS},
};

pub(super) const DEFAULT_VIEWPORT: (u32, u32) = (618, 824);
const MAX_LOGICAL_SIDE: u32 = 4_096;
const PREPARATION_VERSION: u8 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub(super) struct ProcessingSettings {
    resize: bool,
    grayscale: bool,
    side: u32,
}

impl ProcessingSettings {
    pub(super) fn from_preferences(
        preferences: &Preferences,
        viewport: (u32, u32),
    ) -> Result<Self> {
        validate_viewport(viewport)?;
        Ok(Self {
            resize: preferences.resize_downloads,
            grayscale: preferences.grayscale_downloads,
            // Logical coordinates are half the native panel resolution. A
            // square upper bound covers both orientations without preparing
            // another, lower-resolution copy when the device is rotated.
            side: viewport.0.max(viewport.1) * 2,
        })
    }

    pub(super) fn enabled(self) -> bool {
        self.resize || self.grayscale
    }

    fn identity(self, page: &Page, checksum: &str) -> Page {
        Page {
            url: format!(
                "aidoku-prepared:v{PREPARATION_VERSION}:{}:{checksum}:{}:{}:{}",
                CacheManager::page_key(page),
                u8::from(self.resize),
                u8::from(self.grayscale),
                if self.resize { self.side } else { 0 },
            ),
            ..Page::default()
        }
    }
}

pub(super) fn validate_viewport(viewport: (u32, u32)) -> Result<()> {
    ensure!(
        (1..=MAX_LOGICAL_SIDE).contains(&viewport.0)
            && (1..=MAX_LOGICAL_SIDE).contains(&viewport.1),
        "The download preparation viewport is outside the supported range"
    );
    Ok(())
}

/// Look up an existing accelerator without decoding or preparing an image.
/// The returned file is never published as the original page's local path.
pub(super) fn lookup(
    cache: &CacheManager,
    page: &Page,
    original: &Path,
    preferences: &Preferences,
    viewport: (u32, u32),
) -> Result<Option<PathBuf>> {
    let settings = ProcessingSettings::from_preferences(preferences, viewport)?;
    if !settings.enabled() {
        return Ok(None);
    }
    let checksum = cache.fingerprint(original)?;
    cache.lookup_raw(&settings.identity(page, &checksum))
}

/// Prepare an additional cache artifact with the same durable download owner.
/// Changing flags, panel geometry, source bytes, URL, or headers changes its key.
pub(super) fn prepare(
    cache: &CacheManager,
    page: &Page,
    original: &Path,
    settings: ProcessingSettings,
    owner: &str,
    cancellation: &DecodeCancellation,
    stopped: impl Fn() -> bool,
) -> Result<Option<PathBuf>> {
    if !settings.enabled() {
        return Ok(None);
    }
    let check = || -> Result<()> {
        if stopped() {
            cancellation.cancel();
        }
        cancellation.check()
    };
    check()?;
    let _original_lease = cache.lease_path(original)?;
    let checksum = cache.fingerprint(original)?;
    let identity = settings.identity(page, &checksum);
    if let Some(path) = cache.lookup_raw(&identity)? {
        check()?;
        cache.pin_raw(&identity, owner)?;
        return Ok(Some(path));
    }

    let output = transform(original, settings, &check)?;
    check()?;
    ensure!(
        cache.fingerprint(original)? == checksum,
        "The original image changed while preparing its offline copy"
    );
    let expected = u64::from(output.width())
        .saturating_mul(u64::from(output.height()))
        .saturating_mul(u64::from(output.color().bytes_per_pixel()))
        .saturating_add(65_536)
        .min(MAX_PAGE_BYTES);
    let mut write = cache.begin_raw(&identity, Some(owner), expected)?;
    output
        .save_with_format(write.temp_path(), ImageFormat::Png)
        .context("Encode the prepared offline image")?;
    drop(output);
    check()?;
    let bytes = fs::metadata(write.temp_path())?.len();
    ensure!(
        bytes > 0 && bytes <= MAX_PAGE_BYTES,
        "The prepared offline image exceeds the compressed-page limit"
    );
    write.ensure_capacity(bytes)?;
    cache.validate_path(write.temp_path())?;
    check()?;
    // The original pin remains independent. Cancellation or an unsuccessful
    // derivative write can never replace or invalidate the offline original.
    write.commit().map(Some)
}

fn transform(
    original: &Path,
    settings: ProcessingSettings,
    check: &impl Fn() -> Result<()>,
) -> Result<DynamicImage> {
    let metadata = fs::metadata(original).context("Inspect an offline image for preparation")?;
    ensure!(
        metadata.is_file() && metadata.len() > 0 && metadata.len() <= MAX_PAGE_BYTES,
        "The offline original is empty or exceeds the compressed-page limit"
    );
    let _permit = imaging::decode_permit()?;
    check()?;
    let dimensions = ImageReader::open(original)
        .context("Open the offline image header")?
        .with_guessed_format()?
        .into_dimensions()
        .context("Read the offline image dimensions")?;
    ensure!(
        dimensions.0 > 0
            && dimensions.1 > 0
            && u64::from(dimensions.0) * u64::from(dimensions.1) <= MAX_PAGE_PIXELS,
        "This large image retains its original; full-image offline preparation is unsupported"
    );
    let mut reader = ImageReader::open(original)
        .context("Open the offline original")?
        .with_guessed_format()?;
    let mut limits = Limits::default();
    limits.max_image_width = Some(32_768);
    limits.max_image_height = Some(131_072);
    limits.max_alloc = Some(MAX_DECODE_BYTES);
    reader.limits(limits);
    let mut pixels = reader
        .decode()
        .context("Decode the offline original within the image memory budget")?
        .into_rgba8();
    check()?;
    // Composite onto white before interpolation, matching the Reader's paper
    // background and avoiding dark fringes from transparent source pixels.
    for (row, pixels) in pixels.rows_mut().enumerate() {
        if row % 64 == 0 {
            check()?;
        }
        for pixel in pixels {
            let alpha = u32::from(pixel[3]);
            for channel in &mut pixel.0[..3] {
                *channel = ((u32::from(*channel) * alpha + 255 * (255 - alpha) + 127) / 255) as u8;
            }
            pixel[3] = 255;
        }
    }
    check()?;
    let mut output = DynamicImage::ImageRgba8(pixels);
    if settings.resize && (output.width() > settings.side || output.height() > settings.side) {
        output = output.resize(settings.side, settings.side, FilterType::Lanczos3);
    }
    check()?;
    if settings.grayscale {
        output = DynamicImage::ImageLuma8(output.into_luma8());
    }
    check()?;
    Ok(output)
}

#[cfg(test)]
mod tests {
    use std::{collections::BTreeMap, fs::File, io::BufWriter};

    use image::{GenericImageView, ImageBuffer, Rgba};

    use super::*;

    struct Fixture {
        _directory: tempfile::TempDir,
        cache: CacheManager,
        page: Page,
        original: PathBuf,
    }

    impl Fixture {
        fn new() -> Self {
            let directory = tempfile::tempdir().unwrap();
            let cache = CacheManager::open(directory.path()).unwrap();
            let page = Page {
                url: "https://example.test/original.png".into(),
                headers: BTreeMap::from([("Authorization".into(), "fixture-one".into())]),
                local_path: None,
            };
            let write = cache
                .begin_raw(&page, Some("download:test"), 4_096)
                .unwrap();
            let artwork = ImageBuffer::from_fn(96, 48, |x, y| {
                Rgba([((x * 7) % 256) as u8, ((y * 9) % 256) as u8, 41, 255])
            });
            DynamicImage::ImageRgba8(artwork)
                .write_to(
                    &mut BufWriter::new(File::create(write.temp_path()).unwrap()),
                    ImageFormat::Png,
                )
                .unwrap();
            cache.validate_path(write.temp_path()).unwrap();
            let original = write.commit().unwrap();
            Self {
                _directory: directory,
                cache,
                page,
                original,
            }
        }

        fn prepare(&self, preferences: &Preferences) -> PathBuf {
            let settings = ProcessingSettings::from_preferences(preferences, (8, 12)).unwrap();
            imaging::with_background_decode(None, || {
                prepare(
                    &self.cache,
                    &self.page,
                    &self.original,
                    settings,
                    "download:test",
                    &DecodeCancellation::new(),
                    || false,
                )
            })
            .unwrap()
            .unwrap()
        }
    }

    #[test]
    fn resize_preserves_color_and_raw_identity_without_upscaling() {
        let fixture = Fixture::new();
        let checksum = fixture.cache.fingerprint(&fixture.original).unwrap();
        let preferences = Preferences {
            resize_downloads: true,
            ..Preferences::default()
        };
        let prepared = fixture.prepare(&preferences);
        let output = ImageReader::open(&prepared)
            .unwrap()
            .with_guessed_format()
            .unwrap()
            .decode()
            .unwrap();
        assert_eq!(output.dimensions(), (24, 12));
        assert!(output.to_rgb8().pixels().any(|pixel| pixel[0] != pixel[1]));
        assert_ne!(prepared, fixture.original);
        assert_eq!(
            fixture.cache.lookup_raw(&fixture.page).unwrap(),
            Some(fixture.original.clone())
        );
        assert_eq!(
            fixture.cache.fingerprint(&fixture.original).unwrap(),
            checksum
        );
        assert_eq!(fixture.cache.stats().pinned_entries, 2);

        let larger = ProcessingSettings::from_preferences(&preferences, (100, 100)).unwrap();
        let output = transform(&fixture.original, larger, &|| Ok(())).unwrap();
        assert_eq!(output.dimensions(), (96, 48));
    }

    #[test]
    fn grayscale_preserves_geometry_and_survives_cache_reopen() {
        let fixture = Fixture::new();
        let preferences = Preferences {
            grayscale_downloads: true,
            ..Preferences::default()
        };
        let prepared = fixture.prepare(&preferences);
        let output = ImageReader::open(&prepared)
            .unwrap()
            .with_guessed_format()
            .unwrap()
            .decode()
            .unwrap();
        assert_eq!(output.dimensions(), (96, 48));
        assert_eq!(output.color(), image::ColorType::L8);
        let reopened = CacheManager::open(fixture._directory.path()).unwrap();
        assert_eq!(
            lookup(
                &reopened,
                &fixture.page,
                &fixture.original,
                &preferences,
                (8, 12)
            )
            .unwrap(),
            Some(prepared)
        );
    }

    #[test]
    fn settings_and_header_identities_cannot_reuse_different_output() {
        let fixture = Fixture::new();
        let preferences = Preferences {
            resize_downloads: true,
            ..Preferences::default()
        };
        let prepared = fixture.prepare(&preferences);
        assert_eq!(
            lookup(
                &fixture.cache,
                &fixture.page,
                &fixture.original,
                &preferences,
                (12, 8)
            )
            .unwrap(),
            Some(prepared)
        );
        let mut other = fixture.page.clone();
        other
            .headers
            .insert("Authorization".into(), "fixture-two".into());
        assert!(
            lookup(
                &fixture.cache,
                &other,
                &fixture.original,
                &preferences,
                (8, 12)
            )
            .unwrap()
            .is_none()
        );
        let grayscale = Preferences {
            grayscale_downloads: true,
            ..preferences.clone()
        };
        assert!(
            lookup(
                &fixture.cache,
                &fixture.page,
                &fixture.original,
                &grayscale,
                (8, 12)
            )
            .unwrap()
            .is_none()
        );
        assert!(
            lookup(
                &fixture.cache,
                &fixture.page,
                &fixture.original,
                &preferences,
                (8, 16)
            )
            .unwrap()
            .is_none()
        );
        assert!(
            lookup(
                &fixture.cache,
                &fixture.page,
                &fixture.original,
                &Preferences::default(),
                (8, 12)
            )
            .unwrap()
            .is_none()
        );
    }

    #[test]
    fn cancelled_preparation_keeps_only_the_original_pin_and_artifact() {
        let fixture = Fixture::new();
        let settings = ProcessingSettings::from_preferences(
            &Preferences {
                resize_downloads: true,
                ..Preferences::default()
            },
            (8, 12),
        )
        .unwrap();
        let cancellation = DecodeCancellation::new();
        cancellation.cancel();
        let error = prepare(
            &fixture.cache,
            &fixture.page,
            &fixture.original,
            settings,
            "download:test",
            &cancellation,
            || false,
        )
        .unwrap_err();
        assert!(imaging::is_decode_cancelled(&error));
        assert_eq!(fixture.cache.stats().pinned_entries, 1);
        assert!(fixture.cache.lookup_raw(&fixture.page).unwrap().is_some());
    }

    #[test]
    fn alpha_is_composited_onto_paper_before_grayscale_conversion() {
        let directory = tempfile::tempdir().unwrap();
        let original = directory.path().join("transparent.png");
        let image = ImageBuffer::from_fn(2, 1, |x, _| {
            if x == 0 {
                Rgba([0_u8, 0, 0, 0])
            } else {
                Rgba([0, 0, 0, 128])
            }
        });
        DynamicImage::ImageRgba8(image).save(&original).unwrap();
        let settings = ProcessingSettings::from_preferences(
            &Preferences {
                grayscale_downloads: true,
                ..Preferences::default()
            },
            (8, 12),
        )
        .unwrap();
        let output = transform(&original, settings, &|| Ok(()))
            .unwrap()
            .into_luma8();
        assert_eq!(output.get_pixel(0, 0)[0], 255);
        assert_eq!(output.get_pixel(1, 0)[0], 127);
    }
}
