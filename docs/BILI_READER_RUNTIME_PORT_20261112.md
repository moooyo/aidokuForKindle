# BiliComics Reader Runtime Port

Work dates: 2026-11-12 to 2026-11-13 (Asia/Shanghai). Delivered version: Aidoku 0.1.2.

The final package is installed through KPM. Its isolated document-entry startup,
normal exit, retained sleep cover, same-process wake, and post-wake pointer
navigation passed. A separate read-only inspection verified all 30 installed
payloads and the preservation of user metadata; two expected operation-log
appends are recorded explicitly below.

Aidoku ports BiliComics' Reader Raster, next-frame preparation, startup, and
native lifecycle implementations while retaining its source adapters, manifest
store, reading progress, and native ownership checks. The Reader and initial
lifecycle reference is BiliComics revision
`0e2b816b13aef5b0fe5cad87be4e85c0d75e088d`, especially
`src/app/reading.rs`, `src/reader.rs`, `src/reader/triangle.rs`, and the
prepare/attach startup boundary. The additional sleep-cover port follows
BiliComics revision `9f6b6884f9cc9a080318cff9b3d5e5f2a7f1d632` and its
`docs/SLEEP_COVER.md`; backend provenance remains documented in
`vendor/slint-backend-kindle/AIDOKU_PROVENANCE.md` and `PATCHES.md`.

## The reported first-page behavior

The reported samples remained on page one when the user tapped the right side.
The actual device preferences were RTL, three tap regions, and no reversed tap
mapping. In that configuration, a right-side tap requests the previous page;
a left-side tap requests the next page. A previous-page request at page one
remains at that boundary. BiliComics uses the same mapping.

Real Slint pointer input against the frozen 0.1.1 desktop baseline reproduced
page one, a right-side tap remaining on page one, and a left-side tap publishing
page two. This explains the boundary behavior and preserves the configured
reading direction. It does not qualify the new ARM build or establish acceptable
loading latency.

## Reader preparation and next-frame reuse

The Reader now follows BiliComics' memory-Raster path:

- Reader-only integer grayscale conversion preserves BiliComics' luma and
  white-paper alpha compositing. Triangle sampling preserves its operation
  order, and crop-local Bayer tables replace the Reader's previous dithering
  path. Covers and download derivatives retain their separate renderer.
- Display preparation produces immutable grayscale and worker-prepared RGB
  buffers. It no longer waits for processed-PNG encoding, validation, and durable
  publication before a page can be shown. Persistent derivatives remain
  available to consumers that require them.
- A Bili-style `NextFrameSlot` holds at most one complete upcoming frame. Its key
  binds the presented origin, requested target, reader and lifecycle generations,
  source revision, effective preferences, and viewport. The nearest complete
  frame is prepared before spending speculative work on a farther frame.
- Speculative grayscale lives with that upcoming frame rather than evicting
  current or previous frames from the history LRU. Speculative lookups of
  resident viewports and vertical sources do not update history recency.
  Foreground consumption promotes the sealed full-Raster key and pixels after
  fresh source verification. Selecting a wide-page half cannot replace that
  full-Raster entry with half-page pixels.
- The slot supports a single page, a two-page spread, the next half of a wide
  page, and the next vertical window. Existing cross-chapter manifest prefetch
  remains in place. A hit reuses the already prepared pixels through the normal
  publication and progress-commit path.
- Reuse still runs fresh `SourceProof` verification on a worker, as BiliComics
  does before reusing its Raster. Mutable external originals are freshly hashed;
  metadata is a change detector, not a content proof. The proof binds the bytes
  that produced the pixels and checks source-path aliases. A changed or missing
  source rejects the prepared frame and falls back to the ordinary render path.
  Manifest identity is checked again when publishing.

Vertical reading also reuses an immutable decoded source between windows.
`render_cache::SourceKey` and `SourceEntry` retain an unrotated
`imaging::ReaderSource`, keyed by cache namespace, source SHA-256, and encoded
length. The first preparation verifies the actual encoded bytes and source
identity. Later windows still verify current source content before reusing the
decoded source; they avoid decoding the complete strip again.

