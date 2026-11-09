//! Streaming source transfers into the application's shared artifact cache.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail, ensure};
use reqwest::{
    Url,
    blocking::{Client, Response},
    header::{HeaderName, HeaderValue},
};

use super::{
    transfer::{
        DownloadLimits, FetchPriority, NetworkControl, RequestPolicy, TransferCoordinator,
        TransferPermit, TransferToken,
    },
    wasm,
};
use crate::{
    cache::{CacheManager, CacheWrite},
    domain::{Page, SourceConfig},
};

const MAX_TRANSFER_BYTES: u64 = 32 * 1024 * 1024;
const MAX_ARCHIVE_ENTRIES: usize = 20_000;
const MAX_ARCHIVE_EXPANDED_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const COPY_BUFFER_BYTES: usize = 64 * 1024;
const DEFAULT_TOTAL_TIMEOUT: Duration = Duration::from_secs(120);
static NEXT_ARCHIVE_READER: AtomicU64 = AtomicU64::new(1);

#[derive(Clone)]
struct SourcePolicy {
    origin: Option<String>,
    request: RequestPolicy,
    enabled: bool,
    total_timeout: Option<Duration>,
}

#[derive(Clone)]
pub(super) struct PageFetcher {
    cache: CacheManager,
    transfers: Arc<TransferCoordinator>,
    client: Client,
    policies: Arc<BTreeMap<String, SourcePolicy>>,
    default_policy: RequestPolicy,
    network: NetworkControl,
    epoch: u64,
}

#[derive(Clone, Copy)]
enum ValidationKind {
    Image,
    Archive,
}

impl PageFetcher {
    pub(super) fn open(
        data_dir: &Path,
        configurations: &[SourceConfig],
        client: Client,
        limits: DownloadLimits,
    ) -> Result<Self> {
        let transfers = TransferCoordinator::open(data_dir, limits)?;
        let network = transfers.control();
        let epoch = network.epoch();
        let default_policy = RequestPolicy {
            max_parallel: limits.background + limits.foreground,
            min_interval: Duration::ZERO,
        };
        let policies = configurations
            .iter()
            .map(|config| {
                let origin = Url::parse(&config.base_url)
                    .ok()
                    .map(|url| url.origin().ascii_serialization());
                (
                    config.id.clone(),
                    SourcePolicy {
                        origin,
                        request: source_policy(data_dir, config, default_policy),
                        enabled: config.enabled,
                        total_timeout: option_number(config, &["image_request_timeout_ms"])
                            .filter(|value| *value > 0)
                            .map(|value| Duration::from_millis(value.min(600_000))),
                    },
                )
            })
            .collect();
        Ok(Self {
            cache: CacheManager::open(data_dir)?,
            transfers,
            client,
            policies: Arc::new(policies),
            default_policy,
            network,
            epoch,
        })
    }

    pub(super) fn cache(&self) -> &CacheManager {
        &self.cache
    }

    pub(super) fn network_control(&self) -> NetworkControl {
        self.network.clone()
    }

    pub(super) fn epoch(&self) -> u64 {
        self.epoch
    }

    pub(super) fn is_network_current(&self) -> bool {
        self.network.check_epoch(self.epoch).is_ok()
    }

    pub(super) fn fetch(
        &self,
        page: &Page,
        priority: FetchPriority,
        pin_owner: Option<&str>,
    ) -> Result<PathBuf> {
        self.fetch_for_source(page, priority, pin_owner, None)
    }

