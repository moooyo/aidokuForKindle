import copy
import json
import unittest

import audit
import test_host as fixtures


class ClosedTransition(unittest.TestCase):
    def setUp(self):
        runner = fixtures.Runner()
        runner.setUp()
        self.addCleanup(runner.doCleanups)
        self.runner, self.work = runner, runner.work
        runner.line = fixtures.TRANSITION
        self.assertEqual(runner.call("run",1)[0], 0)
        self.attempt, = (self.work / "visits").glob("01-*")

    def test_rebuild_one_closed_slot_not_live_successor(self):
        (self.work / "visits" / "02-unfinished").mkdir(mode=0o700)
        bundle = audit.audit(self.work,1)
        self.assertIsNotNone(bundle["receipts"][0]["transition"])
        self.assertEqual(bundle["analysis"]["measured_initial_to_library"],0)
        text = json.dumps(bundle)
        for private in (fixtures.OWNER,self.attempt.name.partition("-")[2],"lease_token_sha256","mock-private-"):
            self.assertNotIn(private,text)
        path = self.work / "result.json"
        path.write_text(text,encoding="utf-8")
        self.assertEqual(audit.check_public(path),bundle["analysis"])
        bundle["analysis"]["measured_initial_to_library"] = 3
        path.write_text(json.dumps(bundle),encoding="utf-8")
        with self.assertRaises(ValueError):
            audit.check_public(path)

    def test_modified_interval_or_stage_or_frame_cost_fails(self):
        path = self.work / "receipts" / "01.json"
        original = self.runner.frozen.engine.read_json(path)
        for field in ("fence","scene_requested","render"):
            changed = copy.deepcopy(original)
            changed["transition"][field] += 10
            self.runner.frozen.common.checkpoint(path,changed)
            with self.subTest(field=field), self.assertRaisesRegex(Exception,"unverified_public_slot"):
                audit.audit(self.work,1)

    def test_requires_real_return_and_ordinary_card_intent(self):
        ops = self.attempt / "operations.json"
        original = self.runner.frozen.engine.read_json(ops)
        changed = copy.deepcopy(original)
        changed["release"]["owner_and_token_checked"] = False
        self.runner.frozen.common.checkpoint(ops,changed)
        with self.assertRaisesRegex(Exception,"unverified_ownership_or_return"):
            audit.audit(self.work,1)
        self.runner.frozen.common.checkpoint(ops,original)
        (self.attempt / "card-intent.json").unlink()
        with self.assertRaisesRegex(Exception,"missing_card_intent"):
            audit.audit(self.work,1)


if __name__ == "__main__":
    unittest.main()
