#!/usr/bin/env python3
"""Verify native Winit mouse input on an isolated remote Xvfb display.

Run only on the designated Linux worker. Every case gets a separate Xvfb server,
application process, and demo data directory. xdotool sends real X11 mouse and
keyboard events to the application's visible window. No CLI action replay is
used. The application writes the final PNG and state/geometry sidecars.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
import uuid
from typing import Any

from PIL import Image


TITLE = "Aidoku for Kindle"
CASES = {
    "raw-library-swipe": {"screen": "library", "operation": "drag", "start": [540, 380], "end": [80, 380], "route": "library", "page": 2, "selected": "empty"},
    "raw-cover-tap": {"screen": "library", "operation": "tap", "start": [540, 380], "route": "detail", "selected": "empty"},
    "raw-cover-long-press": {"screen": "library", "operation": "hold", "start": [540, 380], "route": "library", "page": 1, "selected": "nonempty"},
    "raw-catalog-swipe": {"screen": "catalog", "operation": "drag", "start": [500, 245], "end": [80, 245], "route": "catalog", "page": 2, "selected": "empty"},
    "raw-reader-hold": {"screen": "reader", "operation": "hold", "hold_seconds": 0.8, "start": [300, 410], "route": "reader", "reader_page": 12, "modal": "choices", "selected": "empty"},
    "raw-reader-hold-600": {"screen": "reader", "operation": "hold", "hold_seconds": 0.6, "start": [300, 410], "route": "reader", "reader_page": 12, "modal": "choices", "selected": "empty"},
    "raw-reader-modal-left": {"screen": "reader", "operation": "hold", "hold_seconds": 0.6, "start": [300, 410], "post_keys": ["Left"], "route": "reader", "reader_page": 12, "menu": False, "modal": "choices", "selected": "empty"},
    "raw-reader-modal-right": {"screen": "reader", "operation": "hold", "hold_seconds": 0.6, "start": [300, 410], "post_keys": ["Right"], "route": "reader", "reader_page": 12, "menu": False, "modal": "choices", "selected": "empty"},
    "raw-reader-modal-escape": {"screen": "reader", "operation": "hold", "hold_seconds": 0.6, "start": [300, 410], "post_keys": ["Left", "Escape"], "route": "reader", "reader_page": 12, "menu": False, "modal": "", "selected": "empty"},
    "raw-download-modal-right": {"screen": "downloads", "operation": "key", "keys": ["Right"], "route": "settings", "page": 1, "panel_page": 2, "modal": "downloads", "selected": "empty"},
    "raw-download-modal-left": {"screen": "downloads", "pre_tap": [407, 657], "operation": "key", "keys": ["Left"], "route": "settings", "page": 1, "panel_page": 1, "modal": "downloads", "selected": "empty"},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def check(case: dict[str, Any], description: str, passed: bool, actual: Any = None) -> None:
    case["checks"].append({"description": description, "passed": bool(passed), "actual": actual})
    if not passed:
        case["passed"] = False
        print(f"FAIL {case['name']}: {description}; actual={actual!r}", flush=True)


def stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


class Mouse:
    def __init__(self, process: subprocess.Popen[Any], started: float, trace: list[dict[str, Any]]):
        self.process = process
        self.started = started
        self.trace = trace
        self.window: str | None = None

    def command(self, *arguments: str, allow_missing: bool = False) -> str:
        command = ["xdotool", *arguments]
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
        self.trace.append({
            "at_ms": round((time.monotonic() - self.started) * 1000, 3),
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        })
        if result.returncode != 0 and not allow_missing:
            raise RuntimeError(f"xdotool failed: {arguments!r}: {result.stderr.strip()}")
        return result.stdout.strip() if result.returncode == 0 else ""

    def find_window(self) -> str:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Application exited before its window was visible: {self.process.returncode}")
            matches = self.command("search", "--onlyvisible", "--all", "--pid", str(self.process.pid), "--name", f"^{TITLE}$", allow_missing=True).splitlines()
            if len(matches) > 1:
                raise RuntimeError("The isolated application exposed multiple matching windows")
            if matches:
                window = matches[0]
                owner = self.command("getwindowpid", window)
                title = self.command("getwindowname", window)
                if owner != str(self.process.pid) or title != TITLE:
                    raise RuntimeError("Window identity did not match the application PID and title")
                self.window = window
                return window
            time.sleep(0.05)
        raise RuntimeError("Timed out waiting for the application's own visible window")

    def move(self, x: int, y: int) -> None:
        if self.window is None:
            raise RuntimeError("The application window has not been selected")
        self.command("mousemove", "--sync", "--window", self.window, str(x), str(y))

    def perform(self, specification: dict[str, Any]) -> dict[str, Any]:
        if specification["operation"] == "key":
            for key in specification["keys"]:
                self.command("key", "--window", str(self.window), key)
                time.sleep(0.1)
            return {"operation": "key", "keys": specification["keys"]}
        self.move(*specification["start"])
        time.sleep(0.08)
        started = time.monotonic()
        self.command("mousedown", "1")
        operation = specification["operation"]
        if operation == "drag":
            x1, y1 = specification["start"]
            x2, y2 = specification["end"]
            for step in range(1, 13):
                target_time = started + 0.28 * step / 12
                time.sleep(max(0, target_time - time.monotonic()))
                self.move(round(x1 + (x2 - x1) * step / 12), round(y1 + (y2 - y1) * step / 12))
        else:
            time.sleep(specification.get("hold_seconds", 0.6) if operation == "hold" else 0.08)
        self.command("mouseup", "1")
        released = time.monotonic()
        for key in specification.get("post_keys", []):
            time.sleep(0.1)
            self.command("key", "--window", str(self.window), key)
        return {"operation": operation, "start": specification["start"], "end": specification.get("end", specification["start"]), "press_to_release_ms": round((released - started) * 1000, 3), "post_keys": specification.get("post_keys", [])}


def run_worker(options: argparse.Namespace) -> int:
    name = options.worker_case
    specification = CASES[name]
    output = options.output_dir.resolve()
    binary = options.binary.resolve()
    data = output / "data" / options.run_id / name
    data.mkdir(parents=True, exist_ok=False)
    runtime = data / "xdg-runtime"
    runtime.mkdir(mode=0o700)
    screenshot = output / f"{name}.png"
    trace: list[dict[str, Any]] = []
    command = [str(binary), "--demo", "--offline", "--device", "pw5", "--screen", specification["screen"], "--data-dir", str(data), "--capture", str(screenshot), "--exit-after-ms", "6000"]
    environment = os.environ.copy()
    environment.update({"SLINT_SCALE_FACTOR": "1", "SLINT_BACKEND": "winit-software", "SLINT_RENDERER": "software", "XDG_RUNTIME_DIR": str(runtime), "TZ": "Asia/Shanghai", "RUST_BACKTRACE": "1"})
    case: dict[str, Any] = {
        "name": name, "passed": True, "checks": [], "command": command,
        "input_transport": "xdotool X11 mouse and keyboard events on an isolated Xvfb display",
        "cli_actions_used": False, "display": environment.get("DISPLAY"),
        "data_dir": str(data), "screenshot": str(screenshot), "state": {}, "metadata": {},
    }
    started = time.monotonic()
    process: subprocess.Popen[Any] | None = None
    try:
        before = sha256(binary)
        case["binary_sha256_before"] = before
        if before != options.expected_sha256:
            raise RuntimeError(f"Frozen binary hash mismatch: {before}")
        with (output / f"{name}.log").open("wb") as log:
            process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
            case["pid"] = process.pid
            mouse = Mouse(process, started, trace)
            window = mouse.find_window()
            case["window"] = window
            geometry_text = mouse.command("getwindowgeometry", "--shell", window)
            geometry = dict(line.split("=", 1) for line in geometry_text.splitlines() if "=" in line)
            case["window_geometry"] = geometry
            check(case, "The owned visible window has a 618 by 824 client area", geometry.get("WIDTH") == "618" and geometry.get("HEIGHT") == "824", geometry)
            if not case["passed"]:
                raise RuntimeError("Raw coordinates cannot be used with unexpected window geometry")
            mouse.command("windowraise", window)
            mouse.command("windowfocus", "--sync", window)
            time.sleep(0.35)
            if "pre_tap" in specification:
                case["preparatory_gestures"] = [mouse.perform({"operation": "tap", "start": specification["pre_tap"]})]
                time.sleep(0.3)
            case["gesture"] = mouse.perform(specification)
            process.wait(timeout=12)
            case["exit_code"] = process.returncode
        check(case, "The application exited successfully", process.returncode == 0, process.returncode)
        for kind in ("state", "metadata"):
            sidecar = screenshot.with_suffix(f".{kind}.json")
            check(case, f"The application generated the {kind} sidecar", sidecar.is_file(), str(sidecar))
            if sidecar.is_file():
                case[kind] = json.loads(sidecar.read_text(encoding="utf-8"))
        check(case, "The application generated the PNG screenshot", screenshot.is_file(), str(screenshot))
        if screenshot.is_file():
            with Image.open(screenshot) as image:
                check(case, "The capture is a 618 by 824 PNG", image.format == "PNG" and image.size == (618, 824), {"format": image.format, "size": list(image.size)})
        state = case["state"]
        metadata = case["metadata"]
        check(case, "The final route matches the raw gesture", state.get("route") == specification["route"], state.get("route"))
        if "page" in specification:
            check(case, "The final list page matches the raw gesture", state.get("page") == specification["page"], state.get("page"))
        for field in ("reader_page", "modal", "menu", "panel_page"):
            if field in specification:
                check(case, f"The final {field} matches the raw gesture", state.get(field) == specification[field], state.get(field))
        selected = state.get("selected")
        selected_matches = isinstance(selected, list) and (len(selected) == 0 if specification["selected"] == "empty" else len(selected) > 0)
        check(case, f"Selection remains {specification['selected']}", selected_matches, selected)
        check(case, "The capture identifies the desktop PW5 profile", metadata.get("profile") == "pw5" and metadata.get("backend") == "desktop", {"profile": metadata.get("profile"), "backend": metadata.get("backend")})
        check(case, "Capture geometry and scale match the raw pointer coordinates", all(metadata.get(field) == [618, 824] for field in ("logical_size", "window_size", "snapshot_size")) and metadata.get("scale_factor") == 1, metadata)
        check(case, "The fixture is isolated, offline demo data", state.get("demo") is True and state.get("offline") is True, {"demo": state.get("demo"), "offline": state.get("offline")})
    except Exception as error:
        check(case, "The raw input case completed without a harness error", False, f"{type(error).__name__}: {error}")
    finally:
        if process is not None:
            stop_process(process)
        case["elapsed_seconds"] = round(time.monotonic() - started, 3)
        after = sha256(binary)
        case["binary_sha256_after"] = after
        check(case, "The frozen binary hash remains unchanged", after == options.expected_sha256 and case.get("binary_sha256_before") == after, after)
        save_json(output / f"{name}.mouse.json", {"window_pid": case.get("pid"), "window_id": case.get("window"), "display": case["display"], "cli_actions_used": False, "preparatory_gestures": case.get("preparatory_gestures", []), "gesture": case.get("gesture"), "xdotool_commands": trace})
        save_json(output / f"{name}.case.json", case)
    print(f"{'PASS' if case['passed'] else 'FAIL'} {name} ({case['elapsed_seconds']}s)", flush=True)
    return 0 if case["passed"] else 1


def run_suite(options: argparse.Namespace) -> int:
    output = options.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    binary = options.binary.resolve(strict=True)
    for tool in ("xvfb-run", "Xvfb", "xdotool"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"The remote worker requires {tool}")
    before = sha256(binary)
    requested_sha256 = options.expected_sha256
    options.expected_sha256 = before if requested_sha256 is None else requested_sha256
    if before != options.expected_sha256:
        raise RuntimeError(f"Frozen binary hash mismatch: {before}")
    run_id = uuid.uuid4().hex[:12]
    cases = []
    started = time.monotonic()
    for name in options.selected_cases or CASES:
        command = ["xvfb-run", "-a", "-s", "-screen 0 1200x1600x24 -nolisten tcp", sys.executable, str(Path(__file__).resolve()), "--binary", str(binary), "--output-dir", str(output), "--expected-sha256", options.expected_sha256, "--worker-case", name, "--run-id", run_id]
        print(f"RUN {name}", flush=True)
        worker = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            log, _ = worker.communicate(timeout=options.timeout)
        except subprocess.TimeoutExpired:
            os.killpg(worker.pid, signal.SIGTERM)
            try:
                log, _ = worker.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid, signal.SIGKILL)
                log, _ = worker.communicate(timeout=3)
            log += "\nThe isolated Xvfb worker exceeded its timeout.\n"
        (output / f"{name}.worker.log").write_text(log, encoding="utf-8")
        print(log.strip(), flush=True)
        case_path = output / f"{name}.case.json"
        if case_path.is_file():
            case = json.loads(case_path.read_text(encoding="utf-8"))
            if worker.returncode != 0 and case.get("passed") is True:
                check(case, "The isolated Xvfb worker exited successfully", False, worker.returncode)
        else:
            case = {"name": name, "passed": False, "checks": [{"description": "The isolated Xvfb worker generated a case report", "passed": False, "actual": worker.returncode}]}
        cases.append(case)
    after = sha256(binary)
    unchanged = before == after == options.expected_sha256
    failures = [case["name"] for case in cases if not case["passed"]]
    report = {
        "schema_version": 1, "kind": "remote-raw-winit-mouse", "host": platform.node(), "run_id": run_id,
        "binary": str(binary), "expected_binary_sha256": options.expected_sha256,
        "expected_sha256_source": "command-line" if requested_sha256 is not None else "initial-binary-hash",
        "binary_sha256_before": before, "binary_sha256_after": after, "binary_unchanged": unchanged,
        "passed": len(cases) - len(failures), "failed": len(failures), "failed_cases": failures,
        "success": not failures and unchanged, "elapsed_seconds": round(time.monotonic() - started, 3),
        "cli_actions_used": False, "cases": cases,
        "evidence_scope": "Real XTEST mouse motion/buttons delivered to visible PID-owned Winit windows on separate Xvfb displays. No CLI action replay or product source modifications. Physical Kindle input and e-ink behavior require separate device verification.",
    }
    save_json(output / "report.json", report)
    lines = ["# Raw Winit mouse verification", "", f"- Host: `{platform.node()}`", f"- Binary SHA-256: `{before}`", f"- Frozen binary unchanged: {str(unchanged).lower()}", f"- Passed: {report['passed']}", f"- Failed: {report['failed']}", "- Input: xdotool XTEST mouse events, without CLI action replay.", "- Isolation: one Xvfb display, application process, and fresh demo data directory per case.", "", "| Case | Result | Screenshot | Mouse events |", "| --- | --- | --- | --- |"]
    for case in cases:
        name = case["name"]
        lines.append(f"| {name} | {'PASS' if case['passed'] else 'FAIL'} | [PNG]({name}.png) | [JSON]({name}.mouse.json) |")
        for item in case["checks"]:
            if not item["passed"]:
                lines.extend(["", f"Failure in `{name}`: {item['description']}; actual: `{item.get('actual')!r}`."])
    lines.extend(["", report["evidence_scope"], ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Raw mouse verification: {report['passed']} passed, {report['failed']} failed; binary unchanged={unchanged}. Report: {output / 'report.json'}", flush=True)
    return 0 if report["success"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True, help="Frozen desktop application executable on the remote worker")
    parser.add_argument("--output-dir", type=Path, required=True, help="Retain captures, state sidecars, mouse traces, logs, and reports")
    parser.add_argument("--expected-sha256", help="Require this SHA-256; otherwise freeze the binary's initial SHA-256 for the entire suite")
    parser.add_argument("--timeout", type=int, default=25, help="Per-case isolated worker timeout in seconds")
    parser.add_argument("--case", dest="selected_cases", choices=CASES, action="append", help="Run only the selected case; repeat to select multiple cases")
    parser.add_argument("--worker-case", choices=CASES, help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run this script only on the designated remote Linux worker through ssh test-env")
    if options.worker_case:
        if not options.run_id or not os.environ.get("DISPLAY") or not options.expected_sha256:
            parser.error("An internal worker requires its isolated display, run ID, and frozen binary SHA-256")
        return run_worker(options)
    return run_suite(options)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print(f"Raw mouse verification failed: {error}", file=sys.stderr, flush=True)
        sys.exit(2)
