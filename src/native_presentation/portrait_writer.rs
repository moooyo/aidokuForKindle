//! Typed portrait records published by the permitted native-reader descendant.

use std::{
    io,
    os::fd::AsRawFd,
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};

use super::*;

const TRANSACTION_TIMEOUT: Duration = Duration::from_millis(1700);
const MAX_CHAIN_MEMBERS: usize = 12;
const MAX_NATIVE_VALUE_BYTES: usize = 128;

#[derive(Eq, PartialEq)]
struct LeaseIdentity {
    sequence: u64,
    launcher: ProcessIdentity,
    reader: ProcessIdentity,
    chain: Vec<ProcessIdentity>,
    sources: Vec<(&'static str, Option<String>)>,
}

struct Writer {
    records: PrivateDirectory,
    generation: u64,
    nonce: String,
}

impl Writer {
    fn from_environment() -> Result<Self> {
        let directory = std::env::var_os("AIDOKU_LAUNCHER_DIR")
            .context("Missing portrait publication directory")?;
        let nonce =
            std::env::var("AIDOKU_LAUNCH_NONCE").context("Missing portrait publication nonce")?;
        ensure!(valid_nonce(&nonce), "Invalid portrait publication nonce");
        let generation_text = std::env::var("AIDOKU_LAUNCH_GENERATION")
            .context("Missing portrait publication generation")?;
        let generation = generation_text.parse::<u64>()?;
        ensure!(
            generation > 0 && generation_text == generation.to_string(),
            "Invalid portrait publication generation"
        );
        Ok(Self {
            records: PrivateDirectory::open(Path::new(&directory))?,
            generation,
            nonce,
        })
    }

    fn lease(&self) -> Result<LeaseIdentity> {
        self.records.ensure_current()?;
        let generation = self.records.read("generation")?;
        let nonce = self.records.read("nonce")?;
        let launcher_raw = self.records.read("pid")?;
        let permission = self.records.read("native-read-go")?;
        ensure!(
            generation == format!("{}:{}\n", self.generation, self.nonce)
                && nonce == format!("{}\n", self.nonce),
            "Portrait publication belongs to another launch"
        );
        let launcher = process_identity(parse_pid_record(&launcher_raw)?)?;
        let line = permission
            .strip_suffix('\n')
            .context("Invalid native-reader permission framing")?;
        let values = line.split(':').collect::<Vec<_>>();
        ensure!(
            values.len() == 3 && values[2] == self.nonce,
            "Native-reader permission belongs to another launch"
        );
        let reader_pid = values[0].parse::<u32>()?;
        let sequence = values[1].parse::<u64>()?;
        ensure!(
            sequence > 0 && line == format!("{reader_pid}:{sequence}:{}", self.nonce),
            "Invalid native-reader permission"
        );
        let reader = process_identity(reader_pid)?;
        ensure!(
            reader.parent == launcher.pid
                && reader.group == reader.pid
                && reader.session == reader.pid,
            "The native reader is not privately owned by this launcher"
        );
        let mut chain = Vec::new();
        let mut member = std::process::id();
        let mut reached_reader = false;
        for _ in 0..MAX_CHAIN_MEMBERS {
            let current = process_identity(member)?;
            ensure!(
                current.group == reader.pid && current.session == reader.pid,
                "The portrait publisher escaped its bounded native-reader group"
            );
            ensure!(
                !chain
                    .iter()
                    .any(|item: &ProcessIdentity| item.pid == current.pid),
                "Cyclic portrait publisher ancestry"
            );
            if current.pid == reader.pid {
                ensure!(current == reader, "The native-reader identity changed");
                reached_reader = true;
                break;
            }
            member = current.parent;
            chain.push(current);
        }
        ensure!(
            reached_reader && !chain.is_empty(),
            "The portrait publisher is not a native-reader descendant"
        );
        let mut sources = vec![
            ("generation", Some(generation)),
            ("nonce", Some(nonce)),
            ("pid", Some(launcher_raw)),
            ("native-read-go", Some(permission)),
        ];
        for name in [
            "application-pid",
            "epoch",
            "sleep-request",
            "resume-request",
            "phase",
        ] {
            sources.push((name, self.records.read_optional(name)?));
        }
        Ok(LeaseIdentity {
            sequence,
            launcher,
            reader,
            chain,
            sources,
        })
    }

