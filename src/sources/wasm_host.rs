//! Portable Aidoku host imports for the Wasmi interpreter.
//!
//! The wire contract follows the MIT-licensed Aidoku/aidoku-rs project.
//! Copyright (c) 2022-2025 Skittyblock; see aidoku-rs.LICENSE.

use std::{
    cell::Cell,
    collections::{BTreeMap, VecDeque},
    fs,
    io::Read,
    path::{Path, PathBuf},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result, anyhow, bail, ensure};
use reqwest::{
    blocking::Client,
    header::{HeaderMap, HeaderName, HeaderValue},
};
use serde::{Deserialize, Serialize};
use wasmi::{Caller, Extern, Memory, StoreLimits, StoreLimitsBuilder, Val};

use super::wasm_html::{HtmlOutput, HtmlStore};
use crate::domain::SourceConfig;

pub(super) const MAX_BUFFER: usize = 16 * 1024 * 1024;
const MAX_RESOURCES: usize = 2_048;
const MAX_HOST_BYTES: usize = 32 * 1024 * 1024;
const MAX_URL_BYTES: usize = 8 * 1024;
const MAX_HEADER_NAME_BYTES: usize = 256;
const MAX_HEADER_BYTES: usize = 64 * 1024;
const MAX_DEFAULTS_BYTES: usize = 1024 * 1024;

#[derive(Clone, Default)]
struct Request {
    method: u8,
    url: String,
    headers: BTreeMap<String, String>,
    body: Vec<u8>,
    timeout: f64,
    response: Option<Response>,
}

#[derive(Clone, Default)]
struct Response {
    url: String,
    code: u16,
    headers: BTreeMap<String, String>,
    body: Vec<u8>,
}

enum Resource {
    Buffer(Vec<u8>),
    Request(Request),
}

#[derive(Default, Serialize, Deserialize)]
struct Defaults {
    values: BTreeMap<String, Vec<u8>>,
}

pub(super) struct Host {
    pub limits: StoreLimits,
    pub client: Client,
    resources: BTreeMap<i32, Resource>,
    next: i32,
    html: HtmlStore,
    defaults: Defaults,
    defaults_path: PathBuf,
    defaults_dirty: Cell<bool>,
    #[cfg(test)]
    defaults_writes: Cell<u64>,
    requests: VecDeque<Instant>,
    rate_permits: usize,
    rate_period: Duration,
    deadline: Instant,
    log: Vec<String>,
}

impl Host {
    pub fn new(client: Client, directory: &Path, config: &SourceConfig) -> Result<Self> {
        fs::create_dir_all(directory).context("Create the source state directory")?;
        let defaults_path = directory.join("defaults.json");
        let existed = defaults_path.exists();
        let mut defaults: Defaults = if existed {
            let bytes = fs::read(&defaults_path).context("Read source preferences")?;
            ensure!(
                bytes.len() <= MAX_BUFFER,
                "The source preference file exceeds its limit"
            );
            serde_json::from_slice(&bytes).context("Decode source preferences")?
        } else {
            Defaults::default()
        };
        let original_count = defaults.values.len();
        for (key, value) in &config.options {
            if matches!(
                key.as_str(),
                "package_dir" | "wasm_file" | "repository" | "repository_url"
            ) {
                continue;
            }
            if key == "defaults" {
                if let Some(values) = value.as_object() {
                    for (name, value) in values {
                        if let Some(bytes) = encode_json_default(value)? {
                            defaults.values.entry(name.clone()).or_insert(bytes);
                        }
                    }
                }
            } else if let Some(bytes) = encode_json_default(value)? {
                defaults.values.entry(key.clone()).or_insert(bytes);
            }
        }
        let defaults_dirty = Cell::new(!existed || defaults.values.len() != original_count);
        Ok(Self {
            limits: StoreLimitsBuilder::new()
                .memory_size(64 * 1024 * 1024)
                .table_elements(16_384)
                .instances(1)
                .memories(1)
                .tables(2)
                .build(),
            client,
            resources: BTreeMap::new(),
            next: 1,
            html: HtmlStore::default(),
            defaults,
            defaults_path,
            defaults_dirty,
            #[cfg(test)]
            defaults_writes: Cell::new(0),
            requests: VecDeque::new(),
            rate_permits: 4,
            rate_period: Duration::from_secs(1),
            deadline: Instant::now() + Duration::from_secs(45),
            log: Vec::new(),
        })
    }

    pub fn reset_deadline(&mut self) {
        self.deadline = Instant::now() + Duration::from_secs(45);
    }

    pub fn seed_defaults(&mut self, values: BTreeMap<String, Vec<u8>>) {
        if values
            .iter()
            .any(|(key, value)| self.defaults.values.get(key) != Some(value))
        {
            self.defaults.values.extend(values);
            self.defaults_dirty.set(true);
        }
    }

    pub fn put_buffer(&mut self, bytes: Vec<u8>) -> Result<i32> {
        ensure!(
            bytes.len() <= MAX_BUFFER,
            "The source buffer exceeds 16 MiB"
        );
        self.put(Resource::Buffer(bytes))
    }

    pub fn put_encoded(&mut self, value: &impl Serialize) -> Result<i32> {
        self.put_buffer(postcard::to_allocvec(value).context("Encode a source argument")?)
    }

    fn put(&mut self, resource: Resource) -> Result<i32> {
        ensure!(
            self.resources.len() < MAX_RESOURCES && self.next < 1_000_000,
            "The source exceeded its resource limit"
        );
        let new_bytes = resource_bytes(&resource);
        let current = self.resource_bytes();
        ensure!(
            current.saturating_add(new_bytes) <= MAX_HOST_BYTES,
            "The source exceeded its host memory limit"
        );
        let id = self.next;
        self.next += 1;
        self.resources.insert(id, resource);
        Ok(id)
    }

