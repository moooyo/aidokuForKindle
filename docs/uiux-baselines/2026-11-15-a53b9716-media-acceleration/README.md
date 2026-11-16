# Hardware acceleration candidate captures

Captured on 2026-11-15 through `ssh test-env`. This supplemental candidate
preserves the frozen 2026-11-14 baseline and the earlier display-mode captures.

The ten desktop cases cover Paperwhite 5 and Scribe, disabled real hit areas,
English night mode, Japanese, Traditional Chinese, and portrait settings after
selecting a landscape Reader preference. Settings intentionally remain portrait;
the initial harness's landscape expectation was corrected and retained in
the research history, rather than changing application behavior.

Three digital framebuffer captures come from the physical Scribe: enabling
the hardware control, restarting the application with that preference, and
reading an imported two-page JPEG fixture. The final native state records two
completed hardware decode/resize operations. No new critical kernel errors
occurred. Native control, brightness and warmth were restored, staging removed,
and the lease released before analysis. Digital captures do not establish
optical ghosting, panel grayscale levels, or page-turn latency.

All cases use the uncommitted candidate based on
`a53b9716d64fe5521fac38431b24732c75cb8ff7`. The production source fingerprint is
`e9f8d08b6599ef657ecb572d8c343b16050fa87d1fa35579c5e518c9d14108e8`.
Exact executable hashes are in `manifest.json`. Original PNGs, state/geometry
sidecars and desktop action replays are preserved. Native inputs are synthetic
or explicitly isolated demo data; no account content or raw lease token is
included.

- [Desktop contact sheet](desktop-contact.png)
- [Physical digital captures](native-contact.png)
- [Desktop checks](desktop-report.json)
- [Native checks](native-report.json)
- [Source manifest](source-manifest.json)
- [Capture manifest](manifest.json)

| Scene | Paperwhite 5 | Scribe |
| --- | --- | --- |
| Disabled hardware controls | [PNG](desktop/pw5-hardware-disabled.png) | [PNG](desktop/scribe-hardware-disabled.png) |
| English night mode | [PNG](desktop/pw5-hardware-en.png) | [PNG](desktop/scribe-hardware-en.png) |
| Japanese | [PNG](desktop/pw5-hardware-ja.png) | [PNG](desktop/scribe-hardware-ja.png) |
| Traditional Chinese | [PNG](desktop/pw5-hardware-zh-TW.png) | [PNG](desktop/scribe-hardware-zh-TW.png) |
| Portrait reentry | [PNG](desktop/pw5-hardware-portrait-reentry.png) | [PNG](desktop/scribe-hardware-portrait-reentry.png) |

The native [enabled control](native/settings.png),
[persisted control](native/settings-restart.png), and
[Reader fixture](native/reader.png) retain full 1860 x 2480 digital output.
