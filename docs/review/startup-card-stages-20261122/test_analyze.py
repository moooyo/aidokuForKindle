from pathlib import Path
import tempfile
import unittest

import analyze


COHORT = "1234abcd5678ef90"


def fixture(slot, *, worker=500_000, gui=1_000_000, main=1_100_000, initial=1_300_000, send=1_800_000,
            width=1_000, hz=100):
    def quantized(point):
        tick = point * hz // 1_000_000
        return [tick * 1_000_000 // hz - width, -(-((tick + 1) * 1_000_000) // hz)]

    def sampled(point):
        return [point - width, point] if point is not None else None

    return {"cohort": COHORT, "slot": slot, "configuration": "diagnostic", "result": "measured",
            "interval": {"start": "stock_card_release", "end": "library_frame_submission_callback",
                         "lower_us": send - width, "upper_us": send, "layout": "large", "library": "nonempty", "grid": "nonempty"},
            "stages": {"hz": hz, "worker": quantized(worker), "gui": quantized(gui), "main": sampled(main),
                       "initial": sampled(initial), "send": sampled(send)}, "closeout": dict.fromkeys(analyze.CLOSE_FIELDS, True)}


def as_line(receipt):
    stages = receipt["stages"]
    pieces = [str(stages["hz"])]
    for name in analyze.POINTS:
        pieces.extend(["none", "none"] if stages[name] is None else map(str, stages[name]))
    return ("STAGES " + " ".join(pieces) + "\n").encode("ascii")


class ClockIntervals(unittest.TestCase):
    def test_same_release_width_is_not_double_counted_in_differences(self):
        receipt = fixture(1)
        self.assertEqual(analyze.parse_stages(as_line(receipt), receipt["interval"]), receipt["stages"])
        self.assertEqual(analyze.view(receipt)["release_relative_milestones_us"]["main"], [1099000, 1100000])
        parts = analyze.view(receipt)["segments"]
        self.assertEqual(parts["worker_to_gui"], {"lower_us": 490000, "upper_us": 510000, "midpoint_us": 500000})
        self.assertEqual(parts["gui_to_main"], {"lower_us": 90000, "upper_us": 100000, "midpoint_us": 95000})
        self.assertEqual(parts["main_to_initial"], {"lower_us": 200000, "upper_us": 200000, "midpoint_us": 200000})
        self.assertEqual(sum(parts[key]["midpoint_us"] for key in analyze.PRIMARY), 1799500)

    def test_runtime_clock_rate_not_assumed_and_outward_rounding(self):
        for rate in (1, 100, 250, 300, 32768, 1000000):
            receipt = fixture(1, hz=rate)
            with self.subTest(rate=rate):
                self.assertEqual(analyze.parse_stages(as_line(receipt), receipt["interval"])["hz"], rate)

    def test_birth_bins_may_overlap_release_and_each_other(self):
        receipt = fixture(1, worker=0, gui=0, main=1300, width=1001)
        row = analyze.view(receipt)
        for item in row["segments"].values():
            self.assertLessEqual(item["lower_us"], item["midpoint_us"])
            self.assertLessEqual(item["midpoint_us"], item["upper_us"])
        self.assertEqual(sum(row["segments"][name]["midpoint_us"] for name in analyze.PRIMARY), row["total"]["midpoint_us"])
        self.assertEqual(row["segments"]["worker_to_gui"]["lower_us"], 0)

    def test_birth_bin_ending_inside_up_is_not_fabricated_or_rejected(self):
        receipt = fixture(1, width=20_000, worker=0)
        row = analyze.view(receipt)
        self.assertEqual(row["segments"]["release_to_worker"],
                         {"lower_us": -20000, "upper_us": 10000, "midpoint_us": -5000})
        self.assertEqual(sum(row["segments"][name]["midpoint_us"] for name in analyze.PRIMARY), row["total"]["midpoint_us"])

    def test_full_missing_is_not_zero(self):
        receipt = fixture(1, initial=None)
        row = analyze.view(receipt)
        self.assertIsNone(analyze.parse_stages(as_line(receipt), receipt["interval"])["initial"])
        self.assertNotIn("initial_to_send", row["segments"])
        self.assertEqual(row["segments"]["main_to_send"]["midpoint_us"], 700000)

    def test_cross_clock_malformed_width_wrong_tick_or_reused_endpoint_are_refused(self):
        for field, value in (("hz", 0), ("hz", True), ("worker", [500000, 510000]), ("main", [1001000, 1100000]),
                             ("gui", [1109000, 1120000]), ("main", [2, 5]), ("send", [1800000, 1801000])):
            receipt = fixture(1)
            receipt["stages"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                analyze.summarize([receipt], COHORT)

    def test_short_line_must_be_canonical_with_balanced_none(self):
        receipt = fixture(1, initial=None)
        line = as_line(receipt)
        for wrong in (line.rstrip(), line * 2, line.replace(b"none none", b"none 123"), line.replace(b"100 ", b"0100 ", 1),
                      line.replace(b"none none", b"0 0"), line.replace(b"499000 ", b"-0 ")):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                analyze.parse_stages(wrong, receipt["interval"])

    def test_rates_cannot_be_pooled(self):
        with self.assertRaisesRegex(ValueError, "tick rate changed"):
            analyze.summarize([fixture(1), fixture(2, hz=250)], COHORT)


class Attribution(unittest.TestCase):
    def test_fast_slow_are_selected_by_whole_visit_not_by_segment(self):
        visits = [fixture(1, send=9000000), fixture(2), fixture(3, main=1200000, send=2000000),
                  fixture(4, worker=900000, gui=1200000, main=1300000, initial=1500000, send=2200000),
                  fixture(5, main=1220000, initial=1550000, send=2100000)]
        report = analyze.summarize(visits, COHORT)
        extremes = report["extreme_comparison"]
        self.assertEqual((extremes["fast_slot"], extremes["slow_slot"]), (2, 4))
        self.assertEqual(extremes["largest_separated_primary_contribution"], "release_to_worker")
        changes = extremes["contributions_us"]
        self.assertEqual(changes["worker_to_main"]["midpoint_us"], -200000)
        self.assertEqual(sum(changes[name]["midpoint_us"] for name in analyze.PRIMARY), 400000)
        self.assertEqual(extremes["slow_minus_fast_total_us"]["midpoint_us"], 400000)
        self.assertEqual(report["total_distribution"]["midpoint_range_us"], [1799500, 2199500])

    def test_extreme_total_intervals_touch_not_unique(self):
        visits = [fixture(1), fixture(2), fixture(3,send=1801000), fixture(4,send=1900000), fixture(5,send=2300000)]
        extremes = analyze.summarize(visits, COHORT)["extreme_comparison"]
        self.assertFalse(extremes["unambiguous_extremes"])
        self.assertIsNone(extremes["largest_separated_primary_contribution"])

    def test_tied_stage_contributions_do_not_have_a_winner(self):
        visits = [fixture(1), fixture(2), fixture(3, send=1850000), fixture(4, send=1890000),
                  fixture(5, worker=600000, gui=1100000, main=1200000, initial=1450000, send=2000000)]
        extremes = analyze.summarize(visits, COHORT)["extreme_comparison"]
        self.assertTrue(extremes["unambiguous_extremes"])
        self.assertIsNone(extremes["largest_separated_primary_contribution"])
        self.assertEqual(extremes["contributions_us"]["main_to_send"]["midpoint_us"], 100000)
        self.assertEqual(extremes["contributions_us"]["release_to_worker"]["midpoint_us"], 100000)

    def test_usual_segment_separate_from_stage_variation(self):
        stable = [fixture(slot, send=2500000 + slot*2000) for slot in range(1, 6)]
        report = analyze.summarize(stable, COHORT)
        self.assertEqual(report["largest_separated_usual_primary_segment"], "main_to_send")
        close = [fixture(slot, worker=500000, gui=800000, main=1000000, initial=1250000, send=1500000)
                 for slot in range(1, 6)]
        self.assertIsNone(analyze.summarize(close, COHORT)["largest_separated_usual_primary_segment"])

    def test_rust_to_library_may_be_largest_only_when_subsegments_are_combined(self):
        visits = [fixture(1), fixture(2), fixture(3, send=1850000), fixture(4, send=1890000),
                  fixture(5, worker=650000, gui=1150000, main=1250000, initial=1550000, send=2150000)]
        extreme = analyze.summarize(visits, COHORT)["extreme_comparison"]
        self.assertEqual(extreme["largest_separated_primary_contribution"], "main_to_send")
        changes = extreme["contributions_us"]
        self.assertEqual([changes[name]["midpoint_us"] for name in ("release_to_worker", "main_to_initial", "initial_to_send")],
                         [150000, 100000, 100000])
        self.assertEqual(changes["main_to_send"]["midpoint_us"], 200000)
        for receipt in visits:
            receipt["stages"]["initial"] = None
        report = analyze.summarize(visits, COHORT)
        self.assertTrue(report["complete_stage_protocol"])
        self.assertFalse(report["complete_initial_diagnostics"])
        self.assertEqual(report["extreme_comparison"]["largest_separated_primary_contribution"], "main_to_send")

    def test_missing_mark_in_fastest_keeps_total_and_does_not_replace_extreme(self):
        visits = [fixture(slot, send=2000000 + slot*10000) for slot in range(1, 6)]
        visits[1]["stages"]["initial"] = None
        result = analyze.summarize(visits, COHORT)
        self.assertTrue(result["complete_endpoints"])
        self.assertTrue(result["complete_stage_protocol"])
        self.assertFalse(result["complete_initial_diagnostics"])
        self.assertEqual(result["extreme_comparison"]["fast_slot"], 2)
        self.assertNotIn("initial_to_send", result["extreme_comparison"]["contributions_us"])
        self.assertEqual(result["extreme_comparison"]["largest_separated_primary_contribution"], "main_to_send")
        self.assertEqual(result["segment_distributions"]["initial_to_send"]["count"], 3)

    def test_missing_whole_stage_line_keeps_only_the_total(self):
        receipt = fixture(1)
        self.assertIsNone(analyze.parse_stages(b"STAGES unavailable\n", receipt["interval"]))
        receipt["stages"] = None
        analyze.validate(receipt, COHORT, 1)
        self.assertIsNotNone(analyze.view(receipt)["total"])
        self.assertFalse(analyze.view(receipt)["segments"])

    def test_empty_or_partial_protocol_never_returns_dominant_result(self):
        for visits in ([], [fixture(slot) for slot in range(1, 5)]):
            result = analyze.summarize(visits, COHORT)
            self.assertIsNone(result["extreme_comparison"])
            self.assertFalse(result["complete_endpoints"])
        failed = fixture(1)
        failed.update(result="not_measured", interval=None, stages=None)
        self.assertFalse(analyze.summarize([failed] + [fixture(slot) for slot in range(2, 6)], COHORT)["complete_endpoints"])


class Publication(unittest.TestCase):
    def test_five_separate_exclusive_slots_and_none_extra(self):
        with tempfile.TemporaryDirectory() as home:
            path = Path(home)
            for slot in range(1, 6):
                analyze.issue(path, fixture(slot))
            self.assertEqual(analyze.load(path, COHORT)["measured_formal"],4)
            for slot in (1,6):
                with self.assertRaises(ValueError):
                    analyze.issue(path, fixture(slot))

    def test_forged_closeout_or_mixed_cohort_rejected(self):
        for key in analyze.CLOSE_FIELDS:
            receipt = fixture(1)
            receipt["closeout"][key] = False
            with self.subTest(key=key), self.assertRaises(ValueError):
                analyze.summarize([receipt], COHORT)
        with self.assertRaises(ValueError):
            analyze.summarize([fixture(1)], "0000000000000000")


if __name__ == "__main__":
    unittest.main()
