# KOReader hardware image-processing research

Reviewed: 2026-11-10, Asia/Shanghai.

## Result

KOReader uses the MediaTek Kindle display driver, including conditional hardware
dithering and an MDP completion fence on selected models. Its reviewed image
scaling paths use CPU raster libraries or a nearest-neighbour pixel loop. No
general-purpose submission of comic image buffers to a MediaTek MDP scaler or
rotator was found in the reviewed application code.

These are separate findings: the MDP can participate in the driver's display
pipeline without KOReader using it to resize a source comic into a destination
image. This source review does not establish that the firmware lacks a usable
MDP scaling interface.

## Source snapshots

The main review follows the actual frontend-to-base dependency:

| Repository | Fixed commit | Role |
| --- | --- | --- |
| `koreader/koreader` | `01ccbd790d6269301ad523e75c45f62d022a6d7b` | Frontend master observed during the review. |
| `koreader/koreader-base` | `b153a4a252f6e4295af7a569bcfdd59ffa688fa6` | The frontend's committed `base` gitlink; primary implementation evidence. |
| `koreader/koreader-base` | `2ec21d4121d5172b5e719e5aa13f8d7221f7c7d6` | Independently observed base master, checked for relevant changes. |

The reviewed framebuffer, MTK, BlitBuffer and picture-wrapper files have no
changes between the pinned base and observed base master. The MuPDF wrapper
changes in that interval update the library version and document saving; the
image-scaling call remains `fz_scale_pixmap`.

## Actual image and display paths

### Image scaling and decoding

