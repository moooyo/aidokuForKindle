# Aidoku for Kindle development bundle

This is an independent Rust/Slint application for jailbroken Kindle devices.
It does not require the WeRead application, a WeRead account, or a KOReader plugin.
The binary uses an isolated Aidoku data directory and a native framebuffer/input backend.

## Build on the remote worker

From Windows PowerShell, transfer this checkout to the designated `test-env` worker.
Run all build and verification commands there, using the repository's remote script.
To build the ARM payload directly on that Linux worker:

```sh
. /root/.cargo/env
export CARGO_TARGET_DIR=/root/aidoku-build/target
export CARGO_BUILD_JOBS=2
export ZIG_BIN_DIR=/root/weread-build/tools/zig-x86_64-linux-0.15.2
export TMPDIR=/root/aidoku-phase3/build-temp
mkdir -p "$TMPDIR"
sh scripts/build-kindle.sh
```

The build requires Rust 1.92 or newer, cargo-zigbuild, Zig, and the
`armv7-unknown-linux-musleabihf` Rust target. It creates
`dist/aidoku-kindle-development.zip` and a SHA-256 payload manifest.
Desktop executables cannot run on Kindle.

This convenience build creates a development artifact in a reusable staging
directory. The final Candidate18 bundle is `aidoku-kindle-development.zip` and
uses separately frozen ARM and launcher inputs. Desktop executables cannot be
installed as the Kindle payload.

| Frozen input | Identity |
| --- | --- |
| Production source | 236 files; 42b11de8d1b07e7af30665f98bb0dbf6291c0d6a6ec3355005c1402f42b21f24 |
| ARM application | 48,088,632 bytes; 35dd10da4d6101733ff468d3267d5b98594357465a034323df8ecf625753749d |
| Launcher | 31,642 bytes; e4a4bc84a1735889079f3b3f25dcc090c8b55f1875e4bf0a9f393cede6d16254 |

Two fresh bounded Search/F13 repair walkthroughs reviewed the final source,
including independent PW/Scribe subtitle endings and each Reader's own source
composition. Their scope is the corrected UI, not a repeated full 101-reference
or 604-case stress suite. C16's wider reference walkthrough and every older
candidate report retain their own source and payload scope. Runtime results and
formal measurements are separately bound in the source checkout's
`docs/PHASE3_VERIFICATION.md`, `docs/PHASE3_NATIVE_RESULTS.md` and
`docs/PHASE3_PERFORMANCE_RESULTS.md`; a package alone does not establish them.

Use the existing source-bound package helper with the exact independently
verified production manifest, then run `scripts/verify-kindle-package.py` on
`test-env`. The external manifest binds the actual archive, each member,
production digest/count and official-manifest identity. This README intentionally
does not contain the enclosing ZIP's future byte count or digest. Current final
archive and runtime evidence belong in the external manifest and source reports.
The original Phase2 dist recovery and every failed attempt remain preserved.

`ZIG_BIN_DIR` is optional when `zig` is already on `PATH`. The example above
uses the existing Zig 0.15.2 installation on the designated remote worker;
set it to the directory containing the executable on another worker. The
build script prepends that directory to its own process `PATH`. It defaults
to two Cargo build jobs unless `CARGO_BUILD_JOBS` is already set.

The staged `aidoku/LICENSE` contains the complete GPLv3 text. The
`aidoku/licenses/` directory retains the backend MIT/Apache licenses and
patch provenance, the source ABI MIT license, the full AOSP Pinyin Apache
notice/license and derivation record, all font OFL texts and font manifests,
and the icon/demo attribution records. `THIRD_PARTY_NOTICES.md` is also
included. Embedded fonts, dictionary data, SVGs, and demo images are not
duplicated as loose runtime assets in the package.

## Install

1. Extract the development ZIP at the root of the Kindle's USB storage.
   Keep the top-level `aidoku`, `extensions`, and `documents` directories.
2. Ensure `aidoku/app/aidoku`, `aidoku/launch.sh`, the KUAL profile script, and
   `documents/Aidoku.sh` are executable. FAT user storage needs the jailbreak's
   executable mount.
3. Open KUAL, choose Aidoku, and select the exact device profile before launch.
4. Choose the Aidoku launch entry. A compatible document-script launcher may
   alternatively execute `documents/Aidoku.sh`; the stock document reader alone
   does not execute shell scripts.

The profiles are `pw5`, `pw6`, `scribe`, `scribe2`, `scribe3`, and
`scribe3-nofrontlight`. Without a saved selection, the initial design profile
is `pw5`. Native dimensions are read from the visible framebuffer, rather than
assuming its virtual height or stride is the screen size. Design profiles
control physical sizing and available front-light/warmth controls. Their
presence is not a tested device-support matrix.

For SSH access, the equivalent command is:

```sh
sh /mnt/us/aidoku/launch.sh
```

