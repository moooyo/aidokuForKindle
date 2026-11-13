use std::{collections::BTreeMap, path::PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct Book {
    pub id: String,
    pub source_id: String,
    pub remote_id: String,
    pub title: String,
    pub author: String,
    pub description: String,
    #[serde(default)]
    pub tags: Vec<String>,
    pub status: String,
    pub cover: String,
    pub chapter_count: u32,
    pub unread: u32,
    pub category: String,
    pub in_library: bool,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct Chapter {
    pub id: String,
    pub book_id: String,
    pub source_id: String,
    pub remote_id: String,
    pub number: f64,
    pub title: String,
    pub date: String,
    pub page_count: u32,
    pub downloaded: bool,
    pub read: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct Page {
    pub url: String,
    #[serde(default)]
    pub headers: BTreeMap<String, String>,
    pub local_path: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CachedPageUpdate {
    pub index: usize,
    pub identity: String,
    pub path: PathBuf,
}

/// Manifest identity is position-sensitive. It is intentionally distinct from
/// the cache's content key, which coalesces identical requests across positions.
pub fn cached_page_identity(page: &Page, index: usize) -> String {
    let mut hash = Sha256::new();
    hash.update((index as u64).to_le_bytes());
    hash.update((page.url.len() as u64).to_le_bytes());
    hash.update(page.url.as_bytes());
    if page.url.is_empty() {
        if let Some(path) = &page.local_path {
            hash.update(path.to_string_lossy().as_bytes());
        }
    }
    for (name, value) in &page.headers {
        hash.update((name.len() as u64).to_le_bytes());
        hash.update(name.as_bytes());
        hash.update((value.len() as u64).to_le_bytes());
        hash.update(value.as_bytes());
    }
    format!("{:x}", hash.finalize())
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct Progress {
    pub book_id: String,
    pub chapter_id: String,
    pub page: u32,
    pub total: u32,
    pub updated_at: i64,
    pub completed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct Bookmark {
    pub book_id: String,
    pub chapter_id: String,
    pub page: u32,
    pub created_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SourceConfig {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub base_url: String,
    #[serde(default)]
    pub username: String,
    #[serde(default)]
    pub password: String,
    #[serde(default)]
    pub token: String,
    pub version: u32,
    #[serde(default)]
    pub languages: Vec<String>,
    pub enabled: bool,
    #[serde(default)]
    pub options: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct DownloadJob {
    pub chapter_id: String,
    pub book_id: String,
    pub state: String,
    pub completed: u32,
    pub total: u32,
    pub error: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Preferences {
    pub reading_direction: String,
    pub tap_layout: String,
    pub reverse_taps: bool,
    pub refresh_every: u32,
    pub flash_on_close: bool,
    pub crop_borders: bool,
    pub dither: bool,
    pub dither_method: String,
    pub contrast: i32,
    pub brightness: u8,
    pub warmth: u8,
    pub double_tap_zoom: bool,
    pub keep_awake: bool,
    pub incognito: bool,
    pub footer_mode: u8,
    pub landscape: bool,
    pub reverse_landscape: bool,
    pub spread: bool,
    pub cover_alone: bool,
    pub night_mode: bool,
    pub ahead_chapters: u8,
    pub resize_downloads: bool,
    pub grayscale_downloads: bool,
    pub auto_update_interval: String,
    pub update_wifi_only: bool,
    pub update_charging_only: bool,
    pub update_skip_completed: bool,
    pub charging_updates: bool,
    pub reading_wifi_off: bool,
    pub screensaver_mode: String,
    pub sleep_cover: bool,
    pub stylus_navigation: bool,
    pub palm_rejection: bool,
    pub split_wide: bool,
    pub language: String,
    pub tracking_auto_sync: bool,
    pub tracking_queue_offline: bool,
    pub write_logs: bool,
}

impl Default for Preferences {
    fn default() -> Self {
        Self {
            reading_direction: "rtl".into(),
            tap_layout: "thirds".into(),
            reverse_taps: false,
            refresh_every: 6,
            flash_on_close: true,
            crop_borders: true,
            dither: true,
            dither_method: "software".into(),
            contrast: 0,
            brightness: 12,
            warmth: 8,
            double_tap_zoom: false,
            keep_awake: true,
            incognito: false,
            footer_mode: 0,
            landscape: false,
            reverse_landscape: false,
            spread: true,
            cover_alone: false,
            night_mode: false,
            ahead_chapters: 0,
            resize_downloads: false,
            grayscale_downloads: false,
            auto_update_interval: "off".into(),
            update_wifi_only: false,
            update_charging_only: false,
            update_skip_completed: false,
            charging_updates: false,
            reading_wifi_off: false,
            screensaver_mode: "cover".into(),
            sleep_cover: true,
            stylus_navigation: false,
            palm_rejection: false,
            split_wide: false,
            language: "zh-CN".into(),
            tracking_auto_sync: false,
            tracking_queue_offline: true,
            write_logs: false,
        }
    }
}

impl Preferences {
    pub fn normalized_dither_method(&self) -> &'static str {
        if self.dither_method == "hardware" {
            "hardware"
        } else {
            "software"
        }
    }

    pub fn uses_hardware_dither(&self, available: bool) -> bool {
        self.dither && self.normalized_dither_method() == "hardware" && available
    }
}

/// A durable navigation checkpoint deliberately excludes credentials, keyboard
/// contents, pairing tokens, and transient source request results.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct NavigationSession {
    pub version: u32,
    pub route: String,
    pub page: usize,
    pub return_route: String,
    pub current_book_id: Option<String>,
    pub current_source: String,
    pub library_filter: i32,
    pub source_filter: i32,
    pub catalog_filter: i32,
    pub history_filter: i32,
    pub newest_first: bool,
    pub sort_title: bool,
    pub unread_only: bool,
    pub downloaded_only: bool,
    pub catalog_unread_only: bool,
    pub catalog_downloaded_only: bool,
    pub catalog_remote_page: u32,
    pub catalog_has_more: bool,
    pub catalog_query: Option<String>,
    pub catalog_book_ids: Vec<String>,
    pub catalog_listings: Vec<(String, String)>,
    pub query: String,
    pub search_scope: String,
    pub search_groups: Vec<SearchSession>,
    pub selection_mode: bool,
    pub selected: Vec<String>,
    pub selected_chapters: Vec<String>,
    pub panel_route: String,
    pub panel_page: usize,
    pub reader: Option<ReaderSession>,
}

impl Default for NavigationSession {
    fn default() -> Self {
        Self {
            version: 1,
            route: "library".into(),
            page: 1,
            return_route: "library".into(),
            current_book_id: None,
            current_source: "mangadex".into(),
            library_filter: 0,
            source_filter: 0,
            catalog_filter: 0,
            history_filter: 0,
            newest_first: true,
            sort_title: false,
            unread_only: false,
            downloaded_only: false,
            catalog_unread_only: false,
            catalog_downloaded_only: false,
            catalog_remote_page: 1,
            catalog_has_more: false,
            catalog_query: None,
            catalog_book_ids: Vec::new(),
            catalog_listings: Vec::new(),
            query: String::new(),
            search_scope: "all".into(),
            search_groups: Vec::new(),
            selection_mode: false,
            selected: Vec::new(),
            selected_chapters: Vec::new(),
            panel_route: String::new(),
            panel_page: 0,
            reader: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct ReaderSession {
    pub book_id: String,
    pub chapter_id: String,
    pub page: u32,
    pub total: u32,
    pub vertical_offset: u32,
    pub wide_half: u8,
    pub wide_page: bool,
    pub scaled_height: u32,
    pub viewport_height: u32,
    pub zoom: f32,
    pub landscape: bool,
    pub spread: bool,
    pub menu_open: bool,
}

impl Default for ReaderSession {
    fn default() -> Self {
        Self {
            book_id: String::new(),
            chapter_id: String::new(),
            page: 1,
            total: 0,
            vertical_offset: 0,
            wide_half: 0,
            wide_page: false,
            scaled_height: 0,
            viewport_height: 0,
            zoom: 1.0,
            landscape: false,
            spread: true,
            menu_open: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
#[serde(default)]
pub struct SearchSession {
    pub id: String,
    pub title: String,
    pub book_ids: Vec<String>,
    pub failed: bool,
    pub has_next: bool,
}

pub fn compound_id(source_id: &str, remote_id: &str) -> String {
    format!("{}:{}:{}", source_id.len(), source_id, remote_id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_dithering_preferences_keep_software_and_off_semantics() {
        for enabled in [false, true] {
            let preferences: Preferences =
                serde_json::from_value(serde_json::json!({"dither": enabled})).unwrap();
            assert_eq!(preferences.dither, enabled);
            assert_eq!(preferences.normalized_dither_method(), "software");
            assert!(!preferences.uses_hardware_dither(true));
        }
        let preferences: Preferences = serde_json::from_value(serde_json::json!({
            "dither": true, "dither_method": "future-method"
        }))
        .unwrap();
        assert_eq!(preferences.normalized_dither_method(), "software");
        assert!(!preferences.uses_hardware_dither(true));
    }

    #[test]
    fn hardware_dithering_preferences_need_both_enablement_and_actual_capability() {
        let mut preferences = Preferences {
            dither_method: "hardware".into(),
            ..Preferences::default()
        };
        assert!(preferences.uses_hardware_dither(true));
        assert!(!preferences.uses_hardware_dither(false));
        let restored: Preferences =
            serde_json::from_str(&serde_json::to_string(&preferences).unwrap()).unwrap();
        assert_eq!(restored, preferences);
        preferences.dither = false;
        assert!(!preferences.uses_hardware_dither(true));
        assert_eq!(preferences.normalized_dither_method(), "hardware");
    }

    #[test]
    fn legacy_landscape_preferences_keep_the_standard_rotation() {
        let preferences: Preferences = serde_json::from_str(r#"{"landscape":true}"#).unwrap();
        assert!(preferences.landscape);
        assert!(!preferences.reverse_landscape);
    }

    #[test]
    fn reverse_landscape_survives_a_preferences_round_trip() {
        let preferences = Preferences {
            landscape: true,
            reverse_landscape: true,
            ..Preferences::default()
        };
        let serialized = serde_json::to_string(&preferences).unwrap();
        let restored: Preferences = serde_json::from_str(&serialized).unwrap();
        assert_eq!(restored, preferences);
    }

    #[test]
    fn legacy_reader_preferences_keep_the_original_layout_defaults() {
        let preferences: Preferences =
            serde_json::from_str(r#"{"tap_layout":"reversed","spread":true}"#).unwrap();
        assert_eq!(preferences.tap_layout, "reversed");
        assert!(!preferences.reverse_taps);
        assert!(!preferences.cover_alone);
    }

    #[test]
    fn isolated_cover_and_independent_tap_reversal_survive_a_round_trip() {
        let preferences = Preferences {
            tap_layout: "l-shaped".into(),
            reverse_taps: true,
            cover_alone: true,
            ..Preferences::default()
        };
        let serialized = serde_json::to_string(&preferences).unwrap();
        let restored: Preferences = serde_json::from_str(&serialized).unwrap();
        assert_eq!(restored, preferences);
    }

    #[test]
    fn legacy_preferences_keep_reader_and_network_defaults() {
        let preferences: Preferences =
            serde_json::from_str(r#"{"reading_direction":"ltr","brightness":7}"#).unwrap();
        let expected = Preferences {
            reading_direction: "ltr".into(),
            brightness: 7,
            ..Preferences::default()
        };
        assert_eq!(preferences, expected);
        assert_eq!(preferences.ahead_chapters, 0);
        assert_eq!(preferences.auto_update_interval, "off");
        assert_eq!(preferences.screensaver_mode, "cover");
        assert!(preferences.sleep_cover);
        assert_eq!(preferences.language, "zh-CN");
        assert!(preferences.tracking_queue_offline);
    }

    #[test]
    fn explicit_legacy_screensaver_modes_survive_missing_sleep_cover() {
        for mode in ["system", "progress", "cover"] {
            let preferences: Preferences = serde_json::from_value(serde_json::json!({
                "screensaver_mode": mode,
            }))
            .unwrap();
            assert_eq!(preferences.screensaver_mode, mode);
            assert!(preferences.sleep_cover);
            let serialized = serde_json::to_string(&preferences).unwrap();
            let restored: Preferences = serde_json::from_str(&serialized).unwrap();
            assert_eq!(restored, preferences);
        }
    }

    #[test]
    fn explicit_sleep_cover_opt_out_survives_a_preferences_round_trip() {
        let preferences: Preferences =
            serde_json::from_str(r#"{"screensaver_mode":"cover","sleep_cover":false}"#).unwrap();
        assert_eq!(preferences.screensaver_mode, "cover");
        assert!(!preferences.sleep_cover);
        let serialized = serde_json::to_string(&preferences).unwrap();
        let restored: Preferences = serde_json::from_str(&serialized).unwrap();
        assert_eq!(restored, preferences);
    }

    #[test]
    fn extended_preferences_survive_a_persistent_round_trip() {
        let preferences = Preferences {
            ahead_chapters: 3,
            resize_downloads: true,
            grayscale_downloads: true,
            auto_update_interval: "weekly".into(),
            update_wifi_only: true,
            update_charging_only: true,
            update_skip_completed: true,
            charging_updates: true,
            reading_wifi_off: true,
            screensaver_mode: "progress".into(),
            sleep_cover: false,
            stylus_navigation: true,
            palm_rejection: true,
            split_wide: true,
            language: "ja".into(),
            tracking_auto_sync: true,
            tracking_queue_offline: false,
            write_logs: true,
            ..Preferences::default()
        };
        let serialized = serde_json::to_string(&preferences).unwrap();
        let restored: Preferences = serde_json::from_str(&serialized).unwrap();
        assert_eq!(restored, preferences);
    }
}
