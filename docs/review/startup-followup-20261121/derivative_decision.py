"""Apply the frozen two-shelf decision to successful production-path trial reports."""

import argparse
import json
from pathlib import Path
from statistics import median


STAGES = ("reservation_ns", "encode_ns", "validation_ns", "commit_ns", "remember_ns")
ARMS = ("control", "no_filter")


def summarize(report):
    if (report["schema"], report["filesystem"], report["image_count"]) != (1, "xfs", 6):
        raise ValueError("Not a six-thumbnail production-path XFS trial")
    pairs = report["pairs"]
    if len(pairs) != 10 or [pair["order"] for pair in pairs] != ["AB", "BA"] * 5:
        raise ValueError("Expected ten alternating pairs, excluding the warmup")
    if report["warmup"]["order"] != "AB":
        raise ValueError("Missing separate control and candidate warmups")
    rows = report["warmup"]["filter_rows"]
    if (any(len(rows[arm]) != 5 or any(type(count) is not int or count < 0 for count in rows[arm])
            for arm in ARMS) or not any(rows["control"][1:])
            or sum(rows["control"]) != rows["no_filter"][0] or any(rows["no_filter"][1:])):
        raise ValueError("The warmup scanlines do not verify default versus unfiltered output")
    sizes = {arm: report["warmup"][arm]["png_bytes"] for arm in ARMS}
    for pair in [report["warmup"], *pairs]:
        for arm in ARMS:
            visit = pair[arm]
            if (not all(type(visit[key]) is int and visit[key] > 0 for key in (*STAGES, "total_ns", "reopen_ns"))
                    or visit["total_ns"] != sum(visit[key] for key in STAGES)
                    or len(visit["png_bytes"]) != 6 or visit["png_bytes"] != sizes[arm]
                    or not all(type(size) is int and size > 0 for size in visit["png_bytes"])):
                raise ValueError("Inconsistent duration or non-reproducible encoded sizes")

    def median_ms(arm, key):
        return median(pair[arm][key] for pair in pairs) / 1e6

    cold = [pair["control"]["total_ns"] - pair["no_filter"]["total_ns"] for pair in pairs]
    relative = median(100 * saving / pair["control"]["total_ns"] for saving, pair in zip(cold, pairs))
    warm_regression = median(pair["no_filter"]["reopen_ns"] - pair["control"]["reopen_ns"] for pair in pairs) / 1e6
    wins = sum(saving > 0 for saving in cold)
    guards = {
        "cold_relative_saving": relative >= 10,
        "cold_wins": wins >= 9,
        "shelf_bytes": 100 * sum(sizes["no_filter"]) <= 105 * sum(sizes["control"]),
        "individual_bytes": all(100 * new <= 120 * old for old, new in zip(sizes["control"], sizes["no_filter"])),
        "reopen": warm_regression <= max(1.0, 0.1 * median_ms("control", "reopen_ns")),
    }
    return {
        "passes": all(guards.values()), "guards": guards, "wins": wins,
        "paired_cold_saving_ms": median(cold) / 1e6, "paired_cold_saving_percent": relative,
        "paired_reopen_regression_ms": warm_regression,
        "per_image_bytes": sizes, "warmup_filter_rows": rows,
        "median_ms": {arm: {key.removesuffix("_ns"): median_ms(arm, key)
                            for key in (*STAGES, "total_ns", "reopen_ns")} for arm in ARMS},
        "pairs_ms": [[pair[arm][key] / 1e6 for key in ("total_ns", "reopen_ns") for arm in ARMS] for pair in pairs],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cold_shelf", type=Path)
    parser.add_argument("structured_shelf", type=Path)
    args = parser.parse_args()
    reports = {name: json.loads(path.read_bytes()) for name, path in vars(args).items()}
    if reports["cold_shelf"] == reports["structured_shelf"]:
        parser.error("The same report was supplied for both shelves")
    results = {name: summarize(report) for name, report in reports.items()}
    print(json.dumps({"select_filter": all(result["passes"] for result in results.values()),
                      "pairs_ms_columns": ["control_cold", "no_filter_cold", "control_reopen", "no_filter_reopen"],
                      "shelves": results}, indent=2, allow_nan=False))
