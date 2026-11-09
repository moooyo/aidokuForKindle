//! Executes current Aidoku `.aix` source packages without a JIT or Apple APIs.

use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard},
};

use anyhow::{Context, Result, anyhow, bail, ensure};
use reqwest::blocking::Client;
use serde::de::DeserializeOwned;
use sha2::{Digest, Sha256};
use wasmi::{Config, Engine, ExternType, Instance, Linker, Module, Store, Val};

use super::{
    SourcePage, abi,
    wasm_host::{self, Host, MAX_BUFFER},
};
use crate::domain::{Book, Chapter, Page, SourceConfig, compound_id};

const FUEL_PER_CALL: u64 = 80_000_000;
static EXECUTION: Mutex<()> = Mutex::new(());

#[cfg(test)]
thread_local! {
    static EXECUTION_WAIT_SIGNAL: std::cell::RefCell<Option<std::sync::mpsc::Sender<()>>> = const { std::cell::RefCell::new(None) };
    static AFTER_GUEST_CALL: std::cell::RefCell<Option<Box<dyn FnOnce()>>> = const { std::cell::RefCell::new(None) };
}

pub(super) fn execution_lock() -> Result<MutexGuard<'static, ()>> {
    #[cfg(test)]
    EXECUTION_WAIT_SIGNAL.with(|signal| {
        if let Some(sender) = signal.borrow_mut().take() {
            let _ = sender.send(());
        }
    });
    EXECUTION
        .lock()
        .map_err(|_| anyhow!("The source execution lock was poisoned"))
}

pub struct WasmSource {
    store: Store<Host>,
    instance: Instance,
    config: SourceConfig,
    directory: PathBuf,
    listings: Vec<abi::Listing>,
    dynamic_listings_loaded: bool,
    // Keep the global permit for the complete instance lifetime, including
    // initialization, exports, and final store destruction.
    _execution: MutexGuard<'static, ()>,
}

