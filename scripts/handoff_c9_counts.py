"""Bounded C9 native unread projections checked against the real Demo Store.

The helper performs six captures per profile and never writes fixture books,
chapters or progress through SQL. Mark-read uses the existing selection action.
Original PNGs remain untouched and still require independent visual inspection.
"""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any


PROFILES = ("pw5", "scribe")
STAGES = ("baseline", "include", "exclude", "ascending", "descending", "mark-read")
CASE_IDS = tuple(f"{device}--c9-unread-{stage}" for device in PROFILES for stage in STAGES)
PAGE_SIZES = {"pw5": 8, "scribe": 18}
BOOK_SQL = """
SELECT b.rowid AS store_rowid, b.id, b.title, b.document,
       COUNT(c.id) AS actual_chapters,
       SUM(CASE WHEN json_extract(c.document, '$.read') = 0 THEN 1 ELSE 0 END) AS actual_unread,
       SUM(CASE WHEN json_extract(c.document, '$.read') = 1 THEN 1 ELSE 0 END) AS actual_read
FROM books b LEFT JOIN chapters c ON c.book_id = b.id
WHERE b.in_library = 1
GROUP BY b.rowid, b.id
ORDER BY b.rowid, b.id
""".strip()
PROGRESS_SQL = "SELECT book_id,chapter_id,page,total,completed,updated_at FROM progress ORDER BY book_id,chapter_id"
INTERACTION_SQL = "SELECT document FROM preferences WHERE key='ui:interaction-v1'"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _retain(suite: Any, api: Any, case: dict[str, Any], label: str, payload: Any) -> None:
    path = suite.output / "fixtures" / f"{case['name']}--{label}.json"
    api.save_json(path, payload)
    case.setdefault("evidence_hashes", {})[f"c9:{label}"] = {"path": str(path), "sha256": _sha256(path)}
    suite.persist_case(case)


def _snapshot(case: dict[str, Any]) -> dict[str, Any]:
    """Read one consistent real Store snapshot through a read-only connection."""
    data_literal = Path(case["data_dir"])
    database_literal = Path(case["state"]["database"])
    if (case["state"].get("demo") is not True or not data_literal.is_absolute()
            or database_literal != data_literal / "demo" / "library.sqlite3"):
        raise ValueError("The count observation must use this capture's actual native database")
    data = data_literal.resolve(strict=True)
    database = database_literal.resolve(strict=True)
    if database != data / "demo" / "library.sqlite3":
        raise ValueError("The exact Demo database path must remain inside its literal native case boundary")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1.0)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        books = []
        for row in connection.execute(BOOK_SQL):
            document = json.loads(row["document"])
            unread = document.get("unread")
            if type(unread) is not int or unread < 0:
                raise ValueError("A persisted library book has no valid actual unread count")
            books.append({"id": row["id"], "title": row["title"], "store_rowid": row["store_rowid"],
                          "unread": unread, "actual_chapters": row["actual_chapters"],
                          "actual_unread": row["actual_unread"], "actual_read": row["actual_read"],
                          "chapter_count_comparison_applicable": row["actual_chapters"] > 0,
                          "book_document": document})
        progress = [dict(row) for row in connection.execute(PROGRESS_SQL)]
        stored = connection.execute(INTERACTION_SQL).fetchone()
        interaction = json.loads(stored[0]) if stored else {}
    return {"database": str(database), "connection_mode": "read-only URI, consistent read transaction",
            "queries": {"books_and_actual_chapters": BOOK_SQL, "progress": PROGRESS_SQL,
                        "interaction": INTERACTION_SQL},
            "library_books": books, "progress_rows": progress, "interaction": interaction,
            "scope": "Actual persisted Store rows; no SQL writes or synthetic badge/read-progress inputs"}


