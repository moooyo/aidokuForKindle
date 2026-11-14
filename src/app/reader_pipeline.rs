//! Bounded reader image work and cache-path publication.

use super::*;
use crate::reader::PageLayout;

pub(super) struct Prefetched {
    generation: u64,
    chapters: Vec<PrefetchedChapter>,
    next_frame: Option<PreparedNextFrame>,
}

#[derive(Clone, PartialEq)]
pub(super) struct NextFrameKey {
    origin: ReaderTarget,
    target: ReaderTarget,
    generation: u64,
    lifecycle_generation: u64,
    source_revision: u64,
    preferences: Preferences,
    hardware_dither: bool,
    viewport: (u32, u32),
}

pub(super) enum NextFrameSlot {
    Empty,
    Preparing(NextFrameKey),
    Ready(Box<PreparedNextFrame>),
}

impl Default for NextFrameSlot {
    fn default() -> Self {
        Self::Empty
    }
}

impl NextFrameSlot {
    pub(super) fn label(&self) -> &'static str {
        match self {
            Self::Empty => "empty",
            Self::Preparing(_) => "preparing",
            Self::Ready(_) => "ready",
        }
    }

    pub(super) fn retained_bytes(&self) -> usize {
        match self {
            Self::Ready(frame) => frame.retained_bytes(),
            _ => 0,
        }
    }
}

pub(super) struct PreparedNextFrame {
    key: NextFrameKey,
    image: ReaderImageFrame,
    proofs: Vec<crate::render_cache::SourceProof>,
    rasters: Vec<crate::render_cache::RetainedRaster>,
    _leases: Vec<crate::cache::CacheLease>,
    _reservation: super::prepared_pixels::PreparedReservation,
}

impl PreparedNextFrame {
    fn retained_bytes(&self) -> usize {
        self.image.byte_len().saturating_add(
            crate::render_cache::RetainedRaster::unique_retained_bytes(&self.rasters),
        )
    }
}

struct ReaderImageFrame {
    chapter_id: String,
    page: u32,
    offset: u32,
    first: super::prepared_pixels::PreparedPixels,
    second: Option<super::prepared_pixels::PreparedPixels>,
    wide: bool,
    wide_half: u8,
    scaled_height: u32,
    viewport_height: u32,
    preferences: Preferences,
    hardware_dither: bool,
    first_path: PathBuf,
    second_path: Option<PathBuf>,
    paths: Vec<CachedPage>,
}

impl ReaderImageFrame {
    fn byte_len(&self) -> usize {
        super::prepared_pixels::unique_prepared_bytes(&[
            &self.first,
            self.second.as_ref().unwrap_or(&self.first),
        ])
    }

    fn into_job_result(self, prepared: bool) -> JobResult {
        JobResult::ReaderImage {
            chapter_id: self.chapter_id,
            page: self.page,
            offset: self.offset,
            first: self.first,
            second: self.second,
            wide: self.wide,
            wide_half: self.wide_half,
            scaled_height: self.scaled_height,
            viewport_height: self.viewport_height,
            preferences: self.preferences,
            hardware_dither: self.hardware_dither,
            first_path: self.first_path,
            second_path: self.second_path,
            paths: self.paths,
            prepared,
        }
    }
}

struct NextFrameBuilder {
    key: NextFrameKey,
    first_index: usize,
    count: usize,
    frames: Vec<Option<RetainedPrefetchFrame>>,
}

struct RetainedPrefetchFrame {
    pixels: super::prepared_pixels::PreparedPixels,
    path: PathBuf,
    cached: CachedPage,
    proof: crate::render_cache::SourceProof,
    raster: crate::render_cache::RetainedRaster,
    wide: bool,
    half: u8,
    scaled_height: u32,
    viewport_height: u32,
    hardware_dither: bool,
}

impl NextFrameKey {
    fn origin_is_current(&self, app: &App) -> bool {
        app.route == "reader"
            && !app.suspended
            && !app.resume_frame_pending
            && app.lifecycle_generation == self.lifecycle_generation
            && app.source_revision == self.source_revision
            && app.reader_generation == self.generation
            && app.reader_target().as_ref() == Some(&self.origin)
            && app.effective_reader_preferences() == self.preferences
            && self.hardware_dither
                == self
                    .preferences
                    .uses_hardware_dither(app.platform.hardware_y4_available())
            && app
                .platform
                .runtime_logical_size(self.preferences.landscape)
                == self.viewport
    }

    fn target_is_current(&self, app: &App) -> bool {
        let displayed = app.displayed_reader.as_ref().map(|frame| {
            ReaderTarget::new(
                &frame.session.chapter_id,
                frame.session.page,
                frame.session.vertical_offset,
                frame.session.zoom,
            )
            .with_wide_half(frame.session.wide_half)
        });
        app.route == "reader"
            && !app.suspended
            && !app.resume_frame_pending
            && app.lifecycle_generation == self.lifecycle_generation
            && app.source_revision == self.source_revision
            && app.reader_generation == self.generation.wrapping_add(1)
            && displayed.as_ref() == Some(&self.origin)
            && app.reader_target().as_ref() == Some(&self.target)
            && app.effective_reader_preferences() == self.preferences
            && self.hardware_dither
                == self
                    .preferences
                    .uses_hardware_dither(app.platform.hardware_y4_available())
            && app
                .platform
                .runtime_logical_size(self.preferences.landscape)
                == self.viewport
    }
}

impl NextFrameBuilder {
    fn new(key: NextFrameKey, first_index: usize, count: usize) -> Self {
        Self {
            key,
            first_index,
            count,
            frames: (0..count).map(|_| None).collect(),
        }
    }

    fn retain(
        &mut self,
        chapter: &Chapter,
        frame: &PrefetchFrame,
        cached: &CachedPage,
        prepared: &super::wide_pages::WidePage,
        pixels: super::prepared_pixels::PreparedPixels,
    ) -> Result<()> {
        if chapter.id != self.key.target.chapter_id || !self.accepts(frame) {
            return Ok(());
        }
        let relative = frame.index - self.first_index;
        let Some(proof) = prepared.rendered.source_proof.clone() else {
            return Ok(());
        };
        let Some(raster) = prepared.rendered.retained_raster.clone() else {
            return Ok(());
        };
        // A download-time derivative requires its original provenance lookup.
        // Until that composite proof is retained, it remains a normal cache hit.
        if fs::canonicalize(&cached.path)?.as_path() != proof.source_path() {
            return Ok(());
        }
        self.frames[relative] = Some(RetainedPrefetchFrame {
            pixels,
            path: prepared.rendered.cache_path.clone(),
            cached: cached.clone(),
            proof,
            raster,
            wide: prepared.wide,
            half: prepared.half,
            scaled_height: prepared.rendered.scaled_height,
            viewport_height: prepared.rendered.viewport_height,
            hardware_dither: frame.options.hardware_dither,
        });
        Ok(())
    }

    fn accepts(&self, frame: &PrefetchFrame) -> bool {
        frame.index >= self.first_index
            && frame.index - self.first_index < self.count
            && frame.offset == self.key.target.vertical_offset
            && frame.zoom == self.key.target.zoom
            && frame.half == self.key.target.wide_half
            && frame.options.hardware_dither == self.key.hardware_dither
    }

