"""Read-only native-pixel checks for the bounded C6 tag qualification.

This module never renders text or repairs a screenshot. The full capture driver
keeps its existing IDs and pagination assertions; these extra observations only
inspect the unchanged PNGs produced by the affected-tag qualification.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _retain(suite: Any, case: dict[str, Any], label: str, value: Any) -> None:
    path = suite.output / "fixtures" / f"{case['name']}--{label}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    case.setdefault("evidence_hashes", {})[f"c6:{label}"] = {"path": str(path), "sha256": _sha256(path)}
    suite.persist_case(case)


def _scan_boxes(gray: Any, region: tuple[int, int, int, int], height: int) -> list[tuple[int, int, int, int]]:
    left, top, right, bottom = region
    if not (0 <= left < right <= gray.width and 0 <= top < bottom <= gray.height):
        raise ValueError("The source-defined native tag region lies outside the original PNG")
    pixels = {(x, y) for y in range(top, bottom) for x in range(left, right) if gray.getpixel((x, y)) < 224}
    boxes = []
    while pixels:
        point = pixels.pop()
        stack = [point]
        x0 = x1 = point[0]
        y0 = y1 = point[1]
        while stack:
            x, y = stack.pop()
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for adjacent in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1),
                             (x - 1, y - 1), (x + 1, y - 1), (x - 1, y + 1), (x + 1, y + 1)):
                if adjacent in pixels:
                    pixels.remove(adjacent)
                    stack.append(adjacent)
        if x1 - x0 + 1 >= 32 and height - 6 <= y1 - y0 + 1 <= height + 6:
            boxes.append((x0, y0, x1 + 1, y1 + 1))
    return sorted(boxes, key=lambda box: (round((box[1] - top) / (height + 8)), box[0]))


def _source_region(case: dict[str, Any], width: int, height: int, paged: bool) -> tuple[int, int, int, int]:
    large = case["device"] == "scribe"
    pad, chip_height = (28, 46) if large else (22, 42)
    panel_width = 620 if large else width
    panel_x = width - panel_width - 28 if large else 0
    if large:
        tag_top = 282 + 315
    else:
        tag_top = height - (chip_height * 2 + 8 + (56 if paged else 0) + 17 + 52 + 24)
    return (panel_x + pad - 1, tag_top - 6,
            panel_x + panel_width - pad + 1, tag_top + 2 * (chip_height + 8) + 6)


def _frame_pixels(gray: Any, box: tuple[int, int, int, int]) -> dict[str, Any]:
    """Measure every edge and uninterrupted missing run on the original frame."""
    left, top, right, bottom = box
    points = {
        "top": [(x, top) for x in range(left + 8, right - 8)],
        "bottom": [(x, bottom - 2) for x in range(left + 8, right - 8)],
        "left": [(left, y) for y in range(top + 8, bottom - 8)],
        "right": [(right - 2, y) for y in range(top + 8, bottom - 8)],
    }
    ink = {name: [min(gray.getpixel((x + dx, y + dy)) for dx in (0, 1) for dy in (0, 1)) < 224
                  for x, y in positions] for name, positions in points.items()}
    return {"rectangle": list(box),
            "edge_ink_fractions": {name: sum(values) / max(1, len(values)) for name, values in ink.items()},
            "longest_missing_edge_runs": {name: _longest_run([not value for value in values]) for name, values in ink.items()},
            "sampling": "Unmodified native border pixels; two-by-two minimum covers native antialiasing and fractional border placement; rounded corners alone are excluded"}


def long_caller_bounds(case: dict[str, Any]) -> tuple[int, int, int, int]:
    """Return the independent caller's full-width long-chip rectangle as LTRB."""
    if case["device"] not in ("pw5", "scribe"):
        raise ValueError("The bounded long-tag fixture requires the declared PW or Scribe portrait profile")
    with Image.open(case["screenshot"]) as original:
        region = _source_region(case, original.width, original.height, True)
        height = 46 if case["device"] == "scribe" else 42
        bounds = (region[0] + 1, region[1] + 6, region[2] - 1, region[1] + 6 + height)
        left, top, right, bottom = bounds
        if not (0 <= left < right <= original.width and 0 <= top < bottom <= original.height):
            raise ValueError("The complete caller frame does not fit the actual native PNG")
    return bounds


