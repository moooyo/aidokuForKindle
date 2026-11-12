mod decode_scheduler;
mod grayscale;
mod reader_raster;
mod row_resize;

#[cfg(test)]
pub(crate) mod fixture_profile;

pub use decode_scheduler::{
    DecodeCancellation, check_decode_cancelled, is_decode_cancelled, with_background_decode,
};
pub(crate) use decode_scheduler::{
    decode_permit, with_decode_cancellation, without_decode_cancellation,
};

use std::{
    borrow::Cow,
    fs,
    io::{BufReader, Cursor, Read},
    path::Path,
    sync::Arc,
};

use anyhow::{Context, Result, bail, ensure};
use image::{
    GrayImage, ImageReader, Limits, Luma,
    imageops::{self, FilterType},
};

/// Compressed and decoded limits keep a malformed page from exhausting Kindle RAM.
pub const MAX_PAGE_BYTES: u64 = 32 * 1024 * 1024;
pub const MAX_PAGE_PIXELS: u64 = 16 * 1024 * 1024;
pub const MAX_DECODE_BYTES: u64 = 96 * 1024 * 1024;
const MAX_SOURCE_WIDTH: u32 = 32_768;
const MAX_SOURCE_HEIGHT: u32 = 131_072;
const MAX_TARGET_SIDE: u32 = 8_192;
const MAX_STREAM_WIDTH: u32 = 16_384;
const MAX_STREAM_HEIGHT: u32 = 1_000_000;
const MAX_STREAM_PIXELS: u64 = 256 * 1024 * 1024;
const PNG_SIGNATURE: &[u8; 8] = b"\x89PNG\r\n\x1a\n";
// Cover, reader, import, and download workers share the scheduler's single
// temporary source-bitmap budget. Compressed inputs remain separately bounded
// by MAX_PAGE_BYTES. Foreground work can pass queued background preparation.

#[cfg(test)]
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct ImageWork {
    bitmap_decodes: u64,
    bounds_passes: u64,
    bounds_rows: u64,
    viewport_passes: u64,
    viewport_rows: u64,
}

#[cfg(test)]
thread_local! {
    static IMAGE_WORK: std::cell::Cell<ImageWork> = std::cell::Cell::new(ImageWork::default());
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImageOptions {
    pub width: u32,
    pub height: u32,
    pub crop_borders: bool,
    pub dither: bool,
    pub contrast: i32,
}

impl Default for ImageOptions {
    fn default() -> Self {
        Self {
            width: 1_236,
            height: 1_648,
            crop_borders: true,
            dither: true,
            contrast: 0,
        }
    }
}

/// A rendered vertical viewport and the complete cropped strip geometry.
/// Geometry is calculated from the same decoded pixels or crop scan as the
/// viewport, rather than performing a separate source decode.
#[derive(Debug)]
pub struct VerticalWindow {
    pub image: GrayImage,
    pub scaled_height: u32,
    pub viewport_height: u32,
}

/// Immutable, unrotated grayscale pixels from a completed bounded decode.
/// Only this module can construct one; cache owners must additionally bind it
/// to the current encoded bytes' checksum and length before retaining it.
#[derive(Debug, Clone)]
pub(crate) struct ReaderSource {
    image: Arc<GrayImage>,
}

impl ReaderSource {
    pub(crate) fn allocation_bytes(&self) -> usize {
        self.image.as_raw().capacity()
    }

    pub(crate) fn dimensions(&self) -> (u32, u32) {
        self.image.dimensions()
    }

    pub(crate) fn allocation_identity(&self) -> usize {
        Arc::as_ptr(&self.image) as usize
    }

    #[cfg(test)]
    pub(crate) fn same_allocation(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.image, &other.image)
    }

    #[cfg(test)]
    pub(crate) fn from_test_image(image: GrayImage) -> Self {
        Self {
            image: Arc::new(image),
        }
    }
}

#[derive(Clone, Copy)]
enum RasterAlgorithm {
    Lanczos3,
    BiliTriangle,
}

fn resize_raster(
    source: &GrayImage,
    width: u32,
    height: u32,
    algorithm: RasterAlgorithm,
) -> Result<GrayImage> {
    match algorithm {
        RasterAlgorithm::Lanczos3 => row_resize::resize_gray(source, width, height),
        RasterAlgorithm::BiliTriangle => reader_raster::resize_gray(source, width, height),
    }
}

fn quantize_raster(image: &mut GrayImage, dither: bool, algorithm: RasterAlgorithm) -> Result<()> {
    match algorithm {
        RasterAlgorithm::Lanczos3 => {
            quantize(image, dither);
            Ok(())
        }
        RasterAlgorithm::BiliTriangle => reader_raster::quantize_gray(image, dither),
    }
}

/// Decode a local page and fit it inside the requested screen rectangle.
pub fn render_page(path: impl AsRef<Path>, options: &ImageOptions) -> Result<GrayImage> {
    let path = path.as_ref();
    let size = fs::metadata(path)
        .with_context(|| format!("Read image metadata: {}", path.display()))?
        .len();
    ensure!(size <= MAX_PAGE_BYTES, "The compressed page exceeds 32 MiB");
    let file = fs::File::open(path).with_context(|| format!("Open image: {}", path.display()))?;
    let mut bytes = Vec::with_capacity(size as usize);
    file.take(MAX_PAGE_BYTES + 1)
        .read_to_end(&mut bytes)
        .with_context(|| format!("Read image: {}", path.display()))?;
    decode_page(&bytes, options)
}

/// Decode with explicit allocation limits, composite transparency onto paper,
/// resize, and produce exactly sixteen printable gray levels.
pub fn decode_page(bytes: &[u8], options: &ImageOptions) -> Result<GrayImage> {
    decode_page_with_algorithm(bytes, options, RasterAlgorithm::Lanczos3)
}

/// Prepare the Reader with BiliComics' single-channel Triangle sampler and
/// crop-local Bayer tone tables. Cover and download derivatives retain their
/// original renderer; callers must namespace this algorithm's cache keys.
pub fn decode_reader_raster(bytes: &[u8], options: &ImageOptions) -> Result<GrayImage> {
    decode_page_with_algorithm(bytes, options, RasterAlgorithm::BiliTriangle)
}

fn decode_page_with_algorithm(
    bytes: &[u8],
    options: &ImageOptions,
    algorithm: RasterAlgorithm,
) -> Result<GrayImage> {
    let _decode_guard = decode_permit()?;
    check_decode_cancelled()?;
    check_options(options)?;
    if needs_png_streaming(bytes)? {
        return Ok(render_streaming_png_with_algorithm(bytes, options, None, algorithm)?.image);
    }
    let mut gray = decode_grayscale_for_algorithm(bytes, algorithm)?;
    if options.crop_borders {
        gray = crop_white_borders(gray);
    }
    let (width, height) =
        fit_dimensions(gray.width(), gray.height(), options.width, options.height);
    let mut output = resize_raster(&gray, width, height, algorithm)?;
    apply_contrast(&mut output, options.contrast);
    quantize_raster(&mut output, options.dither, algorithm)?;
    Ok(output)
}

