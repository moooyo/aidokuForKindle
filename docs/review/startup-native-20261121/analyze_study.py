"""Replay a closed indexed-entry screen offline. Use the private worker host paths."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
from statistics import median
import tarfile
import tempfile

from observer import observer


ENDPOINTS = ("text_and_navigation", "complete_library")
BRACKETS = "relative_to_supplied_device_clock_anchor"
ORIGINS = {"indexed-uri", "stock-tap"}
LABEL = re.compile(r"[0-9a-f]{16}")


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def schedule(pictures_first=False):
    cases = ["control-warmup", "candidate-warmup"]
    for pair in range(1, 7):
        sides = ("candidate", "control") if pair % 2 == 0 else ("control", "candidate")
        cases.extend(f"{side}-{pair:02}" for side in sides)
    pictures = ["control-picture", "candidate-picture"]
    return pictures + cases if pictures_first else cases + pictures


def load_file(path, limit):
    need(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= limit, "missing_or_unsafe_input")
    return path.read_bytes()


def record(path):
    def unique(items):
        result = dict(items)
        need(len(result) == len(items), "duplicate_json_field")
        return result
    result = json.loads(load_file(path, 512 * 1024), object_pairs_hook=unique)
    need(isinstance(result, dict), "invalid_json_record")
    return result


def validate_plan(folder, mode):
    plan = record(folder / "plan.json")
    label, origin = plan.get("label"), plan.get("origin")
    need(isinstance(label, str) and LABEL.fullmatch(label) and folder.name == "run-" + label and
         plan.get("mode") == mode and origin in ORIGINS, "invalid_frozen_plan")
    name = plan.get("schedule")
    marker = folder / "schedule"
    if "schedule" in plan:
        need(mode == "study" and name in ("pictures-first", "pictures-last") and
             (name != "pictures-first" or origin == "stock-tap") and
             load_file(marker, 32) == (name + "\n").encode(), "invalid_frozen_schedule")
    else:
        need(not marker.exists() and not marker.is_symlink(), "missing_frozen_schedule")
    names = schedule(name == "pictures-first") if mode == "study" else ["reference"]
    need(plan.get("cases") == names and isinstance(plan.get("tap_files"), dict), "wrong_schedule")
    if mode == "reference":
        need(origin == "indexed-uri" and plan.get("reference") is None and not plan["tap_files"], "wrong_reference_mode")
    else:
        need(isinstance(plan.get("reference"), str) and bool(plan["reference"]) and
             (origin == "stock-tap") == bool(plan["tap_files"]), "missing_origin_or_reference_binding")
    return plan


def stamp(value):
    need(isinstance(value, str), "missing_clock")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_clock") from None
    need(result.tzinfo is not None, "invalid_clock")
    return result


def transaction(plan, batch):
    receipt = record(batch / "receipt.json")
    mode, names = plan["mode"], plan["cases"]
    acquired, released = receipt.get("acquisition", {}), receipt.get("release", {})
    need(batch.name == mode + "-" + plan["label"] and receipt.get("mode") == mode, "wrong_batch")
    need(("schedule" in plan) == ("schedule" in receipt) and plan.get("schedule") == receipt.get("schedule"),
         "receipt_schedule_mismatch")
    need(acquired.get("operation") == "acquire" and acquired.get("state") == "held" and
         released.get("operation") == "release" and released.get("state") == "released" and
         acquired.get("ok") is True and released.get("ok") is True and
         all(released.get(key) is True for key in ("owner_and_token_checked", "lease_directory_absent_after_release",
                                                    "native_restored_and_connections_closed", "only_owned_owner_json_removed_then_rmdir")) and
         acquired.get("project") == released.get("project") == "Aidoku" and
         acquired.get("run_id") == released.get("run_id") == "aidoku-" + batch.name and
         all(isinstance(acquired.get(key), str) and acquired[key] and acquired[key] == released.get(key)
             for key in ("owner_thread", "lease_token_sha256")), "unverified_owned_release")
    begin, end = stamp(acquired.get("acquired_at_utc")), stamp(released.get("released_at_utc"))
    need(begin <= end, "invalid_release_order")
    ops = [("admit", "guard_admitted"), ("prepare", "device_prepared")]
    for case in names:
        operation = "reference" if mode == "reference" else "picture" if case.endswith("-picture") else "case"
        ops += [(case, "device_" + operation), ("collect-" + case, None), ("discard-case", "device_discarded")]
    ops += [("close", "device_closed")]
    observed = receipt.get("operations")
    need(isinstance(observed, list) and len(observed) == len(ops) and receipt.get("completed") == names and
         receipt.get("ok") is True and not receipt.get("error") and not receipt.get("close_error"), "incomplete_transaction")
    for item, (operation, tag) in zip(observed, ops):
        need(item.get("operation") == operation and type(item.get("status")) is int and item["status"] == 0 and
             item.get("reason") is None and
             (tag is None or item.get("observed", [None])[-1:] == [tag]), "operation_or_protection_mismatch")
    return begin, end


def unpack(batch, case, origin, private):
    directory = private / case
    directory.mkdir()
    extra = ["library-a.frame", "library-b.frame"] if case == "reference" else ["final.frame"] if case.endswith("-picture") else ["trace"]
    expected = {"out/" + case + "/" + leaf for leaf in ["origin", "anchor", "qualified", "stock.frame", *extra]}
    archive_path = batch / (case + ".tar.gz")
    need(archive_path.is_file() and not archive_path.is_symlink() and 0 < archive_path.stat().st_size <= 23 * 1024**2, "missing_or_unsafe_case_archive")
    with tarfile.open(archive_path, "r|gz") as archive:
        for member in archive:
            need(member.name in expected and member.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not member.sparse and
                 not member.linkname and not member.mode & 0o7000 and 0 < member.size <= 5_000_000, "wrong_case_archive_members")
            expected.remove(member.name)
            leaf = member.name.rsplit("/", 1)[1]
            need(leaf not in ("origin", "qualified", "anchor") or member.size <= 128, "oversize_qualifier")
            data = archive.extractfile(member).read()
            need(len(data) == member.size, "truncated_case")
            (directory / leaf).write_bytes(data)
    need(not expected, "missing_case_member")
    need((directory / "qualified").read_bytes() == f"{case} {origin}\n".encode() and
         (directory / "origin").read_bytes() == (origin + "\n").encode(), "wrong_case_or_trigger")
    anchor = re.fullmatch(rb"([1-9][0-9]{0,19}) ([1-9][0-9]{0,19})\n", (directory / "anchor").read_bytes())
    need(anchor is not None, "invalid_anchor")
    lower, upper = map(int, anchor.groups())
    need(lower <= upper <= lower + observer.ANCHOR_WIDTH, "anchor_clock_or_width")
    stock, _ = observer.frame(directory / "stock.frame")
    need(stock[5] < lower and lower - stock[5] <= observer.STOCK_MAX_AGE, "stock_not_bound_to_trigger")
    return directory, lower, upper, stock[4]


def exact_archives(batch, names):
    return {path.name for path in batch.glob("*.tar.gz")} == {case + ".tar.gz" for case in names}


def reference_inputs(plan_dir, batch, rule, private):
    plan = validate_plan(plan_dir, "reference")
    _, released = transaction(plan, batch)
    need(exact_archives(batch, plan["cases"]), "wrong_reference_archives")
    files, _, upper, _ = unpack(batch, "reference", "indexed-uri", private)
    info, *_ = observer.rule(rule)
    frame_a = files / "library-a.frame"
    need(observer.frame(frame_a)[0][4] > upper, "reference_before_trigger")
    regenerated = private / "regenerated.rule"
    observer.freeze(files / "stock.frame", frame_a, files / "library-b.frame", info[4], info[3], regenerated)
    need(load_file(regenerated, 16 * 1024) == load_file(rule, 16 * 1024), "rule_not_from_original_reference")
    return frame_a, released


def case_result(rule, frame_a, closed, case, origin, private):
    files, lower, upper, start = unpack(closed, case, origin, private)
    if case.endswith("-picture"):
        observer.stock_target(rule, files / "stock.frame")
        stamp_info, _ = observer.frame(files / "final.frame")
        need(stamp_info[4] > upper, "picture_before_trigger")
        verdict = observer.compare_settled(rule, frame_a, files / "final.frame")
        need(verdict.get("settled_full_body_equal") is True and verdict.get("right_status_equal_and_observable") is True,
             "incomplete_independent_picture")
        return verdict, (start, stamp_info[5])
    trace = files / "trace"
    sample = observer.measure(rule, trace, lower, upper, files / "stock.frame")
    trace_info, _ = observer.header(load_file(trace, 1024 * 1024), b"AFW2", 8)
    return {"ok": True, "cover_slots": sample["cover_slots"], "brackets_ms": sample[BRACKETS],
            "observer": sample["observer"]}, (start, trace_info[4])


def central(range_ms):
    return (range_ms[0] + range_ms[1]) / 2


def summarize(results):
    arms, pairs, combined = {}, [], {}
    for side in ("control", "candidate"):
        arms[side] = {}
        for endpoint in ENDPOINTS:
            values = [results[f"{side}-{pair:02}"]["brackets_ms"][endpoint] for pair in range(1, 7)]
            arms[side][endpoint] = [round(median(x[edge] for x in values), 4) for edge in (0, 1)]
    for number in range(1, 7):
        pair = {"pair": number, "order": "AB" if number % 2 else "BA"}
        for endpoint in ENDPOINTS:
            control, candidate = [results[f"{side}-{number:02}"]["brackets_ms"][endpoint] for side in ("control", "candidate")]
            lower, upper = control[0] - candidate[1], control[1] - candidate[0]
            pair[endpoint] = {"midpoint_saving_ms": round(central(control) - central(candidate), 4),
                              "saving_bracket_ms": [round(lower, 4), round(upper, 4)],
                              "resolved": "faster" if lower > 0 else "slower" if upper < 0 else "within_resolution"}
        pairs.append(pair)
    for endpoint in ENDPOINTS:
        midpoints = [pair[endpoint]["midpoint_saving_ms"] for pair in pairs]
        directions = [pair[endpoint]["resolved"] for pair in pairs]
        combined[endpoint] = {"median_midpoint_saving_ms": round(median(midpoints), 5),
                              "median_saving_bracket_ms": [round(median(pair[endpoint]["saving_bracket_ms"][edge] for pair in pairs), 4)
                                                           for edge in (0, 1)],
                              "midpoint_faster": sum(value > 0 for value in midpoints), "midpoint_slower": sum(value < 0 for value in midpoints),
                              "resolved_faster": directions.count("faster"), "resolved_slower": directions.count("slower"),
                              "within_resolution": directions.count("within_resolution")}
    first, other = (combined[key] for key in ENDPOINTS)
    thresholds = {"at_least_five_faster_midpoints": first["midpoint_faster"] >= 5,
                  "at_least_100_ms_median": first["median_midpoint_saving_ms"] >= 100,
                  "other_endpoint_regresses_at_most_50_ms": other["median_midpoint_saving_ms"] >= -50}
    return {"per_arm_median_brackets_ms": arms, "paired": pairs, "endpoints": combined, "screen_thresholds": thresholds,
            "consistent_warm_screen_gain": all(thresholds.values()), "warm_300_ms_target": first["median_midpoint_saving_ms"] >= 300}


def reason(error):
    text = str(error)
    return text if type(error) is ValueError and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", text) else "invalid_or_missing_private_record"


def analyze(work, plan_dir, reference_batch):
    report = {"ok": False, "diagnostic_only": True, "origin": None, "unit": "ms", "scope": "sampled_framebuffer_not_optical_or_input",
              "errors": {}, "cases": {}, "cohort": None,
              "qualification": {"separate_control_reference": False, "complete_request_and_protected_closeout": False,
                                "independent_full_body_and_right_status": False}}
    errors = report["errors"]
    try:
        plan = validate_plan(plan_dir, "study")
        if "schedule" in plan:
            report["schedule"] = plan["schedule"]
        origin = report["origin"] = plan["origin"]
        batch = work / "batches" / ("study-" + plan["label"])
        ref_match = re.fullmatch(r"reference-([0-9a-f]{16})", reference_batch.name)
        need(ref_match is not None and reference_batch.parent == batch.parent and plan_dir.parent == work / "plans",
             "wrong_reference_or_study_path")
        ref_plan = work / "plans" / ("run-" + ref_match.group(1))
        rule = plan_dir / "rule"
        need(load_file(Path(plan["reference"]) / "rule", 16 * 1024) == load_file(rule, 16 * 1024), "frozen_rule_changed")
        need(load_file(plan_dir / "observer.py", 128 * 1024) == load_file(Path(observer.__file__), 128 * 1024),
             "frozen_observer_changed")
        try:
            lease_start, _ = transaction(plan, batch)
            report["qualification"]["complete_request_and_protected_closeout"] = True
        except (ValueError, KeyError, TypeError, OSError) as exc:
            errors["transaction"] = reason(exc)
            lease_start = None
        if not exact_archives(batch, plan["cases"]):
            errors["archives"] = "case_archive_set_differs"
        with tempfile.TemporaryDirectory(prefix="aidoku-study-") as directory:
            private = Path(directory)
            frame_a, ref_release = reference_inputs(ref_plan, reference_batch, rule, private)
            need(lease_start is None or ref_release < lease_start, "reference_not_separately_closed_first")
            report["qualification"]["separate_control_reference"] = True
            previous_end = 0
            for case in plan["cases"]:
                try:
                    result, (start, end) = case_result(rule, frame_a, batch, case, origin, private)
                    need(start > previous_end, "overlapping_or_reused_visit")
                    previous_end = end
                    report["cases"][case] = result
                except (KeyError, OSError, tarfile.TarError, ValueError, TypeError, EOFError) as exc:
                    errors[case] = reason(exc)
            report["qualification"]["independent_full_body_and_right_status"] = all(
                case in report["cases"] for case in ("control-picture", "candidate-picture"))
            if not errors:
                report["cohort"] = summarize(report["cases"])
                report["ok"], report["diagnostic_only"] = True, False
    except (KeyError, OSError, tarfile.TarError, ValueError, TypeError, EOFError) as exc:
        errors["setup"] = reason(exc)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for operand in ("work", "study_plan", "reference_batch", "output"):
        parser.add_argument(operand, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    report = analyze(args.work, args.study_plan, args.reference_batch)
    with args.output.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(json.dumps({key: report[key] for key in ("ok", "diagnostic_only", "origin", "errors")}))
    raise SystemExit(0 if report["ok"] else 1)
