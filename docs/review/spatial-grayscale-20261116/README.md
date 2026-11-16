# Spatial grayscale research evidence

The [research report](../../SPATIAL_GRAYSCALE_RESEARCH_20261116.md) explains the
test method, result interpretation and recommended integration.

- `fixture.json`: 256 synthetic patches and their 64 x 64 sample interiors.
- `period-analysis.json`: Bayer4 and Bayer8 complete-period arithmetic.
- `host-analysis.json`: software reference and phase checks on test-env.
- `native-results.json`: all Scribe post-MDP patch means, source hashes,
  quantized-pixel preservation checks and native CPU samples.
- `scribe-cpu-timings.json`: seven interleaved samples after one warmup.
- `images/`: lossless normalized post-MDP outputs and the original input.
- `comparison.png`: native ramp crops at original pixel density and a 3x
  nearest-neighbor enlargement of a constant patch. No blur or contrast boost.
- `summary.json`: exact research source/binary identities and restoration scope.
- `manifest.json`: hashes of the exported measurements and image artifacts.

Raw driver dumps, transport configuration and original private logs remain on
test-env. No credentials, raw lease tokens or private device identifiers are
included here. The chart and image crops are synthetic and contain no user
reading content. No camera luminance calibration was performed.
