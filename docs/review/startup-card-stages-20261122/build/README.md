# Single diagnostic ARM build

On November 22, 2026, `test-env` built one opt-in diagnostic version of the JPEG-on-demand application for this independent cohort. It is not either executable from the completed A/B study and it is not an installed upgrade. No device was connected and no hardware lease was acquired for this build.

The build froze the actual 479 product/build input files from the working checkout based on `1976df8f66a2`, including its uncommitted changes. It compared them by bytes against the build source. Only `Cargo.toml`, `src/main.rs`, `src/bootstrap_metrics.rs` and `src/reader_timing.rs` differed: they introduce and consume the default-off diagnostic feature. The deferred JPEG behavior and ordinary-card launcher are unchanged. The existing shared Ubuntu runner compiled dependencies, then compiled the final application once from the approved sources. Both stages used one private target directory; an uninstrumented application was not linked.

```sh
export CARGO_BUILD_JOBS=6 CARGO_INCREMENTAL=0
export CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16
cargo zigbuild --offline --locked --release --no-default-features \
    --features kindle,startup-stage-diagnostics --target armv7-unknown-linux-musleabihf \
    --bin aidoku -j6
```

The final linker produced a **53,729,744-byte** static ARMv7 EABI5 hard-float executable. No additional runtime programs, shared libraries or environment variables are required by the instrumentation. Both ordinary and no-NEON QEMU runs returned `aidoku 0.1.3`; the Scribe and Paperwhite 6 CLI accepted their own design profiles without printing fake diagnostic records.

The [QEMU verifier](verify_diagnostic.py) links the [frame probe](diagnostic_probe.rs) against the final build's actual release Rust library. The probe records a real clock reading at `main` and calls the production frame hooks with controlled checkpoints; it does not synthesize the log lines. For a populated Scribe Library and an empty PW6 Library, the two stderr lines had the expected layout, the main/first-full/submitted clock order was valid, and the Library line and stage line used exactly the same final timestamp. A third invocation supplied an ineligible noninitial checkpoint and then the Library callback without any valid first full frame: it reported `initial_us=none`. See the [captured output](qualification.json).

These are worker boot-clock readings from separate QEMU processes, **not Kindle startup durations**. They validate the binary, timestamp source, logger format and layout projections, not native input, framebuffer submission or physical ink. A qualifying real visit must establish those endpoints independently.
