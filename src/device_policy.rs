//! Background device observations and ownership-aware reading Wi-Fi policy.

use std::{
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, SyncSender},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use anyhow::{Context, Result, bail};

const OBSERVATION_INTERVAL: Duration = Duration::from_secs(10);
const OBSERVATION_MAX_AGE: Duration = Duration::from_secs(45);
const WIFI_SERVICE: &str = "com.lab126.wifid";
const COMMAND_SERVICE: &str = "com.lab126.cmd";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct DevicePolicyCapabilities {
    pub wifi_control: bool,
    pub screensaver: bool,
    pub stylus: bool,
}

/// Unknown or expired observations are distinct from a measured false value.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DevicePolicyObservation {
    pub charging: Option<bool>,
    pub wifi_connected: Option<bool>,
    pub wifi_enabled: Option<bool>,
    pub capabilities: DevicePolicyCapabilities,
    pub wifi_owned: bool,
    pub error: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct WifiState {
    enabled: bool,
    wireless_enabled: bool,
}

trait DeviceServices {
    fn read(&mut self, service: &str, property: &str) -> Result<String>;
    fn write(&mut self, service: &str, property: &str, value: bool) -> Result<()>;
}

fn read_switch(services: &mut impl DeviceServices, service: &str, property: &str) -> Result<bool> {
    match services.read(service, property)?.trim() {
        "0" => Ok(false),
        "1" => Ok(true),
        _ => bail!("The device service returned an unknown switch value"),
    }
}

fn read_wifi(services: &mut impl DeviceServices) -> Result<WifiState> {
    Ok(WifiState {
        enabled: read_switch(services, WIFI_SERVICE, "enable")?,
        wireless_enabled: read_switch(services, COMMAND_SERVICE, "wirelessEnable")?,
    })
}

fn connected_state(value: &str) -> Option<bool> {
    match value.trim().to_ascii_lowercase().as_str() {
        "connected" => Some(true),
        "disconnected" | "notconnected" | "connecting" | "disabled" | "idle" => Some(false),
        _ => None,
    }
}

#[derive(Default)]
struct WifiPolicy {
    original: Option<WifiState>,
    applied: Option<WifiState>,
    attempted: Option<WifiState>,
    yielded: bool,
}

impl WifiPolicy {
    fn owns_wifi(&self) -> bool {
        self.original.is_some()
    }

    fn owns_state(&self, state: WifiState) -> bool {
        Some(state) == self.applied || Some(state) == self.attempted
    }

    fn reconcile(&mut self, services: &mut impl DeviceServices, disable: bool) -> Result<()> {
        self.reconcile_while(services, disable, || disable)
    }

    fn reconcile_while(
        &mut self,
        services: &mut impl DeviceServices,
        disable: bool,
        still_requested: impl Fn() -> bool,
    ) -> Result<()> {
        if !disable || self.yielded {
            let result = self.restore(services);
            if !disable && result.is_ok() {
                self.yielded = false;
            }
            return result;
        }
        if self.original.is_some() {
            if !self.owns_state(read_wifi(services)?) {
                // A manual or system change retires our ownership. A subsequent
                // poll must not fight that change until the reading lease ends.
                self.original = None;
                self.applied = None;
                self.attempted = None;
                self.yielded = true;
            }
            return Ok(());
        }
        let original = read_wifi(services)?;
        if !original.enabled || !still_requested() {
            return Ok(());
        }
        self.original = Some(original);
        self.applied = Some(original);
        let result = (|| {
            if !still_requested() {
                return self.restore(services);
            }
            self.write_owned(
                services,
                WifiState {
                    enabled: false,
                    ..original
                },
                WIFI_SERVICE,
                "enable",
                false,
            )?;
            if !still_requested() {
                return self.restore(services);
            }
            self.write_owned(
                services,
                WifiState {
                    enabled: false,
                    wireless_enabled: false,
                },
                COMMAND_SERVICE,
                "wirelessEnable",
                false,
            )
        })();
        if result.is_err() {
            // Partial failures retain the exact last attempted state for a
            // compare-before-restore retry, rather than losing the original.
            self.yielded = true;
            let _ = self.restore(services);
        }
        result
    }

