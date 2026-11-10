//! Consistent, local-only library metadata backups and transactional restores.
//!
//! Images, downloaded payloads, cache databases and extension binaries are not
//! included. A restore keeps the live database file in place, so existing
//! connections never continue writing into an unlinked, replaced database.
//! Manual backups are never pruned automatically. Two automatic pre-restore
//! safety snapshots use a separate directory and quota; publication completes
//! before an older automatic snapshot is removed. A failed cleanup may retain
//! one additional snapshot, and the next restore must clean it before writing.

use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::Duration,
};

use anyhow::{Context, Result, ensure};
use rusqlite::{Connection, OpenFlags, TransactionBehavior};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use sha2::{Digest, Sha256};

const FORMAT_VERSION: u32 = 1;
const DATABASE_VERSION: i64 = 1;
const MAX_BACKUPS: usize = 64;
const MAX_ROLLBACKS: usize = 2;
const MAX_BACKUP_BYTES: u64 = 256 * 1024 * 1024;
const MAX_MANIFEST_BYTES: u64 = 128 * 1024;
const MAX_DOCUMENT_BYTES: usize = 8 * 1024 * 1024;
const REQUIRED_TABLES: &[&str] = &[
    "books",
    "chapters",
    "pages",
    "progress",
    "bookmarks",
    "preferences",
    "sources",
    "downloads",
    "download_sleep_ownership",
    "book_ui_metadata",
    "chapter_page_stats",
];

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct BackupCounts {
    pub books: u64,
    pub chapters: u64,
    pub progress: u64,
    pub bookmarks: u64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct BackupInfo {
    pub id: String,
    pub created_at: String,
    pub bytes: u64,
    pub counts: BackupCounts,
    #[serde(default)]
    pub kind: BackupKind,
    #[serde(default)]
    pub sequence: u64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BackupKind {
    #[default]
    Manual,
    AutomaticRollback,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
struct Column {
    name: String,
    declared_type: String,
    not_null: bool,
    primary_key: i64,
}

type Schema = BTreeMap<String, Vec<Column>>;

#[derive(Debug, Serialize, Deserialize)]
struct Manifest {
    format_version: u32,
    database_version: i64,
    application: String,
    application_version: String,
    scope: String,
    info: BackupInfo,
    sha256: String,
    schema: Schema,
}

#[derive(Clone, Debug)]
pub struct BackupManager {
    data_root: PathBuf,
    root: PathBuf,
    staging: PathBuf,
    rollbacks: PathBuf,
}

/// A checked, private copy retained while the user reviews a restore. The
/// original backup can change without changing this pending operation.
#[derive(Debug)]
pub struct PreparedRestore {
    pub info: BackupInfo,
    path: PathBuf,
    data_root: PathBuf,
    sha256: String,
    schema: Schema,
}

impl Drop for PreparedRestore {
    fn drop(&mut self) {
        // The random staging name is created exclusively by prepare_restore.
        // Never follow a replacement link or recursively remove a directory.
        let parent_current = self.path.parent().is_some_and(|parent| {
            fs::symlink_metadata(parent)
                .is_ok_and(|metadata| metadata.is_dir() && !metadata.file_type().is_symlink())
                && parent
                    .canonicalize()
                    .is_ok_and(|canonical| canonical == parent)
        });
        if parent_current && regular_file(&self.path).is_ok() {
            let _ = fs::remove_file(&self.path);
        }
    }
}

#[derive(Clone, Debug)]
pub struct RestoreReport {
    pub restored: BackupInfo,
    pub rollback: BackupInfo,
    pub paused_downloads: u64,
}

impl BackupManager {
    pub fn open(data_dir: impl AsRef<Path>) -> Result<Self> {
        let data_root = data_dir
            .as_ref()
            .canonicalize()
            .context("Resolve the application data directory")?;
        ensure!(
            data_root.is_dir(),
            "The application data directory is missing"
        );
        let root = data_root.join("backups");
        ensure_directory(&root)?;
        let staging = root.join("staging");
        ensure_directory(&staging)?;
        let rollbacks = root.join("rollback");
        ensure_directory(&rollbacks)?;
        Ok(Self {
            data_root,
            root,
            staging,
            rollbacks,
        })
    }

    pub fn directory(&self) -> &Path {
        &self.root
    }

    pub fn list(&self) -> Result<Vec<BackupInfo>> {
        let mut backups = self.list_kind(BackupKind::Manual, MAX_BACKUPS)?;
        backups.extend(self.list_kind(BackupKind::AutomaticRollback, MAX_ROLLBACKS + 1)?);
        backups.sort_by(|left, right| {
            right
                .created_at
                .cmp(&left.created_at)
                .then_with(|| right.sequence.cmp(&left.sequence))
                .then_with(|| right.id.cmp(&left.id))
        });
        Ok(backups)
    }

    fn list_kind(&self, kind: BackupKind, limit: usize) -> Result<Vec<BackupInfo>> {
        self.validate_layout()?;
        let mut backups = Vec::new();
        let mut entries = 0;
        for entry in
            fs::read_dir(self.backup_directory(kind)).context("Read local library backups")?
        {
            entries += 1;
            ensure!(
                entries <= limit * 3 + 16,
                "The backup directory has too many entries"
            );
            let entry = entry?;
            let name = entry.file_name();
            let Some(name) = name.to_str() else { continue };
            let Some(id) = name.strip_suffix(".json") else {
                continue;
            };
            if id_kind(id) != Some(kind) {
                continue;
            }
            let manifest = self.read_manifest(id)?;
            backups.push(manifest.info);
            ensure!(
                backups.len() <= limit,
                "The local backup limit was exceeded"
            );
        }
        backups.sort_by(|left, right| {
            right
                .sequence
                .cmp(&left.sequence)
                .then_with(|| right.created_at.cmp(&left.created_at))
                .then_with(|| right.id.cmp(&left.id))
        });
        Ok(backups)
    }

    /// VACUUM INTO creates one SQLite snapshot, including concurrent commits
    /// entirely before or entirely after its read transaction. No application
    /// tables, migrations or recovery steps run on this auxiliary connection.
    pub fn create(&self) -> Result<BackupInfo> {
        self.create_kind(BackupKind::Manual)
    }

    fn create_rollback(&self) -> Result<BackupInfo> {
        // A previous cleanup failure cannot accumulate another file on every
        // retry. Manual backup paths are never inspected or removed here.
        self.prune_rollbacks(None)?;
        let created = self.create_kind(BackupKind::AutomaticRollback)?;
        self.prune_rollbacks(Some(&created.id))?;
        Ok(created)
    }

    fn create_kind(&self, kind: BackupKind) -> Result<BackupInfo> {
        self.validate_layout()?;
        let sequence = if kind == BackupKind::Manual {
            ensure!(
                self.list_kind(kind, MAX_BACKUPS)?.len() < MAX_BACKUPS,
                "The manual backup limit is 64 files"
            );
            0
        } else {
            let existing = self.list_kind(kind, MAX_ROLLBACKS + 1)?;
            ensure!(
                existing.len() <= MAX_ROLLBACKS,
                "Automatic rollback cleanup is required before another restore"
            );
            existing
                .iter()
                .map(|info| info.sequence)
                .max()
                .unwrap_or(0)
                .checked_add(1)
                .context("The automatic rollback sequence is exhausted")?
        };
        let database = self.database()?;
        let prefix = match kind {
            BackupKind::Manual => "aidoku-backup",
            BackupKind::AutomaticRollback => "aidoku-rollback",
        };
        let id = fresh_id(prefix)?;
        let pending_database = self.staging.join(format!("{id}.sqlite3"));
        let pending_manifest = self.staging.join(format!("{id}.json"));
        let final_database = self.database_path(&id)?;
        let final_manifest = self.manifest_path(&id)?;
        // SQLite accepts an existing empty INTO destination. Creating it first
        // applies private permissions before credentials enter the snapshot.
        let file = create_private_file(&pending_database)?;
        drop(file);
        let mut owns_pending_manifest = false;
        let mut published_database = false;
        let mut published_manifest = false;
        let result = (|| {
            let connection =
                Connection::open_with_flags(&database, OpenFlags::SQLITE_OPEN_READ_WRITE)?;
            configure_connection(&connection)?;
            let version: i64 =
                connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
            ensure!(
                version == DATABASE_VERSION,
                "The library database version is unsupported"
            );
            connection.execute(
                "VACUUM main INTO ?1",
                [pending_database.to_string_lossy().as_ref()],
            )?;
            drop(connection);
            let bytes = regular_file(&pending_database)?.len();
            ensure!(
                bytes > 0 && bytes <= MAX_BACKUP_BYTES,
                "The metadata backup exceeds 256 MiB"
            );
            File::open(&pending_database)?
                .sync_all()
                .context("Sync the metadata snapshot")?;
            let snapshot = open_snapshot(&pending_database)?;
            validate_database(&snapshot)?;
            let schema = read_schema(&snapshot, "main")?;
            let info = BackupInfo {
                id: id.clone(),
                created_at: chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
                bytes,
                counts: database_counts(&snapshot, "main")?,
                kind,
                sequence,
            };
            let manifest = Manifest {
                format_version: FORMAT_VERSION,
                database_version: DATABASE_VERSION,
                application: "aidoku-kindle".into(),
                application_version: env!("CARGO_PKG_VERSION").into(),
                scope: "Library metadata and account configuration; image/cache files and source binaries are excluded".into(),
                info: info.clone(),
                sha256: file_hash(&pending_database, MAX_BACKUP_BYTES)?,
                schema,
            };
            drop(snapshot);
            let bytes = serde_json::to_vec_pretty(&manifest)?;
            ensure!(
                bytes.len() as u64 <= MAX_MANIFEST_BYTES,
                "The backup manifest is too large"
            );
            let mut manifest_file = create_private_file(&pending_manifest)?;
            owns_pending_manifest = true;
            manifest_file.write_all(&bytes)?;
            manifest_file
                .sync_all()
                .context("Sync the backup manifest")?;
            drop(manifest_file);
            // A backup is listed only after its complete manifest is published.
            ensure!(
                !final_database.exists() && !final_manifest.exists(),
                "The backup name already exists"
            );
            fs::rename(&pending_database, &final_database)?;
            published_database = true;
            fs::rename(&pending_manifest, &final_manifest)?;
            published_manifest = true;
            sync_directory(self.backup_directory(kind))?;
            Ok(info)
        })();
        if result.is_err() {
            let owned = [
                (&pending_database, true),
                (&pending_manifest, owns_pending_manifest),
                (&final_database, published_database),
                (&final_manifest, published_manifest),
            ];
            for (path, created) in owned {
                if !created {
                    continue;
                }
                if regular_file(path).is_ok() {
                    let _ = fs::remove_file(path);
                }
            }
        }
        result.context("Create a local metadata backup")
    }

    /// Perform file, digest, schema and integrity checks before showing a final
    /// restore confirmation. This operation belongs on a background worker.
    pub fn prepare_restore(&self, id: &str) -> Result<PreparedRestore> {
        self.validate_layout()?;
        let manifest = self.read_manifest(id)?;
        let original = self.database_path(id)?;
        ensure!(
            regular_file(&original)?.len() == manifest.info.bytes,
            "The backup length changed"
        );
        ensure!(
            file_hash(&original, MAX_BACKUP_BYTES)? == manifest.sha256,
            "The backup checksum does not match"
        );
        let path = self
            .staging
            .join(format!("{}.sqlite3", fresh_id("aidoku-restore")?));
        let mut output = create_private_file(&path)?;
        let copied: Result<()> = (|| {
            let mut input = open_regular_file(&original)?.take(MAX_BACKUP_BYTES + 1);
            let bytes = std::io::copy(&mut input, &mut output)?;
            ensure!(
                bytes == manifest.info.bytes,
                "The backup changed while preparing restore"
            );
            output.sync_all()?;
            Ok(())
        })();
        drop(output);
        if let Err(error) = copied {
            let _ = fs::remove_file(&path);
            return Err(error);
        }
        let prepared = PreparedRestore {
            info: manifest.info,
            path,
            data_root: self.data_root.clone(),
            sha256: manifest.sha256,
            schema: manifest.schema,
        };
        self.validate_prepared(&prepared)?;
        Ok(prepared)
    }

    /// Import rows inside one rollback-journal transaction. The caller must
    /// first quiesce application/source jobs and stop the download worker, then
    /// rebuild managers and in-memory UI state after a successful return.
    /// This method deliberately never renames or overwrites the live database.
    pub fn apply_restore(&self, prepared: PreparedRestore) -> Result<RestoreReport> {
        self.validate_layout()?;
        self.validate_prepared(&prepared)?;
        let rollback = self
            .create_rollback()
            .context("Preserve an automatic rollback backup before restore")?;
        let database = self.database()?;
        let mut connection = Connection::open_with_flags(
            database,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_URI,
        )?;
        configure_connection(&connection)?;
        let live_schema = read_schema(&connection, "main")?;
        ensure_compatible(&prepared.schema, &live_schema)?;
        let mut source_url = url::Url::from_file_path(&prepared.path)
            .map_err(|_| anyhow::anyhow!("The prepared backup path is invalid"))?;
        source_url.set_query(Some("mode=ro&immutable=1"));
        connection.execute(
            "ATTACH DATABASE ?1 AS aidoku_restore",
            [source_url.as_str()],
        )?;
        // No other connection's FK policy changes. This auxiliary connection
        // validates all restored references before publishing its transaction.
        connection.pragma_update(None, "foreign_keys", false)?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        // Page deletion triggers need their old counter rows until all pages
        // have been removed. Other tables can be cleared with FKs disabled.
        transaction.execute("DELETE FROM main.pages", [])?;
        for table in live_schema.keys().filter(|name| name.as_str() != "pages") {
            transaction.execute(&format!("DELETE FROM main.{}", quoted(table)?), [])?;
        }
        for table in prepared.schema.keys().filter(|name| {
            !matches!(
                name.as_str(),
                "chapter_page_stats" | "download_sleep_ownership"
            )
        }) {
            let columns = prepared.schema[table]
                .iter()
                .map(|column| quoted(&column.name))
                .collect::<Result<Vec<_>>>()?
                .join(",");
            let table = quoted(table)?;
            transaction.execute(
                &format!("INSERT INTO main.{table}({columns}) SELECT {columns} FROM aidoku_restore.{table}"),
                [],
            )?;
        }
        // Restoring an old checkpoint cannot grant an old process ownership
        // of a new download. Explicit user resumes reuse validated raw files.
        let paused_downloads = transaction.execute(
            "UPDATE downloads SET state='paused',document=json_set(document,'$.state','paused') WHERE state IN ('queued','downloading')",
            [],
        )? as u64;
        let invalid_references: bool = transaction.query_row(
            "SELECT EXISTS(SELECT 1 FROM pragma_foreign_key_check)",
            [],
            |row| row.get(0),
        )?;
        ensure!(
            !invalid_references,
            "The restored metadata has invalid references"
        );
        let counters_match: bool = transaction.query_row(
            "SELECT NOT EXISTS(SELECT chapter_id,COUNT(*),SUM(json_extract(document,'$.local_path') IS NOT NULL) FROM pages GROUP BY chapter_id EXCEPT SELECT chapter_id,total,mapped FROM chapter_page_stats) AND NOT EXISTS(SELECT chapter_id,total,mapped FROM chapter_page_stats WHERE total<>0 EXCEPT SELECT chapter_id,COUNT(*),SUM(json_extract(document,'$.local_path') IS NOT NULL) FROM pages GROUP BY chapter_id)",
            [], |row| row.get(0),
        )?;
        ensure!(
            counters_match,
            "The restored manifest counters do not match"
        );
        ensure!(
            database_counts(&transaction, "main")? == prepared.info.counts,
            "The restored metadata counts changed"
        );
        transaction
            .commit()
            .context("Commit the metadata restore")?;
        // Dropping the connection releases the attached snapshot. A cleanup
        // error after COMMIT must not report a successfully applied restore as
        // failed and leave the caller using stale in-memory managers.
        drop(connection);
        Ok(RestoreReport {
            restored: prepared.info.clone(),
            rollback,
            paused_downloads,
        })
    }

    fn validate_prepared(&self, prepared: &PreparedRestore) -> Result<()> {
        self.validate_layout()?;
        ensure!(
            prepared.data_root == self.data_root,
            "The prepared restore belongs to another data directory"
        );
        ensure!(
            prepared.path.parent() == Some(self.staging.as_path()),
            "The prepared restore is outside its staging directory"
        );
        ensure!(
            regular_file(&prepared.path)?.len() == prepared.info.bytes,
            "The prepared backup length changed"
        );
        ensure!(
            file_hash(&prepared.path, MAX_BACKUP_BYTES)? == prepared.sha256,
            "The prepared backup checksum changed"
        );
        let snapshot = open_snapshot(&prepared.path)?;
        validate_database(&snapshot)?;
        ensure!(
            read_schema(&snapshot, "main")? == prepared.schema,
            "The backup schema does not match its manifest"
        );
        ensure!(
            database_counts(&snapshot, "main")? == prepared.info.counts,
            "The backup counts do not match its manifest"
        );
        let live = open_snapshot(&self.database()?)?;
        ensure_compatible(&prepared.schema, &read_schema(&live, "main")?)
    }

    fn read_manifest(&self, id: &str) -> Result<Manifest> {
        self.validate_layout()?;
        let path = self.manifest_path(id)?;
        ensure!(
            regular_file(&path)?.len() <= MAX_MANIFEST_BYTES,
            "The backup manifest is too large"
        );
        let mut bytes = Vec::new();
        open_regular_file(&path)?
            .take(MAX_MANIFEST_BYTES + 1)
            .read_to_end(&mut bytes)?;
        ensure!(
            bytes.len() as u64 <= MAX_MANIFEST_BYTES,
            "The backup manifest grew beyond its limit"
        );
        let manifest: Manifest =
            serde_json::from_slice(&bytes).context("Read the backup manifest")?;
        ensure!(
            manifest.format_version == FORMAT_VERSION
                && manifest.database_version == DATABASE_VERSION,
            "The backup format is unsupported"
        );
        ensure!(
            manifest.application == "aidoku-kindle" && manifest.info.id == id,
            "The backup identity does not match"
        );
        ensure!(
            id_kind(id) == Some(manifest.info.kind),
            "The backup kind does not match its directory"
        );
        ensure!(
            manifest.info.bytes > 0 && manifest.info.bytes <= MAX_BACKUP_BYTES,
            "The backup length is invalid"
        );
        ensure!(
            manifest.sha256.len() == 64
                && manifest.sha256.bytes().all(|byte| byte.is_ascii_hexdigit()),
            "The backup checksum is invalid"
        );
        ensure!(
            !manifest.schema.is_empty() && manifest.schema.len() <= 64,
            "The backup schema is invalid"
        );
        for table in REQUIRED_TABLES {
            ensure!(
                manifest.schema.contains_key(*table),
                "The backup is missing required library tables"
            );
        }
        Ok(manifest)
    }

    fn database(&self) -> Result<PathBuf> {
        let path = self.data_root.join("library.sqlite3");
        regular_file(&path)?;
        Ok(path)
    }

    fn database_path(&self, id: &str) -> Result<PathBuf> {
        let kind = id_kind(id).context("The backup identifier is invalid")?;
        Ok(self.backup_directory(kind).join(format!("{id}.sqlite3")))
    }

    fn manifest_path(&self, id: &str) -> Result<PathBuf> {
        let kind = id_kind(id).context("The backup identifier is invalid")?;
        Ok(self.backup_directory(kind).join(format!("{id}.json")))
    }

    fn backup_directory(&self, kind: BackupKind) -> &Path {
        match kind {
            BackupKind::Manual => &self.root,
            BackupKind::AutomaticRollback => &self.rollbacks,
        }
    }

    fn prune_rollbacks(&self, preserve: Option<&str>) -> Result<()> {
        let mut snapshots = self.list_kind(BackupKind::AutomaticRollback, MAX_ROLLBACKS + 1)?;
        // Keep the just-published snapshot even if the clock moved backwards.
        snapshots.sort_by(|left, right| {
            let left_current = preserve == Some(left.id.as_str());
            let right_current = preserve == Some(right.id.as_str());
            right_current
                .cmp(&left_current)
                .then_with(|| right.sequence.cmp(&left.sequence))
                .then_with(|| right.created_at.cmp(&left.created_at))
                .then_with(|| right.id.cmp(&left.id))
        });
        for info in snapshots.into_iter().skip(MAX_ROLLBACKS) {
            ensure!(
                info.kind == BackupKind::AutomaticRollback
                    && id_kind(&info.id) == Some(BackupKind::AutomaticRollback),
                "Only automatic rollback snapshots can be pruned"
            );
            let database = self.database_path(&info.id)?;
            // A retry can finish removing a manifest after a previous attempt
            // already removed its SQLite file. Links and other file kinds are
            // never removed through this retention policy.
            match fs::symlink_metadata(&database) {
                Ok(_) => {
                    regular_file(&database)?;
                    fs::remove_file(&database)?;
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => {
                    return Err(error).context("Inspect an old automatic rollback snapshot");
                }
            }
            let manifest = self.manifest_path(&info.id)?;
            regular_file(&manifest)?;
            fs::remove_file(&manifest).context("Remove an old automatic rollback manifest")?;
        }
        sync_directory(&self.rollbacks)
    }

    fn validate_layout(&self) -> Result<()> {
        for path in [&self.data_root, &self.root, &self.staging, &self.rollbacks] {
            let metadata =
                fs::symlink_metadata(path).context("Inspect the backup directory layout")?;
            ensure!(
                metadata.is_dir() && !metadata.file_type().is_symlink(),
                "A backup directory was replaced"
            );
            ensure!(
                path.canonicalize()? == *path,
                "A backup directory moved outside its data root"
            );
        }
        Ok(())
    }
}

fn id_kind(id: &str) -> Option<BackupKind> {
    if id.len() > 96
        || !id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return None;
    }
    if id.starts_with("aidoku-backup-") {
        Some(BackupKind::Manual)
    } else if id.starts_with("aidoku-rollback-") {
        Some(BackupKind::AutomaticRollback)
    } else {
        None
    }
}

fn fresh_id(prefix: &str) -> Result<String> {
    let mut entropy = [0_u8; 8];
    getrandom::fill(&mut entropy)
        .map_err(|error| anyhow::anyhow!("Generate a backup identifier: {error}"))?;
    let suffix: String = entropy.iter().map(|byte| format!("{byte:02x}")).collect();
    Ok(format!(
        "{prefix}-{}-{suffix}",
        chrono::Utc::now().format("%Y%m%dT%H%M%SZ")
    ))
}

fn configure_connection(connection: &Connection) -> Result<()> {
    connection.busy_timeout(Duration::from_secs(5))?;
    connection.pragma_update(None, "mmap_size", 0)?;
    connection.pragma_update(None, "synchronous", "FULL")?;
    connection.pragma_update(None, "trusted_schema", false)?;
    Ok(())
}

fn open_snapshot(path: &Path) -> Result<Connection> {
    regular_file(path)?;
    let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    connection.busy_timeout(Duration::from_secs(5))?;
    connection.pragma_update(None, "mmap_size", 0)?;
    connection.pragma_update(None, "trusted_schema", false)?;
    connection.pragma_update(None, "query_only", true)?;
    Ok(connection)
}

fn validate_database(connection: &Connection) -> Result<()> {
    let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
    ensure!(
        version == DATABASE_VERSION,
        "The backup database version is unsupported"
    );
    let check: String = connection.query_row("PRAGMA quick_check", [], |row| row.get(0))?;
    ensure!(check == "ok", "The backup database is corrupt");
    let foreign_key_error: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM pragma_foreign_key_check)",
        [],
        |row| row.get(0),
    )?;
    ensure!(
        !foreign_key_error,
        "The backup contains broken library references"
    );
    let schema = read_schema(connection, "main")?;
    for table in REQUIRED_TABLES {
        ensure!(
            schema.contains_key(*table),
            "The backup is missing required library tables"
        );
    }
    for (table, columns) in &schema {
        if columns.iter().any(|column| column.name == "document") {
            let malformed: bool = connection.query_row(
                &format!("SELECT EXISTS(SELECT 1 FROM {} WHERE length(document)>?1 OR NOT json_valid(document))", quoted(table)?),
                [MAX_DOCUMENT_BYTES], |row| row.get(0),
            )?;
            ensure!(
                !malformed,
                "The backup contains an invalid metadata document"
            );
        }
    }
    validate_documents(connection)?;
    let ownership_matches: bool = connection.query_row(
        "SELECT NOT EXISTS(SELECT 1 FROM progress p JOIN chapters c ON c.id=p.chapter_id WHERE p.book_id<>c.book_id) AND NOT EXISTS(SELECT 1 FROM bookmarks b JOIN chapters c ON c.id=b.chapter_id WHERE b.book_id<>c.book_id) AND NOT EXISTS(SELECT 1 FROM downloads d JOIN chapters c ON c.id=d.chapter_id WHERE d.book_id<>c.book_id)",
        [], |row| row.get(0),
    )?;
    ensure!(
        ownership_matches,
        "A backup chapter belongs to a different book"
    );
    let contiguous_pages: bool = connection.query_row(
        "SELECT NOT EXISTS(SELECT 1 FROM pages GROUP BY chapter_id HAVING MIN(position)<>0 OR MAX(position)<>COUNT(*)-1)",
        [], |row| row.get(0),
    )?;
    ensure!(contiguous_pages, "The backup manifest contains a page gap");
    Ok(())
}

fn validate_documents(connection: &Connection) -> Result<()> {
    use crate::domain::{
        Book, Chapter, DownloadJob, NavigationSession, Page, Preferences, SourceConfig,
    };

    let document = |text: String| -> Result<serde_json::Value> {
        ensure!(
            text.len() <= MAX_DOCUMENT_BYTES,
            "A backup metadata document is too large"
        );
        let value: serde_json::Value = serde_json::from_str(&text)?;
        ensure!(
            value.is_object(),
            "A backup metadata document is not an object"
        );
        Ok(value)
    };
    let mut statement = connection
        .prepare("SELECT id,source_id,remote_id,in_library,category,document FROM books")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        let book: Book = decode_document(row.get(5)?)?;
        ensure!(
            book.id == row.get::<_, String>(0)?
                && book.source_id == row.get::<_, String>(1)?
                && book.remote_id == row.get::<_, String>(2)?
                && book.in_library == row.get::<_, bool>(3)?
                && book.category == row.get::<_, String>(4)?,
            "A backup book identity does not match its document"
        );
    }
    let mut statement =
        connection.prepare("SELECT id,book_id,source_id,remote_id,document FROM chapters")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        let chapter: Chapter = decode_document(row.get(4)?)?;
        ensure!(
            chapter.id == row.get::<_, String>(0)?
                && chapter.book_id == row.get::<_, String>(1)?
                && chapter.source_id == row.get::<_, String>(2)?
                && chapter.remote_id == row.get::<_, String>(3)?
                && chapter.number.is_finite(),
            "A backup chapter identity does not match its document"
        );
    }
    let mut statement = connection.prepare("SELECT position,document FROM pages")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        let position: i64 = row.get(0)?;
        let _: Page = decode_document(row.get(1)?)?;
        ensure!(position >= 0, "A backup page position is invalid");
    }
    let mut statement = connection.prepare("SELECT id,document FROM sources")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        let source: SourceConfig = decode_document(row.get(1)?)?;
        ensure!(
            !source.id.is_empty()
                && !source.kind.is_empty()
                && source.id == row.get::<_, String>(0)?,
            "A backup source identity does not match its document"
        );
    }
    let mut statement =
        connection.prepare("SELECT chapter_id,book_id,state,document FROM downloads")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        let job: DownloadJob = decode_document(row.get(3)?)?;
        ensure!(
            job.chapter_id == row.get::<_, String>(0)?
                && job.book_id == row.get::<_, String>(1)?
                && job.state == row.get::<_, String>(2)?
                && job.completed <= job.total
                && matches!(
                    job.state.as_str(),
                    "queued" | "downloading" | "paused" | "cancelled" | "completed" | "failed"
                ),
            "A backup download identity does not match its document"
        );
    }
    let mut statement = connection.prepare("SELECT key,document FROM preferences")?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        let key: String = row.get(0)?;
        let text: String = row.get(1)?;
        match key.as_str() {
            "application" => {
                let preferences: Preferences = decode_document(text)?;
                ensure!(
                    matches!(
                        preferences.reading_direction.as_str(),
                        "rtl" | "ltr" | "vertical" | "vertical-pages"
                    ) && matches!(preferences.refresh_every, 0 | 1 | 3 | 6 | 10)
                        && preferences.brightness <= 24
                        && preferences.warmth <= 24
                        && preferences.footer_mode < 4
                        && (-100..=100).contains(&preferences.contrast),
                    "The backup application preferences are invalid"
                );
            }
            "navigation-session" => {
                let session: NavigationSession = decode_document(text)?;
                ensure!(
                    session.version == 1
                        && session
                            .reader
                            .as_ref()
                            .is_none_or(|reader| reader.zoom.is_finite()),
                    "The backup navigation checkpoint is unsupported"
                );
            }
            key if key.starts_with("ui:") => {
                document(text)?;
            }
            _ => {
                let _: serde_json::Value = serde_json::from_str(&text)?;
            }
        }
    }
    Ok(())
}

