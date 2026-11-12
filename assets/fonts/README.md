# Interface font assets

These are real font outlines, compressed with deterministic gzip headers. The
application decompresses them and registers the raw OpenType/TrueType data at
startup.

| Asset | Family to request | Weight |
| --- | --- | --- |
| `StartupSansSC.otf` | `BiliComics Startup` | 400 |
| `NotoSansSC-Regular.otf.gz` | `Noto Sans CJK SC` | 400 |
| `NotoSansSC-Medium.otf.gz` | `Noto Sans CJK SC` | 500 |
| `NotoSansSC-Bold.otf.gz` | `Noto Sans CJK SC` | 700 |
| `NotoSerifSC-Regular.otf.gz` | `Noto Serif CJK SC` | 400 |
| `NotoSerifSC-Bold.otf.gz` | `Noto Serif CJK SC` | 700 |
| `NotoSerifSC-Black.otf.gz` | `Noto Serif CJK SC` | 900 |
| `Literata-Regular.ttf.gz` | `Literata` | 400 |
| `Literata-SemiBold.ttf.gz` | `Literata` | 600 |
| `Literata-Bold.ttf.gz` | `Literata` | 700 |

The Regular, Bold, and Black faces share the family `Noto Serif CJK SC`.
Request weights 400, 700, and 900 respectively. The legacy family of the Black
face is `Noto Serif CJK SC Black`.
Literata is instantiated at optical size 12 for compact screen text.

The uncompressed 22,040-byte bootstrap face is adopted without modification
from the sibling BiliComics project. It installs the backend before background
workers start. Existing four-locale grayscale startup labels remain independent
of its small glyph set. `bootstrap-provenance.json` records its exact source,
digest, original family, transformation, and retained OFL terms. Normal UI
families switch only after all nine full faces have been registered.

The regular serif asset adds 3,342,333 compressed bytes and 4,140,532 bytes of raw
font data. Startup decompresses and registers it; parser allocations, raster
caches, and temporary decompression storage are additional runtime costs.
The Bold asset adds 3,455,674 compressed bytes and 4,228,248 bytes of raw font
data. It covers 8,112 codepoints, including the complete 8,110-codepoint union of
the existing Regular and Black faces. Their outlines and assets remain intact.

## Sources and licenses

- Noto Sans: [official Noto CJK repository](https://github.com/notofonts/noto-cjk/tree/main/Sans).
  Copyright 2014-2021 Adobe. Noto is a trademark of Google Inc.
  The preparation run reuses the already licensed Simplified Chinese faces from
  the companion WeRead project, then applies this application's smaller subset.
- Noto Serif Regular and Black: [official Noto CJK repository](https://github.com/notofonts/noto-cjk/tree/main/Serif).
  Copyright 2017-2024 Adobe. Noto is a trademark of Google Inc.
- Noto Serif Bold: [official Serif 2.003 source](https://github.com/notofonts/noto-cjk/blob/9b0f1436e455d902de067a2501422e5dc71ad16b/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Bold.otf),
  pinned to commit `9b0f1436e455d902de067a2501422e5dc71ad16b`.
  It uses the same Serif 2.003 release as the existing Regular and Black faces.
  Copyright 2017-2024 Adobe; the [license at that commit](https://github.com/notofonts/noto-cjk/blob/9b0f1436e455d902de067a2501422e5dc71ad16b/Serif/LICENSE)
  is identical to the preserved `NotoSerifCJK-LICENSE.txt`.
- Literata: [official Google Fonts distribution](https://github.com/google/fonts/tree/main/ofl/literata),
  Copyright 2017 The Literata Project Authors.

All three families are distributed under SIL Open Font License 1.1. Complete
license text is retained in `NotoSansCJK-LICENSE.txt`,
`NotoSerifCJK-LICENSE.txt`, and `Literata-OFL.txt`. Copyright and license name
records are preserved inside every prepared face and in `manifest.json`.

## Coverage and preparation

The CJK subset retains GB2312 characters, all supplied design text, UI source
text present at preparation time, Latin text, punctuation, and common interface
symbols. This is a common-text subset rather than a screenshot-only glyph set.
Uncommon characters in externally supplied manga metadata may need a system font
fallback or an expanded preparation run. Do not silently substitute a thin font
for a missing glyph.

`prepare.py` requires Python 3 and fontTools. From the repository root:

```powershell
python assets/fonts/prepare.py --weread-fonts 'path/to/weread/assets/fonts'
```

Omit `--weread-fonts` to download all Noto faces from their official repository.
To add or refresh only the Bold serif asset while preserving the existing
font files, manifest records, and licenses:

```powershell
python assets/fonts/prepare.py --font NotoSerifSC-Bold.otf.gz --coverage-font assets/fonts/NotoSerifSC-Regular.otf.gz --coverage-font assets/fonts/NotoSerifSC-Black.otf.gz
```

`--font` and `--coverage-font` can be repeated. The latter adds the existing
font's codepoints to the requested character set; all outlines still come from
the selected face's official source. This preserves the existing CJK coverage
alongside the current design and UI text when adding another weight.

The supplied PDF text can be regenerated with the design asset extraction tool.
Downloaded source fonts and authoring dependencies stay under `tmp/fonts` and
are not runtime assets. The manifest records family names, weights, sizes,
copyrights, sources, and SHA-256 digests of the prepared assets.
