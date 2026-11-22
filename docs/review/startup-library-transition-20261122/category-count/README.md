# Empty category text: no change

**Keep the production Library unchanged.** Each category creates a `Text` item whose text is always empty. Removing just this item on a private copy did not meet the predeclared screen: at least 1 ms of paired median first-draw improvement and at least five of six wins **on both layouts**, with no material regression to the complete Library. The test queried no Kindle or shared lease.

| Host layout | First Library render, control / candidate median | Paired render saving | Candidate wins | Entry → Library paired saving | Candidate wins |
| --- | ---: | ---: | ---: | ---: | ---: |
| Scribe, 930 × 1240 | 58.263 / 57.543 ms | +0.422 ms | 4/6 | −1.080 ms | 0/6 |
| PW6, 632 × 840 | 46.326 / 46.701 ms | +0.206 ms | 3/6 | −1.954 ms | 1/6 |

Positive is faster without the empty item. Each difference is computed within its pair before taking the median; a difference of arm medians is not interchangeable. A control-first / candidate-first split gives +0.368 / +0.476 ms on Scribe and −0.067 / +0.479 ms on PW6 for the draw. These data do not show that deleting an empty node explains any meaningful part of a native draw or that it slows the actual Kindle card. [All per-visit measurements and orders](result.json) are retained.

## What was timed

This reuses the [real asynchronous bootstrap and software-window fixture](../attach-wake/README.md), with the ordinary delayed attachment path fixed in **both** arms. A is the original Library; B physically removes only the eight-line `category-count` declaration from `ui/library.slint`, with the rest of that file byte-for-byte unchanged. It does not hide a container, change fonts or defer/discard work elsewhere. Both x86_64 development executables were built from the same source path with the same Cargo target and instrumented in the same way. Boot scene, font registration, `TZ=UTC`, CPU affinity and the synthetic six-book shelf are identical. No profiler was active for these visits.

The primary sample brackets the actual synchronous `SoftwareRenderer::render(buffer, stride)` call using `Instant`. Buffer allocation/access, region qualification, file output, interactions and shutdown are outside this timer. Only the first complete, non-booting Library draw counts: full viewport damage, expected ink, six rows, seven cover slots still showing placeholders and a populated Library model. The secondary interval starts at the fixture's `process_entry` (after data seeding) and ends on return from that same draw. A normal Slint pointer press/release opened the sort/filter control; closing it returned to the Library. Every pair, including the excluded warmup, matched over **every RGB byte**. These are host software frames, not an e-ink `SEND`, a physical tap or a panel-completion time.

Each pair ran in fresh processes and data directories. One warmup pair per profile was excluded before six balanced control-first / candidate-first pairs per profile; all 28 visits completed with no replacement. Diagnostic copies, RGB files and the private build target are disposable; the original UI, baseline and device are unchanged.

## Reproduce on `test-env`

Place the working source in the private Ubuntu runner's `/work/source`, the existing `attach-wake/{materialize.py,fixture.rs}` in `/work/study`, and this directory's scripts in `/work/category`. Create fresh `programs` and `reports` directories. Run the two Cargo builds through `ubuntu-run --build` (`-j6`) and reserve the same machine build lock for the short timing command. Do not enable the optional signal-bracket profiler during a comparison.

```sh
cd /work/source
python3 -B /work/study/materialize.py /work/source
python3 -B /work/category/materialize.py instrument
cargo build --offline --locked --no-default-features --features kindle --bin aidoku -j6
cp /work/target/debug/aidoku /work/programs/control
python3 -B /work/category/materialize.py remove-node
cargo build --offline --locked --no-default-features --features kindle --bin aidoku -j6
cp /work/target/debug/aidoku /work/programs/without_empty_text
taskset -c 2 python3 -B /work/category/trial.py run /work/programs /work/reports/result.json
python3 -B /work/category/trial.py check /work/reports/result.json
```
