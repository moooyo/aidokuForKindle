# Sequential startup performance work

Base: `f56dd36657bf77b8652ed7808f1c4a0927dcd8ba`.

Each item receives an independent control/candidate comparison. A change is
retained only when its measured benefit and correctness justify it. Superseded
or unsuccessful experiments remain in the evidence. All verification runs on
test-env, with six Cargo jobs and fresh device leases for continuous batches.
Project and vendored backend packages are force-rebuilt when switching ARM
source variants; production binaries must not contain diagnostic-only logging.

| Order | Work | Status |
| --- | --- | --- |
| 0 | Merge and deploy the verified native-query version; establish consistent installed/runtime and complete-screen timing | Main merged and pushed; verified installation completed; frame/cover instrumentation in progress |
| 1 | Overlap preparation with the initial full-frame completion wait while preserving input and display ownership | Retained after five A/B rounds; first Library median improved by 109 ms, software ready by 228 ms |
| 2 | Profile and optimize first Library rendering | Retained after five A/B rounds; first Library improved by 134 ms, covers settled by 286 ms |
| 3 | Measure and optimize the cover-loading tail | Retained both derivative reuse (2.466 s) and independently tested completion/publication scheduling (589 ms) |
| 4 | Consolidate or reschedule the optional sleep-cover capability observation | Standalone helper trial rejected; combined portrait session retained after five A/B rounds (125 ms first-frame saving) |
| 5 | Reduce lighting-application process overhead | Retained as a small change: 26 ms lower attachment median, confirmed native value readback |
| 6 | Evaluate shared Scriptlet dispatch independently; preserve global scanner and recent-item semantics | Diagnostic entry prepared; native comparison pending |

Timing distinguishes native launch requests, first business-frame submission,
checked frame completion, software readiness, and complete visible-cover
publication. Physical finger-to-photon timing requires a separate observation;
programmatic requests and framebuffer reads must not be presented as optical
measurements. Capture dwell, image encoding and shutdown are excluded from
normal startup endpoints.

The verified daily installation was updated in batch `deploy-native-e3956210`.
Application SHA-256: `3833d6ec12c2462c29278fdca5a3451e0c62d55358de39c3aed3ce1b6a3a0bb9`.
Native helper SHA-256: `b91c2de2d9d1ca1ae7806d0ee6cc78f0f9a3d8a84feae081255d0fcbdb105e16`.
Launcher SHA-256: `02b20bc6c1f10f0afff5fc4c9802288ee43ac72f1647024bc316dc13b8c3a2c0`.
The library, device profile, document entry and cover hashes were unchanged;
native state was restored and the lease was released. Original program files
remain in the owned device and private host deployment backups.

## Item 1: overlap preparation

Batch `step1-overlap-50c06aa6` used the same executable for both variants.
The control added `--startup-serial-preparation`; the candidate began byte and
metadata preparation after the first successful full SEND, before the existing
checked completion wait. UI registration/attachment, pose validation, contact
synchronization, pause, and cancellation gates were retained.

| Endpoint from native launch request | Serial median | Overlap median | Saved |
| --- | ---: | ---: | ---: |
| Software ready | 2.479 s | 2.251 s | 228 ms |
| First Library accepted frame | 2.711 s | 2.602 s | 109 ms |
| First Library software input readiness | 2.711 s | 2.602 s | 109 ms |
| Visible-cover settled accepted frame | 5.953 s | 5.886 s | 67 ms |

Three of five complete-start pairs favored overlap. Process-entry-to-Library
medians were 1.295 and 1.194 s. The first render rose from 182 to 303 ms,
offsetting part of the 228 ms readiness saving. The cover tail remained about
3.2 s, and all measured runs published all seven visible slots (six unique
books), with zero cover failures. These are warm offline software/driver
endpoints, not physical finger-to-photon timing or network performance.

Primary runs do not capture PNGs, read framebuffer snapshots, or inspect live
process files. One warmup per variant precedes five predetermined AB/BA rounds;
two separate capture runs follow them. The initial serial warmup reached its
eight-second observation limit with two covers still pending; it is retained as
a censored warmup, never counted as a complete-cover result. Every measured run
and both later qualification runs completed cover publication. Existing cover
aliases and their artifacts were copied into the isolated fixture. The daily
library and installed files were unchanged, native state and lighting were
restored, and the fresh lease was released.

Remote validation passed 806 library tests (five pre-existing ignores), 22
startup tests, and 190 backend unit tests. The standalone backend documentation
example does not compile without its illustrative fonts and generated UI; this
pre-existing example is recorded separately from the passing unit tests.
Scribe and PW5 Library captures and real pinyin replays were pixel-identical to
the control. Slow metadata retained feedback; cancellation never became ready.

