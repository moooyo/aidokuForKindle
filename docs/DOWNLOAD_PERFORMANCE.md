# Download and Cache Performance

This report preserves historical Phase 2 host and firmware-guest measurements.
See [the Phase 3 verification report](PHASE3_VERIFICATION.md) for current
Candidate7 evidence; the throughput and test counts below are not new results
from that candidate.

This benchmark measures the production download worker and checks the shared page
cache against a deterministic, authenticated HTTP fixture. It must run on the
designated remote verification host. Running it on a developer workstation
requires the user's explicit authorization.

The production path streams responses into one canonical raw cache, reuses
connections, and coalesces identical URL/header requests across readers and
downloads. Two background page transfers share admission with a reserved
foreground slot. Download progress is persisted in batches; completed downloads
remain pinned while unpinned cache entries use a 256 MiB LRU budget and a 64 MiB
free-space reserve. Raw and processed cache storage are shared across sessions.

## Workload and baseline

The default workload contains six chapters of twelve pages each. Every page is a
valid grayscale PNG or JPEG with deterministic, seeded pixels and a distinct
dimension pair. Images alternate PNG and JPEG, use dimensions close to a scanned
comic page, and remain below the application's compressed-image and decoded-pixel
limits. The server requires the fixture API key, waits 120 ms before each image,
and supports HTTP/1.1 connections. Metadata requests have no artificial delay.

Both throughput modes use the same Rust executable, production
`DownloadWorker::start_configured`, cache, HTTP client, SQLite settings, page bytes,
and remote machine. The baseline uses `DownloadOptions.page_concurrency = 1`.
The optimized mode changes only that option to `2`. Each trial starts with a fresh
application data directory. Three repetitions alternate their execution order to
reduce order effects. This is a controlled comparison of serial and concurrent
scheduling in the current implementation; it is not a measurement of a historical
application release.

The HTTP fixture records every request, response byte count, start/end time,
connection, and peak image concurrency. The runner records process wall time,
CPU time, Linux `wait4` maximum resident set size, and 10 ms `/proc/PID/status`
samples. A small native launcher forks and measures the Rust child so the Python
fixture process's memory cannot contaminate the child's pre-exec high-water mark.
Only samples taken after `/proc/PID/exe` identifies the Rust executable are used.
The measured process includes SQLite setup, downloading, cache image
validation, worker teardown, a final decode/hash of every image, and JSON output.
Fixture image generation and the Python server run outside the measured process.

The executable checks completed job counts, chapter download flags, persisted page
paths, and reopened SQLite state. Every final image is decoded and its SHA-256,
dimensions, and byte length are compared with the fixture manifest. An exit code
alone is insufficient to pass a trial.

## Reproduce remotely

Copy a single source snapshot to an independent remote benchmark directory. Do not
modify a source snapshot that another verification task is using. From that
remote snapshot, run:

```sh
CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR=/root/aidoku-build/target \
  /root/.cargo/bin/cargo build --locked --release --no-default-features --example download-benchmark
python3 scripts/benchmark-downloads.py \
  --binary /root/aidoku-build/target/release/examples/download-benchmark \
  --build-profile release \
  --output artifacts/performance/downloads-YYYYMMDD-HHMMSS
```

Python 3, Pillow, and a C compiler are required on the remote host. The fixture binds only to
loopback and uses no public source service. The output directory must not already
exist. The default configuration uses the production background page limit of
two; it does not increase concurrency to produce a larger speedup.

`--only throughput` and `--only regressions` select a subset while investigating a
failure. A partial run is not a full acceptance result. The `--latency-ms`,
`--chunk-bytes`, and `--chunk-delay-ms` options define the network fixture, and are
saved with every result. Comparisons require identical configurations and source
and binary hashes.
The executable reports its compiled profile and enabled application features.
The runner rejects a profile label that differs from the executable's report.

## Regression scenarios

