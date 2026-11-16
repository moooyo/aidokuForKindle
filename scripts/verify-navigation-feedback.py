#!/usr/bin/env python3
"""Verify Tab press, cancellation and selection using real Slint hit areas."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

from PIL import Image, ImageChops


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("navigation_ui_harness", Path(__file__).with_name("verify-ui.py"))
    ui = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ui
    spec.loader.exec_module(ui)
    suite = ui.Suite(SimpleNamespace(binary=args.binary, output_dir=args.output_dir,
        timeout=40, performance_traces=False, selected_cases=["navigation-feedback"]))

    for profile in ["pw5", "scribe"]:
        width, height = ui.PROFILES[profile]
        nav_height = 80 if profile == "scribe" else 72
        box = (0, height - nav_height, width, height)
        for night in [False, True]:
            prefix = profile + ("-night" if night else "-day")

            def replay():
                value = ui.Replay(start=1400)
                if night:
                    value.intent("settings", "set", "night-mode=true")
                return value

            base = suite.run(prefix + "-idle", "search", device=profile,
                replay=replay(), expected_route="search", capture_delay=900)
            point = f"{width * 0.9:g},{height - nav_height / 2:g}"
            press = replay().event("pointer", "move", point).event("pointer", "press", point, 100)
            held = suite.run(prefix + "-held", "search", device=profile,
                replay=press, expected_route="search", capture_delay=900)

            def unchanged(case):
                with Image.open(base["screenshot"]) as before, Image.open(case["screenshot"]) as after:
                    difference = ImageChops.difference(before.convert("RGB").crop(box), after.convert("RGB").crop(box))
                    suite.check(case, "Tab interaction has no transient fill or inverted pixels", difference.getbbox() is None)

            unchanged(held)
            if night:
                continue
            press.event("pointer", "cancel", "", 250)
            cancelled = suite.run(prefix + "-cancelled", "search", device=profile,
                replay=press, expected_route="search", capture_delay=900)
            unchanged(cancelled)
            released = suite.run(prefix + "-selected", "search", device=profile,
                replay=replay().tap(width * 0.9, height - nav_height / 2), expected_route="settings", capture_delay=900)
            with Image.open(released["screenshot"]) as picture:
                palette = picture.convert("RGB")
                backgrounds = [palette.getpixel((int(index * width / 5 + 5), height - nav_height + 12)) for index in range(5)]
                suite.check(released, "Selection keeps every Tab background on paper", all(pixel == (255, 255, 255) for pixel in backgrounds), backgrounds)
            repeated = suite.run(prefix + "-active-reselected", "search", device=profile,
                replay=replay().tap(width * 0.5, height - nav_height / 2), expected_route="search", capture_delay=900)
            unchanged(repeated)
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
