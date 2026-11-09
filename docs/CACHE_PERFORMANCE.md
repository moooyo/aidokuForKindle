# Cache Entry Scaling: Follow-up Iteration

This report preserves the Phase 2 cache comparison and correctness recheck.
Its source snapshots and measurements are historical. See
[the Phase 3 verification report](PHASE3_VERIFICATION.md) for the current
Candidate7 evidence; the numbers below are not fresh Candidate7 measurements.

This is the follow-up cache optimization benchmark. The earlier 72-page download
measurements remain in `DOWNLOAD_PERFORMANCE.md`; they are not evidence that the
changes in this iteration passed.

All execution takes place through `ssh test-env`. The benchmark never runs on the
developer workstation. Baseline and optimized snapshots start from the same
frozen engine source. Only `src/cache.rs` differs. Both executables use the same
example, compiler, release profile, disabled default features, instrumentation,
fixture bytes, entry counts, and operation counts.

## Workloads

The entry counts are 1,000, 5,000, and 10,000. Every managed artifact is an
independent regular file containing the same valid 8-by-8 grayscale PNG. The
payload, dimensions, and SHA-256 are identical across implementations and sizes.
This isolates index, pin, eviction, and lock costs; image decoding throughput is
covered separately by the download benchmark.

Fixture creation runs in its own process: the production cache creates its
schema, every manager and guard closes, then one SQLite transaction seeds real
artifact keys, lengths, checksums, post-write fingerprints, access order, durable
download owners, and aliases. Linux `syncfs` completes preparation. The measured
process reopens that fixture, so neither the registry's cached state nor fixture
generation's RSS high-water mark contaminates the workload.

| Workload | Measurement |
| --- | --- |
| Same owner and process pin | Exactly 1,000 calls after a real warm pass, with the same keys and owner; total wall time and per-call p50/p95/max |
| Clear with foreground access | Clear total wall time while a pinned raw cache hit is looked up every requested millisecond; one additional entry has a real lease |
| Clear races | Real leases and reservations, lease-to-pin protection, a new pinned commit, a replacement with different valid PNG bytes of the same dimensions, and an unprotected commit |
| Pin membership changes | Direct persistent owner checks after unpin/repin and adding a different owner; another clear must preserve promised protection |

Foreground samples retain start/end timestamps. Only call intervals intersecting
clear's first SQLite work through its return enter the reported p50/p95/max.
In-flight calls complete before collection stops. The sample count is reported;
a long blocked call is not diluted by fast calls before or after clear. Race
intervals distinguish requests blocked until clear returns from API operations
that actually complete inside the clear interval.

## SQL and resource accounting

A SQLite auto-extension installs `trace_v2` and `update_hook` on the real cache
connection. This uses the bundled raw SQLite API and does not change production
source, dependency features, or database schema. A qualification executes known
INSERT, UPDATE, DELETE, BEGIN, and COMMIT operations before accepting counters.

Counters preserve actual executions rather than unique SQL strings. DML attempts,
SELECTs, aggregate SELECTs, explicit transaction boundaries, autocommit DML
attempts, and core-table row events remain separate. Operation, foreground, and
race threads have separate counters. Trigger/subprogram events are not counted
again as parent statements. These counters do not claim physical disk writes.
An optional separate syscall audit may count `fsync`/`fdatasync`; its traced wall
time must not be used as the primary performance result.

Setup, warming, correctness scans, and destructor flushes are outside the SQL
measurement window. RSS uses the previously qualified small native launcher and
Linux `wait4`, with post-exec `/proc` samples retained. The whole measured process
includes cache opening and final correctness checks; operation wall time is
reported separately.

## Reproduce remotely

Build the same example in the two independent snapshots, then freeze copies of
both executables before another Cargo invocation can replace the target output:

```sh
CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR=/root/aidoku-build/target \
  /root/.cargo/bin/cargo build --locked --release --no-default-features \
  --example cache-benchmark
python3 scripts/benchmark-cache.py \
  --baseline-binary /absolute/frozen/cache-benchmark-baseline \
  --optimized-binary /absolute/frozen/cache-benchmark-optimized \
  --baseline-source /absolute/baseline-snapshot \
  --optimized-source /absolute/optimized-snapshot \
  --output artifacts/performance/cache-scaling-YYYYMMDD-HHMMSS
```

