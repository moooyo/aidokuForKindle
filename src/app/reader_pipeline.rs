//! Bounded reader image work and cache-path publication.

use super::*;
use crate::reader::PageLayout;

impl App {
    pub(super) fn request_reader_images(&mut self) -> Result<()> {
        self.request_reader_images_with_refresh(RefreshRequest::Full)
    }

    pub(super) fn request_reader_images_with_refresh(
        &mut self,
        refresh: RefreshRequest,
    ) -> Result<()> {
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
        let options = reader_image_options(
            &effective,
            self.platform.runtime_logical_size(effective.landscape),
            index,
            window.total,
        );
        let offline = self.options.offline || self.options.demo;
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
            let rendered = crate::render_cache::render(
                manager.cache(),
                &request,
                &path,
                &options,
                vertical,
                offset,
                zoom,
            )?;
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
                let rendered = crate::render_cache::render(
                    manager.cache(),
                    &request,
                    &path,
                    &options,
                    false,
                    0,
                    zoom,
                )?;
                (Some(rendered.image), Some(rendered.cache_path))
            } else {
                (None, None)
            };
            Ok(JobResult::ReaderImage {
                chapter_id,
                page,
                offset,
                first,
                second,
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
        let chapter_id = reader.current_chapter().id.clone();
        let source_id = reader.current_chapter().source_id.clone();
        let effective = self.effective_reader_preferences();
        let vertical = is_strip(&effective);
        let current = reader.visible_pages().start().saturating_sub(1) as usize;
        let zoom = prefetch_zoom(&effective, reader.zoom);
        let targets = if vertical {
            strip_prefetch_offsets(
                reader.vertical_offset,
                reader.viewport_height,
                reader.image_height,
            )
        } else {
            Vec::new()
        };
        if vertical && targets.is_empty() {
            return Ok(());
        }
        // The snapshot includes the current allocation and at most two future
        // pages: one row for strips, three for single pages, four for spreads.
        let window = self.store.page_window(
            &chapter_id,
            current,
            prefetch_window_limit(&effective, current),
        )?;
        let (first, pages, options) = prefetch_pages(
            &effective,
            window,
            self.platform.runtime_logical_size(effective.landscape),
        );
        if pages.is_empty() {
            return Ok(());
        }
        let offline = self.options.offline || self.options.demo;
        let id = self.spawn_job_kind(false, JobKind::Prefetch, move |manager| {
            let mut paths = Vec::with_capacity(pages.len());
            if vertical {
                if let Ok(path) = cached_page(
                    &manager,
                    &pages[0],
                    &source_id,
                    current,
                    offline,
                    FetchPriority::Prefetch,
                    &mut paths,
                ) {
                    for target in targets {
                        let _ = crate::render_cache::prefetch(
                            manager.cache(),
                            &pages[0],
                            &path,
                            &options,
                            true,
                            target,
                            zoom,
                        );
                    }
                }
            } else {
                for (relative_index, request) in pages.iter().enumerate() {
                    let index = first + relative_index;
                    match cached_page(
                        &manager,
                        request,
                        &source_id,
                        index,
                        offline,
                        FetchPriority::Prefetch,
                        &mut paths,
                    ) {
                        Ok(path) => {
                            let _ = crate::render_cache::prefetch(
                                manager.cache(),
                                request,
                                &path,
                                &options,
                                false,
                                0,
                                zoom,
                            );
                        }
                        Err(error) => eprintln!("aidoku: read-ahead page {index}: {error:#}"),
                    }
                }
            }
            Ok(JobResult::Prefetched { chapter_id, paths })
        })?;
        self.prefetch_inflight = Some(id);
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
}
