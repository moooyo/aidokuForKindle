                                          # Startup optimization, first iteration

Date: 2026-11-16, Asia/Shanghai.

Five paired Scribe launches reduced median software-ready time from **5.70 s
to 4.90 s** and median first Library-frame submission from **5.93 s to 5.13 s**.
The candidate submitted the Library frame earlier in every pair. The production
installation remains unchanged; the tested candidate is available as
`dist/aidoku-startup-optimized-20261116.zip`.

## Changes

1. The native backend renders the first real startup scene into a fresh buffer
   and uses it for the initial full refresh. It no longer submits a separate
   blank clear first. An absent draw cannot consume the initial scene request.
   Input remains blocked until the checked full refresh and touch synchronization
   complete, including startup without a portrait calibration.
2. The launcher reads process identity fields with shell builtins. The parent,
   process-group, session, and birth checks use one coherent `/proc/PID/stat`
   observation where possible. Process names containing spaces or parentheses
   are handled. Native-reader completion and observer publication use 20 ms
   polling while retaining their previous sleep budgets and watchdogs. Native
   calibration queries, nonce/generation checks, and ownership checks remain.
3. Font completion can proceed directly to application attachment, avoiding an
   intermediate loading-stage redraw when metadata is already available. Cheap
   attachment stages run together under a 4 ms between-stage budget. A slow
   stage or blocked lifecycle yields; individual stages are not preempted.
4. Opt-in timing gained backend/window/bootstrap-font and attachment/view-start
   markers. Normal startup does not enable recording.

Implementation: [launcher](../../../packaging/kindle/launch.sh),
[native event loop](../../../vendor/slint-backend-kindle/src/platform.rs),
[startup coordinator](../../../src/bootstrap_startup.rs),
[attachment stages](../../../src/app/startup.rs), and
[process timing](../../../src/main.rs).

## Matched A/B results

| Metric | Baseline median | Candidate median | Reduction |
| --- | ---: | ---: | ---: |
| Native open request to software-ready | 5.700 s | 4.900 s | 0.800 s / 14.0% |
| Native open request to first Library SEND acceptance observed | 5.930 s | 5.130 s | 0.800 s / 13.5% |
| Native open request to application exec request | 2.050 s | 1.840 s | 0.210 s / 10.2% |
| Rust main to startup-scene marker | 1.893 s | 1.523 s | 0.369 s / 19.5% |
| Fonts registered to software-ready | 1.213 s | 0.876 s | 0.337 s / 27.8% |

Intervals overlap or use independently computed medians and must not be added
to reconstruct a median whole launch. Full results, ranges, and per-event data
are in [startup-ab.json](startup-ab.json).

| Pair | Baseline first Library SEND | Candidate first Library SEND | Improvement |
| --- | ---: | ---: | ---: |
| 1 | 5.760 s | 4.870 s | 0.890 s |
| 2 | 5.860 s | 5.100 s | 0.760 s |
| 3 | 6.130 s | 5.170 s | 0.960 s |
| 4 | 6.340 s | 5.150 s | 1.190 s |
| 5 | 5.930 s | 5.130 s | 0.800 s |

Native refresh traces show **two initial full GC16 submissions in every
baseline sample and one in every candidate sample** before the startup-scene
marker. This confirms that the redundant clear was removed. It does not
measure optical clearing quality or the physical duration of a waveform.

Font preparation increased from a median 506 ms to 602 ms, and parallel
metadata preparation increased from 87 ms to 188 ms. They begin earlier in the
overall launch. Scheduling or I/O overlap could explain the change, but this
experiment does not isolate its cause. Both whole-launch endpoints still
improve. Preparation timing should remain visible in subsequent iterations.

## Method and scope

- The baseline is the unchanged installed ARM executable and production
  launcher from the previous source-bound delivery. Its production source
  manifest matches the pre-change checkout. Source changes are limited to the
  startup files and the backend patch record, plus the separate cover assets
  delivered previously.
- Both versions use Rust 1.99.1, the Kindle feature set, the same ARM target,
  and matching release-profile fingerprints. The installed build uses
  `CARGO_PROFILE_RELEASE_LTO=false` and
  `CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16`; these overrides were reproduced.
  A default-profile candidate built during setup was excluded from device
  sampling and delivery. See [compiler provenance](compiler-provenance.json).
- The actual `com.lab126.appmgrd` Scriptlet route opens equivalent temporary
  entry scripts outside the indexed documents folder. These invoke isolated
  copies of the production launchers with instrumentation and bounded exit
  arguments. The ordinary document entry is not rewritten.
- Both variants use the same isolated copy of the real six-book library, in
  offline mode. There is no reboot or page-cache eviction. Each device batch
  begins with one warmup per variant. Paired order alternates AB/BA.
