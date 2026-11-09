use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::{Read, Write},
    net::{IpAddr, Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream, UdpSocket},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use anyhow::{Context, Result, anyhow, bail};
use serde::Deserialize;

use crate::domain::SourceConfig;

const SESSION_TTL: Duration = Duration::from_secs(15 * 60);
const MAX_HEADER_BYTES: usize = 16 * 1024;
const MAX_JSON_BYTES: usize = 64 * 1024;
const MAX_UPLOAD_BYTES: usize = 32 * 1024 * 1024;
const MAX_SESSION_UPLOAD_BYTES: usize = 512 * 1024 * 1024;
const MAX_SESSION_UPLOADS: usize = 32;

#[derive(Debug)]
pub enum PairingEvent {
    Source(SourceConfig),
    Repository(String),
    Import(PathBuf),
}

pub struct PairingServer {
    address: String,
    secret: String,
    code: String,
    expires: Instant,
    stop: Arc<AtomicBool>,
    active: Arc<AtomicBool>,
    connection: Arc<Mutex<Option<TcpStream>>>,
    worker: Option<JoinHandle<()>>,
    pub events: Receiver<PairingEvent>,
}

impl PairingServer {
    pub fn start(data_dir: PathBuf) -> Result<Self> {
        let address = lan_address().context("No active local-network interface is available")?;
        Self::start_at(data_dir, address, Ipv4Addr::UNSPECIFIED)
    }

    fn start_at(data_dir: PathBuf, address: Ipv4Addr, bind: Ipv4Addr) -> Result<Self> {
        let inbox = data_dir.join("inbox");
        fs::create_dir_all(&inbox).context("Create the pairing import inbox")?;
        let metadata = fs::symlink_metadata(&inbox).context("Inspect the pairing import inbox")?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            bail!("The pairing import inbox must be a real directory");
        }

        let listener = TcpListener::bind((bind, 0)).context("Bind the local pairing service")?;
        let port = listener.local_addr()?.port();
        listener.set_nonblocking(true)?;
        let origin = format!("http://{address}:{port}");
        let mut entropy = [0_u8; 36];
        getrandom::fill(&mut entropy)
            .map_err(|error| anyhow!("Generate a pairing secret: {error}"))?;
        let secret: String = entropy[..32]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let code = format!(
            "{:06}",
            u32::from_be_bytes(entropy[32..36].try_into().unwrap()) % 1_000_000
        );
        let expires = Instant::now() + SESSION_TTL;
        let stop = Arc::new(AtomicBool::new(false));
        let active = Arc::new(AtomicBool::new(true));
        let connection = Arc::new(Mutex::new(None));
        let (sender, events) = mpsc::channel();
        let mut context = ServerContext {
            inbox,
            origin: origin.clone(),
            authority: format!("{address}:{port}"),
            secret: secret.clone(),
            code: code.clone(),
            expires,
            stop: stop.clone(),
            active: active.clone(),
            connection: connection.clone(),
            sender,
            attempts: BTreeMap::new(),
            total_attempts: 0,
            upload_count: 0,
            upload_bytes: 0,
        };
        let worker = thread::Builder::new()
            .name("aidoku-pairing".into())
            .spawn(move || serve(listener, &mut context))
            .context("Start the local pairing service")?;
        Ok(Self {
            address: origin,
            secret,
            code,
            expires,
            stop,
            active,
            connection,
            worker: Some(worker),
            events,
        })
    }

    pub fn url(&self) -> String {
        self.address.clone()
    }

    /// Return the high-entropy QR token, never the short manual pairing code.
    pub fn token(&self) -> String {
        self.secret.clone()
    }

    pub fn code(&self) -> String {
        self.code.clone()
    }

    pub fn active(&self) -> bool {
        self.active.load(Ordering::Acquire) && Instant::now() < self.expires
    }

    pub fn expires_in(&self) -> Duration {
        self.expires.saturating_duration_since(Instant::now())
    }
}

impl Drop for PairingServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.active.store(false, Ordering::Release);
        if let Ok(connection) = self.connection.lock() {
            if let Some(stream) = connection.as_ref() {
                let _ = stream.shutdown(Shutdown::Both);
            }
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn lan_address() -> Result<Ipv4Addr> {
    // UDP connect selects a route without transmitting a packet or requiring Internet access.
    let socket = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0))?;
    socket.connect((Ipv4Addr::new(192, 0, 2, 1), 9))?;
    match socket.local_addr()?.ip() {
        IpAddr::V4(address) if !address.is_loopback() && !address.is_unspecified() => Ok(address),
        _ => bail!("An active IPv4 local-network address is required"),
    }
}

