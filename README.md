# Aidoku for Kindle

A native, independent manga reader for jailbroken Kindle devices, implemented in
Rust and Slint. It follows the supplied Aidoku for Kindle design and the sibling
WeRead project's software-rendered UI, native Kindle backend, remote build, and
firmware-emulator workflow. It is not an official Aidoku release or a KOReader
plugin and does not require the WeRead application.

## Current development follow-up

Version 0.1.2 ports BiliComics' in-memory Reader Raster and complete next-frame
reuse, preserves previous-page history during prefetch, and adds staged startup
and a prepared sleep-cover lifecycle. A final startup refinement removes the
initial maintenance-timer delay while retaining the first-frame gate. The
[runtime port and qualification report](docs/BILI_READER_RUNTIME_PORT_20261112.md)
and [sanitized delivery receipt](docs/review/bili-reader-port-20261112.json)
bind the new executables, package, tests, and measured behavior. Physical and
installation qualification are tracked separately from host checks.

Version 0.1.1 adds silent KPM entry scripts, early localized native startup
activity, an explicit settings-page exit action, and submitted-frame exit
feedback. It also cancels obsolete private Reader work and checks paused or
cancelled downloads before dispatch. The
[BiliComics comparison and verification](docs/bilicomics-followup-20261111.md)
records the adopted techniques, current checks, and acceptance boundaries.
Remote suites, the ARM build and the bounded physical KPM startup/exit check
passed. The user also confirmed loading-only startup and exit through the
existing bookstore/KPM entry. Native state was restored and the lease released.
The earlier measurements below retain their own executable identities.

The compatible reader optimizations and optional causal timing are merged and
pushed to main in `4c572ee`. Its 252 runtime inputs match the rebuilt ARM inputs
byte for byte. Default and Kindle-feature library checks passed 676 and 689
tests respectively, with two opt-in benchmarks ignored in each configuration.
Controlled end-to-end and new physical qualification are tracked separately.
The physical native comparison completed 90 valid measured processes across
two explicitly recorded source identity epochs, with independent strict analyses.
Cold-start software submission fell from 6550.273 to 2848.611 ms at the median;
cached turns fell from 209.653 to 160.880 ms. UI acceptance did not improve
uniformly. All batches restored native controls and released their leases.
See [the dated results and limits](docs/READER_E2E_RESULTS.md).

Commit `ffb3417`, merged and pushed to main, adds chapter-boundary and
vertical-image prefetch, prioritized background decoding, prepared RGB
publication, wide-page halves, editable
categories, metadata backup/restore, bounded logs, optional download preparation,
scheduled library updates, configured tracking accounts, and runtime languages.
See [implementation and current verification](docs/READING_FOLLOWUP.md) and
[the KOReader hardware review](docs/KOREADER_HARDWARE_RESEARCH.md).

The current [KOReader optimization evaluation](docs/KOREADER_OPTIMIZATION_EVALUATION.md)
adds compatible grayscale conversion, row-buffer scaling, existing-PNG reuse,
an 8-bpp grayscale retained scene and distinct framebuffer update markers.
Remote tests and the ARM release build passed. The preceding algorithm candidate
also completed an isolated native Reader smoke on a physical Scribe, including page changes,
menu controls, normal exit and restored native controls; see the
[physical report](artifacts/koreader-eval-physical-20261110-r1/reports/PHYSICAL_SMOKE.md).
That report has its own executable identity and does not qualify every later
instrumented build.
The later [sleep/wake continuity fix](docs/SLEEP_WAKE_CONTINUITY.md) passed 37
remote launcher checks and a bounded physical Scribe visual recheck: the user
confirmed that neither sleep entry nor wake exposed the stock bookshelf.
Host stage measurements do not establish physical page-turn latency, ghosting
or battery improvements. Experimental MTK flags remain disabled.

Optional `--performance-trace <new-file.json>` records bounded Reader timing
from Rust main entry through image acceptance and, on Kindle, the matching
framebuffer write and accepted driver submission. It writes once after runtime
shutdown. See the [end-to-end measurement procedure](docs/READER_E2E_MEASUREMENT.md)
for controlled fixtures, baseline observation code, trace qualification and
the distinction between software output and optical panel completion.

