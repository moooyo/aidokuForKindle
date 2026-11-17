# Startup route attribution and remaining opportunities

This is the historical investigation before the catalog optimization. Its
recommended experiments have now been completed, and the new implementation was
deployed on 2026-11-18. See the [final results](../startup-catalog-optimization-20261117/README.md)
and [updated opportunities](../startup-catalog-optimization-20261117/NEXT_STEPS.md).

**The current indexed Library route adds 933 ms to first Library submission and
945 ms to settled-cover submission compared with a temporary script, using the
same current application, helper, launcher, and `--library` lifetime.** All six
pairs are slower on the indexed route. A separate diagnostic confirms catalog
identity replacement and substantially more native Library CPU work on that
route. This investigation changes no production code or installed program.

Source revision: `06b21491f7bf8977f1f44eb95e484ff9a14d6b8a`.
Research branch: `codex/startup-cost-attribution`.
Date: 2026-11-17, Asia/Shanghai.

## Why the earlier 2.x seconds and current 4.x seconds differ

The earlier [sequential optimization](../startup-sequence-20261117/README.md)
reported 2.254 s for the first Library frame and 2.677 s for complete visible
covers through a temporary script outside `documents`. Those were not absolute
timings for the indexed Library item.

The subsequent [formal entry change](../native-library-entry-20261117/README.md)
measured the actual indexed document on both sides: first Library submission
improved from 3.916 to 3.316 s, and complete covers from 4.831 to 4.088 s. Its
gain remains valid. Presenting the earlier temporary-route numbers as the
absolute speed of this formal route was insufficiently qualified.

This investigation now isolates the route using the same current implementation:

| Endpoint from native file-URI request | Temporary script | Indexed Library document | Indexed extra time |
| --- | ---: | ---: | ---: |
| Software ready | 2.079 s | 2.813 s | 734 ms |
| First Library accepted frame | 2.350 s | 3.283 s | 933 ms |
| Library software input readiness | 2.350 s | 3.283 s | 933 ms |
| Visible-cover settled accepted frame | 3.144 s | 4.088 s | 945 ms |

These are six-run medians from `route-attribution-b9aef85e`. The old 2.677 s
cover result and the current temporary-route 3.144 s still differ by about
467 ms. The older batch also differs in entry lifetime, dispatch invocation and
system state. Those differences have not been independently separated; the
entire historical gap must not be attributed to indexing or called a measured
production regression.

Both current routes use native file-URI dispatch, the same boot, the same warm
offline library/cache fixture, and eight seconds of native-screen stabilization
before each request. Two warmups precede six predetermined AB/BA rounds. The
temporary route is a research control, not a second installed user entry.
There is still one installed Aidoku Library item.

The primary runs do not sample CPU or capture screenshots during startup. Two
separate qualification runs have identical framebuffer pixels below the live
status bar. Every measured run settles seven image slots for six unique books,
with no cover failure. These are software/driver boundaries, not physical tap
or optical e-ink completion measurements. Network downloads are excluded.
See [all twelve primary observations and cost intervals](route-comparison.json).

## Where the current time goes

The following disjoint intervals use the six indexed runs' means, so they add
to the mean total of 4.277 s. The median total is 4.088 s; two slower cover tails
raise the mean. All observations are retained.

| Interval | Mean |
| --- | ---: |
| Native dispatch, launcher supervision, calibration and Rust process start | 1.622 s |
| Platform/backend/window initialization, initial checked frame, attachment and view synchronization | 1.179 s |
| Ready to first Library render/submission | 0.487 s |
| First Library submission to settled visible covers | 0.989 s |
| Total | 4.277 s |

The initial checked display/input barrier itself averages 365 ms, almost
identical across routes. Font/metadata preparation already overlaps that
barrier. It is not a newly discovered removable delay. Native dispatch to the
script itself is also similar: medians 320 ms and 325 ms. Most additional route
latency appears after dispatch, across launcher work, attachment, native status
queries, and rendering. The first Library render's wall-clock median rises from
238 to 430 ms; this does not establish that it performs twice as much CPU work.

## Catalog and scheduler evidence

