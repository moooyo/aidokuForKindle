use std::{
    cmp::Ordering,
    collections::BTreeMap,
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result, bail, ensure};
use sha2::{Digest, Sha256};

use crate::domain::{Book, Chapter, Page, compound_id};

const MAX_ENTRIES: usize = 20_000;
const MAX_PAGE_BYTES: u64 = 32 * 1024 * 1024;
const MAX_ARCHIVE_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_METADATA_BYTES: u64 = 1024 * 1024;

#[derive(Debug, Clone)]
pub struct ImportedBook {
    pub book: Book,
    pub chapters: Vec<Chapter>,
    pub pages: BTreeMap<String, Vec<Page>>,
}

#[derive(Debug, Clone)]
pub struct LocalLibrary {
    root: PathBuf,
}

#[derive(Default)]
struct Metadata {
    title: String,
    author: String,
    description: String,
    number: Option<f64>,
}

struct Staging {
    root: PathBuf,
    path: PathBuf,
}

impl Drop for Staging {
    fn drop(&mut self) {
        // Only remove the generated direct child after resolving both paths.
        if let (Ok(root), Ok(path)) = (self.root.canonicalize(), self.path.canonicalize()) {
            if path.parent() == Some(root.as_path())
                && path
                    .file_name()
                    .is_some_and(|name| name.to_string_lossy().starts_with(".import-"))
            {
                let _ = fs::remove_dir_all(path);
            }
        }
    }
}

impl LocalLibrary {
    pub fn new(root: impl AsRef<Path>) -> Result<Self> {
        let content = root.as_ref().join("content");
        fs::create_dir_all(&content).context("Create local content cache")?;
        Ok(Self {
            root: content
                .canonicalize()
                .context("Resolve local content cache")?,
        })
    }

    pub fn scan(&self, path: impl AsRef<Path>) -> Result<Vec<ImportedBook>> {
        let path = path.as_ref();
        if path.is_file() || contains_images(path)? {
            return self.import(path).map(|book| vec![book]);
        }
        ensure!(path.is_dir(), "The import path is not a file or directory");
        let mut candidates = Vec::new();
        for entry in fs::read_dir(path).context("Read local library directory")? {
            let entry = entry.context("Read local library entry")?;
            let kind = entry.file_type().context("Read local library entry type")?;
            if kind.is_symlink() {
                continue;
            }
            let candidate = entry.path();
            if (kind.is_file() && is_archive(&candidate))
                || (kind.is_dir() && contains_images(&candidate)?)
            {
                candidates.push(candidate);
            }
            ensure!(candidates.len() <= MAX_ENTRIES, "Too many local books");
        }
        candidates
            .sort_by(|left, right| natural_cmp(&left.to_string_lossy(), &right.to_string_lossy()));
        candidates
            .into_iter()
            .map(|candidate| self.import(candidate))
            .collect()
    }

