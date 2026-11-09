//! Font-independent startup feedback before the Slint event loop owns the panel.

use std::io;
use std::ops::Range;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, mpsc};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::framebuffer::Framebuffer;
use crate::render_control::GrayMode;

const UPDATE_INTERVAL: Duration = Duration::from_millis(1_250);

/// A pre-rasterized, tightly packed 8-bit grayscale image.
#[derive(Clone, Copy)]
pub struct StartupImage {
    width: usize,
    height: usize,
    pixels: &'static [u8],
}

impl StartupImage {
    /// Decode two little-endian u32 dimensions followed by grayscale pixels.
    /// No font engine, image codec, or system font is needed at startup.
    pub fn from_encoded(bytes: &'static [u8]) -> io::Result<Self> {
        let header = bytes.get(..8).ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                "startup image header is missing",
            )
        })?;
        let width = u32::from_le_bytes(header[..4].try_into().unwrap()) as usize;
        let height = u32::from_le_bytes(header[4..].try_into().unwrap()) as usize;
        let pixels = &bytes[8..];
        if width == 0
            || height == 0
            || width > 2_048
            || height > 256
            || width.checked_mul(height) != Some(pixels.len())
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "startup image dimensions or pixel count are invalid",
            ));
        }
        Ok(Self {
            width,
            height,
            pixels,
        })
    }
}

/// Text is prepared from the application's translation resources at authoring time.
#[derive(Clone, Copy)]
pub struct StartupImages {
    pub title: StartupImage,
    pub starting: StartupImage,
    pub stages: [StartupImage; 3],
}

struct Renderer {
    framebuffer: Framebuffer,
    images: StartupImages,
    flipped: bool,
    dark_mode: bool,
    title_y: usize,
    starting_y: usize,
    dynamic_x: usize,
    dynamic_y: usize,
    dynamic_width: usize,
    dynamic_height: usize,
    stage_height: usize,
    dynamic_pixels: Vec<u8>,
    pulse: usize,
    last_update: Instant,
}

