//! Event-driven portrait observation owned exclusively by the platform loop.

use std::io;

use crate::framebuffer::Framebuffer;
use crate::portrait::{NativePortraitBinding, NativePortraitOrientation, PortraitPresentation};
use crate::portrait_events::NativePortraitObserver;

pub(crate) struct LivePortrait {
    binding: Option<NativePortraitBinding>,
    revision: u64,
    observer: Option<NativePortraitObserver>,
    observer_attempted: bool,
    observer_generation: u64,
    paused: bool,
    source_failed: bool,
    unsafe_layout: bool,
    desired_holding: Option<NativePortraitOrientation>,
    presentation: Option<PortraitPresentation>,
}

impl LivePortrait {
    pub(crate) fn new(binding: Option<NativePortraitBinding>, revision: u64) -> Self {
        Self {
            binding,
            revision,
            observer: None,
            observer_attempted: false,
            observer_generation: 0,
            paused: false,
            source_failed: false,
            unsafe_layout: false,
            desired_holding: binding.map(|value| value.calibration.holding),
            presentation: binding.map(|value| PortraitPresentation {
                holding: value.calibration.holding,
                scanout: value.calibration.scanout,
            }),
        }
    }

    pub(crate) fn synchronize_binding(
        &mut self,
        binding: Option<NativePortraitBinding>,
        revision: u64,
    ) -> bool {
        if revision == self.revision {
            return false;
        }
        self.revision = revision;
        self.binding = binding;
        self.desired_holding = binding.map(|value| value.calibration.holding);
        self.presentation = binding.map(|value| PortraitPresentation {
            holding: value.calibration.holding,
            scanout: value.calibration.scanout,
        });
        self.retire_observer();
        self.observer_attempted = false;
        self.source_failed = false;
        self.unsafe_layout = false;
        true
    }

    pub(crate) fn set_paused(&mut self, paused: bool) -> bool {
        if self.paused == paused {
            return false;
        }
        self.paused = paused;
        self.retire_observer();
        self.observer_attempted = false;
        self.source_failed = false;
        // A fresh checked observation must precede the first resumed frame.
        self.unsafe_layout = !paused && self.binding.is_some();
        true
    }

    fn retire_observer(&mut self) {
        self.observer = None;
        self.observer_generation = self.observer_generation.wrapping_add(1);
    }

