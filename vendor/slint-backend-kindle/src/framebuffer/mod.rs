mod ffi;
mod markers;

#[cfg(test)]
mod dithering_tests;
#[cfg(test)]
mod polarity_tests;
#[cfg(test)]
mod sleep_fence_tests;
#[cfg(test)]
mod test_support;

use crate::dithering::{DitheringHandle, software_gray};
use crate::gray_target::ScenePixels;
use crate::refresh::{RefreshWaveform, ResolvedRefresh};
use crate::render_control::GrayMode;
use std::cell::Cell;
use std::io;
use std::ops::Range;
use std::os::fd::AsRawFd;

use markers::UpdateMarkers;

use ffi::{
    EPDC_FLAG_FORCE_MONOCHROME, FBIOGET_FSCREENINFO, FBIOGET_VSCREENINFO, FbFixScreeninfo,
    FbVarScreeninfo, MTK_EPDC_FLAG_USE_DITHERING_Y4, MXCFB_SEND_UPDATE, MXCFB_SEND_UPDATE_MTK,
    MXCFB_SEND_UPDATE_REX, MXCFB_SEND_UPDATE_ZELDA, MXCFB_WAIT_FOR_UPDATE_COMPLETE,
    TEMP_USE_AMBIENT, UPDATE_MODE_FULL, UPDATE_MODE_PARTIAL, UpdateMarkerData, UpdateRect,
    UpdateRequest, UpdateRequestMtk, UpdateRequestRex, UpdateRequestZelda, WAVEFORM_MODE_A2_LEGACY,
    WAVEFORM_MODE_A2_MODERN, WAVEFORM_MODE_AUTO, WAVEFORM_MODE_DU, WAVEFORM_MODE_GC16,
};

/// Read-only metadata from the descriptor used for this exact observation.
/// The private layout can only originate from checked GETVAR and GETFIX calls.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct NativeFramebufferObservation {
    pub rotation: u32,
    layout: FramebufferLayout,
    inverted_gray8: bool,
}

impl NativeFramebufferObservation {
    pub(crate) fn supports_hardware_y4(&self) -> bool {
        self.layout.words[6] == 8 && matches!(self.layout.words[7], 1 | 2)
    }

    /// A canonical numeric identity for a launcher's protected layout record.
    pub fn layout_token(&self) -> String {
        self.layout
            .words
            .iter()
            .map(u64::to_string)
            .collect::<Vec<_>>()
            .join(":")
    }

