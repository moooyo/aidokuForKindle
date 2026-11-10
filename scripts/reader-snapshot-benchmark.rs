//! Standalone comparison of existing rendered PNGs and an additional snapshot.
//!
//! Run only on the designated remote worker. The existing production modules
//! are compiled directly into a small harness, without the desktop UI stack.

#![allow(dead_code)]

use std::{
    fs::{self, File, OpenOptions},
    io::{BufReader, Cursor, Read, Write},
    path::{Path, PathBuf},
    sync::Arc,
    time::Instant,
};

use anyhow::{Context, Result, bail, ensure};
use flate2::{Compression, read::ZlibDecoder, write::ZlibEncoder};
use image::GrayImage;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

#[path = "snapshot-production/cache.rs"]
mod cache;
#[path = "snapshot-production/domain.rs"]
mod domain;
#[path = "snapshot-production/imaging.rs"]
mod imaging;
#[path = "snapshot-production/render_cache.rs"]
mod render_cache;

#[cfg(all(feature = "host-zstd", not(target_os = "linux")))]
compile_error!("The optional system-zstd benchmark requires a Linux host");
#[cfg(all(feature = "host-zstd", target_os = "linux"))]
#[path = "reader-snapshot-benchmark-zstd.rs"]
mod zstd_snapshot;

const MAGIC: &[u8; 8] = b"AKSNAP01";
const MAX_SNAPSHOT_BYTES: usize = 12 * 1024 * 1024;
const CODECS: [&str; 5] = ["png", "zlib1", "zlib6", "packed1", "packed6"];
const ZSTD_CODECS: [&str; 3] = ["png", "zstd1", "zstd3"];

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Case {
    name: String,
    page: domain::Page,
    width: u32,
    height: u32,
    target_width: u32,
    target_height: u32,
    vertical: bool,
    offset: u32,
    scaled_height: u32,
    viewport_height: u32,
    png_path: PathBuf,
    png_metadata: String,
    pixel_sha256: String,
    source_sha256: String,
    alias: String,
}

impl Case {
    fn options(&self) -> imaging::ImageOptions {
        imaging::ImageOptions {
            width: self.target_width,
            height: self.target_height,
            crop_borders: false,
            dither: true,
            contrast: 0,
        }
    }
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SnapshotHeader {
    version: u8,
    width: u32,
    height: u32,
    packed: bool,
    source_sha256: String,
    render_metadata: String,
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn codec_path(directory: &Path, codec: &str) -> PathBuf {
    directory.join(format!("snapshot-{codec}.bin"))
}

fn initialize_codec(codec: &str) -> Result<Option<Value>> {
    if !codec.starts_with("zstd") {
        return Ok(None);
    }
    #[cfg(all(feature = "host-zstd", target_os = "linux"))]
    return zstd_snapshot::initialize().map(Some);
    #[cfg(not(all(feature = "host-zstd", target_os = "linux")))]
    bail!("Build the isolated Linux harness with --features host-zstd");
}

fn encode(case: &Case, pixels: &[u8], codec: &str) -> Result<Vec<u8>> {
    if codec.starts_with("zstd") {
        let level = match codec {
            "zstd1" => 1,
            "zstd3" => 3,
            _ => bail!("Unknown zstd codec: {codec}"),
        };
        #[cfg(all(feature = "host-zstd", target_os = "linux"))]
        return zstd_snapshot::encode(case, pixels, level);
        #[cfg(not(all(feature = "host-zstd", target_os = "linux")))]
        {
            let _ = level;
            bail!("Build the isolated Linux harness with --features host-zstd");
        }
    }
    if codec == "png" {
        let mut bytes = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut bytes, case.width, case.height);
            encoder.set_color(png::ColorType::Grayscale);
            encoder.set_depth(png::BitDepth::Eight);
            encoder.add_text_chunk("AidokuRender".into(), case.png_metadata.clone())?;
            let mut writer = encoder.write_header()?;
            writer.write_image_data(pixels)?;
            writer.finish()?;
        }
        return Ok(bytes);
    }
    let packed = codec.starts_with("packed");
    let level = match codec {
        "zlib1" | "packed1" => 1,
        "zlib6" | "packed6" => 6,
        _ => bail!("Unknown codec: {codec}"),
    };
    let header = SnapshotHeader {
        version: 1,
        width: case.width,
        height: case.height,
        packed,
        source_sha256: case.source_sha256.clone(),
        render_metadata: case.png_metadata.clone(),
    };
    let header = serde_json::to_vec(&header)?;
    ensure!(header.len() <= 2_048, "The benchmark metadata is oversized");
    let mut output = Vec::new();
    output.extend_from_slice(MAGIC);
    output.extend_from_slice(&(header.len() as u32).to_le_bytes());
    output.extend_from_slice(&header);
    let mut encoder = ZlibEncoder::new(output, Compression::new(level));
    if packed {
        let mut packed = Vec::with_capacity(pixels.len().div_ceil(2));
        for pair in pixels.chunks(2) {
            ensure!(
                pair.iter().all(|value| value % 17 == 0),
                "The image is not sixteen-level grayscale"
            );
            let low = pair.get(1).copied().unwrap_or(0) / 17;
            packed.push((pair[0] / 17) << 4 | low);
        }
        encoder.write_all(&packed)?;
    } else {
        encoder.write_all(pixels)?;
    }
    Ok(encoder.finish()?)
}