| Scenario | Required evidence |
| --- | --- |
| Batch queue with three duplicate enqueues | Exactly 72 image GETs; one durable job per chapter; all 72 files valid |
| Re-enqueue completed chapters | Zero additional HTTP requests, including metadata; a local-manifest queue barrier confirms this process's commands were handled |
| Read four pages, then download their chapter | Twelve image GETs in total; reader and downloader use identical canonical paths; reading progress survives |
| Pause, close the worker, and restart | A durable partial job; the next process requests only missing pages; all twelve pages finish |
| Eight simultaneous readers/downloaders from two independently constructed source managers | One image GET and one canonical path |
| Identical URL with different request headers | Two image GETs, distinct canonical paths, and distinct response hashes |
| Twenty-four background callers plus interactive reading | A server gate first establishes two active background requests; the interactive request uses the third, reserved slot before queued background work |
| LRU with a completed worker download and two unpinned pages | The oldest unpinned page is removed; the newer page and all twelve production download pins remain valid after worker teardown |
| An impossible free-space reserve | A clear storage error; no incomplete cache entry; an existing pinned page retains its path and SHA-256 |

The LRU scenario uses a small `CacheConfig.max_unpinned_bytes` budget. The low-space
scenario injects `CacheConfig.min_free_bytes = u64::MAX`; it does not fill the
remote disk. That scenario validates the application's low-space policy and
preservation behavior, not a physical disk failure or filesystem crash.

## Artifacts and interpretation

`results.json` contains the environment, binary SHA-256, source-file manifest,
configuration, per-process measurements, HTTP statistics, and acceptance checks.
`fixtures.json` contains each input image's hash, dimensions, format, and byte
length. Each trial has `stdout.json`, `stderr.log`, `process.json`, and `http.json`.
`requests.json` preserves the full request chronology across scenarios.
The generated measurement launcher source, binary hash, and compile log are also
retained. Each Rust scenario reports an internal duration, which avoids the
runner's polling tail when examining very short cache-only operations.

A remote launcher qualification allocated 128 MiB in the Python parent before
starting a lightweight child. The direct outer `wait4` result was 158,208 KiB,
while the launcher measured the child at 10,692 KiB and obtained post-exec `/proc`
samples. This confirms why the outer value is retained only as diagnostic data.
The qualification is not an application performance result; its evidence is
`artifacts/performance/measurement-qualification/qualification.json`.

For native Linux/ARM measurements, the example also accepts
`--resource-report /absolute/guest/path/rusage.json`. A lightweight instance of
the same executable launches and reaps the actual scenario, recording its wall
time, child PID, and raw `wait4` resource fields. Its standard output remains the scenario
JSON. `--allow-private-fixture-host` permits an explicitly selected private VM
network fixture; the default accepts only a loopback fixture. Guest and host
measurements must be reported separately and use their own matching baselines.
The host singleflight regression also exercises this native resource-report path
and retains its `native-rusage.json`.
A separate remote qualification confirmed that the native launcher returns the
scenario JSON unchanged, records successful child wall time and RSS, and retains
singleflight behavior. Its evidence is
`artifacts/performance/native-resource-qualification/qualification.json`; it is a
launcher correctness check rather than a throughput comparison.

The designated ARM firmware guest returned zero CPU and RSS fields through
`wait4`, even after a successful 24-page run. Those fields are retained and marked
unavailable. The native measurements therefore use `scripts/native-proc-sampler.c`:
one process requests 20 ms sleeps and reads the actual scenario child's
`/proc/PID/status` and `stat`. Its executable, parent PID, and final resource-report
PID must agree. CSV timestamps preserve the actual sampling intervals. The
reported memory is the maximum observed `VmHWM`; a late change within an
unsampled interval can be missed. CPU ticks divided by `CLK_TCK` are also
reported as observed values. The sampler's guest qualification touched 16 MiB
and observed 16,428 KiB across 44 samples, with the correct child PID.

For a guest comparison, start the host fixture with the frozen ARM ELF:

