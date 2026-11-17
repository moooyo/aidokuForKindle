# Proposed execution sequence

Historical plan: execution and deployment are complete. See the
[execution record](../startup-catalog-optimization-20261117/PROGRESS.md) and
[new remaining-opportunity investigation](../startup-catalog-optimization-20261117/NEXT_STEPS.md).
Date: 2026-11-17, Asia/Shanghai.

## Decision

First establish whether the artificial document timestamp update causes the
indexed-route penalty. If confirmed, implement a compatible metadata update.
Then test final-cover publication. Reconsider native-read supervision only
after measuring the resulting baseline. Small launcher cleanup comes last.

This revises the initial recommendation that put a native supervisor ahead of
cover publication. The supervision surroundings measure 385 ms on the indexed
route but 158 ms on the temporary route. The difference may largely disappear
with the same contention fix. Those budgets are not independent savings.

The reference remains the current indexed Library item: 3.283 s median first
Library submission and 4.088 s complete-cover submission. Approaching the
current temporary-route 2.350/3.144 s is a useful first-stage target, not a
promised outcome or a reason to change the installed entry route.

## 1. Perform a causal timestamp experiment

Use the real `/mnt/us/documents/Aidoku.sh`, its current `--library` lifetime,
the same native handler registration throughout the timing batch, and identical
application/helper binaries and fixtures.

- Control: preserve the handler's document timestamp update and full-scan call.
- Diagnostic: suppress only the artificial `utimes` call for the qualified
  Aidoku document; retain the full-scan call and every other launch operation.
- Select the case outside the measured interval without rewriting the document
  or changing its mtime. Complete staging and indexing before warmups.
- Give both cases identical experimental-wrapper overhead. Qualify its
  pass-through behavior against the unmodified formal entry separately.

The previous dispatch prototype already identifies the exact document at its
`utimes` boundary and validates the installed handler identity. Its qualification
and restoration mechanisms can inform this diagnostic. Its earlier sleep/wait
and xrefresh interventions must stay disabled; they would add confounders.
The archived prototype is not a production dependency.

The temporary suppression changes recent-access behavior during this bounded
diagnostic and is not a shipping design. Restore owned files and normal native
metadata behavior after the batch. Leave other documents and the global scanner
policy unaffected.

Measure whether all three observations change together: catalog identity stays
stable, KPP CPU/Aidoku runnable wait decrease, and real-entry wall time improves.
Collect CPU diagnostics in separate launches from the primary timing samples.

If the identity remains stable but latency does not improve, preserving UUID
alone is insufficient evidence for a shared-component rewrite. Inspect the
remaining native Library activity before selecting another intervention.

## 2. Convert a confirmed result into compatible metadata behavior

Investigate the actual supported catalog update operation and preserve the
existing item's UUID, thumbnail, MIME and recent-use ordering. Avoid treating
the thumbnail-only scanner stub as a generic metadata API or bypassing catalog
notifications with direct database writes.

The desired behavior records recent use on the existing catalog item without
artificially making unchanged script contents appear modified. Confirm that
the candidate reduces native Library work rather than simply moving it into
the first user interaction after startup.

Use explicit opt-in and version qualification at the shared integration
boundary, with original behavior on unsupported combinations. Keep one formal
Library item. Treat the shared change as one maintained integration that Aidoku,
BiliComics and WeRead can adopt, rather than three packages overwriting the same
system component independently. Each application needs its own measurements.

If the supported update mechanism cannot preserve normal behavior, retain the
current formal implementation and proceed with the local cover experiment.
Further shared-integration investigation can remain separate from that work.

## 3. Publish the final prepared cover batch promptly

Keep the existing first-business-frame gate and normal 300 ms publication
coalescing. Only when the current startup's visible cover jobs have all
completed should the final dirty projection be eligible for an earlier flush.

Determine completion from the active lifecycle/source generation and visible
set after processing results, not merely from receipt of any cover message.
Respect suspension, cancellation, route changes and stale results. An empty or
failed cover must not become a successful complete-cover observation.

This is a narrow application change with less lifecycle risk than replacing
the native supervisor. The current 197 ms median from final preparation to
complete submission includes drawing and dispatch, so it is only a target
interval. Verify the effect on actual submissions and refresh count.

Revisit early cache preparation only if substantial preparation work remains
after this change. A separate bounded experiment must show that it does not
undo the previously measured benefit of protecting the first frame from cover
work. Do not combine prewarming and publication changes in one comparison.

## 4. Reprofile supervision, then choose the smallest sufficient change

Measure process creation, child work, completion publication and reaping on the
new baseline. The existing difference between outer duration and the child's
last trace event is not a direct measurement of avoidable shell work.

First consider fewer redundant processes and file handoffs. Introduce a native
supervisor only if remaining independent latency justifies its implementation
and recovery coverage. Preserve process identity/session ownership, bounded
output, cancellation, deadlines, group termination and publication checks.

Keep duplicate `SCRIPT_PATH` resolution as a separate final cleanup. Report an
undetectable timing effect as such; it must not be presented as a substantial
startup gain.

## Measurement and adoption gates

1. Prepare code, fixtures, binaries and tools on test-env before acquiring a
   fresh device lease. One operator runs the continuous device batch, restores
   owned state and releases immediately before host analysis.
2. Qualify correctness first, then use six predetermined AB/BA timing pairs.
   Preserve warmups, failures and slow samples separately and report all paired
   results, median and range. Instrumented diagnostic runs do not enter the
   primary latency comparison.
3. Keep the two startup endpoints separate: first Library submission/input
   readiness and complete visible-cover submission. Also qualify the first
   subsequent user interaction so deferred work does not hide a regression.
4. Require the same visible contents and cover-success criteria, native exit
   restoration, and same-process sleep/wake behavior. Retain the checked
   initial display/input barrier and the current CPU policy.
5. After a successful change, establish the next baseline before testing the
   next item. Do not add independent experiments' apparent savings together.
6. Final qualification includes ordinary Library use and a first launch after
   standby, separately labeled from repeated warm offline launches. Driver
   submission and optical screen completion remain distinct measurements.

An application-local change can be delivered independently if the shared
catalog work takes longer. No speedup is claimed until the matching formal-entry
comparison passes; no architectural rewrite is selected solely from a large
wall-clock interval.
