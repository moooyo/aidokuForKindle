#!/usr/bin/env python3
"""Bounded real App cover-cache and six-job pressure evidence on the Linux worker."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


def load(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Missing frozen capture dependency: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def requests(provider: Any) -> list[dict[str, Any]]:
    with provider.lock:
        return [dict(item) for item in provider.requests]


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_profile(source: Any, suite: Any, device: str) -> dict[str, Any]:
    prefix = f"{device}--app-pressure-cover"
    visible = 8 if device == "pw5" else 18
    with source.KomgaFixture(page_delay=0.8) as provider:
        provider.series_count = 20
        provider.chapter_count = 6
        data, database = suite.seed_provider(prefix, provider, device)
        book_ids = [source.compound_id("komga", f"series-{index:03}") for index in range(20)]
        book = book_ids[0]
        chapters = [source.compound_id(book, f"series-000-chapter-{index:02}") for index in range(1, 7)]
        replay = source.Replay().intent("sources", "open", "komga", wait=1600)
        for identifier in book_ids:
            replay.intent("catalog", "open", identifier, wait=350).intent("detail", "favorite", wait=80)
            replay.intent("detail", "back", wait=80)
        replay.intent("catalog", "open", book, wait=500)
        # Repeating enqueue is the real existing controller action, not a job-row insertion.
        for _ in range(4):
            for chapter in chapters:
                replay.intent("detail", "download", chapter, wait=20)
        replay.intent("detail", "open-chapter", chapters[0], wait=3500).intent("reader", "menu", wait=400)
        start = len(requests(provider))
        active = suite.capture(prefix + "--burst-reader", "sources", device=device, demo=False, offline=False,
                               data=data, replay=replay, expected_route="reader", modal="",
                               expected={"current_book": book, "menu": True}, capture_delay=1200)
        jobs = suite.database_rows(active, "SELECT chapter_id,state,document FROM downloads WHERE book_id=? ORDER BY chapter_id", (book,))
        suite.check(active, "Twenty real HTTP records are actually favorited before the App pressure navigation",
                    suite.database_rows(active, "SELECT count(*) FROM books WHERE in_library=1") == [(20,)])
        suite.check(active, "Twenty-four real enqueue intents coalesce to exactly six distinct actual chapter jobs",
                    [row[0] for row in jobs] == chapters and len(jobs) == 6,
                    {"enqueue_intents": 24, "actual_jobs": [{"id": row[0], "state": row[1]} for row in jobs]})
        active_jobs = [row for row in jobs if row[1] in ("queued", "downloading")]
        suite.check(active, "The native Reader menu is reached while real queued or downloading work remains active",
                    bool(active_jobs) and active["state"].get("route") == "reader" and active["metadata"].get("refresh_count", 0) > 1,
                    {"active_jobs": len(active_jobs), "refresh_count": active["metadata"].get("refresh_count"),
                     "foreground_ready_posts": active["state"].get("foreground_ready_posts")})
        first_requests = requests(provider)[start:]
        suite.check(active, "The active App pressure includes real image requests against the delayed loopback provider",
                    any(item["method"] == "GET" and "/pages/" in item["path"] for item in first_requests), first_requests)
        suite.save_provider(prefix + "--burst-reader", provider, active)
        suite.persist_case(active)

        known_cover_paths = {item["path"] for item in requests(provider)
                             if item["method"] == "GET" and item["path"].endswith("/thumbnail")}
        start = len(requests(provider))
        repeated = source.Replay()
        for _ in range(3):
            repeated.intent("page", "turn", "1", wait=450).intent("page", "turn", "-1", wait=450)
            repeated.intent("downloads", "open", wait=200).intent("panel", "close", wait=150)
        repeated.intent("settings", "open", "downloads", wait=250).intent("navigation", "open", "0", wait=450)
        cache = suite.capture(prefix + "--cover-page-repeat", "library", device=device, demo=False, offline=False,
                              data=data, replay=repeated, modal="", expected={"library_count": 20, "page": 1},
                              capture_delay=1600)
        current = cache["state"].get("visible_books", [])
        suite.check(cache, "The App displays its actual eight or eighteen first-page library covers after three page round trips",
                    len(current) == visible and cache["state"].get("cover_visible_count") == visible
                    and cache["state"].get("cover_count", 0) >= visible
                    and cache["state"].get("cover_visible_failure_count") == 0,
                    {"visible_book_ids": [item["id"] for item in current],
                     "cover_visible_count": cache["state"].get("cover_visible_count"),
                     "cover_count": cache["state"].get("cover_count")})
        repeat_requests = requests(provider)[start:]
        thumbnail_requests = [item for item in repeat_requests if item["method"] == "GET" and item["path"].endswith("/thumbnail")]
        repeated_cached = [item for item in thumbnail_requests if item["path"] in known_cover_paths]
        newly_visible = [item for item in thumbnail_requests if item["path"] not in known_cover_paths]
        duplicate_urls = len(thumbnail_requests) - len({item["path"] for item in thumbnail_requests})
        suite.check(cache, "Previously fetched real cover URLs are reused during the fresh App's repeated library pagination",
                    not repeated_cached and duplicate_urls == 0,
                    {"previously_fetched_urls": sorted(known_cover_paths), "repeated_cached_url_requests": repeated_cached,
                     "first_requests_for_previously_unfetched_visible_urls": newly_visible,
                     "duplicate_urls_within_repeat": duplicate_urls})
        suite.check(cache, "Queue and settings navigation return to the actual first library page with a responsive native scene",
                    cache["state"].get("route") == "library" and cache["metadata"].get("refresh_count", 0) > 1
                    and cache["state"].get("download_queue_count") == 6,
                    {"route": cache["state"].get("route"), "page": cache["state"].get("page"),
                     "queue_count": cache["state"].get("download_queue_count"), "refresh_count": cache["metadata"].get("refresh_count")})
        suite.save_provider(prefix + "--cover-page-repeat", provider, cache)
        suite.persist_case(cache)

        start = len(requests(provider))
        final = suite.capture(prefix + "--queue-final", "library", device=device, demo=False, offline=False,
                              data=data, replay=source.Replay().intent("downloads", "open", wait=35000),
                              modal="downloads", context="downloads", capture_delay=1000)
        final_jobs = suite.database_rows(final, "SELECT chapter_id,state,document FROM downloads WHERE book_id=? ORDER BY chapter_id", (book,))
        flags = suite.database_rows(final, "SELECT id,json_extract(document,'$.downloaded') FROM chapters WHERE book_id=? ORDER BY id", (book,))
        suite.check(final, "All six actual unique jobs reach completed state without duplicate jobs after the App pressure burst",
                    [row[0] for row in final_jobs] == chapters and all(row[1] == "completed" for row in final_jobs),
                    [{"id": row[0], "state": row[1], "document": json.loads(row[2])} for row in final_jobs])
        suite.check(final, "Actual downloaded chapter flags agree with the completed worker terminal state",
                    [row[0] for row in flags] == chapters and all(row[1] == 1 for row in flags), flags)
        suite.check(final, "The final native queue exposes the same six persisted actual jobs",
                    final["state"].get("download_queue_count") == 6 and bool(final["state"].get("download_queue")),
                    final["state"].get("download_queue"))
        suite.save_provider(prefix + "--queue-final", provider, final)
        suite.persist_case(final)
        return {"device": device, "actual_apps": 4, "library_records": 20, "native_visible_target": visible,
                "enqueue_intents": 24, "unique_actual_jobs": len(final_jobs),
                "active_at_reader_capture": len(active_jobs), "repeat_cover_http_requests": len(thumbnail_requests),
                "previously_fetched_url_repeat_requests": len(repeated_cached),
                "first_cover_fetches_during_repeat": [item["path"] for item in newly_visible],
                "provider_request_count": len(requests(provider)), "case_ids": [prefix + suffix for suffix in
                ("--fixture-seed", "--burst-reader", "--cover-page-repeat", "--queue-final")],
                "scope": "Actual App actions, real HTTP, native RAM/refresh observations and Store terminal consistency. No event ratio, latency, allocation, O(1), cross-GUI or physical claim."}


def main() -> int:
    original_argv = list(sys.argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pressure-device", choices=("pw5", "scribe"), action="append")
    extra, inherited = parser.parse_known_args(original_argv[1:])
    devices = extra.pressure_device or ["pw5", "scribe"]
    if len(devices) != len(set(devices)):
        raise ValueError("Each fixed pressure profile may be selected only once")
    guard = load("app_pressure_existing_guard", "verify-handoff-tags.py")
    source = guard.load_source()
    try:
        sys.argv = [original_argv[0], *inherited]
        args = guard._arguments(source)
    finally:
        sys.argv = original_argv
    guard._fresh_output(args.output)
    paths = {path.name: path for path in Path(__file__).resolve().parent.glob("*.py")}
    paths.update({"capture_plan": args.plan, "reference_scope": args.scope})
    before = guard._fingerprints(source, paths)
    suite = source.Suite(args)
    suite.reference_ids = [item["id"] for board in suite.scope["pageStateMatrix"] for item in [board["default"], *board["states"]]]
    results = []
    error = None
    try:
        for device in devices:
            results.append(run_profile(source, suite, device))
    except Exception as exception:
        error = f"{type(exception).__name__}: {exception}"
    raw_exit = suite.finish_manifest()
    after = guard._fingerprints(source, paths)
    failures = [case["name"] for case in suite.cases if case.get("passed") is False]
    unchanged = before == after
    captured = [case for case in suite.cases if case.get("screenshot")]
    passed = error is None and not failures and unchanged and len(captured) == 4 * len(devices)
    report = {"kind": "actual-app-cover-and-six-job-pressure-evidence", "source_sha256": suite.source["sha256"],
              "binary_sha256": suite.binary_hash, "run_id": suite.run_id, "round": args.round,
              "actual_apps": len(captured), "passed": passed, "failed_case_ids": failures, "error": error,
              "actual_profile_scope": devices, "argv": original_argv,
              "profiles": results, "inputs_before": before, "inputs_after": after, "inputs_unchanged": unchanged,
              "raw_manifest_exit": raw_exit, "full_capture_suite_completed": False, "clean_round_credit": 0,
              "scope": "Bounded actual GUI functional pressure evidence only; measurements remain the Root-owned external performance artifacts."}
    source.save_json(suite.output / "app-pressure-cover-evidence.json", report)
    print(json.dumps({"actual_apps": len(captured), "passed": passed, "failures": failures, "error": error}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
