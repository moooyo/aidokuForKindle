//! Standalone offscreen oracle; run only in the designated remote test environment.

#![allow(dead_code)]

#[path = "../../vendor/slint-backend-kindle/src/gray_target.rs"]
mod gray_target;
#[path = "../../vendor/slint-backend-kindle/src/refresh.rs"]
mod refresh;
#[path = "../../vendor/slint-backend-kindle/src/render_control.rs"]
mod render_control;
#[path = "../../vendor/slint-backend-kindle/src/wakeup.rs"]
mod wakeup;

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::RefCell;
use std::hint::black_box;
use std::rc::Rc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use gray_target::{RetainedScene, ScenePixels};
use refresh::PhysicalRect;
use render_control::{GrayMode, GrayRenderer};
use slint::platform::software_renderer::{
    MinimalSoftwareWindow, RenderingRotation, RepaintBufferType,
};
use slint::platform::{Platform, WindowAdapter};
use slint::{ComponentHandle, Image, PhysicalSize, Rgb8Pixel, Rgba8Pixel, SharedPixelBuffer};

static LIVE_BYTES: AtomicUsize = AtomicUsize::new(0);
static PEAK_BYTES: AtomicUsize = AtomicUsize::new(0);
static TEST_FONT: std::sync::OnceLock<Vec<u8>> = std::sync::OnceLock::new();

struct CountedSystem;

fn allocated(size: usize) {
    let live = LIVE_BYTES.fetch_add(size, Ordering::Relaxed) + size;
    PEAK_BYTES.fetch_max(live, Ordering::Relaxed);
}

// SAFETY: Every operation delegates to the system allocator with its original
// pointer and layout. Counters only record requested live bytes.
unsafe impl GlobalAlloc for CountedSystem {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        // SAFETY: The caller provides a valid allocation layout.
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() {
            allocated(layout.size());
        }
        pointer
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        // SAFETY: The caller provides a valid allocation layout.
        let pointer = unsafe { System.alloc_zeroed(layout) };
        if !pointer.is_null() {
            allocated(layout.size());
        }
        pointer
    }

    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        LIVE_BYTES.fetch_sub(layout.size(), Ordering::Relaxed);
        // SAFETY: The pointer and layout belong to this allocator.
        unsafe { System.dealloc(pointer, layout) };
    }

    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        // SAFETY: The pointer/layout pair and new size meet GlobalAlloc's contract.
        let replacement = unsafe { System.realloc(pointer, layout, size) };
        if !replacement.is_null() {
            LIVE_BYTES.fetch_sub(layout.size(), Ordering::Relaxed);
            allocated(size);
        }
        replacement
    }
}

#[global_allocator]
static ALLOCATOR: CountedSystem = CountedSystem;

