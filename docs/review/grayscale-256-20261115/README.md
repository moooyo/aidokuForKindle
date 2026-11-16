# 256-swatch grayscale test

Date: 2026-11-15, Asia/Shanghai.

Screen-refresh settings contain a horizontal test strip with exactly 256
adjacent swatches. Swatch `index` uses `rgb(index, index, index)` for every
integer from 0 to 255. The label states 256, and the endpoints are marked
0 and 255. Integer boundaries prevent gaps, overlaps and blended edges.
The swatch colors remain fixed in both interface themes.

The test is implemented in [GrayscaleTest](../../../ui/grayscale-test.slint).
It is enabled only by the settings instance of `RefreshOptions`, preserving
the existing Reader panel's height and hit areas. The UI draws its input
directly, without passing through JPEG/PNG decoding or the comic-image
16-level software quantizer. The settings scene does not enable the
Reader-specific Y4 policy; this is not a measurement of optical grayscale.

## Verification

All builds and checks ran through `ssh test-env`, with Cargo concurrency six.
Desktop and ARM application/helper builds passed. Eight UI cases passed:
six day/night settings cases across Paperwhite 5, Paperwhite 6 and Scribe,
plus two Reader-panel regression checks. The captures also cover English,
Traditional Chinese and Japanese labels.

Each settings capture was checked against its actual rendered pixels:

- All 256 input values are present in the bar interior.
- There are exactly 256 monotonically ordered constant-gray runs.
- Red, green and blue channels agree for every interior pixel.
- Every interior row has the same swatches, with no missing endpoints.

The package passed 162 archive, source-binding and static ARM checks. No
physical-device operation or installed-application replacement was performed.
Existing Rust suites were not repeated for this UI-only change.

The later [2026-11-16 deployment](../kindle-deployment-20261116/README.md)
installed this exact archive on the physical Scribe and completed native settings
verification. The build-time results above retain their original scope.

## Evidence

- [UI verification](report.json).
- [Per-case pixel values and swatch counts](pixel-values.json).
- [Capture/check script as run](capture-and-check-as-run.py).
- [Frozen screenshots, state, geometry and replays](../../uiux-baselines/2026-11-15-a53b9716-grayscale-256/README.md).
- [Source manifest](source-manifest.json).
- [Package verification](package-verification.json).
- [Development bundle identity](delivery-summary.json).

The uncommitted source is based on
`a53b9716d64fe5521fac38431b24732c75cb8ff7`. Its 335-file fingerprint is
`ffac5ba3b0166a1dae90d9a8240d4f9616915259da8a4223a939bd2261e23f80`.
The archive is `aidoku-grayscale-256.zip`, SHA-256
`49b30748824d995f856e9c30de340aeb872cabcd79b67117078094ae9637929f`.