struct AttemptWindow {
    started: Instant,
    count: u8,
}

struct ServerContext {
    inbox: PathBuf,
    origin: String,
    authority: String,
    secret: String,
    code: String,
    expires: Instant,
    stop: Arc<AtomicBool>,
    active: Arc<AtomicBool>,
    connection: Arc<Mutex<Option<TcpStream>>>,
    sender: Sender<PairingEvent>,
    attempts: BTreeMap<IpAddr, AttemptWindow>,
    total_attempts: u32,
    upload_count: usize,
    upload_bytes: usize,
}

impl ServerContext {
    fn active(&self) -> bool {
        !self.stop.load(Ordering::Acquire) && Instant::now() < self.expires
    }

    fn allow_pair_attempt(&mut self, peer: IpAddr) -> bool {
        if self.total_attempts >= 30
            || (!self.attempts.contains_key(&peer) && self.attempts.len() >= 64)
        {
            return false;
        }
        let window = self.attempts.entry(peer).or_insert_with(|| AttemptWindow {
            started: Instant::now(),
            count: 0,
        });
        if window.started.elapsed() >= Duration::from_secs(60) {
            window.started = Instant::now();
            window.count = 0;
        }
        if window.count >= 5 {
            return false;
        }
        window.count += 1;
        self.total_attempts += 1;
        true
    }
}

fn serve(listener: TcpListener, context: &mut ServerContext) {
    while context.active() {
        match listener.accept() {
            Ok((mut stream, peer)) => {
                let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
                let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
                let _ = stream.set_nodelay(true);
                if let Ok(mut current) = context.connection.lock() {
                    *current = stream.try_clone().ok();
                }
                let response = match read_request(&mut stream, context) {
                    Ok(request) => dispatch(request, peer, context),
                    Err(error) => error,
                };
                let _ = write_response(&mut stream, response);
                let _ = stream.shutdown(Shutdown::Both);
                if let Ok(mut current) = context.connection.lock() {
                    *current = None;
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(30));
            }
            Err(_) => break,
        }
    }
    context.active.store(false, Ordering::Release);
}

struct Request {
    method: String,
    path: String,
    headers: BTreeMap<String, String>,
    body: Vec<u8>,
}

struct Response {
    status: u16,
    content_type: &'static str,
    body: Vec<u8>,
}

impl Response {
    fn json(status: u16, value: serde_json::Value) -> Self {
        Self {
            status,
            content_type: "application/json; charset=utf-8",
            body: serde_json::to_vec(&value).expect("Serialize a fixed JSON response"),
        }
    }

    fn error(status: u16, code: &str) -> Self {
        Self::json(status, serde_json::json!({"error": code}))
    }

    fn success() -> Self {
        Self::json(200, serde_json::json!({"ok": true}))
    }
}

