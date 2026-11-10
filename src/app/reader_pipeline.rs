//! Bounded reader image work and cache-path publication.

use super::*;
use crate::reader::PageLayout;

pub(super) struct Prefetched {
    generation: u64,
    chapters: Vec<PrefetchedChapter>,
}

struct PrefetchedChapter {
    chapter: Chapter,
    manifest: Option<Vec<Page>>,
    paths: Vec<CachedPage>,
}

struct PrefetchFrame {
    index: usize,
    page: Page,
    options: ImageOptions,
    vertical: bool,
    offset: u32,
    zoom: f32,
    half: u8,
}

struct NextChapterPrefetch {
    chapter: Chapter,
    book: Option<Book>,
    window: crate::store::PageWindow,
    capacity: usize,
}

impl App {
    pub(super) fn request_reader_images(&mut self) -> Result<()> {
        self.request_reader_images_with_refresh(RefreshRequest::Full)
    }

    pub(super) fn request_reader_images_with_refresh(
        &mut self,
        refresh: RefreshRequest,
    ) -> Result<()> {
        self.cancel_reader_prefetch();
        self.reader_generation = self.reader_generation.wrapping_add(1);
        self.reader_dirty = true;
        self.reader_refresh = merge_refresh(self.reader_refresh, refresh);
        if let Some(id) = self.reader_inflight {
            // A burst of taps updates the desired state, never the number of
            // decoder threads. The newest frame starts after this one completes.
            self.pending.insert(id);
            self.busy = true;
            return Ok(());
        }
        self.start_reader_image_job()
    }

    pub(super) fn start_reader_image_job(&mut self) -> Result<()> {
        if self.resume_frame_pending
            || self.reader_inflight.is_some()
            || !self.reader_dirty
            || self.route != "reader"
        {
            return Ok(());
        }
        let target = self.reader_target();
        if let Err(error) = self.begin_reader_image_job() {
            if let Some(target) = target {
                self.recover_reader_failure(target, error);
            } else {
                self.invalidate_reader_work();
                self.show_reader_action_error(error);
            }
        }
        Ok(())
    }

    pub(super) fn begin_reader_image_job(&mut self) -> Result<()> {
        let reader = self.reader.as_ref().context("No chapter is open")?;
        let chapter_id = reader.current_chapter().id.clone();
        let source_id = reader.current_chapter().source_id.clone();
        let page = reader.page;
        let zoom = reader.zoom;
        let half = reader.wide_half;
        let effective = self.effective_reader_preferences();
        let vertical = is_strip(&effective);
        let offset = if vertical { reader.vertical_offset } else { 0 };
        let index = reader.visible_pages().start().saturating_sub(1) as usize;
        let window =
            self.store
                .page_window(&chapter_id, index, frame_window_limit(&effective, index))?;
        if window.total == 0 {
            bail!("The chapter contains no pages");
        }
        let spread = frame_page_count(&effective, index, window.total) == 2;
        let viewport = self.platform.runtime_logical_size(effective.landscape);
        let options = reader_image_options(&effective, viewport, index, window.total);
        let offline =
            self.options.offline || self.options.demo || self.platform.network_policy_blocked();
        let id = self.spawn_job_kind(true, JobKind::Reader, move |manager| {
            let mut pages = window.pages.into_iter();
            let request = pages
                .next()
                .context("The requested page is outside this chapter")?;
            let mut paths = Vec::with_capacity(if spread { 2 } else { 1 });
            let path = cached_page(
                &manager,
                &request,
                &source_id,
                index,
                offline,
                FetchPriority::Interactive,
                &mut paths,
            )?;
            let prepared = render_reader_page(
                manager.cache(),
                &request,
                &path,
                &options,
                &effective,
                viewport,
                vertical,
                offset,
                zoom,
                half,
                None,
            )?;
            let wide = prepared.wide;
            let wide_half = prepared.half;
            let rendered = prepared.rendered;
            let scaled_height = rendered.scaled_height;
            let viewport_height = rendered.viewport_height;
            let first_path = rendered.cache_path;
            let first = rendered.image;
            let (second, second_path) = if spread {
                let request = pages
                    .next()
                    .context("The second spread page is outside this chapter")?;
                let path = cached_page(
                    &manager,
                    &request,
                    &source_id,
                    index + 1,
                    offline,
                    FetchPriority::Interactive,
                    &mut paths,
                )?;
                let prepared = render_reader_page(
                    manager.cache(),
                    &request,
                    &path,
                    &options,
                    &effective,
                    viewport,
                    false,
                    0,
                    zoom,
                    0,
                    None,
                )?;
                let rendered = prepared.rendered;
                (Some(rendered.image), Some(rendered.cache_path))
            } else {
                (None, None)
            };
            Ok(JobResult::ReaderImage {
                chapter_id,
                page,
                offset,
                wide,
                wide_half,
                first: super::prepared_pixels::prepare(first),
                second: second.map(super::prepared_pixels::prepare),
                scaled_height,
                viewport_height,
                preferences: effective,
                first_path,
                second_path,
                paths,
            })
        })?;
        self.reader_inflight = Some(id);
        self.reader_image_jobs_started = self.reader_image_jobs_started.saturating_add(1);
        self.reader_dirty = false;
        Ok(())
    }

