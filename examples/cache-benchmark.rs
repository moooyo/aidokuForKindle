//! Entry-count cache benchmark. Run only on the designated remote worker.

use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    ffi::{CStr, c_char, c_int, c_uint, c_void},
    fs,
    io::Cursor,
    path::{Path, PathBuf},
    sync::{
        Arc, Barrier, OnceLock,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    thread,
    time::{Duration, Instant, UNIX_EPOCH},
};

use aidoku_kindle::{
    cache::{CacheConfig, CacheManager},
    domain::Page,
    imaging,
};
use anyhow::{Context, Result, ensure};
use clap::{Parser, ValueEnum};
use rusqlite::{Connection, params};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

const FIXTURE_WIDTH: u32 = 8;
const FIXTURE_HEIGHT: u32 = 8;
const PIN_OWNER: &str = "download:cache-benchmark";
const FOREGROUND_OWNER: &str = "download:foreground-benchmark";
const ROLE_SETUP: usize = 0;
const ROLE_OPERATION: usize = 1;
const ROLE_FOREGROUND: usize = 2;
const ROLE_RACE: usize = 3;
const ROLE_NAMES: [&str; 4] = ["setup", "operation", "foreground", "race"];

thread_local! {
    static SQL_ROLE: Cell<usize> = const { Cell::new(ROLE_SETUP) };
    static ACTIVE_SQL: RefCell<[usize; 64]> = const { RefCell::new([0; 64]) };
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum Scenario {
    ObserverQualification,
    PopulatePublic,
    Seed,
    PinNoop,
    ClearForeground,
    RaceSafety,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
enum FixtureKind {
    Pinned,
    Clearable,
}

#[derive(Debug, Parser)]
struct Arguments {
    #[arg(long, value_enum)]
    scenario: Scenario,
    #[arg(long)]
    data_dir: PathBuf,
    #[arg(long, default_value_t = 1000)]
    entries: usize,
    #[arg(long, default_value_t = 1000)]
    pin_operations: usize,
    #[arg(long, value_enum, default_value_t = FixtureKind::Clearable)]
    fixture_kind: FixtureKind,
    #[arg(long, default_value_t = 1000)]
    foreground_interval_us: u64,
}

#[derive(Default)]
struct SqlCounters {
    statements: AtomicU64,
    dml_statements: AtomicU64,
    autocommit_dml_statements: AtomicU64,
    selects: AtomicU64,
    aggregate_selects: AtomicU64,
    begins: AtomicU64,
    commits: AtomicU64,
    rollbacks: AtomicU64,
    pragmas: AtomicU64,
    subprograms: AtomicU64,
    artifact_inserts: AtomicU64,
    artifact_updates: AtomicU64,
    artifact_deletes: AtomicU64,
    pin_inserts: AtomicU64,
    pin_updates: AtomicU64,
    pin_deletes: AtomicU64,
    alias_inserts: AtomicU64,
    alias_updates: AtomicU64,
    alias_deletes: AtomicU64,
    other_row_events: AtomicU64,
}

impl SqlCounters {
    fn snapshot(&self) -> Value {
        json!({
            "statements": self.statements.load(Ordering::Relaxed),
            "dml_statements": self.dml_statements.load(Ordering::Relaxed),
            "autocommit_dml_statements": self.autocommit_dml_statements.load(Ordering::Relaxed),
            "selects": self.selects.load(Ordering::Relaxed),
            "aggregate_selects": self.aggregate_selects.load(Ordering::Relaxed),
            "begins": self.begins.load(Ordering::Relaxed),
            "commits": self.commits.load(Ordering::Relaxed),
            "rollbacks": self.rollbacks.load(Ordering::Relaxed),
            "pragmas": self.pragmas.load(Ordering::Relaxed),
            "subprograms": self.subprograms.load(Ordering::Relaxed),
            "artifact_rows_inserted": self.artifact_inserts.load(Ordering::Relaxed),
            "artifact_rows_updated": self.artifact_updates.load(Ordering::Relaxed),
            "artifact_rows_deleted": self.artifact_deletes.load(Ordering::Relaxed),
            "pin_rows_inserted": self.pin_inserts.load(Ordering::Relaxed),
            "pin_rows_updated": self.pin_updates.load(Ordering::Relaxed),
            "pin_rows_deleted": self.pin_deletes.load(Ordering::Relaxed),
            "alias_rows_inserted": self.alias_inserts.load(Ordering::Relaxed),
            "alias_rows_updated": self.alias_updates.load(Ordering::Relaxed),
            "alias_rows_deleted": self.alias_deletes.load(Ordering::Relaxed),
            "other_row_events": self.other_row_events.load(Ordering::Relaxed),
        })
    }
}

struct SqlObserver {
    enabled: AtomicBool,
    connections: AtomicU64,
    work_activity: AtomicBool,
    work_activity_ns: AtomicU64,
    statement_tracking_overflows: AtomicU64,
    origin: OnceLock<Instant>,
    roles: [SqlCounters; 4],
}

impl SqlObserver {
    fn global() -> &'static Self {
        static OBSERVER: OnceLock<SqlObserver> = OnceLock::new();
        OBSERVER.get_or_init(|| Self {
            enabled: AtomicBool::new(false),
            connections: AtomicU64::new(0),
            work_activity: AtomicBool::new(false),
            work_activity_ns: AtomicU64::new(0),
            statement_tracking_overflows: AtomicU64::new(0),
            origin: OnceLock::new(),
            roles: std::array::from_fn(|_| SqlCounters::default()),
        })
    }

    fn role(&self) -> &SqlCounters {
        let role = SQL_ROLE.with(Cell::get).min(self.roles.len() - 1);
        &self.roles[role]
    }

    fn snapshot(&self) -> Value {
        let roles = ROLE_NAMES
            .iter()
            .zip(&self.roles)
            .map(|(name, counters)| ((*name).to_owned(), counters.snapshot()))
            .collect::<BTreeMap<_, _>>();
        json!({
            "method": "SQLite auto-extension trace_v2 statement events and update_hook row events",
            "scope": "Only cache/shared-v1/index.sqlite3; preparation and correctness scans excluded",
            "cache_connections_observed": self.connections.load(Ordering::Relaxed),
            "statement_tracking_overflows": self.statement_tracking_overflows.load(Ordering::Relaxed),
            "roles": roles,
            "limits": "DML statement attempts and row events are not physical disk write or fsync counts. Autocommit DML statements are reported separately from explicit COMMIT statements.",
        })
    }
}

struct ObserverRegistration;

impl ObserverRegistration {
    fn install() -> Result<Self> {
        let _ = SqlObserver::global();
        // The binding supplies SQLite's documented extension entry signature.
        // Callback userdata lives for this process, including any connection
        // that outlives registration.
        let status = unsafe { rusqlite::ffi::sqlite3_auto_extension(Some(observe_connection)) };
        ensure!(
            status == rusqlite::ffi::SQLITE_OK,
            "Register the cache SQL observer: {status}"
        );
        Ok(Self)
    }
}

impl Drop for ObserverRegistration {
    fn drop(&mut self) {
        SqlObserver::global()
            .enabled
            .store(false, Ordering::Release);
        unsafe { rusqlite::ffi::sqlite3_cancel_auto_extension(Some(observe_connection)) };
    }
}

unsafe extern "C" fn observe_connection(
    database: *mut rusqlite::ffi::sqlite3,
    _error: *mut *mut c_char,
    _api: *const rusqlite::ffi::sqlite3_api_routines,
) -> c_int {
    let filename = unsafe { rusqlite::ffi::sqlite3_db_filename(database, c"main".as_ptr()) };
    if filename.is_null() {
        return rusqlite::ffi::SQLITE_OK;
    }
    let filename = unsafe { CStr::from_ptr(filename) }.to_bytes();
    if !filename.ends_with(b"/cache/shared-v1/index.sqlite3") {
        return rusqlite::ffi::SQLITE_OK;
    }
    let observer = SqlObserver::global();
    observer.connections.fetch_add(1, Ordering::Relaxed);
    let context = (observer as *const SqlObserver).cast_mut().cast::<c_void>();
    unsafe {
        rusqlite::ffi::sqlite3_trace_v2(
            database,
            (rusqlite::ffi::SQLITE_TRACE_STMT | rusqlite::ffi::SQLITE_TRACE_PROFILE) as c_uint,
            Some(trace_statement),
            context,
        );
        rusqlite::ffi::sqlite3_update_hook(database, Some(trace_row), context);
    }
    rusqlite::ffi::SQLITE_OK
}

fn token_is(sql: &[u8], token: &[u8]) -> bool {
    sql.len() >= token.len()
        && sql[..token.len()].eq_ignore_ascii_case(token)
        && sql
            .get(token.len())
            .is_none_or(|value| !value.is_ascii_alphabetic())
}

unsafe extern "C" fn trace_statement(
    event: c_uint,
    context: *mut c_void,
    statement: *mut c_void,
    sql: *mut c_void,
) -> c_int {
    let observer = unsafe { &*context.cast::<SqlObserver>() };
    let identifier = statement as usize;
    if event == rusqlite::ffi::SQLITE_TRACE_PROFILE as c_uint {
        ACTIVE_SQL.with(|active| {
            let mut active = active.borrow_mut();
            if let Some(slot) = active.iter_mut().find(|value| **value == identifier) {
                *slot = 0;
            }
        });
        return 0;
    }
    if event != rusqlite::ffi::SQLITE_TRACE_STMT as c_uint || sql.is_null() {
        return 0;
    }
    let repeated_active_statement = ACTIVE_SQL.with(|active| {
        let mut active = active.borrow_mut();
        if active.contains(&identifier) {
            return true;
        }
        if let Some(slot) = active.iter_mut().find(|value| **value == 0) {
            *slot = identifier;
        } else {
            observer
                .statement_tracking_overflows
                .fetch_add(1, Ordering::Relaxed);
        }
        false
    });
    if !observer.enabled.load(Ordering::Acquire) {
        return 0;
    }
    let sql = unsafe { CStr::from_ptr(sql.cast::<c_char>()) }
        .to_bytes()
        .trim_ascii_start();
    let counters = observer.role();
    if repeated_active_statement || sql.starts_with(b"--") {
        counters.subprograms.fetch_add(1, Ordering::Relaxed);
        return 0;
    }
    counters.statements.fetch_add(1, Ordering::Relaxed);
    let role = SQL_ROLE.with(Cell::get);
    if role == ROLE_OPERATION {
        if !observer.work_activity.load(Ordering::Relaxed) {
            if let Some(origin) = observer.origin.get() {
                observer
                    .work_activity_ns
                    .store(elapsed_ns(*origin), Ordering::Relaxed);
            }
        }
        observer.work_activity.store(true, Ordering::Release);
    }
    if token_is(sql, b"BEGIN") || token_is(sql, b"SAVEPOINT") {
        counters.begins.fetch_add(1, Ordering::Relaxed);
    } else if token_is(sql, b"COMMIT") || token_is(sql, b"END") || token_is(sql, b"RELEASE") {
        counters.commits.fetch_add(1, Ordering::Relaxed);
    } else if token_is(sql, b"ROLLBACK") {
        counters.rollbacks.fetch_add(1, Ordering::Relaxed);
    } else if token_is(sql, b"PRAGMA") {
        counters.pragmas.fetch_add(1, Ordering::Relaxed);
    } else {
        let statement = statement.cast::<rusqlite::ffi::sqlite3_stmt>();
        let readonly = unsafe { rusqlite::ffi::sqlite3_stmt_readonly(statement) } != 0;
        if readonly {
            counters.selects.fetch_add(1, Ordering::Relaxed);
            if sql
                .windows(4)
                .any(|value| value.eq_ignore_ascii_case(b"SUM("))
            {
                counters.aggregate_selects.fetch_add(1, Ordering::Relaxed);
            }
        } else {
            counters.dml_statements.fetch_add(1, Ordering::Relaxed);
            let database = unsafe { rusqlite::ffi::sqlite3_db_handle(statement) };
            if unsafe { rusqlite::ffi::sqlite3_get_autocommit(database) } != 0 {
                counters
                    .autocommit_dml_statements
                    .fetch_add(1, Ordering::Relaxed);
            }
        }
    }
    0
}

unsafe extern "C" fn trace_row(
    context: *mut c_void,
    operation: c_int,
    _database: *const c_char,
    table: *const c_char,
    _row: rusqlite::ffi::sqlite3_int64,
) {
    let observer = unsafe { &*context.cast::<SqlObserver>() };
    if !observer.enabled.load(Ordering::Acquire) || table.is_null() {
        return;
    }
    let table = unsafe { CStr::from_ptr(table) }.to_bytes();
    let counters = observer.role();
    let target = match (table, operation) {
        (b"artifacts", rusqlite::ffi::SQLITE_INSERT) => &counters.artifact_inserts,
        (b"artifacts", rusqlite::ffi::SQLITE_UPDATE) => &counters.artifact_updates,
        (b"artifacts", rusqlite::ffi::SQLITE_DELETE) => &counters.artifact_deletes,
        (b"pins", rusqlite::ffi::SQLITE_INSERT) => &counters.pin_inserts,
        (b"pins", rusqlite::ffi::SQLITE_UPDATE) => &counters.pin_updates,
        (b"pins", rusqlite::ffi::SQLITE_DELETE) => &counters.pin_deletes,
        (b"aliases", rusqlite::ffi::SQLITE_INSERT) => &counters.alias_inserts,
        (b"aliases", rusqlite::ffi::SQLITE_UPDATE) => &counters.alias_updates,
        (b"aliases", rusqlite::ffi::SQLITE_DELETE) => &counters.alias_deletes,
        _ => &counters.other_row_events,
    };
    target.fetch_add(1, Ordering::Relaxed);
}

fn page(index: usize) -> Page {
    Page {
        url: format!("https://fixture.invalid/cache/{index:08}.png"),
        ..Page::default()
    }
}

fn raw_path(directory: &Path, page: &Page) -> PathBuf {
    let key = CacheManager::page_key(page);
    directory
        .join("cache/shared-v1/raw")
        .join(&key[..2])
        .join(format!("{key}.raw"))
}

fn alias(index: usize) -> String {
    format!("cache-benchmark:{index:08}")
}

fn fixture_bytes() -> Result<Vec<u8>> {
    let mut output = Cursor::new(Vec::new());
    image::GrayImage::from_fn(FIXTURE_WIDTH, FIXTURE_HEIGHT, |x, y| {
        image::Luma([((x * 29 + y * 17) % 256) as u8])
    })
    .write_to(&mut output, image::ImageFormat::Png)?;
    imaging::validate_page(output.get_ref())?;
    Ok(output.into_inner())
}

fn replacement_bytes() -> Result<Vec<u8>> {
    let mut output = Cursor::new(Vec::new());
    image::GrayImage::from_fn(FIXTURE_WIDTH, FIXTURE_HEIGHT, |x, y| {
        image::Luma([((x * 29 + y * 17 + 53) % 256) as u8])
    })
    .write_to(&mut output, image::ImageFormat::Png)?;
    imaging::validate_page(output.get_ref())?;
    Ok(output.into_inner())
}

fn metadata_fingerprint(metadata: &fs::Metadata) -> String {
    let modified = metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(UNIX_EPOCH).ok())
        .map(|value| value.as_nanos())
        .unwrap_or(0);
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        format!(
            "{}:{modified}:{}:{}:{}:{}",
            metadata.len(),
            metadata.dev(),
            metadata.ino(),
            metadata.ctime(),
            metadata.ctime_nsec()
        )
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        format!(
            "{}:{modified}:{}:{}",
            metadata.len(),
            metadata.creation_time(),
            metadata.file_attributes()
        )
    }
    #[cfg(not(any(unix, windows)))]
    {
        format!("{}:{modified}", metadata.len())
    }
}

