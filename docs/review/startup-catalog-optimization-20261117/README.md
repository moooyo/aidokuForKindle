# Indexed Library startup: implementation and deployed results

The optimized application, launcher, Library document and qualified catalog
runtime were installed on the shared Kindle on **2026-11-18**. The first Library
frame improved from **3.005 s to 2.278 s**, and the frame with all visible covers
from **4.112 s to 2.829 s**. Both endpoints improved in all six paired trials.

Base revision: `06b21491f7bf8977f1f44eb95e484ff9a14d6b8a`.
Implementation branch: `codex/startup-catalog-optimization`.
The directory date identifies the beginning of the investigation.

## Final comparison

Batch `final-installed-inputs-4f83ee4a` compares the original main application
and launcher with the final production implementation. Both use the same actual
indexed Library item, native file-URI dispatch, boot, warm offline fixture and
eight-second native stabilization interval. Six AB/BA pairs were fixed before
measurement. Warmups, pixel qualification and CPU diagnostics are excluded.

| Endpoint from the file-URI request | Original main | New implementation | Difference of medians |
| --- | ---: | ---: | ---: |
| Software ready | 2.625 s | 2.028 s | 597 ms faster |
| First Library frame accepted by the driver | 3.005 s | 2.278 s | 727 ms faster |
| Library software input readiness | 3.005 s | 2.278 s | 727 ms faster |
| All visible covers accepted by the driver | 4.112 s | 2.829 s | 1,283 ms faster |
| Shutdown request to native Home active | 3.237 s | 2.620 s | 617 ms faster |

The startup improvements are approximately 24% and 31%. Exit did not regress
in this final batch; its individual values remain variable. An earlier prototype
added approximately 194 ms to exit, but that is not the final implementation's
result. Independent stage medians must not be added together.

The primary comparison keeps one qualified wrapper registration fixed. Its
baseline policy executes the original timestamp behavior; its candidate policy
enables deferred notification. Timing hooks and controlled fixture paths are
used at the actual indexed document path. A separate post-installation check
uses the unmodified installed document and normal user data.

These are software/driver boundaries, **not physical tap-to-pixel or optical
e-ink completion measurements**. They do not predict cold-cache downloads,
arbitrary library sizes, other firmware versions or long deep-suspend wakeups.
The historical 2.x-second temporary-script results remain explicitly qualified
in the [route investigation](../startup-cost-attribution-20261117/README.md).

Primary evidence: [all samples](evidence/final-installed-inputs-4f83ee4a.json),
[paired improvements, exit and CPU diagnostics](evidence/final-diagnostics.json).

## What was implemented

### Commit recent access at launch; notify the native Library after restoration

The original shared handler updates the script timestamp and requests a full
scan. That can replace the catalog identity and trigger expensive native Library
work while Aidoku is initializing. Merely preserving the UUID is insufficient:
an in-place update with the immediate normal notification still regressed.

The qualified integration commits `lastAccess` on the existing Aidoku row with
`noNotify: true`, then delegates the normal notification to the independent
launcher worker. The worker restores native display/input before sending that
notification. It retains the launch timestamp rather than replacing it with the
exit time. The existing zero percent-finished state is preserved.

In independent final diagnostics, native `KPPMainApp` CPU through complete covers
fell from 2,470/2,480 ms to 200/180 ms. Aidoku main-thread runnable wait over that
window fell from 957/963 ms to 166/98 ms. These CPU totals support reduced
contention; they are not additional wall-clock savings.

The production implementation consists of:

- [The dispatcher and hook](../../../src/native_catalog_entry/README.md), which
  qualify the original handler, catalog source and helper identities. Only the
  first timestamp update for the explicitly marked canonical Aidoku document
  is eligible. Other documents keep the original behavior.
- A fixed-entry catalog client with bounded loopback transactions, identity and
  file validation, acknowledged update/readback, and a whole-process alarm.
  Firmware libraries are used in place; authentication material is not logged.
- [The launcher handoff](../../../packaging/kindle/launch.sh), which keeps
  notification responsibility in the independent worker across waiter retirement
  and sleep/wake. It clears the handoff from application-child environments,
  validates the immutable helper, and uses the existing bounded native-command
  protocol. Notification failure falls back to standard timestamp/scan recovery
  after native ownership, with an early-failure completion path before ownership.
