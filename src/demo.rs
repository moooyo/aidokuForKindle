//! Deterministic, explicitly requested demo data based on the supplied design.
//!
//! The caller must invoke this module only for the separate demo data directory.
//! Provider credentials and the normal application database are never imported.

use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
    sync::OnceLock,
};

use anyhow::{Context, Result, ensure};
use chrono::{DateTime, Duration, NaiveDate};
use serde::Deserialize;

use crate::{
    domain::{Book, Bookmark, Chapter, DownloadJob, Page, Progress, SourceConfig, compound_id},
    sources::{Repository, RepositorySource},
    store::Store,
};

const SOURCE_ID: &str = "mangadex";
const SEED_MARKER: &str = ".demo-v1-seeded";
const LIBRARY_DOWNLOAD_INDICES: [usize; 7] = [0, 3, 4, 8, 11, 14, 17];
const LIBRARY_PROGRESS_PERCENTAGES: [(&str, u32); 3] =
    [("demo-02", 23), ("demo-07", 60), ("demo-21", 80)];
const COVER_ART: [&[u8]; 18] = [
    include_bytes!("../assets/demo/cover-00.png"),
    include_bytes!("../assets/demo/cover-01.png"),
    include_bytes!("../assets/demo/cover-02.png"),
    include_bytes!("../assets/demo/cover-03.png"),
    include_bytes!("../assets/demo/cover-04.png"),
    include_bytes!("../assets/demo/cover-05.png"),
    include_bytes!("../assets/demo/cover-06.png"),
    include_bytes!("../assets/demo/cover-07.png"),
    include_bytes!("../assets/demo/cover-08.png"),
    include_bytes!("../assets/demo/cover-09.png"),
    include_bytes!("../assets/demo/cover-10.png"),
    include_bytes!("../assets/demo/cover-11.png"),
    include_bytes!("../assets/demo/cover-12.png"),
    include_bytes!("../assets/demo/cover-13.png"),
    include_bytes!("../assets/demo/cover-14.png"),
    include_bytes!("../assets/demo/cover-15.png"),
    include_bytes!("../assets/demo/cover-16.png"),
    include_bytes!("../assets/demo/cover-17.png"),
];
const PAGE_ART: [&[u8]; 2] = [
    include_bytes!("../assets/demo/page-00.png"),
    include_bytes!("../assets/demo/page-01.png"),
];
const SEARCH_COVER_ART: [&[u8]; 3] = [
    include_bytes!("../assets/demo/search-cover-00.png"),
    include_bytes!("../assets/demo/search-cover-01.png"),
    include_bytes!("../assets/demo/search-cover-02.png"),
];
const CONTEXT_COVER_ART: [(&str, &[u8]); 23] = [
    (
        "catalog-cover-pw-00.png",
        include_bytes!("../assets/demo/catalog-cover-pw-00.png"),
    ),
    (
        "catalog-cover-pw-01.png",
        include_bytes!("../assets/demo/catalog-cover-pw-01.png"),
    ),
    (
        "catalog-cover-pw-02.png",
        include_bytes!("../assets/demo/catalog-cover-pw-02.png"),
    ),
    (
        "catalog-cover-pw-03.png",
        include_bytes!("../assets/demo/catalog-cover-pw-03.png"),
    ),
    (
        "catalog-cover-pw-04.png",
        include_bytes!("../assets/demo/catalog-cover-pw-04.png"),
    ),
    (
        "catalog-cover-scribe-00.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-00.png"),
    ),
    (
        "catalog-cover-scribe-01.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-01.png"),
    ),
    (
        "catalog-cover-scribe-02.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-02.png"),
    ),
    (
        "catalog-cover-scribe-03.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-03.png"),
    ),
    (
        "catalog-cover-scribe-04.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-04.png"),
    ),
    (
        "catalog-cover-scribe-05.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-05.png"),
    ),
    (
        "catalog-cover-scribe-06.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-06.png"),
    ),
    (
        "catalog-cover-scribe-07.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-07.png"),
    ),
    (
        "catalog-cover-scribe-08.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-08.png"),
    ),
    (
        "catalog-cover-scribe-09.png",
        include_bytes!("../assets/demo/catalog-cover-scribe-09.png"),
    ),
    (
        "history-cover-00.png",
        include_bytes!("../assets/demo/history-cover-00.png"),
    ),
    (
        "history-cover-01.png",
        include_bytes!("../assets/demo/history-cover-01.png"),
    ),
    (
        "history-cover-02.png",
        include_bytes!("../assets/demo/history-cover-02.png"),
    ),
    (
        "history-cover-03.png",
        include_bytes!("../assets/demo/history-cover-03.png"),
    ),
    (
        "history-cover-04.png",
        include_bytes!("../assets/demo/history-cover-04.png"),
    ),
    (
        "library-cover-pw-02.png",
        include_bytes!("../assets/demo/library-cover-pw-02.png"),
    ),
    (
        "library-cover-pw-06.png",
        include_bytes!("../assets/demo/library-cover-pw-06.png"),
    ),
    (
        "library-cover-pw-07.png",
        include_bytes!("../assets/demo/library-cover-pw-07.png"),
    ),
];

