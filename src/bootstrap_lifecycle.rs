//! Bootstrap coordination for stock-powerd sleep without restarting the UI.

use std::{
    cell::RefCell,
    fs,
    path::{Path, PathBuf},
    rc::Rc,
    sync::atomic::{AtomicBool, AtomicI32, Ordering},
    time::{Duration, Instant},
};

use aidoku_kindle::{app::App, platform::Platform, ui::AppWindow};
use anyhow::{Context, Result};
use serde_json::json;
use slint::ComponentHandle;

static SUSPEND_REQUESTED: AtomicBool = AtomicBool::new(false);
static RESUME_REQUESTED: AtomicBool = AtomicBool::new(false);
static SIGNAL_WAKE_FD: AtomicI32 = AtomicI32::new(-1);

#[derive(Clone, Debug, PartialEq, Eq)]
struct Token {
    pid: u32,
    nonce: String,
    epoch: u64,
}

impl Token {
    fn line(&self) -> String {
        format!("{} {} {}\n", self.pid, self.nonce, self.epoch)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Phase {
    Active,
    Quiescing,
    Suspended,
}

impl Phase {
    fn name(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Quiescing => "quiescing",
            Self::Suspended => "suspended",
        }
    }
}

pub struct Coordinator {
    directory: Option<PathBuf>,
    nonce: String,
    pid: u32,
    phase: Phase,
    token: Option<Token>,
    pause_started: Option<Instant>,
    paused_total: Duration,
    resume_frame_baseline: Option<u64>,
    last_error: Option<String>,
    same_process_resumes: u64,
    quick_cleanup_count: u64,
    last_resume_kind: &'static str,
    completed_epoch: u64,
    resume_pending: bool,
    quick_resume_pending: bool,
}

impl Coordinator {
    pub fn new(native: bool) -> Self {
        let directory = if native {
            std::env::var_os("AIDOKU_LAUNCHER_DIR").map(PathBuf::from)
        } else {
            None
        };
        Self {
            directory,
            nonce: std::env::var("AIDOKU_LAUNCH_NONCE").unwrap_or_default(),
            pid: std::process::id(),
            phase: Phase::Active,
            token: None,
            pause_started: None,
            paused_total: Duration::ZERO,
            resume_frame_baseline: None,
            last_error: None,
            same_process_resumes: 0,
            quick_cleanup_count: 0,
            last_resume_kind: "initial",
            completed_epoch: 0,
            resume_pending: false,
            quick_resume_pending: false,
        }
    }

    pub fn active(&self) -> bool {
        self.phase == Phase::Active
    }

    pub fn active_elapsed(&self, started: Instant) -> Duration {
        let current_pause = self.pause_started.map_or(Duration::ZERO, |at| at.elapsed());
        started
            .elapsed()
            .saturating_sub(self.paused_total)
            .saturating_sub(current_pause)
    }

    pub fn publish_initial(&self, app: &Rc<RefCell<App>>, platform: &Platform) {
        if let Err(error) = self.publish_state(app, platform) {
            eprintln!("aidoku: initial lifecycle record failed: {error:#}");
        }
    }

