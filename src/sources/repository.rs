use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::{Cursor, Read, Write},
    path::{Component, Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result, bail, ensure};
use reqwest::{Url, blocking::Client};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::domain::SourceConfig;

const MAX_INDEX_BYTES: u64 = 8 * 1024 * 1024;
const MAX_PACKAGE_BYTES: u64 = 32 * 1024 * 1024;
const MAX_FILE_BYTES: u64 = 32 * 1024 * 1024;
const MAX_EXTRACTED_BYTES: u64 = 96 * 1024 * 1024;
const MAX_PACKAGE_ENTRIES: usize = 512;
const MAX_SOURCE_JSON_BYTES: u64 = 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Repository {
    pub name: String,
    pub url: String,
    pub sources: Vec<RepositorySource>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RepositorySource {
    pub id: String,
    pub name: String,
    pub version: u32,
    pub languages: Vec<String>,
    pub download_url: String,
    pub icon_url: String,
    pub base_url: String,
}

impl Repository {
    pub fn fetch(client: &Client, url: &str) -> Result<Self> {
        let url = http_url(url)?;
        match fetch_index(client, &url) {
            Ok(repository) => Ok(repository),
            Err(direct_error) => {
                if url.path().ends_with(".json") {
                    return Err(direct_error);
                }
                let mut directory = url.clone();
                directory.set_query(None);
                directory.set_fragment(None);
                if !directory.path().ends_with('/') {
                    directory.set_path(&format!("{}/", directory.path()));
                }
                let index_url = directory.join("index.min.json")?;
                fetch_index(client, &index_url).with_context(|| {
                    format!("Could not load a source index at {url}: {direct_error:#}")
                })
            }
        }
    }
}

fn fetch_index(client: &Client, url: &Url) -> Result<Repository> {
    let response = super::network::send_checked(
        client
            .get(url.clone())
            .header(reqwest::header::ACCEPT, "application/json")
            .timeout(Duration::from_secs(30)),
    )
    .with_context(|| format!("Could not request source index {url}"))?
    .error_for_status()
    .context("The source repository returned an HTTP error")?;
    let final_url = response.url().clone();
    let bytes = bounded_response(response, MAX_INDEX_BYTES, "Source index")?;
    parse_index(&bytes, &final_url)
}

fn parse_index(bytes: &[u8], index_url: &Url) -> Result<Repository> {
    let document: Value = serde_json::from_slice(bytes).context("Invalid source index JSON")?;
    let (name, entries) = if let Some(entries) = document.as_array() {
        (
            index_url
                .host_str()
                .unwrap_or("Source repository")
                .to_owned(),
            entries,
        )
    } else {
        (
            document
                .get("name")
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .unwrap_or("Source repository")
                .to_owned(),
            document
                .get("sources")
                .and_then(Value::as_array)
                .context("The source index must contain a sources array")?,
        )
    };
    ensure!(
        entries.len() <= 20_000,
        "The source index has too many entries"
    );
    let mut sources = BTreeMap::<String, RepositorySource>::new();
    for (position, entry) in entries.iter().enumerate() {
        let source = parse_source(entry, index_url)
            .with_context(|| format!("Invalid source entry at position {}", position + 1))?;
        match sources.get(&source.id) {
            Some(existing) if existing.version >= source.version => {}
            _ => {
                sources.insert(source.id.clone(), source);
            }
        }
    }
    Ok(Repository {
        name,
        url: index_url.to_string(),
        sources: sources.into_values().collect(),
    })
}

fn parse_source(entry: &Value, index_url: &Url) -> Result<RepositorySource> {
    let id = string_field(entry, "id").context("The source has no id")?;
    validate_source_id(id)?;
    let name = string_field(entry, "name")
        .filter(|value| !value.trim().is_empty())
        .context("The source has no name")?;
    let version = entry
        .get("version")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0 && *value <= u32::MAX as u64)
        .context("The source has an invalid version")? as u32;
    let download_url = if let Some(url) = string_field(entry, "downloadURL") {
        resolve_http(index_url, url)?
    } else {
        let filename = string_field(entry, "file").context("The source has no download URL")?;
        resolve_http(index_url, &format!("sources/{filename}"))?
    };
    let icon_url = if let Some(url) = string_field(entry, "iconURL") {
        resolve_http(index_url, url)?
    } else if let Some(filename) = string_field(entry, "icon") {
        resolve_http(index_url, &format!("icons/{filename}"))?
    } else {
        String::new()
    };
    let base_url = string_field(entry, "baseURL")
        .map(|url| resolve_http(index_url, url))
        .transpose()?
        .unwrap_or_default();
    let languages = match entry.get("languages") {
        Some(value) => parse_languages(value)?,
        None => string_field(entry, "lang")
            .map(|language| vec![language.to_owned()])
            .unwrap_or_default(),
    };
    Ok(RepositorySource {
        id: id.into(),
        name: name.into(),
        version,
        languages,
        download_url,
        icon_url,
        base_url,
    })
}

