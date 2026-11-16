# Kindle media acceleration

The Reader has an opt-in JPEG hardware path for supported first-generation
Scribe models, independent of their firmware version. Its default is off.
The automatic policy uses the
combined decoder and IMGRZ resize only when the complete operation is eligible;
it keeps the software pipeline for cropping, unchanged dimensions, upscaling,
unsupported inputs and hardware failures. This avoids the decoder-only route,
which was slower in the measured production pipeline.

The control is under **Settings > Hardware**. The existing Y4
dithering selector is also available there. Y4 and the four display presets are
independent of JPEG decoding: see [display modes](DISPLAY_MODES_20261115.md)
and [the Y4 audit](HARDWARE_Y4_DITHERING.md).

## Device admission

Model admission does not read or compare firmware, software-build or kernel
version strings. A system update alone does not disable a supported model.
The 2026-11-15 user-requested hardware policy supersedes the original exact-build
gate; recorded firmware versions remain evidence provenance only.

Production availability requires all of the following:

- Native ARM Kindle execution and a positively identified first-generation
  monochrome Scribe model code, read without retaining or logging the serial:
  `27J`, `2BL`, `263`, `227`, `2BM`, `23L`, `23M` or `270`.
- The packaged `kindle-jpeg-worker`, a matching JDEC QUERYCAP response, and
  access to non-destructive per-reader kernel diagnostics through `/dev/kmsg`.
- No unfinished cooperating JPEG worker and no hardware failure latched during
  this application run.

Paperwhite 5/6, later Scribes and unknown model identities retain software
processing and show a disabled control with a reason. These models are outside
the current application support list; this is not a claim that their chips have
no media engine.
Colorsoft is outside scope. A UI design profile or framebuffer size never
establishes this capability.

The physical measurements were made on Scribe 2022 with Linux
`4.9.77-lab126`, software build `058-juno_1905_barolo_bellatrix3-479432`
(firmware 5.19.5). They are not measurements of every firmware release.
On any firmware, the actual driver must still answer the required capability
query; queue/layout checks and runtime failure fallback remain mandatory.

## Production image policy

| Input or operation | Application behavior | Evidence and reason |
| --- | --- | --- |
| Baseline 8-bit grayscale JPEG, one 1x1 component | Eligible for combined decode/resize | Correct small, odd-size and full-page output; successful IMGRZ scaling |
| Baseline 8-bit YCbCr JPEG 4:2:2 or 4:4:4, even width | Eligible for combined decode/resize | Full-page NV16 output and paired Reader comparisons passed; color-to-gray policy is preserved before filter differences |
| Color JPEG 4:2:0 | Software | Two full-size trials produced IOMMU read faults despite correct visible pixels; earlier acceptance was withdrawn |
| JPEG 4:1:1, 4:4:0 and 4:1:0 | Software | Small GREY trials passed, but full-page/color production qualification was not established |
| Odd-width color JPEG | Software | Boundary trials exposed a bad final luminance/chroma column |
| Progressive, arithmetic, multiscanned, restart-coded or unqualified JPEG | Software; no hardware submission | Conservative header and entropy admission |
| PNG, WebP, GIF and other image formats | Software | PNG hardware was slower in the measured pipeline; other formats lack a qualified hardware route |
| Border trimming, centered region crops, vertical viewports, upscaling or unchanged size | Software | Preserve existing geometry/filter behavior and avoid the measured decoder-only slowdown |
| Standalone full-page MDP resize/rotation | Not used | Full-size output failed pixel qualification; small positive examples do not qualify it |

Hardware input limits are 2 MiB compressed, 16-2048 pixels wide, 16-4096 high,
and at most 6 Mi pixels. Small inputs below 256 x 256 equivalent pixels use
software to avoid setup overhead. Targets must be within the source dimensions,
at least 16 pixels per axis, and even-width for color output. These limits are
separate from the application's broader software image limits.

Hardware and software resizing use different filters. The setting explicitly
allows those detail differences; it does not promise pixel-identical output
for arbitrary artwork. A high-frequency small color fixture failed the declared
MAE/PSNR comparison gate and remains recorded as a failure. The application
avoids hardware for small inputs, but that does not turn the larger-image
comparisons into a universal image-quality guarantee.

## Calling contract

The exact [Amazon source archive](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794320058.tar.gz)
and physical pixel tests establish this order:

1. Validate JPEG marker lengths, coding, component sampling and color semantics,
   quantization/Huffman tables, scan layout and complete entropy blocks.
   The worker validates entropy without running IDCT or allocating an RGB image.
2. Open the identified JDEC node, subscribe to SOURCE_CHANGE, and prepare only
   the OUTPUT queue using initialized, page-aligned USERPTR storage.
