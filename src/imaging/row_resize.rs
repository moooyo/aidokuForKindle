//! A bounded intermediate buffer for the existing grayscale Lanczos3 resize.
//!
//! Sampling coordinates, normalization, accumulation order, and final rounding
//! match `image` 0.25.10. Each output row is sampled vertically before it is
//! sampled horizontally, without retaining an entire `Rgba32FImage`.

use std::ops::Range;

use anyhow::Result;
use image::{GenericImageView, GrayImage};

use super::check_decode_cancelled;

struct HorizontalSample {
    first: usize,
    weights: Range<usize>,
}

/// Resize inside the caller's existing decode permit and cancellation scope.
pub(super) fn resize_gray(source: &GrayImage, width: u32, height: u32) -> Result<GrayImage> {
    check_decode_cancelled()?;
    let (source_width, source_height) = source.dimensions();
    if source_width == 0 || source_height == 0 || width == 0 || height == 0 {
        return Ok(source.buffer_with_dimensions(width, height));
    }
    if source.dimensions() == (width, height) {
        let mut output = source.buffer_with_dimensions(width, height);
        let length = output.as_raw().len();
        output.as_mut().copy_from_slice(&source.as_raw()[..length]);
        return Ok(output);
    }

    // The total horizontal support is proportional to the source and output
    // widths. Its weights are reused by every output row.
    let mut horizontal = Vec::new();
    let mut horizontal_weights = Vec::new();
    if height > 1 {
        horizontal.reserve_exact(width as usize);
        for x in 0..width {
            check_decode_cancelled()?;
            let (first, weights) = append_weights(source_width, width, x, &mut horizontal_weights)?;
            horizontal.push(HorizontalSample { first, weights });
        }
    }

    let mut output = source.buffer_with_dimensions(width, height);
    let source_width = source_width as usize;
    let mut row = vec![0.0_f32; source_width];
    let mut vertical_weights = Vec::new();
    for (y, output_row) in output.as_mut().chunks_exact_mut(width as usize).enumerate() {
        check_decode_cancelled()?;
        vertical_weights.clear();
        let (first, weights) =
            append_weights(source_height, height, y as u32, &mut vertical_weights)?;
        row.fill(0.0);

        // Iterate complete source rows for locality. For each pixel, the
        // additions still occur in the same source-y order as image::resize.
        for (index, weight) in vertical_weights[weights].iter().enumerate() {
            check_decode_cancelled()?;
            let start = (first + index) * source_width;
            let source_row = &source.as_raw()[start..start + source_width];
            for (sample, value) in row.iter_mut().zip(source_row) {
                *sample += f32::from(*value) * weight;
            }
        }

        if height == 1 {
            // A one-row result never reuses horizontal weights. Keeping only
            // its current footprint also bounds memory for extremely wide pages.
            for (x, value) in output_row.iter_mut().enumerate() {
                vertical_weights.clear();
                let (first, weights) =
                    append_weights(source_width as u32, width, x as u32, &mut vertical_weights)?;
                *value = horizontal_pixel(&row, first, &vertical_weights[weights]);
            }
        } else {
            for (x, (sample, value)) in horizontal.iter().zip(output_row).enumerate() {
                if x % 256 == 0 {
                    check_decode_cancelled()?;
                }
                *value = horizontal_pixel(
                    &row,
                    sample.first,
                    &horizontal_weights[sample.weights.clone()],
                );
            }
        }
    }
    check_decode_cancelled()?;
    Ok(output)
}

fn horizontal_pixel(row: &[f32], first: usize, weights: &[f32]) -> u8 {
    let values = &row[first..first + weights.len()];
    let mut sum = 0.0_f32;
    for (value, weight) in values.iter().zip(weights) {
        sum += value * weight;
    }
    sum.clamp(0.0, 255.0).round() as u8
}

/// Append one normalized filter footprint with image::resize's f32 arithmetic.
fn append_weights(
    source_length: u32,
    output_length: u32,
    output_position: u32,
    weights: &mut Vec<f32>,
) -> Result<(usize, Range<usize>)> {
    let ratio = source_length as f32 / output_length as f32;
    let scale = if ratio < 1.0 { 1.0 } else { ratio };
    let support = 3.0 * scale;
    let center = (output_position as f32 + 0.5) * ratio;
    let first = ((center - support).floor() as i64).clamp(0, i64::from(source_length) - 1);
    let end = ((center + support).ceil() as i64).clamp(first + 1, i64::from(source_length));
    let center = center - 0.5;
    let start = weights.len();
    let mut sum = 0.0_f32;
    for position in first..end {
        if (position - first) % 1024 == 0 {
            check_decode_cancelled()?;
        }
        let weight = lanczos3((position as f32 - center) / scale);
        weights.push(weight);
        sum += weight;
    }
    let end = weights.len();
    for weight in &mut weights[start..end] {
        *weight /= sum;
    }
    Ok((first as usize, start..end))
}

