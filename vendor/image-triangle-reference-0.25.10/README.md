# Reader Triangle provenance

`src/imaging/reader_raster.rs` adapts the single-channel Triangle resize and
ordered Bayer tone table from BiliComics commit
`0e2b816b13aef5b0fe5cad87be4e85c0d75e088d`:

- `src/reader/triangle.rs`: Triangle coordinates, clipping, normalized f32
  coefficients, vertical-first accumulation, bounded horizontal plans, and the
  exact IEEE-754 conversion to eight-bit grayscale.
- `src/reader.rs`: the crop-local 4x4 Bayer matrix and its seventeen tone tables.

The Triangle implementation derives from `image` version `0.25.10`, under
`MIT OR Apache-2.0`. Both complete upstream license texts are retained here.
The pinned upstream image revision is
`76e57184f22772dad1138e96954e57945406b15e`, and its crate checksum is
`85ab80394333c02fe689eaf900ab500fbd0c2213da414687ebf995a65d5a6104`.
This directory is provenance and licensing material, not a dependency
replacement or Cargo patch.

Aidoku uses an already selected `GrayImage` instead of BiliComics' source crop
view, and adapts cooperative cancellation to its existing decoder work scope.
The horizontal plan remains limited to 256 KiB of vector element capacities.
If the plan cannot fit, Aidoku recomputes one output column's coefficients at a
time while retaining one source-width f32 row; it does not allocate the full
f32 fallback matrix. Neither fallback changes sample accumulation order or
introduces intermediate rounding.

The caller still owns source validation, immutable content identity, target
geometry, contrast, frame retention, and UI publication. Tests compare output
bytes with the pinned `image::imageops::resize(..., Triangle)` dependency and
with BiliComics' direct Bayer arithmetic. Host and physical-device measurements
remain separate from these numerical contracts.
