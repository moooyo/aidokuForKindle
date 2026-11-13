# Hardware Y4 dithering

Aidoku preserves its software Bayer default and the existing `dither` boolean.
The optional `dither_method` preference selects `software` or `hardware`; an
unknown method uses software processing. Turning dithering off retains the
existing sixteen-level conversion without the Bayer offset.

Hardware selection keeps the prepared Reader image in Gray8 until display
processing. It does not claim that a Kindle panel has 256 independently
addressable, stable grayscale levels. A framebuffer input histogram is not a
post-MDP readback or a measurement of panel reflectance.

## Device and driver boundary

The native backend admits only the reviewed monochrome Scribe 2022 and 2024
identities, an actual eight-bit grayscale framebuffer and the MediaTek update
interface. A UI design profile and screen resolution do not grant support.
Paperwhite 5, unknown devices, color formats and desktop simulations use software.

Identity acquisition reads exactly the first sixteen bytes of `/proc/usid` and
then validates their format and public model code. Proc transport padding,
including a trailing NUL, is not part of that identity. Short reads and errors
cannot grant support. Complete real serial numbers are never retained in code,
logs or public evidence.

The MediaTek Y4 flag is `0x4000`; `0x8000` is REGAL. `dither_mode` and
`quant_bit` remain zero. These parameters must not be copied from an older i.MX
ABI. Raw grayscale requests use GC16 and an outward eight-pixel refresh region
clipped to the visible screen. Normal non-raw UI refresh selection remains intact.

## Pixels and scene publication

`ImageOptions.hardware_dither` seals the effective preparation choice. Hardware
frames retain crop, resizing and contrast, but skip final software quantization.
The software and off paths retain their existing pixels. The mode participates
in rendered, decoded, retained-raster and prepared-next-frame identities; false
retains the legacy serialized software cache key.

The actual raw flag travels with prepared pixels, asynchronous results and the
displayed Reader frame. A pending preference change cannot change the processing
of the old visible image. Publishing its replacement updates the image and the
native request in the same UI transaction. Late results still have to pass the
existing generation, source and geometry checks.

Leaving Reader disables the request. A sleep-cover overlay temporarily disables
it and restores the retained Reader frame's mode when hidden. Resume uses that
retained frame, even when its preferences differ from a pending target. Existing
eight-bit download preparation remains eligible; hardware selection does not
silently disable the established download accelerator.

The backend freezes a frame's request and grayscale transform before writing.
An observation callback cannot change the parameters of an already written
frame. RGB fallback, canonical scene retention, portrait ownership, sleep fences
and marker ownership remain in the existing renderer.

## Failure handling

An explicit `EINVAL` or `ENOTTY` rejection disables hardware for the session.
Before rewriting any pixels, the backend settles the previously accepted update.
It then converts the retained canonical scene with software dithering, rewrites
the complete mapping and submits a fresh marker. Clearing a flag on unchanged
raw input would not be an adequate fallback.

An uncertain SEND or completion failure latches the framebuffer against further
writes and submissions. Later mode revisions cannot bypass that latch. The
native event loop stops and uses the existing restoration path instead of
blindly rewriting or resubmitting possibly in-flight data.

Diagnostics distinguish capability, request, accepted hardware treatment, actual
software conversion and uncertain delivery. An accepted fallback has a new write
sequence and marker, with an explicit replacement link to its original written
frame. Ordinary marker and token checks are unchanged.

## Fixed references and verification scope

