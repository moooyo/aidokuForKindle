use std::collections::HashMap;

use anyhow::{Result, ensure};
use chrono::Datelike;
use rusqlite::{OptionalExtension, params};

use super::{Store, decode};
use crate::domain::{Book, Chapter, Progress};

#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct LibraryCounts {
    pub total: usize,
    pub reading: usize,
    pub planned: usize,
    pub completed: usize,
    pub updated: usize,
}

#[derive(Debug, Clone, Copy, Default)]
pub(crate) struct LibraryQuery {
    pub category: i32,
    pub unread_only: bool,
    pub downloaded_only: bool,
    pub sort_title: bool,
    pub demo_order: bool,
    /// An interaction sort key replaces the legacy filter and sort flags.
    pub sort_key: Option<&'static str>,
    pub ascending: bool,
    /// Downloaded, unread, tracked, completed; -1 excludes, 0 ignores, 1 includes.
    pub filters: [i8; 4],
}

#[derive(Debug)]
pub(crate) struct ViewPage<T> {
    pub total: usize,
    pub page: usize,
    pub items: Vec<T>,
}

#[derive(Debug, Default)]
pub(crate) struct BookViewMetadata {
    pub book: Option<Book>,
    pub progress: Option<Progress>,
    pub downloaded: bool,
}

#[derive(Debug, Default)]
pub(crate) struct ChapterViewSummary {
    pub total: usize,
    pub downloaded: usize,
    pub unread: usize,
}

#[derive(Debug)]
pub(crate) struct HistoryViewRow {
    pub book: Book,
    pub chapter: Chapter,
    pub progress: Progress,
}

#[derive(Debug)]
pub(crate) struct HistoryLayoutPage {
    pub total: usize,
    pub page: usize,
    pub page_count: usize,
    pub items: Vec<HistoryViewRow>,
}

#[derive(Debug, Clone, Copy, Default)]
struct HistoryWindow {
    offset: usize,
    len: usize,
}

struct HistoryLayoutPlanner {
    available_height: u64,
    header_height: u64,
    row_height: u64,
    columns: usize,
    requested_page: usize,
    completed_pages: usize,
    completed_items: usize,
    current_len: usize,
    current_height: u64,
    selected: Option<HistoryWindow>,
    last: HistoryWindow,
}

impl HistoryLayoutPlanner {
    fn new(page: usize, large: bool, logical_height: u32) -> Self {
        // Half-pixel layout edges are represented with doubled design pixels.
        let (reserved, header_height, row_height, columns) = if large {
            (723, 120, 248, 2)
        } else {
            (553, 88, 152, 1)
        };
        let available_height = (u64::from(logical_height) * 2)
            .saturating_sub(reserved)
            .max(header_height + row_height);
        Self {
            available_height,
            header_height,
            row_height,
            columns,
            requested_page: page.max(1),
            completed_pages: 0,
            completed_items: 0,
            current_len: 0,
            current_height: 0,
            selected: None,
            last: HistoryWindow::default(),
        }
    }

    fn complete_pages(&mut self, len: usize, count: usize) {
        if count == 0 {
            return;
        }
        let first = self.completed_pages + 1;
        if (first..first + count).contains(&self.requested_page) {
            self.selected = Some(HistoryWindow {
                offset: self.completed_items + (self.requested_page - first) * len,
                len,
            });
        }
        self.last = HistoryWindow {
            offset: self.completed_items + (count - 1) * len,
            len,
        };
        self.completed_pages += count;
        self.completed_items += count * len;
    }

    fn finish_current_page(&mut self) {
        if self.current_len == 0 {
            return;
        }
        self.complete_pages(self.current_len, 1);
        self.current_len = 0;
        self.current_height = 0;
    }

    fn add_group(&mut self, count: usize) {
        if count == 0 {
            return;
        }
        if self.available_height - self.current_height < self.header_height + self.row_height {
            self.finish_current_page();
        }
        let rows = ((self.available_height - self.current_height - self.header_height)
            / self.row_height) as usize;
        let take = count.min(rows * self.columns);
        self.current_len += take;
        self.current_height +=
            self.header_height + take.div_ceil(self.columns) as u64 * self.row_height;
        let remaining = count - take;
        if remaining == 0 {
            return;
        }
        self.finish_current_page();
        let page_capacity = ((self.available_height - self.header_height) / self.row_height)
            as usize
            * self.columns;
        // Skip complete same-date pages arithmetically instead of visiting rows.
        self.complete_pages(page_capacity, remaining / page_capacity);
        let tail = remaining % page_capacity;
        if tail != 0 {
            self.current_len = tail;
            self.current_height =
                self.header_height + tail.div_ceil(self.columns) as u64 * self.row_height;
        }
    }

    fn finish(mut self) -> (usize, usize, usize, HistoryWindow) {
        self.finish_current_page();
        let page_count = self.completed_pages.max(1);
        (
            self.completed_items,
            self.requested_page.min(page_count),
            page_count,
            self.selected.unwrap_or(self.last),
        )
    }
}

#[derive(Debug)]
pub(crate) struct CatalogBookFlags {
    pub unread: u32,
    pub downloaded: bool,
}

fn page_bounds(total: usize, page: usize, page_size: usize) -> Result<(usize, usize)> {
    ensure!(
        (1..=1_000).contains(&page_size),
        "The view page size is outside its limit"
    );
    let page = page.clamp(1, total.div_ceil(page_size).max(1));
    Ok((page, (page - 1) * page_size))
}

pub(crate) fn detail_chapter_page_count(total: usize, large: bool) -> usize {
    if large {
        total.div_ceil(16).max(1)
    } else {
        1 + total.saturating_sub(6).div_ceil(12)
    }
}

fn literal_search_pattern(query: &str) -> String {
    let mut pattern = String::with_capacity(query.len() + 2);
    pattern.push('%');
    for character in query.chars() {
        if matches!(character, '%' | '_' | '!') {
            pattern.push('!');
        }
        pattern.push(character);
    }
    pattern.push('%');
    pattern
}

fn day_prefixes(day: &str) -> [String; 4] {
    let Ok(date) = chrono::NaiveDate::parse_from_str(day, "%Y-%m-%d") else {
        return std::array::from_fn(|_| day.to_owned());
    };
    // The existing calendar parser accepts unpadded dates from source providers.
    [
        format!("{:04}-{:02}-{:02}", date.year(), date.month(), date.day()),
        format!("{:04}-{}-{:02}", date.year(), date.month(), date.day()),
        format!("{:04}-{:02}-{}", date.year(), date.month(), date.day()),
        format!("{:04}-{}-{}", date.year(), date.month(), date.day()),
    ]
}

impl Store {
    pub(crate) fn library_counts(&self) -> Result<LibraryCounts> {
        Ok(self.connection.query_row(
            "SELECT COUNT(*),
                    COALESCE(SUM(category = 'reading'), 0),
                    COALESCE(SUM(category = 'planned'), 0),
                    COALESCE(SUM(category = 'completed'), 0),
                    COALESCE(SUM(json_extract(document, '$.unread') > 0), 0)
             FROM books WHERE in_library = 1",
            [],
            |row| {
                Ok(LibraryCounts {
                    total: row.get(0)?,
                    reading: row.get(1)?,
                    planned: row.get(2)?,
                    completed: row.get(3)?,
                    updated: row.get(4)?,
                })
            },
        )?)
    }

    pub(crate) fn library_continuation(&self, current: Option<&str>) -> Result<Option<String>> {
        let latest = self
            .connection
            .query_row(
                "SELECT p.book_id FROM progress p JOIN books b ON b.id = p.book_id
             WHERE b.in_library = 1 ORDER BY p.updated_at DESC, p.rowid DESC LIMIT 1",
                [],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        if latest.is_some() {
            return Ok(latest);
        }
        if let Some(current) = current {
            let present = self.connection.query_row(
                "SELECT EXISTS(SELECT 1 FROM books WHERE id = ?1 AND in_library = 1)",
                [current],
                |row| row.get::<_, bool>(0),
            )?;
            if present {
                return Ok(Some(current.to_owned()));
            }
        }
        Ok(self
            .connection
            .query_row(
                "SELECT id FROM books WHERE in_library = 1
             ORDER BY updated_at DESC, title COLLATE NOCASE, id LIMIT 1",
                [],
                |row| row.get(0),
            )
            .optional()?)
    }

    pub(crate) fn library_search_matches(&self, query: &str, limit: usize) -> Result<Vec<Book>> {
        ensure!(
            limit <= 12,
            "The local library search limit exceeds its maximum"
        );
        if query.is_empty() || limit == 0 {
            return Ok(Vec::new());
        }
        let pattern = literal_search_pattern(query);
        let mut statement = self.connection.prepare(
            "WITH visible AS MATERIALIZED (
                 SELECT id FROM books WHERE in_library = 1
                 AND title LIKE ?1 ESCAPE '!' COLLATE NOCASE
                 ORDER BY title COLLATE NOCASE, id LIMIT ?2)
             SELECT b.document FROM visible v JOIN books b ON b.id = v.id
             ORDER BY b.title COLLATE NOCASE, b.id",
        )?;
        let items = statement
            .query_map(params![pattern, limit], |row| row.get::<_, String>(0))?
            .map(|row| decode(row?))
            .collect::<Result<Vec<Book>>>()?;
        Ok(items)
    }

