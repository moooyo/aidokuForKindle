# Software dithering settings and button feedback

Date: 2026-11-16. Base commit: `a53b9716d64fe5521fac38431b24732c75cb8ff7`.

## User controls

Settings > Display and refresh > Grayscale dithering provides three mutually
exclusive choices. Paperwhite uses three adjacent controls so the full test
strip remains visible; Scribe uses three full-width rows.

| Choice | Processing | Stored preference |
| --- | --- | --- |
| Off | Nearest of the 16 native gray codes; no spatial pattern | `dither=false`; the last method is retained |
| 8x8 Bayer | Ordered 64-position pattern, with a 16 KiB lookup table | `dither=true`, `dither_method=bayer8` |
| High-precision Floyd-Steinberg | Serpentine error diffusion with Q8 error precision and remainder conservation | `dither=true`, `dither_method=floyd_steinberg` |

The setting applies to Reader image processing and the 256-input settings
strip. It is a CPU capability and does not depend on a Y4 or JPEG driver.
JPEG hardware acceleration remains a separate, default-off hardware setting.

New installations use Bayer8. Loading an older enabled `software` or `hardware`
choice migrates it to Bayer8; an older disabled choice stays disabled. A stored
Floyd-Steinberg choice, including a disabled one, survives restart. The legacy
boolean and field names remain readable for compatibility. Unknown stored
methods resolve to Bayer8; invalid interactive commands return an error.

The three choices never request Y4. Low-level Gray8/Y4 code and its driver
qualification tests remain available for research, but Y4 is no longer a fourth
choice in the product menu. This supersedes the earlier two-way software/Y4 UI.

## Integration contract

The production algorithms live in
[`src/imaging/spatial_dither.rs`](../src/imaging/spatial_dither.rs). They run on
the final physical-size Gray8 raster after its geometry and contrast operations.
Every output pixel is one of `0, 17, 34, ..., 255`. Additional apparent tones
come from pixel distribution, not additional independently addressable panel
levels. The [qualified research](SPATIAL_GRAYSCALE_RESEARCH_20261116.md) explains
the measured 256 increasing patch means and the texture/performance tradeoff.

`ImageOptions.spatial_dither` selects the algorithm. `Some(Off)` is explicit
16-level rounding. `None` preserves unrelated generic image processing paths
and the low-level raw Gray8 research path. Options cannot enable both a spatial
algorithm and hardware Y4. Cancellation is checked at bounded intervals,
including within long rows. Bayer phase uses `(x, y)` rather than a flattened
index; Floyd-Steinberg uses two `width + 2` signed error rows.

Processed and resident cache identities contain the versioned algorithm:
`off`, `bayer8_v1`, or `floyd_steinberg_q8_v1`. Existing cache records without
the optional field retain their previous serialization. Raw decoded sources
can be reused across methods. Source-free processed aliases reject a mismatched
method; foreground, prefetch, prepared next-frame and displayed-history paths
retain the requested processing identity. A setting change invalidates old
Reader work, and late results cannot replace the newer generation.

Fast mode retains its persistent monochrome frame and DU page updates. The
selected grayscale algorithm is saved for the other display modes. Changing
this setting does not restore automatic full clearing in Fast.

The settings test strip is generated at the window's physical pixel dimensions
with the same production algorithm and cached by dimensions/method. It is not
a pre-dithered image that would be processed twice. Its 256 narrow input cells
are a visual aid; use the larger aligned research patches for numerical tone
measurements.

## Button feedback

The controls follow the inspected BiliComics `ActionButton` and `ToggleRow`
conventions: primary/selected fill is combined with press state using OR,
rather than XOR. Holding a selected dark button therefore does not turn it
white. Switches retain their label, track and thumb colors and use a neutral
pressed-row fill. Navigation retains its selected icon/label and top marker.

Button press/release/cancel feedback requests nonflashing dirty-region AUTO
updates. It does not force a DU press frame followed by a grayscale restoration.
The later [Tab feedback and Perf05 fix](TAB_FEEDBACK_AND_PERF05_20261116.md)
removes temporary navigation press states entirely: bottom Tabs update only
after selecting a different destination. It also moves Fast conversion to the
worker and keeps optional sleep-cover preparation outside active reading.
There are no timed click animations, fades, transforms or added settlement
timers. Long-press recognition, actual slider dragging, explicit ghost clearing,
startup feedback and the separately selected Kindle page-turn mode keep their
own existing behavior.

## Verification and reuse

- [Implementation evidence](review/software-dither-settings-20261116/README.md).
- [Frozen UI candidate](uiux-baselines/2026-11-16-a53b9716-software-dither/README.md).
- [Reproducible UI checks](../scripts/verify-spatial-dither-ui.py).
- [Production/prototype pixel comparator](../examples/spatial-dither-research.rs).
- [Earlier independent user A/B tool](review/dither-ab-user-test-20261116/README.md).

All verification runs on `ssh test-env`, with native display checks on the
owned Scribe batch. Paperwhite screenshots validate layout, not physical
Paperwhite display performance. The independent A/B installation remains a
separate tool; it does not become the production setting's input.

For BiliComics, reuse the versioned algorithms and the integration contract,
including post-resize processing, cancellation, cache separation, persistence,
legacy migration, no double dithering, and the same three visible choices.
Keep any project-specific Fast mode and hardware decoder admission independent.