fn decode_png(input: impl Read + std::io::Seek, case: &Case) -> Result<Vec<u8>> {
    let decoder = png::Decoder::new_with_limits(
        BufReader::new(input),
        png::Limits {
            bytes: imaging::MAX_DECODE_BYTES as usize,
        },
    );
    let mut reader = decoder.read_info()?;
    let info = reader.info();
    ensure!(
        info.width == case.width && info.height == case.height,
        "PNG dimensions differ"
    );
    ensure!(
        info.color_type == png::ColorType::Grayscale && info.bit_depth == png::BitDepth::Eight,
        "PNG depth differs"
    );
    let metadata = info
        .uncompressed_latin1_text
        .iter()
        .find(|value| value.keyword == "AidokuRender")
        .context("PNG metadata missing")?;
    ensure!(metadata.text == case.png_metadata, "PNG identity differs");
    let size = reader.output_buffer_size().context("PNG output overflow")?;
    ensure!(
        size <= MAX_SNAPSHOT_BYTES,
        "PNG output exceeds the snapshot budget"
    );
    let mut pixels = vec![0; size];
    let frame = reader.next_frame(&mut pixels)?;
    ensure!(
        frame.buffer_size() == case.width as usize * case.height as usize,
        "PNG output length differs"
    );
    pixels.truncate(frame.buffer_size());
    reader.finish()?;
    Ok(pixels)
}

fn decode_raw(mut input: impl Read, file_len: u64, case: &Case) -> Result<Vec<u8>> {
    let mut magic = [0; 8];
    input.read_exact(&mut magic)?;
    ensure!(&magic == MAGIC, "Snapshot magic differs");
    let mut size = [0; 4];
    input.read_exact(&mut size)?;
    let header_len = u32::from_le_bytes(size) as usize;
    ensure!(header_len <= 2_048, "Snapshot header is oversized");
    let mut header = vec![0; header_len];
    input.read_exact(&mut header)?;
    let header: SnapshotHeader = serde_json::from_slice(&header)?;
    ensure!(
        header.version == 1 && header.width == case.width && header.height == case.height,
        "Snapshot dimensions or version differ"
    );
    ensure!(
        header.source_sha256 == case.source_sha256 && header.render_metadata == case.png_metadata,
        "Snapshot identity differs"
    );
    let length = usize::try_from(u64::from(header.width) * u64::from(header.height))?;
    ensure!(
        length > 0 && length <= MAX_SNAPSHOT_BYTES,
        "Snapshot pixels exceed the byte budget"
    );
    let expected = if header.packed {
        length.div_ceil(2)
    } else {
        length
    };
    let mut decoder = ZlibDecoder::new(input);
    let mut bytes = vec![0; expected];
    decoder.read_exact(&mut bytes)?;
    let mut extra = [0; 1];
    ensure!(
        decoder.read(&mut extra)? == 0,
        "Snapshot contains excess pixels"
    );
    ensure!(
        decoder.total_in() + 12 + header_len as u64 == file_len,
        "Snapshot contains trailing compressed bytes"
    );
    if !header.packed {
        return Ok(bytes);
    }
    let mut pixels = Vec::with_capacity(length);
    for byte in bytes {
        pixels.push((byte >> 4) * 17);
        if pixels.len() < length {
            pixels.push((byte & 15) * 17);
        }
    }
    Ok(pixels)
}

