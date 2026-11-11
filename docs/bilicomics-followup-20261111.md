# BiliComics follow-up engineering report

Work identifier: `20261111`. Report prepared on 2026-11-12, Asia/Shanghai.

This changeset addresses the user's request to replace visible command-line
transitions with loading feedback, compare comic loading/page turns/download
read-ahead with BiliComics, review its startup and exit techniques, and adopt
applicable performance techniques. Implementation, remote software verification,
and physical acceptance have separate statuses below.

## Reference and scope

The read-only BiliComics reference is commit
`0e2b816b13aef5b0fe5cad87be4e85c0d75e088d`. Aidoku's starting revision is
`8ba9e177047db3e63f3da3203850f3bfcd0d596a`. The reference repository was not
modified. The comparison used its reader preparation, download scheduler,
source acquisition, startup, native presentation, and launcher implementations.

| User request | Resulting implementation |
| --- | --- |
| Hide command-line transitions and show loading activity on launch and exit | Silent document/KPM entries, early native startup feedback, and an ordinary-exit activity overlay. Automatic native checks passed, and the user confirmed loading-only startup and exit through the existing bookstore/KPM entry. |
| Compare comic loading, page turns, downloads, and read-ahead | Added cooperative cancellation for obsolete foreground rendering and a download cancellation check immediately before dispatch. Retained existing bounded prefetch and shared-cache behavior. |
| Review BiliComics startup and exit techniques | Adopted early feedback and detached launcher entry. Added a new exit submission gate; BiliComics does not provide an equivalent exit loading flow. |
| Review applicable performance techniques | Preserved Aidoku's existing grayscale rendering, row-based processing, cache reuse, bounded resources, and foreground scheduling. No new percentage speedup or optical-completion claim is made. |

## Reader and download decisions

| Technique | BiliComics reference | Aidoku disposition |
| --- | --- | --- |
| Cancel obsolete foreground preparation | `src/app/reading.rs`, `ReadingState::cancel_obsolete_render` and `request_reader_render_traced` | Adopted in [reader_pipeline.rs](../src/app/reader_pipeline.rs). A new request cancels the previous private render. Ordinary page turns retain worker ownership until its completion is consumed, then start the latest target. |
| Check task validity before acquisition | `src/downloads.rs`, `acquire_page`, before and after slot acquisition | Adopted in [downloads.rs](../src/downloads.rs). A newly started page thread checks chapter `Paused/Cancelled` state before entering shared fetch. |
| Bound speculative preparation and preserve useful completed work | `src/app/reading.rs`, `request_next_frame` and `apply_prepared_reader` | Already addressed by Aidoku's bounded page/strip/half-page and next-chapter prefetch. Completed valid derivatives retain their existing publication behavior. |
| Reuse validated source/prepared pixels | `src/reader.rs`, `load_hashed_source_with_profile`; `src/reader/resources.rs` | Already addressed through canonical raw files, fresh source identity checks, processed PNGs, retained grayscale outputs, and worker-prepared Slint pixel buffers. The representations and budgets differ; no BiliComics cache was transplanted. |
| Shared capacity and bounded priority | `src/downloads.rs`, `Capacity`, `queue_slot`, and `wait_for_slot` | Already addressed by [transfer.rs](../src/sources/transfer.rs): shared in-flight requests, foreground promotion, reserved foreground capacity, and origin/source limits. Decoder admission has its own bounded foreground preference. |
| Cancel a waiting or active HTTP future | `src/downloads.rs`, `wait_for_slot` and `acquire_page`, using cancellation signals and asynchronous selection | Requires a separate design. Aidoku uses blocking shared transfers. Cancelling one flight on behalf of a reader or chapter could interrupt another reader/download follower. This changeset does not introduce that behavior. |
| Refresh queued private image authorization batches | `src/downloads/bili_source.rs`, batch freshness and refresh logic | Not applicable to a generic Aidoku source. BiliComics token/key handling is protocol-specific and was not copied. |
| Transfer preparation slots directly to page tickets | `src/downloads.rs`, `run_page_batch` | Not applicable to Aidoku's current separation of chapter preparation, network admission, and decoder admission. |

The private render fence in
[decode_scheduler.rs](../src/imaging/decode_scheduler.rs) preserves the current
decoder priority. Shared raw transfer and its image validation stay outside
that reader-owned scope. Cancellation can stop queue waits and cooperative
image-processing boundaries; it does not forcibly interrupt a library codec or
an operating-system call. A completed valid processed image may still finish
durable publication through `without_decode_cancellation`.

