use std::time::Duration;

use anyhow::{Context, Result, ensure};

use crate::domain::{Chapter, Preferences, Progress};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReadingDirection {
    RightToLeft,
    LeftToRight,
    /// Whole pages advanced with vertical taps and swipes.
    VerticalPages,
    /// A continuous strip advanced by overlapping viewport sections.
    Vertical,
}

impl ReadingDirection {
    pub fn from_preference(value: &str) -> Self {
        match value {
            "ltr" => Self::LeftToRight,
            "vertical-pages" => Self::VerticalPages,
            "vertical" => Self::Vertical,
            _ => Self::RightToLeft,
        }
    }

    fn is_vertical(self) -> bool {
        matches!(self, Self::VerticalPages | Self::Vertical)
    }
}

/// Source-page geometry shared by foreground frames and bounded read-ahead.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct PageLayout {
    spread: bool,
    cover_alone: bool,
}

impl PageLayout {
    pub(crate) fn new(spread: bool, cover_alone: bool) -> Self {
        Self {
            spread,
            cover_alone,
        }
    }

    pub(crate) fn from_preferences(preferences: &Preferences) -> Self {
        Self::new(
            preferences.spread
                && !ReadingDirection::from_preference(&preferences.reading_direction).is_vertical(),
            preferences.cover_alone,
        )
    }

    pub(crate) fn first_index(self, index: usize) -> usize {
        if self.spread && self.cover_alone && index > 0 {
            index - (index - 1) % 2
        } else {
            index
        }
    }

    pub(crate) fn window_limit(self, index: usize) -> usize {
        if self.spread && !(self.cover_alone && index == 0) {
            2
        } else {
            1
        }
    }

    pub(crate) fn page_count(self, first_index: usize, total: usize) -> usize {
        total
            .saturating_sub(first_index)
            .min(self.window_limit(first_index))
    }

    fn previous_index(self, first_index: usize) -> usize {
        first_index.saturating_sub(if self.spread { 2 } else { 1 })
    }

