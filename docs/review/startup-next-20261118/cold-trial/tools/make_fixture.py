#!/usr/bin/env python3
"""Generate synthetic local cover files for the cold-derivative experiment."""

import argparse
import hashlib
import json
import sys
import zlib
from pathlib import Path

import PIL
from PIL import Image, ImageChops, ImageDraw, ImageOps, features


# The strip exceeds Aidoku's 16 Mi-pixel full-decoder limit. The other five
# inputs intentionally stay below it. All source bytes fit the 32 MiB limit.
CASES = (
    ("01-gray.png", "L", 1001, 1349, 13),
    ("02-rgb-large.png", "RGB", 1961, 2767, 37),
    ("03-alpha.png", "RGBA", 1153, 1619, 59),
    ("04-baseline.jpg", "RGB", 2503, 3301, 83),
    ("05-progressive.jpg", "RGB", 1891, 2789, 107),
    ("06-tall-gray.png", "L", 1537, 11201, 131),
)


def pattern(mode, width, height, seed):
    side = 192
    tile = Image.frombytes("L", (side, side), bytes(
        (x * 3 + y * 5 + ((x ^ y) * 7) + ((x * y + seed) % 31)) & 255
        for y in range(side) for x in range(side)
    ))
    field = Image.new("L", (width, height))
    for y in range(0, height, side):
        for x in range(0, width, side):
            field.paste(tile, (x, y))
    pen = ImageDraw.Draw(field)
    for row in range(0, height, 127):
        start = (row * 67 + seed * 31) % max(width // 3, 1)
        pen.line((start, row, width - start, row + 31), fill=(row + seed) & 255, width=9)
        pen.rectangle((start, row + 39, width - start, row + 54), outline=17, width=3)
    if mode == "L":
        return field
    channels = [field, ImageChops.offset(field, seed, -19), ImageOps.invert(field)]
    if mode == "RGBA":
        channels.append(ImageChops.offset(field, -73, seed))
    return Image.merge(mode, channels)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("destination", type=Path, help="New directory; existing paths are rejected")
    args = cli.parse_args()
    if not sys.platform.startswith("linux"):
        cli.error("Run this fixture generator through ssh test-env")
    args.destination.mkdir(parents=False, exist_ok=False)
    rows = []
    for name, mode, width, height, seed in CASES:
        path = args.destination / name
        image = pattern(mode, width, height, seed)
        if name.endswith(".jpg"):
            image.save(path, format="JPEG", quality=87, subsampling=2,
                       optimize=False, progressive="progressive" in name)
        else:
            image.save(path, format="PNG", compress_level=5, optimize=False)
        image.close()
        with Image.open(path) as check:
            check.verify()
        with Image.open(path) as check:
            check.load()
            if check.mode != mode or check.size != (width, height):
                raise ValueError(f"Unexpected fixture geometry: {name}")
            pixels = hashlib.sha256(check.tobytes()).hexdigest()
        source = path.read_bytes()
        if len(source) > 32 * 1024 * 1024:
            raise ValueError(f"Compressed size exceeded: {name}")
        rows.append({"file": name, "format": path.suffix[1:], "mode": mode, "width": width,
                     "height": height, "bytes": len(source), "source_sha256": hashlib.sha256(source).hexdigest(),
                     "decoded_source_sha256": pixels, "streaming_png": width * height > 16 * 1024 * 1024})
    result = {"generator": "synthetic-pattern-v1", "user_data": False, "inputs": rows,
              "codecs": {"pillow": PIL.__version__, "jpeg": features.version_codec("jpg"),
                         "zlib": zlib.ZLIB_RUNTIME_VERSION}}
    (args.destination / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"{len(rows)} synthetic inputs; {sum(row['bytes'] for row in rows)} compressed bytes")


if __name__ == "__main__":
    main()