The port preserves cooperative cancellation, shared raw-transfer ownership,
foreground/background decoder admission, image-only reading, and successful
frame-publication timing. A prepared download derivative is not treated as an
original-source proof; that optimization keeps its existing provenance checks.

## Resource accounting

| Retained resource | Limit and ownership |
| --- | --- |
| Grayscale viewports and vertical decoded sources | One existing shared 12 MiB, 16-entry LRU; sources do not receive another 12 MiB budget |
| Prepared RGB LRU | Existing 12 MiB, 16-entry limit |
| Wide-page grayscale halves | Existing 6 MiB, 8-entry cache |
| Upcoming complete frame | One additional slot, at most 32 MiB for unique RGB and retained grayscale allocations together, with a reservation released on consumption, rejection, or invalidation |
| Prepared sleep-cover RGB | One separate image, at most 1536 x 2048 pixels, or 9 MiB |

The 32 MiB slot fits a native Scribe RGB frame of roughly 13 MiB together with
its grayscale Raster. One global reservation limits the completed retained
payload; consumption, rejection, invalidation, and error cleanup release it.
Oversized candidates remain ordinary misses. Grayscale source and viewport
entries share the same 12 MiB budget, eviction policy, and `clear_memory`.
Retained grayscale accounting uses allocation capacity; shared allocations
within a frame are counted once. Diagnostics expose slot state, bytes, hits,
stale misses, reservation count, and current/peak slot payload.

These are component retention limits, not a process RSS cap. A candidate under
construction, worker/channel outputs, active UI images, decoder scratch space,
fonts, metadata, and other existing caches have separate lifetimes. Adding the
listed budgets cannot establish peak process memory. Native RSS and display
performance require their own measurements.

## Startup, exit, input, and orientation

The startup port follows BiliComics' small bootstrap font and prepare/attach
boundary. The first loading scene is submitted and the event loop yields before
font and metadata preparation starts. Font decompression runs on a worker;
faces are delivered and registered incrementally. The complete font families
are selected only after every required face is registered.

`App::prepare` transfers single-owner metadata and dormant services to the UI.
Attachment then yields across six stages: Route, Catalog, View, Settings,
Covers, and Ready. Loading and exit presentation keep all four Aidoku locales
(`zh-CN`, `zh-TW`, `en`, and `ja`). Early exit or sleep during incomplete startup
does not replace the saved navigation checkpoint or preferences with temporary
boot state.

The final startup scheduling refinement replaces the fixed initial 50 ms wait
with the existing 1 ms continuation while preserving the event-loop yield and
native successful-SEND gate. Both executable test configurations were rerun
after this refinement, and both release executables were rebuilt from a source
inventory captured before compilation.

After the event loop returns, teardown clears only local cover state and the
temporary overlay. The native backend has already terminated its presentation
tokens and closed control admission, so teardown must not enqueue another
cancellation. Active-loop cancellation errors and genuine event-loop failures
remain observable. The post-loop regression covers pending and completed cover
states, repeated cleanup, and discarded renderer tokens.

The native input, orientation, and lifecycle review used the common backend
lineage while preserving Aidoku's more complete Gray8 path, successful-SEND
accounting, authenticated presentation, calibration, and layout handling. The
selected Bili-style changes bound the wake/control closure queue, serialize
admission with shutdown, defer framebuffer and touch I/O until ownership is
available, and reject refresh requests while paused. Existing
protocol-A touch support and input-epoch boundaries remain intact. Queue
capacity failure follows controlled shutdown and releases captured resources.

## Prepared sleep cover and retained wake

The sleep-cover option requires `sleep_cover = true` and
`screensaver_mode = "cover"`. The application prepares the selected book's image
during ordinary awake work, after active Reader and cover work. Selection binds source identity,
source revision, book identity, and cover identity. Local files, validated cache
artifacts, and permitted background transfers feed a bounded, proportional,
white-paper, sixteen-level derivative. Source changes and superseded jobs reject
obsolete results. Preparation is gated during startup, suspend, shutdown,
metadata restoration, and pending resume frames.

