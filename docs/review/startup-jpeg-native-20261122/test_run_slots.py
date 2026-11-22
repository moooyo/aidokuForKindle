import json
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
import unittest

from run_slots import run


class SlotDriver(unittest.TestCase):
    def test_calls_one_frozen_host_at_a_time_and_stops_at_first_nonzero(self):
        for results, expected in [([0, 0, 0], [3, 4, 5]), ([3], [3]), ([0, 2], [3, 4])]:
            with self.subTest(results=results), TemporaryDirectory() as directory:
                work = Path(directory)
                (work / "plan").mkdir()
                (work / "plan/plan.json").write_text(json.dumps({"owner": "fixture"}))
                calls = []

                def invoke(argv, *, input, cwd, check):
                    self.assertEqual(input, b"fixture-secret\n")
                    self.assertEqual(cwd, work)
                    self.assertFalse(check)
                    self.assertNotIn("fixture-secret", " ".join(argv))
                    self.assertEqual(argv[2], str(work / "plan/host.py"))
                    self.assertIn("--run", argv)
                    calls.append(int(argv[argv.index("--slot") + 1]))
                    return CompletedProcess(argv, results[len(calls) - 1])

                self.assertEqual(run(work, 3, 5, b"fixture-secret\n", invoke), results[-1])
                self.assertEqual(calls, expected)
