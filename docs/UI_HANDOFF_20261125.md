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
| `scripts/verify-ui-handoff-reader.py` | Actual Reader with `xvfb-run`: panel states, tap layouts, progress changes on release, chapter navigation, bookmarks, artwork at night, and a real missing-page fixture. |
| `scripts/verify-ui-handoff-browse-search.py` | Pointer transitions within Browse and Search, including source configuration, keyboard modes and drafts, paginated results, and returning to the originating Catalog page. |
| `scripts/verify-ui-handoff-firmware.py` | Additional native smoke walkthrough on the isolated Scribe and PW6 firmware guests, using QMP touch, the ARM binary and normal launcher entry. |

On the prepared remote checkout:

```sh
cargo build --locked -j 6 --bin aidoku
python3 -I scripts/verify-ui-handoff.py --binary target/debug/aidoku \
  --output-dir /path/to/fresh/ui-frames
python3 -I scripts/verify-ui-handoff-reader.py --binary target/debug/aidoku \
  --device scribe --device pw5 --device pw6 --output-dir /path/to/fresh/reader-frames
python3 -I scripts/verify-ui-handoff-browse-search.py --binary target/debug/aidoku \
  --output-dir /path/to/fresh/browse-search-frames
```

See [the firmware emulator guide](FIRMWARE_EMULATOR.md) for native guest
identities and readiness checks. Wait until the stock framework has reached
its home screen, not just until guest SSH responds. The native runner accepts
`--native-portrait` with the packaged portrait supervisor and `--native-query`
with the packaged LIPC query helper. Neither option changes the launcher's
calibration requirement. Neither desktop screenshots nor
firmware framebuffer captures measure real panel refresh, ghosting, or
front-light behavior.

## First-pass verification

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

## Second portrait audit

The follow-up checked the Scribe and Paperwhite designs again, using the actual
application at both design viewports and at the Paperwhite 6 desktop viewport.
It added pointer replays for returning from a Catalog search or from expanded
results, preserving search edits across the scope picker, opening books from the
continuation card, selecting only chapters in the current filter, retaining an
empty selection, and navigating across chapter boundaries. The explicit
Previous Chapter button opens the preceding chapter at page one; a backward
page turn still lands on its last page.

The Chapter Updates view groups today and yesterday using the same height-aware
pagination as reading history. Downloading all updates leaves the user on that
list, and clearing reading history does not clear downloads or the library.
The updated settings checks include actual backup creation and restore
confirmation, category editing, night mode, startup presets, and capability
controls that stay disabled in the desktop simulator. Localized text and the
category counts and units were checked in captured frames. On the taller
Paperwhite 6 viewport, a populated history page spreads the rows without
changing their height or the page boundaries.

On `test-env`, 333 distinct desktop page and interaction observations passed
(111 per profile), and all 123 Reader observations passed. The independent
Browse and Search matrix passed 108 pointer scenarios, and 18 changed paths
were repeated after the final scope-label adjustment. Four additional Komga
loopback checks exercised actual remote-result pagination without a duplicate
request on return. Desktop and ARM builds passed, along with 851 Rust tests
(eight opt-in tests ignored). Screen geometry checks measured the reference
234 px Scribe and 202 px Paperwhite 5 Library covers, a 28 px bottom inset in
all three populated Library grids, and the toolbar and current-chapter tones.
The populated Catalog and History views also stayed within a 30 px bottom
inset after the Paperwhite 6 History spacing adjustment.

Native firmware verification remains partial. The reference ARM build passed
native portrait calibration, displayed the Library on both guests, and reached
Library page two on Scribe and Detail on PW6. Its Scribe reverse swipe did not
return to page one, and the PW6 Reader stayed on its loading screen during that
run. The follow-up ARM build also passed the normal startup gate on both
models. It reached Library on Scribe and Library and Sources on PW6, but did
not display the Catalog transition within the bounded observation window on
either guest. No cause was established from those captures. The isolated
fixtures and launcher locks were removed, the stock services were checked, and
both guests were stopped. This simulator round did not use a physical Kindle.

## Physical Scribe walkthrough

The physical session used the second-audit working tree on top of `0ef90c1`,
with an isolated offline demo library and the ordinary native launcher. The
Scribe rendered at 930 × 1240 logical pixels (1860 × 2480 framebuffer pixels).
The harness waited for the Library dock to be visible before sending evdev
contacts. It resolved the raw touch coordinates from the active portrait
calibration and checked scene-specific pixels alongside navigation state.
This verifies the native input and rendering paths, not the physical digitizer
or optical e-ink response.

The ARM debug run verified the Library, a forward Library swipe, opening the
continuation title in Detail, and 12 Browse, Search, History, and Settings
observations. The Browse run entered the Catalog, moved to page two, opened
source-scoped search, returned to that same page, then opened source management.
The Settings run checked the entry, frontlight controls and Download Management
pane without changing the physical lights. Some captures preceded background
cover decoding. The earlier reverse-swipe and sort captures showed stale frames
and were not counted as passing those interactions.

The debug Reader reached chapter 34, page 12, but the native frame still showed
its loading label after the bounded observation. The optimized ARM build with
the packaged image helper did not reproduce that result. Both release batches
passed: 25 observations across 24 distinct scenes, including full-screen
artwork at chapter 34, page 12, a visible change to page 13, Reader controls
and the Picture pane, updated progress on return to Library, the Library swipe
round trip, sorting and filters, and long-press selection and dismissal. The
Kindle-feature Rust suite passed 867 tests (eight opt-in tests ignored). Some
release observations also preceded background cover publication. No physical
Paperwhite 6 or optical display behavior was tested. The separate Scribe QEMU
release run passed 11 observations through Reader and return to Library, but
then timed out waiting for the Catalog dock marker. Its stock services and
fixture cleanup were verified and the guest was stopped; the physical Catalog
result does not turn that QMP timeout into a pass.

The temporary app and demo data were removed after each batch. The stock home
and lighting were restored, the installed Aidoku files' metadata was unchanged,
and the SSH connection was closed before the owned lease was released.
