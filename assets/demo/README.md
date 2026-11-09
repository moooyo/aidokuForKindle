# Demo artwork provenance

The cover art is extracted from the user-supplied Aidoku for Kindle UI/UX design PDF.
It is a deterministic demo fixture, not content downloaded from a manga provider.
The original PDF is intentionally kept outside the repository. Its SHA-256 and the
source crop coordinates are recorded in `manifest.json`.

`cover-00.png` through `cover-17.png` follow the first eighteen library items in the
design. They are assigned only to their corresponding titles. Additional library
titles and generated volume editions without dedicated art use an empty cover URI
and the application's title plate, rather than artwork printed with another title.
External captions, application navigation, and unread-count overlays are excluded.
Where a badge obscured the artwork, its original line pattern or title strip was
reconstructed with the bundled Noto Serif Black font. The titles printed inside the
covers are part of the artwork, rather than application labels.

Three `library-cover-pw-*.png` variants keep the printed star, silver, and lighthouse
titles clear of the wider Paperwhite crop and unread badge area. Catalog and history
editions are extracted separately so the artwork and the title printed on it match
their corresponding reference screens. Their extraction script and crop provenance
are provided in `scripts/extract-context-covers.py` and `context-covers.json`.
Context projection applies only while a book still uses bundled art; a user-selected
cover is retained in every view.

`search-cover-00.png` through `search-cover-02.png` reuse the supplied screen-tone
art-only crops at the search card's wider aspect ratio, retaining the complete
internal titles for the three additional search editions. The grouped
search query, provider results, and timeout state are declared in `catalog.json`.
Other queries filter the actual fixture titles and authors instead of returning
the fixed design-query results.

The catalog API returns 128 entries: the device-specific design entries appear
first, followed by stable volume editions derived from the forty-six base titles.
The history fixture contains twenty real progress records; its first five match
the design, and the remaining records use earlier dates. These counts give the
actual twenty-six catalog pages of five items, thirteen catalog pages of ten items,
and four history pages.

Fresh library launches select a separate snapshot with canonical, descending
reading timestamps and validated local downloads. Other initial screens and the
legacy `seed` API retain the history snapshot. Both use the same initialization
marker, so changing a launch screen never resets an existing demo session. See
`../../docs/DEMO_SCENARIOS.md` for the snapshot contract and its limits.

Six chapter-update records are seeded as actual chapters dated on the scenario day.
They have local one-page artwork and no synthetic reading progress. Their update
count is separate from the cover badges and updated-book summary. Catalog rows with
an update age expose a demo-only projected timestamp; unmarked entries expose zero
instead of an invented recent date.

`repository.json` is an embedded demo-only source index with 124 entries. Its first
two entries offer versions above the installed MangaDex and source-A fixtures; the
other 122 use stable synthetic identities. All package URLs use example.invalid.
The demo installation flow changes local fixture configuration only and does not
fetch those URLs or claim that a real Aidoku package has run. Production repository
installation retains its separate network and package-validation path.

`page-00.png` and `page-01.png` are complete standalone illustrative comic pages.
They retain the observed art-only clips from the reader mockups and extend the
hidden regions with matching screen tones, city silhouettes, speech balloons,
crosshatching, and radial lines. The city baseline is aligned to Paperwhite y=284
and Scribe landscape y=330; source crop bounds and alignment reasons are recorded
in `manifest.json`. Application separator rules and the spread gutter marker are
excluded. These images do not
contain baked-in status bars, toolbars, page counts, sliders, or navigation controls.

The front page keeps the clean PDF art clip at `[0, 179, 1172, 1269)`
pixel-for-pixel. Its former first crop row at y=178 contained the PDF application's
header separator and is replaced with continued artwork. The upper horizontal
rules and rectangular balloon retain their observed phase and edges. The lower
continuation follows the observed eight-degree ray period, circle, crosshatch
phase and line width, and straight silhouette sides. All front-panel backgrounds
remain white 255, without a tonal step at either continuation boundary.

The back page keeps the PDF art clip at `[0, 92, 1172, 1304)` pixel-for-pixel.
Its hidden top and bottom continuations follow the observed ray edges, dot lattice,
balloon curves, and stripe phase. They replace an earlier reconstruction whose
misaligned fragments duplicated parts of the balloons at y=92 and y=1304. The
upper dot field retains its original gray-221 background, including the hidden
continuation, while the speech balloons retain their white background. The
correction retains the 1172x1648 page size and its outer panel rules. The predecessor,
corrected image, and preserved clip hashes are recorded in `manifest.json`.

All PNG images use grayscale. Cover images and comic pages are quantized to sixteen
gray levels; the comic motifs use black, white, and a #555 screen-tone background.
These assets do not establish licensing
or availability of any real third-party manga content.

Regenerate the assets using Python with Pillow and pypdf installed:

```powershell
python scripts/extract-design-assets.py --pdf '<path-to-the-user-supplied-design.pdf>'
```

To reproduce either page correction from its canonical extracted PNG, preserving
all other assets and its clean observed clip, use the corresponding command:

```powershell
python scripts/extract-design-assets.py --repair-front-only --output-dir assets/demo
python scripts/extract-design-assets.py --repair-back-only --output-dir assets/demo
```

The corrected PNG was written with Pillow 11.1.0. Full PDF extraction can produce
different antialiasing samples with another PDF/JPEG decoder; the repair-only path
uses the canonical PNG so those decoded samples are retained.
