//! Per-launch performance choices shared by the Library entry and Settings.

use std::{
    fmt,
    fs::{self, OpenOptions},
    io::{ErrorKind, Read, Write},
    path::Path,
    sync::atomic::{AtomicU64, Ordering},
};

use anyhow::{Context, Result, ensure};

const FILENAME: &str = "startup-options";
const LIMIT: usize = 1024;
const KEYS: [&str; 6] = [
    "portrait",
    "prewarm",
    "last_cover_grace",
    "import_thumbnail",
    "first_layout",
    "resident",
];
static SAVE_NUMBER: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StartupOptions {
    pub portrait_native: bool,
    pub prewarm: u8,
    pub last_cover_grace: bool,
    pub import_thumbnail_idle: bool,
    pub first_layout_optimized: bool,
    pub resident_seconds: u8,
}

impl Default for StartupOptions {
    fn default() -> Self {
        Self {
            portrait_native: false,
            prewarm: 0,
            last_cover_grace: false,
            import_thumbnail_idle: false,
            first_layout_optimized: true,
            resident_seconds: 0,
        }
    }
}

impl fmt::Display for StartupOptions {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        for key in KEYS {
            writeln!(out, "{key}={}", self.value(key).expect("known option"))?;
        }
        Ok(())
    }
}

impl StartupOptions {
    pub fn all_enabled() -> Self {
        Self {
            portrait_native: true,
            prewarm: 2,
            last_cover_grace: true,
            import_thumbnail_idle: true,
            first_layout_optimized: true,
            resident_seconds: 45,
        }
    }

    pub fn record_selection(self) {
        if !crate::reader_timing::enabled() {
            return;
        }
        for name in [
            if self.portrait_native {
                "startup_config_portrait_native"
            } else {
                "startup_config_portrait_shell"
            },
            if self.prewarm == 2 {
                "startup_config_prewarm_2"
            } else {
                "startup_config_prewarm_0"
            },
            if self.last_cover_grace {
                "startup_config_last_cover_grace_on"
            } else {
                "startup_config_last_cover_grace_off"
            },
            if self.import_thumbnail_idle {
                "startup_config_import_thumbnail_idle"
            } else {
                "startup_config_import_thumbnail_off"
            },
            if self.first_layout_optimized {
                "startup_config_first_layout_optimized"
            } else {
                "startup_config_first_layout_baseline"
            },
            if self.resident_seconds == 45 {
                "startup_config_resident_45"
            } else {
                "startup_config_resident_off"
            },
        ] {
            crate::reader_timing::mark(name);
        }
    }

