//! Opt-in, real-input Reader CPU measurements for the remote worker.
//!
//! The loop profile keeps its common decoder and Lanczos3 resizer unchanged.
//! The separate BiliComics profile compares complete bounded CPU preparation
//! against an independent generic-pixel, Triangle and Bayer reference.
//! Encoding, publication, UI preparation and native output are measured elsewhere.

use std::{fs, path::PathBuf, time::Instant};

use anyhow::{Context, Result, ensure};
use image::{
    GenericImageView, GrayImage, Luma,
    imageops::{self, FilterType},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::{ImageOptions, tests};

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Case {
    pub(crate) name: String,
    pub(crate) path: PathBuf,
    pub(crate) target_width: u32,
    pub(crate) target_height: u32,
    pub(crate) crop_borders: bool,
    pub(crate) dither: bool,
    pub(crate) contrast: i32,
}

impl Case {
    pub(crate) fn options(&self) -> ImageOptions {
        ImageOptions {
            width: self.target_width,
            height: self.target_height,
            crop_borders: self.crop_borders,
            dither: self.dither,
            hardware_dither: false,
            contrast: self.contrast,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Input {
    pub(crate) schema_version: u8,
    pub(crate) samples: usize,
    pub(crate) output_dir: PathBuf,
    pub(crate) cases: Vec<Case>,
}

pub(crate) fn load() -> Result<Input> {
    ensure!(
        !cfg!(debug_assertions),
        "Use a release build for the real-input profile"
    );
    let path = std::env::var_os("AIDOKU_READER_PROFILE_INPUT")
        .context("Set AIDOKU_READER_PROFILE_INPUT to the remote fixture manifest")?;
    let input: Input = serde_json::from_slice(&fs::read(path)?)?;
    ensure!(
        input.schema_version == 1,
        "Unsupported Reader profile schema"
    );
    ensure!(
        (5..=31).contains(&input.samples),
        "Use five to thirty-one paired samples"
    );
    ensure!(
        !input.cases.is_empty() && input.cases.len() <= 16,
        "The Reader profile case count is invalid"
    );
    for case in &input.cases {
        super::check_options(&case.options())?;
    }
    Ok(input)
}

pub(crate) fn elapsed_ns(start: Instant) -> u64 {
    start.elapsed().as_nanos().min(u128::from(u64::MAX)) as u64
}

pub(crate) fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub(crate) fn save(input: &Input, name: &str, report: &Value) -> Result<()> {
    fs::create_dir_all(&input.output_dir)?;
    let path = input.output_dir.join(format!("{name}.json"));
    fs::write(&path, serde_json::to_vec_pretty(report)?)?;
    eprintln!("Reader profile saved: {}", path.display());
    Ok(())
}

fn raster(bytes: &[u8], options: &ImageOptions, legacy: bool) -> Result<(GrayImage, Value)> {
    let total = Instant::now();
    let guard = super::decode_permit()?;
    super::check_decode_cancelled()?;
    super::check_options(options)?;
    ensure!(
        !super::needs_png_streaming(bytes)?,
        "This profile covers the bounded full decoder"
    );
    let admission_ns = elapsed_ns(total);

    let started = Instant::now();
    let mut gray = super::decode_grayscale(bytes)?;
    let source_decode_ns = elapsed_ns(started);
    let source_dimensions = gray.dimensions();
    let started = Instant::now();
    if options.crop_borders {
        gray = if legacy {
            tests::reference_crop_white_borders(gray)
        } else {
            super::crop_white_borders(gray)
        };
    }
    let crop_ns = elapsed_ns(started);
    let cropped_dimensions = gray.dimensions();
    let (width, height) =
        super::fit_dimensions(gray.width(), gray.height(), options.width, options.height);
    let started = Instant::now();
    let mut output = super::row_resize::resize_gray(&gray, width, height)?;
    let resize_ns = elapsed_ns(started);
    let started = Instant::now();
    if legacy {
        tests::reference_apply_contrast(&mut output, options.contrast);
    } else {
        super::apply_contrast(&mut output, options.contrast);
    }
    let contrast_ns = elapsed_ns(started);
    let started = Instant::now();
    if legacy {
        tests::reference_quantize(&mut output, options.dither);
    } else {
        super::quantize(&mut output, options.dither);
    }
    let quantize_ns = elapsed_ns(started);
    drop(gray);
    drop(guard);
    let total_ns = elapsed_ns(total);
    Ok((
        output,
        json!({
            "admission_ns": admission_ns,
            "source_decode_ns": source_decode_ns,
            "crop_ns": crop_ns,
            "resize_ns": resize_ns,
            "contrast_ns": contrast_ns,
            "quantize_ns": quantize_ns,
            "total_ns": total_ns,
            "source_dimensions": source_dimensions,
            "cropped_dimensions": cropped_dimensions,
        }),
    ))
}

#[test]
#[ignore = "Run only on the remote worker with a release build and real fixture manifest"]
fn real_reader_raster_profile() -> Result<()> {
    let input = load()?;
    let mut cases = Vec::new();
    for case in &input.cases {
        let bytes = fs::read(&case.path)?;
        let options = case.options();
        let (expected, _) = raster(&bytes, &options, true)?;
        let actual = super::decode_page(&bytes, &options)?;
        ensure!(
            actual == expected,
            "The production Reader pixels differ for {}",
            case.name
        );
        let mut previous = Vec::new();
        let mut production = Vec::new();
        let mut order = Vec::new();
        for sample in 0..input.samples {
            let pair = if sample % 2 == 0 {
                [true, false]
            } else {
                [false, true]
            };
            order.push(if pair[0] { "AB" } else { "BA" });
            for legacy in pair {
                let (image, timing) = raster(std::hint::black_box(&bytes), &options, legacy)?;
                ensure!(
                    image == expected,
                    "A sampled Reader image differs for {}",
                    case.name
                );
                if legacy {
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
            "pixel_sha256": digest(expected.as_raw()),
            "output_dimensions": expected.dimensions(),
            "pixel_equal": true,
            "order": order,
            "previous": previous,
            "production": production,
        }));
    }
    save(
        &input,
        "raster",
        &json!({
            "schema_version": 1,
            "scope": "Release CPU preparation on the remote host; excludes source I/O, processed PNG persistence, UI and native display",
            "comparison": "Previous full-pixel crop, per-pixel contrast and coordinate-based diffusion versus production loops; shared decoder and Lanczos3",
            "samples_per_variant": input.samples,
            "cases": cases,
        }),
    )
}

/// Keep the BiliComics reference independent from the production grayscale,
/// specialized Triangle sampler, contrast lookup and ordered-dither tables.
/// The generic DynamicImage pixel view reduces channels to RGBA8 before luma
/// and alpha compositing, matching the pinned BiliComics reader source.
fn reference_bili_raster(bytes: &[u8], options: &ImageOptions) -> Result<GrayImage> {
    super::check_options(options)?;
    ensure!(
        !bytes.is_empty() && bytes.len() as u64 <= super::MAX_PAGE_BYTES,
        "The reference encoded page is empty or exceeds the compressed limit"
    );
    ensure!(
        !super::needs_png_streaming(bytes)?,
        "The BiliComics CPU profile covers the bounded full decoder"
    );
    let dimensions = super::reader(bytes)?.into_dimensions()?;
    ensure!(
        dimensions.0 > 0
            && dimensions.1 > 0
            && dimensions.0 <= super::MAX_SOURCE_WIDTH
            && dimensions.1 <= super::MAX_SOURCE_HEIGHT
            && u64::from(dimensions.0) * u64::from(dimensions.1) <= super::MAX_PAGE_PIXELS,
        "The reference source exceeds the bounded decoder geometry"
    );
    let decoded = super::reader(bytes)?.decode()?;
    ensure!(
        decoded.dimensions() == dimensions,
        "The reference decoded geometry changed"
    );
    let mut gray = GrayImage::new(dimensions.0, dimensions.1);
    for (x, y, pixel) in decoded.pixels() {
        let alpha = u32::from(pixel[3]);
        let luma =
            (54 * u32::from(pixel[0]) + 183 * u32::from(pixel[1]) + 19 * u32::from(pixel[2]) + 128)
                / 256;
        let composite = ((luma * alpha + 255 * (255 - alpha) + 127) / 255) as u8;
        gray.put_pixel(x, y, Luma([composite]));
    }
    drop(decoded);
    if options.crop_borders {
        gray = tests::reference_crop_white_borders(gray);
    }
    let scale = (f64::from(options.width) / f64::from(gray.width()))
        .min(f64::from(options.height) / f64::from(gray.height()));
    let width = (f64::from(gray.width()) * scale)
        .round()
        .max(1.0)
        .min(f64::from(options.width)) as u32;
    let height = (f64::from(gray.height()) * scale)
        .round()
        .max(1.0)
        .min(f64::from(options.height)) as u32;
    let mut output = imageops::resize(&gray, width, height, FilterType::Triangle);
    tests::reference_apply_contrast(&mut output, options.contrast);
    const BAYER: [[i16; 4]; 4] = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];
    for (x, y, pixel) in output.enumerate_pixels_mut() {
        let bias = if options.dither {
            BAYER[(y % 4) as usize][(x % 4) as usize] - 8
        } else {
            0
        };
        let adjusted = (i16::from(pixel[0]) + bias).clamp(0, 255) as u16;
        pixel[0] = (((adjusted + 8) / 17).min(15) * 17) as u8;
    }
    Ok(output)
}

fn timed_bili_raster(bytes: &[u8], options: &ImageOptions) -> Result<(GrayImage, Value)> {
    let started = Instant::now();
    // Measure the actual production API rather than a benchmark copy of it.
    let image = super::decode_reader_raster(std::hint::black_box(bytes), options)?;
    let total_ns = elapsed_ns(started);
    Ok((image, json!({"total_ns": total_ns})))
}

#[test]
#[ignore = "Run only on the remote worker with a release build and real fixture manifest"]
fn real_reader_bili_raster_profile() -> Result<()> {
    let input_path = PathBuf::from(
        std::env::var_os("AIDOKU_READER_PROFILE_INPUT")
            .context("Set AIDOKU_READER_PROFILE_INPUT to the remote fixture manifest")?,
    );
    let input_bytes = fs::read(&input_path)?;
    let input = load()?;
    ensure!(
        fs::read(&input_path)? == input_bytes,
        "The Reader profile manifest changed while loading"
    );
    let mut cases = Vec::new();
    for case in &input.cases {
        // Source I/O, warm-up, reference work and all pixel comparisons stay
        // outside the individual CPU preparation timing windows.
        let bytes = fs::read(&case.path)?;
        let options = case.options();
        let (previous_reference, _) = raster(&bytes, &options, true)?;
        let bili_reference = reference_bili_raster(&bytes, &options)?;
        let (warm_bili, _) = timed_bili_raster(&bytes, &options)?;
        ensure!(
            warm_bili.dimensions() == bili_reference.dimensions()
                && warm_bili.as_raw() == bili_reference.as_raw(),
            "The warmed BiliComics Raster differs from its independent reference for {}",
            case.name
        );
        let mut previous = Vec::new();
        let mut production = Vec::new();
        let mut order = Vec::new();
        for sample in 0..input.samples {
            let pair = if sample % 2 == 0 {
                [true, false]
            } else {
                [false, true]
            };
            order.push(if pair[0] { "AB" } else { "BA" });
            for legacy in pair {
                let (image, timing) = if legacy {
                    raster(std::hint::black_box(&bytes), &options, true)?
                } else {
                    timed_bili_raster(&bytes, &options)?
                };
                let reference = if legacy {
                    &previous_reference
                } else {
                    &bili_reference
                };
                ensure!(
                    image.dimensions() == reference.dimensions()
                        && image.as_raw() == reference.as_raw(),
                    "Sample {sample} {} differs from its reference for {}",
                    if legacy {
                        "previous Raster"
                    } else {
                        "BiliComics Raster"
                    },
                    case.name
                );
                if legacy {
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
            "previous_pixel_sha256": digest(previous_reference.as_raw()),
            "bili_reference_pixel_sha256": digest(bili_reference.as_raw()),
            "production_pixel_sha256": digest(warm_bili.as_raw()),
            "previous_output_dimensions": previous_reference.dimensions(),
            "production_output_dimensions": warm_bili.dimensions(),
            "production_matches_independent_bili_reference": true,
            "previous_matches_production": previous_reference == warm_bili,
            "validated_samples_per_variant": input.samples,
            "order": order,
            "previous": previous,
            "production": production,
        }));
    }
    ensure!(
        fs::read(&input_path)? == input_bytes,
        "The Reader profile manifest changed during execution"
    );
    save(
        &input,
        "bili-raster",
        &json!({
            "schema_version": 1,
            "input_manifest": input_path,
            "input_manifest_sha256": digest(&input_bytes),
            "scope": "Release bounded full-source CPU preparation on the remote host; source bytes are already in memory; excludes source I/O, output PNG encoding, persistence, pixel verification, UI and native display",
            "comparison": "A: full decode, original Aidoku luma, full-pixel crop, Lanczos3 and Floyd-Steinberg; B: production decode_reader_raster with BiliComics RGBA8 luma, Triangle and crop-local Bayer4x4. These algorithms may produce different pixels",
            "production_reference": "Independent bounded DynamicImage RGBA8 view, (54R+183G+19B+128)/256, white alpha composition, full-pixel crop, generic image Triangle, per-pixel contrast and Bayer4x4 formula",
            "timing_boundary": "Each sample includes bounded decoder admission and complete CPU preparation; A also reports its internal stages; B total measures the actual production API without benchmark-only instrumentation",
            "order_labels": {"A": "previous", "B": "production"},
            "samples_per_variant": input.samples,
            "cases": cases,
        }),
    )
}
