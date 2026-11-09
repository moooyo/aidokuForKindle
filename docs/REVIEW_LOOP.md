# Design and behavior review loop

## Active follow-up scope

The user reopened development after the phase-1 delivery. The current scope adds
further cache/download optimization, a completely image-only closed reader, and
automatic return to the retained page after stock sleep. Phase-1 rounds 6 and 7
remain historical evidence; they do not satisfy the two-clean-round condition
for this follow-up. The new rounds are recorded in the table below.

Integration review has already corrected stale reader data selecting a different
manga's detail page on recovery, pending mode geometry after sleep, delayed
double-tap/seek input surviving sleep, and arrow keys navigating behind a modal.
Independent review also found that invalidating a device observation discarded
its last real values; the fix preserves those values while marking them stale.
These integration findings are not clean rounds.

The first follow-up round completed its desktop matrix and manual inspection,
with two new required suggestions: cross-chapter retry/return labels needed
chapter identities, and closed reading exposed stitching in a bundled demo page.
Those fixes were followed by a tone-matching refinement of that same asset.
Native adversarial scheduling also reproduced an Active-phase coalesced
sleep/wake path retaining a pending tap. That path now uses the normal quiesce
and resume cleanup. Candidate 5 includes the complete corrections; its current
UI review and separate native acceptance status are recorded below.

## Follow-up rounds

The final Candidate5 desktop SHA-256 is
`472265b0ce022f4a98b38821db9c5ea2cba456d3794903d514a64ce226face0a`.
The two-clean-round condition applies to complete UI/UX walkthroughs. A complete
same-payload native functional pass is a separate required delivery gate.

| Round | Candidate and evidence | Findings | UI clean streak |
| --- | --- | --- | --- |
| Phase2 1 | Candidate2; `artifacts/phase2/*-round-1`, `docs/review/phase2-round-1-independent.json` | Two required suggestions: cross-chapter recovery identity and a stitched demo back page. Invalid early vertical/bookmark preparation samples and their fresh corrections remain recorded. | 0 |
| Phase2 2 | Candidate4; `artifacts/phase2/*-round-2`, `docs/review/phase2-round-2-independent.json` | All 155 UI, 32 recovery, 11 real-input and 44 exact-fullscreen cases passed. Manual inspection of 92 fresh captures found the front-page continuation seam. | 0 |
| Phase2 3 | Candidate5; `artifacts/phase2/*-round-3`, `docs/review/phase2-round-3-independent.json` | All 155/32/11/44 cases passed. Independent inspection of 94 fresh captures found zero new suggestions and zero unresolved required items. | 1 |
| Phase2 4 | Same Candidate5; `artifacts/phase2/*-round-4`, `docs/review/phase2-round-4-independent.json` | All 155/32/11/44 cases passed. A separate complete review of 94 fresh captures again found zero new suggestions and zero unresolved required items. | 2 |

The last two automated rounds used 115 independent UI data directories and 16
recovery directories per round, with empty cross-round intersections. Raw-input
and fullscreen provenance separately records fresh run IDs and unchanged binary
hashes. Fullscreen comparisons use the entire viewport with zero pixel tolerance,
including bookmarks, all legacy footer values, and real queued-worker notices.
They do not silently discard bands or a spread gutter.

The requested UI/UX exit criterion was achieved in follow-up rounds 3 and 4.
Each manual review actually inspected 94 fresh images across the twelve baseline
screens, capability variants, Reader/session/timer states, recovery, upgrade,
selection, queues and cache controls. The reports enumerate the inspected files;
automated pass counts are not presented as a claim that every capture received a
separate manual inspection. The final native functional/state verification is an
independent delivery requirement and is recorded in `SUSPEND_RESUME.md`.

That final native gate also passed: the accepted selection contains all twelve
required same-payload cases, and fourteen read-only database copies passed
quick/integrity/foreign-key checks in DELETE mode. The final stock body matches
its independent prelaunch baseline exactly, no owned processes/locks remain,
and the original screensaver and USB observations are restored. Environment
failures and the original pre-freeze held-input diagnostic remain linked in the
native report; they are not silently replaced by the accepted selection.

