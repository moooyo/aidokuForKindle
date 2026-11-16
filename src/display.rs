//! Reader display presets, independent of panel precision and image decoding.

use serde::{Deserialize, Serialize};

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DisplayMode {
    Fast,
    HighQuality,
    Kindle,
    #[default]
    #[serde(other)]
    Normal,
}

impl DisplayMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Fast => "fast",
            Self::Normal => "normal",
            Self::HighQuality => "high_quality",
            Self::Kindle => "kindle",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "fast" => Some(Self::Fast),
            "normal" => Some(Self::Normal),
            "high_quality" | "high-quality" => Some(Self::HighQuality),
            "kindle" => Some(Self::Kindle),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn presets_have_stable_persisted_names() {
        for mode in [
            DisplayMode::Fast,
            DisplayMode::Normal,
            DisplayMode::HighQuality,
            DisplayMode::Kindle,
        ] {
            assert_eq!(DisplayMode::parse(mode.as_str()), Some(mode));
            assert_eq!(
                serde_json::from_str::<DisplayMode>(&serde_json::to_string(&mode).unwrap())
                    .unwrap(),
                mode
            );
        }
        assert_eq!(
            DisplayMode::parse("high-quality"),
            Some(DisplayMode::HighQuality)
        );
        assert_eq!(DisplayMode::parse("automatic"), None);
        assert_eq!(
            serde_json::from_str::<DisplayMode>("\"future_mode\"").unwrap(),
            DisplayMode::Normal
        );
    }

    #[test]
    fn legacy_preferences_default_to_normal_and_fast_keeps_one_software_owner() {
        let mut preferences: crate::domain::Preferences = serde_json::from_str("{}").unwrap();
        assert_eq!(preferences.display_mode, DisplayMode::Normal);
        preferences.dither_method = "hardware".into();
        assert!(preferences.uses_hardware_dither(true));
        preferences.display_mode = DisplayMode::Fast;
        assert!(!preferences.uses_hardware_dither(true));
        for mode in [
            DisplayMode::Normal,
            DisplayMode::HighQuality,
            DisplayMode::Kindle,
        ] {
            preferences.display_mode = mode;
            assert!(preferences.uses_hardware_dither(true));
            assert!(!preferences.uses_hardware_dither(false));
        }
    }
}
