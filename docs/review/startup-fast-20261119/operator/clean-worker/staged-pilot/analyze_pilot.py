"""Analyze closed, non-indexed staged-foreground pilots. Never contacts a device."""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import io
import json
from pathlib import Path
from statistics import median
import sys
import tarfile

from PIL import Image, ImageChops, ImageStat

from cohort import CONFIRMATION, PAIR_COUNTS, check_protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))
from analyze import POLICY, settle


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def row(trace, state, marks, regime, name):
    settle(state)
    need(trace["clock"] == "monotonic_rust_main" and all(trace[key] is True for key in ("enabled", "finished", "success"))
         and trace["dropped_events"] == 0, "incomplete_trace")
    shell = {}
    for line in marks.decode().splitlines():
        stamp, key = line.split()
        need(key not in shell, "duplicate_shell_marker")
        shell[key] = int(Decimal(stamp) * 1000000)
    events = trace["events"]
    need(all(left["at_us"] <= right["at_us"] for left, right in zip(events, events[1:])), "clock_order")
    by = defaultdict(list)
    for event in events:
        by[event["name"]].append(event)

    def single(key):
        need(len(by[key]) == 1, "not_one_" + key)
        return by[key][0]

    need(single("process_entry")["at_us"] == 0, "entry")
    policy = [event["name"] for event in events if event["name"].startswith("startup_config_")]
    need(len(policy) == len(POLICY) and set(policy) == POLICY, "startup_policy")
    first = single("startup_library_frame_submitted")
    ready = single("startup_library_input_ready")
    complete = single("startup_visible_covers_settled")
    frames = by["startup_cover_frame_submitted"]
    need(frames and all(frame["cover_slots"] == 7 and 0 <= frame["cover_images"] <= 7
                        and frame["marker"] > 0 and frame["frame_sequence"] > 0 for frame in [*frames, first, ready, complete]),
         "invalid_slots_or_marker")
    fields = ("marker", "frame_sequence", "cover_slots", "cover_images", "cover_pending")
    need(all(first[key] == frames[0][key] and complete[key] == frames[-1][key] for key in fields)
         and any((ready["marker"], ready["frame_sequence"]) == (frame["marker"], frame["frame_sequence"]) for frame in frames)
         and (complete["cover_images"], complete["cover_pending"]) == (7, False), "unbound_library_frame")
    need(all(left["frame_sequence"] < right["frame_sequence"] and left["marker"] != right["marker"]
             and left["cover_images"] <= right["cover_images"] and left["boottime_us"] <= right["boottime_us"]
             for left, right in zip(frames, frames[1:])), "invalid_frame_order")
    initial, fence = single("startup_initial_frame_submitted"), single("startup_initial_input_ready")
    need(initial["full"] is True and initial["marker"] == fence["marker"] and
         initial["boottime_us"] <= fence["boottime_us"] <= ready["boottime_us"], "input_fence")
    start = shell["staged_foreground_requested"]
    usable = max(first["boottime_us"], ready["boottime_us"])
    end = complete["boottime_us"]
    stop = single("shutdown_started")["boottime_us"]
    need(start <= shell["launcher_worker"] <= first["boottime_us"] <= usable <= stop
         and first["boottime_us"] <= end <= stop <= shell["stock_returned"] + 10000, "staged_clock")
    if name.endswith("picture"):
        need(end <= shell["capture_started"] <= stop, "early_native_picture")
    counts = Counter()
    kinds = {"cover_preparation_started": "started", "cover_preparation_finished": "finished",
             "cover_local_derivative_reused": "hit", "cover_local_derivative_prepared": "miss"}
    for event in events:
        if event["name"] in kinds:
            need(start <= event["boottime_us"] <= end, "cover_job_outside_startup")
            counts[kinds[event["name"]]] += 1
            need(counts["finished"] <= counts["hit"] + counts["miss"] <= counts["started"], "cover_job_order")
    need(counts["started"] == counts["finished"] == counts["hit"] + counts["miss"] == 6, "six_jobs")
    need(counts["hit"] == (6 if regime == "warm" and name != "prime" else 0), "wrong_cache_regime")
    return {"usable_ms": round((usable - start) / 1000, 3), "covers_ms": round((end - start) / 1000, 3),
            "hits": counts["hit"], "misses": counts["miss"]}


