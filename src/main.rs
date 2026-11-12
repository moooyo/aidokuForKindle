mod bootstrap_fonts;
mod bootstrap_lifecycle;
mod bootstrap_presentation;
mod bootstrap_startup;
mod sleep_cover_protocol;
mod sleep_cover_runtime;

use std::{
    cell::{Cell, RefCell},
    fs,
    path::{Path, PathBuf},
    rc::Rc,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

use aidoku_kindle::{
    app::{App, AppOptions},
    gestures::PointerPagination,
    platform::{DeviceProfile, Platform, PointerInput, PointerObserver, PointerPhase},
    ui::{AppWindow, Commands, State},
};
use anyhow::{Context, Result, bail};
use clap::Parser;
use serde::{Deserialize, Serialize};
use slint::ComponentHandle;

use bootstrap_fonts::Fonts;
use bootstrap_startup::{ApplicationSlot, Startup};

static STOP_REQUESTED: AtomicBool = AtomicBool::new(false);
static FONT_INITIALIZATIONS: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Parser)]
#[command(name = "aidoku", version, about = "Independent Kindle manga reader")]
struct Arguments {
    /// Internal exclusive startup portrait publication for the owning launcher.
    #[arg(long, hide = true)]
    capture_native_portrait: bool,

    /// Authorize fresh portrait metadata for the launcher's current sleep epoch.
    #[arg(long, hide = true)]
    authorize_native_portrait_resume: Option<u64>,

    /// Internal bounded powerd observation used by the installed launcher.
    #[arg(long, hide = true)]
    native_power_state: bool,

    /// Limit one launcher powerd query without entering the application runtime.
    #[arg(long, hide = true, default_value_t = 700, value_parser = clap::value_parser!(u64).range(1..=700))]
    native_power_state_timeout_ms: u64,

    /// Select a design profile, not a declaration of hardware compatibility.
    #[arg(long, default_value = "pw5")]
    device: DeviceProfile,

    /// Use native Kindle framebuffer and touch input instead of the desktop.
    #[arg(long)]
    kindle: bool,

    /// Use an isolated demonstration catalog without authenticated source writes.
    #[arg(long)]
    demo: bool,

    /// Disable network operations and read locally available content only.
    #[arg(long)]
    offline: bool,

    /// Open a named application screen.
    #[arg(long, default_value = "library", value_parser = ["library", "sources", "catalog", "search", "history", "settings", "detail", "reader", "downloads"])]
    screen: String,

    /// Use a landscape viewport and the supported landscape reader layout.
    #[arg(long)]
    landscape: bool,

    /// Restore the persisted navigation checkpoint after a managed sleep fallback.
    #[arg(long)]
    resume: bool,

    /// Override the per-user metadata and content directory.
    #[arg(long)]
    data_dir: Option<PathBuf>,

    /// Save a final PNG plus metadata and state JSON sidecars.
    #[arg(long)]
    capture: Option<PathBuf>,

    /// Save bounded software timing after exit, relative to the first Rust main entry.
    #[arg(long)]
    performance_trace: Option<PathBuf>,

    /// Allow up to 15 seconds for visible cover publication before a capture.
    #[arg(long)]
    capture_settle_covers: bool,

    /// Replay timed JSON intents and real Slint logical pointer events.
    #[arg(long)]
    actions: Option<PathBuf>,

    /// Quit after this many milliseconds in the event loop.
    #[arg(long)]
    exit_after_ms: Option<u64>,

    /// Import a local comic file or directory through the application adapter.
    #[arg(long)]
    import: Option<PathBuf>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ScriptAction {
    at_ms: u64,
    section: String,
    action: String,
    #[serde(default)]
    value: String,
}

#[derive(Serialize)]
struct CaptureMetadata {
    route: String,
    profile: String,
    backend: &'static str,
    design_logical_size: [u32; 2],
    logical_size: [u32; 2],
    window_size: [u32; 2],
    snapshot_size: [u32; 2],
    scale_factor: f32,
    landscape: bool,
    rotation_degrees: u16,
    refresh_count: u64,
    last_refresh: aidoku_kindle::platform::RefreshKind,
    process_id: u32,
    font_initializations: u64,
    lifecycle: aidoku_kindle::platform::SleepStatus,
}

fn main() -> std::process::ExitCode {
    let process_entry = Instant::now();
    let arguments = Arguments::parse();
    let result =
        aidoku_kindle::reader_timing::enable(arguments.performance_trace.as_deref(), process_entry)
            .and_then(|()| run(arguments));
    let trace_result = aidoku_kindle::reader_timing::finish(result.is_ok());
    match result.and(trace_result) {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("aidoku: {error:#}");
            std::process::ExitCode::FAILURE
        }
    }
}

