# User-operated spatial-dither A/B comparison

Date: 2026-11-16, Asia/Shanghai.

The user requested a direct visual comparison of Bayer8 and high-precision
Floyd-Steinberg Q8 on the physical Scribe. A separate comparison application
and seven-page book are installed under `/mnt/us/aidoku-dither-ab`.
The KUAL entry is **Dither A/B**, with its localized comparison action. A
document-script entry is also installed as `/mnt/us/documents/DitherAB.sh`.

The tool uses separate application data and resumes at its guide page. The
ordinary Aidoku executable and library database were not modified. The test
build fixes startup overwriting stored dithering preferences, so cold launches
retain the required Off setting. The normal installed application retains its
previous executable; the source fix is available for a later normal bundle.

## Comparison pages

1. Chinese instructions.
2. Bayer8 grayscale chart.
3. Floyd-Steinberg Q8 on the same chart.
4. Bayer8 synthetic continuous-shading comic illustration.
5. Floyd-Steinberg Q8 on that same illustration.
6. Bayer8 dot-pattern samples, enlarged 8x by nearest neighbor.
7. Floyd-Steinberg Q8 samples at the same positions and magnification.

The comic illustration derives from existing demo line art with an explicitly
synthetic continuous lighting field. It is not an evaluation of a real manga
page. All pages are prepared at 1860 x 2480 and quantized before packaging.
Headers identify A and B; they do not alter the compared picture region.

The test profile uses left-to-right navigation, portrait single-page viewing,
fixed physical-size images, zero contrast adjustment, no crop, no additional
software dither/Y4, and GC16 clearing on every page. The right side advances;
the left side goes back. Opening the center menu allows ordinary navigation.

These pages are precomputed to make visual switching convenient. Their page
turn speed is not an algorithm-speed benchmark. The measured CPU costs remain
in the [research report](../../SPATIAL_GRAYSCALE_RESEARCH_20261116.md).

## Validation

The first verification found that cold startup unconditionally enabled Y4,
which would confound the comparison. After the startup correction, fresh
native launches retained Off. Both A and B captures matched their prepared
pages exactly, with zero differing pixels. The final cold launch returned to
the guide. Native services and lighting were restored and the device lease
released before user operation.

- [Native verification](native-verification.json).
- [Installed entry and scope](summary.json).
- [Page/source hashes](manifest.json).
- [Guide](pages/01.png).
- [Bayer8 chart](pages/02.png) and [Floyd chart](pages/03.png).
- [Bayer8 comic](pages/04.png) and [Floyd comic](pages/05.png).
- [Book builder](../../../scripts/create-dither-ab-book.py).

No objective claim is made that the lower mean error will be equally visible
to every viewer. Normal-distance comparisons should focus on smooth shading,
dark transitions, regular grid texture and error-diffusion grain.
