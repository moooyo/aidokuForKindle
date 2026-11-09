#!/usr/bin/env python3
"""Exercise visible reader recovery using real imported images on the remote worker.

Each scenario imports an independent CBZ through the normal application adapter.
Only a source image in that scenario's isolated import directory is moved. Cache
schemas are never fabricated. The immutable product binary owns error handling,
rollback, retry, bookmark/export targeting, and the final captures.
"""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
import zipfile
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageStat


CASES = (
    "failed-next-visible", "retry-failed-target", "recovery-back", "recovery-close",
    "pending-bookmark-save", "pending-set-cover", "first-page-visible", "first-page-retry", "first-page-back",
    "manifest-failure-visible", "manifest-retry-target", "manifest-retrying-visible", "slow-first-image-loading",
    "slow-first-action-visible", "slow-first-action-dismiss",
    "pending-menu-displayed",
)


class KomgaRecovery:
    def __init__(self, *, slow_first: bool = False, slow_retry: bool = False, slow_eight: bool = False):
        self.requests: list[dict[str, Any]] = []
        self.failed_second = False
        self.slow_first = slow_first
        self.slow_retry = slow_retry
        self.slow_eight = slow_eight
        self.images: dict[str, bytes] = {}
        for chapter, level in (("c01", 85), ("c02", 170)):
            image = Image.new("L", (600, 800), level)
            draw = ImageDraw.Draw(image)
            draw.rectangle((12, 12, 587, 787), outline=0, width=8)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            self.images[chapter] = buffer.getvalue()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_: Any) -> None:
                pass

            def reply(self, status: int, payload: Any, image: bool = False) -> None:
                body = payload if image else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "image/png" if image else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self) -> None:
                path = urlsplit(self.path).path
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if path == "/api/v1/series/list":
                    self.reply(200, {"content": [{"id": "s1", "name": "Recovery series", "metadata": {"title": "Recovery series", "status": "ONGOING"}, "booksCount": 2}], "number": 0, "last": True, "totalPages": 1})
                elif path == "/api/v1/books/list":
                    self.reply(200, {"content": [{"id": chapter, "name": f"Chapter {number}", "metadata": {"title": f"Chapter {number}", "numberSort": number}, "media": {"pagesCount": 10 if owner.slow_eight and chapter == "c01" else 1}} for number, chapter in enumerate(("c01", "c02"), 1)]})
                else:
                    self.reply(404, {})

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path.endswith("/thumbnail"):
                    self.reply(200, owner.images["c01"], True)
                    return
                for chapter in ("c01", "c02"):
                    if path == f"/api/v1/books/{chapter}/pages":
                        failed = chapter == "c02" and not owner.failed_second
                        owner.failed_second |= failed
                        owner.requests.append({"path": path, "status": 500 if failed else 200})
                        count = 10 if owner.slow_eight and chapter == "c01" else 1
                        self.reply(500 if failed else 200, {} if failed else [{"number": number, "mediaType": "image/png"} for number in range(1, count + 1)])
                        return
                    if path.startswith(f"/api/v1/books/{chapter}/pages/"):
                        number = path.rsplit("/", 1)[-1]
                        if owner.slow_first and chapter == "c01" or owner.slow_retry and chapter == "c02":
                            time.sleep(8)
                        if owner.slow_eight and chapter == "c01" and number == "8":
                            time.sleep(12)
                        self.reply(200, owner.images[chapter], True)
                        return
                self.reply(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)

    def __enter__(self) -> "KomgaRecovery":
        self.thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"