The power-event path uses only prepared pixels and captured metadata. It does
not start cover downloads, decoding, or a fresh metadata query. A current cached
thumbnail may be used before the larger cover is ready. If no bitmap is ready,
the scene uses the current book's captured text; without a current book it uses
the neutral Aidoku scene. The temporary overlay leaves the Reader's retained
images and progress intact.

The launcher and application use the same strict six-field record for
`sleep-cover-request` and `sleep-cover-ready`:

```text
generation pid nonce epoch sequence suspend\n
```

The record is one newline-terminated line. Generation, application PID, nonce,
sleep epoch, and cover sequence must match the current owned session and the
ordinary sleep request. The private directory is pinned, and bounded record
reads reject replacement, unsafe ownership, links, or invalid framing. Ready
publication validates the current request again and atomically renames a private
pending record. The cover sequence is not a renderer token or an EPDC marker.

The transition closes application and transport admission, cancels pending
pointer input, projects the prepared overlay, requests an independent native
presentation, and returns to the event loop. The backend captures an immutable
presentation token before drawing. It settles earlier tracked updates before
rewriting mapped pixels, submits the complete sleep frame with
`refresh_full_checked`, and waits for that exact accepted submission receipt
with `wait_for_submission_complete_checked`. A later request, reused marker,
cancelled token, changed layout, or failed driver wait cannot acknowledge an
earlier frame. A changed native layout requires another complete frame.

Only completion for the same current request permits synchronous sleep
checkpointing. The application then saves state and reaches the paused
render/input/activity barrier before publishing the matching ready record.
The launcher additionally checks owned process identities and the stopped
application before retaining display ownership. This prevents a ready filename
or an ordinary frame counter from authorizing retained cover display.

The final launcher follows BiliComics revision `9f6b6884` exactly for system
capability discovery: it probes the screensaver modules once per managed
session, before taking display ownership. It accepts the ordinary `screensaver`,
`langpicker`, `blankwindow`, and `usb` modules, requires `screensaver`, and
rejects advertisement modules, unknown modules, malformed output, or a failed
query. Sleep and wake transitions use that session capability rather than
repeating the getter after native display ownership changes. Live passcode,
window-manager identity and stopped state, statusbar state, active power state,
USB takeover, and six-field protocol checks remain authoritative.

Missing or mismatched intent, the five-second presentation timeout, renderer
failure, invalid records, unsupported system screensaver modules, or lost
ownership cancels the cover path and uses the existing stock handoff. Passcode
and USB ownership rules remain authoritative. Fallback removes the temporary
overlay and does not report successful cover presentation.

Wake cancels the sleep token and hides the overlay, stages fresh native portrait
permission, and restores the retained Reader scene. Input and ordinary resume
work remain gated until the required full resume frame is accepted. A failed
native observation keeps the existing scene and ownership barrier for a bounded
retry instead of admitting incorrect pixels or stale touch input.

A checked driver completion is a software/driver observation. Some kernels may
return success after an internal wait timeout; it is not an independent optical
measurement of the e-ink panel. Likewise, a final
`sleep_screen.full_resolution` diagnostic does not establish which bitmap was
actually submitted during sleep; that requires matching presentation evidence
and a capture from the sleeping interval.

## Verification and delivery status

All builds, tests, and runtime probes run through `ssh test-env`, with Cargo
concurrency limited to six. No local Windows verification is authorized.

The shared-library, backend, and sleep GUI runs below remain valid: their
implementation inputs have not changed. The final executable regression runs
include the startup scheduling and post-loop cleanup refinements. Launcher and
package fixtures are rerun separately after the session capability change.

| Remote check | Completed result |
| --- | --- |
| Default-feature library tests | 735 passed, 5 ignored |
| Kindle-feature library tests | 748 passed, 5 ignored |
| Main executable tests, default configuration | 24 passed |
| Main executable tests, Kindle configuration | 24 passed |
| Kindle backend tests | 140 passed |
| Previous complete launcher regression | 71 passed |
| Final session-capability targeted regressions | 3 passed |
| Final complete launcher regression | 73 passed |
| Final KPM package fixture rerun | 4 passed |
| Sleep-screen GUI integration | 40 cases, 56 PNG captures |
| Final Reader GUI functional comparison | 18 scenarios passed |
| Final render-cache regressions | 38 passed, 1 ignored |

