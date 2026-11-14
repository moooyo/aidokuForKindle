//! BiliComics reader raster preparation, adapted to Aidoku's decode scope.
//!
//! Triangle sampling and exact byte conversion come from BiliComics commit
//! `0e2b816b13aef5b0fe5cad87be4e85c0d75e088d`, `src/reader/triangle.rs`.
//! The Bayer tables come from that commit's `src/reader.rs`. The Triangle
//! specialization derives from image 0.25.10 under MIT OR Apache-2.0; both
//! upstream licenses and provenance are in `vendor/image-triangle-reference-0.25.10/`.
//! Keep the vertical-first f32 operation order and crop-local Bayer phase.

use std::mem::size_of;

use anyhow::{Context, Result};
use image::{GenericImageView, GrayImage};

use super::check_decode_cancelled;

const WEIGHT_BLOCK: usize = 64;
const PIXEL_BLOCK: usize = 256;
const COPY_BLOCK_BYTES: usize = 64 * 1024;
const HORIZONTAL_PLAN_BYTES: usize = 256 * 1024;

/// Resize the already selected immutable grayscale crop inside its decode scope.
pub(super) fn resize_gray(source: &GrayImage, width: u32, height: u32) -> Result<GrayImage> {
    resize_with_plan_budget(source, width, height, HORIZONTAL_PLAN_BYTES)
}

fn resize_with_plan_budget(
    source: &GrayImage,
    width: u32,
    height: u32,
    plan_budget: usize,
) -> Result<GrayImage> {
    resize_region_with_plan_budget(source, width, height, plan_budget, None)
}

pub(super) fn resize_region_gray(
    source: &GrayImage,
    width: u32,
    height: u32,
    left: f64,
    top: f64,
    region_width: f64,
    region_height: f64,
) -> Result<GrayImage> {
    resize_region_with_plan_budget(
        source,
        width,
        height,
        HORIZONTAL_PLAN_BYTES,
        Some((left, top, region_width, region_height)),
    )
}

fn resize_region_with_plan_budget(
    source: &GrayImage,
    width: u32,
    height: u32,
    plan_budget: usize,
    region: Option<(f64, f64, f64, f64)>,
) -> Result<GrayImage> {
    check_decode_cancelled()?;
    let (source_width, source_height) = source.dimensions();
    if source_width == 0 || source_height == 0 || width == 0 || height == 0 {
        return Ok(source.buffer_with_dimensions(width, height));
    }
    if source.dimensions() == (width, height)
        && region.is_none_or(|r| r == (0.0, 0.0, f64::from(source_width), f64::from(source_height)))
    {
        let mut output = source.buffer_with_dimensions(width, height);
        let length = width as usize * height as usize;
        let output_pixels: &mut [u8] = output.as_mut();
        for first in (0..length).step_by(COPY_BLOCK_BYTES) {
            check_decode_cancelled()?;
            let end = (first + COPY_BLOCK_BYTES).min(length);
            output_pixels[first..end].copy_from_slice(&source.as_raw()[first..end]);
        }
        check_decode_cancelled()?;
        return Ok(output);
    }

    let (left, top, rw, rh) =
        region.unwrap_or((0.0, 0.0, f64::from(source_width), f64::from(source_height)));
    let vertical = Axis::region(source_height, height, top, rh);
    let horizontal = Axis::region(source_width, width, left, rw);
    let plan = HorizontalPlan::build(&horizontal, width, plan_budget)?;
    let mut row = Vec::new();
    row.try_reserve_exact(source_width as usize)
        .context("Reserve the reader's bounded Triangle row")?;
    row.resize(source_width as usize, 0.0_f32);
    let mut output = source.buffer_with_dimensions(width, height);
    let pixels = source.as_raw();
    let stride = source_width as usize;
    let mut vertical_weights = Vec::new();
    let mut horizontal_weights = Vec::new();

    for (y, output_row) in output.as_mut().chunks_exact_mut(width as usize).enumerate() {
        check_decode_cancelled()?;
        let first = vertical.weights(y as u32, &mut vertical_weights)? as usize;
        let source_start = first * stride;
        // Each component retains BiliComics' increasing-source-y accumulation
        // and its stored f32 intermediate before horizontal sampling.
        for first_x in (0..row.len()).step_by(PIXEL_BLOCK) {
            check_decode_cancelled()?;
            let end_x = (first_x + PIXEL_BLOCK).min(row.len());
            for x in first_x..end_x {
                row[x] = accumulate(&vertical_weights, |offset| {
                    pixels[source_start + offset * stride + x] as f32
                })?;
            }
        }
        if let Some(plan) = &plan {
            for first_x in (0..output_row.len()).step_by(PIXEL_BLOCK) {
                check_decode_cancelled()?;
                let end_x = (first_x + PIXEL_BLOCK).min(output_row.len());
                for x in first_x..end_x {
                    let column = &plan.columns[x];
                    let value =
                        accumulate(plan.weights(column), |offset| row[column.first + offset])?;
                    output_row[x] = nearest_u8(value);
                }
            }
        } else {
            // BiliComics' full-matrix fallback is deliberately not retained:
            // recomputing one column's coefficients bounds pixel scratch space
            // to this single source-width row even for extreme aspect ratios.
            for (x, pixel) in output_row.iter_mut().enumerate() {
                if x % PIXEL_BLOCK == 0 {
                    check_decode_cancelled()?;
                }
                let first = horizontal.weights(x as u32, &mut horizontal_weights)? as usize;
                let value = accumulate(&horizontal_weights, |offset| row[first + offset])?;
                *pixel = nearest_u8(value);
            }
        }
    }
    check_decode_cancelled()?;
    Ok(output)
}

