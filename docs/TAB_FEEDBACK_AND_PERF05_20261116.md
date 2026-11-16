# Tab feedback and Perf05 Reader latency

Date: 2026-11-16, Asia/Shanghai.

The user reported repeated dark feedback after selecting a bottom Tab and
intermittent slow turns in **Perf05 High Resolution PNG**. Both were investigated
on the user's Scribe. The final implementation is installed in the ordinary
Aidoku application.

## Findings and changes

### Tab feedback

Navigation used a temporary pressed background and explicitly requested UI
updates during pointer events. Releasing the pointer and changing the route
then produced further redraws. The native baseline showed a separate Tab-only
update while holding a Tab, and the held pixels differed from the idle pixels.

Navigation now keeps its paper background throughout the press. Only a completed
selection changes its icon, label and top marker. Pressing, cancelling, or
reselecting the current Tab does not request an additional navigation refresh.
The final native held/idle images are identical, with zero separate Tab-only
updates in the held and multi-selection trials. Content updates still use the
normal refresh path; this is not a claim that the panel needs no physical update.

### Fast Reader preparation

Fast mode previously expanded grayscale to shared RGB on a worker, then allocated
another RGB buffer and converted it to monochrome on the UI thread. Even a
prepared-next-frame hit repeated that conversion. On the tested two-page layout,
the UI preparation cost was approximately 100 ms.

The Reader and its read-ahead worker now produce the final binary RGB payload
directly. UI publication reuses that allocation. Row iteration also removes a
runtime division per pixel while preserving the established Fast pattern,
including widths that are not multiples of four. The existing grayscale cache
is not replaced with binary pixels. Cancellation remains bounded inside rows.

The Triangle vertical pass now accumulates contiguous source rows. It preserves
the reference's increasing-source-y `f32` operation order and exact output while
reducing indexing overhead. Existing reference, region, edge and random-input
tests pass. The final physical Reader screenshots are byte-equivalent in pixels
to the baseline.

### Optional sleep-cover contention

After read-ahead finished, optional high-resolution sleep-cover preparation
could start immediately. It shared the serial image-decoder admission path with
the next Reader work. In a fresh library this delayed read-ahead, causing the
following two-page turns to repeat foreground image processing.

A single-variable native control used the same intermediate executable and
fixture with sleep covers disabled only in its isolated test data. Prepared
forward turns increased from one of three to three of three; the two slow
approximately 1.18-second frames disappeared. User settings were unchanged.

The production fix keeps the cover feature enabled. Optional full-resolution
preparation waits for five seconds of Reader idle time. New reading operations
cancel pending preparation, retaining its worker slot until the result drains.
Any already available full cover or thumbnail remains usable. Cancellation does
not clear the idle deadline or discard the ready preview. Existing bounded
decoder and raw-source validation rules remain in place; ongoing library
operations still reach their cancellation boundary cooperatively.

The final native trial confirmed that the high-resolution cover was ready after
idle (`1448 x 2048`, feature enabled), while continuous reading retained all
three prepared forward turns. Preparing a cover does not replace the visible
Reader frame or schedule a page-clearing update.

## Physical Scribe results

The fixture is the exact installed `Perf05-High-Resolution-PNG.cbz`, SHA-256
`0e11719838c8da38388be9f3f389fa59ce671625bb033b1707842bce520cf076`.
It contains eight distinct 2480 x 3508 grayscale PNGs. Tests used the device's
existing Fast, Bayer8, crop, two-page and hardware-acceleration preferences.
PNG remained on the CPU path. Two-page mode means three effective forward turns
across four windows; subsequent taps at the end do not produce image requests.

| Measurement | Baseline | Final |
| --- | ---: | ---: |
| First-visit forward turns, median | 1301.524 ms | 139.685 ms |
| First-visit forward turns, range | 364.059–1337.953 ms | 138.279–154.544 ms |
| First-visit prepared-next-frame hits | 1 of 3 | 3 of 3 |
| Prepared forward turns in the repeated run, median | 241.542 ms | 137.699 ms |
| Prepared forward turns in the repeated run, range | 240.869–251.829 ms | 133.974–150.146 ms |
| UI binary-pixel preparation | About 100 ms | 0.009–0.022 ms |
| Final Reader screenshot differences | Reference | Zero pixels in all three compared runs |

Times start when the application receives the scripted pointer-derived reading
action and end when the driver accepts SEND. They do not measure touch-controller
sampling or optical panel completion. The OS file cache was not flushed.
The first-visit and repeated-run samples each contain three delivered forward
turns; they are reproducible small-workload observations, not population p95s.

The 600 ms input-burst trial still supersedes an obsolete request while moving
to the latest target. The final delivered view is correct. Opening or seeking to
unprepared large pages still requires approximately 1.15 seconds in this
two-page workload; preloading cannot eliminate all first-use image processing.

## Implementation and verification

- [`Navigation`](../ui/widgets.slint): selection-only visual feedback.
- [`prepared_pixels`](../src/app/prepared_pixels.rs): cancellable worker preparation and constant-time publication.
- [`reader_pipeline`](../src/app/reader_pipeline.rs): foreground/read-ahead integration and optional-cover deferral.
- [`reader_raster`](../src/imaging/reader_raster.rs): equivalent contiguous-row accumulation.
- [`sleep_screen`](../src/app/sleep_screen.rs): idle scheduling, cancellation and retained preview.
- [`analyze-perf05.py`](../scripts/analyze-perf05.py): trace and image comparison.
- [`verify-navigation-feedback.py`](../scripts/verify-navigation-feedback.py): real Slint pointer checks.
- [Raw results, package and restoration evidence](review/tab-perf05-20261116/README.md).
- [Frozen paired UI and native captures](uiux-baselines/2026-11-16-a53b9716-tab-perf05/README.md).

Final checks passed on `ssh test-env`: 797 Kindle-feature library tests,
782 default-feature library tests, 20 binary tests, 14 paired-profile UI cases,
desktop/ARM builds and 162 package checks. Each library suite retains five
pre-existing ignored cases. Final native performance and navigation runs passed
with no new critical kernel lines or reboot. The installed 32-file payload was
verified, user metadata remained unchanged, native services and lighting were
restored, and every physical batch released its fresh owned lease.
