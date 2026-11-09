"""Fresh bounded C10 search layout checks using only actual native frame ink."""

from __future__ import annotations

from contextlib import ExitStack
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image

import handoff_c9_search as c9


PW_IDS = tuple(name.replace("--c9-", "--c10-") for name in c9.CASE_IDS[:10])
SC_IDS = (
    "scribe--c10-native-titles--fixture-seed",
    "scribe--c10-native-title-groups",
    "scribe--c10-native-tile-open-000",
    "scribe--c10-native-tile-open-001",
    "scribe--c10-native-short-bookmark",
)
CASE_IDS = (*PW_IDS, *SC_IDS)


class FreshNames:
    """Reuse the unchanged C9 PW actions while creating new C10 captures only."""

    def __init__(self, suite: Any):
        self.suite = suite

    def __getattr__(self, name: str) -> Any:
        return getattr(self.suite, name)

    def capture(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.suite.capture(name.replace("--c9-", "--c10-"), *args, **kwargs)

    def seed_provider(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return self.suite.seed_provider(name.replace("--c9-", "--c10-"), *args, **kwargs)


def _mark(suite: Any, case: dict[str, Any], qualifier: bool, requirement: str) -> None:
    case["c10_qualifier"] = bool(qualifier)
    case["c10_contracts"] = ["C10-NATIVE-SEARCH-LAYOUT", "C10-SINGLE-RUN-SCOPE"]
    case["independent_original_review"] = not bool(case.get("prerequisite"))
    case.setdefault("visible_ui_requirements", []).append(requirement)
    suite.persist_case(case)


def _caption(book: dict[str, Any]) -> str:
    author, subtitle = book.get("author", ""), book.get("subtitle", "")
    return subtitle if not author else author + (" · " + subtitle if subtitle else "")


def _bands(gray: Any, rectangle: list[float], threshold: int = 160) -> list[list[int]]:
    left, top, width, height = rectangle
    x0, x1 = math.ceil(left), math.floor(left + width)
    y0, y1 = math.ceil(top), math.floor(top + height)
    if not (0 <= x0 < x1 <= gray.width and 0 <= y0 < y1 <= gray.height):
        raise ValueError("The actual native ink region lies outside the original PNG")
    bands = []
    current = []
    for y in range(y0, y1):
        points = [(x, y) for x in range(x0, x1) if gray.getpixel((x, y)) < threshold]
        if points:
            current.extend(points)
        elif current:
            bands.append([min(x for x, _ in current), min(v for _, v in current),
                          max(x for x, _ in current) + 1, max(v for _, v in current) + 1])
            current = []
    if current:
        bands.append([min(x for x, _ in current), min(v for _, v in current),
                      max(x for x, _ in current) + 1, max(v for _, v in current) + 1])
    return bands


def _geometry(case: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    groups = case["state"].get("search_groups", [])
    if ([group.get("id") for group in groups] != ["slow", "zz-next"]
            or len(groups[0].get("books", [])) != 6 or len(groups[1].get("books", [])) != 2
            or any(group.get("pending") is not False or group.get("error") is not False for group in groups)):
        raise ValueError("Two actual independent provider groups with six and two visible records are required")
    with Image.open(case["screenshot"]) as original:
        width = (original.width - 72 - 22 * 5) / 6
    controls = []
    for group_index, group in enumerate(groups):
        group_top = 36 + 159 + 50 + group_index * 286
        for index, book in enumerate(group["books"]):
            left = 36 + index * (width + 22)
            controls.append({"group_id": group["id"], "group_index": group_index, "index": index,
                             "book": book, "left": left, "width": width,
                             "cover_top": group_top + 48, "group_top": group_top,
                             "next_group_top": group_top + 286,
                             "hit": [left + min(63, width / 2), group_top + 48 + 83]})
    return controls, width


def _scope_pixels(suite: Any, api: Any, case: dict[str, Any]) -> None:
    scope = case["state"].get("search_scope")
    sources = case["state"].get("search_sources", [])
    selected = next((source for source in sources if source.get("id") == scope), None)
    hidden = scope not in ("all", "library") and scope not in [source.get("id") for source in sources[:2]]
    if not hidden:
        return
    if selected is None:
        raise ValueError("The actual hidden scope has no configured full identity")
    with Image.open(case["screenshot"]) as original:
        gray = original.convert("L")
        bounds = c9._ink_bounds(gray, [22, 165, gray.width - 44, 20])
        outside = [(x, y) for y in range(160, 205) for x in range(gray.width)
                   if not (22 <= x < gray.width - 22 and 165 <= y < 185)]
        escaped_ink = sum(gray.getpixel(point) < 160 for point in outside)
    suite.check(case, "The real hidden-scope pending caption occupies its single native viewport without a second overflowing run",
                bounds is not None and bounds[0] >= 22 and bounds[2] <= gray.width - 22
                and bounds[1] >= 165 and bounds[3] <= 185 and escaped_ink == 0,
                {"inside_ink_bounds": bounds, "outside_ink_pixels": escaped_ink})
    caption = api.ui_label("pending_search") + " 1 " + api.ui_label("sources_unit") + " · " + selected["name"]
    c9._retain(suite, api, case, "c10-pending-caption", {
        "source_id": scope, "full_source_name": selected["name"], "compact_source_name": selected.get("display_name"),
        "expected_text_for_independent_original_review": caption, "actual_ink_bounds": bounds,
        "outside_native_viewport_ink_pixels": escaped_ink,
        "native_viewport": [22, 165, gray.width - 44, 20],
        "expanded_native_guard": [0, 160, gray.width, 45],
        "binding_contract": "Production binds prefix and selected identity to one ScopeLabel native Text run; no separate measured-prefix placement is synthesized.",
        "model_text_is_not_rendered_text_proof": True})
    suite.persist_case(case)


def _infer_h2(suite: Any, api: Any, short: dict[str, Any]) -> dict[str, Any]:
    controls, width = _geometry(short)
    primary = [control for control in controls if control["group_index"] == 0]
    anchor = primary[0]
    same_caption = _caption(anchor["book"])
    result: dict[str, Any] = {"classification": "inferred_from_actual_frame", "exact_native_preferred_height": False,
                             "anchor_case_id": short["name"], "same_actual_caption": same_caption,
                             "anchor_book_id": anchor["book"].get("id"), "comparisons": []}
    suite.check(short, "The real HTTP short title supplies an actual same-frame single-line author anchor",
                anchor["book"].get("title") == "Short 000" and anchor["book"].get("in_library") is True
                and bool(same_caption), anchor["book"])
    with Image.open(short["screenshot"]) as original:
        gray = original.convert("L")
        anchor_bands = _bands(gray, [anchor["left"], anchor["cover_top"] + 197, width, 24])
        assigned_anchor = _bands(gray, [anchor["left"], anchor["cover_top"] + 200, width, 18])
        short_title_width = width - (22 if anchor["book"].get("in_library") else 0)
        short_title_bands = _bands(gray, [anchor["left"], anchor["cover_top"] + 172, short_title_width, 26], 96)
        suite.check(short, "The short author anchor is measured in an expanded native guard without cropping its visible glyphs",
                    len(anchor_bands) == 1 and anchor_bands == assigned_anchor, anchor_bands)
        suite.check(short, "The actual single-line short title supplies an independent native glyph-height anchor",
                    len(short_title_bands) == 1, short_title_bands)
        if len(anchor_bands) == 1:
            result["short_author_native_ink_height"] = anchor_bands[0][3] - anchor_bands[0][1]
            result["short_author_expanded_guard"] = [anchor["left"], anchor["cover_top"] + 197, width, 24]
        if len(short_title_bands) == 1:
            result["short_title_native_ink_height"] = short_title_bands[0][3] - short_title_bands[0][1]
        matches = [control for control in primary[1:] if _caption(control["book"]) == same_caption
                   and control["index"] in (2, 4)]
        # These columns are empty in the real two-record next provider, and are
        # outside its short heading and right-side View-all action. The scan
        # extends beyond the allowed card boundary instead of clamping H2.
        values = []
        for control in matches:
            actual = _bands(gray, [control["left"], control["cover_top"] + 169, width, 171])
            inferred = (actual[-1][1] - anchor_bands[0][1] + 28) if len(actual) == 3 and len(anchor_bands) == 1 else None
            record = {"book_id": control["book"].get("id"), "same_actual_caption": _caption(control["book"]),
                      "actual_native_ink_bands": actual, "inferred_H2": inferred}
            result["comparisons"].append(record)
            suite.check(short, "The long comparison contains two real title rows and the identical native author string",
                        len(actual) == 3 and _caption(control["book"]) == same_caption and inferred is not None, record)
            if inferred is not None:
                values.append(inferred)
    valid = (len(matches) == 2 and len(values) == 2 and len(set(values)) == 1 and values[0] > 0
             and len(anchor_bands) == 1 and anchor_bands == assigned_anchor and len(short_title_bands) == 1)
    suite.check(short, "The actual same-frame glyph origins determine one H2 without a default or font substitution", valid, result)
    if valid:
        h2 = values[0]
        result.update({"H2": h2, "card_height": 190 + h2,
                       "native_P_interval": {"lower_exclusive": (h2 - 1) / 2, "upper_inclusive": h2 / 2},
                       "inference_equation": "H2 = long_author_ink_start - short_author_ink_start + 28"})
        suite.check(short, "Inferred H2, native P interval and actual card height satisfy the un-clamped production bounds",
                    h2 <= 48 and h2 / 2 <= 24 and 190 + h2 <= 238, result)
    else:
        result["inference_failed"] = True
    c9._retain(suite, api, short, "c10-inferred-native-line-box", result)
    return result


def _assess_sc(suite: Any, api: Any, case: dict[str, Any], inference: dict[str, Any], *, short_first: bool) -> None:
    controls, _ = _geometry(case)
    h2 = inference.get("H2")
    suite.check(case, "Native layout assessment uses a valid actual-frame H2 inference rather than a hardcoded clamp",
                type(h2) is int and h2 > 0 and not inference.get("inference_failed"), inference)
    if type(h2) is not int or h2 <= 0:
        suite.persist_case(case)
        return
    suite.check(case, "Every inferred native title line box remains within P24/H248/card238",
                h2 <= 48 and h2 / 2 <= 24 and 190 + h2 <= 238, inference)
    records = []
    captions: dict[tuple[str, int], list[dict[str, Any]]] = {}
    with Image.open(case["screenshot"]) as original:
        gray = original.convert("L")
        for control in controls:
            book = control["book"]
            short = short_first and control["group_index"] == 0 and control["index"] == 0
            title_width = control["width"] - (22 if book.get("in_library") else 0)
            title_top = control["cover_top"] + (174 if short else 169)
            title_height = 22 if short else h2
            author_top = control["cover_top"] + (200 if short else 172 + h2)
            title_bands = _bands(gray, [control["left"], title_top, title_width, title_height], 96)
            author_bands = _bands(gray, [control["left"], author_top, control["width"], 18])
            title_guard = _bands(gray, [control["left"], title_top - 2, title_width, title_height + 4], 96)
            author_guard = _bands(gray, [control["left"], author_top - 2, control["width"], 22])
            card_height = 218 if short else 190 + h2
            record = {"book_id": book.get("id"), "title": book.get("title"), "actual_caption": _caption(book),
                      "group_id": control["group_id"], "native_title_ink_rows": title_bands,
                      "native_author_ink_rows": author_bands, "expanded_title_ink_rows": title_guard,
                      "expanded_author_ink_rows": author_guard,
                      "title_rectangle": [control["left"], title_top, title_width, title_height],
                      "author_rectangle": [control["left"], author_top, control["width"], 18],
                      "inferred_H2": h2, "inferred_card_height": card_height,
                      "next_real_group_top": control["next_group_top"], "classification": "inferred_from_actual_frame"}
            suite.check(case, "The actual title exposes complete native glyph rows in its assigned line boxes",
                        len(title_bands) == (1 if short else 2) and title_bands == title_guard
                        and all(row[3] - row[1] >= inference.get("short_title_native_ink_height", 0) - 1
                                for row in title_bands), record)
            suite.check(case, "The actual author remains a complete separate native glyph row below the title",
                        len(author_bands) == 1 and bool(title_bands)
                        and author_bands == author_guard and title_bands[-1][3] <= author_top, record)
            if _caption(book) == inference.get("same_actual_caption"):
                suite.check(case, "The identical author caption keeps the full glyph height of the expanded short-frame anchor",
                            len(author_guard) == 1
                            and author_guard[0][3] - author_guard[0][1] == inference.get("short_author_native_ink_height"), record)
            suite.check(case, "Actual card and author bounds do not enter the next real provider group",
                        control["cover_top"] + card_height <= control["next_group_top"]
                        and author_top + 18 <= control["next_group_top"], record)
            if author_bands:
                captions.setdefault((_caption(book), 218 if short else card_height), []).append(record)
            if short:
                marker = [control["left"] + control["width"] - 16, control["cover_top"] + 177, 16, 16]
                ink = c9._ink_bounds(gray, marker)
                suite.check(case, "The actual short title and in-library bookmark do not collide",
                            book.get("in_library") is True and ink is not None and bool(title_bands)
                            and title_bands[0][2] < marker[0], {"marker": marker, "marker_ink": ink, "title_rows": title_bands})
            records.append(record)
        for equivalents in captions.values():
            if len(equivalents) < 2:
                continue
            heights = [record["native_author_ink_rows"][0][3] - record["native_author_ink_rows"][0][1] for record in equivalents]
            suite.check(case, "Identical actual author strings retain their complete glyph height across columns without next-group masking",
                        len(set(heights)) == 1, {"heights": heights, "records": equivalents})
        next_header = c9._ink_bounds(gray, [36, 531, 650, 48])
        suite.check(case, "The next visible group is a real distinct provider header at its native boundary",
                    next_header is not None and case["state"]["search_groups"][1].get("title") == "Actual next fixture",
                    {"header_ink": next_header, "actual_group": case["state"]["search_groups"][1]})
    c9._retain(suite, api, case, "c10-native-title-pixels", {"metric_inference": inference, "controls": records,
                "original_screenshot": case["screenshot"], "original_sha256": c9._sha256(Path(case["screenshot"])),
                "title_and_suffix_text_requires_independent_original_review": True})
    suite.persist_case(case)


def _sc(suite: Any, api: Any, qualifier: bool) -> None:
    with ExitStack() as stack:
        slow = stack.enter_context(api.Fixture(search_delay=4.0))
        next_provider = stack.enter_context(api.Fixture())
        slow.series_count, next_provider.series_count = 6, 2
        data, database = suite.seed_provider("scribe--c10-native-titles", slow, "scribe")
        seed = suite.cases[-1]
        c9._source_inputs(suite, api, seed, database, [
            {"id": "slow", "name": "Actual slow fixture", "base_url": slow.url, "kind": "komga"},
            {"id": "zz-next", "name": "Actual next fixture", "base_url": next_provider.url, "kind": "komga"}])
        replay = lambda: api.Replay().intent("search", "query", c9.QUERY, wait=5000)
        baseline = suite.capture(SC_IDS[1], "search", device="scribe", demo=False, offline=False, data=data,
                                 replay=replay(), modal="")
        controls, _ = _geometry(baseline)
        expected = [api.compound_id("slow", f"series-{index:03}") for index in range(6)]
        suite.check(baseline, "The first real HTTP group exposes all six distinct series000 through series005 titles",
                    [control["book"]["id"] for control in controls[:6]] == expected
                    and all(control["book"].get("title") == f"Local fixture series {index:03}"
                            for index, control in enumerate(controls[:6])), controls[:6])
        suite.save_provider(baseline["name"] + "--slow", slow, baseline)
        suite.save_provider(baseline["name"] + "--next", next_provider, baseline)
        _mark(suite, baseline, qualifier, "All six actual titles, their author rows and the next real provider boundary must remain intact under the native two-line protocol.")
        with Image.open(baseline["screenshot"]) as original:
            gray = original.convert("L")
            cover_observations = []
            for control in controls[:6]:
                left, top = control["left"], control["cover_top"]
                horizontal = range(math.ceil(left + 8), math.floor(left + 118))
                vertical = range(top + 8, top + 158)
                alternatives = {
                    "top": [[(x, y) for x in horizontal] for y in (top, top + 1)],
                    "bottom": [[(x, y) for x in horizontal] for y in (top + 164, top + 165)],
                    "left": [[(x, y) for y in vertical] for x in range(math.floor(left), math.ceil(left) + 2)],
                    "right": [[(x, y) for y in vertical] for x in range(math.floor(left + 124), math.ceil(left + 126))],
                }
                fractions = {edge: max(sum(gray.getpixel(point) < 224 for point in samples) / len(samples)
                                      for samples in choices) for edge, choices in alternatives.items()}
                suite.check(baseline, "The fresh identity hit belongs to a complete actually visible native cover frame",
                            all(value >= 0.8 for value in fractions.values()),
                            {"id": control["book"]["id"], "cover_rectangle": [left, top, 126, 166], "edge_fractions": fractions})
                cover_observations.append({"book_id": control["book"]["id"], "edge_fractions": fractions,
                                           "cover_rectangle": [left, top, 126, 166], "hit": control["hit"]})
        c9._retain(suite, api, baseline, "c10-visible-cover-hits", cover_observations)
        opened = []
        for index in range(2):
            control = controls[index]
            actions = replay().tap(*control["hit"], wait=1400)
            if index == 0:
                actions.intent("detail", "favorite", wait=200)
            case = suite.capture(SC_IDS[2 + index], "search", device="scribe", demo=False, offline=False, data=data,
                                 replay=actions, expected_route="detail", modal="", expected={"current_book": expected[index]})
            suite.check(case, "The fresh actual native tile hit opens its own distinct provider book",
                        case["state"].get("current_book") == expected[index], case["state"].get("current_book"))
            actual_actions = json.loads(Path(case["actions"]).read_text(encoding="utf-8-sig"))
            pointers = [action for action in actual_actions if action.get("section") == "pointer"]
            suite.check(case, "The current-source identity hit is one native pointer tap without a semantic open substitute",
                        len(pointers) == 3
                        and sum(action.get("action") == "press" for action in pointers) == 1
                        and sum(action.get("action") == "release" for action in pointers) == 1
                        and not any(action.get("action") == "open" and action.get("section") in ("catalog", "search")
                                    for action in actual_actions), pointers)
            opened.append(case["state"].get("current_book"))
            c9._retain(suite, api, case, "c10-tile-origin", {"origin_case_id": baseline["name"],
                       "original_sha256": c9._sha256(Path(baseline["screenshot"])), "visible_control": control,
                       "actual_pointer_actions": pointers,
                       "new_distinct_hit_scope": "Two actual current-source tiles; earlier six-of-six C9 hits are separate component evidence."})
            _mark(suite, case, qualifier, "This actual pointer opens the matching visible tile; prior six-of-six interaction evidence is not relabeled as a new run.")
        suite.check(case, "Both fresh current-source pointer hits resolve to distinct actual books",
                    opened == expected[:2] and len(set(opened)) == 2, opened)
        suite.persist_case(case)
        variant = c9.ShortTitleResponse(suite, api, slow)
        try:
            short = suite.capture(SC_IDS[4], "search", device="scribe", demo=False, offline=False, data=data,
                                  replay=replay(), modal="")
            inference = _infer_h2(suite, api, short)
            _assess_sc(suite, api, short, inference, short_first=True)
            _assess_sc(suite, api, baseline, inference, short_first=False)
            c9._retain(suite, api, short, "c10-short-http-input", variant.records)
            suite.save_provider(short["name"] + "--slow", slow, short)
            suite.save_provider(short["name"] + "--next", next_provider, short)
            _mark(suite, short, qualifier, "The real short-title bookmark frame supplies the measured author anchor; H2 and the native P interval are inferred from actual matching glyph starts, not defaulted or clamped.")
        finally:
            variant.restore()


def run_search_identity(suite: Any, api: Any, qualifier: bool = False) -> None:
    """Run the same fifteen fresh captures and strong pixel checks in every mode."""
    before = len(suite.cases)
    names = FreshNames(suite)
    c9._pw_four(names, api)
    c9._pw_dense(names, api)
    for case in suite.cases[before:]:
        _mark(suite, case, qualifier, "Current-source scope captions, shortcut identities, native pointer selection and the actual unsubmitted draft are freshly checked; no old original is reused.")
        if case["state"].get("modal") == "keyboard":
            _scope_pixels(suite, api, case)
    _sc(suite, api, qualifier)