    pub fn value(self, key: &str) -> Option<&'static str> {
        Some(match key {
            "portrait" => {
                if self.portrait_native {
                    "native"
                } else {
                    "shell"
                }
            }
            "prewarm" => {
                if self.prewarm == 2 {
                    "2"
                } else {
                    "0"
                }
            }
            "last_cover_grace" => {
                if self.last_cover_grace {
                    "on"
                } else {
                    "off"
                }
            }
            "import_thumbnail" => {
                if self.import_thumbnail_idle {
                    "idle"
                } else {
                    "off"
                }
            }
            "first_layout" => {
                if self.first_layout_optimized {
                    "optimized"
                } else {
                    "baseline"
                }
            }
            "resident" => {
                if self.resident_seconds == 45 {
                    "45"
                } else {
                    "off"
                }
            }
            _ => return None,
        })
    }

    pub fn with_setting(mut self, key: &str, value: &str) -> Option<Self> {
        match (key, value) {
            ("portrait", "shell" | "native") => self.portrait_native = value == "native",
            ("prewarm", "0" | "2") => self.prewarm = if value == "2" { 2 } else { 0 },
            ("last_cover_grace", "off" | "on") => self.last_cover_grace = value == "on",
            ("import_thumbnail", "off" | "idle") => self.import_thumbnail_idle = value == "idle",
            ("first_layout", "baseline" | "optimized") => {
                self.first_layout_optimized = value == "optimized"
            }
            ("resident", "off" | "45") => {
                self.resident_seconds = if value == "45" { 45 } else { 0 }
            }
            _ => return None,
        }
        Some(self)
    }

    fn parse(bytes: &[u8]) -> Self {
        let defaults = Self::default();
        if bytes.len() > LIMIT || !bytes.iter().all(|b| matches!(b, b'\n' | b' '..=b'~')) {
            return defaults;
        }
        let mut options = defaults;
        let mut seen = [false; KEYS.len()];
        for line in bytes.split(|&b| b == b'\n') {
            let Ok(line) = std::str::from_utf8(line) else {
                continue;
            };
            let Some((key, value)) = line.split_once('=') else {
                continue;
            };
            let Some(index) = KEYS.iter().position(|k| *k == key) else {
                continue;
            };
            let fallback = defaults.value(key).expect("known option");
            let value = if seen[index] { fallback } else { value };
            options = options
                .with_setting(key, value)
                .unwrap_or_else(|| options.with_setting(key, fallback).expect("valid default"));
            seen[index] = true;
        }
        options
    }

    pub fn load(data_dir: &Path) -> Self {
        let path = data_dir.join(FILENAME);
        let mut opts = OpenOptions::new();
        opts.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW);
        }
        #[cfg(not(unix))]
        if !fs::symlink_metadata(&path).is_ok_and(|m| m.is_file()) {
            return Self::default();
        }
        let Ok(file) = opts.open(&path) else {
            return Self::default();
        };
        if !file
            .metadata()
            .is_ok_and(|m| m.is_file() && m.len() <= LIMIT as u64)
        {
            return Self::default();
        }
        let mut bytes = Vec::new();
        if file
            .take((LIMIT + 1) as u64)
            .read_to_end(&mut bytes)
            .is_err()
        {
            return Self::default();
        }
        Self::parse(&bytes)
    }

    pub fn save(self, data_dir: &Path) -> Result<()> {
        let path = data_dir.join(FILENAME);
        match fs::symlink_metadata(&path) {
            Ok(metadata) => ensure!(
                metadata.is_file(),
                "The startup options path is not a regular file"
            ),
            Err(error) if error.kind() == ErrorKind::NotFound => (),
            Err(error) => return Err(error).context("Inspect the startup options file"),
        }
        let pending = data_dir.join(format!(
            ".startup-options.{}.{}",
            std::process::id(),
            SAVE_NUMBER.fetch_add(1, Ordering::Relaxed)
        ));
        let mut opts = OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut file = opts
            .open(&pending)
            .context("Create the startup options file")?;
        let result = (|| -> Result<()> {
            write!(file, "{self}")?;
            file.sync_all()?;
            drop(file);
            fs::rename(&pending, &path).context("Publish the startup options")
        })();
        if result.is_err() {
            let _ = fs::remove_file(pending);
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_or_duplicate_keys_cannot_enable_an_option() {
        let got = StartupOptions::parse(b"portrait=native\nportrait=native\nprewarm=2\nresident=999\nfirst_layout=baseline\nother=anything\n");
        assert_eq!(got.prewarm, 2);
        assert!(
            !got.portrait_native,
            "duplicates must select the documented default"
        );
        assert_eq!(got.resident_seconds, 0);
        assert!(!got.first_layout_optimized);
        for raw in [&b"resident=45\0"[..], &vec![b'x'; LIMIT + 1]] {
            assert_eq!(StartupOptions::parse(raw), StartupOptions::default());
        }
    }

    #[test]
    fn user_selection_round_trips_without_changing_other_preferences() {
        let data = tempfile::tempdir().unwrap();
        fs::write(data.path().join("device-profile"), b"pw5\n").unwrap();
        assert_eq!(StartupOptions::load(data.path()), StartupOptions::default());
        let all = StartupOptions::all_enabled();
        all.save(data.path()).unwrap();
        let one = StartupOptions::load(data.path())
            .with_setting("resident", "off")
            .unwrap();
        one.save(data.path()).unwrap();
        assert_eq!(
            StartupOptions::load(data.path()),
            StartupOptions {
                resident_seconds: 0,
                ..all
            }
        );
        assert_eq!(
            fs::read(data.path().join("device-profile")).unwrap(),
            b"pw5\n"
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_link_neither_imports_settings_nor_overwrites_its_target() {
        let data = tempfile::tempdir().unwrap();
        let original = data.path().join("original");
        fs::write(&original, b"prewarm=2\n").unwrap();
        std::os::unix::fs::symlink(&original, data.path().join(FILENAME)).unwrap();
        assert_eq!(StartupOptions::load(data.path()), StartupOptions::default());
        assert!(StartupOptions::all_enabled().save(data.path()).is_err());
        assert_eq!(fs::read(original).unwrap(), b"prewarm=2\n");
    }
}