The launch script detaches from its caller, acquires an Aidoku-specific lock,
records any stock window-manager processes it pauses, stops a running status
bar when supported, and restores exactly those resources on exit or a signal.
After restoring those services, it requests a full stock X11 repaint with the
device's `xrefresh` utility when available. This is necessary because direct
framebuffer rendering does not update the stock X server's damage tracking.
For a powerd screensaver transition, the application checkpoints navigation,
pauses its controllers, releases the evdev grab, and stops framebuffer writes.
The launcher then parks the actual application process with SIGSTOP while the
stock screensaver owns the display and input. A confirmed stock wake restores
the same process, fonts, and displayed image, rewrites every RGB pixel, and
submits a full GC16 refresh. The app does not decode the retained page again.
Old touch events are drained before the native grab is restored.

A nonce and monotonically increasing sleep epoch bind pause/resume acknowledgements.
Quick wake cancels an unfinished pause; stale signals from an older transition
cannot claim a newer one. A finite fallback uses `--resume` and the persisted
navigation checkpoint only after this launcher's own sleep transition loses
the retained process. Normal UI exit and USB takeover never relaunch the app.
USB takeover returns ownership without an application repaint over the USB view.
Download controllers resume only automatically paused jobs after network recovery;
manually paused or cancelled jobs keep their state. The idle notification worker
is quiesced before the process parks, so it cannot inhibit the stock screensaver.

Navigation and verified reading progress are persisted by the application.
Force-killing the launcher itself with SIGKILL bypasses every shell trap;
recover with a normal device restart if an interrupted session strands the UI.

## Reading and storage controls

The closed Reader displays artwork across the full viewport without a persistent
page number, bookmark corner, footer, or background-download notice. Open its
menu with the center touch region, a downward swipe from the top, or a hold.
Closing the controls restores the image-only canvas.

Reader settings offer manual portrait, landscape, and reverse-landscape
orientation. Reverse landscape uses 270-degree output and the matching touch
mapping. On a recognized sensor, the active app follows Up/Down as 0/180-degree presentation while retaining the current chapter/page and manual 90/270-degree preferences. Manual four-way orientation preferences are not expanded into automatic four-way rotation. Qualification remains scoped to the actual reports; synthetic sensor input and a RAM fixture in the dedicated firmware VM do not establish physical Kindle sensor, storage or display acceptance. Two-page layouts
offer a separate-cover option. Tap settings include L-shaped and swipe-only
layouts, with a separate reverse-taps preference; swipe-only disables page-turn
taps while retaining menu access and page-turn swipes.

Automatic brightness is not implemented or qualified. The lighting controls
cover only the exposed frontlight/warmth capabilities. Automatic rows visible in
some supplied Scribe references are absent and are an explicit capability limit;
this does not mean Scribe hardware lacks automatic brightness. Unavailable
controls and unobserved operations are not accepted as working features.
Storage settings calculate download, image-cache, cover, and available-space
totals on demand and show the largest per-book associations. Unknown sizes and
zero-byte categories remain distinct. Shared files can appear in more than one
book's associated size, while the global download total counts each file once.
The usage diagram covers tracked image data plus available space, rather than
the device's entire capacity. The per-book download menu offers a
confirmation before removing that book's downloads. Cleanup retains other
books' shared ownership, active image leases, and independently imported files.
The displayed completion result follows the download worker's cleanup result.

Settings > About provides a View action for the complete GPL license in a paged
information panel. The package also retains the complete license and
third-party notices as ordinary files.

## Local data

`/mnt/us/aidoku/data/` contains the SQLite catalog, source configuration,
preferences, download payloads, and cached images. Demo data is isolated by
the application's `--demo` option. Logs go to `data/logs/launcher.log`.
The package excludes runtime data and credentials. Copy source configuration
only through the app or its documented configuration format.

Uninstall by first exiting Aidoku and then removing its `aidoku` and
`extensions/aidoku` directories and `documents/Aidoku.sh`. Back up `aidoku/data`
before removing it if reading progress or offline chapters are needed.

## Rendering and acceptance boundaries

UI updates use partial driver-selected refresh; touch feedback uses DU, and
the configurable page count upgrades every Nth comic page to flashing GC16.
Inversion uses the implemented backend path and its full-refresh boundary.
Synthetic sensor input and a RAM fixture in the dedicated firmware VM are
software-test conditions, not physical Kindle or persistent-filesystem acceptance.
Driver waveform availability varies by device; neither this mapping nor a
desktop screenshot proves physical ghosting or refresh latency.

Desktop captures, native firmware-emulator execution, and physical-device
acceptance are separate evidence. Device claims require the exact model and
firmware, including touch, lifecycle, front light, memory, and panel behavior.
The designated firmware guest reports approximately 957 MiB of memory; its
results do not certify a 512 MiB device. Software wake readiness records RGB
submission and does not establish zero delay to the first physically visible
comic frame. A build or package alone does not establish completed acceptance.
The application is distributed under GPL-3.0-or-later as recorded in its Cargo
metadata. Third-party licenses and source provenance are included alongside
the payload; the native display backend retains its MIT/Apache-2.0 terms.
