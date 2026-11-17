"""Export startup attribution without device identifiers or private transport data.

Run on test-env against the retained private experiment directories. This script
only reads completed batches; it never connects to or changes a device.
"""

import argparse
import csv
import hashlib
import io
import json
import statistics
from pathlib import Path


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def summary(values):
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def completed_report(batch):
    report = read_json(batch / "report.json")
    for key in (
        "success", "lease_released", "native_restored", "same_boot",
        "installed_program_cover_and_user_library_unchanged",
        "formal_entry_restored", "input_roster_unchanged", "lighting_restored",
    ):
        assert report[key] is True, (batch.name, key)
    return report


def trace_inputs(batch, case):
    directory = batch / "outputs/out" / case
    trace = read_json(directory / "application.json")
    assert trace["success"] and not trace["dropped_events"], case
    marks = {event["name"]: event for event in trace["events"]}
    shell = {
        line.split()[1]: round(float(line.split()[0]) * 1_000_000)
        for line in (directory / "shell.tsv").read_text().splitlines()
    }
    first = marks["startup_library_frame_submitted"]
    settled = marks["startup_visible_covers_settled"]
    assert first["marker"] is not None and settled["marker"] is not None
    assert first["boottime_us"] <= settled["boottime_us"]
    assert settled["cover_pending"] is False
    assert settled["cover_slots"] == settled["cover_images"] == 7
    state = read_json(directory / "state.json")
    assert state["route"] == "library"
    assert state["cover_visible_failure_count"] == 0
    return directory, trace, marks, shell


def route_analysis(batch):
    report = completed_report(batch)
    records = []
    for case in report["cases"]:
        if not case.rsplit("-", 1)[-1].isdigit():
            continue
        directory, trace, marks, shell = trace_inputs(batch, case)
        origin = shell["native_open_requested"]
        process_origin = (
            marks["platform_initialized"]["boottime_us"]
            - marks["platform_initialized"]["at_us"]
        )
        points = [origin, shell["document_entry"], process_origin] + [
            marks[name]["boottime_us"] for name in (
                "platform_initialized", "startup_backend_installed",
                "startup_window_shown", "startup_initial_frame_submitted",
                "startup_initial_input_ready", "application_attached",
                "startup_ready", "startup_library_frame_submitted",
                "startup_visible_covers_settled",
            )
        ]
        names = (
            "native_dispatch", "launcher_and_process_start", "platform",
            "backend", "window", "initial_submission", "initial_barrier",
            "remaining_fonts_and_attachment", "initial_view_sync",
            "first_library_render_and_submission", "cover_tail",
        )
        stages = {
            name: round((end - start) / 1000, 3)
            for name, start, end in zip(names, points, points[1:])
        }
        assert all(value >= 0 for value in stages.values())
        total = (points[-1] - origin) / 1000
        assert abs(sum(stages.values()) - total) < 0.02
        portrait = read_json(directory / "portrait.json")
        outer = (shell["portrait_done"] - shell["portrait_start"]) / 1000
        inner = max(event["at_us"] for event in portrait["events"]) / 1000
        prepared = [
            event for event in trace["events"]
            if event["name"] == "cover_preparation_finished"
        ]
        assert len(prepared) == 6, case
        first = marks["startup_library_frame_submitted"]
        settled = marks["startup_visible_covers_settled"]
        records.append({
            "case": case,
            "route": "temporary" if case.startswith("baseline-") else "indexed",
            "endpoints_ms": {
                name: round((marks[name]["boottime_us"] - origin) / 1000, 3)
                for name in (
                    "startup_ready", "startup_library_frame_submitted",
                    "startup_library_input_ready", "startup_visible_covers_settled",
                )
            },
            "stages_ms": stages,
            "first_frame_cost_ms": [round(x / 1000, 3) for x in first["frame_cost_us"]],
            "portrait_outer_ms": round(outer, 3),
            "portrait_internal_last_event_ms": round(inner, 3),
            "portrait_surround_ms": round(outer - inner, 3),
            "first_frame_to_all_covers_prepared_ms": round(
                (prepared[-1]["boottime_us"] - first["boottime_us"]) / 1000, 3),
            "all_covers_prepared_to_submission_ms": round(
                (settled["boottime_us"] - prepared[-1]["boottime_us"]) / 1000, 3),
            "total_ms": round(total, 3),
        })
    assert len(records) == 12
    groups = {route: [r for r in records if r["route"] == route]
              for route in ("temporary", "indexed")}
    result = {
        "schema_version": 1,
        "run_id": report["run_id"],
        "scope": report["scope"],
        "artifact_identities": report["variants"],
        "stage_method": (
            "Disjoint monotonic intervals; stage means sum to the mean total. "
            "Independent medians need not sum to the median total. Shell marks "
            "have 10ms resolution. Portrait internal last event and process "
            "reaping have different boundaries. Subintervals are not promised savings."
        ),
        "frame_cost_order": ["render", "prior_buffer_settle", "pixel_write", "send"],
        "routes": {},
        "records": records,
    }
    for route, group in groups.items():
        result["routes"][route] = {
            "endpoints_ms": {name: summary([r["endpoints_ms"][name] for r in group])
                             for name in group[0]["endpoints_ms"]},
            "stages_ms": {name: summary([r["stages_ms"][name] for r in group])
                          for name in group[0]["stages_ms"]},
            "budgets_ms": {name: summary([r[name] for r in group]) for name in (
                "portrait_outer_ms", "portrait_internal_last_event_ms",
                "portrait_surround_ms", "first_frame_to_all_covers_prepared_ms",
                "all_covers_prepared_to_submission_ms", "total_ms",
            )},
            "first_frame_render_ms": summary([r["first_frame_cost_ms"][0] for r in group]),
        }
    result["indexed_extra_median_ms"] = {
        name: round(result["routes"]["indexed"]["endpoints_ms"][name]["median"]
                    - result["routes"]["temporary"]["endpoints_ms"][name]["median"], 3)
        for name in records[0]["endpoints_ms"]
    }
    result["all_six_pairs_slower_on_indexed_route"] = all(
        next(r for r in groups["indexed"] if r["case"].endswith(f"-{number:02d}"))
        ["endpoints_ms"][endpoint]
        > next(r for r in groups["temporary"] if r["case"].endswith(f"-{number:02d}"))
        ["endpoints_ms"][endpoint]
        for number in range(1, 7)
        for endpoint in ("startup_library_frame_submitted", "startup_visible_covers_settled")
    )
    assert result["all_six_pairs_slower_on_indexed_route"]
    return result


