#!/usr/bin/env python3
"""Exercise the reading follow-up on the designated remote Linux worker."""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import sys
import zipfile

from PIL import Image, ImageStat


def load_harness():
    path = Path(__file__).with_name("verify-ui.py")
    spec = importlib.util.spec_from_file_location("aidoku_remote_ui", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def center_mean(path: str) -> float:
    with Image.open(path) as image:
        width, height = image.size
        center = image.convert("L").crop((width // 3, height // 3, width * 2 // 3, height * 2 // 3))
        return ImageStat.Stat(center).mean[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if os.name == "nt" or not sys.platform.startswith("linux"):
        parser.error("Run through ssh test-env on the designated Linux worker.")
    if args.output_dir.exists():
        parser.error("Use a fresh output directory to preserve previous results.")
    harness = load_harness()
    suite = harness.Suite(args)
    Replay = harness.Replay

    for locale, label in [("zh-CN", "书架"), ("zh-TW", "書櫃"), ("en", "Library"), ("ja", "本棚")]:
        case = suite.run(
            f"settings-language-{locale}", "settings", device="pw6",
            replay=Replay().intent("settings", "set", f"language={locale}").intent("settings", "open", "language"),
            expected_route="settings",
        )
        suite.preference(case, "language", locale)
        suite.expect(case, "interface_language", locale)
        suite.expect(case, "interface_library_label", label)

    downloads = suite.run(
        "scribe-download-preparation", "settings", device="scribe",
        replay=Replay().intent("settings", "set", "resize-downloads=true")
        .intent("settings", "set", "grayscale-downloads=true")
        .intent("settings", "set", "ahead-chapters=2").intent("settings", "open", "downloads"),
        expected_route="settings",
    )
    for key, value in [("resize_downloads", True), ("grayscale_downloads", True), ("ahead_chapters", 2)]:
        suite.preference(downloads, key, value)

    category_replay = Replay().intent("settings", "open", "categories")
    category_replay.intent("settings", "category-create").intent("keyboard", "mode", "english")
    for character in "Archive":
        category_replay.intent("keyboard", "key", character, wait=80)
    category_replay.intent("keyboard", "done")
    categories = suite.run(
        "categories-create", "settings", demo=False,
        replay=category_replay,
        expected_route="settings",
    )
    if categories.get("state", {}).get("database"):
        with sqlite3.connect(categories["state"]["database"]) as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM categories")]
        suite.check(categories, "The keyboard command persisted a custom category", "Archive" in names, names)

    backup = suite.run(
        "metadata-backup", "settings", demo=False,
        replay=Replay().intent("settings", "set", "write-logs=true")
        .intent("settings", "backup-create", wait=1000).intent("settings", "open", "backup"),
        expected_route="settings", capture_delay=1000,
    )
    if backup.get("state", {}).get("database"):
        root = Path(backup["state"]["database"]).parent
        snapshots = list((root / "backups").glob("*.json"))
        suite.check(backup, "The backup button created a local snapshot manifest", bool(snapshots), [path.name for path in snapshots])

    archive = suite.output / "wide-pages.cbz"
    wide = Image.new("L", (1600, 800), 40)
    wide.paste(200, (800, 0, 1600, 800))
    wide.paste(20, (1420, 100, 1480, 700))
    portrait = Image.new("L", (600, 900), 110)
    portrait.paste(20, (100, 100, 500, 700))
    with zipfile.ZipFile(archive, "w") as package:
        for index, image in enumerate([wide, portrait]):
            encoded = io.BytesIO()
            image.save(encoded, format="PNG")
            package.writestr(f"{index + 1:03}.png", encoded.getvalue())
    imported = suite.run("wide-import", demo=False, import_path=archive, capture_delay=1500)
    data = Path(imported["data_dir"])
    suite.run(
        "wide-options", "settings", demo=False, data=data,
        replay=Replay().intent("settings", "set", "split-wide=true")
        .intent("settings", "set", "page-layout-mode=single")
        .intent("settings", "set", "crop-borders=false"),
        expected_route="settings",
    )
    early = suite.run(
        "wide-early-next", "reader", demo=False, data=data,
        replay=Replay(start=50).intent("reader", "next", wait=500),
        expected_route="reader", capture_delay=12000,
    )
    suite.expect(early, "reader_page", 1)
    suite.expect(early, "reader_wide_page", True)
    suite.expect(early, "reader_wide_half", 1)
    suite.expect(early, "pending_wide_navigation", 0)
    first = suite.run(
        "wide-first-half", "reader", demo=False, data=data,
        expected_route="reader", capture_delay=5000,
    )
    suite.expect(first, "reader_page", 1)
    suite.expect(first, "reader_wide_page", True)
    suite.expect(first, "reader_wide_half", 0)
    second = suite.run(
        "wide-second-half", "reader", demo=False, data=data,
        replay=Replay(start=2000).intent("reader", "next", wait=1000),
        expected_route="reader", capture_delay=1500,
    )
    suite.expect(second, "reader_page", 1)
    suite.expect(second, "reader_wide_half", 1)
    if first["passed"] and second["passed"]:
        difference = center_mean(first["screenshot"]) - center_mean(second["screenshot"])
        suite.check(second, "RTL halves display different source pixels in the right order", difference > 80, difference)
    resumed = suite.run("wide-resume", "library", demo=False, data=data, resume=True, expected_route="reader", capture_delay=2000)
    suite.expect(resumed, "reader_page", 1)
    suite.expect(resumed, "reader_wide_page", True)
    suite.expect(resumed, "reader_wide_half", 1)
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
