# November 2026 design handoff

## Reference and scope

The current UI reference is the user-supplied export at
`C:\Users\moooyo\Downloads\aidoku-kindle-handoff\aidoku-kindle-handoff`,
version `1793901158-6c09`, dated `2026-11-06` in its README. This export supersedes
the earlier UI/UX PDF for visual implementation and review.

Read `HANDOFF.md` for the product and interaction rules, `tokens/tokens.json`
for tokens, `screens/INDEX.md` for the capture inventory, and
`prototype/*.dc.html` for exact declared geometry and state structure. The
prototype README explicitly says that the files require the design tool's
missing `support.js` runtime; they are source references, not runnable app pages.
Do not transplant the prototype HTML into the native Slint application.

This document records read-only research and an initial static comparison. It
does not assert implementation completion, executable verification, or either
clean review round. No local application, browser prototype, build, test, or
runtime probe was executed for this research.

The original task's cache/download performance work and retained-scene wake
behavior remain in scope. A visual redesign must preserve their correctness and
must not introduce synchronous image decoding or whole-library scans on the UI
thread.

## Precedence and explicit differences

1. The user's explicit request for an artwork-only closed reader takes
   precedence over persistent decorations in the reference. Keep page numbers,
   bookmark ribbons, status bars, notifications, and decorative gutters out of
   that default scene. The handoff's section 5.3 and `bookmarkRibbon` token call
   for a persistent bookmark ribbon; this is a documented user-directed
   difference. Bookmarks remain available in the reader menu.
2. The user explicitly reaffirmed artwork-only fullscreen after the new handoff
   was supplied: menus and other pages follow the new export, while the closed
   reader stays pure artwork. This overrides section 5.3's invisible lower-right
   footer-cycle target as well as the persistent ribbon. Do not restore that
   target or a closed-reader footer. The `fullscreen-footer` captures remain
   reference-only examples of the overridden design; record their intentional
   absence instead of treating them as missing product states. No further user
   clarification is needed for this decision.
3. Use the screenshot and prototype's declared values for exact geometry. The
   handoff describes Scribe detail as two equal columns, while
   `sc-manga.dc.html` actually declares a 384 px left column on a 930 px canvas.
   The screenshot agrees with the narrower information column. Record this
   textual simplification instead of stretching it to a 465 px sidebar.
4. The prototype contains demonstration-only controls, artificial delays,
   fabricated sample counts, small candidate dictionaries, and incomplete
   business logic. Do not implement authorization simulation buttons,
   prototype navigation links, fabricated online success, or prototype boundary
   bugs as product behavior. Production counts and progress come from records.
5. Device capabilities come from the runtime. Missing light, warmth, sensor,
   dither, or pen capabilities hide the corresponding controls. An emulator's
   measured geometry and capabilities must not be reported as a physical-device
   qualification.

## Geometry and device profiles

All dimensions below are design pixels. One design pixel equals two physical
pixels. Keep two layout profiles; do not apply a global Scribe scale factor.

| Profile | Design portrait | Physical portrait | Landscape reader |
| --- | --- | --- | --- |
| Paperwhite 5 | 618 x 824 | 1236 x 1648 | Manual orientation only |
| Paperwhite 6 | 632 x 840 | 1264 x 1680 | Stretch the PW profile; no separate reference |
| Scribe 1/2 | 930 x 1240 | 1860 x 2480 | 1240 x 930 design / 2480 x 1860 physical |
| Scribe 3 | 990 x 1320 | 1980 x 2640 | Stretch the Scribe profile; no separate reference |

Scribe defaults to portrait single-page reading and supports sensor-following
landscape spreads where a sensor is available. Paperwhite uses explicit
orientation controls. Non-reader pages have only portrait designs; retaining
portrait there is the handoff's recommended default.

