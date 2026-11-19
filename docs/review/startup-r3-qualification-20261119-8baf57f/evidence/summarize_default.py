#!/usr/bin/env python3
"""Export fixed public fields from an independently replayed R3 default report."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


GUARDS = (
    "success", "lease_released", "same_boot", "native_restored",
    "entry_restored", "installed_and_user_unchanged", "input_roster_unchanged",
)
ENDPOINTS = ("usable", "covers", "exit")
PROGRAMS = {
    "candidate-arm": "1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af",
    "launch.sh": "412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675",
    "aidoku-startup-portrait": "bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50",
}


def number(value):
    assert type(value) in (int, float) and math.isfinite(value)
    return value


def summarize(path):
    source = path.read_bytes()
    report = json.loads(source)
    assert report["release_generation"] == "R3"
    assert report["trial"] == "combined" and report["phase"] == "screen"
    assert report["workload"] in ("warm", "cold")
    assert report["frozen_program_members"] == PROGRAMS
    assert all(report[key] is True for key in (
        "valid", "default_guard_passed", "qualification_passed", "frozen_payload_verified",
    ))
    assert report["issues"] == report["input_failures"] == []
    assert report["complete_pairs"] == 6 and report["latency_gate_passed"] is None
    assert len(report["batches"]) == 2 and len(report["pixels"]) == 1
    assert sorted(batch["mode"] for batch in report["batches"]) == ["qualify", "screen"]
    batches = []
    for batch in report["batches"]:
        guards = {key: batch["batch_guards"][key] is True for key in GUARDS}
        assert all(guards.values())
        batches.append({"mode": batch["mode"], "case_count": len(batch["cases"]), "guards": guards})
    image = report["pixels"][0]
    assert (image["width"], image["height"], image["status_bar_excluded_pixels"]) == (1860, 2480, 72)
    region = {"x": 930, "y": 0, "width": 930, "height": 72}
    assert image["right_status_bar_region"] == region
    pixel_keys = ("identical_below_status_bar", "right_status_bar_observable", "identical_right_status_bar")
    assert all(image[key] is True for key in pixel_keys)
    pairs = []
    for pair in report["pairs"]:
        assert pair["complete"] is True and pair["order"] in ("AB", "BA")
        pairs.append({"round": number(pair["round"]), "order": pair["order"],
                      "milliseconds": {key: [number(pair["baseline_ms"][key]), number(pair["candidate_ms"][key]),
                                             number(pair["savings_ms"][key])] for key in ENDPOINTS}})
    return {
        "schema_version": 1, "revision": "R3", "environment": "test-env",
        "source_report_sha256": hashlib.sha256(source).hexdigest(),
        "public_exporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "workload": report["workload"], "valid": True, "default_guard_passed": True,
        "independent_pixel_qualification_passed": True, "latency_gate_passed": None,
        "complete_pairs": 6, "program_sha256": PROGRAMS, "batches": batches,
        "pixels": {"width": 1860, "height": 2480, "status_bar_excluded_pixels": 72,
                   "right_status_bar_region": region, **{key: True for key in pixel_keys}},
        "median_paired_savings_ms": {key: number(report["comparisons_ms"][key]["paired_savings_ms"]["median"]) for key in ENDPOINTS},
        "faster_pairs": {key: number(report["comparisons_ms"][key]["improved_pairs"]) for key in ENDPOINTS},
        "pair_columns": ["baseline", "candidate", "baseline_minus_candidate"], "pairs": pairs,
        "scope": "The original six timing pairs are bound to a separate physical pixel qualification. No timing was rerun. Body pixels below the clock and the nonuniform right status bar matched. Both batches passed the retained restoration and lease-release checks. This is a default regression guard and digital framebuffer comparison, not a confirmed speedup or optical measurement.",
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
    print(json.dumps({key: result[key] for key in ("workload", "valid", "default_guard_passed", "independent_pixel_qualification_passed")}))
