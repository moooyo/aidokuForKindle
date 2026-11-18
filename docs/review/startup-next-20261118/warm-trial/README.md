# Cached local cover prewarming trial

**Decision: do not ship the candidate.** On the indexed Library route the
median paired saving to all visible covers was 200.566 ms, but only 4 of 6
pairs improved (the [predeclared screen](../PLAN.md) required at least 5).
Median paired saving to the first usable frame was 9.072 ms. Only one candidate
launch actually consumed a prewarmed image, and its complete-cover frame was
297.792 ms slower than its control. No confirmation or deployment followed.

## Why five candidates made no attempt

All six enabled launches had complete traces. Metadata was ready 121.855 to
125.881 ms after the initial SEND. The 300 ms prewarm budget begins in the
initial SEND callback, before the worker spawns. Initial input readiness was
365.464 to 366.608 ms after SEND. The five runs with no attempt also had no
`startup_cover_warm_stopped`. The records do not support the explanation that
the 300 ms budget had already expired when metadata became ready.

An attempt is recorded *after* obtaining a plan, opening a separate read-only
store, selecting books, and confirming a local source and absolute cover path.
Errors from this optional selection path are discarded; in particular this
connection has a zero busy timeout. A completed plan that returns after a
monotonic deadline would emit `startup_cover_warm_stopped`; no such stop occurred.
All final views had the same current item and visible local books. Final state
does not identify the transient connection or selection outcome. The recordings
cannot distinguish an absent plan, a store error (including a busy database),
or filtering before the attempt mark. A SQLite lock is a plausible hypothesis,
not an observed failure. A future experiment should record those branches
separately before changing the time limit or retrying database reads.

Pair 1 made two attempts (142.895 and 191.448 ms after initial SEND). Its
first image entered the warm cache at 191.427 ms and was consumed once by the
normal cover path. The second image was not warmed. The other five pairs had
no attempts, successful prewarming or warm-image consumption.

## Intermediate frames explain the apparent endpoint difference

| Pairs | Side that submitted an extra partial cover frame | Complete-cover outcome |
| --- | --- | --- |
| 2, 3, 5, 6 | Control | Candidate faster in all four, with no prewarm attempt |
| 1, 4 | Candidate | Candidate slower in both |

Across all twelve timings, first Library SEND to complete covers was 782.898 ms
median with an intermediate frame, versus 438.889 ms without one. In pair 1 the
candidate submitted a six-of-seven-slot frame at 485.328 ms after the first
Library frame; its final frame spent 187.098 ms settling the previous buffer.
Each pair has different intermediate-frame counts, so the observed 200.566 ms
does not isolate a prewarming benefit. These six pairs do not establish that
prewarming changes the frequency of intermediate frames or predict a percentile.

[Screen breakdown](screen-breakdown.json) contains the twelve relative timelines,
all paired cover savings and the frame counts. It excludes user metadata,
identifiers, images and absolute clocks.

## Reproduction

On `test-env`, apply [candidate.patch](candidate.patch) to an isolated checkout of
`78c5b2bc09a469133c886eb0d73aaf9ed63195b2`. All eleven replayed Rust files
matched the frozen, measured ARM source byte for byte. The candidate default
allows at most two covers; `--startup-cached-covers 0` selects the control.
Both sides keep the checked display/input barrier and verify source bytes again
in the normal cover workers after the first frame.

[Verification](verification.json) records the source, ELF, package-independent
host validation and physical report identities. The candidate passed 822 library
tests (5 existing ignored) and 22 startup tests. The [synthetic six-cover host
diagnostic](host-six-validation.json) verified the local cache mechanism and
matching pixels, but its Linux timings are not Kindle measurements. The patch
is retained as research evidence only and is absent from the production source.
