# Startup controls

Open **Settings → Startup & reopening** from the library. Paperwhite opens a separate pane; Scribe keeps the settings list on the left. Each switch saves independently. **Enable all** and **Use recommended** affect only these six choices. Quit and reopen Aidoku to apply the selected startup configuration. Simply opening the pane does not save a file.

| Switch | What the enabled position changes |
| --- | --- |
| Use the native portrait check | The Library launcher can use the packaged native portrait check before starting Aidoku. |
| Preload two cached covers | Startup may read two already-prepared local cover thumbnails before the first library view. |
| Wait briefly for the last cover | A nearly complete cover group can briefly wait for its last thumbnail before publishing another frame. |
| Prepare covers when idle after import | Generate thumbnails from supported local imports during idle time so later library pages can reuse them. |
| Simplify the first library layout | Use the bounded visible-page projection and avoid redundant title measurement in the first library view. |
| Keep local covers for quick reopening | An eligible Kindle Library launch can retain local cover pixels for up to 45 seconds. Reopening starts the application and checks the native display again. |

When quick return is available, choose **Library → More → Quick return to Kindle** or use the button in the startup settings pane. **Exit app** remains a complete exit. Explicitly switching off quick reopening releases retained covers and withdraws the shortcut for the running Kindle session. It does not remove any books or on-disk thumbnails.

The settings pane reads the application's saved policy for its switch positions. Capture diagnostics keep that selection separate from `active_startup_options`, the configuration with which this run actually started. Demo mode uses its own data directory.

The [label fit check](label-fit.json) measured single-line switch, preset, menu, and entry text against the narrower applicable slot with the bundled Noto Sans UI fonts on `test-env`. None exceeded its slot. Simplified Chinese and English use the bundled face throughout. Traditional Chinese and Japanese already use system fallback for some characters elsewhere in the interface; the new copy introduces no additional fallback characters.

The [desktop acceptance runner](../../../../scripts/verify-startup-settings.py) uses isolated data and the real PW5 and Scribe hit areas. It checks every switch, the rendered pixels, both presets, reopening, the actual policy file, unrelated application preferences, localization, and the absence of a native handoff control on desktop. Device timings and physical return-to-Library behavior require the separate native trials.

## Frozen desktop acceptance

The [final-v2 desktop build](2026-11-19-c6867d1-28c1bb31/desktop-build.json) passed **29 cases and 793 checks** on the remote worker: 15 cases / 408 checks on Paperwhite 5 and 14 cases / 385 checks on Scribe. Twenty-six cases used real pointer events. The [case results](2026-11-19-c6867d1-28c1bb31/startup-settings.json), [capture provenance](2026-11-19-c6867d1-28c1bb31/capture-provenance.json), and [checksums](2026-11-19-c6867d1-28c1bb31/SHA256SUMS) bind these records to product source `28c1bb31…` and desktop executable `97acfaa6…`. The preceding Git revision is the comparison base; the source digest identifies the actual build.

| View | Original capture |
| --- | --- |
| Paperwhite settings list, with startup settings and Exit both visible | [Library → Settings](2026-11-19-c6867d1-28c1bb31/pw5-startup-settings-entry.png) |
| Recommended selections on a fresh Paperwhite profile | [First visit](2026-11-19-c6867d1-28c1bb31/pw5-startup-opened.png) |
| Six switches immediately after saving Enable all on Scribe | [Scribe](2026-11-19-c6867d1-28c1bb31/scribe-startup-enable-all.png) |
| Six selections retained after reopening on Paperwhite | [New process](2026-11-19-c6867d1-28c1bb31/pw5-startup-reopened.png) |
| English night mode, Traditional Chinese and Japanese | [English](2026-11-19-c6867d1-28c1bb31/pw5-startup-render-en.png), [Traditional Chinese](2026-11-19-c6867d1-28c1bb31/pw5-startup-render-zh-TW.png), [Japanese](2026-11-19-c6867d1-28c1bb31/scribe-startup-render-ja.png) |

Each original PNG has its matching state, geometry metadata, and [input replay](2026-11-19-c6867d1-28c1bb31/actions/). The fresh profile enables only the first-library-layout switch in this frozen build. Saving Enable all immediately changes the six displayed positions; the preceding launch configuration remains unchanged until reopening. The runner verified every individual selection against the real policy file and checked that ordinary application preferences were preserved.

Visual review found no cropped control labels, missing-glyph boxes, overlapping hit areas, or covered navigation. All six switches and both presets are visible without scrolling on both profiles. Some fallback glyphs have lighter strokes in the Traditional Chinese and Japanese recommended-preset labels. These examples document desktop rendering; they do not measure an optical Kindle refresh.
