# Software dithering implementation evidence

Date: 2026-11-16, Asia/Shanghai.

The completed candidate is installed in the ordinary Aidoku application on the
physical Scribe. It provides Off, 8x8 Bayer and high-precision Floyd-Steinberg
settings and replaces reversing button feedback with the BiliComics conventions.
The [implementation contract](../../SOFTWARE_DITHER_SETTINGS_20261116.md) describes
persistence, migration, cache identities and the Fast-mode boundary.

## Results

| Check | Result |
| --- | --- |
| Kindle-feature library suite | 793 passed; 5 existing ignored cases |
| Default-feature library suite | 778 passed; 5 existing ignored cases |
| Application binary suite | 20 passed |
| Final desktop and ARM builds | Passed; Cargo concurrency 6 |
| Final desktop UI matrix | 28 cases passed on PW5 and Scribe |
| Package verification | 162 checks passed |
| Final physical Scribe batch | 11 runs completed successfully |
| Reader Bayer8, Floyd-Steinberg, Off, and cold-start Floyd-Steinberg | Zero differing pixels from the corresponding 1860 x 2480 references |
| Native settings test strips, including Off restart | Zero differing pixels from their 1056 x 108 production references |
| Y4 during the software Reader checks | Available on the device, but neither requested nor effective |
| Fast page turns with a saved clearing interval of one | Seven DU/PARTIAL updates; no extra update during the final 9.2-second scheduled idle period |
| Native selected-button held/released comparison | Identical button pixels; control updates used nonflashing AUTO without swipe animation |
| Restoration | User metadata unchanged; native services and lighting restored; staging removed and the owned lease released |

The final narrow-screen layout was adjusted after the library tests. The
recorded source comparison confirms that only three Slint files changed after
those suites; final desktop/ARM builds, the 28-case UI matrix, and all physical
runs use the compact layout. This does not present older binary results as
physical evidence for the later executable.

## Evidence

- [Source fingerprint and complete file records](source-manifest.json).
- [Working-tree sources matched to the installed build on test-env](source-roundtrip.json).
- [Application, worker and development package identity](delivery-summary.json).
- [Package checks](package-verification.json).
- [Algorithm/prototype equivalence](algorithm-equivalence.json).
- [Desktop UI results](ui-verification.json).
- [Native pixels, settings, prepared-frame hits and refresh traces](native-results.json).
- [Installation, recovery and lease release](deployment.json).
- [Frozen screenshots, sidecars and replays](../../uiux-baselines/2026-11-16-a53b9716-software-dither/README.md).
- [Native result checker](../../../scripts/analyze-spatial-dither-native.py).

The original unquantized research chart was imported into an isolated test
library. These checks do not feed pre-dithered A/B pages back through the new
algorithms. The existing independent user A/B installation remains separate.
Snapshots validate the application pixels on the physical device; this batch
does not measure optical reflectance or perform a new post-MDP readback.
Earlier controlled post-MDP evidence remains in the research record.

The first installation succeeded, but its temporary launcher data omitted the
Scribe profile and defaulted to Paperwhite 5 calibration. The native startup
check failed and the batch restored native control and released its lease.
A fresh verification-only batch supplied the missing profile and passed all
11 runs against the same installed executable. No reboot occurred across the
two batches, and the final batch recorded no new critical kernel messages.
Private transport configuration, raw lease tokens, device identity records,
and user database contents are excluded from this repository evidence.
