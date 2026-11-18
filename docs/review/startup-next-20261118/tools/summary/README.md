# Compact physical trial evidence

Run the exporter only on `test-env`. Feed it the public reports produced by the
corresponding benchmark analyzers. It does not open private traces, connect to
the Kindle, create a lease, or repeat a measurement. Each batch has its own
record. A ten-pair confirmation may span two independently released five-pair
batches; evaluate its gate over all ten pairs, separately from the screen.
Only application, launcher, native query and known program-helper hashes are
published. An absent report stays pending. A stopped operation is a separate
record, never a zero-duration launch or a replacement for another case.

## Warm regression rule fixed before the extra batch

The cold-thumbnail candidate will also be compared with the installed
`78c5b2b` application on a warm local thumbnail cache. Use six new fixed pairs,
three AB and three BA. A is the installed application, B is the new ELF with
its production defaults; the launcher and native-query helper must match.
Each timed launch must show six derivative hits and no derivative misses. Both
median paired differences (A minus B) must be at least -50 ms: native request
to the first input-ready Library frame and to the fully covered Library frame.
Record individual values, order, intermediate frames and exit. This is a
regression check, not a significance or statistical noninferiority claim. No
case is discarded because it is slow. Warmups and correctness-only openings
are excluded from the paired timing estimates.

The native supervisor screen uses the same application and instrumented
launcher on both sides. The prewarm screen uses the same candidate application
with the optional work disabled/enabled. Neither comparison is the same as the
cold-thumbnail or last-cover trials, which compare the installed application
against a different candidate ELF. Timings are file-URI request to software or
driver acceptance, not optical e-ink timings. The cold workload resets the
private thumbnail cache and keeps the OS warm; it is not a reboot or network
download test.

Example, using only existing public reports (supply fresh reports as available):

```sh
python3 -I export.py --out /path/to/evidence \
  --report supervisor-screen=/root/aidoku-next-bench-20261118/reports/native-screen.json \
  --report prewarm-screen=/root/aidoku-next-bench-20261118/reports/covers-screen.json \
  --report cold-screen=/root/aidoku-next-coldbench-20261118/reports/cold-screen.json
```

The index distinguishes per-version endpoint medians, their difference, and
the median of the actual per-pair savings. The decision rule uses the latter.
With six cases per version this package makes no percentile claim.

For reports from the later cold-thumbnail plus toolbar-status candidate, pass
`--status-app-sha256` with the independently frozen new candidate executable
hash. The exact application must match for each warm, cold and pixel report.
Only the separate status analyzer can establish an adopted toolbar hit and the
absence of synchronous queries during its first Library projection. Previous
cold-only samples are retained under their own version and never transferred.
If the host candidate fails before physical testing, `--status-unavailable`
records the relevant `host_build_failed`, `host_verification_failed` or
`no_qualified_artifact` disposition without creating a synthetic run.

For the deployed candidate, run `refresh.sh` with the full application digest
and then `closeout.py --out <same-dir> --reports <final-status-reports>
--release <status-release-inputs>` on test-env. The closeout adds the verified
default lifecycle, actual installation and residual per-cover costs. It rejects
an incomplete warm regression check, unrelated source identity or unqualified
release. Do not copy private screenshots or raw catalog/transport records.
