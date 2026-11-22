use aidoku_kindle::{
    platform::{DeviceProfile, FrameCheckpoint},
    reader_timing::{
        startup_library_submitted, startup_stage_initial_frame, startup_stage_main_entry,
    },
};
use std::time::Duration;

fn main() {
    startup_stage_main_entry();
    let (profile, populated, submit_initial) = match std::env::args().nth(1).as_deref() {
        Some("scribe") => (DeviceProfile::Scribe, true, true),
        Some("pw6") => (DeviceProfile::Pw6, false, true),
        Some("scribe-no-initial") => (DeviceProfile::Scribe, true, false),
        _ => std::process::exit(2),
    };
    let library = FrameCheckpoint {
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
    startup_stage_initial_frame(library);
    if submit_initial {
        startup_stage_initial_frame(FrameCheckpoint {
            sequence: 1,
            marker: Some(2),
            initial: true,
            full: true,
            ..library
        });
    }
    startup_library_submitted(library, (profile.is_scribe(), populated, populated));
}
