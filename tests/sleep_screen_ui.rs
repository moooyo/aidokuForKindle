//! Offline Slint evidence for sleep covers, readable controls, and retained pixels.
//!
//! The headless renderer does not open native display/input devices. Native
//! night-mode conversion belongs to the Kindle backend, after this UI scene.

use std::{
    cell::RefCell,
    collections::VecDeque,
    fs,
    io::Read,
    path::{Path, PathBuf},
    rc::Rc,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use aidoku_kindle::{
    app::{App, AppOptions},
    platform::{DeviceProfile, Platform},
    ui::{AppWindow, Commands, SleepScreenModel, State},
};
use anyhow::{Context, Result, ensure};
use flate2::read::GzDecoder;
use serde_json::{Value, json};
use slint::{
    ComponentHandle, Rgb8Pixel, Rgba8Pixel, SharedPixelBuffer,
    fontique_010::fontique,
    platform::{
        EventLoopProxy, WindowAdapter,
        software_renderer::{MinimalSoftwareWindow, RepaintBufferType},
    },
};

type Callback = Box<dyn FnOnce() + Send>;
type EventQueue = Arc<Mutex<VecDeque<Callback>>>;

struct OffscreenPlatform {
    window: Rc<MinimalSoftwareWindow>,
    events: EventQueue,
    started: Instant,
}

struct OffscreenProxy(EventQueue);

impl EventLoopProxy for OffscreenProxy {
    fn quit_event_loop(&self) -> Result<(), slint::EventLoopError> {
        Ok(())
    }

    fn invoke_from_event_loop(&self, event: Callback) -> Result<(), slint::EventLoopError> {
        self.0.lock().unwrap().push_back(event);
        Ok(())
    }
}

impl slint::platform::Platform for OffscreenPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, slint::PlatformError> {
        Ok(self.window.clone())
    }

    fn duration_since_start(&self) -> Duration {
        self.started.elapsed()
    }

    fn new_event_loop_proxy(&self) -> Option<Box<dyn EventLoopProxy>> {
        Some(Box::new(OffscreenProxy(self.events.clone())))
    }
}

fn drain_events(events: &EventQueue) {
    for _ in 0..10_000 {
        let next = events.lock().unwrap().pop_front();
        let Some(next) = next else { return };
        next();
    }
    panic!("The offline control queue did not become idle");
}

fn register_fonts() -> Result<()> {
    // These are the same bundled families and weights used by normal startup.
    let faces: &[(&str, f32, &[u8])] = &[
        (
            "Noto Sans CJK SC",
            400.0,
            include_bytes!("../assets/fonts/NotoSansSC-Regular.otf.gz"),
        ),
        (
            "Noto Sans CJK SC",
            500.0,
            include_bytes!("../assets/fonts/NotoSansSC-Medium.otf.gz"),
        ),
        (
            "Noto Sans CJK SC",
            700.0,
            include_bytes!("../assets/fonts/NotoSansSC-Bold.otf.gz"),
        ),
        (
            "Noto Serif CJK SC",
            400.0,
            include_bytes!("../assets/fonts/NotoSerifSC-Regular.otf.gz"),
        ),
        (
            "Noto Serif CJK SC",
            700.0,
            include_bytes!("../assets/fonts/NotoSerifSC-Bold.otf.gz"),
        ),
        (
            "Noto Serif CJK SC",
            900.0,
            include_bytes!("../assets/fonts/NotoSerifSC-Black.otf.gz"),
        ),
        (
            "Literata",
            400.0,
            include_bytes!("../assets/fonts/Literata-Regular.ttf.gz"),
        ),
        (
            "Literata",
            600.0,
            include_bytes!("../assets/fonts/Literata-SemiBold.ttf.gz"),
        ),
        (
            "Literata",
            700.0,
            include_bytes!("../assets/fonts/Literata-Bold.ttf.gz"),
        ),
    ];
    for (family, weight, encoded) in faces {
        let mut decoded = Vec::new();
        GzDecoder::new(*encoded)
            .take(64 * 1024 * 1024 + 1)
            .read_to_end(&mut decoded)?;
        ensure!(
            decoded.len() <= 64 * 1024 * 1024,
            "A bundled font exceeds its decompression budget"
        );
        let mut collection = slint::fontique_010::shared_collection();
        let registered = collection.register_fonts(fontique::Blob::new(Arc::new(decoded)), None);
        ensure!(
            registered
                .iter()
                .any(|(id, faces)| collection.family_name(*id) == Some(*family)
                    && faces.iter().any(|face| face.weight().value() == *weight)),
            "The UI fixture must use bundled {family} weight {weight}"
        );
    }
    Ok(())
}

