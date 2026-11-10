//! A lossless grayscale target with a conservative RGB fallback.
//!
//! Slint's public target-pixel interface receives both opaque RGB colors and
//! premultiplied RGBA colors. A single channel exactly matches `Rgb8Pixel` while
//! every observed input is neutral. Non-neutral inputs invalidate that attempt:
//! the complete scene is rebuilt in RGB before any pixels can reach scanout.

use std::cell::Cell;

use slint::Rgb8Pixel;
use slint::platform::software_renderer::{
    PhysicalRegion, PremultipliedRgbaColor, RepaintBufferType, SoftwareRenderer, TargetPixel,
};

thread_local! {
    static NON_NEUTRAL_INPUT: Cell<bool> = const { Cell::new(false) };
}

fn observe_color(red: u8, green: u8, blue: u8) {
    // Neutral inputs avoid TLS entirely, including the per-pixel image path.
    if red != green || red != blue {
        NON_NEUTRAL_INPUT.set(true);
    }
}

struct ColorObservation {
    previous: bool,
}

impl ColorObservation {
    fn begin() -> Self {
        Self {
            previous: NON_NEUTRAL_INPUT.replace(false),
        }
    }

    fn saw_color(&self) -> bool {
        NON_NEUTRAL_INPUT.get()
    }
}

impl Drop for ColorObservation {
    fn drop(&mut self) {
        // Preserve an outer observation if rendering ever becomes reentrant.
        NON_NEUTRAL_INPUT.set(NON_NEUTRAL_INPUT.get() || self.previous);
    }
}

/// A canonical neutral channel, before thresholding or output inversion.
#[repr(transparent)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct Gray8Pixel(u8);

impl Gray8Pixel {
    pub(crate) fn value(self) -> u8 {
        self.0
    }
}

impl TargetPixel for Gray8Pixel {
    fn blend(&mut self, color: PremultipliedRgbaColor) {
        observe_color(color.red, color.green, color.blue);
        // Keep Slint 1.17.1's per-channel integer rounding exactly. A colored
        // candidate is deliberately discarded rather than published as luma.
        let inverse_alpha = u16::from(u8::MAX - color.alpha);
        self.0 = (u16::from(self.0) * inverse_alpha / 255) as u8 + color.red;
    }

    fn blend_slice(slice: &mut [Self], color: PremultipliedRgbaColor) {
        observe_color(color.red, color.green, color.blue);
        if color.alpha == u8::MAX {
            slice.fill(Self(color.red));
        } else {
            let inverse_alpha = u16::from(u8::MAX - color.alpha);
            for pixel in slice {
                pixel.0 = (u16::from(pixel.0) * inverse_alpha / 255) as u8 + color.red;
            }
        }
    }

    fn from_rgb(red: u8, green: u8, blue: u8) -> Self {
        observe_color(red, green, blue);
        Self(red)
    }
}

enum SceneStorage {
    Gray(Vec<Gray8Pixel>),
    Rgb(Vec<Rgb8Pixel>),
}

/// Read-only canonical pixels, safe to apply output transforms repeatedly.
#[derive(Clone, Copy)]
pub(crate) enum ScenePixels<'a> {
    Gray(&'a [Gray8Pixel]),
    Rgb(&'a [Rgb8Pixel]),
}

/// Owns the retained scene used for partial redraws and resume rewrites.
pub(crate) struct RetainedScene {
    storage: SceneStorage,
}

impl RetainedScene {
    /// Only monochrome 8-bpp scanout may opt into the grayscale target. Other
    /// framebuffer formats must keep the original RGB rendering path.
    pub(crate) fn new(pixel_count: usize, allow_gray8: bool) -> Self {
        Self {
            storage: if allow_gray8 {
                SceneStorage::Gray(vec![Gray8Pixel::default(); pixel_count])
            } else {
                SceneStorage::Rgb(vec![Rgb8Pixel::default(); pixel_count])
            },
        }
    }

    pub(crate) fn pixels(&self) -> ScenePixels<'_> {
        match &self.storage {
            SceneStorage::Gray(pixels) => ScenePixels::Gray(pixels),
            SceneStorage::Rgb(pixels) => ScenePixels::Rgb(pixels),
        }
    }

    /// Render once into the retained format. The first non-neutral write makes
    /// the RGB fallback permanent, rebuilding the complete scene immediately.
    /// The caller's repaint policy and rendering rotation remain in effect.
    /// Restoring Slint's repaint policy can conservatively invalidate its cache
    /// for the next requested frame; it does not request another frame itself.
    pub(crate) fn render(
        &mut self,
        renderer: &SoftwareRenderer,
        pixel_stride: usize,
    ) -> PhysicalRegion {
        let pixel_count = match &mut self.storage {
            SceneStorage::Rgb(pixels) => return renderer.render(pixels, pixel_stride),
            SceneStorage::Gray(pixels) => {
                let (region, saw_color) = {
                    let observation = ColorObservation::begin();
                    let region = renderer.render(pixels, pixel_stride);
                    (region, observation.saw_color())
                };
                if !saw_color {
                    return region;
                }
                pixels.len()
            }
        };

        // Release the rejected channel before allocating the RGB replacement.
        // Never reconstruct RGB from gray: that would lose colored content and
        // per-channel alpha rounding in parts outside the current dirty region.
        self.storage = SceneStorage::Rgb(Vec::new());
        let SceneStorage::Rgb(pixels) = &mut self.storage else {
            unreachable!();
        };
        pixels.resize(pixel_count, Rgb8Pixel::default());
        let _full_repaint = FullRepaint::new(renderer);
        renderer.render(pixels, pixel_stride)
    }
}