    fn resource_bytes(&self) -> usize {
        self.resources
            .values()
            .map(resource_bytes)
            .fold(0, usize::saturating_add)
    }

    fn ensure_resource_change(
        &self,
        previous_bytes: usize,
        replacement_bytes: usize,
    ) -> Result<()> {
        ensure!(
            self.resource_bytes()
                .saturating_sub(previous_bytes)
                .saturating_add(replacement_bytes)
                <= MAX_HOST_BYTES,
            "The source exceeded its host memory limit"
        );
        Ok(())
    }

    pub fn release_resource(&mut self, id: i32) {
        self.resources.remove(&id);
    }

    #[cfg(test)]
    pub fn resource_count(&self) -> usize {
        self.resources.len()
    }

    pub fn take_image_request(&mut self, id: i32) -> Result<(String, BTreeMap<String, String>)> {
        let Some(Resource::Request(request)) = self.resources.remove(&id) else {
            bail!("The source returned an invalid image request descriptor");
        };
        ensure!(request.method == 0, "Only GET image requests are supported");
        Ok((request.url, request.headers))
    }

    pub fn persist_defaults(&self) -> Result<()> {
        super::network::check_session()?;
        ensure!(
            self.defaults.values.len() <= 1_024,
            "The source preference count exceeds its limit"
        );
        ensure!(
            self.defaults
                .values
                .values()
                .all(|value| value.len() <= MAX_DEFAULTS_BYTES)
                && defaults_bytes(&self.defaults) <= MAX_DEFAULTS_BYTES,
            "Source preference data exceeds 1 MiB"
        );
        if !self.defaults_dirty.get() {
            return Ok(());
        }
        let bytes =
            serde_json::to_vec_pretty(&self.defaults).context("Encode source preferences")?;
        ensure!(
            bytes.len() <= MAX_BUFFER,
            "Source preferences exceed 16 MiB"
        );
        super::settings::atomic_write(&self.defaults_path, &bytes)
            .context("Commit source preferences")?;
        #[cfg(test)]
        self.defaults_writes.set(self.defaults_writes.get() + 1);
        self.defaults_dirty.set(false);
        Ok(())
    }

    fn send(&mut self, id: i32) -> Result<i32> {
        super::network::check_session()?;
        let Some(Resource::Request(request)) = self.resources.get(&id) else {
            return Ok(-1);
        };
        let request = request.clone();
        if let Some(Resource::Request(target)) = self.resources.get_mut(&id) {
            target.response = None;
        }
        let url = reqwest::Url::parse(&request.url)
            .context("The source supplied an invalid request URL")?;
        ensure!(
            matches!(url.scheme(), "http" | "https"),
            "Sources may only request HTTP or HTTPS URLs"
        );
        self.wait_for_rate_limit()?;
        super::network::check_session()?;
        let remaining = self.deadline.saturating_duration_since(Instant::now());
        ensure!(
            !remaining.is_zero(),
            "The source operation exceeded its time limit"
        );
        let timeout = if request.timeout.is_finite() && request.timeout > 0.0 {
            Duration::from_secs_f64(request.timeout.min(30.0)).min(remaining)
        } else {
            Duration::from_secs(20).min(remaining)
        };
        let method = match request.method {
            0 => reqwest::Method::GET,
            1 => reqwest::Method::POST,
            2 => reqwest::Method::PUT,
            3 => reqwest::Method::HEAD,
            4 => reqwest::Method::DELETE,
            5 => reqwest::Method::PATCH,
            6 => reqwest::Method::OPTIONS,
            _ => bail!("The source requested an unsupported HTTP method"),
        };
        let mut headers = HeaderMap::new();
        for (name, value) in &request.headers {
            headers.insert(
                HeaderName::from_bytes(name.as_bytes()).context("Invalid source request header")?,
                HeaderValue::from_str(value).context("Invalid source request header value")?,
            );
        }
        let response = self
            .client
            .request(method, url)
            .headers(headers)
            .body(request.body)
            .timeout(timeout)
            .send();
        super::network::check_session()?;
        let response = match response {
            Ok(response) => response,
            Err(error) if error.is_builder() || error.is_redirect() => {
                return Err(error).context("The source HTTP request violated its request policy");
            }
            Err(error) => {
                let reason = if error.is_timeout() {
                    "timeout"
                } else if error.is_connect() {
                    "connection"
                } else {
                    "transport"
                };
                return Ok(
                    self.network_failure(&format!("The source HTTP request failed ({reason})"))
                );
            }
        };
        let response_url = response.url().to_string();
        let code = response.status().as_u16();
        let mut response_headers = BTreeMap::<String, String>::new();
        for (key, value) in response.headers() {
            if let Ok(value) = value.to_str() {
                response_headers
                    .entry(key.as_str().to_owned())
                    .and_modify(|previous| {
                        previous.push_str(", ");
                        previous.push_str(value);
                    })
                    .or_insert_with(|| value.to_owned());
            }
        }
        let mut body = Vec::new();
        let body_read = super::http_body::with_context(|| {
            response.take(MAX_BUFFER as u64 + 1).read_to_end(&mut body)
        })?;
        super::network::check_session()?;
        if body_read.is_err() {
            return Ok(
                self.network_failure("The source HTTP response failed while reading its body")
            );
        }
        ensure!(
            body.len() <= MAX_BUFFER,
            "The source HTTP response exceeds 16 MiB"
        );
        let response = Response {
            url: response_url,
            code,
            headers: response_headers,
            body,
        };
        self.ensure_resource_change(0, response_bytes(&response))?;
        if let Some(Resource::Request(target)) = self.resources.get_mut(&id) {
            target.response = Some(response);
        }
        Ok(0)
    }

