#!/usr/bin/env python3
"""Measure the frozen Phase 3 example on the designated remote Linux worker.

Preparation, uninstrumented timing, SQL observation, and syscall observation use
separate processes. The historical Candidate5 application is provenance context
only; this runner never treats it as an executable benchmark baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
import traceback
from pathlib import Path


WORKLOADS = ("store", "render", "projection", "publication")
SYNC_CALLS = ("fsync", "fdatasync")
PID_PREFIX = re.compile(r"^\s*(?:\[pid\s+(\d+)\]\s+|(\d+)\s+)?(.*)$")
STDERR_WRITE = re.compile(r'^write\(2,\s*"((?:\\.|[^"\\])*)",\s*\d+\)\s+=\s+(\d+)\b')
COMMIT_MARKER = re.compile(r"^PHASE3_COMMIT_(BEGIN|END):(\d+)(?:\\r)?$")
SYNC_START = re.compile(r"^(fsync|fdatasync)\(")
SYNC_RESUME = re.compile(r"^<\.\.\. (fsync|fdatasync) resumed>")
SYNC_SUCCESS = re.compile(r"=\s+0(?:\s|$)")
USAGE_LIBRARY_TEST = "app::download_usage::tests::benchmark_completed_download_usage_production"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def source_manifest(root: Path) -> dict:
    paths = [root / name for name in ("Cargo.toml", "Cargo.lock", "build.rs", ".cargo/config.toml")]
    for folder, suffixes in (
        ("src", {".rs"}),
        ("examples", {".rs"}),
        ("ui", {".slint"}),
        ("vendor", {".rs", ".toml", ".json", ".slint"}),
    ):
        paths.extend(path for path in (root / folder).rglob("*") if path.suffix in suffixes)
    paths.extend(path for path in (root / "assets").rglob("*") if path.is_file())
    paths.extend(root / "scripts" / name for name in ("benchmark-phase3.py", "benchmark-downloads.py"))
    hashes = {path.relative_to(root).as_posix(): sha256(path) for path in sorted(set(paths)) if path.is_file()}
    body = "\n".join(f"{name} {digest}" for name, digest in sorted(hashes.items())).encode()
    return {"sha256": hashlib.sha256(body).hexdigest(), "files": hashes}


def context_provenance(binary: Path, source: Path) -> dict:
    return {
        "scope": "Historical Candidate5 context only; no before/after ratio or compatible benchmark baseline is inferred.",
        "binary": str(binary),
        "binary_available": binary.is_file(),
        "binary_sha256": sha256(binary) if binary.is_file() else None,
        "source": str(source),
        "source_available": source.is_dir(),
        "source_manifest": source_manifest(source) if source.is_dir() else None,
    }


def load_measurement_helper(path: Path) -> object:
    specification = importlib.util.spec_from_file_location("phase3_download_measurement", path)
    check(specification is not None and specification.loader is not None, "Cannot load the existing measurement helper")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def run_capture(command: list[str], output: Path, timeout: float) -> dict:
    """Capture preparation or a traced audit without treating its wall time as a result."""
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "command.json", command)
    started = time.perf_counter()
    record = {"command": command, "started_utc": utc_now(), "wall_time_scope": "Diagnostic only; excluded from operation timing summaries."}
    process = None
    try:
        with (output / "stdout.json").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                process.wait(timeout=timeout)
            except BaseException:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                raise
        record["exit_code"] = process.returncode
        check(process.returncode == 0, f"Process failed: {output / 'stderr.log'}")
        record["result"] = json.loads((output / "stdout.json").read_text(encoding="utf-8"))
        return record
    except BaseException:
        record["failure"] = traceback.format_exc()
        if process is not None:
            record["exit_code"] = process.returncode
        raise
    finally:
        record["finished_utc"] = utc_now()
        record["wall_seconds"] = time.perf_counter() - started
        write_json(output / "process.json", record)


def normalize_scenario(value: object) -> str:
    return str(value).replace("_", "-").lower()


def verify_result(result: dict, scenario: str, sql_only: bool = False) -> None:
    check(isinstance(result, dict), "A scenario did not return a JSON object")
    check(result.get("build_profile") == "release", "The executable must use the release profile")
    check(result.get("build_features") == [], "The executable must use --no-default-features")
    check(normalize_scenario(result.get("scenario")) == scenario, f"Unexpected scenario identity: {result.get('scenario')}")
    check(result.get("sql_only") is sql_only, "The scenario did not confirm the requested SQL observation mode")
    checks = result.get("checks")
    check(isinstance(checks, dict) and bool(checks), "A scenario omitted its correctness checks")
    check(all(value is True for value in checks.values()), f"A scenario correctness check failed: {checks}")
    operations = result.get("operations")
    check(isinstance(operations, dict), "A scenario omitted its operations map")
    if scenario not in {"prepare", "qualify"}:
        check(bool(operations), "A measured scenario has no operations")


def sql_records(value: object, prefix: str = "") -> dict[str, dict]:
    result = {}
    if isinstance(value, dict):
        for name, child in value.items():
            path = f"{prefix}.{name}" if prefix else name
            if name == "sql" and isinstance(child, dict):
                result[path] = child
            elif name == "sql_collections" and isinstance(child, list):
                for index, collection in enumerate(child):
                    check(isinstance(collection, dict), f"Invalid SQL collection at {path}[{index}]")
                    result[f"{path}[{index}]"] = collection
            elif isinstance(child, (dict, list)):
                result.update(sql_records(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(sql_records(child, f"{prefix}[{index}]"))
    return result


def verify_sql_operations(operations: dict) -> None:
    collections = sql_records(operations)
    check(bool(collections), "The SQL-only process returned no SQL collections")
    for path, collection in collections.items():
        for name in ("statements", "reads", "dml", "begins", "commits", "rollbacks", "subprograms", "vm_steps", "fullscan_steps"):
            number = collection.get(name)
            check(isinstance(number, int) and not isinstance(number, bool) and number >= 0, f"Invalid {name} in {path}")
        check(collection["statements"] > 0 and collection["vm_steps"] > 0, f"The SQL observer recorded no executed work in {path}")
        executions = collection.get("executions")
        check(isinstance(executions, list) and len(executions) == collection["statements"], f"SQL execution records do not match statement counts in {path}")
        check(isinstance(collection.get("row_events"), dict), f"Missing SQL row-event counters in {path}")
        for execution in executions:
            check(isinstance(execution, dict) and isinstance(execution.get("sql"), str) and bool(execution["sql"]), f"A SQL execution omitted its text in {path}")
            for name in ("vm_steps", "fullscan_steps"):
                number = execution.get(name)
                check(isinstance(number, int) and not isinstance(number, bool) and number >= 0, f"Invalid execution {name} in {path}")
        for name in ("vm_steps", "fullscan_steps"):
            check(sum(execution[name] for execution in executions) == collection[name], f"SQL {name} totals do not match execution records in {path}")


def verify_fixture_images(data: Path, fixture: dict) -> dict:
    check(isinstance(fixture, dict), "Preparation omitted its fixture identity")
    images = [fixture["tiny"]] if isinstance(fixture.get("tiny"), dict) else []
    if isinstance(fixture.get("images"), list):
        images.extend(fixture["images"])
    check(bool(images), "The fixture declared no image identities")
    verified = {}
    for record in images:
        check(isinstance(record, dict), "A fixture image identity is not an object")
        name = record.get("file")
        check(isinstance(name, str) and Path(name).name == name, "A fixture image has an invalid filename")
        path = data / "fixtures" / name
        check(path.is_file() and path.stat().st_size == record.get("bytes"), f"Fixture image size differs: {name}")
        digest = sha256(path)
        check(digest == record.get("sha256"), f"Fixture image hash differs: {name}")
        verified[name] = {"sha256": digest, "bytes": path.stat().st_size}
    return verified


def verify_usage_artifacts(jsonl_path: Path, log_path: Path) -> dict:
    """Accept only the default five-sample library test with all twenty records."""
    log = log_path.read_text(encoding="utf-8", errors="replace")
    check(re.findall(r"(?m)^running (\d+) tests?\s*$", log) == ["1"], "The usage benchmark did not actually run exactly one library test")
    check(re.findall(r"(?m)^test (\S+)\s+\.\.\.", log) == [USAGE_LIBRARY_TEST], "The usage log did not execute only the expected exact library test")
    check(re.findall(r"(?m)^test result:\s+(\w+)\.\s+(\d+) passed;\s+(\d+) failed;", log) == [("ok", "1", "0")], "The usage library test did not report one passing test")
    lines = [line for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    check(len(lines) == 20, "The default usage benchmark must emit exactly twenty JSONL records")
    records = [json.loads(line) for line in lines]
    expected = {(scale, sample, kind) for scale in (1000, 5000) for sample in range(1, 6) for kind in ("rebuild", "append8")}
    identities = []
    for record in records:
        check(isinstance(record, dict), "A usage benchmark record is not an object")
        for field in ("schemaVersion", "scale", "sample", "samples", "duration_ns", "merge_duration_ns", "totalbytes"):
            value = record.get(field)
            check(isinstance(value, int) and not isinstance(value, bool) and value >= 0, f"Invalid usage {field}")
        check(record.get("schemaVersion") == 1 and record.get("benchmark") == "completed_download_usage", "Invalid usage benchmark record identity")
        check(record.get("samples") == 5 and record.get("checks_match") is True, "The usage record did not pass the default five-sample contract")
        identity = (record.get("scale"), record.get("sample"), record.get("kind"))
        check(identity in expected, "The usage record has an unexpected scale, sample or kind")
        identities.append(identity)
        stats = record.get("stats")
        check(isinstance(stats, dict), "A usage record omitted its production scan counters")
        for field in ("path_queries", "chapter_queries", "manifest_windows", "paths_observed", "file_checks"):
            value = stats.get(field)
            check(isinstance(value, int) and not isinstance(value, bool) and value >= 0, f"Invalid usage scan {field}")
        if record["kind"] == "append8":
            check(stats.get("path_queries") == 0 and stats.get("chapter_queries") == 8
                  and stats.get("manifest_windows") == 8 and stats.get("file_checks") == 8
                  and stats.get("paths_observed") == 16
                  and record.get("shared_snapshot_identity") is True
                  and record.get("snapshot_identity_preserved") is True,
                  "The append8 usage scan did not stay within its incremental production work")
        else:
            scale = record["scale"]
            unique_paths = scale + scale // 100
            check(stats.get("path_queries") == (unique_paths + 127) // 128
                  and stats.get("chapter_queries") == 1 and stats.get("manifest_windows") == 0
                  and stats.get("paths_observed") == unique_paths and stats.get("file_checks") == scale,
                  "The rebuild usage scan differs from its production keyset and missing-file fixture contract")
    check(len(set(identities)) == 20 and set(identities) == expected, "The usage report has duplicate or missing sample records")
    return {
        "target": "--lib",
        "exact_test": USAGE_LIBRARY_TEST,
        "tests_passed": 1,
        "jsonl_records": 20,
        "samples_per_scale_and_kind": 5,
        "jsonl": str(jsonl_path),
        "jsonl_sha256": sha256(jsonl_path),
        "test_log": str(log_path),
        "test_log_sha256": sha256(log_path),
        "scope": "Separately executed production usage library benchmark; this runner only validates the retained log and JSONL and does not execute the test.",
    }


def latency_records(value: object, prefix: str = "") -> dict[str, dict]:
    """Find nested operation latencies without assuming a render-format layout."""
    result = {}
    if not isinstance(value, dict):
        return result
    latency = value.get("latency")
    if isinstance(latency, dict):
        samples = latency.get("samples_ns")
        check(isinstance(samples, list) and bool(samples), f"Missing raw nanosecond samples for {prefix}")
        check(all(isinstance(sample, int) and not isinstance(sample, bool) and sample >= 0 for sample in samples), f"Invalid nanosecond samples for {prefix}")
        for name in ("p50_ns", "p95_ns", "max_ns"):
            number = latency.get(name)
            check(isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number) and number >= 0, f"Invalid {name} for {prefix}")
        check(latency["p50_ns"] <= latency["p95_ns"] <= latency["max_ns"], f"Unordered latency quantiles for {prefix}")
        check(latency["max_ns"] == max(samples), f"Maximum does not match the retained samples for {prefix}")
        result[prefix] = {**latency, **{name: value[name] for name in ("scope", "raw_fixture_state", "comparison_kind") if name in value}}
    for name, child in value.items():
        if name != "latency" and isinstance(child, dict):
            result.update(latency_records(child, f"{prefix}.{name}" if prefix else name))
    return result


def summarize(report: dict) -> dict:
    groups = {}
    for name, measurement in report["measurements"].items():
        if measurement["mode"] != "timing" or not measurement.get("accepted"):
            continue
        key = f"{measurement['workload']}:pages={measurement['pages']}:books={measurement['books']}"
        group = groups.setdefault(key, {"trials": [], "manifest_algorithm_orders": {}, "operations": {}, "max_process_rss_kib": 0})
        group["trials"].append(name)
        if measurement.get("manifest_algorithm_order") is not None:
            group["manifest_algorithm_orders"][name] = measurement["manifest_algorithm_order"]
        group["max_process_rss_kib"] = max(group["max_process_rss_kib"], measurement.get("peak_rss_kib") or 0)
        for operation, latency in latency_records(measurement["result"]["operations"]).items():
            trial = {field: latency[field] for field in ("p50_ns", "p95_ns", "max_ns")}
            trial["samples"] = len(latency["samples_ns"])
            trial.update({field: latency[field] for field in ("scope", "raw_fixture_state", "comparison_kind") if field in latency})
            if operation == "progress_batches":
                trial["chapter_cumulative_ns"] = sum(latency["samples_ns"])
            group["operations"].setdefault(operation, []).append(trial)
    for group in groups.values():
        group["operations"] = {
            name: {
                "trials": values,
                "median_trial_p50_ns": statistics.median(value["p50_ns"] for value in values),
                "median_trial_p95_ns": statistics.median(value["p95_ns"] for value in values),
                "worst_sample_ns": max(value["max_ns"] for value in values),
            }
            for name, values in group["operations"].items()
        }
        progress = group["operations"].get("progress_batches")
        if progress is not None:
            progress["quantile_scope"] = "Each p50/p95 sample is one two-page progress call, not a whole chapter. The final batch includes complete validation."
            progress["median_chapter_cumulative_ns"] = statistics.median(trial["chapter_cumulative_ns"] for trial in progress["trials"])
    return {
        "schema_version": report["schema_version"],
        "status": report["status"],
        "execution_scope": report["execution_scope"],
        "checks": report["checks"],
        "groups": groups,
        "syscall_audits": report["syscall_audits"],
        "interpretation": report["metric_definitions"],
    }


def syscall_counts() -> dict:
    return {"calls": {name: 0 for name in SYNC_CALLS}, "successful_calls": {name: 0 for name in SYNC_CALLS}}


def parse_syscall_audit(path: Path) -> dict:
    """Count sync calls in complete commit-marker intervals and over the lifecycle."""
    whole = syscall_counts()
    intervals = []
    active = None
    pending = {}
    stderr_buffers = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = PID_PREFIX.match(line)
        if match is None:
            continue
        pid, call = match.group(1) or match.group(2) or "main", match.group(3).strip()
        write = STDERR_WRITE.match(call)
        if write is not None:
            # strace can split one formatted stderr line across several writes.
            # Marker text is ASCII; retain C escapes until a complete newline.
            buffer = stderr_buffers.get(pid, "") + write.group(1)
            while r"\n" in buffer:
                message, buffer = buffer.split(r"\n", 1)
                marker = COMMIT_MARKER.fullmatch(message)
                if marker is None:
                    continue
                kind, index = marker.group(1), int(marker.group(2))
                if kind == "BEGIN":
                    check(active is None, "Overlapping commit markers prevent syscall attribution")
                    active = {"index": index, "marker_pid": pid, "begin_line": line_number, **syscall_counts()}
                    intervals.append(active)
                else:
                    check(active is not None and active["index"] == index and active["marker_pid"] == pid, "An end marker does not match its commit begin marker")
                    active["end_line"] = line_number
                    active = None
            stderr_buffers[pid] = buffer
            continue
        started = SYNC_START.match(call)
        if started is not None:
            name = started.group(1)
            targets = [whole] + ([active] if active is not None else [])
            for target in targets:
                target["calls"][name] += 1
            if "<unfinished ...>" in call:
                check(pid not in pending, "A traced task has multiple unfinished sync calls")
                pending[pid] = (name, targets)
            elif SYNC_SUCCESS.search(call) is not None:
                for target in targets:
                    target["successful_calls"][name] += 1
            continue
        resumed = SYNC_RESUME.match(call)
        if resumed is not None:
            check(pid in pending and pending[pid][0] == resumed.group(1), "An unfinished sync syscall lost its entry record")
            name, targets = pending.pop(pid)
            if SYNC_SUCCESS.search(call) is not None:
                for target in targets:
                    target["successful_calls"][name] += 1
    check(active is None, "The final commit marker interval is incomplete")
    check(not pending, "A sync syscall has no recorded return")
    check(bool(intervals), "No complete PHASE3_COMMIT_BEGIN/END marker interval was observed")
    check(len({interval["index"] for interval in intervals}) == len(intervals), "Commit marker indices are not unique")
    marked = syscall_counts()
    for interval in intervals:
        for group in ("calls", "successful_calls"):
            for name in SYNC_CALLS:
                marked[group][name] += interval[group][name]
    return {
        "whole_lifecycle": whole,
        "marked_commits": marked,
        "commit_intervals": intervals,
        "scope": "All traced tasks during complete stderr commit-marker intervals; whole-lifecycle totals are retained separately. Preparation is excluded. Traced timings are never performance results.",
        "markers_complete": True,
    }


def run(arguments: argparse.Namespace) -> None:
    import fcntl

    source = arguments.source.resolve()
    original_binary = arguments.binary.resolve()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "status": "preparing",
        "execution_scope": {
            "matrix_kind": "default_matrix" if set(arguments.workloads) == set(WORKLOADS) and arguments.pages == [32, 128, 512] and arguments.books == [1000, 5000] and arguments.repetitions == 3 and arguments.samples == 21 else "requested_workload_subset" if set(arguments.workloads) != set(WORKLOADS) else "custom_requested_matrix",
            "requested_workloads": arguments.workloads,
            "completion_claim": "Completion applies only to the requested example workload matrix. Source/binary stability and an operator note are not full-application acceptance or permission to start an unscheduled measurement.",
            "application_ui_validation": "Not performed by this runner.",
        },
        "started_utc": utc_now(),
        "config": {**{name: str(value) if isinstance(value, Path) else value for name, value in vars(arguments).items()}, "source": str(source), "binary": str(original_binary), "output": str(output)},
        "measurements": {},
        "preparations": {},
        "fixture_identities": {},
        "syscall_audits": {},
        "checks": {},
        "metric_definitions": {
            "timing": "Only the example's raw uninstrumented operation samples enter latency summaries; preparation, SQL-only passes, syscall audits, and process-launch/polling tails are excluded.",
            "quantiles": "Each summary p50 or p95 is the median of the corresponding per-trial quantiles. Raw per-operation nanosecond samples remain in process stdout JSON.",
            "sql": "One separate fresh-fixture SQL-only process per unique non-render configuration, after all timing repetitions. Statement executions, row events, VM steps, full-scan steps, and transaction boundaries are not physical disk write counts.",
            "render": "RenderStats cover processed PNG decoding and grayscale output buffers. They do not measure every allocator call, decoder scratch allocation, UI RGB allocation, or total application memory.",
            "render_inputs": "Raw inputs are validated and their fingerprints warmed before timing. The operating-system page cache is not evicted. Primary cold means a processed-cache miss including rendering and persistence; it does not mean cold storage or an unvalidated raw image. Exact operation scope and raw_fixture_state strings remain in raw JSON and summary trials.",
            "projection": "The Store production projection path is measured. It is not a full App sync_view, cover decoding, Slint, input, or physical Kindle latency result.",
            "usage": "The private incremental usage worker is measured separately by the exact library test app::download_usage::tests::benchmark_completed_download_usage_production with --lib. Optional retained evidence is accepted only with one passing test and twenty JSONL records from the default five samples; --bin aidoku does not contain this test.",
            "algorithm_comparison": "legacy_manifest_algorithm uses the candidate Store to model the earlier algorithm. It is not a historical executable baseline and cannot establish a Candidate5 application speedup.",
            "algorithm_order": "Both Store manifest strategies are warmed once. Odd timing repetitions use window then legacy; even repetitions use legacy then window. The actual Rust-reported order is checked and retained. The single SQL pass uses window then legacy and is not a timing sample.",
            "rss": "Qualified low-RSS native launcher wait4 of each measured Rust process, with post-exec /proc sampling. The process also includes open, warm-up, assertions, and close.",
            "fsync": "Separate publication-only strace pass. Marker-scoped successful fsync/fdatasync calls and complete-process totals are retained; traced wall time is excluded.",
            "exclusive": "The required operator note reserves the host against builds, UI, VM, and other timed work. The advisory lock prevents another copy of this runner; it cannot prove that unrelated workloads are idle.",
            "fixture": "Each operation process has a separate prepare process and data directory. Matching configurations must return identical fixture JSON; every declared image's actual bytes and SHA-256 are checked outside timing. This does not claim that path-dependent SQLite database files are byte-identical.",
            "progress": "progress_batches quantiles describe individual two-page calls. Sum of the retained batch samples is a complete chapter's cumulative measured call time, including the final validation batch.",
        },
    }

    def persist() -> None:
        write_json(output / "results.json", report)
        write_json(output / "summary.json", summarize(report))

    lock_stream = None
    try:
        check(source.is_dir() and original_binary.is_file(), "The source directory and candidate example binary must exist")
        for name in ("Cargo.toml", "Cargo.lock", "build.rs", "examples/performance-benchmark.rs", "scripts/benchmark-phase3.py", "scripts/benchmark-downloads.py"):
            check((source / name).is_file(), f"Missing required source file: {name}")
        check(sha256(Path(__file__).resolve()) == sha256(source / "scripts/benchmark-phase3.py"), "The running harness differs from the selected source snapshot")
        arguments.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_stream = arguments.lock_path.open("a+", encoding="utf-8")
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_stream.seek(0)
        lock_stream.truncate()
        lock_stream.write(json.dumps({"pid": os.getpid(), "output": str(output), "note": arguments.exclusive_note, "started_utc": report["started_utc"]}) + "\n")
        lock_stream.flush()
        report["source_manifest"] = source_manifest(source)
        report["candidate5_context"] = context_provenance(arguments.context_binary.resolve(), arguments.context_source.resolve())
        if arguments.usage_jsonl is not None:
            usage_directory = output / "usage-evidence"
            usage_directory.mkdir()
            shutil.copy2(arguments.usage_jsonl, usage_directory / "benchmark.jsonl")
            shutil.copy2(arguments.usage_test_log, usage_directory / "test.log")
            report["usage_evidence"] = verify_usage_artifacts(usage_directory / "benchmark.jsonl", usage_directory / "test.log")
            write_json(usage_directory / "acceptance.json", report["usage_evidence"])
            report["checks"]["separate_usage_library_test_one_pass_and_twenty_records"] = True
        frozen_directory = output / "frozen"
        frozen_directory.mkdir()
        binary = frozen_directory / "performance-benchmark"
        original_digest = sha256(original_binary)
        shutil.copy2(original_binary, binary)
        check(sha256(binary) == original_digest == sha256(original_binary), "The candidate binary changed while being frozen")
        report["binary"] = {"original": str(original_binary), "frozen": str(binary), "sha256": original_digest}
        for name in ("benchmark-phase3.py", "benchmark-downloads.py"):
            shutil.copy2(source / "scripts" / name, frozen_directory / name)
        report["harness"] = {name: sha256(frozen_directory / name) for name in ("benchmark-phase3.py", "benchmark-downloads.py")}
        helper = load_measurement_helper(frozen_directory / "benchmark-downloads.py")
        report["environment"] = helper.environment_metadata()
        launcher, report["measurement_launcher"] = helper.make_measurement_launcher(output)
        report["status"] = "running"
        persist()

        def command(scenario: str, data: Path, pages: int, books: int, extra: tuple[str, ...] = ()) -> list[str]:
            return [str(binary), "--scenario", scenario, "--data-dir", str(data), "--pages", str(pages), "--books", str(books), "--samples", str(arguments.samples), *extra]

        def prepare(name: str, workload: str, pages: int, books: int) -> Path:
            data = output / "data" / name
            check(not data.exists(), "Preparation requires a fresh data directory")
            prepared = run_capture(command("prepare", data, pages, books, ("--workload", workload)), output / "prepare" / name, arguments.timeout_seconds)
            report["preparations"][name] = prepared
            verify_result(prepared["result"], "prepare")
            check(data.is_dir(), "The prepare process did not create its fixture directory")
            fixture = prepared["result"].get("fixture")
            check(isinstance(fixture, dict) and fixture.get("pages") == pages and fixture.get("books") == books, "The fixture identity does not match the requested scale")
            prepared["verified_declared_images"] = verify_fixture_images(data, fixture)
            identity_key = f"{workload}:pages={pages}:books={books}"
            identity = report["fixture_identities"].setdefault(identity_key, fixture)
            check(identity == fixture, "Fixture identities differ across processes at the same workload and scale")
            # Complete fixture writeback without evicting the OS page cache.
            os.sync()
            prepared["premeasurement_durability_sync"] = "Linux os.sync after preparation and outside measured intervals; this completes writeback without page-cache eviction or drop_caches."
            persist()
            return data

        def measure(name: str, workload: str, pages: int, books: int, repetition: int, mode: str, reverse_algorithm_order: bool = False) -> None:
            data = prepare(name, workload, pages, books)
            extra = ("--sql-only",) if mode == "sql" else ()
            if workload == "store" and reverse_algorithm_order:
                extra += ("--reverse-algorithm-order",)
            expected_order = (["legacy", "window"] if reverse_algorithm_order else ["window", "legacy"]) if workload == "store" else None
            started = utc_now()
            measured = helper.run_process(command(workload, data, pages, books, extra), output / "processes" / name, arguments.timeout_seconds, launcher)
            measured.update(workload=workload, pages=pages, books=books, repetition=repetition, mode=mode, started_utc=started, finished_utc=utc_now(), preparation=name, manifest_algorithm_order=expected_order, accepted=False)
            report["measurements"][name] = measured
            verify_result(measured["result"], workload, mode == "sql")
            check(measured["result"].get("fixture") == report["preparations"][name]["result"]["fixture"], "The measured process did not use its prepared fixture identity")
            if workload == "store":
                check(measured["result"].get("manifest_algorithm_order") == expected_order, "The Store process did not confirm the requested manifest algorithm order")
            if mode == "timing":
                check(bool(latency_records(measured["result"]["operations"])), f"No operation latency samples for {name}")
                check(not sql_records(measured["result"]["operations"]), "A timing process returned SQL observer collections")
            else:
                verify_sql_operations(measured["result"]["operations"])
                check(not latency_records(measured["result"]["operations"]), "A SQL-only process returned timed latency samples")
            measured["accepted"] = True
            persist()

        qualification = helper.run_process(command("qualify", output / "data" / "qualify", arguments.pages[0], arguments.books[0]), output / "qualification", arguments.timeout_seconds, launcher)
        report["qualification"] = qualification
        verify_result(qualification["result"], "qualify")
        verify_sql_operations(qualification["result"]["operations"])
        report["checks"]["observer_qualification_passed"] = True
        persist()

        matrix = []
        if "store" in arguments.workloads:
            matrix.extend(("store", pages, arguments.books[0]) for pages in arguments.pages)
        if "render" in arguments.workloads:
            matrix.append(("render", arguments.pages[0], arguments.books[0]))
        if "projection" in arguments.workloads:
            matrix.extend(("projection", arguments.pages[0], books) for books in arguments.books)
        if "publication" in arguments.workloads:
            matrix.append(("publication", arguments.pages[0], arguments.books[0]))
        report["matrix"] = [{"workload": workload, "pages": pages, "books": books} for workload, pages, books in matrix]
        report["benchmark_process_plan"] = {"timing": len(matrix) * arguments.repetitions, "sql": sum(workload != "render" for workload, _, _ in matrix), "qualification": 1, "syscall_audit": int("publication" in arguments.workloads), "scope": "Benchmark processes only; separate preparation processes and native launcher compilation are excluded."}
        for repetition in range(1, arguments.repetitions + 1):
            ordered = matrix if repetition % 2 else list(reversed(matrix))
            for workload, pages, books in ordered:
                stem = f"{workload}-p{pages}-b{books}-r{repetition}"
                print(f"Measuring {stem}", flush=True)
                measure(f"timing-{stem}", workload, pages, books, repetition, "timing", workload == "store" and repetition % 2 == 0)
        for workload, pages, books in matrix:
            if workload == "render":
                continue
            name = f"sql-{workload}-p{pages}-b{books}"
            print(f"Collecting {name}", flush=True)
            measure(name, workload, pages, books, 0, "sql")
        report["checks"]["requested_timing_and_sql_matrix_complete"] = True

        if "publication" in arguments.workloads:
            tracer = shutil.which("strace")
            check(tracer is not None, "The requested publication syscall audit requires strace on the remote worker")
            name = "publication-syscall-audit"
            pages, books = arguments.pages[0], arguments.books[0]
            data = prepare(name, "publication", pages, books)
            audit_directory = output / "syscall-audits" / name
            trace = audit_directory / "syscalls.log"
            traced = [tracer, "-f", "-qq", "-s", "256", "-e", "trace=fsync,fdatasync,write", "-o", str(trace), *command("publication", data, pages, books, ("--syscall-markers",))]
            audit = run_capture(traced, audit_directory, arguments.timeout_seconds)
            report["syscall_audits"][name] = audit
            verify_result(audit["result"], "publication")
            check(audit["result"].get("fixture") == report["preparations"][name]["result"]["fixture"], "The syscall audit did not use its prepared fixture")
            audit["sync_calls"] = parse_syscall_audit(trace)
            commits = audit["result"]["operations"].get("dirty_lru_raw_publication", {}).get("commits_requested")
            check(isinstance(commits, int) and not isinstance(commits, bool) and commits > 0, "The publication audit omitted its commit count")
            check({interval["index"] for interval in audit["sync_calls"]["commit_intervals"]} == set(range(commits)), "Commit marker intervals do not cover every requested publication")
            check(all(sum(interval["successful_calls"].values()) > 0 for interval in audit["sync_calls"]["commit_intervals"]), "A marked commit interval contains no successful sync syscall")
            audit["sync_calls"]["commits_requested"] = commits
            audit["trace_sha256"] = sha256(trace)
            write_json(audit_directory / "audit.json", audit)
            report["checks"]["publication_syscalls_attributed_to_complete_commit_intervals"] = True

        report["source_manifest_after"] = source_manifest(source)
        check(report["source_manifest_after"] == report["source_manifest"], "Source files changed during the measurement window")
        check(sha256(binary) == original_digest, "The frozen candidate executable changed during the run")
        report["checks"]["source_and_frozen_binary_unchanged"] = True
        report["checks"]["prepared_fixture_identities_and_declared_image_bytes_match"] = True
        report["status"] = "complete"
        report["finished_utc"] = utc_now()
        persist()
        print(json.dumps({"output": str(output), "status": report["status"], "checks": report["checks"]}, indent=2), flush=True)
    except BaseException:
        report["status"] = "failed"
        report["failure"] = traceback.format_exc()
        report["finished_utc"] = utc_now()
        persist()
        raise
    finally:
        if lock_stream is not None:
            lock_stream.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclusive-note", required=True, help="Record the reserved remote measurement window and the stopped competing work.")
    parser.add_argument("--pages", nargs="+", type=int, default=[32, 128, 512])
    parser.add_argument("--books", nargs="+", type=int, default=[1000, 5000])
    parser.add_argument("--samples", type=int, default=21)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--workloads", nargs="+", choices=WORKLOADS, default=list(WORKLOADS))
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--lock-path", type=Path, default=Path("/root/aidoku-build/phase3-measurement.lock"))
    parser.add_argument("--context-binary", type=Path, default=Path("/root/aidoku-build/phase2-candidate5/desktop/aidoku"))
    parser.add_argument("--context-source", type=Path, default=Path("/tmp/aidoku-phase2-candidate5"))
    parser.add_argument("--usage-jsonl", type=Path, help="Optional default five-sample production usage library benchmark JSONL.")
    parser.add_argument("--usage-test-log", type=Path, help="Matching library test log; must show the exact test, running 1 test, and 1 passed.")
    arguments = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run this benchmark only on the designated remote Linux worker")
    if not arguments.exclusive_note.strip():
        parser.error("--exclusive-note must describe the reserved measurement window")
    if (arguments.usage_jsonl is None) != (arguments.usage_test_log is None):
        parser.error("Provide --usage-jsonl and --usage-test-log together")
    if not all(2 <= count <= 20_000 for count in arguments.pages) or not all(18 <= count <= 20_000 for count in arguments.books) or not 1 <= arguments.samples <= 512 or arguments.repetitions < 1 or not math.isfinite(arguments.timeout_seconds) or arguments.timeout_seconds <= 0:
        parser.error("Use pages in 2..20000, books in 18..20000, samples in 1..512, positive repetitions, and a finite positive timeout")
    if len(set(arguments.pages)) != len(arguments.pages) or len(set(arguments.books)) != len(arguments.books) or len(set(arguments.workloads)) != len(arguments.workloads):
        parser.error("Do not repeat a page scale, book scale, or workload")
    arguments.lock_path = arguments.lock_path.resolve()
    arguments.context_binary = arguments.context_binary.resolve()
    arguments.context_source = arguments.context_source.resolve()
    run(arguments)


if __name__ == "__main__":
    main()
