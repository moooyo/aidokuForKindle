# Sleep and wake display continuity

Reviewed: 2026-11-11, Asia/Shanghai.

## Observed problem

During the physical Scribe b03 manual qualification, the user saw the native
bookshelf appear on entering sleep and again on wake. The application retained
PID 23556/start-time identity 745807466 and completed one retained resume, so
process continuity did not establish visual continuity.

The software trace separates the real wake from later test cleanup: retained
resume acknowledgement occurred at 44.843 seconds from Rust main, while shutdown
was at 118.394 seconds. The later ordinary return to native UI does not dismiss
the earlier user observation. No optical timing or camera record was collected.

## Cause and change

The launcher used `release_display 1` for normal sleep and quick-wake handoffs.
That restored `awesome` and `statusbar`, then explicitly invoked `xrefresh` on
the stock X11 scene. It could therefore draw the bookshelf before the stock
screensaver or the retained application frame. The bounded redraw wait could
also delay processing later wake events.

| Boundary | Updated behavior |
| --- | --- |
| Normal sleep and power handoff | Restore owned stock services without forcing an X11 repaint. Stock powerd handles its screensaver transition. |
| Display takeover | Pause the owned window-manager process before stopping statusbar, avoiding damage while the stock WM is still drawing. |
| Validated active wake | Take display ownership before the bounded portrait-authorization transaction, then resume the retained application after valid authorization. |
| Failed portrait authorization | Quietly restore stock ownership before waiting or retrying. The active-state, identity and epoch gates remain enforced. |
| Ordinary exit, TERM or fatal failure | Consume one pending stock repaint, including exit after a quiet parked handoff. |
| USB takeover | Restore owned state without painting over the USB view. |

`STOCK_REPAINT_PENDING` records the final repaint obligation across quiet power
handoffs. An untaken display does not create that obligation. Repeated handoff
does not repeatedly start or resume services, and a final repaint is consumed
only once. Existing PID/birth and ownership checks remain in place.

The application still refreshes current framebuffer information and validates
the retained mapping while input and rendering are paused. The retained frame
is fully rewritten and submitted before the resume barrier is acknowledged.
This launcher change does not add a driver flag, new ioctl or permanent
window-manager pause across the complete sleep interval.

## BiliComics reference

The user's BiliComics project was examined read-only. Its wake-continuity work
uses fresh framebuffer GET information and early window-manager suspension,
avoiding a stock Home transition caused by waiting for a winmgr mode query.
Relevant paths are `packaging/kindle/launch.sh`, `src/main.rs`,
`src/native_presentation.rs` and the backend `framebuffer/probe.rs`.

Aidoku already obtains fresh framebuffer information during paused resume, so
the compatible changes are the launcher takeover order and quieter normal
handoff. BiliComics' own sleep-side release still includes an explicit stock
redraw; its historical no-Home wake qualification does not prove that sleep
entry cannot flash. Its private protocol formats and historical physical
acceptance are not transplanted into Aidoku.

## Verification

The isolated remote `sh -n` check and all 37 launcher fixture tests passed on
`ssh test-env`. Tests observe actual fake process/service states and cover
normal/quick/repeated power events, authorization rejection and malformed
publication, existing stopped services, parked ordinary exit and TERM, fallback
redraw and USB preservation. The original source and outputs are retained.

The verified launcher is 32,458 bytes, SHA-256
`4bc477a8cc3575a0bb581700f6721cb24c3b039b06460e0cb5c4336b557d1859`.
The change is limited to the launcher and its tests; the r2 ARM executable
identities and completed host performance cohort remain unchanged.

Physical visual requalification completed in the bounded b09 manual batch.
The user explicitly confirmed that neither sleep entry nor wake showed the
native bookshelf. The same application PID 20189 retained epoch 1 and page 11,
with one retained resume and no lifecycle error. Original native and lighting
state was restored and the owner/token lease was released at 02:37:03
Asia/Shanghai on 2026-11-11, before the host-side evidence analysis.

This is the user's observation for one manual sleep/wake sequence on the
physical Scribe build 479432, supported by software lifecycle records. It is not
an optical latency, residual-image or battery measurement. The per-generation
wait record reports App exit 0 and supervisor 0; the helper's aggregate App exit
field remains null and is preserved as such.
