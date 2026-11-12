# Kindle grayscale feasibility research

Reviewed: 2026-11-13, Asia/Shanghai.

## Finding

The application can preserve and submit all 256 Gray8 input values. This does
not establish 256 independently addressable optical gray levels on the panel.
The reviewed Kindle display pipeline converts Y8 input to Y4 before waveform
processing. No usable 256-level waveform or bypass of that conversion was found
in the reviewed public ABI and implementations.

This finding concerns the recorded Scribe firmware and the inspected source
paths. It is not a claim that every Kindle model, panel, or future firmware has
the same capabilities. Spatial dithering can represent smoother average tones
using neighboring pixels; those averages are distinct from additional stable
gray levels at an individual pixel.

## Application evidence

The application was inspected at `5fbbfa441cec43b5160ec0b8944bbac6508fa6b5`.
BiliComics was inspected at committed
`9f6b6884f9cc9a080318cff9b3d5e5f2a7f1d632`.

| Stage | Source and behavior |
| --- | --- |
| Source decode | `src/imaging/grayscale.rs:6-51` retains Gray8 data, including native Luma8 input. This is not the sixteen-level reduction. |
| Reader raster | `src/imaging/reader_raster.rs:300-346` maps values to `0, 17, ..., 255`. Disabling dithering selects the zero-bias table; it still produces sixteen values. |
| Other image raster paths | `src/imaging.rs:1012-1052` also quantizes to sixteen values. The spread compositor additionally calls this quantizer at line 523. |
| BiliComics reference | Its committed `src/reader.rs:1299-1315` has the same sixteen-level Bayer tone-table construction. It is not a 256-level reference implementation. |
| Scene and scanout | `vendor/slint-backend-kindle/src/gray_target.rs:52-83`, `render_control.rs:60-73`, and `framebuffer/mod.rs:385-401` retain byte precision. Optional binary mode and inversion are separate output transforms. |
| Refresh requests | `framebuffer/ffi.rs:199-206` exposes GC16, DU, A2, and AUTO. The current MTK submission leaves dither and quantization fields at their default values. |

## Amazon kernel evidence

The previously recorded physical firmware build ends in `479432`, with Linux
4.9.77. Its corresponding public
[Amazon source archive](https://s3.amazonaws.com/kindledownloads/Kindle_src_5.19.5_4794320058.tar.gz)
was read on the designated worker. Only selected files from its nested
`gplrelease/linux-4.9.tar.gz` were retained.

| Nested source path | Observation |
| --- | --- |
| `include/linux/hwtcon_ioctl_cmd.h:55-83` | Byte-oriented grayscale formats and waveform choices are separate ABI concepts. The exposed waveform enumeration includes GC16 and GL16 families. |
| `drivers/misc/mediatek/hwtcon_v2/hwtcon_mdp.c:140-141,206-217` | The normal conversion explicitly selects Y8 input and Y4 output. The selected dither algorithm is Y8-to-Y4. |
| `drivers/misc/mediatek/hwtcon/hwtcon_mdp.c:97-98,145-156` | The other included HWTCON implementation uses the same input/output precision. |
| `include/linux/hwtcon_ioctl_cmd.h:280-284` | The four-bit/five-bit waveform-type query describes advanced algorithm support. It is not a 256-gray-level capability declaration. |

The archive also contains panel-interface width and internal waveform fields.
Those fields do not establish optical precision and were not interpreted as
256-level support. Matching a source-package build number does not independently
prove the identity of every loaded binary or measure panel output.

Selected extracted-file SHA-256 values:

```text
include/linux/hwtcon_ioctl_cmd.h
b386c601fcd1a28e04d078ce421f4a33a7ba1b29521d24a9ded73ef21a94b95d
drivers/misc/mediatek/hwtcon_v2/hwtcon_mdp.c
a8da885755caa942e34d1076de0913adf43d8ebb3966d9e55cc5b5c851a90a97
drivers/misc/mediatek/hwtcon/hwtcon_mdp.c
2d0ce2b23217ed028bf08480fbfb74cc262d8fca39caaefd55743fd824bd492c
```

## Independent implementation references

FBInk accepts Gray8 input and distinguishes that from framebuffer bit depth.
Its eight-bit pixel writer retains the supplied byte. Its software dither
quantizes to sixteen evenly spaced values.
[Input formats](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/README.md#L21-L24),
[pixel writer](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L249-L285),
[software dither](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.c#L10880-L10919).

KOReader uses the same sixteen-value dither palette and requests Y4 hardware
dithering on eligible MTK paths.
[Palette conversion](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/blitbuffer.lua#L230-L260),
[MTK request](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L669-L684).
FBInk's GC16HQ identifier belongs to its PocketBook-specific declarations; it
does not establish a Kindle 256-level mode.
[GC16HQ scope](https://github.com/NiLuJe/FBInk/blob/ded92fa510c957e0157c92ae83bd5c51af710c05/fbink.h#L435-L439).

## Polarity is separate from gray-level precision

The exact kernel also distinguishes normal Y8 from inverted Y8. In
`hwtcon_v2/hwtcon_mdp.c:143,165-169,216`, inversion starts disabled, the update's
inversion flag toggles it, and `GRAYSCALE_8BIT_INVERTED` toggles it again. The
other HWTCON implementation uses the same rule at lines 100,122-126,155. With no
explicit inversion update flag and a current inverted-Y8 mode, byte 255 is
therefore inverted before display and byte 0 becomes white. This is a source
inference, not a reflectance measurement.

An application-owned software night transform is another independent inversion.
It must not be confused with the native byte encoding. Canonical scene colors
can remain `0 = black, 255 = white`, while the framebuffer writer maps those
bytes to the actual normal or inverted scanout format exactly once. That
reversible mapping retains all byte values; it does not add optical gray levels.
The actual same-session framebuffer mode must determine the mapping, and a
changed layout must still pass the existing ownership and full-frame gates.

KOReader explicitly selects framebuffer grayscale 2/1 for hardware night mode
and avoids stacking an additional software inversion on that hardware request.
[Linux framebuffer mode](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_linux.lua#L158-L199),
[inversion handling](https://github.com/koreader/koreader-base/blob/b153a4a252f6e4295af7a569bcfdd59ffa688fa6/ffi/framebuffer_mxcfb.lua#L433-L438).

## Feasible follow-up

An optional unquantized Gray8 path is technically feasible. It would need a new
render/cache identity, explicit ownership of software versus hardware
dithering, and consistent treatment of spreads, covers, overlays, inversion,
and partial updates. Existing Gray8 buffers already use one byte per pixel, so
retaining additional input values would not inherently increase their RAM
allocation. Encoded cache size could increase. Removing a CPU quantization pass
might save work, but no speedup is measured or promised.

Gray8 passthrough alone would still encounter the reviewed Y4 conversion. It
could change banding or noise rather than improve it. Hardware dithering is a
separate possible optimization requiring exact-device qualification; it does
not create 256 independent optical levels. A genuine 256-level mode would need
a documented compatible controller, waveform, and panel path absent from this
reviewed API.

Any later quality experiment should compare identical ramps and comic pages,
including solid patches, fine line art, halftones, menu overlays, and both
inversion modes. Framebuffer screenshots can verify the submitted values but
cannot establish how many optical levels the panel distinguishes. That question
requires controlled physical observation or reflectance measurements.

## Method and limits

This was static source research. Public upstream sources and Amazon archives
were fetched or read through `ssh test-env`; local project files were read only.
The configured web search/open gateway was unavailable, so direct public-source
retrieval on the worker was used. No build, test, benchmark, runtime probe,
device command, display setting change, or production grayscale change was
performed.
