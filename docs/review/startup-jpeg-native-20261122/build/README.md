# Matched ARM programs for the ordinary-card JPEG study

These two development programs isolate the cost of the eager JPEG capability check. Neither is the installed R3 binary. The build and QEMU checks ran on `test-env`, without connecting to a Kindle or acquiring a device lease.

| Study arm | `Platform::new` | Bytes | ARM release compile time |
| --- | --- | ---: | ---: |
| A, control | Check JPEG after obtaining display geometry and before constructing the platform | 53,732,576 | 1 min 20 s, warmed target |
| B, candidate | Leave the capability unchecked until a consumer needs it | 53,732,864 | 2 min 36 s, fresh target |

The durations are **compiler wall time**, not measurements of either startup path. Both arms carry the same Settings/Reader implementation, submission callback, libraries, assets and CLI. No runtime study flag was added.

## Frozen inputs and build

The source was the November 22 working tree based on `1976df8f66a2`, **including** its uncommitted product changes. A Git archive of that commit alone is not this input. The transfer used the tracked and nonignored working files for the manifests, build script, licenses, product, UI, assets, vendor libraries, tests, examples, build scripts and Kindle packaging (479 files). No device state or account data was used. The candidate working source was compared directly with this frozen copy before compilation. For the control, a full-tree comparison allowed only `src/platform.rs`; its line diff was exactly:

```diff
         } else {
             profile.native_size()
         };
+        crate::native_media::initialize(kindle);
         Ok(Self {
```

The shared Ubuntu build runner held its build lock for the sequential B then A builds. Both used the same absolute source directory, target directory and Cargo settings; each output was copied separately before reuse of the target. Both programs are mode `0755`. The build ran from 02:34:20 to 02:38:18 Asia/Shanghai on November 22, 2026.

```sh
export CARGO_BUILD_JOBS=6 CARGO_INCREMENTAL=0
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
cargo zigbuild --offline --locked --release --no-default-features --features kindle \
    --target armv7-unknown-linux-musleabihf --bin aidoku -j6
```

Toolchain: rustc/Cargo 1.99.1, cargo-zigbuild 0.24.0 and Zig 0.15.2. The study changes only the main executable; it does not build or replace the installed JPEG worker or launcher.

## Off-device qualification

[The generated result](qualification.json) records both programs. `readelf` reported static, little-endian ARMv7, EABI5, hard-float programs. QEMU user-mode 8.2.2 returned `aidoku 0.1.3` from both with the default CPU and with `-cpu cortex-a7,neon=off,vfp-d32=off`; `--print-startup-options` output was byte-identical. Each executable contains the `startup_library_submitted` prefix.

To check the actual formatter rather than synthesizing an expected log from a shell script, each build's own release Rust library was retained immediately after compiling the corresponding binary. The [fixture](marker_probe.rs) links against that library and invokes `reader_timing::startup_library_submitted` under QEMU with a known frame checkpoint. The [verifier](verify_arms.py) requires exactly one line on stderr, no stdout, a positive boottime timestamp, the requested sequence/marker, and the correct layout/shelf/grid fields (large populated, large filtered, compact empty).

This does **not** exercise the Kindle framebuffer, qualify a UI projection, produce an actual driver submission or measure a card launch. Only the actual study can establish a normal native card release and the first qualifying frame in the same visit.
