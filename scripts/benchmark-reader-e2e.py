#!/usr/bin/env python3
"""Run isolated host Reader trials or qualify already collected native traces.

Run this tool only on the designated remote verification worker. Native device
commands belong to the exclusive device operator; ``analyze`` never connects to
a device. Rust-main, UI acceptance, framebuffer writing, and SEND acceptance are
different boundaries. None is an optical e-ink completion measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import statistics
import subprocess
import sys
import time
from typing import Any


BASELINE_COMMIT = "ffb341741930fea525b96474a81afe56be25ab17"
SCHEMA_VERSION = 1
TRACE_CLOCK = "monotonic_rust_main"
MAX_TRACE_BYTES = 32 * 1024 * 1024
MAX_EVENTS = 200_000
GROUP_EXIT_GRACE_SECONDS = 0.25
GROUP_TERM_GRACE_SECONDS = 2.0
GROUP_KILL_GRACE_SECONDS = 3.0
ACTION_KINDS = {
    "next", "previous", "seek", "next_chapter", "previous_chapter", "tap",
    "zoom", "menu", "suspend", "resume", "open", "settings", "other",
}
ACTION_TERMINALS = {"action_failed", "action_cancelled"}
DEFAULT_ELIGIBLE_ACTIONS = {"next","previous","seek","next_chapter","previous_chapter","tap","zoom"}
INPUT_ORIGINS = {"scripted_intent", "scripted_pointer", "app_dispatch"}
ABORT_EVENTS = {"request_cancelled", "request_failed", "request_invalidated"}
STAGE_EVENTS = {"source_manager_finished", "fetch_finished", "render_finished", "prepare_finished"}
REQUEST_EVENTS = {
    "request_created", "request_queued", "request_waiting_for_worker", "worker_started", "worker_finished", "ui_image_ready",
    "framebuffer_written", "send_accepted", "native_frame_unavailable",
} | ABORT_EVENTS | STAGE_EVENTS
NATIVE_EVENTS = {"framebuffer_written", "send_accepted", "native_frame_unavailable"}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TraceError(ValueError):
    """The retained input cannot qualify the requested timing boundary."""


class ProcessGroupCleanupError(TraceError):
    """A trial must stop because its owned running process group remains."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TraceError(message)


def unsigned(value: Any, description: str, positive: bool = False) -> int:
    require(isinstance(value, int) and not isinstance(value, bool), f"{description} is not an integer")
    require((1 if positive else 0) <= value <= (1 << 64) - 1, f"{description} is outside its unsigned range")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def relative_path(base: Path, value: Any) -> Path:
    require(isinstance(value, str) and bool(value), "An input path is missing")
    result = Path(value)
    return result if result.is_absolute() else base / result


def trace_expectation(value: Any) -> dict[str, Any]:
    require(isinstance(value, dict), "A trace expectation is not an object")
    scope = value.get("scope")
    require(scope in {"desktop", "native"}, "A trace expectation must distinguish desktop from native scope")
    result = dict(value)
    result.setdefault("process_success", True)
    require(isinstance(result["process_success"], bool), "process_success must be Boolean")
    result.setdefault("minimum_ui_requests", 1)
    result.setdefault("minimum_native_requests", 1 if scope == "native" else 0)
    for key in ("minimum_ui_requests", "minimum_native_requests"):
        unsigned(result[key], key)
    require(scope == "native" or result["minimum_native_requests"] == 0, "A desktop trace cannot require native output")
    result.setdefault("require_startup", True)
    require(isinstance(result["require_startup"], bool), "require_startup must be Boolean")
    result.setdefault("allowed_outcomes", [])
    require(isinstance(result["allowed_outcomes"], list), "allowed_outcomes must be a list")
    require(set(result["allowed_outcomes"]) <= ABORT_EVENTS | {"native_frame_unavailable"}, "Unknown allowed terminal outcome")
    for key in ("required_outcomes", "required_successful_actions"):
        result.setdefault(key, {})
        require(isinstance(result[key], dict), f"{key} must be an object")
        for name, count in result[key].items():
            require(name in (ACTION_KINDS if key == "required_successful_actions" else ABORT_EVENTS | {"native_frame_unavailable"}), f"Unknown {key} name")
            unsigned(count, f"{key} count")
    result.setdefault("required_cache_states", {})
    require(isinstance(result["required_cache_states"], dict), "required_cache_states must be an object")
    for name,count in result["required_cache_states"].items():
        require(name in {"memory","disk","cold"}, "Unknown required cache state")
        unsigned(count,"required_cache_states count")
    result.setdefault("eligible_action_kinds",sorted(DEFAULT_ELIGIBLE_ACTIONS))
    require(isinstance(result["eligible_action_kinds"],list) and set(result["eligible_action_kinds"]) <= ACTION_KINDS, "Unknown eligible action kind")
    result.setdefault("allowed_action_outcomes",[])
    require(isinstance(result["allowed_action_outcomes"],list) and set(result["allowed_action_outcomes"]) <= ACTION_TERMINALS, "Unknown allowed action outcome")
    result.setdefault("required_action_outcomes",{})
    require(isinstance(result["required_action_outcomes"],dict), "required_action_outcomes must be an object")
    for name,count in result["required_action_outcomes"].items():
        require(name in ACTION_TERMINALS, "Unknown required action outcome")
        unsigned(count,"required_action_outcomes count")
    return result