def inspect_long_frame(case: dict[str, Any], *, excluded: bool = False) -> dict[str, Any]:
    """Measure a long frame without changing a case, PNG, suite or old report."""
    path = Path(case["screenshot"])
    bounds = long_caller_bounds(case)
    checks = []

    def check(description: str, passed: bool, actual: Any) -> None:
        checks.append({"description": description, "passed": bool(passed), "actual": actual})

    with Image.open(path) as original:
        gray = original.convert("L")
        frame = _frame_pixels(gray, bounds)
        region = _source_region(case, gray.width, gray.height, True)
        height = 46 if case["device"] == "scribe" else 42
        boxes = [] if excluded else _scan_boxes(gray, region, height)
        if excluded:
            check("The full caller rectangle retains four actual dashed native edges",
                  all(0.25 <= value <= 0.9 for value in frame["edge_ink_fractions"].values()), frame)
            check("The actual dash pattern has no additional mask-sized missing run",
                  all(value <= 5 for value in frame["longest_missing_edge_runs"].values()), frame)
            left, top, right, bottom = bounds
            pad = 18 if case["device"] == "scribe" else 16
            marker = (left + pad, top + 6, left + pad + 14, bottom - 6)
            cross = sum(gray.getpixel((x, y)) < 128 for y in range(marker[1], marker[3])
                        for x in range(marker[0], marker[2]))
            middle = (top + bottom) // 2
            strike = max(_longest_run([gray.getpixel((x, y)) < 224 for x in range(left + pad + 18, right - pad)])
                         for y in range(middle - 1, middle + 2))
            check("The actual long excluded tag keeps its visible gray cross", cross >= 5,
                  {"marker_region": list(marker), "cross_pixels": cross})
            check("The actual long excluded label keeps its native strike", strike >= 30,
                  {"longest_middle_ink_run": strike})
        else:
            check("The observed first long chip matches the caller's complete LTRB bounds rather than an outline fragment",
                  bool(boxes) and all(abs(actual - expected) <= 1 for actual, expected in zip(boxes[0], bounds)),
                  {"expected_ltrb": list(bounds), "observed_components_ltrb": boxes})
            check("The full caller rectangle retains four actual closed native solid edges",
                  all(value >= 0.8 for value in frame["edge_ink_fractions"].values()), frame)
            check("The full caller solid frame has no mask-sized white interruption",
                  all(value <= 3 for value in frame["longest_missing_edge_runs"].values()), frame)
    return {"passed": all(item["passed"] for item in checks), "checks": checks,
            "expected_ltrb": list(bounds), "frame": frame,
            "source_screenshot_sha256": _sha256(path), "source_dimensions": list(original.size),
            "style": "excluded-dashed" if excluded else "neutral-solid",
            "scope": "Read-only original native pixels at the independent full-width caller frame; no CC snapping, redraw or repair"}


def observe_neutral(
    suite: Any, case: dict[str, Any], stress_paged: bool = True, *, full_width_first: bool = False,
) -> list[tuple[int, int, int, int]]:
    """Return real neutral chip rectangles ordered by native row and x position."""
    tags = case["state"].get("catalog_tag_options", [])
    suite.check(case, "The native chip observation precedes any tag selection",
                bool(tags) and all(tag.get("state") == 0 for tag in tags), tags)
    original_path = Path(case["screenshot"])
    prior = case.get("native_tag_observation", {})
    with Image.open(original_path) as original:
        gray = original.convert("L")
        region = tuple(prior.get("region") or _source_region(case, gray.width, gray.height, stress_paged))
        boxes = _scan_boxes(gray, region, 46 if case["device"] == "scribe" else 42)
        frames = [_frame_pixels(gray, box) for box in boxes]
        for frame in frames:
            suite.check(case, "The observed neutral chip has four actual closed native edges, not a disconnected outline fragment",
                        all(value >= 0.8 for value in frame["edge_ink_fractions"].values()), frame)
            suite.check(case, "The neutral native outline has no label-mask-sized white interruption",
                        all(value <= 3 for value in frame["longest_missing_edge_runs"].values()), frame)
    suite.check(case, "Real native outlines expose a nonempty neutral tag page", bool(boxes), boxes)
    if prior:
        earlier = [tuple(item["rectangle"]) for item in prior.get("controls", [])]
        current = [(left, top, right - left, bottom - top) for left, top, right, bottom in boxes]
        suite.check(case, "The qualification observes the same unchanged native outlines as the full recipe",
                    current == earlier and prior.get("original_sha256") == _sha256(original_path),
                    {"current": current, "earlier": earlier})
    case["c6_native_tag_observation"] = {
        "original_screenshot": str(original_path), "original_sha256": _sha256(original_path),
        "region": list(region), "boxes": [list(box) for box in boxes],
        "frame_pixels": frames,
        "method": "Connected components of unchanged native outlines, ordered by actual row and horizontal position",
    }
    if full_width_first:
        inspected = inspect_long_frame(case)
        for item in inspected["checks"]:
            suite.check(case, item["description"], item["passed"], item.get("actual"))
        case["c6_native_tag_observation"]["expected_full_frame_ltrb"] = inspected["expected_ltrb"]
        case["c7_full_frame_pixels"] = inspected
        _retain(suite, case, "complete-long-frame-pixels", inspected)
    _retain(suite, case, "neutral-native-outlines", case["c6_native_tag_observation"])
    if not boxes or not case["passed"]:
        raise ValueError(f"The native neutral chip page is not eligible for real pointer replay: {case['name']}")
    return boxes


