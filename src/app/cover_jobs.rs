//! Background preparation of bounded visible cover textures.

use super::*;

impl App {
    pub(super) fn request_covers(&mut self, _books: &[Book]) -> Result<()> {
        // A result may contain hundreds of books. Only the currently visible
        // rows are eligible for the single cover worker after sync_view.
        self.cover_view.clear();
        Ok(())
    }

    pub(super) fn visible_cover_ids(&self) -> Vec<String> {
        let Some(ui) = self.ui.upgrade() else {
            return Vec::new();
        };
        let state = ui.global::<State>();
        let large = self.platform.profile().is_scribe();
        let mut ids = Vec::new();
        match self.route.as_str() {
            "library" => {
                if state.get_library_count() > 0 && (large || !self.selection_mode) {
                    ids.push(state.get_current_book().id.to_string());
                }
                let books = state.get_books();
                for row in 0..books.row_count() {
                    if let Some(book) = books.row_data(row) {
                        ids.push(book.id.to_string());
                    }
                }
            }
            "detail" if large || self.page == 1 => {
                ids.push(state.get_current_book().id.to_string());
            }
            "catalog" | "sources"
                if self.route == "catalog" || large && self.source_filter == 0 =>
            {
                let books = state.get_catalog();
                for row in 0..books.row_count() {
                    if let Some(book) = books.row_data(row) {
                        ids.push(book.id.to_string());
                    }
                }
            }
            "search" => {
                let typing = self.keyboard_target == "search"
                    && (self.modal == "keyboard"
                        || self.modal == "choices" && self.modal_context == "search-scope:typing");
                if typing && large {
                    let books = state.get_search_local_matches();
                    for row in 0..books.row_count() {
                        if let Some(book) = books.row_data(row) {
                            ids.push(book.id.to_string());
                        }
                    }
                } else if !typing {
                    let groups = state.get_search_groups();
                    for row in 0..groups.row_count() {
                        if let Some(group) = groups.row_data(row) {
                            for item in 0..group.books.row_count() {
                                if let Some(book) = group.books.row_data(item) {
                                    ids.push(book.id.to_string());
                                }
                            }
                        }
                    }
                }
            }
            "history" => {
                let history = state.get_history();
                for row in 0..history.row_count() {
                    if let Some(book) = history.row_data(row) {
                        ids.push(book.id.to_string());
                    }
                }
            }
            "settings" if state.get_settings_section() == "wifi-sleep-exit" => {
                ids.push(state.get_current_book().id.to_string());
            }
            _ => {}
        }
        if self.modal == "downloads" {
            let queue = state.get_download_queue();
            for row in 0..queue.row_count() {
                if let Some(job) = queue.row_data(row) {
                    ids.push(job.book_id.to_string());
                }
            }
        }
        if self.modal == "choices" && self.modal_context == "migration" {
            let candidates = state.get_migration_candidates();
            for row in 0..candidates.row_count() {
                if let Some(book) = candidates.row_data(row) {
                    ids.push(book.id.to_string());
                }
            }
        }
        let mut unique = HashSet::new();
        ids.retain(|id| !id.is_empty() && unique.insert(id.clone()));
        ids
    }

    fn visible_cover_book(&self, id: &str) -> Result<Option<Book>> {
        if let Some(book) = self.migration_cover_book(id) {
            return Ok(Some(book));
        }
        self.cover_book_for_view(id)
    }

    fn visible_cover_options(&self, id: &str) -> ImageOptions {
        let large_detail = self.platform.profile().is_scribe()
            && self.route == "detail"
            && self.current_book_id.as_deref() == Some(id);
        let (width, height) = if large_detail { (612, 840) } else { (376, 514) };
        ImageOptions {
            width,
            height,
            crop_borders: false,
            dither: true,
            contrast: 0,
        }
    }

    fn prepared_cover(&self, book: &Book, options: &ImageOptions) -> bool {
        let path = self
            .cover_paths
            .get(&book.id)
            .filter(|_| self.cover_uris.get(&book.id) == Some(&book.cover));
        let image = path
            .and_then(|path| self.cover_cache.get(&(book.id.clone(), path.clone())))
            .or_else(|| {
                self.cover_cache
                    .get(&(book.id.clone(), PathBuf::from(&book.cover)))
            });
        image.is_some_and(|image| {
            let size = image.size();
            cover_pixels_fit(size.width, size.height, options)
        })
    }

    fn retain_visible_cover_state(&mut self, needed: &HashSet<String>) {
        self.cover_cache.retain(|(id, _), _| needed.contains(id));
        self.cover_paths.retain(|id, _| needed.contains(id));
        self.cover_uris.retain(|id, _| needed.contains(id));
        self.cover_retry_after.retain(|id, _| needed.contains(id));
        self.cover_pending.retain(|id| needed.contains(id));
        self.cover_queue.retain(|book| needed.contains(&book.id));
    }

