use super::*;

/// A failed request retains its destination, never a cloned chapter collection.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub(super) struct ReaderTarget {
    pub chapter_id: String,
    pub page: u32,
    pub vertical_offset: u32,
    pub zoom: f32,
    pub wide_half: u8,
}

impl ReaderTarget {
    pub(super) fn new(chapter_id: &str, page: u32, vertical_offset: u32, zoom: f32) -> Self {
        Self {
            chapter_id: chapter_id.into(),
            page: page.max(1),
            vertical_offset,
            zoom,
            wide_half: 0,
        }
    }

    pub(super) fn with_wide_half(mut self, half: u8) -> Self {
        self.wide_half = half.min(1);
        self
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub(super) struct ReaderRecovery {
    pub target: ReaderTarget,
    pub retrying: bool,
    pub reason: ReaderFailureReason,
}

/// Only this allowlisted category reaches UI state or diagnostic snapshots.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum ReaderFailureReason {
    UnsupportedSource,
    Timeout,
    Network,
    ImageTooLarge,
    InvalidImage,
    MissingFile,
    Storage,
    Offline,
    Other,
}

impl ReaderFailureReason {
    fn classify(error: &anyhow::Error) -> Self {
        for cause in error.chain() {
            if let Some(io) = cause.downcast_ref::<std::io::Error>() {
                use std::io::ErrorKind;
                match io.kind() {
                    ErrorKind::NotFound => return Self::MissingFile,
                    ErrorKind::TimedOut => return Self::Timeout,
                    ErrorKind::StorageFull | ErrorKind::PermissionDenied => return Self::Storage,
                    ErrorKind::ConnectionRefused
                    | ErrorKind::ConnectionReset
                    | ErrorKind::ConnectionAborted
                    | ErrorKind::NotConnected => return Self::Network,
                    _ => {}
                }
            }
            if let Some(request) = cause.downcast_ref::<reqwest::Error>() {
                if request.is_timeout() {
                    return Self::Timeout;
                }
                if request.is_connect() || request.is_status() {
                    return Self::Network;
                }
            }
        }
        for cause in error.chain() {
            if let Some(image) = cause.downcast_ref::<image::ImageError>() {
                match image {
                    image::ImageError::Limits(_) => return Self::ImageTooLarge,
                    image::ImageError::Decoding(_) | image::ImageError::Unsupported(_) => {
                        return Self::InvalidImage;
                    }
                    _ => {}
                }
            }
            if cause.is::<png::DecodingError>() {
                return Self::InvalidImage;
            }
        }
        // Guest traps and bounded image guards use contextual text rather than
        // typed errors. Match capability keywords without retaining their text.
        let text = format!("{error:#}").to_ascii_lowercase();
        let contains = |terms: &[&str]| terms.iter().any(|term| text.contains(*term));
        if contains(&[
            "unsupported",
            "not supported",
            "does not support",
            "javascript",
            "browser verification",
            "legacy source",
            "legacy aidoku",
        ]) {
            Self::UnsupportedSource
        } else if contains(&["timeout", "timed out", "operation exceeded its time limit"]) {
            Self::Timeout
        } else if contains(&[
            "enough space",
            "disk full",
            "disk is full",
            "no space",
            "read-only file",
            "readonly database",
            "disk i/o error",
            "permission denied",
        ]) {
            Self::Storage
        } else if contains(&[
            "image",
            "compressed page",
            "source page",
            "png",
            "jpeg",
            "decoder",
            "megapixel",
        ]) && contains(&["exceed", "too large", "split the page", "allocation limit"])
        {
            Self::ImageTooLarge
        } else if contains(&[
            "invalid image",
            "invalid row",
            "decode page",
            "decode image",
            "image format",
            "corrupt image",
            "missing or invalid",
        ]) {
            Self::InvalidImage
        } else if text.contains(tr("chapter_unavailable").as_str()) {
            Self::Offline
        } else if contains(&[
            "network",
            "connection",
            "http request",
            "http status",
            "request failed",
        ]) {
            Self::Network
        } else {
            Self::Other
        }
    }

