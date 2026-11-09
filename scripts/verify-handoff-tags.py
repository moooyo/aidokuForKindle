#!/usr/bin/env python3
"""Capture the bounded C6 affected-tag qualification on the authorized Linux worker.

This driver reuses the full main harness tag-page implementation. It never builds
software, edits production state, or grants complete-suite or clean-round credit.
The expected source and executable hashes are mandatory. Each native application
retains the main harness timeout, original captures, and assertion contracts.
"""

from __future__ import annotations

from contextlib import ExitStack
import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import threading
from typing import Any


REFERENCE_IDS = ("pw-source--filter", "sc-browse--filter")
PROFILES = ("pw5", "scribe")
NORMAL_TAG_COUNT = 34
LONG_TAG = "Actual tag 00 " + "Long provider metadata " * 64 + "DISTINCT-SUFFIX-00"
LONG_SUFFIX = "DISTINCT-SUFFIX-00"


def load_source() -> Any:
    path = Path(__file__).resolve().with_name("verify-handoff-ui.py")
    specification = importlib.util.spec_from_file_location("handoff_tags_source", path)
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load the existing native handoff harness: {path}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _arguments(source: Any) -> Any:
    # Keep the inherited Linux, binary, reference-root, and xvfb-run guards.
    args = source.arguments()
    for field in ("expected_sha256", "expected_source_sha256"):
        value = getattr(args, field, None)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
            option = "--expected-sha256" if field == "expected_sha256" else "--expected-source-sha256"
            raise ValueError(f"Affected-tag qualification requires {option} with the authorized frozen SHA256")
    if any((args.source_manifest_only, args.record_review, args.subset,
            args.reader_vertical_case, args.selected_cases, args.matrix_only)):
        raise ValueError("This fixed affected-tag driver cannot be combined with main-harness selection or review modes")
    return args


def _fresh_output(path: Path) -> None:
    """Reject existing evidence before Suite can create or replace any file."""
    if path.is_symlink():
        raise ValueError("Affected-tag output must not be a symbolic link")
    if path.exists():
        if not path.is_dir():
            raise ValueError("Affected-tag output must be a new or empty directory")
        if next(path.iterdir(), None) is not None:
            raise ValueError("Affected-tag output already contains evidence; choose a fresh directory")


def _fingerprints(source: Any, paths: dict[str, Path]) -> dict[str, Any]:
    return {name: {"path": str(path.resolve()),
                   "sha256": source.file_hash(path) if path.is_file() else None}
            for name, path in paths.items()}


