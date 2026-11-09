//! Isolated native sleep fixture. Creation uses only public Store APIs.
//! Run on the designated remote worker or isolated firmware guest, never locally.

use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

use aidoku_kindle::{
    domain::{Book, Chapter, DownloadJob, Page, Preferences, Progress, SourceConfig, compound_id},
    store::Store,
};
use anyhow::{Context, Result, ensure};
use clap::Parser;
use rusqlite::{Connection, OpenFlags};
use serde_json::{Value, json};
use url::Url;

const SOURCE: &str = "fixture-komga";
const IMAGE: &[u8] = include_bytes!("../assets/demo/page-00.png");

#[derive(Debug, Parser)]
#[command(about = "Create or read an isolated native sleep/HTTP fixture")]
struct Arguments {
    #[arg(long)]
    data_dir: PathBuf,
    #[arg(long)]
    base_url: Option<String>,
    #[arg(long)]
    reader_image: Option<PathBuf>,
    #[arg(long, default_value_t = 8)]
    pages: u32,
    /// Read existing SQLite state without opening Store or recovering jobs.
    #[arg(long)]
    inspect: bool,
}

fn main() -> Result<()> {
    let arguments = Arguments::parse();
    let result = if arguments.inspect {
        inspect(&arguments.data_dir)?
    } else {
        create(arguments)?
    };
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}

