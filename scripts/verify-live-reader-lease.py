#!/usr/bin/env python3
"""Check real Reader leases across same-process book cleanup and forced cache clear.

Run only on the designated Linux worker inside a fresh isolated Xvfb display.
The frozen application creates every page, download, pin, and lease through its
ordinary provider and action paths. Only the public loopback source input is
configured directly. No lease or owner is inserted through SQL.

The supported contract is one application with Reader, download-controller, and
SourceManager handles sharing the canonical process-local CacheManager registry.
This does not test concurrent independent GUI processes or continuous physical
display. The standalone captures are cache-correctness evidence, not UI rounds.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Any, Callable

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parent.parent
PROFILE = "scribe"
SIZE = (930, 1240)
TITLE = "Aidoku for Kindle"
CLEANUP_AT_MS = 15_000
CLEAR_AT_MS = 20_000
CAPTURE_AT_MS = 26_000


def load(name: str, filename: str) -> Any:
    specification = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load the existing verification helper: {filename}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


handoff = load("lease_handoff_helpers", "verify-handoff-ui.py")
cache_ui = load("lease_x11_helpers", "verify-cache-ui.py")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def rows(database: Path, query: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as connection:
        connection.execute("PRAGMA query_only=ON")
        return connection.execute(query, parameters).fetchall()


def file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}


class RecordedFixture(handoff.KomgaFixture):
    def __init__(self, output: Path, **options: Any):
        super().__init__(**options)
        self.evidence_output = output

    def __exit__(self, *values: Any) -> None:
        try:
            super().__exit__(*values)
        finally:
            with self.lock:
                requests = [dict(item) for item in self.requests]
            save(self.evidence_output / "provider-requests.json", requests)


class Harness:
    def __init__(self, arguments: argparse.Namespace):
        self.args = arguments
        self.output = arguments.output.resolve()
        self.binary = arguments.binary.resolve(strict=True)
        self.data = self.output / "data"
        self.data.mkdir()
        self.runtime = self.output / "xdg-runtime"
        self.runtime.mkdir(mode=0o700)
        self.cases: list[dict[str, Any]] = []
        self.binary_before = digest(self.binary)
        if self.binary_before != arguments.expected_sha256:
            raise ValueError("The frozen desktop executable does not match the authorized SHA-256")
        self.environment = os.environ.copy()
        self.environment.update({"SLINT_SCALE_FACTOR": "1", "SLINT_BACKEND": "winit-software",
                                 "SLINT_RENDERER": "software", "XDG_RUNTIME_DIR": str(self.runtime),
                                 "TZ": "Asia/Shanghai", "RUST_BACKTRACE": "1"})

    def check(self, case: dict[str, Any], description: str, passed: bool, actual: Any = None) -> None:
        case["checks"].append({"description": description, "passed": bool(passed), "actual": actual})
        if not passed:
            case["passed"] = False
            print(f"FAIL {case['name']}: {description}; actual={actual!r}", flush=True)

    def expect(self, case: dict[str, Any], field: str, expected: Any) -> None:
        actual = case.get("state", {}).get(field)
        self.check(case, f"Application state {field} matches the required value", actual == expected, actual)

    def persist_case(self, case: dict[str, Any]) -> None:
        records = {}
        for label, path in {"screenshot": Path(case["screenshot"]),
                            "state": Path(case["screenshot"]).with_suffix(".state.json"),
                            "metadata": Path(case["screenshot"]).with_suffix(".metadata.json"),
                            "actions": Path(case["actions"]), "log": self.output / f"{case['name']}.log"}.items():
            if path.is_file():
                records[label] = file_record(path)
        case["evidence"] = records
        save(self.output / "cases" / f"{case['name']}.json", case)

    def capture(self, name: str, screen: str = "library", *, replay: Any = None,
                offline: bool = True, milliseconds: int = 5000,
                expected_route: str | None = None, pure: bool = False,
                observe: Callable[[subprocess.Popen[Any], float, dict[str, Any]], None] | None = None,
                **seed_options: Any) -> dict[str, Any]:
        if digest(self.binary) != self.binary_before:
            raise ValueError("The frozen binary changed before an application stage")
        # seed_provider only passes these fixed fixture attributes.
        if set(seed_options) - {"device", "demo", "prerequisite"}:
            raise ValueError(f"Unexpected capture options: {sorted(seed_options)}")
        if seed_options.get("device", PROFILE) != PROFILE or seed_options.get("demo", False) is not False:
            raise ValueError("Every lease stage must use the real non-demo Scribe application")
        screenshot = self.output / f"{name}.png"
        action_path = self.output / "actions" / f"{name}.json"
        events = replay.events if replay is not None else []
        save(action_path, events)
        command = [str(self.binary), "--device", PROFILE, "--screen", screen,
                   "--data-dir", str(self.data), "--capture", str(screenshot),
                   "--actions", str(action_path), "--exit-after-ms", str(milliseconds)]
        if offline:
            command.append("--offline")
        case: dict[str, Any] = {"name": name, "passed": True, "checks": [], "command": command,
                                "screenshot": str(screenshot), "actions": str(action_path),
                                "data_dir": str(self.data), "state": {}, "metadata": {},
                                "binary_sha256_before": self.binary_before,
                                "started_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        self.cases.append(case)
        process: subprocess.Popen[Any] | None = None
        started = time.monotonic()
        print(f"RUN {name}", flush=True)
        try:
            with (self.output / f"{name}.log").open("wb") as log:
                process = subprocess.Popen(command, env=self.environment, stdout=log, stderr=subprocess.STDOUT)
                case["pid"] = process.pid
                if observe is not None:
                    observe(process, started, case)
                result = process.wait(timeout=max(1, self.args.timeout - (time.monotonic() - started)))
            self.check(case, "The owned application process exits successfully", result == 0, result)
            for kind in ("state", "metadata"):
                path = screenshot.with_suffix(f".{kind}.json")
                self.check(case, f"The application writes its own original {kind} sidecar", path.is_file())
                if path.is_file():
                    case[kind] = json.loads(path.read_text(encoding="utf-8"))
            self.check(case, "The application writes its original PNG", screenshot.is_file())
            metadata = case["metadata"]
            self.check(case, "The capture belongs to the actual owned desktop process",
                       metadata.get("process_id") == process.pid and metadata.get("backend") == "desktop"
                       and metadata.get("profile") == PROFILE and metadata.get("scale_factor") == 1,
                       metadata)
            self.check(case, "All recorded geometry matches the real Scribe viewport",
                       all(metadata.get(key) == list(SIZE) for key in ("logical_size", "window_size", "snapshot_size")))
            if screenshot.is_file():
                with Image.open(screenshot) as image:
                    self.check(case, "The original capture is a viewport-sized PNG", image.format == "PNG" and image.size == SIZE)
            self.expect(case, "route", expected_route or screen)
            self.expect(case, "demo", False)
            self.expect(case, "offline", offline)
            self.expect(case, "busy", False)
            if pure:
                handoff.Suite.inspect_pure_reader(self, case)
        except Exception as error:
            self.check(case, "The application stage completes without a harness error", False,
                       f"{type(error).__name__}: {error}")
        finally:
            cache_ui.stop_process(process)
            case["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            case["binary_sha256_after"] = digest(self.binary)
            self.check(case, "The frozen binary remains unchanged", case["binary_sha256_after"] == self.binary_before)
            self.persist_case(case)
        print(f"{'PASS' if case['passed'] else 'FAIL'} {name}", flush=True)
        if not case["passed"]:
            raise RuntimeError(f"Application stage failed: {name}")
        return case


def observe_reader_before_cleanup(harness: Harness, baseline: dict[str, Any], database: Path,
                                  chapter: str, previous_progress: int, protected: list[dict[str, Any]],
                                  raw_before: list[dict[str, Any]]
                                  ) -> Callable[[subprocess.Popen[Any], float, dict[str, Any]], None]:
    with Image.open(baseline["screenshot"]) as original:
        expected = original.convert("RGB")

    def observe(process: subprocess.Popen[Any], started: float, case: dict[str, Any]) -> None:
        polls = []
        case["live_reader_polls"] = polls
        window = None
        deadline = started + CLEANUP_AT_MS / 1000 - 1
        with cache_ui.XCapture() as capture:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("The live Reader process exited before cleanup")
                if window is None:
                    command = ["xdotool", "search", "--onlyvisible", "--all", "--pid", str(process.pid), "--name", f"^{TITLE}$"]
                    found = subprocess.run(command, capture_output=True, text=True, timeout=2, check=False)
                    matches = found.stdout.strip().splitlines() if found.returncode == 0 else []
                    if len(matches) > 1:
                        raise RuntimeError("Multiple visible windows match the owned Reader PID")
                    if matches:
                        window = matches[0]
                        for operation, required in (("getwindowpid", str(process.pid)), ("getwindowname", TITLE)):
                            identity = subprocess.run(["xdotool", operation, window], capture_output=True,
                                                      text=True, timeout=2, check=True).stdout.strip()
                            if identity != required:
                                raise RuntimeError("The live Reader window identity does not match its PID and title")
                        geometry = subprocess.run(["xdotool", "getwindowgeometry", "--shell", window],
                                                  capture_output=True, text=True, timeout=2, check=True).stdout
                        dimensions = dict(line.split("=", 1) for line in geometry.splitlines() if "=" in line)
                        if [dimensions.get("WIDTH"), dimensions.get("HEIGHT")] != [str(SIZE[0]), str(SIZE[1])]:
                            raise RuntimeError("The live Reader window has unexpected client geometry")
                if window is not None:
                    pixels = capture.grab(window, (0, 0, SIZE[0], SIZE[1]))
                    exact = ImageChops.difference(pixels, expected).getbbox() is None
                    progress = rows(database, "SELECT page,total,updated_at FROM progress WHERE chapter_id=?", (chapter,))
                    accepted = len(progress) == 1 and progress[0][0] == 2 and progress[0][1] == 6 and progress[0][2] > previous_progress
                    polls.append({"at_seconds": round(time.monotonic() - started, 6), "exact_reader_pixels": exact,
                                  "fresh_accepted_progress": accepted, "progress": progress})
                    if exact and accepted:
                        path = harness.output / "live-reader-before-cleanup.png"
                        pixels.save(path)
                        actual = [file_record(Path(item["path"])) for item in protected]
                        harness.check(case, "The real live Reader is visible before any cleanup action",
                                      time.monotonic() < started + CLEANUP_AT_MS / 1000,
                                      {"window": window, "pid": process.pid, "at_seconds": time.monotonic() - started})
                        harness.check(case, "All accepted-frame source and derivative bytes are intact before cleanup", actual == protected, actual)
                        case["live_reader_before_cleanup"] = {"window": window, "pid": process.pid,
                                                               "frame": file_record(path), "progress": progress,
                                                               "observed_at_seconds": time.monotonic() - started,
                                                               "x11_formats": capture.formats,
                                                               "accepted_frame_scope": "Exact live X11 Reader pixels plus this process's fresh production progress write. The earlier baseline cache counter belongs to its separate completed process."}
                        break
                time.sleep(0.04)
            else:
                raise RuntimeError("No fresh accepted live Reader frame was observed before the cleanup deadline")
        save(harness.output / "live-reader-before-cleanup-polls.json", polls)
        # File existence alone is not a completion signal. Observe real primary
        # database cleanup before the earliest possible force-clear action.
        cleanup_polls = []
        case["primary_cleanup_polls"] = cleanup_polls
        while time.monotonic() < started + CLEAR_AT_MS / 1000 - 0.1:
            if process.poll() is not None:
                raise RuntimeError("The application exited before real book cleanup completed")
            jobs = rows(database, "SELECT chapter_id,state FROM downloads WHERE book_id=(SELECT book_id FROM chapters WHERE id=?)", (chapter,))
            flags = rows(database, "SELECT COALESCE(json_extract(document,'$.downloaded'),0) FROM chapters WHERE id=?", (chapter,))
            current = {"at_seconds": round(time.monotonic() - started, 6), "jobs": jobs, "downloaded_flags": flags}
            cleanup_polls.append(current)
            if not jobs and flags == [(0,)]:
                case["primary_cleanup_before_force_clear"] = current
                actual_raw = [file_record(Path(item["path"])) | {"position": item["position"]}
                              for item in raw_before]
                cache_index = harness.data / "cache/shared-v1/index.sqlite3"
                owners = rows(cache_index, "SELECT owner,raw_key FROM pins")
                harness.check(case, "All six actual raw pages still exist after primary cleanup and before force clear",
                              actual_raw == raw_before and owners == [],
                              {"raw_files": actual_raw, "durable_owners": owners})
                save(harness.output / "raw-after-primary-before-force-clear.json",
                     {"raw_files": actual_raw, "durable_owners": owners,
                      "observed_at_seconds": time.monotonic() - started})
                break
            time.sleep(0.04)
        else:
            save(harness.output / "primary-cleanup-polls.json", cleanup_polls)
            raise RuntimeError("Real primary book cleanup was not observed before the force-clear deadline")
        save(harness.output / "primary-cleanup-polls.json", cleanup_polls)

    return observe


def run(harness: Harness) -> None:
    source = "suwayomi"
    book = handoff.compound_id(source, "1")
    chapter = handoff.compound_id(book, "102")
    with RecordedFixture(harness.output, shared_graphql=True, page_delay=0.1) as provider:
        provider.series_count = 1
        provider.chapter_count = 3
        data, database = handoff.Suite.seed_provider(harness, "lease-provider", provider, PROFILE,
                                                    source_id=source, source_kind=source,
                                                    source_options={"source_id": "1"})
        prepare = (handoff.Replay().intent("sources", "open", source, wait=1200)
                   .intent("catalog", "open", book, wait=1000).intent("detail", "favorite", wait=80)
                   .intent("detail", "download", chapter, wait=2500))
        downloaded = harness.capture("actual-exclusive-download", "sources", replay=prepare,
                                     offline=False, milliseconds=8000, expected_route="detail")
        jobs = rows(database, "SELECT chapter_id,state,json_extract(document,'$.completed'),json_extract(document,'$.total') FROM downloads")
        harness.check(downloaded, "One ordinary provider download really completed all six exclusive pages",
                      jobs == [(chapter, "completed", 6, 6)], jobs)
        page_rows = rows(database, "SELECT position,json_extract(document,'$.local_path') FROM pages WHERE chapter_id=? ORDER BY position", (chapter,))
        raw = [file_record(Path(path)) | {"position": position} for position, path in page_rows]
        save(harness.output / "downloaded-raw-before.json", raw)
        harness.check(downloaded, "All six actual completed pages are managed regular raw files",
                      len(raw) == 6 and [item["position"] for item in raw] == list(range(6))
                      and all(Path(item["path"]).suffix == ".raw" and Path(item["path"]).is_relative_to(data / "cache/shared-v1/raw") for item in raw), raw)
        index = data / "cache/shared-v1/index.sqlite3"
        owners = rows(index, "SELECT DISTINCT owner FROM pins ORDER BY owner")
        save(harness.output / "download-owner-before.json", owners)
        harness.check(downloaded, "Only the actual completed chapter owns the download pins", owners == [(f"download:{chapter}",)], owners)
        harness.persist_case(downloaded)
        if not downloaded["passed"]:
            raise RuntimeError("The genuine download fixture did not meet its contract")

        read = (handoff.Replay().intent("settings", "set", "page-layout-mode=single", wait=100)
                .intent("library", "open", book, wait=500).intent("detail", "open-chapter", chapter, wait=2500)
                .intent("reader", "seek", "2", wait=3000))
        baseline = harness.capture("accepted-reader-baseline", replay=read, milliseconds=9000,
                                   expected_route="reader", pure=True)
        baseline_cache = baseline["state"].get("cache", {})
        harness.check(baseline, "The accepted real Reader holds raw and processed cache leases",
                      baseline_cache.get("leased_entries", 0) >= 2, baseline_cache)
        frame = baseline["state"].get("displayed_reader") or {}
        harness.check(baseline, "The accepted Reader really displays the selected exclusive page",
                      frame.get("session", {}).get("chapter_id") == chapter
                      and frame.get("session", {}).get("page") == 2 and not frame.get("second_path"), frame)
        processed = file_record(Path(frame["first_path"]))
        raw_page = {key: value for key, value in raw[1].items() if key != "position"}
        protected = [raw_page, processed]
        save(harness.output / "protected-before.json", protected)
        harness.persist_case(baseline)
        if not baseline["passed"]:
            raise RuntimeError("No genuine accepted Reader baseline is available")
        previous = rows(database, "SELECT updated_at FROM progress WHERE chapter_id=?", (chapter,))
        if len(previous) != 1:
            raise ValueError("The genuine Reader did not record its accepted progress")
        previous_progress = previous[0][0]
        # A distinct integer-second progress write is an accepted-image signal,
        # not a synthetic lease. The live client pixels are checked independently.
        until = time.monotonic() + 3
        while int(time.time()) <= previous_progress and time.monotonic() < until:
            time.sleep(0.04)
        if int(time.time()) <= previous_progress:
            raise RuntimeError("The host clock did not advance beyond the baseline progress stamp")

        active = (handoff.Replay().intent("library", "open", book, wait=500)
                  .intent("detail", "open-chapter", chapter, wait=0))
        active.at = CLEANUP_AT_MS
        active.intent("settings", "detail", f"storage-book:{book}", wait=0)
        active.intent("detail", "remove-all-downloads", wait=0)
        active.intent("panel", "choose", "book-downloads-remove-confirm", wait=0)
        active.intent("detail", "continue", wait=0)
        active.at = CLEAR_AT_MS
        active.intent("settings", "detail", "clear-cache", wait=0)
        main = harness.capture("same-app-reader-cleanup-and-clear", replay=active,
                               milliseconds=CAPTURE_AT_MS, expected_route="reader", pure=True,
                               observe=observe_reader_before_cleanup(harness, baseline, database, chapter,
                                                                     previous_progress, protected, raw))
        state = main["state"]
        removal = state.get("last_book_removal") or {}
        harness.check(main, "The real finalized book-removal actor completed exactly one chapter",
                      removal.get("book_id") == book and removal.get("finalized") is True
                      and removal.get("removed") == 1 and removal.get("cancelled") == 0
                      and removal.get("failed") == 0 and removal.get("had_error") is False
                      and type(removal.get("completion_sequence")) is int
                      and state.get("book_removal_completions") == removal["completion_sequence"], removal)
        after_frame = state.get("displayed_reader") or {}
        harness.check(main, "The same exclusive Reader artwork remains accepted after real cleanup and clear",
                      after_frame.get("session", {}).get("chapter_id") == chapter
                      and after_frame.get("session", {}).get("page") == 2
                      and after_frame.get("first_path") == processed["path"] and not after_frame.get("second_path"), after_frame)
        cache = state.get("cache", {})
        harness.check(main, "The live Reader retains genuine leases after force clear", cache.get("leased_entries", 0) >= 2, cache)
        harness.check(main, "The removed book has no remaining durable owner or completed job",
                      rows(index, "SELECT owner,raw_key FROM pins") == []
                      and rows(database, "SELECT chapter_id FROM downloads WHERE book_id=?", (book,)) == []
                      and cache.get("pinned_entries") == 0)
        actual = [file_record(Path(item["path"])) for item in protected]
        harness.check(main, "The actual accepted raw and processed bytes survive force clear without a durable pin",
                      actual == protected, actual)
        siblings = [item for item in raw if item["position"] != 1]
        reclaimed = [item["path"] for item in siblings if not Path(item["path"]).exists()]
        harness.check(main, "Force clear actually evicted every unshown raw sibling as its positive control",
                      len(reclaimed) == 5 and cache.get("evictions", 0) >= 5, {"reclaimed": reclaimed, "cache": cache})
        harness.persist_case(main)
        if not main["passed"]:
            raise RuntimeError("The same-process live Reader lease contract failed")

        after_exit = harness.capture("clear-after-reader-process-exit", "settings",
                                     replay=handoff.Replay().intent("settings", "detail", "clear-cache", wait=1500),
                                     milliseconds=4000, expected_route="settings")
        harness.check(after_exit, "A fresh non-Reader process has no retained Reader leases",
                      after_exit["state"].get("cache", {}).get("leased_entries") == 0,
                      after_exit["state"].get("cache"))
        harness.check(after_exit, "Only after the real Reader process exits can force clear reclaim its former raw and derivative",
                      all(not Path(item["path"]).exists() for item in protected)
                      and after_exit["state"].get("cache", {}).get("evictions", 0) >= 2,
                      [{"path": item["path"], "exists": Path(item["path"]).exists()} for item in protected])
        harness.persist_case(after_exit)
        if not after_exit["passed"]:
            raise RuntimeError("Post-Reader forced reclamation failed")
        with provider.lock:
            requests = [dict(item) for item in provider.requests]
        save(harness.output / "provider-requests.json", requests)
        if not requests or not all(item.get("authorized") is True for item in requests):
            raise ValueError("The loopback fixture traffic did not preserve its configured credential")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expected-source-file-count", type=int, default=225)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=90)
    arguments = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run this script only on the designated Linux worker through ssh test-env")
    if arguments.expected_source_file_count < 1:
        parser.error("The expected production source file count must be positive")
    if not os.environ.get("DISPLAY") or any(shutil.which(name) is None for name in ("Xvfb", "xdotool")):
        parser.error("Run inside an isolated xvfb-run display with Xvfb and xdotool available")
    arguments.output = arguments.output.resolve()
    if arguments.output.exists():
        parser.error("Use a fresh output directory so failed attempts and original evidence remain intact")
    arguments.output.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {"kind": "same-process-live-reader-lease-correctness", "passed": False,
                              "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                              "source_root": str(arguments.source_root.resolve()),
                              "scope": "One real Reader application and its download/SourceManager worker handles share the process-local canonical cache registry. Independent GUI processes and continuous physical display are not tested or claimed.",
                              "fixture_state_policy": "Only loopback source configuration is direct SQL input; pages, downloads, pins, progress, and leases come from actual application paths.",
                              "visual_round_credit": 0}
    harness: Harness | None = None
    tools = [Path(__file__), ROOT / "scripts/verify-handoff-ui.py", ROOT / "scripts/verify-ui.py",
             ROOT / "scripts/verify-fullscreen.py", ROOT / "scripts/handoff_c4_business.py",
             ROOT / "scripts/verify-cache-ui.py"]
    tools.extend(path for path in (ROOT / "scripts/handoff_c5_ui.py", ROOT / "scripts/handoff_c5_reader.py")
                 if path.is_file())
    before: dict[str, Any] | None = None
    before_tools: list[dict[str, Any]] = []
    try:
        before_tools = [file_record(path) for path in tools]
        before = handoff.source_manifest(arguments.source_root)
        save(arguments.output / "source-before.json", before)
        if (before["sha256"] != arguments.expected_source_sha256
                or before["file_count"] != arguments.expected_source_file_count):
            raise ValueError("The source does not match the authorized frozen production candidate")
        report["source_sha256"] = before["sha256"]
        report["source_file_count"] = before["file_count"]
        harness = Harness(arguments)
        report["binary_sha256"] = harness.binary_before
        run(harness)
        report.update({"passed": all(case["passed"] for case in harness.cases),
                       "application_stage_count": len(harness.cases)})
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    try:
        after = handoff.source_manifest(arguments.source_root)
        save(arguments.output / "source-after.json", after)
        after_tools = [file_record(path) for path in tools]
        report.update({"source_sha256_after": after["sha256"], "source_unchanged": before == after,
                       "tool_files_before": before_tools, "tool_files_after": after_tools,
                       "tools_unchanged": before_tools == after_tools})
        if before != after or before_tools != after_tools:
            report.update(passed=False, error="Production source or verification tools changed during the lease check")
    except Exception as error:
        report.update(passed=False, final_identity_error=f"{type(error).__name__}: {error}")
    if harness is not None:
        report["cases"] = harness.cases
        report["binary_sha256_after"] = digest(harness.binary)
        report["binary_unchanged"] = report["binary_sha256_after"] == harness.binary_before
        if not report["binary_unchanged"]:
            report["passed"] = False
    report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save(arguments.output / "report.json", report)
    print(json.dumps({"passed": report["passed"], "output": str(arguments.output), "error": report.get("error")}, ensure_ascii=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