    #[cfg(test)]
    pub(crate) fn for_test_rotation(rotation: u32) -> Self {
        let variable = FbVarScreeninfo {
            xres: 17,
            yres: 11,
            xres_virtual: 17,
            yres_virtual: 11,
            bits_per_pixel: 8,
            grayscale: 1,
            rotate: rotation,
            ..Default::default()
        };
        let fixed = FbFixScreeninfo {
            line_length: 20,
            smem_len: 220,
            ..Default::default()
        };
        Self {
            rotation,
            layout: FramebufferLayout::from_information(&variable, &fixed).unwrap(),
            inverted_gray8: gray8_is_inverted(&variable),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FramebufferLayout {
    words: [u64; 35],
}

impl FramebufferLayout {
    fn from_information(variable: &FbVarScreeninfo, fixed: &FbFixScreeninfo) -> io::Result<Self> {
        let bytes_per_pixel = match variable.bits_per_pixel {
            8 => 1usize,
            32 => 4usize,
            _ => return Err(invalid_layout("native framebuffer depth is unsupported")),
        };
        let row_bytes = (variable.xres as usize)
            .checked_mul(bytes_per_pixel)
            .ok_or_else(|| invalid_layout("native framebuffer row size overflows"))?;
        let visible_bytes = (fixed.line_length as usize)
            .checked_mul(variable.yres as usize)
            .ok_or_else(|| invalid_layout("native framebuffer mapping size overflows"))?;
        if variable.xres == 0
            || variable.yres == 0
            || (fixed.line_length as usize) < row_bytes
            || visible_bytes > isize::MAX as usize
            || variable.xres_virtual < variable.xres
            || variable.yres_virtual < variable.yres
            || variable.xoffset != 0
            || variable.yoffset != 0
            || u64::from(fixed.smem_len) < visible_bytes as u64
        {
            return Err(invalid_layout(
                "native framebuffer mapping geometry is unsafe",
            ));
        }
        Ok(Self {
            words: [
                variable.xres.into(),
                variable.yres.into(),
                variable.xres_virtual.into(),
                variable.yres_virtual.into(),
                variable.xoffset.into(),
                variable.yoffset.into(),
                variable.bits_per_pixel.into(),
                variable.grayscale.into(),
                variable.nonstd.into(),
                variable.red.offset.into(),
                variable.red.length.into(),
                variable.red.msb_right.into(),
                variable.green.offset.into(),
                variable.green.length.into(),
                variable.green.msb_right.into(),
                variable.blue.offset.into(),
                variable.blue.length.into(),
                variable.blue.msb_right.into(),
                variable.transp.offset.into(),
                variable.transp.length.into(),
                variable.transp.msb_right.into(),
                fixed.smem_start as u64,
                fixed.smem_len.into(),
                fixed.type_.into(),
                fixed.type_aux.into(),
                fixed.visual.into(),
                fixed.line_length.into(),
                fixed.xpanstep.into(),
                fixed.ypanstep.into(),
                fixed.ywrapstep.into(),
                fixed.mmio_start as u64,
                fixed.mmio_len.into(),
                fixed.accel.into(),
                fixed.capabilities.into(),
                variable.vmode.into(),
            ],
        })
    }
}

fn invalid_layout(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn gray8_is_inverted(variable: &FbVarScreeninfo) -> bool {
    variable.bits_per_pixel == 8 && variable.grayscale == 2
}

/// Encode canonical luma for the current native Y8 or Y8INV scanout.
/// This preserves all 256 values and is independent of software night mode.
fn encode_native_gray(value: u8, inverted_gray8: bool) -> u8 {
    if inverted_gray8 { 255 - value } else { value }
}

fn read_variable(fd: libc::c_int) -> io::Result<FbVarScreeninfo> {
    let mut variable = FbVarScreeninfo::default();
    // SAFETY: This read-only GET fills the matching initialized ABI buffer.
    if unsafe { libc::ioctl(fd, FBIOGET_VSCREENINFO as _, &mut variable as *mut _) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(variable)
}

fn read_native_information(fd: libc::c_int) -> io::Result<(FbVarScreeninfo, FbFixScreeninfo)> {
    let variable = read_variable(fd)?;
    let mut fixed = FbFixScreeninfo::default();
    // SAFETY: GETFIX writes this complete aligned structure without retaining it.
    if unsafe { libc::ioctl(fd, FBIOGET_FSCREENINFO as _, &mut fixed as *mut _) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok((variable, fixed))
}

fn read_native_framebuffer(fd: libc::c_int) -> io::Result<NativeFramebufferObservation> {
    let (variable, fixed) = read_native_information(fd)?;
    let layout = FramebufferLayout::from_information(&variable, &fixed)?;
    let (after, fixed_after) = read_native_information(fd)?;
    if variable.rotate != after.rotate
        || layout != FramebufferLayout::from_information(&after, &fixed_after)?
    {
        return Err(io::Error::new(
            io::ErrorKind::WouldBlock,
            "native framebuffer changed during observation",
        ));
    }
    if variable.rotate > 3 {
        return Err(invalid_layout(
            "native framebuffer rotation is not a kernel quarter turn",
        ));
    }
    Ok(NativeFramebufferObservation {
        rotation: variable.rotate,
        layout,
        inverted_gray8: gray8_is_inverted(&variable),
    })
}

/// Inspect native scanout before ownership acquisition, without mapping pixels.
/// This opens only an O_RDONLY descriptor and issues GET ioctls; no input is opened.
pub fn probe_native_framebuffer() -> io::Result<NativeFramebufferObservation> {
    let file = std::fs::File::open("/dev/fb0")?;
    read_native_framebuffer(file.as_raw_fd())
}

/// Which MXCFB update ioctl this kernel accepts, varies with the Kindle model
///
//We probe on first refresh and remember the winner instead of retrying the failing ioctl every frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum UpdateVariant {
    /// `MXCFB_SEND_UPDATE` — 72-byte struct, older devices.
    Legacy,
    /// `MXCFB_SEND_UPDATE_REX` — 80-byte struct, Paperwhite 10th gen and newer.
    Rex,
    /// `MXCFB_SEND_UPDATE_ZELDA` — 88-byte struct, KOA2/KOA3 (Oasis 2/3).
    Zelda,
    /// `MXCFB_SEND_UPDATE_MTK` — 96-byte struct, MediaTek devices (Basic 2022,
    /// Paperwhite 5, Scribe).
    Mtk,
    /// No variant was accepted. Cached so a hopeless kernel costs one probe
    /// and one error log total, not three failing ioctls per frame.
    Unsupported,
}

impl UpdateVariant {
    /// Probed oldest ABI first.
    const PROBE_ORDER: [UpdateVariant; 4] = [Self::Legacy, Self::Rex, Self::Zelda, Self::Mtk];
}

/// Memory-mapped handle to the Kindle's e-ink framebuffer.
///
/// Pixel format is 8-bit grayscale (one byte per pixel) on most models, or
/// 32 bpp on color models like the Colorsoft. The `stride` may be wider than
/// `width * bytes_per_pixel` due to hardware alignment requirements.
pub(crate) struct Framebuffer {
    file: std::fs::File,
    map: *mut u8,
    len: usize,
    pub(crate) width: u32,
    pub(crate) height: u32,
    stride: usize,
    /// Bytes per pixel in the mmap: 1 (8-bit grayscale, most Kindles) or
    /// 4 (32-bit BGRA/RGBA, e.g. Kindle Colorsoft).
    bytes_per_pixel: usize,
    /// Hardware processing additionally requires the checked native gray tag.
    native_gray8: bool,
    retained_layout: Option<FramebufferLayout>,
    /// Derived only from this descriptor's checked native pixel format.
    inverted_gray8: Cell<bool>,
    /// The update ioctl variant this kernel accepts, cached after the first
    /// successful refresh. `None` until then.
    update_variant: Cell<Option<UpdateVariant>>,
    update_markers: UpdateMarkers,
    dithering: DitheringHandle,
    dithering_frame: Cell<DitheringFrame>,
    /// A failed SEND may have reached scanout. Never clear this within an owner.
    dithering_delivery_failure: Cell<Option<Option<i32>>>,
    last_accepted_dithering: Cell<Option<AcceptedDithering>>,
    canonical_rewrite_count: Cell<u64>,
    mapped_software_dithered: Cell<bool>,
}

#[derive(Clone, Copy, Default, Eq, PartialEq)]
struct DitheringFrame {
    available: bool,
    requested: bool,
    bilevel: bool,
}

/// A snapshot associated with one actual payload, never a queued preference.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub(crate) struct FrameDitheringStatus {
    pub(crate) hardware_y4_available: bool,
    pub(crate) hardware_y4_requested: bool,
    pub(crate) hardware_y4_applied: bool,
    pub(crate) software_dither_fallback: bool,
    pub(crate) dithering_delivery_uncertain: bool,
}

#[derive(Clone, Copy)]
struct AcceptedDithering {
    marker: u32,
    status: FrameDitheringStatus,
    rewritten: bool,
    raw_scene: bool,
}

#[derive(Clone, Copy)]
struct FallbackScene<'a> {
    pixels: ScenePixels<'a>,
    mode: GrayMode,
}

/// A checked full update accepted by this exact live framebuffer owner.
/// Callers cannot manufacture a receipt from a counter or a queued request.
pub(crate) struct SubmittedUpdate<'a> {
    framebuffer: &'a Framebuffer,
    marker: u32,
}

impl SubmittedUpdate<'_> {
    pub(crate) fn marker(&self) -> u32 {
        self.marker
    }
}

impl std::fmt::Debug for SubmittedUpdate<'_> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("SubmittedUpdate")
            .field("marker", &self.marker)
            .finish()
    }
}

// SAFETY: The mmap is process-wide and we only access it from the event loop thread.
unsafe impl Send for Framebuffer {}

impl Framebuffer {
    /// Select a neutral target from the actual mapped format, never a profile.
    pub(crate) fn supports_gray8_target(&self) -> bool {
        self.bytes_per_pixel == 1
    }

