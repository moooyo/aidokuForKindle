# Phase 3 measurement plan

This plan describes new measurements, not measured improvements. All execution
belongs on `ssh test-env`, after the integration owner grants an exclusive
window. Do not overlap timing with Cargo, application/UI checks, the firmware
VM, image generation, or another benchmark. Preparation and SQL/syscall audits
are separate from primary latency sampling.

## Minimal matrix

| Workload | Fixture | Primary evidence |
| --- | --- | --- |
| Manifest reads and identity publication | 32, 128, 512 pages | Fixed one-page window, old full-manifest strategy in the same Store, one changed path, latency samples and separate SQLite VM/full-scan steps |
| Batched progress | Same three chapter sizes, two pages per commit | Cumulative VM steps and commits, individual batch samples, one final complete-file validation |
| Repeated selection | Twelve existing chapters/jobs | Zero DML attempts and unchanged rows; explicit read-only transaction boundaries remain visible |
| Render cache | Deterministic PNG and JPEG, fixed output geometry | Cold render, processed PNG disk hit, resident prefetched pixels, output hashes, grayscale-output allocation/decode deltas and RSS |
| Navigation edge cases | Odd last spread page and three new vertical windows | Full-width prefetch reuse and matching pixels/geometry; new-window latency separated from warm repeated hits |
| Normal library projection | 1,000 and 5,000 books, twelve chapters per book, real completed manifests | Visible 8/18-card Store projection, returned-object bounds, SQL work and output identity |
| Cache publication | One dirty access hint and a new durable raw owner | Only `CacheWrite::commit()` is timed; separate SQL transaction count and marked syscall audit |

Three fresh-process repetitions are sufficient for the initial gate. Each
steady-state operation keeps 21 raw latency samples. Expensive cold renders and
new vertical windows use fewer explicitly reported samples. Do not rerun the
earlier complete download/cache-scaling matrices merely to obtain more numbers.
Collect SQL counters once for each unique non-render configuration, after the
timing trials, rather than repeating deterministic instrumentation for all
three repetitions. Alternate the two manifest algorithm orders across trials.

## Scope and comparison rules

The new example compiles `src/store.rs` as a source module to call the exact
production crate-visible view queries without expanding the library API. Its
projection measurements exclude Slint property/model work, cover loading,
platform I/O, and application dispatch. A bounded returned-object count does
not imply constant SQLite work: filtering, counts and sorting may scale with
library size. Record those costs rather than hiding them.

The full-manifest strategy executed inside the new Store is an
`algorithm_comparison`. Its schema, triggers and surrounding implementation
are new. It is not a historical Candidate5 binary and cannot establish a
Candidate5 application speedup.

The preserved Candidate5 files in `/root/aidoku-build/phase2-candidate5` contain
the desktop/native application and native sleep fixture, not this new benchmark.
Keep their existing hashes and source snapshot at
`/tmp/aidoku-phase2-candidate5` as context. A historical microbenchmark comparison
would require an explicitly documented supplemental harness compiled from an
independent Candidate5 source copy; do not overwrite the frozen app binaries or
use global `RUSTFLAGS` in the shared Cargo target directory.

`RenderStats` reports processed PNG decodes and rendered grayscale output
buffers. Zero deltas for a resident prefetched page do not mean zero general
allocator calls, zero source decoder scratch memory, zero RGB copies, or zero
process RSS. Retained decoded memory and live output Arcs are separate metrics.

The production offline-usage worker is private and coupled to application job
lifecycle. Its actual initial keyset scan and incremental eight-chapter update
are measured by the ignored library test
`app::download_usage::tests::benchmark_completed_download_usage_production`.
The module is declared by `src/lib.rs`; `src/main.rs` only imports the library's
`app` module. Always select `--lib`. Selecting `--bin aidoku` can execute zero
matching tests and must never be accepted as a passing measurement. A standalone
reproduction of Store keyset calls remains an algorithm comparison, not worker
latency. The worker's `UsageScanStats` are separate from Store projection timing.

SQLite statement attempts, row events, explicit commits and physical fsync are
different metrics. Qualify the observer with repeated statements and a trigger.
Never reuse syscall-traced wall time as a primary latency result. The marked
publication audit counts successful `fsync`/`fdatasync` only between commit
markers, and retains whole-lifecycle counts separately.

## Evidence and gate

