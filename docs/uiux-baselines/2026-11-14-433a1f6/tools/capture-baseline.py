#!/usr/bin/env python3
"""Capture the unchanged application through real Slint pointer interactions.

Run only on the designated Linux worker. Each scene has an isolated offline
demo database, original PNG, input replay, state, geometry, and check results.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "baseline_ui_suite", Path(__file__).with_name("verify-ui.py")
)
UI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UI)
Replay = UI.Replay


def scenes_for(device: str) -> list[dict]:
    large = device == "scribe"
    width, height = UI.PROFILES[device]
    margin, touch, status, nav = (40, 56, 36, 72) if large else (28, 48, 28, 64)
    bottom = height - nav / 2
    first_cover = (margin + 50, 330 if large else 285)
    chapter_center = (500, 236) if large else (240, 443)

    def event_replay():
        return Replay(2400)

    def reader_replay():
        return Replay(3600).tap(width / 2, height / 2, wait=650)

    def navigation(index: int):
        return event_replay().tap(width * (index + 0.5) / 5, bottom, wait=1000)

    scenes = []

    def add(scene_id, screen, route=None, replay=None, expect=None, **kwargs):
        scenes.append({
            "id": scene_id, "screen": screen, "route": route or screen,
            "replay": replay, "expect": expect or {}, **kwargs,
        })

    add("library", "library")
    add("reader-fullscreen", "library", "reader",
        event_replay().tap(width - margin - 72, status + touch + 8 + (68 if large else 56), wait=3000),
        {"reader_image_ready": True, "menu": False})
    add("reader-controls", "reader", replay=reader_replay(),
        expect={"reader_image_ready": True, "menu": True, "reader_panel": ""})
    add("settings", "library", "settings", navigation(4))
    add("browse-entry", "library", "catalog" if large else "sources", navigation(1))
    add("catalog", "catalog")
    add("sources", "settings", "sources",
        event_replay().tap(160, 360 if large else 294), {"modal": ""})
    add("search", "search")
    add("search-keyboard", "search", replay=event_replay().tap(width * 0.4, status + (46 if large else 34)),
        expect={"modal": "keyboard"})
    add("history", "library", "history", navigation(3))
    add("detail", "library", "detail", event_replay().tap(*first_cover, wait=900))
    add("library-page-two", "library", replay=event_replay().tap(width - margin - touch * 2 - touch / 2, status + touch / 2),
        expect={"page": 2})
    add("library-sort", "library", replay=event_replay().tap(width - margin - touch * 1.5, status + touch / 2),
        expect={"modal": "choices", "modal_context": "library-filter"})
    add("library-selection", "library", replay=event_replay().hold(*first_cover, duration=650),
        expect={"selection_mode": True})
    add("detail-selection", "detail", replay=event_replay().hold(*chapter_center, duration=650),
        expect={"chapter_selection_mode": True})
    add("reader-next-page", "reader", replay=Replay(3600).tap(width * 0.15, height * 0.5, wait=2000),
        expect={"reader_image_ready": True, "reader_page": 13, "menu": False})
    for panel, fraction in [("mode", .375), ("picture", .625), ("brightness", .875)]:
        add("reader-" + panel, "reader", replay=reader_replay().tap(width * fraction, bottom),
            expect={"reader_panel": panel, "reader_image_ready": True})
    add("reader-chapters", "reader", "detail", reader_replay().tap(width / 8, bottom),
        {"detail_filter": 0}, current_chapter=True)
    add("detail-bookmarks", "reader", "detail",
        Replay(3600).tap(width * .15, height * .5, wait=1800)
        .tap(width / 2, height / 2, wait=650)
        .tap(width - margin - touch / 2, 40 if large else 36)
        .tap(width / 8, bottom).intent("detail", "filter", "3"),
        {"detail_filter": 3}, bookmarks=True)

    rows = [("default-direction", "reading"), ("frontlight-warmth", "device"),
            ("screen-refresh", "device"), ("manage-sources", "library"),
            ("storage", "library"), ("categories", "library"), ("backup", "general"),
            ("language", "general"), ("about", "general")]
    y, last_group = status + 8, None
    for section, group in rows:
        if group != last_group:
            y += 40 if large else 30
        center = y + touch / 2
        y += touch
        last_group = group
        if section in {"default-direction", "frontlight-warmth", "screen-refresh", "storage", "about"}:
            add("settings-" + section, "settings", replay=event_replay().tap(160, center),
                expect={"settings_section": section})

    add("downloads", "downloads", "settings", expect={"modal": "downloads"})
    add("library-night", "library", replay=event_replay().intent("settings", "set", "night-mode=true"), night=True)
    add("reader-controls-night", "reader", replay=reader_replay().intent("settings", "set", "night-mode=true"),
        expect={"reader_image_ready": True, "menu": True}, night=True)
    add("reader-landscape", "reader", landscape=True, expect={"reader_image_ready": True})
    add("reader-landscape-controls", "reader", landscape=True,
        replay=Replay(3600).tap(height / 2, width / 2),
        expect={"reader_image_ready": True, "menu": True})
    return scenes


def capture_profile(args, device):
    suite = UI.Suite(argparse.Namespace(
        binary=args.binary,
        output_dir=args.output_dir / "captures" / device,
        timeout=45,
    ))
    for scene in scenes_for(device):
        if args.only and scene["id"] not in args.only:
            continue
        if args.skip and scene["id"] in args.skip:
            continue
        case = suite.run(
            scene["id"], scene["screen"], device=device,
            expected_route=scene["route"], replay=scene.get("replay"),
            landscape=scene.get("landscape", False), capture_delay=2200,
        )
        for key, expected in scene["expect"].items():
            suite.expect(case, key, expected)
        if scene.get("night"):
            suite.preference(case, "night_mode", True)
        if scene.get("current_chapter"):
            suite.check(case, "The saved chapter is visible in the chapter list",
                        any(row.get("current") for row in case["state"].get("visible_chapters", [])))
        if scene.get("bookmarks"):
            suite.check(case, "The added bookmark is present in the visible collection",
                        len(case["state"].get("detail_bookmarks", [])) > 0)
        screenshot = Path(case["screenshot"])
        if screenshot.is_file():
            case["png_sha256"] = hashlib.file_digest(screenshot.open("rb"), "sha256").hexdigest()
        UI.save_json(suite.output / (scene["id"] + ".checks.json"), case)
        UI.save_json(suite.output / "capture-report.json", {
            "device": device, "binary_sha256": suite.binary_hash,
            "passed": all(row["passed"] for row in suite.cases), "cases": suite.cases,
        })
    return {
        "device": device, "case_count": len(suite.cases),
        "check_count": sum(len(case["checks"]) for case in suite.cases),
        "passed": all(case["passed"] for case in suite.cases),
        "failures": [case["name"] for case in suite.cases if not case["passed"]],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--skip", nargs="*")
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(executor.map(lambda device: capture_profile(args, device), ["scribe", "pw5"]))
    summary = {"environment": "test-env", "physical_device_used": False, "profiles": reports}
    UI.save_json(args.output_dir / "capture-summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if all(report["passed"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