def validate_trace(document: Any, expectation: dict[str, Any]) -> dict[str, Any]:
    """Validate complete causality before calculating any successful latency."""
    expectation = trace_expectation(expectation)
    require(isinstance(document, dict), "The trace is not a JSON object")
    require(document.get("schema_version") == SCHEMA_VERSION, "Unsupported trace schema")
    require(document.get("clock") == TRACE_CLOCK, "Unexpected trace clock boundary")
    process_id = unsigned(document.get("process_id"), "process_id", positive=True)
    require(document.get("finished") is True, "The trace was not completely flushed")
    require(document.get("success") is expectation["process_success"], "Unexpected process success outcome")
    require(unsigned(document.get("dropped_events"), "dropped_events") == 0, "The trace dropped events")
    limit = unsigned(document.get("event_limit"), "event_limit", positive=True)
    require(limit <= MAX_EVENTS, "The declared trace event limit is excessive")
    events = document.get("events")
    require(isinstance(events, list) and 2 <= len(events) <= limit, "Missing events or exceeded trace limit")
    require(isinstance(events[0], dict) and events[0].get("name") == "process_entry", "The trace does not begin at Rust main")
    require(events[0].get("at_us") == 0, "The Rust-main epoch is not zero")
    require(isinstance(events[-1], dict) and events[-1].get("name") == "process_finished", "The trace has no final process event")
    actions: dict[int, dict[str, Any]] = {}
    action_terminals: dict[int,dict[str,Any]] = {}
    requests: dict[int, dict[str, Any]] = {}
    previous_us = 0
    process_events = {"process_entry": 0, "process_finished": 0}
    used_frames: dict[int, int] = {}
    latest_request_id = None
    for ordinal, event in enumerate(events):
        require(isinstance(event, dict), f"Event {ordinal} is not an object")
        name = event.get("name")
        require(isinstance(name, str) and bool(IDENTIFIER.fullmatch(name)), f"Event {ordinal} has an invalid fixed name")
        at_us = unsigned(event.get("at_us"), f"Event {ordinal} at_us")
        if "duration_us" in event:
            unsigned(event["duration_us"], f"Event {ordinal} duration_us")
        require(at_us >= previous_us, f"Event {ordinal} moves the monotonic clock backward")
        previous_us = at_us
        if name in process_events:
            process_events[name] += 1
            require("request_id" not in event, "A process event is incorrectly request-bound")
            continue
        if name == "action_received":
            action_id = unsigned(event.get("action_id"), "action_id", positive=True)
            require(action_id not in actions, "An action identity was reused")
            require(event.get("action_kind") in ACTION_KINDS, "An action kind is not a fixed supported value")
            require(event.get("boundary") == "app_intent_received", "An action boundary is ambiguous")
            require(event.get("input_origin") in INPUT_ORIGINS, "An action input origin is ambiguous")
            require("request_id" not in event, "An action must be observed before request creation")
            actions[action_id] = event
            continue
        if name in ACTION_TERMINALS:
            action_id = unsigned(event.get("action_id"), "terminal action_id", positive=True)
            require(action_id in actions, "An action terminal references a missing action")
            require(action_id not in action_terminals, "An action has multiple terminal outcomes")
            require("request_id" not in event, "An action terminal is incorrectly request-bound")
            require(isinstance(event.get("reason"),str) and bool(IDENTIFIER.fullmatch(event["reason"])), "An action terminal reason is not a fixed identifier")
            action_terminals[action_id] = event
            continue
        if "request_id" not in event:
            require(name not in REQUEST_EVENTS, f"{name} omitted its request identity")
            continue
        require(name in REQUEST_EVENTS, "An unknown event is bound to a Reader request")
        request_id = unsigned(event.get("request_id"), "request_id", positive=True)
        generations = (
            unsigned(event.get("reader_generation"), "reader_generation"),
            unsigned(event.get("lifecycle_generation"), "lifecycle_generation"),
        )
        action_id = event.get("action_id")
        if action_id is not None:
            unsigned(action_id, "request action_id", positive=True)
            require(action_id in actions, "A request references a missing or later action")
        if name == "request_created":
            require(request_id not in requests, "A request identity was reused")
            requests[request_id] = {
                "request_id": request_id, "action_id": action_id, "generations": generations,
                "created": event, "events": {}, "stages": {}, "cache_states": [], "missing_render_results":0, "retired": None,
            }
            latest_request_id = request_id
            continue
        require(request_id in requests, f"{name} references an unknown or not-yet-created request")
        request = requests[request_id]
        require(generations == request["generations"], "A completion carries stale Reader/lifecycle generations")
        require(action_id == request["action_id"], "A request changed its action identity")
        recorded = request["events"]
        if name in ABORT_EVENTS:
            require(request["retired"] is None, "A request has multiple retirement outcomes")
            require(isinstance(event.get("reason"), str) and bool(IDENTIFIER.fullmatch(event["reason"])), "A retirement reason is not a fixed identifier")
            require(name != "request_failed" or "ui_image_ready" not in recorded, "A UI-accepted request subsequently claims failure")
            request["retired"] = event
            continue
        if name in STAGE_EVENTS:
            require("worker_started" in recorded, "A work stage completed before its worker started")
            require("ui_image_ready" not in recorded, "A work stage completed after UI acceptance")
            duration = unsigned(event.get("duration_us"), f"{name} duration_us")
            require(duration <= at_us - recorded["worker_started"]["at_us"], "A work-stage duration predates its worker")
            request["stages"].setdefault(name, []).append(duration)
            if name == "render_finished":
                require(event.get("cache_state") in {None,"memory","disk","cold","inconsistent"}, "A render stage has an unknown cache result")
                require(event.get("cache_state") != "inconsistent", "A render reported contradictory cache results")
                if event.get("cache_state") is None:
                    request["missing_render_results"] += 1
                else:
                    request["cache_states"].append(event["cache_state"])
            continue
        require(name not in recorded, f"A request repeated its {name} boundary")
        if name in {"worker_started","worker_finished"}:
            if name == "worker_started":
                require("ui_image_ready" not in recorded, "A worker started after UI acceptance")
            else:
                require("worker_started" in recorded, "A worker finished before it started")
            recorded[name] = event
            continue
        require(request["retired"] is None, "A stale request produced output after retirement")
        if name == "ui_image_ready" or name in NATIVE_EVENTS:
            require(request_id == latest_request_id, "An older request completed after a newer Reader request was created")
        if name in {"request_queued","request_waiting_for_worker"}:
            require("worker_started" not in recorded, "A request was queued after its worker began")
        elif name == "ui_image_ready":
            require("worker_started" in recorded, "UI acceptance has no worker provenance")
        elif name in NATIVE_EVENTS:
            require(expectation["scope"] == "native", "A desktop trace unexpectedly claims native output")
            require("ui_image_ready" in recorded, "Native output precedes accepted Reader pixels")
            require("native_frame_unavailable" not in recorded, "A native completion follows its terminal unavailable outcome")
            frame = unsigned(event.get("frame_sequence",0), "frame_sequence", positive=name != "native_frame_unavailable")
            if name == "framebuffer_written":
                require(frame not in used_frames, "A native frame was attributed to multiple requests")
                require(isinstance(event.get("full"), bool), "Native write does not identify full/partial output")
                used_frames[frame] = request_id
            elif name == "send_accepted":
                require("framebuffer_written" in recorded, "SEND acceptance has no matching framebuffer write")
                require(frame == recorded["framebuffer_written"]["frame_sequence"], "SEND acceptance belongs to another frame")
                marker = unsigned(event.get("marker"), "marker", positive=True)
                require(marker <= (1 << 32) - 1, "SEND marker is outside its ABI range")
            else:
                require(event.get("reason") in {"no_write", "send_rejected", "superseded", "cancelled", "lifecycle", "not_visible"}, "Unknown native unavailable reason")
                require("send_accepted" not in recorded, "A successful native frame was subsequently rejected")
        recorded[name] = event
    require(process_events == {"process_entry": 1, "process_finished": 1}, "The trace has repeated process boundaries")
    require(bool(requests) or not expectation["require_startup"], "The trace has no Reader request")

    outcomes: dict[str, int] = {}
    successful_actions: dict[str, set[int]] = {}
    cache_state_counts: dict[str,int] = {}
    request_rows = []
    samples: dict[str, list[float]] = {}

    def add(name: str, microseconds: int) -> None:
        samples.setdefault(name, []).append(microseconds / 1000.0)

    for request in requests.values():
        recorded = request["events"]
        ui = recorded.get("ui_image_ready")
        written = recorded.get("framebuffer_written")
        sent = recorded.get("send_accepted")
        required_completion = sent if expectation["scope"] == "native" else ui
        retirement = request["retired"]
        unavailable = recorded.get("native_frame_unavailable")
        complete = required_completion is not None
        if complete:
            require(request["missing_render_results"] == 0, "A successful request omitted a render-cache outcome")
            outcome = "complete"
        elif unavailable is not None:
            outcome = "native_frame_unavailable"
        elif retirement is not None:
            outcome = retirement["name"]
        else:
            raise TraceError("A Reader request has no required completion or explicit terminal outcome")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        require(complete or outcome in expectation["allowed_outcomes"], f"Unexpected incomplete Reader outcome: {outcome}")
        row = {
            "request_id": request["request_id"], "action_id": request["action_id"],
            "reader_generation": request["generations"][0], "lifecycle_generation": request["generations"][1],
            "outcome": outcome, "ui_accepted": ui is not None, "native_sent": sent is not None,
            "render_cache_states":request["cache_states"],
            "retirement": None if retirement is None else retirement["name"],
        }
        # Failed/cancelled native requests retain their UI boundary in the raw
        # rows, but do not enter the successful action/native latency cohort.
        if complete:
            for state in request["cache_states"]:
                cache_state_counts[state] = cache_state_counts.get(state,0) + 1
            origin = request["created"]["at_us"]
            for boundary, event in (("ui", ui), ("framebuffer_write", written), ("send_accepted", sent)):
                if event is not None:
                    add(f"request_to_{boundary}_ms", event["at_us"] - origin)
            worker = recorded.get("worker_started")
            if worker is not None:
                add("request_to_worker_started_ms", worker["at_us"] - origin)
            for stage, durations in request["stages"].items():
                add(f"{stage.removesuffix('_finished')}_stage_total_ms", sum(durations))
            action = actions.get(request["action_id"])
            if action is not None:
                successful_actions.setdefault(action["action_kind"], set()).add(action["action_id"])
                for boundary, event in (("ui", ui), ("framebuffer_write", written), ("send_accepted", sent)):
                    if event is not None:
                        add(f"action_to_{boundary}_ms", event["at_us"] - action["at_us"])
                        add(f"action_{action['action_kind']}_to_{boundary}_ms", event["at_us"] - action["at_us"])
        request_rows.append(row)
    ui_events = [request["events"]["ui_image_ready"] for request in requests.values() if "ui_image_ready" in request["events"]]
    sent_events = [request["events"]["send_accepted"] for request in requests.values() if "send_accepted" in request["events"]]
    require(len(ui_events) >= expectation["minimum_ui_requests"], "Too few accepted UI requests")
    require(len(sent_events) >= expectation["minimum_native_requests"], "Too few completed native requests")
    if expectation["require_startup"]:
        require(bool(ui_events), "Startup has no accepted Reader image")
        first_ui = min(ui_events, key=lambda event:event["at_us"])
        first_request = requests[first_ui["request_id"]]
        require(first_request["action_id"] is None, "The first image was action-triggered, not a startup Reader frame")
        add("rust_main_to_first_ui_ms", first_ui["at_us"])
        if expectation["scope"] == "native":
            require("send_accepted" in first_request["events"], "The startup UI image never reached native SEND acceptance")
            add("rust_main_to_first_framebuffer_write_ms", first_request["events"]["framebuffer_written"]["at_us"])
            add("rust_main_to_first_send_accepted_ms", first_request["events"]["send_accepted"]["at_us"])
    for outcome, count in expectation["required_outcomes"].items():
        require(outcomes.get(outcome, 0) >= count, f"The expected {outcome} path was not exercised")
    successful_counts = {kind:len(identities) for kind, identities in successful_actions.items()}
    for kind, count in expectation["required_successful_actions"].items():
        require(successful_counts.get(kind, 0) >= count, f"Too few successfully completed {kind} actions")
    for state,count in expectation["required_cache_states"].items():
        require(cache_state_counts.get(state,0) >= count, f"The expected {state} render-cache path was not exercised")
    successful_action_ids = {action_id for identities in successful_actions.values() for action_id in identities}
    eligible_kinds = set(expectation["eligible_action_kinds"]) | expectation["required_successful_actions"].keys()
    action_rows = []
    action_outcomes: dict[str,int] = {}
    for action_id,action in actions.items():
        associated = [row for row in request_rows if row["action_id"] == action_id]
        terminal = action_terminals.get(action_id)
        eligible = action["action_kind"] in eligible_kinds
        if action_id in successful_action_ids:
            require(terminal is None or terminal["name"] != "action_failed", "A successfully completed action subsequently claims failure")
            outcome = "complete"
        elif terminal is not None:
            outcome = terminal["name"]
        elif associated:
            outcome = "request_aborted"
        else:
            outcome = "incomplete" if eligible else "not_measured"
        if eligible:
            action_outcomes[outcome] = action_outcomes.get(outcome,0) + 1
            require(outcome in {"complete","request_aborted"} or outcome in expectation["allowed_action_outcomes"], "An eligible action has no successful completion or explicitly allowed terminal outcome")
        action_rows.append({"action_id":action_id,"action_kind":action["action_kind"],"input_origin":action["input_origin"],"eligible":eligible,"outcome":outcome,"request_ids":[row["request_id"] for row in associated],"terminal_reason":None if terminal is None else terminal["reason"]})
    for outcome,count in expectation["required_action_outcomes"].items():
        require(action_outcomes.get(outcome,0) >= count, f"The expected {outcome} action path was not exercised")
    return {
        "valid": True, "scope": expectation["scope"], "clock": TRACE_CLOCK,"process_id":process_id,
        "request_count": len(requests), "ui_request_count": len(ui_events),
        "native_request_count": len(sent_events), "outcomes": outcomes,
        "successful_actions": successful_counts, "cache_state_counts":cache_state_counts,
        "actions":action_rows,"eligible_action_count":sum(row["eligible"] for row in action_rows),"action_outcomes":action_outcomes,
        "requests": request_rows, "samples": samples,
        "process_duration_us": events[-1]["at_us"],
        "limits": [
            "The epoch is the first Rust main entry, not OS exec or launcher entry.",
            "Actions begin at app_intent_received; scripted scheduling delay is excluded.",
            "UI image acceptance is not a native framebuffer write.",
            "Framebuffer writing and successful SEND do not establish optical panel completion.",
            "Stage durations may repeat for spreads and are not an additive partition of total latency.",
        ],
    }