`RenderImage:scaleBlitBuffer` skips equal-size operations, normally calls
`Mupdf.scaleBlitBuffer`, and selects `BlitBuffer:scale` when legacy image scaling
is enabled. The MuPDF wrapper creates a pixmap over compatible source pixels,
calls `fz_scale_pixmap`, and copies the resulting samples into an owned
BlitBuffer. This path contains no MDP task or device submission.
[Frontend selection](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/ui/renderimage.lua#L303-L328),
[MuPDF wrapper](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/mupdf.lua#L1028-L1109).

The legacy scaler allocates a new image and runs nested nearest-neighbour pixel
loops. `PicPage:draw` uses that scaler directly. PDF/CBZ raster rendering applies
scale and rotation matrices through MuPDF before drawing into an image buffer.
[Nearest-neighbour loop](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/blitbuffer.lua#L1655-L1669),
[picture drawing](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/pic.lua#L38-L41),
[MuPDF page drawing](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/mupdf.lua#L716-L748).

JPEG rendering can decode through TurboJPEG, including directly into an 8-bit
grayscale buffer. The libjpeg-turbo build conditionally enables CPU SIMD through
`WANT_SIMD`; this is distinct from a MediaTek MDP accelerator.
[JPEG decoder](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/jpeg.lua#L38-L74),
[SIMD build options](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/thirdparty/libjpeg-turbo/CMakeLists.txt#L8-L18).

### MediaTek display updates

The MTK framebuffer backend submits `MXCFB_SEND_UPDATE_MTK` through a real
`ioctl`. The request describes an update rectangle, waveform and display flags;
this call is not a source-to-destination comic scaling operation.
[MTK update path](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L635-L709),
[ioctl submission](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L467-L473),
[request structure](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi-cdecl/include/mtk-kindle.h#L247-L277).

Hardware dithering sets `MTK_EPDC_FLAG_USE_DITHERING_Y4` only when the device
capability and current color-mode conditions permit it. The reviewed frontend
disables `canHWDither` on Paperwhite 5 and 6, but enables it on Scribe and Scribe3.
The Paperwhite 6 source explicitly identifies its disabled setting as an
unverified model assumption. These capability settings are code behavior, not
new measurements of this project's device.
[Dither conditions](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L669-L684),
[Paperwhite capabilities](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L1105-L1149),
[Scribe capabilities](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L1174-L1207).

`MXCFB_WAIT_FOR_MDP_COMPLETION_MTK` is also actually called. Its current
initialization is limited to MTK color screens, and the wait follows fast/A2
updates so temporary highlight pixels are not restored before the driver reads
them. This is a framebuffer-consumption fence, not a rescaling command, and is
not enabled by that condition for a monochrome Paperwhite 6.
[Fence implementation](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L289-L295),
[enablement](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L1148-L1156),
[ABI semantics](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi-cdecl/include/mtk-kindle.h#L384-L389).

Paperwhite 5/6 and Scribe/Scribe3 initialization enable the driver's MTK fast mode through
`MXCFB_SET_UPDATE_FLAGS_MTK`; exit disables it. This controls driver waveform
promotion. It is a useful display-scheduling reference but does not accelerate
comic decoding or scaling.
[Device initialization](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L1768-L1802),
[Scribe initialization](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L1858-L1859),
[Scribe3 initialization](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L1920-L1921),
[fast-mode ioctl](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L743-L750),
[exit restoration](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L2048-L2052).
The upstream exit call clears the fast-mode parameter; it does not snapshot and
restore the prior parameter byte. See [the display evaluation](MTK_DISPLAY_EVALUATION.md)
for the separate official-kernel review, synchronization correction and exact-device
qualification requirements.

### Rotation and other platforms

The Linux framebuffer backend can request hardware rotation using
`FBIOPUT_VSCREENINFO` when a frontend provides `forced_rotation`. The reviewed
Kindle configuration does not supply that setting and follows the default
BlitBuffer rotation path. Allwinner/sunxi has a separate ION/display rotation
field; it is not evidence of a Kindle MDP rotator implementation.
[Conditional Linux rotation](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_linux.lua#L202-L238),
[default rotation](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer.lua#L452-L462),
[sunxi-specific submission](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_sunxi.lua#L329-L340).

## Implications for this project

### Supplied physical-device observations

The user's parallel read-only investigation identified a Kindle Scribe running
5.19.5 / Linux 4.9.77-lab126, with device-tree compatibility
`mediatek,mt8110` / `mediatek,mt8512`, two Cortex-A53 cores, NEON/VFPv4 and a
32-bit userspace. That investigation reported no enabled conventional Mali/DRM
stack or EGL/GLES libraries, while finding `mtk-mdp:m2m` (`video5`), resize and
rotation blocks, `imgrz` (`video0`), JPEG (`video1`) and PNG (`video6`) drivers.
It also identified a running `mdpd` and `libmdp.mt8512.so` symbols for
`DpBlitStream` buffer configuration, support queries and rotation.

These observations were supplied by the user-authorized parallel investigation;
this review did not repeat device probes or submit a hardware task. They make
MDP a concrete candidate for a separate experiment, but do not establish format
compatibility, filter quality, safe concurrency, or a measured speedup. They
must also remain separate from this project's synthetic firmware-VM evidence.

The applicable KOReader examples are avoiding unnecessary rescaling, decoding
directly to grayscale, reusing rendered pixels, and handling MTK display flags
and completion semantics by model. They do not provide a ready-made MediaTek
comic scaling backend to transplant.

A separate MDP scaling experiment would need the actual firmware's userspace
ABI, supported pixel formats, buffer allocation/import rules, stride and size
limits, rotation support and completion synchronization. It should compare
complete transfer-and-scale cost and output quality against the CPU path before
becoming a production default. An exposed device node or a completion ioctl
alone does not establish that such a buffer-to-buffer scaling service is usable.

## Method and limits

Official GitHub search and fixed official source checkouts were read. Repository
searches covered tracked frontend/base code for MDP, V4L2/`VIDIOC`, device nodes,
MDP scaling/rotation identifiers, PxP, IPU and G2D task APIs, followed by tracing
the actual rendering and Kindle refresh functions. Header declarations and
comments were distinguished from executed call sites. No general MDP/V4L2
memory-to-memory scaler submission was found in that scope; PxP/IPU references
were compatibility headers or display-driver context.

The checkouts were created and inspected on `ssh test-env`. No test, build,
benchmark, device probe, VM operation or firmware modification was performed.
Downloaded third-party source trees and private driver internals were not
exhaustively audited. The supplied observation about absent GPU/DRM/EGL/GLES
and exposed MDP hardware was not independently re-probed by this review. No
physical-device latency or hardware-acceleration speedup is claimed.
