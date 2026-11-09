# Reader Metadata Performance

This report preserves the Phase 2 Candidate2 metadata measurement. See
[the Phase 3 verification report](PHASE3_VERIFICATION.md) for current Candidate7
evidence. The indexed-query counts and timings below retain their original scope.

The final Candidate2 reader metadata collector issued three indexed read
statements with cached committed progress and four without it at both 1,000 and
5,000 library books. SQLite reported zero full-scan steps for these operations.
The restored legacy strategy issued 1,008 and 5,008 read statements respectively.

This is a measurement of a menu-state toggle followed by metadata collection on
the designated Linux worker. It is not an end-to-end UI, input, image-loading,
sleep/resume, or Kindle hardware latency measurement.

## Candidate and evidence

The formal run used the frozen executable built from
`/tmp/aidoku-phase2-candidate2`. It started at
`2026-11-05T10:52:11.052154+00:00`, finished at
`2026-11-05T10:52:18.156775+00:00`, and exited successfully. The process wall time
was **7.104587625 seconds**. This outer time includes fixture preparation,
warm-ups, assertions, all timed samples, and cleanup; it is separate from the
per-operation measurements below.

| Identity | SHA-256 |
| --- | --- |
| Frozen benchmark executable | `63cd23a20d13570240da3943254c006edca9387bf1599c8f3dec38677001548e` |
| Candidate2 `src/app/view.rs` | `21f74df03c6edd021b915e63cccac8b978f1a35849c9ebe954053759812a8af3` |
| Archived legacy `src/app/view.rs` | `0a3f9fd72916860378aa267d3b4ef95e1f3d52acd5a08ca7cd17795564b20bd2` |
| Raw benchmark JSON | `4d65c84b4941eb81f632e5e1230e987988c855c2c021007568c8fb0636fbbfe4` |

The executable was copied out of Cargo's shared target directory before the
formal run. Its SHA-256 was identical before and after execution. It used the
release profile with optimization level 3, debug assertions disabled, and no
application features enabled. The recorded compiler was
`rustc 1.99.1 (58cf8f9c3 2026-10-13)` on Linux x86_64. This is a host library-test
executable; no Slint backend or application window was started.

The retained evidence is in
[`artifacts/performance/reader-candidate2-final/`](../artifacts/performance/reader-candidate2-final/):

- [`benchmark.json`](../artifacts/performance/reader-candidate2-final/benchmark.json)
  contains every raw nanosecond sample, quantiles, actual SQL text and counts,
  SQLite statement counters, and result comparisons.
- [`benchmark.log`](../artifacts/performance/reader-candidate2-final/benchmark.log)
  contains the test output.
- [`run-manifest.json`](../artifacts/performance/reader-candidate2-final/run-manifest.json)
  records the exact command, execution timestamps, exit status, process wall time,
  and hashes of the executable, JSON, log, and compilation manifest.
- [`compilation-manifest.json`](../artifacts/performance/reader-candidate2-final/compilation-manifest.json)
  records Cargo's executable profile and feature list, the compiler and host,
  and hashes of Cargo files, the build script, Rust sources, and Slint sources.
- `build.jsonl`, `compiled-view.rs`, `baseline-view.rs`, and
  `reader-metadata-benchmark` preserve the build output, compared source files,
  and frozen executable.

## Compared paths

The production Reader branch returns before loading sources, the complete book
list, reading history, or daily-update statistics. Its
[`load_reader_metadata`](../src/app/view.rs) function projects the displayed
session, loads the current book, checks the displayed page's bookmark, and reads
the actual successor chapter's download flag. The benchmark calls this same
production function.

For a chapter with a successor, the read contract is:

1. `books` lookup by its primary key.
2. `progress` lookup by the chapter primary key when committed progress is not
   available for the displayed book and chapter.
3. `bookmarks` existence check by its composite chapter/page primary key.
4. Successor `chapters` lookup by its primary key.

The matching displayed chapter uses the current reader index. A displayed frame
from another chapter is located within the current book's in-memory chapter
slice. Successor selection uses `index + 1`, including rows with equal chapter
numbers. The measured fixture exercises the normal matching case.

The legacy collector reconstructs the metadata-read strategy from the archived
source identified above: sources, all books, all history, chapters for every
library book, daily-update collection and sorting, recent-progress collection,
and the selected book's card/chapter/bookmark reads. It executes those real Store
methods in the same Candidate2 release test executable. The historical source
hash identifies the strategy's origin; this does not execute a complete
historical application binary.

Both compared paths exclude `sync_view` route dispatch, Slint property setters,
model/layout work, the rest of `sync_reader_view` including its chapter counts,
`App.apply_reader_update`, image decoding and rendering, worker scheduling,
progress writes, and platform status I/O. Those costs must be measured separately
when assessing total reader responsiveness.

## Fixture and method

| Property | 1k fixture | 5k fixture |
| --- | ---: | ---: |
| Library books | 1,000 | 5,000 |
| Chapters per book | 12 | 12 |
| Total chapters | 12,000 | 60,000 |
| Progress rows per book | 4 | 4 |
| Total progress rows | 4,000 | 20,000 |
| Declared page count per chapter | 32 | 32 |

The current book, chapter 4, page 5, and displayed session are the same in both
fixtures. The current book's progress is the most recent record, giving the
legacy book-progress query a favorable position in its history index. Books
have nonempty metadata and unread chapters dated on the fixture's current day,
so the legacy daily-update work is exercised.