    fn last_index(self, total: usize) -> usize {
        if self.spread && self.cover_alone && total > 1 {
            1 + (total - 2) / 2 * 2
        } else if self.spread {
            total.saturating_sub(1) / 2 * 2
        } else {
            total.saturating_sub(1)
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TapLayout {
    Thirds,
    LShaped,
    SwipeOnly,
}

impl TapLayout {
    fn from_preference(value: &str) -> Self {
        match value {
            "l-shaped" => Self::LShaped,
            "swipe-only" => Self::SwipeOnly,
            _ => Self::Thirds,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TapRegion {
    None,
    Menu,
    Turn(bool),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReaderAction {
    None,
    PageChanged,
    ChapterChanged,
    MenuOpened,
    MenuClosed,
    PageMenuOpened,
    ZoomChanged,
    PreviewChanged,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RefreshRequest {
    None,
    Fast,
    Ui,
    Partial,
    Full,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReaderUpdate {
    pub action: ReaderAction,
    pub refresh: RefreshRequest,
}

impl ReaderUpdate {
    fn new(action: ReaderAction, refresh: RefreshRequest) -> Self {
        Self { action, refresh }
    }

    fn unchanged() -> Self {
        Self::new(ReaderAction::None, RefreshRequest::None)
    }
}

#[derive(Debug, Clone)]
pub struct ReaderState {
    chapters: Vec<Chapter>,
    chapter_index: usize,
    pub page: u32,
    pub total: u32,
    pub menu_open: bool,
    pub direction: ReadingDirection,
    /// Legacy preference retained for data compatibility; comic taps never use it.
    pub footer_mode: u8,
    pub landscape: bool,
    /// Effective page layout, resolved by the application for the active device.
    pub spread: bool,
    pub cover_alone: bool,
    pub vertical_offset: u32,
    pub image_height: u32,
    pub viewport_height: u32,
    pub zoom: f32,
    pub pending_page: Option<u32>,
    refresh_every: u32,
    turns_since_full: u32,
    flash_on_close: bool,
    tap_layout: TapLayout,
    reverse_taps: bool,
    double_tap_zoom: bool,
    pending_vertical_bottom: bool,
}

impl ReaderState {
    pub fn new(
        mut chapters: Vec<Chapter>,
        chapter_id: &str,
        page: u32,
        preferences: &Preferences,
    ) -> Result<Self> {
        ensure!(!chapters.is_empty(), "Reader requires at least one chapter");
        chapters.sort_by(|first, second| {
            first
                .number
                .total_cmp(&second.number)
                .then_with(|| first.id.cmp(&second.id))
        });
        let chapter_index = chapters
            .iter()
            .position(|chapter| chapter.id == chapter_id)
            .context("Selected chapter does not exist")?;
        let total = chapters[chapter_index].page_count.max(1);
        Ok(Self {
            chapters,
            chapter_index,
            page: page.clamp(1, total),
            total,
            menu_open: false,
            direction: ReadingDirection::from_preference(&preferences.reading_direction),
            footer_mode: preferences.footer_mode % 4,
            landscape: preferences.landscape,
            spread: preferences.spread,
            cover_alone: preferences.cover_alone,
            vertical_offset: 0,
            image_height: 0,
            viewport_height: 1,
            zoom: 1.0,
            pending_page: None,
            refresh_every: preferences.refresh_every,
            turns_since_full: 0,
            flash_on_close: preferences.flash_on_close,
            tap_layout: TapLayout::from_preference(&preferences.tap_layout),
            reverse_taps: preferences.reverse_taps
                || matches!(preferences.tap_layout.as_str(), "reverse" | "reversed"),
            double_tap_zoom: preferences.double_tap_zoom,
            pending_vertical_bottom: false,
        })
    }

    pub fn current_chapter(&self) -> &Chapter {
        &self.chapters[self.chapter_index]
    }

    pub fn chapter_index(&self) -> usize {
        self.chapter_index
    }

    pub fn chapters(&self) -> &[Chapter] {
        &self.chapters
    }

    /// Resolve the frame without rewriting a retained source-page checkpoint.
    pub fn visible_pages(&self) -> std::ops::RangeInclusive<u32> {
        let first = self.frame_page(self.page);
        first..=first.saturating_add(self.page_step() - 1).min(self.total)
    }

    pub fn set_total(&mut self, total: u32) {
        self.total = total.max(1);
        self.chapters[self.chapter_index].page_count = self.total;
        self.page = self.page.clamp(1, self.total);
    }

    pub fn set_vertical_dimensions(&mut self, image_height: u32, viewport_height: u32) {
        self.viewport_height = viewport_height.max(1);
        if self.direction != ReadingDirection::Vertical {
            self.reset_vertical_geometry();
            return;
        }
        self.image_height = image_height;
        self.vertical_offset = if self.pending_vertical_bottom {
            self.maximum_vertical_offset()
        } else {
            self.vertical_offset.min(self.maximum_vertical_offset())
        };
        self.pending_vertical_bottom = false;
    }

    pub fn apply_preferences(&mut self, preferences: &Preferences) {
        let direction = ReadingDirection::from_preference(&preferences.reading_direction);
        if self.direction != direction {
            self.reset_vertical_geometry();
        }
        self.direction = direction;
        self.landscape = preferences.landscape;
        self.spread = preferences.spread;
        self.cover_alone = preferences.cover_alone;
        self.refresh_every = preferences.refresh_every;
        self.flash_on_close = preferences.flash_on_close;
        self.tap_layout = TapLayout::from_preference(&preferences.tap_layout);
        self.reverse_taps = preferences.reverse_taps
            || matches!(preferences.tap_layout.as_str(), "reverse" | "reversed");
        self.double_tap_zoom = preferences.double_tap_zoom;
        self.footer_mode = preferences.footer_mode % 4;
    }

    /// Coordinates cover the complete comic viewport. No corner is reserved for
    /// a footer or a bookmark: all image regions obey the reading direction.
    pub fn tap(&mut self, x: f32, y: f32) -> ReaderUpdate {
        if !x.is_finite()
            || !y.is_finite()
            || !(0.0..=1.0).contains(&x)
            || !(0.0..=1.0).contains(&y)
        {
            return ReaderUpdate::unchanged();
        }
        if self.menu_open {
            // Actual menu controls handle their own events before this surface.
            // Tapping the exposed comic dismisses chrome without turning a page.
            return self.close_menu();
        }
        match self.tap_region(x, y) {
            TapRegion::None => ReaderUpdate::unchanged(),
            TapRegion::Menu => self.open_menu(),
            TapRegion::Turn(advance) => self.turn_with_reversal(advance),
        }
    }

    fn tap_region(&self, x: f32, y: f32) -> TapRegion {
        let advance = if self.direction.is_vertical() {
            if (1.0 / 3.0..=2.0 / 3.0).contains(&y) {
                return TapRegion::Menu;
            }
            y > 2.0 / 3.0
        } else if self.tap_layout == TapLayout::LShaped && y > 2.0 / 3.0 {
            true
        } else {
            if self.center_bounds().contains(&x) {
                return TapRegion::Menu;
            }
            (self.direction == ReadingDirection::RightToLeft) == (x < 0.5)
        };
        if self.tap_layout == TapLayout::SwipeOnly {
            TapRegion::None
        } else {
            TapRegion::Turn(advance)
        }
    }

    fn turn_with_reversal(&mut self, advance: bool) -> ReaderUpdate {
        if advance != self.reverse_taps {
            self.next_page()
        } else {
            self.previous_page()
        }
    }

    pub fn open_menu(&mut self) -> ReaderUpdate {
        if self.menu_open {
            return ReaderUpdate::unchanged();
        }
        self.menu_open = true;
        ReaderUpdate::new(ReaderAction::MenuOpened, RefreshRequest::Ui)
    }

    pub fn close_menu(&mut self) -> ReaderUpdate {
        if !self.menu_open {
            return ReaderUpdate::unchanged();
        }
        self.menu_open = false;
        ReaderUpdate::new(
            ReaderAction::MenuClosed,
            if self.flash_on_close {
                RefreshRequest::Full
            } else {
                RefreshRequest::Ui
            },
        )
    }

    pub fn toggle_menu(&mut self) -> ReaderUpdate {
        if self.menu_open {
            self.close_menu()
        } else {
            self.open_menu()
        }
    }

    pub fn next_page(&mut self) -> ReaderUpdate {
        if self.direction == ReadingDirection::Vertical
            && self.vertical_offset < self.maximum_vertical_offset()
        {
            self.vertical_offset = self
                .vertical_offset
                .saturating_add(self.vertical_step())
                .min(self.maximum_vertical_offset());
            return self.page_changed();
        }
        if *self.visible_pages().end() < self.total {
            self.page = self.visible_pages().end().saturating_add(1).min(self.total);
            self.reset_page_geometry();
            return self.page_changed();
        }
        self.next_chapter()
    }

    pub fn previous_page(&mut self) -> ReaderUpdate {
        if self.direction == ReadingDirection::Vertical && self.vertical_offset > 0 {
            self.vertical_offset = self.vertical_offset.saturating_sub(self.vertical_step());
            return self.page_changed();
        }
        let first = *self.visible_pages().start();
        if first > 1 {
            self.page = self.page_layout().previous_index(first as usize - 1) as u32 + 1;
            self.reset_page_geometry();
            self.pending_vertical_bottom = self.direction == ReadingDirection::Vertical;
            return self.page_changed();
        }
        self.previous_chapter()
    }

    pub fn next_chapter(&mut self) -> ReaderUpdate {
        if self.chapter_index + 1 >= self.chapters.len() {
            return ReaderUpdate::unchanged();
        }
        self.chapter_index += 1;
        self.total = self.current_chapter().page_count.max(1);
        self.page = 1;
        self.reset_page_geometry();
        self.turns_since_full = 0;
        ReaderUpdate::new(ReaderAction::ChapterChanged, RefreshRequest::Full)
    }

    pub fn previous_chapter(&mut self) -> ReaderUpdate {
        if self.chapter_index == 0 {
            return ReaderUpdate::unchanged();
        }
        self.chapter_index -= 1;
        self.total = self.current_chapter().page_count.max(1);
        self.page = self.page_layout().last_index(self.total as usize) as u32 + 1;
        self.reset_page_geometry();
        self.pending_vertical_bottom = self.direction == ReadingDirection::Vertical;
        self.turns_since_full = 0;
        ReaderUpdate::new(ReaderAction::ChapterChanged, RefreshRequest::Full)
    }

    pub fn jump_to_page(&mut self, page: u32) -> ReaderUpdate {
        let target = self.frame_page(page);
        if target == self.page && self.vertical_offset == 0 {
            return ReaderUpdate::unchanged();
        }
        self.page = target;
        self.reset_page_geometry();
        self.page_changed()
    }

    /// A preview changes the page label; decoding waits until commit_preview.
    pub fn preview_page(&mut self, page: u32) -> ReaderUpdate {
        self.pending_page = Some(self.frame_page(page));
        ReaderUpdate::new(ReaderAction::PreviewChanged, RefreshRequest::Fast)
    }

    pub fn commit_preview(&mut self) -> ReaderUpdate {
        match self.pending_page.take() {
            Some(page) => self.jump_to_page(page),
            None => ReaderUpdate::unchanged(),
        }
    }

    /// Deltas use design pixels. A 150 ppi display is 300 physical ppi at 2x.
    pub fn swipe(
        &mut self,
        delta_x: f32,
        delta_y: f32,
        elapsed: Duration,
        design_ppi: f32,
    ) -> ReaderUpdate {
        if self.menu_open
            || elapsed > Duration::from_millis(900)
            || !delta_x.is_finite()
            || !delta_y.is_finite()
            || !design_ppi.is_finite()
            || design_ppi <= 0.0
        {
            return ReaderUpdate::unchanged();
        }
        let threshold = 5.6 / 25.4 * design_ppi;
        if self.direction.is_vertical() {
            if delta_y.abs() < threshold || delta_y.abs() < delta_x.abs() {
                return ReaderUpdate::unchanged();
            }
            return self.turn_with_reversal(delta_y < 0.0);
        }
        if delta_x.abs() < threshold || delta_x.abs() < delta_y.abs() {
            return ReaderUpdate::unchanged();
        }
        let advance = (delta_x > 0.0) == (self.direction == ReadingDirection::RightToLeft);
        self.turn_with_reversal(advance)
    }

    pub fn top_edge_swipe(
        &mut self,
        start_y: f32,
        delta_y: f32,
        elapsed: Duration,
        design_ppi: f32,
    ) -> ReaderUpdate {
        if start_y.is_finite()
            && delta_y.is_finite()
            && design_ppi.is_finite()
            && design_ppi > 0.0
            && start_y >= 0.0
            && start_y <= 8.0 / 25.4 * design_ppi
            && delta_y >= 5.6 / 25.4 * design_ppi
            && elapsed <= Duration::from_millis(900)
        {
            self.open_menu()
        } else {
            ReaderUpdate::unchanged()
        }
    }

    pub fn long_press(&mut self, elapsed: Duration) -> ReaderUpdate {
        if elapsed < Duration::from_millis(500) {
            return ReaderUpdate::unchanged();
        }
        self.menu_open = true;
        ReaderUpdate::new(ReaderAction::PageMenuOpened, RefreshRequest::Ui)
    }

    pub fn double_tap(&mut self, elapsed: Duration) -> ReaderUpdate {
        if !self.double_tap_zoom || elapsed > Duration::from_millis(300) {
            return ReaderUpdate::unchanged();
        }
        self.zoom = if self.zoom > 1.0 { 1.0 } else { 2.0 };
        self.turns_since_full = 0;
        ReaderUpdate::new(ReaderAction::ZoomChanged, RefreshRequest::Full)
    }

    pub fn set_zoom(&mut self, zoom: f32) -> ReaderUpdate {
        if !zoom.is_finite() {
            return ReaderUpdate::unchanged();
        }
        self.zoom = zoom.clamp(1.0, 4.0);
        self.turns_since_full = 0;
        ReaderUpdate::new(ReaderAction::ZoomChanged, RefreshRequest::Full)
    }

    pub fn progress(&self, updated_at: i64) -> Progress {
        Progress {
            book_id: self.current_chapter().book_id.clone(),
            chapter_id: self.current_chapter().id.clone(),
            page: self.page,
            total: self.total,
            updated_at,
            completed: *self.visible_pages().end() >= self.total
                && (self.direction != ReadingDirection::Vertical
                    || self.vertical_offset >= self.maximum_vertical_offset()),
        }
    }

    fn center_bounds(&self) -> std::ops::RangeInclusive<f32> {
        if self.page_step() == 2 {
            0.3..=0.7
        } else {
            1.0 / 3.0..=2.0 / 3.0
        }
    }

    fn frame_page(&self, page: u32) -> u32 {
        let index = page.clamp(1, self.total).saturating_sub(1) as usize;
        self.page_layout().first_index(index) as u32 + 1
    }

    fn page_step(&self) -> u32 {
        let first = self.frame_page(self.page).saturating_sub(1) as usize;
        self.page_layout().page_count(first, self.total as usize) as u32
    }

    fn page_layout(&self) -> PageLayout {
        PageLayout::new(
            self.spread && !self.direction.is_vertical(),
            self.cover_alone,
        )
    }

    fn vertical_step(&self) -> u32 {
        ((self.viewport_height as f64 * 0.85).round() as u32).max(1)
    }

    fn maximum_vertical_offset(&self) -> u32 {
        self.image_height.saturating_sub(self.viewport_height)
    }

    fn reset_vertical_geometry(&mut self) {
        self.vertical_offset = 0;
        self.image_height = 0;
        self.pending_vertical_bottom = false;
    }

    fn reset_page_geometry(&mut self) {
        self.reset_vertical_geometry();
        self.zoom = 1.0;
    }

    fn page_changed(&mut self) -> ReaderUpdate {
        self.turns_since_full += 1;
        let refresh = if self.refresh_every > 0 && self.turns_since_full >= self.refresh_every {
            self.turns_since_full = 0;
            RefreshRequest::Full
        } else {
            RefreshRequest::Partial
        };
        ReaderUpdate::new(ReaderAction::PageChanged, refresh)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn single_page_preferences() -> Preferences {
        Preferences {
            spread: false,
            ..Preferences::default()
        }
    }

    fn reader(preferences: Preferences) -> ReaderState {
        let chapters = (1..=3)
            .map(|number| Chapter {
                id: format!("chapter-{number}"),
                book_id: "book".into(),
                number: f64::from(number),
                page_count: 10,
                ..Chapter::default()
            })
            .collect();
        ReaderState::new(chapters, "chapter-2", 4, &preferences).unwrap()
    }

    fn bounded_reader(total: u32, preferences: &Preferences) -> ReaderState {
        ReaderState::new(
            vec![Chapter {
                id: "chapter".into(),
                page_count: total,
                ..Chapter::default()
            }],
            "chapter",
            1,
            preferences,
        )
        .unwrap()
    }

    #[test]
    fn taps_follow_direction_and_menus_block_turns() {
        let mut state = reader(single_page_preferences());
        assert_eq!(state.tap(0.1, 0.5).action, ReaderAction::PageChanged);
        assert_eq!(state.page, 5);
        state.tap(0.5, 0.5);
        assert!(state.menu_open);
        assert_eq!(state.tap(0.1, 0.5).action, ReaderAction::MenuClosed);
        assert_eq!(state.page, 5);
        state.open_menu();
        assert_eq!(state.tap(0.5, 0.5).action, ReaderAction::MenuClosed);
        state.direction = ReadingDirection::LeftToRight;
        state.tap(0.9, 0.5);
        assert_eq!(state.page, 6);
    }

    #[test]
    fn default_tap_regions_use_thirds_and_reversal_also_applies_to_swipes() {
        let mut state = reader(Preferences {
            tap_layout: "reverse".into(),
            ..single_page_preferences()
        });
        state.tap(0.35, 0.5);
        assert!(state.menu_open);
        state.close_menu();
        state.tap(0.1, 0.5);
        assert_eq!(state.page, 3);
        state.swipe(40.0, 0.0, Duration::from_millis(200), 150.0);
        assert_eq!(state.page, 2);
        state.direction = ReadingDirection::Vertical;
        state.tap(0.5, 0.35);
        assert!(state.menu_open);
    }

    #[test]
    fn fullscreen_has_no_reserved_corner_for_legacy_footer_modes() {
        for mode in 0..4 {
            let mut rtl = reader(Preferences {
                footer_mode: mode,
                ..single_page_preferences()
            });
            assert!(!rtl.menu_open);
            assert_eq!(rtl.tap(0.9, 0.97).action, ReaderAction::PageChanged);
            assert_eq!(rtl.footer_mode, mode);
            assert_eq!(rtl.page, 3);
            assert!(!rtl.menu_open);

            let mut ltr = reader(Preferences {
                footer_mode: mode,
                reading_direction: "ltr".into(),
                ..single_page_preferences()
            });
            assert_eq!(ltr.tap(0.9, 0.97).action, ReaderAction::PageChanged);
            assert_eq!(ltr.footer_mode, mode);
            assert_eq!(ltr.page, 5);
            assert!(!ltr.menu_open);

            let mut vertical = reader(Preferences {
                footer_mode: mode,
                reading_direction: "vertical".into(),
                ..single_page_preferences()
            });
            vertical.set_vertical_dimensions(2_000, 800);
            assert_eq!(vertical.tap(0.9, 0.97).action, ReaderAction::PageChanged);
            assert_eq!(vertical.footer_mode, mode);
            assert_eq!(vertical.vertical_offset, 680);
            assert_eq!(vertical.page, 4);
            assert!(!vertical.menu_open);
        }
    }

    #[test]
    fn fullscreen_menu_is_explicit_and_close_preserves_the_visible_page() {
        let mut state = reader(single_page_preferences());
        assert!(!state.menu_open);
        assert_eq!(state.tap(0.5, 0.5).action, ReaderAction::MenuOpened);
        assert_eq!(state.tap(0.96, 0.98).action, ReaderAction::MenuClosed);
        assert_eq!(state.page, 4);
        assert!(!state.menu_open);
        assert_eq!(
            state
                .top_edge_swipe(8.0, 40.0, Duration::from_millis(200), 150.0)
                .action,
            ReaderAction::MenuOpened
        );
        state.close_menu();
        assert_eq!(
            state.long_press(Duration::from_millis(499)).action,
            ReaderAction::None
        );
        assert!(!state.menu_open);
        assert_eq!(
            state.long_press(Duration::from_millis(500)).action,
            ReaderAction::PageMenuOpened
        );
        state.close_menu();
        assert_eq!(state.page, 4);
        assert!(!state.menu_open);
    }

    #[test]
    fn chapter_boundaries_use_full_refresh_and_stay_bounded() {
        let mut state = reader(single_page_preferences());
        state.jump_to_page(10);
        let change = state.next_page();
        assert_eq!(change.action, ReaderAction::ChapterChanged);
        assert_eq!(change.refresh, RefreshRequest::Full);
        assert_eq!(state.current_chapter().id, "chapter-3");
        assert_eq!(state.page, 1);
        state.jump_to_page(10);
        assert_eq!(state.next_page().action, ReaderAction::None);
    }

    #[test]
    fn vertical_turns_keep_fifteen_percent_overlap() {
        let mut state = reader(Preferences {
            reading_direction: "vertical".into(),
            landscape: true,
            spread: true,
            ..Preferences::default()
        });
        assert_eq!(state.visible_pages(), 4..=4);
        state.set_vertical_dimensions(2_000, 800);
        state.next_page();
        assert_eq!(state.vertical_offset, 680);
        assert_eq!(state.page, 4);
        state.next_page();
        assert_eq!(state.vertical_offset, 1_200);
        state.next_page();
        assert_eq!(state.page, 5);
        assert_eq!(state.vertical_offset, 0);
        state.previous_page();
        state.set_vertical_dimensions(2_000, 800);
        assert_eq!(state.page, 4);
        assert_eq!(state.vertical_offset, 1_200);
    }

    #[test]
    fn vertical_page_taps_and_swipes_turn_whole_pages_without_strip_geometry() {
        let mut state = reader(Preferences {
            reading_direction: "vertical-pages".into(),
            landscape: true,
            spread: true,
            ..Preferences::default()
        });
        assert_eq!(state.direction, ReadingDirection::VerticalPages);
        assert_eq!(state.visible_pages(), 4..=4);
        state.set_vertical_dimensions(2_000, 800);
        assert_eq!(state.image_height, 0);
        assert_eq!(state.vertical_offset, 0);
        state.tap(0.05, 0.5);
        assert!(state.menu_open);
        state.close_menu();
        assert_eq!(state.tap(0.95, 0.97).action, ReaderAction::PageChanged);
        assert_eq!(state.page, 5);
        assert_eq!(state.vertical_offset, 0);
        assert_eq!(state.tap(0.95, 0.03).action, ReaderAction::PageChanged);
        assert_eq!(state.page, 4);
        assert_eq!(
            state
                .swipe(40.0, 0.0, Duration::from_millis(200), 150.0)
                .action,
            ReaderAction::None
        );
        assert_eq!(
            state
                .swipe(0.0, -40.0, Duration::from_millis(200), 150.0)
                .action,
            ReaderAction::PageChanged
        );
        assert_eq!(state.page, 5);
        state.swipe(0.0, 40.0, Duration::from_millis(200), 150.0);
        assert_eq!(state.page, 4);
        assert_eq!(state.visible_pages(), 4..=4);
        assert_eq!(state.vertical_offset, 0);
    }

    #[test]
    fn vertical_page_progress_and_chapter_boundaries_do_not_wait_for_strip_offsets() {
        let mut pages = reader(Preferences {
            reading_direction: "vertical-pages".into(),
            spread: true,
            ..Preferences::default()
        });
        pages.jump_to_page(10);
        pages.set_vertical_dimensions(2_000, 800);
        assert!(pages.progress(10).completed);
        assert_eq!(pages.next_page().action, ReaderAction::ChapterChanged);
        assert_eq!(pages.page, 1);
        assert_eq!(pages.previous_page().action, ReaderAction::ChapterChanged);
        assert_eq!(pages.page, 10);
        pages.set_vertical_dimensions(2_000, 800);
        assert_eq!(pages.vertical_offset, 0);
        assert!(pages.progress(11).completed);

        let mut strip = reader(Preferences {
            reading_direction: "vertical".into(),
            ..single_page_preferences()
        });
        strip.jump_to_page(10);
        strip.set_vertical_dimensions(2_000, 800);
        assert!(!strip.progress(10).completed);
        strip.next_page();
        assert!(!strip.progress(11).completed);
        strip.next_page();
        assert!(strip.progress(12).completed);
        assert_eq!(strip.page, 10);
    }

    #[test]
    fn changing_reading_modes_discards_strip_offsets_and_pending_bottom_alignment() {
        let mut preferences = Preferences {
            reading_direction: "vertical".into(),
            ..single_page_preferences()
        };
        let mut state = reader(preferences.clone());
        state.set_vertical_dimensions(2_000, 800);
        state.next_page();
        assert_eq!(state.vertical_offset, 680);
        state.set_zoom(2.0);
        preferences.reading_direction = "vertical-pages".into();
        state.apply_preferences(&preferences);
        assert_eq!(state.page, 4);
        assert_eq!(state.vertical_offset, 0);
        assert_eq!(state.image_height, 0);
        assert_eq!(state.zoom, 2.0);
        state.next_page();
        assert_eq!(state.page, 5);

        preferences.reading_direction = "vertical".into();
        state.apply_preferences(&preferences);
        state.previous_page();
        assert!(state.pending_vertical_bottom);
        preferences.reading_direction = "ltr".into();
        state.apply_preferences(&preferences);
        assert!(!state.pending_vertical_bottom);
        state.set_vertical_dimensions(2_000, 800);
        assert_eq!(state.vertical_offset, 0);
        state.next_page();
        assert_eq!(state.page, 5);
    }

    #[test]
    fn effective_spread_uses_wide_center_zone_and_two_page_steps_in_both_orientations() {
        for landscape in [false, true] {
            for direction in ["rtl", "ltr"] {
                let mut state = reader(Preferences {
                    reading_direction: direction.into(),
                    landscape,
                    spread: true,
                    ..Preferences::default()
                });
                state.tap(0.31, 0.5);
                assert!(state.menu_open);
                state.close_menu();
                state.tap(if direction == "rtl" { 0.29 } else { 0.71 }, 0.5);
                assert_eq!(state.page, 6);
                assert_eq!(state.visible_pages(), 6..=7);
                state.jump_to_page(9);
                assert!(state.progress(10).completed);
                assert_eq!(state.next_page().action, ReaderAction::ChapterChanged);
                assert_eq!(state.previous_page().action, ReaderAction::ChapterChanged);
                assert_eq!(state.visible_pages(), 9..=10);
            }
        }
    }

    #[test]
    fn changing_effective_spread_updates_turn_steps_and_tap_regions() {
        for landscape in [false, true] {
            let mut preferences = Preferences {
                landscape,
                ..single_page_preferences()
            };
            let mut state = reader(preferences.clone());
            state.tap(0.31, 0.5);
            assert_eq!(state.page, 5);
            assert!(!state.menu_open);
            assert_eq!(state.visible_pages(), 5..=5);
            preferences.spread = true;
            state.apply_preferences(&preferences);
            state.tap(0.31, 0.5);
            assert!(state.menu_open);
            state.close_menu();
            state.next_page();
            assert_eq!(state.visible_pages(), 7..=8);
            preferences.spread = false;
            state.apply_preferences(&preferences);
            state.next_page();
            assert_eq!(state.visible_pages(), 8..=8);
        }
    }

    #[test]
    fn slider_preview_does_not_change_decoded_page() {
        let mut state = reader(single_page_preferences());
        assert_eq!(state.preview_page(8).refresh, RefreshRequest::Fast);
        assert_eq!(state.page, 4);
        assert_eq!(state.pending_page, Some(8));
        state.commit_preview();
        assert_eq!(state.page, 8);
        assert_eq!(state.pending_page, None);
    }

    #[test]
    fn isolated_cover_turns_are_reversible_in_portrait_and_landscape_spreads() {
        for landscape in [false, true] {
            for direction in ["rtl", "ltr"] {
                let mut state = bounded_reader(
                    5,
                    &Preferences {
                        reading_direction: direction.into(),
                        spread: true,
                        landscape,
                        cover_alone: true,
                        ..Preferences::default()
                    },
                );
                assert_eq!(state.visible_pages(), 1..=1);
                assert_eq!(state.page_step(), 1);
                state.next_page();
                assert_eq!(state.visible_pages(), 2..=3);
                state.next_page();
                assert_eq!(state.visible_pages(), 4..=5);
                assert!(state.progress(1).completed);
                assert_eq!(state.next_page().action, ReaderAction::None);
                state.previous_page();
                assert_eq!(state.visible_pages(), 2..=3);
                state.previous_page();
                assert_eq!(state.visible_pages(), 1..=1);
                assert_eq!(state.previous_page().action, ReaderAction::None);
                assert_eq!(state.total, 5);
                assert_eq!(state.current_chapter().page_count, 5);
            }
        }
    }

    #[test]
    fn isolated_cover_never_adds_a_phantom_source_page_at_either_parity() {
        let preferences = Preferences {
            cover_alone: true,
            spread: true,
            ..Preferences::default()
        };
        for total in [1, 2, 3, 4, 5, 6, 9, 10] {
            let mut state = bounded_reader(total, &preferences);
            let mut visited = Vec::new();
            loop {
                assert_eq!(state.page_step(), state.visible_pages().count() as u32);
                visited.extend(state.visible_pages());
                if state.next_page().action == ReaderAction::None {
                    break;
                }
            }
            assert_eq!(visited, (1..=total).collect::<Vec<_>>(), "{total}");
            assert_eq!(state.total, total);
            assert_eq!(state.progress(1).total, total);
            assert!(state.progress(1).completed);
            assert_eq!(*state.visible_pages().end(), total);
            while state.previous_page().action != ReaderAction::None {}
            assert_eq!(state.page, 1);
            assert_eq!(state.visible_pages(), 1..=1);
        }
    }

    #[test]
    fn isolated_cover_previous_chapter_uses_its_last_real_frame() {
        let preferences = Preferences {
            cover_alone: true,
            spread: true,
            ..Preferences::default()
        };
        for (total, last_start) in [(1, 1), (2, 2), (3, 2), (4, 4), (5, 4), (6, 6)] {
            let chapters = (1..=2)
                .map(|number| Chapter {
                    id: format!("chapter-{number}"),
                    number: f64::from(number),
                    page_count: total,
                    ..Chapter::default()
                })
                .collect();
            let mut state = ReaderState::new(chapters, "chapter-2", 1, &preferences).unwrap();
            assert_eq!(state.previous_page().action, ReaderAction::ChapterChanged);
            assert_eq!(state.page, last_start);
            assert_eq!(*state.visible_pages().end(), total);
            assert!(state.progress(1).completed);
            assert_eq!(state.next_page().action, ReaderAction::ChapterChanged);
            assert_eq!(state.page, 1);
            assert_eq!(state.total, total);
        }
    }

    #[test]
    fn cover_seek_and_preview_resolve_to_the_frame_containing_the_source_page() {
        let mut state = bounded_reader(
            6,
            &Preferences {
                cover_alone: true,
                spread: true,
                ..Preferences::default()
            },
        );
        for (requested, expected, last) in [(0, 1, 1), (3, 2, 3), (5, 4, 5), (u32::MAX, 6, 6)] {
            state.set_zoom(2.0);
            let original_page = state.page;
            state.preview_page(requested);
            assert_eq!(state.pending_page, Some(expected));
            assert_eq!(state.page, original_page);
            assert_eq!(state.zoom, 2.0);
            state.commit_preview();
            assert_eq!(state.page, expected);
            assert_eq!(state.visible_pages(), expected..=last);
            assert_eq!(state.pending_page, None);
        }
        state.jump_to_page(3);
        assert_eq!(state.visible_pages(), 2..=3);
        state.next_page();
        assert_eq!(state.visible_pages(), 4..=5);
    }

    #[test]
    fn cover_and_tap_preferences_preserve_retained_page_zoom_and_strip_offset() {
        for direction in ["rtl", "vertical", "vertical-pages"] {
            let mut preferences = Preferences {
                reading_direction: direction.into(),
                spread: true,
                ..Preferences::default()
            };
            let mut state = reader(preferences.clone());
            state.page = 3;
            state.set_vertical_dimensions(2_000, 800);
            if direction == "vertical" {
                state.next_page();
                assert_eq!(state.vertical_offset, 680);
            }
            state.set_zoom(2.0);
            let retained = (state.page, state.zoom, state.vertical_offset);
            for layout in ["l-shaped", "swipe-only", "thirds"] {
                preferences.cover_alone = true;
                preferences.tap_layout = layout.into();
                preferences.reverse_taps = true;
                state.apply_preferences(&preferences);
                assert_eq!((state.page, state.zoom, state.vertical_offset), retained);
                assert_eq!(
                    state.visible_pages(),
                    if direction == "rtl" { 2..=3 } else { 3..=3 }
                );
            }
        }
    }

    #[test]
    fn cover_preference_keeps_single_pages_and_both_vertical_modes_single() {
        for (direction, spread) in [
            ("rtl", false),
            ("ltr", false),
            ("vertical", true),
            ("vertical-pages", true),
        ] {
            let mut state = bounded_reader(
                5,
                &Preferences {
                    reading_direction: direction.into(),
                    spread,
                    cover_alone: true,
                    ..Preferences::default()
                },
            );
            for page in 1..=5 {
                assert_eq!(state.page, page);
                assert_eq!(state.visible_pages(), page..=page);
                state.next_page();
            }
            state.jump_to_page(3);
            assert_eq!(state.page, 3);
            assert_eq!(state.total, 5);
        }
    }

    #[test]
    fn l_shaped_taps_follow_horizontal_direction_and_independent_reversal() {
        for direction in ["rtl", "ltr"] {
            for reversed in [false, true] {
                let preferences = Preferences {
                    reading_direction: direction.into(),
                    tap_layout: "l-shaped".into(),
                    reverse_taps: reversed,
                    ..single_page_preferences()
                };
                for (x, y, advance) in [
                    (0.1, 0.1, direction == "rtl"),
                    (0.9, 0.1, direction == "ltr"),
                    (0.1, 0.9, true),
                    (0.5, 0.9, true),
                    (0.9, 0.9, true),
                ] {
                    let mut state = reader(preferences.clone());
                    assert_eq!(state.tap(x, y).action, ReaderAction::PageChanged);
                    assert_eq!(state.page, if advance != reversed { 5 } else { 3 });
                }
                let mut state = reader(preferences);
                assert_eq!(state.tap(0.5, 0.2).action, ReaderAction::MenuOpened);
                assert_eq!(state.tap(0.5, 0.9).action, ReaderAction::MenuClosed);
                assert_eq!(state.page, 4);
            }
        }
    }

    #[test]
    fn tap_regions_use_actual_spreads_and_exact_thirds_at_unpaired_pages() {
        for layout in ["thirds", "l-shaped", "swipe-only"] {
            let mut state = bounded_reader(
                4,
                &Preferences {
                    tap_layout: layout.into(),
                    cover_alone: true,
                    spread: true,
                    ..Preferences::default()
                },
            );
            for page in [1, 4] {
                state.jump_to_page(page);
                assert_eq!(state.center_bounds(), 1.0 / 3.0..=2.0 / 3.0);
                for x in [1.0 / 3.0, 2.0 / 3.0] {
                    assert_eq!(state.tap(x, 0.2).action, ReaderAction::MenuOpened);
                    state.close_menu();
                }
                assert_ne!(state.tap_region(0.31, 0.2), TapRegion::Menu);
            }
            state.jump_to_page(3);
            assert_eq!(state.visible_pages(), 2..=3);
            for x in [0.3, 0.31, 0.69, 0.7] {
                assert_eq!(state.tap(x, 0.2).action, ReaderAction::MenuOpened);
                state.close_menu();
            }
            if layout == "l-shaped" {
                assert_eq!(state.tap_region(0.5, 2.0 / 3.0), TapRegion::Menu);
                assert_eq!(state.tap_region(0.5, 0.667), TapRegion::Turn(true));
            }
        }
    }

    #[test]
    fn both_vertical_modes_keep_thirds_and_apply_tap_and_swipe_reversal() {
        for direction in ["vertical", "vertical-pages"] {
            for layout in ["thirds", "l-shaped", "swipe-only"] {
                for reversed in [false, true] {
                    let preferences = Preferences {
                        reading_direction: direction.into(),
                        tap_layout: layout.into(),
                        reverse_taps: reversed,
                        ..single_page_preferences()
                    };
                    for y in [1.0 / 3.0, 0.5, 2.0 / 3.0] {
                        let mut state = reader(preferences.clone());
                        assert_eq!(state.tap(0.9, y).action, ReaderAction::MenuOpened);
                    }
                    for (y, advance) in [(0.1, false), (0.9, true)] {
                        let mut state = reader(preferences.clone());
                        let update = state.tap(0.5, y);
                        if layout == "swipe-only" {
                            assert_eq!(update.action, ReaderAction::None);
                            assert_eq!(state.page, 4);
                        } else {
                            assert_eq!(state.page, if advance != reversed { 5 } else { 3 });
                        }
                    }
                    let mut state = reader(preferences);
                    state.swipe(0.0, -40.0, Duration::from_millis(200), 150.0);
                    assert_eq!(state.page, if reversed { 3 } else { 5 });
                }
            }
        }
    }

    #[test]
    fn swipe_only_blocks_page_taps_and_preserves_menu_swipe_zoom_and_hold_actions() {
        for direction in ["rtl", "ltr"] {
            let mut state = reader(Preferences {
                reading_direction: direction.into(),
                tap_layout: "swipe-only".into(),
                double_tap_zoom: true,
                ..single_page_preferences()
            });
            for (x, y) in [(0.1, 0.1), (0.9, 0.9), (0.1, 0.9)] {
                assert_eq!(state.tap(x, y), ReaderUpdate::unchanged());
                assert_eq!(state.page, 4);
            }
            assert_eq!(state.tap(0.5, 0.9).action, ReaderAction::MenuOpened);
            assert_eq!(state.tap(0.1, 0.1).action, ReaderAction::MenuClosed);
            let delta = if direction == "rtl" { 40.0 } else { -40.0 };
            state.swipe(delta, 0.0, Duration::from_millis(200), 150.0);
            assert_eq!(state.page, 5);
            assert_eq!(
                state.double_tap(Duration::from_millis(300)).action,
                ReaderAction::ZoomChanged
            );
            assert_eq!(
                state.long_press(Duration::from_millis(499)).action,
                ReaderAction::None
            );
            assert_eq!(
                state.long_press(Duration::from_millis(500)).action,
                ReaderAction::PageMenuOpened
            );
            assert_eq!(state.page, 5);
        }
    }

    #[test]
    fn new_tap_layouts_reject_invalid_coordinates_before_classification() {
        for layout in ["l-shaped", "swipe-only"] {
            let mut state = reader(Preferences {
                tap_layout: layout.into(),
                ..single_page_preferences()
            });
            for (x, y) in [
                (f32::NAN, 0.5),
                (0.5, f32::INFINITY),
                (-0.1, 0.5),
                (0.5, 1.1),
            ] {
                assert_eq!(state.tap(x, y), ReaderUpdate::unchanged());
                assert_eq!(state.page, 4);
                assert!(!state.menu_open);
            }
        }
    }

    #[test]
    fn refresh_interval_and_physical_gesture_thresholds_are_respected() {
        let mut state = reader(Preferences {
            refresh_every: 3,
            ..single_page_preferences()
        });
        assert_eq!(state.next_page().refresh, RefreshRequest::Partial);
        assert_eq!(state.next_page().refresh, RefreshRequest::Partial);
        assert_eq!(state.next_page().refresh, RefreshRequest::Full);
        assert_eq!(
            state
                .swipe(20.0, 0.0, Duration::from_millis(200), 150.0)
                .action,
            ReaderAction::None
        );
        assert_eq!(
            state
                .swipe(40.0, 0.0, Duration::from_millis(200), 150.0)
                .action,
            ReaderAction::PageChanged
        );
        assert_eq!(
            state
                .swipe(40.0, 0.0, Duration::from_millis(950), 150.0)
                .action,
            ReaderAction::None
        );
    }
}