fn snapshot(ui: &AppWindow) -> Result<SharedPixelBuffer<Rgba8Pixel>> {
    slint::platform::update_timers_and_animations();
    ui.window().request_redraw();
    ui.window()
        .take_snapshot()
        .map_err(|error| anyhow::anyhow!("Capture the software scene: {error}"))
}

fn save_evidence(
    root: Option<&Path>,
    name: &str,
    pixels: &SharedPixelBuffer<Rgba8Pixel>,
) -> Result<()> {
    if let Some(root) = root {
        image::save_buffer(
            root.join(format!("{name}.png")),
            pixels.as_bytes(),
            pixels.width(),
            pixels.height(),
            image::ColorType::Rgba8,
        )?;
    }
    Ok(())
}

fn projected_controls(
    pixels: &SharedPixelBuffer<Rgba8Pixel>,
    native_inverted: bool,
) -> SharedPixelBuffer<Rgba8Pixel> {
    let mut result = pixels.clone();
    if native_inverted {
        for pixel in result.make_mut_slice() {
            pixel.r = 255 - pixel.r;
            pixel.g = 255 - pixel.g;
            pixel.b = 255 - pixel.b;
        }
    }
    result
}

fn assert_readable_reader_controls(
    pixels: &SharedPixelBuffer<Rgba8Pixel>,
    large: bool,
    landscape: bool,
) -> Result<Value> {
    let width = pixels.width();
    let height = pixels.height();
    let panel_height = if large { 210 } else { 188 };
    let toolbar_height = if large { 77 } else { 69 };
    let inset = if large && landscape {
        120
    } else if large {
        24
    } else {
        0
    };
    let mut paper_pixels = 0;
    for y in height - panel_height..height {
        for x in 0..width {
            paper_pixels += usize::from(paper(&pixels.as_slice()[(y * width + x) as usize]));
        }
    }
    let paper_fraction = paper_pixels as f64 / f64::from(width * panel_height);
    ensure!(
        paper_fraction > 0.80,
        "Reader controls must retain a light opaque surface"
    );
    let mut tool_ink = Vec::new();
    for index in 0..5 {
        let left = inset + (width - inset * 2) * index / 5 + 4;
        let right = inset + (width - inset * 2) * (index + 1) / 5 - 4;
        let mut count = 0;
        for y in height - toolbar_height + 7..height - 10 {
            for x in left..right {
                let pixel = pixels.as_slice()[(y * width + x) as usize];
                count += usize::from(pixel.r < 64 && pixel.g < 64 && pixel.b < 64);
            }
        }
        ensure!(
            count > 40,
            "Every Reader tool must have readable dark glyphs and labels"
        );
        tool_ink.push(count);
    }
    Ok(json!({"light_surface_fraction": paper_fraction, "dark_pixels_per_tool": tool_ink}))
}

fn configure_layout(
    ui: &AppWindow,
    window: &MinimalSoftwareWindow,
    profile: DeviceProfile,
    landscape: bool,
) {
    let (width, height) = profile.logical_size(landscape);
    ui.set_canvas_width(width as f32);
    ui.set_canvas_height(height as f32);
    window.set_size(slint::PhysicalSize::new(width, height));
    let state = ui.global::<State>();
    state.set_large_screen(profile.is_scribe());
    state.set_landscape(landscape);
    state.set_booting(false);
    state.set_exiting(false);
    state.set_route("reader".into());
    state.set_modal("".into());
    state.set_message("".into());
    state.set_busy(false);
    state.set_reader_menu(false);
    state.set_reader_image_ready(true);
    state.set_spread(false);
    let mut underlying = SharedPixelBuffer::<Rgb8Pixel>::new(20, 30);
    underlying.make_mut_slice().fill(Rgb8Pixel {
        r: 255,
        g: 0,
        b: 255,
    });
    state.set_reader_image(slint::Image::from_rgb8(underlying));
    state.set_reader_image_next(slint::Image::default());
}

fn paper(pixel: &Rgba8Pixel) -> bool {
    pixel.r >= 250 && pixel.g >= 250 && pixel.b >= 250
}