#[inline]
fn accumulate(weights: &[f32], mut sample: impl FnMut(usize) -> f32) -> Result<f32> {
    let mut total = 0.0_f32;
    if weights.len() <= WEIGHT_BLOCK {
        for (offset, weight) in weights.iter().enumerate() {
            total += sample(offset) * weight;
        }
    } else {
        for (block, weights) in weights.chunks(WEIGHT_BLOCK).enumerate() {
            check_decode_cancelled()?;
            for (offset, weight) in weights.iter().enumerate() {
                total += sample(block * WEIGHT_BLOCK + offset) * weight;
            }
        }
    }
    Ok(total)
}

/// Exact BiliComics conversion of clamp, round-away and saturating u8 cast.
#[inline]
fn nearest_u8(value: f32) -> u8 {
    let bits = value.to_bits();
    if bits < 0x3f00_0000 || bits > 0x7f80_0000 {
        return 0;
    }
    if bits >= 0x437f_0000 {
        return 255;
    }
    let shift = 150 - (bits >> 23);
    let significand = (bits & 0x007f_ffff) | 0x0080_0000;
    ((significand + (1_u32 << (shift - 1))) >> shift) as u8
}

struct HorizontalColumn {
    first: usize,
    offset: usize,
    length: usize,
}

struct HorizontalPlan {
    columns: Vec<HorizontalColumn>,
    coefficients: Vec<f32>,
}

fn plan_bytes(columns: usize, coefficients: usize) -> Option<usize> {
    columns
        .checked_mul(size_of::<HorizontalColumn>())?
        .checked_add(coefficients.checked_mul(size_of::<f32>())?)
}

