# Cold local cover experiment

The standalone binary was not shipped. It passed independent cold confirmation
but exceeded the 50 ms first-frame regression limit on the warm workload.
The separately built and tested [deployed combination](../README.md) includes
this decode change and a bounded first-Library status observation.

This is a candidate against the installed `78c5b2b` tree. It addresses local
images that have no matching on-disk cover derivative. It does not change read
admission for existing derivatives, network fetches, Library rendering, the
native launcher, or the user's data layout.

On a local miss, the control uses `CacheManager::validate_path` to decode the
source before reading it and decoding it again for the cover. For a standard
JPEG, the same bounded Rust image decoder performs both operations. For
eligible static PNG input, the candidate verifies all chunk boundaries and
CRCs including IEND before using the single rendition as the pixel validation.
The renderer still checks geometry and allocation limits, yields to the shared
decoder permit and consumes every row and final chunks when streaming large
noninterlaced PNGs. The scaling, quantization and processed-file validation
are unchanged. The source content is freshly hashed and its canonical path,
file identity and original metadata are rechecked after the decode, before
admitting the validation proof or publishing the derivative.

Managed sources, unsupported formats, animated PNGs, streams with trailing
data and PNGs with damaged ancillary chunks take the old validation path.
The compressed input buffer is dropped before full legacy validation. An
invalid critical CRC or missing/invalid IEND is rejected; this is stricter
than the old ordinary PNG decoder, which may stop at the last pixel. JPEG
retains the original decoder's acceptance policy. No partial image is stored.

The [source patch](candidate.patch) is the independent trial, not a second
package or an implicit deployment. Host and ARM builds ran only on `test-env`
with six Cargo jobs and private overlay build directories. The frozen ARM
`candidate-arm` is 53,377,488 bytes, SHA-256
`d9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6`.
The optimized library suite passed 822 tests with six ignored (five existing
tests and one opt-in six-image benchmark). New tests cover damaged critical
PNG tails, an optional damaged metadata chunk and changed source identity.
The SHA-256 of the host test binary is
`c4a774bfa9264a4f6bc31ae51ab4d6ae3fcd159af0d9d56b3e79a25523252b21`.

The baseline device arm is the deployed binary, SHA-256
`62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612`.
The candidate uses its normal default without test arguments. The trace events
`cover_cold_validation_combined_started`, `cover_cold_decode_validated` and
`cover_cold_validation_fallback` distinguish the actual branch taken. Cold
device trials must retain every attempted pair; only reset disposable fixture
data, never a user's cache. The test condition is six locally backed cold
cover jobs and seven complete image slots. Desktop image preparation and
Kindle file-URI-to-driver times measure different intervals.

The [synthetic generator](tools/make_fixture.py) makes six redistributable
inputs: ordinary grayscale, RGB and alpha PNGs, baseline and progressive JPEGs,
and a noninterlaced tall streaming PNG. The [manifest](tools/synthetic-fixture.json)
identifies source bytes and decoded pixels; no user book or metadata was used.