| Shared metric | Paperwhite | Scribe |
| --- | ---: | ---: |
| Page margin | 22 | 36 |
| Minimum touch target | 48 | 56 |
| Status bar height | 28 | 36 |
| Main page title | 34 / 42 line height, tracking 4 | 46 / 56, tracking 6 |
| Panel title | 18, weight 900, tracking 2 | 21, weight 900, tracking 3 |
| Bottom navigation height | 72 | 80 |
| Folio pager height | 52 | 68 |
| Library grid | 4 x 2, 8 records | 6 x 3, 18 records |
| Source catalog | 5 list rows | 5 x 3 grid, 15 records |
| Detail chapter rows | First page 6, later pages 12 | 16 |

Some prototype visual controls are smaller than the global touch minimum.
Preserve the visible geometry while expanding the transparent hit target where
that does not overlap another action. Do not enlarge every visual control and
destroy the screenshot layout.

## Foundation and components

### Palette, type, and surface rules

| Token | Value | Role |
| --- | --- | --- |
| ink | `#000000` | Primary text, selected fill, borders |
| ink2 | `#555555` | Secondary text, inactive icons |
| ink3 | `#666666` | Read chapters and unavailable text |
| mute | `#999999` | Disabled items and unset filters |
| rule | `#BBBBBB` | Dotted chapter rules and idle card borders |
| hair | `#DDDDDD` | Separators and empty progress tracks |
| highlight | `#EEEEEE` | State emphasis only, including the current chapter |
| paper | `#FFFFFF` | Surfaces and secondary controls |

Use Noto Serif SC 900 for titles and book names, Noto Sans SC 400/500/700 for
interface text, and Literata 400/600/700 for numerals and Latin labels. The actual
bundled font's internal family alias can differ if it resolves to the same face.
Do not fake weight by omitting the required heavy face.

Use 1 px hairlines, 1.5 px controls, and 2 px popup outlines. Do not draw lines
thinner than 1 design pixel. Control radius is 4; small labels use 2. There are
no pill controls, gray card fills, gradients, shadows, screen-tone masks, or
dimmed modal backgrounds. `#EEE` is not a general card background.

Lists are paginated; there are no animated transitions or scrolling list
containers. Webtoon reading still advances by discrete 85% viewport steps.
Pressed controls invert as a whole; selected controls are black with white
content. Interface copy consists of labels, state, and data. Long explanations
belong in documentation, not permanent product panels.

### Navigation

The five destinations are library, browse, search, history, and settings. The
bar is full width with a 1.5 px top rule. Active icons are heavier and active
labels are weight 700. Inactive content uses `ink2`.

| Navigation metric | Paperwhite | Scribe |
| --- | ---: | ---: |
| Icon size | 24 | 27 |
| Label size | 14 | 16 |
| Active upper bar height | 4 | 5 |
| Active upper bar inset per side | 24% | 30% |
| Active / idle stroke | 2.3 / 1.7 | 2.2 / 1.6 |

Detail, the PW single-source catalog, and the reader hide the main navigation.
Scribe's top-level browse catalog and its source-management screen keep the
80 px dock. The search keyboard replaces the dock while editing. A single
`catalog` route rule cannot describe all these contexts.

### Folio pager

Use the adopted `folio` variant in `pager.dc.html`, not the unused alternatives
in `pager-options.dc.html`. The bar has three columns: `1fr auto 1fr`. Previous
and next actions include text and chevrons. Hide the first/last unavailable
action without collapsing its allocation. The center is a borderless current
page in bold Literata followed by a secondary total. With one page it is not
interactive.

Tapping the center opens a white jump panel directly above the pager with a
2 px top rule and makes the center control black. Outside taps close it. Choose
the balanced column count with
`ceil(pages / ceil(pages / max_columns))`. Selecting a page closes the panel and
combines its redraw with the content-page update.

| Pager metric | Paperwhite | Scribe |
| --- | ---: | ---: |
| Side control height | 40 | 48 |
| Side label / center numeral size | 15 / 16 | 18 / 19 |
| Jump panel padding | 14, 18, 16 | 20, 36, 22 |
| Jump cell height | 46 | 56 |
| Jump cell max width | 72 | 80 |
| Jump cell gap | 8 | 10 |
| Maximum columns | 7 | 10 |

Some import hints say 53/69; the component's declared actual heights and tokens
are 52/68. Hints must not silently add an extra pixel to all layout offsets.

