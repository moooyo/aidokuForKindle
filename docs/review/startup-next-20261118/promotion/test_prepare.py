"""Verify frozen promotion decisions using synthetic samples and packages."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("promotion_prepare", Path(__file__).with_name("prepare.py"))
prepare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare
SPEC.loader.exec_module(prepare)

APP = "aidoku/app/aidoku"
LAUNCH = "aidoku/launch.sh"
HELPER = "aidoku/app/aidoku-startup-portrait"
DOCUMENT = "documents/Aidoku.sh"
DISPATCH = "aidoku/native-catalog/aidoku-library-dispatch"
MECHANISMS = {"supervisor": "startup_portrait_supervisor", "cover": "bounded_cached_covers",
              "cold": "cold_local_cover_decode_validation"}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def artifacts(candidate):
    control = {
        "variant_sha256": digest(b"control-switch"),
        "application_sha256": digest(b"application"),
        "launcher_sha256": digest(b"launcher"),
        "native_query_sha256": digest(b"native-query"),
        "extra_sha256": {},
    }
    if candidate == "cold":
        control.update(application_sha256=prepare.DEPLOYED[APP], launcher_sha256=prepare.DEPLOYED[LAUNCH],
                       native_query_sha256=prepare.DEPLOYED[prepare.QUERY])
    treatment = copy.deepcopy(control)
    treatment["variant_sha256"] = digest(b"treatment-switch")
    if candidate == "supervisor":
        treatment["launcher_sha256"] = digest(b"trial-launcher")
        treatment["extra_sha256"] = {"aidoku-startup-portrait": digest(b"candidate-helper")}
        expected = {LAUNCH: treatment["launcher_sha256"], HELPER: digest(b"candidate-helper")}
    else:
        treatment["application_sha256"] = digest(b"trial-application")
        expected = {APP: treatment["application_sha256"]}
    return {"baseline": control, "candidate": treatment}, expected


def sample(first, input_ready, covers):
    return {"ready": 1400, "first": first, "input_ready": input_ready,
            "usable": max(first, input_ready), "covers": covers, "exit": 1500}


def trial(phase, candidate="supervisor", *, primary=None, secondary=None):
    count = 6 if phase == "screen" else 10
    if primary is None:
        primary = [125] * count
    if secondary is None:
        secondary = [5] * count
    variants, expected = artifacts(candidate)
    key = "first" if candidate == "supervisor" else "covers"
    batch = phase + "-fixture"
    pairs = []
    for index, (main_gain, other_gain) in enumerate(zip(primary, secondary), 1):
        first_gain, covers_gain = ((main_gain, other_gain) if candidate == "supervisor"
                                  else (other_gain, main_gain))
        first = 2200 + index * 3
        covers = first + 700
        before = sample(first, first + 1, covers)
        after = sample(first - first_gain, first + 1 - first_gain, covers - covers_gain)
        pairs.append({"batch": batch, "round": index, "order": "AB" if index % 2 else "BA",
                      "complete": True, "baseline_ms": before, "candidate_ms": after,
                      "savings_ms": {k: before[k] - after[k] for k in before}})
    report = {"phase": phase, "primary_endpoint": key, "valid": True, "latency_gate_passed": True,
              "mechanism": {"name": MECHANISMS[candidate], "verified": True},
              "artifacts": variants, "pairs": pairs, "batches": [
                  {"run_id": batch, "artifacts": copy.deepcopy(variants), "mode": phase,
                   "issues": [], "pairs": copy.deepcopy(pairs)}]}
    if candidate == "cold":
        workload = {"cache_regime": "empty-cache-v1", "fixture_policy": "per-case-empty-cache-v1"}
        report.update(workload, cold_fixture_verified=True, cold_fixture_match=True)
        report["batches"][0].update(workload)
    return report, expected


def catalog_qualification(candidate):
    variants, expected = artifacts(candidate)
    run = "qualify-fixture"
    measured = {"valid": True, "qualification_passed": True, "artifacts": variants,
                "batches": [{"run_id": run, "mode": "qualify", "artifacts": copy.deepcopy(variants),
                             "issues": [], "pairs": []}],
                "pixels": [{"batch": run, "identical_below_status_bar": True, "width": 1860,
                            "height": 2480, "status_bar_excluded_pixels": 72}]}
    lifecycle = {"artifact_sha256": expected.copy(), "checks": {name: True for name in
                 ("pixels", "input", "exit", "sleep_wake", "notification", "catalog_identity",
                  "restoration", "production_defaults")}}
    return measured, lifecycle, expected


def kpm_members(payload):
    rows = "".join(
        f"{digest(content)}|{len(content)}|{mode:o}|{name}\n"
        for name, (content, mode) in sorted(payload.items())
    )
    return {**{f"payload/{name}": data for name, data in payload.items()},
            "payload.tsv": (rows.encode("ascii"), 0o644),
            "manifest.json": (b'{"manifest_version":2,"id":"test"}\n', 0o644),
            **{name: (b"#!/bin/sh\nexit 0\n", 0o755) for name in ("install.sh", "launch.sh", "uninstall.sh")}}


@unittest.skipUnless(sys.platform == "linux", "Run promotion checks on test-env")
class PerformanceSelectionTests(unittest.TestCase):
    def test_each_candidate_has_a_distinct_primary_and_an_independent_confirmation(self):
        for candidate in MECHANISMS:
            screens = []
            for phase, count in (("screen", 6), ("confirm", 10)):
                with self.subTest(candidate=candidate, phase=phase):
                    report, expected = trial(phase, candidate, primary=[100] * (count - 1) + [-1],
                                             secondary=[-50] * count)
                    screens.append(prepare.freeze_gate(report, phase, candidate, expected))
            self.assertTrue(screens[0].isdisjoint(screens[1]))

    def test_win_count_is_separate_from_effect_size(self):
        for phase, count in (("screen", 6), ("confirm", 10)):
            for candidate in MECHANISMS:
                for gains in ([400] * (count - 2) + [0, -1], [99] * count):
                    with self.subTest(phase=phase, candidate=candidate, gains=gains):
                        report, expected = trial(phase, candidate, primary=gains)
                        with self.assertRaises(ValueError):
                            prepare.freeze_gate(report, phase, candidate, expected)

    def test_secondary_regression_is_not_covered_by_large_primary_gain(self):
        for candidate in MECHANISMS:
            report, expected = trial("confirm", candidate, primary=[400] * 10, secondary=[-51] * 10)
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                prepare.freeze_gate(report, "confirm", candidate, expected)

    def test_fast_paint_is_not_a_usable_first_frame(self):
        report, expected = trial("screen", primary=[200] * 6)
        for pair in report["pairs"]:
            # Keep the independently recorded input boundary slow. The precomputed
            # saving is deliberately left optimistic: it cannot authorize release.
            pair["candidate_ms"]["input_ready"] = pair["baseline_ms"]["input_ready"] + 80
            pair["candidate_ms"]["usable"] = pair["candidate_ms"]["input_ready"]
        report["batches"][0]["pairs"] = copy.deepcopy(report["pairs"])
        with self.assertRaises(ValueError):
            prepare.freeze_gate(report, "screen", "supervisor", expected)

    def test_incomplete_duplicate_unbalanced_or_changed_observation_stops_selection(self):
        for case in ("duplicate", "unequal-orders", "failed", "invalid", "flag", "mechanism", "truncated",
                     "wrong-phase", "wrong-endpoint"):
            with self.subTest(case=case):
                report, expected = trial("confirm", "cover")
                pairs = report["pairs"]
                if case == "duplicate":
                    pairs[-1] = copy.deepcopy(pairs[-3])
                elif case == "unequal-orders":
                    pairs[-1]["order"] = "AB"
                elif case == "failed":
                    pairs[-1]["complete"] = False
                elif case == "invalid":
                    report["valid"] = False
                elif case == "flag":
                    report["latency_gate_passed"] = False
                elif case == "mechanism":
                    report["mechanism"]["verified"] = False
                elif case == "truncated":
                    del pairs[-2:]
                elif case == "wrong-phase":
                    report["phase"] = "screen"
                else:
                    report["primary_endpoint"] = "first"
                report["batches"][0]["pairs"] = copy.deepcopy(pairs)
                with self.assertRaises(ValueError):
                    prepare.freeze_gate(report, "confirm", "cover", expected)

    def test_batch_must_bind_the_measured_files_and_unchanged_native_query(self):
        for case in ("app", "helper", "native-query", "batch", "extra-pair"):
            candidate = "cover" if case == "app" else "supervisor"
            report, expected = trial("screen", candidate)
            if case == "app":
                expected[APP] = digest(b"another app")
            elif case == "helper":
                expected[HELPER] = digest(b"another helper")
            elif case == "native-query":
                report["artifacts"]["candidate"]["native_query_sha256"] = digest(b"different native query")
                report["batches"][0]["artifacts"] = copy.deepcopy(report["artifacts"])
            elif case == "batch":
                report["batches"][0]["artifacts"]["candidate"]["launcher_sha256"] = digest(b"different launcher")
            else:
                report["batches"][0]["pairs"].append(copy.deepcopy(report["pairs"][0]))
            with self.subTest(case=case), self.assertRaises(ValueError):
                prepare.freeze_gate(report, "screen", candidate, expected)

    def test_qualification_must_cover_the_same_artifact_and_observed_pixels(self):
        measured, lifecycle, expected = catalog_qualification("cover")
        prepare.qualify_measured(measured, lifecycle, expected)
        for case in ("input", "notification", "restoration", "production_defaults", "identity", "pixels", "foreign-batch"):
            trial_report, trial_lifecycle = copy.deepcopy(measured), copy.deepcopy(lifecycle)
            if case == "identity":
                trial_lifecycle["artifact_sha256"][APP] = digest(b"stale app")
            elif case == "pixels":
                trial_report["pixels"][0]["identical_below_status_bar"] = False
            elif case == "foreign-batch":
                trial_report["pixels"][0]["batch"] = "another-fixture"
            else:
                trial_lifecycle["checks"][case] = False
            with self.subTest(case=case), self.assertRaises(ValueError):
                prepare.qualify_measured(trial_report, trial_lifecycle, expected)

    def test_file_uri_binds_bytes_and_rejects_changes_after_selection(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "selected report.json"
            payload = json.dumps({"phase": "screen"}).encode("ascii")
            path.write_bytes(payload)
            reference = {"uri": path.as_uri(), "sha256": digest(payload)}
            self.assertEqual(prepare.load_bound(reference), payload)
            path.write_bytes(payload + b"\n")
            with self.assertRaises(ValueError):
                prepare.load_bound(reference)

    def test_trial_cannot_promote_an_effect_measured_with_unrelated_program_bytes(self):
        result, selected = trial("screen")
        sides = result["artifacts"]
        for side in sides.values():
            side["application_sha256"] = prepare.DEPLOYED[APP]
            side["native_query_sha256"] = prepare.DEPLOYED[prepare.QUERY]
        sides["baseline"]["launcher_sha256"] = sides["candidate"]["launcher_sha256"]
        sides["baseline"]["extra_sha256"] = sides["candidate"]["extra_sha256"].copy()
        prepare.release_trial_context(result, selected)
        sides["candidate"]["application_sha256"] = digest(b"unselected application")
        with self.assertRaises(ValueError):
            prepare.release_trial_context(result, selected)
        sides["candidate"]["application_sha256"] = prepare.DEPLOYED[APP]
        sides["baseline"]["launcher_sha256"] = digest(b"unrelated launcher")
        with self.assertRaises(ValueError):
            prepare.release_trial_context(result, selected)

    def test_combined_candidates_need_the_deployed_control(self):
        result, selected = trial("confirm")
        selected[APP] = digest(b"selected application")
        for key, identity in (("application_sha256", APP), ("launcher_sha256", LAUNCH),
                              ("native_query_sha256", prepare.QUERY)):
            result["artifacts"]["baseline"][key] = prepare.DEPLOYED[identity]
        result["artifacts"]["candidate"]["application_sha256"] = selected[APP]
        result["artifacts"]["candidate"]["native_query_sha256"] = prepare.DEPLOYED[prepare.QUERY]
        prepare.release_trial_context(result, selected, combined=True)
        result["artifacts"]["baseline"]["application_sha256"] = selected[APP]
        with self.assertRaises(ValueError):
            prepare.release_trial_context(result, selected, combined=True)

    def test_candidate_cannot_claim_a_different_successful_mechanism(self):
        for candidate, required in MECHANISMS.items():
            for other in (None, *[name for name in MECHANISMS.values() if name != required]):
                result, selected = trial("screen", candidate)
                if other is None:
                    del result["mechanism"]["name"]
                else:
                    result["mechanism"]["name"] = other
                with self.subTest(candidate=candidate, other=other), self.assertRaises(ValueError):
                    prepare.freeze_gate(result, "screen", candidate, selected)

    def test_empty_cache_proof_is_explicit_in_every_batch_and_the_phase(self):
        result, _ = trial("screen", "cold")
        prepare.bind_workload(result, "cold")
        for place, key, wrong in (
            ("top", "cache_regime", "warm"), ("top", "cache_regime", None),
            ("batch", "cache_regime", "warm"), ("batch", "cache_regime", None),
            ("top", "fixture_policy", "reuse-one-cache"), ("batch", "fixture_policy", None),
            ("top", "cold_fixture_match", False), ("top", "cold_fixture_verified", None),
        ):
            bad = copy.deepcopy(result)
            target = bad if place == "top" else bad["batches"][0]
            if wrong is None:
                del target[key]
            else:
                target[key] = wrong
            with self.subTest(place=place, key=key, wrong=wrong), self.assertRaises(ValueError):
                prepare.bind_workload(bad, "cold")
        for warm in ("cover", "supervisor"):
            prepare.bind_workload(trial("screen", warm)[0], warm)
            with self.subTest(warm=warm), self.assertRaises(ValueError):
                prepare.bind_workload(result, warm)

    def test_cold_comparison_requires_the_installed_control_and_a_different_application(self):
        for case in ("same-app", "wrong-control-app", "wrong-control-launcher", "extra-control-helper"):
            result, selected = trial("screen", "cold")
            sides = result["artifacts"]
            if case == "same-app":
                sides["candidate"]["application_sha256"] = sides["baseline"]["application_sha256"]
                selected[APP] = sides["candidate"]["application_sha256"]
            elif case == "wrong-control-app":
                sides["baseline"]["application_sha256"] = digest(b"unrelated old app")
            elif case == "wrong-control-launcher":
                sides["baseline"]["launcher_sha256"] = digest(b"another old launcher")
            else:
                sides["baseline"]["extra_sha256"] = {"aidoku-startup-portrait": digest(b"extra control helper")}
            result["batches"][0]["artifacts"] = copy.deepcopy(sides)
            with self.subTest(case=case), self.assertRaises(ValueError):
                prepare.freeze_gate(result, "screen", "cold", selected)

    def test_cold_seed_must_match_every_independent_timing_and_pixel_case(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            run_cases = {"stage-a": {"baseline-01", "candidate-01"}, "stage-b": {"baseline-qualify", "candidate-qualify"}}
            seed_record = (f"{digest(b'fixture database')}  library.sqlite3\n"
                           f"{digest(b'fixture profile')}  device-profile\n").encode("ascii")
            seed = digest(seed_record)
            for run, cases in run_cases.items():
                for case in cases:
                    directory = root / run / "outputs" / "out" / case
                    directory.mkdir(parents=True)
                    (directory / "seed.sha256").write_bytes(seed_record)
                    (directory / "cold-fixture").write_bytes(b"empty-cache-v1\n")
                    (directory / "cold-fixture.sha256").write_text(seed + "\n", encoding="ascii")
            public = prepare.assert_same_seed(root, run_cases)
            self.assertNotIn(seed, repr(public))
            altered = root / "stage-b" / "outputs" / "out" / "candidate-qualify" / "cold-fixture.sha256"
            altered.parent.joinpath("cold-fixture").write_bytes(b"warm\n")
            with self.assertRaises(ValueError):
                prepare.assert_same_seed(root, run_cases)
            altered.parent.joinpath("cold-fixture").write_bytes(b"empty-cache-v1\n")
            altered.parent.joinpath("seed.sha256").write_bytes(seed_record + b"\n")
            with self.assertRaises(ValueError):
                prepare.assert_same_seed(root, run_cases)
            altered.write_text(digest(seed_record + b"\n") + "\n", encoding="ascii")
            with self.assertRaises(ValueError):
                prepare.assert_same_seed(root, run_cases)
            altered.parent.joinpath("seed.sha256").write_bytes(seed_record)
            for different in (digest(b"other fixture") + "\n", "not a digest\n", seed + "\n" + seed + "\n"):
                altered.write_text(different, encoding="ascii")
                with self.subTest(invalid=different != seed + "\n"), self.assertRaises(ValueError):
                    prepare.assert_same_seed(root, run_cases)
            altered.unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                prepare.assert_same_seed(root, run_cases)


@unittest.skipUnless(sys.platform == "linux", "Run promotion checks on test-env")
class PackageIsolationTests(unittest.TestCase):
    def setUp(self):
        self.initial = {APP: (b"old app", 0o755), LAUNCH: (b"original startup", 0o755),
                        DOCUMENT: (b"installed Library document", 0o755),
                        DISPATCH: (b"fixed shared dispatcher", 0o755)}
        self.selected = {LAUNCH: (digest(self.initial[LAUNCH][0]), digest(b"new startup")),
                         HELPER: ("absent", digest(b"native helper"))}
        self.updated = {**self.initial, LAUNCH: (b"new startup", 0o755), HELPER: (b"native helper", 0o755)}

    def test_selection_rejects_unapproved_files_and_half_a_supervisor(self):
        accepted = {LAUNCH: {}, HELPER: {}}
        prepare.allowed_changes(["supervisor"], accepted)
        prepare.allowed_changes(["cover"], {APP: {}})
        prepare.allowed_changes(["cold"], {APP: {}})
        for candidates, artifacts in ((["cold", "cover"], {APP: {}}),
                                      (["cold", "supervisor"], {APP: {}, **accepted}),
                                      (["cold"], {APP: {}, HELPER: {}})):
            with self.subTest(candidates=candidates), self.assertRaises(ValueError):
                prepare.allowed_changes(candidates, artifacts)
        for members in ({**accepted, APP: {}}, {**accepted, DOCUMENT: {}}, {**accepted, DISPATCH: {}}, {LAUNCH: {}}):
            with self.subTest(members=tuple(members)), self.assertRaises(ValueError):
                prepare.allowed_changes(["supervisor"], members)

    def test_zip_and_kpm_allow_only_selected_bytes_in_their_original_layout(self):
        prepare.check_archive_delta(self.initial, self.updated, self.selected)
        prepare.check_archive_delta(kpm_members(self.initial), kpm_members(self.updated), self.selected, kpm=True)
        for kpm in (False, True):
            for case in ("document", "shared", "missing", "foreign", "selected-hash", "helper-mode"):
                shifted = copy.deepcopy(self.updated)
                if case == "missing":
                    del shifted[DOCUMENT]
                elif case == "foreign":
                    shifted["aidoku/data/foreign"] = (b"unexpected state", 0o644)
                elif case == "selected-hash":
                    shifted[LAUNCH] = (b"untested startup", 0o755)
                elif case == "helper-mode":
                    shifted[HELPER] = (b"native helper", 0o644)
                else:
                    shifted[DOCUMENT if case == "document" else DISPATCH] = (b"changed fixture", 0o755)
                before, after = (kpm_members(self.initial), kpm_members(shifted)) if kpm else (self.initial, shifted)
                with self.subTest(kpm=kpm, case=case), self.assertRaises(ValueError):
                    prepare.check_archive_delta(before, after, self.selected, kpm=kpm)

    def test_kpm_hook_and_allowlist_bytes_are_part_of_release_contract(self):
        before = kpm_members(self.initial)
        for case in ("manifest.json", "install.sh", "uninstall.sh", "launch.sh", "payload.tsv", "new_hook"):
            after = kpm_members(self.updated)
            if case == "payload.tsv":
                after[case] = before[case]
            else:
                after[case] = (b"unexpected control file", 0o755)
            with self.subTest(case=case), self.assertRaises(ValueError):
                prepare.check_archive_delta(before, after, self.selected, kpm=True)


if __name__ == "__main__":
    unittest.main()
