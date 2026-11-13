# Hardware Y4 interface boundaries

The application selects software or hardware image preparation separately from
hardware support. This backend's `DitheringHandle` describes the raw image
already published to the scene. The request remains set after definite hardware
rejection until that image is replaced. `hardware_y4_enabled()` means requested
and available. `dithering_delivery_uncertain()` is a persistent failed-owner
latch, not another selectable mode.

## Reviewed sources

- [BiliComics 7241a85fac8aa032b79f4e7f2f6eb29aacdbebd3](https://github.com/moooyo/bilicomics/tree/7241a85fac8aa032b79f4e7f2f6eb29aacdbebd3/vendor/slint-backend-kindle)
  supplies the adapted policy and rejection fixtures.
- [FBInk ded92fa510c957e0157c92ae83bd5c51af710c05,
  `eink/mtk-kindle.h`](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/eink/mtk-kindle.h)
  defines MTK Y4 as `0x4000`; REGAL has the distinct `0x8000` flag.
- [FBInk at the same revision,
  `fbink_device_id.c`](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink_device_id.c)
  supplies the fixed-width identity transport reference.
- [KOReader-base f64c3eb11049e9a75189f186d7345d97c07d87b9,
  `ffi/framebuffer_mxcfb.lua`](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer_mxcfb.lua)
  supplies the MTK flag selection, zero EPDCv2 dithering fields, histogram
  waveforms and eight-pixel region alignment reference.
- [KOReader 7c38cf104cb9ea89df243e47ec820ef4aa97cab8,
  `frontend/device/kindle/device.lua`](https://github.com/koreader/koreader/blob/7c38cf104cb9ea89df243e47ec820ef4aa97cab8/frontend/device/kindle/device.lua)
  supplies the reviewed Scribe 2022 and 2024 identity codes.

## Admission and rendering

Read exactly the first 16 bytes of `/proc/usid`; trailing NUL padding does not
change the identity. Require a leading `G`, uppercase ASCII/digits throughout,
and one reviewed model code at bytes 3 through 5. Complete identifiers are not
retained or logged. Require actual 8-bpp native grayscale with a grayscale tag
of 1 or 2. Paperwhite 5, unknown devices and color devices are not admitted.
Hardware updates use the MTK ABI exclusively. Neither profile selection,
resolution nor successful acceptance of a different ioctl grants eligibility.

The hardware path preserves all Gray8 inputs until driver scanout. CPU palette
quantization and Bayer processing belong to the software path. Every update
while raw content is retained uses GC16. Hardware regions expand outward to
eight-pixel boundaries and clip to visible bounds. This is MDP Y8-to-Y4
processing, not a claim that the panel natively displays 256 physical levels.

Definite Y4 rejection permits a checked canonical-scene software replacement
under a new marker. Unknown delivery permits no automatic rewrite, resubmission,
or acknowledgement of an older marker. The owner's existing native restoration
is responsible for recovery after the event loop returns an error. No persistent
device preference or firmware mode is written.

## Evidence scope

Controlled fixtures do not open native devices. Tests and builds run only in
the project-designated verification environment. Actual firmware acceptance
needs an exclusive, bounded physical-device batch with independently identified
Aidoku artifacts and restoration/release evidence. BiliComics historical device
results remain reference evidence for their exact source and executable only.
