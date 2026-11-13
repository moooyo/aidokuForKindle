#!/usr/bin/env python3
"""Check dither selection and software fallback on the remote desktop worker.

Run through ssh test-env, inside the caller's executable mount namespace when
needed. This focused runner reuses verify-ui.py for isolated application runs,
bounded process cleanup, captures, state sidecars, and reports. All selection
changes under test use real Slint pointer hit areas. Retained desktop captures
require visual review and do not establish native Y4 or physical e-ink acceptance.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
from types import SimpleNamespace
from typing import Any


GROUPS = {
    "pw5-portrait": {"device": "pw5", "landscape": False, "locale": "zh-CN"},
    "scribe-portrait": {"device": "scribe", "landscape": False, "locale": "zh-CN"},
    "scribe-portrait-en": {"device": "scribe", "landscape": False, "locale": "en"},
    "pw5-landscape": {"device": "pw5", "landscape": True, "locale": "en"},
}
DESKTOP_FALSE_FIELDS = (
    "hardware_y4_available",
    "reader_hardware_y4_requested",
    "reader_hardware_y4_effective",
    "dither_pending",
)
ANCHOR_FIELDS = (
    "current_book", "current_chapter", "reader_page", "reader_offset",
    "reader_zoom", "reader_wide_half", "visible_reader_page",
)
SESSION_FIELDS = ("book_id", "chapter_id", "page", "vertical_offset", "wide_half", "zoom")
MODE_INDEX = {"software": 0, "hardware": 1, "off": 2}


def load_harness() -> Any:
    path = Path(__file__).with_name("verify-ui.py")
    specification = importlib.util.spec_from_file_location("aidoku_dither_remote_ui", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("The adjacent remote UI harness could not be loaded")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def viewport(harness: Any, group: dict[str, Any]) -> tuple[int, int]:
    width, height = harness.PROFILES[group["device"]]
    return (height, width) if group["landscape"] else (width, height)


def selector_geometry(
    harness: Any, group: dict[str, Any], surface: str, *, hardware_selected: bool = False,
) -> dict[str, Any]:
    width, height = harness.PROFILES[group["device"]] if surface == "settings" else viewport(harness, group)
    large = group["device"].startswith("scribe")
    if surface == "settings":
        left = 356.0 if large else 22.0
        selector_width = width - (392.0 if large else 44.0)
        top = 385.0 if large else 300.0
        title_height = 21.0 if large else 18.0
    else:
        wide = large or group["landscape"]
        inset = (120.0 if group["landscape"] else 48.0) if large else 20.0
        inner_width = width - inset * 2
        preview = 131.0 if large else 95.0
        selector_width = (inner_width - preview - 72.0) / 2 if wide else inner_width
        left = inset + preview + 72.0 + selector_width if wide else inset
        panel_height = 254.0 if wide else 378.5 + (28.0 if hardware_selected else 0.0)
        panel_top = height - (210.0 if large else 188.0) - panel_height
        top = panel_top + (19.5 if large else 17.5) + (40.0 if wide else 225.0)
        title_height = 24.0 if large else 21.0
    choice_height = 50.0 if large else 44.0
    choices_top = top + title_height + 8.0
    choice_width = (selector_width - 16.0) / 3
    centers = {
        mode: (left + index * (selector_width + 8.0) / 3 + choice_width / 2,
               choices_top + choice_height / 2)
        for mode, index in MODE_INDEX.items()
    }
    return {
        "selector": [left, top, selector_width, title_height + 8.0 + choice_height],
        "centers": centers,
        "fallback_note": [left, choices_top + choice_height + 8.0,
                          selector_width, 24.0 if large else 20.0],
    }


def open_picture(harness: Any, group: dict[str, Any]) -> Any:
    width, height = viewport(harness, group)
    large = group["device"].startswith("scribe")
    wide_toolbar = large and group["landscape"]
    inset = (120.0 if group["landscape"] else 24.0) if large else 0.0
    picture_index = 2 if wide_toolbar else 1
    picture_x = inset + (picture_index + 0.5) * (width - inset * 2) / 5
    bottom = 210.0 if large else 188.0
    controls = 133.0 if large else 119.0
    toolbar = 77.0 if large else 69.0
    picture_y = height - bottom + controls + 1.0 + (toolbar - 1.0) / 2
    return harness.Replay(start=4500).tap(width / 2, height / 2).tap(picture_x, picture_y)


def selection(
    suite: Any, case: dict[str, Any], group: dict[str, Any], method: str, enabled: bool,
    *, reader: bool = False, picture: bool = False,
) -> None:
    state = case["state"]
    suite.preference(case, "dither_method", method)
    suite.check(case, "The persisted dither switch is an explicit boolean",
                state.get("preferences", {}).get("dither") is enabled,
                state.get("preferences", {}).get("dither"))
    suite.preference(case, "language", group["locale"])
    suite.expect(case, "interface_language", group["locale"])
    for field in DESKTOP_FALSE_FIELDS:
        suite.check(case, f"Desktop evidence exposes {field} as false",
                    field in state and state[field] is False, state.get(field))
    if reader:
        suite.expect(case, "menu", picture)
        suite.expect(case, "reader_panel", "picture" if picture else "")
        suite.expect(case, "modal_context", "reader-picture" if picture else "")
        effective = state.get("reader_effective") or {}
        suite.check(case, "Effective reading preferences retain the selected method",
                    effective.get("dither_method") == method, effective.get("dither_method"))
        suite.check(case, "Effective reading preferences retain the dither switch",
                    effective.get("dither") is enabled, effective.get("dither"))
        displayed = state.get("displayed_reader") or {}
        suite.check(case, "The retained desktop frame does not contain raw hardware-dither pixels",
                    "hardware_dither" in displayed and displayed["hardware_dither"] is False,
                    displayed.get("hardware_dither"))
        frame_preferences = displayed.get("preferences") or {}
        suite.check(case, "The accepted frame retains the selected dither switch",
                    frame_preferences.get("dither") is enabled, frame_preferences.get("dither"))
        suite.check(case, "The accepted frame retains the selected dither method",
                    frame_preferences.get("dither_method") == method,
                    frame_preferences.get("dither_method"))
    else:
        suite.expect(case, "settings_section", "image-processing")
        suite.expect(case, "modal", "")
    rows = suite.database_rows(case, "SELECT document FROM preferences WHERE key='application'")
    persisted = json.loads(rows[0][0]) if len(rows) == 1 else {}
    suite.check(case, "The application database durably stores both dither preferences",
                len(rows) == 1 and persisted.get("dither_method") == method
                and persisted.get("dither") is enabled,
                {"dither_method": persisted.get("dither_method"), "dither": persisted.get("dither")})


def anchor(suite: Any, case: dict[str, Any]) -> dict[str, Any]:
    state = case["state"]
    displayed = state.get("displayed_reader") or {}
    session = displayed.get("session") or {}
    complete = (all(field in state and state[field] is not None for field in ANCHOR_FIELDS)
                and all(field in session and session[field] is not None for field in SESSION_FIELDS))
    value = {
        "application": {field: state.get(field) for field in ANCHOR_FIELDS},
        "displayed_session": {field: session.get(field) for field in SESSION_FIELDS},
    }
    suite.check(case, "The application and accepted frame expose a complete reading anchor", complete, value)
    value["complete"] = complete
    case["reading_anchor"] = value
    return value


def same_anchor(suite: Any, case: dict[str, Any], baseline: dict[str, Any]) -> None:
    actual = anchor(suite, case)
    suite.check(case, "Dither selection and cold restart preserve the exact reading anchor",
                baseline["complete"] and actual["complete"] and actual == baseline,
                {"baseline": baseline, "actual": actual})


def fallback_note(
    suite: Any, harness: Any, case: dict[str, Any], group: dict[str, Any], surface: str,
) -> None:
    from PIL import Image

    state = case["state"]
    preferences = state.get("preferences") or {}
    suite.check(case, "The selected hardware mode requires software fallback on this desktop",
                preferences.get("dither") is True and preferences.get("dither_method") == "hardware"
                and state.get("hardware_y4_available") is False,
                {"dither": preferences.get("dither"), "method": preferences.get("dither_method"),
                 "hardware_y4_available": state.get("hardware_y4_available")})
    geometry = selector_geometry(harness, group, surface, hardware_selected=True)
    left, top, width, height = geometry["fallback_note"]
    rectangle = (math.ceil(left + 1), math.ceil(top), math.floor(left + width - 1), math.floor(top + height))
    path = suite.output / f"{case['name']}.fallback-note.png"
    with Image.open(case["screenshot"]) as capture:
        note = capture.convert("L").crop(rectangle)
        ink = sum(note.histogram()[:192])
        note.save(path)
    labels = json.loads((Path(__file__).resolve().parent.parent / "assets" / "i18n"
                         / f"{group['locale']}.json").read_text(encoding="utf-8"))
    evidence = {"rectangle_ltrb": list(rectangle), "dark_pixels": ink, "crop": str(path),
                "expected_text_for_manual_review": labels["dither_software_fallback"],
                "manual_review_required": True, "ocr_performed": False}
    case["fallback_note_evidence"] = evidence
    suite.check(case, "The dedicated fallback note line contains visible text ink", ink >= 8, evidence)


def run_case(
    suite: Any, group_name: str, group: dict[str, Any], name: str, screen: str,
    data: Path, replay: Any = None,
) -> dict[str, Any]:
    # The shared bounded runner owns process-group cleanup. Its retained traces
    # serve diagnostics here and are not used to make latency claims.
    return suite.run(f"{group_name}-{name}", screen, device=group["device"],
                     landscape=group["landscape"],
                     expected_landscape=group["landscape"] if screen == "reader" else False,
                     data=data, replay=replay,
                     expected_route=screen, capture_delay=1600, record_performance=True)


def run_group(suite: Any, harness: Any, group_name: str) -> None:
    group = GROUPS[group_name]
    data = suite.fresh(f"{group_name}-fixture")
    settings = selector_geometry(harness, group, "settings")
    seeded = (harness.Replay(start=1000)
              .intent("settings", "set", f"language={group['locale']}")
              .intent("settings", "set", "reading-direction=rtl")
              .intent("settings", "set", "page-layout-mode=single")
              .intent("settings", "open", "image-processing")
              .tap(*settings["centers"]["off"], wait=1600))
    case = run_case(suite, group_name, group, "settings-off", "settings", data, seeded)
    selection(suite, case, group, "software", False)
    for mode in ("software", "hardware"):
        replay = (harness.Replay(start=1000).intent("settings", "open", "image-processing")
                  .tap(*settings["centers"][mode], wait=1600))
        case = run_case(suite, group_name, group, f"settings-{mode}", "settings", data, replay)
        selection(suite, case, group, mode, True)
        case["dither_pointer_target"] = {"surface": "settings", "mode": mode,
                                         "center": list(settings["centers"][mode])}
        if mode == "hardware":
            fallback_note(suite, harness, case, group, "settings")

    # Enter Reader in a separate process without changing dither preferences.
    # This hardware persistence capture also establishes the anchor baseline.
    cold = run_case(suite, group_name, group, "reader-hardware-restart", "reader", data,
                    open_picture(harness, group))
    selection(suite, cold, group, "hardware", True, reader=True, picture=True)
    fallback_note(suite, harness, cold, group, "reader")
    baseline = anchor(suite, cold)

    current_method = "hardware"
    for mode in ("software", "hardware", "off"):
        geometry = selector_geometry(harness, group, "reader", hardware_selected=current_method == "hardware")
        replay = open_picture(harness, group).tap(*geometry["centers"][mode], wait=1600)
        case = run_case(suite, group_name, group, f"reader-{mode}", "reader", data, replay)
        method = mode if mode != "off" else current_method
        selection(suite, case, group, method, mode != "off", reader=True, picture=True)
        same_anchor(suite, case, baseline)
        case["dither_pointer_target"] = {"surface": "reader", "mode": mode,
                                         "center": list(geometry["centers"][mode])}
        if mode == "hardware":
            fallback_note(suite, harness, case, group, "reader")
        current_method = method

    restarted = run_case(suite, group_name, group, "reader-off-restart", "reader", data)
    selection(suite, restarted, group, "hardware", False, reader=True)
    same_anchor(suite, restarted, baseline)
    width, height = viewport(harness, group)
    turned = run_case(suite, group_name, group, "reader-normal-page-turn", "reader", data,
                      harness.Replay(start=4500).tap(width * 0.13, height / 2, wait=1600))
    selection(suite, turned, group, "hardware", False, reader=True)
    actual = anchor(suite, turned)
    expected_page = baseline["application"]["reader_page"]
    expected_page = expected_page + 1 if isinstance(expected_page, int) else None
    suite.check(turned, "A normal RTL pointer tap still advances exactly one page",
                actual["complete"] and expected_page is not None
                and actual["application"]["reader_page"] == expected_page
                and actual["application"]["visible_reader_page"] == expected_page
                and actual["displayed_session"]["page"] == expected_page,
                {"baseline": baseline, "actual": actual})
    stable_application = ("current_book", "current_chapter", "reader_offset", "reader_zoom", "reader_wide_half")
    stable_session = ("book_id", "chapter_id", "vertical_offset", "wide_half", "zoom")
    suite.check(turned, "The normal page turn preserves the chapter and other anchor components",
                actual["complete"] and baseline["complete"]
                and all(actual["application"][field] == baseline["application"][field]
                        for field in stable_application)
                and all(actual["displayed_session"][field] == baseline["displayed_session"][field]
                        for field in stable_session),
                {"baseline": baseline, "actual": actual})


def finish(suite: Any, harness: Any, groups: list[str], confirmation: str) -> int:
    status = suite.finish()
    path = suite.output / "report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report.update({
        "kind": "remote-hardware-dither-ui",
        "requested_groups": groups,
        "remote_worker_confirmation": confirmation,
        "verification_script_sha256": harness.sha256(Path(__file__).resolve()),
        "native_acceptance": False,
        "manual_design_review_required": True,
        "evidence_scope": "Desktop pointer selection, durable dither preferences, unchanged reading anchors, "
                          "software fallback state and retained note-line pixels. Profile selection does not "
                          "establish native Y4 application, physical e-ink output, or firmware acceptance.",
    })
    harness.save_json(path, report)
    markdown = suite.output / "report.md"
    with markdown.open("a", encoding="utf-8") as stream:
        stream.write("\nThis focused dither report requires manual review of the full captures and fallback-note crops. "
                     "Note-line ink checks are not OCR. Selected Hardware Y4 is a preference, "
                     "and every desktop availability, effective, request, and raw-frame check must remain false. "
                     "No native hardware acceptance is claimed.\n")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True, help="Immutable desktop binary inside the caller's executable namespace")
    parser.add_argument("--output-dir", type=Path, required=True, help="Fresh directory retaining all captures, crops, actions, state and reports")
    parser.add_argument("--timeout", type=int, default=60, help="Per-process timeout in seconds")
    parser.add_argument("--expected-sha256", help="Require this frozen desktop binary SHA-256 before running")
    parser.add_argument("--group", action="append", choices=tuple(GROUPS), dest="groups", help="Run only the selected focused group; repeat to select multiple groups")
    parser.add_argument("--test-env-host", action="store_true", help="Confirm that this SSH session reached the designated worker through ssh test-env")
    options = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run this script only on the designated remote Linux worker through ssh test-env.")
    if not (os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT")):
        parser.error("An SSH session is required; local verification is forbidden.")
    if not options.test_env_host and os.environ.get("AIDOKU_TEST_ENV") != "1":
        parser.error("Confirm the ssh test-env destination with --test-env-host or AIDOKU_TEST_ENV=1.")
    if options.timeout < 1:
        parser.error("--timeout must be positive.")
    if not options.binary.is_file() or not os.access(options.binary, os.X_OK):
        parser.error("--binary must identify an executable desktop build inside the current namespace.")
    if options.output_dir.exists():
        parser.error("Use a fresh --output-dir to preserve earlier captures and reports.")
    if shutil.which("xvfb-run") is None:
        parser.error("The remote worker requires xvfb-run.")
    harness = load_harness()
    if options.expected_sha256 and harness.sha256(options.binary.resolve()) != options.expected_sha256.lower():
        parser.error("The desktop binary differs from --expected-sha256.")
    groups = list(dict.fromkeys(options.groups or GROUPS))
    suite = harness.Suite(SimpleNamespace(binary=options.binary, output_dir=options.output_dir,
                                         timeout=options.timeout, selected_cases=groups,
                                         performance_traces=False))
    for group_name in groups:
        try:
            run_group(suite, harness, group_name)
        except Exception as error:
            suite.cases.append({"name": f"{group_name}-harness", "passed": False, "checks": [
                {"description": "The focused dither group completed", "passed": False,
                 "actual": f"{type(error).__name__}: {error}"},
            ]})
        if suite.process_cleanup_failed:
            break
    confirmation = "--test-env-host" if options.test_env_host else "AIDOKU_TEST_ENV=1"
    return finish(suite, harness, groups, confirmation)


if __name__ == "__main__":
    sys.exit(main())
