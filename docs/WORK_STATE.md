# Work state

Updated: 2026-11-11.

## Current end-to-end delivery

The reader optimization and causal timing implementation is merged and pushed
to main as `4c572ee90355572aa2e9ee631cff829e8a61f1e3`. Its 252 runtime, UI,
asset and production-manifest inputs match the r2 build inputs byte for byte.
R2 default and Kindle-feature library suites passed 676 and 689 tests, retaining
two ignored opt-in benchmarks each. Python trace/fixture checks passed 59 tests.
The unchanged native backend component retains its 108-test passing record.
Both desktop and both ARM release builds passed.

The actual producer qualification exposed missing Reader-entry observation and
a fault fixture whose one-shot failure could be consumed by chapter prefetch.
The entry observation is corrected, real dispatch regressions were added, and
the held-failure fixture independently establishes foreground failure and old
display protection. Original rejected records and r1 binaries remain preserved.
Formal r2 timing and physical qualification are being completed separately;
this status does not claim their success or an optical-panel speedup.

The first cache pilot also proved that replacing local originals during fixture
restoration invalidated warm PNG keys through source metadata identity. Those
pilot results are marked cache-scope-invalid. Corrected workload-specific active
roots retain original source identities; strict pilots confirmed disk, cold and
memory paths before the formal host cohort started.

The shared device lease covers only continuous actual Kindle operations and
their necessary bounded restoration. Host builds, analysis, tool debugging and
idle waits run outside the lease. The r1 preparation lease was released at
23:07:26 Asia/Shanghai with no App run; the upload failure and exact restoration
record remain retained. See [project instructions](../AGENTS.md) and
[the measurement contract](READER_E2E_MEASUREMENT.md).

## Preserved KOReader algorithm evaluation

The algorithm evaluation on top of the delivered `ffb3417` adopts
output-compatible native-channel grayscale
conversion, row-buffer Lanczos3 scaling, processed-PNG reuse with fresh external
source integrity, a neutral Gray8 retained scene on actual 8-bpp mappings, and
distinct successful-update markers. It corrects the earlier omission of Scribe
fast-mode initialization in KOReader. Forced colored-JPEG Luma conversion,
another persistent current-frame codec, and unqualified MTK operations remain
disabled for the measured reasons in
[the full evaluation](KOREADER_OPTIMIZATION_EVALUATION.md).

Final remote checks passed 663 default library tests, 676 Kindle-feature library
tests, 101 backend tests and thirteen primary desktop UI cases. Each application
library configuration retained two ignored opt-in benchmarks. The default
all-target run also passed three binary and 95 example tests. Formatting and the
ARM release build passed. Cargo build concurrency remained six.

The resulting 49,181,368-byte ARM executable, SHA-256
`e0e8338763e7a386482f0cfd41c282e1ae52fa0c563277437770ada8dc0f6e6a`,
ran on a physical Scribe with firmware 5.19.5 build 479432. It accepted Reader
progress 12 -> 13 -> 12, exercised the menu and exited normally. All eleven
bounded smoke checks passed, original lighting and native controls were restored,
and the owned lease was released. See the
[physical smoke report](../artifacts/koreader-eval-physical-20261110-r1/reports/PHYSICAL_SMOKE.md).
These are actual native functional observations of the earlier algorithm
candidate, not acceptance of every later instrumented executable or panel-latency, ghosting,
battery, physical-input or comparative on-device performance measurements.
Host stage timings must not be presented as complete Kindle page-turn gains.

## Delivered reading and application follow-up

Commit `ffb3417`, already merged and pushed to main, adds reader
prefetch/decoder/pixel-publication work and the requested application feature
follow-up. Its implementation and separate
verification status are tracked in [reading follow-up](READING_FOLLOWUP.md).
Final remote checks passed 636 host library tests, 649 Kindle-feature library
tests, thirteen primary UI cases, two actual Reader-panel pointer cases and
twenty-nine mock launcher cases. Both library configurations retained two
ignored opt-in benchmarks. The ARM release build passed. Final Cargo build
concurrency was six; the verification worker was normally rebooted with explicit
user authorization after becoming unavailable. Physical-device and real-account
qualification remain outside these host results.
The frozen Candidate18 delivery and historical acceptance below retain their
original identities; the working changes are not covered by those old reports.

After the final delivery, the user confirmed that automatic brightness is
excluded from requirements and acceptance. Its absence is not an unfinished
item or a delivery blocker. Manual frontlight and warmth controls remain within
the implemented device capabilities.

