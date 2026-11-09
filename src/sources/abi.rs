//! Aidoku source wire types. Field order is part of the Postcard ABI.
//!
//! Adapted from Aidoku/aidoku-rs, Copyright (c) 2022-2025 Skittyblock.
//! Distributed under the MIT license in aidoku-rs.LICENSE.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum MangaStatus {
    #[default]
    Unknown,
    Ongoing,
    Completed,
    Cancelled,
    Hiatus,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub enum ContentRating {
    #[default]
    Unknown,
    Safe,
    Suggestive,
    Nsfw,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub enum Viewer {
    #[default]
    Unknown,
    LeftToRight,
    RightToLeft,
    Vertical,
    Webtoon,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub enum UpdateStrategy {
    #[default]
    Always,
    Never,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Manga {
    pub key: String,
    pub title: String,
    pub cover: Option<String>,
    pub artists: Option<Vec<String>>,
    pub authors: Option<Vec<String>>,
    pub description: Option<String>,
    pub url: Option<String>,
    pub tags: Option<Vec<String>>,
    pub status: MangaStatus,
    pub content_rating: ContentRating,
    pub viewer: Viewer,
    pub update_strategy: UpdateStrategy,
    pub next_update_time: Option<i64>,
    pub chapters: Option<Vec<Chapter>>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MangaPageResult {
    pub entries: Vec<Manga>,
    pub has_next_page: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Chapter {
    pub key: String,
    pub title: Option<String>,
    pub chapter_number: Option<f32>,
    pub volume_number: Option<f32>,
    pub date_uploaded: Option<i64>,
    pub scanlators: Option<Vec<String>>,
    pub url: Option<String>,
    pub language: Option<String>,
    pub thumbnail: Option<String>,
    pub locked: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum PageContent {
    Url(String, Option<BTreeMap<String, String>>),
    Text(String),
    Image(i32),
    Zip(String, String),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Page {
    pub content: PageContent,
    pub thumbnail: Option<String>,
    pub has_description: bool,
    pub description: Option<String>,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize)]
pub enum ListingKind {
    #[default]
    Default,
    List,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Listing {
    pub id: String,
    pub name: String,
    pub kind: ListingKind,
}
