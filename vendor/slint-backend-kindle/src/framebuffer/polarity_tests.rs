//! Canonical luma encoding through the real anonymous-mapped write boundary.

use super::{
    Framebuffer, FramebufferLayout, NativeFramebufferObservation,
    ffi::{FbFixScreeninfo, FbVarScreeninfo},
    gray8_is_inverted,
};
use crate::{
    gray_target::{Gray8Pixel, ScenePixels},
    refresh::PhysicalRect,
    render_control::{GrayMode, GrayRenderer},
};
use slint::{Rgb8Pixel, platform::software_renderer::TargetPixel};

const UNTOUCHED: u8 = 0x31;

fn observation(
    width: u32,
    height: u32,
    stride: usize,
    bytes_per_pixel: usize,
    grayscale: u32,
) -> NativeFramebufferObservation {
    let variable = FbVarScreeninfo {
        xres: width,
        yres: height,
        xres_virtual: width,
        yres_virtual: height,
        bits_per_pixel: (bytes_per_pixel * 8) as u32,
        grayscale,
        ..Default::default()
    };
    let fixed = FbFixScreeninfo {
        line_length: stride as u32,
        smem_len: (stride * height as usize) as u32,
        ..Default::default()
    };
    NativeFramebufferObservation {
        rotation: variable.rotate,
        layout: FramebufferLayout::from_information(&variable, &fixed).unwrap(),
        inverted_gray8: gray8_is_inverted(&variable),
    }
}

fn memory(
    width: u32,
    height: u32,
    stride: usize,
    bytes_per_pixel: usize,
    grayscale: u32,
) -> (Framebuffer, NativeFramebufferObservation) {
    let observed = observation(width, height, stride, bytes_per_pixel, grayscale);
    let mut framebuffer =
        Framebuffer::memory_for_test(width, height, stride, bytes_per_pixel, UNTOUCHED).unwrap();
    framebuffer.retained_layout = Some(observed.layout);
    framebuffer.inverted_gray8.set(observed.inverted_gray8);
    (framebuffer, observed)
}

#[test]
fn gray8_scanout_keeps_all_levels_and_stride_padding() {
    let source = (0..=u8::MAX).collect::<Vec<_>>();
    let original = source.clone();
    for grayscale in [0, 1, 2, 3] {
        let (mut framebuffer, _) = memory(260, 2, 264, 1, grayscale);
        framebuffer.write_line(1, 2..258, &source);
        let expected = source
            .iter()
            .map(|&value| if grayscale == 2 { 255 - value } else { value })
            .collect::<Vec<_>>();
        let first = framebuffer.mapped_bytes_for_test().to_vec();
        assert_eq!(&first[266..522], expected.as_slice());
        assert!(first[..266].iter().all(|value| *value == UNTOUCHED));
        assert!(first[522..].iter().all(|value| *value == UNTOUCHED));

        framebuffer.write_line(1, 2..258, &source);
        assert_eq!(framebuffer.mapped_bytes_for_test(), first.as_slice());
        assert_eq!(
            source, original,
            "The canonical source cannot be inverted in place"
        );
    }
}

