import contextlib
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import shlex
import tarfile
import tempfile
import unittest
from unittest import mock

import run_study as study
import transport


@dataclass(frozen=True)
class Request:
    directory: Path
    identity: Path
    owner: str
    run: str


class Fixture:
    LeaseRequest = Request
    LEASE = Path("/mock-only/no-device-lease")

    def __init__(self, work):
        self.work = work
        self.owner = None
        self.fail = {}
        self.commands = []
        self.releases = 0
        self.acquisitions = 0
        self.durable = {}
        for folder in ("plans", "batches", "reference"):
            (work / folder).mkdir(parents=True)
        paths = list(study.sources(work, "study").values())
        paths += [work / "operator/device.sh", work / "operator/observer/observer.py", work / "reference/rule"]
        for leaf in paths:
            leaf.parent.mkdir(parents=True, exist_ok=True)
            leaf.write_bytes(("fixture " + leaf.name + "\n").encode())

    def load(self, module, _path):
        if module not in ("native_entry_lease", "native_entry_ssh"):
            raise AssertionError("Unexpected module load")
        return self

    def ssh_argv(self, *_):
        return ["simulated-transport"]

    def acquire(self, request):
        self.acquisitions += 1
        if self.owner is not None:
            raise RuntimeError("lease_busy")
        self.owner = request
        return {"ownership_verified": True}

    def status(self, request):
        return {"ownership_verified": self.owner == request}

    def release(self, request, *, native_restored_and_connections_closed):
        assert self.owner == request and native_restored_and_connections_closed
        self.releases += 1
        self.owner = None
        return {"owner_and_token_checked": True, "lease_directory_absent_after_release": True}

    def exchange(self, argv, data, timeout):
        assert self.owner and argv[0] == "simulated-transport" and data and timeout > 0
        operation, label, *args = shlex.split(argv[-1])[5:]
        assert label in self.owner.run
        self.commands.append(operation)
        if operation in self.fail and self.fail[operation] > 0:
            self.fail[operation] -= 1
            return 1, b"", b"refused simulated\n"
        if operation == "collect-case":
            case = args[0]
            if self.fail.pop("bad_archive", False):
                return 0, b"not a compressed archive", b""
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                for name in sorted(study.archive_members(case)):
                    sample = {"origin": b"indexed-uri\n", "qualified": (case + " indexed-uri\n").encode(),
                              "anchor": b"1000 1001\n"}.get(name.split("/")[-1], b"simulated data")
                    member = tarfile.TarInfo(name)
                    member.size = len(sample)
                    archive.addfile(member, io.BytesIO(sample))
            self.durable[case] = stream.getvalue()
            return 0, self.durable[case], b""
        if operation == "discard-case":
            case = args[0]
            archive = self.owner.identity.parent / (case + ".tar.gz")
            assert archive.read_bytes() == self.durable[case], "discard before preserving a complete archive"
        result = {"admit": "guard_admitted", "prepare": "device_prepared", "reference": "device_reference",
                  "case": "device_case", "picture": "device_picture", "discard-case": "device_discarded",
                  "close": "device_closed"}[operation]
        return 0, (result + "\n").encode(), b""

    def plan(self, mode="reference"):
        return study.freeze(self.work, mode, "indexed-uri", self.work / "reference" if mode == "study" else None)

    def tapcheck(self):
        reference = self.work / "batches" / ("reference-" + "0" * 16)
        navigation = self.work / "batches" / ("navigation-" + "1" * 16)
        for mode, folder in (("reference", reference), ("navigation", navigation)):
            folder.mkdir()
            (folder / "receipt.json").write_text(json.dumps({"mode": mode, "completed": [mode], "ok": True,
                "release": {"owner_and_token_checked": True, "lease_directory_absent_after_release": True}}))
        (reference / "rule").write_text("frozen rule\n")
        inputs = {}
        for name in ("artwork.guard", "navigation.sh", "dispatch.sh", "tile-input"):
            path = navigation / name
            path.write_text("test " + name)
            inputs["input/" + name] = str(path)
        (navigation / "tap-files.json").write_text(json.dumps(inputs))
        return reference, inputs

    def run(self, plan, owner="aidoku-test", recover=False):
        original_is_file = Path.is_file
        def pretend_host(path):
            return path == Path("/opt/kindle-toolchain/ubuntu-run") or original_is_file(path)
        with mock.patch.object(transport, "load", self.load), mock.patch.object(Path, "is_file", pretend_host), \
                contextlib.redirect_stdout(io.StringIO()):
            return study.operate(self.work, plan, owner, recover)


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        self.temp = self.enterContext(tempfile.TemporaryDirectory())

    def fixture(self):
        return Fixture(Path(tempfile.mkdtemp(dir=self.temp)))

    def test_tampering_is_rejected_before_acquisition(self):
        for changed in ("order", "origin", "device.sh", "guard.sh", "archive", "source"):
            with self.subTest(changed=changed):
                lane = self.fixture()
                plan = lane.plan()
                config = plan / "plan.json"
                if changed in ("order", "origin"):
                    value = json.loads(config.read_text())
                    value["cases"] = ["candidate-01"] if changed == "order" else value["cases"]
                    value["origin"] = "stock-tap" if changed == "origin" else value["origin"]
                    config.write_text(json.dumps(value))
                elif changed == "archive":
                    with tarfile.open(plan / "payload.tar.gz", "w:gz"):
                        pass
                elif changed == "source":
                    (lane.work / "operator/visit/visit.sh").write_bytes(b"modified source")
                else:
                    (plan / changed).write_bytes(b"changed script\n")
                with self.assertRaises((ValueError, OSError, tarfile.TarError)):
                    lane.run(plan)
                self.assertEqual((lane.acquisitions, lane.commands), (0, []))

    def test_success_and_durable_before_discard(self):
        lane = self.fixture()
        plan = lane.plan("study")
        self.assertTrue(lane.run(plan))
        self.assertEqual(list(lane.durable), study.visits())
        self.assertEqual(lane.commands.count("discard-case"), len(study.visits()))
        self.assertEqual((lane.commands[-1], lane.releases, lane.owner), ("close", 1, None))

    def test_pictures_first_freezes_all_visits_and_reports_the_schedule(self):
        lane = self.fixture()
        reference, inputs = lane.tapcheck()
        for mode, origin, bound, files in (("study", "indexed-uri", reference, None), ("reference", "indexed-uri", None, None),
                                           ("navigation", "stock-navigation", None, inputs), ("tapcheck", "stock-tap", reference, inputs)):
            with self.assertRaisesRegex(ValueError, "pictures_first_requires"):
                study.freeze(lane.work, mode, origin, bound, files, pictures_first=True)
        plan = study.freeze(lane.work, "study", "stock-tap", reference, inputs, pictures_first=True)
        original_order = study.visits()
        expected = ["control-picture", "candidate-picture", *original_order[:-2]]
        config = json.loads((plan / "plan.json").read_bytes())
        self.assertEqual((config["schedule"], config["cases"], len(set(expected))), ("pictures-first", expected, 16))
        self.assertTrue(lane.run(plan))
        self.assertEqual(list(lane.durable), expected)
        receipt = json.loads(next(lane.work.glob("batches/study-*/receipt.json")).read_bytes())
        self.assertEqual((receipt["schedule"], receipt["completed"]), ("pictures-first", expected))

    def test_legacy_plan_and_no_rearranged_or_substituted_visit_before_acquisition(self):
        lane = self.fixture()
        reference, inputs = lane.tapcheck()
        plan = study.freeze(lane.work, "study", "stock-tap", reference, inputs, pictures_first=True)
        config = plan / "plan.json"
        original = json.loads(config.read_bytes())
        visits = original["cases"]
        for changes in ({"schedule": None}, {"schedule": "unregistered"}, {"schedule": "pictures-last", "cases": study.visits()},
                        {"origin": "indexed-uri", "tap_files": {}}, {"cases": study.visits()}, {"cases": visits[2:]},
                        {"cases": [*visits[:-1], visits[-2]]}, {"cases": [*visits[:6], *visits[6:8][::-1], *visits[8:]]}):
            with self.subTest(changes=changes):
                config.write_text(json.dumps(original | changes))
                with self.assertRaises(ValueError):
                    lane.run(plan)
        config.write_text(json.dumps({key: value for key, value in (original | {"cases": study.visits()}).items() if key != "schedule"}))
        with self.assertRaisesRegex(ValueError, "missing_frozen_schedule"):
            lane.run(plan)
        self.assertEqual((lane.acquisitions, lane.commands), (0, []))
        legacy = lane.plan("study")
        legacy_path = legacy / "plan.json"
        legacy_plan = json.loads(legacy_path.read_bytes())
        self.assertEqual(legacy_plan.pop("schedule"), "pictures-last")
        (legacy / "schedule").unlink()
        legacy_path.write_text(json.dumps(legacy_plan))
        self.assertTrue(lane.run(legacy))
        self.assertEqual(list(lane.durable), study.visits())
        self.assertNotIn("schedule", json.loads(next(lane.work.glob("batches/study-*/receipt.json")).read_bytes()))

    def test_isolated_tapcheck_uses_only_installed_control_and_one_visit(self):
        lane = self.fixture()
        reference, inputs = lane.tapcheck()
        for origin, bound, files in (("indexed-uri", reference, inputs), ("stock-tap", None, inputs),
                                     ("stock-tap", reference, {}), ("stock-tap", reference, {"input/tile-input": "bad"})):
            with self.assertRaises(ValueError):
                study.freeze(lane.work, "tapcheck", origin, bound, files)
        plan = study.freeze(lane.work, "tapcheck", "stock-tap", reference, inputs)
        with tarfile.open(plan / "payload.tar.gz") as archive:
            members = set(archive.getnames())
            self.assertEqual(members, {"guard/guard.sh", "visit/visit.sh", "observer/framewatch", *inputs,
                                       "control/program-modes", "control/launch.sh", "control/app/aidoku",
                                       "control/app/aidoku-startup-portrait"})
        self.assertTrue(lane.run(plan))
        self.assertEqual(lane.commands, ["admit", "prepare", "case", "collect-case", "discard-case", "close"])
        receipt = json.loads(next(lane.work.glob("batches/tapcheck-*/receipt.json")).read_bytes())
        self.assertEqual((receipt["mode"], receipt["completed"], lane.releases), ("tapcheck", ["control-warmup"], 1))
        (reference / "rule").write_text("replaced source\n")
        with self.assertRaisesRegex(ValueError, "different_frozen_tapcheck_inputs"):
            lane.run(plan)
        self.assertEqual(lane.acquisitions, 1)

    def test_failed_phase_does_not_skip_restoration(self):
        for phase in ("admit", "prepare", "reference", "case", "picture", "collect-case", "discard-case"):
            with self.subTest(phase=phase):
                lane = self.fixture()
                plan = lane.plan("study" if phase in ("case", "picture") else "reference")
                lane.fail[phase] = 1
                self.assertFalse(lane.run(plan))
                self.assertIn(phase, lane.commands)
                self.assertEqual(lane.commands[-1], "admit" if phase == "admit" else "close")
                self.assertEqual((lane.releases, lane.owner), (1, None))

    def test_unpreserved_evidence_is_never_discarded(self):
        lane = self.fixture()
        plan = lane.plan()
        lane.fail["bad_archive"] = True
        self.assertFalse(lane.run(plan))
        self.assertNotIn("discard-case", lane.commands)
        self.assertEqual((lane.commands[-1], lane.releases), ("close", 1))

    def test_busy_lease_sends_no_device_commands(self):
        lane = self.fixture()
        lane.owner = "another-project"
        self.assertFalse(lane.run(lane.plan()))
        self.assertEqual((lane.commands, lane.releases, lane.owner), ([], 0, "another-project"))

    def test_failed_close_retains_lease_and_rejects_foreign_recovery(self):
        lane = self.fixture()
        plan = lane.plan()
        lane.fail["close"] = 1
        self.assertFalse(lane.run(plan))
        self.assertIsNotNone(lane.owner)
        prior = list(lane.commands)
        with self.assertRaisesRegex(ValueError, "no_matching_recovery_lease"):
            lane.run(plan, "some-other-thread", recover=True)
        self.assertEqual(lane.commands, prior)
        self.assertTrue(lane.run(plan, recover=True))
        self.assertEqual(lane.commands[len(prior):], ["close"])
        self.assertEqual((lane.releases, lane.owner), (1, None))


if __name__ == "__main__":
    unittest.main()
