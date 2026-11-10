//! Bounded, opt-in application events without provider or account data.

use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard},
};

use anyhow::{Context, Result, ensure};
use serde::Serialize;

const MAX_LOG_BYTES: u64 = 2 * 1024 * 1024;
const CURRENT_FILE: &str = "application.jsonl";
const PREVIOUS_FILE: &str = "application.previous.jsonl";

/// Only these event identifiers can reach the log. URLs, paths, titles, user
/// input, credentials and arbitrary error strings are intentionally excluded.
const ALLOWED_EVENTS: &[&str] = &[
    "app.start",
    "app.stop",
    "app.suspend",
    "app.resume",
    "reader.frame",
    "reader.error",
    "download.started",
    "download.completed",
    "download.failed",
    "source.configured",
    "source.failed",
    "backup.created",
    "backup.restored",
    "logs.exported",
    "logs.cleared",
    "settings.changed",
    "tracking.synced",
    "library.updated",
];

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum LogOutcome {
    Success,
    Failure,
    Cancelled,
    Unavailable,
}

#[derive(Clone, Debug)]
pub(super) struct LogSummary {
    pub enabled: bool,
    pub files: usize,
    pub bytes: u64,
}

struct LogState {
    root: PathBuf,
    exports: PathBuf,
    enabled: bool,
}

#[derive(Clone)]
pub(super) struct LogManager {
    state: Arc<Mutex<LogState>>,
}

impl LogManager {
    pub fn open(data_dir: &Path, enabled: bool) -> Result<Self> {
        ensure!(
            data_dir.is_dir(),
            "The application data directory is missing"
        );
        let data_dir = data_dir
            .canonicalize()
            .context("Resolve the application data directory")?;
        let root = data_dir.join("logs");
        ensure_directory(&root)?;
        let exports = data_dir.join("exports").join("logs");
        Ok(Self {
            state: Arc::new(Mutex::new(LogState {
                root,
                exports,
                enabled,
            })),
        })
    }

    pub fn set_enabled(&self, enabled: bool) -> Result<()> {
        self.lock()?.enabled = enabled;
        Ok(())
    }

    /// Record a small typed event. Unrecognized identifiers become `unknown`
    /// rather than being copied into a diagnostic export.
    pub fn record(&self, event: &str, outcome: LogOutcome) -> Result<()> {
        let state = self.lock()?;
        if !state.enabled {
            return Ok(());
        }
        ensure_directory(&state.root)?;
        let event = if ALLOWED_EVENTS.contains(&event) {
            event
        } else {
            "unknown"
        };
        let mut line = serde_json::to_vec(&serde_json::json!({
            "time": chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            "event": event,
            "outcome": outcome,
        }))?;
        line.push(b'\n');
        let current = state.root.join(CURRENT_FILE);
        let current_bytes = file_bytes(&current)?.unwrap_or(0);
        ensure!(
            current_bytes <= MAX_LOG_BYTES,
            "The application log exceeds its byte limit"
        );
        if current_bytes.saturating_add(line.len() as u64) > MAX_LOG_BYTES {
            let previous = state.root.join(PREVIOUS_FILE);
            if file_bytes(&previous)?.is_some() {
                fs::remove_file(&previous).context("Remove the previous application log")?;
            }
            fs::rename(&current, &previous).context("Rotate the application log")?;
        }
        let mut file = open_log_file(&current, true)?;
        file.write_all(&line)
            .context("Append an application event")?;
        Ok(())
    }

    pub fn summary(&self) -> Result<LogSummary> {
        let state = self.lock()?;
        ensure_directory(&state.root)?;
        let mut summary = LogSummary {
            enabled: state.enabled,
            files: 0,
            bytes: 0,
        };
        for name in [PREVIOUS_FILE, CURRENT_FILE] {
            if let Some(bytes) = file_bytes(&state.root.join(name))? {
                ensure!(
                    bytes <= MAX_LOG_BYTES,
                    "An application log exceeds its byte limit"
                );
                summary.files += 1;
                summary.bytes += bytes;
            }
        }
        Ok(summary)
    }

