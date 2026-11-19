//! Link one newly admitted native Library visit to its eventual stock restoration.
//!
//! The listener's owner checks freshness, idle expiry, and current native ownership.
//! This module checks the route and retains the very socket used by that check.

use crate::native_library_visit::{self, NativeLibraryVisit, ProcessBirth};
use std::{
    io::{self, Read},
    os::{fd::AsRawFd, unix::net::UnixStream},
    time::Duration,
};

const ADMITTED: u8 = b'A';
const SETTLED: u8 = b'S';

pub(crate) struct PendingLibraryWaiter {
    stream: UnixStream,
    visit: NativeLibraryVisit,
}

impl PendingLibraryWaiter {
    pub(crate) fn observe(stream: UnixStream) -> io::Result<Self> {
        let visit = native_library_visit::observe(&stream)?;
        Ok(Self { stream, visit })
    }

    pub(crate) fn key(&self) -> ProcessBirth {
        self.visit.su
    }

    /// Call once after the launcher has reserved this unused visit key. The
    /// final checks run after route revalidation and must include the current
    /// idle gate and native ownership prerequisites. No bytes from the waiter
    /// can grant ownership or cause a wake.
    pub(crate) fn admit(
        self,
        confirm_after_proof: impl FnOnce() -> io::Result<()>,
    ) -> io::Result<ActiveLibraryWaiter> {
        self.visit.revalidate(&self.stream)?;
        confirm_after_proof()?;
        send(&self.stream, ADMITTED)?;
        Ok(ActiveLibraryWaiter(self.stream))
    }
}

pub(crate) struct ActiveLibraryWaiter(UnixStream);

impl ActiveLibraryWaiter {
    pub(crate) fn connected(&self) -> io::Result<bool> {
        let mut fd = libc::pollfd {
            fd: self.0.as_raw_fd(),
            events: libc::POLLRDHUP,
            revents: 0,
        };
        if unsafe { libc::poll(&mut fd, 1, 0) } < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(fd.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL | libc::POLLRDHUP) == 0)
    }

    /// Consume the visit after native display and input have been restored. Dropping
    /// an active visit without this acknowledgement reports a lost visit to its waiter.
    pub(crate) fn settle(self) -> io::Result<()> {
        send(&self.0, SETTLED)
    }
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) enum WaitOutcome {
    Unadmitted,
    Settled,
}

/// Wait on a connection to the caller's authenticated app-private supervisor.
/// An error after admission must not be interpreted as permission to cold-start.
pub(crate) fn wait_for_settlement(
    mut stream: UnixStream,
    admission_timeout: Duration,
) -> io::Result<WaitOutcome> {
    stream.set_nonblocking(false)?;
    stream.set_read_timeout(Some(admission_timeout))?;
    match receive(&mut stream)? {
        None => return Ok(WaitOutcome::Unadmitted),
        Some(ADMITTED) => (),
        Some(_) => return Err(io::ErrorKind::InvalidData.into()),
    }
    stream.set_read_timeout(None)?;
    match receive(&mut stream)? {
        Some(SETTLED) => Ok(WaitOutcome::Settled),
        None => Err(io::ErrorKind::ConnectionAborted.into()),
        Some(_) => Err(io::ErrorKind::InvalidData.into()),
    }
}

fn receive(stream: &mut UnixStream) -> io::Result<Option<u8>> {
    loop {
        let mut byte = 0;
        match stream.read(std::slice::from_mut(&mut byte)) {
            Ok(0) => return Ok(None),
            Ok(_) => return Ok(Some(byte)),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => (),
            Err(error) => return Err(error),
        }
    }
}

fn send(stream: &UnixStream, byte: u8) -> io::Result<()> {
    loop {
        let written = unsafe {
            libc::send(
                stream.as_raw_fd(),
                (&raw const byte).cast(),
                1,
                libc::MSG_DONTWAIT | libc::MSG_NOSIGNAL,
            )
        };
        match written {
            1 => return Ok(()),
            -1 => {
                let error = io::Error::last_os_error();
                if error.kind() != io::ErrorKind::Interrupted {
                    return Err(error);
                }
            }
            _ => return Err(io::ErrorKind::WriteZero.into()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn admitted_waiter_returns_only_after_its_own_settlement() {
        // Test the responses of two independently admitted visits without creating
        // a native admission profile or impersonating a stock process.
        let (first_server, first_client) = UnixStream::pair().unwrap();
        let (next_server, next_client) = UnixStream::pair().unwrap();
        let wait = |stream| {
            std::thread::spawn(move || wait_for_settlement(stream, Duration::from_secs(2)))
        };
        let first = wait(first_client);
        let next = wait(next_client);
        send(&first_server, ADMITTED).unwrap();
        send(&next_server, ADMITTED).unwrap();
        let first_active = ActiveLibraryWaiter(first_server);
        let next_active = ActiveLibraryWaiter(next_server);
        assert!(first_active.connected().unwrap());
        assert!(next_active.connected().unwrap());
        first_active.settle().unwrap();
        assert_eq!(first.join().unwrap().unwrap(), WaitOutcome::Settled);
        assert!(!next.is_finished());
        next_active.settle().unwrap();
        assert_eq!(next.join().unwrap().unwrap(), WaitOutcome::Settled);
    }
}
