#!/usr/bin/env python3
"""Read retained R3 reports and traces on test-env, writing only public JSON to stdout.

Usage: python3 -I replay_r3.py REPORTS_DIR BENCH_DIR
REPORTS_DIR contains default-{warm,cold}-with-pixels.json; BENCH_DIR contains
base-{warm,cold}/private/<run from report>/outputs/out/. No device connection.
"""

import argparse
from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median
import sys


REPORT_HASH = {
    "warm": "97d039593fd3549dfbbd66631fef7e71daa99d35eb015fa1900c8afdcbab3147",
    "cold": "a42cb65f6b717320b7ea74e85e4e7452ea03435391c0db819d67f00a6b8fc02a",
}
STAGES = ("request_to_rust_entry", "rust_entry_to_initial_send", "checked_initial_send_to_input",
          "initial_input_to_ready", "ready_to_first_library_send", "first_library_send_to_all_covers")
ENDPOINTS = ("ready", "first", "usable", "first_with_any_cover", "covers")


def summarize(report_dir, bench_dir, regime):
    raw = (report_dir / f"default-{regime}-with-pixels.json").read_bytes()
    assert sha256(raw).hexdigest() == REPORT_HASH[regime], "changed source report"
    report = json.loads(raw)
    assert report["valid"] and report["default_guard_passed"] and report["complete_pairs"] == 6
    batch, = [b for b in report["batches"] if b["mode"] == "screen"]
    root = bench_dir / f"base-{regime}" / "private" / batch["run_id"] / "outputs" / "out"
    stages, costs, endpoints = defaultdict(list), defaultdict(list), defaultdict(list)
    raw_rows, job_rows, frame_counts, first_slot_counts = [], [], [], []
    for pair in report["pairs"]:
        assert pair["complete"] and pair["round"] == len(raw_rows) + 1
        name = f"candidate-{pair['round']:02d}"
        case, = [c for c in batch["cases"] if c["case"] == name]
        lines = (root / name / "shell.tsv").read_text("ascii").splitlines()
        ts, = [r.split()[0] for r in lines if r.endswith(" native_open_requested")]
        start = int(Decimal(ts) * 1_000_000)
        events = defaultdict(list)
        for event in json.loads((root / name / "application.json").read_bytes())["events"]:
            events[event["name"]].append(event)

        def boot(key):
            event, = events[key]
            return event["boottime_us"]

        frames = events["startup_cover_frame_submitted"]
        assert frames[0]["cover_images"] == 0 and frames[-1]["cover_images"] == 7
        assert all(f["cover_slots"] == 7 for f in frames)
        assert all(a["frame_sequence"] < b["frame_sequence"] for a, b in zip(frames, frames[1:]))
        assert len(frames) == case["diagnostics"]["library_frames_through_covers"]
        assert frames[-1]["frame_sequence"] == events["startup_visible_covers_settled"][0]["frame_sequence"]
        first_art = next(f for f in frames if f["cover_images"] > 0)
        first_slot_counts.append(first_art["cover_images"])
        frame_counts.append(len(frames))
        initial = boot("startup_initial_frame_submitted")
        process, = events["process_entry"]
        assert process["at_us"] == 0
        rust_entry = initial - events["startup_initial_frame_submitted"][0]["at_us"]
        gate, ready = boot("startup_initial_input_ready"), boot("startup_ready")
        first, settled = boot("startup_library_frame_submitted"), boot("startup_visible_covers_settled")
        times = [start, rust_entry, initial, gate, ready, first, settled]
        assert times == sorted(times)
        for key, a, b in zip(STAGES, times, times[1:]):
            stages[key].append((b - a) / 1000)
        usable = max(first, boot("startup_library_input_ready"))
        row = [(t - start) / 1000 for t in (ready, first, usable, first_art["boottime_us"], settled)]
        for key, value in zip(ENDPOINTS, row):
            endpoints[key].append(value)
            if key != "first_with_any_cover":
                assert value == pair["candidate_ms"][key] == case["endpoints_ms"][key]
        raw_rows.append(row)

        cache = case["cache"]
        assert (cache["visible_items"], cache["ready_slots"], cache["hits"], cache["misses"]) == (
            6, 7, 6 if regime == "warm" else 0, 0 if regime == "warm" else 6)
        points = ["cover_preparation_started"]
        if regime == "cold":
            points += ["cover_cold_validation_combined_started", "cover_cold_decode_validated"]
        points += ["cover_local_derivative_" + ("prepared" if regime == "cold" else "reused"),
                   "cover_render_ready", "cover_preparation_finished"]
        assert all(len(events[k]) == 6 for k in points)
        jobs = [[e["at_us"] for e in job] for job in zip(*(events[k] for k in points))]
        assert all(job == sorted(job) for job in jobs)
        assert all(a[-1] <= b[0] for a, b in zip(jobs, jobs[1:]))
        amounts = [(job[-1] - job[0]) / 1000 for job in jobs]
        assert amounts == [j["duration_ms"] for j in cache["jobs"]]
        job_rows.append(amounts)
        costs["six_serial_jobs"].append(sum(amounts))
        for i, (a, b) in enumerate(zip(points, points[1:])):
            costs[f"{a}_to_{b}"].append(sum(j[i+1] - j[i] for j in jobs) / 1000)
        published, = events["startup_final_cover_publication"]
        costs["last_job_to_final_publication"].append((published["at_us"] - jobs[-1][-1]) / 1000)
        costs["final_publication_to_submitted"].append((settled - published["boottime_us"]) / 1000)

    def summary(columns, fn):
        return {key: round(fn(vals), 3) for key, vals in columns.items()}

    phases = report["phase_comparisons_ms"]
    return {
        "source_report_sha256": REPORT_HASH[regime], "sample_count": len(raw_rows),
        "derivative_hits_of_six_per_trial": 6 if regime == "warm" else 0,
        "submitted_library_frames_per_trial": frame_counts,
        "image_slots_at_first_nonempty_frame_per_trial": first_slot_counts,
        "samples_ms": raw_rows, "endpoint_medians_ms": summary(endpoints, median),
        "disjoint_means_ms": summary(stages, mean), "disjoint_medians_ms": summary(stages, median),
        "nested_phase_medians_ms": {key: phases[key]["candidate"]["median"] for key in (
            "request_to_launcher_bridge", "portrait_outer", "display_takeover", "first_library_view", "first_render")},
        "serial_job_medians_ms_by_ordinal": [round(median(v), 3) for v in zip(*job_rows)],
        "cover_budget_medians_ms": summary(costs, median),
    }


