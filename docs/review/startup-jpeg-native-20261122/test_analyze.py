import json
from pathlib import Path
import tempfile
import unittest
from statistics import median

import analyze


COHORT = "abcdef0123456789"


def receipt(slot, bounds=(100, 104), *, measured=True, cohort=COHORT):
    return {"cohort": cohort, "slot": slot, "arm": analyze.SCHEDULE[slot - 1],
            "closeout": {key: True for key in analyze.CLOSE_FIELDS},
            "result": "measured" if measured else "not_measured",
            "interval": {"start": "stock_card_release", "end": "library_frame_submission_callback",
                         "lower_us": bounds[0], "upper_us": bounds[1], "layout": "large",
                         "library": "nonempty", "grid": "nonempty"} if measured else None}


def cohort_with_pairs(bounds):
    visits = [receipt(1, (1, 2)), receipt(2, (99999, 100000))]
    for control, candidate in bounds:
        for unused in range(2):
            slot = len(visits) + 1
            visits.append(receipt(slot, control if analyze.SCHEDULE[slot - 1] == "A" else candidate))
    return visits


class PairedAnalysis(unittest.TestCase):
    def test_empty_is_unmeasured_not_zero_time(self):
        report = analyze.summarize([], COHORT)
        self.assertIsNone(report["median_paired_midpoint_us"])
        self.assertIsNone(report["median_paired_conservative_bounds_us"])
        self.assertEqual(report["interpretation"], "incomplete_descriptive_only")
        self.assertEqual([row["status"] for row in report["pairs"]], ["not_run"] * 6)

    def test_case_order_is_fixed_and_warmups_do_not_affect_effect(self):
        self.assertEqual(analyze.SCHEDULE, "AB" + "ABBAABBAABBA")
        report = analyze.summarize(cohort_with_pairs([((113, 117), (98, 105))] * 6), COHORT)
        self.assertEqual([row["arm"] for row in report["warmups_excluded"]], ["A", "B"])
        self.assertTrue(report["full_protocol"])
        self.assertEqual(report["median_paired_conservative_bounds_us"], [8, 19])
        self.assertEqual(report["median_paired_midpoint_us"], 13.5)
        self.assertEqual(report["median_paired_midpoint_by_order_us"], {"AB": 13.5, "BA": 13.5})
        self.assertEqual(report["disjoint_B_earlier"], 6)
        self.assertEqual(report["interpretation"], "consistent_improvement_in_this_batch")
        self.assertFalse(report["automatic_promotion"])
        reverse = report["pairs"][1]
        self.assertEqual([row["arm"] for row in reverse["visits"]], ["B", "A"])
        self.assertEqual([row["release_bracket_width_us"] for row in reverse["visits"]], [7, 4])

    def test_median_of_pairs_not_difference_of_arm_medians(self):
        values = [(300, 100), (200, 190), (100, 99), (1000, 700), (1100, 800), (1200, 900)]
        report = analyze.summarize(cohort_with_pairs([((control, control), (candidate, candidate))
                                                       for control, candidate in values]), COHORT)
        self.assertEqual(report["median_paired_midpoint_us"], 250)
        difference_of_medians = median(control for control, _ in values) - median(candidate for _, candidate in values)
        self.assertEqual(difference_of_medians, 205)
        self.assertNotEqual(report["median_paired_midpoint_us"], difference_of_medians)

    def test_touch_is_not_a_win_and_five_disjoint_wins_pass(self):
        report = analyze.summarize(cohort_with_pairs([((115, 117), (100, 102))] * 5
                                                       + [((100, 110), (90, 100))]), COHORT)
        self.assertEqual(report["disjoint_B_earlier"], 5)
        self.assertEqual(report["overlap_or_touch"], 1)
        self.assertEqual(report["pairs"][-1]["direction"], "overlap_or_touch")
        self.assertEqual(report["interpretation"], "consistent_improvement_in_this_batch")

    def test_midpoint_only_wins_do_not_pass(self):
        report = analyze.summarize(cohort_with_pairs([((105, 120), (100, 113))] * 6), COHORT)
        self.assertGreater(report["median_paired_midpoint_us"], 0)
        self.assertLess(report["median_paired_conservative_bounds_us"][0], 0)
        self.assertEqual(report["disjoint_B_earlier"], 0)
        self.assertEqual(report["overlap_or_touch"], 6)
        self.assertEqual(report["interpretation"], "no_consistent_improvement")

    def test_four_disjoint_wins_are_not_five(self):
        report = analyze.summarize(cohort_with_pairs([((110, 110), (100, 100))] * 4
                                                       + [((105, 110), (100, 108))] * 2), COHORT)
        self.assertGreater(report["median_paired_conservative_bounds_us"][0], 0)
        self.assertEqual(report["disjoint_B_earlier"], 4)
        self.assertEqual(report["interpretation"], "no_consistent_improvement")

    def test_negative_savings_preserved(self):
        report = analyze.summarize(cohort_with_pairs([((100, 102), (110, 112))] * 6), COHORT)
        self.assertEqual(report["median_paired_conservative_bounds_us"], [-12, -8])
        self.assertEqual(report["median_paired_midpoint_us"], -10)
        self.assertEqual(report["disjoint_A_earlier"], 6)
        self.assertEqual(report["disjoint_B_earlier"], 0)

    def test_order_effect_not_hidden(self):
        report = analyze.summarize(cohort_with_pairs([((900, 900), (300, 300)), ((150, 150), (151, 151))] * 3), COHORT)
        self.assertGreater(report["median_paired_midpoint_us"], 0)
        self.assertEqual(report["median_paired_midpoint_by_order_us"], {"AB": 600, "BA": -1})
        self.assertEqual(report["interpretation"], "no_consistent_improvement")

    def test_four_pairs_and_one_single_are_reported_without_completion(self):
        visits = cohort_with_pairs([((150, 150), (20, 20))] * 6)[:11]
        report = analyze.summarize(visits, COHORT)
        self.assertEqual(report["paired_count"], 4)
        self.assertFalse(report["full_protocol"])
        self.assertEqual(report["pairs"][4]["visits"][0]["slot"], 11)
        self.assertEqual(report["pairs"][4]["status"], "incomplete")
        self.assertFalse(report["pairs"][5]["visits"])
        self.assertEqual(report["interpretation"], "incomplete_descriptive_only")

    def test_invalid_slot_keeps_its_partner_and_forbids_a_substituted_pair(self):
        visits = cohort_with_pairs([((101, 101), (10, 10))] * 6)
        visits[3] = receipt(4, measured=False)
        report = analyze.summarize(visits, COHORT)
        self.assertEqual(report["paired_count"], 5)
        self.assertEqual(report["pairs"][0]["status"], "not_measured")
        self.assertEqual(report["pairs"][0]["visits"][0]["midpoint_us"], 101)
        self.assertNotIn("midpoint_us", report["pairs"][0]["visits"][1])
        self.assertEqual(report["interpretation"], "incomplete_descriptive_only")
        with self.assertRaises(ValueError):
            analyze.summarize(visits[:3] + visits[4:], COHORT)

    def test_missing_warmup_precludes_a_qualified_batch(self):
        visits = cohort_with_pairs([((101, 101), (10, 10))] * 6)
        visits[0] = receipt(1, measured=False)
        report = analyze.summarize(visits, COHORT)
        self.assertEqual(report["paired_count"], 6)
        self.assertFalse(report["full_protocol"])


