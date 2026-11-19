//! A one-use, in-memory handoff between two independently admitted Library sessions.
//! The caller decides when a document and its new native presentation are authorized.

use std::{io, path::Path, process::ExitCode};

pub(crate) const KEEPER_ARG: &str = "--resident-keeper";
pub(crate) const MAX_BYTES: usize = 2 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct Peer {
    pub pid: u32,
    /// Kernel start time in clock ticks, field 22 of /proc/PID/stat.
    pub birth: u64,
}

/// Dispatch before initializing tracing, application storage, Slint, or the native backend.
pub(crate) fn entry() -> Option<ExitCode> {
    let mut args = std::env::args_os().skip(1);
    if args.next().as_deref() != Some(std::ffi::OsStr::new(KEEPER_ARG)) {
        return None;
    }
    #[cfg(target_os = "linux")]
    if args.next().is_none() && linux::serve().is_ok() {
        return Some(ExitCode::SUCCESS);
    }
    Some(ExitCode::FAILURE)
}

#[cfg(target_os = "linux")]
pub(crate) use linux::{publish, stop, take};

#[cfg(not(target_os = "linux"))]
pub(crate) fn publish(_: &Path, _: &[u8]) -> io::Result<Peer> {
    Err(io::ErrorKind::Unsupported.into())
}

#[cfg(not(target_os = "linux"))]
pub(crate) fn take(_: &Path) -> io::Result<Option<(Peer, Vec<u8>)>> {
    Ok(None)
}

#[cfg(not(target_os = "linux"))]
pub(crate) fn stop(_: &Path) -> io::Result<bool> {
    Ok(false)
}

#[cfg(target_os = "linux")]
mod linux {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::{
        env, fs,
        io::{Read, Write},
        mem::{self, size_of_val},
        os::{
            fd::{AsRawFd, FromRawFd, OwnedFd, RawFd},
            linux::net::SocketAddrExt,
            unix::{
                ffi::OsStrExt,
                fs::MetadataExt,
                net::{SocketAddr, UnixListener, UnixStream},
                process::CommandExt,
            },
        },
        process::{Child, Command, Stdio},
        time::Duration,
    };

    const ROLE: &str = "aidoku-resident-keeper";
    const ROLE_CMDLINE: &[u8] = b"aidoku-resident-keeper\0--resident-keeper\0";
    const PREFIX: &[u8] = b"aidoku.resident.v1.";
    const SETUP: &[u8; 4] = b"ARK1";
    const TTL: Duration = Duration::from_secs(45);
    const PRESSURE_INTERVAL: Duration = Duration::from_secs(1);
    const MIN_AVAILABLE_KIB: u64 = 48 * 1024;
    const SETUP_LIMIT: Duration = Duration::from_millis(650);
    const EXCHANGE_LIMIT: Duration = Duration::from_millis(120);
    const TAKE: u8 = b'T';
    const STOP: u8 = b'S';

    fn invalid() -> io::Error {
        io::ErrorKind::InvalidData.into()
    }
    fn denied() -> io::Error {
        io::ErrorKind::PermissionDenied.into()
    }
    fn timeout() -> io::Error {
        io::ErrorKind::TimedOut.into()
    }

