#!/usr/bin/env python3
"""Detect the accepted C6 long-frame defect in three unchanged native originals.

Run only on the authorized Linux worker. This driver imports only the adjacent
pure pixel helper; it never starts an application, imports a capture suite,
rewrites historical cases, or edits production inputs. A successful negative
report means that the new rule detects all three retained old defects. It does
not replace the historical 36/36 result or qualify a fresh product candidate.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import sys
from typing import Any


OLD_MANIFEST_SHA256 = "aa389850a0c5c2f41c266307964ffc95faa9b4b1a3247ab568ed5833c8b2547e"
OLD_SOURCE_SHA256 = "adafa23e6e1e4906990c3162025dd26d85e20e2f2bb4bed2f21c980bc4d565f5"
OLD_BINARY_SHA256 = "baafaf4b29a7a6629e8ef95da9dae38da323adccaa89288a92afe3bc4318e0ee"
OLD_RUN_ID = "a2f53f0c33db"
OLD_CAPTURE_COUNT = 36
OLD_REMOTE_ROOT = "/root/aidoku-phase3/evidence/candidate6/ui-tag-qualification-final-01"
NEGATIVES = (
    {"case_id": "pw5--c6-tag-long-neutral", "device": "pw5", "excluded": False,
     "png_sha256": "af67525758a2da77ffdaf6475a9fdaff01258eea13c2144ac2022eb297b350d7",
     "case_json_sha256": "47ab90cbafacf9c4817866ed7102102d05f9c8c7711dd961756de6d2039a692c",
     "dimensions": [618, 824], "expected_full_caller_ltrb": [22, 583, 596, 625]},
    {"case_id": "scribe--c6-tag-long-neutral", "device": "scribe", "excluded": False,
     "png_sha256": "a16beb7d6fc5ed229d76547177ada858408766a5cb279b412e597280c377ac1c",
     "case_json_sha256": "ee41943d8c5fbd09d14012ff278768a353444dd1f2debaad24b5dc52c4ca469a",
     "dimensions": [930, 1240], "expected_full_caller_ltrb": [310, 597, 874, 643]},
    {"case_id": "pw5--c6-tag-long-excluded", "device": "pw5", "excluded": True,
     "png_sha256": "8dfa95b4da595d2207da01a7e35a594750367fce59e4e3d523f24367e4fed02e",
     "case_json_sha256": "b6927957baab815a7ae74477f89f40e2893b4891440530187f6a81ed23d6d0db",
     "dimensions": [618, 824], "expected_full_caller_ltrb": [22, 583, 596, 625]},
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True,
                        help="Unchanged C6 final qualification directory containing manifest.json and cases/")
    parser.add_argument("--output", type=Path, required=True,
                        help="Fresh independent negative-report directory outside the old evidence root")
    args = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run this read-only pixel qualification only on the authorized Linux worker; local execution is forbidden.")
    return args


def _fingerprint(path: Path) -> dict[str, Any]:
    try:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        return {"path": str(path), "bytes": size, "sha256": digest.hexdigest()}
    except OSError as error:
        return {"path": str(path), "bytes": None, "sha256": None,
                "error": f"{type(error).__name__}: {error}"}


def _fingerprints(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {name: _fingerprint(path) for name, path in paths.items()}


def _unchanged(before: dict[str, Any], after: dict[str, Any], expected_count: int) -> bool:
    return (len(before) == expected_count and set(before) == set(after)
            and all(before[name].get("sha256") is not None and before[name] == after[name]
                    for name in before))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _input_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve(strict=True)
    if not path.is_file() or root not in path.parents:
        raise ValueError(f"An old input is not a regular file inside the evidence root: {relative}")
    return path


def _prepare_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    root = args.evidence_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("The historical evidence root must be an existing directory")
    output = args.output.expanduser().resolve()
    if output == root or root in output.parents or output in root.parents:
        raise ValueError("The new report and historical evidence directories must be disjoint")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("The negative output directory is not empty; choose a fresh directory")
    output.mkdir(parents=True, exist_ok=True)
    return root, output


def _manifest_inputs(root: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    cases = manifest.get("cases", [])
    names = [case.get("name") for case in cases]
    if (len(cases) != OLD_CAPTURE_COUNT or len(set(names)) != OLD_CAPTURE_COUNT
            or any(not isinstance(name, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) is None
                   for name in names)):
        raise ValueError("The pinned historical manifest must contain exactly 36 distinct native case IDs")
    paths = {"manifest.json": _input_path(root, "manifest.json")}
    for name in names:
        for relative in (f"{name}.png", f"cases/{name}.json"):
            paths[relative] = _input_path(root, relative)
    return paths


def _old_result(manifest: dict[str, Any]) -> dict[str, Any]:
    result = {"case_count": len(manifest.get("cases", [])), "passed": manifest.get("passed"),
              "failed": manifest.get("failed"), "failed_cases": manifest.get("failed_cases"),
              "mechanical_success": manifest.get("mechanical_success"),
              "full_capture_suite_completed": manifest.get("full_capture_suite_completed"),
              "reference_coverage_complete": manifest.get("reference_coverage_complete"),
              "clean_round": manifest.get("review", {}).get("clean_round")}
    if not (manifest.get("kind") == "phase3-handoff-native-ui-round"
            and manifest.get("run_id") == OLD_RUN_ID
            and manifest.get("source_sha256") == OLD_SOURCE_SHA256
            and manifest.get("binary_sha256") == OLD_BINARY_SHA256
            and result["case_count"] == OLD_CAPTURE_COUNT and result["passed"] == OLD_CAPTURE_COUNT
            and result["failed"] == 0 and result["failed_cases"] == []
            and result["mechanical_success"] is False
            and result["full_capture_suite_completed"] is False
            and result["reference_coverage_complete"] is False and result["clean_round"] is False
            and all(case.get("passed") is True for case in manifest["cases"])):
        raise ValueError("The historical source/binary identity or unchanged 36/36 partial result does not match")
    return result


def _load_pixels(path: Path) -> Any:
    specification = importlib.util.spec_from_file_location("handoff_tag_frame_negative_pixels", path)
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load the adjacent pure pixel helper: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    if Path(module.__file__).resolve() != path or not callable(getattr(module, "inspect_long_frame", None)):
        raise ValueError("The loaded adjacent helper lacks the frozen pure long-frame API")
    return module


def _inspect_case(pixels: Any, root: Path, spec: dict[str, Any],
                  paths: dict[str, Path], before: dict[str, Any]) -> dict[str, Any]:
    identifier = spec["case_id"]
    png_key, case_key = f"{identifier}.png", f"cases/{identifier}.json"
    png_path, case_path = paths[png_key], paths[case_key]
    if (before[png_key].get("sha256") != spec["png_sha256"]
            or before[case_key].get("sha256") != spec["case_json_sha256"]):
        raise ValueError(f"The exact retained PNG or case JSON identity differs: {identifier}")
    old_case = _read_json(case_path)
    old_screenshot = old_case.get("screenshot")
    if not (old_case.get("name") == identifier and old_case.get("device") == spec["device"]
            and old_case.get("passed") is True and old_case.get("run_id") == OLD_RUN_ID
            and old_case.get("source_sha256") == OLD_SOURCE_SHA256
            and old_case.get("binary_sha256") == OLD_BINARY_SHA256
            and isinstance(old_screenshot, str) and PurePosixPath(old_screenshot).name == png_key
            and old_case.get("evidence_hashes", {}).get("screenshot", {}).get("sha256") == spec["png_sha256"]):
        raise ValueError(f"The retained passed case is not the expected original: {identifier}")
    from PIL import Image

    with Image.open(png_path) as original:
        dimensions = list(original.size)
        if original.format != "PNG" or dimensions != spec["dimensions"]:
            raise ValueError(f"The retained original PNG dimensions or encoding differ: {identifier}")
    # Only the in-memory screenshot pathname is remapped. Historical state,
    # classification, check results, and every original file remain untouched.
    mapped_case = copy.deepcopy(old_case)
    mapped_case["screenshot"] = str(root / png_key)
    mapped_before = copy.deepcopy(mapped_case)
    inspected = pixels.inspect_long_frame(mapped_case, excluded=spec["excluded"])
    checks = inspected.get("checks")
    if not (mapped_case == mapped_before and isinstance(checks, list) and bool(checks)
            and all(isinstance(item, dict) and type(item.get("passed")) is bool for item in checks)
            and type(inspected.get("passed")) is bool
            and inspected["passed"] == all(item["passed"] for item in checks)
            and inspected.get("source_screenshot_sha256") == spec["png_sha256"]
            and inspected.get("source_dimensions") == spec["dimensions"]
            and inspected.get("expected_ltrb") == spec["expected_full_caller_ltrb"]
            and inspected.get("frame", {}).get("rectangle") == spec["expected_full_caller_ltrb"]):
        raise ValueError(f"The pure frame observation does not preserve the exact caller/input contract: {identifier}")
    return {"case_id": identifier, "device": spec["device"], "excluded": spec["excluded"],
            "historical_case_passed": old_case["passed"], "historical_screenshot_path": old_screenshot,
            "mapped_screenshot_path": mapped_case["screenshot"], "input_identity_verified": True,
            "expected_png_sha256": spec["png_sha256"], "expected_case_json_sha256": spec["case_json_sha256"],
            "expected_dimensions": spec["dimensions"], "expected_full_caller_ltrb": spec["expected_full_caller_ltrb"],
            "old_png_before": before[png_key], "old_case_json_before": before[case_key],
            "would_fail_new_frame_rule": inspected["passed"] is False,
            "observation": inspected,
            "negative_criterion": "The complete new frame rule fails; edge ratios and missing-run limits need not both fail."}


def main() -> int:
    args = arguments()
    output = None
    root = None
    paths: dict[str, Path] = {}
    before: dict[str, Any] = {}
    tools_before: dict[str, Any] = {}
    historical_result: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    driver = Path(__file__).resolve()
    tool_paths = {"driver": driver, "pixel_helper": driver.with_name("handoff_tag_pixels.py")}
    try:
        root, output = _prepare_paths(args)
        tools_before = _fingerprints(tool_paths)
        if any(value.get("sha256") is None for value in tools_before.values()):
            raise ValueError("The driver or adjacent pixel helper cannot be fingerprinted")
        manifest_path = _input_path(root, "manifest.json")
        paths["manifest.json"] = manifest_path
        before = _fingerprints(paths)
        if before["manifest.json"]["sha256"] != OLD_MANIFEST_SHA256:
            raise ValueError("The historical manifest does not match its fixed original SHA256")
        manifest = _read_json(manifest_path)
        historical_result = _old_result(manifest)
        paths = _manifest_inputs(root, manifest)
        before = _fingerprints(paths)
        if before["manifest.json"]["sha256"] != OLD_MANIFEST_SHA256 or any(
                value.get("sha256") is None for value in before.values()):
            raise ValueError("The historical native input set changed or cannot be read")
        # Do not create bytecode files next to tools or inside old evidence.
        sys.dont_write_bytecode = True
        pixels = _load_pixels(tool_paths["pixel_helper"])
        for spec in NEGATIVES:
            try:
                results.append(_inspect_case(pixels, root, spec, paths, before))
            except Exception as error:
                message = f"{spec['case_id']}: {type(error).__name__}: {error}"
                errors.append(message)
                results.append({"case_id": spec["case_id"], "would_fail_new_frame_rule": False,
                                "input_identity_verified": False, "error": message})
    except Exception as error:
        errors.append(f"{type(error).__name__}: {error}")
    after = _fingerprints(paths)
    tools_after = _fingerprints(tool_paths) if tools_before else {}
    old_unchanged = _unchanged(before, after, 1 + 2 * OLD_CAPTURE_COUNT)
    tools_unchanged = _unchanged(tools_before, tools_after, 2)
    for result in results:
        identifier = result["case_id"]
        result["old_png_after"] = after.get(f"{identifier}.png")
        result["old_case_json_after"] = after.get(f"cases/{identifier}.json")
    detected = sum(result.get("would_fail_new_frame_rule") is True for result in results)
    success = (not errors and len(results) == len(NEGATIVES) and detected == len(NEGATIVES)
               and old_unchanged and tools_unchanged)
    report = {"schema_version": 1, "kind": "c7-old-original-long-tag-frame-negative-qualification",
              "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "detection_success": bool(success), "negative_count": len(NEGATIVES),
              "actual_observation_count": sum(result.get("input_identity_verified") is True for result in results),
              "detected_negative_count": detected, "negative_cases": results, "errors": errors,
              "evidence_root": str(root) if root is not None else str(args.evidence_root),
              "original_remote_evidence_root": OLD_REMOTE_ROOT,
              "fixed_old_identity": {"manifest_sha256": OLD_MANIFEST_SHA256, "source_sha256": OLD_SOURCE_SHA256,
                                     "binary_sha256": OLD_BINARY_SHA256, "run_id": OLD_RUN_ID},
              "historical_result_preserved": historical_result,
              "old_36_native_cases_and_originals_unchanged": old_unchanged,
              "old_evidence_before": before, "old_evidence_after": after,
              "execution": {"argv": [sys.executable, str(driver), *sys.argv[1:]],
                            "platform": platform.platform(), "python": platform.python_version(),
                            "tool_fingerprints_before": tools_before, "tool_fingerprints_after": tools_after,
                            "driver_and_pixel_helper_unchanged": tools_unchanged,
                            "pixel_api": "inspect_long_frame(case, *, excluded=False)",
                            "application_starts": 0, "production_edits": 0, "old_case_writes": 0,
                            "original_png_writes": 0},
              "partial_run": True, "full_capture_suite_completed": False, "clean_round": False,
              "clean_round_credit": 0, "accepted": False,
              "scope": "Read-only negative detection on three exact old originals. Historical 36/36 PASS is retained; no old case is reclassified and no fresh product or complete round is qualified."}
    if output is None:
        print("Negative frame qualification failed: " + "; ".join(errors), file=sys.stderr, flush=True)
        return 2
    try:
        report_path = output / "tag-frame-negatives.json"
        with report_path.open("x", encoding="utf-8") as destination:
            destination.write(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n")
    except (OSError, TypeError, ValueError) as error:
        print(f"Cannot retain the independent negative report: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2
    print(f"Old frame negatives: {detected}/{len(NEGATIVES)} detected; detection_success={bool(success)}; old36_unchanged={old_unchanged}; application_starts=0; report={report_path}", flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
