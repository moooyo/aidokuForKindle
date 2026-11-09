#!/usr/bin/env python3
"""Measure desktop Library navigation while a large public cache is cleared.

Run only on the designated remote Linux worker after execution is authorized.
The root agent supplies frozen application and cache-seeder executables; this
script never invokes Cargo. One isolated Xvfb worker owns both application
processes and all mouse input. Cache preparation uses the seeder's public APIs.

The response metric is the first exact Library-title pixel match observed with
XGetImage after the real Library mousedown. The click itself is included in the
1000 ms limit. Final application exit and final state are separate evidence and
never substitute for this live pixel measurement. This is a desktop UI metric,
not a claim about physical e-ink refresh latency or cache protection correctness.
Navigation is requested 20 ms after clear release; a gap above 50 ms invalidates
the measurement conditions instead of counting as a successful response test.
"""

from __future__ import annotations

import argparse
import ctypes
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

from PIL import Image, ImageChops


TITLE = "Aidoku for Kindle"
SIZE = (618, 824)
TITLE_ROI = (20, 54, 180, 102)
ENTRIES = 1000
BYTES_PER_ENTRY = 65865
RESPONSE_LIMIT_MS = 1000
CLEAR_TO_NAV_TARGET_MS = 20
CLEAR_TO_NAV_MAX_MS = 50


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def check(report: dict[str, Any], description: str, passed: bool, actual: Any = None) -> None:
    report["checks"].append({"description": description, "passed": bool(passed), "actual": actual})
    if not passed:
        report["passed"] = False
        print(f"FAIL cache-ui-response: {description}; actual={actual!r}", flush=True)


def stop_process(process: subprocess.Popen[Any] | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


class XImageFunctions(ctypes.Structure):
    _fields_ = [(name, ctypes.c_void_p) for name in (
        "create_image", "destroy_image", "get_pixel", "put_pixel", "sub_image", "add_pixel"
    )]


class XImage(ctypes.Structure):
    # Native Xlib.h layout, including natural unsigned-long alignment.
    _fields_ = [
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int), ("format", ctypes.c_int),
        ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong), ("obdata", ctypes.c_void_p),
        ("functions", XImageFunctions),
    ]