fn lanczos3(value: f32) -> f32 {
    if value.abs() < 3.0 {
        sinc(value) * sinc(value / 3.0)
    } else {
        0.0
    }
}

fn sinc(value: f32) -> f32 {
    let angle = value * std::f32::consts::PI;
    if value == 0.0 {
        1.0
    } else {
        angle.sin() / angle
    }
}

#[cfg(test)]
mod tests {
    use image::{GrayImage, Luma, imageops};

    use super::resize_gray;

    fn compare(source: &GrayImage, width: u32, height: u32) {
        let actual = resize_gray(source, width, height).unwrap();
        let expected = imageops::resize(source, width, height, imageops::FilterType::Lanczos3);
        assert_eq!(actual.dimensions(), expected.dimensions());
        assert_eq!(actual.as_raw().len(), expected.as_raw().len());
        if let Some(position) = actual
            .as_raw()
            .iter()
            .zip(expected.as_raw())
            .position(|(actual, expected)| actual != expected)
        {
            panic!(
                "Lanczos3 mismatch for {:?} -> ({width}, {height}) at ({}, {}): {} != {}",
                source.dimensions(),
                position % width as usize,
                position / width as usize,
                actual.as_raw()[position],
                expected.as_raw()[position]
            );
        }
    }

    #[test]
    fn preserves_empty_images_and_identity() {
        for (source_width, source_height) in [(0, 0), (0, 7), (7, 0), (1, 1), (17, 29)] {
            let source = GrayImage::from_fn(source_width, source_height, |x, y| {
                Luma([(x.wrapping_mul(13).wrapping_add(y.wrapping_mul(17))) as u8])
            });
            compare(&source, source_width, source_height);
            for (width, height) in [(0, 0), (0, 7), (7, 0), (1, 1), (13, 19)] {
                compare(&source, width, height);
            }
        }
        let source = GrayImage::from_raw(1, 1, vec![7, 91, 255]).unwrap();
        compare(&source, 1, 1);
    }

    #[test]
    fn matches_gradients_edges_and_extreme_aspect_ratios() {
        for (source_width, source_height, width, height) in [
            (1, 257, 89, 1),
            (257, 1, 1, 89),
            (1, 1, 257, 129),
            (2, 2, 1, 1),
            (3, 5, 151, 97),
            (151, 97, 3, 5),
            (257, 193, 113, 79),
            (257, 193, 513, 387),
            (32_768, 2, 1, 113),
            (2, 32_768, 113, 1),
        ] {
            for pattern in 0..4 {
                let source = GrayImage::from_fn(source_width, source_height, |x, y| {
                    Luma([match pattern {
                        0 => x.wrapping_add(y) as u8,
                        1 => u8::from((x + y) % 2 == 0) * 255,
                        2 => u8::from(x < source_width / 2 && y < source_height / 2) * 255,
                        _ => 255,
                    }])
                });
                compare(&source, width, height);
            }
        }
    }

    #[test]
    fn matches_deterministic_random_images_and_dimensions() {
        let mut state = 0xa92e_3d17_54bc_806f_u64;
        let mut next = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state as u32
        };
        for _ in 0..768 {
            let source_width = next() % 97 + 1;
            let source_height = next() % 97 + 1;
            let width = next() % 131 + 1;
            let height = next() % 131 + 1;
            let source =
                GrayImage::from_fn(source_width, source_height, |_, _| Luma([next() as u8]));
            compare(&source, width, height);
        }
    }

    #[test]
    fn matches_every_adjacent_eight_bit_value_pair() {
        // Every possible adjacent pair appears once in each orientation.
        let horizontal = GrayImage::from_fn(512, 256, |x, y| {
            Luma([if x % 2 == 0 { (x / 2) as u8 } else { y as u8 }])
        });
        let vertical = imageops::rotate90(&horizontal);
        for source in [&horizontal, &vertical] {
            for (width, height) in [(1, 1), (257, 129), (1023, 513), (512, 128), (256, 256)] {
                compare(source, width, height);
            }
        }
    }

    #[test]
    fn observes_the_callers_decode_cancellation_scope() {
        let cancellation = super::super::DecodeCancellation::new();
        let error = super::super::with_background_decode(Some(&cancellation), || {
            cancellation.cancel();
            resize_gray(&GrayImage::new(1, 1), 2, 2)
        })
        .unwrap_err();
        assert!(super::super::is_decode_cancelled(&error));
    }
}