    fn network_failure(&mut self, message: &str) -> i32 {
        if self.log.len() < 64 {
            self.log.push(message.chars().take(512).collect());
        }
        // A transport failure is recoverable by a source's mirror or retry logic.
        -10
    }

    fn wait_for_rate_limit(&mut self) -> Result<()> {
        let now = Instant::now();
        while self
            .requests
            .front()
            .is_some_and(|time| now.duration_since(*time) >= self.rate_period)
        {
            self.requests.pop_front();
        }
        if self.requests.len() >= self.rate_permits {
            let wait = self
                .requests
                .front()
                .map(|time| self.rate_period.saturating_sub(now.duration_since(*time)))
                .unwrap_or_default();
            ensure!(
                wait <= self.deadline.saturating_duration_since(now),
                "The source rate limit exceeds the operation deadline"
            );
            sleep_checked(wait)?;
            let now = Instant::now();
            while self
                .requests
                .front()
                .is_some_and(|time| now.duration_since(*time) >= self.rate_period)
            {
                self.requests.pop_front();
            }
        }
        self.requests.push_back(Instant::now());
        Ok(())
    }
}

fn resource_bytes(resource: &Resource) -> usize {
    match resource {
        Resource::Buffer(bytes) => bytes.len(),
        Resource::Request(request) => request
            .body
            .len()
            .saturating_add(request.url.len())
            .saturating_add(header_bytes(&request.headers))
            .saturating_add(request.response.as_ref().map_or(0, response_bytes)),
    }
}

fn header_bytes(headers: &BTreeMap<String, String>) -> usize {
    headers
        .iter()
        .map(|(name, value)| name.len().saturating_add(value.len()))
        .fold(0, usize::saturating_add)
}

fn response_bytes(response: &Response) -> usize {
    response
        .body
        .len()
        .saturating_add(response.url.len())
        .saturating_add(header_bytes(&response.headers))
}

fn defaults_bytes(defaults: &Defaults) -> usize {
    defaults
        .values
        .iter()
        .map(|(key, value)| key.len().saturating_add(value.len()))
        .fold(0, usize::saturating_add)
}

fn encode_json_default(value: &serde_json::Value) -> Result<Option<Vec<u8>>> {
    Ok(match value {
        serde_json::Value::Bool(value) => Some(postcard::to_allocvec(value)?),
        serde_json::Value::Number(value) => {
            if let Some(value) = value.as_i64().and_then(|value| i32::try_from(value).ok()) {
                Some(postcard::to_allocvec(&value)?)
            } else {
                value
                    .as_f64()
                    .map(|value| postcard::to_allocvec(&(value as f32)))
                    .transpose()?
            }
        }
        serde_json::Value::String(value) => Some(postcard::to_allocvec(value)?),
        serde_json::Value::Array(values) if values.iter().all(serde_json::Value::is_string) => {
            Some(postcard::to_allocvec(
                &values
                    .iter()
                    .filter_map(|value| value.as_str().map(str::to_owned))
                    .collect::<Vec<_>>(),
            )?)
        }
        _ => None,
    })
}

pub(super) fn memory(caller: &Caller<'_, Host>) -> Result<Memory> {
    caller
        .get_export("memory")
        .and_then(Extern::into_memory)
        .ok_or_else(|| anyhow!("The source does not export its memory"))
}

pub(super) fn read_bytes(caller: &Caller<'_, Host>, pointer: i32, length: i32) -> Result<Vec<u8>> {
    ensure!(
        pointer >= 0 && length >= 0 && length as usize <= MAX_BUFFER,
        "The source supplied an invalid memory range"
    );
    let mut buffer = vec![0; length as usize];
    memory(caller)?
        .read(caller, pointer as usize, &mut buffer)
        .context("Read source memory")?;
    Ok(buffer)
}

fn text(caller: &Caller<'_, Host>, params: &[Val], pointer: usize) -> Result<String> {
    String::from_utf8(read_bytes(
        caller,
        int(params, pointer)?,
        int(params, pointer + 1)?,
    )?)
    .context("The source supplied invalid UTF-8")
}

fn int(params: &[Val], index: usize) -> Result<i32> {
    params
        .get(index)
        .and_then(Val::i32)
        .ok_or_else(|| anyhow!("The source import has an invalid argument type"))
}

fn write_bytes(caller: &mut Caller<'_, Host>, pointer: i32, bytes: &[u8]) -> Result<()> {
    ensure!(
        pointer >= 0 && bytes.len() <= MAX_BUFFER,
        "The source supplied an invalid output range"
    );
    memory(caller)?
        .write(caller, pointer as usize, bytes)
        .context("Write source memory")
}

fn result_bytes(caller: &Caller<'_, Host>, pointer: i32) -> Result<Vec<u8>> {
    result_bytes_limited(caller, pointer, MAX_BUFFER)
}

fn result_bytes_limited(
    caller: &Caller<'_, Host>,
    pointer: i32,
    maximum_bytes: usize,
) -> Result<Vec<u8>> {
    let header = read_bytes(caller, pointer, 8)?;
    let length = i32::from_le_bytes(header[..4].try_into().expect("Four-byte header"));
    ensure!(
        length >= 8 && length as usize <= MAX_BUFFER,
        "The source preference result has an invalid length"
    );
    ensure!(
        length as usize - 8 <= maximum_bytes,
        "The source preference value exceeds its byte limit"
    );
    read_bytes(
        caller,
        pointer.checked_add(8).context("Source pointer overflow")?,
        length - 8,
    )
}

fn output_int(results: &mut [Val], value: i32) -> Result<()> {
    if let Some(result) = results.first_mut() {
        *result = Val::I32(value);
    }
    Ok(())
}

