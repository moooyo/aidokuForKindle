# Resident handoff transport probe

This standalone Linux fixture runs the production transport against its own executable. It never uses the Kindle, an installed application, a framebuffer, an input device or a real library. The manifest contains the same `libc` and `sha2` versions as the application lockfile. It is not part of the installed package.

On the designated Linux test worker, from this directory, acquire the repository's shared build lock before these Cargo commands and keep concurrency at six:

```sh
export CARGO_BUILD_JOBS=6
cargo test --locked --offline -j6
cargo check --locked --offline -j6 --target armv7-unknown-linux-musleabihf
cargo build --locked --offline -j6 --release
fixture=$(mktemp -d)
target/release/aidoku-resident-probe suite "$fixture"
```

The process test takes about 49 seconds; it does not lower the production 45-second deadline. It publishes a deterministic 2 MiB frame, checks that the helper inherited no application descriptor or environment variable, samples CPU ticks and RSS, verifies that its only socket is a listening abstract Unix stream, and consumes the exact bytes in a fresh process. A copy of the binary at another inode cannot consume them, even when given the fixture's actual address. It checks single consumption, replacement, cancellation, interrupted publication, separate data directories, and expiry for both a runnable and a deliberately stopped child. A committed transfer stays available after the real publisher process exits, while an interrupted publication does not. No fixture data-directory file may appear. Unit tests cover old-kernel descriptor cleanup, reaping a stopped direct child on a failed publication, and fail-closed pressure checks before and during a handoff.

Reported timings and RSS are for this fixture ELF on the test host. Use the complete Kindle app and two fresh Library/native ownership generations to assess end-to-end startup. Under an unresponsive helper, a consumer returns to the ordinary path at its 120 ms IPC deadline. A deliberate stop discards the live listener and payload before acknowledging. If the helper cannot run to acknowledge on an old kernel, its own `CLOCK_BOOTTIME` hard timer still removes it, including while it is stopped; it cannot delay a new launcher or acquire screen/input ownership.
