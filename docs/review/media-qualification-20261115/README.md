# Media research and production verification evidence

The requested research and production integration are complete. The current
policy is documented in `../../KINDLE_MEDIA_ACCELERATION.md`. Failed experiments
remain preserved; a valid-looking image does not override a kernel DMA fault.

- `format-results.json` contains 44 physical format, scaling and boundary
  observations with the expanded IOMMU-aware kernel check. The older
  `format-results-before-expanded-audit.json` is superseded and retained to
  explain the earlier incomplete detector.
- `reader-final-results.json` contains ten actual Reader API cases using the
  final automatic policy. Unsafe/unqualified inputs, cropping and unchanged
  sizes made zero decoder-worker attempts. Every case restored native state
  and released its lease without new critical kernel errors.
- `reader-integration-results.json` also preserves the initial forced-decoder
  comparisons. Their measured slowdown motivated the automatic fallback.
- `native-media-ui-final-verification.json` records the exact final ARM
  application's real switch click, persisted restart and imported JPEG Reader
  use. Full digital captures are in the dated UI baseline directory.
- `png-followup-benchmark-results.json` records the slower physical PNG pipeline.
- `png-key-recovery-and-fault.json` records confirmed ARM page-domain failure
  and completed recovery. `png-color-key-disconnect.json` retains the initial
  interrupted batch; its historical pending fields are superseded by recovery.
- `expanded-kernel-fault-audit.json` and `all-media-dma-fault-audit.json` explain
  withdrawal of full-size color 4:2:0 admission, including the older GREY case.
- `final-admission-checks.json` contains 28 intercepted checks of the current
  shared C transaction. All video opens/ioctls were intercepted. Rust unit
  tests additionally validate complete entropy before hardware submission.
- `worker-normalization-checks.json` compares the actual C worker conversion
  against independent conversions of retained physical planes and checks
  output canaries without opening a device.
- `verification-summary.json`, `delivery-summary.json`, `source-manifest.json`
  and `package-verification.json` bind verification and the two-executable bundle.
- `png-reference-checks.json`, `yuv-reference-checks.json`,
  `yuv-scale-reference-manifest.json`, `yuv-grayscale-policy-comparison.json`
  and `yuv-zune-grayscale-comparison.json` are worker-side reference/analysis
  results, not new physical trials or physical timings.

The remaining preparation, queue-mock and probe files retain their own earlier
scope and hashes. `fixture-manifest.json` is a preparation record, not a support
allowlist. `current-guarded-probe.json` describes its earlier preparation point;
current application identity is in `delivery-summary.json`.

`png-key-as-run/` contains the retired vulnerable probe and synthetic trigger
for source analysis only. Never execute that retired probe. The current
application exposes only its JPEG worker entry, with stricter admission.
Private credentials, raw lease tokens, full serials, complete kernel logs and
raw device-stage archives are not included here.
