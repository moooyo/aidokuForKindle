# Reader display presets and Scribe qualification

Date: 2026-11-15, Asia/Shanghai.

The 2026-11-16 user-directed follow-up replaces Fast's timed preview with a
persistent monochrome frame and removes automatic periodic clearing in Fast.
The as-run preview timings and requests below describe the earlier candidate.
See the [current behavior and native verification](DISPLAY_AND_NAVIGATION_20261116.md).

Scope note: This report preserves the display-qualification candidate and its
measurements. Its software-only media recommendation and prototype codec
timings are superseded by the [final media acceleration policy](KINDLE_MEDIA_ACCELERATION.md)
and [completed media qualification](SCRIBE_MEDIA_QUALIFICATION_20261115.md).
The final application adds an optional, qualified JPEG decode/IMGRZ resize path;
the four display presets and the Y4 findings below remain applicable.

The candidate adds four persisted reader presets for Paperwhite 5, Paperwhite 6
and monochrome Scribe. Normal is the default. Qualified Scribes use hardware
Y4 for final grayscale frames; unqualified identities, including Paperwhite
5/6 under the present capability policy, retain software dithering. Image
decoding and scaling remain in software.

## Implemented behavior

| Preset | Reader behavior | Display request |
| --- | --- | --- |
| Fast | Publish a separate software-dithered black-and-white preview during page changes. Keep the final image and restore it after interaction settles. | DU preview, followed by a GC16 final frame; periodic clearing takes priority over preview. |
| Normal | Retain final grayscale detail and the saved clearing interval. | REAGL/GLR16 for ordinary pages, GC16 for clearing. |
| High Quality | Clear each page. The interval control shows Every page and is disabled; its saved value is retained for the other presets. | GC16 clearing. |
| Kindle | Animate eligible horizontal page changes using the native driver. Other updates retain normal grayscale behavior. | One-shot AUTO/PARTIAL swipe, twelve steps; clearing suppresses animation. |

The selector appears in screen-refresh settings and the reader image panel.
Chinese, Traditional Chinese, English and Japanese labels are provided.
High Quality does not increase physical resolution or panel bit depth.
The initial 220 ms settle delay is a policy value, not a measured optimum.
Fast is a feedback policy, not a promise of a particular frame rate or total
page-turn speed.

The temporary preview retains the final image, page target and lifecycle
generation. Another publication replaces it. Touch activity and outstanding
image work delay restoration; a stale page or lifecycle cannot repaint an old
final frame. Sleep and mode changes retire the preview before publication.
Fast uses software for both phases so each page does not repeatedly switch
between raw and quantized framebuffer ownership.

Waveform, rectangle and FULL/PARTIAL mode are independent. GLR16 uses the
driver's FULL mode even when the requested reading update is non-flashing.
GL16 is exposed in the backend for compatible requests but is not a separate
user preset or the default comic waveform.

## Calling-contract corrections

- Every actual framebuffer write now waits for the previous accepted update.
  Uncertain delivery prevents another write or retry.
- Logical swipe direction receives application render rotation once. The
  exact kernel handles native framebuffer/panel orientation.
- Native inverted Gray8 selects GCK16, GLKW16 or DUNM from the actual encoding,
  independently of UI night colors. The new device batches used tag 1.
- Definite advanced-mode rejection retries GC16 under a fresh marker and
  disables advanced refresh for the owner. A definite Y4 rejection also
  requires a canonical software rewrite. An unknown result is not retried.
- Older driver ABIs cannot interpret MTK GLR16 value 4 as their A2 waveform.
- Optional native request tracing is disabled by default. `--state-output`
  exports application state without forcing another screenshot refresh.

The exact Amazon source, installed proprietary-library evidence, and pinned
FBInk/KOReader clients were checked separately. Their detailed call order,
plane/pitch rules, completion limits and source links are in the
[hardware API audit](SCRIBE_HARDWARE_API_AUDIT_20261114.md).

## Completed Scribe evidence

The device reported Amazon build
`058-juno_1905_barolo_bellatrix3-479432`, Linux `4.9.77-lab126`, and armv7l.
The new display batches used 1860-by-2480 native Gray8, grayscale tag 1,
rotation 3, and a 1872-byte framebuffer scanline.

The controlled Y4 off/on/off trial used byte-identical 256-value framebuffer
input. Both off-trial post-MDP images were identical. Enabling Y4 changed
1294375 of 4612800 output bytes. This confirms a real digital Y4 effect on this
Scribe, rather than merely an accepted flag. All outputs had sixteen encoded
values; optical reflectance and ghosting were not measured.

The actual application then completed all four preset runs with next and
previous page actions. Retained native trace records showed:

