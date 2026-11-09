#!/usr/bin/env python3
"""Run the six-frame C11 author-tail and ViewAll boundary qualification."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import shutil
import sys


def load(name, filename):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load the frozen C11 qualification input: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    suite = None
    source = None
    try:
        original_argv = list(sys.argv)
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--negative-root", type=Path, required=True)
        extra, inherited = parser.parse_known_args(original_argv[1:])
        guard = load("c11_existing_guard", "verify-handoff-tags.py")
        source = guard.load_source()
        try:
            sys.argv = [original_argv[0], *inherited]
            args = guard._arguments(source)
        finally:
            sys.argv = original_argv
        guard._fresh_output(args.output)
        helper = load("handoff_c11_search", "handoff_c11_search.py")
        expected = list(helper.CASE_IDS)
        if len(expected) != 6 or len(set(expected)) != 6 or len(helper.MANUAL_IDS) != 4:
            raise ValueError("The C11 qualification must retain six fixed captures and four manual frames")
        directory = Path(__file__).resolve().parent
        paths = {name: directory / name for name in (
            "verify-handoff-c11.py", "handoff_c11_search.py", "verify-handoff-ui.py", "verify-handoff-tags.py",
            "handoff_c10_search.py", "handoff_c9_search.py", "verify-ui.py", "verify-fullscreen.py",
            "handoff_c4_business.py", "handoff_c5_ui.py", "handoff_c5_reader.py",
        )}
        paths.update({"capture_plan": args.plan, "reference_scope": args.scope,
                      "c11_qualification_plan": directory.parent / "docs/review/phase3-c11-author-boundary-plan.json"})
        paths.update(helper.negative_paths(extra.negative_root))
        before = guard._fingerprints(source, paths)
        if any(value["sha256"] is None for value in before.values()):
            raise ValueError("Every C11 execution dependency and preserved negative input must be present")
        suite = source.Suite(args)
        frozen_inputs = {}
        for name, path in paths.items():
            if name.startswith("negative:"):
                continue
            snapshot = suite.output / "execution-inputs" / path.name
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, snapshot)
            if source.file_hash(snapshot) != before[name]["sha256"]:
                raise ValueError(f"The actual qualification input snapshot did not retain its bytes: {name}")
            frozen_inputs[name] = {"path": str(snapshot), "sha256": source.file_hash(snapshot)}
        suite.reference_ids = [item["id"] for board in suite.scope["pageStateMatrix"]
                               for item in [board["default"], *board["states"]]]
        result, negative = None, None
        raw_exit, finish_error = None, None
        try:
            source.save_json(suite.output / "c11-execution-before.json", {
                "argv": [sys.executable, *original_argv], "inputs": before,
                "execution_input_snapshots": frozen_inputs,
                "expected_case_ids": expected, "manual_case_ids": list(helper.MANUAL_IDS), "partial_run": True})
            negative = helper.qualify_old_negatives(extra.negative_root)
            source.save_json(suite.output / "c11-negative-controls.json", negative)
            if not negative["passed"]:
                raise ValueError("The complete shared-glyph oracle did not reject both preserved C10 masked frames")
            result = helper.run_author_boundary(suite, suite.c4_api(), extra.negative_root)
        except Exception as error:
            case = {"name": "c11-qualification-completion", "passed": False, "manual_review_required": False,
                    "checks": [{"description": "The fixed C11 qualification completed without a product or tool failure",
                                "passed": False, "actual": f"{type(error).__name__}: {error}"}]}
            suite.cases.append(case)
            suite.persist_case(case)
        finally:
            try:
                raw_exit = suite.finish_manifest()
            except Exception as error:
                finish_error = f"{type(error).__name__}: {error}"
        after = guard._fingerprints(source, paths)
        unchanged = set(before) == set(after) and all(before[name] == after[name] for name in before)
        captured = [case for case in suite.cases if case.get("screenshot")]
        names = [case["name"] for case in captured]
        manual = [case["name"] for case in captured if case.get("manual_review_required")]
        missing, unexpected = sorted(set(expected) - set(names)), sorted(set(names) - set(expected))
        duplicate = sorted({name for name in names if names.count(name) > 1})
        failures = [case["name"] for case in suite.cases if not case.get("passed")]
        raw_path = suite.output / "manifest.json"
        raw = source.read_json(raw_path) if raw_path.is_file() else {}
        partial = raw.get("full_capture_suite_completed") is False and raw.get("review", {}).get("clean_round") is False
        passed = (not missing and not unexpected and not duplicate and not failures and unchanged and partial
                  and finish_error is None and len(captured) == 6 and set(manual) == set(helper.MANUAL_IDS)
                  and negative is not None and negative["passed"])
        report = {"schema_version": 1, "kind": "c11-native-author-tail-heading-boundary-qualification",
                  "round": args.round, "run_id": suite.run_id, "qualification_success": bool(passed),
                  "actual_captures": len(captured), "expected_captures": 6, "manual_review_captures": len(manual),
                  "prerequisite_captures": sum(bool(case.get("prerequisite")) for case in captured),
                  "expected_case_ids": expected, "manual_case_ids": list(helper.MANUAL_IDS),
                  "missing_case_ids": missing, "unexpected_case_ids": unexpected, "duplicate_case_ids": duplicate,
                  "failed_case_ids": failures, "result": result, "preserved_negative_controls": negative,
                  "source_sha256": suite.source["sha256"], "binary_sha256": suite.binary_hash,
                  "reference_sha256": suite.design["sha256"], "capture_plan_sha256": suite.design["plan_sha256"],
                  "qualification_helper_sha256": {key: value["sha256"] for key, value in before.items()},
                  "execution_input_snapshots": frozen_inputs,
                  "inputs_before": before, "inputs_after": after, "execution_inputs_unchanged": unchanged,
                  "raw_main_manifest": {"path": str(raw_path), "sha256": source.file_hash(raw_path) if raw_path.is_file() else None,
                                        "exit_code": raw_exit, "finish_error": finish_error},
                  "partial_run": True, "full_capture_suite_completed": False, "clean_round": False,
                  "clean_round_credit": 0, "accepted": False,
                  "metric_boundary": "Existing actual-frame H2/P inference and two-line/title/author/card bounds are unchanged; complete shared-prefix pixels and native pointer outcomes independently qualify the heading-only fix.",
                  "review_requirement": "Fresh original-PNG review of four manual frames must confirm complete restored authors, both provider headings, the prior series005 detail and the next-provider ViewAll catalog. The two real setup frames are not manual coverage."}
        source.save_json(suite.output / "c11-qualification.json", report)
        print(f"C11 focused UI: {len(captured)} captures, success={bool(passed)}, failed={len(failures)}, partial=true, raw full-suite exit={raw_exit}", flush=True)
        return 0 if passed else 1
    except Exception as error:
        if suite is not None and source is not None:
            source.save_json(suite.output / "c11-driver-error.json", {"partial_run": True, "clean_round": False,
                             "error": f"{type(error).__name__}: {error}"})
        print(f"C11 qualification failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