struct FullRepaint<'a> {
    renderer: &'a SoftwareRenderer,
    previous: RepaintBufferType,
}

impl<'a> FullRepaint<'a> {
    fn new(renderer: &'a SoftwareRenderer) -> Self {
        let previous = renderer.repaint_buffer_type();
        renderer.set_repaint_buffer_type(RepaintBufferType::NewBuffer);
        Self { renderer, previous }
    }
}

impl Drop for FullRepaint<'_> {
    fn drop(&mut self) {
        self.renderer.set_repaint_buffer_type(self.previous);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn neutral_blending_matches_rgb_for_every_valid_channel_and_alpha() {
        let observation = ColorObservation::begin();
        for background in 0..=u8::MAX {
            for alpha in 0..=u8::MAX {
                for source in 0..=alpha {
                    let color = PremultipliedRgbaColor {
                        red: source,
                        green: source,
                        blue: source,
                        alpha,
                    };
                    let mut gray = Gray8Pixel(background);
                    let mut rgb = Rgb8Pixel::new(background, background, background);
                    gray.blend(color);
                    rgb.blend(color);
                    assert_eq!(
                        rgb,
                        Rgb8Pixel::new(gray.value(), gray.value(), gray.value())
                    );
                }
            }
        }
        assert!(!observation.saw_color());
    }

    #[test]
    fn opaque_and_slice_inputs_observe_color_before_the_candidate_is_exposed() {
        let observation = ColorObservation::begin();
        let mut pixels = [Gray8Pixel::from_rgb(77, 77, 77); 3];
        Gray8Pixel::blend_slice(
            &mut pixels,
            PremultipliedRgbaColor {
                red: 18,
                green: 18,
                blue: 18,
                alpha: 50,
            },
        );
        assert!(!observation.saw_color());
        let _candidate = Gray8Pixel::from_rgb(255, 0, 0);
        assert!(observation.saw_color());
        drop(observation);

        let observation = ColorObservation::begin();
        Gray8Pixel::blend_slice(
            &mut pixels,
            PremultipliedRgbaColor {
                red: 18,
                green: 17,
                blue: 18,
                alpha: 50,
            },
        );
        assert!(observation.saw_color());
    }

    #[test]
    fn colored_scalar_compositing_is_not_a_lossless_replacement_for_rgb() {
        let mut rgb = Rgb8Pixel::new(255, 0, 0);
        let luma = |pixel: Rgb8Pixel| {
            ((77 * u32::from(pixel.r) + 150 * u32::from(pixel.g) + 29 * u32::from(pixel.b)) >> 8)
                as u8
        };
        let scalar_before_blending = luma(rgb);
        rgb.blend(PremultipliedRgbaColor {
            red: 0,
            green: 0,
            blue: 0,
            alpha: 128,
        });
        assert_eq!(luma(rgb), 38);
        assert_eq!(u16::from(scalar_before_blending) * 127 / 255, 37);
    }

    #[test]
    fn storage_selection_uses_one_byte_only_when_explicitly_allowed() {
        assert_eq!(std::mem::size_of::<Gray8Pixel>(), 1);
        assert_eq!(std::mem::size_of::<Rgb8Pixel>(), 3);
        match RetainedScene::new(7, true).pixels() {
            ScenePixels::Gray(pixels) => assert_eq!(pixels.len(), 7),
            ScenePixels::Rgb(_) => panic!("the grayscale target should be selected"),
        }
        match RetainedScene::new(7, false).pixels() {
            ScenePixels::Rgb(pixels) => assert_eq!(pixels.len(), 7),
            ScenePixels::Gray(_) => panic!("the RGB target should be selected"),
        }
    }
}
