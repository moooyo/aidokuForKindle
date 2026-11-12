//! Stock-powerd ownership transitions without dropping fonts or the UI process.

use std::fmt;
use std::os::fd::{AsRawFd, OwnedFd};
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use crate::framebuffer::NativeFramebufferObservation;
use crate::portrait::{
    NativePortraitBinding, NativePortraitCalibration, NativePortraitOrientation,
};

#[derive(Debug, Clone, Copy, Default, Eq, PartialEq)]
pub struct LifecycleStatus {
    pub render_paused: bool,
    pub input_released: bool,
    pub parked: bool,
    pub pause_count: u64,
    pub resume_count: u64,
    pub frame_write_count: u64,
    pub frame_send_count: u64,
    pub resume_full_frame_count: u64,
    pub input_generation: u64,
}

#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum LifecycleError {
    EventLoopTerminated,
    ResourcesNotReleased,
    InvalidPortraitCalibration,
    PortraitCalibrationRequiresPause,
}

impl fmt::Display for LifecycleError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::EventLoopTerminated => "the native event loop has terminated",
            Self::ResourcesNotReleased => "native display and input ownership are not released yet",
            Self::InvalidPortraitCalibration => {
                "the native portrait calibration rotation is unknown"
            }
            Self::PortraitCalibrationRequiresPause => {
                "native portrait calibration requires pre-I/O or paused ownership"
            }
        })
    }
}

impl std::error::Error for LifecycleError {}

#[derive(Default)]
pub(crate) struct LifecycleState {
    pub(crate) pause_requested: AtomicBool,
    pub(crate) applied_paused: AtomicBool,
    pub(crate) input_released: AtomicBool,
    pub(crate) parked: AtomicBool,
    pub(crate) full_repaint_requested: AtomicBool,
    pub(crate) resume_frame_pending: AtomicBool,
    pub(crate) pause_count: AtomicU64,
    pub(crate) resume_count: AtomicU64,
    pub(crate) frame_write_count: AtomicU64,
    pub(crate) frame_send_count: AtomicU64,
    pub(crate) resume_full_frame_count: AtomicU64,
    pub(crate) input_generation: AtomicU64,
    pub(crate) native_io_started: AtomicBool,
    pub(crate) portrait_calibration: Mutex<Option<NativePortraitBinding>>,
    pub(crate) portrait_revision: AtomicU64,
    pub(crate) sleep_presentation: crate::sleep_presentation::SleepPresentationControl,
}

/// Enqueues ownership changes; applied status is published only by the UI loop.
#[derive(Clone)]
pub struct LifecycleHandle {
    pub(crate) state: Arc<LifecycleState>,
    pub(crate) wake: Arc<OwnedFd>,
    pub(crate) quit: Arc<AtomicBool>,
}

impl LifecycleHandle {
    /// Request a complete frame and a checked EPDC completion receipt.
    ///
    /// This queues no storage or native device work. The caller must display
    /// its prepared state, inhibit ordinary intents, then return to the event
    /// loop before polling the token. Sleeping displays reject new requests.
    pub fn request_sleep_presentation(&self) -> std::io::Result<u64> {
        if self.sleep_presentation_unavailable() {
            return Err(std::io::Error::other(
                "the Kindle display is unavailable for sleep presentation",
            ));
        }
        let token = self.state.sleep_presentation.request()?;
        if self.sleep_presentation_unavailable() {
            let _ = self.state.sleep_presentation.cancel_pending();
            return Err(std::io::Error::other(
                "the Kindle display is unavailable for sleep presentation",
            ));
        }
        self.state
            .full_repaint_requested
            .store(true, Ordering::Release);
        crate::wakeup::poke(&self.wake);
        Ok(token)
    }

    /// True only after the requested full frame's independent update completed.
    /// Queued refreshes, SEND counts and unrelated frames cannot satisfy it.
    pub fn sleep_presentation_completed(&self, token: u64) -> std::io::Result<bool> {
        if self.quit.load(Ordering::SeqCst) {
            return Err(std::io::Error::other(
                "the Kindle event loop has terminated",
            ));
        }
        self.state.sleep_presentation.completed(token)
    }

