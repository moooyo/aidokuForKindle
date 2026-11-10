//! Runtime policy eligibility and truthful device capability projection.

use super::*;

impl App {
    /// The hot path only reads RAM and submits a coalesced worker request.
    pub(super) fn poll_device_policies(&mut self) {
        let observation = self.platform.device_policy_observation();
        let network_free_chapter = self.options.offline
            || self.reader.as_ref().is_some_and(|reader| {
                let chapter = reader.current_chapter();
                chapter.source_id == "local" || chapter.downloaded
            });
        let pairing_active = self.pairing.as_ref().is_some_and(PairingServer::active);
        let disable_wifi = self.prefs.reading_wifi_off
            && !self.options.demo
            && self.route == "reader"
            && !self.suspended
            && !self.shutting_down
            && !self.resume_frame_pending
            && !self.resume_network_pending
            && self.displayed_reader.is_some()
            && network_free_chapter
            && self.downloads.is_idle()
            && !pairing_active
            && observation.capabilities.wifi_control;
        self.platform.set_reading_wifi_policy(disable_wifi);
        if let Some(ui) = self.ui.upgrade() {
            let state = ui.global::<State>();
            state.set_wifi_policy_supported(observation.capabilities.wifi_control);
            state.set_screensaver_supported(observation.capabilities.screensaver);
            state.set_pen_supported(observation.capabilities.stylus);
            state.set_charging_status_supported(observation.charging.is_some());
        }
    }
}
