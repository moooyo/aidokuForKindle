use std::{cell::RefCell, collections::BTreeMap, io::Read, time::Duration};

use anyhow::{Context, Result, anyhow, bail, ensure};
use reqwest::{
    Method, Url,
    blocking::{Client, RequestBuilder, Response},
};
use serde_json::{Value, json};

use super::{SourcePage, transfer::NetworkControl};
use crate::domain::{Book, Chapter, Page, SourceConfig, compound_id};

const PAGE_SIZE: u32 = 20;
const MAX_JSON_BYTES: u64 = 16 * 1024 * 1024;

struct NetworkSession {
    control: NetworkControl,
    epoch: u64,
}

thread_local! {
    static NETWORK_SESSIONS: RefCell<Vec<NetworkSession>> = const { RefCell::new(Vec::new()) };
}

struct SessionGuard;

impl Drop for SessionGuard {
    fn drop(&mut self) {
        NETWORK_SESSIONS.with(|sessions| {
            sessions.borrow_mut().pop();
        });
    }
}

/// Fences a complete native source operation to its manager's creation epoch.
/// The guard is local to this worker thread and unwinds on errors and panics.
pub(super) fn with_session<T>(
    control: &NetworkControl,
    created_epoch: u64,
    work: impl FnOnce() -> Result<T>,
) -> Result<T> {
    check_session()?;
    control.check_epoch(created_epoch)?;
    NETWORK_SESSIONS.with(|sessions| {
        sessions.borrow_mut().push(NetworkSession {
            control: control.clone(),
            epoch: created_epoch,
        })
    });
    let _session = SessionGuard;
    let result = work();
    // An interruption wins over an error from a connection started in the
    // previous epoch and over successful but now stale source metadata.
    check_session()?;
    result
}

/// Standalone source adapters remain usable without a manager session.
pub(super) fn check_session() -> Result<()> {
    NETWORK_SESSIONS.with(|sessions| {
        for session in sessions.borrow().iter() {
            session.control.check_epoch(session.epoch)?;
        }
        Ok(())
    })
}

pub(super) fn send_checked(request: RequestBuilder) -> Result<Response> {
    check_session()?;
    let result = request.send();
    check_session()?;
    result.context("Send the native source metadata request")
}

/// Retrieve search results without pagination metadata.
pub fn search(client: &Client, config: &SourceConfig, query: &str, page: u32) -> Result<Vec<Book>> {
    Ok(search_page(client, config, query, page)?.books)
}

/// Retrieve search results together with the provider's next-page signal.
pub fn search_page(
    client: &Client,
    config: &SourceConfig,
    query: &str,
    page: u32,
) -> Result<SourcePage> {
    match config.kind.to_ascii_lowercase().as_str() {
        "komga" => komga_list(client, config, query, page, "title"),
        "mangadex" => mangadex_list(client, config, query, page, "popular"),
        "kavita" => kavita_list(client, config, query, page, "title"),
        "suwayomi" => suwayomi_list(client, config, query, page, "popular"),
        kind => bail!("Unsupported built-in source kind: {kind}"),
    }
}

/// Retrieve catalog entries without pagination metadata.
pub fn browse(client: &Client, config: &SourceConfig, page: u32, sort: &str) -> Result<Vec<Book>> {
    Ok(browse_page(client, config, page, sort)?.books)
}

/// Retrieve catalog entries together with the provider's next-page signal.
pub fn browse_page(
    client: &Client,
    config: &SourceConfig,
    page: u32,
    sort: &str,
) -> Result<SourcePage> {
    match config.kind.to_ascii_lowercase().as_str() {
        "komga" => komga_list(client, config, "", page, sort),
        "mangadex" => mangadex_list(client, config, "", page, sort),
        "kavita" => kavita_list(client, config, "", page, sort),
        "suwayomi" => suwayomi_list(client, config, "", page, sort),
        kind => bail!("Unsupported built-in source kind: {kind}"),
    }
}

/// Retrieve the complete chapter list of a catalog entry.
pub fn chapters(client: &Client, config: &SourceConfig, book: &Book) -> Result<Vec<Chapter>> {
    match config.kind.to_ascii_lowercase().as_str() {
        "komga" => komga_chapters(client, config, book),
        "mangadex" => mangadex_chapters(client, config, book),
        "kavita" => kavita_chapters(client, config, book),
        "suwayomi" => suwayomi_chapters(client, config, book),
        kind => bail!("Unsupported built-in source kind: {kind}"),
    }
}

/// Resolve page URLs and the credentials required to fetch each image.
pub fn pages(
    client: &Client,
    config: &SourceConfig,
    book: &Book,
    chapter: &Chapter,
) -> Result<Vec<Page>> {
    match config.kind.to_ascii_lowercase().as_str() {
        "komga" => komga_pages(client, config, chapter),
        "mangadex" => mangadex_pages(client, config, chapter),
        "kavita" => kavita_pages(client, config, chapter),
        "suwayomi" => suwayomi_pages(client, config, book, chapter),
        kind => bail!("Unsupported built-in source kind: {kind}"),
    }
}