/// Read a viewport from a vertical comic without allocating the complete scaled
/// strip. Noninterlaced PNGs above MAX_PAGE_PIXELS use a row decoder; other
/// formats retain the bounded full-image decoder.
///
/// `offset` is measured after fitting the original page to `options.width`.
pub fn render_vertical_window(
    bytes: &[u8],
    options: &ImageOptions,
    offset: u32,
) -> Result<GrayImage> {
    Ok(render_vertical_window_with_geometry(bytes, options, offset)?.image)
}

/// Render and measure a vertical window with one bounded source decode. A
/// cropped streaming PNG uses one bounds pass and one viewport pass; both
/// passes validate the complete input, including its final chunks.
pub fn render_vertical_window_with_geometry(
    bytes: &[u8],
    options: &ImageOptions,
    offset: u32,
) -> Result<VerticalWindow> {
    render_vertical_window_with_algorithm(bytes, options, offset, RasterAlgorithm::Lanczos3)
}

pub fn render_vertical_raster_with_geometry(
    bytes: &[u8],
    options: &ImageOptions,
    offset: u32,
) -> Result<VerticalWindow> {
    Ok(render_vertical_raster_with_source(bytes, options, offset, None)?.0)
}

pub(crate) fn render_vertical_raster_with_source(
    bytes: &[u8],
    options: &ImageOptions,
    offset: u32,
    source: Option<ReaderSource>,
) -> Result<(VerticalWindow, Option<ReaderSource>)> {
    let _decode_guard = decode_permit()?;
    check_decode_cancelled()?;
    check_options(options)?;
    if needs_png_streaming(bytes)? {
        return Ok((
            render_streaming_png_with_algorithm(
                bytes,
                options,
                Some(offset),
                RasterAlgorithm::BiliTriangle,
            )?,
            None,
        ));
    }
    let source = if let Some(source) = source {
        source
    } else {
        ReaderSource {
            image: Arc::new(decode_reader_grayscale(bytes)?),
        }
    };
    let crop = if options.crop_borders {
        white_border_crop(&source.image)
    } else {
        CropRect {
            x: 0,
            y: 0,
            width: source.image.width(),
            height: source.image.height(),
        }
    };
    let gray =
        if crop.x == 0 && crop.y == 0 && (crop.width, crop.height) == source.image.dimensions() {
            Cow::Borrowed(source.image.as_ref())
        } else {
            Cow::Owned(
                imageops::crop_imm(&*source.image, crop.x, crop.y, crop.width, crop.height)
                    .to_image(),
            )
        };
    let window = render_vertical_gray(&gray, options, offset, RasterAlgorithm::BiliTriangle)?;
    drop(gray);
    Ok((window, Some(source)))
}

fn render_vertical_window_with_algorithm(
    bytes: &[u8],
    options: &ImageOptions,
    offset: u32,
    algorithm: RasterAlgorithm,
) -> Result<VerticalWindow> {
    let _decode_guard = decode_permit()?;
    check_decode_cancelled()?;
    check_options(options)?;
    if needs_png_streaming(bytes)? {
        return render_streaming_png_with_algorithm(bytes, options, Some(offset), algorithm);
    }
    let mut gray = decode_grayscale_for_algorithm(bytes, algorithm)?;
    if options.crop_borders {
        gray = crop_white_borders(gray);
    }
    render_vertical_gray(&gray, options, offset, algorithm)
}

fn render_vertical_gray(
    gray: &GrayImage,
    options: &ImageOptions,
    offset: u32,
    algorithm: RasterAlgorithm,
) -> Result<VerticalWindow> {
    let scaled_height = scaled_vertical_height(gray.width(), gray.height(), options.width)?;
    let viewport_height = options.height.min(scaled_height);
    let scale = f64::from(options.width) / f64::from(gray.width());
    // Keep the existing floating-point sampling coordinates independent from
    // the exact integer geometry used by the reader's vertical navigation.
    let sampled_height = (f64::from(gray.height()) * scale).ceil().max(1.0) as u32;
    let output_height = options.height.min(sampled_height);
    let offset = offset.min(sampled_height.saturating_sub(output_height));
    let source_top = (f64::from(offset) / scale).floor() as u32;
    let source_bottom = ((f64::from(offset) + f64::from(output_height)) / scale)
        .ceil()
        .min(f64::from(gray.height())) as u32;
    let source_height = source_bottom.saturating_sub(source_top).max(1);
    let strip = imageops::crop_imm(gray, 0, source_top, gray.width(), source_height).to_image();
    let mut output = resize_raster(&strip, options.width, output_height, algorithm)?;
    apply_contrast(&mut output, options.contrast);
    quantize_raster(&mut output, options.dither, algorithm)?;
    Ok(VerticalWindow {
        image: output,
        scaled_height,
        viewport_height,
    })
}

/// Return the complete, cropped strip size after fitting it to the viewport
/// width. The height is not limited to the currently visible window.
pub fn vertical_dimensions(bytes: &[u8], options: &ImageOptions) -> Result<(u32, u32)> {
    let _decode_guard = decode_permit()?;
    check_decode_cancelled()?;
    check_options(options)?;
    let (width, height) = if needs_png_streaming(bytes)? {
        let crop = scan_png_bounds(bytes, options.crop_borders)?;
        (crop.width, crop.height)
    } else {
        let mut gray = decode_grayscale(bytes)?;
        if options.crop_borders {
            gray = crop_white_borders(gray);
        }
        gray.dimensions()
    };
    let scaled_height = scaled_vertical_height(width, height, options.width)?;
    Ok((options.width, scaled_height))
}

fn scaled_vertical_height(width: u32, height: u32, target_width: u32) -> Result<u32> {
    let scaled_height = (u64::from(height) * u64::from(target_width)).div_ceil(u64::from(width));
    ensure!(
        scaled_height <= u64::from(u32::MAX),
        "The scaled vertical page exceeds the page-coordinate limit"
    );
    Ok(scaled_height as u32)
}

/// Validate actual image data before adding an imported or downloaded page.
/// Long noninterlaced PNGs are checked row by row, including their final chunks,
/// without holding the complete source bitmap in memory.
pub fn validate_page(bytes: &[u8]) -> Result<()> {
    let _decode_guard = decode_permit()?;
    check_decode_cancelled()?;
    if needs_png_streaming(bytes)? {
        scan_png_bounds(bytes, false)?;
    } else {
        decode_grayscale(bytes)?;
    }
    Ok(())
}

