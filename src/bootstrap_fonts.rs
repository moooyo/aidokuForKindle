//! Incremental font preparation adapted from the sibling BiliComics loader.

use std::{
    io::Read,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

use aidoku_kindle::ui::{AppWindow, Theme};
use anyhow::{Context, Result, bail, ensure};
use flate2::read::GzDecoder;
use slint::{ComponentHandle, fontique_010::fontique};

const MAX_FONT_BYTES: u64 = 64 * 1024 * 1024;
const STARTUP_FAMILY: &str = "BiliComics Startup";
const BUNDLED_FONTS: &[(&str, f32, &[u8])] = &[
    (
        "Noto Sans CJK SC",
        400.0,
        include_bytes!("../assets/fonts/NotoSansSC-Regular.otf.gz"),
    ),
    (
        "Noto Sans CJK SC",
        500.0,
        include_bytes!("../assets/fonts/NotoSansSC-Medium.otf.gz"),
    ),
    (
        "Noto Sans CJK SC",
        700.0,
        include_bytes!("../assets/fonts/NotoSansSC-Bold.otf.gz"),
    ),
    (
        "Noto Serif CJK SC",
        400.0,
        include_bytes!("../assets/fonts/NotoSerifSC-Regular.otf.gz"),
    ),
    (
        "Noto Serif CJK SC",
        700.0,
        include_bytes!("../assets/fonts/NotoSerifSC-Bold.otf.gz"),
    ),
    (
        "Noto Serif CJK SC",
        900.0,
        include_bytes!("../assets/fonts/NotoSerifSC-Black.otf.gz"),
    ),
    (
        "Literata",
        400.0,
        include_bytes!("../assets/fonts/Literata-Regular.ttf.gz"),
    ),
    (
        "Literata",
        600.0,
        include_bytes!("../assets/fonts/Literata-SemiBold.ttf.gz"),
    ),
    (
        "Literata",
        700.0,
        include_bytes!("../assets/fonts/Literata-Bold.ttf.gz"),
    ),
];

pub struct PreparedFont {
    index: usize,
    bytes: Arc<Vec<u8>>,
}

#[derive(Default)]
pub struct Fonts {
    faces: Vec<Arc<Vec<u8>>>,
    registered: usize,
}

impl Fonts {
    /// Valid uncompressed bytes keep backend setup ahead of every worker.
    pub fn bootstrap_bytes() -> &'static [u8] {
        include_bytes!("../assets/fonts/StartupSansSC.otf")
    }

    pub fn register_bootstrap(ui: &AppWindow) -> Result<()> {
        register_face(
            Arc::new(Self::bootstrap_bytes().to_vec()),
            STARTUP_FAMILY,
            400.0,
        )?;
        ui.global::<Theme>().set_sans(STARTUP_FAMILY.into());
        ui.global::<Theme>().set_serif(STARTUP_FAMILY.into());
        ui.global::<Theme>().set_numbers(STARTUP_FAMILY.into());
        Ok(())
    }

    /// Deliver immutable faces in order while the UI registers earlier ones.
    pub fn prepare_faces(
        cancelled: &AtomicBool,
        mut deliver: impl FnMut(PreparedFont) -> Result<()>,
    ) -> Result<()> {
        for (index, (family, weight, compressed)) in BUNDLED_FONTS.iter().enumerate() {
            if cancelled.load(Ordering::Acquire) {
                return Ok(());
            }
            let mut decoded = Vec::new();
            GzDecoder::new(*compressed)
                .take(MAX_FONT_BYTES + 1)
                .read_to_end(&mut decoded)
                .with_context(|| format!("Load bundled {family} weight {weight}"))?;
            if decoded.len() as u64 > MAX_FONT_BYTES {
                bail!("A bundled font exceeds the decompression limit");
            }
            if cancelled.load(Ordering::Acquire) {
                return Ok(());
            }
            deliver(PreparedFont {
                index,
                bytes: Arc::new(decoded),
            })?;
        }
        aidoku_kindle::reader_timing::mark("fonts_loaded");
        Ok(())
    }

    pub fn accept(&mut self, face: PreparedFont) -> Result<()> {
        ensure!(
            face.index == self.faces.len() && face.index < BUNDLED_FONTS.len(),
            "Bundled font faces arrived out of order"
        );
        self.faces.push(face.bytes);
        Ok(())
    }

    pub fn can_register_next(&self) -> bool {
        self.registered < self.faces.len()
    }

    /// Register one face per event-loop turn without switching UI families.
    pub fn register_next(&mut self) -> Result<bool> {
        let Some(bytes) = self.faces.get(self.registered) else {
            return Ok(self.registered == BUNDLED_FONTS.len());
        };
        let (family, weight, _) = BUNDLED_FONTS[self.registered];
        register_face(bytes.clone(), family, weight)?;
        self.registered += 1;
        Ok(self.registered == BUNDLED_FONTS.len())
    }

    /// Publish the complete family set only after every requested weight exists.
    pub fn finish(&self, ui: &AppWindow) -> Result<()> {
        ensure!(
            self.registered == BUNDLED_FONTS.len(),
            "Bundled font registration is not complete"
        );
        ui.global::<Theme>().set_sans("Noto Sans CJK SC".into());
        ui.global::<Theme>().set_serif("Noto Serif CJK SC".into());
        ui.global::<Theme>().set_numbers("Literata".into());
        crate::FONT_INITIALIZATIONS.fetch_add(1, Ordering::AcqRel);
        aidoku_kindle::reader_timing::mark("fonts_registered");
        Ok(())
    }
}

fn register_face(bytes: Arc<Vec<u8>>, family: &str, weight: f32) -> Result<()> {
    let mut collection = slint::fontique_010::shared_collection();
    let registered = collection.register_fonts(fontique::Blob::new(bytes), None);
    ensure!(
        registered.iter().any(|(id, faces)| {
            collection.family_name(*id) == Some(family)
                && faces.iter().any(|face| face.weight().value() == weight)
        }),
        "Bundled font did not register as {family} weight {weight}"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn incomplete_font_delivery_cannot_finish_the_family_switch() {
        let mut fonts = Fonts::default();
        assert!(!fonts.can_register_next());
        assert!(!fonts.register_next().unwrap());
        fonts
            .accept(PreparedFont {
                index: 0,
                bytes: Arc::new(vec![1]),
            })
            .unwrap();
        assert!(fonts.can_register_next());
        assert_eq!(fonts.registered, 0);
    }

    #[test]
    fn out_of_order_or_repeated_faces_preserve_existing_bytes() {
        let mut fonts = Fonts::default();
        assert!(
            fonts
                .accept(PreparedFont {
                    index: 1,
                    bytes: Arc::new(vec![2]),
                })
                .is_err()
        );
        fonts
            .accept(PreparedFont {
                index: 0,
                bytes: Arc::new(vec![1]),
            })
            .unwrap();
        assert!(
            fonts
                .accept(PreparedFont {
                    index: 0,
                    bytes: Arc::new(vec![9]),
                })
                .is_err()
        );
        assert_eq!(fonts.faces[0].as_slice(), &[1]);
    }

    #[test]
    fn cancellation_before_work_does_not_decode_a_face() {
        let mut deliveries = 0;
        Fonts::prepare_faces(&AtomicBool::new(true), |_| {
            deliveries += 1;
            Ok(())
        })
        .unwrap();
        assert_eq!(deliveries, 0);
    }
}