Use the stable integration snapshot `/root/aidoku-phase3/source` and shared
`CARGO_TARGET_DIR=/root/aidoku-build/target`, with `CARGO_BUILD_JOBS=12`. Build only
after the integration owner confirms that the source is stable. Freeze the
example outside Cargo's target output before measurement. Cargo automatically
discovers `examples/performance-benchmark.rs`; no manifest entry is needed.

After the integration owner's explicit execution gate, the remote commands are:

```sh
cd /root/aidoku-phase3/source
CARGO_BUILD_JOBS=12 CARGO_TARGET_DIR=/root/aidoku-build/target \
  /root/.cargo/bin/cargo build --locked --release --no-default-features \
  --example performance-benchmark
python3 scripts/benchmark-phase3.py \
  --binary /root/aidoku-build/target/release/examples/performance-benchmark \
  --source /root/aidoku-phase3/source \
  --output /root/aidoku-phase3/evidence/focused-performance-UNIQUE \
  --exclusive-note "Integration owner reserved this window; Cargo, UI and VM work stopped."
```

The runner freezes its own copy of the input executable, uses the existing
qualified native RSS launcher, and requires a fresh output directory. A
`--workloads` subset is an investigation run, not complete application
acceptance. Raw inputs are validated and their fingerprints warmed; no
operating-system page-cache eviction is performed. A `cold` render means a
processed derivative miss and includes derivative publication.

## Separate production usage library benchmark

Compile this target before reserving its timing window. The explicit `--lib`
selection and Cargo JSON artifact record must be retained with the frozen test
executable and its SHA-256:

```sh
CARGO_BUILD_JOBS=12 CARGO_TARGET_DIR=/root/aidoku-build/target \
  /root/.cargo/bin/cargo test --locked --release --no-default-features --lib \
  app::download_usage::tests::benchmark_completed_download_usage_production \
  --no-run --message-format=json > /absolute/fresh/usage-build.jsonl
```

Copy the `compiler-artifact.executable` for the library test from that build
record outside the Cargo target directory. After all builds and competing work
stop, run that frozen library test directly. Create the report's parent
directory first; the JSONL file itself must not exist:

```sh
AIDOKU_USAGE_BENCH_SAMPLES=5 \
AIDOKU_USAGE_BENCH_ROOT=/absolute/fresh/usage-fixtures \
AIDOKU_USAGE_BENCH_OUTPUT=/absolute/fresh/usage.jsonl \
  /absolute/frozen/library-test-executable \
  app::download_usage::tests::benchmark_completed_download_usage_production \
  --ignored --exact --nocapture --test-threads=1 \
  > /absolute/fresh/usage-test.log 2>&1
```

Require a unique `running 1 test` line, the actual executed exact test name, and
a unique successful `1 passed; 0 failed` summary in the retained test log.
Require exactly twenty valid JSONL records: two scales
(1,000 and 5,000), five samples, and both `rebuild` and `append8` for each
scale/sample pair, without duplicates. Every `checks_match` must be true. The
eight-chapter append must report zero keyset path queries, eight chapter
queries, eight manifest windows, sixteen observed references and eight file
checks, with both shared-snapshot identity checks true. Rebuild counters must
match the keyset-128 fixture, including references removed after completion.
A successful process
exit alone is insufficient; an exact filter with zero matching tests fails
these acceptance conditions.

The main runner can ingest these separately measured artifacts through
`--usage-jsonl /absolute/fresh/usage.jsonl` and
`--usage-test-log /absolute/fresh/usage-test.log`. It validates the one-test and
twenty-record contract and retains copies and hashes. These options only read
existing evidence; they do not compile or execute the usage test. The usage
scan and merge durations remain separate from example operation timings.

Retain a fresh output directory with source and binary SHA-256 manifests, exact
commands, UTC timestamps, environment, fixture hashes/dimensions, seed logs, raw
operation samples, separate SQL collections, process CPU/RSS evidence, syscall
logs, all correctness assertions and the exclusive-window note. Verify the
frozen executable hash again after the run. Failures and unavailable metrics
remain in the report. A local validation fallback is prohibited.

The initial acceptance conditions are correct output identity and final Store
state, bounded page rows and visible object counts, zero processed decode and
grayscale-output allocation deltas on resident prefetch reuse, bounded decoded
cache retention, no DML for equal batch selections, and publication durability.
SQL scaling and timings are evidence to assess, not hardcoded speedup targets.
