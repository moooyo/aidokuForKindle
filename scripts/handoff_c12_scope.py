"""Fresh native shortcut-name qualification without private width diagnostics."""

from contextlib import contextmanager
import json
import math
from pathlib import Path

from PIL import Image

import handoff_c9_search as c9


PW_IDS = (
    "pw5--c12-dense-sources--fixture-seed", "pw5--c12-dense-source-typing",
    "pw5--c12-dense-shortcut-00", "pw5--c12-dense-shortcut-01-pressed",
)
SC_IDS = (
    "scribe--c12-dense-sources--fixture-seed", "scribe--c12-dense-source-typing",
    "scribe--c12-dense-source-picker-last", "scribe--c12-dense-source-cancel",
    "scribe--c12-dense-source-select-last", "scribe--c12-dense-shortcut-00",
    "scribe--c12-dense-shortcut-01", "scribe--c12-dense-shortcut-02",
)
CASE_IDS = (*PW_IDS, *SC_IDS)
DRAFT = "typed-without-submission"


def _typing(api):
    return (api.Replay().intent("search", "query", "fixture", wait=7000).intent("search", "edit")
            .intent("keyboard", "mode", "english", wait=100).intent("keyboard", "key", "clear", wait=100)
            .type_text(DRAFT))


def _draft(suite, case, scope="all"):
    for key, expected in {"query": "fixture", "keyboard_text": DRAFT, "keyboard_mode": "english",
                          "keyboard_composition": "", "search_scope": scope}.items():
        suite.expect(case, key, expected)
    suite.check(case, "The actual twenty-five configured source identities remain eligible without substituted names",
                len(case["state"].get("search_sources", [])) == 25, len(case["state"].get("search_sources", [])))


def _post_count(suite, case, provider, start):
    with provider.lock:
        delta = [dict(item) for item in provider.requests[start:]]
    posts = [item for item in delta if item["method"] == "POST" and "/series/list" in item["path"]]
    suite.check(case, "Typing, pointer selection and cancel add no search beyond the twenty-five real initial requests",
                len(posts) == 25 and all(item["authorized"] for item in delta), {"initial_search_posts": len(posts)})
    suite.save_provider(case["name"], provider, case)


