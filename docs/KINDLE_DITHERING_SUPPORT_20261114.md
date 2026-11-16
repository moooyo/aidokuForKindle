# Kindle dithering support and recommendations

Initial source review: 2026-11-14. Implementation update: 2026-11-15,
Asia/Shanghai.

Scope: grayscale comic rendering, current upstream device policy, and the
Aidoku implementation at `a53b9716d64fe5521fac38431b24732c75cb8ff7`.
The sections below retain the initial source assessment at that base. The
subsequent implementation and physical evidence are explicitly recorded next;
neither constitutes an optical image-quality benchmark.

## Implementation and evidence update

Fast, Normal, High Quality and Kindle are now selectable, persisted reader
presets. The actual application was exercised on the user's monochrome Scribe:
DU followed by GC16 for Fast, GLR16/Y4 for Normal, GC16/Y4 for High Quality,
and two opposite twelve-step native swipes with Y4 for Kindle. The new native
trial used grayscale tag 1. A separate identical-input off/on/off experiment
confirmed a real change in the driver's post-MDP image when Y4 was enabled.

The app continues to decode and scale images in software. Hardware JPEG/PNG
execution was disabled after the documented JPEG probe incident. Small MDP
copy/rotation results were exact, but a complete full-size scaling output
failed pixel qualification. A successful ioctl is insufficient to promote it.
The user's later request led to a separate corrected codec retest and a
[same-picture JPEG/IMGRZ benchmark](SCRIBE_MEDIA_BENCHMARK_20261115.md), which
qualified selected JPEG/PNG cases and a useful JPEG scaling path. Application
integration remains separate from those probe results.

See [the four-preset implementation record](DISPLAY_MODES_20261115.md),
[the path-by-path exact-source audit](SCRIBE_HARDWARE_API_AUDIT_20261114.md),
and [the decoder incident and recovery](SCRIBE_CODEC_INCIDENT_20261114.md).
The following proposals and descriptions of the old code are historical;
the linked implementation record defines the current behavior.

## Finding

Use a device-aware policy: preserve Gray8 through image preparation, prefer
hardware Y4 on qualified monochrome Scribes, and use software dithering on
other unqualified grayscale devices. Color rendering needs its own CFA path.
This is a practical default, not evidence that hardware dithering always wins
on quality, page-turn latency, or energy use.

Y4 describes four-bit luminance precision: sixteen output codes. It does not
name one universal dithering algorithm. Y8 supplies 256 possible input codes;
spatial dithering distributes quantization error among neighboring pixels so
that intermediate tones have smoother apparent averages. This does not create
256 independently addressable, stable optical levels at one panel pixel.

## Recommendation for the project's target devices

The user narrowed the development scope to Paperwhite 5, Paperwhite 6, and
monochrome Scribe. Older Kindle families and Colorsoft are outside this
recommendation; their entries below remain research context only.

The recommended production architecture is one Gray8 preparation pipeline,
one effective final dithering stage, and refresh selection appropriate to the
actual content being updated.

| Target | Current recommended default | Next qualification step |
| --- | --- | --- |
| Paperwhite 5, including Signature Edition | Software ordered dithering to sixteen grayscale values. | Retain it as the portable reference. No reliable hardware-dither gain was established here. |
| Paperwhite 6, including Signature Edition | Software ordered dithering until native qualification. | Prioritize an exact-device comparison of MDP Y4 against software. Enable hardware by qualified device/firmware if it works correctly and meets quality and latency requirements. |
| Scribe 2022 / 2024 | Existing hardware Y4 path with the established software rejection fallback. | Preserve the current interface evidence; compare optical quality and complete page-turn behavior separately. |
| Scribe 3 | Software until its distinct identity and driver path are qualified for Aidoku. | Extend the same MDP policy after qualification; do not inherit the earlier generations' acceptance automatically. |

Keep crop, resampling, and tone adjustments before final quantization. Reuse
the existing preparation cache and prefetch paths, with effective dithering
mode represented in their identities. Hardware availability alone must not
change how an already-prepared software frame is submitted.

