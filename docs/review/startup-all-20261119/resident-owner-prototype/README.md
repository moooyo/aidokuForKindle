# Returning to Kindle Home without terminating the GUI

This research tests two separate questions. The existing screen-saver transition cannot admit a second Library launch, even when the GUI PID survives. A refactored native backend **can** keep one Slint window alive while disposing of an entire display session. In two ordinary opens on the physical Kindle, [four bounded observations](native-entry-live-results.json) failed to authenticate a fresh canonical visit for a retained GUI. The existing official launch remains in use; no on-device GUI reopen or related performance gain was measured. See the [admission decision and required future entry contract](library-visit-observer.md). These files are not included in the application or its package.

## What failed in the unmodified application code

[Verifier results](verifier-results.json) were generated on the designated Linux worker. The test compiles `src/native_presentation.rs`, its portrait writer, and `src/native_query.rs` byte-for-byte from the checkout. The participant, runner, foreground worker, permitted native-reader process group, and waiter's exit are real Linux processes. Only native hardware is replaced: attempted framebuffer/sensor reads fail, and the fixture never supplies a successful hardware observation.

| Attempt | Actual result |
| --- | --- |
| Load a consistent private startup record in the original child chain | A context was admitted, before hardware I/O. |
| Ask the standard writer to capture a new startup record with a live application PID, or with an outstanding sleep request | Both were refused before any hardware call. The writer explicitly requires an uninitialized application. |
| Replace calibration bytes without replacing the GUI context | Both startup and sleep-resume observations refused the changed record. |
| Increment the owner's generation under the old context | Refused as a different launch generation. |
| Let the original Library waiter exit, keeping exactly the same worker PID/birth/executable and unchanged runner and GUI-participant identities | The worker's parent changed. The original validator refused startup **and** resume observations before probing hardware. |
| Put a different, live launcher in a new record and try to let it own the old GUI participant | Refused because the runner is not the new launcher's child. |
| Give the retained participant a newly constructed context, a synthetic new generation and the original worker's **current** full identity | The context bound, but observing it immediately required a fresh framebuffer probe, which the fixture rejected. This is not a native writer or a successful native calibration. |

`packaging/kindle/launch.sh` currently launches `setsid sh ... --foreground` directly from its first waiter. `setsid` gives the foreground worker a new session but does not detach its parent. The standard waiter's `wait "$LIBRARY_WORKER_PID"` completes only after registration cleanup and process exit. The stock `ordinary-exit` continues to mean full exit. Delivering `CONT/USR2`, clearing the application marker to let the startup writer run again, relaxing process identity, or reusing the sleep anchor would all violate existing boundaries.

## What worked in the backend prototype

[Backend results](backend-results.json) came from the research copy assembled by [prepare-backend.py](prepare-backend.py). The production sources in the working tree are unchanged. The refactor puts the native presentation stack (framebuffer, touch, pose observer, input epoch, neutral scene, and update receipts) inside one call while keeping the terminal event-queue guard, its original Slint platform/window and fonts outside it. A normal screen-saver pause does not take this path. An explicit handoff requires the parked/settled/input-released barriers; its completion is published only **after** the presentation stack is destroyed. An ordinary sleep resume is rejected while returning or at Home. Ordinary quit still terminates the event loop permanently.

The host test ran one real `slint::run_event_loop()` and constructed a real Slint component. After the first presentation, it verified both old descriptors returned `EBADF` and the old mapping returned `ENOMEM` from `mincore`. The second presentation rendered a different software frame through the same component, adapter, and PID. The first property value survived; the first and second expected center RGB values were checked and written into separate framebuffers. A direct sleep resume and reuse of a consumed fixture request were refused. During a 401 ms idle sample the Linux test process accrued zero CPU ticks; RSS was 13,748 to 13,812 KiB. These are host observations, not the Kindle application's memory, power, or speed. The 190 pre-existing backend tests passed separately.

The resource types and their destructors were real. The host frame used the existing anonymous `Framebuffer` fixture, the input descriptor was a private socket, and the native idle acknowledgements and admission were synthetic. No evdev grab, EPDC, device brightness, native catalog update or user-initiated launch is certified. The original report used a test-only grant for its second round.

[Release-receipt results](backend-token-results.json) exercise the updated backend preparation and replace the test-only grant with `LibraryIoClosed`. Only the platform can witness the end of the scope owning the native resources. Its lifecycle handle can issue the resulting receipt once for the exact closed round and parked state. The same Slint loop test checks closed framebuffer and input descriptors, an unmapped framebuffer, rejection before resource release, a single claim from cloned handles, and refusal of ordinary Home resume. Consuming the receipt stages the second observation before the same window draws its next frame. That observation remains synthetic; the receipt proves local resource lifetime, not a native Library visit or permission to take the display. The custody of the original report is unchanged.

[Studio preparation](prepare-studio.py) assembles a separate research backend
with the optional `resident-studio` feature. The caller can start one Slint
window on a thread in its GUI process, close the first presentation, and claim
its real `LibraryIoClosed` after the two file descriptors and mapping are gone.
Only the caller can consume that receipt with its separately validated fresh
observation. `second_frame()` verifies that the original window and component
produce distinct expected pixels; `shutdown()` joins the same event loop.
The software adapter provides `studio_observation(rotation)` for a typed 64 x
64 gray8 layout. It does not test a Kindle framebuffer, touch, or EPDC. The
feature and its source files belong only to the disposable backend copy.