    /// Open the framebuffer device and query its geometry from the kernel.
    ///
    /// This works on any Kindle model - the resolution and stride are read at
    /// runtime rather than being hardcoded.
    pub(crate) fn open() -> std::io::Result<Self> {
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open("/dev/fb0")?;

        let fd = file.as_raw_fd();

        let mut vinfo = FbVarScreeninfo::default();
        if unsafe {
            libc::ioctl(
                fd,
                FBIOGET_VSCREENINFO as _,
                &mut vinfo as *mut _ as *mut libc::c_void,
            )
        } == -1
        {
            return Err(std::io::Error::last_os_error());
        }

        let mut finfo = FbFixScreeninfo::default();
        if unsafe {
            libc::ioctl(
                fd,
                FBIOGET_FSCREENINFO as _,
                &mut finfo as *mut _ as *mut libc::c_void,
            )
        } == -1
        {
            return Err(std::io::Error::last_os_error());
        }

        let width = vinfo.xres;
        let height = vinfo.yres;
        let stride = finfo.line_length as usize;

        // Most Kindles expose an 8-bit grayscale framebuffer, but color models
        // (Kindle Colorsoft) expose a 32 bpp one. The render path produces one
        // gray byte per pixel; for 32 bpp we replicate it into all three color
        // channels, so the byte order (BGRA vs RGBA) doesn't matter.
        let bytes_per_pixel = match vinfo.bits_per_pixel {
            8 => 1,
            32 => 4,
            other => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    format!("unsupported framebuffer depth: {other} bpp (expected 8 or 32)"),
                ));
            }
        };

        if width == 0 || height == 0 || stride < width as usize * bytes_per_pixel {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!(
                    "invalid framebuffer geometry: {width}x{height}, stride={stride}, bpp={}",
                    vinfo.bits_per_pixel
                ),
            ));
        }

        let len = stride * height as usize;
        let retained_layout = FramebufferLayout::from_information(&vinfo, &finfo).ok();

        let map = unsafe {
            libc::mmap(
                std::ptr::null_mut(),
                len,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                fd,
                0,
            )
        };

        if map == libc::MAP_FAILED {
            return Err(std::io::Error::last_os_error());
        }

        Ok(Self {
            file,
            map: map as *mut u8,
            len,
            width,
            height,
            stride,
            bytes_per_pixel,
            native_gray8: vinfo.bits_per_pixel == 8
                && matches!(vinfo.grayscale, 1 | 2)
                && retained_layout.is_some(),
            retained_layout,
            inverted_gray8: Cell::new(gray8_is_inverted(&vinfo)),
            update_variant: Cell::new(None),
            update_markers: UpdateMarkers::default(),
            dithering: DitheringHandle::default(),
            dithering_frame: Cell::new(DitheringFrame::default()),
            dithering_delivery_failure: Cell::new(None),
            last_accepted_dithering: Cell::new(None),
            canonical_rewrite_count: Cell::new(0),
            mapped_software_dithered: Cell::new(false),
        })
    }

    /// Freeze processing with the scene that will be written in this frame.
    /// Observer callbacks and producer revisions cannot reinterpret its pixels.
    pub(crate) fn set_dithering(&mut self, handle: DitheringHandle, bilevel: bool) -> bool {
        if !self.native_gray8
            || matches!(
                self.update_variant.get(),
                Some(
                    UpdateVariant::Legacy
                        | UpdateVariant::Rex
                        | UpdateVariant::Zelda
                        | UpdateVariant::Unsupported
                )
            )
        {
            handle.reject_hardware();
        }
        let frame = DitheringFrame {
            available: handle.hardware_y4_available(),
            requested: handle.requested(),
            bilevel,
        };
        let changed = self.dithering_frame.replace(frame) != frame;
        self.dithering = handle;
        changed
    }

    fn raw_scene(&self) -> bool {
        let frame = self.dithering_frame.get();
        frame.requested && !frame.bilevel
    }

    fn hardware_y4_active(&self) -> bool {
        self.raw_scene() && self.dithering_frame.get().available && self.native_gray8
    }

    pub(crate) fn software_dither_active(&self) -> bool {
        self.raw_scene() && !self.dithering_frame.get().available
    }

    pub(crate) fn current_dithering_status(&self) -> FrameDitheringStatus {
        let frame = self.dithering_frame.get();
        FrameDitheringStatus {
            hardware_y4_available: frame.available,
            hardware_y4_requested: frame.requested,
            software_dither_fallback: self.mapped_software_dithered.get(),
            dithering_delivery_uncertain: self.dithering_delivery_failure.get().is_some(),
            ..FrameDitheringStatus::default()
        }
    }

    pub(crate) fn accepted_dithering(&self, marker: u32) -> Option<FrameDitheringStatus> {
        self.last_accepted_dithering
            .get()
            .filter(|accepted| accepted.marker == marker)
            .map(|accepted| accepted.status)
    }

    pub(crate) fn accepted_frame_was_rewritten(&self, marker: u32) -> bool {
        self.last_accepted_dithering
            .get()
            .is_some_and(|accepted| accepted.marker == marker && accepted.rewritten)
    }

    pub(crate) fn require_known_dithering_delivery(&self) -> io::Result<()> {
        match self.dithering_delivery_failure.get() {
            None => Ok(()),
            Some(Some(errno)) => Err(io::Error::from_raw_os_error(errno)),
            Some(None) => Err(io::Error::other(
                "the display driver returned an unknown update outcome",
            )),
        }
    }

    pub(crate) fn settle_dithering_transition(&self) -> io::Result<()> {
        if let Err(error) = self.wait_for_pending_update_complete_checked() {
            self.block_uncertain_dithering_delivery(&error);
            return Err(error);
        }
        Ok(())
    }

    pub(crate) fn canonical_rewrite_count(&self) -> u64 {
        self.canonical_rewrite_count.get()
    }

    fn block_uncertain_dithering_delivery(&self, error: &io::Error) {
        if self.dithering_delivery_failure.get().is_none() {
            self.dithering_delivery_failure
                .set(Some(error.raw_os_error()));
        }
        self.dithering.block_uncertain_delivery();
    }

    /// Raw retained pixels cannot receive DU/A2, even under a small UI overlay.
    pub(crate) fn resolve_dithering(&self, mut refresh: ResolvedRefresh) -> ResolvedRefresh {
        if self.raw_scene() {
            refresh.waveform = RefreshWaveform::Gc16;
            if self.hardware_y4_active() {
                let region = aligned_dither_region(
                    UpdateRect {
                        left: refresh.region.x as u32,
                        top: refresh.region.y as u32,
                        width: refresh.region.width,
                        height: refresh.region.height,
                    },
                    self.width,
                    self.height,
                );
                refresh.region = crate::PhysicalRect {
                    x: region.left as i32,
                    y: region.top as i32,
                    width: region.width,
                    height: region.height,
                };
            }
        }
        refresh
    }

    /// Recheck the retained mmap contract on the owned descriptor before I/O.
    /// Rotation may change, but geometry, memory identity and pixel layout may not.
    pub(crate) fn observe_native_rotation(
        &self,
        anchor: NativeFramebufferObservation,
    ) -> io::Result<NativeFramebufferObservation> {
        self.observe_native_rotation_with(anchor, || read_native_framebuffer(self.file.as_raw_fd()))
    }

    fn observe_native_rotation_with(
        &self,
        anchor: NativeFramebufferObservation,
        observe: impl FnOnce() -> io::Result<NativeFramebufferObservation>,
    ) -> io::Result<NativeFramebufferObservation> {
        let observed = observe()?;
        if self.retained_layout != Some(observed.layout) || observed.layout != anchor.layout {
            return Err(invalid_layout(
                "native framebuffer layout changed from its same-run anchor",
            ));
        }
        // Polarity is current only after the same ownership/layout check that
        // admits native rendering. A changed format still requires a new owner.
        self.inverted_gray8.set(observed.inverted_gray8);
        Ok(observed)
    }

    /// Encode a horizontal span of canonical grayscale into the mmap at row `y`.
    pub(crate) fn write_line(&mut self, y: usize, x_range: Range<usize>, pixels: &[u8]) {
        if self.dithering_delivery_failure.get().is_some() {
            return;
        }
        let bpp = self.bytes_per_pixel;
        let inverted_gray8 = self.inverted_gray8.get();
        let fallback = self.software_dither_active();
        self.mapped_software_dithered.set(fallback);
        let row_span = unsafe {
            std::slice::from_raw_parts_mut(
                self.map.add(y * self.stride + x_range.start * bpp),
                pixels.len() * bpp,
            )
        };
        match bpp {
            1 if inverted_gray8 || fallback => {
                for (offset, (destination, &value)) in row_span.iter_mut().zip(pixels).enumerate() {
                    let value = if fallback {
                        software_gray(value, x_range.start + offset, y)
                    } else {
                        value
                    };
                    *destination = encode_native_gray(value, inverted_gray8);
                }
            }
            1 => row_span.copy_from_slice(pixels),
            _ => {
                for (offset, (dst, &value)) in row_span.chunks_exact_mut(4).zip(pixels).enumerate()
                {
                    let g = if fallback {
                        software_gray(value, x_range.start + offset, y)
                    } else {
                        value
                    };
                    dst.copy_from_slice(&[g, g, g, 0xff]);
                }
            }
        }
    }

    /// Fill visible pixels from canonical luma (0x00 = black, 0xff = white).
    /// Native scanout polarity is applied here, independently of software modes.
    pub(crate) fn fill(&mut self, value: u8) {
        if self.dithering_delivery_failure.get().is_some() {
            return;
        }
        let bpp = self.bytes_per_pixel;
        let native_gray = encode_native_gray(value, self.inverted_gray8.get());
        let fallback = self.software_dither_active();
        self.mapped_software_dithered.set(fallback);
        for y in 0..self.height as usize {
            let row = unsafe {
                std::slice::from_raw_parts_mut(
                    self.map.add(y * self.stride),
                    self.width as usize * bpp,
                )
            };
            if fallback {
                for (x, pixel) in row.chunks_exact_mut(bpp).enumerate() {
                    let value = software_gray(value, x, y);
                    if bpp == 1 {
                        pixel[0] = encode_native_gray(value, self.inverted_gray8.get());
                    } else {
                        pixel.copy_from_slice(&[value, value, value, 0xff]);
                    }
                }
            } else {
                match bpp {
                    1 => row.fill(native_gray),
                    _ => {
                        for px in row.chunks_exact_mut(4) {
                            px.copy_from_slice(&[value, value, value, 0xff]);
                        }
                    }
                }
            }
        }
    }

    /// Ask the EPDC to refresh a region of the e-ink panel.
    ///
    /// On the first call we probe which update ioctl the kernel accepts and
    /// cache it, so later frames issue exactly one ioctl instead of retrying a
    /// known-failing one every refresh.
    fn send_update(&self, region: UpdateRect, waveform: u32, mode: u32) -> io::Result<()> {
        self.send_update_scene(region, waveform, mode, None)
            .map(|_| ())
    }

    fn send_update_scene(
        &self,
        region: UpdateRect,
        waveform: u32,
        mode: u32,
        scene: Option<FallbackScene<'_>>,
    ) -> io::Result<SubmittedUpdate<'_>> {
        self.send_update_using(
            region,
            waveform,
            mode,
            scene,
            |variant, region, waveform, mode, marker, y4| {
                if self.send_update_variant(variant, region, waveform, mode, marker, y4) {
                    Ok(())
                } else {
                    Err(io::Error::last_os_error())
                }
            },
            |marker| self.wait_for_marker_ioctl(marker),
        )
    }

    fn send_update_using(
        &self,
        mut region: UpdateRect,
        mut waveform: u32,
        mode: u32,
        scene: Option<FallbackScene<'_>>,
        mut send: impl FnMut(UpdateVariant, UpdateRect, u32, u32, u32, bool) -> io::Result<()>,
        wait: impl FnMut(u32) -> io::Result<()>,
    ) -> io::Result<SubmittedUpdate<'_>> {
        self.require_known_dithering_delivery()?;
        let raw_scene = self.raw_scene();
        let y4 = self.hardware_y4_active();
        if raw_scene {
            waveform = WAVEFORM_MODE_GC16;
        }
        if y4 {
            region = aligned_dither_region(region, self.width, self.height);
            // A reviewed Scribe uses the MTK ABI. Never probe another ABI with
            // this raw mapping before a checked software replacement exists.
            self.update_variant.set(Some(UpdateVariant::Mtk));
        }
        if region.width == 0 || region.height == 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "empty display update",
            ));
        }
        let mut rewritten = false;
        let receipt = match self.submit_update_checked_with_policy(raw_scene, |variant, marker| {
            send(
                variant,
                region,
                waveform,
                mode,
                marker,
                y4 && variant == UpdateVariant::Mtk,
            )
        }) {
            Ok(receipt) => receipt,
            Err(error) if y4 && explicit_update_rejection(error.raw_os_error()) => {
                self.dithering.reject_hardware();
                let mut frame = self.dithering_frame.get();
                frame.available = false;
                self.dithering_frame.set(frame);
                // A definite rejection does not replace the preceding accepted
                // marker. Settle that exact update before touching mapped bytes.
                if let Err(wait_error) = self.update_markers.wait_for_last_submitted(wait) {
                    self.block_uncertain_dithering_delivery(&wait_error);
                    return Err(wait_error);
                }
                let scene = scene.ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "a raw display fallback requires its canonical scene",
                    )
                })?;
                self.rewrite_software_scene(scene)?;
                rewritten = true;
                if error.raw_os_error() == Some(libc::ENOTTY) {
                    self.update_variant.set(None);
                }
                let full = UpdateRect {
                    top: 0,
                    left: 0,
                    width: self.width,
                    height: self.height,
                };
                self.submit_update_checked_with_policy(true, |variant, marker| {
                    send(
                        variant,
                        full,
                        WAVEFORM_MODE_GC16,
                        UPDATE_MODE_FULL,
                        marker,
                        false,
                    )
                })?
            }
            Err(error) => return Err(error),
        };
        let mut status = self.current_dithering_status();
        status.hardware_y4_applied = y4 && !rewritten;
        self.last_accepted_dithering.set(Some(AcceptedDithering {
            marker: receipt.marker,
            status,
            rewritten,
            raw_scene,
        }));
        Ok(receipt)
    }

    /// Rebuild from the canonical retained scene after the accepted-update
    /// fence. Neither night mode nor native Y8INV is applied to an old output.
    fn rewrite_software_scene(&self, scene: FallbackScene<'_>) -> io::Result<()> {
        self.require_known_dithering_delivery()?;
        let expected = self.width as usize * self.height as usize;
        let actual = match scene.pixels {
            ScenePixels::Gray(pixels) => pixels.len(),
            ScenePixels::Rgb(pixels) => pixels.len(),
        };
        if actual != expected {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "the canonical display scene has the wrong geometry",
            ));
        }
        let width = self.width as usize;
        for y in 0..self.height as usize {
            for x in 0..width {
                let index = y * width + x;
                let gray = match scene.pixels {
                    ScenePixels::Gray(pixels) => scene.mode.grayscale(pixels[index].value()),
                    ScenePixels::Rgb(pixels) => scene.mode.pixel(pixels[index]),
                };
                let gray = software_gray(gray, x, y);
                let offset = y * self.stride + x * self.bytes_per_pixel;
                // SAFETY: Coordinates remain within the owned visible mapping.
                // The preceding accepted scanout is settled; the event loop
                // owns the mapping and no slice aliases these bounded writes.
                unsafe {
                    if self.bytes_per_pixel == 1 {
                        *self.map.add(offset) = encode_native_gray(gray, self.inverted_gray8.get());
                    } else {
                        std::ptr::copy_nonoverlapping(
                            [gray, gray, gray, 0xff].as_ptr(),
                            self.map.add(offset),
                            4,
                        );
                    }
                }
            }
        }
        self.canonical_rewrite_count
            .set(self.canonical_rewrite_count.get().wrapping_add(1));
        self.mapped_software_dithered.set(true);
        Ok(())
    }

    #[cfg(test)]
    fn submit_update_checked_with(
        &self,
        send: impl FnMut(UpdateVariant, u32) -> io::Result<()>,
    ) -> io::Result<SubmittedUpdate<'_>> {
        self.submit_update_checked_with_policy(self.raw_scene(), send)
    }

    fn submit_update_checked_with_policy(
        &self,
        stop_on_uncertain: bool,
        mut send: impl FnMut(UpdateVariant, u32) -> io::Result<()>,
    ) -> io::Result<SubmittedUpdate<'_>> {
        self.require_known_dithering_delivery()?;
        let mut attempted = false;
        let submission = self.update_markers.submit_checked(|marker| {
            submit_update_with_policy(&self.update_variant, stop_on_uncertain, |variant| {
                attempted = true;
                send(variant, marker)
            })
        });
        let marker = match submission {
            Ok(marker) => marker,
            Err(error) => {
                if stop_on_uncertain
                    && attempted
                    && !explicit_update_rejection(error.raw_os_error())
                {
                    self.block_uncertain_dithering_delivery(&error);
                }
                return Err(error);
            }
        };
        Ok(SubmittedUpdate {
            framebuffer: self,
            marker,
        })
    }

    /// Issue one specific update ioctl variant. Returns whether it succeeded.
    fn send_update_variant(
        &self,
        variant: UpdateVariant,
        region: UpdateRect,
        waveform: u32,
        mode: u32,
        marker: u32,
        y4: bool,
    ) -> bool {
        match variant {
            UpdateVariant::Legacy => self.send_update_legacy(region, waveform, mode, marker),
            UpdateVariant::Rex => self.send_update_rex(region, waveform, mode, marker),
            UpdateVariant::Zelda => self.send_update_zelda(region, waveform, mode, marker),
            UpdateVariant::Mtk => self.send_update_mtk(region, waveform, mode, marker, y4),
            UpdateVariant::Unsupported => false,
        }
    }

    /// Hand one of the `UpdateRequest*` structs to the kernel. Returns whether
    /// the ioctl succeeded.
    fn send_update_ioctl<T>(&self, request: libc::c_ulong, update: &T) -> bool {
        // SAFETY: `update` outlives the ioctl and matches the kernel's struct.
        unsafe { libc::ioctl(self.file.as_raw_fd(), request as _, update as *const _) != -1 }
    }

    /// Issue the legacy `MXCFB_SEND_UPDATE` (72-byte struct). Returns whether the
    /// ioctl succeeded.
    fn send_update_legacy(
        &self,
        region: UpdateRect,
        waveform: u32,
        mode: u32,
        marker: u32,
    ) -> bool {
        let update = UpdateRequest {
            update_region: region,
            waveform_mode: waveform,
            update_mode: mode,
            update_marker: marker,
            temperature: TEMP_USE_AMBIENT,
            flags: if waveform == WAVEFORM_MODE_DU {
                EPDC_FLAG_FORCE_MONOCHROME
            } else {
                0
            },
            ..Default::default()
        };
        self.send_update_ioctl(MXCFB_SEND_UPDATE, &update)
    }

    /// Issue the modern `MXCFB_SEND_UPDATE_REX` (80-byte struct). Returns whether
    /// the ioctl succeeded.
    fn send_update_rex(&self, region: UpdateRect, waveform: u32, mode: u32, marker: u32) -> bool {
        let update = UpdateRequestRex {
            update_region: region,
            waveform_mode: waveform,
            update_mode: mode,
            update_marker: marker,
            temperature: TEMP_USE_AMBIENT,
            flags: if waveform == WAVEFORM_MODE_DU {
                EPDC_FLAG_FORCE_MONOCHROME
            } else {
                0
            },
            ..Default::default()
        };
        self.send_update_ioctl(MXCFB_SEND_UPDATE_REX, &update)
    }

    /// Issue `MXCFB_SEND_UPDATE_MTK` (96-byte struct, MediaTek devices).
    /// Returns whether the ioctl succeeded.
    fn send_update_mtk(
        &self,
        region: UpdateRect,
        waveform: u32,
        mode: u32,
        marker: u32,
        y4: bool,
    ) -> bool {
        let update = mtk_update(region, waveform, mode, marker, y4);
        self.send_update_ioctl(MXCFB_SEND_UPDATE_MTK, &update)
    }

    /// Issue `MXCFB_SEND_UPDATE_ZELDA` (88-byte struct, KOA2/KOA3 — Oasis 2/3).
    /// Returns whether the ioctl succeeded.
    fn send_update_zelda(&self, region: UpdateRect, waveform: u32, mode: u32, marker: u32) -> bool {
        let update = UpdateRequestZelda {
            update_region: region,
            waveform_mode: waveform,
            update_mode: mode,
            update_marker: marker,
            temperature: TEMP_USE_AMBIENT,
            flags: if waveform == WAVEFORM_MODE_DU {
                EPDC_FLAG_FORCE_MONOCHROME
            } else {
                0
            },
            ..Default::default()
        };
        self.send_update_ioctl(MXCFB_SEND_UPDATE_ZELDA, &update)
    }

    /// Apply a request resolved against the current framebuffer bounds.
    pub(crate) fn refresh_planned(&self, refresh: ResolvedRefresh) -> io::Result<()> {
        self.refresh_planned_inner(refresh, None).map(|_| ())
    }

    pub(crate) fn refresh_planned_scene(
        &self,
        refresh: ResolvedRefresh,
        pixels: ScenePixels<'_>,
        mode: GrayMode,
    ) -> io::Result<()> {
        self.refresh_planned_inner(refresh, Some(FallbackScene { pixels, mode }))
            .map(|_| ())
    }

    fn refresh_planned_inner(
        &self,
        refresh: ResolvedRefresh,
        scene: Option<FallbackScene<'_>>,
    ) -> io::Result<SubmittedUpdate<'_>> {
        let refresh = self.resolve_dithering(refresh);
        let waveform = waveform_for_driver(refresh.waveform, self.update_variant.get());
        self.send_update_scene(
            UpdateRect {
                top: refresh.region.y as u32,
                left: refresh.region.x as u32,
                width: refresh.region.width,
                height: refresh.region.height,
            },
            waveform,
            if refresh.flashing {
                UPDATE_MODE_FULL
            } else {
                UPDATE_MODE_PARTIAL
            },
            scene,
        )
    }

    /// Full-screen GC16 refresh
    pub(crate) fn refresh_full(&self) -> io::Result<()> {
        self.send_update(
            UpdateRect {
                top: 0,
                left: 0,
                width: self.width,
                height: self.height,
            },
            WAVEFORM_MODE_GC16,
            UPDATE_MODE_FULL,
        )
    }

    /// Submit a full frame and retain its independent accepted marker.
    pub(crate) fn refresh_full_checked(&self) -> io::Result<SubmittedUpdate<'_>> {
        self.refresh_full_inner(None)
    }

    pub(crate) fn refresh_full_scene_checked(
        &self,
        pixels: ScenePixels<'_>,
        mode: GrayMode,
    ) -> io::Result<SubmittedUpdate<'_>> {
        self.refresh_full_inner(Some(FallbackScene { pixels, mode }))
    }

    fn refresh_full_inner(
        &self,
        scene: Option<FallbackScene<'_>>,
    ) -> io::Result<SubmittedUpdate<'_>> {
        let region = UpdateRect {
            top: 0,
            left: 0,
            width: self.width,
            height: self.height,
        };
        self.send_update_scene(region, WAVEFORM_MODE_GC16, UPDATE_MODE_FULL, scene)
    }

    /// Observe the accepted SEND identity without issuing an ioctl or a wait.
    pub(crate) fn last_submitted_marker(&self) -> Option<u32> {
        self.update_markers.last_submitted()
    }

    /// Keep legacy best-effort handoff for ordinary scenes. Raw scene ownership
    /// cannot advance past a failed completion barrier or an uncertain SEND.
    pub(crate) fn wait_for_native_handoff(&self) -> io::Result<()> {
        self.require_known_dithering_delivery()?;
        let strict = self.raw_scene()
            || self
                .last_accepted_dithering
                .get()
                .is_some_and(|accepted| accepted.raw_scene);
        let result = self.wait_for_update_complete_checked();
        if strict { result } else { Ok(()) }
    }

    /// Settle preceding asynchronous reads before changing mapped pixels.
    /// No accepted update means there is no preceding scanout to settle.
    pub(crate) fn wait_for_pending_update_complete_checked(&self) -> io::Result<()> {
        self.require_known_dithering_delivery()?;
        let Some(marker) = self.update_markers.last_submitted() else {
            return Ok(());
        };
        self.wait_for_accepted_with(marker, |target| self.wait_for_marker_ioctl(target))
    }

    /// Use the accepted update's identity, without waiting on a rejected frame.
    /// Some vendor kernels return success even when their internal wait times
    /// out; this acknowledges the ioctl, not independent panel completion.
    pub(crate) fn wait_for_update_complete_checked(&self) -> io::Result<()> {
        self.require_known_dithering_delivery()?;
        let marker = self
            .update_markers
            .last_submitted()
            .ok_or_else(|| io::Error::other("no EPDC update was successfully submitted"))?;
        self.wait_for_accepted_with(marker, |target| self.wait_for_marker_ioctl(target))
    }

    /// Completion belongs to this receipt, never to a later frame or counter.
    pub(crate) fn wait_for_submission_complete_checked(
        &self,
        submission: &SubmittedUpdate<'_>,
    ) -> io::Result<()> {
        self.wait_for_submission_complete_with(submission, |target| {
            self.wait_for_marker_ioctl(target)
        })
    }

    fn wait_for_submission_complete_with(
        &self,
        submission: &SubmittedUpdate<'_>,
        wait: impl FnMut(u32) -> io::Result<()>,
    ) -> io::Result<()> {
        self.require_known_dithering_delivery()?;
        if !std::ptr::eq(self, submission.framebuffer) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "the update receipt belongs to a different framebuffer owner",
            ));
        }
        self.wait_for_accepted_with(submission.marker, wait)
    }

    fn wait_for_accepted_with(
        &self,
        marker: u32,
        mut wait: impl FnMut(u32) -> io::Result<()>,
    ) -> io::Result<()> {
        self.require_known_dithering_delivery()?;
        let strict = self.raw_scene()
            || self
                .last_accepted_dithering
                .get()
                .is_some_and(|accepted| accepted.marker == marker && accepted.raw_scene);
        let mut attempted = false;
        let result = self.update_markers.wait_for_submitted(marker, |target| {
            attempted = true;
            wait(target)
        });
        // A foreign, superseded or absent receipt is a local input error, not
        // evidence of an uncertain kernel outcome. Only actual failed waits latch.
        if strict
            && attempted
            && let Err(error) = &result
        {
            self.block_uncertain_dithering_delivery(error);
        }
        result
    }

    fn wait_for_marker_ioctl(&self, target: u32) -> io::Result<()> {
        let mut marker = UpdateMarkerData {
            update_marker: target,
            collision_test: 0,
        };
        // SAFETY: The complete marker ABI buffer stays live for this call.
        let result = unsafe {
            libc::ioctl(
                self.file.as_raw_fd(),
                MXCFB_WAIT_FOR_UPDATE_COMPLETE as _,
                &mut marker as *mut _,
            )
        };
        if result >= 0 {
            Ok(())
        } else {
            Err(io::Error::last_os_error())
        }
    }

    /// Partial refresh of a dirty rectangle
    pub(crate) fn refresh_region(
        &self,
        origin: slint::PhysicalPosition,
        size: slint::PhysicalSize,
    ) -> io::Result<()> {
        if size.width == 0 || size.height == 0 {
            return Ok(());
        }
        self.send_update(
            UpdateRect {
                top: origin.y as u32,
                left: origin.x as u32,
                width: size.width,
                height: size.height,
            },
            WAVEFORM_MODE_AUTO,
            UPDATE_MODE_PARTIAL,
        )
    }
}

