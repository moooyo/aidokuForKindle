#!/usr/bin/env python3
"""Run the fixed 39-frame C15 affected-native-UI qualification on Linux.

This standalone partial driver retains the full harness and its raw manifest.
Only an explicitly frozen source and executable may run. Original dependency,
reference and preserved-negative bytes are retained before any native capture.
Fresh manual PNG review remains mandatory and no complete-round credit is given.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
from pathlib import Path
import shutil
import sys
from typing import Any


QUALIFICATION_PLAN_SHA256 = "d1e817ab72f82cee255e95f4a81eee1f1e4337eab3795a6bf3e8d904454c1019"
SCRIPT_NAMES = (
    "verify-handoff-c15.py", "handoff_c15_visual.py", "verify-handoff-ui.py", "verify-handoff-tags.py",
    "verify-ui.py", "verify-fullscreen.py", "handoff_c4_business.py", "handoff_c5_ui.py",
    "handoff_c5_reader.py", "handoff_c9_search.py", "handoff_c10_search.py", "handoff_c11_search.py",
    "handoff_c12_scope.py", "handoff_c13_scope.py",
)


def load(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().with_name(filename)
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load the frozen C15 qualification input: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def _bind_modules(source: Any, guard: Any, helper: Any, directory: Path) -> None:
    """Bind the actual imported closure, including the full C13/C12 dependency."""
    modules = {
        "verify-handoff-tags.py": guard, "verify-handoff-ui.py": source,
        "handoff_c15_visual.py": helper, "verify-ui.py": source.native,
        "verify-fullscreen.py": source.oracle, "handoff_c4_business.py": source.c4_business,
        "handoff_c5_ui.py": source.c5_ui, "handoff_c5_reader.py": source.c5_reader,
        "handoff_c9_search.py": source.c10_search.c9, "handoff_c10_search.py": source.c10_search,
        "handoff_c11_search.py": helper.c11, "handoff_c12_scope.py": source.c13_scope.scope,
        "handoff_c13_scope.py": source.c13_scope,
    }
    for filename, module in modules.items():
        actual = getattr(module, "__file__", None)
        if actual is None or Path(actual).resolve() != (directory / filename).resolve():
            raise ValueError(f"An actual C15 imported dependency is outside its frozen path: {filename}")
    if (helper.c5 is not source.c5_ui or helper.c12 is not source.c13_scope.scope
            or helper.c11.c10 is not source.c10_search
            or helper.c11.c9 is not source.c10_search.c9
            or helper.c12.c9 is not source.c10_search.c9):
        raise ValueError("C15 must reuse the same actual C5, C9, C10, C11 and C12 modules as the full harness")


def _input_paths(source: Any, helper: Any, args: Any, negative_root: Path,
                 directory: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    paths = {name: directory / name for name in SCRIPT_NAMES}
    destinations = {name: Path("scripts") / name for name in SCRIPT_NAMES}
    plans = {
        "capture_plan": args.plan, "reference_scope": args.scope,
        "c15_qualification_plan": directory.parent / "docs/review/phase3-c15-affected-qualification-plan.json",
    }
    paths.update(plans)
    destinations.update({name: Path("plans") / path.name for name, path in plans.items()})
    scope = source.read_json(args.scope)
    relative_references = {entry["reference"] for board in scope["pageStateMatrix"]
                           for entry in [board["default"], *board["states"]]}
    for item in scope.get("supportReferences", []):
        files = item.get("files", [item["file"]] if item.get("file") else [])
        if isinstance(files, list):
            relative_references.update(files)
    root = args.reference_root.resolve(strict=True)
    for relative in sorted(relative_references):
        path = root / relative
        preserved_relative = path.resolve().relative_to(root)
        name = f"reference:{relative}"
        paths[name] = path
        destinations[name] = Path("references") / preserved_relative
    negative_paths = helper.c11.negative_paths(negative_root)
    paths.update(negative_paths)
    destinations.update({name: Path("preserved-c10-negatives") / path.name
                         for name, path in negative_paths.items()})
    if len(set(destinations.values())) != len(destinations):
        raise ValueError("Every frozen C15 input must retain a distinct snapshot path")
    return paths, destinations


def _freeze_inputs(source: Any, suite: Any, paths: dict[str, Path], destinations: dict[str, Path],
                   before: dict[str, Any], retained: dict[str, Any], snapshot_paths: dict[str, Path]) -> None:
    for name, path in paths.items():
        snapshot = suite.output / "execution-inputs" / destinations[name]
        snapshot_paths[name] = snapshot
        retained[name] = {
            "original_path": before[name]["path"], "path": str(snapshot),
            "expected_sha256": before[name]["sha256"], "sha256": None, "bytes_retained": False,
        }
        try:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, snapshot)
            actual = source.file_hash(snapshot)
            retained[name]["sha256"] = actual
            retained[name]["bytes_retained"] = actual == before[name]["sha256"]
            if not retained[name]["bytes_retained"]:
                raise ValueError(f"The C15 execution snapshot did not retain its original bytes: {name}")
        except Exception as error:
            retained[name]["error"] = f"{type(error).__name__}: {error}"
            raise


def _failure(suite: Any, errors: list[dict[str, str]], stage: str, error: BaseException) -> None:
    actual = f"{type(error).__name__}: {error}"
    errors.append({"stage": stage, "error": actual})
    case = {
        "name": f"c15-{stage}-completion", "passed": False, "manual_review_required": False,
        "c15_qualification": True, "partial_run": True,
        "checks": [{"description": "The fixed C15 stage completed without a product or tool failure",
                    "passed": False, "actual": actual}],
    }
    suite.cases.append(case)
    try:
        suite.persist_case(case)
    except Exception as persistence_error:
        errors.append({"stage": f"{stage}-error-report", "error": f"{type(persistence_error).__name__}: {persistence_error}"})


def _counts(source: Any, suite: Any, helper: Any) -> dict[str, Any]:
    captures = [case for case in suite.cases if case.get("screenshot")]
    names = [case.get("name") for case in captures]
    occurrences = Counter(names)
    manual = [case["name"] for case in captures if case.get("manual_review_required") is True]
    prerequisites = [case["name"] for case in captures if case.get("prerequisite") is True]
    missing_pngs, altered_evidence, role_mismatches, identity_mismatches = [], [], [], []
    actual_png_count = 0
    for case in captures:
        name = case["name"]
        screenshot = Path(case["screenshot"])
        if screenshot.is_file() and screenshot.resolve() == (suite.output / f"{name}.png").resolve():
            actual_png_count += 1
        else:
            missing_pngs.append(name)
        expected_prerequisite = name in helper.PREREQUISITE_IDS
        if (case.get("prerequisite") is not expected_prerequisite
                or case.get("manual_review_required") is not (not expected_prerequisite)):
            role_mismatches.append(name)
        if (case.get("source_sha256") != suite.source["sha256"]
                or case.get("binary_sha256") != suite.binary_hash):
            identity_mismatches.append(name)
        evidence = case.get("evidence_hashes", {})
        if not isinstance(evidence, dict) or "screenshot" not in evidence:
            altered_evidence.append({"case_id": name, "evidence": "screenshot", "error": "Original screenshot hash is absent"})
            continue
        for label, record in evidence.items():
            try:
                path = Path(record["path"])
                if not path.is_file() or source.file_hash(path) != record["sha256"]:
                    altered_evidence.append({"case_id": name, "evidence": label, "error": "Original captured evidence is missing or changed"})
            except Exception as error:
                altered_evidence.append({"case_id": name, "evidence": label, "error": f"{type(error).__name__}: {error}"})
    return {
        "capture_attempts": len(captures), "actual_original_png_count": actual_png_count,
        "manual_review_captures": len(manual), "prerequisite_captures": len(prerequisites),
        "captured_case_ids": names, "manual_case_ids": manual, "prerequisite_case_ids": prerequisites,
        "missing_case_ids": sorted(set(helper.CASE_IDS) - set(names)),
        "unexpected_case_ids": sorted(set(names) - set(helper.CASE_IDS)),
        "duplicate_case_ids": sorted(name for name, count in occurrences.items() if count != 1),
        "failed_case_ids": [case["name"] for case in suite.cases if not case.get("passed")],
        "missing_original_png_case_ids": missing_pngs, "capture_role_mismatch_case_ids": role_mismatches,
        "capture_identity_mismatch_case_ids": identity_mismatches, "altered_capture_evidence": altered_evidence,
    }


def _raw_partial_boundary(raw: dict[str, Any], suite: Any, source: Any, raw_exit: Any) -> bool:
    """Require the unchanged full manifest to retain its actual partial exit."""
    return (
        raw_exit == 1 and raw.get("kind") == "phase3-handoff-native-ui-round"
        and raw.get("full_capture_suite_completed") is False
        and raw.get("reference_coverage_complete") is False and raw.get("mechanical_success") is False
        and raw.get("review", {}).get("recorded") is False
        and raw.get("review", {}).get("clean_round") is False
        and raw.get("review", {}).get("clean_round_streak") == 0
        and raw.get("review", {}).get("accepted") is False
        and raw.get("required_reference_ids") == suite.reference_ids
        and len(suite.reference_ids) == 101
        and raw.get("required_supplemental_groups") == suite.required_groups and len(suite.required_groups) == 21
        and raw.get("required_c5_control_ids") == sorted((*source.c5_ui.CASE_IDS, *source.c5_reader.CASE_IDS))
        and raw.get("required_c10_search_identity_ids") == sorted(source.c10_search.CASE_IDS)
        and raw.get("required_c13_source_shortcut_ids") == sorted(source.c13_scope.CASE_IDS)
        and raw.get("required_review_case_ids") == sorted(case["name"] for case in suite.cases
                                                        if case.get("manual_review_required"))
        and raw.get("reference_coverage") == suite.coverage
    )


def _visual_review(source: Any, suite: Any, plan: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = []
    for outcome in plan["required_visual_outcomes"]:
        frames = []
        for identifier in outcome["case_ids"]:
            matches = [case for case in suite.cases if case.get("name") == identifier and case.get("screenshot")]
            case = matches[0] if len(matches) == 1 else None
            path = Path(case["screenshot"]) if case is not None else None
            frames.append({
                "case_id": identifier, "screenshot": str(path) if path is not None else None,
                "sha256": source.file_hash(path) if path is not None and path.is_file() else None,
                "actual_capture_present": path is not None and path.is_file(),
            })
        outcomes.append({
            "id": outcome["id"], "requirement": outcome["requirement"], "original_frames": frames,
            "review_status": "fresh_original_png_review_required", "review_recorded": False,
            "state_is_not_font_proof": True,
        })
    return outcomes


def main() -> int:
    original_argv = sys.argv
    invocation = list(original_argv)
    source, suite, args = None, None, None
    output_is_fresh = False
    errors: list[dict[str, str]] = []
    before, after, retained, snapshot_paths, snapshots_before, snapshots_after = {}, {}, {}, {}, {}, {}
    result, negative, raw_exit, finish_error = None, None, None, None
    raw_path, raw_hash = None, None
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--negative-root", type=Path, required=True)
        extra, inherited = parser.parse_known_args(invocation[1:])
        guard = load("c15_existing_guard", "verify-handoff-tags.py")
        source = guard.load_source()
        try:
            sys.argv = [invocation[0], *inherited]
            args = guard._arguments(source)
        finally:
            sys.argv = original_argv
        if args.timeout != 90 or args.settled_reader_tail_ms != 5000:
            raise ValueError("C15 retains the existing 90-second per-app timeout and 5000-ms settled-reader tail")
        guard._fresh_output(args.output)
        output_is_fresh = True
        directory = Path(__file__).resolve().parent
        helper = load("handoff_c15_visual", "handoff_c15_visual.py")
        _bind_modules(source, guard, helper, directory)
        paths, destinations = _input_paths(source, helper, args, extra.negative_root, directory)
        before = guard._fingerprints(source, paths)
        if any(record["sha256"] is None for record in before.values()):
            raise ValueError("Every actual C15 helper, plan, reference and preserved negative input must be present")
        if before["c15_qualification_plan"]["sha256"] != QUALIFICATION_PLAN_SHA256:
            raise ValueError("The C15 qualification plan does not match the authorized frozen static plan")
        plan = source.read_json(paths["c15_qualification_plan"])
        helper.validate_plan(plan)
        for name, field in (
            ("verify-handoff-ui.py", "frozen_full_harness_sha256"),
            ("capture_plan", "frozen_full_plan_sha256"), ("reference_scope", "frozen_reference_scope_sha256"),
        ):
            if before[name]["sha256"] != plan[field]:
                raise ValueError(f"C15 cannot change the frozen full harness, plan or scope: {name}")
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
            "expected_case_ids": list(helper.CASE_IDS), "manual_case_ids": list(helper.MANUAL_IDS),
            "prerequisite_case_ids": list(helper.PREREQUISITE_IDS),
            "original_negative_root": str(extra.negative_root.resolve()),
            "frozen_negative_root": str(frozen_negative_root), "all_input_bytes_retained": False,
            "partial_run": True, "clean_round_credit": 0,
        }
        stage = "execution-before"
        try:
            source.save_json(suite.output / "c15-execution-before.json", execution_before)
            stage = "input-freeze"
            _freeze_inputs(source, suite, paths, destinations, before, retained, snapshot_paths)
            snapshots_before = guard._fingerprints(source, snapshot_paths)
            if (set(before) != set(retained) or set(before) != set(snapshots_before)
                    or any(snapshots_before[name]["sha256"] != before[name]["sha256"]
                           or retained[name].get("bytes_retained") is not True for name in before)):
                raise ValueError("Every C15 execution snapshot must retain its exact original bytes before native capture")
            execution_before.update({"snapshot_inputs": snapshots_before, "all_input_bytes_retained": True})
            source.save_json(suite.output / "c15-execution-before.json", execution_before)
            stage = "negative-controls"
            negative = helper.c11.qualify_old_negatives(frozen_negative_root)
            source.save_json(suite.output / "c15-negative-controls.json", negative)
            if negative.get("passed") is not True:
                raise ValueError("The existing complete shared-glyph oracle did not reject both preserved C10 masked frames")
            stage = "native-union"
            result = helper.run(suite, suite.c4_api(), frozen_negative_root)
        except Exception as error:
            _failure(suite, errors, stage, error)
        finally:
            if execution_before["all_input_bytes_retained"] is not True:
                try:
                    snapshots_before = guard._fingerprints(source, snapshot_paths)
                    execution_before.update({"snapshot_inputs": snapshots_before, "errors": errors})
                    source.save_json(suite.output / "c15-execution-before.json", execution_before)
                except Exception as error:
                    _failure(suite, errors, "input-freeze-report", error)
            try:
                helper.mark_cases(suite, plan)
            except Exception as error:
                _failure(suite, errors, "review-instructions", error)
            try:
                raw_exit = suite.finish_manifest()
            except Exception as error:
                finish_error = f"{type(error).__name__}: {error}"
                errors.append({"stage": "raw-manifest", "error": finish_error})
        try:
            after = guard._fingerprints(source, paths)
            snapshots_after = guard._fingerprints(source, snapshot_paths)
        except Exception as error:
            errors.append({"stage": "execution-after", "error": f"{type(error).__name__}: {error}"})
        unchanged = (set(before) == set(after)
                     and all(before[name]["sha256"] is not None and before[name] == after[name] for name in before))
        snapshots_unchanged = (set(before) == set(retained) == set(snapshots_before) == set(snapshots_after)
                               and all(snapshots_before[name] == snapshots_after[name]
                                       and snapshots_before[name]["sha256"] == before[name]["sha256"]
                                       and retained[name].get("bytes_retained") is True
                                       for name in before))
        raw, binary_after = {}, None
        try:
            if raw_path.is_file():
                raw_hash = source.file_hash(raw_path)
                raw = source.read_json(raw_path)
            binary_after = source.file_hash(suite.binary)
        except Exception as error:
            errors.append({"stage": "raw-identity", "error": f"{type(error).__name__}: {error}"})
        counts = _counts(source, suite, helper)
        partial = _raw_partial_boundary(raw, suite, source, raw_exit)
        reference_coverage = (raw.get("reference_coverage") == suite.coverage
                              == {identifier: identifier for identifier in helper.REFERENCE_CASE_IDS})
        identity = (
            suite.source["sha256"] == args.expected_source_sha256.lower()
            and suite.binary_hash == args.expected_sha256.lower() == binary_after
            and raw.get("source_sha256") == raw.get("source_sha256_after") == suite.source["sha256"]
            and raw.get("source_unchanged") is True and raw.get("binary_unchanged") is True
            and raw.get("binary_sha256") == suite.binary_hash
            and raw.get("reference_sha256") == suite.design["sha256"]
            and raw.get("reference_scope_sha256") == suite.design["scope_sha256"] == before["reference_scope"]["sha256"]
            and raw.get("capture_plan_sha256") == suite.design["plan_sha256"] == before["capture_plan"]["sha256"]
            and raw.get("harness_sha256") == before["verify-handoff-ui.py"]["sha256"]
        )
        passed = (
            not errors and result is not None and result.get("completed") is True
            and negative is not None and negative.get("passed") is True
            and unchanged and snapshots_unchanged and identity and partial and reference_coverage and finish_error is None
            and counts["capture_attempts"] == counts["actual_original_png_count"] == 39
            and counts["manual_review_captures"] == 35 and counts["prerequisite_captures"] == 4
            and set(counts["manual_case_ids"]) == set(helper.MANUAL_IDS)
            and set(counts["prerequisite_case_ids"]) == set(helper.PREREQUISITE_IDS)
            and not any(counts[field] for field in (
                "missing_case_ids", "unexpected_case_ids", "duplicate_case_ids", "failed_case_ids",
                "missing_original_png_case_ids", "capture_role_mismatch_case_ids",
                "capture_identity_mismatch_case_ids", "altered_capture_evidence",
            ))
        )
        source.save_json(suite.output / "c15-execution-after.json", {
            "inputs": after, "snapshot_inputs": snapshots_after,
            "execution_inputs_unchanged": unchanged, "execution_snapshots_unchanged": snapshots_unchanged,
            "source_sha256_after": raw.get("source_sha256_after"), "binary_sha256_after": binary_after,
            "raw_manifest_sha256": raw_hash, "raw_manifest_exit_code": raw_exit,
            "finish_error": finish_error, "errors": errors, "partial_run": True, "clean_round_credit": 0,
        })
        report = {
            "schema_version": 1, "kind": "c15-affected-native-ui-qualification", "round": args.round,
            "run_id": suite.run_id, "qualification_success": bool(passed),
            "expected_captures": 39, "actual_captures": counts["capture_attempts"],
            "manual_review_captures": counts["manual_review_captures"],
            "prerequisite_captures": counts["prerequisite_captures"],
            "expected_case_ids": list(helper.CASE_IDS), "manual_case_ids": list(helper.MANUAL_IDS),
            "prerequisite_case_ids": list(helper.PREREQUISITE_IDS), "actual_counts": counts,
            "result": result, "preserved_negative_controls": negative, "errors": errors,
            "original_negative_root": str(extra.negative_root.resolve()), "frozen_negative_root": str(frozen_negative_root),
            "source_sha256": suite.source["sha256"], "binary_sha256": suite.binary_hash,
            "source_sha256_after": raw.get("source_sha256_after"), "binary_sha256_after": binary_after,
            "reference_sha256": suite.design["sha256"], "reference_scope_sha256": suite.design["scope_sha256"],
            "capture_plan_sha256": suite.design["plan_sha256"],
            "harness_sha256": before["verify-handoff-ui.py"]["sha256"],
            "qualification_entrypoint_sha256": before["verify-handoff-c15.py"]["sha256"],
            "qualification_helper_sha256": before["handoff_c15_visual.py"]["sha256"],
            "qualification_plan_sha256": before["c15_qualification_plan"]["sha256"],
            "dependency_sha256": {name: record["sha256"] for name, record in before.items()},
            "inputs_before": before, "inputs_after": after, "execution_input_snapshots": retained,
            "snapshot_inputs_before": snapshots_before, "snapshot_inputs_after": snapshots_after,
            "execution_inputs_unchanged": unchanged, "execution_snapshots_unchanged": snapshots_unchanged,
            "source_binary_reference_plan_identity_passed": identity,
            "fixed_reference_coverage_passed": reference_coverage,
            "raw_main_manifest": {"path": str(raw_path), "sha256": raw_hash, "exit_code": raw_exit,
                                  "finish_error": finish_error, "unchanged_full_partial_gate_retained": partial},
            "required_visual_outcomes": _visual_review(source, suite, plan), "manual_review_recorded": False,
            "unchanged_full_regression_mapping": plan["unchanged_full_regression_mapping"],
            "strict_guards": plan["strict_guards"], "full_case_ids_modified": False,
            "partial_run": True, "full_capture_suite_completed": False, "clean_round": False,
            "clean_round_streak": 0, "clean_round_credit": 0, "accepted": False,
            "review_requirement": "Inspect all 35 fresh original manual PNGs, including all nine affected visual outcomes. State values, model strings and private font or preferred-size diagnostics do not establish glyph fidelity.",
            "exit_code_scope": "Zero qualifies only the fixed 39 actual frames and their frozen identities. The original main manifest retains its partial exit1 and all full-suite requirements; no complete or clean round is credited.",
        }
        source.save_json(suite.output / "c15-qualification.json", report)
        if errors:
            source.save_json(suite.output / "c15-driver-error.json", {
                "initial_error": errors[0], "errors": errors, "failed_case_ids": counts["failed_case_ids"],
                "raw_main_manifest": report["raw_main_manifest"], "partial_run": True,
                "full_capture_suite_completed": False, "clean_round": False, "clean_round_credit": 0, "accepted": False,
            })
        print(f"C15 focused UI: {counts['capture_attempts']} captures, success={bool(passed)}, failed={len(counts['failed_case_ids'])}, partial=true, raw full-suite exit={raw_exit}", flush=True)
        return 0 if passed else 1
    except Exception as error:
        failure = {"stage": "driver", "error": f"{type(error).__name__}: {error}"}
        errors.append(failure)
        if output_is_fresh and args is not None and source is not None:
            try:
                source.save_json(args.output / "c15-driver-error.json", {
                    "initial_error": errors[0], "errors": errors, "inputs_before": before, "inputs_after": after,
                    "execution_input_snapshots": retained, "raw_manifest_exit_code": raw_exit,
                    "raw_manifest_finish_error": finish_error,
                    "expected_source_sha256": args.expected_source_sha256.lower(),
                    "expected_binary_sha256": args.expected_sha256.lower(),
                    "source_sha256": suite.source["sha256"] if suite is not None else None,
                    "binary_sha256": suite.binary_hash if suite is not None else None,
                    "reference_sha256": suite.design["sha256"] if suite is not None else None,
                    "capture_plan_sha256": before.get("capture_plan", {}).get("sha256"),
                    "reference_scope_sha256": before.get("reference_scope", {}).get("sha256"),
                    "qualification_plan_sha256": before.get("c15_qualification_plan", {}).get("sha256"),
                    "raw_main_manifest": {"path": str(raw_path) if raw_path is not None else None,
                                          "sha256": raw_hash, "exit_code": raw_exit, "finish_error": finish_error},
                    "failed_case_ids": [case["name"] for case in suite.cases if not case.get("passed")] if suite is not None else [],
                    "partial_run": True, "full_capture_suite_completed": False,
                    "clean_round": False, "clean_round_credit": 0, "accepted": False,
                })
            except Exception as report_error:
                print(f"C15 error report failed: {type(report_error).__name__}: {report_error}", file=sys.stderr, flush=True)
        print(f"C15 qualification failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    sys.exit(main())
