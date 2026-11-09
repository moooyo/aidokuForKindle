#!/usr/bin/env python3
"""Run or review the versioned complete C15 native handoff capture suite.

The frozen base driver owns the complete matrix, supplementary sequence and
review logic. Only three exact reader references gain their own closed-reader
prerequisite. Actual native case collections, never static estimates, supply
the reported count. Capture requires an explicit Root GO and frozen identities.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
from pathlib import Path
import re
import shutil
import sys
from typing import Any


BASE_HARNESS_SHA256 = "1f2379bffd9ce9594f307fa150f56c345fe508fca47aac3be31e8ce29a72fa44"
BASE_PLAN_SHA256 = "d07810abea322f7d2f86261f80e29007b3bd2f194eb2e9a3d4a53d8edcf70687"
SCOPE_SHA256 = "c910bed9f02ed17b2ca0a54aca1beeeeb6caabc5a17a91afe764ddd3e7b0d33c"
OVERLAY_IDS = ("sc-reader--layout", "sc-reader--light", "sc-reader--mode")
PREREQUISITE_IDS = tuple(f"{name}--same-case-closed-reader-prerequisite" for name in OVERLAY_IDS)
SCRIPT_NAMES = (
    "verify-handoff-ui-c15.py", "handoff_c15_artwork.py", "verify-handoff-ui.py", "verify-handoff-tags.py",
    "verify-ui.py", "verify-fullscreen.py", "handoff_c4_business.py", "handoff_c5_ui.py",
    "handoff_c5_reader.py", "handoff_c9_search.py", "handoff_c10_search.py",
    "handoff_c12_scope.py", "handoff_c13_scope.py",
)
VERSION_FIELDS = (
    "versioned_full_entrypoint_sha256", "versioned_artwork_helper_sha256", "versioned_full_plan_sha256",
    "versioned_base_harness_sha256", "versioned_base_plan_sha256",
)


def load(name: str, filename: str) -> Any:
    path = Path(__file__).resolve().with_name(filename)
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load the versioned C15 execution input: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def _paths(source: Any, args: Any, directory: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    paths = {name: directory / name for name in SCRIPT_NAMES}
    destinations = {name: Path("scripts") / name for name in SCRIPT_NAMES}
    plans = {"base_plan": directory.parent / "docs/review/phase3-handoff-capture-plan.json",
             "full_plan": args.plan, "reference_scope": args.scope}
    paths.update(plans)
    destinations.update({name: Path("plans") / path.name for name, path in plans.items()})
    scope = source.read_json(args.scope)
    references = {entry["reference"] for board in scope["pageStateMatrix"]
                  for entry in [board["default"], *board["states"]]}
    for item in scope.get("supportReferences", []):
        files = item.get("files", [item["file"]] if item.get("file") else [])
        if isinstance(files, list):
            references.update(files)
    root = args.reference_root.resolve(strict=True)
    for relative in sorted(references):
        path = root / relative
        name = f"reference:{relative}"
        paths[name] = path
        destinations[name] = Path("references") / path.resolve().relative_to(root)
    if len(set(destinations.values())) != len(destinations):
        raise ValueError("Every C15 full execution input requires a distinct snapshot path")
    return paths, destinations


def _retain(source: Any, suite: Any, paths: dict[str, Path], destinations: dict[str, Path], state: dict[str, Any]) -> None:
    for name, path in paths.items():
        snapshot = suite.output / "execution-inputs" / destinations[name]
        state["snapshot_paths"][name] = snapshot
        record = {"original_path": state["before"][name]["path"], "path": str(snapshot),
                  "expected_sha256": state["before"][name]["sha256"], "sha256": None, "bytes_retained": False}
        state["retained"][name] = record
        try:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, snapshot)
            record["sha256"] = source.file_hash(snapshot)
            record["bytes_retained"] = record["sha256"] == record["expected_sha256"]
            if not record["bytes_retained"]:
                raise ValueError(f"The actual C15 full input snapshot changed bytes: {name}")
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise


def _unchanged(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return set(before) == set(after) and all(before[name]["sha256"] is not None
                                           and before[name] == after[name] for name in before)


def _failure(source: Any, suite: Any, state: dict[str, Any], stage: str, error: BaseException) -> None:
    actual = f"{type(error).__name__}: {error}"
    state["errors"].append({"stage": stage, "error": actual})
    case = {"name": f"c15-full-{stage}-completion", "passed": False, "manual_review_required": False,
            "checks": [{"description": "The versioned complete-capture contract completed without a tool or product failure",
                        "passed": False, "actual": actual}]}
    suite.cases.append(case)
    try:
        suite.persist_case(case)
    except Exception as report_error:
        state["errors"].append({"stage": f"{stage}-error-report", "error": f"{type(report_error).__name__}: {report_error}"})


def _inventory(cases: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    captures = [case for case in cases if case.get("screenshot")]
    counts = Counter(case["name"] for case in captures)
    roles, artwork_passed = True, True
    missing, duplicate = [], sorted(name for name, count in counts.items() if count != 1)
    for overlay_id, prerequisite_id in zip(OVERLAY_IDS, PREREQUISITE_IDS):
        for name, prerequisite in ((prerequisite_id, True), (overlay_id, False)):
            matches = [case for case in captures if case["name"] == name]
            if len(matches) != 1:
                missing.append(name)
                roles, artwork_passed = False, False
                continue
            case = matches[0]
            roles = (roles and case.get("prerequisite") is prerequisite
                     and case.get("manual_review_required") is (not prerequisite)
                     and (prerequisite or case.get("reference_id") == overlay_id))
            art = case.get("c15_artwork", {})
            metrics = art.get("metrics", {})
            artwork_passed = (artwork_passed and case.get("passed") is True
                              and metrics.get("exact_match") is True and metrics.get("pixel_error_tolerance") == 0
                              and metrics.get("ignored_artwork_pixels_outside_ui_mask") == 0)
            if prerequisite:
                pure = case.get("pure_reader_oracle", {}).get("metrics", {})
                artwork_passed = (artwork_passed and art.get("retained_before_overlay_capture") is True
                                  and pure.get("exact_match") is True and pure.get("pixel_error_tolerance") == 0
                                  and pure.get("ignored_pixels") == 0)
            else:
                artwork_passed = artwork_passed and art.get("same_case_source_chain_equal") is True
    absent_pngs = [case["name"] for case in captures
                   if not Path(case["screenshot"]).is_file()
                   or Path(case["screenshot"]).resolve() != (output / f"{case['name']}.png").resolve()]
    return {"count_basis": "actual native casecollection", "actual_native_capture_count": len(captures),
            "actual_original_png_count": len(captures) - len(absent_pngs),
            "actual_manual_capture_count": sum(case.get("manual_review_required") is True for case in captures),
            "actual_prerequisite_capture_count": sum(case.get("prerequisite") is True for case in captures),
            "duplicate_native_capture_case_ids": duplicate, "missing_same_case_ids": missing,
            "missing_original_png_case_ids": absent_pngs, "same_case_roles_complete": bool(roles),
            "same_case_artwork_passed": bool(artwork_passed),
            "same_case_coverage_complete": not missing and not duplicate and not absent_pngs and bool(roles)}


def _versions(inputs: dict[str, Any]) -> dict[str, str]:
    return {"versioned_full_entrypoint_sha256": inputs["verify-handoff-ui-c15.py"]["sha256"],
            "versioned_artwork_helper_sha256": inputs["handoff_c15_artwork.py"]["sha256"],
            "versioned_full_plan_sha256": inputs["full_plan"]["sha256"],
            "versioned_base_harness_sha256": inputs["verify-handoff-ui.py"]["sha256"],
            "versioned_base_plan_sha256": inputs["base_plan"]["sha256"]}


def _versioned_manifest(source: Any, manifest: dict[str, Any], require_complete: bool = True) -> None:
    if (manifest.get("kind") != "phase3-handoff-native-ui-round"
            or manifest.get("versioned_full_capture") != "c15-full-same-case-artwork"
            or any(re.fullmatch(r"[0-9a-fA-F]{64}", str(manifest.get(key, ""))) is None for key in VERSION_FIELDS)
            or manifest.get("harness_sha256") != manifest["versioned_full_entrypoint_sha256"]
            or manifest.get("capture_plan_sha256") != manifest["versioned_full_plan_sha256"]
            or manifest.get("versioned_base_harness_sha256") != BASE_HARNESS_SHA256
            or manifest.get("versioned_base_plan_sha256") != BASE_PLAN_SHA256
            or manifest.get("required_c15_same_case_prerequisite_ids") != list(PREREQUISITE_IDS)):
        raise ValueError("The manifest is not bound to the exact versioned C15 full capture contract")
    helpers = manifest.get("capture_helper_sha256", {})
    if (helpers.get("verify-handoff-ui-c15.py") != manifest["versioned_full_entrypoint_sha256"]
            or helpers.get("handoff_c15_artwork.py") != manifest["versioned_artwork_helper_sha256"]
            or helpers.get("verify-handoff-ui.py") != BASE_HARNESS_SHA256):
        raise ValueError("The versioned C15 manifest does not bind its actual entry, artwork helper and frozen base")
    captured_inputs = manifest.get("versioned_inputs_before", {})
    captured_snapshots = manifest.get("versioned_snapshot_inputs_before", {})
    if (any(manifest.get(key) != value for key, value in _versions(captured_inputs).items())
            or any(helpers.get(name) != captured_inputs[name]["sha256"] for name in SCRIPT_NAMES)
            or set(captured_inputs) != set(captured_snapshots)
            or any(captured_inputs[name]["sha256"] != captured_snapshots[name]["sha256"] for name in captured_inputs)):
        raise ValueError("The C15 manifest identity labels are not connected to its retained actual input bytes")
    if require_complete:
        finalization_path = Path(manifest.get("versioned_finalization_file", ""))
        expected_finalization = Path(manifest["versioned_output"]) / "c15-full-finalization.json"
        if (manifest.get("versioned_finalized") is not True or manifest.get("versioned_terminal_error")
                or finalization_path.resolve() != expected_finalization.resolve() or not finalization_path.is_file()):
            raise ValueError("The complete C15 driver did not finalize all required capture reports")
        finalization = source.read_json(finalization_path)
        if (finalization.get("completed") is not True or finalization.get("initial_error")
                or finalization.get("capture_payload_sha256") != source.stable_hash(
                    {key: value for key, value in manifest.items() if key != "review"})):
            raise ValueError("The C15 finalization record is missing, failed or bound to different capture bytes")
        inventory = _inventory(manifest.get("cases", []), Path(manifest["versioned_output"]))
        if (manifest.get("mechanical_success") is not True or manifest.get("full_capture_suite_completed") is not True
                or manifest.get("reference_coverage_complete") is not True
                or manifest.get("versioned_execution_inputs_unchanged") is not True
                or manifest.get("versioned_execution_snapshots_unchanged") is not True
                or not inventory["same_case_coverage_complete"] or not inventory["same_case_artwork_passed"]):
            raise ValueError("A partial or failed C15 capture cannot supply complete clean-round review credit")
        snapshots = {name: Path(record["path"]) for name, record in manifest["versioned_execution_input_snapshots"].items()}
        actual_snapshots = {name: {"path": str(path.resolve()), "sha256": source.file_hash(path) if path.is_file() else None}
                            for name, path in snapshots.items()}
        if not _unchanged(manifest["versioned_snapshot_inputs_before"], actual_snapshots):
            raise ValueError("The retained complete-capture execution snapshots changed after capture")
        for case in manifest["cases"]:
            if case.get("name") in (*OVERLAY_IDS, *PREREQUISITE_IDS):
                for record in case.get("evidence_hashes", {}).values():
                    path = Path(record["path"])
                    if not path.is_file() or source.file_hash(path) != record["sha256"]:
                        raise ValueError("A required same-case closed or overlay evidence file changed after capture")


def _review_inputs(source: Any, args: Any, manifest: dict[str, Any], state: dict[str, Any],
                   paths: dict[str, Path]) -> dict[str, Any]:
    snapshots = {name: Path(record["path"]) for name, record in manifest["versioned_execution_input_snapshots"].items()}
    return {"inputs": state["guard"]._fingerprints(source, paths),
            "snapshots": state["guard"]._fingerprints(source, snapshots),
            "source_sha256": source.source_manifest(args.source_root)["sha256"],
            "binary_sha256": source.file_hash(Path(manifest["binary"])),
            "review_sha256": source.file_hash(args.record_review),
            "previous_manifest_sha256": source.file_hash(args.previous_manifest) if args.previous_manifest else None}


def _clear_review_credit(source: Any, path: Path, error: BaseException) -> None:
    current = source.read_json(path)
    if current.get("versioned_full_capture") != "c15-full-same-case-artwork" and not any(key in current for key in VERSION_FIELDS):
        return
    current.setdefault("review", {}).update({"clean_round": False, "clean_round_streak": 0, "accepted": False,
                                            "versioned_review_gate_passed": False,
                                            "versioned_review_error": f"{type(error).__name__}: {error}"})
    source.save_json(path, current)


def _record_review(source: Any, original_review: Any, args: Any, state: dict[str, Any], paths: dict[str, Path]) -> int:
    manifest_path = args.output / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = source.json.loads(manifest_bytes.decode("utf-8-sig"))
        _versioned_manifest(source, manifest)
        before = _review_inputs(source, args, manifest, state, paths)
        expected_versions = _versions(before["inputs"])
        if (not _unchanged(manifest["versioned_inputs_before"], before["inputs"])
                or not _unchanged(manifest["versioned_snapshot_inputs_before"], before["snapshots"])
                or any(manifest.get(key) != value for key, value in expected_versions.items())
                or any(manifest["capture_helper_sha256"].get(name) != before["inputs"][name]["sha256"] for name in SCRIPT_NAMES)
                or manifest["source_sha256"] != before["source_sha256"]
                or manifest["source_sha256"] != args.expected_source_sha256.lower()
                or manifest["binary_sha256"] != before["binary_sha256"]
                or manifest["binary_sha256"] != args.expected_sha256.lower()
                or manifest["versioned_full_plan_sha256"] != state["expected_full_plan_sha256"]):
            raise ValueError("The C15 review labels or files differ from the actual captured source, executable, tools, plan, references or snapshots")
        review_bytes = args.record_review.read_bytes()
        review = source.json.loads(review_bytes.decode("utf-8-sig"))
        if (source.hashlib.sha256(review_bytes).hexdigest() != before["review_sha256"]
                or any(review.get(key) != manifest[key] for key in VERSION_FIELDS)):
            raise ValueError("The frozen manual review must identify every exact C15 version field from its review template")
        external = review.get("external_evidence", [])
        if not isinstance(external, list) or any(not isinstance(item, dict) or item.get("passed") is not True for item in external):
            raise ValueError("Root external evidence must all report passed=True; the original coverage and scope gates also remain required")
        before["external_inputs"] = state["guard"]._fingerprints(
            source, {f"external:{index}": Path(item["artifact"]) for index, item in enumerate(external)})
        previous = None
        prior_external_paths: dict[str, Path] = {}
        if args.previous_manifest:
            previous_bytes = args.previous_manifest.read_bytes()
            previous = source.json.loads(previous_bytes.decode("utf-8-sig"))
            if source.hashlib.sha256(previous_bytes).hexdigest() != before["previous_manifest_sha256"]:
                raise ValueError("The previous manifest changed while its continuation evidence was read")
            if previous.get("review", {}).get("clean_round") is True:
                _versioned_manifest(source, previous)
                prior_review = previous["review"]
                if (prior_review.get("external_scope_complete") is not True or prior_review.get("root_external_all_true") is not True
                        or not prior_review.get("external_evidence")
                        or any(item.get("passed") is not True for item in prior_review["external_evidence"])):
                    raise ValueError("The previous C15 clean round lacks the required unchanged Root external all-True gate")
                for index, item in enumerate(prior_review["external_evidence"]):
                    path = Path(item["artifact"])
                    if (item.get("source_sha256") != previous["source_sha256"] or not path.is_file()
                            or source.file_hash(path) != item["sha256"]
                            or not isinstance(item.get("coverage"), list)
                            or any(not isinstance(coverage, str) for coverage in item["coverage"])):
                        raise ValueError("A previous clean round's actual Root external artifact changed source, bytes or coverage")
                    prior_external_paths[f"previous-external:{index}"] = path
        before["prior_external_inputs"] = state["guard"]._fingerprints(source, prior_external_paths)
        same_version = bool(previous) and all(previous.get(key) == manifest[key] for key in VERSION_FIELDS)
        if source.file_hash(manifest_path) != source.hashlib.sha256(manifest_bytes).hexdigest():
            raise ValueError("The captured manifest changed before the complete base review")
        result = original_review(args)
        updated = source.read_json(manifest_path)
        _versioned_manifest(source, updated)
        final_review = updated["review"]
        retained_external = final_review.get("external_evidence", [])
        all_true = (isinstance(retained_external, list) and bool(retained_external)
                    and all(isinstance(item, dict) and item.get("passed") is True for item in retained_external))
        after = _review_inputs(source, args, updated, state, paths)
        after["external_inputs"] = state["guard"]._fingerprints(
            source, {f"external:{index}": Path(item["artifact"]) for index, item in enumerate(retained_external)})
        after["prior_external_inputs"] = state["guard"]._fingerprints(source, prior_external_paths)
        unchanged_capture = ({key: value for key, value in manifest.items() if key != "review"}
                             == {key: value for key, value in updated.items() if key != "review"})
        if (before != after or not unchanged_capture or (final_review.get("same_candidate_as_previous") and not same_version)
                or (final_review.get("clean_round") and (final_review.get("external_scope_complete") is not True or not all_true))):
            raise ValueError("The final C15 source, binary, review, snapshots or Root external gate changed")
        final_review.update({"root_external_all_true": all_true, "same_versioned_candidate_as_previous": same_version,
                             "versioned_review_gate_passed": True})
        source.save_json(manifest_path, updated)
        return result
    except Exception as error:
        state["errors"].append({"stage": "record-review", "error": f"{type(error).__name__}: {error}"})
        try:
            _clear_review_credit(source, manifest_path, error)
            current = source.read_json(manifest_path)
            if current.get("versioned_full_capture") == "c15-full-same-case-artwork" or any(key in current for key in VERSION_FIELDS):
                source.save_json(args.output / "c15-full-review-error.json", {
                    "initial_error": state["errors"][0], "errors": state["errors"],
                    "clean_round": False, "clean_round_credit": 0, "accepted": False})
        except Exception as clear_error:
            state["errors"].append({"stage": "record-review-clear-credit", "error": f"{type(clear_error).__name__}: {clear_error}"})
            print(f"C15 review credit clear failed: {type(clear_error).__name__}: {clear_error}", file=sys.stderr, flush=True)
        raise


def main() -> int:
    original_argv, invocation = sys.argv, list(sys.argv)
    source, args, state = None, None, {"errors": [], "suite": None, "finish_attempted": False,
                                     "retained": {}, "snapshot_paths": {}, "snapshots_before": {}, "snapshots_after": {}}
    output_is_fresh = False
    try:
        directory = Path(__file__).resolve().parent
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--expected-full-plan-sha256", required=True)
        parser.add_argument("--full-capture-go", action="store_true")
        parser.add_argument("--plan", type=Path, default=directory.parent / "docs/review/phase3-handoff-capture-plan-c15.json")
        extra, inherited = parser.parse_known_args(invocation[1:])
        if re.fullmatch(r"[0-9a-fA-F]{64}", extra.expected_full_plan_sha256) is None:
            raise ValueError("The versioned complete driver requires --expected-full-plan-sha256 with the authorized 64-hex hash")
        guard = load("c15_full_existing_guard", "verify-handoff-tags.py")
        source = guard.load_source()
        try:
            sys.argv = [invocation[0], *inherited, "--plan", str(extra.plan)]
            args = source.arguments()
        finally:
            sys.argv = original_argv
        for field in ("expected_sha256", "expected_source_sha256"):
            if re.fullmatch(r"[0-9a-fA-F]{64}", str(getattr(args, field, ""))) is None:
                raise ValueError("Versioned full capture and review require both authorized source and binary 64-hex hashes")
        if any((args.source_manifest_only, args.subset, args.reader_vertical_case, args.selected_cases, args.matrix_only)):
            raise ValueError("The versioned complete driver rejects every partial, subset, matrix or source-manifest mode")
        if not args.record_review and not extra.full_capture_go:
            raise ValueError("Complete capture requires --full-capture-go after explicit Root authorization")
        if args.timeout != 90 or args.settled_reader_tail_ms != 5000:
            raise ValueError("The complete C15 driver retains the 90-second per-app and 5000-ms reader limits")
        if args.record_review:
            captured_manifest = source.read_json(args.output / "manifest.json")
            if args.reference_root is None:
                args.reference_root = Path(source.read_json(Path(captured_manifest["reference_manifest"]))["root"])
            if args.binary is None:
                args.binary = Path(captured_manifest["binary"])
            if args.source_root == source.ROOT:
                args.source_root = Path(captured_manifest["source_root"])
        if not args.record_review:
            guard._fresh_output(args.output)
            output_is_fresh = True
        artwork = load("handoff_c15_artwork", "handoff_c15_artwork.py")
        modules = {"verify-handoff-tags.py": guard, "verify-handoff-ui.py": source, "handoff_c15_artwork.py": artwork,
                   "verify-ui.py": source.native, "verify-fullscreen.py": source.oracle,
                   "handoff_c4_business.py": source.c4_business, "handoff_c5_ui.py": source.c5_ui,
                   "handoff_c5_reader.py": source.c5_reader, "handoff_c9_search.py": source.c10_search.c9,
                   "handoff_c10_search.py": source.c10_search, "handoff_c12_scope.py": source.c13_scope.scope,
                   "handoff_c13_scope.py": source.c13_scope}
        if any(Path(module.__file__).resolve() != (directory / name).resolve() for name, module in modules.items()):
            raise ValueError("An actual versioned full dependency is outside its frozen script path")
        if tuple(artwork.CASE_IDS) != OVERLAY_IDS or tuple(artwork.PREREQUISITE_IDS) != PREREQUISITE_IDS:
            raise ValueError("The full interceptor changed its three original overlays or exclusive prerequisite IDs")
        paths, destinations = _paths(source, args, directory)
        state.update({"guard": guard, "before": guard._fingerprints(source, paths),
                      "expected_full_plan_sha256": extra.expected_full_plan_sha256.lower()})
        before = state["before"]
        if any(record["sha256"] is None for record in before.values()):
            raise ValueError("Every actual complete-capture dependency, plan and reference must be present")
        if (before["verify-handoff-ui.py"]["sha256"] != BASE_HARNESS_SHA256
                or before["base_plan"]["sha256"] != BASE_PLAN_SHA256
                or before["reference_scope"]["sha256"] != SCOPE_SHA256
                or before["full_plan"]["sha256"] != state["expected_full_plan_sha256"]):
            raise ValueError("The complete C15 driver does not match its frozen base, scope or versioned full plan")
        base_plan, plan = source.read_json(paths["base_plan"]), source.read_json(args.plan)
        revision = plan.get("versioned_artwork_revision", {})
        inherited_plan = {key: value for key, value in plan.items() if key != "versioned_artwork_revision"}
        if (inherited_plan != base_plan or revision.get("version") != "c15-full-same-case-artwork"
                or revision.get("overlay_reference_case_ids") != list(OVERLAY_IDS)
                or revision.get("required_prerequisite_case_ids") != list(PREREQUISITE_IDS)
                or revision.get("fixed_added_prerequisite_captures") != 3):
            raise ValueError("The versioned full plan must preserve every original field and add only the three mandatory same-case prerequisites")
        original_suite, original_arguments, original_review = source.Suite, source.arguments, source.record_review

        class VersionedSuite(original_suite):
            def __init__(self, actual_args: Any):
                try:
                    super().__init__(actual_args)
                    state["suite"] = self
                    state["execution_before"] = {"argv": [sys.executable, *invocation], "inputs": before,
                                                  "execution_input_snapshots": state["retained"], "all_input_bytes_retained": False}
                    source.save_json(self.output / "c15-full-execution-before.json", state["execution_before"])
                    _retain(source, self, paths, destinations, state)
                    state["snapshots_before"] = guard._fingerprints(source, state["snapshot_paths"])
                    if (set(before) != set(state["snapshots_before"])
                            or any(state["snapshots_before"][name]["sha256"] != before[name]["sha256"] for name in before)):
                        raise ValueError("A complete C15 input snapshot changed before capture")
                    state["execution_before"].update({"all_input_bytes_retained": True, "snapshot_inputs": state["snapshots_before"]})
                    source.save_json(self.output / "c15-full-execution-before.json", state["execution_before"])
                except Exception as error:
                    if state["suite"] is self:
                        _failure(source, self, state, "input-freeze", error)
                        try:
                            state["snapshots_before"] = guard._fingerprints(source, state["snapshot_paths"])
                            state["execution_before"].update({"snapshot_inputs": state["snapshots_before"], "errors": state["errors"]})
                            source.save_json(self.output / "c15-full-execution-before.json", state["execution_before"])
                        except Exception as report_error:
                            _failure(source, self, state, "input-freeze-report", report_error)
                    else:
                        state["errors"].append({"stage": "suite-initialization", "error": f"{type(error).__name__}: {error}"})
                    raise

            def reference_case(self, board: dict[str, Any], entry: dict[str, Any]) -> Any:
                if entry["id"] in OVERLAY_IDS:
                    api = self.c4_api()
                    api.oracle = source.oracle
                    return artwork.run_case(self, api, board, entry)
                return super().reference_case(board, entry)

            def finish_manifest(self) -> int:
                state["finish_attempted"] = True
                finalization_path = self.output / "c15-full-finalization.json"
                try:
                    source.save_json(finalization_path, {"completed": False, "capture_payload_sha256": None,
                                                         "initial_error": None})
                    if not self.reference_ids:
                        self.reference_ids = [entry["id"] for board in self.scope["pageStateMatrix"]
                                              for entry in [board["default"], *board["states"]]]
                    inventory = _inventory(self.cases, self.output)
                    fixed_ids = {item["id"] for item in base_plan["reference_cases"]}
                    fixed_ids.update((*source.c5_ui.CASE_IDS, *source.c5_reader.CASE_IDS,
                                      *source.c10_search.CASE_IDS, *source.c13_scope.CASE_IDS, *PREREQUISITE_IDS))
                    captured = {case["name"] for case in self.cases if case.get("screenshot")}
                    missing_fixed = sorted(fixed_ids - captured)
                    if missing_fixed or not inventory["same_case_coverage_complete"]:
                        _failure(source, self, state, "case-inventory", ValueError(str({"missing_fixed": missing_fixed, **inventory})))
                    if not inventory["same_case_artwork_passed"]:
                        _failure(source, self, state, "same-case-artwork", ValueError("A required independent same-case artwork chain failed"))
                    raw_exit = super().finish_manifest()
                    state["raw_exit"] = raw_exit
                    raw_path, saved_path = self.output / "manifest.json", self.output / "c15-base-manifest.json"
                    state.update({"raw_path": str(raw_path), "raw_hash": source.file_hash(raw_path)})
                    shutil.copyfile(raw_path, saved_path)
                    raw_hash = source.file_hash(saved_path)
                    if raw_hash != source.file_hash(raw_path):
                        raise ValueError("The newly produced original base manifest did not retain its exact bytes")
                    manifest = source.read_json(raw_path)
                    state.update({"raw_exit": raw_exit, "raw_hash": raw_hash, "raw_path": str(saved_path),
                                  "after": guard._fingerprints(source, paths),
                                  "snapshots_after": guard._fingerprints(source, state["snapshot_paths"])})
                    unchanged = _unchanged(before, state["after"])
                    snapshots_unchanged = (set(before) == set(state["snapshots_before"])
                                           and _unchanged(state["snapshots_before"], state["snapshots_after"]))
                    identity = (manifest.get("harness_sha256") == BASE_HARNESS_SHA256
                                and manifest.get("source_sha256") == manifest.get("source_sha256_after") == args.expected_source_sha256.lower()
                                and manifest.get("binary_sha256") == source.file_hash(self.binary) == args.expected_sha256.lower()
                                and manifest.get("capture_plan_sha256") == state["expected_full_plan_sha256"]
                                and manifest.get("reference_scope_sha256") == SCOPE_SHA256)
                    if not unchanged or not snapshots_unchanged or not identity:
                        _failure(source, self, state, "execution-identity", ValueError("A captured source, executable, script, plan, reference or snapshot changed"))
                    for name in SCRIPT_NAMES:
                        manifest["capture_helper_sha256"].setdefault(name, before[name]["sha256"])
                    versions = _versions(before)
                    failures = [case["name"] for case in self.cases if not case.get("passed")]
                    complete = manifest["full_capture_suite_completed"] and not missing_fixed and inventory["same_case_coverage_complete"]
                    success = (manifest["mechanical_success"] and complete and not failures
                               and inventory["same_case_artwork_passed"] and unchanged and snapshots_unchanged and identity)
                    manifest.update({**versions, "harness_sha256": versions["versioned_full_entrypoint_sha256"],
                                     "versioned_full_capture": "c15-full-same-case-artwork", "versioned_output": str(self.output),
                                     "required_c15_same_case_prerequisite_ids": list(PREREQUISITE_IDS),
                                     "required_c15_overlay_reference_ids": list(OVERLAY_IDS), "required_fixed_case_catalogue": sorted(fixed_ids),
                                     "missing_required_fixed_case_ids": missing_fixed, "native_case_inventory": inventory,
                                     "count_basis": "actual native casecollection", "actual_native_capture_count": inventory["actual_native_capture_count"],
                                     "full_capture_suite_completed": bool(complete), "mechanical_success": bool(success),
                                     "cases": self.cases, "failed_cases": failures, "failed": len(failures), "passed": len(self.cases) - len(failures),
                                     "versioned_inputs_before": before, "versioned_inputs_after": state["after"],
                                     "versioned_execution_input_snapshots": state["retained"],
                                     "versioned_snapshot_inputs_before": state["snapshots_before"], "versioned_snapshot_inputs_after": state["snapshots_after"],
                                     "versioned_execution_inputs_unchanged": unchanged, "versioned_execution_snapshots_unchanged": snapshots_unchanged,
                                     "versioned_source_binary_plan_identity_passed": identity, "versioned_errors": state["errors"],
                                     "versioned_finalized": True, "versioned_finalization_file": str(finalization_path),
                                     "versioned_terminal_error": None,
                                     "raw_base_manifest": {"path": str(saved_path), "sha256": raw_hash, "exit_code": raw_exit}})
                    template_path = self.output / "review-template.json"
                    template = source.read_json(template_path)
                    template.update(versions)
                    source.save_json(template_path, template)
                    source.save_json(self.output / "c15-full-execution-after.json", {
                        "inputs": state["after"], "snapshot_inputs": state["snapshots_after"], "inventory": inventory,
                        "execution_inputs_unchanged": unchanged, "execution_snapshots_unchanged": snapshots_unchanged,
                        "raw_base_manifest": manifest["raw_base_manifest"], "errors": state["errors"]})
                    with (self.output / "manifest.md").open("a", encoding="utf-8") as report:
                        report.write(f"\nVersioned C15 complete capture: {inventory['actual_native_capture_count']} actual native cases; "
                                     f"{inventory['actual_original_png_count']} original PNGs; mechanical success={bool(success)}.\n"
                                     "Count basis: actual native casecollection. Fresh complete manual review and Root external all-True evidence remain required.\n")
                    source.save_json(raw_path, manifest)
                    finalization_commit = finalization_path.with_name("c15-full-finalization.commit.json")
                    source.save_json(finalization_commit, {
                        "completed": True, "initial_error": None,
                        "capture_payload_sha256": source.stable_hash({key: value for key, value in manifest.items() if key != "review"})})
                    finalization_commit.replace(finalization_path)
                    return 0 if success else 1
                except Exception as error:
                    state["errors"].append({"stage": "finish-manifest", "error": f"{type(error).__name__}: {error}"})
                    try:
                        source.save_json(finalization_path, {"completed": False, "capture_payload_sha256": None,
                                                             "initial_error": state["errors"][0], "errors": state["errors"]})
                    except Exception as marker_error:
                        state["errors"].append({"stage": "finish-finalization-error-report",
                                                "error": f"{type(marker_error).__name__}: {marker_error}"})
                    try:
                        current_path = self.output / "manifest.json"
                        if current_path.is_file():
                            current = source.read_json(current_path)
                            if current.get("versioned_full_capture") == "c15-full-same-case-artwork":
                                current.update({"mechanical_success": False, "full_capture_suite_completed": False,
                                                "versioned_finalized": False, "versioned_terminal_error": state["errors"][0],
                                                "versioned_errors": state["errors"]})
                                current.setdefault("review", {}).update({"clean_round": False, "clean_round_streak": 0,
                                                                          "accepted": False, "versioned_review_gate_passed": False})
                                source.save_json(current_path, current)
                    except Exception as invalidation_error:
                        state["errors"].append({"stage": "finish-manifest-invalidation",
                                                "error": f"{type(invalidation_error).__name__}: {invalidation_error}"})
                    raise

        source.Suite = VersionedSuite
        source.arguments = lambda: args
        source.record_review = lambda actual_args: _record_review(source, original_review, actual_args, state, paths)
        try:
            result = source.main()
            if result == 2 and state["suite"] is not None and not state["finish_attempted"]:
                state["suite"].finish_manifest()
            if state["errors"] and output_is_fresh:
                source.save_json(args.output / "c15-full-driver-error.json", {"initial_error": state["errors"][0], "errors": state["errors"],
                                 "execution_input_snapshots": state["retained"], "base_exit_code": result,
                                 "raw_base_manifest": {"path": state.get("raw_path"), "sha256": state.get("raw_hash"), "exit_code": state.get("raw_exit")}})
            return result
        finally:
            source.Suite, source.arguments, source.record_review = original_suite, original_arguments, original_review
    except Exception as error:
        state["errors"].append({"stage": "driver", "error": f"{type(error).__name__}: {error}"})
        if args is not None and args.record_review and source is not None:
            try:
                manifest_path = args.output / "manifest.json"
                _clear_review_credit(source, manifest_path, error)
                current = source.read_json(manifest_path)
                if current.get("versioned_full_capture") == "c15-full-same-case-artwork" or any(key in current for key in VERSION_FIELDS):
                    source.save_json(args.output / "c15-full-review-error.json", {
                        "initial_error": state["errors"][0], "errors": state["errors"],
                        "clean_round": False, "clean_round_credit": 0, "accepted": False})
            except Exception as clear_error:
                print(f"C15 review preparation credit clear failed: {type(clear_error).__name__}: {clear_error}", file=sys.stderr, flush=True)
        elif output_is_fresh and args is not None and source is not None:
            try:
                source.save_json(args.output / "c15-full-driver-error.json", {"initial_error": state["errors"][0], "errors": state["errors"],
                                 "inputs_before": state.get("before", {}), "execution_input_snapshots": state["retained"],
                                 "raw_base_manifest": {"path": state.get("raw_path"), "sha256": state.get("raw_hash"), "exit_code": state.get("raw_exit")}})
            except Exception as report_error:
                print(f"C15 full error report failed: {type(report_error).__name__}: {report_error}", file=sys.stderr, flush=True)
        print(f"C15 full capture failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    sys.exit(main())