    /// Retire unfinished presentation before replacing or hiding prepared state.
    pub fn cancel_sleep_presentation(&self) -> std::io::Result<()> {
        if self.quit.load(Ordering::SeqCst) {
            return Err(std::io::Error::other(
                "the Kindle event loop has terminated",
            ));
        }
        self.state.sleep_presentation.cancel_pending()?;
        crate::wakeup::poke(&self.wake);
        Ok(())
    }

    pub(crate) fn pending_sleep_presentation(&self) -> std::io::Result<Option<u64>> {
        self.state.sleep_presentation.pending()
    }

    pub(crate) fn complete_sleep_presentation(&self, token: u64) -> std::io::Result<()> {
        if self.quit.load(Ordering::SeqCst) {
            return Err(std::io::Error::other(
                "the Kindle event loop has terminated",
            ));
        }
        self.state.sleep_presentation.acknowledge(token)
    }

    fn sleep_presentation_unavailable(&self) -> bool {
        self.quit.load(Ordering::SeqCst)
            || self.state.pause_requested.load(Ordering::Acquire)
            || self.state.applied_paused.load(Ordering::Acquire)
    }

    /// Enable live portrait observation from a trusted same-run calibration.
    ///
    /// The owner must authenticate the independent holding and scanout poses
    /// together with a fresh GETVAR rotation before native I/O or while paused.
    /// Raw rotation values, including 1 and 3, never identify Up or Down alone.
    /// This setter performs no device I/O and changes no application preference.
    pub fn set_native_framebuffer_portrait_calibration(
        &self,
        holding: NativePortraitOrientation,
        scanout: NativePortraitOrientation,
        observed: NativeFramebufferObservation,
    ) -> Result<(), LifecycleError> {
        self.ensure_live()?;
        if observed.rotation > 3 {
            return Err(LifecycleError::InvalidPortraitCalibration);
        }
        let calibration = NativePortraitBinding {
            calibration: NativePortraitCalibration {
                holding,
                scanout,
                raw_rotation: observed.rotation,
            },
            framebuffer: observed,
        };
        let mut current = self
            .state
            .portrait_calibration
            .lock()
            .expect("portrait calibration poisoned");
        if *current == Some(calibration) {
            return Ok(());
        }
        if self.state.native_io_started.load(Ordering::Acquire)
            && !self.state.applied_paused.load(Ordering::Acquire)
        {
            return Err(LifecycleError::PortraitCalibrationRequiresPause);
        }
        *current = Some(calibration);
        self.state.portrait_revision.fetch_add(1, Ordering::AcqRel);
        self.state
            .full_repaint_requested
            .store(true, Ordering::Release);
        crate::wakeup::poke(&self.wake);
        Ok(())
    }

    pub fn begin_pause(&self) -> Result<(), LifecycleError> {
        self.ensure_live()?;
        self.state.parked.store(false, Ordering::Release);
        self.state.pause_requested.store(true, Ordering::Release);
        let _ = self.state.sleep_presentation.cancel_pending();
        crate::wakeup::poke(&self.wake);
        Ok(())
    }

    /// Stop Slint timers and input polling after application checkpoint readiness.
    pub fn park(&self) -> Result<(), LifecycleError> {
        self.ensure_live()?;
        let status = self.status();
        if !status.render_paused || !status.input_released {
            return Err(LifecycleError::ResourcesNotReleased);
        }
        self.state.parked.store(true, Ordering::Release);
        crate::wakeup::poke(&self.wake);
        Ok(())
    }

    /// Retake input and rewrite the complete canonical RGB frame before GC16.
    pub fn resume(&self) -> Result<(), LifecycleError> {
        self.ensure_live()?;
        let _ = self.state.sleep_presentation.cancel_pending();
        self.state.parked.store(false, Ordering::Release);
        self.state.pause_requested.store(false, Ordering::Release);
        self.state
            .resume_frame_pending
            .store(true, Ordering::Release);
        self.state
            .full_repaint_requested
            .store(true, Ordering::Release);
        crate::wakeup::poke(&self.wake);
        Ok(())
    }

