//! Standalone host exerciser. Its executable and keeper have the same inode, as on Kindle.
//! Run `cargo run --release -- suite <empty-temporary-directory>` on the Linux test worker.

#[path = "../../../../../src/resident_transport.rs"]
mod resident_transport;

use resident_transport::{MAX_BYTES, Peer, publish, stop, take};
use std::{
    collections::BTreeMap,
    fs,
    io::{self, Read, Write},
    os::{
        fd::{AsRawFd, OwnedFd},
        linux::net::SocketAddrExt,
        unix::{
            net::{SocketAddr, UnixStream},
            process::CommandExt,
        },
    },
    path::Path,
    process::{Command, ExitCode, Stdio},
    thread,
    time::{Duration, Instant},
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

fn blob(n: usize) -> Vec<u8> {
    (0..n).map(|p| (p.wrapping_mul(17) % 251) as u8).collect()
}

fn state(p: Peer) -> Option<(char, u64)> {
    let stat = fs::read_to_string(format!("/proc/{}/stat", p.pid)).ok()?;
    let (_, tail) = stat.rsplit_once(')')?;
    let f = tail.split_whitespace().collect::<Vec<_>>();
    if f.get(19)?.parse::<u64>().ok()? != p.birth {
        return None;
    }
    let cpu = f.get(11)?.parse::<u64>().ok()? + f.get(12)?.parse::<u64>().ok()?;
    Some((f.first()?.chars().next()?, cpu))
}

fn vanished(p: Peer, end: Instant) -> bool {
    while Instant::now() < end {
        if state(p).is_none() {
            return true;
        }
        thread::sleep(Duration::from_millis(10));
    }
    false
}

fn resources(p: Peer) -> Result<(u64, u64, String)> {
    let root = format!("/proc/{}", p.pid);
    let status = fs::read_to_string(format!("{root}/status"))?;
    let rss = status
        .lines()
        .find_map(|s| s.strip_prefix("VmRSS:"))
        .unwrap()
        .split_whitespace()
        .next()
        .unwrap()
        .parse::<u64>()?;
    let fd_list = || {
        fs::read_dir(format!("{root}/fd"))?
            .map(|e| -> io::Result<_> {
                let e = e?;
                Ok((e.file_name(), fs::read_link(e.path())?))
            })
            .collect::<io::Result<BTreeMap<_, _>>>()
    };
    // A pressure check can transiently open /proc/meminfo between readdir and readlink.
    let mut snapshot = fd_list();
    for _ in 0..5 {
        if !matches!(&snapshot, Err(e) if e.kind() == io::ErrorKind::NotFound)
            && !snapshot
                .as_ref()
                .is_ok_and(|set| set.values().any(|v| v == Path::new("/proc/meminfo")))
        {
            break;
        }
        thread::sleep(Duration::from_millis(2));
        snapshot = fd_list();
    }
    let fds = snapshot?;
    assert_eq!(fds.len(), 4, "unexpected open descriptors: {fds:?}");
    for id in ["0", "1", "2"] {
        assert_eq!(
            fds.get(std::ffi::OsStr::new(id)).unwrap(),
            Path::new("/dev/null")
        );
    }
    let inode = fds
        .values()
        .find_map(|v| v.to_str()?.strip_prefix("socket:[")?.strip_suffix(']'))
        .unwrap();
    let unix = fs::read_to_string(format!("{root}/net/unix"))?;
    let endpoint = unix
        .lines()
        .find_map(|line| {
            let fields = line.split_whitespace().collect::<Vec<_>>();
            (fields.get(3) == Some(&"00010000")
                && fields.get(4) == Some(&"0001")
                && fields.get(6) == Some(&inode))
            .then(|| fields.get(7)?.strip_prefix('@'))
            .flatten()
        })
        .ok_or("expected one listening Unix stream")?;
    assert!(endpoint.starts_with("aidoku.resident.v1."));
    assert_eq!(
        fs::read(format!("{root}/cmdline"))?,
        b"aidoku-resident-keeper\0--resident-keeper\0"
    );
    assert!(fs::read(format!("{root}/environ"))?.is_empty());
    assert_eq!(fs::read_link(format!("{root}/cwd"))?, Path::new("/"));
    let raw = fs::read_to_string(format!("{root}/stat"))?;
    let f = raw
        .rsplit_once(')')
        .unwrap()
        .1
        .split_whitespace()
        .collect::<Vec<_>>();
    // Field 6: session ID.
    assert_eq!(f[3].parse::<u32>()?, p.pid);
    let (st, cpu) = state(p).unwrap();
    assert!(matches!(st, 'S' | 'R'));
    Ok((rss, cpu, endpoint.to_owned()))
}

fn broken_publication(commit_fails: bool, exit_publisher: bool) -> Result<()> {
    let (mut client, setup) = UnixStream::pair()?;
    client.set_read_timeout(Some(Duration::from_millis(700)))?;
    let mut child = Command::new("/proc/self/exe")
        .arg0("aidoku-resident-keeper")
        .arg(resident_transport::KEEPER_ARG)
        .env_clear()
        .stdin(Stdio::from(OwnedFd::from(setup)))
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    client.write_all(b"ARK1")?;
    let mut key = [0; 32];
    key[..4].copy_from_slice(&child.id().to_be_bytes());
    client.write_all(&key)?;
    client.write_all(&42u32.to_be_bytes())?;
    if commit_fails {
        client.write_all(&[5; 42])?;
        let mut b = [0];
        client.read_exact(&mut b)?;
        assert_eq!(&b, b"R");
    } else {
        client.write_all(&[5; 8])?;
    }
    if exit_publisher {
        let stat = fs::read_to_string(format!("/proc/{}/stat", child.id()))?;
        let birth = stat
            .rsplit_once(')')
            .unwrap()
            .1
            .split_whitespace()
            .nth(19)
            .unwrap();
        println!("{} {birth}", child.id());
        io::stdout().flush()?;
        // The OS, not a Rust destructor, now closes the half-published socket.
        std::process::exit(0);
    }
    drop(client);
    assert!(!child.wait()?.success());
    Ok(())
}

fn exited_publisher(mode: &str, data: &Path) -> Result<Peer> {
    let c = Command::new("/proc/self/exe")
        .arg(mode)
        .arg(data)
        .output()?;
    assert!(c.status.success(), "{}", String::from_utf8_lossy(&c.stderr));
    let text = std::str::from_utf8(&c.stdout)?;
    let (pid, birth) = text.trim().split_once(' ').ok_or("invalid owner receipt")?;
    Ok(Peer {
        pid: pid.parse()?,
        birth: birth.parse()?,
    })
}

fn suite(base: &Path) -> Result<()> {
    if fs::read_dir(base)?.next().is_some() {
        return Err("a new empty fixture directory is required".into());
    }
    let data = base.join("data");
    let other = base.join("other");
    fs::create_dir(&data)?;
    fs::create_dir(&other)?;
    let start = Instant::now();
    assert!(take(&data)?.is_none() && !stop(&data)?);
    assert!(publish(&data, &[]).is_err());
    assert!(publish(&data, &vec![0; MAX_BYTES + 1]).is_err());
    let leaked = fs::File::create(base.join("intentional-inheritance-probe"))?;
    if unsafe { libc::fcntl(leaked.as_raw_fd(), libc::F_SETFD, 0) } < 0 {
        return Err(io::Error::last_os_error().into());
    }
    let original = blob(MAX_BYTES);
    let begun = Instant::now();
    let p = publish(&data, &original)?;
    println!(
        "published: pid={} birth={} bytes={} elapsed_us={}",
        p.pid,
        p.birth,
        original.len(),
        begun.elapsed().as_micros()
    );
    let (rss0, cpu0, address) = resources(p)?;
    assert!(take(&other)?.is_none() && !stop(&other)?);
    let replacement = base.join("other-executable");
    fs::copy("/proc/self/exe", &replacement)?;
    assert!(
        Command::new(&replacement)
            .arg("miss")
            .arg(&data)
            .status()?
            .success()
    );
    assert!(
        Command::new(&replacement)
            .args(["check-rejection", &address])
            .status()?
            .success()
    );
    thread::sleep(Duration::from_millis(3100));
    let (rss1, cpu1, _) = resources(p)?;
    println!(
        "idle: ms=3100 cpu_ticks={} rss_before_kib={} rss_after_kib={} fds=devnull*3+unix-listener session_isolated=true",
        cpu1 - cpu0,
        rss0,
        rss1
    );
    assert!(cpu1 - cpu0 <= 2);
    let c = Command::new("/proc/self/exe")
        .arg("consume")
        .arg(&data)
        .arg(p.pid.to_string())
        .arg(p.birth.to_string())
        .arg(MAX_BYTES.to_string())
        .output()?;
    print!("{}", String::from_utf8_lossy(&c.stdout));
    assert!(c.status.success(), "{}", String::from_utf8_lossy(&c.stderr));
    assert!(take(&data)?.is_none());
    assert!(vanished(p, Instant::now() + Duration::from_secs(1)));
    let old = publish(&data, &[1, 2])?;
    let next = publish(&data, &[3, 4])?;
    assert_ne!(old, next);
    assert!(vanished(old, Instant::now() + Duration::from_secs(1)));
    assert!(stop(&data)?);
    assert!(vanished(next, Instant::now() + Duration::from_secs(1)));
    assert!(take(&data)?.is_none());
    broken_publication(false, false)?;
    broken_publication(true, false)?;
    let abandoned = exited_publisher("abandon-ready", &data)?;
    assert!(vanished(abandoned, Instant::now() + Duration::from_secs(2)));
    let detached = exited_publisher("publish-and-exit", &data)?;
    resources(detached)?;
    let (owner, bytes) = take(&data)?.ok_or("publisher exit discarded committed data")?;
    assert_eq!(owner, detached);
    assert_eq!(bytes, [3, 5, 8]);
    assert!(vanished(detached, Instant::now() + Duration::from_secs(2)));
    println!("publisher exit: committed transfer survives; uncommitted READY is revoked");
    let frozen = publish(&data, &[66])?;
    let normal = publish(&other, &[77])?;
    // These are direct children created by this probe; no installed process is touched.
    assert_eq!(unsafe { libc::kill(frozen.pid as i32, libc::SIGSTOP) }, 0);
    let before = Instant::now();
    let stopped_read = take(&data);
    assert!(
        stopped_read
            .as_ref()
            .is_err_and(|e| e.kind() == io::ErrorKind::TimedOut)
    );
    let fallback_ms = before.elapsed().as_millis();
    assert!(fallback_ms < 400);
    println!("frozen-peer cold fallback: {} ms", fallback_ms);
    let expiry = Instant::now() + Duration::from_secs(47);
    assert!(vanished(normal, expiry));
    assert!(vanished(frozen, expiry));
    assert!(take(&data)?.is_none() && take(&other)?.is_none());
    assert!(fs::read_dir(&data)?.next().is_none() && fs::read_dir(&other)?.next().is_none());
    println!(
        "two independent PIDs expired (running and SIGSTOP); interrupted writes/commit, namespace/ELF mismatch, one-shot, explicit stop and empty store verified; suite_ms={}",
        start.elapsed().as_millis()
    );
    Ok(())
}

fn run() -> Result<()> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    match args.as_slice() {
        [cmd, dir] if cmd == "suite" => suite(Path::new(dir)),
        [cmd, _] if cmd == "abandon-ready" => broken_publication(true, true),
        [cmd, dir] if cmd == "publish-and-exit" => {
            let p = publish(Path::new(dir), &[3, 5, 8])?;
            println!("{} {}", p.pid, p.birth);
            Ok(())
        }
        [cmd, dir] if cmd == "miss" => {
            assert!(take(Path::new(dir))?.is_none());
            Ok(())
        }
        [cmd, address] if cmd == "check-rejection" => {
            let mut socket = UnixStream::connect_addr(&SocketAddr::from_abstract_name(address)?)?;
            socket.set_read_timeout(Some(Duration::from_millis(500)))?;
            // This process runs a separate copy of the fixture. The original, known
            // endpoint must close before it gives this different executable any bytes.
            if let Err(e) = socket.write_all(b"T") {
                if matches!(
                    e.kind(),
                    io::ErrorKind::BrokenPipe | io::ErrorKind::ConnectionReset
                ) {
                    return Ok(());
                }
                return Err(e.into());
            }
            match socket.read(&mut [0]) {
                Ok(0) => Ok(()),
                Err(e) if e.kind() == io::ErrorKind::ConnectionReset => Ok(()),
                other => Err(format!("foreign executable was not rejected: {other:?}").into()),
            }
        }
        [cmd, dir, pid, birth, n] if cmd == "consume" => {
            let start = Instant::now();
            let (owner, bytes) = take(Path::new(dir))?.ok_or("expected genuine keeper data")?;
            let us = start.elapsed().as_micros();
            assert_eq!(
                owner,
                Peer {
                    pid: pid.parse()?,
                    birth: birth.parse()?
                }
            );
            assert_eq!(bytes, blob(n.parse()?));
            println!(
                "take: new_pid={} keeper_pid={} bytes={} elapsed_us={us}",
                std::process::id(),
                owner.pid,
                bytes.len()
            );
            Ok(())
        }
        _ => Err("expected suite <empty-directory>".into()),
    }
}
fn main() -> ExitCode {
    if let Some(code) = resident_transport::entry() {
        return code;
    }
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{e}");
            ExitCode::FAILURE
        }
    }
}
