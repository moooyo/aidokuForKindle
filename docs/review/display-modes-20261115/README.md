# Sanitized display and media evidence

Date: 2026-11-15. All execution and analysis used `ssh test-env`.

- `native-display-mode-results.json` retains accepted request fields and final
  application state for four isolated Scribe runs. Its full-size MDP section
  is negative pixel evidence despite successful ioctl completion.
- `y4-output-comparison.json` compares identical-input off/on/off post-MDP
  images and confirms a real digital Y4 effect.
- `media-pixels-01.json` retains earlier small-image copy, resize, rotation and
  RGB-to-gray comparisons. The first full-size saved output was truncated.
- `exact-source-479432.json` identifies the Amazon source archive and reviewed
  kernel files. `mdp-library-interface.txt` lists installed MDP library exports;
  it is not a matching C++ header or a successful direct-library call.
- `probe-contract-checks.json` and `final-probe-checks.json` record offline ABI,
  codec rejection and contract-model checks. At that initial stage, the hardened
  MDP probe had been compiled but had not yet been used for another physical run.
- `verification-summary.json` records completed worker checks and scoped
  restoration/release evidence. Raw credentials, tokens and device identifiers
  remain in private worker storage outside the repository.

The records above describe the initial display-preset qualification. The
user subsequently requested corrected physical codec tests and a same-picture
CPU/hardware comparison. Added follow-up records are:

- `corrected-codec-results.json`: scoped JPEG/PNG trials, packet-tail correction,
  GREY/BGRA acceptance and default-NV12 failures.
- `corrected-mdp-results.json`: exact identity and failed full-size scaling,
  including the comparison with native UI writers parked.
- `native-media-benchmark-results.json`: final paired CPU/JPEG-IMGRZ timings;
  `native-media-benchmark-results-first.json` retains the earlier complete run.
- `benchmark-runs/`: exact sampled timings, quality metrics and reference
  metadata. `benchmark-fixtures/` contains the same synthetic input scene.
- `benchmark-comparison.png`: CPU output, hardware output and amplified
  difference, left to right; logical and native output sizes, top to bottom.
- `final-codec-contract-checks.json`: eleven checks of the final C probe,
  including post-header allocation, packet tails, scaling and benchmark guards.
- `native-media-benchmark-as-run.rs`: the exact benchmark controller source
  used for the measured ARM executable. The current example only received
  whitespace formatting afterward; the production scaler source was unchanged.

See `../../SCRIBE_MEDIA_BENCHMARK_20261115.md` for the calling recipe and limits.
No optical result, full-page latency ranking or enabled production media
acceleration is claimed. The application still uses its software pipeline.
