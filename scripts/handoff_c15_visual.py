"""Reuse the fixed native recipes for the C15 affected-UI qualification only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

import handoff_c5_ui as c5
import handoff_c11_search as c11
import handoff_c12_scope as c12


REFERENCE_CASE_IDS = (
    "pw-manga--more", "sc-manga--more", "pw-search--numbers",
    "pw-history--default", "pw-history--updates", "sc-history--default", "sc-history--updates",
    "pw-settings--display", "pw-settings--power", "sc-settings--default",
    "sc-browse--default", "pw-browse--updates", "pw-source--source-settings", "sc-browse--source-settings",
    "pw-reader--image", "sc-reader-portrait--image", "sc-reader--image",
)
EMPTY_HISTORY_CASE_IDS = (
    "pw5--c5-empty-history-body", "pw5--c5-empty-history-library",
    "scribe--c5-empty-history-body", "scribe--c5-empty-history-browse",
)
SCOPE_CASE_IDS = (
    "pw5--c12-dense-sources--fixture-seed", "pw5--c12-dense-source-typing",
    "pw5--c12-dense-shortcut-00", "pw5--c12-dense-shortcut-01-pressed",
    "scribe--c12-dense-sources--fixture-seed", "scribe--c12-dense-source-typing",
    "scribe--c12-dense-source-picker-last", "scribe--c12-dense-source-cancel",
    "scribe--c12-dense-source-select-last", "scribe--c12-dense-shortcut-00",
    "scribe--c12-dense-shortcut-01", "scribe--c12-dense-shortcut-02",
)
AUTHOR_BOUNDARY_CASE_IDS = (
    "scribe--c11-author-boundary--fixture-seed", "scribe--c11-title-groups",
    "scribe--c11-favorite-000--prerequisite", "scribe--c11-short-bookmark",
    "scribe--c11-author-tail-open-005", "scribe--c11-next-view-all",
)
CASE_IDS = (*REFERENCE_CASE_IDS, *EMPTY_HISTORY_CASE_IDS, *SCOPE_CASE_IDS, *AUTHOR_BOUNDARY_CASE_IDS)
PREREQUISITE_IDS = (
    "pw5--c12-dense-sources--fixture-seed", "scribe--c12-dense-sources--fixture-seed",
    "scribe--c11-author-boundary--fixture-seed", "scribe--c11-favorite-000--prerequisite",
)
MANUAL_IDS = tuple(identifier for identifier in CASE_IDS if identifier not in PREREQUISITE_IDS)
VISUAL_OUTCOME_CASE_IDS = {
    "detail-more-weight": ("pw-manga--more", "sc-manga--more"),
    "numeric-corner-brackets": ("pw-search--numbers",),
    "history-count-tab-allocation": (
        "pw-history--default", "pw-history--updates", "sc-history--default", "sc-history--updates",
        "pw5--c5-empty-history-body", "scribe--c5-empty-history-body",
    ),
    "paperwhite-settings-back-allocation": ("pw-settings--display", "pw-settings--power"),
    "quality-strong-label": ("pw-settings--display", "sc-settings--default"),
    "scribe-source-switch-allocation": ("sc-browse--default",),
    "paperwhite-source-update-subtitle": ("pw-browse--updates",),
    "source-settings-heading": ("pw-source--source-settings", "sc-browse--source-settings"),
    "reader-refresh-labels": ("pw-reader--image", "sc-reader-portrait--image", "sc-reader--image"),
}


def validate_plan(plan: dict[str, Any]) -> None:
    """Keep the qualification union and its roles independent of the full IDs."""
    if (len(CASE_IDS) != 39 or len(set(CASE_IDS)) != 39 or len(MANUAL_IDS) != 35
            or len(PREREQUISITE_IDS) != 4 or set(MANUAL_IDS) & set(PREREQUISITE_IDS)):
        raise ValueError("C15 must retain exactly 39 fixed captures, 35 manual frames and four prerequisites")
    if (plan.get("kind") != "static-c15-affected-native-ui-qualification-plan"
            or plan.get("expected_actual_captures") != 39
            or plan.get("expected_manual_captures") != 35
            or plan.get("expected_prerequisite_captures") != 4
            or plan.get("full_case_ids_modified") is not False):
        raise ValueError("The frozen C15 qualification plan does not retain its fixed partial-run boundary")
    for field, expected in (
        ("reference_case_ids", REFERENCE_CASE_IDS), ("empty_history_case_ids", EMPTY_HISTORY_CASE_IDS),
        ("scope_qualification_case_ids", SCOPE_CASE_IDS), ("author_boundary_case_ids", AUTHOR_BOUNDARY_CASE_IDS),
    ):
        if plan.get(field) != list(expected):
            raise ValueError(f"The frozen C15 qualification changed its exact case list: {field}")
    if tuple(c5.CASE_IDS[-4:]) != EMPTY_HISTORY_CASE_IDS or tuple(c12.CASE_IDS) != SCOPE_CASE_IDS:
        raise ValueError("The reused C5 or C12 recipe no longer exposes its unchanged qualification IDs")
    if (tuple(c11.CASE_IDS) != AUTHOR_BOUNDARY_CASE_IDS
            or tuple(c11.MANUAL_IDS) != tuple(identifier for identifier in AUTHOR_BOUNDARY_CASE_IDS
                                             if identifier not in PREREQUISITE_IDS)):
        raise ValueError("The reused C11 recipe no longer exposes its unchanged capture roles")
    outcomes = plan.get("required_visual_outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != 9:
        raise ValueError("C15 must retain all nine fresh-original-PNG visual review outcomes")
    ids = [outcome.get("id") for outcome in outcomes if isinstance(outcome, dict)]
    if len(ids) != 9 or len(set(ids)) != 9 or set(ids) != set(VISUAL_OUTCOME_CASE_IDS):
        raise ValueError("The C15 visual outcomes contain a missing, duplicate or unexpected identifier")
    for outcome in outcomes:
        if (outcome.get("case_ids") != list(VISUAL_OUTCOME_CASE_IDS[outcome["id"]])
                or not isinstance(outcome.get("requirement"), str) or not outcome["requirement"].strip()):
            raise ValueError(f"The C15 fresh-frame visual review mapping changed: {outcome['id']}")


def mark_cases(suite: Any, plan: dict[str, Any]) -> None:
    """Add review instructions without changing an inherited assertion or role."""
    for case in suite.cases:
        name = case.get("name")
        if name not in CASE_IDS or not case.get("screenshot"):
            continue
        case["c15_qualification"] = True
        case["c15_expected_role"] = "prerequisite" if name in PREREQUISITE_IDS else "manual"
        case["c15_qualification_only_alias"] = name in (*SCOPE_CASE_IDS, *AUTHOR_BOUNDARY_CASE_IDS)
        case["c15_scope"] = "Fixed affected-UI qualification only; every original full-suite case and gate remains required."
        if name in MANUAL_IDS:
            case["independent_original_review"] = True
            case.setdefault("visible_ui_requirements", []).append(
                "Inspect this fresh unmodified native PNG; model strings and diagnostic font values do not prove rendered glyph fidelity.")
        outcomes = [outcome for outcome in plan["required_visual_outcomes"] if name in outcome["case_ids"]]
        if outcomes:
            case["c15_visual_outcomes"] = [{
                "id": outcome["id"], "requirement": outcome["requirement"],
                "review_status": "fresh_original_png_review_required", "state_is_not_font_proof": True,
            } for outcome in outcomes]
            case.setdefault("visible_ui_requirements", []).extend(outcome["requirement"] for outcome in outcomes)
        suite.persist_case(case)


def _reference_frames(suite: Any) -> None:
    entries: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for board in suite.scope["pageStateMatrix"]:
        for entry in [board["default"], *board["states"]]:
            if entry["id"] in REFERENCE_CASE_IDS:
                if entry["id"] in entries:
                    raise ValueError(f"The frozen full reference scope repeats an affected entry: {entry['id']}")
                entries[entry["id"]] = (board, entry)
    if set(entries) != set(REFERENCE_CASE_IDS):
        raise ValueError("The frozen full reference scope lacks one of the 17 exact C15 reference entries")
    for identifier in REFERENCE_CASE_IDS:
        suite.reference_case(*entries[identifier])


def _empty_history_frames(suite: Any, api: Any) -> None:
    """Reuse only the final four-state loop of C5 run_license_history."""
    Replay = api.Replay
    for device in ("pw5", "scribe"):
        large = device == "scribe"
        empty = suite.capture(f"{device}--c5-empty-history-body", "history", device=device,
                              demo=False, offline=True, modal="",
                              expected={"history_count": 0, "history_filter": 0,
                                        "visible_history": [], "page": 1, "page_count": 1})
        c5._mark(empty, True, "C4-C5-G18", "Empty-history next actions are visible inside the body, independently of the global dock.")
        with Image.open(empty["screenshot"]) as original:
            width, height = original.size
        status, navigation, pager = (36, 80, 68) if large else (28, 72, 52)
        content_top, content_height = (177.5, 202) if large else (124.5, 178)
        body_height = height - status - navigation
        content_rect_height = max(0, body_height - content_top - pager)
        empty_top = max(0, (content_rect_height - content_height) / 2)
        top = status + content_top + empty_top + (146 if large else 130)
        controls = c5._observe_buttons(suite, api, empty, top=top, height=56 if large else 48,
                                      left=36 if large else 22, right=width - (36 if large else 10),
                                      styles=("primary-filled", "outlined"))
        chosen = controls[1 if large else 0]
        suffix, route = ("browse", "catalog") if large else ("library", "library")
        destination = suite.capture(f"{device}--c5-empty-history-{suffix}", "history", device=device,
                                    demo=False, offline=True, data=Path(empty["data_dir"]),
                                    replay=Replay().tap(*chosen["hit"], wait=700),
                                    expected_route=route, modal="", expected={"history_count": 0, "page": 1})
        c5._mark(destination, True, "C4-C5-G18", "A real click on the observed empty-body action opens its actual navigation destination.")
        destination["c5_navigation_origin"] = {
            "case_id": empty["name"], "original_screenshot": empty["screenshot"],
            "original_sha256": c5._sha256(Path(empty["screenshot"])),
            "button": api.ui_label("browse_sources" if large else "return_library"),
            "rectangle": chosen["rectangle"], "hit": chosen["hit"], "actual_destination": route,
            "input": "Native pointer press and release; no semantic navigation action is substituted",
        }
        c5._retain(api, destination, "body-navigation", destination["c5_navigation_origin"])
        suite.persist_case(destination)


def run(suite: Any, api: Any, negative_root: Path) -> dict[str, Any]:
    """Capture exactly 17 reference, four C5, twelve C12 and six C11 frames."""
    _reference_frames(suite)
    _empty_history_frames(suite, api)
    c12.run(suite, api)
    author_boundary = c11.run_author_boundary(suite, api, negative_root)
    return {
        "completed": True, "reference_case_ids": list(REFERENCE_CASE_IDS),
        "empty_history_case_ids": list(EMPTY_HISTORY_CASE_IDS), "scope_case_ids": list(SCOPE_CASE_IDS),
        "author_boundary": author_boundary, "new_full_captures": 0,
        "review_boundary": "All nine visual outcomes require fresh original native PNG review; no state value establishes font fidelity.",
    }