Coverage includes real worker cancellation/replacement, Slint publication,
ready-frame reuse, source replacement and aliases, manifest/header changes,
missing originals, corrupt optional derivatives, wide-page selection, spreads,
vertical windows, bounded history retention, and sleep protocol failure paths.
Sleep GUI captures cover four layouts, both night-mode preferences, bitmap and
text fallbacks, long text, neutral scenes, and retained Reader restoration.
Raw Slint captures do not independently qualify native night-mode polarity.

Evidence is retained under `/root/aidoku-reader-fix-20261112-01/` and
`/tmp/aidoku-reader-cover-20261112-e2/`. Historical results and frozen artifacts
remain available, but are not substituted for the final candidate's identity or
qualification.

| Final Aidoku 0.1.2 qualification | Status |
| --- | --- |
| Startup refinement rebuild and relevant regression runs | Passed |
| Final desktop/ARM release identities and matching ELF inputs | Passed; all 311 ELF inputs remain unchanged from the C9 pre-build snapshot |
| Final KPM package, launcher identity, and archive verification | Passed; 30 payload files, no comic sample or user data payload |
| Final GUI functional comparison | Passed; 18 scenarios |
| Numerical desktop comparison | 20 valid processes retained under their measured f0 executable identity; no numerical A5 rerun after the isolated post-loop refinement |
| Native isolated reading, normal exit, and RSS observation | b03 completed one pair with four UI publications and accepted SENDs per side; used the previous launcher |
| Native stock sleep/wake and pointer navigation | b05 passed stock sleep/wake, real left/right pointer input, and normal exit; cover presentation remained false |
| Final production-launcher cover qualification | Passed; 50 evidence gates, visual source-page review, exact retained Reader pixels, same-process wake and normal exit |
| Production installation preserving books, preferences, and progress | Installed and verified; 110 unchanged files plus two verified append-only operational logs |

| Frozen final artifact | Bytes | SHA-256 |
| --- | --- | --- |
| Desktop release A5 | 60,648,776 | `a5d93347134f66d788223672ee93ffa730d440202f5201c7997ef904f16c9f4d` |
| ARM release C9 | 50,092,040 | `c9d4276920532c4e9fa9f1888c12444fbc650eee0c6d1fca1dc438b8f46e8ccf` |
| Production launcher | 41,433 | `faf705dafaba9c6b31292fd196947fa1b8660d601337832a4d15b06fc65f746f` |
| KPM 0.1.2 package | 30,425,529 | `50cc0ed8f70b5c9d56c41bcfe19f35dfd5882c46f358e8b6a450de348e3be25b` |
| KPM payload manifest | Recorded in package inventory | `f705802e98d0d96de547a583650fea3cb5f01abcee9575f627ba3054e1de3546` |

The package was assembled from the prebuilt ARM executable. An independent
archive pass checked every regular member's bytes, mode, size, and manifest
membership, as well as the embedded ARM identity. The retained C9 pre-build
snapshot contains 395 files. Its inventory uses sorted
`sha256 bytes relative-path` records and has SHA-256
`553c93ca616f0f0866541932444804ac3d082223df4f585dbd3bbb2664a6a161`.
The 311-file ELF input subset remains unchanged and has inventory SHA-256
`3bf49a76f5031da66e9a795fd593806d9c85b2e24a0e0d54f92627be77a6b3d3`.
Subsequent implementation changes affect the launcher and its tests only, so
the ELF identities remain valid. The post-packaging 395-file source scope has
inventory SHA-256
`3d81320a191057143e31b139c6c91e9916c22c02183d978c4e78e08ae41f8d02`;
its corresponding Git archive has SHA-256
`c401177d029c7d8baa933c63ff928a46ea35dcdf897ccde86ef4766782f3de7d`.
The archive's files matched that source scope exactly.
These are build/package checks; device installation is qualified separately.