    fn write_owned(
        &mut self,
        services: &mut impl DeviceServices,
        expected: WifiState,
        service: &str,
        property: &str,
        value: bool,
    ) -> Result<()> {
        // A setter can fail either before or after changing the property. Both
        // the confirmed state and this attempted state remain attributable to
        // us until a read resolves the outcome; neither may be forgotten early.
        self.attempted = Some(expected);
        services.write(service, property, value)?;
        self.applied = Some(expected);
        self.attempted = None;
        Ok(())
    }

    fn restore(&mut self, services: &mut impl DeviceServices) -> Result<()> {
        let Some(original) = self.original else {
            return Ok(());
        };
        let current = read_wifi(services)?;
        if !self.owns_state(current) {
            self.original = None;
            self.applied = None;
            self.attempted = None;
            self.yielded = true;
            return Ok(());
        }
        self.applied = Some(current);
        self.attempted = None;
        let mut current = current;
        if current.wireless_enabled != original.wireless_enabled {
            current.wireless_enabled = original.wireless_enabled;
            self.write_owned(
                services,
                current,
                COMMAND_SERVICE,
                "wirelessEnable",
                original.wireless_enabled,
            )?;
        }
        if current.enabled != original.enabled {
            current.enabled = original.enabled;
            self.write_owned(services, current, WIFI_SERVICE, "enable", original.enabled)?;
        }
        self.original = None;
        self.applied = None;
        self.attempted = None;
        Ok(())
    }
}

#[derive(Default)]
struct Shared {
    disable_wifi: AtomicBool,
    network_demand: AtomicBool,
    network_request: Mutex<Option<(Instant, bool)>>,
    paused: AtomicBool,
    stop: AtomicBool,
    restored: AtomicBool,
    observation: Mutex<Option<(Instant, DevicePolicyObservation)>>,
}

pub(super) struct DevicePolicyWorker {
    shared: Arc<Shared>,
    sender: SyncSender<()>,
    thread: Option<JoinHandle<()>>,
}

impl DevicePolicyWorker {
    pub(super) fn start() -> Result<Self> {
        let shared = Arc::new(Shared::default());
        shared.restored.store(true, Ordering::Release);
        let (sender, receiver) = mpsc::sync_channel(1);
        let worker_shared = shared.clone();
        let thread = thread::Builder::new()
            .name("aidoku-device-policy".into())
            .spawn(move || run_loop(&worker_shared, receiver, &mut NativeDeviceServices))
            .context("Start the background device policy worker")?;
        Ok(Self {
            shared,
            sender,
            thread: Some(thread),
        })
    }

    fn notify(&self) {
        let _ = self.sender.try_send(());
    }

    pub(super) fn request_wifi_disabled(&self, disabled: bool) {
        let disabled = disabled
            && !self.shared.paused.load(Ordering::Acquire)
            && !self.shared.network_demand.load(Ordering::Acquire);
        if self.shared.disable_wifi.swap(disabled, Ordering::AcqRel) != disabled {
            self.shared.restored.store(false, Ordering::Release);
            self.notify();
        }
    }

    pub(super) fn request_network_for_user(&self) {
        if !self.shared.network_demand.swap(true, Ordering::AcqRel) {
            let wait_for_connection = self.network_policy_blocked();
            *self
                .shared
                .network_request
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner) =
                Some((Instant::now(), wait_for_connection));
        }
        self.request_wifi_disabled(false);
        self.notify();
    }

    pub(super) fn user_network_ready(&self) -> bool {
        user_network_ready(&self.shared)
    }

    pub(super) fn finish_user_network_request(&self) {
        self.shared.network_demand.store(false, Ordering::Release);
        *self
            .shared
            .network_request
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = None;
        self.notify();
    }

    pub(super) fn network_policy_blocked(&self) -> bool {
        self.shared.disable_wifi.load(Ordering::Acquire)
            || !self.shared.restored.load(Ordering::Acquire)
            || self.shared.network_demand.load(Ordering::Acquire) && !self.user_network_ready()
    }

    pub(super) fn pause(&self) {
        self.shared.paused.store(true, Ordering::Release);
        self.finish_user_network_request();
        self.shared.disable_wifi.store(false, Ordering::Release);
        self.shared.restored.store(false, Ordering::Release);
        self.notify();
    }

    pub(super) fn resume(&self) {
        self.shared.paused.store(false, Ordering::Release);
        self.notify();
    }

    pub(super) fn restored(&self) -> bool {
        self.shared.restored.load(Ordering::Acquire)
    }

    pub(super) fn observation(&self) -> DevicePolicyObservation {
        let observation = self
            .shared
            .observation
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let Some((observed_at, observation)) = observation.as_ref() else {
            return DevicePolicyObservation::default();
        };
        let mut result = observation.clone();
        if observed_at.elapsed() > OBSERVATION_MAX_AGE {
            result.charging = None;
            result.wifi_connected = None;
            result.wifi_enabled = None;
            result.capabilities.wifi_control = false;
        }
        result
    }
}