    pub(super) fn fetch_for_source(
        &self,
        page: &Page,
        priority: FetchPriority,
        pin_owner: Option<&str>,
        source_id: Option<&str>,
    ) -> Result<PathBuf> {
        validate_headers(page)?;
        if let Some(owner) = pin_owner {
            self.cache.pin_raw(page, owner)?;
        }
        if let Some(path) = self.cache.lookup_raw(page)? {
            return Ok(path);
        }
        self.network.check_epoch(self.epoch)?;
        ensure!(
            !page.url.is_empty(),
            "The local comic page is unavailable and has no remote URL"
        );
        let mut requested = page.clone();
        // A missing managed local_path is only a hint. Its original remote URL
        // remains usable for a safe replacement; external valid files returned
        // above are never moved or copied into the network cache.
        requested.local_path = None;
        let key = CacheManager::page_key(&requested);
        let path = self
            .transfers
            .run_in_epoch(&key, priority, self.epoch, |token| {
                if let Some(path) = self.cache.lookup_raw(&requested)? {
                    return Ok(path);
                }
                if let Some(zip) = wasm::zip_request(&requested)? {
                    self.extract_page(&requested, &zip, token, pin_owner, source_id)
                } else {
                    self.fetch_remote(
                        &requested,
                        &requested,
                        token,
                        pin_owner,
                        source_id,
                        ValidationKind::Image,
                    )
                }
            })?;
        // Every follower may protect the same artifact with a different owner.
        if let Some(owner) = pin_owner {
            self.cache.pin_raw(&requested, owner)?;
        }
        Ok(path)
    }

    fn fetch_remote(
        &self,
        identity: &Page,
        request: &Page,
        token: &TransferToken,
        pin_owner: Option<&str>,
        source_id: Option<&str>,
        validation: ValidationKind,
    ) -> Result<PathBuf> {
        let (mut response, permit) = self.response(request, token, source_id)?;
        let expected = response.content_length().unwrap_or(0);
        ensure!(
            expected <= MAX_TRANSFER_BYTES,
            "The source artifact exceeds 32 MiB"
        );
        let mut staging = self.cache.begin_raw(identity, pin_owner, expected)?;
        super::http_body::with_context(|| write_stream(&mut response, &mut staging, token))?
            .context("Stream the source response into the shared cache")?;
        // Admission covers network I/O. Image decoding has a separate bounded
        // decoder gate and must not keep an otherwise idle HTTP slot occupied.
        drop(response);
        drop(permit);
        token.check_active()?;
        match validation {
            ValidationKind::Image => self
                .cache
                .validate_path(staging.temp_path())
                .context("Validate the downloaded comic image")?,
            ValidationKind::Archive => validate_archive(staging.temp_path())
                .context("Validate the downloaded source archive")?,
        }
        token.check_active()?;
        token.commit_if_active(|| {
            staging
                .commit()
                .context("Publish the validated source artifact")
        })
    }

    fn extract_page(
        &self,
        page: &Page,
        zip: &wasm::ZipRequest,
        token: &TransferToken,
        pin_owner: Option<&str>,
        requested_source: Option<&str>,
    ) -> Result<PathBuf> {
        let descriptor = Url::parse(&page.url).context("Read the ZIP source identity")?;
        let fields: BTreeMap<_, _> = descriptor
            .query_pairs()
            .map(|(key, value)| (key.into_owned(), value.into_owned()))
            .collect();
        let source_id = fields.get("source").map(String::as_str);
        ensure!(
            requested_source.is_none() || requested_source == source_id,
            "The ZIP page belongs to another source"
        );
        let mut archive_url =
            Url::parse("aidoku-archive://cache/").expect("The internal archive URL is valid");
        archive_url
            .query_pairs_mut()
            .append_pair("url", &zip.archive.url)
            .append_pair("source", source_id.unwrap_or_default())
            .append_pair(
                "version",
                fields
                    .get("version")
                    .map(String::as_str)
                    .unwrap_or_default(),
            );
        let archive = Page {
            url: archive_url.into(),
            headers: zip.archive.headers.clone(),
            local_path: None,
        };
        let archive_key = CacheManager::page_key(&archive);
        let owner = format!(
            "transient:archive:{}:{}:{}",
            std::process::id(),
            CacheManager::page_key(page),
            NEXT_ARCHIVE_READER.fetch_add(1, Ordering::Relaxed)
        );
        let protection = TransientPin {
            cache: self.cache.clone(),
            owner,
        };
        // Pin before lookup so a concurrent LRU pass cannot unlink the archive
        // between a cache hit and opening its requested entry.
        self.cache.pin_raw(&archive, &protection.owner)?;
        let archive_path = if let Some(path) = self.cache.lookup_raw(&archive)? {
            path
        } else {
            self.transfers.run_with_parent(
                &archive_key,
                token.priority(),
                Some(token),
                |archive_token| {
                    if let Some(path) = self.cache.lookup_raw(&archive)? {
                        return Ok(path);
                    }
                    self.fetch_remote(
                        &archive,
                        &zip.archive,
                        archive_token,
                        Some(&protection.owner),
                        source_id,
                        ValidationKind::Archive,
                    )
                },
            )?
        };
        let mut archive_file = zip::ZipArchive::new(
            File::open(&archive_path).context("Open the shared source archive")?,
        )
        .context("Read the shared source archive directory")?;
        let mut entry = archive_file
            .by_name(&zip.entry)
            .context("The comic page is missing from its source archive")?;
        ensure!(
            !entry.is_dir() && !is_symlink(entry.unix_mode()),
            "The source ZIP page is not a regular file"
        );
        ensure!(
            entry.size() > 0 && entry.size() <= MAX_TRANSFER_BYTES,
            "The archived comic page is empty or exceeds 32 MiB"
        );
        let mut staging = self.cache.begin_raw(page, pin_owner, entry.size())?;
        write_stream(&mut entry, &mut staging, token)
            .context("Stream the requested ZIP entry into the shared cache")?;
        token.check_active()?;
        self.cache
            .validate_path(staging.temp_path())
            .context("Validate the extracted comic page")?;
        token.check_active()?;
        let path = token
            .commit_if_active(|| staging.commit().context("Publish the extracted comic page"))?;
        drop(protection);
        Ok(path)
    }