impl Renderer {
    fn new(
        framebuffer: Framebuffer,
        images: StartupImages,
        flipped: bool,
        dark_mode: bool,
    ) -> io::Result<Self> {
        let stage_width = images.stages.iter().map(|image| image.width).max().unwrap();
        let stage_height = images
            .stages
            .iter()
            .map(|image| image.height)
            .max()
            .unwrap();
        let dynamic_width = (stage_width + 40).max(160);
        let dynamic_height = stage_height + 76;
        let group_height = images.title.height + 56 + images.starting.height + 24 + dynamic_height;
        let width = framebuffer.width as usize;
        let height = framebuffer.height as usize;
        if images.title.width > width
            || images.starting.width > width
            || dynamic_width > width
            || group_height > height
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "the approved display is too small for the startup artwork",
            ));
        }
        let title_y = (height - group_height) / 2;
        let starting_y = title_y + images.title.height + 56;
        let dynamic_y = starting_y + images.starting.height + 24;
        Ok(Self {
            framebuffer,
            images,
            flipped,
            dark_mode,
            title_y,
            starting_y,
            dynamic_x: (width - dynamic_width) / 2,
            dynamic_y,
            dynamic_width,
            dynamic_height,
            stage_height,
            dynamic_pixels: vec![255; dynamic_width * dynamic_height],
            pulse: 0,
            last_update: Instant::now(),
        })
    }

    fn draw_initial(&mut self, stage: usize) {
        self.framebuffer.fill(
            GrayMode {
                dark_mode: self.dark_mode,
                black_and_white: false,
            }
            .paper(),
        );
        let title_x = (self.framebuffer.width as usize - self.images.title.width) / 2;
        let starting_x = (self.framebuffer.width as usize - self.images.starting.width) / 2;
        write_image(
            &mut self.framebuffer,
            self.images.title.pixels,
            self.images.title.width,
            self.images.title.height,
            title_x,
            self.title_y,
            self.flipped,
            self.dark_mode,
        );
        write_image(
            &mut self.framebuffer,
            self.images.starting.pixels,
            self.images.starting.width,
            self.images.starting.height,
            starting_x,
            self.starting_y,
            self.flipped,
            self.dark_mode,
        );
        self.draw_dynamic(stage);
        if let Err(error) = self.framebuffer.refresh_full() {
            log::error!("startup framebuffer refresh was rejected: {error}");
        }
        self.last_update = Instant::now();
    }

    fn draw_dynamic(&mut self, stage: usize) {
        self.dynamic_pixels.fill(255);
        let image = self.images.stages[stage.min(self.images.stages.len() - 1)];
        let image_x = (self.dynamic_width - image.width) / 2;
        let image_y = (self.stage_height - image.height) / 2;
        for (row, pixels) in image.pixels.chunks_exact(image.width).enumerate() {
            let offset = (row + image_y) * self.dynamic_width + image_x;
            self.dynamic_pixels[offset..offset + image.width].copy_from_slice(pixels);
        }
        let center_x = self.dynamic_width / 2;
        let center_y = self.stage_height + 48;
        for dot in 0..3 {
            let x = center_x + dot * 32 - 32;
            let shade = if dot == self.pulse { 0 } else { 170 };
            for dy in -7i32..=7 {
                for dx in -7i32..=7 {
                    if dx * dx + dy * dy <= 49 {
                        let index = (center_y as i32 + dy) as usize * self.dynamic_width
                            + (x as i32 + dx) as usize;
                        self.dynamic_pixels[index] = shade;
                    }
                }
            }
        }
        write_image(
            &mut self.framebuffer,
            &self.dynamic_pixels,
            self.dynamic_width,
            self.dynamic_height,
            self.dynamic_x,
            self.dynamic_y,
            self.flipped,
            self.dark_mode,
        );
    }

    fn update(&mut self, stage: usize) {
        // Match normal rendering: submit updates without blocking on panel
        // completion. The low submission rate avoids continuous e-ink activity.
        self.pulse = (self.pulse + 1) % 3;
        self.draw_dynamic(stage);
        let (x, y) = transformed_origin(
            (
                self.framebuffer.width as usize,
                self.framebuffer.height as usize,
            ),
            (self.dynamic_x, self.dynamic_y),
            (self.dynamic_width, self.dynamic_height),
            self.flipped,
        );
        if let Err(error) = self.framebuffer.refresh_region(
            slint::PhysicalPosition::new(x as i32, y as i32),
            slint::PhysicalSize::new(self.dynamic_width as u32, self.dynamic_height as u32),
        ) {
            log::error!("startup framebuffer region refresh was rejected: {error}");
        }
        self.last_update = Instant::now();
    }
}

fn write_image(
    framebuffer: &mut Framebuffer,
    pixels: &[u8],
    width: usize,
    height: usize,
    x: usize,
    y: usize,
    flipped: bool,
    dark_mode: bool,
) {
    let panel = (framebuffer.width as usize, framebuffer.height as usize);
    for_each_image_row(
        pixels,
        (width, height),
        (x, y),
        panel,
        flipped,
        dark_mode,
        |row, columns, pixels| framebuffer.write_line(row, columns, pixels),
    );
}

fn transformed_origin(
    panel: (usize, usize),
    origin: (usize, usize),
    size: (usize, usize),
    flipped: bool,
) -> (usize, usize) {
    if flipped {
        (panel.0 - origin.0 - size.0, panel.1 - origin.1 - size.1)
    } else {
        origin
    }
}

fn for_each_image_row(
    pixels: &[u8],
    size: (usize, usize),
    origin: (usize, usize),
    panel: (usize, usize),
    flipped: bool,
    dark_mode: bool,
    mut write: impl FnMut(usize, Range<usize>, &[u8]),
) {
    let (width, height) = size;
    let (x, y) = transformed_origin(panel, origin, size, flipped);
    let mode = GrayMode {
        dark_mode,
        black_and_white: false,
    };
    let mut transformed = if flipped || dark_mode {
        vec![0; width]
    } else {
        Vec::new()
    };
    for (row, pixels) in pixels.chunks_exact(width).take(height).enumerate() {
        if flipped || dark_mode {
            for (column, output) in transformed.iter_mut().enumerate() {
                let column = if flipped { width - column - 1 } else { column };
                *output = mode.grayscale(pixels[column]);
            }
            let row = if flipped {
                y + height - row - 1
            } else {
                y + row
            };
            write(row, x..x + width, &transformed);
        } else {
            write(y + row, x..x + width, pixels);
        }
    }
}

