"""Replay one Scribe diagnostic package: one excluded warmup, then four fixed stock-card visits."""

import argparse
import json
import os
from pathlib import Path
import re
from statistics import median
import tempfile


SLOTS = 5
POINTS = ("worker", "gui", "main", "initial", "send")
PRIMARY = ("release_to_worker", "worker_to_main", "main_to_send")
NESTED = ("worker_to_gui", "gui_to_main", "main_to_initial", "initial_to_send")
CLOSE_FIELDS = {"ordinary_exit", "native_restored", "close", "absent", "connections_closed",
                "owner_and_token_checked", "released"}
LIMIT_US = 150_000_000


def require(ok, error):
    if not ok:
        raise ValueError(error)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate field")
        result[key] = value
    return result


def validate_cohort(cohort):
    require(isinstance(cohort, str) and re.fullmatch(r"[0-9a-f]{16}", cohort), "invalid cohort")


def validate_interval(interval):
    require(isinstance(interval, dict) and set(interval) == {"start", "end", "lower_us", "upper_us", "layout",
                                                            "library", "grid"}, "invalid interval fields")
    require([interval[key] for key in ("start", "end", "layout", "library", "grid")] ==
            ["stock_card_release", "library_frame_submission_callback", "large", "nonempty", "nonempty"],
            "wrong endpoint or scene")
    lower, upper = interval["lower_us"], interval["upper_us"]
    require(type(lower) is int and type(upper) is int and 0 <= lower <= upper <= LIMIT_US
            and upper - lower <= 100_000, "invalid release bounds")
    return upper - lower


def coordinates(stages, interval):
    """Undo the common UP bracket; the resulting ranges are all relative to up_before."""
    width = validate_interval(interval)
    require(isinstance(stages, dict) and set(stages) == {"hz", *POINTS}, "invalid stage fields")
    rate = stages["hz"]
    require(type(rate) is int and 1 <= rate <= 1_000_000, "invalid clock tick rate")
    tick = (1_000_000 + rate - 1) // rate
    ranges = {"release": [0, width]}
    for name in POINTS:
        relative = stages[name]
        if name == "initial" and relative is None:
            continue
        require(isinstance(relative, list) and len(relative) == 2 and all(type(us) is int for us in relative),
                "invalid milestone interval")
        lower, upper = relative
        require(-width - tick - 1 <= lower <= upper <= LIMIT_US + tick + 1, "out of range milestone")
        absolute = [lower + width, upper]
        spread = absolute[1] - absolute[0]
        if name in ("worker", "gui"):
            require(tick <= spread <= tick + bool(1_000_000 % rate), "invalid proc tick quantization")
        else:
            require(spread == 0, "software event must be one same-clock instant")
        require(absolute[1] >= 0, "milestone is entirely before the release bracket")
        ranges[name] = absolute
    require(stages["send"] == [interval["lower_us"], interval["upper_us"]], "Library SEND does not match endpoint")
    names = [name for name in POINTS if name in ranges]
    for previous, current in zip(names, names[1:]):
        ranges[current][0] = max(ranges[previous][0], ranges[current][0])
    for earlier, later in zip(reversed(names[:-1]), reversed(names[1:])):
        ranges[earlier][1] = min(ranges[earlier][1], ranges[later][1])
    require(all(lower <= upper for lower, upper in ranges.values()), "impossible milestone order")
    centers = {name: sum(bounds) / 2 for name, bounds in ranges.items()}
    for before, after in zip(names, names[1:]):
        centers[after] = max(centers[before], centers[after])
    return ranges, centers


