# Startup labels

These grayscale labels let the native backend display startup feedback before
the bundled fonts are decompressed or registered. Each file contains two
little-endian 32-bit dimensions followed by tightly packed 8-bit grayscale
pixels. The early native display worker performs no image or font decoding.

`prepare.py` reads the four existing localization catalogs and rasterizes only
the startup, exit, and failure labels using the bundled Noto Sans font. Generate the assets through
`ssh test-env` with `python3 -I assets/startup/prepare.py`; do not run it on the
Windows workstation. The generated manifest records the exact source font,
labels, generator, and payload hashes. Existing font license notices remain in
`assets/fonts`.

The early native display worker stops before the event loop acquires the native
framebuffer. A Slint startup scene then reuses the same masks as alpha coverage,
including light/dark ink and every existing locale. Its 0.8 Hz dot indicator,
exit button, and lifecycle callbacks remain responsive while separate workers
prepare fonts and local metadata. It never advances while the display is
suspended or awaiting its retained resume frame.

`startup_window_shown` identifies the loading window; `startup_scene_submitted`
requires a successful new native SEND before preparation workers start.
`application_ready` and `startup_ready` identify completed application attachment.
The historical `window_shown` marker now refers to the loading window, so compare
the latter readiness markers against an older synchronous business-window show
when assessing total initialization time.