Batch `scheduler-diagnostic-0739ab6e` adds two warmups and four diagnostic runs
in AB/BA order. It reads catalog identity hashes before and after each run and
samples process counters, CPU frequency and main-thread `schedstat` every
100 ms. It changes no scheduler or frequency policy. These instrumented runs
are excluded from the primary latency comparison.

| Observation | Temporary route | Indexed route |
| --- | ---: | ---: |
| Catalog identity changed in diagnostic runs | 0 of 2 | 2 of 2 |
| Original Library document mtime changed | 0 of 2 | 2 of 2 |
| Catalog rows for the Library document after each run | 1 | 1 |
| Native `KPPMainApp` CPU through settled covers | 180-200 ms | 1,830-1,870 ms |
| Native `KPPMainApp` CPU in the common first two seconds | 160-180 ms | 890-1,030 ms |
| Scanner process CPU through settled covers | 100-110 ms | 100 ms |
| Aidoku main-thread runtime observed before first Library frame | 236-274 ms | 227-229 ms |
| Aidoku main-thread runnable wait observed before first Library frame | 65-87 ms | 556-656 ms |
| Mean sampled CPU frequency through settled covers | 1.73-1.76 GHz | 1.96-1.97 GHz |

The slower route runs at a higher sampled frequency. Lower CPU frequency does
not explain this route difference. The scanner's own CPU cost is small and
similar, while the native Library process becomes much busier and Aidoku spends
longer waiting for CPU. This supports scheduling contention as a major source
of the apparent application-stage inflation.

The earlier independent diagnostic quartet also observed `KPPMainApp` deltas
of 19-20 ticks versus 190-193 ticks, with similar scanner costs. The new sampler
confirms 100 ticks per second on the same boot. See
[the earlier CPU observations](cpu-diagnostics.json) and
[catalog/scheduler observations](scheduler-diagnostic.json).

The initial temporary-route warmup also changes catalog identity because the
research document has just been staged. It is retained and explicitly excluded
from the route comparison. Private records retain identity hashes; public
records contain only change flags, not UUIDs, PIDs or device identifiers.

The inspected upstream source is pinned to
`adb7ef5c570a25c76fd64e94837ae0f590bd3b9c`:

