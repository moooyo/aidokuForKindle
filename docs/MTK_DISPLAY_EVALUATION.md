# MediaTek display optimization evaluation

Reviewed: 2026-11-10, Asia/Shanghai.

## Decision

The necessary implementation change is to give each logical framebuffer update
a nonzero, advancing marker and to fence the last successful submission. Reusing
marker `1` for every frame can wait for an older outstanding update in the
reviewed drivers. This is a synchronization correction, with no claimed hardware
speedup.

MTK fast mode, Y4 hardware dithering, and the MDP completion ioctl remain disabled
in this backend. The available evidence establishes useful candidates and some
ABI semantics, but does not qualify an exact physical device and firmware or
establish a performance benefit. No new driver-setting ioctl or experimental
hardware probe is introduced by this evaluation.

| Candidate | Source finding | Application decision |
| --- | --- | --- |
| MTK fast mode | KOReader explicitly enables it on Scribe, as well as Paperwhite 5/6. Reviewed kernels use it to bypass a large-area AUTO waveform promotion. | Qualify the exact device, restore protocol, latency, and ghosting before enabling it. |
| Y4 hardware dithering | KOReader declares Scribe capable; the reviewed 4.9 MDP path accepts a Y4 flag. | Keep CPU dithering. Existing comic pixels are already quantized to 16 levels; setting another flag would not remove that work. |
| MDP completion fence | It fences driver consumption of the framebuffer, before the physical display update. KOReader currently enables its call site for MTK color screens. | Do not replace the panel-update wait. Qualify an exact ABI and reproduce a framebuffer-consumption race before adding another wait. |
| Update markers | Kernel waits find the first matching outstanding marker. | Allocate one identity per logical update, share it across ABI fallback attempts, and change the fence target only after a successful SEND. |

An accepted SEND ABI does not establish support for GET/SET flags or the MDP
completion ioctl. A user-selected `scribe` design profile is also not a physical
hardware identification. Keeping the additional operations absent is the
current fail-closed capability policy.

## Observed physical build

The owned native application smoke test later reported an actual Scribe on
5.19.5 / Linux 4.9.77-lab126, build
`058-juno_1905_barolo_bellatrix3-479432`. The reviewed 479427/479429 GPL packages
are still useful evidence, but neither is an exact build qualification. The
smoke used normal existing framebuffer operations only. No fast-mode setting,
Y4 setting or MDP-wait probe was performed. It does not qualify additional
driver flags, ghosting or physical display completion timing.

## Pinned upstream evidence

The frontend is `koreader/koreader`
`01ccbd790d6269301ad523e75c45f62d022a6d7b`; its committed base is
`koreader/koreader-base` `b153a4a252f6e4295af7a569bcfdd59ffa688fa6`.

Scribe fast mode is not inferred from Paperwhite support. The Scribe initializer
calls `_MTK_ToggleFastMode(true)` itself at
[`device.lua:1858-1859`](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L1858-L1859);
Scribe3 does so at
[`device.lua:1920-1921`](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L1920-L1921).
The base implementation writes the command prefix plus the fast bit, and the
exit path writes the command prefix without that bit. These calls do not save
and restore the previous parameter byte.
[`framebuffer_mxcfb.lua:743-750`](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L743-L750),
[`device.lua:2048-2052`](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L2048-L2052).

