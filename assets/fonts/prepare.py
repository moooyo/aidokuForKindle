"""Prepare small, licensed font assets for the Kindle interface.

This is an asset-authoring utility, not an application verification command.
It requires fontTools and uses only official upstream font sources. Noto Sans
faces may also be reused from an existing WeRead font bundle.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import urllib.request

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont


NOTO_BASE = "https://raw.githubusercontent.com/notofonts/noto-cjk/main"
NOTO_SERIF_BOLD_COMMIT = "9b0f1436e455d902de067a2501422e5dc71ad16b"
NOTO_SERIF_BOLD_BASE = f"https://raw.githubusercontent.com/notofonts/noto-cjk/{NOTO_SERIF_BOLD_COMMIT}"
LITERATA_BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/literata"
FONT_FILES = (
    "NotoSansSC-Regular.otf.gz",
    "NotoSansSC-Medium.otf.gz",
    "NotoSansSC-Bold.otf.gz",
    "NotoSerifSC-Regular.otf.gz",
    "NotoSerifSC-Bold.otf.gz",
    "NotoSerifSC-Black.otf.gz",
    "Literata-Regular.ttf.gz",
    "Literata-SemiBold.ttf.gz",
    "Literata-Bold.ttf.gz",
)


def download(url: str, target: Path) -> bytes:
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            target.write_bytes(response.read())
    return target.read_bytes()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def requested_characters(design_text: Path, source_root: Path) -> set[int]:
    """Keep common Chinese text, all supplied design text, and UI symbols."""
    points = set(range(0x20, 0x250))
    for first, last in ((0x1E00, 0x1F00), (0x2000, 0x2070), (0x2190, 0x2200),
                        (0x2460, 0x2500), (0x25A0, 0x2600), (0x3000, 0x3040),
                        (0xFF00, 0xFFF0)):
        points.update(range(first, last))
    for lead in range(0xA1, 0xF8):
        for trail in range(0xA1, 0xFF):
            try:
                points.update(map(ord, bytes((lead, trail)).decode("gb2312")))
            except UnicodeDecodeError:
                pass
    if design_text.exists():
        points.update(map(ord, design_text.read_text(encoding="utf-8")))
    for directory in (source_root / "ui", source_root / "src"):
        if directory.exists():
            for path in directory.rglob("*"):
                if path.suffix in (".slint", ".rs", ".json"):
                    points.update(map(ord, path.read_text(encoding="utf-8")))
    return points


def write_font(font: TTFont, filename: str, source: bytes, source_url: str,
               points: set[int], output: Path, license_file: str) -> dict:
    font.recalcTimestamp = False
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True
    options.layout_features = ["*"]
    options.notdef_glyph = True
    options.notdef_outline = True
    options.recommended_glyphs = True
    options.hinting = False
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=points & set(font.getBestCmap()))
    subsetter.subset(font)
    raw = io.BytesIO()
    font.save(raw, reorderTables=True)
    data = raw.getvalue()
    compressed = gzip.compress(data, compresslevel=9, mtime=0)
    (output / filename).write_bytes(compressed)
    names = font["name"]
    return {
        "file": filename,
        "family": names.getDebugName(16) or names.getDebugName(1),
        "style": names.getDebugName(17) or names.getDebugName(2),
        "legacy_family": names.getDebugName(1),
        "weight": font["OS/2"].usWeightClass,
        "copyright": names.getDebugName(0),
        "version": names.getDebugName(5),
        "license": "OFL-1.1",
        "license_file": license_file,
        "source_url": source_url,
        "source_sha256": digest(source),
        "sha256": digest(compressed),
        "uncompressed_sha256": digest(data),
        "bytes": len(compressed),
        "uncompressed_bytes": len(data),
        "codepoints": len(font.getBestCmap()),
    }


def prepare(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    selected_fonts = set(args.font or FONT_FILES)
    points = requested_characters(args.design_text, args.root)
    for path in args.coverage_font:
        data = path.read_bytes()
        if path.suffix == ".gz":
            data = gzip.decompress(data)
        with TTFont(io.BytesIO(data)) as font:
            points.update(font.getBestCmap())
    records = []
    for style in ("Regular", "Medium", "Bold"):
        filename = f"NotoSansSC-{style}.otf.gz"
        if filename not in selected_fonts:
            continue
        source_url = f"{NOTO_BASE}/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-{style}.otf"
        reused = args.weread_fonts / filename if args.weread_fonts else None
        if reused and reused.exists():
            source = gzip.decompress(reused.read_bytes())
        else:
            source = download(source_url, cache / f"NotoSansCJKsc-{style}.otf")
        record = write_font(TTFont(io.BytesIO(source)), filename, source, source_url,
                            points, output, "NotoSansCJK-LICENSE.txt")
        record["preparation_input"] = (
            "Companion WeRead Simplified Chinese font subset"
            if reused and reused.exists() else "Official upstream OpenType font"
        )
        records.append(record)
        print(f"Prepared {filename}: {record['bytes']:,} bytes", flush=True)
    for style in ("Regular", "Bold", "Black"):
        filename = f"NotoSerifSC-{style}.otf.gz"
        if filename not in selected_fonts:
            continue
        serif_base = NOTO_SERIF_BOLD_BASE if style == "Bold" else NOTO_BASE
        source_url = f"{serif_base}/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-{style}.otf"
        cache_name = (f"NotoSerifCJKsc-Bold-{NOTO_SERIF_BOLD_COMMIT}.otf"
                      if style == "Bold" else f"NotoSerifCJKsc-{style}.otf")
        source = download(source_url, cache / cache_name)
        record = write_font(TTFont(io.BytesIO(source)), filename, source,
                            source_url, points, output, "NotoSerifCJK-LICENSE.txt")
        records.append(record)
        print(f"Prepared {record['file']}: {record['bytes']:,} bytes", flush=True)
    if any(filename.startswith("Literata-") for filename in selected_fonts):
        source_url = f"{LITERATA_BASE}/Literata%5Bopsz%2Cwght%5D.ttf"
        source = download(source_url, cache / "Literata-Variable.ttf")
        for style, weight in (("Regular", 400), ("SemiBold", 600), ("Bold", 700)):
            filename = f"Literata-{style}.ttf.gz"
            if filename not in selected_fonts:
                continue
            font = TTFont(io.BytesIO(source))
            font.recalcTimestamp = False
            font = instantiateVariableFont(font, {"opsz": 12, "wght": weight},
                                           inplace=True, updateFontNames=True)
            font["name"].setName("Literata", 16, 3, 1, 0x409)
            font["name"].setName(style, 17, 3, 1, 0x409)
            record = write_font(font, filename, source, source_url,
                                points, output, "Literata-OFL.txt")
            records.append(record)
            print(f"Prepared {record['file']}: {record['bytes']:,} bytes", flush=True)
    licenses = (
        ("NotoSans", f"{NOTO_BASE}/Sans/LICENSE", "NotoSansCJK-LICENSE.txt", "NotoSansCJK-OFL.txt"),
        ("NotoSerif", f"{NOTO_SERIF_BOLD_BASE if 'NotoSerifSC-Bold.otf.gz' in selected_fonts else NOTO_BASE}/Serif/LICENSE",
         "NotoSerifCJK-LICENSE.txt", "NotoSerifCJK-OFL.txt"),
        ("Literata", f"{LITERATA_BASE}/OFL.txt", "Literata-OFL.txt", "Literata-OFL.txt"),
    )
    for family, url, filename, cached_name in licenses:
        if not any(filename.startswith(family) for filename in selected_fonts):
            continue
        if args.font and (output / filename).exists():
            continue
        (output / filename).write_bytes(download(url, cache / cached_name))
    manifest = {
        "format_version": 1,
        "description": "GB2312, supplied design text, UI source text, Latin and UI-symbol subsets.",
        "compression": "gzip; mtime=0; raw font data is registered at application startup",
        "hinting": False,
        "literata_optical_size": 12,
        "fonts": records,
    }
    manifest_path = output / "manifest.json"
    if args.font and manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_filename = {record["file"]: record for record in previous["fonts"]}
        by_filename.update({record["file"]: record for record in records})
        order = list(FONT_FILES) + [filename for filename in by_filename if filename not in FONT_FILES]
        previous["fonts"] = [by_filename[filename] for filename in order if filename in by_filename]
        manifest = previous
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
                             encoding="utf-8")
    print(f"Total compressed font bytes: {sum(item['bytes'] for item in manifest['fonts']):,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path("tmp/fonts"))
    parser.add_argument("--design-text", type=Path, default=Path("tmp/design/design-text.txt"))
    parser.add_argument("--weread-fonts", type=Path)
    parser.add_argument("--font", choices=FONT_FILES, action="append",
                        help="Prepare only the selected font and preserve other assets and manifest records.")
    parser.add_argument("--coverage-font", type=Path, action="append", default=[],
                        help="Also retain codepoints covered by an existing raw or gzip-compressed font.")
    prepare(parser.parse_args())
