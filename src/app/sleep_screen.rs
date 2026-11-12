//! Prepare one source-scoped sleep cover outside the power-event critical path.

use super::*;
use crate::{cache::RenderKey, ui::SleepScreenModel};
use image::ImageEncoder;
use slint::{Rgb8Pixel, SharedPixelBuffer, SharedString};
use std::io::{Cursor, Read};

const MAX_COVER_WIDTH: u32 = 1536;
const MAX_COVER_HEIGHT: u32 = 2048;
const MAX_SOURCE_SIDE: u32 = 4096;
const MAX_ENCODED_BYTES: usize = 8 * 1024 * 1024;
const RETRY_DELAY: Duration = Duration::from_secs(30);

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct CoverIdentity {
    source_id: String,
    source_revision: u64,
    book_id: String,
    cover_digest: String,
}

impl CoverIdentity {
    fn new(book: &Book, source_revision: u64) -> Option<Self> {
        if book.source_id.len() > 4096 || book.id.len() > 4096 || book.cover.len() > 8192 {
            return None;
        }
        Some(Self {
            source_id: book.source_id.clone(),
            source_revision,
            book_id: book.id.clone(),
            cover_digest: format!("{:x}", Sha256::digest(book.cover.as_bytes())),
        })
    }
}

struct CoverJob {
    id: u64,
    identity: CoverIdentity,
    cancellation: imaging::DecodeCancellation,
}

#[derive(Default)]
pub(super) struct SleepScreenState {
    identity: Option<CoverIdentity>,
    book: Option<Book>,
    title: SharedString,
    author: SharedString,
    cover: Option<Image>,
    cover_bytes: usize,
    full_resolution: bool,
    job: Option<CoverJob>,
    retry_at: Option<Instant>,
    failures: u8,
}

/// A separate single sleep image, excluded from both Reader pixel LRUs.
pub(super) struct PreparedCover {
    pixels: SharedPixelBuffer<Rgb8Pixel>,
}

impl PreparedCover {
    fn new(gray: &GrayImage) -> Self {
        let mut pixels = SharedPixelBuffer::<Rgb8Pixel>::new(gray.width(), gray.height());
        for (destination, gray) in pixels
            .make_mut_bytes()
            .chunks_exact_mut(3)
            .zip(gray.as_raw())
        {
            destination.fill(*gray);
        }
        Self { pixels }
    }
}

fn bounded_text(text: &str, limit: usize) -> SharedString {
    text.chars().take(limit).collect::<String>().into()
}

/// Match BiliComics' proportional, white-paper, sixteen-level cover derivative.
fn decode_cover(encoded: &[u8]) -> Result<GrayImage> {
    anyhow::ensure!(
        encoded.len() <= MAX_ENCODED_BYTES,
        "Sleep cover exceeds its encoded byte limit"
    );
    let _permit = imaging::decode_permit()?;
    imaging::check_decode_cancelled()?;
    let mut reader = image::ImageReader::new(Cursor::new(encoded)).with_guessed_format()?;
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(MAX_SOURCE_SIDE);
    limits.max_image_height = Some(MAX_SOURCE_SIDE);
    limits.max_alloc = Some(64 * 1024 * 1024);
    reader.limits(limits);
    let image = reader.decode()?;
    let image = if image.width() > MAX_COVER_WIDTH || image.height() > MAX_COVER_HEIGHT {
        image.resize(
            MAX_COVER_WIDTH,
            MAX_COVER_HEIGHT,
            image::imageops::FilterType::Triangle,
        )
    } else {
        image
    };
    let rgba = image.into_rgba8();
    let (width, height) = rgba.dimensions();
    anyhow::ensure!(
        width > 0 && height > 0 && width <= MAX_COVER_WIDTH && height <= MAX_COVER_HEIGHT,
        "Sleep cover exceeds its prepared pixel budget"
    );
    let mut gray = GrayImage::new(width, height);
    for (destination, pixel) in gray.as_mut().iter_mut().zip(rgba.pixels()) {
        let luma = (2126 * u32::from(pixel[0])
            + 7152 * u32::from(pixel[1])
            + 722 * u32::from(pixel[2])
            + 5000)
            / 10000;
        let alpha = u32::from(pixel[3]);
        let opaque = (luma * alpha + 255 * (255 - alpha) + 127) / 255;
        *destination = (((opaque + 8) / 17).min(15) * 17) as u8;
    }
    imaging::check_decode_cancelled()?;
    Ok(gray)
}

