//! Remote download benchmark entry point. See docs/DOWNLOAD_PERFORMANCE.md.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Cursor,
    path::PathBuf,
    sync::{Arc, Barrier},
    thread,
    time::{Duration, Instant},
};

use aidoku_kindle::{
    cache::CacheConfig,
    domain::{Book, Chapter, Page, Progress, SourceConfig},
    downloads::{DownloadEvent, DownloadOptions, DownloadWorker},
    imaging,
    sources::{FetchPriority, SourceManager},
    store::Store,
};
use anyhow::{Context, Result, bail, ensure};
use clap::{Parser, ValueEnum};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

#[derive(Clone, Copy, Debug, ValueEnum)]
enum Scenario {
    Throughput,
    WorkerBatch,
    ReaderDownloadShared,
    WorkerPause,
    WorkerResume,
    Singleflight,
    HeaderIdentity,
    ForegroundPriority,
    CacheLruPins,
    CacheLowSpace,
}

#[derive(Debug, Parser)]
struct Arguments {
    #[arg(long, value_enum)]
    scenario: Scenario,
    #[arg(long)]
    base_url: String,
    #[arg(long)]
    data_dir: PathBuf,
    #[arg(long, default_value_t = 6)]
    chapters: usize,
    #[arg(long, default_value_t = 12)]
    pages: u32,
    #[arg(long, default_value_t = 2)]
    concurrency: usize,
    #[arg(long, default_value_t = 1)]
    duplicate_enqueues: usize,
    #[arg(long, hide = true)]
    resource_report: Option<PathBuf>,
    #[arg(long)]
    allow_private_fixture_host: bool,
}

#[derive(Debug, Serialize)]
struct VerifiedFile {
    url: String,
    headers: BTreeMap<String, String>,
    path: PathBuf,
    sha256: String,
    size_bytes: u64,
    width: u32,
    height: u32,
}

struct Fixture {
    store: Store,
    database: PathBuf,
    book: Book,
    chapters: Vec<Chapter>,
    source: SourceConfig,
}

fn fixture(arguments: &Arguments, chapter_count: usize) -> Result<Fixture> {
    fs::create_dir_all(&arguments.data_dir)?;
    let database = arguments.data_dir.join("state.sqlite");
    let store = Store::open(&database)?;
    let source = SourceConfig {
        id: "benchmark".into(),
        kind: "komga".into(),
        name: "Download benchmark fixture".into(),
        base_url: arguments.base_url.clone(),
        token: "fixture-token".into(),
        enabled: true,
        ..SourceConfig::default()
    };
    store.save_source(&source)?;
    let book = Book {
        id: "benchmark:book".into(),
        source_id: source.id.clone(),
        remote_id: "book".into(),
        title: "Deterministic benchmark pages".into(),
        chapter_count: chapter_count as u32,
        in_library: true,
        ..Book::default()
    };
    let chapters = (1..=chapter_count)
        .map(|index| Chapter {
            id: format!("benchmark:c{index:02}"),
            book_id: book.id.clone(),
            source_id: source.id.clone(),
            remote_id: format!("c{index:02}"),
            number: index as f64,
            title: format!("Chapter {index}"),
            page_count: arguments.pages,
            ..Chapter::default()
        })
        .collect::<Vec<_>>();
    store.upsert_book(&book)?;
    store.save_chapters(&book.id, &chapters)?;
    Ok(Fixture {
        store,
        database,
        book,
        chapters,
        source,
    })
}

fn remote_page(arguments: &Arguments, name: &str) -> Page {
    Page {
        url: format!("{}/images/{name}", arguments.base_url),
        headers: BTreeMap::from([("X-API-Key".into(), "fixture-token".into())]),
        local_path: None,
    }
}

fn verify(pages: &[Page]) -> Result<Vec<VerifiedFile>> {
    pages
        .iter()
        .map(|page| {
            let path = page
                .local_path
                .as_ref()
                .context("A benchmark page has no persisted path")?;
            let bytes = fs::read(path).context("Read a completed benchmark image")?;
            imaging::validate_page(&bytes).context("Decode a completed benchmark image")?;
            let (width, height) = image::ImageReader::new(Cursor::new(&bytes))
                .with_guessed_format()?
                .into_dimensions()?;
            Ok(VerifiedFile {
                url: page.url.clone(),
                headers: page.headers.clone(),
                path: path.clone(),
                sha256: format!("{:x}", Sha256::digest(&bytes)),
                size_bytes: bytes.len() as u64,
                width,
                height,
            })
        })
        .collect()
}

