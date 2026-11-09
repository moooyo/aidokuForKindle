"""Bounded C9 search identity evidence from real loopback provider records."""

from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from PIL import Image


DRAFT = "typed-without-submission"
QUERY = "fixture"
CASE_IDS = (
    "pw5--c9-four-sources--fixture-seed",
    "pw5--c9-four-source-draft",
    "pw5--c9-four-source-more",
    "pw5--c9-four-source-mangadex",
    "pw5--c9-dense-sources--fixture-seed",
    "pw5--c9-dense-source-draft",
    "pw5--c9-dense-shortcut-00",
    "pw5--c9-dense-shortcut-01",
    "pw5--c9-dense-source-more-last",
    "pw5--c9-dense-source-komga",
    "scribe--c9-six-titles--fixture-seed",
    "scribe--c9-six-title-result",
    *(f"scribe--c9-tile-open-{number:03}" for number in range(6)),
    "scribe--c9-short-title-bookmark",
)


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _retain(suite: Any, api: Any, case: dict[str, Any], label: str, value: Any) -> None:
    path = Path(case["screenshot"]).parent / "fixtures" / f"{case['name']}--{label}.json"
    api.save_json(path, value)
    case.setdefault("evidence_hashes", {})[f"c9:{label}"] = {"path": str(path), "sha256": _sha256(path)}


def _mark(suite: Any, case: dict[str, Any], requirement: str) -> None:
    case["c9_contracts"] = ["C9-SEARCH-IDENTITY"]
    case["independent_original_review"] = not bool(case.get("prerequisite"))
    case.setdefault("visible_ui_requirements", []).append(requirement)
    suite.persist_case(case)


def _requests(provider: Any) -> list[dict[str, Any]]:
    with provider.lock:
        return copy.deepcopy(provider.requests)


def _search_http(suite: Any, api: Any, case: dict[str, Any], providers: dict[str, Any],
                 starts: dict[str, int], expected: int) -> None:
    slices = {identifier: _requests(provider)[starts[identifier]:] for identifier, provider in providers.items()}
    posts = [item for records in slices.values() for item in records
             if item.get("method") == "POST" and "/series/list" in item.get("path", "")]
    provider_counts = {identifier: sum(item.get("method") == "POST" and "/series/list" in item.get("path", "")
                                        for item in records) for identifier, records in slices.items()}
    provider_coverage = (len(providers) == 1 or len(providers) == expected
                         and all(value == 1 for value in provider_counts.values()))
    suite.check(case, "Draft editing and source-pointer selection add no search beyond the one actual initial request per source",
                len(posts) == expected and all(item.get("body", {}).get("fullTextSearch") == QUERY for item in posts)
                and provider_coverage
                and all(item.get("authorized") is True for records in slices.values() for item in records),
                {"expected_initial_searches": expected, "actual_search_posts": len(posts),
                 "provider_post_counts": provider_counts, "additional_searches": len(posts) - expected})
    observation = {"request_slices": slices, "initial_search_post_count": len(posts),
                   "provider_post_counts": provider_counts,
                   "expected_initial_search_post_count": expected,
                   "scope": "POST series/list search requests; genuine initial cover GET requests remain separately recorded"}
    _retain(suite, api, case, "actual-search-http", observation)
    suite.persist_case(case)


def _starts(providers: dict[str, Any]) -> dict[str, int]:
    return {identifier: len(_requests(provider)) for identifier, provider in providers.items()}


def _source_inputs(suite: Any, api: Any, seed: dict[str, Any], database: Path,
                   entries: list[dict[str, Any]]) -> None:
    # Only provider configuration is fixture input. Book titles, results,
    # library flags and pages are supplied by HTTP and ordinary app actions.
    with sqlite3.connect(database) as connection:
        original = json.loads(connection.execute("SELECT document FROM sources WHERE id='komga'").fetchone()[0])
        connection.execute("DELETE FROM sources")
        configured = [dict(original, **entry) for entry in entries]
        for source in configured:
            connection.execute("INSERT INTO sources(id,document) VALUES(?,?)", (source["id"], json.dumps(source)))
    _retain(suite, api, seed, "source-configuration-inputs", {"source_configuration_only": True, "sources": configured})
    suite.persist_case(seed)


