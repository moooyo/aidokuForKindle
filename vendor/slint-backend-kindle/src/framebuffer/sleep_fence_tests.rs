use super::{Framebuffer, UpdateVariant};
use std::io;

fn memory() -> Framebuffer {
    Framebuffer::memory_for_test(3, 2, 4, 1, 0xff).unwrap()
}

#[test]
fn no_accepted_submission_can_be_settled_but_cannot_complete_a_sleep_frame() {
    let framebuffer = memory();
    framebuffer
        .wait_for_pending_update_complete_checked()
        .unwrap();
    assert!(framebuffer.wait_for_update_complete_checked().is_err());
    assert!(framebuffer.refresh_full_checked().is_err());
    assert_eq!(framebuffer.last_submitted_marker(), None);
    framebuffer
        .wait_for_pending_update_complete_checked()
        .unwrap();
    assert!(framebuffer.wait_for_update_complete_checked().is_err());
}

#[test]
fn one_checked_frame_shares_its_identity_across_abi_probes() {
    let framebuffer = memory();
    let mut attempted = Vec::new();
    let receipt = framebuffer
        .submit_update_checked_with(|driver, marker| {
            attempted.push((driver, marker));
            if driver == UpdateVariant::Zelda {
                Ok(())
            } else {
                Err(io::Error::from_raw_os_error(libc::EINVAL))
            }
        })
        .unwrap();
    assert_eq!(
        attempted,
        [
            (UpdateVariant::Legacy, 1),
            (UpdateVariant::Rex, 1),
            (UpdateVariant::Zelda, 1),
        ],
    );
    assert_eq!(receipt.marker(), 1);
    let next = framebuffer
        .submit_update_checked_with(|driver, marker| {
            assert_eq!((driver, marker), (UpdateVariant::Zelda, 2));
            Ok(())
        })
        .unwrap();
    assert_eq!(next.marker(), 2);
}

#[test]
fn each_driver_requires_the_sleep_frames_own_new_marker() {
    for driver in UpdateVariant::PROBE_ORDER {
        let framebuffer = memory();
        framebuffer.update_variant.set(Some(driver));
        let preceding = framebuffer
            .submit_update_checked_with(|variant, marker| {
                assert_eq!((variant, marker), (driver, 1));
                Ok(())
            })
            .unwrap();
        let sleep = framebuffer
            .submit_update_checked_with(|variant, marker| {
                assert_eq!((variant, marker), (driver, 2));
                Ok(())
            })
            .unwrap();
        assert_eq!(sleep.marker(), 2);
        assert!(
            framebuffer
                .wait_for_submission_complete_with(&preceding, |_| {
                    panic!("a preceding receipt cannot complete the current frame")
                })
                .is_err()
        );
        let rejected = framebuffer.wait_for_submission_complete_with(&sleep, |target| {
            assert_eq!(target, sleep.marker());
            if target == preceding.marker() {
                Ok(())
            } else {
                Err(io::Error::other("the new frame is not complete"))
            }
        });
        assert!(rejected.is_err());
        framebuffer
            .wait_for_submission_complete_with(&sleep, |target| {
                assert_eq!(target, 2);
                Ok(())
            })
            .unwrap();
        framebuffer
            .wait_for_submission_complete_with(&sleep, |_| {
                panic!("an observed completion is reusable for this receipt")
            })
            .unwrap();
    }
}

#[test]
fn failed_send_cannot_yield_a_receipt_or_replace_the_preceding_update() {
    let framebuffer = memory();
    framebuffer.update_variant.set(Some(UpdateVariant::Mtk));
    let preceding = framebuffer
        .submit_update_checked_with(|_, marker| {
            assert_eq!(marker, 1);
            Ok(())
        })
        .unwrap();
    let error = framebuffer
        .submit_update_checked_with(|_, marker| {
            assert_eq!(marker, 2);
            Err(io::Error::from_raw_os_error(libc::EIO))
        })
        .unwrap_err();
    assert_eq!(error.raw_os_error(), Some(libc::EIO));
    assert_eq!(framebuffer.last_submitted_marker(), Some(1));
    framebuffer
        .wait_for_submission_complete_with(&preceding, |target| {
            assert_eq!(target, 1);
            Ok(())
        })
        .unwrap();
    let sleep = framebuffer
        .submit_update_checked_with(|_, marker| {
            assert_eq!(marker, 3);
            Ok(())
        })
        .unwrap();
    framebuffer
        .wait_for_submission_complete_with(&sleep, |target| {
            assert_eq!(target, 3);
            Ok(())
        })
        .unwrap();
}

#[test]
fn a_foreign_framebuffer_receipt_is_rejected_before_waiting() {
    let owner = memory();
    let foreign = memory();
    let receipt = owner.submit_update_checked_with(|_, _| Ok(())).unwrap();
    foreign.submit_update_checked_with(|_, _| Ok(())).unwrap();
    assert_eq!(
        foreign
            .wait_for_submission_complete_with(&receipt, |_| {
                panic!("a foreign receipt cannot issue a wait ioctl")
            })
            .unwrap_err()
            .kind(),
        io::ErrorKind::InvalidInput,
    );
    owner
        .wait_for_submission_complete_with(&receipt, |target| {
            assert_eq!(target, 1);
            Ok(())
        })
        .unwrap();
}

#[test]
fn interrupted_receipt_wait_keeps_its_captured_target_after_a_new_send() {
    let framebuffer = memory();
    let receipt = framebuffer
        .submit_update_checked_with(|_, _| Ok(()))
        .unwrap();
    let mut targets = Vec::new();
    framebuffer
        .wait_for_submission_complete_with(&receipt, |target| {
            targets.push(target);
            if targets.len() == 1 {
                framebuffer
                    .submit_update_checked_with(|_, _| Ok(()))
                    .unwrap();
                Err(io::Error::from_raw_os_error(libc::EINTR))
            } else {
                Ok(())
            }
        })
        .unwrap();
    assert_eq!(targets, [1, 1]);
    assert_eq!(framebuffer.last_submitted_marker(), Some(2));
    assert_eq!(
        framebuffer
            .wait_for_submission_complete_with(&receipt, |_| {
                panic!("a superseded receipt cannot observe a newer update")
            })
            .unwrap_err()
            .kind(),
        io::ErrorKind::InvalidInput,
    );
    let mut called = false;
    framebuffer
        .update_markers
        .wait_for_last_submitted(|target| {
            called = true;
            assert_eq!(target, 2);
            Ok(())
        })
        .unwrap();
    assert!(called);
}

#[test]
fn checked_public_wait_does_not_turn_driver_failure_into_completion() {
    let framebuffer = memory();
    let receipt = framebuffer
        .submit_update_checked_with(|_, _| Ok(()))
        .unwrap();
    assert!(
        framebuffer
            .wait_for_submission_complete_checked(&receipt)
            .is_err()
    );
    let mut called = false;
    framebuffer
        .wait_for_submission_complete_with(&receipt, |target| {
            called = true;
            assert_eq!(target, receipt.marker());
            Ok(())
        })
        .unwrap();
    assert!(called);
    framebuffer.wait_for_update_complete_checked().unwrap();
}