slint::slint! {
    export component OracleWindow inherits Window {
        in property <int> phase;
        in property <color> accent: #666;
        in property <color> gradient-end: #ddd;
        in property <image> page;
        in property <image> overlay;
        background: #f5f5f5;
        Image {
            width: parent.width; height: parent.height;
            source: root.page; image-fit: fill;
        }
        Rectangle {
            x: 2.5px; y: 1.5px; width: min(212px, parent.width - 4px); height: 24px;
            background: #fff; opacity: 0.81;
            border-width: 1.5px; border-radius: 4.5px; border-color: #222;
        }
        Text {
            x: 5.25px; y: 2.25px; width: parent.width - 10px; height: 22px;
            text: "Gray 8: Aa 0123"; color: #333; font-family: "FreeSans";
            font-size: 15.5px; font-weight: 500;
        }
        Rectangle {
            x: 3px + mod(root.phase, 7) * 2px; y: 30px;
            width: 25.5px; height: 28.5px;
            background: root.accent; border-width: 1.5px;
            border-color: #222; border-radius: 4.5px; opacity: 0.73;
        }
        Rectangle {
            x: 34.25px; y: 30.75px; width: 26.5px; height: 28.5px;
            background: @linear-gradient(37deg, #000, root.gradient-end);
        }
        Image {
            x: 7.25px; y: 62.75px; width: 39.5px; height: 29.5px;
            source: root.overlay; image-fit: contain; opacity: 0.63;
        }
        Path {
            x: 48.5px; y: 65.5px; width: 32px; height: 29px;
            commands: "M 1 1 C 29 2 3 28 29 28 L 1 1";
            fill: #777; stroke: #222; stroke-width: 1.25px;
        }
    }
}

struct OffscreenPlatform {
    last_window: Rc<RefCell<Option<Rc<MinimalSoftwareWindow>>>>,
}

impl Platform for OffscreenPlatform {
    fn create_window_adapter(&self) -> Result<Rc<dyn WindowAdapter>, slint::PlatformError> {
        let window = MinimalSoftwareWindow::new(RepaintBufferType::ReusedBuffer);
        *self.last_window.borrow_mut() = Some(window.clone());
        Ok(window)
    }
}

fn install() -> Rc<RefCell<Option<Rc<MinimalSoftwareWindow>>>> {
    let path = std::env::var("GRAY_TARGET_TEST_FONT")
        .unwrap_or_else(|_| "/usr/share/fonts/truetype/freefont/FreeSans.ttf".into());
    TEST_FONT.get_or_init(|| std::fs::read(path).expect("read the oracle font"));
    let last_window = Rc::new(RefCell::new(None));
    slint::platform::set_platform(Box::new(OffscreenPlatform {
        last_window: last_window.clone(),
    }))
    .expect("install the offscreen platform");
    last_window
}

fn neutral_image(width: u32, height: u32, phase: u32) -> Image {
    let mut buffer = SharedPixelBuffer::<Rgb8Pixel>::new(width, height);
    for (index, pixel) in buffer.make_mut_slice().iter_mut().enumerate() {
        let value = ((index as u32 * 13 + index as u32 / width * 7 + phase) % 256) as u8;
        *pixel = Rgb8Pixel::new(value, value, value);
    }
    Image::from_rgb8(buffer)
}

fn overlay(colored: bool) -> Image {
    let mut buffer = SharedPixelBuffer::<Rgba8Pixel>::new(13, 11);
    for (index, pixel) in buffer.make_mut_slice().iter_mut().enumerate() {
        let value = (index * 17 % 256) as u8;
        *pixel = Rgba8Pixel::new(
            value,
            if colored { 255 - value } else { value },
            value,
            (index * 31 % 256) as u8,
        );
    }
    Image::from_rgba8(buffer)
}

struct Rig {
    app: OracleWindow,
    window: Rc<MinimalSoftwareWindow>,
    scene: RetainedScene,
    output: GrayRenderer,
    panel: Vec<u8>,
    width: u32,
    height: u32,
    stride: usize,
    rotation: RenderingRotation,
}

impl Drop for Rig {
    fn drop(&mut self) {
        self.app.hide().expect("hide the offscreen component");
    }
}

impl Rig {
    fn new(
        last_window: &Rc<RefCell<Option<Rc<MinimalSoftwareWindow>>>>,
        width: u32,
        height: u32,
        allow_gray: bool,
    ) -> Self {
        let app = OracleWindow::new().expect("create the oracle window");
        let window = last_window.borrow_mut().take().expect("capture the window");
        window
            .renderer()
            .register_font_from_memory(TEST_FONT.get().unwrap())
            .expect("register the oracle font");
        window.set_size(PhysicalSize::new(width, height));
        app.set_page(neutral_image(width, height, 0));
        app.set_overlay(overlay(false));
        app.show().expect("show the offscreen component");
        let stride = width as usize + 11;
        Self {
            app,
            window,
            scene: RetainedScene::new(width as usize * height as usize, allow_gray),
            output: GrayRenderer::new(width as usize, GrayMode::default()),
            panel: vec![0x31; stride * height as usize],
            width,
            height,
            stride,
            rotation: RenderingRotation::NoRotation,
        }
    }

    fn draw(&mut self, force_full: bool, mode: GrayMode) -> PhysicalRect {
        slint::platform::update_timers_and_animations();
        if force_full {
            self.window.request_redraw();
        }
        let mut dirty = None;
        self.window.draw_if_needed(|renderer| {
            renderer.set_rendering_rotation(self.rotation);
            let previous = renderer.repaint_buffer_type();
            if force_full {
                renderer.set_repaint_buffer_type(RepaintBufferType::NewBuffer);
            }
            let rendered = self.scene.render(renderer, self.width as usize);
            renderer.set_repaint_buffer_type(previous);
            let origin = rendered.bounding_box_origin();
            let size = rendered.bounding_box_size();
            dirty = Some(PhysicalRect {
                x: origin.x,
                y: origin.y,
                width: size.width,
                height: size.height,
            });
        });
        self.write(dirty, mode);
        dirty.unwrap_or(PhysicalRect {
            x: 0,
            y: 0,
            width: 0,
            height: 0,
        })
    }

    fn write(&mut self, dirty: Option<PhysicalRect>, mode: GrayMode) {
        let stride = self.stride;
        self.output.write_scene_frame(
            self.scene.pixels(),
            (self.width, self.height),
            dirty,
            mode,
            |row, columns, pixels| {
                let offset = row * stride + columns.start;
                self.panel[offset..offset + pixels.len()].copy_from_slice(pixels);
            },
        );
    }

    fn set_rotation(&mut self, rotation: RenderingRotation) {
        let transpose = matches!(
            rotation,
            RenderingRotation::Rotate90 | RenderingRotation::Rotate270
        );
        self.window.set_size(if transpose {
            PhysicalSize::new(self.height, self.width)
        } else {
            PhysicalSize::new(self.width, self.height)
        });
        self.rotation = rotation;
    }

    fn is_gray(&self) -> bool {
        matches!(self.scene.pixels(), ScenePixels::Gray(_))
    }

    fn resize(&mut self, width: u32, height: u32) {
        let allow_gray = self.is_gray();
        self.width = width;
        self.height = height;
        self.stride = width as usize + 11;
        self.scene = RetainedScene::new(width as usize * height as usize, allow_gray);
        self.output = GrayRenderer::new(width as usize, self.output.mode());
        self.panel = vec![0x31; self.stride * height as usize];
        self.window.set_size(PhysicalSize::new(width, height));
        self.app.set_page(neutral_image(width, height, 0));
    }

    fn assert_padding(&self) {
        for row in self.panel.chunks_exact(self.stride) {
            assert!(
                row[self.width as usize..]
                    .iter()
                    .all(|value| *value == 0x31)
            );
        }
    }
}

fn assert_same(candidate: &Rig, reference: &Rig) {
    assert_eq!(
        candidate.panel, reference.panel,
        "scanout pixels must match"
    );
    candidate.assert_padding();
    reference.assert_padding();
    match (candidate.scene.pixels(), reference.scene.pixels()) {
        (ScenePixels::Gray(gray), ScenePixels::Rgb(rgb)) => {
            for (gray, rgb) in gray.iter().zip(rgb) {
                assert_eq!(
                    *rgb,
                    Rgb8Pixel::new(gray.value(), gray.value(), gray.value())
                );
            }
        }
        (ScenePixels::Rgb(candidate), ScenePixels::Rgb(reference)) => {
            assert_eq!(candidate, reference);
        }
        _ => panic!("the reference must retain RGB pixels"),
    }
}

fn run_oracle() {
    let last_window = install();
    let light = GrayMode::default();
    let mut candidate = Rig::new(&last_window, 127, 113, true);
    let mut reference = Rig::new(&last_window, 127, 113, false);
    assert_eq!(candidate.draw(false, light), reference.draw(false, light));
    assert!(candidate.is_gray());
    assert_same(&candidate, &reference);

    for phase in 1..10 {
        candidate.app.set_phase(phase);
        reference.app.set_phase(phase);
        let dirty = candidate.draw(false, light);
        assert_eq!(dirty, reference.draw(false, light));
        assert!(dirty.width < candidate.width || dirty.height < candidate.height);
        assert_same(&candidate, &reference);
    }

    for rotation in [
        RenderingRotation::Rotate90,
        RenderingRotation::Rotate180,
        RenderingRotation::Rotate270,
        RenderingRotation::NoRotation,
    ] {
        candidate.set_rotation(rotation);
        reference.set_rotation(rotation);
        assert_eq!(candidate.draw(true, light), reference.draw(true, light));
        assert!(candidate.is_gray());
        assert_same(&candidate, &reference);
        for black_and_white in [false, true] {
            for dark_mode in [false, true, false] {
                let mode = GrayMode {
                    dark_mode,
                    black_and_white,
                };
                candidate.write(None, mode);
                reference.write(None, mode);
                assert_same(&candidate, &reference);
                let full = Some(PhysicalRect {
                    x: 0,
                    y: 0,
                    width: 127,
                    height: 113,
                });
                candidate.write(full, mode);
                reference.write(full, mode);
                assert_same(&candidate, &reference);
            }
        }
    }

    for (width, height) in [(93, 151), (181, 97), (127, 113)] {
        candidate.resize(width, height);
        reference.resize(width, height);
        assert_eq!(candidate.draw(true, light), reference.draw(true, light));
        assert!(candidate.is_gray());
        assert_same(&candidate, &reference);
    }

    candidate
        .app
        .set_accent(slint::Color::from_rgb_u8(217, 20, 42));
    reference
        .app
        .set_accent(slint::Color::from_rgb_u8(217, 20, 42));
    let rebuilt = candidate.draw(false, light);
    reference.draw(false, light);
    assert_eq!(
        rebuilt,
        PhysicalRect {
            x: 0,
            y: 0,
            width: 127,
            height: 113
        }
    );
    assert!(!candidate.is_gray());
    assert_same(&candidate, &reference);
    candidate
        .app
        .set_accent(slint::Color::from_rgb_u8(102, 102, 102));
    reference
        .app
        .set_accent(slint::Color::from_rgb_u8(102, 102, 102));
    let restored = candidate.draw(false, light);
    reference.draw(false, light);
    assert_eq!(
        restored,
        PhysicalRect {
            x: 0,
            y: 0,
            width: 127,
            height: 113
        }
    );
    assert!(!candidate.is_gray(), "the RGB fallback must remain sticky");
    assert_same(&candidate, &reference);
    candidate.app.set_phase(11);
    reference.app.set_phase(11);
    assert_eq!(candidate.draw(false, light), reference.draw(false, light));
    assert_same(&candidate, &reference);

    for source in ["rgb-image", "rgba-image", "gradient"] {
        let mut candidate = Rig::new(&last_window, 127, 113, true);
        let mut reference = Rig::new(&last_window, 127, 113, false);
        candidate.draw(false, light);
        reference.draw(false, light);
        if source == "rgb-image" {
            let mut pixels = SharedPixelBuffer::<Rgb8Pixel>::new(13, 11);
            pixels.make_mut_slice().fill(Rgb8Pixel::new(255, 0, 0));
            let image = Image::from_rgb8(pixels);
            candidate.app.set_page(image.clone());
            reference.app.set_page(image);
        } else if source == "rgba-image" {
            let image = overlay(true);
            candidate.app.set_overlay(image.clone());
            reference.app.set_overlay(image);
        } else {
            let color = slint::Color::from_rgb_u8(31, 233, 81);
            candidate.app.set_gradient_end(color);
            reference.app.set_gradient_end(color);
        }
        candidate.draw(false, light);
        reference.draw(false, light);
        assert!(
            !candidate.is_gray(),
            "{source} must select RGB before scanout"
        );
        assert_same(&candidate, &reference);
    }
    println!(
        "oracle=pass text=1 opacity=1 rgba=1 path=1 gradient=1 partial=9 rotation=4 resize=3 retained_rewrite=48 color_fallback=4 sticky=1 post_fallback_dirty=1 padding=1"
    );
}

fn rss() -> (usize, usize) {
    let status = std::fs::read_to_string("/proc/self/status").expect("read process RSS");
    let value = |key| {
        status
            .lines()
            .find(|line| line.starts_with(key))
            .and_then(|line| line.split_whitespace().nth(1))
            .and_then(|number| number.parse::<usize>().ok())
            .expect("find process RSS field")
            * 1024
    };
    (value("VmRSS:"), value("VmHWM:"))
}

fn benchmark(kind: &str, workload: &str, width: u32, height: u32, iterations: usize) {
    let last_window = install();
    let mut rig = Rig::new(&last_window, width, height, kind == "gray");
    let pages = [
        neutral_image(width, height, 0),
        neutral_image(width, height, 71),
    ];
    let light = GrayMode::default();
    for index in 0..8 {
        rig.app.set_phase(index);
        rig.draw(false, light);
    }
    let mut durations = Vec::with_capacity(iterations);
    let mut dirty_pixels = 0;
    for index in 0..iterations {
        let started = Instant::now();
        rig.app.set_phase(index as i32 + 8);
        if workload == "full" {
            rig.app.set_page(pages[index % 2].clone());
        }
        let dirty = rig.draw(false, light);
        durations.push(started.elapsed().as_nanos() as u64);
        dirty_pixels += u64::from(dirty.width) * u64::from(dirty.height);
    }
    black_box(&rig.panel);
    durations.sort_unstable();
    let mean = durations.iter().sum::<u64>() as f64 / iterations as f64 / 1000.;
    let median = durations[iterations / 2] as f64 / 1000.;
    let p95 = durations[(iterations - 1) * 95 / 100] as f64 / 1000.;
    let (rss, hwm) = rss();
    println!(
        "kind={kind} workload={workload} width={width} height={height} iterations={iterations} mean_us={mean:.3} median_us={median:.3} p95_us={p95:.3} mean_dirty_pixels={} live_bytes={} peak_bytes={} rss_bytes={rss} hwm_bytes={hwm}",
        dirty_pixels / iterations as u64,
        LIVE_BYTES.load(Ordering::Relaxed),
        PEAK_BYTES.load(Ordering::Relaxed)
    );
}

fn memory(kind: &str, width: u32, height: u32) {
    let last_window = install();
    let app = OracleWindow::new().expect("create memory window");
    let window = last_window
        .borrow_mut()
        .take()
        .expect("capture memory window");
    window
        .renderer()
        .register_font_from_memory(TEST_FONT.get().unwrap())
        .expect("register the memory font");
    window.set_size(PhysicalSize::new(width, height));
    app.set_page(neutral_image(width, height, 0));
    app.set_overlay(overlay(false));
    app.show().expect("show memory window");
    let base_live = LIVE_BYTES.load(Ordering::Relaxed);
    PEAK_BYTES.store(base_live, Ordering::Relaxed);
    let mut scene = RetainedScene::new(width as usize * height as usize, kind == "gray");
    let scene_delta = LIVE_BYTES.load(Ordering::Relaxed) - base_live;
    assert!(window.draw_if_needed(|renderer| {
        scene.render(renderer, width as usize);
    }));
    black_box(scene.pixels());
    let live = LIVE_BYTES.load(Ordering::Relaxed);
    let peak = PEAK_BYTES.load(Ordering::Relaxed);
    let (rss, hwm) = rss();
    println!(
        "kind={kind} width={width} height={height} scene_alloc_bytes={scene_delta} render_live_delta_bytes={} render_peak_delta_bytes={} process_live_bytes={live} rss_bytes={rss} hwm_bytes={hwm}",
        live - base_live,
        peak - base_live
    );
}

fn fallback(width: u32, height: u32, iterations: usize) {
    let last_window = install();
    let light = GrayMode::default();
    let mut candidate_times = Vec::with_capacity(iterations);
    let mut reference_times = Vec::with_capacity(iterations);
    let mut next_times = Vec::with_capacity(iterations);
    let mut allocation_delta = 0;
    let mut allocation_peak_delta = 0;
    let mut fallback_rss = 0;
    let mut fallback_hwm = 0;
    let mut next_dirty_pixels = 0;
    for _ in 0..iterations {
        let mut candidate = Rig::new(&last_window, width, height, true);
        let mut reference = Rig::new(&last_window, width, height, false);
        candidate.draw(false, light);
        reference.draw(false, light);
        let color = slint::Color::from_rgb_u8(217, 20, 42);
        candidate.app.set_accent(color);
        reference.app.set_accent(color);
        let base_live = LIVE_BYTES.load(Ordering::Relaxed);
        PEAK_BYTES.store(base_live, Ordering::Relaxed);
        let started = Instant::now();
        candidate.draw(false, light);
        candidate_times.push(started.elapsed().as_nanos() as u64);
        allocation_delta = LIVE_BYTES.load(Ordering::Relaxed) - base_live;
        allocation_peak_delta = PEAK_BYTES.load(Ordering::Relaxed) - base_live;
        (fallback_rss, fallback_hwm) = rss();
        let started = Instant::now();
        reference.draw(false, light);
        reference_times.push(started.elapsed().as_nanos() as u64);
        assert!(!candidate.is_gray());
        assert_same(&candidate, &reference);
        candidate.app.set_phase(1);
        reference.app.set_phase(1);
        let started = Instant::now();
        let next_dirty = candidate.draw(false, light);
        next_times.push(started.elapsed().as_nanos() as u64);
        next_dirty_pixels = u64::from(next_dirty.width) * u64::from(next_dirty.height);
        reference.draw(false, light);
        assert_same(&candidate, &reference);
    }
    candidate_times.sort_unstable();
    reference_times.sort_unstable();
    next_times.sort_unstable();
    println!(
        "fallback width={width} height={height} iterations={iterations} candidate_median_us={:.3} rgb_dirty_median_us={:.3} next_median_us={:.3} next_dirty_pixels={next_dirty_pixels} allocation_delta_bytes={allocation_delta} allocation_peak_delta_bytes={allocation_peak_delta} rss_bytes={fallback_rss} hwm_bytes={fallback_hwm}",
        candidate_times[iterations / 2] as f64 / 1000.,
        reference_times[iterations / 2] as f64 / 1000.,
        next_times[iterations / 2] as f64 / 1000.
    );
}

fn main() {
    let arguments: Vec<_> = std::env::args().skip(1).collect();
    match arguments.first().map(String::as_str) {
        Some("oracle") | None => run_oracle(),
        Some("bench") => benchmark(
            &arguments[1],
            &arguments[2],
            arguments[3].parse().unwrap(),
            arguments[4].parse().unwrap(),
            arguments[5].parse().unwrap(),
        ),
        Some("memory") => memory(
            &arguments[1],
            arguments[2].parse().unwrap(),
            arguments[3].parse().unwrap(),
        ),
        Some("fallback") => fallback(
            arguments[1].parse().unwrap(),
            arguments[2].parse().unwrap(),
            arguments[3].parse().unwrap(),
        ),
        _ => panic!(
            "use oracle, bench <rgb|gray> <full|partial> <width> <height> <iterations>, memory <rgb|gray> <width> <height>, or fallback <width> <height> <iterations>"
        ),
    }
}

#[cfg(test)]
mod oracle_tests {
    #[test]
    fn actual_software_renderer_matches_rgb_with_retention_and_color_fallback() {
        super::run_oracle();
    }
}
