use super::*;
use crate::{domain::ReaderSession, platform::DeviceProfile};
use image::{ImageFormat, Luma};

fn png(width: u32, height: u32, rgba: &[u8]) -> Result<Vec<u8>> {
    let mut encoded = Vec::new();
    image::codecs::png::PngEncoder::new(&mut encoded).write_image(
        rgba,
        width,
        height,
        image::ExtendedColorType::Rgba8,
    )?;
    Ok(encoded)
}

fn book() -> Book {
    Book {
        id: "sleep:book".into(),
        source_id: "local".into(),
        title: "Synthetic Comic".into(),
        author: "Synthetic Author".into(),
        cover: "https://example.test/synthetic-sleep-cover.png".into(),
        ..Book::default()
    }
}

fn select_book(app: &mut App, book: &Book) {
    app.route = "reader".into();
    app.current_book_id = Some(book.id.clone());
    app.displayed_reader = Some(DisplayedReader {
        session: ReaderSession {
            book_id: book.id.clone(),
            chapter_id: "sleep:chapter".into(),
            page: 7,
            total: 12,
            ..ReaderSession::default()
        },
        preferences: app.effective_reader_preferences(),
        first_path: PathBuf::from("retained-reader.png"),
        second_path: None,
    });
    app.observe_sleep_book(Some(book.clone()));
}

struct HeadlessPlatform {
    window: Rc<slint::platform::software_renderer::MinimalSoftwareWindow>,
}

impl slint::platform::Platform for HeadlessPlatform {
    fn create_window_adapter(
        &self,
    ) -> std::result::Result<Rc<dyn slint::platform::WindowAdapter>, slint::PlatformError> {
        Ok(self.window.clone())
    }
}

fn with_ui(work: impl FnOnce(&AppWindow) -> Result<()> + Send + 'static) -> Result<()> {
    std::thread::spawn(move || {
        let window = slint::platform::software_renderer::MinimalSoftwareWindow::new(
            slint::platform::software_renderer::RepaintBufferType::ReusedBuffer,
        );
        slint::platform::set_platform(Box::new(HeadlessPlatform { window }))?;
        let ui = AppWindow::new()?;
        work(&ui)
    })
    .join()
    .map_err(|_| anyhow::anyhow!("The isolated sleep-screen UI check panicked"))?
}

#[test]
fn complete_covers_preserve_aspect_ratio_without_enlarging_small_sources() -> Result<()> {
    for (width, height, expected) in [(2560, 1280, (1536, 768)), (1280, 2560, (1024, 2048))] {
        let mut pixels = Vec::with_capacity((width * height * 4) as usize);
        for y in 0..height {
            for x in 0..width {
                let gray = match (x < width / 2, y < height / 2) {
                    (true, true) => 0,
                    (false, true) => 85,
                    (true, false) => 170,
                    (false, false) => 255,
                };
                pixels.extend_from_slice(&[gray, gray, gray, 255]);
            }
        }
        let prepared = decode_cover(&png(width, height, &pixels)?)?;
        assert_eq!(prepared.dimensions(), expected);
        assert_eq!(
            u64::from(prepared.width()) * u64::from(height),
            u64::from(prepared.height()) * u64::from(width)
        );
        let last_row = ((prepared.height() - 1) * prepared.width()) as usize;
        let luma = prepared.as_raw();
        assert_eq!(
            [
                luma[0],
                luma[(prepared.width() - 1) as usize],
                luma[last_row],
                *luma.last().unwrap()
            ],
            [0, 85, 170, 255]
        );
    }
    let small = decode_cover(&png(16, 24, &[68, 68, 68, 255].repeat(16 * 24))?)?;
    assert_eq!(small.dimensions(), (16, 24));
    Ok(())
}