impl HorizontalPlan {
    fn build(axis: &Axis, width: u32, budget: usize) -> Result<Option<Self>> {
        check_decode_cancelled()?;
        let count = width as usize;
        if plan_bytes(count, 0).is_none_or(|bytes| bytes > budget) {
            return Ok(None);
        }
        let mut coefficient_count = 0_usize;
        for x in 0..width {
            if x as usize % WEIGHT_BLOCK == 0 {
                check_decode_cancelled()?;
            }
            let range = axis.range(x);
            let Some(length) = coefficient_count.checked_add((range.end - range.first) as usize)
            else {
                return Ok(None);
            };
            if plan_bytes(count, length).is_none_or(|bytes| bytes > budget) {
                return Ok(None);
            }
            coefficient_count = length;
        }
        let mut plan = Self {
            columns: Vec::new(),
            coefficients: Vec::new(),
        };
        if plan.columns.try_reserve_exact(count).is_err()
            || plan_bytes(plan.columns.capacity(), coefficient_count)
                .is_none_or(|bytes| bytes > budget)
        {
            return Ok(None);
        }
        check_decode_cancelled()?;
        if plan
            .coefficients
            .try_reserve_exact(coefficient_count)
            .is_err()
            || plan_bytes(plan.columns.capacity(), plan.coefficients.capacity())
                .is_none_or(|bytes| bytes > budget)
        {
            return Ok(None);
        }
        for x in 0..width {
            if x as usize % WEIGHT_BLOCK == 0 {
                check_decode_cancelled()?;
            }
            let range = axis.range(x);
            let offset = plan.coefficients.len();
            let Some(end) = offset.checked_add((range.end - range.first) as usize) else {
                return Ok(None);
            };
            if end > coefficient_count || end > plan.coefficients.capacity() {
                return Ok(None);
            }
            axis.append_weights(range, &mut plan.coefficients)?;
            plan.columns.push(HorizontalColumn {
                first: range.first as usize,
                offset,
                length: plan.coefficients.len() - offset,
            });
        }
        check_decode_cancelled()?;
        Ok(Some(plan))
    }

    fn weights(&self, column: &HorizontalColumn) -> &[f32] {
        &self.coefficients[column.offset..column.offset + column.length]
    }
}

#[derive(Clone, Copy)]
struct AxisRange {
    first: u32,
    end: u32,
    center: f32,
}

struct Axis {
    source_length: u32,
    offset: f32,
    ratio: f32,
    sampling_ratio: f32,
    support: f32,
}

impl Axis {
    fn new(source_length: u32, target_length: u32) -> Self {
        Self::region(source_length, target_length, 0.0, f64::from(source_length))
    }
    fn region(source_length: u32, target_length: u32, offset: f64, length: f64) -> Self {
        let ratio = length as f32 / target_length as f32;
        let sampling_ratio = if ratio < 1.0 { 1.0 } else { ratio };
        Self {
            source_length,
            offset: offset as f32,
            ratio,
            sampling_ratio,
            support: 1.0 * sampling_ratio,
        }
    }

    fn range(&self, position: u32) -> AxisRange {
        let center = self.offset + (position as f32 + 0.5) * self.ratio;
        let first = (center - self.support).floor() as i64;
        let first = first.clamp(0, i64::from(self.source_length) - 1) as u32;
        let end = (center + self.support).ceil() as i64;
        let end = end.clamp(i64::from(first) + 1, i64::from(self.source_length)) as u32;
        AxisRange {
            first,
            end,
            center: center - 0.5,
        }
    }

    fn weights(&self, position: u32, weights: &mut Vec<f32>) -> Result<u32> {
        let range = self.range(position);
        weights.clear();
        self.append_weights(range, weights)?;
        Ok(range.first)
    }

    fn append_weights(&self, range: AxisRange, weights: &mut Vec<f32>) -> Result<()> {
        let start = weights.len();
        let mut sum = 0.0_f32;
        for block in (range.first..range.end).step_by(WEIGHT_BLOCK) {
            check_decode_cancelled()?;
            for position in block..block.saturating_add(WEIGHT_BLOCK as u32).min(range.end) {
                let distance = (position as f32 - range.center) / self.sampling_ratio;
                let value = if distance.abs() < 1.0 {
                    1.0 - distance.abs()
                } else {
                    0.0
                };
                weights.push(value);
                sum += value;
            }
        }
        for weights in weights[start..].chunks_mut(WEIGHT_BLOCK) {
            check_decode_cancelled()?;
            for weight in weights {
                *weight /= sum;
            }
        }
        Ok(())
    }
}

