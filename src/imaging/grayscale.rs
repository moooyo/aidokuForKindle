use image::{DynamicImage, GenericImageView, GrayImage, Pixel};

/// Preserve the existing RGBA8-to-luma conversion while avoiding dynamic pixel
/// dispatch and unnecessary work for the decoder's native eight-bit channels.
pub(super) fn into_grayscale(image: DynamicImage) -> GrayImage {
    let (width, height) = image.dimensions();
    let pixels = match image {
        DynamicImage::ImageLuma8(image) => {
            let mut pixels = image.into_raw();
            // ImageBuffer permits trailing storage beyond its declared dimensions.
            pixels.truncate(width as usize * height as usize);
            pixels
        }
        DynamicImage::ImageLumaA8(image) => image
            .pixels()
            .map(|pixel| composite_on_white(pixel[0], pixel[1]))
            .collect(),
        DynamicImage::ImageRgb8(image) => image.pixels().map(|pixel| pixel.to_luma()[0]).collect(),
        DynamicImage::ImageRgba8(image) => image
            .pixels()
            .map(|pixel| composite_on_white(pixel.to_luma()[0], pixel[3]))
            .collect(),
        // For high-depth and floating-point channels, the original path first
        // converts each channel to RGBA8. Converting luma before this reduction
        // can change rounding, so retain that order for every other format.
        image => {
            let mut pixels = Vec::with_capacity(width as usize * height as usize);
            pixels.extend(
                image
                    .pixels()
                    .map(|(_, _, pixel)| composite_on_white(pixel.to_luma()[0], pixel[3])),
            );
            pixels
        }
    };
    GrayImage::from_raw(width, height, pixels)
        .expect("The grayscale conversion preserves the image dimensions")
}

