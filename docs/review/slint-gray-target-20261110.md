# Retained grayscale scene evaluation

The grayscale target is suitable for the existing monochrome 8-bpp scanout path.
It reduces the retained scene from three bytes to one byte per pixel and removes
the output RGB-to-gray arithmetic for neutral scenes. Actual colored input causes
an immediate complete RGB rebuild before scanout and a permanent RGB fallback.
Other framebuffer formats must select RGB at construction.

## Public API and exactness

Slint 1.17.1 exposes an implementable
[`TargetPixel`](https://docs.slint.dev/1.17.1/docs/rust/slint/platform/software_renderer/trait.TargetPixel.html)
trait and public components in
[`PremultipliedRgbaColor`](https://docs.slint.dev/1.17.1/docs/rust/slint/platform/software_renderer/struct.PremultipliedRgbaColor.html).
No Slint fork, new library dependency, private image representation, or private
rendering API is required.

For neutral inputs, the target copies Slint's channel arithmetic exactly:
`destination * (255 - alpha) / 255 + premultiplied_source`. All three original
channels remain equal, so the existing weighted output luma is exactly that
channel. An exhaustive oracle covers all 8,421,376 combinations of background,
alpha, and valid neutral premultiplied source channel.

Arbitrary colored compositing cannot be replaced by a scalar luma operation.
Opaque red followed by 50%-opaque black produces RGB luma 38, while blending the
already reduced luma produces 37. The implementation therefore detects unequal
channels in `from_rgb`, `blend`, and `blend_slice`, discards the candidate, drops
its storage, and renders the whole scene again into RGB. Primitive, text,
gradient, and image operations all pass through these target entry points.
The RGB replacement never reconstructs colors from grayscale.

Slint's public
[`Image`](https://docs.slint.dev/1.17.1/docs/rust/slint/struct.Image.html)
constructors support RGB8/RGBA8 buffers, so input `SharedPixelBuffer` images still
use their existing RGB expansion. This change saves the retained scene and output
conversion; it does not claim that every image representation is single-channel.

## Offscreen correctness

The standalone oracle and production module tests passed: 18 tests, 0 failures.
The real software renderer exercised text antialiasing, opacity, rounded borders,
RGBA image compositing, paths, gradients, nine partial updates, four rotations,
three buffer/window resizes, 48 retained output rewrites, output inversion,
black/white thresholds, and padded framebuffer rows. Canonical and scanout pixels
matched the RGB reference. Color fallback was tested for a translucent primitive,
an opaque RGB image, a translucent RGBA image, and a gradient. Its permanence and
the later return to normal partial dirty regions were checked explicitly.

The retained scene is independent of output inversion and thresholding. Repeated
full writes do not invert it again. Raster preparation, dithering, refresh
selection, input handling, suspend/wake ownership, and rotation invalidation keep
their existing responsibilities. No Kindle hardware was contacted.

## Measurements

Tests ran only through `ssh test-env`, using a separate Cargo target directory
and `CARGO_BUILD_JOBS=6`. The host was Linux x86_64 on an AMD Ryzen AI 9 H 365 with
rustc 1.99.1; these are host CPU measurements, not Kindle timings. Timings include
actual Slint rendering, the production output transform, and scanline copies to
a padded simulated framebuffer. Decoding and physical e-ink refresh are excluded.
They are not full-application page-turn or end-user latency measurements.

Three serial rounds were run per case. Full cases used 101 iterations at
1264x1680 and 71 at 1860x2480; partial cases used 301 iterations. The table shows
the median of the three per-process medians. A partial region contained 853 pixels.

| Panel | Update | RGB | Gray8 | Reduction |
| --- | --- | ---: | ---: | ---: |
| 1264x1680 | Full | 3.165 ms | 1.652 ms | 47.8% |
| 1860x2480 | Full | 6.878 ms | 3.599 ms | 47.7% |
| 1264x1680 | Partial | 5.871 us | 4.630 us | 21.1% |
| 1860x2480 | Partial | 5.840 us | 4.630 us | 20.7% |

Fresh-process allocator and RSS measurements after rendering the complete scene:

| Panel | RGB scene bytes | Gray8 scene bytes | Scene saved | RGB RSS | Gray8 RSS |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1264x1680 | 6,370,560 | 2,123,520 | 4,247,040 | 22,859,776 | 18,792,448 |
| 1860x2480 | 13,838,400 | 4,612,800 | 9,225,600 | 38,035,456 | 28,860,416 |

Allocator peak growth from scene creation through the first render was
6,435,052 vs 2,188,012 bytes at 1264x1680 and 13,902,892 vs 4,677,292 bytes at
1860x2480. Allocation counters measure requested bytes; RSS includes the process,
font, input image, and rendering state and can vary with allocator page behavior.

Color fallback has an intentional one-time cost. In 21 warmed samples, a colored
partial update cost approximately 3.36 ms at 1264x1680 and 7.18 ms at 1860x2480;
the RGB partial references cost about 23.7 and 31.0 us. Restoring Slint's public
repaint policy clears its dirty cache, so the next requested frame also redraws
fully, taking approximately 3.17 and 6.84 ms. Subsequent partial regions again
match RGB. No extra redraw, timer, or wake is requested by the fallback itself.
Peak requested allocation growth during fallback was 4,247,684 and 9,226,244
bytes respectively, close to the permanent two-byte-per-pixel increase; the
rejected grayscale scene is released before allocating its RGB replacement.

## Integration boundary

`RetainedScene::new(pixel_count, allow_gray8)` owns canonical pixels.
`RetainedScene::render(renderer, stride)` returns Slint's dirty region and handles
color rejection internally. `RetainedScene::pixels()` provides `ScenePixels` to
`GrayRenderer::write_scene_frame`; the original RGB-only `write_frame` remains a
wrapper. Only the framebuffer's actual 8-bpp capability may set `allow_gray8`.
Keep the same scene across partial draws, mode changes, and retained wake writes.
Keep the existing complete repaint on rotation changes. A newly allocated scene
after a geometry change must be paired with the existing full-repaint policy.

Remote evidence is under
`/root/aidoku-koreader-eval-20261110-01/gray-target/`:
`tests-final.log`, `oracle-final.log`, `bench.log`, `memory-final.log`, and
`fallback-final.log`. Reproduction source and commands are in
[`scripts/gray-target-oracle`](../../scripts/gray-target-oracle/README.md).