#[cfg(test)]
fn submit_update(
    selected: &Cell<Option<UpdateVariant>>,
    send: impl FnMut(UpdateVariant) -> io::Result<()>,
) -> io::Result<()> {
    submit_update_with_policy(selected, false, send)
}

fn submit_update_with_policy(
    selected: &Cell<Option<UpdateVariant>>,
    stop_on_uncertain: bool,
    mut send: impl FnMut(UpdateVariant) -> io::Result<()>,
) -> io::Result<()> {
    match selected.get() {
        Some(UpdateVariant::Unsupported) => Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "No supported framebuffer update ioctl was accepted",
        )),
        Some(variant) => send(variant),
        None => {
            let mut last_error = None;
            for variant in UpdateVariant::PROBE_ORDER {
                match send(variant) {
                    Ok(()) => {
                        selected.set(Some(variant));
                        return Ok(());
                    }
                    Err(error) => {
                        if stop_on_uncertain && !explicit_update_rejection(error.raw_os_error()) {
                            return Err(error);
                        }
                        last_error = Some(error);
                    }
                }
            }
            selected.set(Some(UpdateVariant::Unsupported));
            if stop_on_uncertain {
                return Err(last_error.unwrap_or_else(|| io::Error::other("empty update probe")));
            }
            Err(io::Error::new(
                io::ErrorKind::Unsupported,
                format!(
                    "No supported framebuffer update ioctl was accepted: {}",
                    last_error.unwrap_or_else(|| io::Error::other("empty update probe"))
                ),
            ))
        }
    }
}

