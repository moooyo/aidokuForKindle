//! Generation-bound native portrait observations from the owning launcher.
//!
//! The private-directory and publication rules are adapted from the reviewed
//! native-portrait-0.1.15 reference. Native rotation codes never identify a pose
//! without the stable pre-takeover WM/GETVAR transaction from this exact run.

use std::{
    fs::{self, File, Metadata, OpenOptions},
    io::Read,
    path::{Path, PathBuf},
    sync::Arc,
};

use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use slint_backend_kindle::{
    NativeFramebufferObservation, NativePortraitOrientation, NativePortraitSensorObservation,
};

use crate::platform::DeviceProfile;

mod portrait_writer;

const MAX_MARKER_BYTES: usize = 8192;
const MAX_STATUS_BYTES: usize = 64 * 1024;
const STARTUP_RECORD: &str = "native-portrait-calibration";
const RESUME_RECORD: &str = "resume-native-portrait-calibration";
const STARTUP_PENDING: &str = "native-portrait-calibration.pending";

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
enum NativeDirection {
    #[serde(rename = "U")]
    Up,
    #[serde(rename = "D")]
    Down,
    #[serde(rename = "FIXED")]
    Fixed,
}

impl NativeDirection {
    fn backend(self) -> NativePortraitOrientation {
        match self {
            Self::Up | Self::Fixed => NativePortraitOrientation::Up,
            Self::Down => NativePortraitOrientation::Down,
        }
    }

