"""Qualify the C11 heading paint and hit boundary using original native frames."""

from __future__ import annotations

from contextlib import ExitStack
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

import handoff_c9_search as c9
import handoff_c10_search as c10


CASE_IDS = (
    "scribe--c11-author-boundary--fixture-seed",
    "scribe--c11-title-groups",
    "scribe--c11-favorite-000--prerequisite",
    "scribe--c11-short-bookmark",
    "scribe--c11-author-tail-open-005",
    "scribe--c11-next-view-all",
)
MANUAL_IDS = (CASE_IDS[1], CASE_IDS[3], CASE_IDS[4], CASE_IDS[5])
PREFIX_COLUMNS = 111
INK_THRESHOLD = 160
NEGATIVE_INPUTS = {
    "scribe--c10-native-title-groups": {
        "png": "79b2f3fc1645055968841d9bcdd1c0d9385304167630a441c17187563aa3554c",
        "state.json": "938900723ce51d3214acccb0126bfa8884818bbb46b39ce52edd6a57edac9a03",
        "metadata.json": "e1b393fabed17ec8efb821e673902ade2029f127fc6d43a8d4bf4a85fc07f338",
    },
    "scribe--c10-native-short-bookmark": {
        "png": "18e4210f15336a0775ae552e2ca36574c811aecf7f2815dc364c84ee5c943657",
        "state.json": "f429afe1329ab08e6554418942b8c6101711312d71cb26ccc539c89a5b3fa4d4",
        "metadata.json": "55273b98b8c952c21c0306b70dcc52719028217d80fca4ea214ddb1f2b210db1",
    },
}


def negative_paths(root: Path) -> dict[str, Path]:
    return {f"negative:{name}:{suffix}": root / f"{name}.{suffix}"
            for name, values in NEGATIVE_INPUTS.items() for suffix in values}


def _read_negative(root: Path, name: str) -> dict[str, Any]:
    for suffix, expected in NEGATIVE_INPUTS[name].items():
        path = root / f"{name}.{suffix}"
        if not path.is_file() or c9._sha256(path) != expected:
            raise ValueError(f"The preserved C10 negative input changed or is missing: {path}")
    return {"name": name, "screenshot": str(root / f"{name}.png"),
            "state": json.loads((root / f"{name}.state.json").read_text(encoding="utf-8-sig")),
            "metadata": json.loads((root / f"{name}.metadata.json").read_text(encoding="utf-8-sig"))}


