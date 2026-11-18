# Indexed Library startup after 78c5b2b

This study compares fresh application processes launched from the same real
indexed item on the physical Kindle. It separates opening an existing warm
library from creating six missing local cover thumbnails. Seven on-screen
slots display six distinct local covers. The old production reference is
`78c5b2bc09a469133c886eb0d73aaf9ed63195b2`, application `62de2771...`.
See the [prospective decision rules](PLAN.md) and [per-batch observations](evidence/index.json).

## Selected application

The deployed application (source commit `c6867d1`, ELF `9d92dc4c...`)
combines a single full decode of eligible
local originals on a thumbnail miss with an earlier status observation for the
first Library screen. The exact combined executable was compared with the
installed baseline in both cache regimes. No result from the rejected cold-only
binary was used to qualify it.

| Workload and endpoint | Installed control median | Combined candidate median | Median paired savings | Faster pairs |
| --- | ---: | ---: | ---: | ---: |
| Six missing thumbnails, independent confirmation: complete covers | 10.621 s | 8.411 s | **2.442 s** | **10/10** |
| Same launches: first usable Library frame | 2.252 s | 2.227 s | 0.024 s | 6/10 |
| Same launches: exit to native Home active | 3.633 s | 3.568 s | 0.110 s | 7/10 |
| Six cached thumbnails: first usable Library frame | 2.160 s | 2.173 s | 0.019 s | 3/6 |
| Six cached thumbnails: complete covers | 2.907 s | 2.676 s | 0.113 s | 3/6 |

Every independent cold pair improved complete covers, by **1.766 to 2.847 s**.
The separate preceding cold screen improved that endpoint in all six pairs by
a median 2.696 s. Warm startup met the prespecified regression guard; three of
six wins on each endpoint do not demonstrate consistent acceleration. Exit
times fluctuated by around a second between individual pairs. The roughly
2.4 s saving belongs specifically to generating previously missing local art.

On a cold miss, the renderer used to decode some originals once to validate
them and a second time to produce the cover. Eligible static PNGs and JPEGs
now use the bounded cover decode as pixel validation. Source hashes, file
identity, dimensions, memory limits and rendered output validation remain in
place. The PNG path also checks chunk boundaries and the critical end marker;
unsupported formats and damaged optional metadata retain the documented
fallback. Malformed or missing PNG end chunks are rejected, including cases
where an older decoder might have stopped after the last pixel. No partial
image is published. Network cover retrieval is unchanged.

After the driver accepts the initial full screen, the application makes a
bounded read-only request for Wi-Fi and battery while the panel updates. It
consumes only a fresh completed observation on the Library route. The clock
and free space are still read when constructing the view. Failure, expiry or
a power transition retains the normal query path. Driver acknowledgement and
the input gate are unchanged. In the ten confirmation launches, all ten used
this result; the first projection went from 74.8 ms to 40.4 ms (separate
medians), and no deferred synchronous status query was seen before complete
covers. The initial display/input interval remained about 365 ms in both arms.
This is not a second speedup to add to end-to-end savings.

The code is bound to the measured ELF by the [source chain](status-trial/verified-source-chain.json).
The [implementation and host checks](status-trial/README.md) include source
identity, the 825 passing library tests (six ignored), 22 passing startup tests,
17 native query process fixtures, and pixel-identical Scribe/PW5 Library demos.
Independent physical pixel qualification compares both the content beneath the
changing clock and the **right side** of the real status bar containing battery
and Wi-Fi. Both areas matched. All recorded raw user pixels stayed private.

## Installed version

On 2026-11-18, `promote-f2889bdb575f` backed up and replaced **only**
`aidoku/app/aidoku`. The normal indexed document, installed launcher, accepted
logo and shared handler registration were unchanged. The actual installed
Library item was opened with normal user data and preferences, without the
research bridge. Images before and after the install and after a same-process
short powerd sleep/wake matched, including the right side of the status bar.
Notification, return to native Home and lighting restoration passed; the device
lease was released. The installed-default smoke was not a timing trial.

The [default-options lifecycle qualification](evidence/default-lifecycle.json)
also checked input release/reacquisition, ordinary exit, catalog identity and
the uninstrumented program identity before installation. The first host replay
erroneously expected a recovery marker on stdout after the frozen device script
had consumed and compared it. Its successful checked branch, successful
command exits, identical before/after snapshots and release receipts verified
recovery; no device record was rewritten or waived. [Installation, backup and
package identities](evidence/deployment.json) and [worktree-to-build
comparison](evidence/worktree-source.json) link the result to all 360 production
source inputs. The ZIP passed 211 checks. The ZIP and KPM payloads have the
same program delta; other installed members, including scripts, are unchanged.

## What remains worth measuring

The [remaining cost intervals](evidence/remaining-costs.json) show that the
final cold confirmation still spends a median **6.120 s** after the first
Library frame. Six serial cover jobs occupy **5.872 s** in aggregate (medians
of per-run totals); across runs this accounts for around 96% of the tail.
Their individual median elapsed times, in the same anonymous fixture order,
are **1.089, 2.327, 0.728, 0.481, 0.745 and 0.502 s**. The second job is the
largest. The single-decode markers enclose validation and rendering work, not
just CPU decode. Traces do not yet split source reads, inflate, resize, dither,
encoding and publication into independent removable costs.

