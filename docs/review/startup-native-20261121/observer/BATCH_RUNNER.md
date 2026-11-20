# Interface for the normal-entry batch runner

The observer requires **one new stock capture for each measured visit**. After
the native target guard confirms the real stock tab and uniquely verified
tile, and before starting `framewatch watch`, run `framewatch capture
visit-stock.frame` in this visit's private output directory. Reading
`framewatch clock` around dispatch supplies device-clock bounds. Keep the
identity guard, capture, trigger bounds and trace bound to the same visit.

The host analyzer calls `observer.py measure RULE TRACE EARLIEST LATEST --stock
VISIT_STOCK`. The option is mandatory. Reference-only stock or the beginning
of a timed trace must never be substituted. The separate `observer.py
check-stock RULE VISIT_STOCK` verifies that the admitted native view visibly
differs from every fixed text/artwork region without consulting timing. The
three-reference `freeze` command is unchanged.

`compare-settled` now requires both `settled_full_body_equal` and
`right_status_equal_and_observable`. Require an independent late frame.
Changes to right status, including routine changes of Wi-Fi or battery icons,
invalidate that picture; do not broaden the mask to turn a rejection into a
pass. Left-hand time may differ.
