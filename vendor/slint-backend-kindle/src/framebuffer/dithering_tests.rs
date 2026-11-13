//! Controlled display fixtures. No native framebuffer or identity is opened.

use std::cell::Cell;
use std::io;

use super::ffi::{
    EPDC_FLAG_FORCE_MONOCHROME, MTK_EPDC_FLAG_USE_DITHERING_Y4, UPDATE_MODE_FULL,
    UPDATE_MODE_PARTIAL, UpdateRequestMtk, WAVEFORM_MODE_A2_MODERN, WAVEFORM_MODE_AUTO,
    WAVEFORM_MODE_DU, WAVEFORM_MODE_GC16,
};
use super::*;
use crate::gray_target::Gray8Pixel;
use crate::refresh::PhysicalRect;
use crate::render_control::GrayRenderer;
use slint::Rgb8Pixel;
use slint::platform::software_renderer::TargetPixel;

const SENTINEL: u8 = 0x5a;

fn rect(left: u32, top: u32, width: u32, height: u32) -> UpdateRect {
    UpdateRect {
        left,
        top,
        width,
        height,
    }
}

fn rgb(value: u8) -> Rgb8Pixel {
    Rgb8Pixel::new(value, value, value)
}

fn raw_framebuffer(width: u32, height: u32, stride: usize) -> (Framebuffer, DitheringHandle) {
    let mut framebuffer = Framebuffer::memory_for_test(width, height, stride, 1, SENTINEL).unwrap();
    let handle = DitheringHandle::for_test(true);
    assert!(handle.set_hardware_y4(true));
    framebuffer.set_dithering(handle.clone(), false);
    (framebuffer, handle)
}

fn draw(framebuffer: &mut Framebuffer, scene: ScenePixels<'_>, mode: GrayMode) {
    let mut renderer = GrayRenderer::new(framebuffer.width as usize, mode);
    renderer.write_scene_frame(
        scene,
        (framebuffer.width, framebuffer.height),
        Some(PhysicalRect {
            x: 0,
            y: 0,
            width: framebuffer.width,
            height: framebuffer.height,
        }),
        mode,
        |row, columns, pixels| framebuffer.write_line(row, columns, pixels),
    );
}

#[test]
fn mtk_y4_has_a_distinct_flag_and_zero_epdcv2_dither_fields() {
    let region = rect(8, 16, 24, 32);
    let update = mtk_update(region, WAVEFORM_MODE_GC16, UPDATE_MODE_PARTIAL, 7, true);
    assert_eq!(std::mem::size_of::<UpdateRequestMtk>(), 96);
    assert_eq!(MTK_EPDC_FLAG_USE_DITHERING_Y4, 0x4000);
    assert_ne!(MTK_EPDC_FLAG_USE_DITHERING_Y4, 0x8000);
    assert_eq!(update.flags, 0x4000);
    assert_eq!((update.dither_mode, update.quant_bit), (0, 0));
    assert_eq!(
        (update.hist_bw_waveform_mode, update.hist_gray_waveform_mode),
        (1, 2)
    );
    assert_eq!(
        (update.update_mode, update.update_marker),
        (UPDATE_MODE_PARTIAL, 7)
    );
    assert_eq!(
        mtk_update(region, WAVEFORM_MODE_DU, 0, 8, true).flags,
        EPDC_FLAG_FORCE_MONOCHROME
    );
    assert_eq!(
        mtk_update(region, WAVEFORM_MODE_A2_MODERN, 0, 8, true).flags,
        0
    );
    assert_eq!(mtk_update(region, WAVEFORM_MODE_GC16, 0, 8, false).flags, 0);
}

#[test]
fn hardware_regions_expand_to_eight_pixels_and_clip_at_the_panel() {
    let region = aligned_dither_region(rect(9, 17, 7, 8), 19, 27);
    assert_eq!(
        (region.left, region.top, region.width, region.height),
        (8, 16, 8, 11)
    );
    let edge = aligned_dither_region(rect(18, 26, u32::MAX, u32::MAX), 19, 27);
    assert_eq!(
        (edge.left, edge.top, edge.width, edge.height),
        (16, 24, 3, 3)
    );
    let outside = aligned_dither_region(rect(100, 100, 2, 2), 19, 27);
    assert_eq!((outside.width, outside.height), (0, 0));
}

