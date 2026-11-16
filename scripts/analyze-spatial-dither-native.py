#!/usr/bin/env python3
"""Compare native Reader captures with qualified software dithering outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from PIL import Image, ImageChops


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new evidence path")
    report = {"scope": "Physical Scribe application snapshots and accepted native refresh requests. No optical or new post-MDP measurement.",
        "reader": [], "settings": [], "passed": False}
    for mode, method, enabled, saved in [
        ("reader-bayer8", "production_bayer8", True, "bayer8"),
        ("reader-floyd", "production_fs_q8", True, "floyd_steinberg"),
        ("reader-off", "production_off", False, "bayer8"),
        ("reader-floyd-cold", "production_fs_q8", True, "floyd_steinberg"),
    ]:
        state = json.loads((args.captures / (mode + ".state.json")).read_text())
        expected_bytes = (args.oracle / (method + ".raw")).read_bytes()
        expected = Image.frombytes("L", (1860, 2480), expected_bytes)
        actual = Image.open(args.captures / (mode + ".png")).convert("L")
        assert actual.size == expected.size
        difference = ImageChops.difference(actual, expected)
        changed = sum(difference.histogram()[1:])
        assert changed == 0, (mode, changed)
        assert state["preferences"]["dither"] == enabled
        assert state["preferences"]["dither_method"] == saved
        assert state["reader_image_ready"] and not state["dither_pending"]
        assert not state["reader_hardware_y4_requested"] and not state["reader_hardware_y4_effective"]
        assert state["reader_page"] == 1 and state["reader_total"] == 3
        if mode != "reader-floyd-cold":
            assert state["reader_next_frame"]["hits"] >= 1
        report["reader"].append({"case": mode, "method": saved if enabled else "off", "changed_pixels": changed,
            "width": actual.width, "height": actual.height, "raw_sha256": hashlib.sha256(expected_bytes).hexdigest(),
            "y4_requested": state["reader_hardware_y4_requested"], "y4_effective": state["reader_hardware_y4_effective"],
            "hardware_y4_available": state["hardware_y4_available"], "next_frame_hits": state["reader_next_frame"]["hits"]})
    for mode, enabled, saved in [
        ("settings-bayer8", True, "bayer8"), ("settings-floyd", True, "floyd_steinberg"),
        ("settings-off", False, "floyd_steinberg"), ("settings-off-restart", False, "floyd_steinberg"),
    ]:
        state = json.loads((args.captures / (mode + ".state.json")).read_text())
        assert state["route"] == "settings" and state["settings_section"] == "screen-refresh"
        assert state["preferences"]["dither"] == enabled and state["preferences"]["dither_method"] == saved
        report["settings"].append({"case": mode, "enabled": enabled, "saved_method": saved, "passed": True})

    # Native coordinates are twice the logical Scribe control geometry.
    box = (1262, 492, 1776, 596)
    held = Image.open(args.captures / "button-held.png").convert("RGB").crop(box)
    released = Image.open(args.captures / "button-released.png").convert("RGB").crop(box)
    assert ImageChops.difference(held, released).getbbox() is None
    report["selected_button_changed_pixels"] = 0
    pattern = re.compile(r"native-refresh accepted marker=(\d+) waveform=(\d+) mode=(\d+) flags=(0x[0-9a-f]+) region=([\d,]+) swipe=([\d,]+)")

    def traces(mode: str, after_ready: bool = False) -> list[dict]:
        text = (args.captures / (mode + ".log")).read_text()
        if after_ready:
            text = text[text.index("native window ready;"):]
        return [dict(marker=int(m[0]), waveform=int(m[1]), mode=int(m[2]), flags=m[3], region=m[4], swipe=m[5]) for m in pattern.findall(text)]

    report["button_traces"] = {mode: traces(mode, True) for mode in ["button-held", "button-released", "settings-floyd"]}
    assert all(entry["waveform"] == 257 and entry["mode"] == 0 and entry["swipe"] == "0,0"
        for entries in report["button_traces"].values() for entry in entries)
    fast = json.loads((args.captures / "fast-hold.state.json").read_text())
    assert fast["reader_fast_frame"] and fast["reader_page"] == 8
    assert fast["preferences"]["refresh_every"] == 1
    entries = traces("fast-hold")
    entry_clear = max(index for index, item in enumerate(entries) if item["waveform"] == 2 and item["mode"] == 1)
    turns = entries[entry_clear + 1:]
    assert len(turns) == 7
    assert all(item["waveform"] == 1 and item["mode"] == 0 and item["swipe"] == "0,0" for item in turns)
    report["fast"] = {"final_page": 8, "saved_refresh_interval": 1, "page_updates": turns,
        "automatic_updates_after_last_turn": 0, "scheduled_idle_after_last_input_ms": 9200,
        "next_frame_hits": fast["reader_next_frame"]["hits"]}
    report["passed"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": True, "reader_cases": len(report["reader"]), "settings_cases": len(report["settings"]),
        "changed_reader_pixels": 0, "fast_du_updates": len(turns)}))


if __name__ == "__main__":
    main()
