# Scribe media qualification and integration contract

This record extends the [corrected media benchmark](SCRIBE_MEDIA_BENCHMARK_20261115.md)
and the [hardware API audit](SCRIBE_HARDWARE_API_AUDIT_20261114.md). The original
codec crash and the unsuccessful intermediate calls remain in the
[incident record](SCRIBE_CODEC_INCIDENT_20261114.md). Later successes do not
erase those results.

## Qualification status

The requested format, output-layout and performance investigations are complete,
including failed and excluded paths. The device recovered from the
[PNG transparency incident](SCRIBE_PNG_COLOR_KEY_INCIDENT_20261115.md), and
subsequent safe trials completed without another reboot. The current
[implementation contract](KINDLE_MEDIA_ACCELERATION.md) describes the optional
automatic JPEG path and its final verification scope. Display presets and Y4
remain independent of media decoding.

All physical results in this record refer to the first-generation monochrome
Scribe running firmware 5.19.5, software build 479432, and the
`4.9.77-lab126` ARMv7 kernel. The matching source archive is
[Amazon build 4794320058](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794320058.tar.gz).
A Paperwhite or Scribe design profile is not hardware capability evidence.
These firmware identifiers describe the measured environment, not the current
setting's availability gate. At the user's request, supported Scribe models
offer the default-off option independently of their firmware version, subject
to actual driver availability and the existing runtime checks. Paperwhite 5/6
and other Scribe models remain outside the hardware model support list; lack
of qualification does not prove that their silicon lacks an engine.

## Completed format extensions

Every row below passed an actual physical-device pixel comparison. Small JPEG
results differ from direct software JPEG luminance by at most one code; the
listed full-size JPEG results are exact. PNG results are exact after converting
the measured BGRA output order to RGBA. No new critical kernel messages or
reboot occurred in these batches. Each batch preserved native display state,
removed its owned staging, and released its fresh lease before host analysis.

| Input | Small trial | Full-size trial | Reference |
| --- | --- | --- | --- |
| Baseline JPEG 4:4:4 to GREY | 128 x 96 | 1860 x 2480 | Direct JPEG Y, not RGB converted back to Y |
| Baseline JPEG 4:2:2 to GREY | 128 x 96 | 1860 x 2480 | Direct JPEG Y |
| PNG grayscale plus alpha, 8-bit | 64 x 64 | 1860 x 2480 | Exact RGBA, including straight alpha |
| PNG 8-bit palette, opaque | 64 x 64 | Not repeated | Exact palette expansion |
| PNG 8-bit palette plus tRNS | 64 x 64 | 1860 x 2480 | Exact palette and alpha expansion |
| PNG grayscale, 16-bit | 64 x 64 | 1860 x 2480 | Strip the low byte of each 16-bit sample |
| PNG RGB, 16-bit | 64 x 64 | 1860 x 2480 | Strip the low byte of each channel |
| PNG RGB8 with Adam7 interlace | 64 x 64 | 1860 x 2480 | Independently decoded seven-pass PNG |

These are representative format trials, not proof of every possible cross
product of color type, depth, interlace, metadata and dimensions. In particular,
palette tRNS and intrinsic LA/RGBA alpha do not qualify non-palette color-key
tRNS. The latter has a separate source-level concern described below.

The [independent libpng oracle](../scripts/png-rgba-reference.c) matched all
22 prepared PNG references, including 16-bit gray/LA/RGB/RGBA and Adam7.
It explicitly expands palettes and transparency, strips the low byte of
16-bit samples, and applies no gamma transform. A generic 16-bit grayscale
conversion that clamps values to 255 is not an appropriate reference.

The remaining safe cases also completed on the Scribe: palette 1/2/4-bit with
alpha, grayscale 1/2/4-bit, LA16, RGBA16, and Adam7 palette4 with alpha at
64 x 64, plus the full-size RGB16 case. All matched the reference exactly,
with no new errors under the expanded IOMMU-aware kernel detector. JPEG
4:1:1, 4:4:0 and 4:1:0 at 128 x 96 produced GREY within one code of the
reference; they remain outside the page-size/color production allowlist.