## Frozen Candidate18 delivery

The final Candidate18 package uses the frozen source and payloads below. Its
[strict verification](artifacts/phase3/candidate18-final-root-01/package-final-01/package-verification-01.json)
recorded 137 passing checks, 26 archive members and a valid CRC. The
[returned-copy verification](artifacts/phase3/candidate18-final-root-01/package-final-01/package-return-verification-01.json)
and [I/O owner release](artifacts/phase3/candidate18-final-root-01/package-final-01/owner-release-01.json)
are complete. Acceptance follows the actual scoped UI, native and performance
results; package integrity alone does not establish runtime acceptance.

| Input or deliverable | Frozen identity |
| --- | --- |
| Production source | 236 files; `42b11de8d1b07e7af30665f98bb0dbf6291c0d6a6ec3355005c1402f42b21f24` |
| Desktop application | 265,476,472 bytes; `f07072757d52b78e2d3fc8c71e2e1040bbf55f0ce73859260d30dde898d8b269` |
| ARM application | 48,088,632 bytes; `35dd10da4d6101733ff468d3267d5b98594357465a034323df8ecf625753749d` |
| Launcher | 31,642 bytes; `e4a4bc84a1735889079f3b3f25dcc090c8b55f1875e4bf0a9f393cede6d16254` |
| [Kindle ZIP](dist/aidoku-kindle-development.zip) | 29,700,422 bytes; `b0280e4b26330b07cdd0f86bda8636c2bd33ef5205bb30419888edc14c0aaeb1` |
| [External manifest](dist/aidoku-kindle-development.manifest.json) | 4,877 bytes; `af29e678e78925e7fe40e70fb4d1cf8771220d54074658bf28f13b3c0a2414fd` |

Two fresh bounded Search/F13 repair rounds passed. Each independently reviewed
eight manual cases through three fresh reports, nine comparison pairs and
fourteen necessary image opens, including twenty-seven chapter-unit/status
columns and its own Reader composition. Required findings, required reading
pending and identity mismatches were zero. The
[UI closeout](docs/review/phase3-c18-final-search-repair-closeout-receipt-01.json)
records this scope; it grants no complete Candidate18 reference or stress-suite
result. C16's broader reference review retains its own source and desktop scope.

Targeted native result:

The final C18 targeted Native sequence passed two bounded actual cases: single90 and manual90/270 touch, 270 Down/Up and retained flat wake. The same final PID6613 retained epoch5/page15/manual270 with pressure0, exact wake pixels and no image-job restart. Normal exit restored the exact stock body, policy/USB1 and left no App, sensor, guard or owned lock. These are dedicated firmware-VM synthetic-sensor/RAM-fixture results, not physical Kindle or a repeat of C16 full-matrix acceptance; original failed and unassessed attempts remain preserved.

Formal performance result:

The final C18 formal sequence passed one actual exact ignored library test with twenty measured JSONL records, 21 primary timing processes, six SQL collections, one observer and one marked publication/sync audit. Source, both ELFs, all four tools and the wrapper matched before/after; the Native owner checked-restored the dedicated VM with stock/policy/USB/no-App-lock checks passing. Measurements are bounded host algorithm/render/Store evidence, not physical Kindle or a universal speedup; the 32-page counterexample, limited allocation/RSS counters and scale-dependent SQL remain documented.

See [the current verification record](docs/PHASE3_VERIFICATION.md) and
[delivery record](docs/DELIVERY.md) for the actual result bindings. The
[original README](artifacts/phase3/candidate15/documentation-staging/current-documents-before-final-01/README.md)
and [original design fidelity document](artifacts/phase3/candidate15/documentation-staging/current-documents-before-final-01/docs/DESIGN_FIDELITY.md)
preserve the earlier current-candidate wording and historical review contract.
The [C16 first reference review](docs/review/phase3-c16-final-design-pass1-closeout-receipt-01.json)
and [C16 negative second review](docs/review/phase3-c16-final-design-pass2-closeout-receipt-01.json)
remain historical evidence. The original Phase 2 package remains preserved in
[its recovery directory](artifacts/phase3/historical-phase2-candidate5-package).

## Use the application

- Exit through the action at the top of Settings. Application startup and normal
  exit show localized activity feedback with low-frequency dots.