- [An installer](../../../packaging/kindle/install-catalog-entry.sh), ZIP and KPM
  integration. Runtime versions are immutable and remain available to active
  workers. Unsupported versions retain original behavior; unrelated handler
  registrations are not overwritten. `--disable` restores original dispatch.

The original shared executable, scan request, privilege transition, handler
lifetime, MIME association and accepted Aidoku cover remain in place. There is
still one installed Aidoku Library item. Only its existing Scriptlet command
registration points at the qualified wrapper. The present protocol is Aidoku-only;
sibling applications must coordinate any extension of this shared component.

### Publish the completed startup cover set promptly

[Cover publication](../../../src/app/cover_jobs.rs) still starts background jobs
after the first business frame and retains the normal 300 ms grouping cadence.
When the initial visible set finishes, one early publication is allowed. Route,
modal, lifecycle, source or visible-set changes cancel this opportunity. Failed
covers retain normal retry behavior.

The isolated experiment `cover-final-4f9eaefc` improved complete covers from
4.779 s to 4.513 s, with four of six pairs faster. Its first-frame median was
effectively unchanged, 3.390 s versus 3.373 s. The final-publication event occurred
exactly once after the first frame and before complete covers. This 266 ms
isolated improvement must not be added to the final combined 1,283 ms saving.

See [isolated samples](evidence/cover-final-4f9eaefc.json) and
[publication qualification](evidence/final-flush-qualification.json).

## Experiments that informed the decision

| Experiment | First Library frame | Complete covers | Decision |
| --- | ---: | ---: | --- |
| Preserve the document timestamp | 3.300 to 2.437 s | 4.497 to 3.210 s | Causal diagnostic only: recent access did not advance |
| In-place update with immediate normal notification | 3.434 to 3.523 s | 4.644 to 5.100 s | Rejected: slower despite stable identity |
| Prototype deferred notification | 3.397 to 2.364 s | 4.752 to 3.116 s | Qualified the mechanism; replaced by worker-owned production completion |
| Consolidate shell `/proc` reads | 3.313 to 3.368 s | 4.527 to 4.552 s | Reverted: no stable end-to-end benefit |
| Add removal of unused worker path resolution | 3.313 to 3.252 s | 4.527 to 4.683 s | Reverted: complete covers became slower |

Each row is a separate controlled batch with its own baseline. Cross-row numbers
are not a single cumulative trajectory. The shell variants were tested before
the final catalog fix; their results do not prove a future native supervisor
cannot help, but they do rule out claiming these particular edits as wins.

A separate notification-only diagnostic performed the same recent-access update
with and without the immediate notification. Native Library CPU in its five-second
window was 2,440/2,430 ms with normal notification and 0/0 ms with `noNotify`.
Each quiet transaction was followed by the normal notification and restoration.
This establishes a notification effect in that diagnostic context, not a separate
startup latency result. [Notification evidence](evidence/notification-diagnostic.json).

Research variants are retained under `experiment/`; they are not packaged.
The [shell comparison](evidence/supervision-three-way.json) preserves all three
variants and all six balanced orders, including unfavorable observations.

## Verification and installation

All builds, tests, checks, analysis and device probes ran through `ssh test-env`.
Cargo concurrency was six. [Verification summary](evidence/verification.json).

| Check | Result |
| --- | --- |
| Rust library tests | 819 passed; 5 existing ignored |
| Startup tests | 22 passed |
| Launcher lifecycle and notification tests | 89 passed |
| Native catalog dispatcher/hook tests | 20 passed |
| Catalog client protocol and timeout tests | 17 passed |
| Catalog installer tests | 10 passed |
| KPM packaging and hook tests | 8 and 12 passed |
| Frozen ZIP verification | 211 checks passed |
| Final source consistency | 359 source files and 111 packaging/script files matched exactly |

The final Rust ELF is the already-qualified final-cover build. Every Rust, UI,
asset, vendor and build input was compared with that build before reuse; the new
C runtime was built separately. The last worktree comparison found no source,
packaging or line-ending mismatch. [Source identity](evidence/build-identity.json),
[full source manifest](evidence/source-manifest.json),
[worktree consistency](evidence/final-source-consistency.json),
[package checks](evidence/package-verification.json).