fn aligned_dither_region(region: UpdateRect, width: u32, height: u32) -> UpdateRect {
    if region.width == 0 || region.height == 0 {
        return region;
    }
    let left = region.left.min(width);
    let top = region.top.min(height);
    if left == width || top == height {
        return UpdateRect {
            left,
            top,
            width: 0,
            height: 0,
        };
    }
    let right = (u64::from(region.left) + u64::from(region.width)).min(u64::from(width));
    let bottom = (u64::from(region.top) + u64::from(region.height)).min(u64::from(height));
    let left = left / 8 * 8;
    let top = top / 8 * 8;
    let right = right.div_ceil(8).saturating_mul(8).min(u64::from(width)) as u32;
    let bottom = bottom.div_ceil(8).saturating_mul(8).min(u64::from(height)) as u32;
    UpdateRect {
        left,
        top,
        width: right - left,
        height: bottom - top,
    }
}

fn mtk_update(
    region: UpdateRect,
    waveform: u32,
    mode: u32,
    marker: u32,
    y4: bool,
) -> UpdateRequestMtk {
    let y4 = y4 && waveform == WAVEFORM_MODE_GC16;
    UpdateRequestMtk {
        update_region: region,
        waveform_mode: waveform,
        update_mode: mode,
        update_marker: marker,
        temperature: TEMP_USE_AMBIENT,
        flags: if y4 {
            MTK_EPDC_FLAG_USE_DITHERING_Y4
        } else if waveform == WAVEFORM_MODE_DU {
            EPDC_FLAG_FORCE_MONOCHROME
        } else {
            0
        },
        // MTK chooses Y8-to-Y4 processing through flags. These EPDCv2 fields
        // remain zero; quant_bit=7 is an i.MX convention, not this MTK ABI.
        dither_mode: 0,
        quant_bit: 0,
        hist_bw_waveform_mode: if y4 { WAVEFORM_MODE_DU } else { 0 },
        hist_gray_waveform_mode: if y4 { WAVEFORM_MODE_GC16 } else { 0 },
        ..Default::default()
    }
}