KOReader enables `canHWDither` on Scribe and Scribe3. It disables the capability
on Paperwhite 5 and 6; the Paperwhite 6 setting explicitly remains an unverified
assumption in that source. The update path also checks the dither hint and
current color rendering before setting Y4.
[`device.lua:1105-1207`](https://github.com/koreader/koreader/blob/01ccbd790d6269301ad523e75c45f62d022a6d7b/frontend/device/kindle/device.lua#L1105-L1207),
[`framebuffer_mxcfb.lua:656-684`](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L656-L684).

The upstream MDP wait uses a specific update marker. Its initialization is
limited to MTK color screens, and its call follows fast/A2 updates to avoid
restoring temporary highlight pixels before the driver consumes them. That
explains its current application use; it does not prove that every monochrome
MTK driver consumes pixels synchronously.
[`framebuffer_mxcfb.lua:289-295`](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L289-L295),
[`framebuffer_mxcfb.lua:698-707`](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L698-L707),
[`framebuffer_mxcfb.lua:1148-1156`](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L1148-L1156).

Upstream advances markers, skips zero on wrap, and records the selected marker
in each update request.
[`framebuffer_mxcfb.lua:61-69`](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L61-L69),
[`framebuffer_mxcfb.lua:398-405`](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L398-L405).

## Official kernel source examination

Source archives were downloaded from Amazon's source distribution to the
designated `test-env` worker. The catalogue is the
[Amazon source-code notice](https://www.amazon.com/gp/help/customer/display.html?nodeId=200203720).
The inspected archives are:

| Official archive | Nested kernel | Use in this review |
| --- | --- | --- |
| [5.19.5 build 4794270058](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794270058.tar.gz) | `gplrelease/linux-4.9.tar.gz`, Makefile version 4.9.77 | Source candidate A; inspect actual implementation and conditional compilation. |
| [5.19.5 build 4794290058](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794290058.tar.gz) | `gplrelease/linux-4.9.tar.gz`, Makefile version 4.9.77 | Source candidate B; inspect actual implementation and conditional compilation. |
| [5.19.5 build 4794280058](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794280058.tar.gz) | `gplrelease/linux-5.15.tar.gz`, Makefile version 5.15.41 | Excluded from claims about the observed 4.9.77 device. |

The source investigation did not query a physical device. The later owned
application smoke established the model/build recorded above, which still does
not match either source package exactly. A matching marketing version and Linux
version alone do not establish the identity of the loaded driver.
The sources are under
`/root/aidoku-koreader-eval-20261110-01/mtk-research/` on the worker; downloaded
third-party archives and extracted source are not committed to this repository.

The following paths are inside candidate B's nested kernel. Candidate A's
corresponding observations are identified separately where they differ:

| Source path and lines | Executed behavior |
| --- | --- |
| `drivers/misc/mediatek/hwtcon_v2/hwtcon_extra_feature.c:44-70` | For daytime DU/GL16/GC16_PARTIAL selected by the AUTO path, a rectangle wider than one third of the screen and taller than one quarter can be promoted to GLR16 with FULL update mode when the fast bit is clear. |
| `drivers/misc/mediatek/hwtcon_v2/hwtcon_core.c:2842-2890` | AUTO histogram selection calls that replacement function. This reviewed call site returns early for explicitly requested non-AUTO waveforms. |
| `drivers/misc/mediatek/hwtcon_v2/hwtcon_extra_feature.c:73-108` | SET replaces the entire low parameter byte. GET returns that byte; it does not return the command prefix. Unsupported SET selectors can still return success. |
| `include/linux/hwtcon_ioctl_cmd.h:365-378` | SET uses request number `0x3B`, GET `0x3C`; command prefix is `0x80000000`, parameter mask `0xFF`, fast bit `1`. Other low bits are defined and must be preserved. |
| `drivers/misc/mediatek/hwtcon_v2/hwtcon_fb.c:334-357` | SEND validates then submits a task; an ignored/blanked framebuffer request can also return success without queueing one. |
| `drivers/misc/mediatek/hwtcon_v2/hwtcon_core.c:1236-1282,1541-1564,2446-2489` | Submission queues work; a separate MDP thread later reads the framebuffer buffer. A generic assertion that monochrome MTK SEND consumes all pixels before returning is unsupported. |
| `drivers/misc/mediatek/hwtcon_v2/hwtcon_mdp.c:130-163,205-227` | The path uses Y8 input and Y4 output; the Y4 flag enables the selected MDP dither algorithm. It signals MDP completion after conversion. This is display conversion, not a general comic-resize submission. |
| `drivers/misc/mediatek/hwtcon_v2/hwtcon_core.c:931-974,1086-1129` | MDP and panel waits find the first matching outstanding marker and wait on different completion objects. Missing markers and internal wait timeouts can still return `0`. |
| `drivers/misc/mediatek/hwtcon_v2/hwtcon_fb.c:605-610` | MDP completion and display completion have distinct dispatch cases. |

Candidate A conditionally compiles the MDP ioctl definition and dispatch under
`PRODUCT_ROSSINI` (`include/linux/hwtcon_ioctl_cmd.h:264-269`;
`drivers/misc/mediatek/hwtcon_v2/hwtcon_fb.c:583-586`). Candidate B's corresponding
definition and dispatch are unconditional in the inspected source. This is
why an ioctl number appearing in a related header is insufficient qualification.

For reproducibility, candidate B's SHA-256 values are:

```text
include/linux/hwtcon_ioctl_cmd.h
b386c601fcd1a28e04d078ce421f4a33a7ba1b29521d24a9ded73ef21a94b95d
drivers/misc/mediatek/hwtcon_v2/hwtcon_extra_feature.c
4b9dc8a9adb69c427d9a64eb2c0acc1bdc75c771b31906da382a56888f5750da
drivers/misc/mediatek/hwtcon_v2/hwtcon_core.c
7582367c9286693e1d30c9ba0b8ab6ef7909cea991cc6f4a65433481653012fd
```

## Consequences for this application's output

`src/imaging.rs::quantize` already emits sixteen values (`0, 17, ..., 255`),
with serpentine Floyd-Steinberg error diffusion when dithering is requested.
Its nondithered path also quantizes to those values. Enabling a driver Y4 flag
after that work would retain the entire CPU quantization cost. It might apply
a second transform; this review makes no claim that its output would be
identical or visually better.

The backend's retained Slint scene also contains UI antialiasing and overlays.
The present refresh API carries a rectangle, waveform and flashing flag, with
no per-image declaration that the pixels are unquantized and need hardware
dithering. A future offload therefore needs a separate unquantized L8 path,
cache identity changes, explicit dither ownership, correct partial-region
alignment, and visual checks for mixed image/UI refreshes. It is not a safe
one-constant substitution.

`MXCFB_WAIT_FOR_UPDATE_COMPLETE` and an MDP fence serve different lifetimes.
The former asks about panel-update completion and remains the relevant request
for handing control to stock UI or applying a full-frame input barrier. The
latter asks when framebuffer reads for the chosen update have finished, allowing
pixels to be reused before the slower panel waveform completes. It could help
a demonstrated overlapping-write race, but cannot acknowledge a GC16 panel
barrier. Retained normal frames do not reproduce KOReader's temporary highlight
restoration scenario by themselves.

The marker correction applies to Legacy, Rex, Zelda and MTK SEND requests. It
does not add extra waits to ordinary frames. Rejected submissions do not replace
the wait target; an interrupted wait retries its captured target. With no
successful SEND, a wait issues no ioctl. Vendor timeout handling limits the
meaning of success: the application observes ioctl acceptance, not independent
physical-panel completion.

## Qualification required before a fast-mode experiment

1. Establish the authorized physical-device connection and exclusive display
   lease. Match actual model, firmware build, kernel and driver ABI to reviewed
   source. Do not select hardware policy from the design profile alone.
2. Read and persist the original complete parameter byte before any SET. If
   the original fast bit is already set, do not claim ownership of changing it.
3. Write `0x80000000 | original_parameter | 1` only within the qualified lease;
   GET must read back the exact expected byte. Preserve every other low bit.
4. On pause, suspend, normal exit and failure, stop framebuffer writers and
   compare the current parameter with the exact value installed by this lease.
   Restore the saved byte only while ownership and that comparison hold, then
   read back the restoration before releasing stock display ownership.
5. A mismatched current value indicates another owner or an unexpected driver
   state. Do not overwrite that state with a guessed zero. Record a pending
   recovery and stop the experiment. A GET followed by SET is not an atomic
   compare-and-swap; exclusive ownership is required throughout.
6. Use a supervisor and saved recovery state as well as ordinary scope cleanup.
   Process `Drop`/`finally` cannot guarantee restoration after `SIGKILL` or a
   terminated launcher. Verify recovery paths before treating fast mode as
   safe for normal use.

Compare identical page/overlay sequences with and without the candidate mode,
using the same temperature, framebuffer rotation, update rectangles, waveform
requests and cleanup schedule. Record selected driver waveforms, CPU work,
submission latency, consumption latency where supported, visible latency and
ghosting separately. Alternate trial order and include dense halftones,
grayscale ramps, page turns and overlays. Do not count asynchronous SEND return
time as completed display latency. A synthetic worker or mocked ioctl can
verify the state machine, but cannot qualify hardware or establish a speedup.

## Verification scope

Six pure marker state-machine regressions passed on `ssh test-env` using a
standalone `rustc --test` harness. Backend integration regressions additionally
cover all four accepted ABI variants, fallback identity, cached submission and
rejected probes. The final complete backend suite passed all 101 tests; see the
[integration log](../artifacts/koreader-eval-20261110-01/integration/tests-final-backend.log).

No local test, build, runtime probe or VM operation was performed by this source
review. The separately owned physical smoke used existing framebuffer operations
and did not probe new MTK operations. Downloading and reading official kernel
sources is separate from validating a running device ABI. No measured hardware
speedup is claimed.
