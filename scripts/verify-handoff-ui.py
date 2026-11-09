#!/usr/bin/env python3
"""Capture the complete handoff matrix from a frozen native Slint executable.

Run only on the designated Linux worker after the root agent authorizes a frozen
candidate. This script never builds software and rejects Windows execution. The
original application PNGs, sidecars, action files and logs are retained unchanged.
Fixture downloads use a real loopback Komga API and real image bytes; no download
state, account, connection success or screenshot pixels are fabricated.

An independent reviewer must inspect every generated review case. Mechanical
checks do not establish visual acceptance. Use --record-review after inspection;
two consecutive complete fresh rounds require identical source, binary and design
hashes, no new actionable findings, and no unresolved actionable findings.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import sys
import threading
import time
import zipfile
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageChops, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
SCOPE = ROOT / "docs/review/phase3-reference-scope.json"
PLAN = ROOT / "docs/review/phase3-handoff-capture-plan.json"
SOURCE_FILES = ("Cargo.toml", "Cargo.lock", "build.rs", "rust-toolchain.toml", ".cargo/config.toml")
SOURCE_DIRECTORIES = ("src", "ui", "assets", "vendor", "examples")
SETTINGS = (
    "screen-refresh", "frontlight-warmth", "wifi-sleep-exit", "storage",
    "default-direction", "tap-regions", "image-processing", "spread", "categories",
    "auto-update", "source-lists", "downloads", "tracking", "backup", "language",
    "logs", "about", "pen",
)
SECTION_ALIASES = {
    "display": "screen-refresh", "power": "wifi-sleep-exit", "reading": "default-direction",
    "tap-zones": "tap-regions", "image": "image-processing", "light": "frontlight-warmth",
    "download": "downloads", "tracking-login": "tracking",
}
ROUTES = {
    "library": "library", "manga-detail": "detail", "reader": "reader",
    "source-management": "sources", "source-catalog": "catalog", "search": "search",
    "history": "history", "settings": "settings",
}
SORTS = (
    "title", "recent-read", "recent-open", "recent-update", "added-at",
    "latest-chapter", "unread-count", "chapter-count",
)
FILTERS = ("downloaded", "unread", "tracked", "completed")
EXTERNAL_PERFORMANCE_COVERAGE = {
    "visible-cover-work", "processed-cache-reuse", "distinct-download-usage",
    "coalesced-queue-pressure", "retained-artwork-sleep-wake",
}


def load_module(name: str, filename: str) -> Any:
    specification = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load the existing native capture helper: {filename}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


native = load_module("handoff_native_capture", "verify-ui.py")
oracle = load_module("handoff_processed_oracle", "verify-fullscreen.py")
c4_business = load_module("handoff_c4_business", "handoff_c4_business.py")
c5_ui = load_module("handoff_c5_ui", "handoff_c5_ui.py")
c5_reader = load_module("handoff_c5_reader", "handoff_c5_reader.py")
c10_search = load_module("handoff_c10_search", "handoff_c10_search.py")
c13_scope = load_module("handoff_c13_scope", "handoff_c13_scope.py")


class Replay(native.Replay):
    def type_text(self, text: str, wait: int = 70) -> "Replay":
        """Use actual single-key intents; the application rejects multi-key text."""
        for character in text:
            self.intent("keyboard", "key", character, wait=wait)
        return self


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_manifest(directory: Path) -> dict[str, Any]:
    directory = directory.resolve(strict=True)
    files = [directory / name for name in SOURCE_FILES if (directory / name).is_file()]
    for name in SOURCE_DIRECTORIES:
        root = directory / name
        if not root.is_dir():
            raise ValueError(f"The source snapshot lacks a production directory: {root}")
        files.extend(path for path in root.rglob("*") if path.is_file())
    records = [
        {"path": path.relative_to(directory).as_posix(), "bytes": path.stat().st_size, "sha256": file_hash(path)}
        for path in sorted(files, key=lambda path: path.relative_to(directory).as_posix())
    ]
    return {
        "root": str(directory), "algorithm": "sha256-of-canonical-sorted-relative-file-records-v1",
        "included_files": list(SOURCE_FILES), "included_directories": list(SOURCE_DIRECTORIES),
        "file_count": len(records), "files": records, "sha256": stable_hash(records),
    }


def compound_id(source: str, remote: str) -> str:
    return f"{len(source.encode('utf-8'))}:{source}:{remote}"


class KomgaFixture:
    """Serve ordinary provider data and delayed image responses on loopback only."""

    def __init__(self, *, page_delay: float = 0.0, fail: bool = False, empty: bool = False,
                 recover_after_failure: bool = False, search_delay: float = 0.0,
                 stress: bool = False, recovery_delay: float = 0.0, shared_graphql: bool = False,
                 title_hint_triplet: bool = False):
        self.page_delay = page_delay
        self.fail = fail
        self.empty = empty
        self.recover_after_failure = recover_after_failure
        self.search_delay = search_delay
        self.stress = stress
        self.recovery_delay = recovery_delay
        self.shared_graphql = shared_graphql
        self.title_hint_triplet = title_hint_triplet
        self.requests: list[dict[str, Any]] = []
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.series_count = 40
        self.chapter_count = 8
        self.page_count = 6
        self.images: dict[int, bytes] = {}
        for number in range(self.page_count + 1):
            if shared_graphql:
                image = Image.new("L", (8, 12), 51 + number * 17)
                encoded = io.BytesIO()
                image.save(encoded, format="PNG")
                self.images[number] = encoded.getvalue()
                continue
            image = Image.new("L", (600, 800), 255)
            draw = ImageDraw.Draw(image)
            draw.rectangle((24, 24, 575, 775), outline=0, width=5)
            draw.rectangle((64, 86, 535, 260 + number * 25), fill=0 if number % 2 else 85)
            draw.text((64, 700), f"LOCAL HTTP FIXTURE PAGE {number}", fill=0)
            encoded = io.BytesIO()
            image.save(encoded, format="PNG")
            self.images[number] = encoded.getvalue()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_: Any) -> None:
                pass

            def respond(self, status: int, body: Any, content_type: str = "application/json") -> None:
                encoded = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def record(self, method: str, path: str, body: Any = None) -> bool:
                public = urlsplit(path).path == "/index.json"
                authorized = (public or self.headers.get("X-API-Key") == "handoff-public-fixture-key"
                              or self.headers.get("Authorization") == "Bearer handoff-public-fixture-key")
                with owner.lock:
                    owner.requests.append({
                        "at_seconds": round(time.monotonic() - owner.started, 4),
                        "method": method, "path": path, "body": body,
                        "authorized": authorized, "fixture_failure": owner.fail,
                        "authorization_required": not public,
                    })
                return authorized

            def do_POST(self) -> None:
                target = urlsplit(self.path)
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 65536:
                    self.respond(400, {})
                    return
                try:
                    body = json.loads(self.rfile.read(size))
                except (ValueError, UnicodeDecodeError):
                    self.respond(400, {})
                    return
                if not self.record("POST", self.path, body):
                    self.respond(401, {})
                    return
                if owner.fail:
                    if owner.recover_after_failure:
                        owner.fail = False
                        owner.search_delay = owner.recovery_delay
                    self.respond(503, {"error": "The loopback fixture intentionally rejects this request"})
                    return
                if target.path == "/api/graphql" and owner.shared_graphql:
                    query = body.get("query", "")
                    request = body.get("variables", {}).get("input", {})
                    if "fetchSourceManga" in query:
                        mangas = [{"id": index, "title": f"Shared fixture book {index:02}", "author": "Fixture Author",
                                   "description": "A real provider record with genuinely shared page URLs.",
                                   "genre": ["Shared", "Small files"], "status": "ONGOING",
                                   "thumbnailUrl": f"/api/v1/series/series-{index:03}/thumbnail"}
                                  for index in range(1, owner.series_count + 1)]
                        self.respond(200, {"data": {"fetchSourceManga": {"mangas": mangas, "hasNextPage": False}}})
                    elif "fetchMangaAndChapters" in query:
                        book = int(request.get("id", 1))
                        chapters = [{"id": book * 100 + number, "name": f"Shared fixture chapter {number}",
                                     "chapterNumber": number, "uploadDate": "1793700000000",
                                     "pageCount": owner.page_count, "sourceOrder": number}
                                    for number in range(1, 4)]
                        self.respond(200, {"data": {"fetchMangaAndChapters": {"chapters": chapters}}})
                    elif "fetchChapterPages" in query:
                        chapter = int(request.get("chapterId", 101))
                        prefix = f"/exclusive/{chapter}" if chapter % 100 == 2 else "/shared"
                        pages = [f"{prefix}/page-{number}.png" for number in range(1, owner.page_count + 1)]
                        self.respond(200, {"data": {"fetchChapterPages": {"pages": pages, "chapter": {"pageCount": owner.page_count}}}})
                    else:
                        self.respond(400, {"errors": [{"message": "Unsupported fixture query"}]})
                    return
                if target.path == "/api/v1/series/list":
                    if owner.search_delay:
                        time.sleep(owner.search_delay)
                    page = int(parse_qs(target.query).get("page", ["0"])[0])
                    count = 0 if owner.empty else owner.series_count
                    query = body.get("fullTextSearch")
                    exact = re.match(r"^Local fixture series (\d{3})(?:$| with )", query) if isinstance(query, str) else None
                    exact_index = int(exact.group(1)) if exact else None
                    indices = list(range(page * 20, min((page + 1) * 20, count)))
                    if exact_index is not None:
                        indices = [exact_index] if page == 0 and exact_index < count else []
                        count = 1 if exact_index < count else 0
                        if owner.title_hint_triplet and page == 0 and indices:
                            indices = [exact_index, exact_index + 1, exact_index + 2]
                            count = 3
                    rows = []
                    for index in indices:
                        rows.append({
                            "id": f"series-{index:03}", "name": f"Local fixture series {index:03}",
                            "booksCount": owner.chapter_count,
                            "metadata": {
                                "title": f"Local fixture series {index:03}" + (" with an intentionally long title that must wrap or elide without colliding with adjacent controls" * 3 if owner.stress else ""),
                                "summary": "An actual loopback provider record used for native capture. " * 24,
                                "status": "ONGOING" if index % 2 == 0 else "ENDED",
                                "genres": ["Adventure", "Mystery"],
                                "tags": [f"Actual tag {number:02}" for number in range(32)] if owner.stress else ["Local", "Pagination", "Long metadata"],
                            },
                            "booksMetadata": {"authors": [{"role": "writer", "name": "Fixture Author"}]},
                        })
                        if owner.title_hint_triplet and exact_index is not None:
                            rows[-1]["metadata"]["title"] = (f"LOCAL FIXTURE SERIES\u2014{exact_index:03}" if index == exact_index else
                                                               f"Local fixture series {exact_index + 1:03}" if index == exact_index + 1 else
                                                               "Unrelated fixture title")
                    self.respond(200, {"content": rows, "number": page, "size": 20,
                                        "totalPages": (count + 19) // 20, "totalElements": count,
                                        "last": (page + 1) * 20 >= count})
                elif target.path == "/api/v1/books/list":
                    conditions = body.get("condition", {}).get("allOf", [])
                    series = next((part["seriesId"]["value"] for part in conditions if "seriesId" in part), "series-000")
                    rows = [{
                        "id": f"{series}-chapter-{number:02}", "name": f"Chapter {number}",
                        "metadata": {"numberSort": number, "title": f"Actual local fixture chapter {number}" + (" with a deliberately long chapter title that remains readable within its actual row" * 5 if owner.stress else ""),
                                     "releaseDate": f"2026-11-{number:02}"},
                        "media": {"mediaProfile": "DIVINA", "pagesCount": owner.page_count},
                        "readProgress": {"completed": False},
                    } for number in range(1, owner.chapter_count + 1)]
                    self.respond(200, {"content": rows})
                else:
                    self.respond(404, {})

            def do_GET(self) -> None:
                target = urlsplit(self.path)
                if not self.record("GET", self.path):
                    self.respond(401, {})
                    return
                if target.path == "/index.json":
                    self.respond(200, {"name": "Actual local fixture repository", "sources": []})
                elif owner.fail:
                    self.respond(503, {})
                elif target.path.startswith("/api/v1/series/") and target.path.endswith("/thumbnail"):
                    self.respond(200, owner.images[0], "image/png")
                elif target.path.startswith("/api/v1/books/") and target.path.endswith("/pages"):
                    self.respond(200, [{"number": number, "mediaType": "image/png"}
                                       for number in range(1, owner.page_count + 1)])
                elif target.path.startswith("/api/v1/books/") and "/pages/" in target.path:
                    number = int(target.path.rsplit("/", 1)[-1])
                    if owner.page_delay:
                        time.sleep(owner.page_delay)
                    self.respond(200, owner.images[number], "image/png")
                elif target.path.startswith("/shared/page-") or target.path.startswith("/exclusive/"):
                    number = int(target.path.rsplit("-", 1)[-1].split(".", 1)[0])
                    if target.path.startswith("/exclusive/") and owner.page_delay:
                        time.sleep(owner.page_delay)
                    self.respond(200, owner.images[number], "image/png")
                else:
                    self.respond(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)

    def __enter__(self) -> "KomgaFixture":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"


class Suite(native.Suite):
    def __init__(self, args: argparse.Namespace):
        if (args.output / "manifest.json").exists():
            raise ValueError("A capture output already contains a manifest; choose a fresh round directory")
        args.output_dir = args.output
        super().__init__(args)
        self.scope = read_json(args.scope)
        self.plan = read_json(args.plan)
        self.source = source_manifest(args.source_root)
        if args.expected_sha256 and self.binary_hash != args.expected_sha256.lower():
            raise ValueError("The candidate binary does not match --expected-sha256")
        if args.expected_source_sha256 and self.source["sha256"] != args.expected_source_sha256.lower():
            raise ValueError("The production snapshot does not match --expected-source-sha256")
        save_json(self.output / "source-files.json", self.source)
        self.design = self.reference_manifest(args.reference_root)
        save_json(self.output / "reference-files.json", self.design)
        self.reference_ids: list[str] = []
        self.coverage: dict[str, str] = {}
        self.supplemental_ids: list[str] = []
        self.sample_by_profile: dict[str, dict[str, Any]] = {}
        self.fixture_index = 0
        self.completed_groups: list[str] = []
        self.required_groups = [f"{name}:{device}" for device in ("pw5", "scribe") for name in (
            "all-settings-sections", "library-sort-and-tristate", "keyboard-and-search",
            "list-and-detail-boundaries", "catalog-filter-and-source-tabs", "history-operation",
            "truthful-source-and-migration", "real-http-search", "content-stress",
        )] + ["reader-input-and-pure-scene", "truthful-hardware", "profile-stretch"]

    def reference_manifest(self, root: Path) -> dict[str, Any]:
        root = root.resolve(strict=True)
        references: dict[str, list[str]] = {}
        for board in self.scope["pageStateMatrix"]:
            for entry in [board["default"], *board["states"]]:
                references.setdefault(entry["reference"], []).append(entry["id"])
        for item in self.scope.get("supportReferences", []):
            paths = item.get("files", [item["file"]] if item.get("file") else [])
            if isinstance(paths, list):
                for path in paths:
                    references.setdefault(path, [])
        files = []
        for relative, identifiers in sorted(references.items()):
            path = root / relative
            if not path.is_file():
                raise ValueError(f"A supplied handoff reference is missing: {path}")
            with Image.open(path) as original:
                size = list(original.size)
            files.append({"path": relative, "bytes": path.stat().st_size, "sha256": file_hash(path),
                          "dimensions": size, "reference_ids": identifiers})
        return {"root": str(root), "sha256": stable_hash(files), "files": files,
                "scope_sha256": file_hash(self.args.scope), "plan_sha256": file_hash(self.args.plan)}

    def freeze_check(self) -> None:
        if file_hash(self.binary) != self.binary_hash:
            raise ValueError("The frozen binary changed during this capture round")

    def c4_api(self) -> Any:
        return SimpleNamespace(Fixture=KomgaFixture, Replay=Replay, compound_id=compound_id,
                               save_json=save_json, ui_label=native.ui_label)

    def inspect_png(self, case: dict[str, Any], path: Path) -> None:
        with Image.open(path) as original:
            self.check(case, "Capture is encoded as PNG", original.format == "PNG", original.format)
            width, height = native.PROFILES[case["device"]]
            if case["landscape"]:
                width, height = height, width
            self.check(case, "PNG dimensions match the logical viewport", original.size == (width, height), list(original.size))
            for key in ("window_size", "snapshot_size"):
                self.check(case, f"Native metadata {key} matches the actual PNG", case["metadata"].get(key) == list(original.size), case["metadata"].get(key))
            rgba = original.convert("RGBA")
            self.check(case, "Every captured pixel is opaque", rgba.getchannel("A").getextrema() == (255, 255), list(rgba.getchannel("A").getextrema()))
            red, green, blue, _ = rgba.split()
            channel_error = max(ImageChops.difference(red, green).getextrema()[1], ImageChops.difference(red, blue).getextrema()[1])
            self.check(case, "The e-ink interface has no color-channel tint", channel_error <= 2, channel_error)
            histogram = rgba.convert("L").histogram()
            dark = sum(histogram[:96])
            state = case.get("state", {})
            committed_artwork = (state.get("route") == "reader" and state.get("menu") is False
                                 and state.get("modal") == "" and state.get("reader_image_ready") is True
                                 and isinstance(state.get("displayed_reader"), dict))
            if committed_artwork:
                case["artwork_content_contract"] = "Committed source identity and strict whole-viewport pixel composition; legitimate bright or white source pages need no dark-ink quota."
            else:
                minimum = 0 if case.get("intentional_inflight_capture") else width * height * 0.002
                self.check(case, "The screenshot contains visible interface content", dark > minimum, dark)
            case["image"] = {"size": [width, height], "distinct_gray_levels": sum(count > 0 for count in histogram),
                             "dark_fraction": round(dark / (width * height), 6)}

    def run_native(
        self, name: str, screen: str, *, device: str, landscape: bool, replay: Any,
        data: Path | None, demo: bool, offline: bool, resume: bool,
        expected_route: str, capture_delay: int, expected_landscape: bool | None,
        import_path: Path | None, allow_busy: bool, capture_at_ms: int | None = None,
    ) -> dict[str, Any]:
        data = data or self.fresh(name)
        screenshot = self.output / f"{name}.png"
        action_path = self.output / "actions" / f"{name}.json"
        actions = replay.events if replay else []
        save_json(action_path, actions)
        final_action = replay.at if replay else 450
        deadline = max(1300, final_action + capture_delay)
        if screen == "reader" or expected_route == "reader":
            deadline = max(deadline, 5000 if device.startswith("scribe") else 4000, final_action + 1600)
        if capture_at_ms is not None:
            if capture_at_ms < 1:
                raise ValueError("An explicit initial-state capture deadline must be positive")
            deadline = capture_at_ms
        command = [
            "xvfb-run", "-a", "-s", "-screen 0 2200x3000x24", str(self.binary),
            "--device", device, "--screen", screen, "--data-dir", str(data),
            "--capture", str(screenshot), "--capture-settle-covers",
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
        environment = os.environ.copy()
        environment.update({
            "SLINT_SCALE_FACTOR": "1", "SLINT_BACKEND": "winit-software",
            "SLINT_RENDERER": "software", "RUST_BACKTRACE": "1",
            "XDG_RUNTIME_DIR": str(self.runtime_dir), "TZ": "Asia/Shanghai",
        })
        case: dict[str, Any] = {
            "name": name, "device": device, "screen": screen, "initial_landscape": landscape,
            "landscape": landscape if expected_landscape is None else expected_landscape,
            "passed": True, "checks": [], "screenshot": str(screenshot), "data_dir": str(data),
            "actions": str(action_path), "pointer_events": sum(action["section"] == "pointer" for action in actions),
            "exit_after_ms": deadline, "capture_settle_covers": True,
            "intentional_inflight_capture": allow_busy, "command": command, "state": {}, "metadata": {},
        }
        self.cases.append(case)
        started = time.monotonic()
        print(f"RUN {name}", flush=True)
        try:
            exit_code, log = oracle.completed_process(command, environment, self.args.timeout)
            (self.output / f"{name}.log").write_bytes(log)
            case["exit_code"] = exit_code
            self.check(case, "The native application process exits successfully", exit_code == 0, exit_code)
            for kind in ("metadata", "state"):
                path = screenshot.with_suffix(f".{kind}.json")
                self.check(case, f"The application generates its own {kind} sidecar", path.is_file(), str(path))
                if path.is_file():
                    case[kind] = read_json(path)
            self.check(case, "The application generates the original native PNG", screenshot.is_file(), str(screenshot))
            if screenshot.is_file():
                self.inspect_png(case, screenshot)
            metadata = case["metadata"]
            width, height = native.PROFILES[device]
            if case["landscape"]:
                width, height = height, width
            self.check(case, "Capture metadata identifies the actual requested native profile", metadata.get("profile") == device, metadata.get("profile"))
            self.check(case, "Capture metadata identifies the real desktop backend", metadata.get("backend") == "desktop", metadata.get("backend"))
            self.check(case, "Logical geometry preserves the actual device viewport", metadata.get("logical_size") == [width, height], metadata.get("logical_size"))
            self.check(case, "The native capture uses unit display scale", metadata.get("scale_factor") == 1, metadata.get("scale_factor"))
            self.expect(case, "route", expected_route)
            self.expect(case, "demo", demo)
            self.expect(case, "offline", offline)
            if not allow_busy:
                self.expect(case, "busy", False)
        except Exception as error:
            self.check(case, "The native capture completes without a harness error", False, f"{type(error).__name__}: {error}")
        case["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(f"{'PASS' if case['passed'] else 'FAIL'} {name} ({case['elapsed_seconds']}s)", flush=True)
        return case

    def capture(
        self, name: str, screen: str = "library", *, device: str = "pw5", landscape: bool = False,
        replay: Any = None, expected: dict[str, Any] | None = None,
        context: str | None = None, modal: str | None = None,
        reference: dict[str, Any] | None = None, note: str | None = None,
        data: Path | None = None, demo: bool = True, offline: bool = True,
        pure: bool = False, prerequisite: bool = False, capture_delay: int = 1000,
        expected_route: str | None = None, resume: bool = False,
        expected_landscape: bool | None = None, import_path: Path | None = None,
        allow_busy: bool = False, capture_at_ms: int | None = None,
    ) -> dict[str, Any]:
        self.freeze_check()
        # A committed-reader case must retain strict identity and pixel checks.
        # Give unoptimized large-frame workers a bounded idle tail instead of
        # accepting a pending or stale displayed frame as a successful capture.
        settled_reader = (pure or screen == "reader" or expected_route == "reader") and not allow_busy
        if settled_reader:
            capture_delay = max(capture_delay, self.args.settled_reader_tail_ms)
        case = self.run_native(
            name, screen, device=device, landscape=landscape, replay=replay,
            data=data, demo=demo, offline=offline, resume=resume,
            expected_route=expected_route or screen, capture_delay=capture_delay,
            expected_landscape=expected_landscape, import_path=import_path, allow_busy=allow_busy,
            capture_at_ms=capture_at_ms,
        )
        self.freeze_check()
        case["round"] = self.args.round
        case["run_id"] = self.run_id
        case["capture_method"] = "native Slint application --capture; unmodified original PNG"
        case["binary_sha256"] = self.binary_hash
        case["source_sha256"] = self.source["sha256"]
        case["settled_reader_tail_ms"] = self.args.settled_reader_tail_ms if settled_reader else None
        logical = case.get("metadata", {}).get("logical_size", [])
        case["expected_physical_size"] = [int(value) * 2 for value in logical]
        case["native_capture_size"] = logical
        case["physical_reference_scale"] = 2
        case["prerequisite"] = prerequisite
        case["manual_review_required"] = not prerequisite
        case["intentional_differences"] = [note] if note else []
        state = case.get("state", {})
        self.check(case, "Read-only UI diagnostics are present", state.get("diagnostics_available") is True,
                   state.get("diagnostics_available"))
        self.check(case, "The metadata route matches the actual route", case.get("metadata", {}).get("route") == state.get("route"),
                   case.get("metadata", {}).get("route"))
        self.check(case, "The capture records the actual profile capability declaration",
                   isinstance(state.get("capability_declaration"), dict), state.get("capability_declaration"))
        self.check(case, "Visible cover work and its UI projection are settled before the native capture",
                   state.get("cover_work_pending") is False and state.get("capture_projection_pending") is False
                   and state.get("cover_inflight") is False and state.get("cover_queued_count") == 0,
                   {key: state.get(key) for key in ("cover_work_pending", "capture_projection_pending", "cover_inflight", "cover_queued_count", "cover_visible_count", "cover_visible_failure_count")})
        if state.get("cover_visible_failure_count", 0):
            case["intentional_differences"].append("A real visible cover failed and settled in backoff; the native placeholder is retained truthfully rather than painted over.")
        if state.get("route") != "reader":
            self.check(case, "Every non-reader route returns to portrait", state.get("display_landscape") is False,
                       state.get("display_landscape"))
        if context is not None:
            self.expect(case, "modal_context", context)
        if modal is not None:
            self.expect(case, "modal", modal)
        for key, value in (expected or {}).items():
            self.expect(case, key, value)
        self.inspect_diagnostics(case)
        if reference is not None:
            identifier = reference["id"]
            case["reference_id"] = identifier
            case["reference"] = str(self.args.reference_root.resolve() / reference["reference"])
            case["reference_sha256"] = file_hash(Path(case["reference"]))
            case["reference_applicability"] = reference.get("applicability", "applicable")
            case["reference_expected"] = reference.get("expected")
            if identifier.endswith("--fullscreen-footer"):
                case["reference_role"] = "reference-only alias of the user-overridden artwork-only scene"
            self.coverage[identifier] = name
        elif not prerequisite:
            self.supplemental_ids.append(name)
        actual_closed_reader = (state.get("route") == "reader" and state.get("menu") is False
                                and state.get("modal") == "" and state.get("reader_image_ready") is True)
        if pure or (actual_closed_reader and not allow_busy):
            self.inspect_pure_reader(case)
        for kind, path in {
            "screenshot": Path(case["screenshot"]),
            "state": Path(case["screenshot"]).with_suffix(".state.json"),
            "metadata": Path(case["screenshot"]).with_suffix(".metadata.json"),
            "actions": Path(case["actions"]), "log": self.output / f"{name}.log",
        }.items():
            if path.is_file():
                case.setdefault("evidence_hashes", {})[kind] = {"path": str(path), "sha256": file_hash(path)}
        self.persist_case(case)
        return case

    def persist_case(self, case: dict[str, Any]) -> None:
        save_json(self.output / "cases" / f"{case['name']}.json", case)

    def visible_outline(self, case: dict[str, Any], rectangle: tuple[int, int, int, int]) -> None:
        """Check real native border pixels; never draw or repair the screenshot."""
        x, y, width, height = rectangle
        with Image.open(case["screenshot"]) as original:
            gray = original.convert("L")
            if x < 0 or y < 0 or x + width > gray.width or y + height > gray.height:
                self.check(case, "The required visible frame fits inside the native viewport", False, list(rectangle))
                return
            edges = {
                "top": [(column, y) for column in range(x + 8, x + width - 8)],
                "bottom": [(column, y + height - 2) for column in range(x + 8, x + width - 8)],
                "left": [(x, row) for row in range(y + 8, y + height - 8)],
                "right": [(x + width - 2, row) for row in range(y + 8, y + height - 8)],
            }
            fractions = {}
            for name, points in edges.items():
                dark = sum(min(gray.getpixel((min(gray.width - 1, px + dx), min(gray.height - 1, py + dy)))
                               for dx in (0, 1) for dy in (0, 1)) < 96 for px, py in points)
                fractions[name] = dark / max(1, len(points))
            case.setdefault("native_visible_regions", []).append({"rectangle": list(rectangle), "edge_ink_fractions": fractions})
            self.check(case, "The required native surface has a complete visible outline", all(value >= 0.8 for value in fractions.values()), fractions)

    def c5_visible_reference_controls(self, case: dict[str, Any], screen: str, suffix: str) -> None:
        """Inspect native pixels for C5 controls while retaining the original references."""
        large = case["device"] == "scribe"
        if screen == "catalog" and suffix in ("default", "filter"):
            self.expect(case, "catalog_active_filter_count", 0)
            x, y = (854, 175) if large else (510, 28)
            with Image.open(case["screenshot"]) as original:
                gray = original.convert("L")
                positions = [(x + dx, y + dy) for dx in (*range(5, 11), *range(37, 43))
                             for dy in (*range(5, 11), *range(37, 43))]
                white = sum(gray.getpixel(point) >= 250 for point in positions) / len(positions)
            self.check(case, "A neutral real filter trigger keeps its native white surface even while its panel is open",
                       white >= 0.95, {"rectangle": [x, y, 48, 48], "white_interior_fraction": white})
            case.setdefault("visible_ui_requirements", []).append("The real filter trigger stays neutral with zero active filters; native captions and intrinsic chips remain readable.")
            case.setdefault("native_visible_regions", []).append({"purpose": "neutral-filter-trigger", "rectangle": [x, y, 48, 48],
                                                                      "white_interior_fraction": white})
        if screen == "settings" and case["state"].get("settings_section") == "screen-refresh":
            x, y = (838, 599.5) if large else (546, 446)
            knob_x = x + (27 if large else 22)
            samples = {
                "on-track-fill": (round(x + 7), round(y + 9), 6, 6),
                "disabled-right-knob": (round(knob_x + 6), round(y + 9), 6, 6),
            }
            with Image.open(case["screenshot"]) as original:
                gray = original.convert("L")
                fractions = {}
                for label, (left, top, width, height) in samples.items():
                    pixels = [gray.getpixel((column, row)) for row in range(top, top + height)
                              for column in range(left, left + width)]
                    fractions[label] = sum(value < 32 if label == "on-track-fill" else 140 <= value <= 165
                                           for value in pixels) / len(pixels)
            self.check(case, "The native fixed-on chapter refresh policy displays an on track with a disabled right-hand knob",
                       all(value >= 0.95 for value in fractions.values()), {"samples": samples, "fractions": fractions})
            case.setdefault("native_visible_regions", []).append({"purpose": "fixed-on-chapter-full-refresh", "samples": samples,
                                                                      "fractions": fractions})
            case.setdefault("visible_ui_requirements", []).append("The actual chapter full-refresh row reads Fixed on and is visibly on and unavailable for editing; no new preference is invented.")
        if case["name"] == "Main--more":
            case.setdefault("visible_ui_requirements", []).append("The actual Paperwhite Manage categories row has its native list glyph and the established real action target.")

    def scope_more_position(self, case: dict[str, Any]) -> tuple[float, float]:
        large = case["device"] == "scribe"
        margin, status, row, touch, border = (36, 36, 90, 56, 44) if large else (16, 28, 63, 48, 38)
        with Image.open(case["screenshot"]) as original:
            gray = original.convert("L")
            top, bottom = status + row, status + row + touch
            pixels = {(x, y) for y in range(top, bottom) for x in range(margin, gray.width - margin)
                      if gray.getpixel((x, y)) < 224}
            boxes = []
            while pixels:
                first = pixels.pop()
                stack = [first]
                left = right = first[0]
                upper = lower = first[1]
                while stack:
                    x, y = stack.pop()
                    left, right, upper, lower = min(left, x), max(right, x), min(upper, y), max(lower, y)
                    for point in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1),
                                  (x - 1, y - 1), (x + 1, y - 1), (x - 1, y + 1), (x + 1, y + 1)):
                        if point in pixels:
                            pixels.remove(point)
                            stack.append(point)
                if right - left + 1 >= touch - 6 and lower - upper + 1 >= border - 6:
                    boxes.append((left, upper, right + 1, lower + 1))
            boxes.sort()
        minimum = 6 if large else 4
        self.check(case, "The actual native source shortcuts expose separate complete bordered controls including More", len(boxes) >= minimum, boxes)
        if len(boxes) < minimum:
            self.persist_case(case)
            raise ValueError("The visible More chip could not be located; a hidden semantic picker action is not substituted")
        left, upper, right, lower = boxes[-1]
        point = ((left + right) / 2, (upper + lower) / 2)
        case["native_scope_shortcuts"] = {"observed_region": [margin, status + row, gray.width - margin * 2, touch],
                                           "border_components": boxes, "more_hit": list(point),
                                           "method": "Connected components of the unchanged native control outlines, including antialiased edge pixels."}
        self.persist_case(case)
        return point

    def inspect_diagnostics(self, case: dict[str, Any]) -> None:
        state = case.get("state", {})
        if state.get("route") == "library" and state.get("visible_books"):
            self.inspect_visible_unread_store(case)
        for key, page_key, count_key in (
            ("main", "page", "page_count"), ("panel", "panel_page", "panel_page_count"),
            ("settings-details", "settings_details_page", "settings_details_page_count"),
            ("installed-sources", "installed_source_page", "installed_source_page_count"),
            ("keyboard-candidates", "keyboard_candidate_page", "keyboard_candidate_page_count"),
        ):
            page, count = state.get(page_key), state.get(count_key)
            self.check(case, f"The {key} folio stays within its real range",
                       type(page) is int and type(count) is int and 1 <= page <= count, [page, count])
        for item in state.get("download_queue", []):
            complete, total = item.get("completed"), item.get("total")
            progress = item.get("progress")
            self.check(case, "Every visible queue progress agrees with its real counters",
                       isinstance(complete, int) and isinstance(total, int) and 0 <= complete <= total
                       and isinstance(progress, (int, float)) and 0 <= progress <= 1,
                       {key: item.get(key) for key in ("id", "completed", "total", "progress", "state")})
        if state.get("keyboard_is_secret"):
            text = state.get("keyboard_text", "")
            self.check(case, "Secret input exposes only masking characters",
                       isinstance(text, str) and all(character == "\u2022" for character in text), text)
            self.expect(case, "keyboard_composition", "")
            self.expect(case, "keyboard_candidates", [])
        filters = state.get("library_filters")
        self.check(case, "Library filters use the neutral/include/exclude contract",
                   isinstance(filters, list) and len(filters) == 4 and all(value in (0, 1, -1) for value in filters), filters)
        for tag in state.get("catalog_tag_options", []):
            self.check(case, "Catalog tags use the neutral/include/exclude contract", tag.get("state") in (0, 1, -1), tag)
        if state.get("route") == "reader" and state.get("menu") is False and state.get("modal") == "":
            self.check(case, "Closed reader diagnostics contain no open overlay", state.get("reader_panel") == "", state.get("reader_panel"))
        if state.get("route") == "settings" and state.get("settings_section") == "storage" and not case.get("intentional_inflight_capture"):
            self.expect(case, "storage_ready", True)
            self.expect(case, "storage_loading", False)
            actual = c4_business._usage_observation(self, case)
            accepted = state.get("storage_details") or {}
            for key in ("downloaded_bytes", "image_cache_bytes", "cover_bytes", "books"):
                self.check(case, f"Storage {key} measures actual files even in demo mode",
                           accepted.get(key) == actual[key], {"worker": accepted.get(key), "actual_files": actual[key]})
            categories = state.get("storage_categories", {})
            for name, key in (("downloaded", "downloaded_bytes"), ("image_cache", "image_cache_bytes"), ("covers", "cover_bytes")):
                value = accepted.get(key)
                self.check(case, f"The native storage {name} legend uses measured byte text",
                           type(value) is int and categories.get(name, {}).get("text") == c4_business._bytes_text(value),
                           categories.get(name))
            if state.get("demo"):
                case.setdefault("intentional_differences", []).append("Library summary values belong to the labeled demo scenario; this Storage surface independently measures actual file bytes and never substitutes those illustrative GB values.")

    def inspect_pure_reader(self, case: dict[str, Any]) -> None:
        try:
            state = case["state"]
            self.expect(case, "route", "reader")
            self.expect(case, "menu", False)
            self.expect(case, "modal", "")
            self.expect(case, "reader_inflight", False)
            self.expect(case, "reader_dirty", False)
            self.expect(case, "reader_image_ready", True)
            displayed = state.get("displayed_reader")
            if not isinstance(displayed, dict):
                raise ValueError("The closed reader has no committed processed image evidence")
            session = displayed.get("session", {})
            for session_key, state_key in (("book_id", "current_book"), ("chapter_id", "current_chapter"),
                                            ("page", "reader_page"), ("total", "reader_total"),
                                            ("vertical_offset", "reader_offset"), ("zoom", "reader_zoom")):
                self.check(case, f"The displayed session agrees with {state_key}",
                           session.get(session_key) == state.get(state_key), session.get(session_key))
            with Image.open(case["screenshot"]) as original:
                actual = original.convert("RGBA")
            width, height = actual.size
            expected = Image.new("L", (width, height), 255)
            dual = bool(displayed.get("second_path"))
            visible_pages = state.get("reader_visible_pages")
            self.check(case, "The actual visible page set agrees with the committed one- or two-image allocation",
                       isinstance(visible_pages, list) and len(visible_pages) in (1, 2)
                       and dual == (len(visible_pages) == 2), visible_pages)
            direction = displayed.get("preferences", {}).get("reading_direction", "rtl")
            slots = [(0, 0, width, height)]
            if dual:
                first = width // 2 if direction == "rtl" else 0
                second = 0 if direction == "rtl" else width // 2
                slots = [(first, 0, width // 2, height), (second, 0, width // 2, height)]
            inputs = []
            for index, slot in enumerate(slots):
                key = "first_path" if index == 0 else "second_path"
                path = Path(displayed[key])
                if not path.is_absolute():
                    raise ValueError("A processed image path is not absolute")
                raw = path.read_bytes()
                retained = self.output / "processed" / case["name"] / f"{key}.png"
                retained.parent.mkdir(parents=True, exist_ok=True)
                retained.write_bytes(raw)
                source = oracle.opaque_grayscale_png(raw)
                geometry = oracle.draw_processed(expected, source, slot)
                inputs.append({"field": key, "application_path": str(path), "retained_path": str(retained),
                               "sha256": hashlib.sha256(raw).hexdigest(), "geometry": geometry})
            metrics, difference = oracle.difference_metrics(actual, expected)
            expected_path = self.output / "oracle" / f"{case['name']}.expected.png"
            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected.save(expected_path)
            difference_path = expected_path.with_name(f"{case['name']}.diff.png")
            difference.save(difference_path)
            case["pure_reader_oracle"] = {"sampler_contract": oracle.SAMPLER_CONTRACT,
                                          "metrics": metrics, "processed_inputs": inputs,
                                          "expected": str(expected_path), "difference": str(difference_path),
                                          "original_capture_unchanged": True}
            self.check(case, "Every closed-reader viewport pixel equals actual processed artwork with no ignored region",
                       metrics["exact_match"], metrics)
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.check(case, "The full-viewport processed-image oracle completed", False, f"{type(error).__name__}: {error}")

    def inspect_visible_unread_store(self, case: dict[str, Any]) -> None:
        """Compare actual visible library badges with their durable Book values."""
        visible = case["state"].get("visible_books", [])
        identifiers = [item.get("id") for item in visible]
        self.check(case, "Visible library unread projections have distinct actual identities",
                   all(isinstance(identifier, str) and identifier for identifier in identifiers)
                   and len(identifiers) == len(set(identifiers)), identifiers)
        if not identifiers:
            return
        placeholders = ",".join("?" for _ in identifiers)
        rows = self.database_rows(case, "SELECT id, COALESCE(json_extract(document, '$.unread'),0) FROM books WHERE id IN ("
                                  + placeholders + ")", tuple(identifiers))
        stored = dict(rows)
        self.check(case, "Every actual visible library unread badge agrees with its current Store Book",
                   set(stored) == set(identifiers) and all(type(item.get("unread")) is int
                   and item["unread"] == stored.get(item["id"]) for item in visible),
                   {"visible": [{"id": item.get("id"), "unread": item.get("unread")} for item in visible],
                    "store": stored})

    def reference_matrix(self) -> None:
        for board in self.scope["pageStateMatrix"]:
            for entry in [board["default"], *board["states"]]:
                self.reference_ids.append(entry["id"])
                if self.args.selected_cases and entry["id"] not in self.args.selected_cases:
                    continue
                try:
                    self.reference_case(board, entry)
                except Exception as error:
                    self.cases.append({"name": entry["id"], "reference_id": entry["id"], "passed": False,
                                       "manual_review_required": True, "checks": [{
                                           "description": "The native reference case completed", "passed": False,
                                           "actual": f"{type(error).__name__}: {error}"}]})

    def reference_case(self, board: dict[str, Any], entry: dict[str, Any]) -> None:
        name = entry["id"]
        suffix = name.split("--", 1)[1]
        screen = ROUTES[board["route"]]
        device = "pw5" if board["profile"] == "pw" else "scribe"
        landscape = board["orientation"] == "landscape"
        replay = Replay(start=2200 if screen == "reader" else 650)
        expected: dict[str, Any] = {}
        context = None
        modal = ""
        demo = True
        pure = False
        note = entry.get("intentionalDifference")
        if suffix == "download-queue":
            self.real_queue(name, device, entry)
            return
        if screen == "library":
            if suffix == "jump-pages":
                replay.intent("page", "jump-toggle", "page")
                expected["page_jump_open"] = True
            elif suffix == "more":
                replay.intent("library", "more")
                modal, context = "choices", "library-more"
            elif suffix == "select":
                replay.intent("library", "select", compound_id("mangadex", "demo-00"))
                expected["selection_mode"] = True
            elif suffix == "sort":
                replay.intent("library", "filter-menu")
                modal, context = "choices", "library-filter"
        elif screen == "detail":
            if suffix == "batch-download":
                replay.intent("detail", "batch-download")
                modal, context = "choices", "batch-download"
            elif suffix == "chapters-p2":
                replay.intent("page", "turn", "1")
                expected["page"] = 2
            elif suffix == "description":
                replay.intent("detail", "summary")
                modal, context = "summary", "summary"
                expected["summary_is_book"] = True
            elif suffix == "more":
                replay.intent("detail", "more")
                modal, context = "choices", "detail-more"
            elif suffix == "tracking":
                replay.intent("detail", "track")
                modal, context = "choices", "tracking"
                note = "No external tracking authorization is simulated; capture the implemented unavailable/configuration entry."
            elif suffix == "migrate":
                replay.intent("detail", "migrate", wait=1400)
                modal, context = "choices", "migration"
                note = "Candidates and operation availability are actual cache/worker results; reference identities are illustrative data."
        elif screen == "reader":
            if suffix in ("fullscreen", "fullscreen-footer"):
                pure = True
                expected["menu"] = False
                if suffix == "fullscreen-footer":
                    note = "The user overrides the reference footer and ribbon: every closed-reader pixel remains artwork only."
            else:
                replay.intent("reader", "menu")
                expected["menu"] = True
                panel = {"image": "picture", "light": "brightness", "rotate": "orientation"}.get(suffix, suffix)
                if suffix != "default":
                    replay.intent("reader", panel)
                    modal = "brightness" if panel == "brightness" else "choices"
                    context = f"reader-{panel}"
                if panel == "brightness":
                    note = "Desktop captures show declared profile lighting controls only; physical light effects are not qualified."
                elif panel == "orientation":
                    note = "Manual portrait/landscape controls remain applicable. Only sensor-follow requires an actual orientation sensor; no whole-panel exclusion is allowed."
        elif screen == "sources":
            if device == "scribe":
                replay.intent("sources", "manage")
            if suffix == "available":
                replay.intent("sources", "filter", "1")
                expected["source_filter"] = 1
            elif suffix == "updates":
                replay.intent("sources", "filter", "2")
                expected["source_filter"] = 2
            elif suffix == "source-lists":
                replay.intent("sources", "list")
                modal, context = "choices", "repository-list"
            elif suffix == "add-source":
                replay.intent("sources", "add")
                modal, context = "pairing", "pairing"
                note = "Only the application's actual local pairing endpoint may produce a URL or QR; external installation remains explicit."
        elif screen == "catalog":
            if suffix == "blocked-source":
                replay.intent("sources", "open", "demo-b")
                expected.update({"source_id": "demo-b", "source_available": False})
            elif suffix == "server-setup":
                demo = False
                replay.intent("sources", "open", "komga")
                expected.update({"source_id": "komga", "source_needs_setup": True})
                note = "Capture the actual unconfigured server; no connection or account is fabricated."
            elif suffix == "source-picker":
                replay.intent("sources", "picker")
                modal, context = "choices", "source-picker"
            elif suffix == "filter":
                replay.intent("catalog", "filter-menu")
                modal, context = "choices", "catalog-filter"
            elif suffix == "source-settings":
                replay.intent("catalog", "settings", wait=1400)
                modal, context = "choices", "source-settings"
        elif screen == "search":
            if suffix != "results":
                replay.intent("search", "edit")
                modal = "keyboard"
                expected.update({"keyboard_target": "search", "keyboard_mode": "pinyin"})
                if suffix == "numbers":
                    replay.intent("keyboard", "mode", "numbers")
                    expected["keyboard_mode"] = "numbers"
        elif screen == "history":
            if suffix == "clear-confirm":
                replay.intent("history", "clear")
                modal, context = "choices", "clear-history"
            elif suffix == "incognito":
                replay.intent("history", "incognito")
            elif suffix == "updates":
                replay.intent("history", "filter", "1")
                expected["history_filter"] = 1
        elif screen == "settings":
            if suffix == "page2":
                replay.intent("page", "turn", "1")
                expected["page"] = 2
            elif suffix != "default":
                section = SECTION_ALIASES.get(suffix, suffix)
                if section == "pen":
                    expected["settings_section"] = "screen-refresh"
                    note = "Pen input is not implemented. Capture actual settings navigation proving the absent pen entry; never force a hidden pen page or fabricate pen controls."
                else:
                    replay.intent("settings", "open", section, wait=1000)
                    expected["settings_section"] = section
            elif device == "scribe":
                expected["settings_section"] = "screen-refresh"
            if entry.get("capabilityBound") == "authentication":
                note = "Capture the actual tracking unavailable/external configuration state; no pending code or logged-in account is simulated."
            elif entry.get("capabilityBound") == "hardware" and suffix != "pen":
                note = "Capture only actual implemented controls or the real unavailable state; desktop data does not qualify pen, sensor or frontlight hardware."
        case = self.capture(name, screen, device=device, landscape=landscape, replay=replay,
                            expected=expected, modal=modal, context=context, demo=demo,
                            reference=entry, note=note, pure=pure, capture_delay=1600)
        if suffix == "incognito":
            self.preference(case, "incognito", True)
        if suffix == "source-settings":
            self.check(case, "Source settings come from actual provider declarations",
                       isinstance(case["state"].get("source_preference_groups"), list),
                       case["state"].get("source_preference_groups"))
            if not case["state"].get("source_preference_groups"):
                if device == "scribe":
                    self.visible_outline(case, (362, 112, 540, 180))
                else:
                    with Image.open(case["screenshot"]) as frame:
                        gray = frame.convert("L")
                        top_ink = sum(gray.getpixel((x, gray.height - 156)) < 96 for x in range(8, gray.width - 8))
                    self.check(case, "Empty Paperwhite source preferences have a real visible sheet boundary", top_ink >= 0.8 * (618 - 16), top_ink)
                case["visible_ui_requirements"] = ["A contained empty source-settings surface, explicit no-preferences explanation, and working Done action are visible."]
        if screen == "sources" and suffix == "updates":
            rows = case["state"].get("available_sources", [])
            changing = [row for row in rows if row.get("installed") and row.get("update")]
            self.check(case, "Update rows expose real installed-to-target version transitions",
                       bool(changing) and all("\u2192" in row.get("update_subtitle", "") for row in changing), changing)
            case["visible_ui_requirements"] = ["Installed and target versions are readable on actual updatable source rows."]
        if suffix == "source-lists":
            case["intentional_differences"].append("Repository names and counts are actual saved records; empty data is not replaced with fictional subscriptions.")
        self.c5_visible_reference_controls(case, screen, suffix)
        self.persist_case(case)
        if suffix == "default":
            self.sample_by_profile[f"{device}-{screen}-{landscape}"] = case

    def seed_provider(self, name: str, provider: KomgaFixture, device: str, *,
                      source_id: str = "komga", source_kind: str = "komga",
                      source_options: dict[str, Any] | None = None) -> tuple[Path, Path]:
        seed = self.capture(f"{name}--fixture-seed", device=device, demo=False, prerequisite=True)
        database = Path(seed["state"]["database"])
        source = {
            "id": source_id, "name": f"Local {source_kind} fixture", "kind": source_kind, "base_url": provider.url,
            "username": "", "password": "", "token": "handoff-public-fixture-key", "version": 1,
            "languages": ["en"], "enabled": True, "options": source_options or {},
        }
        # Configure fixture inputs only. Jobs, pages, progress and connection success
        # are always created by actual application workers and ordinary actions.
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM sources")
            connection.execute("INSERT INTO sources(id,document) VALUES(?,?)", (source_id, json.dumps(source)))
        save_json(self.output / "fixtures" / f"{name}.json", {
            "kind": f"loopback-{source_kind}", "base_url": provider.url,
            "source_configuration_only": True, "public_fixture_credential": True,
            "external_network_sources_enabled": False, "series_count": provider.series_count,
            "chapters_per_series": provider.chapter_count, "pages_per_chapter": provider.page_count,
            "page_response_delay_seconds": provider.page_delay,
        })
        return Path(seed["data_dir"]), database

    def save_provider(self, name: str, provider: KomgaFixture, case: dict[str, Any]) -> None:
        path = self.output / "fixtures" / f"{name}.requests.json"
        save_json(path, provider.requests)
        case["provider_requests"] = {"path": str(path), "sha256": file_hash(path)}
        case.setdefault("provider_request_evidence", []).append(case["provider_requests"])
        case.setdefault("evidence_hashes", {})[f"provider:{name}"] = case["provider_requests"]
        self.check(case, "Every fixture request stays on the loopback API and carries the configured public fixture key",
                   bool(provider.requests) and all(request["authorized"] for request in provider.requests),
                   {"requests": len(provider.requests), "unauthorized": sum(not request["authorized"] for request in provider.requests)})
        self.persist_case(case)

    def real_queue(self, name: str, device: str, reference: dict[str, Any] | None = None) -> None:
        with KomgaFixture(page_delay=0.8) as provider:
            data, database = self.seed_provider(name, provider, device)
            series = compound_id("komga", "series-000")
            replay = Replay().intent("sources", "open", "komga", wait=1500).intent("catalog", "open", series, wait=1500)
            replay.intent("detail", "favorite")
            replay.intent("detail", "batch-download").intent("panel", "choose", "batch:next-five", wait=1600)
            replay.intent("navigation", "open", "0").intent("downloads", "open", wait=500)
            case = self.capture(name, "sources", device=device, demo=False, offline=False, data=data,
                                replay=replay, expected_route="library", context="downloads", modal="downloads",
                                reference=reference, capture_delay=800,
                                note="Queue records and progress are produced by actual download workers against delayed loopback image responses.")
            jobs = case["state"].get("downloads", [])
            self.check(case, "Actual batch actions create at least one persisted fixture job", bool(jobs), jobs)
            self.check(case, "Native download rows expose real queue records",
                       bool(case["state"].get("download_queue")), case["state"].get("download_queue"))
            manifests = self.database_rows(case, "SELECT COUNT(*) FROM pages")
            case["fixture_page_manifest_count"] = manifests[0][0] if manifests else 0
            self.check(case, "The provider resolves real image manifests before capture",
                       any("/pages" in request["path"] for request in provider.requests), provider.requests[-4:])
            self.save_provider(name, provider, case)
            second = self.capture(f"{name}--page-two", "settings", device=device, demo=False, offline=False, data=data,
                                  replay=Replay().intent("downloads", "open").intent("panel", "page", "1"),
                                  modal="downloads", context="downloads", expected={"panel_page": 2})
            first_ids = {item.get("id") for item in case["state"].get("download_queue", [])}
            second_ids = {item.get("id") for item in second["state"].get("download_queue", [])}
            self.check(second, "Queue pagination uses distinct actual job membership",
                       bool(second_ids) and not first_ids.intersection(second_ids), sorted(second_ids))
            self.save_provider(f"{name}--page-two", provider, second)
            latest_queue = second
            for operation, allowed in (("pause-all", {"paused", "completed"}),
                                       ("resume-all", {"queued", "downloading", "completed"}),
                                       ("cancel-all", {"cancelled", "completed"})):
                before_jobs = latest_queue["state"].get("downloads", [])
                affected_states = {"paused", "failed"} if operation == "resume-all" else {"queued", "downloading", "paused"}
                affected = [(index, job) for index, job in enumerate(before_jobs) if job.get("state") in affected_states]
                target_index, target_job = affected[-1] if affected else (0, {})
                replay = Replay().intent("downloads", operation, wait=500).intent("downloads", "open")
                if target_index // 3:
                    replay.intent("panel", "page", str(target_index // 3))
                changed = self.capture(f"{name}--{operation}", "settings", device=device, demo=False,
                                       offline=False, data=data,
                                       replay=replay,
                                       expected_route="settings", modal="downloads", context="downloads", capture_delay=300)
                self.check(changed, f"The actual {operation} operation updates every real job",
                           bool(changed["state"].get("downloads")) and
                           all(job.get("state") in allowed for job in changed["state"].get("downloads", [])),
                           changed["state"].get("downloads"))
                visible_target = next((row for row in changed["state"].get("download_queue", [])
                                       if row.get("id") == target_job.get("chapter_id")), None)
                required_visible = {"paused"} if operation == "pause-all" else {"cancelled"} if operation == "cancel-all" else allowed
                self.check(changed, "The operation capture visibly contains an actual affected queue row on its real page",
                           bool(target_job) and visible_target is not None and visible_target.get("state") in required_visible,
                           {"target_chapter": target_job.get("chapter_id"), "visible_row": visible_target,
                            "panel_page": changed["state"].get("panel_page")})
                changed["visible_ui_requirements"] = [
                    "The actual affected task is visible with its final paused/cancelled or live resumed state.",
                    "The native queue outline encloses all rows and its real pagination.",
                ]
                self.save_provider(f"{name}--{operation}", provider, changed)
                latest_queue = changed
            provider.page_delay = 0.0
            chapter = compound_id(series, "series-000-chapter-01")
            completed = self.capture(f"{name}--completed-real-job", "library", device=device, demo=False, offline=False, data=data,
                                     replay=Replay().intent("library", "open", series, wait=1000)
                                     .intent("detail", "select-chapter", chapter).intent("detail", "download-selected", wait=3000)
                                     .intent("downloads", "open"),
                                     expected_route="detail", modal="downloads", context="downloads")
            jobs = [job for job in completed["state"].get("downloads", []) if job.get("chapter_id") == chapter]
            self.check(completed, "A selected actual chapter finishes downloading real image bytes",
                       len(jobs) == 1 and jobs[0].get("state") == "completed" and jobs[0].get("completed") == provider.page_count,
                       jobs)
            self.save_provider(f"{name}--completed-real-job", provider, completed)
            self.capture(f"{name}--completed-job-menu", "settings", device=device, demo=False, offline=False, data=data,
                         replay=Replay().intent("downloads", "open").intent("panel", "choose", f"job:{chapter}"),
                         modal="choices", context=f"job:{chapter}")
            self.capture(f"{name}--completed-remove-confirm", "settings", device=device, demo=False, offline=False, data=data,
                         replay=Replay().intent("downloads", "open").intent("panel", "choose", f"job:{chapter}")
                         .intent("panel", "choose", "job-remove"), modal="choices", context=f"job:{chapter}",
                         expected={"modal_item_ids": ["job-remove-confirm", "close"]})
            cancel = self.capture(f"{name}--completed-remove-cancel", "settings", device=device, demo=False, offline=False, data=data,
                                  replay=Replay().intent("downloads", "open").intent("panel", "choose", f"job:{chapter}")
                                  .intent("panel", "choose", "job-remove").intent("panel", "choose", "close"), modal="")
            self.check(cancel, "Cancel preserves the actual completed download",
                       any(job.get("chapter_id") == chapter and job.get("state") == "completed" for job in cancel["state"].get("downloads", [])),
                       cancel["state"].get("downloads"))
            self.persist_case(cancel)
            removed = self.capture(f"{name}--completed-remove-apply", "settings", device=device, demo=False, offline=False, data=data,
                                   replay=Replay().intent("downloads", "open").intent("panel", "choose", f"job:{chapter}")
                                   .intent("panel", "choose", "job-remove").intent("panel", "choose", "job-remove-confirm", wait=1400),
                                   modal="downloads", context="downloads")
            self.check(removed, "Confirm removes the actual completed job record",
                       all(job.get("chapter_id") != chapter for job in removed["state"].get("downloads", [])),
                       removed["state"].get("downloads"))
            records = self.database_rows(removed, "SELECT document FROM chapters WHERE id=?", (chapter,))
            self.check(removed, "Removal clears the actual chapter's downloaded flag",
                       bool(records) and json.loads(records[0][0]).get("downloaded") is False,
                       records[0][0] if records else None)
            self.save_provider(f"{name}--completed-remove-apply", provider, removed)

    def scenario_group(self, identifier: str, operation: Any, *arguments: Any) -> None:
        try:
            operation(*arguments)
            self.completed_groups.append(identifier)
        except Exception as error:
            self.cases.append({"name": f"group--{identifier.replace(':', '--')}", "passed": False, "manual_review_required": False,
                               "checks": [{"description": "The complete supplemental scenario group executed",
                                           "passed": False, "actual": f"{type(error).__name__}: {error}"}]})

    def supplementary(self) -> None:
        for device in ("pw5", "scribe"):
            for name, operation in (
                ("all-settings-sections", self.setting_sections),
                ("library-sort-and-tristate", self.library_controls),
                ("keyboard-and-search", self.keyboard_modes),
                ("list-and-detail-boundaries", self.boundaries),
                ("catalog-filter-and-source-tabs", self.catalog_controls),
                ("history-operation", self.history_operations),
                ("truthful-source-and-migration", self.source_and_migration),
                ("real-http-search", self.loopback_search),
                ("content-stress", self.content_stress),
            ):
                self.scenario_group(f"{name}:{device}", operation, device)
        self.scenario_group("reader-input-and-pure-scene", self.reader_operations)
        self.scenario_group("truthful-hardware", self.missing_capabilities)
        self.scenario_group("profile-stretch", self.profile_stretch)

    def setting_sections(self, device: str) -> None:
        referenced = {case.get("state", {}).get("settings_section") for case in self.cases
                      if case.get("device") == device and case.get("screen") == "settings"}
        for section in SETTINGS:
            if section == "pen":
                continue
            if section in referenced:
                continue
            self.capture(f"{device}--settings-{section}", "settings", device=device,
                         replay=Replay().intent("settings", "open", section, wait=900),
                         expected={"settings_section": section}, modal="",
                         note="Any unavailable hardware or external integration is the application's actual state; no success is simulated.")
        if device == "scribe":
            c4_business.run_book_storage(self, self.c4_api())
        c5_ui.run_license_history(self, self.c4_api(), profiles=(device,))

    def library_controls(self, device: str) -> None:
        for sort in SORTS:
            replay = Replay().intent("library", "sort", sort).intent("library", "sort-direction", "ascending").intent("library", "filter-menu")
            case = self.capture(f"{device}--sort-{sort}", device=device, replay=replay,
                                modal="choices", context="library-filter",
                                expected={"library_sort": sort, "library_ascending": True})
            selected = [item["id"] for item in case["state"].get("modal_items", []) if item.get("value")]
            self.check(case, "Exactly the selected sort has an active indicator", selected == [f"library-sort:{sort}"], selected)
            stored = self.database_rows(case, "SELECT document FROM preferences WHERE key='ui:interaction-v1'")
            interaction = json.loads(stored[0][0]) if stored else {}
            self.check(case, "The selected sort and direction are durably persisted",
                       interaction.get("library_sort") == sort and interaction.get("library_ascending") is True,
                       {key: interaction.get(key) for key in ("library_sort", "library_ascending")})
            self.persist_case(case)
        descending = self.capture(f"{device}--sort-descending", device=device,
                                  replay=Replay().intent("library", "sort", "title")
                                  .intent("library", "sort-direction", "descending").intent("library", "filter-menu"),
                                  modal="choices", context="library-filter",
                                  expected={"library_sort": "title", "library_ascending": False})
        self.capture(f"{device}--sort-cold-persistence", device=device, data=Path(descending["data_dir"]),
                     expected={"library_sort": "title", "library_ascending": False})
        for index, identifier in enumerate(FILTERS):
            for clicks, value in ((1, 1), (2, -1), (3, 0)):
                replay = Replay()
                for _ in range(clicks):
                    replay.intent("library", "cycle-filter", identifier, wait=120)
                replay.intent("library", "filter-menu")
                values = [0, 0, 0, 0]
                values[index] = value
                self.capture(f"{device}--filter-{identifier}-{clicks}", device=device, replay=replay,
                             modal="choices", context="library-filter", expected={"library_filters": values})
        for tab in range(5):
            self.capture(f"{device}--library-tab-{tab}", device=device,
                         replay=Replay().intent("library", "filter", str(tab)), expected={"library_filter": tab, "page": 1})
        selection = self.capture(f"{device}--selection-all-page", device=device,
                                 replay=Replay().intent("library", "select-page"), expected={"selection_mode": True})
        selected = set(selection["state"].get("selected", []))
        visible = {book["id"] for book in selection["state"].get("visible_books", [])}
        self.check(selection, "Select-page chooses exactly the actual visible page membership", selected == visible and bool(selected), sorted(selected))
        self.persist_case(selection)
        self.capture(f"{device}--selection-category", device=device,
                     replay=Replay().intent("library", "select-page").intent("library", "batch-category"), modal="choices", context="category")
        self.capture(f"{device}--selection-cancel", device=device,
                     replay=Replay().intent("library", "select-page").intent("library", "finish-selection"),
                     expected={"selection_mode": False, "selected": []}, modal="")
        marked = self.capture(f"{device}--selection-mark-read", device=device,
                              replay=Replay().intent("library", "select", compound_id("mangadex", "demo-00"))
                              .intent("library", "batch-read", wait=1400),
                              expected={"selection_mode": False, "selected": []}, modal="")
        rows = self.database_rows(marked, "SELECT document FROM chapters WHERE book_id=?", (compound_id("mangadex", "demo-00"),))
        self.check(marked, "Mark-read updates the selected book's actual chapter records",
                   bool(rows) and all(json.loads(row[0]).get("read") is True for row in rows), len(rows))
        self.persist_case(marked)
        removed = self.capture(f"{device}--selection-remove", device=device,
                               replay=Replay().intent("library", "select", compound_id("mangadex", "demo-00"))
                               .intent("library", "batch-remove"),
                               expected={"selection_mode": False, "selected": []}, modal="")
        rows = self.database_rows(removed, "SELECT document FROM books WHERE id=?", (compound_id("mangadex", "demo-00"),))
        self.check(removed, "Remove updates actual library membership rather than only selection decoration",
                   bool(rows) and json.loads(rows[0][0]).get("in_library") is False, rows[0][0] if rows else None)
        self.persist_case(removed)
        refreshed = self.capture(f"{device}--library-update-toast", device=device,
                                 replay=Replay().intent("library", "refresh", wait=500), capture_delay=250)
        self.check(refreshed, "The actual refresh action produces a user-visible result or active worker",
                   bool(refreshed["state"].get("message")) or refreshed["state"].get("busy") is True,
                   {key: refreshed["state"].get(key) for key in ("message", "busy")})
        self.expect(refreshed, "message", native.ui_label("library_update_demo"))
        refreshed["visible_ui_requirements"] = ["The visible toast truthfully identifies a demo update check, rather than a Saved or completed-network claim."]
        self.persist_case(refreshed)

    def keyboard_modes(self, device: str) -> None:
        base = lambda: Replay().intent("search", "clear").intent("search", "edit")
        for mode in ("pinyin", "english", "numbers"):
            replay = base().intent("keyboard", "mode", mode)
            if mode == "pinyin":
                replay.type_text("s")
                expected = {"keyboard_mode": mode, "keyboard_composition": "s"}
            else:
                replay.type_text("abc" if mode == "english" else "123")
                expected = {"keyboard_mode": mode, "keyboard_text": "abc" if mode == "english" else "123"}
            case = self.capture(f"{device}--keyboard-{mode}", "search", device=device, replay=replay,
                                modal="keyboard", expected=expected)
            if mode == "pinyin":
                self.check(case, "Pinyin composition exposes actual dictionary candidates", bool(case["state"].get("keyboard_candidates")), case["state"].get("keyboard_candidates"))
                self.persist_case(case)
                second = self.capture(f"{device}--keyboard-candidate-page-2", "search", device=device,
                                      replay=base().type_text("s").intent("keyboard", "candidate-page", "1"),
                                      modal="keyboard", expected={"keyboard_candidate_page": 2})
                self.check(second, "Candidate navigation changes the actual visible dictionary page",
                           second["state"].get("keyboard_candidates") != case["state"].get("keyboard_candidates"),
                           second["state"].get("keyboard_candidates"))
                self.persist_case(second)
                candidates = case["state"].get("keyboard_candidates", [])
                if candidates:
                    self.capture(f"{device}--keyboard-candidate-commit", "search", device=device,
                                 replay=base().type_text("s").intent("keyboard", "candidate", candidates[0]),
                                 modal="keyboard", expected={"keyboard_composition": "", "keyboard_text": candidates[0]})
        replay = base().intent("keyboard", "mode", "english").intent("keyboard", "key", "shift").intent("keyboard", "key", "a")
        replay.intent("keyboard", "space").intent("keyboard", "key", "b").intent("keyboard", "backspace")
        self.capture(f"{device}--keyboard-shift-space-backspace", "search", device=device, replay=replay,
                     modal="keyboard", expected={"keyboard_text": "A "})
        self.capture(f"{device}--keyboard-clear", "search", device=device,
                     replay=base().intent("keyboard", "mode", "english").type_text("clearable").intent("search", "clear"),
                     modal="keyboard", expected={"keyboard_text": "", "query": ""})
        self.capture(f"{device}--keyboard-cancel", "search", device=device,
                     replay=base().intent("keyboard", "cancel"), modal="", expected={"route": "search"})
        self.capture(f"{device}--search-source-scope", "search", device=device,
                     replay=base().intent("search", "source", "mangadex"), modal="keyboard", expected={"search_scope": "mangadex"})

    def boundaries(self, device: str) -> None:
        for screen in ("library", "catalog", "detail", "history", "settings", "sources"):
            last_replay = Replay()
            first_replay = Replay()
            if screen == "sources":
                last_replay.intent("sources", "filter", "1")
                first_replay.intent("sources", "filter", "1")
            last = self.capture(f"{device}--{screen}-last-page", screen, device=device,
                                replay=last_replay.intent("page", "turn", "99999"))
            self.check(last, "The forward boundary reaches the actual final folio",
                       last["state"].get("page") == last["state"].get("page_count"),
                       {key: last["state"].get(key) for key in ("page", "page_count")})
            self.persist_case(last)
            case = self.capture(f"{device}--{screen}-first-boundary", screen, device=device,
                                replay=first_replay.intent("page", "turn", "-99999"), expected={"page": 1})
            self.persist_case(case)
        screen = "detail"
        second = self.capture(f"{device}--detail-page-two", screen, device=device,
                              replay=Replay().intent("page", "turn", "1"), expected={"page": 2})
        count = len(second["state"].get("visible_chapters", []))
        self.check(second, "The second detail page uses the profile's real chapter row capacity",
                   count == (16 if device == "scribe" else 12), count)
        self.persist_case(second)
        width, height = native.PROFILES[device]
        dismissed = self.capture(f"{device}--folio-outside-dismiss", device=device,
                                 replay=Replay().intent("page", "jump-toggle", "page").tap(width / 2, 180),
                                 expected={"page_jump_open": False}, modal="")
        self.persist_case(dismissed)
        closed = self.capture(f"{device}--anchored-menu-outside-dismiss", device=device,
                              replay=Replay().intent("library", "more").tap(10, height / 2), modal="")
        self.persist_case(closed)
        empty = self.capture(f"{device}--empty-library", device=device, demo=False, expected={"library_count": 0, "page": 1, "page_count": 1}, modal="")
        self.capture(f"{device}--empty-history", "history", device=device, demo=False, expected={"history_count": 0, "page": 1, "page_count": 1}, modal="")
        self.persist_case(empty)

    def catalog_controls(self, device: str) -> None:
        initial = self.capture(f"{device}--catalog-tags-seed", "catalog", device=device,
                               replay=Replay().intent("catalog", "filter-menu"), modal="choices", context="catalog-filter")
        tags = initial["state"].get("catalog_tag_options", [])
        self.check(initial, "Actual loaded catalog tags are available to filter", bool(tags), tags)
        self.persist_case(initial)
        if tags:
            tag = tags[0]["id"]
            for clicks, expected in ((1, 1), (2, -1), (3, 0)):
                replay = Replay().intent("catalog", "filter-menu")
                for _ in range(clicks):
                    replay.intent("catalog", "filter-choice", tag)
                case = self.capture(f"{device}--catalog-tag-{clicks}", "catalog", device=device,
                                    replay=replay, modal="choices", context="catalog-filter")
                state = next((item.get("state") for item in case["state"].get("catalog_tag_options", []) if item.get("id") == tag), None)
                self.check(case, "The chosen real tag completes the exact three-state cycle", state == expected, state)
                self.persist_case(case)
        self.capture(f"{device}--catalog-filter-apply", "catalog", device=device,
                     replay=Replay().intent("catalog", "filter-menu").intent("catalog", "filter-choice", "sort:latest")
                     .intent("catalog", "filter-choice", "status:ongoing").intent("catalog", "apply-filter"), modal="")
        self.capture(f"{device}--catalog-filter-reset", "catalog", device=device,
                     replay=Replay().intent("catalog", "filter-menu").intent("catalog", "filter-choice", "sort:latest")
                     .intent("catalog", "reset-filter"), modal="choices", context="catalog-filter",
                     expected={"catalog_active_filter_count": 0})
        self.source_languages(device)

    def source_languages(self, device: str) -> None:
        for language in ("all", "zh-Hans", "zh-Hant", "en", "ja"):
            language_case = self.capture(f"{device}--source-language-{language}", "sources", device=device,
                                         replay=Replay().intent("sources", "manage").intent("sources", "filter", "1")
                                         .intent("sources", "language", language),
                                         expected={"source_language": language, "source_filter": 1})
            language_case["visible_ui_requirements"] = [
                "Available is the active source-management tab.",
                "The actual language chip is selected and the filtered rows or explicit empty state are visible.",
            ]
            self.check(language_case, "Available language capture contains real projected result rows or an honest empty result",
                       bool(language_case["state"].get("available_sources"))
                       or language_case["state"].get("available_count") == 0,
                       {key: language_case["state"].get(key) for key in ("source_filter", "source_language", "available_count")})
            self.persist_case(language_case)

    def run_subset(self, name: str) -> None:
        """Qualify focused native branches while retaining incomplete-round gates."""
        self.reference_ids = [entry["id"] for board in self.scope["pageStateMatrix"]
                              for entry in [board["default"], *board["states"]]]
        if name == "c4-reader":
            self.c4_reader_features()
        elif name == "c4-reader-retry":
            self.c4_reader_retry_cases()
        elif name == "c4-book-storage":
            c4_business.run_book_storage(self, self.c4_api())
        elif name == "c4-migration":
            for device in ("pw5", "scribe"):
                self.real_migration(device)
        elif name == "c4-search":
            for device in ("pw5", "scribe"):
                self.loopback_search(device)
        elif name == "c4-content":
            for device in ("pw5", "scribe"):
                self.content_stress(device)
        elif name == "c4-queue-language":
            self.source_languages("pw5")
            self.real_queue("Main--download-queue", "pw5")
            self.real_queue("sc-library--download-queue", "scribe")
        elif name == "c5-ui":
            self.c5_qualification()
        else:
            raise ValueError(f"Unknown focused native subset: {name}")

    def c5_qualification(self) -> None:
        """Exercise thirty-eight actual captures without implying a full round."""
        selected = {"Main--more", "pw-source--filter", "sc-browse--default", "sc-browse--filter",
                    "pw-settings--display", "sc-settings--default"}
        for board in self.scope["pageStateMatrix"]:
            for entry in [board["default"], *board["states"]]:
                if entry["id"] in selected:
                    self.reference_case(board, entry)
        c5_ui.run_license_history(self, self.c4_api(), qualifier=True)
        c5_reader.run_reader_routes(self, self.c4_api(), qualifier=True)
        self.real_migration("scribe")
        with KomgaFixture(stress=True) as provider:
            name = "scribe--c5-tag-control"
            data, _ = self.seed_provider(name, provider, "scribe")
            self.catalog_tag_paging(name, "scribe", data, qualifier=True)
            self.save_provider(f"{name}--qualified-native-controls", provider, self.cases[-1])
        self.cases[-1]["qualification_capture_budget"] = {"maximum": 40, "actual": len(self.cases)}
        self.check(self.cases[-1], "The bounded C5 qualification retains its fixed thirty-eight native captures",
                   len(self.cases) == 38, len(self.cases))
        self.persist_case(self.cases[-1])

    def history_operations(self, device: str) -> None:
        seed = self.capture(f"{device}--history-operation-seed", "history", device=device, prerequisite=True)
        items = seed["state"].get("visible_history", [])
        if items:
            target = items[0]["chapter_id"]
            deleted = self.capture(f"{device}--history-delete-entry", "history", device=device,
                                   replay=Replay().intent("history", "delete", target))
            self.check(deleted, "Deleting one actual history entry decreases the durable count by one",
                       deleted["state"].get("history_count") == seed["state"].get("history_count", 0) - 1,
                       deleted["state"].get("history_count"))
            self.persist_case(deleted)
        self.capture(f"{device}--history-clear-apply", "history", device=device,
                     replay=Replay().intent("history", "clear").intent("panel", "choose", "clear-history-confirm"),
                     modal="", expected={"history_count": 0})
        enabled = self.capture(f"{device}--incognito-enable", "history", device=device,
                               replay=Replay().intent("settings", "set", "incognito=true"))
        before = self.database_rows(enabled, "SELECT chapter_id,page,total,completed,updated_at FROM progress ORDER BY chapter_id")
        private = self.capture(f"{device}--incognito-history-policy", "reader", device=device,
                               data=Path(enabled["data_dir"]),
                               replay=Replay(start=2500).intent("reader", "seek", "20", wait=2500).intent("reader", "back"),
                               expected_route="detail", capture_delay=1200)
        self.preference(private, "incognito", True)
        after = self.database_rows(private, "SELECT chapter_id,page,total,completed,updated_at FROM progress ORDER BY chapter_id")
        self.check(private, "Private reading leaves every durable history row unchanged", after == before,
                   {"before_rows": len(before), "after_rows": len(after)})
        self.persist_case(private)
        restarted = self.capture(f"{device}--incognito-persistence", "history", device=device, data=Path(enabled["data_dir"]))
        self.preference(restarted, "incognito", True)
        self.persist_case(restarted)
        self.capture(f"{device}--history-clear-cancel", "history", device=device,
                     replay=Replay().intent("history", "clear").intent("panel", "choose", "close"),
                     expected={"history_count": seed["state"].get("history_count")}, modal="")

    def source_and_migration(self, device: str) -> None:
        setup = Replay().intent("sources", "open", "komga").intent("sources", "edit-address", "komga")
        self.capture(f"{device}--server-address-keyboard", "catalog", device=device, demo=False,
                     replay=setup, modal="keyboard", expected={"keyboard_target": "server-address", "keyboard_mode": "english"},
                     note="The server is unconfigured; this is the actual address entry rather than a connected mock.")
        secret = Replay().intent("sources", "open", "komga").intent("sources", "edit-credential", "komga")
        secret.intent("panel", "choose", "server-config:edit-password").type_text("public-fixture-password")
        self.capture(f"{device}--server-secret-keyboard", "catalog", device=device, demo=False,
                     replay=secret, modal="keyboard", expected={"keyboard_target": "server-password", "keyboard_is_secret": True,
                                                                "keyboard_text": "\u2022" * len("public-fixture-password")})
        migration = self.capture(f"{device}--migration-options", "detail", device=device,
                                 replay=Replay().intent("detail", "migrate", wait=1200)
                                 .intent("migration", "toggle-read").intent("migration", "toggle-categories")
                                 .intent("migration", "toggle-delete-downloads"),
                                 modal="choices", context="migration",
                                 expected={"migration_preserve_read": False, "migration_preserve_category": False,
                                           "migration_remove_downloads": True})
        self.check(migration, "Migration never enables start without an actual selected candidate",
                   not migration["state"].get("migration_can_start") or
                   any(item.get("id") == migration["state"].get("migration_selected_id") for item in migration["state"].get("migration_candidates", [])),
                   migration["state"].get("migration_selected_id"))
        self.persist_case(migration)
        self.capture(f"{device}--migration-cancel", "detail", device=device,
                     replay=Replay().intent("detail", "migrate", wait=1200).intent("migration", "cancel"),
                     modal="", expected={"migration_candidates": [], "migration_selected_id": ""})
        self.real_migration(device)
        self.repository_operations(device)

    def real_migration(self, device: str) -> None:
        name = f"{device}--real-migration"
        with KomgaFixture() as origin_provider, KomgaFixture(title_hint_triplet=True) as provider:
            data, database = self.seed_provider(name, origin_provider, device)
            with sqlite3.connect(database) as connection:
                original = json.loads(connection.execute("SELECT document FROM sources WHERE id='komga'").fetchone()[0])
                alternate = dict(original, id="komga-alternate", name="Alternate local Komga fixture", base_url=provider.url)
                connection.execute("INSERT INTO sources(id,document) VALUES(?,?)", (alternate["id"], json.dumps(alternate)))
            origins = [compound_id("komga", f"series-{index:03}") for index in range(17)]
            origin = origins[-1]
            target = compound_id("komga-alternate", "series-016")
            prepare = Replay().intent("sources", "open", "komga", wait=1200)
            for book in origins:
                prepare.intent("catalog", "open", book, wait=350).intent("detail", "favorite", wait=80).intent("detail", "back", wait=80)
            first_chapter = compound_id(origins[0], "series-000-chapter-01")
            prepare.intent("catalog", "open", origins[0], wait=400).intent("detail", "open-chapter", first_chapter, wait=2500)
            prepare.intent("reader", "back").intent("app", "back").intent("panel", "choose", "clear-cache", wait=1500)
            seeded = self.capture(f"{name}--origin-library", "sources", device=device, demo=False, offline=False, data=data,
                                  replay=prepare, expected_route="catalog", prerequisite=True)
            self.check(seeded, "The blocked-origin fixture consists of 17 actually favorited provider books",
                       self.database_rows(seeded, "SELECT count(*) FROM books WHERE source_id='komga' AND in_library=1") == [(17,)])
            self.persist_case(seeded)
            with sqlite3.connect(database) as connection:
                current = json.loads(connection.execute("SELECT document FROM sources WHERE id='komga'").fetchone()[0])
                current["enabled"] = False
                connection.execute("UPDATE sources SET document=? WHERE id='komga'", (json.dumps(current),))
                empty = dict(current, id="komga-empty", name="Empty disabled fixture")
                connection.execute("INSERT INTO sources(id,document) VALUES(?,?)", (empty["id"], json.dumps(empty)))
            origin_start = len(origin_provider.requests)

            def selector() -> Replay:
                return Replay().intent("sources", "open", "komga").intent("sources", "migrate", "komga")

            empty_case = self.capture(f"{name}--origin-empty", "catalog", device=device, demo=False, offline=False, data=data,
                                      replay=Replay().intent("sources", "open", "komga-empty").intent("sources", "migrate", "komga-empty"),
                                      modal="choices", context="source-migration:komga-empty")
            empty_row = next(iter(empty_case["state"].get("modal_items", [])), {})
            self.check(empty_case, "A disabled source without library books exposes an honest disabled empty row",
                       empty_row.get("id") == "source-migration-empty" and empty_row.get("enabled") is False, empty_row)
            self.persist_case(empty_case)
            last = self.capture(f"{name}--origin-selector-last", "catalog", device=device, demo=False, offline=False, data=data,
                                replay=selector().intent("panel", "page", "99999"), modal="choices", context="source-migration:komga")
            visible = last["state"].get("modal_item_ids", [])
            self.check(last, "The actual bounded selector reaches this disabled source's final library page",
                       last["state"].get("panel_page") == last["state"].get("panel_page_count") and origin in visible
                       and all(identifier in origins for identifier in visible), visible)
            self.check(last, "The selected source-book identity is distinct from the existing remembered reader book",
                       last["state"].get("current_book") != origin,
                       {"remembered": last["state"].get("current_book"), "to_select": origin})
            if device == "scribe":
                self.visible_outline(last, (145, 240, 640, 246))
            self.persist_case(last)
            cancelled = self.capture(f"{name}--origin-selector-cancel", "catalog", device=device, demo=False, offline=False, data=data,
                                     replay=selector().intent("panel", "page", "99999").intent("panel", "close"), modal="")
            self.check(cancelled, "Cancel never starts migration of a remembered arbitrary book", cancelled["state"].get("modal_context") != "migration", cancelled["state"].get("modal_context"))
            self.persist_case(cancelled)

            def open_migration() -> Replay:
                return selector().intent("panel", "page", "99999").intent("panel", "choose", origin, wait=1600)

            candidates = self.capture(f"{name}--candidates", "catalog", device=device, demo=False, offline=False, data=data,
                                      replay=open_migration(), expected_route="detail", modal="choices", context="migration",
                                      expected={"current_book": origin})
            actual = candidates["state"].get("migration_candidates", [])
            self.check(candidates, "Migration candidates are actual records returned by the alternate loopback source",
                       any(item.get("id") == target and item.get("source_id") == "komga-alternate" for item in actual), actual)
            hints = {item.get("status") for item in actual}
            expected_hints = {native.ui_label(key) for key in ("migration_title_exact", "migration_title_near", "migration_title_check")}
            self.check(candidates, "Three actual provider titles produce the distinct exact, near and check hints",
                       hints == expected_hints and len(actual) == 3, actual)
            self.check(candidates, "Real chapter counts remain readable in every candidate subtitle",
                       all("8" in item.get("subtitle", "") and native.ui_label("chapters_unit") in item.get("subtitle", "") for item in actual), actual)
            candidates["visible_ui_requirements"] = ["All three real title-hint classes appear in the dedicated hint column; each subtitle retains its real chapter count."]
            self.expect(candidates, "migration_can_start", False)
            self.save_provider(f"{name}--candidates", provider, candidates)
            selected = self.capture(f"{name}--selected", "catalog", device=device, demo=False, offline=False, data=data,
                                    replay=open_migration().intent("migration", "select", target),
                                    expected_route="detail", modal="choices", context="migration",
                                    expected={"migration_selected_id": target, "migration_can_start": True})
            self.check(selected, "The selected migration identity matches an actual selected native candidate",
                       any(item.get("id") == target and item.get("selected") is True for item in selected["state"].get("migration_candidates", [])),
                       selected["state"].get("migration_candidates"))
            self.save_provider(f"{name}--selected", provider, selected)
            committed = self.capture(f"{name}--committed", "catalog", device=device, demo=False, offline=False, data=data,
                                     replay=open_migration().intent("migration", "select", target)
                                     .intent("migration", "start", wait=2000), expected_route="detail", modal="",
                                     expected={"current_book": target})
            rows = self.database_rows(committed, "SELECT id,document FROM books WHERE id IN (?,?) ORDER BY id", (origin, target))
            membership = {identifier: json.loads(document).get("in_library") for identifier, document in rows}
            self.check(committed, "A completed migration moves actual durable library membership",
                       membership.get(origin) is False and membership.get(target) is True, membership)
            self.save_provider(f"{name}--committed", provider, committed)
            origin_delta = origin_provider.requests[origin_start:]
            self.check(committed, "Disabled-origin selectors, cold covers and target migration issue zero origin HTTP requests",
                       origin_delta == [], origin_delta)
            path = self.output / "fixtures" / f"{name}--origin-request-delta.json"
            save_json(path, {"baseline_count": origin_start, "new_origin_requests": origin_delta})
            committed.setdefault("evidence_hashes", {})["origin-http-isolation"] = {"path": str(path), "sha256": file_hash(path)}
            self.persist_case(committed)

    def repository_operations(self, device: str) -> None:
        name = f"{device}--source-list-operation"
        with KomgaFixture() as provider:
            data, _ = self.seed_provider(name, provider, device)
            url = f"{provider.url}/index.json"
            added = self.capture(f"{name}--added", "sources", device=device, demo=False, offline=False, data=data,
                                 replay=Replay().intent("repository", "add", url, wait=1400).intent("sources", "list"),
                                 modal="choices", context="repository-list")
            identifier = f"repository-remove:{url}"
            self.check(added, "Adding a real HTTP source index creates an actual saved repository row",
                       identifier in added["state"].get("modal_item_ids", []), added["state"].get("modal_item_ids"))
            self.save_provider(f"{name}--added", provider, added)
            removed = self.capture(f"{name}--removed", "sources", device=device, demo=False, offline=False, data=data,
                                   replay=Replay().intent("sources", "list").intent("panel", "choose", identifier),
                                   modal="choices", context="repository-list")
            self.check(removed, "Removing a repository deletes the actual persisted row",
                       identifier not in removed["state"].get("modal_item_ids", []), removed["state"].get("modal_item_ids"))
            self.persist_case(removed)

    def profile_stretch(self) -> None:
        for device in ("pw6", "scribe3"):
            for screen in ("library", "detail", "catalog", "sources", "search", "history", "settings", "reader"):
                self.capture(f"{device}--stretch-{screen}", screen, device=device,
                             replay=Replay(start=3500).intent("reader", "menu") if screen == "reader" else None,
                             note="A distinct native viewport is captured at this profile's actual size; it is not resized to impersonate an exact handoff canvas.")

    def content_stress(self, device: str) -> None:
        name = f"{device}--content-stress"
        with KomgaFixture(stress=True) as provider:
            data, database = self.seed_provider(name, provider, device)
            origin = compound_id("komga", "series-000")
            with sqlite3.connect(database) as connection:
                original = json.loads(connection.execute("SELECT document FROM sources WHERE id='komga'").fetchone()[0])
                for index in range(24):
                    source = dict(original, id=f"configured-{index:02}",
                                  name=f"Configured local fixture {index:02} with a deliberately long source name")
                    connection.execute("INSERT INTO sources(id,document) VALUES(?,?)", (source["id"], json.dumps(source)))
            manager = self.capture(f"{name}--dense-sources", "sources", device=device, demo=False, data=data,
                                   note="All source rows are real saved loopback configurations. Unverified configurations are not marked connected.")
            self.check(manager, "Dense source management uses actual stored configurations and real pagination",
                       manager["state"].get("installed_count") == 25 and manager["state"].get("installed_source_page_count", 0) > 1,
                       {key: manager["state"].get(key) for key in ("installed_count", "installed_source_page_count")})
            self.persist_case(manager)
            last_installed = self.capture(f"{name}--dense-installed-last", "sources", device=device, demo=False, data=data,
                                          replay=Replay().intent("sources", "installed-page", "99999"),
                                          note="This is the actual Installed configuration boundary, not the Available source-list folio.")
            self.check(last_installed, "Dense Installed source management reaches its actual final page",
                       last_installed["state"].get("installed_source_page") == last_installed["state"].get("installed_source_page_count")
                       and bool(last_installed["state"].get("installed_sources")),
                       {key: last_installed["state"].get(key) for key in ("installed_source_page", "installed_source_page_count", "installed_sources")})
            last_installed["visible_ui_requirements"] = ["The Installed folio is at its real last boundary and distinguishes the final saved configuration identities."]
            self.persist_case(last_installed)
            self.dense_search_scopes(name, device, data, provider)
            detail = self.capture(f"{name}--long-chapters", "sources", device=device, demo=False, offline=False, data=data,
                                  replay=Replay().intent("sources", "open", "komga", wait=1400)
                                  .intent("catalog", "open", origin, wait=1400),
                                  expected_route="detail", modal="")
            self.check(detail, "Long title and chapter rows are actual provider metadata",
                       any(len(row.get("title", "")) > 120 for row in detail["state"].get("visible_chapters", [])),
                       [len(row.get("title", "")) for row in detail["state"].get("visible_chapters", [])])
            self.save_provider(f"{name}--long-chapters", provider, detail)
            self.catalog_tag_paging(name, device, data)
            summary = self.capture(f"{name}--description-and-tags", "library", device=device, demo=False, offline=True, data=data,
                                   replay=Replay().intent("library", "open", origin).intent("detail", "summary"),
                                   expected_route="detail", modal="summary", context="summary", expected={"summary_is_book": True})
            self.check(summary, "Dense tags retain every actual tag rather than truncating metadata",
                       len(summary["state"].get("book_tag_items", [])) >= 32, len(summary["state"].get("book_tag_items", [])))
            self.persist_case(summary)
            self.capture(f"{name}--description-last-page", "library", device=device, demo=False, offline=True, data=data,
                         replay=Replay().intent("library", "open", origin).intent("detail", "summary").intent("panel", "page", "99999"),
                         expected_route="detail", modal="summary", context="summary")
            self.capture(f"{name}--category-capability", "settings", device=device, demo=False, data=data,
                         replay=Replay().intent("settings", "open", "categories"), expected={"settings_section": "categories"},
                         note="Custom category creation/rename is unavailable in this candidate; actual category counts and disabled actions remain explicit.")

    def catalog_tag_paging(self, name: str, device: str, data: Path, *, qualifier: bool = False) -> None:
        def opened() -> Replay:
            return (Replay().intent("sources", "open", "komga", wait=1400 if qualifier else 450)
                    .intent("catalog", "filter-menu").intent("catalog", "reset-filter"))

        first = self.capture(f"{name}--filter-tags-first", "catalog", device=device, demo=False, data=data,
                             offline=not qualifier, replay=opened(), modal="choices", context="catalog-filter")
        tags = first["state"].get("catalog_tag_options", [])
        large = device == "scribe"
        viewport_width, viewport_height = native.PROFILES[device]
        panel_width = 620 if large else viewport_width
        panel_x = viewport_width - panel_width - 28 if large else 0
        pad, chip_height, tags_top = (28, 46, 315) if large else (22, 42, 274)
        def panel_y(rows: int) -> int:
            tag_height = rows * chip_height + max(0, rows - 1) * 8 + 56
            apply_top = tags_top + tag_height + (22 if large else 17)
            height = apply_top + (58 + 28 if large else 52 + 24)
            return min(282, viewport_height - height - 36) if large else viewport_height - height

        def observed_chips(case: dict[str, Any], rows: int) -> list[tuple[int, int, int, int]]:
            """Measure every real outline before pointing at an intrinsic-width chip."""
            top = panel_y(rows) + tags_top
            region = (panel_x + pad - 1, top - 6,
                      panel_x + panel_width - pad + 1, top + rows * (chip_height + 8) + 6)
            with Image.open(case["screenshot"]) as original:
                gray = original.convert("L")
                pixels = {(x, y) for y in range(region[1], region[3])
                          for x in range(region[0], region[2]) if gray.getpixel((x, y)) < 224}
                boxes = []
                while pixels:
                    point = pixels.pop()
                    stack = [point]
                    left = right = point[0]
                    upper = lower = point[1]
                    while stack:
                        x, y = stack.pop()
                        left, right, upper, lower = min(left, x), max(right, x), min(upper, y), max(lower, y)
                        for adjacent in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1),
                                         (x - 1, y - 1), (x + 1, y - 1), (x - 1, y + 1), (x + 1, y + 1)):
                            if adjacent in pixels:
                                pixels.remove(adjacent)
                                stack.append(adjacent)
                    if right - left + 1 >= 32 and chip_height - 6 <= lower - upper + 1 <= chip_height + 6:
                        boxes.append((left, upper, right + 1, lower + 1))
                boxes.sort(key=lambda box: (round((box[1] - top) / (chip_height + 8)), box[0]))
                outlines = []
                for left, upper, right, lower in boxes:
                    points = {
                        "top": [(x, upper) for x in range(left + 8, right - 8)],
                        "bottom": [(x, lower - 2) for x in range(left + 8, right - 8)],
                        "left": [(left, y) for y in range(upper + 8, lower - 8)],
                        "right": [(right - 2, y) for y in range(upper + 8, lower - 8)],
                    }
                    fractions = {edge: sum(min(gray.getpixel((x + dx, y + dy))
                                                   for dx in (0, 1) for dy in (0, 1)) < 224
                                           for x, y in positions) / max(1, len(positions))
                                 for edge, positions in points.items()}
                    self.check(case, "Each measured intrinsic chip retains all four actual native outline edges",
                               all(value >= 0.8 for value in fractions.values()),
                               {"rectangle": [left, upper, right - left, lower - upper], "edge_fractions": fractions})
                    outlines.append({"rectangle": [left, upper, right - left, lower - upper],
                                     "edge_fractions": fractions, "hit": [(left + right) / 2, (upper + lower) / 2]})
            case["native_tag_observation"] = {
                "original_screenshot": case["screenshot"], "original_sha256": file_hash(Path(case["screenshot"])),
                "region": list(region), "controls": outlines,
                "method": "Complete connected native outlines, ordered by actual row and horizontal position; no equal-cell or semantic fallback",
            }
            self.persist_case(case)
            return boxes

        boxes = observed_chips(first, 2)
        first_top = min((box[1] for box in boxes), default=panel_y(2) + tags_top)
        first_row = [box for box in boxes if abs(box[1] - first_top) <= 3]
        columns = len(first_row)
        self.check(first, "Content-aware tag columns are observed from complete native chip outlines", 1 <= columns <= 12, first_row)
        if not 1 <= columns <= 12:
            self.persist_case(first)
            raise ValueError("The native tag-chip geometry could not be observed; no fixed column count is substituted")
        page_size = columns * 2
        page_count = (len(tags) + page_size - 1) // page_size
        self.check(first, "Stress filtering exposes actual paginated metadata tags", 1 < page_count <= 18, len(tags))
        self.check(first, "The first native page has exactly the content-aware two-row capacity",
                   len(boxes) == min(page_size, len(tags)), {"observed": len(boxes), "capacity": page_size})
        first["native_tag_geometry"] = {"columns": columns, "page_size": page_size, "page_count": page_count,
                                        "observed_chip_rectangles": boxes, "observed_first_row": first_row,
                                        "qualification_only": qualifier}
        self.persist_case(first)
        covered = []
        pages = (1, page_count) if qualifier else range(1, page_count + 1)
        for page in pages:
            replay = opened()
            for _ in range(page - 1):
                # The tag pager is bottom anchored; its next control remains
                # stationary even when the final tag page has only one row.
                replay.tap(panel_x + panel_width - pad - 28, 729 if large else viewport_height - 117, wait=250)
            visible = min(page_size, len(tags) - (page - 1) * page_size)
            rows = (visible + columns - 1) // columns
            observation = first if page == 1 else self.capture(
                f"{name}--filter-tag-page-{page}-observe", "catalog", device=device, demo=False, data=data,
                replay=replay, modal="choices", context="catalog-filter", prerequisite=True,
                note="An unchanged native observation locates this page's intrinsic chip surfaces before a separate real pointer replay.")
            page_boxes = boxes if page == 1 else observed_chips(observation, rows)
            self.check(observation, "The observed page exposes every expected actual chip before real input",
                       len(page_boxes) == visible, {"page": page, "expected": visible, "observed": len(page_boxes)})
            self.check(observation, "Reset leaves every actual catalog tag neutral before the measured pointer replay",
                       all(tag.get("state") == 0 for tag in observation["state"].get("catalog_tag_options", [])))
            self.persist_case(observation)
            if len(page_boxes) != visible or not observation["passed"]:
                raise ValueError(f"Native tag geometry is incomplete on page {page}; no alternate hit target is substituted")
            # Selection adds a real marker gutter. Point from right to left so
            # still-neutral controls retain their observed native positions.
            for left, upper, right, lower in reversed(page_boxes):
                replay.tap((left + right) / 2, (upper + lower) / 2, wait=100)
            case = self.capture(f"{name}--filter-tag-page-{page}", "catalog", device=device, demo=False, data=data,
                                replay=replay, modal="choices", context="catalog-filter")
            chosen = [tag["id"] for tag in case["state"].get("catalog_tag_options", []) if tag.get("state") == 1]
            expected = [tag["id"] for tag in tags[(page - 1) * page_size:(page - 1) * page_size + visible]]
            self.check(case, "Real tag-page controls expose the complete continuous next page without omitted or duplicated identities",
                       chosen == expected, {"selected": chosen, "expected": expected, "page": page})
            case["native_tag_geometry"] = {"columns": columns, "page_size": page_size, "page": page,
                                           "page_count": page_count, "visible_tag_ids": expected,
                                           "observation_case_id": observation["name"],
                                           "observed_control_sha256": file_hash(Path(observation["screenshot"])),
                                           "observed_chip_rectangles": page_boxes, "qualification_only": qualifier}
            case["visible_ui_requirements"] = ["Actual intrinsic chip boundaries, distinguishing tag suffixes, selected checkmarks and the true native tag folio remain visible."]
            covered.extend(chosen)
            if page == page_count and not qualifier:
                self.check(case, "The true final content-aware tag page completes the actual tag collection exactly once",
                           covered == [tag["id"] for tag in tags] and len(covered) == len(set(covered)), covered)
            if qualifier:
                case["qualification_scope"] = "Actual first and final intrinsic-width tag controls; the complete collection is still required in both full rounds."
            self.persist_case(case)

    def loopback_search(self, device: str) -> None:
        for variant in ("success", "empty", "failure", "retry", "retrying", "timeout"):
            name = f"{device}--http-search-{variant}"
            with KomgaFixture(fail=variant in ("failure", "retry", "retrying"), empty=variant == "empty",
                              recover_after_failure=variant in ("retry", "retrying"),
                              recovery_delay=4.0 if variant == "retrying" else 0.0,
                              search_delay=33.0 if variant == "timeout" else 0.0) as provider:
                data, _ = self.seed_provider(name, provider, device)
                replay = Replay().intent("search", "query", "fixture", wait=33500 if variant == "timeout" else 1600)
                if variant in ("retry", "retrying"):
                    # The first real HTTP response fails, then only retry can
                    # replace the failed group in this same application process.
                    replay.intent("search", "retry", "komga", wait=150 if variant == "retrying" else 1400)
                case = self.capture(name, "search", device=device, demo=False, offline=False,
                                    data=data, replay=replay, modal="", capture_delay=250 if variant == "retrying" else 1200,
                                    allow_busy=variant == "retrying")
                groups = case["state"].get("search_groups", [])
                remote = next((group for group in groups if group.get("id") == "komga"), None)
                self.check(case, "Search retains the actual fixture source group", remote is not None, groups)
                if remote is not None:
                    self.check(case, "The actual source pending state matches the capture phase",
                               remote.get("pending") is (variant == "retrying"), remote)
                    if variant in ("failure", "timeout"):
                        self.check(case, "A real HTTP failure exposes a retryable source error", remote.get("error") is True, remote)
                    elif variant != "retrying":
                        self.check(case, "The actual source completes without a fabricated error", remote.get("error") is False, remote)
                        self.check(case, "Fixture result count agrees with the actual response",
                                   remote.get("count") == (0 if variant == "empty" else 20), remote.get("count"))
                self.save_provider(name, provider, case)
                if variant == "timeout":
                    group = next((item for item in case["state"].get("search_group_states", []) if item.get("id") == "komga"), {})
                    self.check(case, "The timeout classification comes from a real expired HTTP request",
                               group.get("timeout") is True and group.get("pending") is False, group)
                    self.persist_case(case)
                if variant == "retry":
                    posts = [request for request in provider.requests if request["method"] == "POST" and "/series/list" in request["path"]]
                    self.check(case, "Retry alone follows a failed real request with a successful real request",
                               len(posts) == 2 and posts[0]["fixture_failure"] is True and posts[1]["fixture_failure"] is False,
                               posts)
                    self.persist_case(case)
                if variant == "success":
                    all_results = self.capture(f"{name}--view-all", "search", device=device, demo=False,
                                               offline=False, data=data,
                                               replay=Replay().intent("search", "query", "fixture", wait=1400)
                                               .intent("search", "all", "komga").intent("page", "turn", "999", wait=1200),
                                               expected_route="catalog", modal="", capture_delay=1000)
                    self.check(all_results, "View-all opens real source results rather than a screenshot-only state",
                               all_results["state"].get("source_id") == "komga" and all_results["state"].get("catalog_count", 0) >= 20,
                               {key: all_results["state"].get(key) for key in ("source_id", "catalog_count", "catalog_has_more")})
                    self.expect(all_results, "source_needs_setup", False)
                    source_rows = self.database_rows(all_results, "SELECT document FROM sources WHERE id='komga'")
                    verified = json.loads(source_rows[0][0]).get("options", {}).get("_connection_verified") if source_rows else None
                    self.check(all_results, "Successful real HTTP search persists actual server verification before View all", verified is True, verified)
                    all_results["visible_ui_requirements"] = ["Settled real result cards and their actual catalog folio are visible, with no contradictory setup gate."]
                    self.save_provider(f"{name}--view-all", provider, all_results)
                    first_book = next(iter(all_results["state"].get("visible_catalog", [])), {}).get("id")
                    pointer_open = self.capture(f"{name}--view-all-pointer-open", "search", device=device, demo=False,
                                                offline=False, data=data,
                                                replay=Replay().intent("search", "query", "fixture", wait=1400)
                                                .intent("search", "all", "komga").intent("page", "turn", "999", wait=1200)
                                                .tap(70 if device == "scribe" else 52, 260 if device == "scribe" else 220, wait=1200),
                                                expected_route="detail", expected={"current_book": first_book}, modal="")
                    self.save_provider(f"{name}--view-all-pointer-open", provider, pointer_open)
                    requests_before = len(provider.requests)
                    connected = self.capture(f"{name}--server-connect", "sources", device=device, demo=False,
                                             offline=False, data=data,
                                             replay=Replay().intent("sources", "open", "komga", wait=1400)
                                             .intent("catalog", "settings").intent("sources", "test-connect", "komga", wait=1600),
                                             expected_route="catalog", capture_delay=1200,
                                             expected={"source_needs_setup": False,
                                                       "source_connection_status": native.ui_label("connected")})
                    delta = provider.requests[requests_before:]
                    self.check(connected, "Connection testing produces actual loopback API requests",
                               sum(request["method"] == "POST" and "/series/list" in request["path"] for request in delta) >= 2,
                               delta)
                    self.save_provider(f"{name}--server-connect", provider, connected)
                    before_offline = len(provider.requests)
                    cached = self.capture(f"{name}--cached-unverified", "catalog", device=device, demo=False, data=data,
                                          replay=Replay().intent("sources", "open", "komga")
                                          .intent("sources", "edit-address", "komga").intent("keyboard", "key", "clear", wait=100)
                                          .type_text(provider.url + "/unverified-cache").intent("keyboard", "done", wait=800)
                                          .intent("sources", "open", "komga", wait=800),
                                          modal="", expected={"source_needs_setup": True})
                    self.check(cached, "Cached unverified catalog records remain projected without claiming a new connection",
                               bool(cached["state"].get("visible_catalog")) and len(provider.requests) == before_offline,
                               {"visible_cards": len(cached["state"].get("visible_catalog", [])), "new_http_requests": len(provider.requests) - before_offline})
                    source_rows = self.database_rows(cached, "SELECT document FROM sources WHERE id='komga'")
                    self.check(cached, "Changing the server address clears the actual verification flag",
                               bool(source_rows) and json.loads(source_rows[0][0]).get("options", {}).get("_connection_verified") is not True,
                               json.loads(source_rows[0][0]).get("options", {}) if source_rows else None)
                    cached["visible_ui_requirements"] = ["The cached catalog remains visible with an honest unverified status; no Connected label is fabricated."]
                    self.persist_case(cached)
        self.independent_search(device)
        if device == "scribe":
            c10_search.run_search_identity(self, self.c4_api(), qualifier=False)

    def independent_search(self, device: str) -> None:
        name = f"{device}--independent-search"
        with ExitStack() as stack:
            providers = {
                "fast": stack.enter_context(KomgaFixture()),
                "empty": stack.enter_context(KomgaFixture(empty=True)),
                "error": stack.enter_context(KomgaFixture(fail=True)),
                "slow": stack.enter_context(KomgaFixture(search_delay=4.0)),
            }
            data, database = self.seed_provider(name, providers["fast"], device)
            with sqlite3.connect(database) as connection:
                original = json.loads(connection.execute("SELECT document FROM sources WHERE id='komga'").fetchone()[0])
                connection.execute("DELETE FROM sources")
                for identifier, provider in providers.items():
                    source = dict(original, id=identifier, name=f"Actual {identifier} fixture", base_url=provider.url)
                    connection.execute("INSERT INTO sources(id,document) VALUES(?,?)", (identifier, json.dumps(source)))
            early = self.capture(f"{name}--early-result", "search", device=device, demo=False, offline=False, data=data,
                                 replay=Replay().intent("search", "query", "fixture", wait=150),
                                 capture_delay=250, allow_busy=True,
                                 note="Fast, empty, failing and delayed sources are independent real loopback requests; completed results remain visible while a slower source is pending.")
            groups = {item["id"]: item for item in early["state"].get("search_group_states", [])}
            self.check(early, "A fast actual source completes before the delayed source",
                       groups.get("fast", {}).get("pending") is False and groups.get("fast", {}).get("count") == 20
                       and groups.get("slow", {}).get("pending") is True, groups)
            self.check(early, "Empty and failing sources retain independent actual outcomes",
                       groups.get("empty", {}).get("count") == 0 and groups.get("empty", {}).get("error") is False
                       and groups.get("error", {}).get("error") is True, groups)
            self.persist_case(early)
            finished = self.capture(f"{name}--all-complete", "search", device=device, demo=False, offline=False, data=data,
                                    replay=Replay().intent("search", "query", "fixture", wait=5000), capture_delay=1200)
            groups = {item["id"]: item for item in finished["state"].get("search_group_states", [])}
            self.check(finished, "All actual source groups eventually complete independently",
                       len(groups) == 4 and all(item.get("pending") is False for item in groups.values()), groups)
            self.persist_case(finished)
            for identifier, provider in providers.items():
                self.save_provider(f"{name}--{identifier}", provider, finished)
            more_x, more_y = self.scope_more_position(finished)
            picker = self.capture(f"{name}--more-picker", "search", device=device, demo=False, offline=False, data=data,
                                  replay=Replay().intent("search", "query", "fixture", wait=5000).tap(more_x, more_y),
                                  modal="choices", context="search-scope:results")
            visible_ids = picker["state"].get("modal_item_ids", [])
            self.check(picker, "The visible More picker contains every real eligible source and the two global scopes",
                       set(visible_ids) == {"all", "library", *providers.keys()}, visible_ids)
            self.persist_case(picker)
            selected = self.capture(f"{name}--more-fourth-source", "search", device=device, demo=False, offline=False, data=data,
                                    replay=Replay().intent("search", "query", "fixture", wait=5000).tap(more_x, more_y)
                                    .intent("panel", "choose", "slow", wait=5000), modal="", expected={"search_scope": "slow"})
            self.check(selected, "Selecting the actual fourth-source scope performs only that source's settled search",
                       [group.get("id") for group in selected["state"].get("search_group_states", [])] == ["slow"],
                       selected["state"].get("search_group_states"))
            self.persist_case(selected)

    def dense_search_scopes(self, name: str, device: str, data: Path, provider: KomgaFixture) -> None:
        text = "typed-without-submission"

        def typing() -> Replay:
            return (Replay().intent("search", "query", "fixture", wait=5000).intent("search", "edit")
                    .intent("keyboard", "mode", "english", wait=100).intent("keyboard", "key", "clear", wait=100).type_text(text))

        before = len(provider.requests)
        baseline = self.capture(f"{name}--scope-typing", "search", device=device, demo=False, offline=False, data=data,
                                replay=typing(), modal="keyboard", expected={"query": "fixture", "keyboard_text": text})
        posted = [request for request in provider.requests[before:] if request["method"] == "POST" and "/series/list" in request["path"]]
        self.check(baseline, "Typing does not submit another request beyond the actual 25-source initial search", len(posted) == 25, len(posted))
        self.check(baseline, "All 25 eligible source identities remain available to the visible overflow control",
                   len(baseline["state"].get("search_sources", [])) == 25, len(baseline["state"].get("search_sources", [])))
        self.persist_case(baseline)
        if device == "scribe":
            c13_scope.run_selected_shortcuts(self, self.c4_api(), baseline, data, provider)
        more_x, more_y = self.scope_more_position(baseline)
        before = len(provider.requests)
        picker = self.capture(f"{name}--scope-picker-last", "search", device=device, demo=False, offline=False, data=data,
                              replay=typing().tap(more_x, more_y).intent("panel", "page", "99999"),
                              modal="choices", context="search-scope:typing")
        self.check(picker, "The actual 25-source overflow picker reaches its last bounded page",
                   picker["state"].get("panel_page") == picker["state"].get("panel_page_count")
                   and bool(picker["state"].get("modal_item_ids")),
                   {key: picker["state"].get(key) for key in ("panel_page", "panel_page_count", "modal_item_ids")})
        posts = [request for request in provider.requests[before:] if request["method"] == "POST" and "/series/list" in request["path"]]
        self.check(picker, "Opening and paging the typing scope picker adds no source request", len(posts) == 25, len(posts))
        self.persist_case(picker)
        chosen = next((identifier for identifier in reversed(picker["state"].get("modal_item_ids", [])) if identifier not in ("all", "library")), None)
        if chosen is None:
            raise ValueError("The real source picker has no final eligible source")
        for action, suffix, expected_scope in (("close", "cancel", "all"), ("choose", "select-last", chosen)):
            before = len(provider.requests)
            replay = typing().tap(more_x, more_y).intent("panel", "page", "99999")
            replay.intent("panel", action, chosen if action == "choose" else "")
            case = self.capture(f"{name}--scope-{suffix}", "search", device=device, demo=False, offline=False, data=data,
                                replay=replay, modal="keyboard", expected={"query": "fixture", "keyboard_text": text,
                                                                         "keyboard_mode": "english", "keyboard_composition": "",
                                                                         "search_scope": expected_scope})
            posts = [request for request in provider.requests[before:] if request["method"] == "POST" and "/series/list" in request["path"]]
            self.check(case, "Cancel or typing-scope selection preserves input without submitting a source request", len(posts) == 25, len(posts))
            case["visible_ui_requirements"] = ["The typed text remains visibly intact and the native scope state reflects cancel or actual final-source selection."]
            self.persist_case(case)

    def reader_operations(self) -> None:
        for device, landscape in (("pw5", False), ("scribe", False), ("scribe", True)):
            label = f"{device}-{'landscape' if landscape else 'portrait'}"
            width, height = native.PROFILES[device]
            if landscape:
                width, height = height, width
            step = 2 if landscape else 1
            for direction, x in (("rtl", width * 0.1), ("ltr", width * 0.9)):
                replay = Replay(start=3500).intent("settings", "set", f"reading_direction={direction}", wait=2000).tap(x, height / 2, wait=2500)
                self.capture(f"{label}--{direction}-page-input", "reader", device=device, landscape=landscape,
                             replay=replay, expected={"reader_page": 12 + step, "menu": False}, pure=True, capture_delay=1500)
            held_menu = self.capture(f"{label}--menu-suppresses-page-turn", "reader", device=device, landscape=landscape,
                                     replay=Replay(start=3500).intent("reader", "menu").intent("reader", "next"),
                                     expected={"reader_page": 12, "menu": True}, modal="")
            self.persist_case(held_menu)
            self.capture(f"{label}--toc-bookmarks", "reader", device=device, landscape=landscape,
                         replay=Replay(start=3500).intent("reader", "bookmark").intent("reader", "menu")
                         .intent("reader", "toc").intent("reader", "toc-tab", "bookmarks"),
                         modal="choices", context="reader-toc", expected={"reader_toc_tab": "bookmarks"})
            self.capture(f"{label}--reader-panel-close-pure", "reader", device=device, landscape=landscape,
                         replay=Replay(start=3500).intent("reader", "menu").intent("reader", "mode")
                         .intent("panel", "close").intent("reader", "menu"), pure=True)
            baseline = self.capture(f"{label}--resume-checkpoint", "reader", device=device, landscape=landscape,
                                    replay=Replay(start=3500).intent("reader", "seek", "20", wait=3000),
                                    pure=True, expected={"reader_page": 20})
            restored = self.capture(f"{label}--resume-restored", "library", device=device,
                                    data=Path(baseline["data_dir"]), resume=True, expected_route="reader",
                                    expected_landscape=landscape, pure=True,
                                    expected={"reader_page": 20}, capture_delay=4500)
            self.compare_outside(restored, baseline, (0, 0, 0, 0), "A fresh --resume process restores every closed-reader artwork pixel")
            self.persist_case(restored)
            returned = self.capture(f"{label}--orientation-return", "reader", device=device, landscape=landscape,
                                    replay=Replay(start=3500).intent("settings", "set", "orientation=reverse-landscape", wait=1500)
                                    .intent("reader", "back"), expected_route="detail",
                                    expected_landscape=False, expected={"display_landscape": False}, capture_delay=1600)
            self.check(returned, "Leaving a manually reversed reader restores actual portrait rotation", returned["metadata"].get("rotation_degrees") == 0, returned["metadata"].get("rotation_degrees"))
            self.persist_case(returned)
            last = self.capture(f"{label}--last-page", "reader", device=device, landscape=landscape,
                                replay=Replay(start=3500).intent("reader", "seek", "40", wait=3500),
                                pure=True, expected={"reader_page": 40})
            self.check(last, "The final page uses one actual full-viewport committed image",
                       isinstance(last["state"].get("displayed_reader"), dict)
                       and last["state"]["displayed_reader"].get("second_path") is None,
                       last["state"].get("displayed_reader"))
            self.persist_case(last)
            self.reader_vertical_case(label, device, landscape, width, height)
            self.reader_extra(label, device, landscape, width, height, step)
        self.c4_reader_features()
        c5_reader.run_reader_routes(self, self.c4_api())

    def create_distinct_cbz(self, name: str, pages: int = 5) -> Path:
        archive = self.data_root / f"{name}.cbz"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
            for number in range(1, pages + 1):
                original = Image.new("L", (600, 800), number * 34)
                encoded = io.BytesIO()
                original.save(encoded, format="PNG")
                target.writestr(f"page-{number:02}.png", encoded.getvalue())
            target.writestr("ComicInfo.xml", "<ComicInfo><Series>Distinct page fixture</Series><Title>Chapter 1</Title><Number>1</Number></ComicInfo>")
        return archive

    def c4_reader_retry_cases(self) -> None:
        controls = self.capture("scribe--c4-reader--reverse-controls", "reader", device="scribe",
                                replay=Replay(start=3500).intent("settings", "set", "orientation=reverse-landscape", wait=2500)
                                .intent("reader", "menu").intent("reader", "orientation"),
                                expected_landscape=True, modal="choices", context="reader-orientation",
                                expected={"display_rotation_degrees": 270, "reverse_landscape": True})
        self.check(controls, "The reverse controls are captured only after the actual landscape frame commits",
                   controls["state"].get("reader_inflight") is False
                   and controls["state"].get("displayed_reader", {}).get("session", {}).get("landscape") is True,
                   controls["state"].get("displayed_reader"))
        self.check(controls, "The selected native reverse card and metadata still describe 270 degrees",
                   controls["metadata"].get("rotation_degrees") == 270
                   and any(row.get("id") == "reverse-landscape" and bool(row.get("value")) for row in controls["state"].get("modal_items", [])),
                   controls["state"].get("modal_items"))
        self.persist_case(controls)
        imported = self.capture("scribe--c4-cover--bright-retry-import", demo=False, device="scribe",
                                import_path=self.create_distinct_cbz("c4-bright-retry", 5), prerequisite=True, capture_delay=1800)
        case = self.capture("scribe--c4-cover--pair-four-five", "reader", device="scribe", landscape=True, demo=False,
                            data=Path(imported["data_dir"]), pure=True,
                            replay=Replay(start=3500).intent("settings", "set", "dither=false", wait=100)
                            .intent("settings", "set", "crop-borders=false", wait=100)
                            .intent("settings", "set", "page-layout-mode=double", wait=100)
                            .intent("settings", "set", "cover-alone=true", wait=100)
                            .intent("reader", "seek", "4", wait=2500))
        self.reader_frame_pages(case, [4, 5])

    def reader_frame_pages(self, case: dict[str, Any], expected: list[int]) -> None:
        self.expect(case, "reader_visible_pages", expected)
        displayed = case["state"].get("displayed_reader", {})
        paths = [displayed.get("first_path")] + ([displayed.get("second_path")] if len(expected) == 2 else [])
        for page, path in zip(expected, paths):
            if not path:
                self.check(case, "The visible source page has a real committed processed image", False, {"page": page, "path": path})
                continue
            with Image.open(path) as original:
                gray = original.convert("L")
                marker = gray.getpixel((gray.width // 2, gray.height // 2))
            self.check(case, "The real processed image retains the independently identifiable source-page pixels",
                       marker == page * 34, {"page": page, "actual_center_gray": marker, "path": path})
        self.persist_case(case)

    def c4_reader_features(self) -> None:
        for device, landscape in (("pw5", False), ("scribe", True)):
            label = f"{device}--c4-reader"
            width, height = native.PROFILES[device]
            if landscape:
                width, height = height, width

            def input_base(layout: str, reverse: bool = False) -> Replay:
                return (Replay(start=3500).intent("settings", "set", "page-layout-mode=single", wait=1500)
                        .intent("settings", "set", f"tap-layout={layout}", wait=100)
                        .intent("settings", "set", f"reverse-taps={str(reverse).lower()}", wait=100))

            for reverse, page in ((False, 13), (True, 11)):
                self.capture(f"{label}--l-shaped-{'reverse' if reverse else 'forward'}", "reader", device=device, landscape=landscape,
                             replay=input_base("l-shaped", reverse).tap(width * 0.9, height * 0.9, wait=2000),
                             expected={"reader_page": page, "reverse_taps": reverse}, pure=True)
            self.capture(f"{label}--swipe-only-tap", "reader", device=device, landscape=landscape,
                         replay=input_base("swipe-only").tap(width * 0.1, height * 0.5),
                         expected={"reader_page": 12, "menu": False}, pure=True)
            for reverse, page in ((False, 13), (True, 11)):
                self.capture(f"{label}--swipe-only-{'reverse' if reverse else 'forward'}", "reader", device=device, landscape=landscape,
                             replay=input_base("swipe-only", reverse).swipe(width * 0.25, height * 0.5, width * 0.75, height * 0.5),
                             expected={"reader_page": page, "reverse_taps": reverse}, pure=True)
            controls = self.capture(f"{label}--reverse-controls", "reader", device=device,
                                    replay=Replay(start=3500).intent("settings", "set", "orientation=reverse-landscape", wait=2500)
                                    .intent("reader", "menu").intent("reader", "orientation"),
                                    expected_landscape=True, modal="choices", context="reader-orientation",
                                    expected={"display_rotation_degrees": 270, "reverse_landscape": True})
            self.check(controls, "The native manual-orientation panel contains an actually selected reverse-landscape card",
                       any(row.get("id") == "reverse-landscape" and bool(row.get("value")) for row in controls["state"].get("modal_items", [])),
                       controls["state"].get("modal_items"))
            self.check(controls, "Capture metadata records actual reverse-landscape policy", controls["metadata"].get("rotation_degrees") == 270, controls["metadata"].get("rotation_degrees"))
            controls["visible_ui_requirements"] = ["The reverse-landscape manual card is visibly selected, with a truthful orientation label."]
            self.persist_case(controls)
            data = Path(controls["data_dir"])
            reverse = self.capture(f"{label}--reverse-pure-input", "reader", device=device, data=data,
                                   replay=Replay(start=3500).tap((824 if device == "pw5" else 1240) * 0.1,
                                                                (618 if device == "pw5" else 930) * 0.5, wait=2000),
                                   expected_landscape=True, expected={"display_rotation_degrees": 270,
                                                                     "reader_page": 13 if device == "pw5" else 14}, pure=True,
                                   note="Desktop 90/270 captures retain the canonical logical raster. Native framebuffer/evdev rotation qualification is separate external evidence.")
            self.check(reverse, "The committed reverse-reader input capture records 270 degrees", reverse["metadata"].get("rotation_degrees") == 270, reverse["metadata"].get("rotation_degrees"))
            reverse["external_evidence_required"] = [{"scope": "native-reverse-rotation-mapping", "status": "external_evidence_required",
                                                       "reason": "Desktop snapshots use the canonical logical raster; actual native framebuffer and evdev rotation mapping need Root-owned native evidence."}]
            self.persist_case(reverse)
            restored = self.capture(f"{label}--reverse-restore", "library", device=device, data=data, resume=True,
                                    expected_route="reader", expected_landscape=True, pure=True,
                                    expected={"display_rotation_degrees": 270, "reader_page": reverse["state"].get("reader_page")})
            self.compare_outside(restored, reverse, (0, 0, 0, 0), "A cold reverse-landscape restore preserves every committed artwork pixel")
            self.persist_case(restored)
            self.capture(f"{label}--reverse-wake", "reader", device=device, data=data, resume=True,
                         expected_route="reader", expected_landscape=True, pure=True,
                         replay=Replay(start=3500).intent("lifecycle", "suspend", wait=500).intent("lifecycle", "resume", wait=100)
                         .intent("lifecycle", "frame-displayed", wait=1500), expected={"display_rotation_degrees": 270})
        self.cover_alone_sequence()

    def cover_alone_sequence(self) -> None:
        device = "scribe"
        imported = self.capture("scribe--c4-cover--import", demo=False, device=device,
                                import_path=self.create_distinct_cbz("c4-cover", 5), prerequisite=True, capture_delay=1800)
        data = Path(imported["data_dir"])

        def style() -> Replay:
            return (Replay(start=3500).intent("settings", "set", "dither=false", wait=100)
                    .intent("settings", "set", "crop-borders=false", wait=100)
                    .intent("settings", "set", "page-layout-mode=double", wait=100)
                    .intent("settings", "set", "cover-alone=true", wait=100))

        first = self.capture("scribe--c4-cover--first-full", "reader", device=device, landscape=True, demo=False, data=data,
                             replay=style().intent("reader", "seek", "1", wait=2500), pure=True)
        self.reader_frame_pages(first, [1])
        layout = self.capture("scribe--c4-cover--layout-controls", "reader", device=device, demo=False, data=data,
                              expected_landscape=True, replay=Replay(start=3500).intent("reader", "menu").intent("reader", "layout"),
                              modal="choices", context="reader-layout", expected={"cover_alone": True, "visible_cover_alone": True})
        layout["visible_ui_requirements"] = ["The real separate-cover toggle is visibly enabled in the native Layout surface."]
        self.persist_case(layout)
        for name, actions, expected in (
            ("pair-two-three", [("next", "")], [2, 3]),
            ("pair-four-five", [("next", "")], [4, 5]),
            ("previous-pair", [("previous", "")], [2, 3]),
            ("seek-normalized", [("seek", "3")], [2, 3]),
        ):
            replay = Replay(start=3500)
            for action, value in actions:
                replay.intent("reader", action, value, wait=2500)
            case = self.capture(f"scribe--c4-cover--{name}", "reader", device=device, demo=False, data=data,
                                expected_landscape=True, replay=replay, pure=True)
            self.reader_frame_pages(case, expected)
        toggled = self.capture("scribe--c4-cover--checkpoint-toggle", "reader", device=device, demo=False, data=data,
                               expected_landscape=True, replay=Replay(start=3500).intent("settings", "set", "cover-alone=false", wait=100)
                               .intent("reader", "seek", "3", wait=500).intent("settings", "set", "cover-alone=true", wait=2000),
                               expected={"reader_page": 3}, pure=True)
        self.reader_frame_pages(toggled, [2, 3])
        restored = self.capture("scribe--c4-cover--restore", "library", device=device, demo=False, data=data, resume=True,
                                expected_route="reader", expected_landscape=True, pure=True, expected={"reader_page": 3})
        self.reader_frame_pages(restored, [2, 3])
        self.compare_outside(restored, toggled, (0, 0, 0, 0), "Cover alignment and the original source-page checkpoint survive a cold restore")
        self.check(restored, "Cover-aligned rendering uses real foreground/prefetch cache requests",
                   restored["state"].get("render_cache", {}).get("foreground_requests", 0) > 0
                   and restored["state"].get("render_cache", {}).get("prefetch_requests", 0) > 0,
                   restored["state"].get("render_cache"))
        self.persist_case(restored)

    def reader_vertical_case(self, label: str, device: str, landscape: bool, width: int, height: int) -> None:
        vertical = self.capture(f"{label}--vertical-bottom-right", "reader", device=device, landscape=landscape,
                                replay=Replay(start=3500).intent("settings", "set", "reading_direction=vertical", wait=3000)
                                .tap(width * 0.93, height * 0.96, wait=3000), pure=True)
        self.check(vertical, "The bottom-right region advances actual vertical reading without footer cycling",
                   vertical["state"].get("reader_page", 0) > 12 or vertical["state"].get("reader_offset", 0) > 0,
                   {key: vertical["state"].get(key) for key in ("reader_page", "reader_offset")})
        self.preference(vertical, "footer_mode", 0)
        self.check(vertical, "The visible vertical-reader page is the committed requested page",
                   vertical["state"].get("visible_reader_page") == vertical["state"].get("reader_page"),
                   {key: vertical["state"].get(key) for key in ("reader_page", "visible_reader_page", "reader_inflight", "busy")})
        self.persist_case(vertical)

    def reader_extra(self, label: str, device: str, landscape: bool, width: int, height: int, step: int) -> None:
        split = 0.30 if landscape else 0.33
        for suffix, fraction, menu, page in (
            ("side", split - 0.01, False, 12 + step),
            ("center", split + 0.01, True, 12),
        ):
            self.capture(f"{label}--tap-boundary-{suffix}", "reader", device=device, landscape=landscape,
                         replay=Replay(start=3500).tap(width * fraction, height / 2, wait=2500),
                         expected={"menu": menu, "reader_page": page}, pure=not menu)
        self.capture(f"{label}--reversed-tap-input", "reader", device=device, landscape=landscape,
                     replay=Replay(start=3500).intent("settings", "set", "tap-layout=reversed", wait=1000)
                     .tap(width * 0.1, height / 2, wait=2500),
                     expected={"reader_page": 12 - step, "menu": False}, pure=True)
        self.capture(f"{label}--first-page", "reader", device=device, landscape=landscape,
                     replay=Replay(start=3500).intent("reader", "seek", "1", wait=2500),
                     expected={"reader_page": 1}, pure=True)
        changed = self.capture(f"{label}--next-chapter", "reader", device=device, landscape=landscape,
                               replay=Replay(start=3500).intent("reader", "next-chapter", wait=3000),
                               expected={"reader_page": 1}, pure=True)
        original = self.sample_by_profile.get(f"{device}-reader-{landscape}", {})
        self.check(changed, "Next-chapter resolves and displays a different actual chapter",
                   changed["state"].get("current_chapter") != original.get("state", {}).get("current_chapter")
                   and bool(changed["state"].get("current_chapter")), changed["state"].get("current_chapter"))
        self.persist_case(changed)
        toc = self.capture(f"{label}--toc-last-page", "reader", device=device, landscape=landscape,
                           replay=Replay(start=3500).intent("reader", "menu").intent("reader", "toc")
                           .intent("panel", "page", "99999"), modal="choices", context="reader-toc")
        self.check(toc, "Chapter ToC reaches its real final page with the profile row capacity",
                   toc["state"].get("panel_page") == toc["state"].get("panel_page_count")
                   and 0 < len(toc["state"].get("modal_items", [])) <= (6 if device == "scribe" else 5),
                   {key: toc["state"].get(key) for key in ("panel_page", "panel_page_count", "modal_item_ids")})
        self.persist_case(toc)
        self.capture(f"{label}--toc-page-jump", "reader", device=device, landscape=landscape,
                     replay=Replay(start=3500).intent("reader", "menu").intent("reader", "toc")
                     .intent("page", "jump-toggle", "panel"), modal="choices", context="reader-toc",
                     expected={"page_jump_open": True, "page_jump_context": "panel"})
        before = self.capture(f"{label}--bookmark-baseline", "reader", device=device, landscape=landscape, pure=True)
        toggled = self.capture(f"{label}--bookmark-toggle-pure", "reader", device=device, landscape=landscape,
                               data=Path(before["data_dir"]),
                               replay=Replay(start=3500).intent("reader", "bookmark"), pure=True,
                               expected={"reader_bookmarked": not before["state"].get("reader_bookmarked")})
        self.compare_outside(toggled, before, (0, 0, 0, 0), "Bookmark state never paints a closed-reader ribbon or corner")
        self.persist_case(toggled)
        restored = self.capture(f"{label}--bookmark-cold-persistence", "reader", device=device, landscape=landscape,
                                data=Path(before["data_dir"]), pure=True,
                                expected={"reader_bookmarked": toggled["state"].get("reader_bookmarked")})
        self.persist_case(restored)
        for footer in (1, 2, 3):
            replay = Replay(start=3500)
            for _ in range(footer):
                replay.intent("reader", "footer", wait=100)
            case = self.capture(f"{label}--legacy-footer-{footer}-pure", "reader", device=device, landscape=landscape,
                                replay=replay, pure=True,
                                note="A stored legacy footer preference never restores closed-reader labels or a footer-cycle hit region.")
            self.preference(case, "footer_mode", footer)
            self.persist_case(case)
        self.capture(f"{label}--zones-closed-pure", "reader", device=device, landscape=landscape,
                     replay=Replay(start=3500).intent("settings", "set", "show-tap-zones=true"),
                     expected={"show_tap_zones": True}, pure=True)
        contrast = self.capture(f"{label}--image-setting-invalidation", "reader", device=device, landscape=landscape,
                                data=Path(before["data_dir"]),
                                replay=Replay(start=3500).intent("settings", "set", "contrast=1", wait=3000), pure=True)
        prior_path = before["state"].get("displayed_reader", {}).get("first_path")
        current_path = contrast["state"].get("displayed_reader", {}).get("first_path")
        self.check(contrast, "Changing image processing invalidates the processed-image cache identity",
                   bool(current_path) and current_path != prior_path
                   and contrast["state"].get("reader_effective", {}).get("contrast") == 10,
                   {"before": prior_path, "after": current_path})
        self.persist_case(contrast)
        self.reader_seek(label, device, landscape, width, height)
        self.reader_local_fixtures(label, device, landscape, width, height)

    def reader_seek(self, label: str, device: str, landscape: bool, width: int, height: int) -> None:
        inset = (120 if landscape else 48) if device == "scribe" else 20
        bottom = 210 if device == "scribe" else 188
        range_top, range_height = (45, 40) if device == "scribe" else (39, 36)
        y = height - bottom + range_top + range_height / 2
        target = 32
        start_x = inset + (1 - 12 / 40) * (width - 2 * inset)
        target_x = inset + (1 - target / 40) * (width - 2 * inset)

        def drag(release: bool) -> Replay:
            replay = Replay(start=3500).intent("reader", "menu")
            replay.event("pointer", "move", f"{start_x:g},{y:g}")
            replay.event("pointer", "press", f"{start_x:g},{y:g}", 60)
            replay.event("pointer", "move", f"{target_x:g},{y:g}", 120)
            if release:
                replay.event("pointer", "release", f"{target_x:g},{y:g}", 120)
                replay.at += 2500
            else:
                replay.at += 300
            return replay

        menu = self.capture(f"{label}--seek-menu-baseline", "reader", device=device, landscape=landscape,
                            replay=Replay(start=3500).intent("reader", "menu"), expected={"menu": True, "reader_page": 12})
        preview = self.capture(f"{label}--seek-preview", "reader", device=device, landscape=landscape,
                               replay=drag(False), expected={"menu": True, "reader_page": 12}, capture_delay=300)
        with Image.open(menu["screenshot"]) as original, Image.open(preview["screenshot"]) as shown:
            rectangle = (0, height - bottom, width, height - (77 if device == "scribe" else 69))
            difference = ImageChops.difference(original.convert("RGB").crop(rectangle), shown.convert("RGB").crop(rectangle))
            self.check(preview, "Dragging seek previews real visible range controls before committing the reader session",
                       difference.getbbox() is not None, list(rectangle))
        self.persist_case(preview)
        self.capture(f"{label}--seek-release", "reader", device=device, landscape=landscape,
                     replay=drag(True), expected={"menu": False, "reader_page": target}, pure=True, capture_delay=1600)

    def reader_local_fixtures(self, label: str, device: str, landscape: bool, width: int, height: int) -> None:
        cbz = self.create_cbz(f"{label}--odd-cbz")
        imported = self.capture(f"{label}--odd-cbz-import", device=device, demo=False,
                                import_path=cbz, expected={"library_count": 1}, capture_delay=1700)
        odd = self.capture(f"{label}--odd-final-spread", "reader", device=device, landscape=landscape,
                           data=Path(imported["data_dir"]), demo=False,
                           replay=Replay(start=3500).intent("reader", "seek", "3", wait=2500),
                           expected={"reader_page": 3, "reader_total": 3}, pure=True)
        self.check(odd, "A real odd-length local chapter ends with one full-viewport image",
                   odd["state"].get("displayed_reader", {}).get("second_path") is None,
                   odd["state"].get("displayed_reader"))
        self.persist_case(odd)
        cbz = self.create_vertical_cbz(f"{label}--webtoon-cbz")
        imported = self.capture(f"{label}--webtoon-import", device=device, demo=False,
                                import_path=cbz, expected={"library_count": 1}, capture_delay=1700)
        vertical = self.capture(f"{label}--webtoon-step", "reader", device=device, landscape=landscape,
                                data=Path(imported["data_dir"]), demo=False,
                                replay=Replay(start=3500).intent("settings", "set", "reading-direction=vertical", wait=3000)
                                .tap(width / 2, height * 0.9, wait=3000), pure=True)
        viewport = vertical["state"].get("reader_viewport_height")
        image_height = vertical["state"].get("reader_image_height")
        expected = min(int(viewport * 0.85 + 0.5), max(0, image_height - viewport)) if isinstance(viewport, int) and isinstance(image_height, int) else None
        self.check(vertical, "The actual webtoon advances by an 85-percent viewport step",
                   expected is not None and expected > 0 and vertical["state"].get("reader_offset") == expected,
                   {"viewport_height": viewport, "image_height": image_height, "offset": vertical["state"].get("reader_offset"), "expected": expected})
        self.persist_case(vertical)

    def missing_capabilities(self) -> None:
        no_light = self.capture("capability--no-frontlight-settings", "settings", device="scribe3-nofrontlight",
                                expected={"has_frontlight": False, "has_warmth": False},
                                note="This real device profile declares no frontlight; dependent controls must be absent.")
        setting_ids = no_light["state"].get("visible_setting_ids", [])
        self.check(no_light, "A no-frontlight profile hides the lighting navigation entry",
                   "frontlight-warmth" not in setting_ids and "frontlight" not in setting_ids, setting_ids)
        self.persist_case(no_light)
        reader = self.capture("capability--no-frontlight-reader-menu", "reader", device="scribe3-nofrontlight",
                              replay=Replay(start=3500).intent("reader", "menu"), expected={"has_frontlight": False, "menu": True},
                              note="The absent light tool must be reviewed on the native menu; no imaginary hardware capability is enabled.")
        self.persist_case(reader)

    def finish_manifest(self) -> int:
        self.freeze_check()
        after = source_manifest(self.args.source_root)
        source_unchanged = after["sha256"] == self.source["sha256"]
        if not source_unchanged:
            self.cases.append({"name": "source-immutability", "passed": False, "manual_review_required": False,
                               "checks": [{"description": "Production source remains frozen throughout the round", "passed": False,
                                           "actual": after["sha256"]}]})
        missing = sorted(set(self.reference_ids) - set(self.coverage))
        missing_groups = sorted(set(self.required_groups) - set(self.completed_groups))
        required_c5_ids = sorted((*c5_ui.CASE_IDS, *c5_reader.CASE_IDS))
        missing_c5_ids = sorted(set(required_c5_ids) - {case["name"] for case in self.cases})
        required_c10_ids = sorted(c10_search.CASE_IDS)
        missing_c10_ids = sorted(set(required_c10_ids) - {case["name"] for case in self.cases})
        required_c13_ids = sorted(c13_scope.CASE_IDS)
        missing_c13_ids = sorted(set(required_c13_ids) - {case["name"] for case in self.cases})
        failures = [case["name"] for case in self.cases if not case.get("passed")]
        review_ids = sorted(case["name"] for case in self.cases if case.get("manual_review_required"))
        manifest = {
            "schema_version": 1, "kind": "phase3-handoff-native-ui-round", "round": self.args.round,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "run_id": self.run_id,
            "host": platform.node(), "platform": platform.platform(), "python": platform.python_version(),
            "binary": str(self.binary), "binary_sha256": self.binary_hash, "binary_unchanged": True,
            "source_root": str(self.args.source_root.resolve()), "source_sha256": self.source["sha256"],
            "source_sha256_after": after["sha256"], "source_unchanged": source_unchanged,
            "source_file_manifest": str(self.output / "source-files.json"),
            "reference_manifest": str(self.output / "reference-files.json"), "reference_sha256": self.design["sha256"],
            "reference_scope_sha256": self.design["scope_sha256"], "capture_plan_sha256": self.design["plan_sha256"],
            "capture_method": "native Slint software-renderer application --capture with real --actions",
            "settled_reader_tail_ms": self.args.settled_reader_tail_ms,
            "selected_reader_vertical_case": self.args.reader_vertical_case,
            "selected_c4_subset": self.args.subset if self.args.subset and self.args.subset.startswith("c4-") else None,
            "selected_native_subset": self.args.subset,
            "harness_sha256": file_hash(Path(__file__)),
            "capture_helper_sha256": {name: file_hash(ROOT / "scripts" / name)
                                      for name in ("verify-ui.py", "verify-fullscreen.py", "handoff_c4_business.py", "handoff_c5_ui.py", "handoff_c5_reader.py", "handoff_c9_search.py", "handoff_c10_search.py", "handoff_c12_scope.py", "handoff_c13_scope.py")},
            "original_screenshots_modified": False, "data_root": str(self.data_root),
            "fresh_data_per_round": True, "required_reference_ids": self.reference_ids,
            "reference_coverage": self.coverage, "missing_reference_ids": missing,
            "reference_coverage_complete": not missing and len(self.reference_ids) == 101,
            "required_supplemental_groups": self.required_groups,
            "completed_supplemental_groups": self.completed_groups,
            "missing_supplemental_groups": missing_groups,
            "required_c5_control_ids": required_c5_ids,
            "missing_c5_control_ids": missing_c5_ids,
            "c5_native_control_coverage_complete": not missing_c5_ids,
            "required_c10_search_identity_ids": required_c10_ids,
            "missing_c10_search_identity_ids": missing_c10_ids,
            "c10_search_identity_coverage_complete": not missing_c10_ids,
            "required_c13_source_shortcut_ids": required_c13_ids,
            "missing_c13_source_shortcut_ids": missing_c13_ids,
            "c13_source_shortcut_coverage_complete": not missing_c13_ids,
            "full_capture_suite_completed": not missing and not missing_groups and not missing_c5_ids and not missing_c10_ids and not missing_c13_ids,
            "external_scope_requirements": [{
                "scope_case_id": "performance-and-resume", "owner": "Root-managed native/performance verification",
                "required_coverage": sorted(EXTERNAL_PERFORMANCE_COVERAGE),
                "capture_suite_repeats_external_work": False,
            }],
            "required_review_case_ids": review_ids, "supplemental_case_ids": sorted(self.supplemental_ids),
            "passed": len(self.cases) - len(failures), "failed": len(failures), "failed_cases": failures,
            "mechanical_success": not failures and not missing and not missing_groups and not missing_c5_ids and not missing_c10_ids and not missing_c13_ids and source_unchanged,
            "elapsed_seconds": round(time.monotonic() - self.started, 3), "cases": self.cases,
            "review": {"recorded": False, "coverage_complete": False, "clean_round": False,
                       "clean_round_streak": 0, "accepted": False, "suggestions": [],
                       "reason": "Independent complete manual review has not yet been recorded."},
            "limitations": [
                "Desktop captures do not qualify physical Kindle light, pen, sensors, power, firmware input or e-ink waveforms.",
                "Fixture titles, covers, dates and counts differ from the illustrative handoff data; this does not waive geometry or behavior.",
                "External authorization remains unavailable unless the application actually implements it; no account or code is simulated.",
                "Mechanical assertions and full matrix capture do not establish visual acceptance or a clean round.",
            ],
        }
        save_json(self.output / "manifest.json", manifest)
        lines = ["# Native handoff capture round", "", f"Round: {self.args.round}",
                 f"Binary SHA256: `{self.binary_hash}`", f"Production source SHA256: `{self.source['sha256']}`",
                 f"Reference SHA256: `{self.design['sha256']}`", "",
                 f"Reference coverage: {len(self.coverage)}/101. Mechanical failures: {len(failures)}.",
                 "Independent manual review is still required. No clean round is inferred from this report.", "",
                 "| Case | Reference | Mechanical result | Original native capture |", "| --- | --- | --- | --- |"]
        for case in self.cases:
            name = case["name"]
            image = f"[PNG]({name}.png)" if case.get("screenshot") else "Unavailable"
            lines.append(f"| {name} | {case.get('reference_id', 'Supplemental')} | {'PASS' if case.get('passed') else 'FAIL'} | {image} |")
        (self.output / "manifest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        save_json(self.output / "review-template.json", {
            "round": self.args.round, "run_id": self.run_id, "reviewer": "",
            "reviewed_case_ids": [], "suggestions": [], "intentional_differences_reviewed": False,
            "binary_sha256": self.binary_hash, "source_sha256": self.source["sha256"],
            "reference_sha256": self.design["sha256"],
            "external_evidence": [],
        })
        print(f"Native handoff round {self.args.round}: {len(self.coverage)}/101 reference captures, {len(failures)} mechanical failures. Manual review required.", flush=True)
        return 0 if manifest["mechanical_success"] else 1


def record_review(args: argparse.Namespace) -> int:
    path = args.output / "manifest.json"
    manifest = read_json(path)
    review = read_json(args.record_review)
    if manifest.get("kind") != "phase3-handoff-native-ui-round":
        raise ValueError("The output does not contain a native handoff capture manifest")
    if manifest.get("round") != args.round or review.get("round") != args.round:
        raise ValueError("The review and capture round must match --round")
    for key in ("run_id", "binary_sha256", "source_sha256", "reference_sha256"):
        if review.get(key) != manifest.get(key):
            raise ValueError(f"The review does not identify this exact capture: {key}")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ValueError("An independent review must identify its reviewer")
    supplied = review.get("reviewed_case_ids")
    required = manifest.get("required_review_case_ids", [])
    if not isinstance(supplied, list) or len(supplied) != len(set(supplied)) or set(supplied) != set(required):
        raise ValueError("The review must explicitly identify every required matrix and supplemental native capture")
    if review.get("intentional_differences_reviewed") is not True:
        raise ValueError("The reviewer must explicitly inspect all recorded intentional differences and capability bounds")
    for case in manifest.get("cases", []):
        for evidence in case.get("evidence_hashes", {}).values():
            if file_hash(Path(evidence["path"])) != evidence["sha256"]:
                raise ValueError("A captured evidence file changed after capture")
    previous = read_json(args.previous_manifest) if args.previous_manifest else None
    if previous is not None and (previous.get("kind") != manifest["kind"] or previous.get("schema_version") != manifest["schema_version"]):
        raise ValueError("The preceding review must use the same native handoff manifest schema")
    prior = {item["id"]: item for item in (previous or {}).get("review", {}).get("suggestions", [])}
    merged = dict(prior)
    suggestions = review.get("suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("The review suggestions must be an explicit array")
    seen = set()
    for item in suggestions:
        needed = {"id", "case_id", "description", "evidence", "status", "introduced_round"}
        if not isinstance(item, dict) or not needed.issubset(item):
            raise ValueError("Each suggestion needs id, case_id, description, evidence, status and introduced_round")
        identifier = item["id"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("Suggestion identifiers must be unique nonempty strings")
        seen.add(identifier)
        if item["case_id"] not in required or item["status"] not in ("open", "fixed"):
            raise ValueError("A suggestion refers to an unknown case or status")
        if not item["description"] or not item["evidence"]:
            raise ValueError("A suggestion must include concrete evidence")
        if identifier in prior:
            if item["introduced_round"] != prior[identifier]["introduced_round"] or item["case_id"] != prior[identifier]["case_id"]:
                raise ValueError("An existing suggestion must retain its original round and case")
        elif item["introduced_round"] != args.round:
            raise ValueError("A new suggestion must be introduced in this round")
        merged[identifier] = item
    new = sorted(set(merged) - set(prior))
    unresolved = sorted(identifier for identifier, item in merged.items() if item["status"] == "open")
    external = review.get("external_evidence", [])
    if not isinstance(external, list):
        raise ValueError("external_evidence must be an explicit artifact array")
    external_coverage: set[str] = set()
    satisfied_external_scopes: set[str] = set()
    retained_external = []
    for item in external:
        required_fields = {"scope_case_id", "owner", "artifact", "sha256", "source_sha256", "passed", "coverage"}
        if not isinstance(item, dict) or not required_fields.issubset(item):
            raise ValueError("External evidence needs scope_case_id, owner, artifact, sha256, source_sha256, passed and coverage")
        if item["source_sha256"] != manifest["source_sha256"]:
            raise ValueError("External evidence belongs to a different production source snapshot")
        artifact = Path(item["artifact"])
        if not artifact.is_file() or file_hash(artifact) != item["sha256"]:
            raise ValueError("An external native/performance artifact is missing or changed")
        if not isinstance(item["coverage"], list) or not all(isinstance(part, str) for part in item["coverage"]):
            raise ValueError("External evidence coverage must be a string array")
        if item["scope_case_id"] == "performance-and-resume" and item["passed"] is True:
            external_coverage.update(item["coverage"])
        if item["passed"] is True:
            satisfied_external_scopes.add(item["scope_case_id"])
        retained_external.append(item)
    required_external_scopes = {requirement["scope"] for case in manifest.get("cases", [])
                                for requirement in case.get("external_evidence_required", [])
                                if requirement.get("status") == "external_evidence_required"}
    missing_external_scopes = required_external_scopes - satisfied_external_scopes
    external_complete = EXTERNAL_PERFORMANCE_COVERAGE.issubset(external_coverage) and not missing_external_scopes
    same_candidate = bool(previous) and all(previous.get(key) == manifest.get(key) for key in
                                           ("binary_sha256", "source_sha256", "reference_sha256", "reference_scope_sha256",
                                            "capture_plan_sha256", "harness_sha256", "capture_helper_sha256", "required_review_case_ids"))
    consecutive = bool(previous) and previous.get("round") == args.round - 1 and previous.get("run_id") != manifest.get("run_id")
    clean = (manifest.get("mechanical_success") is True and manifest.get("reference_coverage_complete") is True
             and manifest.get("full_capture_suite_completed") is True and external_complete and not new and not unresolved)
    streak = 1 if clean else 0
    if clean and same_candidate and consecutive and previous.get("review", {}).get("clean_round") is True:
        streak += previous["review"].get("clean_round_streak", 0)
    manifest["review"] = {
        "recorded": True, "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reviewer": review["reviewer"], "source": str(args.record_review.resolve()),
        "source_sha256": manifest["source_sha256"], "review_file_sha256": file_hash(args.record_review),
        "reviewed_case_ids": supplied,
        "coverage_complete": True, "suggestions": [merged[key] for key in sorted(merged)],
        "new_suggestion_ids": new, "unresolved_suggestion_ids": unresolved,
        "carried_forward_suggestion_ids": sorted(set(prior) - seen),
        "same_candidate_as_previous": same_candidate, "consecutive_fresh_round": consecutive,
        "external_evidence": retained_external, "external_scope_complete": external_complete,
        "missing_external_coverage": sorted(EXTERNAL_PERFORMANCE_COVERAGE - external_coverage),
        "required_external_scopes": sorted(required_external_scopes),
        "missing_external_scopes": sorted(missing_external_scopes),
        "clean_round": clean, "clean_round_streak": streak, "accepted": streak >= 2,
        "previous_manifest": str(args.previous_manifest.resolve()) if args.previous_manifest else None,
        "previous_manifest_sha256": file_hash(args.previous_manifest) if args.previous_manifest else None,
    }
    save_json(path, manifest)
    print(f"Manual review recorded: clean round={clean}, streak={streak}, accepted={streak >= 2}", flush=True)
    return 0


def positive_integer(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("The round must be positive")
    return result


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary", type=Path, help="Root-frozen native desktop executable; never compiled by this script")
    parser.add_argument("--round", type=positive_integer)
    parser.add_argument("--output", "--output-dir", dest="output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--source-manifest-only", action="store_true", help="Write the static production source fingerprint to --output without starting the application")
    parser.add_argument("--scope", type=Path, default=SCOPE)
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--reference-root", type=Path, help="Unmodified supplied handoff root containing screens/")
    parser.add_argument("--expected-sha256", help="Require this exact frozen executable SHA256")
    parser.add_argument("--expected-source-sha256", help="Require this exact production source manifest SHA256")
    parser.add_argument("--timeout", type=int, default=90, help="Maximum seconds for each isolated native process")
    parser.add_argument("--settled-reader-tail-ms", type=positive_integer, default=5000,
                        help="Bounded final idle time for cases requiring a committed reader frame; all identity and zero-pixel assertions remain strict")
    parser.add_argument("--reader-vertical-case", choices=("pw5-portrait", "scribe-portrait", "scribe-landscape"),
                        help="Run only the selected real vertical bottom-right input scenario for fresh diagnostic retry; partial runs are never clean rounds")
    parser.add_argument("--subset", choices=("c4-reader", "c4-reader-retry", "c4-book-storage", "c4-migration", "c4-search", "c4-content", "c4-queue-language", "c5-ui"),
                        help="Run a focused fresh native qualification subset; no subset establishes a complete or clean round")
    parser.add_argument("--matrix-only", action="store_true", help="Capture all 101 reference states only; supplementary function evidence remains incomplete")
    parser.add_argument("--case", dest="selected_cases", action="append", help="Capture only this reference id for remote diagnosis; partial runs can never count as clean rounds")
    parser.add_argument("--record-review", type=Path, help="Attach an independent complete review to existing evidence without running the application")
    parser.add_argument("--previous-manifest", type=Path, help="Immediately preceding complete round manifest")
    args = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run this script only on the designated Linux worker through ssh test-env; local verification is forbidden.")
    if not args.source_manifest_only and args.round is None:
        parser.error("Capture and review require --round")
    if sum(bool(value) for value in (args.subset, args.reader_vertical_case, args.selected_cases)) > 1:
        parser.error("Choose only one of --subset, --reader-vertical-case or repeated --case")
    if not args.record_review and not args.source_manifest_only:
        if args.binary is None or not args.binary.is_file() or not os.access(args.binary, os.X_OK):
            parser.error("Capture requires --binary naming the authorized frozen executable")
        if args.reference_root is None or not args.reference_root.is_dir():
            parser.error("Capture requires --reference-root naming the original supplied handoff directory")
        if shutil.which("xvfb-run") is None:
            parser.error("The designated worker requires xvfb-run")
    return args


def main() -> int:
    args = arguments()
    try:
        if args.source_manifest_only:
            manifest = source_manifest(args.source_root)
            save_json(args.output, manifest)
            print(f"Production source SHA256: {manifest['sha256']} ({manifest['file_count']} files)", flush=True)
            return 0
        if args.record_review:
            return record_review(args)
        suite = Suite(args)
        try:
            if args.subset:
                suite.run_subset(args.subset)
            elif args.reader_vertical_case:
                suite.reference_ids = [entry["id"] for board in suite.scope["pageStateMatrix"]
                                       for entry in [board["default"], *board["states"]]]
                label = args.reader_vertical_case
                device = "pw5" if label.startswith("pw5") else "scribe"
                landscape = label.endswith("landscape")
                width, height = native.PROFILES[device]
                if landscape:
                    width, height = height, width
                suite.reader_vertical_case(label, device, landscape, width, height)
            else:
                suite.reference_matrix()
                if not args.matrix_only and not args.selected_cases:
                    suite.supplementary()
        except Exception as error:
            suite.cases.append({"name": "suite-completion", "passed": False, "manual_review_required": False,
                                "checks": [{"description": "Every required scenario group completed", "passed": False,
                                            "actual": f"{type(error).__name__}: {error}"}]})
        return suite.finish_manifest()
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print(f"Handoff capture failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