/// Validate a downloaded file without retaining its compressed representation
/// in RAM. Long PNGs are checked row by row under the shared decoder budget.
pub fn validate_file(path: impl AsRef<Path>) -> Result<()> {
    let _decode_guard = decode_permit()?;
    check_decode_cancelled()?;
    let path = path.as_ref();
    let size = fs::metadata(path)
        .context("Read cached image metadata")?
        .len();
    ensure!(
        size > 0 && size <= MAX_PAGE_BYTES,
        "The compressed page is empty or exceeds 32 MiB"
    );
    let header = ImageReader::open(path)
        .context("Open cached image")?
        .with_guessed_format()
        .context("Recognize cached image format")?;
    let format = header.format();
    let (width, height) = header
        .into_dimensions()
        .context("Read cached image dimensions")?;
    ensure!(width > 0 && height > 0, "The page has no image pixels");
    let large = width > MAX_SOURCE_WIDTH
        || height > MAX_SOURCE_HEIGHT
        || u64::from(width) * u64::from(height) > MAX_PAGE_PIXELS;
    if format == Some(image::ImageFormat::Png) && large {
        ensure!(
            width <= MAX_STREAM_WIDTH
                && height <= MAX_STREAM_HEIGHT
                && u64::from(width) * u64::from(height) <= MAX_STREAM_PIXELS,
            "The streaming PNG dimensions exceed the processing limit"
        );
        let file = fs::File::open(path).context("Open streaming PNG file")?;
        let mut decoder = png::Decoder::new_with_limits(
            BufReader::new(file),
            png::Limits {
                bytes: MAX_DECODE_BYTES as usize,
            },
        );
        decoder.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
        let info = decoder
            .read_header_info()
            .context("Read streaming PNG file header")?;
        ensure!(
            !info.interlaced,
            "Large interlaced PNG pages must be converted to noninterlaced PNG or split first"
        );
        let mut reader = decoder
            .read_info()
            .context("Read streaming PNG file metadata")?;
        ensure!(
            reader.info().animation_control.is_none(),
            "Large animated PNG pages must be converted to a static image first"
        );
        let (color, depth) = reader.output_color_type();
        ensure!(
            depth == png::BitDepth::Eight && color != png::ColorType::Indexed,
            "The PNG row decoder could not produce eight-bit samples"
        );
        let expected = width as usize * color.samples();
        let mut rows = 0u32;
        while let Some(row) = reader
            .next_row()
            .context("Validate streaming PNG file row")?
        {
            if rows % 64 == 0 {
                check_decode_cancelled()?;
            }
            ensure!(
                rows < height && row.data().len() == expected,
                "The PNG page contains an invalid row"
            );
            rows += 1;
        }
        ensure!(rows == height, "The PNG page ended before its final row");
        reader.finish().context("Validate final PNG file chunks")?;
        return Ok(());
    }
    ensure!(
        !large,
        "The source page exceeds the bounded decoder limit; split the page first"
    );
    let mut reader = ImageReader::open(path)
        .context("Open cached page for validation")?
        .with_guessed_format()?;
    let mut limits = Limits::default();
    limits.max_image_width = Some(MAX_SOURCE_WIDTH);
    limits.max_image_height = Some(MAX_SOURCE_HEIGHT);
    limits.max_alloc = Some(MAX_DECODE_BYTES);
    reader.limits(limits);
    reader
        .decode()
        .context("Validate cached page within the decoder memory budget")?;
    Ok(())
}

/// Generate 85-percent viewport steps with a final bottom-aligned viewport.
pub fn vertical_windows(image_height: u32, viewport_height: u32) -> Vec<u32> {
    if image_height == 0 || viewport_height == 0 {
        return Vec::new();
    }
    let last = image_height.saturating_sub(viewport_height);
    let step = (u64::from(viewport_height) * 85 / 100).max(1) as u32;
    let mut result = vec![0];
    let mut offset = 0_u32;
    while let Some(next) = offset.checked_add(step).filter(|next| *next < last) {
        result.push(next);
        offset = next;
    }
    if last > 0 {
        result.push(last);
    }
    result
}

/// Place the first page on the right for RTL and on the left for LTR.
/// Callers pass actual screen dimensions, rather than untrusted image metadata.
pub fn render_spread(
    first: &GrayImage,
    second: &GrayImage,
    width: u32,
    height: u32,
    rtl: bool,
) -> GrayImage {
    let mut output = GrayImage::from_pixel(width, height, Luma([255]));
    if width < 4 || height == 0 {
        return output;
    }
    let gutter = 2;
    let first_slot_width = (width - gutter) / 2;
    let second_slot_width = width - gutter - first_slot_width;
    let (left_page, right_page) = if rtl {
        (second, first)
    } else {
        (first, second)
    };
    place_page(&mut output, left_page, 0, first_slot_width, height);
    place_page(
        &mut output,
        right_page,
        first_slot_width + gutter,
        second_slot_width,
        height,
    );
    quantize(&mut output, false);
    output
}

/// Only preload two following pages; callers can keep the current page outside
/// this bounded read-ahead set.
pub fn preload_indices(current: usize, total: usize) -> Vec<usize> {
    if current >= total {
        return Vec::new();
    }
    (current.saturating_add(1)..total).take(2).collect()
}

fn check_options(options: &ImageOptions) -> Result<()> {
    ensure!(
        options.width > 0 && options.height > 0,
        "The target image dimensions must be positive"
    );
    ensure!(
        options.width <= MAX_TARGET_SIDE && options.height <= MAX_TARGET_SIDE,
        "The target image dimensions exceed the screen limit"
    );
    ensure!(
        u64::from(options.width) * u64::from(options.height) <= MAX_PAGE_PIXELS,
        "The target image exceeds the allocation limit"
    );
    Ok(())
}

fn reader(bytes: &[u8]) -> Result<ImageReader<Cursor<&[u8]>>> {
    let mut reader = ImageReader::new(Cursor::new(bytes))
        .with_guessed_format()
        .context("Recognize page image format")?;
    let mut limits = Limits::default();
    limits.max_image_width = Some(MAX_SOURCE_WIDTH);
    limits.max_image_height = Some(MAX_SOURCE_HEIGHT);
    limits.max_alloc = Some(MAX_DECODE_BYTES);
    reader.limits(limits);
    Ok(reader)
}

#[derive(Debug, Clone, Copy)]
struct CropRect {
    x: u32,
    y: u32,
    width: u32,
    height: u32,
}

fn png_decoder(bytes: &[u8]) -> Result<png::Decoder<Cursor<&[u8]>>> {
    ensure!(
        !bytes.is_empty() && bytes.len() as u64 <= MAX_PAGE_BYTES,
        "The compressed page is empty or exceeds 32 MiB"
    );
    let mut decoder = png::Decoder::new_with_limits(
        Cursor::new(bytes),
        png::Limits {
            bytes: MAX_DECODE_BYTES as usize,
        },
    );
    decoder.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
    Ok(decoder)
}

fn needs_png_streaming(bytes: &[u8]) -> Result<bool> {
    if !bytes.starts_with(PNG_SIGNATURE) {
        return Ok(false);
    }
    let mut decoder = png_decoder(bytes)?;
    let info = decoder.read_header_info().context("Read PNG page header")?;
    Ok(info.width > MAX_SOURCE_WIDTH
        || info.height > MAX_SOURCE_HEIGHT
        || u64::from(info.width) * u64::from(info.height) > MAX_PAGE_PIXELS)
}