impl RepositorySource {
    pub fn install(&self, client: &Client, data_dir: &Path) -> Result<SourceConfig> {
        validate_source_id(&self.id)?;
        let url = http_url(&self.download_url)?;
        let response =
            super::network::send_checked(client.get(url.clone()).timeout(Duration::from_secs(120)))
                .with_context(|| format!("Could not download source package {url}"))?
                .error_for_status()
                .context("The source package returned an HTTP error")?;
        let bytes = bounded_response(response, MAX_PACKAGE_BYTES, "Source package")?;
        self.install_bytes(&bytes, data_dir)
    }

    pub fn install_file(&self, path: &Path, data_dir: &Path) -> Result<SourceConfig> {
        let file = File::open(path).context("Could not open source package")?;
        ensure!(
            file.metadata()?.len() <= MAX_PACKAGE_BYTES,
            "Source package exceeds the download size limit"
        );
        let mut bytes = Vec::new();
        file.take(MAX_PACKAGE_BYTES + 1).read_to_end(&mut bytes)?;
        ensure!(
            bytes.len() as u64 <= MAX_PACKAGE_BYTES,
            "Source package is too large"
        );
        self.install_bytes(&bytes, data_dir)
    }

    fn install_bytes(&self, bytes: &[u8], data_dir: &Path) -> Result<SourceConfig> {
        super::network::check_session()?;
        validate_source_id(&self.id)?;
        ensure!(
            bytes.len() as u64 <= MAX_PACKAGE_BYTES,
            "Source package is too large"
        );
        fs::create_dir_all(data_dir).context("Could not create application data directory")?;
        let data_dir = data_dir.canonicalize()?;
        let sources_dir = data_dir.join("sources");
        checked_directory(&sources_dir)?;
        let source_dir = sources_dir.join(&self.id);
        checked_directory(&source_dir)?;
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let suffix = format!("{}-{nonce}", std::process::id());
        let staging = sources_dir.join(format!(".install-{}-{suffix}", self.id));
        fs::create_dir(&staging).context("Could not create package staging directory")?;
        let mut cleanup = StagingDirectory(staging.clone());
        extract_payload(bytes, &staging)?;
        let payload = staging.join("Payload");
        let package = inspect_payload(&payload, self)?;

        // A unique directory keeps the active package valid until the caller persists the new config.
        let published = source_dir.join(format!("v{}-{suffix}", package.version));
        super::network::check_session()?;
        fs::rename(&payload, &published).context("Could not publish the source package")?;
        let _ = fs::remove_dir(&staging);
        cleanup.0 = PathBuf::new();
        let mut options = BTreeMap::new();
        options.insert("package_dir".into(), json!(published));
        options.insert("repository_download_url".into(), json!(self.download_url));
        options.insert("icon_url".into(), json!(self.icon_url));
        options.insert("source_config".into(), package.document.clone());
        Ok(SourceConfig {
            id: self.id.clone(),
            name: package.name,
            kind: "wasm".into(),
            base_url: package.base_url,
            username: String::new(),
            password: String::new(),
            token: String::new(),
            version: package.version,
            languages: package.languages,
            enabled: true,
            options,
        })
    }
}

struct PackageInfo {
    name: String,
    version: u32,
    languages: Vec<String>,
    base_url: String,
    document: Value,
}

