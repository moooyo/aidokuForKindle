# Indexed startup evidence, 2026-11-19

The production reference is `c6867d1` (ELF `9d92dc4c...`). The first
integration (R1, `3ef03a41...`) was retired; R2 (`1b1c508e...`) revised
prewarm selection and grace handling and made import preprocessing opt-in.
`r1-index.json` and `r2-index.json` are independent. Never carry earlier
measurements, defaults or qualification across that version boundary. Default
guards compare the exact revision with the installed reference. Isolated
option trials compare the given switch on the **same integrated ELF**
and do not silently substitute November 18's experiments. The independent
physical-default report qualifies program behavior, not a population-wide
speedup. An incomplete/invalid run remains separate from the later screen.

`index.json` points to both version indexes and any distinct observations
of the installed reference. Each timing triple is
`[A, B, A-B]` in milliseconds unless a byte or count metric is explicitly
named. Positive times saved favor B. Each `summary_ms` includes the difference
of endpoint medians, the median of individual paired savings and both order
subsets. Only the paired metric determines the engineering gate. Stage budgets
overlap and must not be added. Warmups, native screenshots and separate short
sleep/wake checks do not enter the timing estimates.

Run the compact exporter only on `test-env`. Pass independently reviewed public
analyzer reports; it never opens a device connection, a private trace, a cache
or a catalog. Output uses a fixed whitelist of numeric timings, outcome counts,
program hashes, and operating flags. Private book metadata, pixels, identities,
paths, token files and per-book hashes are omitted.

```sh
root=/root/aidoku-all-bench-20261119/reports
life=/root/aidoku-all-release-20261119/reports/final-defaults-20126222.json
python3 -I export.py --out /path/to/summary \
  --report defaults-warm="$root/default-warm.json" \
  --report defaults-lifecycle="$life" \
  --report invalid-default-pilot="$root/joint-pilot.json"
```

`sh refresh.sh /path/to/summary` discovers the fixed public report names for
the available phases. If the separately reviewed
`../resident-owner-prototype/native-entry-live-results.json` is available,
include it with `--report installed-gui-entry-observation=PATH`; staging that
public file next to the exporter enables the same selection automatically.

An isolated screen needs at least five of six improvements and a 100 ms
paired median on its designated endpoint, without over 50 ms paired median
regression on the other endpoint. Independent confirmation requires nine of
ten and the same timing thresholds. Default warm and empty-thumbnail-cache
six-pair comparisons are protection checks, not tests of individual settings.
No six-pair tail percentile, network speed, battery or optical latency is
inferred. Import deliberately separates acknowledgment/visible work from idle
processing, storage and subsequent process startup. A keeper hit is reuse of
validated cover pixels by a fresh GUI, not GUI process retention.