    fn finish(self, cache: &crate::cache::CacheManager) -> Option<PreparedNextFrame> {
        let mut frames = self.frames.into_iter().collect::<Option<Vec<_>>>()?;
        let first = frames.remove(0);
        let second = frames.pop();
        let mut paths = vec![first.cached.clone()];
        let mut proofs = vec![first.proof];
        let mut rasters = vec![first.raster];
        if let Some(second) = second.as_ref() {
            paths.push(second.cached.clone());
            proofs.push(second.proof.clone());
            rasters.push(second.raster.clone());
        }
        let image = ReaderImageFrame {
            chapter_id: self.key.target.chapter_id.clone(),
            page: self.key.target.page,
            offset: self.key.target.vertical_offset,
            first: first.pixels,
            second: second.as_ref().map(|frame| frame.pixels.clone()),
            wide: first.wide,
            wide_half: first.half,
            scaled_height: first.scaled_height,
            viewport_height: first.viewport_height,
            preferences: self.key.preferences.clone(),
            hardware_dither: first.hardware_dither,
            first_path: first.path,
            second_path: second.map(|frame| frame.path),
            paths,
        };
        let retained_bytes = image.byte_len().saturating_add(
            crate::render_cache::RetainedRaster::unique_retained_bytes(&rasters),
        );
        let reservation = super::prepared_pixels::reserve_next_frame(retained_bytes)?;
        let leases = std::iter::once(&image.first_path)
            .chain(image.second_path.iter())
            .chain(image.paths.iter().map(|path| &path.path))
            .map(|path| cache.lease_path(path))
            .collect::<Result<Vec<_>>>()
            .ok()?;
        Some(PreparedNextFrame {
            key: self.key,
            image,
            proofs,
            rasters,
            _leases: leases,
            _reservation: reservation,
        })
    }
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
        self.cancel_reader_image();
        self.cancel_reader_prefetch();
        self.reader_generation = self.reader_generation.wrapping_add(1);
        crate::reader_timing::request(self.reader_generation, self.lifecycle_generation);
        self.platform.cancel_reader_frame();
        self.reader_dirty = true;
        self.reader_refresh = merge_refresh(self.reader_refresh, refresh);
        if let Some(id) = self.reader_inflight {
            // A burst of taps updates the desired state, never the number of
            // decoder threads. The newest frame starts after this one completes.
            self.pending.insert(id);
            self.busy = true;
            crate::reader_timing::record(
                "request_waiting_for_worker",
                crate::reader_timing::token(self.reader_generation, self.lifecycle_generation),
            );
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
            crate::reader_timing::failed(crate::reader_timing::token(
                self.reader_generation,
                self.lifecycle_generation,
            ));
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
        let timing =
            crate::reader_timing::request(self.reader_generation, self.lifecycle_generation);
        if self.begin_prepared_reader_image_job(timing)? {
            return Ok(());
        }
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
        let hardware_dither = effective.uses_hardware_dither(self.platform.hardware_y4_available());
        let options = reader_image_options_for_mode(
            &effective,
            viewport,
            index,
            window.total,
            hardware_dither,
        );
        let offline =
            self.options.offline || self.options.demo || self.platform.network_policy_blocked();
        let cancellation = imaging::DecodeCancellation::new();
        let worker_cancellation = cancellation.clone();
        let id = self.spawn_job_kind(true, JobKind::Reader, move |manager| {
            worker_cancellation.check()?;
            let mut pages = window.pages.into_iter();
            let request = pages
                .next()
                .context("The requested page is outside this chapter")?;
            let mut paths = Vec::with_capacity(if spread { 2 } else { 1 });
            let fetch_timing = crate::reader_timing::Stage::new(timing, "fetch_finished");
            let cached = cached_page(
                &manager,
                &request,
                &source_id,
                index,
                offline,
                FetchPriority::Interactive,
            )?;
            drop(fetch_timing);
            let mut render_timing = crate::reader_timing::Stage::new(timing, "render_finished");
            // A raw transfer can have download or reader followers. Only the
            // private render uses this reader generation's cancellation fence.
            let prepared = imaging::with_decode_cancellation(&worker_cancellation, || {
                render_reader_page(
                    manager.cache(),
                    &request,
                    &cached.path,
                    &options,
                    &effective,
                    viewport,
                    vertical,
                    offset,
                    zoom,
                    half,
                    None,
                )
            })?;
            render_timing.render_result(prepared.rendered.cache_hit, prepared.rendered.memory_hit);
            drop(render_timing);
            paths.push(cached);
            let wide = prepared.wide;
            let wide_half = prepared.half;
            let rendered = prepared.rendered;
            let scaled_height = rendered.scaled_height;
            let viewport_height = rendered.viewport_height;
            let first_path = rendered.cache_path;
            let first = rendered.image;
            let (second, second_path) = if spread {
                worker_cancellation.check()?;
                let request = pages
                    .next()
                    .context("The second spread page is outside this chapter")?;
                let fetch_timing = crate::reader_timing::Stage::new(timing, "fetch_finished");
                let cached = cached_page(
                    &manager,
                    &request,
                    &source_id,
                    index + 1,
                    offline,
                    FetchPriority::Interactive,
                )?;
                drop(fetch_timing);
                let mut render_timing = crate::reader_timing::Stage::new(timing, "render_finished");
                let prepared = imaging::with_decode_cancellation(&worker_cancellation, || {
                    render_reader_page(
                        manager.cache(),
                        &request,
                        &cached.path,
                        &options,
                        &effective,
                        viewport,
                        false,
                        0,
                        zoom,
                        0,
                        None,
                    )
                })?;
                render_timing
                    .render_result(prepared.rendered.cache_hit, prepared.rendered.memory_hit);
                drop(render_timing);
                paths.push(cached);
                let rendered = prepared.rendered;
                (Some(rendered.image), Some(rendered.cache_path))
            } else {
                (None, None)
            };
            worker_cancellation.check()?;
            let prepare_timing = crate::reader_timing::Stage::new(timing, "prepare_finished");
            let (first, second) = imaging::with_decode_cancellation(&worker_cancellation, || {
                Ok((
                    super::prepared_pixels::prepare_cancellable(first)?,
                    second
                        .map(super::prepared_pixels::prepare_cancellable)
                        .transpose()?,
                ))
            })?;
            drop(prepare_timing);
            Ok(JobResult::ReaderImage {
                chapter_id,
                page,
                offset,
                wide,
                wide_half,
                first,
                second,
                scaled_height,
                viewport_height,
                preferences: effective,
                hardware_dither,
                first_path,
                second_path,
                paths,
                prepared: false,
            })
        })?;
        self.reader_inflight = Some(id);
        self.reader_image_cancel = Some(cancellation);
        self.reader_image_jobs_started = self.reader_image_jobs_started.saturating_add(1);
        self.reader_dirty = false;
        Ok(())
    }

    fn begin_prepared_reader_image_job(
        &mut self,
        timing: Option<crate::reader_timing::RequestToken>,
    ) -> Result<bool> {
        let frame = match &self.reader_next_frame {
            NextFrameSlot::Ready(frame) if frame.key.target_is_current(self) => {
                match std::mem::take(&mut self.reader_next_frame) {
                    NextFrameSlot::Ready(frame) => frame,
                    _ => unreachable!(),
                }
            }
            NextFrameSlot::Ready(_) => {
                self.clear_reader_next_frame();
                return Ok(false);
            }
            _ => return Ok(false),
        };
        let cache = self.disk_cache.clone();
        let cancellation = imaging::DecodeCancellation::new();
        let worker_cancellation = cancellation.clone();
        crate::reader_timing::record("prepared_frame_candidate", timing);
        let id = self.spawn_worker(
            true,
            true,
            JobKind::Reader,
            self.reader_target(),
            None,
            move || {
                worker_cancellation.check()?;
                // BiliComics revalidates the original source before reusing its
                // retained Raster. External originals have no immutability contract.
                let verification =
                    crate::reader_timing::Stage::new(timing, "prepared_source_verified");
                for proof in &frame.proofs {
                    worker_cancellation.check()?;
                    if !imaging::with_decode_cancellation(&worker_cancellation, || {
                        proof.verify(&cache)
                    })
                    .unwrap_or(false)
                    {
                        crate::reader_timing::record("prepared_frame_stale", timing);
                        return Ok(JobResult::ReaderPreparationStale);
                    }
                }
                drop(verification);
                worker_cancellation.check()?;
                for raster in &frame.rasters {
                    worker_cancellation.check()?;
                    raster.promote(&cache)?;
                }
                crate::reader_timing::record("prepared_frame_hit", timing);
                Ok(frame.image.into_job_result(true))
            },
        )?;
        self.reader_inflight = Some(id);
        self.reader_image_cancel = Some(cancellation);
        self.reader_image_jobs_started = self.reader_image_jobs_started.saturating_add(1);
        self.reader_dirty = false;
        Ok(true)
    }

    pub(super) fn clear_reader_next_frame(&mut self) {
        self.reader_next_frame = NextFrameSlot::Empty;
    }

    pub(super) fn finish_reader_next_frame_preparation(&mut self, generation: u64) {
        if matches!(&self.reader_next_frame, NextFrameSlot::Preparing(key)
            if key.generation == generation)
        {
            self.clear_reader_next_frame();
        }
    }

