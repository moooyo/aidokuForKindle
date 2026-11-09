//! Source access is blocking and must be called from the application's worker pool.

mod abi;
mod fetch;
mod http_body;
pub mod network;
pub mod repository;
mod settings;
mod transfer;
pub mod wasm;
mod wasm_host;
mod wasm_html;

use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
    time::Duration,
};

use anyhow::{Context, Result, anyhow, bail, ensure};
use reqwest::blocking::Client;

use crate::{
    cache::CacheManager,
    domain::{Book, Chapter, Page, SourceConfig},
};
pub use repository::{Repository, RepositorySource};
pub use settings::SourceSetting;
pub use transfer::{
    DownloadLimits, FetchPriority, NetworkControl, NetworkInterrupted, is_network_interrupted,
};

#[derive(Debug, Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct SourcePage {
    pub books: Vec<Book>,
    pub has_next: bool,
}

#[derive(Clone)]
pub struct SourceManager {
    data_dir: PathBuf,
    configurations: BTreeMap<String, SourceConfig>,
    client: Client,
    public_client: Client,
    fetcher: fetch::PageFetcher,
}

#[derive(Clone)]
struct HttpClients {
    source: Client,
    public: Client,
    image: Client,
}

static HTTP_CLIENTS: Mutex<Option<HttpClients>> = Mutex::new(None);

impl SourceManager {
    pub fn new(data_dir: impl AsRef<Path>, configurations: &[SourceConfig]) -> Result<Self> {
        Self::new_with_download_limits(data_dir, configurations, DownloadLimits::default())
    }

    pub fn new_with_download_limits(
        data_dir: impl AsRef<Path>,
        configurations: &[SourceConfig],
        limits: DownloadLimits,
    ) -> Result<Self> {
        fs::create_dir_all(data_dir.as_ref().join("sources"))
            .context("Create the source directory")?;
        let data_dir = data_dir
            .as_ref()
            .canonicalize()
            .context("Resolve the application data directory")?;
        let clients = shared_http_clients()?;
        let fetcher =
            fetch::PageFetcher::open(&data_dir, configurations, clients.image.clone(), limits)?;
        Ok(Self {
            data_dir,
            configurations: configurations
                .iter()
                .map(|config| (config.id.clone(), config.clone()))
                .collect(),
            client: clients.source,
            public_client: clients.public,
            fetcher,
        })
    }

    pub fn cache(&self) -> &CacheManager {
        self.fetcher.cache()
    }

    pub fn network_control(&self) -> NetworkControl {
        self.fetcher.network_control()
    }

    pub fn network_epoch(&self) -> u64 {
        self.fetcher.epoch()
    }

    pub fn is_network_current(&self) -> bool {
        self.fetcher.is_network_current()
    }

    pub fn suspend_network(&self) -> u64 {
        self.network_control().suspend()
    }

    pub fn resume_network(&self) -> u64 {
        self.network_control().resume()
    }

    pub fn fetch_page(
        &self,
        page: &Page,
        priority: FetchPriority,
        pin_owner: Option<&str>,
    ) -> Result<PathBuf> {
        self.fetcher.fetch(page, priority, pin_owner)
    }

    pub fn fetch_page_for_source(
        &self,
        source_id: &str,
        page: &Page,
        priority: FetchPriority,
        pin_owner: Option<&str>,
    ) -> Result<PathBuf> {
        self.fetcher
            .fetch_for_source(page, priority, pin_owner, Some(source_id))
    }

    /// Compatibility entry point. The returned canonical cache path is the
    /// artifact location; destination is no longer populated with a duplicate.
    pub fn download_page(&self, page: &Page, _destination: impl AsRef<Path>) -> Result<PathBuf> {
        self.fetch_page(page, FetchPriority::Interactive, None)
    }

    pub fn search(&self, source_id: &str, query: &str, page: u32) -> Result<Vec<Book>> {
        self.search_page(source_id, query, page)
            .map(|page| page.books)
    }