fn run(arguments: Arguments) -> Result<()> {
    install_shutdown_signals()?;
    if arguments.capture_native_portrait || arguments.authorize_native_portrait_resume.is_some() {
        if !arguments.kindle
            || arguments.native_power_state
            || (arguments.capture_native_portrait
                && arguments.authorize_native_portrait_resume.is_some())
        {
            bail!("A native portrait helper requires one exclusive Kindle publication action");
        }
        #[cfg(all(feature = "kindle", target_os = "linux"))]
        {
            if arguments.capture_native_portrait {
                aidoku_kindle::native_presentation::capture_startup(arguments.device, || {
                    STOP_REQUESTED.load(Ordering::Acquire)
                })?;
            } else {
                aidoku_kindle::native_presentation::authorize_resume(
                    arguments.authorize_native_portrait_resume.unwrap(),
                    || STOP_REQUESTED.load(Ordering::Acquire),
                )?;
            }
            println!("PUBLISHED");
            return Ok(());
        }
        #[cfg(not(all(feature = "kindle", target_os = "linux")))]
        bail!("Native portrait publication requires the Linux Kindle backend");
    }
    if arguments.native_power_state {
        let state = aidoku_kindle::platform::query_native_power_state(
            Duration::from_millis(arguments.native_power_state_timeout_ms),
            || STOP_REQUESTED.load(Ordering::Acquire),
        )?;
        println!("{state}");
        return Ok(());
    }
    let actions = load_actions(arguments.actions.as_deref())?;
    let deadline = automatic_deadline(&arguments, &actions)?;
    let data_dir = arguments
        .data_dir
        .clone()
        .map(Ok)
        .unwrap_or_else(|| default_data_dir(arguments.kindle))?;
    fs::create_dir_all(&data_dir).context("Create the application data directory")?;
    bootstrap_lifecycle::install_signal_wakeup(None)?;
    let platform = Rc::new(Platform::new(arguments.device, arguments.kindle)?);
    aidoku_kindle::reader_timing::mark("platform_initialized");
    let mut startup_feedback =
        bootstrap_presentation::StartupFeedback::show(&platform, &data_dir, arguments.demo)?;
    if STOP_REQUESTED.load(Ordering::Relaxed) {
        return Ok(());
    }
    // The Kindle backend changes its default-font environment variable. Install
    // it before Tokio or any other background worker is started.
    startup_feedback.pause()?;
    platform.install_backend(Fonts::bootstrap_bytes())?;
    bootstrap_lifecycle::install_signal_wakeup(platform.signal_wakeup_fd())?;
    let ui = AppWindow::new().context("Create the application window")?;
    Fonts::register_bootstrap(&ui)?;
    ui.global::<State>()
        .set_large_screen(arguments.device.is_scribe());
    ui.global::<State>().set_landscape(arguments.landscape);
    let (width, height) = platform.runtime_logical_size(arguments.landscape);
    ui.set_canvas_width(width as f32);
    ui.set_canvas_height(height as f32);
    platform.configure_window(ui.window(), arguments.landscape)?;
    bootstrap_presentation::configure_startup(&ui, &data_dir, arguments.demo)?;
    let app: ApplicationSlot = Rc::new(RefCell::new(None));
    let startup = Rc::new(RefCell::new(Startup::new(
        &ui,
        AppOptions {
            data_dir,
            demo: arguments.demo,
            offline: arguments.offline,
            initial_screen: arguments.screen,
            landscape: arguments.landscape,
            resume: arguments.resume,
        },
        arguments.device,
        arguments.import,
        platform.sleep_status().frame_send_count,
    )));
    {
        let ui_weak = ui.as_weak();
        ui.global::<Commands>()
            .on_action(move |section, action, _| {
                if section == "app"
                    && action == "exit"
                    && let Some(ui) = ui_weak.upgrade()
                {
                    ui.global::<Commands>().invoke_exit_requested();
                }
            });
    }
    let pointer_observer = pagination_observer(&ui, &app, &platform);
    platform.install_input_observer(ui.window(), pointer_observer.clone())?;

    let failures = Rc::new(RefCell::new(Vec::<String>::new()));
    let quitting = Rc::new(Cell::new(false));
    let exit_feedback = Rc::new(RefCell::new(bootstrap_presentation::ExitFeedback::new(
        Instant::now(),
    )));
    let exit_render_requested = Arc::new(AtomicBool::new(false));
    let exit_rendered = Arc::new(AtomicBool::new(false));
    let startup_rendered = Arc::new(AtomicBool::new(false));
    let rendering_notifier = {
        let requested = exit_render_requested.clone();
        let rendered = exit_rendered.clone();
        let startup_rendered = startup_rendered.clone();
        ui.window()
            .set_rendering_notifier(move |state, _| {
                if matches!(state, slint::RenderingState::AfterRendering) {
                    startup_rendered.store(true, Ordering::Release);
                    if requested.load(Ordering::Acquire) {
                        rendered.store(true, Ordering::Release);
                    }
                }
            })
            .is_ok()
    };
    let capture_attempted = Rc::new(Cell::new(false));
    let capture_settle_covers = arguments.capture_settle_covers;
    let capture_path = arguments.capture;
    let started = Instant::now();
    let startup_ready_elapsed = Rc::new(Cell::new(None::<Duration>));
    let lifecycle = Rc::new(RefCell::new(bootstrap_lifecycle::Coordinator::new(
        arguments.kindle,
    )));
    let advance_startup: Rc<RefCell<dyn FnMut() -> bool>> = {
        let ui = ui.as_weak();
        let app = Rc::downgrade(&app);
        let platform = Rc::downgrade(&platform);
        let lifecycle = lifecycle.clone();
        let startup = startup.clone();
        let rendered = startup_rendered.clone();
        let ready_elapsed = startup_ready_elapsed.clone();
        let failures = failures.clone();
        let quitting = quitting.clone();
        Rc::new(RefCell::new(move || {
            if !lifecycle.borrow().application_work_allowed() {
                return false;
            }
            if ready_elapsed.get().is_some() {
                return true;
            }
            if startup.borrow().failed() {
                return false;
            }
            let (Some(ui), Some(app), Some(platform)) =
                (ui.upgrade(), app.upgrade(), platform.upgrade())
            else {
                return false;
            };
            let result = startup.borrow_mut().advance(
                &ui,
                &platform,
                &app,
                rendered.load(Ordering::Acquire),
                rendering_notifier,
            );
            match result {
                Ok(true) => {
                    ready_elapsed.set(Some(lifecycle.borrow().active_elapsed(started)));
                    lifecycle
                        .borrow()
                        .publish_initial(app.borrow().as_ref(), &platform);
                    true
                }
                Ok(false) => false,
                Err(error) => {
                    failures
                        .borrow_mut()
                        .push(format!("Startup failed: {error:#}"));
                    if let Some(app) = app.borrow().as_ref() {
                        app.borrow_mut().shutdown();
                    }
                    startup.borrow_mut().fail(&ui, &platform);
                    if deadline.is_some() {
                        request_quit(&quitting, &failures);
                    }
                    false
                }
            }
        }))
    };
    {
        let ui_weak = ui.as_weak();
        let app = app.clone();
        let startup = startup.clone();
        let platform = platform.clone();
        let lifecycle = lifecycle.clone();
        let quitting = quitting.clone();
        let failures = failures.clone();
        let feedback = exit_feedback.clone();
        let render_requested = exit_render_requested.clone();
        ui.global::<Commands>().on_exit_requested(move || {
            if quitting.get() || feedback.borrow().requested() {
                return;
            }
            let Some(ui) = ui_weak.upgrade() else {
                return;
            };
            startup.borrow_mut().cancel();
            // Commands can request exit while dispatch still owns RefMut<App>.
            // Retire the renderer now; the post-loop path hides the App scene.
            if let Err(error) = lifecycle.borrow_mut().cancel_cover(None, &platform) {
                failures
                    .borrow_mut()
                    .push(format!("Cancel sleep presentation failed: {error:#}"));
            }
            if app.borrow().is_none() {
                lifecycle.borrow().announce_ordinary_exit();
                request_quit(&quitting, &failures);
                return;
            }
            if !bootstrap_presentation::exit_display_owned(
                lifecycle.borrow().active(),
                platform.sleep_status(),
                bootstrap_lifecycle::display_activity_allowed(),
            ) {
                request_quit(&quitting, &failures);
                return;
            }
            // The exit overlay must never consume a pending Reader timing token.
            platform.cancel_reader_frame();
            let state = ui.global::<State>();
            state.set_exit_frame(0);
            state.set_exit_stage(aidoku_kindle::localization::translate("exit_saving").into());
            state.set_exiting(true);
            ui.window().request_redraw();
            platform.refresh(aidoku_kindle::platform::RefreshKind::Ui);
            render_requested.store(true, Ordering::Release);
            feedback
                .borrow_mut()
                .request(platform.sleep_status().frame_send_count, Instant::now());
            aidoku_kindle::reader_timing::mark("exit_feedback_requested");
        });
    }
    {
        let ui_weak = ui.as_weak();
        ui.window().on_close_requested(move || {
            if let Some(ui) = ui_weak.upgrade() {
                ui.global::<Commands>()
                    .invoke_action("app".into(), "exit".into(), "".into());
            }
            slint::CloseRequestResponse::KeepWindowShown
        });
    }
    {
        let ui_weak = ui.as_weak();
        let app = Rc::downgrade(&app);
        let platform = Rc::downgrade(&platform);
        let lifecycle = lifecycle.clone();
        let quitting = quitting.clone();
        let exit_feedback = exit_feedback.clone();
        let failures = failures.clone();
        let advance_startup = advance_startup.clone();
        ui.global::<Commands>().on_job_ready(move || {
            if quitting.get() {
                return;
            }
            let (Some(ui), Some(app), Some(platform)) =
                (ui_weak.upgrade(), app.upgrade(), platform.upgrade())
            else {
                return;
            };
            if STOP_REQUESTED.load(Ordering::Acquire) {
                retire_sleep_cover(&lifecycle, &app, &platform, &failures);
                lifecycle.borrow().announce_ordinary_exit();
                request_quit(&quitting, &failures);
                return;
            }
            if exit_feedback.borrow().requested() {
                if !bootstrap_lifecycle::display_activity_allowed() {
                    request_quit(&quitting, &failures);
                }
                return;
            }
            // Handle a pending sleep before accepting a worker result. This
            // event must preserve the same ownership gate as the poll timer.
            let application = app.borrow().clone();
            if let Err(error) = lifecycle
                .borrow_mut()
                .drive(&ui, application.as_ref(), &platform)
            {
                failures
                    .borrow_mut()
                    .push(format!("Lifecycle job-ready failed: {error:#}"));
                request_quit(&quitting, &failures);
                return;
            }
            if !lifecycle.borrow().application_work_allowed() {
                if let Some(app) = application {
                    app.borrow().acknowledge_foreground_jobs_ready();
                }
                return;
            }
            let ready = (advance_startup.borrow_mut())();
            let application = app.borrow().clone();
            if ready && let Some(application) = application {
                application.borrow_mut().poll_foreground_jobs();
            }
        });
    }
    let poll_timer = slint::Timer::default();
    {
        let ui = ui.as_weak();
        let app = Rc::downgrade(&app);
        let platform_weak = Rc::downgrade(&platform);
        let lifecycle = lifecycle.clone();
        let quitting = quitting.clone();
        let exit_feedback = exit_feedback.clone();
        let failures = failures.clone();
        platform.install_control_callback(move || {
            let (Some(ui), Some(app), Some(platform)) =
                (ui.upgrade(), app.upgrade(), platform_weak.upgrade())
            else {
                return;
            };
            if STOP_REQUESTED.load(Ordering::Acquire) {
                retire_sleep_cover(&lifecycle, &app, &platform, &failures);
                lifecycle.borrow().announce_ordinary_exit();
                request_quit(&quitting, &failures);
                return;
            }
            if exit_feedback.borrow().requested()
                && !bootstrap_lifecycle::display_activity_allowed()
            {
                request_quit(&quitting, &failures);
                return;
            }
            let application = app.borrow().clone();
            if let Err(error) = lifecycle
                .borrow_mut()
                .drive(&ui, application.as_ref(), &platform)
            {
                failures
                    .borrow_mut()
                    .push(format!("Lifecycle control failed: {error:#}"));
                request_quit(&quitting, &failures);
            }
            if exit_feedback.borrow().requested() && !lifecycle.borrow().active() {
                request_quit(&quitting, &failures);
            }
        })?;
    }
    {
        let ui = ui.as_weak();
        let app = app.clone();
        let platform = platform.clone();
        let failures = failures.clone();
        let quitting = quitting.clone();
        let capture_attempted = capture_attempted.clone();
        let capture_path = capture_path.clone();
        let pointer_observer = pointer_observer.clone();
        let lifecycle = lifecycle.clone();
        let exit_feedback = exit_feedback.clone();
        let exit_rendered = exit_rendered.clone();
        let advance_startup = advance_startup.clone();
        let startup_ready_elapsed = startup_ready_elapsed.clone();
        let next_action = Cell::new(0usize);
        poll_timer.start(
            slint::TimerMode::Repeated,
            Duration::from_millis(50),
            move || {
                if quitting.get() {
                    return;
                }
                let Some(ui) = ui.upgrade() else {
                    return;
                };
                if STOP_REQUESTED.load(Ordering::Acquire) {
                    retire_sleep_cover(&lifecycle, &app, &platform, &failures);
                    lifecycle.borrow().announce_ordinary_exit();
                    request_quit(&quitting, &failures);
                    return;
                }
                if exit_feedback.borrow().requested()
                    && !bootstrap_lifecycle::display_activity_allowed()
                {
                    request_quit(&quitting, &failures);
                    return;
                }
                let application = app.borrow().clone();
                if let Err(error) =
                    lifecycle
                        .borrow_mut()
                        .drive(&ui, application.as_ref(), &platform)
                {
                    failures
                        .borrow_mut()
                        .push(format!("Lifecycle poll failed: {error:#}"));
                    request_quit(&quitting, &failures);
                    return;
                }
                if !lifecycle.borrow().active() {
                    if exit_feedback.borrow().requested() {
                        request_quit(&quitting, &failures);
                    }
                    return;
                }
                if exit_feedback.borrow().requested() {
                    let Some(app) = application else {
                        request_quit(&quitting, &failures);
                        return;
                    };
                    let now = Instant::now();
                    let mut feedback = exit_feedback.borrow_mut();
                    if feedback.frame_submitted(
                        platform.is_kindle(),
                        platform.sleep_status().frame_send_count,
                        exit_rendered.load(Ordering::Acquire),
                        rendering_notifier,
                    ) {
                        aidoku_kindle::reader_timing::mark("exit_feedback_submitted");
                        feedback.begin_shutdown(now);
                        aidoku_kindle::reader_timing::mark("shutdown_started");
                        app.borrow_mut().shutdown();
                        if !bootstrap_lifecycle::display_activity_allowed() {
                            request_quit(&quitting, &failures);
                            return;
                        }
                        ui.global::<State>().set_exit_stage(
                            aidoku_kindle::localization::translate("exit_stopping").into(),
                        );
                    }
                    if feedback.started_shutdown() {
                        if app.borrow().shutdown_ready() {
                            aidoku_kindle::reader_timing::mark("shutdown_complete");
                            request_quit(&quitting, &failures);
                            return;
                        }
                        if feedback.shutdown_timed_out(now) {
                            aidoku_kindle::reader_timing::mark("shutdown_timeout");
                            request_quit(&quitting, &failures);
                            return;
                        }
                    } else if feedback.frame_timed_out(now) {
                        failures.borrow_mut().push(
                            "The exit feedback frame was not submitted within two seconds".into(),
                        );
                        request_quit(&quitting, &failures);
                        return;
                    }
                    if let Some(frame) = feedback.next_activity_frame(now) {
                        ui.global::<State>().set_exit_frame(frame);
                        ui.window().request_redraw();
                        platform.refresh(aidoku_kindle::platform::RefreshKind::Ui);
                    }
                    return;
                }
                if !lifecycle.borrow().application_work_allowed()
                    || !(advance_startup.borrow_mut())()
                {
                    return;
                }
                let Some(app) = app.borrow().clone() else {
                    return;
                };
                let elapsed = lifecycle
                    .borrow()
                    .active_elapsed(started)
                    .saturating_sub(startup_ready_elapsed.get().unwrap_or_default());
                while let Some(action) = actions.get(next_action.get()) {
                    if Duration::from_millis(action.at_ms) > elapsed {
                        break;
                    }
                    let result = aidoku_kindle::reader_timing::with_scripted_input(
                        action.section == "pointer",
                        || {
                            if action.section == "pointer" {
                                dispatch_pointer(
                                    ui.window(),
                                    &action.action,
                                    &action.value,
                                    &pointer_observer,
                                )
                            } else {
                                app.borrow_mut().dispatch(
                                    &action.section,
                                    &action.action,
                                    &action.value,
                                )
                            }
                        },
                    );
                    next_action.set(next_action.get() + 1);
                    if let Err(error) = result {
                        failures.borrow_mut().push(format!(
                            "Scripted {} / {} failed: {error:#}",
                            action.section, action.action
                        ));
                        request_quit(&quitting, &failures);
                        return;
                    }
                    if exit_feedback.borrow().requested() {
                        return;
                    }
                }
                app.borrow_mut().poll();
                if STOP_REQUESTED.load(Ordering::Relaxed)
                    || deadline.is_some_and(|deadline| elapsed >= deadline)
                {
                    if capture_settle_covers
                        && capture_path.is_some()
                        && !STOP_REQUESTED.load(Ordering::Acquire)
                        && deadline.is_some_and(|deadline| {
                            elapsed.saturating_sub(deadline) < Duration::from_secs(15)
                        })
                        && app.borrow().capture_projection_pending()
                    {
                        return;
                    }
                    if let Some(path) = capture_path.as_deref() {
                        capture_attempted.set(true);
                        if let Err(error) =
                            capture_window(&ui, path, &platform, Some(&app.borrow()))
                        {
                            failures
                                .borrow_mut()
                                .push(format!("Capture failed: {error:#}"));
                        }
                    }
                    request_quit(&quitting, &failures);
                }
            },
        );
    }
    startup_feedback.finish()?;
    ui.show().context("Show the application window")?;
    aidoku_kindle::reader_timing::mark("window_shown");
    aidoku_kindle::reader_timing::mark("startup_window_shown");
    // Yield into the first-scene gate without waiting for the maintenance tick.
    startup.borrow().schedule();
    lifecycle.borrow().publish_initial(None, &platform);
    aidoku_kindle::reader_timing::mark("event_loop_started");
    let event_loop_result = slint::run_event_loop();
    poll_timer.stop();
    lifecycle
        .borrow_mut()
        .retire_cover_after_event_loop(app.borrow().as_ref());
    startup.borrow_mut().cancel();
    if event_loop_result.is_ok() && failures.borrow().is_empty() {
        lifecycle.borrow().announce_ordinary_exit();
    }
    let application = app.borrow().clone();
    let capture_app = application.as_ref().map(|app| app.borrow());
    if let Some(path) = capture_path.as_deref()
        && !capture_attempted.replace(true)
        && let Err(error) = capture_window(&ui, path, &platform, capture_app.as_deref())
    {
        failures
            .borrow_mut()
            .push(format!("Final capture failed: {error:#}"));
    }
    drop(capture_app);
    if let Some(app) = application {
        app.borrow_mut().shutdown();
        let already_drained = exit_feedback.borrow().started_shutdown();
        let shutdown_finished = if already_drained && !app.borrow().shutdown_ready() {
            false
        } else {
            app.borrow().finish_shutdown()
        };
        if !shutdown_finished {
            eprintln!("aidoku: a bounded network request is still stopping");
        }
    }
    if !startup.borrow_mut().finish() {
        eprintln!("aidoku: a bounded startup preparation is still stopping");
    }
    bootstrap_lifecycle::clear_signal_wakeup();
    event_loop_result.context("Run the application event loop")?;
    if !failures.borrow().is_empty() {
        bail!("{}", failures.borrow().join("\n"));
    }
    Ok(())
}

