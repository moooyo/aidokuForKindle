//! Measure the production Reader pipeline and scaler on the leased Scribe.
//! Input I/O, output I/O, and output hashing are outside compute measurements.

use std::{
    fs,
    hint::black_box,
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};

use aidoku_kindle::imaging::{self, ImageOptions};
use anyhow::{Context, Result, ensure};
use clap::{Parser, ValueEnum};
use serde_json::json;
use sha2::{Digest, Sha256};

pub use aidoku_kindle::imaging::check_decode_cancelled;

// Include the exact production implementation instead of a substitute filter.
#[allow(dead_code)]
#[path = "../src/imaging/reader_raster.rs"]
mod reader_raster;

#[derive(Clone, Copy, Debug, ValueEnum)]
enum Mode {
    Scale,
    Pipeline,
    Suite,
}

#[derive(Parser, Debug)]
struct Arguments {
    #[arg(long, value_enum)]
    mode: Mode,
    #[arg(long)]
    input: PathBuf,
    #[arg(long)]
    source_width: u32,
    #[arg(long)]
    source_height: u32,
    #[arg(long)]
    width: u32,
    #[arg(long)]
    height: u32,
    #[arg(long)]
    output: Option<PathBuf>,
    #[arg(long)]
    hardware: Option<PathBuf>,
    #[arg(long, default_value_t = 7)]
    samples: usize,
}

static STOP: AtomicBool = AtomicBool::new(false);

extern "C" fn request_stop(_: libc::c_int) {
    STOP.store(true, Ordering::Relaxed);
}

fn checksum(pixels: &[u8]) -> u32 {
    pixels.iter().fold(2_166_136_261_u32, |value, pixel| {
        (value ^ u32::from(*pixel)).wrapping_mul(16_777_619)
    })
}

