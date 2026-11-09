//! Static Aidoku source settings, shared by the UI and the portable WASM host.
//!
//! The setting names follow the MIT-licensed Aidoku Rust source API schema.
//! Copyright (c) 2022-2025 Skittyblock; see aidoku-rs.LICENSE.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

use anyhow::{Context, Result, anyhow, bail, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::domain::SourceConfig;

const MAX_FILE_BYTES: usize = 1024 * 1024;
const MAX_DEFAULTS_FILE_BYTES: usize = 16 * 1024 * 1024;
const MAX_SETTINGS: usize = 1024;
const MAX_NESTING: usize = 16;
const MAX_STRING_BYTES: usize = 64 * 1024;
static NEXT_TEMPORARY: AtomicU64 = AtomicU64::new(1);

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceSetting {
    pub key: String,
    pub title: String,
    pub kind: String,
    pub value: Value,
    /// Actual select values, or display labels for a segment's integer indices.
    pub options: Vec<String>,
}

#[derive(Default, Serialize, Deserialize)]
struct Defaults {
    #[serde(default)]
    values: BTreeMap<String, Vec<u8>>,
}

#[derive(Clone)]
struct Definition {
    key: String,
    title: String,
    kind: String,
    default: Value,
    options: Vec<String>,
    minimum: Option<f64>,
    maximum: Option<f64>,
    step: Option<f64>,
    guards: Vec<(String, bool)>,
}

impl Definition {
    fn supported(&self) -> bool {
        matches!(
            self.kind.as_str(),
            "text"
                | "secure-text"
                | "switch"
                | "stepper"
                | "select"
                | "multi-select"
                | "segment"
                | "picker"
                | "editable-list"
        )
    }

    fn enabled(&self, values: &BTreeMap<String, Value>) -> bool {
        self.guards.iter().all(|(key, expected)| {
            values.get(key).and_then(Value::as_bool).unwrap_or(false) == *expected
        })
    }
}

/// Loads editable controls without evaluating a source module or making a
/// network request. Unsupported actions keep their original `kind` so that the
/// UI can display them as unavailable rather than turn them into text fields.
pub fn load(data_dir: &Path, config: &SourceConfig) -> Result<Vec<SourceSetting>> {
    let definitions = read_definitions(data_dir, config)?;
    let defaults = read_defaults(data_dir, config)?;
    let values = effective_values(config, &definitions, &defaults)?;
    Ok(definitions
        .iter()
        .map(|definition| SourceSetting {
            key: definition.key.clone(),
            title: definition.title.clone(),
            kind: if definition.enabled(&values) {
                definition.kind.clone()
            } else {
                format!("disabled:{}", definition.kind)
            },
            value: values.get(&definition.key).cloned().unwrap_or(Value::Null),
            options: definition.options.clone(),
        })
        .collect())
}

/// Encodes schema defaults using each control's actual wire type. In
/// particular, steppers are f32 values even when JSON writes an integer.
pub(super) fn encoded_defaults(
    data_dir: &Path,
    config: &SourceConfig,
) -> Result<BTreeMap<String, Vec<u8>>> {
    let definitions = read_definitions(data_dir, config)?;
    let defaults = read_defaults(data_dir, config)?;
    let values = effective_values(config, &definitions, &defaults)?;
    definitions
        .iter()
        .filter(|definition| definition.supported() && !definition.key.is_empty())
        .map(|definition| {
            let value = values.get(&definition.key).unwrap_or(&definition.default);
            Ok((definition.key.clone(), encode(definition, value)?))
        })
        .collect()
}

/// Returns the new persistent source configuration and commits the same value
/// to the host's Postcard preferences. Passing null resets the schema default.
/// The application remains responsible for saving the returned configuration.
pub fn with_option(
    data_dir: &Path,
    config: &SourceConfig,
    key: &str,
    value: Value,
) -> Result<SourceConfig> {
    ensure!(
        !reserved(key),
        "The source option {key} is reserved for package metadata"
    );
    let definitions = read_definitions(data_dir, config)?;
    let definition = definitions
        .iter()
        .find(|definition| definition.key == key)
        .ok_or_else(|| anyhow!("The source does not define the option {key}"))?;
    ensure!(
        definition.supported(),
        "Unsupported source setting control: {}",
        definition.kind
    );
    let mut defaults = read_defaults(data_dir, config)?;
    let values = effective_values(config, &definitions, &defaults)?;
    if !value.is_null() {
        ensure!(
            definition.enabled(&values),
            "The source setting {key} requires another option to change first"
        );
    }

    let mut updated = config.clone();
    // Older configurations may hold defaults in a nested object. A direct edit
    // replaces that value as well, including when resetting to the schema.
    if let Some(Value::Object(nested)) = updated.options.get_mut("defaults") {
        nested.remove(key);
    }
    if value.is_null() {
        defaults.values.remove(key);
        updated.options.remove(key);
    } else {
        let value = normalize(definition, &value)?;
        defaults
            .values
            .insert(key.to_owned(), encode(definition, &value)?);
        updated.options.insert(key.to_owned(), value);
    }
    ensure!(
        defaults.values.len() <= MAX_SETTINGS,
        "The source preference count exceeds its limit"
    );
    ensure!(
        defaults
            .values
            .iter()
            .map(|(key, value)| key.len() + value.len())
            .sum::<usize>()
            <= MAX_FILE_BYTES,
        "Source preference data exceeds 1 MiB"
    );
    let encoded = serde_json::to_vec_pretty(&defaults).context("Encode source preferences")?;
    ensure!(
        encoded.len() <= MAX_DEFAULTS_FILE_BYTES,
        "Source preference storage exceeds 16 MiB"
    );
    atomic_write(&defaults_path(data_dir, config), &encoded)?;
    Ok(updated)
}

fn read_definitions(data_dir: &Path, config: &SourceConfig) -> Result<Vec<Definition>> {
    let package = config
        .options
        .get("package_dir")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .unwrap_or_else(|| data_dir.join("sources").join(&config.id));
    let package = if package.is_absolute() {
        package
    } else {
        data_dir.join(package)
    };
    let path = package.join("settings.json");
    if !path.exists() {
        return Ok(Vec::new());
    }
    let bytes = read_limited(&path, "source settings", MAX_FILE_BYTES)?;
    let schema: Value = serde_json::from_slice(&bytes).context("Decode source settings")?;
    let items = schema
        .as_array()
        .or_else(|| schema.get("settings").and_then(Value::as_array))
        .ok_or_else(|| anyhow!("The source settings root must be an array"))?;
    let mut definitions = Vec::new();
    flatten(items, "", &[], 0, &mut definitions)?;
    let mut keys = BTreeSet::new();
    for definition in &definitions {
        if !definition.key.is_empty() {
            ensure!(
                keys.insert(definition.key.clone()),
                "Duplicate source setting key: {}",
                definition.key
            );
        }
    }
    Ok(definitions)
}

fn flatten(
    items: &[Value],
    group_title: &str,
    inherited_guards: &[(String, bool)],
    depth: usize,
    definitions: &mut Vec<Definition>,
) -> Result<()> {
    ensure!(
        depth <= MAX_NESTING,
        "The source settings nesting limit was exceeded"
    );
    for item in items {
        ensure!(
            definitions.len() < MAX_SETTINGS,
            "The source setting count exceeds its limit"
        );
        let object = item
            .as_object()
            .ok_or_else(|| anyhow!("A source setting must be an object"))?;
        let original_kind = object
            .get("type")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let title = localized(object.get("title"));
        let title = if title.is_empty() {
            group_title.to_owned()
        } else {
            title
        };
        let mut guards = inherited_guards.to_vec();
        if let Some(required) = object.get("requires").and_then(Value::as_str) {
            guards.push((required.into(), true));
        }
        if let Some(required) = object.get("requiresFalse").and_then(Value::as_str) {
            guards.push((required.into(), false));
        }
        if matches!(original_kind, "group" | "page") {
            let children = object
                .get("items")
                .and_then(Value::as_array)
                .ok_or_else(|| anyhow!("The source {original_kind} setting has no items array"))?;
            flatten(children, &title, &guards, depth + 1, definitions)?;
            continue;
        }
        let key = object
            .get("key")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        ensure!(key.len() <= 1024, "A source setting key exceeds its limit");
        let kind = if original_kind == "text"
            && object.get("secure").and_then(Value::as_bool) == Some(true)
        {
            "secure-text".to_owned()
        } else {
            original_kind.to_owned()
        };
        let option_field = if kind == "segment" {
            "options"
        } else {
            "values"
        };
        let options: Vec<String> = object
            .get(option_field)
            .and_then(Value::as_array)
            .map(|values| values.iter().map(|value| localized(Some(value))).collect())
            .unwrap_or_default();
        ensure!(
            options.len() <= MAX_SETTINGS,
            "The source setting option count exceeds its limit"
        );
        let minimum = object.get("minimumValue").and_then(Value::as_f64);
        let maximum = object.get("maximumValue").and_then(Value::as_f64);
        let step = object.get("stepValue").and_then(Value::as_f64);
        if let (Some(minimum), Some(maximum)) = (minimum, maximum) {
            ensure!(
                minimum <= maximum,
                "The source setting {key} has an invalid numeric range"
            );
        }
        if let Some(step) = step {
            ensure!(step > 0.0, "The source setting {key} has an invalid step");
        }
        let default = object
            .get("default")
            .cloned()
            .unwrap_or_else(|| match kind.as_str() {
                "switch" => Value::Bool(false),
                "stepper" => json!(minimum.unwrap_or(0.0)),
                "segment" => json!(0),
                "select" | "picker" => Value::String(options.first().cloned().unwrap_or_default()),
                "multi-select" | "editable-list" => Value::Array(Vec::new()),
                "text" | "secure-text" => Value::String(String::new()),
                _ => Value::Null,
            });
        let definition = Definition {
            title: if title.is_empty() { key.clone() } else { title },
            key,
            kind,
            default,
            options,
            minimum,
            maximum,
            step,
            guards,
        };
        if definition.supported() {
            ensure!(
                !definition.key.is_empty(),
                "An editable source setting has no key"
            );
            ensure!(
                !reserved(&definition.key),
                "The source setting {} uses reserved package metadata",
                definition.key
            );
        }
        definitions.push(definition);
    }
    Ok(())
}

fn effective_values(
    config: &SourceConfig,
    definitions: &[Definition],
    defaults: &Defaults,
) -> Result<BTreeMap<String, Value>> {
    definitions
        .iter()
        .map(|definition| {
            if !definition.supported() {
                return Ok((definition.key.clone(), Value::Null));
            }
            // A source update can remove a choice, change a control's type, or
            // narrow a numeric range. Previously valid persisted values must not
            // prevent the updated source from starting or its settings from opening.
            // Only persisted values use this fallback; new edits still pass the
            // strict normalizer in `with_option` before anything is written.
            let persisted = if let Some(bytes) = defaults.values.get(&definition.key) {
                decode(definition, bytes).ok()
            } else if let Some(value) = config
                .options
                .get(&definition.key)
                .or_else(|| {
                    config
                        .options
                        .get("defaults")
                        .and_then(|values| values.get(&definition.key))
                })
                .filter(|value| !value.is_null())
            {
                normalize(definition, value).ok()
            } else {
                None
            };
            let value = match persisted {
                Some(value) => value,
                None => normalize(definition, &definition.default).with_context(|| {
                    format!(
                        "Invalid schema default for source setting {}",
                        definition.key
                    )
                })?,
            };
            Ok((definition.key.clone(), value))
        })
        .collect()
}

fn normalize(definition: &Definition, value: &Value) -> Result<Value> {
    let key = &definition.key;
    let normalized = match definition.kind.as_str() {
        "text" | "secure-text" => {
            let value = value
                .as_str()
                .ok_or_else(|| anyhow!("The source setting {key} must be text"))?;
            ensure!(
                value.len() <= MAX_STRING_BYTES,
                "The source setting {key} exceeds the text limit"
            );
            Value::String(value.to_owned())
        }
        "switch" => Value::Bool(
            value
                .as_bool()
                .ok_or_else(|| anyhow!("The source setting {key} must be a boolean"))?,
        ),
        "select" | "picker" => {
            let value = value
                .as_str()
                .ok_or_else(|| anyhow!("The source setting {key} must be a choice value"))?;
            ensure!(
                definition.options.iter().any(|option| option == value)
                    || (definition.options.is_empty() && value.is_empty()),
                "The source setting {key} contains an unknown choice"
            );
            Value::String(value.to_owned())
        }
        "multi-select" | "editable-list" => {
            let values = value
                .as_array()
                .ok_or_else(|| anyhow!("The source setting {key} must be a string array"))?;
            ensure!(
                values.len() <= MAX_SETTINGS,
                "The source setting {key} contains too many values"
            );
            let mut selected = Vec::new();
            let mut seen = BTreeSet::new();
            for value in values {
                let value = value
                    .as_str()
                    .ok_or_else(|| anyhow!("The source setting {key} must contain text values"))?;
                ensure!(
                    value.len() <= MAX_STRING_BYTES,
                    "The source setting {key} exceeds the text limit"
                );
                ensure!(
                    definition.kind == "editable-list"
                        || definition.options.iter().any(|option| option == value),
                    "The source setting {key} contains an unknown choice"
                );
                if definition.kind == "editable-list" || seen.insert(value) {
                    selected.push(Value::String(value.to_owned()));
                }
            }
            Value::Array(selected)
        }
        "segment" => {
            let index = value
                .as_i64()
                .ok_or_else(|| anyhow!("The source setting {key} must be an integer index"))?;
            ensure!(
                index >= 0
                    && index <= i32::MAX as i64
                    && (index as usize) < definition.options.len(),
                "The source setting {key} has an invalid segment index"
            );
            json!(index)
        }
        "stepper" => {
            let number = value
                .as_f64()
                .ok_or_else(|| anyhow!("The source setting {key} must be a number"))?;
            ensure!(
                number.is_finite() && (number as f32).is_finite(),
                "The source setting {key} exceeds the float range"
            );
            let scale = number
                .abs()
                .max(definition.minimum.unwrap_or(0.0).abs())
                .max(definition.maximum.unwrap_or(0.0).abs())
                .max(1.0);
            let tolerance = 4.0 * f32::EPSILON as f64 * scale;
            if let Some(minimum) = definition.minimum {
                ensure!(
                    number >= minimum - tolerance,
                    "The source setting {key} is below its minimum"
                );
            }
            if let Some(maximum) = definition.maximum {
                ensure!(
                    number <= maximum + tolerance,
                    "The source setting {key} exceeds its maximum"
                );
            }
            if let Some(step) = definition.step {
                let offset = (number - definition.minimum.unwrap_or(0.0)) / step;
                ensure!(
                    (offset - offset.round()).abs() <= (tolerance / step).max(0.0001),
                    "The source setting {key} does not match its step increment"
                );
            }
            // The Aidoku defaults ABI encodes floating-point settings as f32.
            json!(number as f32)
        }
        _ => bail!("Unsupported source setting control: {}", definition.kind),
    };
    Ok(normalized)
}

fn encode(definition: &Definition, value: &Value) -> Result<Vec<u8>> {
    Ok(match definition.kind.as_str() {
        "switch" => {
            postcard::to_allocvec(&value.as_bool().context("Encode the boolean preference")?)?
        }
        "segment" => postcard::to_allocvec(
            &(value.as_i64().context("Encode the segment preference")? as i32),
        )?,
        "stepper" => postcard::to_allocvec(
            &(value.as_f64().context("Encode the numeric preference")? as f32),
        )?,
        "multi-select" | "editable-list" => postcard::to_allocvec(
            &value
                .as_array()
                .context("Encode the list preference")?
                .iter()
                .map(|value| value.as_str().unwrap_or_default().to_owned())
                .collect::<Vec<_>>(),
        )?,
        _ => postcard::to_allocvec(
            &value
                .as_str()
                .context("Encode the text preference")?
                .to_owned(),
        )?,
    })
}

fn decode(definition: &Definition, bytes: &[u8]) -> Result<Value> {
    let value = match definition.kind.as_str() {
        "switch" => json!(postcard::from_bytes::<bool>(bytes)?),
        "segment" => json!(postcard::from_bytes::<i32>(bytes)?),
        "stepper" => {
            if let Ok(value) = postcard::from_bytes::<f32>(bytes) {
                let candidate = json!(value);
                if let Ok(value) = normalize(definition, &candidate) {
                    return Ok(value);
                }
            }
            // Accept integer values written by earlier source configurations.
            json!(postcard::from_bytes::<i32>(bytes)? as f32)
        }
        "multi-select" | "editable-list" => json!(postcard::from_bytes::<Vec<String>>(bytes)?),
        _ => json!(postcard::from_bytes::<String>(bytes)?),
    };
    normalize(definition, &value)
}

fn localized(value: Option<&Value>) -> String {
    let Some(value) = value else {
        return String::new();
    };
    if let Some(text) = value.as_str() {
        return text.to_owned();
    }
    ["zh-CN", "zh", "en", "default"]
        .into_iter()
        .find_map(|language| value.get(language).and_then(Value::as_str))
        .unwrap_or_default()
        .to_owned()
}

fn reserved(key: &str) -> bool {
    matches!(
        key,
        "package_dir" | "wasm_file" | "repository" | "repository_url" | "defaults"
    )
}

fn defaults_path(data_dir: &Path, config: &SourceConfig) -> PathBuf {
    data_dir
        .join("sources")
        .join("state")
        .join(format!("{:x}", Sha256::digest(config.id.as_bytes())))
        .join("defaults.json")
}

fn read_defaults(data_dir: &Path, config: &SourceConfig) -> Result<Defaults> {
    let path = defaults_path(data_dir, config);
    if !path.exists() {
        return Ok(Defaults::default());
    }
    let defaults: Defaults = serde_json::from_slice(&read_limited(
        &path,
        "source preferences",
        MAX_DEFAULTS_FILE_BYTES,
    )?)
    .context("Decode source preferences")?;
    ensure!(
        defaults.values.len() <= MAX_SETTINGS,
        "The source preference count exceeds its limit"
    );
    ensure!(
        defaults
            .values
            .iter()
            .map(|(key, value)| key.len() + value.len())
            .sum::<usize>()
            <= MAX_FILE_BYTES,
        "Source preference data exceeds 1 MiB"
    );
    Ok(defaults)
}

fn read_limited(path: &Path, description: &str, maximum: usize) -> Result<Vec<u8>> {
    let file = File::open(path).with_context(|| format!("Open {description}"))?;
    ensure!(
        file.metadata()?.len() <= maximum as u64,
        "The {description} file exceeds its storage limit"
    );
    let mut bytes = Vec::new();
    file.take(maximum as u64 + 1)
        .read_to_end(&mut bytes)
        .with_context(|| format!("Read {description}"))?;
    ensure!(
        bytes.len() <= maximum,
        "The {description} file exceeds its storage limit"
    );
    Ok(bytes)
}

pub(super) fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let directory = path
        .parent()
        .context("Locate the source preference directory")?;
    fs::create_dir_all(directory).context("Create the source preference directory")?;
    let temporary = directory.join(format!(
        ".defaults-{}-{}.tmp",
        std::process::id(),
        NEXT_TEMPORARY.fetch_add(1, Ordering::Relaxed)
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temporary)
            .context("Create the source preference transaction")?;
        file.write_all(bytes)
            .context("Write the source preference transaction")?;
        file.sync_all()
            .context("Flush the source preference transaction")?;
        drop(file);
        replace_file(&temporary, path).context("Commit source preferences")
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

#[cfg(not(windows))]
fn replace_file(temporary: &Path, target: &Path) -> std::io::Result<()> {
    fs::rename(temporary, target)
}

#[cfg(windows)]
fn replace_file(temporary: &Path, target: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn MoveFileExW(existing: *const u16, replacement: *const u16, flags: u32) -> i32;
    }
    let source: Vec<u16> = temporary.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
    // MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH keeps replacement
    // atomic on Windows without deleting the user's previous preference file.
    if unsafe { MoveFileExW(source.as_ptr(), destination.as_ptr(), 0x1 | 0x8) } == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(directory: &Path) -> SourceConfig {
        let package = directory.join("package");
        fs::create_dir_all(&package).unwrap();
        fs::write(package.join("settings.json"), serde_json::to_vec(&json!([
            {"type":"group","title":"Reading","items":[
                {"type":"switch","key":"enabled","title":{"zh-CN":"Enabled"},"default":true},
                {"type":"page","title":"Details","items":[{"type":"group","items":[
                    {"type":"select","key":"language","title":"Language","values":["en","ja"],"default":"en"},
                    {"type":"stepper","key":"timeout","title":"Timeout","minimumValue":1,"maximumValue":30,"stepValue":0.5,"default":5},
                    {"type":"segment","key":"mode","title":"Mode","options":["First","Second"],"default":0},
                    {"type":"multi-select","key":"tags","title":"Tags","values":["a","b"],"default":[]},
                    {"type":"text","key":"dependent","title":"Dependent","requires":"enabled","default":"text"},
                    {"type":"login","key":"login","title":"Sign in","method":"web"}
                ]}]}
            ]}
        ])).unwrap()).unwrap();
        SourceConfig {
            id: "en.settings-test".into(),
            kind: "wasm".into(),
            options: [("package_dir".into(), json!(package.to_string_lossy()))].into(),
            ..SourceConfig::default()
        }
    }

    #[test]
    fn settings_round_trip_overrides_host_defaults_with_typed_values() {
        let temporary = tempfile::tempdir().unwrap();
        let mut config = fixture(temporary.path());
        for (key, value) in [
            ("language", json!("ja")),
            ("timeout", json!(7.5)),
            ("mode", json!(1)),
            ("tags", json!(["b"])),
        ] {
            config = with_option(temporary.path(), &config, key, value.clone()).unwrap();
            let setting = load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == key)
                .unwrap();
            assert_eq!(setting.value, value);
        }
        let defaults = read_defaults(temporary.path(), &config).unwrap();
        assert_eq!(
            postcard::from_bytes::<f32>(&defaults.values["timeout"]).unwrap(),
            7.5
        );
        assert_eq!(
            postcard::from_bytes::<i32>(&defaults.values["mode"]).unwrap(),
            1
        );
        config.options.insert("language".into(), json!("en"));
        assert_eq!(
            load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == "language")
                .unwrap()
                .value,
            "ja"
        );
        config = with_option(temporary.path(), &config, "language", Value::Null).unwrap();
        assert_eq!(
            load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == "language")
                .unwrap()
                .value,
            "en"
        );
    }

    #[test]
    fn source_controls_reject_invalid_values_and_unsupported_actions() {
        let temporary = tempfile::tempdir().unwrap();
        let config = fixture(temporary.path());
        for (key, value) in [
            ("language", json!("invalid")),
            ("timeout", json!(7.7)),
            ("mode", json!(-1)),
            ("tags", json!(["invalid"])),
            ("login", json!("text")),
            ("package_dir", json!("other")),
        ] {
            assert!(with_option(temporary.path(), &config, key, value).is_err());
        }
        let config = with_option(temporary.path(), &config, "enabled", json!(false)).unwrap();
        assert_eq!(
            load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == "dependent")
                .unwrap()
                .kind,
            "disabled:text"
        );
        assert!(with_option(temporary.path(), &config, "dependent", json!("changed")).is_err());
    }

    #[test]
    fn removed_source_choices_fall_back_and_can_be_reset_or_replaced() {
        let temporary = tempfile::tempdir().unwrap();
        let config = fixture(temporary.path());
        let mut config = with_option(temporary.path(), &config, "language", json!("ja")).unwrap();
        let schema_path =
            Path::new(config.options["package_dir"].as_str().unwrap()).join("settings.json");
        let mut schema: Value = serde_json::from_slice(&fs::read(&schema_path).unwrap()).unwrap();
        let language = schema.pointer_mut("/0/items/1/items/0/items/0").unwrap();
        language["values"] = json!(["en", "fr"]);
        language["default"] = json!("fr");
        fs::write(schema_path, serde_json::to_vec(&schema).unwrap()).unwrap();
        config.version += 1;

        assert_eq!(
            load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == "language")
                .unwrap()
                .value,
            "fr"
        );
        assert_eq!(
            postcard::from_bytes::<String>(
                &encoded_defaults(temporary.path(), &config).unwrap()["language"]
            )
            .unwrap(),
            "fr"
        );
        assert!(with_option(temporary.path(), &config, "language", json!("ja")).is_err());

        config = with_option(temporary.path(), &config, "language", Value::Null).unwrap();
        assert!(!config.options.contains_key("language"));
        assert!(
            !read_defaults(temporary.path(), &config)
                .unwrap()
                .values
                .contains_key("language")
        );
        assert_eq!(
            load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == "language")
                .unwrap()
                .value,
            "fr"
        );

        // Legacy JSON options follow the same migration rule when no host
        // default exists, while a fresh edit of that value remains invalid.
        config.options.insert("language".into(), json!("ja"));
        assert_eq!(
            load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == "language")
                .unwrap()
                .value,
            "fr"
        );
        config = with_option(temporary.path(), &config, "language", json!("en")).unwrap();
        assert_eq!(
            load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == "language")
                .unwrap()
                .value,
            "en"
        );
    }

    #[test]
    fn incompatible_postcard_preferences_do_not_block_recovery() {
        let temporary = tempfile::tempdir().unwrap();
        let config = fixture(temporary.path());
        let config = with_option(temporary.path(), &config, "language", json!("ja")).unwrap();
        let mut defaults = read_defaults(temporary.path(), &config).unwrap();
        defaults.values.insert("language".into(), vec![0xff]);
        atomic_write(
            &defaults_path(temporary.path(), &config),
            &serde_json::to_vec(&defaults).unwrap(),
        )
        .unwrap();

        assert_eq!(
            load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == "language")
                .unwrap()
                .value,
            "en"
        );
        assert_eq!(
            postcard::from_bytes::<String>(
                &encoded_defaults(temporary.path(), &config).unwrap()["language"]
            )
            .unwrap(),
            "en"
        );
        let config = with_option(temporary.path(), &config, "language", Value::Null).unwrap();
        assert!(
            !read_defaults(temporary.path(), &config)
                .unwrap()
                .values
                .contains_key("language")
        );
        let config = with_option(temporary.path(), &config, "language", json!("ja")).unwrap();
        assert_eq!(
            load(temporary.path(), &config)
                .unwrap()
                .into_iter()
                .find(|item| item.key == "language")
                .unwrap()
                .value,
            "ja"
        );
    }

    #[test]
    fn legal_binary_preferences_survive_json_expansion_and_option_edits() {
        let temporary = tempfile::tempdir().unwrap();
        let config = fixture(temporary.path());
        let defaults = Defaults {
            values: BTreeMap::from([("internal-cache".into(), vec![255; 220_000])]),
        };
        let encoded = serde_json::to_vec_pretty(&defaults).unwrap();
        assert!(encoded.len() > MAX_FILE_BYTES && encoded.len() < MAX_DEFAULTS_FILE_BYTES);
        atomic_write(&defaults_path(temporary.path(), &config), &encoded).unwrap();
        assert!(!load(temporary.path(), &config).unwrap().is_empty());
        assert!(
            !encoded_defaults(temporary.path(), &config)
                .unwrap()
                .is_empty()
        );
        let config = with_option(temporary.path(), &config, "language", json!("ja")).unwrap();
        let restored = read_defaults(temporary.path(), &config).unwrap();
        assert_eq!(restored.values["internal-cache"], vec![255; 220_000]);
        assert_eq!(
            postcard::from_bytes::<String>(&restored.values["language"]).unwrap(),
            "ja"
        );
    }
}
