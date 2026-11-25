# Kindle Firmware Emulator on test-env

## Current status: November 23, 2026

**Full-system firmware emulator restored.** The current `test-env` is CentOS
Stream 10. The historical Debian 13 emulator workspace was absent after the
reinstall; the host's existing `qemu-arm` and `qemu-ga` were not Kindle firmware
emulators.

The upstream [eink-emulator source](https://github.com/katadelos/eink-emulator/tree/c96062d51a1a54dd445d1668e740786d160a80bb)
is installed at `/root/aidoku-emulator/eink-emulator`. Kindle-patched
`qemu-system-aarch64` and `qemu-img` report 11.0.2 (QEMU revision
`b75b93cdc82d60d8c3f844dd114fd38dfb481df5`). The pinned KindleTool 1.6.6
imported firmware 5.20.1 for both devices. Both independent guests reached
their stock desktop with an executable userstore and independent SSH access.
The physical Kindle was not contacted.

## Instances

| Profile | Instance | Firmware hardware profile | Visible QMP frame | Guest SSH helper, relative to `/root/aidoku-emulator` |
| --- | --- | --- | --- | --- |
| Scribe 1 | `aidoku-scribe1-5201` | `dvt` | 1860 × 2480 | `guest-ssh-scribe1` |
| Paperwhite 6 | `aidoku-pw6-5201` | `production` | 1272 × 1696 | `guest-ssh` |

Start the requested guest on `test-env` with
`/root/aidoku-emulator/start-guest scribe` or
`/root/aidoku-emulator/start-guest pw6`. The host helper uses the named
instance's QMP socket, localhost-only VNC and SSH, restricted guest networking
and a 256 MiB QEMU translation cache. Allow the stock desktop to settle; SSH
readiness alone does not establish that the launcher can take over the screen.
Confirm the guest marker, the running `statusbar` and `awesome` processes,
and the effective `/mnt/us` mount. PW6 can retain a lower non-executable
mount under the executable mount supplied by the emulator's first-boot setup.

## Managed Library startup check

Run the [firmware harness](../scripts/verify-firmware-emulator.py) from the
checkout on `test-env` with Python 3.11 or later and Pillow. Select the command
for the running guest and set `arm_binary` to the ARM build under review:

```sh
emulator_home=/root/aidoku-emulator
arm_binary=/path/to/arm/aidoku

python3 scripts/verify-firmware-emulator.py --cases startup-library \
  --profile scribe --firmware 5.20.1 --machine aidoku-scribe1-5201 \
  --emulator-root "$emulator_home/eink-emulator" \
  --guest-ssh "$emulator_home/guest-ssh-scribe1" --binary "$arm_binary"

python3 scripts/verify-firmware-emulator.py --cases startup-library \
  --profile pw6 --firmware 5.20.1 --machine aidoku-pw6-5201 \
  --emulator-root "$emulator_home/eink-emulator" \
  --guest-ssh "$emulator_home/guest-ssh" --binary "$arm_binary"
```

The first launch always runs without the native query helper. Add
`--native-query /path/to/aidoku-native-query` for a second functional run with
the helper available. Fallback is valid in either run. Add
`--require-toolbar-snapshot` with `--native-query` only when qualifying a build
whose toolbar snapshot is expected to be used.

The check uses an offline, non-demo Library with two synthetic books and the
real launcher. It verifies the loading frame and input barrier, populated
Library submission, a QMP framebuffer change from stock, and restoration of
the stock desktop and launcher lock state. The QMP change alone does not
identify the Library content. The check does not press the native Library card
or measure physical display latency.

## Startup regression: November 23, 2026

These are historical observations of an unmerged background-toolbar candidate
using its experimental firmware harness. They are not a verification of the
main-branch `startup-library` check or a claim that the candidate shipped.

| Profile | Native Library with no query helper | Helper-present snapshot | Stock desktop and lock cleanup |
| --- | --- | --- | --- |
| Scribe 1 | Pass, 930 × 1240 logical, large layout | No snapshot; the same Library appeared through fallback | Pass on both launches |
| Paperwhite 6 | Pass, 636 × 848 logical, compact layout | No snapshot; the same Library appeared through fallback | Pass on both launches |

The passing runs verified observer admission before portrait calibration and
display takeover, the initial full-frame/input barrier, both fixture book
titles, and a visible change on the guest framebuffer. The helper-present
runs produced a usable Library and exited normally, but failed the fast-path
assertion: the optional probe became unavailable before it logged a completed
session handshake on both guests, and the later ordinary status query worked
with the same helper. Unavailability was logged 88.599 ms and 123.819 ms
after probe start, respectively. This is consistent with the 80 ms probe
budget; the trace does not identify the exact refusal or prove that extending
the budget would yield a fresh snapshot at projection time.

The same traces show a separate freshness limit. The first synchronous
toolbar read began 2.150 s (Scribe) and 4.271 s (PW6) after the background
probe started, beyond the 1 s validity window measured from probe start.
Even a successful early query would have expired in those launches. An
emulator-only scheduling comparison can test starting the probe at metadata
readiness, approximately 130 ms before the toolbar read in these traces,
without waiting for it or changing the deadline and freshness limits. This
is a diagnostic, not evidence that a later trigger is faster on hardware.

The initial pair on each newly started guest failed at the existing portrait
calibration gate after observer admission and before display takeover. The
later runs are separate results and do not erase those four refusals; their
cause was not captured. The product gates and budgets were unchanged. No
physical device test or daily R3 deployment is qualified.

Both guest overlays were retained for reuse. Per-run test fixtures and launcher
locks were removed, and both guests were stopped after syncing their disks.

Historical captures and verification reports describe the earlier setup and
remain unchanged.