#[test]
fn native_hardware_metadata_requires_an_explicit_gray8_format() {
    for (depth, grayscale, expected) in [
        (8, 1, true),
        (8, 2, true),
        (8, 0, false),
        (8, 3, false),
        (32, 1, false),
        (32, 0, false),
    ] {
        let variable = FbVarScreeninfo {
            xres: 8,
            yres: 8,
            xres_virtual: 8,
            yres_virtual: 8,
            bits_per_pixel: depth,
            grayscale,
            ..Default::default()
        };
        let fixed = FbFixScreeninfo {
            line_length: 32,
            smem_len: 256,
            ..Default::default()
        };
        let observation = NativeFramebufferObservation {
            rotation: 0,
            layout: FramebufferLayout::from_information(&variable, &fixed).unwrap(),
            inverted_gray8: gray8_is_inverted(&variable),
        };
        assert_eq!(observation.supports_hardware_y4(), expected);
    }
}

#[test]
fn hardware_payload_keeps_every_gray8_input_for_gray_and_rgb_scenes() {
    let values: Vec<_> = (0..=255).collect();
    let rgb: Vec<_> = values.iter().copied().map(rgb).collect();
    let gray: Vec<_> = values
        .iter()
        .map(|value| Gray8Pixel::from_rgb(*value, *value, *value))
        .collect();
    for scene in [ScenePixels::Rgb(&rgb), ScenePixels::Gray(&gray)] {
        for dark_mode in [false, true] {
            for native_inverted in [false, true] {
                let (mut framebuffer, _) = raw_framebuffer(256, 1, 259);
                framebuffer.inverted_gray8.set(native_inverted);
                let mode = GrayMode {
                    dark_mode,
                    black_and_white: false,
                };
                draw(&mut framebuffer, scene, mode);
                for (x, value) in values.iter().enumerate() {
                    let logical = mode.grayscale(*value);
                    assert_eq!(
                        framebuffer.mapped_bytes_for_test()[x],
                        encode_native_gray(logical, native_inverted)
                    );
                }
                assert_eq!(&framebuffer.mapped_bytes_for_test()[256..], &[SENTINEL; 3]);
                let receipt = framebuffer
                    .send_update_using(
                        rect(0, 0, 256, 1),
                        WAVEFORM_MODE_DU,
                        UPDATE_MODE_PARTIAL,
                        Some(FallbackScene {
                            pixels: scene,
                            mode,
                        }),
                        |variant, region, waveform, _, marker, y4| {
                            assert_eq!(
                                (variant, waveform, marker, y4),
                                (UpdateVariant::Mtk, WAVEFORM_MODE_GC16, 1, true)
                            );
                            assert_eq!((region.width, region.height), (256, 1));
                            Ok(())
                        },
                        |_| panic!("An accepted hardware update needs no fallback barrier"),
                    )
                    .unwrap();
                let status = framebuffer.accepted_dithering(receipt.marker()).unwrap();
                assert!(
                    status.hardware_y4_available
                        && status.hardware_y4_requested
                        && status.hardware_y4_applied
                );
                assert!(!status.software_dither_fallback && !status.dithering_delivery_uncertain);
                assert_eq!(framebuffer.accepted_dithering(2), None);
            }
        }
    }
}

#[test]
fn the_frozen_payload_ignores_request_changes_from_a_write_observer() {
    let (mut framebuffer, handle) = raw_framebuffer(19, 27, 24);
    framebuffer.write_line(17, 9..16, &[110; 7]);
    handle.set_hardware_y4(false);
    let receipt = framebuffer
        .send_update_using(
            rect(9, 17, 7, 8),
            WAVEFORM_MODE_A2_MODERN,
            UPDATE_MODE_PARTIAL,
            None,
            |variant, region, waveform, _, _, y4| {
                assert_eq!(variant, UpdateVariant::Mtk);
                assert_eq!(
                    (region.left, region.top, region.width, region.height),
                    (8, 16, 8, 11)
                );
                assert_eq!(waveform, WAVEFORM_MODE_GC16);
                assert!(y4);
                assert_eq!(
                    &framebuffer.mapped_bytes_for_test()[17 * 24 + 9..17 * 24 + 16],
                    &[110; 7]
                );
                Ok(())
            },
            |_| panic!("A changed producer request cannot reinterpret this frame"),
        )
        .unwrap();
    assert!(
        framebuffer
            .accepted_dithering(receipt.marker())
            .unwrap()
            .hardware_y4_requested
    );
    drop(receipt);
    framebuffer.set_dithering(handle, false);
    assert!(!framebuffer.current_dithering_status().hardware_y4_requested);
}