fn report(files: Vec<VerifiedFile>, checks: Value, details: Value) -> Value {
    json!({
        "verified_pages": files.len(),
        "files": files,
        "checks": checks,
        "details": details,
    })
}

fn throughput(arguments: &Arguments) -> Result<Value> {
    ensure!(
        (1..=2).contains(&arguments.concurrency),
        "Use one or two page download workers"
    );
    let fixture = fixture(arguments, arguments.chapters)?;
    let started = Instant::now();
    with_worker_configured(
        arguments,
        &fixture,
        DownloadOptions {
            offline: false,
            page_concurrency: arguments.concurrency,
        },
        |worker| {
            worker.enqueue_many(&fixture.book, &fixture.chapters)?;
            wait_completed(worker, &fixture)
        },
    )?;
    let fetch_milliseconds = started.elapsed().as_secs_f64() * 1000.0;
    let files = verified_chapters(&fixture)?;
    let reopened = Store::open(&fixture.database)?;
    ensure!(
        reopened
            .downloads()?
            .iter()
            .all(|job| job.state == "completed" && job.completed == job.total),
        "The measured worker did not persist its completed jobs"
    );
    Ok(report(
        files,
        json!({"all_files_decoded": true, "completed_jobs_persisted": true}),
        json!({
            "page_concurrency": arguments.concurrency,
            "fetch_milliseconds": fetch_milliseconds,
            "baseline": "The same production DownloadWorker with page_concurrency=1",
        }),
    ))
}

fn with_worker<T>(
    arguments: &Arguments,
    fixture: &Fixture,
    body: impl FnOnce(&DownloadWorker) -> Result<T>,
) -> Result<T> {
    with_worker_configured(arguments, fixture, DownloadOptions::default(), body)
}

fn with_worker_configured<T>(
    arguments: &Arguments,
    fixture: &Fixture,
    options: DownloadOptions,
    body: impl FnOnce(&DownloadWorker) -> Result<T>,
) -> Result<T> {
    let worker = DownloadWorker::start_configured(
        arguments.data_dir.clone(),
        fixture.database.clone(),
        options,
    )?;
    let result = body(&worker);
    worker.shutdown();
    ensure!(
        worker.wait_stopped(Duration::from_secs(10)),
        "The download worker did not close its database during teardown"
    );
    check_events(&worker)?;
    result
}

fn check_events(worker: &DownloadWorker) -> Result<()> {
    for event in worker.drain_events() {
        match event {
            DownloadEvent::Error(message) => bail!("The worker failed: {message}"),
            DownloadEvent::Failed(job) => bail!("Chapter {} failed: {}", job.chapter_id, job.error),
            _ => {}
        }
    }
    Ok(())
}