    fn response(
        &self,
        page: &Page,
        token: &TransferToken,
        source_id: Option<&str>,
    ) -> Result<(Response, TransferPermit)> {
        let mut url = Url::parse(&page.url).context("The page has an invalid image URL")?;
        let mut headers = page.headers.clone();
        let mut request_deadline: Option<Instant> = None;
        for redirect in 0..=5 {
            token.check_active()?;
            ensure!(
                matches!(url.scheme(), "http" | "https") && url.host_str().is_some(),
                "Page downloads require HTTP or HTTPS"
            );
            ensure!(
                url.username().is_empty() && url.password().is_none(),
                "Image credentials must be provided as request headers"
            );
            let origin = url.origin().ascii_serialization();
            let policy = self.request_policy(&origin, source_id)?;
            let permit = if source_id.is_some() {
                self.transfers
                    .acquire_scoped(token, &origin, source_id, policy)?
            } else {
                self.transfers.acquire(token, &origin, policy)?
            };
            token.check_active()?;
            let now = Instant::now();
            let candidate = now
                .checked_add(self.total_timeout(&origin, source_id))
                .context("The image request deadline is too large")?;
            let deadline = *request_deadline.get_or_insert(candidate);
            let deadline = deadline.min(candidate);
            request_deadline = Some(deadline);
            let remaining = deadline.saturating_duration_since(now);
            ensure!(
                !remaining.is_zero(),
                "The comic image exceeded its total request deadline"
            );
            let mut request = self.client.get(url.clone());
            if !headers
                .keys()
                .any(|name| name.eq_ignore_ascii_case("Accept"))
            {
                request = request.header("Accept", "image/*");
            }
            for (name, value) in &headers {
                request = request.header(name, value);
            }
            let response = request.timeout(remaining).send();
            token.check_active()?;
            let response = response.context("Request the comic image")?;
            if !response.status().is_redirection() {
                return Ok((
                    response
                        .error_for_status()
                        .context("The comic image server returned an error")?,
                    permit,
                ));
            }
            ensure!(redirect < 5, "The comic image exceeded its redirect limit");
            let location = response
                .headers()
                .get(reqwest::header::LOCATION)
                .context("The image redirect has no destination")?
                .to_str()
                .context("The image redirect has an invalid destination")?;
            let next = url
                .join(location)
                .context("The image redirect has an invalid URL")?;
            if next.origin() != url.origin() {
                headers.retain(|name, _| {
                    name.eq_ignore_ascii_case("Accept")
                        || name.eq_ignore_ascii_case("User-Agent")
                        || name.eq_ignore_ascii_case("Accept-Language")
                });
            }
            drop(response);
            drop(permit);
            url = next;
        }
        bail!("The comic image exceeded its redirect limit")
    }

