//! Read-only evidence for a new native Library activation on a qualified route.

use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, Metadata, OpenOptions},
    io::{self, Read},
    mem::MaybeUninit,
    os::{
        fd::{AsFd, AsRawFd},
        unix::{
            fs::{MetadataExt, OpenOptionsExt},
            net::UnixStream,
        },
    },
    path::{Path, PathBuf},
};

const DOCUMENT_PATH: &str = "/mnt/us/documents/Aidoku.sh";
const SU_DOCUMENT_ARGV: &[u8] = b"su\0-c\0sh -l \"/mnt/us/documents/Aidoku.sh\"\0";
const MAX_PROC_BYTES: u64 = 64 * 1024;
const MAX_QUALIFIED_FILE_BYTES: u64 = 16 * 1024 * 1024;

struct ProcessSpec {
    executable: &'static str,
    sha256: [u8; 32],
    argv: &'static [u8],
    effective_uid: u32,
}

struct QualifiedRoute {
    waiter_argv: &'static [u8],
    waiter_uid: u32,
    waiter_gid: u32,
    /// Ordered direct parents from the socket peer up to the child of `su`.
    intermediates: &'static [ProcessSpec],
    su: ProcessSpec,
    handler: ProcessSpec,
    manager: ProcessSpec,
    document_sha256: [u8; 32],
}

// Set only from an independently reviewed capture of the live stock route.
// A source-level candidate or a fixture cannot make this option available.
const QUALIFIED_ROUTE: Option<QualifiedRoute> = None;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
pub(crate) struct ProcessBirth {
    pub pid: u32,
    pub birth: u64,
}

/// A point-in-time observation. The supervisor must consume the visit key once
/// and separately check TTL, display ownership, and fresh native calibration.
#[derive(Debug, Eq, PartialEq)]
pub(crate) struct NativeLibraryVisit {
    pub waiter: ProcessBirth,
    pub su: ProcessBirth,
    pub handler: ProcessBirth,
    socket: (u64, u64),
    document: FileIdentity,
    chain: Vec<ProcessSnapshot>,
}

pub(crate) fn observe(stream: &UnixStream) -> io::Result<NativeLibraryVisit> {
    let route = QUALIFIED_ROUTE.as_ref().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::Unsupported,
            "No qualified native Library visit route",
        )
    })?;
    observe_qualified(stream, route)
}

impl NativeLibraryVisit {
    pub(crate) fn revalidate(&self, stream: &UnixStream) -> io::Result<()> {
        require(socket_identity(stream)? == self.socket)?;
        require(observe(stream)? == *self)
    }
}

fn observe_qualified(
    stream: &UnixStream,
    route: &QualifiedRoute,
) -> io::Result<NativeLibraryVisit> {
    require(route.su.argv == SU_DOCUMENT_ARGV)?;
    let socket = socket_identity(stream)?;
    let peer = credentials(stream)?;
    require((peer.uid, peer.gid) == (route.waiter_uid, route.waiter_gid))?;

    let waiter = process(peer.pid, route.waiter_argv, route.waiter_uid, None)?;
    require(waiter.ids == (peer.uid, peer.gid))?;
    let mut parent = waiter.parent;
    let mut chain = vec![waiter.clone()];
    for spec in route.intermediates {
        let ancestor = qualified_process(parent, spec)?;
        check_lineage(&ancestor, &chain)?;
        parent = ancestor.parent;
        chain.push(ancestor);
    }
    let su = qualified_process(parent, &route.su)?;
    let handler = qualified_process(su.parent, &route.handler)?;
    let manager = qualified_process(handler.parent, &route.manager)?;
    for ancestor in [&su, &handler, &manager] {
        check_lineage(ancestor, &chain)?;
        chain.push(ancestor.clone());
    }

    let document = qualified_file(Path::new(DOCUMENT_PATH), &route.document_sha256)?;
    // Recheck the live links after all slow file reads.
    for recorded in &chain {
        let (current, parent) = birth_and_parent(recorded.id.pid)?;
        require(current == recorded.id && parent == recorded.parent)?;
        require(executable(recorded.id.pid)? == recorded.executable)?;
        let proc = proc_path(recorded.id.pid);
        require(read_bounded(&proc.join("cmdline"), MAX_PROC_BYTES)? == recorded.argv)?;
        require(effective_ids(&proc)? == recorded.ids)?;
    }
    require(credentials(stream)? == peer && socket_identity(stream)? == socket)?;
    require(FileIdentity::new(&fs::symlink_metadata(DOCUMENT_PATH)?) == document)?;
    Ok(NativeLibraryVisit {
        waiter: waiter.id,
        su: su.id,
        handler: handler.id,
        socket,
        document,
        chain,
    })
}

