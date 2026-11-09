# Remote verification

This document defines the procedure and evidence boundaries. It does not assert
that any command or test has passed. A completed run is established by its exit
status and saved report for the exact binary under review. The design review
record is maintained separately in `docs/REVIEW_LOOP.md` when available.

Historical logs for the earlier 57-case UI suite, 126-test Rust snapshot, or
partial 6/7 native run describe those exact older artifacts. They do not accept
the current cache/download candidate. An expanded source suite or a newly built
candidate, including a 190-test listing, is not a pass until its frozen-source
run and matching reports complete. This document does not declare clean review
rounds or final candidate acceptance.

## Required environment

All application tests, builds, smoke checks, runtime probes, and captures run on
the designated `ssh test-env` Linux worker. Do not run them on the Windows
operator machine unless the user explicitly authorizes local verification for
the current task. If the worker is unavailable, report verification as blocked.
Reading source files, transferring sources, and inspecting captured PNGs on
Windows are separate from executing the application there.

The worker needs Rust 1.92+, the locked dependencies, a C compiler for bundled
SQLite, Python 3, Pillow, Xvfb, and the winit desktop runtime libraries. ARM
cross-compilation additionally needs Zig, cargo-zigbuild, and the
`armv7-unknown-linux-musleabihf` target. Kindle firmware verification needs a
separately prepared, isolated Aidoku emulator instance and its guest SSH helper.
Firmware images are not distributed in this repository or development ZIP.

Keep this task's workspace, build output, captures, emulator disks, and guest
paths separate from the active sibling WeRead session. Shared read-only compiler
caches do not authorize replacing another task's binary or changing its guest.

## Standard PowerShell entry point

From this checkout:

```powershell
.\scripts\verify-remote.ps1
.\scripts\verify-remote.ps1 -SkipUi
.\scripts\verify-remote.ps1 -Kindle
.\scripts\verify-remote.ps1 -Firmware
```

The wrapper synchronizes files and invokes commands remotely. Its defaults use
`/root/aidoku-build/source` for sources and `/root/aidoku-build/target` for Cargo
outputs. It checks formatting, runs Rust tests and launcher tests, builds the
desktop application, checks shell syntax, and, unless skipped, runs the desktop
UI harness. `-Kindle` builds/packages ARM; `-Firmware` also runs the native
firmware harness using the isolated `aidoku-pw6-5201` instance.

Use the script's `RemoteDirectory`, `RemoteTargetDirectory`, `EmulatorRoot`,
`GuestSsh`, and `Machine` parameters when the prepared environment differs.
`Machine` must be an Aidoku-specific instance. A missing emulator is a blocked
native check, not a reason to redirect the script at the sibling session.

## Explicit source transfer and checks

The equivalent PowerShell workflow is useful when diagnosing one failure:

```powershell
$AidokuRemoteDirectory = '/root/aidoku-build/source'
$AidokuTransferPaths = @('Cargo.toml', 'Cargo.lock', 'build.rs', 'src', 'ui', 'assets', 'vendor', 'examples', 'scripts', 'packaging', 'LICENSE', 'THIRD_PARTY_NOTICES.md')
ssh test-env "mkdir -p '$AidokuRemoteDirectory' '/root/aidoku-build/target'"
scp -r @AidokuTransferPaths "test-env:${AidokuRemoteDirectory}/"
$AidokuRemoteCommands = @'
set -eu
. /root/.cargo/env
export CARGO_TARGET_DIR=/root/aidoku-build/target CARGO_BUILD_JOBS=2
cd /root/aidoku-build/source
cargo fmt --all -- --check
cargo test --locked --all-targets
cargo test --locked --lib --features kindle platform::
cargo test --locked --manifest-path vendor/slint-backend-kindle/Cargo.toml --lib
python3 packaging/tests/launcher_test.py
cargo build --locked
'@
ssh test-env $AidokuRemoteCommands
```

Formatting fixes, dependency resolution, compilation, and test execution also
belong on the worker. Bring intentional source changes and the resulting
`Cargo.lock` back to the checkout before the final clean run. Do not combine
results from a binary that changed partway through a harness run.

