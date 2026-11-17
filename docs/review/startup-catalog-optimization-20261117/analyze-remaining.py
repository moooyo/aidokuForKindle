#!/usr/bin/env python3
"""Export bounded timing budgets from the existing private six-pair batch.

Run only on test-env. No device connection is made. Exported records omit
process IDs, catalog identities, user metadata, absolute clocks and screenshots.
Intervals describe measured work, not the saving of an untested intervention.
"""

import argparse
import json
import platform
import statistics
from pathlib import Path


RUN_ID = "final-installed-inputs-4f83ee4a"


def read_events(path):
    record = json.loads(path.read_text())
    if not record["success"] or record["dropped_events"]:
        raise ValueError(f"Incomplete timing evidence: {path.name}")
    return record["events"]


def first(events, name):
    return next(event for event in events if event["name"] == name)


def at(events, name):
    return first(events, name)["at_us"] / 1000


def interval(events, start, end):
    return at(events, end) - at(events, start)


def summarize(records, keys):
    result = {}
    for key in keys:
        values = [record[key] for record in records]
        result[key] = {
            "mean_ms": round(statistics.mean(values), 3),
            "median_ms": round(statistics.median(values), 3),
            "min_ms": round(min(values), 3),
            "max_ms": round(max(values), 3),
        }
    return result


def rounded(record):
    return {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in record.items()
    }