The next experiment to prioritize is generating one visible thumbnail at
import time, or during bounded idle time after import. Start with one large
local cover, retain disk limits and byte identity, and compare first-open
latency as well as added import time and energy. Successfully moving half
of that 2.33 s job before opening would shift roughly **1.2 s** in this
fixture; the full job is a work budget rather than a guaranteed saving, and
there is no benefit when the thumbnail is already cached. A separate
diagnostic should split that job into the operations above before changing
image quality or adding decode workers.

With warm covers the final candidate's median first-frame render is **188 ms**
and the first-to-complete-cover tail is **446 ms**. On the evidence here,
neither simplifying the tested shell transaction nor speculative prewarming
or delaying a partial frame reliably removes another large block. A targeted
layout/glyph profile is a lower-priority warm-start experiment; a 20% saving
in that measured rendering interval would be about 38 ms, not seconds. Keep
the checked initial display/input barrier. Retaining an application after
returning to Home would address repeat opens only and has no measured memory,
battery or latency payoff in this investigation.

## Independent candidates

Times run from the native file-URI request to the first submitted Library
frame with software input available, or to driver submission of all visible
covers. They are not optical completion times. Positive paired savings mean
control minus candidate; the median of those within-pair differences can
differ from subtracting the two separate version medians.

| Trial and primary endpoint | Pairs | Primary median paired savings | Result |
| --- | ---: | ---: | --- |
| Native portrait supervisor, usable first frame | 6 | 53 ms; 4 faster | Below the 100 ms / five-win screen. Keep the existing launcher. |
| Two cached covers prepared early, complete covers | 6 | 201 ms; 4 faster | Only one launch actually consumed prewarmed pixels; it was slower. No adoption. |
| Up to 40 ms grace for the last local cover, complete covers | 6 | -77 ms; 3 faster | The grace period never triggered in measured launches. No adoption. |
| One full decode of eligible local originals, cold complete covers | 6 | 2,401 ms; 6 faster | Screen passed. |
| Same single-decode build, independent cold confirmation | 10 | 2,450 ms; 10 faster | Cold benefit confirmed; 2,021 to 2,985 ms saved per pair. |
| Same single-decode build, separate ordinary warm regression check | 6 | Usable first frame -67 ms; complete covers +40 ms | First-frame regression exceeded 50 ms. Standalone build `d9be7683...` not eligible for installation. |

The original cold-control median was 10.787 s to complete covers in confirmation;
the single-decode candidate reached the same endpoint at 8.336 s. The observed
exit delta on this workload was 65 ms slower at the paired median; no exit
improvement is claimed. Actual framebuffer pixels below the clock matched in
the independent cold-render qualification. Both arms of every timed cold open
began with a separate empty application cache. The existing user cache was never
removed. This is not a network-download or post-reboot benchmark.

In the separate warmed comparison, both arms had six local derivative hits and
no misses in all twelve opens. The single-decode code was not exercised. Five
candidate opens had a slower usable frame, and the median first-frame rendering
interval increased by roughly 47 ms. That identifies the affected interval;
it does not establish that the cold decoder altered first-frame drawing. The
cold result cannot override the separate warm guard. No new sample replaced
that guard and the standalone build was not installed.

In the prewarm experiment all four wins occurred when the candidate submitted
one fewer intermediate frame without consuming any speculative cover. Zero-hit
launches were retained. In the separate grace experiment the candidate sent
fewer intermediate frames without ever entering the grace path; frame counts
alone cannot prove it removed those frames. No percentile claim is made from
six pairs.

Candidate sources, tests, and reproduction details:

- [Native supervisor](supervisor-trial/README.md)
- [Bounded prewarm](warm-trial/README.md)
- [Last-cover grace](last-frame/README.md)
- [Single-decode local cover](cold-trial/README.md) and [cold fixture](cold-bench/README.md)

## Recovery and limitations

The first native pilot could not reach the sleeping device and performed no
device writes. The operator woke the Kindle and the new pilot passed. A first
independent cold confirmation stopped in the control warmup when native catalog
access replaced the document's identity and advanced its mtime. It produced
zero primary pairs. The document bytes, cover, native registry, installed
launcher (including mode and mtime), user library/cache, input and lighting
were preserved. The new native catalog row was not overwritten. The owned
research stage was removed, native control restored and the lease released.
After a successful read-only readiness observation, an entirely new ten-pair
batch ran to completion. The failed batch is retained independently, never
mixed into successful samples. See the [bounded recovery record](recovery/README.md).

Device operations used fresh, owner-checked leases. Preparation, analysis and
builds ran through `ssh test-env`; diagnostic captures and short powerd wakeups
are separate from timing samples. Native e-ink response, remote downloads, a
different library mix and long deep suspend were not measured in these trials.
