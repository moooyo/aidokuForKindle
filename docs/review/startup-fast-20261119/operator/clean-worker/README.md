# Startup checks on the rebuilt worker

The original `test-env` was rebuilt, not just rebooted. This lane tests
product commit `61fe3e1` from Git. It does **not** recreate the old release
receipt, compare Kindle performance, or authorize an installation.

## Shared tools

Run on `ssh test-env`. WeRead, Aidoku and BiliComics share **one** Rust/Cargo
1.99.1 installation, the ARMv7 musl hard-float target, Zig 0.15.2,
cargo-zigbuild 0.24.0, a Cargo registry and a Zig cache. The common Ubuntu
24.04 userspace includes GCC, Perl, Pillow, Xvfb, xauth, xdotool, `libxcursor1`
for winit and QEMU user mode. Its installed entry is [ubuntu-run](ubuntu-run):

```sh
/opt/kindle-toolchain/ubuntu-run "$project" rustc --version
/opt/kindle-toolchain/ubuntu-run --build "$project" sh -ec \
  'cd source; cargo build -j6 --locked --release --bin aidoku'
```

`project` is an existing, project-owned directory on the worker. Inside the
runner it is `/work`, the working directory. `HOME` is `/work/.home`;
`CARGO_TARGET_DIR` is `/work/target`. The source, target, reports and programs
belong to the project, not the shared tool installation. `--build` holds
`/opt/kindle-toolchain/build.lock` on the host until the command exits; **do
not take that lock again** inside the command. Use it for heavy builds across
all three projects. Set build-specific environment variables *inside* the
runner. The runner mounts the shared tools, cache, environment file and lock
at their actual paths, never the parent containing the userspace. Ordinary
runs use a read-only system root, fresh PID/mount namespaces and temporary
filesystems, with no Kindle devices or SSH agent mounted.

## Candidate checks

Use a fresh worker directory and the `git archive` product file list in the
[original instructions](../README.md#freeze-and-build). Extract the archive
of `61fe3e1` into `source/`; do not substitute the current working tree.
That exact snapshot failed its ARM build. For the [corrected run](../../REBUILD.md)
make a second copy, `source-neon/`, and apply only the reviewed `build.rs`
delta. Preserve the original and its failure log.
Extract `fa4c7ee:docs/review/startup-fast-20261119/operator/bench` into
`bench/` for the analyzer tests, and keep new operator scripts outside the
product snapshot. For the release comparisons below, set
`CARGO_PROFILE_RELEASE_LTO=false` and `CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16`.
Run the following from the selected product source directory, under the shared
build lock:

```sh
cargo zigbuild -j6 --locked --release --no-default-features --features kindle \
  --target armv7-unknown-linux-musleabihf --bin aidoku
sh scripts/build-startup-portrait.sh /work/programs/aidoku-startup-portrait
sh /work/check-cover-parity.sh
cargo test -j6 --locked --release --no-default-features --features kindle \
  --lib --bin aidoku --test sleep_screen_ui --no-fail-fast -- --test-threads=6
cargo build -j6 --locked --release --bin aidoku
```

Copy [check-cover-parity.sh](check-cover-parity.sh) to the project root first.
It links the scalar reference against the one NEON archive from this ARM
build, runs it under QEMU, and deletes the temporary probe. It does not time
the panel or run the Kindle application on hardware. Record each command
separately so a known failure does not hide an unrun suite. The launcher
and portrait fixtures need only `python3 packaging/tests/launcher_test.py`
and `python3 packaging/tests/startup_portrait_supervisor_test.py`. The
separate [managed-entry check](check-managed-entry.py) takes the product
directory and the eight-file bench directory as arguments. It observes the
instrumented launcher's fake child, isolated data path, trace arguments and
restoration; it does not dispatch the real stock tile. The
two-profile UI command is `python3 scripts/verify-startup-settings.py
--binary /work/target/release/aidoku --output-dir /work/reports/settings-ui`.

## Device boundary

The old build overlays, installed R3 program copies, sealed warm/cold data,
original Home and Library views, guarded input helper, historical raw runs,
and private transport/ownership records are not a Git checkout. A rebuilt
binary, a public timing summary, synthetic pixels, or an empty lease location
cannot replace them. The original tile mode tests, retained analyzer replays
and a new frozen physical comparison require the private inputs. Restore
them from a trusted backup, or define and authorize a separate new study.
Do not open the Kindle to fill the gap, reuse old plans, or report a speedup.

The separately authorized [physical follow-up](../../PHYSICAL.md) does not
reconstruct those missing inputs. For the CLI batch, stage the corrected app
as `aidoku-arm`, the scalar parity executable linked with its NEON archive as
`cover-parity`, and the Git R3 launcher as `r3-launch.sh`. Put exactly these
three files into `hardware-smoke.tar.gz`. Keep the reviewed BiliComics
`scripts/verification/device_lease.py` on the **host**, not in the chroot.
The [smoke coordinator](run_hardware_smoke.py) takes explicit worker/lease
tool, alias, trusted SSH configuration, pinned hosts file and owner arguments.
It labels the common owner record Aidoku; it neither installs a second lock
nor accepts a new host key. Credentials and private token files are not outputs.

The [paired foreground study](staged-pilot/README.md) is a different batch.
For off-device replay of its operator tools, keep the tracked `bench/analyze.py`
two directories above `staged-pilot/` in its `bench/` directory. Run the tests
in the shared userspace. Supply the `mock-mnt` namespace only when freezing
data: the desktop importer writes the future absolute paths into the synthetic
database, without referring to a real device or user library.