    pub fn import(&self, path: impl AsRef<Path>) -> Result<ImportedBook> {
        let input = path
            .as_ref()
            .canonicalize()
            .context("Resolve the selected local comic")?;
        let remote_id = hex_digest(input.as_os_str().as_encoded_bytes());
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let staging = Staging {
            root: self.root.clone(),
            path: self
                .root
                .join(format!(".import-{}-{unique}", std::process::id())),
        };
        fs::create_dir(&staging.path).context("Create isolated import staging directory")?;
        let mut digest = Sha256::new();
        let (names, metadata) = if input.is_dir() {
            import_directory(&input, &staging.path, &mut digest)?
        } else if is_archive(&input) {
            import_archive(&input, &staging.path, &mut digest)?
        } else {
            bail!("Select a CBZ/ZIP archive or a directory containing images");
        };
        ensure!(
            !names.is_empty(),
            "The selected comic contains no supported images"
        );
        let revision = format!("{:x}", digest.finalize());
        let book_directory = self.root.join(&remote_id);
        fs::create_dir_all(&book_directory).context("Create the local comic cache directory")?;
        let destination = book_directory.join(revision);
        if !destination.exists() {
            fs::rename(&staging.path, &destination).context("Commit imported comic pages")?;
        }
        // Existing revisions remain available to an open reader and are immutable.
        let pages: Vec<Page> = names
            .into_iter()
            .map(|name| Page {
                local_path: Some(destination.join(name)),
                ..Page::default()
            })
            .collect();
        let fallback_title = if input.is_dir() {
            input.file_name()
        } else {
            input.file_stem()
        }
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| "Local comic".into());
        let title = if metadata.title.trim().is_empty() {
            fallback_title
        } else {
            metadata.title
        };
        let book_id = compound_id("local", &remote_id);
        let chapter_id = compound_id("local", &format!("{remote_id}/0"));
        let count = u32::try_from(pages.len()).context("Too many local pages")?;
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs()
            .min(i64::MAX as u64) as i64;
        let book = Book {
            id: book_id.clone(),
            source_id: "local".into(),
            remote_id: remote_id.clone(),
            title: title.clone(),
            author: metadata.author,
            description: metadata.description,
            tags: Vec::new(),
            status: "completed".into(),
            cover: pages
                .first()
                .and_then(|page| page.local_path.as_ref())
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or_default(),
            chapter_count: 1,
            unread: 1,
            category: "reading".into(),
            in_library: true,
            updated_at: timestamp,
        };
        let chapter = Chapter {
            id: chapter_id.clone(),
            book_id,
            source_id: "local".into(),
            remote_id: format!("{remote_id}/0"),
            number: metadata.number.unwrap_or(1.0),
            title,
            date: String::new(),
            page_count: count,
            downloaded: true,
            read: false,
        };
        Ok(ImportedBook {
            book,
            chapters: vec![chapter],
            pages: BTreeMap::from([(chapter_id, pages)]),
        })
    }
}

fn contains_images(path: &Path) -> Result<bool> {
    if !path.is_dir() {
        return Ok(false);
    }
    for (count, entry) in fs::read_dir(path)
        .context("Read local image directory")?
        .enumerate()
    {
        ensure!(count < MAX_ENTRIES, "Too many directory entries");
        let entry = entry.context("Read local image entry")?;
        if entry.file_type()?.is_file() && is_image(&entry.path()) {
            return Ok(true);
        }
    }
    Ok(false)
}

fn import_directory(
    input: &Path,
    staging: &Path,
    digest: &mut Sha256,
) -> Result<(Vec<String>, Metadata)> {
    let mut images = Vec::new();
    let mut metadata = Metadata::default();
    for (count, entry) in fs::read_dir(input)
        .context("Read comic image directory")?
        .enumerate()
    {
        ensure!(count < MAX_ENTRIES, "Too many comic directory entries");
        let entry = entry.context("Read comic image entry")?;
        if !entry.file_type()?.is_file() {
            continue;
        }
        let path = entry.path();
        if is_image(&path) {
            images.push(path);
        } else if path
            .file_name()
            .is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("ComicInfo.xml"))
        {
            let bytes = read_bounded(File::open(&path)?, MAX_METADATA_BYTES)?;
            metadata = parse_metadata(&bytes);
        }
    }
    images.sort_by(|left, right| natural_cmp(&left.to_string_lossy(), &right.to_string_lossy()));
    let mut names = Vec::with_capacity(images.len());
    let mut total = 0u64;
    for (index, image) in images.into_iter().enumerate() {
        let bytes = read_bounded(
            File::open(&image).context("Open local page image")?,
            MAX_PAGE_BYTES,
        )?;
        total = total
            .checked_add(bytes.len() as u64)
            .context("Local comic size overflow")?;
        ensure!(
            total <= MAX_ARCHIVE_BYTES,
            "The local comic exceeds the import size limit"
        );
        let name = write_page(
            staging,
            index,
            image
                .extension()
                .and_then(|extension| extension.to_str())
                .unwrap_or("img"),
            &bytes,
            digest,
        )?;
        names.push(name);
    }
    Ok((names, metadata))
}

