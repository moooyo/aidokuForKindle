import copy
import json
import unittest

import audit
import test_host as fixtures


class StageAudit(unittest.TestCase):
    def setUp(self):
        runner = fixtures.Runner()
        runner.setUp()
        self.addCleanup(runner.doCleanups)
        self.runner = runner
        self.work = runner.root
        self.assertEqual(runner.call("run", 1)[0], 0)
        self.attempt, = (self.work / "visits").glob("01-*")

    def test_rebuild_just_one_published_warmup_and_replay_with_no_formal_attribution(self):
        (self.work / "visits" / "02-unclosed-next").mkdir(mode=0o700)
        closed = audit.audit(self.work, 1)
        self.assertEqual(closed["audit"]["visits_with_initial"], 1)
        self.assertEqual(closed["analysis"]["measured_formal"], 0)
        self.assertIsNone(closed["analysis"]["extreme_comparison"])
        path = self.work / "result.json"
        text = json.dumps(closed)
        self.assertNotIn(fixtures.OWNER, text)
        self.assertNotIn(self.attempt.name.partition("-")[2], text)
        self.assertNotIn("mock-private-", text)
        path.write_text(text, encoding="utf-8")
        self.assertEqual(audit.check_public(path), closed["analysis"])
        closed["analysis"]["measured_formal"] = 4
        path.write_text(json.dumps(closed), encoding="utf-8")
        with self.assertRaises(ValueError):
            audit.check_public(path)

    def test_exact_release_independent_absence_and_bound_card_intent_are_required(self):
        read = self.runner.frozen.engine.read_json
        save = self.runner.frozen.common.checkpoint
        reports = self.attempt / "operations.json"
        original = read(reports)
        for which in ("released", "last_op"):
            bad = copy.deepcopy(original)
            if which == "released":
                bad["release"]["owner_and_token_checked"] = False
            else:
                bad["operations"][-1]["outcome"] = "failed"
            save(reports, bad)
            with self.subTest(which=which), self.assertRaisesRegex(Exception, "unverified_"):
                audit.audit(self.work, 1)
        save(reports, original)
        (self.attempt / "card-intent.json").unlink()
        with self.assertRaisesRegex(Exception, "missing_card_intent"):
            audit.audit(self.work, 1)

    def test_private_stage_reparse_rejects_unrelated_public_mark(self):
        path = self.work / "receipts" / "01.json"
        receipt = self.runner.frozen.engine.read_json(path)
        receipt["stages"]["main"] = [value + 5 for value in receipt["stages"]["main"]]
        self.runner.frozen.common.checkpoint(path, receipt)
        with self.assertRaisesRegex(Exception, "unverified_public_slot"):
            audit.audit(self.work, 1)

    def test_missing_stage_data_preserves_only_the_independently_qualified_endpoint(self):
        self.runner.stage = b"STAGES unavailable\n"
        self.assertEqual(self.runner.call("run", 2)[0], 0)
        result = audit.audit(self.work, 2)
        self.assertIsNone(result["receipts"][1]["stages"])
        self.assertIsNotNone(result["receipts"][1]["interval"])
        self.assertEqual(result["audit"]["replayed_endpoint_archives"], 2)
        self.assertEqual(result["audit"]["visits_with_stages"], 1)


if __name__ == "__main__":
    unittest.main()
