//! HTTP regressions for image deadlines and network lifecycle epochs.

use std::{
    collections::BTreeMap,
    fs,
    io::{Cursor, Read, Write},
    net::{Shutdown, TcpListener, TcpStream},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use super::{FetchPriority, SourceManager, is_network_interrupted};
use crate::domain::{Page, SourceConfig};

#[derive(Clone)]
struct ImageReply {
    bytes: Vec<u8>,
    chunk_delay: Duration,
    allow_client_disconnect: bool,
}

struct ImageServer {
    base_url: String,
    done: Arc<AtomicBool>,
    requests: Arc<Mutex<Vec<String>>>,
    worker: Option<thread::JoinHandle<()>>,
}

impl ImageServer {
    fn new(replies: Vec<ImageReply>) -> Self {
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
            let mut connections = Vec::new();
            let mut next = 0;
            while !stopped.load(Ordering::Acquire) && Instant::now() < deadline {
                let mut stream = match listener.accept() {
                    Ok((stream, _)) => stream,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    Err(error) => panic!("Image fixture could not accept a request: {error}"),
                };
                let reply = replies[next.min(replies.len() - 1)].clone();
                next += 1;
                let stopped = stopped.clone();
                let captured = captured.clone();
                // A timed-out slow response must not delay admission of the
                // next fast response to the same URL on a different connection.
                connections.push(thread::spawn(move || {
                    stream.set_nodelay(true).unwrap();
                    stream
                        .set_read_timeout(Some(Duration::from_millis(250)))
                        .unwrap();
                    stream
                        .set_write_timeout(Some(Duration::from_millis(250)))
                        .unwrap();
                    let request = read_request(&mut stream, deadline).unwrap();
                    captured.lock().unwrap().push(request);
                    let result = write_response(&mut stream, &reply, &stopped, deadline);
                    if !reply.allow_client_disconnect {
                        result.unwrap();
                    }
                    let _ = stream.shutdown(Shutdown::Both);
                }));
            }
            for connection in connections {
                connection.join().unwrap();
            }
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

    fn request_count(&self) -> usize {
        self.requests.lock().unwrap().len()
    }

    fn finish(mut self) -> Vec<String> {
        self.done.store(true, Ordering::Release);
        self.worker.take().unwrap().join().unwrap();
        self.requests.lock().unwrap().clone()
    }
}

impl Drop for ImageServer {
    fn drop(&mut self) {
        self.done.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn read_request(stream: &mut TcpStream, deadline: Instant) -> std::io::Result<String> {
    let mut bytes = Vec::new();
    let mut buffer = [0_u8; 1024];
    while !bytes.windows(4).any(|window| window == b"\r\n\r\n") {
        if Instant::now() >= deadline || bytes.len() >= 64 * 1024 {
            return Err(std::io::Error::other(
                "Image fixture request exceeded its time or size limit",
            ));
        }
        let count = stream.read(&mut buffer)?;
        if count == 0 {
            return Err(std::io::ErrorKind::UnexpectedEof.into());
        }
        bytes.extend_from_slice(&buffer[..count]);
    }
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

fn write_response(
    stream: &mut TcpStream,
    reply: &ImageReply,
    done: &AtomicBool,
    deadline: Instant,
) -> std::io::Result<()> {
    stream.write_all(format!(
        "HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        reply.bytes.len(),
    ).as_bytes())?;
    let chunk_size = if reply.chunk_delay.is_zero() {
        reply.bytes.len()
    } else {
        reply.bytes.len().div_ceil(8)
    };
    for chunk in reply.bytes.chunks(chunk_size.max(1)) {
        if done.load(Ordering::Acquire) {
            return Ok(());
        }
        if !reply.chunk_delay.is_zero() {
            thread::sleep(reply.chunk_delay);
        }
        if Instant::now() >= deadline {
            return Err(std::io::ErrorKind::TimedOut.into());
        }
        if done.load(Ordering::Acquire) {
            return Ok(());
        }
        stream.write_all(chunk)?;
        stream.flush()?;
    }
    Ok(())
}

fn png(shade: u8) -> Vec<u8> {
    let mut bytes = Cursor::new(Vec::new());
    image::GrayImage::from_pixel(2, 3, image::Luma([shade]))
        .write_to(&mut bytes, image::ImageFormat::Png)
        .unwrap();
    bytes.into_inner()
}

fn fast_reply(bytes: Vec<u8>) -> ImageReply {
    ImageReply {
        bytes,
        chunk_delay: Duration::ZERO,
        allow_client_disconnect: false,
    }
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
fn blocking_client_timeout_alone_allows_a_continuously_trickled_longer_body() {
    let bytes = png(110);
    let server = ImageServer::new(vec![ImageReply {
        bytes: bytes.clone(),
        chunk_delay: Duration::from_millis(45),
        allow_client_disconnect: false,
    }]);
    // This is the dependency behavior being guarded: without a request-level
    // deadline, the blocking client's timeout restarts for each body read.
    let client = reqwest::blocking::Client::builder()
        .no_proxy()
        .timeout(Duration::from_millis(100))
        .build()
        .unwrap();
    let started = Instant::now();
    let mut response = client.get(server.url("/baseline.png")).send().unwrap();
    let mut received = Vec::new();
    response.read_to_end(&mut received).unwrap();
    assert!(started.elapsed() > Duration::from_millis(100));
    assert_eq!(received, bytes);
    assert_eq!(
        image::ImageReader::new(Cursor::new(received))
            .with_guessed_format()
            .unwrap()
            .into_dimensions()
            .unwrap(),
        (2, 3)
    );
    assert_eq!(server.finish().len(), 1);
}

#[test]
fn image_request_deadline_aborts_a_trickle_and_leaves_the_same_url_retryable() {
    let bytes = png(200);
    let server = ImageServer::new(vec![
        ImageReply {
            bytes: bytes.clone(),
            chunk_delay: Duration::from_millis(45),
            allow_client_disconnect: true,
        },
        fast_reply(bytes.clone()),
    ]);
    let config = SourceConfig {
        id: "slow".into(),
        kind: "komga".into(),
        base_url: server.base_url.clone(),
        enabled: true,
        options: BTreeMap::from([("image_request_timeout_ms".into(), serde_json::json!(100))]),
        ..SourceConfig::default()
    };
    let temporary = tempfile::tempdir().unwrap();
    let manager = SourceManager::new(temporary.path(), &[config]).unwrap();
    let page = Page {
        url: server.url("/deadline.png"),
        ..Page::default()
    };
    let started = Instant::now();
    let error = manager
        .fetch_page_for_source("slow", &page, FetchPriority::Interactive, None)
        .unwrap_err();
    assert!(
        !is_network_interrupted(&error),
        "A request deadline must not close the network epoch: {error:#}"
    );
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "A 100 ms request deadline was not applied: {error:#}"
    );
    assert!(manager.cache().lookup_raw(&page).unwrap().is_none());
    assert!(generated_files(temporary.path()).is_empty());
    let failed = manager.cache().stats();
    assert_eq!(failed.writes_inflight, 0);
    assert_eq!(failed.reserved_bytes, 0);
    assert_eq!(failed.commits, 0);

    let path = manager
        .fetch_page_for_source("slow", &page, FetchPriority::Interactive, None)
        .unwrap();
    assert_eq!(fs::read(&path).unwrap(), bytes);
    assert_eq!(
        manager.cache().lookup_raw(&page).unwrap(),
        Some(path.clone())
    );
    assert_eq!(generated_files(temporary.path()), vec![path]);
    assert_eq!(manager.cache().stats().commits, 1);
    assert_eq!(server.finish().len(), 2);
}

#[test]
fn old_managers_read_cached_and_local_pages_but_cannot_restart_missing_transfers() {
    let first_bytes = png(50);
    let second_bytes = png(230);
    let server = ImageServer::new(vec![
        fast_reply(first_bytes.clone()),
        fast_reply(second_bytes.clone()),
    ]);
    let temporary = tempfile::tempdir().unwrap();
    let old = SourceManager::new(temporary.path(), &[]).unwrap();
    let cloned = old.clone();
    let first = Page {
        url: server.url("/cached.png"),
        ..Page::default()
    };
    let missing = Page {
        url: server.url("/missing.png"),
        ..Page::default()
    };
    let first_path = old
        .fetch_page(&first, FetchPriority::Interactive, None)
        .unwrap();
    assert_eq!(fs::read(&first_path).unwrap(), first_bytes);
    assert_eq!(server.request_count(), 1);
    let external = tempfile::tempdir().unwrap();
    let local_path = external.path().join("local.png");
    fs::write(&local_path, png(140)).unwrap();
    let local = Page {
        local_path: Some(local_path.clone()),
        ..Page::default()
    };
    let control = old.network_control();
    let old_epoch = old.network_epoch();
    control.suspend();

    for manager in [&old, &cloned] {
        assert_eq!(
            manager
                .fetch_page(&first, FetchPriority::Interactive, None)
                .unwrap(),
            first_path
        );
        assert_eq!(
            manager
                .fetch_page(&local, FetchPriority::Interactive, None)
                .unwrap(),
            local_path.canonicalize().unwrap()
        );
        let error = manager
            .fetch_page(&missing, FetchPriority::Interactive, None)
            .unwrap_err();
        assert!(
            is_network_interrupted(&error),
            "A stale missing transfer must be typed as interrupted: {error:#}"
        );
    }
    assert_eq!(server.request_count(), 1);
    control.resume();
    assert_ne!(control.epoch(), old_epoch);
    for manager in [&old, &cloned] {
        let error = manager
            .fetch_page(&missing, FetchPriority::Interactive, None)
            .unwrap_err();
        assert!(is_network_interrupted(&error));
        assert!(!manager.is_network_current());
    }
    assert_eq!(server.request_count(), 1);

    let fresh = SourceManager::new(temporary.path(), &[]).unwrap();
    assert_eq!(fresh.network_epoch(), control.epoch());
    assert!(fresh.is_network_current());
    let second_path = fresh
        .fetch_page(&missing, FetchPriority::Interactive, None)
        .unwrap();
    assert_eq!(fs::read(&second_path).unwrap(), second_bytes);
    assert_eq!(
        old.cache().lookup_raw(&missing).unwrap(),
        Some(second_path.clone())
    );
    assert_eq!(
        cloned
            .fetch_page(&missing, FetchPriority::Interactive, None)
            .unwrap(),
        second_path
    );
    assert_eq!(fresh.cache().stats().writes_inflight, 0);
    assert_eq!(fresh.cache().stats().commits, 2);
    let requests = server.finish();
    assert_eq!(requests.len(), 2);
    assert!(requests[0].starts_with("GET /cached.png "));
    assert!(requests[1].starts_with("GET /missing.png "));
}
