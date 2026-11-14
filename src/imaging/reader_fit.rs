//! Bounded, centered geometry for the four approved reader fitting modes.

use super::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReaderFit {
    Original,
    Page,
    Height,
    Width,
}

impl ReaderFit {
    pub fn from_key(key: &str) -> Self {
        match key {
            "original" => Self::Original,
            "height" => Self::Height,
            "width" => Self::Width,
            _ => Self::Page,
        }
    }

    pub fn key(self) -> &'static str {
        match self {
            Self::Original => "original",
            Self::Page => "page",
            Self::Height => "height",
            Self::Width => "width",
        }
    }
}

#[derive(Debug)]
struct Geometry {
    left: f64,
    top: f64,
    width: f64,
    height: f64,
    draw_width: u32,
    draw_height: u32,
    x: u32,
    y: u32,
}

fn geometry(width: u32, height: u32, target: (u32, u32), fit: ReaderFit) -> Geometry {
    let (vw, vh) = target;
    let (w, h) = (f64::from(width), f64::from(height));
    let (sx, sy) = (f64::from(vw) / w, f64::from(vh) / h);
    if fit == ReaderFit::Page {
        return Geometry {
            left: 0.0,
            top: 0.0,
            width: w,
            height: h,
            draw_width: vw,
            draw_height: vh,
            x: 0,
            y: 0,
        };
    }
    let scale = match fit {
        ReaderFit::Original => 1.0_f64.min(sx).min(sy),
        ReaderFit::Height => sy,
        ReaderFit::Width => sx,
        ReaderFit::Page => unreachable!(),
    };
    let sw = w.min(f64::from(vw) / scale);
    let sh = h.min(f64::from(vh) / scale);
    let dw = (w * scale).round().clamp(1.0, f64::from(vw)) as u32;
    let dh = (h * scale).round().clamp(1.0, f64::from(vh)) as u32;
    Geometry {
        left: (w - sw) / 2.0,
        top: (h - sh) / 2.0,
        width: sw,
        height: sh,
        draw_width: dw,
        draw_height: dh,
        x: (vw - dw) / 2,
        y: (vh - dh) / 2,
    }
}

pub fn decode(bytes: &[u8], options: &ImageOptions, fit: ReaderFit) -> Result<GrayImage> {
    let _permit = decode_permit()?;
    check_options(options)?;
    check_decode_cancelled()?;
    let (mut image, geometry) = if needs_png_streaming(bytes)? {
        streaming(bytes, options, fit)?
    } else {
        let mut source = decode_grayscale_for_algorithm(bytes, RasterAlgorithm::BiliTriangle)?;
        if options.crop_borders {
            source = crop_white_borders(source);
        }
        let geometry = geometry(
            source.width(),
            source.height(),
            (options.width, options.height),
            fit,
        );
        let image = reader_raster::resize_region_gray(
            &source,
            geometry.draw_width,
            geometry.draw_height,
            geometry.left,
            geometry.top,
            geometry.width,
            geometry.height,
        )?;
        (image, geometry)
    };
    apply_contrast(&mut image, options.contrast);
    quantize_raster(&mut image, options, RasterAlgorithm::BiliTriangle)?;
    let mut canvas = GrayImage::from_pixel(options.width, options.height, Luma([255]));
    imageops::replace(
        &mut canvas,
        &image,
        i64::from(geometry.x),
        i64::from(geometry.y),
    );
    check_decode_cancelled()?;
    Ok(canvas)
}

