# Third-party notices and provenance

The application source is distributed under GPL-3.0-or-later, with the full GPLv3
text in `LICENSE`. Third-party code and data retain the independent terms below.
The application license does not relicense third-party fonts, dictionaries,
firmware, manga content, or trademarks.

## Kindle Slint backend

- Upstream: [KindleTweaks/slint-kindle-backend-patch](https://github.com/KindleTweaks/slint-kindle-backend-patch).
- Upstream revision: `03aa48d3531e9eae7b360bdc24e90a72e9f307a4`.
- Package: `slint-backend-kindle` 0.7.1.
- Copyright: 2026 Sverre Johann Bjørke, as written in the retained upstream text.
- Terms: MIT OR Apache-2.0; complete texts are retained in
  `vendor/slint-backend-kindle/LICENSE-MIT` and `LICENSE-APACHE`.
- Adoption: copied from the user's sibling WeRead checkout with its existing
  patches. `UPSTREAM_REVISION`, `PATCHES.md`, and `AIDOKU_PROVENANCE.md` retain
  upstream identity and the two projects' adoption history.

Statements about historical verification in a retained upstream/WeRead file do
not establish verification of this application. Aidoku has its own native
adapter, launcher, package paths, and acceptance evidence.

## Aidoku Rust source ABI and host contract

- Upstream: [Aidoku/aidoku-rs](https://github.com/Aidoku/aidoku-rs).
- Copyright: 2022-2025 Skittyblock.
- License: MIT, retained verbatim in `src/sources/aidoku-rs.LICENSE`.
- Adapted material: Postcard wire-model definitions and the open Rust source API/
  test-runner host contract used by the independent Wasmi host.
- Local work: the portable interpreter integration, persistence, limits, and
  application/provider adapters in `src/sources/`.

The separately licensed Swift [AidokuRunner](https://github.com/Aidoku/AidokuRunner)
is not copied, vendored, or linked. Installing an independently supplied `.aix`
package does not grant rights to that package's source code or hosted content;
source-package authors retain their own licenses.

## Offline Pinyin data

- Upstream: [Android Open Source Project PinyinIME](https://android.googlesource.com/platform/packages/inputmethods/PinyinIME/).
- Revision: `49aebad1c1cfbbcaa9288ffed5161e79e57c3679`.
- Copyright: 2009, The Android Open Source Project.
- License: Apache License 2.0; the complete upstream notice and license are in
  `assets/pinyin/LICENSE-AOSP.txt`.
- Source data: `jni/data/rawdict_utf16_65105_freq.txt`.
- Transformation: lowercase toneless Pinyin/phrase/frequency-rank TSV, described
  in `assets/pinyin/README.md` and reproduced by `generate_dictionary.py`.

The 65,105-entry dictionary and the offline candidate-engine approach are shared
with the sibling WeRead project. Android's decoder, JNI implementation, and
binary dictionary are not included.

## Fonts

| Family | Upstream and copyright | License text |
| --- | --- | --- |
| Noto Sans CJK SC | [Noto CJK](https://github.com/notofonts/noto-cjk), Copyright 2014-2021 Adobe | `assets/fonts/NotoSansCJK-LICENSE.txt` |
| Noto Serif CJK SC | [Noto CJK](https://github.com/notofonts/noto-cjk), Copyright 2017-2024 Adobe | `assets/fonts/NotoSerifCJK-LICENSE.txt` |
| Literata | [Literata](https://github.com/googlefonts/literata), Copyright 2017 The Literata Project Authors | `assets/fonts/Literata-OFL.txt` |

All three font families use SIL Open Font License 1.1. The repository bundles
subsets/instances and deterministic gzip data, retaining embedded copyright,
family, and license records. `assets/fonts/README.md`, `manifest.json`, and
`prepare.py` document sources, weights, preparation, sizes, and hashes. The Sans
preparation input was the already licensed sibling WeRead subset. Noto is a
trademark of Google Inc.; the project does not claim ownership of font names.

## Icons and demo artwork

The interface SVG icons are original drawings made for this implementation from
the familiar symbols in the supplied design. They are project assets governed by
the application's license; no third-party icon pack is embedded. Their geometry
and usage are described in `assets/icons/README.md`.

Demo covers and illustrative comic pages are extracted/reconstructed from the
user-supplied Aidoku for Kindle UI/UX PDF. `assets/demo/README.md` and
`manifest.json` record that provenance. They are fixtures, not downloaded manga,
and do not establish licensing or availability of any real provider's catalog.
The original PDF and third-party manga content are not distributed by this
repository as part of the application source.

## Rust dependencies and tools

Cargo dependencies retain their upstream licenses and copyright notices. Slint
1.17.1 is used under its GPLv3 option for this GPL-3.0-or-later application.
Wasmi uses its MIT/Apache-2.0 terms. Consult the exact versions in `Cargo.lock`
and their accompanying crate license files for the complete dependency graph.

Compiler runtimes, Zig, cargo-zigbuild, Xvfb, Pillow, QEMU, and the firmware
emulator are build/verification tools rather than bundled application code.
Kindle firmware images and Amazon/Aidoku/WeRead trademarks are not granted an
application-source license by this notice. No Kindle firmware is included in the
development ZIP.
