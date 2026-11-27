# Handoff: Kindle Library startup and reopening

## Start here: 2026-11-28 (Asia/Shanghai)

**Paused by the user for continuation in another session.** The Aidoku ready
entry was withdrawn. There is no Aidoku lease, open Kindle connection, or
native cleanup to complete. The last qualified locator package is unused on
`test-env`; this batch has not sent locator or measurement commands, and no
measurement package is sealed. This is not a statement that the shared device
is currently free. Obtain current authorization and a coordinated handoff
before any new device commands; do not inherit an older approval, slot or
lease token.

**The last independently verified Scribe daily install is Aidoku 0.1.3 from
`f3e5070`.** It includes `97173d0` (background toolbar reads), `83cac53`
(reuse the cache reconciliation lookup), and `f3e5070` (coalesce each native
query response write). The last change was explicitly approved despite
missing its performance screen. The installed GUI and launcher did not
change in that delivery; both query helper copies and KPM metadata were
verified. This handoff archives diagnostic sources only, not a new product
build, startup optimization, or deployment. See the [delivery receipt](docs/review/startup-stock-admission-20261127/evidence/f3e5070-delivery.json).

**Next experiment:** get one accepted ordinary-card Scribe sample with
`launcher-generation-v1` and `stock-admission-v2`. The temporary launcher
probe passed on both firmware profiles. The archive passed [82 off-device
checks](docs/review/startup-stock-admission-20261127/evidence/archive-verification.json),
including 64 integrated operator checks.
An older measurement opened the card but failed native closeout; the original
owner restored and released it. The next measurement was refused during
read-only admission, before upload, overlay or input. It was independently
released. Both yielded **zero accepted timing samples**; the older refusal
still does not identify a failed admission predicate.

1. Read the [experiment archive](docs/review/startup-stock-admission-20261127/README.md)
   and use its host-only resume procedure. Recheck that the private locator
   is unused and that its source still matches the installation to be tested.
2. Follow the [ready queue](docs/review/startup-stock-admission-20261127/READY_QUEUE.md):
   announce ready only with approval and complete material, acquire the same
   shared lease just before commands, and verify return immediately. First
   take one no-input locator from the native Library. Release before
   classifying its fresh frame or sealing the measurement. When ready again,
   use a new lease; open the ordinary card at most once and only after
   admission. If either visit is refused, stop and retain the precise reason.
3. Evaluate only a same-visit, closed and independently released sample. The
   historical 150–170 ms preparation stage is actionable only if it
   reproducibly explains a long segment *and* the observer record was
   published early enough to leave usable slack. Otherwise, investigate the
   release-to-worker interval (0.51–0.58 s in older ordinary-card visits)
   without bypassing the normal entry. Framebuffer and driver timestamps are
   not the loading-excluded, actual visible Library endpoint.

**Do not rerun initialized-Library direct-frame feasibility.** It is closed:
metadata worker completion missed the latest loading draw start by 379.755 ms
on Scribe and 367.411 ms on the independent PW6 recheck. Keep the loading
frame and existing display/input checks. See the [closure evidence](docs/review/startup-stock-admission-20261127/evidence/initialized-library-readiness.json).

## Historical 2026-11-26 handoff (Asia/Shanghai)

The following entries retain what was known at the time. The daily install
was then Aidoku 0.1.3 from `97173d0`. Item 3 was pending at that point; the
November 28 entry above records its closure and the current next action.

1. **Font delivery: closed, rejected.** Four alternating Scribe foreground
   pairs showed a 19.353 ms median paired regression to the same-frame
   driver acknowledgement despite earlier font registration. The code was
   reverted; the patch and all ten visits are archived for reference only.
2. **Launcher attribution: closed, no change.** On the same control batch,
   the foreground request to Rust entry took 498 ms at the median; the
   portrait transaction took 300 ms and display takeover 95 ms. Do not remove
   the power, orientation, display or touch checks or the background watchdog.
3. **Direct Library feasibility: pending.** In the firmware probe with a
   synthetic Library that still needed initialization, the early Scribe
   metadata was ready 1.221 s after loading SEND. PW6's early arm passed;
   its baseline failed the framebuffer-difference check. Both guests exited
   and cleaned up. The SSH agent refused subsequent connections before the
   initialized-fixture follow-up, which is the next experiment to run.

