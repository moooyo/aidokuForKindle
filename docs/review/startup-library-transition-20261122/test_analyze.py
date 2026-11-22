from pathlib import Path
import tempfile
import unittest

import analyze


COHORT = "1234567890abcdef"
VALUES = (-1000000, -700000, -850000, -840000, -900000, -895000, -770000,
          -650000, -600000, -590000, -560000, -550000, 300000, 20000, 10000, 1000)


def line(values):
    return ("TRANSITION " + " ".join("none" if value is None else str(value) for value in values) + "\n").encode()


def receipt(slot, shift=0):
    interval = {"start": "stock_card_release", "end": "library_frame_submission_callback",
                "lower_us": 2000000 + shift, "upper_us": 2000010 + shift, "layout": "large",
                "library": "nonempty", "grid": "nonempty"}
    stages = {"hz":100, "worker":[120000,130010], "gui":[580000,590010], "main":[600000,600010],
              "initial":[1000000,1000010], "send":[2000000 + shift,2000010 + shift]}
    values = [(value - shift if index < len(analyze.STAMPS) else value) for index, value in enumerate(VALUES)]
    return {"cohort": COHORT, "configuration": "diagnostic", "slot": slot, "result": "measured", "interval": interval,
            "stages": stages, "transition": analyze.parse_transition(line(values), interval, stages),
            "closeout": dict.fromkeys(analyze.prior.CLOSE_FIELDS, True)}