fn wait_completed(worker: &DownloadWorker, fixture: &Fixture) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        check_events(worker)?;
        let jobs = fixture.store.downloads()?;
        if fixture.chapters.iter().all(|chapter| {
            jobs.iter().any(|job| {
                job.chapter_id == chapter.id
                    && job.state == "completed"
                    && job.completed == job.total
                    && job.total == chapter.page_count
            })
        }) {
            return Ok(());
        }
        ensure!(
            Instant::now() < deadline,
            "Timed out waiting for completed, persisted downloads"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

fn verified_chapters(fixture: &Fixture) -> Result<Vec<VerifiedFile>> {
    let mut files = Vec::new();
    for chapter in &fixture.chapters {
        let saved = fixture
            .store
            .chapter(&chapter.id)?
            .context("A completed chapter disappeared")?;
        ensure!(
            saved.downloaded,
            "A completed chapter has no downloaded marker"
        );
        let pages = fixture.store.pages(&chapter.id)?;
        ensure!(
            pages.len() == chapter.page_count as usize,
            "A completed chapter lost its page manifest"
        );
        files.extend(verify(&pages)?);
    }
    Ok(files)
}

fn worker_batch(arguments: &Arguments) -> Result<Value> {
    let fixture = fixture(arguments, arguments.chapters)?;
    let existing = fixture.store.downloads()?;
    let replaying_completed = fixture.chapters.iter().all(|chapter| {
        existing
            .iter()
            .any(|job| job.chapter_id == chapter.id && job.state == "completed")
    });
    let queue_barrier = if replaying_completed {
        let chapter = Chapter {
            id: "benchmark:queue-barrier".into(),
            book_id: fixture.book.id.clone(),
            source_id: fixture.source.id.clone(),
            remote_id: "queue-barrier".into(),
            number: 1000.0,
            page_count: 1,
            ..Chapter::default()
        };
        fixture
            .store
            .save_chapters(&fixture.book.id, std::slice::from_ref(&chapter))?;
        let page = fixture
            .store
            .pages(&fixture.chapters[0].id)?
            .into_iter()
            .next()
            .context("A cache replay has no local page for its queue barrier")?;
        fixture.store.save_pages(&chapter.id, &[page])?;
        fixture.store.delete_download(&chapter.id)?;
        Some(chapter)
    } else {
        None
    };
    with_worker(arguments, &fixture, |worker| {
        for _ in 0..arguments.duplicate_enqueues {
            worker.enqueue_many(&fixture.book, &fixture.chapters)?;
        }
        if let Some(barrier) = &queue_barrier {
            worker.enqueue(&fixture.book, barrier)?;
        }
        wait_completed(worker, &fixture)?;
        if let Some(barrier) = &queue_barrier {
            let deadline = Instant::now() + Duration::from_secs(20);
            loop {
                check_events(worker)?;
                if fixture.store.downloads()?.iter().any(|job| {
                    job.chapter_id == barrier.id && job.state == "completed" && job.completed == 1
                }) {
                    break;
                }
                ensure!(
                    Instant::now() < deadline,
                    "The cache replay queue barrier was not processed"
                );
                thread::sleep(Duration::from_millis(5));
            }
        }
        Ok(())
    })?;
    let files = verified_chapters(&fixture)?;
    let reopened = Store::open(&fixture.database)?;
    let all_jobs = reopened.downloads()?;
    let mut expected = fixture
        .chapters
        .iter()
        .map(|chapter| chapter.id.clone())
        .collect::<BTreeSet<_>>();
    if let Some(barrier) = &queue_barrier {
        expected.insert(barrier.id.clone());
    }
    ensure!(
        all_jobs.len() == expected.len()
            && all_jobs
                .iter()
                .map(|job| job.chapter_id.clone())
                .collect::<BTreeSet<_>>()
                == expected,
        "The queue contains unexpected or duplicate persisted jobs"
    );
    let jobs = all_jobs
        .into_iter()
        .filter(|job| {
            fixture
                .chapters
                .iter()
                .any(|chapter| chapter.id == job.chapter_id)
        })
        .collect::<Vec<_>>();
    ensure!(
        jobs.len() == fixture.chapters.len(),
        "Duplicate enqueue created duplicate persisted jobs"
    );
    ensure!(
        jobs.iter().all(|job| job.state == "completed"
            && job.completed == arguments.pages
            && job.total == arguments.pages),
        "A completed job was not durable"
    );
    Ok(report(
        files,
        json!({"completed_jobs_persisted": true, "one_job_per_chapter": true, "cache_replay_queue_barrier_processed": !replaying_completed || queue_barrier.is_some()}),
        json!({"jobs": jobs, "local_manifest_queue_barrier": queue_barrier.is_some()}),
    ))
}

fn reader_download_shared(arguments: &Arguments) -> Result<Value> {
    let fixture = fixture(arguments, 1)?;
    let manager = SourceManager::new(&arguments.data_dir, std::slice::from_ref(&fixture.source))?;
    let mut pages = manager.pages(&fixture.book, &fixture.chapters[0])?;
    let mut reader_paths = Vec::new();
    for page in pages.iter_mut().take(4) {
        let path = manager.fetch_page(
            page,
            FetchPriority::Interactive,
            Some("reader:benchmark:c01"),
        )?;
        page.local_path = Some(path.clone());
        reader_paths.push(path);
    }
    fixture.store.save_progress(&Progress {
        book_id: fixture.book.id.clone(),
        chapter_id: fixture.chapters[0].id.clone(),
        page: 3,
        total: arguments.pages,
        ..Progress::default()
    })?;
    with_worker(arguments, &fixture, |worker| {
        worker.enqueue_many(&fixture.book, &fixture.chapters)?;
        wait_completed(worker, &fixture)
    })?;
    let downloaded = fixture.store.pages(&fixture.chapters[0].id)?;
    ensure!(
        downloaded
            .iter()
            .take(4)
            .zip(reader_paths)
            .all(|(page, path)| page.local_path.as_ref() == Some(&path)),
        "Reader and downloader did not use the same canonical files"
    );
    ensure!(
        fixture
            .store
            .chapter_progress(&fixture.chapters[0].id)?
            .is_some_and(|progress| progress.page == 3),
        "Downloading changed the reader's saved progress"
    );
    Ok(report(
        verified_chapters(&fixture)?,
        json!({"reader_download_canonical_paths_match": true, "reading_progress_preserved": true}),
        json!({"reader_pages": 4}),
    ))
}

fn worker_pause(arguments: &Arguments) -> Result<Value> {
    let fixture = fixture(arguments, 1)?;
    let chapter = &fixture.chapters[0];
    with_worker(arguments, &fixture, |worker| {
        worker.enqueue(&fixture.book, chapter)?;
        let deadline = Instant::now() + Duration::from_secs(30);
        let mut pause_sent = false;
        loop {
            check_events(worker)?;
            if let Some(job) = fixture
                .store
                .downloads()?
                .into_iter()
                .find(|job| job.chapter_id == chapter.id)
            {
                if job.state == "paused" {
                    ensure!(
                        pause_sent,
                        "The chapter was paused before this benchmark requested it"
                    );
                    break;
                }
                if !pause_sent && job.completed >= 3 {
                    worker.pause(&chapter.id)?;
                    pause_sent = true;
                }
            }
            ensure!(
                Instant::now() < deadline,
                "Timed out waiting for a persisted pause"
            );
            thread::sleep(Duration::from_millis(5));
        }
        Ok(())
    })?;
    let reopened = Store::open(&fixture.database)?;
    let job = reopened
        .downloads()?
        .into_iter()
        .find(|job| job.chapter_id == chapter.id)
        .context("The paused job disappeared")?;
    ensure!(
        job.state == "paused" && job.completed > 0 && job.completed < job.total,
        "The durable pause state is incorrect"
    );
    let saved = reopened
        .pages(&chapter.id)?
        .into_iter()
        .filter(|page| page.local_path.is_some())
        .collect::<Vec<_>>();
    let files = verify(&saved)?;
    ensure!(
        files.len() == job.completed as usize,
        "Persisted partial progress disagrees with valid files"
    );
    Ok(report(
        files,
        json!({"pause_persisted": true, "partial_progress_matches_files": true}),
        json!({"job": job}),
    ))
}

fn worker_resume(arguments: &Arguments) -> Result<Value> {
    let fixture = fixture(arguments, 1)?;
    let job = fixture
        .store
        .downloads()?
        .into_iter()
        .find(|job| job.chapter_id == fixture.chapters[0].id)
        .context("There is no partial job to resume")?;
    ensure!(
        job.state == "paused",
        "The previous process did not leave a paused job"
    );
    let previous_completed = job.completed;
    with_worker(arguments, &fixture, |worker| {
        worker.resume(&fixture.chapters[0].id)?;
        wait_completed(worker, &fixture)
    })?;
    Ok(report(
        verified_chapters(&fixture)?,
        json!({"restart_completed_persisted_manifest": true}),
        json!({"previous_completed": previous_completed}),
    ))
}

fn singleflight(arguments: &Arguments) -> Result<Value> {
    fs::create_dir_all(&arguments.data_dir)?;
    let page = remote_page(arguments, "singleflight");
    let managers = [
        SourceManager::new(&arguments.data_dir, &[])?,
        SourceManager::new(&arguments.data_dir, &[])?,
    ];
    let barrier = Arc::new(Barrier::new(9));
    let outcomes = thread::scope(|scope| -> Result<Vec<PathBuf>> {
        let mut handles = Vec::new();
        for index in 0..8 {
            let (manager, page, barrier) =
                (managers[index % 2].clone(), page.clone(), barrier.clone());
            handles.push(scope.spawn(move || {
                barrier.wait();
                let (priority, owner) = if index % 2 == 0 {
                    (FetchPriority::Interactive, "reader:singleflight")
                } else {
                    (FetchPriority::Background, "download:singleflight")
                };
                manager.fetch_page(&page, priority, Some(owner))
            }));
        }
        barrier.wait();
        handles
            .into_iter()
            .map(|handle| {
                handle
                    .join()
                    .map_err(|_| anyhow::anyhow!("A fetch thread panicked"))?
            })
            .collect()
    })?;
    ensure!(
        outcomes.iter().collect::<BTreeSet<_>>().len() == 1,
        "Identical requests produced different canonical paths"
    );
    let saved = Page {
        local_path: outcomes.first().cloned(),
        ..page
    };
    Ok(report(
        verify(&[saved])?,
        json!({"concurrent_readers_share_canonical_file": true}),
        json!({"callers": 8, "independent_source_managers": 2}),
    ))
}

fn header_identity(arguments: &Arguments) -> Result<Value> {
    fs::create_dir_all(&arguments.data_dir)?;
    let manager = SourceManager::new(&arguments.data_dir, &[])?;
    let mut first = remote_page(arguments, "headers");
    first
        .headers
        .insert("X-Fixture-Variant".into(), "first".into());
    let mut second = first.clone();
    second
        .headers
        .insert("X-Fixture-Variant".into(), "second".into());
    first.local_path = Some(manager.fetch_page(&first, FetchPriority::Background, None)?);
    second.local_path = Some(manager.fetch_page(&second, FetchPriority::Background, None)?);
    ensure!(
        first.local_path != second.local_path,
        "Different request headers shared one canonical file"
    );
    Ok(report(
        verify(&[first, second])?,
        json!({"headers_use_distinct_cache_keys": true}),
        json!({}),
    ))
}

fn foreground_priority(arguments: &Arguments) -> Result<Value> {
    fs::create_dir_all(&arguments.data_dir)?;
    let manager = SourceManager::new(&arguments.data_dir, &[])?;
    let barrier = Arc::new(Barrier::new(25));
    let (front_latency, mut background) = thread::scope(|scope| -> Result<(f64, Vec<Page>)> {
        let mut handles = Vec::new();
        for index in 1..=24 {
            let (manager, barrier) = (manager.clone(), barrier.clone());
            let mut page = remote_page(arguments, &format!("background-{index}"));
            handles.push(scope.spawn(move || -> Result<Page> {
                barrier.wait();
                page.local_path =
                    Some(manager.fetch_page(&page, FetchPriority::Background, None)?);
                Ok(page)
            }));
        }
        barrier.wait();
        reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(6))
            .build()?
            .get(format!("{}/__control/background-ready", arguments.base_url))
            .header("X-API-Key", "fixture-token")
            .send()?
            .error_for_status()?
            .text()?;
        let mut front = remote_page(arguments, "front");
        let started = Instant::now();
        front.local_path =
            Some(manager.fetch_page(&front, FetchPriority::Interactive, Some("reader:front"))?);
        let front_latency = started.elapsed().as_secs_f64() * 1000.0;
        let mut pages = handles
            .into_iter()
            .map(|handle| {
                handle
                    .join()
                    .map_err(|_| anyhow::anyhow!("A background thread panicked"))?
            })
            .collect::<Result<Vec<_>>>()?;
        pages.push(front);
        Ok((front_latency, pages))
    })?;
    background.sort_by(|first, second| first.url.cmp(&second.url));
    Ok(report(
        verify(&background)?,
        json!({"all_background_and_foreground_files_decoded": true}),
        json!({"background_callers": 24, "foreground_latency_ms": front_latency}),
    ))
}