The fixture marks the successor as downloaded after the ReaderState snapshot is
created. Its 32 page records refer to one real local 2x2 PNG to satisfy Store's
local-file checks. No download worker is run. Both strategies must observe the
persisted flag despite the session's older chapter flags. A bookmark is present
on the displayed page.

Fixture insertion uses a transaction outside all operation timings. Each scale
uses the same already-open database and equivalent reader state for all three
strategies.
Three warm-up cycles run before measurement; caches are not flushed. A progress
hit or miss describes only the in-memory committed progress record. Both cases
use the warmed database.

SQL statements and SQLite `FULLSCAN_STEP`/`VM_STEP` counters are recorded by
`sqlite3_trace_v2` in one separate collection per strategy and scale. The callback
is removed before latency sampling. The table's SQL counters therefore describe
one operation, rather than the sum of all 41 timed samples.

Each timed operation toggles the menu and invokes its collector. Each strategy
has 41 samples per scale. The execution order rotates through
`legacy -> hit -> miss`, `hit -> miss -> legacy`, and
`miss -> legacy -> hit`. The report retains the samples in execution order and
uses nearest-rank p50 and p95. Application UI, native verification, Cargo, cache
correctness, and other benchmark runs were stopped for the formal measurement
window.

Every sampled result is compared with the legacy result, including the book,
progress, bookmark, successor flag, and visible projection. Both scales passed
all comparisons. Separate regression tests cover a displayed page lagging the
requested page, same-chapter preview, cross-chapter frames, and equal-number
successors; these scenarios are not timed by this fixture.

## Observed results

All six SQL collections reported zero write statements. Times below are
microseconds per menu-state toggle plus metadata collection.

| Books | Strategy | Read statements | Full-scan steps | VM steps | p50 (us) | p95 (us) |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1,000 | Restored legacy | 1,008 | 8,997 | 417,675 | 22,608.491 | 23,949.678 |
| 1,000 | Committed progress hit | 3 | 0 | 43 | 60.128 | 75.829 |
| 1,000 | Committed progress miss | 4 | 0 | 60 | 27.699 | 89.277 |
| 5,000 | Restored legacy | 5,008 | 44,997 | 2,085,675 | 128,649.121 | 140,814.330 |
| 5,000 | Committed progress hit | 3 | 0 | 43 | 144.067 | 163.166 |
| 5,000 | Committed progress miss | 4 | 0 | 60 | 33.218 | 167.475 |

The indexed collector's SQL count and VM-step count stay fixed across these two
library sizes. Its largest observed p95 is 167.475 us, compared with 140.814330 ms
for the legacy strategy at 5k books. This supports removing whole-library SQL
work from the measured metadata path.

Elapsed time is not constant: the hit median rises from 60.128 to 144.067 us,
and both indexed p95 values increase at 5k. SQLite VM-step counts do not account
for every internal B-tree operation or operating-system cost. Zero full-scan
steps also do not imply zero physical I/O.

The hit median is higher than the miss median in this run. The cyclic order
places 27 hit samples and 13 miss samples per scale immediately after a legacy
collection, and the raw latency groups correlate with those positions. These medians do not
isolate the cost of the optional progress query. CPU caches, allocation behavior,
and scheduling are possible explanations for the order effect, but this run does
not establish their individual causes. It should not be used to claim that a
progress-cache miss is intrinsically faster than a hit.

The fixture keeps the current book at 12 chapters. A cross-chapter projection can
search that book's chapter slice, and the unmeasured Slint synchronization still
counts its chapters. This evidence establishes bounded library-wide SQL work for
the collector, not constant CPU work for every reader operation. It does not
certify a 500 ms long-press response, ARM performance, e-ink refresh latency,
physical suspend/resume, or power consumption.

## Reproduction

Run only on the designated remote worker. Reserve an exclusive measurement
window after builds and other application verification stop. The compilation
manifest preserves the exact `cargo test --locked --release
--no-default-features --lib reader_metadata_scaling_benchmark --no-run --message-format=json` command
with `CARGO_BUILD_JOBS=2` and the shared target directory. A new compilation must
be copied to a fresh artifact directory with its own executable/source hashes
before it is measured.

The retained frozen executable can be replayed from PowerShell as follows. The
fresh temporary directory keeps the formal evidence above intact.

```powershell
$readerReplayCommands = @'
set -eu
cd /tmp/aidoku-phase2-candidate2
readerReplayOutput=$(mktemp -d /tmp/aidoku-reader-replay.XXXXXX)
export AIDOKU_READER_BENCH_OUTPUT="$readerReplayOutput/benchmark.json"
artifacts/performance/reader-candidate2-final/reader-metadata-benchmark \
  app::view::reader_metadata_tests::reader_metadata_scaling_benchmark \
  --exact --ignored --nocapture --test-threads=1 \
  > "$readerReplayOutput/benchmark.log" 2>&1
printf '%s\n' "$readerReplayOutput"
'@
$readerReplayCommands = $readerReplayCommands.Replace("`r", "")
ssh test-env $readerReplayCommands
```

Retain the JSON and log together with the executable hash, actual source hashes,
command, exit status, execution timestamps, and process wall time. Compare raw
results and scope before reusing these numbers for another candidate.