- The [shared launcher](https://github.com/KindleModding/sh_integration/blob/adb7ef5c570a25c76fd64e94837ae0f590bd3b9c/launcher/main.c)
  calls `utimes(filePath, NULL)` and requests `doFullScan` before invoking the
  application script, to update the recent-item position.
- The [extractor](https://github.com/KindleModding/sh_integration/blob/adb7ef5c570a25c76fd64e94837ae0f590bd3b9c/extractor/main.c)
  handles `SCANNER_UPDATE` by deleting the old catalog entry and SDR, then
  indexing again. `index_file` generates a new UUID each time.

The real-device identity changes agree with that source behavior. However, no
KPP call stack was captured and no metadata-only intervention was tested here.
The evidence does not prove which internal Library operation consumes each
millisecond, or that all 945 ms can be recovered by preserving the UUID.

## Candidate experiments

The subsequent [execution plan](NEXT_STEPS.md) refines the order: first isolate
the timestamp/catalog cause, then qualify a compatible metadata change, then
test final-cover publication. Reprofile native supervision afterward because
its apparent overhead overlaps the same scheduling contention. The component
budgets below are retained as observations, not additive expected savings.

### Update Library metadata without deleting and rebuilding the entry

This has the largest measured opportunity: about 0.94 s between current routes.
It is a route difference, not a guaranteed optimization saving.

Prepare an opt-in, version-qualified experiment that updates the existing
Aidoku catalog row in place while preserving identity, cover, MIME, recent-item
ordering and applicable scanner/hook behavior. Hold the current handler
lifetime and other launch behavior constant. Verify the supported catalog
change operation before using it; the upstream `scanner_update_ccat` stub takes
a thumbnail path and is not evidence of a generic timestamp-update API.

This work occurs before the Aidoku script runs, so changing only the Rust app
or its script cannot remove it. The shared integration/indexer boundary needs
an isolated change. The earlier rejected handler experiments retained the
timestamp/scan work and did not test this new hypothesis. Disabling the global
scanner is not the proposed experiment.

Acceptance should include a real indexed-entry AB/BA comparison, stable catalog
identity, one item with the correct cover and recent ordering, matching pixels,
normal exit and same-process sleep/wake restoration. Repeat the CPU diagnostics
separately to establish whether KPP work actually decreases.

### Consolidate the native-read supervision overhead

The current [launcher](../../../packaging/kindle/launch.sh) creates shell,
watchdog, pipe-collector and record-publication processes around each bounded
native read. In the primary batch, indexed portrait calibration has a 650 ms
outer median; its internal last-event median is 252 ms. The paired surrounding
interval has a 385 ms median, compared with 158 ms on the temporary route.

Evaluate one bounded native supervisor that reduces process launches and file
handoffs while retaining ownership, PID birth/session checks, permission nonce,
the 129-byte overflow observation, timeout group termination, and publication
checks. The internal last-event and process-reap boundaries differ; the whole
385 ms is not proven removable shell overhead. Much of its increase may be the
same contention addressed by item 1, so remeasure after that item and do not
add the two budgets as an expected saving.

### Improve the final cover publication and carefully bounded cache preparation

The current indexed cover tail has an 805 ms median. After all six cover jobs
finish, the median interval until the accepted complete-cover frame is 197 ms.
That includes dispatcher scheduling, the publication deadline, drawing and
submission; it is not a pure fixed sleep.

The [cover worker](../../../src/app/cover_jobs.rs) is intentionally gated until
the first business frame, and the [main dispatcher](../../../src/app.rs) groups
background publication into 300 ms intervals. First test an earlier final
publication only when all visible jobs have completed. Then separately assess
limited cache preparation during an already-required hardware wait, publishing
only ready results without delaying the first frame.

Keep the previously demonstrated first-frame priority and cover coalescing
benefits. Simply admitting all cover work earlier or deleting the 300 ms window
can increase contention and refresh count. Neither new variant has a measured
gain yet; qualify both first-frame and complete-cover endpoints.

### Remove redundant launcher path resolution

`SCRIPT_PATH` runs `dirname`, `cd`, `pwd` and `basename` in both the waiting
entry and the `--foreground` worker, but only the entry uses it. Resolving it
inside that parent branch is a small, isolated candidate. No saving has been
measured, so it remains lower priority than the previous three items.

Further layout/font micro-optimization should follow the contention fix: the
current first-render wall time includes substantial runnable wait. The checked
initial framebuffer/input barrier and current CPU governor are retained.

## Evidence and reproduction

All builds, checks, analysis and device probes ran through `ssh test-env`.
The two completed batches contain 26 successful launches in total. Original
documents, native state and lighting were restored, installed app/launcher/
cover files and the user library matched their original hashes, and both fresh
leases were released before host analysis. Two earlier acquisition attempts
were rejected because another task held the lease; neither ran a device
command, and neither is a startup failure or latency observation.

See [verification and artifact identities](verification.json). The application
is SHA-256 `570887a0e5603ce2f6d820cf87b2f4e9b385514f71e9a729e85c8ead2f6729fc`,
the helper is `84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d`,
and the launcher is `1ae50f6bd7a8262cce185dea08c69f5bc317ed9b7bd7945c55c1f5239dcdd385`.
The installed shared handler was unchanged.

[analyze.py](analyze.py) exports only allowed measurement fields from the
private batches. [system-sampler.c](system-sampler.c) is the diagnostic sampler
source. Its ARM build used Zig 0.15.2 with `-target arm-linux-musleabihf
-mcpu=cortex_a8 -std=c11 -O2 -Wall -Wextra -Werror -static -s` on test-env.

```sh
python3 -I analyze.py \
  --route-batch /root/aidoku-startup-attribution-20261117/private/route-attribution-b9aef85e \
  --scheduler-batch /root/aidoku-startup-attribution-20261117/private/scheduler-diagnostic-0739ab6e \
  --output-dir /root/aidoku-startup-attribution-20261117/report
```

There is no new production variant to deploy in this report. Each proposed
optimization still requires its own controlled comparison before a speedup can
be claimed.