### Covers and state markers

Real covers are resized to their displayed dimensions, converted to grayscale,
and cached. The prototype's eight screen-tone cover designs are fixture
artwork, not a requirement to replace real covers with placeholders. Border and
radius belong to the surrounding page component. Do not add an offset shadow.

Unread badges and downloaded icons belong beside the title below a cover;
library and search status must not cover the artwork. Source catalog membership
is a title-adjacent bookmark icon. A missing cover has a readable title plate.

### Forms and overlays

Switches have rectangular 4 px radius tracks: PW 50 x 30 with a 21 px knob,
Scribe 56 x 32 with a 22 px knob. On is black track/white knob on the right;
off is white track/black knob on the left. The whole row is interactive. Use one
label line and a `hair` separator; no explanatory subtitle.

Segmented choices are equal cells. Radio cards have a square indicator and a
fully inverted selected card. Language selection uses a right-aligned check.
Sliders have a 4 px track, square outlined thumb, decrement/increment controls,
and the 0..24 light range. Contrast uses five positions from -2 through +2.
Three-state filters cycle neutral, include, exclude.

Primary buttons are black, secondary buttons are white with a 1.5 px black
outline, and add actions have a 1.5 px dashed outline. Sheets attach to an edge
with a 2 px top rule. Popover menus have a 2 px outline, no shadow, and no scrim.
Use a transparent input-capture layer to close on an outside tap. Large Scribe
panels are bounded floating outlines near the trigger rather than full-screen
dimmed modal sheets. Confirmation dialogs have equal cancel/action columns.

Toasts are white, outlined by 2 px black, horizontally centered about 140 px
above the dock, and disappear after 2.0..2.6 seconds. They redraw only their own
rectangle. They do not appear over the user's artwork-only closed reader.

## Page and state matrix

The reference has 17 application page boards, 84 interaction-state captures,
and seven component/specification boards. That is 101 application page/state
references, plus component/specification checks. The two overview strips are
navigation aids, not full-resolution comparison targets.

| Board | Default structure | Required reference states |
| --- | --- | --- |
| `Main` | PW library | `more`, `sort`, `download-queue`, `select`, `jump-pages` |
| `pw-manga` | PW detail | `more`, `description`, `batch-download`, `tracking`, `chapters-p2` |
| `pw-reader` | PW reader with menu open | `fullscreen`, `fullscreen-footer`, `toc`, `mode`, `image`, `light`, `rotate`, `more` |
| `pw-browse` | PW source management | `available`, `updates`, `add-source`, `source-lists` |
| `pw-source` | PW single-source catalog | `filter`, `source-settings` |
| `pw-search` | PW search with keyboard | `results`, `numbers` |
| `pw-history` | PW history | `updates`, `clear-confirm`, `incognito` |
| `pw-settings` | PW settings list page 1 | `page2`, `display`, `power` |
| `sc-library` | Scribe library | `more`, `sort`, `download-queue`, `select`, `jump-pages` |
| `sc-manga` | Scribe detail | `more`, `tracking`, `migrate` |
| `sc-reader-portrait` | Scribe portrait reader with menu open | `fullscreen`, `fullscreen-footer`, `toc`, `mode`, `image`, `light`, `orientation`, `more` |
| `sc-reader` | Scribe landscape spread with menu open | `fullscreen`, `toc`, `mode`, `layout`, `image`, `light`, `more` |
| `sc-browse` | Scribe current-source catalog | `source-picker`, `filter`, `source-settings`, `server-setup`, `blocked-source` |
| `sc-sources` | Scribe two-column source management | `updates`, `add-source`, `source-lists` |
| `sc-search` | Scribe search with keyboard | `results` |
| `sc-history` | Scribe two-column history | `updates`, `clear-confirm`, `incognito` |
| `sc-settings` | Scribe split settings, display selected | `about`, `auto-update`, `backup`, `categories`, `download`, `image`, `language`, `light`, `logs`, `pen`, `power`, `reading`, `source-lists`, `spread`, `storage`, `tap-zones`, `tracking-login` |