    pub fn search_page(&self, source_id: &str, query: &str, page: u32) -> Result<SourcePage> {
        self.with_network_session(|| {
            let config = self.configuration(source_id)?;
            if config.kind.eq_ignore_ascii_case("wasm") {
                self.wasm(config)?.search_page(query, page)
            } else {
                network::search_page(&self.client, config, query, page)
            }
        })
    }

    pub fn browse(&self, source_id: &str, page: u32, sort: &str) -> Result<Vec<Book>> {
        self.browse_page(source_id, page, sort)
            .map(|page| page.books)
    }

    pub fn browse_page(&self, source_id: &str, page: u32, sort: &str) -> Result<SourcePage> {
        self.with_network_session(|| {
            let config = self.configuration(source_id)?;
            if config.kind.eq_ignore_ascii_case("wasm") {
                self.wasm(config)?.browse_page(page, sort)
            } else {
                network::browse_page(&self.client, config, page, sort)
            }
        })
    }

    pub fn details(&self, book: &Book) -> Result<Book> {
        if book.source_id == "local" {
            return Ok(book.clone());
        }
        self.with_network_session(|| {
            let config = self.configuration(&book.source_id)?;
            if config.kind.eq_ignore_ascii_case("wasm") {
                self.wasm(config)?.details(book)
            } else {
                network::details(&self.client, config, book)
            }
        })
    }

    pub fn chapters(&self, book: &Book) -> Result<Vec<Chapter>> {
        self.with_network_session(|| {
            let config = self.configuration(&book.source_id)?;
            if config.kind.eq_ignore_ascii_case("wasm") {
                self.wasm(config)?.chapters(book)
            } else {
                network::chapters(&self.client, config, book)
            }
        })
    }

    pub fn pages(&self, book: &Book, chapter: &Chapter) -> Result<Vec<Page>> {
        ensure!(
            chapter.source_id == book.source_id && chapter.book_id == book.id,
            "The chapter does not belong to this manga"
        );
        self.with_network_session(|| {
            let config = self.configuration(&book.source_id)?;
            if config.kind.eq_ignore_ascii_case("wasm") {
                self.wasm(config)?.pages(book, chapter)
            } else {
                network::pages(&self.client, config, book, chapter)
            }
        })
    }

    pub fn listings(&self, source_id: &str) -> Result<Vec<(String, String)>> {
        self.with_network_session(|| self.listings_current(source_id))
    }

    fn listings_current(&self, source_id: &str) -> Result<Vec<(String, String)>> {
        let config = self.configuration(source_id)?;
        if config.kind.eq_ignore_ascii_case("wasm") {
            self.wasm(config)?.listings()
        } else {
            Ok(vec![
                ("popular".into(), "Popular".into()),
                ("latest".into(), "Latest".into()),
                (
                    if config.kind == "mangadex" {
                        "new"
                    } else {
                        "title"
                    }
                    .into(),
                    if config.kind == "mangadex" {
                        "New"
                    } else {
                        "Title"
                    }
                    .into(),
                ),
            ])
        }
    }

    pub fn fetch_repository(&self, url: &str) -> Result<Repository> {
        self.with_network_session(|| Repository::fetch(&self.public_client, url))
    }

    pub fn install_repository_source(&self, source: &RepositorySource) -> Result<SourceConfig> {
        self.with_network_session(|| source.install(&self.public_client, &self.data_dir))
    }

    pub fn image_request(&self, source_id: &str, url: &str) -> Result<Page> {
        self.with_network_session(|| self.image_request_current(source_id, url))
    }

    fn image_request_current(&self, source_id: &str, url: &str) -> Result<Page> {
        let config = self.configuration(source_id)?;
        if config.kind.eq_ignore_ascii_case("wasm") {
            self.wasm(config)?.image_request(url, None)
        } else {
            Ok(Page {
                url: url.into(),
                headers: network::image_headers_for(&self.client, config, url)?,
                local_path: None,
            })
        }
    }