    fn resolve(
        self,
        baseline: u32,
        current: u32,
        resuming: bool,
    ) -> Result<NativePortraitOrientation> {
        ensure!(
            baseline <= 3 && current <= 3,
            "The native framebuffer rotation is unknown"
        );
        match (current + 4 - baseline) % 4 {
            0 => Ok(self.backend()),
            2 if resuming && self != Self::Fixed => Ok(match self {
                Self::Up => NativePortraitOrientation::Down,
                Self::Down => NativePortraitOrientation::Up,
                Self::Fixed => unreachable!("fixed display rejects a rotation delta"),
            }),
            _ => bail!("The native framebuffer changed outside the calibrated portrait transition"),
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
enum NativeMode {
    #[serde(rename = "Y8")]
    Y8,
    #[serde(rename = "Y8INV")]
    Y8Inverted,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct ExecutableIdentity {
    path: PathBuf,
    device: u64,
    inode: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct ProcessIdentity {
    pid: u32,
    parent: u32,
    group: u32,
    session: u32,
    birth: u64,
    executable: ExecutableIdentity,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct StartupCalibration {
    version: u8,
    generation: u64,
    nonce: String,
    launcher: ProcessIdentity,
    publisher: ExecutableIdentity,
    profile: String,
    native_mode: NativeMode,
    native_direction: NativeDirection,
    raw_rotation: u32,
    layout: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
struct ResumePermission {
    version: u8,
    generation: u64,
    nonce: String,
    launcher: ProcessIdentity,
    application: ProcessIdentity,
    runner: ProcessIdentity,
    epoch: u64,
    presentation: ResumePresentation,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
enum ResumePresentation {
    /// Permission to read fresh native metadata, not a cached display mode.
    #[serde(rename = "CALIBRATED_FRAMEBUFFER")]
    CalibratedFramebuffer,
}

/// A current holding pose and a separately calibrated physical scanout pose.
pub struct NativePortraitObservation {
    pub automatic: bool,
    /// True only when the current recognized sensor supplied a valid pose.
    pub holding_from_sensor: bool,
    pub holding: NativePortraitOrientation,
    pub scanout: NativePortraitOrientation,
    pub framebuffer: NativeFramebufferObservation,
}

#[derive(Clone)]
pub struct NativePresentationContext {
    records: Arc<PrivateDirectory>,
    startup: StartupCalibration,
    startup_raw: String,
    application: u32,
}

impl NativePresentationContext {
    /// An unmanaged invocation retains the backend's existing launch behavior.
    /// A managed invocation must supply the complete protected startup record.
    pub fn from_environment(profile: DeviceProfile) -> Result<Option<Self>> {
        let directory = std::env::var_os("AIDOKU_LAUNCHER_DIR");
        let nonce = std::env::var("AIDOKU_LAUNCH_NONCE").ok();
        if directory.is_none() && nonce.is_none() {
            return Ok(None);
        }
        let records = Arc::new(PrivateDirectory::open(Path::new(
            &directory.context("Missing native presentation directory")?,
        ))?);
        let startup_raw = records.read(STARTUP_RECORD)?;
        let startup: StartupCalibration = parse_json_record(&startup_raw)?;
        ensure!(
            startup.version == 1,
            "Unknown native portrait calibration version"
        );
        ensure!(
            startup.generation > 0 && valid_nonce(&startup.nonce),
            "Invalid native portrait binding"
        );
        ensure!(
            Some(startup.nonce.as_str()) == nonce.as_deref(),
            "The portrait calibration belongs to another launch"
        );
        ensure!(
            startup.profile == profile.id(),
            "The portrait calibration belongs to another device profile"
        );
        ensure!(
            startup.raw_rotation <= 3 && valid_layout_token(&startup.layout),
            "Invalid native portrait framebuffer calibration"
        );
        let result = Self {
            records,
            startup,
            startup_raw,
            application: std::process::id(),
        };
        result.binding(false)?;
        result.ensure_startup_current()?;
        Ok(Some(result))
    }

    /// Run before the initial native framebuffer or input is acquired.
    pub fn observe_startup(&self) -> Result<NativePortraitObservation> {
        self.observe(None)
    }

    /// Run while display/input ownership is still paused for this exact epoch.
    pub fn observe_resume(&self, epoch: u64) -> Result<NativePortraitObservation> {
        ensure!(
            epoch > 0,
            "A portrait resume requires a current sleep epoch"
        );
        self.observe(Some(epoch))
    }

    fn observe(&self, epoch: Option<u64>) -> Result<NativePortraitObservation> {
        let before = self.binding(epoch.is_some())?;
        self.ensure_startup_current()?;
        let permission = epoch
            .map(|value| self.resume_permission(value))
            .transpose()?;
        let framebuffer = slint_backend_kindle::probe_native_framebuffer()
            .context("Observe the native framebuffer without acquiring display ownership")?;
        ensure!(
            framebuffer.layout_token() == self.startup.layout,
            "The native framebuffer layout changed from this launch's calibration"
        );
        let scanout = self.startup.native_direction.resolve(
            self.startup.raw_rotation,
            framebuffer.rotation,
            epoch.is_some(),
        )?;
        // Recognized non-pose samples retain automatic observation. Their
        // holding fallback is this transaction's fresh calibrated scanout.
        let (holding, holding_from_sensor) = match self.startup.native_direction {
            NativeDirection::Fixed => (NativePortraitOrientation::Up, false),
            _ => resolve_current_holding(
                scanout,
                slint_backend_kindle::observe_native_portrait_sensor()
                    .context("Observe the current trusted native portrait sensor")?,
            )?,
        };
        self.ensure_startup_current()?;
        if let Some(value) = epoch {
            ensure!(
                self.resume_permission(value)? == permission.unwrap(),
                "The portrait resume permission changed during observation"
            );
        }
        let after = self.binding(epoch.is_some())?;
        ensure!(
            before.agrees_with(&after),
            "Native presentation ownership changed during observation"
        );
        self.records.ensure_current()?;
        Ok(NativePortraitObservation {
            automatic: self.startup.native_direction != NativeDirection::Fixed,
            holding_from_sensor,
            holding,
            scanout,
            framebuffer,
        })
    }

    fn ensure_startup_current(&self) -> Result<()> {
        ensure!(
            self.records.read(STARTUP_RECORD)? == self.startup_raw,
            "The native portrait calibration changed during this run"
        );
        Ok(())
    }

    fn resume_permission(&self, epoch: u64) -> Result<ResumePermission> {
        let expected = format!("{} {} {epoch}\n", self.application, self.startup.nonce);
        ensure!(
            self.records.read("epoch")? == format!("{epoch}\n"),
            "The current sleep epoch changed"
        );
        ensure!(
            self.records.read("sleep-request")? == expected
                && self.records.read("resume-request")? == expected,
            "The portrait observation does not match the current resume request"
        );
        let permission: ResumePermission = parse_json_record(&self.records.read(RESUME_RECORD)?)?;
        ensure!(
            permission.version == 1
                && permission.generation == self.startup.generation
                && permission.nonce == self.startup.nonce
                && permission.launcher == self.startup.launcher
                && permission.epoch == epoch
                && permission.application.pid == self.application,
            "The portrait permission belongs to another launch or sleep epoch"
        );
        ensure!(
            process_identity(permission.application.pid)? == permission.application
                && process_identity(permission.runner.pid)? == permission.runner
                && permission.application.parent == permission.runner.pid
                && permission.runner.parent == permission.launcher.pid,
            "The authorized resumed application identity changed"
        );
        Ok(permission)
    }

    fn binding(&self, require_application: bool) -> Result<BindingIdentity> {
        self.records.ensure_current()?;
        ensure!(
            self.records.read("generation")?
                == format!("{}:{}\n", self.startup.generation, self.startup.nonce)
                && self.records.read("nonce")? == format!("{}\n", self.startup.nonce)
                && self.records.read("pid")? == format!("{}\n", self.startup.launcher.pid),
            "The native presentation belongs to another launcher generation"
        );
        ensure!(
            process_identity(self.startup.launcher.pid)? == self.startup.launcher,
            "The native launcher identity changed"
        );
        let application = process_identity(self.application)?;
        let runner = process_identity(application.parent)?;
        ensure!(
            application.pid != runner.pid
                && runner.pid != self.startup.launcher.pid
                && runner.parent == self.startup.launcher.pid
                && application.executable == self.startup.publisher,
            "The application is not owned by the native presentation launcher"
        );
        let marker = self.records.read_optional("application-pid")?;
        ensure!(
            !require_application || marker.is_some(),
            "The resumed application identity is missing"
        );
        ensure!(
            marker
                .as_ref()
                .is_none_or(|line| line == &format!("{}\n", self.application)),
            "Native presentation records belong to another application"
        );
        Ok(BindingIdentity {
            application,
            runner,
            marker_present: marker.is_some(),
        })
    }
}

fn resolve_current_holding(
    scanout: NativePortraitOrientation,
    observation: Option<NativePortraitSensorObservation>,
) -> Result<(NativePortraitOrientation, bool)> {
    let sensor = observation.context("The calibrated portrait sensor is unavailable")?;
    // The caller has already resolved scanout from this same-run anchor and
    // fresh layout-checked GETVAR. An unknown sample makes no holding claim.
    Ok(match sensor.pose {
        Some(pose) => (pose, true),
        None => (scanout, false),
    })
}

struct BindingIdentity {
    application: ProcessIdentity,
    runner: ProcessIdentity,
    marker_present: bool,
}

impl BindingIdentity {
    fn agrees_with(&self, after: &Self) -> bool {
        self.application == after.application
            && self.runner == after.runner
            && (!self.marker_present || after.marker_present)
    }
}

pub fn capture_startup(profile: DeviceProfile, cancelled: impl Fn() -> bool) -> Result<()> {
    portrait_writer::capture_startup(profile, cancelled)
}

pub fn authorize_resume(epoch: u64, cancelled: impl Fn() -> bool) -> Result<()> {
    portrait_writer::authorize_resume(epoch, cancelled)
}

fn valid_nonce(nonce: &str) -> bool {
    nonce.len() == 32 && nonce.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn valid_layout_token(layout: &str) -> bool {
    !layout.is_empty()
        && layout.len() <= 2048
        && layout
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b':' | b',' | b'-'))
}

fn parse_json_record<T: for<'de> Deserialize<'de>>(raw: &str) -> Result<T> {
    let body = raw
        .strip_suffix('\n')
        .context("A native presentation record requires exactly one LF")?;
    ensure!(
        !body.contains('\n') && !body.contains('\r') && raw.len() <= MAX_MARKER_BYTES,
        "Invalid native presentation record framing"
    );
    Ok(serde_json::from_str(body)?)
}

fn process_identity(pid: u32) -> Result<ProcessIdentity> {
    use std::os::unix::fs::MetadataExt;
    ensure!(pid > 1, "Invalid native process identifier");
    let path = PathBuf::from(format!("/proc/{pid}"));
    let raw = fs::read_to_string(path.join("stat"))?;
    ensure!(
        raw.len() <= MAX_STATUS_BYTES,
        "The native process record is too large"
    );
    let (head, tail) = raw
        .rsplit_once(") ")
        .context("Invalid native process stat record")?;
    ensure!(
        head.starts_with(&format!("{pid} (")),
        "The native process identifier changed"
    );
    let values = tail.split_ascii_whitespace().collect::<Vec<_>>();
    ensure!(
        values.len() >= 20 && !matches!(values[0], "Z" | "X" | "x"),
        "The native process is no longer alive"
    );
    let executable_path = path.join("exe");
    let metadata = fs::metadata(&executable_path)?;
    Ok(ProcessIdentity {
        pid,
        parent: values[1].parse()?,
        group: values[2].parse()?,
        session: values[3].parse()?,
        birth: values[19].parse()?,
        executable: ExecutableIdentity {
            path: fs::read_link(executable_path)?,
            device: metadata.dev(),
            inode: metadata.ino(),
        },
    })
}

/// Keep the opened directory pinned so a path replacement cannot mix records.
pub(crate) struct PrivateDirectory {
    path: PathBuf,
    directory: File,
    identity: Metadata,
}

impl PrivateDirectory {
    pub(crate) fn open(path: &Path) -> Result<Self> {
        ensure!(
            path.is_absolute(),
            "The lifecycle directory must be absolute"
        );
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            // Directory handles require backup semantics; inspect the reparse
            // point itself instead of following a replaced directory link.
            options.custom_flags(0x0200_0000 | 0x0020_0000);
        }
        let directory = options
            .open(path)
            .context("Open private lifecycle directory")?;
        let identity = directory.metadata()?;
        ensure!(
            private_directory(&identity),
            "The lifecycle directory must be owned and private"
        );
        let result = Self {
            path: path.to_owned(),
            directory,
            identity,
        };
        result.ensure_current()?;
        Ok(result)
    }

    pub(crate) fn ensure_current(&self) -> Result<()> {
        let current = fs::symlink_metadata(&self.path)?;
        ensure!(
            private_directory(&current) && same_identity(&self.identity, &current),
            "The private lifecycle directory was replaced"
        );
        Ok(())
    }

    pub(crate) fn read_optional(&self, name: &str) -> Result<Option<String>> {
        match self.read(name) {
            Ok(value) => Ok(Some(value)),
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound) =>
            {
                Ok(None)
            }
            Err(error) => Err(error),
        }
    }

    pub(crate) fn read(&self, name: &str) -> Result<String> {
        ensure!(
            !name.is_empty()
                && name
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-'),
            "Invalid lifecycle marker name"
        );
        self.ensure_current()?;
        let mut file = self.open_record(name)?;
        let before = file.metadata()?;
        ensure!(
            private_regular(&before) && before.len() <= MAX_MARKER_BYTES as u64,
            "Unsafe lifecycle marker"
        );
        let mut bytes = Vec::new();
        file.by_ref()
            .take((MAX_MARKER_BYTES + 1) as u64)
            .read_to_end(&mut bytes)?;
        let after = file.metadata()?;
        ensure!(
            bytes.len() <= MAX_MARKER_BYTES
                && bytes.len() as u64 == before.len()
                && private_regular(&after)
                && stable_file(&before, &after),
            "The lifecycle marker changed while reading"
        );
        let named = self.open_record(name)?.metadata()?;
        ensure!(
            private_regular(&named) && stable_file(&after, &named),
            "The lifecycle marker was replaced while reading"
        );
        self.ensure_current()?;
        Ok(String::from_utf8(bytes)?)
    }

    #[cfg(unix)]
    fn publish_private_marker(
        &self,
        target_name: &str,
        pending_name: &str,
        content: &str,
        require_absent: bool,
        validate: impl Fn() -> Result<()>,
    ) -> Result<()> {
        use std::{
            ffi::CString,
            io::Write,
            os::fd::{AsRawFd, FromRawFd},
        };
        ensure!(
            content.len() <= MAX_MARKER_BYTES,
            "The private marker is too large"
        );
        self.ensure_current()?;
        validate()?;
        if require_absent {
            ensure!(
                self.read_optional(target_name)?.is_none(),
                "The portrait publication target already exists"
            );
        }
        let pending = CString::new(pending_name)?;
        let target = CString::new(target_name)?;
        // SAFETY: the pinned directory fd and both terminated leaf names remain
        // live. O_EXCL does not follow or truncate an existing pending marker.
        let descriptor = unsafe {
            libc::openat(
                self.directory.as_raw_fd(),
                pending.as_ptr(),
                libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                0o600,
            )
        };
        if descriptor < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        // SAFETY: this new descriptor is transferred into exactly one File owner.
        let mut file = unsafe { File::from_raw_fd(descriptor) };
        let result = (|| -> Result<()> {
            file.write_all(content.as_bytes())?;
            file.sync_all()?;
            let created = file.metadata()?;
            ensure!(
                private_regular(&created) && created.len() == content.len() as u64,
                "Unsafe pending private marker"
            );
            validate()?;
            self.ensure_current()?;
            let named = self.open_record(pending_name)?.metadata()?;
            ensure!(
                private_regular(&named) && stable_file(&created, &named),
                "The pending private marker changed before publication"
            );
            if require_absent {
                ensure!(
                    self.read_optional(target_name)?.is_none(),
                    "The portrait publication target appeared before commit"
                );
                #[cfg(target_os = "linux")]
                {
                    // SAFETY: both leaf names and the pinned directory fd remain
                    // valid. RENAME_NOREPLACE never follows or replaces a target
                    // created between the absence check and this atomic commit.
                    if unsafe {
                        libc::syscall(
                            libc::SYS_renameat2,
                            self.directory.as_raw_fd(),
                            pending.as_ptr(),
                            self.directory.as_raw_fd(),
                            target.as_ptr(),
                            libc::RENAME_NOREPLACE,
                        )
                    } == -1
                    {
                        return Err(std::io::Error::last_os_error().into());
                    }
                }
                #[cfg(not(target_os = "linux"))]
                bail!("Exclusive private marker publication requires Linux renameat2");
            } else {
                // SAFETY: both paths are relative to the same pinned directory.
                ensure!(
                    unsafe {
                        libc::renameat(
                            self.directory.as_raw_fd(),
                            pending.as_ptr(),
                            self.directory.as_raw_fd(),
                            target.as_ptr(),
                        )
                    } == 0,
                    "Publish the private output capability"
                );
            }
            let published = self.open_record(target_name)?.metadata()?;
            // A rename may change ctime, so establish a fresh stable-read baseline
            // after committing instead of comparing pre-rename timestamps.
            ensure!(
                private_regular(&published)
                    && same_identity(&created, &published)
                    && created.len() == published.len(),
                "The published private marker was replaced"
            );
            self.ensure_current()?;
            Ok(())
        })();
        // Exclusive portrait failures retain their pending evidence for the
        // owner after this bounded reader has stopped. A named unlink after an
        // inode check cannot atomically exclude a same-UID replacement race.
        // Preserve the existing capability writer's separate cleanup behavior.
        if result.is_err() && !require_absent {
            self.remove_owned_marker_pending(pending_name, &file);
        }
        result
    }

    #[cfg(unix)]
    fn remove_owned_marker_pending(&self, pending_name: &str, file: &File) {
        use std::{ffi::CString, os::fd::AsRawFd};
        let Ok(owned) = file.metadata() else {
            return;
        };
        let Ok(named) = self
            .open_record(pending_name)
            .and_then(|value| Ok(value.metadata()?))
        else {
            return;
        };
        if !private_regular(&owned) || !private_regular(&named) || !same_identity(&owned, &named) {
            return;
        }
        let Ok(pending) = CString::new(pending_name) else {
            return;
        };
        // SAFETY: only the still-matching pending marker in the pinned directory
        // is removed. An unowned pre-existing pending marker is never cleaned up.
        let _ = unsafe { libc::unlinkat(self.directory.as_raw_fd(), pending.as_ptr(), 0) };
    }

    #[cfg(unix)]
    fn open_record(&self, name: &str) -> Result<File> {
        use std::{
            ffi::CString,
            os::fd::{AsRawFd, FromRawFd},
        };
        let name = CString::new(name)?;
        // SAFETY: the directory fd and terminated leaf name remain valid for openat.
        // O_NONBLOCK prevents a FIFO from blocking before its type is checked.
        let descriptor = unsafe {
            libc::openat(
                self.directory.as_raw_fd(),
                name.as_ptr(),
                libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC,
            )
        };
        if descriptor < 0 {
            return Err(std::io::Error::last_os_error().into());
        }
        // SAFETY: this new descriptor is transferred into exactly one File owner.
        Ok(unsafe { File::from_raw_fd(descriptor) })
    }

    #[cfg(not(unix))]
    fn open_record(&self, name: &str) -> Result<File> {
        let path = self.path.join(name);
        let before = fs::symlink_metadata(&path)?;
        ensure!(
            before.is_file() && !before.file_type().is_symlink(),
            "Unsafe lifecycle marker"
        );
        Ok(File::open(path)?)
    }
}

fn private_directory(metadata: &Metadata) -> bool {
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        // SAFETY: geteuid has no pointer arguments and only reads process identity.
        metadata.uid() == unsafe { libc::geteuid() } && metadata.mode() & 0o077 == 0
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn private_regular(metadata: &Metadata) -> bool {
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        // SAFETY: geteuid has no pointer arguments and only reads process identity.
        metadata.uid() == unsafe { libc::geteuid() }
            && metadata.mode() & 0o077 == 0
            && metadata.nlink() == 1
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn same_identity(left: &Metadata, right: &Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        left.dev() == right.dev() && left.ino() == right.ino()
    }
    #[cfg(not(unix))]
    {
        left.created().ok() == right.created().ok()
    }
}

fn stable_file(left: &Metadata, right: &Metadata) -> bool {
    if !same_identity(left, right) || left.len() != right.len() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        left.mtime() == right.mtime()
            && left.mtime_nsec() == right.mtime_nsec()
            && left.ctime() == right.ctime()
            && left.ctime_nsec() == right.ctime_nsec()
    }
    #[cfg(not(unix))]
    {
        left.modified().ok() == right.modified().ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{PermissionsExt, symlink};

    #[test]
    fn scanout_uses_only_this_runs_relative_half_turns() {
        for baseline in 0..4 {
            assert_eq!(
                NativeDirection::Up
                    .resolve(baseline, baseline, false)
                    .unwrap(),
                NativePortraitOrientation::Up
            );
            assert_eq!(
                NativeDirection::Down
                    .resolve(baseline, baseline, false)
                    .unwrap(),
                NativePortraitOrientation::Down
            );
            assert_eq!(
                NativeDirection::Up
                    .resolve(baseline, (baseline + 2) % 4, true)
                    .unwrap(),
                NativePortraitOrientation::Down
            );
            assert_eq!(
                NativeDirection::Down
                    .resolve(baseline, (baseline + 2) % 4, true)
                    .unwrap(),
                NativePortraitOrientation::Up
            );
            for delta in [1, 3] {
                assert!(
                    NativeDirection::Up
                        .resolve(baseline, (baseline + delta) % 4, true)
                        .is_err()
                );
            }
            assert!(
                NativeDirection::Up
                    .resolve(baseline, (baseline + 2) % 4, false)
                    .is_err()
            );
            assert!(
                NativeDirection::Fixed
                    .resolve(baseline, (baseline + 2) % 4, true)
                    .is_err()
            );
        }
        assert!(NativeDirection::Up.resolve(4, 0, true).is_err());
        assert!(NativeDirection::Down.resolve(0, 4, true).is_err());
    }

    #[test]
    fn recognized_flat_startup_uses_the_fresh_calibrated_scanout() {
        for raw_rotation in [1, 3] {
            for direction in [NativeDirection::Up, NativeDirection::Down] {
                let scanout = direction
                    .resolve(raw_rotation, raw_rotation, false)
                    .unwrap();
                let (holding, from_sensor) = resolve_current_holding(
                    scanout,
                    Some(NativePortraitSensorObservation { pose: None }),
                )
                .unwrap();
                assert_eq!(holding, scanout);
                assert!(!from_sensor);
            }
        }
    }

    #[test]
    fn recognized_flat_resume_uses_current_scanout_instead_of_the_old_anchor_pose() {
        for baseline in [1, 3] {
            for direction in [NativeDirection::Up, NativeDirection::Down] {
                let scanout = direction
                    .resolve(baseline, (baseline + 2) % 4, true)
                    .unwrap();
                let (holding, from_sensor) = resolve_current_holding(
                    scanout,
                    Some(NativePortraitSensorObservation { pose: None }),
                )
                .unwrap();
                assert_ne!(scanout, direction.backend());
                assert_eq!(holding, scanout);
                assert_ne!(holding, direction.backend());
                assert!(!from_sensor);
            }
        }
    }

    #[test]
    fn a_valid_holding_sample_remains_independent_and_a_missing_sensor_is_rejected() {
        for (scanout, pose) in [
            (
                NativePortraitOrientation::Up,
                NativePortraitOrientation::Down,
            ),
            (
                NativePortraitOrientation::Down,
                NativePortraitOrientation::Up,
            ),
        ] {
            assert_eq!(
                resolve_current_holding(
                    scanout,
                    Some(NativePortraitSensorObservation { pose: Some(pose) }),
                )
                .unwrap(),
                (pose, true)
            );
            assert!(resolve_current_holding(scanout, None).is_err());
        }
    }

    #[test]
    fn native_records_require_exact_single_line_framing() {
        assert_eq!(
            parse_json_record::<serde_json::Value>("{\"value\":1}\n").unwrap()["value"],
            1
        );
        for malformed in [
            "{\"value\":1}",
            "{\"value\":1}\r\n",
            "{\"value\":1}\n\n",
            "{\n\"value\":1}\n",
        ] {
            assert!(parse_json_record::<serde_json::Value>(malformed).is_err());
        }
        let overlong = format!("{{\"value\":\"{}\"}}\n", "x".repeat(MAX_MARKER_BYTES));
        assert!(parse_json_record::<serde_json::Value>(&overlong).is_err());
    }

    #[test]
    fn a_resume_record_cannot_smuggle_a_cached_pose_or_native_mode() {
        let raw = "{\"version\":1,\"generation\":1,\"nonce\":\"0123456789abcdef0123456789abcdef\",\"launcher\":{\"pid\":2,\"parent\":1,\"group\":2,\"session\":2,\"birth\":1,\"executable\":{\"path\":\"/bin/sh\",\"device\":1,\"inode\":1}},\"application\":{\"pid\":4,\"parent\":3,\"group\":2,\"session\":2,\"birth\":3,\"executable\":{\"path\":\"/app/aidoku\",\"device\":1,\"inode\":2}},\"runner\":{\"pid\":3,\"parent\":2,\"group\":2,\"session\":2,\"birth\":2,\"executable\":{\"path\":\"/bin/sh\",\"device\":1,\"inode\":1}},\"epoch\":1,\"presentation\":\"CALIBRATED_FRAMEBUFFER\"}\n";
        let accepted = parse_json_record::<ResumePermission>(raw).unwrap();
        assert_eq!(accepted.epoch, 1);
        let mut unknown = serde_json::to_value(&accepted).unwrap();
        unknown["holding"] = serde_json::json!("D");
        assert!(serde_json::from_value::<ResumePermission>(unknown).is_err());
        let cached = raw.replace("CALIBRATED_FRAMEBUFFER", "Y8");
        assert!(parse_json_record::<ResumePermission>(&cached).is_err());
    }

    #[test]
    fn private_readers_reject_links_fifos_and_overlong_records() {
        let directory = tempfile::tempdir().unwrap();
        fs::set_permissions(directory.path(), fs::Permissions::from_mode(0o700)).unwrap();
        let records = PrivateDirectory::open(directory.path()).unwrap();
        let target = directory.path().join("target");
        fs::write(&target, "owned\n").unwrap();
        fs::set_permissions(&target, fs::Permissions::from_mode(0o600)).unwrap();
        assert_eq!(records.read("target").unwrap(), "owned\n");
        symlink(&target, directory.path().join("linked")).unwrap();
        assert!(records.read("linked").is_err());
        fs::hard_link(&target, directory.path().join("hardlinked")).unwrap();
        assert!(records.read("target").is_err());
        let fifo =
            std::ffi::CString::new(directory.path().join("fifo").as_os_str().as_encoded_bytes())
                .unwrap();
        // SAFETY: the fixture owns this new terminated path and creates only a
        // private FIFO, whose nonblocking metadata read must reject its type.
        assert_eq!(unsafe { libc::mkfifo(fifo.as_ptr(), 0o600) }, 0);
        assert!(records.read("fifo").is_err());
        let large = directory.path().join("large");
        fs::write(&large, vec![b'x'; MAX_MARKER_BYTES + 1]).unwrap();
        fs::set_permissions(&large, fs::Permissions::from_mode(0o600)).unwrap();
        assert!(records.read("large").is_err());
    }

    #[test]
    fn a_replaced_private_directory_cannot_redirect_the_pinned_reader() {
        let outer = tempfile::tempdir().unwrap();
        let lock = outer.path().join("lock");
        fs::create_dir(&lock).unwrap();
        fs::set_permissions(&lock, fs::Permissions::from_mode(0o700)).unwrap();
        let records = PrivateDirectory::open(&lock).unwrap();
        fs::rename(&lock, outer.path().join("old-lock")).unwrap();
        fs::create_dir(&lock).unwrap();
        fs::set_permissions(&lock, fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(lock.join("marker"), "another owner\n").unwrap();
        fs::set_permissions(lock.join("marker"), fs::Permissions::from_mode(0o600)).unwrap();
        assert!(records.read("marker").is_err());
    }
}
