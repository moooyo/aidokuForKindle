use crate::{
    domain::{Preferences, ReaderSession},
    reader::ReaderState,
};

/// A bounded projection of the retained image. No Store, source client, image
/// decoder, or collection model is available to this first-frame operation.
#[derive(Debug, PartialEq)]
pub(super) struct RetainedReaderScene {
    pub book_id: Option<String>,
    pub chapter_id: String,
    pub chapter_number: String,
    pub chapter_title: String,
    pub page: u32,
    pub total: u32,
    pub landscape: bool,
    pub spread: bool,
    pub reading_direction: String,
    pub image_ready: bool,
}

impl RetainedReaderScene {
    pub(super) fn from_memory(
        displayed: Option<&ReaderSession>,
        reader: Option<&ReaderState>,
        preferences: &Preferences,
    ) -> Self {
        let current = reader.map(ReaderState::current_chapter);
        let (book_id, chapter_id, page, total, landscape, spread) = if let Some(frame) = displayed {
            (
                Some(frame.book_id.clone()),
                frame.chapter_id.clone(),
                frame.page,
                frame.total,
                frame.landscape,
                frame.spread,
            )
        } else {
            (
                current.map(|chapter| chapter.book_id.clone()),
                current
                    .map(|chapter| chapter.id.clone())
                    .unwrap_or_default(),
                reader.map_or(1, |reader| reader.page),
                reader.map_or(1, |reader| reader.total),
                preferences.landscape,
                preferences.spread,
            )
        };
        // The normal suspend checkpoint already restored the displayed chapter.
        // A pending cross-chapter frame can still be resolved within RAM only.
        let chapter = current
            .filter(|chapter| {
                chapter.id == chapter_id && Some(&chapter.book_id) == book_id.as_ref()
            })
            .or_else(|| {
                reader.and_then(|reader| {
                    reader.chapters().iter().find(|chapter| {
                        chapter.id == chapter_id && Some(&chapter.book_id) == book_id.as_ref()
                    })
                })
            });
        let total = total.max(1);
        Self {
            book_id,
            chapter_id,
            chapter_number: chapter
                .map(|chapter| {
                    if chapter.number.fract().abs() < f64::EPSILON {
                        format!("{:.0}", chapter.number)
                    } else {
                        chapter.number.to_string()
                    }
                })
                .unwrap_or_default(),
            chapter_title: chapter
                .map(|chapter| chapter.title.clone())
                .unwrap_or_default(),
            page: page.clamp(1, total),
            total,
            landscape,
            spread,
            reading_direction: preferences.reading_direction.clone(),
            image_ready: displayed.is_some(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::Chapter;

    fn pending_reader() -> ReaderState {
        let chapters = [1, 2]
            .into_iter()
            .map(|number| Chapter {
                id: format!("chapter-{number}"),
                book_id: "book".into(),
                number: f64::from(number),
                title: format!("Chapter {number}"),
                page_count: 20,
                ..Chapter::default()
            })
            .collect();
        let mut reader =
            ReaderState::new(chapters, "chapter-2", 8, &Preferences::default()).unwrap();
        reader.menu_open = true;
        reader.pending_page = Some(12);
        reader.zoom = 2.0;
        reader.vertical_offset = 1024;
        reader
    }

    #[test]
    fn retained_pixels_win_over_pending_chapter_preview_and_preferences() {
        let reader = pending_reader();
        let displayed = ReaderSession {
            book_id: "book".into(),
            chapter_id: "chapter-1".into(),
            page: 7,
            total: 17,
            landscape: true,
            spread: true,
            zoom: 2.0,
            vertical_offset: 1024,
            ..ReaderSession::default()
        };
        let scene = RetainedReaderScene::from_memory(
            Some(&displayed),
            Some(&reader),
            &Preferences::default(),
        );
        assert_eq!(scene.chapter_id, displayed.chapter_id);
        assert_eq!((scene.page, scene.total), (7, 17));
        assert_eq!(scene.chapter_title, "Chapter 1");
        assert!(scene.landscape && scene.spread && scene.image_ready);
        // The projection cannot mutate the in-memory viewport or pending input.
        assert_eq!(
            (
                reader.page,
                reader.zoom,
                reader.vertical_offset,
                reader.pending_page
            ),
            (8, 2.0, 1024, Some(12))
        );
    }

    #[test]
    fn no_frame_keeps_requested_position_and_requires_the_static_loader() {
        let reader = pending_reader();
        let preferences = Preferences {
            reading_direction: "vertical".into(),
            landscape: true,
            spread: false,
            ..Preferences::default()
        };
        let scene = RetainedReaderScene::from_memory(None, Some(&reader), &preferences);
        assert!(!scene.image_ready);
        assert_eq!((scene.page, scene.total), (8, 20));
        assert_eq!(scene.chapter_id, "chapter-2");
        assert!(scene.landscape);
        assert!(!scene.spread);
        assert_eq!(scene.reading_direction, "vertical");
    }

    #[test]
    fn missing_metadata_never_falls_back_to_another_chapters_title() {
        let reader = pending_reader();
        let displayed = ReaderSession {
            book_id: "other-book".into(),
            chapter_id: "retained-chapter".into(),
            page: 3,
            total: 9,
            ..ReaderSession::default()
        };
        let scene = RetainedReaderScene::from_memory(
            Some(&displayed),
            Some(&reader),
            &Preferences::default(),
        );
        assert_eq!(scene.book_id, Some("other-book".into()));
        assert_eq!(scene.chapter_id, "retained-chapter");
        assert!(scene.chapter_number.is_empty() && scene.chapter_title.is_empty());
        assert_eq!((scene.page, scene.total), (3, 9));
        assert!(scene.image_ready);
    }
}
