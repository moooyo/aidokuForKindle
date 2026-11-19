# R3 startup and quick-reopen qualification

**Release status: installed and qualified on 2026-11-19 (Asia/Shanghai).**
The R3 implementation supplies all six startup
controls. Its combined factory defaults passed the warm and empty-cache
protection checks, independent default pixels, and normal default lifecycle
qualification. Import, the four quick-return boundary qualifications, and
the fresh strict All-settings and native-portrait picture qualifications
also passed. The complete release selection passed its gate, the ZIP and KPM
passed 216 package checks, and the normal installed Library entry passed
startup, same-process short sleep/wake, exact body and right-toolbar pixels,
notification, exit and restoration checks.

The installed application is `1b1c508e...`, with launcher `412163f1...` and
portrait helper `bde28cd5...`. The original Library document, application cover
and shared registration remain in place. The program replacement preserved
the database and cache storage across its transaction. Exact prior program
copies were retained and reverified after the device lease was released.
See the [package identities](release/packages.json) and
[installed-entry record](release/installation.json).

All verification was conducted through `ssh test-env`. Reporting agents
replayed retained observations without operating the Kindle. Completed device
batches restored their owned native state and released their own leases. The
original twelve default timing pairs were reused, with new independent picture
qualifications.

## Candidate and available controls

The implementation is product commit `8baf57f`, handed off with evidence commit
`23a3640`. R3 retains the R2 application and portrait-helper bytes and changes
the launcher. The exact source and program identities are bound in the
[frozen R3 index](../startup-all-20261119/evidence/r3-index.json) and
[source-lineage replay](host-release/corrected-lineage-report.json).

| Program | R3 identity |
| --- | --- |
| Application | `1b1c508e…` |
| Launcher | `412163f1…` |
| Native portrait helper | `bde28cd5…` |
| Compiled source manifest | `28c1bb31…` |

Open **Settings > Startup & reopening** to save each choice. Quit and reopen
to apply the saved startup configuration. **Enable all** and **Use recommended**
affect these six controls. The [settings acceptance](../startup-all-20261119/settings/README.md)
preserves the two device-profile layouts, saved versus active selections,
localization, original captures and input replays.

| Control | Factory choice | Implemented behavior and evidence |
| --- | --- | --- |
| Native portrait check | Off (`shell`) | Optional native supervision of the indexed entry; both paths executed and fresh strict pictures passed. The speed screen failed. |
| Preload two cached covers | Off | Eligible cached pixels were actually prepared and consumed in every timed candidate; the speed screen failed. |
| Wait briefly for the last cover | Off | A bounded 40 ms grace path exists and has source-bound host boundary tests. This physical screen recorded no activation. |
| Prepare covers when idle after import | Off | Four previously offscreen derivatives were prepared and reused by a new GUI; R3 correctness and native pixels passed. |
| Simplify the first Library layout | On | Batched projection and reduced title work are available, with matching pixels. Its isolated speed screen failed. |
| Keep local covers for quick reopening | Off | An explicit Quick return action retains bounded cover pixels for up to 45 seconds. Reopening creates a new GUI; ordinary Exit remains complete. |

The frozen layout-on policy is **`functional_default_no_speed_claim`**.
It is supported by functional compatibility and the separate combined-default
guards, rather than an isolated latency improvement. The other five controls
remain independently available with their defaults off. No source or default
policy was changed to reinterpret an unfavorable screen.

## Combined defaults against the previous installed reference

Positive paired savings mean less time on R3. Each regime contains six
preselected pairs, with three in each order. The guard permits at most 50 ms
median paired regression on the usable Library and complete-cover endpoints.
Exit is reported separately.

| Thumbnail regime | Usable Library saving | Complete-cover saving | Exit-to-Home saving | Default guard and independent pixels |
| --- | ---: | ---: | ---: | --- |
| Warm | +101.088 ms (4/6 faster) | +161.602 ms (5/6) | +440.661 ms (4/6) | Passed |
| Empty | -9.358 ms (3/6 faster) | +48.212 ms (4/6) | -115.261 ms (3/6) | Passed |

The [default reports](evidence/default-qualification.md) retain all pairs,
including the slow cold pair. The complete Library body and observable right
status bar matched independently. These guards do not establish an isolated
or aggregate speedup, and no default timing sample was rerun.