- The [manual performance comics](docs/MANUAL_PERFORMANCE_COMICS.md) provide
  six attributed or original CBZ workloads for local loading, page turns,
  wide pages, vertical reading and cache checks inside Aidoku's library.
- Import a CBZ/ZIP archive or an image directory through Browse > Local files or
  the library menu. Imported content, progress, bookmarks, categories, and settings
  persist in SQLite and the application's content cache.
- Configure MangaDex, Komga, Kavita, or Suwayomi under Browse. The built-in adapters
  make real provider requests. Add a repository to install compatible Aidoku `.aix`
  sources; installation alone does not establish runtime compatibility.
- Use the phone/computer pairing page for server URLs, credentials, repositories,
  and comic uploads. Pairing uses an on-demand HTTP service on the same local
  network, an expiring session, and a displayed code or QR token.
- Read with right-to-left, left-to-right, vertical, or supported landscape
  two-page layouts. Menus expose image processing, lighting, refresh intervals,
  and download management. Lists use pages instead of scrolling.
  The closed Reader is an image-only full-screen canvas without persistent page
  numbers, bookmark ribbons/corners, footer, status/debug overlays, download
  notices or an invisible footer-cycle target. Tap the center, swipe down from
  the top, or hold to open its controls; closing them restores the artwork.
  Initial loading uses a static message until the first image arrives. Failed
  requests keep the displayed page and offer an explicit retry or return action;
  bookmarks and image exports always refer to the displayed page.
  On devices with a recognized portrait sensor, Up/Down changes the active presentation by 0/180 degrees while preserving the page and manual landscape preference.
  Manual orientation includes reverse landscape at 270 degrees. Reader settings
  also provide a separate-cover option, L-shaped and swipe-only tap layouts, and
  an independent reverse-taps preference.
- Hold a chapter row for 500 ms to select chapters, then select all, download the
  selection, or cancel. The detail menu also offers the next five/ten chapters,
  unread chapters, and all chapters. Ordinary chapter taps and single-chapter
  download buttons retain their normal behavior. Download management includes
  per-job and global pause, resume, and cancel controls.
  Per-book removal requires confirmation and preserves shared files owned by
  other books. Storage settings load actual cache categories and per-book
  associations on demand; shared associations and unique global totals are distinct.

Reading and downloads share one validated raw-image cache instead of keeping
duplicate copies. The scheduler permits two background transfers while reserving
interactive capacity for reading, coalesces identical requests, and applies both
source and origin limits. Volatile disk artifacts use a 256 MiB LRU budget and a
64 MiB free-space reserve; pinned downloads and in-use leases are protected from
normal cache eviction. Processed grayscale PNGs are disposable accelerators: an
unsuccessful derivative write does not prevent reading a valid offline original.
See [the architecture](docs/ARCHITECTURE.md) for the limits of these protections.

The Kindle launcher retains the application process during stock sleep. Waking
returns directly to its retained page without reloading fonts or decoding an
unchanged image. A durable navigation checkpoint also supports recovery if the
retained process is lost. Downloads wait for network recovery and resume only
jobs automatically paused for sleep; deliberate pauses remain paused.

The normal Kindle data directory is `/mnt/us/aidoku/data`. `--data-dir` overrides
it. `--demo` uses a separate `demo` subdirectory and deterministic design fixtures;
normal account configuration is not imported. Use `--demo --offline` for visual
review without provider traffic.

## Build and verify from Windows

Run all application builds, tests, and runtime checks through `ssh test-env`.
The operator PC is used for editing, source transfer, and reading captured images.
If the worker is unavailable, verification is blocked; do not fall back to local
testing without explicit authorization for the current task.

From PowerShell in this checkout:

```powershell
.\scripts\verify-remote.ps1 -CargoBuildJobs 6
.\scripts\verify-remote.ps1 -Kindle -CargoBuildJobs 6
```

The final Candidate18 recipe uses `CARGO_BUILD_JOBS=2`,
`CARGO_INCREMENTAL=0` and `dev.incremental=false`. Independent worker checks
use isolated workspaces, outputs and `CARGO_TARGET_DIR` values; mutexes cover
only genuinely shared writable resources. Formal measurements record their
actual resource and load controls in their own report.