fn streaming_png_reader(bytes: &[u8]) -> Result<png::Reader<Cursor<&[u8]>>> {
    let mut decoder = png_decoder(bytes)?;
    let info = decoder
        .read_header_info()
        .context("Read streaming PNG header")?;
    ensure!(
        info.width > 0 && info.height > 0,
        "The PNG page has no image pixels"
    );
    ensure!(
        info.width <= MAX_STREAM_WIDTH && info.height <= MAX_STREAM_HEIGHT,
        "The streaming PNG dimensions exceed 16384 by 1000000; split the page first"
    );
    ensure!(
        u64::from(info.width) * u64::from(info.height) <= MAX_STREAM_PIXELS,
        "The streaming PNG exceeds the 256-megapixel processing limit"
    );
    ensure!(
        !info.interlaced,
        "Large interlaced PNG pages must be converted to noninterlaced PNG or split first"
    );
    let reader = decoder.read_info().context("Read streaming PNG metadata")?;
    ensure!(
        reader.info().animation_control.is_none(),
        "Large animated PNG pages must be converted to a static image first"
    );
    let (color, depth) = reader.output_color_type();
    ensure!(
        depth == png::BitDepth::Eight && color != png::ColorType::Indexed,
        "The PNG row decoder could not produce eight-bit samples"
    );
    Ok(reader)
}

fn scan_png_bounds(bytes: &[u8], crop: bool) -> Result<CropRect> {
    scan_png_bounds_with_algorithm(bytes, crop, RasterAlgorithm::Lanczos3)
}

fn scan_png_bounds_with_algorithm(
    bytes: &[u8],
    crop: bool,
    algorithm: RasterAlgorithm,
) -> Result<CropRect> {
    let mut reader = streaming_png_reader(bytes)?;
    let (width, height) = (reader.info().width, reader.info().height);
    let color = reader.output_color_type().0;
    let expected_row_bytes = width as usize * color.samples();
    let (mut left, mut right, mut top, mut bottom) = (width, 0, height, 0);
    let mut row_number = 0;
    while let Some(row) = reader.next_row().context("Decode streaming PNG row")? {
        if row_number % 64 == 0 {
            check_decode_cancelled()?;
        }
        ensure!(
            row_number < height && row.data().len() == expected_row_bytes,
            "The PNG page contains an invalid row"
        );
        if crop {
            for x in 0..width {
                if png_gray_sample(row.data(), x as usize, color, algorithm) < 247 {
                    left = left.min(x);
                    right = right.max(x);
                    top = top.min(row_number);
                    bottom = bottom.max(row_number);
                }
            }
        }
        row_number += 1;
    }
    ensure!(
        row_number == height,
        "The PNG page ended before its final row"
    );
    reader.finish().context("Validate final PNG chunks")?;
    #[cfg(test)]
    IMAGE_WORK.with(|work| {
        let mut current = work.get();
        current.bounds_passes += 1;
        current.bounds_rows += u64::from(row_number);
        work.set(current);
    });
    if !crop || left > right || top > bottom {
        return Ok(CropRect {
            x: 0,
            y: 0,
            width,
            height,
        });
    }
    Ok(bounded_crop(width, height, left, right, top, bottom))
}

/// A long PNG is sampled directly into a screen-sized buffer. A crop pass is
/// optional and row based as well; neither pass allocates a source-sized image.
fn render_streaming_png_with_algorithm(
    bytes: &[u8],
    options: &ImageOptions,
    vertical_offset: Option<u32>,
    algorithm: RasterAlgorithm,
) -> Result<VerticalWindow> {
    check_decode_cancelled()?;
    let crop = if options.crop_borders {
        scan_png_bounds_with_algorithm(bytes, true, algorithm)?
    } else {
        let reader = streaming_png_reader(bytes)?;
        CropRect {
            x: 0,
            y: 0,
            width: reader.info().width,
            height: reader.info().height,
        }
    };
    check_decode_cancelled()?;
    let (output_width, output_height, source_top, source_height, scaled_height) =
        match vertical_offset {
            Some(offset) => {
                let scaled_height = scaled_vertical_height(crop.width, crop.height, options.width)?;
                let output_height = options.height.min(scaled_height);
                let offset = offset.min(scaled_height.saturating_sub(output_height));
                let scale = f64::from(options.width) / f64::from(crop.width);
                (
                    options.width,
                    output_height,
                    f64::from(crop.y) + f64::from(offset) / scale,
                    f64::from(output_height) / scale,
                    scaled_height,
                )
            }
            None => {
                let (width, height) =
                    fit_dimensions(crop.width, crop.height, options.width, options.height);
                (width, height, f64::from(crop.y), f64::from(crop.height), 0)
            }
        };
    let sample_x = (0..output_width)
        .map(|x| {
            crop.x
                + (((f64::from(x) + 0.5) * f64::from(crop.width) / f64::from(output_width)).floor()
                    as u32)
                    .min(crop.width - 1)
        })
        .collect::<Vec<_>>();
    let sample_y = (0..output_height)
        .map(|y| {
            (source_top + (f64::from(y) + 0.5) * source_height / f64::from(output_height))
                .floor()
                .clamp(f64::from(crop.y), f64::from(crop.y + crop.height - 1)) as u32
        })
        .collect::<Vec<_>>();
    let mut output = GrayImage::new(output_width, output_height);
    let mut reader = streaming_png_reader(bytes)?;
    let (width, height) = (reader.info().width, reader.info().height);
    let color = reader.output_color_type().0;
    let expected_row_bytes = width as usize * color.samples();
    let (mut row_number, mut output_y) = (0_u32, 0_u32);
    while let Some(row) = reader
        .next_row()
        .context("Decode streaming PNG viewport row")?
    {
        if row_number % 64 == 0 {
            check_decode_cancelled()?;
        }
        ensure!(
            row_number < height && row.data().len() == expected_row_bytes,
            "The PNG page contains an invalid row"
        );
        while output_y < output_height && sample_y[output_y as usize] == row_number {
            for (output_x, source_x) in sample_x.iter().copied().enumerate() {
                let gray = png_gray_sample(row.data(), source_x as usize, color, algorithm);
                output.put_pixel(output_x as u32, output_y, Luma([gray]));
            }
            output_y += 1;
        }
        row_number += 1;
    }
    ensure!(
        row_number == height && output_y == output_height,
        "The PNG page ended before the requested viewport was complete"
    );
    reader
        .finish()
        .context("Validate final PNG viewport chunks")?;
    #[cfg(test)]
    IMAGE_WORK.with(|work| {
        let mut current = work.get();
        current.viewport_passes += 1;
        current.viewport_rows += u64::from(row_number);
        work.set(current);
    });
    apply_contrast(&mut output, options.contrast);
    quantize_raster(&mut output, options.dither, algorithm)?;
    Ok(VerticalWindow {
        image: output,
        scaled_height,
        viewport_height: if vertical_offset.is_some() {
            output_height
        } else {
            0
        },
    })
}