def load_harness() -> Any:
    path = Path(__file__).with_name("verify-ui.py")
    specification = importlib.util.spec_from_file_location("aidoku_remote_ui_harness", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("The adjacent UI harness could not be loaded")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def label(key: str) -> str:
    labels = json.loads((Path(__file__).resolve().parent.parent / "assets/i18n/zh-CN.json").read_text(encoding="utf-8"))
    return labels[key]


def chapter_page_label(chapter: int, page: int) -> str:
    identity = label("reader_chapter_identity").replace("{chapter}", str(chapter))
    return label("reader_chapter_page").replace("{chapter}", identity).replace("{page}", str(page))


def create_comic(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for number in range(1, 11):
            image = Image.new("L", (600, 800), 255)
            drawing = ImageDraw.Draw(image)
            drawing.rectangle((12, 12, 587, 787), outline=0, width=8)
            drawing.rectangle((40, 100, 559, 699), fill=24 + number * 18)
            drawing.text((40, 50), f"RECOVERY PAGE {number}", fill=0)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            archive.writestr(f"page{number:02}.png", buffer.getvalue())
        archive.writestr("ComicInfo.xml", "<ComicInfo><Series>Reader recovery fixture</Series><Title>One chapter</Title><Number>1</Number></ComicInfo>")


def imported_fixture(suite: Any, name: str) -> tuple[dict[str, Any], str, dict[int, Path]]:
    archive = suite.data_root / f"{name}.cbz"
    create_comic(archive)
    imported = suite.run(f"{name}-import", demo=False, import_path=archive, expected_route="library", capture_delay=1600)
    rows = suite.database_rows(imported, "SELECT chapter_id,position,document FROM pages ORDER BY position")
    suite.check(imported, "Normal CBZ import persisted ten real source images", len(rows) == 10, len(rows))
    if len(rows) != 10:
        raise RuntimeError("The reader recovery fixture did not import its ten pages")
    chapter_id = rows[0][0]
    pages = {int(position) + 1: Path(json.loads(document)["local_path"]) for _, position, document in rows}
    root = suite.data_root.resolve()
    for path in pages.values():
        if not path.resolve().is_relative_to(root):
            raise RuntimeError("An imported recovery image escaped its isolated fixture directory")
    return imported, chapter_id, pages


def recovery_panel(suite: Any, case: dict[str, Any], target: int, displayed: int | None) -> None:
    suite.expect(case, "route", "reader")
    suite.expect(case, "modal", "choices")
    suite.expect(case, "modal_context", "reader-recovery")
    suite.expect(case, "modal_item_ids", ["reader-retry", "reader-recovery-back"])
    recovery = case["state"].get("reader_recovery") or {}
    suite.check(case, "The visible error panel retains the failed target page", recovery.get("target", {}).get("page") == target, recovery)
    suite.check(case, "A missing local source is described by a safe failure category", recovery.get("reason") == "missing_file", recovery.get("reason"))
    frame = case["state"].get("displayed_reader")
    if displayed is None:
        suite.check(case, "A first-page failure has no invented previously displayed frame", frame is None, frame)
    else:
        suite.expect(case, "reader_page", displayed)
        suite.check(case, "A failed next-page request keeps the last actually displayed page", isinstance(frame, dict) and frame.get("session", {}).get("page") == displayed, frame)


def run_scenario(suite: Any, Replay: Any, name: str) -> None:
    if name.startswith("manifest-") or name.startswith("slow-first-") or name == "pending-menu-displayed":
        run_manifest_scenario(suite, Replay, name)
        return
    imported, chapter_id, pages = imported_fixture(suite, name)
    first = name.startswith("first-page")
    target = 1 if first else 8
    original = pages[target]
    hidden = original.with_name(original.name + ".recovery-hidden")
    original.rename(hidden)
    observer: threading.Thread | None = None
    observer_stop = threading.Event()
    restore_evidence: dict[str, Any] = {}
    try:
        if first:
            replay = Replay(start=6000)
        else:
            replay = Replay(start=1500).intent("reader", "seek", "7", wait=4500)
            replay.event("reader", "seek", "8")
        if name == "pending-bookmark-save":
            replay.event("reader", "bookmark").event("panel", "choose", "save-page")
        if name == "pending-set-cover":
            replay.event("panel", "choose", "set-cover")
        if name in ("retry-failed-target", "first-page-retry"):
            # Synchronize with an actual product failure and its committed public
            # progress, not the host/App startup clocks. A slow startup therefore
            # cannot restore the source before the failing request has occurred.
            log_path = suite.output / f"{name}.log"
            marker = f"aidoku: reader request {chapter_id} page {target}:"
            database = imported["state"]["database"]

            def restore_after_failure() -> None:
                while not observer_stop.wait(0.02):
                    try:
                        if not log_path.is_file() or marker not in log_path.read_text(encoding="utf-8", errors="replace"):
                            continue
                        with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=1) as connection:
                            progress = connection.execute("SELECT page FROM progress WHERE chapter_id=?", (chapter_id,)).fetchone()
                        committed = progress[0] if progress else None
                        if committed != (None if first else 7):
                            continue
                        shutil.copy2(hidden, original)
                        restore_evidence.update({"observed_failure": True, "failed_target": target, "committed_page_before_restore": committed, "restored_after_failure": True})
                        return
                    except (OSError, sqlite3.Error) as error:
                        restore_evidence["last_observation_error"] = type(error).__name__

            observer = threading.Thread(target=restore_after_failure, daemon=True)
            observer.start()
            replay.at = 9000
            replay.intent("panel", "choose", "reader-retry", wait=4500)
        elif name in ("recovery-back", "first-page-back"):
            replay.at = 7500
            replay.intent("panel", "choose", "reader-recovery-back", wait=800)
        elif name == "recovery-close":
            replay.at = 7500
            replay.tap(10, 300, wait=800)
        else:
            replay.at = max(replay.at, 7500)
        route = "detail" if name == "first-page-back" else "reader"
        case = suite.run(name, "reader", demo=False, data=Path(imported["data_dir"]), replay=replay, expected_route=route, capture_delay=1600)
        if name in ("failed-next-visible", "first-page-visible", "pending-bookmark-save", "pending-set-cover"):
            recovery_panel(suite, case, target, None if first else 7)
        if name in ("retry-failed-target", "first-page-retry"):
            suite.check(case, "The source was restored only after an observed failure at the intended target", restore_evidence.get("observed_failure") is True and restore_evidence.get("restored_after_failure") is True, restore_evidence)
            case["restore_observation"] = restore_evidence
            suite.expect(case, "reader_page", target)
            suite.expect(case, "modal", "")
            suite.expect(case, "menu", False)
            frame = case["state"].get("displayed_reader") or {}
            suite.check(case, "Retry accepts the failed target, not the previous displayed page", frame.get("session", {}).get("page") == target, frame)
        if name in ("recovery-back", "recovery-close"):
            suite.expect(case, "reader_page", 7)
            suite.expect(case, "modal", "")
            suite.expect(case, "menu", False)
        if name == "first-page-back":
            suite.expect(case, "modal", "")
        actual_data = Path(case["state"].get("database", "")).parent
        stem = hashlib.sha256(chapter_id.encode("utf-8")).hexdigest() + "-7.png"
        if name == "pending-bookmark-save":
            bookmarks = suite.database_rows(case, "SELECT page FROM bookmarks WHERE chapter_id=? ORDER BY page", (chapter_id,))
            suite.check(case, "Bookmarking a pending request targets displayed page seven", bookmarks == [(7,)], bookmarks)
            exported = actual_data / "exports" / stem
            suite.check(case, "Saving a pending request names the displayed page seven", exported.is_file(), str(exported))
            if exported.is_file():
                with Image.open(exported) as image:
                    width, height = image.size
                    mean = ImageStat.Stat(image.convert("L").crop((width // 3, height // 3, width * 2 // 3, height * 2 // 3))).mean[0]
                suite.check(case, "The saved image contains page-seven pixels rather than pending page-eight pixels", abs(mean - (24 + 7 * 18)) <= 3, mean)
        if name == "pending-set-cover":
            covers = suite.database_rows(case, "SELECT document FROM books WHERE id=?", (case["state"].get("current_book"),))
            cover = Path(json.loads(covers[0][0])["cover"]) if covers else Path()
            suite.check(case, "Setting a cover targets the displayed book and page seven", cover.name == stem and cover.parent.name == "covers" and cover.is_file(), str(cover))
    finally:
        observer_stop.set()
        if observer is not None:
            observer.join(timeout=2)
        if hidden.is_file():
            if not original.is_file():
                shutil.copy2(hidden, original)
            hidden.unlink()


def run_manifest_scenario(suite: Any, Replay: Any, name: str) -> None:
    seed = suite.run(name + "-seed", demo=False)
    database = seed["state"].get("database")
    if not database:
        return
    chapter_one = "10:5:komga:s1:c01"
    chapter_two = "10:5:komga:s1:c02"
    slow = name.startswith("slow-first-")
    retrying = name == "manifest-retrying-visible"
    pending_menu = name == "pending-menu-displayed"
    with KomgaRecovery(slow_first=slow, slow_retry=retrying, slow_eight=pending_menu) as provider:
        source = {"id": "komga", "name": "Komga recovery fixture", "kind": "komga", "base_url": provider.url, "username": "", "password": "", "token": "", "version": 1, "languages": ["en"], "enabled": True, "options": {}}
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM sources")
            connection.execute("INSERT INTO sources(id,document) VALUES(?,?)", ("komga", json.dumps(source)))
        replay = Replay().intent("sources", "open", "komga", wait=800).intent("catalog", "open", "5:komga:s1", wait=800).intent("detail", "open-chapter", chapter_one, wait=1000 if slow else 4500)
        if name in ("slow-first-action-visible", "slow-first-action-dismiss"):
            replay.intent("reader", "page-actions").intent("panel", "choose", "save-page")
            if name == "slow-first-action-dismiss":
                replay.intent("panel", "choose", "reader-action-dismiss")
        if pending_menu:
            replay.intent("reader", "seek", "7", wait=4500).event("reader", "seek", "8").event("reader", "bookmark").event("reader", "menu")
            replay.at += 1000
        elif not slow:
            replay.intent("detail", "open-chapter", chapter_two, wait=1200)
            if name in ("manifest-retry-target", "manifest-retrying-visible"):
                replay.intent("panel", "choose", "reader-retry", wait=1000 if retrying else 4500)
        case = suite.run(name, "sources", demo=False, offline=False, data=Path(seed["data_dir"]), replay=replay, expected_route="reader", capture_delay=1600, allow_busy=slow or retrying or pending_menu)
        if pending_menu:
            suite.expect(case, "menu", True)
            suite.expect(case, "visible_reader_page", 7)
            suite.expect(case, "reader_bookmarked", True)
            frame = case["state"].get("displayed_reader") or {}
            suite.check(case, "An explicitly opened menu describes the displayed seventh image while page eight is in flight", frame.get("session", {}).get("page") == 7, frame)
            rows = suite.database_rows(case, "SELECT page FROM bookmarks WHERE chapter_id=? ORDER BY page", (chapter_one,))
            suite.check(case, "The pending request does not redirect the visible bookmark icon or saved bookmark", rows == [(7,)], rows)
        elif slow:
            suite.expect(case, "busy", True)
            suite.expect(case, "menu", False)
            suite.expect(case, "displayed_reader", None)
            if name == "slow-first-action-visible":
                suite.expect(case, "modal_context", "reader-action-error")
                suite.expect(case, "modal_item_ids", ["reader-action-dismiss"])
            else:
                suite.expect(case, "modal", "")
                with Image.open(case["screenshot"]) as image:
                    darkness = image.convert("L").point(lambda value: 255 if value < 180 else 0)
                    bounds = darkness.getbbox()
                width, height = (618, 824)
                suite.check(case, "A slow first image shows static centered loading feedback instead of a silent white viewport", bounds is not None and bounds[0] > width // 4 and bounds[2] < width * 3 // 4 and bounds[1] > height * 2 // 5 and bounds[3] < height * 3 // 5, bounds)
        elif name in ("manifest-failure-visible", "manifest-retrying-visible"):
            suite.expect(case, "modal_context", "reader-recovery")
            recovery = case["state"].get("reader_recovery") or {}
            suite.check(case, "A failed chapter manifest preserves the requested chapter identity", recovery.get("target", {}).get("chapter_id") == chapter_two, recovery)
            displayed = case["state"].get("displayed_reader") or {}
            suite.check(case, "A manifest failure retains the previously displayed chapter", displayed.get("session", {}).get("chapter_id") == chapter_one, displayed)
            controls = suite.modal_rows(case)
            suite.check(case, "Cross-chapter recovery distinguishes target chapter two from displayed chapter one", controls.get("reader-retry", {}).get("title") == label("reader_retry_target").replace("{target}", chapter_page_label(2, 1)) and controls.get("reader-recovery-back", {}).get("title") == label("reader_continue_target").replace("{target}", chapter_page_label(1, 1)), {identifier: controls.get(identifier, {}).get("title") for identifier in ("reader-retry", "reader-recovery-back")})
            if retrying:
                suite.check(case, "A slow retry keeps its target and panel visible", recovery.get("retrying") is True, recovery)
                suite.check(case, "The retry action is disabled until the new target image is accepted", controls.get("reader-retry", {}).get("enabled") is False, controls)
        else:
            suite.expect(case, "current_chapter", chapter_two)
            suite.expect(case, "modal", "")
            suite.expect(case, "menu", False)
            second_requests = [request for request in provider.requests if request["path"] == "/api/v1/books/c02/pages"]
            suite.check(case, "Retry refetches exactly the failed chapter manifest", [request["status"] for request in second_requests] == [500, 200], provider.requests)
        request_path = suite.output / (name + ".http.json")
        request_path.write_text(json.dumps(provider.requests, indent=2) + "\n")
        case["http_requests"] = str(request_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=CASES, dest="selected_cases")
    parser.add_argument("--timeout", type=int, default=60)
    options = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run this recovery verification only on the designated remote Linux worker.")
    harness = load_harness()
    if harness.sha256(options.binary.resolve()) != options.expected_sha256:
        parser.error("The reader recovery binary does not match the frozen SHA-256.")
    suite = harness.Suite(SimpleNamespace(binary=options.binary, output_dir=options.output_dir, timeout=options.timeout, selected_cases=options.selected_cases))
    for name in options.selected_cases or CASES:
        try:
            run_scenario(suite, harness.Replay, name)
        except Exception as error:
            suite.cases.append({"name": name + "-harness", "passed": False, "checks": [{"description": "The recovery scenario completed", "passed": False, "actual": f"{type(error).__name__}: {error}"}]})
    return suite.finish()


if __name__ == "__main__":
    sys.exit(main())
