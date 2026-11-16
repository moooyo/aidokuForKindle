# Scribe hardware API and probe audit

Initial audit: 2026-11-14. Updated: 2026-11-15, Asia/Shanghai.

This audit covers every hardware-facing path in the display-mode work. Source
declarations, installed interfaces, completed transformations, optical effects,
and production integration are separate evidence categories.

The production decision is to use the qualified display interface for the
four reader presets while retaining software image decoding and scaling.
Y4 now has a controlled post-MDP output comparison. The four presets have
native request acceptance. At the user's subsequent request, a separate
corrected probe qualified JPEG-to-GREY and tested 8-bit PNG paths, including
full-size output. JPEG/IMGRZ scaling also passed a same-picture benchmark.
Full-size standalone MDP scaling and default JPEG NV12 output remain
unqualified. See [the implementation record](DISPLAY_MODES_20261115.md) and
[the corrected media tests](SCRIBE_MEDIA_BENCHMARK_20261115.md).
The [extended qualification record](SCRIBE_MEDIA_QUALIFICATION_20261115.md)
tracks later explicit NV12/NV16 successes, expanded PNG inputs, PNG performance,
and the separate non-palette PNG transparency incident and admission guard.

## Device and source identity

Read-only device metadata identifies:

- Software build: `058-juno_1905_barolo_bellatrix3-479432`.
- Kernel/userspace architecture: Linux `4.9.77-lab126`, `armv7l`.
- Observed framebuffer: 1860 by 2480, 8 bpp, rotation 3, and a 1872-byte
  scanline stride. The initial discovery reported grayscale tag 2; after the
  restart, the controlled Y4 and preset batches used tag 1. Native polarity
  must be read from the current framebuffer, not inferred from an earlier run.
- Installed components include `libmdp.mt8512.so`, `libmdpd.so`, `libmdppq.so`,
  `libmdp_prot.mt8512.so`, `libion.so`, and the `mdpd` service.

The exact [Amazon source archive](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794320058.tar.gz)
contains `gplrelease/linux-4.9.tar.gz`. Selected media, videobuf2, and HWTCON
sources were extracted on `ssh test-env`; the source inventory records their
SHA-256 values. The archive is 389884927 bytes. The exact-build JPEG core is
byte-identical to the earlier reviewed 4794290058 copy.

The installed MDP library was copied for static ELF inspection only. Its exports
include `DpBlitStream::queryHWSupport`, source/destination buffer and format
overloads, and `invalidate`. Overloads accept pointer arrays, sizes and plane
counts; detailed format overloads also take a color profile and strides.
Symbol availability does not establish C++ object layout, enum values, or
binary compatibility with headers from another MediaTek product. No vendor
library method was invoked using guessed declarations.

`cjpeg`, `djpeg`, and `jpegtran` are present alongside libjpeg-turbo; their
presence is not a hardware-decoder example. No matching vendor hardware JPEG
or PNG application example has yet been established from the installed file
inventory.

## Path-by-path findings