def sampled_window(rows, origin, end, ticks_per_second):
    selected = [row for row in rows if origin <= row["boottime_us"] <= end]
    result = {"requested_duration_ms": round((end - origin) / 1000, 3), "roles": {}}
    system = [row for row in selected if row["kind"] == "system"]
    assert len(system) >= 2
    result["frequency_khz_mean"] = round(statistics.mean(row["frequency_khz"] for row in system))
    for role in sorted({row["role"] for row in selected if row["kind"] == "process"}):
        samples = [row for row in selected if row["kind"] == "process" and row["role"] == role]
        assert len({(row["pid"], row["birth"]) for row in samples}) == 1, role
        first, last = samples[0], samples[-1]
        result["roles"][role] = {
            "samples": len(samples),
            "observed_window_ms": round((last["boottime_us"] - first["boottime_us"]) / 1000, 3),
            "cpu_ms": round((last["user_ticks"] + last["system_ticks"]
                             - first["user_ticks"] - first["system_ticks"])
                            * 1000 / ticks_per_second, 3),
            "main_run_ms": round((last["main_run_ns"] - first["main_run_ns"]) / 1e6, 3),
            "main_wait_ms": round((last["main_wait_ns"] - first["main_wait_ns"]) / 1e6, 3),
            "major_fault_delta": last["major_faults"] - first["major_faults"],
        }
    return result


def scheduler_analysis(batch):
    report = completed_report(batch)
    records = []
    previous_after = None
    for case in report["cases"]:
        directory, trace, marks, shell = trace_inputs(batch, case)
        before, after = [
            (directory / f"catalog-id-{position}.sha256").read_text().split()[0]
            for position in ("before", "after")
        ]
        assert all(len(value) == 64 and value != hashlib.sha256(b"").hexdigest()
                   for value in (before, after))
        assert previous_after is None or before == previous_after
        previous_after = after
        rows_after = len((directory / "catalog-after.tsv").read_text().splitlines())
        assert rows_after == 1
        record = {
            "case": case,
            "route": "temporary" if case.startswith("baseline-") else "indexed",
            "warmup": case.endswith("-warmup"),
            "catalog_identity_changed": before != after,
            "catalog_rows_after": rows_after,
            "document_mtime_changed": (
                (directory / "document-mtime-before.txt").read_text()
                != (directory / "document-mtime-after.txt").read_text()),
        }
        if not record["warmup"]:
            lines = (directory / "system.csv").read_text().splitlines()
            assert lines[0].startswith("# clock_ticks_per_second=")
            ticks = int(lines[0].split("=")[1])
            assert ticks > 0
            data = io.StringIO("\n".join(line for line in lines if not line.startswith("#")))
            rows = [{key: value if key in ("kind", "role") else int(value)
                     for key, value in row.items()} for row in csv.DictReader(data)]
            record["clock_ticks_per_second"] = ticks
            origin = shell["native_open_requested"]
            ends = {
                "first_two_seconds": origin + 2_000_000,
                "first_library": marks["startup_library_frame_submitted"]["boottime_us"],
                "settled_covers": marks["startup_visible_covers_settled"]["boottime_us"],
            }
            record["windows"] = {name: sampled_window(rows, origin, end, ticks)
                                 for name, end in ends.items()}
        records.append(record)
    return {
        "schema_version": 1,
        "run_id": report["run_id"],
        "method": (
            "Two warmups followed by AB/BA diagnostic launches. Read-only 100ms "
            "sampling is excluded from primary latency statistics. Process CPU "
            "is user+system ticks; schedstat covers only the main thread. Deltas "
            "use the first and last samples inside each window, without "
            "extrapolation. New-process discovery and final fractional sample "
            "intervals are omitted. These values cannot be added to wall time."
        ),
        "warmup_caveat": (
            "The initial temporary-route warmup also observes catalog identity "
            "replacement because the research document was just staged. Both "
            "warmups are excluded from route attribution."
        ),
        "records": records,
        "native_state_and_original_document_restored": True,
        "lease_released": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route-batch", type=Path, required=True)
    parser.add_argument("--scheduler-batch", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    route = route_analysis(args.route_batch)
    scheduler = scheduler_analysis(args.scheduler_batch)
    scheduler["same_boot_as_primary_batch"] = (
        (args.route_batch / "boot-before.stdout").read_bytes()
        == (args.scheduler_batch / "boot-before.stdout").read_bytes()
    )
    assert scheduler["same_boot_as_primary_batch"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "route-comparison.json", route)
    write_json(args.output_dir / "scheduler-diagnostic.json", scheduler)
    print("EXPORTED", len(route["records"]), "primary samples and",
          len(scheduler["records"]), "diagnostic/warmup records")
    print("INDEXED_EXTRA_MEDIAN_MS", route["indexed_extra_median_ms"])


if __name__ == "__main__":
    main()
