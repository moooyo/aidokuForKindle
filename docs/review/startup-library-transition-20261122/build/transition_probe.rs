use aidoku_kindle::{
    platform::{DeviceProfile, FrameCheckpoint},
    reader_timing::{
        StartupTransition, startup_library_submitted, startup_library_submitted_with_costs,
        startup_stage_initial_frame, startup_stage_input_frame, startup_stage_main_entry,
        startup_transition,
    },
};
use std::time::Duration;

fn main() {
    let case = std::env::args().nth(1).unwrap_or_default();
    let profile = match case.as_str() {
        "scribe" | "no-initial" | "wrong-fence" | "no-main" => DeviceProfile::Scribe,
        "pw6-empty" | "unavailable" => DeviceProfile::Pw6,
        _ => std::process::exit(2),
    };
    if case != "no-main" {
        startup_stage_main_entry();
    }
    let library = FrameCheckpoint {
        submitted: true,
        sequence: 4,
        marker: Some(5),
        initial: false,
        full: false,
        active: true,
        input_blocked: false,
        render: Duration::from_micros(19),
        settle: Duration::ZERO,
        write: Duration::from_micros(3),
        submit: Duration::from_micros(2),
    };
    let full = FrameCheckpoint {
        sequence: 1,
        marker: Some(2),
        initial: true,
        full: true,
        input_blocked: true,
        ..library
    };
    if case != "no-initial" {
        startup_stage_initial_frame(full);
    }
    startup_stage_input_frame(FrameCheckpoint {
        marker: Some(if case == "wrong-fence" { 3 } else { 2 }),
        submitted: false,
        input_blocked: false,
        ..full
    });
    if case != "unavailable" {
        for stage in [
            StartupTransition::MetadataReady,
            StartupTransition::MetadataReceived,
            StartupTransition::FontsReady,
            StartupTransition::FontsReceived,
            StartupTransition::FontsRegistered,
            StartupTransition::AttachEnter,
            StartupTransition::AttachExit,
            StartupTransition::ViewEnter,
            StartupTransition::ViewExit,
            StartupTransition::SceneRequested,
        ] {
            startup_transition(stage);
        }
    }
    let populated = profile.is_scribe();
    let projection = (profile.is_scribe(), populated, populated);
    if case == "unavailable" {
        startup_library_submitted(library, projection);
    } else {
        startup_library_submitted_with_costs(library, projection);
    }
}