    /// Export the previous and current events to a new local file. This does
    /// not start a network service or include databases, images or credentials.
    pub fn export(&self) -> Result<PathBuf> {
        let state = self.lock()?;
        ensure_directory(&state.root)?;
        ensure_directory(
            state
                .exports
                .parent()
                .context("Resolve the export directory")?,
        )?;
        ensure_directory(&state.exports)?;
        let mut entropy = [0_u8; 8];
        getrandom::fill(&mut entropy)
            .map_err(|error| anyhow::anyhow!("Generate a log export identifier: {error}"))?;
        let suffix: String = entropy.iter().map(|byte| format!("{byte:02x}")).collect();
        let path = state.exports.join(format!(
            "aidoku-logs-{}-{suffix}.jsonl",
            chrono::Utc::now().format("%Y%m%dT%H%M%SZ")
        ));
        // A pre-existing name is never part of error cleanup. Creating the
        // output before the fallible copy also makes this safe on Windows.
        let mut output = create_private_file(&path)?;
        let result = (|| {
            let mut header = serde_json::to_vec(&serde_json::json!({
                "type": "aidoku-event-export",
                "version": 1,
                "application_version": env!("CARGO_PKG_VERSION"),
                "events_enabled": state.enabled,
                "created": chrono::Utc::now().to_rfc3339(),
                "scope": "Typed application events only; no provider or account data",
            }))?;
            header.push(b'\n');
            output.write_all(&header)?;
            for name in [PREVIOUS_FILE, CURRENT_FILE] {
                let source = state.root.join(name);
                let Some(bytes) = file_bytes(&source)? else {
                    continue;
                };
                ensure!(
                    bytes <= MAX_LOG_BYTES,
                    "An application log exceeds its byte limit"
                );
                let mut input = open_log_file(&source, false)?.take(MAX_LOG_BYTES + 1);
                let written = std::io::copy(&mut input, &mut output)?;
                ensure!(
                    written <= MAX_LOG_BYTES,
                    "An application log grew beyond its export limit"
                );
            }
            output.sync_all().context("Sync the local log export")?;
            Ok(path.clone())
        })();
        drop(output);
        if result.is_err() {
            // This name was created by this operation; user exports and other
            // files in the directory are never removed during error cleanup.
            if file_bytes(&path).ok().flatten().is_some() {
                let _ = fs::remove_file(&path);
            }
        }
        result
    }

    /// Remove only the two owned log names. Explicit exports remain available.
    pub fn clear(&self) -> Result<()> {
        let state = self.lock()?;
        ensure_directory(&state.root)?;
        for name in [PREVIOUS_FILE, CURRENT_FILE] {
            let path = state.root.join(name);
            if file_bytes(&path)?.is_some() {
                fs::remove_file(&path).context("Clear an application log")?;
            }
        }
        Ok(())
    }

    fn lock(&self) -> Result<MutexGuard<'_, LogState>> {
        self.state
            .lock()
            .map_err(|_| anyhow::anyhow!("The application log lock is poisoned"))
    }
}

fn ensure_directory(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => ensure!(
            metadata.is_dir() && !metadata.file_type().is_symlink(),
            "An application log directory is not a real directory"
        ),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(path).context("Create an application log directory")?;
            let metadata = fs::symlink_metadata(path)?;
            ensure!(
                metadata.is_dir() && !metadata.file_type().is_symlink(),
                "The application log directory changed during creation"
            );
        }
        Err(error) => return Err(error).context("Inspect an application log directory"),
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn file_bytes(path: &Path) -> Result<Option<u64>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            ensure!(
                metadata.is_file() && !metadata.file_type().is_symlink(),
                "An application log path is not a regular file"
            );
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                ensure!(
                    metadata.nlink() == 1,
                    "An application log has multiple links"
                );
            }
            Ok(Some(metadata.len()))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error).context("Inspect an application log"),
    }
}

fn create_private_file(path: &Path) -> Result<File> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    options.open(path).context("Create a local log export")
}