fn import_archive(
    input: &Path,
    staging: &Path,
    digest: &mut Sha256,
) -> Result<(Vec<String>, Metadata)> {
    let mut archive = zip::ZipArchive::new(File::open(input).context("Open comic archive")?)
        .context("Read comic archive")?;
    ensure!(
        archive.len() <= MAX_ENTRIES,
        "The comic archive contains too many entries"
    );
    let mut images = Vec::new();
    let mut metadata_index = None;
    let mut total = 0u64;
    for index in 0..archive.len() {
        let entry = archive
            .by_index(index)
            .context("Read comic archive directory")?;
        let name = entry.name().to_owned();
        ensure!(
            safe_archive_name(&name),
            "The comic archive contains an unsafe path"
        );
        ensure!(
            !entry
                .unix_mode()
                .is_some_and(|mode| mode & 0o170000 == 0o120000),
            "Symbolic links are not allowed in comic archives"
        );
        if entry.is_dir() {
            continue;
        }
        total = total
            .checked_add(entry.size())
            .context("Comic archive size overflow")?;
        ensure!(
            total <= MAX_ARCHIVE_BYTES,
            "The comic archive exceeds the import size limit"
        );
        let page_path = Path::new(&name);
        if is_image(page_path) {
            ensure!(
                entry.size() <= MAX_PAGE_BYTES,
                "A comic page exceeds the import size limit"
            );
            ensure!(
                entry.size() < 1024 * 1024 || entry.size() / entry.compressed_size().max(1) <= 1000,
                "A comic page has an excessive compression ratio"
            );
            images.push((name, index));
        } else if name
            .rsplit(['/', '\\'])
            .next()
            .is_some_and(|part| part.eq_ignore_ascii_case("ComicInfo.xml"))
        {
            ensure!(
                entry.size() <= MAX_METADATA_BYTES,
                "ComicInfo.xml is too large"
            );
            metadata_index = Some(index);
        }
    }
    images.sort_by(|left, right| natural_cmp(&left.0, &right.0));
    let metadata = if let Some(index) = metadata_index {
        parse_metadata(&read_bounded(archive.by_index(index)?, MAX_METADATA_BYTES)?)
    } else {
        Metadata::default()
    };
    let mut names = Vec::with_capacity(images.len());
    let mut actual_total = 0u64;
    for (page_index, (name, archive_index)) in images.into_iter().enumerate() {
        let bytes = read_bounded(
            archive
                .by_index(archive_index)
                .context("Open archived comic page")?,
            MAX_PAGE_BYTES,
        )?;
        actual_total = actual_total
            .checked_add(bytes.len() as u64)
            .context("Comic archive size overflow")?;
        ensure!(
            actual_total <= MAX_ARCHIVE_BYTES,
            "The comic archive exceeds the import size limit"
        );
        let extension = Path::new(&name)
            .extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or("img");
        names.push(write_page(staging, page_index, extension, &bytes, digest)?);
    }
    Ok((names, metadata))
}

fn write_page(
    staging: &Path,
    index: usize,
    extension: &str,
    bytes: &[u8],
    digest: &mut Sha256,
) -> Result<String> {
    validate_image(bytes).with_context(|| format!("Decode comic page {}", index + 1))?;
    let name = format!("{:06}.{}", index + 1, extension.to_ascii_lowercase());
    digest.update(name.as_bytes());
    digest.update([0]);
    digest.update((index as u64).to_le_bytes());
    digest.update((bytes.len() as u64).to_le_bytes());
    digest.update(bytes);
    fs::write(staging.join(&name), bytes).context("Cache imported comic page")?;
    Ok(name)
}

fn validate_image(bytes: &[u8]) -> Result<()> {
    crate::imaging::validate_page(bytes)
        .context("Validate comic image within the reader decode limits")
}

fn read_bounded(reader: impl Read, maximum: u64) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    reader
        .take(maximum + 1)
        .read_to_end(&mut bytes)
        .context("Read comic data")?;
    ensure!(
        bytes.len() as u64 <= maximum,
        "The comic entry exceeds the size limit"
    );
    Ok(bytes)
}

fn is_archive(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| matches!(extension.to_ascii_lowercase().as_str(), "cbz" | "zip"))
}

fn is_image(path: &Path) -> bool {
    let name = path.to_string_lossy();
    if name
        .split(['/', '\\'])
        .any(|part| part.eq_ignore_ascii_case("__MACOSX") || part.starts_with("._"))
    {
        return false;
    }
    path.extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| {
            matches!(
                extension.to_ascii_lowercase().as_str(),
                "png" | "jpg" | "jpeg" | "webp" | "gif"
            )
        })
}

fn safe_archive_name(name: &str) -> bool {
    !name.is_empty()
        && !name.starts_with(['/', '\\'])
        && !name.contains('\0')
        && name
            .split(['/', '\\'])
            .all(|part| part != ".." && !part.contains(':'))
}

