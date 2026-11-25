# Portrait UI handoff — November 25, 2026

The November 22 Aidoku Kindle handoff defines seven screen families: Library,
Detail, Reader, Browse, Search, History, and Settings. The Paperwhite drawing
is 618 × 824 logical pixels; the Scribe drawing is 930 × 1240. Paperwhite 6
uses the compact layout with its own runtime viewport. This handoff does not
introduce landscape controls, the proposed end-of-chapter screen, or design
preview switches.

Where the prose and templates disagree, the handoff's shared e-ink rules take
precedence: list rows have no separators, unread badges sit on the Library
covers, and compact modal sheets use a top border without a dimmed backdrop.
Counts, labels, hardware availability, and library images come from the
application's data, not the prototype's hard-coded examples. The supplied ZIP
contains the templates but not their supporting script or referenced artwork.
Offline reference frames can therefore verify layout and controls, not artwork
identity or a pixel-perfect match.

## Repeat the walkthrough

Run on `test-env` with the offline demo fixture. Use the same built
application for all captures in one round. Each program below writes to a fresh
output directory. Existing dated baselines stay unchanged.

| Tool | Coverage |
| --- | --- |
| `scripts/verify-ui-handoff.py` | Actual Slint application with `xvfb-run`: Library, details and chapters, Sources, Catalog, Search, History, and the Settings groups and subpages. Captures, pointer replays, and state assertions for Scribe, PW5, and PW6. |
| `scripts/verify-ui-handoff-reader.py` | Actual Reader with `xvfb-run`: panel states, tap layouts, progress changes on release, bookmark, artwork at night, and a real missing-page fixture. |
| `scripts/verify-ui-handoff-firmware.py` | Additional native smoke walkthrough on the isolated Scribe and PW6 firmware guests, using QMP touch, the ARM binary and normal launcher entry. |

On the prepared remote checkout:

```sh
cargo build --locked -j 6 --bin aidoku
python3 -I scripts/verify-ui-handoff.py --binary target/debug/aidoku \
  --output-dir /path/to/fresh/ui-frames
python3 -I scripts/verify-ui-handoff-reader.py --binary target/debug/aidoku \
  --device scribe --device pw5 --device pw6 --output-dir /path/to/fresh/reader-frames
```

See [the firmware emulator guide](FIRMWARE_EMULATOR.md) for native guest
identities and readiness checks. The native runner accepts `--native-portrait`
with a supervisor built by `scripts/build-startup-portrait.sh`; it does not
change the launcher's calibration requirement. Neither desktop screenshots nor
firmware framebuffer captures measure real panel refresh, ghosting, or
front-light behavior.

## Verification boundaries

The desktop and ARM configurations built on `test-env`. The Rust library
suite passed 850 tests (eight opt-in tests ignored). The portrait designs were
compared side by side with the running Slint application on Scribe and the
Paperwhite 5 design viewport, and the compact layout was also exercised at the
Paperwhite 6 desktop viewport. The final replay matrix passed all 201 screen
and interaction observations, all 114 Reader observations, and nine additional
settings actions after targeted rechecks. The retained reports keep the whole
matrix and its individual reruns separate. Captures include page turns, long
presses, search input, real backup creation, and recovery from a missing comic
page. Reader checks also compare the unaltered artwork pixels in day and night
chrome.

The QEMU Scribe and PW6 launchers did not pass the pre-display native portrait
calibration, even with the portrait supervisor. This prevented a native QMP
page walkthrough; it did not affect the independent Slint simulator runs.
The guest fixtures were cleaned up and both dedicated guests were stopped.
The test wrapper's calibration and offline-demo argument checks passed. The
separate full launcher suite was stopped without a result and is not included
in the UI totals. No physical device was contacted.