fn read_cover(path: &Path) -> Result<Vec<u8>> {
    let mut encoded = Vec::new();
    fs::File::open(path)?
        .take(MAX_ENCODED_BYTES as u64 + 1)
        .read_to_end(&mut encoded)?;
    anyhow::ensure!(
        encoded.len() <= MAX_ENCODED_BYTES,
        "Sleep cover exceeds its encoded byte limit"
    );
    Ok(encoded)
}

fn derivative_alias(scope: &str) -> String {
    // The existing cover prefix keeps disk accounting and eviction unchanged.
    format!("cover-render-v2:sleep-cover-v1:{scope}:{MAX_COVER_WIDTH}x{MAX_COVER_HEIGHT}")
}

fn derivative_key(scope: &str, source_digest: &str) -> RenderKey {
    RenderKey {
        source_key: format!("aidoku-sleep-cover-v1:{scope}:{source_digest}"),
        width: MAX_COVER_WIDTH,
        height: MAX_COVER_HEIGHT,
        crop_borders: false,
        dither: false,
        contrast: 0,
        vertical: false,
        offset: 0,
        zoom_milli: 1000,
    }
}

fn read_derivative(cache: &crate::cache::CacheManager, path: &Path) -> Result<GrayImage> {
    let _lease = cache.lease_path(path)?;
    cache.validate_path(path)?;
    decode_cover(&read_cover(path)?)
}

fn publish_derivative(
    cache: &crate::cache::CacheManager,
    key: &RenderKey,
    alias: &str,
    gray: &GrayImage,
) -> Result<()> {
    let mut encoded = Vec::new();
    image::codecs::png::PngEncoder::new(&mut encoded).write_image(
        gray.as_raw(),
        gray.width(),
        gray.height(),
        image::ExtendedColorType::L8,
    )?;
    anyhow::ensure!(
        encoded.len() <= MAX_ENCODED_BYTES,
        "The prepared sleep PNG exceeds its encoded byte limit"
    );
    imaging::check_decode_cancelled()?;
    let write = cache.begin_processed(key, encoded.len() as u64)?;
    fs::write(write.temp_path(), encoded)?;
    imaging::check_decode_cancelled()?;
    let path = write.commit()?;
    cache.set_alias(alias, &path)
}