#[test]
fn all_binary_waveforms_are_promoted_while_raw_pixels_are_retained() {
    let (framebuffer, _) = raw_framebuffer(19, 27, 24);
    for waveform in [
        RefreshWaveform::Auto,
        RefreshWaveform::Du,
        RefreshWaveform::A2,
        RefreshWaveform::Gc16,
    ] {
        let resolved = framebuffer.resolve_dithering(ResolvedRefresh {
            region: PhysicalRect {
                x: 9,
                y: 17,
                width: 7,
                height: 8,
            },
            waveform,
            flashing: false,
        });
        assert_eq!(resolved.waveform, RefreshWaveform::Gc16);
        assert_eq!(
            resolved.region,
            PhysicalRect {
                x: 8,
                y: 16,
                width: 8,
                height: 11
            }
        );
    }
}

#[test]
fn explicit_rejection_fences_then_rewrites_the_canonical_scene_with_a_fresh_marker() {
    let rgb = vec![rgb(110); 33];
    let gray = vec![Gray8Pixel::from_rgb(110, 110, 110); 33];
    for scene in [ScenePixels::Rgb(&rgb), ScenePixels::Gray(&gray)] {
        for dark_mode in [false, true] {
            for native_inverted in [false, true] {
                let (mut framebuffer, handle) = raw_framebuffer(11, 3, 16);
                framebuffer.inverted_gray8.set(native_inverted);
                let mode = GrayMode {
                    dark_mode,
                    black_and_white: false,
                };
                draw(&mut framebuffer, scene, mode);
                // A stale mapped value must not become the fallback's source.
                framebuffer.write_line(0, 0..1, &[42]);
                framebuffer.update_variant.set(Some(UpdateVariant::Mtk));
                let preceding = framebuffer
                    .submit_update_checked_with(|_, marker| {
                        assert_eq!(marker, 1);
                        Ok(())
                    })
                    .unwrap();
                let stage = Cell::new(0);
                let receipt = framebuffer
                    .send_update_using(
                        rect(3, 1, 2, 1),
                        WAVEFORM_MODE_GC16,
                        UPDATE_MODE_PARTIAL,
                        Some(FallbackScene {
                            pixels: scene,
                            mode,
                        }),
                        |variant, region, waveform, update_mode, marker, y4| {
                            assert_eq!(
                                (variant, waveform),
                                (UpdateVariant::Mtk, WAVEFORM_MODE_GC16)
                            );
                            if y4 {
                                assert_eq!((stage.get(), marker), (0, 2));
                                stage.set(1);
                                Err(io::Error::from_raw_os_error(libc::EINVAL))
                            } else {
                                assert_eq!(
                                    (stage.get(), marker, update_mode),
                                    (2, 3, UPDATE_MODE_FULL)
                                );
                                stage.set(3);
                                assert_eq!(
                                    (region.left, region.top, region.width, region.height),
                                    (0, 0, 11, 3)
                                );
                                for y in 0..3 {
                                    for x in 0..11 {
                                        let expected = software_gray(mode.grayscale(110), x, y);
                                        assert_eq!(
                                            framebuffer.mapped_bytes_for_test()[y * 16 + x],
                                            encode_native_gray(expected, native_inverted)
                                        );
                                    }
                                    assert_eq!(
                                        &framebuffer.mapped_bytes_for_test()
                                            [y * 16 + 11..(y + 1) * 16],
                                        &[SENTINEL; 5]
                                    );
                                }
                                Ok(())
                            }
                        },
                        |marker| {
                            assert_eq!((stage.get(), marker), (1, preceding.marker()));
                            assert_eq!(framebuffer.last_submitted_marker(), Some(1));
                            assert_eq!(
                                framebuffer.mapped_bytes_for_test()[0],
                                encode_native_gray(42, native_inverted)
                            );
                            stage.set(2);
                            Ok(())
                        },
                    )
                    .unwrap();
                assert_eq!(stage.get(), 3);
                assert_eq!(receipt.marker(), 3);
                assert_eq!(framebuffer.last_submitted_marker(), Some(3));
                assert_eq!(framebuffer.canonical_rewrite_count(), 1);
                let status = framebuffer.accepted_dithering(3).unwrap();
                assert!(!status.hardware_y4_available && status.hardware_y4_requested);
                assert!(!status.hardware_y4_applied && status.software_dither_fallback);
                assert!(framebuffer.accepted_frame_was_rewritten(3));
                assert!(!handle.hardware_y4_available() && handle.requested());
                framebuffer
                    .wait_for_submission_complete_with(&receipt, |marker| {
                        assert_eq!(marker, 3);
                        Ok(())
                    })
                    .unwrap();
                assert!(
                    framebuffer
                        .wait_for_submission_complete_with(&preceding, |_| {
                            panic!("An older accepted marker cannot complete the fallback frame")
                        })
                        .is_err()
                );
            }
        }
    }
}