fn open_log_file(path: &Path, append: bool) -> Result<File> {
    let exists = file_bytes(path)?.is_some();
    let mut options = OpenOptions::new();
    if append {
        options.append(true);
        if !exists {
            options.create_new(true);
        }
    } else {
        options.read(true);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path).context("Open an application log")?;
    let metadata = file.metadata()?;
    ensure!(
        metadata.is_file(),
        "The opened application log is not a file"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        ensure!(
            metadata.nlink() == 1,
            "The opened application log has multiple links"
        );
        file.set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    Ok(file)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disabled_events_do_not_create_log_files() {
        let temporary = tempfile::tempdir().unwrap();
        let manager = LogManager::open(temporary.path(), false).unwrap();
        manager.record("app.start", LogOutcome::Success).unwrap();
        assert_eq!(manager.summary().unwrap().files, 0);
        manager.set_enabled(true).unwrap();
        manager.record("app.start", LogOutcome::Success).unwrap();
        assert_eq!(manager.summary().unwrap().files, 1);
    }

    #[test]
    fn arbitrary_event_text_is_never_exported() {
        let temporary = tempfile::tempdir().unwrap();
        let manager = LogManager::open(temporary.path(), true).unwrap();
        let secret = "https://provider.invalid/?password=secret-token";
        manager.record(secret, LogOutcome::Failure).unwrap();
        let exported = fs::read_to_string(manager.export().unwrap()).unwrap();
        assert!(!exported.contains(secret));
        assert!(!exported.contains("secret-token"));
        assert!(exported.contains("unknown"));
        assert!(exported.contains("failure"));
    }

    #[test]
    fn rotation_keeps_two_bounded_owned_files() {
        let temporary = tempfile::tempdir().unwrap();
        let manager = LogManager::open(temporary.path(), true).unwrap();
        let root = temporary.path().join("logs");
        fs::write(root.join(CURRENT_FILE), vec![b' '; MAX_LOG_BYTES as usize]).unwrap();
        manager.record("app.start", LogOutcome::Success).unwrap();
        let summary = manager.summary().unwrap();
        assert_eq!(summary.files, 2);
        assert!(summary.bytes <= MAX_LOG_BYTES * 2);
        assert_eq!(
            fs::metadata(root.join(PREVIOUS_FILE)).unwrap().len(),
            MAX_LOG_BYTES
        );
        fs::write(root.join(CURRENT_FILE), vec![b' '; MAX_LOG_BYTES as usize]).unwrap();
        manager.record("app.stop", LogOutcome::Success).unwrap();
        assert_eq!(manager.summary().unwrap().files, 2);
        let current = fs::read_to_string(root.join(CURRENT_FILE)).unwrap();
        assert!(current.contains("app.stop"));
        assert!(!current.contains("app.start"));
    }

    #[test]
    fn clear_preserves_exports_and_unrelated_files() {
        let temporary = tempfile::tempdir().unwrap();
        let manager = LogManager::open(temporary.path(), true).unwrap();
        manager.record("app.start", LogOutcome::Success).unwrap();
        let exported = manager.export().unwrap();
        let unrelated = temporary.path().join("logs").join("keep.txt");
        fs::write(&unrelated, b"retained").unwrap();
        manager.clear().unwrap();
        assert_eq!(manager.summary().unwrap().files, 0);
        assert!(exported.is_file());
        assert_eq!(fs::read(unrelated).unwrap(), b"retained");
    }

    #[test]
    fn oversized_external_logs_are_rejected_without_exporting_them() {
        let temporary = tempfile::tempdir().unwrap();
        let manager = LogManager::open(temporary.path(), true).unwrap();
        let current = temporary.path().join("logs").join(CURRENT_FILE);
        fs::write(&current, vec![b' '; MAX_LOG_BYTES as usize + 1]).unwrap();
        assert!(manager.summary().is_err());
        assert!(manager.export().is_err());
        assert!(manager.record("app.start", LogOutcome::Success).is_err());
        assert_eq!(fs::metadata(current).unwrap().len(), MAX_LOG_BYTES + 1);
    }

    #[test]
    fn cloned_managers_share_opt_in_state() {
        let temporary = tempfile::tempdir().unwrap();
        let manager = LogManager::open(temporary.path(), false).unwrap();
        let cloned = manager.clone();
        cloned.set_enabled(true).unwrap();
        manager.record("app.start", LogOutcome::Success).unwrap();
        assert!(cloned.summary().unwrap().enabled);
        assert_eq!(cloned.summary().unwrap().files, 1);
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_directories_and_files_are_rejected() {
        use std::os::unix::fs::symlink;

        let temporary = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        symlink(outside.path(), temporary.path().join("logs")).unwrap();
        assert!(LogManager::open(temporary.path(), true).is_err());
        fs::remove_file(temporary.path().join("logs")).unwrap();
        let manager = LogManager::open(temporary.path(), true).unwrap();
        let target = outside.path().join("keep.jsonl");
        fs::write(&target, b"retained").unwrap();
        symlink(&target, temporary.path().join("logs").join(CURRENT_FILE)).unwrap();
        assert!(manager.record("app.start", LogOutcome::Success).is_err());
        assert!(manager.clear().is_err());
        assert!(manager.export().is_err());
        assert_eq!(fs::read(target).unwrap(), b"retained");
    }
}