fn cache_lru_pins(arguments: &Arguments) -> Result<Value> {
    let fixture = fixture(arguments, 1)?;
    with_worker(arguments, &fixture, |worker| {
        worker.enqueue_many(&fixture.book, &fixture.chapters)?;
        wait_completed(worker, &fixture)
    })?;
    let downloaded = fixture.store.pages(&fixture.chapters[0].id)?;
    let manager = SourceManager::new(&arguments.data_dir, std::slice::from_ref(&fixture.source))?;
    manager.cache().set_config(CacheConfig {
        max_unpinned_bytes: u64::MAX,
        min_free_bytes: 0,
    })?;
    let mut old = remote_page(arguments, "lru-old");
    let mut new = remote_page(arguments, "lru-new");
    old.local_path = Some(manager.fetch_page(&old, FetchPriority::Prefetch, None)?);
    new.local_path = Some(manager.fetch_page(&new, FetchPriority::Prefetch, None)?);
    let new_size = fs::metadata(new.local_path.as_ref().unwrap())?.len();
    manager.cache().set_config(CacheConfig {
        max_unpinned_bytes: new_size,
        min_free_bytes: 0,
    })?;
    manager.cache().prune()?;
    ensure!(
        !old.local_path.as_ref().unwrap().exists(),
        "LRU retained an older, unpinned file above its byte budget"
    );
    ensure!(
        manager.cache().lookup_raw(&old)?.is_none(),
        "The cache still exposes the evicted page"
    );
    ensure!(
        new.local_path.as_ref().unwrap().is_file(),
        "LRU evicted the newer page before the old page"
    );
    for pinned in &downloaded {
        ensure!(
            pinned
                .local_path
                .as_ref()
                .is_some_and(|path| path.is_file()),
            "LRU removed a page pinned by the production download worker"
        );
        ensure!(
            manager.cache().lookup_raw(pinned)? == pinned.local_path,
            "A completed download pin lost its canonical manifest entry"
        );
    }
    let mut files = verify(&downloaded)?;
    files.extend(verify(&[new])?);
    Ok(report(
        files,
        json!({"download_pin_survives_lru": true, "oldest_unpinned_page_evicted": true}),
        json!({"unpinned_budget_bytes": new_size, "production_download_pins": downloaded.len(), "evicted_path": old.local_path, "cache": manager.cache().stats()}),
    ))
}

