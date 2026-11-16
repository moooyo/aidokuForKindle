//! Mode transitions exercise real submission state with memory-only fixtures.

use super::*;

fn region() -> UpdateRect {
    UpdateRect {
        left: 0,
        top: 0,
        width: 32,
        height: 32,
    }
}

fn raw() -> (Framebuffer, DitheringHandle) {
    let mut framebuffer = Framebuffer::memory_for_test(32, 32, 32, 1, 91).unwrap();
    let handle = DitheringHandle::for_test(true);
    handle.set_hardware_y4(true);
    framebuffer.set_dithering(handle.clone(), false);
    framebuffer.update_variant.set(Some(UpdateVariant::Mtk));
    (framebuffer, handle)
}

#[test]
fn modern_modes_cannot_turn_into_legacy_a2() {
    for variant in [
        None,
        Some(UpdateVariant::Legacy),
        Some(UpdateVariant::Rex),
        Some(UpdateVariant::Zelda),
    ] {
        assert_eq!(
            waveform_for_driver(RefreshWaveform::Reagl, variant),
            WAVEFORM_MODE_GC16
        );
        assert_eq!(
            waveform_for_driver(RefreshWaveform::Gl16, variant),
            WAVEFORM_MODE_GC16
        );
    }
    assert_eq!(
        waveform_for_driver(RefreshWaveform::Reagl, Some(UpdateVariant::Mtk)),
        WAVEFORM_MODE_GLR16
    );
}

#[test]
fn animation_preserves_y4_and_uses_its_own_driver_mode() {
    let update = mtk_update_animated(
        region(),
        WAVEFORM_MODE_GLR16,
        UPDATE_MODE_FULL,
        12,
        true,
        Some(SwipeDirection::Right),
    );
    assert_eq!(
        update.flags,
        MTK_EPDC_FLAG_USE_DITHERING_Y4 | MTK_EPDC_FLAG_ENABLE_SWIPE
    );
    assert_eq!(
        (update.waveform_mode, update.update_mode),
        (WAVEFORM_MODE_AUTO, UPDATE_MODE_PARTIAL)
    );
    assert_eq!(
        (update.swipe_data.direction, update.swipe_data.steps),
        (3, 12)
    );
    assert_eq!((update.dither_mode, update.quant_bit), (0, 0));
    let tiny = UpdateRect {
        width: 8,
        ..region()
    };
    let update = mtk_update_animated(
        tiny,
        WAVEFORM_MODE_GLR16,
        UPDATE_MODE_FULL,
        13,
        true,
        Some(SwipeDirection::Left),
    );
    assert_eq!(update.flags, MTK_EPDC_FLAG_USE_DITHERING_Y4);
    assert_eq!(update.swipe_data.steps, 0);
    let thin = UpdateRect {
        height: 8,
        ..region()
    };
    let update = mtk_update_animated(
        thin,
        WAVEFORM_MODE_GLR16,
        UPDATE_MODE_FULL,
        14,
        true,
        Some(SwipeDirection::Left),
    );
    assert_eq!(update.swipe_data.steps, 0);
}

#[test]
fn native_inverted_gray_uses_loaded_night_waveforms_without_losing_dither() {
    for (logical, wire) in [
        (WAVEFORM_MODE_GC16, 8),
        (WAVEFORM_MODE_GL16, 9),
        (WAVEFORM_MODE_GLR16, 9),
    ] {
        let update =
            mtk_update_for_format(region(), logical, UPDATE_MODE_PARTIAL, 15, true, None, true);
        assert_eq!(update.waveform_mode, wire);
        assert_eq!(update.flags, MTK_EPDC_FLAG_USE_DITHERING_Y4);
        if wire == 9 {
            assert_eq!(update.update_mode, UPDATE_MODE_FULL);
        }
    }
    let fast = mtk_update_for_format(
        region(),
        WAVEFORM_MODE_DU,
        UPDATE_MODE_PARTIAL,
        16,
        false,
        None,
        true,
    );
    assert_eq!(fast.waveform_mode, 12);
    assert_eq!(fast.flags, EPDC_FLAG_FORCE_MONOCHROME);
    let animated = mtk_update_for_format(
        region(),
        WAVEFORM_MODE_GLR16,
        UPDATE_MODE_FULL,
        17,
        true,
        Some(SwipeDirection::Left),
        true,
    );
    assert_eq!(animated.waveform_mode, WAVEFORM_MODE_AUTO);
    assert_eq!(
        animated.flags,
        MTK_EPDC_FLAG_ENABLE_SWIPE | MTK_EPDC_FLAG_USE_DITHERING_Y4
    );
}

#[test]
fn definite_mode_rejection_keeps_raw_pixels_and_retries_a_fresh_marker() {
    let (framebuffer, handle) = raw();
    let preceding = framebuffer
        .submit_update_checked_with(|_, _| Ok(()))
        .unwrap();
    let mut sends = Vec::new();
    let mut waits = Vec::new();
    let receipt = framebuffer
        .send_update_using_animation(
            region(),
            WAVEFORM_MODE_GLR16,
            UPDATE_MODE_FULL,
            None,
            Some(SwipeDirection::Left),
            |variant, _, waveform, mode, marker, y4, animation| {
                assert_eq!(variant, UpdateVariant::Mtk);
                sends.push((waveform, mode, marker, y4, animation));
                if sends.len() == 1 {
                    Err(io::Error::from_raw_os_error(libc::EINVAL))
                } else {
                    Ok(())
                }
            },
            |marker| {
                waits.push(marker);
                Ok(())
            },
        )
        .unwrap();
    assert_eq!(waits, vec![preceding.marker()]);
    assert_eq!(sends.len(), 2);
    assert_ne!(sends[0].2, sends[1].2);
    assert_eq!(
        sends[1],
        (
            WAVEFORM_MODE_GC16,
            UPDATE_MODE_FULL,
            receipt.marker(),
            true,
            None
        )
    );
    assert!(handle.hardware_y4_available());
    assert!(!handle.advanced_refresh_available());
    assert_eq!(framebuffer.canonical_rewrite_count(), 0);
    assert!(
        framebuffer
            .mapped_bytes_for_test()
            .iter()
            .all(|pixel| *pixel == 91)
    );
    let mut later = Vec::new();
    framebuffer
        .send_update_using_animation(
            region(),
            WAVEFORM_MODE_GLR16,
            UPDATE_MODE_FULL,
            None,
            Some(SwipeDirection::Right),
            |_, _, waveform, _, _, _, animation| {
                later.push((waveform, animation));
                Ok(())
            },
            |_| Ok(()),
        )
        .unwrap();
    assert_eq!(later, vec![(WAVEFORM_MODE_GC16, None)]);
}

#[test]
fn uncertain_animation_delivery_never_retries_or_rewrites() {
    let (framebuffer, handle) = raw();
    let mut attempts = 0;
    let result = framebuffer.send_update_using_animation(
        region(),
        WAVEFORM_MODE_GLR16,
        UPDATE_MODE_FULL,
        None,
        Some(SwipeDirection::Left),
        |_, _, _, _, _, _, _| {
            attempts += 1;
            Err(io::Error::from_raw_os_error(libc::EIO))
        },
        |_| panic!("An uncertain submission cannot be retried"),
    );
    assert!(result.is_err());
    assert_eq!(attempts, 1);
    assert!(handle.dithering_delivery_uncertain());
    assert_eq!(framebuffer.canonical_rewrite_count(), 0);
}
