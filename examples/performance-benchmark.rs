//! Focused Phase 3 measurements. Execute only on the designated remote worker.
//!
//! The embedded Store module is the production source, including crate-visible
//! view queries. This measures Store operations rather than application UI work.

use std::{
    cell::RefCell,
    collections::BTreeMap,
    ffi::{CStr, c_char, c_int, c_uint, c_void},
    fs::{self, File},
    path::{Path, PathBuf},
    sync::Arc,
    time::Instant,
};

use aidoku_kindle::{cache::CacheManager, render_cache};
pub use aidoku_kindle::{domain, imaging, tr};
use anyhow::{Context, Result, ensure};
use clap::{Parser, ValueEnum};
use domain::{Book, CachedPageUpdate, Chapter, DownloadJob, Page, Progress, cached_page_identity};
use image::{GrayImage, Luma};
use rusqlite::{Connection, params};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

#[allow(dead_code)]
#[path = "../src/store.rs"]
mod store;

use store::{Store, view_queries::LibraryQuery};

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
enum Scenario {
    Prepare,
    Qualify,
    Store,
    Render,
    Projection,
    Publication,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum Workload {
    Store,
    Render,
    Projection,
    Publication,
}

#[derive(Debug, Parser)]
struct Arguments {
    #[arg(long, value_enum)]
    scenario: Scenario,
    #[arg(long, value_enum, default_value_t = Workload::Store)]
    workload: Workload,
    #[arg(long)]
    data_dir: PathBuf,
    #[arg(long, default_value_t = 32)]
    pages: usize,
    #[arg(long, default_value_t = 1000)]
    books: usize,
    #[arg(long, default_value_t = 21)]
    samples: usize,
    #[arg(long)]
    sql_only: bool,
    #[arg(long)]
    syscall_markers: bool,
    #[arg(long)]
    reverse_algorithm_order: bool,
}

#[derive(Debug, Default, Serialize)]
struct SqlCounters {
    statements: u64,
    reads: u64,
    dml: u64,
    begins: u64,
    commits: u64,
    rollbacks: u64,
    subprograms: u64,
    vm_steps: u64,
    fullscan_steps: u64,
    row_events: BTreeMap<String, u64>,
    executions: Vec<Value>,
}

#[derive(Default)]
struct SqlState {
    enabled: bool,
    active: BTreeMap<usize, String>,
    counters: SqlCounters,
}

thread_local! {
    static SQL: RefCell<SqlState> = RefCell::new(SqlState::default());
}

struct ObserverRegistration;

impl ObserverRegistration {
    fn install() -> Result<Self> {
        ensure!(
            unsafe { rusqlite::ffi::sqlite3_auto_extension(Some(observe_connection)) }
                == rusqlite::ffi::SQLITE_OK,
            "Register the SQL observer"
        );
        Ok(Self)
    }
}

impl Drop for ObserverRegistration {
    fn drop(&mut self) {
        SQL.with(|state| state.borrow_mut().enabled = false);
        unsafe {
            rusqlite::ffi::sqlite3_cancel_auto_extension(Some(observe_connection));
        }
    }
}

unsafe extern "C" fn observe_connection(
    database: *mut rusqlite::ffi::sqlite3,
    _error: *mut *mut c_char,
    _api: *const rusqlite::ffi::sqlite3_api_routines,
) -> c_int {
    unsafe {
        rusqlite::ffi::sqlite3_trace_v2(
            database,
            (rusqlite::ffi::SQLITE_TRACE_STMT | rusqlite::ffi::SQLITE_TRACE_PROFILE) as c_uint,
            Some(trace_statement),
            std::ptr::null_mut(),
        );
        rusqlite::ffi::sqlite3_update_hook(database, Some(trace_row), std::ptr::null_mut());
    }
    rusqlite::ffi::SQLITE_OK
}

unsafe extern "C" fn trace_statement(
    event: c_uint,
    _context: *mut c_void,
    statement: *mut c_void,
    sql: *mut c_void,
) -> c_int {
    SQL.with(|state| {
        let mut state = state.borrow_mut();
        if !state.enabled {
            return;
        }
        let identifier = statement as usize;
        let statement = statement.cast::<rusqlite::ffi::sqlite3_stmt>();
        if event == rusqlite::ffi::SQLITE_TRACE_STMT as c_uint && !sql.is_null() {
            let text = unsafe { CStr::from_ptr(sql.cast::<c_char>()) }.to_string_lossy();
            let text = text.trim_start();
            if text.starts_with("--") || state.active.contains_key(&identifier) {
                state.counters.subprograms += 1;
                return;
            }
            unsafe {
                rusqlite::ffi::sqlite3_stmt_status(
                    statement,
                    rusqlite::ffi::SQLITE_STMTSTATUS_VM_STEP,
                    1,
                );
                rusqlite::ffi::sqlite3_stmt_status(
                    statement,
                    rusqlite::ffi::SQLITE_STMTSTATUS_FULLSCAN_STEP,
                    1,
                );
            }
            state.active.insert(identifier, text.to_owned());
            let token = text
                .split(|character: char| !character.is_ascii_alphabetic())
                .next()
                .unwrap_or("")
                .to_ascii_uppercase();
            state.counters.statements += 1;
            match token.as_str() {
                "BEGIN" | "SAVEPOINT" => state.counters.begins += 1,
                "COMMIT" | "END" | "RELEASE" => state.counters.commits += 1,
                "ROLLBACK" => state.counters.rollbacks += 1,
                _ if unsafe { rusqlite::ffi::sqlite3_stmt_readonly(statement) } != 0 => {
                    state.counters.reads += 1
                }
                _ => state.counters.dml += 1,
            }
        } else if event == rusqlite::ffi::SQLITE_TRACE_PROFILE as c_uint {
            if let Some(text) = state.active.remove(&identifier) {
                let steps = unsafe {
                    rusqlite::ffi::sqlite3_stmt_status(
                        statement,
                        rusqlite::ffi::SQLITE_STMTSTATUS_VM_STEP,
                        1,
                    )
                } as u64;
                let scans = unsafe {
                    rusqlite::ffi::sqlite3_stmt_status(
                        statement,
                        rusqlite::ffi::SQLITE_STMTSTATUS_FULLSCAN_STEP,
                        1,
                    )
                } as u64;
                state.counters.vm_steps += steps;
                state.counters.fullscan_steps += scans;
                state
                    .counters
                    .executions
                    .push(json!({"sql": text, "vm_steps": steps, "fullscan_steps": scans}));
            }
        }
    });
    0
}

unsafe extern "C" fn trace_row(
    _context: *mut c_void,
    operation: c_int,
    _database: *const c_char,
    table: *const c_char,
    _row: rusqlite::ffi::sqlite3_int64,
) {
    if table.is_null() {
        return;
    }
    SQL.with(|state| {
        let mut state = state.borrow_mut();
        if !state.enabled {
            return;
        }
        let table = unsafe { CStr::from_ptr(table) }.to_string_lossy();
        let operation = match operation {
            rusqlite::ffi::SQLITE_INSERT => "insert",
            rusqlite::ffi::SQLITE_UPDATE => "update",
            rusqlite::ffi::SQLITE_DELETE => "delete",
            _ => "other",
        };
        *state
            .counters
            .row_events
            .entry(format!("{table}.{operation}"))
            .or_default() += 1;
    });
}

fn capture_sql<T>(operation: impl FnOnce() -> Result<T>) -> Result<(T, Value)> {
    SQL.with(|state| {
        *state.borrow_mut() = SqlState {
            enabled: true,
            ..SqlState::default()
        }
    });
    let result = operation();
    let counters = SQL.with(|state| {
        let mut state = state.borrow_mut();
        state.enabled = false;
        ensure!(
            state.active.is_empty(),
            "A statement did not finish inside the SQL window"
        );
        Ok(serde_json::to_value(std::mem::take(&mut state.counters))?)
    })?;
    Ok((result?, counters))
}

fn elapsed_ns(start: Instant) -> u64 {
    start.elapsed().as_nanos().min(u128::from(u64::MAX)) as u64
}

fn latency(samples: Vec<u64>) -> Value {
    let mut sorted = samples.clone();
    sorted.sort_unstable();
    let percentile =
        |numerator: usize| sorted[(sorted.len() * numerator).div_ceil(100).saturating_sub(1)];
    json!({"samples_ns": samples, "count": sorted.len(), "p50_ns": percentile(50),
        "p95_ns": percentile(95), "max_ns": sorted.last(), "method": "nearest rank"})
}

fn measure<T>(
    arguments: &Arguments,
    mut operation: impl FnMut(usize) -> Result<T>,
) -> Result<Value> {
    if arguments.sql_only {
        let (_, sql) = capture_sql(|| operation(0))?;
        return Ok(json!({"latency": null, "sql": sql, "timings_recorded": false}));
    }
    let mut samples = Vec::with_capacity(arguments.samples);
    for index in 0..arguments.samples {
        let start = Instant::now();
        let result = operation(index)?;
        samples.push(elapsed_ns(start));
        std::hint::black_box(result);
    }
    Ok(json!({"latency": latency(samples), "sql": null, "timings_recorded": true}))
}

fn page(chapter: &str, index: usize, path: Option<PathBuf>) -> Page {
    Page {
        url: format!("https://fixture.invalid/phase3/{chapter}/{index:06}.png"),
        local_path: path,
        ..Page::default()
    }
}

fn book(id: &str) -> Book {
    Book {
        id: id.into(),
        source_id: "phase3".into(),
        remote_id: id.into(),
        title: format!("Phase 3 {id}"),
        author: "Fixture Author".into(),
        category: "reading".into(),
        in_library: true,
        updated_at: 1_794_009_600,
        chapter_count: 12,
        unread: 8,
        ..Book::default()
    }
}

fn chapter(book: &Book, id: &str, index: usize, pages: usize) -> Chapter {
    Chapter {
        id: id.into(),
        book_id: book.id.clone(),
        source_id: book.source_id.clone(),
        remote_id: id.into(),
        number: index as f64 + 1.0,
        title: format!("Chapter {index}"),
        date: "2026-11-06".into(),
        page_count: pages as u32,
        ..Chapter::default()
    }
}

fn image_fixture(path: &Path, width: u32, height: u32, jpeg: bool) -> Result<Value> {
    let image = GrayImage::from_fn(width, height, |x, y| {
        let border = x < 12 || y < 12 || x + 12 >= width || y + 12 >= height;
        Luma([if border {
            255
        } else {
            ((x * 31 + y * 17 + (x / 29) * (y / 37)) % 239) as u8
        }])
    });
    if jpeg {
        image::codecs::jpeg::JpegEncoder::new_with_quality(File::create(path)?, 90).encode(
            image.as_raw(),
            width,
            height,
            image::ExtendedColorType::L8,
        )?;
    } else {
        image.save(path)?;
    }
    let bytes = fs::read(path)?;
    Ok(
        json!({"file": path.file_name().map(|name| name.to_string_lossy()), "width": width, "height": height,
        "encoding": if jpeg { "jpeg" } else { "png" }, "bytes": bytes.len(), "sha256": format!("{:x}", Sha256::digest(&bytes))}),
    )
}

fn prepare(arguments: &Arguments) -> Result<Value> {
    ensure!(
        !arguments.data_dir.exists(),
        "Fixture directories must be fresh"
    );
    fs::create_dir_all(arguments.data_dir.join("fixtures"))?;
    let root = &arguments.data_dir;
    let tiny = image_fixture(&root.join("fixtures/tiny.png"), 32, 32, false)?;
    fs::copy(
        root.join("fixtures/tiny.png"),
        root.join("fixtures/alternate.png"),
    )?;
    let mut fixture = json!({"tiny": tiny, "pages": arguments.pages, "books": arguments.books});
    match arguments.workload {
        Workload::Store => {
            let store = Store::open(root.join("library.sqlite3"))?;
            let record = book("store-book");
            store.upsert_book(&record)?;
            let chapters = ["read", "path", "progress"]
                .into_iter()
                .enumerate()
                .map(|(index, id)| chapter(&record, id, index, arguments.pages))
                .collect::<Vec<_>>();
            store.save_chapters(&record.id, &chapters)?;
            for record in chapters {
                store.save_pages(
                    &record.id,
                    &(0..arguments.pages)
                        .map(|index| page(&record.id, index, None))
                        .collect::<Vec<_>>(),
                )?;
            }
            let batch_book = book("batch-book");
            store.upsert_book(&batch_book)?;
            let chapters = (0..12)
                .map(|index| chapter(&batch_book, &format!("batch-{index}"), index, 32))
                .collect::<Vec<_>>();
            store.save_chapters(&batch_book.id, &chapters)?;
        }
        Workload::Render => {
            let mut images = Vec::new();
            for (name, width, height, jpeg) in [
                ("page.png", 1024, 1536, false),
                ("page.jpeg", 1024, 1536, true),
                ("spread.png", 900, 650, false),
                ("vertical.jpeg", 900, 6000, true),
                ("vertical.png", 768, 22528, false),
            ] {
                images.push(image_fixture(
                    &root.join("fixtures").join(name),
                    width,
                    height,
                    jpeg,
                )?);
            }
            CacheManager::open(root)?;
            fixture["images"] = json!(images);
        }
        Workload::Projection => prepare_projection(arguments)?,
        Workload::Publication => {
            let cache = CacheManager::open(root)?;
            let bytes = fs::read(root.join("fixtures/tiny.png"))?;
            let write =
                cache.begin_raw(&page("publication-base", 0, None), None, bytes.len() as u64)?;
            fs::write(write.temp_path(), &bytes)?;
            cache.validate_path(write.temp_path())?;
            write.commit()?;
        }
    }
    fs::write(
        root.join("fixture.json"),
        serde_json::to_vec_pretty(&fixture)?,
    )?;
    Ok(json!({"fixture": fixture, "checks": {"fresh_fixture_prepared": true}, "operations": {}}))
}

fn prepare_projection(arguments: &Arguments) -> Result<()> {
    let root = &arguments.data_dir;
    drop(Store::open(root.join("library.sqlite3"))?);
    let mut connection = Connection::open(root.join("library.sqlite3"))?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    connection.pragma_update(None, "recursive_triggers", "ON")?;
    let transaction = connection.transaction()?;
    for index in 0..arguments.books {
        let mut record = book(&format!("book-{index:06}"));
        record.updated_at += index as i64;
        record.unread = (index % 12) as u32;
        record.category = ["reading", "planned", "completed"][index % 3].into();
        record.description = "Deterministic non-demo library metadata. ".repeat(12);
        transaction.execute("INSERT INTO books(id,source_id,remote_id,title,in_library,category,updated_at,document) VALUES(?1,?2,?3,?4,1,?5,?6,?7)",
            params![record.id,record.source_id,record.remote_id,record.title,record.category,record.updated_at,serde_json::to_string(&record)?])?;
        let local = root
            .join("fixtures")
            .join(format!("offline-{index:06}.png"));
        fs::copy(root.join("fixtures/tiny.png"), &local)?;
        for number in 0..12 {
            let mut item = chapter(
                &record,
                &format!("{}-chapter-{number:02}", record.id),
                number,
                32,
            );
            item.downloaded = number == 0;
            if item.downloaded {
                item.page_count = 3;
            }
            item.read = number >= record.unread as usize;
            transaction.execute("INSERT INTO chapters(id,book_id,source_id,remote_id,number,document) VALUES(?1,?2,?3,?4,?5,?6)",
                params![item.id,item.book_id,item.source_id,item.remote_id,item.number,serde_json::to_string(&item)?])?;
            if number < 4 {
                let progress = Progress {
                    book_id: record.id.clone(),
                    chapter_id: item.id.clone(),
                    page: if number == 0 { 2 } else { 5 },
                    total: item.page_count,
                    updated_at: record.updated_at + number as i64,
                    completed: false,
                };
                transaction.execute("INSERT INTO progress(chapter_id,book_id,page,total,completed,updated_at) VALUES(?1,?2,?3,?4,0,?5)",
                    params![progress.chapter_id,progress.book_id,progress.page,progress.total,progress.updated_at])?;
            }
            if number == 0 {
                for position in 0..3 {
                    let path = if position == 0 {
                        root.join("fixtures/tiny.png")
                    } else {
                        local.clone()
                    };
                    let value = page(&item.id, position, Some(path));
                    transaction.execute(
                        "INSERT INTO pages(chapter_id,position,document) VALUES(?1,?2,?3)",
                        params![item.id, position, serde_json::to_string(&value)?],
                    )?;
                }
                let job = DownloadJob {
                    chapter_id: item.id.clone(),
                    book_id: record.id.clone(),
                    state: "completed".into(),
                    completed: 3,
                    total: 3,
                    error: String::new(),
                };
                transaction.execute("INSERT INTO downloads(chapter_id,book_id,state,document) VALUES(?1,?2,'completed',?3)",
                    params![job.chapter_id,job.book_id,serde_json::to_string(&job)?])?;
            }
        }
    }
    transaction.commit()?;
    Ok(())
}

fn store_operations(arguments: &Arguments) -> Result<Value> {
    let store = Store::open(arguments.data_dir.join("library.sqlite3"))?;
    let mut operations = BTreeMap::new();
    let offset = arguments.pages / 2;
    let expected = page("read", offset, None);
    let warm = store.page_window("read", offset, 1)?;
    ensure!(
        warm.pages == [expected.clone()] && warm.total == arguments.pages,
        "The indexed window differs"
    );
    ensure!(
        store.pages("read")?.get(offset) == Some(&expected),
        "The warmed full-manifest strategy differs"
    );
    let window = || {
        measure(arguments, |_| {
            let window = store.page_window("read", offset, 1)?;
            ensure!(
                window.pages.len() == 1 && window.total == arguments.pages,
                "Window cardinality changed"
            );
            Ok(std::hint::black_box(window.pages.len()))
        })
    };
    let legacy = || {
        measure(arguments, |_| {
            let pages = store.pages("read")?;
            ensure!(
                pages.get(offset) == Some(&expected),
                "The full-manifest strategy differs"
            );
            Ok(std::hint::black_box(pages.len()))
        })
    };
    let (window, mut legacy) = if arguments.reverse_algorithm_order {
        let legacy = legacy()?;
        (window()?, legacy)
    } else {
        (window()?, legacy()?)
    };
    operations.insert("manifest_window", window);
    legacy["comparison_kind"] = json!("algorithm_comparison");
    legacy["scope"] =
        json!("Full-manifest strategy in the new Store/schema, not a Candidate5 binary");
    operations.insert("legacy_manifest_algorithm", legacy);
    let original = page("path", offset, None);
    let identity = cached_page_identity(&original, offset);
    operations.insert(
        "identity_path_update",
        measure(arguments, |index| {
            let path = arguments.data_dir.join(if index % 2 == 0 {
                "fixtures/tiny.png"
            } else {
                "fixtures/alternate.png"
            });
            let report = store.update_cached_paths_report(
                "path",
                &[CachedPageUpdate {
                    index: offset,
                    identity: identity.clone(),
                    path,
                }],
            )?;
            ensure!(
                report.current && report.changed,
                "The identity update did not publish exactly one new path"
            );
            Ok(())
        })?,
    );
    let batches = (0..arguments.pages)
        .step_by(2)
        .map(|start| {
            let end = (start + 2).min(arguments.pages);
            let updates = (start..end)
                .map(|index| {
                    (
                        index as u32,
                        page(
                            "progress",
                            index,
                            Some(arguments.data_dir.join("fixtures/tiny.png")),
                        ),
                    )
                })
                .collect::<Vec<_>>();
            let job = DownloadJob {
                book_id: "store-book".into(),
                chapter_id: "progress".into(),
                state: if end == arguments.pages {
                    "completed"
                } else {
                    "downloading"
                }
                .into(),
                completed: end as u32,
                total: arguments.pages as u32,
                error: String::new(),
            };
            (job, updates)
        })
        .collect::<Vec<_>>();
    let progress = if arguments.sql_only {
        let (_, sql) = capture_sql(|| {
            for (job, updates) in &batches {
                store.record_download_progress(job, updates)?;
            }
            Ok(())
        })?;
        json!({"sql": sql, "latency": null, "timings_recorded": false, "batches": batches.len()})
    } else {
        let mut samples = Vec::new();
        for (job, updates) in &batches {
            let start = Instant::now();
            store.record_download_progress(job, updates)?;
            samples.push(elapsed_ns(start));
        }
        json!({"latency": latency(samples), "batches": batches.len(), "final_batch_includes_complete_validation": true,
            "scope": "Two-page progress calls; each latency sample is one batch and the sum covers the chapter"})
    };
    operations.insert("progress_batches", progress);
    let batch_book = store.book("batch-book")?.context("Read the batch book")?;
    let chapters = store.chapters("batch-book")?;
    store.enqueue_download_batch(&batch_book, &chapters)?;
    let batch_book = store
        .book("batch-book")?
        .context("Read the queued batch book")?;
    let noop = measure(arguments, |_| {
        let jobs = store.enqueue_download_batch(&batch_book, &chapters)?;
        ensure!(jobs.len() == 12, "The equal batch changed job count");
        Ok(())
    })?;
    if arguments.sql_only {
        ensure!(noop["sql"]["dml"] == 0, "An equal batch executed DML");
    }
    operations.insert("batch_noop", noop);
    ensure!(
        store.download("progress")?.is_some_and(
            |job| job.state == "completed" && job.completed as usize == arguments.pages
        ),
        "Progress was not durable"
    );
    ensure!(
        store.pages("progress")?.len() == arguments.pages,
        "The final manifest changed length"
    );
    ensure!(
        store
            .chapter("progress")?
            .is_some_and(|chapter| chapter.downloaded),
        "The final chapter flag was lost"
    );
    Ok(
        json!({"operations": operations, "checks": {"fixed_window_correct": true, "identity_update_correct": true,
        "completed_progress_correct": true, "equal_batch_correct": true}, "pages": arguments.pages,
        "manifest_algorithm_order": if arguments.reverse_algorithm_order { ["legacy", "window"] } else { ["window", "legacy"] }}),
    )
}

fn render_delta(before: &render_cache::RenderStats, after: &render_cache::RenderStats) -> Value {
    let before = serde_json::to_value(before).expect("Serialize render counters");
    let after = serde_json::to_value(after).expect("Serialize render counters");
    let mut delta = serde_json::Map::new();
    for key in [
        "foreground_requests",
        "prefetch_requests",
        "memory_hits",
        "prefetch_memory_hits",
        "disk_hits",
        "cold_renders",
        "processed_decodes",
        "processed_decoded_bytes",
        "output_allocations",
        "output_allocated_bytes",
        "staged_validations",
    ] {
        delta.insert(
            key.into(),
            json!(after[key].as_u64().unwrap() - before[key].as_u64().unwrap()),
        );
    }
    Value::Object(delta)
}

fn rendered_case(
    arguments: &Arguments,
    cache: &CacheManager,
    format: &str,
    mode: &str,
    options: &imaging::ImageOptions,
) -> Result<Value> {
    let raw = arguments.data_dir.join("fixtures").join(format);
    cache.validate_path(&raw)?;
    let samples = if mode == "cold" {
        arguments.samples.min(5)
    } else {
        arguments.samples
    };
    let mut times = Vec::new();
    let mut deltas = Vec::new();
    let mut expected = None;
    let value = page(&format!("render-{format}-{mode}"), 0, None);
    if mode != "cold" {
        drop(render_cache::render(
            cache, &value, &raw, options, false, 0, 1.0,
        )?);
    }
    for index in 0..samples {
        render_cache::clear_memory(cache);
        let value = if mode == "cold" {
            page(&format!("render-{format}-{mode}"), index, None)
        } else {
            value.clone()
        };
        let prepared = if mode == "prefetched_memory_hit" {
            Some(render_cache::prefetch(
                cache, &value, &raw, options, false, 0, 1.0,
            )?)
        } else {
            None
        };
        let before = render_cache::stats();
        let start = Instant::now();
        let output = render_cache::render(cache, &value, &raw, options, false, 0, 1.0)?;
        times.push(elapsed_ns(start));
        let delta = render_delta(&before, &render_cache::stats());
        match mode {
            "cold" => ensure!(
                !output.cache_hit,
                "A cold sample reused a processed artifact"
            ),
            "disk_hit" => ensure!(
                output.cache_hit && !output.memory_hit,
                "A disk sample did not decode its processed artifact"
            ),
            "prefetched_memory_hit" => {
                ensure!(
                    output.memory_hit
                        && prepared
                            .as_ref()
                            .is_some_and(|prepared| Arc::ptr_eq(&prepared.image, &output.image)),
                    "Prefetched pixels were not reused"
                );
                ensure!(
                    delta["processed_decodes"] == 0
                        && delta["output_allocations"] == 0
                        && delta["output_allocated_bytes"] == 0
                        && delta["cold_renders"] == 0
                        && delta["prefetch_memory_hits"] == 1,
                    "Resident foreground work decoded or allocated a grayscale output"
                );
            }
            _ => unreachable!(),
        }
        let hash = format!("{:x}", Sha256::digest(output.image.as_raw()));
        if let Some(expected) = &expected {
            ensure!(expected == &hash, "Rendered pixels changed between samples");
        }
        expected = Some(hash);
        deltas.push(delta);
    }
    Ok(
        json!({"latency": latency(times), "render_deltas": deltas, "pixel_sha256": expected,
        "output_options": {"width": options.width, "height": options.height},
        "raw_fixture_state": "Validated and fingerprint warmed; the operating-system page cache is not flushed",
        "scope": "render_cache::render; output hashing and cache preparation excluded; allocation counters cover grayscale output buffers only"}),
    )
}

fn render_operations(arguments: &Arguments) -> Result<Value> {
    ensure!(
        !arguments.sql_only,
        "Render counters do not require a SQL-traced pass"
    );
    let cache = CacheManager::open(&arguments.data_dir)?;
    let options = imaging::ImageOptions {
        width: 768,
        height: 1024,
        ..imaging::ImageOptions::default()
    };
    let mut operations = BTreeMap::new();
    for format in ["page.png", "page.jpeg"] {
        let mut hashes = Vec::new();
        for mode in ["cold", "disk_hit", "prefetched_memory_hit"] {
            let result = rendered_case(arguments, &cache, format, mode, &options)?;
            hashes.push(result["pixel_sha256"].clone());
            operations.insert(format!("{format}.{mode}"), result);
        }
        ensure!(
            hashes.windows(2).all(|pair| pair[0] == pair[1]),
            "Cold, disk and prefetched pixels differ"
        );
    }
    let spread = imaging::ImageOptions {
        width: 2480,
        height: 1860,
        ..options.clone()
    };
    let mut odd = rendered_case(
        arguments,
        &cache,
        "spread.png",
        "prefetched_memory_hit",
        &spread,
    )?;
    odd["scope"] = json!(
        "Full-width final unpaired spread render configuration; application prefetch/foreground option selection is covered by its module tests"
    );
    operations.insert("odd_last_spread".into(), odd);
    let vertical_options = imaging::ImageOptions {
        width: 512,
        height: 768,
        ..options
    };
    for name in ["vertical.jpeg", "vertical.png"] {
        render_cache::clear_memory(&cache);
        let raw = arguments.data_dir.join("fixtures").join(name);
        cache.validate_path(&raw)?;
        let value = page(name, 0, None);
        let mut times = Vec::new();
        let mut windows = Vec::new();
        for offset in [0, 653, 1306] {
            let before = render_cache::stats();
            let start = Instant::now();
            let output =
                render_cache::render(&cache, &value, &raw, &vertical_options, true, offset, 1.0)?;
            times.push(elapsed_ns(start));
            ensure!(
                !output.cache_hit && output.scaled_height > output.viewport_height,
                "A new vertical window was not a cold viewport"
            );
            let delta = render_delta(&before, &render_cache::stats());
            let repeated =
                render_cache::render(&cache, &value, &raw, &vertical_options, true, offset, 1.0)?;
            ensure!(
                Arc::ptr_eq(&output.image, &repeated.image),
                "A repeated vertical window did not reuse pixels"
            );
            windows.push(json!({"offset": offset, "width": output.image.width(), "height": output.image.height(),
                "scaled_height": output.scaled_height, "viewport_height": output.viewport_height,
                "pixel_sha256": format!("{:x}", Sha256::digest(output.image.as_raw())), "render_delta": delta}));
        }
        operations.insert(format!("{name}.vertical_new_windows"), json!({"latency": latency(times), "windows": windows,
            "source_decoder_passes": null, "scope": "Three distinct cold windows; source pass counters are not exposed in release; repeated resident correctness check excluded"}));
    }
    let stats = render_cache::stats();
    ensure!(
        stats.memory_bytes <= render_cache::DECODED_CACHE_BYTES as u64
            && stats.peak_memory_bytes <= render_cache::DECODED_CACHE_BYTES as u64
            && stats.memory_entries <= 16,
        "The decoded retention budget was exceeded"
    );
    Ok(
        json!({"operations": operations, "render_stats": stats, "checks": {
        "pixels_match_across_cache_modes": true, "prefetch_reuses_arc": true,
        "resident_foreground_zero_processed_decode_and_output_allocation": true,
        "odd_final_full_width_reused": true, "vertical_new_and_repeat_windows_correct": true,
        "decoded_retention_bounded": true}}),
    )
}

fn projection_operations(arguments: &Arguments) -> Result<Value> {
    let store = Store::open(arguments.data_dir.join("library.sqlite3"))?;
    let mut operations = BTreeMap::new();
    for size in [8, 18] {
        let project = || -> Result<Vec<String>> {
            let counts = store.library_counts()?;
            ensure!(
                counts.total == arguments.books,
                "The fixture library count changed"
            );
            let visible = store.library_view_page(
                LibraryQuery {
                    sort_key: Some("recent-read"),
                    ascending: false,
                    demo_order: false,
                    ..LibraryQuery::default()
                },
                1,
                size,
            )?;
            ensure!(
                visible.items.len() == size && visible.total == arguments.books,
                "Projection exceeded its visible page"
            );
            for record in &visible.items {
                let metadata = store.book_view_metadata(&record.id)?;
                ensure!(
                    metadata.book.is_some() && metadata.downloaded && metadata.progress.is_some(),
                    "Visible metadata differs"
                );
            }
            Ok(visible.items.into_iter().map(|record| record.id).collect())
        };
        let expected = project()?;
        let mut measurement = measure(arguments, |_| {
            let actual = project()?;
            ensure!(actual == expected, "Visible IDs changed");
            Ok(())
        })?;
        measurement["visible_ids"] = json!(expected);
        measurement["visible_page_books"] = json!(size);
        measurement["metadata_books"] = json!(size);
        measurement["materialized_book_documents"] = json!(size * 2);
        measurement["demo"] = json!(false);
        measurement["scope"] = json!(
            "Production Store library_counts, recent-read visible page and per-visible-card metadata; excludes Slint, covers, current detail panel, daily updates and offline usage worker"
        );
        operations.insert(format!("normal_library_visible_{size}"), measurement);
    }
    Ok(
        json!({"operations": operations, "books": arguments.books, "chapters_per_book": 12,
        "checks": {"normal_non_demo_fixture": true, "visible_returned_objects_bounded": true, "visible_metadata_correct": true}}),
    )
}

fn publication_operations(arguments: &Arguments) -> Result<Value> {
    let cache = CacheManager::open(&arguments.data_dir)?;
    let bytes = fs::read(arguments.data_dir.join("fixtures/tiny.png"))?;
    let base = page("publication-base", 0, None);
    let count = if arguments.sql_only {
        1
    } else {
        arguments.samples
    };
    let mut samples = Vec::new();
    let mut sql_collections = Vec::new();
    for index in 0..count {
        let value = page("publication-new", index, None);
        let write = cache.begin_raw(
            &value,
            Some("download:phase3-publication"),
            bytes.len() as u64,
        )?;
        fs::write(write.temp_path(), &bytes)?;
        cache.validate_path(write.temp_path())?;
        ensure!(
            cache.lookup_raw(&base)?.is_some(),
            "The dirty-LRU source artifact was lost"
        );
        if arguments.syscall_markers {
            eprintln!("PHASE3_COMMIT_BEGIN:{index}");
        }
        let destination = if arguments.sql_only {
            let (path, sql) = capture_sql(|| write.commit())?;
            sql_collections.push(sql);
            path
        } else {
            let start = Instant::now();
            let path = write.commit()?;
            samples.push(elapsed_ns(start));
            path
        };
        if arguments.syscall_markers {
            eprintln!("PHASE3_COMMIT_END:{index}");
        }
        ensure!(
            fs::read(&destination)? == bytes,
            "Published cache bytes changed"
        );
    }
    let database = Connection::open(arguments.data_dir.join("cache/shared-v1/index.sqlite3"))?;
    let pins: usize = database.query_row(
        "SELECT count(*) FROM pins WHERE owner='download:phase3-publication'",
        [],
        |row| row.get(0),
    )?;
    ensure!(pins == count, "The durable publication owners were lost");
    let operation = json!({"latency": if samples.is_empty() { Value::Null } else { latency(samples) },
        "sql_collections": sql_collections, "commits_requested": count,
        "scope": "CacheWrite::commit only, with one dirty access hint and a new durable raw owner; setup, file validation, correctness reads and drop excluded"});
    Ok(
        json!({"operations": {"dirty_lru_raw_publication": operation}, "checks": {"published_bytes_correct": true, "durable_owners_present": true}}),
    )
}

fn qualify() -> Result<Value> {
    let connection = Connection::open_in_memory()?;
    connection.execute_batch(
        "CREATE TABLE q(v INTEGER); CREATE TABLE audit(v INTEGER);
        CREATE TRIGGER q_insert AFTER INSERT ON q BEGIN INSERT INTO audit VALUES(NEW.v); END;
        INSERT INTO q VALUES(0),(1),(2),(3),(4),(5),(6),(7);",
    )?;
    let (_, sql) = capture_sql(|| {
        connection.execute_batch("BEGIN; INSERT INTO q VALUES(8); UPDATE q SET v=9 WHERE v=8;")?;
        for _ in 0..2 {
            let _: i64 = connection.query_row("SELECT sum(v) FROM q", [], |row| row.get(0))?;
        }
        connection.execute_batch("DELETE FROM q WHERE v=9; COMMIT;")?;
        Ok(())
    })?;
    ensure!(
        sql["dml"] == 3 && sql["reads"] == 2 && sql["begins"] == 1 && sql["commits"] == 1,
        "Statement executions were lost or trigger statements were counted as parents: {sql}"
    );
    ensure!(
        sql["vm_steps"].as_u64().unwrap_or(0) > 0
            && sql["fullscan_steps"].as_u64().unwrap_or(0) > 0
            && sql["subprograms"].as_u64().unwrap_or(0) > 0
            && sql["row_events"]["audit.insert"] == 1,
        "The SQL observer did not retain VM, scan, trigger and row work: {sql}"
    );
    Ok(
        json!({"operations": {"observer_qualification": {"sql": sql}}, "checks": {
        "repeated_statement_executions_counted": true, "trigger_parent_not_double_counted": true,
        "vm_and_fullscan_steps_present": true, "row_events_present": true}}),
    )
}

fn main() -> Result<()> {
    ensure!(
        cfg!(target_os = "linux"),
        "Run this benchmark only on the designated Linux remote worker"
    );
    let arguments = Arguments::parse();
    ensure!(
        (1..=512).contains(&arguments.samples),
        "The sample count is outside its limit"
    );
    ensure!(
        (2..=20_000).contains(&arguments.pages) && (18..=20_000).contains(&arguments.books),
        "The fixture size is outside its limit"
    );
    let _observer = if arguments.sql_only || arguments.scenario == Scenario::Qualify {
        Some(ObserverRegistration::install()?)
    } else {
        None
    };
    let mut result = match arguments.scenario {
        Scenario::Prepare => prepare(&arguments)?,
        Scenario::Qualify => qualify()?,
        Scenario::Store => store_operations(&arguments)?,
        Scenario::Render => render_operations(&arguments)?,
        Scenario::Projection => projection_operations(&arguments)?,
        Scenario::Publication => publication_operations(&arguments)?,
    };
    result["schema_version"] = json!(1);
    result["scenario"] = json!(format!("{:?}", arguments.scenario).to_ascii_lowercase());
    result["build_profile"] = json!(if cfg!(debug_assertions) {
        "debug"
    } else {
        "release"
    });
    result["build_features"] = json!(
        [
            ("desktop", cfg!(feature = "desktop")),
            ("kindle", cfg!(feature = "kindle"))
        ]
        .into_iter()
        .filter_map(|(name, enabled)| enabled.then_some(name))
        .collect::<Vec<_>>()
    );
    result["sql_only"] = json!(arguments.sql_only);
    result["comparison_kind"] = json!("candidate_measurement_with_explicit_algorithm_comparison");
    if arguments.scenario != Scenario::Prepare && arguments.scenario != Scenario::Qualify {
        result["fixture"] =
            serde_json::from_slice(&fs::read(arguments.data_dir.join("fixture.json"))?)?;
    }
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}
