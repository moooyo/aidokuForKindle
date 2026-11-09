#!/usr/bin/env python3
"""Compare closed reader captures with their actually displayed processed PNGs.

Run only on the designated remote Linux worker after the root agent freezes a
binary and authorizes execution. This script never builds software. The oracle
ports the opaque Shared RGB image path of Slint software renderer 1.17.1: f32
contain geometry, rounding of absolute rectangle endpoints, and 8-bit fixed-point
point sampling. Pillow resizing is not used. Every RGB pixel must match exactly;
there are no ignored borders, gutters, overlays, or pixel-error tolerances.

Each case starts with a fresh demo database. Only the application preferences
table is prepared; the application creates its schema, demo assets, and reader
through normal startup. All expected images come from displayed_reader paths,
never from raw source pages. The script retains independent processed copies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import time
import uuid
from typing import Any

from PIL import Image, ImageChops, ImageStat


SAMPLER_CONTRACT = "slint-software-1.17.1-shared-rgb8-fixed8-v1"
SAMPLER_SOURCES = [
    "i-slint-core-1.17.1/graphics/image.rs:449-454,1167-1248,1293-1329",
    "i-slint-renderer-software-1.17.1/lib.rs:2200,2321-2335",
    "i-slint-renderer-software-1.17.1/scene.rs:398-411",
    "i-slint-renderer-software-1.17.1/draw_functions.rs:41-50,196-283",
    "euclid-0.22.14/src/rect.rs:623-640",
]
MODES = {
    "pw5-rtl": {"device": "pw5", "size": [618, 824], "direction": "rtl", "landscape": False, "spread": False, "dual": False},
    "pw5-ltr": {"device": "pw5", "size": [618, 824], "direction": "ltr", "landscape": False, "spread": False, "dual": False},
    "pw5-vertical": {"device": "pw5", "size": [618, 824], "direction": "vertical", "landscape": False, "spread": False, "dual": False},
    "scribe-portrait": {"device": "scribe", "size": [930, 1240], "direction": "rtl", "landscape": False, "spread": False, "dual": False},
    "scribe-landscape-rtl": {"device": "scribe", "size": [1240, 930], "direction": "rtl", "landscape": True, "spread": True, "dual": True},
    "scribe-landscape-ltr": {"device": "scribe", "size": [1240, 930], "direction": "ltr", "landscape": True, "spread": True, "dual": True},
    "scribe-landscape-vertical": {"device": "scribe", "size": [1240, 930], "direction": "vertical", "landscape": True, "spread": True, "dual": False},
}
VARIANTS = ("default", "bookmark-toggle", "footer-1", "footer-2", "footer-3", "background-notification")
CASE_IDS = tuple(f"{mode}-{variant}" for mode in MODES for variant in VARIANTS) + (
    "scribe-landscape-rtl-last-page", "scribe-landscape-ltr-last-page",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def rounded_edge(value: float) -> int:
    return math.floor(f32(value + 0.5))


def contain_rectangle(source: tuple[int, int], slot: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    source_width, source_height = map(f32, source)
    x, y, width, height = map(f32, slot)
    scale = min(f32(width / source_width), f32(height / source_height))
    fitted_width = f32(source_width * scale)
    fitted_height = f32(source_height * scale)
    left = f32(x + f32(f32(width - fitted_width) / 2.0))
    top = f32(y + f32(f32(height - fitted_height) / 2.0))
    right = f32(left + fitted_width)
    bottom = f32(top + fitted_height)
    return tuple(rounded_edge(edge) for edge in (left, top, right, bottom))


def opaque_grayscale_png(raw: bytes) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as original:
        if original.format != "PNG":
            raise ValueError("A displayed processed image is not a PNG")
        rgba = original.convert("RGBA")
        if rgba.getchannel("A").getextrema() != (255, 255):
            raise ValueError("The Shared RGB oracle requires opaque processed PNGs")
        red, green, blue, _ = rgba.split()
        if ImageChops.difference(red, green).getbbox() or ImageChops.difference(red, blue).getbbox():
            raise ValueError("The displayed processed PNG is not the grayscale Shared RGB buffer")
        return red.copy()


def draw_processed(canvas: Image.Image, source: Image.Image, slot: tuple[int, int, int, int]) -> dict[str, Any]:
    left, top, right, bottom = contain_rectangle(source.size, slot)
    width, height = right - left, bottom - top
    result: dict[str, Any] = {"source_size": list(source.size), "slot": list(slot), "destination": [left, top, right, bottom]}
    if width <= 0 or height <= 0:
        result["empty_after_rounding"] = True
        return result
    if left < 0 or top < 0 or right > canvas.width or bottom > canvas.height:
        raise ValueError("The oracle does not approximate clipped image geometry")
    dx8 = max(1, (source.width << 8) // width)
    dy8 = max(1, (source.height << 8) // height)
    xs = [(x * dx8) >> 8 for x in range(width)]
    ys = [(y * dy8) >> 8 for y in range(height)]
    if max(xs) >= source.width or max(ys) >= source.height:
        raise ValueError("The fixed-point sample exceeds the source; this geometry needs an explicit sampler review")
    data = source.tobytes()
    target = bytearray(width * height)
    previous_y = -1
    row = b""
    for y, source_y in enumerate(ys):
        if source_y != previous_y:
            source_row = data[source_y * source.width:(source_y + 1) * source.width]
            if dx8 % 256 == 0:
                row = source_row[0:width * (dx8 >> 8):dx8 >> 8]
            else:
                row = bytes(source_row[index] for index in xs)
            previous_y = source_y
        target[y * width:(y + 1) * width] = row
    image = Image.frombytes("L", (width, height), bytes(target))
    canvas.paste(image, (left, top))
    result.update({"dx8": dx8, "dy8": dy8, "sample_origin": [0, 0], "sample_center_offset": 0})
    return result


def difference_metrics(actual: Image.Image, expected: Image.Image) -> tuple[dict[str, Any], Image.Image]:
    difference = ImageChops.difference(actual.convert("RGB"), expected.convert("RGB"))
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = maximum.histogram()
    changed = actual.width * actual.height - histogram[0]
    band = min(96, actual.height)
    rois = {
        "top_96": (0, 0, actual.width, band),
        "bottom_96": (0, actual.height - band, actual.width, actual.height),
        "left_32": (0, 0, min(32, actual.width), actual.height),
        "right_32": (max(0, actual.width - 32), 0, actual.width, actual.height),
        "center_seam_8": (max(0, actual.width // 2 - 4), 0, min(actual.width, actual.width // 2 + 4), actual.height),
    }
    regions = {}
    for name, rectangle in rois.items():
        crop = maximum.crop(rectangle)
        regions[name] = {"rectangle": list(rectangle), "changed_pixels": crop.width * crop.height - crop.histogram()[0]}
    return {
        "exact_match": changed == 0,
        "changed_pixels": changed,
        "maximum_channel_difference": maximum.getextrema()[1],
        "rgb_mae_diagnostic_only": sum(ImageStat.Stat(difference).mean) / 3,
        "difference_bbox": list(maximum.getbbox()) if maximum.getbbox() else None,
        "regions_diagnostic_only": regions,
        "pixel_error_tolerance": 0,
        "ignored_pixels": 0,
    }, difference


def check(case: dict[str, Any], description: str, passed: bool, actual: Any = None) -> None:
    case["checks"].append({"description": description, "passed": bool(passed), "actual": actual})
    if not passed:
        case["passed"] = False
        print(f"FAIL {case['name']}: {description}; actual={actual!r}", flush=True)


def seed_preferences(directory: Path, mode: dict[str, Any], footer: int) -> None:
    database = directory / "demo" / "library.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=False)
    preferences = {
        "reading_direction": mode["direction"], "landscape": mode["landscape"],
        "spread": mode["spread"], "footer_mode": footer, "night_mode": False,
    }
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE preferences (key TEXT PRIMARY KEY, document TEXT NOT NULL)")
        connection.execute("INSERT INTO preferences(key,document) VALUES('application',?)", (json.dumps(preferences),))


def completed_process(command: list[str], environment: dict[str, str], timeout: int) -> tuple[int, bytes]:
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            output, _ = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate(timeout=3)
        return -1, output + b"\nThe isolated capture exceeded its timeout.\n"
    return process.returncode, output


class Suite:
    def __init__(self, options: argparse.Namespace):
        self.options = options
        self.binary = options.binary.resolve(strict=True)
        self.output = options.output_dir.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.run_id = uuid.uuid4().hex[:12]
        self.data_root = self.output / "data" / self.run_id
        self.data_root.mkdir(parents=True, exist_ok=False)
        self.binary_sha256 = sha256_file(self.binary)
        if options.expected_sha256 and self.binary_sha256 != options.expected_sha256:
            raise ValueError(f"The binary does not match --expected-sha256: {self.binary_sha256}")
        self.cases: list[dict[str, Any]] = []
        self.baselines: dict[str, dict[str, Any]] = {}
        self.started = time.monotonic()

    def assert_binary_unchanged(self) -> None:
        if sha256_file(self.binary) != self.binary_sha256:
            raise ValueError("The frozen binary changed during fullscreen verification")

    def run_case(self, mode_name: str, variant: str, prerequisite: bool = False) -> dict[str, Any]:
        self.assert_binary_unchanged()
        mode = MODES[mode_name]
        name = f"{mode_name}-{variant}"
        directory = self.data_root / name
        footer = int(variant[-1]) if variant.startswith("footer-") else 0
        seed_preferences(directory, mode, footer)
        runtime = directory / "xdg-runtime"
        runtime.mkdir(mode=0o700)
        screenshot = self.output / f"{name}.png"
        actions = [{"at_ms": 2500, "section": "app", "action": "dismiss", "value": ""}]
        baseline = self.baselines.get(mode_name)
        capture_ms = 5000 if mode["device"] == "pw5" else 6000
        if variant == "bookmark-toggle":
            bookmark_ms = 5500 if mode["device"] == "scribe" else 3000
            if mode["device"] == "scribe":
                capture_ms = max(capture_ms, 7500)
            actions.append({"at_ms": bookmark_ms, "section": "reader", "action": "bookmark", "value": ""})
        elif variant == "background-notification":
            if baseline is None:
                raise ValueError("The notification case requires its mode baseline")
            database = Path(baseline["state"]["database"])
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
                chapters = [json.loads(row[0]) for row in connection.execute("SELECT document FROM chapters WHERE book_id=?", (baseline["state"]["current_book"],))]
            candidates = [chapter for chapter in chapters if not chapter.get("downloaded")]
            if not candidates:
                raise ValueError("The demo fixture has no uncached chapter for a real background queue notification")
            target = max(candidates, key=lambda chapter: chapter["number"])["id"]
            actions.append({"at_ms": 3100, "section": "detail", "action": "download", "value": target})
        elif variant == "last-page":
            if baseline is None:
                raise ValueError("The last-page case requires its mode baseline")
            actions.append({"at_ms": 500, "section": "reader", "action": "seek", "value": str(baseline["state"]["reader_total"])})
            capture_ms = capture_ms * 2 + 1000
            actions.sort(key=lambda action: action["at_ms"])
        action_path = self.output / "actions" / f"{name}.json"
        save_json(action_path, actions)
        command = ["xvfb-run", "-a", "-s", "-screen 0 2200x3000x24", str(self.binary), "--demo", "--offline", "--device", mode["device"], "--screen", "reader", "--data-dir", str(directory), "--actions", str(action_path), "--capture", str(screenshot), "--exit-after-ms", str(capture_ms)]
        if mode["landscape"]:
            command.append("--landscape")
        environment = os.environ.copy()
        environment.update({"SLINT_SCALE_FACTOR": "1", "SLINT_BACKEND": "winit-software", "SLINT_RENDERER": "software", "XDG_RUNTIME_DIR": str(runtime), "TZ": "Asia/Shanghai", "RUST_BACKTRACE": "1"})
        case: dict[str, Any] = {"name": name, "mode": mode_name, "variant": variant, "prerequisite": prerequisite, "passed": True, "checks": [], "command": command, "capture_ms": capture_ms, "data_dir": str(directory), "screenshot": str(screenshot), "actions": str(action_path), "state": {}, "metadata": {}}
        self.cases.append(case)
        started = time.monotonic()
        print(f"RUN {name}", flush=True)
        try:
            exit_code, log = completed_process(command, environment, self.options.timeout)
            (self.output / f"{name}.log").write_bytes(log)
            check(case, "The application capture exited successfully", exit_code == 0, exit_code)
            for kind in ("state", "metadata"):
                path = screenshot.with_suffix(f".{kind}.json")
                case[kind] = json.loads(path.read_text(encoding="utf-8"))
            self.inspect_case(case, mode, baseline)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            check(case, "The capture and oracle completed without a contract error", False, f"{type(error).__name__}: {error}")
        self.assert_binary_unchanged()
        case["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_json(self.output / f"{name}.case.json", case)
        print(f"{'PASS' if case['passed'] else 'FAIL'} {name} ({case['elapsed_seconds']}s)", flush=True)
        if variant == "default":
            self.baselines[mode_name] = case
        return case

    def inspect_case(self, case: dict[str, Any], mode: dict[str, Any], baseline: dict[str, Any] | None) -> None:
        state = case["state"]
        metadata = case["metadata"]
        variant = case["variant"]
        check(case, "The reader is closed by default and has no modal overlay", state.get("route") == "reader" and state.get("menu") is False and state.get("modal") == "", {key: state.get(key) for key in ("route", "menu", "modal")})
        check(case, "The displayed reader is fully committed at capture", state.get("reader_inflight") is False and state.get("reader_dirty") is False and state.get("busy") is False, {key: state.get(key) for key in ("reader_inflight", "reader_dirty", "busy")})
        effective = state.get("reader_effective")
        if not isinstance(effective, dict):
            raise ValueError("The snapshot must expose the full reader_effective preferences")
        check(case, "Effective direction and orientation match this mode", effective.get("reading_direction") == mode["direction"] and effective.get("landscape") is mode["landscape"], effective)
        expected_footer = int(variant[-1]) if variant.startswith("footer-") else 0
        check(case, "The requested footer mode is active", effective.get("footer_mode") == expected_footer and state.get("preferences", {}).get("footer_mode") == expected_footer, effective.get("footer_mode"))
        check(case, "The capture uses the opaque normal desktop software viewport", metadata.get("profile") == mode["device"] and metadata.get("backend") == "desktop" and metadata.get("scale_factor") == 1 and all(metadata.get(field) == mode["size"] for field in ("logical_size", "window_size", "snapshot_size")), metadata)
        if effective.get("night_mode") is not False:
            raise ValueError("This oracle matrix requires normal white-paper rendering")
        displayed = state.get("displayed_reader")
        if not isinstance(displayed, dict) or not isinstance(displayed.get("session"), dict):
            raise ValueError("The snapshot must expose displayed_reader with its actual committed session")
        session = displayed["session"]
        matching = all(session.get(session_key) == state.get(state_key) for session_key, state_key in (("book_id", "current_book"), ("chapter_id", "current_chapter"), ("page", "reader_page"), ("total", "reader_total"), ("vertical_offset", "reader_offset"), ("zoom", "reader_zoom"))) and session.get("landscape") is mode["landscape"]
        check(case, "Displayed processed paths belong to the current reader session", matching, session)
        dual = mode["dual"] and variant != "last-page"
        check(case, "The committed second image matches the required one- or two-slot layout", bool(displayed.get("second_path")) == dual, displayed.get("second_path"))
        width, height = mode["size"]
        expected = Image.new("L", (width, height), 255)
        input_directory = self.output / "processed" / case["name"]
        input_directory.mkdir(parents=True, exist_ok=True)
        inputs = []
        slots = [(0, 0, width, height)]
        if dual:
            first_x = width // 2 if mode["direction"] == "rtl" else 0
            second_x = 0 if mode["direction"] == "rtl" else width // 2
            slots = [(first_x, 0, width // 2, height), (second_x, 0, width // 2, height)]
        for index, slot in enumerate(slots):
            field = "first_path" if index == 0 else "second_path"
            path = Path(displayed[field])
            if not path.is_absolute():
                raise ValueError("Displayed processed PNG paths must be absolute")
            raw = path.read_bytes()
            copied = input_directory / f"{field}.png"
            copied.write_bytes(raw)
            source = opaque_grayscale_png(raw)
            geometry = draw_processed(expected, source, slot)
            inputs.append({"field": field, "application_path": str(path), "retained_path": str(copied), "sha256": sha256_bytes(raw), "geometry": geometry})
        case["processed_inputs"] = inputs
        expected_path = self.output / f"{case['name']}.expected.png"
        expected.save(expected_path)
        with Image.open(case["screenshot"]) as capture:
            if capture.format != "PNG" or capture.size != (width, height):
                raise ValueError("The application PNG has unexpected viewport geometry")
            actual = capture.convert("RGBA")
            check(case, "Every actual viewport pixel is opaque", actual.getchannel("A").getextrema() == (255, 255), actual.getchannel("A").getextrema())
            metrics, difference = difference_metrics(actual, expected)
            difference_path = self.output / f"{case['name']}.diff.png"
            difference.save(difference_path)
            case["pixels"] = metrics
            case["expected"] = str(expected_path)
            case["difference"] = str(difference_path)
            check(case, "Every viewport pixel equals the Slint point-sampled processed image composition", metrics["exact_match"], metrics)
            if baseline is not None and variant != "last-page":
                with Image.open(baseline["screenshot"]) as prior:
                    invariance, invariant_difference = difference_metrics(actual, prior)
                case["closed_pixel_invariance"] = invariance
                invariant_difference.save(self.output / f"{case['name']}.invariance-diff.png")
                check(case, "Closed pixels remain identical across bookmark, footer, or background-message state", invariance["exact_match"], invariance)
                check(case, "The mutation does not change the reader page or vertical offset", all(state.get(key) == baseline["state"].get(key) for key in ("current_book", "current_chapter", "reader_page", "reader_offset")), {key: state.get(key) for key in ("current_chapter", "reader_page", "reader_offset")})
        if variant == "bookmark-toggle":
            check(case, "The bookmark state actually changed", baseline is not None and isinstance(state.get("reader_bookmarked"), bool) and state["reader_bookmarked"] != baseline["state"].get("reader_bookmarked"), state.get("reader_bookmarked"))
            if mode["device"] == "scribe":
                rejection = b"aidoku: displayed page action:"
                rejected = rejection in (self.output / f"{case['name']}.log").read_bytes()
                case["displayed_action_rejected"] = rejected
                check(case, "The Scribe bookmark action has no displayed-frame guard rejection in its log", not rejected, {"rejection_prefix": rejection.decode("ascii"), "found": rejected})
        if variant == "background-notification":
            chapter = next(action["value"] for action in json.loads(Path(case["actions"]).read_text()) if action["section"] == "detail")
            jobs = [job for job in state.get("downloads", []) if job.get("chapter_id") == chapter]
            check(case, "A real offline worker accepted the uncached chapter without fictional HTTP work", len(jobs) == 1 and jobs[0].get("state") == "queued", jobs)
            check(case, "The background download notification is still present at capture", isinstance(state.get("message"), str) and bool(state["message"]), state.get("message"))
        else:
            check(case, "No transient notification is used to establish the default closed frame", state.get("message") == "", state.get("message"))
        if variant == "last-page":
            check(case, "The final reader page uses a single full-viewport image", state.get("reader_page") == state.get("reader_total") and displayed.get("second_path") is None, {key: state.get(key) for key in ("reader_page", "reader_total")})

    def finish(self, requested: list[str]) -> int:
        after = sha256_file(self.binary)
        unchanged = after == self.binary_sha256
        if not unchanged:
            self.cases.append({"name": "binary-immutability", "passed": False, "checks": [{"description": "The binary hash remains unchanged throughout verification", "passed": False, "actual": after}]})
        failures = [case["name"] for case in self.cases if not case["passed"]]
        unexecuted = sorted(set(requested) - {case["name"] for case in self.cases})
        report = {"schema_version": 1, "kind": "remote-closed-reader-pixel-oracle", "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "host": platform.node(), "run_id": self.run_id, "binary": str(self.binary), "binary_sha256": self.binary_sha256, "binary_sha256_after": after, "binary_unchanged": unchanged, "sampler_contract": SAMPLER_CONTRACT, "sampler_sources": SAMPLER_SOURCES, "requested_case_ids": requested, "unexecuted_case_ids": unexecuted, "coverage_complete": not unexecuted, "success": not failures and not unexecuted and unchanged, "passed": len(self.cases) - len(failures), "failed": len(failures), "failed_cases": failures, "elapsed_seconds": round(time.monotonic() - self.started, 3), "pixel_error_tolerance": 0, "ignored_pixels": 0, "cases": self.cases, "evidence_scope": "Closed desktop software rendering equals the actual committed processed PNG composition across the entire viewport. Letterboxing is determined only by contain fitting. This report does not determine manual design clean rounds."}
        save_json(self.output / "report.json", report)
        lines = ["# Closed reader fullscreen pixel oracle", "", f"- Binary SHA-256: `{self.binary_sha256}`", f"- Sampler: `{SAMPLER_CONTRACT}`", f"- Passed: {report['passed']}", f"- Failed: {report['failed']}", "- Pixel tolerance: 0. Ignored viewport pixels: 0.", "", "| Case | Result | Actual | Expected | Difference |", "| --- | --- | --- | --- | --- |"]
        for case in self.cases:
            name = case["name"]
            lines.append(f"| {name} | {'PASS' if case['passed'] else 'FAIL'} | [PNG]({name}.png) | [PNG]({name}.expected.png) | [PNG]({name}.diff.png) |")
        lines.extend(["", "The oracle uses f32 contain geometry, rounds absolute rectangle endpoints, then point-samples the original processed RGB buffer with dx8/dy8. Diagnostic edge and seam regions never exclude pixels from acceptance.", "", report["evidence_scope"], ""])
        (self.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"Fullscreen oracle: {report['passed']} passed, {report['failed']} failed. Report: {self.output / 'report.json'}", flush=True)
        return 0 if report["success"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True, help="Newly frozen desktop application executable on the remote worker")
    parser.add_argument("--output-dir", type=Path, required=True, help="Retain captures, processed PNG copies, expected images, differences, logs, and reports")
    parser.add_argument("--expected-sha256", help="Require this exact binary SHA-256; otherwise freeze its initial hash")
    parser.add_argument("--case", dest="selected_cases", choices=CASE_IDS, action="append", help="Run only the selected case, plus its required default baseline")
    parser.add_argument("--timeout", type=int, default=90, help="Timeout for each isolated application capture in seconds")
    options = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run only on the designated remote Linux worker after execution is authorized")
    if not options.binary.is_file() or not os.access(options.binary, os.X_OK):
        parser.error("--binary must identify an executable desktop build")
    if shutil.which("xvfb-run") is None:
        parser.error("The remote worker requires xvfb-run")
    requested = list(dict.fromkeys(options.selected_cases or CASE_IDS))
    suite = Suite(options)
    try:
        for mode_name in MODES:
            variants = [variant for variant in (*VARIANTS, "last-page") if f"{mode_name}-{variant}" in requested]
            if not variants:
                continue
            suite.run_case(mode_name, "default", prerequisite="default" not in variants)
            if not suite.baselines[mode_name]["passed"]:
                break
            for variant in variants:
                if variant != "default":
                    suite.run_case(mode_name, variant)
    except (OSError, ValueError, sqlite3.Error) as error:
        suite.cases.append({"name": "suite-contract", "passed": False, "checks": [{"description": "The frozen suite contract remained valid", "passed": False, "actual": f"{type(error).__name__}: {error}"}]})
    return suite.finish(requested)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"Fullscreen oracle failed: {error}", file=sys.stderr, flush=True)
        sys.exit(2)