def _typing(api: Any) -> Any:
    return (api.Replay().intent("search", "query", QUERY, wait=7000).intent("search", "edit")
            .intent("keyboard", "mode", "english", wait=100).intent("keyboard", "key", "clear", wait=100)
            .type_text(DRAFT, wait=40))


def _draft(suite: Any, case: dict[str, Any], count: int, scope: str = "all") -> None:
    for key, value in {"query": QUERY, "keyboard_text": DRAFT, "keyboard_mode": "english",
                       "keyboard_composition": "", "keyboard_target": "search", "search_scope": scope}.items():
        suite.expect(case, key, value)
    sources = case["state"].get("search_sources", [])
    suite.check(case, "All actual eligible provider identities remain available while the draft stays unsubmitted",
                len(sources) == count and len({item.get("id") for item in sources}) == count, sources)


def _shortcut_points(suite: Any, api: Any, case: dict[str, Any]) -> dict[str, list[float]]:
    more = suite.scope_more_position(case)
    boxes = case.get("native_scope_shortcuts", {}).get("border_components", [])
    sources = case["state"].get("search_sources", [])
    if len(boxes) != 4 or len(sources) < 3:
        raise ValueError("The actual Paperwhite shortcut strip does not have All, two identities and More")
    points = {sources[index]["id"]: [(boxes[index + 1][0] + boxes[index + 1][2]) / 2,
                                     (boxes[index + 1][1] + boxes[index + 1][3]) / 2] for index in range(2)}
    points["more"] = list(more)
    observation = {"original_case_id": case["name"], "original_screenshot": case["screenshot"],
                   "original_sha256": _sha256(Path(case["screenshot"])), "native_border_components": boxes,
                   "points_by_actual_source_id": points, "shortcut_sources": sources[:2]}
    _retain(suite, api, case, "native-shortcut-identities", observation)
    suite.persist_case(case)
    return points


def _picker_point(suite: Any, api: Any, case: dict[str, Any], identifier: str) -> list[float]:
    rows = case["state"].get("modal_items", [])
    indexes = [index for index, row in enumerate(rows) if row.get("id") == identifier and row.get("enabled") is True]
    if len(indexes) != 1 or case["state"].get("modal_context") != "search-scope:typing":
        raise ValueError("The actual typing picker does not expose the requested eligible source row")
    index = indexes[0]
    with Image.open(case["screenshot"]) as original:
        gray = original.convert("L")
        panel_height = 76 + len(rows) * 54 + 24 + (52 if case["state"].get("panel_page_count", 1) > 1 else 0)
        panel_top = gray.height - panel_height
        row_top = panel_top + 76 + index * 54
        points = [(x, y) for y in range(row_top + 6, row_top + 48) for x in range(26, gray.width - 26)]
        ink = sum(gray.getpixel(point) < 128 for point in points)
        suite.check(case, "The pointer target is an actually visible named row inside the native typing picker", ink >= 10,
                    {"id": identifier, "row_top": row_top, "visible_ink_pixels": ink})
        hit = [gray.width / 2, row_top + 27]
    observation = {"original_case_id": case["name"], "original_screenshot": case["screenshot"],
                   "original_sha256": _sha256(Path(case["screenshot"])), "actual_row": rows[index],
                   "row_rectangle": [22, row_top, gray.width - 44, 54], "hit": hit,
                   "panel_page": case["state"].get("panel_page"), "panel_page_count": case["state"].get("panel_page_count")}
    _retain(suite, api, case, "native-picker-row-identity", observation)
    suite.persist_case(case)
    return hit


