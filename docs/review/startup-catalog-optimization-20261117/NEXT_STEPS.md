# Remaining startup opportunities after the deployed optimization

Date: 2026-11-18, Asia/Shanghai.
Reference: the deployed implementation in [the final report](README.md).

The controlled baseline is now **2.278 s to the first Library frame** and
**2.829 s to complete visible covers**, using the real indexed entry, warm offline
data and software/driver endpoints. The prior catalog and final-publication work
is complete. This document evaluates additional work; none of the savings below
has been demonstrated by an intervention on the new baseline.

## Priority and plausible benefit

The ranges are engineering experiment targets, not confidence intervals or
promises. Zero improvement or a regression remains possible. Their budgets
overlap, so neither ranges nor stage medians may be summed.

| Priority | Candidate | Plausible trial target | Confidence and scope |
| --- | --- | --- | --- |
| 1 | Replace the hot shell supervision path with one bounded native supervisor | 150-300 ms faster first frame and complete covers | Low to medium; a large measured path exists, but no native implementation has been compared |
| 2 | Prepare a bounded set of validated cached covers during the existing initial hardware wait | 100-220 ms faster complete covers, with no first-frame regression | Medium mechanism evidence; scheduling and source-proof costs still require an experiment |
| 3 | Avoid the intermediate cover frame when the last cached job narrowly misses the publication deadline | 150-350 ms in affected slow-tail cases; 0-80 ms median target | One observed case out of six; insufficient samples for a tail-frequency or percentile claim |
| 4 | Profile and reduce first business-frame layout/text work and redundant UI projection | 40-100 ms faster first frame | Low until actual layout/shaping stacks are measured |
| 5 | Park a bounded application instance after returning to native Home, then reacquire it from the same Library item | 500-900 ms faster complete covers on subsequent opens only | Low; a larger architectural option with unmeasured memory, battery and recovery costs |

## 1. Native supervision and launcher handoff

The mean request-to-process interval is 1,242 ms: 395 ms before the document and
847 ms from its entry to Rust process start. Within the latter, medians are:

- Entry to independent worker: 180 ms.
- Worker to power-event observer start: 115 ms.
- Observer start to ownership-ready: 100 ms.
- Portrait transaction: 275 ms outside, about 162 ms inside the helper.
- Display takeover: 100 ms.

The per-run median difference between outer and inner portrait boundaries is
115 ms. It includes supervision, scheduling, process startup and reap boundaries;
it is not all proven removable shell cost. The previous shell `/proc` consolidation
and duplicate-path removal did not improve both user-visible endpoints and were
reverted. A native supervisor is a different implementation and needs its own
matched experiment.

First instrument the fresh worker, observer, permission, helper and reaping
boundaries without introducing additional processes into primary timing samples.
Then move one bounded command transaction to a small native helper before
considering wider launcher migration. Preserve PID birth/session/parent checks,
permission release, output overflow detection, command status, group termination,
publication ownership and finite cleanup. Keep the same installed Library entry.

Do not delete the power observer, portrait authorization or the initial display
barrier to meet the target. Recheck ordinary exit, waiter retirement, startup
failure, same-process sleep/wake and post-restoration catalog notification.

## 2. Prepare cached covers inside an already-required wait

The initial checked display/input barrier averages 366 ms. Metadata preparation
finishes about 124 ms after worker admission, leaving **241 ms** before initial
input readiness. Font bytes are already prepared in approximately 6 ms.

After the first Library frame, the six serial cover jobs take **243 ms mean /
238 ms median**, of which 220/218 ms is before the local-derivative-hit marker.
That marker includes mutable-source hashing, cache lookup/validation and bounded
processed-image decoding; the existing trace does not separate those costs.
Post-hit alias checks, conversion and result preparation total only about 22 ms.
Queue handoffs between jobs total about 13 ms.

There is a concrete overlap opportunity: use a finite background budget after
metadata is ready, restricted to already available validated derivatives for
the initial visible set. Hold immutable prepared pixels without changing Slint
models or requesting intermediate frames. Cancel or park work before first-frame
rendering; admit normal misses and network requests through the existing later
path. Validate source identity, options, route, lifecycle and source revision
again before adoption.

Do not treat mutable source metadata as a substitute for its current byte proof,
or remove account/source namespaces. Earlier work already avoided redundant
alias writes, full source decode on derivative hits and repeated access writes;
those are not new savings to claim. Additional parallel workers could recreate
the contention just removed.

Measure one- and two-cover bounded prewarm before attempting the entire set.
Compare first frame, complete covers, background work after cancellation and
memory use. The full 241 ms overlap window is an upper budget, not a guaranteed
241 ms saving.