fn pagination_observer(
    ui: &AppWindow,
    app: &ApplicationSlot,
    platform: &Rc<Platform>,
) -> PointerObserver {
    let observed_app = Rc::downgrade(app);
    let enabled_app = Rc::downgrade(app);
    let activity_platform = Rc::downgrade(platform);
    let enabled_ui = ui.as_weak();
    let ui = ui.as_weak();
    let mut pagination = PointerPagination::default();
    PointerObserver::new(
        move |input| {
            if input.phase == PointerPhase::Press
                && let Some(platform) = activity_platform.upgrade()
            {
                platform.note_user_activity();
            }
            if input.phase == PointerPhase::Cancel {
                if let Some(ui) = ui.upgrade() {
                    let state = ui.global::<State>();
                    state.set_reader_input_epoch(state.get_reader_input_epoch().wrapping_add(1));
                }
                pagination.cancel();
                return false;
            }
            if ui.upgrade().is_some_and(|ui| {
                ui.global::<State>().get_exiting() || ui.global::<State>().get_booting()
            }) {
                pagination.cancel();
                return false;
            }
            let Some(app) = observed_app
                .upgrade()
                .and_then(|slot| slot.borrow().clone())
            else {
                pagination.cancel();
                return false;
            };
            let enabled = {
                let application = app.borrow();
                let (route, modal) = application.gesture_context();
                route != "reader" && modal.is_empty()
            };
            let now = Instant::now();
            let decision = match input.phase {
                PointerPhase::Press => {
                    pagination.press(input.position.x, input.position.y, now, enabled)
                }
                PointerPhase::Move => {
                    pagination.move_to(input.position.x, input.position.y, now, enabled)
                }
                PointerPhase::Release => {
                    pagination.release(input.position.x, input.position.y, now, enabled)
                }
                PointerPhase::Cancel => {
                    pagination.cancel();
                    return false;
                }
            };
            if let Some(ui) = ui.upgrade() {
                if decision.cancel_children {
                    // This cancels the child's grab/hold without going through
                    // any native, Winit, or scripted observation hook again.
                    if let Err(error) = ui
                        .window()
                        .try_dispatch_event(slint::platform::WindowEvent::PointerExited)
                    {
                        eprintln!("aidoku: cancel child pointer input failed: {error}");
                    }
                }
                if let Some(turn) = decision.turn {
                    ui.global::<Commands>().invoke_action(
                        "page".into(),
                        "turn".into(),
                        turn.to_string().into(),
                    );
                }
            }
            decision.consume
        },
        move || {
            if enabled_ui.upgrade().is_some_and(|ui| {
                ui.global::<State>().get_exiting() || ui.global::<State>().get_booting()
            }) {
                return false;
            }
            enabled_app
                .upgrade()
                .and_then(|slot| slot.borrow().clone())
                .is_some_and(|app| {
                    let application = app.borrow();
                    let (route, modal) = application.gesture_context();
                    route != "reader" && modal.is_empty()
                })
        },
    )
}

