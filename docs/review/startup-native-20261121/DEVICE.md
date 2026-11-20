# Worker to device command interface

The host always supplies the frozen shell body through the existing transport,
including on closeout. The command shape is `sh device.sh OP LABEL [ARGS...]`.
The final stdout line is an acceptance tag, except when collecting a gzip tar stream.
Never retry a case under its original name after crossing the request marker.

For newly frozen batches, `prepare` checks that stock is foreground and power
is `active`, then sends the product's one-shot `touchScreenSaverTimeout 1`
immediately before unpacking the upload stream. A stock-tap batch rechecks and
resets it once more before the first navigation, never during an observed
press-to-frame interval, an idle wait, recovery or closeout. No persistent
sleep setting or wake request is made. Idle sleep during the earlier refusal
is a hypothesis, not a confirmed cause; this does not change any frozen plan.

| Operation | Arguments after label | Purpose |
| --- | --- | --- |
| `prepare` | `reference`, `navigation`, `tapcheck` or `study` | Read a **host-approved allowlisted regular-member tar.gz** on stdin. The host checks member names, sizes, duplicates, types, permissions, bytes and the schedule **before acquisition**. |
| `navigation` | None | One stock Library tab visit with no program change or document request. Return and inspect the original tab and pointer, and verify native state and lights. `collect-case navigation` contains only `out/navigation/library.frame` and `out/navigation/qualified`; the qualifier reads `navigation stock_restored`. |
| `reference` | None | A normal installed R3 URI visit. Write private `reference/{stock.frame,library-a.frame,library-b.frame,qualified}` and exit normally. |
| `case` | `control-01`, `candidate-01`, `control-warmup`, etc.; `indexed-uri` or `stock-tap` | Select the normal program tuple, visit once, writing private `CASE/{stock.frame,trace,anchor,origin,qualified}`. |
| `picture` | `control-picture` or `candidate-picture`; origin | An untimed independent normal visit, with `CASE/{final.frame,qualified}`. |
| `collect` | None | Export only allowlisted files below `out/` from the state directory; private evidence is retained on the worker. |
| `collect-case`, `discard-case` | Case name | Stream one qualified case and, only after the worker safely retained its full stream, discard its local raster/trace. Keep the case journal. |
| `close` | None | Recover any open visit, restore the original program tuple, lights and native/peer state, then remove both owned stages. Refuse to clear an unknown or mismatched stage. |

Archive paths are rooted at the media stage, not under `operator/`. Reference
requires `guard/guard.sh`, `visit/visit.sh` and `observer/framewatch`. A study
also includes executable `rename-new`, `programs/programs.sh`, `control/program-modes` and the selected
three files under each of `control/` and `candidate/` (see `programs/README.md`).
`tapcheck` uses only the independently retained control tuple: it opens the
normal tile once without a program selector and cannot produce an A/B cohort.
The study selector temporarily binds the two candidate program files over
their normal paths; neither original is renamed or overwritten. After each
completed visit's trace has left the device, the optional `birth_ticks.sh`
reports only clock frequency and worker/GUI birth ticks. Missing ticks do not
affect the framebuffer endpoints or permit substituting another visit.
The shell sources no paths supplied by a caller. The private root is
`/var/tmp/aidoku-native-LABEL`; the fast media stage is
`/mnt/us/.aidoku-native-LABEL`. Failure reasons are short `refused TAG` lines
on stderr. `case`/`picture` stock pointer integration is fail-closed unless
its freshly frozen and validated tap assets are available.

Single-line success tags are `device_prepared`, `device_reference`, `device_case`,
`device_picture`, `device_discarded`, `device_closed`. All streams use relative
names `out/CASE/LEAF`, including `out/reference/stock.frame`. An anchor is
two decimal CLOCK_BOOTTIME microsecond values, separated by a space and newline.