def analyze_trace(path: Path, expectation: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"trace":str(path), "valid":False}
    try:
        require(path.is_file() and path.stat().st_size <= MAX_TRACE_BYTES, "A trace is missing or exceeds the file limit")
        result["trace_sha256"] = sha256(path)
        result.update(validate_trace(read_json(path), expectation))
    except (OSError, ValueError, TypeError, KeyError) as error:
        result["error"] = {"type":type(error).__name__, "message":str(error)}
    return result


def distribution(values: list[float], processes: int) -> dict[str, Any]:
    ordered = sorted(values)
    require(bool(ordered), "Cannot summarize zero observations")
    return {
        "observations": len(ordered), "independent_processes": processes,
        "p50_ms": statistics.median(ordered), "p95_ms": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "minimum_ms": ordered[0], "maximum_ms": ordered[-1],
        "percentile_method":"median and nearest-rank p95",
        "small_sample":len(ordered) < 21,
        "sampling_note":"Events within a process share state; process count is reported separately.",
    }


def summarize_runs(runs: list[dict[str, Any]], expected_pairs: int | dict[str,int] | None = None) -> dict[str, Any]:
    groups: dict[tuple[str,str], list[dict[str, Any]]] = {}
    for run in runs:
        if run.get("phase", "measure") == "measure":
            groups.setdefault((run["workload"], run["implementation"]), []).append(run)
    summaries: dict[str, Any] = {}
    all_valid = bool(groups) and all(run.get("analysis", {}).get("valid") is True for trials in groups.values() for run in trials)
    for (workload, implementation), trials in groups.items():
        valid = [trial for trial in trials if trial.get("analysis", {}).get("valid") is True]
        collected: dict[str,list[float]] = {}
        process_counts: dict[str,int] = {}
        for trial in valid:
            for metric, values in trial["analysis"]["samples"].items():
                collected.setdefault(metric, []).extend(values)
                process_counts[metric] = process_counts.get(metric, 0) + 1
        summaries.setdefault(workload, {})[implementation] = {
            "requested_processes":len(trials), "valid_processes":len(valid),
            "failed_processes":len(trials) - len(valid),
            "statistics_qualified":len(valid) == len(trials),
            "metrics":{metric:distribution(values, process_counts[metric]) for metric,values in collected.items()},
        }
    comparisons: dict[str, Any] = {}
    measured_workloads = set(summaries)
    declared_workloads = set(expected_pairs) if isinstance(expected_pairs,dict) else measured_workloads
    missing_workloads = sorted(declared_workloads - measured_workloads)
    unexpected_workloads = sorted(measured_workloads - declared_workloads)
    workload_set_complete = not missing_workloads and not unexpected_workloads
    paired = bool(summaries) and workload_set_complete
    for workload, labels in summaries.items():
        baseline = labels.get("baseline")
        candidate = labels.get("candidate")
        complete_pair = baseline is not None and candidate is not None
        if complete_pair:
            complete_pair = baseline["requested_processes"] == candidate["requested_processes"]
            before_trials,after_trials = groups[(workload,"baseline")],groups[(workload,"candidate")]
            before_iterations = {trial["iteration"] for trial in before_trials}
            after_iterations = {trial["iteration"] for trial in after_trials}
            complete_pair = complete_pair and len(before_iterations) == len(before_trials) and before_iterations == after_iterations
            scopes = {trial.get("analysis",{}).get("scope") for trial in before_trials+after_trials}
            expectations = {json.dumps(trial.get("expectation"),sort_keys=True) for trial in before_trials+after_trials}
            complete_pair = complete_pair and len(scopes) == 1 and len(expectations) == 1
            complete_pair = complete_pair and bool(baseline["metrics"]) and baseline["metrics"].keys() == candidate["metrics"].keys()
            if complete_pair:
                complete_pair = all(baseline["metrics"][metric]["observations"] == candidate["metrics"][metric]["observations"] for metric in baseline["metrics"])
            if expected_pairs is not None:
                requested_count = expected_pairs.get(workload) if isinstance(expected_pairs,dict) else expected_pairs
                complete_pair = complete_pair and isinstance(requested_count,int) and not isinstance(requested_count,bool) and requested_count > 0
                if complete_pair:
                    complete_pair = before_iterations == set(range(1,requested_count+1))
        qualified = all_valid and complete_pair and workload_set_complete
        paired = paired and qualified
        ratios = {}
        if qualified:
            for metric in baseline["metrics"].keys() & candidate["metrics"].keys():
                before, after = baseline["metrics"][metric], candidate["metrics"][metric]
                if before["p50_ms"] > 0:
                    ratios[metric] = {"candidate_over_baseline_p50":after["p50_ms"] / before["p50_ms"], "baseline_observations":before["observations"], "candidate_observations":after["observations"]}
        comparisons[workload] = {"qualified":bool(qualified), "ratios":ratios}
    for workload in missing_workloads:
        comparisons[workload] = {"qualified":False,"ratios":{},"missing_measured_trials":True}
    return {"all_trials_valid":all_valid, "paired_samples_complete":bool(paired), "workload_set_complete":workload_set_complete,"missing_workloads":missing_workloads,"unexpected_workloads":unexpected_workloads,"workloads":summaries, "comparisons":comparisons}