fn user_network_ready(shared: &Shared) -> bool {
    if !shared.restored.load(Ordering::Acquire) {
        return false;
    }
    let request = *shared
        .network_request
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let Some((requested_at, true)) = request else {
        // A manually disabled or otherwise unavailable network is not ours to
        // enable. The caller may attempt its ordinary connection/error path.
        return true;
    };
    shared
        .observation
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .as_ref()
        .is_some_and(|(observed_at, observation)| {
            *observed_at >= requested_at
                && observed_at.elapsed() <= OBSERVATION_MAX_AGE
                && observation.wifi_connected == Some(true)
                && observation.wifi_enabled == Some(true)
                && !observation.wifi_owned
        })
}

impl Drop for DevicePolicyWorker {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Release);
        self.shared.disable_wifi.store(false, Ordering::Release);
        self.notify();
        if let Some(thread) = self.thread.take() {
            // Individual LIPC commands already have a bounded deadline. Normal
            // exit waits for ownership restoration rather than abandoning it.
            let _ = thread.join();
        }
    }
}

fn run_loop(shared: &Shared, receiver: Receiver<()>, services: &mut impl DeviceServices) {
    let mut policy = WifiPolicy::default();
    loop {
        let stopping = shared.stop.load(Ordering::Acquire);
        let paused = shared.paused.load(Ordering::Acquire);
        let requested = || {
            shared.disable_wifi.load(Ordering::Acquire)
                && !shared.network_demand.load(Ordering::Acquire)
                && !shared.paused.load(Ordering::Acquire)
                && !shared.stop.load(Ordering::Acquire)
        };
        let disabled = requested() && !paused && !stopping;
        let mut policy_error = policy.reconcile_while(services, disabled, requested).err();
        if !requested() && policy.owns_wifi() {
            if let Err(error) = policy.restore(services) {
                policy_error = Some(error);
            }
        }
        let retry_policy = policy_error.is_some();
        shared
            .restored
            .store(!policy.owns_wifi(), Ordering::Release);
        if shared.stop.load(Ordering::Acquire) {
            if let Some(error) = policy_error {
                eprintln!("aidoku: restore reading Wi-Fi ownership: {error:#}");
            }
            return;
        }
        let paused = shared.paused.load(Ordering::Acquire);
        if !paused {
            // Timestamp the beginning of the sample. A request arriving while
            // LIPC is being read must not accept that in-progress old sample.
            let observed_at = Instant::now();
            let wifi = read_wifi(services).ok();
            let observation = DevicePolicyObservation {
                charging: read_switch(services, "com.lab126.powerd", "isCharging").ok(),
                wifi_connected: services
                    .read(WIFI_SERVICE, "cmState")
                    .ok()
                    .and_then(|value| connected_state(&value)),
                wifi_enabled: wifi.map(|state| state.enabled),
                capabilities: DevicePolicyCapabilities {
                    wifi_control: wifi.is_some(),
                    // This backend relinquishes its framebuffer to stock powerd
                    // during sleep and has no stylus tool/proximity event API.
                    screensaver: false,
                    stylus: false,
                },
                wifi_owned: policy.owns_wifi(),
                error: policy_error.map(|error| error.to_string()),
            };
            *shared
                .observation
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner) =
                Some((observed_at, observation));
        }
        if paused && !policy.owns_wifi() {
            if receiver.recv().is_err() {
                return;
            }
        } else if matches!(
            receiver.recv_timeout(
                if retry_policy
                    || shared.network_demand.load(Ordering::Acquire) && !user_network_ready(shared)
                {
                    Duration::from_secs(1)
                } else {
                    OBSERVATION_INTERVAL
                }
            ),
            Err(mpsc::RecvTimeoutError::Disconnected)
        ) {
            let _ = policy.restore(services);
            return;
        }
    }
}

