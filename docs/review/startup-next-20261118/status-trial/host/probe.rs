//! Standalone host exerciser. The build copies the two production modules verbatim.
#![allow(dead_code)]

mod native_query;
mod platform_startup_toolbar;

use platform_startup_toolbar::{StartupToolbar, ViewStatus};
use std::{cell::RefCell, env, fs, io::{Read, Write}, path::Path, process, thread, time::{Duration, Instant}};

pub struct DeviceStatus { time: String, battery: u8, wifi: bool, free_bytes: u64 }

mod reader_timing {
    use super::*;
    thread_local! { static MARKS: RefCell<Vec<&'static str>> = const { RefCell::new(Vec::new()) }; }
    pub fn mark(value: &'static str) { MARKS.with_borrow_mut(|m| m.push(value)); }
    pub fn marks() -> Vec<&'static str> { MARKS.with_borrow(Clone::clone) }
}

fn append(file: &str, data: &[u8]) {
    fs::OpenOptions::new().create(true).append(true).open(file).unwrap().write_all(data).unwrap();
}

fn mock_helper(mode: &str) {
    assert_eq!(env::args().skip(1).collect::<Vec<_>>(), ["--session"]);
    append("helper-pids", format!("{}\n", process::id()).as_bytes());
    let mut out = std::io::stdout().lock();
    out.write_all(if mode == "bad-hello" { b"AQ2\n" } else { b"AQ1\n" }).unwrap();
    out.flush().unwrap();
    if mode == "bad-hello" { return; }
    let mut input = std::io::stdin().lock();
    let mut opcode = [0; 1];
    while input.read(&mut opcode).unwrap() == 1 {
        append("helper-opcodes", &opcode);
        let op = opcode[0];
        assert!(matches!(op, 4 | 5));
        if matches!(mode, "timeout" | "cancel") { thread::sleep(Duration::from_secs(8)); }
        if mode == "wrong-tag" { out.write_all(&[5, 0, 0, 0]).unwrap(); out.flush().unwrap(); return; }
        if mode == "short-reply" { out.write_all(&[op, 0]).unwrap(); out.flush().unwrap(); return; }
        let data: &[u8] = match (op, mode) {
            (4, "bad-wifi") => b"unknown", (4, "ok-offline") => b"NotConnected", (4, _) => b"connected",
            (5, "bad-battery") => b"101", (5, _) => b"37", _ => unreachable!(),
        };
        out.write_all(&[op, 0, 0, data.len() as u8]).unwrap();
        out.write_all(data).unwrap(); out.flush().unwrap();
    }
    if mode == "trailing" { out.write_all(b"X").unwrap(); out.flush().unwrap(); }
    if mode == "bad-exit" { process::exit(2); }
}

fn observed() -> Vec<u8> { fs::read("helper-opcodes").unwrap_or_default() }

fn verify_retired(count: usize) {
    let data = fs::read_to_string("helper-pids").unwrap_or_default();
    assert_eq!(data.lines().count(), count);
    for pid in data.lines().map(|n| n.parse::<i32>().unwrap()) {
        let mut status = 0;
        // Validate absence as an owned live child or a waitable zombie.
        let result = unsafe { libc::waitpid(pid, &mut status, libc::WNOHANG) };
        assert_eq!(result, -1, "unreaped helper");
        assert_eq!(std::io::Error::last_os_error().raw_os_error(), Some(libc::ECHILD));
        assert!(!Path::new(&format!("/proc/{pid}")).exists(), "helper still running");
    }
}

fn main() {
    let mode = env::var("FIXTURE_CASE").unwrap();
    if env::args().next().unwrap().ends_with("/aidoku-native-query") { return mock_helper(&mode); }
    let mut toolbar = StartupToolbar::default();
    if mode == "sleep-before-send" { toolbar.retire(); }
    reader_timing::mark("fixture_initial_frame_send");
    let start = Instant::now();
    toolbar.observe(|| platform_startup_toolbar::query(&|| {
        mode == "pre-cancel" || mode == "cancel" && !observed().is_empty()
    }));
    let elapsed = start.elapsed().as_millis();
    verify_retired(if matches!(mode.as_str(), "sleep-before-send" | "pre-cancel") { 0 } else { 1 });
    reader_timing::mark("fixture_driver_input_ready");
    let calls = observed();
    let expected: &[u8] = match mode.as_str() {
        "sleep-before-send" | "pre-cancel" | "bad-hello" => &[],
        "wrong-tag" | "short-reply" | "bad-wifi" | "cancel" | "timeout" => &[4],
        _ => &[4, 5],
    };
    assert_eq!(calls, expected);
    assert!(elapsed < 1000, "host guard exceeded one second");
    if mode == "timeout" { assert!((60..1000).contains(&elapsed)); }
    toolbar.observe(|| panic!("a duplicate frame must not repeat the read"));
    toolbar.invalidate(true);
    if mode == "sleep-before-view" { toolbar.retire(); }
    if mode == "invalidate-before-view" { toolbar.invalidate(false); }
    let t = if mode == "stale" { Instant::now() + Duration::from_secs(2) } else { Instant::now() };
    reader_timing::mark("fixture_first_view_started");
    if matches!(mode.as_str(), "ok" | "ok-offline" | "sleep-after-view") {
        let val = ViewStatus { time: "18:03".into(), battery: 37, wifi: mode != "ok-offline", free_bytes: 987_654_321 };
        assert_eq!(toolbar.view(t, || ("18:03".into(), 987_654_321)), Some(val.clone()));
        assert_eq!(toolbar.view(t + Duration::from_secs(4), || panic!("cover flush read local fields again")), Some(val.clone()));
        assert_eq!(toolbar.cached(), Some(val.clone()));
        assert_eq!(reader_timing::marks(), ["fixture_initial_frame_send", "startup_toolbar_probe_started",
            "native_query_session_ready", "native_query_response", "native_query_response",
            "native_query_session_finished", "startup_toolbar_probe_ready", "fixture_driver_input_ready",
            "fixture_first_view_started", "startup_toolbar_snapshot_used"]);
        if mode == "sleep-after-view" { toolbar.retire(); }
        let next = if mode == "sleep-after-view" { t } else { t + Duration::from_secs(30) };
        assert!(toolbar.view(next, || panic!("stale or retired result")).is_none());
        assert_eq!(toolbar.cached(), Some(val));
    } else {
        assert!(toolbar.view(t, || panic!("untrusted or incomplete sample used")).is_none());
        assert!(toolbar.view(t, || panic!("reused failed probe")).is_none());
        assert!(toolbar.cached().is_none());
        let marks = reader_timing::marks();
        assert!(!marks.contains(&"startup_toolbar_snapshot_used"));
        if mode != "sleep-before-send" {
            assert_eq!(marks.iter().filter(|x| **x == "startup_toolbar_sync_fallback").count(), 1);
        }
    }
    assert_eq!(observed(), calls);
    println!("{}|{}|{}|{}", mode, expected.len(), elapsed, reader_timing::marks().join(","));
}