Native and environment integration findings are retained separately in
`SUSPEND_RESUME.md`: an actual coalesced sleep/wake input race was reproduced and
fixed through the shared cancellation path. Stock feature-flag reboot, metrics
queue boot failure and a subsequent userstore-loop kernel panic are not hidden
or counted as successful application runs. The final native suite binds its
actual boot, binaries, policy restoration and stock baseline.

## Exit rule

The requested exit condition is two consecutive complete review rounds with no
new actionable suggestions. A round is complete only after the reference screens,
the relevant interaction states, persistence checks, and any newly added scope
have been reviewed. Existing required fixes must also be closed. An automated
pass count or a low pixel-difference score does not substitute for visual review.

The implementation and verification are performed remotely on `test-env` and its
dedicated `aidoku-pw6-5201` firmware guest. No application builds or tests are run
on the Windows operator machine.

## Historical phase-1 review history

| Round | Evidence | Findings and disposition | Clean streak |
| --- | --- | --- | --- |
| 1 | `docs/review/round-1-design.json`, `artifacts/ui-round-1`, `artifacts/design-round-1` | 23 visual suggestions. The 53 interaction cases also exposed nine incorrect device geometries and one RTL swipe double action. Fixes were carried into the next candidate. | 0 |
| 2 | `docs/review/round-2-design.json`, `artifacts/ui-round-2`, `artifacts/http-round-2` | Five new visual suggestions after full Scribe geometry became available. All 57 interaction and real HTTP pagination checks passed. Remaining visual fixes were carried forward. | 0 |
| 3 | `docs/review/round-3-design.json`, `artifacts/ui-round-3`, `artifacts/interactions-round-3-checked` | One new visual suggestion: fragmented tab rules. Real input testing exposed reader long-press latency. Hidden cover decoding was removed, reducing the measured menu handler from about 608 ms to about 5 ms without changing the 500 ms threshold. | 0 |
| Native integration | `artifacts/native-round-2`, `artifacts/sqlite-native/analysis.md` | Six of seven old-candidate firmware cases passed. Integer-only BusyBox sleep, launcher teardown, and a transient SQLite open error required additional fixes. The original database evidence was retained. | Not a clean round |
| Performance scope | `docs/DOWNLOAD_PERFORMANCE.md`, `artifacts/performance` | The user added caching, downloads, and batch performance. Shared raw storage, bounded scheduling, persistent pins, derived grayscale caching, bulk selection, queue controls, and new regressions became part of the final review scope. | Not a clean round |
| 4 | `docs/review/round-4-design.json`, `docs/review/round-4-independent.json`, `artifacts/ui-round-4-final` | The 95 UI captures and six raw-input cases passed. Independent visual review found additional selection-header, menu-state, pager-position, tag-context, touch-target, localization, and notification issues. These were fixed rather than counted as a clean round. | 0 |
| 5 | `docs/review/round-5-design.json`, `docs/review/round-5-independent.json`, `artifacts/ui-round-5-final` | All 107 unique captures and six raw-input cases passed. Review retained the tab paint-order and notification placement issues and added truthful empty-batch feedback. | 0 |
| 6 | `docs/review/round-6-design.json`, `docs/review/round-6-independent.json`, `artifacts/ui-round-6-final` | Complete review: 119 UI captures, six raw-input cases, zero new suggestions, and zero required unresolved items. The final firmware candidate also passed nine cases, database integrity checks, and stock-screen restoration. | 1 |
| 7 | `docs/review/round-7-design.json`, `docs/review/round-7-independent.json`, `artifacts/ui-round-7`, `artifacts/raw-round-7` | All 119 UI captures and six raw-input cases passed with the identical frozen executable and disjoint fresh data directories. Both reviewers completed the full matrix again with zero new suggestions and zero unresolved required items. | 2 |

## Complete-round checklist

- Paperwhite library, detail, reader, sources, source catalog, search, history,
  and settings, plus Scribe library, two-column detail, source browser, and
  landscape spread reader.
- Typography, physical sizing, grid density, rules, dotted leaders, grayscale,
  cover editions, edge toolbars, and the absence of scrolling or animation.
