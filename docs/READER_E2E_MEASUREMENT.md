# Reader end-to-end software measurement

This procedure separates first-image and page-action measurements from the
earlier isolated grayscale, resize, cache-codec and scene-buffer experiments.
Run all execution and analysis on the designated `ssh test-env` worker. The
Windows workstation is used for editing, transfer and reading retained records.

## Boundaries

The opt-in application argument is `--performance-trace <new-file.json>`. It
keeps a bounded in-memory event sequence and flushes once after the timed
application runtime has stopped. Normal operation without this option does not
write a trace. Every event uses the monotonic epoch captured at the first Rust
`main` entry. This epoch excludes the OS loader and the launcher's preflight.

| Metric | Start | End | Interpretation |
| --- | --- | --- | --- |
| First UI image | Rust `main` entry | First startup `ui_image_ready` | Application image acceptance; not software scanout. |
| First native write | Rust `main` entry | Matching `framebuffer_written` | Actual framebuffer bytes written for the accepted startup token. |
| First native submission | Rust `main` entry | Matching `send_accepted` | The driver accepted the submission following that write. |
| Reader action | `action_received` at `app_intent_received` | Matching UI/write/submission event | Includes work before the image request, such as a chapter manifest wait. |
| Image pipeline | `request_created` | Matching UI/write/submission event | A narrower pipeline interval, reported separately from the action interval. |
| Worker stages | Each stage's own start | Its stage-end event | Includes the actual fetch, render or preparation call; repeated spread-page stages are retained. |

Action events identify `scripted_intent`, `scripted_pointer` or `app_dispatch`.
They do not certify physical touch input. Script scheduling delay, automatic-exit
delay, screenshot timing and supervisor wall time are excluded from the latency
metrics. Stage durations are not necessarily an additive partition of the action
interval. A render's `cache_state` comes from that render result rather than a
global-counter difference.

Neither a framebuffer write nor a successful SEND is optical panel completion.
The reviewed kernel can return success after an internal wait timeout. Visible
latency, residual images and battery consumption require separate observations.

## Controlled inputs and fixture preparation

Use the instrumented `ffb3417` baseline and the instrumented candidate. The
baseline must retain its original algorithms, cache behavior, RGB scene and
constant update marker. Add only the reviewed observation patch. In particular,
do not transplant the marker correction or grayscale/resize changes to make the
baseline look like the candidate. A repeated baseline marker is valid; causal
association uses the request token, generations and observer frame sequence.

Retain the baseline observation patch, source manifests, binary identities and
the integration review. Shared observation modules must be byte-identical across
the two source trees. Hash equality of an observer does not independently prove
that the rest of the baseline patch contains no algorithm change.

Seed fixtures before collecting measured processes. Each workload keeps its own
fixed active path, shared by both implementations. Restore mutable state before
each process while retaining the original source files in place. Local page keys
include source metadata identity: replacing an equal-byte original or calling
copy2/chmod/utime on it can invalidate the processed cache through inode/ctime.
The fixture helper records actual SQLite page-source paths and verifies SHA,
device, inode, length, mtime and ctime before and after each restoration. Identical
files are not rewritten. Warm PNGs are generated normally against those final
source identities; cold cases remove only owned processed derivatives.

This also preserves SQLite's stored absolute image paths without interpreting an
arbitrary copied database as an equivalent fixture. The harness hashes all declared fixture files
after preparation and requires equal bytes across both implementations and all
iterations of a workload. Preparation, source inventories and file copies are
outside the trace interval. Inventory reads warm the OS page cache; the procedure
does not claim cold physical storage.

Every adjacent source page and vertical window must produce distinguishable
visible pixels. A nominal page change with identical output can legitimately
produce no native dirty region and must not be used to fabricate a completion.
Check fixture pixel identities in the preparation receipt.

Suggested workloads are:

- Warm processed-cache startup: at least 21 fresh processes per implementation.
- Managed processed-cache miss: at least 21 fresh processes per implementation,
  preserving originals while removing the derivative before each trial.
- Cached page turns: at least 21 completed actions in a retained process.
- Chapter boundary: an actual next-chapter action with preseeded manifests and
  distinguishable images, with its pre-request wait inside the action interval.