fn prepare_cover(
    manager: Arc<SourceManager>,
    identity: CoverIdentity,
    book: Book,
    configuration: Option<SourceConfig>,
    allow_network: bool,
    cancellation: imaging::DecodeCancellation,
) -> Result<JobResult> {
    imaging::with_background_decode(None, || {
        let cache = manager.cache();
        let local =
            cover_jobs::local_cover_path(&book.cover, book.source_id == "local")?.or_else(|| {
                (url::Url::parse(&book.cover).is_err() && Path::new(&book.cover).is_file())
                    .then(|| PathBuf::from(&book.cover))
            });
        let (raw, scope) = if let Some(path) = local {
            let request = Page {
                local_path: Some(path),
                ..Page::default()
            };
            let raw = cache
                .lookup_raw(&request)?
                .context("The local sleep cover is unavailable")?;
            let mut digest = Sha256::new();
            for value in [
                book.source_id.as_bytes(),
                book.id.as_bytes(),
                book.cover.as_bytes(),
            ] {
                digest.update((value.len() as u64).to_le_bytes());
                digest.update(value);
            }
            (raw, format!("{:x}", digest.finalize()))
        } else {
            let configuration = configuration.context("The sleep cover source is not installed")?;
            let scope = cover_jobs::remote_cover_identity(&book, &configuration)?;
            let source_alias = cover_jobs::cover_source_alias(&scope);
            let mut raw = cache.lookup_alias(&source_alias)?;
            if raw.is_none() {
                for legacy in cover_jobs::legacy_metadata_cover_identities(&book, &configuration)? {
                    raw = cache.lookup_alias(&cover_jobs::cover_source_alias(&legacy))?;
                    if raw.is_some() {
                        break;
                    }
                }
            }
            if raw.is_none() {
                // A managed derivative remains usable after ordinary raw eviction.
                if let Some(path) = cache.lookup_alias(&derivative_alias(&scope))? {
                    if let Ok(gray) = imaging::with_background_decode(Some(&cancellation), || {
                        read_derivative(cache, &path)
                    }) {
                        manager
                            .network_control()
                            .check_epoch(manager.network_epoch())?;
                        return Ok(JobResult::SleepCover {
                            identity,
                            image: PreparedCover::new(&gray),
                        });
                    }
                }
                anyhow::ensure!(
                    allow_network && cover_jobs::cover_source_allows_network(&configuration),
                    "The sleep cover is not available in the offline cache"
                );
                cancellation.check()?;
                let request = manager.cover_page(&book)?;
                let fetched = manager.fetch_page_for_source(
                    &book.source_id,
                    &request,
                    FetchPriority::Background,
                    None,
                )?;
                manager
                    .network_control()
                    .check_epoch(manager.network_epoch())?;
                if let Err(error) = cache.set_alias(&source_alias, &fetched) {
                    eprintln!("aidoku: sleep cover source alias: {error:#}");
                }
                raw = Some(fetched);
            }
            (raw.context("The sleep cover source is unavailable")?, scope)
        };
        let _lease = cache.lease_path(&raw)?;
        cache.validate_path(&raw)?;
        let encoded = read_cover(&raw)?;
        let digest = format!("{:x}", Sha256::digest(&encoded));
        anyhow::ensure!(
            cache.fingerprint(&raw)? == digest,
            "The sleep cover source changed during preparation"
        );
        let key = derivative_key(&scope, &digest);
        let alias = derivative_alias(&scope);
        imaging::with_background_decode(Some(&cancellation), || {
            let cached = cache.lookup_processed(&key)?;
            let gray = match cached.and_then(|path| read_derivative(cache, &path).ok()) {
                Some(gray) => gray,
                None => {
                    let gray = decode_cover(&encoded)?;
                    manager
                        .network_control()
                        .check_epoch(manager.network_epoch())?;
                    if let Err(error) = publish_derivative(cache, &key, &alias, &gray) {
                        cancellation.check()?;
                        eprintln!("aidoku: optional sleep cover cache: {error:#}");
                    }
                    gray
                }
            };
            manager
                .network_control()
                .check_epoch(manager.network_epoch())?;
            cancellation.check()?;
            Ok(JobResult::SleepCover {
                identity,
                image: PreparedCover::new(&gray),
            })
        })
    })
}

impl App {
    pub fn sleep_cover_enabled(&self) -> bool {
        self.prefs.sleep_cover && self.prefs.screensaver_mode == "cover" && !self.is_starting()
    }

    pub(super) fn cancel_sleep_cover_preparation(&mut self) {
        if let Some(job) = &self.sleep_screen.job {
            job.cancellation.cancel();
        }
    }

    pub(super) fn clear_sleep_screen(&mut self) {
        self.cancel_sleep_cover_preparation();
        // Keep the worker slot until its envelope drains, even after selection changes.
        let job = self.sleep_screen.job.take();
        self.sleep_screen = SleepScreenState {
            job,
            ..SleepScreenState::default()
        };
        self.hide_sleep_cover();
    }