impl WasmSource {
    pub fn new(data_dir: &Path, config: &SourceConfig, client: Client) -> Result<Self> {
        // A source may change its preferences during any export. Serializing
        // executions keeps concurrent background jobs from losing those writes
        // and bounds the number of active interpreter memories on Kindle.
        super::network::check_session()?;
        let execution = execution_lock()?;
        super::network::check_session()?;
        ensure!(config.enabled, "The source is disabled");
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
        let wasm_path = ["main.wasm", "source.wasm"]
            .into_iter()
            .map(|name| package.join(name))
            .find(|path| path.is_file())
            .ok_or_else(|| {
                anyhow!("The installed source has no main.wasm; reinstall its .aix package")
            })?;
        let wasm = fs::read(&wasm_path).context("Read the installed source module")?;
        ensure!(
            wasm.len() <= 32 * 1024 * 1024,
            "The source module exceeds 32 MiB"
        );
        let manifest_path = package.join("source.json");
        let manifest: serde_json::Value = if manifest_path.exists() {
            let bytes = fs::read(manifest_path).context("Read the installed source manifest")?;
            ensure!(
                bytes.len() <= 1024 * 1024,
                "The source manifest exceeds 1 MiB"
            );
            serde_json::from_slice(&bytes).context("Decode the installed source manifest")?
        } else {
            serde_json::Value::Null
        };
        if let Some(id) = manifest
            .pointer("/info/id")
            .and_then(serde_json::Value::as_str)
        {
            ensure!(
                id == config.id,
                "The installed source identity differs from its configuration"
            );
        }
        let listings = manifest
            .get("listings")
            .and_then(serde_json::Value::as_array)
            .map(|values| {
                values
                    .iter()
                    .filter_map(|value| {
                        Some(abi::Listing {
                            id: value.get("id")?.as_str()?.to_owned(),
                            name: localized_name(value.get("name")),
                            kind: if value.get("kind").and_then(serde_json::Value::as_u64)
                                == Some(1)
                            {
                                abi::ListingKind::List
                            } else {
                                abi::ListingKind::Default
                            },
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        let directory = data_dir
            .join("sources")
            .join("state")
            .join(format!("{:x}", Sha256::digest(config.id.as_bytes())));
        let mut engine_config = Config::default();
        engine_config.consume_fuel(true);
        let engine = Engine::new(&engine_config);
        let module = Module::new(&engine, &wasm)
            .context("Compile the source with the portable Wasmi interpreter")?;
        let required = [
            "get_search_manga_list",
            "get_manga_update",
            "get_page_list",
            "free_result",
        ];
        for name in required {
            ensure!(
                module.exports().any(|export| export.name() == name),
                "The source does not export {name}; legacy Aidoku source packages are not supported"
            );
        }
        let mut linker = Linker::<Host>::new(&engine);
        let mut imports = std::collections::BTreeSet::new();
        for import in module.imports() {
            let module_name = import.module().to_owned();
            let name = import.name().to_owned();
            if !imports.insert((module_name.clone(), name.clone())) {
                continue;
            }
            let ExternType::Func(function_type) = import.ty() else {
                bail!(
                    "The source imports an unsupported non-function resource: {module_name}.{name}"
                );
            };
            let captured_module = module_name.clone();
            let captured_name = name.clone();
            linker
                .func_new(
                    &module_name,
                    &name,
                    function_type.clone(),
                    move |caller, params, results| {
                        wasm_host::dispatch(
                            caller,
                            &captured_module,
                            &captured_name,
                            params,
                            results,
                        )
                        .map_err(|error| wasmi::Error::new(format!("{error:#}")))
                    },
                )
                .with_context(|| format!("Link the source import {module_name}.{name}"))?;
        }
        let mut host = Host::new(client, &directory, config)?;
        host.seed_defaults(super::settings::encoded_defaults(data_dir, config)?);
        let mut store = Store::new(&engine, host);
        store.limiter(|state| &mut state.limits);
        store.set_fuel(FUEL_PER_CALL)?;
        let instance = linker
            .instantiate(&mut store, &module)
            .context("Instantiate the source module")?
            .start(&mut store)
            .context("Start the source module")?;
        if let Ok(start) = instance.get_typed_func::<(), ()>(&store, "start") {
            super::network::check_session()?;
            let initialized = start.call(&mut store, ());
            super::network::check_session()?;
            initialized.with_context(|| format!("Initialize source {}", config.name))?;
        }
        store.data().persist_defaults()?;
        Ok(Self {
            store,
            instance,
            config: config.clone(),
            directory,
            listings,
            dynamic_listings_loaded: false,
            _execution: execution,
        })
    }

    pub fn search(&mut self, query: &str, page: u32) -> Result<Vec<Book>> {
        self.search_page(query, page).map(|page| page.books)
    }

    pub fn search_page(&mut self, query: &str, page: u32) -> Result<SourcePage> {
        ensure!(
            page > 0 && page <= i32::MAX as u32,
            "The source page number must be positive"
        );
        let result: abi::MangaPageResult = self.with_arguments(|source, arguments| {
            let query = source
                .store
                .data_mut()
                .put_buffer(query.as_bytes().to_vec())?;
            arguments.push(query);
            let filters = source.store.data_mut().put_encoded(&Vec::<()>::new())?;
            arguments.push(filters);
            source.call_result(
                "get_search_manga_list",
                &[Val::I32(query), Val::I32(page as i32), Val::I32(filters)],
            )
        })?;
        Ok(SourcePage {
            books: self.collect_books(result.entries)?,
            has_next: result.has_next_page,
        })
    }

    pub fn browse(&mut self, page: u32, listing: &str) -> Result<Vec<Book>> {
        self.browse_page(page, listing).map(|page| page.books)
    }

    pub fn browse_page(&mut self, page: u32, listing: &str) -> Result<SourcePage> {
        ensure!(
            page > 0 && page <= i32::MAX as u32,
            "The source page number must be positive"
        );
        self.load_listings()?;
        let selected = self
            .listings
            .iter()
            .find(|value| value.id == listing)
            .or_else(|| self.listings.first())
            .cloned();
        if let Some(selected) = selected {
            ensure!(
                self.instance
                    .get_func(&self.store, "get_manga_list")
                    .is_some(),
                "The source declares listings without exporting get_manga_list"
            );
            let result: abi::MangaPageResult = self.with_arguments(|source, arguments| {
                let listing = source.store.data_mut().put_encoded(&selected)?;
                arguments.push(listing);
                source.call_result(
                    "get_manga_list",
                    &[Val::I32(listing), Val::I32(page as i32)],
                )
            })?;
            Ok(SourcePage {
                books: self.collect_books(result.entries)?,
                has_next: result.has_next_page,
            })
        } else {
            self.search_page("", page)
        }
    }

    pub fn listings(&mut self) -> Result<Vec<(String, String)>> {
        self.load_listings()?;
        Ok(self
            .listings
            .iter()
            .map(|listing| (listing.id.clone(), listing.name.clone()))
            .collect())
    }

    fn load_listings(&mut self) -> Result<()> {
        if !self.dynamic_listings_loaded
            && self
                .instance
                .get_func(&self.store, "get_listings")
                .is_some()
        {
            let dynamic: Vec<abi::Listing> = self.call_result("get_listings", &[])?;
            ensure!(
                dynamic.len() <= 512,
                "The source returned too many catalog listings"
            );
            for listing in dynamic {
                if !self
                    .listings
                    .iter()
                    .any(|existing| existing.id == listing.id)
                {
                    self.listings.push(listing);
                }
            }
        }
        self.dynamic_listings_loaded = true;
        Ok(())
    }

    pub fn details(&mut self, book: &Book) -> Result<Book> {
        let manga = self.update_manga(book, true, false)?;
        Ok(self.book(&manga))
    }

    pub fn chapters(&mut self, book: &Book) -> Result<Vec<Chapter>> {
        let manga = self.update_manga(book, true, true)?;
        let chapters = manga
            .chapters
            .as_ref()
            .context("The source did not return a chapter list")?;
        let mut output = Vec::with_capacity(chapters.len());
        for chapter in chapters {
            if chapter.locked {
                continue;
            }
            output.push(Chapter {
                id: compound_id(&book.id, &chapter.key),
                book_id: book.id.clone(),
                source_id: self.config.id.clone(),
                remote_id: chapter.key.clone(),
                number: chapter.chapter_number.map_or(0.0, f64::from),
                title: chapter.title.clone().unwrap_or_else(|| {
                    chapter
                        .chapter_number
                        .map_or_else(|| chapter.key.clone(), |value| format!("Chapter {value}"))
                }),
                date: chapter
                    .date_uploaded
                    .and_then(|timestamp| chrono::DateTime::from_timestamp(timestamp, 0))
                    .map_or_else(String::new, |date| date.format("%Y-%m-%d").to_string()),
                ..Chapter::default()
            });
        }
        Ok(output)
    }

    pub fn pages(&mut self, book: &Book, chapter: &Chapter) -> Result<Vec<Page>> {
        let manga = self.cached_manga(book)?;
        let chapter_model = manga
            .chapters
            .as_ref()
            .and_then(|chapters| chapters.iter().find(|value| value.key == chapter.remote_id))
            .cloned()
            .unwrap_or_else(|| abi::Chapter {
                key: chapter.remote_id.clone(),
                title: Some(chapter.title.clone()),
                chapter_number: Some(chapter.number as f32),
                ..abi::Chapter::default()
            });
        let pages: Vec<abi::Page> = self.with_arguments(|source, arguments| {
            let manga = source.store.data_mut().put_encoded(&manga)?;
            arguments.push(manga);
            let chapter_model = source.store.data_mut().put_encoded(&chapter_model)?;
            arguments.push(chapter_model);
            source.call_result("get_page_list", &[Val::I32(manga), Val::I32(chapter_model)])
        })?;
        ensure!(
            !pages.is_empty(),
            "The source returned no pages for this chapter"
        );
        ensure!(pages.len() <= 5_000, "The source returned too many pages");
        ensure!(
            self.instance
                .get_func(&self.store, "process_page_image")
                .is_none(),
            "This source requires canvas page processing, which the Kindle source runtime does not support"
        );
        let mut output = Vec::with_capacity(pages.len());
        for page in pages {
            match page.content {
                abi::PageContent::Url(url, context) => {
                    output.push(self.image_request(&url, context.as_ref())?)
                }
                abi::PageContent::Zip(url, path) => output.push(self.zip_page(&url, &path)?),
                abi::PageContent::Text(_) => bail!(
                    "This source returned text content; the Kindle manga reader currently supports image pages"
                ),
                abi::PageContent::Image(_) => bail!(
                    "This source returned a canvas image, which the Kindle source runtime does not support"
                ),
            }
        }
        Ok(output)
    }

    pub fn image_request(
        &mut self,
        url: &str,
        context: Option<&BTreeMap<String, String>>,
    ) -> Result<Page> {
        let original =
            reqwest::Url::parse(url).context("The source returned an invalid image URL")?;
        ensure!(
            matches!(original.scheme(), "http" | "https"),
            "The source image URL must use HTTP or HTTPS"
        );
        if self
            .instance
            .get_func(&self.store, "get_image_request")
            .is_some()
        {
            let (request_url, headers) = self.with_arguments(|source, arguments| {
                let url_descriptor = source.store.data_mut().put_encoded(&url)?;
                arguments.push(url_descriptor);
                let context_descriptor = if let Some(context) = context {
                    let descriptor = source.store.data_mut().put_encoded(context)?;
                    arguments.push(descriptor);
                    descriptor
                } else {
                    -1
                };
                let id: i32 = source.call_result(
                    "get_image_request",
                    &[Val::I32(url_descriptor), Val::I32(context_descriptor)],
                )?;
                source.store.data_mut().take_image_request(id)
            })?;
            let target = reqwest::Url::parse(&request_url)
                .context("The source returned an invalid image request URL")?;
            ensure!(
                matches!(target.scheme(), "http" | "https"),
                "The source image request must use HTTP or HTTPS"
            );
            Ok(Page {
                url: target.to_string(),
                headers,
                local_path: None,
            })
        } else {
            Ok(Page {
                url: original.to_string(),
                ..Page::default()
            })
        }
    }

    pub fn cover_request(&mut self, url: &str) -> Result<Page> {
        ensure!(
            self.instance
                .get_func(&self.store, "process_cover_image")
                .is_none(),
            "This source requires canvas cover processing, which the Kindle source runtime does not support"
        );
        self.image_request(url, None)
    }

    fn update_manga(&mut self, book: &Book, details: bool, chapters: bool) -> Result<abi::Manga> {
        let manga = self.cached_manga(book)?;
        let updated: abi::Manga = self.with_arguments(|source, arguments| {
            let descriptor = source.store.data_mut().put_encoded(&manga)?;
            arguments.push(descriptor);
            source.call_result(
                "get_manga_update",
                &[
                    Val::I32(descriptor),
                    Val::I32(i32::from(details)),
                    Val::I32(i32::from(chapters)),
                ],
            )
        })?;
        ensure!(
            updated.key == book.remote_id,
            "The source changed the manga identity; source key migration is required"
        );
        self.cache_manga(&updated)
    }

    fn cached_manga(&self, book: &Book) -> Result<abi::Manga> {
        let path = self.manga_path(&book.remote_id);
        if path.exists() {
            let bytes = fs::read(path).context("Read cached source metadata")?;
            ensure!(
                bytes.len() <= MAX_BUFFER,
                "Cached source metadata exceeds its limit"
            );
            return serde_json::from_slice(&bytes).context("Decode cached source metadata");
        }
        Ok(abi::Manga {
            key: book.remote_id.clone(),
            title: book.title.clone(),
            cover: Some(book.cover.clone()),
            authors: Some(vec![book.author.clone()]),
            description: Some(book.description.clone()),
            tags: Some(book.tags.clone()),
            ..abi::Manga::default()
        })
    }

    fn cache_manga(&self, manga: &abi::Manga) -> Result<abi::Manga> {
        let mut manga = manga.clone();
        let path = self.manga_path(&manga.key);
        let mut previous_bytes = None;
        if path.exists() {
            let bytes = fs::read(&path).context("Read previous source metadata")?;
            ensure!(
                bytes.len() <= MAX_BUFFER,
                "Cached source metadata exceeds its limit"
            );
            let previous: abi::Manga =
                serde_json::from_slice(&bytes).context("Decode previous source metadata")?;
            previous_bytes = Some(bytes);
            // Catalog entries are often sparse. They must not erase the raw
            // chapter metadata needed by get_page_list after a later search.
            if manga.title.is_empty() {
                manga.title = previous.title;
            }
            if manga.cover.is_none() {
                manga.cover = previous.cover;
            }
            if manga.artists.is_none() {
                manga.artists = previous.artists;
            }
            if manga.authors.is_none() {
                manga.authors = previous.authors;
            }
            if manga.description.is_none() {
                manga.description = previous.description;
            }
            if manga.url.is_none() {
                manga.url = previous.url;
            }
            if manga.tags.is_none() {
                manga.tags = previous.tags;
            }
            if manga.chapters.is_none() {
                manga.chapters = previous.chapters;
            }
            if manga.next_update_time.is_none() {
                manga.next_update_time = previous.next_update_time;
            }
            if manga.status == abi::MangaStatus::Unknown {
                manga.status = previous.status;
            }
        }
        let bytes = serde_json::to_vec(&manga).context("Encode cached source metadata")?;
        ensure!(
            bytes.len() <= MAX_BUFFER,
            "Source metadata exceeds its limit"
        );
        if previous_bytes.as_deref() != Some(bytes.as_slice()) {
            super::network::check_session()?;
            super::settings::atomic_write(&path, &bytes).context("Cache source metadata")?;
        }
        Ok(manga)
    }

    fn manga_path(&self, key: &str) -> PathBuf {
        self.directory
            .join(format!("manga-{:x}.json", Sha256::digest(key.as_bytes())))
    }

    fn collect_books(&self, entries: Vec<abi::Manga>) -> Result<Vec<Book>> {
        ensure!(
            entries.len() <= 1_000,
            "The source returned too many manga entries"
        );
        let mut books = Vec::with_capacity(entries.len());
        for manga in entries {
            ensure!(
                !manga.key.is_empty(),
                "The source returned a manga without an identity"
            );
            let manga = self.cache_manga(&manga)?;
            books.push(self.book(&manga));
        }
        Ok(books)
    }

    fn book(&self, manga: &abi::Manga) -> Book {
        Book {
            id: compound_id(&self.config.id, &manga.key),
            source_id: self.config.id.clone(),
            remote_id: manga.key.clone(),
            title: manga.title.clone(),
            author: manga
                .authors
                .as_ref()
                .or(manga.artists.as_ref())
                .map(|authors| authors.join(", "))
                .unwrap_or_default(),
            description: manga.description.clone().unwrap_or_default(),
            tags: manga.tags.clone().unwrap_or_default(),
            status: match manga.status {
                abi::MangaStatus::Ongoing => "ongoing",
                abi::MangaStatus::Completed => "completed",
                abi::MangaStatus::Cancelled => "cancelled",
                abi::MangaStatus::Hiatus => "hiatus",
                _ => "unknown",
            }
            .into(),
            cover: manga.cover.clone().unwrap_or_default(),
            chapter_count: manga
                .chapters
                .as_ref()
                .map_or(0, |chapters| chapters.len() as u32),
            category: "reading".into(),
            ..Book::default()
        }
    }

    fn with_arguments<T>(
        &mut self,
        operation: impl FnOnce(&mut Self, &mut Vec<i32>) -> Result<T>,
    ) -> Result<T> {
        let mut arguments = Vec::new();
        let result = operation(self, &mut arguments);
        // The current ABI borrows input descriptors for one export. Only the
        // wrapper knows which i32 values are resources rather than page/flags.
        for descriptor in arguments {
            self.store.data_mut().release_resource(descriptor);
        }
        result
    }

    fn call_result<T: DeserializeOwned>(&mut self, name: &str, params: &[Val]) -> Result<T> {
        super::network::check_session()?;
        self.store.set_fuel(FUEL_PER_CALL)?;
        self.store.data_mut().reset_deadline();
        let function = self
            .instance
            .get_func(&self.store, name)
            .ok_or_else(|| anyhow!("The source does not export {name}"))?;
        let mut results = [Val::I32(0)];
        let called = function.call(&mut self.store, params, &mut results);
        #[cfg(test)]
        AFTER_GUEST_CALL.with(|hook| {
            let callback = hook.borrow_mut().take();
            if let Some(callback) = callback {
                callback();
            }
        });
        let session = super::network::check_session();
        if let Err(error) = called {
            session?;
            return Err(error)
                .with_context(|| format!("Source {} failed in {name}", self.config.name));
        }
        let Some(pointer) = results[0].i32().filter(|pointer| *pointer > 0) else {
            session?;
            bail!(
                "Source {} returned invalid result {:?} from {name}",
                self.config.name,
                results[0]
            );
        };
        let decoded = session.as_ref().ok().map(|_| self.decode_result(pointer));
        // Even a result from the closed epoch still belongs to the guest.
        // Release it before reporting cancellation; input RIDs are scoped by
        // with_arguments and are released when this method returns an error.
        let released = self
            .instance
            .get_typed_func::<i32, ()>(&self.store, "free_result")
            .map_err(anyhow::Error::from)
            .and_then(|release| {
                release
                    .call(&mut self.store, pointer)
                    .map_err(anyhow::Error::from)
            });
        session?;
        super::network::check_session()?;
        let value = decoded
            .expect("An active source result was decoded")
            .with_context(|| format!("Decode the {name} result from {}", self.config.name))?;
        released.context("Release the source result buffer")?;
        self.store.data().persist_defaults()?;
        Ok(value)
    }

    fn decode_result<T: DeserializeOwned>(&self, pointer: i32) -> Result<T> {
        let memory = self
            .instance
            .get_memory(&self.store, "memory")
            .context("The source does not export memory")?;
        let mut header = [0; 8];
        memory
            .read(&self.store, pointer as usize, &mut header)
            .context("Read the source result header")?;
        let length = i32::from_le_bytes(header[..4].try_into().expect("Four-byte result length"));
        if length == -1 {
            let mut size = [0; 4];
            memory.read(&self.store, pointer as usize + 8, &mut size)?;
            let length = i32::from_le_bytes(size);
            ensure!(
                (12..=MAX_BUFFER as i32).contains(&length),
                "The source error message has an invalid length"
            );
            let mut bytes = vec![0; length as usize - 12];
            memory.read(&self.store, pointer as usize + 12, &mut bytes)?;
            bail!("{}", String::from_utf8_lossy(&bytes));
        }
        ensure!(
            (8..=MAX_BUFFER as i32).contains(&length),
            "The source result exceeds its memory boundary"
        );
        let mut bytes = vec![0; length as usize - 8];
        memory
            .read(&self.store, pointer as usize + 8, &mut bytes)
            .context("Read the source result data")?;
        postcard::from_bytes(&bytes).context("Decode the Aidoku Postcard ABI")
    }

    fn zip_page(&mut self, archive_url: &str, path: &str) -> Result<Page> {
        validate_zip_entry(path)?;
        let archive = self.image_request(archive_url, None)?;
        zip_archive_url(&archive.url)?;
        validate_zip_source(&self.config.id)?;
        ensure!(
            self.config.version > 0,
            "The ZIP source version must be positive"
        );
        let mut descriptor = reqwest::Url::parse("aidoku-zip://page/")?;
        descriptor
            .query_pairs_mut()
            .append_pair("archive", &archive.url)
            .append_pair("path", path)
            .append_pair("source", &self.config.id)
            .append_pair("version", &self.config.version.to_string());
        Ok(Page {
            url: descriptor.to_string(),
            headers: archive.headers,
            local_path: None,
        })
    }
}

#[derive(Debug, Clone)]
pub(super) struct ZipRequest {
    pub archive: Page,
    pub entry: String,
}

/// Decodes a lazy ZIP page without performing network or filesystem work.
pub(super) fn zip_request(page: &Page) -> Result<Option<ZipRequest>> {
    if !page
        .url
        .split_once(':')
        .is_some_and(|(scheme, _)| scheme.eq_ignore_ascii_case("aidoku-zip"))
    {
        return Ok(None);
    }
    let descriptor = reqwest::Url::parse(&page.url).context("Invalid ZIP page descriptor URL")?;
    ensure!(
        descriptor.scheme() == "aidoku-zip"
            && descriptor.host_str() == Some("page")
            && descriptor.path() == "/"
            && descriptor.username().is_empty()
            && descriptor.password().is_none()
            && descriptor.port().is_none()
            && descriptor.fragment().is_none()
            && page.local_path.is_none(),
        "Invalid ZIP page descriptor authority or path"
    );
    let mut fields = BTreeMap::new();
    for (key, value) in descriptor.query_pairs() {
        ensure!(
            matches!(key.as_ref(), "archive" | "path" | "source" | "version"),
            "Unknown ZIP page descriptor field"
        );
        ensure!(
            fields
                .insert(key.into_owned(), value.into_owned())
                .is_none(),
            "Duplicate ZIP page descriptor field"
        );
    }
    let archive_url = fields
        .get("archive")
        .context("The ZIP page descriptor has no archive URL")?;
    let archive_url = zip_archive_url(archive_url)?;
    let entry = fields
        .get("path")
        .context("The ZIP page descriptor has no entry path")?;
    validate_zip_entry(entry)?;
    let source = fields
        .get("source")
        .context("The ZIP page descriptor has no source id")?;
    validate_zip_source(source)?;
    let version = fields
        .get("version")
        .context("The ZIP page descriptor has no source version")?;
    ensure!(
        !version.is_empty()
            && version.bytes().all(|value| value.is_ascii_digit())
            && version.parse::<u32>().is_ok_and(|value| value > 0),
        "The ZIP page descriptor has an invalid source version"
    );
    Ok(Some(ZipRequest {
        archive: Page {
            url: archive_url.to_string(),
            headers: page.headers.clone(),
            local_path: None,
        },
        entry: entry.clone(),
    }))
}

fn zip_archive_url(value: &str) -> Result<reqwest::Url> {
    let url =
        reqwest::Url::parse(value).context("The ZIP page descriptor has an invalid archive URL")?;
    ensure!(
        matches!(url.scheme(), "http" | "https")
            && url.host_str().is_some()
            && url.username().is_empty()
            && url.password().is_none()
            && url.fragment().is_none(),
        "The ZIP page archive URL must use HTTP or HTTPS without credentials or a fragment"
    );
    Ok(url)
}

fn validate_zip_entry(entry: &str) -> Result<()> {
    ensure!(
        !entry.is_empty()
            && entry.len() <= 4_096
            && !entry.contains('\\')
            && !entry.contains(':')
            && !entry.contains('\0')
            && entry
                .split('/')
                .all(|part| !part.is_empty() && part != "." && part != ".."),
        "The source returned an unsafe ZIP page path"
    );
    Ok(())
}

fn validate_zip_source(source: &str) -> Result<()> {
    ensure!(
        !source.is_empty()
            && source.len() <= 160
            && source.as_bytes()[0].is_ascii_alphanumeric()
            && source
                .bytes()
                .all(|value| value.is_ascii_alphanumeric() || matches!(value, b'.' | b'-'))
            && !source.contains("..")
            && !source.ends_with('.'),
        "The ZIP page descriptor has an invalid source id"
    );
    Ok(())
}

fn localized_name(value: Option<&serde_json::Value>) -> String {
    value
        .and_then(|value| {
            value.as_str().map(str::to_owned).or_else(|| {
                value
                    .get("zh-CN")
                    .or_else(|| value.get("zh"))
                    .or_else(|| value.get("en"))
                    .and_then(serde_json::Value::as_str)
                    .map(str::to_owned)
            })
        })
        .unwrap_or_default()
}

#[cfg(test)]
#[path = "wasm_tests.rs"]
mod tests;