fn load_actions(path: Option<&Path>) -> Result<Vec<ScriptAction>> {
    let Some(path) = path else {
        return Ok(Vec::new());
    };
    let mut actions: Vec<ScriptAction> = serde_json::from_slice(
        &fs::read(path).with_context(|| format!("Read actions {}", path.display()))?,
    )
    .context("Parse the action replay array")?;
    for action in &actions {
        if action.section.trim().is_empty() || action.action.trim().is_empty() {
            bail!("Scripted actions require nonempty section and action names");
        }
        if action.section == "pointer" {
            if !matches!(
                action.action.as_str(),
                "press" | "release" | "move" | "tap" | "down" | "up" | "cancel"
            ) {
                bail!("Unsupported logical pointer operation: {}", action.action);
            }
            if action.action != "cancel" {
                pointer_position(&action.value)?;
            }
        }
    }
    actions.sort_by_key(|action| action.at_ms);
    Ok(actions)
}

fn automatic_deadline(arguments: &Arguments, actions: &[ScriptAction]) -> Result<Option<Duration>> {
    let milliseconds = match arguments.exit_after_ms {
        Some(milliseconds) => Some(milliseconds),
        None if arguments.actions.is_some() => Some(
            actions
                .last()
                .map_or(0, |action| action.at_ms)
                .checked_add(1000)
                .context("The final scripted action time is too large")?
                .max(1200),
        ),
        None if arguments.capture.is_some() => Some(1200),
        None => None,
    };
    Ok(milliseconds.map(Duration::from_millis))
}

