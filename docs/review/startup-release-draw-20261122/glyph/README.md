# First Library glyph masks: distinguish reuse from first work

**Do not add a second glyph cache, cache failed rasterizations, or enlarge the current cache.** In four real first-Library software draws every distinct key rasterized successfully exactly once; every repeated request hit Slint's existing cache. This study is separate from the [rejected pre-line paragraph/shaping cache](../../startup-library-transition-20261122/first-draw-profile/release-gray/README.md); it does not alter line breaking, shape text differently, change a font or use user books. No hardware glyph times or per-glyph savings are available from the 637 ms physical draw.

## Count result on `test-env`

| Fresh process / synthetic shelf | Mask lookups | Distinct keys = successful rasterizations | Cache hits | Successful empty masks | Peak cache alpha bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Scribe, English | 397 | 131 | 266 | 6 | 59,681 |
| Scribe, long CJK titles | 313 | 116 | 197 | 5 | 119,252 |
| KPW6, English | 361 | 129 | 232 | 6 | 44,931 |
| KPW6, long CJK titles | 310 | 113 | 197 | 5 | 87,253 |

All four cases had **zero** keys first hit from before the measured draw, failed mask generations (`None`), repeated misses, refused insertions and evictions. Empty alpha maps were successful entries; do not call them misses that need a negative cache. These are counts at the nonzero vector-glyph lookup boundary, not characters, shaped paragraphs, textures painted or timings. English and CJK are different fixtures; do not pool them or generalize the lack of eviction to all books. The [complete aggregate](result.json) contains one diagnostic draw per case. Each independent pristine reference matched the diagnostic's full Gray8 pixels and damage, and both passed ordinary open/dismiss pointer input. No speeds were measured on this instrumented path.

## Actual path

