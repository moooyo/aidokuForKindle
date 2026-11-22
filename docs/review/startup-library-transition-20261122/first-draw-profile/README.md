# Where does the first Library software draw spend CPU cycles?

**Measure text layout before changing Library widgets.** On the existing six-book Scribe software fixture, text measurement, font matching and shaping account for a median **29.4%** of sampled main-thread cycle weight. Glyph rasterization adds a separate **9.7%**. SVG rasterization accounts for **1.5%**. Pixel composition is also substantial (**32.5%**), but this fixture uses unoptimized RGB8, not the Kindle's release Gray8 path. These are not wall-clock percentages of a physical Kindle frame or predicted savings.

| Sampled path (disjoint weights) | Scribe: median [min, max] % | KPW6: median [min, max] % |
| --- | ---: | ---: |
| Text layout, matching, shaping | 29.4 [28.7, 30.6] | 36.6 [33.3, 38.6] |
| Glyph rasterization | 9.7 [9.2, 12.2] | 10.1 [9.2, 12.5] |
| SVG rasterization | 1.5 [1.0, 2.6] | 2.0 [0.6, 2.5] |
| Pixel composition (fills, rectangles and textures) | 32.5 [31.8, 36.2] | 23.0 [21.7, 27.2] |
| Component instantiation | 2.5 [1.5, 2.6] | 2.6 [2.4, 3.6] |
| Other renderer stacks | 15.6 [13.6, 16.7] | 15.9 [14.7, 17.9] |
| Stack did not resolve to the renderer | 7.7 [6.5, 8.9] | 8.7 [7.3, 10.7] |

Four fresh processes per layout alternate profile order; there is no second treatment arm or excluded visit in this cohort. Medians of separate categories need not sum to 100%. [All eight cycle records](result.json) are from the unmodified-widget control of the [empty category counter study](../category-count/README.md). The software fixture registers real fonts, runs the real startup/Library projection, renders six entries and seven placeholder slots, and verifies an ordinary Slint pointer action after the draw. It does not model stock-card latency, a physical input fence, framebuffer writes, scanout, e-ink, or a user's books.

## How the measurement is bounded

- `gprofng` starts paused. The fixture issues two `SIGURG` process signals around **only** `SoftwareRenderer::render` on its first non-booting Library. The instrumentation is disabled in the original A/B. Startup, pointer validation and RGB file export are outside this window. The collector records hardware cycle samples rather than treating its low-resolution clock-profiler ticks as elapsed time.
- On this `test-env` worker, `kill(getpid(), SIGURG)` is required: a thread-directed `raise()` did not resume the GNU collector. A separate three-region C control yielded **0** weight in equal-work before/after functions and nonzero weight in the gated middle function. The profiler checks the exact pause/resume sequence for every GUI visit.
- The parser finds the Linux process leader in the recorded raw hardware samples. It matches that thread's **unique** total to the displayed call tree; GNU's logical `THRID==1` can refer to a worker. The first-to-last GUI sample must cover at least 90% of the signal-delimited window. Two 250,000-cycle pilot traces covered just **41.7% / 55.4%** and were rejected rather than misreporting the early initialization hot spot. At 2,000,000 cycles per sample, the final records span **98.1–99.3%**, with **192–199** samples per Scribe visit and **163–184** per KPW6 visit.
- Weights are call-stack estimates, not measured per-function timers. The parser subtracts child weights before assigning each exclusive remainder to its deepest recognized stage; unresolved renderer and non-renderer stacks remain visible. A generic allocator or cloning frame inherits its nearest recognized stage. A separate release ELF is stripped; it cannot substitute for this same-tree, symbol-bearing dev executable. In particular, unoptimized RGB cloning/filling is not evidence for a Kindle write optimization.

## Reproduce

Use the existing worker Ubuntu userspace, GNU binutils/gprofng 2.42, a permitted hardware `cycles` event, and the [category-count control](../category-count/materialize.py) with its gated first-draw fixture. Only the tested GNU 12.4 experiment format is accepted. Run with the project build/timing lock, one CPU and a fresh output filename; `profile.py` creates and removes its own temporary experiments. No rebuild is needed when the existing control is available.

```sh
taskset -c 2 python3 -B profile.py /work/programs/control /work/first-draw-cycles.json --runs 4
```

**Next experiment:** distinguish the nonempty text measured for layout from text shaped again for paint. In the locked Slint 1.17.1 `i-slint-core/textlayout/sharedparley.rs`, `text_size` constructs paragraphs independently, whereas `draw_text` uses `get_or_create_text_paragraphs` with the item's cache. Both measurement and glyph-paint paths appeared in the sampled stacks. No current record proves that the **same** item, text, font, width, wrap and scale were shaped twice. A fixture-only count of those identities and cache misses would test that claim. This is different from the earlier [grid batching and equal-label optimization](../../startup-all-20261119/first-layout/README.md), and does not imply any change to title elision, hit areas or fonts.

**Follow-up:** the [bounded identity-count study](SHAPE-IDENTITY.md) found actual repeated pre-line-break builds for 18 Scribe and 17 KPW6 item/text/style matches. None had the same width constraint in measurement and paint. This is not evidence that the complete laid-out result can be reused.
