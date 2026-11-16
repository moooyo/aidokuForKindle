# 256-swatch grayscale settings captures

Captured on 2026-11-15 through ssh test-env. This supplemental S04 candidate
uses 256 adjacent swatches with exact RGB values from (0,0,0) to (255,255,255).
Integer boundaries preserve both endpoints and all intermediate input values.
The swatches retain their grayscale values in both interface themes.

Six settings captures cover Paperwhite 5, Paperwhite 6 and Scribe in day/night
themes, including English, Traditional Chinese and Japanese. Pixel checks
found all 256 values, exactly 256 monotonic constant-color swatches, equal RGB
channels and identical rows throughout each bar interior. Two reader-panel
checks passed without adding this settings-only test to the Reader controls.

This is software-renderer input evidence. The settings scene does not enable
the Reader-specific Y4 policy. No native driver output or optical grayscale
count is established, and no physical device operation was run.

Base commit: a53b9716d64fe5521fac38431b24732c75cb8ff7 plus uncommitted edits.

Production source SHA-256: ffac5ba3b0166a1dae90d9a8240d4f9616915259da8a4223a939bd2261e23f80.

Desktop executable SHA-256: 125bca6eed010bccb109102232e1e0fbf8dc2c954fa96ddf2b34904511b123b8.

- [Contact sheet](contact.png).
- [UI results](report.json).
- [Pixel values](pixel-values.json).
- [Source manifest](source-manifest.json).

| Profile | Day | Night |
| --- | --- | --- |
| pw5 | [PNG](captures/pw5-grayscale-256-day.png) | [PNG](captures/pw5-grayscale-256-night.png) |
| pw6 | [PNG](captures/pw6-grayscale-256-day.png) | [PNG](captures/pw6-grayscale-256-night.png) |
| scribe | [PNG](captures/scribe-grayscale-256-day.png) | [PNG](captures/scribe-grayscale-256-night.png) |