fn png_gray_sample(row: &[u8], x: usize, color: png::ColorType, algorithm: RasterAlgorithm) -> u8 {
    let offset = x * color.samples();
    let (gray, alpha) = match color {
        png::ColorType::Grayscale => (u32::from(row[offset]), 255),
        png::ColorType::GrayscaleAlpha => (u32::from(row[offset]), u32::from(row[offset + 1])),
        png::ColorType::Rgb | png::ColorType::Rgba => {
            let gray = match algorithm {
                RasterAlgorithm::Lanczos3 => {
                    (u32::from(row[offset]) * 2_126
                        + u32::from(row[offset + 1]) * 7_152
                        + u32::from(row[offset + 2]) * 722
                        + 5_000)
                        / 10_000
                }
                RasterAlgorithm::BiliTriangle => {
                    (u32::from(row[offset]) * 54
                        + u32::from(row[offset + 1]) * 183
                        + u32::from(row[offset + 2]) * 19
                        + 128)
                        / 256
                }
            };
            let alpha = if color == png::ColorType::Rgba {
                u32::from(row[offset + 3])
            } else {
                255
            };
            (gray, alpha)
        }
        // EXPAND eliminates indexed pixels before rows reach this function.
        png::ColorType::Indexed => unreachable!("Indexed PNG samples were not expanded"),
    };
    ((gray * alpha + 255 * (255 - alpha) + 127) / 255) as u8
}

fn decode_grayscale(bytes: &[u8]) -> Result<GrayImage> {
    Ok(grayscale::into_grayscale(decode_bounded_image(bytes)?))
}

fn decode_reader_grayscale(bytes: &[u8]) -> Result<GrayImage> {
    grayscale::into_reader_grayscale(decode_bounded_image(bytes)?)
}

fn decode_grayscale_for_algorithm(bytes: &[u8], algorithm: RasterAlgorithm) -> Result<GrayImage> {
    match algorithm {
        RasterAlgorithm::Lanczos3 => decode_grayscale(bytes),
        RasterAlgorithm::BiliTriangle => decode_reader_grayscale(bytes),
    }
}

fn decode_bounded_image(bytes: &[u8]) -> Result<image::DynamicImage> {
    check_decode_cancelled()?;
    ensure!(
        !bytes.is_empty() && bytes.len() as u64 <= MAX_PAGE_BYTES,
        "The compressed page is empty or exceeds 32 MiB"
    );
    let (width, height) = reader(bytes)?
        .into_dimensions()
        .context("Read page dimensions")?;
    ensure!(width > 0 && height > 0, "The page has no image pixels");
    ensure!(
        width <= MAX_SOURCE_WIDTH && height <= MAX_SOURCE_HEIGHT,
        "The source dimensions exceed the decoder limit; split the page first"
    );
    if u64::from(width) * u64::from(height) > MAX_PAGE_PIXELS {
        bail!("The source page exceeds 16 megapixels; split the page first");
    }
    let image = reader(bytes)?
        .decode()
        .context("Decode page image within memory limit")?;
    #[cfg(test)]
    IMAGE_WORK.with(|work| {
        let mut current = work.get();
        current.bitmap_decodes += 1;
        work.set(current);
    });
    Ok(image)
}

fn crop_white_borders(image: GrayImage) -> GrayImage {
    let (width, height) = image.dimensions();
    let crop = white_border_crop(&image);
    if crop.x == 0 && crop.y == 0 && crop.width == width && crop.height == height {
        image
    } else {
        imageops::crop_imm(&image, crop.x, crop.y, crop.width, crop.height).to_image()
    }
}

fn white_border_crop(image: &GrayImage) -> CropRect {
    let (width, height) = image.dimensions();
    let whole = CropRect {
        x: 0,
        y: 0,
        width,
        height,
    };
    if width == 0 || height == 0 {
        return whole;
    }
    let rows = image.as_raw()[..width as usize * height as usize].chunks_exact(width as usize);
    let Some(top) = rows
        .clone()
        .position(|row| row.iter().any(|value| *value < 247))
    else {
        return whole;
    };
    let bottom = rows
        .clone()
        .rposition(|row| row.iter().any(|value| *value < 247))
        .expect("The first content row proves that a final content row exists");

    // The crop retains two paper pixels and discards at most one fifth of an
    // edge. Only those edge strips can change the resulting horizontal bounds;
    // artwork in the middle cannot move an already clamped crop boundary.
    // Shrink each remaining search after finding a closer content pixel. Pages
    // whose artwork reaches all four edges need only the boundary samples.
    let search = (width / 5).saturating_add(2).min(width) as usize;
    let (mut left, mut right_margin) = (search, search);
    for row in rows.skip(top).take(bottom - top + 1) {
        if left > 2 {
            if let Some(position) = row[..left].iter().position(|value| *value < 247) {
                left = position;
            }
        }
        if right_margin > 2 {
            if let Some(position) = row[row.len() - right_margin..]
                .iter()
                .rev()
                .position(|value| *value < 247)
            {
                right_margin = position;
            }
        }
        if left <= 2 && right_margin <= 2 {
            break;
        }
    }
    bounded_crop(
        width,
        height,
        left as u32,
        width - 1 - right_margin.min(width as usize - 1) as u32,
        top as u32,
        bottom as u32,
    )
}

fn bounded_crop(width: u32, height: u32, left: u32, right: u32, top: u32, bottom: u32) -> CropRect {
    // Keep a small paper edge and never discard more than a fifth of a page
    // from one edge, even when the artwork itself is mostly white.
    let left = left.saturating_sub(2).min(width / 5);
    let top = top.saturating_sub(2).min(height / 5);
    let right = right
        .saturating_add(2)
        .max(width - width / 5 - 1)
        .min(width - 1);
    let bottom = bottom
        .saturating_add(2)
        .max(height - height / 5 - 1)
        .min(height - 1);
    CropRect {
        x: left,
        y: top,
        width: right - left + 1,
        height: bottom - top + 1,
    }
}

fn fit_dimensions(width: u32, height: u32, max_width: u32, max_height: u32) -> (u32, u32) {
    let scale =
        (f64::from(max_width) / f64::from(width)).min(f64::from(max_height) / f64::from(height));
    (
        (f64::from(width) * scale)
            .round()
            .max(1.0)
            .min(f64::from(max_width)) as u32,
        (f64::from(height) * scale)
            .round()
            .max(1.0)
            .min(f64::from(max_height)) as u32,
    )
}

fn place_page(output: &mut GrayImage, page: &GrayImage, x: u32, width: u32, height: u32) {
    if page.width() == 0 || page.height() == 0 {
        return;
    }
    let (target_width, target_height) = fit_dimensions(page.width(), page.height(), width, height);
    let resized = imageops::resize(page, target_width, target_height, FilterType::Lanczos3);
    let offset_x = x + (width - target_width) / 2;
    let offset_y = (height - target_height) / 2;
    imageops::replace(output, &resized, i64::from(offset_x), i64::from(offset_y));
}

