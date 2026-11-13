//! Application-owned output transforms, independent of hardware inversion.

use std::fmt;
use std::ops::Range;
use std::os::fd::OwnedFd;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use slint::Rgb8Pixel;

use crate::gray_target::ScenePixels;
use crate::refresh::PhysicalRect;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RenderError {
    EventLoopTerminated,
}

impl fmt::Display for RenderError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("the Kindle event loop has terminated")
    }
}

impl std::error::Error for RenderError {}

/// A producer for process-local rendering preferences, with no system writes.
/// Success acknowledges a request, not completion of a physical panel update.
#[derive(Clone)]
pub struct RenderHandle {
    pub(crate) dark_mode: Arc<AtomicBool>,
    pub(crate) wake: Arc<OwnedFd>,
    pub(crate) quit: Arc<AtomicBool>,
}

impl RenderHandle {
    /// Invert the rendered grayscale output without changing canonical RGB pixels.
    /// The event loop rewrites and fully refreshes the panel on a mode transition.
    pub fn set_dark_mode(&self, enabled: bool) -> Result<(), RenderError> {
        if self.quit.load(Ordering::SeqCst) {
            return Err(RenderError::EventLoopTerminated);
        }
        if self.dark_mode.swap(enabled, Ordering::AcqRel) != enabled {
            crate::wakeup::poke(&self.wake);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct GrayMode {
    pub(crate) dark_mode: bool,
    pub(crate) black_and_white: bool,
}

impl GrayMode {
    pub(crate) fn paper(self) -> u8 {
        self.grayscale(255)
    }

    pub(crate) fn pixel(self, pixel: Rgb8Pixel) -> u8 {
        let value = ((77 * u32::from(pixel.r) + 150 * u32::from(pixel.g) + 29 * u32::from(pixel.b))
            >> 8) as u8;
        self.grayscale(value)
    }

    pub(crate) fn grayscale(self, value: u8) -> u8 {
        let value = if self.black_and_white {
            if value < 128 { 0 } else { 255 }
        } else {
            value
        };
        if self.dark_mode { 255 - value } else { value }
    }
}

pub(crate) struct FrameWrite {
    pub(crate) region: Option<PhysicalRect>,
    pub(crate) mode_changed: bool,
}

/// Keeps only a scanline of transformed output. Slint retains canonical scene
/// pixels, so partial redraws and repeated full updates cannot double-invert it.
pub(crate) struct GrayRenderer {
    applied_mode: GrayMode,
    line: Vec<u8>,
}

impl GrayRenderer {
    pub(crate) fn new(width: usize, initial_mode: GrayMode) -> Self {
        Self {
            applied_mode: initial_mode,
            line: vec![0; width],
        }
    }

    pub(crate) fn mode(&self) -> GrayMode {
        self.applied_mode
    }

    pub(crate) fn write_frame(
        &mut self,
        rgb: &[Rgb8Pixel],
        panel: (u32, u32),
        dirty: Option<PhysicalRect>,
        mode: GrayMode,
        write_line: impl FnMut(usize, Range<usize>, &[u8]),
    ) -> FrameWrite {
        self.write_scene_frame(ScenePixels::Rgb(rgb), panel, dirty, mode, write_line)
    }

    pub(crate) fn write_scene_frame(
        &mut self,
        scene: ScenePixels<'_>,
        panel: (u32, u32),
        dirty: Option<PhysicalRect>,
        mode: GrayMode,
        mut write_line: impl FnMut(usize, Range<usize>, &[u8]),
    ) -> FrameWrite {
        let mode_changed = mode != self.applied_mode;
        let region = if mode_changed {
            Some(PhysicalRect {
                x: 0,
                y: 0,
                width: panel.0,
                height: panel.1,
            })
        } else {
            dirty.and_then(|dirty| clip(dirty, panel))
        };
        if let Some(region) = region {
            let width = panel.0 as usize;
            let x = region.x as usize;
            let y = region.y as usize;
            let span = region.width as usize;
            self.line.resize(width, 0);
            for row in y..y + region.height as usize {
                let start = row * width + x;
                let output = &mut self.line[..span];
                match scene {
                    ScenePixels::Gray(gray) => {
                        for (destination, source) in
                            output.iter_mut().zip(&gray[start..start + span])
                        {
                            *destination = mode.grayscale(source.value());
                        }
                    }
                    ScenePixels::Rgb(rgb) => {
                        for (destination, source) in
                            output.iter_mut().zip(&rgb[start..start + span])
                        {
                            *destination = mode.pixel(*source);
                        }
                    }
                }
                write_line(row, x..x + span, output);
            }
        }
        // The entire output is written before this mode can be treated as applied.
        self.applied_mode = mode;
        FrameWrite {
            region,
            mode_changed,
        }
    }
}

fn clip(rect: PhysicalRect, panel: (u32, u32)) -> Option<PhysicalRect> {
    let x = i64::from(rect.x).clamp(0, i64::from(panel.0));
    let y = i64::from(rect.y).clamp(0, i64::from(panel.1));
    let right = (i64::from(rect.x) + i64::from(rect.width)).clamp(0, i64::from(panel.0));
    let bottom = (i64::from(rect.y) + i64::from(rect.height)).clamp(0, i64::from(panel.1));
    if right <= x || bottom <= y {
        return None;
    }
    Some(PhysicalRect {
        x: x as i32,
        y: y as i32,
        width: (right - x) as u32,
        height: (bottom - y) as u32,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gray_target::Gray8Pixel;
    use slint::platform::software_renderer::TargetPixel;

    fn gray(value: u8) -> Rgb8Pixel {
        Rgb8Pixel {
            r: value,
            g: value,
            b: value,
        }
    }

    struct Panel {
        width: usize,
        height: usize,
        stride: usize,
        bytes: Vec<u8>,
    }

    impl Panel {
        fn new(width: usize, height: usize) -> Self {
            let stride = width + 3;
            Self {
                width,
                height,
                stride,
                bytes: vec![0x31; stride * height],
            }
        }

        fn draw(
            &mut self,
            renderer: &mut GrayRenderer,
            rgb: &[Rgb8Pixel],
            dirty: Option<PhysicalRect>,
            mode: GrayMode,
        ) -> FrameWrite {
            renderer.write_frame(
                rgb,
                (self.width as u32, self.height as u32),
                dirty,
                mode,
                |row, columns, pixels| {
                    let start = row * self.stride + columns.start;
                    self.bytes[start..start + pixels.len()].copy_from_slice(pixels);
                },
            )
        }

        fn visible(&self) -> Vec<u8> {
            self.bytes
                .chunks_exact(self.stride)
                .flat_map(|row| row[..self.width].iter().copied())
                .collect()
        }

        fn assert_padding_untouched(&self) {
            for row in self.bytes.chunks_exact(self.stride) {
                assert_eq!(&row[self.width..], &[0x31; 3]);
            }
        }
    }

    fn full(width: u32, height: u32) -> Option<PhysicalRect> {
        Some(PhysicalRect {
            x: 0,
            y: 0,
            width,
            height,
        })
    }

    #[test]
    fn text_antialiasing_and_color_image_pixels_invert_exactly_once() {
        let rgb = [
            gray(255),
            gray(0),
            gray(127),
            gray(128),
            Rgb8Pixel { r: 255, g: 0, b: 0 },
            Rgb8Pixel { r: 0, g: 255, b: 0 },
            Rgb8Pixel { r: 0, g: 0, b: 255 },
        ];
        let original = rgb;
        let mut panel = Panel::new(7, 1);
        let light = GrayMode::default();
        let dark = GrayMode {
            dark_mode: true,
            ..light
        };
        let mut renderer = GrayRenderer::new(7, light);
        panel.draw(&mut renderer, &rgb, full(7, 1), light);
        assert_eq!(panel.visible(), [255, 0, 127, 128, 76, 149, 28]);
        panel.draw(&mut renderer, &rgb, None, dark);
        assert_eq!(panel.visible(), [0, 255, 128, 127, 179, 106, 227]);
        panel.draw(&mut renderer, &rgb, full(7, 1), dark);
        assert_eq!(panel.visible(), [0, 255, 128, 127, 179, 106, 227]);
        assert_eq!(rgb, original, "Canonical RGB pixels must remain unchanged");
        panel.assert_padding_untouched();
    }

    #[test]
    fn transitions_rewrite_every_pixel_and_partial_pages_and_popups_keep_the_mode() {
        let light = GrayMode::default();
        let dark = GrayMode {
            dark_mode: true,
            ..light
        };
        let mut renderer = GrayRenderer::new(3, light);
        let mut panel = Panel::new(3, 2);
        let mut rgb = [
            gray(255),
            gray(0),
            gray(128),
            gray(70),
            gray(255),
            gray(200),
        ];
        panel.draw(&mut renderer, &rgb, full(3, 2), light);
        assert_eq!(panel.visible(), [255, 0, 128, 70, 255, 200]);

        let one_pixel = Some(PhysicalRect {
            x: 0,
            y: 0,
            width: 1,
            height: 1,
        });
        let transition = panel.draw(&mut renderer, &rgb, one_pixel, dark);
        assert_eq!(transition.region, full(3, 2));
        assert!(transition.mode_changed);
        assert_eq!(panel.visible(), [0, 255, 127, 185, 0, 55]);

        rgb[0] = gray(0);
        let popup = panel.draw(&mut renderer, &rgb, one_pixel, dark);
        assert_eq!(popup.region, one_pixel);
        assert!(!popup.mode_changed);
        assert_eq!(panel.visible(), [255, 255, 127, 185, 0, 55]);
        panel.draw(&mut renderer, &rgb, full(3, 2), dark);
        assert_eq!(panel.visible(), [255, 255, 127, 185, 0, 55]);

        rgb = [
            gray(10),
            gray(240),
            gray(90),
            gray(30),
            gray(250),
            gray(110),
        ];
        panel.draw(&mut renderer, &rgb, full(3, 2), dark);
        assert_eq!(panel.visible(), [245, 15, 165, 225, 5, 145]);
        let transition = panel.draw(&mut renderer, &rgb, None, light);
        assert_eq!(transition.region, full(3, 2));
        assert_eq!(panel.visible(), [10, 240, 90, 30, 250, 110]);
        assert!(
            panel
                .draw(&mut renderer, &rgb, None, light)
                .region
                .is_none()
        );
        panel.assert_padding_untouched();
    }

    #[test]
    fn bilevel_threshold_is_applied_before_inversion_and_round_trips_cleanly() {
        let rgb = [gray(0), gray(127), gray(128), gray(255)];
        let light = GrayMode {
            black_and_white: true,
            dark_mode: false,
        };
        let dark = GrayMode {
            dark_mode: true,
            ..light
        };
        let mut renderer = GrayRenderer::new(4, light);
        let mut panel = Panel::new(4, 1);
        panel.draw(&mut renderer, &rgb, full(4, 1), light);
        assert_eq!(panel.visible(), [0, 0, 255, 255]);
        panel.draw(&mut renderer, &rgb, None, dark);
        assert_eq!(panel.visible(), [255, 255, 0, 0]);
        panel.draw(&mut renderer, &rgb, None, light);
        assert_eq!(panel.visible(), [0, 0, 255, 255]);
    }

    #[test]
    fn gray_scene_and_rgb_wrapper_match_across_modes_and_clipped_dirty_regions() {
        let width = 17;
        let height = 19;
        let rgb: Vec<_> = (0..width * height)
            .map(|index| gray((index % 256) as u8))
            .collect();
        let gray: Vec<_> = rgb
            .iter()
            .map(|pixel| Gray8Pixel::from_rgb(pixel.r, pixel.g, pixel.b))
            .collect();
        let original_gray = gray.clone();
        let mut reference_panel = Panel::new(width, height);
        let mut candidate_panel = Panel::new(width, height);
        let mut reference_renderer = GrayRenderer::new(width, GrayMode::default());
        let mut candidate_renderer = GrayRenderer::new(width, GrayMode::default());

        let regions = [
            full(width as u32, height as u32),
            None,
            Some(PhysicalRect {
                x: -3,
                y: -2,
                width: 9,
                height: 7,
            }),
            Some(PhysicalRect {
                x: 15,
                y: 17,
                width: 8,
                height: 9,
            }),
            Some(PhysicalRect {
                x: 40,
                y: 40,
                width: 4,
                height: 4,
            }),
        ];
        for black_and_white in [false, true, false] {
            for dark_mode in [false, true, false] {
                let mode = GrayMode {
                    dark_mode,
                    black_and_white,
                };
                for dirty in regions {
                    let reference =
                        reference_panel.draw(&mut reference_renderer, &rgb, dirty, mode);
                    let candidate = candidate_renderer.write_scene_frame(
                        ScenePixels::Gray(&gray),
                        (width as u32, height as u32),
                        dirty,
                        mode,
                        |row, columns, pixels| {
                            let start = row * candidate_panel.stride + columns.start;
                            candidate_panel.bytes[start..start + pixels.len()]
                                .copy_from_slice(pixels);
                        },
                    );
                    assert_eq!(reference.region, candidate.region);
                    assert_eq!(reference.mode_changed, candidate.mode_changed);
                    assert_eq!(reference_panel.bytes, candidate_panel.bytes);
                }
            }
        }
        assert_eq!(gray, original_gray);
        candidate_panel.assert_padding_untouched();
    }

    #[test]
    fn render_requests_wake_the_loop_and_latest_state_controls_actual_pixels() {
        let wakeup = crate::wakeup::make_wakeup().unwrap();
        let handle = RenderHandle {
            dark_mode: Arc::new(AtomicBool::new(false)),
            wake: wakeup.write.clone(),
            quit: Arc::new(AtomicBool::new(false)),
        };
        handle.set_dark_mode(true).unwrap();
        handle.set_dark_mode(false).unwrap();
        handle.set_dark_mode(true).unwrap();
        let mut descriptor = libc::pollfd {
            fd: std::os::fd::AsRawFd::as_raw_fd(&*wakeup.read),
            events: libc::POLLIN,
            revents: 0,
        };
        // SAFETY: descriptor points to one valid pollfd for the call.
        assert_eq!(unsafe { libc::poll(&mut descriptor, 1, 0) }, 1);
        assert_ne!(descriptor.revents & libc::POLLIN, 0);

        let mut renderer = GrayRenderer::new(2, GrayMode::default());
        let mut panel = Panel::new(2, 1);
        let rgb = [gray(255), gray(20)];
        let mode = GrayMode {
            dark_mode: handle.dark_mode.load(Ordering::Acquire),
            black_and_white: false,
        };
        panel.draw(&mut renderer, &rgb, None, mode);
        assert_eq!(panel.visible(), [0, 235]);
        handle.quit.store(true, Ordering::SeqCst);
        assert_eq!(
            handle.set_dark_mode(false),
            Err(RenderError::EventLoopTerminated)
        );
        panel.draw(&mut renderer, &rgb, full(2, 1), mode);
        assert_eq!(panel.visible(), [0, 235]);
    }
}