- Long vertical image: enough nonuniform image height for at least 21 distinct
  viewport actions; it must exercise the intended streaming or window path.
- Cancellation, failed source and retained lifecycle cases: explicit regression
  expectations, with unsuccessful requests excluded from successful latencies
  and retained in the report.

Actual render-cache states can be required with an expectation such as
`"required_cache_states": {"cold": 1}` or `{"memory": 21}`. Fixture names and
cache counters alone do not prove which path completed.

## Host runner

`scripts/benchmark-reader-e2e.py run --plan <plan.json> --output <new-directory>`
runs desktop processes serially. It alternates baseline/candidate order on odd
iterations and candidate/baseline on even iterations. No timed process includes
a capture or a Kindle operation. Do not run builds, other UI suites, bulk
transfers or other timing experiments concurrently.

Each process owns a new Linux session and process group. On timeout or
interruption the supervisor sends TERM, observes the owned group, and escalates
to KILL for remaining running members even if the original wrapper already
exited. It confirms group exit before continuing. Zombies are retained as
non-running observations. Failure to establish the stopped boundary aborts the
plan; an unexpectedly retained group after a normal wrapper exit also makes the
trial unsuccessful. Consumers of the process helper require `succeeded: true`,
not merely a zero wrapper `exit_code`.

The version-1 plan has:

- `scope: "desktop"` and global `samples: 21`.
- `control.baseline_commit` equal to the full `ffb3417` commit,
  `control.baseline_observation_patch` naming the retained patch, and
  `control.baseline_review_receipt` naming the review bound to that patch, and
  `control.shared_observation_files` containing `name`, `baseline` and
  `candidate` source paths.
- `implementations.baseline.binary` and `implementations.candidate.binary`.
- `workloads`, each with `id`, optional `samples`, an argv-array `command`,
  a list of argv arrays in `prepare`, `fixtures`, `expectation`, `cwd` and
  optional `timeout_seconds`.

Command placeholders are `{binary}`, `{trace}`, `{trial}`, `{iteration}` and
`{preparation_binary}`. Commands use argv arrays rather than shell fragments.
Preparation must be identical for both labels; it can use the common
`{preparation_binary}` but cannot select `{binary}`. A measured command must
include `--offline`, `--data-dir` and `--performance-trace {trace}` and exclude
`--capture` and `--kindle`. A wrapper such as `xvfb-run -a` is permitted.

A long sequence can set `samples: 1` while requiring 21 completed actions. Its
statistics report one independent process and 21 action observations. The global
startup count remains 21. Timed runs must use an existing seeded data root;
do not include first-time demonstration/import creation in a startup sample.

## Native collection and offline analysis

Only the exclusive physical-device operator may acquire the shared lease or
execute a native trial. The generic runner does not connect to a Kindle.
That operator retains exact executable identities, prepared-state identities,
bounded process outcomes, trace hashes, restoration and lease-release receipts.

Prepare and build all inputs before acquiring the lease. Acquire it only when
the next actual device command is ready, and hold it only for the continuous
device-operation batch and necessary bounded restoration. Release immediately
after those operations with owner/token checks. Host analysis, report writing,
tool debugging and waits for other tasks run outside the lease. If an idle
manual step is not ready, end the owned session and release before waiting.
Later batches use fresh lease identities and tokens. The lease is not a
reservation for a future build, measurement or response.

A long native cohort can be split into shorter continuous device batches.
Declare the full workload/count map and global trial schedule before starting.
Select each batch from that schedule without restarting iteration numbering or
AB/BA order. Individual fragments remain incomplete comparisons until the full
declared matrix is collected and independently qualified.

The resumed native cohort declares at most three read-only status observations
within a single ten-second budget, including the first observation, with one
second between observations. Re-observation is allowed only after a successful
execute receipt and when the independent status getter's sole failed native
check is `power_active`. App exit, generation, binary, fixture and trace
identities must remain identical. Every original output and transport receipt
is retained; one actual raw status must pass every original gate. No wake,
repair, fixture reset or App rerun is inserted between observations. Transport
timeout, output truncation, changed identity or execute failure stops the batch.
This policy is declared before sampling and does not retrospectively qualify
an earlier rejected cohort. Status observations occur after the timed App exits
and do not change Rust-main or action-to-output timing boundaries.

