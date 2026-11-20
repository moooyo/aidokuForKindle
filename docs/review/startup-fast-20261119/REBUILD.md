# 2026-11-20: startup verification after the worker rebuild

The worker is reachable. The old R3 builder layers, retained program and
benchmark directories, stock view references and recovery records are absent.
The [new host lane](operator/clean-worker/README.md) shares its compiler and
registry with WeRead and BiliComics, but not project targets or test data.
No Kindle was contacted during these host checks. The [separate physical
follow-up](PHYSICAL.md) records later leased operations.

## What changed

The first release cross-build used the unchanged product files from Git
`61fe3e1329bb4da22ca4301b20cb8eb3965f987d`. It **failed** compiling
`cover_neon.c`: the cargo-zigbuild ARMv7 wrapper explicitly disables NEON and
the upper VFP registers. `-mfpu=neon` alone did not undo those settings.
Enabling only NEON still failed in the LLVM backend. A small compiler probe
with both `+d32` and `+neon` produced an ARMv7/VFPv3/NEONv1 object without
requiring VFPv4 or fused multiply-add.

The corrected tree starts from the same Git archive with a single product
change in `build.rs`: override those two Clang target features **only** for
the cover kernel object. The Rust executable's target, runtime HWCAP check,
scalar fallback, FP contraction policy and launcher are unchanged. Keep the
failed original and corrected results separate.

## Verification records

Use the rebuild directory listed in the private `test-env` handoff. Paths
below are relative to that directory: `source/` is the unchanged product,
`source-neon/` the corrected copy. `reports/neon-build.patch`
is the applied product diff. `arm-build.log` and the `neon-*-probes.log` files
under `reports/` preserve the original failure and its diagnosis.

| Independent check | Result |
| --- | --- |
| Corrected ARM build | The app is a stripped, static ARMv7 EABI5 hard-float ELF; the portrait helper is a dynamic ARM EABI5 PIE. The launcher was copied from the same corrected snapshot; no install or release package. |
| Final NEON kernels vs scalar VFP under QEMU | The application build's own kernel archive matched all 16,777,216 RGB triples, 128 tail lengths and 1,400 weighted-row cases (2,798,527 float sums). `reports/arm-neon-fixed.log` records both the successful compilation and parity. |
| ARM program with NEON disabled | `--version` and `--print-startup-options` passed with `-cpu cortex-a7,neon=off,vfp-d32=off`. The first probe disabled NEON alone; QEMU rejected that CPU configuration before starting the ELF. Neither probe covers a full no-NEON GUI launch. |
| Integrated Rust suites, Kindle features on x86_64 | **856** library tests passed (6 ignored); **23** application tests passed, including `in_flight_resources_never_publish_the_library_or_activate_normal_work`. |
| PW5 dark-preference sleep screen | **Failed** at the original bounds assertion: 618×824 vs the expected 549.33×824. Comparing the new light/dark PNGs found identical artwork in `(34, 0, 584, 824)` and white vs `#111` margins, as in the [earlier control](../startup-all-20261119/build/sleep-baseline-control.json). Later sleep scenes were not reached. The full host test command is **not green**. |
| Desktop release and startup settings | The first two profile-entry cases failed before application startup because winit could not load `libXcursor`; retained under `reports/settings-ui/`. After installing `libxcursor1` in the shared userspace, the **same executable** passed all 29 cases / 793 checks: PW5 15/408, Scribe 14/385 (`reports/settings-ui-x11/`). No product change or relaxed assertion. |
| Final launcher lifecycle suite | 91 passed, including the final process-identity regression; `reports/launcher.log`. |
| Instrumented Library launcher, host services | The fake child received `AIDOKU_MANAGED_LIBRARY=1`, the staged data directory and trace path. Worker/entry markers, ordinary exit and fixture restoration passed; `reports/managed-entry.log`. |
| Native portrait supervisor fixtures | 16 passed. |
| Tile operation and evidence fixtures | 6 passed; synthetic, no device. |
| Timing analyzer fixtures | 8 passed, including frame/input boundaries and independent body/right-toolbar checks. Three retained-run tests are not included. |

`programs/` retains the corrected ARM and desktop executables, launcher, portrait
helper and linked kernel archive for follow-up. The private Cargo build target,
partial duplicate tool installation and temporary parity executable were removed.
The common tools and registry were left intact.

The private R3 replay, stock tile controller tests and warm/empty-thumbnail
device plans cannot be reconstructed from public result JSON. Restore the
private lab or agree on a new study, then freeze the **corrected** product
sources and build a new matching candidate before freezing any device plan.
No new plans were issued. The last verified installation remains R3; nothing
here is a release receipt or evidence of a faster first screen.
