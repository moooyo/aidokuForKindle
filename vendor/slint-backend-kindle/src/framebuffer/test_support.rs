//! Anonymous memory fixtures for the real framebuffer write and fence methods.

use std::os::fd::{FromRawFd, OwnedFd};

use super::{Framebuffer, markers::UpdateMarkers};

impl Framebuffer {
    /// Allocate an anonymous mapping and an ordinary pipe, never a native device.
    pub(crate) fn memory_for_test(
        width: u32,
        height: u32,
        stride: usize,
        bytes_per_pixel: usize,
        initial: u8,
    ) -> std::io::Result<Self> {
        assert!(width > 0 && height > 0);
        assert!(matches!(bytes_per_pixel, 1 | 4));
        assert!(stride >= width as usize * bytes_per_pixel);
        let len = stride.checked_mul(height as usize).unwrap();
        let mut descriptors = [-1; 2];
        // SAFETY: Both descriptor slots are writable and are owned immediately.
        if unsafe { libc::pipe(descriptors.as_mut_ptr()) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: A successful pipe returned two fresh owned descriptors.
        let file = std::fs::File::from(unsafe { OwnedFd::from_raw_fd(descriptors[0]) });
        // SAFETY: This is the other fresh descriptor from the same pipe.
        let _writer = unsafe { OwnedFd::from_raw_fd(descriptors[1]) };
        // SAFETY: An anonymous private mapping has no file or device backing.
        let map = unsafe {
            libc::mmap(
                std::ptr::null_mut(),
                len,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_PRIVATE | libc::MAP_ANONYMOUS,
                -1,
                0,
            )
        };
        if map == libc::MAP_FAILED {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: The newly owned mapping is writable for exactly `len` bytes.
        unsafe { std::ptr::write_bytes(map.cast::<u8>(), initial, len) };
        Ok(Self {
            file,
            map: map.cast(),
            len,
            width,
            height,
            stride,
            bytes_per_pixel,
            native_gray8: bytes_per_pixel == 1,
            retained_layout: None,
            inverted_gray8: std::cell::Cell::new(false),
            update_variant: std::cell::Cell::new(None),
            update_markers: UpdateMarkers::default(),
            dithering: crate::DitheringHandle::default(),
            dithering_frame: std::cell::Cell::new(super::DitheringFrame::default()),
            dithering_delivery_failure: std::cell::Cell::new(None),
            last_accepted_dithering: std::cell::Cell::new(None),
            canonical_rewrite_count: std::cell::Cell::new(0),
            mapped_software_dithered: std::cell::Cell::new(false),
        })
    }

    pub(crate) fn mapped_bytes_for_test(&self) -> &[u8] {
        // SAFETY: This immutable borrow cannot outlive or mutate the owned mapping.
        unsafe { std::slice::from_raw_parts(self.map, self.len) }
    }
}