fn cache_low_space(arguments: &Arguments) -> Result<Value> {
    fs::create_dir_all(&arguments.data_dir)?;
    let manager = SourceManager::new(&arguments.data_dir, &[])?;
    manager.cache().set_config(CacheConfig {
        max_unpinned_bytes: u64::MAX,
        min_free_bytes: 0,
    })?;
    let mut existing = remote_page(arguments, "lru-pin");
    existing.local_path = Some(manager.fetch_page(
        &existing,
        FetchPriority::Background,
        Some("download:low-space-existing"),
    )?);
    let before = verify(std::slice::from_ref(&existing))?;
    manager.cache().set_config(CacheConfig {
        max_unpinned_bytes: u64::MAX,
        min_free_bytes: u64::MAX,
    })?;
    let new_page = remote_page(arguments, "low-space");
    let outcome = manager.fetch_page(
        &new_page,
        FetchPriority::Background,
        Some("download:low-space-new"),
    );
    let error =
        outcome.expect_err("A new page unexpectedly succeeded below the configured disk reserve");
    let message = format!("{error:#}");
    ensure!(
        message.to_ascii_lowercase().contains("space")
            || message.to_ascii_lowercase().contains("disk"),
        "The low-space failure does not explain the storage problem: {message}"
    );
    let after = verify(std::slice::from_ref(&existing))?;
    ensure!(
        before[0].sha256 == after[0].sha256,
        "The failed download changed existing bytes"
    );
    ensure!(
        manager.cache().lookup_raw(&existing)? == existing.local_path,
        "The failed download damaged an existing manifest entry"
    );
    ensure!(
        manager.cache().lookup_raw(&new_page)?.is_none(),
        "A failed low-space request published an incomplete cache entry"
    );
    Ok(report(
        after,
        json!({"low_space_preserves_existing": true, "low_space_has_clear_error": true, "failed_page_not_published": true}),
        json!({"injected_min_free_bytes": u64::MAX, "error": message, "cache": manager.cache().stats()}),
    ))
}

