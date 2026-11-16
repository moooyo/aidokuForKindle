#!/usr/bin/env python3
"""Verify software dithering controls and flat press feedback on test-env."""

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
    parser.add_argument("--layout-only", action="store_true")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("spatial_ui_harness", Path(__file__).with_name("verify-ui.py"))
    ui = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ui
    spec.loader.exec_module(ui)
    suite = ui.Suite(SimpleNamespace(binary=args.binary, output_dir=args.output_dir,
        timeout=40, performance_traces=False, selected_cases=["spatial-dithering", "button-feedback"]))

    for profile in ["pw5", "scribe"]:
        width, height = ui.PROFILES[profile]
        large = profile == "scribe"
        left, top = (360, 64) if large else (28, 108)
        content_width = width - left - (40 if large else 28)
        choice_y = top + (368 if large else 336) + 24 + (29 if large else 26)
        choice_height = 56 if large else 48
        base = suite.run(profile + "-S04-layout", "settings", device=profile,
            replay=ui.Replay(start=1400).intent("settings", "open", "screen-refresh"), expected_route="settings")
        suite.preference(base, "dither_method", "bayer8")
        if args.layout_only:
            continue
        for index, method in enumerate(["off", "bayer8", "floyd_steinberg"]):
            replay = ui.Replay(start=1400).intent("settings", "open", "screen-refresh")
            choice_x = left + content_width / 2 if large else left + index * (content_width + 8) / 3 + (content_width - 16) / 6
            replay.tap(choice_x, choice_y + (index * (choice_height + 8) if large else 0) + choice_height / 2)
            case = suite.run(profile + "-S04-" + method, "settings", device=profile,
                replay=replay, expected_route="settings", capture_delay=1100)
            suite.preference(case, "dither", method != "off")
            suite.preference(case, "dither_method", "bayer8" if method == "off" else method)
            preview_y = top + (368 + 24 + 281 + 24 if large else 336 + 24 + 126 + 16) + 41
            preview_height = 54 if large else 46
            with Image.open(case["screenshot"]) as capture:
                preview = capture.convert("L").crop((left + 1, preview_y,
                    left + content_width - 1, preview_y + preview_height))
                pixels = list(preview.getdata())
                palette = sorted(set(pixels))
                rows = [pixels[y * preview.width:(y + 1) * preview.width] for y in range(preview.height)]
                textured = sum(len({row[x] for row in rows}) > 1 for x in range(preview.width))
                suite.check(case, "The complete test strip is visible above navigation", preview_y + preview_height + 32 < height - (80 if large else 72))
                suite.check(case, "The preview contains only the sixteen native output codes", palette == list(range(0, 256, 17)), palette)
                suite.check(case, "The selected algorithm changes the test strip texture", textured == 0 if method == "off" else textured > preview.width // 4, textured)
            if method != "bayer8":
                restart = ui.Replay(start=1400).intent("settings", "open", "screen-refresh")
                cold = suite.run(profile + "-restart-" + method, "settings", device=profile,
                    data=Path(case["data_dir"]), replay=restart, expected_route="settings")
                suite.preference(cold, "dither", method != "off")
                suite.preference(cold, "dither_method", "bayer8" if method == "off" else method)

        # A selected display button must keep its fill throughout a held press.
        mode_top = top + (144 if large else 128) + 36
        button_width = (content_width - 8) / 2
        selected_x = left + button_width + 8
        selected_box = tuple(map(int, (selected_x + 2, mode_top + 2,
            selected_x + button_width - 2, mode_top + choice_height - 2)))
        press = ui.Replay(start=1400).intent("settings", "open", "screen-refresh")
        point = f"{selected_x + button_width / 2:g},{mode_top + choice_height / 2:g}"
        press.event("pointer", "move", point).event("pointer", "press", point, 100)
        held = suite.run(profile + "-selected-button-held", "settings", device=profile,
            replay=press, expected_route="settings", capture_delay=450)
        with Image.open(base["screenshot"]) as before, Image.open(held["screenshot"]) as after:
            difference = ImageChops.difference(before.convert("RGB").crop(selected_box), after.convert("RGB").crop(selected_box))
            suite.check(held, "Selected control does not reverse its colors while pressed", difference.getbbox() is None)
        press.event("pointer", "cancel", "", 300)
        cancelled = suite.run(profile + "-selected-button-cancelled", "settings", device=profile,
            replay=press, expected_route="settings")
        suite.preference(cancelled, "display_mode", "normal")

        # Two-state switches keep their label and thumb colors while held.
        light = ui.Replay(start=1400).intent("settings", "open", "frontlight-warmth")
        released = suite.run(profile + "-switch-released", "settings", device=profile,
            replay=light, expected_route="settings")
        toggle_y = top + (288 if large else 246)
        point = f"{left + content_width / 2:g},{toggle_y + choice_height / 2:g}"
        light.event("pointer", "move", point).event("pointer", "press", point, 100)
        held = suite.run(profile + "-switch-held", "settings", device=profile,
            replay=light, expected_route="settings", capture_delay=450)
        track_width = 56 if large else 50
        track_box = tuple(map(int, (left + content_width - track_width, toggle_y,
            left + content_width, toggle_y + choice_height)))
        with Image.open(released["screenshot"]) as before, Image.open(held["screenshot"]) as after:
            # Compare the track itself, excluding the row's neutral press fill.
            track_y = (choice_height - (32 if large else 30)) // 2
            rect = (track_box[0], track_box[1] + track_y, track_box[2], track_box[1] + track_y + (32 if large else 30))
            difference = ImageChops.difference(before.convert("RGB").crop(rect), after.convert("RGB").crop(rect))
            suite.check(held, "A held switch does not reverse its thumb or track", difference.getbbox() is None)
        suite.preference(held, "night_mode", False)
        light.event("pointer", "cancel", "", 300)
        cancelled = suite.run(profile + "-switch-cancelled", "settings", device=profile,
            replay=light, expected_route="settings")
        suite.preference(cancelled, "night_mode", False)

        for language in ["en", "ja", "zh-TW"]:
            localized = ui.Replay(start=1400).intent("settings", "set", "language=" + language)
            localized.intent("settings", "set", "dither-mode=floyd_steinberg")
            localized.intent("settings", "set", "night-mode=true")
            localized.intent("settings", "open", "screen-refresh")
            case = suite.run(profile + "-S04-night-" + language, "settings", device=profile,
                replay=localized, expected_route="settings", capture_delay=1100)
            suite.preference(case, "dither_method", "floyd_steinberg")
            suite.preference(case, "night_mode", True)
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