def withhold_comparison_ratios(summary: dict[str,Any]) -> None:
    for comparison in summary["comparisons"].values():
        comparison["qualified"] = False
        comparison["ratios"] = {}


def analyze_manifest(path: Path) -> dict[str, Any]:
    manifest = read_json(path)
    require(isinstance(manifest, dict) and manifest.get("schema_version") == 1, "Unsupported run manifest")
    entries = manifest.get("runs")
    require(isinstance(entries, list) and bool(entries), "The run manifest has no trials")
    base = path.resolve().parent
    runs = []
    identities: set[tuple[str,str,int,str]] = set()
    trace_paths: set[Path] = set()
    trace_hashes: set[str] = set()
    process_identities: set[int] = set()
    for entry in entries:
        require(isinstance(entry, dict), "A trial record is not an object")
        label, workload = entry.get("implementation"), entry.get("workload")
        require(label in {"baseline", "candidate"}, "Unknown implementation label")
        require(isinstance(workload, str) and bool(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", workload)), "Invalid workload name")
        iteration = unsigned(entry.get("iteration"), "iteration", positive=True)
        phase = entry.get("phase", "measure")
        require(phase in {"qualification", "measure"}, "Unknown trial phase")
        identity = (label, workload, iteration, phase)
        require(identity not in identities, "A trial identity is duplicated")
        identities.add(identity)
        trial = dict(entry)
        trace = relative_path(base, entry.get("trace"))
        trial["analysis"] = analyze_trace(trace, entry.get("expectation", manifest.get("expectation", {})))
        duplicate = trace.resolve() in trace_paths
        trace_paths.add(trace.resolve())
        digest = trial["analysis"].get("trace_sha256")
        process_id = trial["analysis"].get("process_id")
        duplicate = duplicate or digest is not None and digest in trace_hashes or process_id is not None and process_id in process_identities
        if digest is not None:
            trace_hashes.add(digest)
        if process_id is not None:
            process_identities.add(process_id)
        if duplicate:
            trial["analysis"] = {"valid":False,"error":{"type":"TraceError","message":"A trace file, content or process identity was reused as another independent process"}}
        if entry.get("trace_sha256") is not None and trial["analysis"].get("trace_sha256") != entry["trace_sha256"]:
            trial["analysis"] = {"valid":False, "error":{"type":"TraceError", "message":"The retained trace hash differs from its manifest"}}
        process_receipt = entry.get("process")
        supervisor_failed = entry.get("supervisor_success") is False or isinstance(process_receipt,dict) and process_receipt.get("succeeded") is not True
        if entry.get("process_exit_code") != 0 or entry.get("timed_out") is True or supervisor_failed:
            trial["analysis"] = {"valid":False, "error":{"type":"ProcessFailure", "message":"The measured process did not exit successfully"}, "trace_analysis":trial["analysis"]}
        runs.append(trial)
    summary = summarize_runs(runs, manifest.get("expected_pairs"))
    qualification_valid = all(run["analysis"].get("valid") is True for run in runs if run.get("phase") == "qualification")
    try:
        control = controlled_inputs(manifest, base)
    except (OSError,ValueError,TypeError,KeyError) as error:
        control = {"verified":False,"error":{"type":type(error).__name__,"message":str(error)}}
    binary_receipts = manifest.get("binaries", {})
    binary_identities_valid = isinstance(binary_receipts,dict) and set(binary_receipts) == {"baseline","candidate"}
    if binary_identities_valid:
        for item in binary_receipts.values():
            try:
                require(isinstance(item,dict), "A binary receipt is not an object")
                require(isinstance(item.get("sha256"),str) and bool(HEX_SHA256.fullmatch(item["sha256"])), "A binary receipt has no SHA-256 identity")
                require(item.get("after_sha256") == item["sha256"], "A binary changed during collection")
                binary_path = relative_path(base,item.get("path"))
                require(sha256(binary_path) == item["sha256"], "The retained executable does not match the binary receipt")
            except (OSError,ValueError,TypeError,KeyError):
                binary_identities_valid = False
                break
    unchanged_declaration = manifest.get("binaries_unchanged")
    if unchanged_declaration is not None:
        binary_identities_valid = binary_identities_valid and isinstance(unchanged_declaration,dict) and set(unchanged_declaration) == {"baseline","candidate"} and all(value is True for value in unchanged_declaration.values())
    fixture_receipts: dict[str,set[str]] = {}
    fixtures_valid = True
    for run in runs:
        if run.get("phase", "measure") != "measure":
            continue
        digest = run.get("fixture_sha256")
        if not isinstance(digest,str) or not HEX_SHA256.fullmatch(digest):
            fixtures_valid = False
        else:
            fixture_receipts.setdefault(run["workload"],set()).add(digest)
    fixtures_valid = fixtures_valid and all(len(digests) == 1 for digests in fixture_receipts.values())
    control_valid = control["verified"] and binary_identities_valid and fixtures_valid
    if not control_valid or not qualification_valid:
        withhold_comparison_ratios(summary)
    return {
        "schema_version":1, "kind":"reader_e2e_analysis", "manifest":str(path.resolve()),
        "manifest_sha256":sha256(path), "runs":runs, "summary":summary,
        "qualification_trials_valid":qualification_valid,
        "control":control, "binary_identities_valid":bool(binary_identities_valid),
        "fixture_identities_valid":fixtures_valid,
        "comparison_qualified":summary["paired_samples_complete"] and qualification_valid and control_valid,
        "control_note":"External device operators must retain the source/instrumentation, binary and identical-fixture receipts behind a verified control record. Trace causality alone does not establish a controlled algorithm comparison.",
    }


def inventory(fixtures: list[dict[str, Any]], base: Path) -> dict[str, Any]:
    result = {}
    for fixture in fixtures:
        require(isinstance(fixture, dict), "A fixture declaration is not an object")
        name = fixture.get("name")
        require(isinstance(name, str) and bool(IDENTIFIER.fullmatch(name)) and name not in result, "A fixture name is invalid or duplicated")
        declared_path = relative_path(base, fixture.get("path"))
        require(not declared_path.is_symlink(), "A prepared fixture is symbolic")
        path = declared_path.resolve()
        require(path.exists(), "A prepared fixture is missing")
        children = [path] if path.is_file() else sorted(path.rglob("*"))
        require(all(not child.is_symlink() for child in children), "A prepared fixture contains a symbolic entry")
        paths = [child for child in children if child.is_file()]
        require(bool(paths), "A prepared fixture is empty")
        files = {}
        for child in paths:
            require(not child.is_symlink(), "A prepared fixture contains a symbolic file")
            key = child.name if path.is_file() else child.relative_to(path).as_posix()
            files[key] = {"bytes":child.stat().st_size, "sha256":sha256(child)}
        encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        result[name] = {"sha256":hashlib.sha256(encoded).hexdigest(), "files":files}
    require(bool(result), "Every measured workload needs a nonempty preseeded fixture inventory")
    return result


def owned_process_group_members(group_id: int) -> list[dict[str,Any]]:
    """Observe only the session/group created by start_new_session=True."""
    require(sys.platform.startswith("linux") and Path("/proc").is_dir(), "Owned process-group verification requires the Linux worker")
    require(group_id > 1 and group_id != os.getpgrp(), "Refusing an unowned process group")
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            tail = (entry/"stat").read_text(encoding="utf-8").rsplit(")",1)[1].split()
        except (FileNotFoundError,ProcessLookupError):
            continue
        require(len(tail) >= 20, "A process-group observation is incomplete")
        if int(tail[2]) == group_id and int(tail[3]) == group_id:
            members.append({"pid":int(entry.name),"start_time_ticks":int(tail[19]),"state":tail[0]})
    return sorted(members,key=lambda member:member["pid"])


def running_group_members(members: list[dict[str,Any]]) -> list[dict[str,Any]]:
    return [member for member in members if member["state"] not in {"Z","X","x"}]


def wait_owned_group_exit(process: subprocess.Popen[Any], seconds: float) -> tuple[bool,list[dict[str,Any]]]:
    deadline = time.monotonic() + seconds
    empty_observations = 0
    while True:
        process.poll()
        members = owned_process_group_members(process.pid)
        empty_observations = empty_observations + 1 if not running_group_members(members) else 0
        if empty_observations >= 2:
            return True,members
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False,members
        time.sleep(min(0.025,remaining))


def finish_owned_process_group(process: subprocess.Popen[Any], force: bool) -> dict[str,Any]:
    """Escalate by the owned group even when its original leader has exited."""
    result: dict[str,Any] = {"process_group":process.pid,"session":process.pid,"term_sent":False,"kill_sent":False,"verified_no_running_members":False,"cleanup_required_after_normal_exit":False}
    started = time.monotonic()
    try:
        if not force:
            empty,members = wait_owned_group_exit(process,GROUP_EXIT_GRACE_SECONDS)
            if empty:
                result.update(verified_no_running_members=True,remaining_members=members)
                return result
            result["cleanup_required_after_normal_exit"] = True
        for requested_signal,grace,field in (
            (signal.SIGTERM,GROUP_TERM_GRACE_SECONDS,"term_sent"),
            (signal.SIGKILL,GROUP_KILL_GRACE_SECONDS,"kill_sent"),
        ):
            members = owned_process_group_members(process.pid)
            if running_group_members(members):
                try:
                    os.killpg(process.pid,requested_signal)
                    result[field] = True
                except ProcessLookupError:
                    pass
            empty,members = wait_owned_group_exit(process,grace)
            if empty:
                result.update(verified_no_running_members=True,remaining_members=members)
                return result
        result["remaining_members"] = members
        return result
    except (OSError,ValueError,TypeError,IndexError) as error:
        result["error"] = {"type":type(error).__name__,"message":str(error)}
        return result
    finally:
        result["duration_seconds_diagnostic_only"] = time.monotonic() - started


def run_process(command: list[str], directory: Path, timeout: float, cwd: Path, environment: dict[str,str]) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=False)
    require(bool(command) and all(isinstance(part, str) and bool(part) for part in command), "A process command must be a nonempty argv array")
    record: dict[str,Any] = {"command":command, "cwd":str(cwd), "succeeded":False,"wall_time_scope":"Diagnostic process/supervisor time only; excluded from latency statistics."}
    process = None
    started = time.monotonic()
    try:
        with (directory/"stdout.log").open("wb") as stdout, (directory/"stderr.log").open("wb") as stderr:
            process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=stdout, stderr=stderr, start_new_session=True)
            record["supervisor_process_id"] = process.pid
            try:
                process.wait(timeout=timeout)
            except BaseException as error:
                record["timed_out"] = isinstance(error, subprocess.TimeoutExpired)
                record["interrupted"] = not record["timed_out"]
                record["process_group_cleanup"] = finish_owned_process_group(process,force=True)
                record["exit_code"] = process.poll()
                if not record["process_group_cleanup"]["verified_no_running_members"]:
                    raise ProcessGroupCleanupError("The owned process group could not be verified stopped; abort subsequent trials") from error
                if not record["timed_out"]:
                    raise
            if "process_group_cleanup" not in record:
                record["process_group_cleanup"] = finish_owned_process_group(process,force=False)
                if not record["process_group_cleanup"]["verified_no_running_members"]:
                    raise ProcessGroupCleanupError("The owned process group could not be verified stopped; abort subsequent trials")
        record["exit_code"] = process.returncode
        record["succeeded"] = process.returncode == 0 and not record.get("timed_out") and not record.get("interrupted") and not record["process_group_cleanup"]["cleanup_required_after_normal_exit"]
    except OSError as error:
        record["error"] = {"type":type(error).__name__, "message":str(error)}
    finally:
        if process is not None:
            record.setdefault("exit_code",process.poll())
        record["wall_seconds_diagnostic_only"] = time.monotonic() - started
        save_json(directory/"process.json", record)
    return record


