# Resume the startup comparison

**No device access is needed for preparation.** Start in a fresh checkout
of `main`, read the root [handoff](../../../../HANDOFF.md) and [repository
instructions](../../../../AGENTS.md). Use the existing `test-env` worker;
this Git directory contains operator source, not a copy of the lab.

**After the 2026-11-20 worker rebuild:** the retained lab directories are
absent. Use the [clean-worker procedure](clean-worker/README.md) for independent
host checks. The historical procedure below requires the original private
inputs; do not make empty directories to get past its preflight.

## Worker prerequisites

- Confirm `ssh test-env` is reachable. The isolated build root is
  `/root/aidoku-launch-fast-20261119`; it requires the hash-pinned R3 builder,
  source, toolchain, shared build lock and overlay layers named in
  [integration.py](build/integration.py) and [run.sh](build/run.sh). Check
  the existing worker `tools/{integration.py,run.sh}` against these tracked
  copies. Restore missing copies from Git; do not overwrite different frozen
  tools or use another project's target directory. Cargo concurrency stays at six.
- The [bench tools](bench/) require the R3 ELF, launcher and helper, the
  previous warm/cold harnesses, and the private fixture/lease/transport inputs
  on the worker. The native tile also requires the retained stock view
  references and guarded input helper under `/root/aidoku-entry-study-20261119`.
  These inputs are deliberately not in Git. Missing inputs are a stop condition,
  not permission to guess coordinates, skip a check or recreate a token.

## Freeze and build

From the repository root in a Bash shell, the following transfers **committed
bytes only** (no ignored archive required). Choose a never-used label. If a
transfer is interrupted, preserve its partial file and choose a new label.

```bash
set -euo pipefail
candidate=61fe3e1329bb4da22ca4301b20cb8eb3965f987d
label="r4-$(date -u +%Y%m%d%H%M%S)"
build=/root/aidoku-launch-fast-20261119
git archive --format=tar.gz "$candidate" Cargo.toml Cargo.lock build.rs LICENSE \
  THIRD_PARTY_NOTICES.md README.md AGENTS.md src ui assets vendor examples \
  tests packaging scripts |
  ssh test-env "umask 077; dd of='$build/incoming/$label.tar' conv=excl status=none"
ssh test-env "cd '$build' && python3 -B -I tools/integration.py freeze '$label' 'incoming/$label.tar'"
ssh test-env "cd '$build' && LIBMOUNT_FORCE_MOUNT2=always sh tools/run.sh '$label' arm"
ssh test-env "cd '$build' && LIBMOUNT_FORCE_MOUNT2=always sh tools/run.sh '$label' host"
```

The alternative [snapshot.sh](build/snapshot.sh) transfers *working-tree*
bytes and uses macOS BSD tar flags. Use it only when intentionally measuring
a new source tree. Record the freeze manifest, exact program hashes, test
outcomes and any known failures. Run the final launcher tests, the [ARM pixel
parity test](../../../../scripts/tests/cover-neon-parity.c), and both desktop
profiles before considering a physical run.

## Prepare the comparison

Stage all eight `.py` files in [bench/](bench/) **together, unchanged**, in a
fresh private tool directory on `test-env`. The analyzer checks sibling and
upstream tool identities, and some tests need historical private observations.
Use `freeze.py --check-only --entry uri --workload both` and the corresponding
tile preflight, then run `test_analyze.py`, `test_tile_mode.py` and
`test_tile_evidence.py` on the worker. A dry plan cannot be used on the
device. In particular, final tile-analyzer QA was interrupted at handoff.

For an eligible candidate, freeze a new `--entry tile --mode screen
--workload both` comparison using `--candidate-app` and `--candidate-launcher`
from the **same build receipt**; verify the portrait helper as well. If the
native tile cannot be identified, an explicitly labelled `--entry uri` study
is a different measurement. Never combine the two. The freezer defaults to
`uri`, so spell out the entry. Only the two-pair pilot and six-pair screen
are implemented in that historical indexed-entry freezer. After the rebuild,
the [separate staged study](../../startup-followup-20261121/README.md) implemented
the ten-pair empty-cache confirmation; its timestamps are not indexed-entry timings.

Do not take the Kindle lease until all host work is ready. If the worker
rebooted, resolve any previous owner/recovery before touching the Kindle.
One operator runs each fresh plan, restores the temporary launcher and native
state, then releases with owner/token checks before offline analysis. Neither
merging this source nor passing a screen authorizes a daily-driver update.
