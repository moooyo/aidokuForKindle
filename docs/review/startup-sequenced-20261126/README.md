# First Library screen: sequential startup experiments

**Historical protocol, not the current handoff.** The initialized-Library
follow-up completed on both profiles; [its outcome](../startup-stock-admission-20261127/evidence/initialized-library-readiness.json)
rejects this direct-frame approach. Keep the loading path and do not rerun
item 3 as pending work. See the [current handoff](../../../HANDOFF.md) for the
last verified installation and next action. The historical daily install was
Aidoku 0.1.3 from `97173d0`; its [receipt](daily-deployment-97173d0.json)
and [background toolbar read](../startup-toolbar-overlap-20261126.md) remain
part of the record.

## Font delivery candidate

The control is source `97173d0`. The candidate only delivers already-linked,
immutable bundled font descriptors on the existing managed Library startup
path instead of racing a background thread for them. Actual registration
still runs on the UI thread, within the existing speculative window after the
loading SEND. The font set, language fallback, cancellation checks, checked
full-frame and touch barrier, and ordinary attachment order are unchanged.

## 1. Physical Scribe: reject the font-delivery candidate

The isolated offline foreground route excluded one warmup per arm and ran
four fresh-process pairs in AB, BA, AB, BA order. Each request was bracketed
on the device clock. `SEND` is the first populated Library submission;
`WAIT` is the driver's acknowledgement of that same update, not optical
settling. Elapsed time spent showing the loading page counts, but the loading
page is not the endpoint. There was no stock Library card press. Both arms
used the shell launcher, packaged native query helper, and identical opt-in
launcher and GUI diagnostics. Neither arm modified the daily installation.