fn inspect_payload(payload: &Path, expected: &RepositorySource) -> Result<PackageInfo> {
    let config_file = File::open(payload.join("source.json"))
        .context("The package does not contain Payload/source.json")?;
    ensure!(
        config_file.metadata()?.len() <= MAX_SOURCE_JSON_BYTES,
        "The source configuration is too large"
    );
    let document: Value = serde_json::from_reader(config_file.take(MAX_SOURCE_JSON_BYTES + 1))
        .context("Invalid source package configuration JSON")?;
    let info = document.get("info").unwrap_or(&document);
    let id = string_field(info, "id").context("The package configuration has no source id")?;
    validate_source_id(id)?;
    ensure!(
        id == expected.id,
        "The package source id does not match the repository entry"
    );
    let name = string_field(info, "name")
        .filter(|value| !value.trim().is_empty())
        .context("The package configuration has no source name")?
        .to_owned();
    let version =
        info.get("version")
            .and_then(Value::as_u64)
            .filter(|value| *value > 0 && *value <= u32::MAX as u64)
            .context("The package configuration has an invalid source version")? as u32;
    ensure!(
        version == expected.version,
        "The package source version does not match the repository entry"
    );
    let languages = if let Some(value) = info.get("languages") {
        parse_languages(value)?
    } else if let Some(language) = string_field(info, "lang") {
        vec![language.to_owned()]
    } else {
        expected.languages.clone()
    };
    let base_url = string_field(info, "url")
        .or_else(|| {
            info.get("urls")
                .and_then(Value::as_array)
                .and_then(|urls| urls.first())
                .and_then(Value::as_str)
        })
        .unwrap_or(&expected.base_url)
        .to_owned();
    if !base_url.is_empty() {
        http_url(&base_url).context("The package has an invalid base URL")?;
    }
    let wasm_path = ["main.wasm", "source.wasm"]
        .into_iter()
        .map(|filename| payload.join(filename))
        .find(|path| path.is_file())
        .context("The package does not contain Payload/main.wasm")?;
    let mut wasm = File::open(wasm_path)?;
    let mut header = [0; 8];
    wasm.read_exact(&mut header)
        .context("The package contains a truncated WASM module")?;
    ensure!(
        header == *b"\0asm\x01\0\0\0",
        "The package does not contain a WebAssembly 1.0 module"
    );
    Ok(PackageInfo {
        name,
        version,
        languages,
        base_url,
        document,
    })
}

fn extract_payload(bytes: &[u8], destination: &Path) -> Result<()> {
    let mut archive =
        zip::ZipArchive::new(Cursor::new(bytes)).context("Invalid AIX ZIP archive")?;
    ensure!(
        archive.len() <= MAX_PACKAGE_ENTRIES,
        "The source package contains too many entries"
    );
    let mut seen = BTreeSet::new();
    let mut extracted = 0u64;
    let mut actually_extracted = 0u64;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .context("Could not read package entry")?;
        let name = entry.name().to_owned();
        ensure!(
            !name.contains('\\'),
            "The source package contains an unsafe path"
        );
        let relative = entry
            .enclosed_name()
            .context("The source package contains a path outside its root")?;
        validate_entry_path(&relative)?;
        ensure!(
            relative.starts_with("Payload"),
            "The source package contains a file outside Payload"
        );
        let key = relative
            .to_string_lossy()
            .trim_end_matches('/')
            .to_ascii_lowercase();
        ensure!(
            seen.insert(key),
            "The source package contains duplicate or case-colliding paths"
        );
        let unix_type = entry.unix_mode().unwrap_or_default() & 0o170000;
        ensure!(
            unix_type == 0 || unix_type == 0o100000 || (unix_type == 0o040000 && entry.is_dir()),
            "The source package contains a symlink or special file"
        );
        ensure!(
            entry.size() <= MAX_FILE_BYTES,
            "A source package entry is too large"
        );
        extracted = extracted
            .checked_add(entry.size())
            .context("The source package size overflowed")?;
        ensure!(
            extracted <= MAX_EXTRACTED_BYTES,
            "The uncompressed source package is too large"
        );
        let output = destination.join(&relative);
        if entry.is_dir() {
            fs::create_dir_all(output)?;
            continue;
        }
        fs::create_dir_all(output.parent().context("Invalid package entry parent")?)?;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(output)?;
        let copied = std::io::copy(&mut entry.by_ref().take(MAX_FILE_BYTES + 1), &mut file)?;
        ensure!(
            copied <= MAX_FILE_BYTES,
            "A source package entry exceeded its size limit"
        );
        actually_extracted = actually_extracted
            .checked_add(copied)
            .context("The source package size overflowed")?;
        ensure!(
            actually_extracted <= MAX_EXTRACTED_BYTES,
            "The uncompressed source package exceeded its size limit"
        );
        file.flush()?;
    }
    Ok(())
}