Candidate18 is the final frozen 236-file source
`42b11de8d1b07e7af30665f98bb0dbf6291c0d6a6ec3355005c1402f42b21f24`.
Actual desktop/ARM/launcher builds and source/ordinary-return identities are
recorded in [current verification](PHASE3_VERIFICATION.md). Main's official
source selection and launcher matched the frozen inputs with no extras or
differences; the independently verified C18 staging supplied the package.

The final C18 targeted Native sequence passed two bounded actual cases: single90 and manual90/270 touch, 270 Down/Up and retained flat wake. The same final PID6613 retained epoch5/page15/manual270 with pressure0, exact wake pixels and no image-job restart. Normal exit restored the exact stock body, policy/USB1 and left no App, sensor, guard or owned lock. These are dedicated firmware-VM synthetic-sensor/RAM-fixture results, not physical Kindle or a repeat of C16 full-matrix acceptance; original failed and unassessed attempts remain preserved.

The final C18 formal sequence passed one actual exact ignored library test with twenty measured JSONL records, 21 primary timing processes, six SQL collections, one observer and one marked publication/sync audit. Source, both ELFs, all four tools and the wrapper matched before/after; the Native owner checked-restored the dedicated VM with stock/policy/USB/no-App-lock checks passing. Measurements are bounded host algorithm/render/Store evidence, not physical Kindle or a universal speedup; the 32-page counterexample, limited allocation/RSS counters and scale-dependent SQL remain documented.

## Final bounded UI and package

Both fresh final Search/F13/own-Reader repair rounds passed. Each reviewed
eight/eight manual cases with three reports, nine comparisons and fourteen new
necessary image opens; each read twenty-seven unit/completion-status columns.
Required findings, essential reading pending and identity mismatches were zero.
This is bounded C18 repair acceptance, not another all101-reference or 604-case
stress pass. The original C16 second-pass visibility failure remains preserved.

The one final ZIP is 29,700,422 bytes,
`b0280e4b26330b07cdd0f86bda8636c2bd33ef5205bb30419888edc14c0aaeb1`.
Its 4,877-byte manifest is
`af29e678e78925e7fe40e70fb4d1cf8771220d54074658bf28f13b3c0a2414fd`.
Strict verification passed 137 checks and 26 members/CRC, and the actual copied
ZIP/manifest returned to test-env matched exactly. Package IO RELEASED before
formal measurement. See [delivery](DELIVERY.md) for final copy and recovery.

## Implementation and limits

The closed Reader is an image-only canvas; menus provide explicit controls.
Recognized sensor Up/Down changes active presentation between 0/180 while
retaining chapter/page and manual 90/270 preferences. Synthetic sensor/RAM
firmware-VM checks do not qualify physical sensor, persistent storage, waveform
or real-device input/display behavior. Automatic brightness is not implemented
or qualified; exposed frontlight/warmth and truthful unavailable states define
the current capability bounds.

The raw/processed cache registry is shared by canonical data root. Admission
and origin/source limits are distinct; a foreground reservation does not promise
an immediate third transfer for every origin. Resident render-cache limits do
not bound live Reader arcs or whole-process RSS. Download dirty batches clear
after successful Store writes; typed SQLite contention retry is limited to its
implemented dequeue/checkpoint/ownership/wake paths, not every database write.
No unmeasured speedup or constant-cost SQLite claim is made.

For the frozen Candidate18 records, verification ran on test-env. Independent
work used isolated outputs and local mutual exclusion for genuinely shared
writable resources, without a whole-host lease. Only the Native owner controlled
the dedicated VM/QMP; that historical work did not acquire or change the shared
physical-Kindle lock. Local operations were
editing, ordinary copying/extraction and image reading, with no Windows tests,
builds, hash validation, runtime probes or image processing.

## Preserved checkpoints

The [original work-state document](../artifacts/phase3/candidate15/documentation-staging/current-documents-before-final-01/docs/WORK_STATE.md)
retains its dated C12 and earlier current/pending language, source identities,
failures and measurements. The [C16 first walkthrough](review/phase3-c16-final-design-pass1-closeout-receipt-01.json)
and [negative second walkthrough](review/phase3-c16-final-design-pass2-closeout-receipt-01.json)
retain their own scope; [the C18 closeout](review/phase3-c18-final-search-repair-closeout-receipt-01.json)
records the final bounded repair. Original code-preparation, oracle, filesystem,
helper, timeout and unassessed Native attempts are preserved, not re-signed.
Historical Phase2 dist recovery and C11 performance/native evidence remain
available through their original records and are not new-payload results.
