# Synthetic import and first appearance of offscreen books

This experiment measures the cost of doing thumbnail work during the import
session as well as the benefit when those books next appear. It covers local
archive import and a later alternative shelf order, not normal warm startup,
online loading, system boot, or energy consumption. `fixture.py` creates only
original grayscale patterns: 20 two-page CBZ archives with synthetic titles,
fixed ZIP metadata, canonical checksums, and natural page ordering. Four covers
are 1024 by 1536; all other pages are 96 by 144. The manifest SHA-256 is
`4bce2ff13a0fab6e04bceb73b1071b86681f0d36fb4ee6eb2d58c91c9039a8a0`.

Each arm starts with a new, empty private data directory. Both use the same
candidate ARM executable and shipping launcher. Only the private
`import_thumbnail=off|idle` option changes. The first indexed file-URI launch
imports the directory with the application's `--import` adapter. In the native
Scribe layout, the current book is S00 and the visible rows are S19 through S05.
Only S01 through S04 can use the offscreen optimization. The source pages,
first-page cover binding, SQLite rows, in-band render key and decoded pixels of
each persisted thumbnail are checked independently of timing markers.

Between the two processes, the fixture sets the supported title-ascending sort
in its private preferences and inserts the same synthetic reading progress for
S00 in both arms. This models a future visit that brings the four other books
onto the first page without drawing that page in the import process. No real
user database is read for fixture construction or written, no covers are deleted
to simulate a miss, and the second process gets no `--import` flag. Its first
visible set is S00 through S14. An unoptimized control must generate exactly
four missing derivatives and reuse eleven; the intervention must reuse all
fifteen. There are sixteen slots including the duplicate current-book tile.

The actual indexed document, installed shared runtime, and catalog registration
are unchanged. Both sides use the same temporary guarded launcher bridge bound
to this run. The parent driver enforces one lease owner, bounded admission,
touchscreen and native status restoration, app identity, catalog notification,
and exact before/after checks. The user bookstore, assets, and binaries are
never replaced by test fixtures. Each batch removes its own bridge and staging
before release. Raw screenshots, process information, timing traces and the
identity of actual device entries stay in the private worker directory.

### Prespecified decisions

- Screen with six pairs (three AB/three BA). Confirm independently with ten
  pairs (five AB/five BA). Warmups and independent screenshots do not count.
  The paired median to all covers on *reopen* must improve by at least 100 ms,
  at least five/six or nine/ten pairs must improve, and the first usable screen
  must not regress by more than 50 ms. A one-pair pilot cannot establish this.
- For a default-on claim, both import start-to-library publication and request
  to the nonempty, fully drawn import shelf must not regress by more than 50 ms.
  The two user-visible waits together must improve by at least 100 ms. The two
  active intervals including the later of the last idle job and import-screen
  acceptance must not regress, and each separately measured exit median must
  not regress by more than 100 ms. These are engineering thresholds for this
  fixture. A complete independent physical pixel qualification compares both
  processes' Library content and right-hand status indicators. A passing result
  still requires the separate ordinary workload, installed-default checks and
  exact production artifact checks before daily deployment.
- Do not add worker durations to a wall interval that already contains them.
  Report total worker wall time, elapsed wait after the last visible frame,
  imported/final cache bytes, book and page identities, and paired sums of the
  two nonoverlapping user-visible waits. Neither thread CPU nor battery energy
  is measured. A faster reopen with slower overall delivery may justify an
  opt-in; it does not establish a net speedup.

### Worker commands

Only run on test-env. `prepare` and test commands use no physical lease.

```sh
python3 -I /root/aidoku-all-bench-20261119/import_probe/tools/materialize.py
python3 -I /root/aidoku-all-bench-20261119/import_probe/tools/test_import.py -v
python3 -I /root/aidoku-all-bench-20261119/import_probe/tools/run_bench.py prepare \
  --trial import --mode pilot --name imp --primary covers \
  --baseline-app "$CANDIDATE_ELF" --candidate-app "$CANDIDATE_ELF" \
  --baseline-option import_thumbnail=off --candidate-option import_thumbnail=idle
```

Only the designated physical operator calls the frozen plan's `runner.py run
.../plan.json`. Never take a device lease for compilation or host analysis.
For `screen`, use rounds 1 through 6 in a single batch or two fixed groups of
three. For `confirm`, use rounds 1 through 10 in one batch or two fixed groups
of five. A separate `qualify` batch captures matched device pixels. The
analysis command returns nonzero for invalid evidence, never omits a failed
planned pair, and never qualifies a pilot as a significant result:

```sh
python3 -I /root/aidoku-all-bench-20261119/import_probe/tools/analyze_import.py \
  --phase confirm --batch "$FIRST_FIVE" --batch "$SECOND_FIVE" \
  --qualification-batch "$QUALIFICATION" --output "$REPORT"
```