fn create(arguments: Arguments) -> Result<Value> {
    ensure!(
        (1..=1_000).contains(&arguments.pages),
        "The fixture page count must be between 1 and 1000"
    );
    let mut base = Url::parse(
        arguments
            .base_url
            .as_deref()
            .context("Creation requires --base-url")?,
    )
    .context("Parse the fixture HTTP base URL")?;
    ensure!(
        matches!(base.scheme(), "http" | "https") && base.host_str().is_some(),
        "The fixture base URL must use HTTP or HTTPS"
    );
    ensure!(
        base.username().is_empty()
            && base.password().is_none()
            && base.query().is_none()
            && base.fragment().is_none(),
        "Pass fixture credentials in headers, never in the base URL"
    );
    if !base.path().ends_with('/') {
        base.set_path(&format!("{}/", base.path()));
    }
    let database = arguments.data_dir.join("library.sqlite3");
    ensure!(
        !database.exists(),
        "Fixture creation requires a fresh data directory; an existing database will not be overwritten"
    );
    for suffix in ["-wal", "-shm", "-journal"] {
        ensure!(
            !arguments
                .data_dir
                .join(format!("library.sqlite3{suffix}"))
                .exists(),
            "Fixture creation cannot reuse an existing SQLite sidecar"
        );
    }
    ensure!(
        !arguments.data_dir.join("sleep-fixture.json").exists(),
        "Fixture creation cannot replace an existing fixture marker"
    );
    fs::create_dir_all(&arguments.data_dir).context("Create the isolated fixture directory")?;
    let data_dir = fs::canonicalize(&arguments.data_dir)?;
    let image = match arguments.reader_image {
        Some(path) => {
            let path = fs::canonicalize(path).context("Resolve the local reader image")?;
            aidoku_kindle::imaging::validate_file(&path)
                .context("Validate the local reader image")?;
            path
        }
        None => {
            let path = data_dir.join("sleep-fixture-page.png");
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
                .context("Create the local reader image without overwriting existing files")?;
            file.write_all(IMAGE)?;
            file.sync_all()?;
            path
        }
    };
    let database = data_dir.join("library.sqlite3");
    let store = Store::open(&database)?;
    store.save_source(&SourceConfig {
        id: SOURCE.into(),
        name: "Sleep fixture Komga".into(),
        kind: "komga".into(),
        base_url: base.as_str().trim_end_matches('/').into(),
        token: "fixture-token".into(),
        version: 1,
        languages: vec!["en".into()],
        enabled: true,
        options: BTreeMap::from([
            ("maximumParallelRequests".into(), json!(3)),
            ("request_interval_ms".into(), json!(0)),
        ]),
        ..SourceConfig::default()
    })?;
    let reader = Book {
        id: compound_id(SOURCE, "reader"),
        source_id: SOURCE.into(),
        remote_id: "reader".into(),
        title: "Sleep reader fixture".into(),
        author: "Fixture".into(),
        status: "ongoing".into(),
        cover: image.to_string_lossy().into_owned(),
        chapter_count: 2,
        unread: 2,
        category: "reading".into(),
        in_library: true,
        updated_at: 1,
        ..Book::default()
    };
    let bulk = Book {
        id: compound_id(SOURCE, "bulk"),
        source_id: SOURCE.into(),
        remote_id: "bulk".into(),
        title: "Background download fixture".into(),
        chapter_count: 4,
        unread: 4,
        in_library: false,
        updated_at: 0,
        ..Book::default()
    };
    store.upsert_book(&reader)?;
    store.upsert_book(&bulk)?;
    let current = chapter(&reader, "c00", 0.0, 1, true);
    let next = chapter(&reader, "c05", 1.0, 3, false);
    store.save_chapters(&reader.id, &[current.clone(), next.clone()])?;
    store.save_pages(
        &current.id,
        &[Page {
            local_path: Some(image.clone()),
            ..Page::default()
        }],
    )?;
    let headers = BTreeMap::from([("X-API-Key".into(), "fixture-token".into())]);
    let next_pages = (1..=3)
        .map(|page| {
            Ok(Page {
                url: base.join(&format!("api/v1/books/c05/pages/{page}"))?.into(),
                headers: headers.clone(),
                local_path: None,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    store.save_pages(&next.id, &next_pages)?;
    let bulk_chapters = ["c01", "c02", "c03", "c04"]
        .into_iter()
        .enumerate()
        .map(|(index, id)| chapter(&bulk, id, index as f64 + 1.0, arguments.pages, false))
        .collect::<Vec<_>>();
    store.save_chapters(&bulk.id, &bulk_chapters)?;
    // Page manifests for background work are intentionally absent. The native
    // application must obtain them from the controlled Komga HTTP endpoint.
    for (chapter, state) in bulk_chapters
        .iter()
        .zip(["queued", "queued", "paused", "cancelled"])
    {
        store.save_download(&DownloadJob {
            chapter_id: chapter.id.clone(),
            book_id: bulk.id.clone(),
            state: state.into(),
            completed: 0,
            total: arguments.pages,
            error: String::new(),
        })?;
    }
    store.save_preferences(&Preferences {
        footer_mode: 3,
        reading_direction: "rtl".into(),
        brightness: 12,
        warmth: 8,
        keep_awake: true,
        spread: false,
        ..Preferences::default()
    })?;
    store.save_progress(&Progress {
        book_id: reader.id.clone(),
        chapter_id: current.id.clone(),
        page: 1,
        total: 1,
        updated_at: 1,
        completed: false,
    })?;
    ensure!(
        store.suspended_download_ids()?.is_empty(),
        "A new fixture unexpectedly owns suspended downloads"
    );
    let description = json!({
        "fixture_version": 1, "data_dir": data_dir, "database": database,
        "source_id": SOURCE, "base_url": base.as_str(), "reader_book_id": reader.id,
        "reader_current": {"id":current.id,"remote_id":"c00","page":1,"local_image":image},
        "reader_next": {"id":next.id,"remote_id":"c05","pages":3},
        "bulk_book_id": bulk.id, "bulk_chapters": bulk_chapters,
        "initial_states":{"c01":"queued","c02":"queued","c03":"paused","c04":"cancelled"},
        "background_pages": arguments.pages,
    });
    let mut marker = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(data_dir.join("sleep-fixture.json"))?;
    marker.write_all(serde_json::to_string_pretty(&description)?.as_bytes())?;
    marker.sync_all()?;
    Ok(description)
}

fn chapter(book: &Book, remote_id: &str, number: f64, pages: u32, downloaded: bool) -> Chapter {
    Chapter {
        id: compound_id(SOURCE, remote_id),
        book_id: book.id.clone(),
        source_id: SOURCE.into(),
        remote_id: remote_id.into(),
        number,
        title: format!("Fixture {remote_id}"),
        page_count: pages,
        downloaded,
        ..Chapter::default()
    }
}

fn inspect(data_dir: &Path) -> Result<Value> {
    let database = data_dir.join("library.sqlite3");
    let connection = Connection::open_with_flags(&database, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .context("Open the fixture database read-only")?;
    connection.busy_timeout(std::time::Duration::from_secs(2))?;
    let jobs = {
        let mut statement = connection
            .prepare("SELECT chapter_id, book_id, state, document FROM downloads ORDER BY rowid")?;
        statement.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?, row.get::<_, String>(3)?)))?
            .map(|row| { let (chapter_id, book_id, state, document) = row?; Ok(json!({"chapter_id":chapter_id,"book_id":book_id,"state":state,"document":serde_json::from_str::<Value>(&document)?})) }).collect::<Result<Vec<_>>>()?
    };
    let owned = {
        let mut statement = connection
            .prepare("SELECT chapter_id FROM download_sleep_ownership ORDER BY chapter_id")?;
        statement
            .query_map([], |row| row.get::<_, String>(0))?
            .collect::<rusqlite::Result<Vec<_>>>()?
    };
    let progress = {
        let mut statement = connection.prepare("SELECT book_id,chapter_id,page,total,completed,updated_at FROM progress ORDER BY updated_at DESC")?;
        statement
            .query_map([], |row| {
                Ok(Progress {
                    book_id: row.get(0)?,
                    chapter_id: row.get(1)?,
                    page: row.get(2)?,
                    total: row.get(3)?,
                    completed: row.get(4)?,
                    updated_at: row.get(5)?,
                })
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?
    };
    Ok(json!({"mode":"inspect","database":database,"jobs":jobs,"owned":owned,"progress":progress}))
}
