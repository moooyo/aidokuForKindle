//! Exercise the production Reader API, including the isolated worker protocol.
//! Device execution requires a prepared, exclusively owned physical batch.

use aidoku_kindle::{
    imaging::{self, ImageOptions, ReaderFit},
    native_media::{self, Acceleration},
};
use anyhow::{Context, Result, ensure};
use clap::Parser;
use image::GrayImage;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{fs, path::PathBuf, time::Instant};

#[derive(Parser)]
struct Arguments {
    #[arg(long)]
    input: PathBuf,
    #[arg(long)]
    output: PathBuf,
    #[arg(long)]
    width: u32,
    #[arg(long)]
    height: u32,
    #[arg(long, default_value = "page")]
    fit: String,
    #[arg(long)]
    crop: bool,
    #[arg(long)]
    native_opt_in: bool,
    #[arg(long)]
    expect_software: bool,
    #[arg(long)]
    include_decode_only: bool,
    #[arg(long, default_value_t = 7)]
    samples: usize,
}

fn digest(image: &GrayImage) -> String {
    format!("{:x}", Sha256::digest(image.as_raw()))
}

fn quality(actual: &GrayImage, reference: &GrayImage) -> serde_json::Value {
    let mut total = 0_u64;
    let mut square = 0_u64;
    let mut max = 0;
    let mut over = 0;
    for (&a, &b) in actual.as_raw().iter().zip(reference.as_raw()) {
        let d = a.abs_diff(b);
        total += u64::from(d);
        square += u64::from(d) * u64::from(d);
        max = max.max(d);
        over += usize::from(d > 4);
    }
    let n = actual.as_raw().len() as f64;
    let mae = total as f64 / n;
    let psnr = (square != 0).then(|| 10.0 * (255.0 * 255.0 / (square as f64 / n)).log10());
    json!({"mae":mae,"max_error":max,"psnr_db":psnr,"pixels_over_4":over,
        "pixel_count":actual.as_raw().len(),"exact":actual.as_raw()==reference.as_raw(),
        "comparison_gate_passed":mae<=1.0 && psnr.is_none_or(|p|p>=40.0)})
}

fn main() -> Result<()> {
    let args = Arguments::parse();
    ensure!(
        (1..=21).contains(&args.samples),
        "Use one through twenty-one samples"
    );
    let status = native_media::initialize(args.native_opt_in);
    if args.native_opt_in {
        ensure!(
            status.available(),
            "Hardware admission failed: {}",
            status.reason()
        );
    }
    let bytes = fs::read(&args.input).context("Read the prepared input")?;
    fs::create_dir_all(&args.output)?;
    let policies = if args.native_opt_in && args.include_decode_only {
        vec![
            Acceleration::Off,
            Acceleration::Decode,
            Acceleration::DecodeAndResize,
        ]
    } else if args.native_opt_in {
        vec![Acceleration::Off, Acceleration::DecodeAndResize]
    } else {
        vec![Acceleration::Off]
    };
    let fit = ReaderFit::from_key(&args.fit);
    let options = ImageOptions {
        width: args.width,
        height: args.height,
        crop_borders: args.crop,
        dither: false,
        hardware_dither: true,
        spatial_dither: None,
        contrast: 0,
        media_acceleration: Acceleration::Off,
    };
    let mut references = Vec::new();
    let mut timing: Vec<Vec<f64>> = policies.iter().map(|_| Vec::new()).collect();
    let mut observations: Vec<Vec<serde_json::Value>> =
        policies.iter().map(|_| Vec::new()).collect();
    for (i, &policy) in policies.iter().enumerate() {
        let before = native_media::statistics();
        let image = imaging::decode_fitted_reader_raster(
            &bytes,
            &ImageOptions {
                media_acceleration: policy,
                ..options.clone()
            },
            fit,
        )?;
        let after = native_media::statistics();
        ensure!(image.dimensions() == (args.width, args.height));
        if policy != Acceleration::Off {
            if args.expect_software {
                ensure!(
                    after.attempts == before.attempts,
                    "An excluded input reached the hardware worker"
                );
            } else {
                ensure!(
                    after.completed > before.completed,
                    "The supposedly accelerated image used only software"
                );
            }
        }
        fs::write(args.output.join(format!("mode-{i}.gray8")), image.as_raw())?;
        references.push(image);
    }
    for sample in 0..args.samples {
        for offset in 0..policies.len() {
            let index = (sample + offset) % policies.len();
            let before = native_media::statistics();
            let started = Instant::now();
            let image = imaging::decode_fitted_reader_raster(
                &bytes,
                &ImageOptions {
                    media_acceleration: policies[index],
                    ..options.clone()
                },
                fit,
            )?;
            let elapsed = started.elapsed().as_secs_f64() * 1000.0;
            let after = native_media::statistics();
            ensure!(
                image.as_raw() == references[index].as_raw(),
                "A repeated Reader output changed"
            );
            timing[index].push(elapsed);
            observations[index].push(json!({"attempts":after.attempts-before.attempts,
                "completed":after.completed-before.completed,"hardware_resizes":after.resized-before.resized}));
        }
    }
    let rows: Vec<_> = policies
        .iter()
        .enumerate()
        .map(|(index, policy)| {
            let mut sorted = timing[index].clone();
            sorted.sort_by(|a, b| a.total_cmp(b));
            let median = if sorted.len() % 2 == 1 {
                sorted[sorted.len() / 2]
            } else {
                (sorted[sorted.len() / 2 - 1] + sorted[sorted.len() / 2]) / 2.0
            };
            json!({"policy":policy,"median_ms":median,"samples_ms":timing[index],
            "observed_operations":observations[index],"sha256":digest(&references[index]),
            "quality":quality(&references[index],&references[0])})
        })
        .collect();
    let report = json!({"schema":1,"native_opt_in":args.native_opt_in,"availability":status.reason(),
        "input_sha256":format!("{:x}",Sha256::digest(&bytes)),"target":[args.width,args.height],
        "fit":args.fit,"crop_borders":args.crop,"samples_per_policy":args.samples,"warmups_per_policy":1,
        "results":rows,"final_availability":native_media::availability().reason(),
        "scope":"Actual production fitted Reader API. Includes admission, entropy verification, process startup, IPC, DMA staging, driver lifecycle, normalization, crop/resize and canvas allocation. Resident input; file I/O and hashing excluded. No display waveform timing."});
    fs::write(
        args.output.join("result.json"),
        serde_json::to_vec_pretty(&report)?,
    )?;
    println!("{report}");
    Ok(())
}
