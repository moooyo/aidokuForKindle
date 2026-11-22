"""Summarize one instrumented foreground launcher visit, not an acceptance run."""

import argparse
import json
from pathlib import Path
import re


PHASES = (
    "worker_enter", "observer_start", "observer_wait_begin", "observer_ready",
    "portrait_begin", "portrait_ready", "display_begin", "display_ready",
    "app_spawn_begin", "app_spawn_returned",
)
PREFIX = b"aidoku: launcher-diag "
ROW = re.compile(rb"aidoku: launcher-diag v1 ([a-z_]+) ([0-9]{1,14})\.([0-9]{2})\n")


def summarize(lines):
    stamps = {}
    for line in lines:
        if not line.startswith(PREFIX):
            continue
        match = ROW.fullmatch(line)
        if match is None:
            raise ValueError("Malformed or unsupported launcher diagnostic")
        phase = match[1].decode("ascii")
        if len(stamps) == len(PHASES) or phase != PHASES[len(stamps)]:
            raise ValueError("Unexpected phase: supply exactly one ordered visit")
        stamp = int(match[2]) * 100 + int(match[3])
        if stamps and stamp < stamps[PHASES[len(stamps) - 1]]:
            raise ValueError("Launcher clock moved backwards")
        stamps[phase] = stamp
    if len(stamps) != len(PHASES):
        raise ValueError("Incomplete visit: missing " + PHASES[len(stamps)])

    def bounds(start, end):
        elapsed_ms = (stamps[end] - stamps[start]) * 10
        return [max(0, elapsed_ms - 10), elapsed_ms + 10]

    return {
        "scope": "One instrumented launcher, not process births, app readiness or a speed comparison",
        "clock": "/proc/uptime (10 ms bins); each pair is a lower and upper elapsed-time bound",
        "adjacent_bounds_ms": {
            f"{start}_to_{end}": bounds(start, end) for start, end in zip(PHASES, PHASES[1:])
        },
        "overlapping_bounds_ms": {
            "observer_start_to_ready_including_generation_work": bounds("observer_start", "observer_ready"),
            "marked_worker_to_app_spawn_returned": bounds("worker_enter", "app_spawn_returned"),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("visit_log", type=Path, help="Private stderr log from exactly one diagnostic visit")
    args = parser.parse_args()
    try:
        with args.visit_log.open("rb") as stream:
            result = summarize(stream)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot report launcher stages: {error}\n")
    print(json.dumps(result, indent=2))