fn assert_future_scene_is_blocked(framebuffer: &mut Framebuffer, handle: &DitheringHandle) {
    let bytes = framebuffer.mapped_bytes_for_test().to_vec();
    let marker = framebuffer.last_submitted_marker();
    handle.set_hardware_y4(false);
    handle.set_hardware_y4(true);
    framebuffer.set_dithering(handle.clone(), false);
    framebuffer.write_line(0, 0..4, &[0; 4]);
    framebuffer.fill(0);
    assert_eq!(framebuffer.mapped_bytes_for_test(), bytes);
    assert!(handle.dithering_delivery_uncertain());
    assert!(framebuffer.require_known_dithering_delivery().is_err());
    assert!(
        framebuffer
            .wait_for_pending_update_complete_checked()
            .is_err()
    );
    assert!(framebuffer.wait_for_update_complete_checked().is_err());
    assert!(framebuffer.refresh_full().is_err());
    assert!(framebuffer.refresh_full_checked().is_err());
    assert!(
        framebuffer
            .refresh_region(
                slint::PhysicalPosition::new(0, 0),
                slint::PhysicalSize::new(4, 1)
            )
            .is_err()
    );
    assert!(
        framebuffer
            .submit_update_checked_with(|_, _| {
                panic!("A low-level submission cannot bypass uncertain delivery")
            })
            .is_err()
    );
    let scene = [rgb(110); 4];
    assert!(
        framebuffer
            .rewrite_software_scene(FallbackScene {
                pixels: ScenePixels::Rgb(&scene),
                mode: GrayMode::default(),
            })
            .is_err()
    );
    assert!(
        framebuffer
            .send_update_using(
                rect(0, 0, 4, 1),
                WAVEFORM_MODE_GC16,
                UPDATE_MODE_FULL,
                None,
                |_, _, _, _, _, _| panic!("A later iteration cannot resubmit an uncertain update"),
                |_| panic!("A later iteration cannot retry the fallback barrier"),
            )
            .is_err()
    );
    assert_eq!(framebuffer.mapped_bytes_for_test(), bytes);
    assert_eq!(framebuffer.last_submitted_marker(), marker);
}