def parse_stages(line, interval):
    require(type(line) is bytes and len(line) <= 512, "invalid stage line")
    if line == b"STAGES unavailable\n":
        validate_interval(interval)
        return None
    match = re.fullmatch(rb"STAGES ([1-9][0-9]{0,6})((?: (?:none|0|-?[1-9][0-9]{0,11})){10})\n", line)
    require(match, "invalid stage line")
    values = match[2].split()
    stages = {"hz": int(match[1])}
    for index, name in enumerate(POINTS):
        fields = values[2 * index:2 * index + 2]
        if fields == [b"none", b"none"] and name == "initial":
            stages[name] = None
        else:
            require(b"none" not in fields, "missing required milestone")
            stages[name] = [int(value) for value in fields]
    coordinates(stages, interval)
    return stages


def validate(receipt, cohort, slot):
    require(isinstance(receipt, dict) and set(receipt) == {"cohort", "slot", "configuration", "result", "interval",
                                                         "stages", "closeout"}, "invalid receipt fields")
    require(receipt["cohort"] == cohort and type(receipt["slot"]) is int and receipt["slot"] == slot
            and 1 <= slot <= SLOTS and receipt["configuration"] == "diagnostic", "wrong cohort, slot or configuration")
    closeout = receipt["closeout"]
    require(isinstance(closeout, dict) and set(closeout) == CLOSE_FIELDS and all(type(flag) is bool for flag in closeout.values()),
            "invalid closeout")
    if receipt["result"] == "not_measured":
        require(receipt["interval"] is None and receipt["stages"] is None, "unqualified times")
        return
    require(receipt["result"] == "measured" and all(closeout.values()), "unverified measured visit")
    validate_interval(receipt["interval"])
    if receipt["stages"] is not None:
        coordinates(receipt["stages"], receipt["interval"])


def read_receipts(directory, cohort):
    validate_cohort(cohort)
    files = sorted(Path(directory).glob("*.json"))
    require(len(files) <= SLOTS, "too many receipts")
    rows = []
    for slot, path in enumerate(files, 1):
        require(path.name == f"{slot:02}.json" and not path.is_symlink() and path.is_file()
                and 0 < path.stat().st_size <= 8192, "expected consecutive regular NN.json receipts")
        row = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        validate(row, cohort, slot)
        rows.append(row)
    return rows


def issue(directory, receipt):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cohort = receipt["cohort"]
    slot = len(read_receipts(directory, cohort)) + 1
    validate(receipt, cohort, slot)
    path = directory / f"{slot:02}.json"
    temp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False)
    try:
        with temp:
            json.dump(receipt, temp, indent=2, allow_nan=False)
            temp.write("\n")
        os.link(temp.name, path)
    finally:
        Path(temp.name).unlink()
    return path


def segment(before, after, middle, release):
    lower = after[0] - before[1]
    return {"lower_us": lower if release else max(0, lower), "upper_us": after[1] - before[0], "midpoint_us": middle}


def view(receipt):
    row = {"slot": receipt["slot"], "total": None, "tick_hz": None, "release_relative_milestones_us": None,
           "segments": {}}
    interval = receipt["interval"]
    if interval is None:
        return row
    lower, upper = interval["lower_us"], interval["upper_us"]
    row["total"] = {"lower_us": lower, "upper_us": upper, "midpoint_us": (lower + upper) / 2}
    stages = receipt["stages"]
    if stages is None:
        return row
    row["tick_hz"] = stages["hz"]
    row["release_relative_milestones_us"] = {name: stages[name] for name in POINTS}
    points, centers = coordinates(stages, interval)
    for name in PRIMARY + NESTED:
        before, after = name.split("_to_")
        if before in points and after in points:
            row["segments"][name] = segment(points[before], points[after], centers[after] - centers[before], before == "release")
    return row


def distribution(samples):
    if not samples:
        return {"count": 0, "median_midpoint_us": None, "median_bounds_us": None, "midpoint_range_us": None,
                "observed_envelope_us": None}
    mids = [sample["midpoint_us"] for sample in samples]
    return {"count": len(samples), "median_midpoint_us": median(mids), "midpoint_range_us": [min(mids), max(mids)],
            "median_bounds_us": [median(row["lower_us"] for row in samples), median(row["upper_us"] for row in samples)],
            "observed_envelope_us": [min(row["lower_us"] for row in samples), max(row["upper_us"] for row in samples)]}


