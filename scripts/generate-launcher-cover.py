#!/usr/bin/env python3
"""Render the official-logo Library cover on the Linux verification host."""

from __future__ import annotations

import argparse
import gzip
import io
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def outlined_label(value: str, size: int, baseline: int, weight: str, tracking: int = 0) -> str:
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    source = ROOT / "assets/fonts" / f"NotoSansSC-{weight}.otf.gz"
    font = TTFont(io.BytesIO(gzip.decompress(source.read_bytes())))
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = size / font["head"].unitsPerEm
    advances = [glyphs[cmap[ord(character)]].width * scale for character in value]
    x = (600 - sum(advances) - tracking * (len(value) - 1)) / 2
    paths = []
    for character, advance in zip(value, advances):
        pen = SVGPathPen(glyphs)
        glyphs[cmap[ord(character)]].draw(pen)
        paths.append(
            f'<path transform="translate({x:.4f} {baseline}) scale({scale:.6f} {-scale:.6f})" '
            f'd="{pen.getCommands()}"/>'
        )
        x += advance + tracking
    return "\n".join(paths)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "assets/launcher")
    arguments = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Generate and inspect the cover through ssh test-env")
    import cairosvg
    from PIL import Image

    title = outlined_label("Aidoku", 88, 650, "Bold")
    subtitle = outlined_label("MANGA READER", 21, 710, "Medium", 4)
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    logo = ET.fromstring((ROOT / "assets/launcher/aidoku-official.svg").read_bytes())
    for element in logo.iter():
        if element.tag.rsplit("}", 1)[-1] not in {"svg", "g", "path"}:
            raise ValueError("The official logo contains an unexpected element")
        if any(key.lower().startswith("on") or key.rsplit("}", 1)[-1] == "href" for key in element.attrib):
            raise ValueError("The official logo must not contain external resources or events")
    if logo.attrib.get("viewBox") != "0 0 605 612":
        raise ValueError("The official logo has an unexpected view box")
    logo.attrib.update(x="80", y="80", width="440", height=f"{440 * 612 / 605:.6f}")
    artwork = ET.tostring(logo, encoding="unicode")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800" viewBox="0 0 600 800" role="img" aria-labelledby="title description">
<title id="title">Aidoku</title>
<desc id="description">The official Aidoku logomark, above the Aidoku name.</desc>
<rect width="600" height="800" fill="#fff"/>
<rect x="20" y="20" width="560" height="760" rx="4" fill="none" stroke="#000" stroke-width="3"/>
{artwork}
<g fill="#000">{title}
{subtitle}</g>
</svg>
'''
    output = arguments.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "aidoku-cover-v2.svg").write_text(svg, encoding="utf-8", newline="\n")
    rendered = cairosvg.svg2png(bytestring=svg.encode(), output_width=1200, output_height=1600)
    image = Image.open(io.BytesIO(rendered)).convert("L")
    image = image.resize((600, 800), Image.Resampling.LANCZOS)
    image.save(output / "aidoku-cover-v2.png", optimize=True)
    print(output / "aidoku-cover-v2.png")


if __name__ == "__main__":
    main()
