//! One persistent startup scene before background preparation begins.

use std::{path::Path, time::Duration};

use aidoku_kindle::ui::{AppWindow, State};
use anyhow::{Result, ensure};
use slint::ComponentHandle;

pub const ACTIVITY_INTERVAL: Duration = Duration::from_millis(400);

#[derive(Debug, PartialEq, Eq)]
struct StartupStyle {
    language: String,
    dark_mode: bool,
}

fn startup_style(data_dir: &Path, demo: bool) -> StartupStyle {
    use rusqlite::{Connection, OpenFlags};

    let data_dir = if demo {
        data_dir.join("demo")
    } else {
        data_dir.to_owned()
    };
    let document = (|| {
        // Reading one optional row must not create a database, migrate it, or
        // wait behind another connection before the first loading frame.
        let connection = Connection::open_with_flags(
            data_dir.join("library.sqlite3"),
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .ok()?;
        connection.busy_timeout(Duration::ZERO).ok()?;
        let document = connection.query_row(
            "SELECT document FROM preferences WHERE key='application' AND length(document)<=65536",
            [],
            |row| row.get::<_, String>(0),
        ).ok()?;
        serde_json::from_str::<serde_json::Value>(&document).ok()
    })();
    let language = document
        .as_ref()
        .and_then(|value| value.get("language"))
        .and_then(serde_json::Value::as_str)
        .filter(|value| matches!(*value, "zh-CN" | "zh-TW" | "en" | "ja"))
        .unwrap_or("zh-CN");
    StartupStyle {
        language: language.to_owned(),
        dark_mode: document
            .as_ref()
            .and_then(|value| value.get("night_mode"))
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false),
    }
}

/// Keep every locale visible in the same scene before full font registration.
/// Small pre-rasterized labels avoid waiting for the complete CJK font bundle.
pub fn configure_startup(ui: &AppWindow, data_dir: &Path, demo: bool) -> Result<()> {
    let style = startup_style(data_dir, demo);
    aidoku_kindle::localization::set_language(&style.language);
    aidoku_kindle::localization::apply_ui_labels(ui);
    let state = ui.global::<State>();
    state.set_booting(true);
    state.set_startup_dark(style.dark_mode);
    state.set_startup_title(startup_mask(include_bytes!(
        "../assets/startup/title.gray"
    ))?);
    macro_rules! labels {
        ($locale:literal) => {
            [
                include_bytes!(concat!(
                    "../assets/startup/",
                    $locale,
                    "-startup_loading.gray"
                ))
                .as_slice(),
                include_bytes!(concat!(
                    "../assets/startup/",
                    $locale,
                    "-startup_fonts.gray"
                ))
                .as_slice(),
                include_bytes!(concat!("../assets/startup/", $locale, "-startup_data.gray"))
                    .as_slice(),
                include_bytes!(concat!(
                    "../assets/startup/",
                    $locale,
                    "-startup_interface.gray"
                ))
                .as_slice(),
                include_bytes!(concat!("../assets/startup/", $locale, "-exit.gray")).as_slice(),
                include_bytes!(concat!("../assets/startup/", $locale, "-error.gray")).as_slice(),
            ]
        };
    }
    let labels = match style.language.as_str() {
        "zh-TW" => labels!("zh-TW"),
        "en" => labels!("en"),
        "ja" => labels!("ja"),
        _ => labels!("zh-CN"),
    };
    state.set_startup_loading(startup_mask(labels[0])?);
    state.set_startup_fonts(startup_mask(labels[1])?);
    state.set_startup_data(startup_mask(labels[2])?);
    state.set_startup_interface(startup_mask(labels[3])?);
    state.set_startup_exit(startup_mask(labels[4])?);
    state.set_startup_error(startup_mask(labels[5])?);
    Ok(())
}

fn startup_mask(bytes: &[u8]) -> Result<slint::Image> {
    let header = bytes
        .get(..8)
        .ok_or_else(|| anyhow::anyhow!("A startup label has no header"))?;
    let width = u32::from_le_bytes(header[..4].try_into().unwrap());
    let height = u32::from_le_bytes(header[4..].try_into().unwrap());
    let pixels = &bytes[8..];
    ensure!(
        width > 0
            && width <= 2_048
            && height > 0
            && height <= 256
            && (width as usize).checked_mul(height as usize) == Some(pixels.len()),
        "A startup label has invalid dimensions or pixels"
    );
    let mut mask = slint::SharedPixelBuffer::<slint::Rgba8Pixel>::new(width, height);
    for (target, value) in mask.make_mut_slice().iter_mut().zip(pixels) {
        *target = slint::Rgba8Pixel {
            r: 0,
            g: 0,
            b: 0,
            a: 255 - *value,
        };
    }
    Ok(slint::Image::from_rgba8(mask))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn startup_style_reads_only_the_existing_database_and_keeps_demo_isolation() {
        let directory = tempfile::tempdir().unwrap();
        assert_eq!(startup_style(directory.path(), false).language, "zh-CN");
        assert!(!directory.path().join("library.sqlite3").exists());
        let connection =
            rusqlite::Connection::open(directory.path().join("library.sqlite3")).unwrap();
        connection
            .execute_batch("CREATE TABLE preferences(key TEXT PRIMARY KEY, document TEXT NOT NULL)")
            .unwrap();
        connection
            .execute(
                "INSERT INTO preferences(key,document) VALUES('application',?1)",
                [r#"{"language":"ja","night_mode":true}"#],
            )
            .unwrap();
        assert_eq!(
            startup_style(directory.path(), false),
            StartupStyle {
                language: "ja".into(),
                dark_mode: true
            }
        );
        assert_eq!(startup_style(directory.path(), true).language, "zh-CN");
    }

    #[test]
    fn localized_label_masks_preserve_coverage_for_light_and_dark_ink() {
        let label = startup_mask(&[2, 0, 0, 0, 1, 0, 0, 0, 0, 255]).unwrap();
        let pixels = label.to_rgba8().unwrap();
        assert_eq!(pixels.as_bytes(), &[0, 0, 0, 255, 0, 0, 0, 0]);
        for invalid in [
            &[][..],
            &[0, 0, 0, 0, 1, 0, 0, 0][..],
            &[2, 0, 0, 0, 1, 0, 0, 0, 0][..],
        ] {
            assert!(startup_mask(invalid).is_err());
        }
    }
}
