# Physical startup follow-up, 2026-11-20

**Later result (November 21):** the [ten-pair confirmation](../startup-followup-20261121/README.md)
of this post-SEND binary passed for empty-cache covers. A separate experimental
warm build did not improve startup and was rejected. The six-pair results
below remain the original screen and are not part of the confirmation.

**Result:** Deferring worker startup until the first full-frame submission
kept the warm Library essentially level. On a synthetic shelf with an empty
thumbnail cache, the corrected build submitted all seven cover slots **709 ms
sooner at the paired median**, faster in all six pairs. This measures the
staged launcher, not a tap on the stock Library tile.

| Independent six-pair screen | Usable Library: paired saving (wins) | All covers: paired saving (wins) |
| --- | ---: | ---: |
| Warm thumbnails | +3.403 ms (3/6) | +9.917 ms (3/6) |
| Empty thumbnail cache | +38.412 ms (5/6) | +709.297 ms (6/6) |

Cold complete-cover savings ranged from 671 to 791 ms. The candidate's
complete-cover median was 4.019 s versus 4.735 s on control; subtracting
those separate medians is not the paired statistic. All 24 timed visits and
the four independent picture arms passed their input, cover, cache, exit
and restoration checks. Both the software captures and live framebuffer
matched in the body and observable right status area. The cold screen met
the 100 ms / five-win rule; warm did not. Neither the 300 ms warm target
nor the 1 s cold target was met. [All pairs and outcomes](screen-post-send.json).

**Decision:** keep the existing daily installation. A separately prepared
ten-pair confirmation or genuine native-indexed entry study would be new
evidence. Do not mix its samples with these pilots or screens. The last
screen closed and released the lease at **16:59:53 Asia/Shanghai**, then
the waiting WeRead operator was notified. No Aidoku run is scheduled.

## CLI and pixel kernels

**Passed on the shared Scribe.** From 16:08:12 to 16:08:54 Asia/Shanghai a
single operator used the test-env host's project/thread/token lease and the
operator's previously trusted SSH host key. No person had to tap or unlock
the device. This is a new physical check, not a reconstruction of the lost
private trial.

- The stock app was foreground, power active, status bar and window manager
  running, and none of the three projects held its device-side launcher lock.
- The installed Aidoku launcher matched the R3 Git bytes via `cmp`. This does
  not identify the entire installed program tuple or the shared registration.
- The staged corrected ARM binary printed `aidoku 0.1.3` and the six expected
  default settings against an absent private data path. It did not create it.
- On the physical ARM CPU, the application build's NEON object matched the
  scalar reference for 16,777,216 RGB triples, 128 tails and 1,400 row cases
  (2,798,527 float sums). No startup time was measured in this batch.
- The temporary files were removed. A separate closeout observed the stage
  absent and stock native control intact. The owner/token-checked release
  succeeded. No shared program, registration, user library or cache was changed.

The [device script](operator/clean-worker/hardware-smoke.sh) is paired with
the [worker coordinator](operator/clean-worker/run_hardware_smoke.py).
Preflight, build and analysis do not take the lease. A held foreign lease
must never be removed to start a new run.

## Staged foreground comparison

The [new two-pair protocol](operator/clean-worker/staged-pilot/README.md)
measures from starting the staged foreground launcher, **not** from a stock
tile press or URI dispatch. Every visit used an isolated synthetic six-book
library. No user book or daily installation was replaced. Positive savings
mean less waiting than the currently installed control in the same pair.

| First candidate | Paired usable savings (AB, BA) | Paired complete-cover savings (AB, BA) |
| --- | ---: | ---: |
| Warm thumbnails | −66.672, −141.114 ms | −70.532, −130.286 ms |
| Empty thumbnail cache | +17.738, −95.439 ms | +712.157, +568.884 ms |

Both regimes verified the expected six hits or misses, seven cover slots,
and matching body and right status pixels in independent software and
live-framebuffer captures. The **warm pilot did not qualify**: its initial
cleanup did not establish the stage lock. A bounded nonblocking closeout
under its original lease then verified identical protected state, restored
stock pixels and stage removal before release. The cold pilot passed its
original closeout. Two earlier warm plans ended at admission without
starting either arm. Their failures and all four measured pairs are retained
in the [first-candidate record](pilot-v1.json).

The cold complete-cover median saving was **641 ms**, below the 1 s target;
both warm pairs were slower. Warm traces placed the candidate's first full
SEND 52–83 ms after starting the preparation workers, whereas the control
started them at SEND. The checked initial display-to-input interval remained
about 366 ms. This motivates testing the same cover changes with background
preparation deferred to the existing successful-SEND hook. It does not
establish that worker contention caused the entire first-screen difference.

## Deferring preparation to SEND

The second candidate changes only the initial scheduling on top of the
NEON build correction. It no longer creates workers in the lifecycle control
callback before the initial paint. It still starts them after a successful
full SEND and may register already-available faces during the checked fence.
The host ARM release, exact linked C-kernel parity, no-NEON CLI and all
23 application startup tests passed. All four fresh measured candidate
traces started workers **after** SEND (0.34–2.88 ms), as intended.

In the [independent second pilots](pilot-post-send.json), warm first usable
changed by −125 and +94 ms; cold complete covers improved by 766 and 675 ms.
Both pilots passed the cache, software/live pixel, native restoration and
lease checks without recovery. Do not combine these observations with the
first revision or with subsequent screening pairs.

## Worker records

The private handoff on `test-env` lists separate directories for the smoke
receipt, the first candidate's failures and same-owner recovery, the revised
binary and its source delta, ARM parity and host-test logs, and the independent
screening runs.
The per-batch plans, operations, synthetic trace/picture archives, analyzer
outputs and release receipts stay private. None contains a daily install
transaction. Recovery of the old private tile experiment remains separate.
