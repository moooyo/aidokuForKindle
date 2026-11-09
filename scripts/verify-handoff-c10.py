#!/usr/bin/env python3
"""Run the bounded C10 native search-layout and visible-source qualification."""

import importlib.util
from pathlib import Path
import sys


def load(name, filename):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load the frozen C10 input: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    suite = None
    source = None
    try:
        guard = load("c10_existing_guard", "verify-handoff-tags.py")
        source = guard.load_source()
        args = guard._arguments(source)
        guard._fresh_output(args.output)
        helper = source.c10_search
        expected = list(helper.CASE_IDS)
        if len(expected) > 40 or len(expected) != len(set(expected)):
            raise ValueError("The C10 qualification must have a fixed bounded set")
        directory = Path(__file__).resolve().parent
        paths = {name: directory / name for name in (
            "verify-handoff-c10.py", "verify-handoff-ui.py", "verify-handoff-tags.py",
            "handoff_c10_search.py", "handoff_c9_search.py", "verify-ui.py", "verify-fullscreen.py",
            "handoff_c4_business.py", "handoff_c5_ui.py", "handoff_c5_reader.py",
        )}
        paths.update({"capture_plan": args.plan, "reference_scope": args.scope})
        before = guard._fingerprints(source, paths)
        suite = source.Suite(args)
        suite.reference_ids = [item["id"] for board in suite.scope["pageStateMatrix"]
                               for item in [board["default"], *board["states"]]]
        result = None
        raw_exit = None
        finish_error = None
        try:
            source.save_json(suite.output / "c10-execution-before.json", {
                "argv": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
                "inputs": before, "expected_case_ids": expected, "partial_run": True,
            })
            result = helper.run_search_identity(suite, suite.c4_api(), qualifier=True)
        except Exception as error:
            case = {"name": "c10-qualification-completion", "passed": False,
                    "manual_review_required": False, "checks": [{"description": "The fixed C10 qualification completed",
                    "passed": False, "actual": f"{type(error).__name__}: {error}"}]}
            suite.cases.append(case)
            suite.persist_case(case)
        finally:
            try:
                raw_exit = suite.finish_manifest()
            except Exception as error:
                finish_error = f"{type(error).__name__}: {error}"
        after = guard._fingerprints(source, paths)
        unchanged = set(before) == set(after) and all(before[name]["sha256"] is not None
                       and before[name] == after[name] for name in before)
        captured = [case for case in suite.cases if case.get("screenshot")]
        names = [case["name"] for case in captured]
        missing = sorted(set(expected) - set(names))
        unexpected = sorted(set(names) - set(expected))
        duplicate = sorted({name for name in names if names.count(name) > 1})
        failures = [case["name"] for case in suite.cases if not case.get("passed")]
        raw_path = suite.output / "manifest.json"
        raw = source.read_json(raw_path) if raw_path.is_file() else {}
        partial = raw.get("full_capture_suite_completed") is False and raw.get("review", {}).get("clean_round") is False
        passed = (not missing and not unexpected and not duplicate and not failures and unchanged
                  and partial and finish_error is None and len(captured) == len(expected)
                  and all(Path(case["screenshot"]).is_file() for case in captured))
        report = {"schema_version": 1, "kind": "c10-native-search-frame-qualification",
                  "round": args.round, "run_id": suite.run_id, "qualification_success": bool(passed),
                  "actual_captures": len(captured), "expected_captures": len(expected),
                  "manual_review_captures": sum(bool(case.get("manual_review_required")) for case in captured),
                  "prerequisite_captures": sum(bool(case.get("prerequisite")) for case in captured),
                  "expected_case_ids": expected, "missing_case_ids": missing, "unexpected_case_ids": unexpected,
                  "duplicate_case_ids": duplicate, "failed_case_ids": failures, "result": result,
                  "source_sha256": suite.source["sha256"], "binary_sha256": suite.binary_hash,
                  "reference_sha256": suite.design["sha256"], "capture_plan_sha256": suite.design["plan_sha256"],
                  "inputs_before": before, "inputs_after": after, "execution_inputs_unchanged": unchanged,
                  "raw_main_manifest": {"path": str(raw_path), "sha256": source.file_hash(raw_path) if raw_path.is_file() else None,
                                        "exit_code": raw_exit, "finish_error": finish_error},
                  "partial_run": True, "full_capture_suite_completed": False, "clean_round": False,
                  "clean_round_credit": 0, "accepted": False,
                  "metric_boundary": "Native preferred-height P is not exported. H2 and the supported P interval are explicitly inferred from actual same-font/same-author native frame origins; glyphs, boundaries and masks are inspected separately without clamping.",
                  "review_requirement": "Fresh original-PNG review must confirm both title lines and distinguishing series000 through005, full author rows, next-group separation, source names, keyboard drafts and short-title bookmark allocation."}
        source.save_json(suite.output / "c10-qualification.json", report)
        print(f"C10 focused UI: {len(captured)} captures, success={bool(passed)}, failed={len(failures)}, partial=true, raw full-suite exit={raw_exit}", flush=True)
        return 0 if passed else 1
    except Exception as error:
        if suite is not None and source is not None:
            source.save_json(suite.output / "c10-driver-error.json", {"partial_run": True, "clean_round": False,
                             "error": f"{type(error).__name__}: {error}"})
        print(f"C10 qualification failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