The component/specification boards are `cover`, `nav-pw`, `dock-sc`, `pager`,
`pager-options`, `spec-foundation`, and `spec-interaction`. Only the adopted
folio pager option is an implementation target.

### Library

PW header padding is `4 10 8 22`; Scribe is `8 24 16 36`. The header has a
secondary count/update caption above the heavy title and only the appropriate
update/sort/more actions. Scribe includes the actual completed-download size.

PW continuation is white with a 1.5 px outline, 16 px horizontal margin,
`12 14 12 12` padding, a 62 x 88 cover, 16 px content gap, 22/28 book title,
and a 52 px black arrow action. Scribe uses a `1.55fr 1fr` continuation/new-chapter
split with a 16 px gap and 28 px outer inset; its cover is 98 x 136 and arrow
action is 64 px.

| Library metric | Paperwhite | Scribe |
| --- | --- | --- |
| Category bar | 48 high; gap 24; label 17; count 13; underline 3 | 64 high; gap 30; label 20; count 15; underline 4 |
| Grid padding | 12, 22, 0 | 22, 36, 0 |
| Grid row / column gap | 14 / 14 | 22 / 20 |
| Cover inner dimensions | 130 x 160 | 124 x 170 |
| Cover-to-caption gap | 8 | 9 |
| Caption size / line height | 15 / 20 | 16 / 22 |
| Title-row unread label | 20 x 18 | 22 x 20 |

Sorting has eight two-column radio choices, explicit ascending/descending
choices, and four three-state filters. Multi-select replaces the title/actions
and bottom dock with selected-count, select-page/done, and four bulk actions.
PW hides continuation in selection mode; the Scribe prototype retains its two
cards. Queue rows show a cover, chapter, progress, and a real waiting/active state
with one global pause/resume action.

Library overlay declarations are:

| Overlay | Paperwhite | Scribe |
| --- | --- | --- |
| More menu | top 84, right 14, width 272, padding `6 0`; four 54 px rows | top 112, right 28, width 340, padding `8 0`; three 62 px rows |
| Sort | Full-width bottom sheet; padding `18 22 24`, gap 12; 23 px title; 46 px choices | top 112, right 28, width 440, padding 22, gap 14; 26 px title; 54 px choices |
| Queue | Full-width bottom sheet; padding `18 22 24`, gap 10; 36 x 50 covers; 4 px progress | Centered width 640, top 300, `border-box`, padding `28 30 26`, gap 12; 46 x 64 covers; 5 px progress |
| Selection check | top/left 6, 28 square, 2 px outline, radius 3 | top/left 8, 34 square, 2.5 px outline, radius 3 |
| Bulk bar | 72 high; padding `6 8 8`; four equal actions, 23 px icons / 13 px labels | 80 high; padding `8 60 10`; four equal actions, 26 px icons / 16 px labels |

Both reference library toasts anchor at bottom 140 and use 2 px outlines; PW
padding is `12 22` at size 15, Scribe is `14 26` at size 18. Unless explicitly
marked `border-box`, a prototype width is its declared CSS content width;
outer border/padding extents are not automatically included. The sheets do not
declare fixed heights and follow content. The Scribe sort screenshot only draws
the eight sort choices and Done, although the product specification describes
the additional direction/filter behavior; keep the distinction explicit when
reviewing screenshot geometry and feature completeness.

### Detail

PW has a 52 px return row, 132 x 186 cover, 30/38 title, three-line 15/24
description, and a 56 px continuation action. The 52 px chapter header is
followed by 52 px rows: chapter number allocation 46 px at size 17, chapter
title size 16, download touch area 52, visible state control 30.

The first PW page holds six chapters below the information block. Later pages
hide the cover/information/actions, put an 18 px book title in the return row,
and hold twelve chapters. For `n > 6`, page count is
`1 + ceil((n - 6) / 12)` and the one-based page `p >= 2` starts at zero-based
offset `6 + (p - 2) * 12`. Thus 86 chapters correctly produce eight pages. The
old claim that the design necessarily required 15 pages is obsolete.