def _ink_bounds(gray: Any, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    points = [(x, y) for y in range(top + 4, bottom - 4) for x in range(left + 3, right - 3)
              if gray.getpixel((x, y)) < 96]
    if not points:
        raise ValueError("A real neutral chip contains no visible label ink")
    return (min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points) + 1, max(y for _, y in points) + 1)


def _centered_labels(suite: Any, case: dict[str, Any], boxes: list[tuple[int, int, int, int]]) -> None:
    pad = 18 if case["device"] == "scribe" else 16
    records = []
    with Image.open(case["screenshot"]) as original:
        gray = original.convert("L")
        for box in boxes:
            left, top, right, bottom = box
            ink = _ink_bounds(gray, box)
            center_error = (ink[0] + ink[2] - left - right) / 2
            width_slack = (right - left) - (ink[2] - ink[0]) - 2 * pad
            record = {"rectangle": list(box), "label_ink_bounds": list(ink),
                      "horizontal_center_error_pixels": center_error,
                      "advance_minus_ink_width_pixels": width_slack,
                      "declared_horizontal_padding": pad}
            suite.check(case, "A real neutral label is horizontally centered in its own intrinsic chip",
                        abs(center_error) <= 3, record)
            suite.check(case, "A real neutral intrinsic chip contains padding and native glyph bearings without an empty marker gutter",
                        -2 <= width_slack <= 8, record)
            records.append(record)
    case["c6_neutral_label_pixels"] = {
        "original_sha256": _sha256(Path(case["screenshot"])), "controls": records,
        "measurement": "Actual opaque native glyph ink, with three-pixel center allowance for raster glyph bearings; no text is rendered by the harness",
    }
    case.setdefault("visible_ui_requirements", []).append("Neutral labels are centered inside content-width outlines without an empty marker gutter; distinguishing suffixes remain readable.")
    _retain(suite, case, "neutral-label-pixels", case["c6_neutral_label_pixels"])


def _longest_run(values: list[bool]) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def _marker_states(suite: Any, first: dict[str, Any], selected: dict[str, Any], excluded: dict[str, Any]) -> None:
    large = first["device"] == "scribe"
    pad, chip_height = (18, 46) if large else (16, 42)
    observation = first["c6_native_tag_observation"]
    region = tuple(observation["region"])
    neutral_box = tuple(observation["boxes"][0])
    with Image.open(selected["screenshot"]) as original:
        gray = original.convert("L")
        boxes = _scan_boxes(gray, region, chip_height)
        suite.check(selected, "The actually selected native page retains all visible chip surfaces",
                    len(boxes) == len(observation["boxes"]), boxes)
        if not boxes:
            suite.persist_case(selected)
            raise ValueError("The selected native chip surface could not be observed")
        box = boxes[0]
        left, top, right, bottom = box
        marker = (left + pad, top + 6, left + pad + 14, bottom - 6)
        background = [(x, y) for y in range(top + 8, bottom - 8) for x in range(left + 5, left + 11)]
        dark_fill = sum(gray.getpixel(point) < 32 for point in background) / len(background)
        visible = sum(gray.getpixel((x, y)) >= 224 for y in range(marker[1], marker[3])
                      for x in range(marker[0], marker[2]))
        suite.check(selected, "The actually selected native chip has a black fill behind its contrasting white checkmark",
                    dark_fill >= 0.95, {"rectangle": list(box), "dark_background_fraction": dark_fill})
        suite.check(selected, "The real selected chip keeps visible white checkmark ink in its actual marker region", visible >= 5,
                    {"rectangle": list(box), "marker_region": list(marker), "white_marker_pixels": visible})
        suite.check(selected, "Selection retains an actual marker gutter while neutral chips use their smaller intrinsic width",
                    right - left >= neutral_box[2] - neutral_box[0] + 6,
                    {"neutral_width": neutral_box[2] - neutral_box[0], "selected_width": right - left})
    selected["c6_selected_marker_pixels"] = {"rectangle": list(box), "marker_region": list(marker), "white_marker_pixels": visible,
                                             "dark_background_fraction": dark_fill}
    _retain(suite, selected, "selected-marker-pixels", selected["c6_selected_marker_pixels"])

    with Image.open(excluded["screenshot"]) as original:
        gray = original.convert("L")
        cross = sum(gray.getpixel((x, y)) < 128 for y in range(marker[1], marker[3])
                    for x in range(marker[0], marker[2]))
        edge_points = {
            "top": [(x, top) for x in range(left + 8, right - 8)],
            "bottom": [(x, bottom - 2) for x in range(left + 8, right - 8)],
            "left": [(left, y) for y in range(top + 8, bottom - 8)],
            "right": [(right - 2, y) for y in range(top + 8, bottom - 8)],
        }
        edges = {name: sum(min(gray.getpixel((x + dx, y + dy)) for dx in (0, 1) for dy in (0, 1)) < 224
                           for x, y in points) / max(1, len(points)) for name, points in edge_points.items()}
        middle = (top + bottom) // 2
        strike = max(_longest_run([gray.getpixel((x, y)) < 224 for x in range(left + pad + 18, right - pad)])
                     for y in range(middle - 1, middle + 2))
    suite.check(excluded, "The actual excluded chip keeps visible cross ink inside its reserved marker region", cross >= 5,
                {"marker_region": list(marker), "cross_pixels": cross})
    suite.check(excluded, "The excluded native chip keeps a real dashed frame at the selected marker width",
                all(0.25 <= fraction <= 0.9 for fraction in edges.values()), {"rectangle": list(box), "edge_fractions": edges})
    suite.check(excluded, "The actual excluded label retains its native horizontal strike", strike >= 30,
                {"longest_middle_ink_run": strike, "rectangle": list(box)})
    excluded["c6_excluded_marker_pixels"] = {"rectangle": list(box), "marker_region": list(marker), "cross_pixels": cross,
                                             "dashed_edge_fractions": edges, "middle_strike_run": strike}
    excluded.setdefault("visible_ui_requirements", []).append("The real excluded chip visibly keeps its cross, dashed frame, readable suffix and label strike.")
    _retain(suite, excluded, "excluded-marker-pixels", excluded["c6_excluded_marker_pixels"])