    pub fn drive(
        &mut self,
        ui: &AppWindow,
        app: &Rc<RefCell<App>>,
        platform: &Platform,
    ) -> Result<()> {
        if self.directory.is_none() || self.nonce.is_empty() {
            return Ok(());
        }
        let resume = RESUME_REQUESTED.swap(false, Ordering::AcqRel);
        let suspend = SUSPEND_REQUESTED.swap(false, Ordering::AcqRel);
        let resume_token = if resume {
            self.matching_resume_request()
        } else {
            None
        };
        if let Some(incoming) = resume_token {
            self.token = Some(incoming);
            if self.phase != Phase::Active {
                self.resume(app, platform)?;
            } else if self
                .token
                .as_ref()
                .is_some_and(|token| token.epoch > self.completed_epoch)
            {
                // Both power events can precede the UI callback. The pending
                // input and unaccepted reader/network work still need the same
                // checkpoint/cancellation path, even without a native park.
                self.quiesce(ui, app, platform)?;
                self.quick_resume_pending = true;
                self.resume(app, platform)?;
            }
        } else if suspend {
            if let Some(token) = self.read_request() {
                if token.epoch <= self.completed_epoch {
                    return Ok(());
                }
                self.token = Some(token);
                self.quiesce(ui, app, platform)?;
            }
        } else if resume && self.phase == Phase::Suspended {
            // A cancelled older resume-watch can deliver a final CONT/USR2
            // after the next sleep epoch. Keep ownership parked and stop again.
            #[cfg(unix)]
            if !crate::STOP_REQUESTED.load(Ordering::Acquire) {
                unsafe {
                    libc::raise(libc::SIGSTOP);
                }
            }
            return Ok(());
        }

        if self.phase == Phase::Quiescing {
            let native = platform.sleep_status();
            let ready = match app.borrow_mut().suspend_ready() {
                Ok(ready) => ready,
                Err(error) => {
                    self.last_error = Some(format!("Suspend barrier: {error:#}"));
                    false
                }
            };
            if ready && native.render_paused && native.input_released && native.activity_paused {
                if self.resume_pending {
                    return self.resume(app, platform);
                }
                if RESUME_REQUESTED.load(Ordering::Acquire)
                    && let Some(token) = self.matching_resume_request()
                {
                    RESUME_REQUESTED.store(false, Ordering::Release);
                    self.token = Some(token);
                    return self.resume(app, platform);
                }
                platform.park_for_suspend()?;
                self.phase = Phase::Suspended;
                self.publish_state(app, platform)?;
                if let Some(token) = &self.token {
                    self.write_token("sleep-ready", token)?;
                }
                // Stop all application threads immediately, including a stalled
                // blocking HTTP read. The supervisor independently confirms STOP.
                // It also repeats CONT/USR2 until resume-ready to close the
                // final-check/STOP race during very fast wake transitions.
                #[cfg(unix)]
                if !crate::STOP_REQUESTED.load(Ordering::Acquire)
                    && !(RESUME_REQUESTED.load(Ordering::Acquire)
                        && self.matching_resume_request().is_some())
                {
                    // SAFETY: SIGSTOP intentionally parks this whole process;
                    // no Rust object is destroyed or moved while it is stopped.
                    unsafe {
                        libc::raise(libc::SIGSTOP);
                    }
                }
            }
        }

        if self.phase == Phase::Active
            && let Some(baseline) = self.resume_frame_baseline
        {
            let native = platform.sleep_status();
            if !native.render_paused
                && !native.input_released
                && native.resume_full_frame_count > baseline
            {
                app.borrow_mut().finish_resume_frame()?;
                self.resume_frame_baseline = None;
                self.publish_state(app, platform)?;
                if let Some(token) = &self.token {
                    self.write_token("resume-ready", token)?;
                }
            }
        }
        Ok(())
    }

    fn quiesce(
        &mut self,
        ui: &AppWindow,
        app: &Rc<RefCell<App>>,
        platform: &Platform,
    ) -> Result<()> {
        let was_active = self.phase == Phase::Active;
        self.phase = Phase::Quiescing;
        if self.pause_started.is_none() {
            self.pause_started = Some(Instant::now());
        }
        self.resume_frame_baseline = None;
        self.resume_pending = false;
        self.quick_resume_pending = false;
        platform.begin_suspend()?;
        platform.cancel_pending_pointer(ui.window())?;
        if was_active && let Err(error) = app.borrow_mut().prepare_suspend() {
            self.last_error = Some(format!("Prepare suspend: {error:#}"));
            return Err(error).context("Prepare application suspend");
        }
        Ok(())
    }

    fn resume(&mut self, app: &Rc<RefCell<App>>, platform: &Platform) -> Result<()> {
        self.resume_pending = true;
        let native = platform.sleep_status();
        if !native.render_paused
            || !native.input_released
            || !native.activity_paused
            || !app.borrow_mut().suspend_ready()?
        {
            return Ok(());
        }
        let epoch = self
            .token
            .as_ref()
            .context("The native resume has no sleep epoch")?
            .epoch;
        if let Err(error) = platform.stage_native_portrait_resume(epoch) {
            // Keep the old retained scene and native ownership barrier. The
            // supervisor's current-epoch signal retries can supply a fresh
            // safe snapshot without accepting wrong pixels or touch input.
            self.last_error = Some(format!("Resume portrait observation: {error:#}"));
            platform.park_for_suspend()?;
            self.phase = Phase::Suspended;
            return Ok(());
        }
        let baseline = native.resume_full_frame_count;
        if let Err(error) = app.borrow_mut().resume_after_sleep() {
            self.last_error = Some(format!("Resume application: {error:#}"));
            return Err(error).context("Restore application after sleep");
        }
        platform.resume_after_sleep()?;
        if let Some(started) = self.pause_started.take() {
            self.paused_total += started.elapsed();
        }
        self.phase = Phase::Active;
        self.resume_pending = false;
        self.same_process_resumes += 1;
        if self.quick_resume_pending {
            self.quick_cleanup_count += 1;
            self.last_resume_kind = "quick-cleanup";
        } else {
            self.last_resume_kind = "retained";
        }
        self.quick_resume_pending = false;
        self.completed_epoch = self
            .completed_epoch
            .max(self.token.as_ref().map_or(0, |token| token.epoch));
        self.resume_frame_baseline = Some(baseline);
        // Publish the application snapshot only after the backend has restored
        // pixels; database diagnostics must not delay the first retained frame.
        Ok(())
    }