    pub(super) fn refresh_visible_covers(&mut self) -> Result<bool> {
        if self.shutting_down || self.suspended || self.resume_frame_pending {
            return Ok(false);
        }
        let now = Instant::now();
        if self
            .cover_retry_after
            .values()
            .any(|deadline| *deadline <= now)
        {
            self.cover_retry_after.retain(|_, deadline| *deadline > now);
            self.cover_view.clear();
        }
        let visible = self.visible_cover_ids();
        let needed = visible.iter().cloned().collect::<HashSet<_>>();
        self.retain_visible_cover_state(&needed);
        // A one-book library and its detail view can have identical IDs while
        // the detail cover needs a larger resident texture.
        let needs_larger_texture = visible.iter().any(|id| {
            if self.cover_pending.contains(id) || self.cover_retry_after.contains_key(id) {
                return false;
            }
            let Some(path) = self.cover_paths.get(id) else {
                return false;
            };
            self.cover_cache
                .get(&(id.clone(), path.clone()))
                .is_some_and(|image| {
                    let size = image.size();
                    !cover_pixels_fit(size.width, size.height, &self.visible_cover_options(id))
                })
        });
        if visible != self.cover_view || needs_larger_texture {
            self.cover_view = visible.clone();
            for id in visible {
                if self.cover_pending.contains(&id) || self.cover_retry_after.contains_key(&id) {
                    continue;
                }
                let Some(book) = self.visible_cover_book(&id)? else {
                    continue;
                };
                if book.cover.is_empty()
                    || self.prepared_cover(&book, &self.visible_cover_options(&id))
                {
                    continue;
                }
                if self.cover_pending.insert(id) {
                    self.cover_queue.push_back(book);
                }
            }
        }
        self.start_cover_job()
    }

    pub(super) fn start_cover_job(&mut self) -> Result<bool> {
        if self.shutting_down
            || self.suspended
            || self.resume_frame_pending
            || self.cover_inflight.is_some()
        {
            return Ok(false);
        }
        while let Some(queued) = self.cover_queue.pop_front() {
            let id = queued.id;
            if !self.cover_view.contains(&id) {
                self.cover_pending.remove(&id);
                continue;
            }
            let Some(book) = self.visible_cover_book(&id)? else {
                self.cover_pending.remove(&id);
                continue;
            };
            let options = self.visible_cover_options(&id);
            if book.cover.is_empty() || self.prepared_cover(&book, &options) {
                self.cover_pending.remove(&id);
                continue;
            }
            let migration_origin = self.modal_context == "migration"
                && self.current_book_id.as_deref() == Some(id.as_str());
            let allow_network = !self.options.offline
                && !self.options.demo
                && !self.resume_network_pending
                && !migration_origin;
            // Reuse the same snapshot source_jobs needs for manager creation.
            // Looking up a disabled origin must not call configuration(), which
            // rejects it before its existing cache aliases can be inspected.
            let result = (|| -> Result<u64> {
                let configurations = match self.source_configurations.as_ref() {
                    Some(configurations) => configurations.clone(),
                    None => {
                        let configurations = Arc::new(self.store.sources()?);
                        self.source_configurations = Some(configurations.clone());
                        configurations
                    }
                };
                let configuration = configurations
                    .iter()
                    .find(|source| source.id == book.source_id)
                    .cloned();
                self.spawn_job_kind(false, JobKind::Cover(id.clone()), move |manager| {
                    prepare_cover(manager, book, options, allow_network, configuration)
                })
            })();
            match result {
                Ok(job) => {
                    self.cover_inflight = Some(job);
                    break;
                }
                Err(error) => {
                    self.cover_pending.remove(&id);
                    self.cover_retry_after
                        .insert(id.clone(), Instant::now() + Duration::from_secs(30));
                    eprintln!("aidoku: cover {id}: {error:#}");
                }
            }
        }
        Ok(false)
    }

    pub(super) fn accept_cover(
        &mut self,
        context: &JobContext,
        id: String,
        uri: String,
        path: PathBuf,
        image: prepared_pixels::PreparedPixels,
    ) -> Result<()> {
        self.cover_pending.remove(&id);
        self.cover_retry_after.remove(&id);
        if self.shutting_down
            || self.suspended
            || self.resume_frame_pending
            || context.lifecycle_generation != self.lifecycle_generation
            || context.source_revision != self.source_revision
        {
            self.cover_view.clear();
            return Ok(());
        }
        let visible = self.visible_cover_ids();
        let needed = visible.iter().cloned().collect::<HashSet<_>>();
        self.retain_visible_cover_state(&needed);
        if !needed.contains(&id) {
            return Ok(());
        }
        let Some(book) = self.visible_cover_book(&id)? else {
            self.cover_view.clear();
            return Ok(());
        };
        if book.cover != uri {
            self.cover_view.clear();
            return Ok(());
        }
        anyhow::ensure!(
            image.width() > 0 && image.height() > 0,
            "The prepared cover contains no pixels"
        );
        let texture = image.into_image();
        self.cover_cache
            .retain(|(cached_id, _), _| cached_id != &id);
        self.cover_cache.insert((id.clone(), path.clone()), texture);
        self.cover_uris.insert(id.clone(), uri);
        self.cover_paths.insert(id, path);
        if self.modal == "downloads" {
            self.download_ui_dirty = true;
        }
        Ok(())
    }
}

