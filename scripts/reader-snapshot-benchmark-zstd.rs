//! Host-only zstd oracle using an existing system library through dlopen.
//!
//! No production target links this module. Context allocation, compression,
//! decompression, and context release belong to the codec timer. Library load,
//! symbol resolution, and version inspection have a separate initialization timer.

use std::{
    ffi::{CStr, c_char, c_int, c_uint, c_void},
    mem,
    sync::OnceLock,
    time::Instant,
};

use anyhow::{Context, Result, anyhow, ensure};
use serde_json::{Value, json};

use super::{Case, MAX_SNAPSHOT_BYTES, SnapshotHeader};

const MAGIC: &[u8; 8] = b"AKZST001";
const FRAME_MAGIC: &[u8; 4] = b"\x28\xb5\x2f\xfd";
const COMPRESSION_LEVEL: c_int = 100;
const CONTENT_SIZE_FLAG: c_int = 200;
const CHECKSUM_FLAG: c_int = 201;

type ContextPointer = *mut c_void;
type CreateContext = unsafe extern "C" fn() -> ContextPointer;
type FreeContext = unsafe extern "C" fn(ContextPointer) -> usize;
type SetParameter = unsafe extern "C" fn(ContextPointer, c_int, c_int) -> usize;
type Compress =
    unsafe extern "C" fn(ContextPointer, *mut c_void, usize, *const c_void, usize) -> usize;
type Decompress =
    unsafe extern "C" fn(ContextPointer, *mut c_void, usize, *const c_void, usize) -> usize;
type CompressBound = unsafe extern "C" fn(usize) -> usize;
type ContentSize = unsafe extern "C" fn(*const c_void, usize) -> u64;
type FrameSize = unsafe extern "C" fn(*const c_void, usize) -> usize;
type IsError = unsafe extern "C" fn(usize) -> c_uint;
type ErrorName = unsafe extern "C" fn(usize) -> *const c_char;
type VersionNumber = unsafe extern "C" fn() -> c_uint;
type VersionString = unsafe extern "C" fn() -> *const c_char;

struct Api {
    // The process retains this handle while its immutable function pointers live.
    _handle: usize,
    create_compressor: CreateContext,
    free_compressor: FreeContext,
    create_decoder: CreateContext,
    free_decoder: FreeContext,
    set_parameter: SetParameter,
    compress: Compress,
    decompress: Decompress,
    compress_bound: CompressBound,
    content_size: ContentSize,
    frame_size: FrameSize,
    is_error: IsError,
    error_name: ErrorName,
    version_number: u32,
    version: String,
    library_path: String,
}

static API: OnceLock<std::result::Result<Api, String>> = OnceLock::new();

unsafe fn symbol<T: Copy>(handle: *mut c_void, name: &CStr) -> Result<T> {
    ensure!(
        mem::size_of::<T>() == mem::size_of::<*mut c_void>(),
        "Unexpected function-pointer ABI"
    );
    let address = unsafe { libc::dlsym(handle, name.as_ptr()) };
    ensure!(
        !address.is_null(),
        "Missing system zstd symbol: {}",
        name.to_string_lossy()
    );
    // Each caller supplies the documented signature for this exact public symbol.
    Ok(unsafe { mem::transmute_copy(&address) })
}

impl Api {
    fn load() -> Result<Self> {
        let handle =
            unsafe { libc::dlopen(c"libzstd.so.1".as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL) };
        if handle.is_null() {
            let error = unsafe { libc::dlerror() };
            let message = if error.is_null() {
                "Unknown dlopen error".into()
            } else {
                unsafe { CStr::from_ptr(error) }
                    .to_string_lossy()
                    .into_owned()
            };
            return Err(anyhow!("Load the existing system libzstd: {message}"));
        }
        let loaded = (|| -> Result<Self> {
            let number: VersionNumber = unsafe { symbol(handle, c"ZSTD_versionNumber")? };
            let string: VersionString = unsafe { symbol(handle, c"ZSTD_versionString")? };
            let mut location: libc::Dl_info = unsafe { mem::zeroed() };
            let located = unsafe { libc::dladdr(number as *const c_void, &mut location) } != 0;
            let library_path = if located && !location.dli_fname.is_null() {
                unsafe { CStr::from_ptr(location.dli_fname) }
                    .to_string_lossy()
                    .into_owned()
            } else {
                "libzstd.so.1".into()
            };
            Ok(Self {
                _handle: handle as usize,
                create_compressor: unsafe { symbol(handle, c"ZSTD_createCCtx")? },
                free_compressor: unsafe { symbol(handle, c"ZSTD_freeCCtx")? },
                create_decoder: unsafe { symbol(handle, c"ZSTD_createDCtx")? },
                free_decoder: unsafe { symbol(handle, c"ZSTD_freeDCtx")? },
                set_parameter: unsafe { symbol(handle, c"ZSTD_CCtx_setParameter")? },
                compress: unsafe { symbol(handle, c"ZSTD_compress2")? },
                decompress: unsafe { symbol(handle, c"ZSTD_decompressDCtx")? },
                compress_bound: unsafe { symbol(handle, c"ZSTD_compressBound")? },
                content_size: unsafe { symbol(handle, c"ZSTD_getFrameContentSize")? },
                frame_size: unsafe { symbol(handle, c"ZSTD_findFrameCompressedSize")? },
                is_error: unsafe { symbol(handle, c"ZSTD_isError")? },
                error_name: unsafe { symbol(handle, c"ZSTD_getErrorName")? },
                version_number: unsafe { number() },
                version: unsafe { CStr::from_ptr(string()) }
                    .to_string_lossy()
                    .into_owned(),
                library_path,
            })
        })();
        if loaded.is_err() {
            unsafe { libc::dlclose(handle) };
        }
        loaded
    }

