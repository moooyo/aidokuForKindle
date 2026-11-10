# KOReader technique evaluation and implementation

Reviewed: 2026-11-10, Asia/Shanghai. Baseline: `ffb3417`.

The preceding delivery completed selected reader changes. It did not establish
that every remaining KOReader technique had been evaluated or adopted. This
follow-up measures the remaining candidates, implements the useful compatible
ones, and records why other mechanisms are not selected.

## Decisions

| Candidate | Evidence and decision | Production change |
| --- | --- | --- |
| Native-channel grayscale conversion | Adopt. Native Luma8 can retain its allocation; RGB/RGBA conversion can avoid DynamicImage's repeated RGBA dispatch while preserving every old output byte. | `imaging/grayscale.rs`, wired into the existing bounded decoder. |
| Force colored JPEG directly to decoder Luma | Reject as a default replacement. The evaluated color cases changed many pixels, and near-white content crossed the crop threshold. CMYK was not supported by that candidate. | No decoder dependency, luminance-matrix or cache-format change. |
| Row-buffer Lanczos3 scaling | Adopt. Preserve vertical-first sampling, f32 accumulation, boundaries, and final rounding; retain one grayscale intermediate row instead of a full RGBA-f32 intermediate image. | `imaging/row_resize.rs` replaces the ordinary page and decoded vertical-window resize calls. |
| Another compressed current-frame encoding | Prefer improving the existing PNG path. Extra codec costs and lifecycle complexity must be compared with the small codec-only saving; KOReader serializes the displayed item on close, not necessarily every turn. | Reuse existing processed PNGs after source-integrity checks; eliminate the eager duplicate source codec pass. The upstream zstd candidate is evaluated separately from the retained zlib measurements. |
| Single-channel retained scene | Adopt for actual 8-bpp mappings. Public Slint target pixels can preserve neutral compositing exactly; actual colored input needs a complete RGB fallback. | Retained Gray8 scene, unchanged output transforms, conservative sticky RGB rebuild. Public input Image buffers remain RGB8/RGBA8. |
| MTK fast mode / Y4 / MDP fence | Keep unqualified driver operations disabled. Scribe does enable fast mode upstream; the earlier report omitted this. Existing pixels are already CPU-quantized, and the MDP fence is not a panel-completion fence. | Correct the research record; do not introduce experimental hardware operations without device/firmware and restoration qualification. |
| Distinct update markers | Adopt as a synchronization correction. Reusing marker 1 can select an older pending update in the reviewed kernels. | Per-logical-frame nonzero marker, successful-submit fence, stable EINTR retry target. |

## Quantified software evidence

All measurements ran on `ssh test-env`, using release builds, deterministic
fixtures, and the project-matched dependency versions and runtime features.
They are host CPU/allocator observations, not physical Kindle timings. Different
stages and their percentages must not be added together as an application
page-turn speedup.

### Grayscale conversion

The same six fixtures produced zero changed output, crop, quantization or
dithering pixels. Native grayscale JPEG decode plus conversion changed from
10.146 to 1.187 ms median; the synthetic colored panel changed from 14.003 to
6.761 ms. Native Luma8 eliminates one 3,145,728-byte conversion allocation.
RGB/RGBA output allocations do not disappear. Timing used the reported fixed
method order, not a claimed randomized or interleaved trial.

The forced decoder-Luma candidate changed 2,055,008 pixels in the colored panel
and moved 2,357,500 near-white pixels across the crop threshold in another
fixture. Its speed alone is insufficient to replace the existing luminance
and rounding behavior.

### Row-buffer scaling

The project-matched comparison passed 51 tests, 252 complete rendering
pixel/geometry comparisons, and all 408 fresh-process records. A synthetic
grayscale page changed from 141.08 to 90.20 ms median; line art changed from
144.10 to 81.61 ms. Tracked temporary allocation for its internal resize changed
from 74,225,984 to 170,260 bytes. Complete-render RSS changed from 88,716 to
28,088 KiB. Identity rendering remained effectively unchanged.

These are separate requested-allocation, main-buffer and process-RSS measures.
They do not establish a whole-application memory bound. The superseded feature
configuration and its original results remain preserved as exploratory data.
See the [full row-buffer report](../artifacts/koreader-eval-20261110-01/resize/README.md).

### Reopening existing pixels

The aligned zlib snapshot comparison retained 735 reopen samples, 275 persistence
samples and 25 codec audits. Raw-zlib level 6 saved only 0.782--3.811 ms of
codec time, while another validated synchronized persistence took
15.663--80.230 ms per encoded frame. An exit-only write moves that cost off the
foreground turn; it must not be subtracted directly from reopen latency or
described as a mandatory cost on every page. The comparison still needs to
account for duplicated bytes, source identity and cleanup. The upstream zstd
format was examined separately before making the codec judgment.
The selected cache-path change reuses existing PNGs
without an unnecessary original-image codec pass. Cold source and processed
content hashes and complete rendering identities remain checked.

Mutable sources outside the private cache root are freshly hashed even when
metadata matches. Integration exposed a same-length, same-timestamp overwrite
that could reuse an old hash within a filesystem timestamp tick. That defect
was corrected rather than weakening the content-identity regression. Trusted
managed artifacts and private staging retain their existing atomic-publication
memo contract. Warm external reads therefore are not I/O-free.

See [the snapshot tradeoff and measurements](READER_SNAPSHOT_EVALUATION.md).