    /// Capture metadata during the existing awake Reader projection, without a new query.
    pub(super) fn observe_sleep_book(&mut self, book: Option<Book>) {
        let book = book.filter(|_| {
            self.route == "reader"
                && self.prefs.sleep_cover
                && self.prefs.screensaver_mode == "cover"
        });
        let identity = book
            .as_ref()
            .and_then(|book| CoverIdentity::new(book, self.source_revision));
        if identity != self.sleep_screen.identity {
            self.clear_sleep_screen();
            self.sleep_screen.identity = identity;
        }
        let Some(book) = book else {
            return;
        };
        self.sleep_screen.title = bounded_text(&book.title, 256);
        self.sleep_screen.author = bounded_text(&book.author, 128);
        if !self.sleep_screen.full_resolution {
            let thumbnail = self
                .cover_paths
                .get(&book.id)
                .filter(|_| self.cover_uris.get(&book.id) == Some(&book.cover))
                .and_then(|path| self.cover_cache.get(&(book.id.clone(), path.clone())))
                .or_else(|| {
                    self.cover_cache
                        .get(&(book.id.clone(), PathBuf::from(&book.cover)))
                });
            if let Some(thumbnail) = thumbnail {
                let size = thumbnail.size();
                if size.width > 0
                    && size.height > 0
                    && size.width <= MAX_COVER_WIDTH
                    && size.height <= MAX_COVER_HEIGHT
                {
                    self.sleep_screen.cover_bytes = size.width as usize * size.height as usize * 3;
                    self.sleep_screen.cover = Some(thumbnail.clone());
                }
            }
        }
        self.sleep_screen.book = Some(book);
    }

    fn sleep_cover_is_current(&self, identity: &CoverIdentity) -> bool {
        if self.route != "reader"
            || identity.source_revision != self.source_revision
            || self.sleep_screen.identity.as_ref() != Some(identity)
        {
            return false;
        }
        self.active_sleep_book_id() == Some(identity.book_id.as_str())
            && self
                .sleep_screen
                .book
                .as_ref()
                .and_then(|book| CoverIdentity::new(book, self.source_revision))
                .as_ref()
                == Some(identity)
    }

    fn active_sleep_book_id(&self) -> Option<&str> {
        (self.route == "reader")
            .then(|| {
                self.displayed_reader
                    .as_ref()
                    .map(|frame| frame.session.book_id.as_str())
                    .or_else(|| {
                        self.reader
                            .as_ref()
                            .map(|reader| reader.current_chapter().book_id.as_str())
                    })
            })
            .flatten()
    }

    /// Only ordinary awake work may acquire, read or decode a candidate.
    pub(super) fn prepare_sleep_screen(&mut self) -> Result<()> {
        if self.suspended
            || self.shutting_down
            || self.is_starting()
            || self.resume_frame_pending
            || (!self.platform.is_kindle() && !self.options.demo)
            || self.metadata_restore_active()
        {
            return Ok(());
        }
        if !self.sleep_cover_enabled() || self.route != "reader" {
            self.observe_sleep_book(None);
            return Ok(());
        }
        let Some(identity) = self
            .sleep_screen
            .identity
            .clone()
            .filter(|identity| self.sleep_cover_is_current(identity))
        else {
            return Ok(());
        };
        if self.sleep_screen.full_resolution
            || self.sleep_screen.job.is_some()
            || self
                .sleep_screen
                .retry_at
                .is_some_and(|at| Instant::now() < at)
            || self.reader_inflight.is_some()
            || self.reader_dirty
            || self.prefetch_inflight.is_some()
            || self.cover_inflight.is_some()
            || !self.cover_queue.is_empty()
        {
            return Ok(());
        }
        let Some(book) = self
            .sleep_screen
            .book
            .clone()
            .filter(|book| !book.cover.trim().is_empty())
        else {
            return Ok(());
        };
        let configurations = match self.source_configurations.as_ref() {
            Some(configurations) => configurations.clone(),
            None => {
                let configurations = Arc::new(self.store.sources()?);
                self.source_configurations = Some(configurations.clone());
                configurations
            }
        };
        let configuration = configurations
            .iter()
            .find(|source| source.id == book.source_id)
            .cloned();
        let allow_network =
            !self.options.offline && !self.options.demo && !self.resume_network_pending;
        let cancellation = imaging::DecodeCancellation::new();
        let worker_cancellation = cancellation.clone();
        let worker_identity = identity.clone();
        match self.spawn_job_kind(false, JobKind::SleepCover, move |manager| {
            prepare_cover(
                manager,
                worker_identity,
                book,
                configuration,
                allow_network,
                worker_cancellation,
            )
        }) {
            Ok(id) => {
                self.sleep_screen.job = Some(CoverJob {
                    id,
                    identity,
                    cancellation,
                })
            }
            Err(error) => {
                self.defer_sleep_cover();
                return Err(error);
            }
        }
        Ok(())
    }

