# Kernel-assisted input discovery

Date: 2026-11-17, Asia/Shanghai.

Five paired Scribe launches reduced median software readiness from **4.070 s
to 2.830 s** and first Library-frame SEND acceptance from **4.440 s to 3.230 s**.
The candidate was faster in every measured pair. Both variants were launched
from isolated temporary installations; the daily-use application was preserved.

## Implementation

The new bounded discovery module reads kernel metadata without opening every
evdev device. Portrait discovery shortlists recognized names; touch discovery
shortlists the required absolute axis. Each hint is bound to the character
node's major/minor number, filesystem and inode. A selected file descriptor
must still pass the original evdev capability checks, and the portrait name
or complete touch axis bitmap must agree with the hint. The complete bounded
roster is observed again before accepting the result.

Missing, malformed, incomplete or changed metadata, an empty shortlist, or a
failed descriptor check returns to descriptor-based scanning. Multiple valid
portrait sources remain ambiguous. The touch fallback retains its existing
handling of linked event nodes. The selected touch descriptor transfers directly
to its owner, avoiding the previous close/reopen cycle.

No input device number is hardcoded. No pose or device roster is cached across
calls. Fresh portrait reads, input grabs, event synchronization, coordinate
conversion, lifecycle handling and display ownership barriers remain in place.

Implementation: [discovery metadata](../../../vendor/slint-backend-kindle/src/input_discovery.rs),
[portrait selection](../../../vendor/slint-backend-kindle/src/portrait_pose.rs),
and [touch ownership](../../../vendor/slint-backend-kindle/src/touch.rs).

## Measurements

The baseline is main at `e141d4479ae6440e2ba3f7b93d8f0e616458055f`, including
the previously accepted font/progress improvements. It is the prepared main
binary, not the older application installed for daily use on the device.

There was one warmup per variant, followed by five rounds in AB, BA, AB, BA, AB
order. Both variants used the same Scribe boot, native appmgr/Scriptlet route,
launcher, isolated offline library snapshot and compiler configuration. No
reboot or cache eviction occurred. All 12 launches completed and restored
native control; the two warmups are excluded from the following statistics.

| Metric | Baseline median | Candidate median | Reduction |
| --- | ---: | ---: | ---: |
| Native launch request to software ready | 4.070 s | **2.830 s** | 1.240 s / 30.5% |
| Native launch request to first Library SEND | 4.440 s | **3.230 s** | 1.210 s / 27.3% |

| Round | Baseline ready | Candidate ready | Baseline Library SEND | Candidate Library SEND |
| --- | ---: | ---: | ---: | ---: |
| 1 | 4.150 s | 3.100 s | 4.530 s | 3.500 s |
| 2 | 4.170 s | 3.010 s | 4.560 s | 3.360 s |
| 3 | 4.070 s | 2.830 s | 4.440 s | 3.230 s |
| 4 | 4.010 s | 2.820 s | 4.390 s | 3.200 s |
| 5 | 4.060 s | 2.790 s | 4.440 s | 3.160 s |

The reductions occur in the phases containing the repeated input discovery:

| Phase | Baseline median | Candidate median |
| --- | ---: | ---: |
| Launcher portrait-calibration interval | 950 ms | 700 ms |
| Backend installation after platform initialization | 360 ms | 83 ms |
| Event-loop entry to initial startup scene | 1,003 ms | 441 ms |

These are surrounding phase intervals, not isolated syscall measurements.
Independently calculated medians must not be added as one execution timeline.
The launcher has one portrait discovery, backend installation has another,
and the event loop opens the portrait observer and touch input. Avoiding
unnecessary evdev closes removes work at each of these sites. Metadata and
font preparation also varied between runs; no additional optimization of those
components is claimed here.

The candidate's remaining pre-exec interval has a 1.530 s median, including
700 ms in the launcher portrait-calibration interval. That launcher/native
handoff is a useful next profiling target. Its restoration and ownership
checks should be measured individually before changing them.

Full observations, warmups, ranges, input nodes, memory samples, source hashes
and native refresh records are retained in [startup-ab.json](startup-ab.json).
The largest measured ready reduction is 1.270 s; the smallest is 1.050 s.

## Verification

All builds, tests, package checks and runtime operations ran through `test-env`.
Cargo concurrency was six. The release overrides match the measured baseline:
`CARGO_PROFILE_RELEASE_LTO=false` and
`CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16`, with the Kindle feature and
`armv7-unknown-linux-musleabihf` target. The compiler fingerprint is unchanged.

| Check | Result |
| --- | --- |
| Kindle backend unit tests | 189 passed, including nine new discovery tests |
| Application startup/lifecycle/font tests | 22 passed |
| Changed Rust files formatting | Passed |
| ARM static executable and package | 182 package checks passed |
| Native warmups and measured launches | 12 succeeded |
| Actual input descriptors at first Library SEND | Same event2 and event4 nodes in all 12 launches |
| Final Library frames | Identical pixels below the top 100 status pixels |
| Native state, lighting, installed files and library | Restored or unchanged; temporary staging removed |
| Shared Kindle lease | Released with owner/token checks |

The new tests cover both kernel bitmap word sizes, malformed and oversized
metadata, missing metadata on skipped nodes, device-number mismatches,
added/removed/relabelled/replaced nodes, descriptor identity, and open flags.
Existing portrait ambiguity, input synchronization and lifecycle tests remain
in the suite. Host compilation initially encountered incomplete inherited
Slint/postcard artifacts; package-scoped rebuilds in this task's own cache
resolved them before the successful results above.

See [verification.json](verification.json), [backend tests](backend-tests.log),
[application tests](application-tests.log), [package verification](package-verification.json),
[final-frame comparison](native/final-library-comparison.json), and
[lease release](native/lease-release.json).

The full real-library captures and raw device records remain private. Public
[baseline](native/baseline-navigation.png) and
[candidate](native/candidate-navigation.png) crops show only navigation. Their
original frame hashes and crop geometry are in [captures.json](native/captures.json).
The existing UI/UX baseline is unchanged.

## Source and artifact identity

| Input | SHA-256 |
| --- | --- |
| Baseline application | `81302b133f196cd0324adf0a2c06b996c503511d9be3404b4127dc45de4ab9a8` |
| Candidate application | `625ca3c1148c3c9dd99e90dc6762575acac27442f55305f4f00cec2d8efca352` |
| Shared launcher | `02b20bc6c1f10f0afff5fc4c9802288ee43ac72f1647024bc316dc13b8c3a2c0` |
| Candidate production source | `a439e402cc3afe74443eeede0a240f8c574468f105118dbd698325f4cdd77fba` |

The candidate executable is 53,343,424 bytes, an increase of 13,808 bytes.
[candidate-build.json](candidate-build.json) binds it to the compiler settings
and [complete production source manifest](candidate-source-manifest.json).
[delivery.json](delivery.json) identifies the verified development ZIP,
including the existing official Aidoku logo. This experiment retains the code
on `codex/startup-input-discovery`; it does not merge or deploy that candidate.

## Measurement limits

This is one physical Scribe, five measured warm launches per variant. Logger
timestamps have 10 ms resolution and scheduling delay. Software readiness and
driver SEND acceptance do not measure physical tap latency or optical panel
completion. The first Library frame can precede completion of background cover
images. This batch does not newly qualify physical touch, rotation, sleep/wake,
cold boots or other Kindle models.
