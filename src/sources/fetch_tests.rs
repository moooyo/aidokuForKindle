//! End-to-end regressions for streamed transfers and canonical cache ownership.

use std::{
    collections::BTreeMap,
    fs,
    io::{Cursor, Read, Write},
    net::{Shutdown, TcpListener, TcpStream},
    path::{Path, PathBuf},
    sync::{
        Arc, Barrier, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};

use super::{DownloadLimits, FetchPriority, SourceManager};
use crate::{
    cache::CacheManager,
    domain::{Page, SourceConfig},
};

#[derive(Clone)]
struct Reply {
    declared_length: usize,
    body: Vec<u8>,
    delay: Duration,
}

#[derive(Default)]
struct ResponseActivity {
    active: AtomicUsize,
    peak: AtomicUsize,
}

struct ActiveResponse(Arc<ResponseActivity>);

impl ActiveResponse {
    fn begin(activity: &Arc<ResponseActivity>) -> Self {
        let current = activity.active.fetch_add(1, Ordering::SeqCst) + 1;
        activity.peak.fetch_max(current, Ordering::SeqCst);
        Self(activity.clone())
    }
}

impl Drop for ActiveResponse {
    fn drop(&mut self) {
        self.0.active.fetch_sub(1, Ordering::SeqCst);
    }
}

struct FixtureServer {
    base_url: String,
    done: Arc<AtomicBool>,
    requests: Arc<Mutex<Vec<String>>>,
    worker: Option<thread::JoinHandle<std::io::Result<()>>>,
}

impl FixtureServer {
    fn new(replies: Vec<Reply>) -> Self {
        Self::with_activity(replies, None)
    }

    fn with_activity(replies: Vec<Reply>, activity: Option<Arc<ResponseActivity>>) -> Self {
        assert!(!replies.is_empty());
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let done = Arc::new(AtomicBool::new(false));
        let requests = Arc::new(Mutex::new(Vec::new()));
        let stopped = done.clone();
        let captured = requests.clone();
        let worker = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(5);
            while !stopped.load(Ordering::Acquire) && Instant::now() < deadline {
                let mut connection = match listener.accept() {
                    Ok((connection, _)) => connection,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(error) => return Err(error),
                };
                connection.set_read_timeout(Some(Duration::from_millis(250)))?;
                connection.set_write_timeout(Some(Duration::from_millis(250)))?;
                let request = read_request(&mut connection, deadline)?;
                let index = {
                    let mut requests = captured.lock().unwrap();
                    let index = requests.len();
                    requests.push(request);
                    index
                };
                // Continue serving duplicates as well: the coalescing regression
                // must fail on an extra GET rather than hang a waiting client.
                let reply = &replies[index.min(replies.len() - 1)];
                let active = activity.as_ref().map(ActiveResponse::begin);
                if !reply.delay.is_zero() {
                    thread::sleep(reply.delay);
                }
                let headers = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    reply.declared_length,
                );
                connection.write_all(headers.as_bytes())?;
                if let Some((last, prefix)) = reply.body.split_last() {
                    connection.write_all(prefix)?;
                    // The client cannot complete the response before the last
                    // byte. End preparation tracking first so scheduler cleanup
                    // does not create an artificial overlap with its successor.
                    drop(active);
                    connection.write_all(std::slice::from_ref(last))?;
                } else {
                    drop(active);
                }
                let _ = connection.shutdown(Shutdown::Both);
            }
            Ok(())
        });
        Self {
            base_url,
            done,
            requests,
            worker: Some(worker),
        }
    }

    fn url(&self, path: &str) -> String {
        format!("{}{path}", self.base_url)
    }

    fn finish(mut self) -> Vec<String> {
        self.done.store(true, Ordering::Release);
        self.worker.take().unwrap().join().unwrap().unwrap();
        self.requests.lock().unwrap().clone()
    }
}