## Explicit JPEG chroma output

The following six physical batches completed with the same boot identity,
owned staging removed and their leases released. An expanded audit found ten
IOMMU faults in the full-size NV12/4:2:0 case. Its previous zero-error count
used an incomplete detector and must not be interpreted as safe admission.
The reference uses `jpeg_read_raw_data` to obtain real JPEG Y/Cb/Cr components,
without an intervening RGB conversion or guessed chroma reconstruction.

| Requested output and input sampling | Small result | 1860 x 2480 result | Admission decision |
| --- | --- | --- | --- |
| Explicit NV12, JPEG 4:2:0 | Y exact; U differs by at most 1; V exact | Pixels matched, but ten IOMMU read faults occurred | Excluded from production |
| Explicit NV16, JPEG 4:2:2 | Y/U/V differ by at most 1 | Y/U/V all exact | Qualified for these identity layouts |
| AYUV, JPEG 4:4:4 | No conventional four-byte channel order reproduces Y | U/V exact, but conventional Y interpretation has maximum error 192 | Conventional AYUV is not qualified |

NV12 requires an explicit byte pitch for the interleaved chroma plane:
`align_up(2 * ceil(width / 2), 16)`, with `ceil(height / 2)` chroma rows.
NV16 uses the same byte-pitch rule and the full image height. Both have two
separately backed planes. Select these pitches after the initial header and
before allocating CAPTURE; do not accept the header's half-width NV12 pitch
as the byte pitch of an interleaved row.
This MediaTek driver uses the NV12/NV16 FOURCCs with two CAPTURE planes even
though generic V4L2 clients may associate those non-M FOURCCs with contiguous
storage. Follow the actual returned plane count and this driver's declarations.

On the identical 64 x 64 JPEG that previously had 96 bad luminance pixels,
explicit full-width UV pitch corrected every Y pixel. This separates the
earlier default-layout failure from an actual inability to emit NV12.
Runs were `4211014b`/`f4e5fac2` for NV12 and `cf240444`/`bedd747f` for NV16,
each prefixed by `display-followup-20261115-`.

AYUV runs `6fde7714` and `604620ae` show an unusual split-luma layout:
`Y = row * stride + 2 * x`, `U = row * stride + 4 * x + 1`, and
`V = row * stride + 4 * x + 3`. This extraction reproduces all three full-size
reference components exactly, but does not provide a conventional alpha byte
or a standard AYUV pixel. It is recorded as an observed driver quirk, not
silently relabeled as ordinary AYUV support.

The exact direct-link IMGRZ setup zero-initializes its parameters and sets
`dst.fmt` to AYUV without setting `dst.f.argb_fmt`. The zero enum value is
RGB565, which the output HAL distinguishes from ARGB8888. The ordinary
standalone IMGRZ format mapper explicitly sets ARGB8888 for AYUV. This is a
source-level explanation consistent with the measured abnormal layout;
no kernel patch or undocumented register override was applied. The final
Reader uses NV16 with its separately admitted color inputs; the observed
AYUV quirk and unsafe full-size 4:2:0 path are not automatic Reader routes.

Later boundary trials found that odd-width color output is not reliable.
At 129 x 97, the split-luma AYUV interpretation had final-column errors up to
143 codes, and NV16 chroma also failed. Grayscale JPEG at that size passed.
AYUV 128 x 97 and 130 x 97, and NV16 130 x 97, passed their applicable
component checks. Production therefore rejects odd-width color input and
YUV targets. The split-luma route is not used by the final automatic Reader
policy because unscaled hardware decoding was slower than software.

The expanded historical audit also found ten IOMMU faults in the earlier
full-size color 4:2:0-to-GREY run `display-codec-20261115-64da2756`. Both
color-4:2:0 hardware admissions are withdrawn despite correct visible pixels.
The exact internal DMA defect has not been established. The application
rejects that sampling before hardware; it does not add guessed buffer padding
or repeat the faulting path. The two remaining planned full-size 4:2:0 scale
cases were replaced by pre-device rejection checks.

