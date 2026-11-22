use aidoku_kindle::{
    platform::{DeviceProfile, FrameCheckpoint},
    reader_timing::{startup_library_submitted_with_costs, startup_stage_main_entry},
};
use slint_backend_kindle::draw_diagnostics::{DrawAccounting, ThreadCounters};
use std::time::Duration;

fn main() {
    startup_stage_main_entry();
    let case = std::env::args().nth(1).unwrap_or_default();
    let profile = match case.as_str() {
        "scribe" | "bad-marker" | "missing-frame" | "initial" => DeviceProfile::Scribe,
        "pw6" | "cpu-missing" | "counters-missing" | "both-missing" => DeviceProfile::Pw6,
        _ => std::process::exit(2),
    };
    let (width, height) = profile.native_size();
    let counters = ThreadCounters { minor_faults: 0, major_faults: 1, voluntary_switches: 2, involuntary_switches: 3 };
    let draw = DrawAccounting {
        thread: (!matches!(case.as_str(), "cpu-missing" | "both-missing")).then_some(Duration::from_micros(20)),
        counters: (!matches!(case.as_str(), "counters-missing" | "both-missing")).then_some(counters),
        width, height, gray: profile.is_scribe(),
    };
    let library = FrameCheckpoint {
        draw: (case != "missing-frame").then_some(draw),
        submitted: true, active: true, input_blocked: false, initial: case == "initial", full: false,
        sequence: 4, marker: (case != "bad-marker").then_some(5),
        render: Duration::from_micros(19), settle: Duration::ZERO,
        write: Duration::from_micros(3), submit: Duration::from_micros(2),
    };
    startup_library_submitted_with_costs(library, (profile.is_scribe(), profile.is_scribe(), profile.is_scribe()));
}
