#!/usr/bin/env python3
"""Measure frozen cache implementations on the designated remote Linux worker."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def source_manifest(directory: Path) -> dict:
    files = [directory / name for name in ("Cargo.toml", "Cargo.lock", "build.rs", ".cargo/config.toml")]
    for folder, suffixes in [("src", {".rs"}), ("examples", {".rs"}), ("ui", {".slint"}), ("vendor", {".rs", ".toml", ".json", ".slint"})]:
        files.extend(path for path in (directory / folder).rglob("*") if path.suffix in suffixes)
    files.extend(path for path in (directory / "assets").rglob("*") if path.is_file())
    for name in ["benchmark-cache.py", "benchmark-downloads.py"]:
        files.append(directory / "scripts" / name)
    hashes = {str(path.relative_to(directory)).replace(os.sep, "/"): sha256(path) for path in sorted(set(files)) if path.is_file()}
    body = "\n".join(f"{name} {digest}" for name, digest in sorted(hashes.items())).encode()
    return {"sha256": hashlib.sha256(body).hexdigest(), "files": hashes}


def load_measurement_helper() -> object:
    path = Path(__file__).resolve().with_name("benchmark-downloads.py")
    specification = importlib.util.spec_from_file_location("download_measurement", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def verify_result(measurement: dict) -> None:
    result = measurement["result"]
    check(result.get("build_profile") == "release", "A cache benchmark executable is not a release build")
    check(result.get("build_features") == [], "Both cache implementations must use --no-default-features")
    check(bool(result.get("checks")) and all(result["checks"].values()), "A cache correctness assertion failed")
    check(result.get("sql", {}).get("statement_tracking_overflows", 0) == 0, "The SQL statement tracker overflowed")
    check(measurement["peak_rss_kib"] > 0, "The native launcher did not record process RSS")


def summarize(report: dict) -> dict:
    summary = {"iteration": report["iteration"], "fixture": report["fixture"], "implementations": report["implementations"], "checks": report["checks"], "sizes": {}}
    for size in report["config"]["sizes"]:
        rows = {}
        for scenario in ["pin-noop", "clear-foreground"]:
            implementations = {}
            for label in ["baseline", "optimized"]:
                trials = [value for value in report["measurements"].values() if value["label"] == label and value["entries"] == size and value["scenario"] == scenario]
                if not trials:
                    continue
                implementations[label] = {
                    "operation_wall_seconds": [trial["result"]["operation_wall_seconds"] for trial in trials],
                    "median_operation_wall_seconds": statistics.median(trial["result"]["operation_wall_seconds"] for trial in trials),
                    "max_process_rss_kib": max(trial["peak_rss_kib"] for trial in trials),
                    "sql_operation": [trial["result"]["sql"]["roles"]["operation"] for trial in trials],
                    "sql_foreground": [trial["result"]["sql"]["roles"]["foreground"] for trial in trials],
                }
                if scenario == "pin-noop":
                    implementations[label]["per_pin_latency"] = [trial["result"]["pin_latency"] for trial in trials]
                else:
                    implementations[label]["foreground_overlapping"] = [trial["result"]["foreground_overlapping"] for trial in trials]
            if implementations:
                rows[scenario] = implementations
        summary["sizes"][str(size)] = rows
    return summary


def run(arguments: argparse.Namespace) -> None:
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    helper = load_measurement_helper()
    launcher, launcher_manifest = helper.make_measurement_launcher(output)
    binaries = {"baseline": arguments.baseline_binary.resolve(), "optimized": arguments.optimized_binary.resolve()}
    sources = {"baseline": arguments.baseline_source.resolve(), "optimized": arguments.optimized_source.resolve()}
    implementations = {label: {"binary": str(binary), "binary_sha256": sha256(binary), "source": source_manifest(sources[label]), "profile": "release", "features": []} for label, binary in binaries.items()}
    check(implementations["baseline"]["source"]["files"]["examples/cache-benchmark.rs"] == implementations["optimized"]["source"]["files"]["examples/cache-benchmark.rs"], "The implementations use different benchmark code")
    production_names = {name for item in implementations.values() for name in item["source"]["files"]}
    production_delta = sorted(name for name in production_names if implementations["baseline"]["source"]["files"].get(name) != implementations["optimized"]["source"]["files"].get(name))
    check(production_delta == ["src/cache.rs"], "The controlled cache comparison changes production code outside src/cache.rs")
    report = {
        "schema_version": 1,
        "iteration": "cache entry scaling after the earlier download benchmark",
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": helper.environment_metadata(),
        "implementations": implementations,
        "production_source_delta": production_delta,
        "measurement_launcher": launcher_manifest,
        "config": {**vars(arguments), "baseline_binary": str(binaries["baseline"]), "optimized_binary": str(binaries["optimized"]), "baseline_source": str(sources["baseline"]), "optimized_source": str(sources["optimized"]), "output": str(output)},
        "fixture": None,
        "measurements": {},
        "checks": {},
        "syscall_audits": {},
        "metric_definitions": {
            "sql": "Actual SQLite statement executions from trace_v2; actual core-table row events from update_hook. DML attempts, SELECT/aggregate queries, explicit transaction boundaries, and autocommit DML attempts remain separate. These are not physical disk write counts.",
            "rss": "Actual measured process maximum RSS from the qualified low-RSS native launcher. Fixture creation runs in a separate process.",
            "foreground": "Same-manager pinned raw lookup with local_path=None; only recorded call intervals intersecting clear's first SQLite work through clear return enter latency percentiles.",
        },
    }

    def persist() -> None:
        write_json(output / "results.json", report)
        write_json(output / "summary.json", summarize(report))

    def seed(name: str, entries: int, kind: str) -> Path:
        data = output / "data" / name
        logs = output / "seed" / name
        logs.mkdir(parents=True)
        command = [str(binaries["baseline"]), "--scenario", "seed", "--data-dir", str(data), "--entries", str(entries), "--fixture-kind", kind]
        with (logs / "stdout.json").open("w") as stdout, (logs / "stderr.log").open("w") as stderr:
            executed = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=arguments.timeout_seconds, check=False)
        check(executed.returncode == 0, f"Fixture creation failed: {logs / 'stderr.log'}")
        fixture = json.loads((logs / "stdout.json").read_text())
        check(fixture.get("build_profile") == "release" and fixture["fixture_entries"] == entries and all(fixture["checks"].values()), "The seed process produced an invalid fixture")
        identity = {key: fixture[key] for key in ["width", "height", "bytes_per_entry", "image_sha256"]}
        if report["fixture"] is None:
            report["fixture"] = identity
        else:
            check(report["fixture"] == identity, "Fixture geometry, bytes, or payload hash changed between implementations or sizes")
        write_json(logs / "command.json", command)
        return data

    def measure(name: str, label: str, scenario: str, entries: int, data: Path) -> dict:
        print(f"Running {name}", flush=True)
        command = [str(binaries[label]), "--scenario", scenario, "--data-dir", str(data), "--entries", str(entries), "--pin-operations", str(arguments.pin_operations), "--foreground-interval-us", str(arguments.foreground_interval_us)]
        measurement = helper.run_process(command, output / name, arguments.timeout_seconds, launcher)
        measurement.update(label=label, scenario=scenario, entries=entries)
        report["measurements"][name] = measurement
        persist()
        verify_result(measurement)
        return measurement

    def audit_fsync(name: str, label: str, scenario: str, entries: int, data: Path) -> None:
        tracer = shutil.which("strace")
        if tracer is None:
            report["syscall_audits"][name] = {"available": False, "reason": "strace is not installed on the designated remote worker"}
            return
        directory = output / name
        directory.mkdir()
        command = [tracer, "-f", "-qq", "-e", "trace=fsync,fdatasync", "-o", str(directory / "syscalls.log"), str(binaries[label]), "--scenario", scenario, "--data-dir", str(data), "--entries", str(entries), "--pin-operations", str(arguments.pin_operations), "--foreground-interval-us", str(arguments.foreground_interval_us)]
        with (directory / "stdout.json").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
            executed = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=arguments.timeout_seconds, check=False)
        check(executed.returncode == 0, f"The separate syscall audit failed: {directory}")
        result = json.loads((directory / "stdout.json").read_text())
        check(result["build_profile"] == "release" and all(result["checks"].values()), "The syscall audit failed its correctness assertions")
        lines = (directory / "syscalls.log").read_text().splitlines()
        counts = {name: sum(re.search(rf"\b{name}\(", line) is not None for line in lines) for name in ["fsync", "fdatasync"]}
        successful = {name: sum(re.search(rf"\b{name}\([^\n]*\)\s+=\s+0\b", line) is not None for line in lines) for name in ["fsync", "fdatasync"]}
        report["syscall_audits"][name] = {"available": True, "label": label, "scenario": scenario, "entries": entries, "command": command, "calls": counts, "successful_calls": successful, "scope": "Whole measured executable lifecycle, including open/warm/operation/verification/drop; seed excluded. Separate traced pass; its wall time is never used for performance conclusions."}
        write_json(directory / "audit.json", report["syscall_audits"][name])

    try:
        for label in binaries:
            name = f"observer-{label}"
            data = output / "data" / name
            measured = measure(name, label, "observer-qualification", 8, data)
            check(measured["result"]["sql"]["roles"]["operation"]["dml_statements"] == 5, "The SQL observer did not count the known DML executions")
        report["checks"]["real_sql_observer_qualified_on_both_binaries"] = True

        for entries in arguments.sizes:
            for scenario, kind in [("pin-noop", "pinned"), ("clear-foreground", "clearable")]:
                if arguments.only not in {"all", scenario}:
                    continue
                for repetition in range(1, arguments.repetitions + 1):
                    order = ["baseline", "optimized"] if repetition % 2 else ["optimized", "baseline"]
                    for label in order:
                        name = f"{scenario}-{entries}-{label}-{repetition}"
                        data = seed(name, entries, kind)
                        measured = measure(name, label, scenario, entries, data)
                        operation_sql = measured["result"]["sql"]["roles"]["operation"]
                        if scenario == "pin-noop":
                            check(measured["result"]["pin_operations"] == arguments.pin_operations, "The implementations ran a different number of pin operations")
                            if label == "optimized":
                                check(operation_sql["dml_statements"] == 0 and operation_sql["autocommit_dml_statements"] == 0 and operation_sql["commits"] == 0 and operation_sql["aggregate_selects"] == 0, "A warmed same-owner pin still writes or scans totals")
                            else:
                                check(operation_sql["dml_statements"] > 0 and operation_sql["aggregate_selects"] > 0, "The frozen baseline did not exercise the identified pin path")
                        else:
                            foreground = measured["result"]["foreground_overlapping"]
                            check(foreground["samples"] > 0 and foreground["p95_ms"] is not None, "There is no foreground overlap latency evidence")
                            if label == "optimized":
                                check(operation_sql["aggregate_selects"] == 0, "Optimized clear still scans full totals during its loop")
                report["checks"][f"{scenario}_{entries}_same_fixture_and_correctness"] = True
                persist()

        if arguments.only in {"all", "race-safety"}:
            for label in binaries:
                name = f"race-safety-{label}"
                data = seed(name, arguments.race_entries, "clearable")
                measured = measure(name, label, "race-safety", arguments.race_entries, data)
                coverage = measured["result"]["race_coverage"]
                check(all(case["attempt_overlapped_clear"] for case in coverage), "A race request was scheduled entirely after clear; repeat with a larger race fixture")
                if label == "optimized":
                    check(any(case["name"] == "pin_after_lease" and case["api_completed_inside_clear"] for case in coverage), "The optimized clear did not demonstrate foreground pin interleaving")
                    check(any(case["name"] == "protected_replacement_and_new_commit" and case["api_completed_inside_clear"] for case in coverage), "The optimized clear did not demonstrate protected commit interleaving")
            report["checks"]["pins_leases_reservations_commits_and_memo_invalidation_safe"] = True
        if arguments.audit_fsync:
            for entries in arguments.sizes:
                for scenario, kind in [("pin-noop", "pinned"), ("clear-foreground", "clearable")]:
                    if arguments.only not in {"all", scenario}:
                        continue
                    for label in binaries:
                        name = f"syscall-audit-{scenario}-{entries}-{label}"
                        data = seed(name, entries, kind)
                        audit_fsync(name, label, scenario, entries, data)
                        persist()
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        persist()
        print(json.dumps({"output": str(output), "checks": report["checks"]}, indent=2), flush=True)
    except Exception:
        report["failure"] = traceback.format_exc()
        persist()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-binary", type=Path, required=True)
    parser.add_argument("--optimized-binary", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--optimized-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", nargs="+", type=int, default=[1000, 5000, 10000])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--pin-operations", type=int, default=1000)
    parser.add_argument("--foreground-interval-us", type=int, default=1000)
    parser.add_argument("--race-entries", type=int, default=1000)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--audit-fsync", action="store_true")
    parser.add_argument("--only", choices=["all", "pin-noop", "clear-foreground", "race-safety"], default="all")
    arguments = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run this benchmark only on the designated remote Linux worker")
    if min(arguments.sizes) < 8 or arguments.pin_operations < 1 or arguments.repetitions < 1 or arguments.race_entries < 8 or arguments.foreground_interval_us < 0:
        parser.error("Use positive repetitions and pin operations, at least eight entries, and a nonnegative probe interval")
    run(arguments)


if __name__ == "__main__":
    main()