## Combined color resizing

| Physical case | MAE | PSNR | Maximum difference | Kernel errors |
| --- | ---: | ---: | ---: | ---: |
| NV12 small 64 x 64 to 32 x 32 | 0.4883 | 50.85 dB | 2 | 0 |
| NV16 small 4:2:2, 128 x 96 to 64 x 48 | 2.5723 | 34.36 dB | 29 | 0 |
| NV16 4:2:2, 1860 x 2480 to 930 x 1240 | 0.07856 | 47.36 dB | 37 | 0 |
| NV16 4:4:4, 1860 x 2480 to 930 x 1240 | 0.08358 | 47.25 dB | 36 | 0 |

The small 4:2:2 fixture failed the predeclared MAE <= 1 / PSNR >= 40 dB
comparison gate. This is retained as a filter/quality limitation, not relaxed
after the fact. Large fixture successes do not prove identical filtering for
all artwork. The hardware option is off by default and explains that detail
can change. Small images use software.

## PNG performance

Batch `display-png-bench-20261115-4395b916` compared the same visible
1920 x 2560 scene encoded as Gray8, RGB8, and opaque RGBA8 PNG. The target was
930 x 1240. Each path had one warmup and seven interleaved measured samples.
The hardware call ran inside the benchmark process with resident compressed
input. Both paths used the production Reader grayscale/alpha conversion and
the same production CPU Triangle resize. All final pixels were identical.

| PNG input | CPU decode and resize median | Hardware decode and CPU resize median | Hardware time increase |
| --- | ---: | ---: | ---: |
| Gray8 | 219.04 ms | 377.72 ms | 72.45% |
| RGB8 | 348.41 ms | 485.60 ms | 39.38% |
| RGBA8 | 404.82 ms | 556.45 ms | 37.46% |

The hardware intervals include DMA staging, allocations, decoder context and
queue operations, BGRA-to-Gray8 conversion, copying and cleanup. They exclude
subprocess transport, pixel files and output hashing. CPU resize alone took
about 191-193 ms. These results establish working PNG hardware decoding, but
do not support making it the default performance path. They do not measure
power consumption or every possible PNG workload.

For comparison, the earlier grayscale JPEG plus linked IMGRZ benchmark
measured 27.68 ms versus 277.13 ms at 930 x 1240, and 53.37 ms versus
443.64 ms at 1860 x 2480. Those are 10.01x and 8.31x image-processing
speedups on that input, not full page-turn speedups. Hardware and Triangle
filters differ; the prior report retains their quality metrics.

## Memory and completion contract

The matching JDEC and PNG drivers advertise `VB2_USERPTR` on both queues.
Their rejection of MMAP buffer allocation does not mean USERPTR is invalid.
The successful calls use initialized, page-aligned, prefaulted allocations
which the driver pins and maps for DMA. Never pass a framebuffer mapping or
an unverified vendor object as a substitute for those buffers.

For JPEG, parse the first OUTPUT header and receive SOURCE_CHANGE before
selecting or allocating CAPTURE. Explicitly choose the output format, dimensions
and pitches, read them back, and allocate exactly the returned plane count.
The submitted JPEG payload includes an initialized zero tail:
`round_up(encoded_bytes + 64, 64)`. Merely allocating extra memory without
including this tail in `bytesused` did not fix the observed FIFO timeout.

Preserve the compressed input and every CAPTURE plane until both queues have
stopped and the context has closed. Do not interrupt a hardware transaction
merely because a Reader generation was superseded. Cancellation fences may
discard the completed result; they are not DMA cancellation primitives.

The exact kernel establishes the following additional constraints:

- `v4l2-mem2mem.c:253-278` waits for `TRANS_RUNNING` to clear after invoking
  `job_abort`. `v4l2_m2m_streamoff`, at lines 424-461, performs that wait before
  calling `vb2_streamoff`.