def _long_excluded_frame(suite: Any, neutral: dict[str, Any], excluded: dict[str, Any]) -> None:
    inspected = inspect_long_frame(excluded, excluded=True)
    suite.check(excluded, "The actual long excluded and neutral states retain the same complete caller frame",
                inspected["expected_ltrb"] == list(long_caller_bounds(neutral)), inspected["expected_ltrb"])
    for item in inspected["checks"]:
        suite.check(excluded, item["description"], item["passed"], item.get("actual"))
    excluded["c6_long_excluded_frame_pixels"] = inspected
    excluded.setdefault("visible_ui_requirements", []).append("The long excluded tag retains its regular native dash pattern without an extra white break beside the ellipsis.")
    _retain(suite, excluded, "long-excluded-frame-pixels", inspected)


def assess_frame_cases(suite: Any) -> None:
    """Inspect the six focused C7 states without claiming a repeated full sweep."""
    by_name = {case["name"]: case for case in suite.cases}
    for identifier in ("pw-source--filter", "sc-browse--filter"):
        case = by_name[identifier]
        _centered_labels(suite, case, observe_neutral(suite, case, stress_paged=False))
    for device in ("pw5", "scribe"):
        neutral = by_name[f"{device}--c7-tag-long-neutral"]
        excluded = by_name[f"{device}--c7-tag-long-excluded"]
        observe_neutral(suite, neutral, full_width_first=True)
        _long_excluded_frame(suite, neutral, excluded)


def assess_tag_cases(suite: Any) -> None:
    """Inspect native neutral, included and excluded surfaces after real replay."""
    by_name = {case["name"]: case for case in suite.cases}
    for case in suite.cases:
        if case["name"] in ("pw-source--filter", "sc-browse--filter"):
            _centered_labels(suite, case, observe_neutral(suite, case, stress_paged=False))
        elif case.get("native_tag_observation"):
            _centered_labels(suite, case, observe_neutral(suite, case))
    # Root adjudicated this C6 fixture as three columns on both profiles from
    # the actual 700c captures and unchanged capacity expressions. The older
    # C4 PW four-column fixture is not a C5/C6 runtime baseline; C5 PW is unknown.
    for device, columns in (("pw5", 3), ("scribe", 3)):
        prefix = f"{device}--content-stress"
        first = by_name[f"{prefix}--filter-tags-first"]
        selected = by_name[f"{prefix}--filter-tag-page-1"]
        excluded = by_name[f"{device}--c6-tag-excluded"]
        tags = first["state"].get("catalog_tag_options", [])
        geometry = first.get("native_tag_geometry", {})
        size = columns * 2
        suite.check(first, "The actual C6 34-tag fixture uses its adjudicated column capacity and complete page count",
                    len(tags) == 34 and len({tag["id"] for tag in tags}) == 34
                    and geometry.get("columns") == columns and geometry.get("page_size") == size
                    and geometry.get("page_count") == (34 + size - 1) // size, geometry)
        suite.persist_case(first)
        _marker_states(suite, first, selected, excluded)
        _long_excluded_frame(suite, by_name[f"{device}--c6-tag-long-neutral"],
                             by_name[f"{device}--c6-tag-long-excluded"])