fn bounds(pixels: &SharedPixelBuffer<Rgba8Pixel>) -> Option<(u32, u32, u32, u32)> {
    let (width, height) = (pixels.width(), pixels.height());
    let mut result: Option<(u32, u32, u32, u32)> = None;
    for y in 0..height {
        for x in 0..width {
            if paper(&pixels.as_slice()[(y * width + x) as usize]) {
                continue;
            }
            result = Some(match result {
                None => (x, y, x, y),
                Some((left, top, right, bottom)) => {
                    (left.min(x), top.min(y), right.max(x), bottom.max(y))
                }
            });
        }
    }
    result
}

fn corner_cover(width: u32, height: u32) -> slint::Image {
    let mut pixels = SharedPixelBuffer::<Rgb8Pixel>::new(width, height);
    let values = [17, 85, 153, 221];
    for y in 0..height {
        for x in 0..width {
            let quadrant = usize::from(x >= width / 2) + 2 * usize::from(y >= height / 2);
            let gray = values[quadrant];
            pixels.make_mut_slice()[(y * width + x) as usize] = Rgb8Pixel {
                r: gray,
                g: gray,
                b: gray,
            };
        }
    }
    slint::Image::from_rgb8(pixels)
}

fn assert_complete_cover(
    pixels: &SharedPixelBuffer<Rgba8Pixel>,
    source: (u32, u32),
) -> Result<Value> {
    let (width, height) = (pixels.width(), pixels.height());
    let (left, top, right, bottom) =
        bounds(pixels).context("The complete sleep cover must be visible")?;
    let actual = ((right - left + 1) as f64, (bottom - top + 1) as f64);
    let scale =
        (f64::from(width) / f64::from(source.0)).min(f64::from(height) / f64::from(source.1));
    let expected = (f64::from(source.0) * scale, f64::from(source.1) * scale);
    ensure!(
        (actual.0 - expected.0).abs() <= 2.0 && (actual.1 - expected.1).abs() <= 2.0,
        "The cover must fit completely without cropping or reserved chrome: {actual:?} versus {expected:?}"
    );
    ensure!(
        (f64::from(left) - (f64::from(width) - expected.0) / 2.0).abs() <= 2.0
            && (f64::from(top) - (f64::from(height) - expected.1) / 2.0).abs() <= 2.0,
        "The proportional cover must remain centered"
    );
    ensure!(
        (actual.0 / actual.1 - f64::from(source.0) / f64::from(source.1)).abs() < 0.01,
        "The complete cover must preserve its source aspect ratio"
    );
    for (x_fraction, y_fraction, expected_gray) in [
        (0.12, 0.12, 17),
        (0.88, 0.12, 85),
        (0.12, 0.88, 153),
        (0.88, 0.88, 221),
    ] {
        let x = left + (actual.0 * x_fraction) as u32;
        let y = top + (actual.1 * y_fraction) as u32;
        let pixel = pixels.as_slice()[(y * width + x) as usize];
        ensure!(
            pixel.r.abs_diff(expected_gray) <= 2 && pixel.r == pixel.g && pixel.g == pixel.b,
            "Every source corner marker must remain visible and grayscale"
        );
    }
    for y in 0..height {
        for x in 0..width {
            if x + 2 < left || x > right + 2 || y + 2 < top || y > bottom + 2 {
                ensure!(
                    paper(&pixels.as_slice()[(y * width + x) as usize]),
                    "A sleep cover cannot expose the underlying Reader or edge badges"
                );
            }
        }
    }
    Ok(
        json!({"source_size": [source.0, source.1], "visible_bounds": [left, top, right, bottom],
        "complete": true, "proportional": true, "all_corner_markers_visible": true, "outside_is_paper": true}),
    )
}

fn assert_text_card(pixels: &SharedPixelBuffer<Rgba8Pixel>) -> Result<Value> {
    let (width, height) = (pixels.width(), pixels.height());
    let expected = (
        f64::from(width) * 0.12,
        f64::from(height) * 0.13,
        f64::from(width) * 0.88,
        f64::from(height) * 0.87,
    );
    let (left, top, right, bottom) =
        bounds(pixels).context("The text fallback must contain a visible card")?;
    ensure!(
        (f64::from(left) - expected.0).abs() <= 2.0
            && (f64::from(top) - expected.1).abs() <= 2.0
            && (f64::from(right) - expected.2).abs() <= 2.0
            && (f64::from(bottom) - expected.3).abs() <= 2.0,
        "Long fallback text must stay inside the intended card"
    );
    let mut body_pixels = 0;
    for y in 0..height {
        for x in 0..width {
            let pixel = &pixels.as_slice()[(y * width + x) as usize];
            if x + 3 < left || x > right + 3 || y + 3 < top || y > bottom + 3 {
                ensure!(
                    paper(pixel),
                    "The text fallback cannot reveal underlying UI or overflow its card"
                );
            }
            if x > left + 20 && x + 20 < right && y > top + 20 && y + 20 < bottom && !paper(pixel) {
                body_pixels += 1;
            }
        }
    }
    ensure!(
        body_pixels > 100,
        "Fallback title and author must render visible glyphs"
    );
    Ok(
        json!({"visible_bounds": [left, top, right, bottom], "body_pixels": body_pixels,
        "text_clipped_to_card": true, "outside_is_paper": true}),
    )
}

