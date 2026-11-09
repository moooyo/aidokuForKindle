pub mod app;
pub mod cache;
pub mod demo;
pub mod domain;
pub mod downloads;
pub mod gestures;
pub mod imaging;
pub mod input;
pub mod local;
#[cfg(all(feature = "kindle", target_os = "linux"))]
pub mod native_presentation;
pub mod pairing;
pub mod platform;
pub mod reader;
pub mod render_cache;
pub mod sources;
pub mod store;

pub mod ui {
    slint::include_modules!();
}

pub fn tr(key: &str) -> String {
    static LABELS: std::sync::OnceLock<serde_json::Value> = std::sync::OnceLock::new();
    LABELS
        .get_or_init(|| {
            serde_json::from_str(include_str!("../assets/i18n/zh-CN.json"))
                .expect("Bundled translations")
        })
        .get(key)
        .and_then(serde_json::Value::as_str)
        .unwrap_or(key)
        .to_owned()
}