#[test]
fn uncertain_send_errors_never_rewrite_retry_or_acknowledge_an_old_marker() {
    for errno in [
        Some(libc::EIO),
        Some(libc::EINTR),
        Some(libc::ETIMEDOUT),
        None,
    ] {
        let (mut framebuffer, handle) = raw_framebuffer(4, 1, 7);
        framebuffer.write_line(0, 0..4, &[110; 4]);
        let bytes = framebuffer.mapped_bytes_for_test().to_vec();
        let calls = Cell::new(0);
        assert!(
            framebuffer
                .send_update_using(
                    rect(0, 0, 4, 1),
                    WAVEFORM_MODE_GC16,
                    UPDATE_MODE_PARTIAL,
                    None,
                    |variant, _, _, _, marker, y4| {
                        assert_eq!((variant, marker, y4), (UpdateVariant::Mtk, 1, true));
                        calls.set(calls.get() + 1);
                        Err(errno
                            .map(io::Error::from_raw_os_error)
                            .unwrap_or_else(|| io::Error::other("unknown delivery")))
                    },
                    |_| panic!("Uncertain SEND must not enter fallback"),
                )
                .is_err()
        );
        assert_eq!(calls.get(), 1);
        assert_eq!(framebuffer.last_submitted_marker(), None);
        assert_eq!(framebuffer.mapped_bytes_for_test(), bytes);
        assert_eq!(framebuffer.canonical_rewrite_count(), 0);
        assert_future_scene_is_blocked(&mut framebuffer, &handle);
    }
}

#[test]
fn a_failed_fallback_barrier_preserves_raw_pixels_and_the_preceding_marker() {
    let (mut framebuffer, handle) = raw_framebuffer(4, 1, 7);
    framebuffer.write_line(0, 0..4, &[110; 4]);
    framebuffer.update_variant.set(Some(UpdateVariant::Mtk));
    framebuffer
        .submit_update_checked_with(|_, _| Ok(()))
        .unwrap();
    let bytes = framebuffer.mapped_bytes_for_test().to_vec();
    let calls = Cell::new(0);
    let scene = [rgb(110); 4];
    assert!(
        framebuffer
            .send_update_using(
                rect(0, 0, 4, 1),
                WAVEFORM_MODE_GC16,
                UPDATE_MODE_PARTIAL,
                Some(FallbackScene {
                    pixels: ScenePixels::Rgb(&scene),
                    mode: GrayMode::default()
                }),
                |_, _, _, _, marker, y4| {
                    assert_eq!(marker, 2);
                    assert!(y4);
                    calls.set(calls.get() + 1);
                    Err(io::Error::from_raw_os_error(libc::EINVAL))
                },
                |marker| {
                    assert_eq!(marker, 1);
                    Err(io::Error::from_raw_os_error(libc::EIO))
                },
            )
            .is_err()
    );
    assert_eq!(calls.get(), 1);
    assert_eq!(framebuffer.mapped_bytes_for_test(), bytes);
    assert_eq!(framebuffer.last_submitted_marker(), Some(1));
    assert!(!handle.hardware_y4_available() && handle.requested());
    let status = framebuffer.current_dithering_status();
    assert!(status.dithering_delivery_uncertain);
    assert!(!status.software_dither_fallback);
    assert_future_scene_is_blocked(&mut framebuffer, &handle);
}

#[test]
fn a_raw_completion_failure_latches_after_the_producer_replaces_its_policy() {
    let (mut framebuffer, handle) = raw_framebuffer(4, 1, 7);
    framebuffer.write_line(0, 0..4, &[110; 4]);
    let marker = framebuffer
        .send_update_using(
            rect(0, 0, 4, 1),
            WAVEFORM_MODE_GC16,
            UPDATE_MODE_FULL,
            None,
            |_, _, _, _, _, _| Ok(()),
            |_| panic!("An accepted raw frame needs no fallback"),
        )
        .unwrap()
        .marker();
    handle.set_hardware_y4(false);
    framebuffer.set_dithering(handle.clone(), false);
    assert!(!framebuffer.raw_scene());
    assert!(
        framebuffer
            .wait_for_accepted_with(marker, |target| {
                assert_eq!(target, marker);
                Err(io::Error::from_raw_os_error(libc::ETIMEDOUT))
            })
            .is_err()
    );
    assert!(framebuffer.wait_for_native_handoff().is_err());
    assert_future_scene_is_blocked(&mut framebuffer, &handle);
}