Scribe uses a fixed 384 px total left column (`border-box`, padding
`6 30 24 40`) under a 60 px return row and 36 px status bar. Its cover is
306 x 420 inside its border. The directory begins at x=384 with 22 px right
padding, an 84 px declared header plus 12 px bottom padding and 1.5 px rule,
and sixteen 60 px chapter rows. Number allocation is 40 px at size 19;
chapter titles are size 18; the download touch area is 56 with a 34 px state
control. The book title is 40/50, continuation action 66, the three actions are
72 high, description is serif 17/31, and tags are white outlined 36 px controls.

The current chapter is `#EEE` with current-reading state. Read chapters use
`ink3`. Download states distinguish unavailable, queued/waiting, downloaded,
and active. Do not turn downloaded state into an inert disabled control when
the action is supposed to remove its download.

PW adds description, batch-download, and tracking sheets. Batch choices are
next five, all unread, custom range, and removal of read downloads, with actual
free space. Scribe adds a tracking float and migration float. Migration keeps
match selection separate from read-state/category/tracking/download-removal
options; a mock candidate list must not be reported as a completed migration.

PW detail More is at top 76/right 14/width 260 with four 52 px rows. Description
is a full-width bottom sheet with `18 26 28` padding, a 23 px title, serif 18/32
body, 34 px tags, and `88px 1fr` metadata columns. Batch download uses
`18 22 26` padding and 56 px actions. Tracking uses the same outer padding, an
outlined account card with `14 16` padding, three equal state/progress/rating
columns, and 40 x 56 increment/decrement controls. All use transparent outside
capture; there is no PW migration-panel screenshot from which to invent fixed
geometry. Scribe More is at top 92/right 24/width 340; tracking is top 260/width
640; migration is top 220/width 680.

### Reader

`pw-reader`, `sc-reader-portrait`, and `sc-reader` default captures have the
menu open. The `fullscreen` captures are the actual reading state. The image
viewport reaches every display edge and preserves comic aspect ratio. A
landscape spread has two 50% page allocations with no decorative seam.

| Reader metric | Paperwhite | Scribe |
| --- | --- | --- |
| Menu header | 26 px reader status + 58 px content row, with bottom rule | 78 px total, integrated time/battery |
| Header book caption | 12 / 17, tracking 3 | 13 / 18, tracking 4 |
| Header chapter title | 19 / 26, tracking 1 | 23 / 32, tracking 2 |
| Bottom tools height | 68 | 76 |
| Tool label / icon | 13 / 23 | 15 / 26 |
| Track/content horizontal inset | 20 | 48 portrait; 120 landscape |
| Progress container height | 36 | 40 |
| Progress thumb | 22 square | 24 square in page prototypes |
| Chapter action height | 40 | 44 |
| Reader panel title | 18, weight 900 | 21, weight 900 |
| Direction cards | 88 high; gap 8 | 100 high; gap 12 |
| ToC rows | 46 high, single column | 50 high, two columns with 48 gap |

Scribe portrait tools are mode, picture, brightness, orientation, more.
Landscape replaces orientation with layout, and has the explicit orientation
lock action in its header. Paperwhite includes page layout choices in the mode
panel and manual portrait/landscape/reverse-landscape choices.

Ordinary horizontal tap zones are 33/34/33%; landscape Scribe spreads are
30/40/30%. RTL maps left to next and right to previous, with explicit inversion
support. Vertical modes use top/middle/bottom at 33/34/33%; webtoon steps are
85% of the viewport. Menu-open input never turns a page. An outside/central tap
closes the menu. Turning past the end enters the next chapter and requests a
full refresh. Seek dragging previews only the page number; decode starts on
release. Preserve the lifecycle/input generation fences around deferred input.

Mode offers four directions plus crop/split controls. Picture contains the
actual preview, five contrast positions, refresh interval choices, dither
capability/state, and night mode. Light uses 0..24 controls, conditional warmth,
and Scribe auto brightness where available. ToC includes chapter/bookmark tabs.
More contains the explicit footer setting, tap-zone display, inversion where
applicable, and manual full refresh. Prototype-only landscape-preview links are
excluded.

