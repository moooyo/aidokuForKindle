#!/usr/bin/env python3
"""Export fixed public fields from a complete R3 isolated feature screen."""

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import sys


PROGRAMS = {
    "candidate-arm": "1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af",
    "launch.sh": "412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675",
    "aidoku-startup-portrait": "bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50",
}
GUARDS = ("success", "lease_released", "same_boot", "native_restored", "entry_restored",
          "installed_and_user_unchanged", "input_roster_unchanged")
COUNTS = ("planned", "observed", "prewarm_plans", "prewarm_ready", "prewarm_attempted_runs",
          "prewarm_usable_runs", "prewarm_zero_attempt_runs", "grace_started_runs",
          "grace_completed_single_frame_runs", "grace_expired", "grace_cancelled")
ENDPOINTS = ("usable", "covers", "exit")
MODES = {"layouts": {"rowwise", "batched", "unmarked"},
         "portrait_engines": {"shell", "native", "unmarked"}}


def number(value):
    assert type(value) in (int, float) and math.isfinite(value)
    return value


def counts(values):
    result = {}
    for key in COUNTS:
        if key in values:
            assert type(values[key]) is int and values[key] >= 0
            result[key] = values[key]
    for key, allowed in MODES.items():
        if key in values:
            assert set(values[key]) <= allowed
            result[key] = {mode: number(value) for mode, value in values[key].items()}
    return result


def summarize(path):
    source = path.read_bytes()
    report = json.loads(source)
    trial = report["trial"]
    assert report["release_generation"] == "R3" and report["frozen_program_members"] == PROGRAMS
    assert trial in ("layout", "prewarm", "grace", "supervisor")
    assert report["phase"] == "screen" and report["workload"] == "warm"
    assert report["complete_pairs"] == 6 and len(report["pairs"]) == 6
    assert report["frozen_payload_verified"] is True
    primary = "usable" if trial in ("layout", "supervisor") else "covers"
    other = "covers" if primary == "usable" else "usable"
    rows = []
    for pair in report["pairs"]:
        assert pair["complete"] is True and pair["order"] in ("AB", "BA")
        rows.append({"round": number(pair["round"]), "order": pair["order"], "milliseconds": {
            key: [number(pair["baseline_ms"][key]), number(pair["candidate_ms"][key]),
                  number(pair["savings_ms"][key])] for key in ENDPOINTS}})
    assert [row["round"] for row in rows] == list(range(1, 7))
    assert [row["order"] for row in rows] == ["AB", "BA"] * 3
    summary = {key: {"median_paired_savings_ms": number(report["comparisons_ms"][key]["paired_savings_ms"]["median"]),
                     "faster_pairs": number(report["comparisons_ms"][key]["improved_pairs"])} for key in ENDPOINTS}
    for key in ENDPOINTS:
        assert abs(median(row["milliseconds"][key][2] for row in rows) - summary[key]["median_paired_savings_ms"]) < 0.002
    checks = {"primary_median_at_least_100_ms": summary[primary]["median_paired_savings_ms"] >= 100,
              "at_least_five_primary_improvements": summary[primary]["faster_pairs"] >= 5,
              "other_median_regression_at_most_50_ms": summary[other]["median_paired_savings_ms"] >= -50}
    assert all(checks.values()) is report["latency_gate_passed"]
    mechanism = report["mechanism"]
    groups = {group: {side: counts(mechanism[group][side]) for side in ("baseline", "candidate")}
              for group in ("timing", "pixels", "warmup")}
    pictures = []
    for image in report["pixels"]:
        assert (image["width"], image["height"], image["status_bar_excluded_pixels"]) == (1860, 2480, 72)
        assert image["right_status_bar_region"] == {"x": 930, "y": 0, "width": 930, "height": 72}
        pictures.append({"width": 1860, "height": 2480, "status_bar_excluded_pixels": 72,
                         "right_status_bar_region": {"x": 930, "y": 0, "width": 930, "height": 72},
                         **{key: image[key] is True for key in ("identical_below_status_bar", "right_status_bar_observable", "identical_right_status_bar")}})
    return {
        "schema_version": 1, "environment": "test-env", "revision": "R3", "trial": trial,
        "phase": "screen", "primary_endpoint": primary, "other_endpoint": other,
        "source_report_sha256": hashlib.sha256(source).hexdigest(),
        "public_exporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "program_sha256": PROGRAMS, "valid": report["valid"] is True,
        "qualification_passed": report["qualification_passed"] is True,
        "latency_gate_passed": report["latency_gate_passed"] is True,
        "source_issue_count": len(report["issues"]), "source_input_failure_count": len(report["input_failures"]),
        "complete_pairs": 6, "numeric_screen_checks": checks, "summary_ms": summary,
        "pair_columns": ["baseline", "candidate", "baseline_minus_candidate"], "pairs": rows,
        "mechanism_verified": mechanism["verified"] is True, "mechanism_observations": groups,
        "batches": [{"mode": batch["mode"], "case_count": len(batch["cases"]),
                     "guards": {key: batch["batch_guards"][key] is True for key in GUARDS}}
                    for batch in report["batches"]], "pixels": pictures,
        "scope": "All six predetermined pairs are retained. Numeric screening, observed feature mechanisms, independent digital pixels, and restoration are separate results. An unpassed numeric screen does not request independent ten-pair confirmation and cannot support a speed benefit. Actual branch exercise must also support any proposed benefit. These are not optical or battery measurements.",
    }


if __name__ == "__main__":
    if sys.platform != "linux" or sys.flags.optimize:
        raise SystemExit("Run through ssh test-env with Python assertions enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.input)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: result[key] for key in ("trial", "valid", "qualification_passed", "latency_gate_passed")}))