#[derive(Debug, Clone)]
pub struct DemoSearchGroup {
    pub id: String,
    pub title: String,
    pub books: Vec<Book>,
    pub error: Option<String>,
}

#[derive(Clone, Default, Deserialize)]
#[serde(default)]
struct DemoBook {
    id: String,
    source_id: String,
    title: String,
    author: String,
    description: String,
    tags: Vec<String>,
    status: String,
    unread: u32,
    category: String,
    cover_index: usize,
    cover_file: String,
    chapter_count: u32,
    in_library: bool,
    updated: String,
    updated_days: u32,
}

#[derive(Deserialize)]
struct DemoHistory {
    book_index: usize,
    chapter_number: u32,
    chapter_title: String,
    page: u32,
    page_count: u32,
    completed: bool,
    read_at: String,
    #[serde(default)]
    cover_file: String,
}

#[derive(Deserialize)]
struct ContinueReading {
    book_index: usize,
    chapter_number: u32,
    page: u32,
    page_count: u32,
}

#[derive(Deserialize)]
struct Fixture {
    fixture_date: String,
    books: Vec<DemoBook>,
    catalog_paperwhite: Vec<DemoBook>,
    catalog_scribe: Vec<DemoBook>,
    catalog_volume_suffix: String,
    history: Vec<DemoHistory>,
    continue_reading: ContinueReading,
    search_query: String,
    search_groups: Vec<SearchFixtureGroup>,
    updates: Vec<DemoUpdate>,
}

#[derive(Deserialize)]
struct DemoUpdate {
    book_index: usize,
    chapter_number: u32,
    title: String,
    date: String,
}

#[derive(Deserialize)]
struct SearchFixtureGroup {
    id: String,
    title: String,
    books: Vec<DemoBook>,
    error: Option<String>,
}

#[derive(Deserialize)]
struct ChapterFixture {
    titles: Vec<String>,
    primary_chapter_title: String,
    sources: Vec<SourceConfig>,
}

fn fixture() -> &'static Fixture {
    static FIXTURE: OnceLock<Fixture> = OnceLock::new();
    FIXTURE.get_or_init(|| {
        serde_json::from_str(include_str!("../assets/demo/catalog.json"))
            .expect("Bundled demo catalog must be valid JSON")
    })
}

fn chapter_fixture() -> &'static ChapterFixture {
    static FIXTURE: OnceLock<ChapterFixture> = OnceLock::new();
    FIXTURE.get_or_init(|| {
        serde_json::from_str(include_str!("../assets/demo/chapters.json"))
            .expect("Bundled demo chapters must be valid JSON")
    })
}

fn fixture_timestamp() -> i64 {
    DateTime::parse_from_rfc3339("2026-11-04T18:20:00+08:00")
        .expect("Bundled demo timestamp must be valid")
        .timestamp()
}

/// Return the scenario date used by demo-only relative dates and update counts.
pub fn fixture_date() -> NaiveDate {
    NaiveDate::parse_from_str(&fixture().fixture_date, "%Y-%m-%d")
        .expect("Bundled demo scenario date must be valid")
}

/// Preserve the source scenario's update-preview order without inventing chapter numbers.
pub fn update_book_ids() -> Vec<String> {
    fixture()
        .updates
        .iter()
        .filter_map(|update| fixture().books.get(update.book_index))
        .map(|book| compound_id(SOURCE_ID, &book.id))
        .collect()
}

