use std::collections::BTreeMap;

use anyhow::{Context, Result, bail, ensure};

use crate::{app::App, startup_options::StartupOptions, tr, ui::SettingItem};

// The off position is independent of the recommended preset, which may enable
// individual switches. The on positions and all recommendations come from the
// same startup policy that the launcher and application use.
const SWITCHES: [(&str, &str, &str); 6] = [
    ("portrait", "startup_native_portrait", "shell"),
    ("prewarm", "startup_cached_covers", "0"),
    ("last_cover_grace", "startup_wait_cover", "off"),
    ("import_thumbnail", "startup_import_covers", "off"),
    ("first_layout", "startup_library_layout", "baseline"),
    ("resident", "startup_quick_reopen", "off"),
];

impl App {
    pub(in crate::app) fn active_startup_values(&self) -> BTreeMap<&'static str, &'static str> {
        SWITCHES
            .iter()
            .filter_map(|&(key, _, _)| self.options.startup_options.value(key).map(|v| (key, v)))
            .collect()
    }

    pub(super) fn startup_setting_rows(&self) -> Vec<SettingItem> {
        let saved = StartupOptions::load(&self.data_dir);
        let all = StartupOptions::all_enabled();
        SWITCHES
            .iter()
            .map(|&(key, label, _)| SettingItem {
                id: key.into(),
                title: tr(label).into(),
                value: if saved.value(key) == all.value(key) {
                    "on"
                } else {
                    "off"
                }
                .into(),
                enabled: true,
                ..SettingItem::default()
            })
            .collect()
    }

    pub(super) fn save_startup_choice(&mut self, action: &str, choice: &str) -> Result<()> {
        ensure!(
            self.route == "settings" && self.interactions.settings_section == "startup-options",
            "Open startup settings before changing a switch"
        );
        let saved = StartupOptions::load(&self.data_dir);
        let next = match (action, choice) {
            ("startup-preset", "all") => StartupOptions::all_enabled(),
            ("startup-preset", "recommended") => StartupOptions::default(),
            ("startup-set", choice) => {
                let (key, position) = choice.split_once('=').context("Missing switch position")?;
                let (_, _, off) = SWITCHES
                    .iter()
                    .find(|row| row.0 == key)
                    .context("Unknown startup switch")?;
                let value = match position {
                    "off" => *off,
                    "on" => StartupOptions::all_enabled()
                        .value(key)
                        .context("Unknown startup option")?,
                    _ => bail!("Unknown startup switch position"),
                };
                saved
                    .with_setting(key, value)
                    .context("Invalid startup option")?
            }
            _ => bail!("Unknown startup preset"),
        };
        // Leave the active snapshot unchanged until a fresh application launch.
        next.save(&self.data_dir)
            .context(tr("startup_save_failed"))?;
        if self.platform.is_kindle()
            && !self.options.demo
            && saved.resident_seconds == 45
            && next.resident_seconds == 0
        {
            self.disable_quick_return();
        }
        self.refresh_setting_details()
    }
}
