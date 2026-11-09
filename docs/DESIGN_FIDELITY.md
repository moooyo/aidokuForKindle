# Design fidelity

## Current Candidate18 scoped result

The [final UI closeout](review/phase3-c18-final-search-repair-closeout-receipt-01.json)
accepts two fresh bounded Search/F13 repair walkthroughs on the same frozen
236-file source, `42b11de8d1b07e7af30665f98bb0dbf6291c0d6a6ec3355005c1402f42b21f24`,
and desktop application,
`f07072757d52b78e2d3fc8c71e2e1040bbf55f0ce73859260d30dde898d8b269`.

| Actual review | Round 1 | Round 2 |
| --- | ---: | ---: |
| Manual cases reviewed | 8 | 8 |
| Fresh reports | 3 | 3 |
| Comparison pairs | 9 | 9 |
| Necessary image opens | 14 | 14 |
| Chapter-unit/completion-status columns independently read | 27 | 27 |
| Required findings / reading pending / identity mismatches | 0 / 0 / 0 | 0 / 0 / 0 |

Each round used its own Reader's processed image, expected composition and current
frame. These are two clean bounded repair passes, without a complete Candidate18
reference or stress-suite result. The
[C16 first reference review](review/phase3-c16-final-design-pass1-closeout-receipt-01.json)
and [negative second review](review/phase3-c16-final-design-pass2-closeout-receipt-01.json)
retain their original source, desktop, findings and scope. Original diagnostic
failures remain preserved in the current closeout's evidence links.

Targeted native result:

The final C18 targeted Native sequence passed two bounded actual cases: single90 and manual90/270 touch, 270 Down/Up and retained flat wake. The same final PID6613 retained epoch5/page15/manual270 with pressure0, exact wake pixels and no image-job restart. Normal exit restored the exact stock body, policy/USB1 and left no App, sensor, guard or owned lock. These are dedicated firmware-VM synthetic-sensor/RAM-fixture results, not physical Kindle or a repeat of C16 full-matrix acceptance; original failed and unassessed attempts remain preserved.

Formal performance result:

The final C18 formal sequence passed one actual exact ignored library test with twenty measured JSONL records, 21 primary timing processes, six SQL collections, one observer and one marked publication/sync audit. Source, both ELFs, all four tools and the wrapper matched before/after; the Native owner checked-restored the dedicated VM with stock/policy/USB/no-App-lock checks passing. Measurements are bounded host algorithm/render/Store evidence, not physical Kindle or a universal speedup; the 32-page counterexample, limited allocation/RSS counters and scale-dependent SQL remain documented.

The final package recorded 137 strict passing checks and 26 members, with CRC,
ordinary return verification and I/O owner release complete. See the
[strict report](../artifacts/phase3/candidate18-final-root-01/package-final-01/package-verification-01.json)
and [current delivery record](DELIVERY.md) for exact package bindings. The
[original design fidelity document](../artifacts/phase3/candidate15/documentation-staging/current-documents-before-final-01/docs/DESIGN_FIDELITY.md)
and [original README](../artifacts/phase3/candidate15/documentation-staging/current-documents-before-final-01/README.md)
preserve the earlier complete-round contract and dated checkpoint wording.

## Reference and geometry

The implementation follows the user-supplied November handoff export
`1793901158-6c09`. [DESIGN_HANDOFF.md](DESIGN_HANDOFF.md) records its scope,
tokens, prototypes and capture inventory. It supersedes the earlier 18-page UI/UX
PDF for current implementation and review. The original export and PDF remain
outside the repository. Demo artwork provenance and crop information are in
`assets/demo/manifest.json`; reference images and review captures are evidence,
rather than application UI backgrounds.

One design pixel represents two physical pixels at approximately 0.17 mm. The
layout does not multiply every control by the short side of the display. Scribe
uses modestly larger controls and spends its additional area on columns and
parallel content.

| Design profile | Logical portrait canvas | Declared physical resolution |
| --- | --- | --- |
| `pw5` | 618 x 824 | 1236 x 1648 |
| `pw6` | 632 x 840 | 1264 x 1680 |
| `scribe`, `scribe2` | 930 x 1240 | 1860 x 2480 |
| `scribe3`, `scribe3-nofrontlight` | 990 x 1320 | 1980 x 2640 |

Native runtime geometry comes from the actual visible framebuffer. A firmware
emulator can therefore have a runtime size that differs from the declared design
profile. Captures record both values rather than silently treating them as the
same measurement.

## Preserved design rules

- The eight UI grays are `#000`, `#555`, `#666`, `#999`, `#BBB`, `#DDD`, `#EEE`,
  and `#FFF`. Comic images are quantized separately to sixteen gray levels.
  Gray tokens have specific text, rule and state-emphasis roles; `#EEE` is not a
  general card fill.
