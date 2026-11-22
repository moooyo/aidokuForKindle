import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

OBSERVER = Path(__file__).resolve().parents[2] / "startup-native-20261121" / "observer"
sys.path.insert(0, str(OBSERVER))
import observer

REPLAY = os.environ["RENDER_REPLAY"]
UNIT = os.environ["RENDER_UNIT"]
START = 2_000_000


def reference(turn):
    target = bytes((index * 17 + 19) % 251 for index in range(observer.POINTS))
    text, art = observer.regions(6)
    masks = [observer.indexes(rectangle, turn) for rectangle in text + art]
    return target, masks


def trace(frames):
    records = bytearray()
    for index, frame in enumerate(frames):
        before = START + index * observer.PERIOD
        records.extend(struct.pack("<QQI", before, before + 400, (index % 2) * observer.HEIGHT))
        records.extend(frame)
    end = START + (observer.SLOTS - 1) * observer.PERIOD + 401
    return (f"AFW2 1 1872 4960 {START} {end} 30000 160 0\n".encode() + records)


class RenderWatch(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_replay(self, rule, frames):
        path = self.root / "rule"
        path.write_bytes(rule)
        return subprocess.run([REPLAY, str(path)], input=trace(frames), capture_output=True, check=False)

    def test_mask_agrees_with_existing_observer(self):
        for turn in (0, 180):
            with self.subTest(turn=turn):
                target, masks = reference(turn)
                expected = bytearray(len(target))
                for number, mask in enumerate(masks, 1):
                    for index in mask:
                        self.assertEqual(expected[index], 0)
                        expected[index] = number
                actual = subprocess.check_output([UNIT, "mask", str(turn)])
                self.assertEqual(actual, expected)

    def test_empty_to_three_text_hits_and_cover_states(self):
        for turn in (0, 180):
            with self.subTest(turn=turn):
                target, masks = reference(turn)
                stock = bytes(len(target))
                text = bytearray(stock)
                for mask in masks[:5]:
                    for index in mask:
                        text[index] = target[index]
                rule = f"AFR3RULE 1 1872 4960 {turn} 6 1234\n".encode() + target
                frames = [stock] * 11 + [bytes(text)] * 8 + [target] * 141
                completed = self.run_replay(rule, frames)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout,
                                 b"ARL1 2750000 2900400 3200400 DIFFER DIFFER\n")
                frames = [stock] * 11 + [target] * 149
                completed = self.run_replay(rule, frames)
                self.assertEqual(completed.stdout,
                                 b"ARL1 2750000 2900400 3200400 MATCH MATCH\n")

    def test_each_region_is_required_and_initial_must_differ(self):
        target, masks = reference(0)
        stock = bytes(len(target))
        rule = b"AFR3RULE 1 1872 4960 0 6 1234\n" + target
        for mask in masks[:5]:
            with self.subTest(region=mask[0]):
                damaged = bytearray(target)
                damaged[mask[0]] ^= 255
                completed = self.run_replay(rule, [stock] * 11 + [bytes(damaged)] * 149)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stderr, b"ARL1_ERR NO_MATCH\n")
                completed = self.run_replay(rule, [bytes(damaged)] * 160)
                self.assertEqual(completed.stderr, b"ARL1_ERR INITIAL\n")

    def test_invalid_rule_and_flat_target_are_refused(self):
        target, _ = reference(0)
        stock = bytes(len(target))
        for rule in (b"AFR3RULE 1 1872 4960 0 7 1234\n" + target,
                     b"AFR3RULE 1 1872 4960 0 6 1234\n" + stock,
                     b"AFR3RULE 1 1872 4960 0 6 1234\n" + target + b"x"):
            with self.subTest(rule=rule[:30]):
                completed = self.run_replay(rule, [stock] * 160)
                self.assertEqual(completed.stderr, b"ARL1_ERR RULE\n")
                self.assertEqual(completed.stdout, b"")


if __name__ == "__main__":
    unittest.main()
