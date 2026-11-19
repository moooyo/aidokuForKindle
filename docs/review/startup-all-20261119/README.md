# Kindle Library startup and reopening

The [handoff](../../../HANDOFF.md) records the stopping point, the installed
version and the first remaining device qualification.

This delivery implements six controls in **Settings > Startup & reopening**.
Each setting can be saved independently; reopen Aidoku to apply it. **Enable
all** opts into all six; **Use recommended** restores the choices below.
Only the first Library layout change is enabled by default. No aggregate
speedup is claimed.

| Control | Recommended | What it changes |
| --- | --- | --- |
| Native portrait check | Off (shell) | An optional packaged native helper performs the portrait check on the indexed Library route. |
| Preload two cached covers | Off | Reads up to two eligible local thumbnails already in the cache. |
| Wait briefly for the last cover | Off | Gives a nearly complete cover group up to 40 ms to publish together. |
| Prepare covers when idle after import | Off | Prepares eligible offscreen thumbnails after committing local imports. |
| Simplify the first Library layout | On | Projects the visible page in one batch and skips redundant title measurement. |
| Keep local covers for quick reopening | Off | **Quick return to Kindle** retains validated local cover pixels for up to 45 seconds. Reopening starts a new GUI and goes through the native checks again. **Exit app** remains a complete exit. |

[Settings walkthrough, captures and input replays](settings/README.md).
The frozen desktop acceptance covered Paperwhite 5 and Scribe in 29 cases
with 793 checks, including interaction, localization, and saved choices.

## R3 candidate and installed release

R3 (`final-v3-clean`) changes only the launcher from R2. The compiled
source manifest is `28c1bb31...`; the ARM application is `1b1c508e...`, the
portrait helper is `bde28cd5...`, and the R3 launcher is `412163f1...`.
The product code is in local branch commit `8baf57f`, outside `main`. That
commit establishes source delivery. The candidate programs are staged; no
release ZIP/KPM was produced and deployment was not attempted. The day-to-day
Kindle installation remains `c6867d1` (`9d92dc4c...`).
The launcher reduces redundant setup before the Library lock. Its
[Linux compatibility and admission trials](launcher-r3/README.md) cannot
establish Kindle startup speed. A separate remote comparison matched all 483
current Windows source and build input files to the frozen R3 manifest and all
three staged program files to the build receipt. See the
[R3 evidence index](evidence/r3-index.json) for exact identities and the status
of each physical and release check.

R3 passed both default protection checks on the Kindle. Each row represents
six balanced pairs against the installed reference. Positive values are paired
median milliseconds saved; parentheses give the number of faster pairs.

| Thumbnail cache | Usable Library | Complete covers | Exit to native Home |
| --- | ---: | ---: | ---: |
| Warm | +101 ms (4/6) | +162 ms (5/6) | +441 ms (4/6) |
| Empty | -9 ms (3/6) | +48 ms (4/6) | -115 ms (3/6) |

The gate allows up to 50 ms of median regression on each startup endpoint.
Exit is shown separately. These comparisons protect the combined defaults and
do not isolate a feature or confirm a stable speedup. The accepted test-env
reports are `v3-default-w.json` and `v3-default-c.json`; the public index
retains all 12 pairs, including the slow cold pair.

At handoff, the independent R3 physical pixel checks and the normal default
lifecycle qualification had not run on the Kindle. The All preset and the
individual portrait, layout, prewarm, grace, import and two-open retention
trials were also unrun on R3. Warm and cold guards used the indexed native
entry with benchmark routing; a normal installed launch, short wake and
rollback acceptance remain separate.

The [source and program tests](build/README.md) passed 852 library and 22
application checks (six ignored). The standalone PW5 dark sleep-screen test
failed on both R2 and the installed reference, with identical captures until
the failure. Its later scenes were not reached. R3 uses the same application;
its launcher has its own lifecycle, portrait, and catalog checks on test-env.