The [default lifecycle replay](host-release/default-lifecycle.json) passed
all 11 checks and verified four physical frames across the two arms. It
covered the normal indexed entry, the resolved factory options, cooperative
ordinary exit, same-process short sleep/wake, input restoration, native
ownership, notification and released-lease evidence. The later
[installed-entry acceptance](release/README.md) separately verified the normal
application home and user library after the actual three-program update.

## Independent feature timing

The prespecified screen requires a primary paired median saving of at least
100 ms, at least five faster pairs out of six, and no more than 50 ms median
regression on the other startup endpoint. An eligible screen would need ten
fresh confirmation pairs with the stricter nine-faster-pair rule. No newly
evaluated R3 screen met its selection rule, so no new R3 confirmation cohort
was run.

| R3 screen | Primary saving | Other startup endpoint | Result |
| --- | ---: | ---: | --- |
| First layout | Usable -27.859 ms (3/6 faster) | Covers -186.839 ms (2/6) | Inputs and pixels passed; speed gate failed. |
| Cached prewarm | Covers -80.558 ms (1/6) | Usable -61.226 ms (1/6) | Actual consumption in 6/6 candidates; speed gate failed. |
| Last-cover grace | Covers -52.492 ms (2/6) | Usable -63.233 ms (2/6) | Inputs and pixels passed; all grace-trigger counts were zero. |
| Native portrait | Usable +124.409 ms (3/6) | Covers +238.089 ms (4/6) | Fresh strict pictures passed; three faster primary pairs are fewer than the five required. |
| Quick reopening | Second covers +172.674 ms (4/6) | Second usable -19.003 ms (3/6) | Six real pixel reuses; four faster pairs are fewer than the five required. |

The [feature screens](evidence/feature-screens.md) and
[resident screen](resident/screen.json) preserve every planned pair, actual
mechanism count, zero value and unfavorable result. The
[accepted portrait replay](evidence/portrait-screen-requalified.json) binds
fresh strict pictures to the same six timing pairs and returns a failed speed
gate. The first combined portrait report remains invalid, with its original
null latency verdict preserved. No timing pair was rerun or substituted.
The [controlled grace fixture](../startup-all-20261119/promotion/grace-fixture/README.md)
is host functional evidence for the identical application, not a physical
grace activation or a timing sample in this screen.

## Import and quick-return boundaries

The [R3 import qualification](evidence/import-pixels.json) passed eight stages
and 22 operations with no failures. It used the same synthetic sources and
only changed `import_thumbnail=off/idle`. The idle arm prepared four offscreen
derivatives; the later GUI reused all 15 visible derivatives without creating
new ones. The control created the four new derivatives and reused 11.
Prepared-target continuity, native ingest/reopen pixels, the complete per-file
user-cache guard, restoration and release all passed.
The [independent import process review](evidence/import-qualification.md)
records the ingest-to-new-GUI continuity without exporting private data.

This R3 import record contains zero timing pairs and makes no performance
claim. The [earlier R2 import trade-off](../startup-all-20261119/README.md)
remains a separate measurement of the identical application with its original
launcher composition. Its later-open benefit, initial-visit penalty and
additional background cost are not relabeled as R3 results or a net speedup.

The [quick-return report](resident/README.md) separately qualifies immediate
reopen, ordinary exit with retention configured, natural expiry, and sleep/wake
on the second GUI. It verifies new GUI admission for each Library request,
six actual retained cover hits, ordinary-exit cleanup and natural expiry
observed approximately 45.080 seconds after publication. The keeper does not
retain the previous GUI, input or framebuffer. Its 2 MiB bound applies to the
serialized cover payload, not process RSS. In the timed candidates, sampled
RSS had a median of 2,260 KiB; these observations do not measure peak memory
or battery energy.

The retained screen also exposes costs outside the second cover endpoint:

| Quick-return screen interval | Paired median savings | Faster pairs |
| --- | ---: | ---: |
| First shutdown to native Home | -165.745 ms | 1/6 |
| Second shutdown to native Home | -466.925 ms | 3/6 |
| Both exit intervals together | -1,216.849 ms | 2/6 |
| Both request-to-cover intervals together | +97.611 ms | 4/6 |
| Entire scripted two-visit span | -1,315 ms | 1/6 |