#[cfg(target_os = "linux")]
fn run_measured_child(path: &std::path::Path) -> Result<()> {
    let mut command = std::process::Command::new(std::env::current_exe()?);
    let mut arguments = std::env::args_os().skip(1);
    while let Some(argument) = arguments.next() {
        if argument == "--resource-report" {
            arguments
                .next()
                .context("The resource report has no output path")?;
        } else if !argument.to_string_lossy().starts_with("--resource-report=") {
            command.arg(argument);
        }
    }
    let started = Instant::now();
    let child = command
        .spawn()
        .context("Start the measured native benchmark child")?;
    let mut status = 0;
    // The launcher has not created a client, fixture, cache, or worker thread.
    // A fresh child therefore cannot inherit the fixture server's RSS peak.
    let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
    loop {
        let waited = unsafe { libc::wait4(child.id() as libc::pid_t, &mut status, 0, &mut usage) };
        if waited >= 0 {
            break;
        }
        let error = std::io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::EINTR) {
            return Err(error).context("Reap the measured native benchmark child");
        }
    }
    let code = if libc::WIFEXITED(status) {
        libc::WEXITSTATUS(status)
    } else {
        128 + libc::WTERMSIG(status)
    };
    let measurement = json!({
        "pid": child.id(),
        "exit_code": code,
        "wall_seconds": started.elapsed().as_secs_f64(),
        "peak_rss_kib": usage.ru_maxrss,
        "user_cpu_seconds": usage.ru_utime.tv_sec as f64 + usage.ru_utime.tv_usec as f64 / 1_000_000.0,
        "system_cpu_seconds": usage.ru_stime.tv_sec as f64 + usage.ru_stime.tv_usec as f64 / 1_000_000.0,
        "rss_method": "Linux wait4 from a Rust launcher before creating clients or workers",
        "command": std::env::args_os().map(|argument| argument.to_string_lossy().into_owned()).collect::<Vec<_>>(),
    });
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_vec_pretty(&measurement)?)?;
    ensure!(
        code == 0,
        "The measured native benchmark child exited {code}"
    );
    Ok(())
}

