#!/usr/bin/env python3
"""Verify display presets through real Slint hit areas on ssh test-env.

The snapshots cover layout, persistence and retained-page state. They do not
establish physical waveform, animation or dithering acceptance.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def harness():
    path = Path(__file__).with_name("verify-ui.py")
    spec = importlib.util.spec_from_file_location("aidoku_display_ui_harness", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("The adjacent UI harness is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODES = ("fast", "normal", "high_quality", "kindle")


def center(profile: str, surface: str, mode: str, landscape: bool = False):
    large = profile == "scribe"
    width, height = (930, 1240) if large else (618, 824)
    if landscape:
        width, height = height, width
    margin, touch = (40, 56) if large else (28, 48)
    if surface == "settings":
        x = (320 if large else 0) + margin
        y = (36 + 28) if large else (28 + touch + 32)
        available = width - (320 if large else 0) - margin * 2
    else:
        x = margin
        y = height - (72 if large else 64) - (368 if large else 336) - 24
        available = width - margin * 2
    index = MODES.index(mode)
    return (x + (index % 2) * (available + 8) / 2 + (available - 8) / 4,
            y + (144 if large else 128) + 36 + (index // 2) * (touch + 8) + touch / 2)


def main():
    if sys.platform != "linux":
        raise SystemExit("Run verification through ssh test-env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    ui = harness()
    suite = ui.Suite(SimpleNamespace(binary=args.binary, output_dir=args.output_dir,
        timeout=30, performance_traces=False, selected_cases=["display-modes"]))
    for profile in ("pw5", "scribe"):
        saved = None
        surfaces = ("settings",) if args.quick else ("settings", "reader")
        for surface in surfaces:
            for mode in MODES:
                replay = ui.Replay(start=4500 if surface == "reader" else 1300)
                if mode == "normal":
                    replay.intent("settings", "set", "display-mode=high_quality", wait=700)
                if surface == "settings":
                    replay.intent("settings", "open", "screen-refresh")
                else:
                    replay.intent("reader", "menu").intent("reader", "picture")
                replay.tap(*center(profile, surface, mode), wait=700)
                case = suite.run(f"{profile}-{surface}-{mode}", surface, device=profile,
                    replay=replay, expected_route=surface, capture_delay=1600)
                suite.preference(case, "display_mode", mode)
                suite.expect(case, "display_mode", mode)
                if surface == "reader":
                    suite.expect(case, "display_mode_presented", mode)
                    suite.expect(case, "reader_fast_frame", mode == "fast")
                    suite.expect(case, "modal_context", "reader-picture")
                else:
                    suite.expect(case, "settings_section", "screen-refresh")
                    saved = Path(case["data_dir"])
        cold = suite.run(f"{profile}-cold-persistence", "settings", device=profile, data=saved,
            replay=ui.Replay(start=1200).intent("settings", "open", "screen-refresh"), expected_route="settings")
        suite.preference(cold, "display_mode", "kindle")
        if not args.quick:
            replay = ui.Replay(start=4500).intent("reader", "menu").intent("reader", "picture")
            replay.tap(*center(profile, "reader", "fast", True), wait=700)
            landscape = suite.run(f"{profile}-landscape-fast", "reader", device=profile,
                landscape=True, replay=replay, expected_route="reader")
            suite.preference(landscape, "display_mode", "fast")
            replay = ui.Replay(start=1300).intent("settings", "set", "language=en")
            replay.intent("settings", "set", "night-mode=true").intent("settings", "open", "screen-refresh")
            replay.tap(*center(profile, "settings", "high_quality"), wait=700)
            night = suite.run(f"{profile}-night-english", "settings", device=profile,
                replay=replay, expected_route="settings")
            suite.preference(night, "display_mode", "high_quality")
            suite.preference(night, "night_mode", True)
            replay = ui.Replay(start=1300).intent("settings", "set", "language=ja")
            replay.intent("settings", "open", "screen-refresh").tap(*center(profile, "settings", "kindle"), wait=700)
            japanese = suite.run(f"{profile}-japanese", "settings", device=profile,
                replay=replay, expected_route="settings")
            suite.preference(japanese, "display_mode", "kindle")
    result = suite.finish()
    report_path = args.output_dir / "report.json"
    report = json.loads(report_path.read_text())
    report["kind"] = "reader-display-mode-ui"
    report["evidence_scope"] = "Preset hit areas, persistence, retained-page state, and desktop layouts; no physical display claims."
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