### Browse and source management

The file names do not describe the same route on both profiles:

| File | Product role | Navigation |
| --- | --- | --- |
| `pw-browse` | Top-level installed/available/update source management | PW dock, no pager |
| `pw-source` | Secondary single-source catalog | No dock |
| `sc-browse` | Top-level current-source catalog with source picker | Scribe dock |
| `sc-sources` | Separate two-column source manager | Scribe dock |

PW management has 48 px tabs, 58 px installed rows, 44 px source tiles,
17/23 source names, 64 px available rows, and minimum 84 x 40 install actions.
Group built-in and extension sources, make setup/blocked state explicit, and
use the dashed add-source action. Its add panel contains QR/local-address,
waiting state, and direct URL input; source lists are removable outlined cards.

PW catalog has a 48 px return row, 52 px source tile, 30/36 name, 48 px tabs,
and five 108 px rows per page. Covers are 64 x 88 with 16 px content gap;
titles are 19/26. Catalog search, filter, and source settings remain distinct.

Scribe browse has a 64 px source title row, 52 px tile, 40/48 name, 62 px tab
row, 300 x 48 source-scoped search field, and a 5 x 3 grid. Inner covers are
150 x 202, the grid gap is 20, padding is `20 36 0`, titles are 17/23,
and metadata is 14/18. The source picker is a bounded 620 px float at
`top=162,left=28`; filter is 620 px at `top=282,right=28`; source settings is
540 px at `top=112,right=28` in the reference canvas.

Scribe management has a fixed 400 px installed-source column and a right
available/update column. Right-side pagination has ten records; it does not
page the installed column. Rows are 68 px, tiles 48, names 18/25, secondary
text 14/20, tabs 60, language filters 42, and install actions at least 96 x 44.
The add/list floats are 700 px wide at top 250, with a 200 px QR region.

Filter state includes ordering, multiple status choices, and three-state tags.
Source settings are driven by declarations. Self-hosted sources have real
configuration/test/connect states. Browser-verification-dependent sources show
unavailable state with migration/uninstall actions; they do not fake a WebView.

### Search and keyboard

| Search metric | Paperwhite | Scribe |
| --- | --- | --- |
| Header height | 68 | 84 |
| Search input height | 52 | 60 |
| Scope visual height / gap | 38 / 8 | 44 / 10 |
| Results per source | Up to 3 | Up to 6 |
| Inner result cover | 102 x 120 | 124 x 164 |
| Result gap | 18 | 22 |
| Source group title row | 40 | 48 |
| Keyboard candidate row | 52 | 64 |
| Letter and bottom-row key height | 52 | 62 |
| Keyboard row gap | 7 | 10 |

The input and scope buttons are white with black outlines. Search typing on
Scribe shows immediate local library matches without network traffic. PW shows
the pending scope count. Pressing search hides the keyboard and restores the
dock. Results are grouped by source; empty, timeout, retry, and retrying states
are independent. Earlier sources can display before slower sources complete,
with a 300 ms combined refresh cadence. Never replace a failed source with
fabricated results.

The on-screen keyboard has composition/candidates, QWERTY, shift, backspace,
numbers/symbols, language, space, and search. Literal prototype dictionaries are
illustrative, not a complete input method. Key feedback has a 100 ms budget.
The Scribe local-match cover is 72 x 98. Result membership icons stay beside
titles, not over images.

### History

PW history rows are 76 px with 46 x 64 covers and 14 px image/text gaps. Date
groups use a 38 px declared label row plus 6 px bottom padding. Scribe uses
two equal columns, 32 px column gap, 124 px rows, 72 x 98 covers, 16 px
image/text gaps, and 52 px group rows plus 8 px bottom padding.

Keep grouped dates, book/chapter/progress/time, per-entry remove, update
download state, and actual pagination. Updates change the page title and expose
bulk download. Incognito uses an inverted status bar and pressed header action;
the handoff recommends persisting that status treatment across all normal
application pages until incognito is disabled.

