//! Runtime interface translations shared by Rust projections and Slint labels.

use std::sync::{
    OnceLock,
    atomic::{AtomicU8, Ordering},
};

use serde_json::{Map, Value};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
enum Language {
    SimplifiedChinese,
    TraditionalChinese,
    English,
    Japanese,
}

impl Language {
    fn from_code(code: &str) -> Option<Self> {
        match code {
            "zh-CN" => Some(Self::SimplifiedChinese),
            "zh-TW" => Some(Self::TraditionalChinese),
            "en" => Some(Self::English),
            "ja" => Some(Self::Japanese),
            _ => None,
        }
    }

    fn code(self) -> &'static str {
        match self {
            Self::SimplifiedChinese => "zh-CN",
            Self::TraditionalChinese => "zh-TW",
            Self::English => "en",
            Self::Japanese => "ja",
        }
    }

    fn load() -> Self {
        match LANGUAGE.load(Ordering::Acquire) {
            1 => Self::TraditionalChinese,
            2 => Self::English,
            3 => Self::Japanese,
            _ => Self::SimplifiedChinese,
        }
    }
}

static LANGUAGE: AtomicU8 = AtomicU8::new(Language::SimplifiedChinese as u8);

struct Catalogs {
    simplified: Map<String, Value>,
    traditional: Map<String, Value>,
    english: Map<String, Value>,
    japanese: Map<String, Value>,
}

fn catalogs() -> &'static Catalogs {
    static CATALOGS: OnceLock<Catalogs> = OnceLock::new();
    CATALOGS.get_or_init(|| Catalogs {
        simplified: serde_json::from_str(include_str!("../assets/i18n/zh-CN.json"))
            .expect("Bundled Simplified Chinese translations"),
        traditional: serde_json::from_str(include_str!("../assets/i18n/zh-TW.json"))
            .expect("Bundled Traditional Chinese translations"),
        english: serde_json::from_str(include_str!("../assets/i18n/en.json"))
            .expect("Bundled English translations"),
        japanese: serde_json::from_str(include_str!("../assets/i18n/ja.json"))
            .expect("Bundled Japanese translations"),
    })
}

fn lookup(language: Language, key: &str) -> Option<&'static str> {
    let catalogs = catalogs();
    let selected = match language {
        Language::SimplifiedChinese => &catalogs.simplified,
        Language::TraditionalChinese => &catalogs.traditional,
        Language::English => &catalogs.english,
        Language::Japanese => &catalogs.japanese,
    };
    selected
        .get(key)
        .and_then(Value::as_str)
        .or_else(|| catalogs.english.get(key).and_then(Value::as_str))
        .or_else(|| catalogs.simplified.get(key).and_then(Value::as_str))
}

/// Select the process interface language. Unknown codes leave it unchanged.
/// Returns true only when the selected language changed.
pub fn set_language(code: &str) -> bool {
    let Some(language) = Language::from_code(code) else {
        return false;
    };
    LANGUAGE.swap(language as u8, Ordering::AcqRel) != language as u8
}

pub fn language() -> &'static str {
    Language::load().code()
}

/// Missing locale entries fall back to English, then the original catalog.
pub fn translate(key: &str) -> String {
    lookup(Language::load(), key).unwrap_or(key).to_owned()
}

// Every window remembers its applied locale, so ordinary view synchronization
// checks one string instead of updating every translated property again.
include!(concat!(env!("OUT_DIR"), "/translation_setters.rs"));

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use super::*;

    fn placeholders(value: &str) -> BTreeSet<&str> {
        value
            .split('{')
            .skip(1)
            .filter_map(|part| part.split_once('}').map(|(key, _)| key))
            .collect()
    }

    #[test]
    fn supported_codes_round_trip_and_unknown_codes_are_rejected() {
        for code in ["zh-CN", "zh-TW", "en", "ja"] {
            assert_eq!(Language::from_code(code).unwrap().code(), code);
        }
        assert_eq!(Language::from_code("de"), None);
        assert_eq!(Language::from_code(""), None);
    }

    #[test]
    fn main_navigation_is_translated_for_every_locale() {
        assert_eq!(lookup(Language::English, "library"), Some("Library"));
        assert_eq!(
            lookup(Language::TraditionalChinese, "library"),
            Some("書櫃")
        );
        assert_eq!(lookup(Language::Japanese, "library"), Some("本棚"));
    }

    #[test]
    fn translated_templates_preserve_named_placeholders() {
        let catalogs = catalogs();
        for (locale, translations) in [
            ("en", &catalogs.english),
            ("zh-TW", &catalogs.traditional),
            ("ja", &catalogs.japanese),
        ] {
            for (key, translated) in translations {
                let Some(original) = catalogs.simplified.get(key).and_then(Value::as_str) else {
                    continue;
                };
                assert_eq!(
                    placeholders(original),
                    placeholders(translated.as_str().expect("Translation must be text")),
                    "Placeholder mismatch for {locale}/{key}"
                );
            }
        }
    }

    #[test]
    fn every_supported_catalog_covers_the_bundled_interface() {
        let catalogs = catalogs();
        for (locale, translations) in [
            ("en", &catalogs.english),
            ("zh-TW", &catalogs.traditional),
            ("ja", &catalogs.japanese),
        ] {
            for key in catalogs.simplified.keys() {
                assert!(
                    translations.get(key).and_then(Value::as_str).is_some(),
                    "Missing translation for {locale}/{key}"
                );
            }
        }
    }

    #[test]
    fn unknown_labels_can_fall_back_without_changing_user_text() {
        assert_eq!(lookup(Language::English, "unregistered_label_key"), None);
    }
}