The b03 native pair recorded the following software/native submission
intervals with isolated data. The candidate used the previous
`d05cd09379c36aee3c89f36317b2a6841c2114bdac264f536da685cb9c50b52d`
launcher. These are individual observations, not p50 values or a statistical
speedup claim, and do not qualify the final launcher's cover path.

| Native b03 observation | Previous 0.1.1 | C9 0.1.2 |
| --- | --- | --- |
| Reader open | 2961.371 ms | 1026.375 ms |
| Next page | 178.097 ms | 143.347 ms |
| Previous page | 334.387 ms | 184.116 ms |
| Latest seek | 2621.079 ms | 895.198 ms |
| Business ready | 3157.651 ms | 3728.565 ms |
| VmHWM | 105,352 KiB | 108,380 KiB |

The earlier b05 batch established stock sleep/wake and post-wake pointer
navigation, but its cover-presented flag remained false. Its empty native-read
buffer does not uniquely establish that a blocked getter caused that failure.
The final launcher follows BiliComics' awake, managed-session capability
contract. The b06 production test then completed the retained-cover handshake,
same-process wake, pointer navigation, software orientation changes, and normal
exit without a recovery signal. All native controls were restored, the isolated
device stage was archived and removed, and the fresh lease was released before
host analysis.

The corrected host analysis accepted the same private directory modes as the
production protocol, `0700` and `2700`; its original stricter failed result was
preserved. All 50 software evidence gates passed. Visual review of the sleeping
capture identified the book's first page, `PERF 05 01/08` with `KEEP MOVING` and
scene anchors 001/041/081. Before sleep and after wake, the retained Reader was
page eight, with zero changed visible framebuffer pixels. These are source and
software-framebuffer checks under the bound startup-mode projection, not a
current-polarity observation or optical panel measurement. Physical sensor
motion was not retested.

The final A5 executable passed the 18 GUI functional scenarios. The numerical
desktop comparison remains bound to executable
`f0b988b46e5963451ae2f69ea514adc73e4a92d7aa688ef042288521d18307c4`:
20 valid processes, five paired samples for each ordinary-PNG and high-resolution
workload. Its later changes were post-loop local teardown and external launcher
behavior. All other 309 core inputs and the main-function prefix before the
event-loop call matched the final source. Those historical medians are retained
with their original identity rather than relabeled as A5 measurements. The final
Reader has direct C9 native observations as shown above.

## Installed data and package verification

The supported own-package KPM uninstall and fresh installation completed with
the exact 0.1.2 registration, no dependency edges, matching package metadata, and
all 30 payload hashes. The ordinary document/KPM entry launched an isolated demo
through the detached production worker. Caller stdout and stderr were empty,
application standard descriptors were isolated, normal exit passed, and the
temporary demo was removed. Native state and transport ownership were restored
and released before subsequent read-only inspection.

The original installation controller retained its strict whole-file comparison
failure. The actual document and KPM entry scripts intentionally appended to
`data/logs/kpm-entry.log` and `data/logs/kpm-launch.log`. Read-only evidence
confirmed the same 112-file set and modes, with no additions or removals. All
110 other files were byte-identical. The two logs preserved their exact original
prefix hashes and added only the expected 87-byte KPM banner and 101-byte launch
request. Thus user metadata is preserved, while an assertion that every user
file is byte-identical would be false. The metadata-backup manifests, unrelated
package rows, and unrelated root-hook snapshots also matched.

A current read-only pass verified all 30 installed payloads, the package
manifest, and completion marker. The own 0.1.2 registry row and zero dependency
edges had already been asserted during installation before the payload-installed
marker. An optional later SQL-only probe did not pass readiness and adds no fresh
SQL claim. No recovery, truncation of audit logs, or reinstall was used to mask
the expected log differences. Failed attempts and their release evidence remain
available privately. The [sanitized delivery receipt](review/bili-reader-port-20261112.json)
records the final artifact and verification bindings without device identifiers
or raw protocol tokens.

Performance reports must state their measurement boundary and candidate hashes.
Desktop processing or GUI publication timings do not establish native driver
latency or optical completion. Device trials use isolated data, one designated
operator, and a fresh shared lease immediately before the continuous device
batch; restoration and checked release precede host analysis or manual waits.

