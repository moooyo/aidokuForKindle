//! Validated settings shared by the optional application features.

use anyhow::{Context, Result, ensure};

use crate::{domain::Preferences, ui::State};

/// Return false for a setting owned by the core reader preference handler.
pub(super) fn set(preferences: &mut Preferences, key: &str, value: &str) -> Result<bool> {
    match key.replace('-', "_").as_str() {
        "ahead_chapters" => {
            let count = value.parse::<u8>().context("Parse advance chapter count")?;
            ensure!(
                count <= 3,
                "Advance chapter count must be between zero and three"
            );
            preferences.ahead_chapters = count;
        }
        "resize_downloads" => preferences.resize_downloads = boolean(value)?,
        "grayscale_downloads" => preferences.grayscale_downloads = boolean(value)?,
        "auto_update_interval" => {
            ensure!(
                matches!(value, "off" | "daily" | "weekly"),
                "Unsupported automatic update interval"
            );
            preferences.auto_update_interval = value.into();
        }
        "update_wifi_only" => preferences.update_wifi_only = boolean(value)?,
        "update_charging_only" => preferences.update_charging_only = boolean(value)?,
        "update_skip_completed" => preferences.update_skip_completed = boolean(value)?,
        "charging_updates" => preferences.charging_updates = boolean(value)?,
        "reading_wifi_off" => preferences.reading_wifi_off = boolean(value)?,
        "screensaver_mode" => {
            ensure!(
                matches!(value, "system" | "cover" | "progress"),
                "Unsupported screensaver mode"
            );
            preferences.screensaver_mode = value.into();
            preferences.sleep_cover = value == "cover";
        }
        "sleep_cover" => {
            let enabled = boolean(value)?;
            preferences.sleep_cover = enabled;
            preferences.screensaver_mode = if enabled { "cover" } else { "system" }.into();
        }
        "stylus_navigation" => preferences.stylus_navigation = boolean(value)?,
        "palm_rejection" => preferences.palm_rejection = boolean(value)?,
        "split_wide" => preferences.split_wide = boolean(value)?,
        "language" => {
            ensure!(
                matches!(value, "zh-CN" | "zh-TW" | "en" | "ja"),
                "Unsupported interface language"
            );
            preferences.language = value.into();
        }
        "tracking_auto_sync" => preferences.tracking_auto_sync = boolean(value)?,
        "tracking_queue_offline" => preferences.tracking_queue_offline = boolean(value)?,
        "write_logs" => preferences.write_logs = boolean(value)?,
        _ => return Ok(false),
    }
    Ok(true)
}

fn boolean(value: &str) -> Result<bool> {
    value
        .parse()
        .context("A switch value must be true or false")
}

