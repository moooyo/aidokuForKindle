//! Deterministic GETVAR/GETFIX values for the private software adapter.

use super::{FbFixScreeninfo, FbVarScreeninfo, FramebufferLayout, NativeFramebufferObservation};
use std::io;

pub fn studio_observation(rotation: u32) -> io::Result<NativeFramebufferObservation> {
    if rotation > 3 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "unknown studio rotation",
        ));
    }
    let variable = FbVarScreeninfo {
        xres: 64,
        yres: 64,
        xres_virtual: 64,
        yres_virtual: 64,
        bits_per_pixel: 8,
        grayscale: 1,
        rotate: rotation,
        ..Default::default()
    };
    let fixed = FbFixScreeninfo {
        line_length: 64,
        smem_len: 4096,
        ..Default::default()
    };
    Ok(NativeFramebufferObservation {
        rotation,
        layout: FramebufferLayout::from_information(&variable, &fixed)?,
        inverted_gray8: false,
    })
}