## 3. Remove a remaining cover-publication race

Five final candidate trials submit the first frame and one completed-cover frame.
In the sixth, the final cached job completes at **301.700 ms** after the first
Library frame, narrowly missing the normal 300 ms publication cadence. A partial
six-of-seven-slot frame is submitted at 489.009 ms. The final publication follows
at 491.503 ms, but its accepted frame arrives at 807.440 ms; its recorded
prior-buffer settling cost is **200.117 ms**.

This explains a specific slow-tail mechanism, not a generic 300 ms sleep after
every cover set. Across all six runs the last-job-to-publication median is only
**1.2 ms**. Removing that already-small median cannot deliver another large
universal speedup.

Test a single short, bounded grace period only for the unchanged startup visible
set when its last cached job is still pending. Keep user interaction, failures,
route changes, ordinary background jobs and retries on their current cadence.
The experiment must not indefinitely wait for a slow or network-backed image.
Collect more than six observations before claiming a stable tail probability
or P95 improvement. Judge the grace period together with cached-cover prewarm,
because prewarm may remove the same partial-frame race.

## 4. First business-frame rendering and view projection

The first render itself has a **238 ms mean / 230 ms median**. Pixel writing and
submission add approximately 20 ms. View synchronization averages 81 ms and
includes a native status transaction plus library/model work. Final cover-frame
rendering is much smaller, approximately 85 ms mean.

Capture diagnostic-only layout, glyph shaping, glyph rasterization and model
invalidation profiles. Inspect `App::sync_view`, which reapplies labels,
preferences and models before route projection, and the first Library scene in
the software renderer. Reduce work only where measured repeated preparation or
unnecessary model replacement is established. Preserve titles, fonts, language,
visible items, geometry and pixel equivalence on both device profiles.

A separately measured status snapshot prepared during the initial wait may
remove a few tens of milliseconds of synchronous view work, but it shares the
same overlap budget as cover preparation. Do not book a second saving for it
before testing the combined path. Font registration alone is about 52 ms, so
further font micro-optimization is lower priority.

## 5. Resident fast return through the existing Library item

The process currently retains state through sleep/wake but exits when returning
to native Home. A bounded parked mode could preserve fonts, UI objects and visible
cover pixels for a subsequent Library open. It would still need fresh native
ownership, current source/configuration checks, display/input authorization,
catalog recent-access behavior and recovery if the process is missing or stale.

The 500-900 ms target is a rough amortization hypothesis drawn from the remaining
initialization, projection and cover-preparation budgets. It is not inferred
from the existing sleep/wake test, which follows a different native route. It
does not improve the first start after reboot, eviction or explicit termination.

Use the same Library entry to request a retained instance; expire it after a
bounded interval or memory pressure and fall back to normal startup. Measure
resident memory, idle battery behavior, concurrent native use, process loss,
sleep/wake and repeated open/exit cycles before deciding whether the saved time
justifies maintaining this lifecycle. Do not retain native display ownership
while the user is in Home.

## Experiments not worth prioritizing now

- More shell path cleanup: the completed three-way experiment did not show a
  consistent complete-cover benefit.
- Disabling catalog notifications or never updating recent access: both remove
  existing behavior; production now preserves it with much lower contention.
- Shortening or skipping the checked initial display/input barrier: this work
  provides no correctness evidence for doing so.
- More font byte-loading optimization: approximately 6 ms remains in that phase,
  already overlapped by the hardware wait.
- Raising CPU frequency or adding unrestricted cover workers: the previous slow
  route already sampled higher CPU frequency, while contention was much worse.
- Quoting cold-cache or online benefits from this batch: those regimes require
  separate prepared fixtures and measurements. The slow pilot warmup is retained,
  but is not a matched cold-cache performance comparison.

## Measurement plan

Start every new comparison from the actually deployed source/artifact identities
in the final report. Prepare the host build and fixtures before acquiring a new
device lease, and update old harness restoration guards for the now-managed
catalog registration. Never restore an obsolete document or shared command.

Use the same formal indexed route, cache fixture, boot and stabilization policy;
predetermine balanced orders and report all paired values. Keep CPU, syscall and
screen-capture diagnostics out of primary timing samples. Report first Library
frame, software input readiness, complete covers, frame count and native exit
alongside the candidate's intended stage. Revalidate pixels, recent access,
identity, notification and lifecycle after every retained implementation.

The [public interval evidence](evidence/remaining-opportunity-evidence.json)
supports this prioritization. There is no demonstrated remaining one-second
removable block in a normal fresh start; the larger architectural opportunity
applies to repeat opens. Conventional optimization should target another few
hundred milliseconds and improved consistency, then be accepted only on measured
end-to-end results.
