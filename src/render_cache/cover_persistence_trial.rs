use super::*;
use flate2::read::ZlibDecoder;
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    env,
    os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt},
    process::Command,
    time::Instant,
};

const IMAGE_COUNT: usize = 6;
const PAIR_COUNT: usize = 10;
const GRID_WIDTH: u32 = 376;
const GRID_HEIGHT: u32 = 514;

struct Input {
    page: RenderedPage,
    metadata: Metadata,
    key: RenderKey,
}

#[derive(Clone, Copy)]
enum Arm {
    Control,
    NoFilter,
}

#[derive(Default, Serialize)]
struct Visit {
    reservation_ns: u64,
    encode_ns: u64,
    validation_ns: u64,
    commit_ns: u64,
    remember_ns: u64,
    total_ns: u64,
    total_ms: f64,
    reopen_ns: u64,
    reopen_ms: f64,
    png_bytes: [u64; IMAGE_COUNT],
}

fn load_inputs(directory: &Path) -> Result<Vec<Input>> {
    let options = ImageOptions {
        width: GRID_WIDTH,
        height: GRID_HEIGHT,
        crop_borders: false,
        dither: true,
        hardware_dither: false,
        spatial_dither: None,
        media_acceleration: Default::default(),
        contrast: 0,
    };
    let mut keys = HashSet::new();
    let mut inputs = Vec::with_capacity(IMAGE_COUNT);
    for ordinal in 1..=IMAGE_COUNT {
        let path = directory.join(format!("{ordinal:02}.png"));
        ensure!(
            fs::symlink_metadata(&path)?.is_file(),
            "Trial input {ordinal} must be a regular file"
        );
        let (reader, metadata) = processed_reader(&path, None)
            .with_context(|| format!("Read production metadata for input {ordinal}"))?;
        let key = metadata
            .render_key
            .clone()
            .context("The trial input has no complete rendering key")?;
        ensure!(
            !key.source_key.is_empty()
                && key == viewport_key(key.source_key.clone(), &options, false, 0, 1_000)
                && keys.insert(key.clone()),
            "Trial input {ordinal} does not have a distinct ordinary grid rendering key"
        );
        let page = read_processed_frame(&path, reader, metadata.clone())
            .with_context(|| format!("Decode trial input {ordinal}"))?;
        ensure!(
            page.image.width() == GRID_WIDTH || page.image.height() == GRID_HEIGHT,
            "Trial input {ordinal} is not fitted to the grid dimensions"
        );
        inputs.push(Input {
            page,
            metadata,
            key,
        });
    }
    Ok(inputs)
}

fn elapsed_ns(start: Instant, end: Instant) -> u64 {
    end.duration_since(start).as_nanos() as u64
}

fn check_cache(cache: &CacheManager, entries: usize, commits: usize) -> Result<()> {
    let stats = cache.stats();
    ensure!(
        stats.entries == entries as u64
            && stats.processed_hits == entries as u64
            && stats.commits == commits as u64
            && stats.corruptions == 0
            && stats.pinned_entries == 0
            && stats.pinned_bytes == 0
            && stats.leased_entries == 0
            && stats.writes_inflight == 0
            && stats.reserved_bytes == 0
            && fs::read_dir(cache.namespace().join("temp"))?.next().is_none(),
        "The trial cache did not have the expected durable entries and idle resources"
    );
    Ok(())
}

