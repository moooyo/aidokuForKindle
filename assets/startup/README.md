# Startup labels

These grayscale labels let the native backend display startup feedback before
the bundled fonts are decompressed or registered. Each file contains two
little-endian 32-bit dimensions followed by tightly packed 8-bit grayscale
pixels. The startup worker performs no image or font decoding.

`prepare.py` reads the four existing localization catalogs and rasterizes only
the startup labels using the bundled Noto Sans font. Generate the assets through
`ssh test-env` with `python3 -I assets/startup/prepare.py`; do not run it on the
Windows workstation. The generated manifest records the exact source font,
labels, generator, and payload hashes. Existing font license notices remain in
`assets/fonts`.

The display worker uses real initialization phases and a bounded 0.8 Hz dot
indicator. It stops before the event loop acquires the native framebuffer and
never runs during retained sleep or resume.