The upstream-format supplement used the existing host libzstd 1.5.7 with frame
checksums, strict lengths and identity checks: 15 codec audits, 315 decode,
165 encode and 165 independent synchronized-write samples passed. Encoding
took 1.290--7.421 ms and the independent synchronized-write stage took
4.984--5.821 ms. These are separate stages, not measured end-to-end close latency. Reading
improved clearly in three cases, was mixed in one, and regressed in one; the
largest codec-only saving was 3.027 ms. This is a legitimate candidate, not an
intrinsically slow encoder. It does not yet establish a startup-image bottleneck
or a need for a second persistent representation beside the PNG already stored.
This delivery therefore keeps the existing codec and takes the measured
integrity-equivalent PNG reuse improvement. See the
[zstd supplement](READER_SNAPSHOT_ZSTD_EVALUATION.md).

### Retained scene and output

Eighteen tests passed, including a real SoftwareRenderer offscreen oracle and
the production arithmetic, refresh and output-control unit tests. The oracle
covers text, opacity, images, paths, gradients, dirty updates, rotation, resizing,
retained output rewrites and RGB fallback. The canonical retained scene saves
4.05 MiB at 1264x1680 and 8.80 MiB at 1860x2480. Rendering plus simulated
scanline output changed from 3.165 to 1.652 ms and 6.878 to 3.599 ms respectively.

The first colored input requires a complete RGB rebuild; restoring the public
repaint policy also makes the next requested frame conservatively redraw in
full. Later dirty updates return to the RGB reference behavior. This cost and
the remaining RGB input images are included in the decision, rather than
claiming a zero-RGB pipeline. See [the complete scene report](review/slint-gray-target-20261110.md).

## Hardware scope and remaining qualification

[The MTK evaluation](MTK_DISPLAY_EVALUATION.md) records pinned upstream and
official kernel evidence. It corrects the missing Scribe/Scribe3 fast-mode
call sites, distinguishes MDP buffer consumption from panel completion, and
documents drivers that can return success after an internal wait timeout.
No new MTK flag or MDP operation is enabled by this work.

Other active sessions supplied the real Scribe connection and shared ownership
procedure. This task waited for the WeRead owner to release the device, then
used its own Aidoku lease, token and private guarded transport. The candidate
was executed in an isolated offline demo on the physical Scribe. It opened the
native 930x1240 logical window at scale two, accepted Reader progress
12 to 13 to 12, exercised the menu, and exited normally. This is actual native
application execution, separate from the host feature tests and earlier VM.

The device reported 5.19.5, Linux 4.9.77-lab126 and build
`058-juno_1905_barolo_bellatrix3-479432`. That is not an exact match for either
reviewed 479427/479429 source package, so no additional flag ioctl was attempted.
Framebuffer captures are not photographs of physical e-ink completion.
Physical page-turn latency, ghosting, battery impact and comparative on-device
algorithm speed remain unmeasured; the percentages above remain host results.

All eleven physical smoke checks passed. Frontlight 8 and warmth 12 were restored
exactly; stock services, framebuffer geometry and rotation, network and power
state were checked. No owned application process, transport or lock remained.
The guarded lease was released at `2026-11-10T10:21:30Z`, and the host-side
post-release check confirmed its absence. See the
[physical smoke report](../artifacts/koreader-eval-physical-20261110-r1/reports/PHYSICAL_SMOKE.md)
and [eleven checks](../artifacts/koreader-eval-physical-20261110-r1/reports/candidate-capture-verification-01.json).

## Algorithm-candidate integrated verification

This table belongs to the algorithm candidate with ARM SHA-256 `e0e8338...`,
before the optional end-to-end timing delivery. The host checks ran on
`ssh test-env`, with Cargo build concurrency six. The
separately owned physical smoke was coordinated through that worker:

| Check | Result | Evidence |
| --- | --- | --- |
| Default all-target suite | 663 library, 3 binary and 95 example tests passed; two opt-in library benchmarks ignored. | [Log](../artifacts/koreader-eval-20261110-01/integration/tests-final-all-targets.log) |
| Kindle-feature library | 676 passed; two opt-in benchmarks ignored. | [Log](../artifacts/koreader-eval-20261110-01/integration/tests-final-kindle.log) |
| Native backend library | 101 passed. | [Log](../artifacts/koreader-eval-20261110-01/integration/tests-final-backend.log) |
| Primary desktop UI cases | 13 passed; zero failed. These are host cases. | [Report](../artifacts/koreader-eval-20261110-01/integration/ui-final-01/report.json) |
| Formatting and ARM release | Passed. ARM executable is 49,181,368 bytes. | [Build log](../artifacts/koreader-eval-20261110-01/integration/build-arm.log) |
| Physical Scribe native smoke | Eleven checks passed, normal exit and exact control restoration; lease released. | [Report](../artifacts/koreader-eval-physical-20261110-r1/reports/PHYSICAL_SMOKE.md) |

The ARM executable's SHA-256 is
`e0e8338763e7a386482f0cfd41c282e1ae52fa0c563277437770ada8dc0f6e6a`.
The same identity was checked on the physical device before execution.
The [verification summary](../artifacts/koreader-eval-20261110-01/integration/verification-summary.json)
and [runtime source manifest](../artifacts/koreader-eval-20261110-01/integration/runtime-source-manifest.json)
bind the returned candidate inputs and retained evidence. Evaluation helpers
have their separate measurement identities; they are not production dependencies.

Original intermediate failures and exploratory runs are retained in the isolated remote root
`/root/aidoku-koreader-eval-20261110-01`. Local operations were source editing,
ordinary transfer/extraction and reading records. No tests, builds or runtime
probes ran on the Windows workstation. The evaluated changes were subsequently
merged with optional causal timing and pushed to main in `4c572ee`. Its
[end-to-end measurement procedure](READER_E2E_MEASUREMENT.md) and later results
retain separate input identities. That delivery does not relabel this earlier
algorithm candidate's physical smoke or the frozen Candidate18 records.
