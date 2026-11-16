# Tab feedback and Perf05 final candidate

Date: 2026-11-16. Base commit: `a53b9716d64fe5521fac38431b24732c75cb8ff7`.

This frozen record contains 14 final desktop captures on PW5 and Scribe,
six final physical Scribe application captures, and one installed-payload
navigation capture. Four earlier physical reference images retain the
instrumented baseline's separate identity. Original pixels, state/geometry
sidecars, traces and replays remain source-bound.

| Interaction | PW5 | Scribe |
| --- | --- | --- |
| B04, idle search Tab | [PNG](desktop/pw5/pw5-day-idle.png) | [PNG](desktop/scribe/scribe-day-idle.png) |
| B04, another Tab held | [PNG](desktop/pw5/pw5-day-held.png) | [PNG](desktop/scribe/scribe-day-held.png) |
| B04, cancelled press | [PNG](desktop/pw5/pw5-day-cancelled.png) | [PNG](desktop/scribe/scribe-day-cancelled.png) |
| S01, selected settings Tab | [PNG](desktop/pw5/pw5-day-selected.png) | [PNG](desktop/scribe/scribe-day-selected.png) |
| B04, active Tab reselected | [PNG](desktop/pw5/pw5-day-active-reselected.png) | [PNG](desktop/scribe/scribe-day-active-reselected.png) |
| Night idle | [PNG](desktop/pw5/pw5-night-idle.png) | [PNG](desktop/scribe/scribe-night-idle.png) |
| Night held | [PNG](desktop/pw5/pw5-night-held.png) | [PNG](desktop/scribe/scribe-night-held.png) |

Native Scribe: [held Tab](native/scribe/final-nav-pressed.png),
[idle Tab](native/scribe/final-nav-released.png),
[selection sequence](native/scribe/final-nav-taps.png),
[first-visit Perf05](native/scribe/final-perf05-steady.png),
[rapid input](native/scribe/final-perf05-burst.png),
[repeated reading](native/scribe/final-perf05-steady-warm.png), and
[installed application](native/scribe/deployment-navigation.png).

The [manifest](manifest.json) identifies final and reference executables. The
[source manifest](source-manifest.json) and [checksums](SHA256SUMS) bind this
record. [Verification and original replays](../../review/tab-perf05-20261116/README.md)
distinguish each physical batch and its timing scope.

Desktop images are 618 x 824 and 930 x 1240 at scale one. Native Scribe images
are 1860 x 2480 application snapshots at scale two. They establish digital
output, not an optical count of panel flashes or gray levels. Later iterations
must use another directory rather than replacing these records.
