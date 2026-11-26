# Scribe first Library screen: overlap the toolbar reads

## Physical result: foreground launcher only

On the physical Kindle Scribe, the candidate reached the same-frame driver
acknowledgement **53.363 ms sooner at the paired median (3.19%)**, with
three of four pairs faster outside the request brackets. The `SEND` endpoint
was **53.905 ms sooner at the paired median**. Both order-specific medians
favored the candidate. Keep the candidate startup change; this comparison does
not establish a win from an ordinary stock Library card or on physical PW6.

A is the `4b7bb02` startup behavior; B adds the background toolbar probe and
the narrower fallback. Both were ARM release builds, with the same opt-in
timing and exact-marker driver wait. The completed run invoked the installed
production launcher's behavior through a temporary foreground wrapper adding
`--offline`. Its two books came from an isolated non-demo Library. The device
clock bracketed the foreground launch request, not the SSH round trip or an
XTEST release. **The stock picker/card dispatch is not included.** The finish
is the first accepted frame containing the populated Library, acknowledged by
that exact frame's driver marker. A loading frame is not counted as the first
screen; elapsed time spent loading is still included. `WAIT` is a driver
acknowledgement, not a direct observation of optical settling.

One warmup per arm was excluded. The four measured fresh-process pairs ran
in AB, BA, AB, BA order, reusing staged files without clearing caches. All
numbers below are request-bracket midpoints, in milliseconds. Positive `A − B`
means the candidate is faster.

| Pair | Order | A: SEND | B: SEND | A: WAIT | B: WAIT | WAIT saving | WAIT saving bounds |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | AB | 1360.639 | 1322.652 | 1652.767 | 1615.558 | +37.209 | +33.525 to +40.892 |
| 2 | BA | 1393.325 | 1323.502 | 1685.176 | 1615.659 | +69.517 | +65.711 to +73.322 |
| 3 | AB | 1342.263 | 1354.502 | 1635.356 | 1648.617 | −13.262 | −16.718 to −9.805 |
| 4 | BA | 1371.322 | 1263.536 | 1662.854 | 1555.583 | +107.271 | +103.114 to +111.428 |

The separate arm medians were **1657.810 → 1615.609 ms (WAIT)** and
**1365.981 → 1323.077 ms (SEND)**. The median paired WAIT bounds were
**49.618 to 57.107 ms**; these bound the measured clock brackets, not sampling
uncertainty. The AB and BA order medians were **11.974 ms** and **88.394 ms**
sooner on WAIT respectively. Warmup WAIT midpoints were 2306.003 and
2366.341 ms (A/B), both slower than their subsequent processes and not evidence
of a cold-start gain. [Sanitized per-launch records](startup-toolbar-overlap-20261126.json)
retain all ten launches, both endpoints, the timing bounds, the arm and pair
statistics, and restoration results. The original installation and data were
restored, stage absence was checked independently, and the original owner/token
verified release at approximately 15:48:24 China time. Temporary builds,
upload archives and stock screenshots were removed; private timing and closeout
records and the sanitized report were retained. No candidate was installed.

## What changes and what stays ordered

The old initial-frame callback waited synchronously for its bounded helper
probe; if that sample failed or expired, first Library projection synchronously
queried the full device status, including charging, brightness and warmth.
Those three reads are not used by the Library toolbar. The new callback starts
one bounded, read-only worker. The view takes a completed, fresh sample without
joining it. An unfinished, failed or stale sample falls back to the same
battery/Wi-Fi values without reading those unrelated properties. The clock
still uses stock `date` and free space is sampled when building the view. The
full device status path is unchanged, and an interrupted startup cannot publish
the worker's stale reply.

The native observer, fresh portrait check, display ownership, accepted
loading-frame fence, touch synchronization, font registration, lighting and
store initialization remain prerequisites. Their elapsed intervals can overlap
background work; do not add them twice or treat all wall time as CPU time.

## What took time on the Scribe

| Nonoverlapping stage | A median (ms) | B median (ms) |
| --- | ---: | ---: |
| Foreground request → Rust entry | 455.963 | 448.703 |
| Rust entry → loading frame SEND | 249.227 | 250.293 |
| Loading frame SEND → initial display/input fence | 363.994 | 364.690 |
| Fence → controller attachment begins | 0.278 | 48.746 |
| Attachment begins → Library scene requested | 110.444 | 66.826 |
| Library scene requested → Library SEND | 198.935 | 142.741 |
| Library SEND → matching WAIT | 291.989 | 292.532 |

