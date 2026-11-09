use std::{borrow::Borrow, collections::BTreeMap};

use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};

use crate::{domain::Book, store::Store};

const SETTINGS_KEY: &str = "interaction-v1";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum LibrarySort {
    Title,
    #[default]
    RecentRead,
    RecentOpen,
    RecentUpdate,
    AddedAt,
    LatestChapter,
    UnreadCount,
    ChapterCount,
}

impl LibrarySort {
    pub(crate) const ALL: [Self; 8] = [
        Self::Title,
        Self::RecentRead,
        Self::RecentOpen,
        Self::RecentUpdate,
        Self::AddedAt,
        Self::LatestChapter,
        Self::UnreadCount,
        Self::ChapterCount,
    ];

    pub(crate) const fn id(self) -> &'static str {
        match self {
            Self::Title => "title",
            Self::RecentRead => "recent-read",
            Self::RecentOpen => "recent-open",
            Self::RecentUpdate => "recent-update",
            Self::AddedAt => "added-at",
            Self::LatestChapter => "latest-chapter",
            Self::UnreadCount => "unread-count",
            Self::ChapterCount => "chapter-count",
        }
    }

    pub(crate) fn parse(value: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|sort| sort.id() == value)
    }

    pub(crate) const fn label(self) -> &'static str {
        match self {
            Self::Title => "sort_title",
            Self::RecentRead => "sort_recent",
            Self::RecentOpen => "sort_recent_open",
            Self::RecentUpdate => "sort_recent_update",
            Self::AddedAt => "sort_added_at",
            Self::LatestChapter => "sort_latest_chapter",
            Self::UnreadCount => "sort_unread_count",
            Self::ChapterCount => "sort_chapter_count",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct RepositoryRecord {
    pub name: String,
    pub url: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub(crate) struct CatalogFilterState {
    pub sort: String,
    pub statuses: Vec<String>,
    pub tags: BTreeMap<String, i8>,
}

impl Default for CatalogFilterState {
    fn default() -> Self {
        Self {
            sort: "relevance".into(),
            statuses: Vec::new(),
            tags: BTreeMap::new(),
        }
    }
}

impl CatalogFilterState {
    pub(crate) fn matches(&self, book: &Book) -> bool {
        (self.statuses.is_empty()
            || canonical_status(&book.status)
                .is_some_and(|value| self.statuses.iter().any(|status| status == value)))
            && self.tags.iter().all(|(tag, state)| match state {
                1 => book.tags.contains(tag),
                -1 => !book.tags.contains(tag),
                _ => true,
            })
    }

    pub(crate) fn sort_books<T: Borrow<Book>>(&self, books: &mut [T]) {
        if self.sort == "latest" {
            books.sort_by(|first, second| {
                let first: &Book = first.borrow();
                let second: &Book = second.borrow();
                second
                    .updated_at
                    .cmp(&first.updated_at)
                    .then_with(|| first.id.cmp(&second.id))
            });
        }
    }
}

fn canonical_status(value: &str) -> Option<&'static str> {
    let normalized = value.trim().to_ascii_lowercase();
    match normalized.as_str() {
        "ongoing" | "publishing" | "releasing" => Some("ongoing"),
        "completed" | "complete" | "ended" | "finished" => Some("completed"),
        "hiatus" | "paused" | "on_hold" | "onhold" => Some("hiatus"),
        "cancelled" | "canceled" | "abandoned" | "discontinued" => Some("cancelled"),
        _ if value.trim() == crate::tr("ongoing") => Some("ongoing"),
        _ if value.trim() == crate::tr("completed") => Some("completed"),
        _ if value.trim() == crate::tr("paused") => Some("hiatus"),
        _ if value.trim() == crate::tr("discontinued") => Some("cancelled"),
        _ => None,
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub(crate) struct InteractionState {
    pub library_sort: LibrarySort,
    pub library_ascending: bool,
    /// Downloaded, unread, tracked, completed; -1 excludes, 0 ignores, 1 includes.
    pub library_filters: [i8; 4],
    pub settings_section: String,
    pub reader_toc_tab: String,
    pub show_tap_zones: bool,
    pub repositories: Vec<RepositoryRecord>,
    pub active_repository_url: String,
    pub source_language: String,
    pub page_layout_mode: String,
    pub remember_book_direction: bool,
    pub book_directions: BTreeMap<String, String>,
    pub catalog_filters: BTreeMap<String, CatalogFilterState>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_sort_preserves_provider_order_and_latest_ties_for_owned_and_borrowed_books() {
        let books: Vec<_> = [("old", 2), ("b", 9), ("a", 9), ("new", 12)]
            .into_iter()
            .map(|(id, updated_at)| Book {
                id: id.into(),
                updated_at,
                ..Book::default()
            })
            .collect();
        for (sort, expected) in [
            ("latest", ["new", "a", "b", "old"]),
            ("relevance", ["old", "b", "a", "new"]),
            ("rating", ["old", "b", "a", "new"]),
        ] {
            let filter = CatalogFilterState {
                sort: sort.into(),
                ..Default::default()
            };
            let mut owned = books.clone();
            let mut borrowed: Vec<&Book> = books.iter().collect();
            filter.sort_books(&mut owned);
            filter.sort_books(&mut borrowed);
            assert_eq!(
                owned
                    .iter()
                    .map(|book| book.id.as_str())
                    .collect::<Vec<_>>(),
                expected
            );
            assert_eq!(
                borrowed
                    .iter()
                    .map(|book| book.id.as_str())
                    .collect::<Vec<_>>(),
                expected
            );
        }
    }

    #[test]
    fn catalog_status_filters_accept_provider_aliases_and_localized_records() {
        for (status, aliases) in [
            (
                "completed",
                vec!["completed", " Complete ", "ENDED", "finished"],
            ),
            ("hiatus", vec!["paused", "ON_HOLD", "onhold"]),
            ("cancelled", vec!["canceled", "ABANDONED", "discontinued"]),
        ] {
            let filter = CatalogFilterState {
                statuses: vec![status.into()],
                ..Default::default()
            };
            for alias in aliases {
                assert!(filter.matches(&Book {
                    status: alias.into(),
                    ..Default::default()
                }));
            }
        }
        let filter = CatalogFilterState {
            statuses: vec!["completed".into()],
            ..Default::default()
        };
        assert!(filter.matches(&Book {
            status: crate::tr("completed"),
            ..Default::default()
        }));
        assert!(!filter.matches(&Book {
            status: "ongoing".into(),
            category: "completed".into(),
            ..Default::default()
        }));
    }

    #[test]
    fn catalog_tag_exclusion_composes_with_series_status() {
        let filter = CatalogFilterState {
            statuses: vec!["ongoing".into()],
            tags: BTreeMap::from([("Fantasy".into(), 1), ("Horror".into(), -1)]),
            ..Default::default()
        };
        let mut book = Book {
            status: "ongoing".into(),
            tags: vec!["Fantasy".into()],
            ..Default::default()
        };
        assert!(filter.matches(&book));
        book.tags.push("Horror".into());
        assert!(!filter.matches(&book));
    }
}

impl Default for InteractionState {
    fn default() -> Self {
        Self {
            library_sort: LibrarySort::RecentRead,
            library_ascending: false,
            library_filters: [0; 4],
            settings_section: String::new(),
            reader_toc_tab: "chapters".into(),
            show_tap_zones: false,
            repositories: Vec::new(),
            active_repository_url: String::new(),
            source_language: "all".into(),
            page_layout_mode: "automatic".into(),
            remember_book_direction: true,
            book_directions: BTreeMap::new(),
            catalog_filters: BTreeMap::new(),
        }
    }
}

impl InteractionState {
    pub(crate) fn load(store: &Store) -> Result<Self> {
        let Some(value) = store.read_ui_settings(SETTINGS_KEY)? else {
            let mut settings = Self::default();
            if let Some(session) = store.session()? {
                settings.library_sort = if session.sort_title {
                    LibrarySort::Title
                } else {
                    LibrarySort::RecentRead
                };
                settings.library_filters[0] = i8::from(session.downloaded_only);
                settings.library_filters[1] = i8::from(session.unread_only);
            }
            return Ok(settings);
        };
        let settings: Self = serde_json::from_value(value)?;
        settings.validate()?;
        Ok(settings)
    }

    pub(crate) fn save(&self, store: &Store) -> Result<()> {
        self.validate()?;
        store.write_ui_settings(SETTINGS_KEY, &serde_json::to_value(self)?)
    }

    fn validate(&self) -> Result<()> {
        ensure!(
            self.library_filters
                .iter()
                .all(|value| (-1..=1).contains(value)),
            "Unknown three-state library filter"
        );
        ensure!(
            self.settings_section.len() <= 64,
            "The settings section is too long"
        );
        ensure!(
            matches!(self.reader_toc_tab.as_str(), "chapters" | "bookmarks"),
            "Unknown reader table-of-contents tab"
        );
        ensure!(
            self.repositories.len() <= 128,
            "Too many source repositories"
        );
        ensure!(
            self.repositories
                .iter()
                .all(|repository| repository.name.len() <= 512 && repository.url.len() <= 4096),
            "Source repository metadata exceeds its limit"
        );
        ensure!(
            matches!(
                self.source_language.as_str(),
                "all" | "zh-Hans" | "zh-Hant" | "en" | "ja"
            ),
            "Unknown source language filter"
        );
        ensure!(
            matches!(
                self.page_layout_mode.as_str(),
                "single" | "double" | "automatic"
            ),
            "Unknown reader page layout"
        );
        ensure!(
            self.book_directions.values().all(|direction| matches!(
                direction.as_str(),
                "rtl" | "ltr" | "vertical" | "vertical-pages"
            )),
            "Unknown saved manga reading direction"
        );
        ensure!(
            self.catalog_filters.values().all(|filter| matches!(
                filter.sort.as_str(),
                "relevance" | "latest"
            ) && filter
                .tags
                .values()
                .all(|state| (-1..=1).contains(state))),
            "Unknown source catalog filter"
        );
        Ok(())
    }
}
