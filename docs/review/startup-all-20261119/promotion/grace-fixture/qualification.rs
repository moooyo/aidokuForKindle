// Test-only adapter, appended to an isolated copy of the exact final cover_jobs.rs.
// It controls only the arrival of the last local result and the publication
// deadline. The actual Slint timer, eligibility and retirement functions are
// unchanged. The application executable and the source tree used to build it
// are never overwritten by this fixture.
#[cfg(test)]
mod qualified_grace_observation {
    use super::*;
    use crate::app::tests::DemoRouteFixture;
    use crate::platform::DeviceProfile;
    use crate::ui::{AppWindow, BookItem, Commands, State};
    use slint::platform::software_renderer::{MinimalSoftwareWindow, RepaintBufferType};
    use std::{cell::Cell, path::PathBuf, rc::Rc};

    struct ProbePlatform(Rc<MinimalSoftwareWindow>, Rc<Cell<Duration>>);
    impl slint::platform::Platform for ProbePlatform {
        fn create_window_adapter(&self) -> std::result::Result<Rc<dyn slint::platform::WindowAdapter>, slint::PlatformError> {
            Ok(self.0.clone())
        }
        fn duration_since_start(&self) -> Duration { self.1.get() }
    }

    #[test]
    fn controlled_final_local_result() {
        let mode = std::env::var("AIDOKU_TEST_GRACE_CASE").unwrap();
        assert!(matches!(mode.as_str(), "early" | "expiry" | "rounded-wake" | "cancel" | "disabled" | "foreign"));
        let output = PathBuf::from(std::env::var_os("AIDOKU_TEST_GRACE_OUT").unwrap());
        let clock = Rc::new(Cell::new(Duration::from_micros(1_000_900)));
        slint::platform::set_platform(Box::new(ProbePlatform(MinimalSoftwareWindow::new(RepaintBufferType::ReusedBuffer), clock.clone()))).unwrap();
        let ui = AppWindow::new().unwrap();
        let wakeups = Rc::new(Cell::new(0_u32));
        let callbacks = wakeups.clone();
        ui.global::<Commands>().on_job_ready(move || callbacks.set(callbacks.get() + 1));
        ui.global::<State>().set_booting(false);
        ui.global::<State>().set_library_count(0);
        let ids = ["fixture-a".to_owned(), "fixture-b".to_owned()];
        ui.global::<State>().set_books(crate::app::model(ids.iter().map(|id| BookItem { id: id.as_str().into(), ..BookItem::default() })));
        let mut fixture = DemoRouteFixture::new("library", DeviceProfile::Pw5, false);
        let app = &mut fixture.app;
        app.ui = ui.as_weak();
        assert_eq!(app.visible_cover_ids(), ids);
        app.begin_startup_cover_publication(mode != "disabled");
        app.cover_view = ids.to_vec();
        app.cover_queue.clear();
        app.cover_retry_after.clear();
        app.cover_inflight = Some(417);
        app.cover_pending = HashSet::from([ids[1].clone()]);
        if let Some(grace) = app.startup_cover_publication.as_mut().unwrap().grace.as_mut() {
            grace.local_hits.insert(ids[0].clone());
            grace.last_local_job = Some(417);
        }
        if mode == "foreign" { app.cover_inflight = Some(418); }
        app.background_ui_dirty = false;
        app.mark_cover_dirty();
        crate::reader_timing::enable(Some(&output), Instant::now()).unwrap();
        let deadline = Instant::now();
        app.next_background_ui = deadline;
        assert!(!app.defer_last_startup_cover(deadline - Duration::from_millis(1)));
        let armed = app.defer_last_startup_cover(deadline);
        assert_eq!(armed, matches!(mode.as_str(), "early" | "expiry" | "rounded-wake" | "cancel"));
        let mut deferred_after_wake = false;
        let mut released = false;
        let mut last_pending = true;
        match mode.as_str() {
            "early" => {
                assert!(app.defer_last_startup_cover(Instant::now()));
                assert_eq!(wakeups.get(), 0);
                app.cover_inflight = None;
                app.cover_pending.remove(&ids[1]);
                app.startup_cover_publication.as_mut().unwrap().grace.as_mut().unwrap().local_hits.insert(ids[1].clone());
                last_pending = !app.cover_pending.is_empty();
                released = app.startup_cover_publication_due();
                assert!(released);
                assert!(!app.startup_cover_publication_due());
            }
            "expiry" | "rounded-wake" => {
                assert!(app.defer_last_startup_cover(Instant::now()));
                assert_eq!(wakeups.get(), 0);
            }
            "cancel" => {
                // A competing UI change must be admitted immediately even if the
                // final cover is still missing, and cancel the outstanding wake.
                app.background_ui_dirty = true;
                assert!(!app.defer_last_startup_cover(Instant::now()));
                assert!(app.background_ui_dirty && app.cover_ui_dirty && app.capture_projection_pending());
            }
            _ => assert_eq!(wakeups.get(), 0),
        }
        // Slint measures milliseconds by flooring the platform's tick. An arm
        // at 1000.9 ms can wake at 1040.0 ms, after only 39.1 ms of real time.
        // Feed that legal submillisecond phase into the unchanged application
        // deadline comparison. No OS scheduling luck is required to exercise it.
        clock.set(Duration::from_micros(1_039_900));
        slint::platform::update_timers_and_animations();
        assert_eq!(wakeups.get(), 0);
        let delivery = if mode == "rounded-wake" { 1_040_000 } else { 1_041_000 };
        clock.set(Duration::from_micros(delivery));
        slint::platform::update_timers_and_animations();
        assert_eq!(wakeups.get(), u32::from(matches!(mode.as_str(), "expiry" | "rounded-wake")));
        if matches!(mode.as_str(), "expiry" | "rounded-wake") {
            let now = deadline + Duration::from_micros(delivery - 1_000_900);
            deferred_after_wake = app.defer_last_startup_cover(now);
            // Advance past the physical deadline even when diagnosing an old
            // build that incorrectly relies on a later periodic poll.
            assert!(!app.defer_last_startup_cover(deadline + Duration::from_millis(41)));
            app.cover_inflight = None;
            app.cover_pending.remove(&ids[1]);
            last_pending = !app.cover_pending.is_empty();
            released = app.startup_cover_publication_due();
            assert!(released && !app.startup_cover_publication_due());
        }
        clock.set(Duration::from_micros(1_100_000));
        slint::platform::update_timers_and_animations();
        crate::reader_timing::finish(!deferred_after_wake).unwrap();
        let facts = serde_json::json!({"case":mode,"visible":app.visible_cover_ids().len(),"armed":armed,
            "publication_once_after_last_result":released,"last_still_pending":last_pending,
            "wake_callbacks":wakeups.get(),"deferred_after_spent_timer":deferred_after_wake,
            "rounded_delivery_us":if mode == "rounded-wake" { Some(delivery - 1_000_900) } else { None },
            "grace_limit_ms":FINAL_LOCAL_GRACE.as_millis()});
        std::fs::write(output.with_extension("observations.json"), serde_json::to_vec(&facts).unwrap()).unwrap();
        assert!(!deferred_after_wake,"A spent single-shot timer must never defer the last pending cover for another dispatcher poll");
    }
}
