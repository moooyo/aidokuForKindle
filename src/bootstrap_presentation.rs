//! Bounded startup ownership and ordinary-exit presentation.

use std::{
    path::Path,
    time::{Duration, Instant},
};

use aidoku_kindle::platform::{Platform, SleepStatus};
use anyhow::Result;

pub const ACTIVITY_INTERVAL: Duration = Duration::from_millis(1_250);
const FEEDBACK_TIMEOUT: Duration = Duration::from_secs(2);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Default)]
pub struct StartupFeedback {
    #[cfg(all(feature = "kindle", target_os = "linux"))]
    splash: Option<slint_backend_kindle::StartupSplash>,
}

impl StartupFeedback {
    pub fn show(platform: &Platform, data_dir: &Path, demo: bool) -> Result<Self> {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if platform.is_kindle() && crate::bootstrap_lifecycle::display_activity_allowed() {
            use aidoku_kindle::native_presentation::NativePresentationContext;
            use anyhow::Context;

            // A raw startup frame must pass the same authenticated ownership
            // check as the normal backend, before any pixels are written.
            let Some(presentation) =
                NativePresentationContext::from_environment(platform.profile())?
            else {
                return Ok(Self::default());
            };
            let observed = presentation.observe_startup()?;
            let style = startup_style(data_dir, demo);
            let (width, height) = platform.runtime_logical_size(false);
            let splash = slint_backend_kindle::StartupSplash::show_with_orientation(
                startup_images(&style.language)?,
                (width * 2, height * 2),
                style.dark_mode,
                observed.holding != observed.scanout,
                crate::bootstrap_lifecycle::display_activity_allowed,
                observed.framebuffer,
            )
            .context("Show the authenticated native startup feedback")?;
            aidoku_kindle::reader_timing::mark("startup_feedback_submitted");
            return Ok(Self {
                splash: Some(splash),
            });
        }
        let _ = (platform, data_dir, demo);
        Ok(Self::default())
    }

    pub fn set_stage(&self, stage: usize) {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if let Some(splash) = &self.splash {
            splash.set_stage(stage);
        }
        let _ = stage;
    }

    pub fn pause(&mut self) -> Result<()> {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if let Some(splash) = &mut self.splash {
            splash.pause()?;
        }
        Ok(())
    }

    pub fn resume(&mut self) -> Result<()> {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if let Some(splash) = &mut self.splash {
            splash.resume()?;
        }
        Ok(())
    }

    pub fn finish(self) -> Result<()> {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if let Some(splash) = self.splash {
            splash.finish()?;
        }
        Ok(())
    }
}

#[cfg(any(test, all(feature = "kindle", target_os = "linux")))]
#[derive(Debug, PartialEq, Eq)]
struct StartupStyle {
    language: String,
    dark_mode: bool,
}