def _points(suite, api, case):
    more = suite.scope_more_position(case)
    boxes = case["native_scope_shortcuts"]["border_components"]
    count, offset = (3, 2) if case["device"] == "scribe" else (2, 1)
    if len(boxes) != count + offset + 1:
        raise ValueError("The actual native shortcut frames do not match their profile")
    sources = case["state"]["search_sources"][:count]
    points = {item["id"]: [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
              for item, box in zip(sources, boxes[offset:offset + count])}
    points["more"] = list(more)
    c9._retain(suite, api, case, "c12-native-shortcut-points", {"original_sha256": c9._sha256(Path(case["screenshot"])),
                 "actual_border_components": boxes, "actual_sources": sources, "points": points})
    return points


def _native_labels(suite, api, case, *, pressed=None):
    suite.scope_more_position(case)
    boxes = case["native_scope_shortcuts"]["border_components"]
    large = case["device"] == "scribe"
    count, offset, cap, pad, height = (3, 2, 150, 20, 44) if large else (2, 1, 168, 16, 38)
    scope = case["state"].get("search_scope")
    records = []
    with Image.open(case["screenshot"]) as image:
        gray = image.convert("L")
        for item, frame in zip(case["state"]["search_sources"][:count], boxes[offset:offset + count]):
            left, top, right, bottom = frame
            viewport = [left + pad, top + 2, right - left - pad * 2, height - 4]
            selected = item["id"] == scope
            inverse = selected != (item["id"] == pressed)
            background = gray.getpixel((math.floor(right - 5), math.floor((top + bottom) / 2)))
            expected_background = 0 if inverse else 255
            pixels = [gray.getpixel((x, y)) for y in range(math.ceil(viewport[1]), math.floor(viewport[1] + viewport[3]))
                      for x in range(math.ceil(viewport[0]), math.floor(viewport[0] + viewport[2]))]
            glyph_pixels = sum(value > 200 if inverse else value < 128 for value in pixels)
            suite.check(case, "The actual native name remains inside the unchanged profile frame and uses the real selected or pressed inverse",
                        0 < right - left <= cap + 2 and bottom - top <= height + 2
                        and background == expected_background and glyph_pixels >= 10,
                        {"id": item["id"], "frame": frame, "label_viewport": viewport, "background": background,
                         "selected": selected, "pressed": item["id"] == pressed, "glyph_pixels": glyph_pixels})
            records.append({"id": item["id"], "name": item["name"], "display_name": item.get("display_name"),
                            "frame": frame, "label_viewport": viewport, "selected": selected, "pressed": item["id"] == pressed,
                            "actual_native_glyph_requires_independent_reading": True})
    c9._retain(suite, api, case, "c12-native-label-geometry", {"records": records,
                 "private_preferred_width_exported": False, "native_font_not_substituted": True,
                 "review_requirement": "Read complete visible00/01/02 digits and configured source names from the original PNG; fields do not prove glyph visibility."})
    case.setdefault("visible_ui_requirements", []).append("Read shortcut numeric identity inside the original complete native frame; verify actual selection or pressed inverse and the unchanged draft.")
    suite.persist_case(case)


def _picker_target(suite, api, case):
    eligible = {item["id"] for item in case["state"]["search_sources"]}
    rows = case["state"].get("modal_items", [])
    selected = next((item for item in reversed(rows) if item.get("enabled") is True and item.get("id") in eligible), None)
    if selected is None:
        raise ValueError("The actual last typing-picker page has no eligible configured source")
    index = rows.index(selected)
    if case["device"] == "pw5":
        return selected["id"], c9._picker_point(suite, api, case, selected["id"])
    rectangle = [173, 328 + index * 62, 584, 62]
    with Image.open(case["screenshot"]) as image:
        ink = c9._ink_bounds(image.convert("L"), [rectangle[0], rectangle[1] + 6, rectangle[2], rectangle[3] - 12])
    suite.check(case, "The chosen actual source has visible native row glyphs on its true final typing-picker page",
                ink is not None and case["state"].get("panel_page") == case["state"].get("panel_page_count"),
                {"actual_item": selected, "rectangle": rectangle, "glyph_bounds": ink})
    hit = [465, rectangle[1] + 31]
    c9._retain(suite, api, case, "c12-last-picker-native-target", {"actual_item": selected, "row_rectangle": rectangle,
                 "hit": hit, "original_sha256": c9._sha256(Path(case["screenshot"]))})
    return selected["id"], hit


def _pointer_only(suite, case):
    actions = json.loads(Path(case["actions"]).read_text())
    suite.check(case, "Actual source selection uses a native pointer rather than a semantic source or panel-choice substitute",
                any(item["section"] == "pointer" and item["action"] == "release" for item in actions)
                and not any((item["section"], item["action"]) in (("search", "source"), ("panel", "choose")) for item in actions), actions)


def _run_device(suite, api, device):
    ids = SC_IDS if device == "scribe" else PW_IDS
    with api.Fixture() as provider:
        data, database = suite.seed_provider(f"{device}--c12-dense-sources", provider, device)
        entries = [{"id": f"configured-{index:02}", "name": f"Configured local fixture {index:02} with a deliberately long source name",
                    "base_url": provider.url} for index in range(24)]
        entries.append({"id": "komga", "name": "Local komga fixture", "base_url": provider.url})
        c9._source_inputs(suite, api, suite.cases[-1], database, entries)
        start = len(provider.requests)
        baseline = suite.capture(ids[1], "search", device=device, demo=False, offline=False, data=data,
                                 replay=_typing(api), modal="keyboard")
        _draft(suite, baseline)
        points = _points(suite, api, baseline)
        _native_labels(suite, api, baseline)
        _post_count(suite, baseline, provider, start)
        if device == "scribe":
            start = len(provider.requests)
            picker = suite.capture(ids[2], "search", device=device, demo=False, offline=False, data=data,
                                   replay=_typing(api).tap(*points["more"]).intent("panel", "page", "99999"),
                                   modal="choices", context="search-scope:typing")
            _draft(suite, picker)
            chosen, hit = _picker_target(suite, api, picker)
            _post_count(suite, picker, provider, start)
            start = len(provider.requests)
            cancel = suite.capture(ids[3], "search", device=device, demo=False, offline=False, data=data,
                                   replay=_typing(api).tap(*points["more"]).intent("panel", "page", "99999").intent("panel", "close"), modal="keyboard")
            _draft(suite, cancel)
            _native_labels(suite, api, cancel)
            _post_count(suite, cancel, provider, start)
            start = len(provider.requests)
            selected = suite.capture(ids[4], "search", device=device, demo=False, offline=False, data=data,
                                     replay=_typing(api).tap(*points["more"]).intent("panel", "page", "99999").tap(*hit), modal="keyboard")
            _draft(suite, selected, chosen)
            _native_labels(suite, api, selected)
            _pointer_only(suite, selected)
            _post_count(suite, selected, provider, start)
        for index in range(3 if device == "scribe" else 2):
            identifier = baseline["state"]["search_sources"][index]["id"]
            start = len(provider.requests)
            replay = _typing(api).tap(*points[identifier])
            pressed = identifier if device == "pw5" and index == 1 else None
            if pressed:
                position = ",".join(f"{value:g}" for value in points[identifier])
                replay.event("pointer", "move", position).event("pointer", "press", position, 50)
            case_id = ids[5 + index] if device == "scribe" else ids[2 + index]
            selected = suite.capture(case_id, "search", device=device, demo=False, offline=False, data=data,
                                     replay=replay, modal="keyboard")
            _draft(suite, selected, identifier)
            _native_labels(suite, api, selected, pressed=pressed)
            _pointer_only(suite, selected)
            _post_count(suite, selected, provider, start)
            suite.persist_case(selected)


def run(suite, api):
    for device in ("pw5", "scribe"):
        _run_device(suite, api, device)