If source identities drift between completed workloads, preserve the original
receipt and whole completed workload. Establish a separately named identity
epoch only after verifying original content and actual database source paths;
keep the full stat/SHA stability checks and leave original images untouched.
Regenerate and qualify caches through normal application behavior as needed.
Freeze new named fixtures and declare their actual inputs before measurement.
A joint report must retain each run's original collection/ordinal, source epoch,
source receipt and helper identity, and provide independently qualified epoch
views. It must not relabel an interrupted original master as completed.

`scripts/benchmark-reader-e2e.py analyze --manifest <runs.json> --output <analysis.json>`
qualifies already collected JSON files. A manifest contains `schema_version: 1`,
`expected_pairs` (an integer or per-workload map), the `control` source paths
above, `binaries` with baseline/candidate retained executable `path`, `sha256`
and `after_sha256` identities, and `runs`.
Each run provides `implementation`, `workload`, `iteration`, `phase` (`measure`
or `qualification`), `trace`, explicit `process_exit_code`, `fixture_sha256`
and `expectation`. Native expectations use `scope: "native"`.

Each binary receipt has `sha256` before the run and matching `after_sha256`.
The baseline review JSON has `schema_version: 1`, the full `baseline_commit`,
`baseline_observation_patch_sha256`, `review_passed: true`, and true checks named
`original_algorithms_preserved`, `same_timing_boundaries` and
`candidate_optimizations_excluded`. The analyzer hashes the actual patch and
requires that exact binding; a free-standing `verified: true` flag is insufficient.

Useful expectation fields are `require_startup`, `minimum_ui_requests`,
`minimum_native_requests`, `required_successful_actions`,
`required_cache_states`, `allowed_outcomes` and `required_outcomes`.
An expected failure/cancellation regression may allow and require its terminal
outcome; that outcome still does not become a successful latency observation.
Every eligible Reader action remains in the denominator even if it never
creates an image request. Such an action must complete or have an explicit
allowed failure/cancellation; it cannot silently disappear from the sample.
`eligible_action_kinds`, `allowed_action_outcomes` and
`required_action_outcomes` describe intentional action-level regression paths.
Menu/settings operations are not page turns unless explicitly selected.
The fixed `open` kind is reserved for an actual Reader entry and is eligible by
default; ordinary non-Reader navigation uses `other`. A first accepted UI image
cannot qualify as startup merely because its request omitted `action_id`: if a
Reader `open` action precedes it in the event sequence, the missing association
is rejected. Qualified scripted Reader entries instead require the correct
action association with `require_startup: false`. Event order resolves equal
microsecond timestamps without reclassifying an earlier completed startup.

The analyzer rejects truncated/unflushed/lossy traces, backward clocks, missing
work or terminal boundaries, stale request/generation/frame associations,
duplicate completions, late native success after a terminal unavailable event,
and repeated trace/process identities masquerading as independent samples.
Pair qualification also requires matching iterations, scope, expectations,
required metrics and observation counts. Missing source, binary or identical-
fixture receipts withhold comparison qualification and ratios. Invalid trials
are retained; a partial set of passing trials is not a qualified comparison.
The runner records every requested workload/count before the first process.
The measured workload set must equal the `expected_pairs` map's key set;
missing or unexpected workloads withhold all comparison ratios, including ratios
for otherwise complete pairs, and are named in the analysis summary.

Reports give the observation count, independent process count, median,
nearest-rank p95, minimum and maximum. Fewer than 21 observations are explicitly
marked as a small sample. Multiple actions in a process share state and must not
be described as independent cold starts. Qualification/warmup processes remain
in the raw record and do not enter measured distributions.

The `analyze` command can succeed for a valid standalone candidate trace without
a controlled baseline comparison. Callers must inspect `comparison_qualified`
and require it to be exactly `true` before reporting a controlled comparison;
CLI success alone is insufficient. Neither a descriptive distribution nor a
passing regression trace grants a before/after speedup claim.

The stdlib counterexample suite is run remotely with:

```powershell
ssh test-env 'cd /path/to/isolated/source && python3 -m unittest discover -s scripts/tests -p test_reader_e2e.py -v'
```

Execution results and actual device sample counts belong in the dated delivery
record. This procedure alone does not claim that any experiment has run.