The default is three alternating-order repetitions at all three entry counts,
with 1,000 timed pins and requested 1 ms foreground probes. The runner verifies
that the only production source delta is `src/cache.rs` and that the benchmark
example is identical. Output directories must be fresh. Raw process JSON, SQL
counters, lookup timestamps, correctness results, seed logs, source manifests,
binary hashes, and the complete configuration are retained.

For separate desktop/UI responsiveness setup, the example exposes a fully public
API population path:

```sh
cache-benchmark --scenario populate-public \
  --data-dir /tmp/aidoku-phase2/isolated-ui-data --entries 1000
```

It uses `open`, `begin_raw`, `validate_path`, `commit`, and `set_alias` without
direct SQL, creating unpinned 256-by-256 grayscale PNGs in a dedicated namespace.
It reports actual byte totals and image hashes. This UI fixture is separate from
the fixed 8-by-8 scale comparison and must never be substituted into its timing
results. The caller must pass its isolated test directory.

## Results

The final formatted implementation passed the complete controlled run: 40 main
processes and 12 separate syscall-audit passes. The source-manifest gate confirmed
that `src/cache.rs` was the only production difference. Every fixture used the
same 140-byte, 8-by-8 PNG with SHA-256
`a0f1b72e32c48c04b83f5d8df1f24704c85089f500e055cc5fffcee954dbe93a`.
Three alternating-order repetitions were completed at every size.

### Warm same-owner pin

Exactly 1,000 calls followed a real warm pass over the same keys and owner.

| Cache entries | Baseline median total (s) | Optimized median total (ms) | Baseline per-call p95 (ms) | Optimized per-call p95 (microseconds) |
| --- | --- | --- | --- | --- |
| 1,000 | 0.404 | 3.426 | 0.434 | 3.369 |
| 5,000 | 2.511 | 3.630 | 2.615 | 3.530 |
| 10,000 | 5.448 | 3.666 | 5.711 | 3.550 |

The p95 columns are the median of the three per-trial p95 values. Every baseline
trial executed 1,000 DML statements and 1,000 full-total aggregate queries.
Every optimized trial executed 1,000 indexed SELECTs, zero DML, zero aggregates,
and zero explicit transaction statements. The separate syscall audit recorded
zero `fsync` and `fdatasync` calls for both versions: SQLite already avoided a
physical flush for the baseline's same-value updates. The measured improvement
here is SQL and aggregate work, rather than a claimed reduction in disk flushes.

### Clear and foreground responsiveness

Each fixture retained one durable pin and one real lease; all other entries and
their aliases had to disappear. The foreground target remained a valid hit.

| Entries | Baseline median clear (s) | Optimized median clear (s) | Baseline median foreground p95 (ms) | Optimized median foreground p50 / p95 (ms) | Optimized worst foreground call (ms) |
| --- | --- | --- | --- | --- | --- |
| 1,000 | 5.276 | 0.432 | 5,280.206 | 23.364 / 39.317 | 51.260 |
| 5,000 | 27.326 | 2.135 | 27,330.404 | 23.329 / 39.433 | 45.884 |
| 10,000 | 54.339 | 4.372 | 54,343.161 | 25.263 / 43.307 | 59.216 |

Foreground percentile columns use the median of per-trial values; the final
column is the largest individual call in any trial. Each baseline clear allowed
only one overlapping lookup, blocked for effectively the entire operation, so
that call is its p50, p95, and maximum. The optimized trials had 17, 79, and 157
overlapping calls per trial respectively. All individual timestamps and trial
values are retained, including the 46.790-second foreground outlier in the first
5,000-entry baseline trial.

Both implementations still deleted the same number of artifact rows. At 10,000
entries, baseline clear executed 9,998 autocommit deletes and 9,998 full-total
aggregates; optimized clear used 313 deletion-batch commits plus one access-flush
commit, zero autocommit DML, and zero aggregate queries in the operation window.
The common access flush accounts for one additional artifact UPDATE in both
versions. Cascaded alias row events are counted separately from parent statement
executions.