- Empty, failed, offline, unavailable-source, long-title, no-frontlight, and
  pagination-boundary states.
- Real input for navigation, taps, long press, reader/page swipes, image-only
  viewport, seek commit, bookmarks, incognito, and restart persistence.
- Batch chapter selection, selection cancellation, bulk enqueue deduplication,
  queue pagination, global controls, cache information/clear, and offline covers.
- Raw X11/Winit input and native evdev/QMP input are checked separately from
  scripted Slint events. Firmware verification includes restoration of the stock
  interface and persistent database reopening.
- Full-viewport pixel comparison checks the closed reader with bookmarks, legacy
  footer preferences and background notices. Native sleep checks retained PID,
  fonts, image-job counts, frame/input ownership, network pause ownership,
  pending mode/gesture boundaries, stale epochs and recovery fallback.
- Performance evidence uses immutable binaries, identical fixture contents,
  recorded requests/bytes/concurrency/RSS, and explicitly identified build
  profiles. Virtualized results are not described as physical-device results.

## Reference inconsistencies and capability boundaries

These are documented differences rather than hidden defects or reasons to
fabricate state:

- The Paperwhite sample declares 86 chapters while showing six chapter rows and
  an eight-page counter. The implementation uses the actual fifteen pages.
- Illustrative counts, dates, and source availability are derived from real
  fixture records. Download and progress labels do not invent completed work.
- The PDF draws slightly different artwork for the same comic page in different
  device examples. One canonical comic page is used across devices; controls and
  panel boundaries are compared separately from that illustration variation.
- The rotation control describes locked/manual orientation. Automatic sensor
  polling is not represented as an implemented feature.
- Firmware emulation does not prove physical panel ghosting, battery use, real
  Wi-Fi behavior, or compatibility with every listed model and firmware.

## Historical phase-1 frozen artifacts

- Desktop executable SHA-256: `1c9f1d4633c8f6b190e6069f627aeb4f0beaec5bf82c619c3c2503fe3a72b05a`.
- Native ARM executable SHA-256: `3b1d672293f12de320abf812bbde9a8da4c42697812bb42561682ea4f7616202`.
- Packaged launcher SHA-256: `5148b3f13313100a2518f1f467ee27a6d00332372b4d9a23a948e11acfe57d96`.
- Development ZIP SHA-256: `86e5248b0978fbb77286d50d57c8c703de0b860e0049170985f7498e72c5acb6`.
- Rust application tests: 202 passed. Vendored native-backend tests: 34 passed.
- Final native evidence: `artifacts/native-round-6/run-20261104T205345Z-241970`.
  Its nine cases passed, both database copies passed quick/integrity/foreign-key
  checks, and the stock Home body matched the prelaunch pixels exactly.
- Release performance: the same 72-image workload, three interleaved rounds per
  scheduling mode, and eleven correctness checks. Median whole-worker wall time
  was 18.222 seconds with one background page versus 6.400 seconds with two.
  Read `docs/DOWNLOAD_PERFORMANCE.md` for the fixture, measurements, emulator
  results, source snapshot, and limits of these observations.

## Historical phase-1 acceptance

The requested two consecutive complete clean rounds were achieved in rounds 6
and 7 on the same immutable desktop candidate. Both used fresh per-case data;
the recorded sets contain 91 data directories each and have no intersection.
Each round passed 119 UI captures and six real Winit input scenarios. The
primary and independent visual/source reviews covered the twelve baseline screens and the
expanded state matrix, with zero new suggestions and zero required unresolved
items in both rounds. Automated case counts are not presented as a claim that
every capture received a separate manual inspection.

The final native ARM candidate passed its nine firmware scenarios, both
database integrity checks, and complete stock-screen restoration. Package CRC,
manifest hashes, executable permissions, license presence, and exclusion of
runtime data were checked remotely. The 20,020,419-byte development archive is
available as `dist/aidoku-kindle-development.zip` with its manifest alongside it.

This acceptance covers the implemented scope and the recorded emulated
environment. The capability boundaries in the README and the physical-device
limits above remain explicit; it does not assert complete upstream Aidoku
feature parity or untested model/firmware compatibility.
