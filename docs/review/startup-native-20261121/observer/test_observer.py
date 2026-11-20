import tempfile
import unittest
from pathlib import Path

import observer as obs


START = 2_000_000
ANCHOR = START + obs.PERIOD * 5 + 2_000


def full_frame(which, turn, count=6):
    data = bytearray([243]) * obs.FRAME_BYTES
    for kinds, rectangles in zip(("text", "art"), obs.regions(count)):
        if which == "blank" or which == "text" and kinds == "art":
            continue
        for rect in rectangles:
            for index in obs.indexes(rect, turn):
                row, col = divmod(index, len(obs.XS))
                raw_y, raw_x = obs.YS[row], obs.XS[col]
                logical_y = obs.HEIGHT - 1 - raw_y if turn else raw_y
                data[raw_y * obs.WIDTH + raw_x] = (19 * index + logical_y) % 239 if which != "stock" else 140
    for row in range(16, 29):
        for col in range(obs.WIDTH - 120, obs.WIDTH - 60):
            position = row * obs.WIDTH + col
            data[-1 - position if turn else position] = 62
    return bytes(data)


def capture(path, raw, stamp, rot=1, pan=0):
    path.write_bytes(f"AFR2 {rot} 1872 4960 {pan} {stamp} {stamp + 400}\n".encode() + raw)
    return path


def trace(path, empty, text, art, tweak=None, rot=1, excessive_cpu=False):
    scans = [empty] * 11 + [text] * 8 + [art] * (obs.SLOTS - 19)
    skip = set()
    if tweak:
        skip = tweak(scans) or skip
    out = bytearray()
    for number, payload in enumerate(scans):
        if number in skip:
            continue
        lo = START + number * obs.PERIOD
        out.extend(obs.RECORD.pack(lo, lo + 400, (number % 2) * obs.HEIGHT))
        out.extend(payload)
    path.write_bytes((f"AFW2 {rot} 1872 4960 {START} {START + (obs.SLOTS-1)*obs.PERIOD+401} "
                      f"{2_000_000 if excessive_cpu else 30_000} {obs.SLOTS-len(skip)} {len(skip)}\n").encode() + out)
    return path