fn decode_bytes(bytes: &[u8], codec: &str, case: &Case) -> Result<Vec<u8>> {
    if codec == "png" {
        decode_png(Cursor::new(bytes), case)
    } else if codec.starts_with("zstd") {
        #[cfg(all(feature = "host-zstd", target_os = "linux"))]
        return zstd_snapshot::decode(bytes, case);
        #[cfg(not(all(feature = "host-zstd", target_os = "linux")))]
        bail!("Build the isolated Linux harness with --features host-zstd");
    } else {
        decode_raw(Cursor::new(bytes), bytes.len() as u64, case)
    }
}

fn read_encoded_file(mut file: File) -> Result<Vec<u8>> {
    let length = usize::try_from(file.metadata()?.len())?;
    ensure!(
        length > 0 && length <= imaging::MAX_PAGE_BYTES as usize,
        "Compressed snapshot file exceeds its byte budget"
    );
    let mut bytes = vec![0; length];
    file.read_exact(&mut bytes)?;
    let mut extra = [0; 1];
    ensure!(
        file.read(&mut extra)? == 0,
        "Compressed snapshot changed its length during reading"
    );
    Ok(bytes)
}

fn decode_file(file: File, codec: &str, case: &Case) -> Result<Vec<u8>> {
    if codec == "png" {
        decode_png(file, case)
    } else if codec.starts_with("zstd") {
        decode_bytes(&read_encoded_file(file)?, codec, case)
    } else {
        let length = file.metadata()?.len();
        decode_raw(BufReader::new(file), length, case)
    }
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary = path.with_extension("part");
    let mut file = OpenOptions::new()
        .write(true)
        .truncate(true)
        .create(true)
        .open(&temporary)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    fs::rename(&temporary, path)?;
    File::open(path.parent().context("Missing parent directory")?)?.sync_all()?;
    Ok(())
}

fn load_case(root: &Path, name: &str) -> Result<(PathBuf, Case)> {
    let directory = root.join(name);
    let case = serde_json::from_slice(&fs::read(directory.join("case.json"))?)?;
    Ok((directory, case))
}

fn prepare(root: &Path, source: &Path, jpeg_fixtures: &Path) -> Result<()> {
    prepare_for_codecs(root, source, jpeg_fixtures, &CODECS)
}