fn cover_pixels_fit(width: u32, height: u32, options: &ImageOptions) -> bool {
    width > 0 && height > 0 && (width >= options.width || height >= options.height)
}

pub(super) fn local_cover_path(cover: &str, local_source: bool) -> Result<Option<PathBuf>> {
    if let Ok(url) = url::Url::parse(cover) {
        if url.scheme() == "file" {
            return url
                .to_file_path()
                .map(Some)
                .map_err(|_| anyhow::anyhow!("The local cover URI is not a filesystem path"));
        }
        if !local_source && !windows_drive_path(cover) {
            return Ok(None);
        }
    }
    let path = PathBuf::from(cover);
    Ok((local_source || path.is_absolute() || windows_drive_path(cover)).then_some(path))
}

fn windows_drive_path(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && matches!(bytes[2], b'/' | b'\\')
}

pub(super) fn remote_cover_identity(book: &Book, configuration: &SourceConfig) -> Result<String> {
    // Include credentials and request options without persisting plaintext in
    // an alias or render key. Listing labels and connection verification are
    // derived UI metadata updated without invalidating the source pool.
    let mut configuration = configuration.clone();
    configuration.options.remove("_cached_listings");
    configuration.options.remove("_connection_verified");
    // Disabling a source closes network admission without changing the bytes
    // or credentials of its already cached images.
    configuration.enabled = true;
    cover_identity_with_configuration(book, &configuration)
}

fn cover_identity_with_configuration(book: &Book, configuration: &SourceConfig) -> Result<String> {
    let mut hash = Sha256::new();
    hash.update(b"aidoku-cover-source-v2");
    for value in [book.id.as_bytes(), book.cover.as_bytes()] {
        hash.update((value.len() as u64).to_le_bytes());
        hash.update(value);
    }
    let configuration = serde_json::to_vec(configuration)?;
    hash.update((configuration.len() as u64).to_le_bytes());
    hash.update(configuration);
    Ok(format!("{:x}", hash.finalize()))
}

pub(super) fn legacy_metadata_cover_identities(
    book: &Book,
    configuration: &SourceConfig,
) -> Result<Vec<String>> {
    // C3 excluded cached listings but retained the boolean verification salt.
    // Only that known UI metadata varies: account credentials, source kind,
    // version, URI, and every request option remain exactly current.
    let mut configuration = configuration.clone();
    configuration.enabled = true;
    configuration.options.remove("_cached_listings");
    let first = configuration
        .options
        .get("_connection_verified")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    [first, !first]
        .into_iter()
        .map(|verified| {
            configuration
                .options
                .insert("_connection_verified".into(), json!(verified));
            cover_identity_with_configuration(book, &configuration)
        })
        .collect()
}

pub(super) fn cover_source_alias(identity: &str) -> String {
    format!("cover-source-v2:{identity}")
}

fn cover_render_alias(identity: &str, options: &ImageOptions) -> String {
    format!(
        "cover-render-v2:{identity}:{}x{}:{}:{}:{}",
        options.width, options.height, options.crop_borders, options.dither, options.contrast
    )
}

fn local_cover_render_alias(
    book: &Book,
    raw: &Path,
    rendered: &crate::render_cache::RenderedPage,
    options: &ImageOptions,
) -> Option<String> {
    if rendered.cache_path.as_path() == raw {
        return None;
    }
    let mut hash = Sha256::new();
    hash.update(b"aidoku-local-cover-render-v2");
    let path = rendered.cache_path.to_string_lossy();
    for value in [book.id.as_bytes(), book.cover.as_bytes(), path.as_bytes()] {
        hash.update((value.len() as u64).to_le_bytes());
        hash.update(value);
    }
    Some(cover_render_alias(
        &format!("{:x}", hash.finalize()),
        options,
    ))
}

pub(super) fn cover_source_allows_network(configuration: &SourceConfig) -> bool {
    configuration.enabled
        && matches!(
            configuration.kind.to_ascii_lowercase().as_str(),
            "komga" | "kavita" | "suwayomi" | "mangadex" | "wasm"
        )
}

fn prepare_cover(
    manager: Arc<SourceManager>,
    book: Book,
    options: ImageOptions,
    allow_network: bool,
    configuration: Option<SourceConfig>,
) -> Result<JobResult> {
    imaging::with_background_decode(None, || {
        prepare_cover_pixels(manager, book, options, allow_network, configuration)
    })
}

