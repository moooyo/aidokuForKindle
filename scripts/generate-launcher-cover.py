#!/usr/bin/env python3
"""Render the original monochrome Library cover on the Linux verification host."""

from __future__ import annotations

import argparse
import gzip
import io
from pathlib import Path
import sys


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
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800" viewBox="0 0 600 800" role="img" aria-labelledby="title description">
<title id="title">Aidoku</title>
<desc id="description">An open manga book in a black square, above the Aidoku name.</desc>
<rect width="600" height="800" fill="#fff"/>
<rect x="20" y="20" width="560" height="760" rx="4" fill="none" stroke="#000" stroke-width="3"/>
<rect x="80" y="80" width="440" height="440" rx="54" fill="#000"/>
<path d="M150 204 C202 189 252 208 300 242 C348 208 398 189 450 204 L450 407 C398 392 348 411 300 444 C252 411 202 392 150 407 Z" fill="#fff"/>
<path d="M300 245 V431" fill="none" stroke="#000" stroke-width="12" stroke-linecap="round"/>
<path d="M176 232 C209 228 241 239 274 259 V324 C241 304 209 293 176 297 Z" fill="#000"/>
<circle cx="201" cy="254" r="11" fill="#fff"/>
<path d="M176 327 C209 323 241 334 274 354 M176 356 C209 352 232 360 254 371" fill="none" stroke="#000" stroke-width="10" stroke-linecap="round"/>
<path d="M326 260 C359 240 391 229 424 233 M326 289 C359 269 382 261 405 260" fill="none" stroke="#000" stroke-width="10" stroke-linecap="round"/>
<path d="M326 321 C359 301 391 290 424 294 V377 C391 373 359 384 326 404 Z" fill="#000"/>
<path d="M344 367 L363 335 L380 350 L402 320 L410 356 C383 356 362 361 344 367 Z" fill="#fff"/>
<g fill="#000">{title}
{subtitle}</g>
</svg>
'''
    output = arguments.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "aidoku-cover.svg").write_text(svg, encoding="utf-8", newline="\n")
    rendered = cairosvg.svg2png(bytestring=svg.encode(), output_width=1200, output_height=1600)
    image = Image.open(io.BytesIO(rendered)).convert("L")
    image = image.resize((600, 800), Image.Resampling.LANCZOS)
    image.save(output / "aidoku-cover.png", optimize=True)
    print(output / "aidoku-cover.png")


if __name__ == "__main__":
    main()