def controlled_inputs(plan: dict[str, Any], base: Path) -> dict[str, Any]:
    contract = plan.get("control")
    require(isinstance(contract, dict) and contract.get("baseline_commit") == BASELINE_COMMIT, "The comparison must identify the ffb3417 baseline")
    patch = relative_path(base, contract.get("baseline_observation_patch"))
    require(patch.is_file() and patch.stat().st_size > 0, "The baseline observation-only patch receipt is missing")
    patch_sha = sha256(patch)
    review_path = relative_path(base,contract.get("baseline_review_receipt"))
    review = read_json(review_path)
    require(isinstance(review,dict) and review.get("schema_version") == 1, "The baseline observation review receipt is invalid")
    require(review.get("baseline_commit") == BASELINE_COMMIT and review.get("baseline_observation_patch_sha256") == patch_sha, "The baseline review does not bind this commit and observation patch")
    require(review.get("review_passed") is True, "The baseline observation review did not pass")
    checks = review.get("checks")
    require(isinstance(checks,dict) and all(checks.get(name) is True for name in ("original_algorithms_preserved","same_timing_boundaries","candidate_optimizations_excluded")), "The baseline observation review omits an essential scope check")
    shared = contract.get("shared_observation_files")
    require(isinstance(shared, list) and bool(shared), "Shared observation source identities are missing")
    files = []
    for item in shared:
        require(isinstance(item, dict), "A shared observation source record is invalid")
        before = relative_path(base, item.get("baseline"))
        after = relative_path(base, item.get("candidate"))
        before_sha, after_sha = sha256(before), sha256(after)
        require(before_sha == after_sha, "The baseline and candidate use different shared observation code")
        files.append({"name":item.get("name",before.name), "sha256":before_sha})
    return {"verified":True, "baseline_commit":BASELINE_COMMIT, "baseline_observation_patch_sha256":patch_sha, "baseline_review_receipt_sha256":sha256(review_path), "shared_observation_files":files, "review_scope":"Byte identity of shared observation modules plus the integration review receipt bound to this baseline commit and observation patch."}