fn explicit_update_rejection(errno: Option<i32>) -> bool {
    matches!(errno, Some(libc::EINVAL | libc::ENOTTY))
}

impl Drop for Framebuffer {
    fn drop(&mut self) {
        unsafe { libc::munmap(self.map as *mut libc::c_void, self.len) };
    }
}

fn waveform_for_driver(waveform: RefreshWaveform, variant: Option<UpdateVariant>) -> u32 {
    match waveform {
        RefreshWaveform::Auto => WAVEFORM_MODE_AUTO,
        RefreshWaveform::Gc16 => WAVEFORM_MODE_GC16,
        RefreshWaveform::Du => WAVEFORM_MODE_DU,
        RefreshWaveform::A2 => match variant {
            Some(UpdateVariant::Legacy) => WAVEFORM_MODE_A2_LEGACY,
            Some(UpdateVariant::Rex | UpdateVariant::Zelda | UpdateVariant::Mtk) => {
                WAVEFORM_MODE_A2_MODERN
            }
            _ => WAVEFORM_MODE_AUTO,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn native_information() -> (FbVarScreeninfo, FbFixScreeninfo) {
        (
            FbVarScreeninfo {
                xres: 17,
                yres: 11,
                xres_virtual: 17,
                yres_virtual: 11,
                bits_per_pixel: 8,
                grayscale: 1,
                rotate: 1,
                ..Default::default()
            },
            FbFixScreeninfo {
                line_length: 20,
                smem_len: 220,
                ..Default::default()
            },
        )
    }

    #[test]
    fn native_layout_identity_excludes_only_the_admitted_rotation() {
        let (mut variable, mut fixed) = native_information();
        let expected = FramebufferLayout::from_information(&variable, &fixed).unwrap();
        variable.rotate = 3;
        assert_eq!(
            FramebufferLayout::from_information(&variable, &fixed).unwrap(),
            expected
        );
        variable.grayscale = 2;
        assert_ne!(
            FramebufferLayout::from_information(&variable, &fixed).unwrap(),
            expected
        );
        variable.grayscale = 1;
        fixed.smem_start = 4096;
        assert_ne!(
            FramebufferLayout::from_information(&variable, &fixed).unwrap(),
            expected
        );
        fixed.smem_start = 0;
        variable.red.offset = 1;
        assert_ne!(
            FramebufferLayout::from_information(&variable, &fixed).unwrap(),
            expected
        );
    }

    #[test]
    fn shifted_truncated_or_inconsistent_native_mappings_are_rejected() {
        for invalid_case in 0..6 {
            let (mut variable, mut fixed) = native_information();
            match invalid_case {
                0 => variable.xoffset = 1,
                1 => variable.yoffset = 1,
                2 => variable.xres_virtual = 16,
                3 => variable.yres_virtual = 10,
                4 => fixed.line_length = 16,
                _ => fixed.smem_len = 219,
            }
            assert_eq!(
                FramebufferLayout::from_information(&variable, &fixed)
                    .unwrap_err()
                    .kind(),
                io::ErrorKind::InvalidData
            );
        }
    }

    #[test]
    fn protected_layout_tokens_are_numeric_and_cover_the_mapping_contract() {
        let observed = NativeFramebufferObservation::for_test_rotation(1);
        let token = observed.layout_token();
        assert_eq!(token.split(':').count(), 35);
        assert!(
            token
                .bytes()
                .all(|byte| byte.is_ascii_digit() || byte == b':')
        );
        assert_eq!(
            token,
            NativeFramebufferObservation::for_test_rotation(3).layout_token()
        );
    }

    #[test]
    fn accepted_driver_is_cached_and_a_later_rejection_is_reported() {
        let selected = Cell::new(None);
        let mut probes = Vec::new();
        submit_update(&selected, |variant| {
            probes.push(variant);
            if variant == UpdateVariant::Rex {
                Ok(())
            } else {
                Err(io::Error::from_raw_os_error(libc::EINVAL))
            }
        })
        .unwrap();
        assert_eq!(probes, [UpdateVariant::Legacy, UpdateVariant::Rex]);
        assert_eq!(selected.get(), Some(UpdateVariant::Rex));
        let error = submit_update(&selected, |variant| {
            assert_eq!(variant, UpdateVariant::Rex);
            Err(io::Error::from_raw_os_error(libc::EIO))
        })
        .unwrap_err();
        assert_eq!(error.raw_os_error(), Some(libc::EIO));
    }

    #[test]
    fn unsupported_driver_cannot_be_reported_as_a_successful_submission() {
        let selected = Cell::new(None);
        let error = submit_update(&selected, |_| {
            Err(io::Error::from_raw_os_error(libc::ENOTTY))
        })
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::Unsupported);
        assert_eq!(selected.get(), Some(UpdateVariant::Unsupported));
        let error = submit_update(&selected, |_| {
            panic!("unsupported ioctl must not be retried")
        })
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::Unsupported);
    }

    #[test]
    fn marker_identity_survives_abi_probes_and_changes_for_cached_submissions() {
        for accepted in UpdateVariant::PROBE_ORDER {
            let selected = Cell::new(None);
            let markers = UpdateMarkers::default();
            let mut probes = Vec::new();
            markers
                .submit(|marker| {
                    submit_update(&selected, |variant| {
                        probes.push((variant, marker));
                        if variant == accepted {
                            Ok(())
                        } else {
                            Err(io::Error::from_raw_os_error(libc::ENOTTY))
                        }
                    })
                })
                .unwrap();
            assert!(probes.iter().all(|(_, marker)| *marker == 1));
            assert_eq!(probes.last(), Some(&(accepted, 1)));
            assert_eq!(selected.get(), Some(accepted));
            probes.clear();
            markers
                .submit(|marker| {
                    submit_update(&selected, |variant| {
                        probes.push((variant, marker));
                        Ok(())
                    })
                })
                .unwrap();
            assert_eq!(probes, [(accepted, 2)]);
            markers
                .wait_for_last_submitted(|target| {
                    assert_eq!(target, 2);
                    Ok(())
                })
                .unwrap();

            assert!(
                markers
                    .submit(|_| {
                        submit_update(&selected, |_| Err(io::Error::from_raw_os_error(libc::EIO)))
                    })
                    .is_err()
            );
            markers
                .wait_for_last_submitted(|target| {
                    assert_eq!(target, 2, "A rejected update cannot replace the fence");
                    Ok(())
                })
                .unwrap();
        }
    }

    #[test]
    fn a_rejected_probe_cannot_create_a_completion_target() {
        let selected = Cell::new(None);
        let markers = UpdateMarkers::default();
        let mut probes = Vec::new();
        assert!(
            markers
                .submit(|marker| {
                    submit_update(&selected, |variant| {
                        probes.push((variant, marker));
                        Err(io::Error::from_raw_os_error(libc::ENOTTY))
                    })
                })
                .is_err()
        );
        assert_eq!(
            probes,
            UpdateVariant::PROBE_ORDER.map(|variant| (variant, 1))
        );
        assert!(
            markers
                .submit(|_| { submit_update(&selected, |_| panic!("unsupported ABI was cached")) })
                .is_err()
        );
        markers
            .wait_for_last_submitted(|_| panic!("no ioctl accepted either update"))
            .unwrap();
    }

    #[test]
    fn waveform_constants_follow_the_detected_driver_family() {
        assert_eq!(
            waveform_for_driver(RefreshWaveform::A2, Some(UpdateVariant::Legacy)),
            4
        );
        for variant in [UpdateVariant::Rex, UpdateVariant::Zelda, UpdateVariant::Mtk] {
            assert_eq!(waveform_for_driver(RefreshWaveform::A2, Some(variant)), 6);
        }
        assert_eq!(
            waveform_for_driver(RefreshWaveform::A2, None),
            WAVEFORM_MODE_AUTO
        );
        assert_eq!(
            waveform_for_driver(RefreshWaveform::A2, Some(UpdateVariant::Unsupported)),
            WAVEFORM_MODE_AUTO
        );
        assert_eq!(
            waveform_for_driver(RefreshWaveform::Du, Some(UpdateVariant::Mtk)),
            1
        );
        assert_eq!(
            waveform_for_driver(RefreshWaveform::Gc16, Some(UpdateVariant::Mtk)),
            2
        );
    }
}