Reader invalidation, source changes, and shutdown cancel the private foreground
render. Existing lifecycle transitions that clear `reader_inflight` remain a
separate ownership path; the ordinary page-turn rule is not a new claim of one
worker across every suspend/restore transition. Download cancellation continues
to drain already admitted shared work, retaining existing pause progress and
pin/cleanup semantics.

The existing performance safeguards remain in place:

- [gray_target.rs](../vendor/slint-backend-kindle/src/gray_target.rs) uses a
  one-byte Gray8 scene for supported monochrome scanout and retains its exact
  RGB fallback for colored input. Slint input images still use the existing
  supported RGB representation.
- [imaging.rs](../src/imaging.rs) and
  [row_resize.rs](../src/imaging/row_resize.rs) preserve bounded grayscale and
  row-based processing, including long PNG viewport handling.
- [render_cache.rs](../src/render_cache.rs) retains the 12 MiB decoded-output
  budget, processed PNG reuse, and source/render identity checks.
  [prepared_pixels.rs](../src/app/prepared_pixels.rs) and
  [wide_pages.rs](../src/app/wide_pages.rs) retain their separate 12 MiB and
  6 MiB retention limits. These are individual cache limits, not a total process
  RSS claim.
- Transfer slots, decoder permits, and worker joins continue to follow actual
  work lifetime. No integrity proof, admission budget, or cache validation was
  removed to obtain an apparent improvement.

## Startup, exit, and launcher behavior

BiliComics `src/main.rs` and `src/app/startup.rs` provide the reference pattern
of presenting startup activity before expensive preparation and advancing real
initialization phases. Aidoku implements that goal with a font-independent
native splash rather than copying the complete asynchronous BiliComics attach
architecture.

[main.rs](../src/main.rs) creates
[StartupFeedback](../src/bootstrap_presentation.rs) before bundled font
decompression and `App::attach`. The startup labels in
[assets/startup](../assets/startup/README.md) are pre-rasterized from the existing
localization resources, with source and payload provenance in their manifest.
The startup path needs no font engine or image codec. A read-only, zero-wait
preference lookup selects the existing language and night mode without creating
or migrating a database.

[StartupSplash](../vendor/slint-backend-kindle/src/splash.rs) submits the initial
frame and updates only the stage/activity area at most once per 1.25 seconds
(0.8 Hz). It uses the authenticated launch generation, native portrait
observation, approved dimensions, and lifecycle display gate. Its worker is
joined before backend installation changes the process environment, then
resumed for preparation and joined/released before the normal event loop takes
over. Sleep, USB takeover, or withdrawal of native display ownership stops
presentation. Startup feedback is not replayed as a retained-reader resume.

For ordinary UI exit, [ui/activity.slint](../ui/activity.slint) presents an
activity overlay while the application still owns display and input.
[ExitFeedback](../src/bootstrap_presentation.rs) records the current
`frame_send_count` baseline. The Kindle backend increments this counter only
after a successful native refresh submission in
[platform.rs](../vendor/slint-backend-kindle/src/platform.rs). A newer successful
SEND is required before `App::shutdown` begins; a framebuffer write, desktop
`AfterRendering` callback, or event-loop yield cannot satisfy the native gate.
Desktop fallback handling remains separate.

The exit activity also advances at most once per 1.25 seconds. The two-second
feedback timeout and the separate two-second drain timeout are event-loop
polling budgets for their respective phases. They are neither a two-second
overall exit guarantee nor evidence that a panel has physically finished
refreshing. Cleanup readiness is observed without blocking the event loop by
`App::shutdown_ready`. Timeout and signal/lifecycle paths preserve cleanup;
loading animation never authorizes painting after native ownership is released.

The entry path is now repository-owned:

- [packaging/kpm/document-launcher.sh](../packaging/kpm/document-launcher.sh)
  contains `DontUseFBInk`, redirects all standard descriptors to private logs,
  and invokes the existing KPM installation.
- [packaging/kpm/launch.sh](../packaging/kpm/launch.sh) redirects its descriptors
  and invokes the production launcher's default detached mode. Supervised
  `--foreground` remains available for host fixtures.
- [packaging/kindle/launch.sh](../packaging/kindle/launch.sh) retains its
  `setsid`/`nohup` worker, native lock ownership, sleep/wake/USB restoration, and
  final status logging. A successful entry return acknowledges the launch
  request; it does not report later application success. No fixed delay is used
  as proof that another native UI has closed.
- [scripts/package-kpm.py](../scripts/package-kpm.py) packages a prebuilt ARM
  executable with an allowlisted, hash-bound payload and retained notices.
  [KPM hooks](../packaging/kpm/README.md) preserve user data, require the
  application to be closed during installation, and retain program rollback
  behavior. Package creation does not establish hardware acceptance.