fn decode_document<T: DeserializeOwned>(text: String) -> Result<T> {
    ensure!(
        text.len() <= MAX_DOCUMENT_BYTES,
        "A backup metadata document is too large"
    );
    serde_json::from_str(&text).context("Decode a backup metadata document")
}

fn read_schema(connection: &Connection, database: &str) -> Result<Schema> {
    let database = quoted(database)?;
    let mut statement = connection.prepare(&format!(
        "SELECT name,sql FROM {database}.sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ))?;
    let tables = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    ensure!(tables.len() <= 64, "The library schema has too many tables");
    let mut schema = Schema::new();
    for (name, sql) in tables {
        let table = quoted(&name)?;
        ensure!(
            sql.trim_start()
                .to_ascii_uppercase()
                .starts_with("CREATE TABLE"),
            "Virtual backup tables are unsupported"
        );
        let mut columns = connection.prepare(&format!("PRAGMA {database}.table_info({table})"))?;
        let columns = columns
            .query_map([], |row| {
                Ok(Column {
                    name: row.get(1)?,
                    declared_type: row.get(2)?,
                    not_null: row.get(3)?,
                    primary_key: row.get(5)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        ensure!(
            !columns.is_empty() && columns.len() <= 64,
            "The backup table columns are invalid"
        );
        for column in &columns {
            quoted(&column.name)?;
        }
        schema.insert(name, columns);
    }
    Ok(schema)
}

fn ensure_compatible(snapshot: &Schema, live: &Schema) -> Result<()> {
    ensure!(
        snapshot == live,
        "The backup schema is incompatible with this application"
    );
    for table in REQUIRED_TABLES {
        ensure!(
            snapshot.contains_key(*table),
            "The backup is missing required library tables"
        );
    }
    Ok(())
}

fn database_counts(connection: &Connection, database: &str) -> Result<BackupCounts> {
    let database = quoted(database)?;
    let count = |table: &str| -> Result<u64> {
        Ok(connection.query_row(
            &format!("SELECT COUNT(*) FROM {database}.{}", quoted(table)?),
            [],
            |row| row.get(0),
        )?)
    };
    Ok(BackupCounts {
        books: count("books")?,
        chapters: count("chapters")?,
        progress: count("progress")?,
        bookmarks: count("bookmarks")?,
    })
}

fn quoted(identifier: &str) -> Result<String> {
    ensure!(
        !identifier.is_empty()
            && identifier.len() <= 128
            && identifier
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_'),
        "The library identifier is invalid"
    );
    Ok(format!("\"{identifier}\""))
}

fn ensure_directory(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => ensure!(
            metadata.is_dir() && !metadata.file_type().is_symlink(),
            "A backup directory is not a real directory"
        ),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir(path).context("Create a private backup directory")?
        }
        Err(error) => return Err(error).context("Inspect a backup directory"),
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

fn regular_file(path: &Path) -> Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path).context("Inspect a backup file")?;
    ensure!(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        "A backup path is not a regular file"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        ensure!(metadata.nlink() == 1, "A backup file has multiple links");
    }
    Ok(metadata)
}

fn create_private_file(path: &Path) -> Result<File> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    options.open(path).context("Create a private backup file")
}

fn open_regular_file(path: &Path) -> Result<File> {
    regular_file(path)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    Ok(options.open(path)?)
}

fn file_hash(path: &Path, limit: u64) -> Result<String> {
    let before = regular_file(path)?;
    ensure!(
        before.len() <= limit,
        "The backup file exceeds its byte limit"
    );
    let mut file = open_regular_file(path)?.take(limit + 1);
    let mut hash = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    let mut bytes = 0_u64;
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        bytes += read as u64;
        ensure!(bytes <= limit, "The backup file grew beyond its byte limit");
        hash.update(&buffer[..read]);
    }
    let after = regular_file(path)?;
    ensure!(
        bytes == before.len()
            && before.len() == after.len()
            && before.modified()? == after.modified()?,
        "The backup file changed while reading"
    );
    Ok(format!("{:x}", hash.finalize()))
}

fn sync_directory(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        File::open(path)?
            .sync_all()
            .context("Sync the backup directory")?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        domain::{Book, Chapter, Page, Progress},
        store::Store,
    };

    fn fixture(directory: &Path) -> Store {
        let store = Store::open(directory.join("library.sqlite3")).unwrap();
        let book = Book {
            id: "local:book".into(),
            source_id: "local".into(),
            remote_id: "book".into(),
            title: "Original".into(),
            in_library: true,
            ..Book::default()
        };
        store.upsert_book(&book).unwrap();
        let chapter = Chapter {
            id: "local:chapter".into(),
            book_id: book.id.clone(),
            source_id: "local".into(),
            remote_id: "chapter".into(),
            page_count: 1,
            ..Chapter::default()
        };
        store.save_chapters(&book.id, &[chapter]).unwrap();
        store
            .save_pages(
                "local:chapter",
                &[Page {
                    url: "https://example.test/1.png".into(),
                    ..Page::default()
                }],
            )
            .unwrap();
        store
            .save_progress(&Progress {
                book_id: book.id,
                chapter_id: "local:chapter".into(),
                page: 1,
                total: 1,
                updated_at: 1,
                completed: false,
            })
            .unwrap();
        store
    }

    #[test]
    fn snapshot_restores_metadata_without_replacing_live_connection() {
        let directory = tempfile::tempdir().unwrap();
        let store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let backup = manager.create().unwrap();
        let mut changed = store.book("local:book").unwrap().unwrap();
        changed.title = "Changed".into();
        store.upsert_book(&changed).unwrap();
        assert_eq!(store.book("local:book").unwrap().unwrap().title, "Changed");
        let prepared = manager.prepare_restore(&backup.id).unwrap();
        let report = manager.apply_restore(prepared).unwrap();
        assert_eq!(store.book("local:book").unwrap().unwrap().title, "Original");
        assert_eq!(store.page_window("local:chapter", 0, 1).unwrap().total, 1);
        assert_ne!(report.rollback.id, backup.id);
        assert_eq!(manager.list().unwrap().len(), 2);
    }

    #[test]
    fn corrupt_snapshot_is_rejected_before_live_writes() {
        let directory = tempfile::tempdir().unwrap();
        let store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let backup = manager.create().unwrap();
        fs::write(manager.database_path(&backup.id).unwrap(), b"corrupt").unwrap();
        assert!(manager.prepare_restore(&backup.id).is_err());
        assert_eq!(store.book("local:book").unwrap().unwrap().title, "Original");
    }

    #[test]
    fn prepared_copy_is_independent_and_removed_after_review_is_cancelled() {
        let directory = tempfile::tempdir().unwrap();
        let _store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let backup = manager.create().unwrap();
        let prepared = manager.prepare_restore(&backup.id).unwrap();
        let path = prepared.path.clone();
        fs::write(manager.database_path(&backup.id).unwrap(), b"changed later").unwrap();
        assert!(manager.validate_prepared(&prepared).is_ok());
        drop(prepared);
        assert!(!path.exists());
    }

    #[test]
    fn traversal_and_foreign_restore_managers_are_rejected() {
        let first = tempfile::tempdir().unwrap();
        let _first_store = fixture(first.path());
        let manager = BackupManager::open(first.path()).unwrap();
        for id in [
            "../library",
            "aidoku-backup-/../../library",
            "C:\\library",
            "aidoku-backup-..",
        ] {
            assert!(manager.prepare_restore(id).is_err());
        }
        let info = manager.create().unwrap();
        let prepared = manager.prepare_restore(&info.id).unwrap();
        let second = tempfile::tempdir().unwrap();
        let _second_store = fixture(second.path());
        assert!(
            BackupManager::open(second.path())
                .unwrap()
                .apply_restore(prepared)
                .is_err()
        );
    }

    #[test]
    fn inconsistent_foreign_keys_are_rejected_before_restore_writes() {
        let directory = tempfile::tempdir().unwrap();
        let store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let backup = manager.create().unwrap();
        let mut prepared = manager.prepare_restore(&backup.id).unwrap();
        let connection = Connection::open(&prepared.path).unwrap();
        // Corrupt only the private snapshot. The bundled SQLite may enable FK
        // enforcement by default, so deliberately disable it for this fault
        // injection rather than failing before the restore validator runs.
        connection
            .pragma_update(None, "foreign_keys", false)
            .unwrap();
        connection
            .execute("UPDATE chapters SET book_id='missing'", [])
            .unwrap();
        drop(connection);
        prepared.sha256 = file_hash(&prepared.path, MAX_BACKUP_BYTES).unwrap();
        let error = manager.apply_restore(prepared).unwrap_err();
        assert!(
            error.to_string().contains("broken library references"),
            "Unexpected rejection: {error:#}"
        );
        assert_eq!(store.book("local:book").unwrap().unwrap().title, "Original");
        assert_eq!(store.progress("local:book").unwrap().unwrap().total, 1);
        assert_eq!(store.page_window("local:chapter", 0, 1).unwrap().total, 1);
        // No automatic rollback is needed because rejection precedes import.
        assert_eq!(manager.list().unwrap().len(), 1);
    }

    #[test]
    fn valid_json_with_an_invalid_document_is_rejected() {
        let directory = tempfile::tempdir().unwrap();
        let store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let backup = manager.create().unwrap();
        let mut prepared = manager.prepare_restore(&backup.id).unwrap();
        let connection = Connection::open(&prepared.path).unwrap();
        connection
            .execute("UPDATE books SET document='null'", [])
            .unwrap();
        drop(connection);
        prepared.sha256 = file_hash(&prepared.path, MAX_BACKUP_BYTES).unwrap();
        assert!(manager.apply_restore(prepared).is_err());
        assert_eq!(store.book("local:book").unwrap().unwrap().title, "Original");
        assert_eq!(manager.list().unwrap().len(), 1);
    }

    #[test]
    fn an_insert_failure_rolls_back_deleted_books_progress_and_page_counters() {
        let directory = tempfile::tempdir().unwrap();
        let store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let backup = manager.create().unwrap();
        let prepared = manager.prepare_restore(&backup.id).unwrap();
        let mut current = store.book("local:book").unwrap().unwrap();
        current.title = "Current".into();
        store.upsert_book(&current).unwrap();
        store.test_connection().execute_batch(
            "CREATE TRIGGER reject_old_title BEFORE INSERT ON books WHEN NEW.title='Original' BEGIN SELECT RAISE(ABORT,'Injected restore failure'); END;",
        ).unwrap();
        assert!(manager.apply_restore(prepared).is_err());
        assert_eq!(store.book("local:book").unwrap().unwrap().title, "Current");
        assert_eq!(store.progress("local:book").unwrap().unwrap().total, 1);
        assert_eq!(store.page_window("local:chapter", 0, 1).unwrap().total, 1);
        assert_eq!(manager.list().unwrap().len(), 2);
    }

    #[test]
    fn a_backup_with_missing_new_tables_cannot_erase_them() {
        let directory = tempfile::tempdir().unwrap();
        let store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let backup = manager.create().unwrap();
        store.test_connection().execute_batch("CREATE TABLE extra_settings(value TEXT); INSERT INTO extra_settings VALUES('retained');").unwrap();
        assert!(manager.prepare_restore(&backup.id).is_err());
        let value: String = store
            .test_connection()
            .query_row("SELECT value FROM extra_settings", [], |row| row.get(0))
            .unwrap();
        assert_eq!(value, "retained");
    }

    #[test]
    fn a_full_manual_quota_still_allows_restore_and_preserves_a_usable_rollback() {
        let directory = tempfile::tempdir().unwrap();
        let store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let original = manager.create().unwrap();
        // Populate equivalent, independently owned manual snapshots without
        // timing 64 VACUUM operations. Every manifest has its own identity.
        for index in 1..MAX_BACKUPS {
            let id = format!("aidoku-backup-quota-{index}");
            let mut manifest = manager.read_manifest(&original.id).unwrap();
            manifest.info.id = id.clone();
            fs::copy(
                manager.database_path(&original.id).unwrap(),
                manager.database_path(&id).unwrap(),
            )
            .unwrap();
            fs::write(
                manager.manifest_path(&id).unwrap(),
                serde_json::to_vec(&manifest).unwrap(),
            )
            .unwrap();
        }
        assert_eq!(
            manager
                .list_kind(BackupKind::Manual, MAX_BACKUPS)
                .unwrap()
                .len(),
            MAX_BACKUPS
        );
        assert!(manager.create().is_err());
        let mut current = store.book("local:book").unwrap().unwrap();
        current.title = "Before restore".into();
        store.upsert_book(&current).unwrap();
        let report = manager
            .apply_restore(manager.prepare_restore(&original.id).unwrap())
            .unwrap();
        assert_eq!(report.rollback.kind, BackupKind::AutomaticRollback);
        assert_eq!(store.book("local:book").unwrap().unwrap().title, "Original");
        let rollback = manager.prepare_restore(&report.rollback.id).unwrap();
        let rollback_store = Store::open_read_only(&rollback.path).unwrap();
        assert_eq!(
            rollback_store.book("local:book").unwrap().unwrap().title,
            "Before restore"
        );
        assert_eq!(
            manager
                .list_kind(BackupKind::Manual, MAX_BACKUPS)
                .unwrap()
                .len(),
            MAX_BACKUPS
        );
        assert!(manager.database_path(&original.id).unwrap().is_file());
    }

    #[test]
    fn automatic_retention_replaces_only_older_automatic_snapshots() {
        let directory = tempfile::tempdir().unwrap();
        let store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let manual = manager.create().unwrap();
        let original_hash = file_hash(
            &manager.database_path(&manual.id).unwrap(),
            MAX_BACKUP_BYTES,
        )
        .unwrap();
        let mut rollbacks = Vec::new();
        for index in 0..5 {
            let mut current = store.book("local:book").unwrap().unwrap();
            current.title = format!("Current {index}");
            store.upsert_book(&current).unwrap();
            let report = manager
                .apply_restore(manager.prepare_restore(&manual.id).unwrap())
                .unwrap();
            assert!(manager.prepare_restore(&report.rollback.id).is_ok());
            rollbacks.push(report.rollback);
            assert!(
                manager
                    .list_kind(BackupKind::AutomaticRollback, MAX_ROLLBACKS + 1)
                    .unwrap()
                    .len()
                    <= MAX_ROLLBACKS
            );
        }
        assert_eq!(
            manager
                .list_kind(BackupKind::Manual, MAX_BACKUPS)
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            file_hash(
                &manager.database_path(&manual.id).unwrap(),
                MAX_BACKUP_BYTES
            )
            .unwrap(),
            original_hash
        );
        for removed in &rollbacks[..rollbacks.len() - MAX_ROLLBACKS] {
            assert!(!manager.database_path(&removed.id).unwrap().exists());
            assert!(!manager.manifest_path(&removed.id).unwrap().exists());
        }
        assert_eq!(manager.list().unwrap().len(), 1 + MAX_ROLLBACKS);
    }

    #[test]
    fn a_cleanup_retry_finishes_before_publishing_another_rollback() {
        let directory = tempfile::tempdir().unwrap();
        let _store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let first = manager.create_kind(BackupKind::AutomaticRollback).unwrap();
        let second = manager.create_kind(BackupKind::AutomaticRollback).unwrap();
        let third = manager.create_kind(BackupKind::AutomaticRollback).unwrap();
        assert_eq!(
            manager
                .list_kind(BackupKind::AutomaticRollback, MAX_ROLLBACKS + 1)
                .unwrap()
                .len(),
            3
        );
        assert!(manager.create_kind(BackupKind::AutomaticRollback).is_err());
        let current = manager.create_rollback().unwrap();
        assert!(manager.prepare_restore(&current.id).is_ok());
        let remaining = manager
            .list_kind(BackupKind::AutomaticRollback, MAX_ROLLBACKS + 1)
            .unwrap();
        assert_eq!(remaining.len(), MAX_ROLLBACKS);
        assert!(
            remaining
                .iter()
                .all(|info| info.kind == BackupKind::AutomaticRollback)
        );
        assert!(
            [first, second, third]
                .iter()
                .filter(|info| manager.database_path(&info.id).unwrap().exists())
                .count()
                <= 1
        );
    }

    #[test]
    fn rollback_retention_uses_creation_sequence_when_the_clock_moves_backwards() {
        let directory = tempfile::tempdir().unwrap();
        let _store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let oldest = manager.create_kind(BackupKind::AutomaticRollback).unwrap();
        let recent = manager.create_kind(BackupKind::AutomaticRollback).unwrap();
        let mut future_dated = manager.read_manifest(&oldest.id).unwrap();
        future_dated.info.created_at = "9999-12-31T23:59:59Z".into();
        fs::write(
            manager.manifest_path(&oldest.id).unwrap(),
            serde_json::to_vec(&future_dated).unwrap(),
        )
        .unwrap();
        let current = manager.create_rollback().unwrap();
        assert!(current.sequence > recent.sequence);
        assert!(!manager.database_path(&oldest.id).unwrap().exists());
        assert!(manager.prepare_restore(&recent.id).is_ok());
        assert!(manager.prepare_restore(&current.id).is_ok());
    }

    #[cfg(unix)]
    #[test]
    fn backup_symlinks_are_rejected() {
        use std::os::unix::fs::symlink;
        let directory = tempfile::tempdir().unwrap();
        let _store = fixture(directory.path());
        let manager = BackupManager::open(directory.path()).unwrap();
        let backup = manager.create().unwrap();
        let original = manager.database_path(&backup.id).unwrap();
        let moved = directory.path().join("snapshot.sqlite3");
        fs::rename(&original, &moved).unwrap();
        symlink(&moved, &original).unwrap();
        assert!(manager.prepare_restore(&backup.id).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn replaced_backup_parent_is_rejected_before_publication() {
        use std::os::unix::fs::symlink;
        let directory = tempfile::tempdir().unwrap();
        let _store = fixture(directory.path());
        let outside = tempfile::tempdir().unwrap();
        let manager = BackupManager::open(directory.path()).unwrap();
        fs::rename(&manager.root, directory.path().join("old-backups")).unwrap();
        symlink(outside.path(), &manager.root).unwrap();
        assert!(manager.create().is_err());
        assert_eq!(fs::read_dir(outside.path()).unwrap().count(), 0);
    }
}