- The [`Cargo.lock`](../../../../Cargo.lock) resolves Slint 1.17.1. Its [software `draw_glyph_run`](https://docs.rs/crate/i-slint-renderer-software/1.17.1/source/lib.rs#3333) obtains the shaped font, face index, pixel size, variation coordinates and glyph ID. The zero-ID `None` short-circuits before the raster cache. Font selection/shaping/paragraph construction and blending an existing mask onto the Gray8 scene are **not** glyph generation. The [Swash face information cache](https://docs.rs/crate/i-slint-renderer-software/1.17.1/source/fonts/systemfonts.rs#19) is a separate structure.
- [`VectorFont::render_vector_glyph`](https://docs.rs/crate/i-slint-renderer-software/1.17.1/source/fonts/vectorfont.rs#152) indexes a thread-local LRU by **font blob ID, collection face index, physical pixel size, glyph ID and normalized variation-coordinate hash**. On a hit it returns a clone of `RenderableVectorGlyph`: the alpha bytes are `Rc<[u8]>`, not copied and not rasterized again. Color and placement are applied afterwards; different weights/fallback faces, physical sizes or variations may legitimately miss for the same character. A code point is not a shaped glyph ID.
- A miss uses the Slint context's Swash 0.2.10 scaler, with the same pixel size/coordinates, `Source::Outline`, `Format::Alpha`; it is **not a per-glyph FreeType subprocess or Fontdue call**. The locked [`swash` implementation](https://docs.rs/crate/swash/0.2.10/source/src/scale/mod.rs#846) produces the image, or `None`. Successful results, including a successful empty alpha map, follow the positive insertion path. `None` returns before insertion; that does not prove that spaces repeatedly miss. A 1,048,576-unit [weighted CLru limit](https://docs.rs/crate/clru/0.6.3/source/src/lib.rs#253) accounts for entries plus alpha-map length; it is not an RSS limit. An evicted mask can be generated again.
- The boot scene uses [pre-rasterized labels](../../../../assets/startup/README.md). Installing the real font families and shaping a line do not by themselves populate this renderer's mask LRU. Moving vector rasterization onto another thread or process does not warm the UI thread's cache. Reopening an arbitrary atlas under a guessed family/size, assuming Latin space has no mask or merging CJK fallback keys would risk wrong pixels. None is proposed.
- Do not label the whole `vectorfont` namespace a mask miss: `draw_glyph_run` constructs a `VectorFont` and its unscaled Skrifa face metrics **before** asking the LRU about any glyph. A lookup hit still incurs run setup and texture painting. Require stacks reaching the outline/scale/raster work or an observed cache miss to attribute actual mask generation.

## Isolated diagnostic, not a speed test

The **unmodified optimized Gray8** [profile](../profile/result.json) captured 389 eligible Scribe samples and 344 KPW6 samples across 48 fresh processes per layout. The actual mask/outline category was **11.05% / 19.48%**, respectively; **40.10% / 37.50%** remained unresolved. No sample was assigned solely to the generic glyph-lookup category; this does not mean that no lookup ran. The earlier [dev RGB8 profile](../../startup-library-transition-20261122/first-draw-profile/README.md) used different pixels and optimization. Neither profile identifies cache misses or justifies multiplying a host percentage by the physical first draw.

[materialize.py](materialize.py) bracketed exactly the first complete Library render of the existing six-book Gray8 fixture in a **separate diagnostic executable**. It copied, rather than edited, the registry renderer. The aggregate contains no text, IDs, keys, font names, pixels or absolute times. [counts.rs](counts.rs) caps each synthetic diagnostic draw at 8,192 calls. Never use the instrumented renderer for speed comparisons or mix its visits with the pristine profile.

The [collector](collect.py) uses fresh English and long-CJK six-book fixtures for each of the two physical-sized viewports. It verifies stock/observed Gray8 bytes, damage, endpoint and pointer completion before writing only counters; it intentionally does not export durations. All stock profiling and the separate composition A/B finished before this diagnostic acquired the shared build lock.

`AIDOKU_GLYPH_DIAG1 [calls, distinct, hits, misses, preexisting_hit_keys, rendered, empty_alpha, raster_none, misses_after_prior_none, misses_after_prior_cached, insertion_refused, evicted_entries] peak_alpha_bytes` is one aggregate per process. `calls == hits + misses` and `misses == rendered + raster_none`. `preexisting_hit_keys` counts first hits on previously unobserved-in-frame masks; `misses_after_prior_cached` is not a global eviction count. Empty-success masks are a subset of `rendered`. A `None` repeat and a miss following an earlier cached value are not interchangeable. Zero counters are observations only after a completed nonempty frame, never a substitute for an unexecuted diagnostic.

On `test-env`, both locked source anchors and materialization into a disposable copy of the actual Gray8 fixture passed. A standalone compiled Rust test covered hits carried into the frame, distinct size keys, repeat failures, successful empty masks, post-cache misses and eviction accounting. The compiled release ELF used the original release settings and target: only the software renderer and three downstream packages rebuilt (3m 13s). To reproduce these checks and the census in a private Ubuntu workspace, first place the same stock Gray8 source in `/work/glyph-source`, the pristine executable at `STOCK`, and create the empty `/work/glyph-count-only` safety marker. Do not use another operator's source tree. The one private `--offline` lockfile update binds only the same-version renderer path.

```sh
renderer=/opt/kindle-toolchain/tools/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/i-slint-renderer-software-1.17.1
python3 -B /work/glyph/materialize.py check "$renderer"
rustc --edition=2024 --test /work/glyph/counts.rs -o /work/counter-unit
/work/counter-unit
python3 -B /work/glyph/materialize.py prepare "$renderer" --source glyph-source
cd /work/glyph-source
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_STRIP=false CARGO_PROFILE_RELEASE_DEBUG=line-tables-only
cargo build --offline --release --no-default-features --features kindle --bin aidoku -j6
taskset -c 2 python3 -B /work/glyph/collect.py STOCK /work/target/release/aidoku /work/new-counts.json
```

## One conditional candidate

No additional A/B is justified by repeated rasterizations: there were none. A *different*, prospective project-only optimization would shift genuinely cold work for a few immutable Library labels **offscreen on the same UI thread**. This aggregate deliberately does not distinguish user titles from UI labels, so it does not qualify that experiment by itself. Only consider the normal managed Library's otherwise-unused checked initial-panel interval after all required faces are registered; use a separate offscreen software window with its own pixels/dirty state, at the actual scale, exact localized strings/face/weight/size, and no titles or live framebuffer access. Stock must remain the fallback if those preconditions or lifecycle admission are missing. Do not call the visible component's `render()` early, consume its dirty flags, prime before first full SEND, extend the input fence, or reserve a worker's distinct cache. This shifts cold work and can slow startup; it is **not** a recommendation to implement until a safe, bounded slot and same-thread cache hits are demonstrated. If the host cannot bound even the smallest atomic draw or costs exceed its budget, reject without device use.

Use pristine vs candidate optimized binaries, identical software Gray8 profiles and canonical pixels, English and long CJK controls, one excluded warmup per arm, six fresh AB/BA pairs per size. Measure the priming interval, full initial draw, first Library draw **and** entry-to-Library; reject changed pixels/state, missed frame admissions, delayed stop/sleep, or a fuller cache without earlier end-to-end draws. Require ≥1 ms paired Library-draw gain, ≥5/6 earlier and no >1 ms median entry-to-Library regression on either layout before even considering a fresh device comparison. If repeated misses follow cache pressure rather than cold fixed labels, or the major cost is painting already cached masks, do not apply this intervention.
