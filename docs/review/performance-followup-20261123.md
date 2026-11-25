# Kindle performance follow-up: November 22–23, 2026

The [qualified R3 installation](startup-r3-qualification-20261119-8baf57f/README.md)
remained in place at the end of these studies. The results below are separate
cohorts, not a combined benchmark or acceptance of the experimental changes.
See [performance opportunities](performance-opportunities-20261122.md) for the
earlier cache and Reader investigations.

## Library startup

- **Installed Scribe R3:** Three successive ordinary card opens took
  **3.364–3.487 s**, **2.034–2.156 s**, and **2.036–2.157 s** from card release
  to the confirmed Library framebuffer match, without requiring covers. GUI
  process creation to Library was the longest stage on all three opens. The
  experiment did not force a cold cache or measure optical ink settling.
- **First draw:** In a separate diagnostic cohort, the first Library draw took
  **741–911 ms** with **97–98 ms** of GUI-thread CPU. Runnable wait accounted
  for less than **3%** of its off-CPU time; the rest remains unattributed. This
  does not establish storage I/O as the cause.
- **Portrait calibration:** Across three other Scribe opens, authorization to
  result publication took at least **170 / 240 / 240 ms**. Publication to parent
  confirmation took at most **30 / 40 / 50 ms**, below **18%** of the authorized
  interval each time. The result does not justify shortening the parent's poll.
- **Power observer ordering:** In one pilot pair, portrait calibration began
  **130–170 ms** earlier, but the uncertainty bounds for the card-to-Library
  difference included zero. A later launcher-only installation was rolled back
  when the ordinary-card smoke failed to confirm the Library framebuffer. A
  subsequent metadata-only check could not establish that the card was visible.
- **Background toolbar:** The [historical firmware runs](../FIRMWARE_EMULATOR.md#startup-regression-november-23-2026)
  produced a usable Library through fallback on the Scribe and PW6 emulators;
  neither used the candidate's snapshot with the helper present. That confirms
  neither the fast path nor a physical startup improvement.

## Reader

The second-book physical Scribe comparison used six matched process pairs per
mode. The control was built from `a38b9b3`, not the installed R3. The measured
endpoint was application action to driver acceptance of the matching framebuffer
update; it excludes physical touch sampling and optical ink settling.

| Mode | Paired median change | Candidate earlier | Decision |
| --- | ---: | ---: | --- |
| Fast, two pages | 26.446 ms faster | 6/6 | Passed the timing gate |
| Normal, one page | 9.688 ms slower | 1/6 | Exceeded the 5 ms regression limit |

Normal turns used the original source verification without keeping encoded
snapshots. The slowdown's cause was not established. The Fast-only experiment
was not deployed; it does not establish performance on a physical PW6.
