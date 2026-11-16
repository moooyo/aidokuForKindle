#!/usr/bin/env python3
"""Exercise hardware settings through actual desktop hit areas on test-env."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def harness():
    path = Path(__file__).with_name("verify-ui.py")
    spec = importlib.util.spec_from_file_location("media_settings_ui", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main():
    if sys.platform != "linux":
        raise SystemExit("Run verification through ssh test-env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--geometry-only", action="store_true")
    args = parser.parse_args()
    ui = harness()
    suite = ui.Suite(SimpleNamespace(binary=args.binary, output_dir=args.output_dir,
        timeout=40, performance_traces=False, selected_cases=["media-settings"]))
    for profile in ["pw5", "scribe"]:
        large = profile == "scribe"
        content_x = 360 if large else 28
        content_y = 64 if large else 108
        content_width = 530 if large else 562
        dither_height = 79 if large else 70
        touch = 56 if large else 48
        if args.geometry_only:
            case = suite.run(profile + "-hardware-portrait-reentry", "settings", device=profile,
                landscape=True, expected_landscape=False,
                replay=ui.Replay(start=1400).intent("settings", "open", "hardware-acceleration"),
                expected_route="settings", capture_delay=800)
            suite.expect(case, "hardware_acceleration", False)
            continue
        replay = ui.Replay(start=1400).intent("settings", "open", "hardware-acceleration")
        replay.tap(content_x + content_width / 2, content_y + (29 if large else 26) + (25 if large else 22))
        replay.tap(content_x + content_width - 28, content_y + dither_height + 24 + touch / 2)
        case = suite.run(profile + "-hardware-disabled", "settings", device=profile,
            replay=replay, expected_route="settings", capture_delay=900)
        suite.expect(case, "settings_section", "hardware-acceleration")
        suite.expect(case, "hardware_media_available", False)
        suite.expect(case, "hardware_acceleration", False)
        suite.preference(case, "hardware_acceleration", False)
        suite.preference(case, "dither_method", "software")
        suite.check(case, "Disabled controls did not reach a hardware decoder",
            case["state"].get("native_media", {}).get("attempts") == 0)
        for locale, night in [("en", True), ("ja", False), ("zh-TW", False)]:
            replay = ui.Replay(start=1400).intent("settings", "set", "language=" + locale)
            if night: replay.intent("settings", "set", "night-mode=true")
            replay.intent("settings", "open", "hardware-acceleration")
            case = suite.run(profile + "-hardware-" + locale, "settings", device=profile,
                replay=replay, expected_route="settings", capture_delay=800)
            suite.expect(case, "hardware_media_available", False)
            suite.preference(case, "language", locale)
        landscape = suite.run(profile + "-hardware-portrait-reentry", "settings", device=profile,
            landscape=True, expected_landscape=False, replay=ui.Replay(start=1400).intent("settings", "open", "hardware-acceleration"),
            expected_route="settings", capture_delay=800)
        suite.expect(landscape, "hardware_acceleration", False)
    status = suite.finish()
    report_path = args.output_dir / "report.json"
    report = json.loads(report_path.read_text())
    report["evidence_scope"] = "Desktop Slint controls, disabled hit areas, localization and layout. No physical hardware support is inferred."
    report_path.write_text(json.dumps(report, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
