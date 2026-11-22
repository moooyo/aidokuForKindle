"""Reanalyze a closed staged cohort using its first submitted Library frame."""

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from statistics import median
import sys
import tarfile


REVIEW = Path(__file__).resolve().parents[2]
PILOT = REVIEW / "startup-fast-20261119/operator/clean-worker/staged-pilot"
FOLLOWUP = Path(__file__).resolve().parent.parent
COHORTS = {"cold-confirmation": ("cold", "confirmation", 10),
           "warm-post-send": ("warm", "screen", 6),
           "warm-rejected-revision": ("warm", "screen", 6)}
sys.path.insert(0, str(PILOT))
sys.path.insert(0, str(FOLLOWUP))

from analyze_pilot import analyze
from cohort import check_protocol
from replay_cold_costs import archive_members, read_trace


def fail_if(condition, reason):
    if condition:
        raise ValueError(reason)


def submitted_time(trace, marks):
    fail_if(not isinstance(trace, dict) or trace.get("clock") != "monotonic_rust_main"
            or not isinstance(trace.get("events"), list), "invalid_trace")
    fail_if(not all(trace.get(key) is True for key in ("enabled", "finished", "success"))
            or trace.get("dropped_events") != 0, "incomplete_trace")
    events = trace["events"]
    fail_if(any(not isinstance(event, dict) for event in events), "invalid_event")

    def single(name):
        matches = [event for event in events if event.get("name") == name]
        fail_if(len(matches) != 1, "invalid_" + name)
        return matches[0]

    initial = single("startup_initial_frame_submitted")
    first = single("startup_library_frame_submitted")
    frames = [event for event in events if event.get("name") == "startup_cover_frame_submitted"]
    fields = ("marker", "frame_sequence", "cover_slots", "cover_images", "cover_pending")
    fail_if(not frames or any(field not in first or field not in frames[0] for field in fields)
            or any(first[field] != frames[0][field] for field in fields), "unbound_library_frame")
    fail_if(type(first.get("marker")) is not int or first["marker"] <= 0
            or type(first.get("frame_sequence")) is not int or first["frame_sequence"] <= 0
            or type(first.get("cover_slots")) is not int or first["cover_slots"] != 7
            or type(first.get("cover_images")) is not int or not 0 <= first["cover_images"] <= 7
            or not isinstance(first.get("cover_pending"), bool),
            "invalid_library_frame")
    fail_if(initial.get("full") is not True or type(initial.get("boottime_us")) is not int
            or type(first.get("boottime_us")) is not int
            or initial["boottime_us"] > first["boottime_us"], "invalid_frame_clock")

    starts = []
    try:
        for line in marks.decode("ascii").splitlines():
            parts = line.split()
            fail_if(len(parts) != 2, "invalid_shell_marker")
            if parts[1] == "staged_foreground_requested":
                fail_if(re.fullmatch(r"[0-9]+\.[0-9]{2}", parts[0]) is None, "invalid_origin_clock")
                starts.append(int(Decimal(parts[0]) * 1000000))
    except (UnicodeDecodeError, InvalidOperation):
        raise ValueError("invalid_shell_marker") from None
    fail_if(len(starts) != 1 or starts[0] > initial["boottime_us"], "invalid_origin")
    return first["boottime_us"] - starts[0]


def read_marks(archive, members, case):
    member = members.get(f"out/{case}/shell.tsv")
    fail_if(member is None or not member.isreg() or not 0 < member.size <= 16384, "invalid_marks")
    stream = archive.extractfile(member)
    fail_if(stream is None, "invalid_marks")
    data = stream.read(16385)
    fail_if(len(data) != member.size, "invalid_marks")
    return data


def replay(attempt, cohort):
    plan = json.loads((attempt.parent / "plan.json").read_text(encoding="utf-8"))
    check_protocol(plan)
    fail_if((plan.get("regime"), plan.get("mode"), plan.get("pairs")) != COHORTS.get(cohort), "unexpected_cohort")
    with tarfile.open(attempt / "output.tar.gz", "r:gz") as archive:
        members = archive_members(archive)
        verdict = analyze(attempt)
        fail_if(verdict.get("valid_staged_cohort") is not True
                or verdict.get("pairs_expected") != plan["pairs"]
                or (plan["mode"] == "confirmation" and verdict.get("confirmed_staged_benefit") is not True),
                "original_cohort_not_accepted")
        rows = []
        for number in range(1, plan["pairs"] + 1):
            values = []
            for side in ("baseline", "candidate"):
                case = f"{side}-{number:02}"
                values.append(submitted_time(read_trace(archive, members, case), read_marks(archive, members, case)))
            rows.append({"pair": number, "order": "AB" if number % 2 else "BA",
                         "baseline_us": values[0], "candidate_us": values[1],
                         "baseline_minus_candidate_us": values[0] - values[1]})
    savings = [row["baseline_minus_candidate_us"] for row in rows]
    return {
        "measurement": "Historical staged foreground request to first submitted Library frame",
        "cohort": cohort, "regime": plan["regime"], "mode": plan["mode"], "pairs": len(rows),
        "endpoint": "startup_library_frame_submitted, not input-ready or all-covers-ready",
        "origin": "staged_foreground_requested immediately before the foreground launcher invocation",
        "clock": "Device CLOCK_BOOTTIME; origin from /proc/uptime at 10 ms resolution; frame timestamp in microseconds",
        "scope": "Closed prior Scribe staged synthetic-shelf experiment; no new device visit",
        "limitations": ["Not measured from a native card release or indexed entry",
                        "Software submission is not proof of optical panel completion or interaction readiness",
                        "The 10 ms origin resolution limits interpretation of small differences and pair wins",
                        "Regimes and candidate builds are distinct cohorts and must not be pooled",
                        "Cohort label is supplied by the operator and must be matched to the historical evidence index",
                        "The original cohort qualification additionally checked cover completion and input readiness; these are not the replay endpoint"],
        "reproduction": "python3 -B docs/review/startup-followup-20261121/rendered-library/replay.py CLOSED_ATTEMPT --cohort COHORT",
        "unit": "microseconds", "savings_convention": "baseline minus candidate",
        "baseline_median_us": median(row["baseline_us"] for row in rows),
        "candidate_median_us": median(row["candidate_us"] for row in rows),
        "median_paired_saving_us": median(savings),
        "candidate_faster_pairs": sum(value > 0 for value in savings),
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt", type=Path)
    parser.add_argument("--cohort", choices=COHORTS, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.attempt, args.cohort), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