fn cache_config() -> CacheConfig {
    CacheConfig {
        max_unpinned_bytes: u64::MAX,
        min_free_bytes: 0,
    }
}

fn seed(arguments: &Arguments) -> Result<Value> {
    ensure!(
        !arguments.data_dir.exists(),
        "Fixture directories must be fresh"
    );
    {
        let cache = CacheManager::open_with_config(&arguments.data_dir, cache_config())?;
        ensure!(
            cache.stats().entries == 0,
            "The new fixture cache is not empty"
        );
    }
    let bytes = fixture_bytes()?;
    let checksum = format!("{:x}", Sha256::digest(&bytes));
    let root = arguments.data_dir.canonicalize()?.join("cache/shared-v1");
    let mut connection = Connection::open(root.join("index.sqlite3"))?;
    connection.execute_batch("PRAGMA foreign_keys=ON; PRAGMA synchronous=FULL;")?;
    let transaction = connection.transaction()?;
    for index in 0..arguments.entries {
        let page = page(index);
        let key = CacheManager::page_key(&page);
        let path = raw_path(&arguments.data_dir, &page);
        fs::create_dir_all(path.parent().unwrap())?;
        fs::write(&path, &bytes)?;
        let fingerprint = metadata_fingerprint(&fs::metadata(&path)?);
        let id = format!("raw:{key}");
        transaction.execute(
            "INSERT INTO artifacts(id,kind,cache_key,size,checksum,fingerprint,last_access,valid) VALUES(?1,'raw',?2,?3,?4,?5,?6,1)",
            params![id, key, bytes.len() as u64, checksum, fingerprint, index as u64 + 1],
        )?;
        if arguments.fixture_kind == FixtureKind::Pinned || index == 0 {
            let owner = if arguments.fixture_kind == FixtureKind::Pinned {
                PIN_OWNER
            } else {
                FOREGROUND_OWNER
            };
            transaction.execute(
                "INSERT INTO pins(raw_key,owner,process_id) VALUES(?1,?2,0)",
                params![key, owner],
            )?;
        }
        if arguments.fixture_kind == FixtureKind::Clearable {
            let alias_key = format!("{:x}", Sha256::digest(alias(index).as_bytes()));
            transaction.execute(
                "INSERT INTO aliases(alias_key,artifact_id) VALUES(?1,?2)",
                params![alias_key, id],
            )?;
        }
    }
    transaction.commit()?;
    let integrity: String =
        connection.pragma_query_value(None, "integrity_check", |row| row.get(0))?;
    ensure!(
        integrity == "ok",
        "The seeded fixture index is invalid: {integrity}"
    );
    #[cfg(target_os = "linux")]
    {
        use std::os::fd::AsRawFd;
        let directory = fs::File::open(&root)?;
        ensure!(
            unsafe { libc::syncfs(directory.as_raw_fd()) } == 0,
            "Synchronize the fixture filesystem: {}",
            std::io::Error::last_os_error()
        );
    }
    Ok(json!({
        "fixture_entries": arguments.entries,
        "fixture_kind": format!("{:?}", arguments.fixture_kind),
        "width": FIXTURE_WIDTH, "height": FIXTURE_HEIGHT,
        "bytes_per_entry": bytes.len(), "image_sha256": checksum,
        "total_image_bytes": bytes.len() as u64 * arguments.entries as u64,
        "checks": {"fresh_fixture_integrity": true},
        "seed_method": "Production schema bootstrap, close all managers, then one SQLite transaction and independent regular files; seed process is never measured as workload",
        "fixture_durability": "Linux syncfs after the single seed transaction, outside the measured process",
    }))
}