Retain the current Reader 4-by-4 ordered implementation as the shipping
software baseline. The first software-quality experiment should compare it
with the mature 8-by-8 ordered implementation used upstream, holding all
other processing constant. This review supplies no optical result that would
justify declaring either matrix universally superior or replacing every
software path with error diffusion immediately.

Use GC16 as the conservative comic-image reference. Suitable small, purely
black-and-white interaction updates may use DU; updates containing retained
Gray8 images or antialiased gray content still need a compatible waveform.
Tune full refreshes and any REAGL/fast-mode policy through controlled ghosting
and latency comparisons rather than fixing an unmeasured refresh interval.

The implementation priority is Paperwhite 6 qualification and controlled
waveform comparisons, followed by software-dither comparisons. Preserve
current caches and prefetching. MDP resizing/rotation or decoder offload is a later, separate
optimization if profiling shows image preparation remains a material cost.
No production rendering code was changed during the initial source-only review.
The subsequent preset implementation is described in the update above.

## Waveform policy for Paperwhite 5/6 and monochrome Scribe

Dithering controls quantized image values. A waveform controls the transition
from previous panel states to new states, affecting ghosting, visible flashing,
and update latency. The MTK definitions map GC16 to 2, GL16 to 3, and
GLR16 / REAGL to 4. These values do not establish identical optical behavior
across panel, temperature, and firmware combinations.
[MTK waveform definitions](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/eink/mtk-kindle.h#L102-L125).

| Waveform | Role | Proposed application policy |
| --- | --- | --- |
| GC16 | High-fidelity arbitrary grayscale transitions; the image-quality and clearing reference. | Retain the current comic baseline. Use a clearing update for initial presentation, rotation, periodic cleanup, or other transitions needing a clean reference. Compare it against REAGL on ordinary page turns. |
| GL16 | Optimized for white-background text and light interface content. | A candidate for suitable small white-background UI updates. Avoid treating it as a universal faster replacement for image refreshes. |
| GLR16 / REAGL | Reduce ghosting and visible flashing, especially for text and lightly mixed content. | The first alternative to qualify for routine comic page turns. Promote it to the balanced default only if screentones, dark fills, thin lines, and consecutive pages remain acceptable compared with GC16. Keep GC16 cleanup. |

FBInk describes the waveform roles above. Its approximate generic timings
are not device measurements and do not prove that GL16 or REAGL is faster
than GC16 on these targets.
[Waveform roles](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.h#L387-L416).

KOReader maps ordinary MTK reading refreshes to GLR16 and clearing refreshes
to GC16. It promotes REAGL requests to the driver's FULL update mode even
when the application requested a non-flashing reading update.
[Reading waveform selection](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer_mxcfb.lua#L1071-L1090),
[GC16 clearing selection](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer_mxcfb.lua#L1020-L1026),
[REAGL mode promotion](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer_mxcfb.lua#L460-L465).

Waveform, driver update mode, and update rectangle are separate axes. FULL
does not by itself mean the rectangle covers the entire panel, or that every
waveform produces the same black flash. A GC16 region update and a REAGL
FULL-mode region update are different operations. A new policy must preserve
this distinction instead of converting every FULL-mode requirement into a
request for visible clearing.

The reviewed base represented only AUTO, GC16, DU, and A2 in
`vendor/slint-backend-kindle/src/refresh.rs`. The backend maps its `flashing`
boolean directly to FULL/PARTIAL. It forces retained raw scenes to GC16 at
`framebuffer/mod.rs:540-542`, and its `mtk_update` helper enables Y4 only for
GC16 at line 1282. Adding an enum value alone would therefore be insufficient:
REAGL needs its mode semantics, Y4 compatibility, merge policy, synchronization,
and rejection behavior integrated together.

Ordinary software-prepared updates currently request AUTO. The previously
reviewed Amazon 4.9 candidate selects a waveform from old/new image histograms,
then can promote sufficiently large daytime updates to GLR16/FULL when fast
mode is clear. Thus the project may already encounter GL16 or REAGL indirectly;
it does not yet choose them explicitly. That kernel's promotion path returns
early for explicit non-AUTO waveforms, so global fast mode is not a prerequisite
for investigating explicit waveform selection.
[Existing kernel review](MTK_DISPLAY_EVALUATION.md).

The target policy is qualified REAGL for suitable routine reading updates,
GC16 for image-fidelity fallback and clearing, and GL16 or DU for suitable UI
regions. Native night-mode configurations require their compatible waveform
mapping and separate qualification. The existing six-page refresh preference
is an implementation default, not a measured optimal cleanup interval. No
waveform or refresh setting was changed during this source review.

## Feasibility of Fast, Normal, and High Quality presets

These application-level presets are feasible within the current target scope.
Each preset should select a coordinated policy for image precision, dithering,
waveforms, update scheduling, and cleanup. A preset is not a one-to-one alias
for a waveform constant or for the MTK fast-mode driver flag.

BOOX provides a concrete public reference. Its SDK documents NORMAL,
FAST_QUALITY, REGAL, FAST, and FAST_X options. Its lower-level update modes
include sixteen-level partial/full updates, black-and-white updates, and
dithered quality variants of fast updates. A separate manager controls the
interval between clearing updates and entry/exit from animation mode.
This establishes the general policy approach without disclosing every
vendor's implementation or proving an exact mapping to Kindle waveforms.
[BOOX mode definitions](https://github.com/onyx-intl/OnyxAndroidDemo/blob/ec512c79c0d39c1e294214d36580ce4c914bbea6/doc/EPD-Update-Mode.md),
[BOOX refresh scheduling](https://github.com/onyx-intl/OnyxAndroidDemo/blob/ec512c79c0d39c1e294214d36580ce4c914bbea6/doc/EpdDeviceManager.md).

| Proposed preset | Reading behavior | Display policy |
| --- | --- | --- |
| Fast | Prioritize feedback during scrolling, dragging, zooming, or repeated navigation. Restore full grayscale after interaction settles. | Produce a separate black-and-white preview; use qualified DU/A2 behavior with correct entry/exit transitions. Coalesce pending intermediate frames while respecting in-flight updates. Finish with one current-generation sixteen-level frame and appropriate cleanup. |
| Normal, recommended default | Preserve manga detail while reducing disruptive flashing. | Use sixteen-level output with device-selected Y4 or software dithering. Use qualified REAGL for suitable page turns and GC16 for periodic or required clearing. Keep GC16 fallback where REAGL is unqualified or visually unsuitable. |
| High Quality | Prioritize final image fidelity and clean dark/gray regions over minimal flashing. | Retain full input precision through preparation and complete final reading images with GC16 and a more aggressive clearing policy. Keep the same panel resolution and sixteen-level output capability. |

Fast mode must retain the canonical image and its final-render settings.
The preview cannot replace source images or contaminate final-frame cache
entries. On interaction completion, resolve only the newest valid page and
geometry; do not repaint a stale delayed cleanup frame after another gesture,
page change, sleep transition, or mode switch. Do not assume unqualified
hardware Y1 support: software preview preparation provides the portable path.

The existing `GrayMode::black_and_white` transform is a threshold operation,
not a complete dithered fast-preview implementation. Existing GC16/Y4 guards
also prevent merely sending an A2 request over retained raw grayscale content.
Implementation requires explicit preview/final state, compatible submission
and completion handling, and device-qualified transition behavior. No fixed
frame rate or settle delay has been measured in this research.

The presets can reproduce the product concept of quality/responsiveness
choices. They do not establish performance parity with BOOX Super Refresh
devices. BOOX describes BSR as a hardware/software system, and its Tab Ultra C
specifically includes a separate graphics processor. That machinery is not
created by changing a Kindle update flag.
[BOOX BSR description](https://shop.boox.com/blogs/news/boox-super-refresh-bsr-technology),
[BOOX hardware and mode description](https://shop.boox.com/products/tabultrac).

This section records feasibility and proposed behavior. It does not add a
mode selector, modify production code, or claim a completed native trial.

## Native Kindle page-turn animation

The reviewed MediaTek display interface provides a dedicated swipe-animation
path. An application can prepare the next page and submit a display update
with `MTK_EPDC_FLAG_ENABLE_SWIPE` (`0x10000`) plus `swipe_data.direction`
and `swipe_data.steps`. The intermediate transition is scheduled by the
display driver; this interface does not require application-generated frames
that progressively translate the complete page image.
[Swipe request definition](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/eink/mtk-kindle.h#L188-L208),
[FBInk submission](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L2607-L2635).

The Amazon 5.19.5 build 4794290058 archive contains
`drivers/misc/mediatek/hwtcon_v2/hal/hwtcon_pipeline_config.c`.
Its `pipeline_handle_swipe_update` implementation at lines 1034-1188:

1. Validates direction and step count and divides the update into regions.
2. Runs REAGL processing into a temporary image buffer.
3. Waits for active waveform resources and initializes the working buffer.
4. Configures a waveform-LUT slot for each divided region.
5. Calls `wf_lut_config_show_picture_by_frame` to stage the regions over time.
6. Waits for completion, performs a LUT-override working-buffer operation,
   and assigns the P2SW waveform for the concluding stage.

This is a region-and-timing transition driven by the display pipeline. It does
not establish a general-purpose high-frame-rate renderer or a faster total
page-turn time. The extracted C file has SHA-256
`a9a2d9d885d9e06b5cb87b983fc28b938772d5c93cac0a881a44bd3bd95d67a1`.
It was read statically on `ssh test-env` from the
[Amazon source archive](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794290058.tar.gz);
this candidate is not an exact firmware qualification for every target.

FBInk records twelve steps for the stock Paperwhite 5 reader and a maximum
of sixty for Bellatrix. A step count is not an application FPS setting.
It also checks that the animation dimension can accommodate the step count.
[Parameter notes](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.h#L1645-L1655).

KOReader enables this capability for Paperwhite 5/6 and the reviewed Scribe
classes. Its page-change handler arms a one-shot animation with a direction;
the MTK backend adds the swipe flag to the next eligible update and then
clears the request. Flashing cleanup updates are excluded. Rotation affects
the mapping between reading direction and native swipe direction.
[Page-change handler](https://github.com/koreader/koreader/blob/4184b73a737b5dcc77b29e2eaf69c31a06501ab9/frontend/apps/reader/modules/readerview.lua#L1051-L1060),
[MTK animation submission](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer_mxcfb.lua#L686-L740).

Animation is a separate policy dimension from Fast/Normal/High Quality.
For Aidoku, the native path is a concrete implementation candidate for ordinary
page transitions, while rapid repeated navigation and clearing updates should
be able to bypass it. Y4 and animation are distinct request flags; their
combination, buffer lifetime, completion accounting, and image quality still
need qualification on the selected firmware.

The reviewed base declared the ABI's `SwipeData` structure, but left its fields
at their defaults without the swipe flag. The initial research did not enable
animation; the subsequent implementation and native trial are recorded above.

## Source snapshots

The current upstream revisions were retrieved directly through `ssh test-env`.
The KOReader base revision is its frontend's actual gitlink.

| Source | Revision |
| --- | --- |
| KOReader master | `4184b73a737b5dcc77b29e2eaf69c31a06501ab9` |
| KOReader base gitlink | `f64c3eb11049e9a75189f186d7345d97c07d87b9` |
| FBInk master | `ded92fa510c957e0157c92ae83bd5c51af710c05` |
| Kindle Comic Converter master | `8e3d587bd8c201df5ce2e7cb889e0a5819fbdff9` |

The previously extracted Amazon 5.19.5 build 4794320058 kernel sources were
also re-read on the worker. Their relevant file hashes match the recorded
[grayscale research](KINDLE_GRAYSCALE_RESEARCH_20261113.md).

## Device support

The following table describes usable upstream application policy, not a
silicon feature guarantee. An exposed ioctl, a successful submission, an
enabled application capability, and optically verified output are different
levels of evidence.

| Device family | Current KOReader policy | Interpretation for Aidoku |
| --- | --- | --- |
| Scribe 2022 and Scribe 2024 | Hardware dithering enabled; both generations use the same device class. | The existing whitelist admits these generations with an actual Gray8 framebuffer. Recorded native acceptance is narrower than the whitelist. |
| Scribe 3 | Hardware dithering enabled in its separate device class. | Upstream support is a qualification candidate; the current Aidoku whitelist does not include it. |
| Colorsoft, including variants mapped to ColorSoft 2, and Scribe Colorsoft | Capability enabled, but the Y4 flag is suppressed while color rendering is enabled. Partial refresh conditions can suppress dithering too. | Do not treat these as ordinary monochrome Scribes or enable Y4 for RGB color content. |
| Paperwhite 5, including Signature Edition | Hardware dithering disabled; the source records that the MDP option appears ineffective. | Keep software dithering. A shared MediaTek platform is insufficient evidence. |
| Paperwhite 6 | Hardware dithering disabled; its author explicitly lacked a device to verify the assumption. FBInk separately implements Bellatrix4 Y1/Y4 submission for this model. | A strong qualification candidate with differing upstream policies, not proven unsupported. Keep the current software default until qualified. |
| Basic 2022 / Basic 2024, represented by Basic4 / Basic5 | Hardware dithering disabled; Basic5 has the same unverified-assumption qualification. | Keep software until qualified. |
| Earlier Paperwhite, Voyage, Oasis, and Basic families | Inherit disabled hardware dithering. Zelda/Rex paths have historical display defects when it is enabled. | Use software; legacy driver flags are a separate mechanism. |

Sources: [device capability definitions](https://github.com/koreader/koreader/blob/4184b73a737b5dcc77b29e2eaf69c31a06501ab9/frontend/device/kindle/device.lua#L1105-L1243),
[Scribe generation mapping](https://github.com/koreader/koreader/blob/4184b73a737b5dcc77b29e2eaf69c31a06501ab9/frontend/device/kindle/device.lua#L2190-L2225),
[default hardware policy](https://github.com/koreader/koreader/blob/4184b73a737b5dcc77b29e2eaf69c31a06501ab9/frontend/device/kindle/device.lua#L408-L416),
and [Paperwhite 4 defect report](https://github.com/koreader/koreader/issues/5884).

Some older Kindle interfaces also contain a Y4 flag. FBInk distinguishes their
legacy in-kernel CPU dithering from accelerator offload, and describes the
legacy Y4 effect on its Paperwhite 2 as weak. The shared spelling does not make
it the same implementation as MediaTek MDP Y4.
[Legacy mode definition](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.h#L462-L474),
[legacy Kindle submission](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L2288-L2303).

Paperwhite 6 deserves explicit follow-up. FBInk identifies it as Bellatrix4
and its refresh implementation selects hardware Y1 for A2/DU or Y4 for other
waveforms. The maintainer expects that platform to support these operations.
This is more than an enum declaration, but it is not a new physical result
from this research and does not settle the difference from KOReader's policy.
[Paperwhite 6 platform mapping](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink_device_id.c#L334-L351),
[Bellatrix4 submission](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L2639-L2658).

## What the Scribe path actually does

In the reviewed Amazon archive,
`drivers/misc/mediatek/hwtcon_v2/hwtcon_mdp.c:140-141` selects Y8 input and
Y4 output. Lines 162-163 enable dithering from the Y4 update flag, and lines
206-217 submit `MDP_DITHER_ALGO_Y8_Y4_S`. The other included HWTCON
implementation selects the same algorithm. FBInk describes the `_S` variants
as Floyd-Steinberg. That is not a claim of bit-for-bit equivalence with a
particular CPU implementation.
[Amazon source archive](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794320058.tar.gz),
[algorithm declarations](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/eink/mtk-kindle.h#L162-L185).

The MediaTek Y4 flag is `0x4000`. FBInk's ordinary Y4 request leaves
`dither_mode` / `quant_bit` at zero. The reviewed Scribe 4.9 path selects its
Y4 algorithm internally. Newer Bellatrix4 kernels have additional Y1
selection semantics, which are distinct from the old EPDC-v2 convention.
Do not transplant i.MX settings into this ABI.
[MediaTek submission](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L2575-L2587).

Current KOReader explains why it excludes Y4 during color rendering. On
Scribe Colorsoft, that request can bypass the hardware color engine and yield
grayscale output. On Colorsoft, it can reprocess already-dithered CFA output
or produce output that a later pass ignores. Color image refreshes instead
use the appropriate GCC16 / GLRC16 waveform path.
[Color exclusions](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer_mxcfb.lua#L656-L684),
[color waveform promotion](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer_mxcfb.lua#L407-L430).

## Alternatives to Y4

Hardware acceleration, dithering precision, waveform control, and color
processing should be evaluated separately.

| Mechanism | Purpose | Evidence and limits on Kindle |
| --- | --- | --- |
| MediaTek Y1 dithering | Convert to two output levels for suitable DU/A2 black-and-white updates. | FBInk has a real Bellatrix4 submission branch, including Paperwhite 6 and Colorsoft platform mappings. This is a lower-precision fast-interaction candidate, not an image-quality upgrade over Y4. The inspected Scribe 2022/2024 kernel path does not establish equivalent Y1 support. |
| Y2 dithering | Convert to four output levels. | MediaTek exposes Y2 algorithm identifiers, but the inspected public Kindle refresh implementations do not establish a qualified modern Y2 hardware path. Older i.MX Y2 flags can request in-kernel CPU processing. |
| i.MX PxP / EPDC-v2 ordered dithering | Offload quantization and ordered dithering through a different accelerator. | Actual Kindle Zelda/Rex submission code exists, but KOReader disables hardware dithering across these families following display defects. Do not import positive Kobo experience as Kindle qualification. |
| Different MDP algorithms | Quantization-only, Bayer-style ordered processing, or error diffusion at a chosen output precision. | The header lists Q/B/S identifiers. FBInk specifically documents newer Y1 selection through `dither_mode`; that does not prove arbitrary Y2/Y4 algorithm switching works on an older Scribe. The inspected 4.9 Y4 call selects `_S` internally. |
| GC16, GL16, REAGL and fast mode | Choose refresh quality, ghosting behavior, and waveform promotion policy. | These are complementary display controls, not additional grayscale precision or alternative image dither algorithms. The project's prior review already records actual KOReader fast-mode calls on Scribe. |
| CFA color processing | Map RGB content to a color-filter-array panel and prepare compatible output. | Colorsoft and Scribe Colorsoft use different mixtures of hardware and driver software. Current KOReader avoids stacking ordinary Y4 onto their color pipeline. |
| MTK halftone / lightbox | Gray out regions with a checker pattern for overlays. | FBInk exposes `fbink_mtk_set_halftone`; it does not perform general tone-preserving comic dithering. A driver API alone does not identify which stages are hardware on every model. |
| MDP resizing/rotation and image decode engines | Reduce source-image preparation CPU work. | Prior supplied Scribe observations found relevant device nodes and libraries, but do not qualify formats, output quality, concurrency, or speedup. This is a separate acceleration investigation. |

Primary implementation references:

- [Bellatrix4 Y1 and Y4 selection](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L2639-L2658)
  and [MDP algorithm declarations](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/eink/mtk-kindle.h#L162-L185).
- [PxP request construction](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L2399-L2431)
  and [Basic 2019 hardware-dither defect](https://github.com/koreader/koreader-base/pull/1039).
- [Halftone and refresh controls](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.h#L1664-L1691)
  and [fast-mode semantics](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/eink/mtk-kindle.h#L502-L518).
- [Existing MDP and decoder investigation](KOREADER_HARDWARE_RESEARCH.md),
  whose supplied physical observations are historical evidence and were not
  repeated here.

No usable public native 256-level Kindle waveform was established by this
review. An eight-bit framebuffer, internal five-bit waveform terminology, or
the PocketBook-specific `GC16HQ` name does not establish such a mode.
For the current monochrome Scribe reader, Y4 remains the qualified hardware
candidate; waveform tuning and image-preparation offload address different
potential improvements.

## Recommended choices

1. **Interactive grayscale comics:** crop, resize, and adjust tones while
   retaining Gray8; perform one final dithering stage. On qualified Scribes,
   let MDP own that stage. Otherwise, quantize in software. KOReader also
   prefers hardware when its device capability is enabled, with software as
   its eight-bit fallback.
   [Upstream selection](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer.lua#L512-L529).
2. **Portable software implementation:** ordered dithering is a mature
   baseline. KOReader and FBInk both use an 8-by-8 ordered pattern with sixteen
   grayscale values. Compare it against the project's existing 4-by-4 Reader
   path and serpentine Floyd-Steinberg path before choosing a new quality
   default. Larger ordered matrices or error diffusion do not establish a
   universal visual ranking without the actual pages and panel.
   [KOReader software conversion](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/blitbuffer.lua#L230-L260),
   [FBInk software conversion](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L10880-L10919).
3. **Refresh quality:** use a suitable sixteen-level image waveform, with
   GC16 as the conservative image-quality reference. DU/A2 are useful for
   suitable fast black-and-white interactions. Full-refresh frequency is a
   separate ghosting/flash tradeoff and must be evaluated independently from
   the dither algorithm. Do not transfer the generic timing estimates in
   FBInk's enum comments into a claim about a specific Kindle.
   [Waveform roles](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.h#L381-L409).
4. **Offline, fixed-size comics:** preprocessing for the final viewport can
   move resizing and dithering off the device. KCC supports device profiles
   and sixteen-color images, including a PNG/CBZ workflow for compatible
   readers. Preserve source images when later zooming or viewport changes are
   needed, and avoid a second resampling/dithering pass over a preprocessed
   page. No fixed speed, energy, or compression improvement is asserted here.
   [KCC workflow](https://github.com/ciromattia/kcc/blob/8e3d587bd8c201df5ce2e7cb889e0a5819fbdff9/README.md#L89-L100),
   [palette quantization](https://github.com/ciromattia/kcc/blob/8e3d587bd8c201df5ce2e7cb889e0a5819fbdff9/kindlecomicconverter/image.py#L577-L584).

## Current project status and remaining evidence

At the inspected application revision, `src/app.rs:377-383` chooses hardware
or software from runtime capability during attachment. The older opening
description in `HARDWARE_Y4_DITHERING.md` belongs to its earlier implementation
snapshot; the current attachment behavior is automatic.

`vendor/slint-backend-kindle/src/dithering.rs:122-155` admits only the reviewed
Scribe 2022/2024 identities. The framebuffer must actually expose eight-bit
native grayscale. `src/imaging.rs:149-164` skips application quantization for
hardware frames. The backend uses GC16, aligned update regions, and an actual
software rewrite after definite hardware rejection.

The software paths should not be conflated: Reader's tone tables use 4-by-4
Bayer at `src/imaging/reader_raster.rs:337-364`, while the general image
quantizer uses serpentine Floyd-Steinberg at `src/imaging.rs:1040-1090`.

The recorded native batch in [hardware Y4 evidence](HARDWARE_Y4_DITHERING.md)
establishes retained Gray8 input and accepted Y4 submissions for its specific
candidate and device. It does not establish optical superiority, 256 native
panel levels, page-turn speedup, or lower energy use. Keeping Gray8 does not
by itself increase current one-byte-per-pixel buffer allocations, although
encoded cache sizes can change.

A future quality comparison should hold source page, final geometry, tone
adjustments, inversion, waveform, and refresh history constant. Compare
gradients, dark detail, thin lines, and manga screentones across existing Bayer,
ordered 8-by-8, software error diffusion, and hardware Y4. Observe the physical
panel under controlled lighting; a framebuffer capture only measures submitted
pixels. Measure rendering CPU cost and visible update latency separately.

No tests, builds, runtime probes, device commands, or display-setting changes
were performed for this research. The web gateway was unavailable; direct
primary-source retrieval and static inspection ran on `ssh test-env`. Local
work was limited to reading the repository and adding this report.
