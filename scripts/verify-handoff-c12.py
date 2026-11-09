#!/usr/bin/env python3
"""Run the fixed twelve-frame C12 shortcut-name qualification on Linux."""

import importlib.util
from pathlib import Path
import shutil
import sys


def load(name, filename):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    guard = load("c12_fixed_guard", "verify-handoff-tags.py")
    source = guard.load_source()
    args = guard._arguments(source)
    guard._fresh_output(args.output)
    helper = load("handoff_c12_scope", "handoff_c12_scope.py")
    directory = Path(__file__).resolve().parent
    paths = {name: directory / name for name in ("verify-handoff-c12.py", "handoff_c12_scope.py", "verify-handoff-ui.py",
             "verify-handoff-tags.py", "handoff_c9_search.py", "handoff_c10_search.py", "verify-ui.py", "verify-fullscreen.py",
             "handoff_c4_business.py", "handoff_c5_ui.py", "handoff_c5_reader.py", "handoff_c13_scope.py")}
    paths.update({"plan": args.plan, "scope": args.scope, "qualification_plan": directory.parent / "docs/review/phase3-c12-scope-qualification.json"})
    before = guard._fingerprints(source, paths)
    if any(item["sha256"] is None for item in before.values()):
        raise ValueError("Every frozen qualification dependency must be present")
    suite = source.Suite(args)
    suite.reference_ids = [item["id"] for board in suite.scope["pageStateMatrix"] for item in [board["default"], *board["states"]]]
    retained = {}
    for name, path in paths.items():
        copy = suite.output / "execution-inputs" / path.name
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copy)
        retained[name] = {"path": str(copy), "sha256": source.file_hash(copy)}
    error = None
    try:
        helper.run(suite, suite.c4_api())
    except Exception as exception:
        error = f"{type(exception).__name__}: {exception}"
    raw_exit = suite.finish_manifest()
    after = guard._fingerprints(source, paths)
    captured = [case for case in suite.cases if case.get("screenshot")]
    names = [case["name"] for case in captured]
    missing = sorted(set(helper.CASE_IDS) - set(names))
    unexpected = sorted(set(names) - set(helper.CASE_IDS))
    failed = [case["name"] for case in suite.cases if case.get("passed") is False]
    unchanged = before == after
    passed = not error and not missing and not unexpected and not failed and unchanged and len(names) == 12 and len(set(names)) == 12
    report = {"kind": "c12-native-shortcut-name-qualification", "round": args.round, "run_id": suite.run_id,
              "passed": bool(passed), "actual_captures": len(captured), "expected_captures": 12,
              "manual_review_captures": sum(bool(case.get("manual_review_required")) for case in captured),
              "prerequisite_captures": sum(bool(case.get("prerequisite")) for case in captured),
              "expected_case_ids": list(helper.CASE_IDS), "missing_case_ids": missing, "unexpected_case_ids": unexpected,
              "failed_case_ids": failed, "error": error, "source_sha256": suite.source["sha256"], "binary_sha256": suite.binary_hash,
              "reference_sha256": suite.design["sha256"], "capture_plan_sha256": suite.design["plan_sha256"],
              "inputs_before": before, "inputs_after": after, "execution_input_snapshots": retained,
              "inputs_unchanged": unchanged, "raw_manifest_exit": raw_exit, "partial_run": True,
              "full_capture_suite_completed": False, "clean_round_credit": 0, "accepted": False,
              "review_boundary": "Original native glyphs00/01/02 and real configured names must be freshly read. Private preferred-width values and model strings are not glyph proof; no font reduction or synthetic identifier is used.",
              "historical_negative_basis": "C11 scope-typing202deb and select-last117249 with owner crops4b7ce remain historical actual clipped-label evidence; they are not relabeled as C12."}
    source.save_json(suite.output / "c12-qualification.json", report)
    print(f"C12 focused: {len(captured)} actual, passed={bool(passed)}, failures={len(failed)}, partial=true", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
