import unittest

import launcher_stages as stages


def visit():
    stamps = ("1.99", "2.02", "2.04", "2.04", "2.05", "2.33", "2.33", "2.43", "2.45", "2.45")
    return [f"aidoku: launcher-diag v1 {phase} {stamp}\n".encode() for phase, stamp in zip(stages.PHASES, stamps)]


class LauncherStageTests(unittest.TestCase):
    def test_bounds_and_overlapping_observer(self):
        result = stages.summarize([b"ordinary stderr\n", *visit(), b"\xff unrelated log\n"])
        adjacent = result["adjacent_bounds_ms"]
        self.assertEqual(adjacent["worker_enter_to_observer_start"], [20, 40])
        self.assertEqual(adjacent["observer_wait_begin_to_observer_ready"], [0, 10])
        self.assertEqual(adjacent["portrait_begin_to_portrait_ready"], [270, 290])
        self.assertEqual(adjacent["display_begin_to_display_ready"], [90, 110])
        self.assertEqual(result["overlapping_bounds_ms"], {
            "observer_start_to_ready_including_generation_work": [10, 30],
            "marked_worker_to_app_spawn_returned": [450, 470],
        })

    def test_rejects_partial_duplicate_reordered_and_mixed_visits(self):
        rows = visit()
        for bad in ([], rows[:5], rows[1:], [rows[0], *rows], [rows[1], rows[0], *rows[2:]], rows * 2):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                stages.summarize(bad)

    def test_rejects_bad_clock_and_protocol(self):
        rows = visit()
        for invalid in (rows[-1].replace(b"2.45", b"2.42"), rows[-1].replace(b"2.45", b"2.450"),
                        rows[-1].replace(b"v1 ", b"v2 "), rows[-1].rstrip()):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                stages.summarize([*rows[:-1], invalid])
        with self.assertRaisesRegex(ValueError, "unsupported"):
            stages.summarize([*rows, rows[0].replace(b"v1 ", b"v2 ")])


if __name__ == "__main__":
    unittest.main()