def difference(slow, fast):
    return {"lower_us": slow["lower_us"] - fast["upper_us"], "upper_us": slow["upper_us"] - fast["lower_us"],
            "midpoint_us": slow["midpoint_us"] - fast["midpoint_us"]}


def summarize(receipts, cohort):
    validate_cohort(cohort)
    visits = []
    for slot, receipt in enumerate(receipts, 1):
        validate(receipt, cohort, slot)
        visits.append(view(receipt))
    formal = visits[1:]
    measured = [row for row in formal if row["total"] is not None]
    full = len(measured) == 4 and visits[0]["total"] is not None
    stage_complete = full and all(name in row["segments"] for row in formal for name in PRIMARY)
    rates = {row["tick_hz"] for row in visits if row["tick_hz"] is not None}
    require(len(rates) <= 1, "tick rate changed across diagnostic visits")
    stats = {name: distribution([row["segments"][name] for row in formal if name in row["segments"]]) for name in PRIMARY + NESTED}
    usual = [name for name in PRIMARY if stage_complete and all(stats[name]["median_bounds_us"][0] >
             stats[other]["median_bounds_us"][1] for other in PRIMARY if other != name)]
    extremes = None
    if full:
        ordered = sorted(measured, key=lambda row: (row["total"]["midpoint_us"], row["slot"]))
        fast, slow = ordered[0], ordered[-1]
        distinct = (all(fast["total"]["upper_us"] < row["total"]["lower_us"] for row in ordered[1:])
                    and all(slow["total"]["lower_us"] > row["total"]["upper_us"] for row in ordered[:-1]))
        changes = {name: difference(slow["segments"][name], fast["segments"][name]) for name in PRIMARY + NESTED
                   if name in slow["segments"] and name in fast["segments"]}
        larger = [name for name in PRIMARY if stage_complete and distinct and changes[name]["lower_us"] > 0
                  and all(changes[name]["lower_us"] > changes[other]["upper_us"] for other in PRIMARY if other != name)]
        extremes = {"fast_slot": fast["slot"], "slow_slot": slow["slot"], "unambiguous_extremes": distinct,
                    "slow_minus_fast_total_us": difference(slow["total"], fast["total"]), "contributions_us": changes,
                    "largest_separated_primary_contribution": larger[0] if larger else None}
    return {"cohort": cohort, "configuration": "diagnostic", "clock": "same-visit device CLOCK_BOOTTIME, microseconds",
            "proc_tick_bins": "With rate H and start tick T: [floor(T*1e6/H), ceil((T+1)*1e6/H)]; not exec or Rust main.",
            "midpoints": "Differences of same-visit milestone centers; a birth bin overlapping UP may give a negative release-relative bound, not a proven pre-release worker.",
            "warmup_excluded": visits[:1], "formal_visits": formal, "planned_formal": 4,
            "measured_formal": len(measured), "complete_endpoints": full, "complete_stage_protocol": stage_complete,
            "complete_initial_diagnostics": stage_complete and all("initial_to_send" in row["segments"] for row in formal),
            "primary_segments": list(PRIMARY), "nested_diagnostics_not_additive": list(NESTED),
            "total_distribution": distribution([row["total"] for row in measured]), "segment_distributions": stats,
            "largest_separated_usual_primary_segment": usual[0] if usual else None,
            "extreme_comparison": extremes, "interpretation": "Descriptive only for this diagnostic cohort; no causal or speedup claim."}


def load(directory, cohort):
    require(Path(directory).is_dir(), "missing receipt directory")
    return summarize(read_receipts(directory, cohort), cohort)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--cohort", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(load(args.directory, args.cohort), indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"Invalid stage evidence: {error}\n")
