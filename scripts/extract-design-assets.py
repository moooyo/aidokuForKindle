"""Extract clean demo cover art and rebuild standalone comic pages from the supplied PDF.

This is an input-asset preparation utility, not an application test or screenshot runner.
The source PDF remains outside the repository. It contains raster mockups at three pixels
per design unit. Crops exclude navigation, captions, and application status badges.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader


COVER_TITLES = [
    "\u96fe\u6e2f\u5947\u8c2d", "\u7b2c\u4e03\u53f7\u5217\u8f66", "\u661f\u5c51\u98df\u5802", "\u6df1\u6d77\u90ae\u5dee",
    "\u7eb8\u9e64\u4e0e\u5251", "\u591c\u884c\u690d\u7269\u56ed", "\u94f6\u8272\u5c71\u8109", "\u706f\u5854\u5b88\u671b\u8005",
    "\u673a\u68b0\u5e08\u7684\u5348\u540e", "\u5317\u65b9\u65c5\u56e2", "\u767d\u663c\u7684\u732b", "\u56de\u58f0\u5c0f\u9547",
    "\u8ff7\u9014\u56fe\u4e66\u9986", "\u96ea\u539f\u4fe1\u4f7f", "\u9752\u94dc\u65f6\u4ee3", "\u6708\u9762\u6563\u6b65",
    "\u51ac\u65e5\u70bc\u91d1\u672f", "\u8fdc\u96f7",
]


def quantize_gray(image: Image.Image) -> Image.Image:
    """Keep image samples on the display's sixteen equally spaced gray levels."""
    return image.convert("L").point(lambda value: min(255, ((value + 8) // 17) * 17))


def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    data = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return ImageFont.truetype(io.BytesIO(data), size=size)


def extract_covers(reader: PdfReader, output: Path, font_path: Path) -> list[dict]:
    sheet = reader.pages[8].images[0].image.convert("RGB")
    clean_first = reader.pages[14].images[0].image.convert("RGB")
    x_origins = [108, 546, 986, 1424, 1864, 2302]
    y_origins = [1238, 1912, 2586]
    width, height = 376, 514
    title_font = load_font(font_path, 47)
    header_font = load_font(font_path, 43)
    records = []

    for index, title in enumerate(COVER_TITLES):
        left, top = x_origins[index % 6], y_origins[index // 6]
        crop = sheet.crop((left, top, left + width, top + height))
        drawing = ImageDraw.Draw(crop)
        repair = "none"

        if index == 0:
            crop = clean_first.resize((width, height), Image.Resampling.LANCZOS)
            repair = "clean standalone component from PDF page 15"
        elif index == 2:
            drawing.rectangle((2, 2, width - 3, 118), fill="white")
            bounds = drawing.textbbox((0, 0), title, font=header_font)
            drawing.text(((width - (bounds[2] - bounds[0])) / 2, 29 - bounds[1]), title, font=header_font, fill="black")
            crop.paste(crop.crop((0, 468, 90, 486)), (0, 496))
            repair = "redrawn title band to remove an unread-count overlay"
        elif index in (3, 7, 11, 15):
            strip_left = 274 if index in (3, 11) else 278
            if index in (3, 11):
                crop.paste(crop.crop((112, 5, 211, 256)), (274, 5))
                crop.paste(crop.crop((112, 13, 211, 67)), (274, 256))
            else:
                drawing.rectangle((strip_left, 2, width - 3, height - 3), fill="white")
                crop.paste(crop.crop((0, 460, 228, 484)), (0, 490))
            drawing.rectangle((strip_left, 17, width - 15, 28 + 51 * len(title)), fill="white")
            for offset, character in enumerate(title):
                bounds = drawing.textbbox((0, 0), character, font=title_font)
                drawing.text((strip_left + (width - 15 - strip_left - (bounds[2] - bounds[0])) / 2, 28 + offset * 51 - bounds[1]), character, font=title_font, fill="black")
            repair = "redrawn vertical title strip to remove an unread-count overlay"
        elif index in (5, 13):
            crop.paste(crop.crop((150, 17, 256, 116)), (256, 17))
            repair = "continued existing horizontal rules over the unread-count overlay"
        elif index in (9, 17):
            drawing.rectangle((2, 2, width - 3, 245), fill="black")
            radius = round(width * 0.185)
            center_x, center_y = round(width * 0.72), round(height * 0.24)
            drawing.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill="white")
            repair = "rebuilt the original moon motif without the unread-count overlay"

        destination = output / f"cover-{index:02}.png"
        quantize_gray(crop).save(destination, optimize=True)
        records.append({
            "file": destination.name,
            "title": title,
            "source_pdf_page": 15 if index == 0 else 9,
            "source_pixel_crop": None if index == 0 else [left, top, left + width, top + height],
            "width": width,
            "height": height,
            "overlay_removal": repair,
        })
    return records


def panel(size: tuple[int, int], pattern: str, phase: int = 0) -> Image.Image:
    width, height = size
    image = Image.new("L", size, 255)
    drawing = ImageDraw.Draw(image)
    if pattern == "dots":
        for y in range(5 + phase, height, 14):
            for x in range(5, width, 14):
                drawing.ellipse((x - 3, y - 3, x + 3, y + 3), fill=0)
    elif pattern == "white-dots":
        drawing.rectangle((0, 0, width, height), fill=85)
        for y in range(4, height, 12):
            for x in range(4, width, 12):
                drawing.ellipse((x - 2, y - 2, x + 2, y + 2), fill=255)
    elif pattern in ("horizontal", "vertical"):
        if pattern == "horizontal":
            for y in range(0, height, 12):
                drawing.line((0, y, width, y), fill=0, width=2)
        else:
            for x in range(0, width, 18):
                drawing.line((x, 0, x, height), fill=0, width=5)
    elif pattern == "crosshatch":
        for x in range(-height, width + height, 18):
            drawing.line((x, 0, x + height, height), fill=0, width=2)
            drawing.line((x, 0, x - height, height), fill=0, width=2)
    elif pattern == "sunburst":
        center = (width // 2, round(height * 0.43))
        radius = round(width * 0.19)
        for angle in range(0, 360, 12):
            radians = math.radians(angle)
            start = (center[0] + math.cos(radians) * radius, center[1] + math.sin(radians) * radius)
            end = (center[0] + math.cos(radians) * max(width, height) * 2, center[1] + math.sin(radians) * max(width, height) * 2)
            drawing.line((*start, *end), fill=0, width=6)
        drawing.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=255)
    drawing.rectangle((1, 1, width - 2, height - 2), outline=0, width=5)
    return image


def speech(image: Image.Image, bounds: tuple[int, int, int, int], count: int = 3, square: bool = False) -> None:
    drawing = ImageDraw.Draw(image)
    if square:
        drawing.rounded_rectangle(bounds, radius=8, fill=255, outline=0, width=4)
    else:
        drawing.ellipse(bounds, fill=255, outline=0, width=4)
    left, top, right, bottom = bounds
    center_x = (left + right) / 2
    for index in range(count):
        x = round(center_x + (index - (count - 1) / 2) * 19)
        drawing.line((x, top + 44, x, bottom - 44 + (index % 2) * 12), fill=0, width=6)


def continue_front_art(image: Image.Image) -> None:
    """Match front-page continuations to the original art on both clip boundaries.

    The first observed row, y=178, contains a PDF application-header separator.
    Replace it with continued art and keep [0, 179, 1172, 1269) unchanged. All
    three front-panel backgrounds use white 255, unlike the back-page dot field.
    """
    width, height = image.size
    scale = 8
    sample_offset = (scale - 1) / (2 * scale)

    def point(x: float, y: float, origin_x: int = 0, origin_y: int = 0) -> tuple[int, int]:
        return (round((x - origin_x + sample_offset) * scale), round((y - origin_y + sample_offset) * scale))

    def box(bounds: tuple[float, float, float, float], origin_x: int = 0, origin_y: int = 0) -> tuple[int, int, int, int]:
        left, top, right, bottom = bounds
        return (*point(left, top, origin_x, origin_y), *point(right, bottom, origin_x, origin_y))

    # Sample the original horizontal-rule profile, including its antialiasing.
    top = quantize_gray(image.crop((1100, 192, 1101, 371))).resize((width, 179), Image.Resampling.NEAREST)
    overlay = Image.new("RGBA", (width * scale, 179 * scale), (0, 0, 0, 0))
    drawing = ImageDraw.Draw(overlay)
    drawing.rounded_rectangle(box((36.5, 87.5, 160.5, 284.5)), radius=8 * scale, fill=(0, 0, 0, 255))
    drawing.rounded_rectangle(box((40.5, 91.5, 156.5, 280.5)), radius=4 * scale, fill=(255, 255, 255, 255))
    for left, right in ((72.5, 80.5), (95.5, 103.5), (116.5, 124.5)):
        drawing.rectangle(box((left, 131.5, right, 179)), fill=(0, 0, 0, 255))
    for left, right in ((-0.5, 4.5), (1166.5, 1171.5)):
        drawing.rectangle(box((left, -0.5, right, 179)), fill=(0, 0, 0, 255))
    top = quantize_gray(Image.alpha_composite(top.convert("RGBA"), overlay.resize(top.size, Image.Resampling.LANCZOS)))

    lower_height = height - 1269
    left = Image.new("L", (576 * scale, lower_height * scale), 255)
    drawing = ImageDraw.Draw(left)
    focus_x, focus_y = 287.41545428149124, 1336.0866790262958
    phase = 6.768962320321114
    half_angle = 1.4984945157857084 / 2
    for index in range(45):
        start = math.radians(phase + index * 8 - half_angle)
        end = math.radians(phase + index * 8 + half_angle)
        vertices = ((focus_x, focus_y), (focus_x + math.cos(start) * 4000, focus_y + math.sin(start) * 4000), (focus_x + math.cos(end) * 4000, focus_y + math.sin(end) * 4000))
        drawing.polygon([point(x, y, 0, 1269) for x, y in vertices], fill=0)
    circle_x, circle_y, radius = 287.7675886575821, 1337.1505415301122, 97.17647228926168
    drawing.ellipse(box((circle_x - radius, circle_y - radius, circle_x + radius, circle_y + radius), 0, 1269), fill=255)
    for bounds in ((-0.5, 1268.5, 4.5, 1647.5), (570.5, 1268.5, 575.5, 1647.5), (-0.5, 1642.5, 575.5, 1647.5)):
        drawing.rectangle(box(bounds, 0, 1269), fill=0)
    left = quantize_gray(left.resize((576, lower_height), Image.Resampling.LANCZOS))

    right = Image.new("L", (576 * scale, lower_height * scale), 255)
    drawing = ImageDraw.Draw(right)
    slope = 1.0003055300947143
    period = 16.970562748477143
    line_width = round(1.9993473797851546 * scale)
    for phase, direction in ((12.303998239857012, 1), (6.694682932717064, -1)):
        for index in range(-200, 201):
            constant = phase + index * period
            drawing.line((point(constant + direction * slope * 1260, 1260, 596, 1269), point(constant + direction * slope * 1650, 1650, 596, 1269)), fill=0, width=line_width)
    # The observed silhouette has straight sides at x=645 and x=885.
    drawing.rectangle(box((644.5, 1268.5, 884.5, 1647.5), 596, 1269), fill=0)
    for bounds in ((595.5, 1268.5, 600.5, 1647.5), (1166.5, 1268.5, 1171.5, 1647.5), (595.5, 1642.5, 1171.5, 1647.5)):
        drawing.rectangle(box(bounds, 596, 1269), fill=0)
    right = quantize_gray(right.resize((576, lower_height), Image.Resampling.LANCZOS))
    bottom = Image.new("L", (width, lower_height), 255)
    bottom.paste(left, (0, 0))
    bottom.paste(right, (596, 0))
    image.paste(top.crop((0, 6, width, 179)), (0, 6))
    image.paste(bottom, (0, 1269))


def front_continuation_record(path: Path) -> dict:
    image = Image.open(path).convert("L")
    return {
        "file": "page-00.png",
        "width": 1172,
        "height": 1648,
        "repaired_regions": [[0, 6, 1172, 179], [0, 1269, 1172, 1648]],
        "reason": "Match the observed horizontal-rule phase, balloon edges, eight-degree ray period, crosshatch phase and width, and silhouette sides. Remove the PDF application-header separator in the first observed row instead of retaining a baked-in UI line.",
        "panel_backgrounds": {"city": 255, "sunburst": 255, "crosshatch": 255},
        "excluded_application_separator_rows": [178],
        "preserved_clip_pixel_bounds": [0, 179, 1172, 1269],
        "preserved_clip_pixels_sha256": hashlib.sha256(image.crop((0, 179, 1172, 1269)).tobytes()).hexdigest(),
        "replaced_fixture_file_sha256": "0b8dfbc4da3e6cb6f2f533fcac74ab1711b7a611951835f0a7a8b8e23196a5af",
        "after_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def continue_back_art(image: Image.Image) -> None:
    """Continue the PDF clip's geometry without duplicating its clipped balloons.

    The PDF reader chrome hides y=0..91 and y=1304..1647. These continuations
    follow the observed rays, dot lattice, balloon curves, and stripe phase. The
    original art pixels in [0, 92, 1172, 1304) remain untouched.
    """
    width, height = image.size
    scale = 8
    top = Image.new("L", (width * scale, 94 * scale), 255)
    drawing = ImageDraw.Draw(top)

    def rectangle(bounds: tuple[float, float, float, float], fill: int = 0) -> None:
        drawing.rectangle(tuple(round(value * scale) for value in bounds), fill=fill)

    def balloon(center: tuple[float, float], radii: tuple[float, float], extra: float, fill: int) -> None:
        x, y = center
        radius_x, radius_y = radii[0] + extra, radii[1] + extra
        drawing.ellipse(tuple(round(value * scale) for value in (x - radius_x, y - radius_y, x + radius_x, y + radius_y)), fill=fill)

    # Linear edge fits use original rows 92..115; no synthetic ray is copied.
    rays = [
        ((0.8469565217391308, 2.673333333333289), (0.8139130434782608, 19.843333333333376)),
        ((0.6343478260869576, 92.55333333333321), (0.5999999999999993, 107.48333333333345)),
        ((0.4521739130434789, 168.3666666666666), (0.4169565217391319, 181.80333333333317)),
        ((0.2756521739130427, 236.80333333333343), (0.2556521739130449, 247.87333333333316)),
        ((0.14913043478260954, 296.43999999999994), (0.12086956521739055, 307.65666666666675)),
        ((0.0, 356.5), (6.040304473112398e-15, 364.49999999999926)),
        ((-0.14478260869565232, 415.9433333333333), (-0.16826086956521757, 426.8733333333334)),
        ((-0.29304347826087085, 477.6633333333335), (-0.3152173913043473, 488.99999999999994)),
        ((-0.4443478260869607, 543.6566666666671), (-0.47782608695652257, 557.0800000000002)),
    ]
    for (left_slope, left_start), (right_slope, right_start) in rays:
        points = ((left_start, 0), (right_start, 0), (right_start + right_slope * 94, 94), (left_start + left_slope * 94, 94))
        drawing.polygon([(round(x * scale), round(y * scale)) for x, y in points], fill=0)
    rectangle((0, 0, 66.5, 94), 255)
    rectangle((551.5, 0, width, 94), 255)
    rectangle((66.5, 0, 71.5, 94))
    rectangle((546.5, 0, 551.5, 94))

    # The PDF dot field is gray 221; a white continuation creates a visible seam.
    rectangle((573, 0, 1155.5, 94), 221)
    # The fitted grid retains the horizontal and vertical phases of the PDF dots.
    dot_radius = 4.59879473787221
    for row in range(-8, 1):
        y = 117.3315589792018 + row * 18.91861138246331
        for col in range(-15, 17):
            x = 849.4956645664004 + col * 18.90333103782296
            if 579 < x < 1150:
                drawing.ellipse(tuple(round(value * scale) for value in (x - dot_radius, y - dot_radius, x + dot_radius, y + dot_radius)), fill=0)
    upper_center = (707.7865168539325, 179.7543619830827)
    upper_radii = (90.71417512446395, 132.52813590631172)
    balloon(upper_center, upper_radii, 3.2, 0)
    balloon(upper_center, upper_radii, 0, 255)
    rectangle((573, 0, 577.5, 94))
    rectangle((1150.5, 0, 1155.5, 94))
    top = quantize_gray(top.resize((width, 94), Image.Resampling.LANCZOS))

    # A sampled row preserves the exact stripe antialiasing and phase at y=1304.
    bottom = quantize_gray(image.crop((0, 1080, width, 1081))).resize((width, height - 1304), Image.Resampling.NEAREST)
    supersampled = bottom.resize((width * scale, bottom.height * scale), Image.Resampling.NEAREST)
    drawing = ImageDraw.Draw(supersampled)
    lower_center = (613.1932989690722, 1224.0433004197575 - 1304)
    lower_radii = (124.74018203309839, 124.90819410328363)
    balloon(lower_center, lower_radii, 3.2, 0)
    balloon(lower_center, lower_radii, 0, 255)
    completed = quantize_gray(supersampled.resize(bottom.size, Image.Resampling.LANCZOS))
    bottom.paste(completed.crop((480, 0, 748, 60)), (480, 0))
    # Preserve the outer rules used by border cropping, as well as the PDF clip.
    image.paste(top.crop((0, 2, width, 92)), (0, 2))
    image.paste(bottom.crop((0, 0, width, bottom.height - 2)), (0, 1304))


def back_continuation_record(path: Path) -> dict:
    image = Image.open(path).convert("L")
    return {
        "file": "page-01.png",
        "width": 1172,
        "height": 1648,
        "repaired_regions": [[0, 2, 1172, 92], [0, 1304, 1172, 1646]],
        "reason": "Continue the observed ray edges, dot lattice, balloon curves, and stripe phase instead of pasting misaligned synthetic panel fragments at y=92 and y=1304. Retain the source dot field's gray-221 background across the top continuation.",
        "upper_dot_field_background": 221,
        "speech_balloon_background": 255,
        "preserved_clip_pixel_bounds": [0, 92, 1172, 1304],
        "preserved_clip_pixels_sha256": hashlib.sha256(image.crop((0, 92, 1172, 1304)).tobytes()).hexdigest(),
        "replaced_fixture_file_sha256": "b927049af19f5a1141e428c63d24c6147b22755ffe22c49cf54d431ebee020d3",
        "after_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def comic_pages(reader: PdfReader, output: Path) -> list[str]:
    """Restore the observed art clips and extend artwork behind the reader chrome."""
    width, height = 1172, 1648
    front = Image.new("L", (width, height), 255)
    city = panel((width, 568), "horizontal")
    city_draw = ImageDraw.Draw(city)
    for left, top, right in [(16, 218, 128), (136, 142, 290), (298, 262, 384), (394, 94, 524), (532, 196, 638), (646, 294, 862), (870, 164, 978), (986, 236, 1154)]:
        city_draw.rectangle((left, top + 180, right, 562), fill=0)
    speech(city, (38, 88, 156, 280), count=3, square=True)
    front.paste(city, (0, 0))
    left_middle = panel((572, 484), "dots")
    speech(left_middle, (392, 28, 538, 260))
    front.paste(left_middle, (0, 588))
    right_long = panel((576, 1060), "crosshatch")
    speech(right_long, (370, 36, 540, 348))
    silhouette = ImageDraw.Draw(right_long)
    silhouette.rounded_rectangle((62, 382, 294, 1270), radius=116, fill=0)
    front.paste(right_long, (596, 588))
    front.paste(panel((572, 552), "sunburst"), (0, 1096))
    portrait = reader.pages[2].images[0].image.convert("L")
    observed_front = portrait.crop((48, 267, 1806, 1904)).resize((1172, 1091), Image.Resampling.LANCZOS)
    front.paste(observed_front, (0, 178))
    continue_front_art(front)

    back = Image.new("L", (width, height), 255)
    back.paste(panel((504, 1000), "sunburst"), (0, 0))
    back_upper = panel((644, 448), "dots")
    speech(back_upper, (54, -50, 278, 226))
    back.paste(back_upper, (528, 0))
    back_lower = panel((644, 528), "white-dots")
    speech(back_lower, (430, 258, 590, 490), count=2, square=True)
    back.paste(back_lower, (528, 472))
    back_bottom = panel((width, 624), "vertical")
    speech(back_bottom, (452, 40, 726, 388), count=4)
    back.paste(back_bottom, (0, 1024))

    landscape = reader.pages[11].images[0].image.convert("L")
    observed_back = landscape.crop((0, 234, 1860, 2156))
    ImageDraw.Draw(observed_back).rectangle((1842, 0, 1859, observed_back.height), fill=255)
    observed_back = observed_back.resize((1172, 1212), Image.Resampling.LANCZOS)
    back.paste(observed_back, (0, 92))
    # These panel rules preserve the full image bounds when border cropping is enabled.
    # In the open-menu mockup they lie behind the top and bottom reader chrome.
    back_draw = ImageDraw.Draw(back)
    back_draw.line((0, 0, width - 1, 0), fill=0, width=2)
    back_draw.line((0, height - 1, width - 1, height - 1), fill=0, width=2)
    continue_back_art(back)

    names = ["page-00.png", "page-01.png"]
    for image, name in zip((front, back), names):
        quantize_gray(image).save(output / name, optimize=True)
    return names


def search_covers(reader: PdfReader, output: Path) -> list[str]:
    """Use the wide source-card artwork so printed titles survive cover fitting."""
    source = reader.pages[5].images[0].image.convert("L")
    rectangles = [(432, 672, 744, 1038), (798, 672, 1110, 1038), (66, 1317, 378, 1683)]
    names = []
    for index, rectangle in enumerate(rectangles):
        name = f"search-cover-{index:02}.png"
        quantize_gray(source.crop(rectangle)).save(output / name, optimize=True)
        names.append(name)
    return names


def paperwhite_library_covers(output: Path, font_path: Path) -> list[str]:
    """Keep printed titles clear of badge areas in the wider Paperwhite cover slots."""
    width, height = 266, 340
    font = load_font(font_path, 32)
    star = Image.open(output / "cover-02.png").convert("L").resize((width, height), Image.Resampling.LANCZOS)
    drawing = ImageDraw.Draw(star)
    drawing.rectangle((2, 2, width - 3, 77), fill=255)
    title = "\u661f\u5c51\u98df\u5802"
    bounds = drawing.textbbox((0, 0), title, font=font)
    drawing.text(((width - bounds[2] + bounds[0]) / 2, 28 - bounds[1]), title, font=font, fill=0)

    silver = Image.new("L", (width, height), 255)
    drawing = ImageDraw.Draw(silver)
    for offset in range(-height, width + height, 12):
        drawing.line((offset, 0, offset + height, height), fill=0, width=2)
        drawing.line((offset, 0, offset - height, height), fill=0, width=2)
    drawing.rectangle((1, 1, width - 2, height - 2), outline=0, width=2)
    drawing.rectangle((26, 36, 214, 92), fill=0)
    title = "\u94f6\u8272\u5c71\u8109"
    bounds = drawing.textbbox((0, 0), title, font=font)
    drawing.text(((26 + 214 - bounds[2] + bounds[0]) / 2, 47 - bounds[1]), title, font=font, fill=255)

    lighthouse = Image.open(output / "cover-07.png").convert("L").resize((width, height), Image.Resampling.LANCZOS)
    drawing = ImageDraw.Draw(lighthouse)
    drawing.rectangle((197, 2, width - 3, height - 3), fill=255)
    font = load_font(font_path, 34)
    title = "\u706f\u5854\u5b88\u671b\u8005"
    for index, character in enumerate(title):
        bounds = drawing.textbbox((0, 0), character, font=font)
        drawing.text((197 + (67 - bounds[2] + bounds[0]) / 2, 28 + index * 40 - bounds[1]), character, font=font, fill=0)
    names = []
    for index, image in ((2, star), (6, silver), (7, lighthouse)):
        name = f"library-cover-pw-{index:02}.png"
        quantize_gray(image).save(output / name, optimize=True)
        names.append(name)
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "assets" / "demo")
    parser.add_argument("--serif-font", type=Path, default=Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSerifSC-Black.otf.gz")
    parser.add_argument("--repair-back-only", action="store_true", help="Repair page-01.png and its manifest entry while preserving every other asset.")
    parser.add_argument("--repair-front-only", action="store_true", help="Repair page-00.png and its manifest entry while preserving every other asset.")
    args = parser.parse_args()
    if args.repair_front_only and args.repair_back_only:
        parser.error("Select only one repair-only mode.")
    if args.repair_front_only:
        page_path = args.output_dir / "page-00.png"
        image = Image.open(page_path).convert("L")
        if image.size != (1172, 1648):
            parser.error("--repair-front-only expects the 1172x1648 bundled page-00.png.")
        continue_front_art(image)
        quantize_gray(image).save(page_path, optimize=True)
        manifest_path = args.output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["comic_page_alignment"]["paperwhite_front_hidden_continuation"] = front_continuation_record(page_path)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print("Repaired the front-page continuations; retained the clean PDF clip and all other assets.")
        return
    if args.repair_back_only:
        page_path = args.output_dir / "page-01.png"
        image = Image.open(page_path).convert("L")
        if image.size != (1172, 1648):
            parser.error("--repair-back-only expects the 1172x1648 bundled page-01.png.")
        continue_back_art(image)
        quantize_gray(image).save(page_path, optimize=True)
        manifest_path = args.output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["comic_page_alignment"]["scribe_back_hidden_continuation"] = back_continuation_record(page_path)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        print("Repaired the back-page continuations; retained the PDF clip and all other assets.")
        return
    if args.pdf is None:
        parser.error("--pdf is required unless a repair-only mode is selected.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(args.pdf)
    records = extract_covers(reader, args.output_dir, args.serif_font)
    pages = comic_pages(reader, args.output_dir)
    search_art = search_covers(reader, args.output_dir)
    paperwhite_art = paperwhite_library_covers(args.output_dir, args.serif_font)
    manifest = {
        "format_version": 1,
        "source": "User-supplied Aidoku for Kindle UI/UX design PDF",
        "source_sha256": hashlib.sha256(args.pdf.read_bytes()).hexdigest(),
        "covers": records,
        "library_cover_mapping": "Use cover-(manga_index % 18), zero-padded to two digits, for the 46-item demo library.",
        "comic_pages": pages,
        "search_covers": search_art,
        "search_cover_alignment": {
            "pdf_page": 6,
            "source_pixel_crops": [[432, 672, 744, 1038], [798, 672, 1110, 1038], [66, 1317, 378, 1683]],
            "reason": "The 312x366 crops match the 104x122 search-card aspect ratio. Reusing the narrower library art caused cover fitting to cut off the Nocturne title.",
        },
        "paperwhite_library_covers": paperwhite_art,
        "comic_page_provenance": "The observed art-only clips in PDF pages 3 and 12 are retained, and their hidden top and bottom regions are continued with matching panel rules and screen tones. No application UI is included.",
        "comic_page_alignment": {
            "reason": "The initial reconstruction put the city panel at image y=0..194, so the 89-unit reader header hid its upper section. The revised city extends behind the header and ends at design y=284; middle panels start at y=294 and the radial panel at y=548.",
            "paperwhite_front_art_clip": {"pdf_page": 3, "source_pixels": [48, 267, 1806, 1904], "destination_pixels": [0, 178, 1172, 1269]},
            "paperwhite_front_hidden_continuation": front_continuation_record(args.output_dir / "page-00.png"),
            "scribe_back_art_clip": {"pdf_page": 12, "source_pixels": [0, 234, 1860, 2156], "destination_pixels": [0, 92, 1172, 1304]},
            "scribe_back_hidden_continuation": back_continuation_record(args.output_dir / "page-01.png"),
            "art_only_boundaries": "Crop bottoms exclude the four-pixel application-toolbar separator rules at source y=1904 and y=2156; the landscape center gutter marker is also excluded.",
            "scribe_spread_projection": "A 1172x1648 page contained in a 620x930 half-screen starts at y=29.095 design units. The city baseline projects to y=329.57 and the middle-panel top to y=340.15.",
            "border_crop": "Full-width black panel rules at the back page's top and bottom preserve its intended bounds under the default border-cropping setting.",
        },
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(records)} library covers, {len(search_art)} search covers, and {len(pages)} standalone comic pages.")


if __name__ == "__main__":
    main()