Clear confirmation is an outlined white dialog with no dimmed background. PW
uses left/right 44 and top 250 with `26 24 20` padding and 50 px action height.
Scribe uses width 560, top 360, centered horizontally, `32 32 26` padding,
and 60 px actions. Clearing and individual removal must update real records.

### Settings

PW has a device card and two pages of grouped summary rows. Entering a setting
is a dedicated secondary page with a labeled back action. Scribe places device
information in the main title header and uses a fixed 320 px grouped navigation
column with a 610 px detail area. It has no list pager; only the 80 px dock.

Scribe left choices are 46 px visual rows with `0 14 0 20` margins, 21 px icons,
and size 17 labels. The active row is black. The detail area padding is
`26 36 20`, title 30/40, segmented controls 50 high, radio cards at least 60,
square indicators 24, and normal switch/data rows at least 60.

PW title padding is `4 22 10`; its version caption is size 13 with tracking 2.
The device card exists only on the first list page, has margin `0 16 6`,
padding `14 18`, a 1.5 px outline, 16 px gap, a 40 x 54 device graphic, and
Literata 18 device name. Group headers have declared height 34 plus 4 px bottom
padding. List rows are 52 px, icons 22, labels 17, summary values 14 with a
240 px maximum. The first page has seven settings rows; page two has nine.
Secondary pages retain the dock and hide the list pager; they use a 48 px back
action and a 30/38 serif title.

PW display uses `6 22 14` content padding, 44 px refresh choices, 52 px minimum
quality cards, 22 px square indicators, 54 px switch rows, and a 34 px
sixteen-step grayscale strip. PW power has three equal screensaver choices
with 150 px previews and 12 px gaps; selection is a 3 px black border and idle
is a 1 px `mute` border. The quit row is a white outlined card. PW tracking
expands pending authorization inside its account card; the 150 px QR region,
code, pending/cancel state do not require a separate modal overlay.

The full settings state set contains display/refresh, light/warmth, pen, power,
storage, reading direction, tap zones, image treatment, spread/orientation,
categories, automatic updates, source lists, downloads, tracking, backup,
language, logs, and about. Display includes the sixteen-step grayscale test.
Power includes keep-awake downloads, charging updates, reading Wi-Fi policy,
three screensaver choices, and exit. Storage reports downloaded/cache/cover/free
space separately and offers targeted cleanup. Download settings include advance
chapters, resizing, grayscale persistence, and destination. Tracking has real
pending/authorized/failed/offline state, with no simulation action.

## Refresh, gesture, and performance contract

| Event | Policy | Region |
| --- | --- | --- |
| Control press/release | Fast A2/DU, then UI restore | Control rectangle |
| Reader menu | UI GL16; optional flash on close | Header and footer rectangles |
| Popup/dialog/toast | UI GL16 | Current or previous overlay rectangle |
| List/tab page | UI GL16 | Content and pager number, fixed chrome retained |
| Jump panel | UI GL16 | Panel; merge selection with list update |
| Comic page | Partial REAGL/GL16 | Artwork; full every configured N, default 6 |
| Enter reader / chapter change | Full GC16 | Full viewport |
| Seek drag / release | Fast preview, then partial | Number preview, then target artwork |
| Cover and source-search completion | Combined UI update every 300 ms | Changed visible content |
| Night mode | Hardware inversion where actually supported | Driver operation; documented fallback otherwise |

Unsupported waveforms safely fall back to GL16. These names are intent, not
evidence that a physical panel completed a waveform. Preserve existing refresh
submission error reporting and native wake ownership/fencing.

Gesture declarations are hold >=500 ms, double tap <300 ms, swipe within
900 ms and >=5.6 mm, and top-edge menu pull beginning within 8 mm. Double tap
zoom is off by default; enabling it delays single-tap disambiguation. A pinch
finishes with a full refresh. There are no hardware page-turn keys on the target
models.