class Transition(unittest.TestCase):
    def test_four_primary_phases_and_three_nested_checks_do_not_double_count(self):
        row = analyze.view(receipt(1))
        self.assertEqual(list(row["primary_us"].values()), [300000,50000,100000,550000])
        self.assertEqual(sum(row["primary_us"].values()), 1000000)
        self.assertEqual(list(row["readiness_us"].values()), [0,0,50000])
        self.assertEqual(sum(row["attach_us"].values()), 100000)
        self.assertEqual(list(row["frame_us"].values()), [300000,20000,10000,1000,219000])
        self.assertEqual(row["relative_to_fence_us"]["metadata_received"], -140000)
        self.assertEqual(row["relative_to_fence_us"]["fonts_registered"], -70000)

    def test_late_production_and_late_ui_delivery_are_distinct(self):
        item = receipt(1)
        item["transition"].update(metadata_ready=-690000,metadata_received=-670000,fonts_registered=-660000)
        analyze.validate(item, COHORT, 1)
        row = analyze.view(item)
        self.assertEqual(list(row["readiness_us"].values()), [10000,30000,10000])
        self.assertEqual(row["worker_delivery_lags_us"]["metadata_produced_to_received"], 20000)
        self.assertEqual(row["relative_to_fence_us"]["fonts_registered"], 40000)

    def test_missing_costs_are_not_zero_and_real_zero_costs_remain_zero(self):
        item = receipt(1)
        for key in analyze.COSTS:
            item["transition"][key] = None
        analyze.validate(item, COHORT, 1)
        self.assertIsNone(analyze.view(item)["frame_us"])
        for key in analyze.COSTS:
            item["transition"][key] = 0
        analyze.validate(item, COHORT, 1)
        self.assertEqual(analyze.view(item)["frame_us"]["other_elapsed"], 550000)

    def test_instant_and_boottime_disagreement_retains_costs_but_no_frame_partition(self):
        item = receipt(1)
        item["transition"].update(render=550000)
        analyze.validate(item, COHORT, 1)
        row = analyze.view(item)
        self.assertIsNone(row["frame_us"])
        self.assertEqual(row["frame_clock_balance_us"], -31000)
        self.assertEqual(row["same_frame_elapsed_us"]["render"], 550000)

    def test_fence_can_overlap_attach_or_metadata_precede_initial(self):
        item = receipt(1)
        item["transition"].update(fence=-610000, metadata_ready=-1050000)
        analyze.validate(item, COHORT, 1)
        row = analyze.view(item)
        self.assertIsNone(row["primary_us"])
        self.assertIsNone(row["readiness_us"])
        self.assertEqual(row["initial_to_library_us"], 1000000)
        self.assertEqual(row["observed_clock_gaps_us"]["fence_to_attach"], -40000)

    def test_calendar_ticks_optional_but_existing_initial_must_match(self):
        item = receipt(1)
        item["stages"] = None
        analyze.validate(item, COHORT, 1)
        self.assertEqual(analyze.view(item)["initial_to_library_us"], 1000000)
        item = receipt(1)
        item["stages"]["initial"] = None
        with self.assertRaisesRegex(ValueError, "marks disagree"):
            analyze.validate(item, COHORT, 1)
        item["transition"].update(initial=None,fence=None)
        analyze.validate(item, COHORT, 1)
        self.assertIsNone(analyze.view(item)["initial_to_library_us"])
        self.assertEqual(analyze.view(item)["frame_us"]["render"], 300000)

    def test_missing_line_keeps_independent_initial_to_library(self):
        item = receipt(1)
        item["transition"] = analyze.parse_transition(b"TRANSITION unavailable\n", item["interval"],item["stages"])
        row = analyze.view(item)
        self.assertIsNone(row["primary_us"])
        self.assertEqual(row["initial_to_library_us"], 1000000)

    def test_forbidden_lines_and_causal_contradictions(self):
        item = receipt(1)
        for old,new in ((b"-1000000",b"-1000001"),(b"-700000",b"-1200000"),(b"-895000",b"-910000"),
                        (b"-895000",b"+0"),(b"-895000",b"-0"),(b"300000",b"none"),(b"300000",b"999999999999"),
                        (b"-850000",b"-2000011"),(b"-560000",b"-590001")):
            with self.subTest(new=new), self.assertRaises(ValueError):
                analyze.parse_transition(line(VALUES).replace(old,new),item["interval"],item["stages"])

    def test_three_formals_use_i_to_s_not_stock_total_for_extremes(self):
        rows = [receipt(1,700000),receipt(2),receipt(3,300000),receipt(4,100000)]
        for key in ("lower_us","upper_us"):
            rows[1]["interval"][key] += 2000000
        rows[1]["stages"] = None
        report = analyze.summarize(rows, COHORT)
        extreme = report["extreme_comparison"]
        self.assertEqual((extreme["fast_slot"], extreme["slow_slot"]),(2,3))
        self.assertEqual(report["initial_to_library"]["median_us"],1100000)
        self.assertEqual(extreme["largest_separated_primary_contribution"],"scene_to_library")
        self.assertEqual(sum(extreme["contributions_us"]["primary_us"].values()),300000)
        self.assertEqual(extreme["contributions_us"]["frame_us"]["other_elapsed"],300000)
        self.assertEqual(extreme["slow_minus_fast_us"],300000)
        self.assertEqual(report["largest_separated_usual_primary_segment"],"scene_to_library")

    def test_primary_negative_contribution_and_unrelated_median(self):
        rows = [receipt(1),receipt(2),receipt(3,100000),receipt(4,300000)]
        rows[3]["transition"]["fence"] -= 50000
        report = analyze.summarize(rows,COHORT)
        changes = report["extreme_comparison"]["contributions_us"]["primary_us"]
        self.assertEqual(changes["initial_to_fence"],-50000)
        self.assertEqual(changes["fence_to_attach"],50000)
        self.assertEqual(sum(changes.values()),300000)

    def test_stage_missing_on_fast_slot_does_not_replace_it(self):
        rows = [receipt(1),receipt(2),receipt(3,100000),receipt(4,300000)]
        rows[1]["transition"] = None
        report = analyze.summarize(rows,COHORT)
        self.assertEqual(report["extreme_comparison"]["fast_slot"],2)
        self.assertIsNone(report["extreme_comparison"]["largest_separated_primary_contribution"])
        self.assertEqual(report["segment_distributions"]["primary_us"]["scene_to_library"]["count"],2)

    def test_resolution_tie_prevents_attribution_and_partial_run_is_descriptive(self):
        rows = [receipt(1),receipt(2),receipt(3,2),receipt(4,300000)]
        report = analyze.summarize(rows,COHORT)
        self.assertFalse(report["extreme_comparison"]["unambiguous_extremes"])
        self.assertIsNone(report["extreme_comparison"]["largest_separated_primary_contribution"])
        self.assertIsNone(analyze.summarize(rows[:3],COHORT)["extreme_comparison"])
        self.assertEqual(analyze.summarize([],COHORT)["initial_to_library"]["median_us"],None)

    def test_issued_slots_four_only_no_replacement_no_unclosed_time(self):
        with tempfile.TemporaryDirectory() as directory:
            for slot in range(1,5):
                analyze.issue(directory,receipt(slot))
            self.assertTrue(analyze.load(Path(directory),COHORT)["complete_primary_partition"])
            with self.assertRaises(ValueError):
                analyze.issue(directory,receipt(4))
        case = receipt(1)
        case["closeout"]["absent"] = False
        with self.assertRaises(ValueError):
            analyze.summarize([case],COHORT)


if __name__ == "__main__":
    unittest.main()