fn artwork_directory(data_dir: &Path) -> Result<PathBuf> {
    let data_dir = if data_dir.is_absolute() {
        data_dir.to_path_buf()
    } else {
        std::env::current_dir()
            .context("Resolve the demo data directory")?
            .join(data_dir)
    };
    Ok(data_dir.join("demo-assets"))
}

fn prepare_assets(data_dir: &Path) -> Result<PathBuf> {
    let directory = artwork_directory(data_dir)?;
    fs::create_dir_all(&directory).context("Create the demo artwork directory")?;
    for (index, bytes) in COVER_ART.iter().enumerate() {
        write_asset(&directory.join(format!("cover-{index:02}.png")), bytes)?;
    }
    for (index, bytes) in PAGE_ART.iter().enumerate() {
        write_page_asset(&directory.join(format!("page-{index:02}.png")), bytes)?;
    }
    for (index, bytes) in SEARCH_COVER_ART.iter().enumerate() {
        write_asset(
            &directory.join(format!("search-cover-{index:02}.png")),
            bytes,
        )?;
    }
    for (name, bytes) in CONTEXT_COVER_ART {
        write_asset(&directory.join(name), bytes)?;
    }
    Ok(directory)
}

fn write_page_asset(path: &Path, bytes: &[u8]) -> Result<()> {
    match fs::read(path) {
        Ok(existing) if existing.as_slice() == bytes => return Ok(()),
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error).context("Read the managed demo page"),
    }
    // Refresh only bundled demo pages on an artwork update. The seed marker,
    // database, progress, bookmarks, preferences and imported comics stay intact.
    let staging = path.with_extension("png.refresh");
    fs::write(&staging, bytes).context("Stage the updated demo page")?;
    fs::rename(&staging, path).context("Publish the updated demo page")
}

fn write_asset(path: &Path, bytes: &[u8]) -> Result<()> {
    if !path.is_file()
        || fs::metadata(path)
            .context("Read demo artwork metadata")?
            .len()
            == 0
    {
        fs::write(path, bytes).with_context(|| format!("Write demo artwork {}", path.display()))?;
    }
    Ok(())
}

fn book_from_row(
    row: &DemoBook,
    remote_id: String,
    artwork: &Path,
    in_library: bool,
    ordinal: usize,
) -> Book {
    let source_id = if row.source_id.is_empty() {
        SOURCE_ID
    } else {
        &row.source_id
    };
    Book {
        id: compound_id(source_id, &remote_id),
        source_id: source_id.into(),
        remote_id,
        title: row.title.clone(),
        author: row.author.clone(),
        description: row.description.clone(),
        tags: row.tags.clone(),
        status: row.status.clone(),
        cover: cover_path(row, artwork)
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_default(),
        chapter_count: row.chapter_count,
        unread: row.unread,
        category: row.category.clone(),
        in_library,
        updated_at: fixture_timestamp() - ordinal as i64,
    }
}

fn cover_path(row: &DemoBook, artwork: &Path) -> Option<PathBuf> {
    if !row.cover_file.is_empty() {
        return Some(artwork.join(&row.cover_file));
    }
    // The title printed inside a bundled cover belongs to that exact edition.
    // Additional titles and generated volumes use the UI's title plate instead.
    let original = fixture().books.get(row.cover_index)?;
    (row.cover_index < COVER_ART.len() && row.title == original.title)
        .then(|| artwork.join(format!("cover-{:02}.png", row.cover_index)))
}

fn is_bundled_cover(path: &str, artwork: &Path) -> bool {
    (0..COVER_ART.len())
        .any(|index| Path::new(path) == artwork.join(format!("cover-{index:02}.png")).as_path())
        || (0..SEARCH_COVER_ART.len()).any(|index| {
            Path::new(path)
                == artwork
                    .join(format!("search-cover-{index:02}.png"))
                    .as_path()
        })
        || CONTEXT_COVER_ART
            .iter()
            .any(|(name, _)| Path::new(path) == artwork.join(name).as_path())
}

fn chapter_id(book: &Book, number: u32) -> String {
    compound_id(
        &book.source_id,
        &format!("{}-chapter-{number:03}", book.remote_id),
    )
}