fn apply_contrast(image: &mut GrayImage, contrast: i32) {
    let contrast = contrast.clamp(-100, 100);
    if contrast == 0 {
        return;
    }
    let factor = (259.0 * (f64::from(contrast) + 255.0)) / (255.0 * (259.0 - f64::from(contrast)));
    let values = std::array::from_fn::<_, 256, _>(|value| {
        (factor * (value as f64 - 128.0) + 128.0)
            .round()
            .clamp(0.0, 255.0) as u8
    });
    let length = image.width() as usize * image.height() as usize;
    for value in &mut image.as_mut()[..length] {
        *value = values[*value as usize];
    }
}

fn quantize(image: &mut GrayImage, dither: bool) {
    if !dither {
        for pixel in image.pixels_mut() {
            pixel[0] = (((u16::from(pixel[0]) + 8) / 17) * 17) as u8;
        }
        return;
    }
    let width = image.width() as usize;
    if width == 0 {
        return;
    }
    let mut current = vec![0_i32; width + 2];
    let mut next = vec![0_i32; width + 2];
    let length = width * image.height() as usize;
    for (y, row) in image.as_mut()[..length].chunks_exact_mut(width).enumerate() {
        let reverse = y % 2 == 1;
        for position in 0..width {
            let x = if reverse {
                width - position - 1
            } else {
                position
            };
            let value = (i32::from(row[x]) * 16 + current[x + 1]).clamp(0, 255 * 16);
            let gray = ((value + 136) / 272 * 17).clamp(0, 255);
            row[x] = gray as u8;
            let error = value - gray * 16;
            if reverse {
                current[x] += error * 7 / 16;
                next[x + 2] += error * 3 / 16;
                next[x + 1] += error * 5 / 16;
                next[x] += error / 16;
            } else {
                current[x + 2] += error * 7 / 16;
                next[x] += error * 3 / 16;
                next[x + 1] += error * 5 / 16;
                next[x + 2] += error / 16;
            }
        }
        std::mem::swap(&mut current, &mut next);
        next.fill(0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{DynamicImage, ImageFormat, Rgba, RgbaImage};
    use std::{io::Write, sync::OnceLock};

    fn png(image: impl Into<DynamicImage>) -> Vec<u8> {
        let mut bytes = Cursor::new(Vec::new());
        image.into().write_to(&mut bytes, ImageFormat::Png).unwrap();
        bytes.into_inner()
    }

    fn options(width: u32, height: u32) -> ImageOptions {
        ImageOptions {
            width,
            height,
            crop_borders: false,
            dither: false,
            contrast: 0,
        }
    }

    pub(super) fn reference_crop_white_borders(image: GrayImage) -> GrayImage {
        let (width, height) = image.dimensions();
        // Empty images have no content bounds. The legacy extrema scan's
        // sentinel values otherwise resemble a zero-coordinate content pixel.
        if width == 0 || height == 0 {
            return image;
        }
        let (mut left, mut right, mut top, mut bottom) = (width, 0, height, 0);
        for (x, y, pixel) in image.enumerate_pixels() {
            if pixel[0] < 247 {
                left = left.min(x);
                right = right.max(x);
                top = top.min(y);
                bottom = bottom.max(y);
            }
        }
        if left > right || top > bottom {
            return image;
        }
        let crop = bounded_crop(width, height, left, right, top, bottom);
        if crop.x == 0 && crop.y == 0 && crop.width == width && crop.height == height {
            image
        } else {
            imageops::crop_imm(&image, crop.x, crop.y, crop.width, crop.height).to_image()
        }
    }

    pub(super) fn reference_apply_contrast(image: &mut GrayImage, contrast: i32) {
        let value = contrast.clamp(-100, 100);
        if value == 0 {
            return;
        }
        let factor = (259.0 * (f64::from(value) + 255.0)) / (255.0 * (259.0 - f64::from(value)));
        for pixel in image.pixels_mut() {
            pixel[0] = (factor * (f64::from(pixel[0]) - 128.0) + 128.0)
                .round()
                .clamp(0.0, 255.0) as u8;
        }
    }

    pub(super) fn reference_quantize(image: &mut GrayImage, dither: bool) {
        if !dither {
            for pixel in image.pixels_mut() {
                pixel[0] = (((u16::from(pixel[0]) + 8) / 17) * 17) as u8;
            }
            return;
        }
        let width = image.width() as usize;
        let mut current = vec![0_i32; width + 2];
        let mut next = vec![0_i32; width + 2];
        for y in 0..image.height() {
            let reverse = y % 2 == 1;
            for position in 0..width {
                let x = if reverse {
                    width - position - 1
                } else {
                    position
                };
                let value = (i32::from(image.get_pixel(x as u32, y)[0]) * 16 + current[x + 1])
                    .clamp(0, 255 * 16);
                let gray = ((value + 136) / 272 * 17).clamp(0, 255);
                image.put_pixel(x as u32, y, Luma([gray as u8]));
                let error = value - gray * 16;
                if reverse {
                    current[x] += error * 7 / 16;
                    next[x + 2] += error * 3 / 16;
                    next[x + 1] += error * 5 / 16;
                    next[x] += error / 16;
                } else {
                    current[x + 2] += error * 7 / 16;
                    next[x] += error * 3 / 16;
                    next[x + 1] += error * 5 / 16;
                    next[x + 2] += error / 16;
                }
            }
            std::mem::swap(&mut current, &mut next);
            next.fill(0);
        }
    }

    #[test]
    fn edge_strip_crop_matches_full_pixel_bounds_and_thresholds() {
        let mut state = 0x7d31_4a9b_632e_0851_u64;
        let mut next = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state as u8
        };
        for width in [0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 17, 31, 64, 97] {
            for height in [0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 17, 31, 64, 97] {
                for pattern in 0..10 {
                    let source = GrayImage::from_fn(width, height, |x, y| {
                        Luma([match pattern {
                            0 => 255,
                            1 => 247,
                            2 => 246,
                            3 => u8::from(x > 0 && y > 0) * 255,
                            4 => u8::from(x + 1 < width && y + 1 < height) * 255,
                            5 => {
                                if x == width / 2 && y == height / 2 {
                                    246
                                } else {
                                    247
                                }
                            }
                            6 => {
                                if x < width / 5 + 2 || x >= width.saturating_sub(width / 5 + 2) {
                                    255
                                } else {
                                    0
                                }
                            }
                            7 => {
                                if y < height / 5 + 2 || y >= height.saturating_sub(height / 5 + 2)
                                {
                                    255
                                } else {
                                    0
                                }
                            }
                            8 => {
                                if next() < 4 {
                                    246
                                } else {
                                    255
                                }
                            }
                            _ => next(),
                        }])
                    });
                    let expected = reference_crop_white_borders(source.clone());
                    let actual = crop_white_borders(source);
                    assert_eq!(
                        actual, expected,
                        "Crop mismatch for {width}x{height}, pattern {pattern}"
                    );
                }
            }
        }
        let source =
            GrayImage::from_raw(3, 2, vec![255, 247, 246, 255, 255, 255, 0, 0, 0]).unwrap();
        assert_eq!(
            crop_white_borders(source.clone()),
            reference_crop_white_borders(source)
        );
    }

    #[test]
    fn contrast_lookup_preserves_every_level_and_clamped_setting() {
        for contrast in -120..=120 {
            let mut source =
                GrayImage::from_raw(256, 1, (0..=255).chain([13, 27]).collect()).unwrap();
            let mut expected = source.clone();
            reference_apply_contrast(&mut expected, contrast);
            apply_contrast(&mut source, contrast);
            assert_eq!(
                source.as_raw(),
                expected.as_raw(),
                "Contrast mismatch at {contrast}"
            );
        }
    }

    #[test]
    fn row_quantization_preserves_serpentine_diffusion_and_trailing_storage() {
        let mut state = 0xa92e_3d17_54bc_806f_u64;
        for width in [0, 1, 2, 3, 7, 16, 31, 65] {
            for height in [0, 1, 2, 3, 7, 16, 31] {
                let mut pixels = Vec::new();
                for _ in 0..width * height + 3 {
                    state ^= state << 13;
                    state ^= state >> 7;
                    state ^= state << 17;
                    pixels.push(state as u8);
                }
                let source = GrayImage::from_raw(width, height, pixels).unwrap();
                for dither in [false, true] {
                    let mut expected = source.clone();
                    reference_quantize(&mut expected, dither);
                    let mut actual = source.clone();
                    quantize(&mut actual, dither);
                    assert_eq!(
                        actual.as_raw(),
                        expected.as_raw(),
                        "Quantization mismatch for {width}x{height}, dither {dither}"
                    );
                }
            }
        }
    }

    fn measured_work<T>(render: impl FnOnce() -> T) -> (T, ImageWork) {
        IMAGE_WORK.with(|work| work.set(ImageWork::default()));
        let output = render();
        let work = IMAGE_WORK.with(std::cell::Cell::get);
        (output, work)
    }

    fn long_png() -> &'static [u8] {
        static BYTES: OnceLock<Vec<u8>> = OnceLock::new();
        BYTES.get_or_init(|| {
            let mut bytes = Vec::new();
            let mut encoder = png::Encoder::new(&mut bytes, 1_024, 20_000);
            encoder.set_color(png::ColorType::Grayscale);
            encoder.set_depth(png::BitDepth::Eight);
            {
                let mut writer = encoder.write_header().unwrap();
                {
                    let mut stream = writer.stream_writer().unwrap();
                    let dark = [0_u8; 1_024];
                    let paper = [255_u8; 1_024];
                    for y in 0..20_000 {
                        stream
                            .write_all(if y < 10_000 { &dark } else { &paper })
                            .unwrap();
                    }
                    stream.finish().unwrap();
                }
                writer.finish().unwrap();
            }
            bytes
        })
    }

    #[test]
    fn alpha_is_composited_onto_white_paper() {
        let source = RgbaImage::from_pixel(4, 4, Rgba([0, 0, 0, 0]));
        let result = decode_page(&png(source), &options(4, 4)).unwrap();
        assert!(result.pixels().all(|pixel| pixel[0] == 255));
    }

    #[test]
    fn fit_preserves_aspect_ratio_and_emits_only_native_gray_levels() {
        let source = GrayImage::from_fn(160, 80, |x, _| Luma([(x * 255 / 159) as u8]));
        let mut settings = options(80, 80);
        settings.dither = true;
        let output = decode_page(&png(source), &settings).unwrap();
        assert_eq!(output.dimensions(), (80, 40));
        assert!(output.pixels().all(|pixel| pixel[0] % 17 == 0));
        let mean = output
            .pixels()
            .map(|pixel| f64::from(pixel[0]))
            .sum::<f64>()
            / 3_200.0;
        assert!((mean - 127.5).abs() < 2.0);
    }

    #[test]
    fn oversized_page_metadata_is_rejected_before_full_decode() {
        let source = GrayImage::from_pixel(MAX_SOURCE_WIDTH + 1, 1, Luma([255]));
        let error = decode_page(&png(source), &options(80, 80)).unwrap_err();
        assert!(error.to_string().contains("dimension") || error.to_string().contains("limit"));
    }

    #[test]
    fn blank_page_remains_valid_when_cropping() {
        let source = GrayImage::from_pixel(40, 80, Luma([255]));
        let mut settings = options(40, 80);
        settings.crop_borders = true;
        let output = decode_page(&png(source), &settings).unwrap();
        assert_eq!(output.dimensions(), (40, 80));
    }

    #[test]
    fn vertical_steps_keep_overlap_and_reach_the_bottom() {
        assert_eq!(vertical_windows(1_000, 400), vec![0, 340, 600]);
        assert_eq!(vertical_windows(400, 400), vec![0]);
        assert_eq!(vertical_windows(100, 0), Vec::<u32>::new());
    }

    #[test]
    fn vertical_window_uses_scaled_offsets() {
        let source = GrayImage::from_fn(20, 200, |_, y| Luma([if y < 100 { 0 } else { 255 }]));
        let bytes = png(source);
        let top = render_vertical_window(&bytes, &options(40, 100), 0).unwrap();
        let bottom = render_vertical_window(&bytes, &options(40, 100), 300).unwrap();
        assert_eq!(top.dimensions(), (40, 100));
        assert!(top.pixels().all(|pixel| pixel[0] == 0));
        assert!(bottom.pixels().all(|pixel| pixel[0] == 255));
    }

    #[test]
    fn vertical_geometry_uses_the_same_crop_as_the_rendered_windows() {
        let source = GrayImage::from_fn(100, 300, |x, y| {
            Luma([if (20..80).contains(&x) && (20..280).contains(&y) {
                0
            } else {
                255
            }])
        });
        let bytes = png(source);
        let mut settings = options(200, 100);
        settings.crop_borders = true;
        assert_eq!(vertical_dimensions(&bytes, &settings).unwrap(), (200, 825));
        let bottom = render_vertical_window(&bytes, &settings, 725).unwrap();
        assert_eq!(bottom.dimensions(), (200, 100));
        assert_eq!(bottom.get_pixel(100, 50)[0], 0);
    }

    #[test]
    fn vertical_render_and_geometry_share_one_cropped_bitmap_decode() {
        let source = GrayImage::from_fn(100, 300, |x, y| {
            Luma([if (20..80).contains(&x) && (20..280).contains(&y) {
                0
            } else {
                255
            }])
        });
        let bytes = png(source);
        let mut settings = options(200, 100);
        settings.crop_borders = true;
        let (window, work) =
            measured_work(|| render_vertical_window_with_geometry(&bytes, &settings, 725).unwrap());
        assert_eq!(window.scaled_height, 825);
        assert_eq!(window.viewport_height, 100);
        assert_eq!(window.image.dimensions(), (200, 100));
        assert_eq!(window.image.get_pixel(100, 50)[0], 0);
        assert_eq!(
            work,
            ImageWork {
                bitmap_decodes: 1,
                ..ImageWork::default()
            }
        );
        assert_eq!(
            vertical_dimensions(&bytes, &settings).unwrap(),
            (settings.width, window.scaled_height)
        );
        eprintln!("Combined bounded vertical viewport work: {work:?}");
    }

    #[test]
    fn reader_raster_adjacent_vertical_windows_share_one_source_decode() {
        let source = GrayImage::from_fn(80, 600, |x, y| Luma([((x * 7 + y / 3) % 247) as u8]));
        let bytes = png(source);
        let settings = options(160, 100);
        let ((first, source), work) = measured_work(|| {
            render_vertical_raster_with_source(&bytes, &settings, 0, None).unwrap()
        });
        assert_eq!(work.bitmap_decodes, 1);
        let source = source.unwrap();
        let ((second, reused), work) = measured_work(|| {
            render_vertical_raster_with_source(&bytes, &settings, 85, Some(source.clone())).unwrap()
        });
        assert_eq!(work.bitmap_decodes, 0);
        assert!(source.same_allocation(&reused.unwrap()));
        assert_eq!(first.scaled_height, second.scaled_height);
        assert_ne!(first.image, second.image);
        let expected = render_vertical_raster_with_geometry(&bytes, &settings, 85).unwrap();
        assert_eq!(second.image, expected.image);
        assert_eq!(second.viewport_height, expected.viewport_height);
    }

    #[test]
    fn combined_streaming_window_reuses_its_crop_pass_for_geometry() {
        let mut settings = options(128, 100);
        settings.crop_borders = true;
        let (window, work) = measured_work(|| {
            render_vertical_window_with_geometry(long_png(), &settings, 1_900).unwrap()
        });
        assert_eq!(window.scaled_height, 2_000);
        assert_eq!(window.viewport_height, 100);
        assert_eq!(window.image.dimensions(), (128, 100));
        assert!(window.image.pixels().all(|pixel| pixel[0] == 255));
        assert_eq!(
            work,
            ImageWork {
                bounds_passes: 1,
                bounds_rows: 20_000,
                viewport_passes: 1,
                viewport_rows: 20_000,
                ..ImageWork::default()
            }
        );
        assert_eq!(
            vertical_dimensions(long_png(), &settings).unwrap(),
            (settings.width, window.scaled_height)
        );
        eprintln!("Combined cropped streaming viewport work: {work:?}");
    }

    #[test]
    fn uncropped_streaming_window_measures_geometry_in_one_complete_row_pass() {
        let settings = options(128, 100);
        let (window, work) = measured_work(|| {
            render_vertical_window_with_geometry(long_png(), &settings, 2_400).unwrap()
        });
        assert_eq!(window.scaled_height, 2_500);
        assert_eq!(window.viewport_height, 100);
        assert_eq!(window.image.dimensions(), (128, 100));
        assert!(window.image.pixels().all(|pixel| pixel[0] == 255));
        assert_eq!(
            work,
            ImageWork {
                viewport_passes: 1,
                viewport_rows: 20_000,
                ..ImageWork::default()
            }
        );
        eprintln!("Combined uncropped streaming viewport work: {work:?}");
    }

    #[test]
    fn combined_streaming_geometry_does_not_skip_truncated_final_chunks() {
        let bytes = long_png();
        let truncated = &bytes[..bytes.len() - 12];
        for crop_borders in [false, true] {
            let mut settings = options(128, 100);
            settings.crop_borders = crop_borders;
            assert!(render_vertical_window_with_geometry(truncated, &settings, 0).is_err());
        }
    }

    #[test]
    fn rtl_spreads_place_the_first_page_on_the_right() {
        let first = GrayImage::from_pixel(20, 20, Luma([0]));
        let second = GrayImage::from_pixel(20, 20, Luma([255]));
        let output = render_spread(&first, &second, 82, 40, true);
        assert_eq!(output.get_pixel(20, 20)[0], 255);
        assert_eq!(output.get_pixel(62, 20)[0], 0);
    }

    #[test]
    fn preload_never_exceeds_two_pages_or_the_chapter() {
        assert_eq!(preload_indices(0, 10), vec![1, 2]);
        assert_eq!(preload_indices(8, 10), vec![9]);
        assert!(preload_indices(10, 10).is_empty());
        assert!(preload_indices(usize::MAX, usize::MAX).is_empty());
    }

    #[test]
    fn invalid_target_dimensions_are_rejected() {
        let bytes = png(GrayImage::from_pixel(1, 1, Luma([0])));
        assert!(decode_page(&bytes, &options(0, 100)).is_err());
    }

    #[test]
    fn long_png_is_validated_and_rendered_without_a_source_sized_bitmap() {
        let bytes = long_png();
        assert!(needs_png_streaming(bytes).unwrap());
        assert!(decode_grayscale(bytes).is_err());
        validate_page(bytes).unwrap();
        assert_eq!(
            vertical_dimensions(bytes, &options(128, 100)).unwrap(),
            (128, 2_500)
        );
        let top = render_vertical_window(bytes, &options(128, 100), 0).unwrap();
        let bottom = render_vertical_window(bytes, &options(128, 100), 2_400).unwrap();
        assert_eq!(top.dimensions(), (128, 100));
        assert!(top.pixels().all(|pixel| pixel[0] == 0));
        assert!(bottom.pixels().all(|pixel| pixel[0] == 255));
        let fit = decode_page(bytes, &options(128, 200)).unwrap();
        assert_eq!(fit.dimensions(), (10, 200));
        assert!(fit.pixels().all(|pixel| pixel[0] % 17 == 0));
    }

    #[test]
    fn long_png_validation_rejects_missing_final_chunks() {
        let bytes = long_png();
        assert!(validate_page(&bytes[..bytes.len() - 12]).is_err());
    }

    #[test]
    fn file_validation_streams_long_pngs_and_rejects_truncated_files() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("long.png");
        fs::write(&path, long_png()).unwrap();
        validate_file(&path).unwrap();
        let bytes = long_png();
        fs::write(&path, &bytes[..bytes.len() - 12]).unwrap();
        assert!(validate_file(&path).is_err());
    }

    #[test]
    fn oversized_interlaced_png_is_rejected_before_bitmap_allocation() {
        let mut bytes = Vec::new();
        let mut info = png::Info::with_size(1_024, 20_000);
        info.interlaced = true;
        let encoder = png::Encoder::with_info(&mut bytes, info).unwrap();
        let writer = encoder.write_header().unwrap();
        let _ = writer.finish();
        let error = validate_page(&bytes).unwrap_err();
        assert!(error.to_string().contains("interlaced"));
    }
}