def _prefix_measurement(case: dict[str, Any], h2: int) -> dict[str, Any]:
    """Compare the observed shared glyph region without equating full captions."""
    controls, width = c10._geometry(case)
    a, b = controls[2], controls[5]
    if (a["book"].get("author") != "Fixture Author" or b["book"].get("author") != "Fixture Author"
            or a["book"].get("subtitle") != "8 话" or b["book"].get("subtitle") != "已完结"
            or any(control["book"].get(key) for control in (a, b) for key in ("in_library", "selected"))):
        raise ValueError("The real 002/005 shared-prefix control does not retain its original public metadata")
    meta = case["metadata"]
    if (meta.get("scale_factor") != 1 or meta.get("snapshot_size") != [930, 1240]
            or meta.get("profile") != "scribe"):
        raise ValueError("The shared-prefix oracle requires the original native Scribe physical scale")
    left_a, left_b = math.ceil(a["left"]), math.ceil(b["left"])
    author_top = a["cover_top"] + 172 + h2
    top, bottom = author_top - 2, author_top + 20
    with Image.open(case["screenshot"]) as original:
        gray = original.convert("L")
        region_a = gray.crop((left_a, top, left_a + PREFIX_COLUMNS, bottom))
        region_b = gray.crop((left_b, top, left_b + PREFIX_COLUMNS, bottom))
        bands_a = c10._bands(gray, [a["left"], author_top, width, 18])
        bands_b = c10._bands(gray, [b["left"], author_top, width, 18])
        if len(bands_a) != 1 or len(bands_b) != 1:
            raise ValueError("Both real author captions must occupy one complete separate native row")
        tail_y = bands_a[0][3] - 1
        above_a = gray.crop((left_a, top, left_a + PREFIX_COLUMNS, tail_y))
        above_b = gray.crop((left_b, top, left_b + PREFIX_COLUMNS, tail_y))
        differences = ImageChops.difference(region_a, region_b).histogram()
        tail_a = [gray.getpixel((left_a + x, tail_y)) for x in range(PREFIX_COLUMNS)]
        tail_b = [gray.getpixel((left_b + x, tail_y)) for x in range(PREFIX_COLUMNS)]
        ink_a = sum(value < INK_THRESHOLD for value in tail_a)
        ink_b = sum(value < INK_THRESHOLD for value in tail_b)
        candidates = [x for x in range(PREFIX_COLUMNS)
                      if tail_a[x] < INK_THRESHOLD and tail_b[x] < INK_THRESHOLD]
        point = [left_b + candidates[len(candidates) // 2] + 0.5, tail_y + 0.5] if candidates else None
    same_phase = abs((b["left"] - a["left"]) - 440) < 1e-9
    equal_above = above_a.tobytes() == above_b.tobytes()
    equal_complete = region_a.tobytes() == region_b.tobytes()
    return {"case_id": case["name"], "original_sha256": c9._sha256(Path(case["screenshot"])),
            "classification": "observed_shared_prefix_native_pixels",
            "full_rendered_captions": [c10._caption(a["book"]), c10._caption(b["book"])],
            "full_captions_are_equal": False, "complete_caption_equality_is_not_assumed": True,
            "prefix_columns": PREFIX_COLUMNS, "threshold": INK_THRESHOLD,
            "author_top": author_top, "author_bands_002": bands_a, "author_bands_005": bands_b,
            "physical_integer_dx": left_b - left_a, "same_fractional_x": same_phase,
            "roi_002_ltrb": [left_a, top, left_a + PREFIX_COLUMNS, bottom],
            "roi_005_ltrb": [left_b, top, left_b + PREFIX_COLUMNS, bottom],
            "equal_above_tail": equal_above, "complete_prefix_raw_equal": equal_complete,
            "complete_prefix_raw_difference_pixels": sum(differences[1:]),
            "observed_tail_y": tail_y, "legacy_next_view_all_top": b["next_group_top"] - 4,
            "tail_002_ink_pixels": ink_a, "tail_005_ink_pixels": ink_b,
            "tail_005_is_entirely_paper_white": all(value == 255 for value in tail_b),
            "actual_restored_tail_hit": point,
            "positive_oracle": bool(same_phase and equal_above and equal_complete and ink_a > 0
                                    and ink_a == ink_b and tail_y == b["next_group_top"] - 4),
            "scope": "A complete 111-column observed shared glyph region with its actual tail; not a full-caption, font-substitution, preferred-height or semantic screenshot oracle."}


def qualify_old_negatives(root: Path) -> dict[str, Any]:
    observations = []
    for name in NEGATIVE_INPUTS:
        case = _read_negative(root, name)
        measurement = _prefix_measurement(case, 48)
        measurement["negative_rejected"] = (not measurement["positive_oracle"]
                and measurement["equal_above_tail"] and measurement["observed_tail_y"] == 527
                and measurement["tail_002_ink_pixels"] == 37 and measurement["tail_005_ink_pixels"] == 0
                and measurement["tail_005_is_entirely_paper_white"])
        _read_negative(root, name)
        observations.append(measurement)
    return {"kind": "preserved-c10-white-mask-negative-controls", "observations": observations,
            "passed": all(item["negative_rejected"] for item in observations),
            "prior_result_rewritten": False, "old_source_sha256": "5dca4c420996df4d4c4556430724350f33af4223dd8edb5e91626290813008df",
            "old_binary_sha256": "9ff5f18c03d1ce9c53d260f4e9619cba7f18233e0bed98669329863f2aa8239d"}


def _mark(suite: Any, case: dict[str, Any], requirement: str) -> None:
    case["c11_qualification"] = True
    case["c11_scope"] = "Heading-only ViewAll paint and hit boundary; the existing fixed full fifteen-case search coverage remains unchanged."
    case.setdefault("visible_ui_requirements", []).append(requirement)
    suite.persist_case(case)


def _pointer_actions(suite: Any, api: Any, case: dict[str, Any], origin: dict[str, Any], target: dict[str, Any]) -> None:
    actions = json.loads(Path(case["actions"]).read_text(encoding="utf-8-sig"))
    pointers = [action for action in actions if action.get("section") == "pointer"]
    suite.check(case, "One actual native pointer tap resolves the observed control without a semantic open or ViewAll substitute",
                len(pointers) == 3 and sum(action.get("action") == "press" for action in pointers) == 1
                and sum(action.get("action") == "release" for action in pointers) == 1
                and not any(action.get("section") == "search" and action.get("action") in ("open", "all")
                            for action in actions), pointers)
    same_candidate = (origin["source_sha256"] == case["source_sha256"]
                      and origin["binary_sha256"] == case["binary_sha256"])
    fresh_process = (origin["metadata"].get("process_id") != case["metadata"].get("process_id")
                     and isinstance(case["metadata"].get("process_id"), int)
                     and case["metadata"]["process_id"] > 0)
    suite.check(case, "The pointer result is a fresh native process bound to the same frozen source and executable as its observed target",
                same_candidate and fresh_process,
                {"same_candidate": same_candidate, "fresh_process": fresh_process,
                 "origin_process_id": origin["metadata"].get("process_id"),
                 "result_process_id": case["metadata"].get("process_id")})
    c9._retain(suite, api, case, "c11-pointer-origin", {
        "origin_case_id": origin["name"], "original_sha256": c9._sha256(Path(origin["screenshot"])),
        "target": target, "actual_pointer_actions": pointers,
        "origin_and_target_have_same_frozen_source_and_binary": same_candidate,
        "new_native_process": fresh_process,
    })


def _headings(suite: Any, api: Any, case: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    records = []
    with Image.open(case["screenshot"]) as new_original, Image.open(old["screenshot"]) as old_original:
        new_gray, old_gray = new_original.convert("L"), old_original.convert("L")
        for group_index, group in enumerate(case["state"]["search_groups"]):
            top = 245 + group_index * 286
            heading = [36, top, 858, 48]
            bounds = [36, top, 894, top + 48]
            button = [766, top, 128, 48]
            label_ink = c9._ink_bounds(new_gray, button)
            equal = new_gray.crop(bounds).tobytes() == old_gray.crop(bounds).tobytes()
            record = {"group_id": group["id"], "heading_rectangle": heading,
                      "button_rectangle": button, "button_label_ink": label_ink,
                      "native_button_center": [830, top + 24], "heading_raw_pixels_unchanged": equal,
                      "old_frame_sha256": c9._sha256(Path(old["screenshot"]))}
            suite.check(case, "Each actual provider heading preserves its complete native label, count and ViewAll center inside its own heading",
                        equal and label_ink is not None and button[1] >= heading[1]
                        and button[1] + button[3] <= heading[1] + heading[3], record)
            records.append(record)
    c9._retain(suite, api, case, "c11-heading-boundaries", records)
    return records[1]


def run_author_boundary(suite: Any, api: Any, negative_root: Path) -> dict[str, Any]:
    with ExitStack() as stack:
        slow = stack.enter_context(api.Fixture(search_delay=4.0))
        next_provider = stack.enter_context(api.Fixture())
        slow.series_count, next_provider.series_count = 6, 2
        data, database = suite.seed_provider("scribe--c11-author-boundary", slow, "scribe")
        seed = suite.cases[-1]
        c9._source_inputs(suite, api, seed, database, [
            {"id": "slow", "name": "Actual slow fixture", "base_url": slow.url, "kind": "komga"},
            {"id": "zz-next", "name": "Actual next fixture", "base_url": next_provider.url, "kind": "komga"}])
        replay = lambda: api.Replay().intent("search", "query", c9.QUERY, wait=5000)
        baseline = suite.capture(CASE_IDS[1], "search", device="scribe", demo=False, offline=False,
                                 data=data, replay=replay(), modal="")
        controls, _ = c10._geometry(baseline)
        expected = [api.compound_id("slow", f"series-{index:03}") for index in range(6)]
        suite.check(baseline, "The fresh real HTTP results retain six distinct slow books and two next-provider books",
                    [control["book"]["id"] for control in controls[:6]] == expected
                    and all(control["book"].get("title") == f"Local fixture series {index:03}"
                            for index, control in enumerate(controls[:6])), controls)
        favorite = suite.capture(CASE_IDS[2], "search", device="scribe", demo=False, offline=False, data=data,
                                 replay=replay().tap(*controls[0]["hit"], wait=1400).intent("detail", "favorite", wait=200),
                                 expected_route="detail", expected={"current_book": expected[0]}, modal="", prerequisite=True)
        _mark(suite, favorite, "Setup only: one actual pointer opens series000 and the ordinary favorite action creates its real library state.")
        if not favorite["passed"]:
            raise ValueError("The real favorite prerequisite failed; no short-title or boundary pointer is launched")
        variant = c9.ShortTitleResponse(suite, api, slow)
        try:
            short = suite.capture(CASE_IDS[3], "search", device="scribe", demo=False, offline=False,
                                  data=data, replay=replay(), modal="")
            inference = c10._infer_h2(suite, api, short)
            c10._assess_sc(suite, api, short, inference, short_first=True)
            c10._assess_sc(suite, api, baseline, inference, short_first=False)
            c9._retain(suite, api, short, "c11-short-title-http-input", variant.records)
            measurements = []
            heading_controls = []
            for case, old_name in ((baseline, "scribe--c10-native-title-groups"),
                                   (short, "scribe--c10-native-short-bookmark")):
                measurement = _prefix_measurement(case, inference["H2"])
                suite.check(case, "The same-phase observed shared author glyph is complete through its actual y527 tail without white masking",
                            measurement["positive_oracle"], measurement)
                c9._retain(suite, api, case, "c11-complete-shared-author-glyph", measurement)
                measurements.append(measurement)
                heading_controls.append(_headings(suite, api, case, _read_negative(negative_root, old_name)))
                for label, provider in (("slow", slow), ("next", next_provider)):
                    suite.save_provider(case["name"] + "--" + label, provider, case)
                _mark(suite, case, "Fresh native title, author-tail and both provider headings; actual-frame H2/P interval is inferred without clamping, and the shared glyph closure is independently inspected.")
        finally:
            variant.restore()
        if not baseline["passed"] or not short["passed"] or not all(item["positive_oracle"] for item in measurements):
            raise ValueError("A native title, author or heading boundary failed; no downstream pointer is launched")
        author_target = {"book_id": expected[5], "native_hit": measurements[0]["actual_restored_tail_hit"],
                         "observed_author_tail": measurements[0], "expected_route": "detail"}
        opened = suite.capture(CASE_IDS[4], "search", device="scribe", demo=False, offline=False, data=data,
                               replay=replay().tap(*author_target["native_hit"], wait=1400),
                               expected_route="detail", expected={"current_book": expected[5], "page": 1}, modal="", context="")
        _pointer_actions(suite, api, opened, baseline, author_target)
        suite.check(opened, "The restored prior-group author tail opens the actual slow series005 instead of the following provider",
                    opened["state"].get("current_book") == expected[5], opened["state"].get("current_book"))
        actual_origin = suite.database_rows(opened, "SELECT source_id,remote_id FROM books WHERE id=?", (expected[5],))
        suite.check(opened, "The author-tail pointer resolves the actual Store origin without relying on the global catalog source",
                    actual_origin == [("slow", "series-005")], actual_origin)
        _mark(suite, opened, "The real pointer press/release on the newly visible y527 author tail must open slow series005.")
        button = heading_controls[0]
        view_all = suite.capture(CASE_IDS[5], "search", device="scribe", demo=False, offline=False, data=data,
                                 replay=replay().tap(*button["native_button_center"], wait=1200),
                                 expected_route="catalog", expected={"source_id": "zz-next", "page": 1}, modal="")
        _pointer_actions(suite, api, view_all, baseline, button)
        expected_next = [api.compound_id("zz-next", f"series-{index:03}") for index in range(2)]
        actual_next = [book["id"] for book in view_all["state"].get("visible_catalog", [])]
        suite.check(view_all, "The following heading center opens its own actual two-record next-provider catalog",
                    view_all["state"].get("source_id") == "zz-next" and actual_next == expected_next
                    and view_all["state"].get("catalog_count") == 2,
                    {"source_id": view_all["state"].get("source_id"), "ids": actual_next,
                     "catalog_count": view_all["state"].get("catalog_count")})
        suite.expect(view_all, "source_needs_setup", False)
        source_rows = suite.database_rows(view_all, "SELECT document FROM sources WHERE id=?", ("zz-next",))
        verified = json.loads(source_rows[0][0]).get("options", {}).get("_connection_verified") if source_rows else None
        suite.check(view_all, "The actual next-source HTTP result supplies persisted verification without a fabricated connection flag",
                    verified is True, verified)
        _mark(suite, view_all, "The unchanged native ViewAll center must enter zz-next with its own two real records and an honest verified source state.")
        for case in (opened, view_all):
            for label, provider in (("slow", slow), ("next", next_provider)):
                suite.save_provider(case["name"] + "--" + label, provider, case)
        return {"metric_inference": inference, "positive_author_glyph_observations": measurements,
                "actual_author_tail_target": author_target, "actual_next_view_all_target": button,
                "full_case_ids_unchanged": list(c10.CASE_IDS), "new_full_captures": 0}
