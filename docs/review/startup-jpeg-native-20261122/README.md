# Ordinary Kindle card: JPEG capability admission

## Scribe result: no consistent first-screen improvement

**Keep the on-demand JPEG policy in source, but do not promote it as a startup speedup or change the daily R3 installation.** All 14 ordinary card opens completed on the Scribe: two excluded warmups and six alternating A/B pairs. B reached the first complete Library submission earlier in **2 of 6** pairs and later in four. The paired median saving, A minus B, was **−31.223 ms**: the candidate was later, not earlier, at this median. The median lower and upper bounds from the release brackets were **−32.489 to −29.956 ms**; these are measurement bounds, not confidence limits for future launches.

| Pair | Order | A: eager (ms) | B: on demand (ms) | A − B (ms) | Bounds on A − B (ms) |
| ---: | :---: | ---: | ---: | ---: | ---: |
| 1 | AB | 3153.944 | 2698.206 | +455.738 | +454.866 to +456.610 |
| 2 | BA | 3066.294 | 3081.262 | −14.968 | −16.193 to −13.743 |
| 3 | AB | 2953.869 | 2520.415 | +433.454 | +432.263 to +434.644 |
| 4 | BA | 2741.791 | 3382.779 | −640.988 | −642.464 to −639.512 |
| 5 | AB | 2843.473 | 2890.950 | −47.477 | −48.785 to −46.169 |
| 6 | BA | 3077.252 | 3163.321 | −86.069 | −87.272 to −84.866 |

The times are midpoints of card-release-to-submission intervals. The order-specific paired medians were **+433.454 ms (AB)** and **−86.069 ms (BA)**. This batch does not isolate the cause of that difference. Subtracting the separate A/B arm medians (3010.081 and 2986.106 ms) would misleadingly suggest B was faster. All observations remain in the [sanitized, replayable result](result.json); no trials were substituted or added. There is no physical Paperwhite result in this study.

Every visit had one independently held lease, a normal application exit, restoration of the installed original and native state, closed device connections, an independent absence check and an exact owner/token-checked release. The final return was at **04:15:47 Asia/Shanghai on November 22, 2026**. The 14 per-visit source archives and closed lease receipts were audited against the exported intervals; `python3 -B audit.py public result.json` replays the published data offline. The [two ARM builds](build/README.md) and tested qualification/fallback behavior remain useful independently of this card timing.

The two consumed upload archives (189,517,324 bytes total) and temporary build/test directories were removed. The small closed operations, sanitized result, build qualification and private evidence remain. The shared `test-env` return receipt is `aidoku-jpeg-native-return-20261122-041547.json` in the coordination directory; it records the completed return, not present device availability or a reservation.

**Follow-up:** no extra pairs were collected for this result. A [separate ordinary-card stage diagnostic](../startup-card-stages-20261122/README.md) put stock release, foreground launcher process birth, Rust entry and Library submission on one clock. It located the largest time and variation inside the application, particularly after its initial full frame, without changing the installed launcher. Its five diagnostic observations are not extra A/B samples.

## What this comparison tests

A and B are rebuilt from the same Aidoku working tree. A restores the eager `native_media::initialize(kindle)` call in `Platform::new`; B keeps the [on-demand change](../startup-followup-20261121/jpeg-probe-admission/README.md). Nothing else in the application is intentionally different. Both emit the same managed-start `startup_library_submitted` log. Both use the installed stock card, launcher, JPEG worker, portrait helper and existing Library. The installed R3 app itself is **neither** arm. No cache clear, book open, sort interaction, manual touch, video or full-cover completion is needed.

**Primary endpoint:** the release of an actual press on the ordinary Aidoku card in the stock Library, to the first successful application submission of the complete nonempty Scribe Library layout, entries and navigation. A placeholder counts as a cover slot. The press-release bracket and the submission callback use the device's `CLOCK_BOOTTIME`; the result is an interval, not a synthetic app-start timestamp or an optical panel measurement. A splash, generic Ready, SSH launch or failed/absent callback cannot supply a sample.

## Schedule and decision fixed before hardware access

Use one excluded launch of each arm (A then B), followed by six pairs in order **AB, BA, AB, BA, AB, BA**. Each letter is a new visit with its own exclusive lease, including warmups. Do not clear caches between arms or label ordinary repeated opens as known cold. Other projects may use the device **between** visits; no one holds a lease for the pair as a whole.

