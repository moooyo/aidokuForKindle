"""Pair a qualified stock-card release with one native launcher's scoped log."""

import argparse
import json
from pathlib import Path
import re
import sys


LIMIT = 1024 * 1024
START = rb"aidoku: Launcher worker started \(pid=[1-9][0-9]*\)\."
EXIT = b"aidoku: Launcher worker exited with status 0."
PREFIX = b"aidoku: startup_library_submitted "
NUMBER = rb"([1-9][0-9]{0,19})"
EVENT = re.compile(re.escape(PREFIX) + rb"boottime_us=" + NUMBER + rb" sequence=" + NUMBER
                   + rb" marker=" + NUMBER + rb" layout=(large|compact)"
                   + rb" library=(empty|nonempty) grid=(empty|nonempty)")


def small(path, limit):
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("case_record_too_large")
    return value


def interval(case, delta):
    if (re.fullmatch(r"[a-z][a-z0-9-]{0,31}", case.name) is None
            or small(case / "origin", 32) != b"stock-tap\n"
            or small(case / "qualified", 64) != f"{case.name} stock-tap\n".encode("ascii")):
        raise ValueError("unqualified_stock_visit")
    bracket = small(case / "pointer-brackets", 128)
    if small(case / "anchor", 128) != bracket:
        raise ValueError("anchor_is_not_same_visit_pointer")
    fields = re.fullmatch(b" ".join([NUMBER] * 4) + rb"\n", bracket)
    if not fields:
        raise ValueError("invalid_pointer_brackets")
    down_before, down_after, up_before, up_after = map(int, fields.groups())
    if not 0 < down_before <= down_after < up_before <= up_after < 2**64:
        raise ValueError("invalid_pointer_clock_order")

    if len(delta) > LIMIT:
        raise ValueError("launcher_delta_too_large")
    lines = delta.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith(b"aidoku: Launcher worker started")]
    exits = [index for index, line in enumerate(lines) if line.startswith(b"aidoku: Launcher worker exited")]
    frames = [index for index, line in enumerate(lines) if line.startswith(PREFIX.rstrip())]
    if (len(starts) != 1 or len(exits) != 1 or len(frames) != 1
            or not starts[0] < frames[0] < exits[0] or not re.fullmatch(START, lines[starts[0]])
            or lines[exits[0]] != EXIT):
        raise ValueError("missing_or_ambiguous_closed_visit")
    event = EVENT.fullmatch(lines[frames[0]])
    if not event:
        raise ValueError("invalid_submitted_event")
    when, sequence, marker = map(int, event.groups()[:3])
    layout, library, grid = (value.decode("ascii") for value in event.groups()[3:])
    if (not up_after <= when < 2**64 or sequence >= 2**64 or marker >= 2**32
            or (library == "empty" and grid != "empty")):
        raise ValueError("invalid_clock_or_library_frame")
    return {"start": "stock_card_release", "end": "library_frame_submission_callback",
            "lower_us": when - up_after, "upper_us": when - up_before,
            "layout": layout, "library": library, "grid": grid}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path, help="Qualified case directory; feed only its launcher delta on stdin")
    args = parser.parse_args()
    try:
        result = interval(args.case, sys.stdin.buffer.read(LIMIT + 1))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Not measured: {error if isinstance(error, ValueError) else 'missing_case_record'}.\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