- Four complete pairs came from the first measurement batch. Its next baseline
  launch completed successfully, including capture and normal launcher exit,
  but the subsequent single-shot native-service check failed. The precise
  transient was not captured. That valid but unpaired timing remains in the
  data and is excluded from the paired summary. A bounded restoration check
  was added, and a fresh batch supplied the fifth complete pair.
- The two batches used the same boot and the same original program, document
  entry, cover, and library database. Their recorded before/after identities
  match. The full data includes four warmups and the additional unpaired
  baseline launch, without mixing them into the primary distribution.

The main whole-launch endpoints use device `/proc/uptime`, with 10 ms
resolution. A small reader timestamps the existing software-ready stderr line
and the first native refresh acceptance line after it. The latter corresponds
to the first business scene following removal of the loading view; framebuffer
captures confirm the Library navigation. Pipe scheduling adds observation delay.
This measurement includes exec/loading time, unlike the previous report's
sum of shell and Rust-main intervals. The new comparison uses its contemporary
matched baseline, rather than subtracting the historical 6.192 s estimate.

These are software/driver submission observations, not physical tap latency,
optical panel completion, ghosting, battery, cold-reboot, or network-startup
measurements. Five pairs on one Scribe do not qualify other Kindle hardware.
No additional physical sleep cycle or USB-takeover trial was performed; those
paths retain the host lifecycle regression coverage.

## Verification

All builds, tests, rendering, capture, and verification ran through `ssh
test-env`, with six Cargo build jobs. No local verification was run.

| Check | Result |
| --- | --- |
| Application library tests | 797 passed, 5 existing tests ignored |
| Application binary/startup tests | 20 passed |
| Native backend tests | 180 passed |
| Launcher lifecycle tests | 76 passed |
| Candidate development-package verification | 170 checks passed |
| Scribe final desktop Library capture | Pixel-identical to baseline |
| Paperwhite 5 final desktop Library capture | Pixel-identical to baseline |
| Native Library navigation pixels | Pixel-identical in all five pairs |

The added launcher regression sets a real fixture process name containing
spaces and parentheses, then checks retained sleep/wake ownership and cleanup.
This exercises the new process-field parsing through the lifecycle.

Scoped formatting checks pass for the changed Rust files. The initial full-tree
format check reported pre-existing formatting differences in unrelated files;
those files were not reformatted. Several incomplete inherited host build-cache
entries were rebuilt before the successful test runs. Completed host build
cache files created by this task were removed when worker disk space became low.

The [desktop captures and comparisons](desktop/verification.json) retain their
original image/state/metadata sidecars. [Native navigation evidence](native/verification.json)
uses an exact 180-degree pixel rotation to make this run's raw scanout readable,
followed by a navigation-only crop. Full unmodified native captures remain
private. These are framebuffer reads following the first content submission,
not optical or atomic display photographs. Existing visual baselines are frozen
and were not changed.

## Device ownership and failed setup attempts

Every device-operation batch used a fresh lease identity and token. All owned
leases were released. Normal native control and original lighting were restored,
and temporary device staging/runtime directories were removed. The installed
application, launcher, cover, document entry, and user library remain unchanged.

An initial read-only preflight encountered a disconnected device and performed
no device writes. After the user reconnected it, the first harness setup placed
a FIFO on Kindle's FAT user storage, which rejects that file type. It failed
before application launch. Restoration helpers also needed correction before
that owned batch was closed. Those attempts produced no benchmark samples and
their evidence is retained. The corrected harness uses a private `/tmp`
directory for pipes and keeps analysis/tool repair outside device ownership.
See [batch outcomes](batches.json) for the closed attempts and measurement runs.

## Candidate artifact

The source-bound ZIP contains the exact sampled candidate executable and
production launcher, the unchanged JPEG worker from the verified earlier
package, the Library cover, and the complete package notice set. Instrumented
launchers, automatic exits, test data, capture tools, private transport files,
and runtime credentials are not included.

- Application SHA-256:
  `019c3f005362e20f02b48abefe740561fbfb9af120336148c3d199ee6f22e030`.
- Launcher SHA-256:
  `02b20bc6c1f10f0afff5fc4c9802288ee43ac72f1647024bc316dc13b8c3a2c0`.
- ZIP SHA-256:
  `a78c9c380bd878ffab9f44418e7006eafe458afca7a2a0ef261ad18f25783b9b`.

See [delivery identities](delivery.json), [source manifest](source-manifest.json),
[package verification](package-verification.json), and
[test/build verification](verification.json). The candidate was exercised in
isolated device homes and has not replaced the normal installed application.
