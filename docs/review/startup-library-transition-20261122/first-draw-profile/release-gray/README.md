# Release Gray8 decision: do not promote pre-line-break reuse

**Keep stock Slint. Do not open a physical A/B for this change.** Reuse eliminated 18 paint paragraph builds on the Scribe fixture and 17 on KPW6, but the first complete Library draw was only **0.198 ms** and **0.060 ms** faster respectively at the paired median. Neither reached the predeclared **1 ms** minimum; KPW6 also missed the five-win requirement. All correctness controls passed. The earlier [dev RGB8 result](../PRELINE-TRIAL.md) is a separate experiment and does not override this decision.

| Host first complete Library | Scribe, 1860×2480 | KPW6, 1264×1680 |
| --- | ---: | ---: |
| Stock / reuse median draw | 3.853 / 3.686 ms | 3.057 / 2.999 ms |
| Median within-pair draw saving | **0.198 ms**, 6/6 wins | **0.060 ms**, 4/6 wins |
| Draw saving by execution order: AB / BA | +0.497 / +0.036 ms | −0.123 / +0.127 ms |
| Rust entry → first complete software Library: paired saving | +0.798 ms, 4/6 | +0.405 ms, 3/6 |
| Entry saving by order: AB / BA | −0.854 / +1.491 ms | −2.061 / +1.590 ms |
| Actual paint builds, hook-off verification / reuse | 45 / 27 | 43 / 26 |
| Peak retained entries / total deeply cloned paragraphs | 18 / 18 | 17 / 17 |

Each layout had one excluded warmup pair, then six alternating **AB, BA, AB, BA, AB, BA** pairs, each in fresh processes. To select a physical trial **both** profiles had to save ≥1 ms at the median, with ≥5/6 positive draw pairs and no more than 1 ms median regression to the first software Library. The six Scribe draw savings in ms were **+0.497, +0.013, +2.459, +0.200, +0.197, +0.036**; KPW6 **−0.123, +0.127, −0.217, +0.094, +0.026, +0.326**. Paired medians are not differences of separate medians. See [all measurements and controls](result.json).

## What the test measures

- Both host executables used Rust release optimization, `CARGO_PROFILE_RELEASE_LTO=false`, one codegen unit, identical features, one CPU and the same private build path. **A uses pristine upstream `i-slint-core`**: no experimental lookup, clone, cache scope or counters run in stock's render. The basic fixture's timing, state/pixel checks and input are common. B alone includes the [same pre-line-break experiment](../preline-cache.rs) and its count hooks. The measured window includes allocating/entering B's frame scope and draining it before the timer stops.
- The headless Slint adapter invokes the vendored [`RetainedScene::render`](../../../../../vendor/slint-backend-kindle/src/gray_target.rs) itself. On initial paint it requests `NewBuffer`, then restores `ReusedBuffer`, matching the backend's initial draw policy. Both the software scale factor **2.0** and exact physical/logical dimensions were asserted. `ScenePixels::Gray` was required; fallback to RGB would have failed. The observed physical damage, summed using disjoint regions, was **4,612,800 / 2,123,520 pixels** on both the initial full scene and the first Library in every visit, not merely a full-size bounding rectangle. Only software rendering is timed: no framebuffer write, SEND, native input fence, optical settling or stock-card release is measured.
- The fixture verifies the six books and seven visible slots before covers arrive. Every pair compares the **entire canonical Gray8 buffer byte for byte**, not a hash or a sample. An ordinary Slint pointer press/release opens the sort popup, dismissal returns to Library, and the next draw confirms the optional cache scope is absent. Separate untimed CJK pairs produced matching pixels and genuine CJK hits (11 Scribe / 10 KPW6); 72 / 68 changed-key lookups rejected mismatched weight, context, text or wrap. The widths differed between measurement and paint on **all** 18 / 17 hits; they still used the actual size to relayout. No input or pixel check was traded away for speed.
- To isolate the reference choice from the earlier dev study, two extra pairs per layout compared the pristine stock binary with the **candidate binary in hook-off mode**. The latter made **zero deep clones**; its paint counts establish the equivalent unoptimized-work count without instrumenting the formal stock arm. Median stock-minus-off draw differences were **−0.119 ms (0/2)** and **−0.092 ms (1/2)**, with both execution orders recorded. This small check cannot quantify a stable hook overhead and must not be subtracted from the formal result.
- The counts are actual concurrent entry counts and total deep copies created, not byte estimates. Parley's owned pre-break layout contains variable-sized working data. This run did not instrument allocation sizes, peak heap bytes or use RSS as a proxy. Entries are scoped to one synchronous UI-thread frame after the controlled font-registration epoch; a font context pointer does **not** establish production font-collection versioning. Introducing the experiment as shipped code would require a private Slint patch and lifecycle/invalidation maintenance for a gain that did not qualify here.

Original single-arm Kindle timings and this two-arm x86 host result remain separate. There is no claim that Gray8 draws in these host milliseconds on a Scribe, or that this change speeds ordinary device startup. No real Kindle, user books, lease, installation or upstream/shared registry was used or modified for this study.

## Reproduce on test-env

Use a fresh private `ubuntu-run` workspace and its shared build lock with Cargo `-j6`. Put the project in `/work/source`, the [original six-book fixture and materializer](../../attach-wake/README.md) and `../preline-{materialize.py,cache.rs}` in `/work/study`, this directory's [materializer](materialize.py) and [runner](trial.py) in `/work/gray`, and an ordinary private copy of the locked `i-slint-core-1.17.1` in `/work/local-slint`. Create `/work/programs` before building. In the *private* manifest only, add `[patch.crates-io] i-slint-core = { path = "../local-slint" }` and the explicit dependency `i-slint-core = { version = "=1.17.1", default-features = false }` as in the [previous trial](../PRELINE-TRIAL.md). Both arms use the same resolved private path. Do not patch the installed compiler, global registry or upstream project.

```sh
cd /work/source
python3 -B /work/study/materialize.py /work/source
export CARGO_PROFILE_RELEASE_LTO=false
python3 -B /work/gray/materialize.py stock
cargo build -j6 --release --offline --no-default-features --features kindle --bin aidoku
cp /work/target/release/aidoku /work/programs/stock
python3 -B /work/gray/materialize.py candidate
cargo build -j6 --release --offline --locked --no-default-features --features kindle --bin aidoku
cp /work/target/release/aidoku /work/programs/candidate
taskset -c 2 python3 -B /work/gray/trial.py /work/programs /work/release-gray-result.json
```
