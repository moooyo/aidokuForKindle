# Empty-input lookup and final startup-label experiment

Date: 2026-11-17, Asia/Shanghai.

> Build-audit correction: the original candidate binaries below contained
> diagnostic backend logging inherited from a stale Cargo dependency. Keep
> this record as historical evidence; use the [audit and clean remeasurement](../startup-native-query-20261117-4e06926/BUILD_AUDIT.md)
> for authoritative source identity and performance conclusions. The code
> change is retained, and the clean comparison confirms a small improvement.

The retained candidate makes a modest improvement. Across ten paired warm
Scribe launches, median software readiness changed from **2.810 s to 2.695 s**
and first Library SEND acceptance from **3.185 s to 3.045 s**. The candidate
was faster in eight of ten pairs for both endpoints. Two slower pairs remain
in the results; this is not a claim that every launch improves.

Implementation commit: `6b7765fa13ed3e5b3bd07c0df9006e755ed21973` on
`codex/startup-input-feedback`, based on main at
`4ce1fd156a7a4a75ca2fda5f4324991d6b02ce92`.

## Retained changes

1. Empty, punctuation-only, numeric-only and non-ASCII-only compositions return
   no candidates before accessing the bundled pinyin dictionary. The existing
   lookup and normalization remain responsible for compositions containing
   ASCII letters. Candidate order, selection and pagination are unchanged.
   This avoids building the 65,105-entry dictionary merely to synchronize a
   hidden keyboard on the Library page.
2. The loading scene keeps its current label until the business view is ready.
   It no longer publishes a separate attachment-stage label immediately before
   Library presentation. The initial scene, activity timer, resource-wait
   feedback, font activation timing and native input barriers remain unchanged.

Code: [input lookup](../../../src/input.rs) and
[startup coordinator](../../../src/bootstrap_startup.rs).

Dictionary initialization is deferred to the first nonempty composition. It
has not been moved to a background worker; physical first-keystroke latency
was not newly qualified. Host replay verifies the resulting candidate list
and keyboard appearance, and the input regression suite verifies behavior.

## Final matched results

The two final batches use exactly the same candidate, baseline, compiler
configuration and launcher. They share the same device boot and unchanged
original library. Each batch starts with one warmup per variant; ten measured
AB/BA pairs follow in total. The second batch continues at round 6, balancing
which variant starts first across all ten pairs. All 24 launches succeeded.

| Endpoint | Baseline median | Candidate median | Reduction |
| --- | ---: | ---: | ---: |
| Native request to software ready | 2.810 s | 2.695 s | 115 ms / 4.1% |
| Native request to first Library SEND | 3.185 s | 3.045 s | 140 ms / 4.4% |
| Rust main to software ready | 1.252 s | 1.142 s | 110 ms |
| First view synchronization | 311 ms | 184 ms | 127 ms |

The mean whole-launch reductions were 85 ms for software ready and 121 ms for
first Library SEND. Baseline first-SEND observations ranged from 3.060 to
3.520 seconds; candidate observations ranged from 2.880 to 3.510 seconds.
Launcher and runtime variation remains significant relative to this gain.

| Round | Baseline ready | Candidate ready | Baseline SEND | Candidate SEND |
| --- | ---: | ---: | ---: | ---: |
| 1 | 3.110 s | 2.790 s | 3.520 s | 3.220 s |
| 2 | 2.820 s | 2.690 s | 3.200 s | 3.090 s |
| 3 | 2.730 s | 2.700 s | 3.100 s | 3.000 s |
| 4 | 2.800 s | 2.600 s | 3.170 s | 2.880 s |
| 5 | 2.830 s | 3.080 s | 3.220 s | 3.510 s |
| 6 | 2.880 s | 2.960 s | 3.260 s | 3.350 s |
| 7 | 2.800 s | 2.640 s | 3.170 s | 2.940 s |
| 8 | 2.680 s | 2.640 s | 3.060 s | 2.920 s |
| 9 | 2.770 s | 2.640 s | 3.150 s | 2.940 s |
| 10 | 3.040 s | 2.870 s | 3.450 s | 3.240 s |

Pre-ready native updates fell from four to three in every measured final
sample. The much larger reductions in individual discarded display waits
did not translate into equally large whole-launch savings. CPU work and
display consumption overlap, so component durations must not be added as
independent improvements.

Full timings, warmups, input-device identities, memory snapshots and native
refresh parameters are in [final-startup-ab.json](final-startup-ab.json).