fn make_chapter(book: &Book, number: u32, title: String, page_count: u32, read: bool) -> Chapter {
    let published = NaiveDate::from_ymd_opt(2026, 11, 2).expect("Valid demo publication date")
        - Duration::weeks(i64::from(book.chapter_count.saturating_sub(number)));
    let remote_id = format!("{}-chapter-{number:03}", book.remote_id);
    Chapter {
        id: compound_id(&book.source_id, &remote_id),
        book_id: book.id.clone(),
        source_id: book.source_id.clone(),
        remote_id,
        number: f64::from(number),
        title,
        date: published.format("%Y-%m-%d").to_string(),
        page_count,
        downloaded: false,
        read,
    }
}

fn pages(artwork: &Path, count: u32) -> Vec<Page> {
    (0..count)
        .map(|index| Page {
            local_path: Some(artwork.join(format!(
                "page-{:02}.png",
                (index as usize + 1) % PAGE_ART.len()
            ))),
            ..Page::default()
        })
        .collect()
}

fn seed_primary_chapters(store: &Store, book: &Book, artwork: &Path) -> Result<()> {
    let titles = &chapter_fixture().titles;
    let chapters: Vec<_> = (1..=86)
        .map(|number| {
            let title = if number == 34 {
                chapter_fixture().primary_chapter_title.clone()
            } else {
                titles[(86 - number) as usize % titles.len()].clone()
            };
            make_chapter(book, number, title, 40, number <= 33)
        })
        .collect();
    store.save_chapters(&book.id, &chapters)?;
    let cached_pages = pages(artwork, 40);
    for number in 27..=35 {
        let id = chapter_id(book, number);
        store.save_pages(&id, &cached_pages)?;
        store.save_download(&DownloadJob {
            chapter_id: id,
            book_id: book.id.clone(),
            state: "completed".into(),
            completed: 40,
            total: 40,
            error: String::new(),
        })?;
    }
    Ok(())
}

fn seed_sample_chapter(
    store: &Store,
    book: &Book,
    artwork: &Path,
    history: Option<&DemoHistory>,
) -> Result<()> {
    let number = history
        .map(|entry| entry.chapter_number)
        .unwrap_or(book.chapter_count.max(1));
    let total = history.map(|entry| entry.page_count).unwrap_or(40);
    let title = history
        .map(|entry| entry.chapter_title.clone())
        .unwrap_or_default();
    let chapter = make_chapter(book, number, title, total, false);
    store.save_chapters(&book.id, std::slice::from_ref(&chapter))?;
    store.save_pages(&chapter.id, &pages(artwork, total))?;
    Ok(())
}

/// Seed an isolated demo database once, preserving all later session changes.
pub fn seed(store: &Store, data_dir: &Path) -> Result<()> {
    seed_scenario(store, data_dir, false)
}

/// Choose a real initial database snapshot for the requested reference screen.
/// The shared seed marker prevents later route changes from resetting user data.
pub fn seed_for_screen(store: &Store, data_dir: &Path, initial_screen: &str) -> Result<()> {
    seed_scenario(store, data_dir, initial_screen == "library")
}

fn seed_scenario(store: &Store, data_dir: &Path, library_scenario: bool) -> Result<()> {
    let artwork = prepare_assets(data_dir)?;
    let marker = data_dir.join(SEED_MARKER);
    if marker.is_file() {
        return Ok(());
    }
    let data = fixture();
    let mut books = Vec::with_capacity(data.books.len());
    for (index, row) in data.books.iter().enumerate() {
        let book = book_from_row(row, row.id.clone(), &artwork, true, index);
        store.upsert_book(&book)?;
        books.push(book);
    }
    for source in sources() {
        store.save_source(&source)?;
    }
    for (index, book) in books.iter().enumerate() {
        if index == 0 {
            seed_primary_chapters(store, book, &artwork)?;
        } else {
            seed_sample_chapter(
                store,
                book,
                &artwork,
                data.history.iter().find(|entry| entry.book_index == index),
            )?;
        }
    }
    for update in &data.updates {
        let book = books
            .get(update.book_index)
            .context("A demo update references a missing book")?;
        let mut chapter = make_chapter(book, update.chapter_number, update.title.clone(), 1, false);
        chapter.date = update.date.clone();
        store.save_chapters(&book.id, std::slice::from_ref(&chapter))?;
        store.save_pages(&chapter.id, &pages(&artwork, 1))?;
    }
    let reading = &data.continue_reading;
    let current_book = books
        .get(reading.book_index)
        .context("The demo continuation references a missing book")?;
    let current_chapter = chapter_id(current_book, reading.chapter_number);
    store.save_progress(&Progress {
        book_id: current_book.id.clone(),
        chapter_id: current_chapter.clone(),
        page: reading.page,
        total: reading.page_count,
        updated_at: fixture_timestamp(),
        completed: false,
    })?;
    for entry in &data.history {
        let book = books
            .get(entry.book_index)
            .context("The demo history references a missing book")?;
        store.save_progress(&Progress {
            book_id: book.id.clone(),
            chapter_id: chapter_id(book, entry.chapter_number),
            page: entry.page,
            total: entry.page_count,
            updated_at: DateTime::parse_from_rfc3339(&entry.read_at)
                .context("Parse a demo history timestamp")?
                .timestamp(),
            completed: entry.completed,
        })?;
    }
    if library_scenario {
        seed_library_progress_and_downloads(store, &books)?;
    }
    store.toggle_bookmark(&Bookmark {
        book_id: current_book.id.clone(),
        chapter_id: current_chapter,
        page: reading.page,
        created_at: fixture_timestamp(),
    })?;
    let marker_document: &[u8] = if library_scenario {
        b"Aidoku demo fixture version 1\nScenario: library\n"
    } else {
        b"Aidoku demo fixture version 1\n"
    };
    fs::write(marker, marker_document).context("Record the completed demo initialization")?;
    Ok(())
}