struct GlyphMask {
    width: u32,
    height: u32,
    top: u32,
    pixels: Vec<bool>,
}

fn text_region(
    pixels: &SharedPixelBuffer<Rgba8Pixel>,
    title: bool,
    large: bool,
) -> (u32, u32, u32, u32) {
    let (width, height) = (f64::from(pixels.width()), f64::from(pixels.height()));
    let margin = if large { 36.0 } else { 22.0 };
    let (start, end) = if title { (0.18, 0.60) } else { (0.69, 0.84) };
    (
        (width * 0.12 + margin).floor() as u32,
        (height * (0.13 + 0.74 * start)).floor() as u32,
        (width * 0.88 - margin).ceil() as u32,
        (height * (0.13 + 0.74 * end)).ceil() as u32,
    )
}

fn glyph_mask(
    pixels: &SharedPixelBuffer<Rgba8Pixel>,
    title: bool,
    large: bool,
    rows: Option<u32>,
) -> Result<GlyphMask> {
    let (left, top, right, bottom) = text_region(pixels, title, large);
    let ink = |x, y| {
        let pixel = pixels.as_slice()[(y * pixels.width() + x) as usize];
        pixel.r < 128 && pixel.g < 128 && pixel.b < 128
    };
    let first = (top..bottom)
        .find(|y| (left..right).any(|x| ink(x, *y)))
        .context("The fallback prefix must contain visible glyphs")?;
    let end = rows.map_or(bottom, |rows| (first + rows).min(bottom));
    let mut result: Option<(u32, u32, u32, u32)> = None;
    for y in first..end {
        for x in left..right {
            if !ink(x, y) {
                continue;
            }
            result = Some(match result {
                None => (x, y, x, y),
                Some((left, top, right, bottom)) => {
                    (left.min(x), top.min(y), right.max(x), bottom.max(y))
                }
            });
        }
    }
    let (left, top, right, bottom) = result.context("The first fallback line must be visible")?;
    let (width, height) = (right - left + 1, bottom - top + 1);
    let mut mask = Vec::with_capacity((width * height) as usize);
    for y in top..=bottom {
        for x in left..=right {
            mask.push(ink(x, y));
        }
    }
    Ok(GlyphMask {
        width,
        height,
        top,
        pixels: mask,
    })
}

fn glyph_matches(source: &GlyphMask, target: &GlyphMask) -> f64 {
    let mut matched = 0;
    let mut total = 0;
    for y in 0..source.height {
        for x in 0..source.width {
            if !source.pixels[(y * source.width + x) as usize] {
                continue;
            }
            total += 1;
            let nearby = (-1..=1).any(|dy| {
                (-1..=1).any(|dx| {
                    let (x, y) = (i64::from(x) + dx, i64::from(y) + dy);
                    x >= 0
                        && y >= 0
                        && x < i64::from(target.width)
                        && y < i64::from(target.height)
                        && target.pixels[(y as u32 * target.width + x as u32) as usize]
                })
            });
            matched += usize::from(nearby);
        }
    }
    matched as f64 / total.max(1) as f64
}

fn assert_leading_prefixes(
    reference: &SharedPixelBuffer<Rgba8Pixel>,
    long: &SharedPixelBuffer<Rgba8Pixel>,
    large: bool,
) -> Result<Value> {
    let mut scores = Vec::new();
    for title in [true, false] {
        let expected = glyph_mask(reference, title, large, None)?;
        let observed = glyph_mask(long, title, large, Some(expected.height + 2))?;
        ensure!(
            expected.width.abs_diff(observed.width) <= 2
                && expected.height.abs_diff(observed.height) <= 2,
            "The first visible fallback line must retain its complete leading prefix"
        );
        let score = glyph_matches(&expected, &observed).min(glyph_matches(&observed, &expected));
        ensure!(
            score >= 0.97,
            "The fallback must display leading glyphs instead of a vertically cropped middle line"
        );
        let (_, top, _, bottom) = text_region(reference, title, large);
        let center_offset = (f64::from(expected.top) + f64::from(expected.height) / 2.0
            - f64::from(top + bottom) / 2.0)
            .abs();
        ensure!(
            center_offset <= f64::from(expected.height),
            "A short fallback label must remain centered in its original region"
        );
        scores.push(json!({"role": if title { "title" } else { "author" },
            "leading_glyph_match": score, "short_label_center_offset": center_offset}));
    }
    Ok(json!({"leading_prefixes_visible": true, "labels": scores}))
}

