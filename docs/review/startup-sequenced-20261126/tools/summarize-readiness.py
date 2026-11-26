#!/usr/bin/env python3
"""Summarize firmware-only readiness without exporting device clocks or raw logs."""

import argparse
import json
from pathlib import Path
import re

from PIL import Image, ImageChops, ImageOps


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def once(items, reason):
    values = list(items)
    require(len(values) == 1, reason)
    return values[0]


def find_event(trace, name):
    return once((e for e in trace["events"] if e["name"] == name), f"Missing or repeated event: {name}")


def diagnostic(log, name):
    line = once(re.findall(rf"^aidoku: {name} ([^\r\n]+)$", log, re.M), f"Missing or repeated diagnostic: {name}")
    return dict(word.split("=", 1) for word in line.split())


def offset(event, reference):
    return event["boottime_us"] - reference


def case_evidence(case, directory, native_size):
    name = case["name"]
    prefix = directory / name / "startup-library-with-helper"
    summary = {
        "mode": name,
        "passed": case["passed"],
        "error": case["error"],
        "desktop_restored": case["checks"].get("desktop_restored", False),
        "launcher_lock_removed": case["checks"].get("launcher_lock_removed", False),
        "library_settings_library_qmp_touch": case["checks"].get("native_library_settings_round_trip", False),
    }
    if not summary["passed"]:
        return summary
    require(all(summary[key] for key in ("desktop_restored", "launcher_lock_removed", "library_settings_library_qmp_touch")),
            f"Missing passed-case evidence: {name}")
    log = prefix.with_suffix(".log").read_text(encoding="utf-8")
    trace = json.loads(prefix.with_suffix(".trace.json").read_bytes())
    stages = diagnostic(log, "startup_stage_diag")
    transitions = diagnostic(log, "startup_transition")
    confirmed = diagnostic(log, "startup_library_wait")
    submission = diagnostic(log, "startup_library_submitted")
    first = find_event(trace, "startup_initial_frame_submitted")
    input_ready = find_event(trace, "startup_initial_input_ready")
    business = find_event(trace, "startup_library_frame_submitted")
    require(first["full"] is True and first["marker"] == input_ready["marker"]
            and first["frame_sequence"] == input_ready["frame_sequence"] < business["frame_sequence"],
            "The normal loading full-frame and input fence changed")
    require(all(int(line["sequence"]) == business["frame_sequence"] and int(line["marker"]) == business["marker"]
                for line in (transitions, confirmed, submission)), "Driver acknowledgement is not bound to the Library frame")
    require(submission["library"] == "nonempty" and submission["grid"] == "nonempty"
            and int(confirmed["boottime_us"]) >= int(submission["boottime_us"]), "Invalid populated Library endpoint")
    first_send = first["boottime_us"]
    render_cost = sum(first["frame_cost_us"])
    require(0 <= render_cost <= first_send and int(stages["initial_us"]) <= first_send,
            "The loading frame trace has invalid render accounting")
    timings = {
        "window_constructed": offset(find_event(trace, "startup_window_constructed"), first_send),
        "worker_launched": offset(find_event(trace, "startup_metadata_worker_launched"), first_send),
        "metadata_prepared": offset(find_event(trace, "application_prepared"), first_send),
        "metadata_worker_completed": int(transitions["metadata_ready_us"]) - first_send,
        "metadata_received_on_gui": int(transitions["metadata_received_us"]) - first_send,
        "latest_possible_start_of_loading_draw": -render_cost,
        "loading_send": 0,
        "input_fence": offset(input_ready, first_send),
        "fonts_registered": int(transitions["fonts_registered_us"]) - first_send,
        "attachment_to_scene_request": int(transitions["scene_requested_us"]) - int(transitions["attach_enter_us"]),
        "library_send": int(submission["boottime_us"]) - first_send,
        "library_driver_wait": int(confirmed["boottime_us"]) - first_send,
    }
    checks = case["checks"]["readiness_probe"]
    require(checks["early"] == (name == "early") and timings["worker_launched"] == checks["worker_launched_relative_to_loading_send_us"]
            and timings["metadata_prepared"] == checks["metadata_ready_relative_to_loading_send_us"],
            "Mode or readiness log does not match the trace")
    require(timings["metadata_prepared"] <= timings["metadata_worker_completed"] <= timings["metadata_received_on_gui"],
            "Metadata publication ordering is invalid")
    summary.update({
        "relative_timings_us": timings,
        "metadata_ready_by_loading_send": timings["metadata_worker_completed"] <= 0,
        "metadata_missed_even_latest_possible_loading_draw": (
            timings["metadata_worker_completed"] > timings["latest_possible_start_of_loading_draw"]
        ),
        "same_marker_library_driver_acknowledged": True,
    })
    for suffix in (".framebuffer.png", ".settings-framebuffer.png", ".returned-framebuffer.png"):
        with Image.open(prefix.with_suffix(suffix)) as capture:
            require(capture.size == native_size, "Incorrect guest framebuffer size")
    return summary