3. Submit `round_up(encoded_length + 64, 64)` bytes, including an initialized
   zero tail. Extra allocation without the corresponding `bytesused` tail did
   not fix the observed FIFO behavior.
4. Queue and start OUTPUT. Consume the parsed-header event and inspect CAPTURE.
   Do not allocate CAPTURE against a guessed pre-header format or plane count.
5. Select GREY for monochrome or NV16 for color, with the target dimensions.
   Explicitly provide and read back the byte pitches and plane capacities.
   On this driver, NV16 uses two separately backed planes despite its non-M
   FOURCC: both have full-width byte pitch and full-height storage.
6. Allocate exactly the negotiated plane count; verify layout again before
   starting CAPTURE. Dequeue both buffers and reject error flags or incomplete
   payloads before publishing any result.
7. Convert full-range YCbCr using the pinned `zune-jpeg` 0.5.15 scalar 14-bit
   coefficients, clip RGB, then apply the Reader's 54/183/19 grayscale weights.
   NV16 chroma expansion follows the existing centered 3:1 interpolation.
8. Stop CAPTURE and OUTPUT, close the context, and only then release USERPTR
   storage. Reject new kernel faults and suspicious timeout-length operations.

NV12 identity tests established that its chroma pitch must describe a complete
interleaved UV row, not the header's half-width default. That fixed the earlier
96 bad Y pixels in the small fixture. It did not qualify full-size 4:2:0 after
the later IOMMU audit.

Conventional AYUV was not correct. The tested firmware has a split-luma quirk:
Y at `row * stride + 2*x`, U at `row * stride + 4*x + 1`, and V at
`row * stride + 4*x + 3`. Even-width 4:4:4 normalization can match the CPU
exactly, but odd width corrupts the last column. That route is retained for
explicit research comparisons; the final automatic Reader path does not use
unscaled AYUV processing.

## Lifetime and failure handling

The application launches the separate `kindle-jpeg-worker` executable for each
transaction. It starts in an independent process session, ignores cancellation
signals during ownership, and holds `/tmp/kindle-jpeg-hardware.lock` until
completion. The parent waits through a bounded I/O owner. A deadline latches
hardware off and returns to software; it never kills the DMA owner or frees
the child's buffers. A lingering worker prevents another cooperating worker
from opening the engine, including after an application restart.

The exact driver's empty `job_abort` does not provide bounded cancellation.
STREAMOFF waits for running M2M work. The JPEG release routine frees temporary
chroma before releasing its M2M context, so closing an active GREY context is
not a safe substitute for successful stream stop. The shared transaction
retries interrupted stops and retains context/storage when stop is unresolved.

The control is disabled after a hardware/transport/diagnostic failure during
the current run. Settings commands enforce the same admission as the UI.
Changing the option invalidates Reader work and distinguishes render/cache
identities; retained source identities also carry the processing policy.
Older software cache keys keep their serialization because the new policy is
omitted when off. Software fallback remains available for every skipped image.

## PNG and incident conclusions

The completed PNG matrix includes palette 1/2/4/8-bit, palette tRNS, grayscale
1/2/4/8/16-bit, LA8/16, RGB8/16, RGBA8/16, and representative Adam7 RGB/palette
inputs. Successful cases matched an independent libpng RGBA reference, using
explicit high-byte stripping for 16-bit samples. This is a representative
matrix, not every combination of depth, interlace and metadata.

Non-palette tRNS is unsafe in the tested driver. Its M2M callback directly
dereferences the submitted userspace address without protected access, also
omitting the chunk offset. The controlled Gray8 color-key trial produced an
ARM page-domain fault and reboot. The faulting byte load and register value
match that source path. The input is now rejected before video-device open;
the RGB variant was not retried. See the separate
[PNG incident](SCRIBE_PNG_COLOR_KEY_INCIDENT_20261115.md) and the earlier
[JPEG plane-allocation incident](SCRIBE_CODEC_INCIDENT_20261114.md).

## Measured application costs

Final automatic-policy revalidation measured:

| Workload | Software median | Automatic hardware median | Speedup |
| --- | ---: | ---: | ---: |
| Gray 1920 x 2560 to 930 x 1240 | 279.65 ms | 85.91 ms | 3.25x |
| Gray 1920 x 2560 to 1860 x 2480 | 466.31 ms | 150.94 ms | 3.09x |
| Color 4:4:4 1860 x 2480 to 930 x 1240 | 353.98 ms | 139.27 ms | 2.54x |

All three used seven measured samples after one warmup. The final policy's
4:2:0, progressive JPEG, PNG color-key, odd-width color, border-crop and
unchanged-size cases made **zero decoder-worker attempts** and matched the
software output exactly. A separate nonuniform spread-size trial also passed.
Every final batch restored native state and released its lease, with no new
critical kernel faults. See [final Reader results](review/media-qualification-20261115/reader-final-results.json).