struct NativeDeviceServices;

impl DeviceServices for NativeDeviceServices {
    fn read(&mut self, service: &str, property: &str) -> Result<String> {
        native_command("lipc-get-prop", &[service, property])
    }

    fn write(&mut self, service: &str, property: &str, value: bool) -> Result<()> {
        native_command(
            "lipc-set-prop",
            &["-i", service, property, if value { "1" } else { "0" }],
        )
        .map(|_| ())
    }
}

fn native_command(program: &str, arguments: &[&str]) -> Result<String> {
    #[cfg(all(feature = "kindle", target_os = "linux"))]
    {
        super::command(program, arguments)
    }
    #[cfg(not(all(feature = "kindle", target_os = "linux")))]
    {
        let _ = (program, arguments);
        bail!("Native device policy services are unavailable in this build")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FakeServices {
        state: WifiState,
        writes: Vec<(String, String, bool)>,
        fail_once: Option<(&'static str, bool)>,
        fail_before_write: bool,
        unreadable: bool,
        revoke_on_read: Option<Arc<AtomicBool>>,
        revoke_on_write: Option<Arc<AtomicBool>>,
    }

    impl FakeServices {
        fn enabled() -> Self {
            Self {
                state: WifiState {
                    enabled: true,
                    wireless_enabled: true,
                },
                writes: Vec::new(),
                fail_once: None,
                fail_before_write: false,
                unreadable: false,
                revoke_on_read: None,
                revoke_on_write: None,
            }
        }
    }

    impl DeviceServices for FakeServices {
        fn read(&mut self, _service: &str, property: &str) -> Result<String> {
            if self.unreadable {
                bail!("The service is temporarily unavailable");
            }
            let enabled = match property {
                "enable" => self.state.enabled,
                "wirelessEnable" => self.state.wireless_enabled,
                _ => bail!("Unexpected property"),
            };
            if property == "wirelessEnable" {
                if let Some(requested) = &self.revoke_on_read {
                    requested.store(false, Ordering::Release);
                }
            }
            Ok(String::from(if enabled { "1" } else { "0" }))
        }

        fn write(&mut self, service: &str, property: &str, value: bool) -> Result<()> {
            self.writes.push((service.into(), property.into(), value));
            if self.fail_before_write && self.fail_once == Some((property, value)) {
                self.fail_once = None;
                bail!("The setter failed before changing its property");
            }
            match property {
                "enable" => self.state.enabled = value,
                "wirelessEnable" => self.state.wireless_enabled = value,
                _ => bail!("Unexpected property"),
            }
            if property == "enable" && !value {
                if let Some(requested) = &self.revoke_on_write {
                    requested.store(false, Ordering::Release);
                }
            }
            if self.fail_once == Some((property, value)) {
                self.fail_once = None;
                bail!("The setter failed after changing its property");
            }
            Ok(())
        }
    }

    #[test]
    fn reading_lease_restores_the_original_pair_in_the_correct_order() {
        let mut services = FakeServices::enabled();
        let original = services.state;
        let mut policy = WifiPolicy::default();
        policy.reconcile(&mut services, true).unwrap();
        assert!(policy.owns_wifi());
        assert!(!services.state.enabled);
        assert!(!services.state.wireless_enabled);
        policy.reconcile(&mut services, true).unwrap();
        assert_eq!(services.writes.len(), 2);
        policy.reconcile(&mut services, false).unwrap();
        assert_eq!(services.state, original);
        assert!(!policy.owns_wifi());
        assert_eq!(services.writes[2].1, "wirelessEnable");
        assert_eq!(services.writes[3].1, "enable");
    }

    #[test]
    fn an_already_disabled_radio_is_never_enabled_by_cleanup() {
        let mut services = FakeServices::enabled();
        services.state.enabled = false;
        let original = services.state;
        let mut policy = WifiPolicy::default();
        policy.reconcile(&mut services, true).unwrap();
        policy.reconcile(&mut services, false).unwrap();
        assert_eq!(services.state, original);
        assert!(services.writes.is_empty());
    }

    #[test]
    fn a_manual_change_retires_ownership_without_fighting_the_user() {
        let mut services = FakeServices::enabled();
        let mut policy = WifiPolicy::default();
        policy.reconcile(&mut services, true).unwrap();
        services.state.wireless_enabled = true;
        policy.reconcile(&mut services, true).unwrap();
        assert!(!policy.owns_wifi());
        policy.reconcile(&mut services, true).unwrap();
        policy.reconcile(&mut services, false).unwrap();
        assert_eq!(services.writes.len(), 2);
        assert!(services.state.wireless_enabled);
        assert!(!services.state.enabled);
    }

    #[test]
    fn a_partial_setter_failure_restores_instead_of_losing_ownership() {
        let mut services = FakeServices::enabled();
        let original = services.state;
        services.fail_once = Some(("wirelessEnable", false));
        let mut policy = WifiPolicy::default();
        assert!(policy.reconcile(&mut services, true).is_err());
        assert_eq!(services.state, original);
        assert!(!policy.owns_wifi());
        let writes = services.writes.len();
        policy.reconcile(&mut services, true).unwrap();
        assert_eq!(services.writes.len(), writes);
    }

    #[test]
    fn unavailable_cleanup_keeps_ownership_for_a_later_retry() {
        let mut services = FakeServices::enabled();
        let original = services.state;
        let mut policy = WifiPolicy::default();
        policy.reconcile(&mut services, true).unwrap();
        services.unreadable = true;
        assert!(policy.reconcile(&mut services, false).is_err());
        assert!(policy.owns_wifi());
        services.unreadable = false;
        policy.reconcile(&mut services, false).unwrap();
        assert_eq!(services.state, original);
    }

    #[test]
    fn a_setter_failure_before_the_second_write_restores_the_first_write() {
        let mut services = FakeServices::enabled();
        let original = services.state;
        services.fail_once = Some(("wirelessEnable", false));
        services.fail_before_write = true;
        let mut policy = WifiPolicy::default();
        assert!(policy.reconcile(&mut services, true).is_err());
        assert_eq!(services.state, original);
        assert!(!policy.owns_wifi());
    }

    #[test]
    fn a_restore_failure_before_a_write_keeps_the_confirmed_owned_state() {
        let mut services = FakeServices::enabled();
        let original = services.state;
        let mut policy = WifiPolicy::default();
        policy.reconcile(&mut services, true).unwrap();
        services.fail_once = Some(("wirelessEnable", true));
        services.fail_before_write = true;
        assert!(policy.reconcile(&mut services, false).is_err());
        assert!(policy.owns_wifi());
        policy.reconcile(&mut services, false).unwrap();
        assert_eq!(services.state, original);
        assert!(!policy.owns_wifi());
    }

    #[test]
    fn connection_observations_do_not_invent_a_false_value_for_unknown_states() {
        assert_eq!(connected_state("CONNECTED\n"), Some(true));
        assert_eq!(connected_state("connecting"), Some(false));
        assert_eq!(connected_state(""), None);
        assert_eq!(connected_state("unexpected-firmware-state"), None);
    }

    #[test]
    fn a_request_revoked_during_observation_never_disables_wifi() {
        let requested = Arc::new(AtomicBool::new(true));
        let mut services = FakeServices::enabled();
        let original = services.state;
        services.revoke_on_read = Some(requested.clone());
        let mut policy = WifiPolicy::default();
        policy
            .reconcile_while(&mut services, true, || requested.load(Ordering::Acquire))
            .unwrap();
        assert_eq!(services.state, original);
        assert!(services.writes.is_empty());
        assert!(!policy.owns_wifi());
    }

    #[test]
    fn a_request_revoked_between_setters_restores_before_returning() {
        let requested = Arc::new(AtomicBool::new(true));
        let mut services = FakeServices::enabled();
        let original = services.state;
        services.revoke_on_write = Some(requested.clone());
        let mut policy = WifiPolicy::default();
        policy
            .reconcile_while(&mut services, true, || requested.load(Ordering::Acquire))
            .unwrap();
        assert_eq!(services.state, original);
        assert!(!policy.owns_wifi());
        assert_eq!(services.writes.len(), 2);
        assert!(
            services
                .writes
                .iter()
                .all(|(_, property, _)| property == "enable")
        );
    }

    #[test]
    fn user_network_demand_releases_policy_and_requires_a_fresh_connected_observation() {
        let shared = Arc::new(Shared::default());
        shared.disable_wifi.store(true, Ordering::Release);
        let (sender, _receiver) = mpsc::sync_channel(1);
        let worker = DevicePolicyWorker {
            shared: shared.clone(),
            sender,
            thread: None,
        };
        *shared.observation.lock().unwrap() = Some((
            Instant::now() - Duration::from_secs(1),
            DevicePolicyObservation {
                wifi_connected: Some(true),
                wifi_enabled: Some(true),
                ..DevicePolicyObservation::default()
            },
        ));
        worker.request_network_for_user();
        assert!(!shared.disable_wifi.load(Ordering::Acquire));
        worker.request_wifi_disabled(true);
        assert!(!shared.disable_wifi.load(Ordering::Acquire));
        assert!(!worker.user_network_ready());
        assert!(worker.network_policy_blocked());
        shared.restored.store(true, Ordering::Release);
        assert!(!worker.user_network_ready());
        *shared.observation.lock().unwrap() = Some((
            Instant::now(),
            DevicePolicyObservation {
                wifi_connected: Some(true),
                wifi_enabled: Some(true),
                ..DevicePolicyObservation::default()
            },
        ));
        assert!(worker.user_network_ready());
        assert!(!worker.network_policy_blocked());
        worker.finish_user_network_request();
        worker.request_wifi_disabled(true);
        assert!(shared.disable_wifi.load(Ordering::Acquire));
    }

    #[test]
    fn a_user_network_request_does_not_enable_a_radio_that_the_policy_did_not_disable() {
        let shared = Arc::new(Shared::default());
        shared.restored.store(true, Ordering::Release);
        let (sender, _receiver) = mpsc::sync_channel(1);
        let worker = DevicePolicyWorker {
            shared: shared.clone(),
            sender,
            thread: None,
        };
        worker.request_network_for_user();
        assert!(worker.user_network_ready());
        assert!(!worker.network_policy_blocked());
        assert!(!shared.disable_wifi.load(Ordering::Acquire));
    }

    #[test]
    fn reconnect_readiness_rejects_an_old_sample_published_after_the_request() {
        let shared = Arc::new(Shared::default());
        shared.disable_wifi.store(true, Ordering::Release);
        let (sender, _receiver) = mpsc::sync_channel(1);
        let worker = DevicePolicyWorker {
            shared: shared.clone(),
            sender,
            thread: None,
        };
        let old_sample_started = Instant::now() - Duration::from_secs(1);
        worker.request_network_for_user();
        shared.restored.store(true, Ordering::Release);
        let mut observation = DevicePolicyObservation {
            wifi_connected: Some(true),
            wifi_enabled: Some(true),
            ..DevicePolicyObservation::default()
        };
        *shared.observation.lock().unwrap() = Some((old_sample_started, observation.clone()));
        assert!(!worker.user_network_ready());
        observation.wifi_enabled = Some(false);
        *shared.observation.lock().unwrap() = Some((Instant::now(), observation.clone()));
        assert!(!worker.user_network_ready());
        observation.wifi_enabled = Some(true);
        observation.wifi_owned = true;
        *shared.observation.lock().unwrap() = Some((Instant::now(), observation.clone()));
        assert!(!worker.user_network_ready());
        observation.wifi_owned = false;
        *shared.observation.lock().unwrap() = Some((Instant::now(), observation));
        assert!(worker.user_network_ready());
    }
}
