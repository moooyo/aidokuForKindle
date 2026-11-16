# Hardware Y4 dithering

The product menu now exposes Off, Bayer8 and high-precision Floyd-Steinberg,
as documented in [software dithering settings](SOFTWARE_DITHER_SETTINGS_20261116.md).
Persisted enabled software/Y4 choices migrate to Bayer8; disabled choices stay
disabled. The hardware paths described below remain low-level research and
driver qualification interfaces, not a fourth product setting.
Application attachment preserves the saved selection. Turning dithering off
retains sixteen-level conversion without a spatial pattern.
The Fast preset keeps its persistent monochrome frame in the software path;
it does not enable Y4 or schedule a grayscale restoration after interaction.

Hardware selection keeps the prepared Reader image in Gray8 until display
processing. It does not claim that a Kindle panel has 256 independently
addressable, stable grayscale levels. A framebuffer input histogram is not a
post-MDP readback or a measurement of panel reflectance.

For the user's intended spatial simulation of finer gray transitions, the
[2026-11-16 prototype comparison](SPATIAL_GRAYSCALE_RESEARCH_20261116.md)
demonstrates 256 increasing regional means on the physical Scribe with Bayer8
and error diffusion. This is the current feasibility evidence for that goal.

The stored physical gradient was reanalyzed on 2026-11-15 using equal-weight
means of post-MDP codes within each original input stripe. Off had 16 distinct
stripe means; Y4 had 217, with 39 adjacent plateaus and 8 adjacent reversals.
Both outputs still contained only 16 per-pixel codes. The 7-8-pixel stripes
sample different dithering phases, and encoded means are not optical luminance.
Consequently, 217 is a statistic of this fixture, not a qualified effective
grayscale count; neither 217 nor 256 independently distinguishable displayed
levels is established. The [analysis and original source hashes](review/display-modes-20261115/y4-stripe-average-analysis.json)
are retained separately from the original readback record. This reanalysis
used stored buffers on test-env and made no new device requests.

## Device and driver boundary

The [kindle-eink256 comparison](KINDLE_EINK256_COMPARISON_20261116.md) explains
how the Android application's CPU error diffusion differs from Aidoku's
Reader Bayer processing and native Y4 processing.

The screen-refresh settings now include a
[256-swatch input test](review/grayscale-256-20261115/README.md), with exact
RGB values `(index, index, index)` for indices 0 through 255. It is drawn
directly by Slint and does not pass through the comic-image quantizer. This
settings-only scene does not enable the Reader-specific Y4 policy; its input
histogram and desktop captures do not establish a post-MDP or optical count.

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
ABI. Raw grayscale requests use compatible GC16, GL16 or GLR16 paths, or an
eligible AUTO swipe request. GC16 remains the clearing and rejection fallback.
Hardware regions expand outward to eight-pixel boundaries and clip to the
visible screen. Native inverted Gray8 maps GC16 to GCK16 and GL16/GLR16 to
GLKW16, independently of semantic UI night colors. Ordinary non-raw UI refresh
selection remains separate from reader presets.

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

The backend waits for the previous accepted update before changing framebuffer
bytes, then freezes a frame's request and grayscale transform before writing.
An observation callback cannot change the parameters of an already written
frame. RGB fallback, canonical scene retention, portrait ownership, sleep fences
and marker ownership remain in the existing renderer.

## Failure handling

An explicit `EINVAL` or `ENOTTY` rejection of an advanced waveform first disables
advanced refresh for that owner and retries GC16 under a fresh marker while
retaining the same raw input. A definite rejection of Y4 itself disables
hardware for the session.
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

Initial Kindle-feature library test builds exceeded the remote worker's available
memory in two configurations and did not complete. The standard ThinLTO release
build also exceeded the available memory budget and was stopped. Neither is
counted as passed. The completed all-target type check, bounded ARM build and
isolated native acceptance above retain their stated scope. Original failed
build and observation logs are preserved separately from the successful runs.

## Follow-up verification after resource recovery

After freeing worker disk space and increasing swap from 8 GiB to 16 GiB on
2026-11-13, the following checks completed on source commit
`26c006144b242cfb0961ff3a69ebd955294a9499`. Its 304 runtime inputs matched the
inventory above. Build output, temporary files and private cache overlays used
the worker's ext4 filesystem. The checks ran sequentially with six Cargo jobs.

| Check | Result |
| --- | --- |
| Library with `--no-default-features --features kindle` | 766 passed, 5 ignored |
| Library with default desktop features and `--features kindle` | 766 passed, 5 ignored |
| Standard ARM release with ThinLTO | Passed |

The standard release build used `armv7-unknown-linux-musleabihf`. The live
compiler arguments confirmed optimization level 3, `lto=thin`,
`codegen-units=1`, and symbol stripping. The resulting statically linked,
stripped ARM EABI5 executable has SHA-256
`c0136daca7ad6a316cc48aac682644ced7c65a8b052219224ae1343109116fc1`.

Worker-wide sampling during these checks showed additional swap usage of about
6.42 GiB, leaving at least 1.58 GiB free. No new kernel OOM kill occurred, no
resource guard stopped a phase, and all owned process groups were reaped.
The original incomplete runs remain preserved as separate evidence.

Physical batch `b02` qualifies the earlier bounded-profile candidate identified
above. The new standard ThinLTO binary has host build acceptance only.

## Four-preset and post-MDP follow-up, 2026-11-15

The records above retain their original 2026-11-13 scope. A later isolated
Scribe batch compared identical Gray8 input with Y4 off/on/off and read the
driver's actual post-MDP buffer. The two off results were identical; the on
result differed in 1294375 of 4612800 bytes. This confirms a digital processing
effect of Y4 on that device. It does not measure physical reflectance, ghosting
or full-page latency.

The application now implements Fast, Normal, High Quality and Kindle presets.
Native page requests matched DU followed by GC16, GLR16 plus Y4, GC16 plus Y4,
and opposite-direction twelve-step swipe plus Y4 respectively. Both batches
restored native state and released fresh exclusive leases. These runs used
native grayscale tag 1; they do not extend the earlier tag-2 observations into
a new inverted-waveform trial.

See [the implementation and qualification record](DISPLAY_MODES_20261115.md)
and [the exact-source API audit](SCRIBE_HARDWARE_API_AUDIT_20261114.md).
The separate standalone MDP full-size scaling test failed pixel qualification.
At the user's subsequent request, a new guarded codec probe qualified selected
JPEG/PNG paths and measured JPEG-linked IMGRZ scaling; the old common codec
probe remains disabled. See [the media retest](SCRIBE_MEDIA_BENCHMARK_20261115.md).
These media paths are not used to implement the reader presets.
