# Handoff: Kindle Library startup and reopening

## Start here: 2026-11-21 (Asia/Shanghai)

**The startup candidate is not installed as the daily driver.** The last verified
installation is R3, product `8baf57f`, with application `1b1c508e...`, launcher
`412163f1...` and portrait helper `bde28cd5...`. The source being handed off
is the tested **post-SEND** candidate: start preparation after the first
successful full-frame submission, and enable the required ARM features only
for the Clang-compiled NEON cover kernel. Merging this code does not install
it on the Kindle. The rejected warm-start experiment is not included.

### First action: close the interrupted repeat

At 06:07 the `test-env` host still verified the original Aidoku lease owner
and token. No release receipt existed for this repeat. This was a **host-only**
check, not a new observation of the Kindle. The new R3 reference had closed
normally at 05:08:18; the subsequent pictures-first repeat stopped at native
tile admission, **before its first document click and before any candidate
selection**. It produced no timing samples. Pinned SSH last reported
`No route to host` at 05:22:33; the cause is unknown.

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

**Next optimization, after recovery:** measure power-observer readiness,
portrait calibration and display takeover separately. The observed interval
from launcher process creation to application process creation was
**825–845 ms on R3** and **830–850 ms on the candidate**. This identifies
where to look, not 0.83 s of removable work. Keep the first-frame/input,
native exit and restoration checks intact. Use a new plan and lease for
any later device test; do not promote this candidate on those timings.

The [November 21 follow-up](docs/review/startup-followup-20261121/README.md)
independently confirmed the post-SEND candidate's **empty-cache** cover gain
on the Scribe: **4.657 → 3.980 s**, a paired **681 ms** saving (10/10).
The first usable frame did not consistently improve (2 ms, 5/10). The
[November 20 screen](docs/review/startup-fast-20261119/PHYSICAL.md) had
found 709 ms on empty-cache covers and just 3 ms on warm first usable (3/6).
These are staged managed launches, not native Library tile presses. The
original private tile experiment has not been restored. A fresh warm candidate
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

**Shared Kindle — WeRead protection baseline (owner report, 2026-11-20).**
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
