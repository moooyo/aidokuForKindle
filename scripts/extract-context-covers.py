"""Extract context-specific demo covers from the supplied Kindle design PDF.

Only artwork inside each cover is retained. Application badges on three Scribe
covers are repaired before output, so the interface can draw its own badges.
This utility authors input assets and does not run the application or its tests.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pypdf import PdfReader


SOURCE_SCALE = 3
PW_BOUNDS = [(66, 615 + 324 * index, 264, 885 + 324 * index) for index in range(5)]
HISTORY_BOUNDS = [(66, top, 210, top + 198) for top in (621, 873, 1263, 1515, 1905)]
SCRIBE_COLUMNS = (108, 634, 1162, 1688, 2216)
SCRIBE_ROWS = (1299, 2151)
SCRIBE_BOUNDS = [
    (left, top, left + 468, top + 624)
    for top in SCRIBE_ROWS
    for left in SCRIBE_COLUMNS
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def title_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return ImageFont.truetype(io.BytesIO(data), size=size)


def draw_title(image: Image.Image, text: str, font: ImageFont.FreeTypeFont,
               top: int, left: int | None = None) -> None:
    draw = ImageDraw.Draw(image)
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    x = left if left is not None else (image.width - 8 - width) // 2
    draw.text((x - bounds[0], top - bounds[1]), text, font=font, fill="white")


def repair_scribe_badge(image: Image.Image, index: int,
                        font: ImageFont.FreeTypeFont) -> tuple[Image.Image, str | None]:
    image = image.copy()
    draw = ImageDraw.Draw(image)
    if index == 2:
        # Preserve the white circle and border; reconstruct only the black footer.
        draw.rectangle((3, 500, image.width - 10, image.height - 4), fill="black")
        draw_title(image, "\u76d0\u4e0e\u7cd6", font, top=518, left=48)
        return image, "Removed the baked library badge and restored the white cover title on black."
    if index == 6:
        # This translation preserves the diagonal pattern's phase. The donor
        # region is clear artwork in the same cover, above and right of the badge.
        target = (20, 524, 188, 605)
        donor = (target[0] + 200, target[1] - 216, target[2] + 200, target[3] - 216)
        image.paste(image.crop(donor), target[:2])
        return image, "Removed the baked library badge using a phase-matched diagonal texture patch."
    if index == 8:
        # The badge covers only the black title band, not the gray dot pattern.
        draw.rectangle((3, 519, image.width - 10, image.height - 4), fill="black")
        draw_title(image, "\u5317\u65b9\u65c5\u56e2", font, top=550)
        return image, "Removed the baked library badge and restored the complete white cover title."
    return image, None


def save_cover(source: Image.Image, bounds: tuple[int, int, int, int],
               size: tuple[int, int], destination: Path, font: ImageFont.FreeTypeFont,
               scribe_index: int | None = None) -> str | None:
    cover = source.crop(bounds)
    repair = None
    if scribe_index is not None:
        cover, repair = repair_scribe_badge(cover, scribe_index, font)
    cover = ImageOps.grayscale(cover).resize(size, Image.Resampling.LANCZOS)
    cover = cover.point(lambda value: round(value / 17) * 17)
    cover.save(destination, optimize=True)
    return repair


def update_catalog(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for index, row in enumerate(data["catalog_paperwhite"][:5]):
        row["cover_file"] = f"catalog-cover-pw-{index:02}.png"
    for index, row in enumerate(data["catalog_scribe"][:10]):
        row["cover_file"] = f"catalog-cover-scribe-{index:02}.png"
    for index, row in enumerate(data.get("catalog", [])[:15]):
        row["cover_file"] = (
            f"catalog-cover-pw-{index:02}.png" if index < 5
            else f"catalog-cover-scribe-{index - 5:02}.png"
        )
    for index, row in enumerate(data["history"][:5]):
        row["cover_file"] = f"history-cover-{index:02}.png"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract(args: argparse.Namespace) -> None:
    args.output.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(args.pdf)
    pages = {
        page: reader.pages[page - 1].images[0].image.convert("RGB")
        for page in (5, 7, 11)
    }
    font = title_font(args.font, 54)
    groups = (
        ("catalog-paperwhite", 5, "catalog-cover-pw", PW_BOUNDS, (132, 180)),
        ("catalog-scribe", 11, "catalog-cover-scribe", SCRIBE_BOUNDS, (312, 416)),
        ("history", 7, "history-cover", HISTORY_BOUNDS, (96, 132)),
    )
    assets = []
    for context, page, prefix, all_bounds, size in groups:
        for index, bounds in enumerate(all_bounds):
            filename = f"{prefix}-{index:02}.png"
            path = args.output / filename
            repair = save_cover(pages[page], bounds, size, path, font,
                                index if context == "catalog-scribe" else None)
            assets.append({
                "file": filename,
                "context": context,
                "source_page": page,
                "source_pixel_bounds": list(bounds),
                "source_design_bounds": [round(value / SOURCE_SCALE, 3) for value in bounds],
                "output_pixels": list(size),
                "ui_badge_repair": repair,
                "sha256": sha256(path.read_bytes()),
            })
    manifest = {
        "format_version": 1,
        "source": "User-supplied Aidoku for Kindle UI UX design PDF",
        "source_pdf_sha256": sha256(args.pdf.read_bytes()),
        "source_pixels_per_design_unit": SOURCE_SCALE,
        "output_pixels_per_design_unit": 2,
        "grayscale_levels": 16,
        "description": "Context-specific cover artwork without surrounding application UI or baked library badges.",
        "assets": assets,
    }
    (args.output / "context-covers.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    update_catalog(args.catalog)
    print(f"Prepared {len(assets)} context covers and updated only their catalog cover_file fields.")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=root / "assets" / "demo")
    parser.add_argument("--catalog", type=Path, default=root / "assets" / "demo" / "catalog.json")
    parser.add_argument("--font", type=Path, default=root / "assets" / "fonts" / "NotoSerifSC-Black.otf.gz")
    extract(parser.parse_args())