    pub fn announce_ordinary_exit(&self) {
        let Some(directory) = &self.directory else {
            return;
        };
        let token = self
            .read_request()
            .or_else(|| self.token.clone())
            .unwrap_or(Token {
                pid: self.pid,
                nonce: self.nonce.clone(),
                epoch: 0,
            });
        // The supervisor's own sleep-time fallback termination is not a user exit.
        if fs::read_to_string(directory.join("fallback-sleep"))
            .ok()
            .is_some_and(|line| line.trim() == token.line().trim())
        {
            return;
        }
        if let Err(error) = self.write_token("ordinary-exit", &token) {
            eprintln!("aidoku: exit intent failed: {error:#}");
        }
    }

    fn read_request(&self) -> Option<Token> {
        self.read_token("sleep-request")
    }

    fn matching_resume_request(&self) -> Option<Token> {
        let sleep = self.read_request()?;
        let resume = self.read_token("resume-request")?;
        (resume == sleep && resume.epoch >= self.completed_epoch).then_some(resume)
    }

    fn read_token(&self, name: &str) -> Option<Token> {
        let directory = self.directory.as_ref()?;
        let line = fs::read_to_string(directory.join(name)).ok()?;
        let mut values = line.split_whitespace();
        let pid = values.next()?.parse::<u32>().ok()?;
        let nonce = values.next()?.to_owned();
        let epoch = values.next()?.parse::<u64>().ok()?;
        if values.next().is_some() || pid != self.pid || nonce != self.nonce {
            return None;
        }
        Some(Token { pid, nonce, epoch })
    }

    fn write_token(&self, name: &str, token: &Token) -> Result<()> {
        if let Some(directory) = &self.directory {
            write_atomic(directory, name, token.line().as_bytes())?;
        }
        Ok(())
    }