impl Drop for FixtureServer {
    fn drop(&mut self) {
        self.done.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn read_request(connection: &mut TcpStream, deadline: Instant) -> std::io::Result<String> {
    let mut request = Vec::new();
    let mut buffer = [0_u8; 1024];
    while !request.windows(4).any(|window| window == b"\r\n\r\n") {
        if Instant::now() >= deadline || request.len() >= 64 * 1024 {
            return Err(std::io::Error::other(
                "Fixture request exceeded its time or size limit",
            ));
        }
        let read = connection.read(&mut buffer)?;
        if read == 0 {
            return Err(std::io::Error::from(std::io::ErrorKind::UnexpectedEof));
        }
        request.extend_from_slice(&buffer[..read]);
    }
    Ok(String::from_utf8_lossy(&request).into_owned())
}

fn png(shade: u8) -> Vec<u8> {
    let mut output = Cursor::new(Vec::new());
    image::GrayImage::from_pixel(2, 3, image::Luma([shade]))
        .write_to(&mut output, image::ImageFormat::Png)
        .unwrap();
    output.into_inner()
}

fn manager(path: &Path) -> SourceManager {
    SourceManager::new_with_download_limits(
        path,
        &[],
        DownloadLimits {
            background: 2,
            foreground: 1,
        },
    )
    .unwrap()
}

fn generated_files(data_dir: &Path) -> Vec<PathBuf> {
    fn collect(directory: &Path, files: &mut Vec<PathBuf>) {
        if !directory.exists() {
            return;
        }
        for entry in fs::read_dir(directory).unwrap() {
            let entry = entry.unwrap();
            if entry.file_type().unwrap().is_dir() {
                collect(&entry.path(), files);
            } else {
                files.push(entry.path());
            }
        }
    }
    let mut files = Vec::new();
    for kind in ["raw", "processed", "temp"] {
        collect(&data_dir.join("cache/shared-v1").join(kind), &mut files);
    }
    files.sort();
    files
}

#[test]
fn truncated_stream_is_not_published_and_the_same_request_can_retry() {
    let bytes = png(220);
    let server = FixtureServer::new(vec![
        Reply {
            declared_length: bytes.len(),
            body: bytes[..bytes.len() / 2].to_vec(),
            delay: Duration::ZERO,
        },
        Reply {
            declared_length: bytes.len(),
            body: bytes.clone(),
            delay: Duration::ZERO,
        },
    ]);
    let temporary = tempfile::tempdir().unwrap();
    let manager = manager(temporary.path());
    let page = Page {
        url: server.url("/retry.png"),
        ..Page::default()
    };

    assert!(
        manager
            .fetch_page(&page, FetchPriority::Interactive, None)
            .is_err()
    );
    assert!(manager.cache().lookup_raw(&page).unwrap().is_none());
    assert!(generated_files(temporary.path()).is_empty());
    let failed = manager.cache().stats();
    assert_eq!(failed.entries, 0);
    assert_eq!(failed.writes_inflight, 0);
    assert_eq!(failed.reserved_bytes, 0);

    let path = manager
        .fetch_page(&page, FetchPriority::Interactive, None)
        .unwrap();
    assert_eq!(path, path.canonicalize().unwrap());
    assert_eq!(
        manager.cache().lookup_raw(&page).unwrap(),
        Some(path.clone())
    );
    assert_eq!(fs::read(&path).unwrap(), bytes);
    assert_eq!(
        image::ImageReader::open(&path)
            .unwrap()
            .with_guessed_format()
            .unwrap()
            .into_dimensions()
            .unwrap(),
        (2, 3)
    );
    assert_eq!(generated_files(temporary.path()), vec![path]);
    assert_eq!(manager.cache().stats().commits, 1);
    let requests = server.finish();
    assert_eq!(requests.len(), 2);
    assert!(
        requests
            .iter()
            .all(|request| request.starts_with("GET /retry.png "))
    );
}

#[test]
fn oversized_content_length_is_rejected_before_body_or_cache_allocation() {
    let server = FixtureServer::new(vec![Reply {
        declared_length: 32 * 1024 * 1024 + 1,
        body: Vec::new(),
        delay: Duration::ZERO,
    }]);
    let temporary = tempfile::tempdir().unwrap();
    let manager = manager(temporary.path());
    let page = Page {
        url: server.url("/oversized.png"),
        ..Page::default()
    };

    let error = manager
        .fetch_page(&page, FetchPriority::Interactive, None)
        .unwrap_err();
    assert!(
        format!("{error:#}").contains("32 MiB"),
        "Unexpected rejection: {error:#}"
    );
    assert!(manager.cache().lookup_raw(&page).unwrap().is_none());
    let stats = manager.cache().stats();
    assert_eq!(stats.entries, 0);
    assert_eq!(stats.commits, 0);
    assert_eq!(stats.peak_reserved_bytes, 0);
    assert_eq!(stats.writes_inflight, 0);
    assert!(generated_files(temporary.path()).is_empty());
    assert_eq!(server.finish().len(), 1);
}

#[test]
fn url_less_external_pages_keep_distinct_canonical_paths_without_cache_copies() {
    let temporary = tempfile::tempdir().unwrap();
    let data_dir = temporary.path().join("data");
    let external = temporary.path().join("external pages");
    fs::create_dir_all(&external).unwrap();
    let first = external.join("first.png");
    let second = external.join("second.png");
    let first_bytes = png(30);
    let second_bytes = png(240);
    fs::write(&first, &first_bytes).unwrap();
    fs::write(&second, &second_bytes).unwrap();
    let first_page = Page {
        local_path: Some(first.clone()),
        ..Page::default()
    };
    let second_page = Page {
        local_path: Some(external.join(".").join("second.png")),
        ..Page::default()
    };
    assert_ne!(
        CacheManager::page_key(&first_page),
        CacheManager::page_key(&second_page)
    );

    let manager = manager(&data_dir);
    let first_result = manager
        .fetch_page(
            &first_page,
            FetchPriority::Interactive,
            Some("reader:local-one"),
        )
        .unwrap();
    let second_result = manager
        .fetch_page(
            &second_page,
            FetchPriority::Interactive,
            Some("reader:local-two"),
        )
        .unwrap();
    assert_eq!(first_result, first.canonicalize().unwrap());
    assert_eq!(second_result, second.canonicalize().unwrap());
    assert_ne!(first_result, second_result);
    assert_eq!(fs::read(first_result).unwrap(), first_bytes);
    assert_eq!(fs::read(second_result).unwrap(), second_bytes);
    let stats = manager.cache().stats();
    assert_eq!(stats.external_hits, 2);
    assert_eq!(stats.entries, 0);
    assert_eq!(stats.commits, 0);
    assert!(generated_files(&data_dir).is_empty());
}

#[test]
fn independent_managers_coalesce_header_case_variants_and_keep_both_pin_owners() {
    let bytes = png(180);
    let server = FixtureServer::new(vec![Reply {
        declared_length: bytes.len(),
        body: bytes.clone(),
        delay: Duration::from_millis(100),
    }]);
    let temporary = tempfile::tempdir().unwrap();
    let first_manager = manager(temporary.path());
    let second_manager = manager(&temporary.path().join("."));
    let first_page = Page {
        url: server.url("/shared.png"),
        headers: BTreeMap::from([("X-Image-Token".into(), "same-value".into())]),
        local_path: None,
    };
    let second_page = Page {
        headers: BTreeMap::from([("x-image-token".into(), "same-value".into())]),
        ..first_page.clone()
    };
    assert_eq!(
        CacheManager::page_key(&first_page),
        CacheManager::page_key(&second_page)
    );
    let barrier = Arc::new(Barrier::new(9));
    let (sender, receiver) = mpsc::channel();
    let mut workers = Vec::new();
    for index in 0..8 {
        let (manager, page, owner) = if index % 2 == 0 {
            (
                first_manager.clone(),
                first_page.clone(),
                "download:shared-chapter",
            )
        } else {
            (
                second_manager.clone(),
                second_page.clone(),
                "reader:shared-page",
            )
        };
        let barrier = barrier.clone();
        let sender = sender.clone();
        workers.push(thread::spawn(move || {
            barrier.wait();
            let result = manager.fetch_page(&page, FetchPriority::Interactive, Some(owner));
            let _ = sender.send(result);
        }));
    }
    drop(sender);
    barrier.wait();
    let deadline = Instant::now() + Duration::from_secs(4);
    let mut paths = Vec::new();
    for _ in 0..8 {
        paths.push(
            receiver
                .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                .unwrap()
                .unwrap(),
        );
    }
    for worker in workers {
        worker.join().unwrap();
    }
    let requests = server.finish();
    assert_eq!(requests.len(), 1);
    assert!(
        requests[0]
            .to_ascii_lowercase()
            .contains("x-image-token: same-value")
    );
    assert!(paths.iter().all(|path| path == &paths[0]));
    assert_eq!(paths[0], paths[0].canonicalize().unwrap());
    assert_eq!(fs::read(&paths[0]).unwrap(), bytes);
    assert_eq!(generated_files(temporary.path()), vec![paths[0].clone()]);
    assert_eq!(first_manager.cache().stats().commits, 1);
    assert_eq!(first_manager.cache().stats().pinned_entries, 1);

    first_manager
        .cache()
        .unpin_owner("download:shared-chapter")
        .unwrap();
    second_manager.cache().clear_unpinned().unwrap();
    assert!(paths[0].exists());
    assert_eq!(second_manager.cache().stats().pinned_entries, 1);
    second_manager
        .cache()
        .unpin_owner("reader:shared-page")
        .unwrap();
    first_manager.cache().clear_unpinned().unwrap();
    assert!(!paths[0].exists());
    assert_eq!(first_manager.cache().stats().pinned_entries, 0);
}

#[test]
fn explicit_source_context_preserves_parallel_limits_across_cdn_origins() {
    let activity = Arc::new(ResponseActivity::default());
    let first_bytes = png(70);
    let second_bytes = png(190);
    let first_server = FixtureServer::with_activity(
        vec![Reply {
            declared_length: first_bytes.len(),
            body: first_bytes.clone(),
            delay: Duration::from_millis(150),
        }],
        Some(activity.clone()),
    );
    let second_server = FixtureServer::with_activity(
        vec![Reply {
            declared_length: second_bytes.len(),
            body: second_bytes.clone(),
            delay: Duration::from_millis(150),
        }],
        Some(activity.clone()),
    );
    let config = SourceConfig {
        id: "scoped".into(),
        name: "Scoped transfer fixture".into(),
        kind: "komga".into(),
        base_url: "https://metadata.example.invalid".into(),
        enabled: true,
        options: BTreeMap::from([("maximum_parallel_requests".into(), serde_json::json!(1))]),
        ..SourceConfig::default()
    };
    let temporary = tempfile::tempdir().unwrap();
    let manager = SourceManager::new_with_download_limits(
        temporary.path(),
        &[config],
        DownloadLimits {
            background: 2,
            foreground: 1,
        },
    )
    .unwrap();
    let pages = [
        Page {
            url: first_server.url("/cdn-one.png"),
            ..Page::default()
        },
        Page {
            url: second_server.url("/cdn-two.png"),
            ..Page::default()
        },
    ];
    assert_ne!(first_server.base_url, second_server.base_url);
    let barrier = Arc::new(Barrier::new(3));
    let (sender, receiver) = mpsc::channel();
    let mut workers = Vec::new();
    for (index, page) in pages.into_iter().enumerate() {
        let manager = manager.clone();
        let barrier = barrier.clone();
        let sender = sender.clone();
        workers.push(thread::spawn(move || {
            barrier.wait();
            let result =
                manager.fetch_page_for_source("scoped", &page, FetchPriority::Background, None);
            let _ = sender.send((index, result));
        }));
    }
    drop(sender);
    barrier.wait();
    let deadline = Instant::now() + Duration::from_secs(4);
    let expected = [first_bytes, second_bytes];
    for _ in 0..2 {
        let (index, result) = receiver
            .recv_timeout(deadline.saturating_duration_since(Instant::now()))
            .unwrap();
        assert_eq!(fs::read(result.unwrap()).unwrap(), expected[index]);
    }
    for worker in workers {
        worker.join().unwrap();
    }
    assert_eq!(first_server.finish().len(), 1);
    assert_eq!(second_server.finish().len(), 1);
    assert_eq!(activity.active.load(Ordering::SeqCst), 0);
    assert_eq!(activity.peak.load(Ordering::SeqCst), 1);
    assert_eq!(manager.cache().stats().commits, 2);
}