The known silent chain invokes KPM without `--fbink`. In the pinned KPM 0.2.2
[command dispatcher](https://github.com/KindleModding/KPM/blob/07495e01d6fbb43afb8050116cef140fcbd086ea/cli/main.c),
that option explicitly enables native framebuffer logging before the package
hook runs; its [I/O implementation](https://github.com/KindleModding/KPM/blob/07495e01d6fbb43afb8050116cef140fcbd086ea/cli/io.c)
can therefore bypass standard-descriptor redirection. `--asap` is only passed
to the package hook and is not a silent-output switch. The Scriptlet metadata
controls the external SH launcher, not KPM itself. Direct entry testing does
not establish the flags used by an unidentified storefront front caller, and
the change does not replace that global frontend.

## KPM 0.2.2 local upgrade diagnostic

The first physical update attempt failed during dependency graph resolution,
before the new package's payload hook or application ran. The original Aidoku
0.1.0 programs, package registration, user metadata, other packages, and native
state were restored. All transports closed and owner/token-checked release
completed. A fresh read-only diagnostic batch collected the bounded KPM log
and confirmed the original version before closing and releasing separately.

The uploaded package had matched the frozen delivery SHA. Independent
host `gzip -t`, full decompression to EOF, and reading every regular tar member
all passed. The log's `gzip: unexpected end of file` therefore did not establish
corruption of the uploaded archive. The explicit blocking message was
`Could not resolve dependency graph`.

The installed version is KPM 0.2.2. Its matching source is commit
`07495e01d6fbb43afb8050116cef140fcbd086ea`; an earlier inspected source snapshot
identified itself as 0.2.4 and is not treated as the installed implementation.
In the 0.2.2 [install graph construction](https://github.com/KindleModding/KPM/blob/07495e01d6fbb43afb8050116cef140fcbd086ea/src/install.c),
the installed ID is compared to the raw `file://` target before the new
manifest's real ID is loaded. Both old and new Aidoku artifacts consequently
enter the graph. The [resolver](https://github.com/KindleModding/KPM/blob/07495e01d6fbb43afb8050116cef140fcbd086ea/src/dependencies.c)
rejects the conflicting artifacts. This source behavior is consistent with the
observed failure stage.

The supported workaround is to uninstall only this project's closed Aidoku
program, then install the local file as a fresh package, preserving data and
profile through the owned hooks and device-local rollback backups. Independent
dependency checks are required: 0.2.2's own dependent lookup contains a column
name error and its caller ignores that query's return value. The controlled
update must verify zero relations in either direction, unchanged other-package
rows/hooks, and preserved user metadata around both operations. No KPM binary,
global store hook, or other application is replaced.

## Verification status

The task coordinator reported the following completed checks through
`ssh test-env`, using Cargo concurrency six. No local verification was run for
this follow-up. Counts describe separate configurations rather than unique
assertions or physical acceptance.

| Remote check | Result |
| --- | --- |
| Default library | 680 passed; two opt-in tests ignored. |
| Default binary | Eight passed. |
| Kindle-feature library | 693 passed; two opt-in tests ignored. |
| Kindle-feature binary | Eight passed. |
| Native backend library | 108 passed. |
| Launcher tests | 37 passed. |
| Builds | Desktop debug and Kindle ARM release builds succeeded. |
| KPM hook fixtures | 12 passed, including the independent legacy ZIP entry. |
| KPM package fixtures | Four passed, including exact KPM Scriptlet mapping. |

Reader regressions cover obsolete foreground work, replacement by the latest
target, invalidation ownership, priority/context restoration, and cancellation
of explicitly queued foreground and background decoder work. Existing download
regressions retain cancellation drain, paused progress, and active-pin coverage.
The download thread-entry race was closed without adding a scheduling-dependent
test or a new production synchronization hook.

## Frozen delivery inputs

The final ARM release was rebuilt after the Scribe English header spacing
correction. The KPM archive contains 25 allowlisted payload files and five
package-root files, with no user data, credentials, completion marker, KUAL,
or sample comic. The returned Windows delivery copy and its manifest matched
the frozen originals byte for byte when checked again on `test-env`.

| Input | SHA-256 |
| --- | --- |
| ARM ELF, 49,559,224 bytes | `c2123e189066bd1efe7cc72ee4749a66d1636b850a590d93cc3d7a65729dddc1` |
| Desktop debug ELF used by final affected UI captures | `b66ee6900faac309262cd63d3055646ef6c67baadebb69d097015a5856964bfa` |
| Production launcher | `f5b0a2062cb84f6eaf6b9a984b2bc9825e9376c71fe56ccffca0121d6f7c63c1` |
| `aidoku-0.1.1.kpkg`, 30,200,660 bytes | `9277c2b70462e6dcefa7a37ae1ca1c6aaf3869dd3087d21a4561a566114f87c3` |
| Package sidecar manifest | `032baa20d07638ca97857fd10bb17d0ef28090abc8e3879438ef4c11d72adca2` |
| Canonical sorted 395-file remote packaging snapshot | `881c7ac659dce34c64d977b7f1e12ac5a929c98027964ec599fc2a86a0e4afd4` |

The packaging snapshot contains only a `packaging/kpm/README.md` change after
the ELF build; program and hook bytes remain unchanged. Subsequent top-level
README, engineering-report and work-state edits are outside that snapshot. The earlier
pre-visual-fix executable and archive remain preserved separately and were not
selected for deployment. A staged Git archive also matched 365 runtime,
asset, packaging, script, and Cargo inputs against the remote build tree;
text line endings were normalized to LF and binary assets compared exactly.
The final README correction and its updated payload index were repackaged
separately. Complete gzip EOF, all 42 tar entries and 30 regular members, and
all payload/sidecar hashes passed again for this final archive.

| Follow-up acceptance | Status |
| --- | --- |
| Desktop settings/exit UI appearance, localization, and interaction | Thirteen targeted scenarios passed after correcting two harness assumptions. Four affected scenarios passed again after the Scribe English title spacing fix, with manual visual review. |
| Newly packaged KPM installation, silent launch, ordinary exit, and rollback | Passed the controlled own-package reinstall. The earlier failed update restored the old program/package before a fresh diagnostic and successful update. |
| New native startup/exit submission and ownership evidence | Passed one isolated native document/KPM launch and UI exit with identity-bound trace, restored native state and checked lease release. |
| Actual bookstore/KPM visual transition | User confirmed startup and exit show only loading after automatic checks released the device. |
| New comparative reader or startup/exit performance benchmark | Not performed; no new percentage speedup is certified. |

The first desktop capture round retained two harness failures: settings remain
portrait even when the reader's landscape option is supplied, and the new
header button's pointer coordinates must include the status bar. Corrected
targeted reruns passed. Actual pointer activation records
`exit_feedback_requested`, `exit_feedback_submitted`, `shutdown_started`, and
`shutdown_complete` in order. The latest-target rapid-turn scenario reached
page six after requests for pages four, nine, and six. These are desktop
software checks rather than native display evidence. Independent ready-window
TERM testing exited successfully with no ordinary-exit activity markers and
with an empty owned process group after cleanup.

## Physical delivery and user acceptance

The final package was installed in controlled batch
`aidoku-ux-refresh-20261112-b03`. It independently verified zero dependency
relations, backed up the union of 26 old/new program paths, used KPM's supported
Aidoku-only uninstall and fresh installation, and checked registration,
program hashes, metadata/source preferences, and unrelated package rows/hooks.
The isolated native demo used the installed document entry, KPM hook and
default detached production launcher. Caller output was silent, standard
descriptors targeted private logs, and the worker remained independently alive
after the caller returned.

The identity-bound software trace recorded startup feedback submission at
429,008 microseconds after Rust main, before font decompression completed at
1,105,529 and the window was shown at 8,268,668. UI exit was requested at
14,309,504; the native exit SEND was accepted at 14,413,358, cleanup began at
14,413,361, completed at 14,464,156, and the process finished at 14,718,307.
This is one functional trial with software/driver boundaries, not a comparative
startup benchmark or an optical-panel latency measurement.

The trial needed no recovery signal, dropped no trace events, removed its demo
home/database, and restored window manager, status bar, power, USB/Wi-Fi,
brightness/warmth and framebuffer layout/rotation. Every guarded transport was
reaped. Owner/token-checked release completed at
`2026-11-11T16:55:09.963764Z`; trace analysis started afterward.

After that release, the user launched and exited through the existing
bookstore/KPM entry and confirmed that both transitions showed only loading.
This is separately attributed human acceptance; the frontend's exact command
flags were not instrumented. The
[delivery receipt](review/bili-lifecycle-delivery-20261112.json) retains the
automated outcome, frozen inputs, timing boundary and separate manual result.

The earlier 90-process native reader comparison in
[READER_E2E_RESULTS.md](READER_E2E_RESULTS.md) remains historical evidence for its
explicitly identified binaries, source epochs, workloads, and submission
boundaries. It does not certify this follow-up executable, the new KPM package,
or the startup/exit behavior. Neither that history nor a successful native SEND
establishes optical panel completion. This follow-up's UI, package and native
checks use their own frozen inputs and do not replace those earlier cohorts.