fn retained_position(snapshot: &Value) -> Value {
    let keys = [
        "route",
        "current_book",
        "source_id",
        "reader_page",
        "current_chapter",
        "reader_offset",
        "reader_zoom",
        "displayed_reader",
    ];
    Value::Object(
        keys.into_iter()
            .map(|key| (key.to_owned(), snapshot[key].clone()))
            .collect(),
    )
}

fn tap(ui: &AppWindow, x: f32, y: f32) -> Result<()> {
    use slint::platform::{PointerEventButton, WindowEvent};

    let position = slint::LogicalPosition::new(x, y);
    // Native touch can begin without a preceding hover or move event.
    ui.window()
        .try_dispatch_event(WindowEvent::PointerPressed {
            position,
            button: PointerEventButton::Left,
        })?;
    ui.window()
        .try_dispatch_event(WindowEvent::PointerReleased {
            position,
            button: PointerEventButton::Left,
        })?;
    slint::platform::update_timers_and_animations();
    Ok(())
}

fn assert_reader_toolbar_interactions(
    ui: &AppWindow,
    app: &Rc<RefCell<App>>,
    profile: DeviceProfile,
    landscape: bool,
) -> Result<Value> {
    let actions = Rc::new(RefCell::new(Vec::<(String, String)>::new()));
    let observed = actions.clone();
    let application = Rc::downgrade(app);
    ui.global::<Commands>()
        .on_action(move |section, action, value| {
            observed
                .borrow_mut()
                .push((section.to_string(), action.to_string()));
            application
                .upgrade()
                .expect("The pointer fixture application must remain attached")
                .borrow_mut()
                .dispatch(section.as_str(), action.as_str(), value.as_str())
                .expect("A pointer control action must complete successfully");
        });
    let (width, height) = profile.logical_size(landscape);
    let (width, height) = (width as f32, height as f32);
    let large = profile.is_scribe();
    let header = if large { 78.0 } else { 89.5 };
    let bottom = if large { 210.0 } else { 188.0 };
    let toolbar = if large { 77.0 } else { 69.0 };
    let inset = if large && landscape {
        120.0
    } else if large {
        24.0
    } else {
        0.0
    };
    let tools: &[&str] = if large && landscape {
        &["mode", "layout", "picture", "brightness", "more"]
    } else {
        &["mode", "picture", "brightness", "orientation", "more"]
    };
    let original_page = app.borrow().snapshot()["reader_page"].clone();
    snapshot(ui)?;
    tap(ui, width / 2.0, height / 2.0)?;
    ensure!(
        ui.global::<State>().get_reader_menu(),
        "A content tap must open the Reader controls"
    );

    // Header and footer paper belong to the controls, never to the page tap area.
    actions.borrow_mut().clear();
    tap(ui, width / 2.0, 3.0)?;
    tap(ui, width / 2.0, height - bottom + 5.0)?;
    ensure!(
        actions.borrow().is_empty() && ui.global::<State>().get_reader_menu(),
        "Blank control surfaces must not dismiss the menu or turn the page"
    );

    for (index, action) in tools.iter().enumerate() {
        let x = inset + (index as f32 + 0.5) * (width - inset * 2.0) / tools.len() as f32;
        for activation in 0..2 {
            actions.borrow_mut().clear();
            tap(ui, x, height - toolbar / 2.0)?;
            snapshot(ui)?;
            ensure!(
                *actions.borrow() == vec![("reader".into(), (*action).into())],
                "Each {action} tab contact must dispatch exactly one Reader action"
            );
            let state = ui.global::<State>();
            ensure!(
                state.get_reader_panel().as_str() == *action
                    && !state.get_modal().is_empty()
                    && state.get_reader_menu()
                    && app.borrow().snapshot()["reader_page"] == original_page,
                "Reader tab {action} must remain open after activation {activation}"
            );
        }
    }

    actions.borrow_mut().clear();
    tap(ui, width / 2.0, header + 8.0)?;
    snapshot(ui)?;
    ensure!(
        *actions.borrow() == vec![("panel".into(), "close".into())]
            && ui.global::<State>().get_modal().is_empty()
            && ui.global::<State>().get_reader_menu(),
        "An outside-panel tap must close only the panel and retain the toolbar"
    );
    actions.borrow_mut().clear();
    tap(ui, width / 2.0, height / 2.0)?;
    ensure!(
        *actions.borrow() == vec![("reader".into(), "menu".into())]
            && !ui.global::<State>().get_reader_menu(),
        "A subsequent content tap must dismiss the Reader controls once"
    );
    actions.borrow_mut().clear();
    tap(ui, width * 0.1, height / 2.0)?;
    ensure!(
        *actions.borrow() == vec![("reader".into(), "next".into())]
            && app.borrow().snapshot()["reader_page"] != original_page,
        "The closed-menu page tap area must resume normal page turns"
    );
    Ok(json!({"profile": profile.id(), "landscape": landscape,
        "tabs": tools, "activations_per_tab": 2, "one_action_per_contact": true,
        "blank_controls_isolated": true, "outside_close_and_page_turn_retained": true}))
}

