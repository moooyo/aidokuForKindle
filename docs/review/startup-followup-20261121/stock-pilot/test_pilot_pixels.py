import tempfile
import unittest
from pathlib import Path

from PIL import Image

import pilot_pixels as pixels


START = 10_000_000
DOWN_BEFORE = START + 1_600_000
DOWN_AFTER = DOWN_BEFORE + 20_000
UP_BEFORE = DOWN_AFTER + 80_000
UP_AFTER = UP_BEFORE + 30_000


def lattice_sample(raw):
    sampled = bytearray()
    for row in pixels.observer.YS:
        begin = row * pixels.observer.WIDTH
        for column in pixels.observer.XS:
            sampled.append(raw[begin + column])
    return bytes(sampled)


def frame_from_png(path, half_turn):
    image = Image.open(path).convert("L").resize(
        (pixels.observer.WIDTH, pixels.observer.HEIGHT), Image.Resampling.NEAREST
    )
    if half_turn == 180:
        image = image.transpose(Image.Transpose.ROTATE_180)
    full = image.tobytes()
    return full, lattice_sample(full)


def write_rule(path, target, count=15, half_turn=0, stamp=7_000_000):
    path.write_bytes(f"AFR3RULE 1 1872 4960 {half_turn} {count} {stamp}\n".encode() + target)


def write_stock(path, full, before=8_000_000, after=8_000_400):
    path.write_bytes(f"AFR2 1 1872 4960 0 {before} {after}\n".encode() + full)


def write_trace(path, scans):
    payload = bytearray()
    for index, sampled in enumerate(scans):
        begin = START + index * pixels.observer.PERIOD
        payload.extend(pixels.observer.RECORD.pack(begin, begin + 400, (index % 2) * pixels.observer.HEIGHT))
        payload.extend(sampled)
    end = START + (pixels.observer.SLOTS - 1) * pixels.observer.PERIOD + 401
    header = (f"AFW2 1 1872 4960 {START} {end} 30000 {pixels.observer.SLOTS} 0\n").encode()
    path.write_bytes(header + payload)


class PilotPixelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        local = Path(__file__).resolve().with_name("library.png")
        if local.is_file():
            baselines = local.parent
        else:
            root = Path(__file__).resolve().parents[4]
            baselines = root / "docs" / "uiux-baselines" / "2026-11-14-433a1f6" / "raw" / "scribe"
        cls.library_full, cls.library_sample = frame_from_png(baselines / "library.png", 0)
        _, cls.sort_sample = frame_from_png(baselines / "library-sort.png", 0)
        cls.blank_full = bytes([255]) * pixels.observer.FRAME_BYTES
        cls.blank_sample = lattice_sample(cls.blank_full)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.rule = self.root / "rule"
        self.trace = self.root / "trace"
        self.stock = self.root / "stock"

    def _analyze(self, scans, brackets=(DOWN_BEFORE, DOWN_AFTER, UP_BEFORE, UP_AFTER)):
        write_rule(self.rule, self.library_sample, count=15)
        write_stock(self.stock, self.blank_full)
        write_trace(self.trace, scans)
        return pixels.analyze(self.rule, self.trace, self.stock, brackets)

    def _positive_scans(self):
        scans = [self.blank_sample] * pixels.observer.SLOTS
        for index in range(24, 30):
            scans[index] = self.library_sample
        for index in range(30, pixels.observer.SLOTS):
            scans[index] = self.sort_sample
        return scans

    def test_positive_shelf_then_two_panel_frames_uses_first_confirmed_endpoint(self):
        report = self._analyze(self._positive_scans())
        self.assertIn("ready_upper_ms", report)
        self.assertIn("visible_upper_ms", report)
        self.assertIn("first_response_observed_us", report)
        self.assertEqual(report["first_visible_observed_us"], START + 24 * pixels.observer.PERIOD + 400)
        self.assertEqual(report["first_response_observed_us"], START + 30 * pixels.observer.PERIOD + 400)
        self.assertAlmostEqual(report["visible_upper_ms"], (report["first_visible_observed_us"] - UP_BEFORE) / 1000)
        self.assertAlmostEqual(report["ready_upper_ms"], (report["first_response_observed_us"] - UP_BEFORE) / 1000)
        self.assertTrue(report["first_response_is_upper_bound"])

    def test_rejects_no_panel_and_black_or_white_fake_columns(self):
        with self.assertRaisesRegex(ValueError, "sort_panel_response_not_observed"):
            self._analyze([self.blank_sample] * 24 + [self.library_sample] * (pixels.observer.SLOTS - 24))

        options, directions, done, header, title = pixels.panel_regions(0)
        fake_indexes = {index for mask in (*options, *directions, done, header, title) for index in mask}
        fake_black = bytearray(self.library_sample)
        fake_white = bytearray(self.library_sample)
        for index in fake_indexes:
            fake_black[index] = 0
            fake_white[index] = 255
        scans = [self.blank_sample] * pixels.observer.SLOTS
        for index in range(24, 30):
            scans[index] = self.library_sample
        for index in range(30, pixels.observer.SLOTS):
            scans[index] = bytes(fake_black if index % 2 == 0 else fake_white)
        with self.assertRaisesRegex(ValueError, "sort_panel_response_not_observed"):
            self._analyze(scans)

    def test_rejects_panel_already_present_before_release(self):
        scans = self._positive_scans()
        scans[22] = self.sort_sample
        with self.assertRaisesRegex(ValueError, "target_before_card_release"):
            self._analyze(scans)

    def test_rejects_when_visible_shelf_never_observed(self):
        scans = [self.blank_sample] * pixels.observer.SLOTS
        for index in range(24, pixels.observer.SLOTS):
            scans[index] = self.sort_sample
        with self.assertRaisesRegex(ValueError, "panel_before_visible_shelf"):
            self._analyze(scans)

    def test_rejects_bad_lower_edge_anchor(self):
        bad = (DOWN_AFTER + 10, DOWN_AFTER, UP_BEFORE, UP_AFTER)
        with self.assertRaisesRegex(ValueError, "invalid_release_brackets"):
            self._analyze(self._positive_scans(), bad)

    def test_short_measured_press_and_placeholder_covers(self):
        sample = bytearray(self.library_sample)
        for rectangle in pixels.observer.regions(15)[1]:
            for index in pixels.observer.indexes(rectangle, 0):
                sample[index] = 220
        scans = self._positive_scans()
        scans[24:30] = [bytes(sample)] * 6
        release = (DOWN_BEFORE, DOWN_AFTER, DOWN_AFTER + 10_000, DOWN_AFTER + 12_000)
        report = self._analyze(scans, release)
        self.assertEqual(report['up_before_us'], release[2])
        self.assertEqual(report['ready_upper_ms'], (report['first_response_observed_us'] - release[2]) / 1000)

    def test_primary_endpoint_requires_one_observed_touch_between_shelf_and_panel(self):
        self._analyze(self._positive_scans())
        period = pixels.observer.PERIOD
        def witness(down, up):
            return (f'ATW1 SINGLE {START} {START + 159 * period} {down} {down + 100} '
                    f'{up} {up + 100} 158\n').encode()
        args = self.rule, self.trace, self.stock, (DOWN_BEFORE, DOWN_AFTER, UP_BEFORE, UP_AFTER)
        valid = pixels.analyze_visit(*args, witness(START + 25 * period, START + 26 * period))
        self.assertTrue(valid['physical_sort_tap_observed'])
        for invalid in [b'', b'ATW1 TIMEOUT 1 4 0 0 0 0 3\n', witness(START + period, START + 2 * period),
                        witness(START + 31 * period, START + 32 * period)]:
            with self.subTest(witness=invalid), self.assertRaises(ValueError):
                pixels.analyze_visit(*args, invalid)


if __name__ == "__main__":
    unittest.main()