| Path | Contract and evidence | Status |
| --- | --- | --- |
| Capability discovery | QUERYCAP, ENUM_FMT and QUERYCTRL only; no formats, queues, or streams changed. Some drivers omit optional enumeration/query operations. | Completed on the device. Missing enumeration is not interpreted as missing hardware support. |
| MDP Gray8 copy | The M2M driver accepts `Y8  ` on both queues, MMAP buffers, and standard queue/stream operations. | Actual output retained all 256 values and matched the input exactly. |
| MDP rotation | The driver implements S_CTRL ROTATE despite omitting QUERYCTRL. The tested angles were 90, 180 and 270 degrees. | All three actual outputs matched the corresponding reference rotations exactly. |
| MDP resizing | Negotiated geometry, crop/compose rectangles, memory stride, plane capacity and returned payload must agree; pixel output needs a separate oracle. | Small resizing completed. A fresh full-size case returned the complete 4642560-byte payload, but its pixels were incorrect. Full-size scaling is not qualified. |
| MDP RGB to gray | The public path performs conversion, but color range/profile handling matters. | Output differs from the application's CPU reference. It is not a drop-in replacement for current grayscale conversion. |
| JPEG decoder | USERPTR, header-driven capture renegotiation, actual plane count, aligned plane sizes, an advertised zero packet tail and completion ownership are required. | Corrected GREY output passed small and full-size trials. The default NV12 result did not pass. The retired common probe remains disabled. |
| JPEG-linked IMGRZ scaling | Select the desired GREY dimensions after the source header, before capture allocation. | 1920-by-2560 input to 930-by-1240 and 1860-by-2480 output passed the declared benchmark quality gate, with filter differences recorded. |
| PNG decoder | USERPTR only. Starting queued OUTPUT parses IHDR and rewrites capture geometry and ARGB32 layout. There is no corresponding source-change subscription in its ioctl table. | The corrected RGB8, RGBA8 and grayscale8 cases matched software pixels. Full-size RGB output also matched; other encodings remain unqualified. |
| HWTCON Y4 | Actual Gray8 samples, native polarity, the MTK flag, compatible grayscale waveform, region alignment and matching submission lifetime must agree. | Confirmed by identical input in off/on/off trials and a changed post-MDP image only for the on trial. This is digital output evidence, not optical measurement. |
| GC16/GL16/REAGL | Update mode and rectangle are separate from waveform selection. Inverted native Gray8 selects the kernel's loaded night waveform family. | Native GC16, DU and REAGL requests matched the preset policy. GL16 and inverted wire mappings have source/model coverage, not a new optical or tag-2 device trial. |
| Native swipe animation | Uses direction, bounded step count and a one-shot flag. The driver stages divided regions and REAGL/P2SW processing. Clearing requests suppress animation. | Two opposite 12-step requests with AUTO/PARTIAL and Y4 were accepted. Driver-side timing and visible direction were not measured optically. |
| Release and failure handling | Own each device session with a fresh host lease; do all preparation first and all analysis after release. Never reuse a released token or take another owner's lease. | Discovery and recovery evidence is retained. The decoder incident's initially unverified recovery is explicitly distinguished from the later verified cleanup. |

## JPEG: the initialization error and corrected sequence

The exact source provides the following chain:

1. `mtk_jdec_buf_queue`, lines 639-646, recognizes the first parsed header,
   emits a source-change event, and calls `mtk_jdec_set_queue_data`.
2. `mtk_jdec_set_queue_data`, lines 509-524, rewrites capture dimensions and
   selects NV12 with two planes and independently aligned plane sizes.
3. The old probe had already selected GREY and allocated one capture plane.
4. `mtk_jdec_set_disp_param`, lines 447-449, follows the format's current plane
   count, not the originally allocated buffer's plane count.
5. `videobuf2-core.c:879-884` returns NULL for a nonexistent plane cookie.
   `include/media/videobuf2-dma-contig.h:19-24` dereferences the cookie.

The old ordering was incorrect. The kernel also lacked a defensive check at
the failing dereference. The [incident record](SCRIBE_CODEC_INCIDENT_20261114.md)
keeps the observed fault separate from unproven allocator assumptions.

The source-backed initialization order is:

1. Open one decoder context and subscribe to `V4L2_EVENT_SOURCE_CHANGE`.
2. Set the coded OUTPUT format and allocate only its correctly backed USERPTR
   buffer, retaining that memory for the entire queued lifetime. The tested
   JPEG policy submits `round_up(file_bytes + 64, 64)` bytes with an initialized
   zero tail; unadvertised spare allocation alone did not prevent a timeout.
3. Queue the first coded image and start OUTPUT, allowing header parsing while
   CAPTURE is not streaming.
4. Consume the source-change notification and read CAPTURE with G_FMT.
5. Select the qualified GREY format and required output dimensions while
   CAPTURE is still unallocated, then read the negotiated result again.
   Merely accepting the initial NV12 layout did not qualify its output pixels.
6. Allocate exactly the reported number of capture planes, each with its own
   required alignment and capacity. Recheck the layout before queueing.
7. Queue CAPTURE, start CAPTURE, and wait for both buffer completions.
8. Handle later source changes by stopping and renegotiating CAPTURE before
   recycling its buffers. Never assume the initial format persists.

This agrees with the Linux [stateful decoder initialization protocol](https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/dev-decoder.html)
and [USERPTR ownership model](https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/userp.html).
Those documents supplement the exact Kindle source; they do not guarantee that
this driver implements every standard operation correctly.