class Analysis(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def inputs(self, turn=0, rot=1, count=6):
        native, text, art = (full_frame(kind, turn, count) for kind in ("stock", "text", "art"))
        first = capture(self.root / "first", art, 900_000, rot, obs.HEIGHT)
        second = capture(self.root / "second", art, 1_300_000, rot)
        stock = capture(self.root / "stock", native, 100_000, rot)
        rule = self.root / "rule"
        obs.freeze(stock, first, second, count, turn, rule)
        reordered = bytes(157 if value == 140 else value for value in native)
        capture(self.root / "visit-stock", reordered, 1_700_000, rot, obs.HEIGHT)
        return rule, tuple(map(obs.sample, (reordered, text, art)))

    def test_normal_and_reverse_both_raw_kernel_rotations_and_pan(self):
        for reversal, rot, count in ((0, 3, 6), (180, 1, 15), (0, 1, 2)):
            with self.subTest(reversal=reversal, rot=rot, count=count):
                rule, imgs = self.inputs(reversal, rot, count)
                path = trace(self.root / "trace", *imgs, rot=rot)
                report = obs.measure(rule, path, ANCHOR, ANCHOR + 1000, self.root / "visit-stock")
                bounds = report["relative_to_supplied_device_clock_anchor"]
                self.assertEqual(bounds["text_and_navigation"], [372.0, 448.4])
                self.assertEqual(bounds["all_cover_slots"], [972.0, 1048.4])
                self.assertEqual(bounds["complete_library"], bounds["all_cover_slots"])
                self.assertEqual(report["cover_slots"], 1 + count)
                self.assertEqual(report["observer"]["points_per_scan"], 4466)
                self.assertTrue(obs.compare_settled(rule, self.root / "first", self.root / "second")["ok"])
                rule.unlink()

    def test_no_whole_picture_match_and_late_damaged_cover_fail(self):
        rule, images = self.inputs()
        native, text, art = images
        for name, effect, expected in (
                ("no_launch", lambda frames: frames.__setitem__(slice(None), [native] * len(frames)), "terminal_picture_differs"),
                ("no_art", lambda frames: frames.__setitem__(slice(19, None), [text] * (len(frames) - 19)), "terminal_picture_differs"),
                ("short_final", lambda frames: frames.__setitem__(slice(19, -2), [text] * (len(frames) - 21)), "target_not_stable"),
                ("not_stock", lambda frames: frames.__setitem__(4, art), "pretrigger_stock_mismatch"),
                ("lost_scans", lambda frames: {12, 13, 14}, "observer_gap_or_cpu")):
            with self.subTest(name=name):
                path = trace(self.root / name, native, text, art, effect)
                with self.assertRaisesRegex(ValueError, expected):
                    obs.measure(rule, path, ANCHOR, ANCHOR + 1000, self.root / "visit-stock")

    def test_independent_qualification_and_boundaries(self):
        rule, imgs = self.inputs()
        path = trace(self.root / "trace", *imgs, rot=3)
        with self.assertRaisesRegex(ValueError, "wrong_trace"):
            obs.measure(rule, path, ANCHOR, ANCHOR, self.root / "visit-stock")
        path = trace(path, *imgs, excessive_cpu=True)
        with self.assertRaisesRegex(ValueError, "observer_gap_or_cpu"):
            obs.measure(rule, path, ANCHOR, ANCHOR, self.root / "visit-stock")
        art = full_frame("art", 0)
        flat = bytes([255]) * obs.FRAME_BYTES
        first, later = self.root / "first", self.root / "second"
        for new_first, new_second, stamp, reason in ((flat, flat, 2_100_000, "unobservable_right_status"),
                (art, flat, 2_100_000, "unstable_library"), (art, art, 1_400_000, "spaced")):
            with self.subTest(reason=reason):
                capture(first, new_first, 1_250_000)
                capture(later, new_second, stamp)
                with self.assertRaisesRegex(ValueError, reason):
                    obs.freeze(self.root / "stock", first, later, 6, 0, self.root / "invalid")
        capture(first, art, 1_250_000)
        changed = bytearray(art)
        changed[obs.WIDTH * 300 + 7] = 77
        capture(later, changed, 2_100_000)
        with self.assertRaisesRegex(ValueError, "final_full_body_differs"):
            obs.compare_settled(rule, first, later)

    def test_requires_fresh_preentry_stock_and_distinct_regions(self):
        rule, images = self.inputs()
        timing = trace(self.root / "trace", *images)
        stock = self.root / "visit-stock"
        with self.assertRaisesRegex(ValueError, "stock_not_from_new_visit"):
            obs.measure(rule, timing, ANCHOR, ANCHOR, self.root / "stock")
        for stamp in (START + 1, ANCHOR + 1000):
            with self.subTest(stamp=stamp):
                capture(stock, full_frame("stock", 0), stamp)
                with self.assertRaisesRegex(ValueError, "stock_not_bound_to_trigger"):
                    obs.measure(rule, timing, ANCHOR, ANCHOR, stock)
        capture(stock, full_frame("text", 0), 1_800_000)
        with self.assertRaisesRegex(ValueError, "indistinct_stock_region"):
            obs.stock_target(rule, stock)

    def test_right_status_changes_or_uniformity_fail_left_clock_may_change(self):
        for turn, rot in ((0, 3), (180, 1)):
            with self.subTest(turn=turn):
                rule, _ = self.inputs(turn, rot)
                original = self.root / "first"
                visit = self.root / "final"
                size = obs.WIDTH * obs.HEIGHT
                art = full_frame("art", turn)
                for col, should_pass in ((25, True), (obs.WIDTH - 25, False)):
                    changed = bytearray(art)
                    index = 29 * obs.WIDTH + col
                    changed[-1-index if turn else index] = 97
                    capture(visit, changed, 2_500_000, rot)
                    if should_pass:
                        self.assertTrue(obs.compare_settled(rule, original, visit)["right_status_equal_and_observable"])
                    else:
                        with self.assertRaisesRegex(ValueError, "right_status_differs"):
                            obs.compare_settled(rule, original, visit)
                changed = bytearray(art)
                for row in range(72):
                    begin = row * obs.WIDTH + obs.WIDTH // 2
                    start = size - begin - obs.WIDTH // 2 if turn else begin
                    changed[start:start + obs.WIDTH // 2] = bytes([243]) * (obs.WIDTH // 2)
                capture(visit, changed, 2_500_000, rot)
                with self.assertRaisesRegex(ValueError, "unobservable_right_status"):
                    obs.compare_settled(rule, original, visit)
                rule.unlink()


if __name__ == "__main__":
    unittest.main()
