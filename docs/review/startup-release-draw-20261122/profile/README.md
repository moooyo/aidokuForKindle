# Release Gray8: what executes inside the first complete Library draw?

**The unmodified release renderer spends measurable CPU in glyph generation, text layout/shaping and pixel composition. The evidence does not establish one dominant stage.** Unresolved samples are the largest group on both profiles. Of the named groups, text layout/shaping is highest on the Scribe fixture and glyph scaling/outline/raster work is highest on KPW6. These percentages are **fixed-period samples of x86 user CPU cycles**—not percentages of the physical Kindle's 637.197 ms software-render median, saved time, or an optical measurement.

| Mutually exclusive sampled category | Scribe (389 samples) | KPW6 (344 samples) |
| --- | ---: | ---: |
| Glyph mask scaling, outline generation or rasterization (`swash`, `skrifa`, `zeno`) | 43 (11.05%) | 67 (19.48%) |
| Glyph draw/cache wrapper without a raster-specific frame | 0 | 0 |
| Text measurement, font matching, analysis and shaping | 67 (17.22%) | 48 (13.95%) |
| Retained pixel fills, rectangle/texture scanlines and blending | 54 (13.88%) | 50 (14.53%) |
| SVG parse/paint | 5 (1.29%) | 1 (0.29%) |
| Other identified Slint/renderer context | 64 (16.45%) | 49 (14.24%) |
| No reliable stage assignment | **156 (40.10%)** | **129 (37.50%)** |

A name such as `VectorFont::render_glyph` alone is **not** evidence of a newly generated glyph: the function can reuse the glyph cache. Zero wrapper-only samples means no such sampled stacks, not that there were no lookups or misses. An external `memset`, allocator or other generic function is not labeled as pixel composition unless a concrete matching renderer ancestor is actually available. Consequently the pixel figure is not a count of redundant fills. A real duplicate-write observation plus a single-variable pixel/interaction-checked A/B is a stronger test of a proposed redundant fill than this profile alone.

## Quality and limitations

- **48/48 visits per profile passed.** Every independently started process presented the synthetic six-book Library with seven placeholder slots, returned the complete neutral Gray8 buffer, and correctly opened and dismissed the sorting control on the same GUI. Each full buffer matched a separate unprofiled pristine reference for that profile, byte for byte. Scribe had 4,612,800/4,612,800 dirty pixels; KPW6 had 2,123,520/2,123,520. The software renderer used the actual vendored `RetainedScene`, the checked initial full repaint then `ReusedBuffer`, and **2×** scale. The hosts performed no Kindle writes, SEND, stock-card interaction or panel measurement.
- The [predeclared contract](PLAN.md) was revised **before any application visit**. A three-region C preflight at 110,000 cycles violated the worker's 7,000 samples/second limit (31 throttle records). The single replacement used **2,000,000 cycles**, one fresh process per visit, no replacement or adaptive period. Its independent C control yielded **59 identified samples only in the middle function and one unresolved interrupt sample**, no before/after hits and no throttle. No kernel setting was changed. All app records were in the strict same-thread render window and reconciled; no lost/overwritten/throttled record or dropped visit was accepted.
- Scribe recorded 8–9 samples per draw, KPW6 7–9. The four relative time quartiles contained **24.2/22.6/20.1/33.2%** and **15.4/27.3/28.5/28.8%** respectively; both meet the aggregate gate. Median elapsed first-draw times with the sampling apparatus present were **4.451 ms** and **3.669 ms**—context only. Typical first samples arrived ~0.49 ms after the draw started; the largest observed gap *between* samples within a single frame was 2.238 ms on Scribe, 0.706 ms on KPW6. Fixed periods can align with drawing order and PMU interrupt skid exists. An unsampled interval cannot be allocated just because it lies on the timeline.
- `CLOCK_MONOTONIC` timestamps are captured immediately around the canonical `RetainedScene::render`, **inside** synchronous enabling/disabling signal brackets. Only samples strictly within that inner interval and belonging to the actual GUI process/TID are used. The counter is pinned, excludes kernel/hypervisor and includes no workers. If an overflow interrupted a kernel instruction, that sample was charged to `unresolved`, not user code. The ratio of eligible period weight to the hardware counter's full enabled count had medians **0.947 / 0.930**; the difference is not distributed to functions.
- A stock `opt-level=3`, LTO-off, unstripped binary with DWARF line tables—not the older dev RGB8 binary—was used for both layouts. `addr2line -i` recovered source at **313/389** and **286/344** leaf samples; **232 / 206** had inline ancestry. The release build does not force frame pointers. **294 / 282** sampled IPs had no additional physical caller. DWARF reconstructs an inlining chain at an IP, not a missing runtime call stack. The unresolved breakdown is **101/83** lacking concrete stage context, **50/38** external and **5/8** kernel skid (Scribe/KPW6). `swash`/outline is not silently replaced by all font functions. The [static source check](symbols.json) resolved known Slint text/renderer/glyph paths and inline frames; its separate preselected `shape_text` symbol was not confirmed, so there is no symbol-based claim for that method alone.
- [All 96 sanitized visit records](result.json) include the sample count, bins, first/last/internal time gaps, measured draw duration, cycle ratio, stage counts, and aggregate qualification. Dividing the cohorts into the first and last 24 visits, Scribe glyph/text/pixel shares varied **13.9→8.2 / 16.5→17.9 / 16.0→11.8%**; KPW6 **22.5→16.4 / 13.3→14.6 / 13.9→15.2%**. Pooling the two different panels, treating samples as independent wall-time slices, or multiplying these figures by a device time would overstate what is known.

