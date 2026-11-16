# Hardware model admission candidate captures

Captured on 2026-11-15 through ssh test-env, from an uncommitted candidate based
on a53b9716d64fe5521fac38431b24732c75cb8ff7.

The JPEG option now uses supported hardware models and actual driver capability.
Firmware, software-build and kernel version strings do not gate availability.
The default remains off. The unavailable-firmware reason has been removed;
the available-state copy describes model support without claiming that every
system release has been physically tested.

All ten desktop checks passed across Paperwhite 5 and Scribe profiles, including
disabled pointer interactions, four locales and portrait settings reentry.
These are desktop UI results. No new physical-device trial was run for this
eligibility-only follow-up; earlier codec and native Reader evidence remains
preserved in its original source-bound reports.

Production source SHA-256: a1227f0e8f5041d4fb7570847e1f7b5810f24ee411f2384b70a473312249c264.

Desktop executable SHA-256: b3c7708282774a5b16d01995016e5b49cd435f062499bb1ee97fd12348217265.

- [Contact sheet](contact.png).
- [Actual UI checks](report.json).
- [Source manifest](source-manifest.json).

| Scene | Paperwhite 5 | Scribe |
| --- | --- | --- |
| hardware-disabled | [PNG](desktop/pw5-hardware-disabled.png) | [PNG](desktop/scribe-hardware-disabled.png) |
| hardware-en | [PNG](desktop/pw5-hardware-en.png) | [PNG](desktop/scribe-hardware-en.png) |
| hardware-ja | [PNG](desktop/pw5-hardware-ja.png) | [PNG](desktop/scribe-hardware-ja.png) |
| hardware-zh-TW | [PNG](desktop/pw5-hardware-zh-TW.png) | [PNG](desktop/scribe-hardware-zh-TW.png) |
| hardware-portrait-reentry | [PNG](desktop/pw5-hardware-portrait-reentry.png) | [PNG](desktop/scribe-hardware-portrait-reentry.png) |