fn persist_visit(
    cache: CacheManager,
    inputs: &[Input],
    arm: Arm,
) -> Result<(Visit, Vec<PathBuf>)> {
    check_cache(&cache, 0, 0)?;
    let mut visit = Visit::default();
    let mut paths = Vec::with_capacity(IMAGE_COUNT);
    for input in inputs {
        let key = DecodedKey {
            namespace: cache.namespace().to_path_buf(),
            render: input.key.clone(),
        };
        let started = Instant::now();
        let (path, reserved, encoded, validated, committed) =
            imaging::without_decode_cancellation(|| {
                let mut write = cache.begin_processed(&input.key, 0)?;
                let reserved = Instant::now();
                match arm {
                    Arm::Control => encode(&mut write, &input.page.image, &input.metadata)?,
                    Arm::NoFilter => encode_with_policy(
                        &mut write,
                        &input.page.image,
                        &input.metadata,
                        PngEncoding::NoFilter,
                    )?,
                }
                let encoded = Instant::now();
                validate_processed(write.temp_path(), &input.key, input.page.image.dimensions())?;
                let validated = Instant::now();
                imaging::check_decode_cancelled()?;
                let path = write.commit()?;
                Ok((path, reserved, encoded, validated, Instant::now()))
            })?;
        let page = RenderedPage {
            image: input.page.image.clone(),
            cache_path: path.clone(),
            scaled_height: input.metadata.scaled_height,
            viewport_height: input.metadata.viewport_height,
            cache_hit: false,
            memory_hit: false,
            source_proof: None,
            retained_raster: None,
        };
        remember(&cache, key, &page, RenderPurpose::LocalCover);
        let finished = Instant::now();
        visit.reservation_ns += elapsed_ns(started, reserved);
        visit.encode_ns += elapsed_ns(reserved, encoded);
        visit.validation_ns += elapsed_ns(encoded, validated);
        visit.commit_ns += elapsed_ns(validated, committed);
        visit.remember_ns += elapsed_ns(committed, finished);
        visit.total_ns += elapsed_ns(started, finished);
        paths.push(path);
    }
    visit.total_ms = visit.total_ns as f64 / 1_000_000.0;
    check_cache(&cache, IMAGE_COUNT, IMAGE_COUNT)?;
    let remembered = decoded(|state| {
        inputs.iter().zip(&paths).all(|(input, path)| {
            let key = DecodedKey {
                namespace: cache.namespace().to_path_buf(),
                render: input.key.clone(),
            };
            state.entries.get(&key).is_some_and(|entry| {
                &entry.page.cache_path == path && Arc::ptr_eq(&entry.page.image, &input.page.image)
            })
        })
    });
    clear_memory(&cache);
    ensure!(remembered, "A committed trial image was not remembered");
    for (bytes, path) in visit.png_bytes.iter_mut().zip(&paths) {
        *bytes = fs::metadata(path)?.len();
    }
    Ok((visit, paths))
}

fn reopen_visit(
    directory: &Path,
    inputs: &[Input],
    mut visit: Visit,
    paths: &[PathBuf],
) -> Result<Visit> {
    let cache = CacheManager::open(directory)?;
    clear_memory(&cache);
    let decodes_before = RENDER_WORK.with(|work| work.get().processed_decodes);
    let mut restored = Vec::with_capacity(IMAGE_COUNT);
    for input in inputs {
        let key = DecodedKey {
            namespace: cache.namespace().to_path_buf(),
            render: input.key.clone(),
        };
        let started = Instant::now();
        let page = lookup(&cache, &key, RenderPurpose::LocalCover)?;
        visit.reopen_ns += elapsed_ns(started, Instant::now());
        restored.push(page.context("A reopened trial image missed its disk cache")?);
    }
    visit.reopen_ms = visit.reopen_ns as f64 / 1_000_000.0;
    clear_memory(&cache);
    check_cache(&cache, IMAGE_COUNT, 0)?;
    ensure!(
        RENDER_WORK.with(|work| work.get().processed_decodes) - decodes_before == IMAGE_COUNT as u64,
        "The trial did not decode every reopened image from disk"
    );
    for ((input, path), page) in inputs.iter().zip(paths).zip(restored) {
        let (_, metadata) = processed_reader(path, None)?;
        ensure!(
            page.cache_hit
                && !page.memory_hit
                && &page.cache_path == path
                && metadata == input.metadata
                && page.image == input.page.image,
            "A reopened trial image differs from the original metadata or grayscale pixels"
        );
    }
    Ok(visit)
}

fn count_png_filters(inputs: &[Input], paths: &[PathBuf]) -> Result<[u64; 5]> {
    let mut counts = [0; 5];
    for (input, path) in inputs.iter().zip(paths) {
        let mut png = Vec::new();
        File::open(path)?
            .take(MAX_PAGE_BYTES + 1)
            .read_to_end(&mut png)?;
        ensure!(png.len() as u64 <= MAX_PAGE_BYTES, "Oversized warmup PNG");
        let mut chunks = png
            .strip_prefix(b"\x89PNG\r\n\x1a\n")
            .context("Invalid warmup PNG signature")?;
        let mut compressed = Vec::new();
        loop {
            ensure!(chunks.len() >= 12, "Truncated warmup PNG chunk");
            let size = u32::from_be_bytes(chunks[..4].try_into()?) as usize;
            ensure!(size <= chunks.len() - 12, "Invalid warmup PNG chunk length");
            match &chunks[4..8] {
                b"IDAT" => compressed.extend_from_slice(&chunks[8..8 + size]),
                b"IEND" => break,
                _ => {}
            }
            chunks = &chunks[12 + size..];
        }
        let row_bytes = input.page.image.width() as usize + 1;
        let expected = input.page.image.height() as usize * row_bytes;
        let mut scanlines = Vec::with_capacity(expected);
        ZlibDecoder::new(compressed.as_slice())
            .take(expected as u64 + 1)
            .read_to_end(&mut scanlines)?;
        ensure!(
            scanlines.len() == expected,
            "The warmup PNG has an unexpected number of filtered bytes"
        );
        for row in scanlines.chunks_exact(row_bytes) {
            *counts
                .get_mut(usize::from(row[0]))
                .context("A warmup PNG scanline contains an invalid filter byte")? += 1;
        }
    }
    Ok(counts)
}

