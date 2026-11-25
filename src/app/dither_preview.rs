//! A bounded, cached settings fixture using the actual software algorithms.

use super::*;

impl App {
    pub(super) fn sync_dither_preview(&mut self, ui: &AppWindow) -> Result<()> {
        if self.route != "settings" || self.interactions.settings_section != "screen-refresh" {
            return Ok(());
        }
        let large = self.platform.profile().is_scribe();
        let logical_width = self.platform.runtime_logical_size(false).0;
        // Match the Settings content column and subtract the two preview borders.
        let content =
            logical_width.saturating_sub(if large { 320 + 48 + 40 + 2 } else { 28 * 2 + 2 });
        let scale = ui.window().scale_factor();
        let width = (content as f32 * scale).round() as u32;
        let height = ((if large { 54.0 } else { 46.0 }) * scale).round() as u32;
        anyhow::ensure!(width > 0 && width <= 4096 && height > 0 && height <= 256);
        let method = self.prefs.spatial_dither();
        let key = (width, height, method);
        if self.dither_preview_key == Some(key) {
            return Ok(());
        }
        // No image decoding or resampling: this small fixture is generated once
        // per size/method change, at the window's physical pixel resolution.
        let mut gray = image::GrayImage::from_fn(width, height, |x, _| {
            image::Luma([((u64::from(x) * 256 / u64::from(width)).min(255)) as u8])
        });
        imaging::spatial_dither::apply(&mut gray, method)?;
        ui.global::<State>()
            .set_grayscale_preview(prepared_pixels::prepare(Arc::new(gray)).into_image());
        self.dither_preview_key = Some(key);
        Ok(())
    }
}