    fn label(self) -> String {
        tr(match self {
            Self::UnsupportedSource => "reader_reason_unsupported",
            Self::Timeout => "request_timeout",
            Self::Network => "reader_reason_network",
            Self::ImageTooLarge => "reader_reason_image_large",
            Self::InvalidImage => "reader_reason_image_invalid",
            Self::MissingFile => "reader_reason_file_missing",
            Self::Storage => "reader_reason_storage",
            Self::Offline => "reader_reason_offline",
            Self::Other => "error",
        })
    }
}

struct RecoveryChapterHint {
    number: Option<String>,
    title: String,
    fingerprint: String,
}

impl RecoveryChapterHint {
    fn new(id: &str, chapter: Option<&Chapter>) -> Self {
        let number = chapter.and_then(|chapter| {
            let value = chapter.number;
            let text = if value.fract().abs() < f64::EPSILON {
                format!("{value:.0}")
            } else {
                value.to_string()
            };
            (value.is_finite() && value >= 0.0 && text.len() <= 10).then_some(text)
        });
        let title = chapter
            .map(|chapter| safe_chapter_title(&chapter.title))
            .unwrap_or_default();
        let fingerprint = format!("{:x}", Sha256::digest(id.as_bytes()))[..6].to_uppercase();
        Self {
            number,
            title,
            fingerprint,
        }
    }

    fn page_label(&self, page: u32, include_title: bool, repeated_title: bool) -> String {
        let chapter = match self.number.as_deref() {
            Some(number) => tr("reader_chapter_identity").replace("{chapter}", number),
            None => tr("reader_unknown_chapter").replace("{id}", &self.fingerprint),
        };
        let target = tr("reader_chapter_page")
            .replace("{chapter}", &chapter)
            .replace("{page}", &page.to_string());
        if !include_title && self.number.is_some() {
            return target;
        }
        if self.number.is_none() && self.title.is_empty() {
            return target;
        }
        let detail = if repeated_title {
            // Equal or equally truncated special titles need a safe short key.
            let prefix: String = self.title.chars().take(2).collect();
            format!("{prefix}#{}", self.fingerprint)
        } else if self.title.is_empty() {
            if self.number.is_none() {
                return target;
            }
            format!("#{}", self.fingerprint)
        } else {
            self.title.clone()
        };
        // The chapter and page always precede the bounded optional title.
        tr("reader_target_title")
            .replace("{target}", &target)
            .replace("{title}", &detail)
    }
}

fn safe_chapter_title(title: &str) -> String {
    let normalized: String = title
        .chars()
        .filter(|value| {
            !value.is_control()
                && !matches!(*value,
            '\u{00ad}' | '\u{061c}' | '\u{200b}'..='\u{200f}'
            | '\u{202a}'..='\u{202e}' | '\u{2060}'..='\u{2069}' | '\u{feff}')
        })
        .take(80)
        .collect();
    let lowered = normalized.to_ascii_lowercase();
    if [
        "://",
        "www.",
        "token=",
        "api_key=",
        "password=",
        "authorization",
    ]
    .iter()
    .any(|value| lowered.contains(*value))
    {
        return String::new();
    }
    let characters: Vec<_> = normalized.trim().chars().collect();
    if characters.len() <= 4 {
        return characters.into_iter().collect();
    }
    let mut short: String = characters.iter().take(2).copied().collect();
    short.push('…');
    if let Some(last) = characters.last() {
        short.push(*last);
    }
    short
}

impl App {
    fn recovery_chapter_hint(&self, id: &str) -> RecoveryChapterHint {
        if let Some(chapter) = self
            .reader
            .as_ref()
            .and_then(|reader| reader.chapters().iter().find(|chapter| chapter.id == id))
        {
            return RecoveryChapterHint::new(id, Some(chapter));
        }
        // This is a single primary-key lookup, with no source or network work.
        let chapter = self.store.chapter(id).ok().flatten();
        RecoveryChapterHint::new(id, chapter.as_ref())
    }