## Reproduce on `test-env`

Use the worker's x86 Ubuntu userspace, shared six-job build/timing lock, and an unshared private project root. Put a clean application tree in `/work/source`, the original [attach-wake fixture](../../startup-library-transition-20261122/attach-wake/README.md) and its materializer in `/work/study`, and the original [release-gray materializer](../../startup-library-transition-20261122/first-draw-profile/release-gray/materialize.py) in `/work/gray`. Put this directory's files in `/work/profile`; create fresh `/work/programs` and `/work/reports`. The reference uses the normal **pristine registry Slint core**: the special path override and the candidate materialization of the earlier caching study are not used. Geometry/cycle observations are fixture-only. Do not modify the global registry, kernel settings or shared firmware device.

```sh
cd /work/source
python3 -B /work/study/materialize.py /work/source
python3 -B /work/gray/materialize.py stock
python3 -B /work/profile/materialize.py
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_STRIP=false CARGO_PROFILE_RELEASE_DEBUG=line-tables-only
cargo build -j6 --locked --offline --release --no-default-features --features kindle --bin aidoku
cp /work/target/release/aidoku /work/programs/stock
cc -std=c11 -fPIC -shared -O2 /work/profile/frame_cycles.c -o /work/programs/frame_cycles.so
cc -O3 -g -fPIE -pie /work/profile/gate_control.c -o /work/programs/control
taskset -c 2 env AIDOKU_FRAME_CYCLES=/work/reports/gated2.raw AIDOKU_FRAME_MAPS=/work/reports/gated2.maps \
  LD_PRELOAD=/work/programs/frame_cycles.so /work/programs/control > /work/reports/gated2.window
python3 -B /work/profile/check_control.py /work/programs/control /work/reports /work/control.json
python3 -B /work/profile/symbol_check.py /work/programs/stock /work/reports/symbols.json
cd /work/profile && python3 -B -m unittest test_profile
taskset -c 2 python3 -B measure.py /work/programs/stock /work/programs/frame_cycles.so /work/private
python3 -B report.py /work/private /work/programs/stock /work/control.json /work/public-profile.json
```

Only the last aggregate file and the static source check are intended for publication. Raw event IPs, mappings, temporary input fixtures and framebuffer images remain in the private workspace. No hashes were computed to qualify this study.
