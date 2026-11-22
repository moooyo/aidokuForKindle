"""Build two six-book derivative fixture suites from an approved desktop binary."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from PIL import Image, ImageDraw


EXPECTED_BOOKS = 6
EXPECTED_WIDTH = 376
EXPECTED_HEIGHT = 514
RUN_SCREEN = "-screen 0 2200x3000x24"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate primary and secondary six-book fixtures, run the provided "
            "desktop binary for each suite, and export sorted processed PNG "
            "derivatives as 01.png..06.png."
        )
    )
    parser.add_argument(
        "primary_jpegs",
        type=Path,
        help="Directory containing approved 01.jpg..06.jpg source images.",
    )
    parser.add_argument(
        "desktop",
        type=Path,
        help="Retained aidoku-desktop ELF path used for fixture extraction.",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Nonexistent output directory to create for this run.",
    )
    return parser.parse_args()


def require_inputs(primary_jpegs: Path, desktop: Path, output: Path) -> None:
    if not primary_jpegs.is_dir():
        raise ValueError("The primary JPEG directory does not exist.")
    for index in range(1, EXPECTED_BOOKS + 1):
        source = primary_jpegs / f"{index:02d}.jpg"
        if not source.is_file():
            raise ValueError(f"Missing required source image: {source.name}")
    if not desktop.is_file():
        raise ValueError("The desktop binary path is not a regular file.")
    if output.exists():
        raise ValueError("The output directory must not already exist.")


def make_books_from_primary(source_dir: Path, books_dir: Path) -> None:
    for index in range(1, EXPECTED_BOOKS + 1):
        target = books_dir / f"Fixture-{index:02d}" / "001.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_dir / f"{index:02d}.jpg", target)


def make_secondary_books(books_dir: Path) -> None:
    width, height = 1010, 1440
    for index in range(1, EXPECTED_BOOKS + 1):
        mode = "RGB" if index in (3, 5) else "L"
        white = (255, 255, 255) if mode == "RGB" else 255
        image = Image.new(mode, (width, height), white)
        draw = ImageDraw.Draw(image)

        if index == 1:
            for y in range(60, 1390, 48):
                draw.rectangle((46, y, 956 - y % 143, y + 11), fill=0)
        elif index == 2:
            for y in range(40, 1420, 225):
                for x in range(25, 975, 323):
                    draw.rectangle((x, y, x + 300, y + 200), outline=0, width=14)
                    draw.ellipse((x + 44, y + 25, x + 245, y + 175), outline=60, width=8)
        elif index == 3:
            for y in range(height):
                draw.line(
                    (0, y, width - 1, y),
                    fill=(
                        255 * y // height,
                        255 - 255 * y // height,
                        min(255, 60 + 150 * y // height),
                    ),
                )
            draw.polygon([(100, 100), (930, 320), (570, 1260)], fill="#992c78")
            draw.ellipse((111, 360, 600, 860), outline="black", width=11)
        elif index == 4:
            for x in range(50, 950, 19):
                draw.line((x, 33, 970 - x, 1388), fill=x % 173, width=2)
        elif index == 5:
            for y in range(0, height, 35):
                for x in range(0, width, 35):
                    draw.rectangle(
                        (x, y, x + 34, y + 34),
                        fill=(
                            ((x // 35) * 19) % 128 + 64,
                            ((y // 35) * 11) % 190 + 20,
                            (x // 35 + y // 35) * 7 % 160 + 45,
                        ),
                    )
        elif index == 6:
            draw.rectangle((95, 110, 915, 1300), outline=12, width=4)
            draw.rectangle((230, 468, 750, 575), fill=12)
            draw.ellipse((860, 1250, 890, 1280), fill=90)
        else:
            raise AssertionError("Unsupported secondary fixture index.")

        target = books_dir / f"Fixture-{index:02d}" / "001.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="JPEG", quality=86, subsampling=1)


def run_suite(desktop: Path, books_dir: Path, data_dir: Path, state_path: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=False)
    command = [
        "xvfb-run",
        "-a",
        "-s",
        RUN_SCREEN,
        str(desktop),
        "--device",
        "scribe",
        "--offline",
        "--screen",
        "library",
        "--data-dir",
        str(data_dir),
        "--import",
        str(books_dir),
        "--exit-after-ms",
        "8000",
        "--state-output",
        str(state_path),
    ]
    subprocess.run(command, env=dict(os.environ, SLINT_BACKEND="winit-software"), check=True)


def require_state(state_path: Path) -> None:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("library_count") != EXPECTED_BOOKS:
        raise ValueError("State check failed: library_count must be 6.")
    if state.get("cover_visible_count") != EXPECTED_BOOKS:
        raise ValueError("State check failed: cover_visible_count must be 6.")
    if state.get("cover_visible_failure_count") != 0:
        raise ValueError("State check failed: visible cover failures must be 0.")
    if state.get("cover_pending_count") != 0:
        raise ValueError("State check failed: cover_pending_count must be 0.")
    if state.get("capture_projection_pending") is not False:
        raise ValueError("State check failed: capture_projection_pending must be false.")


def load_aidoku_render_chunk(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        chunk = image.info.get("AidokuRender")
        if chunk is None and hasattr(image, "text"):
            chunk = image.text.get("AidokuRender")
    if chunk is None:
        raise ValueError("Derivative PNG has no in-band AidokuRender metadata.")
    payload = str(chunk)
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("AidokuRender metadata is not valid JSON.") from error
    if not isinstance(value, dict):
        raise ValueError("AidokuRender metadata must be a JSON object.")
    return value


def require_render_metadata(metadata: dict[str, Any]) -> None:
    if metadata.get("version") != 1:
        raise ValueError("AidokuRender metadata version must be 1.")
    render_key = metadata.get("render_key")
    if not isinstance(render_key, dict):
        raise ValueError("AidokuRender metadata must include a render_key object.")

    expected = {
        "width": EXPECTED_WIDTH,
        "height": EXPECTED_HEIGHT,
        "crop_borders": False,
        "dither": True,
        "contrast": 0,
        "vertical": False,
        "offset": 0,
        "zoom_milli": 1000,
    }
    for field, value in expected.items():
        if render_key.get(field) != value:
            raise ValueError(f"render_key.{field} does not match the expected fixture target.")
    if render_key.get("hardware_dither", False):
        raise ValueError("render_key.hardware_dither must be false for software dither.")
    if render_key.get("spatial_dither") not in (None,):
        raise ValueError("render_key.spatial_dither must be absent or null.")
    if render_key.get("media_acceleration", "off") != "off":
        raise ValueError("render_key.media_acceleration must be off.")
    if not isinstance(render_key.get("source_key"), str) or not render_key["source_key"]:
        raise ValueError("render_key.source_key must be present and non-empty.")
    if metadata.get("scaled_height") != 0 or metadata.get("viewport_height") != 0:
        raise ValueError("Cover derivatives must keep scaled_height and viewport_height at 0.")


def collect_derivatives(data_dir: Path, target_dir: Path) -> None:
    processed_root = data_dir / "cache" / "shared-v1" / "processed"
    candidates = sorted(processed_root.glob("*/*.png"))
    if len(candidates) != EXPECTED_BOOKS:
        raise ValueError("Expected exactly 6 processed derivative PNG files.")

    target_dir.mkdir(parents=True, exist_ok=False)
    for index, source in enumerate(candidates, start=1):
        with Image.open(source) as image:
            if image.mode != "L":
                raise ValueError("Derivative PNG mode must be L.")
        metadata = load_aidoku_render_chunk(source)
        require_render_metadata(metadata)
        shutil.copyfile(source, target_dir / f"{index:02d}.png")


def build_suite(name: str, desktop: Path, output: Path, prepare_books: Any) -> None:
    suite = output / name
    books_dir = suite / "books"
    data_dir = suite / "data"
    state_path = suite / "state.json"
    exported = suite / "references"
    books_dir.mkdir(parents=True, exist_ok=False)
    prepare_books(books_dir)
    run_suite(desktop, books_dir, data_dir, state_path)
    require_state(state_path)
    collect_derivatives(data_dir, exported)


def main() -> None:
    args = parse_args()
    primary_jpegs = args.primary_jpegs.resolve()
    desktop = args.desktop.resolve()
    output = args.output.resolve()
    require_inputs(primary_jpegs, desktop, output)
    output.mkdir(parents=True, exist_ok=False)

    build_suite(
        "primary",
        desktop,
        output,
        lambda books_dir: make_books_from_primary(primary_jpegs, books_dir),
    )
    build_suite("secondary", desktop, output, make_secondary_books)

    manifest = {
        "primary": [f"{index:02d}.png" for index in range(1, EXPECTED_BOOKS + 1)],
        "secondary": [f"{index:02d}.png" for index in range(1, EXPECTED_BOOKS + 1)],
    }
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"fixture generation failed: {error}")