    pub(super) fn reader_target(&self) -> Option<ReaderTarget> {
        self.reader.as_ref().map(|reader| {
            ReaderTarget::new(
                &reader.current_chapter().id,
                reader.page,
                reader.vertical_offset,
                reader.zoom,
            )
            .with_wide_half(reader.wide_half)
        })
    }

    /// Page actions always refer to pixels that have actually been published.
    pub(super) fn displayed_reader_session(&self) -> Result<ReaderSession> {
        self.displayed_reader
            .as_ref()
            .filter(|_| self.route == "reader")
            .map(|frame| frame.session.clone())
            .context(tr("reader_page_not_displayed"))
    }

    pub(super) fn reader_job_failed(&mut self, context: &JobContext, error: anyhow::Error) {
        if !self.job_context_is_current(context) {
            return;
        }
        if let Some(target) = context.reader_target.clone() {
            self.recover_reader_failure(target, error);
        } else {
            self.show_error(error);
        }
    }

    pub(super) fn recover_reader_failure(&mut self, target: ReaderTarget, error: anyhow::Error) {
        self.pending_wide_navigation = 0;
        let reason = ReaderFailureReason::classify(&error);
        eprintln!(
            "aidoku: reader request {} page {}: {error:#}",
            target.chapter_id, target.page
        );
        self.invalidate_reader_work();
        self.busy = false;
        self.message.clear();
        self.message_until = None;
        // A detail screen can still retain a hidden texture from another book.
        // Only a frame that was visible in Reader is a valid rollback surface.
        if self.route == "reader" {
            if let Err(restore_error) = self.restore_displayed_reader() {
                eprintln!("aidoku: restore displayed reader after failure: {restore_error:#}");
            }
        }
        self.reader_recovery = Some(ReaderRecovery {
            target,
            retrying: false,
            reason,
        });
        self.show_reader_recovery();
        // Wake-frame acknowledgement can fail before any worker is started;
        // there may be no later result to trigger the usual view projection.
        if let Err(sync_error) = self.sync_view() {
            eprintln!("aidoku: reader recovery feedback: {sync_error:#}");
        }
        self.platform.refresh(RefreshKind::Ui);
    }

    pub(super) fn show_reader_recovery(&mut self) {
        let Some(recovery) = self.reader_recovery.as_ref() else {
            return;
        };
        let page = recovery.target.page.to_string();
        let displayed = self.displayed_reader_session().ok();
        let mut title = tr("reader_page_load_failed").replace("{page}", &page);
        let mut retry = tr("reader_retry_page").replace("{page}", &page);
        let mut back = displayed
            .as_ref()
            .map(|session| {
                tr("reader_continue_displayed").replace("{page}", &session.page.to_string())
            })
            .unwrap_or_else(|| tr("reader_return_detail"));
        if let Some(displayed) =
            displayed.filter(|session| session.chapter_id != recovery.target.chapter_id)
        {
            let failed_hint = self.recovery_chapter_hint(&recovery.target.chapter_id);
            let displayed_hint = self.recovery_chapter_hint(&displayed.chapter_id);
            let same_number = failed_hint.number == displayed_hint.number;
            let repeated_title = same_number && failed_hint.title == displayed_hint.title;
            let failed_label =
                failed_hint.page_label(recovery.target.page, same_number, repeated_title);
            let displayed_label =
                displayed_hint.page_label(displayed.page, same_number, repeated_title);
            title = tr("reader_target_load_failed").replace("{target}", &failed_label);
            retry = tr("reader_retry_target").replace("{target}", &failed_label);
            back = tr("reader_continue_target").replace("{target}", &displayed_label);
        }
        let retrying = recovery.retrying;
        let reason = recovery.reason.label();
        let _ = self.show_choices(
            &title,
            "reader-recovery",
            vec![
                (
                    "reader-retry".into(),
                    retry,
                    if retrying { tr("loading") } else { reason },
                ),
                ("reader-recovery-back".into(), back, String::new()),
            ],
        );
        if retrying {
            self.choice_disabled.insert("reader-retry".into());
            self.sync_choice_rows();
        }
    }

