# What delays the first Library after the startup frame?

**Follow-up:** a [separate Scribe cohort and optimized host experiment](../startup-release-draw-20261122/README.md) distinguish GUI-thread CPU from draw elapsed time, and test a redundant Library fill. The draw interval below is not CPU time; do not combine the two cohorts or infer an improvement from their medians.

## Scribe result: the first Library draw

**Optimize the first business frame's software drawing, not its framebuffer transfer or the initial input fence.** The ordinary stock card's release to complete Library submission took **3.081 s** at the median interval midpoint. All four visits closed independently; only the three formal visits are timings below. The first accepted loading frame to Library submission took **1.245 s** at the median. Of the four adjacent stages, scene-ready to Library submission was the longest on every visit and contributed most of the difference between the fastest and slowest transitions.

**Decision: keep R3; reject the experimental text-shaping cache.** Duplicate shaping was real, but eliminating it in a [release-build Gray8 comparison against unmodified Slint](first-draw-profile/release-gray/README.md) saved only **0.198 ms** on Scribe-sized software frames and **0.060 ms** on PW6-sized frames. Neither met the 1 ms threshold; PW6 was earlier in only four of six pairs. Keep the opt-in stage logger, not a private rendering-library fork. There is no native speed claim for this change and no reason to spend another device batch on it. The next useful test is to profile the **unmodified optimized draw** at panel resolution, separating glyph rasterization and pixel composition before choosing another change. Do not use host debug cycle proportions as ARM time estimates.

| Formal visit | Card release → Library | Initial full → fence | Fence → attach | Attach → scene ready | Scene ready → Library | Initial full → Library |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 3134.132–3136.012 | 365.595 | 4.467 | 174.825 | 784.260 | 1329.147 |
| 2 | 2853.882–2854.799 | 365.219 | 4.502 | 158.771 | 637.604 | 1166.096 |
| 3 | 3080.006–3081.557 | 364.848 | 40.220 | 141.951 | 698.042 | 1245.061 |
| Median | **3080.006–3081.557** | **365.219** | **4.502** | **158.771** | **698.042** | **1245.061** |

All times are milliseconds on the same device. Each row's four stages partition its transition; independently taken medians are not additive. The range in the card column reflects the input-release bracket, not sampling or population uncertainty. All visits observed the large Library layout, its populated grid and the same accepted frame; neither complete cover art nor panel settling was required. This cohort is diagnostic, not a timing of unmodified R3 or a before/after comparison.

| Formal visit | Software draw | Prior-buffer settle | Pixel write | Display submit call | Other elapsed | Draw share of scene ready → Library |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 754.553 | 0.015 | 20.377 | 0.090 | 9.225 | 96.2% |
| 2 | 597.548 | 0.013 | 31.813 | 0.136 | 8.094 | 93.7% |
| 3 | 637.197 | 0.016 | 51.001 | 1.459 | 8.369 | 91.3% |

These costs are from the same qualifying Library frame. The median software draw was **637.197 ms**; the median of the three within-visit draw shares was **93.7%**. This is elapsed time around the backend's drawing operation, not exclusively CPU cycles, or a claim that 637 ms can be removed. The display submission time ends when the call returns; it is not optical latency. No cause is assigned to the uninstrumented remainder.

**Why some launches took longer:** formal visit 1 took **163.051 ms** longer than visit 2 between the initial full frame and the Library. Those same visits differ by **+0.376, −0.035, +16.054 and +146.656 ms** across the four stages respectively. The last stage's software draw itself took **157.005 ms longer**, partially offset by an **11.436 ms shorter** pixel write. The display/input fence, by comparison, took 364.848–365.595 ms across all three. Font registration finished **229–235 ms before** the fence each time. Metadata was ready before the fence on visits 1 and 2, and **39.669 ms after** it on visit 3. That late producer explains much of visit 3's 40.220 ms fence-to-attach interval, not the slowest launch. Do not shorten the safety fence or defer more font work based on this cohort.

The [sanitized receipts and analysis](result.json) replay independently on `test-env` with `python3 -B audit.py public result.json`. The private audit also checked normal exit, installation/native restoration, independent absence, closed connections and the original owner/token release for every visit. The final release was **06:22:08 Asia/Shanghai on November 22, 2026**; R3 remains installed. The consumed 94,768,280-byte upload was removed; small evidence and the shared `aidoku-library-transition-return-20261122-062208.json` return receipt were retained. No other project's entry was changed.

## Diagnostic question