const fn make_tone_tables() -> [[u8; 256]; 17] {
    let bayer: [[i16; 4]; 4] = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];
    let mut tables = [[0_u8; 256]; 17];
    let mut phase = 0;
    while phase < 17 {
        let bias = if phase == 16 {
            0
        } else {
            bayer[phase / 4][phase % 4] - 8
        };
        let mut value = 0;
        while value < 256 {
            let adjusted = value as i16 + bias;
            let adjusted = if adjusted < 0 {
                0
            } else if adjusted > 255 {
                255
            } else {
                adjusted
            } as u16;
            let level = (adjusted + 8) / 17;
            tables[phase][value] = ((if level > 15 { 15 } else { level }) * 17) as u8;
            value += 1;
        }
        phase += 1;
    }
    tables
}

const TONE_TABLES: [[u8; 256]; 17] = make_tone_tables();

/// Apply BiliComics' crop-local ordered dither to the single-channel raster.
pub(super) fn quantize_gray(image: &mut GrayImage, dither: bool) -> Result<()> {
    check_decode_cancelled()?;
    let width = image.width() as usize;
    if width == 0 {
        return Ok(());
    }
    let length = width * image.height() as usize;
    for (y, row) in image.as_mut()[..length].chunks_exact_mut(width).enumerate() {
        for first in (0..width).step_by(PIXEL_BLOCK) {
            check_decode_cancelled()?;
            let end = (first + PIXEL_BLOCK).min(width);
            for (offset, value) in row[first..end].iter_mut().enumerate() {
                let x = first + offset;
                let phase = if dither { (y % 4) * 4 + x % 4 } else { 16 };
                *value = TONE_TABLES[phase][usize::from(*value)];
            }
        }
    }
    check_decode_cancelled()
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{
        Luma,
        imageops::{self, FilterType},
    };

    fn compare(source: &GrayImage, width: u32, height: u32, budget: usize) {
        let expected = imageops::resize(source, width, height, FilterType::Triangle);
        let actual = resize_with_plan_budget(source, width, height, budget).unwrap();
        assert_eq!(actual.dimensions(), expected.dimensions());
        assert_eq!(
            actual.as_raw(),
            expected.as_raw(),
            "Triangle mismatch for {:?} -> {width}x{height}, plan {budget}",
            source.dimensions()
        );
    }

    #[test]
    fn triangle_matches_bili_reference_for_edges_empty_identity_and_fallback() {
        for (source_width, source_height, width, height) in [
            (0, 0, 3, 5),
            (0, 5, 3, 5),
            (5, 0, 3, 5),
            (3, 5, 0, 7),
            (3, 5, 7, 0),
            (1, 1, 1, 1),
            (17, 29, 17, 29),
            (1, 257, 89, 1),
            (257, 1, 1, 89),
            (3, 5, 151, 97),
            (151, 97, 3, 5),
            (257, 193, 113, 79),
            (257, 193, 513, 387),
            (1024, 3, 17, 23),
            (3, 1024, 23, 17),
        ] {
            for pattern in 0..4 {
                let source = GrayImage::from_fn(source_width, source_height, |x, y| {
                    Luma([match pattern {
                        0 => x.wrapping_mul(13).wrapping_add(y.wrapping_mul(17)) as u8,
                        1 => u8::from((x + y) % 2 == 0) * 255,
                        2 => u8::from(x < source_width / 2 && y < source_height / 2) * 255,
                        _ => 255,
                    }])
                });
                for budget in [0, HORIZONTAL_PLAN_BYTES] {
                    compare(&source, width, height, budget);
                }
            }
        }
        compare(
            &GrayImage::from_raw(1, 1, vec![7, 91, 255]).unwrap(),
            1,
            1,
            HORIZONTAL_PLAN_BYTES,
        );
    }

    #[test]
    fn triangle_matches_bili_reference_for_deterministic_random_images() {
        let mut state = 0xa92e_3d17_54bc_806f_u64;
        let mut next = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state as u32
        };
        for sample in 0..384 {
            let source_width = next() % 97 + 1;
            let source_height = next() % 97 + 1;
            let width = next() % 131 + 1;
            let height = next() % 131 + 1;
            let source =
                GrayImage::from_fn(source_width, source_height, |_, _| Luma([next() as u8]));
            compare(
                &source,
                width,
                height,
                if sample % 2 == 0 {
                    0
                } else {
                    HORIZONTAL_PLAN_BYTES
                },
            );
        }
    }

    #[test]
    fn horizontal_plan_never_exceeds_its_element_capacity_budget() {
        let plan = HorizontalPlan::build(&Axis::new(1700, 1860), 1860, HORIZONTAL_PLAN_BYTES)
            .unwrap()
            .unwrap();
        assert!(
            plan_bytes(plan.columns.capacity(), plan.coefficients.capacity()).unwrap()
                <= HORIZONTAL_PLAN_BYTES
        );
        assert!(
            HorizontalPlan::build(&Axis::new(32768, 8192), 8192, HORIZONTAL_PLAN_BYTES)
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn bayer_quantization_matches_bili_reference_for_every_phase_and_level() {
        const BAYER: [[i16; 4]; 4] = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];
        let mut pixels = (0..4)
            .flat_map(|_| (0..1024).map(|x| (x / 4) as u8))
            .collect::<Vec<_>>();
        pixels.extend_from_slice(&[13, 27, 247]);
        let source = GrayImage::from_raw(1024, 4, pixels).unwrap();
        for dither in [false, true] {
            let mut expected = source.clone();
            for (x, y, pixel) in expected.enumerate_pixels_mut() {
                let bias = if dither {
                    BAYER[(y % 4) as usize][(x % 4) as usize] - 8
                } else {
                    0
                };
                let adjusted = (i16::from(pixel[0]) + bias).clamp(0, 255) as u16;
                pixel[0] = (((adjusted + 8) / 17).min(15) * 17) as u8;
            }
            let mut actual = source.clone();
            quantize_gray(&mut actual, dither).unwrap();
            assert_eq!(actual.as_raw(), expected.as_raw());
        }
        quantize_gray(&mut GrayImage::new(0, 7), true).unwrap();
    }

    #[test]
    fn exact_conversion_matches_reference_at_byte_boundaries_and_special_values() {
        for value in [
            f32::NEG_INFINITY,
            -255.0,
            -0.0,
            0.0,
            f32::NAN,
            f32::INFINITY,
        ] {
            assert_eq!(nearest_u8(value), value.clamp(0.0, 255.0).round() as u8);
        }
        for byte in 0..256 {
            let value = byte as f32 + 0.5;
            for bits in [value.to_bits() - 1, value.to_bits(), value.to_bits() + 1] {
                let value = f32::from_bits(bits);
                assert_eq!(nearest_u8(value), value.clamp(0.0, 255.0).round() as u8);
            }
        }
    }

    #[test]
    fn raster_preparation_observes_the_decode_cancellation_scope() {
        let cancellation = crate::imaging::DecodeCancellation::new();
        cancellation.cancel();
        let error = crate::imaging::with_background_decode(Some(&cancellation), || {
            resize_gray(&GrayImage::new(3, 5), 7, 11)
        })
        .unwrap_err();
        assert!(crate::imaging::is_decode_cancelled(&error));
        let error = crate::imaging::with_background_decode(Some(&cancellation), || {
            quantize_gray(&mut GrayImage::new(3, 5), true)
        })
        .unwrap_err();
        assert!(crate::imaging::is_decode_cancelled(&error));
    }
}