This adaptation follows BiliComics commit
[`7241a85fac8aa032b79f4e7f2f6eb29aacdbebd3`](https://github.com/moooyo/bilicomics/commit/7241a85fac8aa032b79f4e7f2f6eb29aacdbebd3),
while retaining Aidoku's renderer, caches, lifecycle and frame-observation model.
Backend license and provenance notices remain in
[`vendor/slint-backend-kindle`](../vendor/slint-backend-kindle/AIDOKU_PROVENANCE.md).

Reviewed primary references include
[FBInk's MediaTek ABI](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/eink/mtk-kindle.h),
[KOReader's MTK submission and alignment](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer_mxcfb.lua),
and [KOReader's device policy](https://github.com/koreader/koreader/blob/7c38cf104cb9ea89df243e47ec820ef4aa97cab8/frontend/device/kindle/device.lua).

BiliComics' R68 evidence establishes its own accepted driver request and intact
Gray8 input on one Scribe 2022 running firmware 5.19.5. It is not Aidoku binary
acceptance. Aidoku verification must run on `ssh test-env`, with six Cargo jobs,
and any physical batch requires a fresh exclusive lease after preparation.
Private transport configuration, tokens and raw device identifiers stay outside
committed sources and public evidence.

## Aidoku validation results

Validation on 2026-11-13 ran through `ssh test-env`, with six Cargo jobs. The
matching runtime source inventory contained 304 inputs with inventory SHA-256
`60161474e69d34c1553be16ede45101ba9070ddf375bae0508525204862991aa`.

Completed host checks:

- Backend unit tests: 173 passed.
- Default application library tests: 753 passed, 5 ignored.
- Executable tests: 20 passed.
- Reader trace analyzer tests: 53 passed.
- Retained Reader and sleep UI matrix: passed across four layouts.
- Kindle-feature all-target type check: passed with `--offline --locked
  --no-default-features --features kindle --all-targets -j6`.
- Desktop pointer and persistence scenarios: 36 passed, covering Chinese
  Paperwhite 5 and Scribe portrait layouts, an English Scribe narrow column,
  and an English Paperwhite 5 landscape layout. These check selection,
  software fallback, cold starts and page turns; they do not establish native
  hardware application.

The ARM candidate used release optimization level 3, no debug information,
`lto=false`, and 16 codegen units. Its SHA-256 was
`7df3360348728dbdd407fe1a4a8312d1bc9531bb9060a82076f1e105fe7e71b8`.
The desktop functional candidate used the development profile at optimization
level 0; its SHA-256 was
`132a73b871ea8a9aeeb96ed5c948dc34c40c9cef8c1de9a6b95a480983bed3d0`.
No desktop performance comparison is claimed.

Two physical batches used isolated candidate files and a private database,
without replacing the production installation. Batch `b01` is retained as a
failed observation check: its palette results were correct, but four image
publications lacked accepted-marker attribution because a dithering transition
was incorrectly treated as presentation cancellation. The correction separates
that observation reason while retaining the actual full refresh and all
existing ownership, input, rotation, grayscale and sleep barriers. An armed
image publication is still required; a control-only revision cannot create a
Reader observation.

Batch `b02` passed the strict process, startup, writer-to-accepted-marker,
conservative timing, native palette and cold-database continuity checks:

| Capture | Distinct input values | Writer / accepted sequence | Accepted marker | Hardware applied |
| --- | ---: | --- | ---: | --- |
| Hardware | 256 | 7 / 7 | 8 | Yes |
| Off | 16 | 8 / 8 | 9 | No |
| Software | 16 | 9 / 9 | 10 | No |
| Hardware saved | 256 | 10 / 10 | 11 | Yes |
| Cold hardware | 256 | 6 / 6 | 7 | Yes |

All five accepted observations had software fallback and delivery uncertainty
clear. Both candidate processes exited normally, native state was restored,
production program and data were preserved, all owned transports and staging
files were removed, and the fresh lease was released with owner/token checks
before analysis.

The exported projections are conditional on the recorded native `Y8INV` startup
mode and apply inversion once, without an additional direction-based rotation.
`exact_coordinate_pattern_qualified` remains false. These results establish
intact 256-value input and accepted Y4 requests for the hardware cases; they do
not establish post-MDP pixels, physical panel completion, optical measurements,
or 256 stable panel grayscale levels.

Full Kindle-feature library test builds exceeded the remote worker's available
memory in two configurations and did not complete. The standard ThinLTO release
build also exceeded the available memory budget and was stopped. Neither is
counted as passed. The completed all-target type check, bounded ARM build and
isolated native acceptance above retain their stated scope. Original failed
build and observation logs are preserved separately from the successful runs.