fn run_pair(
    directory: &Path,
    inputs: &[Input],
    control_first: bool,
    check_filters: bool,
) -> Result<Value> {
    let first_dir = directory.join("first");
    let second_dir = directory.join("second");
    let first_cache = CacheManager::open(&first_dir)?;
    let second_cache = CacheManager::open(&second_dir)?;
    let (first_arm, second_arm) = if control_first {
        (Arm::Control, Arm::NoFilter)
    } else {
        (Arm::NoFilter, Arm::Control)
    };
    let (first, first_paths) = persist_visit(first_cache, inputs, first_arm)?;
    let (second, second_paths) = persist_visit(second_cache, inputs, second_arm)?;
    let first = reopen_visit(&first_dir, inputs, first, &first_paths)?;
    let second = reopen_visit(&second_dir, inputs, second, &second_paths)?;
    let (order, control, no_filter, control_paths, no_filter_paths) = if control_first {
        ("AB", first, second, first_paths, second_paths)
    } else {
        ("BA", second, first, second_paths, first_paths)
    };
    let mut pair = json!({"order": order, "control": control, "no_filter": no_filter});
    if check_filters {
        let control = count_png_filters(inputs, &control_paths)?;
        let no_filter = count_png_filters(inputs, &no_filter_paths)?;
        ensure!(
            control[1..].iter().any(|count| *count > 0) && no_filter[1..] == [0; 4],
            "The warmup PNG filters do not distinguish the default and no-filter arms"
        );
        pair["filter_rows"] = json!({"control": control, "no_filter": no_filter});
    }
    Ok(pair)
}

fn env_path(name: &str) -> Result<PathBuf> {
    env::var_os(name)
        .map(PathBuf::from)
        .with_context(|| format!("Set {name} for the local cover persistence trial"))
}

fn canonical_parent_path(path: &Path) -> Result<PathBuf> {
    let path = std::path::absolute(path)?;
    let leaf = path
        .file_name()
        .context("A trial path must have a leaf name")?;
    Ok(path
        .parent()
        .context("A trial path must have a parent")?
        .canonicalize()?
        .join(leaf))
}

#[test]
#[ignore = "Run only on the Linux worker with a release build, XFS and actual grid PNGs"]
fn real_local_cover_persistence_trial() -> Result<()> {
    ensure!(
        !cfg!(debug_assertions),
        "Use a release build for the persistence trial"
    );
    let _guard = super::tests::TEST_RENDER_LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let root = env_path("AIDOKU_COVER_TRIAL_ROOT")?;
    ensure!(
        root.is_absolute(),
        "The trial workspace must have an absolute path"
    );
    let root = canonical_parent_path(&root)?;
    let output = canonical_parent_path(&env_path("AIDOKU_COVER_TRIAL_OUTPUT")?)?;
    ensure!(
        !output.starts_with(&root),
        "Store the report outside the trial workspace"
    );
    let filesystem = Command::new("stat")
        .args(["--file-system", "--format=%T", "--"])
        .arg(
            root.parent()
                .context("The trial workspace must have a parent")?,
        )
        .output()
        .context("Find the trial filesystem type")?;
    ensure!(
        filesystem.status.success() && filesystem.stdout == b"xfs\n",
        "The trial workspace must use XFS"
    );
    let inputs = load_inputs(&env_path("AIDOKU_COVER_TRIAL_INPUT")?)?;
    fs::DirBuilder::new()
        .mode(0o700)
        .create(&root)
        .context("Create the separate trial workspace; it must not already exist")?;
    let owned = fs::symlink_metadata(&root)?;
    let warmup = run_pair(&root.join("warmup"), &inputs, true, true)?;
    let mut pairs = Vec::with_capacity(PAIR_COUNT);
    for pair in 0..PAIR_COUNT {
        pairs.push(run_pair(
            &root.join(format!("pair-{pair:02}")),
            &inputs,
            pair % 2 == 0,
            false,
        )?);
    }
    let report = json!({
        "schema": 1,
        "filesystem": "xfs",
        "image_count": IMAGE_COUNT,
        "warmup": warmup,
        "pairs": pairs,
    });
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(output)
        .context("Create a new trial report")?;
    serde_json::to_writer_pretty(&mut file, &report)?;
    writeln!(file)?;
    file.sync_all()?;
    let current = fs::symlink_metadata(&root)?;
    ensure!(
        current.is_dir()
            && current.dev() == owned.dev()
            && current.ino() == owned.ino()
            && root.canonicalize()? == root,
        "The owned trial workspace changed; refusing to remove it"
    );
    fs::remove_dir_all(&root).context("Remove the completed trial workspace")?;
    Ok(())
}
