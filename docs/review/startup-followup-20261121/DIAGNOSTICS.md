# Where the next startup experiment should spend its time

The host checks and offline replay run on `test-env`. The cold observations below come from the **original** closed cold confirmation, not new device visits. R3 and the frozen studies remain unchanged. The separate interrupted native-tile repeat ended by [authorized cleanup](../../../HANDOFF.md#shared-kindle-cleanup-complete-ordinary-acquisition-reopened), not verified restoration of its old snapshot. No device visit or installation was needed for the offline replay.

## Warm opens: separate observer wait, portrait and display takeover

The primary endpoint is **normal stock Library tile release to the first rendered Library screen**, including layout, entries (or the correct empty state) and navigation. Cover placeholders are allowed; complete covers are secondary. No input-readiness fence, successful tap, video or manual test is required. Use same-visit device-clock render logs, not window creation, generic Ready or the boot shell. The [render-only protocol](rendered-library/README.md) distinguishes application frame submission from a sampled framebuffer upper bound on the unmodified installation. Keep historical timings under their original names; do not combine real card releases and staged requests.

The [normal tile study](../startup-native-20261121/README.md#first-complete-batch-visual-check-failed) bracketed launcher birth to application birth at 825–845 ms on R3 and 830–850 ms on the candidate. A different [controlled R3 indexed-request replay](../startup-entry-timing-20261119/r3-replay.json) placed the *outer* portrait operation at 280 ms and display takeover at 100 ms on warm thumbnails. These are elapsed intervals from different visits, not amounts that can be removed or subtracted from the tile result. None separates time spent waiting on the observer from other generation setup.

The launcher under test makes one narrow change: `take_display` reads the window manager's state and birth tick from the same [`/proc/PID/stat` record](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html), instead of reading it twice through command substitutions. For a live manager this removes one procfs read and two shell command substitutions. It still skips an already stopped manager, records only a successful stop, and rechecks the recorded birth tick before resuming the process. This does not optimize the status-bar service call or establish a wall-time win.

Use the [single-visit operator](launcher-visit/README.md) to collect the next stage breakdown. It launches the default portrait path with a private six-book offline Library and the unchanged post-SEND ARM pair. It does not tap a stock tile or change an installed program. One such run can locate a long stage, but cannot estimate a paired daily-open saving.

For a **separate diagnostic visit** with a staged launcher, set `AIDOKU_LAUNCHER_DIAGNOSTICS=launcher-v1` in its environment, and direct stderr to a new private regular file for that visit. No diagnostic path is accepted from the environment. The Library waiter passes the option to its foreground worker; the worker consumes it before starting the GUI. Unset the option for primary A/B runs. Do **not** export it in the stock shell, modify the native entry or its environment allowlist, or reuse a frozen plan. An installed R3 launcher does not emit this protocol. A missing trace is not a zero.

| Marker interval | Interpretation |
| --- | --- |
| `worker_enter` → `observer_start` | Foreground shell entry after definitions, through initial admission and FIFO setup; not kernel process birth. |
| `observer_start` → `observer_wait_begin` | Observer is started asynchronously while the launcher prepares the generation. Do not count this again as blocked time. |
| `observer_wait_begin` → `observer_ready` | Remaining wait for the checked observer and its owned source process. This is not a timestamp from inside the power service. |
| `portrait_begin` → `portrait_ready` | Bounded native portrait transaction, publication check and any required sleep-capability fallback. |
| `display_begin` → `display_ready` | Time inside `take_display`, including the stock status-bar call if needed. Return does not prove a panel refresh. |
| `display_ready` → `app_spawn_begin` → `app_spawn_returned` | Fork/schedule the runner and start the GUI command. The last mark follows acquisition of `$!`; it is not Rust entry or input readiness. |

After the operator closes the diagnostic batch, analyze its private log on `test-env`:

```sh
review=docs/review/startup-followup-20261121
python3 -B "$review/launcher_stages.py" "$VISIT_LOG"
```

The clock is the first field of `/proc/uptime`. [Linux prints it to hundredths of a second](https://github.com/torvalds/linux/blob/master/fs/proc/uptime.c), so a difference of two readings has uncertainty of up to one 10 ms bin on either side. Equal stamps mean 0–10 ms, not zero work. The tool rejects missing, duplicate, reordered or concatenated visits. It prints bounds rather than absolute timestamps. The overall observer span includes the overlapping generation work; do not sum it with the adjacent intervals. Tracing itself has a cost: do not use a traced run for speed qualification.

## Empty thumbnail cache: reuse the existing confirmation

Use the closed ten-pair **staged cold confirmation** indexed on the worker, not a native-tile batch, warm experiment or synthetic timing fixture. With its original `plan.json`, `operations.json` and `output.tar.gz` in place, run from the new checkout in a worker Python environment with Pillow:

```sh
python3 -B "$review/replay_cold_costs.py" "$CLOSED_CONFIRMATION_ATTEMPT"
```

The replay rechecks the original cohort verdict, including pixels and release, and requires six nonoverlapping misses per timed candidate visit. It reports per-job intervals and ranks the median **per-visit sums** for the ten candidates. Warmups and pictures are excluded from the cost estimates. A missing or ambiguous phase suppresses the aggregate ranking; do not substitute another visit. No private pictures, book identifiers, or raw logs belong in a report.

Use the ranking to select one intervention. `decoder_start_to_source_decoded` is distinct from grayscale and resize. `start_to_original_read` mixes lookup, fingerprinting and reading; `quantized_to_cold_decode_validated` includes final source verification; `cold_decode_validated_to_derivative_prepared` includes derivative persistence. These are elapsed intervals for six synthetic JPEGs, not CPU profiles or a measurement of streaming PNGs. The R3 control lacks the fine CPU marks, so the ranking cannot apportion the previously confirmed 681 ms saving. In particular, do not remove the final check of the named file, or allocate an encoded snapshot on every warm hit, just because a mixed interval is large.

### Observed cost ranking

The replay accepted the original ten-pair confirmation and attributed all 60 timed candidate jobs. Each visit had six serial misses; all reported NEON availability. The median of the six-job workload per visit was **2,471.378 ms** (range **2,443.825–2,635.700 ms**). The table ranks medians of the per-visit sums, not medians of individual thumbnails.

| Interval over six candidate jobs | Median wall time (ms) |
| --- | ---: |
| Decoder start to decoded source | 948.011 |
| Validated source to prepared derivative, including persistence | 580.852 |
| Job start to original bytes read, including lookup and fingerprint | 285.757 |
| Grayscale ready to resize finished | 232.451 |
| Quantized pixels to final source validation | 147.471 |
| Combined validation start to verified source bytes | 109.167 |
| Prepared derivative to render ready | 79.403 |
| Decoded source to grayscale ready | 38.698 |
| Resize finished to quantized pixels | 33.772 |
| Render ready to job finished | 14.485 |
| Verified source bytes to decoder start | 0.177 |
| Original bytes read to combined validation start | 0.112 |

The sums exclude inter-job gaps, Library presentation and launch overhead. The stage medians need not add to the workload median. This is not a new end-to-end A/B or a basis for estimating daily icon latency.

The [ten paired workload totals](cold-job-replay.json) retain the original order in microseconds, without images or per-book identifiers.

A second replay with `--paired-common` uses only the boundaries present in **both** binaries, six serial misses per visit and ten matched pairs. The median within-pair saving in the sum of those six job durations was **647.455 ms** (10/10, range 490.109–934.382 ms). The largest common window, combined validation start to validated decoded cover, was **581.330 ms faster** (10/10); this includes hashing, decoding, grayscale, resize, quantization and final named-source verification. It does **not** isolate NEON or the removed intermediate source read. The job-start-to-combined window was 48.501 ms faster (9/10), validated-to-prepared 7.906 ms faster (7/10), prepared-to-render-ready 1.852 ms faster (8/10), and the final handoff 0.242 ms slower (one candidate win). These per-interval medians do not sum to the paired total. The previously confirmed **680.849 ms** is the separate end-to-end cover submission result.

### First decoder hypothesis

`decode_bounded_image` currently constructs one image reader for `into_dimensions()` and another for `decode()`. In the locked `image` 0.25.10 JPEG implementation, constructing a decoder buffers the compressed input and parses its header. The hypothesis is **one redundant input copy and header pass**, not a second full pixel decode. The six retained synthetic JPEGs total 8,961,303 encoded bytes. Test reusing the same decoder while keeping the dimensions, pixel cap, output reservation, allocation limits and RGB output unchanged; do not replace the JPEG codec or force luminance output.

Before host timing, set the screen to ten alternating AB/BA pairs after independent warmups, with identical resident inputs. Compare dimensions, color type and every decoded byte outside the timed interval. Report all paired six-image differences. Require exact parity, at least nine faster pairs and at least 5% median within-pair relative saving to prioritize a device trial of this intervention. A host pass only selects work; it neither changes the production path nor predicts Kindle time. An inconclusive screen leaves the decoder unchanged.

The [isolated probe](jpeg-header-probe/src/main.rs) reads the six original **synthetic** JPEG members from the cold fixture, not user books and not regenerated pictures. Give them private ordinal filenames, in the order of the sizes recorded in `plan.json`. In a disposable `test-env` workspace, seed the probe's standalone lockfile from the product's `Cargo.lock`; the first offline Cargo invocation prunes unused packages. Use six jobs under the shared build lock. Do not write into the original cold confirmation.

```sh
cd /work/source/docs/review/startup-followup-20261121/jpeg-header-probe
cp /work/source/Cargo.lock Cargo.lock
cargo test -j6 --release --offline
cargo build -j6 --release --locked --offline
cargo zigbuild -j6 --release --locked --offline --target armv7-unknown-linux-musleabihf
qemu-arm -cpu cortex-a9 /work/target/armv7-unknown-linux-musleabihf/release/jpeg-header-probe --parity-only /work/synthetic-jpeg/0[1-6].jpg
```

The timing executable is `/work/target/release/jpeg-header-probe` with the same six paths and no `--parity-only` option. Input I/O and pixel comparisons are outside timing. Only one decoded output is live during the timed decode loop. QEMU runs the untimed comparison only.

**Result: do not promote decoder reuse.** All three probe unit tests passed and the nine resolved registry package versions matched the product lock. Dimensions, color type and every decoded byte matched across both arms on x86_64 before and after timing, and on the ARMv7 release binary under Cortex-A9 QEMU. The [ten host pairs](jpeg-header-screen.json) ran with one vCPU affinity under the shared worker lock. The separate six-image medians were 65.874 ms (control) and 65.753 ms (reuse). The paired median saving was **0.483 ms / 0.734%** with **8/10** wins. Neither threshold was met. This comparison does not imply the same relative saving on the Scribe. The production decoder was not changed and no hardware speed trial was opened.

### Derivative write decision

**Keep the default adaptive PNG filter.** The [two-shelf persistence trial](DERIVATIVE.md#decision-keep-adaptive-filtering) split the host write path into reservation, encoding, full validation, durable commit and verified post-commit lookup. Turning off row filtering made the original six-cover shelf **33.924% slower** at the paired median (0/10 faster) and the structured shelf **6.354% faster** (9/10, below the 10% floor). Both stored fewer bytes and reopened faster without any pixel or metadata changes. Neither shelf qualified. These are isolated Linux persistence measurements, not a split of the device's **580.852 ms validated-to-prepared window** or a measurement of daily opens. The next cold diagnostic target is the durable commit, not another speculative PNG setting; leave file sync, directory sync, checksum and the index transaction intact.

## Verification order

The single-visit package was sealed on `test-env` with 11 regular members (39,589,709 compressed bytes). All **67** operator, evidence and fixture tests passed in the Ubuntu runner. The frozen launcher separately emitted exactly ten ordered stages and one ordinary-exit marker with a successful fake app, and no such exit marker with a failing fake app. Historical closed Scribe application/state files passed the new evidence checks when paired with synthetic launcher markers; neither exercise produced a new Scribe timing. At 09:39 Asia/Shanghai on November 21 the host-only preflight verified the package and configured host-key pin, refused the missing original close and occupied lease, and left the new plan unused. No device connection was opened.

Worker results for the current launcher: `sh -n` passed; **96 launcher lifecycle tests**, **16 native portrait supervisor tests**, **23 offline reporting tests** and the separate staged managed-entry fixture passed. The ten markers emitted by the fake Library worker also passed the real single-visit log parser and the fixture's stock restoration checks. The regression against the unmodified post-SEND launcher observed **2 → 1** stat reads for the live fake window manager before GUI admission; a pre-stopped peer required one read on both arms and was not resumed. None of these host fixtures measures Kindle launcher latency.

Run the launcher tests in the worker's Ubuntu userspace. An extra run directly on the CentOS host reported two observer fixture failures; both also failed on the unmodified launcher there. Both fixture logs showed `exec: perl: not found` in the test-only `usleep` replacement. The Ubuntu runner supplies Perl: both isolated observer tests and the full 96-test rerun passed there without changing either test or launcher to hide the failure.

Both published pair tables were compared with the worker output. The optional `cargo fmt --check` for the standalone probe was unavailable because this shared Rust installation has no `rustfmt` component; the toolchain was not modified.

1. Use a fresh project workspace under `/opt/kindle-toolchain/ubuntu-run` on `ssh test-env`. From its source root, run `sh -n packaging/kindle/launch.sh`, `python3 -B packaging/tests/launcher_test.py` and `python3 -B -m unittest discover -s docs/review/startup-followup-20261121 -p 'test_*.py'`. These need no physical lease. Run the cold replay against the retained private attempt separately; a fixture does not establish a hardware cost.
2. The [administrative cleanup receipt](../../../HANDOFF.md#shared-kindle-cleanup-complete-ordinary-acquisition-reopened) reopened ordinary acquisition without certifying the old settings. Prepare and freeze new tools and cases on the worker first; check the current shared lease at actual dispatch. Never rerun the old close/repair or reuse a previous lease.
3. Use a fresh single-operator lease for a bounded diagnostic batch. Keep normal exit, stock/light checks and peer protections. A software input fence can remain a separate lifecycle check in existing diagnostics; it is not the render endpoint. Release before analysis or tool repair. If portrait rather than display takeover dominates, investigate its child work and supervision before extending the window-manager change.
4. To evaluate the one-snapshot change itself, compare identical app/helper bytes with and without **only** the takeover change and the same trace-disabled launcher hooks. Keep real tile and staged starts separate. Six balanced pairs must show at least 100 ms median primary saving, five wins and no more than 50 ms regression on the other endpoint before ten independent confirmation pairs. Pictures and lifecycle gates still apply; 300 ms warm and 1 s cold remain practical targets, not claims.