fn prepare_for_codecs(
    root: &Path,
    source: &Path,
    jpeg_fixtures: &Path,
    codecs: &[&str],
) -> Result<()> {
    fs::create_dir_all(root)?;
    let specs = [
        (
            "pw-demo-front",
            source.join("assets/demo/page-00.png"),
            1_236,
            1_648,
            false,
            0,
        ),
        (
            "scribe-demo-back",
            source.join("assets/demo/page-01.png"),
            1_860,
            2_480,
            false,
            0,
        ),
        (
            "pw-color-panels",
            jpeg_fixtures.join("color-panels.jpg"),
            1_236,
            1_648,
            false,
            0,
        ),
        (
            "scribe-gradient",
            jpeg_fixtures.join("high-quality-gradient.jpg"),
            1_860,
            2_480,
            false,
            0,
        ),
        (
            "pw-vertical",
            source.join("assets/demo/page-01.png"),
            1_236,
            1_648,
            true,
            80,
        ),
    ];
    let mut records = Vec::new();
    for (name, input, target_width, target_height, vertical, offset) in specs {
        let directory = root.join(name);
        fs::create_dir_all(&directory)?;
        let cache = cache::CacheManager::open(&directory)?;
        let mut page = domain::Page {
            url: format!("https://snapshot.example.invalid/{name}"),
            ..Default::default()
        };
        page.headers
            .insert("X-Fixture-Source".into(), "snapshot-evaluation-v1".into());
        let input = fs::read(input)?;
        let write = cache.begin_raw(&page, None, input.len() as u64)?;
        fs::write(write.temp_path(), &input)?;
        cache.validate_path(write.temp_path())?;
        let raw = write.commit()?;
        page.local_path = Some(raw.clone());
        let options = imaging::ImageOptions {
            width: target_width,
            height: target_height,
            crop_borders: false,
            dither: true,
            contrast: 0,
        };
        let rendered = render_cache::render(&cache, &page, &raw, &options, vertical, offset, 1.0)?;
        let mut png =
            png::Decoder::new(BufReader::new(File::open(&rendered.cache_path)?)).read_info()?;
        let png_metadata = png
            .info()
            .uncompressed_latin1_text
            .iter()
            .find(|value| value.keyword == "AidokuRender")
            .context("Production PNG metadata missing")?
            .text
            .clone();
        png.finish()?;
        let alias = format!("reader-snapshot-benchmark:{name}");
        cache.set_alias(&alias, &rendered.cache_path)?;
        let case = Case {
            name: name.into(),
            page,
            width: rendered.image.width(),
            height: rendered.image.height(),
            target_width,
            target_height,
            vertical,
            offset,
            scaled_height: rendered.scaled_height,
            viewport_height: rendered.viewport_height,
            png_path: rendered.cache_path.clone(),
            png_metadata,
            pixel_sha256: digest(rendered.image.as_raw()),
            source_sha256: digest(&input),
            alias,
        };
        fs::write(directory.join("pixels.bin"), rendered.image.as_raw())?;
        let mut sizes = serde_json::Map::new();
        for &codec in codecs {
            let bytes = encode(&case, rendered.image.as_raw(), codec)?;
            ensure!(
                decode_bytes(&bytes, codec, &case)? == *rendered.image.as_raw(),
                "Snapshot codec is not byte-exact: {codec}"
            );
            atomic_write(&codec_path(&directory, codec), &bytes)?;
            sizes.insert(codec.into(), json!(bytes.len()));
        }
        fs::write(
            directory.join("case.json"),
            serde_json::to_vec_pretty(&case)?,
        )?;
        records.push(json!({"name":name, "dimensions":[case.width,case.height], "pixels":rendered.image.as_raw().len(), "pixel_sha256":case.pixel_sha256, "source_sha256":case.source_sha256, "source_bytes":input.len(), "production_png_bytes":fs::metadata(&case.png_path)?.len(), "codec_bytes":sizes, "vertical":vertical}));
    }
    println!(
        "{}",
        json!({"cases":records, "oracle":"Exact production grayscale pixels, including dither and sixteen-level quantization", "build_profile":"release"})
    );
    Ok(())
}

fn reopen(root: &Path, name: &str, mode: &str) -> Result<()> {
    let (directory, case) = load_case(root, name)?;
    let codec_initialization = initialize_codec(mode)?;
    let mut stages = serde_json::Map::new();
    let mut retained_cache = None;
    let start = Instant::now();
    let pixels = if mode == "production" || mode == "validated-fingerprint" || mode == "alias" {
        let stage = Instant::now();
        let cache = cache::CacheManager::open(&directory)?;
        stages.insert("cache_open_us".into(), json!(stage.elapsed().as_micros()));
        let stage = Instant::now();
        let rendered = if mode != "alias" {
            let raw = case
                .page
                .local_path
                .as_ref()
                .context("Missing raw fixture path")?;
            if mode == "production" {
                cache.validate_path(raw)?;
                stages.insert(
                    "raw_validation_us".into(),
                    json!(stage.elapsed().as_micros()),
                );
            }
            let stage = Instant::now();
            let rendered = render_cache::render(
                &cache,
                &case.page,
                raw,
                &case.options(),
                case.vertical,
                case.offset,
                1.0,
            )?;
            stages.insert(
                "render_lookup_and_decode_us".into(),
                json!(stage.elapsed().as_micros()),
            );
            rendered
        } else {
            ensure!(
                !case.vertical,
                "The production cover alias API excludes vertical windows"
            );
            let rendered = render_cache::load_alias(&cache, &case.alias, &case.options())?
                .context("Alias recovery missed")?;
            stages.insert(
                "alias_lookup_and_decode_us".into(),
                json!(stage.elapsed().as_micros()),
            );
            rendered
        };
        ensure!(
            rendered.cache_hit && !rendered.memory_hit,
            "The fresh process did not use a persistent PNG"
        );
        retained_cache = Some(cache);
        rendered.image
    } else {
        let path = codec_path(&directory, mode);
        let file = File::open(&path)?;
        let pixels = decode_file(file, mode, &case)?;
        Arc::new(
            GrayImage::from_raw(case.width, case.height, pixels)
                .context("Invalid decoded dimensions")?,
        )
    };
    let elapsed = start.elapsed().as_micros();
    let pixel_sha256 = digest(pixels.as_raw());
    ensure!(
        pixel_sha256 == case.pixel_sha256,
        "Reopened pixels differ from the production oracle"
    );
    let start = Instant::now();
    let mut rgb = vec![0_u8; pixels.as_raw().len() * 3];
    for (output, value) in rgb.chunks_exact_mut(3).zip(pixels.as_raw()) {
        output.fill(*value);
    }
    let rgb_us = start.elapsed().as_micros();
    std::hint::black_box(rgb);
    println!(
        "{}",
        json!({"case":name,"mode":mode,"elapsed_us":elapsed,"rgb_expansion_us":rgb_us,"pixel_sha256":pixel_sha256,"stages":stages,"render_stats":render_cache::stats(),"codec_initialization":codec_initialization})
    );
    drop(retained_cache);
    Ok(())
}