```sh
python3 scripts/benchmark-downloads.py \
  --binary /absolute/path/to/arm/download-benchmark \
  --build-profile release \
  --output artifacts/performance/native-3x8-YYYYMMDD-HHMMSS \
  --chapters 3 --pages 8 --serve-native \
  --guest-host-address 192.168.15.201
```

The guest fixture listens on `0.0.0.0` and records the observed guest NAT host
address in `fixture-ready.json`, together with the ELF hash and guest URLs. The `/serial` and
`/concurrent` prefixes tag the HTTP phases while serving identical image bytes.
Run the guest executable with `--scenario throughput`, separate fresh guest data
directories, `--chapters 3 --pages 8`, `--concurrency 1` or `2`,
`--allow-private-fixture-host`, and a separate `--resource-report` path per phase.
On this firmware, invoke each phase through the same sampler:

```sh
native-proc-sampler phase.samples.csv phase.proc.json phase.rusage.json \
  /absolute/guest/download-benchmark /absolute/guest/download-benchmark \
  --scenario throughput --base-url GUEST_PHASE_URL --data-dir FRESH_DATA_DIR \
  --chapters 3 --pages 8 --concurrency 1 \
  --resource-report phase.rusage.json --allow-private-fixture-host
```

Collect scenario JSON, native resource JSON, guest environment, and the guest ELF
SHA-256. Stop the fixture through its authenticated `/__control/stop` endpoint
after both phases complete; the default timeout is fifteen minutes. Its final
`results.json` verifies the per-phase request count, bytes, and observed
concurrency. Guest scenario image hashes must also match `fixtures.json` before
reporting results.
Use the host address observed in that guest's active network configuration; the
address above belongs to the designated firmware VM's USB NAT at measurement time.

Results belong under the ignored `artifacts/performance/` directory. They should
be retained with their complete JSON and logs when reporting a performance number.
Do not compare runs from different machines or workloads. Loopback latency results
show the scheduling benefit under that latency; they do not predict a real
provider's throughput or Kindle battery use. Native Kindle validation is a
separate requirement.

## Separate UI cache observation

An earlier desktop debug trace identified synchronous cover decoding in reader
and menu handlers. Hidden library, catalog, and history projections caused ten
cover cache misses during one interaction. Individual repeat misses took about
24-97 ms, and the observed handler interval was 608-628 ms. Restricting projections
to the visible page and retaining a 64-entry cover cache keyed by `(id, path)`
reduced the same observed handler interval to about 5 ms on the same host and
input path. The original 500 ms hold threshold then fired in the retained raw
600 ms and 800 ms input traces.

These are handler timing observations from debug instrumentation. They are
separate from download throughput, end-to-end rendering latency, and native
Kindle performance. Retained evidence is in
`artifacts/performance/ui-cache-profile/`: `before-cli-profile.log`,
`before-raw-reader-hold.log`, `after-raw-hold-600.log`, and
`after-raw-hold-800.log`.

## Recorded result

The historical source snapshot recorded below, with source-manifest SHA-256
`e4ed331bbffa97e6b9ca53d1068fd6131a1513e6c8cc99811bcabb687ca58f95`,
includes guarded transfer of the validated staging
image memo to its canonical cache path. The image flag, staging fingerprint,
checksum, and published file identity must agree. Opaque archives and modified
staging files retain their separate validation paths. Root verification passed
190 tests, including the four added cache regressions.

### Host release

Rust 1.99.1, x86-64 Linux, `release`, `--no-default-features`, three trials per
mode, the default 120 ms image delay, and fresh data directories:

| Page concurrency | Wall time per trial (s) | Median wall time (s) | Peak HTTP images | Largest process RSS (KiB) |
| --- | --- | --- | --- | --- |
| 1 | 19.426, 18.222, 17.520 | 18.222 | 1 | 19,444 |
| 2 | 6.400, 6.523, 6.399 | 6.400 | 2 | 21,500 |