fn pointer_position(value: &str) -> Result<slint::LogicalPosition> {
    let (x, y) = value
        .split_once(',')
        .context("Pointer coordinates must be x,y")?;
    let (x, y) = (
        x.trim()
            .parse::<f32>()
            .context("Parse the pointer x coordinate")?,
        y.trim()
            .parse::<f32>()
            .context("Parse the pointer y coordinate")?,
    );
    if !x.is_finite() || !y.is_finite() || x < 0.0 || y < 0.0 {
        bail!("Pointer coordinates must be finite and nonnegative");
    }
    Ok(slint::LogicalPosition::new(x, y))
}

fn dispatch_pointer(
    window: &slint::Window,
    action: &str,
    value: &str,
    observer: &PointerObserver,
) -> Result<()> {
    use slint::platform::{PointerEventButton, WindowEvent};
    if action == "cancel" {
        return dispatch_observed_pointer(window, WindowEvent::PointerExited, observer);
    }
    let position = pointer_position(value)?;
    match action {
        "tap" => {
            dispatch_observed_pointer(window, WindowEvent::PointerMoved { position }, observer)?;
            dispatch_observed_pointer(
                window,
                WindowEvent::PointerPressed {
                    position,
                    button: PointerEventButton::Left,
                },
                observer,
            )?;
            dispatch_observed_pointer(
                window,
                WindowEvent::PointerReleased {
                    position,
                    button: PointerEventButton::Left,
                },
                observer,
            )?;
        }
        "press" | "down" => dispatch_observed_pointer(
            window,
            WindowEvent::PointerPressed {
                position,
                button: PointerEventButton::Left,
            },
            observer,
        )?,
        "release" | "up" => dispatch_observed_pointer(
            window,
            WindowEvent::PointerReleased {
                position,
                button: PointerEventButton::Left,
            },
            observer,
        )?,
        "move" => {
            dispatch_observed_pointer(window, WindowEvent::PointerMoved { position }, observer)?
        }
        _ => bail!("Unsupported logical pointer operation: {action}"),
    }
    Ok(())
}

