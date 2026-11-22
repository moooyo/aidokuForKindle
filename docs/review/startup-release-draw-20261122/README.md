# What does the first Library draw actually do?

The [previous ordinary-card study](../startup-library-transition-20261122/README.md) measured a median **637.197 ms of elapsed time** around the draw, on three Scribe launches. That duration is neither CPU time nor the sum of sampled host hot spots. The [previous release Gray8 A/B](../startup-library-transition-20261122/first-draw-profile/release-gray/README.md) already ruled out forking Slint to reuse shaped paragraphs before line breaking. Do not repeat that candidate or combine either earlier cohort with this study.

## Decision and Scribe result

**Keep R3, the current Library background and Slint's existing glyph cache.** The optional diagnostic now measures the GUI thread's CPU time in the same frame as the original elapsed timer. The draw took **418–778 ms elapsed** across three formal stock-card launches, but its thread used just **95.772–97.219 ms of CPU**. Before optimizing more raster operations, establish how much elapsed delay is present on repeated launches of the *same* executable file. Do not infer that the difference is all I/O, change process priority, or prefetch the entire application.

| Formal visit | Stock card release → Library (ms) | Draw elapsed (ms) | GUI CPU (ms) | CPU / elapsed | Elapsed − CPU (ms) | Minor / major faults | Voluntary / involuntary switches |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 3190.427–3191.637 | 777.871 | 95.772 | 12.3% | 682.099 | 336 / 140 | 142 / 100 |
| 2 | 2959.577–2960.765 | 623.075 | 97.219 | 15.6% | 525.856 | 310 / 140 | 138 / 78 |
| 3 | 2427.984–2429.231 | 418.275 | 96.852 | 23.2% | 321.423 | 317 / 140 | 141 / 100 |

One warmup was excluded. The card-release-to-Library interval midpoint had a median of **2.960 s**; the three per-visit CPU fractions had a median of **15.6%**. All three qualifying frames were populated Library views drawn into a **1860 × 2480 Gray8** retained surface. The first formal draw exceeded the third by **359.596 ms** elapsed, while its GUI CPU was **1.080 ms lower**; the signed elapsed-minus-CPU difference increased by **360.676 ms**. The count of **140 major faults** did not vary. Counts alone do not identify the file, bytes read, fault handling time or scheduling delay. A new private executable file was unpacked and bound for each test, so the trend is **not** a measurement of cold versus warm production starts. See the [scope and next comparison](device/INTERPRETATION.md).

The [raw relative receipts and replayable summary](device/result.json) cover all four visits. Each one exited normally, restored the installed R3 and native state, passed independent absence checks, closed its connections and released its own lease. The final return was **07:36:47 Asia/Shanghai on November 22, 2026**. The consumed **94,759,220-byte** upload was removed and the shared return receipt is `aidoku-release-draw-return-20261122-073647.json`. Nothing was installed permanently. `python3 -B device/audit.py public device/result.json` rechecks the published summary on `test-env` from this directory.

## Why not change the UI?

- **Background fill:** removing only `LibraryPage.background` eliminated **4,181,280 bytes** of full-width paper writes on Scribe and **1,870,720 bytes** on PW6 in the first release software draw. But the candidate drew **0.168 ms / 0.149 ms later** at the paired medians and won only **3/6 pairs on each**. The retained pixels matched in English and Chinese across ten states, including night mode, a pressed button, menus and returns from Settings. This was a real redundant write, not a worthwhile startup change. [One-line difference, counters and A/B](composition/README.md).
- **Glyph cache:** in the four English/CJK test shelves, every distinct eligible key rasterized once; repeated requests all hit Slint's existing cache. There were no evictions, failed insertions or repeated misses. Adding another cache or enlarging it would not remove those first misses. This does not claim every production Library has the same working set. [Glyph counter results](glyph/README.md).

## Release software draw