#[test]
fn transparency_uses_white_paper_and_exact_sixteen_level_grayscale() -> Result<()> {
    let rgba = [
        0, 0, 0, 0, 0, 0, 0, 255, 255, 255, 255, 255, 0, 0, 0, 128, 255, 0, 0, 255, 0, 255, 0, 255,
        0, 0, 255, 255,
    ];
    let prepared = decode_cover(&png(7, 1, &rgba)?)?;
    assert_eq!(
        prepared.as_raw().as_slice(),
        [255, 0, 255, 119, 51, 187, 17]
    );
    let pixels = PreparedCover::new(&prepared);
    assert!(
        pixels
            .pixels
            .as_bytes()
            .chunks_exact(3)
            .all(|pixel| pixel[0] == pixel[1] && pixel[1] == pixel[2] && pixel[0] % 17 == 0)
    );
    Ok(())
}

#[test]
fn encoded_and_decoded_source_budgets_are_enforced() -> Result<()> {
    assert!(decode_cover(&vec![0; MAX_ENCODED_BYTES + 1]).is_err());
    assert!(decode_cover(&png(4097, 1, &[0, 0, 0, 255].repeat(4097))?).is_err());
    assert!(decode_cover(&png(1, 4097, &[0, 0, 0, 255].repeat(4097))?).is_err());
    assert!(decode_cover(b"invalid encoded image").is_err());
    Ok(())
}

#[test]
fn identity_separates_sources_revisions_books_and_cover_urls() {
    let original = book();
    let first = CoverIdentity::new(&original, 1).unwrap();
    assert_ne!(first, CoverIdentity::new(&original, 2).unwrap());
    for field in ["source", "book", "cover"] {
        let mut changed = original.clone();
        match field {
            "source" => changed.source_id.push_str("-other"),
            "book" => changed.id.push_str("-other"),
            _ => changed.cover.push_str("?replacement=1"),
        }
        assert_ne!(first, CoverIdentity::new(&changed, 1).unwrap());
    }
    let mut changed = original;
    changed.cover = "u".repeat(8193);
    assert!(CoverIdentity::new(&changed, 1).is_none());
    assert_eq!(first.cover_digest.len(), 64);
    assert!(!first.cover_digest.contains("example.test"));
    assert_eq!(
        bounded_text(&"\u{00e9}".repeat(300), 256).chars().count(),
        256
    );
    assert_eq!(
        bounded_text(&"\u{1f4d6}".repeat(200), 128).chars().count(),
        128
    );
}

#[test]
fn replacing_a_candidate_rejects_late_pixels_and_retains_only_one_worker_slot() {
    let mut fixture =
        super::super::tests::DemoRouteFixture::new("settings", DeviceProfile::Pw5, false);
    let original = book();
    select_book(&mut fixture.app, &original);
    let identity = fixture.app.sleep_screen.identity.clone().unwrap();
    let cancellation = imaging::DecodeCancellation::new();
    fixture.app.sleep_screen.job = Some(CoverJob {
        id: 41,
        identity: identity.clone(),
        cancellation: cancellation.clone(),
    });
    let mut replacement = original;
    replacement.id.push_str("-replacement");
    replacement.title = "Current candidate".into();
    select_book(&mut fixture.app, &replacement);
    assert!(cancellation.is_cancelled());
    assert_eq!(fixture.app.sleep_screen.job.as_ref().unwrap().id, 41);
    fixture.app.accept_sleep_cover(
        identity,
        PreparedCover::new(&GrayImage::from_pixel(2, 3, Luma([68]))),
    );
    assert!(!fixture.app.sleep_screen.full_resolution);
    assert!(fixture.app.sleep_screen.cover.is_none());
    assert_eq!(fixture.app.sleep_screen.title.as_str(), "Current candidate");
    fixture
        .app
        .finish_sleep_cover_job(41, Some(&anyhow::anyhow!("An old candidate failed")));
    assert!(fixture.app.sleep_screen.job.is_none());
    assert!(fixture.app.sleep_screen.retry_at.is_none());
}