/// Retrieve metadata that Kavita omits from catalog and search entries.
pub fn details(client: &Client, config: &SourceConfig, book: &Book) -> Result<Book> {
    if !config.kind.eq_ignore_ascii_case("kavita") {
        return Ok(book.clone());
    }
    let session = kavita_session(client, config)?;
    let mut url = endpoint(config, &["api", "Series", "metadata"])?;
    url.query_pairs_mut()
        .append_pair("seriesId", &book.remote_id);
    let result = receive(
        send_checked(request(
            client,
            config,
            Method::GET,
            url,
            Some(&session.token),
        ))?,
        "Kavita",
    )?;
    ensure!(
        result.is_object(),
        "Kavita: incompatible series metadata response"
    );
    let mut book = book.clone();
    book.description = text(&result["summary"]);
    book.author = result["writers"]
        .as_array()
        .map(|writers| {
            writers
                .iter()
                .map(|writer| text(&writer["name"]))
                .filter(|writer| !writer.is_empty())
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();
    book.tags = ["genres", "tags"]
        .into_iter()
        .flat_map(|field| result[field].as_array().into_iter().flatten())
        .map(|tag| text(&tag["title"]))
        .filter(|tag| !tag.is_empty())
        .collect();
    book.status = text(&result["publicationStatus"]);
    Ok(book)
}

/// Supply authentication for protected cover images without embedding secrets in URLs.
pub fn image_headers(client: &Client, config: &SourceConfig) -> Result<BTreeMap<String, String>> {
    if config.kind.eq_ignore_ascii_case("mangadex") {
        return Ok([
            ("Accept".into(), "image/*".into()),
            ("User-Agent".into(), "AidokuForKindle/0.1".into()),
        ]
        .into());
    }
    let token = if config.kind.eq_ignore_ascii_case("kavita") {
        Some(kavita_token(client, config)?)
    } else if config.kind.eq_ignore_ascii_case("suwayomi") {
        suwayomi_token(client, config)?
    } else {
        None
    };
    request_headers(client, config, token.as_deref())
}

/// Keep server credentials on the configured origin when fetching cover images.
pub fn image_headers_for(
    client: &Client,
    config: &SourceConfig,
    url: &str,
) -> Result<BTreeMap<String, String>> {
    let target = Url::parse(url).context("Invalid source image URL")?;
    ensure!(
        matches!(target.scheme(), "http" | "https"),
        "Source images must use HTTP or HTTPS"
    );
    let same_origin = target.origin() == endpoint(config, &[])?.origin();
    let mut headers = image_headers(client, config)?;
    if !same_origin {
        headers.retain(|name, _| {
            !matches!(
                name.to_ascii_lowercase().as_str(),
                "authorization" | "proxy-authorization" | "x-api-key" | "cookie"
            )
        });
    }
    Ok(headers)
}

fn endpoint(config: &SourceConfig, segments: &[&str]) -> Result<Url> {
    let base = if config.base_url.is_empty() && config.kind.eq_ignore_ascii_case("mangadex") {
        "https://api.mangadex.org"
    } else {
        config.base_url.as_str()
    };
    ensure!(
        !base.is_empty(),
        "Configure the server URL for {}",
        config.name
    );
    let mut url = Url::parse(base).context("Invalid source server URL")?;
    ensure!(
        matches!(url.scheme(), "http" | "https"),
        "Source servers must use HTTP or HTTPS"
    );
    ensure!(
        url.username().is_empty() && url.password().is_none(),
        "Use the source credential fields instead of URL credentials"
    );
    url.set_query(None);
    url.set_fragment(None);
    {
        let mut path = url
            .path_segments_mut()
            .map_err(|_| anyhow!("The server URL cannot contain path segments"))?;
        path.pop_if_empty();
        for segment in segments {
            path.push(segment);
        }
    }
    Ok(url)
}

fn request(
    client: &Client,
    config: &SourceConfig,
    method: Method,
    url: Url,
    bearer: Option<&str>,
) -> RequestBuilder {
    let mut builder = client
        .request(method, url)
        .timeout(Duration::from_secs(30))
        .header("Accept", "application/json")
        .header("User-Agent", "AidokuForKindle/0.1");
    if let Some(token) = bearer.filter(|token| !token.is_empty()) {
        builder = builder.bearer_auth(token);
    } else if !config.token.is_empty() {
        builder = if config.kind.eq_ignore_ascii_case("komga") {
            builder.header("X-API-Key", &config.token)
        } else {
            builder.bearer_auth(&config.token)
        };
    } else if !config.username.is_empty() {
        builder = builder.basic_auth(&config.username, Some(&config.password));
    }
    builder
}

fn request_headers(
    client: &Client,
    config: &SourceConfig,
    bearer: Option<&str>,
) -> Result<BTreeMap<String, String>> {
    let request = request(client, config, Method::GET, endpoint(config, &[])?, bearer).build()?;
    let mut headers = BTreeMap::new();
    for (name, value) in request.headers() {
        if name != "accept" {
            headers.insert(name.as_str().into(), value.to_str()?.into());
        }
    }
    headers.insert("Accept".into(), "image/*".into());
    Ok(headers)
}

fn receive(mut response: Response, provider: &str) -> Result<Value> {
    check_session()?;
    let result = (|| {
        let status = response.status();
        if status.as_u16() == 401 {
            bail!(
                "{provider}: authentication failed; check the source username, password, or token"
            );
        }
        if status.as_u16() == 403 {
            bail!(
                "{provider}: access denied; this source may require browser verification, which is not supported on Kindle"
            );
        }
        if status.as_u16() == 429 {
            bail!("{provider}: too many requests; wait before retrying");
        }
        ensure!(
            status.is_success(),
            "{provider}: server returned HTTP {}",
            status.as_u16()
        );
        if let Some(size) = response.content_length() {
            ensure!(
                size <= MAX_JSON_BYTES,
                "{provider}: server response is too large"
            );
        }
        check_session()?;
        let mut bytes = Vec::new();
        super::http_body::with_context(|| {
            (&mut response)
                .take(MAX_JSON_BYTES + 1)
                .read_to_end(&mut bytes)
        })??;
        check_session()?;
        ensure!(
            bytes.len() as u64 <= MAX_JSON_BYTES,
            "{provider}: server response is too large"
        );
        if bytes
            .iter()
            .copied()
            .find(|byte| !byte.is_ascii_whitespace())
            == Some(b'<')
        {
            bail!(
                "{provider}: the server returned a web page instead of API data; browser verification is not supported on Kindle"
            );
        }
        serde_json::from_slice(&bytes)
            .with_context(|| format!("{provider}: incompatible API response"))
    })();
    check_session()?;
    result
}

fn array<'a>(value: &'a Value, field: Option<&str>, provider: &str) -> Result<&'a [Value]> {
    let data = field.map_or(value, |field| &value[field]);
    data.as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| anyhow!("{provider}: incompatible API response (expected a result array)"))
}

fn text(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        _ => String::new(),
    }
}

