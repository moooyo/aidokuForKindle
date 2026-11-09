"""Bounded native C5 evidence for real license viewing and empty-history actions."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from PIL import Image


CASE_IDS = (
    "pw5--c5-about-license-entry",
    "pw5--c5-license-first",
    "pw5--c5-license-last",
    "scribe--c5-about-license-entry",
    "scribe--c5-license-first",
    "scribe--c5-license-last",
    "scribe--c5-license-return",
    "pw5--c5-empty-history-body",
    "pw5--c5-empty-history-library",
    "scribe--c5-empty-history-body",
    "scribe--c5-empty-history-browse",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
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


def _ink_runs(gray: Any, y: int, left: int, right: int) -> list[tuple[int, int]]:
    runs = []
    start = None
    for x in range(left, right):
        dark = gray.getpixel((x, y)) < 128
        if dark and start is None:
            start = x
        elif not dark and start is not None:
            if x - start >= 24:
                runs.append((start, x))
            start = None
    if start is not None and right - start >= 24:
        runs.append((start, right))
    return runs


def _observe_buttons(
    suite: Any, api: Any, case: dict[str, Any], *, top: float, height: int,
    left: int, right: int, styles: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Locate real filled or outlined surfaces inside one known native action row."""
    with Image.open(case["screenshot"]) as original:
        gray = original.convert("L")
        candidates = []
        for y in range(math.floor(top), math.floor(top) + 3):
            if 0 <= y < gray.height and 0 <= left < right <= gray.width:
                runs = _ink_runs(gray, y, left, right)
                if len(runs) == len(styles):
                    candidates.append((y, runs))
        suite.check(case, "The native action row exposes exactly its real button surfaces",
                    bool(candidates), {"row_top": top, "bounds": [left, right], "expected": len(styles)})
        if not candidates:
            suite.persist_case(case)
            raise ValueError(f"The native action surfaces could not be located: {case['name']}")
        scan_y, runs = max(candidates, key=lambda item: sum(end - start for start, end in item[1]))
        interior = range(math.ceil(top + 7), math.floor(top + height - 7))

        def vertical_edge(x: int) -> bool:
            return (0 <= x < gray.width and bool(interior)
                    and sum(gray.getpixel((x, y)) < 128 for y in interior) / len(interior) >= 0.75)

        controls = []
        for style, (start, end) in zip(styles, runs):
            left_edges = [x for x in range(max(left, start - 6), min(right, start + 7)) if vertical_edge(x)]
            right_edges = [x for x in range(max(left, end - 7), min(right, end + 6)) if vertical_edge(x)]
            suite.check(case, "The observed action has real left and right surface edges",
                        bool(left_edges) and bool(right_edges), {"top_rule": [start, end, scan_y], "style": style})
            if not left_edges or not right_edges:
                suite.persist_case(case)
                raise ValueError(f"The native action boundary is incomplete: {case['name']}")
            x0, x1 = min(left_edges), max(right_edges) + 1
            y0, y1 = math.floor(top), math.ceil(top + height)
            controls.append({
                "style": style, "rectangle": [x0, y0, x1 - x0, y1 - y0],
                "hit": [(x0 + x1) / 2, top + height / 2],
                "observed_top_rule": [start, end, scan_y],
            })
    for control in controls:
        if control["style"] == "outlined":
            suite.visible_outline(case, tuple(control["rectangle"]))
    observation = {
        "original_screenshot": str(case["screenshot"]),
        "original_sha256": _sha256(Path(case["screenshot"])),
        "method": "Native top-rule and vertical-edge observation inside the source-defined action row",
        "controls": controls,
    }
    case["c5_native_controls"] = observation
    _retain(api, case, "native-controls", observation)
    suite.persist_case(case)
    return controls


def _check_license_page(
    suite: Any, api: Any, case: dict[str, Any], *, source: Path, raw: bytes,
    text: str, final: bool, first: dict[str, Any] | None = None,
) -> None:
    state = case["state"]
    body = state.get("panel_text")
    page, count = state.get("panel_page"), state.get("panel_page_count")
    suite.expect(case, "modal", "summary")
    suite.expect(case, "modal_context", "license")
    suite.expect(case, "summary_is_book", False)
    suite.check(case, "The real license viewer exposes a nonempty page and a multi-page folio",
                isinstance(body, str) and bool(body) and type(page) is int
                and type(count) is int and count > 1 and 1 <= page <= count,
                {"page": page, "count": count, "text_characters": len(body) if isinstance(body, str) else None})
    if final:
        suite.check(case, "The actual final license page reaches the bundled document's exact suffix",
                    isinstance(body, str) and bool(body) and text.endswith(body) and page == count,
                    {"page": page, "count": count, "suffix_matches": isinstance(body, str) and text.endswith(body)})
        if first is not None:
            suite.check(case, "The first and last native pages retain the same actual license folio",
                        count == first["state"].get("panel_page_count")
                        and body != first["state"].get("panel_text"), count)
    else:
        suite.check(case, "The actual first license page starts at the bundled document's exact prefix",
                    isinstance(body, str) and bool(body) and text.startswith(body) and page == 1,
                    {"page": page, "prefix_matches": isinstance(body, str) and text.startswith(body)})
    evidence = {
        "source": str(source), "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw), "source_characters": len(text),
        "observed_page": page, "observed_page_count": count,
        "observed_text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest() if isinstance(body, str) else None,
        "boundary": "suffix" if final else "prefix",
        "full_text_contract": "Root-owned source-bound license_tests cover complete concatenation; these captures inspect the actual first and final pages.",
    }
    case["c5_license_evidence"] = evidence
    case.setdefault("evidence_hashes", {})["c5:bundled-license"] = {
        "path": str(source), "sha256": evidence["source_sha256"],
    }
    _retain(api, case, "license-boundary", evidence)
    suite.persist_case(case)


