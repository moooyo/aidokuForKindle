# Kindle Library cover

`aidoku-cover.svg` is original monochrome artwork for this project: an open
manga book, panel shapes, and an outlined Aidoku wordmark. `aidoku-cover.png`
is the opaque 600-by-800, 8-bit grayscale image consumed by Kindle Scriptlets.
The image uses no external resources, transparency, gradients, or runtime fonts.
Its source is licensed under the project's GPL-3.0-or-later license.

The wordmark and subtitle use outlined glyphs from the bundled Noto Sans CJK SC
Bold and Medium fonts. Their SIL Open Font License and upstream provenance are
retained in `assets/fonts/` and in both packages' font notices.

The first six lines of both document-entry templates declare
`# Icon: /mnt/us/aidoku/assets/aidoku-cover.png`. Install the PNG before exposing
the document entry to the scanner. The stock catalog obtains this image from
Scriptlet metadata; no ebook, global hook replacement, or direct catalog edit
is needed. Existing entries require the document to be rescanned after updating
its header. This is separate from manga covers and the sleeping Reader cover.

To regenerate on `test-env`, use Python with CairoSVG, FontTools, and Pillow:

```sh
python3 scripts/generate-launcher-cover.py
```

Inspect the resulting cover and reduced Library thumbnails before adopting a
new version. The SVG contains outlined text and remains independent of host
font discovery. The generator and bundled fonts are the editable sources.
