//! Reader-only display orientation and geometry.

use super::*;

impl App {
    pub(crate) fn display_landscape(&self) -> bool {
        matches!(self.display_rotation_degrees(), 90 | 270)
    }

    pub(crate) fn display_rotation_degrees(&self) -> u16 {
        reader_display_rotation_degrees(&self.route, &self.effective_reader_preferences())
    }

    pub(crate) fn sync_runtime_geometry(&self) -> Result<()> {
        let Some(ui) = self.ui.upgrade() else {
            return Ok(());
        };
        let rotation_degrees = self.display_rotation_degrees();
        let landscape = matches!(rotation_degrees, 90 | 270);
        let (width, height) = self.platform.runtime_logical_size(landscape);
        if ui.get_canvas_width() != width as f32
            || ui.get_canvas_height() != height as f32
            || ui.global::<State>().get_landscape() != landscape
            || self.platform.display_rotation_degrees() != rotation_degrees
        {
            self.platform
                .configure_window_rotation(ui.window(), rotation_degrees)?;
            ui.set_canvas_width(width as f32);
            ui.set_canvas_height(height as f32);
            ui.global::<State>().set_landscape(landscape);
        }
        Ok(())
    }
}

pub(super) fn reader_display_rotation_degrees(route: &str, preferences: &Preferences) -> u16 {
    if route != "reader" || !preferences.landscape {
        0
    } else if preferences.reverse_landscape {
        270
    } else {
        90
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reader_landscape_uses_the_selected_manual_direction() {
        let mut preferences = Preferences::default();
        assert_eq!(reader_display_rotation_degrees("reader", &preferences), 0);
        preferences.reverse_landscape = true;
        assert_eq!(reader_display_rotation_degrees("reader", &preferences), 0);
        preferences.landscape = true;
        assert_eq!(reader_display_rotation_degrees("reader", &preferences), 270);
        preferences.reverse_landscape = false;
        assert_eq!(reader_display_rotation_degrees("reader", &preferences), 90);
    }

    #[test]
    fn routes_outside_the_reader_always_use_portrait() {
        for reverse_landscape in [false, true] {
            let preferences = Preferences {
                landscape: true,
                reverse_landscape,
                ..Preferences::default()
            };
            for route in [
                "library",
                "detail",
                "settings",
                "sources",
                "search",
                "downloads",
            ] {
                assert_eq!(reader_display_rotation_degrees(route, &preferences), 0);
            }
        }
    }
}
