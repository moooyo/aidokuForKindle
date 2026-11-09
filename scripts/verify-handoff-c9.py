#!/usr/bin/env python3
"""Qualify the C9 actual unread and visible search-identity changes remotely.

Every candidate is an original native Slint capture. The focused result does not
replace the main full-suite gates or establish a complete or clean round.
"""

import importlib.util
import json
from pathlib import Path
import sys


def load(name, filename):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"The frozen C9 helper could not be loaded: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    suite = None
    source = None
    try:
        guards = load("c9_existing_guards", "verify-handoff-tags.py")
        source = guards.load_source()
        args = guards._arguments(source)
        guards._fresh_output(args.output)
        counts = load("c9_actual_unread", "handoff_c9_counts.py")
        search = load("c9_visible_identity", "handoff_c9_search.py")
        expected = [*counts.CASE_IDS, *search.CASE_IDS]
        if len(expected) > 40 or len(expected) != len(set(expected)):
            raise ValueError("The fixed C9 capture set must remain distinct and bounded to forty")
        root = Path(__file__).resolve().parent
        paths = {name: root / name for name in (
            "verify-handoff-c9.py", "verify-handoff-ui.py", "verify-handoff-tags.py",
            "handoff_c9_counts.py", "handoff_c9_search.py", "verify-ui.py",
            "verify-fullscreen.py", "handoff_c4_business.py", "handoff_c5_ui.py",
            "handoff_c5_reader.py",
        )}
        paths.update({"capture_plan": args.plan, "reference_scope": args.scope})
        before = guards._fingerprints(source, paths)
        execution = {"argv": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
                     "frozen_inputs_before": before, "expected_capture_ids": expected,
                     "expected_capture_count": len(expected), "maximum_captures": 40,
                     "partial_run": True}
        suite = source.Suite(args)
        suite.reference_ids = [entry["id"] for board in suite.scope["pageStateMatrix"]
                               for entry in [board["default"], *board["states"]]]
        result = {}
        raw_exit = None
        finish_error = None
        try:
            source.save_json(suite.output / "c9-execution.json", execution)
            result["unread"] = counts.run_unread_counts(suite, suite.c4_api())
            result["search"] = search.run_search_identity(suite, suite.c4_api())
        except Exception as error:
            case = {"name": "c9-qualification-completion", "passed": False,
                    "manual_review_required": False, "checks": [{"description": "The bounded C9 qualification completed",
                    "passed": False, "actual": f"{type(error).__name__}: {error}"}]}
            suite.cases.append(case)
            suite.persist_case(case)
        finally:
            try:
                raw_exit = suite.finish_manifest()
            except Exception as error:
                finish_error = f"{type(error).__name__}: {error}"
        after = guards._fingerprints(source, paths)
        unchanged = (set(before) == set(after) and all(before[name]["sha256"] is not None
                     and before[name] == after[name] for name in before))
        captured = [case for case in suite.cases if case.get("screenshot")]
        names = [case["name"] for case in captured]
        failures = [case["name"] for case in suite.cases if not case.get("passed")]
        missing = sorted(set(expected) - set(names))
        unexpected = sorted(set(names) - set(expected))
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raw_path = suite.output / "manifest.json"
        raw = source.read_json(raw_path) if raw_path.is_file() else {}
        partial = (raw.get("full_capture_suite_completed") is False
                   and raw.get("review", {}).get("clean_round") is False)
        passed = (not failures and not missing and not unexpected and not duplicates
                  and len(captured) == len(expected) and len(captured) <= 40
                  and all(Path(case["screenshot"]).is_file() for case in captured)
                  and unchanged and partial and finish_error is None
                  and set(result) == {"unread", "search"})
        report = {"schema_version": 1, "kind": "c9-focused-native-ui-qualification",
                  "round": args.round, "run_id": suite.run_id, "qualification_success": bool(passed),
                  "actual_captures": len(captured), "expected_captures": len(expected),
                  "manual_review_captures": sum(bool(case.get("manual_review_required")) for case in captured),
                  "prerequisite_captures": sum(bool(case.get("prerequisite")) for case in captured),
                  "expected_capture_ids": expected, "failed_cases": failures,
                  "missing_capture_ids": missing, "unexpected_capture_ids": unexpected,
                  "duplicate_capture_ids": duplicates, "results": result,
                  "source_sha256": suite.source["sha256"], "binary_sha256": suite.binary_hash,
                  "reference_sha256": suite.design["sha256"], "capture_plan_sha256": suite.design["plan_sha256"],
                  "execution": {**execution, "frozen_inputs_after": after}, "execution_inputs_unchanged": unchanged,
                  "raw_main_manifest": {"path": str(raw_path), "sha256": source.file_hash(raw_path) if raw_path.is_file() else None,
                                        "exit_code": raw_exit, "finish_error": finish_error},
                  "partial_run": True, "full_capture_suite_completed": False, "clean_round": False,
                  "clean_round_credit": 0, "accepted": False,
                  "review_requirement": "Fresh independent viewing must confirm actual count badges, source names, title suffixes, author lines, bookmark allocation and distinct native pointer targets. Store and model observations never substitute for readable original PNGs."}
        source.save_json(suite.output / "c9-qualification.json", report)
        print(f"C9 focused UI: {len(captured)} captures, success={bool(passed)}, failed={len(failures)}, partial=true, raw full-suite exit={raw_exit}", flush=True)
        return 0 if passed else 1
    except Exception as error:
        if suite is not None and source is not None:
            source.save_json(suite.output / "c9-driver-error.json", {"partial_run": True, "clean_round": False,
                             "error": f"{type(error).__name__}: {error}"})
        print(f"C9 qualification could not complete: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
