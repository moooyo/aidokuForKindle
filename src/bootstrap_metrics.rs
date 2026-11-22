//! Observe startup output without scheduling extra frames or blocking display work.

use std::{
    cell::{Cell, RefCell},
    rc::Rc,
};

use aidoku_kindle::{
    platform::{FrameCheckpoint, Platform},
    reader_timing,
    ui::{AppWindow, Commands, State},
};
use slint::ComponentHandle;

use crate::{
    bootstrap_lifecycle,
    bootstrap_startup::{ApplicationSlot, Startup},
};

fn managed_library_log_enabled(kindle: bool, normal_library: bool, managed: bool) -> bool {
    kindle && normal_library && managed
}

fn log_first_library(
    pending: &Cell<bool>,
    frame: FrameCheckpoint,
    projection: Option<(bool, bool, bool)>,
    log: impl FnOnce(FrameCheckpoint, (bool, bool, bool)),
) {
    if pending.get() && frame.active && frame.submitted
        && let Some(projection) = projection
    {
        pending.set(false);
        log(frame, projection);
    }
}

pub fn observe(
    platform: &Rc<Platform>,
    ui: &AppWindow,
    startup: &Rc<RefCell<Startup>>,
    application: &ApplicationSlot,
    lifecycle: &Rc<RefCell<bootstrap_lifecycle::Coordinator>>,
    overlap: bool,
    prepare_toolbar: bool,
    prime_library: bool,
    last_cover_grace: bool,
) {
    let ui = ui.as_weak();
    let startup = Rc::downgrade(startup);
    let application = Rc::downgrade(application);
    let lifecycle = Rc::downgrade(lifecycle);
    let observed_platform = Rc::downgrade(platform);
    let first_library = Cell::new(None::<u64>);
    let library_input_ready = Cell::new(false);
    let covers_settled = Cell::new(false);
    let library_log_pending = Cell::new(managed_library_log_enabled(
        platform.is_kindle(),
        prepare_toolbar,
        std::env::var_os("AIDOKU_MANAGED_LIBRARY").as_deref() == Some("1".as_ref()),
    ));
    let startup_costs = cfg!(feature = "startup-stage-diagnostics") && library_log_pending.get();
    platform.set_checkpoint_observer(startup_costs, move |frame| {
        if !frame.active || !bootstrap_lifecycle::display_activity_allowed() {
            return;
        }
        let Some(lifecycle) = lifecycle.upgrade() else {
            return;
        };
        if !lifecycle.borrow().application_work_allowed() {
            return;
        }
        let Some(platform) = observed_platform.upgrade() else {
            return;
        };
        if frame.initial && frame.full && frame.submitted {
            #[cfg(feature = "startup-stage-diagnostics")]
            if library_log_pending.get() {
                reader_timing::startup_stage_initial_frame(frame);
            }
            reader_timing::startup_frame("startup_initial_frame_submitted", frame, None);
            if overlap && let Some(startup) = startup.upgrade() {
                startup.borrow_mut().prepare_in_background();
            }
            if prepare_toolbar && frame.input_blocked {
                platform
                    .prepare_startup_toolbar(|| !bootstrap_lifecycle::display_activity_allowed());
            }
            if prime_library
                && frame.input_blocked
                && let (Some(startup), Some(ui)) = (startup.upgrade(), ui.upgrade())
            {
                startup.borrow_mut().prime_fonts_during_initial_frame(&ui);
            }
        }
        if !reader_timing::enabled()
            && !platform.startup_render_pending()
            && !library_log_pending.get()
        {
            return;
        }
        if frame.initial && !frame.submitted && !frame.input_blocked {
            #[cfg(feature = "startup-stage-diagnostics")]
            if library_log_pending.get() {
                reader_timing::startup_stage_input_frame(frame);
            }
            if let Some(startup) = startup.upgrade() {
                startup.borrow().close_cover_warm_window();
            }
            reader_timing::startup_frame("startup_initial_input_ready", frame, None);
        }
        let Some(ui) = ui.upgrade() else { return };
        if ui.global::<State>().get_booting() {
            return;
        }
        if library_log_pending.get() && frame.submitted {
            let projection = application
                .upgrade()
                .and_then(|slot| slot.borrow().clone())
                .and_then(|app| app.borrow().startup_library_log_projection());
            log_first_library(
                &library_log_pending,
                frame,
                projection,
                #[cfg(feature = "startup-stage-diagnostics")]
                reader_timing::startup_library_submitted_with_costs,
                #[cfg(not(feature = "startup-stage-diagnostics"))]
                reader_timing::startup_library_submitted,
            );
        }
        if frame.submitted && platform.release_startup_render_priority() {
            reader_timing::mark("startup_background_covers_admitted");
            if let Some(app) = application.upgrade().and_then(|slot| slot.borrow().clone()) {
                app.borrow_mut().begin_startup_cover_publication(last_cover_grace);
            }
            let ui = ui.as_weak();
            // Return to the regular lifecycle-gated dispatcher before starting work.
            slint::Timer::single_shot(std::time::Duration::ZERO, move || {
                if let Some(ui) = ui.upgrade() {
                    ui.global::<Commands>().invoke_job_ready();
                }
            });
        }
        if !reader_timing::enabled() {
            return;
        }
        let Some(app) = application.upgrade().and_then(|slot| slot.borrow().clone()) else {
            return;
        };
        if frame.submitted {
            app.borrow_mut().observe_import_presentation(frame);
        }
        if covers_settled.get() && library_input_ready.get() {
            return;
        }
        let Some(covers) = app.borrow().startup_cover_projection() else {
            return;
        };
        if frame.submitted {
            if first_library.get().is_none() {
                first_library.set(Some(frame.sequence));
                reader_timing::startup_frame(
                    "startup_library_frame_submitted",
                    frame,
                    Some(covers),
                );
            }
            reader_timing::startup_frame("startup_cover_frame_submitted", frame, Some(covers));
            if !covers.2 && !covers_settled.replace(true) {
                reader_timing::startup_frame("startup_visible_covers_settled", frame, Some(covers));
            }
        } else if !frame.input_blocked
            && first_library
                .get()
                .is_some_and(|first| frame.sequence >= first)
            && !library_input_ready.replace(true)
        {
            reader_timing::startup_frame("startup_library_input_ready", frame, Some(covers));
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn frame(submitted: bool, active: bool) -> FrameCheckpoint {
        FrameCheckpoint {
            #[cfg(all(feature = "startup-draw-diagnostics", target_os = "linux"))]
            draw: None,
            submitted,
            sequence: 4,
            marker: Some(5),
            initial: false,
            full: false,
            active,
            input_blocked: true,
            render: Duration::ZERO,
            settle: Duration::ZERO,
            write: Duration::ZERO,
            submit: Duration::ZERO,
        }
    }

    #[test]
    fn startup_library_log_is_one_shot_only_for_an_active_submitted_projection() {
        let pending = Cell::new(true);
        let calls = Cell::new(0);
        let projection = Some((true, false, false));
        for (frame, projection) in [
            (frame(false, true), projection),
            (frame(true, false), projection),
            (frame(true, true), None),
        ] {
            log_first_library(&pending, frame, projection, |_, _| calls.set(calls.get() + 1));
        }
        assert!(pending.get());
        assert_eq!(calls.get(), 0);
        for _ in 0..2 {
            log_first_library(&pending, frame(true, true), projection, |_, actual| {
                assert_eq!(actual, (true, false, false));
                calls.set(calls.get() + 1);
            });
        }
        assert!(!pending.get());
        assert_eq!(calls.get(), 1);
    }

    #[test]
    fn startup_library_log_stays_disabled_for_unmanaged_or_desktop_sessions() {
        assert!(managed_library_log_enabled(true, true, true));
        assert!(!managed_library_log_enabled(false, true, true));
        assert!(!managed_library_log_enabled(true, false, true));
        assert!(!managed_library_log_enabled(true, true, false));
        let pending = Cell::new(false);
        log_first_library(&pending, frame(true, true), Some((false, true, true)), |_, _| {
            panic!("Disabled startup logging must not emit a line")
        });
    }
}