fn validate_entry_path(path: &Path) -> Result<()> {
    ensure!(
        !path.as_os_str().is_empty(),
        "The source package contains an empty path"
    );
    for component in path.components() {
        let Component::Normal(value) = component else {
            bail!("The source package contains an unsafe path");
        };
        let value = value
            .to_str()
            .context("The package contains a non-UTF-8 path")?;
        ensure!(
            !value.contains(':')
                && !value.contains('\0')
                && !value.ends_with('.')
                && !value.ends_with(' ')
                && !is_windows_device_name(value),
            "The source package contains an unsafe path component"
        );
    }
    Ok(())
}

fn checked_directory(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => ensure!(
            metadata.is_dir() && !metadata.file_type().is_symlink(),
            "The source package directory is not a regular directory"
        ),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => fs::create_dir(path)?,
        Err(error) => return Err(error.into()),
    }
    Ok(())
}

fn validate_source_id(id: &str) -> Result<()> {
    ensure!(
        !id.is_empty()
            && id.len() <= 160
            && id.as_bytes()[0].is_ascii_alphanumeric()
            && id
                .bytes()
                .all(|value| value.is_ascii_alphanumeric() || matches!(value, b'.' | b'-'))
            && !id.contains("..")
            && !id.ends_with('.')
            && !is_windows_device_name(id),
        "Invalid source id"
    );
    Ok(())
}

fn is_windows_device_name(value: &str) -> bool {
    let stem = value
        .split('.')
        .next()
        .unwrap_or_default()
        .to_ascii_uppercase();
    matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || (stem.len() == 4
            && (stem.starts_with("COM") || stem.starts_with("LPT"))
            && matches!(stem.as_bytes()[3], b'1'..=b'9'))
}

fn parse_languages(value: &Value) -> Result<Vec<String>> {
    value
        .as_array()
        .context("Source languages must be an array")?
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_owned)
                .context("A source language must be a string")
        })
        .collect()
}

fn string_field<'a>(object: &'a Value, key: &str) -> Option<&'a str> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn http_url(value: &str) -> Result<Url> {
    let url = Url::parse(value).context("Invalid repository URL")?;
    ensure!(
        matches!(url.scheme(), "http" | "https") && url.host_str().is_some(),
        "Source URLs must use HTTP or HTTPS"
    );
    Ok(url)
}

fn resolve_http(base: &Url, value: &str) -> Result<String> {
    let resolved = base.join(value).context("Invalid relative source URL")?;
    Ok(http_url(resolved.as_str())?.to_string())
}

fn bounded_response(
    mut response: reqwest::blocking::Response,
    limit: u64,
    label: &str,
) -> Result<Vec<u8>> {
    super::network::check_session()?;
    if let Some(length) = response.content_length() {
        ensure!(length <= limit, "{label} exceeds the size limit");
    }
    let mut bytes = Vec::new();
    let read = super::http_body::with_context(|| {
        response.by_ref().take(limit + 1).read_to_end(&mut bytes)
    })?;
    super::network::check_session()?;
    read.with_context(|| format!("Could not read {label}"))?;
    ensure!(
        bytes.len() as u64 <= limit,
        "{label} exceeds the size limit"
    );
    Ok(bytes)
}

struct StagingDirectory(PathBuf);

