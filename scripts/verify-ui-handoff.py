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


def swipe(first, last, y):
    return (("pointer", "press", f"{first},{y}", 120),
            ("pointer", "move", f"{last},{y}", 120),
            ("pointer", "release", f"{last},{y}", 300))


def cases(width, height, large):
    inset = 28 if large else 14
    touch = 56 if large else 48
    toolbar_y = 68 if large else 56
    nav_y = height - (38 if large else 35)
    right = width - inset - touch / 2
    previous_tool = right - touch
    first_cover = (115, 370) if large else (80, 325)
    detail_row = (440, 230) if large else (164, 443)
    detail_heading_y = 166 if large else 376
    history_row_y = 215 if large else 164
    detail_bookmark = (575 if large else 242, detail_heading_y)
    detail_unread = (420 if large else 115, detail_heading_y)
    detail_downloaded = (498 if large else 177, detail_heading_y)
    detail_locate = (width - (122 if large else 98), detail_heading_y)
    select_all = (365 if large else 60, detail_heading_y)
    size = 15 if large else 8
    chapter_size = 12 if large else 5
    primary = "8:mangadex:demo-00"
    chapter = f"{primary}-chapter-034"
    sort_filter = tap(previous_tool, toolbar_y)
    tracked_filter_chip = (714 if large else width * 0.62, 575 if large else height - 46)

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
        Case("library-swipe-next", "library", swipe(128, 52, first_cover[1]), (("route", "library"), ("page", 2), ("selection_mode", False))),
        Case("library-swipe-return", "library", (tap(right - 2 * touch, toolbar_y), *swipe(52, 128, first_cover[1])), (("route", "library"), ("page", 1), ("selection_mode", False))),
        Case("library-category", "library", (tap(114 if large else 94, toolbar_y),), (("library_filter", 1),)),
        Case("library-sort", "library", (sort_filter,), (("modal_context", "library-filter"),)),
        Case("library-sort-changed", "library", (sort_filter, tap(510 if large else 100, 258 if large else height - 334)), (("library_sort", "title"),)),
        Case("library-filter-exclude", "library", (sort_filter, tap(*tracked_filter_chip), tap(*tracked_filter_chip)), (("library_filters", [0, 0, -1, 0]),)),
        Case("library-filter-cycle", "library", (sort_filter, tap(*tracked_filter_chip), tap(*tracked_filter_chip), tap(*tracked_filter_chip)), (("library_filters", [0, 0, 0, 0]),)),
        Case("library-more", "library", (tap(right, toolbar_y),), (("modal_context", "library-more"),)),
        Case("library-more-selection-empty", "library", (tap(right, toolbar_y), tap(width - 120, 193 if large else 224), tap(width * 0.625, nav_y)), (("selection_mode", True), ("selected.length", 0), ("modal", ""))),
        Case("library-selection", "library", hold(*first_cover), (("selection_mode", True), ("selected.length", 1), ("route", "library"))),
        Case("library-selection-page", "library", (*hold(*first_cover), tap(width - 160, toolbar_y)), (("selection_mode", True), ("selected.length", size))),
        Case("library-selection-confirm", "library", (*hold(*first_cover), tap(width * 0.875, nav_y)), (("modal_item_ids.0", "remove-selected-confirm"),)),
        Case("library-selection-remove", "library", (*hold(*first_cover), tap(width * 0.875, nav_y), tap(width * 0.64 if large else width * 0.74, 502 if large else height - 48)), (("library_count", 45), ("downloads.length", 15), ("selection_mode", False))),
        Case("library-filter-empty", "library", (intent("library", "filter", "4"), intent("library", "cycle-filter", "tracked")), (("visible_books.length", 0),)),
        Case("library-clear-filters", "library", (intent("library", "filter", "4"), intent("library", "cycle-filter", "tracked"), tap(width / 2, 500 if large else 370)), (("library_filter", 0), ("library_filters", [0, 0, 0, 0]), ("visible_books.length", size))),
        Case("library-empty", "library", expected=(("library_count", 0),), demo=False),
        Case("library-empty-browse", "library", (tap(width / 2, 500 if large else 370),), (("route", "catalog" if large else "sources"),), demo=False),
        Case("library-continuation-detail", "library", (tap(185, 166 if large else 145),), (("route", "detail"), ("current_book", primary), ("page", 3 if large else 7))),
        Case("library-continuation-reader", "library", (tap(width - 90, 166 if large else 145),), (("route", "reader"), ("current_chapter", chapter), ("reader_page", 12), ("reader_image_ready", True)), 3300),
        Case("detail", "library", (tap(*first_cover),), (("route", "detail"), ("visible_chapters.length", chapter_size))),
        Case("detail-back", "library", (tap(*first_cover), tap(60 if large else 44, 64 if large else 52)), (("route", "library"),)),
        Case("detail-selection", "detail", hold(*detail_row), (("chapter_selection_mode", True), ("selected_chapter_count", 1), ("route", "detail"))),
        Case("detail-selection-cancel", "detail", (*hold(*detail_row), tap(width - (80 if large else 60), detail_heading_y)), (("chapter_selection_mode", False),)),
        Case("detail-selection-zero", "detail", (*hold(*detail_row), tap(*detail_row), tap(width / 2 + (145 if large else 0), detail_heading_y)), (("chapter_selection_mode", True), ("selected_chapter_count", 0), ("route", "detail"))),
        Case("detail-selection-back", "library", (tap(*first_cover), *hold(*detail_row), tap(60 if large else 44, 64 if large else 52)), (("chapter_selection_mode", False), ("route", "library"))),
        Case("detail-selection-filtered", "detail", (tap(*detail_unread), *hold(*detail_row), tap(*select_all)), (("detail_filter", 1), ("chapter_selection_mode", True), ("selected_chapter_count", 53), ("visible_chapters.0.selected", True))),
        Case("detail-selection-downloaded", "detail", (tap(*detail_downloaded), *hold(*detail_row), tap(*select_all)), (("detail_filter", 2), ("selected_chapter_count", 9), ("visible_chapters.0.selected", True))),
        Case("detail-locate", "detail", (tap(*detail_unread), tap(*detail_locate)), (("detail_filter", 0), ("page", 3 if large else 7), (f"visible_chapters.{9 if large else 3}.current", True))),
        Case("detail-reader", "detail", (tap(423 if large else 160, 956 if large else 653),), (("route", "reader"), ("current_chapter", chapter), ("reader_page", 12), ("reader_image_ready", True)), 3300),
        Case("detail-bookmarks", "detail", (tap(*detail_bookmark),), (("detail_filter", 3), ("detail_bookmarks.length", 1))),
        Case("detail-bookmark-open", "detail", (tap(*detail_bookmark), tap(*detail_row)), (("route", "reader"), ("current_chapter", chapter), ("reader_page", 12), ("reader_image_ready", True)), 3300),
        Case("detail-bookmark-delete", "detail", (tap(*detail_bookmark), tap(width - (77 if large else 52), detail_heading_y), tap(width - (68 if large else 52), detail_row[1])), (("detail_filter", 3), ("detail_bookmarks.length", 0), ("bookmark_count", 0))),
        Case("detail-more", "detail", (tap(width - (60 if large else 44), (64 if large else 52)),), (("modal_context", "detail-more"), ("modal_items.length", 5))),
        Case("detail-summary", "detail", (tap(143 if large else width - 52, 740 if large else 284),), (("modal", "summary"), ("summary_is_book", True))),
        Case("detail-batch", "detail", (tap(182, 632) if large else tap(width - 75, 327),), (("modal_context", "batch-download"),)),
        Case("detail-sorted", "detail", (tap(width - (69 if large else 52), 168 if large else 380),), (("newest_first", True), ("visible_chapters.0.is_new", True))),
        Case("detail-sleep-cover", "detail", (tap(width - (60 if large else 44), (64 if large else 52)), tap(width - 180, 317 if large else 275)), (("modal", ""), ("preferences.sleep_cover_book_id", primary))),
        Case("detail-page-two", "detail", (tap(width - (82 if large else 80), height - 26),), (("page", 4 if large else 8), ("visible_chapters.length", chapter_size))),
        Case("history", "history", expected=(("route", "history"), ("history_filter", 0), ("visible_history.length", 13 if large else 6), ("chapter_update_count", 6))),
        Case("history-held", "history", (("pointer", "press", f"{round(previous_tool)},{toolbar_y}", 100),), (("preferences.incognito", False),), 120),
        Case("history-next", "history", (tap(right - 2 * touch, toolbar_y),), (("page", 2),)),
        Case("history-open", "history", (tap(164, history_row_y),), (("route", "reader"), ("current_chapter", chapter), ("reader_page", 12), ("reader_image_ready", True)), 3300),
        Case("history-delete", "history", (tap(421 if large else width - 52, history_row_y),), (("history_count", 19), ("library_count", 46), ("downloads.length", 9), ("visible_history.0.title", "北方旅团"))),
        Case("history-updates", "history", (tap(178 if large else 140, toolbar_y),), (("history_filter", 1), ("visible_history.length", 6), ("visible_history.0.date_label", "今天"), ("visible_history.3.date_label", "昨天"), ("visible_history.3.group_start", True))),
        Case("history-update-open", "history", (tap(178 if large else 140, toolbar_y), tap(164, history_row_y)), (("route", "detail"), ("current_book", "8:mangadex:demo-09"))),
        Case("history-updates-download-all", "history", (tap(178 if large else 140, toolbar_y), tap(right, toolbar_y)), (("history_filter", 1), ("route", "history"), ("modal", ""), ("downloads.length", 15)), 2200),
        Case("history-updates-empty", "history", (tap(178 if large else 140, toolbar_y), tap(right, toolbar_y)), (("history_filter", 1), ("visible_history.length", 0), ("modal", "")), demo=False),
        Case("history-incognito", "history", (tap(previous_tool, toolbar_y),), (("preferences.incognito", True), ("message", "已开启无痕")), 2800),
        Case("history-toast-dismissed", "history", (tap(previous_tool, toolbar_y, 2600), tap(width - 4, 90)), (("preferences.incognito", True), ("message", ""))),
        Case("history-confirm", "history", (tap(right, toolbar_y),), (("modal_context", "clear-history"),)),
        Case("history-confirm-cancel", "history", (tap(right, toolbar_y), tap(width * 0.36 if large else width * 0.25, 641 if large else height - 45)), (("history_count", 20), ("modal", ""))),
        Case("history-empty", "history", (tap(right, toolbar_y), tap(width * 0.64 if large else width * 0.74, 641 if large else height - 45)), (("history_count", 0), ("library_count", 46), ("downloads.length", 9), ("message", "已清空阅读历史"))),
        Case("history-empty-library", "history", (tap(width / 2 - 95, 550 if large else 390),), (("route", "library"),), demo=False),
        Case("history-empty-browse", "history", (tap(width / 2 + 95, 550 if large else 390),), (("route", "catalog" if large else "sources"),), demo=False),
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
        Case("settings-tap-layout-reader", "settings", (
            tap(153 if large else width * 0.265, 186 if large else 163),
            tap(629 if large else width / 2, 508 if large else 432),
            tap(width - 62 if large else width - 49, 605 if large else 519),
            dock(0), tap(width - 90, 168 if large else 146, 1000),
            tap(width / 2, height - 30, 750),
        ), (("route", "reader"), ("preferences.tap_layout", "l-shaped"), ("preferences.reverse_taps", True), ("reader_page", 11))),
        Case("settings-backup-confirm", "settings", (
            tap(150 if large else width * 0.735, 650 if large else 467),
            tap(500 if large else 150, 167 if large else 130, 1700),
            tap(width - (75 if large else 73), 285 if large else 230),
        ), (("settings_section", "backup"), ("modal_context", "backup-restore-confirm"), ("settings_details.length", 1))),
        Case("settings-storage-return", "settings", (
            tap(150 if large else width * 0.735, 530 if large else 377, 1200),
            tap(width - (75 if large else 78), 326 if large else 280),
            tap(60 if large else 44, 64 if large else 52),
        ), (("route", "settings"), ("settings_section", "storage"))),
        Case("settings-storage-en", "settings", (
            intent("settings", "set", "language=en", 900),
            tap(150 if large else width * 0.735, 530 if large else 377),
        ), (("preferences.language", "en"), ("settings_section", "storage"))),
        Case("settings-refresh-ja", "settings", (
            intent("settings", "set", "language=ja", 900),
            tap(150 if large else width * 0.265, 306 if large else 253),
        ), (("preferences.language", "ja"), ("settings_section", "screen-refresh"))),
        Case("settings-media-disabled", "settings", (
            tap(150 if large else width * 0.735, 366 if large else 253),
            tap(width - 62 if large else width - 49, 198 if large else 158),
        ), (("settings_section", "hardware-acceleration"), ("hardware_media_available", False), ("preferences.hardware_acceleration", False))),
        Case("settings-startup-all", "settings", (
            tap(150 if large else width * 0.265, 874 if large else 681),
            tap(495 if large else width * 0.28, 570 if large else 493),
            tap(620 if large else width / 2, 637 if large else 551),
        ), (("settings_section", "startup-options"), ("settings_details.0.value", "on"), ("settings_details.5.value", "on"), ("active_startup_options.portrait", "shell"), ("quick_return_available", False))),
        Case("settings-startup-recommended", "settings", (
            tap(150 if large else width * 0.265, 874 if large else 681),
            tap(495 if large else width * 0.28, 570 if large else 493),
            tap(756 if large else width * 0.72, 570 if large else 493),
        ), (("settings_section", "startup-options"), ("settings_details.0.value", "off"), ("settings_details.4.value", "on"), ("settings_details.5.value", "off"))),
        Case("library-night", "settings", (
            tap(150 if large else width * 0.735, 246 if large else 163),
            tap(width - 62 if large else width - 49, 390 if large else 332), dock(0),
        ), (("route", "library"), ("preferences.night_mode", True))),
        Case("settings-category-create", "settings", (
            tap(150 if large else width * 0.265, 590 if large else 467),
            tap(width * 0.66 if large else width / 2, 452 if large else 387),
            tap(width * 0.208, height - 38), tap(width * 0.055, height - (274 if large else 216)),
            tap(width * 0.11, height - (199 if large else 156)), tap(width * 0.88, height - 38),
        ), (("settings_section", "categories"), ("modal", ""), ("settings_details.3.title", "qa"))),
        Case("settings-category-rename", "settings", (
            tap(150 if large else width * 0.265, 590 if large else 467),
            tap(width - 65, 198 if large else 158),
        ), (("modal", "keyboard"), ("keyboard_target", "category-rename:reading"), ("keyboard_text", "在读"))),
        Case("settings-license-open", "settings", (
            tap(150 if large else width * 0.735, 814 if large else 591),
            tap(width - 65, 378 if large else 320),
        ), (("settings_section", "about"), ("modal", "summary"), ("summary_is_book", False))),
        Case("history-update-download-one", "history", (
            tap(178 if large else 140, toolbar_y), tap(421 if large else width - 52, history_row_y),
        ), (("history_filter", 1), ("route", "history"), ("modal", ""), ("downloads.length", 10)), 2200),
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
                grayscale = frame.convert("L")
                if frame.size != (width, height) or grayscale.getextrema()[0] > 80:
                    result["errors"].append(f"Invalid visible frame: {frame.size}")
                if case.name == "library-night" and grayscale.getpixel((5, 5)) != 17:
                    result["errors"].append("Library night mode did not render the dark paper color")
                if case.name == "history" and frame.size == (width, height):
                    margin, dock, cover_inset = (40, 80, 9) if profile == "scribe" else (28, 72, 5)
                    edge = next((y for y in range(height - dock - 2, 99, -1)
                                 if grayscale.getpixel((margin, y)) < 70), 0)
                    tail = height - dock - edge - 1 - cover_inset
                    if not 0 <= tail <= 30:
                        result["errors"].append(f"Reading history leaves {tail}px after the last row")
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
        for screen in ("library", "history"):
            normal, pressed = folder / f"{screen}.png", folder / f"{screen}-held.png"
            if normal.exists() and pressed.exists():
                with Image.open(normal) as first, Image.open(pressed) as second:
                    toolbar = (0, 0, first.width, 100 if profile == "scribe" else 84)
                    unchanged = ImageChops.difference(first.crop(toolbar).convert("RGB"), second.crop(toolbar).convert("RGB")).getbbox() is None
                results.append({"profile": profile, "case": f"{screen}-no-press-flash", "errors": [] if unchanged else ["Press changed the visible toolbar"]})
    passed = sum(not result["errors"] for result in results)
    (output / "report.json").write_text(json.dumps({"passed": passed, "failed": len(results) - passed, "cases": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {passed}/{len(results)} observations; report: {output / 'report.json'}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