fn required_id(value: &Value, provider: &str) -> Result<String> {
    let id = text(value);
    ensure!(!id.is_empty(), "{provider}: an entry has no identifier");
    Ok(id)
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .map(text)
                .filter(|text| !text.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

fn option_string<'a>(config: &'a SourceConfig, name: &str) -> Option<&'a str> {
    config
        .options
        .get(name)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn offset(page: u32, size: u32) -> u32 {
    page.max(1).saturating_sub(1).saturating_mul(size)
}

fn has_next_page(metadata: &Value, page: u32, count: usize) -> bool {
    if count == 0 {
        return false;
    }
    for candidate in [metadata, &metadata["pageInfo"], &metadata["page"]] {
        if let Some(value) = candidate["hasNextPage"]
            .as_bool()
            .or_else(|| candidate["hasNext"].as_bool())
        {
            return value;
        }
        if let Some(last) = candidate["last"].as_bool() {
            return !last;
        }
    }
    let pagination = metadata
        .get("page")
        .filter(|page| page.is_object())
        .unwrap_or(metadata);
    let current_page = pagination["currentPage"]
        .as_u64()
        .or_else(|| {
            pagination["number"]
                .as_u64()
                .map(|number| number.saturating_add(1))
        })
        .unwrap_or(u64::from(page.max(1)));
    if let Some(total_pages) = pagination["totalPages"].as_u64() {
        return current_page < total_pages;
    }
    let total = ["total", "totalItems", "totalElements", "totalCount"]
        .into_iter()
        .find_map(|field| pagination[field].as_u64());
    if let Some(total) = total {
        let size = pagination["limit"]
            .as_u64()
            .or_else(|| pagination["size"].as_u64())
            .or_else(|| pagination["itemsPerPage"].as_u64())
            .filter(|size| *size > 0)
            .unwrap_or(u64::from(PAGE_SIZE));
        let start = pagination["offset"]
            .as_u64()
            .unwrap_or_else(|| current_page.saturating_sub(1).saturating_mul(size));
        return start.saturating_add(size) < total;
    }
    count == PAGE_SIZE as usize
}

fn response_pagination(response: &Response) -> Option<Value> {
    ["Pagination", "X-Pagination"].into_iter().find_map(|name| {
        response
            .headers()
            .get(name)
            .and_then(|header| serde_json::from_slice(header.as_bytes()).ok())
    })
}

fn chapter_record(config: &SourceConfig, book: &Book, remote_id: String) -> Chapter {
    Chapter {
        id: compound_id(&book.id, &remote_id),
        book_id: book.id.clone(),
        source_id: config.id.clone(),
        remote_id,
        ..Chapter::default()
    }
}

fn komga_list(
    client: &Client,
    config: &SourceConfig,
    query: &str,
    page: u32,
    sort: &str,
) -> Result<SourcePage> {
    let sorting = match sort {
        "latest" | "updated" => "lastModifiedDate,desc",
        "new" | "recent" => "createdDate,desc",
        "popular" => "booksCount,desc",
        _ => "metadata.titleSort,asc",
    };
    let mut url = endpoint(config, &["api", "v1", "series", "list"])?;
    url.query_pairs_mut()
        .append_pair("page", &page.max(1).saturating_sub(1).to_string())
        .append_pair("size", &PAGE_SIZE.to_string())
        .append_pair("sort", sorting);
    let mut conditions = vec![json!({"deleted":{"operator":"isFalse"}})];
    if let Some(library) = option_string(config, "library_id") {
        conditions.push(json!({"libraryId":{"operator":"is","value":library}}));
    }
    let body = json!({"condition":{"allOf":conditions}, "fullTextSearch":if query.is_empty() { Value::Null } else { json!(query) }});
    let result = receive(
        send_checked(request(client, config, Method::POST, url, None).json(&body))?,
        "Komga",
    )?;
    let books = array(&result, Some("content"), "Komga")?
        .iter()
        .map(|series| {
            let remote_id = required_id(&series["id"], "Komga")?;
            let metadata = &series["metadata"];
            let author = series["booksMetadata"]["authors"]
                .as_array()
                .map(|authors| {
                    authors
                        .iter()
                        .filter(|author| {
                            author["role"].as_str().is_none_or(|role| role == "writer")
                        })
                        .map(|author| text(&author["name"]))
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .unwrap_or_default();
            let mut tags = strings(&metadata["genres"]);
            tags.extend(strings(&metadata["tags"]));
            tags.sort();
            tags.dedup();
            Ok(Book {
                id: compound_id(&config.id, &remote_id),
                source_id: config.id.clone(),
                title: metadata["title"]
                    .as_str()
                    .unwrap_or(series["name"].as_str().unwrap_or("Untitled"))
                    .into(),
                author,
                description: text(&metadata["summary"]),
                tags,
                status: text(&metadata["status"]),
                cover: endpoint(config, &["api", "v1", "series", &remote_id, "thumbnail"])?.into(),
                chapter_count: series["booksCount"].as_u64().unwrap_or(0) as u32,
                remote_id,
                ..Book::default()
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(SourcePage {
        has_next: has_next_page(&result, page, books.len()),
        books,
    })
}

fn komga_chapters(client: &Client, config: &SourceConfig, book: &Book) -> Result<Vec<Chapter>> {
    let mut url = endpoint(config, &["api", "v1", "books", "list"])?;
    url.query_pairs_mut()
        .append_pair("unpaged", "true")
        .append_pair("sort", "metadata.numberSort,asc");
    let body = json!({"condition":{"allOf":[{"seriesId":{"operator":"is","value":book.remote_id}}, {"deleted":{"operator":"isFalse"}}]}});
    let result = receive(
        send_checked(request(client, config, Method::POST, url, None).json(&body))?,
        "Komga",
    )?;
    array(&result, Some("content"), "Komga")?
        .iter()
        .filter(|book| {
            book["media"]["mediaProfile"].as_str() != Some("EPUB")
                || book["media"]["epubDivinaCompatible"].as_bool() == Some(true)
        })
        .map(|item| {
            let mut chapter = chapter_record(config, book, required_id(&item["id"], "Komga")?);
            chapter.number = item["metadata"]["numberSort"].as_f64().unwrap_or(0.0);
            chapter.title = item["metadata"]["title"]
                .as_str()
                .unwrap_or(item["name"].as_str().unwrap_or("Chapter"))
                .into();
            chapter.date = item["metadata"]["releaseDate"]
                .as_str()
                .unwrap_or(item["createdDate"].as_str().unwrap_or(""))
                .into();
            chapter.page_count = item["media"]["pagesCount"].as_u64().unwrap_or(0) as u32;
            chapter.read = item["readProgress"]["completed"].as_bool().unwrap_or(false);
            Ok(chapter)
        })
        .collect()
}

fn komga_pages(client: &Client, config: &SourceConfig, chapter: &Chapter) -> Result<Vec<Page>> {
    let url = endpoint(config, &["api", "v1", "books", &chapter.remote_id, "pages"])?;
    let result = receive(
        send_checked(request(client, config, Method::GET, url, None))?,
        "Komga",
    )?;
    let headers = request_headers(client, config, None)?;
    array(&result, None, "Komga")?
        .iter()
        .map(|page| {
            let number = required_id(&page["number"], "Komga")?;
            let mut url = endpoint(
                config,
                &["api", "v1", "books", &chapter.remote_id, "pages", &number],
            )?;
            if !matches!(
                page["mediaType"].as_str(),
                Some("image/jpeg" | "image/png" | "image/webp" | "image/gif")
            ) {
                url.query_pairs_mut().append_pair("convert", "png");
            }
            Ok(Page {
                url: url.into(),
                headers: headers.clone(),
                local_path: None,
            })
        })
        .collect()
}

fn mangadex_languages(config: &SourceConfig) -> Vec<String> {
    if let Some(languages) = config
        .options
        .get("chapter_languages")
        .and_then(Value::as_array)
    {
        languages
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_owned)
            .collect()
    } else {
        config
            .languages
            .iter()
            .filter(|language| language.as_str() != "multi")
            .cloned()
            .collect()
    }
}

fn localized(value: &Value, config: &SourceConfig) -> String {
    for language in config
        .languages
        .iter()
        .map(String::as_str)
        .chain(["zh-hans", "zh", "en", "ja-ro"])
    {
        if let Some(value) = value
            .get(language)
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            return value.into();
        }
    }
    value
        .as_object()
        .and_then(|object| object.values().find_map(Value::as_str))
        .unwrap_or("")
        .into()
}

fn mangadex_list(
    client: &Client,
    config: &SourceConfig,
    query: &str,
    page: u32,
    sort: &str,
) -> Result<SourcePage> {
    ensure!(
        offset(page, PAGE_SIZE) < 10_000,
        "MangaDex: the API result limit has been reached; narrow the search"
    );
    let mut url = endpoint(config, &["manga"])?;
    {
        let mut params = url.query_pairs_mut();
        params
            .append_pair("limit", &PAGE_SIZE.to_string())
            .append_pair("offset", &offset(page, PAGE_SIZE).to_string())
            .append_pair("includes[]", "cover_art")
            .append_pair("includes[]", "author")
            .append_pair("includes[]", "artist")
            .append_pair("contentRating[]", "safe")
            .append_pair("contentRating[]", "suggestive");
        if !query.is_empty() {
            params.append_pair("title", query);
        }
        match sort {
            "latest" | "updated" => {
                params.append_pair("order[updatedAt]", "desc");
            }
            "new" | "recent" => {
                params.append_pair("order[createdAt]", "desc");
            }
            "title" | "alphabetical" => {
                params.append_pair("order[title]", "asc");
            }
            _ => {
                params.append_pair("order[followedCount]", "desc");
            }
        }
    }
    let result = receive(
        send_checked(request(client, config, Method::GET, url, None))?,
        "MangaDex",
    )?;
    let books = array(&result, Some("data"), "MangaDex")?
        .iter()
        .map(|entry| {
            let remote_id = required_id(&entry["id"], "MangaDex")?;
            let attributes = &entry["attributes"];
            let relationships = entry["relationships"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or_default();
            let author = relationships
                .iter()
                .filter(|relation| relation["type"].as_str() == Some("author"))
                .map(|relation| text(&relation["attributes"]["name"]))
                .filter(|name| !name.is_empty())
                .collect::<Vec<_>>()
                .join(", ");
            let cover_file = relationships
                .iter()
                .find(|relation| relation["type"].as_str() == Some("cover_art"))
                .map(|relation| text(&relation["attributes"]["fileName"]))
                .unwrap_or_default();
            let cover = if cover_file.is_empty() {
                String::new()
            } else {
                let mut uploads = Url::parse(
                    option_string(config, "uploads_url").unwrap_or("https://uploads.mangadex.org"),
                )?;
                uploads
                    .path_segments_mut()
                    .map_err(|_| anyhow!("Invalid MangaDex uploads URL"))?
                    .pop_if_empty()
                    .push("covers")
                    .push(&remote_id)
                    .push(&format!("{cover_file}.256.jpg"));
                uploads.into()
            };
            let tags = attributes["tags"]
                .as_array()
                .map(|tags| {
                    tags.iter()
                        .map(|tag| localized(&tag["attributes"]["name"], config))
                        .filter(|tag| !tag.is_empty())
                        .collect()
                })
                .unwrap_or_default();
            Ok(Book {
                id: compound_id(&config.id, &remote_id),
                source_id: config.id.clone(),
                remote_id,
                title: localized(&attributes["title"], config),
                author,
                description: localized(&attributes["description"], config),
                tags,
                status: text(&attributes["status"]),
                cover,
                ..Book::default()
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let has_next = has_next_page(&result, page, books.len())
        && offset(page, PAGE_SIZE).saturating_add(PAGE_SIZE) < 10_000;
    Ok(SourcePage { books, has_next })
}

fn mangadex_chapters(client: &Client, config: &SourceConfig, book: &Book) -> Result<Vec<Chapter>> {
    let mut chapters = Vec::new();
    let mut start = 0;
    loop {
        let mut url = endpoint(config, &["manga", &book.remote_id, "feed"])?;
        {
            let mut params = url.query_pairs_mut();
            params
                .append_pair("limit", "500")
                .append_pair("offset", &start.to_string())
                .append_pair("order[chapter]", "asc")
                .append_pair("includeFutureUpdates", "0")
                .append_pair("includeEmptyPages", "0");
            for language in mangadex_languages(config) {
                params.append_pair("translatedLanguage[]", &language);
            }
        }
        let result = receive(
            send_checked(request(client, config, Method::GET, url, None))?,
            "MangaDex",
        )?;
        let entries = array(&result, Some("data"), "MangaDex")?;
        for entry in entries {
            let mut chapter = chapter_record(config, book, required_id(&entry["id"], "MangaDex")?);
            let attributes = &entry["attributes"];
            chapter.number = attributes["chapter"]
                .as_str()
                .and_then(|number| number.parse().ok())
                .unwrap_or(0.0);
            chapter.title = text(&attributes["title"]);
            if chapter.title.is_empty() {
                chapter.title = format!("Chapter {}", text(&attributes["chapter"]));
            }
            chapter.date = text(&attributes["publishAt"]);
            chapter.page_count = attributes["pages"].as_u64().unwrap_or(0) as u32;
            chapters.push(chapter);
        }
        start += entries.len() as u32;
        let total = result["total"].as_u64().unwrap_or(start as u64);
        if entries.len() < 500 || start as u64 >= total {
            break;
        }
        ensure!(
            start < 10_000,
            "MangaDex: more than 10,000 chapters; select fewer chapter languages"
        );
    }
    Ok(chapters)
}

fn mangadex_pages(client: &Client, config: &SourceConfig, chapter: &Chapter) -> Result<Vec<Page>> {
    let url = endpoint(config, &["at-home", "server", &chapter.remote_id])?;
    let result = receive(
        send_checked(request(client, config, Method::GET, url, None))?,
        "MangaDex",
    )?;
    let base = result["baseUrl"]
        .as_str()
        .context("MangaDex: chapter has no image server; it may be an external chapter")?;
    let hash = result["chapter"]["hash"]
        .as_str()
        .context("MangaDex: chapter has no page hash")?;
    let saver = config
        .options
        .get("data_saver")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let field = if saver { "dataSaver" } else { "data" };
    let files = array(&result["chapter"], Some(field), "MangaDex")?;
    ensure!(
        !files.is_empty(),
        "MangaDex: the chapter has no readable pages"
    );
    files
        .iter()
        .map(|file| {
            let file = file.as_str().context("MangaDex: invalid image filename")?;
            let mut url = Url::parse(base).context("MangaDex: invalid image server URL")?;
            url.path_segments_mut()
                .map_err(|_| anyhow!("MangaDex: invalid image server URL"))?
                .pop_if_empty()
                .push(if saver { "data-saver" } else { "data" })
                .push(hash)
                .push(file);
            let mut headers = BTreeMap::new();
            headers.insert("User-Agent".into(), "AidokuForKindle/0.1".into());
            Ok(Page {
                url: url.into(),
                headers,
                local_path: None,
            })
        })
        .collect()
}

struct KavitaSession {
    token: String,
    api_key: String,
}

fn kavita_session(client: &Client, config: &SourceConfig) -> Result<KavitaSession> {
    let api_key = option_string(config, "api_key").unwrap_or("");
    if !config.token.is_empty() {
        return Ok(KavitaSession {
            token: config.token.clone(),
            api_key: api_key.into(),
        });
    }
    ensure!(
        !config.username.is_empty() || !api_key.is_empty(),
        "Kavita: configure a username and password, bearer token, or API key"
    );
    let body = if !config.username.is_empty() {
        json!({"username":config.username,"password":config.password})
    } else {
        json!({"apiKey":api_key})
    };
    let result = receive(
        send_checked(
            client
                .post(endpoint(config, &["api", "Account", "login"])?)
                .timeout(Duration::from_secs(30))
                .json(&body),
        )?,
        "Kavita",
    )?;
    let token = result["token"]
        .as_str()
        .filter(|token| !token.is_empty())
        .context("Kavita: login did not return a bearer token")?
        .into();
    let api_key = result["apiKey"]
        .as_str()
        .filter(|key| !key.is_empty())
        .unwrap_or(api_key)
        .into();
    Ok(KavitaSession { token, api_key })
}

fn kavita_token(client: &Client, config: &SourceConfig) -> Result<String> {
    Ok(kavita_session(client, config)?.token)
}

fn kavita_list(
    client: &Client,
    config: &SourceConfig,
    query: &str,
    page: u32,
    sort: &str,
) -> Result<SourcePage> {
    let session = kavita_session(client, config)?;
    let mut url = endpoint(config, &["api", "Series", "all-v2"])?;
    url.query_pairs_mut()
        .append_pair("pageNumber", &page.max(1).to_string())
        .append_pair("pageSize", &PAGE_SIZE.to_string());
    let sort_field = match sort {
        "new" | "recent" => 2,
        "updated" => 3,
        "latest" => 4,
        _ => 1,
    };
    // The global search endpoint is capped at fifteen matches and cannot page.
    // A name filter on all-v2 returns real pages and their Pagination header.
    let statements = if query.is_empty() {
        Vec::new()
    } else {
        vec![json!({"comparison":7,"field":1,"value":query})]
    };
    let body = json!({"statements":statements,"combination":1,"sortOptions":{"sortField":sort_field,"isAscending":sort_field==1}});
    let response =
        send_checked(request(client, config, Method::POST, url, Some(&session.token)).json(&body))?;
    let pagination = response_pagination(&response);
    let result = receive(response, "Kavita")?;
    let books = array(&result, None, "Kavita")?
        .iter()
        .map(|series| {
            let remote_id = required_id(&series["id"], "Kavita")?;
            let mut cover = endpoint(config, &["api", "Image", "series-cover"])?;
            cover.query_pairs_mut().append_pair("seriesId", &remote_id);
            // A cover URL remains usable after the short-lived login token expires.
            if !session.api_key.is_empty() {
                cover
                    .query_pairs_mut()
                    .append_pair("apiKey", &session.api_key);
            }
            let title = ["localizedName", "name", "originalName"]
                .into_iter()
                .find_map(|key| series[key].as_str().filter(|title| !title.is_empty()))
                .unwrap_or("Untitled");
            Ok(Book {
                id: compound_id(&config.id, &remote_id),
                source_id: config.id.clone(),
                remote_id,
                title: title.into(),
                description: text(&series["summary"]),
                cover: cover.into(),
                chapter_count: series["chapterCount"].as_u64().unwrap_or(0) as u32,
                status: text(&series["publicationStatus"]),
                ..Book::default()
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(SourcePage {
        has_next: has_next_page(pagination.as_ref().unwrap_or(&result), page, books.len()),
        books,
    })
}

fn kavita_chapters(client: &Client, config: &SourceConfig, book: &Book) -> Result<Vec<Chapter>> {
    let session = kavita_session(client, config)?;
    let mut url = endpoint(config, &["api", "Search", "chapters-by-series"])?;
    url.query_pairs_mut()
        .append_pair("seriesId", &book.remote_id);
    let result = receive(
        send_checked(request(
            client,
            config,
            Method::GET,
            url,
            Some(&session.token),
        ))?,
        "Kavita",
    )?;
    let mut chapters: Vec<Chapter> = array(&result, None, "Kavita")?
        .iter()
        .map(|entry| {
            let mut chapter = chapter_record(config, book, required_id(&entry["id"], "Kavita")?);
            chapter.number = entry["minNumber"]
                .as_f64()
                .or_else(|| {
                    entry["number"]
                        .as_str()
                        .and_then(|number| number.parse().ok())
                })
                .unwrap_or(0.0);
            chapter.title = ["titleName", "title", "number"]
                .into_iter()
                .find_map(|key| entry[key].as_str().filter(|title| !title.is_empty()))
                .unwrap_or("Chapter")
                .into();
            chapter.date = entry["releaseDate"]
                .as_str()
                .unwrap_or(entry["createdUtc"].as_str().unwrap_or(""))
                .into();
            chapter.page_count = entry["pages"].as_u64().unwrap_or(0) as u32;
            chapter.read = chapter.page_count > 0
                && entry["pagesRead"].as_u64().unwrap_or(0) >= chapter.page_count as u64;
            Ok(chapter)
        })
        .collect::<Result<_>>()?;
    chapters.sort_by(|left, right| left.number.total_cmp(&right.number));
    Ok(chapters)
}

fn kavita_pages(client: &Client, config: &SourceConfig, chapter: &Chapter) -> Result<Vec<Page>> {
    let session = kavita_session(client, config)?;
    let mut info_url = endpoint(config, &["api", "Reader", "chapter-info"])?;
    info_url
        .query_pairs_mut()
        .append_pair("chapterId", &chapter.remote_id)
        .append_pair("extractPdf", "true");
    let result = receive(
        send_checked(request(
            client,
            config,
            Method::GET,
            info_url,
            Some(&session.token),
        ))?,
        "Kavita",
    )?;
    let count = result["pages"]
        .as_u64()
        .unwrap_or(chapter.page_count as u64);
    ensure!(
        count > 0 && count <= 100_000,
        "Kavita: the chapter has no readable pages or its page count is invalid"
    );
    let headers = request_headers(client, config, Some(&session.token))?;
    (0..count)
        .map(|page| {
            let mut url = endpoint(config, &["api", "Reader", "image"])?;
            url.query_pairs_mut()
                .append_pair("chapterId", &chapter.remote_id)
                .append_pair("page", &page.to_string())
                .append_pair("apiKey", &session.api_key)
                .append_pair("extractPdf", "true");
            Ok(Page {
                url: url.into(),
                headers: headers.clone(),
                local_path: None,
            })
        })
        .collect()
}

fn graphql(
    client: &Client,
    config: &SourceConfig,
    query: &str,
    variables: Value,
    token: Option<&str>,
) -> Result<Value> {
    let result = receive(
        send_checked(
            request(
                client,
                config,
                Method::POST,
                endpoint(config, &["api", "graphql"])?,
                token,
            )
            .json(&json!({"query":query,"variables":variables})),
        )?,
        "Suwayomi",
    )?;
    if let Some(errors) = result["errors"]
        .as_array()
        .filter(|errors| !errors.is_empty())
    {
        let messages = errors
            .iter()
            .filter_map(|error| error["message"].as_str())
            .collect::<Vec<_>>()
            .join("; ");
        bail!(
            "Suwayomi: {}",
            if messages.is_empty() {
                "GraphQL request failed"
            } else {
                &messages
            }
        );
    }
    ensure!(
        result.get("data").is_some_and(|data| !data.is_null()),
        "Suwayomi: GraphQL response has no data"
    );
    Ok(result["data"].clone())
}

fn suwayomi_token(client: &Client, config: &SourceConfig) -> Result<Option<String>> {
    if !config.token.is_empty() {
        return Ok(Some(config.token.clone()));
    }
    if config.username.is_empty()
        || matches!(
            option_string(config, "auth_mode"),
            Some("basic" | "basic_auth")
        )
    {
        return Ok(None);
    }
    let result = graphql(
        client,
        config,
        "mutation($input: LoginInput!) { login(input: $input) { accessToken } }",
        json!({"input":{"username":config.username,"password":config.password}}),
        None,
    )?;
    let token = result["login"]["accessToken"]
        .as_str()
        .filter(|token| !token.is_empty())
        .context("Suwayomi: login did not return an access token")?;
    Ok(Some(token.into()))
}

fn suwayomi_source(config: &SourceConfig) -> Result<String> {
    let source = config
        .options
        .get("source_id")
        .map(text)
        .unwrap_or_default();
    ensure!(
        !source.is_empty() && source.parse::<i64>().is_ok(),
        "Suwayomi: configure source_id with the identifier of an installed server extension"
    );
    Ok(source)
}

fn relative_image(config: &SourceConfig, value: &str) -> Result<String> {
    if let Ok(url) = Url::parse(value) {
        ensure!(
            matches!(url.scheme(), "http" | "https"),
            "The image server must use HTTP or HTTPS"
        );
        return Ok(url.into());
    }
    let mut base = endpoint(config, &[])?;
    if !base.path().ends_with('/') {
        base.set_path(&format!("{}/", base.path()));
    }
    Ok(base.join(value.trim_start_matches('/'))?.into())
}

fn suwayomi_list(
    client: &Client,
    config: &SourceConfig,
    query: &str,
    page: u32,
    sort: &str,
) -> Result<SourcePage> {
    let token = suwayomi_token(client, config)?;
    let source = suwayomi_source(config)?;
    let kind = if !query.is_empty() {
        "SEARCH"
    } else if matches!(sort, "latest" | "updated" | "new" | "recent") {
        "LATEST"
    } else {
        "POPULAR"
    };
    let result = graphql(
        client,
        config,
        "mutation($input: FetchSourceMangaInput!) { fetchSourceManga(input: $input) { mangas { id title author description genre status thumbnailUrl } hasNextPage } }",
        json!({"input":{"source":source,"type":kind,"page":page.max(1),"query":query}}),
        token.as_deref(),
    )?;
    let books = array(&result["fetchSourceManga"], Some("mangas"), "Suwayomi")?
        .iter()
        .map(|entry| {
            let remote_id = required_id(&entry["id"], "Suwayomi")?;
            let thumbnail = text(&entry["thumbnailUrl"]);
            let tags = if entry["genre"].is_array() {
                strings(&entry["genre"])
            } else {
                entry["genre"]
                    .as_str()
                    .unwrap_or("")
                    .split(',')
                    .map(str::trim)
                    .filter(|tag| !tag.is_empty())
                    .map(str::to_owned)
                    .collect()
            };
            Ok(Book {
                id: compound_id(&config.id, &remote_id),
                source_id: config.id.clone(),
                remote_id,
                title: text(&entry["title"]),
                author: text(&entry["author"]),
                description: text(&entry["description"]),
                tags,
                status: text(&entry["status"]),
                cover: if thumbnail.is_empty() {
                    String::new()
                } else {
                    relative_image(config, &thumbnail)?
                },
                ..Book::default()
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(SourcePage {
        has_next: has_next_page(&result["fetchSourceManga"], page, books.len()),
        books,
    })
}

fn suwayomi_chapters(client: &Client, config: &SourceConfig, book: &Book) -> Result<Vec<Chapter>> {
    let token = suwayomi_token(client, config)?;
    let id: i32 = book
        .remote_id
        .parse()
        .context("Suwayomi: invalid manga identifier")?;
    let result = graphql(
        client,
        config,
        "mutation($input: FetchMangaAndChaptersInput!) { fetchMangaAndChapters(input: $input) { chapters { id name chapterNumber uploadDate pageCount sourceOrder } } }",
        json!({"input":{"id":id,"fetchManga":true,"fetchChapters":true}}),
        token.as_deref(),
    )?;
    let mut chapters = array(
        &result["fetchMangaAndChapters"],
        Some("chapters"),
        "Suwayomi",
    )?
    .iter()
    .map(|entry| {
        let mut chapter = chapter_record(config, book, required_id(&entry["id"], "Suwayomi")?);
        chapter.number = entry["chapterNumber"].as_f64().unwrap_or(0.0);
        chapter.title = text(&entry["name"]);
        chapter.page_count = entry["pageCount"].as_u64().unwrap_or(0) as u32;
        let timestamp = text(&entry["uploadDate"]).parse::<i64>().unwrap_or(0);
        chapter.date = unix_date(timestamp / 1000);
        Ok(chapter)
    })
    .collect::<Result<Vec<_>>>()?;
    chapters.sort_by(|left, right| left.number.total_cmp(&right.number));
    Ok(chapters)
}

fn suwayomi_pages(
    client: &Client,
    config: &SourceConfig,
    _book: &Book,
    chapter: &Chapter,
) -> Result<Vec<Page>> {
    let token = suwayomi_token(client, config)?;
    let id: i32 = chapter
        .remote_id
        .parse()
        .context("Suwayomi: invalid chapter identifier")?;
    let result = graphql(
        client,
        config,
        "mutation($input: FetchChapterPagesInput!) { fetchChapterPages(input: $input) { pages chapter { pageCount } } }",
        json!({"input":{"chapterId":id,"format":"png"}}),
        token.as_deref(),
    )?;
    let entries = array(&result["fetchChapterPages"], Some("pages"), "Suwayomi")?;
    ensure!(
        !entries.is_empty(),
        "Suwayomi: the chapter has no readable pages"
    );
    let headers = request_headers(client, config, token.as_deref())?;
    let source_origin = endpoint(config, &[])?.origin();
    entries
        .iter()
        .map(|entry| {
            let value = entry
                .as_str()
                .context("Suwayomi: incompatible image path response")?;
            let url = relative_image(config, value)?;
            // A provider must never receive the self-hosted server's credentials.
            let page_headers = if Url::parse(&url)?.origin() == source_origin {
                headers.clone()
            } else {
                BTreeMap::new()
            };
            Ok(Page {
                url,
                headers: page_headers,
                local_path: None,
            })
        })
        .collect()
}

fn unix_date(seconds: i64) -> String {
    if seconds <= 0 {
        return String::new();
    }
    let days = seconds.div_euclid(86_400) + 719_468;
    let era = days.div_euclid(146_097);
    let day_of_era = days - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let mut year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_prime = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_prime + 2) / 5 + 1;
    let month = month_prime + if month_prime < 10 { 3 } else { -9 };
    year += if month <= 2 { 1 } else { 0 };
    format!("{year:04}-{month:02}-{day:02}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{BufRead, BufReader, Write},
        net::TcpListener,
        thread,
    };

    #[test]
    fn old_metadata_sessions_cannot_send_after_suspend_and_resume() {
        let temporary = tempfile::tempdir().unwrap();
        let control = NetworkControl::open(temporary.path()).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let url = format!("http://{}/metadata", listener.local_addr().unwrap());
        let client = Client::builder()
            .no_proxy()
            .timeout(Duration::from_millis(250))
            .build()
            .unwrap();
        let old_epoch = control.epoch();
        control.suspend();
        control.resume();
        let called = std::cell::Cell::new(false);
        let error = with_session(&control, old_epoch, || {
            called.set(true);
            send_checked(client.get(&url))
        })
        .unwrap_err();
        assert!(super::super::transfer::is_network_interrupted(&error));
        assert!(!called.get());
        assert!(
            matches!(listener.accept(), Err(error) if error.kind() == std::io::ErrorKind::WouldBlock)
        );

        // A session that was valid at entry must also be fenced immediately
        // before send if sleep closes its epoch while the operation is running.
        let error = with_session(&control, control.epoch(), || {
            control.suspend();
            control.resume();
            send_checked(client.get(&url))
        })
        .unwrap_err();
        assert!(super::super::transfer::is_network_interrupted(&error));
        assert!(
            matches!(listener.accept(), Err(error) if error.kind() == std::io::ErrorKind::WouldBlock)
        );
        assert!(check_session().is_ok());
    }

    #[test]
    fn nested_metadata_sessions_unwind_without_polluting_the_worker_thread() {
        let outer_directory = tempfile::tempdir().unwrap();
        let inner_directory = tempfile::tempdir().unwrap();
        let outer = NetworkControl::open(outer_directory.path()).unwrap();
        let inner = NetworkControl::open(inner_directory.path()).unwrap();
        with_session(&outer, outer.epoch(), || {
            let error = with_session::<()>(&inner, inner.epoch(), || {
                bail!("Expected nested metadata failure")
            })
            .unwrap_err();
            assert!(
                error
                    .to_string()
                    .contains("Expected nested metadata failure")
            );
            check_session()?;
            let panic = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                with_session::<()>(&inner, inner.epoch(), || {
                    panic!("Expected nested metadata panic")
                })
            }));
            assert!(panic.is_err());
            inner.suspend();
            inner.resume();
            // Either leaked inner guard would now fail this outer-only check.
            check_session()
        })
        .unwrap();
        outer.suspend();
        outer.resume();
        // Standalone adapters on the same thread remain usable after the outer
        // operation ends, including when its old control is now interrupted.
        assert!(check_session().is_ok());
    }

    fn fixture_server(replies: Vec<Value>) -> (String, thread::JoinHandle<Vec<String>>) {
        fixture_server_with_headers(
            replies
                .into_iter()
                .map(|reply| (reply, Vec::new()))
                .collect(),
        )
    }

    fn fixture_server_with_headers(
        replies: Vec<(Value, Vec<(String, String)>)>,
    ) -> (String, thread::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let handle = thread::spawn(move || {
            let mut requests = Vec::new();
            for (reply, extra_headers) in replies {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut headers = String::new();
                loop {
                    let mut line = String::new();
                    reader.read_line(&mut line).unwrap();
                    if line == "\r\n" {
                        break;
                    }
                    assert!(!line.is_empty(), "Unexpected end of HTTP request");
                    headers.push_str(&line);
                }
                let length = headers
                    .lines()
                    .find_map(|line| {
                        let (name, value) = line.split_once(':')?;
                        name.eq_ignore_ascii_case("content-length")
                            .then(|| value.trim().parse::<usize>().unwrap())
                    })
                    .unwrap_or(0);
                let mut body = vec![0; length];
                reader.read_exact(&mut body).unwrap();
                requests.push(format!("{headers}\r\n{}", String::from_utf8(body).unwrap()));
                let body = reply.to_string();
                let extra_headers = extra_headers
                    .iter()
                    .map(|(name, value)| format!("{name}: {value}\r\n"))
                    .collect::<String>();
                write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n{extra_headers}Connection: close\r\n\r\n{body}", body.len()).unwrap();
            }
            requests
        });
        (format!("http://{address}/comics/"), handle)
    }

    fn request_query(request: &str) -> BTreeMap<String, String> {
        let target = request
            .lines()
            .next()
            .unwrap()
            .split_whitespace()
            .nth(1)
            .unwrap();
        Url::parse(&format!("http://fixture.test{target}"))
            .unwrap()
            .query_pairs()
            .into_owned()
            .collect()
    }

    #[test]
    fn server_endpoints_preserve_reverse_proxy_paths_and_encode_identifiers() {
        let config = SourceConfig {
            base_url: "https://example.test/comics/".into(),
            ..SourceConfig::default()
        };
        let url = endpoint(&config, &["api", "series", "a/b"]).unwrap();
        assert_eq!(url.as_str(), "https://example.test/comics/api/series/a%2Fb");
        assert_eq!(
            relative_image(&config, "/api/page/1").unwrap(),
            "https://example.test/comics/api/page/1"
        );
    }

    #[test]
    fn pagination_and_dates_are_stable() {
        assert_eq!(offset(0, 20), 0);
        assert_eq!(offset(1, 20), 0);
        assert_eq!(offset(3, 20), 40);
        assert_eq!(unix_date(1_704_067_200), "2024-01-01");
        assert_eq!(unix_date(0), "");
    }

    #[test]
    fn source_identifiers_are_required() {
        assert!(required_id(&Value::Null, "test").is_err());
        let config = SourceConfig {
            options: [("source_id".into(), json!("2499283573021220255"))].into(),
            ..SourceConfig::default()
        };
        assert_eq!(suwayomi_source(&config).unwrap(), "2499283573021220255");
    }

    #[test]
    fn chapter_identity_preserves_book_boundaries_for_keys_with_slashes() {
        let config = SourceConfig {
            id: "source".into(),
            ..SourceConfig::default()
        };
        let first = Book {
            id: compound_id(&config.id, "a/b"),
            remote_id: "a/b".into(),
            ..Book::default()
        };
        let second = Book {
            id: compound_id(&config.id, "a"),
            remote_id: "a".into(),
            ..Book::default()
        };
        let first_chapter = chapter_record(&config, &first, "c".into());
        let second_chapter = chapter_record(&config, &second, "b/c".into());
        assert_ne!(first_chapter.id, second_chapter.id);
        assert_eq!(first_chapter.book_id, first.id);
        assert_eq!(second_chapter.book_id, second.id);
    }

    #[test]
    fn cover_credentials_are_scoped_to_the_configured_origin_and_port() {
        let config = SourceConfig {
            kind: "komga".into(),
            base_url: "https://example.test:8443/comics/".into(),
            token: "server-key".into(),
            ..SourceConfig::default()
        };
        let client = Client::builder().no_proxy().build().unwrap();
        let same = image_headers_for(&client, &config, "https://example.test:8443/covers/one.jpg")
            .unwrap();
        assert_eq!(same["x-api-key"], "server-key");
        for url in [
            "https://example.test:9443/covers/one.jpg",
            "https://images.example.test/covers/one.jpg",
        ] {
            let external = image_headers_for(&client, &config, url).unwrap();
            assert!(
                !external
                    .keys()
                    .any(|key| key.eq_ignore_ascii_case("x-api-key"))
            );
            assert_eq!(external["Accept"], "image/*");
        }
    }

    #[test]
    fn komga_catalog_and_pages_keep_api_key_authentication() {
        let (base_url, server) = fixture_server(vec![
            json!({"content":[{"id":"series1","metadata":{"title":"A catalog book","summary":"Description","status":"ONGOING","genres":["Adventure"]},"booksMetadata":{"authors":[{"name":"An Author","role":"writer"}]},"booksCount":2}]}),
            json!([{"number":1,"mediaType":"image/jpeg"}]),
        ]);
        let config = SourceConfig {
            id: "komga".into(),
            kind: "komga".into(),
            base_url,
            token: "test-key".into(),
            ..SourceConfig::default()
        };
        let client = Client::builder().no_proxy().build().unwrap();
        let books = browse(&client, &config, 1, "title").unwrap();
        assert_eq!(books[0].author, "An Author");
        assert_eq!(books[0].chapter_count, 2);
        let chapter = Chapter {
            remote_id: "chapter1".into(),
            ..Chapter::default()
        };
        let pages = pages(&client, &config, &books[0], &chapter).unwrap();
        assert!(
            pages[0]
                .url
                .ends_with("/comics/api/v1/books/chapter1/pages/1")
        );
        assert_eq!(pages[0].headers["x-api-key"], "test-key");
        let requests = server.join().unwrap();
        assert!(requests[0].starts_with("POST /comics/api/v1/series/list?"));
        assert!(
            requests[0]
                .to_ascii_lowercase()
                .contains("x-api-key: test-key")
        );
        assert!(
            !requests[0]
                .to_ascii_lowercase()
                .contains("authorization: bearer")
        );
    }

    #[test]
    fn suwayomi_page_resolution_preserves_proxy_path_without_leaking_server_token() {
        let (base_url, server) = fixture_server(vec![
            json!({"data":{"fetchChapterPages":{"pages":["/api/v1/page/0","https://images.example.test/page1.png"],"chapter":{"pageCount":2}}}}),
        ]);
        let config = SourceConfig {
            id: "suwayomi".into(),
            kind: "suwayomi".into(),
            base_url,
            token: "server-token".into(),
            ..SourceConfig::default()
        };
        let client = Client::builder().no_proxy().build().unwrap();
        let chapter = Chapter {
            remote_id: "42".into(),
            ..Chapter::default()
        };
        let pages = pages(&client, &config, &Book::default(), &chapter).unwrap();
        assert!(pages[0].url.contains("/comics/api/v1/page/0"));
        assert_eq!(pages[0].headers["authorization"], "Bearer server-token");
        assert!(pages[1].headers.is_empty());
        let requests = server.join().unwrap();
        assert!(requests[0].starts_with("POST /comics/api/graphql "));
        let body: Value =
            serde_json::from_str(requests[0].split_once("\r\n\r\n").unwrap().1).unwrap();
        assert_eq!(body["variables"]["input"]["chapterId"], 42);
    }

    #[test]
    fn mangadex_search_preserves_total_and_offset_pagination() {
        let entry = |number| json!({"id":format!("manga-{number}"),"attributes":{"title":{"en":format!("Manga {number}")}},"relationships":[]});
        let first = (0..20).map(entry).collect::<Vec<_>>();
        let second = (20..23).map(entry).collect::<Vec<_>>();
        let (base_url, server) = fixture_server(vec![
            json!({"data":first,"offset":0,"limit":20,"total":23}),
            json!({"data":second,"offset":20,"limit":20,"total":23}),
        ]);
        let config = SourceConfig {
            id: "md".into(),
            kind: "mangadex".into(),
            base_url,
            ..SourceConfig::default()
        };
        let client = Client::builder().no_proxy().build().unwrap();
        let first = search_page(&client, &config, "two words", 1).unwrap();
        let second = search_page(&client, &config, "two words", 2).unwrap();
        assert_eq!((first.books.len(), first.has_next), (20, true));
        assert_eq!((second.books.len(), second.has_next), (3, false));
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(request_query(&requests[0])["offset"], "0");
        assert_eq!(request_query(&requests[1])["offset"], "20");
        assert_eq!(request_query(&requests[1])["limit"], "20");
        assert_eq!(request_query(&requests[1])["title"], "two words");
    }

    #[test]
    fn komga_catalog_preserves_zero_based_page_parameters_and_terminal_metadata() {
        let entries = |start, count| {
            (start..start + count).map(|number| json!({"id":format!("series-{number}"),"metadata":{"title":format!("Series {number}")}})).collect::<Vec<_>>()
        };
        let (base_url, server) = fixture_server(vec![
            json!({"content":entries(0,20),"number":0,"totalPages":2,"last":false}),
            json!({"content":entries(20,20),"number":1,"totalPages":2,"last":true}),
        ]);
        let config = SourceConfig {
            id: "komga".into(),
            kind: "komga".into(),
            base_url,
            ..SourceConfig::default()
        };
        let client = Client::builder().no_proxy().build().unwrap();
        let first = browse_page(&client, &config, 1, "title").unwrap();
        let second = browse_page(&client, &config, 2, "title").unwrap();
        assert_eq!((first.books.len(), first.has_next), (20, true));
        assert_eq!((second.books.len(), second.has_next), (20, false));
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(request_query(&requests[0])["page"], "0");
        assert_eq!(request_query(&requests[1])["page"], "1");
        assert_eq!(request_query(&requests[1])["size"], "20");
    }

    #[test]
    fn kavita_search_uses_real_paged_name_filters_and_pagination_headers() {
        let entries = |start| {
            (start..start + 20)
                .map(|number| json!({"id":number,"name":format!("Series {number}")}))
                .collect::<Vec<_>>()
        };
        let pagination = |page| {
            vec![(
                "Pagination".into(),
                json!({"currentPage":page,"itemsPerPage":20,"totalItems":40,"totalPages":2})
                    .to_string(),
            )]
        };
        let (base_url, server) = fixture_server_with_headers(vec![
            (json!(entries(1)), pagination(1)),
            (json!(entries(21)), pagination(2)),
        ]);
        let config = SourceConfig {
            id: "kavita".into(),
            kind: "kavita".into(),
            base_url,
            token: "fixture-token".into(),
            ..SourceConfig::default()
        };
        let client = Client::builder().no_proxy().build().unwrap();
        let first = search_page(&client, &config, "needle", 1).unwrap();
        let second = search_page(&client, &config, "needle", 2).unwrap();
        assert_eq!((first.books.len(), first.has_next), (20, true));
        assert_eq!((second.books.len(), second.has_next), (20, false));
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(request_query(&requests[0])["pageNumber"], "1");
        assert_eq!(request_query(&requests[1])["pageNumber"], "2");
        assert_eq!(request_query(&requests[1])["pageSize"], "20");
        let body: Value =
            serde_json::from_str(requests[1].split_once("\r\n\r\n").unwrap().1).unwrap();
        assert_eq!(
            body["statements"],
            json!([{"field":1,"comparison":7,"value":"needle"}])
        );
        assert_eq!(body["combination"], 1);
    }

    #[test]
    fn suwayomi_catalog_uses_the_graphql_next_page_flag_even_for_a_full_last_page() {
        let entries = |start| {
            (start..start + 20)
                .map(|number| json!({"id":number,"title":format!("Manga {number}")}))
                .collect::<Vec<_>>()
        };
        let (base_url, server) = fixture_server(vec![
            json!({"data":{"fetchSourceManga":{"mangas":entries(1),"hasNextPage":true}}}),
            json!({"data":{"fetchSourceManga":{"mangas":entries(21),"hasNextPage":false}}}),
        ]);
        let config = SourceConfig {
            id: "suwayomi".into(),
            kind: "suwayomi".into(),
            base_url,
            options: [("source_id".into(), json!("2499283573021220255"))].into(),
            ..SourceConfig::default()
        };
        let client = Client::builder().no_proxy().build().unwrap();
        let first = browse_page(&client, &config, 1, "popular").unwrap();
        let second = browse_page(&client, &config, 2, "popular").unwrap();
        assert_eq!((first.books.len(), first.has_next), (20, true));
        assert_eq!((second.books.len(), second.has_next), (20, false));
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        for (index, request) in requests.iter().enumerate() {
            let body: Value =
                serde_json::from_str(request.split_once("\r\n\r\n").unwrap().1).unwrap();
            assert_eq!(body["variables"]["input"]["page"], index + 1);
            assert_eq!(body["variables"]["input"]["source"], "2499283573021220255");
        }
    }

    #[test]
    fn catalogs_without_pagination_metadata_stop_after_an_empty_page() {
        let entries = (1..=20)
            .map(|number| json!({"id":number,"name":format!("Series {number}")}))
            .collect::<Vec<_>>();
        let (base_url, server) = fixture_server(vec![json!(entries), json!([])]);
        let config = SourceConfig {
            id: "kavita".into(),
            kind: "kavita".into(),
            base_url,
            token: "fixture-token".into(),
            ..SourceConfig::default()
        };
        let client = Client::builder().no_proxy().build().unwrap();
        let first = browse_page(&client, &config, 1, "title").unwrap();
        let second = browse_page(&client, &config, 2, "title").unwrap();
        assert_eq!((first.books.len(), first.has_next), (20, true));
        assert_eq!((second.books.len(), second.has_next), (0, false));
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(request_query(&requests[1])["pageNumber"], "2");
    }
}