#[test]
fn sleep_screen_preserves_complete_covers_text_and_retained_reader_at_all_layouts() -> Result<()> {
    let events = EventQueue::default();
    let window = MinimalSoftwareWindow::new(RepaintBufferType::NewBuffer);
    slint::platform::set_platform(Box::new(OffscreenPlatform {
        window: window.clone(),
        events: events.clone(),
        started: Instant::now(),
    }))
    .map_err(|error| anyhow::anyhow!("Install the offline software platform: {error}"))?;
    let ui = AppWindow::new()?;
    register_fonts()?;
    ui.show()?;
    let directory = tempfile::tempdir()?;
    let evidence = std::env::var_os("AIDOKU_SLEEP_SCREEN_EVIDENCE").map(PathBuf::from);
    if let Some(root) = evidence.as_deref() {
        fs::create_dir_all(root)?;
    }
    let mut reports = Vec::new();
    let mut reader_control_reports = Vec::new();
    let mut reader_interaction_reports = Vec::new();
    let mut startup_color_reports = Vec::new();

    for profile in [DeviceProfile::Pw5, DeviceProfile::Scribe] {
        for landscape in [false, true] {
            let layout = format!(
                "{}-{}",
                profile.id(),
                if landscape { "landscape" } else { "portrait" }
            );
            let (width, height) = profile.logical_size(landscape);
            configure_layout(&ui, &window, profile, landscape);
            for dark in [false, true] {
                let state = ui.global::<State>();
                state.set_booting(true);
                state.set_startup_dark(dark);
                state.set_native_scene_inverted(false);
                let before_transform = snapshot(&ui)?;
                state.set_native_scene_inverted(true);
                let after_transform = projected_controls(&snapshot(&ui)?, true);
                ensure!(
                    before_transform.width() == after_transform.width()
                        && before_transform.height() == after_transform.height()
                        && before_transform
                            .as_slice()
                            .iter()
                            .zip(after_transform.as_slice())
                            .all(|(a, b)| a.r.abs_diff(b.r) <= 1
                                && a.g.abs_diff(b.g) <= 1
                                && a.b.abs_diff(b.b) <= 1),
                    "The single startup scene must retain its visible colors when native inversion activates"
                );
                startup_color_reports.push(json!({"layout": layout, "requested_dark": dark,
                    "native_inversion_activation_preserves_visible_scene": true}));
                state.set_booting(false);
                state.set_native_scene_inverted(false);
            }
            for night_mode in [false, true] {
                let name = format!(
                    "{layout}-{}",
                    if night_mode {
                        "dark-preference"
                    } else {
                        "light"
                    }
                );
                ui.global::<State>().set_night_mode(night_mode);
                let model = ui.global::<SleepScreenModel>();
                for source in [(200, 300), (300, 160)] {
                    model.set_cover(corner_cover(source.0, source.1));
                    model.set_has_cover(true);
                    model.set_active(true);
                    let pixels = snapshot(&ui)?;
                    ensure!(
                        (pixels.width(), pixels.height()) == (width, height),
                        "The real Slint snapshot must match the requested layout"
                    );
                    let capture = format!("{name}-cover-{}x{}", source.0, source.1);
                    save_evidence(evidence.as_deref(), &capture, &pixels)?;
                    let checks = assert_complete_cover(&pixels, source)?;
                    reports.push(json!({"case": capture, "size": [width, height], "night_mode_preference": night_mode, "checks": checks}));
                }
                model.set_has_cover(false);
                let title_prefix = "Comic Title ABC";
                let author_prefix = "Aidoku / Author";
                model.set_title(title_prefix.into());
                model.set_author(author_prefix.into());
                let prefix_reference = snapshot(&ui)?;
                save_evidence(
                    evidence.as_deref(),
                    &format!("{name}-text-reference"),
                    &prefix_reference,
                )?;
                let title = format!("{title_prefix}\n{}", "Long title line\n".repeat(32))
                    .chars()
                    .take(256)
                    .collect::<String>();
                let author = format!("{author_prefix}\n{}", "Writer\n".repeat(32))
                    .chars()
                    .take(128)
                    .collect::<String>();
                ensure!(
                    title.chars().count() == 256 && author.chars().count() == 128,
                    "The direct-model fixture must respect the application's text scalar budgets"
                );
                ensure!(
                    title.lines().count() > 12 && author.lines().count() > 12,
                    "Every layout must exercise height clipping beyond its title and author regions"
                );
                model.set_title(title.into());
                model.set_author(author.into());
                let text = snapshot(&ui)?;
                let capture = format!("{name}-long-text");
                save_evidence(evidence.as_deref(), &capture, &text)?;
                let checks = assert_text_card(&text)?;
                let prefixes =
                    assert_leading_prefixes(&prefix_reference, &text, profile.is_scribe())?;
                reports.push(json!({"case": capture, "night_mode_preference": night_mode, "checks": checks, "prefixes": prefixes}));
                model.set_active(false);
            }

            let options = AppOptions {
                data_dir: directory.path().join(&layout),
                demo: true,
                offline: true,
                initial_screen: "reader".into(),
                landscape,
                resume: false,
            };
            let platform = Rc::new(Platform::new(profile, false)?);
            let prepared = App::prepare(options, profile)?;
            let app = App::attach_prepared(&ui, prepared, platform)?;
            while !app.borrow_mut().advance_startup()? {}
            ui.global::<State>().set_booting(false);
            let deadline = Instant::now() + Duration::from_secs(20);
            loop {
                drain_events(&events);
                app.borrow_mut().poll();
                let snapshot = app.borrow().snapshot();
                if ui.global::<State>().get_reader_image_ready()
                    && ui.global::<State>().get_reader_image().size().width > 0
                    && snapshot["sleep_screen"]["full_resolution"] == true
                {
                    break;
                }
                ensure!(
                    Instant::now() < deadline,
                    "The offline Reader and sleep cover did not prepare for {layout}"
                );
                std::thread::sleep(Duration::from_millis(5));
            }
            app.borrow_mut()
                .dispatch("settings", "set", "screensaver-mode=cover")?;
            for night_mode in [false, true] {
                let name = format!(
                    "{layout}-{}",
                    if night_mode {
                        "dark-preference"
                    } else {
                        "light"
                    }
                );
                app.borrow_mut().dispatch(
                    "settings",
                    "set",
                    if night_mode {
                        "night-mode=true"
                    } else {
                        "night-mode=false"
                    },
                )?;
                ui.global::<State>().set_reader_menu(false);
                ui.global::<State>().set_busy(false);
                let before = snapshot(&ui)?;
                ui.global::<State>().set_native_scene_inverted(night_mode);
                ui.global::<State>().set_reader_menu(true);
                let controls = snapshot(&ui)?;
                let projected = projected_controls(&controls, night_mode);
                let checks =
                    assert_readable_reader_controls(&projected, profile.is_scribe(), landscape)?;
                save_evidence(
                    evidence.as_deref(),
                    &format!("{name}-reader-controls"),
                    &projected,
                )?;
                reader_control_reports.push(json!({"case": name, "native_scene_inverted": night_mode,
                    "conversion_scope": "Software simulation of the backend's full-scene inversion; not hardware scanout", "checks": checks}));
                ui.global::<State>().set_reader_menu(false);
                ui.global::<State>().set_native_scene_inverted(false);
                let closed = snapshot(&ui)?;
                ensure!(
                    before.as_bytes() == closed.as_bytes(),
                    "Closing controls must retain the exact comic pixels"
                );
                let position = retained_position(&app.borrow().lifecycle_snapshot());
                ensure!(
                    app.borrow_mut().begin_sleep_transition(true)?,
                    "The prepared application overlay must be projected after its intent gate"
                );
                let model = ui.global::<SleepScreenModel>();
                ensure!(
                    model.get_active() && model.get_has_cover(),
                    "The awake-prepared application cover must be resident before sleep"
                );
                let sleeping = snapshot(&ui)?;
                save_evidence(evidence.as_deref(), &format!("{name}-app-sleep"), &sleeping)?;
                app.borrow_mut().hide_sleep_cover();
                let restored = snapshot(&ui)?;
                ensure!(
                    before.as_bytes() == restored.as_bytes(),
                    "Hiding the sleep overlay must restore the exact Reader pixels for {name}"
                );
                ensure!(
                    position == retained_position(&app.borrow().lifecycle_snapshot()),
                    "The overlay must preserve the same Reader source anchor for {name}"
                );
                save_evidence(
                    evidence.as_deref(),
                    &format!("{name}-reader-restored"),
                    &restored,
                )?;
                reports.push(json!({"case": format!("{name}-app-roundtrip"), "night_mode_preference": night_mode,
                    "pixels_restored_exactly": true, "source_position_retained": true, "sleep_image_ready": true}));
                app.borrow_mut().resume_after_sleep()?;
                let resumed = snapshot(&ui)?;
                ensure!(
                    before.as_bytes() == resumed.as_bytes(),
                    "The retained resume frame must match its original pixels for {name}"
                );
                app.borrow_mut().finish_resume_frame()?;
            }

            reader_interaction_reports.push(assert_reader_toolbar_interactions(
                &ui, &app, profile, landscape,
            )?);
            app.borrow_mut().dispatch("navigation", "tab", "0")?;
            for night_mode in [false, true] {
                let name = format!(
                    "{layout}-{}",
                    if night_mode {
                        "dark-preference"
                    } else {
                        "light"
                    }
                );
                app.borrow_mut().dispatch(
                    "settings",
                    "set",
                    if night_mode {
                        "night-mode=true"
                    } else {
                        "night-mode=false"
                    },
                )?;
                ensure!(
                    app.borrow_mut().begin_sleep_transition(true)?,
                    "A non-Reader route must project the neutral sleep overlay"
                );
                let model = ui.global::<SleepScreenModel>();
                ensure!(
                    model.get_active()
                        && !model.get_has_cover()
                        && model.get_title().as_str() == "Aidoku"
                        && model.get_author().as_str() == aidoku_kindle::tr("sleep_neutral"),
                    "A non-Reader route must not select residual Reader metadata or cover pixels"
                );
                let neutral = snapshot(&ui)?;
                ensure!(
                    ui.global::<State>().get_route().as_str() == "library"
                        && !ui.global::<State>().get_landscape(),
                    "The actual non-Reader route must follow the application's portrait policy"
                );
                let capture = format!("{name}-to-library-neutral");
                save_evidence(evidence.as_deref(), &capture, &neutral)?;
                let checks = assert_text_card(&neutral)?;
                reports.push(json!({"case": capture, "origin_reader_layout": layout,
                    "route": "library", "actual_landscape": false,
                    "size": [neutral.width(), neutral.height()],
                    "night_mode_preference": night_mode, "checks": checks}));
                app.borrow_mut().hide_sleep_cover();
                app.borrow_mut().resume_after_sleep()?;
                snapshot(&ui)?;
                app.borrow_mut().finish_resume_frame()?;
            }
            app.borrow_mut().shutdown();
            ensure!(
                app.borrow().finish_shutdown(),
                "The offline application must release its owned worker before the next layout"
            );
            drop(app);
            drain_events(&events);
        }
    }
    if let Some(root) = evidence.as_deref() {
        fs::write(
            root.join("report.json"),
            serde_json::to_vec_pretty(&json!({
                "scope": "Offline Slint software rendering with synthetic covers and demo Reader data; no physical panel claim",
                "reference": "BiliComics 9f6b6884f9cc9a080318cff9b3d5e5f2a7f1d632 tests/sleep_screen_ui.rs",
                "layouts": 4, "night_mode_preference_states": [false, true],
                "native_dark_conversion_exercised": false,
                "night_mode_boundary": "The Kindle backend converts the light Slint scene after this renderer; dark-preference PNGs are raw Slint evidence, not native framebuffer captures",
                "cases": reports,
                "reader_control_cases": reader_control_reports,
                "reader_interaction_cases": reader_interaction_reports,
                "startup_color_cases": startup_color_reports,
            }))?,
        )?;
    }
    ui.hide()?;
    Ok(())
}