fn dispatch_observed_pointer(
    window: &slint::Window,
    event: slint::platform::WindowEvent,
    observer: &PointerObserver,
) -> Result<()> {
    if !PointerInput::from_window_event(&event).is_some_and(|input| observer.observe(input)) {
        window.try_dispatch_event(event)?;
    }
    Ok(())
}

fn capture_window(
    ui: &AppWindow,
    path: &Path,
    platform: &Platform,
    app: Option<&App>,
) -> Result<()> {
    let landscape = ui.global::<State>().get_landscape();
    let (design_width, design_height) = platform.profile().logical_size(landscape);
    let (width, height) = platform.runtime_logical_size(landscape);
    let actual = ui.window().size();
    let snapshot = ui
        .window()
        .take_snapshot()
        .context("Render the application snapshot")?;
    if let Some(parent) = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent)?;
    }
    image::save_buffer_with_format(
        path,
        snapshot.as_bytes(),
        snapshot.width(),
        snapshot.height(),
        image::ColorType::Rgba8,
        image::ImageFormat::Png,
    )
    .with_context(|| format!("Write screenshot {}", path.display()))?;
    let mut state = app.map(App::snapshot).unwrap_or_else(|| {
        serde_json::json!({
            "route": "startup", "startup_failed": ui.global::<State>().get_startup_failed(),
        })
    });
    let metadata = CaptureMetadata {
        route: state["route"].as_str().unwrap_or("unknown").to_owned(),
        profile: platform.profile().id().to_owned(),
        backend: if platform.is_kindle() {
            "kindle"
        } else {
            "desktop"
        },
        design_logical_size: [design_width, design_height],
        logical_size: [width, height],
        window_size: [actual.width, actual.height],
        snapshot_size: [snapshot.width(), snapshot.height()],
        scale_factor: ui.window().scale_factor(),
        landscape,
        rotation_degrees: platform.display_rotation_degrees(),
        refresh_count: platform.refresh_count(),
        last_refresh: platform.last_refresh(),
        process_id: std::process::id(),
        font_initializations: FONT_INITIALIZATIONS.load(Ordering::Acquire),
        lifecycle: platform.sleep_status(),
    };
    fs::write(
        path.with_extension("metadata.json"),
        serde_json::to_vec_pretty(&metadata)?,
    )?;
    if let Some(object) = state.as_object_mut() {
        object.insert("capture_metadata".into(), serde_json::to_value(&metadata)?);
    }
    fs::write(
        path.with_extension("state.json"),
        serde_json::to_vec_pretty(&state)?,
    )?;
    Ok(())
}