fn streaming(
    bytes: &[u8],
    options: &ImageOptions,
    fit: ReaderFit,
) -> Result<(GrayImage, Geometry)> {
    let crop = if options.crop_borders {
        scan_png_bounds_with_algorithm(bytes, true, RasterAlgorithm::BiliTriangle)?
    } else {
        let reader = streaming_png_reader(bytes)?;
        CropRect {
            x: 0,
            y: 0,
            width: reader.info().width,
            height: reader.info().height,
        }
    };
    let geometry = geometry(
        crop.width,
        crop.height,
        (options.width, options.height),
        fit,
    );
    let xs: Vec<_> = (0..geometry.draw_width)
        .map(|x| {
            (f64::from(crop.x)
                + geometry.left
                + (f64::from(x) + 0.5) * geometry.width / f64::from(geometry.draw_width))
            .floor()
            .clamp(f64::from(crop.x), f64::from(crop.x + crop.width - 1)) as u32
        })
        .collect();
    let ys: Vec<_> = (0..geometry.draw_height)
        .map(|y| {
            (f64::from(crop.y)
                + geometry.top
                + (f64::from(y) + 0.5) * geometry.height / f64::from(geometry.draw_height))
            .floor()
            .clamp(f64::from(crop.y), f64::from(crop.y + crop.height - 1)) as u32
        })
        .collect();
    let mut output = GrayImage::new(geometry.draw_width, geometry.draw_height);
    let mut reader = streaming_png_reader(bytes)?;
    let color = reader.output_color_type().0;
    let expected = reader.info().width as usize * color.samples();
    let height = reader.info().height;
    let (mut row_number, mut y) = (0, 0usize);
    while let Some(row) = reader.next_row().context("Decode fitted PNG row")? {
        if row_number % 64 == 0 {
            check_decode_cancelled()?;
        }
        ensure!(
            row_number < height && row.data().len() == expected,
            "Invalid fitted PNG row"
        );
        while y < ys.len() && ys[y] == row_number {
            for (x, source_x) in xs.iter().enumerate() {
                output.put_pixel(
                    x as u32,
                    y as u32,
                    Luma([png_gray_sample(
                        row.data(),
                        *source_x as usize,
                        color,
                        RasterAlgorithm::BiliTriangle,
                    )]),
                );
            }
            y += 1;
        }
        row_number += 1;
    }
    ensure!(
        row_number == height && y == ys.len(),
        "Incomplete fitted PNG"
    );
    reader.finish().context("Validate fitted PNG")?;
    Ok((output, geometry))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn small_original_is_centered_without_changing_content_pixels() {
        let source = GrayImage::from_pixel(4, 2, Luma([85]));
        let mut bytes = Cursor::new(Vec::new());
        source
            .write_to(&mut bytes, image::ImageFormat::Png)
            .unwrap();
        let options = ImageOptions {
            width: 10,
            height: 8,
            crop_borders: false,
            dither: false,
            ..ImageOptions::default()
        };
        let actual = decode(bytes.get_ref(), &options, ReaderFit::Original).unwrap();
        assert_eq!(actual.dimensions(), (10, 8));
        for (x, y, pixel) in actual.enumerate_pixels() {
            assert_eq!(
                pixel[0],
                if (3..7).contains(&x) && (3..5).contains(&y) {
                    85
                } else {
                    255
                }
            );
        }
        let stretched = decode(bytes.get_ref(), &options, ReaderFit::Page).unwrap();
        assert!(stretched.pixels().all(|pixel| pixel[0] == 85));
    }
    #[test]
    fn wide_source_uses_the_named_axis_and_centered_overflow() {
        let target = (1860, 2480);
        let original = geometry(3400, 2360, target, ReaderFit::Original);
        assert_eq!(
            (
                original.draw_width,
                original.draw_height,
                original.x,
                original.y
            ),
            (1860, 1291, 0, 594)
        );
        let page = geometry(3400, 2360, target, ReaderFit::Page);
        assert_eq!((page.draw_width, page.draw_height), (1860, 2480));
        assert_eq!((page.width, page.height), (3400.0, 2360.0));
        let height = geometry(3400, 2360, target, ReaderFit::Height);
        assert_eq!((height.draw_width, height.draw_height), (1860, 2480));
        assert!((height.left - 815.0).abs() < 0.01);
        let width = geometry(3400, 2360, target, ReaderFit::Width);
        assert_eq!(
            (width.draw_width, width.draw_height, width.y),
            (1860, 1291, 594)
        );
    }
    #[test]
    fn original_never_enlarges_small_artwork_and_tall_width_is_center_cropped() {
        let small = geometry(100, 50, (618, 824), ReaderFit::Original);
        assert_eq!(
            (small.draw_width, small.draw_height, small.x, small.y),
            (100, 50, 259, 387)
        );
        let tall = geometry(100, 1000, (600, 800), ReaderFit::Width);
        assert_eq!((tall.draw_width, tall.draw_height), (600, 800));
        assert!((tall.top - (1000.0 - 800.0 / 6.0) / 2.0).abs() < 0.0001);
    }
}