fn check_lineage(ancestor: &ProcessSnapshot, chain: &[ProcessSnapshot]) -> io::Result<()> {
    let child = chain.last().ok_or_else(denied)?;
    require(ancestor.id.pid == child.parent && ancestor.id.birth <= child.id.birth)?;
    require(chain.iter().all(|member| member.id.pid != ancestor.id.pid))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Credentials {
    pid: u32,
    uid: u32,
    gid: u32,
}

fn credentials(stream: &UnixStream) -> io::Result<Credentials> {
    let mut value = MaybeUninit::<libc::ucred>::uninit();
    let mut size = size_of::<libc::ucred>() as libc::socklen_t;
    if unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            value.as_mut_ptr().cast(),
            &mut size,
        )
    } != 0
    {
        return Err(io::Error::last_os_error());
    }
    require(size as usize == size_of::<libc::ucred>())?;
    let value = unsafe { value.assume_init() };
    require(value.pid > 1)?;
    Ok(Credentials {
        pid: value.pid as u32,
        uid: value.uid,
        gid: value.gid,
    })
}

fn socket_identity(stream: &UnixStream) -> io::Result<(u64, u64)> {
    let mut poll = libc::pollfd {
        fd: stream.as_raw_fd(),
        events: libc::POLLRDHUP,
        revents: 0,
    };
    if unsafe { libc::poll(&mut poll, 1, 0) } < 0 {
        return Err(io::Error::last_os_error());
    }
    require(
        poll.revents & (libc::POLLHUP | libc::POLLERR | libc::POLLNVAL | libc::POLLRDHUP) == 0,
    )?;
    let metadata = File::from(stream.as_fd().try_clone_to_owned()?).metadata()?;
    Ok((metadata.dev(), metadata.ino()))
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ProcessSnapshot {
    id: ProcessBirth,
    parent: u32,
    executable: (PathBuf, FileIdentity),
    argv: Vec<u8>,
    ids: (u32, u32),
}

fn qualified_process(pid: u32, spec: &ProcessSpec) -> io::Result<ProcessSnapshot> {
    process(pid, spec.argv, spec.effective_uid, Some(spec))
}

fn process(
    pid: u32,
    argv: &[u8],
    uid: u32,
    spec: Option<&ProcessSpec>,
) -> io::Result<ProcessSnapshot> {
    let (id, parent) = birth_and_parent(pid)?;
    let proc = proc_path(pid);
    let observed_argv = read_bounded(&proc.join("cmdline"), MAX_PROC_BYTES)?;
    require(!argv.is_empty() && observed_argv == argv)?;
    let ids = effective_ids(&proc)?;
    require(ids.0 == uid)?;
    let exe = executable(pid)?;
    if let Some(spec) = spec {
        let installed = Path::new(spec.executable);
        require(exe.0 == installed)?;
        require(exe.1.uid == 0 && exe.1.mode & 0o022 == 0)?;
        require(qualified_file(installed, &spec.sha256)? == exe.1)?;
    } else {
        require(exe == executable(std::process::id())?)?;
    }
    require(birth_and_parent(pid)? == (id, parent))?;
    Ok(ProcessSnapshot {
        id,
        parent,
        executable: exe,
        argv: observed_argv,
        ids,
    })
}

fn proc_path(pid: u32) -> PathBuf {
    PathBuf::from(format!("/proc/{pid}"))
}

fn birth_and_parent(pid: u32) -> io::Result<(ProcessBirth, u32)> {
    require(pid > 1)?;
    let stat = read_bounded(&proc_path(pid).join("stat"), MAX_PROC_BYTES)?;
    require(stat.starts_with(format!("{pid} (").as_bytes()))?;
    let close = stat.iter().rposition(|&b| b == b')').ok_or_else(denied)?;
    let tail =
        std::str::from_utf8(stat.get(close + 1..).ok_or_else(denied)?).map_err(|_| denied())?;
    require(tail.starts_with(' '))?;
    let mut fields = tail.split_ascii_whitespace();
    require(matches!(fields.next(), Some("R" | "S" | "D" | "I")))?;
    let parent = fields
        .next()
        .ok_or_else(denied)?
        .parse::<u32>()
        .map_err(|_| denied())?;
    let birth = fields
        .nth(17)
        .ok_or_else(denied)?
        .parse::<u64>()
        .map_err(|_| denied())?;
    require(birth > 0)?;
    Ok((ProcessBirth { pid, birth }, parent))
}

fn effective_ids(proc: &Path) -> io::Result<(u32, u32)> {
    let raw = read_bounded(&proc.join("status"), MAX_PROC_BYTES)?;
    let parse = |key| -> io::Result<u32> {
        let value = raw
            .split(|b| *b == b'\n')
            .find_map(|line| line.strip_prefix(key))
            .ok_or_else(denied)?;
        std::str::from_utf8(value)
            .map_err(|_| denied())?
            .split_ascii_whitespace()
            .nth(1)
            .ok_or_else(denied)?
            .parse()
            .map_err(|_| denied())
    };
    Ok((parse(b"Uid:" as &[u8])?, parse(b"Gid:" as &[u8])?))
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct FileIdentity {
    device: u64,
    inode: u64,
    mode: u32,
    uid: u32,
    len: u64,
    mtime: (i64, i64),
    ctime: (i64, i64),
}

impl FileIdentity {
    fn new(info: &Metadata) -> Self {
        Self {
            device: info.dev(),
            inode: info.ino(),
            mode: info.mode(),
            uid: info.uid(),
            len: info.len(),
            mtime: (info.mtime(), info.mtime_nsec()),
            ctime: (info.ctime(), info.ctime_nsec()),
        }
    }
}

fn executable(pid: u32) -> io::Result<(PathBuf, FileIdentity)> {
    let path = proc_path(pid).join("exe");
    let target = fs::read_link(&path)?;
    let info = fs::metadata(path)?;
    require(info.is_file())?;
    Ok((target, FileIdentity::new(&info)))
}

fn qualified_file(path: &Path, expected: &[u8; 32]) -> io::Result<FileIdentity> {
    require(fs::canonicalize(path)? == path)?;
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW)
        .open(path)?;
    let info = file.metadata()?;
    require(info.is_file() && (1..=MAX_QUALIFIED_FILE_BYTES).contains(&info.len()))?;
    let before = FileIdentity::new(&info);
    let mut count = 0;
    let mut hash = Sha256::new();
    let mut chunk = [0; 8192];
    let mut reader = (&file).take(MAX_QUALIFIED_FILE_BYTES + 1);
    loop {
        let n = reader.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        count += n as u64;
        hash.update(&chunk[..n]);
    }
    require(count == before.len && &<[u8; 32]>::from(hash.finalize()) == expected)?;
    require(FileIdentity::new(&file.metadata()?) == before)?;
    require(FileIdentity::new(&fs::symlink_metadata(path)?) == before)?;
    Ok(before)
}

fn read_bounded(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    File::open(path)?.take(limit + 1).read_to_end(&mut bytes)?;
    require(bytes.len() as u64 <= limit)?;
    Ok(bytes)
}

fn require(valid: bool) -> io::Result<()> {
    if valid { Ok(()) } else { Err(denied()) }
}

fn denied() -> io::Error {
    io::ErrorKind::PermissionDenied.into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_profile_never_admits_a_connected_peer() {
        let (server, _peer) = UnixStream::pair().unwrap();
        let error = observe(&server).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::Unsupported);
    }

    #[test]
    fn private_socket_tracks_the_kernel_peer_and_disconnect() {
        let (server, peer) = UnixStream::pair().unwrap();
        let pid = std::process::id();
        let cred = credentials(&server).unwrap();
        assert_eq!(cred.pid, pid);
        assert_eq!(
            effective_ids(&proc_path(pid)).unwrap(),
            (cred.uid, cred.gid)
        );
        let (process, parent) = birth_and_parent(pid).unwrap();
        assert_eq!(process.pid, pid);
        assert!(process.birth > 0);
        assert_eq!(parent, unsafe { libc::getppid() } as u32);
        assert_ne!(
            socket_identity(&server).unwrap(),
            socket_identity(&peer).unwrap()
        );
        drop(peer);
        assert_eq!(
            socket_identity(&server).unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
    }
}