fn write_sample(root: &Path, name: &str, codec: &str) -> Result<()> {
    let (directory, case) = load_case(root, name)?;
    let pixels = fs::read(directory.join("pixels.bin"))?;
    ensure!(
        digest(&pixels) == case.pixel_sha256,
        "The encoding oracle differs"
    );
    let codec_initialization = initialize_codec(codec)?;
    let start = Instant::now();
    let bytes = encode(&case, &pixels, codec)?;
    let encode_us = start.elapsed().as_micros();
    let stage = Instant::now();
    let decoded = decode_bytes(&bytes, codec, &case)?;
    let validate_us = stage.elapsed().as_micros();
    ensure!(decoded == pixels, "Encoded snapshot pixels differ");
    drop(decoded);
    let stage = Instant::now();
    atomic_write(&directory.join(format!("write-{codec}.bin")), &bytes)?;
    let durable_write_us = stage.elapsed().as_micros();
    println!(
        "{}",
        json!({"case":name,"codec":codec,"encode_us":encode_us,"validate_us":validate_us,"durable_write_us":durable_write_us,"total_us":start.elapsed().as_micros(),"bytes":bytes.len(),"codec_initialization":codec_initialization})
    );
    Ok(())
}

fn encode_sample(root: &Path, name: &str, codec: &str) -> Result<()> {
    let (directory, case) = load_case(root, name)?;
    let pixels = fs::read(directory.join("pixels.bin"))?;
    ensure!(
        digest(&pixels) == case.pixel_sha256,
        "The encoding oracle differs"
    );
    let codec_initialization = initialize_codec(codec)?;
    let start = Instant::now();
    let bytes = encode(&case, &pixels, codec)?;
    let encode_us = start.elapsed().as_micros();
    let check = Instant::now();
    ensure!(
        decode_bytes(&bytes, codec, &case)? == pixels,
        "Encoded snapshot pixels differ"
    );
    println!(
        "{}",
        json!({"case":name,"codec":codec,"encode_us":encode_us,"post_timing_oracle_us":check.elapsed().as_micros(),"bytes":bytes.len(),"codec_initialization":codec_initialization})
    );
    Ok(())
}

fn sync_write_sample(root: &Path, name: &str, codec: &str) -> Result<()> {
    let (directory, case) = load_case(root, name)?;
    let bytes = read_encoded_file(File::open(codec_path(&directory, codec))?)?;
    let codec_initialization = initialize_codec(codec)?;
    let pixels = decode_bytes(&bytes, codec, &case)?;
    ensure!(
        digest(&pixels) == case.pixel_sha256,
        "The preencoded write oracle differs"
    );
    drop(pixels);
    // The preencoded payload is fully checked before the independent I/O timer.
    let start = Instant::now();
    atomic_write(
        &directory.join(format!("independent-sync-{codec}.bin")),
        &bytes,
    )?;
    let sync_write_us = start.elapsed().as_micros();
    println!(
        "{}",
        json!({"case":name,"codec":codec,"sync_write_us":sync_write_us,"bytes":bytes.len(),"codec_initialization":codec_initialization})
    );
    Ok(())
}

fn audit(root: &Path) -> Result<()> {
    audit_for_codecs(root, &CODECS)
}

