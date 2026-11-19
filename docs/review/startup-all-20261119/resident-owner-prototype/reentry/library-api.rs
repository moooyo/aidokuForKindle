//! A second catalog presentation is a new transaction, never a screen-saver resume.
//! No production entry calls this module without a separately qualified native visit.
use super::*;
use crate::native_library_visit::{self, NativeLibraryVisit};
use std::os::unix::net::UnixStream;
use std::sync::Arc;

const MAX_VISITS: u64 = 8;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct VisitIdentity {
    peer: ProcessIdentity,
    activation: (u32, u64),
    handler: (u32, u64),
}

pub struct Entry {
    stream: UnixStream,
    verified: NativeLibraryVisit,
}

impl Entry {
    pub fn from_waiter(stream: UnixStream) -> Result<Self> {
        let verified =
            native_library_visit::observe(&stream).context("Qualify this native Library visit")?;
        let entry = Self { stream, verified };
        entry.identity()?;
        Ok(entry)
    }

    fn identity(&self) -> Result<VisitIdentity> {
        self.verified.revalidate(&self.stream)?;
        let mut credentials = libc::ucred {
            pid: 0,
            uid: u32::MAX,
            gid: u32::MAX,
        };
        let mut size = std::mem::size_of_val(&credentials) as libc::socklen_t;
        // SAFETY: the kernel receives a borrowed stream and an initialized, exactly sized ucred.
        let status = unsafe {
            libc::getsockopt(
                self.stream.as_raw_fd(),
                libc::SOL_SOCKET,
                libc::SO_PEERCRED,
                (&mut credentials as *mut libc::ucred).cast(),
                &mut size,
            )
        };
        ensure!(
            status == 0
                && size as usize == std::mem::size_of_val(&credentials)
                && credentials.pid > 1
                && credentials.uid == unsafe { libc::geteuid() },
            "Invalid Library waiter credential"
        );
        let mut fd = libc::pollfd {
            fd: self.stream.as_raw_fd(),
            events: libc::POLLIN | libc::POLLRDHUP,
            revents: 0,
        };
        // The channel is silent while the visit is in progress. EOF and unexpected bytes retire it.
        let ready = unsafe { libc::poll(&mut fd, 1, 0) };
        ensure!(
            ready >= 0 && fd.revents == 0,
            "The Library waiter left or cancelled this visit"
        );
        let peer = process_identity(credentials.pid as u32)?;
        ensure!(
            (peer.pid, peer.birth) == (self.verified.waiter.pid, self.verified.waiter.birth),
            "The authenticated Library waiter changed"
        );
        Ok(VisitIdentity {
            peer,
            activation: (self.verified.su.pid, self.verified.su.birth),
            handler: (self.verified.handler.pid, self.verified.handler.birth),
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Returned {
    version: u8,
    round: u64,
    release_id: String,
    previous: StartupCalibration,
    application: ProcessIdentity,
    runner: ProcessIdentity,
    waiter: VisitIdentity,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Admission {
    version: u8,
    round: u64,
    nonce: String,
    previous_release: String,
    previous: StartupCalibration,
    waiter: VisitIdentity,
    application: ProcessIdentity,
    runner: ProcessIdentity,
    calibration: StartupCalibration,
}

fn text<T: Serialize>(value: &T) -> Result<String> {
    Ok(format!("{}\n", serde_json::to_string(value)?))
}
fn leaf(prefix: &str, round: u64) -> String {
    format!("library-{prefix}-{round}")
}
fn write_once<T: Serialize>(
    records: &PrivateDirectory,
    target: &str,
    value: &T,
    check: impl Fn() -> Result<()>,
) -> Result<()> {
    records.publish_private_marker(
        target,
        &format!("{target}.pending"),
        &text(value)?,
        true,
        check,
    )
}
fn read<T: for<'a> Deserialize<'a>>(records: &PrivateDirectory, target: &str) -> Result<T> {
    parse_json_record(&records.read(target)?)
}
fn random_id() -> Result<String> {
    let mut random = [0u8; 16];
    File::open("/dev/urandom")?.read_exact(&mut random)?;
    use std::fmt::Write as _;
    let mut hex = String::new();
    for value in random {
        write!(hex, "{value:02x}")?;
    }
    Ok(hex)
}
fn anchor(records: &PrivateDirectory, round: u64) -> Result<StartupCalibration> {
    if round == 0 {
        return read(records, STARTUP_RECORD);
    }
    let permit: Admission = read(records, &leaf("grant", round))?;
    ensure!(
        permit.version == 1 && permit.round == round && valid_nonce(&permit.nonce),
        "Invalid earlier Library presentation"
    );
    Ok(permit.calibration)
}
fn active_family(
    records: &PrivateDirectory,
    startup: &StartupCalibration,
) -> Result<(ProcessIdentity, ProcessIdentity)> {
    ensure!(
        records.read("pid")? == format!("{}\n", startup.launcher.pid)
            && records.read("generation")? == format!("{}:{}\n", startup.generation, startup.nonce)
            && records.read("nonce")? == format!("{}\n", startup.nonce)
            && process_identity(startup.launcher.pid)? == startup.launcher,
        "The original owner is no longer the same process"
    );
    let application = process_identity(parse_pid_record(&records.read("application-pid")?)?)?;
    let runner = process_identity(application.parent)?;
    ensure!(
        application.executable == startup.publisher
            && runner.pid != startup.launcher.pid
            && runner.parent == startup.launcher.pid,
        "The resident GUI is no longer owned by its original runner"
    );
    for name in [
        "ordinary-exit",
        "sleep-request",
        "resume-request",
        "fallback-sleep",
    ] {
        ensure!(
            records.read_optional(name)?.is_none(),
            "An ordinary exit or power transition revoked the Library handoff"
        );
    }
    Ok((application, runner))
}
fn enough_memory() -> Result<()> {
    let raw = fs::read_to_string("/proc/meminfo")?;
    let field = |key| {
        raw.lines()
            .find(|line| line.starts_with(key))
            .and_then(|line| {
                let mut parts = line.split_ascii_whitespace();
                let (Some(_), Some(kib), Some("kB"), None) =
                    (parts.next(), parts.next(), parts.next(), parts.next())
                else {
                    return None;
                };
                kib.parse::<u64>().ok()
            })
    };
    ensure!(
        field("MemAvailable:").is_some_and(|kib| kib >= 48 * 1024),
        "Insufficient memory for retained Library state"
    );
    Ok(())
}
fn boottime() -> Result<u64> {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    ensure!(
        unsafe { libc::clock_gettime(libc::CLOCK_BOOTTIME, &mut ts) } == 0
            && ts.tv_sec >= 0
            && (0..1_000_000_000).contains(&ts.tv_nsec),
        "No monotonic suspend-aware clock"
    );
    (ts.tv_sec as u64)
        .checked_mul(1000)
        .and_then(|v| v.checked_add((ts.tv_nsec as u64) / 1_000_000))
        .context("Invalid suspend-aware clock")
}

/// The backend supplies this handoff only after discarding its presentation stack.
/// The application must not announce Home from an ordinary screen-saver pause.
pub struct Closed {
    returned: Returned,
    fence: slint_backend_kindle::LibraryIoClosed,
}

/// The prepared driver supplies the current qualified waiter and its consumed native-IO fence.
/// The separate owner acknowledges native Home restoration and catalog settlement before a new visit.
pub enum Showing {
    Initial {
        context: NativePresentationContext,
        entry: Entry,
    },
    Reopened(LibraryPresentation),
}

pub fn record_closed(
    showing: Showing,
    fence: slint_backend_kindle::LibraryIoClosed,
) -> Result<Closed> {
    let displayed_round = fence.round();
    ensure!(
        displayed_round < MAX_VISITS,
        "The resident session exhausted its visit limit"
    );
    let (records, current, expected) = match &showing {
        Showing::Initial { context, entry } => {
            ensure!(
                displayed_round == 0 && context.library_handoffs,
                "Not a managed initial Library presentation"
            );
            context.ensure_startup_current()?;
            context.binding(true)?;
            (&context.records, entry, &context.startup)
        }
        Showing::Reopened(active) => {
            ensure!(
                displayed_round == active.grant.round,
                "The backend returned a different presentation"
            );
            active.current_in("library-closing\n")?;
            (&active.records, &active.current, &active.grant.calibration)
        }
    };
    let prior = anchor(records, displayed_round)?;
    ensure!(
        &prior == expected,
        "Previous Library anchor belongs to a different session"
    );
    let (application, runner) = active_family(records, &prior)?;
    ensure!(
        application.pid == std::process::id(),
        "Only the retained GUI can report closure"
    );
    let waiter = current.identity()?;
    let returned = Returned {
        version: 1,
        round: displayed_round,
        release_id: random_id()?,
        previous: prior,
        application,
        runner,
        waiter,
    };
    write_once(records, &leaf("closed", displayed_round), &returned, || {
        ensure!(
            active_family(records, &returned.previous)?
                == (returned.application.clone(), returned.runner.clone())
                && current.identity()? == returned.waiter
                && records.read("phase")? == "library-closing\n",
            "The current Library closure changed before publication"
        );
        Ok(())
    })?;
    Ok(Closed { returned, fence })
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Home {
    returned: Returned,
    since_ms: u64,
}

/// Called in the stable launcher after it has restored stock state and settled this waiter's notification.
pub fn confirm_home(round: u64, current: &Entry) -> Result<()> {
    ensure!(round < MAX_VISITS, "Invalid Home round");
    let writer = Writer::from_environment()?;
    let lease = writer.lease()?;
    let records = &writer.records;
    let returned: Returned = read(&records, &leaf("closed", round))?;
    ensure!(
        returned.round == round
            && returned.previous.launcher == lease.launcher
            && returned.previous.publisher == lease.chain[0].executable,
        "Wrong Home restoration owner"
    );
    let home = Home {
        returned,
        since_ms: boottime()?,
    };
    write_once(&records, &leaf("home", round), &home, || {
        ensure!(
            writer.lease()? == lease
                && records.read("phase")? == "library-restored\n"
                && current.identity()? == home.returned.waiter
                && read::<Returned>(&records, &leaf("closed", round))? == home.returned
                && active_family(&records, &home.returned.previous)?
                    == (
                        home.returned.application.clone(),
                        home.returned.runner.clone()
                    ),
            "The native Home restoration changed"
        );
        Ok(())
    })
}

fn home_before(
    records: &PrivateDirectory,
    round: u64,
    entry: &Entry,
) -> Result<(Home, VisitIdentity)> {
    ensure!(
        (1..=MAX_VISITS).contains(&round),
        "Invalid new Library round"
    );
    let home: Home = read(records, &leaf("home", round - 1))?;
    let returned = &home.returned;
    let first: StartupCalibration = read(records, STARTUP_RECORD)?;
    let waiting = entry.identity()?;
    ensure!(
        returned.version == 1
            && returned.round == round - 1
            && valid_nonce(&returned.release_id)
            && returned.previous == anchor(records, round - 1)?
            && returned.previous.launcher == first.launcher
            && returned.previous.publisher == first.publisher
            && returned.previous.generation == first.generation
            && returned.previous.nonce == first.nonce
            && returned.previous.profile == first.profile
            && returned.waiter != waiting
            && read::<Returned>(records, &leaf("closed", round - 1))? == *returned,
        "A different Library visit is required"
    );
    ensure!(
        active_family(records, &first)? == (returned.application.clone(), returned.runner.clone()),
        "The resident process family changed"
    );
    for number in 0..round {
        let earlier: Returned = read(records, &leaf("closed", number))?;
        ensure!(
            earlier.round == number && earlier.waiter.activation != waiting.activation,
            "This native Library activation has already been consumed"
        );
    }
    let elapsed = boottime()?
        .checked_sub(home.since_ms)
        .context("Home clock moved backwards")?;
    ensure!(elapsed < 45_000, "The Library return expired");
    enough_memory()?;
    Ok((home, waiting))
}

/// A new bounded reader performs the *complete* normal startup observation without removing application-pid.
pub fn capture(
    profile: DeviceProfile,
    round: u64,
    entry: &Entry,
    cancelled: impl Fn() -> bool,
) -> Result<()> {
    let writer = Writer::from_environment()?;
    let lease = writer.lease()?;
    let (home, waiting) = home_before(&writer.records, round, entry)?;
    let prior = &home.returned;
    ensure!(
        writer.records.read("phase")? == "library-preparing\n"
            && lease.launcher == prior.previous.launcher
            && lease.chain[0].executable == prior.previous.publisher
            && profile.id() == prior.previous.profile,
        "The Library writer is not bound to the returned presentation"
    );
    let (calibration, _) = observe_stock(&writer, &lease, profile, false, &cancelled)?;
    let admission = Admission {
        version: 1,
        round,
        nonce: random_id()?,
        previous_release: prior.release_id.clone(),
        previous: prior.previous.clone(),
        waiter: waiting,
        application: prior.application.clone(),
        runner: prior.runner.clone(),
        calibration,
    };
    let check = || -> Result<()> {
        let (live_home, live_waiter) = home_before(&writer.records, round, entry)?;
        ensure!(
            !cancelled()
                && writer.lease()? == lease
                && live_home == home
                && live_waiter == admission.waiter
                && writer.records.read("phase")? == "library-preparing\n",
            "The new Library presentation changed before commit"
        );
        Ok(())
    };
    check()?;
    write_once(&writer.records, &leaf("grant", round), &admission, &check)?;
    check()
}

/// The owner publishes matching display ownership *after* takeover and before GUI input/display acquisition.
pub fn confirm_takeover(round: u64, entry: &Entry) -> Result<()> {
    let writer = Writer::from_environment()?;
    let lease = writer.lease()?;
    let records = &writer.records;
    let grant: Admission = read(&records, &leaf("grant", round))?;
    ensure!(
        grant.calibration.launcher == lease.launcher
            && grant.calibration.publisher == lease.chain[0].executable,
        "Only the original launcher can commit display ownership"
    );
    write_once(&records, &leaf("owned", round), &grant, || {
        let (home, waiter) = home_before(&records, round, entry)?;
        ensure!(
            grant.version == 1
                && grant.round == round
                && valid_nonce(&grant.nonce)
                && grant.waiter == waiter
                && grant.previous_release == home.returned.release_id
                && grant.previous == home.returned.previous
                && grant.application == home.returned.application
                && writer.lease()? == lease
                && grant.runner == home.returned.runner
                && records.read("phase")? == "library-acquired\n"
                && read::<Admission>(&records, &leaf("grant", round))? == grant,
            "Library presentation takeover changed"
        );
        Ok(())
    })
}

pub struct LibraryPresentation {
    records: Arc<PrivateDirectory>,
    current: Entry,
    grant: Admission,
    returned: Returned,
}

impl Closed {
    /// A read-only readiness query never consumes the closed presentation.
    pub fn verify_commit(&self, path: &Path, entry: Entry) -> Result<()> {
        let records = Arc::new(PrivateDirectory::open(path)?);
        let round = self
            .returned
            .round
            .checked_add(1)
            .context("No next Library round")?;
        let grant = read::<Admission>(&records, &leaf("grant", round))?;
        LibraryPresentation {
            records,
            current: entry,
            grant,
            returned: self.returned.clone(),
        }
        .current()
    }
    /// A consumed old session cannot bind the prepared record until this specific independent takeover.
    pub fn rebind(
        self,
        path: &Path,
        entry: Entry,
    ) -> Result<(LibraryPresentation, NativePortraitObservation)> {
        let records = Arc::new(PrivateDirectory::open(path)?);
        let round = self
            .returned
            .round
            .checked_add(1)
            .context("No next Library round")?;
        let grant = read::<Admission>(&records, &leaf("grant", round))?;
        let candidate = LibraryPresentation {
            records,
            current: entry,
            grant,
            returned: self.returned,
        };
        let observed = candidate.observe()?;
        let live_calibration = observed.automatic.then_some((
            observed.holding,
            observed.scanout,
            observed.framebuffer,
        ));
        candidate.current()?;
        self.fence
            .reopen(live_calibration)
            .context("Rebind only this backend and its new Library presentation")?;
        Ok((candidate, observed))
    }
}
impl LibraryPresentation {
    fn current(&self) -> Result<()> {
        self.current_in("library-acquired\n")
    }
    fn current_in(&self, phase: &str) -> Result<()> {
        let grant = &self.grant;
        let returned = &self.returned;
        ensure!(
            grant.round == returned.round + 1
                && grant.version == 1
                && valid_nonce(&grant.nonce)
                && self.records.read("phase")? == phase
                && self.current.identity()? == grant.waiter
                && read::<Admission>(&self.records, &leaf("grant", grant.round))? == *grant
                && read::<Admission>(&self.records, &leaf("owned", grant.round))? == *grant
                && grant.previous_release == returned.release_id
                && grant.previous == returned.previous
                && grant.application == returned.application
                && grant.runner == returned.runner
                && grant.waiter != returned.waiter
                && grant.calibration.version == 1
                && grant.calibration.profile == returned.previous.profile
                && grant.calibration.launcher == returned.previous.launcher
                && grant.calibration.publisher == returned.previous.publisher
                && grant.calibration.nonce == returned.previous.nonce
                && grant.calibration.generation == returned.previous.generation
                && grant.application.pid == std::process::id()
                && active_family(&self.records, &grant.calibration)?
                    == (grant.application.clone(), grant.runner.clone()),
            "The committed Library transaction is no longer current"
        );
        self.records.ensure_current()
    }
    /// This is a new observation, with no sleep or previous presentation pose to reuse.
    pub fn observe(&self) -> Result<NativePortraitObservation> {
        self.current()?;
        let fresh = &self.grant.calibration;
        let framebuffer = slint_backend_kindle::probe_native_framebuffer()
            .context("Observe this Library presentation's framebuffer")?;
        ensure!(
            framebuffer.layout_token() == fresh.layout,
            "The new Library framebuffer layout changed"
        );
        let scanout =
            fresh
                .native_direction
                .resolve(fresh.raw_rotation, framebuffer.rotation, false)?;
        let (holding, holding_from_sensor) = match fresh.native_direction {
            NativeDirection::Fixed => (NativePortraitOrientation::Up, false),
            _ => resolve_current_holding(
                scanout,
                slint_backend_kindle::observe_native_portrait_sensor()?,
            )?,
        };
        self.current()?;
        Ok(NativePortraitObservation {
            automatic: fresh.native_direction != NativeDirection::Fixed,
            holding_from_sensor,
            holding,
            scanout,
            framebuffer,
        })
    }
}