fn hex_digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn natural_cmp(left: &str, right: &str) -> Ordering {
    let left_lower = left.to_lowercase();
    let right_lower = right.to_lowercase();
    let left_bytes = left_lower.as_bytes();
    let right_bytes = right_lower.as_bytes();
    let (mut li, mut ri) = (0, 0);
    while li < left_bytes.len() && ri < right_bytes.len() {
        if left_bytes[li].is_ascii_digit() && right_bytes[ri].is_ascii_digit() {
            let left_start = li;
            let right_start = ri;
            while li < left_bytes.len() && left_bytes[li].is_ascii_digit() {
                li += 1;
            }
            while ri < right_bytes.len() && right_bytes[ri].is_ascii_digit() {
                ri += 1;
            }
            let left_digits = &left_bytes[left_start..li];
            let right_digits = &right_bytes[right_start..ri];
            let left_significant = left_digits
                .iter()
                .position(|byte| *byte != b'0')
                .map(|index| &left_digits[index..])
                .unwrap_or(&[]);
            let right_significant = right_digits
                .iter()
                .position(|byte| *byte != b'0')
                .map(|index| &right_digits[index..])
                .unwrap_or(&[]);
            let order = left_significant
                .len()
                .cmp(&right_significant.len())
                .then_with(|| left_significant.cmp(right_significant));
            if order != Ordering::Equal {
                return order;
            }
            let zeros = left_digits.len().cmp(&right_digits.len());
            if zeros != Ordering::Equal {
                return zeros;
            }
        } else {
            let order = left_bytes[li].cmp(&right_bytes[ri]);
            if order != Ordering::Equal {
                return order;
            }
            li += 1;
            ri += 1;
        }
    }
    left_bytes
        .len()
        .cmp(&right_bytes.len())
        .then_with(|| left.cmp(right))
}

fn parse_metadata(bytes: &[u8]) -> Metadata {
    let text = String::from_utf8_lossy(bytes);
    Metadata {
        title: xml_value(&text, "Title")
            .filter(|value| !value.trim().is_empty())
            .or_else(|| xml_value(&text, "Series"))
            .unwrap_or_default(),
        author: xml_value(&text, "Writer")
            .filter(|value| !value.trim().is_empty())
            .or_else(|| xml_value(&text, "Penciller"))
            .unwrap_or_default(),
        description: xml_value(&text, "Summary").unwrap_or_default(),
        number: xml_value(&text, "Number")
            .and_then(|number| number.trim().parse::<f64>().ok())
            .filter(|number| number.is_finite()),
    }
}

