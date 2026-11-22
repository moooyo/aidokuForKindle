"""Analyze the first accepted populated Library draw of one stock-card diagnostic build."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
from statistics import median
import tempfile


HERE = Path(__file__).resolve().parent
PRIOR = HERE / "transition_analysis.py"
if not PRIOR.is_file():
    PRIOR = HERE.parents[1] / "startup-library-transition-20261122" / "analyze.py"
SPEC = importlib.util.spec_from_file_location("audited_transition_clock", PRIOR)
transition_analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transition_analysis)
stock = transition_analysis.prior
require, unique_keys, validate_cohort = stock.require, stock.unique_keys, stock.validate_cohort
SLOTS = 4
COUNTERS = ("minor_faults", "major_faults", "voluntary_switches", "involuntary_switches")
FIELDS = ("wall_us", "cpu_us", *COUNTERS, "width", "height", "format")


def validate_draw(draw, interval):
    stock.validate_interval(interval)
    if draw is None:
        return
    require(isinstance(draw, dict) and set(draw) == set(FIELDS), "unexpected draw fields")
    require(type(draw["wall_us"]) is int and 0 <= draw["wall_us"] <= interval["upper_us"], "invalid wall time")
    require(draw["cpu_us"] is None or type(draw["cpu_us"]) is int and 0 <= draw["cpu_us"] <= stock.LIMIT_US,
            "invalid thread CPU time")
    values = [draw[key] for key in COUNTERS]
    require(all(value is None for value in values) or all(type(value) is int and 0 <= value < 10**12 for value in values),
            "invalid or partial usage counters")
    require(all(type(draw[key]) is int and 0 < draw[key] <= 8192 for key in ("width", "height"))
            and draw["format"] in ("gray8", "rgb8"), "invalid render geometry or target format")


def parse_draw(line, interval, transition, sequence, marker):
    require(type(sequence) is int and 0 < sequence < 2**64 and type(marker) is int and 0 < marker < 2**32,
            "unidentified submitted frame")
    if line == b"DRAWACCOUNT unavailable\n":
        validate_draw(None, interval)
        return None
    number = rb" (0|[1-9][0-9]{0,11})"
    optional = rb" (none|0|[1-9][0-9]{0,11})"
    require(type(line) is bytes and len(line) <= 256, "invalid draw record")
    found = re.fullmatch(rb"DRAWACCOUNT" + number + optional * 5 + number * 2 + rb" (gray8|rgb8)\n", line)
    require(found is not None, "invalid draw record")
    draw = {key: None if field == b"none" else int(field) for key, field in zip(FIELDS[:-1], found.groups()[:-1], strict=True)}
    draw["format"] = found.groups()[-1].decode("ascii")
    validate_draw(draw, interval)
    require(isinstance(transition, dict) and type(transition.get("render")) is int
            and transition["render"] == draw["wall_us"], "changed or missing same-frame render witness")
    return draw


def validate(receipt, cohort, slot):
    require(isinstance(receipt, dict) and set(receipt) == {"cohort", "configuration", "slot", "result", "interval", "draw", "closeout"}
            and type(slot) is int and 1 <= slot <= SLOTS, "invalid draw receipt")
    stock.validate({key: value for key, value in receipt.items() if key != "draw"} | {"stages": None}, cohort, slot)
    if receipt["interval"] is None:
        require(receipt["draw"] is None, "unqualified draw")
    else:
        validate_draw(receipt["draw"], receipt["interval"])


def read_receipts(directory, cohort):
    validate_cohort(cohort)
    paths = sorted(Path(directory).glob("*.json"))
    require(len(paths) <= SLOTS, "too many slots")
    rows = []
    for slot, path in enumerate(paths, 1):
        require(path.name == f"{slot:02}.json" and not path.is_symlink() and path.is_file()
                and 0 < path.stat().st_size <= 8192, "expected regular consecutive receipts")
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
    target = directory / f"{slot:02}.json"
    temp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False)
    try:
        with temp:
            json.dump(receipt, temp, indent=2, allow_nan=False)
            temp.write("\n")
        os.link(temp.name, target)
    finally:
        Path(temp.name).unlink()
    return target


def visit(receipt):
    interval, draw = receipt["interval"], receipt["draw"]
    row = {"slot": receipt["slot"], "stock_to_library_us": None, "stock_midpoint_us": None,
           **dict.fromkeys(FIELDS), "cpu_share_percent": None, "wall_minus_thread_cpu_us": None, "cpu_exceeds_wall": None}
    if interval is not None:
        bounds = [interval["lower_us"], interval["upper_us"]]
        row.update(stock_to_library_us=bounds, stock_midpoint_us=sum(bounds) / 2)
    if draw is None:
        return row
    row.update(draw)
    if draw["cpu_us"] is not None:
        row["wall_minus_thread_cpu_us"] = draw["wall_us"] - draw["cpu_us"]
        row["cpu_exceeds_wall"] = row["wall_minus_thread_cpu_us"] < 0
        if draw["wall_us"] > 0:
            row["cpu_share_percent"] = 100 * draw["cpu_us"] / draw["wall_us"]
    return row


def distribution(values):
    present = [value for value in values if value is not None]
    return {"count": len(present), "median": median(present) if present else None,
            "range": [min(present), max(present)] if present else None}


def summarize(receipts, cohort):
    validate_cohort(cohort)
    visits = []
    for slot, receipt in enumerate(receipts, 1):
        validate(receipt, cohort, slot)
        visits.append(visit(receipt))
    formal = visits[1:]
    complete = len(visits) == SLOTS and all(row["stock_to_library_us"] is not None for row in visits)
    stats = {key: distribution([row[key] for row in formal]) for key in ("stock_midpoint_us", "wall_us", "cpu_us",
             "cpu_share_percent", "wall_minus_thread_cpu_us", *COUNTERS)}
    return {"cohort": cohort, "configuration": "diagnostic", "warmup_excluded": visits[:1], "formal_visits": formal,
            "planned_formal": 3, "measured_formal_draws": stats["cpu_us"]["count"], "complete_endpoints": complete,
            "complete_draws": complete and all(row["cpu_us"] is not None for row in visits),
            "complete_usage_counters": complete and all(all(row[key] is not None for key in COUNTERS) for row in visits),
            "formal_distributions": stats, "formal_cpu_above_wall_count": sum(row["cpu_exceeds_wall"] is True for row in formal),
            "render_window": "Same accepted first Library frame; elapsed wall time and CPU consumed by its GUI thread, in microseconds.",
            "scope": "Thread usage snapshots bracket thread CPU stamps, which bracket the existing render wall interval. CPU can slightly exceed the inner wall. The line is associated by the scoped GUI log and matching submitted frame; it has no independent writer PID.",
            "geometry": "Width and height are unrotated framebuffer/retained-surface pixels. Format is the retained draw target, not panel bits per pixel.",
            "ratio": "Per-visit 100 * thread_cpu_us / wall_us. No clipping; unavailable CPU or a zero wall denominator gives no ratio.",
            "interpretation": "Signed wall minus thread CPU is not an identified runnable wait or I/O interval. Faults and context switches are thread counts, not causes or durations. No speedup comparison."}


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
        parser.exit(1, f"Invalid draw evidence: {error}\n")