    fn request_policy(&self, origin: &str, source_id: Option<&str>) -> Result<RequestPolicy> {
        if let Some(id) = source_id {
            let source = self
                .policies
                .get(id)
                .with_context(|| format!("Source {id} is not configured for this transfer"))?;
            ensure!(source.enabled, "The source is disabled");
            return Ok(source.request);
        }
        let mut policy = self.default_policy;
        for source in self.policies.values() {
            if source.enabled && source.origin.as_deref() == Some(origin) {
                policy.max_parallel = policy.max_parallel.min(source.request.max_parallel);
                policy.min_interval = policy.min_interval.max(source.request.min_interval);
            }
        }
        Ok(policy)
    }

    fn total_timeout(&self, origin: &str, source_id: Option<&str>) -> Duration {
        if let Some(source) = source_id.and_then(|id| self.policies.get(id)) {
            return source.total_timeout.unwrap_or(DEFAULT_TOTAL_TIMEOUT);
        }
        self.policies
            .values()
            .filter(|source| source.enabled && source.origin.as_deref() == Some(origin))
            .filter_map(|source| source.total_timeout)
            .min()
            .unwrap_or(DEFAULT_TOTAL_TIMEOUT)
    }
}

struct TransientPin {
    cache: CacheManager,
    owner: String,
}

impl Drop for TransientPin {
    fn drop(&mut self) {
        let _ = self.cache.unpin_owner(&self.owner);
    }
}

fn validate_headers(page: &Page) -> Result<()> {
    ensure!(
        page.headers.len() <= 128,
        "The image request has too many headers"
    );
    let mut names = BTreeSet::new();
    let mut size = 0usize;
    for (name, value) in &page.headers {
        size = size.saturating_add(name.len()).saturating_add(value.len());
        ensure!(size <= 64 * 1024, "The image request headers exceed 64 KiB");
        HeaderName::from_bytes(name.as_bytes())
            .context("The image request has an invalid header name")?;
        HeaderValue::from_str(value).context("The image request has an invalid header value")?;
        ensure!(
            names.insert(name.to_ascii_lowercase()),
            "The image request repeats a header with a different case"
        );
    }
    Ok(())
}

fn write_stream(
    reader: &mut impl Read,
    staging: &mut CacheWrite,
    token: &TransferToken,
) -> Result<u64> {
    let mut output = OpenOptions::new()
        .write(true)
        .truncate(false)
        .open(staging.temp_path())
        .context("Open the shared cache staging file")?;
    let mut buffer = [0; COPY_BUFFER_BYTES];
    let mut total = 0u64;
    loop {
        token.check_active()?;
        let read = reader.read(&mut buffer);
        token.check_active()?;
        let count = match read {
            Ok(count) => count,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error).context("Read the source artifact stream"),
        };
        if count == 0 {
            break;
        }
        total = total
            .checked_add(count as u64)
            .context("The source artifact length overflowed")?;
        ensure!(
            total <= MAX_TRANSFER_BYTES,
            "The source artifact exceeds 32 MiB"
        );
        staging.ensure_capacity(total)?;
        output
            .write_all(&buffer[..count])
            .context("Write the source artifact stream")?;
    }
    ensure!(total > 0, "The source artifact is empty");
    output.flush().context("Flush the source artifact stream")?;
    Ok(total)
}