/// Owns the panel only until the normal Slint event loop starts.
///
/// The first frame is synchronous and independent of font decompression. Updates
/// are limited to 0.8 Hz and touch only the stage label and activity dots.
/// Call `pause` and join the worker before changing process environment variables.
pub struct StartupSplash {
    renderer: Option<Renderer>,
    stage: Arc<AtomicUsize>,
    stop: Option<mpsc::Sender<()>>,
    worker: Option<JoinHandle<Renderer>>,
}

impl StartupSplash {
    /// Show startup text on a display whose dimensions the application approved.
    pub fn show(
        images: StartupImages,
        approved_size: (u32, u32),
        dark_mode: bool,
    ) -> io::Result<Self> {
        let framebuffer = Framebuffer::open()?;
        if (framebuffer.width, framebuffer.height) != approved_size {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "framebuffer dimensions changed after launch approval",
            ));
        }
        let flipped = crate::platform::launch_render_offset() == 180;
        let mut renderer = Renderer::new(framebuffer, images, flipped, dark_mode)?;
        renderer.draw_initial(0);
        let mut splash = Self {
            renderer: Some(renderer),
            stage: Arc::new(AtomicUsize::new(0)),
            stop: None,
            worker: None,
        };
        splash.resume()?;
        Ok(splash)
    }

    /// Publish an actual startup phase; the next scheduled frame picks it up.
    pub fn set_stage(&self, stage: usize) {
        self.stage.store(stage.min(2), Ordering::Relaxed);
    }

    /// Stop and join the display worker, keeping the last frame on the panel.
    /// This must complete before calling the backend installer that changes env.
    pub fn pause(&mut self) -> io::Result<()> {
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(());
        }
        if let Some(worker) = self.worker.take() {
            self.renderer = Some(
                worker
                    .join()
                    .map_err(|_| io::Error::other("the startup display worker panicked"))?,
            );
        }
        Ok(())
    }

    /// Continue activity feedback after backend installation is complete.
    pub fn resume(&mut self) -> io::Result<()> {
        if self.worker.is_some() {
            return Ok(());
        }
        let Some(mut renderer) = self.renderer.take() else {
            return Err(io::Error::other(
                "the startup display renderer is unavailable",
            ));
        };
        let stage = self.stage.clone();
        let (stop, receiver) = mpsc::channel();
        let worker = std::thread::Builder::new()
            .name("weread-startup".into())
            .spawn(move || {
                loop {
                    let remaining = UPDATE_INTERVAL.saturating_sub(renderer.last_update.elapsed());
                    match receiver.recv_timeout(remaining) {
                        Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
                        Err(mpsc::RecvTimeoutError::Timeout) => {
                            renderer.update(stage.load(Ordering::Relaxed));
                        }
                    }
                }
                // Joining transfers the sole userspace writer; it does not wait
                // for the asynchronous panel refresh to physically complete.
                renderer
            })?;
        self.stop = Some(stop);
        self.worker = Some(worker);
        Ok(())
    }

    /// Join and release the framebuffer before the event loop takes ownership.
    pub fn finish(mut self) -> io::Result<()> {
        self.pause()?;
        drop(self.renderer.take());
        Ok(())
    }
}

impl Drop for StartupSplash {
    fn drop(&mut self) {
        let _ = self.pause();
    }
}

#[cfg(test)]
mod tests {
    use super::{GrayMode, StartupImage, for_each_image_row, transformed_origin};

    #[test]
    fn encoded_images_require_a_complete_bounded_grayscale_payload() {
        let image = StartupImage::from_encoded(&[2, 0, 0, 0, 1, 0, 0, 0, 0, 255]).unwrap();
        assert_eq!((image.width, image.height), (2, 1));
        assert_eq!(image.pixels, &[0, 255]);
        for bytes in [
            &[][..],
            &[2, 0, 0, 0, 1, 0, 0][..],
            &[0, 0, 0, 0, 1, 0, 0, 0][..],
            &[1, 8, 0, 0, 1, 0, 0, 0][..],
            &[1, 0, 0, 0, 1, 1, 0, 0][..],
            &[2, 0, 0, 0, 1, 0, 0, 0, 0][..],
            &[1, 0, 0, 0, 1, 0, 0, 0, 0, 255][..],
        ] {
            assert!(StartupImage::from_encoded(bytes).is_err());
        }
    }

