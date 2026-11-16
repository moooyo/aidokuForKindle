# Scribe corrected media calls and scaling benchmark

Date: 2026-11-15, Asia/Shanghai.

This report preserves the earlier corrected-call and grayscale-JPEG benchmark
scope. Later format extensions, explicit NV12/NV16 results, PNG performance
and a separate PNG transparency incident are tracked in the
[extended qualification record](SCRIBE_MEDIA_QUALIFICATION_20261115.md).
That later audit withdrew production admission for full-size color 4:2:0,
including the earlier color-to-GREY example here, because IOMMU faults had
been omitted from the initial kernel-log detector. Correct pixels remain
recorded as pixel results, not safe hardware admission. Current application
policy and inclusive worker/IPC timings are in
[the integration contract](KINDLE_MEDIA_ACCELERATION.md).

The corrected JPEG and PNG calls were executed on the physical Scribe after
the user explicitly requested a retest. JPEG-to-GREY and the tested 8-bit PNG
paths produced valid pixels, including full Scribe-size images. JPEG's linked
IMGRZ scaler also worked. Standalone full-size MDP scaling and the default
JPEG NV12 output did not pass pixel qualification.

These are isolated probes, not an enabled decoder in the application. The
four display presets remain implemented independently of media acceleration.
The original reboot and the failed intermediate results remain part of the
[incident record](SCRIBE_CODEC_INCIDENT_20261114.md) and
[hardware API audit](SCRIBE_HARDWARE_API_AUDIT_20261114.md).

## Corrected calling sequence

The exact [Amazon 5.19.5 build 4794320058 source](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794320058.tar.gz)
matches the tested device build. Its JPEG and PNG queues support USERPTR.
`videobuf2-dma-contig.c:490-585` pins ordinary page-backed memory, maps its
scatterlist through the device DMA API and checks contiguous DMA extent.
The new probe uses page-aligned, prefaulted allocations and retains them until
both streams and the decoder context have closed. No MDP donor allocation or
guessed proprietary C++ object layout is involved.

USERPTR is the working buffer interface used by the successful trials. The
application allocates memory and submits its userspace address and capacity;
the driver pins and DMA-maps the pages. MMAP instead requests driver-allocated
queue buffers and maps them into the application. The exact JDEC and PNG
drivers set both queues' `io_modes` to `VB2_USERPTR`; their MMAP queue rejection
is not a rejection of hardware decoding or of USERPTR. Framebuffer mappings
and other drivers' MMAP support are separate interfaces.

For JPEG:

1. Open the positively identified JDEC node and subscribe to SOURCE_CHANGE.
2. Prepare only OUTPUT. Include an initialized zero tail in the submitted
   packet: `submitted_bytes = round_up(file_bytes + 64, 64)`. The allocation
   remains larger and page-aligned; its capacity alone is not the submitted
   payload length.
3. Queue OUTPUT and start OUTPUT so the first header is parsed.
4. Consume SOURCE_CHANGE and read CAPTURE. This driver initially reports
   two-plane NV12, even if the client wants grayscale.
5. Before requesting CAPTURE buffers, select GREY with the desired output
   dimensions, stride and capacity. Read the format back and verify it.
6. Allocate and queue exactly the negotiated planes. Recheck layout before
   starting CAPTURE. Wait for both buffers to dequeue before reading output.
7. Stop CAPTURE, stop OUTPUT, close the context, then release USERPTR memory.

The packet-tail adjustment was established with the same decoded image: the
original 1318-byte color JPEG stalled JDEC/IMGRZ and left partial output.
Advertising its initialized 1408-byte padded packet removed the timeouts.
GREY output then matched direct software JPEG luminance exactly. The source
programs FIFO end in 64-byte units and the software write pointer in 16-byte
units (`mtk_jdec_hw.c:303-350`). The tested policy is sufficient on this build;
it is not a claim that the minimum padding has been determined for every device.

