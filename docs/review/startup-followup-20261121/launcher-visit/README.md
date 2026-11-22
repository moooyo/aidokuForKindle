# One staged launcher diagnostic on Scribe

**One foreground launch, no install and no stock tile press.** This visit times admission of the power observer, portrait capture, display takeover and the GUI spawn in the instrumented launcher. The app and portrait helper are the retained post-SEND pair. The six-book offline synthetic library has no thumbnail cache. The first submitted full frame and its input fence, the six visible covers, normal app exit and restoration are required. The visit does not measure a daily warm open, compare two versions or qualify a rollout. Do not repeat a failed visit or reuse any historical device plan.

The private data has no `startup-options` and the operator sets no portrait override. This measures the default shell-supervised portrait path, not the optional native supervisor. The app requests an ordinary exit after eight seconds of active startup-ready time; eight seconds is not a process-start deadline or a guarantee that every cover has finished.

## Prepare off the device

Use a private `test-env` workspace. Put the operator scripts in `review/launcher-visit/` and `launcher_stages.py` in `review/`. Use the worker's existing lease module and pinned SSH configuration, not a copied credential. `inputs/` contains the candidate launcher, the retained ARM app and portrait helper, the current three-project protection guard, the retained desktop simulator and the six original synthetic JPEGs. Use the guard that protects BiliComics' installed lowercase entry, **not** the historical guard that expects BiliComics to be absent.

Separately populate `review/approved/` from the reviewed checkout (`launcher.sh`), the accepted three-project guard (`guard.sh`), and the retained post-SEND programs (`app/aidoku`, `app/aidoku-startup-portrait`). Put the two original worker tool modules under `review/approved/tools/`. Do not populate this reference directory by copying `inputs/` or the proposed tool copies: `seal` compares them to independent sources before accepting the archive.

Generate a fresh 16-character lowercase hex stage label and store it in `label`. In the Ubuntu runner's private mount namespace bind the workspace's `work/mock-mnt` to `/mnt`. Import the six images with the retained offline desktop app into `/mnt/us/.aidoku-native-$label/seed`. The SQLite cover paths must refer to that exact future stage. Compare all six source files against the frozen inputs by bytes. Do not copy the thumbnail cache created by the desktop check.

```sh
# Inside the worker's Ubuntu runner, with the private mock mount in place:
label=$(cat /work/label)
python3 -B /work/review/launcher-visit/payload.py freeze /work/plan /work/inputs "/mnt/us/.aidoku-native-$label/seed" "$label"
python3 -B -m unittest discover -s /work/review/launcher-visit -p 'test_*.py'

# On the test-env host, CODEX_THREAD_ID must name the actual new operator.
runner="$work/review/launcher-visit/run_visit.py"
python3 -B "$runner" seal "$work"
python3 -B "$runner" preflight "$work"
```

`seal` binds the operator identity, scripts, approved source files and exact package bytes without acquiring the lease. `preflight` verifies the package and configured host-key pin **without contacting the Kindle**. Exit 3 means at least one gate is closed; it is not permission to probe the device. An old lock disappearing or an old study reporting `ok` is not a recovery receipt: require the interrupted batch's original acquisition identity, a successful close that observed `device_closed`, checked native restoration and verified release. A later peer can still hold the lease, and acquisition is always atomic. A ready package is not a reservation.

An independently authorized administrator's cleanup-only record is not proof of native restoration or an original-owner release. It does not satisfy this visit's `prior_close_verified` gate or authorize the frozen visit. A separate future protocol may verify that record and check the current lease independently, but must not describe either result as restoration of the historical state.

## One operator, one continuous batch

Only after coordinating with other ready operators and verifying the original closeout, run `python3 -B "$runner" run "$work"` on the host. The designated operator must be ready to start immediately, with its normal private authentication. The runner admits only a free stock home, stages non-indexed private data, runs one diagnostic and collects three bounded files. It does not open a catalog entry, swap installed programs, retry a click, or install any package. The ordinary-exit marker is emitted only by the staged supervisor after the app's exit record is checked; an exit code of zero alone is insufficient.

Immediately before the upload and again before launch, the operator resets the idle timer once, only while the stock app is foreground and power is already active. It never wakes the device, changes a persistent sleep setting or refreshes the timer during measurement or recovery. On cancellation it waits for the recorded launcher to exit, or sends `TERM` only after confirming its PID, birth, staged command and open event FIFO. The production launcher opens that FIFO after installing its cleanup traps. The operator never removes the global display lock.

The runner checkpoints ownership and operations. It restores native state, checks the protected apps, lighting and stock framebuffer body, closes its stage, and releases only its own verified lease before report analysis. If the run failed after a possible mutation, do not edit the checkpoint, reuse the plan or take anyone else's token. **Only this plan's original operator** uses `python3 -B "$runner" recover "$work"`; recovery never launches the app. A refusal is not a release. Preserve the private receipt, and do not claim readiness based on absence of a process or directory alone.

If the host lost the `close` receipt, it may repeat `close` only while both owned directories and the evidence for the original checks remain. A missing stage, a private `close.intent` alone, or a reboot is not proof of restoration. If re-verification is no longer possible, stop and notify the original operator for manual reconciliation; do not manufacture a receipt or release the lease. A recorded successful `close` still requires a separate absence check on the mounted media before release.

After a successful run and verified release, `python3 -B "$runner" analyze "$work"` reports the launcher intervals (10 ms clock bins) and the application fence checks. Do not publish raw logs, images, identifiers or credentials. A failure to collect sufficient evidence invalidates the diagnostic, not a separately verified restoration. Notify peers of the verified return; do not hold the lease for analysis or another build.