#[test]
fn software_night_and_native_polarity_compose_without_mutating_the_scene() {
    let rgb = (0..=u8::MAX)
        .map(|value| Rgb8Pixel::new(value, value, value))
        .collect::<Vec<_>>();
    let gray = rgb
        .iter()
        .map(|pixel| Gray8Pixel::from_rgb(pixel.r, pixel.g, pixel.b))
        .collect::<Vec<_>>();
    let original_rgb = rgb.clone();
    let original_gray = gray.clone();
    let full = PhysicalRect {
        x: 0,
        y: 0,
        width: 256,
        height: 1,
    };
    let one_pixel = PhysicalRect { width: 1, ..full };
    for grayscale in [1, 2] {
        for use_gray_scene in [false, true] {
            let (mut framebuffer, _) = memory(256, 1, 259, 1, grayscale);
            let mut renderer = GrayRenderer::new(256, GrayMode::default());
            for (index, dark_mode) in [false, true, false].into_iter().enumerate() {
                let mode = GrayMode {
                    dark_mode,
                    black_and_white: false,
                };
                let scene = if use_gray_scene {
                    ScenePixels::Gray(&gray)
                } else {
                    ScenePixels::Rgb(&rgb)
                };
                let written = renderer.write_scene_frame(
                    scene,
                    (256, 1),
                    Some(if index == 0 { full } else { one_pixel }),
                    mode,
                    |row, columns, pixels| framebuffer.write_line(row, columns, pixels),
                );
                assert_eq!(written.region, Some(full));
                assert_eq!(written.mode_changed, index != 0);
                for (value, &native) in framebuffer.mapped_bytes_for_test()[..256]
                    .iter()
                    .enumerate()
                {
                    let software = if dark_mode {
                        255 - value as u8
                    } else {
                        value as u8
                    };
                    let expected_native = if grayscale == 2 {
                        255 - software
                    } else {
                        software
                    };
                    assert_eq!(native, expected_native);
                    let decoded = if grayscale == 2 { 255 - native } else { native };
                    assert_eq!(decoded, software);
                }
                assert!(
                    framebuffer.mapped_bytes_for_test()[256..]
                        .iter()
                        .all(|value| *value == UNTOUCHED)
                );
                let previous = framebuffer.mapped_bytes_for_test().to_vec();
                renderer.write_scene_frame(
                    scene,
                    (256, 1),
                    Some(full),
                    mode,
                    |row, columns, pixels| framebuffer.write_line(row, columns, pixels),
                );
                assert_eq!(framebuffer.mapped_bytes_for_test(), previous.as_slice());
            }
        }
    }
    assert_eq!(rgb, original_rgb);
    assert_eq!(gray, original_gray);
}

#[test]
fn rgb_fallback_encodes_native_polarity_after_luma_conversion() {
    let rgb = [
        Rgb8Pixel::new(0, 0, 0),
        Rgb8Pixel::new(255, 255, 255),
        Rgb8Pixel::new(255, 0, 0),
        Rgb8Pixel::new(0, 255, 0),
        Rgb8Pixel::new(0, 0, 255),
        Rgb8Pixel::new(70, 140, 210),
    ];
    let canonical_luma = [0_u8, 255, 76, 149, 28, 126];
    let original = rgb;
    for bytes_per_pixel in [1, 4] {
        for grayscale in [1, 2] {
            for dark_mode in [false, true] {
                let stride = 6 * bytes_per_pixel + 3;
                let (mut framebuffer, _) = memory(6, 1, stride, bytes_per_pixel, grayscale);
                let mode = GrayMode {
                    dark_mode,
                    black_and_white: false,
                };
                let mut renderer = GrayRenderer::new(6, GrayMode::default());
                renderer.write_frame(
                    &rgb,
                    (6, 1),
                    Some(PhysicalRect {
                        x: 0,
                        y: 0,
                        width: 6,
                        height: 1,
                    }),
                    mode,
                    |row, columns, pixels| framebuffer.write_line(row, columns, pixels),
                );
                for (index, value) in canonical_luma.into_iter().enumerate() {
                    let software = if dark_mode { 255 - value } else { value };
                    let native = if bytes_per_pixel == 1 && grayscale == 2 {
                        255 - software
                    } else {
                        software
                    };
                    let offset = index * bytes_per_pixel;
                    let pixel =
                        &framebuffer.mapped_bytes_for_test()[offset..offset + bytes_per_pixel];
                    if bytes_per_pixel == 1 {
                        assert_eq!(pixel, [native]);
                    } else {
                        assert_eq!(pixel, [native, native, native, 255]);
                    }
                }
                assert!(
                    framebuffer.mapped_bytes_for_test()[6 * bytes_per_pixel..]
                        .iter()
                        .all(|value| *value == UNTOUCHED)
                );
            }
        }
    }
    assert_eq!(rgb, original);
}