The first command synchronizes sources, runs the Rust and launcher checks, builds
the desktop binary, and executes the desktop UI harness on the worker. `-Kindle`
also cross-compiles the ARM payload and creates
`dist/aidoku-kindle-development.zip`. The worker needs Rust 1.92+, Xvfb, Python,
Pillow, and the dependencies described in [VERIFICATION.md](docs/VERIFICATION.md).
The Kindle build additionally needs Zig, cargo-zigbuild, and the
`armv7-unknown-linux-musleabihf` Rust target. These commands create development
artifacts. The final package above uses separately frozen inputs, fresh staging,
an explicit source-bound manifest and independent remote verification recorded in
[PHASE3_VERIFICATION.md](docs/PHASE3_VERIFICATION.md).

## Install on Kindle

For an existing KPM installation, use the
[KPM package instructions](packaging/kpm/README.md). Close Aidoku, install the
versioned `.kpkg` through KPM, then launch it from the existing KPM frontend.
The package also provides a silent `documents/Aidoku.sh` Scriptlet. This path
preserves the current device profile and user data and does not require KUAL.

For the separately preserved legacy ZIP, follow the instructions below.

Extract the delivered ZIP at the root of the device's USB storage, preserving
the `aidoku`, `extensions`, and `documents` directories. Open KUAL, select the
matching Aidoku device profile, and launch the app. A compatible document-script
launcher can alternatively execute `documents/Aidoku.sh`. The stock document
reader does not execute shell scripts on its own.

See [the bundle instructions](packaging/kindle/README.md) for executable
permissions, the lifecycle wrapper, logs, backup, and removal. A profile describes
layout and capabilities; physical model/firmware acceptance requires its own
recorded device evidence.

## Scope and evidence

Current Aidoku Postcard sources run in a portable Wasmi interpreter with standard
buffers/date, HTTP, typed preferences, and read-only HTML/CSS host services.
Legacy source ABIs, JavaScript, canvas, WebViews, interactive browser verification,
DOM mutation, and text-only reader pages produce explicit capability errors.

Manual orientation is implemented. On a recognized sensor, the active app follows
Up/Down as 0/180-degree presentation while retaining its current chapter/page and
manual 90/270-degree preferences. Other orientations remain manual. Automatic
brightness is not implemented or qualified; the lighting controls cover exposed
frontlight/warmth capabilities. Missing automatic rows in supplied Scribe
references are a documented limit, without establishing the model's hardware
capabilities or unconditional reference parity. The development follow-up
supports configured AniList/MyAnimeList accounts and synchronization; it requires
the user's legitimate credentials or registered OAuth client. The frozen
Candidate18 package retains its original feature scope.

Inversion uses the implemented backend path and its full-refresh boundary.
Physical ghosting, battery use, lighting behavior, sleep/wake, and touch quality
require device testing. Synthetic sensor input and a RAM fixture in the dedicated
firmware VM qualify only the reported software conditions; they do not establish
physical Kindle, persistent-filesystem or e-ink waveform acceptance. A border in
comic artwork is assessed through that Reader's own source, processed image,
expected composition and current frame. No real-account provider acceptance is
claimed by this README.

- [Architecture and data boundaries](docs/ARCHITECTURE.md)
- [Remote verification and evidence](docs/VERIFICATION.md)
- [BiliComics Reader, startup, and sleep-cover port](docs/BILI_READER_RUNTIME_PORT_20261112.md)
- [Design fidelity and intentional differences](docs/DESIGN_FIDELITY.md)
- [Source runtime contract](src/sources/README.md)
- [Download performance method and recorded results](docs/DOWNLOAD_PERFORMANCE.md)
- [Follow-up cache scaling and download recheck](docs/CACHE_PERFORMANCE.md)
- [Reader metadata scaling and measurement limits](docs/READER_PERFORMANCE.md)
- [Retained-process sleep, recovery and native evidence](docs/SUSPEND_RESUME.md)
- [Current Phase 3 candidate verification and delivery](docs/PHASE3_VERIFICATION.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

Application code is GPL-3.0-or-later; bundled third-party components retain their
own licenses. The complete GPLv3 text is in [LICENSE](LICENSE).