#[cfg(any(test, all(feature = "kindle", target_os = "linux")))]
fn startup_style(data_dir: &Path, demo: bool) -> StartupStyle {
    use rusqlite::{Connection, OpenFlags};

    let data_dir = if demo {
        data_dir.join("demo")
    } else {
        data_dir.to_owned()
    };
    let document = (|| {
        // Reading one optional row must not create a database, migrate it, or
        // wait behind another connection before the first loading frame.
        let connection = Connection::open_with_flags(
            data_dir.join("library.sqlite3"),
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .ok()?;
        connection.busy_timeout(Duration::ZERO).ok()?;
        let document = connection.query_row(
            "SELECT document FROM preferences WHERE key='application' AND length(document)<=65536",
            [],
            |row| row.get::<_, String>(0),
        ).ok()?;
        serde_json::from_str::<serde_json::Value>(&document).ok()
    })();
    let language = document
        .as_ref()
        .and_then(|value| value.get("language"))
        .and_then(serde_json::Value::as_str)
        .filter(|value| matches!(*value, "zh-CN" | "zh-TW" | "en" | "ja"))
        .unwrap_or("zh-CN");
    StartupStyle {
        language: language.to_owned(),
        dark_mode: document
            .as_ref()
            .and_then(|value| value.get("night_mode"))
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false),
    }
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn startup_images(language: &str) -> Result<slint_backend_kindle::StartupImages> {
    use slint_backend_kindle::{StartupImage, StartupImages};

    macro_rules! images {
        ($locale:literal) => {
            StartupImages {
                title: StartupImage::from_encoded(include_bytes!("../assets/startup/title.gray"))?,
                starting: StartupImage::from_encoded(include_bytes!(concat!(
                    "../assets/startup/",
                    $locale,
                    "-startup_loading.gray"
                )))?,
                stages: [
                    StartupImage::from_encoded(include_bytes!(concat!(
                        "../assets/startup/",
                        $locale,
                        "-startup_fonts.gray"
                    )))?,
                    StartupImage::from_encoded(include_bytes!(concat!(
                        "../assets/startup/",
                        $locale,
                        "-startup_data.gray"
                    )))?,
                    StartupImage::from_encoded(include_bytes!(concat!(
                        "../assets/startup/",
                        $locale,
                        "-startup_interface.gray"
                    )))?,
                ],
            }
        };
    }
    Ok(match language {
        "zh-TW" => images!("zh-TW"),
        "en" => images!("en"),
        "ja" => images!("ja"),
        _ => images!("zh-CN"),
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ExitPhase {
    Idle,
    AwaitingFrame,
    Draining,
}

pub struct ExitFeedback {
    phase: ExitPhase,
    submitted_baseline: u64,
    phase_started: Instant,
    last_activity: Instant,
    desktop_yielded: bool,
    frame: i32,
}

impl ExitFeedback {
    pub fn new(now: Instant) -> Self {
        Self {
            phase: ExitPhase::Idle,
            submitted_baseline: 0,
            phase_started: now,
            last_activity: now,
            desktop_yielded: false,
            frame: 0,
        }
    }

    pub fn requested(&self) -> bool {
        self.phase != ExitPhase::Idle
    }

    pub fn started_shutdown(&self) -> bool {
        self.phase == ExitPhase::Draining
    }

    pub fn request(&mut self, submitted_baseline: u64, now: Instant) -> bool {
        if self.requested() {
            return false;
        }
        self.phase = ExitPhase::AwaitingFrame;
        self.submitted_baseline = submitted_baseline;
        self.phase_started = now;
        self.last_activity = now;
        true
    }

    pub fn frame_submitted(
        &mut self,
        native: bool,
        submitted_count: u64,
        desktop_rendered: bool,
        notifier_available: bool,
    ) -> bool {
        if self.phase != ExitPhase::AwaitingFrame {
            return false;
        }
        if native {
            return submitted_count > self.submitted_baseline;
        }
        if notifier_available {
            return desktop_rendered;
        }
        // Some desktop software renderers do not provide AfterRendering. Give
        // their ordinary event loop a full turn before beginning local cleanup;
        // this fallback is never accepted as native SEND evidence.
        std::mem::replace(&mut self.desktop_yielded, true)
    }

    pub fn begin_shutdown(&mut self, now: Instant) {
        self.phase = ExitPhase::Draining;
        self.phase_started = now;
    }

    pub fn frame_timed_out(&self, now: Instant) -> bool {
        self.phase == ExitPhase::AwaitingFrame
            && now.saturating_duration_since(self.phase_started) >= FEEDBACK_TIMEOUT
    }

    pub fn shutdown_timed_out(&self, now: Instant) -> bool {
        self.phase == ExitPhase::Draining
            && now.saturating_duration_since(self.phase_started) >= SHUTDOWN_TIMEOUT
    }

    pub fn next_activity_frame(&mut self, now: Instant) -> Option<i32> {
        if !self.requested()
            || now.saturating_duration_since(self.last_activity) < ACTIVITY_INTERVAL
        {
            return None;
        }
        self.last_activity = now;
        self.frame = (self.frame + 1) % 3;
        Some(self.frame)
    }
}

pub fn exit_display_owned(active: bool, native: SleepStatus, signals_allow_display: bool) -> bool {
    active
        && signals_allow_display
        && !native.requested
        && !native.render_paused
        && !native.input_released
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_exit_requires_a_new_successful_send() {
        let mut feedback = ExitFeedback::new(Instant::now());
        assert!(feedback.request(7, Instant::now()));
        assert!(!feedback.frame_submitted(true, 7, true, true));
        assert!(!feedback.frame_submitted(true, 7, true, false));
        assert!(!feedback.started_shutdown());
        assert!(feedback.frame_submitted(true, 8, false, false));
        feedback.begin_shutdown(Instant::now());
        assert!(feedback.started_shutdown());
    }

    #[test]
    fn duplicate_exit_requests_do_not_reset_the_submission_baseline_or_timeout() {
        let now = Instant::now();
        let mut feedback = ExitFeedback::new(now);
        assert!(feedback.request(7, now));
        assert!(!feedback.request(8, now + Duration::from_secs(1)));
        assert!(feedback.frame_submitted(true, 8, false, false));
        assert!(feedback.frame_timed_out(now + FEEDBACK_TIMEOUT));
    }

    #[test]
    fn sleep_and_native_handoffs_disable_exit_presentation() {
        let active = SleepStatus::default();
        assert!(exit_display_owned(true, active, true));
        assert!(!exit_display_owned(false, active, true));
        assert!(!exit_display_owned(true, active, false));
        for paused in [
            SleepStatus {
                requested: true,
                ..active
            },
            SleepStatus {
                render_paused: true,
                ..active
            },
            SleepStatus {
                input_released: true,
                ..active
            },
        ] {
            assert!(!exit_display_owned(true, paused, true));
        }
    }

    #[test]
    fn activity_frames_advance_only_for_pending_work_at_the_bounded_rate() {
        let now = Instant::now();
        let mut feedback = ExitFeedback::new(now);
        assert_eq!(feedback.next_activity_frame(now + ACTIVITY_INTERVAL), None);
        feedback.request(0, now);
        assert_eq!(
            feedback.next_activity_frame(now + ACTIVITY_INTERVAL - Duration::from_millis(1)),
            None
        );
        assert_eq!(
            feedback.next_activity_frame(now + ACTIVITY_INTERVAL),
            Some(1)
        );
        assert_eq!(feedback.next_activity_frame(now + ACTIVITY_INTERVAL), None);
    }

    #[test]
    fn startup_style_reads_only_the_existing_database_and_keeps_demo_isolation() {
        let directory = tempfile::tempdir().unwrap();
        assert_eq!(startup_style(directory.path(), false).language, "zh-CN");
        assert!(!directory.path().join("library.sqlite3").exists());
        let connection =
            rusqlite::Connection::open(directory.path().join("library.sqlite3")).unwrap();
        connection
            .execute_batch("CREATE TABLE preferences(key TEXT PRIMARY KEY, document TEXT NOT NULL)")
            .unwrap();
        connection
            .execute(
                "INSERT INTO preferences(key,document) VALUES('application',?1)",
                [r#"{"language":"ja","night_mode":true}"#],
            )
            .unwrap();
        assert_eq!(
            startup_style(directory.path(), false),
            StartupStyle {
                language: "ja".into(),
                dark_mode: true
            }
        );
        assert_eq!(startup_style(directory.path(), true).language, "zh-CN");
    }
}