fn seed_library_progress_and_downloads(store: &Store, books: &[Book]) -> Result<()> {
    let data = fixture();
    let reading = &data.continue_reading;
    for (index, book) in books.iter().enumerate() {
        let current = index == reading.book_index;
        let number = if current {
            reading.chapter_number
        } else {
            data.history
                .iter()
                .find(|entry| entry.book_index == index)
                .map(|entry| entry.chapter_number)
                .unwrap_or(book.chapter_count.max(1))
        };
        let chapter_id = chapter_id(book, number);
        let total = u32::try_from(store.page_window(&chapter_id, 0, 0)?.total)
            .context("The demo sample chapter has too many pages")?;
        ensure!(
            total > 0,
            "The demo library sample chapter has no page manifest"
        );
        let page = if current {
            reading.page
        } else {
            LIBRARY_PROGRESS_PERCENTAGES
                .iter()
                .find(|(id, _)| *id == book.remote_id.as_str())
                .map(|(_, percentage)| {
                    ((u64::from(total) * u64::from(*percentage) + 50) / 100)
                        .clamp(1, u64::from(total)) as u32
                })
                .unwrap_or(1)
        };
        store.save_progress(&Progress {
            book_id: book.id.clone(),
            chapter_id: chapter_id.clone(),
            page,
            total,
            updated_at: fixture_timestamp() - index as i64,
            completed: false,
        })?;
        if LIBRARY_DOWNLOAD_INDICES.contains(&index) && store.download(&chapter_id)?.is_none() {
            // Use the ordinary completion API: every referenced local page must
            // exist before this entry can advertise an offline download.
            store.save_download(&DownloadJob {
                chapter_id,
                book_id: book.id.clone(),
                state: "completed".into(),
                completed: total,
                total,
                error: String::new(),
            })?;
        }
    }
    Ok(())
}

/// Return the appropriate catalog fixture while retaining changes to cached entries.
pub fn catalog(store: &Store, data_dir: &Path, large: bool) -> Result<Vec<Book>> {
    let artwork = prepare_assets(data_dir)?;
    let data = fixture();
    let mut rows = if large {
        data.catalog_scribe.clone()
    } else {
        data.catalog_paperwhite.clone()
    };
    for index in rows.len()..128 {
        let mut row = data.books[index % data.books.len()].clone();
        row.id = format!("catalog-volume-{index:03}");
        row.title.push_str(
            &data
                .catalog_volume_suffix
                .replace("{number}", &(index / data.books.len() + 1).to_string()),
        );
        row.in_library = false;
        row.category.clear();
        row.unread = 0;
        rows.push(row);
    }
    let mut result = Vec::with_capacity(rows.len());
    for (index, row) in rows.iter().enumerate() {
        let library_row = data.books.iter().find(|book| book.title == row.title);
        let remote_id = if !row.id.is_empty() {
            row.id.clone()
        } else if row.in_library {
            library_row
                .map(|book| book.id.clone())
                .unwrap_or_else(|| format!("catalog-{index:02}"))
        } else {
            library_row
                .map(|book| format!("catalog-{}", book.id))
                .unwrap_or_else(|| format!("catalog-{index:02}"))
        };
        let initial = book_from_row(row, remote_id, &artwork, row.in_library, 100 + index);
        let mut book = if let Some(book) = store.book(&initial.id)? {
            book
        } else {
            store.upsert_book(&initial)?;
            seed_sample_chapter(store, &initial, &artwork, None)?;
            store
                .book(&initial.id)?
                .context("Read the cached demo catalog entry")?
        };
        if book.cover.is_empty() || is_bundled_cover(&book.cover, &artwork) {
            book.cover = cover_path(row, &artwork)
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or_default();
        }
        book.updated_at = if row.updated.is_empty() {
            0
        } else {
            fixture_timestamp() - i64::from(row.updated_days) * 86_400
        };
        result.push(book);
    }
    Ok(result)
}