- Noto Serif SC 900 carries page and book titles; Noto Sans SC 400/500/700 carries
  interface text; Literata carries numbers and Latin labels. The bounded visual
  reviews read visible glyphs and extents; they did not measure exact font identity
  or numerical weight.
- Paperwhite uses a four-column, two-row library. Scribe uses six columns and
  three rows, a continuation/update split, and a two-column detail page.
- Lists are paged. Navigation and reader toolbars touch the display edges.
  Selected navigation has a black upper rule. Controls invert when pressed.
- There are no animated transitions, rotating loaders, hover effects, shadows,
  screen-tone masks, gray card fills or dimmed modal backgrounds. Panels use
  2 px black outlines and a transparent input-capture layer for outside-tap
  dismissal.
- The Reader uses percentage tap regions, direction-aware turns, menu gating,
  bookmarks, preview-on-drag/release-to-decode, and 85% vertical steps. Chapter
  changes request full refresh.

The user's artwork-only instruction takes precedence over persistent Reader
decorations in both supplied references. A closed Reader exposes only artwork
over the full viewport: no page number, bookmark ribbon/corner, footer, status or
debug overlay, download notice, decorative spread gutter or invisible footer-cycle
target. Aspect ratio is preserved rather than clipping panels to fill a mismatched
canvas. Reader controls are compared with the menu explicitly open. Closing a
menu/panel or pressing Escape restores the image-only scene.

An apparent border or caption within a comic must be traced through that Reader's
own source, processed image, expected composition and current frame. Another
Reader's image or a mechanical pixel count does not establish its provenance.

## Data-dependent pagination

Pagination is calculated from actual records and each page's visible capacity.
Paperwhite detail keeps six chapter rows below the first-page information block.
Later pages hide that block and hold twelve chapters. For `n > 6`, page count is
`1 + ceil((n - 6) / 12)`; page `p >= 2` begins at zero-based offset
`6 + (p - 2) * 12`. Thus 86 chapters produce eight pages, as specified by the
current handoff.

Scribe detail holds sixteen chapter rows; 86 chapters produce six pages.
Catalog capacity is five records per Paperwhite page and fifteen per Scribe page.
A dataset of 128 records therefore produces 26 and 9 pages respectively. These
are arithmetic examples; actual counts and acceptance examples belong to their
own persisted dataset and captures.

Normal libraries, search results, imported comics, history and configured sources
derive their counts from their actual filtered persisted records. Design sample
statistics are not shown as if they were the user's real library.

## Platform-dependent differences

Inversion uses the implemented backend path and its full-refresh boundary. UI
updates use driver-selected partial refresh; touch feedback uses DU, and the
configured comic-page interval submits flashing GC16. Driver waveform names and
physical behavior vary by panel/firmware; desktop frames and firmware-VM runs do
not establish physical ghosting or refresh latency.

Manual orientation includes 90/270-degree landscape. On a recognized sensor, the
active app follows Up/Down as 0/180-degree presentation while retaining the current
chapter/page and manual 90/270-degree preferences. Other orientations remain
manual. Synthetic sensor input and a RAM fixture in the dedicated firmware VM are
software-test conditions, without physical Kindle sensor, persistent-filesystem
or e-ink waveform acceptance.

Automatic brightness is not implemented or qualified. The lighting controls cover
only exposed frontlight/warmth capabilities and are hidden or disabled when
unavailable. Automatic rows visible in some Scribe references are absent, with
their layout differences recorded in the
[lighting scope disposition](review/phase3-c16-final-design-pass2-light-scope-adjudication-01.json).
This limitation establishes neither a Scribe model's hardware capability nor
unconditional reference parity. A design profile alone provides no physical
hardware acceptance.

Compatible current Aidoku WASM packages can execute standard/HTTP/preferences/
read-only HTML paths. Source flows requiring legacy ABIs, JavaScript, canvas,
WebViews, browser verification, DOM mutation, or text-only reader pages show
capability errors. Tracking authorization/synchronization and full cross-source
migration are not complete integrations.

## Review scope and retained history

The accepted Candidate18 exit was two fresh clean walkthroughs of the affected
Search/F13 repair and the necessary own-Reader control. Each round independently
read the required visible units and completion status; normal whole-character
author elision remains intentional. Related fixture comparisons do not establish
exact provider state or design-pixel equality.

The earlier complete design-reference rounds and stress plans retain their own
candidate inputs and outcomes in [REVIEW_LOOP.md](REVIEW_LOOP.md), the original
documents linked above and their original receipts. C16's negative second review
remains negative. Unexecuted large stress plans remain unexecuted; no historical
image, Native result or timing measurement is transferred to Candidate18.
