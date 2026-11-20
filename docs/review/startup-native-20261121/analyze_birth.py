"""Optional post-release timing attribution; never opens a device or changes the primary result."""

import argparse
import json
from pathlib import Path
import re
from statistics import median
import tarfile

import analyze_study as primary


LINE = re.compile(r"birth_ticks ([1-9][0-9]{0,9}) ([1-9][0-9]{0,19}) ([1-9][0-9]{0,19})")


def born(ticks, rate):
    return ticks * 1_000_000 // rate, -(-((ticks + 1) * 1_000_000) // rate)


def interval(before, after):
    low, high = after[0] - before[1], after[1] - before[0]
    primary.need(0 <= before[0] <= before[1] and after[0] <= after[1] and high >= 0, "birth_order")
    return max(0, low), high


def segments(anchor, worker, app, rate, relative_text):
    primary.need(0 < worker <= app and rate > 0 and 0 < anchor[0] <= anchor[1], "birth_input")
    text = (anchor[1] + round(relative_text[0] * 1000), anchor[0] + round(relative_text[1] * 1000))
    worker_time, app_time = born(worker, rate), born(app, rate)
    return {"trigger_to_worker": interval(anchor, worker_time), "worker_to_app": interval(worker_time, app_time),
            "app_to_visible_text_and_navigation": interval(app_time, text)}


def case_anchor(batch, case):
    with tarfile.open(batch / (case + ".tar.gz"), "r|gz") as archive:
        for item in archive:
            if item.name == "out/" + case + "/anchor" and item.isreg() and not item.sparse and 0 < item.size <= 128:
                match = re.fullmatch(rb"([1-9][0-9]{0,19}) ([1-9][0-9]{0,19})\n", archive.extractfile(item).read())
                if match:
                    return tuple(map(int, match.groups()))
    raise ValueError("missing_birth_anchor")


def attribution(work, plan_dir, reference_batch):
    study = primary.analyze(work, plan_dir, reference_batch)
    primary.need(study["ok"] and not study["errors"] and not study["diagnostic_only"], "unsuccessful_primary_study")
    plan = primary.validate_plan(plan_dir, "study")
    batch = work / "batches" / ("study-" + plan["label"])
    primary.transaction(plan, batch)
    receipts = [item["observed"] for item in primary.record(batch / "receipt.json")["operations"]
                if item["operation"] == "discard-case"]
    result = {"scope": "Optional same-boot process birth bins and sampled framebuffer, not Rust entry or optical time",
              "origin": study["origin"], "median_bounds_ms": None, "unit": "ms"}
    rows = {"control": [], "candidate": []}
    rates, previous_app, missing = set(), 0, []
    for case, lines in zip(plan["cases"], receipts, strict=True):
        if not re.fullmatch(r"(control|candidate)-0[1-6]", case):
            continue
        candidates = [line for line in lines if line.startswith("birth_ticks")]
        if not candidates:
            missing.append(case)
            continue
        match = LINE.fullmatch(candidates[0])
        primary.need(len(candidates) == 1 and match is not None, "bad_birth_record")
        rate, worker, app = map(int, match.groups())
        primary.need(worker > previous_app, "reused_or_reordered_birth")
        previous_app = app
        rates.add(rate)
        pair = study["cases"][case]["brackets_ms"]["text_and_navigation"]
        rows[case.split("-")[0]].append(segments(case_anchor(batch, case), worker, app, rate, pair))
    if missing:
        return {**result, "missing_cases": missing}
    primary.need(len(rates) == 1 and all(len(arm) == 6 for arm in rows.values()), "inconsistent_birth_cohort")
    result["clock_ticks_per_second"] = rates.pop()
    result["median_bounds_ms"] = {
        arm: {key: [median(row[key][edge] for row in samples) / 1000 for edge in (0, 1)]
              for key in samples[0]} for arm, samples in rows.items()
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("work", "study_plan", "reference_batch", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    result = attribution(args.work, args.study_plan, args.reference_batch)
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