    pub(super) fn cancel_reader_image(&mut self) {
        if let Some(token) = self.reader_image_cancel.take() {
            token.cancel();
        }
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
        let hardware_dither = effective.uses_hardware_dither(self.platform.hardware_y4_available());
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
        let (first, pages, options) =
            prefetch_pages_for_mode(&effective, window, viewport, hardware_dither);
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
                        options: reader_image_options_for_mode(
                            &effective,
                            viewport,
                            next_index,
                            total,
                            hardware_dither,
                        ),
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
                    options: reader_image_options_for_mode(
                        &effective,
                        viewport,
                        current,
                        total,
                        hardware_dither,
                    ),
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
        let mut next_frame = self.next_frame_builder(&effective, viewport, hardware_dither);
        if let Some(builder) = next_frame.as_ref() {
            // Publish the nearest complete frame before spending another decode
            // on a farther page. The next accepted frame advances this slot.
            frames.retain(|frame| builder.accepts(frame));
        }
        let next_chapter = if next_frame.is_some() {
            None
        } else {
            next_chapter
        };
        let next_key = next_frame.as_ref().map(|builder| builder.key.clone());
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
                        &manager,
                        chapter,
                        None,
                        frames,
                        offline,
                        &effective,
                        viewport,
                        token,
                        next_frame.as_mut(),
                    )?);
                }
                if let Some(next) = next_chapter {
                    token.check()?;
                    let chapter_id = next.chapter.id.clone();
                    match prefetch_next_chapter(
                        &manager,
                        next,
                        &effective,
                        viewport,
                        hardware_dither,
                        offline,
                        token,
                    ) {
                        Ok(next) => chapters.push(next),
                        Err(error) if !token.is_cancelled() => {
                            eprintln!("aidoku: next chapter read-ahead {chapter_id}: {error:#}");
                        }
                        Err(_) => {}
                    }
                }
                token.check()?;
                let next_frame = next_frame.and_then(|builder| builder.finish(manager.cache()));
                Ok(Prefetched {
                    generation,
                    chapters,
                    next_frame,
                })
            });
            if token.is_cancelled() {
                return Ok(JobResult::Prefetched(Prefetched {
                    generation,
                    chapters: Vec::new(),
                    next_frame: None,
                }));
            }
            result.map(JobResult::Prefetched)
        })?;
        self.prefetch_inflight = Some(id);
        self.reader_prefetch_cancel = Some(cancellation);
        self.reader_next_frame = next_key.map(NextFrameSlot::Preparing).unwrap_or_default();
        Ok(())
    }

    fn next_frame_builder(
        &self,
        preferences: &Preferences,
        viewport: (u32, u32),
        hardware_dither: bool,
    ) -> Option<NextFrameBuilder> {
        let reader = self.reader.as_ref()?;
        let displayed = self.displayed_reader.as_ref()?;
        let origin = self.reader_target()?;
        if displayed.session.chapter_id != origin.chapter_id
            || displayed.session.page != origin.page
            || displayed.session.vertical_offset != origin.vertical_offset
            || displayed.session.zoom != origin.zoom
            || displayed.session.wide_half != origin.wide_half
        {
            return None;
        }
        let target = if reader.is_wide_page() && reader.wide_half == 0 {
            ReaderTarget::new(&origin.chapter_id, origin.page, origin.vertical_offset, 1.0)
                .with_wide_half(1)
        } else if is_strip(preferences) {
            match strip_prefetch_offsets(
                reader.vertical_offset,
                reader.viewport_height,
                reader.image_height,
            )
            .first()
            {
                Some(offset) => {
                    ReaderTarget::new(&origin.chapter_id, reader.page, *offset, reader.zoom)
                }
                None if *reader.visible_pages().end() < reader.total => ReaderTarget::new(
                    &origin.chapter_id,
                    reader.visible_pages().end().saturating_add(1),
                    0,
                    1.0,
                ),
                None => return None,
            }
        } else if *reader.visible_pages().end() < reader.total {
            ReaderTarget::new(
                &origin.chapter_id,
                reader.visible_pages().end().saturating_add(1),
                0,
                1.0,
            )
        } else {
            // Cross-chapter manifest preparation remains on its existing path.
            return None;
        };
        let first = PageLayout::from_preferences(preferences)
            .first_index(target.page.saturating_sub(1) as usize);
        let count = frame_page_count(preferences, first, reader.total as usize);
        (count > 0).then(|| {
            NextFrameBuilder::new(
                NextFrameKey {
                    origin,
                    target,
                    generation: self.reader_generation,
                    lifecycle_generation: self.lifecycle_generation,
                    source_revision: self.source_revision,
                    preferences: preferences.clone(),
                    hardware_dither,
                    viewport,
                },
                first,
                count,
            )
        })
    }

    pub(super) fn cancel_reader_prefetch(&mut self) {
        if let Some(token) = self.reader_prefetch_cancel.take() {
            token.cancel();
        }
    }

    pub(super) fn accept_reader_prefetch(&mut self, result: Prefetched) -> Result<()> {
        self.finish_reader_next_frame_preparation(result.generation);
        if result.generation != self.reader_generation || self.route != "reader" {
            return Ok(());
        }
        let mut manifest_current = true;
        for batch in result.chapters {
            let Some(chapter) = self.store.chapter(&batch.chapter.id)? else {
                manifest_current = false;
                continue;
            };
            if !same_chapter_identity(&chapter, &batch.chapter) {
                manifest_current = false;
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
            manifest_current &= self.save_cached_paths(&chapter.id, batch.paths)?;
        }
        if let Some(frame) = result.next_frame {
            if manifest_current && frame.key.origin_is_current(self) {
                self.reader_next_frame = NextFrameSlot::Ready(Box::new(frame));
            }
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
    hardware_dither: bool,
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
    let frames = chapter_head_frames_for_mode(
        preferences,
        window,
        viewport,
        plan.capacity,
        hardware_dither,
    );
    prefetch_chapter_frames(
        manager,
        plan.chapter,
        manifest,
        frames,
        offline,
        preferences,
        viewport,
        cancellation,
        None,
    )
}

fn chapter_head_frames_for_mode(
    preferences: &Preferences,
    window: crate::store::PageWindow,
    viewport: (u32, u32),
    capacity: usize,
    hardware_dither: bool,
) -> Vec<PrefetchFrame> {
    let options =
        reader_image_options_for_mode(preferences, viewport, 0, window.total, hardware_dither);
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

#[cfg(test)]
fn chapter_head_frames(
    preferences: &Preferences,
    window: crate::store::PageWindow,
    viewport: (u32, u32),
    capacity: usize,
) -> Vec<PrefetchFrame> {
    chapter_head_frames_for_mode(preferences, window, viewport, capacity, false)
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
    mut next_frame: Option<&mut NextFrameBuilder>,
) -> Result<PrefetchedChapter> {
    let mut paths = Vec::with_capacity(frames.len());
    let mut fetched = HashMap::<usize, CachedPage>::new();
    let mut failed = HashSet::new();
    for frame in frames {
        cancellation.check()?;
        if failed.contains(&frame.index) {
            continue;
        }
        let cached = if let Some(cached) = fetched.get(&frame.index) {
            cached.clone()
        } else {
            match cached_page(
                manager,
                &frame.page,
                &chapter.source_id,
                frame.index,
                offline,
                FetchPriority::Prefetch,
            ) {
                Ok(cached) => {
                    fetched.insert(frame.index, cached.clone());
                    cached
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
        match render_reader_page_with_next(
            manager.cache(),
            &frame.page,
            &cached.path,
            &frame.options,
            preferences,
            viewport,
            frame.vertical,
            frame.offset,
            frame.zoom,
            frame.half,
            Some(cancellation),
            next_frame
                .as_ref()
                .is_some_and(|builder| builder.accepts(&frame)),
        ) {
            Ok(prepared) => {
                cancellation.check()?;
                let pixels = imaging::with_decode_cancellation(cancellation, || {
                    super::prepared_pixels::prepare_cancellable(prepared.rendered.image.clone())
                })?;
                if let Some(next_frame) = next_frame.as_deref_mut() {
                    if let Err(error) =
                        next_frame.retain(&chapter, &frame, &cached, &prepared, pixels)
                    {
                        eprintln!("aidoku: next-frame proof unavailable: {error:#}");
                    }
                }
                if !paths
                    .iter()
                    .any(|path: &CachedPage| path.index == cached.index)
                {
                    paths.push(cached);
                }
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
    if !vertical
        && !preferences.spread
        && !preferences.split_wide
        && matches!(
            preferences.reader_fit.as_str(),
            "original" | "page" | "height" | "width"
        )
    {
        return false;
    }
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
    render_reader_page_with_next(
        cache,
        page,
        original,
        options,
        preferences,
        viewport,
        vertical,
        offset,
        zoom,
        half,
        cancellation,
        false,
    )
}

fn render_reader_page_with_next(
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
    prepare_next: bool,
) -> Result<super::wide_pages::WidePage> {
    let render = |source: &Path, stage_next: bool| {
        if let Some(token) = cancellation {
            if stage_next {
                super::wide_pages::prefetch_next(
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
            }
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
        return render(original, prepare_next);
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
            // A derivative is not retained in the ready slot until its original
            // provenance can be carried too. Preserve ordinary prefetch reuse.
            if let Ok(rendered) = render(&prepared, false) {
                return Ok(rendered);
            }
            if let Some(token) = cancellation {
                token.check()?;
            }
        }
    }
    // This path only looks up download-time copies. It never creates or repairs
    // one on a page turn, and never replaces Page.local_path with a derivative.
    render(original, prepare_next)
}

fn cached_page(
    manager: &SourceManager,
    request: &Page,
    source_id: &str,
    index: usize,
    offline: bool,
    priority: FetchPriority,
) -> Result<CachedPage> {
    let identity = cached_page_identity(request, index);
    let path = if let Some(path) = request.local_path.as_ref().filter(|path| path.is_file()) {
        path.clone()
    } else if let Some(path) = manager.cache().lookup_raw(request)? {
        path
    } else if request.url.is_empty() && request.local_path.is_some() {
        return Err(std::io::Error::from(std::io::ErrorKind::NotFound))
            .context("The local comic page is missing");
    } else if offline {
        bail!("{}", tr("chapter_unavailable"));
    } else {
        manager.fetch_page_for_source(source_id, request, priority, None)?
    };
    let metadata = fs::metadata(&path).context("Read the reader source metadata")?;
    anyhow::ensure!(
        metadata.is_file() && metadata.len() > 0 && metadata.len() <= imaging::MAX_PAGE_BYTES,
        "The reader source is empty or exceeds the compressed-image limit"
    );
    // This is a size-bounded candidate, not an image or checksum proof. The
    // following render verifies source bytes and the complete rendering key;
    // doing fingerprint or codec work here would duplicate that verification.
    // Callers publish its manifest path only after the render succeeds.
    Ok(CachedPage {
        index,
        identity,
        path,
    })
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

fn reader_image_options_for_mode(
    preferences: &Preferences,
    viewport: (u32, u32),
    first_index: usize,
    total: usize,
    hardware_dither: bool,
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
        hardware_dither,
        contrast: preferences.contrast,
    }
}

#[cfg(test)]
fn reader_image_options(
    preferences: &Preferences,
    viewport: (u32, u32),
    first_index: usize,
    total: usize,
) -> ImageOptions {
    reader_image_options_for_mode(preferences, viewport, first_index, total, false)
}

fn prefetch_pages_for_mode(
    preferences: &Preferences,
    window: crate::store::PageWindow,
    viewport: (u32, u32),
    hardware_dither: bool,
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
    let options =
        reader_image_options_for_mode(preferences, viewport, first, window.total, hardware_dither);
    let pages = window
        .pages
        .into_iter()
        .skip(skip)
        .take(if vertical { 1 } else { 2 })
        .collect();
    (first, pages, options)
}

#[cfg(test)]
fn prefetch_pages(
    preferences: &Preferences,
    window: crate::store::PageWindow,
    viewport: (u32, u32),
) -> (usize, Vec<Page>, ImageOptions) {
    prefetch_pages_for_mode(preferences, window, viewport, false)
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
    use crate::{app::tests::DemoRouteFixture, platform::DeviceProfile};
    use image::Pixel;

    static READER_PUBLICATION_TESTS: std::sync::Mutex<()> = std::sync::Mutex::new(());

    thread_local! {
        static READER_TEST_PLATFORM_INSTALLED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
    }

    struct ReaderTestPlatform {
        window: Rc<slint::platform::software_renderer::MinimalSoftwareWindow>,
    }

    impl slint::platform::Platform for ReaderTestPlatform {
        fn create_window_adapter(
            &self,
        ) -> std::result::Result<Rc<dyn slint::platform::WindowAdapter>, slint::PlatformError>
        {
            Ok(self.window.clone())
        }
    }

    fn reader_test_window() -> AppWindow {
        READER_TEST_PLATFORM_INSTALLED.with(|installed| {
            if !installed.get() {
                slint::platform::set_platform(Box::new(ReaderTestPlatform {
                    window: slint::platform::software_renderer::MinimalSoftwareWindow::new(
                        slint::platform::software_renderer::RepaintBufferType::ReusedBuffer,
                    ),
                }))
                .unwrap();
                installed.set(true);
            }
        });
        let ui = AppWindow::new().unwrap();
        ui.global::<State>().set_booting(false);
        ui
    }

    #[test]
    fn prepared_frame_targets_follow_real_navigation_for_every_frame_shape() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Scribe, false);
        let app = &mut fixture.app;
        for (direction, spread, cover_alone, split_wide, page, offset, wide, half, zoom) in [
            ("rtl", false, false, false, 1, 0, false, 0, 2.0),
            ("ltr", true, false, false, 1, 0, false, 0, 1.0),
            ("rtl", true, true, false, 1, 0, false, 0, 1.0),
            ("rtl", true, true, false, 2, 0, false, 0, 1.0),
            ("vertical", false, false, false, 1, 0, false, 0, 2.0),
            ("vertical", false, false, false, 1, 9_000, false, 0, 2.0),
            ("rtl", false, false, true, 1, 0, true, 0, 2.0),
            ("rtl", false, false, true, 1, 0, true, 1, 2.0),
        ] {
            let preferences = Preferences {
                reading_direction: direction.into(),
                spread,
                cover_alone,
                split_wide,
                ..Preferences::default()
            };
            let mut reader = ReaderState::new(
                vec![Chapter {
                    id: "next-target-chapter".into(),
                    book_id: "next-target-book".into(),
                    page_count: 10,
                    ..Chapter::default()
                }],
                "next-target-chapter",
                page,
                &preferences,
            )
            .unwrap();
            reader.set_vertical_dimensions(10_000, 1_000);
            reader.vertical_offset = offset;
            reader.set_wide_page(wide, half);
            reader.zoom = zoom;
            app.displayed_reader = Some(DisplayedReader {
                session: ReaderSession {
                    book_id: "next-target-book".into(),
                    chapter_id: "next-target-chapter".into(),
                    page,
                    total: 10,
                    vertical_offset: offset,
                    wide_half: half,
                    wide_page: wide,
                    zoom,
                    ..ReaderSession::default()
                },
                preferences: preferences.clone(),
                hardware_dither: false,
                first_path: PathBuf::new(),
                second_path: None,
            });
            app.reader = Some(reader.clone());
            let planned = app
                .next_frame_builder(&preferences, (930, 1_240), false)
                .unwrap();
            assert_eq!(reader.next_page().action, ReaderAction::PageChanged);
            let expected = ReaderTarget::new(
                &reader.current_chapter().id,
                reader.page,
                reader.vertical_offset,
                reader.zoom,
            )
            .with_wide_half(reader.wide_half);
            assert_eq!(planned.key.target, expected);
        }
    }

    #[test]
    fn foreground_turns_cancel_old_work_before_starting_the_latest_frame() {
        let _publication = READER_PUBLICATION_TESTS.lock().unwrap();
        let ui = reader_test_window();
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw6, false);
        let app = &mut fixture.app;
        app.ui = ui.as_weak();
        let book = Book {
            id: "cancel-reader-book".into(),
            source_id: "local".into(),
            remote_id: "cancel-reader-book".into(),
            title: "Reader cancellation fixture".into(),
            ..Book::default()
        };
        let chapter = Chapter {
            id: "cancel-reader-chapter".into(),
            book_id: book.id.clone(),
            source_id: book.source_id.clone(),
            remote_id: "cancel-reader-chapter".into(),
            page_count: 4,
            ..Chapter::default()
        };
        let pages = (0..4)
            .map(|index| {
                let path = app.data_dir.join(format!("cancel-reader-{index}.png"));
                GrayImage::from_pixel(16, 24, image::Luma([48 + index * 64]))
                    .save(&path)
                    .unwrap();
                Page {
                    local_path: Some(path),
                    ..Page::default()
                }
            })
            .collect::<Vec<_>>();
        app.store.upsert_book(&book).unwrap();
        app.store
            .save_chapters(&book.id, &[chapter.clone()])
            .unwrap();
        app.store.save_pages(&chapter.id, &pages).unwrap();
        app.route = "reader".into();
        app.current_book_id = Some(book.id.clone());
        app.prefs.crop_borders = false;
        app.prefs.split_wide = false;
        app.prefs.spread = false;
        app.prefs.dither = false;
        app.reader =
            Some(ReaderState::new(vec![chapter.clone()], &chapter.id, 1, &app.prefs).unwrap());
        app.reader.as_mut().unwrap().set_total(4);

        // Keep the real decoder budget occupied. Superseding a render must
        // release its worker even before this budget becomes available.
        let active = imaging::decode_permit().unwrap();
        app.request_reader_images().unwrap();
        let first = app.reader_inflight.unwrap();
        let cancelled = app.reader_image_cancel.as_ref().unwrap().clone();
        app.dispatch("reader", "next", "").unwrap();
        assert_eq!(app.reader.as_ref().unwrap().page, 2);
        assert!(cancelled.is_cancelled());
        assert_eq!(app.reader_inflight, Some(first));
        assert_eq!(app.reader_image_jobs_started, 1);
        assert!(app.reader_dirty);

        let obsolete = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        assert_eq!(obsolete.id, first);
        assert!(imaging::is_decode_cancelled(
            obsolete.result.as_ref().err().unwrap()
        ));
        app.job_tx.send(obsolete).unwrap();
        app.poll();
        let latest = app.reader_inflight.unwrap();
        assert_ne!(latest, first);
        assert_eq!(app.reader_image_jobs_started, 2);
        assert!(!app.reader_dirty);
        assert!(app.reader_recovery.is_none());
        assert!(!app.reader_image_cancel.as_ref().unwrap().is_cancelled());
        drop(active);

        let completed = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        assert_eq!(completed.id, latest);
        match completed.result.as_ref().unwrap() {
            JobResult::ReaderImage { page, first, .. } => {
                assert_eq!(*page, 2);
                assert!(first.as_bytes().iter().all(|value| *value == 119));
            }
            _ => panic!("The latest reader job must prepare its requested frame"),
        }
        app.job_tx.send(completed).unwrap();
        app.poll();
        assert!(app.reader_inflight.is_none());
        assert!(app.reader_image_cancel.is_none());
        assert!(app.reader_recovery.is_none());
        assert_eq!(app.displayed_reader.as_ref().unwrap().session.page, 2);
        assert_eq!(app.committed_progress.as_ref().unwrap().page, 2);
        assert_eq!(ui.global::<State>().get_reader_page(), 2);
        let displayed = ui.global::<State>().get_reader_image().to_rgb8().unwrap();
        assert!(displayed.as_bytes().iter().all(|value| *value == 119));

        while app.prefetch_inflight.is_some() {
            let completed = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
            app.job_tx.send(completed).unwrap();
            app.poll();
        }
        assert!(matches!(app.reader_next_frame, NextFrameSlot::Ready(_)));

        // Exercise the same normalized left-side tap used by RTL navigation,
        // then consume the actual worker result through the publication path.
        app.dispatch("reader", "tap", "0.1,0.5").unwrap();
        assert_eq!(app.reader.as_ref().unwrap().page, 3);
        assert_eq!(ui.global::<State>().get_reader_page(), 2);
        let target = app.reader_inflight.unwrap();
        loop {
            let completed = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
            app.job_tx.send(completed).unwrap();
            app.poll();
            if app.reader_inflight != Some(target) {
                break;
            }
        }
        assert_eq!(app.displayed_reader.as_ref().unwrap().session.page, 3);
        assert_eq!(app.committed_progress.as_ref().unwrap().page, 3);
        assert_eq!(ui.global::<State>().get_reader_page(), 3);
        let displayed = ui.global::<State>().get_reader_image().to_rgb8().unwrap();
        assert!(displayed.as_bytes().iter().all(|value| *value == 170));
        assert_eq!(app.reader_next_frame_hits, 1);

        while app.prefetch_inflight.is_some() {
            let completed = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
            app.job_tx.send(completed).unwrap();
            app.poll();
        }
        assert!(matches!(app.reader_next_frame, NextFrameSlot::Ready(_)));
        GrayImage::from_pixel(16, 24, image::Luma([204]))
            .save(pages[3].local_path.as_ref().unwrap())
            .unwrap();
        app.dispatch("reader", "next", "").unwrap();
        while app.reader_inflight.is_some() || app.reader_dirty {
            let completed = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
            app.job_tx.send(completed).unwrap();
            app.poll();
        }
        assert_eq!(app.reader_next_frame_hits, 1);
        assert_eq!(app.reader_next_frame_misses, 1);
        assert_eq!(app.displayed_reader.as_ref().unwrap().session.page, 4);
        assert_eq!(app.committed_progress.as_ref().unwrap().page, 4);
        assert_eq!(ui.global::<State>().get_reader_page(), 4);
        let displayed = ui.global::<State>().get_reader_image().to_rgb8().unwrap();
        assert!(displayed.as_bytes().iter().all(|value| *value == 204));
        assert!(app.reader_recovery.is_none());
        let released = super::super::prepared_pixels::next_frame_stats();
        assert_eq!(released.bytes, 0);
        assert_eq!(released.reservations, 0);
    }

    #[test]
    fn prepared_raw_gray_and_rgb_keep_dithering_through_forward_and_previous_publication() {
        let _publication = READER_PUBLICATION_TESTS.lock().unwrap();
        let ui = reader_test_window();
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw6, false);
        let app = &mut fixture.app;
        app.ui = ui.as_weak();
        let book = Book {
            id: "reader-history-book".into(),
            source_id: "local".into(),
            remote_id: "reader-history-book".into(),
            title: "Reader history fixture".into(),
            ..Book::default()
        };
        let chapter = Chapter {
            id: "reader-history-chapter".into(),
            book_id: book.id.clone(),
            source_id: book.source_id.clone(),
            remote_id: "reader-history-chapter".into(),
            page_count: 3,
            ..Chapter::default()
        };
        let levels = [51, 102, 153];
        let pages = levels
            .iter()
            .enumerate()
            .map(|(index, level)| {
                let path = app.data_dir.join(format!("reader-history-{index}.png"));
                GrayImage::from_pixel(16, 24, image::Luma([*level]))
                    .save(&path)
                    .unwrap();
                Page {
                    local_path: Some(path),
                    ..Page::default()
                }
            })
            .collect::<Vec<_>>();
        app.store.upsert_book(&book).unwrap();
        app.store
            .save_chapters(&book.id, &[chapter.clone()])
            .unwrap();
        app.store.save_pages(&chapter.id, &pages).unwrap();
        app.current_book_id = Some(book.id);
        app.route = "reader".into();
        app.prefs.crop_borders = false;
        app.prefs.split_wide = false;
        app.prefs.spread = false;
        app.prefs.dither = true;
        app.prefs.dither_method = "hardware".into();
        app.platform.set_test_hardware_y4_available(true);
        app.reader =
            Some(ReaderState::new(vec![chapter.clone()], &chapter.id, 1, &app.prefs).unwrap());
        app.request_reader_images().unwrap();

        let finish_jobs = |app: &mut App| {
            while app.reader_inflight.is_some() || app.prefetch_inflight.is_some() {
                let completed = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
                app.job_tx.send(completed).unwrap();
                app.poll();
            }
        };
        let assert_published = |app: &App, page: u32| {
            assert_eq!(app.displayed_reader.as_ref().unwrap().session.page, page);
            assert!(app.displayed_reader.as_ref().unwrap().hardware_dither);
            assert!(app.platform.reader_hardware_y4_requested());
            assert_eq!(app.committed_progress.as_ref().unwrap().page, page);
            assert_eq!(ui.global::<State>().get_reader_page(), page as i32);
            let displayed = ui.global::<State>().get_reader_image().to_rgb8().unwrap();
            assert!(
                displayed
                    .as_bytes()
                    .iter()
                    .all(|value| *value == levels[page as usize - 1])
            );
            assert!(app.reader_recovery.is_none());
        };
        finish_jobs(app);
        assert_published(app, 1);
        let NextFrameSlot::Ready(frame) = &app.reader_next_frame else {
            panic!("The next complete frame must be retained after preparation");
        };
        assert!(frame.key.hardware_dither && frame.image.hardware_dither);
        assert!(!frame.rasters.is_empty());
        assert!(frame.retained_bytes() > frame.image.byte_len());
        assert_eq!(
            frame.retained_bytes(),
            super::super::prepared_pixels::next_frame_stats().bytes
        );

        for (action, page) in [("next", 2), ("next", 3), ("previous", 2), ("previous", 1)] {
            app.dispatch("reader", action, "").unwrap();
            finish_jobs(app);
            assert_published(app, page);
        }
        assert_eq!(app.reader_next_frame_hits, 2);
        assert_eq!(app.reader_next_frame_misses, 0);
        let allocated = super::super::prepared_pixels::next_frame_stats();
        assert!(allocated.bytes <= allocated.budget_bytes);
        assert!(allocated.reservations <= 1);
        app.invalidate_reader_work();
        assert!(matches!(app.reader_next_frame, NextFrameSlot::Empty));
        let released = super::super::prepared_pixels::next_frame_stats();
        assert_eq!(released.bytes, 0);
        assert_eq!(released.reservations, 0);
    }

    #[test]
    fn asynchronous_dither_targets_never_change_the_retained_frame_processing() {
        let _publication = READER_PUBLICATION_TESTS.lock().unwrap();
        let ui = reader_test_window();
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Scribe, false);
        let app = &mut fixture.app;
        app.ui = ui.as_weak();
        let book = Book {
            id: crate::domain::compound_id("local", "dither-publication-book"),
            source_id: "local".into(),
            remote_id: "dither-publication-book".into(),
            title: "Dither publication fixture".into(),
            ..Book::default()
        };
        let chapter = Chapter {
            id: crate::domain::compound_id("local", "dither-publication-chapter"),
            book_id: book.id.clone(),
            source_id: book.source_id.clone(),
            remote_id: "dither-publication-chapter".into(),
            page_count: 1,
            ..Chapter::default()
        };
        let path = app.data_dir.join("dither-publication-ramp.png");
        GrayImage::from_fn(256, 4, |x, _| image::Luma([x as u8]))
            .save(&path)
            .unwrap();
        app.store.upsert_book(&book).unwrap();
        app.store
            .save_chapters(&book.id, &[chapter.clone()])
            .unwrap();
        app.store
            .save_pages(
                &chapter.id,
                &[Page {
                    local_path: Some(path),
                    ..Page::default()
                }],
            )
            .unwrap();
        app.current_book_id = Some(book.id);
        app.route = "reader".into();
        app.prefs.crop_borders = false;
        app.prefs.split_wide = false;
        app.prefs.spread = false;
        app.prefs.contrast = 0;
        app.prefs.dither = true;
        app.prefs.dither_method = "hardware".into();
        app.reader =
            Some(ReaderState::new(vec![chapter.clone()], &chapter.id, 1, &app.prefs).unwrap());

        // A persisted hardware choice on an unsupported cold start prepares the
        // original software pixels without sending a raw request to the backend.
        assert!(!app.platform.hardware_y4_available());
        app.request_reader_images().unwrap();
        let cold = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        let JobResult::ReaderImage {
            hardware_dither,
            first,
            ..
        } = cold.result.as_ref().unwrap()
        else {
            panic!("The cold reader must produce a pixel frame");
        };
        assert!(!hardware_dither);
        assert!(first.as_bytes().iter().all(|sample| sample % 17 == 0));
        app.job_tx.send(cold).unwrap();
        app.poll();
        assert!(!app.platform.reader_hardware_y4_requested());
        assert!(!app.displayed_reader.as_ref().unwrap().hardware_dither);

        app.platform.set_test_hardware_y4_available(true);
        app.request_reader_images().unwrap();
        assert!(app.reader_dither_pending());
        assert!(!app.platform.reader_hardware_y4_requested());
        let raw = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        let JobResult::ReaderImage {
            hardware_dither,
            first,
            ..
        } = raw.result.as_ref().unwrap()
        else {
            panic!("The supported reader must produce raw samples");
        };
        assert!(*hardware_dither);
        assert!(first.as_bytes().iter().any(|sample| sample % 17 != 0));
        // Capability can be revoked while the worker is finishing. Publication
        // must preserve its actual raw mode so native fallback owns those samples.
        app.platform.set_test_hardware_y4_available(false);
        app.job_tx.send(raw).unwrap();
        app.poll();
        assert!(app.displayed_reader.as_ref().unwrap().hardware_dither);
        assert!(app.platform.reader_hardware_y4_requested());
        assert!(!app.reader_dither_pending());

        app.platform.set_test_hardware_y4_available(true);
        app.route = "settings".into();
        ui.global::<State>().set_route("settings".into());
        app.sync_presented_reader_dithering();
        assert!(!app.platform.reader_hardware_y4_requested());
        app.route = "reader".into();
        ui.global::<State>().set_route("reader".into());
        app.sync_presented_reader_dithering();
        assert!(app.platform.reader_hardware_y4_requested());

        app.prefs.dither_method = "software".into();
        app.request_reader_images().unwrap();
        assert!(app.reader_dither_pending());
        assert!(app.platform.reader_hardware_y4_requested());
        let obsolete = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        assert!(matches!(
            obsolete.result.as_ref().unwrap(),
            JobResult::ReaderImage {
                hardware_dither: false,
                ..
            }
        ));
        app.prefs.dither_method = "hardware".into();
        app.request_reader_images().unwrap();
        let old_generation = obsolete.context.reader_generation;
        assert_ne!(old_generation, app.reader_generation);
        app.job_tx.send(obsolete).unwrap();
        app.poll();
        assert!(app.displayed_reader.as_ref().unwrap().hardware_dither);
        assert!(app.platform.reader_hardware_y4_requested());
        let current = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        app.job_tx.send(current).unwrap();
        app.poll();
        assert!(app.displayed_reader.as_ref().unwrap().hardware_dither);

        app.prefs.dither_method = "software".into();
        app.request_reader_images().unwrap();
        assert!(app.platform.reader_hardware_y4_requested());
        let software = app.job_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        app.job_tx.send(software).unwrap();
        app.poll();
        assert!(!app.displayed_reader.as_ref().unwrap().hardware_dither);
        assert!(!app.platform.reader_hardware_y4_requested());
        assert!(!app.reader_dither_pending());
        let pixels = ui.global::<State>().get_reader_image().to_rgb8().unwrap();
        assert!(pixels.as_bytes().iter().all(|sample| sample % 17 == 0));
    }

    #[test]
    fn poll_projects_post_send_hardware_rejection_without_rerendering_the_retained_frame() {
        let _publication = READER_PUBLICATION_TESTS.lock().unwrap();
        let ui = reader_test_window();
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Scribe, false);
        let app = &mut fixture.app;
        app.ui = ui.as_weak();
        app.route = "reader".into();
        app.prefs.dither = true;
        app.prefs.dither_method = "hardware".into();
        app.platform.set_test_hardware_y4_available(true);
        let book = Book {
            id: crate::domain::compound_id("local", "post-send-book"),
            source_id: "local".into(),
            remote_id: "post-send-book".into(),
            title: "Post-SEND fallback fixture".into(),
            ..Book::default()
        };
        let chapter = Chapter {
            id: crate::domain::compound_id("local", "post-send-chapter"),
            book_id: book.id.clone(),
            source_id: book.source_id.clone(),
            remote_id: "post-send-chapter".into(),
            page_count: 1,
            ..Chapter::default()
        };
        app.store.upsert_book(&book).unwrap();
        app.store
            .save_chapters(&book.id, &[chapter.clone()])
            .unwrap();
        app.current_book_id = Some(book.id.clone());
        app.reader =
            Some(ReaderState::new(vec![chapter.clone()], &chapter.id, 1, &app.prefs).unwrap());
        app.displayed_reader = Some(DisplayedReader {
            session: ReaderSession {
                book_id: book.id,
                chapter_id: chapter.id,
                page: 1,
                total: 1,
                ..ReaderSession::default()
            },
            preferences: app.effective_reader_preferences(),
            hardware_dither: true,
            first_path: PathBuf::from("post-send-retained.png"),
            second_path: None,
        });
        let mut pixels = slint::SharedPixelBuffer::<slint::Rgb8Pixel>::new(2, 1);
        pixels
            .make_mut_bytes()
            .copy_from_slice(&[42, 42, 42, 216, 216, 216]);
        ui.global::<State>()
            .set_reader_image(Image::from_rgb8(pixels));
        ui.global::<State>().set_route("reader".into());
        app.sync_interaction_state();
        app.sync_presented_reader_dithering();
        assert!(ui.global::<State>().get_hardware_dither_available());
        assert!(app.platform.reader_hardware_y4_requested());
        let generation = app.reader_generation;
        let reader_jobs = app.reader_image_jobs_started;

        // The native rejection happens after publication, independently of
        // opt-in timing observers. The next ordinary poll refreshes only state.
        app.platform.set_test_hardware_y4_available(false);
        assert!(ui.global::<State>().get_hardware_dither_available());
        app.poll();
        assert!(!ui.global::<State>().get_hardware_dither_available());
        assert!(!ui.global::<State>().get_dither_pending());
        assert!(app.platform.reader_hardware_y4_requested());
        assert!(app.displayed_reader.as_ref().unwrap().hardware_dither);
        assert_eq!(app.reader_generation, generation);
        assert_eq!(app.reader_image_jobs_started, reader_jobs);
        assert!(app.reader_inflight.is_none());
        let retained = ui.global::<State>().get_reader_image().to_rgb8().unwrap();
        assert_eq!(retained.as_bytes(), &[42, 42, 42, 216, 216, 216]);
    }

    #[test]
    fn invalidating_reader_work_cancels_the_private_render_and_keeps_worker_ownership() {
        let mut fixture = DemoRouteFixture::new("settings", DeviceProfile::Pw6, false);
        let app = &mut fixture.app;
        let cancellation = imaging::DecodeCancellation::new();
        app.reader_inflight = Some(42);
        app.reader_image_cancel = Some(cancellation.clone());
        app.pending.insert(42);
        app.invalidate_reader_work();
        assert!(cancellation.is_cancelled());
        assert_eq!(app.reader_inflight, Some(42));
        assert!(!app.pending.contains(&42));
        assert!(!app.reader_dirty);
        assert!(app.reader_image_cancel.is_none());
    }

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

    #[derive(Debug, Clone, Copy)]
    enum ReaderSourceKind {
        Managed,
        External,
    }

    struct CachedReaderFixture {
        directory: tempfile::TempDir,
        kind: ReaderSourceKind,
        page: Page,
        original: PathBuf,
        processed: PathBuf,
        pixels: Arc<GrayImage>,
        source_proof: crate::render_cache::SourceProof,
        preferences: Preferences,
        viewport: (u32, u32),
        options: ImageOptions,
    }

    impl CachedReaderFixture {
        fn new(kind: ReaderSourceKind) -> Self {
            let directory = tempfile::tempdir().unwrap();
            let cache = crate::cache::CacheManager::open(directory.path()).unwrap();
            let mut page = Page {
                url: "https://example.test/reader-cache.png".into(),
                ..Page::default()
            };
            page.headers
                .insert("X-Reader-Identity".into(), "first".into());
            let source =
                GrayImage::from_fn(32, 48, |x, y| image::Luma([((x * 13 + y * 7) % 239) as u8]));
            let original = match kind {
                ReaderSourceKind::Managed => {
                    let write = cache.begin_raw(&page, None, 4_096).unwrap();
                    source
                        .save_with_format(write.temp_path(), image::ImageFormat::Png)
                        .unwrap();
                    write.commit().unwrap()
                }
                ReaderSourceKind::External => {
                    let path = directory.path().join("source.png");
                    source
                        .save_with_format(&path, image::ImageFormat::Png)
                        .unwrap();
                    path
                }
            };
            page.local_path = Some(original.clone());
            let preferences = Preferences {
                crop_borders: false,
                dither: false,
                spread: false,
                split_wide: false,
                resize_downloads: false,
                grayscale_downloads: false,
                ..Preferences::default()
            };
            let viewport = (8, 12);
            let options = reader_image_options(&preferences, viewport, 0, 1);
            let rendered = render_reader_page(
                &cache,
                &page,
                &original,
                &options,
                &preferences,
                viewport,
                false,
                0,
                1.0,
                0,
                None,
            )
            .unwrap();
            let source_proof = rendered.rendered.source_proof.clone().unwrap();
            let pixels = rendered.rendered.image;
            // A legacy derivative is deliberately independent of the Reader's
            // in-memory Raster. Corrupting it must never overwrite the source.
            let processed =
                crate::render_cache::render(&cache, &page, &original, &options, false, 0, 1.0)
                    .unwrap()
                    .cache_path;
            assert_ne!(processed, original);
            crate::render_cache::clear_memory(&cache);
            // No cache manager or lease survives setup. Reopening the fixture
            // therefore starts with cold checksum and source-validation state.
            drop(cache);
            Self {
                directory,
                kind,
                page,
                original,
                processed,
                pixels,
                source_proof,
                preferences,
                viewport,
                options,
            }
        }

        fn manager(&self) -> SourceManager {
            SourceManager::new(self.directory.path(), &[]).unwrap()
        }

        fn render(
            &self,
            manager: &SourceManager,
            page: &Page,
        ) -> Result<(CachedPage, super::super::wide_pages::WidePage)> {
            let cached = self.candidate(manager, page)?;
            let rendered = self.render_candidate(manager, page, &cached)?;
            Ok((cached, rendered))
        }

        fn candidate(&self, manager: &SourceManager, page: &Page) -> Result<CachedPage> {
            cached_page(
                manager,
                page,
                "reader-fixture",
                0,
                true,
                FetchPriority::Interactive,
            )
        }

        fn render_candidate(
            &self,
            manager: &SourceManager,
            page: &Page,
            cached: &CachedPage,
        ) -> Result<super::super::wide_pages::WidePage> {
            render_reader_page(
                manager.cache(),
                page,
                &cached.path,
                &self.options,
                &self.preferences,
                self.viewport,
                false,
                0,
                1.0,
                0,
                None,
            )
        }

        fn frame(&self, page: &Page) -> PrefetchFrame {
            PrefetchFrame {
                index: 0,
                page: page.clone(),
                options: self.options.clone(),
                vertical: false,
                offset: 0,
                zoom: 1.0,
                half: 0,
            }
        }

        fn prefetch(
            &self,
            manager: &SourceManager,
            frames: Vec<PrefetchFrame>,
        ) -> PrefetchedChapter {
            prefetch_chapter_frames(
                manager,
                Chapter {
                    id: "reader-chapter".into(),
                    book_id: "reader-book".into(),
                    source_id: "reader-fixture".into(),
                    remote_id: "reader-chapter".into(),
                    page_count: 1,
                    ..Chapter::default()
                },
                None,
                frames,
                true,
                &self.preferences,
                self.viewport,
                &imaging::DecodeCancellation::new(),
                None,
            )
            .unwrap()
        }

        fn replace_source(&self, bytes: &[u8]) {
            match self.kind {
                ReaderSourceKind::Managed => {
                    let cache = crate::cache::CacheManager::open(self.directory.path()).unwrap();
                    let write = cache
                        .begin_raw(&self.page, None, bytes.len() as u64)
                        .unwrap();
                    fs::write(write.temp_path(), bytes).unwrap();
                    write.commit().unwrap();
                }
                ReaderSourceKind::External => fs::write(&self.original, bytes).unwrap(),
            }
        }

        fn key(&self) -> crate::cache::RenderKey {
            let checksum = format!("{:x}", Sha256::digest(fs::read(&self.original).unwrap()));
            crate::cache::RenderKey {
                source_key: format!(
                    "{}:{checksum}",
                    crate::cache::CacheManager::page_key(&self.page)
                ),
                width: self.options.width,
                height: self.options.height,
                crop_borders: self.options.crop_borders,
                dither: self.options.dither,
                hardware_dither: self.options.hardware_dither,
                contrast: self.options.contrast,
                vertical: false,
                offset: 0,
                zoom_milli: 1_000,
            }
        }
    }

    #[test]
    fn reader_raster_cold_reopen_checks_source_hash_without_duplicate_codec_validation() {
        for kind in [ReaderSourceKind::Managed, ReaderSourceKind::External] {
            let fixture = CachedReaderFixture::new(kind);
            let manager = fixture.manager();
            let bytes = fs::read(&fixture.original).unwrap();
            let checksum = format!("{:x}", Sha256::digest(&bytes));
            let processed_bytes = fs::read(&fixture.processed).unwrap();
            let before = manager.cache().stats();
            let cached = fixture.candidate(&manager, &fixture.page).unwrap();
            assert_eq!(
                manager.cache().stats().checksum_bytes,
                before.checksum_bytes
            );
            let rendered = fixture
                .render_candidate(&manager, &fixture.page, &cached)
                .unwrap();
            assert!(!rendered.rendered.cache_hit && !rendered.rendered.memory_hit);
            assert_eq!(rendered.rendered.cache_path, fixture.original);
            assert_eq!(rendered.rendered.image, fixture.pixels);
            assert_eq!(cached.index, 0);
            assert_eq!(cached.identity, cached_page_identity(&fixture.page, 0));
            let after = manager.cache().stats();
            assert_eq!(after.external_validations - before.external_validations, 0);
            assert_eq!(
                after.checksum_bytes - before.checksum_bytes,
                bytes.len() as u64
                    * if matches!(kind, ReaderSourceKind::External) {
                        2
                    } else {
                        1
                    },
                "A cold Raster verifies canonical source bytes without reading the legacy PNG: {kind:?}"
            );
            assert_eq!(after.commits, before.commits);
            assert_eq!(fs::read(&fixture.processed).unwrap(), processed_bytes);
            let warm = fixture
                .render_candidate(&manager, &fixture.page, &cached)
                .unwrap();
            assert!(warm.rendered.cache_hit && warm.rendered.memory_hit);
            assert!(Arc::ptr_eq(&rendered.rendered.image, &warm.rendered.image));
            let warm_hash_bytes = manager.cache().stats().checksum_bytes - after.checksum_bytes;
            assert_eq!(
                warm_hash_bytes,
                if matches!(kind, ReaderSourceKind::External) {
                    bytes.len() as u64
                } else {
                    0
                },
                "Mutable external source bytes must be checked even on a resident hit"
            );
            assert_eq!(
                manager.cache().fingerprint(&fixture.original).unwrap(),
                checksum
            );
        }
    }

    #[test]
    fn reader_cache_external_source_replacement_changes_the_render_key() {
        let fixture = CachedReaderFixture::new(ReaderSourceKind::External);
        let original_identity = crate::cache::CacheManager::page_key(&fixture.page);
        let manager = fixture.manager();
        let (_, previous) = fixture.render(&manager, &fixture.page).unwrap();
        let previous_proof = previous.rendered.source_proof.as_ref().unwrap();
        assert!(previous_proof.verify(manager.cache()).unwrap());
        GrayImage::from_pixel(32, 48, image::Luma([0]))
            .save_with_format(&fixture.original, image::ImageFormat::Png)
            .unwrap();
        assert_eq!(
            crate::cache::CacheManager::page_key(&fixture.page),
            original_identity
        );
        assert!(!previous_proof.verify(manager.cache()).unwrap());
        let (_, rendered) = fixture.render(&manager, &fixture.page).unwrap();
        assert!(!rendered.rendered.cache_hit);
        assert_eq!(rendered.rendered.cache_path, fixture.original);
        assert!(!Arc::ptr_eq(
            &rendered.rendered.image,
            &previous.rendered.image
        ));
        assert!(
            rendered
                .rendered
                .source_proof
                .as_ref()
                .unwrap()
                .verify(manager.cache())
                .unwrap()
        );
        assert_ne!(rendered.rendered.image, fixture.pixels);
        assert!(
            rendered
                .rendered
                .image
                .as_raw()
                .iter()
                .all(|pixel| *pixel == 0)
        );
    }

    #[test]
    fn reader_cache_invalid_originals_never_publish_prefetch_paths() {
        for kind in [ReaderSourceKind::Managed, ReaderSourceKind::External] {
            let fixture = CachedReaderFixture::new(kind);
            fixture.replace_source(b"A checksummed artifact that is not an image");
            let manager = fixture.manager();
            let mut page = fixture.page.clone();
            if matches!(kind, ReaderSourceKind::Managed) {
                // This covers a raw cache hit without an existing manifest path.
                page.local_path = None;
            }
            let candidate = fixture.candidate(&manager, &page).unwrap();
            assert!(
                fixture
                    .render_candidate(&manager, &page, &candidate)
                    .is_err()
            );
            let prefetched = fixture.prefetch(&manager, vec![fixture.frame(&page)]);
            assert!(prefetched.paths.is_empty());
            assert!(fixture.processed.is_file());
        }
    }

    #[test]
    fn reader_raster_ignores_corrupt_pngs_while_persistent_consumers_rebuild() {
        for matching_checksum in [false, true] {
            let fixture = CachedReaderFixture::new(ReaderSourceKind::Managed);
            if matching_checksum {
                let cache = crate::cache::CacheManager::open(fixture.directory.path()).unwrap();
                let write = cache.begin_processed(&fixture.key(), 64).unwrap();
                fs::write(
                    write.temp_path(),
                    b"An indexed processed artifact with invalid PNG data",
                )
                .unwrap();
                write.commit().unwrap();
            } else {
                fs::write(
                    &fixture.processed,
                    b"A processed file with a changed checksum",
                )
                .unwrap();
            }
            let manager = fixture.manager();
            let original_bytes = fs::read(&fixture.original).unwrap();
            let corrupt_bytes = fs::read(&fixture.processed).unwrap();
            let before = manager.cache().stats();
            let candidate = fixture.candidate(&manager, &fixture.page).unwrap();
            let rendered = fixture
                .render_candidate(&manager, &fixture.page, &candidate)
                .unwrap();
            assert!(!rendered.rendered.cache_hit);
            assert_eq!(rendered.rendered.image, fixture.pixels);
            assert_eq!(rendered.rendered.cache_path, fixture.original);
            assert_eq!(manager.cache().stats().commits, before.commits);
            assert_eq!(fs::read(&fixture.processed).unwrap(), corrupt_bytes);
            assert_eq!(fs::read(&fixture.original).unwrap(), original_bytes);

            let persistent = crate::render_cache::render(
                manager.cache(),
                &fixture.page,
                &fixture.original,
                &fixture.options,
                false,
                0,
                1.0,
            )
            .unwrap();
            assert!(!persistent.cache_hit);
            assert_eq!(persistent.cache_path, fixture.processed);
            assert_eq!(
                *persistent.image,
                imaging::decode_page(&original_bytes, &fixture.options).unwrap()
            );
            assert!(manager.cache().stats().corruptions > before.corruptions);
            assert_eq!(fs::read(&fixture.original).unwrap(), original_bytes);
        }
    }

    #[test]
    fn reader_cache_managed_checksum_mismatch_cannot_reuse_old_pixels() {
        let fixture = CachedReaderFixture::new(ReaderSourceKind::Managed);
        fs::write(
            &fixture.original,
            b"A raw file changed outside the cache index",
        )
        .unwrap();
        let manager = fixture.manager();
        let candidate = fixture.candidate(&manager, &fixture.page).unwrap();
        assert!(
            fixture
                .render_candidate(&manager, &fixture.page, &candidate)
                .is_err()
        );
        let prefetched = fixture.prefetch(&manager, vec![fixture.frame(&fixture.page)]);
        assert!(prefetched.paths.is_empty());
        assert!(manager.cache().stats().corruptions > 0);
        assert!(fixture.processed.is_file());
    }

    #[test]
    fn reader_cache_missing_source_does_not_restore_its_processed_png() {
        for kind in [ReaderSourceKind::Managed, ReaderSourceKind::External] {
            let fixture = CachedReaderFixture::new(kind);
            fs::remove_file(&fixture.original).unwrap();
            let manager = fixture.manager();
            assert!(!fixture.source_proof.verify(manager.cache()).unwrap());
            assert!(fixture.candidate(&manager, &fixture.page).is_err());
            let prefetched = fixture.prefetch(&manager, vec![fixture.frame(&fixture.page)]);
            assert!(prefetched.paths.is_empty());
            assert!(fixture.processed.is_file());
        }
    }

    #[test]
    fn reader_cache_preserves_page_identity_when_headers_change() {
        let fixture = CachedReaderFixture::new(ReaderSourceKind::Managed);
        let mut page = fixture.page.clone();
        page.headers
            .insert("X-Reader-Identity".into(), "second".into());
        let manager = fixture.manager();
        let (_, previous) = fixture.render(&manager, &fixture.page).unwrap();
        let (cached, rendered) = fixture.render(&manager, &page).unwrap();
        assert_ne!(cached.identity, cached_page_identity(&fixture.page, 0));
        assert_eq!(cached.identity, cached_page_identity(&page, 0));
        assert_eq!(rendered.rendered.cache_path, fixture.original);
        assert!(!Arc::ptr_eq(
            &rendered.rendered.image,
            &previous.rendered.image
        ));
        assert!(!fixture.source_proof.matches_page(&page));
        let proof = rendered.rendered.source_proof.as_ref().unwrap();
        assert!(proof.matches_page(&page));
        assert!(!proof.matches_page(&fixture.page));
        assert!(proof.verify(manager.cache()).unwrap());
        assert_eq!(rendered.rendered.image, fixture.pixels);
        assert!(!rendered.rendered.cache_hit);
    }

    #[test]
    fn reader_cache_empty_and_oversized_sources_fail_before_hashing() {
        for length in [0, imaging::MAX_PAGE_BYTES + 1] {
            let fixture = CachedReaderFixture::new(ReaderSourceKind::External);
            fs::OpenOptions::new()
                .write(true)
                .open(&fixture.original)
                .unwrap()
                .set_len(length)
                .unwrap();
            let manager = fixture.manager();
            let before = manager.cache().stats().checksum_bytes;
            assert!(fixture.candidate(&manager, &fixture.page).is_err());
            assert_eq!(manager.cache().stats().checksum_bytes, before);
            let prefetched = fixture.prefetch(&manager, vec![fixture.frame(&fixture.page)]);
            assert!(prefetched.paths.is_empty());
        }
    }

    #[test]
    fn reader_cache_prefetch_only_publishes_successful_source_pages() {
        let fixture = CachedReaderFixture::new(ReaderSourceKind::Managed);
        let manager = fixture.manager();
        let mut invalid = fixture.frame(&fixture.page);
        invalid.options.width = 0;
        let failed = fixture.prefetch(&manager, vec![invalid]);
        assert!(failed.paths.is_empty());
        let mut invalid = fixture.frame(&fixture.page);
        invalid.options.width = 0;
        let mixed = fixture.prefetch(&manager, vec![invalid, fixture.frame(&fixture.page)]);
        assert_eq!(mixed.paths.len(), 1);
        assert_eq!(mixed.paths[0].index, 0);
        assert_eq!(
            mixed.paths[0].identity,
            cached_page_identity(&fixture.page, 0)
        );
        assert_eq!(mixed.paths[0].path, fixture.original);
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
            hardware_dither: options.hardware_dither,
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
    fn named_fit_keeps_original_geometry_instead_of_resized_download_geometry() {
        let fixture = OfflineReaderFixture::new();
        let derivative = fixture.prepare_download();
        let options =
            reader_image_options_for_mode(&fixture.preferences, fixture.viewport, 0, 1, true);
        let from_original = crate::render_cache::render_raster(
            &fixture.cache,
            &fixture.page,
            &fixture.original,
            &options,
            false,
            0,
            1.0,
        )
        .unwrap();
        let from_derivative = crate::render_cache::render_raster(
            &fixture.cache,
            &fixture.page,
            &derivative,
            &options,
            false,
            0,
            1.0,
        )
        .unwrap();
        assert_ne!(from_original.image, from_derivative.image);
        let actual = render_reader_page(
            &fixture.cache,
            &fixture.page,
            &fixture.original,
            &options,
            &fixture.preferences,
            fixture.viewport,
            false,
            0,
            1.0,
            0,
            None,
        )
        .unwrap();
        let expected = imaging::decode_fitted_reader_raster(
            &fs::read(&fixture.original).unwrap(),
            &options,
            imaging::ReaderFit::Page,
        )
        .unwrap();
        assert_eq!(*actual.rendered.image, expected);
        assert_eq!(
            actual.rendered.image.dimensions(),
            (options.width, options.height)
        );
        assert!(
            actual
                .rendered
                .image
                .as_raw()
                .iter()
                .any(|sample| sample % 17 != 0)
        );
        assert_eq!(
            actual.rendered.source_proof.unwrap().source_path(),
            fs::canonicalize(&fixture.original).unwrap()
        );
    }

    #[test]
    fn prepared_reader_prefetch_and_foreground_share_pixels_and_native_geometry() {
        let fixture = OfflineReaderFixture::new();
        let original_bytes = fs::read(&fixture.original).unwrap();
        let prepared = fixture.prepare_download();
        let commits_before_reader = fixture.cache.stats().commits;
        let cancellation = imaging::DecodeCancellation::new();
        let options = reader_image_options(&fixture.preferences, fixture.viewport, 0, 1);
        let prefetched = render_reader_page_with_next(
            &fixture.cache,
            &fixture.page,
            &fixture.original,
            &options,
            &fixture.preferences,
            fixture.viewport,
            false,
            0,
            1.0,
            0,
            Some(&cancellation),
            true,
        )
        .unwrap();
        assert!(
            prefetched
                .rendered
                .source_proof
                .as_ref()
                .unwrap()
                .verify(&fixture.cache)
                .unwrap()
        );
        assert!(prefetched.rendered.retained_raster.is_some());
        let foreground = fixture.render(&fixture.preferences, fixture.viewport, false, 1.0, None);
        assert_eq!(prefetched.rendered.image, foreground.rendered.image);
        assert_eq!(
            foreground.rendered.image.dimensions(),
            (options.width, options.height)
        );
        assert_eq!(
            foreground
                .rendered
                .source_proof
                .as_ref()
                .unwrap()
                .source_path(),
            fs::canonicalize(&fixture.original).unwrap()
        );
        assert_ne!(foreground.rendered.cache_path, prepared);
        let rotated = fixture.render(&fixture.preferences, (12, 8), false, 1.0, None);
        assert_eq!(rotated.rendered.image.dimensions(), (24, 16));
        assert_eq!(fixture.cache.stats().commits, commits_before_reader);
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
        let original = crate::render_cache::render_raster(
            &fixture.cache,
            &fixture.page,
            &fixture.original,
            &options,
            false,
            0,
            1.0,
        )
        .unwrap();
        let different_geometry = crate::render_cache::render_raster(
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
            let expected = if !vertical {
                crate::render_cache::render_fitted_reader(
                    &fixture.cache,
                    &fixture.page,
                    &fixture.original,
                    &options,
                    imaging::ReaderFit::Page,
                    None,
                    false,
                )
                .unwrap()
            } else {
                crate::render_cache::render_raster(
                    &fixture.cache,
                    &fixture.page,
                    &fixture.original,
                    &options,
                    vertical,
                    0,
                    1.0,
                )
                .unwrap()
            };
            let actual =
                fixture.render(&fixture.preferences, fixture.viewport, vertical, 1.0, None);
            assert_eq!(actual.rendered.cache_path, expected.cache_path);
            assert_eq!(actual.rendered.image, expected.image);
            assert_eq!(actual.rendered.scaled_height, expected.scaled_height);
            assert_eq!(actual.rendered.viewport_height, expected.viewport_height);
        }
    }
}
