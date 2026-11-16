# kindle-eink256 and Aidoku grayscale paths

Date: 2026-11-16, Asia/Shanghai.

The user's clarified goal is spatial grayscale simulation. A subsequent
[same-chart prototype and Scribe test](SPATIAL_GRAYSCALE_RESEARCH_20261116.md)
demonstrates that Aidoku can represent all 256 target tones using pixel
distribution, with practical Bayer8 performance. The Android comparison below
is source research; the later physical tests use our own prototypes.

The linked project adds software dithering to the Android Kindle application.
Its "256" describes simulated gray transitions from an eight-bit image using
a sixteen-level output palette. It does not establish 256 independently
addressable optical states in a Kindle panel.

## Inspected sources

- [kindle-eink256 README at 8b28558](https://github.com/zanjie1999/kindle-eink256/blob/8b28558a20b8088d459711193b442e8862d12f76/README.md).
- [Kindle bitmap admission and JNI wrapper](https://github.com/zanjie1999/kindle-eink256/blob/8b28558a20b8088d459711193b442e8862d12f76/kindle_decoded/smali_classes7/com/zyyme/eink256/Eink256Patch.smali).
- [Author-linked native algorithm at 194c4d5](https://github.com/zanjie1999/eink256/blob/194c4d559aca3e074e4a9b985a02c5b751289665/app/src/main/cpp/eink256.cpp).
- [Generic Android/Xposed integration](https://github.com/zanjie1999/eink256/blob/194c4d559aca3e074e4a9b985a02c5b751289665/app/src/main/java/com/zyyme/eink256/Eink256.java).

The Kindle fork is a modified APK based on Android Kindle 8.51.1.0. It is
distinct from the generic Xposed module linked by its author. Neither is an
executable for the original Linux Kindle environment used by Aidoku.

## Processing mechanism

The published native algorithm converts bitmap pixels to grayscale, rounds
them to a palette with step 17, and propagates the rounding error to adjacent
pixels using Floyd-Steinberg weights: right 7/16, lower-left 3/16, below 5/16,
and lower-right 1/16. It uses two error rows and writes the result back to the
bitmap. The logical output palette remains 0, 17, 34, through 255.

The Kindle wrapper checks bitmap validity and a 64-by-64 minimum. It loads
`libzyymeEink256.so`, obtains writable software bitmap storage where needed,
and calls `ditherBitmap`. Its in-place entry requires mutable storage. Here
"native" means CPU code called through JNI, not a JPEG engine or display-driver
waveform operation. The shipped library exports that JNI entry and the two
pixel-template functions and imports Android bitmap access functions.

Inspected Kindle call sites include `BitmapHelper.createScaledBitmap`,
`AndroidRasterImageProvider.createBitmap/updateBitmap`, and
`BitmapMobiRenderElement.render`. They process image/page bitmaps; this does
not establish global dithering of every Android widget. The generic upstream
module separately hooks decoder APIs. The exact timing relative to any later
view scaling depends on the caller.

## Comparison with this project

| Path | Processing location | Dithering and output |
| --- | --- | --- |
| kindle-eink256 | Android image/page bitmap hooks | CPU Floyd-Steinberg; sixteen-level palette |
| Aidoku Reader software | After image fitting and contrast processing | Four-by-four Bayer tone tables; sixteen-level palette |
| Aidoku generic image renderer | After Lanczos3 fitting | Serpentine Floyd-Steinberg with fixed-point error accumulation; sixteen-level palette |
| Aidoku hardware Y4 | Final native display processing | Retains Gray8 input and requests MediaTek Y4; tested post-MDP output has sixteen encoded values |
| Aidoku Fast preset | Reader presentation and refresh policy | Persistent black-and-white frame with DU page updates; no delayed or periodic automatic clearing |

Aidoku already has the same broad idea of spatially representing intermediate
gray values. The algorithm and placement differ. Its generic error-diffusion
routine is not the Bayer routine used by the current Reader. Neither routine
should be described as identical to hardware Y4 without a matching test.

JPEG hardware decoding and IMGRZ scaling are independent of these grayscale
choices. Turning JPEG acceleration on does not add panel gray levels.

## Interpretation and limits

An eight-bit input, sixteen per-pixel output codes and the number of perceptually
distinguishable regional gray tones are different measurements. The project's
name is not a calibrated optical measurement. We have not run its Android APK
or made a controlled physical quality/performance comparison with Aidoku, so
this audit does not rank their visual quality or claim identical results.

The settings [256-swatch test](review/grayscale-256-20261115/README.md) draws
plain UI rectangles and does not use the Reader's Y4 processing. Seeing sixteen
bands there is not a comparison with a dithered book bitmap in the Android app.
Matching comparisons must use the same image, final dimensions, grayscale and
alpha policy, then compare the actual processed output under the same display
conditions. The existing [Y4 evidence](HARDWARE_Y4_DITHERING.md) retains its
digital-versus-optical distinction.

Only source inspection and static library inspection were performed. No APK,
external native library or third-party implementation was executed or added
to the Aidoku runtime.