def pixels(images):
    rois = {"body": (0, 72, 1860, 2480), "right_toolbar": (930, 0, 1860, 72)}
    checked = {}
    for kind in ("software", "panel"):
        left, right = [images[side][kind] for side in ("baseline", "candidate")]
        for label, region in rois.items():
            images_roi = [image.crop(region) for image in (left, right)]
            difference = ImageChops.difference(*images_roi)
            checked[kind + "_" + label] = {"equal": difference.getbbox() is None,
                "observable": all(image.getextrema()[0] != image.getextrema()[1] for image in images_roi)}
    return checked


def screen_gate(rows, regime, valid):
    if not valid or len(rows) != 6:
        return False
    primary, guard = ("usable_ms", "covers_ms") if regime == "warm" else ("covers_ms", "usable_ms")
    return (median(pair[primary] for pair in rows) >= 100 and sum(pair[primary] > 0 for pair in rows) >= 5
            and median(pair[guard] for pair in rows) >= -50)


def confirmation_gate(rows, valid):
    if not valid or len(rows) != PAIR_COUNTS["confirmation"]:
        return False
    primary = [pair[CONFIRMATION["primary"]] for pair in rows]
    guard = [pair[CONFIRMATION["guard"]] for pair in rows]
    return (sum(value > 0 for value in primary) >= CONFIRMATION["min_faster_pairs"]
            and median(primary) >= CONFIRMATION["median_min_saving_ms"]
            and median(guard) >= -CONFIRMATION["guard_max_median_regression_ms"])


def recorded_confirmation(plan, operations):
    expected = ([("admit", "-"), ("prepare", "-")] + [("case", case) for case in plan["cases"]]
                + [("restore", "-"), ("collect", "-"), ("close", "-"), ("admit", "-")])
    operations_log = operations.get("operations", [])
    acquired, released = operations.get("acquisition", {}), operations.get("release", {})
    return (operations.get("scope") == plan["origin"] and operations.get("regime") == plan["regime"]
            and operations.get("completed") == plan["cases"]
            and [(step.get("mode"), step.get("case")) for step in operations_log] == expected
            and all(step.get("status") == 0 for step in operations_log)
            and operations.get("restoration") == ["protected_state_equal", "stock_body_equal", "native_restored"]
            and operations.get("final_native_idle") is True
            and acquired.get("project") == released.get("project") == "Aidoku"
            and acquired.get("run_id") == released.get("run_id") == "aidoku-pilot-" + plan["label"]
            and all(acquired.get(key) and acquired[key] == released.get(key) for key in ("owner_thread", "lease_token_sha256")))