The vendored backend has a separate checked-in lock for its standalone tests;
application builds use the root lock. The Kindle-feature platform tests cover
the background idle worker and scoped TCP interruption without opening a native
framebuffer. Bootstrap tests check transition tokens. These checks complement
the actual firmware sleep/wake run and do not replace it.

## Retained-process sleep checks

The general `-Firmware` harness and the sleep/wake suite have different scopes.
Use the exact build and three-driver commands in
[`SUSPEND_RESUME.md`](SUSPEND_RESUME.md) for the final native sleep matrix and
the read-only database/stock-restoration collector. That report records the
application, fixture helper, launcher, test guard and boot identities.

Keep the emulator's initial screensaver inhibition in place while preparing
files and waiting for the first accepted image. Immediately before a planned
power action, reset stock activity and temporarily clear inhibition using the
explicit test option. Finally restore the original value. Record actual stock
Going/Out events; do not replace them with a fake application signal. The
adversarial coalescing test additionally documents its bounded notification
delivery schedule and rejects watchdog or late-ordering samples.

Acquisition, global power/input actions and release are bound to the same boot
and suite owner. A changed boot invalidates the run. Preserve its failure and
diagnostics rather than sending global cleanup input to a newly started system.
Only a completely closed owned session may release the suite guard.

All of these operations belong to the isolated Aidoku guest. They do not
authorize changes to the shared worker, another project's VM, or a physical
device. Physical panel, RF and every-firmware acceptance remain separate.

## Desktop captures and real hit areas

The CLI can open every main route and save a PNG plus JSON sidecars. This
PowerShell command executes the application inside remote Xvfb:

```powershell
ssh test-env 'cd /root/aidoku-build/source && xvfb-run -a -s "-screen 0 2200x3000x24" /root/aidoku-build/target/debug/aidoku --demo --offline --device pw5 --screen library --data-dir /tmp/aidoku-design-review --capture artifacts/manual/pw5-library.png --exit-after-ms 1800'
```

The output includes `pw5-library.png`, `pw5-library.metadata.json`, and
`pw5-library.state.json`. Metadata distinguishes the declared design size, runtime
logical size, window size, captured size, backend, and scale factor. State records
the route, reader, preferences, persistence paths, download jobs, and selection.
Desktop status readings are simulated; only native device status uses Kindle
interfaces.

`--actions` accepts a JSON array with millisecond timestamps:

```json
[
  { "at_ms": 300, "section": "library", "action": "continue", "value": "" },
  { "at_ms": 700, "section": "pointer", "action": "tap", "value": "309,390" },
  { "at_ms": 1000, "section": "reader", "action": "next", "value": "" }
]
```

Use logical coordinates for `pointer` actions. Supported pointer operations are
`tap`, `press`/`down`, `release`/`up`, and `move`. Press/move/release sequences
exercise holds, swipes, and slider release behavior through Slint. Intent replay
checks application transitions but does not prove that the visual control's hit
area is correct; pointer tests supply that separate evidence. A scripted action
failure produces a nonzero process exit.

Run the Scribe portrait and landscape layouts with `--device scribe`, adding
`--landscape --screen reader` for the two-page reader. A no-front-light profile
checks that unavailable lighting controls are absent. Use a fresh data directory
for baseline screenshots and reuse an intentional fixture directory for restart
and persistence tests. `--import /path/to/Comic.cbz` exercises the real local
import path on the worker.

Chapter batch-selection checks should exercise a real 500 ms hold and verify that
release selects the chapter without opening it. Then test a second selection,
select-all across directory pages, selected downloads, cancellation, and restored
ordinary taps/download icons. Verify next-five, next-ten, unread, and all-chapter
presets against the actual chapter set. Queue checks include individual/global
pause, resume, cancel, the SQL-backed eight-row panel, and restart without
implicitly resuming a deliberately paused job.

Do not lower the hold threshold or weaken an assertion to compensate for a slow
main thread. Reader/menu and detail holds must exercise raw pointer input events
as well as action replay. Inspect whether hidden-page image work, cache
variant churn, background progress, or decoding blocks event delivery.

## Download and cache performance

