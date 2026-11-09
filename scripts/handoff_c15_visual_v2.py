"""Extend the unchanged C15 qualifier with the fixed v2 native capture union."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import handoff_c9_search as c9
import handoff_c10_search as c10
import handoff_c15_artwork as artwork
import handoff_c15_visual as v1


ADDITIONAL_REFERENCE_CASE_IDS = ("Main--more", "sc-settings--tap-zones")
PW_FOUR_SOURCE_CASE_IDS = (
    "pw5--c10-four-sources--fixture-seed", "pw5--c10-four-source-draft",
    "pw5--c10-four-source-more", "pw5--c10-four-source-mangadex",
)
SC_NATIVE_TITLE_CASE_IDS = (
    "scribe--c10-native-titles--fixture-seed", "scribe--c10-native-title-groups",
    "scribe--c10-native-tile-open-000", "scribe--c10-native-tile-open-001",
    "scribe--c10-native-short-bookmark",
)
INDEPENDENT_SEARCH_CASE_IDS = tuple(
    f"{device}--independent-search--{suffix}"
    for device in ("pw5", "scribe")
    for suffix in ("fixture-seed", "early-result", "all-complete", "more-picker", "more-fourth-source")
)
CATALOG_DETAIL_CASE_IDS = tuple(
    f"{device}--http-search-success--{suffix}"
    for device in ("pw5", "scribe")
    for suffix in ("fixture-seed", "view-all", "view-all-pointer-open")
)
ARTWORK_CASE_IDS = ("sc-reader--layout", "sc-reader--light", "sc-reader--mode")
ARTWORK_PREREQUISITE_IDS = tuple(f"{identifier}--same-case-closed-reader-prerequisite"
                                for identifier in ARTWORK_CASE_IDS)
ARTWORK_CAPTURE_IDS = tuple(identifier for pair in zip(ARTWORK_PREREQUISITE_IDS, ARTWORK_CASE_IDS)
                            for identifier in pair)
REFERENCE_CASE_IDS = (*v1.REFERENCE_CASE_IDS, *ADDITIONAL_REFERENCE_CASE_IDS, *ARTWORK_CASE_IDS)
CASE_IDS = (*v1.CASE_IDS, *ADDITIONAL_REFERENCE_CASE_IDS, *PW_FOUR_SOURCE_CASE_IDS,
            *SC_NATIVE_TITLE_CASE_IDS, *INDEPENDENT_SEARCH_CASE_IDS, *CATALOG_DETAIL_CASE_IDS,
            *ARTWORK_CAPTURE_IDS)
PREREQUISITE_IDS = (
    *v1.PREREQUISITE_IDS, PW_FOUR_SOURCE_CASE_IDS[0], SC_NATIVE_TITLE_CASE_IDS[0],
    "pw5--independent-search--fixture-seed", "scribe--independent-search--fixture-seed",
    "pw5--http-search-success--fixture-seed", "scribe--http-search-success--fixture-seed",
    *ARTWORK_PREREQUISITE_IDS,
)
MANUAL_IDS = tuple(identifier for identifier in CASE_IDS if identifier not in PREREQUISITE_IDS)


def validate_plan(plan: dict[str, Any], original_plan: dict[str, Any]) -> None:
    v1.validate_plan(original_plan)
    if (len(CASE_IDS) != 72 or len(set(CASE_IDS)) != 72 or len(MANUAL_IDS) != 59
            or len(PREREQUISITE_IDS) != 13 or set(MANUAL_IDS) & set(PREREQUISITE_IDS)):
        raise ValueError("C15 v2 must retain exactly 72 actual captures, 59 manual frames and 13 prerequisites")
    if (plan.get("kind") != "static-c15-v2-affected-native-ui-qualification-plan"
            or plan.get("expected_actual_captures") != 72 or plan.get("maximum_actual_captures") != 72
            or plan.get("expected_manual_captures") != 59 or plan.get("expected_prerequisite_captures") != 13
            or plan.get("optional_captures") != 0 or plan.get("full_case_ids_modified") is not False):
        raise ValueError("The C15 v2 plan changed its fixed capture or partial-run boundary")
    for field, expected in (
        ("v1_case_ids", v1.CASE_IDS), ("additional_reference_case_ids", ADDITIONAL_REFERENCE_CASE_IDS),
        ("paperwhite_four_source_case_ids", PW_FOUR_SOURCE_CASE_IDS),
        ("scribe_native_title_case_ids", SC_NATIVE_TITLE_CASE_IDS),
        ("independent_search_case_ids", INDEPENDENT_SEARCH_CASE_IDS),
        ("catalog_detail_chain_case_ids", CATALOG_DETAIL_CASE_IDS),
        ("same_case_artwork_case_ids", ARTWORK_CASE_IDS),
        ("same_case_artwork_prerequisite_ids", ARTWORK_PREREQUISITE_IDS),
        ("expected_case_ids", CASE_IDS), ("manual_case_ids", MANUAL_IDS),
        ("prerequisite_case_ids", PREREQUISITE_IDS), ("reference_case_ids", REFERENCE_CASE_IDS),
    ):
        if plan.get(field) != list(expected):
            raise ValueError(f"The fixed C15 v2 case list changed: {field}")
    if tuple(c10.PW_IDS[:4]) != PW_FOUR_SOURCE_CASE_IDS or tuple(c10.SC_IDS) != SC_NATIVE_TITLE_CASE_IDS:
        raise ValueError("The reused C9/C10 native recipes changed their exact fresh IDs")
    if (tuple(artwork.CASE_IDS) != ARTWORK_CASE_IDS
            or tuple(artwork.PREREQUISITE_IDS) != ARTWORK_PREREQUISITE_IDS):
        raise ValueError("Each additional Scribe overlay must retain its own fixed closed-reader prerequisite")
    outcomes = plan.get("required_visual_outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        raise ValueError("C15 v2 must retain its additional fresh native visual review requirements")
    identifiers = [outcome.get("id") for outcome in outcomes if isinstance(outcome, dict)]
    if len(identifiers) != len(outcomes) or len(set(identifiers)) != len(identifiers):
        raise ValueError("The C15 v2 fresh visual review requirements contain duplicate or invalid IDs")
    for outcome in outcomes:
        if (not isinstance(outcome.get("requirement"), str) or not outcome["requirement"].strip()
                or not isinstance(outcome.get("case_ids"), list) or not outcome["case_ids"]
                or len(set(outcome["case_ids"])) != len(outcome["case_ids"])
                or not set(outcome["case_ids"]).issubset(MANUAL_IDS)):
            raise ValueError(f"The C15 v2 visual requirement is not bound to fresh manual frames: {outcome['id']}")


def mark_cases(suite: Any, plan: dict[str, Any], original_plan: dict[str, Any]) -> None:
    v1.mark_cases(suite, original_plan)
    for case in suite.cases:
        name = case.get("name")
        if name not in CASE_IDS or not case.get("screenshot"):
            continue
        case["c15_v2_qualification"] = True
        case["c15_v2_expected_role"] = "prerequisite" if name in PREREQUISITE_IDS else "manual"
        case["c15_v2_scope"] = "Fixed 72-frame qualification only; all original complete-round gates and IDs remain required."
        if name in MANUAL_IDS:
            case["independent_original_review"] = True
            case.setdefault("visible_ui_requirements", []).append(
                "Inspect this fresh native PNG and its real source identity; state values, model strings and private font or preferred-size diagnostics do not prove rendered glyph fidelity.")
        outcomes = [outcome for outcome in plan["required_visual_outcomes"] if name in outcome["case_ids"]]
        if outcomes:
            case["c15_v2_visual_outcomes"] = [{
                "id": outcome["id"], "requirement": outcome["requirement"],
                "review_status": "fresh_original_png_review_required", "state_is_not_font_proof": True,
            } for outcome in outcomes]
            case.setdefault("visible_ui_requirements", []).extend(outcome["requirement"] for outcome in outcomes)
        suite.persist_case(case)


def _failure(suite: Any, result: dict[str, Any], stage: str, error: BaseException) -> None:
    actual = f"{type(error).__name__}: {error}"
    result["errors"].append({"stage": stage, "error": actual})
    case = {
        "name": f"c15-v2-{stage}-completion", "passed": False, "manual_review_required": False,
        "c15_v2_qualification": True, "partial_run": True,
        "checks": [{"description": "The fixed native qualification stage completed without a product or tool failure",
                    "passed": False, "actual": actual}],
    }
    suite.cases.append(case)
    try:
        suite.persist_case(case)
    except Exception as report_error:
        result["errors"].append({"stage": f"{stage}-error-report", "error": f"{type(report_error).__name__}: {report_error}"})


def _stage(suite: Any, result: dict[str, Any], name: str, operation: Callable[[], Any]) -> Any:
    try:
        value = operation()
        result["stages"][name] = {"completed": True, "result": value}
        return value
    except Exception as error:
        _failure(suite, result, name, error)
        result["stages"][name] = {"completed": False, "error": f"{type(error).__name__}: {error}"}
        return None


def _reference(suite: Any, identifier: str) -> None:
    matches = [(board, entry) for board in suite.scope["pageStateMatrix"]
               for entry in [board["default"], *board["states"]] if entry["id"] == identifier]
    if len(matches) != 1:
        raise ValueError(f"The frozen full scope must contain exactly one additional reference: {identifier}")
    suite.reference_case(*matches[0])


def _pw_contracts(suite: Any, api: Any, case: dict[str, Any]) -> None:
    c10._mark(suite, case, True, "Current-source scope captions, shortcut identities, native pointer selection and the actual unsubmitted draft are freshly checked; no old original is reused.")
    if case.get("state", {}).get("modal") == "keyboard":
        c10._scope_pixels(suite, api, case)


def _catalog_detail(suite: Any, api: Any, device: str) -> None:
    """Reuse only the success seed, self-query ViewAll and real first-card hit."""
    name = f"{device}--http-search-success"
    with api.Fixture() as provider:
        data, _ = suite.seed_provider(name, provider, device)

        def view_all() -> Any:
            return (api.Replay().intent("search", "query", "fixture", wait=1400)
                    .intent("search", "all", "komga").intent("page", "turn", "999", wait=1200))

        catalog = suite.capture(f"{name}--view-all", "search", device=device, demo=False,
                                offline=False, data=data, replay=view_all(),
                                expected_route="catalog", modal="", capture_delay=1000)
        suite.check(catalog, "View-all opens real source results rather than a screenshot-only state",
                    catalog["state"].get("source_id") == "komga" and catalog["state"].get("catalog_count", 0) >= 20,
                    {key: catalog["state"].get(key) for key in ("source_id", "catalog_count", "catalog_has_more")})
        suite.expect(catalog, "source_needs_setup", False)
        rows = suite.database_rows(catalog, "SELECT document FROM sources WHERE id='komga'")
        verified = json.loads(rows[0][0]).get("options", {}).get("_connection_verified") if rows else None
        suite.check(catalog, "Successful real HTTP search persists actual server verification before View all", verified is True, verified)
        catalog.setdefault("visible_ui_requirements", []).append(
            "Settled real catalog cards, their actual title rows and folio remain visible without a contradictory setup gate.")
        suite.save_provider(catalog["name"], provider, catalog)
        first_book = next(iter(catalog["state"].get("visible_catalog", [])), {}).get("id")
        if catalog.get("passed") is not True or not isinstance(first_book, str) or not first_book:
            raise ValueError("The actual self-query catalog failed or has no first book; no downstream pointer is launched")
        hit = [70 if device == "scribe" else 52, 260 if device == "scribe" else 220]
        opened = suite.capture(f"{name}--view-all-pointer-open", "search", device=device, demo=False,
                               offline=False, data=data, replay=view_all().tap(*hit, wait=1200),
                               expected_route="detail", expected={"current_book": first_book}, modal="")
        actions = json.loads(Path(opened["actions"]).read_text(encoding="utf-8-sig"))
        pointers = [action for action in actions if action.get("section") == "pointer"]
        suite.check(opened, "The actual first catalog card opens through one native pointer tap without a semantic open substitute",
                    len(pointers) == 3 and sum(action.get("action") == "press" for action in pointers) == 1
                    and sum(action.get("action") == "release" for action in pointers) == 1
                    and not any(action.get("action") == "open" and action.get("section") in ("catalog", "search")
                                for action in actions), pointers)
        c9._retain(suite, api, opened, "c15-v2-catalog-pointer-origin", {
            "origin_case_id": catalog["name"], "original_sha256": c9._sha256(Path(catalog["screenshot"])),
            "actual_first_book_id": first_book, "native_hit": hit, "actual_pointer_actions": pointers,
            "recipe": "Unchanged full-harness success ViewAll query, folio and first-card native pointer route",
        })
        opened.setdefault("visible_ui_requirements", []).append(
            "Read the actual first catalog book's native detail title, author and chapter rows after the pointer opens that same book.")
        suite.save_provider(opened["name"], provider, opened)


def run(suite: Any, api: Any, negative_root: Path) -> dict[str, Any]:
    """Run the immutable v1 union first, then each fixed independent v2 stage."""
    result: dict[str, Any] = {"stages": {}, "errors": [], "completed": False, "full_case_ids_added": 0}
    result["v1"] = _stage(suite, result, "v1-union", lambda: v1.run(suite, api, negative_root))
    for identifier in ADDITIONAL_REFERENCE_CASE_IDS:
        _stage(suite, result, f"reference-{identifier}", lambda identifier=identifier: _reference(suite, identifier))
    _stage(suite, result, "pw-four-sources", lambda: c9._pw_four(c10.FreshNames(suite), api))
    for case in list(suite.cases):
        if case.get("name") in PW_FOUR_SOURCE_CASE_IDS and case.get("screenshot"):
            _stage(suite, result, f"pw-contract-{case['name']}", lambda case=case: _pw_contracts(suite, api, case))
    _stage(suite, result, "scribe-native-titles", lambda: c10._sc(suite, api, True))
    for device in ("pw5", "scribe"):
        _stage(suite, result, f"{device}-independent-search", lambda device=device: suite.independent_search(device))
    for device in ("pw5", "scribe"):
        _stage(suite, result, f"{device}-catalog-detail", lambda device=device: _catalog_detail(suite, api, device))
    result["same_case_artwork"] = _stage(suite, result, "same-case-artwork", lambda: artwork.run(suite, api))
    result["completed"] = not result["errors"] and all(stage["completed"] for stage in result["stages"].values())
    return result