def analyze(attempt):
    plan = json.loads((attempt.parent / "plan.json").read_text())
    check_protocol(plan)
    operations = json.loads((attempt / "operations.json").read_text())
    release = operations.get("release")
    recovered = False
    if not release and operations.get("recovery_required") is True:
        recovery = json.loads((attempt / "recovery.json").read_text())
        release = recovery.get("release", {})
        need(all(release.get(key) == operations["acquisition"].get(key)
                 for key in ("owner_thread", "run_id", "lease_token_sha256"))
             and [step["mode"] for step in recovery["steps"]] == ["restore", "collect", "close", "admit"]
             and all(step["status"] == 0 for step in recovery["steps"]), "wrong_recovery")
        recovered = True
    need(release and release.get("owner_and_token_checked") is True
         and release["lease_directory_absent_after_release"] is True, "missing_release")
    results, errors, images = {}, {}, {}
    with tarfile.open(attempt / "output.tar.gz", "r:gz") as archive:
        members = {member.name: member for member in archive}

        def raw(name, leaf):
            member = members["out/" + name + "/" + leaf]
            need(member.isreg() and 0 < member.size <= 16 * 1024**2, "invalid_output")
            return archive.extractfile(member).read()

        for name in plan["cases"]:
            try:
                results[name] = row(json.loads(raw(name, "application.json")), json.loads(raw(name, "state.json")),
                                    raw(name, "shell.tsv"), plan["regime"], name)
                if name.endswith("picture"):
                    meta = json.loads(raw(name, "final.metadata.json"))
                    need(meta["route"] == "library" and meta["profile"] == "scribe" and meta["backend"] == "kindle"
                         and meta["landscape"] is False and meta["rotation_degrees"] in (0, 180) and meta["window_size"] == [1860, 2480],
                         "bad_picture_metadata")
                    settle(json.loads(raw(name, "final.state.json")))
                    software = Image.open(io.BytesIO(raw(name, "final.png")))
                    need(software.size == (1860, 2480), "software_picture_geometry")
                    panel = raw(name, "panel.gray")
                    need(len(panel) == 1872 * 2480, "panel_geometry")
                    rotation, offset, virtual = raw(name, "panel.geometry").decode().split()
                    need(rotation in ("1", "3") and (virtual, offset) in {("1872,4960", "0"), ("1872,4960", "2480"),
                         ("1872,2480", "0"), ("1860,2480", "0")}, "unsupported_scanout")
                    frame = Image.frombytes("L", (1872, 2480), panel).crop((0, 0, 1860, 2480))
                    if meta["rotation_degrees"] == 180:
                        frame = frame.transpose(Image.Transpose.ROTATE_180)
                    baseline = software.convert("L")
                    aligned = [image.resize((64, 80), Image.Resampling.BOX) for image in (baseline, frame)]
                    delta = ImageStat.Stat(ImageChops.difference(*aligned)).mean[0]
                    reverse = ImageStat.Stat(ImageChops.difference(aligned[0], aligned[1].transpose(Image.Transpose.ROTATE_180))).mean[0]
                    need(delta <= 12 and reverse >= delta + 3, "panel_software_layout_disagreement")
                    images[name.split("-")[0]] = {"software": baseline, "panel": frame}
                else:
                    need(not any("out/" + name + "/" + leaf in members for leaf in ("panel.gray", "final.png")), "instrumented_primary")
            except (KeyError, ValueError, OSError) as error:
                errors[name] = str(error)
    checks = pixels(images) if len(images) == 2 else {}
    paired = []
    pair_numbers = []
    count = plan["pairs"]
    for number in range(1, count + 1):
        base, cand = [results.get(f"{side}-{number:02}") for side in ("baseline", "candidate")]
        if base and cand:
            paired.append({key: round(base[key] - cand[key], 3) for key in ("usable_ms", "covers_ms")})
            pair_numbers.append(number)
    valid = (operations.get("operations_passed") is True and not errors and len(paired) == count
             and len(checks) == 4 and all(all(check.values()) for check in checks.values()))
    confirmation = plan["mode"] == "confirmation"
    if confirmation and not recorded_confirmation(plan, operations):
        errors["operations"] = "confirmation_operation_record_mismatch"
        valid = False
    medians = {key: round(median(pair[key] for pair in paired), 3) for key in paired[0]} if len(paired) == count else None
    wins = {key: sum(pair[key] > 0 for pair in paired) for key in paired[0]} if len(paired) == count else None
    return {"regime": plan["regime"], "origin": plan["origin"], "valid_staged_cohort": valid,
            "pairs_expected": count, "wins": wins, "promising_staged_screen": screen_gate(paired, plan["regime"], valid) if count == 6 else None,
            "confirmed_staged_benefit": confirmation_gate(paired, valid) if confirmation else None,
            "primary_practical_target_met": median(pair[CONFIRMATION["primary"]] for pair in paired) >= CONFIRMATION["practical_target_ms"]
            if confirmation and valid else None,
            "operations_passed": operations.get("operations_passed", False), "separate_closeout": recovered, "errors": errors,
            "all_cases": results, "pixels": checks, "paired_savings_ms": paired, "complete_pair_numbers": pair_numbers,
            "median_paired_savings_ms": medians,
            "indexed_entry": False, "optical": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt", type=Path)
    args = parser.parse_args()
    value = analyze(args.attempt)
    with (args.attempt / "analysis.json").open("x") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")
    print(json.dumps({key: value[key] for key in ("regime", "valid_staged_cohort", "promising_staged_screen",
                                                  "confirmed_staged_benefit", "primary_practical_target_met",
                                                  "errors", "pixels", "wins", "median_paired_savings_ms")}))
