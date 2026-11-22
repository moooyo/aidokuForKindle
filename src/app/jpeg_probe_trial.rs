use super::*;
use crate::native_media::{Availability, probe_helper};
use crate::platform::DeviceProfile;
use crate::ui::Theme;
use anyhow::ensure;
use slint::platform::{
    PointerEventButton, WindowAdapter, WindowEvent,
    software_renderer::{MinimalSoftwareWindow, RepaintBufferType},
};
use std::cell::Cell;

struct TrialDisplay(Rc<MinimalSoftwareWindow>);

impl slint::platform::Platform for TrialDisplay {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, slint::PlatformError> {
        Ok(self.0.clone())
    }
}

fn seed_shelf(directory: &Path) -> Result<()> {
    fs::create_dir(directory)?;
    let store = Store::open(directory.join("library.sqlite3"))?;
    for source in default_sources() {
        store.save_source(&source)?;
    }
    store.save_preferences(&Preferences {
        language: "en".into(),
        ..Default::default()
    })?;
    for index in 1..=6 {
        let cover = directory.join(format!("cover-{index}.png"));
        image::GrayImage::from_pixel(76, 104, image::Luma([index * 30])).save(&cover)?;
        let id = format!("5:local:trial-{index}");
        store.upsert_book(&Book {
            id: id.clone(),
            source_id: "local".into(),
            remote_id: format!("trial-{index}"),
            title: format!("Shelf study volume {index}"),
            author: "Trial author".into(),
            updated_at: 1_700_000_000 + i64::from(index),
            in_library: true,
            cover: cover.to_string_lossy().into_owned(),
            ..Default::default()
        })?;
        store.save_chapters(
            &id,
            &[Chapter {
                id: format!("chapter-{index}"),
                book_id: id.clone(),
                source_id: "local".into(),
                remote_id: format!("chapter-{index}"),
                number: 1.0,
                title: "One chapter".into(),
                page_count: 6,
                ..Default::default()
            }],
        )?;
    }
    Ok(())
}

fn preload_fonts() -> Result<()> {
    use slint::fontique_010::{fontique::Blob, shared_collection};
    let directory = Path::new(env!("OUT_DIR")).join("fonts");
    for name in [
        "NotoSansSC-Regular.otf",
        "NotoSansSC-Medium.otf",
        "NotoSansSC-Bold.otf",
        "NotoSerifSC-Regular.otf",
        "NotoSerifSC-Bold.otf",
        "NotoSerifSC-Black.otf",
        "Literata-Regular.ttf",
        "Literata-SemiBold.ttf",
        "Literata-Bold.ttf",
    ] {
        let bytes = fs::read(directory.join(name))?;
        let registered = shared_collection().register_fonts(Blob::new(Arc::new(bytes)), None);
        ensure!(!registered.is_empty(), "Missing bundled font: {name}");
    }
    Ok(())
}