    fn publish_state(&self, app: &Rc<RefCell<App>>, platform: &Platform) -> Result<()> {
        let Some(directory) = &self.directory else {
            return Ok(());
        };
        let state = json!({
            "pid": self.pid, "nonce": self.nonce, "epoch": self.token.as_ref().map_or(0, |token| token.epoch),
            "phase": self.phase.name(), "same_process_resumes": self.same_process_resumes,
            "quick_cleanup_count": self.quick_cleanup_count, "last_resume_kind": self.last_resume_kind,
            "font_initializations": crate::FONT_INITIALIZATIONS.load(Ordering::Acquire),
            "platform": platform.sleep_status(), "application": app.borrow().lifecycle_snapshot(), "last_error": self.last_error,
        });
        write_atomic(
            directory,
            "lifecycle-state.json",
            &serde_json::to_vec_pretty(&state)?,
        )
    }
}

fn write_atomic(directory: &Path, name: &str, bytes: &[u8]) -> Result<()> {
    let pending = directory.join(format!("{name}.pending"));
    fs::write(&pending, bytes).with_context(|| format!("Write {name}"))?;
    fs::rename(pending, directory.join(name)).with_context(|| format!("Publish {name}"))
}

#[cfg(unix)]
extern "C" fn lifecycle_signal(signal: libc::c_int) {
    if signal == libc::SIGUSR1 {
        SUSPEND_REQUESTED.store(true, Ordering::Release);
    }
    if signal == libc::SIGUSR2 {
        RESUME_REQUESTED.store(true, Ordering::Release);
    }
    signal_poke();
}

#[cfg(unix)]
pub fn signal_poke() {
    let fd = SIGNAL_WAKE_FD.load(Ordering::Relaxed);
    if fd < 0 {
        return;
    }
    let byte = 1u8;
    // SAFETY: the backend retains this nonblocking pipe fd until it is reset;
    // write is async-signal-safe and does not allocate or acquire a Rust lock.
    unsafe {
        #[cfg(target_os = "linux")]
        let saved_errno = *libc::__errno_location();
        libc::write(fd, &byte as *const u8 as *const libc::c_void, 1);
        #[cfg(target_os = "linux")]
        {
            *libc::__errno_location() = saved_errno;
        }
    }
}

#[cfg(not(unix))]
pub fn signal_poke() {}

pub fn install_signal_wakeup(fd: Option<i32>) -> Result<()> {
    SIGNAL_WAKE_FD.store(fd.unwrap_or(-1), Ordering::Release);
    #[cfg(unix)]
    {
        // SAFETY: the handlers only update atomics and write the retained pipe.
        unsafe {
            for signal in [libc::SIGUSR1, libc::SIGUSR2] {
                if libc::signal(signal, lifecycle_signal as *const () as libc::sighandler_t)
                    == libc::SIG_ERR
                {
                    return Err(std::io::Error::last_os_error())
                        .context("Install lifecycle signal handlers");
                }
            }
        }
    }
    Ok(())
}

/// Startup feedback must stop before a pending native ownership transition.
pub fn display_activity_allowed() -> bool {
    !SUSPEND_REQUESTED.load(Ordering::Acquire)
        && !RESUME_REQUESTED.load(Ordering::Acquire)
        && !crate::STOP_REQUESTED.load(Ordering::Acquire)
}

pub fn clear_signal_wakeup() {
    SIGNAL_WAKE_FD.store(-1, Ordering::Release);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn coordinator(directory: &Path) -> Coordinator {
        let mut coordinator = Coordinator::new(false);
        coordinator.directory = Some(directory.to_owned());
        coordinator.pid = 42;
        coordinator.nonce = "test-nonce".into();
        coordinator
    }

    #[test]
    fn resume_request_is_bound_to_the_current_sleep_epoch() {
        let directory = tempfile::tempdir().unwrap();
        let mut coordinator = coordinator(directory.path());
        fs::write(directory.path().join("sleep-request"), "42 test-nonce 1\n").unwrap();
        fs::write(directory.path().join("resume-request"), "42 test-nonce 1\n").unwrap();
        assert_eq!(coordinator.matching_resume_request().unwrap().epoch, 1);
        fs::write(directory.path().join("sleep-request"), "42 test-nonce 2\n").unwrap();
        assert!(coordinator.matching_resume_request().is_none());
        fs::write(directory.path().join("resume-request"), "42 test-nonce 2\n").unwrap();
        assert_eq!(coordinator.matching_resume_request().unwrap().epoch, 2);
        coordinator.completed_epoch = 3;
        assert!(coordinator.matching_resume_request().is_none());
    }

    #[test]
    fn request_from_another_process_or_nonce_is_never_accepted() {
        let directory = tempfile::tempdir().unwrap();
        let coordinator = coordinator(directory.path());
        fs::write(directory.path().join("sleep-request"), "43 test-nonce 1\n").unwrap();
        assert!(coordinator.read_request().is_none());
        fs::write(directory.path().join("sleep-request"), "42 old-nonce 1\n").unwrap();
        assert!(coordinator.read_request().is_none());
    }

    #[test]
    fn current_fallback_termination_does_not_publish_a_stale_ordinary_exit() {
        let directory = tempfile::tempdir().unwrap();
        let mut coordinator = coordinator(directory.path());
        coordinator.token = Some(Token {
            pid: 42,
            nonce: "test-nonce".into(),
            epoch: 1,
        });
        fs::write(directory.path().join("sleep-request"), "42 test-nonce 2\n").unwrap();
        fs::write(directory.path().join("fallback-sleep"), "42 test-nonce 2\n").unwrap();
        coordinator.announce_ordinary_exit();
        assert!(!directory.path().join("ordinary-exit").exists());
        fs::remove_file(directory.path().join("fallback-sleep")).unwrap();
        coordinator.announce_ordinary_exit();
        assert_eq!(
            fs::read_to_string(directory.path().join("ordinary-exit")).unwrap(),
            "42 test-nonce 2\n"
        );
    }
}