## Smallest credible product integration

1. Start the immutable owner/runner/GUI family under an already detached, stable parent **before the first capture**. Give each canonical Library entry its own waiting connection with ownership of that entry's catalog notification. The first waiter may retire after its own confirmed display handoff and notification; it cannot represent the lifetime of the retained owner. Do not change the shared handler or registration.
2. Separate the short-lived current *display* admission from the resident application's identity directory. Release the display lock at Home. A kernel lifetime-bound lock or equivalent must vanish if its exclusive holder dies; a stale directory and a stopped orphan must not block a fresh authorised cold launch. Children need explicit parent-death cleanup, and waiter cancellation or an unavailable owner must take the cold path. Bind IPC to the application, exact home/data identity, canonical document, binary, owner/process birth and peer credentials. An arbitrary `--library` string or sleep event cannot be a new presentation.
3. Accept the explicit quick-return action only from a fully initialized Library. Quiesce all work, settle/checkpoint writes, suspend outbound admission, release activity and Wi-Fi, and require any app-owned brightness restoration separately. The existing policy barrier covers Wi-Fi; it is not evidence that brightness/warmth has been restored. Do not overwrite a newer stock/user lighting change. Request the backend's complete I/O drop and wait for its post-destructor acknowledgement. Restore the status bar and original window-manager process identities, repaint native Home, settle exactly this catalog visit, and retire its handler. No components may draw, read input, write the database or use the network while Home is active.
4. A later exact Library open within the nonrenewable short TTL must produce a **new protected presentation transaction** for the *same* owner/runner/GUI. A distinct writer should bind it to that specific new waiter, the previously relinquished session, and a fresh monotonic generation/nonce. Perform the complete bounded native power/mode/WM-direction/GETVAR observation while stock is still live; compare the source identities before and after atomic publication. Keep the existing adapter and rebind its `NativePresentationContext` only after a fresh private identity and native observation check. Never truncate the first record or delete `application-pid` to bypass the current writer. Separate prepare from commit: even successfully observing a new record does not permit the GUI to acquire touch or display until its owner has legitimately taken the display for that waiter. Require a new full frame completion and new input snapshot before counting the reopen as interactive.
5. Keep `ordinary-exit` unconditional. Expired/replayed/cancelled attempts, low memory, sleep/USB transitions while at Home, owner/runner death, stale artifacts, changed files, or a failed calibration must release any partial attempt and leave the normal cold entry usable. Do not send a general `CONT` to a stopped participant to paint over Home. Repeated open/return cycles, forced-death cases, and foreground policy restoration require separate hardware qualification.

For a future Kindle trial, compare canonical Library tap to **native input-ready** for a fresh PID and the retained PID, on identical content in alternating order. Also measure the cost of the new return-to-Home path. Record each waiter, notification, boot/process birth and generation; before, during and after Home check native locks/FDs/mappings, brightness/warmth, network, CPU/RSS and absence of idle database writes. A successful screen-saver wake, a same-number PID after `exec`, an opt-in cache keeper, or a faster second launch from the OS page cache is not completion of this feature. This prototype provides no defensible numerical estimate of the end-to-end gain.

## Reproduction on the designated remote worker

From the repository root on the Linux worker, use a fresh, private scratch directory. The private overlay is optional when using an otherwise empty Cargo target directory; all builds use the project's shared lock and `-j6`. `verify-owner.py` copies only the three production source files listed above and their dependency lock. `prepare-backend.py` refuses to write into or over its source.

```sh
export RESEARCH_SRC="$PWD/docs/review/startup-all-20261119/resident-owner-prototype"
export OWNER_PROBE_WORKSPACE="$(mktemp -d)"
export OWNER_PROBE_CARGO_LOCK=/root/aidoku-input-discovery-20261116/startup-build.lock
python3 "$RESEARCH_SRC/verify-owner.py" --source "$PWD" \
  --build-dir "$OWNER_PROBE_WORKSPACE/probe" --build-lock "$OWNER_PROBE_CARGO_LOCK" \
  --output "$OWNER_PROBE_WORKSPACE/verifier-results.json"
export OWNER_PROBE_BACKEND="$OWNER_PROBE_WORKSPACE/backend"
python3 "$RESEARCH_SRC/prepare-backend.py" vendor/slint-backend-kindle "$OWNER_PROBE_BACKEND"
# Set this to the existing read-only Cargo target layers on the test host.
export OWNER_PROBE_CARGO_LOWERS=/path/to/existing/read-only/cargo/target
sh "$RESEARCH_SRC/verify-backend.sh"
```

For the standalone software adapter, generate a new directory and use its
feature explicitly. Integrating probes may alias this same library dependency
as `real_backend`.

```sh
python3 "$RESEARCH_SRC/prepare-studio.py" vendor/slint-backend-kindle "$OWNER_PROBE_WORKSPACE/studio-backend"
cargo run -j6 --locked --offline --manifest-path "$OWNER_PROBE_WORKSPACE/studio-backend/Cargo.toml" \
  --features resident-studio --example resident-studio
```

The later [independent stable-owner and fresh Library transaction](reentry/README.md) keeps the original GUI family alive through waiter retirement and exercises a separately supervised calibration for a second visit. Its [combined run](reentry/combined-results.json) includes the same real Slint GUI across two distinct live waiters and two complete controlled calibration transactions. The separate physical admission check failed on the installed route, so the same-GUI candidate was not tried on the Kindle.
