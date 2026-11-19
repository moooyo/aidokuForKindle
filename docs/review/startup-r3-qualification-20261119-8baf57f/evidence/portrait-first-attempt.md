# Portrait screen and retained first pixel failure

The later [fresh strict pixel qualification](portrait-screen-requalified.json)
passed, using exactly the original six timing pairs. The
[continuity record](strict-requalification.json) preserves both results and
checks the unchanged programs, settings, runner, auditor, and pixel regions.
The original failure described here remains unchanged. The numeric direction
threshold still fails, so the accepted new qualification has
`latency_gate_passed=false` and does not trigger ten-pair confirmation.

The first combined portrait report remains unqualified. The unchanged R3
analyzer returned `valid=false`, `qualification_passed=false`, and
`latency_gate_passed=null`, with the sole issue `right_status_bar_mismatch`.
All six timing pairs and both arms' operating evidence remain in the
[public first-attempt record](portrait-first-attempt.json). No device batch,
pixel rule, or original observation was changed to obtain this report.

Independent arithmetic on the complete timing sample gives:

| Endpoint | Median paired saving | Faster pairs |
| --- | ---: | ---: |
| Usable Library, primary | 124.409 ms | 3/6 |
| Complete covers, auxiliary | 238.089 ms | 4/6 |

The primary median and auxiliary tolerance pass their numeric thresholds, but
the primary count fails the required five of six directions. Thus even the
separate timing arithmetic does not qualify for ten-pair confirmation. The
failed pixel result remains an additional independent requirement.

All 18 cases, including warmups and independent pixels, observed the correct
six startup values and their actual portrait engines: shell on the control
and native on the candidate. The timing cases contain six observations per
arm and the independent pixel cases one per arm, excluding warmups. The raw
receipts confirm 14 completed timing stages and four pixel stages, with
26 and 16 recorded operations and zero failed operations. Protected snapshots,
native/entry/input restoration, owned-stage removal, owner/token-checked
release, and connection shutdown all passed.

## Pixel and sampling observations

The [host-only image diagnosis](portrait-toolbar-diagnostic.json) finds zero
body differences below the top 72 pixels. The right status bar differs at
1,144 pixels inside `[1585, 21, 1742, 51]`. This includes 21 battery-icon
pixels inside `[1739, 29, 1742, 44]`. Each native capture exactly matches its
own application's final screenshot, with zero differing pixels across the
full 1860-by-2480 image.

Two bounded OCR crops consistently read the displayed battery labels as
`25%` for the control and `24%` for the candidate; confidence values are
approximately 81.81 and 53.20. The Wi-Fi glyph moves two pixels left, from
`[1587, 21, 1623, 51]` to `[1585, 21, 1621, 51]`. Both glyphs have 376 foreground
pixels and are identical when their tight bounding boxes are aligned.
That alignment is diagnostic only and is never used as a replacement for
the unchanged strict right-status-bar comparison.

The candidate's independent pixel case has a distinct query path:
`startup_toolbar_probe_unavailable=1`, `startup_toolbar_sync_fallback=1`,
`startup_toolbar_snapshot_used=0`, and seven `native_query_response` events.
The other 17 cases successfully used their early toolbar snapshots. The
retained first All-settings failure had different displayed battery labels
as well, but both of its qualification arms used the early snapshots.

The retained sidecars and logs do not contain actual firmware battery or
Wi-Fi result values. Display OCR and the fallback event cannot independently
establish a firmware battery decrease, identify a native-portrait defect, or
resolve the causal explanation for the different displayed values. The
original full report remains on `test-env` at
`/root/aidoku-r3-handoff-20261119/reports/portrait-screen-with-pixels.json`.
No confirmation plan was prepared.

## Read-only source review and next qualification

The [source and query timeline review](../status-query/README.md) found that
both supervisors eventually read the same integer `powerd.battLevel` property
through the same query helper. The portrait transaction does not read or
carry battery/Wi-Fi values. The normal path parses the same integer; it does
not round one valid percentage into an adjacent percentage.

The first All-settings qualification used successful early snapshots on both
arms. Their selected battery-response events were 31.160957 seconds apart,
so the portrait-only fallback cannot explain both failures on its own.
In the portrait candidate, successful Wi-Fi and battery responses preceded
the unavailable event. Exhaustion of the 80 ms budget during session completion
best fits the retained sequence, but the absent error category prevents a
definitive timeout diagnosis. Its later ordinary battery response was
404.185 ms after the discarded response; the selected responses across the
two portrait arms were 30.544251 seconds apart.

No definite mode-dependent value defect was found, and the missing firmware
values still prevent independent proof of a physical battery decrease or a
complete exclusion of implementation problems. The unchanged preparation tool
froze a [fresh strict pixel qualification](../host-preparation/portrait/README.md)
under a new identity. That batch subsequently completed and released, and
the unchanged auditor passed the full body and right status-bar comparisons.
The original timing pairs and timing mechanism objects are identical in the
first and later reports. Both original failures remain retained.

The accepted complete source report is
`/root/aidoku-r3-handoff-20261119/reports/portrait-screen-requalified.json`
on `test-env`, with SHA-256
`362263137e8fcafcb898803acf0c4739b212447b56badfeef5583a5d47394e30`.
