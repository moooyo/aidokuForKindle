//! A startup-only projection for the four fields used by the Library toolbar.

use super::DeviceStatus;
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    time::{Duration, Instant},
};

#[cfg(feature = "startup-stage-diagnostics")]
use crate::reader_timing::{StartupToolbarStage, startup_toolbar_stage};

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

struct Sample {
    at: Instant,
    wifi: bool,
    battery: u8,
}

struct InFlight {
    result: Receiver<Sample>,
    retired: Arc<AtomicBool>,
}

impl Drop for InFlight {
    fn drop(&mut self) {
        self.retired.store(true, Ordering::Release);
    }
}

/// The producer owns only a bounded read, not the Platform or any GUI state.
pub struct StartupToolbarProbe {
    result: Sender<Sample>,
    retired: Arc<AtomicBool>,
}

impl StartupToolbarProbe {
    pub fn run(self, cancelled: impl Fn() -> bool) {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        self.observe(cancelled, query);
        #[cfg(not(all(feature = "kindle", target_os = "linux")))]
        self.observe(cancelled, |_| None);
    }

    pub(super) fn observe(
        self,
        cancelled: impl Fn() -> bool,
        read: impl FnOnce(&dyn Fn() -> bool) -> Option<(bool, u8)>,
    ) {
        let cancelled = || self.retired.load(Ordering::Acquire) || cancelled();
        if cancelled() {
            return;
        }
        let at = Instant::now();
        crate::reader_timing::mark("startup_toolbar_probe_started");
        #[cfg(feature = "startup-stage-diagnostics")]
        startup_toolbar_stage(StartupToolbarStage::ProbeBegin);
        let result = read(&cancelled);
        #[cfg(feature = "startup-stage-diagnostics")]
        startup_toolbar_stage(StartupToolbarStage::ProbeEnd);
        if let Some((wifi, battery)) = result.filter(|_| !cancelled()) {
            let _ = self.result.send(Sample { at, wifi, battery });
            crate::reader_timing::mark("startup_toolbar_probe_ready");
        } else {
            crate::reader_timing::mark("startup_toolbar_probe_unavailable");
        }
    }
}

#[derive(Default)]
enum Probe {
    #[default]
    Idle,
    Pending(InFlight),
    Finished,
}

#[derive(Default)]
pub(super) struct StartupToolbar {
    probe: Probe,
    published: Option<(Option<Instant>, ViewStatus)>,
}

impl StartupToolbar {
    /// A new process's checked initial frame can initiate at most one probe.
    pub(super) fn begin(&mut self) -> Option<StartupToolbarProbe> {
        if !matches!(self.probe, Probe::Idle) {
            return None;
        }
        let (sender, receiver) = mpsc::channel();
        let retired = Arc::new(AtomicBool::new(false));
        self.probe = Probe::Pending(InFlight {
            result: receiver,
            retired: retired.clone(),
        });
        Some(StartupToolbarProbe {
            result: sender,
            retired,
        })
    }

    pub(super) fn view(
        &mut self,
        now: Instant,
        local: impl FnOnce() -> (String, u64),
        fallback: impl FnOnce() -> (bool, u8),
    ) -> Option<ViewStatus> {
        if let Some((Some(at), status)) = &self.published
            && now.saturating_duration_since(*at) < VIEW_TTL
        {
            return Some(status.clone());
        }
        let Probe::Pending(pending) = std::mem::replace(&mut self.probe, Probe::Finished) else {
            return None;
        };
        let sample = pending.result.try_recv().ok();
        // Never join the worker or wait for its result on the UI thread.
        drop(pending);
        let (time, free_bytes) = local();
        let (wifi, battery, at) = match sample {
            Some(sample) if now.saturating_duration_since(sample.at) < MAX_SAMPLE_AGE => {
                crate::reader_timing::mark("startup_toolbar_snapshot_used");
                #[cfg(feature = "startup-stage-diagnostics")]
                startup_toolbar_stage(StartupToolbarStage::SnapshotUsed);
                (sample.wifi, sample.battery, sample.at)
            }
            _ => {
                crate::reader_timing::mark("startup_toolbar_sync_fallback");
                let (wifi, battery) = fallback();
                (wifi, battery, Instant::now())
            }
        };
        let status = ViewStatus {
            time,
            battery,
            wifi,
            free_bytes,
        };
        self.published = Some((Some(at), status.clone()));
        Some(status)
    }

    pub(super) fn invalidate(&mut self, initial_preferences: bool) {
        // The outstanding read does not include either lighting setting.
        if !initial_preferences && matches!(self.probe, Probe::Pending(_)) {
            self.probe = Probe::Finished;
        }
        if let Some((observed, _)) = &mut self.published {
            *observed = None;
        }
    }

    pub(super) fn retire(&mut self) {
        self.invalidate(false);
        self.probe = Probe::Finished;
    }

