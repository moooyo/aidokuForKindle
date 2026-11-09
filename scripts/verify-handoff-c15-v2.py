#!/usr/bin/env python3
"""Run the fixed 72-frame C15 v2 affected-native-UI qualification on Linux.

The unchanged v1 driver supplies dependency retention and raw-manifest checks.
The final source, executable and independently frozen v2 plan hashes are mandatory.
Original native frames require manual review and earn no complete-round credit.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import re
import sys
from typing import Any


FROZEN_V1_SHA256 = {
    "verify-handoff-c15.py": "3d704a70c1b998878c9e8e0ba921e5d243c8d36ce71d5fb55158b7cca8df7ff1",
    "handoff_c15_visual.py": "e1e09623e39363bf1b62c9c4e0b806bb32f54b0669ccc167ad0f84a49f5de7bd",
    "c15_qualification_plan": "d1e817ab72f82cee255e95f4a81eee1f1e4337eab3795a6bf3e8d904454c1019",
}
ADDITIONAL_SCRIPTS = ("verify-handoff-c15-v2.py", "handoff_c15_visual_v2.py", "handoff_c15_artwork.py")


def load(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().with_name(filename)
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load the frozen C15 v2 qualification input: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def _bind(base: Any, source: Any, guard: Any, helper: Any, directory: Path) -> None:
    base._bind_modules(source, guard, helper.v1, directory)
    for filename, module in (
        ("verify-handoff-c15.py", base), ("handoff_c15_visual_v2.py", helper),
        ("handoff_c15_artwork.py", helper.artwork),
    ):
        actual = getattr(module, "__file__", None)
        if actual is None or Path(actual).resolve() != (directory / filename).resolve():
            raise ValueError(f"An actual C15 v2 dependency is outside its frozen script path: {filename}")
    if helper.c10 is not source.c10_search or helper.c9 is not source.c10_search.c9:
        raise ValueError("C15 v2 must use the same actual C9/C10 modules as the unchanged full harness")


def main() -> int:
    original_argv, invocation = sys.argv, list(sys.argv)
    source, suite, args = None, None, None
    output_is_fresh = False
    before, after, retained, snapshot_paths, snapshots_before, snapshots_after = {}, {}, {}, {}, {}, {}
    errors: list[dict[str, str]] = []
    result, negative, raw_exit, finish_error, raw_path, raw_hash = None, None, None, None, None, None
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--negative-root", type=Path, required=True)
        parser.add_argument("--expected-qualification-plan-sha256", required=True)
        extra, inherited = parser.parse_known_args(invocation[1:])
        if re.fullmatch(r"[0-9a-fA-F]{64}", extra.expected_qualification_plan_sha256) is None:
            raise ValueError("C15 v2 requires --expected-qualification-plan-sha256 with the authorized 64-hex frozen plan hash")
        base = load("c15_v2_frozen_v1_driver", "verify-handoff-c15.py")
        guard = base.load("c15_v2_existing_guard", "verify-handoff-tags.py")
        source = guard.load_source()
        try:
            sys.argv = [invocation[0], *inherited]
            args = guard._arguments(source)
        finally:
            sys.argv = original_argv
        if args.timeout != 90 or args.settled_reader_tail_ms != 5000:
            raise ValueError("C15 v2 retains the 90-second per-app timeout and 5000-ms settled-reader tail")
        guard._fresh_output(args.output)
        output_is_fresh = True
        directory = Path(__file__).resolve().parent
        helper = load("handoff_c15_visual_v2", "handoff_c15_visual_v2.py")
        _bind(base, source, guard, helper, directory)
        paths, destinations = base._input_paths(source, helper.v1, args, extra.negative_root, directory)
        paths.update({name: directory / name for name in ADDITIONAL_SCRIPTS})
        destinations.update({name: Path("scripts") / name for name in ADDITIONAL_SCRIPTS})
        plan_path = directory.parent / "docs/review/phase3-c15-affected-qualification-v2-plan.json"
        paths["c15_v2_qualification_plan"] = plan_path
        destinations["c15_v2_qualification_plan"] = Path("plans") / plan_path.name
        if len(set(destinations.values())) != len(destinations):
            raise ValueError("Every C15 v2 frozen input must retain a distinct snapshot path")
        before = guard._fingerprints(source, paths)
        if any(record["sha256"] is None for record in before.values()):
            raise ValueError("Every actual C15 v2 script, plan, reference and preserved negative input must be present")
        if before["c15_v2_qualification_plan"]["sha256"] != extra.expected_qualification_plan_sha256.lower():
            raise ValueError("The actual C15 v2 plan does not match its independently frozen authorized SHA256")
        for name, expected in FROZEN_V1_SHA256.items():
            if before[name]["sha256"] != expected:
                raise ValueError(f"C15 v2 must retain the original qualification input's exact bytes: {name}")
        original_plan = source.read_json(paths["c15_qualification_plan"])
        plan = source.read_json(plan_path)
        helper.validate_plan(plan, original_plan)
        for name, field in (
            ("verify-handoff-ui.py", "frozen_full_harness_sha256"),
            ("capture_plan", "frozen_full_plan_sha256"), ("reference_scope", "frozen_reference_scope_sha256"),
        ):
            if before[name]["sha256"] != original_plan[field] or plan[field] != original_plan[field]:
                raise ValueError(f"C15 v2 cannot change the frozen full harness, plan or scope: {name}")
        for name, field in (
            ("verify-handoff-c15.py", "frozen_v1_entrypoint_sha256"),
            ("handoff_c15_visual.py", "frozen_v1_helper_sha256"),
            ("c15_qualification_plan", "frozen_v1_plan_sha256"),
        ):
            if plan[field] != FROZEN_V1_SHA256[name]:
                raise ValueError(f"The C15 v2 plan changed an authorized v1 identity: {field}")
        suite = source.Suite(args)
        suite.reference_ids = [entry["id"] for board in suite.scope["pageStateMatrix"]
                               for entry in [board["default"], *board["states"]]]
        if len(suite.reference_ids) != 101 or len(set(suite.reference_ids)) != 101:
            raise ValueError("The original full reference scope must retain its 101 distinct IDs")
        raw_path = suite.output / "manifest.json"
        frozen_negative_root = suite.output / "execution-inputs" / "preserved-c10-negatives"
        execution_before = {
            "argv": [sys.executable, *invocation], "inputs": before,
            "execution_input_snapshots": retained, "snapshot_inputs": snapshots_before,
            "expected_source_sha256": args.expected_source_sha256.lower(),
            "expected_binary_sha256": args.expected_sha256.lower(),
            "expected_qualification_plan_sha256": extra.expected_qualification_plan_sha256.lower(),
            "expected_case_ids": list(helper.CASE_IDS), "manual_case_ids": list(helper.MANUAL_IDS),
            "prerequisite_case_ids": list(helper.PREREQUISITE_IDS),
            "original_negative_root": str(extra.negative_root.resolve()), "frozen_negative_root": str(frozen_negative_root),
            "all_input_bytes_retained": False, "partial_run": True, "clean_round_credit": 0,
        }
        stage = "v2-execution-before"
        try:
            source.save_json(suite.output / "c15-v2-execution-before.json", execution_before)
            stage = "v2-input-freeze"
            base._freeze_inputs(source, suite, paths, destinations, before, retained, snapshot_paths)
            snapshots_before = guard._fingerprints(source, snapshot_paths)
            if (set(before) != set(retained) or set(before) != set(snapshots_before)
                    or any(snapshots_before[name]["sha256"] != before[name]["sha256"]
                           or retained[name].get("bytes_retained") is not True for name in before)):
                raise ValueError("Every C15 v2 execution input must retain its exact bytes before native capture")
            execution_before.update({"snapshot_inputs": snapshots_before, "all_input_bytes_retained": True})
            source.save_json(suite.output / "c15-v2-execution-before.json", execution_before)
            stage = "v2-negative-controls"
            negative = helper.v1.c11.qualify_old_negatives(frozen_negative_root)
            source.save_json(suite.output / "c15-v2-negative-controls.json", negative)
            if negative.get("passed") is not True:
                raise ValueError("The unchanged complete shared-glyph oracle did not reject both preserved C10 negative frames")
            stage = "v2-native-union"
            api = suite.c4_api()
            api.oracle = source.oracle
            result = helper.run(suite, api, frozen_negative_root)
            errors.extend(result.get("errors", []))
        except Exception as error:
            base._failure(suite, errors, stage, error)
        finally:
            if execution_before["all_input_bytes_retained"] is not True:
                try:
                    snapshots_before = guard._fingerprints(source, snapshot_paths)
                    execution_before.update({"snapshot_inputs": snapshots_before, "errors": errors})
                    source.save_json(suite.output / "c15-v2-execution-before.json", execution_before)
                except Exception as error:
                    base._failure(suite, errors, "v2-input-freeze-report", error)
            try:
                helper.mark_cases(suite, plan, original_plan)
            except Exception as error:
                base._failure(suite, errors, "v2-review-instructions", error)
            try:
                raw_exit = suite.finish_manifest()
            except Exception as error:
                finish_error = f"{type(error).__name__}: {error}"
                errors.append({"stage": "v2-raw-manifest", "error": finish_error})
        try:
            after = guard._fingerprints(source, paths)
            snapshots_after = guard._fingerprints(source, snapshot_paths)
        except Exception as error:
            errors.append({"stage": "v2-execution-after", "error": f"{type(error).__name__}: {error}"})
        unchanged = (set(before) == set(after)
                     and all(before[name]["sha256"] is not None and before[name] == after[name] for name in before))
        snapshots_unchanged = (set(before) == set(retained) == set(snapshots_before) == set(snapshots_after)
                               and all(snapshots_before[name] == snapshots_after[name]
                                       and snapshots_before[name]["sha256"] == before[name]["sha256"]
                                       and retained[name].get("bytes_retained") is True for name in before))
        raw, binary_after = {}, None
        try:
            if raw_path.is_file():
                raw_hash = source.file_hash(raw_path)
                raw = source.read_json(raw_path)
            binary_after = source.file_hash(suite.binary)
        except Exception as error:
            errors.append({"stage": "v2-raw-identity", "error": f"{type(error).__name__}: {error}"})
        counts = base._counts(source, suite, helper)
        partial = base._raw_partial_boundary(raw, suite, source, raw_exit)
        reference_coverage = (raw.get("reference_coverage") == suite.coverage
                              == {identifier: identifier for identifier in helper.REFERENCE_CASE_IDS})
        identity = (
            suite.source["sha256"] == args.expected_source_sha256.lower()
            and suite.binary_hash == args.expected_sha256.lower() == binary_after
            and raw.get("source_sha256") == raw.get("source_sha256_after") == suite.source["sha256"]
            and raw.get("source_unchanged") is True and raw.get("binary_unchanged") is True
            and raw.get("binary_sha256") == suite.binary_hash and raw.get("reference_sha256") == suite.design["sha256"]
            and raw.get("reference_scope_sha256") == suite.design["scope_sha256"] == before["reference_scope"]["sha256"]
            and raw.get("capture_plan_sha256") == suite.design["plan_sha256"] == before["capture_plan"]["sha256"]
            and raw.get("harness_sha256") == before["verify-handoff-ui.py"]["sha256"]
            and before["c15_v2_qualification_plan"]["sha256"] == extra.expected_qualification_plan_sha256.lower()
        )
        art = result.get("same_case_artwork") if isinstance(result, dict) else None
        artwork_passed = (
            isinstance(art, dict) and art.get("completed") is True and art.get("passed") is True
            and art.get("case_ids") == list(helper.ARTWORK_CASE_IDS)
            and art.get("prerequisite_case_ids") == list(helper.ARTWORK_PREREQUISITE_IDS)
        )
        passed = (
            not errors and isinstance(result, dict) and result.get("completed") is True
            and negative is not None and negative.get("passed") is True and artwork_passed
            and unchanged and snapshots_unchanged and identity and partial and reference_coverage and finish_error is None
            and counts["capture_attempts"] == counts["actual_original_png_count"] == 72
            and counts["manual_review_captures"] == 59 and counts["prerequisite_captures"] == 13
            and set(counts["manual_case_ids"]) == set(helper.MANUAL_IDS)
            and set(counts["prerequisite_case_ids"]) == set(helper.PREREQUISITE_IDS)
            and not any(counts[field] for field in (
                "missing_case_ids", "unexpected_case_ids", "duplicate_case_ids", "failed_case_ids",
                "missing_original_png_case_ids", "capture_role_mismatch_case_ids",
                "capture_identity_mismatch_case_ids", "altered_capture_evidence",
            ))
        )
        source.save_json(suite.output / "c15-v2-execution-after.json", {
            "inputs": after, "snapshot_inputs": snapshots_after,
            "execution_inputs_unchanged": unchanged, "execution_snapshots_unchanged": snapshots_unchanged,
            "source_sha256_after": raw.get("source_sha256_after"), "binary_sha256_after": binary_after,
            "raw_manifest_sha256": raw_hash, "raw_manifest_exit_code": raw_exit,
            "finish_error": finish_error, "errors": errors, "partial_run": True, "clean_round_credit": 0,
        })
        visual_plan = {"required_visual_outcomes": [*original_plan["required_visual_outcomes"], *plan["required_visual_outcomes"]]}
        report = {
            "schema_version": 1, "kind": "c15-v2-affected-native-ui-qualification", "round": args.round,
            "run_id": suite.run_id, "qualification_success": bool(passed), "expected_captures": 72,
            "actual_captures": counts["capture_attempts"], "manual_review_captures": counts["manual_review_captures"],
            "prerequisite_captures": counts["prerequisite_captures"], "expected_case_ids": list(helper.CASE_IDS),
            "manual_case_ids": list(helper.MANUAL_IDS), "prerequisite_case_ids": list(helper.PREREQUISITE_IDS),
            "actual_counts": counts, "result": result, "preserved_negative_controls": negative, "errors": errors,
            "same_case_artwork": art, "same_case_artwork_passed": artwork_passed,
            "original_negative_root": str(extra.negative_root.resolve()), "frozen_negative_root": str(frozen_negative_root),
            "source_sha256": suite.source["sha256"], "binary_sha256": suite.binary_hash,
            "source_sha256_after": raw.get("source_sha256_after"), "binary_sha256_after": binary_after,
            "reference_sha256": suite.design["sha256"], "reference_scope_sha256": suite.design["scope_sha256"],
            "capture_plan_sha256": suite.design["plan_sha256"], "harness_sha256": before["verify-handoff-ui.py"]["sha256"],
            "qualification_entrypoint_sha256": before["verify-handoff-c15-v2.py"]["sha256"],
            "qualification_helper_sha256": before["handoff_c15_visual_v2.py"]["sha256"],
            "same_case_artwork_helper_sha256": before["handoff_c15_artwork.py"]["sha256"],
            "qualification_plan_sha256": before["c15_v2_qualification_plan"]["sha256"],
            "frozen_v1_sha256": FROZEN_V1_SHA256,
            "dependency_sha256": {name: record["sha256"] for name, record in before.items()},
            "inputs_before": before, "inputs_after": after, "execution_input_snapshots": retained,
            "snapshot_inputs_before": snapshots_before, "snapshot_inputs_after": snapshots_after,
            "execution_inputs_unchanged": unchanged, "execution_snapshots_unchanged": snapshots_unchanged,
            "source_binary_reference_plan_identity_passed": identity, "fixed_reference_coverage_passed": reference_coverage,
            "raw_main_manifest": {"path": str(raw_path), "sha256": raw_hash, "exit_code": raw_exit,
                                  "finish_error": finish_error, "unchanged_full_partial_gate_retained": partial},
            "required_visual_outcomes": base._visual_review(source, suite, visual_plan), "manual_review_recorded": False,
            "strict_guards": plan["strict_guards"], "unchanged_full_regression_mapping": original_plan["unchanged_full_regression_mapping"],
            "full_case_ids_modified": False, "partial_run": True, "full_capture_suite_completed": False,
            "clean_round": False, "clean_round_streak": 0, "clean_round_credit": 0, "accepted": False,
            "review_requirement": "Inspect all 59 fresh original manual PNGs, including the nine unchanged v1 visual outcomes and the additional typography, source-family and same-case artwork scopes. State values and private font or preferred-size diagnostics are not native glyph proof.",
            "exit_code_scope": "Zero qualifies only the fixed 72 actual frames, their 13 prerequisite roles and exact frozen identities. The original main raw manifest retains exit1 and every full-suite gate; no complete or clean round is credited.",
        }
        source.save_json(suite.output / "c15-v2-qualification.json", report)
        if errors:
            source.save_json(suite.output / "c15-v2-driver-error.json", {
                "initial_error": errors[0], "errors": errors, "failed_case_ids": counts["failed_case_ids"],
                "raw_main_manifest": report["raw_main_manifest"], "partial_run": True,
                "full_capture_suite_completed": False, "clean_round": False, "clean_round_credit": 0, "accepted": False,
            })
        print(f"C15 v2 focused UI: {counts['capture_attempts']} captures, success={bool(passed)}, failed={len(counts['failed_case_ids'])}, partial=true, raw full-suite exit={raw_exit}", flush=True)
        return 0 if passed else 1
    except Exception as error:
        errors.append({"stage": "v2-driver", "error": f"{type(error).__name__}: {error}"})
        if output_is_fresh and args is not None and source is not None:
            try:
                source.save_json(args.output / "c15-v2-driver-error.json", {
                    "initial_error": errors[0], "errors": errors, "inputs_before": before, "inputs_after": after,
                    "execution_input_snapshots": retained,
                    "expected_source_sha256": args.expected_source_sha256.lower(),
                    "expected_binary_sha256": args.expected_sha256.lower(),
                    "expected_qualification_plan_sha256": extra.expected_qualification_plan_sha256.lower(),
                    "source_sha256": suite.source["sha256"] if suite is not None else None,
                    "binary_sha256": suite.binary_hash if suite is not None else None,
                    "reference_sha256": suite.design["sha256"] if suite is not None else None,
                    "capture_plan_sha256": before.get("capture_plan", {}).get("sha256"),
                    "qualification_plan_sha256": before.get("c15_v2_qualification_plan", {}).get("sha256"),
                    "raw_main_manifest": {"path": str(raw_path) if raw_path is not None else None,
                                          "sha256": raw_hash, "exit_code": raw_exit, "finish_error": finish_error},
                    "failed_case_ids": [case["name"] for case in suite.cases if not case.get("passed")] if suite is not None else [],
                    "partial_run": True, "full_capture_suite_completed": False,
                    "clean_round": False, "clean_round_credit": 0, "accepted": False,
                })
            except Exception as report_error:
                print(f"C15 v2 error report failed: {type(report_error).__name__}: {report_error}", file=sys.stderr, flush=True)
        print(f"C15 v2 qualification failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    sys.exit(main())