#[test]
fn source_retirement_clears_prepared_memory_and_late_results() {
    let mut fixture =
        super::super::tests::DemoRouteFixture::new("settings", DeviceProfile::Pw5, false);
    let original = book();
    select_book(&mut fixture.app, &original);
    let identity = fixture.app.sleep_screen.identity.clone().unwrap();
    fixture.app.accept_sleep_cover(
        identity.clone(),
        PreparedCover::new(&GrayImage::from_pixel(2, 3, Luma([68]))),
    );
    assert!(fixture.app.sleep_screen.full_resolution);
    fixture.app.source_images_changed();
    assert!(fixture.app.sleep_screen.identity.is_none());
    assert!(fixture.app.sleep_screen.cover.is_none());
    fixture.app.accept_sleep_cover(
        identity,
        PreparedCover::new(&GrayImage::from_pixel(2, 3, Luma([136]))),
    );
    assert!(fixture.app.sleep_screen.cover.is_none());
}

#[test]
fn the_sleep_entry_projects_prepared_memory_before_any_checkpoint() -> Result<()> {
    with_ui(|ui| {
        let mut fixture =
            super::super::tests::DemoRouteFixture::new("settings", DeviceProfile::Pw5, false);
        fixture.app.ui = ui.as_weak();
        let selected = book();
        select_book(&mut fixture.app, &selected);
        let identity = fixture.app.sleep_screen.identity.clone().unwrap();
        fixture.app.accept_sleep_cover(
            identity,
            PreparedCover::new(&GrayImage::from_pixel(16, 24, Luma([68]))),
        );
        let reader = Image::from_rgb8(SharedPixelBuffer::<Rgb8Pixel>::new(2, 3));
        ui.global::<State>().set_reader_image(reader);
        let checkpoint = NavigationSession {
            route: "history".into(),
            page: 9,
            ..NavigationSession::default()
        };
        fixture.app.store.save_session(&checkpoint)?;
        let sequence = fixture.app.next_job;
        let jobs = fixture.app.active_jobs.len();
        assert!(fixture.app.begin_sleep_transition(true)?);
        assert!(fixture.app.suspended);
        assert!(fixture.app.network_control.is_suspended());
        assert!(!fixture.app.suspend_checkpoint_ready && !fixture.app.suspend_checkpoint_started);
        assert!(!fixture.app.downloads.suspend_ready());
        assert_eq!(fixture.app.store.session()?, Some(checkpoint));
        assert_eq!(fixture.app.next_job, sequence);
        assert_eq!(fixture.app.active_jobs.len(), jobs);
        fixture.app.prepare_sleep_screen()?;
        let model = ui.global::<SleepScreenModel>();
        assert!(model.get_active() && model.get_has_cover());
        let size = model.get_cover().size();
        assert_eq!((size.width, size.height), (16, 24));
        assert_eq!(model.get_title().as_str(), "Synthetic Comic");
        assert_eq!(model.get_author().as_str(), "Synthetic Author");
        assert_eq!(
            fixture.app.displayed_reader.as_ref().unwrap().session.page,
            7
        );
        fixture.app.hide_sleep_cover();
        fixture.app.hide_sleep_cover();
        assert!(!model.get_active() && !model.get_has_cover());
        assert!(model.get_title().is_empty() && model.get_author().is_empty());
        let retained = ui.global::<State>().get_reader_image().size();
        assert_eq!((retained.width, retained.height), (2, 3));
        Ok(())
    })
}