def images_for(report_path, profile):
    directory = report_path.parent
    early = directory / "early/startup-library-with-helper"
    baseline = directory / "baseline/startup-library-with-helper.framebuffer.png"
    with Image.open(baseline) as first, Image.open(early.with_suffix(".framebuffer.png")) as second:
        require(first.size == second.size, "Profile geometry mismatch")
        roi = (0, 72 if profile == "scribe" else 56, *first.size)
        equals = ImageChops.difference(first.convert("L").crop(roi), second.convert("L").crop(roi)).getbbox() is None
    previews = []
    for suffix in (".framebuffer.png", ".settings-framebuffer.png"):
        with Image.open(early.with_suffix(suffix)) as capture:
            previews.append(ImageOps.contain(capture.convert("L"), (360, 480), Image.Resampling.LANCZOS))
    return equals, previews


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    all_runs = []
    previews = {}
    missing = []
    for kind in ("readiness", "initialized"):
        for profile in ("scribe", "pw6"):
            reports = sorted(args.work.glob(f"metadata-{kind}-{profile}-*/run-*/report.json"))
            if not reports:
                missing.append({"fixture": kind, "profile": profile})
            for i, path in enumerate(reports, 1):
                raw = json.loads(path.read_bytes())
                require(raw["profile"] == profile, "Firmware profile evidence changed")
                native = tuple(raw["native_size"])
                require(native == {"scribe": (1860, 2480), "pw6": (1272, 1696)}[profile], "Wrong native geometry")
                run = {
                    "fixture": "fresh_schema" if kind == "readiness" else "initialized_schema_copy",
                    "profile": profile,
                    "attempt": i,
                    "guest_fixture_cleaned": raw["guest_fixture_cleaned"],
                    "error": raw["error"],
                    "cases": [case_evidence(c, path.parent, native) for c in raw["cases"]],
                }
                compared = [c for c in run["cases"] if c["mode"] in {"baseline", "early"} and c["passed"]]
                require(len(compared) <= 2 and len({c["mode"] for c in compared}) == len(compared), "Duplicate comparison mode")
                if len(compared) == 2:
                    run["library_pixels_equal_below_live_status_bar"], tiles = images_for(path, profile)
                    if kind == "initialized":
                        previews[profile] = tiles
                all_runs.append(run)
    result = {
        "source": "97173d0 with an isolated early-metadata readiness probe",
        "scope": "QEMU Kindle firmware 5.20.1 only; same ARM ELF per profile/fixture, default or early worker launch. No direct Library frame or physical timing.",
        "timing_reference": "Guest CLOCK_BOOTTIME relative to the traced loading SEND. No absolute host or device clocks are exported.",
        "missing_comparisons": missing,
        "runs": all_runs,
    }
    with args.json.open("x", encoding="utf-8") as file:
        json.dump(result, file, indent=2)
        file.write("\n")
    if args.preview and len(previews) == 2:
        grid = Image.new("L", (724, 964), 255)
        for y, profile in zip((0, 484), ("scribe", "pw6"), strict=True):
            for x, frame in zip((0, 364), previews[profile], strict=True):
                grid.paste(frame, (x, y))
        with args.preview.open("xb") as file:
            grid.save(file, format="PNG")
    for run in all_runs:
        print(run["fixture"], run["profile"], run["attempt"], "cleaned=" + str(run["guest_fixture_cleaned"]))
        for case in run["cases"]:
            p = case.get("relative_timings_us", {})
            print(" ", case["mode"], "pass" if case["passed"] else case["error"],
                  *(round(p[key] / 1000, 3) for key in ("worker_launched", "metadata_worker_completed", "latest_possible_start_of_loading_draw", "fonts_registered") if key in p))


if __name__ == "__main__":
    main()