For each qualified visit, preserve `[submit − release_after, submit − release_before]`. Per-pair saving is A minus B: report both the midpoint difference and the conservative interval `[A_low − B_high, A_high − B_low]`. A positive midpoint alone is not a definite win; touching or overlapping intervals are inconclusive for that pair. Report all attempted slots, both launch orders, and the median of within-pair differences rather than subtracting two cohort medians.

Call the result *consistent improvement on this device and cohort* only if both warmups and all six formal pairs qualify, both order-specific median midpoint savings are positive, the median conservative lower bound is positive and at least five pairs have disjoint intervals favoring B. Otherwise the outcome is descriptive or incomplete. Never replace a failed measured visit or add pairs until a desired result appears. The rule is not an automatic deployment decision. Starting a new cohort after a material test correction does not salvage an old visit.

## Device use

Prepare the ARM programs, private tools and both final payloads on `test-env` before contacting the shared device. Run the host collector with the project's own authorized authentication; never package it. Each visit checks the current stock state, protects the other installations, selects only the Aidoku application executable temporarily, presses the verified ordinary card and binds that launch's library event and normal exit. An actual visit is eligible only after restoration, an independent absence check, closed connections and exact owner/token release. Keep raw launcher records, catalog identifiers and credentials out of the public results. A busy lease is not a reservation or a measurement. A failed admission is not a launch; do not claim restoration for a device state that was never changed.

For a new freeze, supply the designated operator through `CODEX_THREAD_ID` on `test-env`; the host's `--owner` must match the new plan. The closed predecessor is verified separately against its own lease evidence and does not authorize the new operator. Run the synthetic regression with `python3 -B -m unittest discover -s docs/review/startup-jpeg-native-20261122 -p test_freeze.py -v` on `test-env`.

The measured [historical 25.456 ms eager-probe cost and two-profile host comparison](../startup-followup-20261121/jpeg-probe-admission/README.md) are separate evidence. Neither those values nor the [installed R3 framebuffer upper bound](../startup-followup-20261121/rendered-library/stock-r3.json) is a substitute for this experiment.

## Selector contract

`selector.sh` is the per-visit app selector module. It only touches
`/mnt/us/aidoku/app/aidoku` via a single-file bind mount and never mutates
`launch.sh`, `aidoku-startup-portrait`, user data or catalog state.

### CLI

- `sh selector.sh prepare LABEL CASE`
- `sh selector.sh select LABEL CASE`
- `sh selector.sh check LABEL CASE`
- `sh selector.sh restore LABEL`
- `sh selector.sh absent LABEL`

`LABEL` is a 16-character lowercase hex stage label. `CASE` is one of:
`control-warmup`, `candidate-warmup`, `control-01..06`, `candidate-01..06`.

`prepare`, `select` and `check` require the same case string; the prepared case is sealed
into private journal state and mismatches are refused.

### Required stage payload members

- `owner`
- `control/program-modes`
- `control/launch.sh`
- `control/app/aidoku`
- `control/app/aidoku-startup-portrait`
- `programs/selected-app`
- `rename-new`

`programs/selected-app` is the only staged ELF selected for the visit. The name
is common to both payloads, not the bytes: A carries the eager build and B the
on-demand build. Neither runs installed R3 during timing. The selector binds the
appropriate leaf for the named visit and restores the installed original on exit.

### State, receipts and restoration markers

- Stage root: `/mnt/us/.aidoku-native-$LABEL`
- Private state root: `/var/tmp/aidoku-native-$LABEL`
- Journal directory: `/var/tmp/aidoku-native-$LABEL/selector-journal`
- Journal markers:
  - `owner` and `sealed` (`LABEL` + newline)
  - `case`
  - `aidoku.old` (frozen installed identity)
  - `aidoku.source` (frozen selected identity)
  - `aidoku.mount-intent`
  - `aidoku.mount-id`

Successful operations print exactly:
`selector_prepare`, `selector_select`, `selector_check`,
`selector_restore`, `selector_absent`.
Refusal paths print `refused <tag>` on stderr.

`check` is a strict read-only proof gate. It verifies sealed ownership, case,
intent marker, stored mount ID, selected source identity and currently mounted
target identity without taking the selector lock and without creating or
repairing any mount.

`restore` is the close-path recovery entrypoint. It validates mount custody,
unmounts only the owned single app bind, re-checks the original control bytes
and metadata, then clears the journal. `absent` is an independent post-close
check that requires stage and private state to be gone, no selector lock leaf
and no mount left on the installed app path.
