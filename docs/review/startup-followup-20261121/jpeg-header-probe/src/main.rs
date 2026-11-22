use std::{
    error::Error,
    fs::File,
    hint::black_box,
    io::{Cursor, Read},
    time::{Duration, Instant},
};

use image::{DynamicImage, ImageDecoder, ImageReader, Limits};

const MAX_PAGE_BYTES: u64 = 32 * 1024 * 1024;
const MAX_PAGE_PIXELS: u64 = 16 * 1024 * 1024;
const MAX_DECODE_BYTES: u64 = 96 * 1024 * 1024;
const MAX_SOURCE_WIDTH: u32 = 32_768;
const MAX_SOURCE_HEIGHT: u32 = 131_072;
const IMAGE_COUNT: usize = 6;
const PAIR_COUNT: usize = 10;

type Result<T> = std::result::Result<T, Box<dyn Error>>;
type Decode = fn(&[u8]) -> Result<DynamicImage>;

fn limits() -> Limits {
    let mut limits = Limits::default();
    limits.max_image_width = Some(MAX_SOURCE_WIDTH);
    limits.max_image_height = Some(MAX_SOURCE_HEIGHT);
    limits.max_alloc = Some(MAX_DECODE_BYTES);
    limits
}

fn reader(bytes: &[u8]) -> Result<ImageReader<Cursor<&[u8]>>> {
    let mut reader = ImageReader::new(Cursor::new(bytes)).with_guessed_format()?;
    reader.limits(limits());
    Ok(reader)
}

fn check_bytes(bytes: &[u8]) -> Result<()> {
    if bytes.is_empty() || bytes.len() as u64 > MAX_PAGE_BYTES {
        return Err("The compressed page is empty or exceeds 32 MiB".into());
    }
    Ok(())
}

fn check_dimensions(width: u32, height: u32) -> Result<()> {
    if width == 0 || height == 0 {
        return Err("The page has no image pixels".into());
    }
    if width > MAX_SOURCE_WIDTH || height > MAX_SOURCE_HEIGHT {
        return Err("The source dimensions exceed the decoder limit; split the page first".into());
    }
    if u64::from(width) * u64::from(height) > MAX_PAGE_PIXELS {
        return Err("The source page exceeds 16 megapixels; split the page first".into());
    }
    Ok(())
}

fn control(bytes: &[u8]) -> Result<DynamicImage> {
    check_bytes(bytes)?;
    let (width, height) = reader(bytes)?.into_dimensions()?;
    check_dimensions(width, height)?;
    Ok(reader(bytes)?.decode()?)
}

fn candidate(bytes: &[u8]) -> Result<DynamicImage> {
    check_bytes(bytes)?;
    let mut decoder = reader(bytes)?.into_decoder()?;
    let (width, height) = decoder.dimensions();
    check_dimensions(width, height)?;
    let mut limits = limits();
    limits.reserve(decoder.total_bytes())?;
    decoder.set_limits(limits)?;
    Ok(DynamicImage::from_decoder(decoder)?)
}

fn measure(inputs: &[Vec<u8>], decode: Decode) -> Result<Duration> {
    let started = Instant::now();
    for bytes in inputs {
        drop(black_box(decode(black_box(bytes))?));
    }
    Ok(started.elapsed())
}

fn exact_parity(left: &DynamicImage, right: &DynamicImage) -> bool {
    left.width() == right.width()
        && left.height() == right.height()
        && left.color() == right.color()
        && left.as_bytes() == right.as_bytes()
}

fn verify(inputs: &[Vec<u8>]) -> Result<()> {
    for bytes in inputs {
        if !exact_parity(&control(bytes)?, &candidate(bytes)?) {
            return Err("Decoded images differ".into());
        }
    }
    Ok(())
}

fn main() -> Result<()> {
    let mut parity_only = false;
    let mut paths = Vec::new();
    for argument in std::env::args_os().skip(1) {
        if argument == "--parity-only" {
            if parity_only {
                return Err("The --parity-only option may appear only once".into());
            }
            parity_only = true;
        } else {
            paths.push(argument);
        }
    }
    if paths.len() != IMAGE_COUNT {
        return Err("Expected six synthetic JPEG paths and optional --parity-only".into());
    }
    let mut inputs = Vec::with_capacity(IMAGE_COUNT);
    for path in paths {
        let mut bytes = Vec::new();
        File::open(path)?
            .take(MAX_PAGE_BYTES + 1)
            .read_to_end(&mut bytes)?;
        check_bytes(&bytes)?;
        inputs.push(bytes);
    }
    let total_encoded_bytes: u64 = inputs.iter().map(|bytes| bytes.len() as u64).sum();

    verify(&inputs)?;
    println!("images={IMAGE_COUNT} total_encoded_bytes={total_encoded_bytes} exact_parity=true");
    if parity_only {
        return Ok(());
    }

    println!(
        "clock=std::time::Instant units=nanoseconds scope=host_only_not_Kindle input_io=excluded comparison=excluded total_encoded_bytes={total_encoded_bytes}"
    );
    measure(&inputs, control)?;
    measure(&inputs, candidate)?;

    for pair in 0..PAIR_COUNT {
        let (control_time, candidate_time, order) = if pair % 2 == 0 {
            let first = measure(&inputs, control)?;
            (first, measure(&inputs, candidate)?, "AB")
        } else {
            let first = measure(&inputs, candidate)?;
            (measure(&inputs, control)?, first, "BA")
        };
        println!(
            "pair={} order={} control_ns={} candidate_ns={}",
            pair + 1,
            order,
            control_time.as_nanos(),
            candidate_time.as_nanos()
        );
    }
    verify(&inputs)?;
    println!("post_timing_exact_parity=true");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{GrayImage, Luma, codecs::jpeg::JpegEncoder};

    #[test]
    fn grayscale_jpeg_has_exact_parity() {
        let image = DynamicImage::ImageLuma8(GrayImage::from_fn(11, 9, |column, row| {
            Luma([((column * 19 + row * 7) % 256) as u8])
        }));
        let mut encoded = Vec::new();
        JpegEncoder::new(&mut encoded).encode_image(&image).unwrap();
        let first = control(&encoded).unwrap();
        let second = candidate(&encoded).unwrap();
        assert!(exact_parity(&first, &second));
    }

    #[test]
    fn malformed_and_oversized_inputs_are_rejected() {
        for decode in [control as Decode, candidate as Decode] {
            assert!(decode(&[]).is_err());
            assert!(decode(&[0xff, 0xd8, 0xff]).is_err());
            assert!(decode(&vec![0; MAX_PAGE_BYTES as usize + 1]).is_err());
        }
        assert!(check_dimensions(0, 1).is_err());
        assert!(check_dimensions(MAX_SOURCE_WIDTH + 1, 1).is_err());
        assert!(check_dimensions(1, MAX_SOURCE_HEIGHT + 1).is_err());
        assert!(check_dimensions(4_097, 4_096).is_err());
    }

    #[test]
    fn comparison_rejects_different_pixels() {
        let first = DynamicImage::ImageLuma8(GrayImage::from_pixel(1, 1, Luma([1])));
        let second = DynamicImage::ImageLuma8(GrayImage::from_pixel(1, 1, Luma([2])));
        assert!(!exact_parity(&first, &second));
    }
}