Evidence: [all A/B observations](step1-overlap.json) and
[host verification](step1-host-verification.json). Private raw captures, traces,
launcher output, source archive, and lease proofs remain on test-env under the
matching batch directory. Application SHA-256:
`383eb94db3b3beb94118dcd87ff351b2f7ae84d9dfe796ff1aa0c3eeae1760e8`.

## Item 2: prioritize the first Library render

Batch `step2-render-priority-8399bcac` used one executable, with
`--startup-eager-covers` selecting the previous scheduling. The candidate queues
the same covers during attachment but admits workers after an active business
frame is accepted. A queued UI turn returns through the existing lifecycle gate;
the callback itself changes no UI models. Other routes, resume, and desktop
startup retain their existing admission behavior.

| Endpoint from native launch request | Eager median | First-frame priority median | Saved |
| --- | ---: | ---: | ---: |
| Software ready | 2.300 s | 2.270 s | 30 ms |
| First Library accepted frame | 2.630 s | 2.496 s | 134 ms |
| Software input readiness | 2.630 s | 2.496 s | 134 ms |
| Visible-cover settled accepted frame | 6.028 s | 5.741 s | 286 ms |
| First Library render duration | 322 ms | 202 ms | 120 ms |

All five first-frame pairs and all five cover-settled pairs favored the
candidate. Thus the scheduling improvement did not merely transfer a wait to
the cover endpoint. Fourteen cover tests, including closed admission and sleep
gating, passed remotely. Both profiles' final Library and keyboard captures
were pixel-identical; slow/cancelled metadata checks also passed. All fourteen
device launches completed, all measured covers loaded, and restoration/lease
checks passed. The two warmups used a longer twelve-second observation window.

Separate cover milestones show repeated full validation of local source images
before consulting the already rendered derivative. In a representative
candidate run, six raw validation intervals total about 2.7 s; one is 1.67 s.
The derivative-render and texture-preparation intervals are much smaller. This
is the next optimization target; source-byte identity checks must be preserved.

Evidence: [all A/B observations](step2-render-priority.json),
[host verification](step2-host-verification.json). Application SHA-256:
`f71846b0e233483f1efa06306739b2e8459a9de714f7842f2ec4e09ea52295e7`.

## Item 3: reuse proven local cover derivatives

Batch `step3-cover-cache-6dca06d2` compared item 2 with source-validated local
derivative reuse. The source remains required, bounded, and freshly hashed.
A derivative hit must match those bytes and the complete rendering key; its
processed PNG is validated. Full original-image codec validation moves to the
cache-miss branch. Cold rendering retains the previous validation, byte-proof,
resource limits, and decode behavior. Network covers are unchanged.

| Endpoint from native launch request | Previous median | Candidate median | Saved |
| --- | ---: | ---: | ---: |
| First Library accepted frame | 2.523 s | 2.525 s | -2 ms (no established change) |
| Visible-cover settled accepted frame | 5.861 s | 3.395 s | **2.466 s** |

All five complete-cover pairs improved. A representative six-cover run fell
from 2.833 s of worker preparation to 257 ms, with all six derivatives reused.
All seven visible image slots loaded, with no failures. Both separate native
captures match pixel-for-pixel outside the live status bar. Fourteen cover
tests and 44 render-cache tests passed (one existing ignored benchmark).
New regressions cover a reopened cache, missing/malformed/changed source bytes,
and corrupt derivative rebuilding. Both desktop profiles and keyboard replays
remained pixel-identical; slow startup and cancellation checks passed.

Evidence: [five A/B pairs and all warmups](step3-cover-cache.json),
[host checks](step3-host-verification.json), and
[native qualification](step3-native-qualification.json). All fourteen device
launches completed and the owned state and lease were restored/released.
Application SHA-256:
`817023e92ac245872ebd88482e8db273fe173bcd3f0d9270eb5f91ed7f0e7aa0`.

### Item 3 follow-up: prompt completion, coalesced initial publication

Batch `step3b-cover-events-53aae7d9` kept the derivative fix on both sides.
Visible-cover completion now uses the existing coalesced job-ready wake path,
so the next single cover worker does not wait for a 50 ms maintenance tick.
After the first Library SEND, initial cover publication uses the existing
300 ms background interval to collect results before drawing. Foreground
interaction and lifecycle processing retain their normal priority.

The complete-cover median improved from **3.515 to 2.926 s (589 ms)**. All five
pairs improved. The Library frame count through settled covers fell from
`4,4,5,4,4` to `2,2,2,2,2`: the first content frame and one completed-cover frame.
First-render medians remained similar (218/214 ms); the apparent 69 ms first-SEND
change tracks pre-render variation and is not claimed as a rendering saving.

Fourteen cover tests, four source-pool tests, slow/cancelled preparation checks,
and both profiles' Library/keyboard pixel comparisons passed remotely. The
native final captures match outside the live status bar. All measured visible
slots contain images, with no failures; all fourteen runs and restoration
checks passed and the lease was released.