    pub fn status(&self) -> LifecycleStatus {
        LifecycleStatus {
            render_paused: self.state.applied_paused.load(Ordering::Acquire),
            input_released: self.state.input_released.load(Ordering::Acquire),
            parked: self.state.parked.load(Ordering::Acquire),
            pause_count: self.state.pause_count.load(Ordering::Acquire),
            resume_count: self.state.resume_count.load(Ordering::Acquire),
            frame_write_count: self.state.frame_write_count.load(Ordering::Acquire),
            frame_send_count: self.state.frame_send_count.load(Ordering::Acquire),
            resume_full_frame_count: self.state.resume_full_frame_count.load(Ordering::Acquire),
            input_generation: self.state.input_generation.load(Ordering::Acquire),
        }
    }

    /// Keep this handle alive while a signal handler uses the returned fd.
    /// The handler may only make an async-signal-safe nonblocking write to it.
    pub fn signal_wakeup_fd(&self) -> libc::c_int {
        self.wake.as_raw_fd()
    }

    fn ensure_live(&self) -> Result<(), LifecycleError> {
        if self.quit.load(Ordering::SeqCst) {
            Err(LifecycleError::EventLoopTerminated)
        } else {
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn handle() -> (LifecycleHandle, Arc<OwnedFd>) {
        let wake = crate::wakeup::make_wakeup().unwrap();
        (
            LifecycleHandle {
                state: Arc::new(LifecycleState::default()),
                wake: wake.write,
                quit: Arc::new(AtomicBool::new(false)),
            },
            wake.read,
        )
    }

    #[test]
    fn sleep_presentation_is_deferred_and_frame_counters_are_not_receipts() {
        fn require_send_sync<T: Send + Sync>() {}
        require_send_sync::<LifecycleHandle>();
        let (handle, _read) = handle();
        let token = handle.request_sleep_presentation().unwrap();
        assert_eq!(handle.pending_sleep_presentation().unwrap(), Some(token));
        assert!(!handle.sleep_presentation_completed(token).unwrap());
        assert!(handle.state.full_repaint_requested.load(Ordering::Acquire));
        assert!(!handle.state.native_io_started.load(Ordering::Acquire));
        handle
            .state
            .frame_write_count
            .fetch_add(1, Ordering::AcqRel);
        handle.state.frame_send_count.fetch_add(1, Ordering::AcqRel);
        assert!(!handle.sleep_presentation_completed(token).unwrap());
        handle.complete_sleep_presentation(token).unwrap();
        assert!(handle.sleep_presentation_completed(token).unwrap());
    }

    #[test]
    fn ownership_handoffs_cancel_unfinished_sleep_presentation() {
        let (handle, _read) = handle();
        let interrupted = handle.request_sleep_presentation().unwrap();
        handle.begin_pause().unwrap();
        assert!(handle.sleep_presentation_completed(interrupted).is_err());
        assert!(handle.request_sleep_presentation().is_err());
        handle.resume().unwrap();
        let completed = handle.request_sleep_presentation().unwrap();
        handle.complete_sleep_presentation(completed).unwrap();
        handle.begin_pause().unwrap();
        assert!(handle.sleep_presentation_completed(completed).unwrap());
        handle.resume().unwrap();
        let stale = handle.request_sleep_presentation().unwrap();
        handle.resume().unwrap();
        assert!(handle.sleep_presentation_completed(stale).is_err());
    }

    #[test]
    fn a_replaced_sleep_presentation_cannot_complete_after_its_overlay_is_hidden() {
        let (handle, _read) = handle();
        let stale = handle.request_sleep_presentation().unwrap();
        handle.cancel_sleep_presentation().unwrap();
        assert_eq!(handle.pending_sleep_presentation().unwrap(), None);
        assert!(handle.complete_sleep_presentation(stale).is_err());
        let fresh = handle.request_sleep_presentation().unwrap();
        handle.complete_sleep_presentation(fresh).unwrap();
        assert!(handle.sleep_presentation_completed(fresh).unwrap());
        assert!(handle.sleep_presentation_completed(stale).is_err());
    }

    #[test]
    fn sleep_requests_remain_unavailable_until_applied_pause_is_released() {
        let (handle, _read) = handle();
        handle.begin_pause().unwrap();
        handle.state.applied_paused.store(true, Ordering::Release);
        handle.resume().unwrap();
        assert!(handle.request_sleep_presentation().is_err());
        handle.state.applied_paused.store(false, Ordering::Release);
        let token = handle.request_sleep_presentation().unwrap();
        handle.quit.store(true, Ordering::Release);
        assert!(handle.request_sleep_presentation().is_err());
        assert!(handle.sleep_presentation_completed(token).is_err());
        assert!(handle.complete_sleep_presentation(token).is_err());
        assert!(handle.cancel_sleep_presentation().is_err());
    }

    #[test]
    fn pause_request_does_not_claim_evdev_or_render_ownership_is_released() {
        let (handle, _read) = handle();
        handle.begin_pause().unwrap();
        assert!(!handle.status().render_paused);
        assert!(!handle.status().input_released);
        assert_eq!(handle.park(), Err(LifecycleError::ResourcesNotReleased));
    }

    #[test]
    fn parked_backend_resumes_with_a_required_full_pixel_rewrite() {
        let (handle, _read) = handle();
        handle.begin_pause().unwrap();
        handle.state.applied_paused.store(true, Ordering::Release);
        handle.state.input_released.store(true, Ordering::Release);
        handle.park().unwrap();
        assert!(handle.status().parked);
        handle.resume().unwrap();
        assert!(!handle.state.pause_requested.load(Ordering::Acquire));
        assert!(!handle.status().parked);
        assert!(handle.state.full_repaint_requested.load(Ordering::Acquire));
        assert!(handle.status().render_paused);
    }

    #[test]
    fn ownership_requests_after_loop_termination_are_rejected() {
        let (handle, _read) = handle();
        handle.quit.store(true, Ordering::Release);
        assert_eq!(
            handle.begin_pause(),
            Err(LifecycleError::EventLoopTerminated)
        );
        assert_eq!(handle.resume(), Err(LifecycleError::EventLoopTerminated));
    }

    #[test]
    fn live_portrait_calibration_stages_typed_metadata_without_native_io() {
        let (handle, _read) = handle();
        let observed = NativeFramebufferObservation::for_test_rotation(1);
        handle
            .set_native_framebuffer_portrait_calibration(
                NativePortraitOrientation::Down,
                NativePortraitOrientation::Up,
                observed,
            )
            .unwrap();
        let stored = handle.state.portrait_calibration.lock().unwrap().unwrap();
        assert_eq!(stored.framebuffer, observed);
        assert_eq!(stored.calibration.holding, NativePortraitOrientation::Down);
        assert_eq!(stored.calibration.scanout, NativePortraitOrientation::Up);
        assert!(!handle.state.native_io_started.load(Ordering::Acquire));
        assert!(handle.state.full_repaint_requested.load(Ordering::Acquire));
        assert_eq!(handle.status().input_generation, 0);
    }

    #[test]
    fn recalibration_requires_an_actual_pause_after_native_io_has_started() {
        let (handle, _read) = handle();
        handle
            .state
            .native_io_started
            .store(true, Ordering::Release);
        handle.begin_pause().unwrap();
        let observed = NativeFramebufferObservation::for_test_rotation(3);
        assert_eq!(
            handle.set_native_framebuffer_portrait_calibration(
                NativePortraitOrientation::Up,
                NativePortraitOrientation::Down,
                observed,
            ),
            Err(LifecycleError::PortraitCalibrationRequiresPause)
        );
        handle.state.applied_paused.store(true, Ordering::Release);
        handle
            .set_native_framebuffer_portrait_calibration(
                NativePortraitOrientation::Up,
                NativePortraitOrientation::Down,
                observed,
            )
            .unwrap();
        assert_eq!(handle.state.portrait_revision.load(Ordering::Acquire), 1);
    }
}