Desktop Library pixels are identical on both Scribe and PW5 profiles. The valid
recapture is `paired-profiles-r2`; the earlier empty capture files are not used as
evidence. Its archive SHA-256 is
`321c78a3b4af6b5233bcc7c85037551b138d8b8fc1423cec8fbdea9ea8c75608`.
The recapture used tracing for capture diagnosis, so its host timings are not
performance evidence. Existing frozen UI baselines were not replaced.
[Desktop pixel evidence](evidence/desktop-pixels.json).

Physical qualification covered the real installer, catalog identity, advancing
recent access, MIME and thumbnail preservation, matching pixels below the live
status bar, ordinary exit, and same-process powerd sleep/wake. The wake test used
a local six-second scheduled wake and is not extended deep-suspend validation.
All ten final candidate runs completed their worker-owned notification without
fallback. [Pilot](evidence/production-pilot.json),
[lifecycle](evidence/production-lifecycle.json).

Deployment `deploy-startup-bd3b6a33` installed the verified ZIP and enabled the
runtime. User library, profile, cover assets and original shared-handler bytes
were preserved. Program rollback copies were retained. Native state was restored
and the owned lease was released. [Deployment](evidence/deployment.json).

Then `installed-smoke-5d30361d` opened the **unmodified installed Library document**
twice with normal user data and preferences. Pixels matched the qualified view,
catalog identity remained stable, notification completed, and the first case
survived same-process sleep/wake with matching restored pixels. Native state and
lighting were restored and the fresh lease was released. External ready-log
receipt was 2.230 s and 1.970 s; these observations are not complete-cover times
or a new controlled speedup estimate. [Installed smoke](evidence/installed-smoke.json).

Retained limitations and failures:

- A pre-existing night-mode sleep-screen UI assertion was reproduced on unchanged
  main earlier in this task. Its UI and assertion were not altered to hide it.
- The first installed smoke successfully launched, woke and notified, but its
  immediate native-state observation and redundant wake request rejected an
  already-active state. Bounded recovery restored state/lighting and released the
  lease. The successor used the existing bounded readiness observation and passed.
  [Retained failure](evidence/retained-smoke-failure.json).
- Earlier catalog probes encountered a guarded canonical-path mismatch and a
  transient read-only SQLite observation lock. The original state and owned
  leases were restored. Neither is included in latency statistics.
- Transport failures from a removed temporary known-hosts file and oversized SSH
  command arguments were retained privately. Later operations used strict host-key
  checking with persistent private inputs and short `sh -s` commands. Tool debugging
  and builds occurred outside device leases.

Raw tokens, device identifiers, catalog identities, user metadata and real user
screenshots remain private. Exported evidence contains measurements, flags and
artifact identities only.

## Remaining time and next work

The new six-run mean is 2.800 s to complete covers. Disjoint intervals account for
it as follows; the separately reported total median is 2.829 s.

| Interval | Mean |
| --- | ---: |
| Native dispatch and qualified catalog access | 395 ms |
| Launcher, observer, portrait calibration, takeover and process start | 847 ms |
| Platform/backend/window and initial submission | 256 ms |
| Checked initial display/input barrier | 366 ms |
| Remaining font registration, attachment and view synchronization | 174 ms |
| First Library render and submission | 258 ms |
| First Library frame to complete visible covers | 503 ms |
| Total | 2,800 ms |

[The follow-up investigation](NEXT_STEPS.md) ranks further opportunities and
separates measured budgets from untested savings. The largest remaining practical
targets are a bounded native supervisor, cached-cover preparation within the
existing hardware wait, and avoiding the occasional intermediate cover frame.
Font byte loading is now about 6 ms and font registration about 52 ms; neither is
the remaining second-scale bottleneck. The checked display/input barrier stays.

Analysis is reproducible with [analyze-remaining.py](analyze-remaining.py) on
test-env. It reads existing private recordings and performs no device operation.
The public [cost map](evidence/final-cost-map.json) and
[opportunity evidence](evidence/remaining-opportunity-evidence.json) retain each
candidate observation. Build artifacts and private records are under
`/root/aidoku-startup-delivery-20261118` on test-env.