fn composite_on_white(value: u8, alpha: u8) -> u8 {
    let (value, alpha) = (u32::from(value), u32::from(alpha));
    ((value * alpha + 255 * (255 - alpha) + 127) / 255) as u8
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{GrayAlphaImage, ImageBuffer, Luma, LumaA, Rgb, RgbImage, Rgba, RgbaImage};

    fn reference(image: &DynamicImage) -> GrayImage {
        let (width, height) = image.dimensions();
        let mut output = GrayImage::new(width, height);
        for (x, y, pixel) in image.pixels() {
            let alpha = u32::from(pixel[3]);
            let value = u32::from(pixel.to_luma()[0]);
            let paper_value = (value * alpha + 255 * (255 - alpha) + 127) / 255;
            output.put_pixel(x, y, Luma([paper_value as u8]));
        }
        output
    }

    fn matches_reference(image: DynamicImage) {
        let expected = reference(&image);
        let actual = into_grayscale(image);
        assert_eq!(actual.dimensions(), expected.dimensions());
        assert_eq!(actual.as_raw(), expected.as_raw());
    }

    #[test]
    fn luma8_reuses_the_decoded_allocation() {
        let image = GrayImage::from_fn(256, 1, |x, _| Luma([x as u8]));
        let allocation = image.as_raw().as_ptr();
        let output = into_grayscale(DynamicImage::ImageLuma8(image));
        assert_eq!(output.as_raw().as_ptr(), allocation);
        assert_eq!(output.as_raw(), &(0..=255).collect::<Vec<u8>>());
    }

    #[test]
    fn luma8_ignores_storage_outside_the_image_dimensions() {
        let image = GrayImage::from_raw(2, 1, vec![246, 247, 0, 255]).unwrap();
        matches_reference(DynamicImage::ImageLuma8(image));
    }

    #[test]
    fn all_eight_bit_gray_alpha_pairs_match_the_original_order() {
        let image = GrayAlphaImage::from_fn(256, 256, |x, y| LumaA([x as u8, y as u8]));
        matches_reference(DynamicImage::ImageLumaA8(image));
    }

    #[test]
    fn rgb8_preserves_luma_rounding_and_the_crop_threshold() {
        let values = [0, 1, 2, 63, 127, 128, 129, 245, 246, 247, 248, 254, 255];
        let mut samples = Vec::new();
        for red in values {
            for green in values {
                for blue in values {
                    samples.extend_from_slice(&[red, green, blue]);
                }
            }
        }
        let image = RgbImage::from_raw(samples.len() as u32 / 3, 1, samples).unwrap();
        matches_reference(DynamicImage::ImageRgb8(image));
        let image = RgbImage::from_raw(
            6,
            1,
            vec![
                255, 0, 0, 0, 255, 0, 0, 0, 255, 246, 246, 246, 247, 247, 247, 248, 248, 248,
            ],
        )
        .unwrap();
        assert_eq!(
            into_grayscale(DynamicImage::ImageRgb8(image)).into_raw(),
            vec![54, 182, 18, 246, 247, 248]
        );
    }

    #[test]
    fn rgba8_preserves_transparent_and_nearly_opaque_edges() {
        let values = [0, 1, 17, 127, 128, 246, 247, 248, 254, 255];
        let mut samples = Vec::new();
        for red in values {
            for green in values {
                for blue in values {
                    for alpha in values {
                        samples.extend_from_slice(&[red, green, blue, alpha]);
                    }
                }
            }
        }
        let image = RgbaImage::from_raw(samples.len() as u32 / 4, 1, samples).unwrap();
        matches_reference(DynamicImage::ImageRgba8(image));
    }

    #[test]
    fn high_depth_formats_retain_rgba8_reduction_before_luma() {
        let gray = ImageBuffer::from_fn(257, 1, |x, _| Luma([(x * 255) as u16]));
        matches_reference(DynamicImage::ImageLuma16(gray));
        let gray_alpha =
            ImageBuffer::from_fn(257, 257, |x, y| LumaA([(x * 255) as u16, (y * 255) as u16]));
        matches_reference(DynamicImage::ImageLumaA16(gray_alpha));
        let rgb = ImageBuffer::from_fn(257, 17, |x, y| {
            Rgb([
                (x * 255) as u16,
                (y * 3_855) as u16,
                (x * 113 + y * 127) as u16,
            ])
        });
        matches_reference(DynamicImage::ImageRgb16(rgb));
        let rgba = ImageBuffer::from_fn(257, 17, |x, y| {
            Rgba([
                (x * 255) as u16,
                (y * 3_855) as u16,
                (x * 113 + y * 127) as u16,
                (x * 127 + y * 257) as u16,
            ])
        });
        matches_reference(DynamicImage::ImageRgba16(rgba));
    }

    #[test]
    fn floating_point_formats_retain_channel_clamping_and_reduction() {
        let values = [-1.0, 0.0, 0.003, 0.5, 0.969, 1.0, 2.0, f32::NAN];
        let rgb = ImageBuffer::from_fn(64, 8, |x, y| {
            Rgb([
                values[x as usize / 8],
                values[x as usize % 8],
                values[y as usize],
            ])
        });
        matches_reference(DynamicImage::ImageRgb32F(rgb));
        let rgba = ImageBuffer::from_fn(64, 64, |x, y| {
            Rgba([
                values[x as usize / 8],
                values[x as usize % 8],
                values[y as usize / 8],
                values[y as usize % 8],
            ])
        });
        matches_reference(DynamicImage::ImageRgba32F(rgba));
    }

    #[test]
    fn empty_images_keep_their_dimensions() {
        for image in [
            DynamicImage::ImageLuma8(GrayImage::new(0, 3)),
            DynamicImage::ImageLumaA8(GrayAlphaImage::new(3, 0)),
            DynamicImage::ImageRgb8(RgbImage::new(0, 0)),
            DynamicImage::ImageRgba8(RgbaImage::new(0, 3)),
        ] {
            let dimensions = image.dimensions();
            let output = into_grayscale(image);
            assert_eq!(output.dimensions(), dimensions);
            assert!(output.as_raw().is_empty());
        }
    }
}