In the [previous Scribe card study](../startup-card-stages-20261122/README.md), the median interval from the initial full submission to the first complete Library submission was **1206.774 ms**. That was elapsed time, not a render duration. The previous records do not establish when the UI received prepared resources, how long it took to attach the view, or how much of the time was spent writing the framebuffer. Do not add new measurements to the previous cohort.

## Protocol fixed before testing

Use the same diagnostic build for one excluded warmup and **three formal visits**, in that order. Use the ordinary stock Library card and the existing Library, leave caches alone, and acquire and release a new exclusive lease for every visit. Keep the installed launcher, peer apps, rendering policy and safety checks unchanged. Restore the original Aidoku executable and native state after each visit. No manual input, video, completed covers or interaction test is part of the endpoint. A single diagnostic build provides an attribution, not an optimization A/B.

Each eligible visit must bind the actual card release, GUI process, ordinary exit and Library log in the same boot. Reuse the **same** first Library submission timestamp, sequence and marker; a later frame with more covers is not a substitute. Record the following four adjacent intervals on the device:

| Interval | Interpretation |
| --- | --- |
| Initial full submission → initial display/input fence reported ready | Wall time. Display work and preparatory work may overlap; do not label the whole interval idle or removable. |
| Fence ready → start of attaching the prepared controller | Distinguish metadata and font bytes produced by workers from receipt and font registration on the UI thread. These events can fall before the fence. |
| Start of attachment → Library scene ready for redraw | Includes controller attachment, the actual first view projection, and intervening event-loop turns. These subintervals are not additional time. |
| Scene ready → successful Library submission | Report the same accepted frame's software render, previous-buffer settle, pixel write and display submit durations separately. Any remainder is **unattributed**, not proof of idle time. |

Report the median of each stage and the per-visit breakdown. Select the slowest and fastest **by initial-full-to-Library time** and calculate the changes using those same two visits. Do not add independent medians or bill concurrent font/metadata work again. Missing detail cannot be silently interpreted as zero or replaced with another visit. A new production change needs a separate comparison; do not change policy during this diagnostic or infer that the longest gate is safe to remove.

## Changes tested off-device

The [first-draw-only CPU profile](first-draw-profile/README.md) made text measurement and shaping a better test target than guessing at invisible widgets. In an [independent call-count experiment](first-draw-profile/SHAPE-IDENTITY.md), 18 of 40 eligible Scribe paint calls and 17 of 39 PW6 paint calls reshaped text already measured for the **same live item and shaping inputs**. None had the same width constraint in measure and paint: cached final lines would be incorrect. A [frame-local experiment](first-draw-profile/PRELINE-TRIAL.md) instead reused the shaped paragraphs **before** line breaks, then applied the real paint width, alignment and overflow. The x86 development RGB renderer drew the complete Library **2.177 ms / 1.331 ms earlier** at the paired median (Scribe / PW6, **6/6 pairs each**); English and CJK controls preserved complete pixels. The subsequent [release trial](first-draw-profile/release-gray/README.md) used an unmodified Slint control, the native retained Gray8 scene, 2× scale and full panel resolutions. Both arms produced identical full-frame gray bytes and passed the reversible input check. At **0.198 ms (6/6) / 0.060 ms (4/6)**, the draw failed the predeclared screen on both layouts. These are separate host software-frame studies, not an ARM comparison or milliseconds saved on a Kindle. Do not adopt this private handoff and its deep clones.

- **Keep the post-attachment timer.** A [single-variable test of the real startup handler and software Library](attach-wake/README.md) replaced one timer with a direct, still-asynchronous wake. Attachment to view entry improved by about 1.08 ms in all eight pairs on each layout. From initial software draw to complete Library draw, Scribe gained 1.387 ms at the paired median; Paperwhite 6 gained 0.269 ms and the result reversed with run order. Both arms produced the same Library pixels. This does not explain the whole historical 22–25 ms gap or justify a native rollout.
- **Keep the system clock command.** The [toolbar clock experiment](toolbar-clock/README.md) found the in-process alternative faster on the worker, but it displayed a different hour from the system `date` when `TZDIR` changed the zone lookup or the zone file changed while the process was alive. Do not exchange correct local time for an unverified Kindle startup saving.
- **Do not change the Library category row for startup.** [Removing the always-empty count text](category-count/README.md) saved only 0.422 ms on Scribe and 0.206 ms on PW6 at the paired median of the first complete software draw. It won 4/6 and 3/6 pairs respectively and missed the predeclared screen on both. The complete buffers matched; removing an empty text node is not a demonstrated fix for a slow first render.

None of these changes is in the physical diagnostic binary or the daily R3 installation.
