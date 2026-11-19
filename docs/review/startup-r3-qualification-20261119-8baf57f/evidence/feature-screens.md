# Independent R3 feature screens

Positive paired savings are control minus candidate. All six predetermined
pairs remain in each report. A screen requires at least 100 ms median paired
saving on its primary endpoint, five faster primary pairs, and no more than
50 ms median regression on the other startup endpoint. Observed feature
exercise, independent native pixels, and restoration are also checked.

| Feature | Primary endpoint | Primary paired saving | Other endpoint saving | Input and pixels | Numeric screen | Ten-pair confirmation |
| --- | --- | ---: | ---: | --- | --- | --- |
| [First layout](layout-screen-with-pixels.json) | Usable Library | -27.859 ms (3/6 faster) | Covers -186.839 ms (2/6 faster) | Passed | Failed all three thresholds | Not triggered |
| [Cached prewarm](prewarm-screen-with-pixels.json) | Complete covers | -80.558 ms (1/6 faster) | Usable -61.226 ms (1/6 faster) | Passed | Failed all three thresholds | Not triggered |
| [Last-cover grace](grace-screen-with-pixels.json) | Complete covers | -52.492 ms (2/6 faster) | Usable -63.233 ms (2/6 faster) | Passed | Failed all three thresholds; no grace trigger | Not triggered |
| [Portrait](portrait-screen-requalified.json) | Usable Library | 124.409 ms (3/6 faster) | Covers 238.089 ms (4/6 faster) | Passed with fresh strict pixels | Failed the 5/6 direction threshold | Not triggered |

The portrait row uses the original six timing pairs with a new independent
strict pixel qualification. Its [first pixel failure](portrait-first-attempt.md)
remains retained. The [continuity record](strict-requalification.json) verifies
identical timing pairs and mechanisms, programs, settings, runners, auditor,
and comparison regions; it does not count the timing sample twice.

## First layout

The unchanged R3 analyzer accepted the frozen timing batch and its separate
pixel qualification. The six measured control cases actually used `rowwise`
projection and the six candidate cases used `batched` projection. Each arm's
independent pixel case used its corresponding mode. Warmups remain separate
from the six pairs.

The complete body below the top 72 pixels and the observable right status bar
at x=930, y=0, width=930, height=72 matched in the 1860-by-2480 captures. All
input, fixture, program, and restoration checks passed. The original retained
receipts independently show 14 completed timing stages and four pixel stages,
26 and 16 recorded operations respectively, and zero failed operations. Both
before/after protected snapshots match; both owned stages were removed and
both owner/token-checked lease releases closed their connections.

The primary usable frame took 27.859 ms longer at the paired median, and
complete covers took 186.839 ms longer. The latter also exceeds the 50 ms
auxiliary-endpoint tolerance. This screen therefore does not request ten
independent confirmation pairs and supplies no measured speed benefit.
Functional/pixel compatibility and the separate combined-default guards are
retained as distinct observations.

The full checked report is retained on `test-env` at
`/root/aidoku-r3-handoff-20261119/reports/layout-screen-with-pixels.json`.
[The public exporter](summarize_feature.py) ran on `test-env` and preserves
numeric pairs, public program hashes, observed mechanism counts, pixel geometry,
and boolean outcomes. It omits private fixture identities, user pixels, image
hashes, and transport credentials; it does not run a device or change a gate.

## Cached prewarm

The unchanged R3 analyzer accepted all six prewarm timing pairs and the
separate native pixel qualification. The full body and observable right
status bar matched. Only the real `prewarm=0` versus `prewarm=2` setting
changed between the identical R3 programs.

All six enabled timing cases actually attempted two preloads, hit one,
and consumed one verified result. Across the screen this is 12 attempts,
six hits verified before input readiness, and six verified consumptions.
Each candidate also recorded one `image_unavailable`; those unsuccessful
attempts remain in the same timing sample. No candidate case had zero
attempts, hits, or consumption. The disabled control retains all six
zero-attempt, zero-hit, and zero-consumption cases. No store-busy, open,
selection, or image-decode failure was reported. The independent pixel
case and both warmups per arm are preserved separately in the
[complete mechanism counter export](prewarm-mechanism.json).

Complete covers took 80.558 ms longer at the paired median, with one of six
favorable pairs. The usable frame took 61.226 ms longer, also with one
favorable pair, exceeding the 50 ms auxiliary tolerance. All three screen
thresholds failed, so no ten-pair confirmation plan was prepared.

The raw timing and pixel receipts independently confirm 14 and four complete
stages, 26 and 16 operations, and zero failed operations. Protected snapshots,
native/entry/input restoration, owned-stage removal, owner/token-checked
release, and connection shutdown all passed. The full checked report remains
on `test-env` at
`/root/aidoku-r3-handoff-20261119/reports/prewarm-screen-with-pixels.json`.

## Last-cover grace

The unchanged R3 analyzer accepted the six grace timing pairs, exact off/on
settings, and separate native pixels. The full body and observable right
status bar matched. All 18 cases contain the expected startup event for the
grace setting in their arm, including warmups and qualification.

Every case recorded zero grace `started`, `completed`, `expired`, and
`cancelled` events. The [complete mechanism export](grace-mechanism.json)
retains all six zero-trigger candidate timing cases and the independent pixel
and warmup rows. `actual_wait_ms` remains null because no grace interval was
observed; it is not a measured zero-duration wait. No triggered single-frame
completion occurred. Passing configuration and payload checks does not supply
positive device execution evidence for this branch.

Complete covers took 52.492 ms longer at the paired median, with two of six
favorable pairs. The usable frame took 63.233 ms longer, also with two favorable
pairs, exceeding the auxiliary tolerance. The screen fails all three numeric
thresholds and has no positive grace trigger to support attribution. No
independent ten-pair confirmation plan was prepared. The earlier
[controlled timer fixture](../../startup-all-20261119/promotion/grace-fixture/README.md)
remains separate host boundary evidence and contributes no device latency
observation to this screen.

The timing and pixel receipts show 14 and four completed stages, 26 and 16
operations, and zero failures. Protected snapshots, native/entry/input
restoration, owned-stage removal, owner/token-checked release, and connection
shutdown all passed. The full checked report remains on `test-env` at
`/root/aidoku-r3-handoff-20261119/reports/grace-screen-with-pixels.json`.

## Separate R3 import qualification

The [idle-import correctness and pixel qualification](import-qualification.md)
passed with eight complete stages. Four offscreen target derivatives were
prepared in idle mode and reused by the later new GUI; both stages' native
body and right status-bar comparisons passed. All 55 installed user-cache
files remained identical, and restoration/release checks passed. This R3
record has no timing pairs or economic claim; the prior R2 timing study stays
under its original identity.