All 48 first Library draws per layout qualified under the [frozen sampling protocol](profile/PLAN.md). The collector observed only the main thread inside the stock release Gray8 draw, at the actual 2× panel resolutions. Each sample belongs to one group; the [per-visit data](profile/result.json) retain unresolved samples in the denominator.

| Sampled user-cycle group | Scribe (389 samples) | PW6 (344 samples) |
| --- | ---: | ---: |
| Text measurement, font matching and shaping | 17.22% | 13.95% |
| Glyph mask / outline rasterization | 11.05% | 19.48% |
| Pixel composition | 13.88% | 14.53% |
| SVG | 1.29% | 0.29% |
| Other resolved work | 16.45% | 14.24% |
| Unresolved | 40.10% | 37.50% |

**No single CPU bottleneck is established on both layouts.** In particular, an unresolved stack is not implicitly pixel filling or font work. A zero count on the separate glyph-cache lookup wrapper is not evidence that no lookups occurred. These are sampled cycles on the x86 host: neither a timing breakdown of a native launch nor an estimate of the savings from removing an operation. The background experiment tests one independently observable redundant paint instead of treating a whole profile category as recoverable time.

## Questions and decision rules

1. On `test-env`, profile only the **first complete six-book Library draw** of the unmodified release renderer. Use the actual retained Gray8 target at Scribe and PW6 panel resolutions and 2× UI scale. Separate glyph rasterization from text measurement/shaping and pixel composition; keep unclassified samples visible. A sampled CPU-cycle share is not a wall-time saving. Do not multiply it by the Scribe's 637 ms.
2. Investigate one app-level change: avoid painting the opaque Library background over an identical window background. Qualify that the extra paint really occurs and that the parent covers the vacated area before timing it. Keep the startup scene, Library layout, text, assets, full-refresh rules and event handling unchanged. With profiling **off**, use one excluded warmup pair and six fresh alternating AB/BA pairs per layout. Before promotion require equal complete Gray8 pixels, intact input and retained repaints, at least **1 ms** median paired reduction in the complete software draw and **5/6** earlier draws on **each** layout, with no more than 1 ms median regression from program entry to that frame. Do not replace attempted pairs or test additional variants until one appears fast. A host pass alone is not a card-speed claim.
3. On a new independent physical cohort, ask how much **GUI-thread CPU time** the same accepted Library draw used. One diagnostic program; one excluded warmup, then three formal ordinary stock-card launches; a new exclusive lease and complete return per visit. Preserve the normal release-to-submission endpoint, original entry, user Library and daily installation. Do not clear caches, change priority, choose a CPU frequency or touch peer apps. The `render` elapsed interval, thread CPU, faults and context-switch counts must come from one draw and join to the same submitted frame, not a later cover or network event. A missing counter stays missing. Report the per-visit CPU fraction, signed wall minus CPU and absolute counts; this residual is **not** specifically run-queue wait, I/O or power throttling. Do not interpret faults or switches as durations. No recording, manual input or optical measurement is involved.

These are separate experiments: the physical program has no candidate UI change or statistical profiler. Only a new controlled ordinary-card A/B can establish that a candidate improves device startup.

## What the device counters mean

The diagnostic reads [`CLOCK_THREAD_CPUTIME_ID`](https://man7.org/linux/man-pages/man3/clock_gettime.3.html) on the rendering thread, not the whole process. Its before/after readings bracket the existing elapsed timer; the outermost [`getrusage(RUSAGE_THREAD)`](https://man7.org/linux/man-pages/man2/getrusage.2.html) readings supply per-thread fault and switch deltas. A slightly larger CPU interval is possible because these clocks are not read simultaneously. An unavailable counter group is not four zeros. The [earlier 100 ms scheduler sampling](../startup-cost-attribution-20261117/README.md#catalog-and-scheduler-evidence) measured contention across different launch routes and a longer interval; it cannot explain a particular draw in this cohort.