fn read_request(
    stream: &mut TcpStream,
    context: &ServerContext,
) -> std::result::Result<Request, Response> {
    let deadline = Instant::now() + Duration::from_secs(45);
    let mut buffer = Vec::with_capacity(4096);
    let header_end = loop {
        if !context.active() || Instant::now() > deadline {
            return Err(Response::error(410, "expired"));
        }
        if let Some(position) = buffer.windows(4).position(|bytes| bytes == b"\r\n\r\n") {
            break position + 4;
        }
        if buffer.len() >= MAX_HEADER_BYTES {
            return Err(Response::error(431, "headers_too_large"));
        }
        let mut chunk = [0_u8; 2048];
        let amount = stream
            .read(&mut chunk)
            .map_err(|_| Response::error(408, "request_timeout"))?;
        if amount == 0 {
            return Err(Response::error(400, "invalid_request"));
        }
        buffer.extend_from_slice(&chunk[..amount]);
    };
    if header_end > MAX_HEADER_BYTES {
        return Err(Response::error(431, "headers_too_large"));
    }
    let text = std::str::from_utf8(&buffer[..header_end])
        .map_err(|_| Response::error(400, "invalid_request"))?;
    let mut lines = text.split("\r\n");
    let start: Vec<_> = lines
        .next()
        .unwrap_or_default()
        .split_whitespace()
        .collect();
    if start.len() != 3
        || !matches!(start[2], "HTTP/1.1" | "HTTP/1.0")
        || !start[1].starts_with('/')
        || start[1].len() > 4096
    {
        return Err(Response::error(400, "invalid_request"));
    }
    let method = start[0].to_owned();
    let path = start[1].split('?').next().unwrap_or("/").to_owned();
    let mut headers = BTreeMap::new();
    for line in lines.take_while(|line| !line.is_empty()) {
        let (key, value) = line
            .split_once(':')
            .ok_or_else(|| Response::error(400, "invalid_request"))?;
        if key.is_empty()
            || !key
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        {
            return Err(Response::error(400, "invalid_request"));
        }
        let key = key.to_ascii_lowercase();
        if headers.insert(key, value.trim().to_owned()).is_some() {
            return Err(Response::error(400, "duplicate_header"));
        }
    }
    if headers.get("host").map(String::as_str) != Some(context.authority.as_str())
        || headers.contains_key("transfer-encoding")
    {
        return Err(Response::error(400, "invalid_host"));
    }
    if method != "GET" && method != "POST" {
        return Err(Response::error(405, "method_not_allowed"));
    }
    if method == "POST"
        && (headers.get("origin").map(String::as_str) != Some(context.origin.as_str())
            || headers
                .get("sec-fetch-site")
                .is_some_and(|value| value != "same-origin" && value != "none"))
    {
        return Err(Response::error(403, "wrong_origin"));
    }
    let length = match headers.get("content-length") {
        Some(value) => value
            .parse::<usize>()
            .map_err(|_| Response::error(400, "invalid_length"))?,
        None if method == "POST" => return Err(Response::error(411, "length_required")),
        None => 0,
    };
    let limit = if path == "/api/import" {
        MAX_UPLOAD_BYTES
    } else {
        MAX_JSON_BYTES
    };
    if length > limit {
        return Err(Response::error(413, "body_too_large"));
    }
    if method == "GET" && length != 0 {
        return Err(Response::error(400, "invalid_request"));
    }
    if method == "POST" && path != "/api/pair" && !authenticated(&headers, &context.secret) {
        return Err(Response::error(401, "pairing_required"));
    }
    let mut body = buffer.split_off(header_end);
    if body.len() > length {
        return Err(Response::error(400, "invalid_length"));
    }
    while body.len() < length {
        if !context.active() || Instant::now() > deadline {
            return Err(Response::error(410, "expired"));
        }
        let mut chunk = [0_u8; 16 * 1024];
        let remaining = (length - body.len()).min(chunk.len());
        let amount = stream
            .read(&mut chunk[..remaining])
            .map_err(|_| Response::error(408, "request_timeout"))?;
        if amount == 0 {
            return Err(Response::error(400, "invalid_length"));
        }
        body.extend_from_slice(&chunk[..amount]);
    }
    Ok(Request {
        method,
        path,
        headers,
        body,
    })
}

fn authenticated(headers: &BTreeMap<String, String>, secret: &str) -> bool {
    headers
        .get("authorization")
        .and_then(|value| value.strip_prefix("Bearer "))
        .is_some_and(|value| constant_time_equal(value.as_bytes(), secret.as_bytes()))
}

fn constant_time_equal(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0_u8, |difference, (a, b)| difference | (a ^ b))
        == 0
}

