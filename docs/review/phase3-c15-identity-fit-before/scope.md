# C15 Display Identity Change Scope

The three `.before` files in this directory are untouched text copies captured before the C15 display identity edit. No local verification, formatting, hashing, image inspection, or runtime probe was performed.

Authorized production scope:

- `src/app/view.rs`: bounded, actual-name-derived display fallback generation, visible sibling collision refinement, and focused regression tests.
- `ui/state.slint`: an independent `BookItem.display-title` field. The complete `BookItem.title` value is retained.
- `src/app/migration.rs`: the required new-field initializer in the existing complete `BookItem` literal. Migration behavior and the complete candidate title remain intact.

The source shortcut budget remains 56 quarter units. Only different actual names with the same compact baseline are refined. Existing noncolliding numeric and Unicode source labels retain their baseline output.

Book fallback candidates use 48 quarter units. Complete numeric identifiers or actual distinguishing words precede optional name-derived context. Collision refinement is restricted to already-materialized visible page rows. No new database, disk, image, or full-library work is added.

The sole UI writer owns native full-text measurements and bindings in actual clipped single-line slots. Full titles remain the source for cover placeholders and accessibility. Existing successful two-line search title paths retain their complete text and geometry. Closed reader rendering remains artwork-only.

Required verification is a fresh remote FVD execution on `ssh test-env`, with Cargo jobs limited to two. These edits do not establish a C15 pass or change any C14 report disposition.
