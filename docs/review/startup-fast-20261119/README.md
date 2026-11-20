# Library startup candidate: qualification pending

**The startup candidate is not installed as the daily driver.** New
[physical staged-foreground pilots](PHYSICAL.md) report both improvements and
regressions. The [November 21 independent confirmation](../startup-followup-20261121/README.md)
found a 681 ms empty-cache cover saving in ten new pairs. These studies do
not measure a tap on the stock Library tile. The [November 21 native tile
comparison](../startup-native-20261121/README.md) records separate ordinary
opens and the failed visual qualification. The [November 19 R3 baseline](../startup-entry-timing-20261119/README.md) measured
about 2.15 s from pressing its native Library tile to book text, and 2.52 s
to all visible covers on repeated visits. An isolated R3 test without local
thumbnails took 8.20 s from an indexed open *request* to complete covers;
its starting event is different. See the [prospective trial rules](PLAN.md).

## Changes to test

- On an ordinary managed Library launch with cover prewarming off, start
  preparing dormant services and font bytes after the first native lifecycle
  check. After a successful full-screen submission, register already-delivered
  font faces while the panel updates. Do not wait for missing bytes, admit
  input, run the controller, apply native lighting, or report a Library frame
  at that point. A pending stop or sleep precludes speculative work. Other
  routes and the existing prewarm choice keep their previous scheduling.
- Overlap the power observer's startup with generation bookkeeping. Still
  require the owned observer and its source process before the first native
  orientation read or display takeover. Check PID, process state and parent
  in one kernel snapshot instead of spawning two shell substitutions.
- For an already verified processed cover, check the unchanged file identity
  and bind its hashed alias directly against the valid indexed checksum. An
  existing correct binding is read-only. Missing verification or a changed
  fingerprint falls back to the full validation path. This avoids reopening
  the same derivative just to confirm its alias.
- For eligible missing local covers, retain both the SHA-256 of the bytes
  actually decoded and the final check of the named file, metadata and digest.
  Remove the redundant intermediate reread. Across the six retained local
  originals this removes one pass over 7,533,567 encoded bytes (7.18 MiB),
  not 7.18 MiB of reduced peak memory or a time saving measured on the device.
  ARM Linux devices advertising NEON can also vectorize the RGB8-to-gray
  conversion and the vertical Lanczos accumulation. Other CPUs keep the
  scalar path; the Reader, bounded large-PNG path and on-disk pixel format
  are unchanged. Stage markers separate decoding, luminance, resize and
  quantization for the next physical profile.

## Verification state

The table below is the original pre-rebuild handoff. See the [2026-11-20
worker report](REBUILD.md) for the integrated cross-build failure, corrective
change and subsequent independent checks. They use a fresh target and do not
replay the old private trial.

| Scope | Last observed result on `test-env` |
| --- | --- |
| R3 control identity | Original product and build inputs matched the retained R3 baseline before the worker outage. |
| Cache and import changes (isolated `alias-a`) | 854 library tests passed, 6 ignored; 22 application startup tests passed. Includes unchanged binding, reassignment, modified/corrupt target and cache clear. The separate known PW5 dark-sleep-cover assertion failed; the full command was not green. |
| Earlier launcher snapshot | 90 lifecycle tests passed. The final PID/trap hardening and additional regression test were written afterwards and are not covered by that count. |
| Scalar vs ARM vertical kernel prototype | 1,400 cases, approximately 2.94 million pre-rounding sums matched bit-for-bit. This is not a test of the final linked product. |
| Complete final candidate | Worker restarted, then `ssh test-env` repeatedly timed out or reported no route. Final host/ARM build, full RGB/kernel parity, managed startup test, independent pixels, and physical A/B are **pending**. No candidate run, incomplete timing sample, deployment or device lease is outstanding from this work. |

The [operator tools and clean-checkout instructions](operator/README.md) are
tracked. A source-only transfer archive was also kept in the original
checkout's ignored `artifacts/` directory; it is **not** needed to resume and
is not an executable or installable package. The separate
host-only dry plans used the control on both sides to check the harness and
cannot be used as evidence for the candidate. Do not run or recycle them.

## Resume on the worker

1. Once `ssh test-env` is available, use a fresh label and the isolated
   builder at `/root/aidoku-launch-fast-20261119`. Freeze the committed
   candidate `61fe3e1` as described in the [operator instructions](operator/README.md).
   Never overwrite a previous build or plan or silently substitute a newer tree.
2. On the worker run `LIBMOUNT_FORCE_MOUNT2=always sh tools/run.sh LABEL arm`
   and `LIBMOUNT_FORCE_MOUNT2=always sh tools/run.sh LABEL host` with the
   shared six-job lock. Run the final launcher tests and the independent
   [ARM/scalar parity test](../../../scripts/tests/cover-neon-parity.c).
   Check receipt, source identities, both desktop profiles and sleep/exit
   behavior. An inherited test failure is not a new successful qualification.
3. Use the [tracked benchmark scripts](operator/bench/) with the retained
   inputs on `/root/aidoku-startup-perf-r4-bench-20261119`. Recheck the new tile-mode fixtures and
   analyzer on the worker first: those checks were interrupted. Freeze **new**
   matched, isolated warm and cold plans bound to the exact candidate ELF,
   launcher, helper and source receipt. If a staged route does not propagate
   `AIDOKU_MANAGED_LIBRARY=1`, it cannot qualify the managed-only font change.
4. A single operator acquires a **fresh** verified lease immediately before
   each continuous device batch, restores the installed entry and owned
   native state and releases before analysis. After an unexpected worker
   reboot, absence of the old `/tmp` lease alone is not evidence that an
   interrupted session restored the device. Resolve outstanding ownership
   before starting anything new. Keep all private data on the worker. Report
   every pair and any failed gate before considering promotion.
