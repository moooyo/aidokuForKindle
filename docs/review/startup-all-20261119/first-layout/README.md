# First Library layout: worker evidence

`first_layout=optimized` uses the selected page's existing materialized SQL window to project ordered books, downloaded state and the latest progress together. It returns each UI row directly, instead of keeping a second page of decoded books. All existing category/filter/order/clamping rules and progress tie breakers are shared with the original query. The Library caption also bypasses the hidden full-text measurement if its compact label is empty or identical to the full title; a distinct alternative, including a long title with a distinguishing suffix, still uses the original font-aware width test. Accessibility keeps the full title. This mode applies to Library publications, including the first one; it does not change font families, visible items or the input barrier.

The existing unfiltered `recent-read` grid loads each visible `Book` document twice: first to select the page, then in `book_view_metadata` to obtain download/progress state. These measurements isolate that duplication. A faster grid query does not imply a corresponding reduction in the time to render, send or physically display the first Kindle frame.

## Projection experiment

[`profile-grid.py`](profile-grid.py) uses the read-only, built-in offline demo database previously seeded by the real `c6867d1` desktop release (46 library books, 137 chapters and 46 progress rows). The benchmark changes only the visible page limit. Six visible rows mean the first six items from this same fixture, not a reconstruction of a user's library.

The baseline reproduces `library_view_page` followed by one `book_view_metadata` call per row. The joined candidate adds the same indexed progress and download projection after the materialized visible window. The separate flags candidate keeps the visible query and fetches progress/downloaded for its IDs in one additional statement. Each run compares the entire ordered list of (display document, authoritative stored document, downloaded flag, most recent progress) and the total count; identities, scores and order may not be dropped. SQLite tracing and an instrumented JSON decoder count actual calls in an untimed pass.

[Results](grid-results.json), on `test-env` with 120 interleaved rounds per mode, warm database pages and no prepared-statement cache:

| Visible books | Statements: baseline / joined / batch | Full document decodes: baseline / either candidate | Paired median saving: joined | Paired median saving: batch |
| ---: | --- | --- | ---: | ---: |
| 6 | 8 / 2 / 3 | 12 / 6 | 0.071 ms | 0.064 ms |
| 8 | 10 / 2 / 3 | 16 / 8 | 0.114 ms | 0.096 ms |
| 15 | 17 / 2 / 3 | 30 / 15 | 0.204 ms | 0.198 ms |

Every ordered result matched. The microbenchmark uses Python's SQLite/JSON implementation on x86_64; it does not exercise Rust, Slint, current-book/daily-update cards, thumbnail work, framebuffer writes or the e-ink controller. Per-stage medians in the JSON need not sum to the median total.

Run on the designated worker with an offline demo database next to `.demo-v1-seeded`:

```sh
python3 profile-grid.py --demo-db /path/to/demo/library.sqlite3 --output grid-results.json
```

## Product pixels and interaction

[`compare-desktop.py`](compare-desktop.py) runs the actual desktop executable under Xvfb with scale one, `winit-software`, `--offline`, `--demo`, and fresh isolated data for each launch. It checks row count, page, modal or selection state, font initialization, absence of failed covers, and settled projection. It uses the original frozen pointer events for L02 (next page), L03 (sort/filter popup) and L04 (long press). A paired run compares all RGB pixels as well as visible identity, titles, progress, downloaded badges, selection, counts and sorting fields on both PW5 (618 x 824) and Scribe (930 x 1240).

[Reference-only qualification](desktop-reference.json) passed all eight profile/scene combinations with the prior release desktop ELF `3dcb68d4d089af15fb341e2971af978bc00260b37c832ea56e9a4339d2ceeb2d`. Both L01 PNG hashes reproduced the previously qualified product captures. This only qualifies the inputs and capture harness; it is not evidence that a new candidate passes.

```sh
python3 compare-desktop.py --reference-only --binary /path/to/prior/aidoku \
  --replays /path/to/docs/uiux-baselines/2026-11-14-433a1f6/replay --output /new/reference-captures
```

For a candidate run, use the same executable in both modes. Pass `--data-options` to write the production `startup-options` file under the isolated demo data directory. `--baseline-marker library_page_projection_rowwise` and `--optimized-marker library_page_projection_batched` identify the actual completed paths; a launch that silently ignores the requested setting fails. `--reference-captures` additionally verifies that neither mode regresses any previously qualified image. The report retains hashes, aggregate checks and timings; the worker's raw demo state, images and traces remain in the capture directory. Single view-attachment durations are diagnostic examples, not timing claims.

## Layout qualification

The [paired layout captures](desktop-layout-results.json) used one release executable (`82b5f3f0d0954c6d4f3234266e703c397d9cf1e6dd1fc37e79c32408cf5f85ec`) built on `test-env` from the previous production sources plus the final layout code, its shared disk-options parser and the two corresponding configuration bindings. It is an isolated desktop qualification, not the integrated Kindle executable. All 16 launches reached the expected first-projection marker. L01, L02, L03 and L04 on PW5 and Scribe matched both modes and the earlier reference with **zero differing RGB pixels**, the same ordered library state and no failed covers. All 16 store view-query regression tests passed, including active and missing progress, tied timestamps, download badges, sorting, filters and clamped pages.

The [separate mixed-title qualification](desktop-mixed-titles.json) used a fresh copy of the demo DB and alternated two-letter labels with long synthetic series names distinguished by a final number. The PW5 first page contained four short and four long titles and Scribe contained eight short and seven long titles. All four launches used the requested branch and the paired pictures and projected state matched. Neither generated book content nor captures from a real user are included here. The first attempted synthetic seed rejected an unillustrated book's empty cover path before running an application; the final tool preserves empty paths and the corrected qualification used fresh directories.

The per-scene first-grid timing markers are moderate-resolution diagnostics on an x86 worker, not a paired device speed estimate. In particular, a marker after publishing a row model does not measure asynchronous layout, glyph rasterization, panel submission or optical completion.
