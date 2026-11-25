#!/usr/bin/env python3
"""Extract missing localized interface glyphs from an official Noto Sans CJK face."""

import argparse
import gzip
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fonttools", type=Path, help="Private dependency directory for isolated Python")
    args = parser.parse_args()
    if args.fonttools:
        sys.path.insert(0, str(args.fonttools.resolve()))
    from fontTools import subset
    from fontTools.ttLib import TTFont

    assets = Path(__file__).resolve().parents[1]
    required = set()
    for locale in ("en", "ja", "zh-CN", "zh-TW"):
        labels = json.loads((assets / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        required.update(ord(character) for label in labels.values() for character in label if character.isprintable())
    coverage = []
    for path in sorted((assets / "fonts").glob("Noto*SC-*.otf.gz")):
        with gzip.open(path, "rb") as data:
            coverage.append(set(TTFont(data).getBestCmap()))
    missing = required - set.intersection(*coverage)
    # Native language names use the supplemental face directly, not a system fallback.
    missing.update(map(ord, "繁體中文日本語"))
    font = TTFont(args.source, recalcTimestamp=False)
    codepoints = missing & font.getBestCmap().keys()
    options = subset.Options()
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 8, 9, 11, 13, 14, 16, 17]
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)

    family = "Aidoku Locale Fallback"
    postscript = "AidokuLocaleFallback-Regular"
    names = {1: family, 2: "Regular", 3: postscript, 4: family, 6: postscript, 16: family, 17: "Regular"}
    for record in list(font["name"].names):
        if record.nameID in names:
            font["name"].setName(names[record.nameID], record.nameID, record.platformID, record.platEncID, record.langID)
    for index, _ in enumerate(font["CFF "].cff.fontNames):
        font["CFF "].cff.fontNames[index] = postscript
    args.output.parent.mkdir(parents=True, exist_ok=True)
    font.save(args.output)
    print(f"Added {len(codepoints)} codepoints to {args.output.name}")


if __name__ == "__main__":
    main()
