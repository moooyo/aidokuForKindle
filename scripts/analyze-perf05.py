#!/usr/bin/env python3
"""Summarize isolated Scribe Perf05 traces without accessing a device."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import statistics

from PIL import Image, ImageChops


CASES = ["perf05-steady", "perf05-burst", "perf05-steady-warm"]


def summarize(values):
    return None if not values else {"count": len(values), "median_ms": round(statistics.median(values), 3),
        "min_ms": round(min(values), 3), "max_ms": round(max(values), 3)}


def trial(directory, case):
    trace = json.loads((directory / (case + ".timing.json")).read_text())
    assert trace["success"] and trace["finished"] and trace["dropped_events"] == 0
    events = trace["events"]
    actions = {item["action_id"]: item for item in events if item["name"] == "action_received"}
    state = json.loads((directory / (case + ".state.json")).read_text())
    assert state["reader_visible_pages"] == [7, 8] and state["reader_fast_frame"]
    rows = []
    for request in sorted({item["request_id"] for item in events if "request_id" in item}):
        sequence = [item for item in events if item.get("request_id") == request]
        action_id = next((item.get("action_id") for item in sequence if item.get("action_id")), None)
        action = actions.get(action_id)
        origin = action["at_us"] if action else sequence[0]["at_us"]
        stages = {}
        for item in sequence:
            if "duration_us" in item:
                stages.setdefault(item["name"], []).append(round(item["duration_us"] / 1000, 3))
        row = {"request_id": request, "action_kind": action["action_kind"] if action else "startup",
            "input_origin": action.get("input_origin") if action else None,
            "prepared_frame_hit": any(item["name"] == "prepared_frame_hit" for item in sequence), "stages_ms": stages}
        for boundary in ["worker_finished", "ui_image_ready", "send_accepted"]:
            event = next((item for item in sequence if item["name"] == boundary), None)
            row[boundary + "_ms"] = None if event is None else round((event["at_us"] - origin) / 1000, 3)
        rows.append(row)
    turns = [row for row in rows if row["action_kind"] == "next" and row["send_accepted_ms"] is not None]
    return {"requests": rows, "effective_turns": len(turns),
        "turn_latency": summarize([row["send_accepted_ms"] for row in turns]),
        "prepared_turn_latency": summarize([row["send_accepted_ms"] for row in turns if row["prepared_frame_hit"]]),
        "ui_pixel_preparation": summarize([item["duration_us"] / 1000 for item in events if item["name"] == "display_pixels_prepared"]),
        "prepared_frame_hits": state["reader_next_frame"]["hits"],
        "sleep_cover": state["sleep_screen"], "preferences": state["preferences"]}


def navigation(directory):
    held = Image.open(directory / "nav-pressed.png").convert("RGB").crop((0, 2320, 1860, 2480))
    idle = Image.open(directory / "nav-released.png").convert("RGB").crop((0, 2320, 1860, 2480))
    updates = {}
    for case in ["nav-pressed", "nav-released", "nav-taps"]:
        text = (directory / (case + ".log")).read_text().split("native window ready;")[-1]
        updates[case] = sum(bool(re.search(r"region=\d+,2323,\d+,157 ", line))
            for line in text.splitlines() if "native-refresh accepted" in line)
    return {"held_pixels_unchanged": ImageChops.difference(held, idle).getbbox() is None,
        "isolated_tab_updates_after_window_ready": updates}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new evidence path")
    result = {"scope": "Physical Scribe, Perf05 High Resolution PNG, existing Fast and two-page settings. Software action dispatch to driver SEND acceptance; not optical panel completion.",
        "fixture_sha256": "0e11719838c8da38388be9f3f389fa59ce671625bb033b1707842bce520cf076",
        "baseline": {}, "final": {}, "equal_pixels": {}, "passed": False}
    for case in CASES:
        result["baseline"][case] = trial(args.baseline, case)
        result["final"][case] = trial(args.candidate, case)
        original = Image.open(args.baseline / (case + ".png")).convert("RGB")
        current = Image.open(args.candidate / (case + ".png")).convert("RGB")
        assert original.size == current.size == (1860, 2480)
        assert ImageChops.difference(original, current).getbbox() is None
        result["equal_pixels"][case] = True
        assert result["final"][case]["preferences"]["sleep_cover"]
        assert result["final"][case]["sleep_cover"]["enabled"]
        assert result["final"][case]["sleep_cover"]["full_resolution"]
    result["cover_disabled_control"] = trial(args.control, "perf05-steady")
    assert not result["cover_disabled_control"]["preferences"]["sleep_cover"]
    result["navigation_before"] = navigation(args.baseline)
    result["navigation_after"] = navigation(args.candidate)
    assert result["navigation_after"]["held_pixels_unchanged"]
    assert all(value == 0 for value in result["navigation_after"]["isolated_tab_updates_after_window_ready"].values())
    result["limits"] = ["The eight-image book has four two-page windows; taps after the last window do not create a render request.",
        "One burst request is superseded; only delivered frames are included in latency summaries.",
        "The OS file cache was not flushed. First-visit rendering and process restart are identified separately.",
        "Stages may overlap other work and are not an additive partition of full latency."]
    result["passed"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": True, "first_visit_before": result["baseline"]["perf05-steady"]["turn_latency"],
        "first_visit_after": result["final"]["perf05-steady"]["turn_latency"],
        "warm_before": result["baseline"]["perf05-steady-warm"]["turn_latency"],
        "warm_after": result["final"]["perf05-steady-warm"]["turn_latency"]}, indent=2))


if __name__ == "__main__":
    main()
