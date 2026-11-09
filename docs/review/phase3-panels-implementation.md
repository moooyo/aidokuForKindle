# Phase 3 panels and settings implementation

This document records implementation scope and static review findings. It is not
an executable verification result or a clean visual-review round. All runtime,
build, and screenshot verification belongs to the remote candidate workflow.

## Native surfaces

- `ui/panels.slint` dispatches modal contexts to native Slint components. Outside
  input capture is transparent. Paperwhite sheets touch the bottom edge; Scribe
  popovers use the handoff's trigger-relative or centered positions.
- `ui/panels-common.slint` supplies outlined actions, headings, data rows, and
  dashed frames. Popup outlines are black, surfaces are white, and there is no
  scrim, screen-tone mask, offset shadow, gradient, or animated transition.
- `ui/panels-library.slint` implements eight sorting choices, direction, three
  state library filters, and the actual paginated download queue. Active and
  completed queue rows show real completed/total page data when known. Global
  pause/resume is enabled from actual queue state.
- `ui/panels-detail.slint` implements description/tag/data sheets, batch actions,
  unconfigured tracking accounts, and equal-column confirmation dialogs. All
  tags remain reachable through two-row local tag pagination.
- `ui/panels-sources.slint` implements the real pairing QR/address/code flow,
  stored repository cards, and the bounded installed-source picker. Empty
  built-in groups are omitted on extension-only picker pages.
- `ui/panels-catalog.slint` implements real loaded-catalog sort/status/tag
  filters. The result label explicitly describes loaded records. Unsupported
  follows/rating sort keys are disabled. Narrow three-state tag controls retain
  readable titles in every state, and additional tags have local pagination.
- `ui/panels-source-settings.slint` renders actual source preference schemas,
  including inline option groups, switches, and editable value rows. Secure
  values remain masked. Source changes are disabled during an outstanding save.
- `ui/panels-keyboard.slint` restores the reference 300/370 design-pixel keyboard
  heights, actual IME candidates, candidate paging, and all symbols needed for
  real URLs and paths. Non-search forms retain a visible input strip.
- `ui/panels-reader.slint` implements seven explicitly opened Reader panels.
  The shared ReaderMetrics contract positions them above the retained
  188/210-pixel Reader controls/toolbars without covering header/tool actions.
  The table of contents preserves five Paperwhite rows or two Scribe columns
  of three rows, with bounded pagination for the remaining chapters.
- `ui/settings.slint`, `ui/settings-controls.slint`, and
  `ui/settings-sections.slint` implement the Paperwhite two-page settings list,
  secondary settings views, and the Scribe 320-pixel navigation column with its
  dedicated settings area and no root folio pager.

## Data and capability boundaries

The closed Reader remains image-only under the user's explicit override. No
permanent page footer, bookmark ribbon, lower-right footer target, status bar,
or success toast is restored. Reader panel dismissal preserves its open menu;
the next outside tap can close the menu through the ordinary Reader action.

Counts, progress, repository records, source settings, pairing codes, and
connection states come from application models. The UI does not fabricate a
logged-in tracking account, authorization success, migration match, source
capability, or download-size estimate. Missing hardware controls are hidden;
software policies without a runtime contract are clearly unavailable.

The Scribe sort reference image depicts the eight sort cards and Done only.
The implementation also exposes direction and three-state filters required by
the handoff's product behavior, extending that popover's height.

Native source filters operate on actual loaded catalog records because the
portable source API does not expose a universal remote tag/rating filter.
The explicit loaded-result label prevents that bounded view from appearing to
represent the entire remote source.

## Static review repairs

Independent source reading identified and repaired three initial issues: queue
rows lacked numeric page progress, description tags after the sixth item were
unreachable, and extension-only picker pages retained an empty built-in group.
A second reading identified narrow included/excluded catalog tags losing their
titles; the catalog now uses compact prefix markers. No definite intrinsic
layout cycle was found in the source-preference layout.

A source `picker` wire-type mismatch was reported to the interaction owner:
picker/select use string values, while segment uses an integer index. Its
runtime verification remains part of the remote regression gate.

These reviews did not launch the application or prototype, execute a test,
capture an implementation screenshot, or establish either required clean round.

## Candidate 1 diagnostic geometry repair

The remotely captured Paperwhite and Scribe default settings PNGs were viewed
directly beside the supplied reference PNGs. The Scribe heading and device strip
appeared in the middle of the page, while the Paperwhite heading and device card
were covered by later content. The header/card containers omitted explicit
origins, and several section labels, fills, and decorative lines depended on
implicit positioning.

The settings header, device cards, section wrappers, absolute controls, and
capture layers now have explicit geometry. The same issue was corrected in the
panel family: top rules are anchored at zero, progress fills start at their
declared edge, labels stay in their allocated columns, and outside capture keeps
its original extent. Layout-managed children retain their layout constraints;
intrinsic source-preference layout retains its content-derived height.

The repairs preserve data contracts, callbacks, row sizes, capability gating,
and the image-only closed Reader. They require a newly built remote candidate
and fresh screenshots before they can be regarded as visually verified. The
initial diagnostic captures do not establish a clean review round.