class ReceiptGates(unittest.TestCase):
    def test_every_return_witness_is_required_to_count(self):
        for key in analyze.CLOSE_FIELDS:
            for value in (False, 1):
                case = receipt(1)
                case["closeout"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    analyze.summarize([case], COHORT)

    def test_not_measured_cannot_carry_an_interval(self):
        case = receipt(1)
        case["result"] = "not_measured"
        with self.assertRaises(ValueError):
            analyze.summarize([case], COHORT)

    def test_bad_schema_origin_shelf_and_bounds_fail(self):
        changes = [("lower_us", False), ("lower_us", -1), ("lower_us", 200), ("upper_us", 1 << 64),
                   ("upper_us", 103.5), ("extra", "private"), ("start", "indexed_uri"),
                   ("end", "input_ready"), ("layout", "compact"), ("grid", "empty"), ("library", "empty")]
        for field, value in changes:
            case = receipt(1)
            case["interval"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                analyze.summarize([case], COHORT)

    def test_mixed_cohorts_wrong_arm_or_unknown_private_fields_fail(self):
        for changes in ({"cohort": "abcdef0123456780"}, {"arm": "B"}, {"slot": True}, {"device_id": "private"}):
            case = dict(receipt(1), **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                analyze.summarize([case], COHORT)

    def test_archive_is_ordered_exclusive_and_replayable(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch)
            with self.assertRaises(ValueError):
                analyze.issue(directory, receipt(2))
            self.assertEqual(analyze.issue(directory, receipt(1)).name, "01.json")
            analyze.issue(directory, receipt(2, measured=False))
            with self.assertRaises(ValueError):
                analyze.issue(directory, receipt(2))
            report = analyze.load(directory, COHORT)
            self.assertEqual(report["receipt_count"], 2)
            self.assertEqual(report["warmups_excluded"][1]["result"], "not_measured")
            (directory / "04.json").write_text(json.dumps(receipt(4)), encoding="utf-8")
            with self.assertRaises(ValueError):
                analyze.load(directory, COHORT)

    def test_duplicate_keys_and_refrozen_cohort_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch)
            first = analyze.issue(directory, receipt(1))
            with self.assertRaises(ValueError):
                analyze.issue(directory, receipt(2, cohort="1111222233334444"))
            first.write_text('{"arm":"A",' + first.read_text(encoding="utf-8")[1:], encoding="utf-8")
            with self.assertRaises(ValueError):
                analyze.load(directory, COHORT)


if __name__ == "__main__":
    unittest.main()
