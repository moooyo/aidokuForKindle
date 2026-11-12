//! Per-session update identities for submission and completion fences.

use std::cell::Cell;
use std::io;

/// A marker identifies one logical update, including its initial ABI probes.
/// Failed submissions consume an identity without replacing the wait target.
#[derive(Default)]
pub(super) struct UpdateMarkers {
    allocated: Cell<u32>,
    last_submitted: Cell<Option<u32>>,
    last_completed: Cell<Option<u32>>,
}

impl UpdateMarkers {
    pub(super) fn last_submitted(&self) -> Option<u32> {
        self.last_submitted.get()
    }

    pub(super) fn submit(&self, send: impl FnOnce(u32) -> io::Result<()>) -> io::Result<()> {
        self.submit_checked(send).map(|_| ())
    }

    pub(super) fn submit_checked(
        &self,
        send: impl FnOnce(u32) -> io::Result<()>,
    ) -> io::Result<u32> {
        // A reused marker could match an old pending or completed update. Zero
        // is never a submitted identity, and exhaustion must not wrap to one.
        let marker = self
            .allocated
            .get()
            .checked_add(1)
            .ok_or_else(|| io::Error::from_raw_os_error(libc::EOVERFLOW))?;
        self.allocated.set(marker);
        send(marker)?;
        self.last_submitted.set(Some(marker));
        Ok(marker)
    }

    pub(super) fn wait_for_last_submitted(
        &self,
        wait: impl FnMut(u32) -> io::Result<()>,
    ) -> io::Result<()> {
        let Some(marker) = self.last_submitted.get() else {
            // Opening a framebuffer does not create an update to wait for.
            return Ok(());
        };
        self.wait_for_submitted(marker, wait)
    }

    pub(super) fn wait_for_submitted(
        &self,
        marker: u32,
        mut wait: impl FnMut(u32) -> io::Result<()>,
    ) -> io::Result<()> {
        if self.last_submitted.get() != Some(marker) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "the update receipt is not the current accepted submission",
            ));
        }
        if self.last_completed.get() == Some(marker) {
            return Ok(());
        }
        loop {
            match wait(marker) {
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => return Err(error),
                Ok(()) => {
                    self.last_completed.set(Some(marker));
                    return Ok(());
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_fence_targets_the_newest_submission_even_with_an_older_update_pending() {
        let markers = UpdateMarkers::default();
        let mut pending = Vec::new();
        for _ in 0..3 {
            markers
                .submit(|marker| {
                    pending.push(marker);
                    Ok(())
                })
                .unwrap();
        }
        assert_eq!(pending, [1, 2, 3]);
        markers
            .wait_for_last_submitted(|target| {
                // The driver finds the first matching pending update, so
                // reusing marker 1 would acknowledge the oldest frame here.
                assert_eq!(pending.iter().position(|marker| *marker == target), Some(2));
                Ok(())
            })
            .unwrap();
    }

    #[test]
    fn a_failed_send_preserves_the_fence_and_does_not_reuse_its_identity() {
        let markers = UpdateMarkers::default();
        markers
            .submit(|marker| {
                assert_eq!(marker, 1);
                Ok(())
            })
            .unwrap();
        let error = markers
            .submit(|marker| {
                assert_eq!(marker, 2);
                Err(io::Error::from(io::ErrorKind::BrokenPipe))
            })
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::BrokenPipe);
        markers
            .wait_for_last_submitted(|marker| {
                assert_eq!(marker, 1);
                Ok(())
            })
            .unwrap();
        markers
            .submit(|marker| {
                assert_eq!(marker, 3);
                Ok(())
            })
            .unwrap();
        markers
            .wait_for_last_submitted(|marker| {
                assert_eq!(marker, 3);
                Ok(())
            })
            .unwrap();
    }

    #[test]
    fn no_successful_submission_means_no_wait_ioctl() {
        let markers = UpdateMarkers::default();
        markers
            .wait_for_last_submitted(|_| panic!("no update was submitted"))
            .unwrap();
        assert!(
            markers
                .submit(|_| Err(io::Error::from(io::ErrorKind::Unsupported)))
                .is_err()
        );
        markers
            .wait_for_last_submitted(|_| panic!("the first update was rejected"))
            .unwrap();
    }

    #[test]
    fn marker_exhaustion_cannot_reuse_an_old_submission_or_completion() {
        let markers = UpdateMarkers {
            allocated: Cell::new(u32::MAX - 1),
            ..Default::default()
        };
        assert_eq!(markers.submit_checked(|_| Ok(())).unwrap(), u32::MAX);
        markers
            .wait_for_last_submitted(|target| {
                assert_eq!(target, u32::MAX);
                Ok(())
            })
            .unwrap();
        let error = markers
            .submit_checked(|_| panic!("an exhausted session cannot issue another SEND"))
            .unwrap_err();
        assert_eq!(error.raw_os_error(), Some(libc::EOVERFLOW));
        assert_eq!(markers.last_submitted(), Some(u32::MAX));
        markers
            .wait_for_last_submitted(|_| panic!("the accepted completion remains cached"))
            .unwrap();
    }

    #[test]
    fn interrupted_waits_keep_the_original_target_until_the_fence_finishes() {
        let markers = UpdateMarkers::default();
        markers.submit(|_| Ok(())).unwrap();
        let mut targets = Vec::new();
        markers
            .wait_for_last_submitted(|target| {
                targets.push(target);
                if targets.len() == 1 {
                    markers.submit(|_| Ok(())).unwrap();
                    Err(io::Error::from(io::ErrorKind::Interrupted))
                } else {
                    Ok(())
                }
            })
            .unwrap();
        assert_eq!(targets, [1, 1]);
        markers
            .wait_for_last_submitted(|target| {
                assert_eq!(target, 2);
                Ok(())
            })
            .unwrap();
    }

    #[test]
    fn a_wait_error_is_reported_without_forgetting_the_submitted_update() {
        let markers = UpdateMarkers::default();
        markers.submit(|_| Ok(())).unwrap();
        let error = markers
            .wait_for_last_submitted(|target| {
                assert_eq!(target, 1);
                Err(io::Error::from(io::ErrorKind::TimedOut))
            })
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        markers
            .wait_for_last_submitted(|target| {
                assert_eq!(target, 1);
                Ok(())
            })
            .unwrap();
    }
}