fn source_policy(data_dir: &Path, config: &SourceConfig, default: RequestPolicy) -> RequestPolicy {
    let mut maximum = option_number(
        config,
        &[
            "maximum_parallel_requests",
            "maximumParallelRequests",
            "max_parallel_requests",
        ],
    );
    if config.kind.eq_ignore_ascii_case("wasm") {
        let package = config
            .options
            .get("package_dir")
            .and_then(serde_json::Value::as_str)
            .map(PathBuf::from)
            .unwrap_or_else(|| data_dir.join("sources").join(&config.id));
        let package = if package.is_absolute() {
            package
        } else {
            data_dir.join(package)
        };
        if let Ok(file) = File::open(package.join("source.json")) {
            if file
                .metadata()
                .is_ok_and(|metadata| metadata.len() <= 1024 * 1024)
            {
                if let Ok(manifest) =
                    serde_json::from_reader::<_, serde_json::Value>(file.take(1024 * 1024 + 1))
                {
                    if let Some(value) = manifest
                        .pointer("/config/maximumParallelRequests")
                        .and_then(serde_json::Value::as_u64)
                        .filter(|value| *value > 0)
                    {
                        maximum = Some(maximum.map_or(value, |existing| existing.min(value)));
                    }
                }
            }
        }
    }
    let interval = option_number(
        config,
        &["request_interval_ms", "minimum_request_interval_ms"],
    )
    .unwrap_or(0)
    .min(60_000);
    RequestPolicy {
        max_parallel: maximum
            .and_then(|value| usize::try_from(value).ok())
            .filter(|value| *value > 0)
            .unwrap_or(default.max_parallel)
            .min(default.max_parallel),
        min_interval: Duration::from_millis(interval),
    }
}

fn option_number(config: &SourceConfig, names: &[&str]) -> Option<u64> {
    names.iter().find_map(|name| {
        config
            .options
            .get(*name)
            .and_then(serde_json::Value::as_u64)
    })
}

fn validate_archive(path: &Path) -> Result<()> {
    let mut file = File::open(path).context("Open the source archive for validation")?;
    let length = file.metadata()?.len();
    ensure!(
        (22..=MAX_TRANSFER_BYTES).contains(&length),
        "The source archive has an invalid length"
    );
    // Reject oversized central directories before the ZIP library allocates
    // their entry metadata. ZIP64 and split archives exceed this bounded use.
    let tail_length = length.min(65_557);
    file.seek(SeekFrom::End(-(tail_length as i64)))?;
    let mut tail = vec![0; tail_length as usize];
    file.read_exact(&mut tail)?;
    let end = tail
        .windows(4)
        .rposition(|bytes| bytes == b"PK\x05\x06")
        .context("The source archive has no ZIP directory terminator")?;
    ensure!(
        end + 22 <= tail.len(),
        "The source ZIP directory terminator is truncated"
    );
    let word = |offset: usize| u16::from_le_bytes([tail[end + offset], tail[end + offset + 1]]);
    let count = usize::from(word(10));
    ensure!(
        word(4) == 0 && word(6) == 0 && usize::from(word(8)) == count,
        "Split source archives are not supported"
    );
    ensure!(
        count > 0 && count <= MAX_ARCHIVE_ENTRIES,
        "The source archive has too many entries or uses ZIP64"
    );
    ensure!(
        end + 22 + usize::from(word(20)) == tail.len(),
        "The source archive has an invalid ZIP comment boundary"
    );
    file.seek(SeekFrom::Start(0))?;
    let mut archive = zip::ZipArchive::new(file).context("Read the source archive directory")?;
    ensure!(
        archive.len() == count,
        "The source archive directory count is inconsistent"
    );
    let mut expanded = 0u64;
    let mut names = BTreeSet::new();
    for index in 0..archive.len() {
        let entry = archive
            .by_index(index)
            .context("Read the source archive entry")?;
        let name = entry.name().trim_end_matches('/');
        ensure!(
            !name.is_empty()
                && name.len() <= 4_096
                && !name.contains(['\\', ':', '\0'])
                && name
                    .split('/')
                    .all(|part| !part.is_empty() && part != "." && part != ".."),
            "The source archive contains an unsafe entry path"
        );
        ensure!(
            !is_symlink(entry.unix_mode()),
            "Symbolic links are not allowed in source archives"
        );
        ensure!(
            names.insert(name.to_ascii_lowercase()),
            "The source archive repeats an entry path"
        );
        expanded = expanded
            .checked_add(entry.size())
            .context("The source archive expanded size overflowed")?;
        ensure!(
            expanded <= MAX_ARCHIVE_EXPANDED_BYTES,
            "The expanded source archive exceeds 2 GiB"
        );
    }
    Ok(())
}

fn is_symlink(mode: Option<u32>) -> bool {
    mode.is_some_and(|mode| mode & 0o170000 == 0o120000)
}
