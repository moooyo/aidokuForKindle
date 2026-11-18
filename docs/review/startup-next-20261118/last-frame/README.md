# Final visible-cover grace trial

Outcome: the [six-pair physical screen](../evidence/aidoku-next-frame-screen-91a7bc28.json)
did not establish a benefit. The grace path did not trigger in a timed candidate
launch, and the complete-cover endpoint was 77 ms slower at the paired median.
This trial is not included in the installed application.

The candidate applies to an ordinary Library open. Once the first business
frame has been accepted, the existing cover worker normally publishes partial
results at 300 ms. A partial software submission can leave a later completed
frame waiting for that earlier buffer to settle.

In [the motivating twelve warm launches](eligibility.json), six showed an extra
partial frame. Four satisfied the narrow workload pattern at the nominal cut:
five earlier local jobs had hit existing derivatives; the sixth local job had
started but had not yet reported a hit. It was finished at 323.692, 329.865,
336.879 and 370.640 ms. Only three of those four observed completion times fall
within a 40 ms extension. The other two partial frames still had multiple jobs
outstanding and were intentionally outside the proposed mechanism. The 300 ms
cut is reconstructed from first frame; dispatch and submission are distinct
boundaries. These associations do not measure a speedup.

The standalone [candidate patch](candidate.patch) keeps the first-frame gate,
ordinary publication and urgent UI updates. It delays only a cover-only partial
publication, once, when one known local job is in flight, its queue is empty,
all other IDs in the unchanged initial projection have delivered confirmed local
cache hits, and there is no retry or state change. The final job is predicted
to be fast; its own cache status is not known yet. A hit is never assumed for
display, and job completion still goes through the normal source and lifecycle
checks. A single event-loop timer requests a poll by the original deadline plus
40 ms, regardless of whether that last job wakes the application. No model or
frame is sent by the timer itself. Scheduling can make the observed dispatch
later than its requested deadline. Foreground action, another kind of dirty
state, source/presentation change, sleep or failure uses the existing path.

The patch is isolated from the supervisor, cached-cover prewarm and cold-source
experiments. There is no environment switch or special option for the candidate;
the physical comparison uses the installed baseline ELF from `78c5b2b` and this
standalone ELF, with the same indexed item, shared registration and launcher.
Frame attribution requires the actual marker sequence and complete projection;
all planned pairs count even if neither variant submitted a partial frame. See
[the trial analysis contract](analysis/) and [selection thresholds](../PLAN.md).

The source was replayed on test-env against the exact deployed baseline and all
five edited-file digests matched the build. ARM SHA-256:
`153e018635461d00a6b0378a391f76adf929178fd75b787b2088e9753d3fcd3f`
(53,379,344 bytes, ARM EABI5 hard float). The complete Linux Kindle-feature
library suite passed: 821 tests, five existing ignored. It includes controller
regression cases for stale owners/visibility, non-cover updates, unknown prior
hits, expiration and completion. The patch also retains the mechanical rustfmt
changes present in the exact build. [Host evidence](host-verification.json).