def _case(suite: Any, name: str) -> dict[str, Any]:
    matches = [case for case in suite.cases if case.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one actual captured case {name}; found {len(matches)}")
    return matches[0]


def _failure(source: Any, suite: Any, name: str, error: BaseException) -> None:
    case = {
        "name": name, "passed": False, "manual_review_required": False,
        "checks": [{"description": "The fixed affected-tag stage completed without a driver error",
                    "passed": False, "actual": f"{type(error).__name__}: {error}"}],
        "c6_tag_qualification": True, "partial_run": True,
    }
    suite.cases.append(case)
    suite.persist_case(case)


def _retain(source: Any, suite: Any, case: dict[str, Any], label: str, payload: Any) -> None:
    path = suite.output / "fixtures" / f"{case['name']}--{label}.json"
    source.save_json(path, payload)
    case.setdefault("evidence_hashes", {})[f"c6-tags:{label}"] = {
        "path": str(path), "sha256": source.file_hash(path),
    }


def _mark(suite: Any, case: dict[str, Any], role: str) -> None:
    case["c6_tag_qualification"] = True
    case["c6_tag_role"] = role
    case["qualification_scope"] = "Affected tags only; the full reference, supplemental and C5 control gates remain unchanged."
    suite.persist_case(case)


def _request_records(provider: Any) -> list[dict[str, Any]]:
    with provider.lock:
        return copy.deepcopy(provider.requests)


class LongMetadataVariant:
    """Change only real HTTP metadata after the ordinary full-page phases finish."""

    def __init__(self, source: Any, suite: Any, provider: Any, device: str):
        self.source = source
        self.suite = suite
        self.provider = provider
        self.device = device
        self.enabled = False
        self.start_request_index = 0
        self.sequence = 0
        self.records: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.claimed_requests: set[int] = set()
        self.lock = threading.Lock()
        self.handler = provider.server.RequestHandlerClass
        self.original_respond = self.handler.respond
        owner = self

        def respond(request: Any, status: int, body: Any,
                    content_type: str = "application/json") -> None:
            outgoing = body
            try:
                if owner.enabled and status == 200 and isinstance(body, dict) and isinstance(body.get("content"), list):
                    changed = copy.deepcopy(body)
                    replaced = 0
                    for item in changed["content"]:
                        if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
                            continue
                        tags = item["metadata"].get("tags")
                        if isinstance(tags, list):
                            replaced += sum(tag == "Actual tag 00" for tag in tags)
                            item["metadata"]["tags"] = [LONG_TAG if tag == "Actual tag 00" else tag for tag in tags]
                    if replaced:
                        outgoing = changed
                        owner._record_response(request, status, content_type, body, outgoing, replaced)
            except Exception as error:
                with owner.lock:
                    owner.errors.append(f"{type(error).__name__}: {error}")
                # Preserve a real HTTP response even if provenance cannot be saved.
                # The native long-tag identity and the final error checks then fail.
                outgoing = body
            owner.original_respond(request, status, outgoing, content_type)

        self.handler.respond = respond

    def enable(self) -> None:
        self.start_request_index = len(_request_records(self.provider))
        self.enabled = True

    def restore(self) -> None:
        self.enabled = False
        self.handler.respond = self.original_respond

    def _record_response(self, request: Any, status: int, content_type: str,
                         original: dict[str, Any], outgoing: dict[str, Any], replaced: int) -> None:
        requests = _request_records(self.provider)
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
            candidates = [index for index, item in enumerate(requests)
                          if index >= self.start_request_index and index not in self.claimed_requests
                          and item.get("method") == request.command and item.get("path") == request.path]
            request_index = candidates[0] if len(candidates) == 1 else None
            if request_index is not None:
                self.claimed_requests.add(request_index)
        directory = self.suite.output / "fixtures" / f"{self.device}--c6-long-http"
        directory.mkdir(parents=True, exist_ok=True)
        # Match the existing handler's json.dumps encoding exactly, retaining
        # both the baseline body and the body handed to the real HTTP writer.
        original_raw = json.dumps(original).encode("utf-8")
        outgoing_raw = json.dumps(outgoing).encode("utf-8")
        original_path = directory / f"response-{sequence:03}-original.json"
        outgoing_path = directory / f"response-{sequence:03}-sent.json"
        original_path.write_bytes(original_raw)
        outgoing_path.write_bytes(outgoing_raw)
        record = {
            "sequence": sequence, "method": request.command, "path": request.path,
            "request_index_zero_based": request_index, "matching_request_indices": candidates,
            "status": status, "content_type": content_type, "replacements": replaced,
            "original_json": {"path": str(original_path), "bytes": len(original_raw),
                              "sha256": self.source.file_hash(original_path)},
            "sent_json": {"path": str(outgoing_path), "bytes": len(outgoing_raw),
                          "sha256": self.source.file_hash(outgoing_path)},
            "actual_handler": "The existing fixture RequestHandlerClass.respond writes this JSON to the native application's real HTTP request.",
            "scope": "Public fixture provider metadata only; no application model, database tag, or image is edited.",
        }
        with self.lock:
            self.records.append(record)
        self.source.save_json(directory / f"response-{sequence:03}-provenance.json", record)

    def evidence(self) -> dict[str, Any]:
        with self.lock:
            return {"enabled_after_normal_full_pages": self.enabled,
                    "start_request_index_zero_based": self.start_request_index,
                    "responses": copy.deepcopy(self.records), "errors": list(self.errors)}


def _target(suite: Any, first: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    tags = first.get("state", {}).get("catalog_tag_options", [])
    rectangle, hit = control.get("rectangle"), control.get("hit")
    valid = (isinstance(rectangle, (list, tuple)) and len(rectangle) == 4
             and isinstance(hit, (list, tuple)) and len(hit) == 2
             and all(type(value) in (int, float) for value in [*rectangle, *hit]))
    if valid:
        left, top, width, height = rectangle
        valid = (width > 0 and height > 0 and left < hit[0] < left + width
                 and top < hit[1] < top + height
                 and abs(hit[0] - (left + width / 2)) <= 1
                 and abs(hit[1] - (top + height / 2)) <= 1)
    suite.check(first, "The excluded replay uses the actual first neutral chip's measured native center",
                valid and bool(tags) and len(tags) == NORMAL_TAG_COUNT
                and all(tag.get("state") == 0 for tag in tags),
                {"control": control, "tag_count": len(tags)})
    suite.persist_case(first)
    if not valid or not tags or len(tags) != NORMAL_TAG_COUNT or any(tag.get("state") != 0 for tag in tags):
        raise ValueError("The actual first neutral chip is unavailable; no semantic or estimated hit target is substituted")
    return {"id": tags[0]["id"], "title": tags[0]["title"],
            "hit": list(hit), "rectangle": list(rectangle)}


def _excluded(source: Any, suite: Any, context: dict[str, Any], first: dict[str, Any],
              target: dict[str, Any], name: str, *, online: bool, long: bool = False) -> dict[str, Any]:
    replay = (source.Replay().intent("sources", "open", "komga", wait=1400 if online else 450)
              .intent("catalog", "filter-menu").intent("catalog", "reset-filter")
              .tap(*target["hit"], wait=200).tap(*target["hit"], wait=200))
    case = suite.capture(name, "catalog", device=context["device"], demo=False,
                         offline=not online, data=context["data"], replay=replay,
                         modal="choices", context="catalog-filter")
    _mark(suite, case, "long-excluded-pointer" if long else "normal-excluded-pointer")
    original = first["state"].get("catalog_tag_options", [])
    actual = case["state"].get("catalog_tag_options", [])
    expected_ids = [tag["id"] for tag in original]
    suite.check(case, "Two actual pointer taps exclude precisely the measured provider tag and leave every other tag neutral",
                [tag.get("id") for tag in actual] == expected_ids
                and all(tag.get("state") == (-1 if tag.get("id") == target["id"] else 0) for tag in actual),
                {"target": target["id"], "states": [{"id": tag.get("id"), "state": tag.get("state")} for tag in actual]})
    actions = source.read_json(Path(case["actions"]))
    pointers = [action for action in actions if action.get("section") == "pointer"]
    suite.check(case, "The excluded-state action proof contains real native presses and releases without filter-choice substitution",
                sum(action.get("action") == "press" for action in pointers) == 2
                and sum(action.get("action") == "release" for action in pointers) == 2
                and len(pointers) == 6 and case.get("pointer_events") == 6
                and not any(action.get("section") == "catalog" and action.get("action") == "filter-choice" for action in actions),
                pointers)
    observation = {"origin_case_id": first["name"], "original_screenshot": first["screenshot"],
                   "original_sha256": source.file_hash(Path(first["screenshot"])),
                   "target": target, "actual_pointer_actions": pointers,
                   "scope": "Measured real chip center, two native pointer presses/releases, and actual excluded model state"}
    case["c6_excluded_pointer_origin"] = observation
    case.setdefault("visible_ui_requirements", []).append(
        "The actual excluded chip retains its visible exclusion glyph and frame; inspect the original native PNG.")
    if long:
        case["independent_original_review"] = True
        case["long_tail_visibility"] = {"status": "independent_original_review_required",
                                        "distinguishing_suffix": LONG_SUFFIX,
                                        "state_is_not_rendered_text_proof": True}
    _retain(source, suite, case, "excluded-pointer-origin", observation)
    suite.save_provider(name, context["provider"], case)
    suite.persist_case(case)
    return case


def _normal_profile(source: Any, suite: Any, context: dict[str, Any]) -> None:
    device, name, provider = context["device"], context["name"], context["provider"]
    data, _ = suite.seed_provider(name, provider, device)
    context["data"] = data
    online_name = f"{device}--c6-tag-online"
    online = suite.capture(online_name, "sources", device=device, demo=False, offline=False, data=data,
                           replay=source.Replay().intent("sources", "open", "komga", wait=1400),
                           expected_route="catalog", modal="")
    _mark(suite, online, "actual-online-bootstrap")
    suite.expect(online, "source_id", "komga")
    suite.expect(online, "catalog_count", 20)
    suite.expect(online, "catalog_remote_page", 1)
    suite.expect(online, "catalog_has_more", True)
    suite.check(online, "A real online series response bootstraps the cached catalog before offline full-page tag coverage",
                any(item.get("method") == "POST" and "/series/list" in item.get("path", "")
                    and item.get("authorized") is True for item in _request_records(provider)),
                _request_records(provider))
    suite.save_provider(online_name, provider, online)

    # This is the ordinary full collection, including every page's original
    # observation and selected-state replay. The main harness owns that logic.
    suite.catalog_tag_paging(name, device, data, qualifier=False)
    first = _case(suite, f"{name}--filter-tags-first")
    geometry = first.get("native_tag_geometry", {})
    tags = first["state"].get("catalog_tag_options", [])
    columns, pages = geometry.get("columns"), geometry.get("page_count")
    suite.check(first, "The normal stress provider supplies the complete actual 34-tag collection",
                len(tags) == NORMAL_TAG_COUNT and len({tag.get("id") for tag in tags}) == NORMAL_TAG_COUNT,
                {"tag_count": len(tags), "columns": columns, "pages": pages})
    if type(columns) is not int or columns < 1 or type(pages) is not int or pages < 1:
        raise ValueError("The main full-page capture did not publish actual native tag geometry")
    expected_pages = (len(tags) + columns * 2 - 1) // (columns * 2)
    suite.check(first, "Actual full-page capacity accounts for every provider tag", pages == expected_pages, geometry)
    suite.persist_case(first)
    controls = first.get("native_tag_observation", {}).get("controls", [])
    if not controls:
        raise ValueError("The main first-page native observation contains no real chip control")
    target = _target(suite, first, controls[0])
    _excluded(source, suite, context, first, target, f"{device}--c6-tag-excluded", online=False)
    expected = [f"{name}--fixture-seed", online_name, first["name"],
                *[f"{name}--filter-tag-page-{page}" for page in range(1, pages + 1)],
                *[f"{name}--filter-tag-page-{page}-observe" for page in range(2, pages + 1)],
                f"{device}--c6-tag-excluded"]
    for identifier in expected:
        case = _case(suite, identifier)
        if "c6_tag_role" not in case:
            _mark(suite, case, "normal-full-page-observation" if case.get("prerequisite") else "normal-full-page-selected")
    context["normal"] = {"tag_count": len(tags), "columns": columns, "page_count": pages,
                         "expected_capture_ids": expected, "expected_capture_count": 3 + 2 * pages,
                         "main_qualifier_argument": False, "full_collection_executed": True}


def _long_profile(source: Any, pixels: Any, suite: Any, context: dict[str, Any]) -> None:
    device, variant = context["device"], context["variant"]
    variant.enable()
    name = f"{device}--c6-tag-long-neutral"
    neutral = suite.capture(name, "sources", device=device, demo=False, offline=False, data=context["data"],
                            replay=source.Replay().intent("sources", "open", "komga", wait=1400)
                            .intent("catalog", "filter-menu").intent("catalog", "reset-filter"),
                            expected_route="catalog", modal="choices", context="catalog-filter")
    _mark(suite, neutral, "long-neutral-actual-http")
    tags = neutral["state"].get("catalog_tag_options", [])
    suite.check(neutral, "Real HTTP metadata replaces exactly one provider tag without changing the 34-tag collection",
                len(tags) == NORMAL_TAG_COUNT and sum(tag.get("title") == LONG_TAG for tag in tags) == 1
                and bool(tags) and tags[0].get("title") == LONG_TAG
                and all(tag.get("state") == 0 for tag in tags),
                {"tag_count": len(tags), "first_tag": tags[0] if tags else None})
    boxes = pixels.observe_neutral(suite, neutral, stress_paged=True)
    if not boxes:
        raise ValueError("The long neutral native observation contains no real chip outline")
    left, top, right, bottom = boxes[0]
    target = _target(suite, neutral, {"rectangle": [left, top, right - left, bottom - top],
                                    "hit": [(left + right) / 2, (top + bottom) / 2]})
    if target["title"] != LONG_TAG:
        raise ValueError("The actual first long chip does not match the served provider metadata")
    first_top = min(box[1] for box in boxes)
    columns = sum(abs(box[1] - first_top) <= 3 for box in boxes)
    observed_geometry = {"columns": columns, "visible_boxes": [list(box) for box in boxes],
                         "tag_count": len(tags), "page_count": (len(tags) + columns * 2 - 1) // (columns * 2)}
    neutral["c6_long_native_geometry"] = observed_geometry
    neutral["independent_original_review"] = True
    neutral["long_tail_visibility"] = {"status": "independent_original_review_required",
                                       "distinguishing_suffix": LONG_SUFFIX,
                                       "state_is_not_rendered_text_proof": True}
    neutral.setdefault("visible_ui_requirements", []).append(
        "Inspect the actual long chip's distinguishing suffix, bounded native truncation and bounded native frame in this original PNG; model text alone is not visibility proof.")
    _retain(source, suite, neutral, "long-http-responses", variant.evidence())
    suite.save_provider(name, context["provider"], neutral)
    suite.persist_case(neutral)
    excluded = _excluded(source, suite, context, neutral, target,
                         f"{device}--c6-tag-long-excluded", online=True, long=True)
    evidence = variant.evidence()
    suite.check(excluded, "Each long fixture metadata response retains its original and actual HTTP JSON with an unambiguous request index",
                len(evidence["responses"]) >= 2 and not evidence["errors"]
                and all(type(item.get("request_index_zero_based")) is int for item in evidence["responses"]),
                evidence)
    _retain(source, suite, excluded, "long-http-responses", evidence)
    suite.persist_case(excluded)
    context["long"] = {"expected_capture_ids": [neutral["name"], excluded["name"]],
                       "expected_capture_count": 2, "native_geometry": observed_geometry,
                       "provider_response_evidence": evidence,
                       "tail_visibility": "independent_original_review_required"}


def run_qualification(source: Any, pixels: Any, suite: Any) -> dict[str, Any]:
    entries = [(board, entry) for board in suite.scope["pageStateMatrix"]
               for entry in [board["default"], *board["states"]]]
    # Preserve the main complete-round identity and all missing full-suite gates.
    suite.reference_ids = [entry["id"] for _, entry in entries]
    result: dict[str, Any] = {"reference_ids": list(REFERENCE_IDS), "profiles": {}, "errors": []}
    for identifier in REFERENCE_IDS:
        try:
            matches = [(board, entry) for board, entry in entries if entry["id"] == identifier]
            if len(matches) != 1:
                raise ValueError(f"The original fixed reference ID is not unique: {identifier}")
            suite.reference_case(*matches[0])
            _mark(suite, _case(suite, identifier), "original-reference-neutral-chinese")
        except Exception as error:
            result["errors"].append(f"{identifier}: {type(error).__name__}: {error}")
            _failure(source, suite, f"c6-tags-reference-{identifier}", error)
    with ExitStack() as stack:
        contexts = []
        for device in PROFILES:
            provider = stack.enter_context(source.KomgaFixture(stress=True))
            variant = LongMetadataVariant(source, suite, provider, device)
            stack.callback(variant.restore)
            context = {"device": device, "name": f"{device}--content-stress",
                       "provider": provider, "variant": variant}
            contexts.append(context)
            try:
                _normal_profile(source, suite, context)
            except Exception as error:
                result["errors"].append(f"{device} normal: {type(error).__name__}: {error}")
                _failure(source, suite, f"{device}--c6-tags-normal-completion", error)
        normal_complete = all("normal" in context for context in contexts)
        result["long_variant_enabled_only_after_all_normal_profiles_completed"] = normal_complete
        if normal_complete:
            for context in contexts:
                try:
                    _long_profile(source, pixels, suite, context)
                except Exception as error:
                    result["errors"].append(f"{context['device']} long: {type(error).__name__}: {error}")
                    _failure(source, suite, f"{context['device']}--c6-tags-long-completion", error)
        else:
            result["errors"].append("Long HTTP variants were not enabled because ordinary full-page coverage did not finish for both profiles")
        for context in contexts:
            evidence_path = suite.output / "fixtures" / f"{context['device']}--c6-tag-complete-requests.json"
            records = _request_records(context["provider"])
            source.save_json(evidence_path, records)
            result["profiles"][context["device"]] = {
                "normal": context.get("normal"), "long": context.get("long"),
                "request_count": len(records),
                "provider_requests": {"path": str(evidence_path), "sha256": source.file_hash(evidence_path)},
                "long_variant_errors": context["variant"].evidence()["errors"],
            }
    return result


def _counts(suite: Any, result: dict[str, Any]) -> dict[str, Any]:
    expected = list(REFERENCE_IDS)
    formulas = {}
    for device in PROFILES:
        profile = result.get("profiles", {}).get(device, {})
        normal, long = profile.get("normal"), profile.get("long")
        if normal:
            expected.extend(normal["expected_capture_ids"])
            formulas[device] = {"observed_normal_columns": normal["columns"],
                               "observed_normal_pages": normal["page_count"],
                               "normal_formula": "seed + online + 2 * actual_pages + excluded",
                               "normal_expected": normal["expected_capture_count"], "long_expected": 2}
        if long:
            expected.extend(long["expected_capture_ids"])
    captures = [case for case in suite.cases if case.get("screenshot")]
    names = [case["name"] for case in captures]
    duplicates = sorted({name for name in names if names.count(name) != 1})
    return {"normal_page_formula": formulas,
            "complete_formula": "2 original references + sum(5 + 2 * actual_normal_pages) over both profiles",
            "nominal_capture_count": 34, "nominal_normal_pages": {"pw5": 5, "scribe": 6},
            "expected_capture_ids_from_completed_native_geometry": expected,
            "derived_expected_capture_count": len(expected), "capture_attempts": len(captures),
            "actual_original_png_count": sum(Path(case["screenshot"]).is_file() for case in captures),
            "prerequisite_captures": sum(bool(case.get("prerequisite")) for case in captures),
            "manual_review_captures": sum(bool(case.get("manual_review_required")) for case in captures),
            "missing_capture_ids": sorted(set(expected) - set(names)),
            "unexpected_capture_ids": sorted(set(names) - set(expected)), "duplicate_capture_ids": duplicates,
            "failed_case_ids": [case["name"] for case in suite.cases if not case.get("passed")]}


def main() -> int:
    source = None
    suite = None
    execution: dict[str, Any] = {}
    result: dict[str, Any] = {"profiles": {}, "errors": []}
    raw_exit = None
    raw_finish_error = None
    try:
        source = load_source()
        args = _arguments(source)
        _fresh_output(args.output)
        driver_path = Path(__file__).resolve()
        main_path = driver_path.with_name("verify-handoff-ui.py")
        pixels_path = driver_path.with_name("handoff_tag_pixels.py")
        fingerprint_paths = {
            "driver": driver_path, "main_harness": main_path, "pixel_helper": pixels_path,
            **{name: driver_path.with_name(name) for name in (
                "verify-ui.py", "verify-fullscreen.py", "handoff_c4_business.py",
                "handoff_c5_ui.py", "handoff_c5_reader.py")},
            "capture_plan": args.plan, "reference_scope": args.scope,
        }
        before = _fingerprints(source, fingerprint_paths)
        execution = {"driver": str(driver_path), "driver_sha256_before": before["driver"]["sha256"],
                     "main_harness": str(main_path), "main_harness_sha256_before": before["main_harness"]["sha256"],
                     "pixel_helper": str(pixels_path),
                     "pixel_helper_sha256_before": before["pixel_helper"]["sha256"],
                     "frozen_inputs_before": before, "frozen_input_count": len(fingerprint_paths),
                     "argv": [sys.executable, str(driver_path), *sys.argv[1:]],
                     "expected_binary_sha256": args.expected_sha256.lower(),
                     "expected_source_sha256": args.expected_source_sha256.lower(),
                     "partial_run": True, "clean_round_credit": 0}
        suite = source.Suite(args)
        try:
            source.save_json(suite.output / "tag-driver-execution.json", execution)
            import handoff_tag_pixels as pixels
            if Path(pixels.__file__).resolve() != pixels_path:
                raise ValueError("The pixel observer is not the helper beside this frozen driver")
            result = run_qualification(source, pixels, suite)
            # Keep completed profile counts even if the source-owned pixel
            # assessment raises. Its real assertions are never replaced with
            # a placeholder pass or a reduced collection.
            pixels.assess_tag_cases(suite)
            result["pixel_assessment_executed"] = True
        except Exception as error:
            result.setdefault("errors", []).append(f"{type(error).__name__}: {error}")
            _failure(source, suite, "c6-tags-driver-completion", error)
        finally:
            # Always retain the main raw manifest, including failures and every
            # unchanged missing full-suite requirement. Its partial exit is not
            # replaced with a fictional complete or clean result.
            try:
                raw_exit = suite.finish_manifest()
            except Exception as error:
                raw_finish_error = f"{type(error).__name__}: {error}"
        after = _fingerprints(source, fingerprint_paths)
        execution.update({"driver_sha256_after": after["driver"]["sha256"],
                          "main_harness_sha256_after": after["main_harness"]["sha256"],
                          "pixel_helper_sha256_after": after["pixel_helper"]["sha256"],
                          "frozen_inputs_after": after})
        unchanged = (len(before) == 10 and set(before) == set(after)
                     and all(before[name]["sha256"] is not None and before[name] == after[name]
                             for name in before))
        counts = _counts(suite, result)
        raw_path = suite.output / "manifest.json"
        raw = source.read_json(raw_path) if raw_path.is_file() else {}
        retained_partial = (raw.get("full_capture_suite_completed") is False
                            and raw.get("reference_coverage_complete") is False
                            and raw.get("review", {}).get("clean_round") is False)
        complete_profiles = all(result.get("profiles", {}).get(device, {}).get("normal")
                                and result.get("profiles", {}).get(device, {}).get("long") for device in PROFILES)
        passed = (not result.get("errors") and result.get("pixel_assessment_executed") is True
                  and complete_profiles and unchanged and retained_partial and raw_finish_error is None
                  and not counts["failed_case_ids"] and not counts["missing_capture_ids"]
                  and not counts["unexpected_capture_ids"] and not counts["duplicate_capture_ids"]
                  and counts["capture_attempts"] == counts["derived_expected_capture_count"]
                  and counts["actual_original_png_count"] == counts["capture_attempts"])
        report = {"schema_version": 1, "kind": "c6-affected-tag-native-qualification",
                  "run_id": suite.run_id, "round": args.round, "partial_run": True,
                  "qualification_success": bool(passed), "full_capture_suite_completed": False,
                  "clean_round": False, "clean_round_streak": 0, "accepted": False,
                  "clean_round_credit": 0, "execution": execution, "scripts_unchanged": unchanged,
                  "execution_inputs_unchanged": unchanged,
                  "binary_sha256": suite.binary_hash, "source_sha256": suite.source["sha256"],
                  "reference_sha256": suite.design["sha256"], "capture_plan_sha256": suite.design["plan_sha256"],
                  "actual_counts": counts, "qualification": result,
                  "raw_main_manifest": {"path": str(raw_path), "exit_code": raw_exit,
                                        "sha256": source.file_hash(raw_path) if raw_path.is_file() else None,
                                        "finish_error": raw_finish_error,
                                        "full_suite_gate_retained": retained_partial},
                  "long_suffix_review": "Inspect every long-neutral and long-excluded original PNG independently; provider state and JSON do not prove the suffix was rendered.",
                  "exit_code_scope": "Zero means only the affected-tag assertions passed. The retained main full-suite manifest remains partial and supplies no clean-round credit."}
        source.save_json(suite.output / "tag-qualification.json", report)
        print(f"C6 affected tags: {counts['capture_attempts']} native capture attempts; focused success={bool(passed)}; partial=true; clean_round=false; raw full-suite exit={raw_exit}", flush=True)
        return 0 if passed else 1
    except Exception as error:
        if suite is not None and source is not None:
            source.save_json(suite.output / "tag-driver-error.json", {
                "partial_run": True, "clean_round": False, "execution": execution,
                "error": f"{type(error).__name__}: {error}", "raw_manifest_finish_error": raw_finish_error})
        print(f"Affected-tag qualification failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