fn cache_search_book(
    store: &Store,
    row: &DemoBook,
    artwork: &Path,
    ordinal: usize,
) -> Result<Book> {
    let remote_id = if row.id.is_empty() {
        format!("search-{ordinal:03}")
    } else {
        row.id.clone()
    };
    let initial = book_from_row(row, remote_id, artwork, row.in_library, ordinal);
    let mut book = if let Some(book) = store.book(&initial.id)? {
        book
    } else {
        store.upsert_book(&initial)?;
        seed_sample_chapter(store, &initial, artwork, None)?;
        store
            .book(&initial.id)?
            .context("Read the cached demo search result")?
    };
    if book.cover.is_empty() || is_bundled_cover(&book.cover, artwork) {
        book.cover = cover_path(row, artwork)
            .map(|path| path.to_string_lossy().into_owned())
            .unwrap_or_default();
    }
    Ok(book)
}

fn matches_query(book: &DemoBook, query: &str) -> bool {
    book.title.to_lowercase().contains(query) || book.author.to_lowercase().contains(query)
}

/// Search actual fixture metadata, with the supplied design query reproducing its source states.
pub fn search(store: &Store, data_dir: &Path, query: &str) -> Result<Vec<DemoSearchGroup>> {
    let query = query.trim();
    if query.is_empty() {
        return Ok(Vec::new());
    }
    let artwork = prepare_assets(data_dir)?;
    let data = fixture();
    let design_query = query == data.search_query;
    let normalized = query.to_lowercase();
    let enabled_sources: HashSet<_> = store
        .sources()?
        .into_iter()
        .filter(|source| source.enabled)
        .map(|source| source.id)
        .collect();
    let mut result = Vec::with_capacity(data.search_groups.len());
    for (group_index, group) in data.search_groups.iter().enumerate() {
        if !enabled_sources.contains(&group.id) {
            continue;
        }
        let rows: Vec<&DemoBook> = if design_query {
            group.books.iter().collect()
        } else if group.id == SOURCE_ID {
            let mut seen = HashSet::new();
            group
                .books
                .iter()
                .chain(data.books.iter())
                .filter(|book| matches_query(book, &normalized))
                .filter(|book| seen.insert(book.title.clone()))
                .collect()
        } else {
            group
                .books
                .iter()
                .filter(|book| matches_query(book, &normalized))
                .collect()
        };
        let books = rows
            .into_iter()
            .enumerate()
            .map(|(index, row)| {
                cache_search_book(store, row, &artwork, 1000 + group_index * 100 + index)
            })
            .collect::<Result<Vec<_>>>()?;
        result.push(DemoSearchGroup {
            id: group.id.clone(),
            title: group.title.clone(),
            books,
            error: if design_query {
                group.error.clone()
            } else {
                None
            },
        });
    }
    if !design_query && result.iter().all(|group| group.books.is_empty()) {
        result.clear();
    }
    Ok(result)
}

/// Identify the book used by the initial continuation and reader screenshots.
pub fn primary_id() -> String {
    compound_id(SOURCE_ID, "demo-00")
}

/// Select the wider Paperwhite cover edition without replacing a user's custom art.
pub fn library_cover(data_dir: &Path, book: &Book, large: bool) -> Option<PathBuf> {
    if large {
        return None;
    }
    let artwork = artwork_directory(data_dir).ok()?;
    if !is_bundled_cover(&book.cover, &artwork) {
        return None;
    }
    let index = book
        .remote_id
        .strip_prefix("demo-")?
        .parse::<usize>()
        .ok()?;
    matches!(index, 2 | 6 | 7).then(|| artwork.join(format!("library-cover-pw-{index:02}.png")))
}