## Isolated feature comparisons

| Feature | Observation and limits |
| --- | --- |
| Native portrait check | [Host compatibility](launcher-r3/README.md) covers native and shell selection, fallback behavior, and ownership restoration with mock services. It is not a physical speed measurement. |
| Cached prewarm | The [R2 screen](evidence/v2-prewarm-screen.json) saved 204 ms to complete covers (6/6). Independent R2 confirmation took 6 ms longer to complete covers (5/10 faster) and 72 ms longer to reach a usable Library (3/10 faster). One zero-attempt candidate remains in those ten pairs. The screen did not reproduce. |
| Final-cover grace | The [R1 physical screen](evidence/grace-screen.json) recorded zero activations. A [controlled timer fixture](promotion/grace-fixture/README.md) exposed a spent timer that could miss its release turn, verified the R2 fix, and exercised early completion, expiry, cancellation and fallback. |
| Idle import thumbnails | A guarded R2 physical screen demonstrated four offscreen derivatives reused by a fresh process. R2 uses the same application as R3; both R2 test arms used the same R2 launcher. The measured trade-off is detailed below. |
| First Library layout | [Host projection and paired desktop pixels](first-layout/README.md) show less query and title work and zero differing pixels in the relevant Paperwhite 5 and Scribe scenes. The [R1 physical screen](evidence/layout-screen.json) was 115 ms slower to a usable Library and 152 ms slower to complete covers. It did not meet the latency gate. |
| Quick return | [Keeper mechanics](resident/README.md) cover one-shot, expiring pixel transfer and ordinary exit. A cached hit can avoid a decode. A subsequent indexed entry always starts a new GUI. |

In the six-pair R2 import screen, the next open reached complete covers
921 ms sooner (6/6), but reached the usable Library 152 ms later. The import
published 86 ms later and its visible covers finished 324 ms later. The two
visible waits together improved by 553 ms (6/6); counting deferred work as
well, the paired total was 934 ms longer. The worker added 1,074 ms of elapsed
work, the median extra completion tail was 1,563 ms, and the four derivatives
used 298,935 more bytes. This establishes a shift in when work happens in the
synthetic fixture. The screen did not meet the default or latency gate, and
did not include a passing independent physical pixel qualification. The
[import protocol](bench/import_probe/README.md) documents replay on test-env.

The [same-GUI host prototype](resident-owner-prototype/reentry/README.md)
kept a real GUI PID through two controlled transactions with test adapters.
The [official installed Library route observation](evidence/installed-gui-entry-observation.json)
could not authenticate the required native process lineage across two ordinary
opens. No same-GUI reopen ran on the Kindle. The pixel keeper does not retain
the old window, framebuffer or input.

## Evidence and retained failures

The [trial plan](PLAN.md) defines the balanced pairs, default guard and
confirmation rules. Positive paired savings mean the candidate is faster.
Times describe software readiness, accepted complete covers, or native Home
handoff. They do not measure optical e-ink completion. Screen/confirmation
measurements, physical pixel/lifecycle qualification, and normal installed
entry are separate. A missing or invalid result cannot support promotion.

- [R1 evidence](evidence/r1-index.json): both default timing guards passed,
  but the candidate was retired. SQLite busy blocked every measured prewarm
  attempt; the grace screen never armed; layout missed its gate; the import
  pilot did not cover its immediate penalty, and the later import screen is
  invalid because the installed-cache guard failed.
- [R2 evidence](evidence/r2-index.json): the cold and standalone defaults
  lifecycle checks passed; warm missed the 50 ms tolerance at both startup
  endpoints (-63 ms usable and -63 ms complete covers). The later physical
  prewarm confirmation and guarded import screen remain R2 results. The
  report names on test-env are `v2-prewarm-confirm.json` and `v2-import-screen.json`.
- [R3 evidence](evidence/r3-index.json), [build replay](build/README.md), and
  [packaging, install and rollback procedure](promotion/README.md).
