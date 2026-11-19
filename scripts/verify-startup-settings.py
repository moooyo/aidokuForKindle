#!/usr/bin/env python3
"""Exercise the startup controls with real Slint hit areas and isolated data on test-env."""

import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

from PIL import Image


# File values form a contract with the native launcher. Recommended positions
# are discovered through the first read-only visit, not copied from the UI.
SWITCHES = {
    "portrait": ("shell", "native"),
    "prewarm": ("0", "2"),
    "last_cover_grace": ("off", "on"),
    "import_thumbnail": ("off", "idle"),
    "first_layout": ("baseline", "optimized"),
    "resident": ("off", "45"),
}


def load_harness():
    path = Path(__file__).with_name("verify-ui.py")
    spec = importlib.util.spec_from_file_location("startup_settings_ui", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def application_preferences(case):
    db_path = Path(case["state"]["database"])
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        return db.execute("SELECT key,document FROM preferences WHERE key='application'").fetchall()


def positions(case):
    return {row["id"]: row["value"] for row in case.get("state", {}).get("settings_details", [])}


def verify_profile(ui, suite, device):
    large = device == "scribe"
    width, height = ui.PROFILES[device]
    margin, touch, status, tab, row = (40, 56, 36, 80, 60) if large else (28, 48, 28, 72, 54)
    panel_top = status + (28 if large else touch + 32)
    rows_top = panel_top + touch
    rows_y = [rows_top + row * (index + 0.5) for index in range(len(SWITCHES))]
    nav_y = height - tab / 2
    data = suite.fresh(device)
    policy = data / "demo" / "startup-options"

    def replay():
        return ui.Replay(start=1550)

    def run(name, events=None, screen="settings", route="settings"):
        return suite.run(device + "-startup-" + name, screen, data=data, device=device,
                         demo=True, offline=True, replay=events, expected_route=route,
                         capture_delay=950)

    def check_panel(case, expected, stored, active=None):
        suite.expect(case, "settings_section", "startup-options")
        suite.expect(case, "quick_return_available", False)
        active = expected if active is None else active
        suite.expect(case, "active_startup_options",
                     {key: SWITCHES[key][value == "on"] for key, value in active.items()})
        suite.check(case, "Six independently labelled switches appear in order",
                    list(positions(case)) == list(SWITCHES) and all(
                        row["enabled"] and row["title"].strip() for row in case["state"]["settings_details"]),
                    list(positions(case)))
        suite.check(case, "Each switch shows the expected saved position", positions(case) == expected,
                    positions(case))
        if set(positions(case)) == set(SWITCHES):
            with Image.open(suite.output / (case["name"] + ".png")) as image:
                image = image.convert("L")
                # Sample the inside of the switch border, beyond both thumb positions.
                rendered = {key: "on" if image.getpixel((width - margin - 5, int(y))) < 128 else "off"
                            for key, y in zip(SWITCHES, rows_y)}
            suite.check(case, "Rendered ink and paper track fills agree with all six saved positions",
                        rendered == expected, rendered)
        suite.check(case, "The ordinary application preferences remain unchanged",
                    case["state"].get("preferences") == original_prefs and
                    application_preferences(case) == original_document)
        suite.check(case, "Demo controls do not write to the real application's policy",
                    not (data / "startup-options").exists())
        if stored:
            pairs = dict(line.split("=", 1) for line in policy.read_text(encoding="ascii").splitlines()) if policy.is_file() else {}
            wanted = {key: SWITCHES[key][value == "on"] for key, value in expected.items()}
            suite.check(case, "The actual launcher-readable policy matches every UI position", pairs == wanted, pairs)
        else:
            suite.check(case, "Opening the panel does not create or rewrite startup options", not policy.exists())

    def enter_settings():
        return replay().tap(width * 0.9, nav_y)

    root = run("settings-entry", enter_settings(), screen="library")
    entry_ids = root.get("state", {}).get("visible_setting_ids", [])
    suite.check(root, "Startup settings are reachable from the library's Settings tab",
                "startup-options" in entry_ids, entry_ids)
    if not root["passed"]:
        return
    original_prefs = root["state"]["preferences"]
    original_document = application_preferences(root)
    # Four group headings and eleven single-height rows precede the fixed Exit control.
    entry_y = status + 8 + 4 * (40 if large else 30) + (11 - 0.5) * touch
    opened = run("opened", enter_settings().tap(140, entry_y), screen="library")
    recommendations = positions(opened)
    check_panel(opened, recommendations, False)
    if set(recommendations) != set(SWITCHES):
        return

    content_left = 320 + margin if large else margin
    content_width = width - margin - content_left
    presets_y = rows_top + len(SWITCHES) * row + 16 + touch / 2
    all_x, recommended_x = content_left + content_width / 4, content_left + content_width * 3 / 4
    expected = dict.fromkeys(SWITCHES, "on")
    enabled = run("enable-all", replay().tap(all_x, presets_y))
    check_panel(enabled, expected, True, active=recommendations)
    reopened = run("reopened", enter_settings(), screen="library")
    check_panel(reopened, expected, True)

    for key, y in zip(SWITCHES, rows_y):
        previous = expected.copy()
        expected[key] = "off"
        case = run("toggle-" + key, replay().tap(width - margin - touch / 2, y))
        check_panel(case, expected, True, active=previous)
    restored = run("recommended", replay().tap(recommended_x, presets_y))
    check_panel(restored, recommendations, True, active=expected)

    # Desktop cannot acquire the formal Kindle display/input handoff, even
    # when the user has saved the 45-second quick-return preference.
    enabled_resident = run("request-quick-return", replay().tap(all_x, presets_y))
    check_panel(enabled_resident, dict.fromkeys(SWITCHES, "on"), True, active=recommendations)
    menu = run("ineligible-menu", replay().tap(width - margin - touch / 2, status + touch / 2),
               screen="library", route="library")
    suite.expect(menu, "modal_context", "library-more")
    suite.check(menu, "An ineligible desktop launch does not offer a native Quick return",
                "quick-return" not in menu["state"].get("modal_item_ids", []))

    for locale, night in ([("ja", False)] if large else [("en", True), ("zh-TW", False)]):
        events = replay().intent("settings", "set", "language=" + locale)
        events.intent("settings", "set", "night-mode=" + str(night).lower())
        localized = run("render-" + locale, events.intent("settings", "open", "startup-options"))
        catalog = Path(__file__).resolve().parents[1] / "assets" / "i18n" / (locale + ".json")
        labels = json.loads(catalog.read_text(encoding="utf-8"))
        expected_labels = [labels["startup_" + suffix] for suffix in ("native_portrait", "cached_covers",
                           "wait_cover", "import_covers", "library_layout", "quick_reopen")]
        actual_labels = [s["title"] for s in localized["state"].get("settings_details", [])]
        suite.expect(localized, "interface_language", locale)
        suite.check(localized, "All six switch labels use the selected language",
                    actual_labels == expected_labels, actual_labels)
        suite.preference(localized, "night_mode", night)


def main():
    if sys.platform != "linux":
        raise SystemExit("Run verification through ssh test-env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    ui = load_harness()
    suite = ui.Suite(SimpleNamespace(binary=args.binary, output_dir=args.output_dir, timeout=40,
                                     performance_traces=False, selected_cases=["startup-settings"]))
    for device in ["pw5", "scribe"]:
        verify_profile(ui, suite, device)
    status = suite.finish()
    ui.save_json(suite.output / "startup-settings.json", {
        "scope": "Desktop PW5 and Scribe Slint hit areas, rendered switch positions, local persistence, and normal preference isolation; physical quick return is qualified separately.",
        "binary_sha256": suite.binary_hash,
        "cases": [{"name": case["name"], "passed": case["passed"], "checks": len(case["checks"]),
                   "failed": [c for c in case["checks"] if not c["passed"]]} for case in suite.cases],
    })
    return status


if __name__ == "__main__":
    raise SystemExit(main())