fn retire_sleep_cover(
    lifecycle: &RefCell<bootstrap_lifecycle::Coordinator>,
    app: &ApplicationSlot,
    platform: &Platform,
    failures: &RefCell<Vec<String>>,
) {
    let application = app.borrow().clone();
    if let Err(error) = lifecycle
        .borrow_mut()
        .cancel_cover(application.as_ref(), platform)
    {
        failures
            .borrow_mut()
            .push(format!("Cancel sleep presentation failed: {error:#}"));
    }
}

fn request_quit(quitting: &Cell<bool>, failures: &RefCell<Vec<String>>) {
    if quitting.replace(true) {
        return;
    }
    if let Err(error) = slint::quit_event_loop() {
        failures
            .borrow_mut()
            .push(format!("Stop the event loop failed: {error}"));
    }
}

fn default_data_dir(kindle: bool) -> Result<PathBuf> {
    if kindle {
        return Ok(PathBuf::from("/mnt/us/aidoku/data"));
    }
    #[cfg(target_os = "windows")]
    {
        let base = std::env::var_os("APPDATA").context("The APPDATA directory is unavailable")?;
        Ok(PathBuf::from(base).join("AidokuForKindle"))
    }
    #[cfg(not(target_os = "windows"))]
    {
        if let Some(directory) = std::env::var_os("XDG_DATA_HOME") {
            return Ok(PathBuf::from(directory).join("aidoku-kindle"));
        }
        let base = std::env::var_os("HOME").context("The user home directory is unavailable")?;
        Ok(PathBuf::from(base).join(".local/share/aidoku-kindle"))
    }
}

#[cfg(unix)]
fn install_shutdown_signals() -> Result<()> {
    extern "C" fn stop(_: libc::c_int) {
        STOP_REQUESTED.store(true, Ordering::Relaxed);
        bootstrap_lifecycle::signal_poke();
    }
    // SAFETY: this handler only stores a lock-free atomic flag. It performs no
    // allocation, I/O, locking, or UI calls inside the signal context.
    unsafe {
        for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGHUP] {
            if libc::signal(signal, stop as *const () as libc::sighandler_t) == libc::SIG_ERR {
                return Err(std::io::Error::last_os_error()).context("Install shutdown handlers");
            }
        }
    }
    Ok(())
}

#[cfg(not(unix))]
fn install_shutdown_signals() -> Result<()> {
    Ok(())
}
