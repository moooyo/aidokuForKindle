# Spatial simulation of 256 grayscale tones

Date: 2026-11-16, Asia/Shanghai.

The user's goal is spatial grayscale simulation: use the distribution of
neighboring pixels to represent finer gray transitions. This is feasible and
has now been demonstrated on the physical Scribe with software prototypes.
The investigation evaluates that goal directly rather than treating native
per-pixel bit depth as a blocker.

The subsequent [production implementation](SOFTWARE_DITHER_SETTINGS_20261116.md)
provides the user-requested Off/Bayer8/Floyd-Steinberg setting. The measurements
below remain the original, source-bound research results.

## Method

One synthetic 1860 x 2480 Gray8 chart contains all 256 input tones. Its
16 x 16 grid uses 96 x 96 constant-tone patches. Measurements use each patch's
central 64 x 64 pixels, covering complete Bayer periods and avoiding patch
boundaries. A separate continuous ramp provides a visual comparison.

The chart is decoded at its existing dimensions, then processed with the
actual Reader Bayer4 path, the existing generic Floyd-Steinberg Q4 path, or
the Bayer8 and error-conserving Floyd-Steinberg Q8 research prototypes. The
same original chart is also submitted to hardware Y4. All physical submissions
use the same GC16 clearing policy. The off control is repeated at the end.

The actual Reader prepares single pages at twice the logical viewport size,
matching physical pixels on the target profiles. Software quantization occurs
after fitting and contrast processing. Fullscreen geometry is therefore an
appropriate integration point; subsequent preview/menu scaling must not be
confused with this full-resolution comparison.

## Physical Scribe results

Native post-MDP codes were oriented back to input coordinates and normalized
to the logical sixteen-level palette 0, 17, through 255. All four software
outputs were preserved exactly by the driver: zero changed quantized pixels.
The ARM and test-env outputs were also byte-identical. Both off controls matched.

| Method | Distinct patch means | Strictly increasing across 256 inputs | Maximum mean error | Mean absolute error |
| --- | ---: | --- | ---: | ---: |
| Off | 16 | No | 15.0000 | 5.3125 |
| Current Reader Bayer4 | 241 | No; 15 plateaus | 1.0000 | 0.4980 |
| Existing Floyd-Steinberg Q4 | 256 | Yes | 0.1365 | 0.0511 |
| Bayer8 prototype | 256 | Yes | 0.1250 | 0.0659 |
| Floyd-Steinberg Q8 prototype | 256 | Yes | 0.0381 | 0.0080 |
| Hardware Y4 | 209 | No; 47 plateaus | 2.0000 | 0.6327 |

Errors are in the 0-255 encoded input scale. These are measurements of spatial
representation in this controlled chart, not a camera-derived count of visual
just-noticeable differences. In particular, Y4's 209 is not a universal panel
limit. The earlier 217-stripe result used a different, narrow-strip fixture;
this aligned-patch test is more suitable for comparing spatial averages.

## Why Bayer8 can cover the target

For an input tone between adjacent palette entries, distribute the upper
entry among the 64 pixels of an 8 x 8 tile. Round that pixel count to the
nearest available proportion. Every one of the 256 input tones then has a
distinct increasing tile mean. In this implementation the largest error is
0.125 of one 8-bit gray step.

The current 4 x 4 pattern has only 16 positions per period and yields 241
distinct period means. Its current integer threshold convention also has a
small downward bias. The Bayer8 prototype uses a 64 x 256 lookup table and
explicit two-dimensional phase, avoiding per-pixel division in its hot loop.
The lookup table occupies 16 KiB.

The Q8 error-diffusion prototype retains 1/256-step error precision and assigns
the rounding remainder to the last neighbor, preserving total propagated
error. It improves mean-tone accuracy over the existing Q4 implementation.
It has a different spatial texture; lower mean error alone does not establish
that it is visually preferable for every manga image.

## Scribe CPU cost

Each method had one warmup and seven interleaved measured iterations. Times
include resident PNG decoding, identity fitting, allocation and dithering;
they exclude file I/O and display refresh. The benchmark opens no framebuffer
or hardware codec devices.

| CPU method | Median |
| --- | ---: |
| Preserve Gray8, no quantization | 38.10 ms |
| Current Reader Bayer4 | 55.68 ms |
| Bayer8 lookup prototype | 55.84 ms |
| Existing Floyd-Steinberg Q4 | 181.40 ms |
| Floyd-Steinberg Q8 prototype | 194.01 ms |

Bayer8 and Bayer4 were effectively equal in this workload. These numbers do
not measure complete page-turn latency or Y4's incremental display-engine cost.

## Recommended integration

Use Bayer8 as the normal software option for spatial 256-tone representation.
Offer the Q8 error-diffusion path as an optional alternative for users who
prefer its texture and accept its CPU cost. Retain hardware Y4 as a separately
identified alternative; it did not cover every target tone in this test.

Integration should:

1. Process the final physical-size image, after cropping, scaling and contrast.
2. Include the algorithm/version in processed-image and retained-image cache
   identities so old Bayer4 output cannot masquerade as the new result.
3. Apply the same selected algorithm to diagnostic images, instead of judging
   it with plain undithered settings rectangles.
4. Update software fallback consistently with the chosen policy.
5. Preserve the user's existing Fast mode: black-and-white DU with no automatic
   cleanup. Spatial grayscale enhancement belongs to the grayscale modes.

The software approach can cover Paperwhite 5/6 and Scribe without depending on
Y4 availability. The current physical evidence is for the user's Scribe;
performance and display behavior on other models still need their own checks.

## Artifacts and scope

- [Research example](../examples/spatial-dither-research.rs).
- [Controlled display probe](../scripts/native-spatial-dither-probe.c).
- [Native results and all patch means](review/spatial-grayscale-20261116/native-results.json).
- [All seven Scribe CPU samples](review/spatial-grayscale-20261116/scribe-cpu-timings.json).
- [Lossless output comparison](review/spatial-grayscale-20261116/comparison.png).
- [Run identity and restoration](review/spatial-grayscale-20261116/summary.json).

The first connection attempt timed out before any device changes. A fresh
batch ran after the user enabled SSH. The successful batch had no new critical
kernel messages; the original framebuffer, native services and lighting were
restored, staging removed and the exact owned lease released. No hardware
JPEG/PNG decoding was performed. The installed application and its defaults
were not changed by that research batch. The later production integration and
deployment have their own source identity and evidence linked above.