    pub fn cover_page(&self, book: &Book) -> Result<Page> {
        ensure!(!book.cover.is_empty(), "This manga has no cover image");
        if book.source_id == "local" {
            return Ok(Page {
                local_path: Some(PathBuf::from(&book.cover)),
                ..Page::default()
            });
        }
        self.with_network_session(|| {
            let config = self.configuration(&book.source_id)?;
            if config.kind.eq_ignore_ascii_case("wasm") {
                self.wasm(config)?.cover_request(&book.cover)
            } else {
                Ok(Page {
                    url: book.cover.clone(),
                    headers: network::image_headers_for(&self.client, config, &book.cover)?,
                    local_path: None,
                })
            }
        })
    }

    pub fn source_settings(&self, source_id: &str) -> Result<Vec<SourceSetting>> {
        settings::load(&self.data_dir, self.configuration(source_id)?)
    }

    pub fn with_source_option(
        &self,
        source_id: &str,
        key: &str,
        value: serde_json::Value,
    ) -> Result<SourceConfig> {
        let _execution = wasm::execution_lock()?;
        settings::with_option(&self.data_dir, self.configuration(source_id)?, key, value)
    }

    pub fn configuration(&self, source_id: &str) -> Result<&SourceConfig> {
        let config = self
            .configurations
            .get(source_id)
            .with_context(|| format!("Source {source_id} is not installed"))?;
        ensure!(config.enabled, "The source is disabled");
        if config.kind == "local" {
            bail!("Local comics are read from the imported library cache");
        }
        Ok(config)
    }

    fn wasm(&self, config: &SourceConfig) -> Result<wasm::WasmSource> {
        wasm::WasmSource::new(&self.data_dir, config, self.client.clone())
            .with_context(|| format!("Load source {}", config.name))
    }

    fn with_network_session<T>(&self, work: impl FnOnce() -> Result<T>) -> Result<T> {
        network::with_session(&self.network_control(), self.network_epoch(), work)
    }
}

fn shared_http_clients() -> Result<HttpClients> {
    let mut shared = HTTP_CLIENTS
        .lock()
        .map_err(|_| anyhow!("The shared HTTP client pool is unavailable"))?;
    if let Some(clients) = shared.as_ref() {
        return Ok(clients.clone());
    }
    let client_builder = || {
        reqwest::blocking::ClientBuilder::from(
            reqwest::Client::builder()
                .user_agent("AidokuForKindle/0.1")
                .connect_timeout(Duration::from_secs(10))
                .read_timeout(Duration::from_secs(30)),
        )
        .timeout(Duration::from_secs(30))
    };
    let client = client_builder()
        .redirect(reqwest::redirect::Policy::custom(|attempt| {
            if attempt.previous().len() >= 5 {
                attempt.error("The source exceeded its redirect limit")
            } else if attempt
                .previous()
                .first()
                .is_some_and(|previous| previous.origin() != attempt.url().origin())
            {
                // reqwest does not remove custom authentication headers such
                // as X-API-Key when a server redirects to another origin.
                attempt.error(
                    "The source redirected to another origin; configure its final server URL",
                )
            } else {
                attempt.follow()
            }
        }))
        .build()
        .context("Create the authenticated source HTTP client")?;
    let public_client = client_builder()
        .redirect(reqwest::redirect::Policy::limited(5))
        .build()
        .context("Create the public source repository client")?;
    // Blocking RequestBuilder::timeout also becomes its per-read wait limit.
    // Configure the async body's independent idle limit before wrapping it so
    // the 120-second request deadline does not weaken the 30-second idle cap.
    let image_client = client_builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .context("Create the image download client")?;
    let clients = HttpClients {
        source: client,
        public: public_client,
        image: image_client,
    };
    *shared = Some(clients.clone());
    Ok(clients)
}

#[cfg(test)]
mod fetch_tests;

#[cfg(test)]
mod lifecycle_tests;

