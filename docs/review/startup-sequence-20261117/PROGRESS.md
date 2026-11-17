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
| 2 | Profile and optimize first Library rendering | Investigation in progress; first render is slower after overlap and offsets some of its benefit |
| 3 | Measure and optimize the cover-loading tail | Pending |
| 4 | Consolidate or reschedule the optional sleep-cover capability observation | Pending |
| 5 | Reduce lighting-application process overhead | Pending |
| 6 | Evaluate shared Scriptlet dispatch independently; preserve global scanner and recent-item semantics | Pending |

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
