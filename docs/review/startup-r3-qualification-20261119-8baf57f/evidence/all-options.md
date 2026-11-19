# All-settings qualification

The [fresh strict qualification](all-options-requalification.json) passed
with the original programs, six-setting comparison, runner, auditor, and
complete pixel regions. `valid`, `qualification_passed`, and
`all_options_functional_passed` are true, with no issues or input failures.
The [continuity record](strict-requalification.json) binds the new result to
the unchanged method and preserves the first failed report below. This
All-settings result remains an independent required qualification for release.

The first independent All-settings batch completed all four operating cases
and restored its owned native state, but the unchanged R3 analyzer rejected
its exact right-status-bar comparison. The [first-attempt report](all-options-first-attempt.json)
retains `valid=false`, `qualification_passed=false`, and
`all_options_functional_passed=false`. Its only issue is
`right_toolbar_unverified_or_different`. This result is not replaced by a
passing package-selection gate.

All four cases separately observed their expected six effective values:

| Setting | Recommended arm | All arm |
| --- | --- | --- |
| Portrait | `shell` | `native` |
| Prewarm | `0` | `2` |
| Last-cover grace | `off` | `on` |
| First layout | `optimized` | `optimized` |
| Import thumbnails | `off` | `idle` |
| Resident pixels | `off` | `45` |

The actual portrait engine matched each arm. All four traces contain one
ordered `shutdown_started`, `shutdown_complete`, and `process_finished`
sequence, with no keeper publication or reclaim event. The final native
snapshot checks also completed. These observations cover ordinary exit;
they do not establish import work, keeper reacquisition, or a speed benefit.
The 16 recorded operations had no failures. Protected before/after bytes
matched, the owned stage was removed, and the owner/token-checked release,
connection shutdown, and lease-directory removal all completed.

## Retained toolbar difference

The [host image diagnosis](all-options-toolbar-diagnostic.json) exports only
numeric differences and bounding boxes. The two complete bodies below the
top 72 pixels are identical. The right status bar differs at 1,128 pixels,
inside `[1585, 21, 1705, 51]` in the 1860-by-2480 image. Each native framebuffer
capture is pixel-identical to its own application screenshot, so this record
does not show a capture/renderer mismatch.

The two application pictures show different battery labels, read as `28%`
and `27%`. Two bounded OCR crops returned the same labels; their Tesseract
confidence values were approximately 93.21 and 67.88. The Wi-Fi glyph moved
two pixels right, from `[1585, 21, 1621, 51]` to `[1587, 21, 1623, 51]`.
Both glyphs contain 376 foreground pixels and are exactly identical after
aligning their bounding boxes. `ui/widgets.slint` positions Wi-Fi relative to
the battery text's preferred width, which explains that movement. The
battery icon itself is unchanged.

The retained sidecars and toolbar events do not contain the actual firmware
battery or Wi-Fi result values. This evidence therefore does not independently
prove that the firmware battery reading decreased. Image alignment and OCR are
diagnostic only: neither changed the original comparison region or its exact
equality requirement, and the first qualification remains failed.

A [new independent All-settings qualification](../host-preparation/all-options/README.md)
was prepared and then completed with the original programs, runner, pixel
region, and strict comparison rule. Its four operating cases and 16 recorded
operations completed without failure; all six actual settings, shell/native
selection, ordinary shutdown with no keeper publication/reclaim events, and
restoration/release checks passed. Both full body and right status pixels are
exactly equal. No timing sample was rerun by that qualification.

The new complete source report remains on `test-env` at
`/root/aidoku-r3-handoff-20261119/reports/all-options-requalification.json`,
with SHA-256
`194908d7a2665725f989d319087f030c9aa50877d28856e52dc19a0d763cd234`.
The first report retains its original false qualification values.
