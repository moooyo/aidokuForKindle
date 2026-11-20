# Normal Library framebuffer observer

On the reference-only installed R3 visit, retain the native idle view before
dispatch and two independent captures of the fully loaded ordinary Library,
at least 300 ms apart. Use `framewatch capture FILE` for each (`stock.frame`,
`library-a.frame`, `library-b.frame`). They include the active viewport,
kernel rotation, pan geometry and device boot-clock acquisition bounds. Only
the current visible page is copied. Keep these records private on `test-env`.
The operator must identify the actual Library, occupied grid cells, continuing
card and final artwork independently of timing; the analyzer cannot identify
a book or infer missing art from an unchanged picture. Preserve the normal
entry and native restoration receipts separately. No user book text or
framebuffer data belongs in published evidence. A reference-only URI visit
may start on native Home; it does not determine the stock pixels or tile
position on any later timed visit.

The timed interface is `framewatch watch TRACE` (75 ms, 12 s). Start it before
the real trigger and wait for `READY` on stderr, emitted after the first scan.
On every visit first have the separate native target guard identify the
intended stock tab and uniquely matched tile (or named URI origin), then
`framewatch capture visit-stock.frame` **before** starting `watch`. Use a new
capture on every visit; recent items and their ordering may change. The latest
permitted gap from this capture to dispatch is 30 s. Preserve each independent
target/identity receipt; the observer cannot identify an input target.
Allow at least two more ticks before dispatch. `framewatch clock` prints device
`CLOCK_BOOTTIME` microseconds for an external bracket. Keep the trigger's
earliest and latest `CLOCK_BOOTTIME` microseconds
and optionally its name. Repeating the three reference captures is not required
on each timed visit. The trace and reference rule are private worker artifacts.

On the worker, inspect the two independent ordinary Library captures and the
native idle capture without publishing them. Verify the app route, true book
text, current card, all occupied covers (not placeholders), and the rendered
orientation. `--half-turn` is the clockwise correction to upright for the
raw captured pixels, not a conversion inferred from the kernel's rotation.
The first capture line is ASCII; the rest is exactly 1860 × 2480 bytes of
unpadded row-major Gray8 (for example `Image.frombytes("L", (1860, 2480),
payload)` in private review). For layout other than the normal Scribe
continuation and 1–15 occupied cells in the five-column grid, do not freeze
the rule. Choosing a count, orientation or ROI to rescue a timed visit is
not allowed.

```sh
python3 -B observer.py freeze stock.frame library-a.frame library-b.frame rule.private \
  --grid-books 6 --half-turn 0 --reviewed-text-art-and-orientation
python3 -B observer.py check-stock rule.private visit-stock.frame
python3 -B observer.py measure rule.private visit.trace DOWN_EARLIEST_US DOWN_LATEST_US --stock visit-stock.frame
python3 -B observer.py compare-settled rule.private library-a.frame visit-final.frame
```

`check-stock` tests the independent capture against every frozen text and
artwork region; it requires differences large enough to distinguish them from
the app. `measure` requires the same test and exact matching of the first and
last two pretrigger body samples against **this** visit's capture, never the
earlier reference idle view. Do not copy a pretrigger timing sample into
`visit-stock.frame`.

`compare-settled` requires a separate ordinary foreground capture taken after
the timed window and before exit. It separately compares the complete body and
the full right half of the 72 px status strip (including a meaningful contrast
witness for battery/Wi-Fi); only the left clock is excluded. Natural icon,
battery or Wi-Fi changes and flat status bands still fail the check. Report
them as failures rather than masking them. Sparse samples do not substitute for that independent full-frame
check. The timing rule requires the same idle body before dispatch, measurable
contrast in each text/caption and artwork region, independent matching settled
frames, the final sampled body, stable geometry and at least 200 ms of sustained
matching (not added to the endpoint). Both text and all artwork require
terminal stability. One underfilled or missing cover invalidates a run. With a
very short title or a line-art cover lacking distinguishable sampled features,
report no measurement rather than lowering the checks. Anchors over 100 ms,
scans over 50 ms, inter-scan gaps over 150 ms, and sampler CPU over 10% of one
core are refused. Returned numbers bracket framebuffer writes from the supplied
trigger; they measure neither optical settling nor software input readiness.
An indexed URI trigger is not a native tile press.

The helper only opens `/dev/fb0` read-only and writes its exclusive private
output. Each scan obtains the current Y pan, uses the observed 1872-byte
stride and confirms the same pan/geometry again afterward. An intra-scan pan
or layout change refuses the run; complete page flips between scans are valid.
The private trace stores 4,466 bytes at each 75 ms tick, buffering during the
window. The Linux supported targets are little-endian. No device-facing
command here obtains, extends or releases the outer shared lease.

```sh
zig cc -target arm-linux-musleabihf -O2 -Wall -Wextra -Werror -s -static -o framewatch framewatch.c
cc -O2 -Wall -Wextra -Werror -o /tmp/framewatch-scan-test test_scan.c && /tmp/framewatch-scan-test
python3 -B -m unittest -v test_observer
```

### Independent reference admission

Run `python3 -B -m observer.review_reference ROOT REFERENCE_BATCH L01_PNG
NAVIGATION_PNG` from the worker operator folder. Use the public 2026-11-14
Scribe `raw/scribe/library.png` and 2026-11-16 navigation-fast
`desktop/scribe-navigation.png` as templates. The helper accepts only its six
fixed, bounded archive members and the matching completed and released R3
transaction. It checks upright orientation independently of book count using
the fixed category strip, controls/button, and bottom navigation. It then
checks the continuing title/art and each of the fifteen potential cell
interiors, captions and empty areas, not alternative counts until one timing
rule passes. Indeterminate unillustrated cells and layouts fail closed. A
compact raw-orientation preview is left only in the private batch folder on
visual ambiguity. A known layout is never inferred from the timed cohorts.
After independent acceptance `--freeze-reviewed` writes `rule` and aggregate
`reference-review.json` exclusively. Neither contains a public book title;
the rule contains sampled personal pixels and must remain private.

The public template tests run from the same directory with
`AIDOKU_REVIEW_BASELINES=observer/baselines python3 -B -m unittest -v
observer.test_reference_review`.