Bound visible records by the page: library 8/18, PW catalog 5, Scribe catalog
15, source-manager right column 10, detail 6 then 12 or Scribe 16, grouped
history within its page. Only visible covers should request pixel work. The
handoff calls for screen-sized grayscale processed caches, two-page prefetch,
and segmented long-webtoon decoding. Reuse prepared pixels and page manifests;
do not decode a cover during normal view projection. Bulk queue changes should
produce coalesced presentation updates rather than one full projection per
accepted chapter. Scribe's downloaded-size caption must use the maintained
distinct-file snapshot, not per-frame filesystem enumeration.

Resume should display the retained application scene directly, preserving the
user's displayed chapter/page and image geometry. Menu panels may be closed
for a clean artwork scene, but restoring that first frame must not require
catalog reloads, metadata SQL, font startup, source-policy parsing, or image
decoding. Keep the existing lifecycle gate before handling new completion or
input callbacks. This is separate from changing the operating system's
screensaver cover.

## Initial high-confidence implementation gaps

These are static findings from the pre-redesign working tree, not results from
running the current application. They are expected to become obsolete as the
assigned implementation files are changed.

| Area | Initial gap | Required change |
| --- | --- | --- |
| `ui/app.slint`, `theme.slint`, `widgets.slint` | Common 30 px status offset, 78 px Scribe dock, shared navigation type/icon metrics | Use profile-specific 28/36 and 72/80 geometry everywhere |
| `widgets.slint` pager | Arrow-only pager with static center; inconsistent page overrides | Folio labels, preserved edge allocation, balanced jump panel |
| Library/cards | Gray continuation surface, offset cover shadows, over-image unread labels | White outlined continuation, no shadows, title-row state |
| PW detail | Fixed six-row list and always-visible information | First-page information plus six rows, then compact twelve-row pages |
| Scribe detail | Proportional sidebar and stretched row geometry | Fixed 384 px sidebar and 60 px rows from prototype |
| Source routes | Scribe horizontal source tiles and shared hidden catalog dock | Current-source browse grid/picker plus separate 400 px source manager |
| Search | Gray input/scopes, Scribe five-cover limit, shadow and image-overlay membership | White outlined input/scopes, six results, title-adjacent state |
| Scribe settings | Full-width summary list, gray device card, pager | Header device summary and 320 px split navigation/detail |
| Panels | Screen-tone capture mask and solid offset shadow described by old fidelity document | Transparent capture, 2 px outlines, no mask or shadow |
| Toast/status | Full-width black top notice | Small white outlined lower toast; incognito owns inverted status treatment |
| Documentation | Old PDF and incorrect fixed-six-row pagination explanation | Reference this handoff and variable PW detail page capacity |

## Required review loop

Run all execution on `ssh test-env` or the designated Aidoku guest. Local reads
and edits do not authorize local verification. Preserve the source snapshot,
candidate binary hash, design version, capture dimensions, and actual device
capabilities for every review round.

1. Compare every applicable page/state in the matrix with its matching full-size
   reference. Use PW5 1236 x 1648 and Scribe 1860 x 2480 / 2480 x 1860 for exact
   reference captures. Capture stretched PW6/Scribe3 separately; do not rescale
   their results and call them exact-device pixel matches.
2. Record deliberate differences separately: the user's artwork-only reader,
   real data/counts, absent hardware capabilities, unavailable third-party
   integrations, and supported driver fallbacks. These are not blanket waivers
   for layout differences elsewhere.
3. Inspect all enabled controls: open/close/outside tap/back, selection, first and
   last page, center jump, source switching, page-two detail, keyboard modes,
   independent retry, grouped history, queue global controls, and settings
   state. Each visible control must have a concrete behavior or honest
   unavailable state, not a simulated successful operation.
4. Check empty/long-title/offline/timeout/no-front-light cases, odd final spread,
   chapter boundaries, saved bookmark and progress, cache invalidation, batch
   queue pressure, and sleep/wake back to the same artwork scene.
5. Record each actionable finding with reference, observed evidence, owner,
   change, and retest. A change resets the clean-round streak. A review round
   is clean only after the whole applicable matrix has been reviewed on the
   same frozen candidate and no new actionable suggestion remains.
6. Exit only after two consecutive complete clean rounds against this new
   handoff. Prior C5/PDF review rounds do not count toward this exit condition.

