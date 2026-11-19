# Stable GUI ownership and an independent Library presentation

This experiment makes the second Library open a new presentation, without deleting the original `application-pid`, changing the shared catalog registration, relaxing the existing process-identity checks, or interpreting a screen-saver wake as a Library visit. It is not part of the production package. No Kindle launch-time saving was established by this study. The [separate physical entry check](../native-entry-live-results.json) rejected the installed route for same-GUI reentry on both ordinary opens; that check did not run the research GUI on Kindle.

The initial Library waiter starts an application-private guardian. The guardian waits for its own foreground worker. This extra parent means the first document can finish without reparenting the worker recorded in the first portrait calibration. A separate verified Unix socket belongs to each subsequent native visit. The initial waiter is retired only after its worker confirms that it restored Home and settled the catalog notification; release of its socket alone does not grant another presentation.

[The transaction code](library-api.rs) links the immutable initial record, current runner and same GUI, old and new waiters, and the next nonrepeating native activation. Returning consumes the old presentation context and the backend's one-use [`LibraryIoClosed` receipt](../resident-session.rs). The original context cannot be constructed again at Home. Each later bounded reader executes the **same complete stock calibration procedure** as the initial reader, including both power, display-mode and WM-direction reads when the sensor is supported. It publishes a distinct, exclusive record for the new presentation before takeover. The owner then commits its specific native takeover; only then can the retained GUI accept the new native observation and use its own backend receipt to reopen. The operation retains the original process family and does not inherit a previous sleep epoch. Entries fail closed without a separately qualified native origin or if their source changes, the peer leaves, the original owner changes, a power transition or ordinary exit starts, the 45-second admission deadline expires, or available memory cannot be established.

The [independent process test](writer-results.json) ran as a non-root Linux user with no new privileges. It used the real Rust reader, kernel peers, guardian, process identities, atomic publication and rejection paths, but replaced firmware reads and catalog admission with typed **test-only** adapters. The persistent participant was initially calibrated up, the first waiter retired, then a different waiter initiated a down calibration (rotation 2). The participant observed the new state in its original PID. Both observations ran the same ordered set of checks; attempts to use the old context or dead peer, pending sleep, a merely prepared grant, or an already committed takeover were refused. The existing application PID marker and initial calibration bytes remained unchanged. This test did not use an actual native input device or update the on-device catalog.

The [combined session run](combined-results.json) additionally exercised that process path and the actual backend on one GUI PID in a single run. The Slint component was a controlled test window, not the Aidoku Library screen. The first cold observation preceded creation of a real Slint software window. The backend destroyed its framebuffer mapping and returned both device-backed fixture descriptors before the first waiter retired. A fresh request from a different kernel peer then went through the complete writer, confirmed takeover and `LibraryIoClosed::reopen` with its distinct new calibration. The same Slint component retained its state and rendered the expected different pixels on its second presentation. No new Slint event loop or process replaced it. In the 401 ms Home sample, this software fixture accrued zero CPU ticks; its RSS stayed at 9,920 KiB and its PSS at 7,702 KiB. These numbers include neither a real Aidoku collection nor a Kindle memory or power measurement. The fixture did not impersonate a stock handler, use a real framebuffer ioctl, or claim a Kindle speed result.

The [separate backend test](../backend-token-results.json) uses the real Slint software window and control handle. It verified that both old descriptors were closed and the mapping unmapped before issuing a unique round-zero receipt. The same Slint window produced a different second software frame using fresh typed metadata, and the original ordinary sleep resume was rejected. Its hardware backing is a private socket and an anonymous framebuffer. Neither test establishes firmware admission, EPDC timing or power savings.

The [actual application library](arm-build.json) also compiled for `armv7-unknown-linux-musleabihf` against the real extended backend, production native query module and app-side observer using the designated Linux worker and Cargo `-j6`. This was a build check, not an installed executable. The [entry observer](../library-visit-observer.md) requires separately qualified, live evidence for the specific canonical document. The installed route never provided that evidence in four observations and keeps the ordinary cold entry. Enabling this path in a product would additionally require real per-visit catalog settlement after the existing Home restoration, the separate active-display lock, a revalidated full display/input barrier, and power and low-memory failover on the complete application. None of those claims follows solely from a successful software-frame test.

## Reproduction on a designated Linux worker

[prepare-source.py](prepare-source.py) reads the three installed source files and checks their fingerprints. It refuses to modify the original or an existing output tree. The freshly generated test crate shares the production protected-record code. Its `src/main.rs` provides only private host adapters; those adapters must never be copied into an application build.

```sh
export STUDY="$PWD/docs/review/startup-all-20261119/resident-owner-prototype/reentry"
export SCRATCH="$(mktemp -d)"
python3 "$STUDY/prepare-source.py" "$PWD" "$SCRATCH/probe"
# The private probe has a small dependency set; preserve the repository's resolved versions.
cd "$SCRATCH/probe"
export CARGO_NET_OFFLINE=true CARGO_BUILD_JOBS=6
flock /root/aidoku-input-discovery-20261116/startup-build.lock cargo build --offline -j6
# Use the independently compiled guardian from this study, no absolute device paths or native services.
python3 "$STUDY/verify-session.py" "$GUARDIAN" "$PWD/target/debug/aidoku-owner-boundary-probe" "$SCRATCH/results.json"
```

Run the driver and its binaries under an unprivileged user in accessible private temporary directories. The coordinator accepts only its own Unix peers, has bounded waits, and reaps its child processes. Actual on-device qualification and A/B testing are separate and must use the shared-device lease.
For the combined backend run, first generate the research backend and studio variant from the [backend instructions](../README.md). Then generate the combined crate in a new directory. Its optional software I/O adapter is built only in the study, never into a device package.

```sh
python3 "$STUDY/prepare-combined.py" "$SOURCE" "$STUDIO_BACKEND" "$SCRATCH/combined"
cd "$SCRATCH/combined"
flock /root/aidoku-input-discovery-20261116/startup-build.lock cargo build --offline -j6
python3 "$STUDY/verify-session.py" --with-slint "$GUARDIAN" "$PWD/target/debug/aidoku-owner-boundary-probe" "$SCRATCH/combined-results.json"
```