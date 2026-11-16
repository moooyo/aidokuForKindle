# Software dithering and flat feedback candidate

Date: 2026-11-16. Base commit: `a53b9716d64fe5521fac38431b24732c75cb8ff7`.

This frozen candidate records the three software dithering choices, the actual
algorithm-generated 256-input test strip, and BiliComics-style button feedback.
It contains 28 desktop captures and ten physical Scribe application snapshots,
with original state/geometry sidecars and action replays. The eleventh native
run retains state and accepted refresh evidence for persistent Fast page turns.

The desktop renderer uses scale one: PW5 is 618 x 824 and Scribe is 930 x 1240.
The physical Scribe uses scale two and 1860 x 2480 snapshots. Native snapshots
are digital application output, not camera measurements of panel gray levels.

| Scene | PW5 | Scribe |
| --- | --- | --- |
| S04, Off | [PNG](raw/pw5/pw5-S04-off.png) | [PNG](raw/scribe/scribe-S04-off.png) |
| S04, Bayer8 | [PNG](raw/pw5/pw5-S04-bayer8.png) | [PNG](raw/scribe/scribe-S04-bayer8.png) |
| S04, Floyd-Steinberg | [PNG](raw/pw5/pw5-S04-floyd_steinberg.png) | [PNG](raw/scribe/scribe-S04-floyd_steinberg.png) |
| S04, selected display button held | [PNG](raw/pw5/pw5-selected-button-held.png) | [PNG](raw/scribe/scribe-selected-button-held.png) |
| S03, switch held | [PNG](raw/pw5/pw5-switch-held.png) | [PNG](raw/scribe/scribe-switch-held.png) |
| S04, English night mode | [PNG](raw/pw5/pw5-S04-night-en.png) | [PNG](raw/scribe/scribe-S04-night-en.png) |
| S04, Japanese night mode | [PNG](raw/pw5/pw5-S04-night-ja.png) | [PNG](raw/scribe/scribe-S04-night-ja.png) |
| S04, Traditional Chinese night mode | [PNG](raw/pw5/pw5-S04-night-zh-TW.png) | [PNG](raw/scribe/scribe-S04-night-zh-TW.png) |

Native Scribe: [Off setting](native/scribe/settings-off.png),
[Bayer8 setting](native/scribe/settings-bayer8.png),
[Floyd-Steinberg setting](native/scribe/settings-floyd.png),
[Bayer8 Reader](native/scribe/reader-bayer8.png),
[Floyd-Steinberg Reader](native/scribe/reader-floyd.png),
[Off Reader](native/scribe/reader-off.png), and
[cold-start Floyd-Steinberg](native/scribe/reader-floyd-cold.png).

Use the [manifest](manifest.json), [source identity](source-manifest.json),
[UI assertions](evidence/ui-verification.json),
[native assertions](evidence/native-results.json) and [checksums](SHA256SUMS)
when comparing later changes. The earlier baselines remain unchanged.

Verification runs only on `ssh test-env` and the owned physical-device batch.
The [implementation evidence](../../review/software-dither-settings-20261116/README.md)
records the temporary launcher correction, full validation scope, and restoration.
