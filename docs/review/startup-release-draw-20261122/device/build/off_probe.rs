use aidoku_kindle::{
    platform::{DeviceProfile, FrameCheckpoint},
    reader_timing::{startup_library_submitted_with_costs, startup_stage_main_entry},
};
use std::time::Duration;

fn main() {
    startup_stage_main_entry();
    let profile = match std::env::args().nth(1).as_deref() {
        Some("scribe") => DeviceProfile::Scribe,
        Some("pw6") => DeviceProfile::Pw6,
        _ => std::process::exit(2),
    };
    let library = FrameCheckpoint {
        submitted: true, sequence: 4, marker: Some(5), initial: false, full: false, active: true,
        input_blocked: false, render: Duration::from_micros(19), settle: Duration::ZERO,
        write: Duration::from_micros(3), submit: Duration::from_micros(2),
    };
    let populated = profile.is_scribe();
    startup_library_submitted_with_costs(library, (populated, populated, populated));
}
