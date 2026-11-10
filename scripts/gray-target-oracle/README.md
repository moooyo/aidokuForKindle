# Grayscale target oracle

This standalone workspace tests the production `gray_target.rs` and
`render_control.rs` modules against Slint 1.17.1's RGB software renderer. It uses
only dependencies already present in the application. It must run on the
designated `ssh test-env` environment; it does not open a framebuffer or input
device and does not contact a Kindle.

The offscreen component contains text, a translucent rounded rectangle, a
gradient, a filled/stroked path, an opaque RGB page, and a translucent RGBA image.
The oracle compares canonical scene pixels and every simulated framebuffer byte,
including untouched scanline padding. It exercises partial redraws, four output
rotations, three resizes, retained full rewrites, both output transforms, colored
primitive/image fallback, and subsequent restoration of partial redraws.

The harness registers FreeSans from
`/usr/share/fonts/truetype/freefont/FreeSans.ttf`. Set `GRAY_TARGET_TEST_FONT` to a
compatible font file on the remote host if needed.

After staging the repository files in the remote evaluation directory, run from
PowerShell:

```powershell
ssh test-env 'cd /root/aidoku-koreader-eval-20261110-01/gray-target/scripts/gray-target-oracle && PATH=/root/.cargo/bin:$PATH CARGO_BUILD_JOBS=6 CARGO_TARGET_DIR=/root/aidoku-koreader-eval-20261110-01/gray-target/target /root/.cargo/bin/cargo test --release --offline -- --test-threads=1'
ssh test-env 'cd /root/aidoku-koreader-eval-20261110-01/gray-target/scripts/gray-target-oracle && PATH=/root/.cargo/bin:$PATH CARGO_BUILD_JOBS=6 CARGO_TARGET_DIR=/root/aidoku-koreader-eval-20261110-01/gray-target/target /root/.cargo/bin/cargo build --release --offline'
ssh test-env '/root/aidoku-koreader-eval-20261110-01/gray-target/target/release/gray-target-oracle oracle'
```

The binary also accepts:

```text
bench <rgb|gray> <full|partial> <width> <height> <iterations>
memory <rgb|gray> <width> <height>
fallback <width> <height> <iterations>
```

Run timing cases serially and coordinate with other remote benchmarks. Each
benchmark process starts with eight warmup frames. Full updates swap two prepared
neutral RGB images; partial updates move one translucent control. Timing includes
property changes, timer updates, actual Slint rendering, output conversion, and
scanline copies into a padded simulated framebuffer. Image preparation, image
decoding, e-ink ioctl calls, and panel refresh time are excluded.

`memory` starts a fresh process for each format, records actual requested live
and peak bytes through a counting system allocator, renders the complete scene
to commit its pages, and reads process RSS/HWM from `/proc/self/status`.
`fallback` compares the first colored partial update with an RGB reference and
times the following requested frame. Slint clears its dirty cache when the
repaint policy is restored, so that following frame is conservatively full.
Offscreen windows are explicitly hidden between fallback samples.

See [the evaluation report](../../docs/review/slint-gray-target-20261110.md) for
the measured results and public API limits.