def analyze_case(directory):
    events = read_events(directory / "application.json")
    portrait = read_events(directory / "portrait.json")
    shell = {
        name: float(stamp) * 1000
        for stamp, name in (
            line.split() for line in (directory / "shell.tsv").read_text().splitlines()
        )
    }
    anchor = first(events, "platform_initialized")
    process_start = (anchor["boottime_us"] - anchor["at_us"]) / 1000
    phases = {
        "dispatch": shell["document_entry"] - shell["native_open_requested"],
        "launcher_and_process_start": process_start - shell["document_entry"],
        "platform": at(events, "platform_initialized"),
        "backend": interval(events, "platform_initialized", "startup_backend_installed"),
        "window": interval(events, "startup_backend_installed", "startup_window_shown"),
        "initial_submit": interval(events, "startup_window_shown", "startup_initial_frame_submitted"),
        "initial_barrier": interval(events, "startup_initial_frame_submitted", "startup_initial_input_ready"),
        "remaining_fonts_attach": interval(events, "startup_initial_input_ready", "application_attached"),
        "view_sync": interval(events, "application_attached", "startup_ready"),
        "first_render_submit": interval(events, "startup_ready", "startup_library_frame_submitted"),
        "cover_tail": interval(events, "startup_library_frame_submitted", "startup_visible_covers_settled"),
    }
    jobs = []
    current = None
    for event in events:
        name = event["name"]
        stamp = event["at_us"] / 1000
        if name == "cover_preparation_started":
            if current is not None:
                raise ValueError("The single-worker evidence overlaps cover jobs")
            current = {"start": stamp}
        elif name == "cover_local_derivative_reused" and current is not None:
            current["cache"] = stamp
        elif name == "cover_preparation_finished" and current is not None:
            current["finish"] = stamp
            jobs.append(current)
            current = None
    if current is not None or len(jobs) != 6 or any("cache" not in job for job in jobs):
        raise ValueError("The warm six-cover fixture is not fully represented")

    first_frame = first(events, "startup_library_frame_submitted")
    complete_frame = first(events, "startup_visible_covers_settled")
    cover_frames = [
        event for event in events
        if event["name"] == "startup_cover_frame_submitted"
        and event["at_us"] <= complete_frame["at_us"]
    ]
    row = {
        "case": directory.name,
        "request_to_document_ms": phases["dispatch"],
        "entry_to_worker_ms": shell["launcher_worker"] - shell["launcher_entry"],
        "worker_to_observer_ms": shell["observer_start"] - shell["launcher_worker"],
        "observer_start_to_ready_ms": shell["observer_ready"] - shell["observer_start"],
        "observer_ready_to_portrait_ms": shell["portrait_start"] - shell["observer_ready"],
        "portrait_outer_ms": shell["portrait_done"] - shell["portrait_start"],
        "portrait_inner_ms": at(portrait, "process_finished"),
        "display_takeover_ms": shell["display_takeover_done"] - shell["display_takeover_start"],
        "cover_job_ms": sum(job["finish"] - job["start"] for job in jobs),
        "cover_cache_path_ms": sum(job["cache"] - job["start"] for job in jobs),
        "cover_post_cache_ms": sum(job["finish"] - job["cache"] for job in jobs),
        "cover_admission_ms": jobs[0]["start"] - first_frame["at_us"] / 1000,
        "cover_queue_gaps_ms": sum(right["start"] - left["finish"] for left, right in zip(jobs, jobs[1:])),
        "cover_count": len(jobs),
        "final_job_to_publish_ms": at(events, "startup_final_cover_publication") - jobs[-1]["finish"],
        "final_publish_to_send_ms": interval(events, "startup_final_cover_publication", "startup_visible_covers_settled"),
        "metadata_prepare_ms": interval(events, "startup_workers_started", "application_prepared"),
        "preparation_end_to_input_ready_ms": interval(events, "application_prepared", "startup_initial_input_ready"),
        "fonts_load_ms": interval(events, "startup_workers_started", "fonts_loaded"),
        "input_ready_to_fonts_registered_ms": interval(events, "startup_initial_input_ready", "fonts_registered"),
        "first_frame_render_ms": first_frame["frame_cost_us"][0] / 1000,
        "cover_frame_render_ms": complete_frame["frame_cost_us"][0] / 1000,
        "cover_frame_prior_buffer_settle_ms": complete_frame["frame_cost_us"][1] / 1000,
        "library_frames_through_complete_covers": len(cover_frames),
        "startup_activity_pulses": sum(event["name"] == "startup_activity_pulse" for event in events),
    }
    row["portrait_surrounding_ms"] = row["portrait_outer_ms"] - row["portrait_inner_ms"]
    row["cover_timeline"] = [
        {
            "event": event["name"],
            "after_first_library_ms": round((event["at_us"] - first_frame["at_us"]) / 1000, 3),
            **{
                key: event[key] for key in ("cover_images", "cover_pending", "frame_cost_us")
                if key in event
            },
        }
        for event in events
        if event["name"] in (
            "cover_preparation_finished", "startup_final_cover_publication",
            "startup_cover_frame_submitted", "startup_visible_covers_settled",
        )
    ]
    total = sum(phases.values())
    cost = {
        "case": directory.name,
        "phases_ms": rounded(phases),
        "total_ms": round(total, 3),
    }
    return rounded(row), cost


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Private batch outputs/out directory")
    parser.add_argument("--output", type=Path, required=True, help="Public evidence directory")
    arguments = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("Run this analysis through ssh test-env")
    records, costs = zip(*(
        analyze_case(arguments.input / f"candidate-{index:02}") for index in range(1, 7)
    ))
    evidence = {
        "run_id": RUN_ID,
        "scope": "Analysis of the six existing warm offline candidate timing records. No extra device access or speedup intervention. Shell events use 10 ms-resolution uptime; application events use monotonic microseconds. Event intervals are budgets, not removable overhead.",
        "summary": summarize(records, [key for key in records[0] if key.endswith("_ms")]),
        "records": records,
    }
    cost_map = {
        "run_id": RUN_ID,
        "method": "Disjoint monotonic intervals; means add to the mean total. Independent medians must not be added as a speedup forecast. Mixed-clock anchoring can differ from directly observed endpoints by microseconds.",
        "summary": summarize([cost["phases_ms"] for cost in costs], list(costs[0]["phases_ms"])),
        "mean_total_ms": round(statistics.mean(cost["total_ms"] for cost in costs), 3),
        "records": costs,
    }
    arguments.output.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("remaining-opportunity-evidence.json", evidence), ("final-cost-map.json", cost_map),
    ):
        (arguments.output / name).write_text(json.dumps(value, indent=2) + "\n")
    print(json.dumps({"cases": len(records), "mean_total_ms": cost_map["mean_total_ms"]}))


if __name__ == "__main__":
    main()