def _pw_four(suite: Any, api: Any) -> None:
    with ExitStack() as stack:
        providers = {"empty": stack.enter_context(api.Fixture(empty=True)),
                     "error": stack.enter_context(api.Fixture(fail=True)),
                     "fast": stack.enter_context(api.Fixture()),
                     "mangadex": stack.enter_context(api.Fixture(search_delay=4.0))}
        data, database = suite.seed_provider("pw5--c9-four-sources", providers["fast"], "pw5")
        seed = suite.cases[-1]
        _source_inputs(suite, api, seed, database,
                       [{"id": identifier, "name": "MangaDex" if identifier == "mangadex" else f"Actual {identifier} fixture",
                         "base_url": provider.url, "kind": "komga"} for identifier, provider in providers.items()])
        starts = _starts(providers)
        draft = suite.capture("pw5--c9-four-source-draft", "search", device="pw5", demo=False, offline=False,
                              data=data, replay=_typing(api), modal="keyboard")
        _draft(suite, draft, 4)
        points = _shortcut_points(suite, api, draft)
        _search_http(suite, api, draft, providers, starts, 4)
        _mark(suite, draft, "The fresh four-provider strip exposes real source identities and More while the unsubmitted draft is retained.")
        starts = _starts(providers)
        picker = suite.capture("pw5--c9-four-source-more", "search", device="pw5", demo=False, offline=False, data=data,
                               replay=_typing(api).tap(*points["more"]), modal="choices", context="search-scope:typing")
        _draft(suite, picker, 4)
        hit = _picker_point(suite, api, picker, "mangadex")
        _search_http(suite, api, picker, providers, starts, 4)
        _mark(suite, picker, "The actual More picker visibly exposes the complete MangaDex configured source name, rather than an inaccessible hidden identity.")
        starts = _starts(providers)
        selected = suite.capture("pw5--c9-four-source-mangadex", "search", device="pw5", demo=False, offline=False, data=data,
                                 replay=_typing(api).tap(*points["more"]).tap(*hit), modal="keyboard")
        _draft(suite, selected, 4, "mangadex")
        source = next((item for item in selected["state"].get("search_sources", []) if item.get("id") == "mangadex"), {})
        suite.check(selected, "The hidden source selection retains its true configured full name", source.get("name") == "MangaDex", source)
        _search_http(suite, api, selected, providers, starts, 4)
        _mark(suite, selected, "The selected More state visibly identifies MangaDex in the pending scope line and preserves the actual keyboard draft.")


def _pw_dense(suite: Any, api: Any) -> None:
    with api.Fixture() as provider:
        data, database = suite.seed_provider("pw5--c9-dense-sources", provider, "pw5")
        seed = suite.cases[-1]
        entries = [{"id": f"configured-{index:02}",
                    "name": f"Configured local fixture {index:02} with a deliberately long source name",
                    "base_url": provider.url} for index in range(24)]
        entries.append({"id": "komga", "name": "Local komga fixture", "base_url": provider.url})
        _source_inputs(suite, api, seed, database, entries)
        providers = {"all-configured-sources": provider}
        starts = _starts(providers)
        draft = suite.capture("pw5--c9-dense-source-draft", "search", device="pw5", demo=False, offline=False, data=data,
                              replay=_typing(api), modal="keyboard")
        _draft(suite, draft, 25)
        points = _shortcut_points(suite, api, draft)
        first = draft["state"].get("search_sources", [])[:2]
        suite.check(draft, "The two real long shortcuts retain distinct 00 and 01 numeric identities",
                    [item.get("id") for item in first] == ["configured-00", "configured-01"]
                    and first[0].get("display_name") != first[1].get("display_name")
                    and "00" in first[0].get("display_name", "") and "01" in first[1].get("display_name", ""), first)
        _search_http(suite, api, draft, providers, starts, 25)
        _mark(suite, draft, "The actual 00 and 01 shortcut labels remain distinguishable inside separate complete native frames.")
        for index in range(2):
            identifier = f"configured-{index:02}"
            starts = _starts(providers)
            case = suite.capture(f"pw5--c9-dense-shortcut-{index:02}", "search", device="pw5", demo=False, offline=False, data=data,
                                 replay=_typing(api).tap(*points[identifier]), modal="keyboard")
            _draft(suite, case, 25, identifier)
            _search_http(suite, api, case, providers, starts, 25)
            _mark(suite, case, f"A real pointer hit on shortcut {index:02} selects its own actual source ID and keeps the draft unsubmitted.")
        starts = _starts(providers)
        picker = suite.capture("pw5--c9-dense-source-more-last", "search", device="pw5", demo=False, offline=False, data=data,
                               replay=_typing(api).tap(*points["more"]).intent("panel", "page", "99999"),
                               modal="choices", context="search-scope:typing")
        _draft(suite, picker, 25)
        suite.check(picker, "The real overflow picker reaches the final bounded page containing Komga",
                    picker["state"].get("panel_page") == picker["state"].get("panel_page_count")
                    and picker["state"].get("modal_item_ids") == ["configured-22", "configured-23", "komga"],
                    picker["state"].get("modal_items"))
        hit = _picker_point(suite, api, picker, "komga")
        _search_http(suite, api, picker, providers, starts, 25)
        _mark(suite, picker, "The true last More page retains its actual source identities and native picker boundaries.")
        starts = _starts(providers)
        chosen = suite.capture("pw5--c9-dense-source-komga", "search", device="pw5", demo=False, offline=False, data=data,
                               replay=_typing(api).tap(*points["more"]).intent("panel", "page", "99999").tap(*hit), modal="keyboard")
        _draft(suite, chosen, 25, "komga")
        _search_http(suite, api, chosen, providers, starts, 25)
        _mark(suite, chosen, "The actual last-page Komga row selects Komga through native pointer input and visibly retains its full name with the untouched keyboard draft.")


