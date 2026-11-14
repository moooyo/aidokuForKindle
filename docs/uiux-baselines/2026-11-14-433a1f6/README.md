# Kindle UI/UX baseline

Source: `433a1f6ba6478bc82abe21163aeff345d4acd621`.

Open `index.html` to compare 31 captured states for Kindle Scribe and Paperwhite 5.
The viewer supports scene selection, proportional or equal-width comparison,
optional illustrative device frames, and original-image inspection at 100%.

All 62 PNGs in `raw/` are unchanged outputs of the actual desktop application,
executed on `test-env` with the built-in offline demo fixture. Portrait geometry
is 930 x 1240 for Scribe and 618 x 824 for Paperwhite 5. Landscape swaps the axes.
The desktop backend enforces a scale factor of one. Native scanout, physical
panel response, ghosting, illumination, and touch hardware were not captured.
The status bar contains simulator values. No physical Kindle was accessed.

`comparisons/` embeds original screenshots at exactly one image pixel per output
pixel. The external device frames are illustrative, not measured hardware CAD.
`contact-sheets/` contains downsampled overview images. Original PNGs, not contact
sheets, are the reference for subsequent changes. `replay/` contains the actual
timed pointer and application actions. Every PNG retains application-generated
state and geometry sidecars. `manifest.json` binds paths, dimensions and hashes.

`evidence/verification.json` records source identity, capture checks and pixel
preservation. The initial harness used `reader_menu` instead of the actual `menu`
state field; saved state was rechecked with the correct field without modifying
image or state files. The bookmark replay was rerun after advancing to page 13,
because the initial fixture already contained a page 12 bookmark. Initial
diagnostics remain preserved in the task's isolated worker workspace.

All capture, image production and verification ran on `test-env`. The Windows
workstation was used only for editing, file transfers and viewing the results.
The production application and repository sources were not modified.

## Repeat the capture

Use the matching desktop executable and run `tools/capture-baseline.py` through
`ssh test-env`, supplying `--binary` and a new `--output-dir`. The helper imports
the adjacent project `verify-ui.py`. Each scene uses a fresh offline demo data
directory. Keep Cargo concurrency at six if a new executable must be built.
