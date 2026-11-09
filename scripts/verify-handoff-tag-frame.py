#!/usr/bin/env python3
"""Capture fresh C7 long-tag frames without repeating the prior full-page scope.

Eight native captures comprise two original reference states, four long neutral
or excluded candidates, and two source-configuration prerequisites. The existing
affected-tag driver supplies the real HTTP metadata variant and common guards.
No complete-suite or clean-round credit is granted by this bounded driver.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


REFERENCE_IDS = ("pw-source--filter", "sc-browse--filter")
PROFILES = ("pw5", "scribe")
FRAME_IDS = tuple(f"{device}--c7-tag-long-{state}"
                  for device in PROFILES for state in ("neutral", "excluded"))
SETUP_IDS = tuple(f"{device}--c7-tag-frame--fixture-seed" for device in PROFILES)
CASE_IDS = (*REFERENCE_IDS, *SETUP_IDS, *FRAME_IDS)
CALLER_FRAMES = {"pw5": (22, 583, 596, 625), "scribe": (310, 597, 874, 643)}


def load_affected_driver() -> Any:
    path = Path(__file__).resolve().with_name("verify-handoff-tags.py")
    specification = importlib.util.spec_from_file_location("handoff_tag_frame_reused_tags", path)
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load the existing affected-tag implementation: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _failure(suite: Any, name: str, error: BaseException) -> None:
    case = {"name": name, "passed": False, "manual_review_required": False,
            "c7_tag_frame_qualification": True, "partial_run": True,
            "checks": [{"description": "The bounded fresh frame stage completed without a driver error",
                        "passed": False, "actual": f"{type(error).__name__}: {error}"}]}
    suite.cases.append(case)
    suite.persist_case(case)


def _retain(source: Any, suite: Any, case: dict[str, Any], label: str, payload: Any) -> None:
    path = suite.output / "fixtures" / f"{case['name']}--{label}.json"
    source.save_json(path, payload)
    case.setdefault("evidence_hashes", {})[f"c7-frame:{label}"] = {
        "path": str(path), "sha256": source.file_hash(path)}


def _mark(suite: Any, case: dict[str, Any], role: str) -> None:
    case["c7_tag_frame_qualification"] = True
    case["c7_tag_frame_role"] = role
    case["qualification_scope"] = "Fresh long neutral/excluded frames and two original references only; ordinary full-page component evidence is not replayed or relabeled."
    suite.persist_case(case)


def _variant_evidence(variant: Any) -> dict[str, Any]:
    observation = variant.evidence()
    enabled = observation.pop("enabled_after_normal_full_pages", variant.enabled)
    observation.update({"variant_enabled_for_fresh_frame_scope": enabled,
                        "normal_full_page_coverage_executed_this_run": False,
                        "reused_implementation": "LongMetadataVariant from the unchanged verify-handoff-tags.py"})
    return observation


def _assert_long_metadata(affected: Any, suite: Any, case: dict[str, Any], *, excluded: bool) -> list[dict[str, Any]]:
    tags = case["state"].get("catalog_tag_options", [])
    suite.check(case, "The fresh frame is based on the actual HTTP 34-tag collection with one distinguishing long provider label",
                len(tags) == affected.NORMAL_TAG_COUNT
                and len({tag.get("id") for tag in tags}) == affected.NORMAL_TAG_COUNT
                and sum(tag.get("title") == affected.LONG_TAG for tag in tags) == 1
                and bool(tags) and tags[0].get("title") == affected.LONG_TAG,
                {"tag_count": len(tags), "first_tag": tags[0] if tags else None})
    if not tags:
        raise ValueError("The actual long HTTP tag is unavailable")
    target_id = tags[0].get("id")
    suite.check(case, "Only the actual first long tag is excluded; every other current tag remains neutral",
                all(tag.get("state") == (-1 if excluded and tag.get("id") == target_id else 0)
                    for tag in tags),
                {"target": target_id, "states": [{"id": tag.get("id"), "state": tag.get("state")} for tag in tags]})
    return tags


def _profile(affected: Any, source: Any, pixels: Any, suite: Any, device: str) -> dict[str, Any]:
    with source.KomgaFixture(stress=True) as provider:
        seed_name = f"{device}--c7-tag-frame"
        data, _ = suite.seed_provider(seed_name, provider, device)
        seed = affected._case(suite, f"{seed_name}--fixture-seed")
        _mark(suite, seed, "source-configuration-prerequisite")
        variant = affected.LongMetadataVariant(source, suite, provider, f"{device}--c7-frame")
        try:
            # C7 reuses the real HTTP variant, not its former full-page phase.
            variant.enable()
            neutral_name = f"{device}--c7-tag-long-neutral"
            neutral = suite.capture(neutral_name, "sources", device=device, demo=False, offline=False, data=data,
                                    replay=source.Replay().intent("sources", "open", "komga", wait=1400)
                                    .intent("catalog", "filter-menu").intent("catalog", "reset-filter"),
                                    expected_route="catalog", modal="choices", context="catalog-filter")
            _mark(suite, neutral, "fresh-long-neutral")
            suite.expect(neutral, "source_id", "komga")
            suite.expect(neutral, "catalog_count", 20)
            suite.expect(neutral, "source_needs_setup", False)
            tags = _assert_long_metadata(affected, suite, neutral, excluded=False)
            boxes = pixels.observe_neutral(suite, neutral, stress_paged=True, full_width_first=True)
            expected_frame = CALLER_FRAMES[device]
            inspected = neutral.get("c7_full_frame_pixels", {})
            caller = tuple(inspected.get("expected_ltrb", ()))
            complete = (caller == expected_frame and bool(boxes)
                        and all(abs(actual - expected) <= 1 for actual, expected in zip(boxes[0], caller)))
            suite.check(neutral, "The first long chip is checked against its complete source-defined caller frame, not a connected fragment",
                        complete,
                        {"caller_frame": list(expected_frame), "observed_boxes": [list(box) for box in boxes]})
            suite.persist_case(neutral)
            if not complete:
                raise ValueError("The complete caller-native long frame is unavailable; no fragment or estimated tap is substituted")
            left, top, right, bottom = caller
            target = affected._target(suite, neutral, {
                "rectangle": [left, top, right - left, bottom - top],
                "hit": [(left + right) / 2, (top + bottom) / 2]})
            if target["title"] != affected.LONG_TAG:
                raise ValueError("The caller-native first frame does not belong to the served long provider tag")
            neutral["independent_original_review"] = True
            neutral["long_tail_visibility"] = {"status": "independent_original_review_required",
                                               "distinguishing_suffix": affected.LONG_SUFFIX,
                                               "model_text_is_not_rendered_text_proof": True}
            neutral.setdefault("visible_ui_requirements", []).append(
                "Independently inspect the unmodified long neutral PNG for its complete caller-native solid frame, bounded truncation and distinguishing suffix.")
            _retain(source, suite, neutral, "actual-long-http", _variant_evidence(variant))
            suite.save_provider(neutral_name, provider, neutral)
            suite.persist_case(neutral)

            excluded_name = f"{device}--c7-tag-long-excluded"
            replay = (source.Replay().intent("sources", "open", "komga", wait=1400)
                      .intent("catalog", "filter-menu").intent("catalog", "reset-filter")
                      .tap(*target["hit"], wait=200).tap(*target["hit"], wait=200))
            excluded = suite.capture(excluded_name, "sources", device=device, demo=False, offline=False, data=data,
                                     replay=replay, expected_route="catalog", modal="choices", context="catalog-filter")
            _mark(suite, excluded, "fresh-long-excluded-pointer")
            actual = _assert_long_metadata(affected, suite, excluded, excluded=True)
            suite.check(excluded, "The two fresh long frames retain the same actual provider tag identities",
                        [tag.get("id") for tag in actual] == [tag.get("id") for tag in tags])
            actions = source.read_json(Path(excluded["actions"]))
            pointers = [action for action in actions if action.get("section") == "pointer"]
            suite.check(excluded, "The excluded frame uses exactly two real pointer taps at the complete neutral caller frame",
                        len(pointers) == 6 and excluded.get("pointer_events") == 6
                        and sum(action.get("action") == "press" for action in pointers) == 2
                        and sum(action.get("action") == "release" for action in pointers) == 2
                        and not any(action.get("section") == "catalog" and action.get("action") == "filter-choice"
                                    for action in actions), pointers)
            origin = {"case_id": neutral["name"], "original_screenshot": neutral["screenshot"],
                      "original_sha256": source.file_hash(Path(neutral["screenshot"])),
                      "caller_native_frame": list(expected_frame), "actual_target": target,
                      "actual_pointer_actions": pointers}
            excluded["c7_excluded_pointer_origin"] = origin
            excluded["independent_original_review"] = True
            excluded["long_tail_visibility"] = neutral["long_tail_visibility"]
            excluded.setdefault("visible_ui_requirements", []).append(
                "Independently inspect the actual excluded original PNG for the full caller frame, normal dash gaps, gray cross, label strike and distinguishing suffix; no selected-width growth is required for a capped long chip.")
            evidence = _variant_evidence(variant)
            suite.check(excluded, "Both fresh frames are backed by actual long HTTP JSON and unambiguous recorded request identities",
                        len(evidence["responses"]) >= 2 and not evidence["errors"]
                        and all(type(item.get("request_index_zero_based")) is int for item in evidence["responses"]), evidence)
            source_records = suite.database_rows(excluded, "SELECT document FROM sources WHERE id='komga'")
            config = json.loads(source_records[0][0]) if source_records else {}
            suite.check(excluded, "Ordinary source actions really verify the configured public loopback provider",
                        config.get("enabled") is True and config.get("base_url", "").rstrip("/") == provider.url
                        and config.get("token") == "handoff-public-fixture-key"
                        and config.get("options", {}).get("_connection_verified") is True,
                        {"enabled": config.get("enabled"), "base_url": config.get("base_url"),
                         "public_fixture_key_matches": config.get("token") == "handoff-public-fixture-key",
                         "verified": config.get("options", {}).get("_connection_verified")})
            _retain(source, suite, excluded, "actual-long-http", evidence)
            _retain(source, suite, excluded, "caller-pointer-origin", origin)
            suite.save_provider(excluded_name, provider, excluded)
            suite.persist_case(excluded)
            return {"setup_case_id": seed["name"], "review_case_ids": [neutral_name, excluded_name],
                    "actual_tag_count": len(actual), "complete_caller_frame": list(expected_frame),
                    "normal_full_collection_executed_this_run": False, "provider_response_evidence": evidence}
        finally:
            variant.restore()


def run_frames(affected: Any, source: Any, pixels: Any, suite: Any) -> dict[str, Any]:
    entries = [(board, entry) for board in suite.scope["pageStateMatrix"]
               for entry in [board["default"], *board["states"]]]
    suite.reference_ids = [entry["id"] for _, entry in entries]
    result: dict[str, Any] = {"profiles": {}, "errors": [], "normal_full_collection_executed_this_run": False}
    for identifier in REFERENCE_IDS:
        try:
            pairs = [(board, entry) for board, entry in entries if entry["id"] == identifier]
            if len(pairs) != 1:
                raise ValueError(f"The original fixed reference ID is not unique: {identifier}")
            suite.reference_case(*pairs[0])
            _mark(suite, affected._case(suite, identifier), "original-reference-neutral")
        except Exception as error:
            result["errors"].append(f"{identifier}: {type(error).__name__}: {error}")
            _failure(suite, f"c7-frame-reference-{identifier}", error)
    for device in PROFILES:
        try:
            result["profiles"][device] = _profile(affected, source, pixels, suite, device)
        except Exception as error:
            result["errors"].append(f"{device}: {type(error).__name__}: {error}")
            _failure(suite, f"{device}--c7-frame-completion", error)
    return result


def _counts(suite: Any) -> dict[str, Any]:
    captures = [case for case in suite.cases if case.get("screenshot")]
    names = [case["name"] for case in captures]
    prerequisites = [case["name"] for case in captures if case.get("prerequisite")]
    manual = [case["name"] for case in captures if case.get("manual_review_required")]
    return {"expected_capture_ids": list(CASE_IDS), "expected_review_ids": [*REFERENCE_IDS, *FRAME_IDS],
            "expected_prerequisite_ids": list(SETUP_IDS), "capture_attempts": len(captures),
            "actual_original_png_count": sum(Path(case["screenshot"]).is_file() for case in captures),
            "manual_review_case_ids": manual, "prerequisite_case_ids": prerequisites,
            "missing_capture_ids": sorted(set(CASE_IDS) - set(names)),
            "unexpected_capture_ids": sorted(set(names) - set(CASE_IDS)),
            "duplicate_capture_ids": sorted({name for name in names if names.count(name) != 1}),
            "failed_case_ids": [case["name"] for case in suite.cases if not case.get("passed")]}


def main() -> int:
    source = None
    suite = None
    execution: dict[str, Any] = {}
    result: dict[str, Any] = {"profiles": {}, "errors": []}
    raw_exit = None
    raw_finish_error = None
    try:
        affected = load_affected_driver()
        source = affected.load_source()
        args = affected._arguments(source)
        affected._fresh_output(args.output)
        driver = Path(__file__).resolve()
        affected_path = driver.with_name("verify-handoff-tags.py")
        main_path = driver.with_name("verify-handoff-ui.py")
        pixels_path = driver.with_name("handoff_tag_pixels.py")
        paths = {"driver": driver, "affected_tag_driver": affected_path,
                 "main_harness": main_path, "pixel_helper": pixels_path,
                 **{name: driver.with_name(name) for name in (
                     "verify-ui.py", "verify-fullscreen.py", "handoff_c4_business.py",
                     "handoff_c5_ui.py", "handoff_c5_reader.py")},
                 "capture_plan": args.plan, "reference_scope": args.scope}
        before = affected._fingerprints(source, paths)
        execution = {"driver": str(driver), "main_harness": str(main_path), "pixel_helper": str(pixels_path),
                     "affected_tag_driver": str(affected_path), "frozen_inputs_before": before,
                     "frozen_input_count": len(paths), "argv": [sys.executable, str(driver), *sys.argv[1:]],
                     "expected_binary_sha256": args.expected_sha256.lower(),
                     "expected_source_sha256": args.expected_source_sha256.lower(),
                     "partial_run": True, "normal_full_collection_executed_this_run": False,
                     **{f"{name}_sha256_before": before[name]["sha256"]
                        for name in ("driver", "main_harness", "pixel_helper", "affected_tag_driver")}}
        suite = source.Suite(args)
        try:
            source.save_json(suite.output / "tag-frame-execution.json", execution)
            import handoff_tag_pixels as pixels
            if Path(pixels.__file__).resolve() != pixels_path:
                raise ValueError("The frame observer is not the helper beside the frozen driver")
            result = run_frames(affected, source, pixels, suite)
            pixels.assess_frame_cases(suite)
            result["frame_pixel_assessment_executed"] = True
        except Exception as error:
            result.setdefault("errors", []).append(f"{type(error).__name__}: {error}")
            _failure(suite, "c7-frame-driver-completion", error)
        finally:
            try:
                raw_exit = suite.finish_manifest()
            except Exception as error:
                raw_finish_error = f"{type(error).__name__}: {error}"
        after = affected._fingerprints(source, paths)
        execution.update({"frozen_inputs_after": after,
                          **{f"{name}_sha256_after": after[name]["sha256"]
                             for name in ("driver", "main_harness", "pixel_helper", "affected_tag_driver")}})
        unchanged = (len(before) == 11 and set(before) == set(after)
                     and all(before[name]["sha256"] is not None and before[name] == after[name] for name in before))
        counts = _counts(suite)
        raw_path = suite.output / "manifest.json"
        raw = source.read_json(raw_path) if raw_path.is_file() else {}
        partial = (raw.get("full_capture_suite_completed") is False
                   and raw.get("reference_coverage_complete") is False
                   and raw.get("review", {}).get("clean_round") is False)
        passed = (not result.get("errors") and result.get("frame_pixel_assessment_executed") is True
                  and set(result.get("profiles", {})) == set(PROFILES) and unchanged and partial
                  and raw_finish_error is None and counts["capture_attempts"] == 8
                  and counts["actual_original_png_count"] == 8
                  and set(counts["prerequisite_case_ids"]) == set(SETUP_IDS)
                  and set(counts["manual_review_case_ids"]) == set((*REFERENCE_IDS, *FRAME_IDS))
                  and not any(counts[key] for key in ("missing_capture_ids", "unexpected_capture_ids",
                                                      "duplicate_capture_ids", "failed_case_ids")))
        report = {"schema_version": 1, "kind": "c7-fresh-long-tag-frame-qualification",
                  "run_id": suite.run_id, "round": args.round, "partial_run": True,
                  "qualification_success": bool(passed), "full_capture_suite_completed": False,
                  "clean_round": False, "clean_round_streak": 0, "accepted": False, "clean_round_credit": 0,
                  "execution": execution, "execution_inputs_unchanged": unchanged,
                  "binary_sha256": suite.binary_hash, "source_sha256": suite.source["sha256"],
                  "reference_sha256": suite.design["sha256"], "capture_plan_sha256": suite.design["plan_sha256"],
                  "actual_counts": counts, "frame_scope": result,
                  "normal_full_collection_executed_this_run": False,
                  "separate_component_scope": "Ordinary 34-tag capacity, every page and the final-page coverage remain separately bound prior component evidence; this driver does not rerun or relabel them.",
                  "long_suffix_review": "Every fresh long candidate requires independent original-PNG review; HTTP/model text is not proof of visible suffix rendering.",
                  "raw_main_manifest": {"path": str(raw_path), "exit_code": raw_exit,
                                        "sha256": source.file_hash(raw_path) if raw_path.is_file() else None,
                                        "finish_error": raw_finish_error, "full_suite_gate_retained": partial},
                  "exit_code_scope": "Zero means only the fresh-frame qualification assertions passed. The retained main manifest remains partial/exit1 and grants no clean-round credit."}
        source.save_json(suite.output / "tag-frame-qualification.json", report)
        print(f"C7 fresh frames: {counts['capture_attempts']} actual capture attempts, {len(counts['manual_review_case_ids'])} review and {len(counts['prerequisite_case_ids'])} setup; focused success={bool(passed)}; partial=true; clean_round=false; raw full-suite exit={raw_exit}", flush=True)
        return 0 if passed else 1
    except Exception as error:
        if source is not None and suite is not None:
            source.save_json(suite.output / "tag-frame-driver-error.json", {
                "partial_run": True, "clean_round": False, "execution": execution,
                "error": f"{type(error).__name__}: {error}", "raw_manifest_finish_error": raw_finish_error})
        print(f"Fresh tag-frame qualification failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