Open the [resume procedure, results and archived tools](docs/review/startup-sequenced-20261126/README.md#resume-item-3-on-test-env).
The first two items used an isolated **foreground launcher**, not a stock
Library card click; no current card-to-screen time or optical settling time
was measured. The physical Scribe batch restored the original state, removed
its files and verified release. Item 3 used only disposable firmware guests;
there is no Aidoku lease to recover or resume. Any later physical work must
coordinate a fresh lease immediately before device commands.

## Start here: 2026-11-22 (Asia/Shanghai)

**Historical record. Follow the November 28 handoff above, not the R3 actions below.**

**Decision at the time: keep the daily R3, existing Library paper fill and glyph cache.** A new [ordinary-card draw-accounting study](docs/review/startup-release-draw-20261122/README.md) measured **2.960 s** at the card-to-complete-Library median (one warmup excluded, three formal openings). The first business draw was **623.075 ms elapsed** at the median, but only **96.852 ms of GUI CPU**; the median within-visit CPU share was **15.6%**. All three draws recorded 140 major faults. The elapsed difference is not a measured I/O or scheduler delay; these visits unpacked a new executable file each time. In the separate [release Gray8 A/B](docs/review/startup-release-draw-20261122/composition/README.md), removing a provable 4.18 / 1.87 MB redundant fill did not speed up either layout. Do not add a second [glyph cache](docs/review/startup-release-draw-20261122/glyph/README.md). Keep the new optional same-frame logger; the tested diagnostic was not installed. All four independent leases were returned and the last release was at **07:36:47**. The next discriminating comparison is [ordinary opens with controlled executable identity](docs/review/startup-release-draw-20261122/device/INTERPRETATION.md), not a speculative renderer rewrite or blanket prefetch.

**WeRead protection baseline — November 22, 07:42:22 Asia/Shanghai (WeRead operator report).** After Aidoku's 07:36:47 return, WeRead reported that all 38 deployed public files matched on readback and its ordinary stock card opened the existing grid Library. It restored native state, removed its staging files, closed its connections and released its original lease. The reported sizes are **95,228,264 bytes** for `/mnt/us/weread/app/weread` and **137,236 bytes** for `/mnt/us/weread/launch.sh`. The original `/mnt/us/documents/weread.sh` remains **43,102 bytes**; the two WeRead helpers, Aidoku and the shared entry were reported unchanged. Aidoku did not independently inspect the device. Neither project's return is a claim of current availability.

The consumed Aidoku guard pins the previous **95,137,176-byte** WeRead executable and will reject the reported update. Leave that historical guard and its receipts intact: do not bypass the check or restore old WeRead files. Qualify a **new** guard against the updated WeRead deployment for the next batch, retaining the card, helper, shared-entry and within-visit checks; file size alone is not proof of file identity.

**Earlier modification decision: keep R3 and upstream Slint.** Reusing same-frame text shaping before line breaks eliminated 18 / 17 actual reshapes on the six-book host fixture, but [optimized Gray8 A/B against stock Slint](docs/review/startup-library-transition-20261122/first-draw-profile/release-gray/README.md) saved only **0.198 ms on Scribe (6/6)** and **0.060 ms on PW6 (4/6)** at the paired draw median. Neither passed the 1 ms screen. Do not install it, add the private cache, or call the encouraging debug RGB result a card speedup. The [subsequent release CPU profile](docs/review/startup-release-draw-20261122/profile/README.md) distinguishes drawing stages on the host; it does not apportion a Scribe frame's native elapsed time between them.

**Latest diagnosis:** the [four-visit ordinary-card transition study](docs/review/startup-library-transition-20261122/README.md) found that the **first complete Library software draw** took **637 ms** at the median and **91–96%** of the interval between scene-ready and the accepted Library frame. One warmup was excluded; the three formal card-release-to-Library midpoints had a median of **3.081 s**. Of the **163 ms** difference between the slowest and fastest *initial-full-to-Library transition*, **147 ms** was after the scene was ready. The protected initial fence barely varied. Do not remove it or change the launcher. All four visits restored R3 and native state, and the last lease was released at **06:22:08**. A [same-frame text shaping experiment](docs/review/startup-library-transition-20261122/first-draw-profile/PRELINE-TRIAL.md) tests reuse before line breaks; host improvements are not a stock-card speedup.

**Preceding study:** five independent [ordinary-card stage visits](docs/review/startup-card-stages-20261122/README.md) all closed on the Scribe. Excluding one warmup, the median card-release-to-Library interval midpoint was **3.203 s**. The largest main stage was Rust `main` to Library (**1.685 s** median); the initial full frame to Library alone took **1.207 s** median. The slowest launch exceeded the fastest by **1.071 s**, with **763 ms** of that difference inside the application (including **479 ms** after the initial full frame), and **319 ms** between foreground launcher birth and `main`. These are different visits and do not constitute a before/after comparison. The diagnostic build was not deployed; original R3 and native state were restored at **05:10:25**.

**Latest normal-card decision:** the [physical JPEG-admission A/B](docs/review/startup-jpeg-native-20261122/README.md) completed all 14 Scribe visits. Excluding warmups, on-demand qualification was earlier in **2/6** pairs; the paired median A−B was **−31.223 ms**. Order-specific medians had opposite signs. Keep the demand policy for avoiding unnecessary work, not as a demonstrated first-screen speedup. Do not deploy either test build; the last visit restored the original R3 and released the device at **04:15:47**. Investigate the card-to-launcher and launcher-to-application intervals before choosing the next optimization.

**The startup candidate is not installed as the daily driver.** The last verified
installation is R3, product `8baf57f`, with application `1b1c508e...`, launcher
`412163f1...` and portrait helper `bde28cd5...`. The measured source merged
in `1976df8` is the tested **post-SEND** candidate: start preparation after the first
successful full-frame submission, and enable the required ARM features only
for the Clang-compiled NEON cover kernel. Merging this code does not install
it on the Kindle. The rejected warm-start experiment is not included.

### Shared Kindle: cleanup complete; ordinary acquisition reopened

At 09:56:42 Asia/Shanghai on November 21, after the user confirmed the former Aidoku session had ended, an administrator retired its specific **host lease**. The unchanged ownership record was moved into private quarantine in an atomic exchange with a maintenance gate; there was no interval for a new acquisition and no old token was used. This action did not connect to the Kindle. Its host-only receipt is `test-env:/var/tmp/kindle-build-coordination/aidoku-orphan-retired-20261121.json`. **Do not run the original-owner recovery command below against this retired lease.**

The user subsequently reported seeing the stock native page. This is separate evidence (`aidoku-admin-visual-observation-20261121.json` in the same coordination directory), not proof about hidden stage directories, program mounts, lighting or the saved navigation pointer. Do not retry the icon visit, reinterpret the administrator's retirement as an ordinary release, or enter a password in chat.

At 10:09 the administrator's first authenticated close returned `not_closed / refused_mount_or_file_changed`. Its connection ended and the host maintenance gate returned. The frozen program selector uses that single refusal for its mount, journal and file-identity checks. The outer check had just compared all three installed programs to their control bytes and found no direct overlay; the refusal does not establish a changed executable. The visit recovery precedes this program check, so the first `close-only` command is no longer the correct entry. Do not retry it, edit its record, or force an unmount.

The subsequent `--repair` attempt returned `not_repaired / refused_guard_state_changed`. Its pre-repair snapshot found no program mounts or live readers; all three installed files matched their control bytes. `app.source_meta` is the first **old journal** comparison that failed in that snapshot (candidate inode and modification time). It is not the reason the later guard refused, and no specific guard field was reported. Neither command should be repeated.

The user authorized **discarding this batch's leftovers without restoring historical settings**. Cleanup is limited to the two tagged study directories and any independently identifiable lock belonging to them. It must not replace applications, change navigation or lighting, or touch a peer's lock. Only a separate, verified cleanup-only receipt and the end of the maintenance hold permit a new operator to acquire a fresh lease. This is not a historical native restoration and does not change the failed study result.

The one-time cleanup completed at **10:50:15 Asia/Shanghai on November 21**. The host independently verified the private completion and retirement records through `ssh test-env /var/tmp/kindle-build-coordination/aidoku-cleanup --status`; the separate public receipt is `test-env:/var/tmp/kindle-build-coordination/aidoku-admin-cleanup-20261121.json`. `resolved_by_admin_cleanup: true` and `restoration_verified: false` are both expected. The shared lease was observed free at handoff. Do not rerun cleanup; acquire a new lease only when ready to start. This receipt is not a reservation.

### First action: close the interrupted repeat

The following instructions record the **superseded original-owner procedure** and explain why closing the study was necessary.

At 06:07 and again at 08:46 the `test-env` host verified that the original
Aidoku lease was still held by the matching owner and token. There was no
recovery receipt or verified release for this repeat. This was a **host-only**
check, not a new observation of the Kindle. The new R3 reference had closed
normally at 05:08:18; the subsequent pictures-first repeat stopped at native
tile admission, **before its first document click and before any candidate
selection**. It produced no timing samples. Pinned SSH last reported
`No route to host` at 05:22:33; the cause is unknown.

At 09:39 the [new single-visit launcher diagnostic](docs/review/startup-followup-20261121/launcher-visit/README.md) was sealed on `test-env`; all 67 worker tests passed. Its device-free preflight verified the package and SSH host-key pin but found no verified predecessor close and a non-free lease. No new attempt was created. At 09:40 the indexed original batch still had zero recovery receipts. WeRead has a ready two-launch pilot and BiliComics reported desktop-only work; no one has been promised a time slot. If the original chat is unavailable on the current desktop, ask the user to reopen it on its original host; do not impersonate its operator.

1. When the Kindle is reachable, the **original operator** uses the private
   `work`, `plan` and `batch` paths in
   `test-env:/var/tmp/kindle-build-coordination/aidoku-operator-recovery-20261121.json`.
   Keep the existing owner, token and trusted SSH configuration. Do not
   substitute a new thread ID, reclaim the lock, or copy scripts from this
   checkout over the frozen worker. If the device is asleep, it only needs
   to be awake and on Wi-Fi; no one needs to open Aidoku.
2. With the original operator's authenticated transport on the `test-env`
   **host**, run `python3 -B "$WORK/operator/run_study.py" "$WORK" recover "$PLAN" "$ORIGINAL_OWNER"`.
   Use `recover`, never `run`: it only closes the owned batch; it cannot
   retry the tile press. The new idle-timer guard is for future plans only.
3. Before handing the device back, check the **new** `recovery-*.json` for
   `device_closed`, `native_restored_and_connections_closed`,
   `owner_and_token_checked` and `lease_directory_absent_after_release`.
   Notify the other operators. The failed study receipt stays failed even
   after a successful recovery. If close is refused, retain the original
   lease and do not start a new batch or claim restoration.

### Measured results and next optimization

**Current primary metric: normal stock Library card release → first rendered application Library.** The first screen must contain the Library layout, its entries (or its correct empty state) and navigation; covers may still be placeholders. Do not wait for input readiness or all covers. No sort action, recording or manual step is required. The operator runs the test and interprets its logs. See the [render-only protocol and results](docs/review/startup-followup-20261121/rendered-library/README.md).

Keep the observation method explicit. A qualifying application frame submission is a software event, not an optical ink measurement. For the unmodified installed R3, whose normal launcher does not request the application trace, use the same visit's device-clock release bracket and a timestamped text log from a live, non-cover framebuffer predicate. Report its conservative observation upper bound, not an exact submission time. A staged launcher clock is not a card release, a single installed visit is not a paired improvement, and older measures retain their original anchors and names. The superseded touch pilot closed and released without tapping the card or producing any sample.

**New baseline, installed R3 on a Scribe:** all three automatic ordinary card opens gave rendered-Library upper bounds of **3,599.788 / 2,305.229 / 2,398.630 ms**; median **2,398.630 ms**. None of the visits waited for cover completion. Normal exit, installation checks, close, absence and owner/token-checked release passed at **11:59:01**. No candidate was installed. Separately, reanalysis of historical staged submissions showed no consistent first-Library gain for post-SEND: **+2.139 ms (5/10) cold, +3.431 ms (3/6) warm** at the paired median. Keep R3 as the daily driver; the confirmed cold cover speedup is secondary. [Result and per-visit bounds](docs/review/startup-followup-20261121/rendered-library/stock-r3.json).

A newly scoped [normal Library tile comparison](docs/review/startup-native-20261121/README.md)
uses real stock pointer presses and the original six-book Aidoku shelf. Its
separate check of the untouched R3 measured 2.090–2.166 s to text/navigation
and 2.915–2.993 s to all seven covers. That single visit verifies the entry
and timing path; it is not the repeated-open median or a candidate A/B.

**Actual repeated opens, six new pairs:** R3 took **2.238–2.314 s** to book
text/navigation and **2.635–2.712 s** to all covers; the post-SEND candidate
took **2.232–2.309 s** and **2.607–2.684 s**. Paired midpoint savings were
**16.5 ms** and **140.0 ms**, with four wins in six on each endpoint. These
are descriptive framebuffer measurements, **not an accepted warm speedup**.
The two final bodies and right toolbars matched each other exactly; both
failed the frozen full-toolbar comparison against the older, 91% battery
reference (both showed 89%). This study restored R3 and released at
05:06:00 Asia/Shanghai. Do not mix its samples with the staged study or
replace a failed picture with another one.

**Current first-screen change:** [defer the optional JPEG capability check](docs/review/startup-followup-20261121/jpeg-probe-admission/README.md) until the first opted-in Reader image job, explicit enable or hardware settings view. Six historical Scribe traces showed this successful check took a median **25.456 ms** before a shelf that did not use it. On `test-env` the [software-render A/B](docs/review/startup-followup-20261121/jpeg-probe-admission/HOST-TRIAL.md) removed **13.83 ms on the Scribe layout and 14.63 ms on PW6** at the paired median (8/8 each, identical pixels); the full library suite had 862 passes and no failures. The [subsequent normal-card comparison](docs/review/startup-jpeg-native-20261122/README.md) used identical submission logging in both arms, with solely the eager probe line added to A. It did not show a consistent card gain. Leave R3 installed; do not reuse a consumed device plan or subtract the historical probe duration from its baseline.

**Separate launcher investigation:** measure power-observer readiness,
portrait calibration and display takeover separately. The observed interval
from launcher process creation to application process creation was
**825–845 ms on R3** and **830–850 ms on the candidate**. This identifies
where to look, not 0.83 s of removable work. Keep the first-frame/input,
native exit and restoration checks intact. Use a new plan and lease for
any later device test; do not promote this candidate on those timings.

The [next diagnostic workflow and verified cost breakdown](docs/review/startup-followup-20261121/DIAGNOSTICS.md)
adds opt-in launcher timing and a one-snapshot window-manager takeover candidate.
The `test-env` lifecycle fixtures passed. Offline replay of all ten closed cold
pairs placed the candidate's six JPEG decodes at 948 ms and the mixed
post-validation derivative stage at 581 ms (per-visit medians). Reusing the
JPEG decoder for the header check preserved pixels but missed the host trial
threshold (0.734%, 8/10). Keep the decoder unchanged. The [completed derivative
trial](docs/review/startup-followup-20261121/DERIVATIVE.md#decision-keep-adaptive-filtering)
also rejected disabling PNG row filters: the original shelf wrote 33.9% slower
(0/10 faster pairs) and the structured shelf saved only 6.4% (9/10; 10%
required). Keep adaptive PNGs; among those earlier trials, retain only the
one-snapshot launcher change as a production optimization candidate (two live-WM stat reads reduced to
one, not a measured launch-time saving). The largest default write stage on
Linux/XFS was durable commit; separate its sync and index time before changing
I/O policy. No new device timing or rollout took place in those experiments; the
separate automated render-only stock-card visit must pass its own admission and release checks.

The [November 21 follow-up](docs/review/startup-followup-20261121/README.md)
independently confirmed the post-SEND candidate's **empty-cache** cover gain
on the Scribe: **4.657 → 3.980 s**, a paired **681 ms** saving (10/10).
The first usable frame did not consistently improve (2 ms, 5/10). The
[November 20 screen](docs/review/startup-fast-20261119/PHYSICAL.md) had
found 709 ms on empty-cache covers and just 3 ms on warm first usable (3/6).
These are staged managed launches, not native Library tile presses. The
original interrupted tile experiment was closed by cleanup only, not by verifying its historical snapshot. A fresh warm candidate
eliminated an intermediate frame but was **38 ms slower** to first usable and
**53 ms slower** to all covers (two wins out of six on each). Its source
changes were reverted; do not install that binary. See the same follow-up for
the retained original program bytes and modes, collected separately after
the trials. No user-data backup or change was involved.

| What the next session needs | Source of truth |
| --- | --- |
| Historical R3 baseline | [November 19 native Library tile study](docs/review/startup-entry-timing-20261119/README.md): **2.15 s** to book text/navigation, **2.52 s** to all covers. Separate from the November 21 pairs; neither measures optical settling. |
| Candidate source | `bea63d4` contains the Clang NEON build correction and post-SEND scheduling change on top of `61fe3e1`. Both product files matched the measured worker sources byte-for-byte; the retained ARM program matched the candidate used in the tile study. See the [cross-build record](docs/review/startup-fast-20261119/REBUILD.md) and [separate outcomes](docs/review/startup-fast-20261119/PHYSICAL.md). |
| Acceptance decision | [Trial rules](docs/review/startup-fast-20261119/PLAN.md). The **300 ms** warm first-screen and **1 s** empty-cache complete-cover reductions are targets, not measured gains. |
| Installed version to protect | [R3 installation and rollback receipt](docs/review/startup-r3-qualification-20261119-8baf57f/release/installation.json). Do not use the historical plans below for this candidate. |

**Historical WeRead protection baseline (owner report, 2026-11-20; superseded November 22).**
After the Aidoku screens, the WeRead operator deployed the tested 95,137,176-byte
app and its two packaged helpers. `/mnt/us/documents/weread.sh` is now the
packaged `document-launcher.sh`; `/mnt/us/weread/launch.sh` is the production
supervisor and must be preserved. Do not expect the historical single-file
launcher or an absent supervisor. The operator reported a successful normal
entry and exit, restored lighting, unchanged Aidoku, BiliComics and shared
registration, and an owner/token-checked release. This is not an independent
Aidoku inventory; use the WeRead deployment package for exact file comparisons.

**BiliComics protection baseline (owner report, 2026-11-21).** BiliComics
retained its normal installation after a successful Library tile launch and
reported an owner/token-checked release at 04:47:08 Asia/Shanghai. Protect
the single lowercase `documents/bilicomics.sh` and its catalog row, and the
installed launcher, 74,340,832-byte application and 426,672-byte JPEG worker.
An uppercase lookup can yield a different virtual inode for the same lowercase
directory entry; it is not a second file. Do not expect the historical absence.
The owner's record on `test-env` is
`/var/tmp/kindle-build-coordination/bilicomics-production-20261121.txt`.

Exact locations of the retained control, measured candidate, private trial
archives and original reports are indexed on the `test-env` host in
`/var/tmp/kindle-build-coordination/aidoku-startup-evidence-20261121.json`.
The repository includes the operator sources and aggregate results, not
device credentials, user pictures, program archives or live lease records.

`ssh test-env` is reachable but was completely rebuilt. The old build layers,
sealed fixtures, native view references and ownership records were not present.
Independent host checks now use the [shared toolchain and clean-worker
procedure](docs/review/startup-fast-20261119/operator/clean-worker/README.md).
The corrected ARM build, pixel parity, launcher and both desktop profiles
passed; the known PW5 dark-sleep assertion still fails. These results cannot
replace private input recovery or qualify R3-vs-candidate timings.

For this handoff, the retained ARM program was rechecked under QEMU with
NEON available and with NEON/D32 disabled: version and all six default
startup settings passed in both configurations without creating app data.
The earlier release build and 23 startup tests apply to the byte-matched
product sources; neither a fresh release build nor a device rollout was
performed for this merge.

Pre-merge fixture verification on `ssh test-env` passed 93 Python tests
(70 native-entry and 23 clean-worker/staged/retained-control), shell syntax
checks, two native C fixtures and two ARM/QEMU + Xvfb input suites. These
checks used isolated inputs and no physical lease; they do not qualify the
failed visual comparison or restore the interrupted device session.

**Historical protocol:** reproducing the November 19 native-tile study itself
still requires its original private lab. The separately scoped November 21
comparison above obtained new reference images, verified the real stock tile,
and bound every visit to a new device-clock anchor. The
[ordinary installed-entry checks](docs/review/startup-fast-20261119/operator/normal-entry/README.md)
are separate from timing; the confirmed empty-cache result does not qualify
the normal entry or replace a rollback check.

<details>
<summary>Historical R3 handoffs (read-only context; the old plans are not next steps)</summary>

## Continuation completed on 2026-11-19

R3 is installed and qualified. Continue from the
[final qualification and release report](docs/review/startup-r3-qualification-20261119-8baf57f/README.md),
not the historical pending list below. The continuation branch is
`codex/startup-r3-handoff`, based on the original `23a3640` handoff. Product
sources remain the measured `8baf57f` implementation. No merge or push to
`main` was performed.

The installed tuple is application `1b1c508e...`, launcher `412163f1...`, and
portrait helper `bde28cd5...`. The existing Library document, app cover and
shared catalog registration were preserved. The normal installed entry,
same-process short sleep/wake, body and right-toolbar pixels, notifications,
ordinary exit and native/light restoration passed. The program transaction
preserved the user database and cache storage. Exact previous program copies
were retained and reverified; no actual rollback was performed.

All six controls are available in Settings > Startup & reopening. Only the
first Library layout is enabled by the frozen recommended policy, as a
functional default with no speed claim. The other five remain off by default.
No R3 individual screen met the prespecified speed rule, so no confirmation
cohort was added. Original negative results and both first right-toolbar
qualification failures remain recorded; fresh strict picture batches passed.

The ZIP and KPM passed 216 checks and are saved under
`dist/library-startup-20261119-r3/`. See the
[package identities](docs/review/startup-r3-qualification-20261119-8baf57f/release/packages.json)
and [installation receipt](docs/review/startup-r3-qualification-20261119-8baf57f/release/installation.json).
The 50-test corrected release-tool suite passed. The inherited PW5 dark
sleep-screen assertion remains documented as a pre-existing failed check.

All device batches are complete, their owned native state is restored and
their leases are released. No further run is scheduled. Earlier plans were
consumed and still bind the previous installed control; future device work
needs new plans and lease identities against the verified R3 tuple. The
original plans, raw observations and original tools remain preserved.

## Historical stop record

The text below records the state before this continuation. Its pending items
have now been completed or replaced by the explicitly documented fresh
qualifications in the final report above.

**Stopped on 2026-11-19 (Asia/Shanghai), at the operator's request.** The
implementation is committed, and the final candidate passed both default timing
checks. **R3 is not installed or packaged for release.** No device work is
running, no lease from this task is outstanding, and no future run is scheduled.

## Code and device state

| Item | Exact state at handoff |
| --- | --- |
| Work branch | `codex/startup-catalog-optimization`; product commit `8baf57f` |
| Daily driver | `c6867d1`, app `9d92dc4c...`, launcher `10432125...`; unchanged |
| Candidate | `final-v3-clean`: app `1b1c508e...`, launcher `412163f1...`, native portrait helper `bde28cd5...` |
| Build identity | Source manifest `28c1bb31...`; receipt `516d572640a5f04b3243180b42b5f792f80687664febdac182f2bc8dc6e083c8` |
| What differs from R2 | Only `packaging/kindle/launch.sh`. R2 and R3 share the app and helper bytes; the app hash alone cannot identify R3. |
| Shared resources | Keep the current Aidoku Library document, logo and qualified shared catalog registration; preserve the user library, cache and preferences. |
| Git and packages | No merge or push to `main`. Existing ZIP/KPM files under `dist/library-startup-20261118/` belong to the previous release. |

The [prior release record](docs/review/startup-next-20261118/README.md) and its
[build evidence](docs/review/startup-next-20261118/status-trial/proof/README.md)
identify the version to protect during a later installation or rollback.

There are [six user controls](docs/review/startup-all-20261119/settings/README.md).
Only the streamlined Library layout is recommended by default. Portrait
supervision, prewarming, final-cover grace, post-import thumbnails, and quick
return remain independently selectable. Quick return holds up to 2 MiB of
validated local cover pixels for at most 45 seconds. Reopening starts a **new
GUI process**. The original GUI surviving a return to Home is a [host
prototype](docs/review/startup-all-20261119/resident-owner-prototype/reentry/README.md);
the [installed entrance did not establish the necessary per-visit
identity](docs/review/startup-all-20261119/evidence/installed-gui-entry-observation.json).
Do not treat a keeper hit or a same-process screensaver wake as a same-GUI reopen.

## Completed evidence

The [round report](docs/review/startup-all-20261119/README.md) and [R3 evidence
index](docs/review/startup-all-20261119/evidence/r3-index.json) are the record
of what was actually observed. R3 used the same indexed item as the daily
driver. Each regime has six separate, balanced pairs. Positive paired medians
mean less waiting on the candidate. Timing ends at a software/driver boundary.

| R3 default vs installed | Usable Library frame | All visible covers | Return to native Home |
| --- | ---: | ---: | ---: |
| Warm thumbnails | +101 ms (4/6 faster) | +162 ms (5/6) | +441 ms (4/6) |
| Empty thumbnail cache | -9 ms (3/6) | +48 ms (4/6) | -115 ms (3/6) |

Both regimes passed the prespecified 50 ms median regression guard on the
first two endpoints. This is **not** release approval or a confirmed speedup
for each switch. Do not repeat these twelve pairs. Their formal reports are
`v3-default-w.json` and `v3-default-c.json` on `test-env`; the earlier R2
analyzer rejection of the new launcher is retained separately.

Other results that must not be relabeled as R3 device trials:

- The unchanged application passed 852 library and 22 startup tests; the two
  desktop profiles passed 29 settings cases / 793 checks. One [pre-existing
  dark-cover UI assertion](docs/review/startup-all-20261119/build/sleep-baseline-control.json)
  still fails identically on the installed baseline and candidate; five PNGs
  matched byte-for-byte. The remainder of that failing suite was not exercised.
- R2 prewarm consumed real prepared pixels. The initial six-pair benefit did
  not hold in ten independent pairs: complete covers were 6 ms slower at the
  paired median. The R2 default warm check also failed. Preserve those results.
- The isolated import trial verified real offscreen thumbnails and later cache
  hits. An earlier batch that failed the original-cache guard was explicitly
  excluded. A new helper or pre-existing thumbnail is not proof of net speedup.
- The R2 quick-return pilot observed the distinct keeper and a fresh GUI.
  R3's default lifecycle, independent pictures, individual screens and keeper
  qualifications are still pending. Selected host fixtures are functional
  evidence for identical app bytes, not additional R3 device measurements.

## Resume from here

**First pending action: the R3 default lifecycle batch (`lifecycle.defaults`).**
It uses the actual Library entry, the frozen default policy, separate app data,
and the exact candidate program tuple. It has not been run. No rebuild or
new plan is needed. Use one operator and a fresh lease immediately before
each continuous device batch, restore native state and release before any
analysis or wait. Do not take another session's lease or reuse a released one.

On the Windows host, the following read-only command lists the frozen plans,
their digests, and their status when the task stopped. It does not open a device
connection. Do not run every plan blindly: a later operator may have used one.

```powershell
ssh test-env 'cat /root/aidoku-all-release-20261119/handoff/r3-plan-index.json'
```

Inventory SHA-256: `d2b29f829a081bba6f579102da135e414ab7578fb3dc7d63edf5efe57772854e`.
It binds **19** existing plans: two completed and released, 17 prepared but not
run. The first pending plan's SHA-256 is
`b23e0e85ca825bacc3e13d0fc72ac8eafd946d88bbb103bd1170d1d0cece39ea`.
The inventory is private to `test-env`; it contains no reusable lease token.

1. Recheck the plan digest and whether its run already started. For an unused
   plan, run its own `runner.py run plan.json` from its directory on `test-env`.
   For a started plan, inspect the existing report; follow its recovery rules
   when needed. Never convert a failed run to a successful one by rerunning it.
2. Verify the default lifecycle off-device with `defaults.py qualify`. Then run
   the independent default warm/cold pictures and bind them to the *existing*
   timing samples. Check Library content and the observable right-hand Wi-Fi /
   battery area; do not time the screenshot cases.
3. Use the [individual trial and pixel instructions](docs/review/startup-all-20261119/bench/R3.md)
   and [specialist plan guide](docs/review/startup-all-20261119/bench/r3-specialists.md)
   for layout, prewarm, final-cover grace, native portrait, import, all-options
   and quick return. A separate portrait pixel plan is still to be prepared.
   Preserve zero-trigger samples and all unfavorable pairs. Confirm an eligible
   individual screen with ten fresh pairs before claiming sustained benefit.
4. For quick return, check normal exit, immediate reopen, TTL expiry and
   sleep/wake on the second GUI. For import, use the guarded synthetic fixture
   and report both visits and background costs. Do not touch the user's cache
   to manufacture a miss. See the [original decision rules](docs/review/startup-all-20261119/PLAN.md).
5. Only after the outstanding gates pass, use the [release workflow](docs/review/startup-all-20261119/promotion/README.md)
   on `test-env` to replay the evidence, assemble the release, back up the
   installed program, and verify an unmodified installed-entry startup and
   rollback path. If a gate fails, retain `9d92dc4c...`. Record actual deployment
   and release evidence before telling other sessions to protect a new build.

The host release-tool suite previously reported 48 passing checks. Two final
source/trace integrity checks were added after that run. Recheck the release
tooling before packaging; do not describe that earlier suite as verification
of these last edits. This does not invalidate either completed timing batch.

</details>
