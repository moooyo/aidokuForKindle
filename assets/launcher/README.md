# Kindle Library cover

`aidoku-cover-v2.svg` uses the actual Aidoku logomark from
[the official website](https://aidoku.app/images/aidoku.svg), above an outlined
Aidoku title. The upstream SVG is retained byte for byte as `aidoku-official.svg`.
Its original shape, proportions, and `#FF2F52` fill are preserved in the composed
SVG. `aidoku-cover-v2.png` is an opaque 600-by-800, 8-bit grayscale rendering for
Kindle. The logomark is not a recreated or AI-generated approximation.

The website's SVG matches `static/images/aidoku.svg` in the official
`Aidoku/Website` repository at commit
`3de2b3f26b69b1d22ccb2bde88020055f5e5fa5c`.
`official-logo-provenance.json` records its URL, hash, and adaptation.
The website repository's GNU GPL version 3 license is preserved in
`AIDOKU-LOGO-LICENSE.txt`. The original logomark is credited to the Aidoku project.

The title and subtitle use outlined glyphs from the bundled Noto Sans CJK SC
Bold and Medium fonts. Their SIL Open Font License and upstream provenance are
retained in `assets/fonts/` and in both packages' font notices.

Both document-entry templates declare
`# Icon: /mnt/us/aidoku/assets/aidoku-cover-v2.png` within their first six lines.
The versioned image path refreshes the native thumbnail reference when updating
an existing Scriptlet. Install the PNG before exposing the updated document
entry to the scanner. No ebook or global hook replacement is required.

The original `aidoku-cover.svg` and `aidoku-cover.png` remain as historical
artwork from the first cover iteration. The old runtime PNG is retained for
previously indexed entry references; new document entries use v2.

To regenerate v2 on `test-env`, use Python with CairoSVG, FontTools, and Pillow:

```sh
python3 scripts/generate-launcher-cover.py
```

The generator keeps font outlines and the official vector paths editable.
The runtime PNG contains no transparency, runtime fonts, scripts, or external
resources. Library covers are independent of Reader sleep covers.
