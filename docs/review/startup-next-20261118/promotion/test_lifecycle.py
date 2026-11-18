"""Check release lifecycle analysis against synthetic private records on test-env."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


def load_local(name):
    location = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("fixture_" + name, location)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value) + "\n", encoding="ascii")


def write_seed(directory, tag=b"library"):
    raw = (digest(tag) + "  library.sqlite3\n" + digest(b"profile") + "  device-profile\n").encode("ascii")
    (directory / "seed.sha256").write_bytes(raw)
    (directory / "cold-fixture").write_bytes(b"empty-cache-v1\n")
    (directory / "cold-fixture.sha256").write_text(digest(raw) + "\n", encoding="ascii")


def nul_words(values):
    return b"".join(v.encode("ascii") + b"\x00" for v in values)


def put(directory, name, data):
    data = data.encode("ascii") if isinstance(data, str) else data
    (directory / name).write_bytes(data)


def arguments(directory, run, side):
    stage = "/mnt/us/." + run + "/" + side
    app = [stage + "/app/aidoku", "--kindle", "--device", "scribe", "--data-dir", stage + "/data"]
    worker = ["sh", stage + "/launch.sh", "--foreground"]
    for who, words in (("app", app), ("worker", worker)):
        put(directory, who + ".argv.nul", nul_words(words))
        put(directory, who + ".env-relevant", "AIDOKU_HOME=" + stage + "\n")
    return app, worker


APP = "aidoku/app/aidoku"
LAUNCH = "aidoku/launch.sh"
QUERY = "aidoku/app/aidoku-native-query"
JPEG = "aidoku/app/kindle-jpeg-worker"
EXTRA = "aidoku/app/aidoku-startup-portrait"
PROGRAM = {name: digest(("synthetic " + name).encode("ascii")) for name in (APP, LAUNCH, QUERY, JPEG)}


@unittest.skipUnless(sys.platform == "linux", "Run release lifecycle checks on test-env")
class CaseEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.analyzer = load_local("analyze_lifecycle")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="source-lifecycle-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.run = "aidoku-next-life-f1a2c3d4"
        self.side = "candidate"
        self.directory = self.base / self.run / "outputs" / "out" / "candidate-default"
        self.directory.mkdir(parents=True)
        self.expected = PROGRAM.copy()
        self.regime = "empty-cache-v1"
        self.make_case()

    def make_case(self):
        d = self.directory
        arguments(d, self.run, self.side)
        write_seed(d)
        put(d, "expected-seed.sha256", (d / "seed.sha256").read_bytes())
        put(d, "fixture-regime.txt", "empty-cache-v1\n")
        put(d, "fixture-state.sha256", digest(b"complete fixture-local data") + "\n")
        put(d, "installed-hashes.tsv", "".join(
            f"{name.removeprefix('aidoku/')}\t{value}\n" for name, value in self.expected.items()))
        put(d, "app-exe.sha", self.expected[APP] + "\n")
        for phase in ("active", "parked", "wake"):
            put(d, "proc-" + phase + ".txt", "900 107 800 106 700 105\n")
            # A parked application can release evdev ownership without closing descriptors.
            put(d, "input-" + phase + ".txt", "event3\nevent4\n")
        for phase in ("parked", "wake"):
            parked = phase == "parked"
            state = {"pid": 900, "epoch": 1, "phase": "suspended" if parked else "active",
                     "same_process_resumes": int(not parked), "input_released": str(parked).lower(),
                     "render_paused": str(parked).lower(), "parked": str(parked).lower(),
                     "pause_count": 1, "resume_count": int(not parked), "frame_send_count": 5 + int(not parked),
                     "resume_full_frame_count": int(not parked), "input_generation": 1 + int(not parked)}
            put(d, "lifecycle-" + phase + ".tsv", "".join(f"{key}\t{value}\n" for key,value in state.items()))
        for name in ("sleep-ready", "resume-ready", "ordinary-exit"):
            put(d, name + ".tsv", "900\t1\n")
        put(d, "library.sha", digest(b"synthetic settled Scribe image") + "\n")
        put(d, "wake.sha", (d / "library.sha").read_bytes())
        put(d, "frame-format.tsv", "width=1860 height=2480 bpp=8 p6_header_bytes=36 p6_bytes=13838436 crop_top_px=72\n")
        for phase in ("before", "after"):
            put(d, "catalog-" + phase + ".identity", "00000000-0000-4000-8000-000000000001|text/x-shellscript|private-fixture-thumb|4100|430\n")
            put(d, "catalog-" + phase + ".access", "4090\n" if phase == "before" else "4092\n")
        marks = ("request", "ready", "library", "request_sleep", "parked", "request_wake", "wake",
                 "restored_frame", "request_term", "ordinary_exit", "native_restored")
        put(d, "evidence.tsv", "".join(f"{40 + i}.00\t{name}\n" for i, name in enumerate(marks)))
        put(d, "launcher.log", "aidoku: Launcher worker started (pid=700).\n"
            "aidoku: native window ready; profile=scribe, logical=930x1240, scale=2\n"
            "aidoku: Catalog notification completed.\naidoku: Launcher worker exited with status 0.\n")
        put(d, "case-ok", "ok\n")

    def review(self, expected=None):
        return self.analyzer.case_evidence(self.directory, self.run, self.side,
                                           self.expected if expected is None else expected, self.regime)

    def reject_modified(self, name, change):
        path = self.directory / name
        original = path.read_bytes()
        changed = change(original)
        self.assertNotEqual(original, changed)
        try:
            path.write_bytes(changed)
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.review()
        finally:
            path.write_bytes(original)

    def test_selected_bytes_are_bound_by_role(self):
        self.review()
        for component in (APP, LAUNCH, QUERY, JPEG):
            with self.subTest(component=component), self.assertRaises(ValueError):
                self.review({**self.expected, component: digest(b"unmeasured program")})
        self.reject_modified("app-exe.sha", lambda _b: (digest(b"unstaged executable") + "\n").encode("ascii"))

    def test_same_pid_after_reexec_or_parent_reuse_is_not_same_process(self):
        self.review()
        for index in range(6):
            for name in ("proc-parked.txt", "proc-wake.txt"):
                with self.subTest(index=index, observation=name):
                    def replace_identity(row):
                        fields = row.split()
                        fields[index] = str(int(fields[index]) + 4000).encode("ascii")
                        return b" ".join(fields) + b"\n"
                    self.reject_modified(name, replace_identity)

    def test_default_arguments_and_environment_are_observed(self):
        self.review()
        for who in ("app", "worker"):
            with self.subTest(process=who, change="forced argument"):
                self.reject_modified(who + ".argv.nul", lambda current: current + b"--startup-cached-covers\x002\x00")
            with self.subTest(process=who, change="forced environment"):
                self.reject_modified(who + ".env-relevant", lambda current: current + b"AIDOKU_STARTUP_PORTRAIT=native\n")
        with self.subTest(change="noncanonical executable"):
            self.reject_modified("app.argv.nul", lambda b: b.replace(b"/candidate/app/aidoku", b"/baseline/app/aidoku"))

    def test_catalog_identity_access_and_notification_are_independent_requirements(self):
        self.review()
        self.reject_modified("catalog-after.access", lambda _: (self.directory / "catalog-before.access").read_bytes())
        self.reject_modified("catalog-after.identity", lambda current: current.replace(b"text/x-shellscript", b"text/plain"))
        for extra in (b"aidoku: Catalog notification completed.\n",
                      b"aidoku: Catalog notification unavailable; using standard reindex recovery.\n"):
            with self.subTest(event=extra):
                self.reject_modified("launcher.log", lambda current: current + extra)
        self.reject_modified("launcher.log", lambda current: current.replace(b"aidoku: Catalog notification completed.\n", b""))
        self.reject_modified("launcher.log", lambda current: b"aidoku: Catalog notification completed.\n" +
                             current.replace(b"aidoku: Catalog notification completed.\n", b""))

    def test_exercised_input_and_ordinary_exit_leave_physical_evidence(self):
        self.review()
        for name in ("input-active.txt", "input-wake.txt", "ordinary-exit.tsv", "lifecycle-parked.tsv", "lifecycle-wake.tsv"):
            with self.subTest(missing=name):
                self.reject_modified(name, lambda _: b"")

    def test_restored_application_pixels_must_match_the_initial_library(self):
        self.review()
        self.reject_modified("wake.sha", lambda _: (digest(b"different cropped pixels") + "\n").encode("ascii"))
        self.reject_modified("frame-format.tsv", lambda b: b.replace(b"crop_top_px=72", b"crop_top_px=73"))

    def test_native_restoration_must_follow_observed_exit(self):
        self.review()
        self.reject_modified("evidence.tsv", lambda b: b.replace(b"\tnative_restored\n", b"\tordinary_exit\n"))
        self.reject_modified("evidence.tsv", lambda b: b.replace(b"50.00\tnative_restored\n", b"47.10\tnative_restored\n"))
        self.reject_modified("launcher.log", lambda b: b.replace(b"exited with status 0", b"exited with status 1"))

    def test_input_reacquisition_requires_a_common_descriptor_and_a_real_resume(self):
        self.review()
        self.reject_modified("input-wake.txt", lambda _: b"event9\n")
        self.reject_modified("lifecycle-wake.tsv", lambda b: b.replace(b"resume_full_frame_count\t1", b"resume_full_frame_count\t0"))
        self.reject_modified("lifecycle-wake.tsv", lambda b: b.replace(b"same_process_resumes\t1", b"same_process_resumes\t0"))
        self.reject_modified("lifecycle-parked.tsv", lambda b: b.replace(b"input_released\ttrue", b"input_released\tfalse"))
        self.reject_modified("sleep-ready.tsv", lambda b: b.replace(b"\t1\n", b"\t2\n"))
        self.reject_modified("ordinary-exit.tsv", lambda b: b.replace(b"900\t", b"901\t"))

    def test_pair_requires_same_seed_catalog_and_independently_qualified_picture(self):
        candidate = self.review()
        baseline = copy.deepcopy(candidate)
        baseline["access_before"] = candidate["access_before"] - 3
        baseline["access_after"] = candidate["access_before"] - 1
        frames = {candidate["frame"]}
        self.analyzer.pair_evidence(baseline, candidate, frames)
        for field in ("seed", "fixture", "frame", "wake", "catalog", "access_after"):
            broken = copy.deepcopy(candidate)
            if field == "access_after":
                broken[field] = broken["access_before"]
            else:
                value = broken[field]
                broken[field] = (b"different-fixture" if isinstance(value, bytes) else "different-fixture")
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.analyzer.pair_evidence(baseline, broken, frames)
        with self.assertRaises(ValueError):
            self.analyzer.pair_evidence(baseline, candidate, {digest(b"independent different picture").encode("ascii")})
        with self.assertRaises(ValueError):
            self.analyzer.pair_evidence(baseline, candidate, set())

    def test_lifecycle_can_never_reuse_screen_confirm_or_pixel_batch(self):
        run = self.run
        self.analyzer.independent(run, {"screen"}, {"confirm-a", "confirm-b"}, {"pixels"})
        for groups in (({run}, {"confirm"}, {"pixels"}), ({"screen"}, {run}, {"pixels"}),
                       ({"screen"}, {"confirm"}, {run}), ({"scope"}, {"scope"}, {"pixels"}),
                       ({"screen"}, {"confirm"}, {"confirm"}), ({"screen"}, {"confirm"}, {"screen"}),
                       (set(), {"confirm"}, {"pixels"}), ({"screen"}, set(), {"pixels"}),
                       ({"screen"}, {"confirm"}, set())):
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                self.analyzer.independent(run, *groups)


@unittest.skipUnless(sys.platform == "linux", "Run release lifecycle checks on test-env")
class SourceEvidenceTests(unittest.TestCase):
    ALGORITHM = "sha256-of-canonical-sorted-relative-file-records-v1"

    @classmethod
    def setUpClass(cls):
        cls.analyzer = load_local("analyze_lifecycle")
        repo = next((p for p in Path(__file__).resolve().parents if (p / "scripts/verify-kindle-package.py").is_file()),
                    Path("/root/aidoku-startup-delivery-20261118/source"))
        cls.verifier_source = repo / "scripts/verify-kindle-package.py"

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="lifecycle-source-proof-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source"
        self.batch = self.root / "batch"
        self.batch.mkdir()
        for directory in ("src", "ui", "assets", "vendor", "examples", "scripts"):
            (self.source / directory).mkdir(parents=True)
        self.verifier_path = self.source / "scripts/verify-kindle-package.py"
        shutil.copyfile(self.verifier_source, self.verifier_path)
        self.native = self.source / "src/supervisor.c"
        self.native.write_bytes(b"int fixture_count(void) { return 2; }\n")
        (self.source / "src/fixture.rs").write_bytes(b"pub const COUNT: usize = 2;\n")
        verifier = self.analyzer.load("source_fixture_verifier", self.verifier_path)
        self.assertEqual(verifier.SOURCE_ALGORITHM, self.ALGORITHM)
        self.manifest = dict(verifier.source_manifest(self.source), algorithm=self.ALGORITHM)
        self.manifest_path = self.batch / "qualified-source-manifest.json"
        write_json(self.manifest_path, self.manifest)
        self.selected = {APP: digest(b"synthetic ARM application")}
        self.producer = self.root / "retained-producer.json"
        self.build = {"input": {"path": "src/supervisor.c", "sha256": digest(self.native.read_bytes())},
                      "executable_sha256": self.selected[APP]}
        write_json(self.producer, self.build)
        self.attestation = {"artifact_sha256": self.selected.copy(), "source_manifest_sha256": self.manifest["sha256"],
                            "evidence": [self.producer_ref()]}
        self.plan = {"source_manifest_sha256": self.manifest["sha256"], "source_manifest": {"source_sha256": self.manifest["sha256"]}}
        self.attestation_path = self.batch / "build-attestation.json"
        self.bind_attestation()

    def producer_ref(self):
        return {"uri": self.producer.as_uri(), "sha256": digest(self.producer.read_bytes())}

    def bind_attestation(self):
        write_json(self.attestation_path, self.attestation)
        self.plan["source_manifest"]["build_attestation_sha256"] = digest(self.attestation_path.read_bytes())

    def review(self):
        return self.analyzer.source_evidence(self.batch, self.plan, self.source, self.selected)

    def test_current_producer_output_names_an_existing_input_digest_and_the_selected_executable(self):
        self.assertEqual(self.review(), self.manifest["sha256"])
        for key in ("wrong-name", "wrong-content", "wrong-artifact"):
            bad = copy.deepcopy(self.build)
            if key == "wrong-name":
                bad["input"]["path"] = "src/unrelated.c"
            elif key == "wrong-content":
                bad["input"]["sha256"] = digest(b"unbuilt source")
            else:
                bad["executable_sha256"] = digest(b"unmeasured application")
            write_json(self.producer, bad)
            self.attestation["evidence"] = [self.producer_ref()]
            self.bind_attestation()
            with self.subTest(change=key), self.assertRaises(ValueError):
                self.review()

    def test_manifest_algorithm_or_records_cannot_change(self):
        for label in ("version", "incorrect-file-hash", "truncated-file-list", "malformed-json"):
            record = copy.deepcopy(self.manifest)
            if label == "malformed-json":
                self.manifest_path.write_bytes(b'{"files": unterminated')
            else:
                if label == "version":
                    record["algorithm"] = self.ALGORITHM + "-unrecognized"
                elif label == "incorrect-file-hash":
                    record["files"][0]["sha256"] = digest(b"other file")
                else:
                    record["files"].pop()
                write_json(self.manifest_path, record)
            with self.subTest(change=label), self.assertRaises(ValueError):
                self.review()

    def test_renamed_algorithm_in_both_source_and_manifest_requires_explicit_approval(self):
        previous = self.verifier_path.read_bytes()
        self.assertIn(self.ALGORITHM.encode("ascii"), previous)
        version = self.ALGORITHM + "-unrecognized"
        self.verifier_path.write_bytes(previous.replace(self.ALGORITHM.encode("ascii"), version.encode("ascii")))
        write_json(self.manifest_path, dict(self.manifest, algorithm=version))
        with self.assertRaises(ValueError):
            self.review()

    def test_unavailable_build_observations_cannot_be_substituted_by_attestation_flags(self):
        self.attestation["evidence"] = []
        self.attestation["passed"] = True
        self.bind_attestation()
        with self.assertRaises(ValueError):
            self.review()

    def test_mutated_source_producer_or_attestation_rejects_even_when_file_size_matches(self):
        for name, file, old, new in (("source", self.native, b"2;", b"3;"),
                                     ("producer", self.producer, b"input", b"other"),
                                     ("attestation", self.attestation_path, b"sha256", b"sha257")):
            original = file.read_bytes()
            self.assertIn(old, original)
            try:
                file.write_bytes(original.replace(old, new, 1))
                with self.subTest(change=name), self.assertRaises(ValueError):
                    self.review()
            finally:
                file.write_bytes(original)
        self.assertEqual(self.review(), self.manifest["sha256"])

    def test_lifecycle_replay_rejects_synthetic_true_flags_without_a_bound_run(self):
        record = {"artifact_sha256": self.selected, "checks": {name: True for name in self.analyzer.gate.LIFECYCLE},
                  "source_manifest_sha256": self.manifest["sha256"], "passed": True}
        with self.assertRaises(ValueError):
            self.analyzer.gate.require_lifecycle_replay(record, self.source, digest(b"synthetic pixels"))


if __name__ == "__main__":
    unittest.main()
