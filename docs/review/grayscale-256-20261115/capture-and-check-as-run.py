from pathlib import Path
from types import SimpleNamespace
import importlib.util
import json
import sys
from PIL import Image
import numpy as np

root = Path("/root/aidoku-display-modes-20261114-01a1da18")
source = root / "source"

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value

ui = module("grayscale_ui", source / "scripts/verify-ui.py")
modes = module("grayscale_modes", source / "scripts/verify-display-modes.py")
output = root / "reports/grayscale-256-ui"
suite = ui.Suite(SimpleNamespace(binary=root / "artifacts/aidoku-desktop", output_dir=output,
    timeout=35, performance_traces=False, selected_cases=["grayscale-256"]))
pixels = []
for profile, locale in [("pw5", "en"), ("pw6", "zh-TW"), ("scribe", "ja")]:
    large = profile == "scribe"
    for night in [False, True]:
        mode = "high_quality" if night else "normal"
        replay = ui.Replay(start=1400)
        if night:
            replay.intent("settings", "set", "language=" + locale)
            replay.intent("settings", "set", "night-mode=true")
        replay.intent("settings", "open", "screen-refresh")
        replay.tap(*modes.center("scribe" if large else "pw5", "settings", mode), wait=700)
        name = profile + "-grayscale-256-" + ("night" if night else "day")
        case = suite.run(name, "settings", device=profile, replay=replay,
                         expected_route="settings", capture_delay=900)
        suite.expect(case, "settings_section", "screen-refresh")
        suite.expect(case, "display_mode", mode)
        image = np.asarray(Image.open(output / (name + ".png")).convert("RGB"))
        width = image.shape[1]
        left = (320 if large else 0) + (40 if large else 28)
        content_top = 64 if large else 108
        bar_top = content_top + (368 if large else 336) + 24 + 40
        bar_width = width - (320 if large else 0) - (80 if large else 56)
        bar_height = 56 if large else 48
        interior = image[bar_top+1:bar_top+bar_height-1, left+1:left+bar_width-1]
        row = interior[len(interior)//2, :, 0]
        unique = np.unique(row)
        runs = row[np.r_[True, row[1:] != row[:-1]]]
        rgb_gray = bool(np.all(interior[:,:,0] == interior[:,:,1]) and np.all(interior[:,:,1] == interior[:,:,2]))
        flat_columns = bool(np.all(interior == interior[0:1, :, :]))
        complete = bool(np.array_equal(unique, np.arange(256)))
        monotonic = bool(np.array_equal(runs, np.arange(256)))
        suite.check(case, "All 256 grayscale input values are present in the actual rendered bar", complete, unique.tolist())
        suite.check(case, "The bar has exactly 256 ordered constant-gray swatches", monotonic and flat_columns and rgb_gray,
                    {"runs": len(runs), "flat_columns": flat_columns, "rgb_gray": rgb_gray})
        counts = np.bincount(row, minlength=256)
        pixels.append({"case": name, "profile": profile, "night": night,
                       "rectangle": [left+1, bar_top+1, bar_width-2, bar_height-2],
                       "distinct_values": unique.tolist(), "swatches": int(len(runs)),
                       "minimum_swatch_width": int(counts.min()), "maximum_swatch_width": int(counts.max()),
                       "all_rows_match": flat_columns, "rgb_gray": rgb_gray,
                       "ordered_0_to_255": monotonic})
for profile in ["pw5", "scribe"]:
    replay = ui.Replay(start=4500).intent("reader", "menu").intent("reader", "picture")
    replay.tap(*modes.center(profile, "reader", "high_quality"), wait=700)
    case = suite.run(profile + "-reader-panel", "reader", device=profile,
                     replay=replay, expected_route="reader", capture_delay=900)
    suite.expect(case, "display_mode", "high_quality")
    suite.expect(case, "modal_context", "reader-picture")
status = suite.finish()
report = json.loads((output / "report.json").read_text())
report["scope"] = "Actual desktop software-renderer pixels and controls. The settings ramp directly supplies 256 input gray codes. No native MDP/Y4 output or optical gray-level count is inferred."
(output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
(output / "pixel-values.json").write_text(json.dumps({"cases": pixels, "passed": status == 0}, indent=2) + "\n")
print(json.dumps({"passed": report["passed"], "failed": report["failed"],
                  "calibration_cases": len(pixels), "all_inputs": "0..255"}))
raise SystemExit(status)
