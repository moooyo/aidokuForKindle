use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::{Duration, Instant};

use anyhow::{Context, Result, bail};
use clap::ValueEnum;
use serde::{Deserialize, Serialize};

use crate::domain::Preferences;

#[cfg(all(feature = "kindle", target_os = "linux"))]
#[path = "platform_activity.rs"]
mod activity;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PointerPhase {
    Press,
    Move,
    Release,
    Cancel,
}

#[derive(Debug, Clone, Copy)]
pub struct PointerInput {
    pub phase: PointerPhase,
    pub position: slint::LogicalPosition,
}

impl PointerInput {
    pub fn from_window_event(event: &slint::platform::WindowEvent) -> Option<Self> {
        use slint::platform::{PointerEventButton, WindowEvent};
        match event {
            WindowEvent::PointerPressed {
                position,
                button: PointerEventButton::Left,
            } => Some(Self {
                phase: PointerPhase::Press,
                position: *position,
            }),
            WindowEvent::PointerMoved { position } => Some(Self {
                phase: PointerPhase::Move,
                position: *position,
            }),
            WindowEvent::PointerReleased {
                position,
                button: PointerEventButton::Left,
            } => Some(Self {
                phase: PointerPhase::Release,
                position: *position,
            }),
            WindowEvent::PointerExited => Some(Self {
                phase: PointerPhase::Cancel,
                position: slint::LogicalPosition::new(0.0, 0.0),
            }),
            _ => None,
        }
    }
}

/// A UI-thread observer shared by desktop, native evdev, and scripted input.
#[derive(Clone)]
pub struct PointerObserver {
    callback: Rc<RefCell<Box<dyn FnMut(PointerInput) -> bool>>>,
    enabled: Rc<dyn Fn() -> bool>,
}

impl PointerObserver {
    pub fn new(
        callback: impl FnMut(PointerInput) -> bool + 'static,
        enabled: impl Fn() -> bool + 'static,
    ) -> Self {
        Self {
            callback: Rc::new(RefCell::new(Box::new(callback))),
            enabled: Rc::new(enabled),
        }
    }

    pub fn observe(&self, input: PointerInput) -> bool {
        (self.callback.borrow_mut())(input)
    }

    pub fn enabled(&self) -> bool {
        (self.enabled)()
    }
}

/// A design profile; hardware support requires separate model/firmware evidence.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, ValueEnum, Serialize, Deserialize)]
pub enum DeviceProfile {
    #[default]
    #[value(name = "pw5", alias = "paperwhite5")]
    Pw5,
    #[value(name = "pw6", alias = "paperwhite6")]
    Pw6,
    #[value(name = "scribe", alias = "scribe1", alias = "scribe2022")]
    Scribe,
    #[value(name = "scribe2", alias = "scribe2024")]
    Scribe2,
    #[value(name = "scribe3", alias = "scribe2025")]
    Scribe3,
    #[value(name = "scribe3-nofrontlight")]
    Scribe3NoFrontlight,
}