if __name__ == "__main__":
    if sys.flags.optimize:
        raise SystemExit("Run with assertions enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    parser.add_argument("batches", type=Path)
    args = parser.parse_args()
    result = {
        "source": "R3 product 8baf57f; original indexed-request default protection trials, replayed on test-env.",
        "sample_columns_ms": ENDPOINTS,
        "scope": [
            "Each cache regime uses the six R3 arms of previously qualified balanced pairs. No new launches, warmups, captures, reboot, cover downloads or optical measurement contribute.",
            "Start: device shell marker immediately before native indexed file-URI dispatch, not a screen tap. End: driver SEND acceptance, not optical settling. Usable=max(first Library SEND, software input readiness). First with any cover requires at least one populated image slot in a submitted frame.",
            "Separate benchmark data: six distinct local covers, seven displayed slots. Every first Library frame has zero cover images. Cold means empty thumbnail cache, not cold OS boot.",
            "Shell clock is /proc/uptime (10 ms granularity); app endpoints are CLOCK_BOOTTIME. Rust entry is estimated from initial SEND boottime minus its elapsed process time. Means of the six disjoint intervals add to mean request-to-covers; medians and nested diagnostics are not additive.",
            "The combined cold validation marker interval includes byte identity checks, decoding, resizing and validation, not just decoder CPU. These are elapsed work budgets, not achievable savings or charge/energy measurements.",
        ],
        **{regime: summarize(args.reports, args.batches, regime) for regime in REPORT_HASH},
    }
    print(json.dumps(result, indent=2, allow_nan=False))
