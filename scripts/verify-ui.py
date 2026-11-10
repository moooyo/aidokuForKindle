#!/usr/bin/env python3
"""Run isolated desktop E2E checks on the designated remote Linux worker.

This harness exercises real Slint hit areas as well as deterministic application
intents. PNGs and their application-generated state/geometry sidecars are retained
for manual design review. Passing this harness does not certify physical e-ink
waveforms, power use, suspend/resume, or a particular Kindle firmware.
"""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageChops, ImageDraw


PROFILES = {
    "pw5": (618, 824),
    "pw6": (632, 840),
    "scribe": (930, 1240),
    "scribe2": (930, 1240),
    "scribe3": (990, 1320),
    "scribe3-nofrontlight": (990, 1320),
}
SCREENS = ("library", "detail", "reader", "sources", "catalog", "search", "history", "settings")
CHAPTER_CASES = (
    "detail-chapter-hold-600", "detail-chapter-select-two", "detail-chapter-select-all",
    "detail-chapter-cancel", "detail-chapter-download-selected",
)
BATCH_CASES = ("batch-next-five", "batch-next-ten", "batch-unread", "batch-all")
DOWNLOAD_CASES = ("downloads-lazy-pagination", "downloads-pause-all", "downloads-resume-all", "downloads-cancel-all")
CACHE_CASES = ("cover-offline-restart", "storage-cache-info", "storage-clear-cache")
REVIEW_CASES = (
    "scribe-chapter-select-two", "summary-book-tags", "summary-cache-info-tagged", "summary-about-tagged",
    "downloads-disabled-global", "completed-job-menu", "completed-job-read",
    "completed-remove-confirmation", "completed-remove-cancel", "completed-remove-apply",
    "downloads-failed-controls",
)
FEEDBACK_CASES = ("batch-last-chapter-empty", "batch-completed-only", "batch-empty-selected", "status-strip")
SESSION_CASES = (
    "session-library-restore", "session-detail-restore", "session-reader-restore", "session-landscape-restore", "session-cross-book-detail",
    "session-vertical-restore", "session-secrets", "session-incognito-ram", "lifecycle-retained-reader",
    "lifecycle-pending-direction", "lifecycle-pending-rotation", "lifecycle-doubletap-reset", "lifecycle-seek-reset",
    "demo-assets-upgrade",
)
SELECTABLE_CASES = CHAPTER_CASES + BATCH_CASES + DOWNLOAD_CASES + CACHE_CASES + REVIEW_CASES + FEEDBACK_CASES + SESSION_CASES + (
    "reader-long-press-600", "reader-long-press-650", "reader-long-press-800",
    "baseline-large-readers", "local-cbz-roundtrip", "reader-bottom-right-vertical",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def ui_label(key: str) -> str:
    labels = json.loads((Path(__file__).resolve().parent.parent / "assets/i18n/zh-CN.json").read_text(encoding="utf-8"))
    return labels[key]


class Replay:
    """Keep press/release events separated across the application's 50 ms poll."""

    def __init__(self, start: int = 450):
        self.events: list[dict[str, Any]] = []
        self.at = start

    def event(self, section: str, action: str, value: str = "", delay: int = 0) -> "Replay":
        self.at += delay
        self.events.append({"at_ms": self.at, "section": section, "action": action, "value": str(value)})
        return self

    def intent(self, section: str, action: str, value: str = "", wait: int = 450) -> "Replay":
        self.event(section, action, value)
        self.at += wait
        return self

    def tap(self, x: float, y: float, wait: int = 450) -> "Replay":
        position = f"{x:g},{y:g}"
        self.event("pointer", "move", position)
        self.event("pointer", "press", position, 50)
        self.event("pointer", "release", position, 100)
        self.at += wait
        return self

    def swipe(self, x1: float, y1: float, x2: float, y2: float) -> "Replay":
        self.event("pointer", "move", f"{x1:g},{y1:g}")
        self.event("pointer", "press", f"{x1:g},{y1:g}", 50)
        self.event("pointer", "move", f"{(x1+x2)/2:g},{(y1+y2)/2:g}", 100)
        self.event("pointer", "move", f"{x2:g},{y2:g}", 100)
        self.event("pointer", "release", f"{x2:g},{y2:g}", 100)
        self.at += 500
        return self

    def hold(self, x: float, y: float, duration: int = 650) -> "Replay":
        position = f"{x:g},{y:g}"
        self.event("pointer", "move", position)
        self.event("pointer", "press", position, 50)
        self.event("pointer", "release", position, duration)
        self.at += 400
        return self


class MockKomga:
    """Serve a real two-page Komga API on the remote worker's loopback only."""

    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        cover = Image.new("L", (192, 256), 255)
        drawing = ImageDraw.Draw(cover)
        drawing.rectangle((8, 8, 183, 247), outline=0, width=4)
        drawing.rectangle((24, 40, 167, 130), fill=0)
        encoded = io.BytesIO()
        cover.save(encoded, format="PNG")
        self.cover = encoded.getvalue()
        self.covers: dict[str, bytes] = {}
        for index in range(40):
            unique = cover.copy()
            marker = ImageDraw.Draw(unique)
            marker.rectangle((24, 136, 167, 224), fill=255)
            marker.rectangle((24, 136, 167, 140 + index * 2), fill=0)
            encoded = io.BytesIO()
            unique.save(encoded, format="PNG")
            self.covers[f"series-{index:03}"] = encoded.getvalue()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_: Any) -> None:
                pass

            def respond(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                target = urlsplit(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                if target.path != "/api/v1/series/list" or not 0 <= length <= 65536:
                    self.respond(404, b"{}", "application/json")
                    return
                body = json.loads(self.rfile.read(length))
                page = int(parse_qs(target.query).get("page", ["0"])[0])
                authorized = self.headers.get("X-API-Key") == "fixture-api-key"
                owner.requests.append({"method": "POST", "page": page, "body": body, "authorized": authorized})
                if not authorized:
                    self.respond(401, b"{}", "application/json")
                    return
                rows = [
                    {"id": f"series-{index:03}", "name": f"Series {index:03}", "booksCount": 1,
                     "metadata": {"title": f"Series {index:03}", "summary": "Remote HTTP pagination fixture.", "status": "ONGOING", "genres": [], "tags": []},
                     "booksMetadata": {"authors": []}}
                    for index in range(page * 20, min((page + 1) * 20, 40))
                ]
                payload = {"content": rows, "number": page, "size": 20, "totalPages": 2, "totalElements": 40, "last": page >= 1}
                self.respond(200, json.dumps(payload).encode("utf-8"), "application/json")

            def do_GET(self) -> None:
                target = urlsplit(self.path)
                if target.path.startswith("/api/v1/series/") and target.path.endswith("/thumbnail"):
                    identifier = target.path.split("/")[-2]
                    self.respond(200, owner.covers.get(identifier, owner.cover), "image/png")
                else:
                    self.respond(404, b"{}", "application/json")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)

    def __enter__(self) -> "MockKomga":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"


class Suite:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.binary = args.binary.resolve()
        self.output = args.output_dir.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.run_id = uuid.uuid4().hex[:12]
        self.data_root = self.output / "data" / self.run_id
        self.data_root.mkdir(parents=True)
        self.runtime_dir = self.data_root / "xdg-runtime"
        self.runtime_dir.mkdir(mode=0o700)
        self.binary_stat = (self.binary.stat().st_size, self.binary.stat().st_mtime_ns)
        self.binary_hash = sha256(self.binary)
        self.cases: list[dict[str, Any]] = []
        self.reference_cases: dict[str, dict[str, Any]] = {}
        self.started = time.monotonic()
        self.process_cleanup_failed = False
        self.timing_process_runner = None

    def fresh(self, name: str) -> Path:
        directory = self.data_root / name
        directory.mkdir(parents=True, exist_ok=False)
        return directory

    def check(self, case: dict[str, Any], description: str, passed: bool, actual: Any = None) -> None:
        case["checks"].append({"description": description, "passed": bool(passed), "actual": actual})
        if not passed:
            case["passed"] = False
            print(f"  FAIL {case['name']}: {description}; actual={actual!r}", flush=True)

    def expect(self, case: dict[str, Any], field: str, expected: Any) -> None:
        actual = case.get("state", {}).get(field)
        self.check(case, f"Application state {field} equals {expected!r}", actual == expected, actual)

    def preference(self, case: dict[str, Any], key: str, expected: Any) -> None:
        actual = case.get("state", {}).get("preferences", {}).get(key)
        self.check(case, f"Persisted preference {key} equals {expected!r}", actual == expected, actual)

    def run(
        self,
        name: str,
        screen: str = "library",
        *,
        device: str = "pw5",
        landscape: bool = False,
        expected_landscape: bool | None = None,
        demo: bool = True,
        offline: bool = True,
        resume: bool = False,
        replay: Replay | None = None,
        data: Path | None = None,
        import_path: Path | None = None,
        expected_route: str | None = None,
        capture_delay: int = 650,
        allow_busy: bool = False,
    ) -> dict[str, Any]:
        if self.process_cleanup_failed:
            raise RuntimeError("An owned process group was not stopped; abort subsequent UI scenarios")
        if (self.binary.stat().st_size, self.binary.stat().st_mtime_ns) != self.binary_stat:
            raise RuntimeError("The binary changed during verification; rerun against an immutable build.")
        data = data or self.fresh(name)
        screenshot = self.output / f"{name}.png"
        action_path = self.output / "actions" / f"{name}.json"
        save_json(action_path, replay.events if replay else [])
        deadline = max(1300, (replay.at if replay else 450) + capture_delay)
        if screen == "reader" or expected_route == "reader":
            # Cold image decode and processed-cache writes continue after the UI
            # becomes interactive. Input timestamps remain unchanged; capture only
            # after enough idle time for the requested image to be committed.
            deadline = max(deadline, 5000 if device.startswith("scribe") else 4000, (replay.at if replay else 450) + 1600)
        command = [
            "xvfb-run", "-a", "-s", "-screen 0 2200x3000x24",
            str(self.binary), "--device", device, "--screen", screen,
            "--data-dir", str(data), "--capture", str(screenshot),
            "--actions", str(action_path), "--exit-after-ms", str(deadline),
        ]
        if demo:
            command.append("--demo")
        if offline:
            command.append("--offline")
        if resume:
            command.append("--resume")
        if landscape:
            command.append("--landscape")
        if import_path is not None:
            command.extend(["--import", str(import_path)])
        performance_trace = None
        if getattr(self.args, "performance_traces", False):
            performance_trace = self.output / f"{name}.performance.json"
            command.extend(["--performance-trace", str(performance_trace)])
        environment = os.environ.copy()
        environment.update({
            "SLINT_SCALE_FACTOR": "1", "SLINT_BACKEND": "winit-software",
            "SLINT_RENDERER": "software", "RUST_BACKTRACE": "1",
            "XDG_RUNTIME_DIR": str(self.runtime_dir), "TZ": "Asia/Shanghai",
        })
        case: dict[str, Any] = {
            "name": name, "device": device, "screen": screen, "initial_landscape": landscape,
            "landscape": landscape if expected_landscape is None else expected_landscape,
            "passed": True, "checks": [], "screenshot": str(screenshot),
            "data_dir": str(data), "actions": str(action_path), "pointer_events": sum(event["section"] == "pointer" for event in (replay.events if replay else [])),
            "exit_after_ms": deadline,
            "intentional_inflight_capture": allow_busy,
            "state": {}, "metadata": {},
        }
        if performance_trace is not None:
            case["performance_trace"] = str(performance_trace)
        self.cases.append(case)
        started = time.monotonic()
        print(f"RUN {name}", flush=True)
        try:
            # Keep diagnostics visible while the process runs. Recovery harnesses
            # synchronize isolated file restoration with actual failure events.
            if performance_trace is None:
                with (self.output / f"{name}.log").open("wb") as log:
                    result = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=self.args.timeout, check=False)
                exit_code = result.returncode
            else:
                if self.timing_process_runner is None:
                    path = Path(__file__).with_name("benchmark-reader-e2e.py")
                    specification = importlib.util.spec_from_file_location("reader_e2e_ui_process", path)
                    if specification is None or specification.loader is None:
                        raise RuntimeError("The adjacent bounded process runner could not be loaded")
                    self.timing_process_runner = importlib.util.module_from_spec(specification)
                    specification.loader.exec_module(self.timing_process_runner)
                try:
                    process = self.timing_process_runner.run_process(command, self.output / "processes" / name, self.args.timeout, self.output, environment)
                except self.timing_process_runner.ProcessGroupCleanupError:
                    self.process_cleanup_failed = True
                    raise
                case["process"] = process
                exit_code = process.get("exit_code")
                self.check(case, "The bounded application process and its owned group completed successfully", process.get("succeeded") is True, process)
            case["exit_code"] = exit_code
            self.check(case, "Application exited successfully", exit_code == 0, exit_code)
            for kind in ("metadata", "state"):
                path = screenshot.with_suffix(f".{kind}.json")
                self.check(case, f"Application generated {kind} sidecar", path.is_file(), str(path))
                if path.is_file():
                    case[kind] = json.loads(path.read_text(encoding="utf-8"))
            self.check(case, "Application generated a PNG capture", screenshot.is_file(), str(screenshot))
            if screenshot.is_file():
                self.inspect_png(case, screenshot)
            metadata = case["metadata"]
            width, height = PROFILES[device]
            if case["landscape"]:
                width, height = height, width
            self.check(case, "Metadata identifies the requested profile", metadata.get("profile") == device, metadata.get("profile"))
            self.check(case, "Metadata identifies the desktop software backend", metadata.get("backend") == "desktop", metadata.get("backend"))
            self.check(case, "Logical geometry preserves the device design dimensions", metadata.get("logical_size") == [width, height], metadata.get("logical_size"))
            self.check(case, "Metadata records a unit scale factor", metadata.get("scale_factor") == 1, metadata.get("scale_factor"))
            if expected_route is not None:
                self.expect(case, "route", expected_route)
            elif replay is None:
                self.expect(case, "route", screen)
            if case["state"]:
                self.check(case, "Page index is within the available page range", 1 <= case["state"].get("page", 0) <= case["state"].get("page_count", 0), [case["state"].get("page"), case["state"].get("page_count")])
                if not allow_busy:
                    self.check(case, "No foreground worker remains unfinished at capture", case["state"].get("busy") is False, case["state"].get("busy"))
                self.check(case, "The isolated fixture uses the requested demo mode", case["state"].get("demo") is demo, case["state"].get("demo"))
                self.check(case, "The fixture uses the requested network mode", case["state"].get("offline") is offline, case["state"].get("offline"))
        except Exception as error:
            self.check(case, "The capture completed without a harness error", False, f"{type(error).__name__}: {error}")
        case["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(f"{'PASS' if case['passed'] else 'FAIL'} {name} ({case['elapsed_seconds']}s)", flush=True)
        return case

    def inspect_png(self, case: dict[str, Any], path: Path) -> None:
        with Image.open(path) as original:
            self.check(case, "Capture is encoded as PNG", original.format == "PNG", original.format)
            width, height = PROFILES[case["device"]]
            if case["landscape"]:
                width, height = height, width
            self.check(case, "PNG dimensions match the logical viewport", original.size == (width, height), list(original.size))
            self.check(case, "Window metadata records the actual PNG dimensions", case["metadata"].get("window_size") == list(original.size), case["metadata"].get("window_size"))
            self.check(case, "Snapshot metadata records the actual PNG dimensions", case["metadata"].get("snapshot_size") == list(original.size), case["metadata"].get("snapshot_size"))
            rgba = original.convert("RGBA")
            self.check(case, "Every captured pixel is opaque", rgba.getchannel("A").getextrema() == (255, 255), list(rgba.getchannel("A").getextrema()))
            red, green, blue, _ = rgba.split()
            channel_error = max(ImageChops.difference(red, green).getextrema()[1], ImageChops.difference(red, blue).getextrema()[1])
            self.check(case, "The e-ink interface has no color-channel tint", channel_error <= 2, channel_error)
            histogram = rgba.convert("L").histogram()
            dark = sum(histogram[:96])
            minimum_ink = 0 if case.get("intentional_inflight_capture") else width * height * 0.002
            self.check(case, "The screenshot contains visible interface content", dark > minimum_ink, dark)
            case["image"] = {"size": [width, height], "distinct_gray_levels": sum(count > 0 for count in histogram), "dark_fraction": round(dark / (width * height), 6)}

    def database_rows(self, case: dict[str, Any], query: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        database = case.get("state", {}).get("database")
        if not database:
            self.check(case, "The capture exposes its isolated database path", False)
            return []
        try:
            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
                return connection.execute(query, parameters).fetchall()
        except sqlite3.Error as error:
            self.check(case, "The isolated database can be inspected read-only", False, str(error))
            return []

    def primary_jobs(self, case: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {job["chapter_id"]: job for job in case.get("state", {}).get("downloads", []) if job.get("book_id") == "8:mangadex:demo-00"}

    def expect_job_numbers(self, case: dict[str, Any], expected: set[int]) -> None:
        jobs = self.primary_jobs(case)
        actual = {int(identifier.rsplit("-", 1)[-1]) for identifier in jobs}
        self.check(case, "The queue contains exactly the requested distinct chapter IDs", actual == expected and len(jobs) == len(expected), sorted(actual))

    def modal_rows(self, case: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows = case.get("state", {}).get("modal_items", [])
        self.check(case, "Snapshot exposes the actual visible modal control states", isinstance(rows, list) and all(isinstance(row, dict) and "id" in row and "enabled" in row for row in rows) and bool(rows), rows)
        return {row["id"]: row for row in rows if isinstance(row, dict) and "id" in row}

    def pager_bounds(self, case: dict[str, Any], side: str) -> list[int] | None:
        rectangle = (175, 630, 245, 690) if side == "previous" else (375, 630, 440, 690)
        try:
            with Image.open(case["screenshot"]) as image:
                region = image.convert("RGB").crop(rectangle)
                mask = Image.new("L", region.size)
                mask.putdata([255 if pixel == (238, 238, 238) else 0 for pixel in region.getdata()])
                bounds = mask.getbbox()
                return [bounds[0] + rectangle[0], bounds[1] + rectangle[1], bounds[2] + rectangle[0], bounds[3] + rectangle[1]] if bounds else None
        except OSError as error:
            self.check(case, "Pager position evidence can be read", False, str(error))
            return None

    def summary_tag_ink(self, case: dict[str, Any]) -> int:
        try:
            with Image.open(case["screenshot"]) as image:
                histogram = image.convert("L").crop((52, 552, 568, 590)).histogram()
                return sum(histogram[:160])
        except OSError as error:
            self.check(case, "Summary tag-strip evidence can be read", False, str(error))
            return -1

    def create_cbz(self, name: str) -> Path:
        archive = self.data_root / f"{name}.cbz"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for number in (10, 2, 1):
                image = Image.new("L", (600, 800), 255)
                draw = ImageDraw.Draw(image)
                draw.rectangle((30, 30, 570, 770), outline=0, width=6)
                draw.rectangle((70, 100, 530, 180 + number * 25), fill=0)
                draw.text((80, 620), f"OFFLINE PAGE {number}", fill=0)
                stream = io.BytesIO()
                image.save(stream, format="PNG")
                output.writestr(f"page{number}.png", stream.getvalue())
            output.writestr("ComicInfo.xml", "<ComicInfo><Series>Offline fixture</Series><Title>Chapter 1</Title><Number>1</Number></ComicInfo>")
        return archive

    def create_vertical_cbz(self, name: str) -> Path:
        archive = self.data_root / f"{name}.cbz"
        image = Image.new("L", (600, 3600), 255)
        draw = ImageDraw.Draw(image)
        draw.rectangle((8, 8, 591, 3591), outline=0, width=8)
        for index in range(12):
            top = index * 300 + 30
            draw.rectangle((30, top, 570, top + 180), fill=0 if index % 2 == 0 else 85)
            draw.text((40, top + 220), f"VERTICAL PANEL {index + 1}", fill=0)
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            output.writestr("page1.png", encoded.getvalue())
            output.writestr("ComicInfo.xml", "<ComicInfo><Series>Vertical fixture</Series><Title>Chapter 1</Title><Number>1</Number></ComicInfo>")
        return archive

    def navigation_session(self, case: dict[str, Any]) -> dict[str, Any]:
        rows = self.database_rows(case, "SELECT document FROM preferences WHERE key='navigation-session'")
        self.check(case, "A durable navigation checkpoint was written", len(rows) == 1, len(rows))
        return json.loads(rows[0][0]) if rows else {}

    def shots(self) -> None:
        for screen in SCREENS:
            replay = Replay().tap(309, 400) if screen == "reader" else None
            case = self.run(f"pw5-{screen}", screen, replay=replay, expected_route=screen)
            self.reference_cases[f"pw5-{screen}"] = case
            self.expect(case, "library_count", 46)
            if screen == "reader":
                self.expect(case, "reader_page", 12)
                self.expect(case, "reader_total", 40)
                self.expect(case, "menu", True)
            if screen == "library":
                self.expect(case, "page_count", 6)
        pure = self.run("pw5-reader-fullscreen", "reader")
        self.expect(pure, "menu", False)
        for screen in ("library", "detail", "sources"):
            self.reference_cases[f"scribe-{screen}"] = self.run(f"scribe-{screen}", screen, device="scribe")
        self.reference_cases["scribe-landscape-reader"] = self.run("scribe-landscape-reader", "reader", device="scribe", landscape=True, replay=Replay().tap(620, 400), expected_route="reader")
        self.run("pw6-library", "library", device="pw6")
        self.run("scribe2-library", "library", device="scribe2")
        self.run("scribe3-library", "library", device="scribe3")
        no_light = self.run("scribe3-nofrontlight-settings", "settings", device="scribe3-nofrontlight")
        self.preference(no_light, "brightness", 0)
        self.preference(no_light, "warmth", 0)
        if "has_frontlight" in no_light["state"]:
            self.expect(no_light, "has_frontlight", False)
        if "visible_setting_ids" in no_light["state"]:
            settings = no_light["state"]["visible_setting_ids"]
            self.check(no_light, "A device without front light has no lighting control", "frontlight-warmth" not in settings and "frontlight" not in settings, settings)
        self.run("scribe3-nofrontlight-reader", "reader", device="scribe3-nofrontlight")

    def pointer_navigation(self) -> None:
        for index, route in enumerate(("library", "sources", "search", "history", "settings")):
            start = "settings" if index == 0 else "library"
            self.run(f"pointer-navigation-{route}", start, replay=Replay().tap((index + 0.5) * 618 / 5, 789), expected_route=route)
        detail = self.run("pointer-library-detail", replay=Replay().tap(80, 380), expected_route="detail")
        self.check(detail, "The actual first cover selects a manga", bool(detail["state"].get("current_book")), detail["state"].get("current_book"))
        reader = self.run("pointer-detail-reader", replay=Replay().tap(80, 380).tap(220, 340), expected_route="reader")
        self.expect(reader, "reader_page", 12)
        self.expect(reader, "reader_total", 40)
        self.expect(reader, "menu", False)

    def reader_interactions(self) -> None:
        menu = self.run("reader-menu-blank-does-not-turn", "reader", replay=Replay().tap(309, 400).tap(80, 400), expected_route="reader")
        self.expect(menu, "reader_page", 12)
        self.expect(menu, "menu", False)
        page = self.run("reader-rtl-turn-and-reopen", "reader", replay=Replay().tap(80, 400).tap(309, 400), expected_route="reader")
        self.expect(page, "reader_page", 13)
        self.expect(page, "menu", True)
        corner = self.run("reader-bottom-right-rtl", "reader", replay=Replay().tap(570, 792), expected_route="reader")
        self.expect(corner, "reader_page", 11)
        self.expect(corner, "menu", False)
        self.preference(corner, "footer_mode", 0)
        ltr_corner = self.run("reader-bottom-right-ltr", "reader", replay=Replay().intent("settings", "set", "reading_direction=ltr").tap(570, 792), expected_route="reader")
        self.expect(ltr_corner, "reader_page", 13)
        self.preference(ltr_corner, "footer_mode", 0)
        vertical_corner = self.run("reader-bottom-right-vertical", "reader", replay=Replay().intent("settings", "set", "reading_direction=vertical", wait=4000).tap(570, 792, wait=4000), expected_route="reader")
        self.check(vertical_corner, "The bottom-right region advances vertical reading instead of changing a footer mode", vertical_corner["state"].get("reader_page", 0) > 12 or vertical_corner["state"].get("reader_offset", 0) > 0, {"page": vertical_corner["state"].get("reader_page"), "offset": vertical_corner["state"].get("reader_offset")})
        self.preference(vertical_corner, "footer_mode", 0)
        swipe = self.run("reader-rtl-swipe", "reader", replay=Replay().swipe(180, 420, 480, 420), expected_route="reader")
        self.expect(swipe, "reader_page", 13)
        left_to_right = self.run("reader-ltr-tap", "reader", replay=Replay().intent("settings", "set", "reading_direction=ltr").tap(540, 400), expected_route="reader")
        self.expect(left_to_right, "reader_page", 13)
        self.preference(left_to_right, "reading_direction", "ltr")
        held = self.run("reader-long-press", "reader", replay=Replay().hold(300, 410), expected_route="reader")
        self.expect(held, "reader_page", 12)
        self.expect(held, "modal", "choices")
        for duration in (600, 800):
            timed_hold = self.run(f"reader-long-press-{duration}", "reader", replay=Replay().hold(300, 410, duration=duration), expected_route="reader")
            self.expect(timed_hold, "reader_page", 12)
            self.expect(timed_hold, "modal", "choices")

        preview = Replay().tap(309, 400).event("pointer", "move", "430,694").event("pointer", "press", "430,694", 50).event("pointer", "move", "100,694", 100)
        preview.at += 200
        before_release = self.run("reader-seek-preview", "reader", replay=preview, expected_route="reader", capture_delay=250)
        self.expect(before_release, "reader_page", 12)
        commit = Replay().tap(309, 400).event("pointer", "move", "430,694").event("pointer", "press", "430,694", 50).event("pointer", "move", "100,694", 100).event("pointer", "release", "100,694", 100)
        commit.at += 500
        released = self.run("reader-seek-commit", "reader", replay=commit, expected_route="reader")
        self.expect(released, "reader_page", 35)
        self.expect(released, "menu", False)

        crossing = self.run("reader-next-chapter", "reader", replay=Replay().intent("reader", "seek", "40").tap(80, 400), expected_route="reader")
        self.expect(crossing, "reader_page", 1)
        self.check(crossing, "The final page crosses into the next chapter", str(crossing["state"].get("current_chapter", "")).endswith("chapter-035"), crossing["state"].get("current_chapter"))
        panel_closed = self.run("reader-panel-close-pure", "reader", replay=Replay().tap(309, 400).tap(590, 54).tap(10, 300), expected_route="reader")
        self.expect(panel_closed, "modal", "")
        self.expect(panel_closed, "menu", False)
        escaped = self.run("reader-escape-closes-menu", "reader", replay=Replay().tap(309, 400).intent("app", "back"), expected_route="reader")
        self.expect(escaped, "menu", False)
        self.run("reader-second-escape-leaves-reader", "reader", replay=Replay().tap(309, 400).intent("app", "back").intent("app", "back"), expected_route="detail")
        escaped_panel = self.run("reader-escape-panel-pure", "reader", replay=Replay().tap(309, 400).tap(590, 54).intent("app", "back"), expected_route="reader")
        self.expect(escaped_panel, "modal", "")
        self.expect(escaped_panel, "menu", False)
        toc = self.run("reader-toc-chapter-pure", "reader", replay=Replay().tap(309, 400).tap(590, 54).intent("panel", "choose", "chapter:8:mangadex:demo-00-chapter-035"), expected_route="reader")
        self.expect(toc, "menu", False)
        self.expect(toc, "modal", "")
        self.check(toc, "An explicitly chosen chapter returns directly to pure reading", str(toc["state"].get("current_chapter", "")).endswith("chapter-035"), toc["state"].get("current_chapter"))

    def persistence(self) -> None:
        bookmark_data = Path(self.reference_cases["pw5-reader"]["data_dir"])
        initial_bookmarks = self.reference_cases["pw5-reader"]["state"].get("bookmark_count", 0)
        cleared = self.run("bookmark-pointer-toggle", "reader", replay=Replay().tap(309, 400).tap(538, 54).tap(309, 400), data=bookmark_data, expected_route="reader")
        self.expect(cleared, "bookmark_count", initial_bookmarks - 1)
        restarted = self.run("bookmark-restart", "reader", data=bookmark_data)
        self.expect(restarted, "bookmark_count", initial_bookmarks - 1)

        settings_data = Path(self.reference_cases["pw5-settings"]["data_dir"])
        changed = self.run("settings-pointer-direction", "settings", replay=Replay().tap(200, 504).tap(308, 443), data=settings_data, expected_route="settings")
        self.preference(changed, "reading_direction", "ltr")
        self.expect(changed, "modal", "")
        persisted = self.run("settings-restart", "settings", data=settings_data)
        self.preference(persisted, "reading_direction", "ltr")

        history = self.run("incognito-pointer-enable", "history", replay=Replay().tap(498, 70), expected_route="history")
        self.preference(history, "incognito", True)
        rows_before = self.database_rows(history, "SELECT chapter_id,page,total,completed,updated_at FROM progress ORDER BY chapter_id")
        hidden = self.run("incognito-reader-history", "reader", replay=Replay().tap(80, 400), data=Path(history["data_dir"]), expected_route="reader")
        self.expect(hidden, "reader_page", 13)
        self.preference(hidden, "incognito", True)
        rows_after = self.database_rows(hidden, "SELECT chapter_id,page,total,completed,updated_at FROM progress ORDER BY chapter_id")
        self.check(hidden, "Incognito reading does not modify any persisted history row", rows_before == rows_after, {"before": rows_before, "after": rows_after})

    def lists_and_search(self) -> None:
        previous = self.run("pagination-first-boundary", replay=Replay().tap(210, 728), expected_route="library")
        self.expect(previous, "page", 1)
        next_page = self.run("pagination-pointer-next", replay=Replay().tap(405, 728), expected_route="library")
        self.expect(next_page, "page", 2)
        turns = Replay()
        for _ in range(7):
            turns.tap(405, 728, wait=250)
        last = self.run("pagination-last-boundary", replay=turns, expected_route="library")
        self.expect(last, "page", 6)
        self.expect(last, "page_count", 6)

        empty = self.run("empty-library", demo=False)
        self.expect(empty, "library_count", 0)
        self.expect(empty, "history_count", 0)
        zero = self.run("search-zero-results", "search", replay=Replay().intent("search", "query", "__no_matching_manga__"), expected_route="search")
        self.expect(zero, "query", "__no_matching_manga__")
        self.expect(zero, "page_count", 1)
        if "search_result_count" in zero["state"]:
            self.expect(zero, "search_result_count", 0)
        else:
            self.check(zero, "Snapshot exposes the search result count for a zero-result assertion", False, "missing search_result_count")
        clear = self.run("search-pointer-clear", "search", replay=Replay().tap(524, 62), expected_route="search")
        self.expect(clear, "query", "")
        cancel = self.run("search-pointer-cancel", "search", replay=Replay().tap(582, 62), expected_route="library")
        self.expect(cancel, "query", "")
        keyboard = self.run("search-pointer-keyboard", "search", replay=Replay().tap(524, 62).tap(200, 62).tap(162, 798).tap(73, 694).tap(568, 798), expected_route="search")
        self.expect(keyboard, "query", "a")
        self.expect(keyboard, "modal", "")

        history_data = Path(self.reference_cases["pw5-history"]["data_dir"])
        initial_history = self.reference_cases["pw5-history"]["state"].get("history_count", 0)
        deleted = self.run("history-pointer-delete", "history", replay=Replay().tap(568, 240), data=history_data, expected_route="history")
        self.expect(deleted, "history_count", initial_history - 1)
        catalog = self.run("sources-pointer-catalog", "sources", replay=Replay().tap(200, 510), expected_route="catalog")
        self.expect(catalog, "source_id", "mangadex")
        favorite = self.run("catalog-pointer-add-library", "catalog", replay=Replay().tap(190, 350).tap(524, 340), expected_route="detail")
        self.expect(favorite, "library_count", 47)

    def local_content(self) -> None:
        archive = self.data_root / "offline-fixture.cbz"
        pages: list[Path] = []
        for number in (10, 2, 1):
            image = Image.new("L", (600, 800), 255)
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 30, 570, 770), outline=0, width=6)
            draw.rectangle((70, 100, 530, 180 + number * 25), fill=0)
            draw.text((80, 620), f"OFFLINE PAGE {number}", fill=0)
            path = self.data_root / f"page{number}.png"
            image.save(path)
            pages.append(path)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for page in pages:
                output.write(page, page.name)
            output.writestr("ComicInfo.xml", "<ComicInfo><Series>Offline fixture</Series><Title>Chapter 1</Title><Number>1</Number></ComicInfo>")
        imported = self.run("local-cbz-import", demo=False, import_path=archive, capture_delay=1600, expected_route="library")
        self.expect(imported, "library_count", 1)
        page_rows = self.database_rows(imported, "SELECT document FROM pages ORDER BY position")
        self.check(imported, "The real CBZ import persisted three image pages", len(page_rows) == 3, len(page_rows))
        for row in page_rows:
            record = json.loads(row[0])
            path = record.get("local_path")
            self.check(imported, "Every imported page resolves to a real local file", bool(path) and Path(path).is_file(), path)
        data = Path(imported["data_dir"])
        read = self.run("local-cbz-reader", "reader", demo=False, data=data, replay=Replay().tap(80, 400), expected_route="reader")
        self.expect(read, "reader_total", 3)
        self.expect(read, "reader_page", 2)
        restart = self.run("local-cbz-restart", "reader", demo=False, data=data)
        self.expect(restart, "reader_total", 3)
        self.expect(restart, "reader_page", 2)

    def http_source_pagination(self) -> None:
        for search in (False, True):
            name = "http-search" if search else "http-browse"
            seed = self.run(f"{name}-seed", demo=False)
            database = seed["state"].get("database")
            if not database:
                continue
            data = Path(seed["data_dir"])
            with MockKomga() as provider:
                source = {
                    "id": "komga", "name": "Komga fixture", "kind": "komga", "base_url": provider.url,
                    "username": "", "password": "", "token": "fixture-api-key", "version": 1,
                    "languages": ["en"], "enabled": True, "options": {},
                }
                # Only this loopback source is enabled, so the online fixture cannot
                # accidentally contact MangaDex or another external provider.
                with sqlite3.connect(database) as connection:
                    connection.execute("DELETE FROM sources")
                    connection.execute("INSERT INTO sources(id,document) VALUES(?,?)", ("komga", json.dumps(source)))
                replay = Replay()
                if search:
                    replay.intent("search", "query", "paging", wait=1000).tap(546, 210, wait=700)
                else:
                    replay.intent("sources", "open", "komga", wait=1000)
                for _ in range(7):
                    replay.tap(405, 800, wait=650)
                case = self.run(
                    f"{name}-pagination", "search" if search else "sources", demo=False,
                    offline=False, data=data, replay=replay, expected_route="catalog", capture_delay=1600,
                )
                rows = self.database_rows(case, "SELECT remote_id FROM books WHERE source_id='komga' ORDER BY remote_id")
                self.check(case, "Actual next-page taps retrieve and persist forty distinct server entries", len(rows) == 40 and len(set(rows)) == 40, len(rows))
                self.expect(case, "page", 8)
                self.expect(case, "page_count", 8)
                pages = [request["page"] for request in provider.requests]
                self.check(case, "The provider receives exactly the first and second HTTP pages", pages == [0, 1], pages)
                self.check(case, "The configured provider API key reaches each HTTP request", all(request["authorized"] for request in provider.requests), [request["authorized"] for request in provider.requests])
                expected_query = "paging" if search else None
                queries = [request["body"].get("fullTextSearch") for request in provider.requests]
                self.check(case, "Search continuation retains its query instead of switching to browse", queries == [expected_query, expected_query], queries)
                if "catalog_count" in case["state"]:
                    self.expect(case, "catalog_count", 40)
                if "catalog_has_more" in case["state"]:
                    self.expect(case, "catalog_has_more", False)
                request_path = self.output / f"{name}-requests.json"
                save_json(request_path, provider.requests)
                case["provider_requests"] = str(request_path)

    def list_interactions(self) -> None:
        advanced = self.run("list-library-cover-swipe", replay=Replay().swipe(540, 380, 80, 380), expected_route="library")
        self.expect(advanced, "page", 2)
        self.expect(advanced, "selected", [])
        returned = self.run("list-library-cover-swipe-return", replay=Replay().swipe(540, 380, 80, 380).swipe(80, 380, 540, 380), expected_route="library")
        self.expect(returned, "page", 1)
        self.expect(returned, "selected", [])
        catalog = self.run("list-catalog-row-swipe", "catalog", replay=Replay().swipe(500, 245, 80, 245), expected_route="catalog")
        self.expect(catalog, "page", 2)
        self.expect(catalog, "selected", [])
        held = self.run("list-library-cover-hold", replay=Replay().hold(80, 380), expected_route="library")
        self.expect(held, "page", 1)
        self.expect(held, "selected", ["8:mangadex:demo-00"])

        sources = self.run("scribe-installable-pagination", "sources", device="scribe", replay=Replay().intent("sources", "filter", "1").tap(587, 1128), expected_route="sources")
        self.expect(sources, "page", 2)
        self.expect(sources, "page_count", 18)
        self.check(sources, "Available source pagination leaves the installed source count unchanged", self.database_rows(sources, "SELECT COUNT(*) FROM sources") == [(7,)], self.database_rows(sources, "SELECT COUNT(*) FROM sources"))
        installed = self.run("scribe-pointer-demo-install", "sources", device="scribe", replay=Replay().intent("sources", "filter", "1").tap(587, 1128).tap(854, 243), expected_route="sources")
        rows = self.database_rows(installed, "SELECT document FROM sources")
        records = [json.loads(row[0]) for row in rows]
        self.check(installed, "The actual install button adds exactly one demo source", len(records) == 8, len(records))
        simulated = [source for source in records if source.get("kind") == "demo"]
        self.check(installed, "Simulated installation is explicitly marked as demo rather than a real WASM package", len(simulated) == 1 and simulated[0].get("options", {}).get("demo") is True, [{"id": source.get("id"), "kind": source.get("kind"), "demo": source.get("options", {}).get("demo")} for source in simulated])

    def new_ui_cases(self, requested: set[str] | None = None) -> None:
        wanted = set(CHAPTER_CASES + BATCH_CASES + DOWNLOAD_CASES + CACHE_CASES + REVIEW_CASES + FEEDBACK_CASES) if requested is None else requested
        for name in CHAPTER_CASES:
            if name not in wanted:
                continue
            replay = Replay().hold(200, 461, duration=600)
            if name in ("detail-chapter-select-two", "detail-chapter-download-selected"):
                replay.tap(200, 506)
            elif name == "detail-chapter-select-all":
                replay.tap(388, 410)
            elif name == "detail-chapter-cancel":
                replay.tap(564, 410)
            if name == "detail-chapter-download-selected":
                replay.tap(476, 410, wait=800)
            case = self.run(name, "detail", replay=replay, expected_route="detail", capture_delay=1000)
            selected = set(case["state"].get("selected_chapters", []))
            expected = {
                "detail-chapter-hold-600": {"8:mangadex:demo-00-chapter-086"},
                "detail-chapter-select-two": {"8:mangadex:demo-00-chapter-086", "8:mangadex:demo-00-chapter-085"},
                "detail-chapter-select-all": {f"8:mangadex:demo-00-chapter-{number:03}" for number in range(1, 87)},
                "detail-chapter-cancel": set(),
                "detail-chapter-download-selected": set(),
            }[name]
            self.check(case, "Chapter selection matches the actual hold/tap/button interaction", selected == expected, sorted(selected))
            if name == "detail-chapter-download-selected":
                self.expect_job_numbers(case, set(range(27, 36)) | {85, 86})
                self.check(case, "Selected chapters are queued locally in the explicit offline demo", all(self.primary_jobs(case).get(f"8:mangadex:demo-00-chapter-{number:03}", {}).get("state") == "queued" for number in (85, 86)), {number: self.primary_jobs(case).get(f"8:mangadex:demo-00-chapter-{number:03}", {}).get("state") for number in (85, 86)})
            else:
                self.expect_job_numbers(case, set(range(27, 36)))

        for index, name in enumerate(BATCH_CASES):
            if name not in wanted:
                continue
            replay = Replay()
            # Both requests use the actual More > Batch download controls. The
            # second request proves chapter-identity deduplication at the UI boundary.
            for _ in range(2):
                replay.tap(572, 54).tap(309, 469).tap(309, 365 + index * 52, wait=800)
            case = self.run(name, "detail", replay=replay, expected_route="detail", capture_delay=1000)
            expected = {
                "batch-next-five": set(range(27, 41)),
                "batch-next-ten": set(range(27, 46)),
                "batch-unread": set(range(27, 87)),
                "batch-all": set(range(1, 87)),
            }[name]
            self.expect_job_numbers(case, expected)
            self.expect(case, "modal", "")
            self.check(case, "No fictional HTTP failure is generated by demo batch queueing", not any(job.get("state") == "failed" for job in self.primary_jobs(case).values()), [job.get("error") for job in self.primary_jobs(case).values() if job.get("state") == "failed"])
            jobs = self.primary_jobs(case)
            self.check(case, "Batch replay keeps every valid completed chapter completed", all(jobs.get(f"8:mangadex:demo-00-chapter-{number:03}", {}).get("state") == "completed" for number in range(27, 36)), {number: jobs.get(f"8:mangadex:demo-00-chapter-{number:03}", {}).get("state") for number in range(27, 36)})

        for name, row, expected_state in (
            ("downloads-pause-all", 0, "paused"),
            ("downloads-resume-all", 1, "queued"),
            ("downloads-cancel-all", 2, "cancelled"),
        ):
            if name not in wanted:
                continue
            replay = Replay().intent("detail", "select-chapter", "8:mangadex:demo-00-chapter-086").intent("detail", "select-chapter", "8:mangadex:demo-00-chapter-085").intent("detail", "download-selected", wait=800).intent("downloads", "open")
            if name == "downloads-resume-all":
                replay.tap(309, 233, wait=800)
            replay.tap(309, 233 + row * 52, wait=800)
            case = self.run(name, "detail", replay=replay, expected_route="detail", capture_delay=1200)
            self.expect(case, "modal", "downloads")
            self.expect_job_numbers(case, set(range(27, 36)) | {85, 86})
            jobs = self.primary_jobs(case)
            self.check(case, "The global operation changes every queued selected chapter", all(jobs.get(f"8:mangadex:demo-00-chapter-{number:03}", {}).get("state") == expected_state for number in (85, 86)), {number: jobs.get(f"8:mangadex:demo-00-chapter-{number:03}", {}).get("state") for number in (85, 86)})
            self.check(case, "Global controls preserve all previously completed downloads", all(jobs.get(f"8:mangadex:demo-00-chapter-{number:03}", {}).get("state") == "completed" for number in range(27, 36)), {number: jobs.get(f"8:mangadex:demo-00-chapter-{number:03}", {}).get("state") for number in range(27, 36)})

        if "downloads-lazy-pagination" in wanted:
            self.download_pagination()
        self.cache_ui_cases(wanted & set(CACHE_CASES))
        self.review_regressions(wanted & set(REVIEW_CASES))
        self.feedback_regressions(wanted & set(FEEDBACK_CASES))

    def download_pagination(self) -> None:
        replay = Replay().intent("detail", "batch-download").intent("panel", "choose", "batch:all", wait=1000).intent("downloads", "open", wait=700)
        first = self.run("downloads-lazy-pagination-01", "detail", replay=replay, expected_route="detail", capture_delay=1000)
        self.expect_job_numbers(first, set(range(1, 87)))
        expected_ids = set(self.primary_jobs(first))
        self.expect(first, "panel_page", 1)
        self.expect(first, "panel_page_count", 12)
        visible = first["state"].get("modal_item_ids", [])
        self.check(first, "Download page one has three controls and five jobs", len(visible) == 8 and visible[:3] == ["downloads-pause-all", "downloads-resume-all", "downloads-cancel-all"] and sum(identifier.startswith("job:") for identifier in visible) == 5, visible)
        first_pager = {side: self.pager_bounds(first, side) for side in ("previous", "next")}
        self.check(first, "The fixed-height multi-page panel keeps the expected pager hit areas", first_pager == {"previous": [187, 633, 235, 681], "next": [383, 633, 431, 681]}, first_pager)
        observed = {identifier.removeprefix("job:") for identifier in visible if identifier.startswith("job:")}
        data = Path(first["data_dir"])
        for page in range(2, 13):
            replay = Replay().intent("downloads", "open")
            if page == 2:
                replay.tap(407, 657)
            else:
                replay.intent("panel", "page", str(page - 1))
            case = self.run(f"downloads-lazy-pagination-{page:02}", "settings", data=data, replay=replay, expected_route="settings")
            self.expect(case, "modal", "downloads")
            self.expect(case, "panel_page", page)
            self.expect(case, "panel_page_count", 12)
            visible = case["state"].get("modal_item_ids", [])
            expected_rows = 8 if page < 12 else 1
            self.check(case, "Only this lazy page's task rows are materialized", len(visible) == expected_rows and all(identifier.startswith("job:") for identifier in visible), visible)
            current = {identifier.removeprefix("job:") for identifier in visible if identifier.startswith("job:")}
            self.check(case, "A task is not repeated on another download page", not (observed & current), sorted(observed & current))
            observed.update(current)
            self.expect_job_numbers(case, set(range(1, 87)))
            if page == 12:
                self.check(case, "Visiting every lazy page exposes every queued task exactly once", observed == expected_ids, {"expected": len(expected_ids), "observed": len(observed), "missing": sorted(expected_ids - observed)})
                last_pager = {side: self.pager_bounds(case, side) for side in ("previous", "next")}
                self.check(case, "The final sparse page has exactly the same pager coordinates as the first page", last_pager == first_pager, {"first": first_pager, "last": last_pager})

    def cache_ui_cases(self, wanted: set[str]) -> None:
        if not wanted:
            return
        seed = self.run("cache-cover-seed", demo=False)
        database = seed["state"].get("database")
        if not database:
            return
        data = Path(seed["data_dir"])
        with MockKomga() as provider:
            source = {"id": "komga", "name": "Komga fixture", "kind": "komga", "base_url": provider.url, "username": "", "password": "", "token": "fixture-api-key", "version": 1, "languages": ["en"], "enabled": True, "options": {}}
            with sqlite3.connect(database) as connection:
                connection.execute("DELETE FROM sources")
                connection.execute("INSERT INTO sources(id,document) VALUES(?,?)", ("komga", json.dumps(source)))
            online = self.run("cache-cover-online", "sources", demo=False, offline=False, data=data, replay=Replay().intent("sources", "open", "komga", wait=1600), expected_route="catalog", capture_delay=1600)
            self.check(online, "Online catalog fetches real cover images into the cache", online["state"].get("cover_count", 0) >= 5 and online["state"].get("cache", {}).get("entries", 0) > 0, {"covers": online["state"].get("cover_count"), "cache": online["state"].get("cache")})
        # The HTTP listener is closed before the second application starts.
        offline = self.run("cache-cover-offline", "sources", demo=False, data=data, replay=Replay().intent("sources", "open", "komga"), expected_route="catalog")
        self.check(offline, "A cold offline process restores visible network covers through persistent aliases", offline["state"].get("cover_count", 0) >= 5, offline["state"].get("cover_count"))
        self.check(offline, "Offline catalog navigation retains the provider listing labels", bool(offline["state"].get("catalog_tabs")) and offline["state"].get("catalog_tabs") == online["state"].get("catalog_tabs"), {"online": online["state"].get("catalog_tabs"), "offline": offline["state"].get("catalog_tabs")})
        try:
            with Image.open(online["screenshot"]) as first, Image.open(offline["screenshot"]) as second:
                rectangle = (23, 207, 86, 294)
                difference = ImageChops.difference(first.convert("RGB").crop(rectangle), second.convert("RGB").crop(rectangle))
                error = max(channel[1] for channel in difference.getextrema())
                self.check(offline, "The restored offline cover has the same rendered pixels", error <= 2, error)
        except OSError as error:
            self.check(offline, "Cover comparison evidence can be read", False, str(error))

        imported: dict[str, Any] | None = None
        protected: dict[str, str] = {}
        if "storage-clear-cache" in wanted:
            imported = self.run("cache-local-import", demo=False, data=data, import_path=self.create_cbz("cache-import-fixture"), expected_route="library", capture_delay=1500)
            rows = self.database_rows(imported, "SELECT pages.document FROM pages JOIN chapters ON chapters.id=pages.chapter_id WHERE chapters.source_id='local'")
            for row in rows:
                path = json.loads(row[0]).get("local_path")
                if path and Path(path).is_file():
                    protected[path] = sha256(Path(path))
            self.check(imported, "The UI cache-clear fixture contains real imported comic pages", len(protected) == 3, sorted(protected))
        if "storage-cache-info" in wanted:
            info = self.run("storage-cache-info", "settings", demo=False, data=data, replay=Replay().tap(200, 414).tap(309, 443), expected_route="settings")
            self.expect(info, "modal", "summary")
            self.check(info, "Cache information uses actual persisted cache statistics", info["state"].get("cache", {}).get("entries", 0) > 0, info["state"].get("cache"))
        if "storage-clear-cache" in wanted:
            before = (imported or offline)["state"].get("cache", {})
            cleared = self.run("storage-clear-cache", "settings", demo=False, data=data, replay=Replay().tap(200, 414).tap(309, 495, wait=1000), expected_route="settings", capture_delay=1500)
            self.expect(cleared, "modal", "")
            after = cleared["state"].get("cache", {})
            self.check(cleared, "The clear-cache UI does not increase unpinned cache usage", after.get("unpinned_bytes", 0) <= before.get("unpinned_bytes", 0), {"before": before, "after": after})
            self.check(cleared, "Clear-cache preserves protected imported image files byte for byte", len(protected) == 3 and all(Path(path).is_file() and sha256(Path(path)) == digest for path, digest in protected.items()), sorted(protected))
            if before.get("pinned_bytes", 0):
                self.check(cleared, "Pinned cache data remains available after the UI clear operation", after.get("pinned_bytes", 0) >= before["pinned_bytes"], {"before": before.get("pinned_bytes"), "after": after.get("pinned_bytes")})

    def review_regressions(self, wanted: set[str]) -> None:
        if "scribe-chapter-select-two" in wanted:
            case = self.run("scribe-chapter-select-two", "detail", device="scribe", replay=Replay().hold(500, 226, duration=600).tap(500, 287), expected_route="detail")
            self.check(case, "Scribe selection highlights exactly the two touched chapter rows", set(case["state"].get("selected_chapters", [])) == {"8:mangadex:demo-00-chapter-086", "8:mangadex:demo-00-chapter-085"}, sorted(case["state"].get("selected_chapters", [])))
            self.expect_job_numbers(case, set(range(27, 36)))

        for name in ("summary-book-tags", "summary-cache-info-tagged", "summary-about-tagged"):
            if name not in wanted:
                continue
            if name == "summary-book-tags":
                screen, replay = "detail", Replay().intent("detail", "summary")
            elif name == "summary-cache-info-tagged":
                screen, replay = "settings", Replay().tap(200, 414).tap(309, 443)
            else:
                screen, replay = "settings", Replay().intent("detail", "summary").intent("app", "back").intent("settings", "open", "about")
            case = self.run(name, screen, replay=replay, expected_route=screen)
            self.expect(case, "modal", "summary")
            self.check(case, "The selected manga has nonempty genres, so this is not an empty-tag fixture", bool(case["state"].get("book_tags")), case["state"].get("book_tags"))
            is_book = name == "summary-book-tags"
            self.expect(case, "summary_is_book", is_book)
            ink = self.summary_tag_ink(case)
            self.check(case, "Only a book summary renders the manga genre strip", ink > 0 if is_book else ink == 0, ink)

        if "downloads-disabled-global" in wanted:
            case = self.run("downloads-disabled-global", "downloads", replay=Replay().tap(309, 233).tap(309, 285).tap(309, 337), expected_route="settings")
            self.expect(case, "modal", "downloads")
            rows = self.modal_rows(case)
            controls = ("downloads-pause-all", "downloads-resume-all", "downloads-cancel-all")
            self.check(case, "Completed-only queues disable all three global controls", all(rows.get(identifier, {}).get("enabled") is False for identifier in controls), {identifier: rows.get(identifier, {}).get("enabled") for identifier in controls})
            self.expect_job_numbers(case, set(range(27, 36)))
            self.check(case, "Tapping disabled global controls leaves all completed jobs intact", all(job.get("state") == "completed" for job in self.primary_jobs(case).values()), {identifier: job.get("state") for identifier, job in self.primary_jobs(case).items()})

        for name in ("completed-job-menu", "completed-job-read", "completed-remove-confirmation", "completed-remove-cancel", "completed-remove-apply"):
            if name not in wanted:
                continue
            replay = Replay().tap(309, 389)
            if name == "completed-job-read":
                replay.tap(309, 391)
            elif name.startswith("completed-remove"):
                replay.tap(309, 443)
                if name == "completed-remove-cancel":
                    replay.tap(309, 469)
                elif name == "completed-remove-apply":
                    replay.tap(309, 417, wait=1000)
            expected_route = "reader" if name == "completed-job-read" else "settings"
            case = self.run(name, "downloads", replay=replay, expected_route=expected_route, capture_delay=1000)
            jobs = self.primary_jobs(case)
            if name == "completed-job-menu":
                ids = case["state"].get("modal_item_ids", [])
                self.check(case, "A completed job offers reading, removal, and cancel instead of pending-job controls", len(ids) == 3 and ids[0].startswith("chapter:") and ids[1:] == ["job-remove", "close"], ids)
            elif name == "completed-job-read":
                self.expect(case, "reader_total", 40)
                self.check(case, "The completed-row reading action opens its downloaded chapter", case["state"].get("current_chapter") in jobs, case["state"].get("current_chapter"))
            elif name == "completed-remove-confirmation":
                self.expect(case, "modal_item_ids", ["job-remove-confirm", "close"])
            if name == "completed-remove-apply":
                self.expect(case, "modal", "downloads")
                self.check(case, "Confirmed removal deletes exactly one completed queue record", len(jobs) == 8, sorted(jobs))
                paths: set[str] = set()
                for identifier in jobs:
                    for row in self.database_rows(case, "SELECT document FROM pages WHERE chapter_id=?", (identifier,)):
                        path = json.loads(row[0]).get("local_path")
                        if path:
                            paths.add(path)
                self.check(case, "Removing one chapter preserves image files shared by the remaining completed chapters", bool(paths) and all(Path(path).is_file() for path in paths), sorted(paths))
            else:
                self.expect_job_numbers(case, set(range(27, 36)))
            self.check(case, "Completed jobs are not exposed as paused, retrying, or cancelled", all(job.get("state") == "completed" for job in jobs.values()), {identifier: job.get("state") for identifier, job in jobs.items()})

        if "downloads-failed-controls" in wanted:
            seed = self.run("downloads-failed-controls-seed", "downloads", expected_route="settings")
            database = seed["state"].get("database")
            identifier = "8:mangadex:demo-00-chapter-027"
            if database:
                with sqlite3.connect(database) as connection:
                    row = connection.execute("SELECT document FROM downloads WHERE chapter_id=?", (identifier,)).fetchone()
                    job = json.loads(row[0])
                    job["state"], job["error"] = "failed", "Controlled fixture failure"
                    connection.execute("UPDATE downloads SET state='failed',document=? WHERE chapter_id=?", (json.dumps(job), identifier))
                case = self.run("downloads-failed-controls", "downloads", data=Path(seed["data_dir"]), replay=Replay().tap(309, 337), expected_route="settings")
                rows = self.modal_rows(case)
                expected_enabled = {"downloads-pause-all": False, "downloads-resume-all": True, "downloads-cancel-all": False}
                self.check(case, "A failed-only pending queue offers retry and keeps cancel/pause disabled", all(rows.get(key, {}).get("enabled") is value for key, value in expected_enabled.items()), {key: rows.get(key, {}).get("enabled") for key in expected_enabled})
                self.check(case, "The disabled cancel control does not alter a failed job", self.primary_jobs(case).get(identifier, {}).get("state") == "failed", self.primary_jobs(case).get(identifier, {}).get("state"))

    def feedback_regressions(self, wanted: set[str]) -> None:
        if "batch-last-chapter-empty" in wanted:
            seed = self.run("batch-last-chapter-empty-seed", "detail")
            database = seed["state"].get("database")
            if database:
                with sqlite3.connect(database) as connection:
                    latest = connection.execute("SELECT MAX(updated_at) FROM progress").fetchone()[0]
                    connection.execute("INSERT OR REPLACE INTO progress(chapter_id,book_id,page,total,completed,updated_at) VALUES(?,?,?,?,?,?)", ("8:mangadex:demo-00-chapter-086", "8:mangadex:demo-00", 40, 40, 1, (latest or 0) + 1000))
                case = self.run("batch-last-chapter-empty", "detail", data=Path(seed["data_dir"]), replay=Replay().tap(572, 54).tap(309, 469).tap(309, 365, wait=800), expected_route="detail")
                self.expect_job_numbers(case, set(range(27, 36)))
                self.expect(case, "message", ui_label("no_downloads_needed"))
        if "batch-completed-only" in wanted:
            replay = Replay()
            for number in range(27, 36):
                replay.intent("detail", "select-chapter", f"8:mangadex:demo-00-chapter-{number:03}", wait=100)
            replay.intent("detail", "download-selected", wait=1000)
            case = self.run("batch-completed-only", "detail", replay=replay, expected_route="detail")
            self.expect_job_numbers(case, set(range(27, 36)))
            self.expect(case, "selected_chapters", [])
            self.check(case, "An accepted batch containing only valid completed chapters stays completed", all(job.get("state") == "completed" for job in self.primary_jobs(case).values()), {identifier: job.get("state") for identifier, job in self.primary_jobs(case).items()})
            self.expect(case, "message", ui_label("no_downloads_needed"))
        if "batch-empty-selected" in wanted:
            case = self.run("batch-empty-selected", "detail", replay=Replay().intent("detail", "download-selected"), expected_route="detail")
            self.expect_job_numbers(case, set(range(27, 36)))
            self.expect(case, "selected_chapters", [])
            self.expect(case, "message", ui_label("no_downloads_needed"))
        if "status-strip" in wanted:
            self.status_strip_regressions()

    def compare_outside(self, case: dict[str, Any], baseline: dict[str, Any], rectangle: tuple[int, int, int, int], description: str = "The status notice changes no pixels outside its existing status/clock slot") -> None:
        try:
            with Image.open(case["screenshot"]) as actual, Image.open(baseline["screenshot"]) as expected:
                difference = ImageChops.difference(actual.convert("RGB"), expected.convert("RGB"))
                difference.paste((0, 0, 0), rectangle)
                self.check(case, description, difference.getbbox() is None, difference.getbbox())
        except OSError as error:
            self.check(case, "Status-strip pixel evidence can be read", False, str(error))

    def status_strip_regressions(self) -> None:
        baseline = self.run("status-strip-baseline")
        active = self.run("status-strip-notice", replay=Replay().intent("library", "refresh"), expected_route="library")
        self.expect(active, "message", ui_label("saved"))
        self.compare_outside(active, baseline, (0, 0, 618, 30))
        tapped = self.run("status-strip-tap", replay=Replay().intent("library", "refresh").tap(300, 15), expected_route="library")
        self.expect(tapped, "message", ui_label("saved"))
        self.expect(tapped, "page", 1)
        replay = Replay().intent("library", "refresh")
        replay.at += 3500
        expired = self.run("status-strip-expired", replay=replay, expected_route="library")
        self.expect(expired, "message", "")
        self.compare_outside(expired, baseline, (0, 0, 0, 0))
        modal_baseline = self.run("status-strip-modal-baseline", replay=Replay().intent("library", "more"), expected_route="library")
        hidden = self.run("status-strip-modal-hidden", replay=Replay().intent("library", "refresh").intent("library", "more"), expected_route="library")
        self.expect(hidden, "modal", "choices")
        self.expect(hidden, "message", ui_label("saved"))
        self.compare_outside(hidden, modal_baseline, (0, 0, 0, 0))
        wide_baseline = self.run("status-strip-wide-reader-baseline", "reader", device="scribe", landscape=True, replay=Replay().tap(620, 400), expected_route="reader")
        wide = self.run("status-strip-wide-reader-notice", "reader", device="scribe", landscape=True, replay=Replay(start=3400).tap(620, 400).intent("library", "refresh"), expected_route="reader")
        self.expect(wide, "message", ui_label("saved"))
        self.compare_outside(wide, wide_baseline, (1126, 20, 1232, 56))

    def session_cases(self, requested: set[str] | None = None) -> None:
        wanted = set(SESSION_CASES) if requested is None else requested
        if "demo-assets-upgrade" in wanted:
            before = self.run("demo-assets-upgrade-before", "reader", replay=Replay(start=2000).intent("reader", "seek", "20", wait=4000).intent("reader", "bookmark").intent("settings", "set", "refresh_every=10", wait=1000), expected_route="reader")
            self.expect(before, "reader_page", 20)
            data = Path(before["data_dir"])
            actual_data = Path(before["state"]["database"]).parent
            owned_page = actual_data / "demo-assets/page-01.png"
            legacy_page = Path(__file__).with_name("fixtures") / "demo-page-01-before.png"
            bundled_page = Path(__file__).resolve().parent.parent / "assets/demo/page-01.png"
            if not owned_page.resolve().is_relative_to(self.data_root.resolve()):
                raise RuntimeError("The demo-upgrade target escaped its isolated fixture directory")
            history = self.database_rows(before, "SELECT chapter_id,page,total,completed,updated_at FROM progress ORDER BY chapter_id")
            bookmarks = self.database_rows(before, "SELECT chapter_id,page,created_at FROM bookmarks ORDER BY chapter_id,page")
            marker = actual_data / ".demo-v1-seeded"
            marker_digest = sha256(marker)
            cover = actual_data / "demo-assets/cover-00.png"
            cover_digest = sha256(cover)
            old_digest = sha256(legacy_page)
            new_digest = sha256(bundled_page)
            self.check(before, "The upgrade fixture contains a genuinely older managed page", old_digest != new_digest, {"before": old_digest, "bundled": new_digest})
            shutil.copy2(legacy_page, owned_page)
            restored = self.run("demo-assets-upgrade-after", "settings", data=data, expected_route="settings")
            self.check(restored, "Startup atomically replaces the older owned demo page with the bundled repair", sha256(owned_page) == new_digest, {"actual": sha256(owned_page), "expected": new_digest})
            self.check(restored, "Demo asset repair does not reset or rewrite the seed marker", sha256(marker) == marker_digest)
            self.check(restored, "Demo page repair leaves cover assets untouched", sha256(cover) == cover_digest)
            self.check(restored, "Demo asset repair preserves every reading-progress row", self.database_rows(restored, "SELECT chapter_id,page,total,completed,updated_at FROM progress ORDER BY chapter_id") == history)
            self.check(restored, "Demo asset repair preserves every bookmark row", self.database_rows(restored, "SELECT chapter_id,page,created_at FROM bookmarks ORDER BY chapter_id,page") == bookmarks)
            self.check(restored, "Demo asset repair preserves saved reading preferences", restored["state"].get("preferences") == before["state"].get("preferences"), restored["state"].get("preferences"))
        if "session-library-restore" in wanted:
            before = self.run("session-library-checkpoint", replay=Replay().intent("library", "filter", "1").intent("page", "turn", "1"), expected_route="library")
            self.expect(before, "page", 2)
            saved = self.navigation_session(before)
            self.check(before, "The library checkpoint retains category and pagination", saved.get("library_filter") == 1 and saved.get("page") == 2, saved)
            restored = self.run("session-library-restored", data=Path(before["data_dir"]), resume=True, expected_route="library")
            self.expect(restored, "page", 2)
            self.expect(restored, "page_count", 2)
        if "session-detail-restore" in wanted:
            before = self.run("session-detail-checkpoint", "detail", replay=Replay().intent("detail", "sort").intent("page", "turn", "1"), expected_route="detail")
            saved = self.navigation_session(before)
            self.check(before, "The detail checkpoint retains chapter ordering", saved.get("newest_first") is False, saved.get("newest_first"))
            restored = self.run("session-detail-restored", data=Path(before["data_dir"]), resume=True, expected_route="detail")
            self.expect(restored, "page", 2)
            self.expect(restored, "current_book", before["state"].get("current_book"))
            self.check(restored, "Restored detail ordering remains selected", self.navigation_session(restored).get("newest_first") is False)
        if "session-reader-restore" in wanted:
            before = self.run("session-reader-checkpoint", "reader", replay=Replay(start=2000).intent("reader", "seek", "20", wait=4000), expected_route="reader")
            self.expect(before, "reader_page", 20)
            self.expect(before, "menu", False)
            saved = self.navigation_session(before)
            self.check(before, "The committed reader page is checkpointed with its menu closed", saved.get("reader", {}).get("page") == 20 and saved.get("reader", {}).get("menu_open") is False, saved.get("reader"))
            restored = self.run("session-reader-restored", data=Path(before["data_dir"]), resume=True, expected_route="reader")
            self.expect(restored, "reader_page", 20)
            self.expect(restored, "menu", False)
            self.compare_outside(restored, before, (0, 0, 0, 0), "A cold --resume process restores exactly the previously displayed pure comic pixels")
        if "session-cross-book-detail" in wanted:
            before = self.run("session-cross-book-checkpoint", "reader", replay=Replay(start=4500).intent("reader", "back").intent("app", "back").tap(220, 380), expected_route="detail")
            expected = "8:mangadex:demo-01"
            self.expect(before, "current_book", expected)
            self.check(before, "A non-reader checkpoint does not retain the old displayed reader identity", self.navigation_session(before).get("reader") is None)
            restored = self.run("session-cross-book-restored", data=Path(before["data_dir"]), resume=True, expected_route="detail")
            self.expect(restored, "current_book", expected)
        if "session-landscape-restore" in wanted:
            before = self.run("session-landscape-checkpoint", "reader", device="scribe", landscape=True, replay=Replay(start=3500).intent("reader", "seek", "18", wait=5000), expected_route="reader")
            self.expect(before, "reader_page", 18)
            restored = self.run("session-landscape-restored", device="scribe", expected_landscape=True, data=Path(before["data_dir"]), resume=True, expected_route="reader")
            self.expect(restored, "reader_page", 18)
            self.expect(restored, "menu", False)
            self.compare_outside(restored, before, (0, 0, 0, 0), "A cold fallback restores Scribe spread layout and page pixels without reopening chrome")
        if "session-vertical-restore" in wanted:
            imported = self.run("session-vertical-import", demo=False, import_path=self.create_vertical_cbz("session-vertical"), expected_route="library", capture_delay=1600)
            before = self.run("session-vertical-checkpoint", "reader", demo=False, data=Path(imported["data_dir"]), replay=Replay().intent("settings", "set", "reading_direction=vertical", wait=4000).tap(570, 792).intent("reader", "zoom", wait=4000), expected_route="reader")
            self.check(before, "The vertical fixture is positioned beyond its first viewport", before["state"].get("reader_offset", 0) > 0, before["state"].get("reader_offset"))
            self.expect(before, "reader_zoom", 2.0)
            restored = self.run("session-vertical-restored", demo=False, data=Path(imported["data_dir"]), resume=True, expected_route="reader")
            self.expect(restored, "reader_offset", before["state"].get("reader_offset"))
            self.expect(restored, "reader_zoom", 2.0)
            self.expect(restored, "menu", False)
            self.compare_outside(restored, before, (0, 0, 0, 0), "Vertical position and zoom restore the exact displayed image instead of jumping to the chapter start")
        if "session-secrets" in wanted:
            seed = self.run("session-secrets-seed")
            database = seed["state"].get("database")
            markers = ["SESSION_FIXTURE_API_TOKEN", "SESSION_FIXTURE_PASSWORD", "SESSION_FIXTURE_USERNAME"]
            if database:
                with sqlite3.connect(database) as connection:
                    row = connection.execute("SELECT document FROM sources WHERE id='komga'").fetchone()
                    source = json.loads(row[0])
                    source.update({"token": markers[0], "password": markers[1], "username": markers[2]})
                    connection.execute("UPDATE sources SET document=? WHERE id='komga'", (json.dumps(source),))
                saved = self.run("session-secrets-checkpoint", "catalog", data=Path(seed["data_dir"]), replay=Replay().intent("sources", "open", "komga").intent("page", "turn", "1"), expected_route="catalog")
                document = json.dumps(self.navigation_session(saved), sort_keys=True)
                self.check(saved, "Navigation persistence excludes provider credentials and API tokens", all(marker not in document for marker in markers), {"checkpoint_bytes": len(document), "secret_markers_found": [marker for marker in markers if marker in document]})
                restored = self.run("session-secrets-restored", data=Path(seed["data_dir"]), resume=True, expected_route="catalog")
                self.expect(restored, "page", 2)
        if "session-incognito-ram" in wanted:
            history = self.run("session-incognito-enable", "history", replay=Replay().intent("settings", "set", "incognito=true"), expected_route="history")
            rows_before = self.database_rows(history, "SELECT chapter_id,page,total,completed,updated_at FROM progress ORDER BY chapter_id")
            before = self.run("session-incognito-ram", "reader", data=Path(history["data_dir"]), replay=Replay(start=2000).intent("reader", "seek", "13", wait=4000).intent("lifecycle", "suspend", wait=700).intent("lifecycle", "resume", wait=100).intent("lifecycle", "frame-displayed", wait=700), expected_route="reader", capture_delay=1000)
            self.expect(before, "reader_page", 13)
            self.expect(before, "menu", False)
            self.check(before, "Incognito suspend/resume retains the RAM page without writing history", self.database_rows(before, "SELECT chapter_id,page,total,completed,updated_at FROM progress ORDER BY chapter_id") == rows_before)
            saved = self.navigation_session(before)
            self.check(before, "The private reader is not written into the durable resume checkpoint", saved.get("reader") is None and saved.get("route") == "library", saved)
            restored = self.run("session-incognito-restored", data=Path(history["data_dir"]), resume=True, expected_route="library")
            self.check(restored, "Restarting an incognito session still adds no reading-history record", self.database_rows(restored, "SELECT chapter_id,page,total,completed,updated_at FROM progress ORDER BY chapter_id") == rows_before)
        if "lifecycle-retained-reader" in wanted:
            baseline = self.run("lifecycle-reader-baseline", "reader", replay=Replay(start=2000).intent("reader", "seek", "20", wait=4000), expected_route="reader")
            suspended = self.run("lifecycle-reader-suspended", "reader", replay=Replay(start=2000).intent("reader", "seek", "20", wait=4000).intent("lifecycle", "suspend", wait=800), expected_route="reader")
            self.expect(suspended, "reader_page", 20)
            self.compare_outside(suspended, baseline, (0, 0, 0, 0), "Application-level suspend retains the entire displayed pure comic frame")
            resumed = self.run("lifecycle-reader-resumed", "reader", replay=Replay(start=2000).intent("reader", "seek", "20", wait=4000).intent("lifecycle", "suspend", wait=500).intent("reader", "next", wait=500).intent("lifecycle", "resume", wait=100).intent("lifecycle", "frame-displayed", wait=800), expected_route="reader")
            self.expect(resumed, "reader_page", 20)
            self.expect(resumed, "menu", False)
            self.compare_outside(resumed, baseline, (0, 0, 0, 0), "Desktop App lifecycle resume restores the retained page and ignores input while suspended")
        if "lifecycle-pending-direction" in wanted:
            replay = Replay(start=4500).event("settings", "set", "reading_direction=vertical").event("lifecycle", "suspend")
            replay.at += 700
            replay.intent("lifecycle", "resume", wait=100).intent("lifecycle", "frame-displayed", wait=4000)
            case = self.run("lifecycle-pending-direction", "reader", replay=replay, expected_route="reader", capture_delay=1600)
            self.expect(case, "reader_page", 12)
            self.expect(case, "reader_offset", 0)
            self.expect(case, "menu", False)
            displayed = case["state"].get("displayed_reader") or {}
            self.check(case, "A pending direction change is applied only after retained-frame acknowledgement", displayed.get("preferences", {}).get("reading_direction") == "vertical", displayed.get("preferences"))
        if "lifecycle-pending-rotation" in wanted:
            replay = Replay(start=6000).event("settings", "set", "landscape=true").event("lifecycle", "suspend")
            replay.at += 700
            replay.intent("lifecycle", "resume", wait=100).intent("lifecycle", "frame-displayed", wait=5000)
            case = self.run("lifecycle-pending-rotation", "reader", device="scribe", expected_landscape=True, replay=replay, expected_route="reader", capture_delay=2000)
            self.expect(case, "reader_page", 12)
            self.expect(case, "menu", False)
            displayed = case["state"].get("displayed_reader") or {}
            self.check(case, "Pending Scribe rotation eventually displays the reflowed landscape frame on the same page", displayed.get("preferences", {}).get("landscape") is True and displayed.get("session", {}).get("landscape") is True, {"preferences": displayed.get("preferences"), "session": displayed.get("session")})
        if "lifecycle-doubletap-reset" in wanted:
            replay = Replay(start=4500).intent("settings", "set", "double_tap_zoom=true", wait=1000).tap(309, 400, wait=50).intent("lifecycle", "suspend", wait=800).intent("lifecycle", "resume", wait=100).intent("lifecycle", "frame-displayed", wait=500).tap(80, 400, wait=700)
            case = self.run("lifecycle-doubletap-reset", "reader", replay=replay, expected_route="reader", capture_delay=1400)
            self.expect(case, "reader_page", 13)
            self.expect(case, "reader_zoom", 1.0)
            self.expect(case, "menu", False)
        if "lifecycle-seek-reset" in wanted:
            replay = Replay(start=4500).tap(309, 400).event("pointer", "move", "430,694").event("pointer", "press", "430,694", 50).event("pointer", "move", "100,694", 100).event("lifecycle", "suspend")
            replay.at += 700
            replay.intent("lifecycle", "resume", wait=100).intent("lifecycle", "frame-displayed", wait=300).event("pointer", "release", "100,694")
            replay.at += 800
            case = self.run("lifecycle-seek-reset", "reader", replay=replay, expected_route="reader", capture_delay=1600)
            self.expect(case, "reader_page", 12)
            self.expect(case, "menu", False)

    def run_selected(self, names: list[str]) -> None:
        requested = set(names)
        if "reader-bottom-right-vertical" in requested:
            case = self.run("reader-bottom-right-vertical", "reader", replay=Replay().intent("settings", "set", "reading_direction=vertical", wait=4000).tap(570, 792, wait=4000), expected_route="reader")
            self.check(case, "The bottom-right region advances vertical reading instead of changing a footer mode", case["state"].get("reader_page", 0) > 12 or case["state"].get("reader_offset", 0) > 0, {"page": case["state"].get("reader_page"), "offset": case["state"].get("reader_offset")})
            self.preference(case, "footer_mode", 0)
        if "baseline-large-readers" in requested:
            self.run("scribe-landscape-reader", "reader", device="scribe", landscape=True, replay=Replay().tap(620, 400), expected_route="reader")
            self.run("scribe3-nofrontlight-reader", "reader", device="scribe3-nofrontlight")
        if "local-cbz-roundtrip" in requested:
            self.local_content()
        for name in sorted(requested & {"reader-long-press-600", "reader-long-press-650", "reader-long-press-800"}):
            duration = int(name.rsplit("-", 1)[-1])
            case = self.run(name, "reader", replay=Replay().hold(300, 410, duration=duration), expected_route="reader")
            self.expect(case, "reader_page", 12)
            self.expect(case, "modal", "choices")
        self.new_ui_cases(requested)
        self.session_cases(requested & set(SESSION_CASES))

    def finish(self) -> int:
        immutable = sha256(self.binary) == self.binary_hash
        if not immutable:
            self.cases.append({"name": "binary-immutability", "passed": False, "checks": [{"description": "The binary hash remains unchanged throughout verification", "passed": False}]})
        failures = [case["name"] for case in self.cases if not case["passed"]]
        report = {
            "schema_version": 1, "kind": "remote-desktop-e2e", "host": platform.node(),
            "binary": str(self.binary), "binary_sha256": self.binary_hash, "binary_unchanged": immutable,
            "elapsed_seconds": round(time.monotonic() - self.started, 3), "run_id": self.run_id,
            "requested_case_ids": getattr(self.args, "selected_cases", None),
            "passed": len(self.cases) - len(failures), "failed": len(failures), "failed_cases": failures,
            "pointer_case_count": sum(case.get("pointer_events", 0) > 0 for case in self.cases),
            "cases": self.cases,
            "evidence_scope": "Desktop layout, real Slint hit areas, offline state transitions, local content, persistence, and isolated loopback HTTP source pagination. Physical e-ink, firmware input, power and suspend behavior require separate device verification.",
            "manual_design_review_required": True,
        }
        save_json(self.output / "report.json", report)
        lines = ["# Remote UI verification", "", f"- Host: `{platform.node()}`", f"- Binary SHA-256: `{self.binary_hash}`", f"- Passed: {report['passed']}", f"- Failed: {report['failed']}", f"- Cases with real pointer events: {report['pointer_case_count']}", "", "Desktop evidence does not replace Kindle firmware or physical e-ink acceptance.", "", "| Case | Result | Screenshot |", "| --- | --- | --- |"]
        for case in self.cases:
            lines.append(f"| {case['name']} | {'PASS' if case['passed'] else 'FAIL'} | [{case['name']}.png]({case['name']}.png) |")
        (self.output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"UI verification: {report['passed']} passed, {report['failed']} failed. Report: {self.output / 'report.json'}", flush=True)
        return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True, help="Immutable desktop aidoku binary on the remote worker")
    parser.add_argument("--output-dir", "--outputdir", dest="output_dir", type=Path, required=True, help="Retain captures, replay actions, logs, state sidecars and the final report here")
    parser.add_argument("--timeout", type=int, default=60, help="Per-process timeout in seconds")
    parser.add_argument("--performance-traces", action="store_true", help="Retain opt-in Reader producer traces for qualification; capture trials are excluded from timing comparisons")
    parser.add_argument("--quick", "--screenshots-only", dest="quick", action="store_true", help="Capture all device/design states without the extended interaction suite")
    parser.add_argument("--network-only", action="store_true", help="Run only the isolated loopback Komga browse/search pagination E2E cases")
    parser.add_argument("--interaction-only", action="store_true", help="Run only child-started list gestures and Scribe simulated source installation")
    parser.add_argument("--new-ui-only", action="store_true", help="Run only chapter selection, batch downloads, lazy download management, and persistent-cover/cache UI scenarios")
    parser.add_argument("--session-only", action="store_true", help="Run durable --resume and desktop App lifecycle cases; native SIGSTOP/evdev restoration requires the separate firmware suite")
    parser.add_argument("--case", dest="selected_cases", choices=SELECTABLE_CASES, action="append", help="Run one new UI scenario, including necessary fixture prerequisites; repeat to select multiple scenarios")
    args = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run this verification on the designated remote Linux worker through ssh test-env.")
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        parser.error("--binary must identify an executable desktop build.")
    if shutil.which("xvfb-run") is None:
        parser.error("The remote worker requires xvfb-run.")
    suite = Suite(args)
    try:
        if args.selected_cases:
            suite.run_selected(args.selected_cases)
        elif args.session_only:
            suite.session_cases()
        elif args.new_ui_only:
            suite.new_ui_cases()
        elif args.network_only:
            suite.http_source_pagination()
        elif args.interaction_only:
            suite.list_interactions()
        else:
            suite.shots()
        if not args.quick and not args.network_only and not args.interaction_only and not args.new_ui_only and not args.session_only and not args.selected_cases:
            for group in (suite.pointer_navigation, suite.reader_interactions, suite.persistence, suite.lists_and_search, suite.local_content):
                try:
                    group()
                except Exception as error:
                    suite.cases.append({"name": group.__name__, "passed": False, "checks": [{"description": "Scenario group completed", "passed": False, "actual": f"{type(error).__name__}: {error}"}]})
            suite.http_source_pagination()
            suite.list_interactions()
            suite.new_ui_cases()
            suite.session_cases()
    except Exception as error:
        suite.cases.append({"name": "suite", "passed": False, "checks": [{"description": "The verification suite completed", "passed": False, "actual": f"{type(error).__name__}: {error}"}]})
    return suite.finish()


if __name__ == "__main__":
    sys.exit(main())
