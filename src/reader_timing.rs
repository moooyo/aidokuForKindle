//! Opt-in, bounded Reader timing from the first Rust `main` entry.
//!
//! Trace events identify software ownership boundaries. A successful framebuffer
//! submission is not an observation of physical e-ink completion.

use std::{
    cell::Cell,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::Instant,
};

use anyhow::{Context, Result};
use serde::Serialize;

const EVENT_LIMIT: usize = 16_384;
static TRACE: OnceLock<Recorder> = OnceLock::new();

thread_local! {
    static INPUT_ORIGIN: Cell<&'static str> = const { Cell::new("app_dispatch") };
}

#[cfg(test)]
thread_local! {
    static TEST_TRACE: std::cell::RefCell<Option<TraceState>> = const { std::cell::RefCell::new(None) };
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct RequestToken {
    pub(crate) request_id: u64,
    action_id: Option<u64>,
    reader_generation: u64,
    lifecycle_generation: u64,
}

#[derive(Clone, Debug, Serialize)]
struct Event {
    name: &'static str,
    at_us: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    action_id: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reader_generation: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    lifecycle_generation: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    action_kind: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    input_origin: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    boundary: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    cache_state: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    duration_us: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    frame_sequence: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    marker: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    full: Option<bool>,
}

impl Event {
    fn new(name: &'static str, token: Option<RequestToken>) -> Self {
        Self {
            name,
            at_us: 0,
            request_id: token.map(|token| token.request_id),
            action_id: token.and_then(|token| token.action_id),
            reader_generation: token.map(|token| token.reader_generation),
            lifecycle_generation: token.map(|token| token.lifecycle_generation),
            action_kind: None,
            input_origin: None,
            boundary: None,
            reason: None,
            cache_state: None,
            duration_us: None,
            frame_sequence: None,
            marker: None,
            full: None,
        }
    }
}

struct Recorder {
    epoch: Instant,
    output: PathBuf,
    state: Mutex<TraceState>,
}

struct TraceState {
    events: Vec<Event>,
    event_limit: usize,
    dropped_events: u64,
    last_at_us: u64,
    next_action: u64,
    next_request: u64,
    pending_action: Option<u64>,
    deferred_action: Option<u64>,
    current: Option<RequestToken>,
    current_ui_accepted: bool,
    pending_ui: Option<RequestToken>,
    native_pending: Option<(RequestToken, Option<u64>)>,
    finished: bool,
}

impl TraceState {
    fn new(event_limit: usize) -> Self {
        Self {
            events: Vec::with_capacity(event_limit),
            event_limit,
            dropped_events: 0,
            last_at_us: 0,
            next_action: 0,
            next_request: 0,
            pending_action: None,
            deferred_action: None,
            current: None,
            current_ui_accepted: false,
            pending_ui: None,
            native_pending: None,
            finished: false,
        }
    }

    fn record(&mut self, mut event: Event, at_us: u64) {
        if self.finished {
            return;
        }
        self.last_at_us = self.last_at_us.max(at_us);
        event.at_us = self.last_at_us;
        if self.events.len() < self.event_limit {
            self.events.push(event);
        } else {
            self.dropped_events = self.dropped_events.saturating_add(1);
        }
    }

    fn action(&mut self, kind: &'static str, origin: &'static str, at_us: u64) -> u64 {
        if let Some(action_id) = self.pending_action.take()
            && self.deferred_action == Some(action_id)
        {
            let mut cancelled = Event::new("action_cancelled", None);
            cancelled.action_id = Some(action_id);
            cancelled.reason = Some("superseded");
            self.record(cancelled, at_us);
        }
        self.next_action = self.next_action.wrapping_add(1).max(1);
        self.pending_action = Some(self.next_action);
        self.deferred_action = None;
        let mut event = Event::new("action_received", None);
        event.action_id = self.pending_action;
        event.action_kind = Some(kind);
        event.input_origin = Some(origin);
        event.boundary = Some("app_intent_received");
        self.record(event, at_us);
        self.next_action
    }

    fn end_action(&mut self, action_id: u64) {
        if self.pending_action == Some(action_id) && self.deferred_action != Some(action_id) {
            self.pending_action = None;
        }
    }

    fn request(
        &mut self,
        reader_generation: u64,
        lifecycle_generation: u64,
        at_us: u64,
    ) -> RequestToken {
        if let Some(current) = self.current
            && current.reader_generation == reader_generation
            && current.lifecycle_generation == lifecycle_generation
        {
            return current;
        }
        let action_id = self.pending_action.take().or_else(|| {
            (!self.current_ui_accepted)
                .then(|| self.current.and_then(|token| token.action_id))
                .flatten()
        });
        self.deferred_action = None;
        self.invalidate("superseded", at_us);
        self.next_request = self.next_request.wrapping_add(1).max(1);
        let token = RequestToken {
            request_id: self.next_request,
            action_id,
            reader_generation,
            lifecycle_generation,
        };
        self.current = Some(token);
        self.current_ui_accepted = false;
        self.record(Event::new("request_created", Some(token)), at_us);
        token
    }

    fn invalidate(&mut self, reason: &'static str, at_us: u64) {
        if let Some(token) = self.current.take() {
            let mut event = Event::new("request_invalidated", Some(token));
            event.reason = Some(reason);
            self.record(event, at_us);
        }
        self.native_pending = None;
        self.pending_ui = None;
        self.current_ui_accepted = false;
    }

    fn token(&self, reader_generation: u64, lifecycle_generation: u64) -> Option<RequestToken> {
        self.current.filter(|token| {
            token.reader_generation == reader_generation
                && token.lifecycle_generation == lifecycle_generation
        })
    }

    fn fail(&mut self, token: RequestToken, at_us: u64) {
        if self.current != Some(token) {
            return;
        }
        let mut event = Event::new("request_failed", Some(token));
        event.reason = Some("failed");
        self.record(event, at_us);
        self.current = None;
        self.current_ui_accepted = false;
        self.native_pending = None;
        self.pending_ui = None;
    }

    fn retry(
        &mut self,
        reader_generation: u64,
        lifecycle_generation: u64,
        at_us: u64,
    ) -> Option<RequestToken> {
        let token = self.token(reader_generation, lifecycle_generation)?;
        if self.pending_action.is_none() && !self.current_ui_accepted {
            self.pending_action = token.action_id;
        }
        self.invalidate("superseded", at_us);
        Some(self.request(reader_generation, lifecycle_generation, at_us))
    }

    fn accept(&mut self, token: RequestToken, native: bool, at_us: u64) -> bool {
        if self.current != Some(token) || self.current_ui_accepted {
            return false;
        }
        self.record(Event::new("ui_image_ready", Some(token)), at_us);
        self.current_ui_accepted = true;
        self.native_pending = native.then_some((token, None));
        true
    }

    fn native(
        &mut self,
        token_id: u64,
        frame_sequence: u64,
        duration_us: u64,
        full: bool,
        marker: Option<u32>,
        at_us: u64,
    ) {
        let Some((token, written_sequence)) = self.native_pending else {
            return;
        };
        if token.request_id != token_id || self.current != Some(token) {
            return;
        }
        if marker.is_some() && written_sequence != Some(frame_sequence) {
            return;
        }
        if marker.is_none() && written_sequence.is_some() {
            return;
        }
        let mut event = Event::new(
            if marker.is_some() {
                "send_accepted"
            } else {
                "framebuffer_written"
            },
            Some(token),
        );
        event.duration_us = Some(duration_us);
        event.frame_sequence = Some(frame_sequence);
        event.marker = marker;
        event.full = Some(full);
        event.boundary = Some(if marker.is_some() {
            "driver_submission_accepted"
        } else {
            "framebuffer_bytes_written"
        });
        self.record(event, at_us);
        self.native_pending = if marker.is_some() {
            None
        } else {
            Some((token, Some(frame_sequence)))
        };
    }
}

fn micros(value: std::time::Duration) -> u64 {
    value.as_micros().min(u128::from(u64::MAX)) as u64
}

fn update<R>(apply: impl FnOnce(&mut TraceState, u64) -> R) -> Option<R> {
    #[cfg(test)]
    if TEST_TRACE.with(|trace| trace.borrow().is_some()) {
        return TEST_TRACE.with(|trace| {
            let mut trace = trace.borrow_mut();
            let state = trace.as_mut().expect("The test trace is active");
            let at_us = state.last_at_us.saturating_add(1);
            Some(apply(state, at_us))
        });
    }
    let recorder = TRACE.get()?;
    let mut state = recorder
        .state
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let at_us = micros(recorder.epoch.elapsed());
    Some(apply(&mut state, at_us))
}

pub fn enabled() -> bool {
    #[cfg(test)]
    if TEST_TRACE.with(|trace| trace.borrow().is_some()) {
        return true;
    }
    TRACE.get().is_some()
}

#[cfg(test)]
pub(crate) fn capture_test_events<R>(apply: impl FnOnce() -> R) -> (R, serde_json::Value) {
    struct ResetTrace;
    impl Drop for ResetTrace {
        fn drop(&mut self) {
            TEST_TRACE.with(|trace| *trace.borrow_mut() = None);
        }
    }
    TEST_TRACE.with(|trace| {
        assert!(trace.borrow().is_none(), "Test traces cannot be nested");
        *trace.borrow_mut() = Some(TraceState::new(EVENT_LIMIT));
    });
    let _reset = ResetTrace;
    let result = apply();
    let trace = TEST_TRACE.with(|trace| trace.borrow_mut().take().unwrap());
    (result, serde_json::to_value(trace.events).unwrap())
}

/// Enable in-memory timing. No output file is opened until `finish`.
pub fn enable(path: Option<&Path>, process_entry: Instant) -> Result<()> {
    let Some(path) = path else {
        return Ok(());
    };
    TRACE
        .set(Recorder {
            epoch: process_entry,
            output: path.to_owned(),
            state: Mutex::new(TraceState::new(EVENT_LIMIT)),
        })
        .map_err(|_| anyhow::anyhow!("Performance timing is already enabled"))?;
    update(|state, _| state.record(Event::new("process_entry", None), 0));
    Ok(())
}

pub fn mark(name: &'static str) {
    update(|state, at_us| state.record(Event::new(name, None), at_us));
}

pub fn with_scripted_input<R>(pointer: bool, apply: impl FnOnce() -> R) -> R {
    if !enabled() {
        return apply();
    }
    struct RestoreOrigin(&'static str);
    impl Drop for RestoreOrigin {
        fn drop(&mut self) {
            INPUT_ORIGIN.set(self.0);
        }
    }
    let previous = INPUT_ORIGIN.replace(if pointer {
        "scripted_pointer"
    } else {
        "scripted_intent"
    });
    let _restore = RestoreOrigin(previous);
    apply()
}

pub(crate) struct ActionGuard(u64);

impl Drop for ActionGuard {
    fn drop(&mut self) {
        update(|state, _| state.end_action(self.0));
    }
}

pub(crate) fn action(kind: &'static str) -> Option<ActionGuard> {
    if enabled() {
        let origin = INPUT_ORIGIN.get();
        update(|state, at_us| state.action(kind, origin, at_us)).map(ActionGuard)
    } else {
        None
    }
}

pub(crate) fn defer_action() {
    update(|state, _| state.deferred_action = state.pending_action);
}

pub(crate) fn action_failed() {
    update(|state, at_us| {
        if let Some(action_id) = state.pending_action.take() {
            let mut event = Event::new("action_failed", None);
            event.action_id = Some(action_id);
            event.reason = Some("failed");
            state.record(event, at_us);
            state.deferred_action = None;
        }
    });
}

pub(crate) fn cancel_action(reason: &'static str) {
    update(|state, at_us| {
        if let Some(action_id) = state.pending_action.take() {
            let mut event = Event::new("action_cancelled", None);
            event.action_id = Some(action_id);
            event.reason = Some(reason);
            state.record(event, at_us);
        }
        state.deferred_action = None;
    });
}

pub(crate) fn request(reader_generation: u64, lifecycle_generation: u64) -> Option<RequestToken> {
    update(|state, at_us| state.request(reader_generation, lifecycle_generation, at_us))
}

pub(crate) fn retry(reader_generation: u64, lifecycle_generation: u64) {
    update(|state, at_us| state.retry(reader_generation, lifecycle_generation, at_us));
}

pub(crate) fn token(reader_generation: u64, lifecycle_generation: u64) -> Option<RequestToken> {
    update(|state, _| state.token(reader_generation, lifecycle_generation)).flatten()
}

pub(crate) fn record(name: &'static str, token: Option<RequestToken>) {
    if let Some(token) = token {
        update(|state, at_us| state.record(Event::new(name, Some(token)), at_us));
    }
}

pub(crate) fn invalidate(reason: &'static str) {
    update(|state, at_us| state.invalidate(reason, at_us));
}

pub(crate) fn failed(token: Option<RequestToken>) {
    if let Some(token) = token {
        update(|state, at_us| state.fail(token, at_us));
    }
}

pub(crate) fn prepare_publication(token: Option<RequestToken>) {
    if let Some(token) = token {
        update(|state, _| {
            if state.current == Some(token) {
                state.pending_ui = Some(token);
            }
        });
    }
}

pub(crate) fn publish(
    reader_generation: u64,
    lifecycle_generation: u64,
    native: bool,
) -> Option<u64> {
    update(|state, at_us| {
        let token = state.pending_ui.take()?;
        if state.token(reader_generation, lifecycle_generation) != Some(token) {
            return None;
        }
        state
            .accept(token, native, at_us)
            .then_some(token.request_id)
    })
    .flatten()
}

pub struct Stage {
    token: Option<RequestToken>,
    started: Option<Instant>,
    event: &'static str,
    cache_state: Option<&'static str>,
}

impl Stage {
    pub(crate) fn new(token: Option<RequestToken>, event: &'static str) -> Self {
        Self {
            token,
            started: token.map(|_| Instant::now()),
            event,
            cache_state: None,
        }
    }

    pub(crate) fn render_result(&mut self, cache_hit: bool, memory_hit: bool) {
        if self.token.is_some() {
            self.cache_state = Some(match (cache_hit, memory_hit) {
                (true, true) => "memory",
                (true, false) => "disk",
                (false, false) => "cold",
                (false, true) => "inconsistent",
            });
        }
    }
}

impl Drop for Stage {
    fn drop(&mut self) {
        if let (Some(token), Some(started)) = (self.token, self.started) {
            let duration_us = micros(started.elapsed());
            update(|state, at_us| {
                let mut event = Event::new(self.event, Some(token));
                event.duration_us = Some(duration_us);
                event.cache_state = self.cache_state;
                state.record(event, at_us);
            });
        }
    }
}

pub fn native_frame_written(token: u64, frame_sequence: u64, elapsed_us: u64, full: bool) {
    update(|state, at_us| state.native(token, frame_sequence, elapsed_us, full, None, at_us));
}

pub fn native_send_accepted(
    token: u64,
    frame_sequence: u64,
    marker: u32,
    elapsed_us: u64,
    full: bool,
) {
    update(|state, at_us| {
        state.native(token, frame_sequence, elapsed_us, full, Some(marker), at_us)
    });
}

pub fn native_frame_unavailable(token_id: u64, reason: &'static str) {
    let reason = match reason {
        "no_write" => "no_write",
        "send_rejected" => "send_rejected",
        "superseded" => "superseded",
        _ => "cancelled",
    };
    update(|state, at_us| {
        if let Some((token, _)) = state.native_pending
            && token.request_id == token_id
        {
            let mut event = Event::new("native_frame_unavailable", Some(token));
            event.reason = Some(reason);
            state.record(event, at_us);
            state.native_pending = None;
        }
    });
}

pub(crate) fn cancel_native(reason: &'static str) {
    update(|state, at_us| {
        if let Some((token, _)) = state.native_pending.take() {
            let mut event = Event::new("native_frame_unavailable", Some(token));
            event.reason = Some(reason);
            state.record(event, at_us);
        }
        if state.pending_ui.is_some() {
            state.invalidate(reason, at_us);
        }
    });
}

/// Flush only after the application has stopped its timed runtime work.
pub fn finish(success: bool) -> Result<()> {
    let Some(recorder) = TRACE.get() else {
        return Ok(());
    };
    let mut state = recorder
        .state
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    if state.finished {
        return Ok(());
    }
    state.record(
        Event::new("process_finished", None),
        micros(recorder.epoch.elapsed()),
    );
    state.finished = true;
    let output = serde_json::json!({
        "schema_version": 1,
        "clock": "monotonic_rust_main",
        "process_id": std::process::id(),
        "scope": "Software boundaries only; not OS exec, physical touch, panel completion, ghosting or battery latency.",
        "enabled": true,
        "finished": true,
        "success": success,
        "event_limit": state.event_limit,
        "dropped_events": state.dropped_events,
        "events": state.events,
    });
    let bytes = serde_json::to_vec_pretty(&output).context("Serialize the performance trace")?;
    drop(state);
    if let Some(parent) = recorder
        .output
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
    {
        fs::create_dir_all(parent).context("Create the performance trace directory")?;
    }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut file = options
        .open(&recorder.output)
        .context("Create a new performance trace")?;
    file.write_all(&bytes)
        .context("Write the performance trace")?;
    file.write_all(b"\n")
        .context("Finish the performance trace")?;
    file.sync_all()
        .context("Synchronize the performance trace")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bounded_records_report_loss_without_replacing_earlier_evidence() {
        let mut state = TraceState::new(2);
        state.record(Event::new("process_entry", None), 0);
        state.record(Event::new("window_shown", None), 10);
        state.record(Event::new("process_finished", None), 20);
        assert_eq!(state.events.len(), 2);
        assert_eq!(state.dropped_events, 1);
        assert_eq!(state.events[0].name, "process_entry");
        assert_eq!(state.events[1].at_us, 10);
    }

    #[test]
    fn stale_request_and_wrong_frame_cannot_complete_current_submission() {
        let mut state = TraceState::new(32);
        state.action("next", "scripted_intent", 1);
        let old = state.request(1, 0, 2);
        assert!(state.accept(old, true, 3));
        state.action("next", "scripted_intent", 4);
        let current = state.request(2, 0, 5);
        assert!(!state.accept(old, true, 6));
        assert!(state.accept(current, true, 7));
        state.native(old.request_id, 10, 1, false, None, 8);
        state.native(current.request_id, 11, 1, false, Some(99), 9);
        state.native(current.request_id, 12, 1, false, None, 10);
        state.native(current.request_id, 13, 1, false, Some(99), 11);
        state.native(current.request_id, 12, 1, false, Some(99), 12);
        state.native(current.request_id, 12, 1, false, Some(99), 13);
        let writes: Vec<_> = state
            .events
            .iter()
            .filter(|event| event.name == "framebuffer_written")
            .collect();
        let sends: Vec<_> = state
            .events
            .iter()
            .filter(|event| event.name == "send_accepted")
            .collect();
        assert_eq!(writes.len(), 1);
        assert_eq!(sends.len(), 1);
        assert_eq!(sends[0].request_id, Some(current.request_id));
        assert_eq!(sends[0].frame_sequence, Some(12));
    }

    #[test]
    fn lifecycle_invalidation_clears_an_accepted_native_arm() {
        let mut state = TraceState::new(16);
        let token = state.request(1, 1, 1);
        assert!(state.accept(token, true, 2));
        state.invalidate("lifecycle", 3);
        state.native(token.request_id, 1, 1, true, None, 4);
        assert!(
            state
                .events
                .iter()
                .all(|event| event.name != "framebuffer_written")
        );
        assert!(state.token(1, 1).is_none());
    }

    #[test]
    fn cross_chapter_wait_preserves_the_received_action_identity() {
        let mut state = TraceState::new(16);
        state.action("next_chapter", "scripted_intent", 1);
        state.invalidate("invalidated", 10);
        let token = state.request(20, 2, 1_000);
        assert_eq!(token.action_id, Some(1));
        assert_eq!(state.events[0].at_us, 1);
        assert_eq!(state.events[1].at_us, 1_000);
    }

    #[test]
    fn schema_contains_only_controlled_labels_and_numeric_identities() {
        let mut state = TraceState::new(8);
        state.action("seek", "app_dispatch", 10);
        let token = state.request(3, 5, 11);
        assert!(state.accept(token, true, 12));
        let value = serde_json::to_value(&state.events).unwrap();
        assert_eq!(value[0]["boundary"], "app_intent_received");
        assert_eq!(value[1]["reader_generation"], 3);
        assert_eq!(value[1]["lifecycle_generation"], 5);
        assert!(value.as_array().unwrap().iter().all(|event| {
            event.as_object().unwrap().keys().all(|key| {
                !["path", "book_id", "chapter_id", "url", "error", "value"].contains(&key.as_str())
            })
        }));
    }
    #[test]
    fn completed_turn_cannot_lend_its_action_to_an_unrelated_rerender() {
        let mut state = TraceState::new(32);
        state.action("next", "scripted_intent", 1);
        let turn = state.request(1, 0, 2);
        assert!(state.accept(turn, true, 3));
        state.native(turn.request_id, 1, 1, false, None, 4);
        state.native(turn.request_id, 1, 1, false, Some(1), 5);
        let rerender = state.request(2, 0, 6);
        assert_eq!(turn.action_id, Some(1));
        assert_eq!(rerender.action_id, None);
    }

    #[test]
    fn unpublished_geometry_resolution_retains_its_causal_turn() {
        let mut state = TraceState::new(16);
        state.action("previous", "app_dispatch", 1);
        let unresolved = state.request(10, 0, 2);
        let resolved = state.request(11, 0, 3);
        assert_eq!(resolved.action_id, unresolved.action_id);
        assert_eq!(resolved.action_id, Some(1));
    }

    #[test]
    fn action_scope_clears_unused_menu_but_keeps_explicit_manifest_wait() {
        let mut state = TraceState::new(16);
        let menu = state.action("menu", "app_dispatch", 1);
        state.end_action(menu);
        assert_eq!(state.request(1, 0, 2).action_id, None);
        let chapter = state.action("next_chapter", "scripted_intent", 3);
        state.deferred_action = state.pending_action;
        state.end_action(chapter);
        assert_eq!(state.request(2, 0, 20).action_id, Some(chapter));
    }

    #[test]
    fn desktop_publication_does_not_invent_a_native_failure() {
        let mut state = TraceState::new(16);
        let token = state.request(1, 0, 1);
        assert!(state.accept(token, false, 2));
        assert!(state.native_pending.is_none());
        state.native(token.request_id, 1, 1, false, None, 3);
        assert!(state.events.iter().all(|event| !matches!(
            event.name,
            "framebuffer_written" | "send_accepted" | "native_frame_unavailable"
        )));
    }
    #[test]
    fn same_generation_retry_has_a_new_identity_and_discards_late_failure() {
        let mut state = TraceState::new(32);
        state.action("next", "scripted_intent", 1);
        let old = state.request(8, 2, 2);
        let retry = state.retry(8, 2, 3).unwrap();
        assert_ne!(old.request_id, retry.request_id);
        assert_eq!(old.action_id, retry.action_id);
        state.fail(old, 4);
        assert_eq!(state.current, Some(retry));
        assert!(
            !state
                .events
                .iter()
                .any(|event| event.name == "request_failed")
        );
        let next = state.request(9, 2, 5);
        assert_eq!(state.retry(8, 2, 6), None);
        assert_eq!(state.current, Some(next));
    }
}