    // CLOCK_BOOTTIME counts Kindle suspend time. A stale payload is never handed out on wake.
    fn now() -> io::Result<Duration> {
        let mut ts = libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };
        if unsafe { libc::clock_gettime(libc::CLOCK_BOOTTIME, &mut ts) } != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Duration::new(ts.tv_sec as u64, ts.tv_nsec as u32))
    }

    fn wait(fd: RawFd, events: i16, end: Duration) -> io::Result<()> {
        let mut fd = libc::pollfd {
            fd,
            events,
            revents: 0,
        };
        loop {
            let left = end
                .checked_sub(now()?)
                .filter(|d| !d.is_zero())
                .ok_or_else(timeout)?;
            let millis = left.as_nanos().div_ceil(1_000_000).min(i32::MAX as u128) as i32;
            match unsafe { libc::poll(&mut fd, 1, millis) } {
                n if n > 0 => return Ok(()),
                0 => return Err(timeout()),
                _ => {
                    let e = io::Error::last_os_error();
                    if e.kind() != io::ErrorKind::Interrupted {
                        return Err(e);
                    }
                }
            }
        }
    }

    fn read_all(mut socket: &UnixStream, mut buf: &mut [u8], end: Duration) -> io::Result<()> {
        while !buf.is_empty() {
            if now()? >= end {
                return Err(timeout());
            }
            match socket.read(buf) {
                Ok(0) => return Err(io::ErrorKind::UnexpectedEof.into()),
                Ok(n) => buf = &mut buf[n..],
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    wait(socket.as_raw_fd(), libc::POLLIN, end)?
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => (),
                Err(e) => return Err(e),
            }
        }
        Ok(())
    }

    fn write_all(mut socket: &UnixStream, mut buf: &[u8], end: Duration) -> io::Result<()> {
        while !buf.is_empty() {
            if now()? >= end {
                return Err(timeout());
            }
            match socket.write(buf) {
                Ok(0) => return Err(io::ErrorKind::WriteZero.into()),
                Ok(n) => buf = &buf[n..],
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    wait(socket.as_raw_fd(), libc::POLLOUT, end)?
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => (),
                Err(e) => return Err(e),
            }
        }
        Ok(())
    }

    fn expect(socket: &UnixStream, value: u8, end: Duration) -> io::Result<()> {
        let mut b = [0];
        read_all(socket, &mut b, end)?;
        if b[0] != value {
            return Err(invalid());
        }
        Ok(())
    }

    fn closed(socket: &UnixStream, end: Duration) -> io::Result<()> {
        match read_all(socket, &mut [0], end) {
            Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => Ok(()),
            Err(e) => Err(e),
            Ok(()) => Err(invalid()),
        }
    }

    fn size(n: usize) -> io::Result<usize> {
        if (1..=MAX_BYTES).contains(&n) {
            Ok(n)
        } else {
            Err(invalid())
        }
    }

    fn recv_payload(socket: &UnixStream, end: Duration) -> io::Result<Vec<u8>> {
        let mut len = [0; 4];
        read_all(socket, &mut len, end)?;
        let n = size(u32::from_be_bytes(len) as usize)?;
        let mut data = Vec::new();
        data.try_reserve_exact(n)
            .map_err(|_| io::ErrorKind::OutOfMemory)?;
        data.resize(n, 0);
        read_all(socket, &mut data, end)?;
        Ok(data)
    }

    fn available_from(text: &str) -> Option<u64> {
        let mut parts = text
            .lines()
            .find_map(|s| s.strip_prefix("MemAvailable:"))?
            .split_whitespace();
        let n = parts.next()?.parse().ok()?;
        (parts.next() == Some("kB") && parts.next().is_none()).then_some(n)
    }

    fn memory_ok() -> bool {
        fs::read_to_string("/proc/meminfo")
            .ok()
            .and_then(|s| available_from(&s))
            .is_some_and(|n| n >= MIN_AVAILABLE_KIB)
    }

    fn namespace(data: &Path) -> io::Result<[u8; 32]> {
        let data = data.canonicalize()?;
        let dir = data.metadata()?;
        if !dir.is_dir() {
            return Err(invalid());
        }
        let elf = fs::metadata("/proc/self/exe")?;
        let mut hash = Sha256::new();
        hash.update(PREFIX);
        hash.update(unsafe { libc::geteuid() }.to_be_bytes());
        for n in [dir.dev(), dir.ino(), elf.dev(), elf.ino()] {
            hash.update(n.to_be_bytes());
        }
        hash.update(data.as_os_str().as_bytes());
        Ok(hash.finalize().into())
    }

    fn name(key: &[u8; 32]) -> Vec<u8> {
        const HEX: &[u8] = b"0123456789abcdef";
        let mut name = Vec::with_capacity(PREFIX.len() + 64);
        name.extend_from_slice(PREFIX);
        for &b in key {
            name.extend([HEX[(b >> 4) as usize], HEX[(b & 15) as usize]]);
        }
        name
    }

    // AF_UNIX can block in connect() when its queue is full. Set O_NONBLOCK at creation.
    fn connect(key: &[u8; 32]) -> io::Result<Option<UnixStream>> {
        let bytes = name(key);
        let mut addr: libc::sockaddr_un = unsafe { mem::zeroed() };
        addr.sun_family = libc::AF_UNIX as _;
        for (c, b) in addr.sun_path[1..].iter_mut().zip(&bytes) {
            *c = *b as _;
        }
        let len = mem::offset_of!(libc::sockaddr_un, sun_path) + 1 + bytes.len();
        let fd = unsafe {
            libc::socket(
                libc::AF_UNIX,
                libc::SOCK_STREAM | libc::SOCK_NONBLOCK | libc::SOCK_CLOEXEC,
                0,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let socket = unsafe { UnixStream::from_raw_fd(fd) };
        let rc = unsafe { libc::connect(fd, (&raw const addr).cast(), len as _) };
        if rc != 0 {
            let error = io::Error::last_os_error();
            return match error.raw_os_error() {
                Some(libc::ECONNREFUSED | libc::ENOENT) => Ok(None),
                _ => Err(error),
            };
        }
        Ok(Some(socket))
    }

    fn birth(pid: u32) -> io::Result<u64> {
        let s = fs::read_to_string(format!("/proc/{pid}/stat"))?;
        s.rsplit_once(')')
            .and_then(|(_, tail)| tail.split_whitespace().nth(19))
            .and_then(|s| s.parse().ok())
            .filter(|&n| n != 0)
            .ok_or_else(invalid)
    }

    fn peer(socket: &UnixStream, expect_keeper: bool) -> io::Result<Peer> {
        let mut c: libc::ucred = unsafe { mem::zeroed() };
        let mut len = size_of_val(&c) as libc::socklen_t;
        if unsafe {
            libc::getsockopt(
                socket.as_raw_fd(),
                libc::SOL_SOCKET,
                libc::SO_PEERCRED,
                (&raw mut c).cast(),
                &mut len,
            )
        } != 0
        {
            return Err(io::Error::last_os_error());
        }
        if len as usize != size_of_val(&c) || c.pid <= 1 || c.uid != unsafe { libc::geteuid() } {
            return Err(denied());
        }
        let pid = c.pid as u32;
        let start = birth(pid)?;
        let own = fs::metadata("/proc/self/exe")?;
        let other = fs::metadata(format!("/proc/{pid}/exe"))?;
        if (own.dev(), own.ino()) != (other.dev(), other.ino()) {
            return Err(denied());
        }
        if expect_keeper {
            let mut cmd = Vec::with_capacity(ROLE_CMDLINE.len() + 1);
            fs::File::open(format!("/proc/{pid}/cmdline"))?
                .take(ROLE_CMDLINE.len() as u64 + 1)
                .read_to_end(&mut cmd)?;
            if cmd != ROLE_CMDLINE {
                return Err(denied());
            }
        }
        if birth(pid)? != start {
            return Err(denied());
        }
        Ok(Peer { pid, birth: start })
    }

    fn stop_key(key: &[u8; 32]) -> io::Result<bool> {
        let end = now()? + EXCHANGE_LIMIT;
        let Some(s) = connect(key)? else {
            return Ok(false);
        };
        peer(&s, true)?;
        write_all(&s, &[STOP], end)?;
        expect(&s, STOP, end)?;
        closed(&s, end)?;
        Ok(true)
    }

    pub(crate) fn stop(data: &Path) -> io::Result<bool> {
        stop_key(&namespace(data)?)
    }

    pub(crate) fn take(data: &Path) -> io::Result<Option<(Peer, Vec<u8>)>> {
        let end = now()? + EXCHANGE_LIMIT;
        let Some(s) = connect(&namespace(data)?)? else {
            return Ok(None);
        };
        let keeper = peer(&s, true)?;
        if !memory_ok() {
            write_all(&s, &[STOP], end)?;
            return Ok(None);
        }
        write_all(&s, &[TAKE], end)?;
        let bytes = recv_payload(&s, end)?;
        closed(&s, end)?;
        Ok(Some((keeper, bytes)))
    }

    // Mark every inherited descriptor except standard I/O close-on-exec. On old Kindle
    // kernels close_range is absent. Enumerate the child's descriptors with syscalls only:
    // this closure runs after a multithreaded fork and must not allocate or take libc locks.
    fn legacy_cloexec() -> io::Result<()> {
        let dir = unsafe {
            libc::open(
                c"/proc/self/fd".as_ptr(),
                libc::O_DIRECTORY | libc::O_RDONLY | libc::O_CLOEXEC,
            )
        };
        if dir < 0 {
            return Err(io::Error::last_os_error());
        }
        let mut buf = [0u8; 2048];
        let result = (|| {
            loop {
                let n = unsafe {
                    libc::syscall(libc::SYS_getdents64, dir, buf.as_mut_ptr(), buf.len())
                };
                if n == 0 {
                    return Ok(());
                }
                if n < 0 {
                    return Err(io::Error::last_os_error());
                }
                let mut entries = &buf[..n as usize];
                while !entries.is_empty() {
                    // linux_dirent64: d_reclen at byte 16, d_name at byte 19.
                    if entries.len() < 20 {
                        return Err(invalid());
                    }
                    let len = u16::from_ne_bytes([entries[16], entries[17]]) as usize;
                    if !(20..=entries.len()).contains(&len) {
                        return Err(invalid());
                    }
                    let name = &entries[19..len];
                    let mut fd: i32 = 0;
                    for &b in name.iter().take_while(|&&b| b != 0) {
                        if !b.is_ascii_digit() {
                            break;
                        }
                        fd = fd
                            .checked_mul(10)
                            .and_then(|n| n.checked_add(i32::from(b - b'0')))
                            .ok_or_else(invalid)?;
                    }
                    if fd > 2
                        && fd != dir
                        && unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } < 0
                    {
                        return Err(io::Error::last_os_error());
                    }
                    entries = &entries[len..];
                }
            }
        })();
        unsafe {
            libc::close(dir);
        }
        result
    }

    fn parent_death_signal(signal: libc::c_int) -> io::Result<()> {
        if unsafe {
            libc::prctl(
                libc::PR_SET_PDEATHSIG,
                signal as libc::c_ulong,
                0 as libc::c_ulong,
                0 as libc::c_ulong,
                0 as libc::c_ulong,
            )
        } != 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    fn isolate(publisher: u32) -> io::Result<()> {
        // Cover the fork-to-publication interval, including a disappearing parent
        // before exec. A fully committed keeper clears this after arming its own timer.
        parent_death_signal(libc::SIGKILL)?;
        if unsafe { libc::getppid() } as u32 != publisher {
            return Err(denied());
        }
        if unsafe { libc::setsid() } < 0 {
            return Err(io::Error::last_os_error());
        }
        if unsafe {
            libc::syscall(
                libc::SYS_close_range,
                3u32,
                u32::MAX,
                libc::CLOSE_RANGE_CLOEXEC,
            )
        } != 0
        {
            legacy_cloexec()?;
        }
        Ok(())
    }

    fn spawn(socket: UnixStream) -> io::Result<Child> {
        // With this disposition an exited child stays waitable (and its PID cannot be
        // recycled) until this sole Child owner reaps it. Do not start a helper if an
        // external child-reaping policy would invalidate that guarantee on older Linux.
        let mut sigchld = unsafe { mem::zeroed::<libc::sigaction>() };
        if unsafe { libc::sigaction(libc::SIGCHLD, std::ptr::null(), &mut sigchld) } != 0 {
            return Err(io::Error::last_os_error());
        }
        if sigchld.sa_sigaction != libc::SIG_DFL || sigchld.sa_flags & libc::SA_NOCLDWAIT != 0 {
            return Err(io::ErrorKind::Unsupported.into());
        }
        let publisher = std::process::id();
        let mut c = Command::new("/proc/self/exe");
        c.arg0(ROLE)
            .arg(KEEPER_ARG)
            .env_clear()
            .current_dir("/")
            .stdin(Stdio::from(OwnedFd::from(socket)))
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        unsafe {
            c.pre_exec(move || isolate(publisher));
        }
        c.spawn()
    }

    fn reap(mut c: Child, committed: bool) {
        if !committed && matches!(c.try_wait(), Ok(None)) {
            // Kill only our still-unreaped direct child, never a saved/discovered PID.
            // This also releases a helper stopped before it could arm the expiry timer.
            let _ = c.kill();
        }
        // Also reap in callers which continue running after a publication. The keeper's
        // session and kernel deadline do not depend on the publishing process surviving.
        let _ = std::thread::Builder::new()
            .name("resident-reap".into())
            .spawn(move || {
                let _ = c.wait();
            });
    }

    pub(crate) fn publish(data: &Path, bytes: &[u8]) -> io::Result<Peer> {
        let n = size(bytes.len())?;
        if !memory_ok() {
            return Err(io::ErrorKind::OutOfMemory.into());
        }
        let key = namespace(data)?;
        stop_key(&key)?;
        let end = now()? + SETUP_LIMIT;
        let (s, child_end) = UnixStream::pair()?;
        s.set_nonblocking(true)?;
        let child = spawn(child_end)?;
        let pid = child.id();
        let result = (|| {
            write_all(&s, SETUP, end)?;
            write_all(&s, &key, end)?;
            write_all(&s, &(n as u32).to_be_bytes(), end)?;
            write_all(&s, bytes, end)?;
            expect(&s, b'R', end)?;
            let keeper = Peer {
                pid,
                birth: birth(pid)?,
            };
            write_all(&s, b"C", end)?;
            expect(&s, b'P', end)?;
            closed(&s, end)?;
            Ok(keeper)
        })();
        drop(s);
        reap(child, result.is_ok());
        result
    }

    // A CLOCK_BOOTTIME timer is enforced by the kernel even if this helper is stopped.
    // A firmware lacking this facility falls back to ordinary startup.
    fn hard_expiry(ttl: Duration) -> io::Result<()> {
        let mut event: libc::sigevent = unsafe { mem::zeroed() };
        event.sigev_notify = libc::SIGEV_SIGNAL;
        event.sigev_signo = libc::SIGKILL;
        let mut timer: libc::timer_t = unsafe { mem::zeroed() };
        if unsafe { libc::timer_create(libc::CLOCK_BOOTTIME, &mut event, &mut timer) } != 0 {
            return Err(io::Error::last_os_error());
        }
        let mut spec: libc::itimerspec = unsafe { mem::zeroed() };
        spec.it_value.tv_sec = ttl.as_secs() as _;
        spec.it_value.tv_nsec = ttl.subsec_nanos() as _;
        if unsafe { libc::timer_settime(timer, 0, &spec, std::ptr::null_mut()) } != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub(super) fn serve() -> io::Result<()> {
        let started = now()?;
        hard_expiry(TTL)?;
        // stdin is the private socketpair created by publish(), never a user-supplied path.
        let input = unsafe { UnixStream::from_raw_fd(libc::STDIN_FILENO) };
        if peer(&input, false)?.pid != unsafe { libc::getppid() } as u32 {
            return Err(denied());
        }
        if env::args_os().next().as_deref() != Some(std::ffi::OsStr::new(ROLE)) || !memory_ok() {
            return Err(denied());
        }
        let setup = input.try_clone()?;
        drop(input);
        // All standard descriptors are benign before acknowledging publication. The
        // publisher also observes EOF, so it knows this last bootstrap socket closed.
        if unsafe { libc::dup2(libc::STDOUT_FILENO, libc::STDIN_FILENO) } < 0 {
            return Err(io::Error::last_os_error());
        }
        setup.set_nonblocking(true)?;
        let prepare = started + SETUP_LIMIT;
        let expires = started + TTL;
        let mut magic = [0; 4];
        read_all(&setup, &mut magic, prepare)?;
        if &magic != SETUP {
            return Err(invalid());
        }
        let mut key = [0; 32];
        read_all(&setup, &mut key, prepare)?;
        let data = recv_payload(&setup, prepare)?;
        let listener = UnixListener::bind_addr(&SocketAddr::from_abstract_name(name(&key))?)?;
        listener.set_nonblocking(true)?;
        write_all(&setup, b"R", prepare)?;
        expect(&setup, b'C', prepare)?;
        parent_death_signal(0)?;
        write_all(&setup, b"P", prepare)?;
        drop(setup);
        service(listener, data, expires, memory_ok)
    }

    fn service(
        listener: UnixListener,
        data: Vec<u8>,
        expires: Duration,
        memory_ok: impl Fn() -> bool,
    ) -> io::Result<()> {
        let mut rejected = 0;
        loop {
            let current = now()?;
            if current >= expires || !memory_ok() {
                return Ok(());
            }
            match wait(
                listener.as_raw_fd(),
                libc::POLLIN,
                expires.min(current + PRESSURE_INTERVAL),
            ) {
                Ok(()) => (),
                Err(e) if e.kind() == io::ErrorKind::TimedOut => continue,
                Err(e) => return Err(e),
            }
            let (s, _) = listener.accept()?;
            s.set_nonblocking(true)?;
            if peer(&s, false).is_err() {
                // An unrelated process cannot read, evict, or replace this payload by
                // accident. Under repeated bad connections abandon the optional cache.
                rejected += 1;
                if rejected >= 4 {
                    return Ok(());
                }
                continue;
            }
            let end = expires.min(now()? + EXCHANGE_LIMIT);
            let mut op = [0];
            read_all(&s, &mut op, end)?;
            if !memory_ok() {
                return Ok(());
            }
            // Withdraw the address before replying: no second consumer and no stale lock
            // when the application publishes its next generation immediately after stop.
            drop(listener);
            match op[0] {
                TAKE => {
                    write_all(&s, &(data.len() as u32).to_be_bytes(), end)?;
                    return write_all(&s, &data, end);
                }
                STOP => {
                    drop(data);
                    return write_all(&s, &[STOP], end);
                }
                _ => return Err(invalid()),
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        fn assert_retired(addr: &SocketAddr) {
            // Concurrent process tests can temporarily inherit this CLOEXEC
            // descriptor between fork and exec. The single-threaded keeper
            // itself does not fork after binding the endpoint.
            let deadline = std::time::Instant::now() + Duration::from_millis(250);
            while UnixStream::connect_addr(addr).is_ok() {
                assert!(std::time::Instant::now() < deadline, "listener remains reachable");
                std::thread::sleep(Duration::from_millis(2));
            }
        }

        #[test]
        fn publication_failure_reaps_a_stopped_direct_child() {
            let child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
            let pid = child.id();
            let original = birth(pid).unwrap();
            assert_eq!(unsafe { libc::kill(pid as i32, libc::SIGSTOP) }, 0);
            reap(child, false);
            let end = std::time::Instant::now() + Duration::from_secs(2);
            while birth(pid).is_ok_and(|n| n == original) {
                assert!(std::time::Instant::now() < end);
                std::thread::sleep(Duration::from_millis(5));
            }
        }

        #[test]
        fn older_kernel_descriptor_cleanup_preserves_the_parent() {
            let file = fs::File::open("/dev/null").unwrap();
            let fd = unsafe { libc::fcntl(file.as_raw_fd(), libc::F_DUPFD, 64) };
            assert!(fd >= 64);
            let inherited = unsafe { OwnedFd::from_raw_fd(fd) };
            let mut probe = Command::new("/bin/sh");
            probe.args([
                "-c",
                "test ! -e /proc/self/fd/$1",
                "check-fd",
                &fd.to_string(),
            ]);
            unsafe {
                probe.pre_exec(legacy_cloexec);
            }
            assert!(probe.status().unwrap().success());
            assert_eq!(
                unsafe { libc::fcntl(inherited.as_raw_fd(), libc::F_GETFD) },
                0
            );
        }

        #[test]
        fn missing_or_malformed_memory_information_fails_closed() {
            assert_eq!(available_from("MemFree: 99999 kB"), None);
            assert_eq!(available_from("MemAvailable: 99999 B"), None);
            assert_eq!(available_from("MemAvailable: 99999 kB extra"), None);
            assert_eq!(
                available_from("MemFree: 12 kB\nMemAvailable: 49152 kB\n"),
                Some(MIN_AVAILABLE_KIB)
            );
        }

        #[test]
        fn wrong_sizes_do_not_allocate_and_partial_payload_is_rejected() {
            for n in [0_u32, MAX_BYTES as u32 + 1, u32::MAX] {
                let (s, mut w) = UnixStream::pair().unwrap();
                w.write_all(&n.to_be_bytes()).unwrap();
                assert_eq!(
                    recv_payload(&s, now().unwrap() + SETUP_LIMIT)
                        .unwrap_err()
                        .kind(),
                    io::ErrorKind::InvalidData
                );
            }
            let (s, mut w) = UnixStream::pair().unwrap();
            w.write_all(&9_u32.to_be_bytes()).unwrap();
            w.write_all(&[1, 2]).unwrap();
            drop(w);
            assert_eq!(
                recv_payload(&s, now().unwrap() + SETUP_LIMIT)
                    .unwrap_err()
                    .kind(),
                io::ErrorKind::UnexpectedEof
            );
        }

        #[test]
        fn low_memory_drops_the_listener_without_a_consumer() {
            let addr = SocketAddr::from_abstract_name(format!(
                "aidoku.test.pressure.{}",
                std::process::id()
            ))
            .unwrap();
            let l = UnixListener::bind_addr(&addr).unwrap();
            service(l, vec![7], now().unwrap() + TTL, || false).unwrap();
            assert_retired(&addr);
        }

        #[test]
        fn newly_low_memory_releases_data_before_a_connected_consumer_can_read_it() {
            let addr = SocketAddr::from_abstract_name(format!(
                "aidoku.test.pressure.connected.{}",
                std::process::id()
            ))
            .unwrap();
            let l = UnixListener::bind_addr(&addr).unwrap();
            let mut client = UnixStream::connect_addr(&addr).unwrap();
            client.write_all(&[TAKE]).unwrap();
            let previous = std::cell::Cell::new(true);
            service(l, vec![7], now().unwrap() + TTL, || previous.replace(false)).unwrap();
            assert_eq!(client.read(&mut [0]).unwrap(), 0);
            assert_retired(&addr);
        }
    }
}
