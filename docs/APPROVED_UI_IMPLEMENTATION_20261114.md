# Approved portrait UI implementation

This change implements the approved single-footer design from November 14,
2026 in the Rust and Slint application. The HTML design remains a reference;
application captures are produced by the actual desktop software renderer.

The [frozen UI/UX baseline](uiux-baselines/README.md) records the resulting
interface at source `433a1f6`, with 31 paired states, original screenshots and
an interactive comparison for subsequent UI/UX iteration.

## Interface and navigation

- Library, catalog, search and history use compact pagination in their existing
  top operation areas. The bottom contains one text navigation band.
- Source management uses a single list. Provider configuration remains available
  from the row settings action, alongside installation, updates and local import.
- Reader tools are Chapters, Reading, Image and Brightness. Chapters opens the
  book detail page and locates saved progress. Detail Back restores its origin;
  Reader Back returns to the library.
- The detail filters are All, Unread, Downloaded and Bookmarks. Filtering occurs
  before database pagination. Bookmarks open their saved page and can be removed
  individually in edit mode; repeated removal does not create a bookmark.
- Application settings reuse the reader controls within their own navigation
  context. Exit is at the bottom of the settings list or sidebar. Inputs and
  binary switches have square corners.
- Refresh interval uses the five snapping values `[1, 3, 6, 10, 0]`. Brightness,
  warmth and contrast sliders preview while held and commit on release. Contrast
  retains the five internal values `[-20, -10, 0, 10, 20]`.

## Reader pixels

The four named fits use source dimensions after optional white-border cropping:

| Fit | Scaling and placement |
| --- | --- |
| Original | `min(1, viewport_width/source_width, viewport_height/source_height)`, centered; small images are not enlarged. |
| Fill screen | Independent horizontal and vertical scales fill the viewport, including intentional stretching. |
| Fit height | Uniform scale to viewport height; center-crop horizontal overflow or center with side margins. |
| Fit width | Uniform scale to viewport width; center-crop vertical overflow or center with top and bottom margins. |

Each fit prepares a complete viewport bitmap before the existing publication
pipeline replaces the displayed frame. Cache identities include the selected
fit. Named fits bypass resized download derivatives to preserve source geometry,
while retaining decode limits, cancellation, private prefetch and source proofs.
Oversized streaming PNGs retain the existing bounded sampling policy.

Night mode changes semantic UI colors. Full-frame backend inversion is disabled,
so comic and cover pixels retain their original polarity. Dithering is selected
from runtime capability at attachment and displayed read-only in About as Y4 or
Software. Existing backend rejection and fallback behavior remains in place.

## Verification

All builds, tests and captures run on `ssh test-env`, with Cargo concurrency six.
No application process or verification suite runs on the Windows workstation.

The dedicated `scripts/verify-approved-ui.py` harness exercises both Paperwhite
and Scribe, including light/dark captures, actual fit button hit areas, return
paths, chapter filters, persisted bookmarks, pagination, release-only refresh
changes, and pixel-identical full-screen comics across night-mode changes.

Run the harness against an immutable built binary:

```sh
python3 scripts/verify-approved-ui.py \
  --binary "$CARGO_TARGET_DIR/debug/aidoku" \
  --output-dir artifacts/approved-ui
```

Physical e-ink waveforms, optical timing and real-device interaction are outside
this remote software verification. No shared Kindle lease is acquired.

### Completed checks

- Default all-target tests: 756 library tests, 20 binary tests and 95 example
  tests passed. Five opt-in library benchmarks remain ignored.
- Kindle-feature library tests: 769 passed, with the same five benchmarks ignored.
- Vendored backend tests: 173 passed. Launcher tests: 75 passed.
- Final ARM release build and development-package generation passed. The 130
  transferred source, interface, translation and harness files match the
  operator checkout byte for byte, checked on the remote worker.
- Full portrait UI run: 78 scenarios and 1,620 checks passed across both profiles.
  This includes unchanged comic and cover pixels in night mode, actual fit button
  clicks, bookmark persistence and release-only refresh commits.
- Final chapter-order correction: eight focused navigation scenarios and 166
  checks passed against the final application binary, covering both sort orders,
  opening from the library, visible current-chapter placement and return paths.
  The earlier complete UI run is retained separately; the final correction
  changes only the chapter-position lookup.
- Delivery library captures wait for visible covers to finish loading. The
  overview uses the corrected chapter-navigation capture.

The focused navigation check can be repeated with `--navigation-only`.
