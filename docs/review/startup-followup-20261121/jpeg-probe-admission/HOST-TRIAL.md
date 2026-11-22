# Metadata admission and the first Library software draw

This comparison uses **one Linux x86-64 libtest executable** for both arms. It exercises Aidoku's application/controller and Slint software renderer, not a deployed Kindle executable. A normal desktop platform has no qualified native JPEG device, so measuring an unchanged desktop package would not exercise the old blocking operation.

## Result on `test-env`, 2026-11-21

| Host viewport | Control frame, median | Candidate frame, median | Paired time removed, median (range) | Candidate earlier |
| --- | ---: | ---: | ---: | ---: |
| Scribe, 930 × 1240 | 81.29 ms | 67.66 ms | 13.83 ms (12.60–16.10) | 8/8 |
| Paperwhite 6, 632 × 840 | 70.63 ms | 56.08 ms | 14.63 ms (12.79–20.69) | 8/8 |

Both execution orders favored the candidate. The control's synchronous fixture exchange itself took a median 14.28 ms and 14.18 ms, respectively. All 32 measured launches and four warmups passed: the first populated frame matched exactly within each viewport, its sort/filter control responded, and the candidate did not run the helper until the relevant Settings section opened. Both arms then had exactly one completed metadata request. This establishes **admission and its host-fixture cost**, not a normal-card or hardware speedup. The full [per-arm observations and order-stratified summary](host-result.json) are replayable with `host_trial.py check`.

## Comparison

- **Control:** call the capability operation synchronously next to `Platform::new`, before creating the application window or attaching the controller. **Candidate:** do not call it there. Both arms then take the same application path. A test-only platform hook starts in `not_probed` and caches the first result, as the native process does.
- The hook runs the production `probe_helper` / operation-zero `exchange`: a bounded caller, a separate I/O thread, a new child process, real stdin/stdout pipes, request/response parsing and child reaping. The Python fixture accepts exactly the empty metadata packet, acknowledges it by creating one exclusive receipt, then returns success. It never sleeps, opens a network connection, touches a device, or supplies image bytes. Its interpreter startup and filesystem/IPC cost are **not** an estimate of the actual native helper, including its hardware/driver qualification.
- Each arm starts a new process with a private temporary home, app directory and receipt. It seeds the same six local books, six chapters, six local covers and disabled acceleration. Both arms preload the same nine bundled font faces and allocate the same output buffer **before** timing. The existing first-frame priority gate keeps cover work queued. Both start with an ordinary, non-demo, non-resume offline Library, six projected grid rows and seven pending image slots, all placeholders.
- The origin is the process's `Instant` immediately before constructing `Platform`. The endpoint is the return of `SoftwareRenderer::render` for the first complete Library scene. The same process also records platform-ready and attachment checkpoints. The timed path includes `App::attach` (real database-backed preparation, route/view attachment and readiness stages), window creation and software rasterization. It does **not** include executable launch, bootstrap font preparation, native frame submission, panel settling, or a native card release. The app uses the synchronous convenience attachment here rather than the native concurrent bootstrap choreography.
- Each accepted sample must draw the entire expected viewport and contain actual ink, not just a dirty flag. Only **after** this endpoint does a normal Slint pointer press/release open the sort/filter panel. The app closes it, visits ordinary Settings without waking candidate media, then visits Hardware Acceleration twice. Both arms must finish with one successful helper call total, acceleration still off and zero hardware decode attempts. This demonstrates software interaction and later admission, not native input latency.
- One warmup pair per viewport is excluded. Eight measured pairs per viewport alternate arm order (four control-first and four candidate-first), with profile order changing each pair. Every cross-arm RGB buffer and each source profile's first warmup buffer must match byte for byte, without a checksum. The driver stops on any missing case, protocol/route/raster mismatch or executable replacement; it neither retries individual samples nor drops slow observations. Only sanitized per-arm observations are kept; the private homes, databases, generated covers and raw buffers are discarded.

## Reproduction

Inside the designated `test-env` Linux toolchain, use the same source tree for the binary and review scripts. Keep its build output directory (including Cargo's expanded fonts) accessible to the libtest. No X server, host input device or physical Kindle is needed.

```sh
cargo test --offline --locked --no-default-features --features kindle --lib --no-run -j6
trial=docs/review/startup-followup-20261121/jpeg-probe-admission
python3 -B "$trial/host_trial.py" run /absolute/path/to/the/emitted/libtest /tmp/new-host-result.json
python3 -B "$trial/host_trial.py" check /tmp/new-host-result.json
```

The driver will not overwrite an existing report. Times are in microseconds; paired differences always mean control minus candidate. The median of paired differences is not necessarily the difference between the two unpaired medians. The `check` command validates the fixed cohort and recalculates the published summary from its original per-arm receipts; it does not reproduce a hardware measurement or re-render saved screenshots. Run a new full cohort to repeat the byte comparisons. Use [the independent archived probe replay](historical.json) for observations from an actual Scribe, not for this modification's effect on normal launch time.
