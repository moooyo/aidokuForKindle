# Hardware model admission follow-up

Date: 2026-11-15, Asia/Shanghai.

The user requested that JPEG hardware acceleration availability depend on
supported hardware models instead of an exact system version. The option
remains off by default. This supersedes the original firmware/build gate;
earlier physical results retain their measured environment and source identity.

## Current behavior

- The application and its independent JPEG worker use the same Scribe 2022
  model list: `27J`, `2BL`, `263`, `227`, `2BM`, `23L`, `23M` and `270`.
- Eligibility no longer reads `/etc/version.txt` or
  `/proc/sys/kernel/osrelease`. No firmware, build or kernel version must match.
- Native ARM execution, actual JDEC capability, helper availability,
  diagnostics and exclusive worker ownership are still checked.
- Unsupported models remain disabled. Missing drivers and runtime failures
  still cause software processing. Model identity does not override those checks.
- JPEG format/geometry admission, complete entropy validation, combined
  decode/resize policy and DMA buffer lifetime are unchanged.
- The obsolete firmware-disabled reason was removed from the state mapping
  and all four locales. Available-state copy now describes model support.
- The BiliComics handoff was updated and its implementation chat acknowledged
  the same hardware-model policy.

See the [maintained contract](../../KINDLE_MEDIA_ACCELERATION.md) for all input,
driver and failure-handling requirements. Recorded firmware versions describe
where measurements were taken; they do not control the setting.

## Verification

All checks ran through `ssh test-env`, with Cargo concurrency limited to six.

| Check | Result |
| --- | --- |
| Kindle library | 783 passed; 5 existing ignored |
| Default library | 768 passed; 5 existing ignored |
| Application binary tests | 20 passed |
| Desktop hardware settings | 10 passed across Paperwhite 5 and Scribe profiles |
| Desktop and ARM application/helper builds | Passed |
| Package, source identity and static ARM checks | 162 passed |

Focused tests cover supported model codes without system-version metadata,
malformed/other model identities, retired or unknown worker status values,
default-off behavior, preference persistence and failure fallback. The desktop
UI matrix exercises disabled hit areas, four locales and portrait reentry.

No physical-device operation was performed for this eligibility follow-up.
The codec transaction implementation was not changed; the earlier
[Scribe investigation](../../SCRIBE_MEDIA_QUALIFICATION_20261115.md) and native
Reader evidence remain preserved. This record does not establish measured
performance on untested firmware releases.

## Artifacts and evidence

The uncommitted source is based on
`a53b9716d64fe5521fac38431b24732c75cb8ff7`. Its 334-file production fingerprint
is `a1227f0e8f5041d4fb7570847e1f7b5810f24ee411f2384b70a473312249c264`.

- [Source manifest](source-manifest.json).
- [Delivery and test summary](delivery-summary.json).
- [Package verification](package-verification.json).
- [Actual desktop UI results](desktop-ui-verification.json).
- [Kindle focused test outcomes](kindle-tests-focused.txt).
- [Default focused test outcomes](tests-focused.txt).
- [Frozen supplemental UI captures](../../uiux-baselines/2026-11-15-a53b9716-media-model-policy/README.md).

The development archive is `aidoku-media-model-policy.zip`, SHA-256
`7720efe6e87d433b4dc63c8d8d08ebb4e303098486f10cdce46af954b5249f34`.
Earlier archives are retained with their original identity. The updated
application has not replaced the installed Kindle application.
