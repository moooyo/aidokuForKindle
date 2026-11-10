# Aidoku adoption provenance

This directory is adopted from the operator's sibling WeRead project's
`vendor/slint-backend-kindle` on 2026-11-04 (Asia/Shanghai). Its upstream identity
is retained verbatim in `UPSTREAM_REVISION`, and its prior modifications and
their attribution are retained in `PATCHES.md`.

The library source and original `LICENSE-MIT` and `LICENSE-APACHE` files are
preserved. The Aidoku adoption initially changes no library source. Historical
verification statements in `PATCHES.md` describe the sibling project and do
not establish that Aidoku or this checkout has passed those checks.

Aidoku owns its own platform adapter, entry point, application state, launcher,
package paths, and acceptance evidence. It does not depend on the WeRead app.

## Aidoku input observation extension

The subsequent Aidoku change adds `KindleBackend::set_input_observer` and
wires it through the platform to native touch dispatch. This callback runs on
the UI thread after rotation/scale conversion and before Slint dispatches to
child controls. Returning `true` consumes the event. Direct public Slint
window dispatch, including cancellation with `PointerExited`, bypasses this
native hook and cannot recursively invoke it.

The change preserves upstream touch-device selection, exclusive evdev grab,
slot ownership, coordinate conversion, and framebuffer behavior. Its purpose
is to let the same application pagination observer handle native, desktop,
and scripted input before a child cover/button can grab a horizontal swipe.
`PATCHES.md` documents the changed files and verification boundaries.

## Stock powerd lifecycle extension

The later Aidoku lifecycle change adds a requested/applied ownership handle,
UI-thread control callback, signal-pipe wakeup descriptor, and a parked loop.
The native loop releases its evdev grab before publishing applied pause,
suppresses framebuffer writes and input/timer polling while parked, then drains
old events and retakes input on a validated application resume. It rewrites the
entire retained canonical RGB frame before requesting GC16. Font resources and
Slint image objects remain owned by the original process.

This is distinct from the original optional RTC/sysfs `WakeSchedule` mechanism.
The application launcher cooperates with stock powerd and does not use this
extension to claim USB, issue a kernel suspend, or change system wake schedules.

The subsequent retained-scene refinement keeps Slint's reused RGB scene across
stock display ownership. It applies actual scene dirtiness before rewriting all
physical pixels, instead of treating a replaced physical framebuffer as a new
software scene. Rotation still invalidates the software buffer. Refresh
submission errors propagate through the event loop, and a failed full update
cannot advance the resume counter. This confirms submission acceptance only,
not physical waveform completion. Upstream licenses and earlier attribution are
unchanged.

## Evaluated grayscale target and submission identities

The next Aidoku refinement uses the public Slint target-pixel extension to
retain one neutral channel for actual 8-bpp framebuffer mappings. An observed
colored input forces a complete RGB rebuild and permanent RGB fallback before
any candidate pixels reach scanout. Other framebuffer formats keep RGB. The
canonical scene remains independent of output inversion, partial updates,
render rotation and retained wake; input Image buffers remain RGB8/RGBA8.

Framebuffer update submissions now use distinct nonzero markers within their
owned session. ABI fallback shares the same logical marker, failed SENDs do
not replace the last accepted target, and interrupted waits retry that target.
No additional driver-parameter or MDP completion operation is enabled.
`PATCHES.md` records the affected modules and the host oracle scope. The separate
[Aidoku evaluation](../../docs/KOREADER_OPTIMIZATION_EVALUATION.md) records the
final integration checks and bounded physical Scribe smoke with its exact
executable identity; it does not claim panel timing or new MTK-flag qualification.

## Opt-in Reader frame observations

The subsequent observation extension adds a one-shot application token and an
optional public frame observer. It reports actual framebuffer writes and
successful SEND calls separately, with phase duration, frame-write sequence and
the submitted driver marker. Superseded, cancelled, unwritten and rejected
attempts cannot become successful observations. Callbacks run without retaining
the observer's mutable borrow and may cancel the current attempt safely.

The first full scene can establish startup image output. Later ownership,
rotation and output-mode rewrites remain separate from page-turn observations.
No additional repaint, wait, driver flag or MDP operation is requested. The
application's observer is installed only for opt-in timing; physical e-ink
completion still requires a separate observation. See the
[measurement contract](../../docs/READER_E2E_MEASUREMENT.md).
