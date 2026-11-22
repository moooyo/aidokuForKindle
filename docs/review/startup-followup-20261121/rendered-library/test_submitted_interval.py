from pathlib import Path
import tempfile
import unittest

from submitted_interval import interval


START = b"aidoku: Launcher worker started (pid=1234).\n"
END = b"aidoku: Launcher worker exited with status 0.\n"
FRAME = (b"aidoku: startup_library_submitted boottime_us=5000 sequence=3 marker=5"
         b" layout=compact library=nonempty grid=nonempty\n")


class SubmittedInterval(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.case = Path(scratch.name) / "control-01"
        self.case.mkdir()
        for name, value in (("origin", b"stock-tap\n"), ("qualified", b"control-01 stock-tap\n"),
                            ("anchor", b"100 101 150 152\n"), ("pointer-brackets", b"100 101 150 152\n")):
            (self.case / name).write_bytes(value)

    def test_release_not_press_or_cover_completion(self):
        result = interval(self.case, START + FRAME + END)
        self.assertEqual((result["lower_us"], result["upper_us"]), (4848, 4850))

    def test_all_empty_and_filtered_empty_are_both_valid(self):
        for library in (b"empty", b"nonempty"):
            line = FRAME.replace(b"library=nonempty grid=nonempty", b"library=" + library + b" grid=empty")
            self.assertEqual(interval(self.case, START + line + END)["grid"], "empty")

    def test_requires_exactly_one_successful_worker_and_bound_event(self):
        for bad in (START + END, START + FRAME * 2 + END, START * 2 + FRAME + END, FRAME + START + END,
                    START + FRAME, START + FRAME + END.replace(b"0.", b"1."),
                    START + FRAME.replace(b"marker=5", b"marker=0") + END,
                    START + FRAME.replace(b"5000", b"151") + END):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                interval(self.case, bad)

    def test_rejects_staged_or_button_down_anchors(self):
        (self.case / "origin").write_bytes(b"indexed-uri\n")
        with self.assertRaises(ValueError):
            interval(self.case, START + FRAME + END)
        (self.case / "origin").write_bytes(b"stock-tap\n")
        (self.case / "anchor").write_bytes(b"100 101\n")
        with self.assertRaises(ValueError):
            interval(self.case, START + FRAME + END)


if __name__ == "__main__":
    unittest.main()