    fn check(&self, value: usize, operation: &str) -> Result<usize> {
        if unsafe { (self.is_error)(value) } != 0 {
            let message = unsafe { CStr::from_ptr((self.error_name)(value)) }.to_string_lossy();
            return Err(anyhow!("{operation}: {message}"));
        }
        Ok(value)
    }
}

fn api() -> Result<&'static Api> {
    match API.get_or_init(|| Api::load().map_err(|error| format!("{error:#}"))) {
        Ok(api) => Ok(api),
        Err(error) => Err(anyhow!(error.clone())),
    }
}

pub(super) fn initialize() -> Result<Value> {
    let start = Instant::now();
    let api = api()?;
    let initialization_us = start.elapsed().as_micros();
    Ok(json!({
        "library":api.library_path,
        "version":api.version,
        "version_number":api.version_number,
        "initialization_us":initialization_us,
        "initialization_scope":"First dlopen(RTLD_NOW), public-symbol resolution, dladdr and version inspection; no file hashing or codec context creation",
        "codec_timer":"Library initialization excluded and reported separately; context creation/release, frame checksum, allocation and codec work included",
        "frame_checksum":true,
        "content_size":true,
        "threads":"Default single-threaded ZSTD_compress2 context",
    }))
}

struct ContextGuard {
    pointer: ContextPointer,
    free: FreeContext,
}

impl Drop for ContextGuard {
    fn drop(&mut self) {
        let _ = unsafe { (self.free)(self.pointer) };
    }
}

fn frame(pixels: &[u8], level: c_int, checksum: bool) -> Result<Vec<u8>> {
    let api = api()?;
    let pointer = unsafe { (api.create_compressor)() };
    ensure!(!pointer.is_null(), "Allocate a zstd compression context");
    let _context = ContextGuard {
        pointer,
        free: api.free_compressor,
    };
    for (parameter, value) in [
        (COMPRESSION_LEVEL, level),
        (CONTENT_SIZE_FLAG, 1),
        (CHECKSUM_FLAG, c_int::from(checksum)),
    ] {
        api.check(
            unsafe { (api.set_parameter)(pointer, parameter, value) },
            "Configure the zstd frame",
        )?;
    }
    let bound = unsafe { (api.compress_bound)(pixels.len()) };
    ensure!(
        bound > 0 && bound <= super::imaging::MAX_PAGE_BYTES as usize,
        "Zstd compressed bound exceeds the artifact budget"
    );
    let mut output = Vec::<u8>::with_capacity(bound);
    // The C API initializes at most bound bytes; successful output length is
    // checked before exposing any of those bytes as an initialized Rust slice.
    let length = api.check(
        unsafe {
            (api.compress)(
                pointer,
                output.as_mut_ptr().cast(),
                bound,
                pixels.as_ptr().cast(),
                pixels.len(),
            )
        },
        "Compress the zstd pixels",
    )?;
    ensure!(length <= bound, "Zstd wrote beyond its advertised bound");
    unsafe { output.set_len(length) };
    Ok(output)
}

fn envelope(case: &Case, frame: &[u8]) -> Result<Vec<u8>> {
    let header = SnapshotHeader {
        version: 1,
        width: case.width,
        height: case.height,
        packed: false,
        source_sha256: case.source_sha256.clone(),
        render_metadata: case.png_metadata.clone(),
    };
    let header = serde_json::to_vec(&header)?;
    ensure!(header.len() <= 2_048, "Zstd snapshot metadata is oversized");
    let mut output = Vec::with_capacity(12 + header.len() + frame.len());
    output.extend_from_slice(MAGIC);
    output.extend_from_slice(&(header.len() as u32).to_le_bytes());
    output.extend_from_slice(&header);
    output.extend_from_slice(frame);
    Ok(output)
}