- `mtk_jdec_core.c:822-824` has an empty `job_abort`. A caller's timeout does
  not prove that the engine stopped. A pending transaction may also wait for
  shared IMGRZ resources.
- The JPEG worker resets and powers down JDEC before reporting completion
  (`mtk_jdec_hw.c:191-195`), and the linked IMGRZ completion path uninitializes
  its engine (`mtk_imgrz_core.c:1390-1406`).
- `mtk_jdec_release` frees its temporary chroma allocation before its M2M
  context release. A client must stop its streams before closing the context;
  closing an actively running GREY context is not a substitute for that order.
- The JPEG worker can report DONE after logging an internal timeout. Buffer
  flags alone are insufficient. Qualification and production admission need
  bounded formats, device/firmware checks and a conservative failure latch.

Keep any uncertain transaction and its memory alive in its owning worker until
the completion/stream-stop contract is satisfied. Never free buffers, kill a
worker or close an active decoder to pretend that a timeout was cancelled.
Any production fallback must preserve this ownership independently of the
requesting Reader task.

## PNG color-key source concern

In the matching `mtk_png_core.c:475-477`, `mtk_png_set_trns` receives the
start of the source USERPTR buffer. For non-palette transparency,
`mtk_png_hal.c:260` reads from that pointer without adding `trns.offset`.
The RGB branch also writes both color-key register words to KEY0 at lines
272-274. Palette transparency uses `iova + trns.offset` and a different
hardware path. A dedicated grayscale color-key trial subsequently disconnected
the device. The recovered previous-kernel log confirms an ARM page-domain
fault at the submitted userspace address and a new boot identity. Further review identified direct
unprotected userspace-pointer dereferencing from the M2M decode callback, a more
serious defect missed by the initial offset review. The probe now rejects all
non-palette tRNS before opening hardware. See the separate
[incident and containment record](SCRIBE_PNG_COLOR_KEY_INCIDENT_20261115.md).

## Output color policy

The Reader's established conversion weights are 54/183/19 with white-paper
alpha compositing. Native JPEG GREY is JPEG Y, which is a different policy for
color input. A production implementation must either preserve the Reader
policy using qualified chroma output, restrict acceleration to grayscale
input, or expose a clearly described opt-in tone change. A performance result
from a grayscale JPEG cannot silently justify replacing color-page tones.

Host analysis of the already captured NV12/NV16 planes now validates a route
that preserves the existing RGB-to-gray policy. Chroma is expanded with
centered bilinear interpolation, vertically before horizontally, and converted
with the pinned `zune-jpeg` 0.5.15 scalar coefficients. RGB must be clipped
before applying the Reader's weights:

```text
y0 = Y * 16384 + 8191
cb = Cb - 128
cr = Cr - 128
R = clamp((y0 + 22970 * cr) >> 14, 0, 255)
G = clamp((y0 - 11700 * cr - 5638 * cb) >> 14, 0, 255)
B = clamp((y0 + 29032 * cb) >> 14, 0, 255)
gray = (54 * R + 183 * G + 19 * B + 128) >> 8
```

The full-size NV16 4:2:2 case then matches the production CPU Reader exactly.
The full-size NV12 4:2:0 case has MAE 0.00413 and maximum difference 1;
the small cases differ by at most 1-2 codes. These are conversions of retained
physical outputs, using a CPU reference on `test-env`; they are not new device
runs, scaled-color qualification, or physical timing measurements. The
[comparison evidence](review/media-qualification-20261115/yuv-zune-grayscale-comparison.json)
retains every result. The earlier generic Pillow color-conversion comparison
is retained separately because its rounding differs from the actual decoder.

Standalone full-size MDP output did not pass the earlier pixel checks, even
after parking native UI services. Its small identity/rotation successes do
not qualify it for ordinary full-size Reader work. Do not enable that path or
include its unsuccessful output in speedup figures.