def _ink_bounds(gray: Any, rectangle: list[float], threshold: int = 160) -> list[int] | None:
    left, top, width, height = rectangle
    points = [(x, y) for y in range(math.ceil(top), math.floor(top + height))
              for x in range(math.ceil(left), math.floor(left + width)) if gray.getpixel((x, y)) < threshold]
    return [min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points) + 1, max(y for _, y in points) + 1] if points else None


def _six_tiles(suite: Any, api: Any, case: dict[str, Any], *, short_first: bool = False) -> list[dict[str, Any]]:
    groups = case["state"].get("search_groups", [])
    if len(groups) != 1 or groups[0].get("id") != "slow" or len(groups[0].get("books", [])) != 6:
        raise ValueError("The fresh native search page does not expose the six actual slow-provider tiles")
    group = groups[0]
    suite.check(case, "The six-tile header uses the actual delayed provider's complete configured name",
                group.get("title") == "Actual slow fixture" and group.get("pending") is False
                and group.get("count") == 6, group)
    controls = []
    with Image.open(case["screenshot"]) as original:
        gray = original.convert("L")
        card_width = (gray.width - 72 - 22 * 5) / 6
        top = 36 + 159 + 50 + 48
        for index, book in enumerate(group["books"]):
            left = 36 + index * (card_width + 22)
            short = short_first and index == 0
            title_width = card_width - (22 if book.get("in_library") else 0)
            cover = [left, top, 126, 166]
            title = [left, top + (174 if short else 170), title_width, 22 if short else 44]
            author = [left, top + (200 if short else 218), card_width, 18]
            title_ink = _ink_bounds(gray, title)
            author_ink = _ink_bounds(gray, author)
            cover_points = [(x, y) for y in range(top + 5, top + 161)
                            for x in range(math.ceil(left + 5), math.floor(left + 121))]
            dark_cover = sum(gray.getpixel(point) < 128 for point in cover_points) / len(cover_points)
            horizontal = range(math.ceil(left + 8), math.floor(left + 118))
            vertical = range(top + 8, top + 158)
            edge_samples = {
                "top": [[(x, y) for x in horizontal] for y in (top, top + 1)],
                "bottom": [[(x, y) for x in horizontal] for y in (top + 164, top + 165)],
                "left": [[(x, y) for y in vertical] for x in range(math.floor(left), math.ceil(left) + 2)],
                "right": [[(x, y) for y in vertical] for x in range(math.floor(left + 124), math.ceil(left + 126))],
            }
            edge_fractions = {edge: max(sum(gray.getpixel(point) < 224 for point in samples) / len(samples)
                                        for samples in alternatives) for edge, alternatives in edge_samples.items()}
            suite.check(case, "Each pointer identity target has the actual native cover boundary at its visible tile position",
                        all(value >= 0.8 for value in edge_fractions.values()),
                        {"id": book.get("id"), "cover_rectangle": cover, "edge_ink_fractions": edge_fractions})
            suite.check(case, "Each native identity tile contains an actual prepared fixture cover", dark_cover >= 0.08,
                        {"id": book.get("id"), "cover_rectangle": cover, "dark_fraction": dark_cover})
            suite.check(case, "The actual title and author ink occupy their separate native rows",
                        title_ink is not None and author_ink is not None and title_ink[3] <= author[1],
                        {"id": book.get("id"), "title_ink": title_ink, "author_ink": author_ink})
            if not short:
                suite.check(case, "The actual series identity uses the intended two-line title space",
                            title_ink is not None and title_ink[3] - title_ink[1] > 22, title_ink)
            else:
                bookmark = [left + card_width - 16, top + 177, 16, 16]
                bookmark_ink = _ink_bounds(gray, bookmark)
                suite.check(case, "The real in-library bookmark stays separate from the short title's native ink",
                            book.get("in_library") is True and bookmark_ink is not None
                            and title_ink is not None and title_ink[2] < bookmark[0],
                            {"title_ink": title_ink, "bookmark_ink": bookmark_ink, "bookmark_rectangle": bookmark})
            controls.append({"id": book["id"], "title": book["title"], "author": book.get("author"),
                             "in_library": book.get("in_library"), "cover_rectangle": cover,
                             "native_cover_edge_ink_fractions": edge_fractions,
                             "title_rectangle": title, "author_rectangle": author,
                             "title_ink_bounds": title_ink, "author_ink_bounds": author_ink,
                             "hit": [left + min(63, card_width / 2), top + 83]})
    _retain(suite, api, case, "actual-six-tile-geometry", {"original_case_id": case["name"],
            "original_screenshot": case["screenshot"], "original_sha256": _sha256(Path(case["screenshot"])),
            "controls": controls, "text_identity_requires_independent_original_review": True})
    suite.persist_case(case)
    return controls