pub(super) fn encode(case: &Case, pixels: &[u8], level: c_int) -> Result<Vec<u8>> {
    ensure!(
        pixels.len() == case.width as usize * case.height as usize
            && pixels.len() <= MAX_SNAPSHOT_BYTES,
        "Zstd source pixels exceed their exact bounded dimensions"
    );
    envelope(case, &frame(pixels, level, true)?)
}

pub(super) fn decode(bytes: &[u8], case: &Case) -> Result<Vec<u8>> {
    ensure!(
        bytes.len() >= 12 && bytes.len() <= super::imaging::MAX_PAGE_BYTES as usize,
        "Zstd snapshot compressed length is invalid"
    );
    ensure!(&bytes[..8] == MAGIC, "Zstd snapshot magic differs");
    let header_len = u32::from_le_bytes(bytes[8..12].try_into()?) as usize;
    ensure!(
        header_len <= 2_048 && header_len <= bytes.len() - 12,
        "Zstd snapshot header length is invalid"
    );
    let header: SnapshotHeader = serde_json::from_slice(&bytes[12..12 + header_len])?;
    ensure!(
        header.version == 1
            && !header.packed
            && header.width == case.width
            && header.height == case.height,
        "Zstd snapshot dimensions, layout or version differ"
    );
    ensure!(
        header.source_sha256 == case.source_sha256 && header.render_metadata == case.png_metadata,
        "Zstd snapshot source or rendering identity differs"
    );
    let expected = usize::try_from(u64::from(header.width) * u64::from(header.height))?;
    ensure!(
        expected > 0 && expected <= MAX_SNAPSHOT_BYTES,
        "Zstd snapshot pixels exceed the byte budget"
    );
    let frame = &bytes[12 + header_len..];
    ensure!(
        frame.len() >= 6 && &frame[..4] == FRAME_MAGIC && frame[4] & 0x04 != 0,
        "Zstd snapshot requires a standard frame with a content checksum"
    );
    let api = api()?;
    let content_size = unsafe { (api.content_size)(frame.as_ptr().cast(), frame.len()) };
    ensure!(
        content_size == expected as u64,
        "Zstd frame must advertise the exact bounded output length"
    );
    let frame_size = api.check(
        unsafe { (api.frame_size)(frame.as_ptr().cast(), frame.len()) },
        "Read the complete zstd frame length",
    )?;
    ensure!(
        frame_size == frame.len(),
        "Zstd snapshot contains trailing bytes or another frame"
    );
    let pointer = unsafe { (api.create_decoder)() };
    ensure!(!pointer.is_null(), "Allocate a zstd decompression context");
    let _context = ContextGuard {
        pointer,
        free: api.free_decoder,
    };
    let mut output = Vec::<u8>::with_capacity(expected);
    // The destination is bounded by the validated dimensions. The decoder
    // verifies the frame checksum before successful bytes become a Rust slice.
    let length = api.check(
        unsafe {
            (api.decompress)(
                pointer,
                output.as_mut_ptr().cast(),
                expected,
                frame.as_ptr().cast(),
                frame.len(),
            )
        },
        "Decode and verify the zstd frame",
    )?;
    ensure!(
        length == expected,
        "Zstd decoded length differs from its identity"
    );
    unsafe { output.set_len(length) };
    Ok(output)
}

pub(super) fn audit_requirements(bytes: &[u8], case: &Case, pixels: &[u8]) -> Result<()> {
    let missing_checksum = envelope(case, &frame(pixels, 1, false)?)?;
    ensure!(
        decode(&missing_checksum, case).is_err(),
        "A zstd frame without its checksum was accepted"
    );
    let short = envelope(case, &frame(&pixels[..pixels.len() - 1], 1, true)?)?;
    ensure!(
        decode(&short, case).is_err(),
        "A short advertised zstd output was accepted"
    );
    let mut extra = pixels.to_vec();
    extra.push(0);
    let excess = envelope(case, &frame(&extra, 1, true)?)?;
    ensure!(
        decode(&excess, case).is_err(),
        "An excessive advertised zstd output was accepted"
    );
    let mut damaged_checksum = bytes.to_vec();
    *damaged_checksum
        .last_mut()
        .context("Zstd checksum is missing")? ^= 0x5a;
    ensure!(
        decode(&damaged_checksum, case).is_err(),
        "An incorrect zstd frame checksum was accepted"
    );
    let mut concatenated = bytes.to_vec();
    concatenated.extend_from_slice(&frame(pixels, 1, true)?);
    ensure!(
        decode(&concatenated, case).is_err(),
        "Concatenated zstd frames were accepted"
    );
    let mut wrong_render = case.clone();
    wrong_render.png_metadata = "A different render version or parameter identity".into();
    ensure!(
        decode(bytes, &wrong_render).is_err(),
        "A different zstd render identity was accepted"
    );
    Ok(())
}