    pub(super) fn retry_reader_recovery(&mut self) -> Result<()> {
        let Some(recovery) = self.reader_recovery.as_mut() else {
            return Ok(());
        };
        if recovery.retrying {
            return Ok(());
        }
        recovery.retrying = true;
        let target = recovery.target.clone();
        self.show_reader_recovery();
        // An empty/failed manifest must go through the source chapter adapter.
        // load_reader_target also preserves the exact image viewport on a hit.
        self.load_reader_target(target)?;
        if self
            .reader_recovery
            .as_ref()
            .is_some_and(|recovery| recovery.retrying)
        {
            self.show_reader_recovery();
        }
        Ok(())
    }

    pub(super) fn clear_reader_recovery(&mut self) {
        self.reader_recovery = None;
        if matches!(
            self.modal_context.as_str(),
            "reader-recovery" | "reader-action-error"
        ) {
            self.modal.clear();
            self.modal_context.clear();
        }
    }

    pub(super) fn leave_reader_recovery(&mut self) -> Result<()> {
        self.invalidate_reader_work();
        if self.route == "reader" && self.displayed_reader.is_some() {
            self.restore_displayed_reader()?;
        }
        self.clear_reader_recovery();
        if self.route == "reader" && self.displayed_reader.is_none() {
            self.reader = None;
            self.route = if self.current_book_id.is_some() {
                "detail"
            } else {
                "library"
            }
            .into();
        }
        if let Some(reader) = &mut self.reader {
            reader.menu_open = false;
        }
        Ok(())
    }

    pub(super) fn show_reader_action_error(&mut self, error: anyhow::Error) {
        self.pending_wide_navigation = 0;
        eprintln!("aidoku: displayed page action: {error:#}");
        let title = tr(if self.displayed_reader_session().is_ok() {
            "reader_action_failed"
        } else {
            "reader_page_not_displayed"
        });
        let _ = self.show_choices(
            &title,
            "reader-action-error",
            vec![("reader-action-dismiss".into(), tr("confirm"), String::new())],
        );
        if let Err(sync_error) = self.sync_view() {
            eprintln!("aidoku: reader action feedback: {sync_error:#}");
        }
        self.platform.refresh(RefreshKind::Ui);
    }

    pub(super) fn dismiss_reader_action_error(&mut self) {
        if self.reader_recovery.is_some() {
            self.show_reader_recovery();
        } else {
            self.modal.clear();
            self.modal_context.clear();
        }
    }
}

#[cfg(test)]
mod reason_tests {
    use super::*;

    #[test]
    fn typed_io_causes_survive_unrelated_context_and_remain_safe_codes() {
        for (kind, expected) in [
            (
                std::io::ErrorKind::NotFound,
                ReaderFailureReason::MissingFile,
            ),
            (std::io::ErrorKind::TimedOut, ReaderFailureReason::Timeout),
            (
                std::io::ErrorKind::StorageFull,
                ReaderFailureReason::Storage,
            ),
        ] {
            let error = anyhow::Error::from(std::io::Error::from(kind))
                .context("A private source URL and token must not enter the reason");
            let reason = ReaderFailureReason::classify(&error);
            assert_eq!(reason, expected);
            let serialized = serde_json::to_string(&reason).unwrap();
            assert!(!serialized.contains("private"));
            assert!(!serialized.contains("token"));
        }
    }
}
