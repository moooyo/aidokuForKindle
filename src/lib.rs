pub mod app;
pub mod backup;
pub mod cache;
pub mod demo;
pub mod domain;
pub mod downloads;
pub mod gestures;
pub mod imaging;
pub mod input;
pub mod local;
pub mod localization;
#[cfg(all(feature = "kindle", target_os = "linux"))]
pub mod native_presentation;
pub mod pairing;
pub mod platform;
pub mod reader;
pub mod render_cache;
pub mod sources;
pub mod store;
pub mod tracking;

pub mod ui {
    slint::include_modules!();
}

pub fn tr(key: &str) -> String {
    localization::translate(key)
}
