//! Bound one app-owned idle period before a newly admitted native Library visit.

use std::{
    fs::File,
    io::{self, Read},
    os::{
        fd::{AsRawFd, FromRawFd, OwnedFd},
        unix::net::{UnixListener, UnixStream},
    },
    time::Duration,
};

const TTL: Duration = Duration::from_secs(45);
const SAMPLE_INTERVAL: Duration = Duration::from_secs(1);
const MIN_AVAILABLE_KIB: u64 = 48 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum IdleEnd {
    Expired,
    LowMemory,
    UnknownMemory,
    Exit,
    Usb,
    Sleep,
    OwnerLost,
    MonitorFailed,
}

#[derive(Clone, Copy)]
pub(crate) enum IdleOwnerEvent {
    Exit,
    Usb,
    Sleep,
}

pub(crate) struct IdleOwnerEvents(UnixStream);
pub(crate) struct IdleOwnerNotices(UnixStream);

/// Issue a single peer to an app-owned observer and retain its paired endpoint.
/// The role is bound by this private capability for the idle period; its bytes
/// only cancel retention. This channel never carries a native visit or wake.
pub(crate) fn idle_owner_channel() -> io::Result<(IdleOwnerEvents, IdleOwnerNotices)> {
    let (writer, reader) = UnixStream::pair()?;
    Ok((IdleOwnerEvents(writer), IdleOwnerNotices(reader)))
}

impl IdleOwnerEvents {
    pub(crate) fn cancel(&self, event: IdleOwnerEvent) -> io::Result<()> {
        let byte: u8 = match event {
            IdleOwnerEvent::Exit => b'X',
            IdleOwnerEvent::Usb => b'U',
            IdleOwnerEvent::Sleep => b'D',
        };
        loop {
            let n = unsafe {
                libc::send(
                    self.0.as_raw_fd(),
                    (&raw const byte).cast(),
                    1,
                    libc::MSG_DONTWAIT | libc::MSG_NOSIGNAL,
                )
            };
            if n == 1 {
                return Ok(());
            }
            if n == 0 {
                return Err(io::ErrorKind::WriteZero.into());
            }
            let error = io::Error::last_os_error();
            if error.kind() != io::ErrorKind::Interrupted {
                return Err(error);
            }
        }
    }
}

impl IdleOwnerNotices {
    fn check(&self) -> Result<(), IdleEnd> {
        let mut byte: u8 = 0;
        loop {
            let n = unsafe {
                libc::recv(
                    self.0.as_raw_fd(),
                    (&raw mut byte).cast(),
                    1,
                    libc::MSG_DONTWAIT,
                )
            };
            return Err(match n {
                1 => match byte {
                    b'X' => IdleEnd::Exit,
                    b'U' => IdleEnd::Usb,
                    b'D' => IdleEnd::Sleep,
                    _ => IdleEnd::MonitorFailed,
                },
                0 => IdleEnd::OwnerLost,
                -1 => match io::Error::last_os_error().kind() {
                    io::ErrorKind::Interrupted => continue,
                    io::ErrorKind::WouldBlock => return Ok(()),
                    _ => IdleEnd::MonitorFailed,
                },
                _ => IdleEnd::MonitorFailed,
            });
        }
    }
}

pub(crate) struct NativeLibraryIdle {
    owner: IdleOwnerNotices,
    timer: OwnedFd,
    deadline: Duration,
    next_sample: Duration,
    memory: fn() -> Option<u64>,
    end: Option<IdleEnd>,
}

impl NativeLibraryIdle {
    /// Begin once when the worker enters confirmed Home, after all device I/O
    /// and presentation ownership are released. Keep this instance across
    /// rejected, timed-out, and otherwise incomplete Library visits.
    pub(crate) fn start(owner: IdleOwnerNotices) -> Result<Self, IdleEnd> {
        Self::start_with(owner, TTL, available_memory)
    }

    fn start_with(
        owner: IdleOwnerNotices,
        ttl: Duration,
        memory: fn() -> Option<u64>,
    ) -> Result<Self, IdleEnd> {
        let entered = boottime()?;
        let deadline = entered.checked_add(ttl).ok_or(IdleEnd::MonitorFailed)?;
        let fd = unsafe {
            libc::timerfd_create(libc::CLOCK_BOOTTIME, libc::TFD_CLOEXEC | libc::TFD_NONBLOCK)
        };
        if fd < 0 {
            return Err(IdleEnd::MonitorFailed);
        }
        let mut idle = Self {
            owner,
            timer: unsafe { OwnedFd::from_raw_fd(fd) },
            deadline,
            next_sample: entered,
            memory,
            end: None,
        };
        idle.check(false)?;
        Ok(idle)
    }