#[test]
#[ignore = "One isolated sample for the jpeg-probe-admission host_trial.py driver"]
fn render_first_library() -> Result<()> {
    let case = PathBuf::from(std::env::var("AIDOKU_JPEG_TRIAL_CASE")?);
    let helper = PathBuf::from(std::env::var("AIDOKU_JPEG_TRIAL_HELPER")?);
    let arm = std::env::var("AIDOKU_JPEG_TRIAL_ARM")?;
    ensure!(matches!(arm.as_str(), "control" | "candidate"));
    let profile = match std::env::var("AIDOKU_JPEG_TRIAL_PROFILE")?.as_str() {
        "scribe" => DeviceProfile::Scribe,
        "pw6" => DeviceProfile::Pw6,
        _ => bail!("Unknown trial profile"),
    };
    let directory = case.join("data");
    seed_shelf(&directory)?;
    let options = AppOptions {
        data_dir: directory,
        startup_options: Default::default(),
        demo: false,
        offline: true,
        initial_screen: "library".into(),
        landscape: false,
        resume: false,
    };
    let (width, height) = profile.logical_size(false);
    let mut pixels = slint::SharedPixelBuffer::<slint::Rgb8Pixel>::new(width, height);
    let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(TrialDisplay(window.clone())))?;
    preload_fonts()?;
    let probes = Rc::new(Cell::new(0));
    let probe_time = Rc::new(Cell::new(Duration::ZERO));
    let count = probes.clone();
    let duration = probe_time.clone();
    let receipt = case.join("metadata.receipt");
    ensure!(!receipt.exists());
    let start = Instant::now();
    let platform = Rc::new(Platform::new(profile, false)?);
    platform.set_test_jpeg_probe(Rc::new(move || {
        count.set(count.get() + 1);
        let entered = Instant::now();
        let result = probe_helper(&helper);
        duration.set(entered.elapsed());
        result
    }));
    if arm == "control" {
        ensure!(platform.probe_hardware_media() == Availability::Available);
    }
    platform.set_startup_render_priority(true);
    let platform_elapsed = start.elapsed();
    let ui = AppWindow::new()?;
    ui.global::<State>()
        .set_optimized_library_projection(options.startup_options.first_layout_optimized);
    ui.show()?;
    let app = App::attach(&ui, options, platform.clone())?;
    let attached_elapsed = start.elapsed();
    ui.window().request_redraw();
    let mut draw = None;
    window.draw_if_needed(|renderer| {
        let region = renderer.render(pixels.make_mut_slice(), width as usize);
        draw = Some((
            start.elapsed(),
            region.bounding_box_origin(),
            region.bounding_box_size(),
        ));
    });
    let (rendered_elapsed, origin, size) = draw.context("No first Library software draw")?;
    ensure!((origin.x, origin.y, size.width, size.height) == (0, 0, width, height));
    let state = ui.global::<State>();
    ensure!(state.get_library_count() == 6 && state.get_books().row_count() == 6);
    ensure!(
        app.borrow().startup_library_log_projection() == Some((profile.is_scribe(), true, true))
    );
    ensure!(app.borrow().startup_cover_projection() == Some((7, 0, true)));
    let probes_at_frame = probes.get();
    let at_frame = platform.hardware_media_availability();
    let eager = arm == "control";
    ensure!(probes_at_frame == usize::from(eager) && receipt.exists() == eager);
    let expected = if eager {
        Availability::Available
    } else {
        Availability::NotProbed
    };
    ensure!(at_frame == expected);
    let dark = pixels
        .as_slice()
        .iter()
        .filter(|pixel| pixel.r < 96)
        .count();
    ensure!(dark > 1000 && dark < pixels.as_slice().len() * 3 / 4);
    fs::write(case.join("shelf.rgb"), pixels.as_bytes())?;

    let theme = ui.global::<Theme>();
    let position = slint::LogicalPosition::new(
        width as f32 - theme.get_margin() - 1.5 * theme.get_touch(),
        theme.get_status_height() + 0.5 * theme.get_touch(),
    );
    ui.window().try_dispatch_event(WindowEvent::PointerPressed {
        position,
        button: PointerEventButton::Left,
    })?;
    ui.window().try_dispatch_event(WindowEvent::PointerReleased {
        position,
        button: PointerEventButton::Left,
    })?;
    ensure!(
        state.get_modal() == "choices" && state.get_modal_context() == "library-filter",
        "Sort/filter pointer contact did not open the shelf panel"
    );
    app.borrow_mut().dispatch("panel", "close", "")?;
    ensure!(state.get_modal().is_empty() && state.get_route() == "library");
    app.borrow_mut().dispatch("navigation", "open", "4")?;
    ensure!(
        state.get_route() == "settings" && probes.get() == probes_at_frame && receipt.exists() == eager
    );
    for _ in 0..2 {
        app.borrow_mut()
            .dispatch("settings", "open", "hardware-acceleration")?;
        ensure!(state.get_hardware_media_available() && !state.get_hardware_acceleration());
        app.borrow_mut().dispatch("settings", "back", "")?;
    }
    ensure!(probes.get() == 1 && fs::read(&receipt)? == b"metadata-only\n");
    ensure!(crate::native_media::statistics().attempts == 0);
    let row = json!({
        "arm": arm, "profile": profile.id(), "width": width, "height": height,
        "platform_us": platform_elapsed.as_micros(), "attached_us": attached_elapsed.as_micros(),
        "rendered_us": rendered_elapsed.as_micros(), "helper_us": probe_time.get().as_micros(),
        "frame_probes": probes_at_frame, "final_probes": probes.get(), "dark_pixels": dark,
        "frame_reason": at_frame.reason(), "sort_pointer_opened_and_closed": true
    });
    app.borrow_mut().shutdown();
    ensure!(app.borrow().finish_shutdown());
    fs::write(case.join("sample.json"), serde_json::to_vec(&row)?)?;
    Ok(())
}
