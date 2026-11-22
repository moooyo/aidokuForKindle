import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
import time


def epoch(year, month, day, hour, minute, second=0):
    return int(datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp())


INSTANTS = (
    epoch(2026, 1, 1, 23, 59, 59),
    epoch(2026, 1, 2, 0, 0),
    epoch(2026, 3, 8, 6, 59, 59),
    epoch(2026, 3, 8, 7, 0),
    epoch(2026, 11, 1, 5, 30),
    epoch(2026, 11, 1, 6, 30),
    epoch(2040, 6, 1, 12, 0),
)


def run(command, environment):
    return subprocess.check_output(command, env=environment, text=True).strip()


def compare(candidate, environment, instant):
    original = run(["date", "-d", f"@{instant}", "+%H:%M"], environment)
    current = run([*candidate, "epoch", str(instant)], environment)
    return {"date": original, "chrono": current, "equal": original == current}


def median_p95(values):
    ordered = sorted(values)
    return {"median_us": statistics.median(values), "p95_us": ordered[(len(ordered) * 95 + 99) // 100 - 1]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", type=Path)
    parser.add_argument("--qemu", action="store_true")
    parser.add_argument("--bench", type=int, default=30)
    args = parser.parse_args()
    candidate = (["qemu-arm", "-cpu", "cortex-a7"] if args.qemu else []) + [str(args.binary)]
    environment = os.environ.copy()
    environment.pop("TZ", None)
    environment.pop("TZDIR", None)
    environment["LC_ALL"] = "C"
    scenarios = {"unset": None, "empty": "", "UTC": "UTC", "Shanghai": "Asia/Shanghai",
                 "New_York": "America/New_York", "London": "Europe/London",
                 "Lord_Howe": "Australia/Lord_Howe", "Chatham": "Pacific/Chatham",
                 "missing": ":/nonexistent/aidoku-timezone"}
    results = {}
    for label, zone in scenarios.items():
        case_env = environment.copy()
        if zone is not None:
            case_env["TZ"] = zone
        values = [compare(candidate, case_env, instant) for instant in INSTANTS]
        results[label] = {"samples": len(values), "mismatches": sum(not value["equal"] for value in values)}
        if label != "missing" and results[label]["mismatches"]:
            raise RuntimeError(f"ordinary timezone mismatch: {label}")
    with tempfile.TemporaryDirectory(prefix="aidoku-clock-") as temporary:
        directory = Path(temporary)
        zone_file = directory / "Asia" / "Shanghai"
        zone_file.parent.mkdir()
        shutil.copyfile("/usr/share/zoneinfo/Europe/London", zone_file)
        custom = environment | {"TZ": "Asia/Shanghai", "TZDIR": str(directory)}
        custom_result = compare(candidate, custom, INSTANTS[-1])
        if custom_result["equal"]:
            raise RuntimeError("TZDIR fixture did not demonstrate differing lookup")
        dynamic = directory / "dynamic-zone"
        shutil.copyfile("/usr/share/zoneinfo/America/New_York", dynamic)
        changing = environment | {"TZ": ":" + str(dynamic)}
        process = subprocess.Popen([*candidate, "stream"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=changing, text=True)
        try:
            process.stdin.write(str(INSTANTS[-1]) + "\n")
            process.stdin.flush()
            chrono_before = process.stdout.readline().strip()
            date_before = run(["date", "-d", f"@{INSTANTS[-1]}", "+%H:%M"], changing)
            replacement = directory / "replacement"
            shutil.copyfile("/usr/share/zoneinfo/Asia/Tokyo", replacement)
            os.replace(replacement, dynamic)
            time.sleep(1.2)
            process.stdin.write(str(INSTANTS[-1]) + "\n")
            process.stdin.flush()
            chrono_after = process.stdout.readline().strip()
            date_after = run(["date", "-d", f"@{INSTANTS[-1]}", "+%H:%M"], changing)
        finally:
            process.stdin.close()
            process.wait(timeout=5)
        if chrono_before != date_before or chrono_after == date_after or date_before == date_after:
            raise RuntimeError("timezone replacement fixture did not demonstrate stale cache")
        changing_result = {"before": {"date": date_before, "chrono": chrono_before},
                           "after": {"date": date_after, "chrono": chrono_after}}
    report = {"ordinary": results, "custom_tzdir": custom_result, "changing_zone_file": changing_result,
              "scope": "GNU date and chrono on test-env, not Kindle firmware"}
    if args.bench:
        samples = run([*candidate, "bench", str(args.bench)], environment).splitlines()
        pairs = [tuple(map(int, sample.split())) for sample in samples]
        if len(pairs) != args.bench or any(len(pair) != 2 for pair in pairs):
            raise RuntimeError("invalid benchmark samples")
        report["timings"] = {"pairs": len(pairs), "local": median_p95([pair[0] for pair in pairs]),
                             "date_with_5ms_poll": median_p95([pair[1] for pair in pairs]),
                             "first_local_us": pairs[0][0], "unit": "microseconds"}
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
