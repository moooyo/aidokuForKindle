//! Completion tokens for application-owned sleep frames.

use std::{io, sync::Mutex};

#[derive(Clone, Copy, Default)]
struct State {
    requested: u64,
    completed: u64,
    cancelled_through: u64,
    terminated: bool,
}

#[derive(Default)]
pub(crate) struct SleepPresentationControl(Mutex<State>);

fn unavailable(message: &'static str) -> io::Error {
    io::Error::other(message)
}

fn checked(state: &State, token: u64) -> io::Result<()> {
    if state.terminated {
        return Err(unavailable("the Kindle event loop has terminated"));
    }
    if token == 0 || token > state.requested {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid sleep presentation token",
        ));
    }
    if token <= state.cancelled_through {
        return Err(unavailable("the sleep presentation was cancelled"));
    }
    Ok(())
}

impl SleepPresentationControl {
    fn lock(&self) -> io::Result<std::sync::MutexGuard<'_, State>> {
        self.0
            .lock()
            .map_err(|_| unavailable("the sleep presentation state was poisoned"))
    }

    pub(crate) fn request(&self) -> io::Result<u64> {
        let mut state = self.lock()?;
        if state.terminated {
            return Err(unavailable("the Kindle event loop has terminated"));
        }
        state.requested = state
            .requested
            .checked_add(1)
            .ok_or_else(|| unavailable("the sleep presentation sequence is exhausted"))?;
        Ok(state.requested)
    }

    pub(crate) fn pending(&self) -> io::Result<Option<u64>> {
        let state = self.lock()?;
        if state.terminated {
            return Err(unavailable("the Kindle event loop has terminated"));
        }
        Ok(
            (state.requested > state.completed && state.requested > state.cancelled_through)
                .then_some(state.requested),
        )
    }

    pub(crate) fn completed(&self, token: u64) -> io::Result<bool> {
        let state = self.lock()?;
        checked(&state, token)?;
        Ok(token <= state.completed)
    }

    /// Only acknowledge the token captured before drawing, after its checked
    /// full update has completed. A request arriving later needs another frame.
    pub(crate) fn acknowledge(&self, token: u64) -> io::Result<()> {
        let mut state = self.lock()?;
        checked(&state, token)?;
        state.completed = state.completed.max(token);
        Ok(())
    }

    pub(crate) fn cancel_pending(&self) -> io::Result<()> {
        let mut state = self.lock()?;
        if state.requested > state.completed {
            state.cancelled_through = state.requested;
        }
        Ok(())
    }

    pub(crate) fn terminate(&self) {
        // Poisoning already rejects every request and receipt. Cleanup must
        // still mark the recovered state without panicking during unwinding.
        self.0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .terminated = true;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_frame_only_completes_tokens_requested_before_it_was_captured() {
        let control = SleepPresentationControl::default();
        assert_eq!(control.pending().unwrap(), None);
        let first = control.request().unwrap();
        let captured = control.request().unwrap();
        assert!(!control.completed(first).unwrap());
        let later = control.request().unwrap();
        control.acknowledge(captured).unwrap();
        assert!(control.completed(first).unwrap());
        assert!(control.completed(captured).unwrap());
        assert!(!control.completed(later).unwrap());
        assert_eq!(control.pending().unwrap(), Some(later));
    }

    #[test]
    fn an_interrupted_request_cannot_be_completed_by_a_later_frame() {
        let control = SleepPresentationControl::default();
        let cancelled = control.request().unwrap();
        control.cancel_pending().unwrap();
        assert!(control.completed(cancelled).is_err());
        assert!(control.acknowledge(cancelled).is_err());
        assert_eq!(control.pending().unwrap(), None);
        let fresh = control.request().unwrap();
        control.acknowledge(fresh).unwrap();
        assert!(control.completed(fresh).unwrap());
        assert!(control.completed(cancelled).is_err());
    }

    #[test]
    fn invalid_tokens_overflow_and_termination_never_report_success() {
        let control = SleepPresentationControl::default();
        assert!(control.completed(0).is_err());
        assert!(control.completed(1).is_err());
        *control.0.lock().unwrap() = State {
            requested: u64::MAX,
            ..State::default()
        };
        assert!(control.request().is_err());
        control.acknowledge(u64::MAX).unwrap();
        assert!(control.completed(u64::MAX).unwrap());
        control.terminate();
        assert!(control.pending().is_err());
        assert!(control.completed(u64::MAX).is_err());
        assert!(control.request().is_err());
        assert!(control.acknowledge(u64::MAX).is_err());
    }

    #[test]
    fn thread_safe_control_fails_closed_after_poisoning() {
        fn require_send_sync<T: Send + Sync>() {}
        require_send_sync::<SleepPresentationControl>();
        let control = SleepPresentationControl::default();
        let token = control.request().unwrap();
        let poisoned = std::panic::catch_unwind(|| {
            let _state = control.0.lock().unwrap();
            panic!("controlled sleep presentation state failure");
        });
        assert!(poisoned.is_err());
        assert!(control.pending().is_err());
        assert!(control.completed(token).is_err());
        assert!(control.acknowledge(token).is_err());
        assert!(control.request().is_err());
        control.terminate();
    }
}
