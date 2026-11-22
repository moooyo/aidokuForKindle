import copy
import json
import shutil
import unittest

import audit
import host
import test_host as fixtures


class ClosedAudit(unittest.TestCase):
    def setUp(self):
        visit = fixtures.Visits()
        visit.setUp()
        self.addCleanup(visit.doCleanups)
        self.visit = visit
        plan = visit.root / "plan"
        plan.mkdir(mode=0o700)
        for name, source in (("host.py", host.__file__), ("analyze.py", host.analyze.__file__),
                             ("rendered.py", host.rendered.__file__), ("common.py", host.common.__file__)):
            shutil.copyfile(source, plan / name)
        (plan / "common.py").chmod(0o700)
        (plan / "tools").mkdir()
        for name in ("guard.sh", "device.sh", "tools/device_lease.py", "tools/run_hardware_smoke.py"):
            (plan / name).write_text("unused\n", encoding="utf-8")
        (plan / "plan.json").write_text(json.dumps({"schema": 1, "owner": visit.visit["owner"], "cohort": fixtures.COHORT,
                                                    "origin": "stock-tap"}), encoding="utf-8")
        (plan / "ready").write_text("ready\n", encoding="utf-8")
        report = visit.run_one()
        host.publish(visit.root, visit.visit, visit.attempt, report)

    def test_closed_prefix_rebuilds_and_replays_without_reading_next_attempt(self):
        (self.visit.root / "visits" / "02-in-progress").mkdir(mode=0o700)
        bundle = audit.audit(self.visit.root, 1)
        self.assertEqual(bundle["audit"]["replayed_submission_archives"], 1)
        self.assertEqual(bundle["receipts"][0]["interval"]["lower_us"], 2_000_000)
        text = json.dumps(bundle)
        for private in (self.visit.visit["owner"], self.visit.visit["label"], self.visit.lease.identity["lease_token_sha256"]):
            self.assertNotIn(private, text)
        path = self.visit.root / "result.json"
        path.write_text(text, encoding="utf-8")
        self.assertEqual(audit.check_public(path), bundle["analysis"])
        bundle["analysis"]["paired_count"] = 1
        path.write_text(json.dumps(bundle), encoding="utf-8")
        with self.assertRaises(ValueError):
            audit.check_public(path)

    def test_release_must_be_from_original_token_and_close_must_precede_absent(self):
        original = host.read_json(self.visit.attempt / "operations.json")
        for mutation in ("identity", "absent"):
            report = copy.deepcopy(original)
            if mutation == "identity":
                report["release"]["lease_token_sha256"] = "different"
            else:
                report["operations"][-1]["outcome"] = "fail"
            host.common.checkpoint(self.visit.attempt / "operations.json", report)
            with self.subTest(mutation=mutation), self.assertRaises(Exception) as rejected:
                audit.audit(self.visit.root, 1)
            self.assertIn(str(rejected.exception), {"unverified_ownership_or_return", "unverified_device_closeout"})

    def test_tampered_public_interval_or_missing_archive_is_not_measured(self):
        path = self.visit.root / "receipts" / "01.json"
        original = host.read_json(path)
        changed = copy.deepcopy(original)
        changed["interval"]["lower_us"] -= 1
        host.common.checkpoint(path, changed)
        with self.assertRaisesRegex(Exception, "public_slot_differs_from_private_evidence"):
            audit.audit(self.visit.root, 1)
        host.common.checkpoint(path, original)
        (self.visit.attempt / "case.tar.gz").unlink()
        with self.assertRaisesRegex(Exception, "public_slot_differs_from_private_evidence"):
            audit.audit(self.visit.root, 1)


if __name__ == "__main__":
    unittest.main()