    pub(super) fn cached(&self) -> Option<ViewStatus> {
        self.published.as_ref().map(|(_, view)| view.clone())
    }
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn query(cancelled: &dyn Fn() -> bool) -> Option<(bool, u8)> {
    let deadline = Instant::now() + Duration::from_millis(250);
    query_helper(deadline, cancelled).or_else(|| query_stock(deadline, cancelled))
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn query_helper(deadline: Instant, cancelled: &dyn Fn() -> bool) -> Option<(bool, u8)> {
    use crate::native_query::{Property, Session};
    let mut session = Session::try_open(deadline, &cancelled)?;
    let wifi = session.query(Property::WifiState, deadline, &cancelled).ok()?;
    let battery = session.query(Property::Battery, deadline, &cancelled).ok()?;
    session.finish(deadline, &cancelled).ok()?;
    parse_radio_status(&wifi, &battery)
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn query_stock(deadline: Instant, cancelled: &dyn Fn() -> bool) -> Option<(bool, u8)> {
    use crate::native_query::Property;
    let read = |property: Property| {
        let remaining = deadline.checked_duration_since(Instant::now())?;
        if cancelled() {
            return None;
        }
        super::bounded_device_command(
            "lipc-get-prop",
            property.legacy_arguments(),
            remaining,
            cancelled,
        )
        .ok()
    };
    parse_radio_status(&read(Property::WifiState)?, &read(Property::Battery)?)
}

#[cfg(any(test, all(feature = "kindle", target_os = "linux")))]
fn parse_radio_status(wifi: &str, battery: &str) -> Option<(bool, u8)> {
    let connected = match wifi.to_ascii_lowercase().as_str() {
        "connected" => true,
        "disconnected" | "notconnected" | "connecting" | "disabled" | "idle" => false,
        _ => return None,
    };
    Some((connected, battery.parse::<u8>().ok()?.min(100)))
}

/// A missed probe still reads real toolbar data; do not query three unrelated
/// properties or use this projection to populate the full device status cache.
#[cfg(all(feature = "kindle", target_os = "linux"))]
pub(super) fn read_now() -> (bool, u8) {
    use crate::native_query::Property;
    if let Some(sample) = query_helper(Instant::now() + Duration::from_millis(250), &|| false) {
        return sample;
    }
    let read = |property: Property| super::command("lipc-get-prop", property.legacy_arguments());
    let wifi = read(Property::WifiState).is_ok_and(|value| value.eq_ignore_ascii_case("connected"));
    let battery = read(Property::Battery)
        .ok()
        .and_then(|value| value.parse::<u8>().ok())
        .unwrap_or(0)
        .min(100);
    (wifi, battery)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn no_fallback() -> (bool, u8) {
        panic!("a usable cached observation does not need a fallback")
    }

    #[test]
    fn first_projection_uses_live_local_fields_and_survives_cover_publications() {
        let mut toolbar = StartupToolbar::default();
        let probe = toolbar.begin().unwrap();
        assert!(toolbar.begin().is_none());
        probe.observe(|| false, |_| Some((false, 13)));
        toolbar.invalidate(true);
        let started = Instant::now();
        let first = toolbar
            .view(started, || ("23:59".into(), 98), no_fallback)
            .unwrap();
        assert_eq!(
            (first.time.as_str(), first.wifi, first.battery, first.free_bytes),
            ("23:59", false, 13, 98)
        );
        assert_eq!(
            toolbar.view(started + Duration::from_secs(4), || panic!("cover flush reread"), no_fallback),
            Some(first.clone())
        );
        assert_eq!(
            toolbar.view(started + VIEW_TTL, || panic!("expired observation cannot refill"), no_fallback),
            None
        );
        assert_eq!(toolbar.cached(), Some(first));
    }

    #[test]
    fn the_view_never_waits_for_an_in_flight_result() {
        let mut toolbar = StartupToolbar::default();
        let probe = toolbar.begin().unwrap();
        let fallback = toolbar
            .view(Instant::now(), || ("07:03".into(), 20), || (true, 20))
            .unwrap();
        assert!(fallback.wifi);
        assert_eq!(fallback.battery, 20);
        probe.observe(|| false, |_| panic!("an abandoned read must not start"));
        assert_eq!(toolbar.cached(), Some(fallback));
    }

    #[test]
    fn retiring_a_probe_during_a_read_discards_its_late_reply() {
        let mut toolbar = StartupToolbar::default();
        let probe = toolbar.begin().unwrap();
        probe.observe(|| false, |cancelled| {
            assert!(!cancelled());
            toolbar.retire();
            assert!(cancelled());
            Some((true, 90))
        });
        assert!(toolbar.cached().is_none());
        assert!(
            toolbar.view(Instant::now(), || panic!("retired sample"), no_fallback).is_none()
        );
    }

    #[test]
    fn failed_and_expired_reads_preserve_the_fallback() {
        for (result, elapsed) in [(None, Duration::ZERO), (Some((true, 54)), MAX_SAMPLE_AGE)] {
            let mut toolbar = StartupToolbar::default();
            toolbar.begin().unwrap().observe(|| false, |_| result);
            let status = toolbar
                .view(Instant::now() + elapsed, || ("00:01".into(), 15), || (false, 14))
                .unwrap();
            assert_eq!((status.wifi, status.battery), (false, 14));
        }
    }

    #[test]
    fn suspend_and_preference_invalidation_retire_pending_or_cached_samples() {
        let mut parked = StartupToolbar::default();
        let probe = parked.begin().unwrap();
        parked.retire();
        probe.observe(|| false, |_| panic!("parking retires the outstanding read"));
        assert!(parked.begin().is_none());
        assert!(
            parked.view(Instant::now(), || panic!("stale snapshot"), no_fallback).is_none()
        );
        let mut prefs = StartupToolbar::default();
        prefs.begin().unwrap().observe(|| false, |_| Some((true, 54)));
        let cached = prefs.view(Instant::now(), || ("00:01".into(), 15), no_fallback);
        prefs.invalidate(false);
        assert!(
            prefs.view(Instant::now(), || panic!("needs an ordinary refresh"), no_fallback).is_none()
        );
        assert_eq!(prefs.cached(), cached);
    }

    #[test]
    fn radio_values_are_usable_only_if_they_parse() {
        assert_eq!(parse_radio_status("CONNECTED", "42"), Some((true, 42)));
        assert_eq!(parse_radio_status("disabled", "101"), Some((false, 100)));
        assert_eq!(parse_radio_status("unexpected", "42"), None);
        assert_eq!(parse_radio_status("idle", "?"), None);
    }
}
