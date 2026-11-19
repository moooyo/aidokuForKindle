# First page turn after a local import

On `test-env`, precomputing the one hidden cover reduced a quiet page turn from **11.59 to 3.18 ms** before the new row actually had its image. When the turn immediately followed the eight previous workers, the same optimization made the worker faster but did not make the row appear earlier: the normal publication interval dominated. This is a per-cover host result, not a physical Library startup measurement.

| x86 host metric, ms | Quiet off | Quiet idle | Immediate off | Immediate idle |
| --- | ---: | ---: | ---: | ---: |
| Import request to Library metadata in the model | 7.05 | 6.94 | 7.21 | 6.72 |
| Import request to eight visible textures accepted | 75.59 | 76.71 | 76.28 | 80.23 |
| Import request to filled first-page model | 309.45 | 309.08 | 310.07 | 309.10 |
| Import request to all optional work settled | 309.48 | 819.70 | 310.12 | 821.14 |
| New process, page turn to target worker | 10.37 | 2.58 | 9.63 | 2.04 |
| New process, page turn to target image in the model | 11.59 | 3.18 | 286.34 | 286.82 |

Cells are medians of four trials per arm per interaction timing. The median of adjacent off-minus-idle paired differences for actual model publication was **+8.557 ms** after quiet and **-0.482 ms** when turning immediately. All eight `off` targets were disk misses, and all eight `idle` targets were disk hits. Across all sixteen trials the foreground worker produced identical 343 x 514 RGB data (SHA-256 `4cdaca7334db786223152363380fda43151188a8c6d163c2ede6289fd259970a`). Preparation moved work into the import's idle period: its roughly 510 ms additional wall time includes the deliberate 500 ms quiet gate and must not be interpreted as compute time.

This host experiment separates one cover worker's actual RGB output from the time that the Library publishes it. It uses two new Linux processes per trial: one for a normal import, another for a normal Library page turn. The two processes share only the trial's private data directory. No generated test code is part of the application package.

The trial uses nine unmodified CBZs from the [20-book synthetic fixture](../../bench/import_probe/fixture.py). S05 through S12 are the eight visible PW5 titles after import. S04 is a 1024 x 1536 grayscale PNG, alone on the second page. File ordering fixes the initial selection at S05; descending title order keeps it on page one. After the measured import finishes, the harness saves one normal S05 progress row so that a new process will keep the same current-book tile regardless of an import crossing a timestamp boundary. The harness asserts that S04 was never in the first-page selection or visible worker texture cache. No source or derivative is injected into the application.

Both arms load the production `startup-options` reader and run `App::attach`, `App::import_local`, `App::poll`, the visible cover worker, and the actual `page/turn` command. The only option changed is `import_thumbnail=off|idle`. A headless Slint window supplies the real view models. On the second page, the test observes the regular worker result, including its local-derivative hit bit and shared RGB pixels, then returns the unchanged job messages in arrival order for normal acceptance. It stops timing the model when the correct row contains a nonempty image. There is no physical display, input latency, driver submission or optical observation here. The OS page cache is not flushed.

Two interaction timings exercise the normal 300 ms publication cadence independently. `immediate` turns after the first page's eight workers have completed, so prior publication can still be pending. `quiet` waits until every first-page slot contains its image and then waits 400 ms before turning. The artificial dwell is outside the page-turn clock. Each timing uses four adjacent control/candidate pairs in `off, idle, idle, off, off, idle, idle, off` order, with fresh synthetic files, SQLite databases and processes each time. The observer polls every 2 ms except when awaiting the target worker directly. The reported medians describe this machine and image; neither on-device speed nor battery use can be inferred.

To reproduce, use the isolated source directory on `ssh test-env` and its six-job shared Cargo lock. In a private test source copy, copy [bench.rs](bench.rs) to `src/app/import_thumbnails/bench.rs` and append `#[cfg(test)] mod bench;` to `src/app/import_thumbnails.rs`. The installed application and normal build do not include this test. Build the specialized libtest executable with the [existing host build script](../build-host.sh), then run:

```sh
python3 -I run.py /absolute/path/to/host-tests /absolute/path/to/import_probe/fixture.py quiet.json --turn quiet
python3 -I run.py /absolute/path/to/host-tests /absolute/path/to/import_probe/fixture.py immediate.json --turn immediate
```

The driver refuses incorrect visible sets, target source hashes, archive mutations, unexpected derivative counts, and differing foreground RGB hashes. `settled_ms` includes the import, visible model publication and optional idle work, including any deliberate quiet delay; it is wall time, not worker CPU time. `visible_workers_ms` ends when all eight first-page textures have been accepted; `visible_model_ms` ends when all first-page slots have reached the Slint model. The exact raw times, medians, test ELF hash and fixture hash are in [quiet.json](quiet.json) and [immediate.json](immediate.json).