Evidence: [A/B observations](step3b-cover-events.json),
[host checks](step3b-host-verification.json), and
[native qualification](step3b-native-qualification.json). Application SHA-256:
`687344c73c6bfbbe3ff7dc4ca3e51d6e297354fcb0c311bc76f6798bdc3e3d63`.

## Item 4: consolidate the supervised sleep-capability observation

The initial standalone-helper trial did not reduce the target interval: both
medians were 150 ms. Its 42/51 ms whole-start medians are not sufficient evidence
of a real improvement in that operation. That implementation is not retained.
See [all observations](step4-standalone.json) and its
[preserved patch](step4-standalone.patch).

The retained implementation reads the same blanket module list inside the
existing supervised startup portrait transaction, before its critical fresh
state observations. An explicit `--startup-session` / `AQ2` negotiation enables
the ninth read-only opcode. The original `--session` remains `AQ1` with eight
opcodes, including compatibility with older parents. An older or missing helper
uses the original CLI read inside the owned transaction, before calibration;
unsupported sessions never receive opcode 9. Native property failure or unknown
modules deny the optional capability. A malformed connected transport still
fails calibration rather than publishing an uncertain portrait.

The publisher returns the capability bit only after successful typed record
publication, clean session finish, and ownership revalidation. The launcher
caches it for its managed session. Original portrait output has an explicit
legacy-parser fallback; malformed combined output cannot take the display.
The byte bound, allowlist, advertisement rejection, passcode checks, sleep and
wake ownership boundaries remain intact.

Batch `step4b-combined-probe-ae530933` used one ELF/helper and two launchers:

| Endpoint | Separate observation | Combined transaction | Saved |
| --- | ---: | ---: | ---: |
| First Library accepted frame | 2.519 s | 2.394 s | 125 ms |
| Visible-cover settled accepted frame | 2.931 s | 2.825 s | 106 ms |
| Independent capability interval | 120 ms | None | Removed |
| Portrait transaction | 260 ms | 280 ms | -20 ms |

All five pairs improved the combined capability-plus-calibration interval.
Three of five complete-start pairs improved amid unrelated dispatch variation.
The bundled property read took 4.9-10.4 ms. Every calibration used one session,
with six replies in the control and seven in the candidate; every capability
decision was supported. Both final native captures match outside the status bar.

Remote validation passed nine transport tests, ten presentation tests, 22 startup
tests, and 109 packaging/lifecycle tests. All fourteen native launches completed;
owned state was restored and the lease released. Evidence:
[A/B observations](step4b-combined-probe.json),
[native qualification](step4b-native-qualification.json).
Application: `ca3b9b01b7586be7d3541b4567311020f604a31a9d0fa03436a082a3256a8c38`.
Helper: `1216411f90fd5174cbee3a746af917058a4d6f949664642ca83881ad9b6818f5`.
Launcher: `130413e24459e6ab55cad21c3e7a4b92c5f24be477ab51485417f83119baec79`.

## Item 5: apply lighting through one bounded helper command

The dedicated `--apply-lighting` command accepts only brightness and warmth,
each either unchanged or an integer from 0 to 24. It opens one handle and
acknowledges only successful completion of every requested assignment. The
read-only AQ1/AQ2 protocols retain their opcode and version boundaries.
The existing 700 ms command deadline and child cleanup apply. Unsupported,
failed, or malformed helper responses reapply the exact requested values with
the existing idempotent CLI setters. Failed assignment never updates the
application's applied-values cache; unsupported hardware and unchanged values
remain skipped.

Batch `step5-lighting-cafcb00b` used the same helper and combined-probe launcher
on both sides. The application attachment median fell from 70.2 to 43.8 ms
(26.4 ms). First Library medians were 2.284/2.258 s and complete-cover medians
2.695/2.667 s. Three of five complete-start pairs improved. This is a small
blocking-work reduction; the end-to-end difference is not large relative to
dispatch variation. Every candidate trace acknowledged the helper assignment.

Separate native readback confirmed brightness 12 and warmth 8 for both variants,
matching their unchanged preferences. Final native captures match outside the
status bar. All fourteen launches completed; original lighting/native state and
installed data were restored/unchanged and the lease was released.

Remote all-target validation passed 931 Rust tests, with five pre-existing
ignored tests. Eleven helper tests passed, including out-of-range arguments,
single-property changes, and failure after the first setter. The prior 109
launcher/package tests still cover the unchanged launcher.
Evidence: [A/B observations](step5-lighting.json),
[readback and native pixels](step5-native-qualification.json).
Application: `5f7fdd5010ee91bb9ac56cf37231dd08497ca2652c81b33b859c241a105e03f3`.
Helper: `84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d`.