def _ordered(books: list[dict[str, Any]], unread_filter: int, ascending: bool) -> list[dict[str, Any]]:
    eligible = [book for book in books if unread_filter == 0
                or (book["unread"] > 0 if unread_filter == 1 else book["unread"] == 0)]
    # The Store's unread metric uses its real rowid and ID as stable tie breakers.
    return sorted(eligible, key=lambda book: (book["unread"], book["store_rowid"], book["id"]),
                  reverse=not ascending)


def _assess(suite: Any, api: Any, case: dict[str, Any], snapshot: dict[str, Any], *,
            unread_filter: int, ascending: bool | None) -> None:
    state = case["state"]
    visible = state.get("visible_books", [])
    books = snapshot["library_books"]
    by_id = {book["id"]: book for book in books}
    identities = [row.get("id") for row in visible]
    filters = [0, unread_filter, 0, 0]
    suite.check(case, "The real library projection contains distinct visible native BookItems",
                bool(visible) and len(identities) == len(set(identities)), identities)
    suite.check(case, "The count qualification uses the actual all-library category and unread tri-state only",
                state.get("library_filter") == 0 and state.get("library_filters") == filters,
                {"category": state.get("library_filter"), "filters": state.get("library_filters")})
    suite.check(case, "The native library total equals actual durable membership",
                state.get("library_count") == len(books), {"shown": state.get("library_count"), "store": len(books)})
    records = []
    for row in visible:
        stored = by_id.get(row.get("id"))
        observation = {"native_book_item": row, "actual_store_book": stored}
        suite.check(case, "Every visible unread count equals the latest authoritative durable book count",
                    stored is not None and type(row.get("unread")) is int
                    and row["unread"] == stored["unread"]
                    and row.get("in_library") is True, observation)
        if stored is not None and stored["chapter_count_comparison_applicable"]:
            suite.check(case, "A visible Demo book with real persisted chapters agrees with their actual read states",
                        stored["unread"] == stored["actual_unread"]
                        and stored["actual_chapters"] == stored["actual_read"] + stored["actual_unread"], observation)
        suite.check(case, "Every visible member satisfies the actual included or excluded unread condition",
                    stored is not None and (unread_filter == 0
                    or (stored["unread"] > 0 if unread_filter == 1 else stored["unread"] == 0)), observation)
        records.append(observation)
    interaction = snapshot["interaction"]
    suite.check(case, "The unread tri-state is durably persisted through real native actions",
                interaction.get("library_filters") == filters, interaction.get("library_filters"))
    if ascending is not None:
        suite.check(case, "The visible unread sort and direction match their durable native preference",
                    state.get("library_sort") == "unread-count" and state.get("library_ascending") is ascending
                    and interaction.get("library_sort") == "unread-count"
                    and interaction.get("library_ascending") is ascending,
                    {"native_sort": state.get("library_sort"), "native_ascending": state.get("library_ascending"),
                     "stored_sort": interaction.get("library_sort"), "stored_ascending": interaction.get("library_ascending")})
        ordered = _ordered(books, unread_filter, ascending)
        size = PAGE_SIZES[case["device"]]
        page = state.get("page")
        valid_page = type(page) is int and page >= 1
        expected = ordered[(page - 1) * size:page * size] if valid_page else []
        suite.check(case, "Actual visible membership and unread counts follow the Store's complete count order for this native page",
                    valid_page and identities == [book["id"] for book in expected]
                    and [row.get("unread") for row in visible] == [book["unread"] for book in expected]
                    and state.get("page_count") == max(1, (len(ordered) + size - 1) // size),
                    {"page": page, "native_capacity": size, "actual_eligible_books": len(ordered),
                     "expected_ids": [book["id"] for book in expected], "visible_ids": identities,
                     "expected_counts": [book["unread"] for book in expected]})
    case["c9_unread_qualification"] = True
    case.setdefault("visible_ui_requirements", []).append(
        "Independently inspect each actual visible unread badge and native filter/sort state against the retained Store observations; model values alone are not rendered-pixel proof.")
    _retain(suite, api, case, "actual-unread-store", {"store": snapshot, "visible_member_comparisons": records,
                                                      "unread_filter": unread_filter, "ascending": ascending,
                                                      "count_authority": "BookItems use persisted books.document.unread. Chapter counts are additional consistency evidence only when actual chapter rows exist; no chapter rows never imply a replacement zero badge."})


def _wait_marked(case: dict[str, Any], target: str) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    attempts = 0
    last_error = None
    snapshot = None
    durable = False
    while True:
        attempts += 1
        try:
            snapshot = _snapshot(case)
            stored = next((book for book in snapshot["library_books"] if book["id"] == target), None)
            durable = (stored is not None and stored["actual_chapters"] > 0
                       and stored["unread"] == 0 and stored["actual_unread"] == 0
                       and stored["actual_read"] == stored["actual_chapters"])
        except sqlite3.OperationalError as error:
            last_error = f"{type(error).__name__}: {error}"
        if durable or time.monotonic() - started >= 2.0:
            break
        time.sleep(0.1)
    if snapshot is None:
        raise ValueError(f"The actual post-action Store could not be observed: {last_error}")
    return snapshot, {"target_id": target, "attempts": attempts, "maximum_wait_seconds": 2.0,
                      "elapsed_seconds": time.monotonic() - started, "durable_target_read": bool(durable),
                      "last_read_error": last_error,
                      "scope": "Bounded read-only persistence observation, not a formal performance measurement or extra App capture"}


def _profile(suite: Any, api: Any, device: str) -> dict[str, Any]:
    def replay() -> Any:
        return api.Replay(start=700).intent("library", "filter", "0", wait=150)

    def capture(stage: str, actions: Any, *, data: Path | None = None,
                expected_page: int = 1) -> dict[str, Any]:
        case = suite.capture(f"{device}--c9-unread-{stage}", "library", device=device,
                             demo=True, offline=True, data=data, replay=actions, modal="",
                             expected={"page": expected_page}, capture_delay=1200)
        case["c9_unread_stage"] = stage
        suite.persist_case(case)
        return case

    baseline = capture("baseline", replay().intent("library", "sort-direction", "descending", wait=150))
    data = Path(baseline["data_dir"])
    base = _snapshot(baseline)
    _assess(suite, api, baseline, base, unread_filter=0, ascending=None)
    suite.check(baseline, "The fresh native Demo Store contains both actual unread and fully read books",
                any(book["unread"] > 0 for book in base["library_books"])
                and any(book["unread"] == 0 for book in base["library_books"]),
                {book["id"]: book["unread"] for book in base["library_books"]})
    suite.persist_case(baseline)

    include = capture("include", replay().intent("library", "sort", "unread-count")
                      .intent("library", "sort-direction", "ascending")
                      .intent("library", "cycle-filter", "unread", wait=200), data=data)
    _assess(suite, api, include, _snapshot(include), unread_filter=1, ascending=True)
    exclude = capture("exclude", replay().intent("library", "cycle-filter", "unread", wait=200)
                      .intent("library", "sort-direction", "descending"), data=data)
    _assess(suite, api, exclude, _snapshot(exclude), unread_filter=-1, ascending=False)
    ascending = capture("ascending", replay().intent("library", "cycle-filter", "unread", wait=200)
                        .intent("library", "sort-direction", "ascending"), data=data)
    _assess(suite, api, ascending, _snapshot(ascending), unread_filter=0, ascending=True)
    descending = capture("descending", replay().intent("library", "sort-direction", "descending"), data=data)
    before = _snapshot(descending)
    _assess(suite, api, descending, before, unread_filter=0, ascending=False)

    visible_ids = {book["id"] for book in descending["state"].get("visible_books", [])}
    candidates = [book for book in before["library_books"] if book["id"] in visible_ids
                  and book["unread"] > 0 and book["actual_unread"] > 0]
    suite.check(descending, "The real selected mark-read target is currently visible and has actual unread chapters",
                bool(candidates), [book["id"] for book in candidates])
    suite.persist_case(descending)
    if not candidates or not descending["passed"]:
        raise ValueError("No eligible real visible unread target exists; no synthetic Demo state is substituted")
    target = min(candidates, key=lambda book: (book["store_rowid"], book["id"]))
    predicted = [{**book, "unread": 0 if book["id"] == target["id"] else book["unread"]}
                 for book in before["library_books"]]
    # A native mark-read upsert preserves rowid. Derive the target page from
    # actual Store membership and that one action's expected zero unread value.
    ordered = _ordered(predicted, 0, True)
    target_rank = next(index for index, book in enumerate(ordered) if book["id"] == target["id"])
    target_page = target_rank // PAGE_SIZES[device] + 1
    actions = (replay().intent("library", "select", target["id"], wait=150)
               .intent("library", "batch-read", wait=2400)
               .intent("library", "sort-direction", "ascending", wait=200))
    if target_page > 1:
        actions.intent("page", "turn", str(target_page - 1), wait=200)
    marked = capture("mark-read", actions, data=data, expected_page=target_page)
    after, waited = _wait_marked(marked, target["id"])
    _assess(suite, api, marked, after, unread_filter=0, ascending=True)
    shown_target = [book for book in marked["state"].get("visible_books", []) if book.get("id") == target["id"]]
    state = marked["state"]
    suite.check(marked, "Actual selection mark-read commits real chapters and projects the visible target's zero unread count",
                waited["durable_target_read"] and len(shown_target) == 1 and shown_target[0].get("unread") == 0,
                {"target_before": target, "target_after_native": shown_target, "persistence": waited})
    posts = state.get("foreground_ready_posts")
    suite.check(marked, "The real foreground mark-read job posts completion and settles its native projection",
                state.get("busy") is False and state.get("capture_projection_pending") is False
                and type(posts) is int and posts >= 1 and state.get("selection_mode") is False
                and state.get("selected") == [],
                {key: state.get(key) for key in ("busy", "capture_projection_pending", "foreground_ready_posts",
                                               "selection_mode", "selected", "message")})
    suite.check(marked, "Mark-read preserves every actual reading-progress row instead of manufacturing completed progress",
                before["progress_rows"] == after["progress_rows"],
                {"before": before["progress_rows"], "after": after["progress_rows"]})
    _retain(suite, api, marked, "mark-read-durable-projection", {
        "actual_selected_target_before": target, "target_page_derived_from_store": target_page,
        "persistence": waited, "store_before": before, "store_after": after,
        "completion_contract": "Real native selection/batch-read, actual Store commit, visible zero count and current sort; selection clearing alone is not completion proof.",
        "foreground_ready_posts_semantics": "Cumulative successful foreground event-loop posts within this one native process; not an outstanding queue length",
    })
    return {"case_ids": [f"{device}--c9-unread-{stage}" for stage in STAGES], "capture_budget": 6,
            "data_dir": str(data), "actual_mark_read_target": target["id"], "target_page": target_page,
            "sql_mode": "read-only", "fixture_sql_writes": 0}


def run_unread_counts(suite: Any, api: Any) -> dict[str, Any]:
    """Capture at most twelve actual C9 cases across two fresh Demo profiles."""
    result: dict[str, Any] = {"expected_case_ids": list(CASE_IDS), "capture_budget": 12,
                              "profiles": {}, "errors": [], "fixture_sql_writes": 0}
    for device in PROFILES:
        try:
            result["profiles"][device] = _profile(suite, api, device)
        except Exception as error:
            message = f"{device}: {type(error).__name__}: {error}"
            result["errors"].append(message)
            failed = {"name": f"{device}--c9-unread-completion", "passed": False,
                      "manual_review_required": False, "c9_unread_qualification": True,
                      "checks": [{"description": "The bounded real unread sequence completed without a helper error",
                                  "passed": False, "actual": message}]}
            suite.cases.append(failed)
            suite.persist_case(failed)
    return result
