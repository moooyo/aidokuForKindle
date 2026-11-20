//! Optional acceleration for local cover thumbnails, not Reader image processing.

use anyhow::Result;
use image::{GrayImage, RgbImage};

#[derive(Clone, Copy)]
pub(super) struct Kernels {
    weighted_rows: unsafe extern "C" fn(*mut f32, *const u8, usize, *const f32, usize),
    rgb8_luma: unsafe extern "C" fn(*mut u8, *const u8, usize),
}

#[cfg(all(feature = "kindle", target_os = "linux", target_arch = "arm"))]
pub(super) fn available() -> Option<Kernels> {
    use std::sync::OnceLock;

    unsafe extern "C" {
        fn aidoku_cover_weighted_rows(
            out: *mut f32,
            source: *const u8,
            width: usize,
            weights: *const f32,
            taps: usize,
        );
        fn aidoku_cover_rgb8_luma(out: *mut u8, source: *const u8, count: usize);
    }
    const HWCAP_NEON: libc::c_ulong = 1 << 12;
    static DISPATCH: OnceLock<Option<Kernels>> = OnceLock::new();
    *DISPATCH.get_or_init(|| {
        // SAFETY: this reads the calling process's Linux auxiliary vector only.
        let hwcap = unsafe { libc::getauxval(libc::AT_HWCAP) };
        (hwcap & HWCAP_NEON != 0).then_some(Kernels {
            weighted_rows: aidoku_cover_weighted_rows,
            rgb8_luma: aidoku_cover_rgb8_luma,
        })
    })
}

#[cfg(not(all(feature = "kindle", target_os = "linux", target_arch = "arm")))]
pub(super) fn available() -> Option<Kernels> {
    None
}

impl Kernels {
    pub(super) fn grayscale(self, source: RgbImage) -> Result<GrayImage> {
        let (width, height) = source.dimensions();
        let mut gray = GrayImage::new(width, height);
        let len = gray.as_raw().len();
        for (src, dst) in source.as_raw()[..3 * len]
            .chunks(3 * 16384)
            .zip(gray.as_mut().chunks_mut(16384))
        {
            super::check_decode_cancelled()?;
            // SAFETY: there are three source bytes per unique output byte and
            // `available` verifies the required ISA before providing the kernels.
            unsafe { (self.rgb8_luma)(dst.as_mut_ptr(), src.as_ptr(), dst.len()) };
        }
        Ok(gray)
    }

    pub(super) fn accumulate(self, out: &mut [f32], source: &[u8], weights: &[f32]) -> Result<()> {
        let width = out.len();
        assert!(width > 0 && source.len() / width >= weights.len());
        // Bound time between cancellation checks on very tall or wide images.
        let batch = (32768 / width).clamp(1, 32);
        for (n, taps) in weights.chunks(batch).enumerate() {
            super::check_decode_cancelled()?;
            let rows = &source[n * batch * width..][..taps.len() * width];
            // SAFETY: the source has exactly `taps.len()` complete `width`-
            // pixel rows and does not alias `out`. Accumulate in source-y order.
            unsafe {
                (self.weighted_rows)(
                    out.as_mut_ptr(),
                    rows.as_ptr(),
                    width,
                    taps.as_ptr(),
                    taps.len(),
                )
            };
        }
        Ok(())
    }
}