#[test]
fn text_fallback_and_non_reader_neutral_screen_never_use_residual_reader_cover() -> Result<()> {
    with_ui(|ui| {
        let mut fixture =
            super::super::tests::DemoRouteFixture::new("settings", DeviceProfile::Pw5, false);
        fixture.app.ui = ui.as_weak();
        let selected = book();
        select_book(&mut fixture.app, &selected);
        fixture.app.show_sleep_cover()?;
        let model = ui.global::<SleepScreenModel>();
        assert!(model.get_active() && !model.get_has_cover());
        assert_eq!(model.get_title().as_str(), "Synthetic Comic");
        assert_eq!(model.get_author().as_str(), "Synthetic Author");
        let identity = fixture.app.sleep_screen.identity.clone().unwrap();
        fixture.app.accept_sleep_cover(
            identity,
            PreparedCover::new(&GrayImage::from_pixel(2, 3, Luma([68]))),
        );
        fixture.app.route = "library".into();
        assert!(fixture.app.displayed_reader.is_some());
        fixture.app.show_sleep_cover()?;
        assert!(!model.get_has_cover());
        assert_eq!(model.get_title().as_str(), "Aidoku");
        assert_eq!(model.get_author().as_str(), tr("sleep_neutral"));
        fixture.app.observe_sleep_book(None);
        assert!(fixture.app.sleep_screen.cover.is_none());
        fixture.app.prefs.screensaver_mode = "system".into();
        assert!(!fixture.app.sleep_cover_enabled());
        assert!(!fixture.app.begin_sleep_transition(true)?);
        assert!(!model.get_active());
        Ok(())
    })
}

#[test]
fn stock_checkpoint_runs_only_after_the_memory_gate_and_is_idempotent() -> Result<()> {
    let mut fixture =
        super::super::tests::DemoRouteFixture::new("settings", DeviceProfile::Pw5, false);
    let checkpoint = NavigationSession {
        route: "history".into(),
        page: 9,
        ..NavigationSession::default()
    };
    fixture.app.store.save_session(&checkpoint)?;
    assert!(!fixture.app.begin_sleep_transition(false)?);
    assert_eq!(fixture.app.store.session()?, Some(checkpoint));
    assert!(!fixture.app.suspend_checkpoint_ready && !fixture.app.suspend_checkpoint_started);
    let generation = fixture.app.lifecycle_generation;
    fixture.app.complete_sleep_checkpoint()?;
    assert!(fixture.app.suspend_checkpoint_ready && fixture.app.suspend_checkpoint_started);
    assert_eq!(fixture.app.store.session()?.unwrap().route, "settings");
    fixture.app.complete_sleep_checkpoint()?;
    assert_eq!(fixture.app.lifecycle_generation, generation);
    Ok(())
}

#[test]
fn managed_derivatives_refill_after_corruption_and_bind_actual_source_bytes() -> Result<()> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("source-cover.png");
    let encode = |gray| -> Result<Vec<u8>> {
        let mut bytes = Cursor::new(Vec::new());
        GrayImage::from_pixel(16, 24, Luma([gray])).write_to(&mut bytes, ImageFormat::Png)?;
        Ok(bytes.into_inner())
    };
    fs::write(&path, encode(68)?)?;
    let selected = Book {
        cover: path.to_string_lossy().into_owned(),
        ..book()
    };
    let manager = Arc::new(SourceManager::new(directory.path(), &[])?);
    let identity = CoverIdentity::new(&selected, 0).unwrap();
    let run = || {
        prepare_cover(
            manager.clone(),
            identity.clone(),
            selected.clone(),
            None,
            false,
            imaging::DecodeCancellation::new(),
        )
    };
    let JobResult::SleepCover { image, .. } = run()? else {
        panic!("A sleep image must be prepared")
    };
    assert!(image.pixels.as_bytes().iter().all(|gray| *gray == 68));
    let first = manager
        .cache()
        .storage_artifacts_page(None, 128)?
        .into_iter()
        .find(|artifact| artifact.cover == Some(true))
        .unwrap();
    assert!(first.path.starts_with(directory.path().join("cache")));
    fs::write(&first.path, b"corrupt derivative")?;
    let JobResult::SleepCover { image, .. } = run()? else {
        panic!("A corrupt derivative must refill")
    };
    assert!(image.pixels.as_bytes().iter().all(|gray| *gray == 68));
    fs::write(&path, encode(136)?)?;
    let JobResult::SleepCover { image, .. } = run()? else {
        panic!("Changed source bytes must be prepared")
    };
    assert!(image.pixels.as_bytes().iter().all(|gray| *gray == 136));
    assert!(path.is_file());
    assert_eq!(image.pixels.width(), 16);
    assert_eq!(image.pixels.height(), 24);
    Ok(())
}
