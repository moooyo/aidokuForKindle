//! Persistent fast frames and native page-animation selection.

use super::*;
use crate::display::DisplayMode;

impl App {
    /// Fast frames remain monochrome until a later publication replaces them.
    pub(super) fn publish_display_pixels(
        &self,
        ui: &AppWindow,
        first: prepared_pixels::PreparedPixels,
        second: Option<prepared_pixels::PreparedPixels>,
        preferences: &Preferences,
    ) -> bool {
        let _preparation = crate::reader_timing::Stage::new(
            crate::reader_timing::token(self.reader_generation, self.lifecycle_generation),
            "display_pixels_prepared",
        );
        let fast = preferences.display_mode == DisplayMode::Fast;
        let image = |pixels: prepared_pixels::PreparedPixels| {
            if fast {
                pixels.into_fast_image()
            } else {
                pixels.into_image()
            }
        };
        let state = ui.global::<State>();
        state.set_reader_image(image(first));
        state.set_reader_image_next(second.map(image).unwrap_or_default());
        state.set_reader_fast_frame(fast);
        fast
    }

    pub(super) fn page_animation_direction(&self, preferences: &Preferences) -> Option<bool> {
        if preferences.display_mode != DisplayMode::Kindle
            || matches!(
                preferences.reading_direction.as_str(),
                "vertical" | "vertical-pages"
            )
        {
            return None;
        }
        let previous = self.displayed_reader.as_ref()?;
        let reader = self.reader.as_ref()?;
        let old_chapter = reader
            .chapters()
            .iter()
            .position(|chapter| chapter.id == previous.session.chapter_id)?;
        let old = (
            old_chapter,
            previous.session.page,
            previous.session.wide_half,
        );
        let new = (reader.chapter_index(), reader.page, reader.wide_half);
        if old == new {
            return None;
        }
        Some((new > old) != (preferences.reading_direction == "rtl"))
    }
}