fn verify_file(path: &Path, expected: &[u8]) -> Result<()> {
    ensure!(
        fs::read(path)? == expected,
        "A surviving cache image changed: {}",
        path.display()
    );
    imaging::validate_file(path)
}

fn index_counts(directory: &Path) -> Result<Value> {
    let connection = Connection::open(directory.join("cache/shared-v1/index.sqlite3"))?;
    let integrity: String =
        connection.pragma_query_value(None, "integrity_check", |row| row.get(0))?;
    ensure!(
        integrity == "ok",
        "The final cache index is invalid: {integrity}"
    );
    let artifacts: u64 =
        connection.query_row("SELECT count(*) FROM artifacts", [], |row| row.get(0))?;
    let pins: u64 = connection.query_row("SELECT count(*) FROM pins", [], |row| row.get(0))?;
    let aliases: u64 =
        connection.query_row("SELECT count(*) FROM aliases", [], |row| row.get(0))?;
    let dangling_aliases: u64 = connection.query_row("SELECT count(*) FROM aliases LEFT JOIN artifacts ON artifacts.id=aliases.artifact_id WHERE artifacts.id IS NULL", [], |row| row.get(0))?;
    ensure!(
        dangling_aliases == 0,
        "A cleared cache alias points at a missing artifact"
    );
    Ok(
        json!({"artifacts": artifacts, "pins": pins, "aliases": aliases, "dangling_aliases": dangling_aliases}),
    )
}