Independently computed medians do not add to a launch. The worker-to-GUI
portion was roughly 445 ms (A) and 430 ms (B), subject to 10 ms process-tick
resolution. The loading display/input fence and the Library driver wait were
essentially unchanged. Neither was shortened or bypassed. The software draw
used about 61 ms of the GUI thread on both arms; its elapsed time was about
92 and 90 ms respectively. Panel timing and software draw time do not explain
the observed improvement.

All ten startups used the toolbar snapshot. The prior callback spent
**50.387 ms on the GUI thread** at the control median; the candidate's worker
spent **48.284 ms** off that thread. The later toolbar read still sampled time
and free space, and did not get faster (3.655 → 5.117 ms). The reduced fallback
was not taken in these physical launches and cannot explain the measured gain.
Font registration finished later on the candidate (62.483 → 411.822 ms after
the loading SEND); metadata readiness was similar. The median wait to settle
the previous framebuffer update fell **78.267 → 29.115 ms** (paired median
saving 50.711 ms), and the first Library draw cost was similar. These events
changed their ordering and can overlap. The observed framebuffer wait is a
large contributor to this cohort's first-frame gain, not evidence that the
underlying driver operation itself became faster.

## Prior stock-card and emulator attribution

The [earlier stock-card Scribe cohort](startup-library-transition-20261122/README.md)
recorded 3.081 s at the median from release to Library submission. Its loading
frame to input fence, attachment to scene readiness, and scene readiness to
Library submission medians were 365, 159 and 698 ms. A
[separate physical draw diagnostic](startup-release-draw-20261122/README.md)
measured 418–778 ms elapsed in drawing, of which 96–97 ms was GUI-thread CPU.
These are separate historical cohorts, not control samples for this experiment
or evidence that the whole rendering interval is removable.

The matched control reached a populated Library and restored the desktop on
both firmware profiles with the query helper. In those two emulator runs the
post-loading probe ran for 103 ms (Scribe) and 331 ms (PW6), and the later
synchronous toolbar projection took 186 and 173 ms respectively. Both probes
missed the startup snapshot path. The probe elapsed time exceeded its nominal
80 ms budget under emulation; the wall clock includes scheduling and cleanup.
These values establish the call ordering and fallback, not physical gains.

## Verification and ordinary-card attempts

After `test-env` recovered, sequential Linux validation passed 876 library
and 25 binary tests (8 opt-in library tests ignored) and six firmware harness
unit tests. The production Kindle feature set also passed `cargo check` with
startup diagnostics disabled. The earlier font-manifest failure compared the
raw locale fallback against the nine build-expanded gzip assets. The test
scope was corrected; font bytes and runtime registration did not change. The
earlier candidate emulator attempts stopped at the stock active-state gate
before calibration and did not exercise the candidate's Library path.

The two staged ARM release programs passed stock and foreground harness
preflight. The candidate reached the populated Library and restored the stock
firmware desktop with the packaged helper on both Scribe and PW6. PW6 also
passed without the helper; a separate helperless Scribe attempt stopped at
portrait calibration before the application started. Both guests' test data
were removed and both guests were stopped. This is a functional check, not an
emulated speed comparison.

The first physical batch attempt stopped at SSH transport and yielded no
samples. Recovery with a short command found no test-owned state, independently
checked absence, closed both SSH connections and released the lease with the
original owner and token. The second batch used short commands and bounded
stdin framing. Transport and stock navigation succeeded, but the historical
artwork guard could not qualify the current card. No card was pressed and no
sample qualified. The original installation and stock state were restored,
independently checked for residue, and the fresh lease was released with its
own owner and token. A subsequent navigation-only visit captured the current
Library and independently verified restoration and exact release, without
pressing the card. Offline, the historical guard reported a missing tile; the
current image contained exactly one match to the repository's official cover.
A refreshed guard passed the actual ARM matcher's fixture replay and then
opened the ordinary stock card on the Scribe. The control warmup, excluded
in advance, submitted the first populated Library at **2860.126–2861.579 ms**
and acknowledged the same update at **3153.343–3154.796 ms** after button
release. The old catalog check then stopped the batch: normal indexing had
changed the Aidoku row UUID and modification time. The thumbnail, canonical
path and type, document bytes and inode, other installation files and data,
stock tab and pointer, and lighting were verified. Nothing was written to
the stock catalog to undo the legitimate indexing. All test-owned staging
was removed; independent absence and exact lease release were confirmed.
The next run distinguished mutable indexing metadata from the identity of
the original card and retained strict checks on the other files. Its live
visual guard could not confirm a unique card before the first warmup, so it
stopped without a press, restored stock state and passed independent absence
and exact release. These interrupted stock batches provide no qualified
paired timing. The completed foreground protocol above is a separate
physical comparison and is not mixed with ordinary card release timings.
