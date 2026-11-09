# Offline Pinyin Dictionary

`dictionary.tsv` contains 65,105 normalized character readings and phrases from
the Android Open Source Project PinyinIME dictionary. It is shared with the
sibling WeRead Kindle implementation so that this application uses the same
offline input approach instead of a short application-specific candidate table.

The data contains 17,037 single-character readings and 48,068 phrase readings.
The decoder supports exact matches, prefix completion, bounded word segmentation,
partial candidate commits, and five-candidate pages. Uncommon names can be entered
by selecting their component characters. Unknown spelling remains editable and is
never silently discarded on submission.

## Source and license

- Project: [AOSP PinyinIME](https://android.googlesource.com/platform/packages/inputmethods/PinyinIME/).
- Revision: `49aebad1c1cfbbcaa9288ffed5161e79e57c3679`.
- Source: `jni/data/rawdict_utf16_65105_freq.txt`.
- Copyright: 2009, The Android Open Source Project.
- License: Apache License, Version 2.0. The complete upstream NOTICE and license
  are preserved verbatim in `LICENSE-AOSP.txt`.

The dictionary license is independent of the application license. Android's
decoder, binary dictionary, JNI implementation, and other source code are not
included.

## Format and derivation

Each UTF-8 TSV line contains `joined_toneless_pinyin`, `phrase`, and a positive
frequency rank. Lower ranks indicate more frequent source entries. Umlaut-u is
normalized to `v`. The transformation retains all source CJK entries, deduplicates
identical spelling/phrase pairs, and sorts by spelling, rank, and phrase.

`generate_dictionary.py` reproduces the data from the exact source revision:

```sh
python3 assets/pinyin/generate_dictionary.py \
  /path/to/PinyinIME/jni/data/rawdict_utf16_65105_freq.txt \
  assets/pinyin/dictionary.tsv
```

Run reproduction and decoder tests only in the designated remote verification
environment. This repository does not treat the sibling project's historical
checks as verification of its own implementation.

## Integrity

- Upstream source SHA-256: `408700f28a56091fa07f3b849a0f134fbfc71e6b2ae9b3f52973a5b076f599ff`.
- Dictionary SHA-256: `a32775ccf82e47d04feb3f01e5174ec6fcf12a15064da14cb5cd840a1c1eb422`.