    fn publish<T: Serialize>(
        &self,
        target: &str,
        pending: &str,
        value: &T,
        lease: &LeaseIdentity,
    ) -> Result<()> {
        let mut content = serde_json::to_string(value)?;
        content.push('\n');
        self.records
            .publish_private_marker(target, pending, &content, true, || {
                ensure!(
                    self.lease()? == *lease,
                    "Portrait publication ownership changed before commit"
                );
                Ok(())
            })?;
        ensure!(
            self.records.read(target)? == content,
            "The published portrait record changed"
        );
        ensure!(
            self.lease()? == *lease,
            "Portrait publication ownership changed after commit"
        );
        self.records.ensure_current()?;
        Ok(())
    }
}

pub(super) fn capture_startup(profile: DeviceProfile, cancelled: impl Fn() -> bool) -> Result<()> {
    let writer = Writer::from_environment()?;
    let lease = writer.lease()?;
    ensure!(
        writer.records.read_optional("application-pid")?.is_none()
            && writer.records.read_optional("sleep-request")?.is_none(),
        "Startup portrait publication follows application initialization"
    );
    let deadline = Instant::now() + TRANSACTION_TIMEOUT;
    ensure_active(deadline, &cancelled)?;
    let captured_mode = native_mode(&bounded_value(
        "lipc-get-prop",
        &["-s", "com.lab126.winmgr", "epdcMode"],
        deadline,
        &cancelled,
    )?)?;
    let before = slint_backend_kindle::probe_native_framebuffer()?;
    // Capability remains present while the recognized sensor reports a flat or
    // otherwise non-pose sample. Its current pose does not identify scanout.
    let automatic = slint_backend_kindle::observe_native_portrait_sensor()?.is_some();
    let direction = if automatic {
        native_direction(
            &bounded_value(
                "lipc-get-prop",
                &["com.lab126.winmgr", "accelerometer"],
                deadline,
                &cancelled,
            )?,
            profile,
        )?
    } else {
        NativeDirection::Fixed
    };
    let after = slint_backend_kindle::probe_native_framebuffer()?;
    ensure!(
        before == after,
        "The native framebuffer changed during startup calibration"
    );
    if automatic {
        let repeated = native_direction(
            &bounded_value(
                "lipc-get-prop",
                &["com.lab126.winmgr", "accelerometer"],
                deadline,
                &cancelled,
            )?,
            profile,
        )?;
        ensure!(
            direction == repeated,
            "The native WM portrait direction changed during calibration"
        );
    }
    let repeated_mode = native_mode(&bounded_value(
        "lipc-get-prop",
        &["-s", "com.lab126.winmgr", "epdcMode"],
        deadline,
        &cancelled,
    )?)?;
    ensure!(
        captured_mode == repeated_mode,
        "The native display mode changed during calibration"
    );
    ensure_active(deadline, &cancelled)?;
    ensure!(
        !cancelled() && Instant::now() < deadline,
        "The native portrait transaction expired"
    );
    ensure!(
        writer.lease()? == lease,
        "Startup portrait ownership changed during native observation"
    );
    let calibration = StartupCalibration {
        version: 1,
        generation: writer.generation,
        nonce: writer.nonce.clone(),
        launcher: lease.launcher.clone(),
        publisher: lease.chain[0].executable.clone(),
        profile: profile.id().to_owned(),
        native_mode: captured_mode,
        native_direction: direction,
        raw_rotation: after.rotation,
        layout: after.layout_token(),
    };
    ensure!(
        valid_layout_token(&calibration.layout),
        "The framebuffer layout token is malformed"
    );
    writer.publish(STARTUP_RECORD, STARTUP_PENDING, &calibration, &lease)
}

pub(super) fn authorize_resume(epoch: u64, cancelled: impl Fn() -> bool) -> Result<()> {
    ensure!(epoch > 0, "A native portrait resume requires a sleep epoch");
    let writer = Writer::from_environment()?;
    let lease = writer.lease()?;
    let startup_raw = writer.records.read(STARTUP_RECORD)?;
    let startup: StartupCalibration = parse_json_record(&startup_raw)?;
    ensure!(
        startup.version == 1
            && startup.generation == writer.generation
            && startup.nonce == writer.nonce
            && startup.launcher == lease.launcher
            && startup.publisher == lease.chain[0].executable,
        "The resume permission does not belong to the calibrated native run"
    );
    let application =
        process_identity(parse_pid_record(&writer.records.read("application-pid")?)?)?;
    let runner = process_identity(application.parent)?;
    ensure!(
        runner.parent == lease.launcher.pid
            && runner.pid != lease.reader.pid
            && application.executable == startup.publisher,
        "The resumed application is not owned by this portrait launcher"
    );
    ensure!(
        writer.records.read("epoch")? == format!("{epoch}\n")
            && writer.records.read("sleep-request")?
                == format!("{} {} {epoch}\n", application.pid, writer.nonce),
        "The portrait resume permission belongs to another sleep epoch"
    );
    ensure_active(Instant::now() + TRANSACTION_TIMEOUT, &cancelled)?;
    let permission = ResumePermission {
        version: 1,
        generation: writer.generation,
        nonce: writer.nonce.clone(),
        launcher: lease.launcher.clone(),
        application,
        runner,
        epoch,
        presentation: ResumePresentation::CalibratedFramebuffer,
    };
    if let Some(existing) = writer.records.read_optional(RESUME_RECORD)? {
        // A watchdog may retire the old reader after atomic publication but
        // before its acknowledgement. Recheck the same epoch and identities;
        // never replace, truncate or clean a record from this retry.
        ensure!(
            parse_json_record::<ResumePermission>(&existing)? == permission,
            "The existing portrait resume permission belongs to another transaction"
        );
        ensure!(
            writer.lease()? == lease,
            "Portrait resume retry ownership changed"
        );
        ensure!(
            writer.records.read(RESUME_RECORD)? == existing,
            "The portrait permission changed during retry"
        );
    } else {
        // Failed exclusive pending evidence remains owned by its reader lease.
        // A later permitted reader uses a fresh leaf without deleting evidence.
        let pending = format!("{RESUME_RECORD}-{}.pending", lease.sequence);
        writer.publish(RESUME_RECORD, &pending, &permission, &lease)?;
    }
    ensure!(
        writer.records.read(STARTUP_RECORD)? == startup_raw,
        "The startup portrait anchor changed while authorizing resume"
    );
    ensure!(
        writer.lease()? == lease,
        "Portrait resume authorization ownership changed"
    );
    Ok(())
}

fn parse_pid_record(raw: &str) -> Result<u32> {
    let value = raw
        .strip_suffix('\n')
        .context("Invalid native process marker framing")?;
    let pid = value.parse::<u32>()?;
    ensure!(
        pid > 1 && value == pid.to_string(),
        "Invalid native process identifier"
    );
    Ok(pid)
}

fn native_mode(value: &str) -> Result<NativeMode> {
    match value {
        "Y8" => Ok(NativeMode::Y8),
        "Y8INV" => Ok(NativeMode::Y8Inverted),
        _ => bail!("The native display mode is unknown"),
    }
}

fn native_direction(value: &str, profile: DeviceProfile) -> Result<NativeDirection> {
    match value {
        "U" => Ok(NativeDirection::Up),
        "D" => Ok(NativeDirection::Down),
        "L" if profile == DeviceProfile::Scribe => Ok(NativeDirection::Up),
        "R" if profile == DeviceProfile::Scribe => Ok(NativeDirection::Down),
        _ => bail!("The native WM portrait direction is unknown"),
    }
}

fn ensure_active(deadline: Instant, cancelled: &impl Fn() -> bool) -> Result<()> {
    ensure!(
        bounded_value(
            "lipc-get-prop",
            &["-s", "com.lab126.powerd", "state"],
            deadline,
            cancelled
        )? == "active",
        "Native screen-saver dismissal or unlock is incomplete"
    );
    Ok(())
}

struct QueryChild(Child);

impl Drop for QueryChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn bounded_value(
    program: &str,
    arguments: &[&str],
    deadline: Instant,
    cancelled: &impl Fn() -> bool,
) -> Result<String> {
    ensure!(
        !cancelled() && Instant::now() < deadline,
        "The native portrait transaction expired"
    );
    // Every child stays in the launcher's separately proven reader group. Its
    // outer watchdog kills the entire group, including inherited pipe writers.
    let mut child = QueryChild(
        Command::new(program)
            .args(arguments)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?,
    );
    let mut stdout = child
        .0
        .stdout
        .take()
        .context("The native query has no stdout pipe")?;
    let fd = stdout.as_raw_fd();
    // SAFETY: fd is the owned pipe; both fcntl requests use only integer flags.
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Err(io::Error::last_os_error().into());
    }
    let mut bytes = [0u8; MAX_NATIVE_VALUE_BYTES + 1];
    let mut length = 0;
    let mut eof = false;
    loop {
        ensure!(
            !cancelled() && Instant::now() < deadline,
            "The native query exceeded its portrait transaction deadline"
        );
        if !eof {
            match stdout.read(&mut bytes[length..]) {
                Ok(0) => eof = true,
                Ok(count) => {
                    length += count;
                    ensure!(
                        length <= MAX_NATIVE_VALUE_BYTES,
                        "The native query returned an overlong value"
                    );
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {}
                Err(error) => return Err(error.into()),
            }
        }
        if let Some(status) = child.0.try_wait()? {
            ensure!(status.success(), "The native query failed with {status}");
            if eof {
                return Ok(std::str::from_utf8(&bytes[..length])?
                    .trim_ascii()
                    .to_owned());
            }
        }
        std::thread::sleep(
            deadline
                .saturating_duration_since(Instant::now())
                .min(Duration::from_millis(5)),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_reviewed_scribe_profile_normalizes_landscape_native_tokens() {
        assert_eq!(
            native_direction("L", DeviceProfile::Scribe).unwrap(),
            NativeDirection::Up
        );
        assert_eq!(
            native_direction("R", DeviceProfile::Scribe).unwrap(),
            NativeDirection::Down
        );
        for profile in [
            DeviceProfile::Pw5,
            DeviceProfile::Pw6,
            DeviceProfile::Scribe2,
            DeviceProfile::Scribe3,
        ] {
            assert!(native_direction("L", profile).is_err());
            assert!(native_direction("R", profile).is_err());
            assert_eq!(native_direction("U", profile).unwrap(), NativeDirection::Up);
        }
        for value in ["", "UD", "u", "accelerometer U", "U D"] {
            assert!(native_direction(value, DeviceProfile::Scribe).is_err());
        }
    }
}