    #[test]
    fn unrotated_blits_preserve_scanline_order_and_visible_bounds() {
        let mut output = vec![255; 7 * 8];
        for_each_image_row(
            &[1, 2, 3, 4, 5, 6],
            (2, 3),
            (1, 2),
            (7, 8),
            false,
            false,
            |row, columns, pixels| {
                assert!(row < 8 && columns.end <= 7);
                assert_eq!(columns.len(), pixels.len());
                output[row * 7 + columns.start..row * 7 + columns.end].copy_from_slice(pixels);
            },
        );
        let mut expected = vec![255; 7 * 8];
        expected[15..17].copy_from_slice(&[1, 2]);
        expected[22..24].copy_from_slice(&[3, 4]);
        expected[29..31].copy_from_slice(&[5, 6]);
        assert_eq!(output, expected);
    }

    #[test]
    fn rotated_blits_reverse_both_axes_and_refresh_the_same_rectangle() {
        let mut output = vec![255; 7 * 8];
        let (x, y) = transformed_origin((7, 8), (1, 2), (2, 3), true);
        assert_eq!((x, y), (4, 3));
        for_each_image_row(
            &[1, 2, 3, 4, 5, 6],
            (2, 3),
            (1, 2),
            (7, 8),
            true,
            false,
            |row, columns, pixels| {
                assert!((y..y + 3).contains(&row));
                assert_eq!(columns, x..x + 2);
                output[row * 7 + columns.start..row * 7 + columns.end].copy_from_slice(pixels);
            },
        );
        let mut expected = vec![255; 7 * 8];
        expected[25..27].copy_from_slice(&[6, 5]);
        expected[32..34].copy_from_slice(&[4, 3]);
        expected[39..41].copy_from_slice(&[2, 1]);
        assert_eq!(output, expected);
        // Odd centering gaps must transform the refresh origin as well as pixels.
        assert_eq!(transformed_origin((7, 8), (2, 2), (2, 3), true), (3, 3));
    }

    #[test]
    fn dark_blits_map_background_text_and_activity_shades_in_both_orientations() {
        let source = [0, 64, 127, 128, 170, 255];
        let original = source;
        let dark = GrayMode {
            dark_mode: true,
            black_and_white: false,
        };
        assert_eq!(dark.paper(), 0);
        for flipped in [false, true] {
            let mut output = vec![dark.paper(); 7 * 5];
            let mut draw = || {
                for_each_image_row(
                    &source,
                    (3, 2),
                    (1, 1),
                    (7, 5),
                    flipped,
                    true,
                    |row, columns, pixels| {
                        assert!(row < 5 && columns.end <= 7);
                        output[row * 7 + columns.start..row * 7 + columns.end]
                            .copy_from_slice(pixels);
                    },
                );
            };
            draw();
            draw();
            let mut expected = vec![0; 7 * 5];
            if flipped {
                expected[17..20].copy_from_slice(&[0, 85, 127]);
                expected[24..27].copy_from_slice(&[128, 191, 255]);
            } else {
                expected[8..11].copy_from_slice(&[255, 191, 128]);
                expected[15..18].copy_from_slice(&[127, 85, 0]);
            }
            assert_eq!(output, expected);
            assert_eq!(source, original);
        }
    }

    #[test]
    fn dark_activity_redraw_clears_previous_bright_pixels_from_canonical_white() {
        for flipped in [false, true] {
            let mut output = vec![0; 6 * 3];
            for source in [&[0, 170, 255, 255][..], &[255, 255, 0, 170][..]] {
                for_each_image_row(
                    source,
                    (4, 1),
                    (1, 1),
                    (6, 3),
                    flipped,
                    true,
                    |row, columns, pixels| {
                        output[row * 6 + columns.start..row * 6 + columns.end]
                            .copy_from_slice(pixels);
                    },
                );
            }
            let mut expected = vec![0; 6 * 3];
            expected[7..11].copy_from_slice(if flipped {
                &[85, 255, 0, 0]
            } else {
                &[0, 0, 255, 85]
            });
            assert_eq!(output, expected);
        }
    }
}