| Preset | Accepted wire values on the two page changes |
| --- | --- |
| Fast | DU=1/PARTIAL/flags `0x2`, followed by GC16=2/FULL/flags zero, twice |
| Normal | GLR16=4/FULL/flags `0x4000`, twice |
| High Quality | GC16=2/FULL/flags `0x4000`, twice |
| Kindle | AUTO=257/PARTIAL/flags `0x14000`; direction 3 then 2; twelve steps each |

All final states had the intended preset, a ready current image, no temporary
preview, no pending dithering change and no outstanding image job. All four
processes exited normally. The launcher restored native window-manager and
statusbar operation; the batch restored original brightness and warmth,
removed its isolated staging and released its fresh owner/token lease before
analysis. The existing production installation was not replaced.

These observations establish accepted requests and final application state.
They do not establish physical Paperwhite 5/6 behavior, optical swipe direction,
temperature-dependent waveform quality or a measured speed ranking.

## Media acceleration decision

Small MDP Gray8 identity and 90/180/270-degree rotation tests matched the
reference pixels exactly. Small resizing completed with a different numerical
result from the CPU reference. Full-size scaling failed pixel qualification
despite successful dequeues and a complete saved buffer. RGB-to-gray also
differs from the application's conversion. None is promoted into the shipping
image pipeline by this candidate.

An earlier JPEG probe allocated one capture plane before the first header
changed the driver's capture layout to two-plane NV12. The resulting missing
plane access is strongly supported as the source of the observed kernel
NULL-pointer fault. The [incident record](SCRIBE_CODEC_INCIDENT_20261114.md)
documents the reboot, recovery and missing interrupted-run output. The old
common probe still refuses hardware codecs. After the user explicitly asked
for corrected physical trials, a separate guarded probe established working
JPEG-to-GREY and 8-bit PNG cases. It also exposed and corrected JPEG packet-tail
handling; default NV12 output remains unqualified.

The recommended project configuration is therefore Normal with device-qualified
Y4, the other three presets as user choices, and the existing software codecs,
scaler and preparation cache. Specialized media offload requires a separate
successful pixel and recovery qualification.

The [same-picture media benchmark](SCRIBE_MEDIA_BENCHMARK_20261115.md) provides
that follow-up's scoped results. For one grayscale JPEG, JPEG/IMGRZ decode plus
scale took 27.68 ms versus 277.13 ms on the production CPU path at 930 by 1240,
and 53.37 ms versus 443.64 ms at 1860 by 2480. Filters differ, and no full-page
latency claim is made. These results support a gated future integration; the
application decoder/scaler was not switched by the probes.

## Verification and artifacts

All builds, tests, captures and analysis ran through `ssh test-env`. Cargo
concurrency was six. The runtime source is based on
`a53b9716d64fe5521fac38431b24732c75cb8ff7` plus this uncommitted change set;
it is not presented as a new Git commit.

| Check | Result |
| --- | --- |
| Backend unit tests | 180 passed |
| Default application library | 760 passed, 5 ignored |
| Executable unit tests | 20 passed |
| Kindle-feature library | 773 passed, 5 ignored |
| Desktop and Kindle all-target checks | Passed |
| Offline hardware contract models | 6 passed; no device I/O |
| Final UI pointer/state matrix | 24 passed across Paperwhite 5 and Scribe layouts |
| Isolated native preset runs | 4 completed; accepted wire requests matched policy |
| Y4 post-MDP off/on/off comparison | Confirmed digital effect |
| Full-size standalone MDP pixels | Failed; retained as negative evidence |
| Corrected JPEG/PNG trials and JPEG/IMGRZ benchmark | Completed separately; see the linked media report for supported formats, failures and timing scope |

The native candidate is a static ARM executable built at optimization level 3,
with `lto=false` and sixteen codegen units. It is not a new standard ThinLTO
build. Its SHA-256 is
`a65d56511151af192563bd2d21e10dc85217ecdf11de56d2e821741a5f4f899f`.
The desktop UI candidate SHA-256 is
`030821e2c91c36f0f0b22befa62146a4907be16cc9b588fcd8e9efee9d1c8f3f`.

- [Sanitized native requests and pixel results](review/display-modes-20261115/native-display-mode-results.json).
- [Controlled Y4 comparison](review/display-modes-20261115/y4-output-comparison.json).
- [Supplemental UI captures](uiux-baselines/2026-11-15-a53b9716-display-modes/README.md).

The source manifest, development ZIP, package verification and full worker
reports accompany the local candidate artifact. No lease tokens, transport
credentials or complete private device identifiers are part of the package or
committed evidence.

The final development ZIP contains 29605938 bytes with SHA-256
`07643fc5fe9c741d8df5a1ba6176cc93c4327eaa68bb143690b4a696201acea5`.
Its 325-file source manifest, including the later benchmark example, has fingerprint
`c01d73f6c58c1ec28f130cd692bed2934067f6e0caf27d7739600d8da495a655`.
Package verification checked the exact member allowlist, embedded assets,
licenses, static ARM identity, application digest and source binding.