Use the method and final recorded results in
[`DOWNLOAD_PERFORMANCE.md`](DOWNLOAD_PERFORMANCE.md). The benchmark executable
must be built from the same frozen library sources as the application candidate.
A development-profile result, an exploratory throughput percentage, or a run
from an earlier source hash is not the final optimized release result.

From an independent remote snapshot, the release benchmark can be launched through
PowerShell as follows. Choose a new output directory for each run:

```powershell
$AidokuPerformanceCommands = @'
set -eu
. /root/.cargo/env
cd /root/aidoku-build/source
export CARGO_TARGET_DIR=/root/aidoku-build/target CARGO_BUILD_JOBS=2
cargo build --locked --release --no-default-features --example download-benchmark
python3 scripts/benchmark-downloads.py --binary /root/aidoku-build/target/release/examples/download-benchmark --build-profile release --output artifacts/performance/final-unique-run
'@
ssh test-env $AidokuPerformanceCommands
```

Keep fixture bytes, chapter/page counts, authentication, artificial latency, build
profile, hardware, and cache state identical between serial and concurrent
trials. Record source and binary hashes, wall/CPU time, native child-process RSS,
HTTP request/byte counts, connection reuse, and peak concurrency. Throughput alone
does not validate correct or complete downloads.

Required regression evidence includes identical-request singleflight, header
identity isolation, foreground admission while two background responses are
blocked, reader/download canonical-path sharing, zero-HTTP completed cache hits,
missing-page-only restart, paused duplicate batches, global queue controls,
low-space behavior, pins/leases under LRU pressure, and worker-stop ordering.
Processed-image tests cover option/content invalidation, corrupted PNG fallback,
vertical geometry restoration, and continued offline reading when only the
disposable derivative cannot be written. Cover-alias restart recovery and active
route-only UI projection need their own checks.

The 256 MiB volatile budget excludes pinned download content; the 64 MiB reserve
is an admission policy rather than a guarantee against external disk writers.
Tests must distinguish managed cache eviction from user/external-file deletion.
Orderly teardown should record whether the worker acknowledged completion after
its SQLite connection closed. Do not equate the app's two-second acknowledgement
wait or the launcher's five-second TERM grace with proof that SIGKILL and every
filesystem failure are lossless.

## Native ARM and firmware checks

The remote ARM packaging command is:

```powershell
ssh test-env 'cd /root/aidoku-build/source && . /root/.cargo/env && CARGO_TARGET_DIR=/root/aidoku-build/target sh scripts/build-kindle.sh'
```

It stages the ARM executable, launcher, KUAL entries, notices, and a compatible
document-script entry. `scripts/package-kindle.py` creates the ZIP and a SHA-256
payload manifest, rejects non-ARM binaries, and excludes runtime data.

The firmware harness drives QMP touch input through the guest kernel and native
evdev backend. It records QMP framebuffer screenshots, application snapshots,
exported state, binary identity, actual geometry, model profile, firmware, and
per-case results. The application and framebuffer captures observe different
paths and must not be assumed pixel-identical. Logical action replay alone is
not a native touch test.

## What each result establishes

| Evidence | Can establish | Cannot establish |
| --- | --- | --- |
| Rust and HTTP-fixture tests | Persistence, import, source ABI, provider request/response behavior, decoding limits | Availability or authentication of a live provider |
| Desktop captures and pointer tests | Layout, touch hit areas, navigation, state restoration, deterministic design comparison | Framebuffer/evdev integration or physical panel quality |
| ARM build and package inspection | Target binary and staging/package consistency | Successful execution on a particular model |
| Firmware emulator | Native ABI, framebuffer, touch path, launcher behavior in that firmware environment | Physical ghosting, flicker, contrast, refresh latency, battery use, radio behavior |
| Named physical model and firmware | The specific hardware behaviors actually exercised and recorded | Compatibility with untested models/firmware |

Real-account Komga/Kavita/Suwayomi connections, current MangaDex availability,
third-party `.aix` packages, browser challenges, tracking services, and physical
sleep/wake need their own recorded acceptance. Never infer those passes from
offline demo data, a sibling project's historical report, or a zero-error build.