#[test]
fn definite_rejection_requires_a_canonical_scene_before_any_rewrite() {
    let (mut framebuffer, handle) = raw_framebuffer(4, 1, 7);
    framebuffer.write_line(0, 0..4, &[110; 4]);
    let bytes = framebuffer.mapped_bytes_for_test().to_vec();
    assert_eq!(
        framebuffer
            .send_update_using(
                rect(0, 0, 4, 1),
                WAVEFORM_MODE_GC16,
                UPDATE_MODE_PARTIAL,
                None,
                |_, _, _, _, _, _| Err(io::Error::from_raw_os_error(libc::EINVAL)),
                |_| panic!("No earlier accepted frame exists"),
            )
            .unwrap_err()
            .kind(),
        io::ErrorKind::InvalidInput
    );
    assert_eq!(framebuffer.mapped_bytes_for_test(), bytes);
    assert_eq!(framebuffer.canonical_rewrite_count(), 0);
    assert!(!handle.hardware_y4_available() && handle.requested());
    assert!(!handle.dithering_delivery_uncertain());
    assert!(
        !framebuffer
            .current_dithering_status()
            .software_dither_fallback
    );
}

#[test]
fn a_rejected_mtk_abi_only_probes_other_abis_after_canonical_software_conversion() {
    let (mut framebuffer, handle) = raw_framebuffer(4, 1, 7);
    framebuffer.write_line(0, 0..4, &[110; 4]);
    let scene = [rgb(110); 4];
    let calls = Cell::new(0);
    let receipt = framebuffer
        .send_update_using(
            rect(0, 0, 4, 1),
            WAVEFORM_MODE_GC16,
            UPDATE_MODE_PARTIAL,
            Some(FallbackScene {
                pixels: ScenePixels::Rgb(&scene),
                mode: GrayMode::default(),
            }),
            |variant, _, waveform, _, marker, y4| {
                calls.set(calls.get() + 1);
                assert_eq!(waveform, WAVEFORM_MODE_GC16);
                if y4 {
                    assert_eq!((variant, marker), (UpdateVariant::Mtk, 1));
                    Err(io::Error::from_raw_os_error(libc::ENOTTY))
                } else {
                    assert_eq!((variant, marker), (UpdateVariant::Legacy, 2));
                    for x in 0..4 {
                        assert_eq!(
                            framebuffer.mapped_bytes_for_test()[x],
                            software_gray(110, x, 0)
                        );
                    }
                    Ok(())
                }
            },
            |_| panic!("No earlier accepted update exists"),
        )
        .unwrap();
    assert_eq!((calls.get(), receipt.marker()), (2, 2));
    assert_eq!(
        framebuffer.update_variant.get(),
        Some(UpdateVariant::Legacy)
    );
    assert!(!handle.hardware_y4_available());
}

#[test]
fn fallback_abi_probing_stops_at_uncertain_delivery() {
    for errno in [libc::EIO, libc::EINTR, libc::ETIMEDOUT] {
        let (mut framebuffer, handle) = raw_framebuffer(4, 1, 7);
        framebuffer.write_line(0, 0..4, &[110; 4]);
        let scene = [rgb(110); 4];
        let calls = Cell::new(0);
        assert!(
            framebuffer
                .send_update_using(
                    rect(0, 0, 4, 1),
                    WAVEFORM_MODE_GC16,
                    UPDATE_MODE_PARTIAL,
                    Some(FallbackScene {
                        pixels: ScenePixels::Rgb(&scene),
                        mode: GrayMode::default()
                    }),
                    |variant, _, _, _, marker, y4| {
                        calls.set(calls.get() + 1);
                        if y4 {
                            assert_eq!((variant, marker), (UpdateVariant::Mtk, 1));
                            Err(io::Error::from_raw_os_error(libc::ENOTTY))
                        } else {
                            assert_eq!((variant, marker), (UpdateVariant::Legacy, 2));
                            Err(io::Error::from_raw_os_error(errno))
                        }
                    },
                    |_| panic!("No earlier accepted update exists"),
                )
                .is_err()
        );
        assert_eq!(calls.get(), 2);
        assert_eq!(framebuffer.last_submitted_marker(), None);
        assert_eq!(framebuffer.update_variant.get(), None);
        assert_eq!(framebuffer.canonical_rewrite_count(), 1);
        assert!(
            framebuffer
                .current_dithering_status()
                .software_dither_fallback
        );
        assert_future_scene_is_blocked(&mut framebuffer, &handle);
    }
}