def retained_control_contract(contract: dict[str,Any], base: Path) -> dict[str,Any]:
    """Keep re-verifiable input paths separate from their verification receipt."""
    result = dict(contract)
    for key in ("baseline_observation_patch","baseline_review_receipt"):
        result[key] = str(relative_path(base,contract[key]).resolve())
    result["shared_observation_files"] = [dict(item,baseline=str(relative_path(base,item["baseline"]).resolve()),candidate=str(relative_path(base,item["candidate"]).resolve())) for item in contract["shared_observation_files"]]
    return result


def substitute(parts: list[str], replacements: dict[str,str]) -> list[str]:
    require(isinstance(parts, list) and all(isinstance(part, str) for part in parts), "A command template must be an argv array")
    result = []
    for part in parts:
        for key, value in replacements.items():
            part = part.replace("{"+key+"}", value)
        result.append(part)
    return result


def run_plan(path: Path, output: Path) -> dict[str, Any]:
    require(sys.platform.startswith("linux"), "Host runtime trials belong on the designated Linux verification worker")
    plan = read_json(path)
    require(isinstance(plan, dict) and plan.get("schema_version") == 1 and plan.get("scope") == "desktop", "run accepts only a version-1 desktop plan; native devices use the exclusive operator and analyze")
    samples = unsigned(plan.get("samples",21), "samples", positive=True)
    require(samples <= 1000, "The requested process sample count is excessive")
    base = path.resolve().parent
    control = controlled_inputs(plan, base)
    implementations = plan.get("implementations")
    require(isinstance(implementations, dict) and set(implementations) == {"baseline","candidate"}, "A controlled run requires baseline and candidate")
    binaries = {}
    binary_receipts = {}
    for label,item in implementations.items():
        require(isinstance(item, dict), "An implementation declaration is invalid")
        binary = relative_path(base,item.get("binary")).resolve()
        require(binary.is_file(), "An implementation executable is missing")
        binaries[label] = binary
        binary_receipts[label] = {"path":str(binary), "sha256":sha256(binary), "bytes":binary.stat().st_size}
    workloads = plan.get("workloads")
    require(isinstance(workloads,list) and bool(workloads), "The plan has no workloads")
    planned_counts = {}
    for workload in workloads:
        require(isinstance(workload,dict), "A workload is not an object")
        name = workload.get("id")
        require(isinstance(name,str) and bool(re.fullmatch(r"[a-z][a-z0-9-]{0,63}",name)), "Invalid workload identifier")
        require(name not in planned_counts, "A workload identifier is duplicated")
        workload_samples = unsigned(workload.get("samples",samples), "workload samples", positive=True)
        require(workload_samples <= 1000, "The workload process sample count is excessive")
        planned_counts[name] = workload_samples
    output = output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    save_json(output/"input-plan.json",plan)
    manifest = {
        "schema_version":1, "kind":"reader_e2e_run_manifest", "expected_pairs":planned_counts,
        "environment":{"host":socket.gethostname(), "platform":sys.platform, "os_page_caches":"Warm and not explicitly dropped", "sampling":"Serial fresh processes, alternating AB/BA per iteration; preparation and file inventories occur before measured process startup"},
        "binaries":binary_receipts, "control":retained_control_contract(plan["control"],base), "control_verification":control, "runs":[],
    }
    reference_fixtures: dict[str,dict[str,Any]] = {}
    environment = os.environ.copy()
    configured_environment = plan.get("environment",{})
    require(isinstance(configured_environment,dict) and all(isinstance(key,str) and isinstance(value,str) for key,value in configured_environment.items()), "environment must map text names to text values")
    environment.update(configured_environment)
    try:
        for workload in workloads:
            require(isinstance(workload,dict), "A workload is not an object")
            name = workload.get("id")
            require(isinstance(name,str) and bool(re.fullmatch(r"[a-z][a-z0-9-]{0,63}",name)), "Invalid workload identifier")
            workload_samples = planned_counts[name]
            template = workload.get("command")
            require(isinstance(template,list) and template.count("{binary}") == 1, "A measured command must identify exactly one implementation binary")
            require("--performance-trace" in template and template[template.index("--performance-trace")+1] == "{trace}", "A measured command must use the dedicated trace destination")
            require("--offline" in template and "--data-dir" in template and "--capture" not in template and "--kindle" not in template, "Host timing requires isolated offline data and excludes captures/native operations")
            expectation = trace_expectation(workload.get("expectation",{"scope":"desktop"}))
            require(expectation["scope"] == "desktop", "A host plan cannot claim native timings")
            preparations = workload.get("prepare",[])
            require(isinstance(preparations,list), "Workload prepare must be a list of argv arrays")
            cwd = relative_path(base,workload.get("cwd",str(base))).resolve()
            require(cwd.is_dir(), "The workload directory is missing")
            timeout = workload.get("timeout_seconds",60)
            require(isinstance(timeout,(int,float)) and not isinstance(timeout,bool) and math.isfinite(timeout) and 1 <= timeout <= 600, "Invalid workload timeout")
            for iteration in range(1,workload_samples+1):
                labels = ("baseline","candidate") if iteration % 2 else ("candidate","baseline")
                for label in labels:
                    run_id = f"{name}-{iteration:03}-{label}"
                    trial_directory = output/run_id
                    trial_directory.mkdir()
                    trace = trial_directory/"trace.json"
                    replacements = {"binary":str(binaries[label]),"trace":str(trace),"trial":str(trial_directory),"iteration":str(iteration),"preparation_binary":str(binaries["baseline"])}
                    record = {"implementation":label,"workload":name,"iteration":iteration,"phase":"measure","trace":str(trace.relative_to(output)),"expectation":expectation,"preparation":[]}
                    try:
                        for index,command in enumerate(preparations):
                            require("{binary}" not in command, "Preparation must use the same implementation for both labels")
                            prepared = run_process(substitute(command,replacements),trial_directory/f"prepare-{index:02}",timeout,cwd,environment)
                            record["preparation"].append(prepared)
                            require(prepared.get("succeeded") is True, "Fixture preparation failed")
                        actual_fixtures = inventory(workload.get("fixtures",[]),base)
                        if name not in reference_fixtures:
                            reference_fixtures[name] = actual_fixtures
                        require(actual_fixtures == reference_fixtures[name], "Prepared fixture bytes changed across implementations or trials")
                        record["fixture_inventory"] = actual_fixtures
                        record["fixture_sha256"] = hashlib.sha256(json.dumps(actual_fixtures,sort_keys=True,separators=(",",":")).encode()).hexdigest()
                        process = run_process(substitute(template,replacements),trial_directory/"process",timeout,cwd,environment)
                        record["process"] = process
                        record["process_exit_code"] = process.get("exit_code")
                        record["supervisor_success"] = process.get("succeeded")
                        record["analysis"] = analyze_trace(trace,expectation)
                        if process.get("succeeded") is not True:
                            record["analysis"]["valid"] = False
                            record["analysis"]["process_failure"] = True
                    except ProcessGroupCleanupError as error:
                        record["analysis"] = {"valid":False,"error":{"type":type(error).__name__,"message":str(error)}}
                        record["fatal_process_group_cleanup_failure"] = True
                        manifest["runs"].append(record)
                        save_json(trial_directory/"trial.json",record)
                        raise
                    except (OSError,ValueError,TypeError,KeyError,IndexError) as error:
                        record["analysis"] = {"valid":False,"error":{"type":type(error).__name__,"message":str(error)}}
                    except BaseException as error:
                        record["analysis"] = {"valid":False,"error":{"type":type(error).__name__,"message":str(error)}}
                        record["interrupted"] = True
                        manifest["runs"].append(record)
                        save_json(trial_directory/"trial.json",record)
                        raise
                    manifest["runs"].append(record)
                    save_json(trial_directory/"trial.json",record)
                    save_json(output/"runs.json",manifest)
        after_hashes = {label:sha256(binary) for label,binary in binaries.items()}
        unchanged = {label:digest == binary_receipts[label]["sha256"] for label,digest in after_hashes.items()}
        for label,digest in after_hashes.items():
            binary_receipts[label]["after_sha256"] = digest
        manifest["binaries_unchanged"] = unchanged
        manifest["summary"] = summarize_runs(manifest["runs"],manifest["expected_pairs"])
        manifest["comparison_qualified"] = manifest["summary"]["paired_samples_complete"] and all(unchanged.values())
        if not manifest["comparison_qualified"]:
            withhold_comparison_ratios(manifest["summary"])
        return manifest
    finally:
        save_json(output/"runs.json",manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode",required=True)
    analyze = commands.add_parser("analyze",help="Qualify retained native or desktop traces without executing a program")
    analyze.add_argument("--manifest",type=Path,required=True)
    analyze.add_argument("--output",type=Path,required=True)
    run = commands.add_parser("run",help="Run serial, controlled host trials on the designated remote worker")
    run.add_argument("--plan",type=Path,required=True)
    run.add_argument("--output",type=Path,required=True)
    arguments = parser.parse_args()
    try:
        if arguments.mode == "analyze":
            report = analyze_manifest(arguments.manifest)
            save_json(arguments.output,report)
            valid = report["summary"]["all_trials_valid"] and report["qualification_trials_valid"]
        else:
            report = run_plan(arguments.plan,arguments.output)
            valid = report["comparison_qualified"]
        print(json.dumps({"valid":valid,"comparison_qualified":report.get("comparison_qualified",False),"output":str(arguments.output)},sort_keys=True))
        return 0 if valid else 1
    except (OSError,ValueError,TypeError,KeyError,IndexError) as error:
        print(f"reader-e2e: {type(error).__name__}: {error}",file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
