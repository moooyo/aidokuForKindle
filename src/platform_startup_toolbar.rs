//! A startup-only projection for the four fields used by the Library toolbar.

use super::DeviceStatus;
use std::time::{Duration, Instant};

const MAX_SAMPLE_AGE: Duration = Duration::from_secs(1);
const VIEW_TTL: Duration = Duration::from_secs(30);

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ViewStatus {
    pub time: String,
    pub battery: u8,
    pub wifi: bool,
    pub free_bytes: u64,
}

impl From<DeviceStatus> for ViewStatus {
    fn from(value: DeviceStatus) -> Self {
        Self {
            time: value.time,
            battery: value.battery,
            wifi: value.wifi,
            free_bytes: value.free_bytes,
        }
    }
}

#[derive(Default)]
enum Probe {
    #[default]
    Idle,
    Failed,
    Ready {
        at: Instant,
        wifi: bool,
        battery: u8,
    },
    Finished,
}

#[derive(Default)]
pub(super) struct StartupToolbar {
    probe: Probe,
    published: Option<(Option<Instant>, ViewStatus)>,
}

impl StartupToolbar {
    /// Only a new process's first checked initial frame can initiate the probe.
    pub(super) fn observe(&mut self, probe: impl FnOnce() -> Option<(bool, u8)>) {
        if !matches!(self.probe, Probe::Idle) {
            return;
        }
        let at = Instant::now();
        crate::reader_timing::mark("startup_toolbar_probe_started");
        self.probe = match probe() {
            Some((wifi, battery)) => {
                crate::reader_timing::mark("startup_toolbar_probe_ready");
                Probe::Ready { at, wifi, battery }
            }
            None => {
                crate::reader_timing::mark("startup_toolbar_probe_unavailable");
                Probe::Failed
            }
        };
    }

    pub(super) fn view(
        &mut self,
        now: Instant,
        local: impl FnOnce() -> (String, u64),
    ) -> Option<ViewStatus> {
        if let Some((Some(at), status)) = &self.published
            && now.saturating_duration_since(*at) < VIEW_TTL
        {
            return Some(status.clone());
        }
        match std::mem::replace(&mut self.probe, Probe::Finished) {
            Probe::Ready { at, wifi, battery }
                if now.saturating_duration_since(at) < MAX_SAMPLE_AGE =>
            {
                // Use the time and free space at view construction, after attachment.
                let (time, free_bytes) = local();
                let status = ViewStatus {
                    time,
                    battery,
                    wifi,
                    free_bytes,
                };
                self.published = Some((Some(at), status.clone()));
                crate::reader_timing::mark("startup_toolbar_snapshot_used");
                Some(status)
            }
            Probe::Failed | Probe::Ready { .. } => {
                crate::reader_timing::mark("startup_toolbar_sync_fallback");
                None
            }
            _ => None,
        }
    }

    pub(super) fn invalidate(&mut self, initial_preferences: bool) {
        // These two radio/power fields do not include either lighting setting.
        if !initial_preferences && matches!(self.probe, Probe::Ready { .. }) {
            self.probe = Probe::Failed;
        }
        if let Some((observed, _)) = &mut self.published {
            *observed = None;
        }
    }

    pub(super) fn retire(&mut self) {
        self.invalidate(false);
        if matches!(self.probe, Probe::Idle) {
            self.probe = Probe::Finished;
        }
    }

    pub(super) fn cached(&self) -> Option<ViewStatus> {
        self.published.as_ref().map(|(_, view)| view.clone())
    }
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
pub(super) fn query(cancelled: &impl Fn() -> bool) -> Option<(bool, u8)> {
    use crate::native_query::{Property, Session};
    let deadline = Instant::now() + Duration::from_millis(80);
    let mut session = Session::try_open(deadline, cancelled)?;
    let wifi = session
        .query(Property::WifiState, deadline, cancelled)
        .ok()?;
    let wifi = match wifi.to_ascii_lowercase().as_str() {
        "connected" => true,
        "disconnected" | "notconnected" | "connecting" | "disabled" | "idle" => false,
        _ => return None,
    };
    let battery: u8 = session
        .query(Property::Battery, deadline, cancelled)
        .ok()?
        .parse()
        .ok()?;
    session.finish(deadline, cancelled).ok()?;
    (!cancelled() && battery <= 100).then_some((wifi, battery))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_projection_uses_live_local_fields_and_survives_cover_publications() {
        let mut toolbar = StartupToolbar::default();
        toolbar.observe(|| Some((false, 13)));
        toolbar.observe(|| panic!("a duplicate full frame must not repeat the probe"));
        toolbar.invalidate(true);
        let started = Instant::now();
        let first = toolbar.view(started, || ("23:59".into(), 98)).unwrap();
        assert_eq!(
            (
                first.time.as_str(),
                first.wifi,
                first.battery,
                first.free_bytes
            ),
            ("23:59", false, 13, 98)
        );
        assert_eq!(
            toolbar.view(started + Duration::from_secs(4), || panic!(
                "cover flush reread"
            )),
            Some(first.clone())
        );
        assert_eq!(
            toolbar.view(started + VIEW_TTL, || panic!(
                "expired observation cannot refill"
            )),
            None
        );
        assert_eq!(toolbar.cached(), Some(first));
    }

    #[test]
    fn failure_sleep_expiry_and_invalidation_preserve_the_normal_read_path() {
        let mut missing = StartupToolbar::default();
        missing.observe(|| None);
        assert_eq!(missing.view(Instant::now(), || panic!("no snapshot")), None);
        let mut parked = StartupToolbar::default();
        parked.observe(|| Some((true, 20)));
        parked.retire();
        parked.observe(|| panic!("resume cannot resample the old startup"));
        assert_eq!(
            parked.view(Instant::now(), || panic!("stale snapshot")),
            None
        );
        let mut old = StartupToolbar::default();
        old.observe(|| Some((true, 20)));
        assert_eq!(
            old.view(Instant::now() + MAX_SAMPLE_AGE, || panic!(
                "late attachment"
            )),
            None
        );
        let mut pref = StartupToolbar::default();
        pref.observe(|| Some((true, 54)));
        let cached = pref.view(Instant::now(), || ("00:01".into(), 15));
        pref.invalidate(false);
        assert_eq!(
            pref.view(Instant::now(), || panic!("needs a full refresh")),
            None
        );
        assert_eq!(pref.cached(), cached);
    }
}