The source's FIFO-end and write-pointer setup uses different address units
(`mtk_jdec_hw.c:303-350`). Same-image packet-tail trials removed the JDEC and
IMGRZ timeouts, and the GREY path then produced correct pixels. Page-aligned
ordinary USERPTR memory is mapped and checked by `vb2_dc_get_userptr`; the new
trials do not borrow MDP allocations. The complete tested recipe and its limits
are in the [media retest report](SCRIBE_MEDIA_BENCHMARK_20261115.md).

Progressive JPEG is explicitly rejected in `mtk_jdec_parse.c:109-113`.
The decoder's `job_abort` callback is empty. An uncertain or timed-out decode
must not be treated as a successful cancellation or as permission to recycle
DMA memory. That failure contract needs separate qualification before any
production integration.

## PNG: its contract is different

`mtk_png_s_fmt` requires one plane and a 16-byte-aligned row pitch. Both queues
admit USERPTR only. `mtk_png_buf_queue` parses the source and calls
`mtk_png_set_queue_data` for each image. That helper replaces capture width,
height, format, pitch and size using the PNG header and ARGB32 output.

A prospective client must initialize source processing before committing the
final capture allocation and re-read the resulting capture layout. It must
not wait for a JPEG-style source-change event that the PNG ioctl table does
not expose, or reuse old capture buffers after dimensions change. Output byte
order, alpha semantics, bit depth and interlace support require independent
pixel comparisons. The follow-up established BGRA byte order, exact RGB8 and
RGBA8 output, and exact grayscale8 output. Its RGB full-size case was
1860 by 2480. Palette, high-depth and interlaced cases were not qualified.

## MDP: preserve geometry and color semantics

For this driver, selection types are VIDEO_OUTPUT for CROP and VIDEO_CAPTURE
for COMPOSE, even when pixel-buffer queues use the multiplanar types. MDP
memory geometry should be padded as required by the driver, while crop/compose
rectangles describe the desired visible dimensions. Every returned payload
must fit the allocated buffer; reading only an earlier G_FMT size can truncate
output when hardware pitch is larger.

The small Gray8 copy and rotations were exact. The 128-by-96 to 64-by-48 result
had a mean absolute difference of 4.6888 and a maximum difference of 31 from
Pillow bilinear. RGB-to-gray differed by a mean of 8.7327 and a maximum of 27
from the CPU reference. Neither comparison is an application speedup result.

The full-size case in `display-native-20261115-caa53f82` negotiated 1872-by-2480
memory, a 1872-byte pitch, and a 1860-by-2480 visible compose rectangle. Both
DQBUF operations succeeded and the entire 4642560-byte allocation was saved.
Nevertheless, the first 4612800 bytes contained only sixteen values
(`0, 16, ..., 240`) and the remaining 29760 bytes retained the `0xa5` sentinel.
Interpreting the negotiated layout produced a mean absolute difference of
85.1188 and a maximum of 255 from the bilinear reference. A compact-row
reinterpretation did not fix the image. This is a failed pixel qualification,
not an acceptable filter difference. Its output SHA-256 is
`838ebb93aedc2c63fa28a384fe35a54cb4d75621ecaccb0856481d8f09390577`.

The exact driver maps Y8 to `DP_COLOR_Y8` in `mtk_mdp_regs.c:66-68`, copies
per-context format and pitch into the vendor processing interface, and starts
with zeroed picture-quality settings. The source does not justify silently
changing this request into Y4. The M2M/VPU programming path and HWTCON's direct
MDP path share hardware, but their possible interaction has not been proven
to explain this result. The read-only follow-up still identified video5 as
`mtk-mdp:m2m`; no matching installed C++ headers were found in the checked
include directories. Do not attribute the failure to a specific driver,
allocator, or library defect without further evidence.

The initial full-size capture had also saved fewer bytes than DQBUF reported.
The corrected output from the interrupted decoder batch was missing after
reboot and cannot be analyzed. Both earlier records remain separate from the
fresh failed pixel comparison above. Software scaling remains the application
path; arbitrary dimensions and a production MDP resource-recovery protocol
are not qualified.

The standalone MDP probe now checks QUERYCAP driver identity and streaming
capabilities before format changes, compares S_FMT and G_FMT layouts, validates
the actual buffer capacities, and preserves the entire returned payload.
It explicitly reports that pixel validation is external. These guards catch
contract violations; they cannot turn an incorrect image into a passing result.