def run_license_history(
    suite: Any, api: Any, qualifier: bool = False, *, profiles: tuple[str, ...] = ("pw5", "scribe"),
) -> None:
    """Capture the same eleven actual UI states in the full suite and qualifier."""
    Replay = api.Replay
    source = (Path(suite.args.source_root) / "assets" / "licenses" / "GPL-3.0-or-later.txt").resolve(strict=True)
    raw = source.read_bytes()
    license_text = raw.decode("utf-8")

    for device in profiles:
        large = device == "scribe"
        about = suite.capture(f"{device}--c5-about-license-entry", "settings", device=device,
                              demo=False, offline=True,
                              replay=Replay().intent("settings", "open", "about"), modal="",
                              expected={"settings_section": "about"})
        _mark(about, qualifier, "C4-C5-G02", "The real About license row has an enabled outlined View control and the actual GPL identity.")
        rows = [item for item in about["state"].get("settings_details", []) if item.get("id") == "license"]
        suite.check(about, "About exposes exactly one real enabled GPL license entry",
                    len(rows) == 1 and rows[0].get("enabled") is True
                    and rows[0].get("value") == "GPL-3.0-or-later"
                    and rows[0].get("title") == api.ui_label("license"), rows)
        with Image.open(about["screenshot"]) as original:
            width = original.width
        row_top, row_height = (229.5 + 2 * 60, 60) if large else (130 + 2 * 54, 54)
        button_top = row_top + (row_height - 42) / 2
        right = width - (36 if large else 22)
        view = _observe_buttons(suite, api, about, top=button_top, height=42,
                                left=max(0, right - 180), right=right, styles=("outlined",))[0]
        data = Path(about["data_dir"])

        def open_view() -> Any:
            return Replay().intent("settings", "open", "about").tap(*view["hit"], wait=600)

        first = suite.capture(f"{device}--c5-license-first", "settings", device=device,
                              demo=False, offline=True, data=data, replay=open_view(),
                              modal="summary", context="license", expected={"summary_is_book": False})
        _mark(first, qualifier, "C4-C5-G02", "The actual About View button opens real license text without book tags or book metadata.")
        _check_license_page(suite, api, first, source=source, raw=raw, text=license_text, final=False)
        last = suite.capture(f"{device}--c5-license-last", "settings", device=device,
                             demo=False, offline=True, data=data,
                             replay=open_view().intent("panel", "page", "99999"),
                             modal="summary", context="license", expected={"summary_is_book": False})
        _mark(last, qualifier, "C4-C5-G02", "The real final license folio and document ending remain visible inside the native viewer.")
        _check_license_page(suite, api, last, source=source, raw=raw, text=license_text, final=True, first=first)
        if large:
            returned = suite.capture("scribe--c5-license-return", "settings", device=device,
                                     demo=False, offline=True, data=data,
                                     replay=open_view().intent("panel", "close"), modal="", context="",
                                     expected={"settings_section": "about"})
            _mark(returned, qualifier, "C4-C5-G02", "Closing the actual license viewer returns to About and preserves the enabled license entry.")
            suite.check(returned, "Closing returns to the real enabled license row",
                        any(item.get("id") == "license" and item.get("enabled") is True
                            for item in returned["state"].get("settings_details", [])),
                        returned["state"].get("settings_details"))
            suite.persist_case(returned)

    for device in profiles:
        large = device == "scribe"
        empty = suite.capture(f"{device}--c5-empty-history-body", "history", device=device,
                              demo=False, offline=True, modal="",
                              expected={"history_count": 0, "history_filter": 0,
                                        "visible_history": [], "page": 1, "page_count": 1})
        _mark(empty, qualifier, "C4-C5-G18", "Empty-history next actions are visible inside the body, independently of the global dock.")
        with Image.open(empty["screenshot"]) as original:
            width, height = original.size
        status, navigation, pager = (36, 80, 68) if large else (28, 72, 52)
        content_top, content_height = (177.5, 202) if large else (124.5, 178)
        body_height = height - status - navigation
        content_rect_height = max(0, body_height - content_top - pager)
        empty_top = max(0, (content_rect_height - content_height) / 2)
        top = status + content_top + empty_top + (146 if large else 130)
        controls = _observe_buttons(suite, api, empty, top=top, height=56 if large else 48,
                                    left=36 if large else 22, right=width - (36 if large else 10),
                                    styles=("primary-filled", "outlined"))
        chosen = controls[1 if large else 0]
        suffix, route = ("browse", "catalog") if large else ("library", "library")
        destination = suite.capture(f"{device}--c5-empty-history-{suffix}", "history", device=device,
                                    demo=False, offline=True, data=Path(empty["data_dir"]),
                                    replay=Replay().tap(*chosen["hit"], wait=700),
                                    expected_route=route, modal="", expected={"history_count": 0, "page": 1})
        _mark(destination, qualifier, "C4-C5-G18", "A real click on the observed empty-body action opens its actual navigation destination.")
        destination["c5_navigation_origin"] = {
            "case_id": empty["name"], "original_screenshot": empty["screenshot"],
            "original_sha256": _sha256(Path(empty["screenshot"])),
            "button": api.ui_label("browse_sources" if large else "return_library"),
            "rectangle": chosen["rectangle"], "hit": chosen["hit"], "actual_destination": route,
            "input": "Native pointer press and release; no semantic navigation action is substituted",
        }
        _retain(api, destination, "body-navigation", destination["c5_navigation_origin"])
        suite.persist_case(destination)