fn prepare_cover_pixels(
    manager: Arc<SourceManager>,
    book: Book,
    options: ImageOptions,
    allow_network: bool,
    configuration: Option<SourceConfig>,
) -> Result<JobResult> {
    let cache = manager.cache();
    let local = local_cover_path(&book.cover, book.source_id == "local")?.or_else(|| {
        (url::Url::parse(&book.cover).is_err() && Path::new(&book.cover).is_file())
            .then(|| PathBuf::from(&book.cover))
    });
    let rendered = if let Some(path) = local {
        let request = Page {
            local_path: Some(path),
            ..Page::default()
        };
        let raw = cache
            .lookup_raw(&request)?
            .context("The local cover image is unavailable")?;
        let rendered = crate::render_cache::render(cache, &request, &raw, &options, false, 0, 1.0)?;
        manager
            .network_control()
            .check_epoch(manager.network_epoch())?;
        if let Some(alias) = local_cover_render_alias(&book, &raw, &rendered, &options) {
            publish_rendered_cover_alias(cache, &alias, &raw, &rendered);
        }
        rendered
    } else {
        let configuration = configuration.context("The cover source is not installed")?;
        let allow_network = allow_network && cover_source_allows_network(&configuration);
        let identity = remote_cover_identity(&book, &configuration)?;
        let source_alias = cover_source_alias(&identity);
        let render_alias = cover_render_alias(&identity, &options);
        // This synthetic identity is used only for rendering. Resolving a real
        // cover request can evaluate WASM or log in, so cache-only work never
        // calls cover_page(), even when the image URL already looks complete.
        let render_identity = Page {
            url: format!("aidoku-cover:{identity}"),
            ..Page::default()
        };
        let raw = cache.lookup_alias(&source_alias)?;
        // A concurrent clear can remove the artifact between alias lookup and
        // leasing. That is a cache miss, so the processed fallback still runs.
        let leased_raw = raw.and_then(|raw| cache.lease_path(&raw).ok().map(|lease| (raw, lease)));
        if let Some((raw, _lease)) = leased_raw {
            let rendered = crate::render_cache::render(
                cache,
                &render_identity,
                &raw,
                &options,
                false,
                0,
                1.0,
            )?;
            manager
                .network_control()
                .check_epoch(manager.network_epoch())?;
            publish_rendered_cover_alias(cache, &render_alias, &raw, &rendered);
            rendered
        } else if let Some(rendered) =
            crate::render_cache::load_alias(cache, &render_alias, &options)?
        {
            rendered
        } else if let Some(rendered) = migrate_legacy_cover_aliases(
            manager.as_ref(),
            &book,
            &configuration,
            &identity,
            &options,
        )? {
            rendered
        } else {
            anyhow::ensure!(
                allow_network,
                "The cover image is not available in the offline cache"
            );
            let request = manager.cover_page(&book)?;
            let raw = manager.fetch_page_for_source(
                &book.source_id,
                &request,
                FetchPriority::Background,
                None,
            )?;
            let _lease = cache.lease_path(&raw)?;
            let rendered = crate::render_cache::render(
                cache,
                &render_identity,
                &raw,
                &options,
                false,
                0,
                1.0,
            )?;
            manager
                .network_control()
                .check_epoch(manager.network_epoch())?;
            if let Err(error) = cache.set_alias(&source_alias, &raw) {
                eprintln!("aidoku: cover source alias: {error:#}");
            }
            publish_rendered_cover_alias(cache, &render_alias, &raw, &rendered);
            rendered
        }
    };
    manager
        .network_control()
        .check_epoch(manager.network_epoch())?;
    Ok(JobResult::Cover {
        id: book.id,
        uri: book.cover,
        path: rendered.cache_path,
        image: prepared_pixels::prepare(rendered.image),
    })
}

fn migrate_legacy_cover_aliases(
    manager: &SourceManager,
    book: &Book,
    configuration: &SourceConfig,
    identity: &str,
    options: &ImageOptions,
) -> Result<Option<crate::render_cache::RenderedPage>> {
    let cache = manager.cache();
    let source_alias = cover_source_alias(identity);
    let render_alias = cover_render_alias(identity, options);
    let render_identity = Page {
        url: format!("aidoku-cover:{identity}"),
        ..Page::default()
    };
    let legacy_identities = legacy_metadata_cover_identities(book, configuration)?;
    // Every known raw source takes precedence over every legacy derivative,
    // including one saved under the other verification metadata value.
    for legacy_identity in &legacy_identities {
        manager
            .network_control()
            .check_epoch(manager.network_epoch())?;
        let raw = cache.lookup_alias(&cover_source_alias(&legacy_identity))?;
        let leased_raw = raw.and_then(|raw| cache.lease_path(&raw).ok().map(|lease| (raw, lease)));
        if let Some((raw, _lease)) = leased_raw {
            // A present source wins over an older derivative. Rendering from
            // its validated fingerprint also produces the canonical key.
            let rendered =
                crate::render_cache::render(cache, &render_identity, &raw, options, false, 0, 1.0)?;
            manager
                .network_control()
                .check_epoch(manager.network_epoch())?;
            if let Err(error) = cache.set_alias(&source_alias, &raw) {
                eprintln!("aidoku: promoted cover source alias: {error:#}");
            }
            publish_rendered_cover_alias(cache, &render_alias, &raw, &rendered);
            return Ok(Some(rendered));
        }
    }
    for legacy_identity in &legacy_identities {
        manager
            .network_control()
            .check_epoch(manager.network_epoch())?;
        if let Some(rendered) = crate::render_cache::load_alias(
            cache,
            &cover_render_alias(&legacy_identity, options),
            options,
        )? {
            // Keep the validated artifact alive only through alias promotion;
            // the returned pixel Arc does not retain a persistent disk lease.
            let Ok(_lease) = cache.lease_path(&rendered.cache_path) else {
                continue;
            };
            manager
                .network_control()
                .check_epoch(manager.network_epoch())?;
            publish_processed_cover_alias(cache, &render_alias, &rendered.cache_path);
            return Ok(Some(rendered));
        }
    }
    Ok(None)
}