class ShortTitleResponse:
    """Serve one small title through the real HTTP writer, preserving provenance."""

    def __init__(self, suite: Any, api: Any, provider: Any):
        self.records = []
        self.provider = provider
        self.original = provider.server.RequestHandlerClass.respond
        self.handler = provider.server.RequestHandlerClass
        owner = self

        def respond(request: Any, status: int, body: Any, content_type: str = "application/json") -> None:
            outgoing = body
            if status == 200 and isinstance(body, dict) and isinstance(body.get("content"), list):
                outgoing = copy.deepcopy(body)
                changed = False
                for item in outgoing["content"]:
                    if item.get("id") == "series-000" and isinstance(item.get("metadata"), dict):
                        item["metadata"]["title"] = "Short 000"
                        changed = True
                if changed:
                    directory = suite.output / "fixtures" / "scribe--c9-short-title-http"
                    directory.mkdir(parents=True, exist_ok=True)
                    number = len(owner.records) + 1
                    paths = []
                    for label, payload in (("original", body), ("sent", outgoing)):
                        path = directory / f"response-{number:03}-{label}.json"
                        path.write_bytes(json.dumps(payload).encode("utf-8"))
                        paths.append({"role": label, "path": str(path), "sha256": _sha256(path)})
                    record = {"method": request.command, "path": request.path, "bodies": paths,
                              "changed_actual_remote_id": "series-000", "fixture_title": "Short 000",
                              "scope": "Public HTTP metadata input only; no book row, library flag, or screenshot is edited"}
                    owner.records.append(record)
                    api.save_json(directory / f"response-{number:03}-provenance.json", record)
            owner.original(request, status, outgoing, content_type)

        self.handler.respond = respond

    def restore(self) -> None:
        self.handler.respond = self.original


