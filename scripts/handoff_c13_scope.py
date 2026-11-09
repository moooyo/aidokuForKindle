"""Repeat all three selected Scribe source names in every complete fresh round."""

import handoff_c12_scope as scope


CASE_IDS = tuple(f"scribe--c13-dense-shortcut-{index:02}" for index in range(3))


def run_selected_shortcuts(suite, api, baseline, data, provider):
    """Reuse the frozen native outline, draft and pointer contracts on real data."""
    points = scope._points(suite, api, baseline)
    sources = baseline["state"]["search_sources"][:3]
    if len(sources) != 3:
        raise ValueError("All three actual Scribe source shortcuts are required")
    for case_id, item in zip(CASE_IDS, sources):
        start = len(provider.requests)
        selected = suite.capture(case_id, "search", device="scribe", demo=False, offline=False, data=data,
                                 replay=scope._typing(api).tap(*points[item["id"]]), modal="keyboard")
        scope._draft(suite, selected, item["id"])
        scope._native_labels(suite, api, selected)
        scope._pointer_only(suite, selected)
        scope._post_count(suite, selected, provider, start)
        selected["c13_source_shortcut_control"] = True
        selected.setdefault("visible_ui_requirements", []).append(
            "Fresh original glyph review must read the complete selected700 source numeric identity and name; the same round's existing scope-typing/cancel/select-last frames provide the normal500 comparison.")
        suite.persist_case(selected)
