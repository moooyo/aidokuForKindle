# Reader end-to-end qualification results

Dates: 2026-11-10 through 2026-11-11, Asia/Shanghai.

Status: the corrected r2 host comparison completed and independently qualified
all 90 measured processes. Physical native producer qualification and a bounded
manual touch/retained-sleep sequence also completed. A user-reported bookshelf
flash was fixed in the launcher and the user confirmed both transitions clear
in a later bounded manual batch. Full native timing remains separate and pending.

## Compared inputs

The implementation commit is `4c572ee90355572aa2e9ee631cff829e8a61f1e3`,
merged and pushed to main. The source-identity fixture/tool commit is
`7d63a664f326ffb9dce4e670573e8cf1c946727a`; it does not change runtime code.
Its 252 runtime, UI, asset and production-manifest inputs matched the remote
candidate build inputs byte for byte. The baseline is
`ffb341741930fea525b96474a81afe56be25ab17` plus the reviewed observation patch.
The baseline keeps its original image conversion, resize, cache behavior, RGB
scene and constant update marker. The changes under comparison are therefore
the complete optimization delivery, not a single isolated algorithm.

The observation patch SHA-256 is
`6beb7944e4ff8d767e61fa320e121891a4fb9a23a4270d14c2b724dabeffc1bf`.
Root, application and native adapter reviews found no optimization transplanted
into that baseline. Both source trees use byte-identical timing and frame
observer modules. The shared observation contract SHA-256 is
`897736512ced68227c90b2a546e15442bb1d878637170c1e3271f447a84ae51b`.

| Release artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Candidate ARM | 49,248,360 | `8ad8d0452764cd166656ef1c1dbfe64b7830e7e06c09120bb1b83068caad97f2` |
| Baseline ARM with observations | 49,156,824 | `ffae4052306a1bca26ea6a72ff5c9d5e5fb9591475bee79c53bef76eb0c32fa4` |
| Candidate desktop | 59,813,320 | `8e08bc956f554c0a074e75cc98e71d5833e2ff9016225321199fce94b2ddb4fb` |
| Baseline desktop with observations | 59,811,784 | `faa9a995254f6fdeb669b1b57bf2c2ad99f2584649c60c2050f5a87fd9d20022` |

## Completed remote verification

All execution and byte/hash validation uses `ssh test-env`; Windows is used for
source editing, ordinary transfer and reading records. Cargo concurrency is six.
No benchmark runs concurrently with builds, host UI suites or bulk transfers.

| Check | Result |
| --- | --- |
| Default library | 676 passed; two opt-in benchmarks ignored. |
| Default binary and example targets | Three binary and 95 example tests passed. |
| Kindle-feature library | 689 passed; two opt-in benchmarks ignored. |
| Native backend library | 108 passed on the unchanged native component source. |
| Final Python trace/fixture/process-group tests | 59 passed. Earlier 47/52/56-test runs remain preserved with their own inputs. |
| Follow-up UI | Thirteen passed. |
| Strict r2 producer qualification | 32 actual process traces passed, including session/retained behavior and both implementations' recovery cases. |
| Identity-stable workload pilots | Ten passed, including actual disk/cold/memory cache gates and all planned actions. |
| Release builds | Both desktop and both ARM release builds passed. ARM files are static, stripped ARM EABI5 executables. |

Compile warnings remain in the original logs. The test counts describe separate
configurations, not a count of unique independent assertions.

## Measurement boundaries

Startup begins at the first Rust `main` entry, before argument parsing. It does
not include the OS loader or launcher preflight. Action timing begins when the
application receives an intent; script scheduling delay is excluded. Worker
queueing, source-manager setup, fetch, render and RGB preparation stages are
retained separately. Successful UI image acceptance follows view publication.
On Kindle, a one-shot token associates that accepted image with actual written
framebuffer bytes and the following successful SEND.

Neither SEND acceptance nor a framebuffer snapshot proves physical e-ink
completion. Optical latency, residual images and battery consumption are not
measured by these traces. Manual physical controls are a separate functional
qualification and do not become timed touch or optical samples.

Each workload retains the same fixed active path across implementations and
fresh processes. Original files are never recreated during restoration: actual
page-source paths retain their SHA, device, inode, length, mtime and ctime.
Mutable state is reset and actual input bytes are inventoried outside timing.
This warms OS page caches. A cold-render fixture removes only its owned
processed derivatives and preserves its originals. It is not a claim of cold
physical storage.

Measured process order alternates AB/BA. Startup workloads target 21 independent
processes per implementation. Retained turn sequences target at least 21 actions
in one process per implementation; those actions share state. Reports include
both action observations and independent process counts. P95 uses nearest rank.
Pilot and manual qualification are retained separately from formal samples.

## Controlled host measurements

The release x86_64 comparison completed in 308.25 seconds on `test-env` with
12 vCPUs. Before sampling, available memory was about 14.8 GiB and there were no
live build, application, Xvfb or bulk-copy processes. The task controlled its own
load and did not acquire a whole-host lease or exclude all unrelated activity.
No device upload was performed by this task during the formal host window.

