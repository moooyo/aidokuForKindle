import copy
import json
import unittest

import audit
import test_host as fixtures


class IndependentClosedAudit(unittest.TestCase):
    def setUp(self):
        runner = fixtures.Runner()
        runner.setUp()
        self.addCleanup(runner.doCleanups)
        self.runner, self.work = runner, runner.work
        runner.line = fixtures.DRAW
        self.assertEqual(runner.call("run",1)[0],0)
        self.attempt, = (self.work / "visits").glob("01-*")

    def test_rebuild_is_an_independent_read_of_private_archive_and_does_not_touch_real_receipt(self):
        path = self.work / "receipts" / "01.json"
        original = path.stat()
        (self.work / "visits" / "02-currently-running").mkdir(mode=0o700)
        bundle = audit.audit(self.work,1)
        self.assertEqual(bundle["analysis"]["measured_formal_draws"],0)
        self.assertEqual(bundle["audit"]["replayed_draw_records"],1)
        self.assertEqual((original.st_ino,original.st_mtime_ns),(path.stat().st_ino,path.stat().st_mtime_ns))
        result = self.work / "result.json"
        text = json.dumps(bundle)
        self.assertNotIn(self.attempt.name.partition("-")[2],text)
        self.assertNotIn(fixtures.OWNER,text)
        self.assertNotIn("lease_token_sha256",text)
        result.write_text(text,encoding="utf-8")
        self.assertEqual(audit.check_public(result),bundle["analysis"])
        bundle["analysis"]["measured_formal_draws"] = 3
        result.write_text(json.dumps(bundle),encoding="utf-8")
        with self.assertRaises(ValueError):
            audit.check_public(result)

    def test_wrong_outer_cpu_counter_format_or_scope_archive_refused(self):
        path = self.work / "receipts" / "01.json"
        signed = self.runner.frozen.engine.read_json(path)
        for key,value in (("cpu_us",235000),("major_faults",9),("format","rgb8")):
            changed = copy.deepcopy(signed)
            changed["draw"][key] = value
            self.runner.frozen.common.checkpoint(path,changed)
            with self.subTest(key=key), self.assertRaisesRegex(Exception,"archive_disagrees"):
                audit.audit(self.work,1)

    def test_replay_requires_original_token_absence_card_and_cohort(self):
        ops = self.attempt / "operations.json"
        original = self.runner.frozen.engine.read_json(ops)
        for field in ("release","absence","last"):
            changed = copy.deepcopy(original)
            if field == "release":
                changed["release"]["lease_token_sha256"] = "wrong-test-token"
            elif field == "absence":
                changed["absence_confirmed"] = False
            else:
                changed["operations"][-1]["outcome"] = "failed"
            self.runner.frozen.common.checkpoint(ops,changed)
            with self.subTest(field=field), self.assertRaisesRegex(Exception,"unverified_"):
                audit.audit(self.work,1)
        self.runner.frozen.common.checkpoint(ops,original)
        (self.attempt / "card-intent.json").unlink()
        with self.assertRaisesRegex(Exception,"missing_card_intent"):
            audit.audit(self.work,1)

    def test_missing_cpu_has_no_imaginary_measurement_after_return(self):
        self.runner.line = fixtures.DRAW.replace(b"240000",b"none")
        self.assertEqual(self.runner.call("run",2)[0],4)
        value = audit.audit(self.work,2)
        self.assertEqual(value["audit"]["replayed_endpoints"],2)
        self.assertIsNotNone(value["receipts"][1]["interval"])
        self.assertIsNone(value["receipts"][1]["draw"]["cpu_us"])
        self.assertEqual(value["analysis"]["measured_formal_draws"],0)


if __name__ == "__main__":
    unittest.main()