impl DeviceProfile {
    pub const fn id(self) -> &'static str {
        match self {
            Self::Pw5 => "pw5",
            Self::Pw6 => "pw6",
            Self::Scribe => "scribe",
            Self::Scribe2 => "scribe2",
            Self::Scribe3 => "scribe3",
            Self::Scribe3NoFrontlight => "scribe3-nofrontlight",
        }
    }

    pub const fn name(self) -> &'static str {
        match self {
            Self::Pw5 => "Kindle Paperwhite 5",
            Self::Pw6 => "Kindle Paperwhite 6",
            Self::Scribe => "Kindle Scribe 1",
            Self::Scribe2 => "Kindle Scribe 2",
            Self::Scribe3 => "Kindle Scribe 3",
            Self::Scribe3NoFrontlight => "Kindle Scribe 3 (no front light)",
        }
    }

    pub const fn logical_size(self, landscape: bool) -> (u32, u32) {
        let portrait = match self {
            Self::Pw5 => (618, 824),
            Self::Pw6 => (632, 840),
            Self::Scribe | Self::Scribe2 => (930, 1240),
            Self::Scribe3 | Self::Scribe3NoFrontlight => (990, 1320),
        };
        if landscape {
            (portrait.1, portrait.0)
        } else {
            portrait
        }
    }

    pub const fn native_size(self) -> (u32, u32) {
        let (width, height) = self.logical_size(false);
        (width * 2, height * 2)
    }

    pub const fn is_scribe(self) -> bool {
        !matches!(self, Self::Pw5 | Self::Pw6)
    }

    pub const fn has_frontlight(self) -> bool {
        !matches!(self, Self::Scribe3NoFrontlight)
    }

    pub const fn has_warmth(self) -> bool {
        self.has_frontlight()
    }

    pub const fn has_orientation(self) -> bool {
        self.is_scribe()
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RefreshKind {
    #[default]
    Ui,
    Partial,
    Full,
    FlashUi,
    Fast,
    A2,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeviceStatus {
    pub time: String,
    pub battery: u8,
    pub wifi: bool,
    pub charging: bool,
    pub brightness: u8,
    pub warmth: u8,
    pub free_bytes: u64,
}

#[derive(Debug, Clone, Copy, Default, Serialize)]
pub struct SleepStatus {
    pub requested: bool,
    pub render_paused: bool,
    pub input_released: bool,
    pub parked: bool,
    pub activity_paused: bool,
    pub pause_count: u64,
    pub resume_count: u64,
    pub frame_write_count: u64,
    pub resume_full_frame_count: u64,
    pub input_generation: u64,
}

impl Default for DeviceStatus {
    fn default() -> Self {
        Self {
            time: "18:47".into(),
            battery: 86,
            wifi: true,
            charging: false,
            brightness: 12,
            warmth: 8,
            free_bytes: 9_400_000_000,
        }
    }
}

pub struct Platform {
    profile: DeviceProfile,
    kindle: bool,
    native_geometry: (u32, u32),
    display_rotation_degrees: Cell<u16>,
    configured_logical_size: Cell<Option<(u32, u32)>>,
    preferences: RefCell<Preferences>,
    partial_count: Cell<u32>,
    last_refresh: Cell<RefreshKind>,
    refresh_count: Cell<u64>,
    status_cache: RefCell<Option<(Option<Instant>, DeviceStatus)>>,
    applied_lighting: Cell<Option<(u8, u8)>>,
    sleep_requested: Cell<bool>,
    pointer_observer: RefCell<Option<PointerObserver>>,
    #[cfg(all(feature = "kindle", target_os = "linux"))]
    backend: RefCell<Option<slint_backend_kindle::KindleBackend>>,
    #[cfg(all(feature = "kindle", target_os = "linux"))]
    activity: RefCell<Option<activity::ActivityWorker>>,
    #[cfg(all(feature = "kindle", target_os = "linux"))]
    native_presentation: RefCell<Option<crate::native_presentation::NativePresentationContext>>,
}

impl Platform {
    pub fn new(profile: DeviceProfile, kindle: bool) -> Result<Self> {
        #[cfg(not(all(feature = "kindle", target_os = "linux")))]
        if kindle {
            bail!("The native Kindle backend is unavailable in this build");
        }
        let native_geometry = if kindle {
            native_geometry()?
        } else {
            profile.native_size()
        };
        Ok(Self {
            profile,
            kindle,
            native_geometry,
            display_rotation_degrees: Cell::new(0),
            configured_logical_size: Cell::new(None),
            preferences: RefCell::new(Preferences::default()),
            partial_count: Cell::new(0),
            last_refresh: Cell::new(RefreshKind::Ui),
            refresh_count: Cell::new(0),
            status_cache: RefCell::new(None),
            applied_lighting: Cell::new(None),
            sleep_requested: Cell::new(false),
            pointer_observer: RefCell::new(None),
            #[cfg(all(feature = "kindle", target_os = "linux"))]
            backend: RefCell::new(None),
            #[cfg(all(feature = "kindle", target_os = "linux"))]
            activity: RefCell::new(None),
            #[cfg(all(feature = "kindle", target_os = "linux"))]
            native_presentation: RefCell::new(None),
        })
    }

    pub const fn profile(&self) -> DeviceProfile {
        self.profile
    }

    pub const fn is_kindle(&self) -> bool {
        self.kindle
    }

    pub fn install_control_callback(&self, callback: impl FnMut() + 'static) -> Result<()> {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if self.kindle {
            let backend = self.backend.borrow();
            backend
                .as_ref()
                .context("The native backend is not installed")?
                .set_control_callback(callback);
            return Ok(());
        }
        let _ = callback;
        Ok(())
    }

    pub fn signal_wakeup_fd(&self) -> Option<i32> {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if self.kindle {
            return self
                .backend
                .borrow()
                .as_ref()
                .map(|backend| backend.lifecycle_handle().signal_wakeup_fd());
        }
        None
    }

    pub fn begin_suspend(&self) -> Result<()> {
        self.sleep_requested.set(true);
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if self.kindle {
            if let Some(worker) = self.activity.borrow().as_ref() {
                worker.pause();
            }
            self.backend
                .borrow()
                .as_ref()
                .context("The native backend is not installed")?
                .lifecycle_handle()
                .begin_pause()?;
        }
        Ok(())
    }

    pub fn park_for_suspend(&self) -> Result<()> {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if self.kindle {
            if self
                .activity
                .borrow()
                .as_ref()
                .is_some_and(|worker| !worker.paused_ready())
            {
                bail!("The idle notification worker has not acknowledged suspend yet");
            }
            self.backend
                .borrow()
                .as_ref()
                .context("The native backend is not installed")?
                .lifecycle_handle()
                .park()?;
        }
        Ok(())
    }

    pub fn resume_after_sleep(&self) -> Result<()> {
        self.sleep_requested.set(false);
        self.invalidate_status_cache();
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if self.kindle {
            if let Some(worker) = self.activity.borrow().as_ref() {
                worker.resume();
            }
            self.backend
                .borrow()
                .as_ref()
                .context("The native backend is not installed")?
                .lifecycle_handle()
                .resume()?;
        }
        Ok(())
    }

    /// Stage fresh portrait output before the retained native frame resumes.
    pub fn stage_native_portrait_resume(&self, epoch: u64) -> Result<()> {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if self.kindle {
            let presentation = self.native_presentation.borrow();
            let Some(context) = presentation.as_ref() else {
                return Ok(());
            };
            let status = self.sleep_status();
            if !status.render_paused || !status.input_released {
                bail!("Native portrait resume requires paused display and input ownership");
            }
            let observed = context
                .observe_resume(epoch)
                .context("Match the current portrait permission to fresh native metadata")?;
            if observed.automatic {
                self.backend
                    .borrow()
                    .as_ref()
                    .context("The native backend is not installed")?
                    .lifecycle_handle()
                    .set_native_framebuffer_portrait_calibration(
                        observed.holding,
                        observed.scanout,
                        observed.framebuffer,
                    )?;
            }
        }
        let _ = epoch;
        Ok(())
    }

    pub fn sleep_status(&self) -> SleepStatus {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if self.kindle
            && let Some(backend) = self.backend.borrow().as_ref()
        {
            let status = backend.lifecycle_handle().status();
            return SleepStatus {
                requested: self.sleep_requested.get(),
                render_paused: status.render_paused,
                input_released: status.input_released,
                parked: status.parked,
                activity_paused: self
                    .activity
                    .borrow()
                    .as_ref()
                    .is_none_or(|worker| worker.paused_ready()),
                pause_count: status.pause_count,
                resume_count: status.resume_count,
                frame_write_count: status.frame_write_count,
                resume_full_frame_count: status.resume_full_frame_count,
                input_generation: status.input_generation,
            };
        }
        SleepStatus {
            requested: self.sleep_requested.get(),
            ..SleepStatus::default()
        }
    }

    pub fn runtime_logical_size(&self, landscape: bool) -> (u32, u32) {
        if !self.kindle {
            return self.profile.logical_size(landscape);
        }
        let (width, height) = self.native_geometry;
        let short = width.min(height) / 2;
        let long = width.max(height) / 2;
        if landscape {
            (long, short)
        } else {
            (short, long)
        }
    }

    /// Install before creating a window or starting background threads.
    pub fn install_backend(&self, font: &[u8]) -> Result<()> {
        if self.kindle {
            #[cfg(all(feature = "kindle", target_os = "linux"))]
            {
                let presentation =
                    crate::native_presentation::NativePresentationContext::from_environment(
                        self.profile,
                    )
                    .context("Require the protected pre-takeover portrait calibration")?;
                let observed = presentation
                    .as_ref()
                    .map(|context| context.observe_startup())
                    .transpose()
                    .context("Match the startup portrait calibration before native I/O")?;
                let backend = slint_backend_kindle::install(font)
                    .context("Install the native Kindle display and input backend")?;
                if let Some(observed) = observed
                    && observed.automatic
                {
                    backend
                        .lifecycle_handle()
                        .set_native_framebuffer_portrait_calibration(
                            observed.holding,
                            observed.scanout,
                            observed.framebuffer,
                        )?;
                }
                self.native_presentation.replace(presentation);
                *self.backend.borrow_mut() = Some(backend);
                // Backend installation mutates its default-font environment.
                // Start this worker only after that single-threaded step.
                *self.activity.borrow_mut() = Some(activity::ActivityWorker::start()?);
                return Ok(());
            }
            #[cfg(not(all(feature = "kindle", target_os = "linux")))]
            bail!("The native Kindle backend is unavailable in this build");
        }
        let _ = font;
        #[cfg(feature = "desktop")]
        {
            slint::BackendSelector::new()
                .backend_name("winit".into())
                .renderer_name("software".into())
                .select()
                .context("Select the desktop software renderer")?;
            Ok(())
        }
        #[cfg(not(feature = "desktop"))]
        bail!("The desktop backend is unavailable in this build")
    }

    pub fn configure_window(&self, window: &slint::Window, landscape: bool) -> Result<()> {
        self.configure_window_rotation(window, if landscape { 90 } else { 0 })
    }

    pub fn display_rotation_degrees(&self) -> u16 {
        self.display_rotation_degrees.get()
    }

    pub fn configure_window_rotation(&self, window: &slint::Window, degrees: u16) -> Result<()> {
        let logical_size = self.runtime_logical_size(rotation_is_landscape(degrees)?);
        let (width, height) = logical_size;
        let physical_scale = if self.kindle { 2 } else { 1 };
        let scale = physical_scale as f32;
        let physical_size =
            slint::PhysicalSize::new(width * physical_scale, height * physical_scale);
        let geometry_changed = self.configured_logical_size.get() != Some(logical_size)
            || window.size() != physical_size
            || window.scale_factor() != scale;
        let rotation_changed = self.display_rotation_degrees.get() != degrees;
        if !geometry_changed && !rotation_changed {
            return Ok(());
        }
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if self.kindle && self.backend.borrow().is_none() {
            bail!("The native display backend is not installed");
        }
        if geometry_changed {
            window
                .try_dispatch_event(slint::platform::WindowEvent::ScaleFactorChanged {
                    scale_factor: scale,
                })
                .context("Set the application display scale")?;
            window.set_size(physical_size);
            window
                .try_dispatch_event(slint::platform::WindowEvent::Resized {
                    size: slint::LogicalSize::new(width as f32, height as f32),
                })
                .context("Set the application viewport")?;
        }
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if let Some(backend) = self.backend.borrow().as_ref() {
            backend.set_rotation(u32::from(degrees));
        }
        if rotation_changed {
            // Equal landscape dimensions still require a new physical frame.
            window.request_redraw();
        }
        self.configured_logical_size.set(Some(logical_size));
        self.display_rotation_degrees.set(degrees);
        Ok(())
    }

    pub fn install_input_observer(
        &self,
        window: &slint::Window,
        observer: PointerObserver,
    ) -> Result<()> {
        self.pointer_observer.replace(Some(observer.clone()));
        if self.kindle {
            #[cfg(all(feature = "kindle", target_os = "linux"))]
            {
                let backend = self.backend.borrow();
                let backend = backend
                    .as_ref()
                    .context("The native input backend is not installed")?;
                backend.set_input_observer(move |event| {
                    PointerInput::from_window_event(event)
                        .is_some_and(|input| observer.observe(input))
                });
                return Ok(());
            }
            #[cfg(not(all(feature = "kindle", target_os = "linux")))]
            bail!("The native input observer is unavailable in this build");
        }
        #[cfg(feature = "desktop")]
        {
            install_desktop_input_observer(window, observer);
            Ok(())
        }
        #[cfg(not(feature = "desktop"))]
        {
            let _ = (window, observer);
            bail!("The desktop input observer is unavailable in this build")
        }
    }

    /// Cancel shared pagination and widget input before checkpointing the App.
    pub fn cancel_pending_pointer(&self, window: &slint::Window) -> Result<()> {
        self.cancel_pointer_observer();
        window
            .try_dispatch_event(slint::platform::WindowEvent::PointerExited)
            .context("Cancel pre-sleep widget input")
    }

    fn cancel_pointer_observer(&self) {
        let observer = self.pointer_observer.borrow().clone();
        if let Some(observer) = observer {
            observer.observe(PointerInput {
                phase: PointerPhase::Cancel,
                position: slint::LogicalPosition::new(0.0, 0.0),
            });
        }
    }

    pub fn refresh(&self, requested: RefreshKind) {
        let every = self.preferences.borrow().refresh_every;
        let (kind, count) = next_refresh(requested, self.partial_count.get(), every);
        self.partial_count.set(count);
        self.last_refresh.set(kind);
        self.refresh_count.set(self.refresh_count.get() + 1);
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if let Some(backend) = self.backend.borrow().as_ref() {
            use slint_backend_kindle::{RefreshArea, RefreshRequest, RefreshWaveform};
            let request = match kind {
                RefreshKind::Full => RefreshRequest::full(),
                RefreshKind::FlashUi => RefreshRequest {
                    waveform: RefreshWaveform::Gc16,
                    flashing: true,
                    area: RefreshArea::Dirty,
                },
                RefreshKind::Fast => RefreshRequest {
                    waveform: RefreshWaveform::Du,
                    flashing: false,
                    area: RefreshArea::Dirty,
                },
                RefreshKind::A2 => RefreshRequest {
                    waveform: RefreshWaveform::A2,
                    flashing: false,
                    area: RefreshArea::Dirty,
                },
                RefreshKind::Ui | RefreshKind::Partial => RefreshRequest {
                    // AUTO lets the detected driver select its safe partial waveform.
                    waveform: RefreshWaveform::Auto,
                    flashing: false,
                    area: RefreshArea::Dirty,
                },
            };
            if let Err(error) = backend.refresh_handle().request(request) {
                eprintln!("aidoku: display refresh request failed: {error}");
            }
        }
    }

    pub fn last_refresh(&self) -> RefreshKind {
        self.last_refresh.get()
    }

    pub fn refresh_count(&self) -> u64 {
        self.refresh_count.get()
    }

    pub fn apply_preferences(&self, preferences: &Preferences) -> Result<()> {
        *self.preferences.borrow_mut() = preferences.clone();
        self.invalidate_status_cache();
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if let Some(worker) = self.activity.borrow().as_ref() {
            worker.set_keep_download_awake(preferences.keep_awake);
        }
        if !self.kindle || self.sleep_requested.get() {
            return Ok(());
        }
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if let Some(backend) = self.backend.borrow().as_ref() {
            // Hardware inversion is not exposed by the reviewed backend. Its
            // software conversion is an explicit fallback, not a hardware claim.
            backend
                .render_handle()
                .set_dark_mode(preferences.night_mode)
                .context("Apply the native software night-mode fallback")?;
        }
        let brightness = preferences.brightness.min(24);
        let warmth = preferences.warmth.min(24);
        let previous = self.applied_lighting.get();
        if self.profile.has_frontlight() && previous.map(|value| value.0) != Some(brightness) {
            set_power_property("flIntensity", brightness)?;
        }
        if self.profile.has_warmth() && previous.map(|value| value.1) != Some(warmth) {
            set_power_property("currentAmberLevel", warmth)?;
        }
        self.applied_lighting.set(Some((brightness, warmth)));
        Ok(())
    }

    /// Input hot path: only atomics and a bounded nonblocking notification.
    pub fn note_user_activity(&self) {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if !self.sleep_requested.get()
            && let Some(worker) = self.activity.borrow().as_ref()
        {
            worker.note_user();
        }
    }

    pub fn set_download_activity(&self, active: bool) {
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        if let Some(worker) = self.activity.borrow().as_ref() {
            worker.set_download_active(active);
        }
        #[cfg(not(all(feature = "kindle", target_os = "linux")))]
        let _ = active;
    }

    pub fn status(&self) -> DeviceStatus {
        let preferences = self.preferences.borrow();
        if self.sleep_requested.get() {
            return self.cached_status();
        }
        if !self.kindle {
            return self.observe_status_with(|| DeviceStatus {
                brightness: preferences.brightness,
                warmth: preferences.warmth,
                ..DeviceStatus::default()
            });
        }
        self.observe_status_with(|| native_status(&preferences))
    }

    fn observe_status_with(&self, refresh: impl FnOnce() -> DeviceStatus) -> DeviceStatus {
        if let Some((Some(instant), status)) = self.status_cache.borrow().as_ref()
            && instant.elapsed() < Duration::from_secs(30)
        {
            return status.clone();
        }
        let status = refresh();
        *self.status_cache.borrow_mut() = Some((Some(Instant::now()), status.clone()));
        status
    }

    /// Return the last observation without device I/O or keep-awake writes.
    pub fn cached_status(&self) -> DeviceStatus {
        self.status_cache
            .borrow()
            .as_ref()
            .map(|(_, status)| status.clone())
            .unwrap_or_else(|| {
                let preferences = self.preferences.borrow();
                DeviceStatus {
                    brightness: preferences.brightness,
                    warmth: preferences.warmth,
                    ..DeviceStatus::default()
                }
            })
    }

    pub fn invalidate_status_cache(&self) {
        if let Some((observed_at, _)) = self.status_cache.borrow_mut().as_mut() {
            // Stale observations remain truthful cached values. Only the next
            // explicit status() observation may replace them with fresh data.
            *observed_at = None;
        }
    }

    /// Publish an explicit UI exit before checkpointing or worker shutdown waits.
    pub fn announce_exit_intent(&self) -> Result<()> {
        let (Some(directory), Some(nonce)) = (
            std::env::var_os("AIDOKU_LAUNCHER_DIR"),
            std::env::var_os("AIDOKU_LAUNCH_NONCE"),
        ) else {
            return Ok(());
        };
        let directory = std::path::PathBuf::from(directory);
        let nonce = nonce.to_string_lossy();
        let pid = std::process::id();
        let epoch = std::fs::read_to_string(directory.join("sleep-request"))
            .ok()
            .and_then(|line| {
                let values = line.split_whitespace().collect::<Vec<_>>();
                (values.len() == 3
                    && values[0].parse::<u32>().ok() == Some(pid)
                    && values[1] == nonce)
                    .then(|| values[2].parse::<u64>().ok())
                    .flatten()
            })
            .unwrap_or(0);
        let pending = directory.join("ordinary-exit.pending");
        std::fs::write(&pending, format!("{pid} {nonce} {epoch}\n"))?;
        std::fs::rename(pending, directory.join("ordinary-exit"))?;
        Ok(())
    }

    /// Abort only this process's established outbound TCP after source admission
    /// is suspended. Listener ports protect accepted Companion connections.
    pub fn abort_outbound_network_for_suspend(&self) -> Result<usize> {
        if !self.kindle {
            return Ok(0);
        }
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        {
            return abort_own_outbound_tcp();
        }
        #[cfg(not(all(feature = "kindle", target_os = "linux")))]
        Ok(0)
    }
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn abort_own_outbound_tcp() -> Result<usize> {
    let descriptors: Vec<i32> = std::fs::read_dir("/proc/self/fd")
        .context("Inspect this process's network descriptors")?
        .filter_map(|entry| entry.ok()?.file_name().to_str()?.parse().ok())
        .collect();
    abort_tcp_descriptors(descriptors)
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn abort_tcp_descriptors(descriptors: Vec<i32>) -> Result<usize> {
    use std::{
        collections::HashSet,
        mem,
        os::fd::{AsRawFd, FromRawFd, OwnedFd},
    };

    fn integer(fd: i32, option: i32) -> Option<i32> {
        let mut value = 0i32;
        let mut length = mem::size_of::<i32>() as libc::socklen_t;
        // SAFETY: getsockopt writes this correctly sized integer buffer only.
        let result = unsafe {
            libc::getsockopt(
                fd,
                libc::SOL_SOCKET,
                option,
                &mut value as *mut _ as *mut libc::c_void,
                &mut length,
            )
        };
        (result == 0).then_some(value)
    }

    fn endpoint(fd: i32, peer: bool) -> Option<(i32, u16)> {
        // SAFETY: zero is a valid initial sockaddr_storage representation.
        let mut address: libc::sockaddr_storage = unsafe { mem::zeroed() };
        let mut length = mem::size_of_val(&address) as libc::socklen_t;
        let pointer = &mut address as *mut _ as *mut libc::sockaddr;
        // SAFETY: both calls receive the allocated sockaddr_storage buffer and
        // its true capacity; they retain neither pointer.
        let result = unsafe {
            if peer {
                libc::getpeername(fd, pointer, &mut length)
            } else {
                libc::getsockname(fd, pointer, &mut length)
            }
        };
        if result != 0 {
            return None;
        }
        match i32::from(address.ss_family) {
            libc::AF_INET if length as usize >= mem::size_of::<libc::sockaddr_in>() => {
                // SAFETY: the family, length, and storage alignment match sockaddr_in.
                let value = unsafe { &*(&address as *const _ as *const libc::sockaddr_in) };
                Some((libc::AF_INET, u16::from_be(value.sin_port)))
            }
            libc::AF_INET6 if length as usize >= mem::size_of::<libc::sockaddr_in6>() => {
                // SAFETY: the family, length, and storage alignment match sockaddr_in6.
                let value = unsafe { &*(&address as *const _ as *const libc::sockaddr_in6) };
                Some((libc::AF_INET6, u16::from_be(value.sin6_port)))
            }
            _ => None,
        }
    }

    // Snapshot the descriptor numbers first so our own temporary duplicates
    // cannot be rediscovered by a live /proc iterator.
    let mut sockets = Vec::new();
    let mut identities = HashSet::new();
    for descriptor in descriptors {
        // SAFETY: fcntl duplicates a live descriptor or returns an error. The
        // duplicate pins the underlying socket even if another thread closes
        // or reuses its original fd before inspection/shutdown completes.
        let duplicate = unsafe { libc::fcntl(descriptor, libc::F_DUPFD_CLOEXEC, 0) };
        if duplicate < 0 {
            continue;
        }
        // SAFETY: a successful F_DUPFD_CLOEXEC returned a newly owned fd.
        let duplicate = unsafe { OwnedFd::from_raw_fd(duplicate) };
        let fd = duplicate.as_raw_fd();
        if integer(fd, libc::SO_TYPE) != Some(libc::SOCK_STREAM)
            || integer(fd, libc::SO_PROTOCOL) != Some(libc::IPPROTO_TCP)
        {
            continue;
        }
        // A closed original (including /proc's own directory fd) may have been
        // reused by one of our duplicates. Pin and count each socket only once.
        let mut information = mem::MaybeUninit::<libc::stat>::uninit();
        // SAFETY: fstat fills the provided stat object and retains no pointer.
        if unsafe { libc::fstat(fd, information.as_mut_ptr()) } != 0 {
            continue;
        }
        // SAFETY: successful fstat initialized all fields.
        let information = unsafe { information.assume_init() };
        if !identities.insert((information.st_dev as u64, information.st_ino as u64)) {
            continue;
        }
        let Some(local) = endpoint(fd, false) else {
            continue;
        };
        let Some(listening) = integer(fd, libc::SO_ACCEPTCONN) else {
            continue;
        };
        sockets.push((duplicate, local, listening != 0));
    }
    let listeners: HashSet<_> = sockets
        .iter()
        .filter(|(_, _, listening)| *listening)
        .map(|(_, local, _)| *local)
        .collect();
    let mut aborted = 0;
    for (socket, local, listening) in &sockets {
        if *listening || listeners.contains(local) || endpoint(socket.as_raw_fd(), true).is_none() {
            continue;
        }
        // SAFETY: this is our stable duplicate of a verified established TCP
        // socket, never a listener, UDS, accepted local service, or another pid.
        if unsafe { libc::shutdown(socket.as_raw_fd(), libc::SHUT_RDWR) } == 0 {
            aborted += 1;
        }
    }
    Ok(aborted)
}

/// A background-safe observation with no keep-awake or display side effects.
pub fn probe_network_ready(native: bool) -> bool {
    if !native {
        return true;
    }
    #[cfg(all(feature = "kindle", target_os = "linux"))]
    {
        return command("lipc-get-prop", &["com.lab126.wifid", "cmState"])
            .map(|value| value.trim().eq_ignore_ascii_case("connected"))
            .unwrap_or(false);
    }
    #[cfg(not(all(feature = "kindle", target_os = "linux")))]
    false
}

#[cfg(feature = "desktop")]
fn install_desktop_input_observer(window: &slint::Window, observer: PointerObserver) {
    use slint::platform::{PointerEventButton, WindowEvent};
    use slint::winit_030::{EventResult, WinitWindowAccessor, winit};
    use std::collections::HashSet;
    use winit::event::{ElementState, MouseButton, TouchPhase, WindowEvent as NativeEvent};

    let mut cursor = slint::LogicalPosition::new(0.0, 0.0);
    let mut mouse_manual = false;
    let mut touches = HashSet::new();
    let mut touch_manual = false;
    let mut primary_touch = None;
    window.on_winit_window_event(move |window, event| {
        match event {
            NativeEvent::CursorMoved { position, .. } => {
                let position = position.to_logical::<f64>(window.scale_factor() as f64);
                cursor = slint::LogicalPosition::new(position.x as f32, position.y as f32);
                if touch_manual && !touches.is_empty() {
                    return EventResult::PreventDefault;
                }
                if mouse_manual {
                    forward_observed_pointer(
                        window,
                        WindowEvent::PointerMoved { position: cursor },
                        &observer,
                    );
                    return EventResult::PreventDefault;
                }
            }
            NativeEvent::MouseInput {
                state,
                button: MouseButton::Left,
                ..
            } => {
                if touch_manual && !touches.is_empty() {
                    return EventResult::PreventDefault;
                }
                match state {
                    ElementState::Pressed => {
                        mouse_manual = observer.enabled();
                        if mouse_manual {
                            forward_observed_pointer(
                                window,
                                WindowEvent::PointerPressed {
                                    position: cursor,
                                    button: PointerEventButton::Left,
                                },
                                &observer,
                            );
                            return EventResult::PreventDefault;
                        }
                    }
                    ElementState::Released if mouse_manual => {
                        mouse_manual = false;
                        forward_observed_pointer(
                            window,
                            WindowEvent::PointerReleased {
                                position: cursor,
                                button: PointerEventButton::Left,
                            },
                            &observer,
                        );
                        return EventResult::PreventDefault;
                    }
                    _ => {}
                }
            }
            NativeEvent::Touch(touch) => {
                if matches!(touch.phase, TouchPhase::Started) && touches.is_empty() {
                    touch_manual = observer.enabled();
                    primary_touch = touch_manual.then_some(touch.id);
                    if touch_manual && mouse_manual {
                        mouse_manual = false;
                        cancel_observed_pointer(window, &observer);
                    }
                }
                if matches!(touch.phase, TouchPhase::Started) {
                    touches.insert(touch.id);
                }
                let manual = touch_manual;
                if manual && primary_touch == Some(touch.id) {
                    let position = touch
                        .location
                        .to_logical::<f64>(window.scale_factor() as f64);
                    let position =
                        slint::LogicalPosition::new(position.x as f32, position.y as f32);
                    match touch.phase {
                        TouchPhase::Started => forward_observed_pointer(
                            window,
                            WindowEvent::PointerPressed {
                                position,
                                button: PointerEventButton::Left,
                            },
                            &observer,
                        ),
                        TouchPhase::Moved => forward_observed_pointer(
                            window,
                            WindowEvent::PointerMoved { position },
                            &observer,
                        ),
                        TouchPhase::Ended => {
                            forward_observed_pointer(
                                window,
                                WindowEvent::PointerReleased {
                                    position,
                                    button: PointerEventButton::Left,
                                },
                                &observer,
                            );
                            primary_touch = None;
                        }
                        TouchPhase::Cancelled => {
                            cancel_observed_pointer(window, &observer);
                            primary_touch = None;
                        }
                    }
                }
                if matches!(touch.phase, TouchPhase::Ended | TouchPhase::Cancelled) {
                    touches.remove(&touch.id);
                    if touches.is_empty() {
                        touch_manual = false;
                        primary_touch = None;
                    }
                }
                if manual {
                    // Own the complete sequence, including ordinary taps. If
                    // Started reached Slint but Ended were consumed, its Winit
                    // multi-touch tracker would retain a stale active contact.
                    return EventResult::PreventDefault;
                }
            }
            NativeEvent::CursorLeft { .. } => {
                cancel_observed_pointer(window, &observer);
                mouse_manual = false;
            }
            NativeEvent::Focused(false) => {
                cancel_observed_pointer(window, &observer);
                mouse_manual = false;
                touches.clear();
                touch_manual = false;
                primary_touch = None;
            }
            _ => {}
        }
        EventResult::Propagate
    });
}

#[cfg(feature = "desktop")]
fn forward_observed_pointer(
    window: &slint::Window,
    event: slint::platform::WindowEvent,
    observer: &PointerObserver,
) {
    if PointerInput::from_window_event(&event).is_some_and(|input| observer.observe(input)) {
        return;
    }
    if let Err(error) = window.try_dispatch_event(event) {
        eprintln!("aidoku: desktop pointer dispatch failed: {error}");
    }
}

#[cfg(feature = "desktop")]
fn cancel_observed_pointer(window: &slint::Window, observer: &PointerObserver) {
    let _ = observer.observe(PointerInput {
        phase: PointerPhase::Cancel,
        position: slint::LogicalPosition::new(0.0, 0.0),
    });
    // This direct public dispatch bypasses the raw Winit observer.
    if let Err(error) = window.try_dispatch_event(slint::platform::WindowEvent::PointerExited) {
        eprintln!("aidoku: desktop pointer cancellation failed: {error}");
    }
}

fn rotation_is_landscape(degrees: u16) -> Result<bool> {
    match degrees {
        0 | 180 => Ok(false),
        90 | 270 => Ok(true),
        _ => bail!("Unsupported display rotation {degrees}; expected 0, 90, 180, or 270 degrees"),
    }
}

fn next_refresh(requested: RefreshKind, count: u32, every: u32) -> (RefreshKind, u32) {
    match requested {
        RefreshKind::Full => (RefreshKind::Full, 0),
        RefreshKind::Partial if every > 0 && count.saturating_add(1) >= every => {
            (RefreshKind::Full, 0)
        }
        RefreshKind::Partial => (RefreshKind::Partial, count.saturating_add(1)),
        other => (other, count),
    }
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn native_geometry() -> Result<(u32, u32)> {
    use std::{fs::OpenOptions, os::fd::AsRawFd};
    #[repr(C)]
    struct FramebufferInfo {
        words: [u32; 40],
    }
    let file = OpenOptions::new()
        .read(true)
        .open("/dev/fb0")
        .context("Open the native framebuffer")?;
    let mut information = FramebufferInfo { words: [0; 40] };
    // SAFETY: Linux FBIOGET_VSCREENINFO fills this aligned 160-byte structure.
    // It does not retain the pointer or mutate the framebuffer.
    let result = unsafe {
        libc::ioctl(
            file.as_raw_fd(),
            0x4600,
            &mut information as *mut FramebufferInfo,
        )
    };
    if result == -1 {
        return Err(std::io::Error::last_os_error()).context("Read visible display geometry");
    }
    let (width, height) = (information.words[0], information.words[1]);
    if width == 0 || height == 0 || width > 8192 || height > 8192 {
        bail!("The framebuffer reported unsupported visible dimensions: {width} x {height}");
    }
    if information.words[4] != 0 || information.words[5] != 0 {
        bail!("A nonzero native framebuffer panning offset is unsupported");
    }
    Ok((width, height))
}

#[cfg(not(all(feature = "kindle", target_os = "linux")))]
fn native_geometry() -> Result<(u32, u32)> {
    bail!("Native framebuffer geometry is unavailable in this build")
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn command(program: &str, arguments: &[&str]) -> Result<String> {
    bounded_device_command(program, arguments, Duration::from_millis(700), || false)
}

/// Observe stock power state without opening framebuffer, fonts, or metadata.
/// The launcher forwards termination so an in-flight LIPC child is reaped.
pub fn query_native_power_state(timeout: Duration, cancelled: impl Fn() -> bool) -> Result<String> {
    #[cfg(all(feature = "kindle", target_os = "linux"))]
    {
        if timeout < Duration::from_millis(1) || timeout > Duration::from_millis(700) {
            bail!("The native power query budget must be within 1..700 ms");
        }
        bounded_device_command(
            "lipc-get-prop",
            &["-s", "com.lab126.powerd", "state"],
            timeout,
            cancelled,
        )
    }
    #[cfg(not(all(feature = "kindle", target_os = "linux")))]
    {
        let _ = (timeout, cancelled);
        bail!("Native power observation is unavailable in this build")
    }
}

#[cfg(any(all(test, unix), all(feature = "kindle", target_os = "linux")))]
fn bounded_device_command(
    program: &str,
    arguments: &[&str],
    timeout: Duration,
    cancelled: impl Fn() -> bool,
) -> Result<String> {
    use std::os::fd::AsRawFd;
    use std::process::{Command, Stdio};
    let mut child = Command::new(program)
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .with_context(|| format!("Start {program}"))?;
    let mut stdout = child
        .stdout
        .take()
        .context("The device query has no output pipe")?;
    let descriptor = stdout.as_raw_fd();
    // SAFETY: this is our owned pipe. Nonblocking reads keep inherited writer
    // descriptors from extending the device-service deadline after child exit.
    let flags = unsafe { libc::fcntl(descriptor, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(descriptor, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0
    {
        let error = std::io::Error::last_os_error();
        let _ = child.kill();
        let _ = child.wait();
        return Err(error).context("Configure the device query output pipe");
    }
    let deadline = Instant::now() + timeout;
    let mut output = Vec::new();
    loop {
        if let Err(error) =
            read_available_device_output(&mut stdout, &mut output, program, deadline, &cancelled)
        {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
        let status = match child.try_wait() {
            Ok(status) => status,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error).with_context(|| format!("Wait for {program}"));
            }
        };
        if let Some(status) = status {
            // The final bytes may have arrived between the first read and
            // waitpid. Read only what is ready; never await a descendant's EOF.
            read_available_device_output(&mut stdout, &mut output, program, deadline, &cancelled)?;
            if !status.success() {
                bail!("{program} failed with {status}");
            }
            return Ok(String::from_utf8_lossy(&output).trim().to_owned());
        }
        std::thread::sleep(
            deadline
                .saturating_duration_since(Instant::now())
                .min(Duration::from_millis(5)),
        );
    }
}

#[cfg(any(all(test, unix), all(feature = "kindle", target_os = "linux")))]
fn read_available_device_output(
    reader: &mut impl std::io::Read,
    output: &mut Vec<u8>,
    program: &str,
    deadline: Instant,
    cancelled: &impl Fn() -> bool,
) -> Result<()> {
    let mut buffer = [0; 1_024];
    loop {
        if cancelled() {
            bail!("{program} was cancelled before its device-service query completed");
        }
        if Instant::now() >= deadline {
            bail!("{program} exceeded its device-service deadline");
        }
        match reader.read(&mut buffer) {
            Ok(0) => return Ok(()),
            Ok(count) => {
                if output.len() + count > 65_536 {
                    bail!("{program} returned too much device-service output");
                }
                output.extend_from_slice(&buffer[..count]);
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => return Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error).with_context(|| format!("Read {program} output")),
        }
    }
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn set_power_property(property: &str, value: u8) -> Result<()> {
    command(
        "lipc-set-prop",
        &["-i", "com.lab126.powerd", property, &value.to_string()],
    )
    .map(|_| ())
}

#[cfg(not(all(feature = "kindle", target_os = "linux")))]
fn set_power_property(_property: &str, _value: u8) -> Result<()> {
    bail!("Native power services are unavailable in this build")
}

#[cfg(all(feature = "kindle", target_os = "linux"))]
fn native_status(preferences: &Preferences) -> DeviceStatus {
    let property = |name| {
        command("lipc-get-prop", &["com.lab126.powerd", name])
            .ok()
            .and_then(|value| value.parse::<u8>().ok())
    };
    let time = command("date", &["+%H:%M"]).unwrap_or_else(|_| "--:--".into());
    let wifi = command("lipc-get-prop", &["com.lab126.wifid", "cmState"])
        .map(|value| value.trim().eq_ignore_ascii_case("connected"))
        .unwrap_or(false);
    let mut filesystem = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    // SAFETY: the C string is terminated and statvfs writes only to this buffer.
    let free_bytes = if unsafe { libc::statvfs(c"/mnt/us".as_ptr(), filesystem.as_mut_ptr()) } == 0
    {
        // SAFETY: successful statvfs initialized the complete structure.
        let filesystem = unsafe { filesystem.assume_init() };
        filesystem.f_bavail as u64 * filesystem.f_frsize as u64
    } else {
        0
    };
    DeviceStatus {
        time,
        battery: property("battLevel").unwrap_or(0).min(100),
        wifi,
        charging: property("isCharging").unwrap_or(0) != 0,
        brightness: property("flIntensity")
            .unwrap_or(preferences.brightness)
            .min(24),
        warmth: property("currentAmberLevel")
            .unwrap_or(preferences.warmth)
            .min(24),
        free_bytes,
    }
}

#[cfg(not(all(feature = "kindle", target_os = "linux")))]
fn native_status(_preferences: &Preferences) -> DeviceStatus {
    DeviceStatus::default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn display_rotation_accepts_only_the_four_supported_angles() {
        for (degrees, landscape) in [(0, false), (90, true), (180, false), (270, true)] {
            assert_eq!(rotation_is_landscape(degrees).unwrap(), landscape);
        }
        for degrees in [1, 45, 91, 359, 360, 450, u16::MAX] {
            assert!(rotation_is_landscape(degrees).is_err());
        }
    }

    #[test]
    fn reverse_landscape_preserves_logical_geometry_for_every_profile() {
        for profile in DeviceProfile::value_variants() {
            let platform = Platform::new(*profile, false).unwrap();
            let portrait = profile.logical_size(false);
            assert_eq!(platform.display_rotation_degrees(), 0);
            for degrees in [0, 180] {
                assert_eq!(
                    platform.runtime_logical_size(rotation_is_landscape(degrees).unwrap()),
                    portrait
                );
            }
            for degrees in [90, 270] {
                assert_eq!(
                    platform.runtime_logical_size(rotation_is_landscape(degrees).unwrap()),
                    (portrait.1, portrait.0)
                );
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn ready_device_output_does_not_wait_for_an_inherited_writer_to_close() {
        use std::io::Write;
        use std::os::unix::net::UnixStream;

        let (mut reader, mut writer) = UnixStream::pair().unwrap();
        reader.set_nonblocking(true).unwrap();
        writer.write_all(b"active").unwrap();
        let mut output = Vec::new();
        read_available_device_output(
            &mut reader,
            &mut output,
            "query-fixture",
            Instant::now() + Duration::from_secs(1),
            &|| false,
        )
        .unwrap();
        assert_eq!(output, b"active");
        // The writer intentionally remains alive through this assertion.
        writer.write_all(b"!").unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn bounded_query_returns_completed_output_without_a_fixed_second_delay() {
        let output = bounded_device_command(
            "sh",
            &["-c", "printf active"],
            Duration::from_millis(700),
            || false,
        )
        .unwrap();
        assert_eq!(output, "active");
    }

    #[cfg(unix)]
    #[test]
    fn bounded_query_cancels_and_reaps_its_owned_child() {
        let polls = Cell::new(0);
        let error = bounded_device_command(
            "sh",
            &["-c", "exec sleep 30"],
            Duration::from_millis(700),
            || {
                polls.set(polls.get() + 1);
                polls.get() >= 3
            },
        )
        .unwrap_err();
        assert!(error.to_string().contains("cancelled"));
        assert!(polls.get() >= 3);
    }

    #[cfg(unix)]
    #[test]
    fn bounded_query_times_out_without_waiting_for_the_service() {
        let error = bounded_device_command(
            "sh",
            &["-c", "exec sleep 30"],
            Duration::from_millis(20),
            || false,
        )
        .unwrap_err();
        assert!(error.to_string().contains("device-service deadline"));
    }

    #[test]
    fn sleep_cancellation_prevents_a_shared_old_drag_from_turning_a_page() {
        use crate::gestures::{PaginationDecision, PointerPagination};

        let platform = Platform::new(DeviceProfile::Pw6, false).unwrap();
        let turns = Rc::new(Cell::new(0));
        let observed_turns = turns.clone();
        let mut pagination = PointerPagination::default();
        let observer = PointerObserver::new(
            move |input| {
                let now = Instant::now();
                let decision = match input.phase {
                    PointerPhase::Press => {
                        pagination.press(input.position.x, input.position.y, now, true)
                    }
                    PointerPhase::Move => {
                        pagination.move_to(input.position.x, input.position.y, now, true)
                    }
                    PointerPhase::Release => {
                        pagination.release(input.position.x, input.position.y, now, true)
                    }
                    PointerPhase::Cancel => {
                        pagination.cancel();
                        PaginationDecision::default()
                    }
                };
                if let Some(turn) = decision.turn {
                    observed_turns.set(observed_turns.get() + turn);
                }
                decision.consume
            },
            || true,
        );
        platform.pointer_observer.replace(Some(observer.clone()));
        let input = |phase, x| PointerInput {
            phase,
            position: slint::LogicalPosition::new(x, 100.0),
        };
        observer.observe(input(PointerPhase::Press, 400.0));
        observer.observe(input(PointerPhase::Move, 300.0));
        platform.cancel_pointer_observer();
        observer.observe(input(PointerPhase::Release, 200.0));
        assert_eq!(turns.get(), 0);
        observer.observe(input(PointerPhase::Press, 400.0));
        observer.observe(input(PointerPhase::Move, 300.0));
        observer.observe(input(PointerPhase::Release, 200.0));
        assert_eq!(turns.get(), 1);
    }

    #[test]
    fn stale_status_keeps_last_observation_until_an_explicit_refresh() {
        let platform = Platform::new(DeviceProfile::Pw6, false).unwrap();
        let observations = Cell::new(0);
        let first = DeviceStatus {
            time: "07:09".into(),
            battery: 13,
            wifi: false,
            charging: true,
            brightness: 4,
            warmth: 2,
            free_bytes: 123_456_789,
        };
        let second = DeviceStatus {
            time: "07:10".into(),
            battery: 12,
            ..first.clone()
        };
        let third = DeviceStatus {
            time: "07:11".into(),
            battery: 11,
            ..first.clone()
        };
        platform.observe_status_with(|| {
            observations.set(observations.get() + 1);
            first.clone()
        });
        platform.invalidate_status_cache();
        assert_eq!(platform.cached_status(), first);
        assert_eq!(observations.get(), 1);
        assert!(platform.status_cache.borrow().as_ref().unwrap().0.is_none());
        assert_eq!(
            platform.observe_status_with(|| {
                observations.set(observations.get() + 1);
                second.clone()
            }),
            second
        );
        assert_eq!(observations.get(), 2);
        platform
            .apply_preferences(&Preferences {
                brightness: 15,
                warmth: 6,
                ..Preferences::default()
            })
            .unwrap();
        assert_eq!(platform.cached_status(), second);
        assert_eq!(observations.get(), 2);
        assert_eq!(
            platform.observe_status_with(|| {
                observations.set(observations.get() + 1);
                third.clone()
            }),
            third
        );
        assert_eq!(observations.get(), 3);
        platform.resume_after_sleep().unwrap();
        assert_eq!(platform.cached_status(), third);
        assert_eq!(observations.get(), 3);
        platform.observe_status_with(|| {
            observations.set(observations.get() + 1);
            first.clone()
        });
        assert_eq!(observations.get(), 4);
    }

    #[test]
    fn public_status_reobserves_after_invalidation_and_preference_changes() {
        let platform = Platform::new(DeviceProfile::Pw5, false).unwrap();
        let observed = DeviceStatus {
            time: "23:41".into(),
            battery: 8,
            wifi: false,
            ..DeviceStatus::default()
        };
        platform.observe_status_with(|| observed.clone());
        assert_eq!(platform.status(), observed);
        platform.invalidate_status_cache();
        assert_eq!(platform.cached_status(), observed);
        let refreshed = platform.status();
        assert_ne!(refreshed, observed);
        platform
            .apply_preferences(&Preferences {
                brightness: 15,
                warmth: 6,
                ..Preferences::default()
            })
            .unwrap();
        assert_eq!(platform.cached_status(), refreshed);
        let refreshed = platform.status();
        assert_eq!((refreshed.brightness, refreshed.warmth), (15, 6));
        assert_eq!(platform.cached_status(), refreshed);
    }

    #[cfg(all(feature = "kindle", target_os = "linux"))]
    #[test]
    fn suspend_tcp_abort_preserves_listener_accepted_connections_and_unix_streams() {
        use std::{
            io::{Read, Write},
            net::{TcpListener, TcpStream},
            os::{fd::AsRawFd, unix::net::UnixStream},
        };
        let companion = TcpListener::bind("127.0.0.1:0").unwrap();
        let mut phone = TcpStream::connect(companion.local_addr().unwrap()).unwrap();
        let (mut accepted, _) = companion.accept().unwrap();
        accepted
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let provider = TcpListener::bind("127.0.0.1:0").unwrap();
        let outbound = TcpStream::connect(provider.local_addr().unwrap()).unwrap();
        let (mut remote, _) = provider.accept().unwrap();
        remote
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let (mut local, mut peer) = UnixStream::pair().unwrap();
        peer.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        let aborted = abort_tcp_descriptors(vec![
            companion.as_raw_fd(),
            accepted.as_raw_fd(),
            outbound.as_raw_fd(),
            outbound.as_raw_fd(),
            local.as_raw_fd(),
        ])
        .unwrap();
        assert_eq!(aborted, 1);
        let mut byte = [0u8; 1];
        assert_eq!(remote.read(&mut byte).unwrap(), 0);
        phone.write_all(b"p").unwrap();
        accepted.read_exact(&mut byte).unwrap();
        assert_eq!(byte, *b"p");
        local.write_all(b"u").unwrap();
        peer.read_exact(&mut byte).unwrap();
        assert_eq!(byte, *b"u");
        let _another_phone = TcpStream::connect(companion.local_addr().unwrap()).unwrap();
        let _another_connection = companion.accept().unwrap();
    }

    #[test]
    fn refresh_counter_counts_pages_without_consuming_ui_changes() {
        let mut count = 0;
        for index in 1..=12 {
            let (kind, next) = next_refresh(RefreshKind::Partial, count, 6);
            assert_eq!(
                kind,
                if index % 6 == 0 {
                    RefreshKind::Full
                } else {
                    RefreshKind::Partial
                }
            );
            assert_eq!(
                next_refresh(RefreshKind::Ui, next, 6),
                (RefreshKind::Ui, next)
            );
            count = next;
        }
    }

    #[test]
    fn chapter_only_refresh_never_adds_a_periodic_flash() {
        assert_eq!(
            next_refresh(RefreshKind::Partial, 400, 0),
            (RefreshKind::Partial, 401)
        );
        assert_eq!(
            next_refresh(RefreshKind::Full, 401, 0),
            (RefreshKind::Full, 0)
        );
    }

    #[test]
    fn design_profiles_preserve_physical_scale_and_no_frontlight_capabilities() {
        for profile in DeviceProfile::value_variants() {
            let (width, height) = profile.logical_size(false);
            assert_eq!(profile.native_size(), (width * 2, height * 2));
            assert_eq!(profile.logical_size(true), (height, width));
        }
        assert!(!DeviceProfile::Scribe3NoFrontlight.has_frontlight());
        assert!(!DeviceProfile::Scribe3NoFrontlight.has_warmth());
        assert!(DeviceProfile::Scribe3NoFrontlight.has_orientation());
        assert!(!DeviceProfile::Pw5.has_orientation());
    }
}