| Pair | Order | Control SEND (ms) | Candidate SEND (ms) | Control WAIT (ms) | Candidate WAIT (ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | AB | 1377.835 | 1506.905 | 1671.994 | 1801.008 |
| 2 | BA | 1354.531 | 1386.544 | 1648.892 | 1680.514 |
| 3 | AB | 1444.880 | 1398.145 | 1738.983 | 1692.399 |
| 4 | BA | 1406.272 | 1412.960 | 1700.679 | 1707.763 |
| Arm median | — | 1392.053 | 1405.552 | 1686.337 | 1700.081 |

**Do not retain, merge or deploy this candidate.** The within-pair median
regression was **19.351 ms at SEND and 19.353 ms at WAIT**. Three of four
pairs regressed outside the request brackets; both order medians regressed.
The WAIT median regression bounds were 15.491–23.215 ms. These bound clock
bracketing, not sampling uncertainty. Wait from Library SEND to WAIT was
essentially unchanged (294.260 vs. 294.180 ms).

Font registration finished a median 18.440 ms after the loading SEND rather
than 411.033 ms, and the post-fence delay before attachment fell from 46.242
ms to 0.343 ms. However, the time from attachment to requesting the Library
rose from 72.174 ms to 110.462 ms, and the prior framebuffer-update wait
rose from 18.342 ms to 67.345 ms. These are elapsed intervals and separate
medians, not additive or proof that font registration made the driver
slower. Attachment began earlier relative to the unchanged display barrier,
but the actual Library did not appear sooner. All samples used the existing
toolbar snapshot; that path is retained unchanged.

[Per-launch data](physical-first-screen.json) includes the warmups, bounds and
same-marker verification. After ten launches, test directories were absent,
the native desktop and lighting were restored, and the original owner/token
verified exact lease release. [The experimental patch](font-early-delivery.patch)
is preserved separately from the clean product working tree.

## 2. Physical Scribe: what the launcher waits for

Use the four formal **control** launches in the same trace-enabled batch.
The foreground request to Rust `main` took **498 ms** at the median. The
approximately 449 ms in the prior toolbar study is a separate cohort and
should not be combined with these stage medians. `/proc/uptime` and process
birth ticks have 10 ms resolution. Equal shell marks do not imply zero work.

| Stage | Control median |
| --- | ---: |
| Foreground request → first shell marker | 10 ms |
| Shell marker → observer start (initial admission and FIFO) | 20 ms |
| Observer start → start waiting (overlapping generation setup) | 35 ms |
| Remaining wait for the owned power-event process | 10 ms |
| Checked portrait transaction, including sleep-capability observation and publication | **300 ms** |
| Native display takeover, including statusbar stop and check | **95 ms** |
| GUI spawn request → Rust `main` | 19 ms |

Small gaps between adjacent marks are not listed, and the medians are
independent. The observer gate does not certify subscriptions inside the
power service. The portrait interval combines process startup, native reads,
display/power consistency checks, publishing, polling and reaping; the coarse
trace does not isolate a removable part. The `sleep 2` is a private background
watchdog, not a foreground timeout reached on every launch. The display stage
includes suspending native drawing; it is not an optional delay. The only
clearly optional startup query in the transaction is stock sleep-module
capability, already combined into the existing helper session. Deferring it
would require preserving capability before the first sleep, and these traces
do not establish a worthwhile saving. No polling, power, orientation,
publication or takeover gate was removed.

## 3. Direct Library as the first frame: feasibility, not a rollout

The existing path cannot show the Library first: it starts metadata work from
an accepted loading update, requires a new native SEND and a yielded turn
before attachment, and only then finishes the initial Library projection.
The backend can check any first full frame, including a real Library frame,
but the decision must happen before drawing that frame. Do not delete the
full-update acknowledgement or touch resynchronization.

In this physical control cohort, the loading SEND occurred 247–272 ms after
Rust entry. Metadata was ready another 316–353 ms after that SEND; attaching
and requesting the scene subsequently took 71–122 ms. In the previous cohort
that reused the isolated data across launches, the equivalent metadata
interval was 108–130 ms, a separate measurement. The current cohort resets the
fixture each time; the two setups do not isolate the cause of the timing
difference. Even granting this cohort's worker the full Rust-entry-to-loading
window, with its measured elapsed cost unchanged, it would miss the original
loading SEND by 59–81 ms in all four formal controls. That is a scheduling
illustration, not a measured prediction for moving the worker: earlier work
can change contention, and the business scene needs more than metadata.

The first test is an opt-in readiness probe, not a two-frame-to-one patch.
It starts existing metadata preparation just after creating the window,
compares the same executable with that trigger enabled/disabled, and keeps
loading, fonts, attachment order, display checks and touch gates unchanged.
The [firmware harness generator](tools/prepare-readiness-harness.py) was used
with the built ARM probe and packaged query helper on both Kindle 5.20.1
emulators.
Both arms start from a fresh synthetic two-book fixture that still needs
normal application initialization.

| Profile | Mode | Metadata worker started relative to loading SEND | Metadata prepared relative to loading SEND | Functional result |
| --- | --- | ---: | ---: | --- |
| Scribe | Baseline | +3.009 ms | +2,870.274 ms | Pass |
| Scribe | Early | −861.329 ms | +1,220.893 ms | Pass |
| PW6 | Early | Not retrieved | Not retrieved | Pass |
| PW6 | Baseline | — | — | Failed generic before/after framebuffer difference check |

Scribe passed the existing full-frame/input barrier and non-demo Library
checks, with real QMP Library → Settings → Library touches and normal
exit in both arms. The early Scribe sample finished metadata preparation
**after** even the loading SEND, so it could not have had the business
scene ready before the loading draw. These are single emulated observations,
not physical startup times or an estimate of a production speed gain. PW6
ran in reverse order; its failed baseline does not provide a qualified pair.
Both guests cleaned their fixtures, restored stock services and stopped.
No physical Kindle was contacted or reserved for this step.

After the first run, the configured SSH key agent again refused new
`test-env` connections. The
[initialized-fixture follow-up](tools/prepare-initialized-readiness.py) had
not yet run, and the complete traces and screenshots had not yet been retrieved.
The follow-up uses an actual completed startup to seed the same initialized
synthetic Library for both arms. For that follow-up, require the owned app,
verified Library projection, native Settings and return input, update and
stock restoration rather than using a generic framebuffer difference alone.
Do not treat the first-round PW6 failure as resolved. The unbuilt-source
reproduction starts from `97173d0` using the
[source probe generator](tools/prepare-direct-screen.py), not the rejected font
patch. No direct Library frame or speed gain was measured.

A later direct-frame candidate should be limited to managed ordinary Library
opens. It needs timely prepared state and fonts, a checked native ownership
and geometry decision, a correctly projected empty or populated Library, and
an unchanged loading deadline on slow or failed preparation. Native settings
and ordinary background jobs must not prematurely activate. The chosen first
full update still owns the input epoch through the driver's acknowledgement,
portrait recheck and touch resynchronization. Sleep, cancellation and rotation
must fall back or restore without admitting stale input. Require both device
profiles and real-user-equivalent input on firmware before a new Scribe A/B.
At the time, the decision was to keep the loading path and the then-installed
`97173d0` daily build. This experiment did not modify or take a second lease on
the device.

## Resume item 3 on `test-env`

**Historical recipe only.** The initialized comparison was subsequently
closed on both profiles; it is not the next experiment.

The original procedure started from a checkout containing this archive. No files from the
previous computer's ignored `artifacts` directory are required. Follow the
[project instructions](../../../AGENTS.md) and
[firmware emulator guide](../../FIRMWARE_EMULATOR.md). Use the configured
`test-env` SSH alias; if its key agent refuses signing, resolve authentication
before running anything. Do not copy a key or change the device connection.

1. On `test-env`, look for the private workspace matching
   `/root/aidoku-first-screen-sequenced-20261126.*`. Select the existing
   workspace only if it contains the built `bin/direct-screen-probe`, the
   `bin/aidoku-native-query` helper, `verify-readiness.sh`, and the two
   `metadata-readiness-*-ssh-restored` result directories. That workspace
   already ran the fresh-fixture pair on each guest. Copy the tracked
   `tools` directory into a new private subdirectory of the selected
   workspace. Set `work` and `tools` below to those two remote paths.
2. If the private workspace is gone, start with a fresh private directory
   under `/root` matching the same prefix; do not overwrite a prior run.
   Export the Git tree at `97173d0` into its `source` subdirectory and copy
   the tracked `tools` directory alongside it. On `test-env`, with the
   intended paths assigned to `work` and `tools`, run:

   ```sh
   mkdir -p "$work/bin" "$work/logs"
   python3 -I -B "$tools/prepare-direct-screen.py" "$work/source"
   python3 -I -B "$tools/prepare-firmware.py" "$work/source"
   cp "$tools/verify-firmware.sh" "$work/verify-firmware.sh"
   python3 -I -B "$tools/prepare-readiness-harness.py" "$work"
   . /opt/kindle-toolchain/env.sh
   export CARGO_TARGET_DIR="$work/target"
   cd "$work/source"
   flock --exclusive --close --timeout 1200 /opt/kindle-toolchain/build.lock \
     cargo zigbuild --offline --locked --release --no-default-features \
     --features kindle,startup-draw-diagnostics \
     --target armv7-unknown-linux-musleabihf -j6 --bin aidoku
   install -m 755 "$CARGO_TARGET_DIR/armv7-unknown-linux-musleabihf/release/aidoku" \
     "$work/bin/direct-screen-probe"
   sh scripts/build-native-query.sh "$work/bin/aidoku-native-query"
   ```

   Use the shared build lock on a busy worker. The generated `verify-readiness.sh`
   can reproduce the fresh-fixture experiment, but it is not necessary to
   repeat those runs before the initialized-fixture follow-up.
3. Confirm both isolated emulators are free. Use the already built ELF or
   the rebuilt counterpart: the two arms must use the **same executable**
   with only the early-worker switch changed. Generate the follow-up once;
   if the generated files already exist, inspect their provenance instead
   of overwriting them. Run one guest at a time:

   ```sh
   python3 -I -B "$tools/prepare-initialized-readiness.py" "$work"
   sh -n "$work/verify-seeded-admission.sh"
   sh "$work/verify-seeded-admission.sh" scribe seeded
   sh "$work/verify-seeded-admission.sh" pw6 seeded
   python3 -I -B "$tools/summarize-readiness.py" "$work" \
     --json "$work/readiness-evidence.json" --preview "$work/readiness-preview.png"
   ```

   The seeded harness and collector were prepared but not run before this
   handoff. Preserve failures, inspect the actual Library and Settings
   captures, and confirm guest fixture removal, stock service restoration,
   and shutdown. A recheck needs its own output suffix. The collector skips
   nonexistent reports and labels them as missing; that is not a pass.
4. Check whether worker publication precedes the **start of the loading
   draw**, not merely SEND. The collector reports an upper bound on when
   the draw could have started from the measured work before SEND. Missing
   that bound rules out direct rendering in that visit. Beating the bound
   alone does not prove timely metadata delivery, font registration, or
   complete UI readiness. If the initialized arms are still late, keep the
   loading path and do not schedule a physical experiment. Only a separate,
   bounded direct-frame candidate that meets correctness and timing gates
   on both firmware profiles should be considered for a new Scribe A/B.

Keep raw logs, credentials, lease tokens, user data and private identifiers
out of Git. The existing private host workspace is a convenience, not a
lease or a condition for reconstructing the experiment.

## Functional verification on `test-env`

- Both ARM release builds use the same startup diagnostics. The Kindle-only
  configuration with diagnostics disabled passed `cargo check`.
- 25 binary tests and 876 library tests passed. Eight pre-existing opt-in
  library tests were ignored. One binary test that calculates font asset
  hashes was not run. The first parallel library run had one native-query
  fixture failure (`ETXTBSY`); the complete serial rerun passed it without
  modifying that fixture.
- The six firmware startup harness tests and three launcher evidence parser
  tests passed.
- With the packaged native query helper, the candidate displayed the two-book
  offline Library, accepted native QMP touches from Library to Settings and
  back, exited, and restored the stock firmware UI on Scribe and PW6. Scribe
  passed that combination twice. Control and candidate pixels below the live
  status bar matched on each profile. The inspected screenshots are synthetic
  fixtures; emulated panel timing is not a hardware result.
- Two Scribe candidate launches without the optional query helper stopped at
  portrait calibration, before the GUI process and font path. A separate
  PW6 control launch with the helper reached both Library and Settings but
  timed out verifying the return to Library. These failures are preserved in
  [the firmware qualification record](firmware-qualification.json), not
  reclassified as passes. Both emulators removed their test fixtures, restored
  their stock desktops, and were stopped.