pub(super) fn dispatch(
    mut caller: Caller<'_, Host>,
    module: &str,
    name: &str,
    params: &[Val],
    results: &mut [Val],
) -> Result<()> {
    super::network::check_session()?;
    ensure!(
        Instant::now() <= caller.data().deadline,
        "The source operation exceeded its time limit"
    );
    match module {
        "env" => match name {
            "abort" => bail!("The source aborted execution"),
            "print" => {
                let message = text(&caller, params, 0)?;
                if caller.data().log.len() < 64 {
                    caller
                        .data_mut()
                        .log
                        .push(message.chars().take(2_048).collect());
                }
                Ok(())
            }
            "sleep" => {
                let seconds = int(params, 0)?;
                ensure!(
                    (0..=5).contains(&seconds),
                    "The source requested an excessive sleep"
                );
                sleep_checked(Duration::from_secs(seconds as u64))?;
                Ok(())
            }
            "send_partial_result" => {
                let _ = result_bytes(&caller, int(params, 0)?)?;
                Ok(())
            }
            _ => bail!("Unsupported Aidoku host capability: {module}.{name}"),
        },
        "std" => match name {
            "destroy" => {
                let id = int(params, 0)?;
                caller.data_mut().resources.remove(&id);
                caller.data_mut().html.destroy(id);
                Ok(())
            }
            "buffer_len" => {
                let value = match caller.data().resources.get(&int(params, 0)?) {
                    Some(Resource::Buffer(buffer)) => buffer.len() as i32,
                    _ => -1,
                };
                output_int(results, value)
            }
            "read_buffer" => {
                let id = int(params, 0)?;
                let size = int(params, 2)?;
                let buffer = match caller.data().resources.get(&id) {
                    Some(Resource::Buffer(buffer))
                        if size >= 0 && size as usize <= buffer.len() =>
                    {
                        buffer[..size as usize].to_vec()
                    }
                    _ => return output_int(results, -1),
                };
                write_bytes(&mut caller, int(params, 1)?, &buffer)?;
                output_int(results, 0)
            }
            "current_date" => {
                if let Some(result) = results.first_mut() {
                    *result = Val::F64(
                        (SystemTime::now()
                            .duration_since(UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_secs_f64())
                        .into(),
                    );
                }
                Ok(())
            }
            "utc_offset" => {
                if let Some(result) = results.first_mut() {
                    *result = Val::I64(i64::from(chrono::Local::now().offset().utc_minus_local()));
                }
                Ok(())
            }
            "parse_date" => {
                let date = text(&caller, params, 0)?;
                let format = text(&caller, params, 2)?;
                let timezone = text(&caller, params, 6)?;
                let value = parse_date(&date, &format, &timezone).unwrap_or(-5) as f64;
                if let Some(result) = results.first_mut() {
                    *result = Val::F64(value.into());
                }
                Ok(())
            }
            _ => bail!("Unsupported Aidoku host capability: {module}.{name}"),
        },
        "defaults" => match name {
            "get" => {
                let key = text(&caller, params, 0)?;
                let id = if let Some(bytes) = caller.data().defaults.values.get(&key).cloned() {
                    caller.data_mut().put_buffer(bytes)?
                } else {
                    -2
                };
                output_int(results, id)
            }
            "set" => {
                let key = text(&caller, params, 0)?;
                ensure!(key.len() <= 1_024, "The source preference key is too long");
                let kind = int(params, 2)?;
                ensure!(
                    (0..=6).contains(&kind),
                    "The source supplied an unsupported preference type"
                );
                if kind == 6 {
                    if caller.data_mut().defaults.values.remove(&key).is_some() {
                        caller.data().defaults_dirty.set(true);
                    }
                } else {
                    let bytes = result_bytes_limited(&caller, int(params, 3)?, MAX_DEFAULTS_BYTES)?;
                    ensure!(
                        caller.data().defaults.values.len() < 1_024
                            || caller.data().defaults.values.contains_key(&key),
                        "The source preference count exceeds its limit"
                    );
                    let previous = caller
                        .data()
                        .defaults
                        .values
                        .get(&key)
                        .map_or(0, |value| key.len().saturating_add(value.len()));
                    ensure!(
                        defaults_bytes(&caller.data().defaults)
                            .saturating_sub(previous)
                            .saturating_add(key.len())
                            .saturating_add(bytes.len())
                            <= MAX_DEFAULTS_BYTES,
                        "Source preference data exceeds 1 MiB"
                    );
                    if caller.data().defaults.values.get(&key) != Some(&bytes) {
                        caller.data_mut().defaults.values.insert(key, bytes);
                        caller.data().defaults_dirty.set(true);
                    }
                }
                caller.data().persist_defaults()?;
                output_int(results, 0)
            }
            _ => bail!("Unsupported Aidoku host capability: {module}.{name}"),
        },
        "net" => dispatch_net(&mut caller, name, params, results),
        "html" => dispatch_html(&mut caller, name, params, results),
        "js" => bail!(
            "This source requires JavaScript or browser verification ({module}.{name}), which this Kindle runtime does not support"
        ),
        "canvas" => bail!(
            "This source requires canvas image processing ({module}.{name}), which this Kindle runtime does not support"
        ),
        _ => bail!(
            "Unsupported Aidoku host module: {module}.{name}; this package may use the legacy source ABI"
        ),
    }
}

fn sleep_checked(duration: Duration) -> Result<()> {
    let deadline = Instant::now()
        .checked_add(duration)
        .context("The source sleep interval is too large")?;
    loop {
        super::network::check_session()?;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Ok(());
        }
        std::thread::sleep(remaining.min(Duration::from_millis(50)));
    }
}

