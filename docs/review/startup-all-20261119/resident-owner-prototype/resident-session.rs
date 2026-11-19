//! One-use release receipts for disposable native presentation stacks.

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use crate::framebuffer::NativeFramebufferObservation;
use crate::lifecycle::{LifecycleError, LifecycleHandle, LifecycleStatus};
use crate::platform::ClosedNativeSession;
use crate::portrait::NativePortraitOrientation;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ParkedState {
    status: LifecycleStatus,
    portrait_revision: u64,
}

impl ParkedState {
    fn capture(handle: &LifecycleHandle, native_io_started: bool) -> Result<Self, LifecycleError> {
        let state = &handle.state;
        let status = handle.status();
        if !status.render_paused
            || !status.input_released
            || !status.parked
            || !state.pause_requested.load(Ordering::Acquire)
            || state.native_io_started.load(Ordering::Acquire) != native_io_started
            || handle.pending_sleep_presentation().ok() != Some(None)
        {
            return Err(LifecycleError::ResourcesNotReleased);
        }
        Ok(Self {
            status,
            portrait_revision: state.portrait_revision.load(Ordering::Acquire),
        })
    }
}

#[derive(Default, Debug, Copy, Clone, PartialEq, Eq)]
enum Mode {
    #[default]
    Showing,
    Closing(ParkedState),
    Home(ParkedState),
    ReceiptTaken(ParkedState),
}

/// Proves that this backend dropped its previous presentation stack. The owner
/// must independently authorize the new visit and its portrait observation.
#[must_use]
pub struct LibraryIoClosed {
    owner: LifecycleHandle,
    round: u64,
    parked: ParkedState,
}

impl LibraryIoClosed {
    /// Round zero is the first native presentation on the retained event loop.
    pub fn round(&self) -> u64 {
        self.round
    }

    /// Consume the release receipt and request one new presentation on its owner.
    pub fn reopen(
        self,
        observed: Option<(
            NativePortraitOrientation,
            NativePortraitOrientation,
            NativeFramebufferObservation,
        )>,
    ) -> Result<(), LifecycleError> {
        let handle = self.owner;
        handle.ensure_live()?;
        let mut inner = handle
            .state
            .library
            .inner
            .lock()
            .expect("library session poisoned");
        if *inner != (self.round, Mode::ReceiptTaken(self.parked))
            || !handle.matches_closed_library_park(self.parked)
        {
            return Err(LifecycleError::LibraryTransition);
        }
        let next = self
            .round
            .checked_add(1)
            .ok_or(LifecycleError::LibraryTransition)?;
        if let Some((holding, scanout, framebuffer)) = observed {
            handle.apply_native_framebuffer_portrait_calibration(holding, scanout, framebuffer)?;
        }
        *inner = (next, Mode::Showing);
        handle.resume_flags();
        Ok(())
    }
}

#[derive(Default)]
pub(crate) struct Sessions {
    inner: Mutex<(u64, Mode)>,
    returning: AtomicBool,
}

impl Sessions {
    pub(crate) fn returning(&self) -> bool {
        self.returning.load(Ordering::Acquire)
    }

    pub(crate) fn require_showing<T>(
        &self,
        action: impl FnOnce() -> T,
    ) -> Result<T, LifecycleError> {
        let inner = self.inner.lock().expect("library session poisoned");
        if inner.1 != Mode::Showing {
            return Err(LifecycleError::LibraryTransition);
        }
        Ok(action())
    }
}

impl LifecycleHandle {
    /// Quiesce application work and await native park before requesting this.
    pub fn return_library_io(&self) -> Result<(), LifecycleError> {
        self.ensure_live()?;
        let mut inner = self
            .state
            .library
            .inner
            .lock()
            .expect("library session poisoned");
        if inner.1 != Mode::Showing {
            return Err(LifecycleError::ResourcesNotReleased);
        }
        inner.1 = Mode::Closing(ParkedState::capture(self, true)?);
        self.state.library.returning.store(true, Ordering::Release);
        crate::wakeup::poke(&self.wake);
        Ok(())
    }

    // The witness is constructible only by the platform after its stack returns.
    pub(crate) fn confirm_library_io_closed(
        &self,
        _closed: ClosedNativeSession,
    ) -> Result<(), LifecycleError> {
        self.ensure_live()?;
        let mut inner = self
            .state
            .library
            .inner
            .lock()
            .expect("library session poisoned");
        if inner.1 != Mode::Closing(ParkedState::capture(self, true)?) {
            return Err(LifecycleError::LibraryTransition);
        }
        *self
            .state
            .portrait_calibration
            .lock()
            .expect("portrait calibration poisoned") = None;
        self.state.portrait_revision.fetch_add(1, Ordering::AcqRel);
        self.state.native_io_started.store(false, Ordering::Release);
        self.state.library.returning.store(false, Ordering::Release);
        inner.1 = Mode::Home(ParkedState::capture(self, false)?);
        Ok(())
    }

    fn matches_closed_library_park(&self, expected: ParkedState) -> bool {
        !self.state.library.returning()
            && ParkedState::capture(self, false).ok() == Some(expected)
            && self
                .state
                .portrait_calibration
                .lock()
                .expect("portrait calibration poisoned")
                .is_none()
    }

    /// Claim the completed release of the backend's previous native resources once.
    pub fn take_library_io_closed(&self) -> Result<LibraryIoClosed, LifecycleError> {
        self.ensure_live()?;
        let mut inner = self
            .state
            .library
            .inner
            .lock()
            .expect("library session poisoned");
        let (round, Mode::Home(parked)) = *inner else {
            return Err(LifecycleError::LibraryTransition);
        };
        if !self.matches_closed_library_park(parked) {
            return Err(LifecycleError::LibraryTransition);
        }
        inner.1 = Mode::ReceiptTaken(parked);
        Ok(LibraryIoClosed {
            owner: self.clone(),
            round,
            parked,
        })
    }

    #[cfg(test)]
    pub(crate) fn home_for_fixture(&self) -> bool {
        matches!(
            self.state
                .library
                .inner
                .lock()
                .expect("library session poisoned")
                .1,
            Mode::Home(_) | Mode::ReceiptTaken(_)
        )
    }
}