The entire scripted span includes deliberate application dwell, the observed
Home gap, resource-sampling windows and both exits. It is not a pure second
startup measurement. The [independent raw audit](resident/raw-receipt-audit.json)
recomputes the timing from retained application events and shell markers and
preserves the negative costs. The wake epoch/nonce checks were executed by
the frozen device script; separate raw epoch values were not retained for
an independent host-side recomputation.

## Retained failures and tooling repair

The first [All-settings picture attempt](evidence/all-options.md) and first
[native-portrait picture attempt](evidence/portrait-first-attempt.md) failed
their unchanged strict right-status-bar comparisons. Their Library bodies
matched, while the displayed battery labels differed and the Wi-Fi glyph's
position followed the battery label width. Those first reports remain failed.
The accepted [All-settings replacement](evidence/all-options-requalification.json)
and [portrait replacement](evidence/portrait-screen-requalified.json) use
fresh qualification identities with the original exact pixel rule. Their
[continuity record](evidence/strict-requalification.json) binds unchanged
programs, runners and comparison regions; the portrait timing pairs are
also unchanged. Passing these fresh qualifications does not rewrite either
first failure or prove why its displayed values differed.

The [status-query review](status-query/README.md) found no definite
mode-dependent battery or Wi-Fi value defect. Both modes use the same integer
property and helper. One portrait picture case selected the ordinary query
fallback after its early observation became unavailable. The original records
do not preserve raw firmware property values, so they cannot independently
prove natural battery decline or fully exclude an implementation problem.
OCR and aligned-glyph diagnosis did not replace the strict comparison.

The [release-tool repair](host-release/README.md) fixed variable shadowing in
source-lineage verification. The old 48-test suite had passed while the actual
lineage entry point failed. The corrected copy adds coverage of that entry
point and corrupted trace input; all 50 tests passed with zero skips. The
accepted lineage output remained byte-identical, with frozen product sources,
plans and original tools preserved. The copied public test now receives its
mandatory private reference through an environment variable; it does not
publish a private capture location or skip the test when the input is missing.

The unchanged application inherits the [source-bound build results](../startup-all-20261119/build/README.md):
852 library tests passed with six ignored, 22 application tests passed, and
29 PW5/Scribe settings cases passed 793 checks. These are retained build
results, not newly rerun application suites. The known PW5 dark sleep-screen
assertion still fails on both the installed reference and candidate; all five
captures available before the stop matched. The test counted dark paper as
artwork, and its later scenes were not reached. Its failure remains recorded;
the overall historical host application suite is not represented as all green.

## Release record

The complete evidence selection is bound by SHA-256
`0409dc728b91ede66bd8f2a1dc5a5c8677854760a7674437ad1e0d98a7d44076`.
The [additional All-settings admission](release/all-options-admission.json)
binds its strict accepted report, plan, six options and exact program tuple
to that selection. It was checked before installation because the historical
package-selection gate does not independently judge the All preset. The
selection and generated package plan were not modified to add this record.

| Release step | Recorded status |
| --- | --- |
| Accepted replacement All-settings qualification | Passed |
| Accepted replacement portrait pictures bound to existing timing | Passed; speed screen failed |
| Complete evidence selection and package gate | Passed; exact source and three-program tuple reproduced |
| R3 ZIP and KPM identities | Produced; all 216 package checks passed |
| Normal installed Library entry and short wake | Passed with normal user data and no benchmark routing |
| Program replacement storage guard | Database and cache storage identical immediately before and after replacement |
| Prior program backup | Retained and reverified after release; no actual rollback was performed |
| Native state, lighting, shared registration and lease | Restored or preserved; owner/token-checked lease released |

Use the [corrected release tools](host-release/README.md) with the
[package and install procedure](../startup-all-20261119/promotion/README.md).
The resulting plan has SHA-256
`a4036ef7599e73e7303da6c5274d407b6b9456c85d78785b58c7127b2765dc7f`.
The local installation files are in `dist/library-startup-20261119-r3/`:
`aidoku-startup-all.zip` and `aidoku-startup-all.kpkg`. Their complete hashes,
sizes, allowed changes and six feature dispositions are recorded in
[packages.json](release/packages.json). They are ignored distribution artifacts,
not repository source files.

The continuation is on `codex/startup-r3-handoff`; it has not been merged or
pushed to `main`. All reported times end at software or driver acceptance;
they do not measure optical e-ink completion. Public records contain approved
counts, booleans, relative timings and program identities. Private device and
fixture identities, user pixels, image digests and transport tokens remain
outside this report.
