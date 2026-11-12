"""Rasterize startup labels on the designated remote worker.

Run with `python3 -I assets/startup/prepare.py` through `ssh test-env`.
The application uses these bounded grayscale payloads before font decompression;
no image decoder, font parser, or PNG decode runs in the startup display worker.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFont
import PIL


ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "assets" / "startup"
LOCALES = ("zh-CN", "zh-TW", "en", "ja")
LABELS = ("startup_loading", "startup_fonts", "startup_data", "startup_interface", "exit", "error")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rasterize(text: str, font_data: bytes, size: int) -> tuple[bytes, int, int]:
    font = ImageFont.truetype(io.BytesIO(font_data), size=size)
    left, top, right, bottom = font.getbbox(text)
    width, height = right - left + 8, bottom - top + 8
    image = Image.new("L", (width, height), 255)
    ImageDraw.Draw(image).text((4 - left, 4 - top), text, fill=0, font=font)
    if not (0 < width <= 2048 and 0 < height <= 256):
        raise ValueError(f"Startup label exceeds the backend bounds: {text!r}")
    return struct.pack("<II", width, height) + image.tobytes(), width, height


def main() -> None:
    source_path = ROOT / "assets" / "fonts" / "NotoSansSC-Regular.otf.gz"
    compressed_font = source_path.read_bytes()
    font_data = gzip.decompress(compressed_font)
    manifest = {
        "format_version": 1,
        "purpose": "Font-independent startup labels; activity dots are drawn by the backend",
        "encoding": "Two little-endian u32 dimensions followed by packed 8-bit grayscale pixels",
        "generator": "assets/startup/prepare.py",
        "generator_sha256": sha256(Path(__file__).read_bytes()),
        "pillow_version": PIL.__version__,
        "font": {
            "source": "assets/fonts/NotoSansSC-Regular.otf.gz",
            "sha256": sha256(compressed_font),
            "decompressed_sha256": sha256(font_data),
            "license": "OFL-1.1; see assets/fonts/OFL.txt and assets/fonts/NotoSansCJK-LICENSE.txt",
        },
        "images": {},
    }

    def write_image(name: str, text: str, size: int) -> None:
        payload, width, height = rasterize(text, font_data, size)
        (DESTINATION / name).write_bytes(payload)
        manifest["images"][name] = {
            "text": text,
            "font_size": size,
            "width": width,
            "height": height,
            "bytes": len(payload),
            "sha256": sha256(payload),
        }

    write_image("title.gray", "Aidoku", 64)
    for locale in LOCALES:
        catalog = json.loads((ROOT / "assets" / "i18n" / f"{locale}.json").read_text("utf-8-sig"))
        for label in LABELS:
            write_image(f"{locale}-{label}.gray", catalog[label], 40 if label == "startup_loading" else 32)
    (DESTINATION / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
