#!/usr/bin/env python3
"""Create original, numbered CBZ workloads on the designated Linux worker.

Run this generator and its full decoding checks through ``ssh test-env``.
It creates data only and never opens an application, a library database, or a
physical device. The geometry is original; BiliComics supplied the workload
concept of distinct page patterns, large numerals, and visible page identity.
"""

from __future__ import annotations

import argparse
from functools import lru_cache
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import stat
import sys
from xml.etree import ElementTree as ET
import zipfile

import PIL
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DATE = (2026, 11, 12, 0, 0, 0)
MAX_BOOK_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 90 * 1024 * 1024
SEED = 20261112
BOOKS = (
    (1, "Distinct-PNG", 12, "PNG", "L", "Distinct page identity, quick turns, backtracking and stale-frame detection"),
    (2, "Detailed-JPEG", 24, "JPEG", "RGB", "Color JPEG decoding, grayscale conversion, fine lines and cache eviction"),
    (3, "Vertical-PNG", 6, "PNG", "L", "Numbered long-strip windows, vertical movement and adjacent-window reuse"),
    (4, "Mixed-Spreads", 13, "JPEG", "RGB", "Portrait cover, wide halves, landscape spreads and an odd final page"),
    (5, "High-Resolution-PNG", 8, "PNG", "L", "Detailed high-resolution PNG decode, image processing and cold versus warm reopening"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dimensions(book: int, page: int) -> tuple[int, int]:
    if book == 3:
        return 618, 7690
    if book == 4 and page != 1 and page % 3 != 1:
        return 3400, 2360
    if book == 5:
        return 2480, 3508
    return 1700, 2360


class Artwork:
    def __init__(self, compressed_font: bytes):
        self.font_bytes = gzip.decompress(compressed_font)

    @lru_cache(maxsize=24)
    def font(self, size: int):
        return ImageFont.truetype(io.BytesIO(self.font_bytes), size=size)

    def text(self, draw, position, value: str, size: int, fill=0, anchor=None):
        draw.text(position, value, font=self.font(size), fill=fill, anchor=anchor,
                  stroke_width=0)

    def paper(self, size, mode: str, seed: int, detailed: bool):
        width, height = size
        randomizer = random.Random(seed)
        divisor = 4 if detailed else 12
        small_size = (max(1, width // divisor), max(1, height // divisor))
        noise = Image.frombytes("L", small_size, randomizer.randbytes(math.prod(small_size)))
        noise = noise.filter(ImageFilter.GaussianBlur(0.65))
        noise = noise.resize(size, Image.Resampling.BILINEAR)
        noise = noise.point([max(195, min(255, 216 + value // 7)) for value in range(256)])
        if mode == "RGB":
            noise = noise.convert("RGB")
        return noise

    def panel(self, image, box, seed: int, number: int, color: bool):
        randomizer = random.Random(seed)
        left, top, right, bottom = map(int, box)
        width, height = right - left, bottom - top
        layer = image.crop((left, top, right, bottom))
        draw = ImageDraw.Draw(layer)
        ink = (20, 23, 30) if color else 15
        white = (255, 255, 255) if color else 255
        light = (245, 245, 245) if color else 245
        palette = ((202, 220, 237), (241, 209, 195), (209, 231, 210), (229, 213, 237))
        tone = palette[number % len(palette)] if color else (204, 221, 238, 187)[number % 4]
        # Alternating screen tones and fine diagonals produce comic-like detail.
        spacing = max(9, width // (50 + number % 11))
        for offset in range(-height, width, spacing):
            draw.line((offset, 0, offset + height, height), fill=tone, width=max(1, width // 600))
        for line in range(18 + number % 13):
            y = int(height * (0.08 + line * 0.027))
            if y < height:
                draw.line((0, y, width, y + randomizer.randrange(-14, 15)), fill=tone,
                          width=max(1, width // 700))
        # City silhouettes and hills vary with the page seed.
        horizon = int(height * 0.67)
        x = 0
        while x < width:
            block = randomizer.randint(max(12, width // 35), max(16, width // 10))
            roof = horizon - randomizer.randint(max(10, height // 12), max(12, height // 3))
            draw.rectangle((x, roof, min(width, x + block), horizon), fill=ink)
            for window_y in range(roof + 12, horizon - 6, max(16, height // 30)):
                for window_x in range(x + 9, min(width, x + block) - 7, max(15, width // 50)):
                    draw.rectangle((window_x, window_y, window_x + 5, window_y + 6), fill=light)
            x += block + max(6, width // 90)
        center = (int(width * 0.57), int(height * 0.80))
        for ray in range(0, 360, 12 + number % 5):
            angle = math.radians(ray)
            draw.line((center[0], center[1], center[0] + int(width * math.cos(angle)),
                       center[1] + int(height * math.sin(angle))), fill=tone, width=2)
        radius = max(30, min(width, height) // 9)
        draw.ellipse((center[0] - radius, center[1] - radius,
                      center[0] + radius, center[1] + radius), fill=light, outline=ink, width=3)
        balloon = (int(width * 0.05), int(height * 0.07), int(width * 0.73), int(height * 0.30))
        draw.rounded_rectangle(balloon, radius=max(10, width // 35), fill=white, outline=ink, width=3)
        phrases = ("FOLLOW THE PATH", "KEEP MOVING", "A NEW VIEW", "WATCH THE NEXT FRAME")
        label_size = max(19, min(54, width // 22))
        self.text(draw, (balloon[0] + width * 0.03, balloon[1] + height * 0.025),
                  phrases[number % len(phrases)], label_size, ink)
        self.text(draw, (balloon[0] + width * 0.03, balloon[1] + height * 0.12),
                  f"SCENE {number:03}", label_size, ink)
        image.paste(layer, (left, top))
        ImageDraw.Draw(image).rectangle(box, outline=ink, width=max(4, image.width // 280))

    def identity(self, image, book: int, page: int, count: int, name: str):
        draw = ImageDraw.Draw(image)
        width, height = image.size
        ink = (0, 0, 0) if image.mode == "RGB" else 0
        white = (255, 255, 255) if image.mode == "RGB" else 255
        margin = max(16, width // 30)
        header_height = max(190, width // 6)
        draw.rectangle((margin, margin, width - margin, margin + header_height), fill=white, outline=ink, width=5)
        size = min(112, max(44, width // 17))
        self.text(draw, (margin * 1.5, margin * 1.4), f"PERF {book:02}   {page:02} / {count:02}", size, ink)
        self.text(draw, (margin * 1.5, margin * 1.4 + size * 1.55), name.upper(), max(24, size // 2), ink)
        # Eight large bits include fixed black/white endpoints and six page bits.
        cell = max(18, min(60, width // 17))
        row_width = cell * 8 + cell // 3 * 7
        start = (width - row_width) // 2
        y = height - margin - cell
        values = [0] + [255 if page & (1 << bit) else 0 for bit in range(6)] + [255]
        for index, value in enumerate(values):
            x = start + index * (cell + cell // 3)
            fill = (value, value, value) if image.mode == "RGB" else value
            draw.rectangle((x, y, x + cell, y + cell), fill=fill, outline=ink, width=2)
        return {"encoding": "fixed-black-six-page-bits-lsb-fixed-white", "value": page,
                "cell_size": cell, "top_left": [start, y]}

    def render(self, book: int, page: int, count: int, name: str, mode: str):
        width, height = dimensions(book, page)
        seed = SEED + book * 10000 + page * 101
        image = self.paper((width, height), mode, seed, detailed=book in (2, 5))
        margin = max(16, width // 30)
        header_bottom = margin + max(190, width // 6) + margin
        footer_top = height - margin * 2 - max(18, min(60, width // 17))
        if book == 3:
            segments = 6
            band_height = (footer_top - header_bottom - margin * (segments - 1)) // segments
            for segment in range(segments):
                top = header_bottom + segment * (band_height + margin)
                self.panel(image, (margin, top, width - margin, top + band_height),
                           seed + segment, page * 10 + segment, False)
                draw = ImageDraw.Draw(image)
                label = f"STRIP {page:02} / SEGMENT {segment + 1:02}"
                draw.rectangle((margin + 8, top + band_height - 105, width - margin - 8,
                                top + band_height - 8), fill=255)
                self.text(draw, (margin + 20, top + band_height - 95), label, 33)
        elif width > height:
            gutter = max(40, width // 40)
            for half, label in enumerate(("L", "R")):
                left = margin if half == 0 else width // 2 + gutter // 2
                right = width // 2 - gutter // 2 if half == 0 else width - margin
                center_y = (header_bottom + footer_top) // 2
                self.panel(image, (left, header_bottom, right, center_y - margin // 2),
                           seed + half, page * 10 + half, True)
                self.panel(image, (left, center_y + margin // 2, right, footer_top),
                           seed + 5 + half, page * 10 + 3 + half, True)
                draw = ImageDraw.Draw(image)
                draw.rectangle((right - 390, header_bottom + 14, right - 14, header_bottom + 200), fill="white")
                self.text(draw, (right - 375, header_bottom + 16), f"{page:02}-{label}", 130)
        else:
            gap = margin // 2
            middle = int(header_bottom + (footer_top - header_bottom) * 0.38)
            self.panel(image, (margin, header_bottom, width - margin, middle - gap), seed, page, mode == "RGB")
            center = width // 2
            self.panel(image, (margin, middle + gap, center - gap, footer_top), seed + 1, page + 40, mode == "RGB")
            self.panel(image, (center + gap, middle + gap, width - margin, footer_top), seed + 2, page + 80, mode == "RGB")
        marker = self.identity(image, book, page, count, name)
        return image, marker


def encode(image, image_format: str) -> bytes:
    stream = io.BytesIO()
    if image_format == "PNG":
        image.save(stream, format="PNG", compress_level=6, optimize=False, interlace=0)
    else:
        image.save(stream, format="JPEG", quality=88, subsampling=0, optimize=False, progressive=False)
    return stream.getvalue()


def metadata(title: str, purpose: str, page_count: int) -> bytes:
    root = ET.Element("ComicInfo")
    values = {"Title": title, "Series": title, "Writer": "Aidoku Test Fixtures", "Number": "1",
              "PageCount": str(page_count), "LanguageISO": "en",
              "Summary": purpose + ". Original synthetic test artwork with unique numbered pages. "
                         "Local decode, image-processing, cache and navigation workload; no HTTP timing claim."}
    for key, value in values.items():
        ET.SubElement(root, key).text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def member(archive, name: str, content: bytes):
    info = zipfile.ZipInfo(name, DATE)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_STORED
    archive.writestr(info, content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--font-gzip", type=Path, default=ROOT / "assets/fonts/NotoSansSC-Regular.otf.gz")
    args = parser.parse_args()
    if os.name == "nt" or not sys.platform.startswith("linux"):
        parser.error("Generate and verify through ssh test-env on the designated Linux worker.")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    expected = [f"Perf{book:02}-{name}.cbz" for book, name, *_ in BOOKS]
    expected += ["synthetic-manifest.json", "synthetic-preview.png"]
    if any((output / name).exists() or (output / name).is_symlink() for name in expected):
        parser.error("Use fresh synthetic output paths; existing artifacts are never replaced.")
    font = args.font_gzip.read_bytes()
    artwork = Artwork(font)
    manifest = {"schema_version": 1, "kind": "original-numbered-performance-comics", "seed": SEED,
                "generator": "scripts/prepare-performance-comics.py", "generator_sha256": sha256(Path(__file__).read_bytes()),
                "pillow_version": PIL.__version__, "font_sha256": sha256(font),
                "font_notice": "Noto Sans CJK, OFL-1.1; font software is not included in the CBZ archives",
                "artwork_provenance": "Original geometric fixtures generated by this script; no third-party comic pages or BiliComics database copied",
                "scope": "Offline page identity, decode, grayscale/image processing, cache and navigation; not network download throughput",
                "archive_compression": "ZIP_STORED", "books": []}
    all_hashes = set()
    previews = []
    for book, name, count, image_format, mode, purpose in BOOKS:
        filename = f"Perf{book:02}-{name}.cbz"
        title = f"Perf{book:02} {name.replace('-', ' ')}"
        path = output / filename
        pages = []
        print(f"GENERATE {filename}: {count} pages", flush=True)
        with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_STORED) as archive:
            member(archive, "ComicInfo.xml", metadata(title, purpose, count))
            member(archive, "README.txt", (title + "\n" + purpose + "\n\nOriginal synthetic artwork. "
                   "Each encoded page and visible page number is distinct. Generated by "
                   "scripts/prepare-performance-comics.py. Bundled Noto font output only; no font software "
                   "or third-party comic image is distributed.\n").encode("utf-8"))
            for page in range(1, count + 1):
                image, marker = artwork.render(book, page, count, name, mode)
                payload = encode(image, image_format)
                digest = sha256(payload)
                if digest in all_hashes:
                    raise ValueError("An encoded image is duplicated across the fixture set")
                all_hashes.add(digest)
                suffix = "png" if image_format == "PNG" else "jpg"
                entry = f"{page:03}.{suffix}"
                with Image.open(io.BytesIO(payload)) as decoded:
                    decoded.load()
                    if decoded.size != image.size or decoded.mode != mode or decoded.format != image_format:
                        raise ValueError(f"Decoded image identity differs: {filename}/{entry}")
                    grayscale = decoded.convert("L")
                    start, top = marker["top_left"]
                    cell = marker["cell_size"]
                    expected_tones = [0] + [255 if page & (1 << bit) else 0 for bit in range(6)] + [255]
                    for index, expected in enumerate(expected_tones):
                        position = (start + index * (cell + cell // 3) + cell // 2, top + cell // 2)
                        if abs(grayscale.getpixel(position) - expected) > 8:
                            raise ValueError(f"The visible page identity marker differs: {filename}/{entry}")
                if image_format == "PNG" and (payload[24] != 8 or payload[28] != 0):
                    raise ValueError("PNG pages must be non-interlaced eight-bit grayscale")
                if len(payload) >= MAX_BOOK_BYTES:
                    raise ValueError("A source image exceeds the local import page limit")
                member(archive, entry, payload)
                pages.append({"path": entry, "bytes": len(payload), "sha256": digest,
                              "width": image.width, "height": image.height, "format": image_format,
                              "mode": mode, "visual_page_number": page, "machine_marker": marker})
                if page == (2 if book == 4 else 1):
                    preview = image.convert("RGB")
                    if book == 3:
                        preview = preview.crop((0, 0, preview.width, min(2100, preview.height)))
                    preview.thumbnail((460, 580))
                    previews.append((title, preview))
                image.close()
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError(f"ZIP CRC validation failed: {filename}")
            for record in pages:
                payload = archive.read(record["path"])
                if sha256(payload) != record["sha256"]:
                    raise ValueError("The archived image differs from the decoded source")
                with Image.open(io.BytesIO(payload)) as decoded:
                    decoded.load()
        size = path.stat().st_size
        if not 0 < size < MAX_BOOK_BYTES:
            raise ValueError(f"The CBZ exceeds the paired-upload limit: {filename}, {size}")
        manifest["books"].append({"file": filename, "title": title, "purpose": purpose, "page_count": count,
                                  "bytes": size, "sha256": sha256(path.read_bytes()), "pages": pages,
                                  "checks": {"full_decode": True, "zip_crc": True, "source_archive_sha_match": True,
                                             "distinct_encoded_pages": True, "under_32_mib": True}})
        print(f"VERIFIED {filename}: {size} bytes", flush=True)
    total = sum(book["bytes"] for book in manifest["books"])
    if total > MAX_TOTAL_BYTES:
        raise ValueError("The synthetic suite exceeds the intended manual-test footprint")
    manifest["total_bytes"] = total
    manifest["total_pages"] = len(all_hashes)
    sheet = Image.new("RGB", (1500, 1340), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (title, preview) in enumerate(previews):
        x, y = index % 3 * 500, index // 3 * 670
        artwork.text(draw, (x + 15, y + 10), title, 24)
        sheet.paste(preview, (x + (500 - preview.width) // 2, y + 60))
    sheet.save(output / "synthetic-preview.png")
    with (output / "synthetic-manifest.json").open("x", encoding="utf-8") as destination:
        json.dump(manifest, destination, indent=2)
        destination.write("\n")
    print(json.dumps({"verified": True, "books": len(BOOKS), "pages": len(all_hashes), "bytes": total,
                      "manifest": str(output / "synthetic-manifest.json")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
