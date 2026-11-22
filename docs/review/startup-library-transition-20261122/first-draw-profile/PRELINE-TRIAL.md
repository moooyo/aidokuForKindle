# Frame-local reuse of measured text before line breaking

**Final decision:** the subsequent [release Gray8 screen](release-gray/README.md), against pristine Slint at 2× physical resolution, missed the preset cutoff on both profiles. Do not promote this candidate or schedule a Kindle A/B for it. The dev result below retains its original scope.

**Follow-up decision:** the [production-optimized Gray8 host screen](release-gray/README.md) did not replicate a sufficient benefit. Do not promote this candidate or infer a native improvement from the development RGB result below.

**Keep this as a bounded experimental candidate, not a production change.** The candidate passes the predeclared desktop screen. On each synthetic six-book Library, the first complete software draw was faster in **6/6** fresh pairs. Median within-pair savings were **2.177 ms** on Scribe and **1.331 ms** on KPW6. This is a same-binary, x86-64 **dev RGB8** result. It is not a stock-card, ARM release, native Gray8, display submission or optical measurement.

| Control − candidate | Scribe 930×1240 | KPW6 632×840 |
| --- | ---: | ---: |
| First software Library draw | +2.177 ms (6/6) | +1.331 ms (6/6) |
| Same draw, paired medians by order AB / BA | +1.955 / +2.399 ms | +1.246 / +1.620 ms |
| Rust entry → first complete software Library draw | +1.048 ms (4/6) | +1.502 ms (5/6) |
| Actual `text_size` paragraph builds, both arms | 59 | 57 |
| Actual paint paragraph builds, control → candidate | 45 → 27 | 43 → 26 |
| Reused measured paragraphs / reused at a different logical width | 18 / 18 | 17 / 17 |

The six ordered draw savings in ms were **2.470, 7.457, 0.561, 0.571, 1.955, 2.399** on Scribe and **1.246, 1.620, 1.416, 0.545, 0.737, 1.941** on KPW6. All visits and arm orders are included in the [aggregate report](preline-result.json). Positive differences mean less time. The screen required both layouts to save at least 1 ms at the paired median, win at least five draw pairs each, and regress the median entry-to-Library interval by no more than 1 ms. This does not establish a large user-visible benefit.

## What changed inside the experiment

- A and B share a single program built against a **private** copy of `i-slint-core` 1.17.1. Only B retains the measured paragraph before Slint's `layout()` performs wrapping, alignment and elision; A uses the original shaping and line-layout path. A still enters the experimental hooks and counts calls: it is a cache-disabled control, **not an uninstrumented upstream binary**. The timer includes frame-local state setup and disposal of unused clones; RGB export, equality comparison, hit tests and process shutdown are outside it. One warmup pair per layout is excluded, followed by six fresh alternating AB/BA pairs. Profiling is off.
- The upstream private `TextParagraph` wrapper has no public handoff. Its fields are cloneable, including the owned `parley::Layout<Brush>` (Parley 0.10.0). The [experimental patch](preline-materialize.py) derives `Clone` for that wrapper. The [bounded store](preline-cache.rs) takes an independent **deep clone** before measurement mutates the layout. On a true miss in the existing paint cache, it consumes a matching copy once; the unchanged paint path line-breaks and aligns it for the actual `Some(width)`, `Some(height)`, alignment and overflow. No complete, unbounded measured layout or measured pixels are reused.
- Admission requires the same live item-tree allocation and index, plain single-line nonempty text, whole font request, renderer scale, wrap, no stroke, and the same `parley::FontContext`; strings over 512 UTF-8 bytes and buffers beyond 128 distinct entries fall back. The scope is created only after all fonts are registered and discarded before returning from that **one synchronous first draw** on the UI thread. The fixture has no mid-draw font registration, UI mutation or event-loop reentry, and verifies its known completed font generation before/after. The font context address **is not a general mutation generation**. The code neither caches across frames nor claims safe handling of arbitrary register/unregister, item replacement, animated changes, rich text, selection or fallback changes in production.
- `shape_paragraphs` normally reaches Parley's builder; the trial counts the fallback branch, and the existing same-input [count study](SHAPE-IDENTITY.md) independently verified the real `RangedBuilder::build → shape_text` chain and measured calls. Every paired 930×1240 or 632×840 RGB buffer matches byte for byte. Each process verifies six visible books, seven placeholders and a real Slint pointer press/release opening sorting; dismissal restores the Library.

## Independent correctness checks

Both arms also ran each layout with a Chinese locale and long synthetic CJK book titles. Full RGB buffers matched; 11 of the 18 Scribe hits and 10 of the 17 KPW6 hits contained a CJK character. Each match used distinct width constraints. Candidate-only dry lookups changed one key field at a time (font weight, wrap, string or font context) and rejected all **72 / 68** altered keys. These are negative key checks, **not** evidence that fonts or a user-visible setting actually changed mid-draw. Untimed CJK visits are not pooled with the English screen. No user content, pixels, font/item IDs, strings or hashes are retained.

## Reproduce and decide

Use the private `/work/source`, `/work/study` and `/work/local-slint` layout described in the [count study](SHAPE-IDENTITY.md). Start from the original crate/fixture, not the count-instrumented copy. Materialize the attach-wake fixture, then add the same `[patch.crates-io] i-slint-core = { path = "../local-slint" }` and explicit `i-slint-core = { version = "=1.17.1", default-features = false }` dependency to the **private** app manifest. No global Cargo registry, product sources or installed Kindle programs are changed. Use the Ubuntu runner's shared lock and six build jobs.

```sh
cd /work/source
python3 -B /work/study/materialize.py /work/source
python3 -B /work/probe/preline-materialize.py
cargo build --offline --no-default-features --features kindle --bin aidoku -j6
taskset -c 2 python3 -B /work/probe/preline-trial.py /work/target/debug/aidoku /work/preline-result.json
```

Slint already documents that `WordBreak` affects shaping; a whole-layout cache or unconditional reuse from the measurement cache would be wrong. Promoting this variant as written would require maintaining a patch to **private Slint internals** and handling font-collection versioning, item lifetime, invalidation and bounded memory. The normal Aidoku product and its scheduling remain unchanged. Before accepting that maintenance cost, test a production-optimized release with the native renderer's gray target and quantify clone memory/cost. A physical single-arm profile cannot establish that such a change improves ordinary Kindle startup.