All 90 processes passed, producing 216 accepted UI requests and 126 completed
actions. All 90 source-identity checks passed. Actual render outcomes were
48 disk hits, 42 cold renders and 126 memory hits. Both executable hashes were
unchanged afterward. Independent reanalysis returned `comparison_qualified:
true`; no failed process or missing workload was removed from the cohort.

Times are median / nearest-rank p95 in milliseconds. Startup begins at Rust
main; turn sequences begin at the received application action. All endpoints
in this table are UI image acceptance, not native or optical completion.

| Workload | Baseline p50 / p95 | Candidate p50 / p95 | Per implementation |
| --- | ---: | ---: | --- |
| Disk-cached startup | 92.563 / 130.912 | 90.158 / 123.758 | 21 independent processes |
| Cold-render startup | 244.700 / 249.112 | 182.323 / 193.759 | 21 independent processes |
| Cached page turns | 13.979 / 16.515 | 16.239 / 39.788 | 21 actions in one process |
| Chapter boundary | 16.933 / 33.284 | 14.098 / 18.931 | 21 actions in one process |
| Streamed vertical windows | 14.677 / 19.660 | 13.721 / 18.300 | 21 actions in one process |

Cold-render startup improved by 25.49% at the median in this workload. Cached
turns regressed by 16.17% at the median and also had a worse p95. This negative
result is retained. The demo has two alternating bundled pages; it does not
represent broad content diversity. Each long-action distribution has only one
independent process, so it cannot establish a stable universal improvement or
regression.

The cached-turn diagnostic shows multiple tail mechanisms. Its slowest candidate
action took 41.349 ms, with 4.306 ms in the worker and 36.308 ms between worker
completion and UI acceptance. A different 39.788 ms action included 22.021 ms
in rendering. Render peaks in both implementations recur about every 2.1 seconds,
consistent with the existing two-second access-metadata flush cadence, but no
separate flush timer establishes causality. Fresh source hashing is a known
integrity change and is compatible with the render-median increase from 0.196
to 0.496 ms; the small originals and mixed tails do not justify attributing the
whole difference to SHA. Percentiles of overlapping phases must not be added.

Both implementations also prepare a 13,124,160-byte RGB image, larger than the
existing 12 MiB prepared-pixel cache budget. Neither retains that RGB buffer in
this workload. This is a shared repeated-conversion cost, not a demonstrated
single cause of the candidate regression. No integrity protection or memory
budget was weakened to improve these measurements.

See [the compact host result](../artifacts/reader-e2e-20261110-01/reports/r2/host-formal-summary.json)
and [stage diagnostics](../artifacts/reader-e2e-20261110-01/reports/r2/cached-turn-stage-diagnostics.json).

## Physical device qualification

The first r2 short batch on 2026-11-11 used a fresh task-owned lease and confirmed
the physical Scribe build 479432, 8-bpp framebuffer and current original controls.
Both ARM payloads matched their complete device-side SHA-256 values. A normal
offline demo seed and one same-path native pilot exited with status zero in a
single application generation, without fallback. Native and lighting restoration
passed, and the lease was released immediately at 00:35:30 Asia/Shanghai.
Strict analysis qualified the seed's cold path and the same-active pilot's disk
path, with complete UI/write/SEND associations, zero dropped events and single
generation App exit 0.

The b03 manual window recorded real unscripted menu, previous and next actions
with matching UI/write/SEND events. It also retained the same PID and page 11
through one stock sleep/wake. The user nevertheless saw the native bookshelf on
both transitions. Its later cleanup was about 73.55 software seconds after the
retained-frame acknowledgement; that cleanup cannot dismiss the reported issue.
The supervisor code 143 and aggregate actual App exit null remain in that record.

The [sleep/wake display fix](SLEEP_WAKE_CONTINUITY.md) removes normal handoff's
forced stock redraw and takes ownership earlier on validated wake. All 37 remote
launcher tests passed. In b09 the user explicitly confirmed neither transition
showed the bookshelf. PID 20189, epoch 1, page 11 and one retained resume are
preserved in the corresponding trace. Native/lighting restoration passed and
the lease was released at 02:37:03 Asia/Shanghai on 2026-11-11. The per-generation
App wait and supervisor codes are 0, while the helper aggregate App field is
still null; this discrepancy is not rewritten into a stronger claim.

Earlier manual windows with no observed actions, parameter-validation rejection,
or a missed deployment remain failed or unqualified tool attempts. They do not
become successful product tests. Full native timing cohorts are pending.

The earlier `aidoku-koreader-20261110-r1` native smoke and the stopped r1 upload
attempt have separate identities and remain separate evidence. Neither is
relabeled as final r2 application or performance acceptance.

## Reproduction and evidence

See [the measurement procedure](READER_E2E_MEASUREMENT.md) for controlled-input,
cache-path, causal-trace and failed-trial gates. The remote task root is
`/root/aidoku-reader-e2e-20261110-01`. Public reports, source manifests, logs and
original unsuccessful attempts are retained separately from private device
transport and lease material. Only a whitelisted public evidence set is copied
back to the workstation; credentials, tokens and raw device identifiers are
excluded.
