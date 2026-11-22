# Decision trial: persist local Library thumbnails without adaptive row filtering

## Question and decision rule

The [closed Scribe replay](DIAGNOSTICS.md#observed-cost-ranking) placed the candidate's six-job validated-source-to-prepared-derivative window at 580.852 ms. It did not time the individual PNG and cache stages. Test **one** change: `png::Filter::NoFilter` instead of the default adaptive row filter. Keep the same `png` 0.18.1 compressor, Gray8 pixels, embedded `AidokuRender` metadata, reservation, full staged validation, checksum, fsync, rename, SQLite transaction and verified reload. Do not equate this window with PNG encoding, or Linux XFS with the Kindle's storage.

Freeze these rules before looking at A/B timings:

- Run two separate six-image shelves. The first uses the closed cold confirmation's synthetic JPEGs. The second challenges the choice with line art, panels, gradients/color blocks, thin strokes, tiles and a mostly blank page. Generate both sets of reference derivatives using the retained candidate desktop application, in Scribe Library mode. The exact benchmark inputs are the decoded Gray8 pixels and in-band render keys from those derivatives, not a reconstruction from page dimensions alone.
- In each shelf, warm both code paths separately, then time ten alternating AB/BA pairs. Each arm gets a fresh empty cache for every visit. Sum the six serial operations per visit. Compare cold persistence from reservation through verified post-commit lookup, and separately the cost of restoring the six files from disk with no decoded cache entry. The latter is not a reboot or an OS page-cache purge.
- On **each** shelf require exact pixels and geometry/metadata on reload; a cold paired median relative saving of at least **10%** and at least **9/10** faster pairs; combined encoded bytes at most **105%** of control and no individual PNG above **120%** of its control. A positive warm-reopen regression must not exceed the greater of **1 ms per six images** and **10% of the control reopen median**. Compute cold relative savings within each pair, not from the two separate arm medians.
- If either shelf fails, do not switch the production PNG filter on this evidence. If both pass, restrict the candidate to the measured local grid-thumbnail policy; other render routes retain their tested encoder. Host acceptance does not qualify daily icon startup or authorize installation.

## Decision: keep adaptive filtering

**Do not change the production encoder.** Both shelves failed the predeclared cold-write rule on `test-env` (x86_64 Linux, XFS). The same release executable ran one test per shelf, serially, pinned to one logical CPU under the shared build lock. Each shelf used one warmup per arm and ten alternating pairs; no visit was dropped. Positive savings in the table favor `NoFilter`.

| Six-cover shelf | Paired cold saving | Faster pairs | Paired reopen saving | Encoded bytes, default → `NoFilter` |
| --- | ---: | ---: | ---: | ---: |
| [Original synthetic JPEG shelf](derivative-cold-shelf.json) | **−20.192 ms (−33.924%)** | **0/10** | 1.931 ms | 533,812 → 367,980 |
| [Structured illustrations](derivative-structured-shelf.json) | **+3.344 ms (+6.354%)** | **9/10** | 0.566 ms | 127,381 → 98,814 |

Both size guards and the reopen guard passed on both shelves. Every image was smaller, and every disk reload matched the original pixels and embedded metadata. In each warmup, all 3,084 candidate rows actually used filter zero; the control predominantly used Average on the original shelf (3,009 rows) and Up on the structured shelf (2,265). The negative result is not a failure to enable the candidate. At the same DEFLATE setting, fewer bytes did **not** mean less time spent encoding the original shelf. The structured shelf's gain missed the 10% minimum; do not infer a production exception for line art from it.

| Median over six files (ms) | Original default | Original `NoFilter` | Structured default | Structured `NoFilter` |
| --- | ---: | ---: | ---: | ---: |
| Reserve | 0.512 | 0.558 | 0.486 | 0.472 |
| Encode, including buffered writes | 16.429 | 41.402 | 13.328 | 11.160 |
| Validate staged rows and final chunks | 2.957 | 1.338 | 1.134 | 0.646 |
| Commit durably | 41.851 | 42.043 | 38.669 | 37.543 |
| Remember verified output | 0.251 | 0.285 | 0.289 | 0.262 |
| **Cold total** | **62.088** | **85.147** | **54.372** | **50.512** |
| Reopen lookup and decode | 3.989 | 2.109 | 1.740 | 1.201 |

Stage medians need not sum to the total; subtracting the arm medians does not reproduce the within-pair differences. The largest control stage on **this host** was durable commit. For further cold-start work, profile the staged-file sync, checksum, directory sync and SQLite transaction *within* `CacheWrite::commit` with unchanged durability; do not remove a sync or transplant these numbers into the Scribe's 580.852 ms interval. No cache key, format, reader, download route or installed program changed. `NoFilter` is compiled only for the Linux test; the normal encoder has no runtime switch. The worker built the non-test `aidoku` release with the `kindle` feature on x86_64. The release library suite passed (856 passed, seven intentionally ignored); the ignored decision trial was run separately on both shelves.

## Reproduce on the worker

Use the six synthetic JPEGs from the original staged payload (private ordinal names `01.jpg` through `06.jpg`) and its retained desktop renderer. No Kindle or user library is needed. Inside the shared Ubuntu runner, create the two inputs in a **new** directory:

```sh
python3 -B derivative_fixture.py /work/synthetic /work/aidoku-desktop /work/reproduced
```

Each offline Scribe Library reported six visible books, six ready covers, zero failures and zero pending work. In a separate extraction, all six decoded Gray8 pixel buffers, modes and dimensions on each shelf matched the original host render one-to-one, irrespective of filename order. A target of 376 × 514 is a **bounding box**, not the encoded image width. Source paths and filesystem identities contribute to the embedded rendering key, so restoring the fixture elsewhere need not reproduce the same PNG bytes or file ordering. The trial compares both arms with the *same* six fixed pixel buffers and full metadata.

Build the Linux release library test under the shared build lock (`ubuntu-run --build`, `CARGO_PROFILE_RELEASE_LTO=false`, `CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16`):

```sh
cargo test -j6 --locked --offline --release --no-default-features --features kindle --lib --no-run
```

Copy that executable into a private XFS project as `trial-test`. From `/work` in that project's runner, with the reproduced references available there:

```sh
test_name=render_cache::cover_persistence_trial::real_local_cover_persistence_trial
set -eu
for shelf in primary secondary; do
    AIDOKU_COVER_TRIAL_INPUT="/work/reproduced/$shelf/references" \
    AIDOKU_COVER_TRIAL_ROOT="/work/$shelf-cache" \
    AIDOKU_COVER_TRIAL_OUTPUT="/work/$shelf.json" \
        ./trial-test --ignored --exact "$test_name" --test-threads=1
done
python3 -B derivative_decision.py primary.json secondary.json
```

Use nonexisting cache/report paths and check that **one** ignored test actually ran and passed per shelf. The timed workload is `begin_processed`, PNG encoding, full staged validation, durable commit and verified post-commit `remember`. Reopen timing starts after opening a fresh `CacheManager`, with no decoded entry, and includes the validated lookup and PNG decode. It does not include rebuilding the index, re-fingerprinting a book, rendering a frame or waiting for the panel. Outside timing, every visit verifies the reopened Gray8 bytes, geometry and metadata; the warmup also inflates the actual IDAT data to check the filter byte on every row. The [PNG specification](https://www.w3.org/TR/png-3/#9Filters) distinguishes this lossless per-row step from compression; `NoFilter` does not mean an uncompressed file.
