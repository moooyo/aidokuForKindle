# C15 Display Identity Edit Handoff

The authorized three production files are ready for the sole UI writer and remote FVD. This handoff records static source inspection only and does not claim successful compilation, tests, formatting, screenshots, or a C15 pass.

`BookItem.display-title` is a separate fallback field. `BookItem.title` and `SourceItem.name` retain their complete actual values. The migration literal initializes the same field through `super::view::book_display_title`, without changing migration matching or transaction behavior.

Source shortcut refinement operates on the first three already-materialized eligible Scribe shortcut rows. Different actual names sharing a compact baseline receive a leading distinguishing word derived from their common actual-name prefix and suffix. The existing source baseline algorithm and noncolliding numeric/Unicode outputs are retained. The budget is 56 quarter units.

Book fallback candidates use a 48-quarter-unit budget. Complete numeric runs precede optional actual-name context. Unicode tail candidates and collided actual middle words also come from the title itself. Refinement uses only visible library, catalog, local-match, or search-group rows already in memory. Identical actual titles do not receive artificial IDs. Complete titles remain available to native full-text measurements, placeholders, accessibility, and APIs.

The sole UI writer consumes the fallback only after actual native single-line overflow, using the slot's requested family, size, weight, and tracking. Actual affected roles are library captions, CatalogGrid captions including View All, and the detail title. Already-qualified Scribe two-line search titles retain their full text and P/H2 boundaries. No history or global multiline-title feature is added.

Six focused Rust tests were added in `src/app/view.rs`:

- `colliding_search_shortcuts_expose_real_middle_words`
- `shortcut_collision_refinement_keeps_noncolliding_numeric_and_unicode_baselines`
- `collision_tokens_preserve_utf8_boundaries_inside_long_shared_words`
- `book_fallbacks_expose_complete_actual_series_numbers_on_visible_pages`
- `book_fallback_collisions_use_real_words_and_do_not_invent_duplicate_title_ids`
- `book_display_projection_retains_the_complete_actual_title`

Remote FVD must perform the actual changed-file Rustfmt, compilation, and regression execution with Cargo jobs limited to two, then verify the applicable affected Scribe scenarios and readable-cover counterexamples from the retained raw manifest. Original C14 evidence and dispositions are unchanged.