/// Project persisted preferences without changing runtime capability flags.
pub(super) fn sync(state: &State, preferences: &Preferences) {
    state.set_ahead_chapters(i32::from(preferences.ahead_chapters));
    state.set_resize_downloads(preferences.resize_downloads);
    state.set_grayscale_downloads(preferences.grayscale_downloads);
    state.set_auto_update_interval(preferences.auto_update_interval.as_str().into());
    state.set_update_wifi_only(preferences.update_wifi_only);
    state.set_update_charging_only(preferences.update_charging_only);
    state.set_update_skip_completed(preferences.update_skip_completed);
    state.set_charging_updates(preferences.charging_updates);
    state.set_reading_wifi_off(preferences.reading_wifi_off);
    state.set_screensaver_mode(preferences.screensaver_mode.as_str().into());
    state.set_sleep_cover(preferences.sleep_cover);
    state.set_stylus_navigation(preferences.stylus_navigation);
    state.set_palm_rejection(preferences.palm_rejection);
    state.set_split_wide(preferences.split_wide);
    state.set_language(preferences.language.as_str().into());
    state.set_tracking_auto_sync(preferences.tracking_auto_sync);
    state.set_tracking_queue_offline(preferences.tracking_queue_offline);
    state.set_write_logs(preferences.write_logs);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_switch_accepts_both_command_key_spellings() {
        let switches: [(&str, fn(&Preferences) -> bool); 14] = [
            ("resize_downloads", |value| value.resize_downloads),
            ("grayscale_downloads", |value| value.grayscale_downloads),
            ("update_wifi_only", |value| value.update_wifi_only),
            ("update_charging_only", |value| value.update_charging_only),
            ("update_skip_completed", |value| value.update_skip_completed),
            ("charging_updates", |value| value.charging_updates),
            ("reading_wifi_off", |value| value.reading_wifi_off),
            ("sleep_cover", |value| value.sleep_cover),
            ("stylus_navigation", |value| value.stylus_navigation),
            ("palm_rejection", |value| value.palm_rejection),
            ("split_wide", |value| value.split_wide),
            ("tracking_auto_sync", |value| value.tracking_auto_sync),
            ("tracking_queue_offline", |value| {
                value.tracking_queue_offline
            }),
            ("write_logs", |value| value.write_logs),
        ];
        for (key, read) in switches {
            let mut preferences = Preferences::default();
            assert!(set(&mut preferences, &key.replace('_', "-"), "true").unwrap());
            assert!(read(&preferences), "{key}");
            assert!(set(&mut preferences, key, "false").unwrap());
            assert!(!read(&preferences), "{key}");
        }
    }

    #[test]
    fn constrained_preferences_accept_only_supported_values() {
        let mut preferences = Preferences::default();
        for count in 0..=3 {
            assert!(set(&mut preferences, "ahead-chapters", &count.to_string()).unwrap());
            assert_eq!(preferences.ahead_chapters, count);
        }
        for interval in ["off", "daily", "weekly"] {
            assert!(set(&mut preferences, "auto-update-interval", interval).unwrap());
            assert_eq!(preferences.auto_update_interval, interval);
        }
        for mode in ["system", "cover", "progress"] {
            assert!(set(&mut preferences, "screensaver-mode", mode).unwrap());
            assert_eq!(preferences.screensaver_mode, mode);
            assert_eq!(preferences.sleep_cover, mode == "cover");
        }
        for language in ["zh-CN", "zh-TW", "en", "ja"] {
            assert!(set(&mut preferences, "language", language).unwrap());
            assert_eq!(preferences.language, language);
        }
    }

    #[test]
    fn sleep_cover_switch_and_screensaver_mode_stay_consistent() {
        let mut preferences = Preferences {
            screensaver_mode: "progress".into(),
            sleep_cover: false,
            ..Preferences::default()
        };
        assert!(set(&mut preferences, "sleep-cover", "true").unwrap());
        assert!(preferences.sleep_cover);
        assert_eq!(preferences.screensaver_mode, "cover");
        assert!(set(&mut preferences, "sleep_cover", "false").unwrap());
        assert!(!preferences.sleep_cover);
        assert_eq!(preferences.screensaver_mode, "system");
    }

    #[test]
    fn rejected_values_and_unknown_keys_preserve_the_entire_preferences() {
        let mut preferences = Preferences::default();
        let original = preferences.clone();
        for (key, value) in [
            ("ahead-chapters", "4"),
            ("ahead_chapters", "-1"),
            ("ahead-chapters", "256"),
            ("resize-downloads", "1"),
            ("write_logs", "TRUE"),
            ("sleep_cover", "enabled"),
            ("auto_update_interval", "hourly"),
            ("screensaver_mode", "custom"),
            ("language", "de"),
        ] {
            assert!(set(&mut preferences, key, value).is_err(), "{key}={value}");
            assert_eq!(preferences, original);
        }
        assert!(!set(&mut preferences, "crop-borders", "true").unwrap());
        assert!(!set(&mut preferences, "unknown-setting", "true").unwrap());
        assert_eq!(preferences, original);
    }
}