#[cfg(not(target_os = "linux"))]
fn run_measured_child(_path: &std::path::Path) -> Result<()> {
    bail!("Native resource reports require Linux wait4")
}

fn main() -> Result<()> {
    let arguments = Arguments::parse();
    if let Some(path) = &arguments.resource_report {
        return run_measured_child(path);
    }
    let endpoint = reqwest::Url::parse(&arguments.base_url)?;
    let allowed_host = match endpoint.host() {
        Some(url::Host::Ipv4(address)) => {
            address.is_loopback() || arguments.allow_private_fixture_host && address.is_private()
        }
        Some(url::Host::Ipv6(address)) => {
            address.is_loopback()
                || arguments.allow_private_fixture_host && address.is_unique_local()
        }
        _ => false,
    };
    ensure!(
        endpoint.scheme() == "http"
            && allowed_host
            && endpoint.username().is_empty()
            && endpoint.password().is_none(),
        "Benchmarks require a loopback fixture, or an explicitly allowed private guest-network fixture"
    );
    ensure!(
        arguments.chapters > 0 && arguments.pages >= 6,
        "Use at least one chapter and six pages"
    );
    let started = Instant::now();
    let mut output = match arguments.scenario {
        Scenario::Throughput => throughput(&arguments),
        Scenario::WorkerBatch => worker_batch(&arguments),
        Scenario::ReaderDownloadShared => reader_download_shared(&arguments),
        Scenario::WorkerPause => worker_pause(&arguments),
        Scenario::WorkerResume => worker_resume(&arguments),
        Scenario::Singleflight => singleflight(&arguments),
        Scenario::HeaderIdentity => header_identity(&arguments),
        Scenario::ForegroundPriority => foreground_priority(&arguments),
        Scenario::CacheLruPins => cache_lru_pins(&arguments),
        Scenario::CacheLowSpace => cache_low_space(&arguments),
    }?;
    output["scenario_work_milliseconds"] = json!(started.elapsed().as_secs_f64() * 1000.0);
    output["build_profile"] = json!(if cfg!(debug_assertions) {
        "dev"
    } else {
        "release"
    });
    output["build_features"] = json!(
        [
            ("desktop", cfg!(feature = "desktop")),
            ("kindle", cfg!(feature = "kindle")),
        ]
        .into_iter()
        .filter_map(|(name, enabled)| enabled.then_some(name))
        .collect::<Vec<_>>()
    );
    println!("{}", serde_json::to_string(&output)?);
    Ok(())
}