#[test]
fn unsupported_formats_or_abis_quantize_physically_without_hardware_flags() {
    for (bytes_per_pixel, driver, native_gray8) in [
        (1, UpdateVariant::Legacy, true),
        (1, UpdateVariant::Rex, true),
        (1, UpdateVariant::Zelda, true),
        (4, UpdateVariant::Mtk, false),
        (1, UpdateVariant::Mtk, false),
    ] {
        let mut framebuffer =
            Framebuffer::memory_for_test(4, 1, 4 * bytes_per_pixel + 3, bytes_per_pixel, SENTINEL)
                .unwrap();
        framebuffer.native_gray8 = native_gray8;
        framebuffer.update_variant.set(Some(driver));
        let handle = DitheringHandle::for_test(true);
        handle.set_hardware_y4(true);
        framebuffer.set_dithering(handle.clone(), false);
        assert!(!handle.hardware_y4_available() && handle.requested());
        framebuffer.write_line(0, 0..4, &[110; 4]);
        framebuffer
            .send_update_using(
                rect(0, 0, 4, 1),
                WAVEFORM_MODE_DU,
                UPDATE_MODE_PARTIAL,
                None,
                |variant, _, waveform, _, _, y4| {
                    assert_eq!((variant, waveform, y4), (driver, WAVEFORM_MODE_GC16, false));
                    Ok(())
                },
                |_| panic!("An unsupported device cannot request Y4"),
            )
            .unwrap();
        for x in 0..4 {
            assert_eq!(
                framebuffer.mapped_bytes_for_test()[x * bytes_per_pixel],
                software_gray(110, x, 0)
            );
        }
    }
}

#[test]
fn fallback_fill_and_partial_rows_keep_identical_phase_padding_and_polarity() {
    for bytes_per_pixel in [1, 4] {
        for inverted in [false, true] {
            let stride = 11 * bytes_per_pixel + 5;
            let mut filled =
                Framebuffer::memory_for_test(11, 3, stride, bytes_per_pixel, SENTINEL).unwrap();
            let mut partial =
                Framebuffer::memory_for_test(11, 3, stride, bytes_per_pixel, SENTINEL).unwrap();
            for framebuffer in [&mut filled, &mut partial] {
                let handle = DitheringHandle::for_test(false);
                handle.set_hardware_y4(true);
                framebuffer.set_dithering(handle, false);
                framebuffer
                    .inverted_gray8
                    .set(inverted && bytes_per_pixel == 1);
            }
            filled.fill(110);
            for y in 0..3 {
                for span in [0..2, 2..7, 7..11, 2..7] {
                    partial.write_line(y, span.clone(), &vec![110; span.len()]);
                }
            }
            assert_eq!(
                filled.mapped_bytes_for_test(),
                partial.mapped_bytes_for_test()
            );
            for row in partial.mapped_bytes_for_test().chunks_exact(stride) {
                assert_eq!(&row[11 * bytes_per_pixel..], &[SENTINEL; 5]);
            }
        }
    }
}

#[test]
fn bilevel_mode_keeps_the_existing_fast_waveform_without_y4() {
    let (mut framebuffer, handle) = raw_framebuffer(4, 1, 7);
    framebuffer.set_dithering(handle.clone(), true);
    let refresh = ResolvedRefresh {
        region: PhysicalRect {
            x: 1,
            y: 0,
            width: 2,
            height: 1,
        },
        waveform: RefreshWaveform::Du,
        flashing: false,
    };
    assert_eq!(framebuffer.resolve_dithering(refresh), refresh);
    framebuffer
        .send_update_using(
            rect(1, 0, 2, 1),
            WAVEFORM_MODE_DU,
            UPDATE_MODE_PARTIAL,
            None,
            |_, _, waveform, _, _, y4| {
                assert_eq!(waveform, WAVEFORM_MODE_DU);
                assert!(!y4);
                Ok(())
            },
            |_| panic!("Bilevel mode must not request Y4"),
        )
        .unwrap();
    assert!(handle.hardware_y4_available());
}