fn dispatch_net(
    caller: &mut Caller<'_, Host>,
    name: &str,
    params: &[Val],
    results: &mut [Val],
) -> Result<()> {
    if name == "init" {
        let method = int(params, 0)?;
        if !(0..=6).contains(&method) {
            return output_int(results, -3);
        }
        let id = caller.data_mut().put(Resource::Request(Request {
            method: method as u8,
            ..Request::default()
        }))?;
        return output_int(results, id);
    }
    if name == "set_rate_limit" {
        let permits = int(params, 0)?;
        let period = int(params, 1)?;
        let unit = int(params, 2)?;
        ensure!(
            permits > 0 && period > 0 && (0..=2).contains(&unit),
            "Invalid source request rate limit"
        );
        let multiplier = match unit {
            1 => 60,
            2 => 3_600,
            _ => 1,
        };
        caller.data_mut().rate_permits = (permits as usize).min(64);
        caller.data_mut().rate_period =
            Duration::from_secs((period as u64).saturating_mul(multiplier).min(86_400));
        return Ok(());
    }
    if name == "send_all" {
        let length = int(params, 1)?;
        ensure!(
            (0..=64).contains(&length),
            "Too many concurrent source requests"
        );
        let pointer = int(params, 0)?;
        let bytes = read_bytes(caller, pointer, length * 4)?;
        let ids = bytes
            .chunks_exact(4)
            .map(|bytes| i32::from_le_bytes(bytes.try_into().expect("Four-byte request ID")))
            .collect::<Vec<_>>();
        let mut statuses = Vec::with_capacity(bytes.len());
        let mut failed = false;
        for id in ids {
            let status = caller.data_mut().send(id)?;
            failed |= status != 0;
            statuses.extend_from_slice(&status.to_le_bytes());
        }
        write_bytes(caller, pointer, &statuses)?;
        return output_int(results, if failed { -10 } else { 0 });
    }
    let id = int(params, 0)?;
    if name == "send" {
        let status = caller.data_mut().send(id)?;
        return output_int(results, status);
    }
    let Some(Resource::Request(request)) = caller.data().resources.get(&id) else {
        return output_int(results, -1);
    };
    match name {
        "set_url" | "set_body" | "set_header" | "set_timeout" => {
            match name {
                "set_url" => {
                    ensure!(
                        (0..=MAX_URL_BYTES as i32).contains(&int(params, 2)?),
                        "The source request URL exceeds 8 KiB"
                    );
                    let url = reqwest::Url::parse(&text(caller, params, 1)?)
                        .context("Invalid source request URL")?;
                    ensure!(
                        matches!(url.scheme(), "https" | "http"),
                        "Sources may only request HTTP or HTTPS URLs"
                    );
                    ensure!(
                        url.as_str().len() <= MAX_URL_BYTES,
                        "The source request URL exceeds 8 KiB"
                    );
                    caller
                        .data()
                        .ensure_resource_change(request.url.len(), url.as_str().len())?;
                    let Some(Resource::Request(request)) = caller.data_mut().resources.get_mut(&id)
                    else {
                        return output_int(results, -1);
                    };
                    request.url = url.into();
                }
                "set_header" => {
                    ensure!(
                        (1..=MAX_HEADER_NAME_BYTES as i32).contains(&int(params, 2)?),
                        "The source request header name exceeds 256 bytes"
                    );
                    ensure!(
                        (0..=MAX_HEADER_BYTES as i32).contains(&int(params, 4)?),
                        "The source request header value exceeds 64 KiB"
                    );
                    let name = text(caller, params, 1)?.to_ascii_lowercase();
                    let value = text(caller, params, 3)?;
                    HeaderName::from_bytes(name.as_bytes())
                        .context("Invalid source request header")?;
                    HeaderValue::from_str(&value).context("Invalid source request header value")?;
                    let previous_key = request
                        .headers
                        .keys()
                        .find(|key| key.eq_ignore_ascii_case(&name))
                        .cloned();
                    let previous = previous_key
                        .as_ref()
                        .and_then(|key| {
                            request
                                .headers
                                .get(key)
                                .map(|value| key.len().saturating_add(value.len()))
                        })
                        .unwrap_or(0);
                    let replacement = name.len().saturating_add(value.len());
                    ensure!(
                        header_bytes(&request.headers)
                            .saturating_sub(previous)
                            .saturating_add(replacement)
                            <= MAX_HEADER_BYTES,
                        "The source request headers exceed 64 KiB"
                    );
                    caller
                        .data()
                        .ensure_resource_change(previous, replacement)?;
                    let Some(Resource::Request(request)) = caller.data_mut().resources.get_mut(&id)
                    else {
                        return output_int(results, -1);
                    };
                    if let Some(key) = previous_key {
                        request.headers.remove(&key);
                    }
                    request.headers.insert(name, value);
                }
                "set_body" => {
                    let length = int(params, 2)?;
                    ensure!(
                        (0..=MAX_BUFFER as i32).contains(&length),
                        "The source request body exceeds 16 MiB"
                    );
                    caller
                        .data()
                        .ensure_resource_change(request.body.len(), length as usize)?;
                    let body = read_bytes(caller, int(params, 1)?, length)?;
                    let Some(Resource::Request(request)) = caller.data_mut().resources.get_mut(&id)
                    else {
                        return output_int(results, -1);
                    };
                    request.body = body;
                }
                "set_timeout" => {
                    let timeout = params
                        .get(1)
                        .and_then(Val::f64)
                        .map(f64::from)
                        .context("Invalid source request timeout")?;
                    let Some(Resource::Request(request)) = caller.data_mut().resources.get_mut(&id)
                    else {
                        return output_int(results, -1);
                    };
                    request.timeout = timeout;
                }
                _ => {}
            }
            output_int(results, 0)
        }
        "data_len" => output_int(
            results,
            request
                .response
                .as_ref()
                .map_or(-8, |response| response.body.len() as i32),
        ),
        "read_data" => {
            let Some(response) = request.response.as_ref() else {
                return output_int(results, -8);
            };
            let size = int(params, 2)?;
            if size < 0 || size as usize > response.body.len() {
                return output_int(results, -6);
            }
            let bytes = response.body[..size as usize].to_vec();
            write_bytes(caller, int(params, 1)?, &bytes)?;
            output_int(results, 0)
        }
        "get_status_code" => output_int(
            results,
            request
                .response
                .as_ref()
                .map_or(-8, |response| i32::from(response.code)),
        ),
        "get_url" | "get_header" | "html" => {
            let Some(response) = request.response.as_ref() else {
                return output_int(results, -8);
            };
            let response = response.clone();
            let value = match name {
                "get_url" => caller.data_mut().put_buffer(response.url.into_bytes())?,
                "get_header" => {
                    let key = text(caller, params, 1)?.to_ascii_lowercase();
                    if let Some(header) = response.headers.get(&key) {
                        caller.data_mut().put_buffer(header.as_bytes().to_vec())?
                    } else {
                        -7
                    }
                }
                "html" => {
                    let html = String::from_utf8(response.body)
                        .context("The source HTML response is not UTF-8")?;
                    match caller
                        .data_mut()
                        .html
                        .dispatch("parse", 0, 0, &html, &response.url)?
                    {
                        HtmlOutput::Int(value) => value,
                        HtmlOutput::Text(_) => {
                            bail!("The HTML parser returned an invalid descriptor")
                        }
                    }
                }
                _ => unreachable!(),
            };
            output_int(results, value)
        }
        "get_image" => bail!(
            "This source requires canvas image decoding (net.get_image), which this Kindle source runtime does not support"
        ),
        _ => bail!("Unsupported Aidoku host capability: net.{name}"),
    }
}

