#!/usr/bin/env python3
"""Replay visible Browse and Search controls on the actual portrait simulator."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sys


def load_replay_helpers():
    path = Path(__file__).with_name("verify-ui-handoff.py")
    spec = importlib.util.spec_from_file_location("portrait_handoff_replay", path)
    helpers = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helpers
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(helpers)
    finally:
        sys.dont_write_bytecode = previous
    return helpers


REPLAY = load_replay_helpers()
Case, tap = REPLAY.Case, REPLAY.tap


def cases(width, height, large):
    toolbar = 68 if large else 56
    back = tap(62 if large else 50, toolbar)
    next_page = tap(492 if large else width - 150, toolbar)
    source_picker = tap(125, toolbar)
    catalog_search = tap(680 if large else width - 94, toolbar)
    filter_button = tap(width - (56 if large else 38), toolbar)
    first_tag = tap(340 if large else 52, 347 if large else height - 166)
    apply_filter = tap(width / 2, 469 if large else height - 46)
    installable = tap(182 if large else 154, toolbar)
    updates = tap(305 if large else 253, toolbar)
    english = tap(width * 0.625, 142 if large else 117)
    server = tap(width - 75, 344 if large else 300)
    address = tap(320 if large else 150, 388 if large else height - 289)
    credential = tap(320 if large else 150, 488 if large else height - 200)
    repositories = tap(width - (56 if large else 38), toolbar)
    field = tap(126, 76 if large else 60)
    cancel = tap(width - 39, 76 if large else 60)
    clear = tap(width - (123 if large else 98), 76 if large else 60)
    more = tap(652 if large else 407, 147 if large else 120)
    choose_extension = tap(300 if large else 120, 665 if large else height - 52)
    close_picker = tap(3, 206)
    language_key = tap(width * 0.208, height - 38)
    numbers_key = tap(62, height - 38)
    w_key = tap(145 if large else 96, height - (274 if large else 216))
    first_candidate = tap(82 if large else 72, height - (338 if large else 273))
    backspace = tap(width - 46, height - (129 if large else 94))
    submit = tap(width * 0.88, height - 38)
    view_all = tap(width - 80, 261 if large else 210)
    result_cover = tap(65 if large else 54, 342 if large else 281)
    demo_query = "雾港"
    demo_sources = ["all", "library", "mangadex", "komga", "local", "demo-a"]
    matching_tag_books = 61 if large else 63
    return (
        Case("browse-catalog-page", "catalog", (next_page,), (("page", 2),)),
        Case("browse-catalog-listing", "catalog", (next_page, tap(158 if large else 135, 128 if large else 106)),
             (("catalog_filter", 1), ("page", 1))),
        Case("browse-picker-select", "catalog", (source_picker, tap(110, 197 if large else 172)),
             (("route", "catalog"), ("source_id", "komga"), ("modal", ""))),
        Case("browse-picker-unconfigured", "catalog", (source_picker, tap(110, 259 if large else 228)),
             (("route", "catalog"), ("source_id", "mangadex"), ("modal_context", "source-picker"))),
        Case("browse-picker-manage-back", "catalog", (source_picker, tap(100, 569 if large else 508), back),
             (("route", "catalog"), ("source_id", "mangadex"), ("modal", ""))),
        Case("browse-filter-include", "catalog", (filter_button, first_tag),
             (("modal_context", "catalog-filter"), ("catalog_tag_options.0.state", 1), ("catalog_filter_result_count", matching_tag_books))),
        Case("browse-filter-exclude", "catalog", (filter_button, first_tag, first_tag),
             (("catalog_tag_options.0.state", -1), ("catalog_filter_result_count", 128 - matching_tag_books))),
        Case("browse-filter-reset-apply", "catalog", (filter_button, first_tag, first_tag, first_tag, apply_filter),
             (("catalog_active_filter_count", 0), ("catalog_result_count", 128), ("modal", ""))),
        Case("browse-filter-unavailable-rating", "catalog",
             (filter_button, tap(width - 145, 247 if large else height - 262)),
             (("catalog_sort_options.2.enabled", False), ("catalog_active_filter_count", 0))),
        Case("browse-catalog-search-edit", "catalog", (catalog_search,),
             (("route", "search"), ("search_scope", "mangadex"), ("modal", "keyboard"), ("keyboard_target", "search"))),
        Case("browse-catalog-search-cancel", "catalog", (next_page, catalog_search, cancel),
             (("route", "catalog"), ("page", 2), ("source_id", "mangadex"), ("modal", ""))),
        Case("browse-sources-language", "sources", (installable, english),
             (("source_language", "en"), ("available_count", 1), ("available_sources.0.id", "mangadex"))),
        Case("browse-sources-installed-disabled", "sources",
             (installable, english, tap(width - 75, 217 if large else 192)),
             (("installed_count", 7), ("available_sources.0.installed", True), ("message", ""))),
        Case("browse-sources-install", "sources", (installable, tap(width - 75, 375 if large else 335)),
             (("installed_count", 8), ("available_sources.2.id", "demo-source-001"), ("available_sources.2.installed", True))),
        Case("browse-sources-update-one", "sources", (updates, tap(width - 75, 220 if large else 192)),
             (("source_update_count", 1), ("source_filter", 2))),
        Case("browse-sources-update-all", "sources", (updates, tap(width - 75, 140 if large else 120, 2600)),
             (("source_update_count", 0), ("message", "已保存"))),
        Case("browse-sources-toast-next-tap", "sources",
             (updates, tap(width - 75, 140 if large else 120, 2600), tap(width - 3, 115)),
             (("source_update_count", 0), ("message", ""))),
        Case("browse-sources-address-keyboard", "sources", (server, address),
             (("modal", "keyboard"), ("keyboard_target", "server-address"), ("keyboard_mode", "english"))),
        Case("browse-sources-address-cancel", "sources", (server, address, tap(width - 45, height - (457 if large else 371))),
             (("modal_context", "server-configuration"), ("modal_items.0.value", ""))),
        Case("browse-sources-credentials", "sources", (server, credential), (("modal_context", "server-credentials"),)),
        Case("browse-repositories", "sources", (repositories,), (("modal_context", "repository-list"),)),
        Case("browse-repositories-close", "sources", (repositories, tap(width - (194 if large else 65), 292 if large else height - 175)),
             (("route", "sources"), ("modal", ""))),
        Case("search-scope-more", "search", (more,),
             (("modal_context", "search-scope:results"), ("modal_item_ids", demo_sources))),
        Case("search-scope-select", "search", (more, choose_extension),
             (("modal", ""), ("query", demo_query), ("search_scope", "demo-a"), ("search_group_states.0.id", "demo-a"))),
        Case("search-scope-edit-draft", "search", (field, w_key, more, choose_extension),
             (("modal", "keyboard"), ("keyboard_composition", "w"), ("search_scope", "demo-a"),
              ("search_group_states.length", 4), ("search_local_matches.length", 3 if large else 2))),
        Case("search-scope-edit-cancel", "search", (field, w_key, more, choose_extension, cancel),
             (("modal", ""), ("query", demo_query), ("search_scope", "all"), ("search_group_states.length", 4))),
        Case("search-keyboard-mode-after-picker", "search", (field, language_key, numbers_key, more, close_picker, numbers_key),
             (("modal", "keyboard"), ("keyboard_mode", "english"), ("keyboard_text", demo_query))),
        Case("search-keyboard-candidate", "search", (field, w_key, first_candidate),
             (("modal", "keyboard"), ("keyboard_text", demo_query + "我"), ("keyboard_composition", ""))),
        Case("search-keyboard-backspace-submit", "search", (field, w_key, first_candidate, backspace, submit),
             (("modal", ""), ("query", demo_query), ("search_group_states.0.count", 3))),
        Case("search-keyboard-clear", "search", (field, w_key, clear),
             (("modal", "keyboard"), ("query", ""), ("keyboard_text", ""), ("keyboard_composition", ""))),
        Case("search-open-result", "search", (result_cover,), (("route", "detail"), ("current_book", "8:mangadex:demo-00"))),
        Case("search-open-local-match-then-scope", "search",
             (field, more, choose_extension, tap(155 if large else 137, 277 if large else 225), back,
              tap(315 if large else 210, 147 if large else 120)),
             (("route", "search"), ("modal", ""), ("keyboard_target", ""), ("search_scope", "mangadex"),
              ("search_group_states.length", 1), ("search_group_states.0.id", "mangadex"))),
        Case("search-view-all", "search", (view_all,),
             (("route", "catalog"), ("query", demo_query), ("source_id", "mangadex"), ("visible_catalog.length", 3))),
        Case("search-view-all-back", "search", (view_all, back),
             (("route", "search"), ("query", demo_query), ("search_scope", "all"), ("search_group_states.length", 4),
              ("search_group_states.0.count", 3))),
        Case("search-view-all-system-back", "search", (view_all, REPLAY.intent("app", "back")),
             (("route", "search"), ("query", demo_query), ("search_group_states.0.count", 3))),
        Case("search-production-unconfigured-scopes", "search", (tap(340 if large else 313, 147 if large else 120),),
             (("search_sources.length", 2), ("search_sources.0.id", "mangadex"), ("search_sources.1.id", "local")), demo=False),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=REPLAY.PROFILES, action="append")
    parser.add_argument("--only", action="append", help="Run scene IDs containing any of these strings")
    args = parser.parse_args()
    profiles = args.device or REPLAY.PROFILES
    jobs = [(profile, case) for profile in profiles for case in cases(*REPLAY.PROFILES[profile])
            if not args.only or any(term in case.name for term in args.only)]
    if not jobs:
        parser.error("No scenes match the requested selection")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda job: REPLAY.verify(binary, output, *job), jobs))
    failures = sum(bool(result["errors"]) for result in results)
    (output / "report.json").write_text(json.dumps({"passed": len(results) - failures, "failed": failures,
                                                    "cases": results}, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    print(f"Verified {len(results) - failures}/{len(results)} pointer scenarios; report: {output / 'report.json'}")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