#[cfg(test)]
mod tests {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::mpsc,
        thread,
        time::{Duration, Instant},
    };

    use super::*;

    fn serve_once(
        listener: TcpListener,
        response: Vec<u8>,
    ) -> (mpsc::Receiver<String>, thread::JoinHandle<()>) {
        listener.set_nonblocking(true).unwrap();
        let (sender, receiver) = mpsc::channel();
        let thread = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(3);
            let mut connection = loop {
                match listener.accept() {
                    Ok((connection, _)) => break connection,
                    Err(error)
                        if error.kind() == std::io::ErrorKind::WouldBlock
                            && Instant::now() < deadline =>
                    {
                        thread::sleep(Duration::from_millis(5))
                    }
                    Err(error) => panic!("Fixture server could not accept a request: {error}"),
                }
            };
            connection
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut request = Vec::new();
            let mut buffer = [0; 1024];
            while !request.windows(4).any(|window| window == b"\r\n\r\n") {
                let count = connection.read(&mut buffer).unwrap();
                if count == 0 {
                    break;
                }
                request.extend_from_slice(&buffer[..count]);
            }
            sender
                .send(String::from_utf8_lossy(&request).into_owned())
                .unwrap();
            connection.write_all(&response).unwrap();
        });
        (receiver, thread)
    }

    #[test]
    fn authenticated_api_redirects_cannot_expose_custom_api_keys() {
        let target = TcpListener::bind("127.0.0.1:0").unwrap();
        target.set_nonblocking(true).unwrap();
        let origin = TcpListener::bind("127.0.0.1:0").unwrap();
        let origin_url = format!("http://{}/api/v1/series", origin.local_addr().unwrap());
        let response = format!("HTTP/1.1 302 Found\r\nLocation: http://{}/stolen\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", target.local_addr().unwrap()).into_bytes();
        let (request, server) = serve_once(origin, response);
        let temporary = tempfile::tempdir().unwrap();
        let manager = SourceManager::new(temporary.path(), &[]).unwrap();
        let error = manager
            .client
            .get(origin_url)
            .header("X-API-Key", "private-api-key")
            .send()
            .unwrap_err();
        assert!(error.is_redirect());
        assert!(
            request
                .recv_timeout(Duration::from_secs(3))
                .unwrap()
                .contains("private-api-key")
        );
        server.join().unwrap();
        assert!(
            matches!(target.accept(), Err(error) if error.kind() == std::io::ErrorKind::WouldBlock)
        );
    }

    #[test]
    fn image_redirects_remove_all_source_secrets_and_cache_valid_images() {
        let target = TcpListener::bind("127.0.0.1:0").unwrap();
        let target_url = format!("http://{}/page.png", target.local_addr().unwrap());
        let mut png = std::io::Cursor::new(Vec::new());
        image::GrayImage::from_pixel(2, 3, image::Luma([255]))
            .write_to(&mut png, image::ImageFormat::Png)
            .unwrap();
        let png = png.into_inner();
        let mut response = format!("HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", png.len()).into_bytes();
        response.extend_from_slice(&png);
        let (target_request, target_server) = serve_once(target, response);
        let origin = TcpListener::bind("127.0.0.1:0").unwrap();
        let origin_url = format!("http://{}/protected.png", origin.local_addr().unwrap());
        let response = format!("HTTP/1.1 302 Found\r\nLocation: {target_url}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").into_bytes();
        let (origin_request, origin_server) = serve_once(origin, response);
        let temporary = tempfile::tempdir().unwrap();
        let manager = SourceManager::new(temporary.path(), &[]).unwrap();
        let page = Page {
            url: origin_url,
            headers: BTreeMap::from([
                ("X-API-Key".into(), "private-api-key".into()),
                ("X-Source-Secret".into(), "private-source-secret".into()),
                ("Authorization".into(), "Bearer private-token".into()),
                ("Accept".into(), "image/*".into()),
            ]),
            local_path: None,
        };
        let saved = manager
            .download_page(&page, temporary.path().join("page.png"))
            .unwrap();
        assert_eq!(fs::read(saved).unwrap(), png);
        let first = origin_request.recv_timeout(Duration::from_secs(3)).unwrap();
        assert!(first.contains("private-api-key") && first.contains("private-source-secret"));
        let second = target_request
            .recv_timeout(Duration::from_secs(3))
            .unwrap()
            .to_ascii_lowercase();
        assert!(second.starts_with("get /page.png"));
        assert!(
            !second.contains("private-")
                && !second.contains("authorization")
                && !second.contains("x-source-secret")
        );
        origin_server.join().unwrap();
        target_server.join().unwrap();
    }
}
