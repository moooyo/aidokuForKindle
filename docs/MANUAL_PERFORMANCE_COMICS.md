# Manual performance comics

Prepared for the installed Kindle application on 2026-11-12, Asia/Shanghai.
This suite contains six CBZ books and 66 source images. A long source strip
contains several viewport-sized reading steps; source-image count is not the
number of physical page turns.

| Book prefix | Source images | Geometry and encoding | Suggested exercise |
| --- | --- | --- | --- |
| Perf01 Distinct PNG | 12 | 1700 x 2360, grayscale PNG | Sequential turns, rapid next/back input, and stale-frame detection using the large page numbers. |
| Perf02 Detailed JPEG | 24 | 1700 x 2360, RGB JPEG | JPEG decode, grayscale processing, longer reading, and returning to earlier pages after cache pressure. |
| Perf03 Vertical PNG | 6 | 618 x 7690, grayscale PNG | Select Vertical reading direction; follow the strip/segment numbers through successive windows. |
| Perf04 Mixed Spreads | 13 | Portrait cover/pages plus 3400 x 2360 RGB JPEG spreads | Try single-page layout with wide-page splitting, then landscape spreads; inspect L/R labels and the odd final source page. |
| Perf05 High Resolution PNG | 8 | 2480 x 3508, detailed grayscale PNG | Large-image opening, processing, sequential preparation, and reopening previously viewed pages. |
| Perf06 Pepper & Carrot CN - Episode 01 | 3 | 2481 x 3503, original RGB JPEG | Read an actual Chinese story while checking detailed color-to-grayscale rendering. |

The five generated books use original geometric panels, distinct page content,
large page numbers and machine-readable black/white identity markers. They do
not reuse the same two image objects across a long nominal chapter. Their
grayscale PNGs are eight-bit and non-interlaced; every CBZ uses ZIP_STORED and
remains below 32 MiB.

## BiliComics reference

The reference commit is `0e2b816b13aef5b0fe5cad87be4e85c0d75e088d`.
Its `scripts/verification/physical/distinct_reader_fixture.py` uses 12 distinct
1700 x 2360 pages with large numerals and black/white identity marks. Its demo
manifest also records a 618 x 7690 strip. This suite adopts those workload
ideas and two dimensions without copying its database or artwork. The larger
PNG and mixed-spread cases are additional manual workloads.

## Open comic attribution

The three story images in Perf06 are the unmodified Chinese edition of
Pepper&Carrot, Episode 1, The Potion of Flight. Art and scenario are by David
Revoy; the Chinese translation is by Ran Zhuang. The
[official source page](https://www.peppercarrot.com/cn/webcomic-sources/ep01_Potion-of-Flight__files.html)
licenses the artwork under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
Full author, translation and Hereva credits, original image URLs and the license
are retained in `ComicInfo.xml` and `ATTRIBUTION.txt`. The separate cover,
decorative header and narrow credits footer are not inserted as story pages.

## Try the suite

The suite belongs only to Aidoku's own library. The source CBZ files and
imported page images remain in Aidoku's private data directories; no test book
or raw image is added to the native Kindle library or its documents directory.
Aidoku presents six books, with the 66 source images contained as their pages.

Open the application from the existing bookstore/KPM entry. Imported books have
the prefixes Perf01 through Perf06. They are also available under Browse's
Local files source. Importing uses the ordinary application adapter; no
synthetic library database is installed.

Keep image-processing and refresh preferences consistent while comparing the
same case. Begin with Perf01, read several pages normally, turn rapidly, then
return to earlier pages and check the large numbers. Continue with Perf02 and
Perf05 for detailed and large images. Use Vertical direction for Perf03 and
wide-page splitting or landscape layout for Perf04. Perf06 provides actual
story artwork for a final visual and loading check.

Compare a first visit with revisiting the same pages and with reopening the
application. Neighboring pages can already have been prefetched, and the OS
file cache is not flushed. These are qualitative manual comparisons, not
strict cold-cache benchmarks. Do not clear the user's shared cache to prepare
this suite.

Each CBZ imports as one book with one chapter. This offline suite exercises
loading, validation, image processing, local read-ahead, cache reuse and page
navigation. It does not measure HTTP download throughput, authenticated-source
behavior, cross-chapter prefetch, battery use or optical panel latency. The
618 x 7690 strips do not claim coverage of the separate over-16-megapixel
streaming-PNG path.

## Reproduce through the remote worker

Use a fresh output directory and the repository's existing Pillow environment:

```sh
python3 scripts/prepare-performance-comics.py --output-dir /tmp/manual-comics/out
python3 scripts/prepare-open-comic-sample.py --root /tmp/manual-comics
```

Run these commands only through `ssh test-env`. The first script checks every
image's complete decode, dimensions, encoding, unique identity, marker pixels
and ZIP CRC. The second pins the official image bytes, fully decodes them and
preserves attribution. Both retain earlier outputs rather than overwriting
them. A failed earlier generation must be kept separate from the final suite.

Only the six CBZ files and non-image manifest/readme files belong in the import
directory. A loose preview image or image subdirectory would be interpreted
as another local comic. The `imports` directory is not automatically scanned;
the prepared device transfer explicitly uses the existing `--import` entry.
The supplied ZIP books also fit the pairing page's individual upload limit.

Final per-book hashes, byte sizes, page records and deployment results are
recorded separately; generation and validation do not constitute a performance
result.

## Delivery result

The six books were added only to Aidoku's real library through the existing
application import adapter. The exact-title, chapter, page-count and canonical
content-path checks passed for all six books and 66 source images. Existing
books, chapters, pages, reading progress, bookmarks, preferences and sources
were preserved, except for the normal navigation-session update. Consistent
SQLite and sidecar backups remain private on the device.

The original CBZs remain under Aidoku's private
`data/imports/Performance-Samples-20261112` directory. Imported page images are
managed under `data/local/content`. The temporary import home and its data
symlink were removed, the owned application exited, native state was restored,
and the shared lease was released with owner/token checks. No Kindle system
documents/catalogue, scanner, KPM installation or program files were modified.
See the [delivery receipt](review/manual-performance-comics-20261112.json).

The final CBZ total is 69,231,514 bytes (66.024 MiB). The local download copy is
under `dist/performance-comics-20261112`; its returned tar copy matched remotely.
The six books were also imported through the desktop application on `test-env`
before the physical-device batch. The first host harness used an incorrect
standalone chapter-count column and was stopped and cleaned up; the corrected
run read the actual JSON field and passed. Synthetic attempts one and two
remain preserved separately; only the final third generation was deployed.