fn pin_owners(directory: &Path, page: &Page) -> Result<Vec<String>> {
    let connection = Connection::open(directory.join("cache/shared-v1/index.sqlite3"))?;
    let mut statement =
        connection.prepare("SELECT owner FROM pins WHERE raw_key=?1 ORDER BY owner")?;
    Ok(statement
        .query_map([CacheManager::page_key(page)], |row| {
            row.get::<_, String>(0)
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?)
}

fn observer_qualification(arguments: &Arguments) -> Result<Value> {
    ensure!(
        !arguments.data_dir.exists(),
        "Observer qualification requires a fresh directory"
    );
    {
        let _cache = CacheManager::open_with_config(&arguments.data_dir, cache_config())?;
    }
    let connection = Connection::open(arguments.data_dir.join("cache/shared-v1/index.sqlite3"))?;
    connection.execute_batch("PRAGMA foreign_keys=ON;")?;
    SQL_ROLE.with(|role| role.set(ROLE_OPERATION));
    let observer = SqlObserver::global();
    observer.enabled.store(true, Ordering::Release);
    let key = "0".repeat(64);
    connection.execute(
        "INSERT INTO pins(raw_key,owner,process_id) VALUES(?1,'download:observer',0)",
        [&key],
    )?;
    connection.execute("UPDATE pins SET process_id=0 WHERE raw_key=?1", [&key])?;
    connection.execute_batch("BEGIN IMMEDIATE;")?;
    connection.execute("DELETE FROM pins WHERE raw_key=?1", [&key])?;
    connection.execute_batch("COMMIT;")?;
    observer.enabled.store(false, Ordering::Release);
    for index in 0..2 {
        let key = format!("{index:064x}");
        let id = format!("raw:{key}");
        connection.execute("INSERT INTO artifacts(id,kind,cache_key,size,checksum,fingerprint,last_access,valid) VALUES(?1,'raw',?2,0,?2,'',0,1)", params![id,key])?;
        connection.execute(
            "INSERT INTO aliases(alias_key,artifact_id) VALUES(?1,?2)",
            params![key, id],
        )?;
    }
    observer.enabled.store(true, Ordering::Release);
    {
        let mut delete = connection.prepare("DELETE FROM artifacts WHERE id=?1")?;
        for index in 0..2 {
            delete.execute([format!("raw:{index:064x}")])?;
        }
    }
    observer.enabled.store(false, Ordering::Release);
    let counters = &observer.roles[ROLE_OPERATION];
    ensure!(
        counters.dml_statements.load(Ordering::Relaxed) == 5
            && counters.autocommit_dml_statements.load(Ordering::Relaxed) == 4,
        "The observer did not count each actual DML execution or repeated prepared execution"
    );
    ensure!(
        counters.pin_inserts.load(Ordering::Relaxed) == 1
            && counters.pin_updates.load(Ordering::Relaxed) == 1
            && counters.pin_deletes.load(Ordering::Relaxed) == 1,
        "The observer did not receive the real pin row events"
    );
    ensure!(
        counters.begins.load(Ordering::Relaxed) == 1
            && counters.commits.load(Ordering::Relaxed) == 1,
        "The observer missed explicit transaction boundaries"
    );
    ensure!(
        counters.artifact_deletes.load(Ordering::Relaxed) == 2
            && counters.alias_deletes.load(Ordering::Relaxed) == 2,
        "The observer missed parent or cascade row mutations"
    );
    ensure!(
        observer
            .statement_tracking_overflows
            .load(Ordering::Relaxed)
            == 0,
        "The statement tracker overflowed"
    );
    Ok(
        json!({"sql": observer.snapshot(), "checks": {"actual_dml_executions_counted": true, "actual_pin_row_events_counted": true, "explicit_transaction_boundaries_counted": true, "repeated_prepared_execution_and_cascade_not_double_counted": true}}),
    )
}

fn populate_public(arguments: &Arguments) -> Result<Value> {
    let cache = CacheManager::open_with_config(
        &arguments.data_dir,
        CacheConfig {
            max_unpinned_bytes: u64::MAX,
            min_free_bytes: 64 * 1024 * 1024,
        },
    )?;
    let mut random = 0x4ca3_d72b_u32;
    let image = image::GrayImage::from_fn(256, 256, |_, _| {
        random ^= random << 13;
        random ^= random >> 17;
        random ^= random << 5;
        image::Luma([(random >> 8) as u8])
    });
    let mut output = Cursor::new(Vec::new());
    image.write_to(&mut output, image::ImageFormat::Png)?;
    let bytes = output.into_inner();
    imaging::validate_page(&bytes)?;
    let before = cache.stats();
    let mut first = None;
    let mut last = None;
    for index in 0..arguments.entries {
        let page = Page {
            url: format!("https://fixture.invalid/public-ui-cache/{index:08}.png"),
            ..Page::default()
        };
        let mut write = cache.begin_raw(&page, None, bytes.len() as u64)?;
        fs::write(write.temp_path(), &bytes)?;
        write.ensure_capacity(bytes.len() as u64)?;
        cache.validate_path(write.temp_path())?;
        let path = write.commit()?;
        cache.set_alias(&format!("public-ui-cache:{index:08}"), &path)?;
        if first.is_none() {
            first = Some(path.clone());
        }
        last = Some(path);
    }
    verify_file(
        first
            .as_deref()
            .context("The public fixture has no first entry")?,
        &bytes,
    )?;
    verify_file(
        last.as_deref()
            .context("The public fixture has no last entry")?,
        &bytes,
    )?;
    Ok(json!({
        "public_api_only": true,
        "data_dir": arguments.data_dir,
        "namespace": "https://fixture.invalid/public-ui-cache/",
        "entries_requested": arguments.entries,
        "width": 256, "height": 256,
        "bytes_per_entry": bytes.len(),
        "total_fixture_image_bytes": bytes.len() as u64 * arguments.entries as u64,
        "image_sha256": format!("{:x}", Sha256::digest(&bytes)),
        "before": before, "cache": cache.stats(),
        "checks": {"public_cache_api_used": true, "first_and_last_images_valid": true},
        "scope": "UI fixture preparation only; different from the fixed 8x8 cache-scaling workload and excluded from performance timing",
    }))
}

fn percentile(values: &[f64], fraction: f64) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    let mut values = values.to_vec();
    values.sort_by(f64::total_cmp);
    Some(
        values[((values.len() as f64 * fraction).ceil() as usize)
            .saturating_sub(1)
            .min(values.len() - 1)],
    )
}

fn distribution(values: &[f64]) -> Value {
    json!({"samples": values.len(), "p50_ms": percentile(values, 0.50), "p95_ms": percentile(values, 0.95), "max_ms": percentile(values, 1.0)})
}

fn pin_noop(arguments: &Arguments) -> Result<Value> {
    let cache = CacheManager::open_with_config(&arguments.data_dir, cache_config())?;
    ensure!(
        cache.stats().entries == arguments.entries as u64,
        "The pinned fixture entry count is wrong"
    );
    let keys = (0..arguments.pin_operations.min(arguments.entries))
        .map(page)
        .collect::<Vec<_>>();
    for page in &keys {
        cache.pin_raw(page, PIN_OWNER)?;
    }
    let observer = SqlObserver::global();
    SQL_ROLE.with(|role| role.set(ROLE_OPERATION));
    observer.enabled.store(true, Ordering::Release);
    let mut latencies = Vec::with_capacity(arguments.pin_operations);
    let started = Instant::now();
    for index in 0..arguments.pin_operations {
        let call = Instant::now();
        cache.pin_raw(&keys[index % keys.len()], PIN_OWNER)?;
        latencies.push(call.elapsed().as_secs_f64() * 1000.0);
    }
    let wall = started.elapsed().as_secs_f64();
    observer.enabled.store(false, Ordering::Release);
    ensure!(
        observer.connections.load(Ordering::Relaxed) > 0,
        "The SQL observer did not attach to the production cache connection"
    );
    let sql = observer.snapshot();
    let counts = index_counts(&arguments.data_dir)?;
    ensure!(
        counts["artifacts"] == arguments.entries as u64
            && counts["pins"] == arguments.entries as u64,
        "Repeated same-owner pins changed the fixture membership"
    );
    verify_file(&raw_path(&arguments.data_dir, &page(0)), &fixture_bytes()?)?;
    Ok(json!({
        "cache_entries": arguments.entries, "pin_operations": arguments.pin_operations,
        "warm_pin_keys": keys.len(), "operation_wall_seconds": wall,
        "pin_latency": distribution(&latencies), "pin_latencies_ms": latencies,
        "sql": sql, "index": counts, "cache": cache.stats(),
        "checks": {"same_owner_pin_preserves_membership": true, "sql_observer_attached": true, "surviving_file_valid": true},
    }))
}

#[derive(Clone, Debug, Serialize)]
struct LookupSample {
    start_ns: u64,
    end_ns: u64,
    hit: bool,
}

fn elapsed_ns(start: Instant) -> u64 {
    start.elapsed().as_nanos().min(u128::from(u64::MAX)) as u64
}

fn clear_foreground(arguments: &Arguments) -> Result<Value> {
    let cache = CacheManager::open_with_config(&arguments.data_dir, cache_config())?;
    ensure!(
        cache.stats().entries == arguments.entries as u64,
        "The clear fixture entry count is wrong"
    );
    let foreground = page(0);
    cache.pin_raw(&foreground, FOREGROUND_OWNER)?;
    let leased = page(1);
    let lease = cache.lease_path(&raw_path(&arguments.data_dir, &leased))?;
    let expected_foreground = cache
        .lookup_raw(&foreground)?
        .context("The foreground page is not cached")?;
    verify_file(&expected_foreground, &fixture_bytes()?)?;
    let ready = Arc::new(Barrier::new(2));
    let finished = Arc::new(AtomicBool::new(false));
    let origin = Instant::now();
    let observer = SqlObserver::global();
    let _ = observer.origin.set(origin);
    let foreground_thread = {
        let (cache, ready, finished, page) = (
            cache.clone(),
            ready.clone(),
            finished.clone(),
            foreground.clone(),
        );
        let interval = Duration::from_micros(arguments.foreground_interval_us);
        thread::spawn(move || -> Result<Vec<LookupSample>> {
            SQL_ROLE.with(|role| role.set(ROLE_FOREGROUND));
            ready.wait();
            while !observer.work_activity.load(Ordering::Acquire)
                && !finished.load(Ordering::Acquire)
            {
                thread::yield_now();
            }
            let mut samples = Vec::with_capacity(4096);
            while !finished.load(Ordering::Acquire) {
                let start_ns = elapsed_ns(origin);
                let hit = cache.lookup_raw(&page)?.is_some();
                let end_ns = elapsed_ns(origin);
                samples.push(LookupSample {
                    start_ns,
                    end_ns,
                    hit,
                });
                if !interval.is_zero() {
                    thread::sleep(interval);
                }
            }
            Ok(samples)
        })
    };
    SQL_ROLE.with(|role| role.set(ROLE_OPERATION));
    ready.wait();
    observer.enabled.store(true, Ordering::Release);
    let clear_start_ns = elapsed_ns(origin);
    let result = cache.clear_unpinned();
    let clear_end_ns = elapsed_ns(origin);
    finished.store(true, Ordering::Release);
    let samples = foreground_thread
        .join()
        .map_err(|_| anyhow::anyhow!("The foreground lookup thread panicked"))??;
    observer.enabled.store(false, Ordering::Release);
    result?;
    ensure!(
        !samples.is_empty(),
        "No foreground lookup overlapped the clear workload"
    );
    ensure!(
        samples.iter().all(|sample| sample.hit),
        "Clear removed the foreground cache hit"
    );
    let clear_first_sql_ns = observer.work_activity_ns.load(Ordering::Acquire);
    let overlapping = samples
        .iter()
        .filter(|sample| sample.start_ns <= clear_end_ns && sample.end_ns >= clear_first_sql_ns)
        .map(|sample| (sample.end_ns - sample.start_ns) as f64 / 1_000_000.0)
        .collect::<Vec<_>>();
    ensure!(
        !overlapping.is_empty(),
        "No foreground lookup interval intersected clear"
    );
    let counts = index_counts(&arguments.data_dir)?;
    ensure!(
        counts["artifacts"] == 2 && counts["aliases"] == 2,
        "Clear did not remove exactly the unprotected fixture entries and aliases"
    );
    let bytes = fixture_bytes()?;
    for index in 0..arguments.entries {
        let path = raw_path(&arguments.data_dir, &page(index));
        if index < 2 {
            verify_file(&path, &bytes)?;
        } else {
            ensure!(!path.exists(), "An unprotected artifact survived clear");
        }
    }
    ensure!(
        cache.lookup_alias(&alias(0))?.is_some() && cache.lookup_alias(&alias(1))?.is_some(),
        "A protected cache alias was lost"
    );
    let sql = observer.snapshot();
    let stats = cache.stats();
    drop(lease);
    Ok(json!({
        "cache_entries": arguments.entries,
        "operation_wall_seconds": (clear_end_ns - clear_start_ns) as f64 / 1_000_000_000.0,
        "clear_start_ns": clear_start_ns, "clear_end_ns": clear_end_ns,
        "clear_first_sql_ns": clear_first_sql_ns,
        "foreground_interval_us": arguments.foreground_interval_us,
        "foreground_overlapping": distribution(&overlapping),
        "foreground_samples": samples, "sql": sql, "index": counts, "cache": stats,
        "checks": {"foreground_remained_a_hit": true, "pinned_and_leased_files_survive": true, "unprotected_entries_and_aliases_removed": true, "index_integrity": true},
    }))
}

fn wait_for_clear(observer: &SqlObserver) -> Result<()> {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !observer.work_activity.load(Ordering::Acquire) {
        ensure!(
            Instant::now() < deadline,
            "Clear did not reach its SQLite work"
        );
        thread::yield_now();
    }
    Ok(())
}

fn race_safety(arguments: &Arguments) -> Result<Value> {
    let cache = CacheManager::open_with_config(&arguments.data_dir, cache_config())?;
    let bytes = fixture_bytes()?;
    let changed_bytes = replacement_bytes()?;
    ensure!(
        changed_bytes != bytes,
        "The replacement fixture must change its valid image bytes"
    );
    let foreground = page(0);
    cache.pin_raw(&foreground, FOREGROUND_OWNER)?;
    let lease = cache.lease_path(&raw_path(&arguments.data_dir, &page(1)))?;
    let race_lease = cache.lease_path(&raw_path(&arguments.data_dir, &page(2)))?;
    let replacement_page = page(3);
    let replacement = cache.begin_raw(
        &replacement_page,
        Some("download:race-replacement"),
        changed_bytes.len() as u64,
    )?;
    fs::write(replacement.temp_path(), &changed_bytes)?;
    cache.validate_path(replacement.temp_path())?;
    let new_page = page(arguments.entries);
    let new_write = cache.begin_raw(&new_page, Some("download:race-new"), bytes.len() as u64)?;
    fs::write(new_write.temp_path(), &bytes)?;
    cache.validate_path(new_write.temp_path())?;
    let unprotected_page = page(arguments.entries + 1);
    let unprotected = cache.begin_raw(&unprotected_page, None, bytes.len() as u64)?;
    fs::write(unprotected.temp_path(), &bytes)?;
    cache.validate_path(unprotected.temp_path())?;
    let observer = SqlObserver::global();
    let origin = Instant::now();
    let _ = observer.origin.set(origin);
    let barrier = Arc::new(Barrier::new(4));
    let pin_thread = {
        let (cache, barrier) = (cache.clone(), barrier.clone());
        thread::spawn(move || -> Result<Value> {
            SQL_ROLE.with(|role| role.set(ROLE_RACE));
            barrier.wait();
            wait_for_clear(observer)?;
            let start_ns = elapsed_ns(origin);
            cache.pin_raw(&page(2), "download:race-pin")?;
            let end_ns = elapsed_ns(origin);
            drop(race_lease);
            Ok(
                json!({"name": "pin_after_lease", "start_ns": start_ns, "end_ns": end_ns, "lease_released_ns": elapsed_ns(origin)}),
            )
        })
    };
    let commit_thread = {
        let barrier = barrier.clone();
        thread::spawn(move || -> Result<Value> {
            SQL_ROLE.with(|role| role.set(ROLE_RACE));
            barrier.wait();
            wait_for_clear(observer)?;
            let start_ns = elapsed_ns(origin);
            let replacement_path = replacement.commit()?;
            let new_path = new_write.commit()?;
            Ok(
                json!({"name": "protected_replacement_and_new_commit", "start_ns": start_ns, "end_ns": elapsed_ns(origin), "paths": [replacement_path, new_path]}),
            )
        })
    };
    let unprotected_thread = {
        let barrier = barrier.clone();
        thread::spawn(move || -> Result<Value> {
            SQL_ROLE.with(|role| role.set(ROLE_RACE));
            barrier.wait();
            wait_for_clear(observer)?;
            let start_ns = elapsed_ns(origin);
            let path = unprotected.commit()?;
            Ok(
                json!({"name": "unprotected_new_commit", "start_ns": start_ns, "end_ns": elapsed_ns(origin), "path": path}),
            )
        })
    };
    SQL_ROLE.with(|role| role.set(ROLE_OPERATION));
    barrier.wait();
    observer.enabled.store(true, Ordering::Release);
    let clear_start_ns = elapsed_ns(origin);
    let clear_result = cache.clear_unpinned();
    let clear_end_ns = elapsed_ns(origin);
    let races = [
        pin_thread.join(),
        commit_thread.join(),
        unprotected_thread.join(),
    ]
    .into_iter()
    .map(|result| result.map_err(|_| anyhow::anyhow!("A cache race thread panicked"))?)
    .collect::<Result<Vec<_>>>()?;
    observer.enabled.store(false, Ordering::Release);
    clear_result?;
    for index in [0, 1, 2, arguments.entries] {
        let path = cache
            .lookup_raw(&page(index))?
            .context("Clear removed a protected race artifact")?;
        verify_file(&path, &bytes)?;
    }
    verify_file(
        &cache
            .lookup_raw(&replacement_page)?
            .context("Clear removed the replacement generation")?,
        &changed_bytes,
    )?;
    ensure!(
        pin_owners(&arguments.data_dir, &replacement_page)?
            .contains(&"download:race-replacement".to_owned()),
        "The replacement's durable pin was not committed"
    );
    ensure!(
        pin_owners(&arguments.data_dir, &new_page)?.contains(&"download:race-new".to_owned()),
        "The new artifact's durable pin was not committed"
    );
    let unprotected_path = raw_path(&arguments.data_dir, &unprotected_page);
    let unprotected_key = format!("raw:{}", CacheManager::page_key(&unprotected_page));
    let unprotected_row: bool =
        Connection::open(arguments.data_dir.join("cache/shared-v1/index.sqlite3"))?.query_row(
            "SELECT EXISTS(SELECT 1 FROM artifacts WHERE id=?1)",
            [unprotected_key],
            |row| row.get(0),
        )?;
    ensure!(
        unprotected_row == unprotected_path.is_file(),
        "The unprotected commit left a mismatched file and index row"
    );
    if unprotected_row {
        verify_file(&unprotected_path, &bytes)?;
        ensure!(
            cache.lookup_raw(&unprotected_page)?.is_some(),
            "The committed unprotected page is not readable"
        );
    }
    cache.unpin_owner("download:race-pin")?;
    cache.pin_raw(&page(2), "download:race-pin")?;
    ensure!(
        pin_owners(&arguments.data_dir, &page(2))? == ["download:race-pin"],
        "Same-owner repin was incorrectly skipped after unpin"
    );
    cache.pin_raw(&page(2), "download:race-other-owner")?;
    ensure!(
        pin_owners(&arguments.data_dir, &page(2))?
            == ["download:race-other-owner", "download:race-pin"],
        "A different pin owner was incorrectly skipped"
    );
    cache.unpin_owner("download:race-pin")?;
    let owners = pin_owners(&arguments.data_dir, &page(2))?;
    ensure!(
        owners == ["download:race-other-owner"],
        "Unpin/repin or a different owner was incorrectly treated as a stale no-op"
    );
    cache.clear_unpinned()?;
    for index in [0, 1, 2, arguments.entries] {
        verify_file(
            &cache
                .lookup_raw(&page(index))?
                .context("A second clear removed a protected race artifact")?,
            &bytes,
        )?;
    }
    verify_file(
        &cache
            .lookup_raw(&replacement_page)?
            .context("A second clear removed the pinned replacement generation")?,
        &changed_bytes,
    )?;
    let clear_first_sql_ns = observer.work_activity_ns.load(Ordering::Acquire);
    let coverage = races.iter().map(|race| {
        let start = race["start_ns"].as_u64().unwrap_or(0);
        let end = race["end_ns"].as_u64().unwrap_or(0);
        json!({"name": race["name"], "attempt_overlapped_clear": start < clear_end_ns && end > clear_first_sql_ns, "api_completed_inside_clear": end <= clear_end_ns})
    }).collect::<Vec<_>>();
    let counts = index_counts(&arguments.data_dir)?;
    let stats = cache.stats();
    drop(lease);
    Ok(json!({
        "cache_entries": arguments.entries, "clear_start_ns": clear_start_ns, "clear_end_ns": clear_end_ns, "clear_first_sql_ns": clear_first_sql_ns,
        "races": races, "race_coverage": coverage, "sql": observer.snapshot(), "index": counts, "cache": stats,
        "checks": {"pin_and_lease_protection": true, "protected_new_and_replacement_commits_valid": true, "protected_commit_pins_survive_second_clear": true, "unprotected_commit_valid_or_fully_reclaimed": true, "unpin_repin_and_different_owner_persisted": true, "index_integrity": true},
    }))
}

fn main() -> Result<()> {
    let arguments = Arguments::parse();
    ensure!(
        arguments.entries >= 8 && arguments.pin_operations > 0,
        "Use at least eight entries and one pin operation"
    );
    let _registration = ObserverRegistration::install()?;
    let started = Instant::now();
    let mut result = match arguments.scenario {
        Scenario::ObserverQualification => observer_qualification(&arguments),
        Scenario::PopulatePublic => populate_public(&arguments),
        Scenario::Seed => seed(&arguments),
        Scenario::PinNoop => pin_noop(&arguments),
        Scenario::ClearForeground => clear_foreground(&arguments),
        Scenario::RaceSafety => race_safety(&arguments),
    }?;
    result["scenario"] = json!(format!("{:?}", arguments.scenario));
    result["scenario_wall_seconds"] = json!(started.elapsed().as_secs_f64());
    result["build_profile"] = json!(if cfg!(debug_assertions) {
        "dev"
    } else {
        "release"
    });
    result["build_features"] = json!(
        [
            ("desktop", cfg!(feature = "desktop")),
            ("kindle", cfg!(feature = "kindle")),
        ]
        .into_iter()
        .filter_map(|(name, enabled)| enabled.then_some(name))
        .collect::<Vec<_>>()
    );
    result["fixture_geometry"] = if matches!(arguments.scenario, Scenario::PopulatePublic) {
        json!([256, 256])
    } else {
        json!([FIXTURE_WIDTH, FIXTURE_HEIGHT])
    };
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}
