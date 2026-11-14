#!/usr/bin/env python3
"""Exercise the approved portrait UI on the remote Linux worker."""

import argparse
import importlib.util
import json
from pathlib import Path

from PIL import Image, ImageChops

spec = importlib.util.spec_from_file_location("ui_suite", Path(__file__).with_name("verify-ui.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Replay = module.Replay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--previews-only", action="store_true")
    parser.add_argument("--navigation-only", action="store_true")
    args = parser.parse_args()
    suite = module.Suite(args)
    for device, width, height in [("pw5", 618, 824), ("scribe", 930, 1240)]:
        if args.navigation_only:
            for descending in [False, True]:
                replay = Replay(3000).intent("reader", "toc")
                if descending:
                    replay.intent("detail", "sort").intent("detail", "locate")
                case = suite.run(f"{device}-located-{'descending' if descending else 'ascending'}", "reader", device=device, replay=replay, expected_route="detail")
                suite.check(case, "Current chapter is visible in the selected order", any(row["current"] for row in case["state"].get("visible_chapters", [])))
            case = suite.run(f"{device}-library-located-detail", device=device,
                             replay=Replay(4500).tap(width * .1, height * .27), expected_route="detail")
            suite.check(case, "Opening a library book locates its saved chapter", any(row["current"] for row in case["state"].get("visible_chapters", [])))
            suite.run(f"{device}-located-detail-back", "reader", device=device,
                      replay=Replay(3000).intent("reader", "toc").intent("detail", "back"), expected_route="reader")
            continue
        for screen in ["library", "catalog", "sources", "search", "history", "settings", "detail"]:
            suite.run(f"{device}-{screen}", screen, device=device)
        for section in ["default-direction", "frontlight-warmth", "screen-refresh", "about"]:
            case = suite.run(f"{device}-settings-{section}", "settings", device=device,
                             replay=Replay().intent("settings", "open", section), expected_route="settings")
            suite.expect(case, "settings_section", section)
        for panel in ["mode", "picture", "brightness"]:
            case = suite.run(f"{device}-reader-{panel}", "reader", device=device,
                             replay=Replay(1600).tap(width / 2, height / 2).tap(width * {"mode": .375, "picture": .625, "brightness": .875}[panel], height - 32), expected_route="reader")
            suite.expect(case, "reader_panel", panel)
        if args.previews_only:
            continue
        light = suite.run(f"{device}-reader-fullscreen", "reader", device=device)
        night = suite.run(f"{device}-reader-night", "reader", device=device,
                          replay=Replay(1600).intent("settings", "set", "night-mode=true"), expected_route="reader")
        with Image.open(light["screenshot"]) as a, Image.open(night["screenshot"]) as b:
            suite.check(night, "Night mode preserves every fullscreen comic pixel", ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None)
        for screen in ["library", "catalog", "sources", "search", "history", "settings", "detail"]:
            suite.run(f"{device}-{screen}-night", screen, device=device,
                      replay=Replay().intent("settings", "set", "night-mode=true"), expected_route=screen)
        library_light = next(case for case in suite.cases if case["name"] == f"{device}-library")
        library_night = next(case for case in suite.cases if case["name"] == f"{device}-library-night")
        margin, gap, columns, top = (40, 20, 5, 252) if device == "scribe" else (28, 14, 4, 212)
        cover_width = (width - margin * 2 - gap * (columns - 1)) / columns
        interior = (margin + 3, top + 3, int(margin + cover_width - 3), int(top + cover_width * 1.36 - 3))
        with Image.open(library_light["screenshot"]) as a, Image.open(library_night["screenshot"]) as b:
            suite.check(library_night, "Night mode preserves cover artwork pixels", ImageChops.difference(a.convert("RGB").crop(interior), b.convert("RGB").crop(interior)).getbbox() is None)
        for fit in ["original", "page", "height", "width"]:
            fit_index = ["original", "page", "height", "width"].index(fit)
            touch, margin, nav, panel = (56, 40, 72, 300) if device == "scribe" else (48, 28, 64, 268)
            options_top = height - nav - panel - 24
            button_y = options_top + touch * 2 + 52 + (fit_index // 2) * (touch + 8) + touch / 2
            button_width = (width - margin * 2 - 8) / 2
            button_x = margin + (fit_index % 2) * (button_width + 8) + button_width / 2
            case = suite.run(f"{device}-fit-{fit}", "reader", device=device,
                             replay=Replay(1600).tap(width / 2, height / 2).tap(width * .375, height - 32).tap(button_x, button_y, wait=1200), expected_route="reader")
            suite.preference(case, "reader_fit", fit)
            suite.expect(case, "reader_panel", "mode")
        chapters = suite.run(f"{device}-reader-chapters", "reader", device=device,
                             replay=Replay(1600).tap(width / 2, height / 2).tap(width / 8, height - 32), expected_route="detail")
        suite.expect(chapters, "detail_filter", 0)
        suite.check(chapters, "Current chapter is visible after opening Chapters", any(row["current"] for row in chapters["state"].get("visible_chapters", [])))
        restored = suite.run(f"{device}-chapters-back", "reader", device=device,
                             replay=Replay(1600).intent("reader", "toc").intent("detail", "back"), expected_route="reader")
        suite.check(restored, "Returning from chapters retains the displayed comic", restored["state"].get("reader_image_ready"))
        suite.run(f"{device}-reader-back-library", "reader", device=device,
                  replay=Replay(1600).intent("reader", "back"), expected_route="library")
        for filter_id in [1, 2, 3]:
            case = suite.run(f"{device}-detail-filter-{filter_id}", "detail", device=device,
                             replay=Replay().intent("detail", "filter", str(filter_id)), expected_route="detail")
            suite.expect(case, "detail_filter", filter_id)
            visible = case["state"].get("visible_chapters", [])
            if filter_id == 1:
                suite.check(case, "Unread filter excludes read chapters", all(not chapter["read"] for chapter in visible))
            if filter_id == 2:
                suite.check(case, "Downloaded filter excludes unavailable chapters", all(chapter["downloaded"] for chapter in visible))
        bookmark = suite.run(f"{device}-bookmark-add", "reader", device=device,
                             replay=Replay(3000).intent("reader", "seek", "13", wait=3000).intent("reader", "bookmark").intent("reader", "toc").intent("detail", "filter", "3"), expected_route="detail")
        rows = bookmark["state"].get("detail_bookmarks", [])
        suite.check(bookmark, "Reader bookmark is present at the newly saved page", any(row["id"].endswith("|13") for row in rows), rows)
        if rows:
            identity = rows[0]["id"]
            opened = suite.run(f"{device}-bookmark-open", "detail", device=device, data=Path(bookmark["data_dir"]),
                               replay=Replay().intent("panel", "choose", f"bookmark:{identity}", wait=1600), expected_route="reader")
            suite.expect(opened, "reader_page", int(identity.rsplit("|", 1)[1]))
            removed = suite.run(f"{device}-bookmark-remove", "detail", device=device, data=Path(bookmark["data_dir"]),
                                replay=Replay().intent("detail", "filter", "3").intent("detail", "bookmark-remove", identity), expected_route="detail")
            suite.check(removed, "Removing a bookmark updates the actual collection", all(row["id"] != identity for row in removed["state"].get("detail_bookmarks", [])))
        paged = suite.run(f"{device}-library-page-two", device=device,
                          replay=Replay().intent("page", "turn", "1"), expected_route="library")
        suite.expect(paged, "page", 2)
        refreshed = suite.run(f"{device}-refresh-preset", "settings", device=device,
                              replay=Replay().intent("settings", "open", "screen-refresh").tap(width - margin - 13, (36 + 28 + 28 + touch / 2) if device == "scribe" else (28 + touch + 32 + 28 + touch / 2)), expected_route="settings")
        suite.preference(refreshed, "refresh_every", 0)
        preset_y = (36 + 28 + 28 + touch / 2) if device == "scribe" else (28 + touch + 32 + 28 + touch / 2)
        pending = Replay().intent("settings", "open", "screen-refresh").event("pointer", "move", f"{width - margin - 13},{preset_y}").event("pointer", "press", f"{width - margin - 13},{preset_y}", 60)
        preview = suite.run(f"{device}-refresh-unreleased", "settings", device=device, replay=pending, expected_route="settings")
        suite.preference(preview, "refresh_every", 6)
    report = {"binary_sha256": suite.binary_hash, "cases": suite.cases,
              "passed": all(case["passed"] for case in suite.cases),
              "case_count": len(suite.cases), "check_count": sum(len(case["checks"]) for case in suite.cases)}
    module.save_json(suite.output / "report.json", report)
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