The syscall audit covers each executable's whole lifecycle, including opening,
warming, operation, verification, and close, with seed excluded:

| Entries | Baseline clear successful fsync | Optimized clear successful fsync |
| --- | --- | --- |
| 1,000 | 4,004 | 144 |
| 5,000 | 20,004 | 680 |
| 10,000 | 40,004 | 1,348 |

No audit pass issued `fdatasync`. Audit timings are excluded from the wall-time
tables. Across the measured pin trials, largest RSS was 7,448/7,476 KiB at 1,000
entries, 10,264/10,288 KiB at 5,000, and 10,212/10,320 KiB at 10,000
(baseline/optimized). Corresponding clear maxima were 7,212/7,356, 8,524/8,644,
and 8,656/8,644 KiB. These are entry-metadata workloads with tiny fixed images,
not full application or maximum-size manga-page memory measurements.

### Safety and provenance

All checks passed for pinned and leased files, active replacement reservations,
different replacement bytes, persistent new-commit owners, unprotected commit
row/file consistency, unpin-to-repin, different owners, a second clear, aliases,
and SQLite integrity. In the optimized race fixture, pin and both new-commit
operations completed inside the clear interval. The earlier unlock-plus-yield
candidate failed that actual-interleaving gate; it is retained as preflight
evidence rather than included in the final results. The accepted version uses a
FIFO ticket mutex without a timing sleep.

Final evidence is retained under
`artifacts/performance/cache-scaling-formatted-final-20261105/`: complete
`results.json`, `summary.json`, process RSS/CPU JSON, per-call lookup timestamps,
SQL and row counters by role, seed configuration, syscall logs, correctness
outputs, and manifests. The run is a controlled comparison on the designated
remote host and does not predict physical Kindle flash latency.

| Item | SHA-256 |
| --- | --- |
| Baseline cache source | `a39a9c10d434deed661b490608f2b15a635302bbdc725b65a9119e6a1c186ae7` |
| Optimized formatted cache source | `0987c987b26efba9db07a5161702bfc1886ee1cd5fd02351cab6c10169f3e0c3` |
| Baseline executable | `b1c1bf20c994d9804af72b1d7120a822b233a499f72e3d7e9d19117bb4d2be5f` |
| Optimized executable | `327be66403c072b010a416018bf7e76e0817497c26a9c9a2adf173ec53e9b0da` |
| Baseline complete source manifest | `47890c36b552f0b231c39347030eaee014c370e65e761141fbd6b7331d1dac2f` |
| Optimized complete source manifest | `3793c222c7ece68d7f5ac2fa6ff6b49ab9d77e430fd161562b00b86e9d8c5399` |

## Candidate 2 integration recheck

After the final source, recovery, and application integration freeze, a separate
single 6-by-12 download regression run used Candidate 2's frozen release example.
It ran only the regression scenarios, with no throughput repetitions. All ten
checks passed: duplicate batches made exactly 72 image GETs plus six page-list
requests, completed cache replay made zero HTTP requests, the reader and
downloader shared bytes, pause/resume requested six images before and six after
restart, singleflight made one GET, differing headers kept separate identities,
foreground capacity reached three while background stayed bounded, download pins
survived LRU, and low-space failure preserved existing data.

The batch's 72 decoded files matched the same 52,062,680 image payload bytes.
Recorded process durations from this correctness run are not new throughput
measurements, because another agent was preparing a test executable concurrently.
The cache source hash remained the optimized `0987c987...69f3e0c3` above.

Evidence:
`artifacts/performance/phase2-candidate2/download-regressions-6x12/`.
Release binary SHA-256:
`cfe393c8508a13ba45c8ba083248138f16e10171652a223cf284018f297c8650`.
Complete Candidate 2 source-manifest SHA-256:
`b3c67ec185aa6c599668bf89b0bbbc447bae666f6a36d81d9913574e5b83ae1b`.