fn dispatch(request: Request, peer: SocketAddr, context: &mut ServerContext) -> Response {
    if !context.active() {
        return Response::error(410, "expired");
    }
    match (request.method.as_str(), request.path.as_str()) {
        ("GET", "/") => Response {
            status: 200,
            content_type: "text/html; charset=utf-8",
            body: PAIRING_PAGE.as_bytes().to_vec(),
        },
        ("GET", "/api/status") if authenticated(&request.headers, &context.secret) => {
            Response::json(
                200,
                serde_json::json!({"ok": true, "expires_in": context.expires.saturating_duration_since(Instant::now()).as_secs()}),
            )
        }
        ("GET", "/api/status") => Response::error(401, "pairing_required"),
        ("POST", "/api/pair") => {
            if !context.allow_pair_attempt(peer.ip()) {
                return Response::error(429, "too_many_attempts");
            }
            #[derive(Deserialize)]
            #[serde(deny_unknown_fields)]
            struct PairRequest {
                code: String,
            }
            match serde_json::from_slice::<PairRequest>(&request.body) {
                Ok(pair) if constant_time_equal(pair.code.as_bytes(), context.code.as_bytes()) => {
                    Response::json(
                        200,
                        serde_json::json!({"ok": true, "token": context.secret}),
                    )
                }
                _ => Response::error(401, "invalid_pairing_code"),
            }
        }
        ("POST", "/api/source") => match source_request(&request.body) {
            Ok(source) => send_event(context, PairingEvent::Source(source)),
            Err(_) => Response::error(400, "invalid_source"),
        },
        ("POST", "/api/repository") => {
            #[derive(Deserialize)]
            #[serde(deny_unknown_fields)]
            struct RepositoryRequest {
                url: String,
            }
            match serde_json::from_slice::<RepositoryRequest>(&request.body)
                .ok()
                .and_then(|request| checked_http_url(&request.url).ok())
            {
                Some(url) => send_event(context, PairingEvent::Repository(url)),
                None => Response::error(400, "invalid_url"),
            }
        }
        ("POST", "/api/import") => {
            if context.upload_count >= MAX_SESSION_UPLOADS
                || context.upload_bytes.saturating_add(request.body.len())
                    > MAX_SESSION_UPLOAD_BYTES
            {
                return Response::error(429, "upload_limit");
            }
            let filename = match request
                .headers
                .get("x-aidoku-filename")
                .and_then(|name| decode_filename(name).ok())
                .filter(|name| safe_archive_name(name))
            {
                Some(name) => name,
                None => return Response::error(400, "invalid_filename"),
            };
            if !request.body.starts_with(b"PK\x03\x04") && !request.body.starts_with(b"PK\x05\x06")
            {
                return Response::error(400, "invalid_archive");
            }
            match save_upload(context, &filename, &request.body) {
                Ok(path) => {
                    let response = send_event(context, PairingEvent::Import(path.clone()));
                    if response.status == 200 {
                        context.upload_count += 1;
                        context.upload_bytes += request.body.len();
                    } else {
                        let _ = fs::remove_file(path);
                    }
                    response
                }
                Err(_) => Response::error(500, "save_failed"),
            }
        }
        _ => Response::error(404, "not_found"),
    }
}