The revised probe was subsequently executed again. Identity remained exact.
Full-size output still failed, with 4637646 of 4642560 bytes retaining the
initialization sentinel. Repeating that same request with awesome and statusbar
parked by the validated launcher, after a three-second settle wait, produced
byte-identical bad output. Parking those native writers did not fix this case;
it is not sufficient to attribute the failure to ordinary concurrent UI updates.
The [retest evidence](review/display-modes-20261115/corrected-mdp-results.json)
retains both contexts. JPEG's working IMGRZ path is distinct from this standalone
MDP route and must not be presented as a successful MDP scaling test.

MDP's `job_abort` callback is also empty. The completed positive cases do not
qualify arbitrary timeout injection, forced teardown, all dimensions, all
formats, or a production resource-recovery protocol.

## Display contract and completed qualification

The HWTCON display path uses kernel-owned framebuffer/image buffers, not the
JPEG USERPTR queue path. Y4 flag `0x4000` is distinct from REGAL `0x8000`.
The exact driver selects Y8 input, Y4 output and its `_S` dithering algorithm
when Y4 is enabled. Disabling software quantization preserves the necessary
input precision but is not proof that the hardware flag changed output.

For native grayscale tag 2, the exact waveform table uses GCK16/GLKW16 instead
of ordinary GC16/GLR16. The application selects the wire family from the actual
framebuffer encoding, independently of semantic UI night colors. The new
physical batches used tag 1; tag-2 mapping is covered by source review and
memory-only regression tests.

The reviewed ABI uses a 96-byte SEND request (`0x4060462e`) and an 8-byte marker
WAIT request (`0xc008462f`). Flags are Y4 `0x4000`, REGAL `0x8000`, and swipe
`0x10000`; the older i.MX dither fields remain zero. The reviewed client pattern
uses AUTO/PARTIAL, a valid direction, and enough pixels for the requested steps. The
application transforms direction only for its own render rotation. The exact
driver's `get_dev_swipe_dir` handles framebuffer and panel orientation.

Before each actual framebuffer write, the backend waits for the previously
accepted update. Unknown delivery latches the owner against another write or
retry. A definite advanced-waveform rejection falls back to GC16 with a fresh
marker; a definite Y4 rejection additionally requires canonical software
conversion. The driver's WAIT can return zero for an absent marker or an
internal timeout, so accepted/waited requests do not certify optical completion.

Batch `display-y4-20261115-cabb2be9` used the exact driver's synchronous
`dump_last` export while the managed launcher owned display activity. It
refused preexisting diagnostic dump files, supplied identical Gray8 input in
off/on/off order, and restored the original framebuffer afterward:

- All three framebuffer inputs were byte-identical and contained 256 values.
- Both off-trial post-MDP images were byte-identical.
- Enabling Y4 changed 1294375 of 4612800 post-MDP output bytes.
- Each output contained sixteen encoded values. No 256-level optical claim is
  made, and no optical quality or speed ranking was measured.

Batch `display-native-20261115-caa53f82` then ran the actual application with
isolated demo data. Its next/previous page requests were:

| Preset | Accepted page requests | Final state |
| --- | --- | --- |
| Fast | DU/PARTIAL, flags `0x2`; then GC16/FULL, flags zero, for each page | Preview cleared; software final frame retained |
| Normal | GLR16/FULL, flags `0x4000` | Y4 effective |
| High Quality | GC16/FULL, flags `0x4000` | Y4 effective |
| Kindle | AUTO/PARTIAL, flags `0x14000`, directions 3 then 2, twelve steps | Y4 effective |

All four processes exited normally. Native window-manager/statusbar operation,
original brightness and warmth were restored, temporary files were removed,
and the lease was released with owner/token checks before analysis. Hardware
JPEG/PNG nodes were not exercised. Physical Paperwhite 5/6, optical ghosting,
visible animation direction and full-page latency remain outside these trials.

The exact kernel, pinned
[FBInk ABI](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/eink/mtk-kindle.h),
[FBInk swipe example](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L2607-L2635),
and [KOReader MTK client](https://github.com/koreader/koreader-base/blob/f64c3eb11049e9a75189f186d7345d97c07d87b9/ffi/framebuffer_mxcfb.lua)
provide mutually checked display declarations and calling examples. The
installed MDP symbols provide only partial proprietary-library evidence.
There is no equally qualified installed JPEG/PNG client example in this audit.
The new [standalone codec probe](../scripts/native-codec-probe.c) now provides
a source-reviewed, physically exercised calling example for the qualified
cases; it is not a recovered proprietary vendor-library sample.