For PNG, queue and start OUTPUT first, then read the header-derived CAPTURE
layout. This driver does not expose the JPEG source-change subscription.
The tested layout is one-plane ARGB32, with BGRA byte order on this device.
Do not allocate against an earlier guessed format. The same completion and
memory-lifetime rules apply.

JPEG CAPTURE dimensions also feed the linked IMGRZ scaler. Selecting GREY
with a smaller width/height after header parsing performs decode and scaling
in one hardware pipeline. The optional target dimensions in
[`native-codec-probe.c`](../scripts/native-codec-probe.c) implement this path.
The probe requires a reviewed single-frame opt-in and is excluded from the
application package. The retired common media probe still refuses JPEG/PNG.

## Physical pixel results

| Input and output path | Tested dimensions | Result |
| --- | --- | --- |
| Baseline grayscale JPEG to GREY | 128 x 96; 1860 x 2480 | Small image differed by at most one code from direct software luminance; full-size output matched exactly. |
| Baseline color JPEG to GREY, padded packet | 64 x 64; 1860 x 2480 | Direct software JPEG luminance matched exactly. |
| JPEG plus IMGRZ scaling to GREY | 64 x 64 to 32 x 32; 1920 x 2560 to 930 x 1240 and 1860 x 2480 | Correct geometry and full Gray8 precision. Filter differences are quantified below. |
| RGB PNG to BGRA | 64 x 64; 1860 x 2480 | Exact RGBA-reference match after the measured byte-order mapping. |
| RGBA PNG to BGRA | 64 x 64 | Exact color and straight-alpha match. |
| Grayscale PNG to BGRA | 128 x 96 | Exact match. This grayscale sample alone cannot distinguish RGB from BGR order. |
| Default JPEG NV12 layout | 64 x 64 | Failed pixel qualification, including after packet padding removed timeouts. Do not promote this path. |
| Revised MDP Gray8 identity | 128 x 96 | Exact match with all 256 values retained. |
| Revised standalone MDP scaling | 1920 x 2560 to 1860 x 2480 | Failed both with native UI active and with awesome/statusbar parked. The two revised outputs were byte-identical and mostly retained the initialization sentinel. |

The source rejects progressive JPEG. At the user's subsequent request, the
hardware probe now also rejects progressive JPEG before opening a video
device, with the explicit `unsupported_progressive_jpeg`/ENOTSUP result.
The admission parser follows segment boundaries through the first scan;
an embedded marker inside APP/EXIF data does not trigger a false rejection.
Fifteen worker-side admission checks passed with all video opens and ioctls
intercepted before execution. The ARM probe compiled successfully. This is a
hardware-entry policy; the application's existing CPU decoder was not changed.
The measured benchmark predates this admission addition, and its
[as-run C source](review/codec-admission-20261115/native-codec-probe-benchmark-as-run.c)
is retained alongside the [admission checks](review/codec-admission-20261115/admission-checks.json).

Palette, high-depth and interlaced PNG,
other JPEG sampling modes, arbitrary size changes and sustained recovery after
driver faults are outside this qualification. No new reboot occurred during
the corrected trials. Every owned batch closed its processes, restored owned
native state, removed its temporary staging and released its fresh lease.

For JPEG, the reference is direct software luminance decoding, using libjpeg
through Pillow's luminance draft mode before loading. Decoding to RGB and then
converting back to Y can introduce clipping and rounding differences. Those
earlier comparison values are retained separately. In addition, the actual
Reader uses weights 54/183/19 for color-to-gray conversion. Native JPEG Y is
not automatically a replacement for that color policy. The performance input
below is a grayscale JPEG, avoiding that mismatch.

## Same-picture benchmark

The input is one fixed 1920 x 2560 synthetic grayscale scene encoded as a
baseline JPEG. The identical 288237-byte file was used for every path; its
SHA-256 is
`376ec78abb2c33477cbaf83fac6b5a3f940fc515e10a97a75cf80cdde90bf34b`.

The CPU executable calls the production Reader pipeline and includes the
unchanged production Triangle scaler for the isolated resize measurement.
It first checks that isolated scaling produces the same output as the complete
CPU pipeline. Each target has one warmup and seven measured samples per path,
with alternating execution order. Every repeated output must match its own
qualified reference. Native window management and statusbar activity were
parked, and the stock ondemand CPU governor was preserved.