def _sc_six(suite: Any, api: Any) -> None:
    with api.Fixture(search_delay=4.0) as provider:
        provider.series_count = 6
        data, database = suite.seed_provider("scribe--c9-six-titles", provider, "scribe")
        seed = suite.cases[-1]
        _source_inputs(suite, api, seed, database,
                       [{"id": "slow", "name": "Actual slow fixture", "base_url": provider.url, "kind": "komga"}])
        replay = lambda: api.Replay().intent("search", "query", QUERY, wait=5000)
        baseline = suite.capture("scribe--c9-six-title-result", "search", device="scribe", demo=False, offline=False,
                                 data=data, replay=replay(), modal="")
        controls = _six_tiles(suite, api, baseline)
        expected_ids = [api.compound_id("slow", f"series-{number:03}") for number in range(6)]
        suite.check(baseline, "Six actual HTTP records expose different series000 through series005 identities",
                    [item["id"] for item in controls] == expected_ids
                    and all(item["title"] == f"Local fixture series {number:03}" and item["author"] == "Fixture Author"
                            for number, item in enumerate(controls)), controls)
        suite.save_provider(baseline["name"], provider, baseline)
        _mark(suite, baseline, "All six series000–005 titles are readable in their two-line native title space, with author rows separated below and the actual slow source name above.")
        opened = []
        for number, control in enumerate(controls):
            actions = replay().tap(*control["hit"], wait=1400)
            if number == 0:
                actions.intent("detail", "favorite", wait=200)
            case = suite.capture(f"scribe--c9-tile-open-{number:03}", "search", device="scribe", demo=False, offline=False,
                                 data=data, replay=actions, expected_route="detail", modal="",
                                 expected={"current_book": control["id"]})
            rows = suite.database_rows(case, "SELECT source_id,remote_id,title,in_library FROM books WHERE id=?", (control["id"],))
            suite.check(case, "The actual native tile hit opens its own persisted provider identity",
                        len(rows) == 1 and rows[0][0:3] == ("slow", f"series-{number:03}", control["title"]), rows)
            if number == 0:
                suite.check(case, "The in-library flag is created by the real favorite action after opening the first tile",
                            bool(rows) and rows[0][3] == 1, rows)
            actual_actions = json.loads(Path(case["actions"]).read_text(encoding="utf-8-sig"))
            pointer_actions = [action for action in actual_actions if action.get("section") == "pointer"]
            suite.check(case, "The book identity hit is one actual pointer tap with no semantic open substitute",
                        len(pointer_actions) == 3
                        and sum(action.get("action") == "press" for action in pointer_actions) == 1
                        and sum(action.get("action") == "release" for action in pointer_actions) == 1
                        and not any(action.get("action") == "open" and action.get("section") in ("catalog", "search")
                                    for action in actual_actions), pointer_actions)
            _retain(suite, api, case, "native-tile-pointer-origin", {"origin_case_id": baseline["name"],
                    "original_screenshot": baseline["screenshot"], "original_sha256": _sha256(Path(baseline["screenshot"])),
                    "actual_visible_control": control, "expected_opened_id": control["id"],
                    "actual_pointer_actions": pointer_actions,
                    "input": "A real native cover/tile pointer press and release; no search/open or catalog/open intent is substituted"})
            opened.append(case["state"].get("current_book"))
            suite.save_provider(case["name"], provider, case)
            _mark(suite, case, "The actual pointer hit opens the matching book identity and its real detail records.")
        suite.check(case, "All six native tile hits resolve to six distinct actual books", opened == expected_ids and len(set(opened)) == 6, opened)
        suite.persist_case(case)
        variant = ShortTitleResponse(suite, api, provider)
        try:
            short = suite.capture("scribe--c9-short-title-bookmark", "search", device="scribe", demo=False, offline=False,
                                  data=data, replay=replay(), modal="")
            short_controls = _six_tiles(suite, api, short, short_first=True)
            suite.check(short, "The short title is served by real HTTP for the already favorited actual book",
                        short_controls[0]["id"] == expected_ids[0] and short_controls[0]["title"] == "Short 000"
                        and short_controls[0]["in_library"] is True and bool(variant.records), short_controls[0])
            _retain(suite, api, short, "short-title-actual-http", variant.records)
            suite.save_provider(short["name"], provider, short)
            _mark(suite, short, "The real short title and in-library bookmark occupy separate native regions without colliding, while author information remains below.")
        finally:
            variant.restore()


def run_search_identity(suite: Any, api: Any) -> None:
    """Capture nineteen fixed fresh identity cases without semantic hit substitutes."""
    _pw_four(suite, api)
    _pw_dense(suite, api)
    _sc_six(suite, api)
