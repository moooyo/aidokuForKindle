#!/usr/bin/env python3
"""Prepare fixed-size, preprocessed A/B pages on the designated Linux worker."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    if not sys.platform.startswith("linux"):
        raise SystemExit("Run this preparation through ssh test-env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processor", type=Path, required=True)
    parser.add_argument("--chart", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    args.output.mkdir(parents=True)
    source_dir = args.output / "sources"
    pages_dir = args.output / "pages"
    source_dir.mkdir()
    pages_dir.mkdir()
    width, height = 1860, 2480
    fonts = root / "assets/fonts"
    regular = gzip.decompress((fonts / "NotoSansSC-Regular.otf.gz").read_bytes())
    bold = gzip.decompress((fonts / "NotoSansSC-Bold.otf.gz").read_bytes())

    def font(size: int, heavy: bool = False):
        return ImageFont.truetype(io.BytesIO(bold if heavy else regular), size)

    def palette(image: Image.Image) -> Image.Image:
        values = np.asarray(image.convert("L"), dtype=np.uint16)
        return Image.fromarray((((values + 8) // 17) * 17).astype(np.uint8))

    chart = Image.open(args.chart).convert("L")
    assert chart.size == (width, height)
    chart.save(source_dir / "chart.png")
    # Existing demo art is already quantized. Add an explicitly synthetic,
    # continuous lighting field to create a controlled eight-bit shading input.
    art = Image.open(root / "assets/demo/page-01.png").convert("L")
    art.thumbnail((1700, 2100), Image.Resampling.LANCZOS)
    values = np.asarray(art, dtype=np.float64)
    yy, xx = np.mgrid[:art.height, :art.width]
    nx, ny = xx / max(1, art.width - 1), yy / max(1, art.height - 1)
    lighting = np.clip(0.72 + 0.23 * nx + 0.05 * np.cos(ny * np.pi * 2), 0.67, 1.0)
    shaded = Image.fromarray(np.rint(values * lighting).astype(np.uint8))
    comic = Image.new("L", (width, height), 255)
    comic.paste(shaded, ((width - art.width) // 2, 210))
    comic.save(source_dir / "comic-shading.png")

    outputs: dict[str, dict[str, Image.Image]] = {}
    for name in ["chart", "comic-shading"]:
        processed = args.output / (name + "-processed")
        subprocess.run([str(args.processor.resolve()), str(source_dir / (name + ".png")),
                        str(width), str(height), str(processed), "1"], check=True, capture_output=True)
        outputs[name] = {}
        for method in ["bayer8", "fs_q8"]:
            pixels = (processed / (method + ".raw")).read_bytes()
            assert len(pixels) == width * height
            assert all(value % 17 == 0 for value in set(pixels))
            outputs[name][method] = Image.frombytes("L", (width, height), pixels)

    def label(image: Image.Image, method: str, subject: str, pair: int) -> Image.Image:
        image = image.copy()
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width - 1, 200), fill=255)
        draw.rectangle((0, height - 115, width - 1, height - 1), fill=255)
        name = "A · 8×8 Bayer" if method == "bayer8" else "B · Floyd–Steinberg Q8"
        draw.text((80, 35), name, fill=0, font=font(64, True))
        draw.text((80, 126), f"{subject}　{pair}/3　右侧下一页，左侧上一页", fill=0, font=font(38))
        draw.text((80, height - 90), "同图同尺寸 · 每页全刷 · 关闭额外抖动与缩放", fill=0, font=font(35))
        return palette(image)

    guide = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(guide)
    draw.text((100, 105), "灰阶算法 A/B 对照", fill=0, font=font(76, True))
    lines = [
        "A：8×8 Bayer　B：高精度 Floyd–Steinberg",
        "相邻两页是同一张图，只有抖动算法不同。",
        "第 2–3 页：灰阶与渐变。",
        "第 4–5 页：合成连续阴影漫画测试图。",
        "第 6–7 页：网点放大 8 倍，观察排列纹理。",
        "点右侧下一页，点左侧上一页；中间打开菜单。",
        "先以正常阅读距离比较，再靠近观察网点。",
        "重点看平滑阴影、暗部、网格感和颗粒感。",
        "测试图与屏幕尺寸一致，已设置每页全刷。",
        "请不要另开 Y4、额外抖动、裁边或快速模式。",
        "这套测试使用独立数据，不改变平时阅读设置。",
        "每组都能表达 256 个目标灰度；",
        "误差数值更小，不等于肉眼效果提升同样倍数。",
    ]
    for index, text in enumerate(lines):
        draw.text((100, 275 + index * 125), text, fill=0, font=font(42))
    draw.text((100, 2130), "漫画页保留示例线稿，并加入合成连续明暗。", fill=0, font=font(36))
    draw.text((100, 2190), "放大页用于看网点，不代表正常阅读大小。", fill=0, font=font(36))
    pages = [("guide", "guide", palette(guide))]
    for pair, name, subject in [(1, "chart", "灰阶与渐变"), (2, "comic-shading", "合成连续阴影漫画")]:
        for method in ["bayer8", "fs_q8"]:
            pages.append((name, method, label(outputs[name][method], method, subject, pair)))
    for method in ["bayer8", "fs_q8"]:
        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)
        for row, tone in enumerate([32, 128, 224]):
            x = 160 + (tone % 16) * 96 + 16
            y = 256 + (tone // 16) * 96 + 16
            patch = outputs["chart"][method].crop((x, y, x + 64, y + 64))
            image.paste(patch.resize((512, 512), Image.Resampling.NEAREST), (130, 270 + row * 680))
            draw.text((730, 400 + row * 680), f"目标灰度 {tone}", fill=0, font=font(54, True))
            draw.text((730, 490 + row * 680), "同一块区域的原样网点", fill=0, font=font(40))
        # Enlarge each 64x64 sample by 8x so single display pixels are obvious.
        pages.append(("texture", method, label(image, method, "网点放大 8 倍", 3)))
    records = []
    archive = args.output / "灰阶对照.cbz"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for index, (subject, method, image) in enumerate(pages, 1):
            target = pages_dir / f"{index:02d}.png"
            image.save(target)
            content = target.read_bytes()
            zipped.writestr(target.name, content)
            records.append({"page": index, "subject": subject, "method": method,
                            "sha256": hashlib.sha256(content).hexdigest(), "size": [width, height]})
    manifest = {"pages": records, "archive": archive.name,
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "source_chart_sha256": hashlib.sha256(args.chart.read_bytes()).hexdigest(),
                "demo_art_sha256": hashlib.sha256((root / "assets/demo/page-01.png").read_bytes()).hexdigest(),
                "comic_scope": "Existing illustrative demo line art with a synthetic continuous lighting field; not a real manga quality ranking."}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "pages": len(records), "archive_bytes": archive.stat().st_size}))


if __name__ == "__main__":
    main()
