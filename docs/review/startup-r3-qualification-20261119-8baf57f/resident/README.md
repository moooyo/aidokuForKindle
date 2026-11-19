# R3 quick-return qualification

The coordinating operator ran the device batches; this directory records
independent replay on `test-env`. Each visit uses the formal indexed Library
entry and a newly admitted GUI. The optional keeper holds cover pixels only.
Qualification observations and the later balanced timing screen are separate.

| Boundary | Result | Public evidence |
| --- | --- | --- |
| Immediate second open | Passed | [Observed options, actions and resources](immediate.json) |
| Ordinary exit with retention configured | Passed | [Observed options, actions and resources](ordinary.json) |
| Natural expiry before the second open | Passed | [Observed options, expiry and fallback](ttl.json) |
| Short sleep and wake on the second GUI | Passed | [Process, input and visual qualification](wake.json) |
| Six-pair second-open latency screen | Valid; speed gate not met | [All pairs, mechanisms and resources](screen.json) |

## Immediate second open

The frozen four-episode qualification and its eight visits passed. Both the
warmup and qualification candidates published one keeper during cooperative
shutdown, reclaimed it once during the second entry, and used six retained
covers. The disabled control published and reclaimed none. Every second GUI
had a different process identity; both visits returned native Home, and each
second ordinary exit left no keeper.

The candidate qualification observed keeper RSS of 2,360 KiB and PSS of
2,348 KiB, with one CPU tick over an 8.71-second same-keeper observation.
Those figures include process overhead and sampling costs. They do not measure
peak memory, idle energy, optical e-ink completion or a repeatable speedup.

The immutable source limits the serialized cover payload to 2 MiB, at most
six covers, and a nonrenewable 45-second `CLOCK_BOOTTIME` lifetime. These are
source-bound limits, not a 2 MiB RSS ceiling or an expiry observation in this
immediate case. See the [implementation and prior host qualification](../../startup-all-20261119/resident/README.md).

## Ordinary exit with retention configured

The second four-episode qualification also passed. Both candidate visits
actually read `resident=45`, but ordinary exit published no keeper. All
publication, reclaim, rejection and retained-pixel-hit counts were zero;
all three process observations per episode found the keeper absent. Every
second open used a new GUI and both visits returned native Home.

This verifies that enabling the feature does not turn the ordinary Exit
action into Quick return. It does not establish a latency advantage.

## Natural expiry

The candidate qualification first published one keeper and observed it in
native Home. It then waited for natural expiry; the keeper was absent before
the second indexed request. The new GUI reclaimed no keeper and used no
resident pixels, completing the normal disk-backed startup and ordinary exit.
The disabled control remained absent throughout. The warmup's successful
retention is retained separately and is not counted as an expiry result.

The explicit expiry wait was 41.57 seconds. That interval starts after the
first exit and Home observation, rather than at keeper creation. The audit
also checks its position against the first shutdown and the 45-second
deadline; the explicit wait is not a measurement of the entire keeper lifetime.
Independent receipt inspection observed the keeper absent 45.080 seconds
after publication, using a 250 ms polling interval. The sealed TTL branch
only observes the process; it does not execute the deliberate-loss or kill
path. This supports natural expiry within the predeclared observation window.

## Short sleep and wake on the second GUI

Both arms retained the second GUI's identity across its own short sleep and
wake. That identity remained distinct from the first visit. Input-handle
observations were `2 -> 1 -> 2` in each arm, with the parked and restored
platform states in the required order. The full Library body and observable
right toolbar were exactly equal before and after wake within each arm;
the checks retained their original regions and nonuniformity requirements.

The candidate reclaimed one keeper and used six covers during the second
entry before sleep. It did not publish another keeper during the second
ordinary exit. All four episodes completed and returned native Home. This
is a sleep/wake and control-ownership result, not evidence of reusing the
first GUI across two Library requests or of an optical refresh speedup.

The frozen device script checked epoch, nonce and sleep/resume-ready
identities before emitting the platform sidecars. The host independently
bound that script and checked its successful completion, retained owner
identities and ordered markers. Raw epoch values were not saved separately;
they were not independently recomputed from a second host-side record.

## Six-pair latency screen

All six preselected pairs completed, with three in each order. Every timed
candidate published one keeper, reclaimed it during a newly admitted second
GUI, and used six cover images; every control followed ordinary exit without
retention. No zero-hit or fallback candidate was removed: none occurred in
these six pairs. Both warmups remain in the public case-level record.

Positive paired savings below mean the candidate was faster. The primary
endpoint saved 172.674 ms at the paired median, but only four of six pairs
were faster. The screen requires at least five faster pairs, so it failed
even though its median exceeded 100 ms and the second usable-frame regression
stayed within 50 ms. No ten-pair confirmation is warranted, and no confirmed
speedup is claimed.

| Software endpoint | Paired median savings | Faster pairs |
| --- | ---: | ---: |
| Second usable Library | -19.003 ms | 3/6 |
| Second complete covers | +172.674 ms | 4/6 |
| First shutdown to native Home | -165.745 ms | 1/6 |
| Second shutdown to native Home | -466.925 ms | 3/6 |
| Both request-to-cover intervals together | +97.611 ms | 4/6 |
| Entire scripted two-visit span | -1,315 ms | 1/6 |

The last row also contains the intentional in-application dwell, observed
Home gap, resource-observation windows and both exits. It is not a second
startup measurement. The public dataset retains each of those components
and every unfavorable pair, rather than counting only the saved cover decode.
The feature remains an optional functional path, with its existing factory
default disabled.

Across the six timed candidates, observed keeper RSS ranged from 2,152 to
2,360 KiB (median 2,260), and PSS from 2,140 to 2,348 KiB (median 2,248).
The same-keeper Home observations covered 8.56 to 8.72 seconds and recorded
zero or one CPU tick. These are sampled resource observations, not maximum
memory or an energy measurement.

## Evidence handling

The original analyzer and frozen device inputs remain unchanged. The
[public exporter](summarize_resident.py) ran only on `test-env` and admits
explicit numerical, boolean, option and program-identity fields. Private
process identities, absolute clocks, user pixels and image digests remain
on the verification host. The accepted report binds the exact R3 application,
launcher and portrait helper plus the immutable plan digest.

An [independent raw-receipt review](raw-receipt-audit.json) also checked the
four cases and sixteen device commands in each qualification batch, plus the
screen's fourteen cases and twenty-six commands. Every visit had exactly one
successful catalog notification. The review checked distinct GUI identities,
exact protected snapshots, and matching acquisition/release evidence. The
immediate keeper descriptor checks admitted only null standard streams and
one Unix listener. Current shared use by a later operator was not mistaken
for a lease left behind by these completed batches.

The screen was separately recomputed directly from all 28 application traces
and shell-marker records using integer microseconds. The raw-receipt summary
keeps half-microsecond paired medians before the original report's three-place
millisecond rounding. It reaches the same failed screen decision.

The functional qualifications and completed screen do not establish a
repeatable speed benefit. These measurements end at software or driver
acceptance, rather than optical panel completion.