fn hardware(
    args: &Arguments,
    directory: &Path,
    no_output: bool,
) -> Result<(serde_json::Value, f64)> {
    ensure!(!STOP.load(Ordering::Relaxed), "Benchmark was interrupted");
    let binary = args
        .hardware
        .as_ref()
        .context("The hardware probe is required")?;
    let prefix = directory.join("hardware-reference");
    let mut command = Command::new(binary);
    command
        .env("AIDOKU_REVIEWED_CODEC_TRIAL", "1")
        .args(["--reviewed-single-frame", "jpeg"])
        .arg(&args.input)
        .arg(&prefix)
        .args([
            args.source_width.to_string(),
            args.source_height.to_string(),
            "gray".to_owned(),
            args.width.to_string(),
            args.height.to_string(),
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if no_output {
        command.arg("--no-output");
    }
    let started = Instant::now();
    let mut child = command.spawn().context("Start the owned hardware probe")?;
    let pid = child.id();
    let stat = fs::read_to_string(format!("/proc/{pid}/stat"))?;
    let birth = stat
        .rsplit_once(") ")
        .context("Read the child process identity")?
        .1
        .split_whitespace()
        .nth(19)
        .context("Read the child start time")?;
    let active = directory.join("active-child.json");
    fs::write(
        &active,
        serde_json::to_vec(&json!({
            "pid": pid, "birth": birth, "executable": binary,
        }))?,
    )?;
    let mut termination_sent = false;
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if (STOP.load(Ordering::Relaxed) || started.elapsed() > Duration::from_secs(20))
            && !termination_sent
        {
            // This direct child is unreaped, so its PID cannot be reused here.
            unsafe {
                libc::kill(pid as libc::pid_t, libc::SIGTERM);
            }
            termination_sent = true;
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    let mut stdout = String::new();
    let mut stderr = String::new();
    child
        .stdout
        .take()
        .context("Read probe output")?
        .read_to_string(&mut stdout)?;
    child
        .stderr
        .take()
        .context("Read probe diagnostics")?
        .read_to_string(&mut stderr)?;
    fs::remove_file(active)?;
    let wall_ms = started.elapsed().as_secs_f64() * 1000.0;
    ensure!(
        status.success() && !termination_sent,
        "Hardware probe failed: {stderr}"
    );
    let result: serde_json::Value = serde_json::from_str(&stdout)?;
    ensure!(
        result["ioctl_completed"] == true && result["cleanup_errno"] == 0,
        "The hardware operation did not complete normally"
    );
    ensure!(result["width"] == args.width && result["height"] == args.height);
    ensure!(result["benchmark_no_output"] == no_output);
    Ok((result, wall_ms))
}

fn suite(args: &Arguments, bytes: &[u8], options: &ImageOptions) -> Result<()> {
    ensure!(
        (3..=21).contains(&args.samples),
        "Use three through twenty-one samples"
    );
    let directory = args
        .output
        .as_ref()
        .context("The suite output directory is required")?;
    fs::create_dir_all(directory)?;
    unsafe {
        libc::signal(libc::SIGTERM, request_stop as *const () as libc::sighandler_t);
        libc::signal(libc::SIGINT, request_stop as *const () as libc::sighandler_t);
        libc::signal(libc::SIGHUP, request_stop as *const () as libc::sighandler_t);
    }
    let source = imaging::decode_reader_raster(
        bytes,
        &ImageOptions {
            width: args.source_width,
            height: args.source_height,
            ..options.clone()
        },
    )?;
    ensure!(source.dimensions() == (args.source_width, args.source_height));
    let cpu = imaging::decode_reader_raster(bytes, options)?;
    let isolated = reader_raster::resize_gray(&source, args.width, args.height)?;
    ensure!(
        cpu.as_raw() == isolated.as_raw(),
        "The isolated scaler differs from the actual Reader pipeline"
    );
    let cpu_hash = format!("{:x}", Sha256::digest(cpu.as_raw()));
    fs::write(directory.join("cpu.gray8"), cpu.as_raw())?;
    let (reference, _) = hardware(args, directory, false)?;
    fs::write(
        directory.join("hardware-reference.json"),
        serde_json::to_vec_pretty(&reference)?,
    )?;
    let raw = fs::read(directory.join("hardware-reference.plane0.bin"))?;
    let stride = reference["planes"][0]["stride"]
        .as_u64()
        .context("Read hardware stride")? as usize;
    ensure!(stride >= args.width as usize && raw.len() >= stride * args.height as usize);
    let normalized: Vec<u8> = (0..args.height as usize)
        .flat_map(|row| {
            raw[row * stride..row * stride + args.width as usize]
                .iter()
                .copied()
        })
        .collect();
    let expected_checksum = checksum(&normalized);
    let mut absolute = 0_u64;
    let mut squared = 0_u64;
    let mut maximum = 0_u8;
    let mut over_four = 0_u64;
    for (first, second) in cpu.as_raw().iter().zip(&normalized) {
        let difference = first.abs_diff(*second);
        absolute += u64::from(difference);
        squared += u64::from(difference).pow(2);
        maximum = maximum.max(difference);
        over_four += u64::from(difference > 4);
    }
    let length = normalized.len() as f64;
    let mae = absolute as f64 / length;
    let mse = squared as f64 / length;
    let psnr = if mse == 0.0 {
        None
    } else {
        Some(10.0 * (255.0_f64.powi(2) / mse).log10())
    };
    let eligible = mae <= 1.0 && psnr.is_none_or(|value| value >= 40.0);
    let quality = json!({
        "mae": mae, "max_error": maximum, "psnr_db": psnr,
        "pixels_over_4": over_four, "pixel_count": normalized.len(),
        "cpu_output_sha256": cpu_hash,
        "hardware_output_sha256": format!("{:x}", Sha256::digest(&normalized)),
        "hardware_checksum": expected_checksum,
        "benchmark_eligible": eligible,
        "threshold": "This scene only: MAE <= 1 and PSNR >= 40 dB; different filters are not bit-exact.",
    });
    fs::write(
        directory.join("quality.json"),
        serde_json::to_vec_pretty(&quality)?,
    )?;
    ensure!(
        eligible,
        "Output quality did not meet the declared benchmark gate"
    );
    let mut log = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(directory.join("samples.jsonl"))?;
    for sample in 0..=args.samples {
        let modes = if sample % 2 == 0 {
            [0, 1, 2]
        } else {
            [2, 1, 0]
        };
        for mode in modes {
            ensure!(!STOP.load(Ordering::Relaxed), "Benchmark was interrupted");
            let record = if mode == 2 {
                let (value, wall_ms) = hardware(args, directory, true)?;
                ensure!(
                    value["normalized_checksum"].as_u64() == Some(u64::from(expected_checksum)),
                    "A repeated hardware output differed from the qualified frame"
                );
                json!({"mode": "hardware_decode_scale", "compute_ms": value["compute_ms"],
                    "decode_scale_ms": value["decode_ms"], "helper_total_ms": value["total_ms"],
                    "child_wall_ms": wall_ms, "output_checksum": expected_checksum})
            } else {
                let began = Instant::now();
                let result = if mode == 0 {
                    reader_raster::resize_gray(&source, args.width, args.height)?
                } else {
                    imaging::decode_reader_raster(bytes, options)?
                };
                black_box(&result);
                let elapsed = began.elapsed().as_secs_f64() * 1000.0;
                let digest = format!("{:x}", Sha256::digest(result.as_raw()));
                ensure!(digest == cpu_hash, "A repeated CPU output changed");
                json!({"mode": if mode == 0 { "cpu_scale" } else { "cpu_decode_scale" },
                    "compute_ms": elapsed, "output_sha256": digest})
            };
            let record = json!({"iteration": sample, "warmup": sample == 0,
                "width": args.width, "height": args.height, "result": record});
            writeln!(log, "{record}")?;
            log.flush()?;
        }
    }
    let summary = json!({"completed": true, "samples_per_mode": args.samples,
        "warmups_per_mode": 1, "source_width": args.source_width, "source_height": args.source_height,
        "width": args.width, "height": args.height, "quality": quality,
        "input_sha256": format!("{:x}", Sha256::digest(bytes)),
        "scaler_source_sha256": format!("{:x}", Sha256::digest(include_bytes!("../src/imaging/reader_raster.rs"))),
        "scope": "Quiet native UI; resident input; CPU production Triangle versus JPEG/IMGRZ. Cold hardware contexts include setup, owned Gray8 normalization, and cleanup; no pixel file I/O or hashing in measured samples."});
    fs::write(
        directory.join("report.json"),
        serde_json::to_vec_pretty(&summary)?,
    )?;
    println!("{summary}");
    Ok(())
}

fn main() -> Result<()> {
    let started = Instant::now();
    let args = Arguments::parse();
    ensure!(cfg!(target_os = "linux"), "Run through ssh test-env");
    for value in [
        args.source_width,
        args.source_height,
        args.width,
        args.height,
    ] {
        ensure!(
            (16..=4096).contains(&value),
            "Unsupported benchmark dimensions"
        );
    }
    let bytes = fs::read(&args.input).context("Read the prepared benchmark input")?;
    let options = ImageOptions {
        width: args.width,
        height: args.height,
        crop_borders: false,
        dither: false,
        hardware_dither: true,
        contrast: 0,
    };
    if matches!(args.mode, Mode::Suite) {
        return suite(&args, &bytes, &options);
    }
    let decode_started = Instant::now();
    let source = if matches!(args.mode, Mode::Scale) {
        let source = imaging::decode_reader_raster(
            &bytes,
            &ImageOptions {
                width: args.source_width,
                height: args.source_height,
                ..options.clone()
            },
        )?;
        ensure!(source.dimensions() == (args.source_width, args.source_height));
        Some(source)
    } else {
        None
    };
    let source_prepare_ms = decode_started.elapsed().as_secs_f64() * 1000.0;
    let compute_started = Instant::now();
    let output = match source {
        Some(ref source) => reader_raster::resize_gray(source, args.width, args.height)?,
        None => imaging::decode_reader_raster(&bytes, &options)?,
    };
    black_box(&output);
    let compute_ms = compute_started.elapsed().as_secs_f64() * 1000.0;
    ensure!(output.dimensions() == (args.width, args.height));
    let digest = format!("{:x}", Sha256::digest(output.as_raw()));
    if let Some(path) = args.output {
        fs::write(path, output.as_raw()).context("Write benchmark pixels")?;
    }
    println!(
        "{}",
        json!({
            "schema": 1,
            "mode": match args.mode { Mode::Scale => "cpu_scale", Mode::Pipeline => "cpu_decode_scale", Mode::Suite => unreachable!() },
            "source_width": args.source_width,
            "source_height": args.source_height,
            "width": args.width,
            "height": args.height,
            "compute_ms": compute_ms,
            "source_prepare_ms": source_prepare_ms,
            "total_ms": started.elapsed().as_secs_f64() * 1000.0,
            "output_sha256": digest,
            "input_sha256": format!("{:x}", Sha256::digest(&bytes)),
            "scaler": "production_reader_triangle",
            "crop_borders": false,
            "quantization": false,
        })
    );
    Ok(())
}