fn audit_for_codecs(root: &Path, codecs: &[&str]) -> Result<()> {
    let mut checked = 0;
    let mut zstd_checked = 0;
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        if !entry.path().join("case.json").is_file() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        let (directory, case) = load_case(root, &name)?;
        let oracle = fs::read(directory.join("pixels.bin"))?;
        for &codec in codecs {
            let bytes = fs::read(codec_path(&directory, codec))?;
            ensure!(
                decode_bytes(&bytes, codec, &case)? == oracle,
                "Codec differs from the oracle"
            );
            for length in [0, 7, 11, bytes.len() / 2, bytes.len() - 3] {
                ensure!(
                    decode_bytes(&bytes[..length], codec, &case).is_err(),
                    "A truncated snapshot was accepted: {codec}/{length}"
                );
            }
            let mut damaged = bytes.clone();
            let index = damaged.len() - 9;
            damaged[index] ^= 0x5a;
            ensure!(
                decode_bytes(&damaged, codec, &case).is_err(),
                "A corrupted snapshot was accepted"
            );
            if codec != "png" {
                let mut trailing = bytes.clone();
                trailing.push(1);
                ensure!(
                    decode_bytes(&trailing, codec, &case).is_err(),
                    "A trailing snapshot payload was accepted"
                );
                let mut wrong = case.clone();
                wrong.source_sha256 = "a different source".into();
                ensure!(
                    decode_bytes(&trailing[..trailing.len() - 1], codec, &wrong).is_err(),
                    "A different source identity was accepted"
                );
            }
            if codec.starts_with("zstd") {
                #[cfg(all(feature = "host-zstd", target_os = "linux"))]
                zstd_snapshot::audit_requirements(&bytes, &case, &oracle)?;
                zstd_checked += 1;
            }
            checked += 1;
        }
    }
    println!(
        "{}",
        json!({"checked_codecs":checked,"checks":{"byte_exact":true,"truncation_rejected":true,"corruption_rejected":true,"trailing_raw_payload_rejected":true,"wrong_source_rejected":true},"zstd_checked_codecs":zstd_checked,"zstd_checks":if zstd_checked > 0 {json!({"frame_checksum_required":true,"checksum_corruption_rejected":true,"exact_advertised_length_required":true,"concatenated_frames_rejected":true,"render_identity_required":true})} else {Value::Null}})
    );
    Ok(())
}

fn main() -> Result<()> {
    ensure!(
        !cfg!(debug_assertions),
        "Use a release build on the remote worker"
    );
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    let command = arguments.first().context("A command is required")?;
    if command == "zstd-info" {
        println!(
            "{}",
            initialize_codec("zstd1")?.context("System zstd metadata is missing")?
        );
        return Ok(());
    }
    let root = Path::new(
        arguments
            .get(1)
            .context("A fixture directory is required")?,
    );
    match command.as_str() {
        "prepare" => prepare(
            root,
            Path::new(
                arguments
                    .get(2)
                    .context("A production source directory is required")?,
            ),
            Path::new(
                arguments
                    .get(3)
                    .context("A JPEG fixture directory is required")?,
            ),
        ),
        "prepare-zstd" => {
            initialize_codec("zstd1")?;
            prepare_for_codecs(
                root,
                Path::new(
                    arguments
                        .get(2)
                        .context("A production source directory is required")?,
                ),
                Path::new(
                    arguments
                        .get(3)
                        .context("A JPEG fixture directory is required")?,
                ),
                &ZSTD_CODECS,
            )
        }
        "reopen" => reopen(
            root,
            arguments.get(2).context("A case is required")?,
            arguments
                .get(3)
                .context("A codec or production mode is required")?,
        ),
        "write" => write_sample(
            root,
            arguments.get(2).context("A case is required")?,
            arguments.get(3).context("A codec is required")?,
        ),
        "encode-only" => encode_sample(
            root,
            arguments.get(2).context("A case is required")?,
            arguments.get(3).context("A codec is required")?,
        ),
        "sync-write" => sync_write_sample(
            root,
            arguments.get(2).context("A case is required")?,
            arguments.get(3).context("A codec is required")?,
        ),
        "audit" => audit(root),
        "audit-zstd" => audit_for_codecs(root, &ZSTD_CODECS),
        _ => bail!("Unknown command: {command}"),
    }
}
