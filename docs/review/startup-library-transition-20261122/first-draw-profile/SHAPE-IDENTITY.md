# Do measurement and painting shape the same text?

**Yes for some pre-line-break inputs, no for the final layout.** On the first six-book software Library frame, **18 of 40** nonempty plain single-line Scribe paint calls and **17 of 39** KPW6 calls had an earlier real measurement build for the **same live item** with identical text, complete `FontRequest`, scale, wrap and stroke configuration. **None** of those pairs had the same width constraint. Every matching measurement had unbounded width; drawing used the item's finite width.

| Same-visit calls on the first business frame | Scribe | KPW6 |
| --- | ---: | ---: |
| `sharedparley::text_size` entries / actual paragraph builds | 59 / 59 | 57 / 57 |
| Measurements with actual builds for admitted nonempty plain text | 54 | 53 |
| Paint's `get_or_create_text_paragraphs` calls / actual paragraph builds | 45 / 45 | 43 / 43 |
| Paint calls with actual builds for admitted nonempty plain text | 40 | 39 |
| Paint calls preceded by a matching item, text and all shaping inputs | 18 | 17 |
| Also identical width constraint / unbounded measure vs finite paint | 0 / 18 | 0 / 17 |
| Same text seen in a prior measurement on a **different** item | 11 | 5 |

Each profile produced the identical counts twice in four fresh processes (Scribe, KPW6, KPW6, Scribe). The final row can overlap other rows and is **not** a positive same-item match. The 22 other admitted paint calls on each layout had no earlier identical item-and-text measurement in the window. No same-item/same-text measurements with different font request, scale, stroke, or wrap were observed in this particular fixture; that is not an invariant of Slint.

The [separate controlled handoff experiment](PRELINE-TRIAL.md) reuses a pre-line-break clone only, and always lays it out at the real paint size. Its results are independent of these observational counts.

## What a match proves

- The [probe](shape-probe.rs) runs only on the GUI thread inside the first non-booting `SoftwareRenderer::render` and retains text solely in process memory. It compares item-tree allocation + item index, actual string equality, full resolved font request (family, requested size, weight, italic, letter spacing), scale, wrap mode and presence/style of stroke. It admits only nonempty plain single-line strings, not rich text, links or selection. It counts a qualifying painted item once irrespective of how many times it was measured. No hashes, item identities, font names or book text are exported. Normal first-shelf geometry, six books, seven placeholder slots and the subsequent Slint pointer interaction remain checked.
- A build is counted directly at the paragraph builder's call to Parley's `RangedBuilder::build`, not inferred from a stack or cache lookup. In the locked Parley source that path invokes `build_into_layout` and `shape::shape_text`. On a paint-cache hit the shaping callback does not run. This is an observational build of a **private copy** of `i-slint-core` 1.17.1; the original item cache is active and unchanged, no new cache or lookup shortcut was added. [All four aggregate records](shape-result.json).
- Width and height are not inputs to `LayoutWithoutLineBreaksBuilder`. They **are** inputs to the subsequent line layout. Horizontal preferred-size queries force unbounded `NoWrap`; vertical queries may use a width constraint and a different wrap. Paint sets `Some(width)`, `Some(height)`, actual alignment and overflow; measurement sets no maximum height, left/top and clip. The final layouts therefore cannot be interchanged. No font-resolution generation/invalidation, mutable selection/rich text, per-glyph output equivalence, memory cost or device speed qualification is implied by these counts. No count is converted to milliseconds.

## Reproduce on test-env

Use one private `ubuntu-run` workspace: `/work/source` contains the product snapshot, `/work/study` the [six-book baseline materializer and fixture](../attach-wake/README.md), `/work/probe` the two `shape-*` source files, and `/work/local-slint` an ordinary **copy**, not a symlink, of the locked registry's `i-slint-core-1.17.1` sources. Do not edit the shared registry or another project's target. Run with six build jobs. The first build requires `--offline` without `--locked` because only the **disposable** lockfile needs its registry-to-path change; no registry download or dependency upgrade is needed.

```sh
cd /work/source
python3 -B /work/study/materialize.py /work/source
python3 -B /work/probe/shape-materialize.py prepare
cargo build -j6 --offline --no-default-features --features kindle --bin aidoku
taskset -c 2 python3 -B /work/probe/shape-materialize.py run
```
