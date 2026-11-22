import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "touch_witness.c"


class TouchWitnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="touch-witness-host-")
        cls.binary = Path(cls._tmp.name) / "touch-witness"
        subprocess.run(
            ["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(cls.binary), str(SOURCE)],
            check=True,
            cwd=ROOT,
        )

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_fixture(self, rows, window_ms=12000):
        fixture = Path(self._tmp.name) / "fixture.txt"
        fixture.write_text(rows, encoding="utf-8")
        return subprocess.run(
            [str(self.binary), "fixture", str(fixture), str(window_ms)],
            check=False,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def run_validate(self, rows):
        fixture = Path(self._tmp.name) / "validate-fixture.txt"
        fixture.write_text(rows, encoding="utf-8")
        return subprocess.run(
            [str(self.binary), "validate-fixture", str(fixture)],
            check=False,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_single_contact_bracket(self):
        result = self.run_fixture("1000 1050 0 0 -1\n1100 1150 1 1 77\n1200 1250 0 0 -1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "READY\n")
        self.assertEqual(result.stdout, "ATW1 SINGLE 1000 1250 1000 1150 1100 1250 3\n")

    def test_timeout_without_touch(self):
        result = self.run_fixture("2000 2050 0 0 -1\n2100 2150 0 0 -1\n2200 2250 0 0 -1\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "READY\n")
        self.assertEqual(result.stdout, "ATW1 TIMEOUT 2000 2250 0 0 0 0 3\n")

    def test_rejects_multitouch(self):
        result = self.run_fixture("1000 1050 0 0 -1\n1100 1150 1 2 40\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused ambiguous_contact", result.stderr)

    def test_rejects_missing_release(self):
        result = self.run_fixture("1000 1050 0 0 -1\n1100 1150 1 1 5\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused no_release", result.stderr)

    def test_rejects_invalid_fixture_row(self):
        result = self.run_fixture("bad fixture row\n")
        self.assertEqual(result.returncode, 2)
        self.assertIn("touch-witness: invalid_fixture", result.stderr)

    def test_rejects_tracking_switch(self):
        result = self.run_fixture("1000 1050 0 0 -1\n1100 1150 1 1 21\n1200 1250 1 1 22\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused tracking_switch", result.stderr)

    def test_rejects_sampling_gap(self):
        result = self.run_fixture("1000 1050 0 0 -1\n500000 500050 0 0 -1\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused sampling_gap", result.stderr)

    def test_validate_requires_ev_abs_and_mt_axes(self):
        result = self.run_validate(
            "0 0 1 1 1 1 0 0 10 0 10 0 0 0 10\n"
            "1 0 1 1 1 0 0 0 10 0 10 0 0 0 10\n"
            "1 0 1 1 0 1 0 0 10 0 10 0 0 0 10\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "VC1 INVALID missing_ev_abs",
                "VC1 INVALID missing_abs_capability",
                "VC1 INVALID missing_abs_capability",
            ],
        )

    def test_validate_checks_axis_ranges_and_btn_touch_seen(self):
        result = self.run_validate(
            "1 1 1 1 1 1 1 0 100 0 100 0 1 -1 1000\n"
            "1 1 1 1 1 1 1 10 10 0 100 0 1 -1 1000\n"
            "1 1 1 1 1 1 1 0 100 9 9 0 1 -1 1000\n"
            "1 1 1 1 1 1 1 0 100 0 100 1 1 -1 1000\n"
            "1 1 1 1 1 1 1 0 100 0 100 0 33 -1 1000\n"
            "1 1 1 1 1 1 1 0 100 0 100 0 1 5 5\n"
            "1 1 1 1 1 1 1 0 100 0 100 0 1 -1 1000\n"
            "1 0 1 1 1 1 0 0 100 0 100 0 1 -1 1000\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "VC1 VALID 2 1",
                "VC1 INVALID invalid_mt_x_axis",
                "VC1 INVALID invalid_mt_y_axis",
                "VC1 INVALID invalid_mt_slot_axis",
                "VC1 INVALID invalid_mt_slot_axis",
                "VC1 INVALID invalid_mt_tracking_axis",
                "VC1 VALID 2 1",
                "VC1 VALID 2 0",
            ],
        )

    def test_validate_rejects_bad_fixture_shape(self):
        result = self.run_validate("")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused no_validate_cases", result.stderr)
        result = self.run_validate("1 1 1\n")
        self.assertEqual(result.returncode, 2)
        self.assertIn("touch-witness: invalid_validate_fixture", result.stderr)


if __name__ == "__main__":
    unittest.main()
