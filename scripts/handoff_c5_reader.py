"""Eight native C5 cases for prospective orientation and lazy demo routes."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from PIL import Image


CASE_IDS = (
    "scribe--c5-orientation-portrait-automatic",
    "scribe--c5-orientation-landscape-automatic",
    "scribe--c5-orientation-landscape-single",
    "scribe--c5-orientation-landscape-vertical",
    "pw5--c5-cold-reader-no-catalog",
    "pw5--c5-cold-reader-to-browse",
    "scribe--c5-cold-management-no-catalog",
    "scribe--c5-cold-management-to-browse",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _retain(api: Any, case: dict[str, Any], label: str, payload: Any) -> None:
    path = Path(case["screenshot"]).parent / "fixtures" / f"{case['name']}--{label}.json"
    api.save_json(path, payload)
    case.setdefault("evidence_hashes", {})[f"c5:{label}"] = {
        "path": str(path), "sha256": _sha256(path),
    }


def _mark(case: dict[str, Any], qualifier: bool, contract: str, requirement: str) -> None:
    case["c5_qualifier"] = bool(qualifier)
    case["c5_contracts"] = [contract]
    case.setdefault("visible_ui_requirements", []).append(requirement)


def _processed_resources(suite: Any, case: dict[str, Any]) -> list[dict[str, Any]]:
    displayed = case["state"].get("displayed_reader") or {}
    resources = []
    for field in ("first_path", "second_path"):
        stored = displayed.get(field)
        if not stored:
            continue
        path = Path(stored)
        record: dict[str, Any] = {"field": field, "path": str(path)}
        try:
            if not path.is_absolute():
                raise ValueError("A committed processed image path is not absolute")
            with Image.open(path) as original:
                record["dimensions"] = list(original.size)
                record["format"] = original.format
            record.update({"bytes": path.stat().st_size, "sha256": _sha256(path)})
            suite.check(case, "The displayed reader retains an actual processed PNG resource",
                        record["format"] == "PNG" and all(value > 0 for value in record["dimensions"]), record)
        except (OSError, ValueError) as error:
            record["error"] = f"{type(error).__name__}: {error}"
            suite.check(case, "The displayed processed resource remains inspectable", False, record)
        resources.append(record)
    return resources


def _reader_identity(suite: Any, api: Any, case: dict[str, Any], *,
                     landscape: bool, spread: bool, direction: str) -> dict[str, Any]:
    state = case["state"]
    displayed = state.get("displayed_reader")
    suite.check(case, "A real committed reader frame is available", isinstance(displayed, dict), displayed)
    displayed = displayed if isinstance(displayed, dict) else {}
    session = displayed.get("session", {})
    preferences = displayed.get("preferences", {})
    effective = state.get("reader_effective", {})
    for key in ("reader_inflight", "reader_dirty"):
        suite.expect(case, key, False)
    suite.expect(case, "reader_image_ready", True)
    suite.expect(case, "display_landscape", landscape)
    suite.check(case, "Effective reader geometry follows the actual layout and direction",
                effective.get("landscape") is landscape and effective.get("spread") is spread
                and effective.get("reading_direction") == direction, effective)
    suite.check(case, "The committed frame uses the same effective reader preferences",
                preferences == effective and session.get("landscape") is landscape
                and session.get("spread") is spread, {"session": session, "preferences": preferences})
    for key, state_key in (("book_id", "current_book"), ("chapter_id", "current_chapter"),
                           ("page", "reader_page"), ("total", "reader_total"),
                           ("vertical_offset", "reader_offset"), ("zoom", "reader_zoom")):
        suite.check(case, f"The committed reader {key} matches its live identity",
                    session.get(key) == state.get(state_key),
                    {"committed": session.get(key), "live": state.get(state_key)})
    suite.check(case, "The committed reader identity is a genuine nonempty book and chapter",
                bool(session.get("book_id")) and bool(session.get("chapter_id"))
                and type(session.get("page")) is int and type(session.get("total")) is int
                and 1 <= session["page"] <= session["total"], session)
    suite.check(case, "The visible chapter and total describe the committed frame",
                state.get("visible_reader_chapter_id") == session.get("chapter_id")
                and state.get("visible_reader_total") == session.get("total"),
                {"chapter": state.get("visible_reader_chapter_id"), "total": state.get("visible_reader_total")})
    visible = state.get("reader_visible_pages", [])
    expected_count = 2 if spread else 1
    suite.check(case, "Actual displayed resources reflect the effective single or double-page frame",
                isinstance(visible, list) and len(visible) == expected_count
                and bool(displayed.get("first_path"))
                and bool(displayed.get("second_path")) is spread,
                {"visible_pages": visible, "first_path": displayed.get("first_path"),
                 "second_path": displayed.get("second_path")})
    # A committed session records a closed menu at publication time. Its
    # menu_open field is not the subsequently opened orientation-panel state.
    observation = {
        "session": session, "effective": effective,
        "reader_visible_pages": visible,
        "processed_resources": _processed_resources(suite, case),
        "native_resource_evidence": {key: value for key, value in case.get("evidence_hashes", {}).items()
                                     if key in ("screenshot", "state", "metadata", "actions", "log")},
    }
    case["c5_reader_resources"] = observation
    _retain(api, case, "reader-identity", observation)
    return observation


def _orientation_pixels(suite: Any, api: Any, case: dict[str, Any], *,
                        landscape: bool, prospective: bool, direction: str, layout: str) -> None:
    """Observe real card surfaces; leave actual label wording to original-PNG review."""
    observation: dict[str, Any] = {
        "method": "Original native pixels at the production orientation-card rectangles",
        "dynamic_labels_require_independent_original_review": True,
        "cards": [],
    }
    try:
        path = Path(case["screenshot"])
        with Image.open(path) as original:
            gray = original.convert("L")
            width, height = gray.size
            inset, gap, card_height = (120 if landscape else 48), 12, 104
            card_width = (width - inset * 2 - gap * 2) / 3
            panel_top = height - 210 - 186
            card_top = panel_top + 19.5 + 31.5 + 14
            selected = "landscape" if landscape else "portrait"
            suffix = api.ui_label("double_page") if prospective else (
                api.ui_label("single_page") if direction in ("vertical", "vertical-pages") or layout == "single" else "")

            def line_fraction(points: list[tuple[int, int]], threshold: int = 224) -> float:
                return sum(gray.getpixel(point) < threshold for point in points) / max(1, len(points))

            for index, orientation in enumerate(("portrait", "landscape", "reverse-landscape")):
                left = inset + index * (card_width + gap)
                right, bottom = left + card_width, card_top + card_height
                x0, x1 = math.floor(left), math.ceil(right)
                y0, y1 = math.floor(card_top), math.ceil(bottom)
                if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                    raise ValueError("The source-defined orientation card lies outside the native PNG")
                horizontal = range(math.ceil(left + 8), math.floor(right - 8))
                vertical = range(math.ceil(card_top + 8), math.floor(bottom - 8))
                edges = {
                    "top": max(line_fraction([(x, y) for x in horizontal]) for y in range(y0, min(y1, y0 + 3))),
                    "bottom": max(line_fraction([(x, y) for x in horizontal]) for y in range(max(y0, y1 - 3), y1)),
                    "left": max(line_fraction([(x, y) for y in vertical]) for x in range(x0, min(x1, x0 + 3))),
                    "right": max(line_fraction([(x, y) for y in vertical]) for x in range(max(x0, x1 - 3), x1)),
                }
                interior = [(x, y) for y in range(y0 + 7, y0 + 12)
                            for x in range(x0 + 12, x1 - 12)]
                dark_fill = line_fraction(interior, 96)
                chosen = orientation == selected
                label_key = "reverse_landscape" if orientation == "reverse-landscape" else orientation
                record = {"orientation": orientation, "rectangle": [left, card_top, card_width, card_height],
                          "edge_ink_fractions": edges, "interior_dark_fraction": dark_fill,
                          "expected_selected": chosen,
                          "expected_text_for_independent_review": api.ui_label(label_key) + ("" if index == 0 else suffix)}
                suite.check(case, "The original PNG actually contains the complete orientation-card surface",
                            all(value >= 0.75 for value in edges.values()), record)
                suite.check(case, "The original orientation-card fill agrees with the real selected direction",
                            dark_fill >= 0.85 if chosen else dark_fill <= 0.15, record)
                observation["cards"].append(record)
            observation.update({"original_screenshot": str(path), "original_sha256": _sha256(path),
                                "native_dimensions": [width, height], "panel_top": panel_top})
    except (OSError, ValueError) as error:
        observation["error"] = f"{type(error).__name__}: {error}"
        suite.check(case, "Actual orientation-card pixels are inspectable", False, observation["error"])
    case["c5_native_orientation_controls"] = observation
    _retain(api, case, "native-orientation-controls", observation)


def _catalog_inventory(suite: Any, api: Any, case: dict[str, Any]) -> dict[str, Any]:
    queries = {
        "all_books": "SELECT COUNT(*) FROM books",
        "generated_books": "SELECT COUNT(*) FROM books WHERE remote_id LIKE 'catalog-%'",
        "volume_books": "SELECT COUNT(*) FROM books WHERE remote_id LIKE 'catalog-volume-%'",
        "generated_page_positions": "SELECT COUNT(*) FROM pages p JOIN chapters c ON c.id=p.chapter_id "
                                    "JOIN books b ON b.id=c.book_id WHERE b.remote_id LIKE 'catalog-%'",
    }
    observed: dict[str, Any] = {"database": case["state"].get("database")}
    for label, query in queries.items():
        rows = suite.database_rows(case, query)
        observed[label] = rows[0][0] if rows else None
    visible = case["state"].get("visible_catalog", [])
    backed = []
    for item in visible:
        rows = suite.database_rows(case, "SELECT id,title FROM books WHERE id=?", (item.get("id"),))
        backed.append({"visible_id": item.get("id"), "visible_title": item.get("title"),
                       "persisted": [list(row) for row in rows]})
    observed["visible_persisted_records"] = backed
    _retain(api, case, "catalog-inventory", observed)
    case["c5_catalog_inventory"] = observed
    return observed


def _cold_catalog(suite: Any, api: Any, case: dict[str, Any]) -> dict[str, Any]:
    suite.expect(case, "catalog_count", 0)
    suite.expect(case, "visible_catalog", [])
    observed = _catalog_inventory(suite, api, case)
    suite.check(case, "A fresh non-Browse route retains only the real 46-book base demo fixture",
                observed.get("all_books") == 46 and observed.get("generated_books") == 0
                and observed.get("volume_books") == 0 and observed.get("generated_page_positions") == 0,
                observed)
    return observed


def _browse_catalog(suite: Any, api: Any, case: dict[str, Any], origin: dict[str, Any], *,
                    large: bool, action: str) -> None:
    suite.expect(case, "catalog_count", 128)
    suite.expect(case, "catalog_result_count", 128)
    suite.expect(case, "catalog_has_more", False)
    suite.expect(case, "page", 1)
    suite.expect(case, "page_count", 9 if large else 26)
    suite.expect(case, "source_id", "mangadex")
    visible = case["state"].get("visible_catalog", [])
    suite.check(case, "The actual Browse screenshot presents a populated native page of real catalog cards",
                len(visible) == (15 if large else 5) and all(bool(item.get("id")) for item in visible), visible)
    suite.expect(case, "cover_visible_failure_count", 0)
    observed = _catalog_inventory(suite, api, case)
    generated, volumes = (125, 118) if large else (126, 123)
    before = origin.get("c5_catalog_inventory", {})
    suite.check(case, "Entering real Browse materializes the profile's actual catalog records and manifests",
                observed.get("generated_books") == generated and observed.get("volume_books") == volumes
                and observed.get("generated_page_positions") == generated * 40
                and before.get("all_books") == 46 and observed.get("all_books") == 46 + generated,
                {"before": before, "after": observed})
    suite.check(case, "Every visible catalog identity and title is backed by an actual persisted book",
                len(observed["visible_persisted_records"]) == len(visible)
                and all(item["persisted"] == [[item["visible_id"], item["visible_title"]]]
                        for item in observed["visible_persisted_records"]),
                observed["visible_persisted_records"])
    suite.check(case, "Ordinary Browse preserves the existing demo library and reading history",
                case["state"].get("library_count") == origin["state"].get("library_count")
                and case["state"].get("history_count") == origin["state"].get("history_count"),
                {"library_before": origin["state"].get("library_count"),
                 "library_after": case["state"].get("library_count"),
                 "history_before": origin["state"].get("history_count"),
                 "history_after": case["state"].get("history_count")})
    case["c5_navigation_origin"] = {
        "case_id": origin["name"], "original_screenshot": origin["screenshot"],
        "original_evidence": origin.get("evidence_hashes", {}), "actual_navigation": action,
        "shared_outer_data_dir": origin["data_dir"],
        "origin_catalog_count": origin["state"].get("catalog_count"),
        "destination_catalog_count": case["state"].get("catalog_count"),
        "proof_scope": "A real navigation replay, populated native catalog cards, and read-only persisted records",
    }
    _retain(api, case, "cold-to-browse", case["c5_navigation_origin"])


def run_reader_routes(suite: Any, api: Any, qualifier: bool = False) -> None:
    """Capture the same four open panels and four cold-route states in both suites."""
    Replay = api.Replay
    modes = (
        (CASE_IDS[0], "portrait", "automatic", "rtl", False, False, True),
        (CASE_IDS[1], "landscape", "automatic", "rtl", True, True, True),
        (CASE_IDS[2], "landscape", "single", "rtl", True, False, False),
        (CASE_IDS[3], "landscape", "double", "vertical", True, False, False),
    )
    for name, orientation, layout, direction, landscape, actual_spread, prospective in modes:
        replay = (Replay(start=3500).intent("settings", "set", "remember-book-direction=false", wait=100)
                  .intent("settings", "set", f"page-layout-mode={layout}", wait=100)
                  .intent("settings", "set", "cover-alone=false", wait=100)
                  .intent("settings", "set", f"reading-direction={direction}", wait=150)
                  .intent("settings", "set", f"orientation={orientation}", wait=2500)
                  .intent("reader", "seek", "12", wait=2500)
                  .intent("reader", "menu", wait=150).intent("reader", "orientation"))
        case = suite.capture(name, "reader", device="scribe", landscape=landscape,
                             demo=True, offline=True, replay=replay, expected_landscape=landscape,
                             modal="choices", context="reader-orientation",
                             expected={"menu": True, "reader_panel": "orientation", "page_layout_mode": layout,
                                       "reading_direction": direction, "reader_landscape_spread": prospective,
                                       "display_rotation_degrees": 90 if landscape else 0})
        _mark(case, qualifier, "C4-C5-G12",
              "The actual open orientation panel visibly distinguishes prospective landscape spread from the current committed frame; single and vertical never claim double-page behavior.")
        suite.check(case, "Native metadata describes the committed requested orientation",
                    case.get("metadata", {}).get("rotation_degrees") == (90 if landscape else 0),
                    case.get("metadata", {}).get("rotation_degrees"))
        rows = case["state"].get("modal_items", [])
        suite.check(case, "The real open orientation panel retains all three actual manual choices",
                    [row.get("id") for row in rows] == ["portrait", "landscape", "reverse-landscape"], rows)
        suite.check(case, "Exactly the actual current orientation is selected",
                    [row.get("id") for row in rows if row.get("value")] == [orientation], rows)
        _reader_identity(suite, api, case, landscape=landscape, spread=actual_spread, direction=direction)
        _orientation_pixels(suite, api, case, landscape=landscape, prospective=prospective,
                            direction=direction, layout=layout)
        case["c5_orientation_contract"] = {
            "current_layout": layout, "current_direction": direction, "current_orientation": orientation,
            "actual_spread": actual_spread, "prospective_landscape_spread": prospective,
            "label_evidence": "Dynamic wording and diagram dividers require independent review of this unmodified original PNG; modal_items expose base labels only.",
        }
        suite.persist_case(case)

    cold_reader = suite.capture(CASE_IDS[4], "reader", device="pw5", demo=True, offline=True,
                                pure=True, modal="", expected={"menu": False, "catalog_count": 0})
    _mark(cold_reader, qualifier, "C5-COLD-DEMO-ROUTES",
          "A fresh real closed Reader remains artwork-only and does not create an unseen Browse catalog.")
    _reader_identity(suite, api, cold_reader, landscape=False, spread=False, direction="rtl")
    suite.check(cold_reader, "The closed cold Reader preserves the strict full-viewport artwork oracle",
                cold_reader.get("pure_reader_oracle", {}).get("metrics", {}).get("exact_match") is True,
                cold_reader.get("pure_reader_oracle", {}).get("metrics"))
    _cold_catalog(suite, api, cold_reader)
    suite.persist_case(cold_reader)

    pw_browse = suite.capture(CASE_IDS[5], "reader", device="pw5", demo=True, offline=True,
                              data=Path(cold_reader["data_dir"]), expected_route="catalog", modal="",
                              replay=Replay(start=3500).intent("navigation", "open", "1", wait=250)
                              .intent("sources", "open", "mangadex", wait=1400))
    _mark(pw_browse, qualifier, "C5-COLD-DEMO-ROUTES",
          "After the actual Paperwhite Browse navigation and installed-source open, real persisted catalog cards and pagination become visible.")
    _browse_catalog(suite, api, pw_browse, cold_reader, large=False,
                    action="navigation/open/1 -> sources/open/mangadex")
    suite.persist_case(pw_browse)

    management = suite.capture(CASE_IDS[6], "sources", device="scribe", demo=True, offline=True,
                               modal="", expected={"catalog_count": 0, "source_filter": 0})
    _mark(management, qualifier, "C5-COLD-DEMO-ROUTES",
          "Fresh Scribe source management displays its actual saved sources and available-source controls without materializing a hidden catalog.")
    suite.check(management, "The cold management screenshot exposes real installed and available source models",
                management["state"].get("installed_count", 0) > 0
                and bool(management["state"].get("installed_sources"))
                and bool(management["state"].get("available_sources")),
                {key: management["state"].get(key) for key in ("installed_count", "installed_sources", "available_sources")})
    _cold_catalog(suite, api, management)
    suite.persist_case(management)

    sc_browse = suite.capture(CASE_IDS[7], "sources", device="scribe", demo=True, offline=True,
                              data=Path(management["data_dir"]), expected_route="catalog", modal="",
                              replay=Replay().intent("sources", "back", wait=1400))
    _mark(sc_browse, qualifier, "C5-COLD-DEMO-ROUTES",
          "Leaving the real Scribe management page opens Browse and materializes its actual profile catalog, with visible cards rather than a count-only claim.")
    _browse_catalog(suite, api, sc_browse, management, large=True, action="sources/back")
    suite.persist_case(sc_browse)