The ratio of median wall times is 2.85, or 64.9% lower wall time. This covers
network waiting, cache commits, and batched SQLite progress in the complete
worker path. Each trial made exactly 78 HTTP requests: 72 images and six page
lists. Image payload was 52,062,680 bytes and total response payload was
52,065,686 bytes. Every image matched its fixture hash, dimensions, and size.
All eleven acceptance checks passed, including completed cache replay with zero
HTTP requests. Maximum RSS in the table is the largest of the three process
high-water marks, approximately 19.0 and 21.0 MiB.

Evidence: `artifacts/performance/host-release-final-6x12-20261105/`, including
`results.json`, `summary.json`, build log, per-trial process/HTTP data, and image
manifests. Frozen binary SHA-256:
`fb4d7b7c953903f81eca928ad873cae00f0649be642e9910d8caa0e16f596823`.
Source manifest SHA-256:
`e4ed331bbffa97e6b9ca53d1068fd6131a1513e6c8cc99811bcabb687ca58f95`.

### ARM firmware guest

The independent `aidoku-pw6-5201` guest runs Kindle 5.20.1 with Linux
5.15.41-lab126, two emulated ARMv7 CPUs, approximately 957 MiB visible RAM, and
FUSE user storage. QEMU was launched with 2 GiB; this was not a 512 MiB machine.
One fresh 3-chapter, 8-page pair used the same `release`/`kindle` ELF and the same
external process sampler for both modes:

| Page concurrency | Child wall time (s) | Observed VmHWM (KiB) | Image GETs | Peak HTTP images |
| --- | --- | --- | --- | --- |
| 1 | 48.790 | 8,848 | 24 | 1 |
| 2 | 23.501 | 8,908 | 24 | 2 |

Both phases made 27 HTTP requests and received the same 16,926,831 image bytes
and 16,927,827 total payload bytes. All 24 image hashes, dimensions, and lengths,
completed jobs, persisted chapter manifests, child identity checks, and fixture
phase checks passed. There were 1,191 and 900 process samples respectively.
Actual median sample intervals were 21.45 and 21.34 ms; the largest intervals
were 1,087.75 and 323.72 ms. The CSV retains these scheduling gaps.

These are one instrumented guest pair's observations. The sampler and emulation
can affect timing: the earlier successful serial qualification without valid
RSS collection took 24.297 seconds, while the formal sampled serial timeline
included an approximately 10-second gap between image requests. Use the repeated
host release trials as the primary scheduling result. Guest absolute latency and
the single-pair ratio do not predict a physical Kindle's CPU, flash, battery, or
panel performance. Raw zero-valued `wait4` fields remain explicitly unavailable.

Evidence: `artifacts/performance/native-release-proc-final-3x8-20261105/` and
`artifacts/performance/native-results-20261104T195607Z/`, including
`verified-summary.json`, scenario/resource JSON, process CSV, environment,
commands, and fixture chronology. Benchmark ELF SHA-256:
`c8743644b638645cdf8b9e5d1d16b02b278a2eec756bdf3db13065a415c7cef3`.
Sampler ELF SHA-256:
`b7daad2e0548dc8bccb57d944e070c5e2a9f722c30e22f9613d98b7dda91783f`.
Sampler qualification and its source hash are retained in
`artifacts/performance/native-sampler-qualification/`.

### Intermediate evidence

The earlier dev-profile snapshot is retained in
`artifacts/performance/host-final-6x12-20261105/` as intermediate evidence. Its
three serial wall times were 28.607, 30.936, and 28.642 seconds; two-worker times
were 22.013, 25.570, and 25.714 seconds. The medians were 28.642 and 25.570 seconds
(10.7% lower wall time). All trials fetched the same 72 images and 52,062,680
image bytes, and all eleven acceptance checks passed. These dev-profile numbers
do not describe the final release implementation.
The initial ARM serial run and its zero-valued `wait4` resource report are
retained in `artifacts/performance/native-wait4-unavailable/` and
`artifacts/performance/native-wait4-unavailable-fixture/`. They are not combined
with the formal sampled pair.
