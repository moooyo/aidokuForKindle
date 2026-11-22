from pathlib import Path
import tempfile
import unittest

import analyze


COHORT = "1234567890abcdef"
INTERVAL = {"start":"stock_card_release", "end":"library_frame_submission_callback", "lower_us":2200000,
            "upper_us":2201000, "layout":"large", "library":"nonempty", "grid":"nonempty"}
DRAW = b"DRAWACCOUNT 300000 240000 4 0 2 3 1860 2480 gray8\n"


def receipt(slot, wall=300000, cpu=240000):
    line = DRAW.replace(b"300000",str(wall).encode()).replace(b"240000",b"none" if cpu is None else str(cpu).encode())
    draw = analyze.parse_draw(line,INTERVAL,{"render":wall},2,9)
    return {"cohort":COHORT, "configuration":"diagnostic", "slot":slot, "result":"measured", "interval":dict(INTERVAL),
            "draw":draw,"closeout":dict.fromkeys(analyze.stock.CLOSE_FIELDS,True)}


class Accounting(unittest.TestCase):
    def test_wall_cpu_signed_difference_counter_units_and_retained_geometry(self):
        row = analyze.visit(receipt(1))
        self.assertEqual(row["stock_to_library_us"],[2200000,2201000])
        self.assertEqual(row["stock_midpoint_us"],2200500)
        self.assertEqual(row["wall_minus_thread_cpu_us"],60000)
        self.assertEqual(row["cpu_share_percent"],80)
        self.assertFalse(row["cpu_exceeds_wall"])
        self.assertEqual([row[key] for key in analyze.COUNTERS],[4,0,2,3])
        self.assertEqual((row["width"],row["height"],row["format"]),(1860,2480,"gray8"))

    def test_overlapping_clock_windows_do_not_clip_cpu(self):
        row = analyze.visit(receipt(1,10000,10001))
        self.assertEqual(row["wall_minus_thread_cpu_us"],-1)
        self.assertAlmostEqual(row["cpu_share_percent"],100.01)
        self.assertTrue(row["cpu_exceeds_wall"])

    def test_zero_cpu_or_zero_wall_is_not_missing_work(self):
        self.assertEqual(analyze.visit(receipt(1,1000,0))["cpu_share_percent"],0)
        row = analyze.visit(receipt(1,0,1))
        self.assertEqual(row["wall_minus_thread_cpu_us"],-1)
        self.assertIsNone(row["cpu_share_percent"])

    def test_unavailable_cpu_and_counter_set_are_independent(self):
        row = analyze.visit(receipt(1,300000,None))
        self.assertIsNone(row["cpu_share_percent"])
        self.assertIsNone(row["wall_minus_thread_cpu_us"])
        self.assertEqual(row["minor_faults"],4)
        sample = receipt(1)
        sample["draw"] = analyze.parse_draw(DRAW.replace(b"4 0 2 3",b"none none none none"),INTERVAL,{"render":300000},2,9)
        self.assertEqual(analyze.visit(sample)["cpu_share_percent"],80)
        self.assertTrue(all(sample["draw"][key] is None for key in analyze.COUNTERS))

    def test_wrong_units_order_or_partial_counter_vector_cannot_contribute(self):
        for bad in (DRAW.replace(b"300000",b"300000us"),DRAW.replace(b"240000",b"24.0000"),
                    DRAW.replace(b"4 0 2 3",b"4 none 2 3"),DRAW.replace(b"4 0 2 3",b"4 -1 2 3"),
                    DRAW.replace(b"300000 240000",b"240000 300000"), DRAW.replace(b"240000",b"0240000"),
                    DRAW.replace(b"gray8",b"bpp=8"), DRAW.replace(b"1860",b"0"),DRAW + DRAW,DRAW.rstrip()):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                analyze.parse_draw(bad,INTERVAL,{"render":300000},2,9)

    def test_wall_requires_same_frame_and_validated_outer_frame_ids(self):
        for witness,seq,mark in ((None,2,9),({},2,9),({"render":299999},2,9),({"render":300000},0,9),
                                ({"render":300000},2,0),({"render":300000},2,2**32)):
            with self.subTest(witness=witness,seq=seq,mark=mark), self.assertRaises(ValueError):
                analyze.parse_draw(DRAW,INTERVAL,witness,seq,mark)
        self.assertIsNone(analyze.parse_draw(b"DRAWACCOUNT unavailable\n",INTERVAL,None,2,9))

    def test_median_is_of_visit_ratios_not_ratio_of_medians(self):
        visits = [receipt(1,1,100),receipt(2,1000,900),receipt(3,2000,200),receipt(4,10000,7000)]
        report = analyze.summarize(visits,COHORT)
        stats = report["formal_distributions"]
        self.assertEqual(stats["cpu_share_percent"]["median"],70)
        self.assertEqual(stats["wall_us"]["median"],2000)
        self.assertEqual(stats["cpu_us"]["median"],900)
        self.assertEqual(stats["wall_minus_thread_cpu_us"]["median"],1800)
        self.assertEqual(report["measured_formal_draws"],3)
        self.assertTrue(report["complete_draws"])
        self.assertTrue(report["complete_usage_counters"])
        self.assertEqual(report["formal_cpu_above_wall_count"],0)

    def test_unmeasured_endpoint_missing_cpu_and_missing_counters_not_replaced(self):
        rows = [receipt(1),receipt(2),receipt(3),receipt(4)]
        rows[1]["draw"] = None
        rows[2]["draw"]["cpu_us"] = None
        for key in analyze.COUNTERS:
            rows[3]["draw"][key] = None
        report = analyze.summarize(rows,COHORT)
        self.assertTrue(report["complete_endpoints"])
        self.assertFalse(report["complete_draws"])
        self.assertFalse(report["complete_usage_counters"])
        self.assertEqual(report["formal_distributions"]["major_faults"]["count"],1)
        self.assertEqual(report["measured_formal_draws"],1)
        self.assertEqual(analyze.summarize([],COHORT)["formal_distributions"]["cpu_us"]["median"],None)

    def test_unqualified_return_and_mixed_or_replaced_slots_rejected(self):
        for flag in analyze.stock.CLOSE_FIELDS:
            row = receipt(1)
            row["closeout"][flag] = False
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                analyze.summarize([row],COHORT)
        row = receipt(1)
        row.update(result="not_measured",interval=None)
        with self.assertRaises(ValueError):
            analyze.summarize([row],COHORT)
        with tempfile.TemporaryDirectory() as directory:
            for slot in range(1,5):
                analyze.issue(directory,receipt(slot))
            self.assertTrue(analyze.load(Path(directory),COHORT)["complete_draws"])
            for slot in (4,5):
                with self.assertRaises(ValueError):
                    analyze.issue(directory,receipt(slot))
            with self.assertRaises(ValueError):
                analyze.load(Path(directory),"abcdef0000000000")

    def test_counters_require_real_numbers_not_json_booleans(self):
        row = receipt(1)
        for field in ("wall_us","cpu_us",*analyze.COUNTERS,"width","height"):
            bad = dict(row,draw=dict(row["draw"],**{field: True}))
            with self.subTest(field=field), self.assertRaises(ValueError):
                analyze.summarize([bad],COHORT)


if __name__ == "__main__":
    unittest.main()