fn send_event(context: &ServerContext, event: PairingEvent) -> Response {
    if !context.active() {
        return Response::error(410, "expired");
    }
    if context.sender.send(event).is_ok() {
        Response::success()
    } else {
        Response::error(503, "application_closed")
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceRequest {
    #[serde(default)]
    id: String,
    kind: String,
    #[serde(default)]
    name: String,
    #[serde(default, alias = "url")]
    base_url: String,
    #[serde(default)]
    username: String,
    #[serde(default)]
    password: String,
    #[serde(default)]
    token: String,
    #[serde(default)]
    source_id: String,
}

fn source_request(bytes: &[u8]) -> Result<SourceConfig> {
    let request: SourceRequest = serde_json::from_slice(bytes)?;
    if !matches!(
        request.kind.as_str(),
        "komga" | "kavita" | "suwayomi" | "mangadex"
    ) {
        bail!("Unsupported source kind");
    }
    let id = if request.id.trim().is_empty() {
        request.kind.clone()
    } else {
        request.id.trim().to_owned()
    };
    if id.len() > 128
        || !id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
        || request.name.len() > 512
        || request.username.len() > 1024
        || request.password.len() > 4096
        || request.token.len() > 16384
        || request.source_id.len() > 128
    {
        bail!("Source settings exceed their bounds");
    }
    let base_url = if request.base_url.trim().is_empty() && request.kind == "mangadex" {
        "https://api.mangadex.org".to_owned()
    } else {
        checked_http_url(&request.base_url)?
    };
    let name = if request.name.trim().is_empty() {
        match request.kind.as_str() {
            "komga" => "Komga",
            "kavita" => "Kavita",
            "suwayomi" => "Suwayomi",
            _ => "MangaDex",
        }
        .to_owned()
    } else {
        request.name.trim().to_owned()
    };
    let mut options = BTreeMap::new();
    if !request.source_id.trim().is_empty() {
        options.insert(
            "source_id".into(),
            serde_json::Value::String(request.source_id.trim().to_owned()),
        );
    }
    Ok(SourceConfig {
        id,
        name,
        kind: request.kind,
        base_url,
        username: request.username,
        password: request.password,
        token: request.token,
        version: 1,
        languages: vec![],
        enabled: true,
        options,
    })
}

fn checked_http_url(value: &str) -> Result<String> {
    let value = value.trim();
    if value.is_empty() || value.len() > 2048 {
        bail!("An HTTP URL is required");
    }
    let parsed = url::Url::parse(value)?;
    if !matches!(parsed.scheme(), "http" | "https")
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.fragment().is_some()
    {
        bail!("Use an HTTP URL without embedded credentials or a fragment");
    }
    Ok(parsed.to_string())
}

fn safe_archive_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 180
        && !name.starts_with('.')
        && !name.chars().any(|character| {
            character.is_control()
                || matches!(
                    character,
                    '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|'
                )
        })
        && (name.to_ascii_lowercase().ends_with(".cbz")
            || name.to_ascii_lowercase().ends_with(".zip"))
}

fn decode_filename(value: &str) -> Result<String> {
    let mut bytes = Vec::with_capacity(value.len());
    let mut iterator = value.bytes();
    while let Some(byte) = iterator.next() {
        if byte == b'%' {
            let high = iterator
                .next()
                .and_then(hex_digit)
                .context("Invalid encoded filename")?;
            let low = iterator
                .next()
                .and_then(hex_digit)
                .context("Invalid encoded filename")?;
            bytes.push(high * 16 + low);
        } else {
            bytes.push(byte);
        }
    }
    Ok(String::from_utf8(bytes)?)
}

fn hex_digit(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn save_upload(context: &ServerContext, name: &str, body: &[u8]) -> Result<PathBuf> {
    if !context.active() {
        bail!("Pairing session expired");
    }
    let unique = format!("{}-{}-{name}", &context.secret[..12], context.upload_count);
    let destination = context.inbox.join(&unique);
    let temporary = context.inbox.join(format!("{unique}.part"));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let result = (|| {
        let mut file = options.open(&temporary)?;
        file.write_all(body)?;
        file.sync_all()?;
        if !context.active() || destination.exists() {
            bail!("Import interrupted or destination already exists");
        }
        fs::rename(&temporary, &destination)?;
        Ok(destination)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn write_response(stream: &mut TcpStream, response: Response) -> std::io::Result<()> {
    let reason = match response.status {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        408 => "Request Timeout",
        410 => "Gone",
        411 => "Length Required",
        413 => "Content Too Large",
        429 => "Too Many Requests",
        431 => "Request Header Fields Too Large",
        503 => "Service Unavailable",
        _ => "Internal Server Error",
    };
    write!(
        stream,
        "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\nX-Frame-Options: DENY\r\nCross-Origin-Resource-Policy: same-origin\r\nContent-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'\r\n\r\n",
        response.status,
        reason,
        response.content_type,
        response.body.len()
    )?;
    stream.write_all(&response.body)
}

const PAIRING_PAGE: &str = r##"<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aidoku · 连接 Kindle</title>
<style>html{font:16px/1.6 system-ui,sans-serif;color:#000;background:#fff}body{max-width:680px;margin:32px auto;padding:0 20px}h1{font-family:serif;font-size:32px;margin-bottom:8px}h2{font-size:21px;margin:0 0 16px}section{border:1px solid #bbb;padding:24px;margin:24px 0;border-radius:4px}label{display:block;font-weight:600;margin:12px 0 4px}input,select,button{box-sizing:border-box;font:inherit;min-height:48px;border:1px solid #555;border-radius:4px;padding:8px 12px;width:100%;background:#fff;color:#000}button{background:#000;color:#fff;font-weight:700;margin-top:20px;cursor:pointer}button:disabled{background:#eee;color:#666;cursor:default}input:focus,select:focus,button:focus{outline:3px solid #555;outline-offset:3px}.muted{color:#555}.notice{padding:12px;background:#eee;border-left:3px solid #000;white-space:pre-wrap}[hidden]{display:none!important}.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:480px){.row{grid-template-columns:1fr}section{padding:18px}body{margin-top:20px}}</style></head>
<body><header><p class="muted">AIDOKU FOR KINDLE</p><h1>连接你的 Kindle</h1><p class="muted">在同一 Wi-Fi 下添加图源、配置服务器或导入漫画。配对窗口有效期为 15 分钟。</p></header>
<p id="status" class="notice" role="status" aria-live="polite">请输入 Kindle 屏幕上的配对码。</p>
<section id="pairing"><h2>输入配对码</h2><form id="pair-form"><label for="code">六位配对码</label><input id="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required><button>连接 Kindle</button></form></section>
<main id="connected" hidden>
<section><h2>配置图源</h2><form id="source-form"><label for="kind">图源类型</label><select id="kind"><option value="komga">Komga</option><option value="kavita">Kavita</option><option value="suwayomi">Suwayomi</option><option value="mangadex">MangaDex</option></select><label for="name">显示名称（可选）</label><input id="name" maxlength="128" autocomplete="off"><label for="server">服务器地址</label><input id="server" type="url" placeholder="http://192.168.1.10:25600" autocomplete="url"><div class="row"><div><label for="username">用户名（可选）</label><input id="username" autocomplete="username"></div><div><label for="password">密码（可选）</label><input id="password" type="password" autocomplete="current-password"></div></div><label for="api-key">API Key（可选）</label><input id="api-key" type="password" autocomplete="off"><label for="source-id">Suwayomi 图源 ID（可选）</label><input id="source-id" autocomplete="off"><button>保存图源配置</button></form></section>
<section><h2>添加 Aidoku 图源仓库</h2><form id="repository-form"><label for="repository">仓库地址</label><input id="repository" type="url" placeholder="https://example.org/sources/" required><button>添加仓库</button></form></section>
<section><h2>导入本地漫画</h2><form id="import-form"><label for="archive">CBZ 或 ZIP 文件</label><input id="archive" type="file" accept=".cbz,.zip,application/zip" required><p class="muted">单个文件最大 32 MB。文件上传后，Kindle 会显示导入结果。</p><button>上传到 Kindle</button></form></section>
</main>
<script>
let token=new URLSearchParams(location.search).get('token')||'';
history.replaceState(null,'',location.pathname);
const status=document.getElementById('status');
const messages={expired:'配对已过期，请在 Kindle 上重新打开添加图源页面。',pairing_required:'请先输入配对码。',invalid_pairing_code:'配对码不正确，请查看 Kindle 屏幕。',too_many_attempts:'尝试次数过多，请稍后重试或重新打开配对页面。',invalid_source:'图源配置不完整，请检查服务器地址和输入内容。',invalid_url:'请输入有效的 HTTP 或 HTTPS 地址。',invalid_filename:'请选择 CBZ 或 ZIP 文件。',invalid_archive:'文件不是有效的 ZIP / CBZ 压缩包。',body_too_large:'文件过大，单个文件最大 32 MB。',upload_limit:'本次配对已达到上传上限，请重新打开配对页面。',save_failed:'保存文件失败，请检查 Kindle 的剩余空间。',application_closed:'Kindle 应用已关闭。',request_timeout:'上传超时，请重试。',wrong_origin:'请使用 Kindle 屏幕上显示的地址打开此页面。'};
function show(message){status.textContent=message;}
function connected(){document.getElementById('pairing').hidden=true;document.getElementById('connected').hidden=false;show('已连接 Kindle。选择下方操作，结果将在 Kindle 上显示。');}
async function request(path,body,headers={}){let response;try{response=await fetch(path,{method:'POST',headers:{'Authorization':'Bearer '+token,...headers},body});}catch{throw new Error('连接中断，请检查 Kindle 与当前设备是否连接同一 Wi-Fi。');}let result;try{result=await response.json();}catch{throw new Error('Kindle 返回了无法读取的响应，请重试。');}if(!response.ok)throw new Error(messages[result.error]||'操作未完成，请检查 Kindle 上的提示后重试。');return result;}
async function submit(form,work){const button=form.querySelector('button');button.disabled=true;try{await work();}catch(error){show(error.message||'连接中断，请检查 Kindle 与当前设备是否连接同一 Wi-Fi。');}finally{button.disabled=false;}}
document.getElementById('pair-form').addEventListener('submit',event=>{event.preventDefault();submit(event.currentTarget,async()=>{const result=await request('/api/pair',JSON.stringify({code:document.getElementById('code').value.trim()}),{'Content-Type':'application/json'});token=result.token;document.getElementById('code').value='';connected();});});
document.getElementById('source-form').addEventListener('submit',event=>{event.preventDefault();submit(event.currentTarget,async()=>{const value=id=>document.getElementById(id).value;await request('/api/source',JSON.stringify({kind:value('kind'),name:value('name'),base_url:value('server'),username:value('username'),password:value('password'),token:value('api-key'),source_id:value('source-id')}),{'Content-Type':'application/json'});document.getElementById('password').value='';document.getElementById('api-key').value='';show('配置已发送到 Kindle，请在图源列表中打开该图源。');});});
document.getElementById('repository-form').addEventListener('submit',event=>{event.preventDefault();submit(event.currentTarget,async()=>{await request('/api/repository',JSON.stringify({url:document.getElementById('repository').value}),{'Content-Type':'application/json'});show('仓库已发送到 Kindle。读取完成后，可在“可安装”页签选择图源。');});});
document.getElementById('import-form').addEventListener('submit',event=>{event.preventDefault();submit(event.currentTarget,async()=>{const file=document.getElementById('archive').files[0];if(!file)throw new Error('请选择文件。');if(file.size>32*1024*1024)throw new Error(messages.body_too_large);show('正在上传，请保持 Kindle 应用和此页面打开…');await request('/api/import',file,{'Content-Type':'application/octet-stream','X-Aidoku-Filename':encodeURIComponent(file.name)});document.getElementById('archive').value='';show('文件已上传，Kindle 正在导入。');});});
if(token){fetch('/api/status',{headers:{'Authorization':'Bearer '+token}}).then(async response=>{if(!response.ok){const result=await response.json();throw new Error(messages[result.error]||messages.pairing_required);}connected();}).catch(()=>{token='';show('连接中断或配对已过期，请查看 Kindle 后重新输入配对码。');});}
</script></body></html>"##;

#[cfg(test)]
mod tests {
    use super::*;

    fn server() -> PairingServer {
        let directory = tempfile::tempdir().unwrap().keep();
        PairingServer::start_at(directory, Ipv4Addr::LOCALHOST, Ipv4Addr::LOCALHOST).unwrap()
    }

    fn call(
        server: &PairingServer,
        path: &str,
        body: &[u8],
        authenticated: bool,
        origin: Option<&str>,
        additional: &str,
    ) -> (u16, serde_json::Value) {
        let address = server.url().trim_start_matches("http://").to_owned();
        let mut stream = TcpStream::connect(&address).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        write!(
            stream,
            "POST {path} HTTP/1.1\r\nHost: {address}\r\nContent-Length: {}\r\n",
            body.len()
        )
        .unwrap();
        if let Some(origin) = origin {
            write!(stream, "Origin: {origin}\r\n").unwrap();
        }
        if authenticated {
            write!(stream, "Authorization: Bearer {}\r\n", server.token()).unwrap();
        }
        write!(stream, "{additional}\r\n").unwrap();
        stream.write_all(body).unwrap();
        let mut result = Vec::new();
        stream.read_to_end(&mut result).unwrap();
        let split = result
            .windows(4)
            .position(|bytes| bytes == b"\r\n\r\n")
            .unwrap();
        let status = std::str::from_utf8(&result[..split])
            .unwrap()
            .split_whitespace()
            .nth(1)
            .unwrap()
            .parse()
            .unwrap();
        (
            status,
            serde_json::from_slice(&result[split + 4..]).unwrap(),
        )
    }

    #[test]
    fn pairing_requires_correct_code_and_limits_guessing() {
        let server = server();
        let origin = server.url();
        let payload = serde_json::to_vec(&serde_json::json!({"code": server.code()})).unwrap();
        let (status, response) = call(
            &server,
            "/api/pair",
            &payload,
            false,
            Some(&origin),
            "Content-Type: application/json\r\n",
        );
        assert_eq!(status, 200);
        assert_eq!(response["token"], server.token());
        for _ in 0..4 {
            assert_eq!(
                call(
                    &server,
                    "/api/pair",
                    br#"{"code":"wrong"}"#,
                    false,
                    Some(&origin),
                    ""
                )
                .0,
                401
            );
        }
        assert_eq!(
            call(&server, "/api/pair", &payload, false, Some(&origin), "").0,
            429
        );
    }

    #[test]
    fn source_configuration_rejects_cross_origin_and_unauthenticated_writes() {
        let server = server();
        let origin = server.url();
        let body = br#"{"kind":"komga","base_url":"http://192.168.1.2:25600","username":"reader","password":"secret"}"#;
        assert_eq!(
            call(&server, "/api/source", body, false, Some(&origin), "").0,
            401
        );
        assert_eq!(
            call(
                &server,
                "/api/source",
                body,
                true,
                Some("http://untrusted.invalid"),
                ""
            )
            .0,
            403
        );
        assert_eq!(
            call(&server, "/api/source", body, true, Some(&origin), "").0,
            200
        );
        let PairingEvent::Source(source) =
            server.events.recv_timeout(Duration::from_secs(1)).unwrap()
        else {
            panic!("Expected a source event")
        };
        assert_eq!(source.id, "komga");
        assert_eq!(source.password, "secret");
    }

    #[test]
    fn upload_is_confined_and_rejects_traversal() {
        let server = server();
        let origin = server.url();
        let bytes = b"PK\x03\x04fixture";
        assert_eq!(
            call(
                &server,
                "/api/import",
                bytes,
                true,
                Some(&origin),
                "X-Aidoku-Filename: ..%2Fescape.cbz\r\n"
            )
            .0,
            400
        );
        assert_eq!(
            call(
                &server,
                "/api/import",
                bytes,
                true,
                Some(&origin),
                "X-Aidoku-Filename: comic.cbz\r\n"
            )
            .0,
            200
        );
        let PairingEvent::Import(path) =
            server.events.recv_timeout(Duration::from_secs(1)).unwrap()
        else {
            panic!("Expected an import event")
        };
        assert_eq!(path.parent().unwrap().file_name().unwrap(), "inbox");
        assert_eq!(fs::read(path).unwrap(), bytes);
    }

    #[test]
    fn archive_names_and_server_urls_are_validated() {
        assert!(safe_archive_name("chapter-01.cbz"));
        assert!(!safe_archive_name("C:\\chapter.cbz"));
        assert!(!safe_archive_name("../chapter.zip"));
        assert!(!safe_archive_name(".hidden.zip"));
        assert!(!safe_archive_name("chapter.exe"));
        assert!(checked_http_url("http://127.0.0.1:8080").is_ok());
        assert!(checked_http_url("file:///etc/passwd").is_err());
        assert!(checked_http_url("https://user:password@example.org").is_err());
        assert!(checked_http_url("https://example.org/#secret").is_err());
    }

    #[test]
    fn blocked_attempts_do_not_exhaust_other_devices_pairing_budget() {
        let (sender, _) = mpsc::channel();
        let mut context = ServerContext {
            inbox: PathBuf::new(),
            origin: String::new(),
            authority: String::new(),
            secret: String::new(),
            code: String::new(),
            expires: Instant::now() + SESSION_TTL,
            stop: Arc::new(AtomicBool::new(false)),
            active: Arc::new(AtomicBool::new(true)),
            connection: Arc::new(Mutex::new(None)),
            sender,
            attempts: BTreeMap::new(),
            total_attempts: 0,
            upload_count: 0,
            upload_bytes: 0,
        };
        let first = IpAddr::V4(Ipv4Addr::new(192, 168, 1, 2));
        let second = IpAddr::V4(Ipv4Addr::new(192, 168, 1, 3));
        for _ in 0..5 {
            assert!(context.allow_pair_attempt(first));
        }
        for _ in 0..30 {
            assert!(!context.allow_pair_attempt(first));
        }
        assert_eq!(context.total_attempts, 5);
        assert!(context.allow_pair_attempt(second));
    }

    #[test]
    fn drop_closes_listener_and_interrupts_an_incomplete_request() {
        let server = server();
        let address = server.url().trim_start_matches("http://").to_owned();
        let mut stream = TcpStream::connect(&address).unwrap();
        stream.write_all(b"POST /api/source HTTP/1.1\r\n").unwrap();
        thread::sleep(Duration::from_millis(80));
        let started = Instant::now();
        drop(server);
        assert!(started.elapsed() < Duration::from_secs(1));
        assert!(TcpStream::connect(address).is_err());
    }
}