class XCapture:
    def __init__(self) -> None:
        self.library = ctypes.CDLL("libX11.so.6")
        self.library.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.library.XOpenDisplay.restype = ctypes.c_void_p
        self.library.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.library.XCloseDisplay.restype = ctypes.c_int
        self.library.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
        self.library.XGetImage.restype = ctypes.POINTER(XImage)
        self.display = self.library.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("XOpenDisplay could not open this worker's isolated display")
        self.formats: list[dict[str, Any]] = []

    def __enter__(self) -> "XCapture":
        return self

    def __exit__(self, *_: Any) -> None:
        self.library.XCloseDisplay(self.display)
        self.display = None

    def grab(self, window: str, rectangle: tuple[int, int, int, int]) -> Image.Image:
        left, top, right, bottom = rectangle
        width, height = right - left, bottom - top
        pointer = self.library.XGetImage(self.display, int(window), left, top, width, height, ctypes.c_ulong(-1).value, 2)
        if not pointer:
            raise RuntimeError("XGetImage could not read the owned client window")
        header = pointer.contents
        destroy_address = header.functions.destroy_image
        if not destroy_address:
            raise RuntimeError("The XImage destroy_image function is missing")
        destroy = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(XImage))(destroy_address)
        try:
            pixel_format = {key: getattr(header, key) for key in ("depth", "bits_per_pixel", "byte_order", "red_mask", "green_mask", "blue_mask")}
            if pixel_format not in self.formats:
                self.formats.append(pixel_format)
            if header.width != width or header.height != height or header.format != 2 or header.xoffset != 0:
                raise RuntimeError("XGetImage returned unsupported client image geometry")
            if (header.red_mask, header.green_mask, header.blue_mask) != (0xFF0000, 0x00FF00, 0x0000FF) or header.depth not in (24, 32):
                raise RuntimeError(f"Unsupported X11 visual masks: {pixel_format}")
            modes = {(32, 0): "BGRX", (32, 1): "XRGB", (24, 0): "BGR", (24, 1): "RGB"}
            mode = modes.get((header.bits_per_pixel, header.byte_order))
            if mode is None or not header.data or header.bytes_per_line < width * (header.bits_per_pixel // 8):
                raise RuntimeError(f"Unsupported X11 pixel storage: {pixel_format}")
            raw = ctypes.string_at(header.data, header.bytes_per_line * height)
            # The X byte is storage padding, not alpha. Copy before destroying.
            return Image.frombytes("RGB", (width, height), raw, "raw", mode, header.bytes_per_line, 1)
        finally:
            destroy(pointer)


class Mouse:
    def __init__(self, process: subprocess.Popen[Any], started_ns: int, trace: list[dict[str, Any]]):
        self.process = process
        self.started_ns = started_ns
        self.trace = trace
        self.window: str | None = None

    def command(self, *arguments: str, allow_missing: bool = False) -> str:
        started = time.perf_counter_ns()
        result = subprocess.run(["xdotool", *arguments], capture_output=True, text=True, timeout=3, check=False)
        ended = time.perf_counter_ns()
        self.trace.append({"start_ns": started, "end_ns": ended, "at_ms": (started - self.started_ns) / 1_000_000, "command": ["xdotool", *arguments], "exit_code": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()})
        if result.returncode != 0 and not allow_missing:
            raise RuntimeError(f"xdotool failed: {arguments!r}: {result.stderr.strip()}")
        return result.stdout.strip() if result.returncode == 0 else ""

    def find_window(self) -> str:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"The application exited before its visible window: {self.process.returncode}")
            matches = self.command("search", "--onlyvisible", "--all", "--pid", str(self.process.pid), "--name", f"^{TITLE}$", allow_missing=True).splitlines()
            if len(matches) > 1:
                raise RuntimeError("Multiple windows matched this case's application PID and title")
            if matches:
                window = matches[0]
                if self.command("getwindowpid", window) != str(self.process.pid) or self.command("getwindowname", window) != TITLE:
                    raise RuntimeError("The selected window does not belong to this case's application")
                geometry = dict(line.split("=", 1) for line in self.command("getwindowgeometry", "--shell", window).splitlines() if "=" in line)
                if geometry.get("WIDTH") != "618" or geometry.get("HEIGHT") != "824":
                    raise RuntimeError(f"The owned window has unexpected client geometry: {geometry}")
                self.window = window
                self.command("windowraise", window)
                self.command("windowfocus", "--sync", window)
                return window
            time.sleep(0.05)
        raise RuntimeError("The application's own visible window did not appear")

    def move(self, x: int, y: int) -> None:
        if self.window is None:
            raise RuntimeError("No owned window has been selected")
        self.command("mousemove", "--sync", "--window", self.window, str(x), str(y))

    def tap(self, x: int, y: int) -> dict[str, int]:
        self.move(x, y)
        time.sleep(0.04)
        return self.click_here()

    def click_here(self) -> dict[str, int]:
        down = time.perf_counter_ns()
        self.command("mousedown", "1")
        time.sleep(0.04)
        release = time.perf_counter_ns()
        self.command("mouseup", "1")
        return {"down_start_ns": down, "release_start_ns": release, "release_end_ns": time.perf_counter_ns()}