## Discarded attempts

Two earlier candidates were measured independently, with five pairs each.
Their source diffs, full observations and source/build identities are retained.
They are excluded from the final candidate's ten-pair distribution because
their implementations differ.

| Candidate | Behavior | Software-ready median | First Library SEND median | Decision |
| --- | --- | ---: | ---: | --- |
| Attempt 1 | Skip empty dictionary work, reset the activity clock, gate stage labels, and defer family activation until ready | 2.830 to 2.750 s | 3.210 to 3.310 s | Not adopted |
| Attempt 2 | Keep original family activation but retain the broader feedback coalescing | 2.750 to 2.620 s | 3.130 to 3.120 s | No meaningful first-SEND improvement established |
| Retained candidate | Skip empty dictionary work and only remove the final attachment label | 2.810 to 2.695 s | 3.185 to 3.045 s | Retained as a modest improvement |

Both broader attempts reduced pre-ready updates to one, but that count alone
was an insufficient performance criterion. Their exact scheduling and rendering
effects were not isolated further. The final implementation restores the
original preparation-feedback and font paths and makes the narrower change.

Evidence: [attempt 1](attempt-1/startup-ab.json),
[attempt 1 diff](attempt-1/source.patch),
[attempt 2](attempt-2/startup-ab.json),
[attempt 2 diff](attempt-2/source.patch).

## Verification

All builds, tests, rendering, capture and verification ran through test-env,
with six Cargo jobs. ARM build settings match the measured main binary:
Kindle feature, `armv7-unknown-linux-musleabihf`, release LTO=false and
codegen-units=16. The compiler fingerprint is unchanged.

| Check | Result |
| --- | --- |
| Input regression tests | 13 passed, including two new lookup regressions |
| Startup/font/lifecycle binary tests | 22 passed |
| Package verification | 182 checks passed |
| Scribe and PW5 Library captures | Pixel-identical to their baseline |
| Scribe and PW5 real pinyin replay | Pixel-identical for composition `tushuguan`, with nonempty candidates |
| Metadata held for 2.2 seconds | Five feedback pulses after fonts completed; none after attachment |
| Cancellation during preparation | No ready publication; shutdown completed |
| Final-candidate native runs | All 24 completed and restored native control |
| Native final Library body | All 24 pixel-identical below the top 100 status pixels |
| Shared physical device | Each acquired lease restored and released with owner/token checks |

The tests inject a failing dictionary resolver for empty and non-letter
compositions, demonstrating that lookup is not requested. Letter compositions
retain the original query text and dictionary results. Existing composition,
candidate paging and Unicode input tests continue to pass.

Two acquisition attempts encountered another task's valid device lease. They
performed no device commands and acquired no lease. All actual device batches
used fresh identities, and only owned staging/runtime directories were removed.
Daily-use application, launcher, cover, entry and original library hashes were
unchanged. Lighting and native state were restored.

See [verification.json](verification.json), [input tests](input-tests.log),
[startup tests](startup-tests.log), [host evidence](desktop/verification.json),
[native frame comparisons](native/final-library-comparison.json),
[lease releases](native/lease-releases.json), and
[package checks](package-verification.json).

## Source and delivery

- Application SHA-256:
  `534ef996da84356018813283c72b1aa9d2d48f87a941a42dee500a66f549e8b9`.
- Production source SHA-256:
  `a0dfc29f3072dbf9d1a462fe5f4959fee650b9e14cc83f539e24bbbd3c9dfeda`.
- The executable is 53,362,784 bytes; the baseline is 53,343,424 bytes.
- [Committed-source verification](committed-source-verification.json) confirms
  the implementation commit matches the tested and packaged production source.
- [Build identity](candidate-build.json),
  [complete source manifest](candidate-source-manifest.json), and
  [delivery identity](delivery.json) bind the candidate ZIP to these inputs.

The package contains the sampled executable and unchanged production launcher,
JPEG worker, official Aidoku cover and notices. It excludes measurement entry
scripts, automatic-exit arguments, private transport data and user fixtures.
The experiment did not replace the daily-use installation or adopt a new
UI/UX baseline. The candidate and its report remain on the experiment branch.

These are warm software/driver observations on one Scribe. They do not measure
optical completion, all-cover completion, cold boot, or newly qualify physical
typing, rotation, sleep/wake, USB takeover or other Kindle models. The more
substantial remaining targets are native status and portrait-query overhead.
