"""Extract the six timed JPEG probes from the closed, rejected warm revision."""

import argparse
import json
from pathlib import Path
from statistics import median
import tarfile


MARKS = ("startup_jpeg_capability_started", "startup_jpeg_capability_finished",
         "platform_initialized", "startup_initial_frame_submitted", "startup_library_frame_submitted")
COLUMNS = ["visit", "probe_start_us", "probe_finish_us", "platform_initialized_us",
           "initial_full_submitted_us", "first_library_submitted_us", "probe_elapsed_us"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_member(archive, path):
    members = [member for member in archive.getmembers() if member.name == path]
    require(len(members) == 1 and members[0].isreg() and 0 < members[0].size <= 2 * 1024**2,
            "Missing, ambiguous or invalid case record")
    with archive.extractfile(members[0]) as stream:
        return json.load(stream)


def visit_row(number, trace, state):
    require(trace["clock"] == "monotonic_rust_main" and trace["dropped_events"] == 0
            and all(trace[key] is True for key in ("enabled", "finished", "success")), "Incomplete trace")
    require(state["route"] == "library" and state["library_count"] == 6 and state["demo"] is False
            and state["hardware_media_available"] is True and state["hardware_media_reason"] == "available"
            and state["hardware_acceleration"] is False and state["native_media"]["attempts"] == 0,
            "Unexpected shelf or hardware JPEG state")
    events = trace["events"]
    marks = []
    for name in MARKS:
        matches = [event for event in events if event["name"] == name]
        require(len(matches) == 1, "Missing or ambiguous " + name)
        marks.append(matches[0])
    for clock in ("at_us", "boottime_us"):
        times = [mark[clock] for mark in marks]
        require(all(type(value) is int and value > 0 for value in times)
                and all(before < after for before, after in zip(times, times[1:])), "Invalid event order")
    initial, library = marks[-2:]
    covers = [event for event in events if event["name"] == "startup_cover_frame_submitted"]
    fields = ("marker", "frame_sequence", "cover_slots", "cover_images", "cover_pending")
    require(initial["full"] is True and 0 < initial["frame_sequence"] < library["frame_sequence"]
            and library["marker"] > 0 and library["cover_slots"] == 7 and library["cover_images"] == 0
            and library["cover_pending"] is True and covers and all(library[key] == covers[0][key] for key in fields),
            "Unqualified first Library submission")
    require(sum(event["name"] == "cover_local_derivative_reused" for event in events) == 6, "Not six warm hits")
    times = [mark["at_us"] for mark in marks]
    return [number, *times, times[1] - times[0]]


def replay(attempt):
    plan = json.loads((attempt.parent / "plan.json").read_text(encoding="utf-8"))
    verdict = json.loads((attempt / "analysis.json").read_text(encoding="utf-8"))
    visits = list(range(1, 7))
    require(plan["origin"] == "pre-exec of staged managed foreground launcher"
            and (plan["regime"], plan["mode"], plan["pairs"]) == ("warm", "screen", len(visits))
            and verdict["valid_staged_cohort"] is True and verdict["operations_passed"] is True
            and verdict["promising_staged_screen"] is False and verdict["complete_pair_numbers"] == visits,
            "Expected the closed, qualified warm cohort with the rejected candidate")
    with tarfile.open(attempt / "output.tar.gz", "r:gz") as archive:
        rows = []
        for number in visits:
            case = f"out/candidate-{number:02}"
            rows.append(visit_row(number, read_member(archive, case + "/application.json"),
                                  read_member(archive, case + "/state.json")))
    elapsed = [row[-1] for row in rows]
    return {"columns": COLUMNS, "rows": rows, "median_probe_us": median(elapsed),
            "range_probe_us": [min(elapsed), max(elapsed)]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt", type=Path)
    parser.add_argument("--check", action="store_true", help="Compare against the published observations")
    args = parser.parse_args()
    result = replay(args.attempt)
    if args.check:
        expected = json.loads(Path(__file__).with_name("historical.json").read_text(encoding="utf-8"))
        require(result == expected["observations"], "Published observations differ from replay")
    print(json.dumps(result))
