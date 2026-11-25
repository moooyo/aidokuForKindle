#!/usr/bin/env python3
"""Drive the portrait handoff in the real Slint simulator and retain reviewable evidence."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageChops


@dataclass(frozen=True)
class Case:
    name: str
    screen: str
    steps: tuple = ()
    expected: tuple = ()
    settle_ms: int = 1000
    demo: bool = True


def tap(x, y, wait=150):
    return ("pointer", "tap", f"{round(x)},{round(y)}", wait)


def intent(section, action, value="", wait=150):
    return (section, action, value, wait)


def hold(x, y, duration=650):
    position = f"{x},{y}"
    return (("pointer", "press", position, duration), ("pointer", "release", position, 150))


def cases(width, height, large):
    inset = 28 if large else 14
    touch = 56 if large else 48
    toolbar_y = 68 if large else 56
    nav_y = height - (38 if large else 35)
    right = width - inset - touch / 2
    previous_tool = right - touch
    first_cover = (115, 370) if large else (80, 325)
    detail_row = (440, 230) if large else (164, 443)
    select_header = (440, 166) if large else (132, 376)
    size = 15 if large else 8
    chapter_size = 12 if large else 5
    def dock(index):
        return tap(width * (index + 0.5) / 5, nav_y, 400)

    keyboard_open = tap(126, 76 if large else 60)
    settings = [
        ("default-direction", "settings-reading"), ("frontlight-warmth", "settings-light"),
        ("screen-refresh", "settings-refresh"), ("hardware-acceleration", "settings-media"),
        ("manage-sources", "settings-sources"), ("storage", "settings-storage"),
        ("categories", "settings-categories"), ("backup", "settings-backup"),
        ("language", "settings-language"), ("about", "settings-about"),
        ("startup-options", "settings-startup"),
    ]
    result = [
        Case("library", "library", expected=(("route", "library"), ("visible_books.length", size))),
        Case("library-held", "library", (("pointer", "press", f"{round(right)},{toolbar_y}", 100),), (("modal", ""),), 120),
        Case("library-page-two", "library", (tap(right - 2 * touch, toolbar_y),), (("page", 2),)),
        Case("library-category", "library", (tap(114 if large else 94, toolbar_y),), (("library_filter", 1),)),
        Case("library-sort", "library", (tap(previous_tool, toolbar_y),), (("modal_context", "library-filter"),)),
        Case("library-sort-changed", "library", (tap(previous_tool, toolbar_y), tap(510 if large else 100, 253 if large else height - 317)), (("library_sort", "title"),)),
        Case("library-more", "library", (tap(right, toolbar_y),), (("modal_context", "library-more"),)),
        Case("library-selection", "library", hold(*first_cover), (("selection_mode", True), ("selected.length", 1), ("route", "library"))),
        Case("library-selection-page", "library", (*hold(*first_cover), tap(width - 160, toolbar_y)), (("selection_mode", True), ("selected.length", size))),
        Case("library-selection-confirm", "library", (*hold(*first_cover), tap(width * 0.875, nav_y)), (("modal_item_ids.0", "remove-selected-confirm"),)),
        Case("library-filter-empty", "library", (intent("library", "filter", "4"), intent("library", "cycle-filter", "tracked")), (("visible_books.length", 0),)),
        Case("library-clear-filters", "library", (intent("library", "filter", "4"), intent("library", "cycle-filter", "tracked"), tap(width / 2, (400 if large else 300) + 78)), (("library_filter", 0), ("library_filters", [0, 0, 0, 0]), ("visible_books.length", size))),
        Case("library-empty", "library", expected=(("library_count", 0),), demo=False),
        Case("detail", "library", (tap(*first_cover),), (("route", "detail"), ("visible_chapters.length", chapter_size))),
        Case("detail-selection", "detail", hold(*detail_row), (("chapter_selection_mode", True), ("selected_chapter_count", 1), ("route", "detail"))),
        Case("detail-selection-cancel", "detail", (*hold(*detail_row), tap(width - (80 if large else 60), select_header[1])), (("chapter_selection_mode", False),)),
        Case("detail-bookmarks", "detail", (tap(575 if large else 242, 166 if large else 375),), (("detail_filter", 3),)),
        Case("detail-more", "detail", (tap(width - (60 if large else 44), (64 if large else 52)),), (("modal_context", "detail-more"), ("modal_items.length", 5))),
        Case("detail-summary", "detail", (tap(143 if large else width - 52, 740 if large else 284),), (("modal", "summary"), ("summary_is_book", True))),
        Case("detail-batch", "detail", (tap(182, 632) if large else tap(width - 75, 327),), (("modal_context", "batch-download"),)),
        Case("detail-sorted", "detail", (tap(width - (69 if large else 52), 168 if large else 380),), (("newest_first", True), ("visible_chapters.0.is_new", True))),
        Case("detail-sleep-cover", "detail", (tap(width - (60 if large else 44), (64 if large else 52)), tap(width - 180, 318 if large else 277)), (("modal", ""),)),
        Case("detail-page-two", "detail", (tap(width - (82 if large else 80), height - 26),), (("visible_chapters.length", chapter_size),)),
        Case("history", "history", expected=(("route", "history"), ("history_filter", 0), ("visible_history.length", 13 if large else 6))),
        Case("history-next", "history", (tap(right - 2 * touch, toolbar_y),), (("page", 2),)),
        Case("history-updates", "history", (tap(178 if large else 140, toolbar_y),), (("history_filter", 1),)),
        Case("history-incognito", "history", (tap(previous_tool, toolbar_y),), (("preferences.incognito", True), ("message", "已开启无痕")), 2800),
        Case("history-toast-dismissed", "history", (tap(previous_tool, toolbar_y, 2600), tap(width - 4, 90)), (("preferences.incognito", True), ("message", ""))),
        Case("history-confirm", "history", (tap(right, toolbar_y),), (("modal_context", "clear-history"),)),
        Case("history-empty", "history", (tap(right, toolbar_y), tap(width * 0.64 if large else width * 0.74, 641 if large else height - 45)), (("history_count", 0), ("library_count", 46))),
        Case("browse-entry", "library", (dock(1),), (("route", "catalog" if large else "sources"),)),
        Case("catalog", "catalog", expected=(("route", "catalog"), ("visible_catalog.length", 15 if large else 6))),
        Case("catalog-page-two", "catalog", (intent("page", "turn", "1"),), (("page", 2),)),
        Case("catalog-picker", "catalog", (tap(125, toolbar_y),), (("modal_context", "source-picker"),)),
        Case("catalog-filter", "catalog", (tap(right, toolbar_y),), (("modal_context", "catalog-filter"),)),
        Case("sources", "sources", expected=(("route", "sources"), ("source_filter", 0))),
        Case("sources-installable", "sources", (tap(182 if large else 154, toolbar_y),), (("source_filter", 1),)),
        Case("sources-updates", "sources", (tap(305 if large else 253, toolbar_y),), (("source_filter", 2),)),
        Case("sources-repositories", "sources", (tap(right, toolbar_y),), (("modal_context", "repository-list"),)),
        Case("sources-server", "sources", (intent("sources", "preferences", "komga"),), (("modal_context", "server-configuration"),)),
        Case("search", "search", expected=(("route", "search"), ("query", "雾港"))),
        Case("search-empty", "library", (dock(2),), (("route", "search"), ("query", ""))),
        Case("search-keyboard", "search", (keyboard_open,), (("modal", "keyboard"), ("keyboard_mode", "pinyin"))),
        Case("search-local-matches", "search", (keyboard_open,), (("modal", "keyboard"),)),
        Case("search-keyboard-number", "search", (keyboard_open, tap(62, height - 38)), (("keyboard_mode", "numbers"),)),
        Case("search-keyboard-english", "search", (keyboard_open, tap(width * 0.208, height - 38)), (("keyboard_mode", "english"),)),
        Case("search-keyboard-submit", "search", (keyboard_open, tap(width * 0.88, height - 38)), (("modal", ""), ("query", "雾港"))),
        Case("search-scope", "search", (intent("search", "source-picker"),), (("modal_context", "search-scope:results"),)),
        Case("search-scope-typing", "search", (keyboard_open, intent("search", "source-picker")), (("modal_context", "search-scope:typing"),)),
        Case("search-no-results", "search", (intent("search", "query", "No match in the handoff fixture"),), (("search_result_count", 0),)),
        Case("settings", "library", (dock(4),), (("route", "settings"), ("visible_setting_ids.length", 12))),
        Case("settings-download-queue", "library", (tap(right, toolbar_y), tap(width - 145, 143 if large else 171)), (("route", "library"), ("modal", "downloads"))),
    ]
    for index, (section, scene) in enumerate(settings):
        x = 153 if large else width * (0.265 if index % 2 == 0 else 0.735)
        y = (186 + 284 * (index // 4) + 60 * (index % 4)) if large else (163 + 214 * (index // 4) + 90 * ((index % 4) // 2))
        expected = (("route", "sources"),) if section == "manage-sources" else (("settings_section", section),)
        result.append(Case(scene, "settings", (tap(x, y),), expected))
    result.extend([
        Case("settings-tap-layout-choice", "settings", (
            intent("settings", "open", "default-direction"),
            tap(629 if large else width / 2, 508 if large else 432),
        ), (("preferences.tap_layout", "l-shaped"),)),
        Case("settings-language-choice", "settings", (
            intent("settings", "open", "language"),
            tap(420 if large else 110, 318 if large else 266),
        ), (("preferences.language", "en"), ("interface_library_label", "Library"))),
        Case("settings-backup-create", "settings", (
            intent("settings", "open", "backup", wait=400),
            tap(495 if large else 154, 166 if large else 128),
        ), (("settings_details.length", 1),), 1800),
        Case("settings-refresh-fast", "settings", (intent("settings", "open", "screen-refresh"), intent("settings", "set", "display-mode=fast")), (("display_mode", "fast"),)),
        Case("settings-refresh-high", "settings", (intent("settings", "open", "screen-refresh"), intent("settings", "set", "display-mode=high_quality")), (("display_mode", "high_quality"),)),
        Case("settings-grayscale-off", "settings", (intent("settings", "open", "screen-refresh"), intent("settings", "set", "dither-mode=off")), (("preferences.dither", False),)),
    ])
    return result


PROFILES = {"scribe": (930, 1240, True), "pw5": (618, 824, False), "pw6": (632, 840, False)}


def lookup(state, path):
    for segment in path.split("."):
        if segment == "length":
            state = len(state)
        elif segment.isdigit():
            state = state[int(segment)]
        else:
            state = state[segment]
    return state


def verify(binary, output, profile, case):
    width, height, _ = PROFILES[profile]
    directory = output / profile
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / case.name
    result = {"profile": profile, "case": case.name, "checks": [], "errors": []}
    now = 850
    actions = []
    steps = ((intent("detail", "locate"),) if case.screen == "detail" else ()) + case.steps
    for section, action, value, wait in steps:
        actions.append({"at_ms": now, "section": section, "action": action, "value": value})
        now += wait
    action_file = stem.with_suffix(".actions.json")
    action_file.write_text(json.dumps(actions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix=f"aidoku-ui-{profile}-") as data, stem.with_suffix(".log").open("wb") as log:
        cmd = [str(binary), "--offline", "--device", profile, "--screen", case.screen, "--data-dir", data,
               "--actions", str(action_file), "--capture", str(stem.with_suffix(".png")), "--capture-settle-covers",
               "--exit-after-ms", str(now + case.settle_ms)]
        if case.demo:
            cmd.append("--demo")
        if not os.environ.get("DISPLAY"):
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1400x1600x24", *cmd]
        try:
            subprocess.run(cmd, check=True, timeout=90, stdout=log, stderr=subprocess.STDOUT)
            metadata = json.loads(stem.with_suffix(".metadata.json").read_text())
            state = json.loads(stem.with_suffix(".state.json").read_text())
            with Image.open(stem.with_suffix(".png")) as frame:
                if frame.size != (width, height) or frame.convert("L").getextrema()[0] > 80:
                    result["errors"].append(f"Invalid visible frame: {frame.size}")
            if metadata["profile"] != profile or metadata["logical_size"] != [width, height]:
                result["errors"].append(f"Wrong simulator profile: {metadata}")
            for path, expected in case.expected:
                try:
                    actual = lookup(state, path)
                except (KeyError, IndexError, TypeError):
                    actual = None
                result["checks"].append({"path": path, "expected": expected, "actual": actual})
                if expected != actual:
                    result["errors"].append(f"{path}: expected {expected!r}, got {actual!r}")
            if case.name == "search-local-matches" and not state["search_local_matches"]:
                result["errors"].append("Typing did not project the matching library books")
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            result["errors"].append(str(error))
    print(f"{profile}/{case.name}: {'PASS' if not result['errors'] else result['errors']}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", action="append", choices=PROFILES)
    parser.add_argument("--only", action="append", help="Run matching scene ID(s), or omit for the whole matrix")
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2, 3))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    profiles = args.device or ("scribe", "pw5", "pw6")
    jobs = [(device, case) for device in profiles for case in cases(*PROFILES[device])
            if not args.only or any(pattern in case.name for pattern in args.only)]
    if not jobs:
        parser.error("No scenes match the requested selection")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(verify, args.binary.resolve(), output, device, case) for device, case in jobs]
        results = [future.result() for future in futures]
    for profile in profiles:
        folder = output / profile
        normal, pressed = folder / "library.png", folder / "library-held.png"
        if normal.exists() and pressed.exists():
            with Image.open(normal) as first, Image.open(pressed) as second:
                toolbar = (0, 0, first.width, 100 if profile == "scribe" else 84)
                unchanged = ImageChops.difference(first.crop(toolbar).convert("RGB"), second.crop(toolbar).convert("RGB")).getbbox() is None
            results.append({"profile": profile, "case": "no-press-flash", "errors": [] if unchanged else ["Press changed the visible frame"]})
    passed = sum(not result["errors"] for result in results)
    (output / "report.json").write_text(json.dumps({"passed": passed, "failed": len(results) - passed, "cases": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {passed}/{len(results)} observations; report: {output / 'report.json'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