fn dispatch_html(
    caller: &mut Caller<'_, Host>,
    name: &str,
    params: &[Val],
    results: &mut [Val],
) -> Result<()> {
    let (rid, index, input, other) = match name {
        "parse" | "parse_fragment" => (0, 0, text(caller, params, 0)?, text(caller, params, 2)?),
        "escape" | "unescape" => (0, 0, text(caller, params, 0)?, String::new()),
        "set_attr" => (
            int(params, 0)?,
            0,
            text(caller, params, 1)?,
            text(caller, params, 3)?,
        ),
        "select" | "select_first" | "attr" | "has_attr" | "has_class" | "add_class"
        | "remove_class" | "remove_attr" | "set_text" | "set_html" | "prepend" | "append" => {
            (int(params, 0)?, 0, text(caller, params, 1)?, String::new())
        }
        "get" => (
            int(params, 0)?,
            int(params, 1)?,
            String::new(),
            String::new(),
        ),
        _ => (int(params, 0)?, 0, String::new(), String::new()),
    };
    let output = caller
        .data_mut()
        .html
        .dispatch(name, rid, index, &input, &other)?;
    let value = match output {
        HtmlOutput::Int(value) => value,
        HtmlOutput::Text(value) => caller.data_mut().put_buffer(value.into_bytes())?,
    };
    output_int(results, value)
}

fn parse_date(value: &str, swift_format: &str, timezone: &str) -> Option<i64> {
    use chrono::{DateTime, NaiveDate, NaiveDateTime, TimeZone};
    if let Ok(date) = DateTime::parse_from_rfc3339(value) {
        return Some(date.timestamp());
    }
    if let Ok(date) = DateTime::parse_from_rfc2822(value) {
        return Some(date.timestamp());
    }
    let format = date_format(swift_format);
    if let Ok(date) = DateTime::parse_from_str(value, &format) {
        return Some(date.timestamp());
    }
    let date = NaiveDateTime::parse_from_str(value, &format)
        .ok()
        .or_else(|| {
            NaiveDate::parse_from_str(value, &format)
                .ok()?
                .and_hms_opt(0, 0, 0)
        })?;
    match timezone {
        "current" => chrono::Local
            .from_local_datetime(&date)
            .single()
            .map(|date| date.timestamp()),
        "" | "UTC" | "GMT" | "Etc/UTC" => Some(date.and_utc().timestamp()),
        "Asia/Shanghai" | "Asia/Hong_Kong" | "Asia/Taipei" => {
            Some(date.and_utc().timestamp() - 8 * 3_600)
        }
        "Asia/Tokyo" | "Asia/Seoul" => Some(date.and_utc().timestamp() - 9 * 3_600),
        _ => None,
    }
}

