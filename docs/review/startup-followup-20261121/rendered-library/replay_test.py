import copy
import unittest

from replay import submitted_time


class SubmittedTimeTests(unittest.TestCase):
    def setUp(self):
        frame = {"marker": 2, "frame_sequence": 2, "cover_slots": 7,
                 "cover_images": 0, "cover_pending": True, "boottime_us": 1210000}
        self.trace = {"clock": "monotonic_rust_main", "enabled": True, "finished": True, "success": True,
                      "dropped_events": 0, "events": [
                          {"name": "startup_initial_frame_submitted", "full": True, "boottime_us": 1120000},
                          {"name": "startup_library_frame_submitted", **frame},
                          {"name": "startup_cover_frame_submitted", **frame}]}
        self.marks = b"1.00 staged_foreground_requested\n1.07 launcher_worker\n1.50 stock_returned\n"

    def test_uses_library_submission_without_input_or_complete_covers(self):
        self.assertEqual(submitted_time(self.trace, self.marks), 210000)

    def test_rejects_missing_or_duplicate_library_event(self):
        for events in (self.trace["events"][:1], self.trace["events"] + [self.trace["events"][1]]):
            trace = dict(self.trace, events=events)
            with self.subTest(count=len(events)), self.assertRaises(ValueError):
                submitted_time(trace, self.marks)

    def test_rejects_unbound_or_invalid_frame(self):
        for key, value in (("marker", 3), ("frame_sequence", 8), ("cover_pending", False)):
            trace = copy.deepcopy(self.trace)
            trace["events"][2][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                submitted_time(trace, self.marks)

    def test_requires_seven_slots_but_not_completed_covers(self):
        trace = copy.deepcopy(self.trace)
        for event in trace["events"][1:]:
            event["cover_slots"] = 6
        with self.assertRaises(ValueError):
            submitted_time(trace, self.marks)

    def test_requires_full_initial_frame(self):
        trace = copy.deepcopy(self.trace)
        trace["events"][0]["full"] = False
        with self.assertRaises(ValueError):
            submitted_time(trace, self.marks)

    def test_rejects_missing_duplicate_or_imprecise_origin(self):
        for marks in (b"1.00 launcher_worker\n", self.marks + b"1.01 staged_foreground_requested\n",
                      b"1.001 staged_foreground_requested\n"):
            with self.subTest(marks=marks), self.assertRaises(ValueError):
                submitted_time(self.trace, marks)

    def test_rejects_library_before_initial_or_origin(self):
        trace = copy.deepcopy(self.trace)
        trace["events"][1]["boottime_us"] = 1110000
        with self.assertRaises(ValueError):
            submitted_time(trace, self.marks)
        with self.assertRaises(ValueError):
            submitted_time(self.trace, b"1.20 staged_foreground_requested\n")


if __name__ == "__main__":
    unittest.main()