    /// Open only once per active ownership generation; absence is explicit.
    pub(crate) fn ensure_observer(&mut self) -> io::Result<bool> {
        if self.paused || self.binding.is_none() || self.observer_attempted {
            return Ok(false);
        }
        self.observer_attempted = true;
        self.observer_generation = self.observer_generation.wrapping_add(1);
        let observer = match NativePortraitObserver::open() {
            Ok(Some(observer)) => observer,
            Ok(None) => {
                self.source_failed = true;
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    "the calibrated portrait sensor is unavailable",
                ));
            }
            Err(error) => {
                self.source_failed = true;
                return Err(error);
            }
        };
        if let Some(holding) = observer.current_pose() {
            self.desired_holding = Some(holding);
        }
        self.observer = Some(observer);
        Ok(true)
    }

    pub(crate) fn poll_snapshot(&self) -> (libc::c_int, u64) {
        let descriptor = if self.paused {
            -1
        } else {
            self.observer
                .as_ref()
                .map_or(-1, NativePortraitObserver::fd)
        };
        (descriptor, self.observer_generation)
    }

    /// Every ready batch, including duplicates, precedes a fresh scanout check.
    pub(crate) fn poll(&mut self, expected_generation: u64) -> io::Result<()> {
        if self.paused || expected_generation != self.observer_generation {
            return Ok(());
        }
        let Some(observer) = self.observer.as_mut() else {
            return Ok(());
        };
        if let Err(error) = observer.poll() {
            self.fail_source();
            return Err(error);
        }
        if let Some(holding) = observer.current_pose() {
            self.desired_holding = Some(holding);
        }
        Ok(())
    }

    pub(crate) fn fail_source(&mut self) {
        self.source_failed = true;
        self.retire_observer();
    }

    /// Reuse the mapping only after fresh layout-checked read-only GET ioctls.
    /// A failed observation preserves the previously applied presentation.
    pub(crate) fn observe_framebuffer(&mut self, framebuffer: &Framebuffer) -> io::Result<bool> {
        self.observe_framebuffer_with(|anchor| framebuffer.observe_native_rotation(anchor))
    }

    fn observe_framebuffer_with(
        &mut self,
        observe: impl FnOnce(
            crate::framebuffer::NativeFramebufferObservation,
        ) -> io::Result<crate::framebuffer::NativeFramebufferObservation>,
    ) -> io::Result<bool> {
        let Some(binding) = self.binding else {
            return Ok(false);
        };
        if self.paused || self.source_failed {
            return Ok(false);
        }
        let holding = self.desired_holding.unwrap_or(binding.calibration.holding);
        let result = observe(binding.framebuffer).and_then(|observed| {
            binding
                .calibration
                .resolve(holding, observed.rotation)
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidData,
                        "native portrait scanout moved by an unapproved quarter turn",
                    )
                })
        });
        match result {
            Ok(presentation) => {
                let changed = self.presentation != Some(presentation) || self.unsafe_layout;
                self.presentation = Some(presentation);
                self.unsafe_layout = false;
                Ok(changed)
            }
            Err(error) => {
                self.unsafe_layout = true;
                self.fail_source();
                Err(error)
            }
        }
    }

    pub(crate) fn is_blocked(&self) -> bool {
        self.source_failed || self.unsafe_layout
    }

    pub(crate) fn render_offset(&self, compatibility_offset: u32) -> u32 {
        self.presentation
            .map_or(compatibility_offset, PortraitPresentation::render_offset)
    }

    pub(crate) fn touch_rotation(&self, app_rotation: u32) -> u32 {
        self.presentation
            .map_or(app_rotation, |value| value.touch_rotation(app_rotation))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::framebuffer::NativeFramebufferObservation;
    use crate::portrait::NativePortraitCalibration;

    fn runtime() -> LivePortrait {
        LivePortrait::new(
            Some(NativePortraitBinding {
                calibration: NativePortraitCalibration {
                    holding: NativePortraitOrientation::Up,
                    scanout: NativePortraitOrientation::Up,
                    raw_rotation: 1,
                },
                framebuffer: NativeFramebufferObservation::for_test_rotation(1),
            }),
            1,
        )
    }

    #[test]
    fn rejected_scanout_keeps_the_last_pose_until_a_new_resume_observation() {
        let mut portrait = runtime();
        portrait.desired_holding = Some(NativePortraitOrientation::Down);
        assert!(
            portrait
                .observe_framebuffer_with(|_| Ok(NativeFramebufferObservation::for_test_rotation(
                    1
                )))
                .unwrap()
        );
        assert_eq!(portrait.render_offset(0), 180);
        assert_eq!(portrait.touch_rotation(90), 270);
        let accepted = portrait.presentation;
        assert!(
            portrait
                .observe_framebuffer_with(|_| Ok(NativeFramebufferObservation::for_test_rotation(
                    2
                )))
                .is_err()
        );
        assert!(portrait.is_blocked());
        assert_eq!(portrait.presentation, accepted);
        assert!(
            !portrait
                .observe_framebuffer_with(|_| panic!("rejected native I/O waits for resume"))
                .unwrap()
        );
        portrait.set_paused(true);
        portrait.set_paused(false);
        assert!(
            portrait
                .observe_framebuffer_with(|_| Ok(NativeFramebufferObservation::for_test_rotation(
                    3
                )))
                .unwrap()
        );
        assert!(!portrait.is_blocked());
        assert_eq!(portrait.render_offset(0), 0);
        assert_eq!(portrait.touch_rotation(90), 270);
    }

    #[test]
    fn sleep_retires_old_readiness_and_retains_the_independent_manual_angle() {
        let mut portrait = runtime();
        portrait.desired_holding = Some(NativePortraitOrientation::Down);
        portrait
            .observe_framebuffer_with(|_| Ok(NativeFramebufferObservation::for_test_rotation(1)))
            .unwrap();
        let before_suspend = portrait.poll_snapshot();
        assert!(portrait.set_paused(true));
        assert_eq!(portrait.poll_snapshot().0, -1);
        assert_ne!(portrait.poll_snapshot().1, before_suspend.1);
        assert_eq!(portrait.touch_rotation(270), 90);
        assert_eq!(portrait.render_offset(0), 180);
        portrait.poll(before_suspend.1).unwrap();
        assert_eq!(portrait.touch_rotation(270), 90);
        assert!(portrait.set_paused(false));
        assert!(portrait.is_blocked());
        assert!(
            portrait
                .observe_framebuffer_with(|_| Ok(NativeFramebufferObservation::for_test_rotation(
                    3
                )))
                .unwrap()
        );
        assert!(!portrait.is_blocked());
        assert_eq!(portrait.touch_rotation(270), 90);
        assert_eq!(portrait.render_offset(0), 0);
    }

    #[test]
    fn hard_source_failure_is_quiet_until_a_new_ownership_generation() {
        let mut portrait = runtime();
        portrait.fail_source();
        assert!(portrait.is_blocked());
        assert_eq!(portrait.poll_snapshot().0, -1);
        assert!(
            !portrait
                .observe_framebuffer_with(|_| panic!("a failed observer cannot authorize input"))
                .unwrap()
        );
        portrait.set_paused(true);
        portrait.set_paused(false);
        assert!(
            portrait
                .observe_framebuffer_with(|_| Ok(NativeFramebufferObservation::for_test_rotation(
                    1
                )))
                .unwrap()
        );
    }
}