#[test]
fn fill_encodes_gray8_and_preserves_rgba_and_stride_padding() {
    for bytes_per_pixel in [1, 4] {
        for grayscale in [1, 2, 3] {
            let stride = 3 * bytes_per_pixel + 3;
            let (mut framebuffer, _) = memory(3, 2, stride, bytes_per_pixel, grayscale);
            for value in [0, 17, 127, 128, 255] {
                framebuffer.fill(value);
                let native = if bytes_per_pixel == 1 && grayscale == 2 {
                    255 - value
                } else {
                    value
                };
                for row in framebuffer.mapped_bytes_for_test().chunks_exact(stride) {
                    for pixel in row[..3 * bytes_per_pixel].chunks_exact(bytes_per_pixel) {
                        if bytes_per_pixel == 1 {
                            assert_eq!(pixel, [native]);
                        } else {
                            assert_eq!(pixel, [value, value, value, 255]);
                        }
                    }
                    assert!(
                        row[3 * bytes_per_pixel..]
                            .iter()
                            .all(|value| *value == UNTOUCHED)
                    );
                }
            }
        }
    }
}

#[test]
fn matched_layout_observations_keep_the_current_scanout_encoding() {
    for grayscale in [1, 2] {
        let (mut framebuffer, anchor) = memory(3, 1, 6, 1, grayscale);
        let observed = NativeFramebufferObservation {
            rotation: 3,
            ..anchor
        };
        // A matching current observation, rather than a stale cached output
        // interpretation, must determine the next scanout encoding.
        framebuffer.inverted_gray8.set(grayscale != 2);
        assert_eq!(
            framebuffer
                .observe_native_rotation_with(anchor, || Ok(observed))
                .unwrap(),
            observed,
        );
        framebuffer.write_line(0, 0..3, &[0, 127, 255]);
        assert_eq!(
            &framebuffer.mapped_bytes_for_test()[..3],
            if grayscale == 2 {
                &[255, 128, 0]
            } else {
                &[0, 127, 255]
            },
        );
    }
}

#[test]
fn changed_grayscale_cannot_rebind_the_mapping_or_bypass_its_anchor() {
    for grayscale in [1, 2] {
        let (framebuffer, anchor) = memory(3, 1, 6, 1, grayscale);
        let next_grayscale = if grayscale == 1 { 2 } else { 1 };
        let changed = observation(3, 1, 6, 1, next_grayscale);
        assert_ne!(anchor.layout_token(), changed.layout_token());
        for requested_anchor in [anchor, changed] {
            let error = framebuffer
                .observe_native_rotation_with(requested_anchor, || Ok(changed))
                .unwrap_err();
            assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
            assert_eq!(framebuffer.inverted_gray8.get(), grayscale == 2);
        }
        let stale_anchor = framebuffer
            .observe_native_rotation_with(changed, || Ok(anchor))
            .unwrap_err();
        assert_eq!(stale_anchor.kind(), std::io::ErrorKind::InvalidData);
        assert_eq!(framebuffer.inverted_gray8.get(), grayscale == 2);
        let failed = framebuffer
            .observe_native_rotation_with(anchor, || {
                Err(std::io::Error::from_raw_os_error(libc::EIO))
            })
            .unwrap_err();
        assert_eq!(failed.raw_os_error(), Some(libc::EIO));
        assert_eq!(framebuffer.inverted_gray8.get(), grayscale == 2);
        assert!(
            framebuffer
                .mapped_bytes_for_test()
                .iter()
                .all(|value| *value == UNTOUCHED)
        );

        let (mut reopened, new_anchor) = memory(3, 1, 6, 1, next_grayscale);
        reopened
            .observe_native_rotation_with(new_anchor, || Ok(changed))
            .unwrap();
        reopened.fill(255);
        assert_eq!(
            &reopened.mapped_bytes_for_test()[..3],
            if next_grayscale == 2 {
                &[0, 0, 0]
            } else {
                &[255, 255, 255]
            },
        );
    }
}