impl Drop for StagingDirectory {
    fn drop(&mut self) {
        if !self.0.as_os_str().is_empty() {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_current_and_legacy_source_indexes() {
        let url = Url::parse("https://example.org/repository/index.json").unwrap();
        let current = parse_index(br#"{"name":"Community","sources":[{"id":"en.example","name":"Example","version":2,"languages":["en"],"downloadURL":"packages/example.aix","iconURL":"icons/example.png"}]}"#, &url).unwrap();
        assert_eq!(
            current.sources[0].download_url,
            "https://example.org/repository/packages/example.aix"
        );
        let legacy = parse_index(br#"[{"id":"en.example","name":"Example","version":1,"lang":"en","file":"example.aix","icon":"example.png"}]"#, &url).unwrap();
        assert_eq!(
            legacy.sources[0].download_url,
            "https://example.org/repository/sources/example.aix"
        );
        assert_eq!(legacy.sources[0].languages, ["en"]);
    }

    #[test]
    fn rejects_unsafe_identifiers_and_non_http_downloads() {
        for id in [
            "../escape",
            "en/escape",
            "en\\escape",
            "en..escape",
            "CON",
            "en.",
        ] {
            assert!(validate_source_id(id).is_err(), "{id}");
        }
        assert!(validate_source_id("zh.example-2").is_ok());
        let url = Url::parse("https://example.org/index.json").unwrap();
        let document = br#"[{"id":"en.example","name":"Example","version":1,"downloadURL":"file:///etc/passwd"}]"#;
        assert!(parse_index(document, &url).is_err());
    }

    #[test]
    fn keeps_only_the_latest_duplicate_repository_entry() {
        let url = Url::parse("https://example.org/index.json").unwrap();
        let document = br#"[{"id":"en.example","name":"Example","version":2,"file":"new.aix"},{"id":"en.example","name":"Example","version":1,"file":"old.aix"}]"#;
        let repository = parse_index(document, &url).unwrap();
        assert_eq!(repository.sources.len(), 1);
        assert_eq!(repository.sources[0].version, 2);
    }

    #[test]
    fn rejects_zip_paths_outside_payload_before_writing() {
        let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
        zip.start_file("../escape", zip::write::SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"escape").unwrap();
        let bytes = zip.finish().unwrap().into_inner();
        let destination = std::env::temp_dir().join("aidoku-reject-package-traversal");
        assert!(extract_payload(&bytes, &destination).is_err());
    }

    #[test]
    fn publishes_the_verified_payload_and_returns_its_directory() {
        let package = test_package("en.example", 2);
        let source = test_source();
        let destination = test_directory("publish");
        let config = source.install_bytes(&package, &destination).unwrap();
        let package_dir = PathBuf::from(config.options["package_dir"].as_str().unwrap());
        assert!(package_dir.join("main.wasm").is_file());
        assert_eq!(config.id, "en.example");
        assert_eq!(config.version, 2);
        assert_eq!(config.kind, "wasm");
        let _ = fs::remove_dir_all(destination);
    }

    #[test]
    fn rejects_a_package_that_substitutes_another_source() {
        let destination = test_directory("identity");
        let package = test_package("en.other", 2);
        assert!(test_source().install_bytes(&package, &destination).is_err());
        assert!(!destination.join("sources/en.other").exists());
        let source_dir = destination.join("sources/en.example");
        assert_eq!(fs::read_dir(source_dir).unwrap().count(), 0);
        let _ = fs::remove_dir_all(destination);
    }

    fn test_directory(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "aidoku-package-{label}-{}-{nonce}",
            std::process::id()
        ))
    }

    fn test_source() -> RepositorySource {
        RepositorySource {
            id: "en.example".into(),
            name: "Example".into(),
            version: 2,
            languages: vec!["en".into()],
            download_url: "https://example.org/package.aix".into(),
            icon_url: String::new(),
            base_url: "https://example.org".into(),
        }
    }

    fn test_package(id: &str, version: u32) -> Vec<u8> {
        let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
        let options = zip::write::SimpleFileOptions::default();
        zip.start_file("Payload/source.json", options).unwrap();
        zip.write_all(
            serde_json::to_string(&json!({"info": {
                "id": id, "name": "Example", "version": version,
                "languages": ["en"], "url": "https://example.org"
            }}))
            .unwrap()
            .as_bytes(),
        )
        .unwrap();
        zip.start_file("Payload/main.wasm", options).unwrap();
        zip.write_all(b"\0asm\x01\0\0\0").unwrap();
        zip.finish().unwrap().into_inner()
    }
}