    pub(super) fn start_reader_prefetch(&mut self) -> Result<()> {
        if self.resume_frame_pending
            || self.resume_network_pending
            || self.prefetch_inflight.is_some()
            || !self.prefetch_dirty
            || self.route != "reader"
        {
            return Ok(());
        }
        self.prefetch_dirty = false;
        let reader = self.reader.as_ref().context("No chapter is open")?;
        let chapter = reader.current_chapter().clone();
        let successor = successor_chapter(reader);
        let effective = self.effective_reader_preferences();
        let vertical = is_strip(&effective);
        let current = reader.visible_pages().start().saturating_sub(1) as usize;
        let wide_mode = effective.split_wide && !effective.spread && !vertical;
        let current_wide_remainder = wide_mode && reader.is_wide_page() && reader.wide_half == 0;
        let zoom = prefetch_zoom(&effective, reader.zoom);
        let viewport = self.platform.runtime_logical_size(effective.landscape);
        let targets = if vertical {
            strip_prefetch_offsets(
                reader.vertical_offset,
                reader.viewport_height,
                reader.image_height,
            )
        } else {
            Vec::new()
        };
        // The snapshot includes the current allocation and at most two future
        // pages: one row for strips, three for single pages, four for spreads.
        let window = self.store.page_window(
            &chapter.id,
            current,
            prefetch_window_limit(&effective, current),
        )?;
        let total = window.total;
        let current_page = current_wide_remainder
            .then(|| window.pages.first().cloned())
            .flatten();
        let (first, pages, options) = prefetch_pages(&effective, window, viewport);
        let mut frames = Vec::with_capacity(2);
        let mut chapter_end = first.saturating_add(pages.len()) >= total;
        if vertical {
            chapter_end = false;
            // A strip turn first exhausts the current source image. Spend any
            // remaining look-ahead slot on the next source image's top window.
            let next_frame = if targets.len() < 2 {
                let next_index = current.saturating_add(1);
                let next = self.store.page_window(&chapter.id, next_index, 1)?;
                if let Some(page) = next.pages.into_iter().next() {
                    Some(PrefetchFrame {
                        index: next_index,
                        page,
                        options: reader_image_options(&effective, viewport, next_index, total),
                        vertical: true,
                        offset: 0,
                        zoom: 1.0,
                        half: 0,
                    })
                } else {
                    chapter_end = next_index >= total;
                    None
                }
            } else {
                None
            };
            frames =
                strip_prefetch_frames(current, pages.first(), options, &targets, zoom, next_frame);
        } else {
            if let Some(page) = current_page {
                frames.push(PrefetchFrame {
                    index: current,
                    page,
                    options: reader_image_options(&effective, viewport, current, total),
                    vertical: false,
                    offset: 0,
                    zoom: 1.0,
                    half: 1,
                });
            }
            let capacity = 2_usize.saturating_sub(frames.len());
            frames.extend(
                pages
                    .into_iter()
                    .take(capacity)
                    .enumerate()
                    .map(|(relative, page)| PrefetchFrame {
                        index: first + relative,
                        page,
                        options: options.clone(),
                        vertical: false,
                        offset: 0,
                        zoom,
                        half: 0,
                    }),
            );
        }
        if wide_mode {
            // A full double-width source can consume most of the decoded
            // budget. Preparing a farther source must not evict the nearest
            // half-page before the next turn gets a chance to reuse it.
            frames.truncate(1);
        }
        let offline =
            self.options.offline || self.options.demo || self.platform.network_policy_blocked();
        let remaining = if wide_mode && !frames.is_empty() {
            0
        } else {
            2_usize.saturating_sub(frames.len())
        };
        let next_chapter = if chapter_end && remaining > 0 {
            if let Some(next) = successor {
                let window = self.store.page_window(&next.id, 0, 2)?;
                if window.total > 0 {
                    Some(NextChapterPrefetch {
                        chapter: next,
                        book: None,
                        window,
                        capacity: remaining,
                    })
                } else if !offline && next.source_id != "local" {
                    self.store
                        .book(&next.book_id)?
                        .map(|book| NextChapterPrefetch {
                            chapter: next,
                            book: Some(book),
                            window,
                            capacity: remaining,
                        })
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            None
        };
        if frames.is_empty() && next_chapter.is_none() {
            return Ok(());
        }
        let generation = self.reader_generation;
        let cancellation = imaging::DecodeCancellation::new();
        let worker_cancellation = cancellation.clone();
        let id = self.spawn_job_kind(false, JobKind::Prefetch, move |manager| {
            let token = &worker_cancellation;
            // Shared raw transfers may have foreground followers. Background
            // admission applies to their validation, but per-reader cancellation
            // only surrounds fetches and the private render preparation.
            let result = imaging::with_background_decode(None, || {
                token.check()?;
                let mut chapters = Vec::with_capacity(2);
                if !frames.is_empty() {
                    chapters.push(prefetch_chapter_frames(
                        &manager, chapter, None, frames, offline, &effective, viewport, token,
                    )?);
                }
                if let Some(next) = next_chapter {
                    token.check()?;
                    let chapter_id = next.chapter.id.clone();
                    match prefetch_next_chapter(
                        &manager, next, &effective, viewport, offline, token,
                    ) {
                        Ok(next) => chapters.push(next),
                        Err(error) if !token.is_cancelled() => {
                            eprintln!("aidoku: next chapter read-ahead {chapter_id}: {error:#}");
                        }
                        Err(_) => {}
                    }
                }
                token.check()?;
                Ok(Prefetched {
                    generation,
                    chapters,
                })
            });
            if token.is_cancelled() {
                return Ok(JobResult::Prefetched(Prefetched {
                    generation,
                    chapters: Vec::new(),
                }));
            }
            result.map(JobResult::Prefetched)
        })?;
        self.prefetch_inflight = Some(id);
        self.reader_prefetch_cancel = Some(cancellation);
        Ok(())
    }

    pub(super) fn cancel_reader_prefetch(&mut self) {
        if let Some(token) = self.reader_prefetch_cancel.take() {
            token.cancel();
        }
    }

    pub(super) fn accept_reader_prefetch(&mut self, result: Prefetched) -> Result<()> {
        if result.generation != self.reader_generation || self.route != "reader" {
            return Ok(());
        }
        for batch in result.chapters {
            let Some(chapter) = self.store.chapter(&batch.chapter.id)? else {
                continue;
            };
            if !same_chapter_identity(&chapter, &batch.chapter) {
                continue;
            }
            if let Some(manifest) = batch.manifest {
                if self
                    .store
                    .publish_prefetched_manifest(&batch.chapter, &manifest)?
                {
                    self.invalidate_storage_details();
                }
            }
            self.save_cached_paths(&chapter.id, batch.paths)?;
        }
        Ok(())
    }

    pub(super) fn save_cached_paths(
        &mut self,
        chapter_id: &str,
        paths: Vec<CachedPage>,
    ) -> Result<bool> {
        let report = self.store.update_cached_paths_report(chapter_id, &paths)?;
        if report.changed {
            self.invalidate_storage_details();
        }
        if report.affects_downloaded {
            self.invalidate_download_usage();
        }
        Ok(report.current)
    }
}

fn same_chapter_identity(first: &Chapter, second: &Chapter) -> bool {
    first.id == second.id
        && first.book_id == second.book_id
        && first.source_id == second.source_id
        && first.remote_id == second.remote_id
}

fn successor_chapter(reader: &ReaderState) -> Option<Chapter> {
    let current = reader.current_chapter();
    reader
        .chapters()
        .get(reader.chapter_index().saturating_add(1))
        .filter(|next| next.book_id == current.book_id && next.source_id == current.source_id)
        .cloned()
}

fn prefetch_next_chapter(
    manager: &SourceManager,
    plan: NextChapterPrefetch,
    preferences: &Preferences,
    viewport: (u32, u32),
    offline: bool,
    cancellation: &imaging::DecodeCancellation,
) -> Result<PrefetchedChapter> {
    cancellation.check()?;
    let (window, manifest) = if plan.window.total == 0 {
        let book = plan.book.context("The next manga is missing")?;
        let pages = manager.pages(&book, &plan.chapter)?;
        cancellation.check()?;
        anyhow::ensure!(
            !pages.is_empty() && pages.len() <= 20_000,
            "The next chapter has an invalid page count"
        );
        let window = crate::store::PageWindow {
            offset: 0,
            total: pages.len(),
            pages: pages.iter().take(2).cloned().collect(),
        };
        (window, Some(pages))
    } else {
        (plan.window, None)
    };
    let frames = chapter_head_frames(preferences, window, viewport, plan.capacity);
    prefetch_chapter_frames(
        manager,
        plan.chapter,
        manifest,
        frames,
        offline,
        preferences,
        viewport,
        cancellation,
    )
}

fn chapter_head_frames(
    preferences: &Preferences,
    window: crate::store::PageWindow,
    viewport: (u32, u32),
    capacity: usize,
) -> Vec<PrefetchFrame> {
    let options = reader_image_options(preferences, viewport, 0, window.total);
    let count = frame_page_count(preferences, 0, window.total).min(capacity.min(2));
    window
        .pages
        .into_iter()
        .take(count)
        .enumerate()
        .map(|(index, page)| PrefetchFrame {
            index,
            page,
            options: options.clone(),
            vertical: is_strip(preferences),
            offset: 0,
            zoom: 1.0,
            half: 0,
        })
        .collect()
}

fn strip_prefetch_frames(
    current: usize,
    page: Option<&Page>,
    options: ImageOptions,
    targets: &[u32],
    zoom: f32,
    next: Option<PrefetchFrame>,
) -> Vec<PrefetchFrame> {
    let mut frames = Vec::with_capacity(2);
    if let Some(page) = page {
        frames.extend(targets.iter().take(2).map(|offset| PrefetchFrame {
            index: current,
            page: page.clone(),
            options: options.clone(),
            vertical: true,
            offset: *offset,
            zoom,
            half: 0,
        }));
    }
    if frames.len() < 2 {
        frames.extend(next);
    }
    frames
}

fn prefetch_chapter_frames(
    manager: &SourceManager,
    chapter: Chapter,
    manifest: Option<Vec<Page>>,
    frames: Vec<PrefetchFrame>,
    offline: bool,
    preferences: &Preferences,
    viewport: (u32, u32),
    cancellation: &imaging::DecodeCancellation,
) -> Result<PrefetchedChapter> {
    let mut paths = Vec::with_capacity(frames.len());
    let mut fetched = HashMap::<usize, PathBuf>::new();
    let mut failed = HashSet::new();
    for frame in frames {
        cancellation.check()?;
        if failed.contains(&frame.index) {
            continue;
        }
        let path = if let Some(path) = fetched.get(&frame.index) {
            path.clone()
        } else {
            match cached_page(
                manager,
                &frame.page,
                &chapter.source_id,
                frame.index,
                offline,
                FetchPriority::Prefetch,
                &mut paths,
            ) {
                Ok(path) => {
                    fetched.insert(frame.index, path.clone());
                    path
                }
                Err(error) => {
                    cancellation.check()?;
                    failed.insert(frame.index);
                    eprintln!("aidoku: read-ahead page {}: {error:#}", frame.index);
                    continue;
                }
            }
        };
        cancellation.check()?;
        match render_reader_page(
            manager.cache(),
            &frame.page,
            &path,
            &frame.options,
            preferences,
            viewport,
            frame.vertical,
            frame.offset,
            frame.zoom,
            frame.half,
            Some(cancellation),
        ) {
            Ok(prepared) => {
                cancellation.check()?;
                let _ = super::prepared_pixels::prepare(prepared.rendered.image);
            }
            Err(error) => {
                cancellation.check()?;
                eprintln!("aidoku: read-ahead render {}: {error:#}", frame.index);
            }
        }
    }
    Ok(PrefetchedChapter {
        chapter,
        manifest,
        paths,
    })
}

fn prepared_source_is_eligible(preferences: &Preferences, vertical: bool, zoom: f32) -> bool {
    if zoom != 1.0 || !(preferences.resize_downloads || preferences.grayscale_downloads) {
        return false;
    }
    // Resampling before a crop, strip window, or half-page selection can change
    // source geometry or discard detail needed by a later operation. Even a
    // grayscale-only copy can quantize transparent edge pixels differently,
    // changing a later content-boundary decision. Keep those paths on originals.
    !vertical && !preferences.crop_borders && !preferences.split_wide
}

fn render_reader_page(
    cache: &crate::cache::CacheManager,
    page: &Page,
    original: &Path,
    options: &ImageOptions,
    preferences: &Preferences,
    viewport: (u32, u32),
    vertical: bool,
    offset: u32,
    zoom: f32,
    half: u8,
    cancellation: Option<&imaging::DecodeCancellation>,
) -> Result<super::wide_pages::WidePage> {
    let render = |source: &Path| {
        if let Some(token) = cancellation {
            super::wide_pages::prefetch(
                cache,
                page,
                source,
                options,
                vertical,
                offset,
                zoom,
                preferences,
                half,
                token,
            )
        } else {
            super::wide_pages::render(
                cache,
                page,
                source,
                options,
                vertical,
                offset,
                zoom,
                preferences,
                half,
            )
        }
    };
    if !prepared_source_is_eligible(preferences, vertical, zoom) {
        return render(original);
    }
    if let Some(token) = cancellation {
        token.check()?;
    }
    // Retain the canonical original throughout lookup and rendering so a failed
    // accelerator always has the same valid, in-use source available as fallback.
    let _original_lease = cache.lease_path(original)?;
    if let Ok(Some(prepared)) =
        crate::downloads::prepared_page_path(cache, page, original, preferences, viewport)
    {
        if let Ok(_prepared_lease) = cache.lease_path(&prepared) {
            if let Ok(rendered) = render(&prepared) {
                return Ok(rendered);
            }
            if let Some(token) = cancellation {
                token.check()?;
            }
        }
    }
    // This path only looks up download-time copies. It never creates or repairs
    // one on a page turn, and never replaces Page.local_path with a derivative.
    render(original)
}

fn cached_page(
    manager: &SourceManager,
    request: &Page,
    source_id: &str,
    index: usize,
    offline: bool,
    priority: FetchPriority,
    paths: &mut Vec<CachedPage>,
) -> Result<PathBuf> {
    let identity = cached_page_identity(request, index);
    if let Some(path) = request.local_path.as_ref().filter(|path| path.is_file()) {
        manager.cache().validate_path(path)?;
        paths.push(CachedPage {
            index,
            identity,
            path: path.clone(),
        });
        return Ok(path.clone());
    }
    let path = if let Some(path) = manager.cache().lookup_raw(request)? {
        path
    } else if request.url.is_empty() && request.local_path.is_some() {
        return Err(std::io::Error::from(std::io::ErrorKind::NotFound))
            .context("The local comic page is missing");
    } else if offline {
        bail!("{}", tr("chapter_unavailable"));
    } else {
        manager.fetch_page_for_source(source_id, request, priority, None)?
    };
    paths.push(CachedPage {
        index,
        identity,
        path: path.clone(),
    });
    Ok(path)
}

fn is_strip(preferences: &Preferences) -> bool {
    preferences.reading_direction == "vertical"
}

fn prefetch_zoom(preferences: &Preferences, current_zoom: f32) -> f32 {
    if is_strip(preferences) {
        current_zoom
    } else {
        // Moving to a different source page resets the reader's zoom.
        1.0
    }
}

fn frame_window_limit(preferences: &Preferences, first_index: usize) -> usize {
    PageLayout::from_preferences(preferences).window_limit(first_index)
}

fn prefetch_window_limit(preferences: &Preferences, first_index: usize) -> usize {
    if is_strip(preferences) {
        1
    } else {
        frame_window_limit(preferences, first_index) + 2
    }
}

fn frame_page_count(preferences: &Preferences, index: usize, total: usize) -> usize {
    PageLayout::from_preferences(preferences).page_count(index, total)
}

fn reader_image_options(
    preferences: &Preferences,
    viewport: (u32, u32),
    first_index: usize,
    total: usize,
) -> ImageOptions {
    let (width, height) = viewport;
    ImageOptions {
        width: if frame_page_count(preferences, first_index, total) == 2 {
            width
        } else {
            width.saturating_mul(2)
        },
        height: height.saturating_mul(2),
        crop_borders: preferences.crop_borders,
        dither: preferences.dither,
        contrast: preferences.contrast,
    }
}

fn prefetch_pages(
    preferences: &Preferences,
    window: crate::store::PageWindow,
    viewport: (u32, u32),
) -> (usize, Vec<Page>, ImageOptions) {
    let vertical = is_strip(preferences);
    let skip = if vertical {
        0
    } else {
        frame_page_count(preferences, window.offset, window.total)
    };
    let first = window.offset.saturating_add(skip);
    // Both pages of the next spread share its first index. A final unpaired
    // source page uses the same full width as its later foreground request,
    // including the parity change after an isolated cover.
    let options = reader_image_options(preferences, viewport, first, window.total);
    let pages = window
        .pages
        .into_iter()
        .skip(skip)
        .take(if vertical { 1 } else { 2 })
        .collect();
    (first, pages, options)
}

fn strip_prefetch_offsets(offset: u32, viewport_height: u32, image_height: u32) -> Vec<u32> {
    let viewport_height = viewport_height.max(1);
    let step = ((f64::from(viewport_height) * 0.85).round() as u32).max(1);
    let maximum = image_height.saturating_sub(viewport_height);
    let mut targets = Vec::with_capacity(2);
    let mut current = offset;
    for _ in 0..2 {
        let target = current.saturating_add(step).min(maximum);
        if target <= current {
            break;
        }
        targets.push(target);
        current = target;
    }
    targets
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::reader::ReaderAction;
    use image::Pixel;

    struct OfflineReaderFixture {
        directory: tempfile::TempDir,
        cache: crate::cache::CacheManager,
        store: Store,
        page: Page,
        original: PathBuf,
        book: Book,
        chapter: Chapter,
        preferences: Preferences,
        viewport: (u32, u32),
    }

    impl OfflineReaderFixture {
        fn new() -> Self {
            let artwork = image::RgbImage::from_fn(96, 48, |x, y| {
                image::Rgb([((x * 7) % 256) as u8, ((y * 9) % 256) as u8, 41])
            });
            Self::with_artwork(image::DynamicImage::ImageRgb8(artwork))
        }

        fn with_artwork(artwork: image::DynamicImage) -> Self {
            let directory = tempfile::tempdir().unwrap();
            let cache = crate::cache::CacheManager::open(directory.path()).unwrap();
            let mut page = Page {
                url: "https://example.test/prepared-reader.png".into(),
                ..Page::default()
            };
            let write = cache.begin_raw(&page, None, 65_536).unwrap();
            artwork
                .save_with_format(write.temp_path(), image::ImageFormat::Png)
                .unwrap();
            cache.validate_path(write.temp_path()).unwrap();
            let original = write.commit().unwrap();
            page.local_path = Some(original.clone());
            let store = Store::open(directory.path().join("library.sqlite3")).unwrap();
            let book = Book {
                id: "reader-book".into(),
                source_id: "reader-fixture".into(),
                remote_id: "reader-book".into(),
                title: "Offline Reader".into(),
                in_library: true,
                ..Book::default()
            };
            let chapter = Chapter {
                id: "reader-chapter".into(),
                book_id: book.id.clone(),
                source_id: book.source_id.clone(),
                remote_id: "reader-chapter".into(),
                number: 1.0,
                page_count: 1,
                ..Chapter::default()
            };
            store.upsert_book(&book).unwrap();
            store.save_chapters(&book.id, &[chapter.clone()]).unwrap();
            store.save_pages(&chapter.id, &[page.clone()]).unwrap();
            let preferences = Preferences {
                resize_downloads: true,
                grayscale_downloads: true,
                crop_borders: false,
                dither: false,
                spread: false,
                ..Preferences::default()
            };
            Self {
                directory,
                cache,
                store,
                page,
                original,
                book,
                chapter,
                preferences,
                viewport: (8, 12),
            }
        }

        fn prepare_download(&self) -> PathBuf {
            self.store.save_preferences(&self.preferences).unwrap();
            let worker = crate::downloads::DownloadWorker::start_configured(
                self.directory.path().to_path_buf(),
                self.directory.path().join("library.sqlite3"),
                crate::downloads::DownloadOptions {
                    offline: false,
                    ..crate::downloads::DownloadOptions::default()
                },
            )
            .unwrap();
            worker
                .set_processing_viewport(self.viewport.0, self.viewport.1)
                .unwrap();
            worker.enqueue(&self.book, &self.chapter).unwrap();
            let deadline = Instant::now() + Duration::from_secs(10);
            loop {
                let event = worker
                    .events
                    .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                    .expect("The cached offline download must complete without HTTP");
                match event {
                    DownloadEvent::Completed(job) if job.chapter_id == self.chapter.id => break,
                    DownloadEvent::Failed(job) => {
                        panic!("The offline fixture failed: {}", job.error)
                    }
                    DownloadEvent::Error(error) => panic!("The offline worker failed: {error}"),
                    _ => {}
                }
            }
            worker.shutdown();
            assert!(worker.wait_stopped(Duration::from_secs(5)));
            crate::downloads::prepared_page_path(
                &self.cache,
                &self.page,
                &self.original,
                &self.preferences,
                self.viewport,
            )
            .unwrap()
            .expect("The actual download worker must prepare the optional copy")
        }

        fn render(
            &self,
            preferences: &Preferences,
            viewport: (u32, u32),
            vertical: bool,
            zoom: f32,
            cancellation: Option<&imaging::DecodeCancellation>,
        ) -> super::super::wide_pages::WidePage {
            let options = reader_image_options(preferences, viewport, 0, 1);
            render_reader_page(
                &self.cache,
                &self.page,
                &self.original,
                &options,
                preferences,
                viewport,
                vertical,
                0,
                zoom,
                0,
                cancellation,
            )
            .unwrap()
        }
    }

    fn preferences(direction: &str, spread: bool) -> Preferences {
        Preferences {
            reading_direction: direction.into(),
            spread,
            ..Preferences::default()
        }
    }

    fn manifest_window(
        preferences: &Preferences,
        offset: usize,
        total: usize,
    ) -> crate::store::PageWindow {
        crate::store::PageWindow {
            offset,
            total,
            pages: (offset..total)
                .take(prefetch_window_limit(preferences, offset))
                .map(|index| Page {
                    url: format!("page-{index}"),
                    ..Page::default()
                })
                .collect(),
        }
    }

    #[test]
    fn all_directions_keep_manifest_windows_bounded() {
        for (direction, spread, foreground, prefetch) in [
            ("rtl", true, 2, 4),
            ("ltr", true, 2, 4),
            ("rtl", false, 1, 3),
            ("vertical-pages", true, 1, 3),
            ("vertical", true, 1, 1),
        ] {
            let preferences = preferences(direction, spread);
            assert_eq!(frame_window_limit(&preferences, 10_000), foreground);
            assert_eq!(prefetch_window_limit(&preferences, 10_000), prefetch);
            let (first, pages, _) = prefetch_pages(
                &preferences,
                manifest_window(&preferences, 10_000, 1_000_000),
                (618, 824),
            );
            assert_eq!(
                first,
                10_000
                    + if direction == "vertical" {
                        0
                    } else {
                        foreground
                    }
            );
            assert_eq!(pages.len(), if direction == "vertical" { 1 } else { 2 });
        }
    }

    #[test]
    fn future_spread_keeps_its_even_final_page_at_half_width() {
        let preferences = preferences("rtl", true);
        let (first, pages, options) = prefetch_pages(
            &preferences,
            manifest_window(&preferences, 4, 8),
            (1_240, 930),
        );
        assert_eq!(first, 6);
        assert_eq!(
            pages
                .iter()
                .map(|page| page.url.as_str())
                .collect::<Vec<_>>(),
            ["page-6", "page-7"]
        );
        assert_eq!(options.width, 1_240);
        assert_eq!(
            options,
            reader_image_options(&preferences, (1_240, 930), first, 8)
        );
        // A direct seek to the final page is a distinct, full-width frame.
        assert_eq!(
            reader_image_options(&preferences, (1_240, 930), 7, 8).width,
            2_480
        );
    }

    #[test]
    fn odd_final_page_prefetch_matches_full_width_foreground() {
        let preferences = preferences("ltr", true);
        let (first, pages, options) = prefetch_pages(
            &preferences,
            manifest_window(&preferences, 4, 7),
            (1_240, 930),
        );
        assert_eq!(first, 6);
        assert_eq!(pages.len(), 1);
        assert_eq!(pages[0].url, "page-6");
        assert_eq!(options.width, 2_480);
        assert_eq!(
            options,
            reader_image_options(&preferences, (1_240, 930), first, 7)
        );
        let (_, pages, _) = prefetch_pages(
            &preferences,
            manifest_window(&preferences, first, 7),
            (1_240, 930),
        );
        assert!(pages.is_empty());
    }

    #[test]
    fn vertical_pages_use_full_page_geometry_and_future_pages() {
        let preferences = preferences("vertical-pages", true);
        let (first, pages, options) = prefetch_pages(
            &preferences,
            manifest_window(&preferences, 2, 8),
            (930, 1_240),
        );
        assert!(!is_strip(&preferences));
        assert_eq!(first, 3);
        assert_eq!(pages[0].url, "page-3");
        assert_eq!(pages[1].url, "page-4");
        assert_eq!((options.width, options.height), (1_860, 2_480));
    }

    fn render_identity(page: &Page, options: &ImageOptions, zoom: f32) -> crate::cache::RenderKey {
        crate::cache::RenderKey {
            source_key: page.url.clone(),
            width: options.width,
            height: options.height,
            crop_borders: options.crop_borders,
            dither: options.dither,
            contrast: options.contrast,
            vertical: false,
            offset: 0,
            zoom_milli: (zoom * 1_000.0).round() as u32,
        }
    }

    #[test]
    fn isolated_cover_uses_a_full_width_frame_and_half_width_following_pair() {
        let preferences = Preferences {
            cover_alone: true,
            ..preferences("rtl", true)
        };
        let viewport = (1_240, 930);
        assert_eq!(frame_window_limit(&preferences, 0), 1);
        assert_eq!(prefetch_window_limit(&preferences, 0), 3);
        assert_eq!(frame_page_count(&preferences, 0, 6), 1);
        let cover = reader_image_options(&preferences, viewport, 0, 6);
        assert_eq!((cover.width, cover.height), (2_480, 1_860));
        let (first, pages, pair) =
            prefetch_pages(&preferences, manifest_window(&preferences, 0, 6), viewport);
        assert_eq!(first, 1);
        assert_eq!(
            pages
                .iter()
                .map(|page| page.url.as_str())
                .collect::<Vec<_>>(),
            ["page-1", "page-2"]
        );
        assert_eq!(pair.width, 1_240);
        assert_eq!(frame_window_limit(&preferences, first), 2);
        assert_eq!(prefetch_window_limit(&preferences, first), 4);
        assert_ne!(
            render_identity(&pages[0], &cover, 1.0),
            render_identity(&pages[0], &pair, 1.0)
        );
    }

    #[test]
    fn cover_prefetch_keys_match_future_foreground_frames_at_both_parities() {
        for total in 1..=8 {
            for direction in ["rtl", "ltr"] {
                for landscape in [false, true] {
                    let preferences = Preferences {
                        cover_alone: true,
                        landscape,
                        contrast: 1,
                        ..preferences(direction, true)
                    };
                    let viewport = if landscape {
                        (1_240, 930)
                    } else {
                        (930, 1_240)
                    };
                    let mut reader = ReaderState::new(
                        vec![Chapter {
                            id: "chapter".into(),
                            page_count: total,
                            ..Chapter::default()
                        }],
                        "chapter",
                        1,
                        &preferences,
                    )
                    .unwrap();
                    loop {
                        reader.set_zoom(2.0);
                        let current = reader.visible_pages().start().saturating_sub(1) as usize;
                        let future_zoom = prefetch_zoom(&preferences, reader.zoom);
                        assert!(frame_window_limit(&preferences, current) <= 2);
                        assert!(prefetch_window_limit(&preferences, current) <= 4);
                        let (first, pages, prefetched) = prefetch_pages(
                            &preferences,
                            manifest_window(&preferences, current, total as usize),
                            viewport,
                        );
                        assert!(pages.len() <= 2);
                        if reader.next_page().action == ReaderAction::None {
                            assert!(pages.is_empty());
                            break;
                        }
                        let next = reader.visible_pages().start().saturating_sub(1) as usize;
                        assert_eq!(first, next);
                        assert_eq!(pages.len(), reader.visible_pages().count());
                        let foreground =
                            reader_image_options(&preferences, viewport, next, total as usize);
                        for (relative, page) in pages.iter().enumerate() {
                            assert_eq!(page.url, format!("page-{}", next + relative));
                            assert_eq!(
                                render_identity(page, &prefetched, future_zoom),
                                render_identity(page, &foreground, reader.zoom)
                            );
                        }
                    }
                    assert_eq!(reader.total, total);
                    assert_eq!(*reader.visible_pages().end(), total);
                }
            }
        }
    }

    #[test]
    fn cover_option_does_not_change_single_or_vertical_frame_and_prefetch_geometry() {
        for (direction, spread) in [("rtl", false), ("vertical-pages", true), ("vertical", true)] {
            let preferences = Preferences {
                cover_alone: true,
                ..preferences(direction, spread)
            };
            assert_eq!(frame_window_limit(&preferences, 0), 1);
            assert_eq!(frame_window_limit(&preferences, 1), 1);
            for index in 0..5 {
                assert_eq!(
                    reader_image_options(&preferences, (930, 1_240), index, 5).width,
                    1_860
                );
            }
            let (first, pages, _) = prefetch_pages(
                &preferences,
                manifest_window(&preferences, 0, 5),
                (930, 1_240),
            );
            assert_eq!(first, if direction == "vertical" { 0 } else { 1 });
            assert_eq!(pages.len(), if direction == "vertical" { 1 } else { 2 });
        }
    }

    #[test]
    fn retained_source_page_uses_its_visible_cover_frame_for_all_windows() {
        let preferences = Preferences {
            cover_alone: true,
            ..preferences("rtl", true)
        };
        let reader = ReaderState::new(
            vec![Chapter {
                id: "chapter".into(),
                page_count: 7,
                ..Chapter::default()
            }],
            "chapter",
            3,
            &preferences,
        )
        .unwrap();
        assert_eq!(reader.page, 3);
        assert_eq!(reader.visible_pages(), 2..=3);
        let current = reader.visible_pages().start().saturating_sub(1) as usize;
        let (first, pages, options) = prefetch_pages(
            &preferences,
            manifest_window(&preferences, current, 7),
            (1_240, 930),
        );
        assert_eq!(first, 3);
        assert_eq!(
            pages
                .iter()
                .map(|page| page.url.as_str())
                .collect::<Vec<_>>(),
            ["page-3", "page-4"]
        );
        assert_eq!(options.width, 1_240);
    }

    #[test]
    fn prefetch_zoom_matches_page_resets_and_retained_strip_sections() {
        for direction in ["rtl", "ltr", "vertical-pages", "vertical"] {
            let preferences = preferences(direction, false);
            let mut reader = ReaderState::new(
                vec![Chapter {
                    id: "chapter".into(),
                    page_count: 2,
                    ..Chapter::default()
                }],
                "chapter",
                1,
                &preferences,
            )
            .unwrap();
            reader.set_vertical_dimensions(2_000, 800);
            reader.set_zoom(2.0);
            let future_zoom = prefetch_zoom(&preferences, reader.zoom);
            reader.next_page();
            assert_eq!(future_zoom, reader.zoom);
            assert_eq!(reader.page, if direction == "vertical" { 1 } else { 2 });
        }
    }

    #[test]
    fn strip_prefetch_offsets_match_reader_rounding_and_bottom_clamping() {
        for (viewport, image_height, offset) in [
            (1_648, 10_000, 0),
            (1_648, 5_000, 2_500),
            (1_648, 5_000, 3_352),
            (1, 3, 0),
        ] {
            let preferences = preferences("vertical", true);
            let mut reader = ReaderState::new(
                vec![Chapter {
                    id: "chapter".into(),
                    page_count: 1,
                    ..Chapter::default()
                }],
                "chapter",
                1,
                &preferences,
            )
            .unwrap();
            reader.set_vertical_dimensions(image_height, viewport);
            reader.vertical_offset = offset;
            let mut expected = Vec::new();
            for _ in 0..2 {
                let previous = reader.vertical_offset;
                reader.next_page();
                if reader.vertical_offset <= previous {
                    break;
                }
                expected.push(reader.vertical_offset);
            }
            assert_eq!(
                strip_prefetch_offsets(offset, viewport, image_height),
                expected
            );
        }
        assert_eq!(strip_prefetch_offsets(0, 1_648, 10_000), [1_401, 2_802]);
        assert_eq!(strip_prefetch_offsets(2_500, 1_648, 5_000), [3_352]);
    }

    #[test]
    fn next_chapter_head_keeps_foreground_geometry_and_the_global_budget() {
        let viewport = (1_240, 930);
        for direction in ["rtl", "ltr", "vertical-pages", "vertical"] {
            for spread in [false, true] {
                for cover_alone in [false, true] {
                    for total in 1..=4 {
                        for capacity in 0..=2 {
                            let preferences = Preferences {
                                cover_alone,
                                ..preferences(direction, spread)
                            };
                            let window = crate::store::PageWindow {
                                total,
                                offset: 0,
                                pages: (0..total)
                                    .take(2)
                                    .map(|index| Page {
                                        url: format!("next-{index}"),
                                        ..Page::default()
                                    })
                                    .collect(),
                            };
                            let frames =
                                chapter_head_frames(&preferences, window, viewport, capacity);
                            assert_eq!(
                                frames.len(),
                                frame_page_count(&preferences, 0, total).min(capacity)
                            );
                            for (index, frame) in frames.iter().enumerate() {
                                assert_eq!(frame.index, index);
                                assert_eq!(frame.page.url, format!("next-{index}"));
                                assert_eq!(frame.offset, 0);
                                assert_eq!(frame.zoom, 1.0);
                                assert_eq!(frame.vertical, direction == "vertical");
                                assert_eq!(
                                    frame.options,
                                    reader_image_options(&preferences, viewport, 0, total)
                                );
                            }
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn strip_read_ahead_crosses_source_images_at_the_actual_turn_boundary() {
        let preferences = preferences("vertical", false);
        let viewport = (618, 824);
        let current_page = Page {
            url: "current-strip".into(),
            ..Page::default()
        };
        for offset in [0, 2_500, 3_352] {
            let mut reader = ReaderState::new(
                vec![Chapter {
                    id: "chapter".into(),
                    page_count: 2,
                    ..Chapter::default()
                }],
                "chapter",
                1,
                &preferences,
            )
            .unwrap();
            reader.set_vertical_dimensions(5_000, 1_648);
            reader.vertical_offset = offset;
            reader.set_zoom(2.0);
            let targets = strip_prefetch_offsets(offset, 1_648, 5_000);
            let frames = strip_prefetch_frames(
                0,
                Some(&current_page),
                reader_image_options(&preferences, viewport, 0, 2),
                &targets,
                reader.zoom,
                Some(PrefetchFrame {
                    index: 1,
                    page: Page {
                        url: "next-strip".into(),
                        ..Page::default()
                    },
                    options: reader_image_options(&preferences, viewport, 1, 2),
                    vertical: true,
                    offset: 0,
                    zoom: 1.0,
                    half: 0,
                }),
            );
            assert!(frames.len() <= 2);
            assert_eq!(frames.len(), if offset == 3_352 { 1 } else { 2 });
            for frame in frames {
                assert_eq!(reader.next_page().action, ReaderAction::PageChanged);
                assert_eq!(frame.index, reader.page as usize - 1);
                assert_eq!(frame.offset, reader.vertical_offset);
                assert_eq!(frame.zoom, reader.zoom);
                assert_eq!(
                    frame.options,
                    reader_image_options(&preferences, viewport, frame.index, 2)
                );
            }
        }
    }

    #[test]
    fn read_ahead_follows_the_real_ordered_chapter_successor() {
        let preferences = preferences("rtl", false);
        let chapters = [
            ("last", 8.0),
            ("earlier", 1.0),
            ("middle-z", 4.0),
            ("middle-a", 4.0),
        ]
        .into_iter()
        .map(|(id, number)| Chapter {
            id: id.into(),
            book_id: "book".into(),
            source_id: "source".into(),
            remote_id: id.into(),
            number,
            page_count: 1,
            ..Chapter::default()
        })
        .collect();
        let mut reader = ReaderState::new(chapters, "earlier", 1, &preferences).unwrap();
        for expected in ["middle-a", "middle-z", "last"] {
            assert_eq!(successor_chapter(&reader).unwrap().id, expected);
            assert_eq!(reader.next_chapter().action, ReaderAction::ChapterChanged);
            assert_eq!(reader.current_chapter().id, expected);
        }
        assert!(successor_chapter(&reader).is_none());
    }

    #[test]
    fn changed_chapter_state_is_allowed_but_reused_identities_are_not() {
        let original = Chapter {
            id: "chapter".into(),
            book_id: "book".into(),
            source_id: "source".into(),
            remote_id: "remote".into(),
            ..Chapter::default()
        };
        let updated = Chapter {
            read: true,
            downloaded: true,
            page_count: 120,
            title: "A refreshed title".into(),
            ..original.clone()
        };
        assert!(same_chapter_identity(&original, &updated));
        for changed in [
            Chapter {
                book_id: "another-book".into(),
                ..original.clone()
            },
            Chapter {
                source_id: "another-source".into(),
                ..original.clone()
            },
            Chapter {
                remote_id: "another-remote".into(),
                ..original.clone()
            },
        ] {
            assert!(!same_chapter_identity(&original, &changed));
        }
    }

    #[test]
    fn prepared_reader_lookup_never_generates_a_copy_and_invalid_copies_fall_back() {
        let fixture = OfflineReaderFixture::new();
        let original_bytes = fs::read(&fixture.original).unwrap();
        assert!(
            crate::downloads::prepared_page_path(
                &fixture.cache,
                &fixture.page,
                &fixture.original,
                &fixture.preferences,
                fixture.viewport,
            )
            .unwrap()
            .is_none()
        );
        let original = fixture.render(&fixture.preferences, fixture.viewport, false, 1.0, None);
        assert!(
            crate::downloads::prepared_page_path(
                &fixture.cache,
                &fixture.page,
                &fixture.original,
                &fixture.preferences,
                fixture.viewport,
            )
            .unwrap()
            .is_none()
        );
        let prepared = fixture.prepare_download();
        assert_ne!(prepared, fixture.original);
        fs::write(&prepared, b"An invalid optional accelerator").unwrap();
        let fallback = fixture.render(&fixture.preferences, fixture.viewport, false, 1.0, None);
        assert_eq!(original.rendered.cache_path, fallback.rendered.cache_path);
        assert_eq!(original.rendered.image, fallback.rendered.image);
        assert_eq!(fs::read(&fixture.original).unwrap(), original_bytes);
        assert_eq!(
            fixture.store.pages(&fixture.chapter.id).unwrap()[0].local_path,
            Some(fixture.original.clone())
        );
        assert_eq!(
            fixture.cache.lookup_raw(&fixture.page).unwrap(),
            Some(fixture.original)
        );
    }

    #[test]
    fn prepared_reader_prefetch_and_foreground_share_pixels_and_native_geometry() {
        let fixture = OfflineReaderFixture::new();
        let original_bytes = fs::read(&fixture.original).unwrap();
        let prepared = fixture.prepare_download();
        let cancellation = imaging::DecodeCancellation::new();
        let prefetched = fixture.render(
            &fixture.preferences,
            fixture.viewport,
            false,
            1.0,
            Some(&cancellation),
        );
        // Rotation reuses the same download copy, while each frame retains its
        // actual foreground target geometry and its normal render-cache key.
        let foreground = fixture.render(&fixture.preferences, fixture.viewport, false, 1.0, None);
        let options = reader_image_options(&fixture.preferences, fixture.viewport, 0, 1);
        let expected = crate::render_cache::render(
            &fixture.cache,
            &fixture.page,
            &prepared,
            &options,
            false,
            0,
            1.0,
        )
        .unwrap();
        assert!(Arc::ptr_eq(
            &prefetched.rendered.image,
            &foreground.rendered.image
        ));
        assert!(Arc::ptr_eq(&expected.image, &foreground.rendered.image));
        assert_eq!(foreground.rendered.image.dimensions(), (16, 8));
        assert_eq!(foreground.rendered.cache_path, expected.cache_path);
        let rotated = fixture.render(&fixture.preferences, (12, 8), false, 1.0, None);
        assert_eq!(rotated.rendered.image.dimensions(), (24, 12));
        assert!(!prepared_source_is_eligible(
            &fixture.preferences,
            true,
            1.0
        ));
        assert!(!prepared_source_is_eligible(
            &fixture.preferences,
            false,
            2.0
        ));
        assert!(!prepared_source_is_eligible(
            &Preferences {
                crop_borders: true,
                ..fixture.preferences.clone()
            },
            false,
            1.0,
        ));
        assert!(!prepared_source_is_eligible(
            &Preferences {
                split_wide: true,
                ..fixture.preferences.clone()
            },
            false,
            1.0,
        ));
        assert!(!prepared_source_is_eligible(
            &Preferences::default(),
            false,
            1.0
        ));
        assert_eq!(fs::read(&fixture.original).unwrap(), original_bytes);
        assert_eq!(
            fixture.store.pages(&fixture.chapter.id).unwrap()[0].local_path,
            Some(fixture.original)
        );
    }

    #[test]
    fn transparent_gray_copies_cannot_change_reader_crop_or_strip_geometry() {
        let artwork = image::RgbaImage::from_fn(16, 24, |x, y| {
            if (4..12).contains(&x) && (6..18).contains(&y) {
                image::Rgba([0, 0, 0, 255])
            } else if x == 0 && y == 8 {
                // Reader luma is 39, composited to 247. The download copy
                // composites RGB to [252,245,245], then rounds luma to 246.
                image::Rgba([185, 0, 0, 10])
            } else {
                image::Rgba([0, 0, 0, 0])
            }
        });
        let mut fixture =
            OfflineReaderFixture::with_artwork(image::DynamicImage::ImageRgba8(artwork));
        fixture.preferences.resize_downloads = false;
        fixture.preferences.crop_borders = true;
        let prepared = fixture.prepare_download();
        let source_pixels = image::ImageReader::open(&fixture.original)
            .unwrap()
            .with_guessed_format()
            .unwrap()
            .decode()
            .unwrap()
            .into_rgba8();
        assert_eq!(source_pixels.get_pixel(0, 8).0, [185, 0, 0, 10]);
        assert_eq!(source_pixels.get_pixel(0, 8).to_luma()[0], 39);
        let prepared_pixels = image::ImageReader::open(&prepared)
            .unwrap()
            .with_guessed_format()
            .unwrap()
            .decode()
            .unwrap()
            .into_luma8();
        assert_eq!(prepared_pixels.get_pixel(0, 8)[0], 246);
        let options = reader_image_options(&fixture.preferences, fixture.viewport, 0, 1);
        let original = crate::render_cache::render(
            &fixture.cache,
            &fixture.page,
            &fixture.original,
            &options,
            false,
            0,
            1.0,
        )
        .unwrap();
        let different_geometry = crate::render_cache::render(
            &fixture.cache,
            &fixture.page,
            &prepared,
            &options,
            false,
            0,
            1.0,
        )
        .unwrap();
        assert_ne!(
            original.image.dimensions(),
            different_geometry.image.dimensions()
        );
        for vertical in [false, true] {
            let expected = crate::render_cache::render(
                &fixture.cache,
                &fixture.page,
                &fixture.original,
                &options,
                vertical,
                0,
                1.0,
            )
            .unwrap();
            let actual =
                fixture.render(&fixture.preferences, fixture.viewport, vertical, 1.0, None);
            assert_eq!(actual.rendered.cache_path, expected.cache_path);
            assert_eq!(actual.rendered.image, expected.image);
            assert_eq!(actual.rendered.scaled_height, expected.scaled_height);
            assert_eq!(actual.rendered.viewport_height, expected.viewport_height);
        }
    }
}
