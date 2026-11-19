// Appended only to a disposable copy of the exact production cover_jobs.rs.
// Each case gets a fresh process, temporary database and synthetic image.
#[cfg(test)]
mod qualified_prewarm_observation {
    use super::*;
    use crate::{cache::CacheManager, platform::DeviceProfile, render_cache, startup_options::StartupOptions};
    use rusqlite::{Connection, Error, ErrorCode, OpenFlags};
    use std::{collections::BTreeMap, fs::File, time::SystemTime};

    const CASES: [&str; 8] = ["disabled", "hit-locked", "changed-before", "changed-after",
        "missing-before", "missing-after", "cancelled", "decoder-busy"];

    fn hash(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }

    // Invert the two palette entries without changing the IHDR, IDAT, or file
    // size. The rendered pattern reverses, so an old pixel buffer cannot pass.
    fn synthetic_png(invert: bool) -> Result<Vec<u8>> {
        let mut bytes = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut bytes, 16, 24);
            encoder.set_color(png::ColorType::Indexed);
            encoder.set_depth(png::BitDepth::Eight);
            encoder.set_palette(if invert { vec![255,255,255,0,0,0] } else { vec![0,0,0,255,255,255] });
            let mut writer = encoder.write_header()?;
            let indices = (0..24).flat_map(|y| (0..16).map(move |x| ((x/4+y/6)%2) as u8)).collect::<Vec<_>>();
            writer.write_image_data(&indices)?;
            writer.finish()?;
        }
        Ok(bytes)
    }

    fn replace_source(path: &Path, replacement: &[u8], original_time: SystemTime) -> Result<()> {
        let old = fs::read(path)?;
        assert_eq!(old.len(), replacement.len());
        assert_ne!(old.as_slice(), replacement);
        fs::write(path, replacement)?;
        File::options().write(true).open(path)?.set_times(fs::FileTimes::new().set_modified(original_time))?;
        assert_eq!(fs::metadata(path)?.modified()?, original_time);
        Ok(())
    }

    fn content(path: &Path) -> Result<Option<String>> {
        match fs::read(path) {
            Ok(bytes) => Ok(Some(hash(&bytes))),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e.into()),
        }
    }

    fn inventory(cache: &CacheManager) -> Result<BTreeMap<PathBuf, String>> {
        fn visit(root: &Path, dir: &Path, files: &mut BTreeMap<PathBuf, String>) -> Result<()> {
            for entry in fs::read_dir(dir)? {
                let entry = entry?;
                let path = entry.path();
                let kind = entry.file_type()?;
                if kind.is_dir() { visit(root, &path, files)?; }
                else {
                    assert!(kind.is_file());
                    files.insert(path.strip_prefix(root)?.into(), hash(&fs::read(path)?));
                }
            }
            Ok(())
        }
        let root = cache.namespace();
        let mut files = BTreeMap::new();
        for area in ["raw", "processed", "temp"] { visit(root, &root.join(area), &mut files)?; }
        Ok(files)
    }

    struct CoverOutput {
        path: PathBuf,
        rgb: Vec<u8>,
        width: u32,
        height: u32,
        local_derivative_hit: bool,
    }

    fn ordinary(data: &Path, book: &Book) -> Result<CoverOutput> {
        // Supply no remote source, no credentials and no network permission.
        let manager = Arc::new(SourceManager::new(data, &[])?);
        let result = prepare_cover(manager, book.clone(), cover_options(376, 514), false, None)?;
        let JobResult::Cover { id, uri, path, image, local_derivative_hit } = result else { panic!("Expected a local cover result") };
        assert_eq!(id, book.id);
        assert_eq!(uri, book.cover);
        Ok(CoverOutput { path, rgb: image.as_bytes().to_vec(), width: image.width(), height: image.height(), local_derivative_hit })
    }

    fn options(data: &Path, limit: u8) -> AppOptions {
        AppOptions { data_dir: data.to_owned(), startup_options: StartupOptions { prewarm: limit,
            last_cover_grace: false, import_thumbnail_idle: false, resident_seconds: 0, ..Default::default() },
            demo: false, offline: true, resume: false, landscape: false, initial_screen: "library".into() }
    }

    // Test an independent, fresh SQLite reader at both ends of warm.run. An
    // unrelated open or I/O error cannot stand in for the intended lock.
    fn blocked_reader(database: &Path) -> Result<i32> {
        let reader = Connection::open_with_flags(database, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        reader.busy_timeout(Duration::ZERO)?;
        let result = reader.query_row("SELECT count(*) FROM books", [], |row| row.get::<_, u64>(0));
        match result {
            Err(Error::SqliteFailure(e, _)) if matches!(e.code, ErrorCode::DatabaseBusy | ErrorCode::DatabaseLocked) => Ok(e.extended_code & 255),
            other => anyhow::bail!("A competing library reader was not denied by SQLite: {other:?}"),
        }
    }

    fn independent_cache_reader(cache: &CacheManager) -> Result<u64> {
        let db = Connection::open_with_flags(cache.namespace().join("index.sqlite3"), OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        db.busy_timeout(Duration::ZERO)?;
        Ok(db.query_row("SELECT count(*) FROM artifacts WHERE kind = 'processed' AND valid = 1", [], |row| row.get(0))?)
    }

    fn verify_trace(path: &Path, case: &str) -> Result<()> {
        let trace: Value = serde_json::from_slice(&fs::read(path)?)?;
        assert_eq!(trace["success"], true);
        assert_eq!(trace["finished"], true);
        assert_eq!(trace["dropped_events"], 0);
        let names = trace["events"].as_array().unwrap().iter().map(|event| event["name"].as_str().unwrap()).collect::<Vec<_>>();
        assert_eq!(names.iter().filter(|n| **n == "application_prepared").count(), 1);
        assert_eq!(names.iter().filter(|n| **n == "cover_preparation_started").count(), 1);
        assert_eq!(names.iter().filter(|n| **n == "cover_preparation_finished").count(), 1);
        let mut expected = Vec::new();
        if case != "disabled" {
            expected.push("startup_cover_warm_plan");
            if case == "cancelled" { expected.push("startup_cover_warm_stopped"); }
            else {
                expected.push("startup_cover_warm_attempt");
                expected.push(match case {
                    "changed-before" | "decoder-busy" => "startup_cover_warm_image_unavailable",
                    "missing-before" => "startup_cover_warm_image_failed",
                    _ => "startup_cover_warm_hit",
                });
            }
        }
        if case == "hit-locked" { expected.push("cover_local_derivative_memory_hit"); }
        expected.push(if case.starts_with("changed-") || case.starts_with("missing-") {
            "cover_local_derivative_prepared"
        } else { "cover_local_derivative_reused" });
        let events = names.into_iter().filter(|n| n.starts_with("startup_cover_warm_") || n.starts_with("cover_local_derivative_")).collect::<Vec<_>>();
        assert_eq!(events, expected, "Unexpected production warm or cover event sequence");
        Ok(())
    }

    #[test]
    fn controlled_primary_hint() -> Result<()> {
        let case = std::env::var("AIDOKU_TEST_PREWARM_CASE")?;
        let output = PathBuf::from(std::env::var_os("AIDOKU_TEST_PREWARM_OUT").context("Missing trace destination")?);
        assert!(CASES.contains(&case.as_str()));
        let dir = tempfile::tempdir()?;
        let data = fs::canonicalize(dir.path())?;
        let database = data.join("library.sqlite3");
        let raw = data.join("synthetic-cover.png");
        let original = synthetic_png(false)?;
        let replacement = synthetic_png(true)?;
        assert_eq!(original.len(), replacement.len());
        assert_ne!(original, replacement);
        fs::write(&raw, &original)?;
        let original_time = fs::metadata(&raw)?.modified()?;
        assert!(raw.is_absolute());
        let book = Book { id: "local:prewarm-fixture".into(), source_id: "local".into(), remote_id: "fixture".into(),
            title: "Synthetic pattern".into(), in_library: true, cover: raw.to_string_lossy().into(), ..Default::default() };
        {
            let store = Store::open(&database)?;
            store.save_source(&SourceConfig { id: "local".into(), kind: "local".into(), enabled: true, ..Default::default() })?;
            store.upsert_book(&book)?;
        }
        let cache = CacheManager::open(&data)?;
        let seed = ordinary(&data, &book)?;
        assert!(!seed.local_derivative_hit);
        assert!(seed.path.is_file() && seed.path.starts_with(cache.namespace().join("processed")));
        assert!(seed.rgb.windows(2).any(|p| p[0] != p[1]));
        render_cache::clear_memory(&cache);
        assert_eq!(render_cache::stats().memory_entries, 0);

        // Untraced A on the same book, absolute source, derivative and data
        // directory as the contender. A separate traced disabled case also
        // proves that its baseline cannot consume a speculative memory entry.
        let (baseline, none) = App::prepare_with_cover_hints(options(&data, 0), DeviceProfile::Scribe, 0, || true)?;
        assert!(none.is_none());
        assert!(baseline.options.offline && baseline.network_control.is_suspended());
        baseline.network_control.resume();
        let baseline_before = render_cache::stats();
        let control = ordinary(&data, &book)?;
        let baseline_after = render_cache::stats();
        assert!(control.local_derivative_hit);
        assert_eq!(baseline_after.prefetch_memory_hits, baseline_before.prefetch_memory_hits);
        assert_eq!(baseline_after.memory_hits, baseline_before.memory_hits);
        assert_eq!(baseline_after.disk_hits, baseline_before.disk_hits + 1);
        assert_eq!(control.rgb, seed.rgb);
        assert_eq!(control.path, seed.path);
        baseline.shutdown();
        assert!(baseline.finish_shutdown(Duration::from_secs(3)));
        drop(baseline);
        render_cache::clear_memory(&cache);
        assert_eq!(render_cache::stats().memory_entries, 0);

        crate::reader_timing::enable(Some(&output), Instant::now())?;
        let limit = if case == "disabled" { 0 } else { 2 };
        let (prepared, hint) = App::prepare_with_cover_hints(options(&data, limit), DeviceProfile::Scribe, limit, || true)?;
        let proposed = hint.is_some();
        assert_eq!(proposed, limit == 2);
        assert_eq!(prepared.current_book_id.as_deref(), Some(book.id.as_str()));
        assert_eq!(prepared.data_dir, data);
        assert!(prepared.options.offline && prepared.network_control.is_suspended());
        let writer = Connection::open(&database)?;
        writer.busy_timeout(Duration::from_secs(3))?;
        let initial_rows: u64 = writer.query_row("SELECT count(*) FROM books", [], |row| row.get(0))?;
        assert_eq!(initial_rows, 1);
        writer.execute_batch("BEGIN EXCLUSIVE")?;
        let busy_before = blocked_reader(&database)?;
        let cache_rows_before = independent_cache_reader(&cache)?;
        assert_eq!(cache_rows_before, 1);
        let initial_derivative = content(&seed.path)?.context("Seed derivative missing")?;
        if case == "changed-before" { replace_source(&raw, &replacement, original_time)?; }
        if case == "missing-before" { fs::remove_file(&seed.path)?; }
        let before_derivative = content(&seed.path)?;
        let before_files = inventory(&cache)?;
        let before_memory = render_cache::stats();
        let commits_before = cache.stats().commits;
        assert_eq!(before_memory.memory_entries, 0);
        let occupied = if case == "decoder-busy" {
            Some(imaging::try_spare_decode_permit().context("Fixture could not reserve an idle decoder")?)
        } else { None };
        if let Some(hint) = hint { hint.run(|| case != "cancelled"); }
        drop(occupied);
        let after_memory = render_cache::stats();
        let after_files = inventory(&cache)?;
        let after_derivative = content(&seed.path)?;
        let commits_after_warm = cache.stats().commits;
        let warm_commits = commits_after_warm - commits_before;
        let busy_after = blocked_reader(&database)?;
        let cache_rows_after = independent_cache_reader(&cache)?;
        assert_eq!(cache_rows_after, 1);
        assert_eq!(before_files, after_files, "Speculation changed a raw, processed or temporary artifact");
        assert_eq!(before_derivative, after_derivative);
        assert_eq!(warm_commits, 0);
        assert_eq!(after_memory.cold_renders, before_memory.cold_renders);
        let cached = matches!(case.as_str(), "hit-locked" | "changed-after" | "missing-after");
        assert_eq!(after_memory.memory_entries, u64::from(cached));
        assert_eq!(after_memory.processed_decoded_bytes > before_memory.processed_decoded_bytes, cached);
        if case == "changed-after" { replace_source(&raw, &replacement, original_time)?; }
        if case == "missing-after" { fs::remove_file(&seed.path)?; }
        let before_foreground = content(&seed.path)?;
        writer.execute_batch("ROLLBACK")?;
        let reopened_rows: u64 = writer.query_row("SELECT count(*) FROM books", [], |row| row.get(0))?;
        assert_eq!(reopened_rows, initial_rows);

        // The real initially-suspended metadata controller resumes admission
        // on the Library route. The ensuing worker still admits local I/O only.
        prepared.network_control.resume();
        let result = ordinary(&data, &book)?;
        let after_front = render_cache::stats();
        let consumed = after_front.prefetch_memory_hits - after_memory.prefetch_memory_hits;
        let foreground_commits = cache.stats().commits - commits_after_warm;
        let regenerated = case.starts_with("changed-") || case.starts_with("missing-");
        assert_eq!(consumed, u64::from(case == "hit-locked"));
        assert_eq!(after_front.memory_hits - after_memory.memory_hits, consumed);
        assert_eq!(after_front.cold_renders - after_memory.cold_renders, u64::from(regenerated));
        assert_eq!(foreground_commits, u64::from(regenerated));
        assert_eq!(result.local_derivative_hit, !regenerated);
        assert_eq!(result.path == control.path, !case.starts_with("changed-"));
        let final_source = fs::read(&raw)?;
        let expected_source: &[u8] = if case.starts_with("changed-") { &replacement } else { &original };
        assert_eq!(final_source.as_slice(), expected_source);
        assert_eq!(result.rgb == control.rgb, !case.starts_with("changed-"));
        let reference = prepared_pixels::prepare(Arc::new(imaging::decode_page(&final_source, &cover_options(376, 514))?));
        assert_eq!(reference.as_bytes(), result.rgb.as_slice());
        assert_eq!((reference.width(), reference.height()), (result.width, result.height));
        assert_eq!(result.rgb.len() as u64, u64::from(result.width) * u64::from(result.height) * 3);
        let final_derivative = content(&result.path)?.context("Foreground did not leave its derivative")?;
        let same_mtime = fs::metadata(&raw)?.modified()? == original_time;
        assert!(same_mtime);
        let mut facts = serde_json::json!({"case":case, "limit":limit, "hint_selected":proposed,
            "library_rows_unlocked":[initial_rows,reopened_rows], "library_busy_codes":[busy_before,busy_after],
            "cache_valid_rows_while_locked":[cache_rows_before,cache_rows_after], "offline":prepared.options.offline,
            "initial_source_sha256":hash(&original), "final_source_sha256":hash(&final_source), "source_sizes":[original.len(),final_source.len()],
            "same_source_mtime":same_mtime, "initial_derivative_sha256":initial_derivative, "before_warm_derivative_sha256":before_derivative,
            "after_warm_derivative_sha256":after_derivative, "before_foreground_derivative_sha256":before_foreground, "final_derivative_sha256":final_derivative});
        let rendering = serde_json::json!({
            "initial_rgb_sha256":hash(&control.rgb), "final_rgb_sha256":hash(&result.rgb), "reference_rgb_sha256":hash(reference.as_bytes()),
            "width":result.width, "height":result.height, "rgb_bytes":result.rgb.len(), "memory_entries_before_warm":before_memory.memory_entries,
            "memory_entries_after_warm":after_memory.memory_entries, "prefetch_consumed":consumed, "warm_commits":warm_commits, "foreground_commits":foreground_commits,
            "foreground_local_derivative_hit":result.local_derivative_hit, "artifacts_unchanged_during_warm":before_files == after_files,
            "artifact_count_before_warm":before_files.len(), "artifact_count_after_warm":after_files.len()});
        facts.as_object_mut().unwrap().extend(rendering.as_object().unwrap().clone());
        prepared.shutdown();
        assert!(prepared.finish_shutdown(Duration::from_secs(3)));
        crate::reader_timing::finish(true)?;
        verify_trace(&output, &case)?;
        fs::write(output.with_extension("observations.json"), serde_json::to_vec(&facts)?)?;
        Ok(())
    }
}
