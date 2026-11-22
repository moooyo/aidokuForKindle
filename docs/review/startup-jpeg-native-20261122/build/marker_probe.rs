use aidoku_kindle::{platform::FrameCheckpoint, reader_timing::startup_library_submitted};
use std::time::Duration;

fn main() {
    let projection = match std::env::args().nth(1).as_deref() {
        Some("scribe-grid") => (true, true, true),
        Some("scribe-filtered") => (true, true, false),
        Some("paperwhite-empty") => (false, false, false),
        _ => std::process::exit(2),
    };
    let frame = FrameCheckpoint {
        submitted: true,
        sequence: 4,
        marker: Some(5),
        initial: false,
        full: false,
        active: true,
        input_blocked: true,
        render: Duration::ZERO,
        settle: Duration::ZERO,
        write: Duration::ZERO,
        submit: Duration::ZERO,
    };
    startup_library_submitted(frame, projection);
}
