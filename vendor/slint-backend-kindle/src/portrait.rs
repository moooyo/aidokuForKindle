//! Portrait output mapping and input boundaries, independent of reader state.

use crate::framebuffer::NativeFramebufferObservation;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct NativePortraitBinding {
    pub(crate) calibration: NativePortraitCalibration,
    pub(crate) framebuffer: NativeFramebufferObservation,
}

/// A reviewed native portrait direction, independent of application rotation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NativePortraitOrientation {
    Up,
    Down,
}

impl NativePortraitOrientation {
    pub(crate) fn offset(self) -> u32 {
        match self {
            Self::Up => 0,
            Self::Down => 180,
        }
    }

    fn opposite(self) -> Self {
        match self {
            Self::Up => Self::Down,
            Self::Down => Self::Up,
        }
    }
}

/// The owner must pair these values within the same authenticated native run.
/// A raw kernel rotation code alone never identifies a physical direction.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct NativePortraitCalibration {
    pub(crate) holding: NativePortraitOrientation,
    pub(crate) scanout: NativePortraitOrientation,
    pub(crate) raw_rotation: u32,
}

impl NativePortraitCalibration {
    pub(crate) fn resolve(
        self,
        holding: NativePortraitOrientation,
        raw_rotation: u32,
    ) -> Option<PortraitPresentation> {
        if self.raw_rotation > 3 || raw_rotation > 3 {
            return None;
        }
        let scanout = match (raw_rotation + 4 - self.raw_rotation) % 4 {
            0 => self.scanout,
            2 => self.scanout.opposite(),
            _ => return None,
        };
        Some(PortraitPresentation { holding, scanout })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct PortraitPresentation {
    pub(crate) holding: NativePortraitOrientation,
    pub(crate) scanout: NativePortraitOrientation,
}

impl PortraitPresentation {
    pub(crate) fn render_offset(self) -> u32 {
        (self.holding.offset() + 360 - self.scanout.offset()) % 360
    }

    pub(crate) fn touch_rotation(self, app_rotation: u32) -> u32 {
        (app_rotation + self.holding.offset()) % 360
    }
}

/// Old readiness notifications and contacts cannot cross a full-frame barrier.
#[derive(Default)]
pub(crate) struct InputEpoch {
    value: u64,
    blocked: bool,
    frame_applied: bool,
}

impl InputEpoch {
    pub(crate) fn begin(&mut self) -> u64 {
        self.value = self.value.wrapping_add(1);
        self.blocked = true;
        self.frame_applied = false;
        self.value
    }

    pub(crate) fn snapshot(&self) -> u64 {
        self.value
    }

    pub(crate) fn accepts(&self, expected: u64) -> bool {
        !self.blocked && expected == self.value
    }

    pub(crate) fn is_blocked(&self) -> bool {
        self.blocked
    }

    pub(crate) fn needs_frame(&self) -> bool {
        self.blocked && !self.frame_applied
    }

    pub(crate) fn awaiting_snapshot(&self) -> bool {
        self.blocked && self.frame_applied
    }

    pub(crate) fn mark_frame_applied(&mut self) {
        self.frame_applied = true;
    }

    pub(crate) fn complete_frame(&mut self) {
        if !self.frame_applied {
            return;
        }
        self.blocked = false;
        self.frame_applied = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_relative_half_turns_resolve_for_every_kernel_baseline() {
        for raw in 0..4 {
            for scanout in [
                NativePortraitOrientation::Up,
                NativePortraitOrientation::Down,
            ] {
                let anchor = NativePortraitCalibration {
                    holding: NativePortraitOrientation::Up,
                    scanout,
                    raw_rotation: raw,
                };
                assert_eq!(
                    anchor
                        .resolve(NativePortraitOrientation::Down, raw)
                        .unwrap()
                        .scanout,
                    scanout
                );
                assert_eq!(
                    anchor
                        .resolve(NativePortraitOrientation::Up, (raw + 2) % 4)
                        .unwrap()
                        .scanout,
                    scanout.opposite()
                );
                assert_eq!(
                    anchor.resolve(NativePortraitOrientation::Up, (raw + 1) % 4),
                    None
                );
                assert_eq!(
                    anchor.resolve(NativePortraitOrientation::Up, (raw + 3) % 4),
                    None
                );
                assert_eq!(anchor.resolve(NativePortraitOrientation::Up, 4), None);
            }
        }
    }

    #[test]
    fn holding_and_scanout_offsets_are_independent_of_manual_landscape() {
        for (holding, scanout, residual) in [
            (
                NativePortraitOrientation::Up,
                NativePortraitOrientation::Up,
                0,
            ),
            (
                NativePortraitOrientation::Up,
                NativePortraitOrientation::Down,
                180,
            ),
            (
                NativePortraitOrientation::Down,
                NativePortraitOrientation::Up,
                180,
            ),
            (
                NativePortraitOrientation::Down,
                NativePortraitOrientation::Down,
                0,
            ),
        ] {
            let presentation = PortraitPresentation { holding, scanout };
            assert_eq!(presentation.render_offset(), residual);
            for app_rotation in [0, 90, 180, 270] {
                assert_eq!(
                    presentation.touch_rotation(app_rotation),
                    (holding.offset() + app_rotation) % 360
                );
                assert_eq!((app_rotation + residual) % 180, app_rotation % 180);
            }
        }
    }

    #[test]
    fn flip_and_sleep_boundaries_reject_previously_ready_input() {
        let mut epoch = InputEpoch::default();
        let before_flip = epoch.snapshot();
        assert!(epoch.accepts(before_flip));
        let after_flip = epoch.begin();
        assert!(epoch.needs_frame());
        assert!(!epoch.accepts(before_flip));
        assert!(!epoch.accepts(after_flip));
        epoch.mark_frame_applied();
        assert!(!epoch.needs_frame());
        assert!(epoch.awaiting_snapshot());
        assert!(!epoch.accepts(after_flip));
        epoch.complete_frame();
        assert!(epoch.accepts(after_flip));
        let after_suspend = epoch.begin();
        assert!(!epoch.accepts(after_flip));
        let after_resume = epoch.begin();
        assert!(!epoch.accepts(after_suspend));
        epoch.mark_frame_applied();
        epoch.complete_frame();
        assert!(!epoch.accepts(after_flip));
        assert!(epoch.accepts(after_resume));
    }

    #[test]
    fn a_new_pose_replaces_a_frame_waiting_for_a_fresh_contact_snapshot() {
        let mut epoch = InputEpoch::default();
        let first = epoch.begin();
        epoch.mark_frame_applied();
        assert!(epoch.awaiting_snapshot());
        let replacement = epoch.begin();
        assert!(epoch.needs_frame());
        assert!(!epoch.awaiting_snapshot());
        assert!(!epoch.accepts(first));
        assert!(!epoch.accepts(replacement));
        epoch.complete_frame();
        assert!(!epoch.accepts(replacement));
    }
}