Seven-sample medians after one warmup, using the actual fitted Reader API on
the Scribe, with native UI parked and identical resident input. The intervals
include entropy validation, worker startup, IPC, DMA staging, cleanup and
Reader postprocessing. Input/output files and hashing are excluded.

| Input and target | Software | Forced hardware decode plus CPU resize | Combined hardware decode/resize |
| --- | ---: | ---: | ---: |
| Gray JPEG 1920 x 2560 to 930 x 1240 | 281.71 ms | 359.24 ms | 98.26 ms |
| Gray JPEG 1920 x 2560 to 1860 x 2480 | 472.91 ms | 538.57 ms | 179.10 ms |
| JPEG 4:2:2 1860 x 2480 to 930 x 1240 | 386.52 ms | 452.97 ms | 133.36 ms |
| JPEG 4:4:4 1860 x 2480 to 930 x 1240 | 355.57 ms | 515.21 ms | 145.48 ms |
| JPEG 4:4:4 with border trimming | 351.63 ms | 485.86 ms | Not applicable; the intermediate implementation took 491.19 ms using CPU resize |

Those initial forced-decoder measurements motivated the final automatic software fallback for
decoder-only and crop scenarios. Their initial as-run source and artifact
identities are retained separately. The earlier 27.68/53.37 ms prototype
figures exclude the final worker/validation/IPC costs and must not be used as
application-level timing claims. None of these numbers is full e-ink page-turn
latency, network latency, cache-hit speed or energy use.

PNG's same-picture in-process hardware-plus-CPU-resize pipeline was 37-72%
slower than CPU alone, despite identical pixels. The production application
therefore does not expose PNG hardware decoding.

## Reuse and verification

The [2026-11-16 navigation/Fast follow-up](DISPLAY_AND_NAVIGATION_20261116.md)
is the latest deployed candidate. It preserves the media admission policy and
updates the display behavior independently of JPEG decoding.

The [2026-11-16 physical deployment](review/kindle-deployment-20261116/README.md)
installed the subsequent 256-swatch candidate on the Scribe, verified all 32
package files, preserved user metadata, and passed two native settings checks.
Its helper is installed beside the application. The JPEG setting was available
and off by default; those deployment checks did not submit hardware decode jobs.

The [hardware-model admission follow-up](review/media-model-policy-20261115/README.md)
records the current version-independent policy, 783 passing Kindle-library
tests, 768 passing default-library tests, 20 binary tests, ten desktop UI cases
and 162 package checks. Its application and helper were rebuilt for ARM.
It does not claim a new physical trial or measurements on additional firmware
releases. The earlier physical evidence below retains its original source identity.

The final ARM application was exercised through its ordinary native launcher
with isolated data. A real pointer enabled the setting; a second launch retained
it; importing a two-page JPEG comic then produced two completed hardware
decode/resize operations in the actual Reader. Native services, lighting and
temporary files were restored afterward. The paired desktop UI suite passed
ten cases, and the bundle passed 162 member, source-binding and ARM checks.
Native library tests passed 780 cases with five existing ignored cases; the
default-library and binary suites passed 767 and 20 respectively. All checks
ran on `test-env`.

The application and worker are packaged together. The final application hash is
`c9b7d25c178ba863929a6b4fb1e8f03f4d163353ada7622f7ad7c164d5cafa8a`;
the worker hash is
`6f45e6b977cc94238cf22a1885dbb7298c7834abfc072aacbc19b748b8b277e2`.
The source fingerprint is
`e9f8d08b6599ef657ecb572d8c343b16050fa87d1fa35579c5e518c9d14108e8`.
These identify an uncommitted candidate, not a new Git commit. No production
installation was replaced by these verification batches.

- [Rust admission and process owner](../src/native_media/mod.rs),
  [entropy validator](../src/native_media/jpeg.rs), and
  [isolated worker](../src/native_media/worker.rs).
- [Shared, source-reviewed C transaction](../scripts/native-codec-probe.c).
  The application build exports only its JPEG worker entry points; PNG and
  standalone trial entry points remain separate build modes.
- [Production Reader comparison](../examples/native-reader-integration.rs),
  [input-admission checks](../scripts/verify-codec-admission.py), and
  [libpng reference](../scripts/png-rgba-reference.c).
- [Dated qualification and evidence](SCRIBE_MEDIA_QUALIFICATION_20261115.md).

All builds and checks run through `ssh test-env`, with six Cargo jobs. Device
batches use fresh identities, restore owned native state and release before
analysis. Credentials, raw tokens, serials and complete kernel logs stay out
of the repository. Conclusions can be reused independently; copied code must
retain its applicable license and provenance.
