"""Replay three fixed Library transitions and one excluded warmup; no device access."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
from statistics import median
import tempfile


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "stage_analysis.py"
if not SOURCE.is_file():
    SOURCE = HERE.parent / "startup-card-stages-20261122/analyze.py"
SPEC = importlib.util.spec_from_file_location("closed_stage_clocks", SOURCE)
prior = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prior)
parse_stages, unique_keys, require = prior.parse_stages, prior.unique_keys, prior.require
SLOTS = 4
STAMPS = ("initial", "fence", "metadata_ready", "metadata_received", "fonts_ready", "fonts_received",
          "fonts_registered", "attach_enter", "attach_exit", "view_enter", "view_exit", "scene_requested")
COSTS = ("render", "settle", "write", "send")
PRIMARY = ("initial_to_fence", "fence_to_attach", "attach_to_scene", "scene_to_library")
READINESS = ("fence_to_last_producer", "last_producer_to_ui_ready", "ui_ready_to_attach")
ATTACH = ("controller_attach", "attach_exit_to_view", "view_projection", "view_exit_to_scene")
FRAME = (*COSTS, "other_elapsed")


def cross_initial(interval, stages):
    prior.validate_interval(interval)
    if stages is None:
        return None
    prior.coordinates(stages, interval)
    if stages["initial"] is None:
        return None
    return stages["initial"][0] - stages["send"][0]


def validate_transition(transition, interval, stages):
    independent = cross_initial(interval, stages)
    if transition is None:
        return
    require(isinstance(transition, dict) and set(transition) == {*STAMPS, *COSTS}, "invalid transition fields")
    for key in STAMPS:
        number = transition[key]
        require(number is None or type(number) is int and -interval["upper_us"] <= number <= 0, "invalid relative stamp")
        if number is not None and stages is not None:
            require(number >= stages["main"][0] - stages["send"][0], "stamp precedes Rust main")
    require(stages is None or transition["initial"] == independent, "initial and Library marks disagree")
    require(transition["fence"] is None or transition["initial"] is not None, "fence without its initial frame")
    chain = ("attach_enter", "attach_exit", "view_enter", "view_exit", "scene_requested")
    for keys in (("initial", "fence"), ("metadata_ready", "metadata_received", *chain),
                 ("fonts_ready", "fonts_received", "fonts_registered", *chain)):
        present = [transition[key] for key in keys if transition[key] is not None]
        require(present == sorted(present), "contradictory event order")
    costs = [transition[key] for key in COSTS]
    require(all(value is None for value in costs) or all(type(value) is int and 0 <= value <= interval["upper_us"]
                                                         for value in costs), "incomplete or invalid frame costs")


def parse_transition(line, interval, stages):
    if line == b"TRANSITION unavailable\n":
        validate_transition(None, interval, stages)
        return None
    require(type(line) is bytes and len(line) <= 256, "invalid transition line")
    signed = rb" (none|0|-[1-9][0-9]{0,11})"
    elapsed = rb" (none|0|[1-9][0-9]{0,11})"
    matched = re.fullmatch(rb"TRANSITION" + signed * len(STAMPS) + elapsed * len(COSTS) + rb"\n", line)
    require(matched, "invalid transition line")
    transition = {key: None if value == b"none" else int(value)
                  for key, value in zip(STAMPS + COSTS, matched.groups(), strict=True)}
    validate_transition(transition, interval, stages)
    return transition


def validate(receipt, cohort, slot):
    require(isinstance(receipt, dict) and "transition" in receipt and type(slot) is int and 1 <= slot <= SLOTS,
            "wrong transition slot or fields")
    base = {key: value for key, value in receipt.items() if key != "transition"}
    prior.validate(base, cohort, slot)
    if receipt["interval"] is None:
        require(receipt["transition"] is None, "unqualified transition")
    else:
        validate_transition(receipt["transition"], receipt["interval"], receipt["stages"])


def read_receipts(directory, cohort):
    prior.validate_cohort(cohort)
    paths = sorted(Path(directory).glob("*.json"))
    require(len(paths) <= SLOTS, "too many slots")
    rows = []
    for slot, path in enumerate(paths, 1):
        require(path.name == f"{slot:02}.json" and not path.is_symlink() and path.is_file()
                and 0 < path.stat().st_size <= 8192, "invalid receipt sequence")
        item = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        validate(item, cohort, slot)
        rows.append(item)
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


def pieces(names, points):
    if None in points or list(points) != sorted(points):
        return None
    return dict(zip(names, (end - start for start, end in zip(points, points[1:])), strict=True))


def view(receipt):
    interval, stages, transition = (receipt[key] for key in ("interval", "stages", "transition"))
    row = {"slot": receipt["slot"], "stock_to_library_us": None, "initial_to_library_us": None,
           "primary_us": None, "readiness_us": None, "attach_us": None, "frame_us": None,
           "same_frame_elapsed_us": None, "frame_clock_balance_us": None,
           "relative_to_fence_us": {}, "worker_delivery_lags_us": {}, "observed_clock_gaps_us": {}}
    if interval is None:
        return row
    row["stock_to_library_us"] = [interval["lower_us"], interval["upper_us"]]
    independent = cross_initial(interval, stages)
    initial = independent if transition is None else transition["initial"]
    if initial is not None:
        row["initial_to_library_us"] = -initial
    if transition is None:
        return row
    item = transition
    fence, attach, ready = (item[key] for key in ("fence", "attach_enter", "scene_requested"))
    branches = (initial, fence, attach, ready, 0)
    row["primary_us"] = pieces(PRIMARY, branches)
    row["observed_clock_gaps_us"] = {key: end - start for key, start, end in zip(PRIMARY, branches, branches[1:])
                                     if start is not None and end is not None}
    if fence is not None:
        row["relative_to_fence_us"] = {key: item[key] - fence for key in STAMPS[2:7] if item[key] is not None}
    for name, start, end in (("metadata_produced_to_received", "metadata_ready", "metadata_received"),
                             ("fonts_produced_to_received", "fonts_ready", "fonts_received"),
                             ("fonts_received_to_registered", "fonts_received", "fonts_registered")):
        if item[start] is not None and item[end] is not None:
            row["worker_delivery_lags_us"][name] = item[end] - item[start]
    if all(value is not None for value in (fence, attach, item["metadata_ready"], item["fonts_ready"],
                                           item["metadata_received"], item["fonts_registered"])):
        producers = max(fence, item["metadata_ready"], item["fonts_ready"])
        ui_ready = max(producers, item["metadata_received"], item["fonts_registered"])
        row["readiness_us"] = pieces(READINESS, (fence, producers, ui_ready, attach))
    row["attach_us"] = pieces(ATTACH, (attach, item["attach_exit"], item["view_enter"], item["view_exit"], ready))
    if item[COSTS[0]] is not None:
        costs = {key: item[key] for key in COSTS}
        row["same_frame_elapsed_us"] = costs
        if ready is not None:
            balance = -ready - sum(costs.values())
            row["frame_clock_balance_us"] = balance
            if balance >= 0:
                row["frame_us"] = {**costs, "other_elapsed": balance}
    return row


def distribution(values):
    return {"count": len(values), "median_us": median(values) if values else None,
            "range_us": [min(values), max(values)] if values else None}


def aligned(rows, group, names):
    return {key: distribution([row[group][key] for row in rows if row[group] is not None and key in row[group]])
            for key in names}


def largest(values, margin):
    return next((key for key, value in values.items() if value > margin and
                 all(value - margin > other + margin for name, other in values.items() if name != key)), None)


def summarize(receipts, cohort):
    prior.validate_cohort(cohort)
    visits = []
    for slot, receipt in enumerate(receipts, 1):
        validate(receipt, cohort, slot)
        visits.append(view(receipt))
    formal = visits[1:]
    totals = [row["initial_to_library_us"] for row in formal if row["initial_to_library_us"] is not None]
    complete = len(totals) == 3 and visits[0]["initial_to_library_us"] is not None
    full_primary = complete and all(row["primary_us"] is not None for row in formal)
    groups = {"primary_us": PRIMARY, "readiness_us": READINESS, "attach_us": ATTACH, "frame_us": FRAME}
    statistics = {group: aligned(formal, group, names) for group, names in groups.items()}
    usual = largest({key: statistics["primary_us"][key]["median_us"] for key in PRIMARY}, 1) if full_primary else None
    extremes = None
    if complete:
        fast, middle, slow = sorted(formal, key=lambda row: (row["initial_to_library_us"], row["slot"]))
        distinct = fast["initial_to_library_us"] + 2 < middle["initial_to_library_us"] < slow["initial_to_library_us"] - 2
        contributions = {group: {key: slow[group][key] - fast[group][key] for key in names}
                         if slow[group] is not None and fast[group] is not None else None for group, names in groups.items()}
        changes = contributions["primary_us"]
        variation = largest(changes, 2) if distinct and full_primary else None
        extremes = {"fast_slot": fast["slot"], "slow_slot": slow["slot"], "unambiguous_extremes": distinct,
                    "slow_minus_fast_us": slow["initial_to_library_us"] - fast["initial_to_library_us"],
                    "contributions_us": contributions, "largest_separated_primary_contribution": variation,
                    "usual_and_variation_differ": usual != variation if usual and variation else None}
    stock = [row["stock_to_library_us"] for row in formal if row["stock_to_library_us"] is not None]
    return {"cohort": cohort, "unit": "microseconds", "configuration": "diagnostic", "warmup_excluded": visits[:1],
            "formal_visits": formal, "planned_formal": 3, "measured_initial_to_library": len(totals),
            "complete_endpoints": complete, "complete_primary_partition": full_primary,
            "initial_to_library": distribution(totals), "stock_to_library_midpoints_secondary": distribution([(lo + hi) / 2 for lo, hi in stock]),
            "segment_distributions": statistics, "largest_separated_usual_primary_segment": usual,
            "extreme_comparison": extremes,
            "interpretation": "Primary segments partition the same I-to-S visit. Branch diagnostics nest, not add. Signed fence-relative readiness includes overlap. Other elapsed is not an inferred renderer start or CPU time.",
            "resolution_rule": "One microsecond per within-visit event difference and two per between-visit difference for ranking; ties or overlapping bounds name no largest segment. No causal or optimization claim."}


def load(directory, cohort):
    require(Path(directory).is_dir(), "receipt directory does not exist")
    return summarize(read_receipts(directory, cohort), cohort)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--cohort", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(load(args.directory, args.cohort), indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"Invalid transition evidence: {error}\n")
