# Startup optimization execution record

Status: implementation, final comparison, deployment and installed-entry
verification completed on 2026-11-18. See [the final report](README.md) and
[the remaining-opportunity investigation](NEXT_STEPS.md).

## Completion record

| Work item | Outcome |
| --- | --- |
| Timestamp/catalog causality | Six-pair diagnostic confirmed a large indexed-entry penalty; timestamp preservation alone was not suitable for delivery |
| In-place recent-access update | Immediate notification regressed; retained as a rejected experiment |
| Notification isolation | Quiet update removed the native Library CPU burst; normal notification was restored afterward |
| Deferred notification | Implemented with completion owned by the independent production launcher worker |
| Final visible-cover publication | Implemented, tested and retained |
| Shell supervision and redundant path resolution | Implemented and measured; reverted because complete-cover performance did not consistently improve |
| Production packaging | Immutable qualified runtime, explicit document opt-in, installer, ZIP and KPM completed |
| Final same-route AB/BA | First frame 3.005 to 2.278 s; complete covers 4.112 to 2.829 s; both faster in 6/6 pairs |
| Physical lifecycle | Matching pixels, recent access, stable candidate identity, exit and same-process short sleep/wake passed |
| Deployment and actual installed-entry smoke | Completed; normal user data preserved, notification completed, native state/lighting restored and fresh leases released |
| Remaining-opportunity investigation | Completed from existing recordings and code inspection; estimates remain explicitly unmeasured |

The following sections preserve the earlier investigation record. Their baseline
and pending-work descriptions refer to that earlier stage, before delivery.

## Timestamp causality

`catalog-pilot-d76bec0f` qualified the unchanged formal entry, the pass-through
diagnostic and timestamp preservation. Both diagnostic policies use the same
wrapper and hook. Scanning, waiting, privilege transition and redraw remain
unchanged. All six launches completed and original state was restored.

`catalog-causal-602ffb58` then ran two warmups, six predetermined AB/BA timing
pairs and four separate CPU diagnostics through the actual indexed document.

| Endpoint | Original timestamp update | Preserve timestamp | Saving |
| --- | ---: | ---: | ---: |
| Software ready | 2.859 s | 2.163 s | 696 ms |
| First Library submission | 3.300 s | 2.437 s | 863 ms |
| Complete visible-cover submission | 4.497 s | 3.210 s | 1,287 ms |

All six pairs improve both frame endpoints. All 18 identity/mtime/recent-access
observations match the selected policy. The native Library CPU deltas through
complete covers are 2,030/2,070 ms for the control and 220/170 ms for the
candidate. Aidoku main-thread runnable wait through the first frame is
683/602 ms versus 174/124 ms. CPU sampling is excluded from primary timings.

Timestamp preservation also prevents the existing recent-access update. It is
a diagnostic result, not a shipping design. The 14 isolated host tests pass.
The compiled diagnostic source is retained under `experiment/`; its original
artifacts and full private records are on test-env under
`/root/aidoku-startup-catalog-20261117`.

## In-place catalog API qualification

Read-only firmware inspection confirms a local catalog `/query` and `/change`
protocol. An update command can set the existing UUID's `lastAccess` in epoch
seconds while retaining normal catalog notifications. Firmware sources and
libraries remain private; they are not redistributed here.

`experiment/catalog-client.c` queries only the fixed Aidoku script location
and shell MIME, rejects duplicate or stale metadata, updates recent access and
the existing zero percent-finished state, and verifies the same identity and
file after the acknowledged transaction. It uses the firmware token helper
internally and never logs tokens or catalog IDs. The full operation has an
800 ms deadline. Thirteen isolated host tests pass, including wrong identity,
stale files, rejected writes, malformed replies, timeout and header injection.

Real framework-user probe `catalog-client-probe-1b8912c9` succeeds: root query
83 ms, framework query 28 ms, framework update plus readback 185 ms. Identity,
document bytes, mtime, size, MIME and thumbnail are unchanged; recent access
advances. Temporary files are removed and the fresh lease is released.

Retained probe failures:

- `catalog-client-probe-7531775e`: the canonical path guard rejected firmware's
  `/tmp` to `/var/tmp` alias before the client executed. A bounded ownership-
  checked recovery removed the temporary directory and released the lease.
  The catalog was unchanged. Later probes use the observed canonical directory.
- `catalog-client-probe-92a60762`: framework update/readback succeeded in 140 ms,
  but the independent SQLite observation hit a transient busy database. Cleanup
  and release passed. A bounded read-only busy timeout fixed the observation;
  it does not change application behavior.

## Historical remaining-work plan (completed)

1. Add a separately qualified in-place-update policy to an isolated diagnostic
   wrapper. Run the fixed catalog client under a bounded owned child, fall back
   to original timestamp handling on failure, and retain all other handler work.
2. Verify that preserving recent-use behavior also preserves the startup gain.
   Qualify pixels, catalog behavior, ordinary exit, and sleep/wake before deciding
   how to deliver a shared opt-in integration.
3. Independently test final-cover publication, reprofile native-read supervision,
   and assess duplicate launcher path resolution against each retained baseline.

All verification ran on test-env. Device leases covered prepared continuous
operation batches and were released before host analysis. At the end of this
historical stage the installed files were still the main baseline. They have
since been replaced by the verified production delivery documented above.