fn date_format(format: &str) -> String {
    let mut result = String::new();
    let mut characters = format.chars().peekable();
    let mut literal = false;
    while let Some(character) = characters.next() {
        if character == '\'' {
            if characters.peek() == Some(&'\'') {
                characters.next();
                result.push('\'');
            } else {
                literal = !literal;
            }
            continue;
        }
        if literal {
            if character == '%' {
                result.push('%');
            }
            result.push(character);
            continue;
        }
        let mut count = 1;
        while characters.peek() == Some(&character) {
            characters.next();
            count += 1;
        }
        result.push_str(match character {
            'y' => {
                if count == 2 {
                    "%y"
                } else {
                    "%Y"
                }
            }
            'M' | 'L' => match count {
                1 | 2 => "%m",
                3 => "%b",
                _ => "%B",
            },
            'd' => "%d",
            'H' => "%H",
            'h' => "%I",
            'm' => "%M",
            's' => "%S",
            'a' => "%p",
            'E' => {
                if count >= 4 {
                    "%A"
                } else {
                    "%a"
                }
            }
            'Z' => {
                if count == 4 {
                    "GMT%:z"
                } else {
                    "%#z"
                }
            }
            'X' | 'x' => "%#z",
            'S' => {
                result.push_str(&format!("%{}f", count.min(9)));
                continue;
            }
            '%' => "%%",
            _ => {
                for _ in 0..count {
                    result.push(character);
                }
                continue;
            }
        });
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use wasmi::{Engine, ExternType, Linker, Module, Store};

    fn host(directory: &Path) -> Host {
        let client = Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(1))
            .build()
            .unwrap();
        Host::new(client, directory, &SourceConfig::default()).unwrap()
    }

    fn run_guest(host: Host, source: &str) -> Result<()> {
        let engine = Engine::default();
        let module = Module::new(&engine, &wat::parse_str(source)?)?;
        let mut linker = Linker::<Host>::new(&engine);
        for import in module.imports() {
            let ExternType::Func(function_type) = import.ty() else {
                bail!("Expected a function import")
            };
            let module_name = import.module().to_owned();
            let name = import.name().to_owned();
            let captured_module = module_name.clone();
            let captured_name = name.clone();
            linker.func_new(
                &module_name,
                &name,
                function_type.clone(),
                move |caller, params, results| {
                    dispatch(caller, &captured_module, &captured_name, params, results)
                        .map_err(|error| wasmi::Error::new(format!("{error:#}")))
                },
            )?;
        }
        let mut store = Store::new(&engine, host);
        store.limiter(|host| &mut host.limits);
        let instance = linker.instantiate(&mut store, &module)?.start(&mut store)?;
        instance
            .get_typed_func::<(), ()>(&store, "run")?
            .call(&mut store, ())?;
        Ok(())
    }

    #[test]
    fn transport_failure_returns_the_recoverable_abi_error_and_clears_previous_data() {
        // Keep the socket open without replying so the transport deterministically times out.
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let directory = tempfile::tempdir().unwrap();
        let mut host = host(directory.path());
        let id = host
            .put(Resource::Request(Request {
                url: format!("http://{}/waiting", listener.local_addr().unwrap()),
                timeout: 0.05,
                response: Some(Response {
                    body: b"previous response".to_vec(),
                    ..Response::default()
                }),
                ..Request::default()
            }))
            .unwrap();
        assert_eq!(host.send(id).unwrap(), -10);
        let Some(Resource::Request(request)) = host.resources.get(&id) else {
            panic!("Missing request")
        };
        assert!(request.response.is_none());
        assert!(host.log.iter().any(|entry| entry.contains("timeout")));
    }

    #[test]
    fn invalid_urls_and_operation_limits_remain_host_errors() {
        let directory = tempfile::tempdir().unwrap();
        let mut host = host(directory.path());
        let invalid = host
            .put(Resource::Request(Request {
                url: "file:///outside-the-host".into(),
                ..Request::default()
            }))
            .unwrap();
        assert!(host.send(invalid).is_err());
        let expired = host
            .put(Resource::Request(Request {
                url: "https://example.invalid".into(),
                ..Request::default()
            }))
            .unwrap();
        host.deadline = Instant::now() - Duration::from_secs(1);
        assert!(host.send(expired).is_err());
    }

    #[test]
    fn guest_send_all_receives_each_failure_status_without_a_wasm_trap() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let directory = tempfile::tempdir().unwrap();
        let mut host = host(directory.path());
        let id = host
            .put(Resource::Request(Request {
                url: format!("http://{}/waiting", listener.local_addr().unwrap()),
                timeout: 0.05,
                ..Request::default()
            }))
            .unwrap();
        let engine = Engine::default();
        let bytes = wat::parse_str(
            r#"(module
            (import "net" "send_all" (func $send_all (param i32 i32) (result i32)))
            (memory (export "memory") 1)
            (func (export "run") (result i32)
                i32.const 32 i32.const 2 call $send_all))"#,
        )
        .unwrap();
        let module = Module::new(&engine, &bytes).unwrap();
        let mut linker = Linker::<Host>::new(&engine);
        for import in module.imports() {
            let ExternType::Func(function_type) = import.ty() else {
                panic!("Expected a function import")
            };
            linker
                .func_new(
                    "net",
                    "send_all",
                    function_type.clone(),
                    |caller, params, results| {
                        dispatch(caller, "net", "send_all", params, results)
                            .map_err(|error| wasmi::Error::new(format!("{error:#}")))
                    },
                )
                .unwrap();
        }
        let mut store = Store::new(&engine, host);
        let instance = linker
            .instantiate(&mut store, &module)
            .unwrap()
            .start(&mut store)
            .unwrap();
        let memory = instance.get_memory(&store, "memory").unwrap();
        let ids = [id.to_le_bytes(), (-999_i32).to_le_bytes()].concat();
        memory.write(&mut store, 32, &ids).unwrap();
        assert_eq!(
            instance
                .get_typed_func::<(), i32>(&store, "run")
                .unwrap()
                .call(&mut store, ())
                .unwrap(),
            -10
        );
        let mut statuses = [0; 8];
        memory.read(&store, 32, &mut statuses).unwrap();
        assert_eq!(i32::from_le_bytes(statuses[..4].try_into().unwrap()), -10);
        assert_eq!(i32::from_le_bytes(statuses[4..].try_into().unwrap()), -1);
    }

    #[test]
    fn swift_date_formats_preserve_fractional_seconds_offsets_and_quoted_literals() {
        assert_eq!(
            parse_date("2024/01/02 00:00:00.789", "yyyy/MM/dd HH:mm:ss.SSS", "UTC"),
            Some(1_704_153_600)
        );
        assert_eq!(
            parse_date(
                "2024/01/02 08:00:00.001 +0800",
                "yyyy/MM/dd HH:mm:ss.SSS Z",
                "UTC"
            ),
            Some(1_704_153_600)
        );
        assert_eq!(
            parse_date(
                "02 Jan 2024 00:00:00 +08:00",
                "dd MMM yyyy HH:mm:ss XXX",
                "UTC"
            ),
            Some(1_704_124_800)
        );
        assert_eq!(
            parse_date(
                "2024-01-02 at o'clock 00:00:00",
                "yyyy-MM-dd 'at o''clock' HH:mm:ss",
                "UTC"
            ),
            Some(1_704_153_600)
        );
        assert_eq!(parse_date("not a date", "yyyy/MM/dd", "UTC"), None);
    }

    #[test]
    fn guest_request_body_setter_cannot_bypass_the_aggregate_host_budget() {
        let directory = tempfile::tempdir().unwrap();
        let mut host = host(directory.path());
        host.put_buffer(vec![0; MAX_BUFFER]).unwrap();
        host.put_buffer(vec![0; MAX_BUFFER - 4_096]).unwrap();
        let request = host.put(Resource::Request(Request::default())).unwrap();
        let source = format!(
            r#"(module
            (import "net" "set_body" (func $set_body (param i32 i32 i32) (result i32)))
            (memory (export "memory") 1)
            (func (export "run")
                i32.const {request} i32.const 0 i32.const 8192 call $set_body drop))"#
        );
        let error = run_guest(host, &source).unwrap_err();
        assert!(format!("{error:#}").contains("host memory limit"));
    }

    #[test]
    fn guest_header_setter_enforces_the_complete_header_map_budget() {
        let directory = tempfile::tempdir().unwrap();
        let mut host = host(directory.path());
        let request = host.put(Resource::Request(Request::default())).unwrap();
        let source = format!(
            r#"(module
            (import "net" "set_header" (func $set_header (param i32 i32 i32 i32 i32) (result i32)))
            (memory (export "memory") 1)
            (data (i32.const 0) "xy")
            (func (export "run")
                i32.const 2 i32.const 97 i32.const 65534 memory.fill
                i32.const {request} i32.const 0 i32.const 1 i32.const 2 i32.const 65534 call $set_header drop
                i32.const {request} i32.const 1 i32.const 1 i32.const 2 i32.const 1 call $set_header drop))"#
        );
        let error = run_guest(host, &source).unwrap_err();
        assert!(format!("{error:#}").contains("headers exceed 64 KiB"));
    }

    #[test]
    fn guest_defaults_setter_rejects_an_oversized_raw_map_before_serializing_it() {
        let directory = tempfile::tempdir().unwrap();
        let mut host = host(directory.path());
        host.defaults
            .values
            .insert("existing".into(), vec![0; MAX_DEFAULTS_BYTES - 32]);
        let source = r#"(module
            (import "defaults" "set" (func $set (param i32 i32 i32 i32) (result i32)))
            (memory (export "memory") 1)
            (data (i32.const 0) "new")
            (data (i32.const 1024) "\48\00\00\00\48\00\00\00")
            (func (export "run")
                i32.const 0 i32.const 3 i32.const 0 i32.const 1024 call $set drop))"#;
        let error = run_guest(host, source).unwrap_err();
        assert!(format!("{error:#}").contains("preference data exceeds 1 MiB"));
    }

    #[test]
    fn unchanged_preferences_skip_synchronous_file_replacement() {
        let directory = tempfile::tempdir().unwrap();
        let mut host = host(directory.path());
        host.seed_defaults(BTreeMap::from([(
            "enabled".into(),
            postcard::to_allocvec(&true).unwrap(),
        )]));
        host.persist_defaults().unwrap();
        assert_eq!(host.defaults_writes.get(), 1);
        let original = fs::read(&host.defaults_path).unwrap();
        let before = fs::metadata(&host.defaults_path)
            .unwrap()
            .modified()
            .unwrap();
        for _ in 0..2_000 {
            host.seed_defaults(BTreeMap::from([(
                "enabled".into(),
                postcard::to_allocvec(&true).unwrap(),
            )]));
            host.persist_defaults().unwrap();
        }
        assert_eq!(host.defaults_writes.get(), 1);
        assert_eq!(fs::read(&host.defaults_path).unwrap(), original);
        assert_eq!(
            fs::metadata(&host.defaults_path)
                .unwrap()
                .modified()
                .unwrap(),
            before
        );
        host.seed_defaults(BTreeMap::from([(
            "enabled".into(),
            postcard::to_allocvec(&false).unwrap(),
        )]));
        host.persist_defaults().unwrap();
        assert_eq!(host.defaults_writes.get(), 2);
        let saved: Defaults =
            serde_json::from_slice(&fs::read(&host.defaults_path).unwrap()).unwrap();
        assert!(!postcard::from_bytes::<bool>(&saved.values["enabled"]).unwrap());
    }

    #[test]
    fn guest_setting_an_existing_preference_to_the_same_value_does_not_write_it() {
        let directory = tempfile::tempdir().unwrap();
        let mut host = host(directory.path());
        host.seed_defaults(BTreeMap::from([(
            "enabled".into(),
            postcard::to_allocvec(&true).unwrap(),
        )]));
        host.persist_defaults().unwrap();
        let mut preserved = fs::read(&host.defaults_path).unwrap();
        preserved.extend_from_slice(b"\n \n");
        fs::write(&host.defaults_path, &preserved).unwrap();
        let source = r#"(module
            (import "defaults" "set" (func $set (param i32 i32 i32 i32) (result i32)))
            (memory (export "memory") 1)
            (data (i32.const 0) "enabled")
            (data (i32.const 1024) "\09\00\00\00\09\00\00\00\01")
            (func (export "run")
                i32.const 0 i32.const 7 i32.const 1 i32.const 1024 call $set drop))"#;
        run_guest(host, source).unwrap();
        assert_eq!(
            fs::read(directory.path().join("defaults.json")).unwrap(),
            preserved
        );
    }
}