    pub(super) fn finish_sleep_cover_job(&mut self, id: u64, error: Option<&anyhow::Error>) {
        if self
            .sleep_screen
            .job
            .as_ref()
            .is_some_and(|job| job.id == id)
        {
            let job = self
                .sleep_screen
                .job
                .take()
                .expect("The sleep job was selected by identity");
            if self.sleep_screen.identity.as_ref() != Some(&job.identity)
                || error.is_some_and(|error| {
                    imaging::is_decode_cancelled(error) || source_jobs::is_superseded(error)
                })
            {
                self.sleep_screen.retry_at = None;
            } else if error.is_some() {
                self.defer_sleep_cover();
            }
        }
    }

    fn defer_sleep_cover(&mut self) {
        self.sleep_screen.failures = self.sleep_screen.failures.saturating_add(1);
        let delay = RETRY_DELAY
            .saturating_mul(1_u32 << self.sleep_screen.failures.saturating_sub(1).min(4))
            .min(Duration::from_secs(5 * 60));
        self.sleep_screen.retry_at = Some(Instant::now() + delay);
    }

    pub(super) fn accept_sleep_cover(&mut self, identity: CoverIdentity, prepared: PreparedCover) {
        if self.suspended || !self.sleep_cover_enabled() || !self.sleep_cover_is_current(&identity)
        {
            return;
        }
        self.sleep_screen.cover_bytes = prepared.pixels.as_bytes().len();
        self.sleep_screen.cover = Some(Image::from_rgb8(prepared.pixels));
        self.sleep_screen.full_resolution = true;
        self.sleep_screen.retry_at = None;
        self.sleep_screen.failures = 0;
        self.invalidate_storage_details();
    }

    /// Consume already prepared memory, even after the intent gate has closed.
    pub fn show_sleep_cover(&mut self) -> Result<()> {
        let ui = self
            .ui
            .upgrade()
            .context("The sleep presentation window is unavailable")?;
        let current = self
            .sleep_screen
            .identity
            .as_ref()
            .is_some_and(|identity| self.sleep_cover_is_current(identity));
        let model = ui.global::<SleepScreenModel>();
        if current {
            model.set_has_cover(self.sleep_screen.cover.is_some());
            model.set_cover(self.sleep_screen.cover.clone().unwrap_or_default());
            model.set_title(self.sleep_screen.title.clone());
            model.set_author(self.sleep_screen.author.clone());
        } else if self
            .sleep_screen
            .book
            .as_ref()
            .is_some_and(|book| self.active_sleep_book_id() == Some(book.id.as_str()))
        {
            model.set_has_cover(false);
            model.set_cover(Image::default());
            model.set_title(self.sleep_screen.title.clone());
            model.set_author(self.sleep_screen.author.clone());
        } else {
            model.set_has_cover(false);
            model.set_cover(Image::default());
            model.set_title("Aidoku".into());
            model.set_author(tr("sleep_neutral").into());
        }
        model.set_active(true);
        ui.window().request_redraw();
        Ok(())
    }

    /// Remove the temporary overlay before restoring the retained Reader frame.
    pub fn hide_sleep_cover(&mut self) {
        if let Some(ui) = self.ui.upgrade() {
            let model = ui.global::<SleepScreenModel>();
            model.set_active(false);
            model.set_has_cover(false);
            model.set_cover(Image::default());
            model.set_title("".into());
            model.set_author("".into());
        }
    }

    pub(super) fn sleep_screen_snapshot(&self) -> Value {
        json!({
            "enabled": self.sleep_cover_enabled(),
            "selected": self.sleep_screen.identity.is_some(),
            "full_resolution": self.sleep_screen.full_resolution,
            "retained_bytes": self.sleep_screen.cover_bytes,
            "width": self.sleep_screen.cover.as_ref().map(|image| image.size().width),
            "height": self.sleep_screen.cover.as_ref().map(|image| image.size().height),
            "budget_bytes": MAX_COVER_WIDTH as usize * MAX_COVER_HEIGHT as usize * 3,
            "pending": self.sleep_screen.job.is_some(),
            "active": self.ui.upgrade().is_some_and(|ui| ui.global::<SleepScreenModel>().get_active()),
        })
    }
}

#[cfg(test)]
mod tests;