    pub(crate) fn source_search_matches(
        &self,
        query: &str,
        source_id: &str,
        limit: usize,
    ) -> Result<Vec<Book>> {
        ensure!(
            limit <= 12,
            "The local source search limit exceeds its maximum"
        );
        if query.is_empty() || limit == 0 {
            return Ok(Vec::new());
        }
        let pattern = literal_search_pattern(query);
        let mut statement = self.connection.prepare(
            "WITH visible AS MATERIALIZED (
                 SELECT id FROM books WHERE source_id = ?2
                 AND (title LIKE ?1 ESCAPE '!' COLLATE NOCASE
                      OR COALESCE(json_extract(document, '$.author'), '') LIKE ?1 ESCAPE '!' COLLATE NOCASE)
                 ORDER BY updated_at DESC, title COLLATE NOCASE, id LIMIT ?3)
             SELECT b.document FROM visible v JOIN books b ON b.id = v.id
             ORDER BY b.updated_at DESC, b.title COLLATE NOCASE, b.id"
        )?;
        let items = statement
            .query_map(params![pattern, source_id, limit], |row| {
                row.get::<_, String>(0)
            })?
            .map(|row| decode(row?))
            .collect::<Result<Vec<Book>>>()?;
        Ok(items)
    }

    pub(crate) fn library_view_page(
        &self,
        query: LibraryQuery,
        page: usize,
        page_size: usize,
    ) -> Result<ViewPage<Book>> {
        ensure!(
            query.filters.iter().all(|value| (-1..=1).contains(value)),
            "Unknown three-state library filter"
        );
        let legacy_unread_only = query.sort_key.is_none() && query.unread_only;
        let legacy_downloaded_only = query.sort_key.is_none() && query.downloaded_only;
        let completed_status = crate::tr("completed").trim().to_lowercase();
        // No tracking adapter currently persists per-book tracking records.
        let filter = "b.in_library = 1
            AND (CASE ?1
                 WHEN 1 THEN b.category = 'reading'
                 WHEN 2 THEN b.category = 'planned'
                 WHEN 3 THEN b.category = 'completed'
                 WHEN 4 THEN b.category NOT IN ('reading', 'planned', 'completed')
                 ELSE 1 END)
            AND (?2 = 0 OR json_extract(b.document, '$.unread') > 0)
            AND (?3 = 0 OR EXISTS(
                SELECT 1 FROM chapters c WHERE c.book_id = b.id
                AND json_extract(c.document, '$.downloaded') = 1))
            AND (?4 = 0 OR EXISTS(
                SELECT 1 FROM chapters c WHERE c.book_id = b.id
                AND json_extract(c.document, '$.downloaded') = 1) = (?4 = 1))
            AND (?5 = 0 OR (COALESCE(json_extract(b.document, '$.unread'), 0) > 0) = (?5 = 1))
            AND ?6 <= 0
            AND (?7 = 0 OR (lower(trim(COALESCE(json_extract(b.document, '$.status'), '')))
                IN ('completed', 'complete', 'ended', 'finished', ?8)) = (?7 = 1))";
        let total = self.connection.query_row(
            &format!("SELECT COUNT(*) FROM books b WHERE {filter}"),
            params![
                query.category,
                legacy_unread_only,
                legacy_downloaded_only,
                query.filters[0],
                query.filters[1],
                query.filters[2],
                query.filters[3],
                completed_status
            ],
            |row| row.get::<_, usize>(0),
        )?;
        let (page, offset) = page_bounds(total, page, page_size)?;
        let (selection, order, visible_order) = if let Some(key) = query.sort_key {
            let metric = match key {
                "title" => "b.title COLLATE BINARY",
                "recent-read" => {
                    "COALESCE((SELECT MAX(p.updated_at) FROM progress p WHERE p.book_id = b.id), 0)"
                }
                "recent-open" => {
                    "COALESCE((SELECT m.opened_at FROM book_ui_metadata m WHERE m.book_id = b.id), 0)"
                }
                "recent-update" => "b.updated_at",
                "added-at" => {
                    "COALESCE((SELECT m.added_at FROM book_ui_metadata m WHERE m.book_id = b.id), 0)"
                }
                "latest-chapter" => {
                    "COALESCE((SELECT MAX(c.number) FROM chapters c WHERE c.book_id = b.id), 0)"
                }
                "unread-count" => "COALESCE(json_extract(b.document, '$.unread'), 0)",
                "chapter-count" => "COALESCE(json_extract(b.document, '$.chapter_count'), 0)",
                _ => anyhow::bail!("Unknown library sort key"),
            };
            let direction = if query.ascending { "ASC" } else { "DESC" };
            (
                format!("b.id, {metric} AS sort_value, b.rowid AS tie_breaker"),
                format!("sort_value {direction}, tie_breaker {direction}, b.id {direction}"),
                format!("v.sort_value {direction}, v.tie_breaker {direction}, b.id {direction}"),
            )
        } else {
            let order = if query.sort_title {
                "b.title COLLATE BINARY, b.updated_at DESC, b.title COLLATE NOCASE, b.id"
            } else if query.demo_order {
                "b.remote_id COLLATE BINARY, b.updated_at DESC, b.title COLLATE NOCASE, b.id"
            } else {
                "COALESCE((SELECT p.updated_at FROM progress p WHERE p.book_id = b.id
                  ORDER BY p.updated_at DESC, p.rowid DESC LIMIT 1), b.updated_at) DESC,
                  b.title COLLATE BINARY, b.updated_at DESC, b.title COLLATE NOCASE, b.id"
            };
            ("b.id".to_owned(), order.to_owned(), order.to_owned())
        };
        let mut statement = self.connection.prepare(&format!(
            "WITH visible AS MATERIALIZED (
                 SELECT {selection} FROM books b WHERE {filter}
                 ORDER BY {order} LIMIT ?9 OFFSET ?10)
             SELECT b.document FROM visible v JOIN books b ON b.id = v.id
             ORDER BY {visible_order}"
        ))?;
        let items = statement
            .query_map(
                params![
                    query.category,
                    legacy_unread_only,
                    legacy_downloaded_only,
                    query.filters[0],
                    query.filters[1],
                    query.filters[2],
                    query.filters[3],
                    completed_status,
                    page_size,
                    offset
                ],
                |row| row.get::<_, String>(0),
            )?
            .map(|row| decode(row?))
            .collect::<Result<Vec<Book>>>()?;
        Ok(ViewPage { total, page, items })
    }

    pub(crate) fn book_view_metadata(&self, id: &str) -> Result<BookViewMetadata> {
        let row = self
            .connection
            .query_row(
                "SELECT b.document,
                    EXISTS(SELECT 1 FROM chapters c WHERE c.book_id = b.id
                           AND json_extract(c.document, '$.downloaded') = 1),
                    p.book_id, p.chapter_id, p.page, p.total, p.updated_at, p.completed
             FROM books b LEFT JOIN progress p ON p.rowid = (
                 SELECT r.rowid FROM progress r WHERE r.book_id = b.id
                 ORDER BY r.updated_at DESC, r.rowid DESC LIMIT 1)
             WHERE b.id = ?1",
                [id],
                |row| {
                    let progress = if let Some(chapter_id) = row.get::<_, Option<String>>(3)? {
                        Some(Progress {
                            book_id: row.get(2)?,
                            chapter_id,
                            page: row.get(4)?,
                            total: row.get(5)?,
                            updated_at: row.get(6)?,
                            completed: row.get(7)?,
                        })
                    } else {
                        None
                    };
                    Ok((row.get::<_, String>(0)?, row.get::<_, bool>(1)?, progress))
                },
            )
            .optional()?;
        let Some((document, downloaded, progress)) = row else {
            return Ok(BookViewMetadata::default());
        };
        Ok(BookViewMetadata {
            book: Some(decode(document)?),
            progress,
            downloaded,
        })
    }

    pub(crate) fn catalog_book_flags(
        &self,
        ids: &[&str],
    ) -> Result<HashMap<String, CatalogBookFlags>> {
        let mut result = HashMap::with_capacity(ids.len());
        // Keep bindings below SQLite's variable limit, including older userstores.
        for ids in ids.chunks(400) {
            let bindings = std::iter::repeat_n("?", ids.len())
                .collect::<Vec<_>>()
                .join(",");
            let mut statement = self.connection.prepare(&format!(
                "SELECT b.id, COALESCE(json_extract(b.document, '$.unread'), 0),
                        EXISTS(SELECT 1 FROM chapters c WHERE c.book_id = b.id
                               AND json_extract(c.document, '$.downloaded') = 1)
                 FROM books b WHERE b.id IN ({bindings})"
            ))?;
            for entry in statement.query_map(rusqlite::params_from_iter(ids.iter()), |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    CatalogBookFlags {
                        unread: row.get(1)?,
                        downloaded: row.get(2)?,
                    },
                ))
            })? {
                let (id, flags) = entry?;
                result.insert(id, flags);
            }
        }
        Ok(result)
    }

    pub(crate) fn chapter_view_summary(
        &self,
        book_id: &str,
        current_unread: Option<&str>,
    ) -> Result<ChapterViewSummary> {
        Ok(self.connection.query_row(
            "SELECT COUNT(*),
                    COALESCE(SUM(json_extract(document, '$.downloaded') = 1), 0),
                    COALESCE(SUM(json_extract(document, '$.read') = 0
                                 AND (?2 IS NULL OR id != ?2)), 0)
             FROM chapters WHERE book_id = ?1",
            params![book_id, current_unread],
            |row| {
                Ok(ChapterViewSummary {
                    total: row.get(0)?,
                    downloaded: row.get(1)?,
                    unread: row.get(2)?,
                })
            },
        )?)
    }

    pub(crate) fn first_chapter(&self, book_id: &str) -> Result<Option<Chapter>> {
        let document = self
            .connection
            .query_row(
                "SELECT document FROM chapters WHERE book_id = ?1 ORDER BY number, id LIMIT 1",
                [book_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        document.map(decode).transpose()
    }

    pub(crate) fn first_unread_chapter_or_first(&self, book_id: &str) -> Result<Option<Chapter>> {
        let document = self
            .connection
            .query_row(
                "WITH candidate AS MATERIALIZED (
                 SELECT id FROM chapters WHERE book_id = ?1
                 AND json_extract(document, '$.read') = 0 ORDER BY number, id LIMIT 1)
             SELECT c.document FROM candidate v JOIN chapters c ON c.id = v.id",
                [book_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        if let Some(document) = document {
            return Ok(Some(decode(document)?));
        }
        self.first_chapter(book_id)
    }

    pub(crate) fn next_chapter_downloaded(&self, book_id: &str, number: f64) -> Result<bool> {
        Ok(self
            .connection
            .query_row(
                "SELECT json_extract(document, '$.downloaded') FROM chapters
             WHERE book_id = ?1 AND number > ?2 ORDER BY number, id LIMIT 1",
                params![book_id, number],
                |row| row.get(0),
            )
            .optional()?
            .unwrap_or(false))
    }

    pub(crate) fn chapters_view_page(
        &self,
        book_id: &str,
        newest_first: bool,
        total: usize,
        page: usize,
        page_size: usize,
    ) -> Result<ViewPage<Chapter>> {
        let (page, offset) = page_bounds(total, page, page_size)?;
        let items = self.chapter_view_window(book_id, newest_first, offset, page_size)?;
        Ok(ViewPage { total, page, items })
    }

    pub(crate) fn detail_chapters_view_page(
        &self,
        book_id: &str,
        newest_first: bool,
        total: usize,
        page: usize,
        large: bool,
    ) -> Result<ViewPage<Chapter>> {
        let page = page.clamp(1, detail_chapter_page_count(total, large));
        let (page_size, offset) = if large {
            (16, (page - 1) * 16)
        } else if page == 1 {
            (6, 0)
        } else {
            (12, 6 + (page - 2) * 12)
        };
        let items = self.chapter_view_window(book_id, newest_first, offset, page_size)?;
        Ok(ViewPage { total, page, items })
    }

    pub(crate) fn filtered_chapters_view_page(
        &self,
        book_id: &str,
        newest_first: bool,
        filter: i32,
        page: usize,
        page_size: usize,
    ) -> Result<ViewPage<Chapter>> {
        let predicate = match filter {
            1 => "AND COALESCE(json_extract(document, '$.read'), 0) = 0",
            2 => "AND json_extract(document, '$.downloaded') = 1",
            _ => "",
        };
        let total: usize = self.connection.query_row(
            &format!("SELECT COUNT(*) FROM chapters WHERE book_id = ?1 {predicate}"),
            [book_id],
            |row| row.get(0),
        )?;
        let (page, offset) = page_bounds(total, page, page_size)?;
        let order = if newest_first {
            "number DESC, id DESC"
        } else {
            "number, id"
        };
        let mut statement = self.connection.prepare(&format!(
            "SELECT document FROM chapters WHERE book_id = ?1 {predicate} ORDER BY {order} LIMIT ?2 OFFSET ?3"
        ))?;
        let items = statement
            .query_map(params![book_id, page_size, offset], |row| {
                row.get::<_, String>(0)
            })?
            .map(|row| decode(row?))
            .collect::<Result<Vec<Chapter>>>()?;
        Ok(ViewPage { total, page, items })
    }

    fn chapter_view_window(
        &self,
        book_id: &str,
        newest_first: bool,
        offset: usize,
        limit: usize,
    ) -> Result<Vec<Chapter>> {
        let order = if newest_first {
            "number DESC, id DESC"
        } else {
            "number, id"
        };
        let mut statement = self.connection.prepare(&format!(
            "SELECT document FROM chapters WHERE book_id = ?1
             ORDER BY {order} LIMIT ?2 OFFSET ?3"
        ))?;
        let items = statement
            .query_map(params![book_id, limit, offset], |row| {
                row.get::<_, String>(0)
            })?
            .map(|row| decode(row?))
            .collect::<Result<Vec<Chapter>>>()?;
        Ok(items)
    }

    pub(crate) fn daily_update_count(&self, day: &str) -> Result<usize> {
        let days = day_prefixes(day);
        Ok(self.connection.query_row(
            "SELECT COUNT(*) FROM chapters c JOIN books b ON b.id = c.book_id
             WHERE b.in_library = 1 AND json_extract(c.document, '$.read') = 0
             AND substr(json_extract(c.document, '$.date'), 1, 10) IN (?1, ?2, ?3, ?4)",
            params![days[0], days[1], days[2], days[3]],
            |row| row.get(0),
        )?)
    }

    pub(crate) fn daily_updates_view_page(
        &self,
        day: &str,
        demo_order: bool,
        unique_books: bool,
        page: usize,
        page_size: usize,
    ) -> Result<ViewPage<(Book, Chapter)>> {
        let days = day_prefixes(day);
        let filter = "b.in_library = 1 AND json_extract(c.document, '$.read') = 0
                      AND substr(json_extract(c.document, '$.date'), 1, 10) IN (?1, ?2, ?3, ?4)";
        let total = self.connection.query_row(
            &format!(
                "SELECT COUNT({}) FROM chapters c JOIN books b ON b.id = c.book_id WHERE {filter}",
                if unique_books { "DISTINCT b.id" } else { "*" }
            ),
            params![days[0], days[1], days[2], days[3]],
            |row| row.get::<_, usize>(0),
        )?;
        let (page, offset) = page_bounds(total, page, page_size)?;
        let priority = if demo_order {
            "CASE remote_id WHEN 'demo-09' THEN 0 WHEN 'demo-22' THEN 1 WHEN 'demo-20' THEN 2 ELSE 3 END,"
        } else {
            ""
        };
        let ranking = if unique_books {
            ", ROW_NUMBER() OVER (PARTITION BY b.id ORDER BY
                json_extract(c.document, '$.date') DESC, c.number DESC, c.id) AS position"
        } else {
            ""
        };
        let select = if unique_books {
            "WHERE position = 1"
        } else {
            ""
        };
        let mut statement = self.connection.prepare(&format!(
            "WITH updates AS (
                 SELECT b.id AS book_id, b.remote_id AS remote_id, b.updated_at AS updated_at,
                        json_extract(c.document, '$.date') AS date, c.number AS number, c.id AS chapter_id
                        {ranking}
                 FROM chapters c JOIN books b ON b.id = c.book_id WHERE {filter}),
             visible AS MATERIALIZED (
                 SELECT * FROM updates {select}
                 ORDER BY {priority} date DESC, updated_at DESC, book_id, number DESC, chapter_id
                 LIMIT ?5 OFFSET ?6)
             SELECT b.document, c.document FROM visible v
             JOIN books b ON b.id = v.book_id JOIN chapters c ON c.id = v.chapter_id
             ORDER BY {qualified_priority} v.date DESC, v.updated_at DESC, v.book_id, v.number DESC, v.chapter_id",
             qualified_priority = if demo_order {
                 "CASE v.remote_id WHEN 'demo-09' THEN 0 WHEN 'demo-22' THEN 1 WHEN 'demo-20' THEN 2 ELSE 3 END,"
             } else { "" },
        ))?;
        let items = statement
            .query_map(
                params![days[0], days[1], days[2], days[3], page_size, offset],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?)),
            )?
            .map(|row| {
                let (book, chapter) = row?;
                Ok((decode(book)?, decode(chapter)?))
            })
            .collect::<Result<Vec<_>>>()?;
        Ok(ViewPage { total, page, items })
    }

    pub(crate) fn weekly_read_count(&self, start: i64, end: i64) -> Result<usize> {
        Ok(self.connection.query_row(
            "SELECT COUNT(*) FROM progress WHERE updated_at >= ?1 AND updated_at < ?2",
            params![start, end],
            |row| row.get(0),
        )?)
    }

    pub(crate) fn history_view_page(
        &self,
        page: usize,
        page_size: usize,
    ) -> Result<ViewPage<HistoryViewRow>> {
        let total = self.connection.query_row(
            "SELECT COUNT(*) FROM progress p JOIN books b ON b.id = p.book_id
             JOIN chapters c ON c.id = p.chapter_id",
            [],
            |row| row.get::<_, usize>(0),
        )?;
        let (page, offset) = page_bounds(total, page, page_size)?;
        let items = self.history_view_window(offset, page_size, None)?;
        Ok(ViewPage { total, page, items })
    }

    pub(crate) fn history_layout_view_page(
        &self,
        page: usize,
        large: bool,
        logical_height: u32,
        demo: bool,
    ) -> Result<HistoryLayoutPage> {
        let modifier = if demo { "+8 hours" } else { "localtime" };
        let mut planner = HistoryLayoutPlanner::new(page, large, logical_height);
        let mut statement = self.connection.prepare(
            "SELECT COUNT(*) FROM progress p JOIN books b ON b.id = p.book_id
             JOIN chapters c ON c.id = p.chapter_id
             GROUP BY COALESCE(date(p.updated_at, 'unixepoch', ?1), '')
             ORDER BY COALESCE(date(p.updated_at, 'unixepoch', ?1), '') DESC",
        )?;
        for count in statement.query_map([modifier], |row| row.get::<_, usize>(0))? {
            planner.add_group(count?);
        }
        drop(statement);
        let (total, page, page_count, window) = planner.finish();
        let items = self.history_view_window(window.offset, window.len, Some(modifier))?;
        Ok(HistoryLayoutPage {
            total,
            page,
            page_count,
            items,
        })
    }

    fn history_view_window(
        &self,
        offset: usize,
        limit: usize,
        date_modifier: Option<&str>,
    ) -> Result<Vec<HistoryViewRow>> {
        let day = if date_modifier.is_some() {
            "COALESCE(date(p.updated_at, 'unixepoch', ?3), '')"
        } else {
            "''"
        };
        let date_order = if date_modifier.is_some() {
            "day DESC,"
        } else {
            ""
        };
        let visible_date_order = if date_modifier.is_some() {
            "v.day DESC,"
        } else {
            ""
        };
        let mut statement = self.connection.prepare(&format!(
            "WITH visible AS MATERIALIZED (
                 SELECT p.rowid AS history_row, p.updated_at AS updated_at, {day} AS day
                 FROM progress p JOIN books b ON b.id = p.book_id JOIN chapters c ON c.id = p.chapter_id
                 ORDER BY {date_order} p.updated_at DESC, p.rowid DESC LIMIT ?1 OFFSET ?2)
             SELECT b.document, c.document, p.book_id, p.chapter_id,
                    p.page, p.total, p.updated_at, p.completed
             FROM visible v JOIN progress p ON p.rowid = v.history_row JOIN books b ON b.id = p.book_id
             JOIN chapters c ON c.id = p.chapter_id
             ORDER BY {visible_date_order} v.updated_at DESC, v.history_row DESC"
        ))?;
        let read_row = |row: &rusqlite::Row<'_>| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                Progress {
                    book_id: row.get(2)?,
                    chapter_id: row.get(3)?,
                    page: row.get(4)?,
                    total: row.get(5)?,
                    updated_at: row.get(6)?,
                    completed: row.get(7)?,
                },
            ))
        };
        let rows = if let Some(modifier) = date_modifier {
            statement.query_map(params![limit, offset, modifier], read_row)?
        } else {
            statement.query_map(params![limit, offset], read_row)?
        };
        rows.map(|row| {
            let (book, chapter, progress) = row?;
            Ok(HistoryViewRow {
                book: decode(book)?,
                chapter: decode(chapter)?,
                progress,
            })
        })
        .collect::<Result<Vec<_>>>()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(count: usize) -> Result<(Store, Vec<(Book, Chapter, Progress)>)> {
        let store = Store::open(":memory:")?;
        let mut records = Vec::with_capacity(count);
        let transaction = store.connection.unchecked_transaction()?;
        for index in 0..count {
            let book = Book {
                id: format!("view-book-{index:04}"),
                source_id: "local".into(),
                remote_id: format!("remote-{index:04}"),
                title: format!("Title {:04}", count - index),
                in_library: index % 7 != 6,
                category: ["reading", "planned", "completed", "custom"][index % 4].into(),
                updated_at: 20_000 - index as i64,
                unread: u32::from(index % 5 != 0),
                chapter_count: 1,
                ..Book::default()
            };
            let chapter = Chapter {
                id: format!("{}-chapter", book.id),
                book_id: book.id.clone(),
                source_id: "local".into(),
                remote_id: "chapter".into(),
                title: "A chapter".into(),
                number: 1.0,
                date: if index % 11 == 0 {
                    "2026-11-03"
                } else {
                    "2026-11-04T10:00:00Z"
                }
                .into(),
                downloaded: index % 3 == 0,
                read: book.unread == 0,
                page_count: 20,
            };
            let progress = Progress {
                book_id: book.id.clone(),
                chapter_id: chapter.id.clone(),
                page: if chapter.read { 20 } else { 5 },
                total: 20,
                completed: chapter.read,
                updated_at: 30_000 + (index % 13) as i64,
            };
            transaction.execute(
                "INSERT INTO books (id,source_id,remote_id,title,in_library,category,updated_at,document)
                 VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
                params![book.id, book.source_id, book.remote_id, book.title, book.in_library,
                    book.category, book.updated_at, serde_json::to_string(&book)?],
            )?;
            transaction.execute(
                "INSERT INTO chapters (id,book_id,source_id,remote_id,number,document)
                 VALUES (?1,?2,?3,?4,?5,?6)",
                params![
                    chapter.id,
                    chapter.book_id,
                    chapter.source_id,
                    chapter.remote_id,
                    chapter.number,
                    serde_json::to_string(&chapter)?
                ],
            )?;
            transaction.execute(
                "INSERT INTO progress (chapter_id,book_id,page,total,completed,updated_at)
                 VALUES (?1,?2,?3,?4,?5,?6)",
                params![
                    progress.chapter_id,
                    progress.book_id,
                    progress.page,
                    progress.total,
                    progress.completed,
                    progress.updated_at
                ],
            )?;
            records.push((book, chapter, progress));
        }
        transaction.commit()?;
        Ok((store, records))
    }

    #[test]
    fn normal_library_pages_preserve_filters_sorting_and_real_counts() -> Result<()> {
        let (store, records) = fixture(257)?;
        let counts = store.library_counts()?;
        assert_eq!(
            counts.total,
            records
                .iter()
                .filter(|(book, _, _)| book.in_library)
                .count()
        );
        assert_eq!(
            counts.updated,
            records
                .iter()
                .filter(|(book, _, _)| book.in_library && book.unread > 0)
                .count()
        );
        for category in 0..=4 {
            for downloaded_only in [false, true] {
                for unread_only in [false, true] {
                    for sort_title in [false, true] {
                        let query = LibraryQuery {
                            category,
                            downloaded_only,
                            unread_only,
                            sort_title,
                            ascending: true,
                            ..LibraryQuery::default()
                        };
                        let mut expected = records
                            .iter()
                            .filter(|(book, chapter, _)| {
                                book.in_library
                                    && (!unread_only || book.unread > 0)
                                    && (!downloaded_only || chapter.downloaded)
                                    && match category {
                                        1 => book.category == "reading",
                                        2 => book.category == "planned",
                                        3 => book.category == "completed",
                                        4 => !matches!(
                                            book.category.as_str(),
                                            "reading" | "planned" | "completed"
                                        ),
                                        _ => true,
                                    }
                            })
                            .collect::<Vec<_>>();
                        expected.sort_by(|(left, _, left_progress), (right, _, right_progress)| {
                            if sort_title {
                                left.title.cmp(&right.title)
                            } else {
                                right_progress
                                    .updated_at
                                    .cmp(&left_progress.updated_at)
                                    .then_with(|| left.title.cmp(&right.title))
                            }
                        });
                        for requested in [1, 2, usize::MAX] {
                            let visible = store.library_view_page(query, requested, 8)?;
                            let page = requested.clamp(1, expected.len().div_ceil(8).max(1));
                            assert_eq!(visible.total, expected.len());
                            assert_eq!(visible.page, page);
                            let actual = visible
                                .items
                                .iter()
                                .map(|book| &book.id)
                                .collect::<Vec<_>>();
                            let expected = expected
                                .iter()
                                .skip((page - 1) * 8)
                                .take(8)
                                .map(|(book, _, _)| &book.id)
                                .collect::<Vec<_>>();
                            assert_eq!(actual, expected);
                        }
                    }
                }
            }
        }
        Ok(())
    }

    fn rewrite_book(store: &Store, book: &Book) -> Result<()> {
        store.connection.execute(
            "UPDATE books SET title = ?2, category = ?3, updated_at = ?4, document = ?5 WHERE id = ?1",
            params![book.id, book.title, book.category, book.updated_at, serde_json::to_string(book)?],
        )?;
        Ok(())
    }

    fn insert_chapter(store: &Store, chapter: &Chapter) -> Result<()> {
        store.connection.execute(
            "INSERT INTO chapters (id,book_id,source_id,remote_id,number,document)
             VALUES (?1,?2,?3,?4,?5,?6)",
            params![
                chapter.id,
                chapter.book_id,
                chapter.source_id,
                chapter.remote_id,
                chapter.number,
                serde_json::to_string(chapter)?
            ],
        )?;
        Ok(())
    }

    fn history_groups(counts: &[usize]) -> Result<(Store, Vec<String>)> {
        let (store, records) = fixture(counts.iter().sum())?;
        let noon = chrono::NaiveDate::from_ymd_opt(2026, 11, 6)
            .expect("Fixture date")
            .and_hms_opt(4, 0, 0)
            .expect("Fixture time")
            .and_utc()
            .timestamp();
        let transaction = store.connection.unchecked_transaction()?;
        let mut index = 0;
        for (day, count) in counts.iter().enumerate() {
            for position in 0..*count {
                transaction.execute(
                    "UPDATE progress SET updated_at = ?2 WHERE chapter_id = ?1",
                    params![
                        records[index].1.id,
                        noon - day as i64 * 86_400 - position as i64
                    ],
                )?;
                index += 1;
            }
        }
        transaction.commit()?;
        Ok((
            store,
            records.into_iter().map(|(book, _, _)| book.id).collect(),
        ))
    }

    #[test]
    fn local_library_search_treats_wildcards_literally_and_bounds_decoding() -> Result<()> {
        let (store, mut records) = fixture(7)?;
        let titles = [
            "Case 100%_! Issue A",
            "case 100%_! Issue B",
            "AA 100xxxx_! Wildcard Decoy",
            "AA 100%z! Wildcard Decoy",
            "Nonmatching malformed record",
            "Zed case 100%_! Issue C",
            "AA 100%_! Outside Library",
        ];
        for (index, (book, _, _)) in records.iter_mut().enumerate() {
            book.title = titles[index].into();
            rewrite_book(&store, book)?;
        }
        store.connection.execute(
            "UPDATE books SET document = '{\"unread\":0}' WHERE id = ?1",
            [&records[4].0.id],
        )?;
        assert!(store.books().is_err());
        let expected = vec![
            records[0].0.id.clone(),
            records[1].0.id.clone(),
            records[5].0.id.clone(),
        ];
        let matches = store.library_search_matches("100%_!", 12)?;
        assert_eq!(
            matches.into_iter().map(|book| book.id).collect::<Vec<_>>(),
            expected
        );
        let limited = store.library_search_matches("CASE 100%_! issue", 2)?;
        assert_eq!(
            limited.into_iter().map(|book| book.id).collect::<Vec<_>>(),
            expected[..2].to_vec()
        );
        assert_eq!(
            store.library_search_matches("100%_!", 1)?[0].id,
            expected[0]
        );
        assert!(store.library_search_matches("100%_!", 13).is_err());
        Ok(())
    }

    #[test]
    fn local_source_search_includes_author_matches_and_isolates_the_source() -> Result<()> {
        let (store, mut records) = fixture(7)?;
        let titles = [
            "Needle Title",
            "Author Only",
            "Needle Other Source",
            "Unrelated Malformed Record",
            "Later Needle",
            "Another Source",
            "Archived Author Match",
        ];
        for (index, (book, _, _)) in records.iter_mut().enumerate() {
            book.title = titles[index].into();
            book.source_id = if matches!(index, 2 | 5) {
                "other"
            } else {
                "local"
            }
            .into();
            book.author = if matches!(index, 1 | 6) {
                "NEEDLE Writer"
            } else {
                "Someone Else"
            }
            .into();
            rewrite_book(&store, book)?;
            store.connection.execute(
                "UPDATE books SET source_id = ?2 WHERE id = ?1",
                params![book.id, book.source_id],
            )?;
        }
        store.connection.execute("UPDATE books SET document = '{\"unread\":0,\"author\":\"Someone Else\"}' WHERE id = ?1",
            [&records[3].0.id])?;
        assert!(store.books().is_err());
        let expected = vec![
            records[0].0.id.clone(),
            records[1].0.id.clone(),
            records[4].0.id.clone(),
            records[6].0.id.clone(),
        ];
        let matches = store.source_search_matches("needle", "local", 12)?;
        assert_eq!(
            matches.into_iter().map(|book| book.id).collect::<Vec<_>>(),
            expected
        );
        let limited = store.source_search_matches("needle", "local", 2)?;
        assert_eq!(
            limited.into_iter().map(|book| book.id).collect::<Vec<_>>(),
            expected[..2].to_vec()
        );
        let other = store.source_search_matches("needle", "other", 12)?;
        assert_eq!(
            other.into_iter().map(|book| book.id).collect::<Vec<_>>(),
            vec![records[2].0.id.clone()]
        );
        assert!(store.source_search_matches("needle", "local", 13).is_err());
        Ok(())
    }

    #[test]
    fn history_layout_accounts_for_date_headers_and_resets_scribe_columns() -> Result<()> {
        for (large, height, groups, lengths) in [
            (false, 824, vec![1; 9], vec![4, 4, 1]),
            (true, 1240, vec![1; 9], vec![4, 4, 1]),
            (false, 824, vec![4, 4, 4], vec![6, 6]),
            (true, 1240, vec![3, 3, 3], vec![8, 1]),
            (true, 1240, vec![5, 7, 7, 1], vec![11, 8, 1]),
            (false, 820, vec![3, 3], vec![5, 1]),
            (false, 821, vec![3, 3], vec![6]),
            (true, 1225, vec![5, 5], vec![9, 1]),
            (true, 1226, vec![5, 5], vec![10]),
            (false, 0, vec![3], vec![1, 1, 1]),
            (true, 0, vec![3], vec![2, 1]),
        ] {
            let (store, ids) = history_groups(&groups)?;
            let mut offset = 0;
            for (index, length) in lengths.iter().enumerate() {
                let visible = store.history_layout_view_page(index + 1, large, height, true)?;
                assert_eq!(
                    (visible.total, visible.page, visible.page_count),
                    (ids.len(), index + 1, lengths.len()),
                    "large {large}, height {height}, groups {groups:?}"
                );
                assert_eq!(
                    visible
                        .items
                        .into_iter()
                        .map(|row| row.book.id)
                        .collect::<Vec<_>>(),
                    ids[offset..offset + length].to_vec(),
                    "large {large}, height {height}, groups {groups:?}"
                );
                offset += length;
            }
            assert_eq!(offset, ids.len());
            assert_eq!(
                store.history_layout_view_page(0, large, height, true)?.page,
                1
            );
            let last = store.history_layout_view_page(usize::MAX, large, height, true)?;
            assert_eq!(last.page, lengths.len());
            assert_eq!(
                last.items
                    .into_iter()
                    .map(|row| row.book.id)
                    .collect::<Vec<_>>(),
                ids[ids.len() - lengths.last().expect("Nonempty layout")..].to_vec()
            );
        }
        let store = Store::open(":memory:")?;
        for large in [false, true] {
            let empty = store.history_layout_view_page(usize::MAX, large, 0, true)?;
            assert_eq!(
                (empty.total, empty.page, empty.page_count, empty.items.len()),
                (0, 1, 1, 0)
            );
        }
        Ok(())
    }

    #[test]
    fn demo_history_layout_groups_the_fixed_local_day_at_the_utc_boundary() -> Result<()> {
        let (store, records) = fixture(3)?;
        let boundary = chrono::NaiveDate::from_ymd_opt(2026, 11, 5)
            .expect("Fixture date")
            .and_hms_opt(16, 0, 0)
            .expect("Fixture time")
            .and_utc()
            .timestamp();
        for (index, seconds) in [1, -1, -2].into_iter().enumerate() {
            store.connection.execute(
                "UPDATE progress SET updated_at = ?2 WHERE chapter_id = ?1",
                params![records[index].1.id, boundary + seconds],
            )?;
        }
        let first = store.history_layout_view_page(1, false, 473, true)?;
        assert_eq!((first.total, first.page_count), (3, 2));
        assert_eq!(
            first
                .items
                .into_iter()
                .map(|row| row.book.id)
                .collect::<Vec<_>>(),
            vec![records[0].0.id.clone()]
        );
        let second = store.history_layout_view_page(2, false, 473, true)?;
        assert_eq!(
            second
                .items
                .into_iter()
                .map(|row| row.book.id)
                .collect::<Vec<_>>(),
            vec![records[1].0.id.clone(), records[2].0.id.clone()]
        );
        Ok(())
    }

    #[test]
    fn large_single_date_history_decodes_only_its_requested_layout_window() -> Result<()> {
        let (store, records) = fixture(2_049)?;
        let expected = store.history_layout_view_page(100, false, 824, true)?;
        assert_eq!(
            (
                expected.total,
                expected.page,
                expected.page_count,
                expected.items.len()
            ),
            (2_049, 100, 342, 6)
        );
        let visible_ids = expected
            .items
            .iter()
            .map(|row| row.book.id.as_str())
            .collect::<std::collections::HashSet<_>>();
        let transaction = store.connection.unchecked_transaction()?;
        for (book, chapter, _) in &records {
            if !visible_ids.contains(book.id.as_str()) {
                transaction.execute(
                    "UPDATE books SET document = '{\"unread\":0}' WHERE id = ?1",
                    [&book.id],
                )?;
                transaction.execute(
                    "UPDATE chapters SET document = '{}' WHERE id = ?1",
                    [&chapter.id],
                )?;
            }
        }
        transaction.commit()?;
        assert!(store.books().is_err());
        let visible = store.history_layout_view_page(100, false, 824, true)?;
        assert_eq!(
            (visible.total, visible.page, visible.page_count),
            (2_049, 100, 342)
        );
        assert_eq!(
            visible
                .items
                .iter()
                .map(|row| (&row.book, &row.chapter, &row.progress))
                .collect::<Vec<_>>(),
            expected
                .items
                .iter()
                .map(|row| (&row.book, &row.chapter, &row.progress))
                .collect::<Vec<_>>()
        );
        Ok(())
    }

    #[test]
    fn library_sort_choices_use_independent_metrics_in_both_directions() -> Result<()> {
        let (store, mut records) = fixture(5)?;
        let titles = ["Bravo", "Delta", "Alpha", "Echo", "Charlie"];
        let recent_read = [20, 50, 30, 10, 40];
        let recent_open = [40, 30, 20, 50, 10];
        let recent_update = [50, 10, 40, 30, 20];
        let added_at = [10, 30, 50, 20, 40];
        let latest_chapter = [3.0, 5.0, 1.0, 4.0, 2.0];
        let unread = [4, 2, 5, 3, 1];
        let chapter_count = [5, 4, 2, 1, 3];
        for (index, (book, chapter, progress)) in records.iter_mut().enumerate() {
            book.title = titles[index].into();
            book.updated_at = recent_update[index];
            book.unread = unread[index];
            book.chapter_count = chapter_count[index];
            rewrite_book(&store, book)?;
            chapter.number = latest_chapter[index];
            store.connection.execute(
                "UPDATE chapters SET number = ?2, document = ?3 WHERE id = ?1",
                params![chapter.id, chapter.number, serde_json::to_string(chapter)?],
            )?;
            progress.updated_at = recent_read[index];
            store.connection.execute(
                "UPDATE progress SET updated_at = ?2 WHERE chapter_id = ?1",
                params![progress.chapter_id, progress.updated_at],
            )?;
            store.record_book_open(&book.id, recent_open[index])?;
            store.record_library_added(&book.id, added_at[index])?;
        }
        let mut earlier = records[0].1.clone();
        earlier.id.push_str("-earlier");
        earlier.remote_id.push_str("-earlier");
        earlier.number = 0.0;
        insert_chapter(&store, &earlier)?;
        store.connection.execute(
            "INSERT INTO progress (chapter_id,book_id,page,total,completed,updated_at) VALUES (?1,?2,1,20,0,1)",
            params![earlier.id, earlier.book_id],
        )?;

        let cases = [
            ("title", [2, 0, 4, 1, 3]),
            ("recent-read", [3, 0, 2, 4, 1]),
            ("recent-open", [4, 2, 1, 0, 3]),
            ("recent-update", [1, 4, 3, 2, 0]),
            ("added-at", [0, 3, 1, 4, 2]),
            ("latest-chapter", [2, 4, 0, 3, 1]),
            ("unread-count", [4, 1, 3, 0, 2]),
            ("chapter-count", [3, 2, 4, 1, 0]),
        ];
        for (key, order) in cases {
            for ascending in [true, false] {
                let query = LibraryQuery {
                    sort_key: Some(key),
                    ascending,
                    sort_title: true,
                    demo_order: true,
                    ..LibraryQuery::default()
                };
                let mut expected = order
                    .into_iter()
                    .map(|index| records[index].0.id.clone())
                    .collect::<Vec<_>>();
                if !ascending {
                    expected.reverse();
                }
                let mut actual = Vec::new();
                for page in 1..=3 {
                    let visible = store.library_view_page(query, page, 2)?;
                    assert_eq!(
                        (visible.total, visible.page),
                        (5, page),
                        "{key}, {ascending}"
                    );
                    actual.extend(visible.items.into_iter().map(|book| book.id));
                }
                assert_eq!(actual, expected, "{key}, {ascending}");
            }
        }
        Ok(())
    }

    #[test]
    fn unknown_interaction_times_keep_zero_and_rowid_ties_across_pages() -> Result<()> {
        let (store, mut records) = fixture(6)?;
        for (book, _, _) in &mut records {
            book.title = "Same title".into();
            rewrite_book(&store, book)?;
        }
        store.clear_history()?;
        store.record_book_open(&records[1].0.id, 0)?;
        store.record_library_added(&records[2].0.id, 100)?;
        store.record_book_open(&records[3].0.id, 100)?;
        store.record_library_added(&records[5].0.id, 0)?;
        let cases = [
            ("title", [0, 1, 2, 3, 4, 5]),
            ("recent-read", [0, 1, 2, 3, 4, 5]),
            ("recent-open", [0, 1, 2, 4, 5, 3]),
            ("added-at", [0, 1, 3, 4, 5, 2]),
        ];
        for (key, order) in cases {
            for ascending in [true, false] {
                let query = LibraryQuery {
                    sort_key: Some(key),
                    ascending,
                    ..LibraryQuery::default()
                };
                let mut expected = order
                    .into_iter()
                    .map(|index| records[index].0.id.clone())
                    .collect::<Vec<_>>();
                if !ascending {
                    expected.reverse();
                }
                let mut actual = Vec::new();
                for page in 1..=3 {
                    actual.extend(
                        store
                            .library_view_page(query, page, 2)?
                            .items
                            .into_iter()
                            .map(|book| book.id),
                    );
                }
                assert_eq!(actual, expected, "{key}, {ascending}");
                assert_eq!(
                    actual
                        .iter()
                        .collect::<std::collections::HashSet<_>>()
                        .len(),
                    records.len()
                );
                assert_eq!(store.library_view_page(query, 0, 2)?.page, 1);
                let last = store.library_view_page(query, usize::MAX, 2)?;
                assert_eq!(last.page, 3);
                assert_eq!(
                    last.items
                        .into_iter()
                        .map(|book| book.id)
                        .collect::<Vec<_>>(),
                    expected[4..].to_vec()
                );
            }
        }
        Ok(())
    }

    #[test]
    fn library_three_state_filters_use_chapters_status_and_real_tracking() -> Result<()> {
        let (store, mut records) = fixture(5)?;
        let statuses = [
            " COMPLETED ".to_owned(),
            "ongoing".to_owned(),
            crate::tr("completed"),
            "finished".to_owned(),
            "paused".to_owned(),
        ];
        let categories = ["planned", "completed", "reading", "completed", "planned"];
        for (index, (book, chapter, _)) in records.iter_mut().enumerate() {
            book.status = statuses[index].clone();
            book.category = categories[index].into();
            book.unread = [0, 3, 2, 0, 0][index];
            rewrite_book(&store, book)?;
            chapter.downloaded = index == 3;
            store.connection.execute(
                "UPDATE chapters SET document = ?2 WHERE id = ?1",
                params![chapter.id, serde_json::to_string(chapter)?],
            )?;
        }
        let mut later_download = records[0].1.clone();
        later_download.id.push_str("-later");
        later_download.remote_id.push_str("-later");
        later_download.number = 2.0;
        later_download.downloaded = true;
        insert_chapter(&store, &later_download)?;

        let query = LibraryQuery {
            sort_key: Some("added-at"),
            ascending: true,
            ..LibraryQuery::default()
        };
        let included = [vec![0, 3], vec![1, 2], vec![], vec![0, 2, 3]];
        let excluded = [
            vec![1, 2, 4],
            vec![0, 3, 4],
            vec![0, 1, 2, 3, 4],
            vec![1, 4],
        ];
        for filter in 0..4 {
            for value in [-1, 0, 1] {
                let mut filters = [0; 4];
                filters[filter] = value;
                let expected_indices = match value {
                    1 => included[filter].clone(),
                    -1 => excluded[filter].clone(),
                    _ => vec![0, 1, 2, 3, 4],
                };
                let expected = expected_indices
                    .into_iter()
                    .map(|index| records[index].0.id.clone())
                    .collect::<Vec<_>>();
                let visible = store.library_view_page(LibraryQuery { filters, ..query }, 1, 8)?;
                assert_eq!(
                    visible.total,
                    expected.len(),
                    "filter {filter}, value {value}"
                );
                assert_eq!(
                    visible
                        .items
                        .into_iter()
                        .map(|book| book.id)
                        .collect::<Vec<_>>(),
                    expected,
                    "filter {filter}, value {value}"
                );
            }
        }
        for (filters, indices) in [
            ([1, -1, 0, 1], vec![0, 3]),
            ([-1, 1, -1, 1], vec![2]),
            ([0, 1, 1, 0], vec![]),
        ] {
            let visible = store.library_view_page(LibraryQuery { filters, ..query }, 1, 8)?;
            let expected = indices
                .into_iter()
                .map(|index| records[index].0.id.clone())
                .collect::<Vec<_>>();
            assert_eq!(visible.total, expected.len());
            assert_eq!(
                visible
                    .items
                    .into_iter()
                    .map(|book| book.id)
                    .collect::<Vec<_>>(),
                expected
            );
        }
        let visible = store.library_view_page(
            LibraryQuery {
                category: 3,
                filters: [0, 0, 0, 1],
                ..query
            },
            1,
            8,
        )?;
        assert_eq!(
            visible
                .items
                .into_iter()
                .map(|book| book.id)
                .collect::<Vec<_>>(),
            vec![records[3].0.id.clone()]
        );
        let legacy = store.library_view_page(
            LibraryQuery {
                sort_key: None,
                downloaded_only: true,
                ..query
            },
            1,
            8,
        )?;
        assert_eq!(
            legacy
                .items
                .into_iter()
                .map(|book| book.id)
                .collect::<Vec<_>>(),
            vec![records[3].0.id.clone(), records[0].0.id.clone()]
        );
        assert_eq!(
            store
                .library_view_page(
                    LibraryQuery {
                        sort_key: None,
                        downloaded_only: true,
                        filters: [-1, 0, 0, 0],
                        ..query
                    },
                    1,
                    8
                )?
                .total,
            0
        );
        let authoritative = store.library_view_page(
            LibraryQuery {
                unread_only: true,
                downloaded_only: true,
                sort_title: true,
                filters: [-1, 0, 0, 0],
                ..query
            },
            1,
            8,
        )?;
        assert_eq!(authoritative.total, 3);
        assert_eq!(
            authoritative
                .items
                .into_iter()
                .map(|book| book.id)
                .collect::<Vec<_>>(),
            vec![
                records[1].0.id.clone(),
                records[2].0.id.clone(),
                records[4].0.id.clone()
            ]
        );
        Ok(())
    }

    #[test]
    fn new_library_sort_queries_decode_only_the_visible_window() -> Result<()> {
        for key in [
            "title",
            "recent-read",
            "recent-open",
            "recent-update",
            "added-at",
            "latest-chapter",
            "unread-count",
            "chapter-count",
        ] {
            let (store, records) = fixture(96)?;
            for (index, (book, _, _)) in records.iter().enumerate() {
                store.record_book_open(&book.id, index as i64)?;
                store.record_library_added(&book.id, index as i64)?;
            }
            let query = LibraryQuery {
                sort_key: Some(key),
                ascending: true,
                ..LibraryQuery::default()
            };
            let expected = store.library_view_page(query, 1, 8)?;
            let visible_ids = expected
                .items
                .iter()
                .map(|book| book.id.as_str())
                .collect::<std::collections::HashSet<_>>();
            for (book, _, _) in &records {
                if !visible_ids.contains(book.id.as_str()) {
                    let scalars = serde_json::json!({"unread": book.unread,
                        "chapter_count": book.chapter_count, "status": book.status});
                    store.connection.execute(
                        "UPDATE books SET document = ?2 WHERE id = ?1",
                        params![book.id, serde_json::to_string(&scalars)?],
                    )?;
                }
            }
            assert!(store.books().is_err(), "{key}");
            let visible = store.library_view_page(query, 1, 8)?;
            assert_eq!(visible.total, expected.total, "{key}");
            assert_eq!(visible.items, expected.items, "{key}");
        }
        Ok(())
    }

    #[test]
    fn detail_chapter_pages_cover_all_chapters_with_device_specific_capacity() -> Result<()> {
        let (store, records) = fixture(2)?;
        let (book, first, _) = &records[0];
        for number in 2..=86 {
            let mut chapter = first.clone();
            chapter.id = format!("{}-chapter-{number:03}", book.id);
            chapter.remote_id = format!("chapter-{number:03}");
            chapter.number = number as f64;
            insert_chapter(&store, &chapter)?;
        }
        store.connection.execute(
            "UPDATE chapters SET document = '{}' WHERE book_id = ?1",
            [&records[1].0.id],
        )?;
        assert!(store.chapters(&records[1].0.id).is_err());
        for (total, paperwhite, scribe) in [
            (0, 1, 1),
            (1, 1, 1),
            (6, 1, 1),
            (7, 2, 1),
            (18, 2, 2),
            (19, 3, 2),
            (86, 8, 6),
        ] {
            assert_eq!(detail_chapter_page_count(total, false), paperwhite);
            assert_eq!(detail_chapter_page_count(total, true), scribe);
        }
        for large in [false, true] {
            let pages = detail_chapter_page_count(86, large);
            for newest_first in [false, true] {
                let mut numbers = Vec::new();
                for page in 1..=pages {
                    let visible =
                        store.detail_chapters_view_page(&book.id, newest_first, 86, page, large)?;
                    let expected_len = if large {
                        if page == pages { 6 } else { 16 }
                    } else if page == 1 {
                        6
                    } else if page == pages {
                        8
                    } else {
                        12
                    };
                    assert_eq!(
                        (visible.total, visible.page, visible.items.len()),
                        (86, page, expected_len)
                    );
                    numbers.extend(
                        visible
                            .items
                            .into_iter()
                            .map(|chapter| chapter.number as u32),
                    );
                }
                let mut expected = (1..=86).collect::<Vec<u32>>();
                if newest_first {
                    expected.reverse();
                }
                assert_eq!(
                    numbers, expected,
                    "large {large}, newest_first {newest_first}"
                );
                assert_eq!(
                    numbers
                        .iter()
                        .collect::<std::collections::HashSet<_>>()
                        .len(),
                    86
                );
                assert_eq!(
                    store
                        .detail_chapters_view_page(&book.id, newest_first, 86, 0, large)?
                        .page,
                    1
                );
                assert_eq!(
                    store
                        .detail_chapters_view_page(&book.id, newest_first, 86, usize::MAX, large)?
                        .page,
                    pages
                );
            }
        }
        store.connection.execute(
            "UPDATE chapters SET document = '{}' WHERE book_id = ?1 AND number = 86",
            [&book.id],
        )?;
        assert!(store.chapters(&book.id).is_err());
        assert_eq!(
            store
                .detail_chapters_view_page(&book.id, false, 86, 1, false)?
                .items
                .len(),
            6
        );
        assert!(
            store
                .detail_chapters_view_page(&book.id, false, 86, 8, false)
                .is_err()
        );
        Ok(())
    }

    #[test]
    fn continuation_selects_the_first_unread_then_falls_back_to_the_first_chapter() -> Result<()> {
        let (store, records) = fixture(1)?;
        let (book, first, _) = &records[0];
        assert!(first.read);
        let mut second = first.clone();
        second.id.push_str("-second");
        second.remote_id.push_str("-second");
        second.number = 2.0;
        second.read = false;
        insert_chapter(&store, &second)?;
        let mut later = second.clone();
        later.id.push_str("-later");
        later.remote_id.push_str("-later");
        later.number = 3.0;
        insert_chapter(&store, &later)?;
        store.connection.execute(
            "UPDATE chapters SET document = '{\"read\":false}' WHERE id = ?1",
            [&later.id],
        )?;
        assert!(store.chapters(&book.id).is_err());
        assert_eq!(store.first_chapter(&book.id)?, Some(first.clone()));
        assert_eq!(store.first_unread_chapter_or_first(&book.id)?, Some(second));
        store.connection.execute("UPDATE chapters SET document = json_set(document, '$.read', json('true')) WHERE book_id = ?1",
            [&book.id])?;
        assert_eq!(
            store.first_unread_chapter_or_first(&book.id)?,
            Some(first.clone())
        );
        let mut empty = book.clone();
        empty.id.push_str("-empty");
        empty.remote_id.push_str("-empty");
        store.upsert_book(&empty)?;
        assert_eq!(store.first_unread_chapter_or_first(&empty.id)?, None);
        Ok(())
    }

    #[test]
    fn visible_queries_do_not_deserialize_unrelated_documents() -> Result<()> {
        let (store, records) = fixture(256)?;
        let expected = store.library_view_page(
            LibraryQuery {
                sort_title: true,
                ..LibraryQuery::default()
            },
            1,
            8,
        )?;
        let visible_ids = expected
            .items
            .iter()
            .map(|book| book.id.as_str())
            .collect::<std::collections::HashSet<_>>();
        for (book, _, _) in &records {
            if !visible_ids.contains(book.id.as_str()) {
                store.connection.execute(
                    "UPDATE books SET document = '{\"unread\":1}' WHERE id = ?1",
                    [&book.id],
                )?;
            }
        }
        assert!(store.books().is_err());
        let visible = store.library_view_page(
            LibraryQuery {
                sort_title: true,
                ..LibraryQuery::default()
            },
            1,
            8,
        )?;
        assert_eq!(visible.items, expected.items);
        assert_eq!(
            store.library_counts()?.total,
            records
                .iter()
                .filter(|(book, _, _)| book.in_library)
                .count()
        );
        for book in &visible.items {
            let metadata = store.book_view_metadata(&book.id)?;
            assert_eq!(metadata.book.as_ref(), Some(book));
            assert!(metadata.progress.is_some());
        }
        Ok(())
    }

    #[test]
    fn history_updates_and_chapter_summaries_only_return_the_requested_window() -> Result<()> {
        let (store, records) = fixture(257)?;
        let history = store.history_view_page(2, 5)?;
        assert_eq!(history.total, records.len());
        assert_eq!(history.items.len(), 5);
        let mut expected_history = records.iter().enumerate().collect::<Vec<_>>();
        expected_history.sort_by(|(left_index, (_, _, left)), (right_index, (_, _, right))| {
            right
                .updated_at
                .cmp(&left.updated_at)
                .then_with(|| right_index.cmp(left_index))
        });
        assert_eq!(
            history
                .items
                .iter()
                .map(|row| row.progress.clone())
                .collect::<Vec<_>>(),
            expected_history
                .iter()
                .skip(5)
                .take(5)
                .map(|(_, (_, _, progress))| progress.clone())
                .collect::<Vec<_>>()
        );
        let mut updates = records
            .iter()
            .filter(|(book, chapter, _)| {
                book.in_library && !chapter.read && chapter.date.starts_with("2026-11-04")
            })
            .collect::<Vec<_>>();
        updates.sort_by(
            |(left_book, left_chapter, _), (right_book, right_chapter, _)| {
                right_chapter
                    .date
                    .cmp(&left_chapter.date)
                    .then_with(|| right_book.updated_at.cmp(&left_book.updated_at))
                    .then_with(|| left_book.id.cmp(&right_book.id))
                    .then_with(|| right_chapter.number.total_cmp(&left_chapter.number))
            },
        );
        assert_eq!(store.daily_update_count("2026-11-04")?, updates.len());
        let visible = store.daily_updates_view_page("2026-11-04", false, false, 2, 5)?;
        assert_eq!(visible.total, updates.len());
        assert_eq!(
            visible
                .items
                .iter()
                .map(|(book, chapter)| (&book.id, &chapter.id))
                .collect::<Vec<_>>(),
            updates
                .iter()
                .skip(5)
                .take(5)
                .map(|(book, chapter, _)| (&book.id, &chapter.id))
                .collect::<Vec<_>>()
        );
        let unique = store.daily_updates_view_page("2026-11-04", false, true, 1, 3)?;
        assert_eq!(unique.items.len(), 3);
        assert_eq!(
            unique
                .items
                .iter()
                .map(|(book, _)| &book.id)
                .collect::<Vec<_>>(),
            updates
                .iter()
                .take(3)
                .map(|(book, _, _)| &book.id)
                .collect::<Vec<_>>()
        );
        let (book, chapter, progress) = &records[1];
        let summary = store.chapter_view_summary(&book.id, Some(&progress.chapter_id))?;
        assert_eq!((summary.total, summary.unread), (1, 0));
        let page = store.chapters_view_page(&book.id, true, summary.total, 99, 6)?;
        assert_eq!(page.items, vec![chapter.clone()]);
        let expected_continuation = expected_history
            .iter()
            .find(|(_, (book, _, _))| book.in_library)
            .map(|(_, (book, _, _))| book.id.clone());
        assert_eq!(store.library_continuation(None)?, expected_continuation);
        store.clear_history()?;
        assert_eq!(
            store.library_continuation(Some(&book.id))?,
            Some(book.id.clone())
        );
        assert_eq!(
            store.library_continuation(Some("missing"))?,
            Some(records[0].0.id.clone())
        );
        Ok(())
    }

    #[test]
    fn update_cards_select_one_latest_chapter_per_book_before_limiting() -> Result<()> {
        let (store, records) = fixture(4)?;
        let mut next = records[1].1.clone();
        next.id.push_str("-next");
        next.remote_id.push_str("-next");
        next.number = 2.0;
        store.connection.execute(
            "INSERT INTO chapters (id,book_id,source_id,remote_id,number,document)
             VALUES (?1,?2,?3,?4,?5,?6)",
            params![
                next.id,
                next.book_id,
                next.source_id,
                next.remote_id,
                next.number,
                serde_json::to_string(&next)?
            ],
        )?;
        assert_eq!(store.daily_update_count("2026-11-04")?, 4);
        let unique = store.daily_updates_view_page("2026-11-04", false, true, 1, 3)?;
        assert_eq!(unique.total, 3);
        assert_eq!(unique.items.len(), 3);
        assert_eq!(unique.items[0].1.id, next.id);
        assert_eq!(unique.items[1].0.id, records[2].0.id);
        assert_eq!(unique.items[2].0.id, records[3].0.id);
        next.date = "2026-1-4".into();
        store.connection.execute(
            "UPDATE chapters SET document = ?1 WHERE id = ?2",
            params![serde_json::to_string(&next)?, next.id],
        )?;
        assert_eq!(store.daily_update_count("2026-01-04")?, 1);
        let unpadded = store.daily_updates_view_page("2026-01-04", false, false, 1, 5)?;
        assert_eq!(unpadded.items.len(), 1);
        assert_eq!(unpadded.items[0].1.id, next.id);
        Ok(())
    }
}
