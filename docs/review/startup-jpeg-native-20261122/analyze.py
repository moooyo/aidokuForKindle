"""Analyze immutable, sanitized stock-card visit receipts after device closeout."""

import argparse
import json
import os
from pathlib import Path
import re
from statistics import median
import tempfile


ORDERS = ("AB", "BA", "AB", "BA", "AB", "BA")
SCHEDULE = "AB" + "".join(ORDERS)
EXACT_INTERVAL_LIMIT_US = 1 << 49
CLOSE_FIELDS = {"ordinary_exit", "native_restored", "close", "absent", "connections_closed",
                "owner_and_token_checked", "released"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_cohort(cohort):
    require(isinstance(cohort, str) and re.fullmatch(r"[0-9a-f]{16}", cohort), "invalid cohort")


def validate(receipt, cohort, slot):
    require(isinstance(receipt, dict) and set(receipt) == {"cohort", "slot", "arm", "result", "interval", "closeout"},
            "unexpected visit schema")
    require(receipt["cohort"] == cohort and type(receipt["slot"]) is int and receipt["slot"] == slot
            and 1 <= slot <= len(SCHEDULE) and receipt["arm"] == SCHEDULE[slot - 1], "wrong cohort, slot or arm")
    closeout = receipt["closeout"]
    require(isinstance(closeout, dict) and set(closeout) == CLOSE_FIELDS
            and all(type(flag) is bool for flag in closeout.values()), "invalid closeout record")
    interval = receipt["interval"]
    if receipt["result"] == "not_measured":
        require(interval is None, "invalid visits cannot publish a time")
        return
    require(receipt["result"] == "measured" and all(closeout.values()), "measurement without verified closeout")
    require(isinstance(interval, dict) and set(interval) == {"start", "end", "lower_us", "upper_us", "layout",
                                                            "library", "grid"}, "invalid interval schema")
    require((interval["start"], interval["end"], interval["layout"], interval["library"], interval["grid"])
            == ("stock_card_release", "library_frame_submission_callback", "large", "nonempty", "nonempty"),
            "unexpected clock endpoint or shelf")
    require(type(interval["lower_us"]) is int and type(interval["upper_us"]) is int
            and 0 <= interval["lower_us"] <= interval["upper_us"] < EXACT_INTERVAL_LIMIT_US,
            "invalid release-to-submit bounds")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate receipt field")
        result[key] = value
    return result


def read_receipts(directory, cohort):
    validate_cohort(cohort)
    files = sorted(Path(directory).glob("*.json"))
    require(len(files) <= len(SCHEDULE), "too many visits")
    receipts = []
    for slot, path in enumerate(files, 1):
        require(path.name == f"{slot:02}.json" and not path.is_symlink() and path.is_file()
                and 0 < path.stat().st_size <= 8192, "expected regular, consecutive NN.json receipts")
        receipt = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        validate(receipt, cohort, slot)
        receipts.append(receipt)
    return receipts


def issue(directory, receipt):
    """Publish once. The caller binds the private attempt, interval and return evidence before calling."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    require(isinstance(receipt, dict) and "cohort" in receipt, "unexpected visit schema")
    cohort = receipt["cohort"]
    slot = len(read_receipts(directory, cohort)) + 1
    validate(receipt, cohort, slot)
    path = directory / f"{slot:02}.json"
    temp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False)
    scratch = Path(temp.name)
    try:
        with temp:
            json.dump(receipt, temp, indent=2, allow_nan=False)
            temp.write("\n")
        os.link(scratch, path)
    finally:
        scratch.unlink()
    return path


def visit_result(receipt):
    interval = receipt["interval"]
    if interval is None:
        return {"slot": receipt["slot"], "arm": receipt["arm"], "result": "not_measured"}
    lower, upper = interval["lower_us"], interval["upper_us"]
    return {"slot": receipt["slot"], "arm": receipt["arm"], "result": "measured", "lower_us": lower,
            "upper_us": upper, "release_bracket_width_us": upper - lower, "midpoint_us": (lower + upper) / 2}


def summarize(receipts, cohort):
    validate_cohort(cohort)
    visits = []
    for slot, receipt in enumerate(receipts, 1):
        validate(receipt, cohort, slot)
        visits.append(visit_result(receipt))
    pairs = []
    for index, order in enumerate(ORDERS):
        assigned = visits[2 + 2 * index:4 + 2 * index]
        row = {"pair": index + 1, "order": order, "visits": assigned}
        if len(assigned) == 2 and all(visit["result"] == "measured" for visit in assigned):
            control, candidate = sorted(assigned, key=lambda visit: visit["arm"])
            lower = control["lower_us"] - candidate["upper_us"]
            upper = control["upper_us"] - candidate["lower_us"]
            row.update(status="paired", saving_lower_us=lower, saving_upper_us=upper,
                       saving_midpoint_us=(lower + upper) / 2,
                       direction="B_earlier" if lower > 0 else "A_earlier" if upper < 0 else "overlap_or_touch")
        else:
            row["status"] = ("not_measured" if any(visit["result"] == "not_measured" for visit in assigned)
                             else "incomplete" if assigned else "not_run")
        pairs.append(row)
    paired = [pair for pair in pairs if pair["status"] == "paired"]

    def middle(field, rows):
        return median(row[field] for row in rows) if rows else None

    order_medians = {order: middle("saving_midpoint_us", [row for row in paired if row["order"] == order])
                     for order in ("AB", "BA")}
    conservative_lower = middle("saving_lower_us", paired)
    wins = sum(row["direction"] == "B_earlier" for row in paired)
    full = len(paired) == len(ORDERS) and all(row["result"] == "measured" for row in visits[:2])
    consistent = full and conservative_lower > 0 and all(value > 0 for value in order_medians.values()) and wins >= 5
    return {"cohort": cohort, "endpoint": "ordinary stock Library card release to first software Library submission",
            "saving": "eager A minus on-demand B; microseconds; positive means B earlier",
            "scope": "This device and cohort only; intervals bound release timing, not sampling variation or panel settling.",
            "warmups_excluded": visits[:2], "receipt_count": len(visits), "pairs": pairs,
            "paired_count": len(paired), "planned_pairs": len(ORDERS), "full_protocol": full,
            "median_paired_midpoint_us": middle("saving_midpoint_us", paired),
            "median_paired_conservative_bounds_us": [conservative_lower, middle("saving_upper_us", paired)] if paired else None,
            "median_paired_midpoint_by_order_us": order_medians, "disjoint_B_earlier": wins,
            "disjoint_A_earlier": sum(row["direction"] == "A_earlier" for row in paired),
            "overlap_or_touch": sum(row["direction"] == "overlap_or_touch" for row in paired),
            "interpretation": ("consistent_improvement_in_this_batch" if consistent else
                               "no_consistent_improvement" if full else "incomplete_descriptive_only"),
            "automatic_promotion": False}


def load(directory, cohort):
    directory = Path(directory)
    require(directory.is_dir(), "receipt directory does not exist")
    return summarize(read_receipts(directory, cohort), cohort)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--cohort", required=True, help="Public ID of the frozen plan, not a lease identity")
    args = parser.parse_args()
    try:
        print(json.dumps(load(args.directory, args.cohort), indent=2, allow_nan=False))
    except (ValueError, OSError) as error:
        parser.exit(1, f"Invalid visit evidence: {error}\n")