/// Select the history edition while respecting a cover chosen during the session.
pub fn history_cover(data_dir: &Path, book: &Book) -> Option<PathBuf> {
    let artwork = artwork_directory(data_dir).ok()?;
    if !book.cover.is_empty() && !is_bundled_cover(&book.cover, &artwork) {
        return None;
    }
    fixture()
        .history
        .iter()
        .find(|entry| {
            !entry.cover_file.is_empty()
                && fixture()
                    .books
                    .get(entry.book_index)
                    .is_some_and(|row| compound_id(SOURCE_ID, &row.id) == book.id)
        })
        .map(|entry| artwork.join(&entry.cover_file))
}

/// Provide the seven installed sources, including the disabled browser-only fixture.
pub fn sources() -> Vec<SourceConfig> {
    chapter_fixture().sources.clone()
}

/// Return an embedded demo-only index; its package URLs are never fetched in demo mode.
pub fn available_sources() -> Vec<RepositorySource> {
    static REPOSITORY: OnceLock<Repository> = OnceLock::new();
    REPOSITORY
        .get_or_init(|| {
            serde_json::from_str(include_str!("../assets/demo/repository.json"))
                .expect("Bundled demo source repository must be valid JSON")
        })
        .sources
        .clone()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{domain::Preferences, store::view_queries::LibraryQuery};

    fn fixture_book_id(index: usize) -> String {
        let row = &fixture().books[index];
        compound_id(
            if row.source_id.is_empty() {
                SOURCE_ID
            } else {
                &row.source_id
            },
            &row.id,
        )
    }

    fn recent_library_ids(store: &Store) -> Vec<String> {
        store
            .library_view_page(
                LibraryQuery {
                    sort_key: Some("recent-read"),
                    ..LibraryQuery::default()
                },
                1,
                18,
            )
            .unwrap()
            .items
            .into_iter()
            .map(|book| book.id)
            .collect()
    }

    #[test]
    fn library_snapshot_orders_real_progress_and_validates_its_offline_chapters() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(":memory:").unwrap();
        seed_for_screen(&store, directory.path(), "library").unwrap();
        assert_eq!(
            recent_library_ids(&store),
            (0..18).map(fixture_book_id).collect::<Vec<_>>()
        );
        assert_eq!(store.history().unwrap().len(), fixture().books.len());
        for index in 0..fixture().books.len() {
            let id = fixture_book_id(index);
            let progress = store.progress(&id).unwrap().unwrap();
            let manifest = store.pages(&progress.chapter_id).unwrap();
            assert!(!manifest.is_empty());
            assert!(
                manifest
                    .iter()
                    .all(|page| page.local_path.as_ref().is_some_and(|path| path.is_file()))
            );
            assert_eq!(progress.total as usize, manifest.len());
            assert_eq!(progress.updated_at, fixture_timestamp() - index as i64);
            assert_eq!(
                progress.page,
                match index {
                    0 => 12,
                    2 => 5,
                    7 => 13,
                    21 => 18,
                    _ => 1,
                }
            );
            let downloaded = store
                .chapters(&id)
                .unwrap()
                .iter()
                .any(|chapter| chapter.downloaded);
            assert_eq!(downloaded, LIBRARY_DOWNLOAD_INDICES.contains(&index));
            if LIBRARY_DOWNLOAD_INDICES.contains(&index) {
                let job = store.download(&progress.chapter_id).unwrap().unwrap();
                assert_eq!(job.state, "completed");
                assert_eq!((job.completed, job.total), (progress.total, progress.total));
            }
        }
        let current = store.progress(&primary_id()).unwrap().unwrap();
        assert_eq!(
            (current.chapter_id, current.page, current.total),
            (
                chapter_id(&store.book(&primary_id()).unwrap().unwrap(), 34),
                12,
                40
            )
        );
    }

    #[test]
    fn history_entrypoint_preserves_the_public_history_fixture_and_custom_covers() {
        let directory = tempfile::tempdir().unwrap();
        let legacy = Store::open(":memory:").unwrap();
        let history = Store::open(":memory:").unwrap();
        seed(&legacy, &directory.path().join("legacy")).unwrap();
        let data_dir = directory.path().join("history");
        seed_for_screen(&history, &data_dir, "history").unwrap();
        assert_eq!(legacy.history().unwrap(), history.history().unwrap());
        let records = history.history().unwrap();
        assert_eq!(records.len(), 20);
        assert_eq!(
            records
                .iter()
                .take(5)
                .map(|progress| progress.book_id.clone())
                .collect::<Vec<_>>(),
            [0, 9, 21, 2, 3]
                .into_iter()
                .map(fixture_book_id)
                .collect::<Vec<_>>()
        );
        let mut salt = history.book(&fixture_book_id(21)).unwrap().unwrap();
        assert!(salt.cover.is_empty());
        let context_art = history_cover(&data_dir, &salt).unwrap();
        assert_eq!(context_art.file_name().unwrap(), "history-cover-02.png");
        assert!(context_art.is_file());
        let custom = directory.path().join("custom-cover.png");
        fs::write(&custom, PAGE_ART[0]).unwrap();
        salt.cover = custom.to_string_lossy().into_owned();
        history.upsert_book(&salt).unwrap();
        assert!(history_cover(&data_dir, &salt).is_none());
        let projected = cache_search_book(
            &history,
            &fixture().books[21],
            &artwork_directory(&data_dir).unwrap(),
            21,
        )
        .unwrap();
        assert_eq!(projected.cover, salt.cover);
    }

    #[test]
    fn completed_seed_marker_preserves_user_changes_across_routes_and_database_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let database = directory.path().join("library.sqlite3");
        let store = Store::open(&database).unwrap();
        seed_for_screen(&store, directory.path(), "library").unwrap();
        let mut progress = store.progress(&fixture_book_id(9)).unwrap().unwrap();
        progress.page = 2;
        progress.updated_at = fixture_timestamp() + 100;
        store.save_progress(&progress).unwrap();
        let offline = store.progress(&fixture_book_id(3)).unwrap().unwrap();
        store
            .remove_completed_download(&offline.chapter_id)
            .unwrap();
        let preferences = Preferences {
            brightness: 3,
            spread: false,
            ..Preferences::default()
        };
        store.save_preferences(&preferences).unwrap();
        let mut book = store.book(&fixture_book_id(21)).unwrap().unwrap();
        book.cover = directory
            .path()
            .join("chosen-cover.png")
            .to_string_lossy()
            .into_owned();
        store.upsert_book(&book).unwrap();
        let marker = fs::read(directory.path().join(SEED_MARKER)).unwrap();
        assert_eq!(recent_library_ids(&store)[0], fixture_book_id(9));
        drop(store);

        let store = Store::open(&database).unwrap();
        seed_for_screen(&store, directory.path(), "history").unwrap();
        seed_for_screen(&store, directory.path(), "library").unwrap();
        seed(&store, directory.path()).unwrap();
        assert_eq!(store.progress(&fixture_book_id(9)).unwrap(), Some(progress));
        assert_eq!(recent_library_ids(&store)[0], fixture_book_id(9));
        assert!(store.download(&offline.chapter_id).unwrap().is_none());
        assert!(
            !store
                .chapter(&offline.chapter_id)
                .unwrap()
                .unwrap()
                .downloaded
        );
        assert_eq!(store.preferences().unwrap(), preferences);
        assert_eq!(store.book(&book.id).unwrap().unwrap().cover, book.cover);
        assert_eq!(
            fs::read(directory.path().join(SEED_MARKER)).unwrap(),
            marker
        );
    }

    #[test]
    fn unrelated_titles_and_volume_editions_do_not_reuse_another_printed_title() {
        let artwork = Path::new("demo-assets");
        for (index, row) in fixture().books.iter().enumerate() {
            let book = book_from_row(row, row.id.clone(), artwork, true, index);
            if index < COVER_ART.len() {
                let expected = artwork
                    .join(format!("cover-{index:02}.png"))
                    .to_string_lossy()
                    .into_owned();
                assert_eq!(book.cover, expected);
            } else {
                assert!(
                    book.cover.is_empty(),
                    "An additional title reused unrelated cover art"
                );
            }
        }
        let mut volume = fixture().books[0].clone();
        volume.id = "another-volume".into();
        volume.title.push_str(" Volume 2");
        assert!(cover_path(&volume, artwork).is_none());
        volume.cover_file = "search-cover-00.png".into();
        assert_eq!(
            cover_path(&volume, artwork),
            Some(artwork.join("search-cover-00.png"))
        );
    }
}