fn xml_value(text: &str, tag: &str) -> Option<String> {
    let start_tag = format!("<{tag}>");
    let end_tag = format!("</{tag}>");
    let start = text.find(&start_tag)? + start_tag.len();
    let end = text[start..].find(&end_tag)? + start;
    let raw = text[start..end].trim();
    if let Some(cdata) = raw
        .strip_prefix("<![CDATA[")
        .and_then(|value| value.strip_suffix("]]>"))
    {
        return Some(cdata.to_owned());
    }
    Some(
        raw.replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", "\"")
            .replace("&apos;", "'")
            .replace("&amp;", "&"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Write};

    fn png(shade: u8) -> Vec<u8> {
        let image = image::RgbImage::from_pixel(2, 3, image::Rgb([shade, shade, shade]));
        let mut bytes = Cursor::new(Vec::new());
        image.write_to(&mut bytes, image::ImageFormat::Png).unwrap();
        bytes.into_inner()
    }

    #[test]
    fn directory_import_is_natural_stable_and_immutable() {
        let fixture = tempfile::tempdir().unwrap();
        let input = fixture.path().join("Comic");
        fs::create_dir(&input).unwrap();
        fs::write(input.join("page10.png"), png(100)).unwrap();
        fs::write(input.join("page2.png"), png(20)).unwrap();
        fs::write(input.join("page1.png"), png(10)).unwrap();
        let library = LocalLibrary::new(fixture.path().join("cache")).unwrap();
        let first = library.import(&input).unwrap();
        let second = library.import(&input).unwrap();
        assert_eq!(first.book.id, second.book.id);
        assert_eq!(first.pages, second.pages);
        let pages = first.pages.values().next().unwrap();
        assert_eq!(pages.len(), 3);
        assert_eq!(
            fs::read(pages[0].local_path.as_ref().unwrap()).unwrap(),
            png(10)
        );
        assert_eq!(
            fs::read(pages[1].local_path.as_ref().unwrap()).unwrap(),
            png(20)
        );
        assert_eq!(
            fs::read(pages[2].local_path.as_ref().unwrap()).unwrap(),
            png(100)
        );
        fs::write(input.join("page1.png"), png(30)).unwrap();
        let third = library.import(&input).unwrap();
        assert_eq!(first.book.id, third.book.id);
        assert_ne!(first.book.cover, third.book.cover);
        assert!(Path::new(&first.book.cover).exists());
    }

    #[test]
    fn archive_import_rejects_traversal_and_decodes_real_images() {
        let fixture = tempfile::tempdir().unwrap();
        let archive_path = fixture.path().join("unsafe.cbz");
        let mut archive = zip::ZipWriter::new(File::create(&archive_path).unwrap());
        archive
            .start_file("../escape.png", zip::write::SimpleFileOptions::default())
            .unwrap();
        archive.write_all(&png(0)).unwrap();
        archive.finish().unwrap();
        let library = LocalLibrary::new(fixture.path().join("cache")).unwrap();
        assert!(
            library
                .import(&archive_path)
                .unwrap_err()
                .to_string()
                .contains("unsafe path")
        );
        assert!(!fixture.path().join("escape.png").exists());
        let directory = fixture.path().join("broken");
        fs::create_dir(&directory).unwrap();
        fs::write(directory.join("cover.png"), b"not an image").unwrap();
        assert!(library.import(directory).is_err());
    }

    #[test]
    fn archive_metadata_and_page_order_are_preserved() {
        let fixture = tempfile::tempdir().unwrap();
        let archive_path = fixture.path().join("book.cbz");
        let mut archive = zip::ZipWriter::new(File::create(&archive_path).unwrap());
        let options = zip::write::SimpleFileOptions::default();
        archive.start_file("nested/10.png", options).unwrap();
        archive.write_all(&png(100)).unwrap();
        archive.start_file("nested/2.png", options).unwrap();
        archive.write_all(&png(20)).unwrap();
        archive.start_file("ComicInfo.xml", options).unwrap();
        archive.write_all(b"<ComicInfo><Title>Fog &amp; tide</Title><Writer>Artist</Writer><Number>4</Number></ComicInfo>").unwrap();
        archive.finish().unwrap();
        let imported = LocalLibrary::new(fixture.path().join("cache"))
            .unwrap()
            .import(archive_path)
            .unwrap();
        assert_eq!(imported.book.title, "Fog & tide");
        assert_eq!(imported.book.author, "Artist");
        assert_eq!(imported.chapters[0].number, 4.0);
        let pages = imported.pages.values().next().unwrap();
        assert_eq!(
            fs::read(pages[0].local_path.as_ref().unwrap()).unwrap(),
            png(20)
        );
    }

    #[test]
    fn natural_comparison_does_not_overflow_large_numbers() {
        assert_eq!(natural_cmp("page2.png", "page10.png"), Ordering::Less);
        assert_eq!(natural_cmp("2.png", "02.png"), Ordering::Less);
        assert_eq!(
            natural_cmp(
                "999999999999999999999999.png",
                "1000000000000000000000000.png"
            ),
            Ordering::Less
        );
        assert!(!safe_archive_name("C:\\escape.png"));
        assert!(!safe_archive_name("nested\\..\\escape.png"));
    }

    #[test]
    fn a_tall_noninterlaced_png_can_be_imported_for_streaming_reading() {
        let fixture = tempfile::tempdir().unwrap();
        let input = fixture.path().join("Vertical comic");
        fs::create_dir(&input).unwrap();
        image::GrayImage::from_pixel(4, 150_000, image::Luma([17]))
            .save(input.join("page1.png"))
            .unwrap();
        let imported = LocalLibrary::new(fixture.path().join("cache"))
            .unwrap()
            .import(input)
            .unwrap();
        assert_eq!(imported.chapters[0].page_count, 1);
        assert!(Path::new(&imported.book.cover).is_file());
    }
}