    /// Wait for a possible visit or a terminal idle condition. Listener readiness
    /// has no admission meaning. The caller must independently verify a new visit.
    pub(crate) fn wait_for_visit(&mut self, visits: &UnixListener) -> Result<(), IdleEnd> {
        loop {
            self.check(false)?;
            let mut fds = [
                poll_fd(self.owner.0.as_raw_fd()),
                poll_fd(self.timer.as_raw_fd()),
                poll_fd(visits.as_raw_fd()),
            ];
            let available = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as _, -1) };
            if available < 0 {
                if io::Error::last_os_error().kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return self.latch(Err(IdleEnd::MonitorFailed));
            }
            self.check(false)?;
            if fds.iter().any(|fd| fd.revents & libc::POLLNVAL != 0)
                || fds[1..]
                    .iter()
                    .any(|fd| fd.revents & (libc::POLLERR | libc::POLLHUP) != 0)
            {
                return self.latch(Err(IdleEnd::MonitorFailed));
            }
            if fds[2].revents & libc::POLLIN != 0 {
                return Ok(());
            }
        }
    }

    /// Recheck after the potentially slow native proof and immediately before
    /// committing takeover. Doing this cannot grant a visit or extend idle time.
    pub(crate) fn before_commit(&mut self) -> Result<(), IdleEnd> {
        self.check(true)
    }

    fn check(&mut self, force_memory: bool) -> Result<(), IdleEnd> {
        if let Some(end) = self.end {
            return Err(end);
        }
        let result = self.check_current(force_memory);
        self.latch(result)
    }

    fn check_current(&mut self, force_memory: bool) -> Result<(), IdleEnd> {
        self.owner.check()?;
        let now = boottime()?;
        if now >= self.deadline {
            return Err(IdleEnd::Expired);
        }
        if now >= self.next_sample || force_memory {
            let value = (self.memory)().ok_or(IdleEnd::UnknownMemory)?;
            if value < MIN_AVAILABLE_KIB {
                return Err(IdleEnd::LowMemory);
            }
            self.owner.check()?;
            let checked = boottime()?;
            if checked >= self.deadline {
                return Err(IdleEnd::Expired);
            }
            if now >= self.next_sample {
                self.next_sample = checked
                    .checked_add(SAMPLE_INTERVAL)
                    .ok_or(IdleEnd::MonitorFailed)?;
                self.arm(self.next_sample.min(self.deadline))?;
            }
        }
        Ok(())
    }

    fn arm(&self, at: Duration) -> Result<(), IdleEnd> {
        let spec = libc::itimerspec {
            it_interval: libc::timespec {
                tv_sec: 0,
                tv_nsec: 0,
            },
            it_value: libc::timespec {
                tv_sec: at
                    .as_secs()
                    .try_into()
                    .map_err(|_| IdleEnd::MonitorFailed)?,
                tv_nsec: at.subsec_nanos() as libc::c_long,
            },
        };
        if unsafe {
            libc::timerfd_settime(
                self.timer.as_raw_fd(),
                libc::TFD_TIMER_ABSTIME,
                &spec,
                std::ptr::null_mut(),
            )
        } != 0
        {
            return Err(IdleEnd::MonitorFailed);
        }
        Ok(())
    }

    fn latch(&mut self, result: Result<(), IdleEnd>) -> Result<(), IdleEnd> {
        if let Err(end) = result {
            self.end = Some(end);
        }
        result
    }
}

fn poll_fd(fd: libc::c_int) -> libc::pollfd {
    libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    }
}

fn boottime() -> Result<Duration, IdleEnd> {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    if unsafe { libc::clock_gettime(libc::CLOCK_BOOTTIME, &mut time) } != 0
        || time.tv_sec < 0
        || !(0..1_000_000_000).contains(&time.tv_nsec)
    {
        return Err(IdleEnd::MonitorFailed);
    }
    Ok(Duration::new(time.tv_sec as u64, time.tv_nsec as u32))
}

fn available_memory() -> Option<u64> {
    let mut text = String::new();
    File::open("/proc/meminfo")
        .ok()?
        .take(32 * 1024 + 1)
        .read_to_string(&mut text)
        .ok()?;
    (text.len() <= 32 * 1024)
        .then(|| memory_from(&text))
        .flatten()
}