Median compute times on this Scribe:

| Output size | CPU resize only | CPU decode plus resize | JPEG/IMGRZ decode plus resize | Matched pipeline speedup | Pipeline time reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| 930 x 1240 | 191.60 ms | 277.13 ms | 27.68 ms | 10.01x | 90.01% |
| 1860 x 2480 | 364.19 ms | 443.64 ms | 53.37 ms | 8.31x | 87.97% |

The hardware compute interval includes a fresh decoder context, format/queue
setup, DMA buffer handling, copying the visible result into owned Gray8 memory,
stream shutdown and context cleanup. Both paths begin with prepared compressed
input; input file I/O, output file I/O and output hashing are outside these
compute intervals. The standalone CPU resize begins with a decoded image.
There is no independently measured hardware-only resize interval: JDEC and
IMGRZ work as a linked pipeline, so their overlapping times are not obtained by
subtracting unrelated measurements.

The hardware request-to-dequeue medians alone were 17.94 ms and 24.60 ms;
those smaller numbers are not used for the speedup claims. All seven timings
are retained in the [benchmark evidence](review/display-modes-20261115/native-media-benchmark-results.json).
No new critical kernel messages appeared in the benchmark batch.

The table uses the final timing-fence build. An earlier complete batch is
retained separately and measured 9.92x and 7.61x for the same two targets.
Both batches produced identical reference pixels and stable per-path repeat
checksums. The variation is retained rather than selecting the fastest sample.

The filters differ. Before timing, this scene had to meet a declared MAE <= 1
and PSNR >= 40 dB comparison gate against the production CPU output. Results:

| Output size | Mean absolute code difference | PSNR | Maximum difference | Pixels differing by more than four codes |
| --- | ---: | ---: | ---: | ---: |
| 930 x 1240 | 0.06082 | 50.96 dB | 58 | 1394 of 1153200 |
| 1860 x 2480 | 0.00392 | 70.59 dB | 7 | 2 of 4612800 |

The larger local differences occur at edges. The outputs are not bit-exact
between filters, and this benchmark does not prove identical image quality
for all comics. It measures one uncached image-processing workload, not cache
hits, input/network loading, e-ink waveform time, full page-turn latency or
energy use. Standalone MDP's incorrect full-size output is excluded from all
speedup calculations. PNG decode acceptance is not a measured PNG scaling gain.

## Artifact and integration scope

- Corrected decoder and scaling probe:
  [`scripts/native-codec-probe.c`](../scripts/native-codec-probe.c).
- CPU and paired native benchmark:
  [`examples/native-media-benchmark.rs`](../examples/native-media-benchmark.rs).
- [Corrected codec evidence](review/display-modes-20261115/corrected-codec-results.json).
- [Standalone MDP retest](review/display-modes-20261115/corrected-mdp-results.json).
- [Benchmark results](review/display-modes-20261115/native-media-benchmark-results.json).

The benchmark ARM executable SHA-256 is
`1715a35f38c8757909fb1f367a0cacbbb666456252f836877244cc99b2ec7253`.
The final measured probe ARM SHA-256 is
`eb5342d82c7a53268b22f437c36e834d5a441fdc1549cad8f6f5e2eb3525b437`.
Builds used `ssh test-env` with six Cargo jobs. The Rust benchmark used release
optimization level 3, `lto=false` and sixteen codegen units; the C probe used
optimization level 2. These are separately identified test executables.
The [as-run CPU controller source](review/display-modes-20261115/native-media-benchmark-as-run.rs)
is retained; the current example only received whitespace formatting afterward.

The application still uses its existing software decoder and scaler. The
results justify a separately gated grayscale-JPEG/IMGRZ integration candidate,
with software fallback for unqualified formats and devices. Color semantics,
driver failure handling, cache identities and actual Reader timing must be
preserved and checked during integration. No production installation was
replaced by these experiments.
