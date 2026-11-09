"""Focused native C4 coverage using real provider, download, and storage data."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


MAX_PAGE_BYTES = 32 * 1024 * 1024


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _retain(suite: Any, api: Any, case: dict[str, Any], label: str, payload: Any) -> None:
    path = Path(case["screenshot"]).parent / "fixtures" / f"{case['name']}--{label}.json"
    api.save_json(path, payload)
    record = {"path": str(path), "sha256": _digest(path)}
    case.setdefault("business_evidence", {})[label] = record
    case.setdefault("evidence_hashes", {})[f"business:{label}"] = record


def _requests(provider: Any) -> list[dict[str, Any]]:
    with provider.lock:
        return [dict(item) for item in provider.requests]


def _request_evidence(suite: Any, api: Any, case: dict[str, Any], provider: Any,
                      start: int = 0) -> list[dict[str, Any]]:
    records = _requests(provider)[start:]
    _retain(suite, api, case, "provider-requests", records)
    suite.check(case, "All recorded fixture traffic uses the configured loopback credential",
                bool(records) and all(item.get("authorized") is True for item in records),
                {"count": len(records), "unauthorized": sum(item.get("authorized") is not True for item in records)})
    return records


def _cache_records(suite: Any, case: dict[str, Any]) -> dict[str, Any]:
    root = Path(case["state"]["database"]).parent / "cache" / "shared-v1"
    result: dict[str, Any] = {"root": str(root), "pins": [], "artifacts": [], "errors": []}
    try:
        root = root.resolve(strict=True)
        index = root / "index.sqlite3"
        with sqlite3.connect(index.as_uri() + "?mode=ro", uri=True) as connection:
            result["pins"] = [list(row) for row in connection.execute(
                "SELECT owner,raw_key FROM pins ORDER BY owner,raw_key")]
            rows = connection.execute(
                "SELECT a.id,a.kind,a.cache_key,"
                "a.kind='raw' AND EXISTS(SELECT 1 FROM pins WHERE raw_key=a.cache_key),"
                "CASE WHEN EXISTS(SELECT 1 FROM aliases WHERE artifact_id=a.id AND cover=1) THEN 1 "
                "WHEN EXISTS(SELECT 1 FROM aliases WHERE artifact_id=a.id AND cover IS NULL) "
                "THEN NULL ELSE 0 END FROM artifacts AS a WHERE a.valid=1 ORDER BY a.id"
            ).fetchall()
        for identifier, kind, key, pinned, cover in rows:
            if kind not in ("raw", "processed"):
                raise ValueError(f"Unexpected managed artifact kind: {kind}")
            suffix = "raw" if kind == "raw" else "png"
            path = root / kind / key[:2] / f"{key}.{suffix}"
            item = {"id": identifier, "kind": kind, "key": key, "pinned": bool(pinned),
                    "cover": cover, "path": str(path), "exists": path.is_file()}
            if path.is_file():
                canonical = path.resolve(strict=True)
                if path.is_symlink() or canonical != path:
                    raise ValueError(f"A managed artifact is a symbolic link: {path}")
                item.update({"canonical_path": str(canonical), "bytes": path.stat().st_size,
                             "sha256": _digest(path)})
            result["artifacts"].append(item)
        capacity = shutil.disk_usage(root)
        result.update({"root": str(root), "available_bytes_observed": capacity.free,
                       "filesystem_capacity_bytes": capacity.total})
    except (OSError, sqlite3.Error, ValueError) as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    suite.check(case, "The actual cache index and managed files can be inspected read-only",
                not result["errors"], result["errors"])
    return result


def _file_records(suite: Any, case: dict[str, Any], paths: list[str]) -> list[dict[str, Any]]:
    records = []
    for stored in sorted(set(paths)):
        path = Path(stored)
        try:
            canonical = path.resolve(strict=True)
            record = {"path": str(path), "canonical_path": str(canonical),
                      "bytes": canonical.stat().st_size, "sha256": _digest(canonical)}
        except OSError as error:
            record = {"path": str(path), "error": f"{type(error).__name__}: {error}"}
            suite.check(case, "A protected fixture file remains readable", False, record)
        records.append(record)
    return records


def _protection(suite: Any, case: dict[str, Any], archive: Path) -> dict[str, Any]:
    jobs = suite.database_rows(case,
        "SELECT chapter_id,book_id,state,document FROM downloads ORDER BY chapter_id")
    flags = suite.database_rows(case,
        "SELECT id,book_id,source_id,COALESCE(json_extract(document,'$.downloaded'),0) "
        "FROM chapters ORDER BY id")
    local_paths = suite.database_rows(case,
        "SELECT DISTINCT json_extract(p.document,'$.local_path') FROM pages p "
        "JOIN chapters c ON c.id=p.chapter_id WHERE c.source_id='local' "
        "AND json_extract(p.document,'$.local_path') IS NOT NULL")
    cache = _cache_records(suite, case)
    return {
        "jobs": [{"chapter_id": chapter, "book_id": book, "state": state,
                  "document": json.loads(document)} for chapter, book, state, document in jobs],
        "downloaded_flags": [{"chapter_id": chapter, "book_id": book,
                              "source_id": source, "downloaded": bool(flag)}
                             for chapter, book, source, flag in flags],
        "pins": cache["pins"],
        "local_files": _file_records(suite, case, [str(archive)] + [row[0] for row in local_paths]),
    }


def _usage_observation(suite: Any, case: dict[str, Any]) -> dict[str, Any]:
    references = suite.database_rows(case,
        "SELECT b.id,b.title,c.id,p.position,json_extract(p.document,'$.local_path') "
        "FROM books b JOIN chapters c ON c.book_id=b.id JOIN pages p ON p.chapter_id=c.id "
        "WHERE json_extract(c.document,'$.downloaded')=1 "
        "AND json_extract(p.document,'$.local_path') IS NOT NULL ORDER BY b.id,c.id,p.position")
    files: dict[str, dict[str, Any]] = {}
    by_book: dict[str, set[str]] = defaultdict(set)
    titles: dict[str, str] = {}
    missing = []
    for book, title, chapter, position, stored in references:
        try:
            path = Path(stored).resolve(strict=True)
            size = path.stat().st_size
            if not path.is_file() or not 0 < size <= MAX_PAGE_BYTES:
                continue
            canonical = str(path)
            entry = files.setdefault(canonical, {"path": canonical, "bytes": size,
                                                  "sha256": _digest(path), "references": []})
            entry["references"].append({"book_id": book, "chapter_id": chapter, "position": position})
            by_book[book].add(canonical)
            titles[book] = title
        except OSError as error:
            missing.append({"stored_path": stored, "error": f"{type(error).__name__}: {error}"})
    books = [{"id": identifier, "title": titles[identifier],
              "bytes": sum(files[path]["bytes"] for path in paths)}
             for identifier, paths in by_book.items()]
    books.sort(key=lambda item: (-item["bytes"], item["title"], item["id"]))
    cache = _cache_records(suite, case)
    uses = {path: {"bytes": item["bytes"], "usage": "download"} for path, item in files.items()}
    for item in cache["artifacts"]:
        if item["pinned"] or not item["exists"]:
            continue
        canonical = item["canonical_path"]
        usage = "cover" if item["cover"] == 1 else "unknown" if item["cover"] is None else "image"
        previous = uses.get(canonical)
        if previous is None:
            uses[canonical] = {"bytes": item["bytes"], "usage": usage}
        elif previous["usage"] != "download":
            previous["usage"] = ("cover" if "cover" in (previous["usage"], usage)
                                 else "unknown" if "unknown" in (previous["usage"], usage) else "image")
    unknown = any(item["usage"] == "unknown" and item["bytes"] > 0 for item in uses.values())
    return {
        "downloaded_bytes": sum(item["bytes"] for item in files.values()),
        "image_cache_bytes": None if unknown else sum(item["bytes"] for item in uses.values() if item["usage"] == "image"),
        "cover_bytes": None if unknown else sum(item["bytes"] for item in uses.values() if item["usage"] == "cover"),
        "books": books, "canonical_download_files": list(files.values()),
        "download_reference_count": len(references), "missing": missing, "cache": cache,
    }


def _bytes_text(value: int) -> str:
    if value < 1000:
        return f"{value} B"
    if value < 1_000_000:
        return f"{value / 1000:.1f} KB"
    if value < 1_000_000_000:
        return f"{value / 1_000_000:.1f} MB"
    return f"{value / 1_000_000_000:.1f} GB"


def _check_storage(suite: Any, api: Any, case: dict[str, Any]) -> dict[str, Any]:
    observed = _usage_observation(suite, case)
    _retain(suite, api, case, "actual-storage-files", observed)
    state = case["state"]
    raw = state.get("storage_details")
    suite.expect(case, "storage_ready", True)
    suite.expect(case, "storage_loading", False)
    suite.expect(case, "storage_status_code", "ready")
    suite.expect(case, "storage_scale_known", True)
    suite.check(case, "The worker publishes a real accepted storage snapshot", isinstance(raw, dict), raw)
    if not isinstance(raw, dict):
        suite.persist_case(case)
        return observed
    for key in ("downloaded_bytes", "image_cache_bytes", "cover_bytes", "books"):
        suite.check(case, f"Worker {key} agrees with canonical real files", raw.get(key) == observed[key],
                    {"worker": raw.get(key), "actual_files": observed[key]})
    suite.check(case, "The fixture uses actual small image bytes instead of fictional capacity labels",
                0 < observed["downloaded_bytes"] < 1_000_000
                and all(0 < item["bytes"] < 1_000_000 for item in observed["books"]),
                {"downloaded_bytes": observed["downloaded_bytes"], "books": observed["books"]})
    free = raw.get("free_bytes")
    capacity = observed["cache"].get("filesystem_capacity_bytes")
    suite.check(case, "Available space is a real nonnegative capacity observation on the cache filesystem",
                type(free) is int and type(capacity) is int and 0 <= free <= capacity,
                {"worker_free": free, "later_free": observed["cache"].get("available_bytes_observed"),
                 "filesystem_capacity": capacity})
    categories = state.get("storage_categories", {})
    mapping = {"downloaded": "downloaded_bytes", "image_cache": "image_cache_bytes",
               "covers": "cover_bytes", "free": "free_bytes"}
    values = [raw.get(key) for key in mapping.values()]
    scale = sum(values) if all(type(value) is int for value in values) else 0
    for name, key in mapping.items():
        item = categories.get(name, {})
        value = raw.get(key)
        fraction = item.get("fraction")
        valid = (type(value) is int and value >= 0 and item.get("known") is True
                 and item.get("text") == _bytes_text(value)
                 and type(fraction) in (int, float) and scale > 0
                 and abs(fraction - value / scale) <= 0.000001)
        suite.check(case, f"The visible {name} legend and bar use the accepted real byte count", valid, item)
    suite.check(case, "Real reader rendering and actual cover publication populate both cache classes",
                type(raw.get("image_cache_bytes")) is int and raw["image_cache_bytes"] > 0
                and type(raw.get("cover_bytes")) is int and raw["cover_bytes"] > 0,
                {key: raw.get(key) for key in ("image_cache_bytes", "cover_bytes")})
    expected_rows = [{"id": f"storage-book:{item['id']}", "title": item["title"],
                      "value": _bytes_text(item["bytes"]), "group": "storage-book", "enabled": True}
                     for item in observed["books"][:5]]
    suite.expect(case, "storage_books", expected_rows)
    suite.expect(case, "storage_book_count", len(observed["books"]))
    case["visible_ui_requirements"] = [
        "All four real storage classes have readable legends and an honest proportional bar.",
        "At most five real largest-book rows expose their measured sizes and management actions.",
    ]
    suite.persist_case(case)
    return observed


def run_book_storage(suite: Any, api: Any) -> None:
    """Capture one shared-data business flow without manufacturing durable state."""
    device = "scribe"
    name = f"{device}--c4-book-storage"
    source = "suwayomi"
    Replay = api.Replay
    with api.Fixture(shared_graphql=True, page_delay=0.8) as provider:
        provider.series_count = 6
        provider.chapter_count = 3
        data, _ = suite.seed_provider(name, provider, device, source_id=source,
                                     source_kind="suwayomi", source_options={"source_id": "1"})
        books = [api.compound_id(source, str(number)) for number in range(1, 7)]
        chapters = [api.compound_id(book, str(number * 100 + 1))
                    for number, book in enumerate(books, 1)]
        target = books[0]
        active_chapter = api.compound_id(target, "102")

        prepare = Replay().intent("sources", "open", source, wait=1200)
        for number, (book, chapter) in enumerate(zip(books, chapters)):
            prepare.intent("catalog", "open", book, wait=1000).intent("detail", "favorite", wait=80)
            prepare.intent("detail", "download", chapter, wait=1200)
            if number < len(books) - 1:
                prepare.intent("detail", "back", wait=100)
        prepared = suite.capture(f"{name}--six-real-downloads", "sources", device=device,
                                 demo=False, offline=False, data=data, replay=prepare,
                                 expected_route="detail", modal="", prerequisite=True, capture_delay=1600)
        jobs = {item.get("chapter_id"): item for item in prepared["state"].get("downloads", [])}
        suite.check(prepared, "Six ordinary detail download actions complete actual provider chapter data",
                    set(jobs) == set(chapters) and all(jobs[chapter].get("state") == "completed"
                    and jobs[chapter].get("completed") == provider.page_count
                    and jobs[chapter].get("total") == provider.page_count for chapter in chapters), jobs)
        flags = suite.database_rows(prepared,
            "SELECT id,in_library FROM books WHERE source_id=? ORDER BY id", (source,))
        suite.check(prepared, "All six actual browsed provider books were explicitly added to the library",
                    {identifier for identifier, member in flags if member} == set(books), flags)
        completed_flags = suite.database_rows(prepared,
            "SELECT id FROM chapters WHERE source_id=? AND json_extract(document,'$.downloaded')=1 ORDER BY id",
            (source,))
        suite.check(prepared, "Only the six genuinely completed chapters receive durable downloaded flags",
                    {row[0] for row in completed_flags} == set(chapters), completed_flags)
        _request_evidence(suite, api, prepared, provider)
        suite.persist_case(prepared)

        reader = suite.capture(f"{name}--real-reader-cache", "library", device=device,
                              demo=False, offline=False, data=data, prerequisite=True, pure=True,
                              replay=Replay().intent("library", "open", target, wait=1000)
                              .intent("settings", "set", "page-layout-mode=single", wait=100)
                              .intent("detail", "open-chapter", chapters[0], wait=1800),
                              expected_route="reader", modal="", capture_delay=1800)
        displayed = reader["state"].get("displayed_reader") or {}
        first_path = displayed.get("first_path")
        suite.check(reader, "The real reader publishes an actual processed cache image",
                    bool(first_path) and Path(first_path).is_file()
                    and "processed" in Path(first_path).parts, displayed)
        suite.persist_case(reader)

        archive = suite.create_distinct_cbz(f"{name}--protected-local", pages=1)
        imported = suite.capture(f"{name}--real-local-import", "library", device=device,
                                demo=False, offline=True, data=data, import_path=archive,
                                prerequisite=True, capture_delay=1800, expected_route="library")
        local = suite.database_rows(imported,
            "SELECT id FROM books WHERE source_id='local' ORDER BY id")
        suite.check(imported, "A genuine local CBZ import creates a protected local book", len(local) == 1, local)
        initial = _protection(suite, imported, archive)
        suite.check(imported, "The local import retains an actual readable page and its original archive",
                    len(initial["local_files"]) >= 2 and all("sha256" in item for item in initial["local_files"]),
                    initial["local_files"])
        _retain(suite, api, imported, "initial-protection", initial)
        suite.persist_case(imported)
        if local:
            protected = suite.capture(f"{name}--delete-all-local-protected", "library", device=device,
                                      demo=False, offline=True, data=data,
                                      replay=Replay().intent("library", "open", local[0][0], wait=800)
                                      .intent("detail", "more"), expected_route="detail", modal="choices", context="detail-more")
            row = next((item for item in protected["state"].get("modal_items", [])
                        if item.get("id") == "remove-all-downloads"), {})
            suite.check(protected, "The native More menu honestly disables deletion of original local imports",
                        row.get("enabled") is False and row.get("value") == api.ui_label("local_import_protected"), row)
            protected["visible_ui_requirements"] = ["The local-import protection reason is visibly attached to the disabled delete-all entry."]
            suite.persist_case(protected)

        early = suite.capture(f"{name}--storage-initial-loading", "settings", device=device,
                              demo=False, offline=True, data=data,
                              replay=Replay(start=0).intent("settings", "open", "storage", wait=0),
                              capture_at_ms=1, allow_busy=True, modal="",
                              expected={"settings_section": "storage", "storage_ready": False,
                                        "storage_loading": True, "storage_status_code": "loading",
                                        "storage_details": None},
                              note="The first real event-loop poll starts the storage worker; no model field is injected.")
        categories = early["state"].get("storage_categories", {})
        suite.check(early, "Unknown first-frame values remain explicit placeholders while the real worker runs",
                    set(categories) == {"downloaded", "image_cache", "covers", "free"}
                    and all(item.get("known") is False and item.get("text") == "\u2014"
                            for item in categories.values()), categories)
        suite.persist_case(early)

        ready = suite.capture(f"{name}--storage-ready-four-classes", "settings", device=device,
                              demo=False, offline=True, data=data,
                              replay=Replay().intent("settings", "open", "storage", wait=1400),
                              modal="", expected={"settings_section": "storage"}, capture_delay=1200)
        before_usage = _check_storage(suite, api, ready)
        suite.check(ready, "The global download total deduplicates genuine shared paths across six books",
                    len(before_usage["books"]) == 7
                    and sum(item["bytes"] for item in before_usage["books"]) > before_usage["downloaded_bytes"]
                    and before_usage["download_reference_count"] > len(before_usage["canonical_download_files"]),
                    {"books": len(before_usage["books"]), "global_bytes": before_usage["downloaded_bytes"],
                     "associated_book_bytes": sum(item["bytes"] for item in before_usage["books"]),
                     "references": before_usage["download_reference_count"],
                     "unique_paths": len(before_usage["canonical_download_files"])})
        suite.check(ready, "The current real provider book is a visible largest-storage management target",
                    f"storage-book:{target}" in [row.get("id") for row in ready["state"].get("storage_books", [])],
                    ready["state"].get("storage_books"))
        provider_sizes = [item["bytes"] for item in before_usage["books"] if item["id"] in books]
        suite.check(ready, "All six tiny provider-book measurements use honest byte-sized values",
                    len(provider_sizes) == 6 and all(0 < value < 1000 for value in provider_sizes), provider_sizes)
        suite.persist_case(ready)

        managed = suite.capture(f"{name}--storage-book-management", "settings", device=device,
                                demo=False, offline=True, data=data,
                                replay=Replay().intent("settings", "open", "storage", wait=1200)
                                .intent("settings", "detail", f"storage-book:{target}"),
                                expected_route="detail", modal="", expected={"current_book": target})
        suite.expect(managed, "downloaded_count", 1)
        suite.persist_case(managed)

        def open_target() -> Any:
            return Replay().intent("library", "open", target, wait=1000)

        more = suite.capture(f"{name}--delete-all-more", "library", device=device,
                             demo=False, offline=False, data=data,
                             replay=open_target().intent("detail", "more"),
                             expected_route="detail", modal="choices", context="detail-more")
        delete_row = next((row for row in more["state"].get("modal_items", [])
                           if row.get("id") == "remove-all-downloads"), {})
        suite.check(more, "The Scribe More menu exposes the actual current-book delete-all action",
                    delete_row.get("enabled") is True and delete_row.get("title") == api.ui_label("delete_all_downloads"),
                    delete_row)
        baseline = _protection(suite, more, archive)
        target_owner = f"download:{chapters[0]}"
        shared_keys = {key for owner, key in baseline["pins"] if owner == target_owner}
        other_owners = {f"download:{chapter}" for chapter in chapters[1:]}
        suite.check(more, "Completed books hold real durable owners of the same raw page keys",
                    len(shared_keys) == provider.page_count
                    and all({key for owner, key in baseline["pins"] if owner == other} == shared_keys
                            for other in other_owners), baseline["pins"])
        raw_by_key = {item["key"]: item for item in _cache_records(suite, more)["artifacts"] if item["kind"] == "raw"}
        shared_before = _file_records(suite, more, [raw_by_key[key]["path"] for key in sorted(shared_keys) if key in raw_by_key])
        _retain(suite, api, more, "before-delete-protection", baseline)
        _retain(suite, api, more, "shared-raw-files", shared_before)
        suite.persist_case(more)

        confirm_replay = open_target().intent("detail", "more").intent("panel", "choose", "remove-all-downloads")
        confirm = suite.capture(f"{name}--delete-all-confirm", "library", device=device,
                                demo=False, offline=False, data=data, replay=confirm_replay,
                                expected_route="detail", modal="choices", context=f"book-downloads:{target}")
        rows = confirm["state"].get("modal_items", [])
        suite.check(confirm, "Confirmation identifies one real managed chapter and keeps Cancel reachable",
                    [row.get("id") for row in rows] == ["book-downloads-remove-confirm", "close"]
                    and rows[0].get("enabled") is True
                    and rows[0].get("title") == api.ui_label("book_downloads_remove_confirm").replace("{count}", "1"), rows)
        suite.check(confirm, "Opening confirmation leaves every actual job, flag, pin and local file unchanged",
                    _protection(suite, confirm, archive) == baseline)
        suite.expect(confirm, "book_removal_completions", 0)
        suite.expect(confirm, "last_book_removal", None)
        suite.persist_case(confirm)

        cancelled = suite.capture(f"{name}--delete-all-cancel", "library", device=device,
                                  demo=False, offline=False, data=data,
                                  replay=open_target().intent("detail", "remove-all-downloads")
                                  .intent("panel", "close"), expected_route="detail", modal="")
        cancel_protection = _protection(suite, cancelled, archive)
        suite.check(cancelled, "Cancel preserves all real jobs, downloaded flags, owners and imported bytes",
                    cancel_protection == baseline)
        suite.expect(cancelled, "book_removal_completions", 0)
        suite.expect(cancelled, "last_book_removal", None)
        _retain(suite, api, cancelled, "cancel-protection", cancel_protection)
        suite.persist_case(cancelled)

        active_start = len(_requests(provider))
        active = suite.capture(f"{name}--delete-all-active-confirm", "library", device=device,
                               demo=False, offline=False, data=data,
                               replay=open_target().intent("detail", "download", active_chapter, wait=200)
                               .intent("detail", "remove-all-downloads", wait=0),
                               expected_route="detail", modal="choices", context=f"book-downloads:{target}",
                               capture_delay=0, allow_busy=True)
        active_job = next((item for item in active["state"].get("downloads", [])
                           if item.get("chapter_id") == active_chapter), {})
        active_requests = _request_evidence(suite, api, active, provider, active_start)
        suite.check(active, "The confirmation includes a genuinely downloading current-book chapter",
                    active_job.get("state") == "downloading" and active_job.get("completed", 6) < provider.page_count,
                    active_job)
        active_confirm = next((row for row in active["state"].get("modal_items", [])
                               if row.get("id") == "book-downloads-remove-confirm"), {})
        suite.check(active, "The current-book confirmation counts both completed and active managed chapters",
                    active_confirm.get("enabled") is True
                    and active_confirm.get("title") == api.ui_label("book_downloads_remove_confirm").replace("{count}", "2"),
                    active_confirm)
        suite.check(active, "The active chapter has issued actual delayed exclusive image requests",
                    any(urlsplit(item["path"]).path.startswith("/exclusive/102/") for item in active_requests),
                    active_requests)
        suite.expect(active, "book_removal_completions", 0)
        suite.expect(active, "last_book_removal", None)
        suite.persist_case(active)

        actual_job = suite.database_rows(active, "SELECT state FROM downloads WHERE chapter_id=?", (active_chapter,))
        suite.check(active, "The active preview leaves a real unfinished job for the guarded apply process",
                    bool(actual_job) and actual_job[0][0] != "completed", actual_job)
        suite.persist_case(active)
        # Fence any startup resume with an ordinary pause before opening details.
        # Resume immediately before confirmation so partial cached pages cannot
        # let this job finish during the unrelated detail-loading interval.
        apply_replay = (Replay(start=0).intent("downloads", "pause", active_chapter, wait=200)
                        .intent("library", "open", target, wait=1000)
                        .intent("detail", "download", active_chapter, wait=80)
                        .intent("downloads", "resume", active_chapter, wait=200))
        apply_replay.intent("detail", "remove-all-downloads", wait=100)
        apply_replay.intent("panel", "choose", "book-downloads-remove-confirm", wait=5000)
        apply_start = len(_requests(provider))
        applied = suite.capture(f"{name}--delete-all-applied", "library", device=device,
                                demo=False, offline=False, data=data, replay=apply_replay,
                                expected_route="detail", modal="", capture_delay=1800)
        report = applied["state"].get("last_book_removal") or {}
        sequence = report.get("completion_sequence")
        suite.check(applied, "Only a finalized actor event completes current-book cleanup after active drain",
                    report.get("book_id") == target and report.get("finalized") is True
                    and type(sequence) is int and sequence > 0
                    and applied["state"].get("book_removal_completions") == sequence
                    and report.get("removed") == 1 and report.get("cancelled") == 1
                    and report.get("failed") == 0 and report.get("retained_local") == 0
                    and report.get("usage_changed") is True and report.get("had_error") is False, report)
        apply_requests = _request_evidence(suite, api, applied, provider, apply_start)
        suite.check(applied, "The apply process really starts the delayed chapter before bulk cancellation",
                    any(urlsplit(item["path"]).path.startswith("/exclusive/102/") for item in apply_requests),
                    apply_requests)
        after = _protection(suite, applied, archive)
        suite.check(applied, "Cleanup removes every current-book managed job and downloaded flag",
                    not any(item["book_id"] == target for item in after["jobs"])
                    and all(not item["downloaded"] for item in after["downloaded_flags"] if item["book_id"] == target),
                    {"jobs": after["jobs"], "flags": after["downloaded_flags"]})
        suite.check(applied, "Other books retain their exact completed records and downloaded flags",
                    [item for item in after["jobs"] if item["book_id"] != target]
                    == [item for item in baseline["jobs"] if item["book_id"] != target]
                    and [item for item in after["downloaded_flags"] if item["book_id"] != target]
                    == [item for item in baseline["downloaded_flags"] if item["book_id"] != target])
        removed_owners = {target_owner, f"download:{active_chapter}"}
        suite.check(applied, "Cleanup releases only this book's durable owners and preserves all other pins",
                    after["pins"] == [row for row in baseline["pins"] if row[0] not in removed_owners], after["pins"])
        shared_after = _file_records(suite, applied, [item["path"] for item in shared_before])
        suite.check(applied, "Shared raw images remain byte-identical under the other books' real owners",
                    len(shared_before) == provider.page_count and shared_after == shared_before, shared_after)
        suite.check(applied, "Local imported content and the original CBZ remain byte-identical",
                    after["local_files"] == baseline["local_files"], after["local_files"])
        applied["external_evidence_required"] = [{
            "scope": "independent-live-reader-lease", "status": "external_evidence_required",
            "reason": "The supported contract is one App process: a genuine held CacheLease is shared through the in-process registry with independent CacheManager and download-worker instances. These sequential GUI captures do not invent such a lease or require another GUI process.",
            "required_evidence": "Root-owned exact C4 tests must retain a real held CacheLease object while the supported same-process manager/worker performs cleanup, with actual PASS, log, artifact hash and source hash evidence. Cross-process GUI lease sharing is unsupported and is not claimed.",
        }]
        _retain(suite, api, applied, "after-delete-protection", after)
        _retain(suite, api, applied, "actor-completion", report)
        _retain(suite, api, applied, "shared-raw-files-after", shared_after)
        suite.persist_case(applied)

        noop = suite.capture(f"{name}--delete-all-empty-disabled", "library", device=device,
                             demo=False, offline=True, data=data,
                             replay=open_target().intent("detail", "remove-all-downloads"),
                             expected_route="detail", modal="choices", context=f"book-downloads:{target}")
        disabled = next((row for row in noop["state"].get("modal_items", [])
                         if row.get("id") == "book-downloads-remove-confirm"), {})
        suite.check(noop, "An empty book exposes an accurate disabled zero-count confirmation",
                    disabled.get("enabled") is False
                    and disabled.get("title") == api.ui_label("book_downloads_remove_confirm").replace("{count}", "0"),
                    disabled)
        suite.expect(noop, "book_removal_completions", 0)
        suite.expect(noop, "last_book_removal", None)
        suite.check(noop, "The disabled no-op state leaves the completed cleanup and protected content unchanged",
                    _protection(suite, noop, archive) == after)
        suite.persist_case(noop)

        refreshed = suite.capture(f"{name}--storage-after-cleanup", "settings", device=device,
                                  demo=False, offline=True, data=data,
                                  replay=Replay().intent("settings", "open", "storage", wait=1400),
                                  modal="", expected={"settings_section": "storage"}, capture_delay=1200)
        after_usage = _check_storage(suite, api, refreshed)
        suite.check(refreshed, "A fresh real worker removes only the cleaned book from storage ranking",
                    after_usage["books"] == [item for item in before_usage["books"] if item["id"] != target],
                    after_usage["books"])
        suite.check(refreshed, "Global downloaded bytes remain deduplicated while other books still own every shared file",
                    after_usage["downloaded_bytes"] == before_usage["downloaded_bytes"],
                    {"before": before_usage["downloaded_bytes"], "after": after_usage["downloaded_bytes"]})
        _retain(suite, api, refreshed, "complete-provider-history", _requests(provider))
        suite.persist_case(refreshed)