fn memory_from(text: &str) -> Option<u64> {
    let mut entries = text
        .lines()
        .filter_map(|line| line.strip_prefix("MemAvailable:"));
    let mut fields = entries.next()?.split_ascii_whitespace();
    let number = fields.next()?;
    if !number.bytes().all(|b| b.is_ascii_digit())
        || fields.next() != Some("kB")
        || fields.next().is_some()
        || entries.next().is_some()
    {
        return None;
    }
    number.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        os::{linux::net::SocketAddrExt, unix::net::SocketAddr},
        sync::atomic::{AtomicU32, Ordering},
    };

    fn listener() -> (UnixListener, SocketAddr) {
        static NEXT: AtomicU32 = AtomicU32::new(0);
        let address = SocketAddr::from_abstract_name(format!(
            "aidoku.idle-test.{}.{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ))
        .unwrap();
        (UnixListener::bind_addr(&address).unwrap(), address)
    }

    fn healthy() -> Option<u64> {
        Some(MIN_AVAILABLE_KIB)
    }

    #[test]
    fn initial_deadline_survives_repeated_unadmitted_connections() {
        let (_observer, owner) = idle_owner_channel().unwrap();
        let mut idle =
            NativeLibraryIdle::start_with(owner, Duration::from_secs(2), healthy).unwrap();
        let deadline = idle.deadline;
        let (visits, address) = listener();
        for _ in 0..3 {
            let _unadmitted = UnixStream::connect_addr(&address).unwrap();
            idle.wait_for_visit(&visits).unwrap();
            drop(visits.accept().unwrap());
            assert_eq!(deadline, idle.deadline);
        }
        assert_eq!(idle.wait_for_visit(&visits), Err(IdleEnd::Expired));
        assert_eq!(idle.before_commit(), Err(IdleEnd::Expired));
    }

    #[test]
    fn trusted_signals_interrupt_idle_even_with_a_ready_visit() {
        for (event, expected) in [
            (IdleOwnerEvent::Exit, IdleEnd::Exit),
            (IdleOwnerEvent::Usb, IdleEnd::Usb),
            (IdleOwnerEvent::Sleep, IdleEnd::Sleep),
        ] {
            let (observer, owner) = idle_owner_channel().unwrap();
            let mut idle = NativeLibraryIdle::start_with(owner, TTL, healthy).unwrap();
            let (visits, address) = listener();
            observer.cancel(event).unwrap();
            let _arrival = UnixStream::connect_addr(&address).unwrap();
            assert_eq!(idle.wait_for_visit(&visits), Err(expected));
            assert_eq!(idle.before_commit(), Err(expected));
        }
    }

    #[test]
    fn receiving_peer_loss_interrupts_a_blocked_wait() {
        let (observer, owner) = idle_owner_channel().unwrap();
        let mut idle = NativeLibraryIdle::start_with(owner, TTL, healthy).unwrap();
        let (visits, _) = listener();
        let waiting = std::thread::spawn(move || idle.wait_for_visit(&visits));
        drop(observer);
        assert_eq!(waiting.join().unwrap(), Err(IdleEnd::OwnerLost));
    }

    #[test]
    fn pressure_is_resampled_while_idle_without_a_visit() {
        static AVAILABLE: AtomicU32 = AtomicU32::new(MIN_AVAILABLE_KIB as u32);
        let (_observer, owner) = idle_owner_channel().unwrap();
        let mut idle = NativeLibraryIdle::start_with(owner, TTL, || {
            Some(AVAILABLE.load(Ordering::Relaxed).into())
        })
        .unwrap();
        AVAILABLE.store(MIN_AVAILABLE_KIB as u32 - 1, Ordering::Relaxed);
        let (visits, _) = listener();
        assert_eq!(idle.wait_for_visit(&visits), Err(IdleEnd::LowMemory));
    }

    #[test]
    fn unknown_memory_never_starts_idle() {
        let (_observer, owner) = idle_owner_channel().unwrap();
        assert!(matches!(
            NativeLibraryIdle::start_with(owner, TTL, || None),
            Err(IdleEnd::UnknownMemory)
        ));
        assert_eq!(memory_from("MemAvailable: 49152 kB\n"), healthy());
        assert_eq!(memory_from("MemFree: 49152 kB\n"), None);
        assert_eq!(memory_from("MemAvailable: 49152 bytes\n"), None);
        assert_eq!(
            memory_from("MemAvailable: 49152 kB\nMemAvailable: 1 kB\n"),
            None
        );
    }

    #[test]
    #[ignore = "uses the full 45-second production idle deadline"]
    fn production_home_deadline_expires_after_45_seconds() {
        let (_observer, owner) = idle_owner_channel().unwrap();
        let started = boottime().unwrap();
        let mut idle = NativeLibraryIdle::start(owner).unwrap();
        let (visits, _) = listener();
        assert_eq!(idle.wait_for_visit(&visits), Err(IdleEnd::Expired));
        let elapsed = boottime().unwrap() - started;
        assert!(elapsed >= TTL);
        println!("production_boottime_elapsed_ms={}", elapsed.as_millis());
    }
}
