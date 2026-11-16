//! Compare spatial grayscale representations without changing Reader defaults.
//! Run only on test-env or an explicitly owned physical-device research batch.

use std::{fs, hint::black_box, path::PathBuf, time::Instant};

use aidoku_kindle::{imaging, native_media::Acceleration};
use anyhow::{Context, Result, ensure};
use image::GrayImage;
use serde_json::json;

const fn bayer8_rank(x: usize, y: usize) -> u16 {
    let mut rank = 0;
    let mut bit = 0;
    while bit < 3 {
        let a = (x >> bit) & 1;
        let b = (y >> bit) & 1;
        rank = rank * 4 + [0, 2, 3, 1][b * 2 + a];
        bit += 1;
    }
    rank
}

const fn bayer8_tables() -> [[u8; 256]; 64] {
    let mut tables = [[0; 256]; 64];
    let mut phase = 0;
    while phase < 64 {
        let rank = bayer8_rank(phase % 8, phase / 8) as usize;
        let mut value = 0;
        while value < 256 {
            let upper_count = ((value % 17) * 64 + 8) / 17;
            let level = value / 17 + if rank < upper_count { 1 } else { 0 };
            tables[phase][value] = ((if level > 15 { 15 } else { level }) * 17) as u8;
            value += 1;
        }
        phase += 1;
    }
    tables
}

fn ordered8(image: &mut GrayImage) {
    const TABLES: [[u8; 256]; 64] = bayer8_tables();
    let width = image.width() as usize;
    for (y, row) in image.as_mut().chunks_exact_mut(width).enumerate() {
        for (x, value) in row.iter_mut().enumerate() {
            *value = TABLES[(y % 8) * 8 + x % 8][usize::from(*value)];
        }
    }
}

fn diffuse_q8(image: &mut GrayImage) {
    let width = image.width() as usize;
    let mut current = vec![0_i32; width + 2];
    let mut next = vec![0_i32; width + 2];
    for (y, row) in image.as_mut().chunks_exact_mut(width).enumerate() {
        let reverse = y % 2 != 0;
        for position in 0..width {
            let x = if reverse {
                width - 1 - position
            } else {
                position
            };
            let value = i32::from(row[x]) * 256 + current[x + 1];
            let quantized = ((value + 2176).div_euclid(4352)).clamp(0, 15) * 17;
            row[x] = quantized as u8;
            let error = value - quantized * 256;
            let right = error * 7 / 16;
            let below_left = error * 3 / 16;
            let below = error * 5 / 16;
            // Preserve fractional error instead of truncating four shares independently.
            let below_right = error - right - below_left - below;
            if reverse {
                current[x] += right;
                next[x + 2] += below_left;
                next[x + 1] += below;
                next[x] += below_right;
            } else {
                current[x + 2] += right;
                next[x] += below_left;
                next[x + 1] += below;
                next[x + 2] += below_right;
            }
        }
        std::mem::swap(&mut current, &mut next);
        next.fill(0);
    }
}

fn process(bytes: &[u8], width: u32, height: u32, method: &str) -> Result<GrayImage> {
    let mut options = imaging::ImageOptions {
        media_acceleration: Acceleration::Off,
        width,
        height,
        crop_borders: false,
        dither: true,
        hardware_dither: false,
        spatial_dither: None,
        contrast: 0,
    };
    match method {
        "current_bayer4" => imaging::decode_reader_raster(bytes, &options),
        "current_fs_q4" => imaging::decode_page(bytes, &options),
        "production_off" | "production_bayer8" | "production_fs_q8" => {
            options.spatial_dither = Some(match method {
                "production_off" => imaging::SpatialDither::Off,
                "production_bayer8" => imaging::SpatialDither::Bayer8,
                _ => imaging::SpatialDither::FloydSteinberg,
            });
            imaging::decode_reader_raster(bytes, &options)
        }
        "bayer8" | "fs_q8" | "gray8" => {
            // This flag only preserves CPU-decoded Gray8 here; no platform is initialized.
            options.hardware_dither = true;
            let mut image = imaging::decode_reader_raster(bytes, &options)?;
            match method {
                "bayer8" => ordered8(&mut image),
                "fs_q8" => diffuse_q8(&mut image),
                _ => (),
            }
            Ok(image)
        }
        _ => anyhow::bail!("Unknown research method"),
    }
}

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    ensure!(
        args.len() == 6,
        "Usage: spatial-dither-research INPUT WIDTH HEIGHT OUTPUT SAMPLES"
    );
    let bytes = fs::read(&args[1]).context("Read the synthetic input")?;
    let width: u32 = args[2].parse()?;
    let height: u32 = args[3].parse()?;
    let samples: usize = args[5].parse()?;
    ensure!(width > 0 && height > 0 && u64::from(width) * u64::from(height) <= 6 * 1024 * 1024);
    ensure!((1..=15).contains(&samples));
    let output = PathBuf::from(&args[4]);
    ensure!(!output.exists(), "Use a fresh output directory");
    fs::create_dir_all(&output)?;
    let methods = [
        "gray8",
        "current_bayer4",
        "current_fs_q4",
        "bayer8",
        "fs_q8",
        "production_off",
        "production_bayer8",
        "production_fs_q8",
    ];
    let mut times = vec![Vec::new(); methods.len()];
    for iteration in 0..=samples {
        let order: Vec<_> = if iteration % 2 == 0 {
            (0..methods.len()).collect()
        } else {
            (0..methods.len()).rev().collect()
        };
        for index in order {
            let started = Instant::now();
            let image = process(&bytes, width, height, methods[index])?;
            black_box(image.as_raw());
            let ms = started.elapsed().as_secs_f64() * 1000.0;
            if iteration == 0 {
                ensure!(image.dimensions() == (width, height));
                if methods[index] != "gray8" {
                    ensure!(image.as_raw().iter().all(|value| value % 17 == 0));
                }
                fs::write(
                    output.join(format!("{}.raw", methods[index])),
                    image.as_raw(),
                )?;
            } else {
                times[index].push(ms);
            }
        }
    }
    for (production, prototype) in [("production_bayer8", "bayer8"), ("production_fs_q8", "fs_q8")] {
        ensure!(fs::read(output.join(format!("{production}.raw")))? == fs::read(output.join(format!("{prototype}.raw")))?,
            "Production output differs from the qualified prototype: {production}");
    }
    let records: Vec<_> = methods.iter().enumerate().map(|(index, method)| {
        let mut ordered = times[index].clone();
        ordered.sort_by(f64::total_cmp);
        json!({"method": method, "samples_ms": times[index], "median_ms": ordered[ordered.len()/2]})
    }).collect();
    let report = json!({"width": width, "height": height, "warmups": 1, "samples": samples,
        "results": records, "scope": "Resident PNG input; CPU decode, identity geometry, allocation and dithering. File I/O excluded. No native display or codec device opened."});
    fs::write(
        output.join("timings.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    println!("{report}");
    Ok(())
}
