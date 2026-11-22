# Library transition: ARM build and emitter qualification

This batch uses one JPEG-on-demand application with `startup-stage-diagnostics` enabled. It retains the previous normal `startup_library_submitted` and `startup_stage_diag` records. The new `startup_transition` record identifies the **same** first Library submission by its sequence, marker and boottime; its costs are for that frame, not for the preceding startup frame. The build does not change an installation or establish device performance.

## Source and binary

The private `test-env` workspace started with the actual working tree (479 product/build files), including the prior stage logger. A direct full-tree comparison against the final build source found the six intended deltas: `src/app/startup.rs`, `src/bootstrap_fonts.rs`, `src/bootstrap_metrics.rs`, `src/bootstrap_startup.rs`, `src/platform.rs` and `src/reader_timing.rs`. No other product inputs, Cargo manifest/features, launcher, or vendor backend differed. The [source seal](source_seal.sh) stopped compilation before the application crate until these changes were ready. Dependency preparation and the single ARM application build used the same private target and the shared build runner.

```sh
export CARGO_BUILD_JOBS=6 CARGO_INCREMENTAL=0
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
cargo zigbuild --offline --locked --release --no-default-features \
    --features kindle,startup-stage-diagnostics --target armv7-unknown-linux-musleabihf \
    --bin aidoku -j6
```

The release binary is **53,755,296 bytes**, `0755`, a static little-endian ARMv7 hard-float EABI5 ELF. Rust/Cargo 1.99.1, cargo-zigbuild 0.24.0 and Zig 0.15.2 were used. The final application compiled in 1 min 23 s on November 22, 2026. No extra runtime helper or environment variable was introduced. Both the ordinary and no-NEON QEMU CLI runs returned `aidoku 0.1.3`; Scribe and PW6 both printed startup options without diagnostic noise.

## What QEMU checked

The [probe](transition_probe.rs) links the final ARM release library and invokes its production callback functions. The [verifier](verify_transition.py) runs each scenario in an independent QEMU process and validates the exact ordered records. The [raw qualification result](qualification.json) contains the resulting lines. When present, the Library submission timestamp must be identical across all three lines; the sequence and marker must match between the first and third. A valid zero duration must not be converted to a missing cost.

| Supplied callback scenario | Required output |
| --- | --- |
| Scribe, populated Library | Large, nonempty projection; all supplied milestones; exact frame costs 19 / 0 / 3 / 2 µs |
| PW6, empty Library | Compact, empty projection; same checks, with no cover dependency |
| Missing valid initial full frame | Initial and input-fence timestamps both absent; never infer them from later callbacks |
| Input fence for a different marker | Valid initial timestamp, but no matching input-fence timestamp |
| No worker/view events or frame costs available | No invented optional times; all four costs are `none`, not zero |
| No Rust entry timestamp | Only the ordinary Library submission record; no accepted stage or transition record |

The boottime readings here are **worker** clocks; the frame costs and ordering of independent work streams are **supplied inputs**. The exercise does not render a stock card, issue an actual display submission, or time a physical Kindle. The device cohort must observe real input, accepted markers, phase times and restoration separately.
