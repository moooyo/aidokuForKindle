//! One-shot Library pixel retention. The next GUI still owns a fresh native session.

use crate::{app::AppOptions, reader_timing, render_cache};
use std::{path::Path, process::ExitCode};

#[path = "resident_transport.rs"]
mod transport;

/// Dispatch before parsing regular options or opening any application resources.
pub fn keeper_entry() -> Option<ExitCode> {
    transport::entry()
}

pub(crate) fn library_session() -> bool {
    cfg!(all(target_os = "linux", feature = "kindle"))
        && std::env::var_os("AIDOKU_MANAGED_LIBRARY").as_deref()
            == Some(std::ffi::OsStr::new("1"))
        && std::env::var_os("AIDOKU_LAUNCHER_DIR").is_some_and(|p| Path::new(&p).is_absolute())
        && std::env::var_os("AIDOKU_LAUNCH_NONCE").is_some_and(|n| !n.is_empty())
}

pub(crate) fn prepare(options: &AppOptions) {
    if options.demo || options.resume || options.initial_screen != "library" || !library_session() {
        return;
    }
    if options.startup_options.resident_seconds != 45 {
        discard(&options.data_dir);
        return;
    }
    if let Ok(Some((_peer, blob))) = transport::take(&options.data_dir) {
        if render_cache::resident::install(&options.data_dir, blob) > 0 {
            reader_timing::mark("resident_library_keeper_reclaimed");
            return;
        }
        reader_timing::mark("resident_library_handoff_rejected");
    }
}

pub(crate) fn finish(data_dir: &Path) {
    match render_cache::resident::capture(data_dir) {
        Some(blob) if transport::publish(data_dir, &blob).is_ok() => {
            reader_timing::mark("resident_library_keeper_published");
        }
        _ => {
            discard(data_dir);
            reader_timing::mark("resident_library_keeper_unavailable");
        }
    }
}

pub(crate) fn discard(data_dir: &Path) {
    let _ = transport::stop(data_dir);
    render_cache::resident::clear();
}