fn publish_rendered_cover_alias(
    cache: &crate::cache::CacheManager,
    render_alias: &str,
    raw: &Path,
    rendered: &crate::render_cache::RenderedPage,
) {
    // render() can intentionally fall back to an unpersisted raw path under
    // storage pressure. Such a file is not a processed PNG alias.
    if rendered.cache_path.as_path() == raw {
        return;
    }
    publish_processed_cover_alias(cache, render_alias, &rendered.cache_path);
}

fn publish_processed_cover_alias(cache: &crate::cache::CacheManager, alias: &str, path: &Path) {
    if cache.lookup_alias(alias).ok().flatten().as_deref() == Some(path) {
        return;
    }
    if let Err(error) = cache.set_alias(alias, path) {
        eprintln!("aidoku: cover rendered alias: {error:#}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{ImageFormat, Luma};
    use std::io::Cursor;

    struct LegacyCoverFixture {
        manager: Arc<SourceManager>,
        book: Book,
        configuration: SourceConfig,
        options: ImageOptions,
        identity: String,
        raw: PathBuf,
        rendered: crate::render_cache::RenderedPage,
        _directory: tempfile::TempDir,
    }

    impl LegacyCoverFixture {
        fn store_raw(cache: &crate::cache::CacheManager, book: &Book, gray: u8) -> PathBuf {
            let mut encoded = Cursor::new(Vec::new());
            GrayImage::from_pixel(8, 12, Luma([gray]))
                .write_to(&mut encoded, ImageFormat::Png)
                .unwrap();
            let bytes = encoded.into_inner();
            let request = Page {
                url: book.cover.clone(),
                headers: [("Authorization".into(), "Bearer account-token".into())].into(),
                ..Page::default()
            };
            let write = cache.begin_raw(&request, None, bytes.len() as u64).unwrap();
            fs::write(write.temp_path(), bytes).unwrap();
            write.commit().unwrap()
        }

        fn new(verified: bool) -> Self {
            let directory = tempfile::tempdir().unwrap();
            let book = Book {
                id: "source:book".into(),
                source_id: "source".into(),
                cover: "https://example.test/cover.png".into(),
                ..Book::default()
            };
            let mut configuration = SourceConfig {
                id: "source".into(),
                kind: "komga".into(),
                base_url: "https://example.test".into(),
                token: "account-token".into(),
                version: 3,
                enabled: true,
                ..SourceConfig::default()
            };
            configuration
                .options
                .insert("_connection_verified".into(), json!(verified));
            let manager =
                Arc::new(SourceManager::new(directory.path(), &[configuration.clone()]).unwrap());
            let cache = manager.cache();
            let raw = Self::store_raw(cache, &book, 170);
            let options = ImageOptions {
                width: 8,
                height: 12,
                crop_borders: false,
                dither: false,
                contrast: 0,
            };
            // Seed exactly the C3 configuration serialization, including the
            // explicit verification boolean, rather than a canonical C4 key.
            let identity = cover_identity_with_configuration(&book, &configuration).unwrap();
            let render_identity = Page {
                url: format!("aidoku-cover:{identity}"),
                ..Page::default()
            };
            let rendered =
                crate::render_cache::render(cache, &render_identity, &raw, &options, false, 0, 1.0)
                    .unwrap();
            cache
                .set_alias(&cover_source_alias(&identity), &raw)
                .unwrap();
            cache
                .set_alias(
                    &cover_render_alias(&identity, &options),
                    &rendered.cache_path,
                )
                .unwrap();
            Self {
                manager,
                book,
                configuration,
                options,
                identity,
                raw,
                rendered,
                _directory: directory,
            }
        }
    }

    #[test]
    fn legacy_verification_raw_aliases_are_promoted_without_network() {
        for verified in [false, true] {
            let fixture = LegacyCoverFixture::new(verified);
            let mut configuration = fixture.configuration.clone();
            configuration.enabled = false;
            let identity = remote_cover_identity(&fixture.book, &configuration).unwrap();
            let cache = fixture.manager.cache();
            assert_ne!(identity, fixture.identity);
            assert!(
                cache
                    .lookup_alias(&cover_source_alias(&identity))
                    .unwrap()
                    .is_none()
            );
            let result = prepare_cover(
                fixture.manager.clone(),
                fixture.book.clone(),
                fixture.options.clone(),
                false,
                Some(configuration),
            )
            .unwrap();
            let JobResult::Cover { path, image, .. } = result else {
                panic!("Expected the cached cover result");
            };
            assert_eq!(
                image.as_bytes(),
                prepared_pixels::prepare(fixture.rendered.image.clone()).as_bytes()
            );
            assert_eq!(
                cache.lookup_alias(&cover_source_alias(&identity)).unwrap(),
                Some(fixture.raw.clone())
            );
            assert_eq!(
                cache
                    .lookup_alias(&cover_render_alias(&identity, &fixture.options))
                    .unwrap(),
                Some(path)
            );
            assert_eq!(cache.stats().leased_entries, 0);
        }
    }

    #[test]
    fn legacy_processed_only_covers_keep_renderer_validation_during_promotion() {
        let fixture = LegacyCoverFixture::new(true);
        let cache = fixture.manager.cache();
        let keep_processed = cache.lease_path(&fixture.rendered.cache_path).unwrap();
        cache.clear_unpinned().unwrap();
        drop(keep_processed);
        assert!(
            cache
                .lookup_alias(&cover_source_alias(&fixture.identity))
                .unwrap()
                .is_none()
        );
        let identity = remote_cover_identity(&fixture.book, &fixture.configuration).unwrap();
        let result = prepare_cover(
            fixture.manager.clone(),
            fixture.book.clone(),
            fixture.options.clone(),
            false,
            Some(fixture.configuration.clone()),
        )
        .unwrap();
        let JobResult::Cover { path, image, .. } = result else {
            panic!("Expected the processed cached cover result");
        };
        assert_eq!(path, fixture.rendered.cache_path);
        assert_eq!(
            image.as_bytes(),
            prepared_pixels::prepare(fixture.rendered.image.clone()).as_bytes()
        );
        assert!(
            cache
                .lookup_alias(&cover_source_alias(&identity))
                .unwrap()
                .is_none()
        );
        let alias = cover_render_alias(&identity, &fixture.options);
        assert_eq!(cache.lookup_alias(&alias).unwrap(), Some(path));
        let promoted = crate::render_cache::load_alias(cache, &alias, &fixture.options)
            .unwrap()
            .unwrap();
        assert!(promoted.cache_hit);
        // A misleading legacy alias cannot bypass the actual embedded options.
        let mut changed_options = fixture.options.clone();
        changed_options.contrast = 1;
        cache
            .set_alias(
                &cover_render_alias(&fixture.identity, &changed_options),
                &fixture.rendered.cache_path,
            )
            .unwrap();
        assert!(
            prepare_cover(
                fixture.manager.clone(),
                fixture.book.clone(),
                changed_options.clone(),
                false,
                Some(fixture.configuration.clone()),
            )
            .is_err()
        );
        assert!(
            cache
                .lookup_alias(&cover_render_alias(&identity, &changed_options))
                .unwrap()
                .is_none()
        );
        assert_eq!(cache.stats().leased_entries, 0);
    }

    #[test]
    fn any_legacy_raw_source_precedes_an_older_processed_metadata_variant() {
        let fixture = LegacyCoverFixture::new(true);
        let cache = fixture.manager.cache();
        let keep_processed = cache.lease_path(&fixture.rendered.cache_path).unwrap();
        cache.clear_unpinned().unwrap();
        drop(keep_processed);
        assert!(
            cache
                .lookup_alias(&cover_source_alias(&fixture.identity))
                .unwrap()
                .is_none()
        );
        let mut other_metadata = fixture.configuration.clone();
        other_metadata
            .options
            .insert("_connection_verified".into(), json!(false));
        let other_identity =
            cover_identity_with_configuration(&fixture.book, &other_metadata).unwrap();
        let newer_raw = LegacyCoverFixture::store_raw(cache, &fixture.book, 68);
        cache
            .set_alias(&cover_source_alias(&other_identity), &newer_raw)
            .unwrap();
        let result = prepare_cover(
            fixture.manager.clone(),
            fixture.book.clone(),
            fixture.options.clone(),
            false,
            Some(fixture.configuration.clone()),
        )
        .unwrap();
        let JobResult::Cover { image, .. } = result else {
            panic!("Expected the current cached raw cover result");
        };
        assert!(image.as_bytes().iter().all(|component| *component == 68));
        assert_ne!(
            image.as_bytes(),
            prepared_pixels::prepare(fixture.rendered.image.clone()).as_bytes()
        );
        let identity = remote_cover_identity(&fixture.book, &fixture.configuration).unwrap();
        assert_eq!(
            cache.lookup_alias(&cover_source_alias(&identity)).unwrap(),
            Some(newer_raw)
        );
        assert_eq!(cache.stats().leased_entries, 0);
    }

    #[test]
    fn legacy_metadata_fallback_never_probes_another_request_configuration() {
        let fixture = LegacyCoverFixture::new(true);
        let mut configurations = Vec::new();
        let mut changed = fixture.configuration.clone();
        changed.username = "another-account".into();
        configurations.push(changed);
        let mut changed = fixture.configuration.clone();
        changed.password = "another-password".into();
        configurations.push(changed);
        let mut changed = fixture.configuration.clone();
        changed.token = "another-token".into();
        configurations.push(changed);
        let mut changed = fixture.configuration.clone();
        changed.base_url = "https://another.example.test".into();
        configurations.push(changed);
        let mut changed = fixture.configuration.clone();
        changed.kind = "kavita".into();
        configurations.push(changed);
        let mut changed = fixture.configuration.clone();
        changed.version += 1;
        configurations.push(changed);
        let mut changed = fixture.configuration.clone();
        changed.options.insert("auth_mode".into(), json!("basic"));
        configurations.push(changed);
        for configuration in configurations {
            let identity = remote_cover_identity(&fixture.book, &configuration).unwrap();
            assert!(
                !legacy_metadata_cover_identities(&fixture.book, &configuration)
                    .unwrap()
                    .contains(&fixture.identity)
            );
            assert!(
                prepare_cover(
                    fixture.manager.clone(),
                    fixture.book.clone(),
                    fixture.options.clone(),
                    false,
                    Some(configuration),
                )
                .is_err()
            );
            assert!(
                fixture
                    .manager
                    .cache()
                    .lookup_alias(&cover_source_alias(&identity))
                    .unwrap()
                    .is_none()
            );
            assert!(
                fixture
                    .manager
                    .cache()
                    .lookup_alias(&cover_render_alias(&identity, &fixture.options))
                    .unwrap()
                    .is_none()
            );
        }
        assert!(
            fixture
                .manager
                .cache()
                .lookup_alias(&cover_source_alias(&fixture.identity))
                .unwrap()
                .is_some()
        );
        assert_eq!(fixture.manager.cache().stats().leased_entries, 0);
    }

    #[test]
    fn source_credentials_and_options_isolate_persistent_cover_aliases() {
        let book = Book {
            id: "source:book".into(),
            cover: "https://example.test/cover.png".into(),
            ..Book::default()
        };
        let configuration = SourceConfig {
            id: "source".into(),
            password: "private-password".into(),
            enabled: true,
            ..SourceConfig::default()
        };
        let first = remote_cover_identity(&book, &configuration).unwrap();
        let mut changed = configuration.clone();
        changed.password = "another-password".into();
        assert_ne!(first, remote_cover_identity(&book, &changed).unwrap());
        changed = configuration.clone();
        changed.options.insert("quality".into(), json!("high"));
        assert_ne!(first, remote_cover_identity(&book, &changed).unwrap());
        let alias = cover_source_alias(&first);
        assert!(!alias.contains(&book.cover));
        assert!(!alias.contains(&configuration.password));
    }

    #[test]
    fn book_identity_and_render_geometry_cannot_share_an_alias() {
        let mut book = Book {
            id: "source:first".into(),
            cover: "https://example.test/cover.png".into(),
            ..Book::default()
        };
        let configuration = SourceConfig::default();
        let first = remote_cover_identity(&book, &configuration).unwrap();
        book.id = "source:second".into();
        assert_ne!(first, remote_cover_identity(&book, &configuration).unwrap());
        let mut options = ImageOptions::default();
        let alias = cover_render_alias(&first, &options);
        options.width += 1;
        assert_ne!(alias, cover_render_alias(&first, &options));
        options.width -= 1;
        options.dither = !options.dither;
        assert_ne!(alias, cover_render_alias(&first, &options));
    }

    #[test]
    fn cached_listing_labels_do_not_discard_offline_covers_after_restart() {
        let book = Book {
            id: "source:book".into(),
            cover: "https://example.test/cover.png".into(),
            ..Book::default()
        };
        let mut configuration = SourceConfig::default();
        let identity = remote_cover_identity(&book, &configuration).unwrap();
        configuration.options.insert(
            "_cached_listings".into(),
            json!([["popular", "Popular"], ["latest", "Latest"],]),
        );
        assert_eq!(
            identity,
            remote_cover_identity(&book, &configuration).unwrap()
        );
        configuration
            .options
            .insert("request_interval_ms".into(), json!(250));
        assert_ne!(
            identity,
            remote_cover_identity(&book, &configuration).unwrap()
        );
    }

    #[test]
    fn connection_verification_metadata_keeps_the_same_persistent_alias() {
        let book = Book {
            id: "source:book".into(),
            cover: "https://example.test/cover.png".into(),
            ..Book::default()
        };
        let mut configuration = SourceConfig {
            id: "source".into(),
            enabled: true,
            ..SourceConfig::default()
        };
        let identity = remote_cover_identity(&book, &configuration).unwrap();
        for verified in [false, true] {
            configuration
                .options
                .insert("_connection_verified".into(), json!(verified));
            assert_eq!(
                identity,
                remote_cover_identity(&book, &configuration).unwrap()
            );
        }
        configuration.token = "another-account-token".into();
        assert_ne!(
            identity,
            remote_cover_identity(&book, &configuration).unwrap()
        );
    }

    #[test]
    fn disabling_a_source_reuses_cached_covers_without_network_admission() {
        let book = Book {
            id: "source:book".into(),
            cover: "https://example.test/cover.png".into(),
            ..Book::default()
        };
        let mut configuration = SourceConfig {
            id: "source".into(),
            kind: "komga".into(),
            enabled: true,
            token: "account-token".into(),
            ..SourceConfig::default()
        };
        let identity = remote_cover_identity(&book, &configuration).unwrap();
        let source_alias = cover_source_alias(&identity);
        let options = ImageOptions::default();
        let rendered_alias = cover_render_alias(&identity, &options);
        assert!(cover_source_allows_network(&configuration));
        configuration.enabled = false;
        let disabled_identity = remote_cover_identity(&book, &configuration).unwrap();
        assert_eq!(source_alias, cover_source_alias(&disabled_identity));
        assert_eq!(
            rendered_alias,
            cover_render_alias(&disabled_identity, &options)
        );
        assert!(!cover_source_allows_network(&configuration));
        configuration.version += 1;
        assert_ne!(
            identity,
            remote_cover_identity(&book, &configuration).unwrap()
        );
        configuration.version -= 1;
        configuration.token = "another-account-token".into();
        assert_ne!(
            identity,
            remote_cover_identity(&book, &configuration).unwrap()
        );
    }

    #[test]
    fn unsupported_source_kinds_never_admit_cover_network_requests() {
        for kind in ["browser", "unsupported", "local", ""] {
            let configuration = SourceConfig {
                kind: kind.into(),
                enabled: true,
                ..SourceConfig::default()
            };
            assert!(!cover_source_allows_network(&configuration));
        }
        let mut configuration = SourceConfig {
            kind: "WASM".into(),
            enabled: true,
            ..SourceConfig::default()
        };
        assert!(cover_source_allows_network(&configuration));
        configuration.enabled = false;
        assert!(!cover_source_allows_network(&configuration));
    }

    #[test]
    fn only_persisted_local_derivatives_receive_a_cover_alias() {
        let book = Book {
            id: "local:book".into(),
            cover: "/data/local/cover.png".into(),
            ..Book::default()
        };
        let raw = Path::new(&book.cover);
        let options = ImageOptions::default();
        let mut rendered = crate::render_cache::RenderedPage {
            image: Arc::new(GrayImage::new(1, 1)),
            cache_path: raw.to_path_buf(),
            source_proof: None,
            retained_raster: None,
            scaled_height: 0,
            viewport_height: 0,
            cache_hit: false,
            memory_hit: false,
        };
        assert!(local_cover_render_alias(&book, raw, &rendered, &options).is_none());
        rendered.cache_path = PathBuf::from("/cache/processed/first.png");
        let alias = local_cover_render_alias(&book, raw, &rendered, &options).unwrap();
        assert!(alias.starts_with("cover-render-v2:"));
        assert!(!alias.contains(&book.cover));
        rendered.cache_path = PathBuf::from("/cache/processed/replaced.png");
        assert_ne!(
            alias,
            local_cover_render_alias(&book, raw, &rendered, &options).unwrap()
        );
    }

    #[test]
    fn absolute_local_and_demo_paths_never_need_a_source_request() {
        for path in [
            "/data/demo-assets/cover.png",
            "C:\\Books\\cover.png",
            "C:/Books/cover.png",
        ] {
            assert!(local_cover_path(path, false).unwrap().is_some());
        }
        assert!(
            local_cover_path("relative/cover.png", true)
                .unwrap()
                .is_some()
        );
        assert!(
            local_cover_path("https://example.test/cover.png", false)
                .unwrap()
                .is_none()
        );
        assert!(
            local_cover_path("custom-cover:asset", false)
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn a_larger_detail_cover_requires_more_pixels_than_a_grid_cover() {
        let options = ImageOptions {
            width: 612,
            height: 840,
            ..ImageOptions::default()
        };
        assert!(!cover_pixels_fit(376, 514, &options));
        assert!(cover_pixels_fit(600, 840, &options));
        assert!(cover_pixels_fit(612, 800, &options));
        assert!(!cover_pixels_fit(0, 840, &options));
    }
}