def read_capture(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return tuple(json.loads(path.with_suffix(f".{kind}.json").read_text(encoding="utf-8")) for kind in ("state", "metadata"))


def require_capture(report: dict[str, Any], path: Path, state: dict[str, Any], metadata: dict[str, Any], route: str) -> None:
    with Image.open(path) as image:
        check(report, f"The {route} capture is a 618 by 824 PNG", image.format == "PNG" and image.size == SIZE, {"format": image.format, "size": list(image.size)})
    check(report, f"The final {route} state matches the captured route", state.get("route") == route, state.get("route"))
    check(report, "The capture retains the isolated offline demo fixture", state.get("demo") is True and state.get("offline") is True, {"demo": state.get("demo"), "offline": state.get("offline")})
    check(report, "The capture metadata agrees with the real client geometry", metadata.get("profile") == "pw5" and metadata.get("backend") == "desktop" and metadata.get("scale_factor") == 1 and all(metadata.get(field) == list(SIZE) for field in ("logical_size", "window_size", "snapshot_size")), metadata)


def run_worker(options: argparse.Namespace) -> int:
    output = options.output_dir.resolve()
    binary = options.binary.resolve()
    seeder = options.cache_seeder.resolve()
    data = output / "data" / options.run_id / "cache-ui-response"
    data.mkdir(parents=True, exist_ok=False)
    runtime = data / "xdg-runtime"
    runtime.mkdir(mode=0o700)
    environment = os.environ.copy()
    environment.update({"SLINT_SCALE_FACTOR": "1", "SLINT_BACKEND": "winit-software", "SLINT_RENDERER": "software", "XDG_RUNTIME_DIR": str(runtime), "TZ": "Asia/Shanghai", "RUST_BACKTRACE": "1"})
    report: dict[str, Any] = {"schema_version": 1, "kind": "remote-cache-ui-response", "passed": True, "checks": [], "run_id": options.run_id, "host": platform.node(), "display": os.environ.get("DISPLAY"), "binary": str(binary), "expected_binary_sha256": options.expected_sha256, "cache_seeder": str(seeder), "cache_seeder_sha256": sha256(seeder), "outer_data_dir": str(data), "cli_actions_used": False, "response_limit_ms": RESPONSE_LIMIT_MS, "roi_ltrb": list(TITLE_ROI), "stage": "baseline", "measurement_scope": "Desktop X11 client pixels only; no physical e-ink latency or imported/pinned-content protection claim."}
    trace: list[dict[str, Any]] = []
    polls: list[dict[str, Any]] = []
    started_ns = time.perf_counter_ns()
    baseline_process: subprocess.Popen[Any] | None = None
    process: subprocess.Popen[Any] | None = None
    try:
        report["binary_sha256_before"] = sha256(binary)
        if report["binary_sha256_before"] != options.expected_sha256:
            raise RuntimeError("The application binary does not match the required frozen SHA-256")
        baseline_path = output / "baseline-library.png"
        baseline_command = [str(binary), "--demo", "--offline", "--device", "pw5", "--screen", "library", "--data-dir", str(data), "--capture", str(baseline_path), "--exit-after-ms", "4000"]
        report["baseline_command"] = baseline_command
        with (output / "baseline-library.log").open("wb") as log:
            baseline_process = subprocess.Popen(baseline_command, env=environment, stdout=log, stderr=subprocess.STDOUT)
            mouse = Mouse(baseline_process, started_ns, trace)
            window = mouse.find_window()
            report["baseline_window"] = {"pid": baseline_process.pid, "window": window}
            time.sleep(0.5)
            with XCapture() as capture:
                live = capture.grab(window, (0, 0, *SIZE))
                live.save(output / "baseline-live-client.png")
                live_title = live.crop(TITLE_ROI)
                report["x11_pixel_formats"] = capture.formats
            baseline_process.wait(timeout=30)
        check(report, "The baseline application exited successfully", baseline_process.returncode == 0, baseline_process.returncode)
        baseline_state, baseline_metadata = read_capture(baseline_path)
        report["baseline_state"] = baseline_state
        report["baseline_metadata"] = baseline_metadata
        require_capture(report, baseline_path, baseline_state, baseline_metadata, "library")
        with Image.open(baseline_path) as image:
            expected_title = image.convert("RGB").crop(TITLE_ROI)
        expected_title.save(output / "baseline-library-title.png")
        calibrated = ImageChops.difference(live_title, expected_title).getbbox() is None
        check(report, "Live X11 title pixels exactly match the application's baseline PNG", calibrated, {"roi": list(TITLE_ROI), "expected_rgb_sha256": hashlib.sha256(expected_title.tobytes()).hexdigest(), "live_rgb_sha256": hashlib.sha256(live_title.tobytes()).hexdigest()})
        if not calibrated or not report["passed"]:
            raise RuntimeError("Baseline geometry or X11 pixel calibration failed; no response conclusion is possible")
        actual_data = Path(baseline_state["database"]).resolve().parent
        if actual_data != (data / "demo").resolve():
            raise RuntimeError("The baseline database is outside this case's expected demo data directory")
        report["actual_app_data_dir"] = str(actual_data)
        report["stage"] = "public-cache-population"
        seeder_command = [str(seeder), "--scenario", "populate-public", "--data-dir", str(actual_data), "--entries", str(ENTRIES)]
        report["seeder_command"] = seeder_command
        result = subprocess.run(seeder_command, capture_output=True, text=True, timeout=120, check=False)
        (output / "cache-seeder.stdout.json").write_text(result.stdout, encoding="utf-8")
        (output / "cache-seeder.stderr.log").write_text(result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"The public cache seeder failed with exit code {result.returncode}")
        seeded = json.loads(result.stdout)
        report["seeder"] = seeded
        checks = seeded.get("checks", {})
        valid = seeded.get("public_api_only") is True and checks.get("public_cache_api_used") is True and checks.get("first_and_last_images_valid") is True
        valid = valid and seeded.get("entries_requested") == ENTRIES and (seeded.get("width"), seeded.get("height")) == (256, 256) and seeded.get("bytes_per_entry") == BYTES_PER_ENTRY
        before = seeded.get("cache", {})
        prior = seeded.get("before", {})
        valid = valid and before.get("entries", 0) - prior.get("entries", 0) == ENTRIES and before.get("unpinned_bytes", 0) >= ENTRIES * BYTES_PER_ENTRY
        check(report, "Public APIs appended 1000 real 256 by 256 PNG cache entries", valid, seeded)
        if not valid:
            raise RuntimeError("The public cache fixture does not match the required UI workload")
        if sha256(binary) != options.expected_sha256 or sha256(seeder) != report["cache_seeder_sha256"]:
            raise RuntimeError("A supplied executable changed during cache fixture preparation")
        report["stage"] = "live-cache-clear-navigation"
        final_path = output / "final-library.png"
        command = [str(binary), "--demo", "--offline", "--device", "pw5", "--screen", "settings", "--data-dir", str(data), "--capture", str(final_path), "--exit-after-ms", "12000"]
        report["application_command"] = command
        with (output / "cache-clear-application.log").open("wb") as log:
            process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
            mouse = Mouse(process, started_ns, trace)
            window = mouse.find_window()
            report["interactive_window"] = {"pid": process.pid, "window": window}
            time.sleep(0.5)
            with XCapture() as capture:
                settings_title = capture.grab(window, TITLE_ROI)
                check(report, "The initial Settings title differs from the Library target", ImageChops.difference(settings_title, expected_title).getbbox() is not None)
                mouse.tap(200, 414)
                time.sleep(0.25)
                capture.grab(window, (0, 0, *SIZE)).save(output / "storage-before-clear.png")
                clear = mouse.tap(309, 495)
                mouse.move(62, 789)
                target_click_ns = clear["release_end_ns"] + CLEAR_TO_NAV_TARGET_MS * 1_000_000
                time.sleep(max(0, (target_click_ns - time.perf_counter_ns()) / 1_000_000_000))
                navigation = mouse.click_here()
                gap_ms = (navigation["down_start_ns"] - clear["release_end_ns"]) / 1_000_000
                timing_valid = CLEAR_TO_NAV_TARGET_MS <= gap_ms <= CLEAR_TO_NAV_MAX_MS
                report["mouse_timings"] = {"clear": clear, "library": navigation, "requested_clear_release_to_library_down_ms": CLEAR_TO_NAV_TARGET_MS, "maximum_accepted_clear_release_to_library_down_ms": CLEAR_TO_NAV_MAX_MS, "clear_release_to_library_down_ms": gap_ms, "measurement_conditions_met": timing_valid}
                check(report, "The nominal 20 ms navigation follows clear release within the 50 ms scheduling window", timing_valid, report["mouse_timings"])
                origin = navigation["down_start_ns"]
                first_match_ns = None
                limit_reported = False
                last_title = settings_title
                while time.perf_counter_ns() - origin < 3_000_000_000:
                    last_title = capture.grab(window, TITLE_ROI)
                    observed_ns = time.perf_counter_ns()
                    matches = ImageChops.difference(last_title, expected_title).getbbox() is None
                    elapsed_ms = (observed_ns - origin) / 1_000_000
                    polls.append({"observed_ns": observed_ns, "elapsed_ms": elapsed_ms, "exact_title_match": matches})
                    if matches:
                        first_match_ns = observed_ns
                        break
                    if elapsed_ms > RESPONSE_LIMIT_MS and not limit_reported:
                        check(report, "Library pixels respond within 1000 ms of the real mousedown", False, {"elapsed_ms_without_match": elapsed_ms})
                        limit_reported = True
                    time.sleep(0.01)
                last_title.save(output / "response-title.png")
                response_frame = capture.grab(window, (0, 0, *SIZE))
                response_frame_ns = time.perf_counter_ns()
                response_frame.save(output / "response.png")
                latency_ms = (first_match_ns - origin) / 1_000_000 if first_match_ns else None
                report["response"] = {"origin": "immediately before the real Library mousedown; includes click duration and pixel acquisition", "origin_ns": origin, "first_match_ns": first_match_ns, "response_frame_ns": response_frame_ns, "response_frame_title_matches": ImageChops.difference(response_frame.crop(TITLE_ROI), expected_title).getbbox() is None, "latency_ms": latency_ms, "threshold_ms": RESPONSE_LIMIT_MS, "poll_count": len(polls), "measurement_conditions_met": timing_valid, "exact_pixel_match_required": True, "final_exit_used_as_response_evidence": False}
                check(report, "The first exact live Library-title match is within 1000 ms", latency_ms is not None and latency_ms <= RESPONSE_LIMIT_MS, report["response"])
                report["x11_pixel_formats"] = capture.formats
            process.wait(timeout=30)
        check(report, "The interactive application exited successfully", process.returncode == 0, process.returncode)
        state, metadata = read_capture(final_path)
        report["final_state"] = state
        report["final_metadata"] = metadata
        require_capture(report, final_path, state, metadata, "library")
        after = state.get("cache", {})
        report["cache_comparison"] = {"populated": before, "final": after}
        check(report, "Cache entries decreased after the actual clear-cache action", isinstance(after.get("entries"), int) and after["entries"] < before["entries"], {"before": before.get("entries"), "after": after.get("entries")})
        check(report, "Unpinned cache bytes decreased after the actual clear-cache action", isinstance(after.get("unpinned_bytes"), int) and after["unpinned_bytes"] < before["unpinned_bytes"], {"before": before.get("unpinned_bytes"), "after": after.get("unpinned_bytes")})
        report["stage"] = "complete"
    except Exception as error:
        report["failure_stage"] = report["stage"]
        check(report, "The cache UI case completed without a preparation or capture error", False, f"{type(error).__name__}: {error}")
    finally:
        stop_process(baseline_process)
        stop_process(process)
        app_after = sha256(binary)
        seeder_after = sha256(seeder)
        report["binary_sha256_after"] = app_after
        report["cache_seeder_sha256_after"] = seeder_after
        check(report, "The frozen application binary remains unchanged", app_after == options.expected_sha256, app_after)
        check(report, "The supplied cache seeder remains unchanged", seeder_after == report["cache_seeder_sha256"], seeder_after)
        report["elapsed_seconds"] = (time.perf_counter_ns() - started_ns) / 1_000_000_000
        save_json(output / "mouse.json", {"cli_actions_used": False, "windows": {key: report.get(key) for key in ("baseline_window", "interactive_window")}, "commands": trace, "timings": report.get("mouse_timings")})
        save_json(output / "response-polls.json", polls)
        save_json(output / "worker-report.json", report)
    print(f"{'PASS' if report['passed'] else 'FAIL'} cache-ui-response ({report['elapsed_seconds']:.3f}s)", flush=True)
    return 0 if report["passed"] else 1


def run_suite(options: argparse.Namespace) -> int:
    output = options.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for executable in ("xvfb-run", "Xvfb", "xdotool"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"The remote worker requires {executable}")
    binary = options.binary.resolve(strict=True)
    seeder = options.cache_seeder.resolve(strict=True)
    if sha256(binary) != options.expected_sha256:
        raise RuntimeError("The binary does not match --expected-sha256")
    run_id = uuid.uuid4().hex[:12]
    command = ["xvfb-run", "-a", "-s", "-screen 0 1200x1600x24 -nolisten tcp", sys.executable, str(Path(__file__).resolve()), "--binary", str(binary), "--expected-sha256", options.expected_sha256, "--cache-seeder", str(seeder), "--output-dir", str(output), "--worker", "--run-id", run_id]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        log, _ = process.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            log, _ = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            log, _ = process.communicate(timeout=3)
        log += "\nThe isolated cache UI worker exceeded its timeout.\n"
    (output / "worker.log").write_text(log, encoding="utf-8")
    print(log.strip(), flush=True)
    path = output / "worker-report.json"
    if path.is_file():
        report = json.loads(path.read_text(encoding="utf-8"))
        check(report, "The case report belongs to this fresh isolated run", report.get("run_id") == run_id, report.get("run_id"))
        check(report, "The isolated Xvfb worker exited successfully", process.returncode == 0, process.returncode)
    else:
        report = {"schema_version": 1, "kind": "remote-cache-ui-response", "passed": False, "run_id": run_id, "checks": [{"description": "The isolated worker generated a report", "passed": False, "actual": process.returncode}]}
    save_json(output / "report.json", report)
    response = report.get("response", {})
    lines = ["# Large-cache desktop UI response", "", f"- Result: {'PASS' if report['passed'] else 'FAIL'}", f"- Application SHA-256: `{options.expected_sha256}`", "- Cache preparation: 1000 public API PNG entries, 256 by 256 pixels each.", "- Input: real xdotool XTEST mouse events on this worker's PID-owned client window.", f"- Library-title ROI: `{TITLE_ROI}` in client coordinates.", f"- Clear release to Library mousedown: {report.get('mouse_timings', {}).get('clear_release_to_library_down_ms')} ms; requested 20 ms, maximum accepted 50 ms.", f"- Measurement conditions met: {response.get('measurement_conditions_met')}.", f"- Observed first exact-match latency: {response.get('latency_ms')} ms.", "- Acceptance limit: 1000 ms from Library mousedown, including the click and pixel acquisition.", "- Final application exit is not response-latency evidence.", "", "[Baseline](baseline-library.png) | [Response frame](response.png) | [Live title](response-title.png) | [Final capture](final-library.png) | [Mouse timings](mouse.json) | [Pixel observations](response-polls.json) | [Public seeder JSON](cache-seeder.stdout.json)", "", "This report covers desktop UI response and cache-stat reduction. Imported and pinned content protection is covered by separate backend tests; physical e-ink refresh is outside this metric.", "", "## Checks", ""]
    for item in report["checks"]:
        lines.append(f"- {'PASS' if item['passed'] else 'FAIL'}: {item['description']}")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Cache UI response: {'PASS' if report['passed'] else 'FAIL'}. Report: {output / 'report.json'}", flush=True)
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True, help="Root-frozen desktop application executable")
    parser.add_argument("--expected-sha256", required=True, help="Required frozen application SHA-256")
    parser.add_argument("--cache-seeder", type=Path, required=True, help="Root-built cache-benchmark executable with the populate-public scenario")
    parser.add_argument("--output-dir", type=Path, required=True, help="Retain live response evidence, seeder output, mouse timings, captures, state, and cache statistics")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    options = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run only on the designated remote Linux worker after execution is authorized")
    for label, executable in (("binary", options.binary), ("cache-seeder", options.cache_seeder)):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            parser.error(f"--{label} must identify the supplied executable")
    if options.worker:
        if not options.run_id or not os.environ.get("DISPLAY"):
            parser.error("An internal worker requires its fresh run ID and isolated display")
        return run_worker(options)
    return run_suite(options)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Cache UI response verification failed: {error}", file=sys.stderr, flush=True)
        sys.exit(2)
