"""Offline replay contracts; all input clocks, pixels and files are synthetic."""

import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_gui as replay


def tap_bytes(**updates):
    obs = dict(mode='tap', clock='CLOCK_BOOTTIME', x=801, y=411, hold_requested_ms=50,
               down_before_us=1000000, down_after_us=1000300,
               up_before_us=1051000, up_after_us=1051900,
               down_confirmed=True, up_sent=True, buttons_clear_after=True, ok=True)
    obs.update(updates)
    return (json.dumps(obs) + '\n').encode('ascii')


class ClockBounds(unittest.TestCase):
    def test_subtraction_preserves_both_uncertain_endpoints(self):
        # Earliest finish minus latest start; latest finish minus earliest start.
        self.assertEqual(replay.subtract((5000, 5300), (1000, 1200)), (3800, 4300))
        self.assertEqual(replay.subtract((5000, 5000), (1000, 1000)), (4000, 4000))

    def test_process_birth_uses_a_tick_bin_not_a_precise_timestamp(self):
        lo, hi = replay.born(1234, 100)
        self.assertEqual(lo, 12340000)
        self.assertIn(hi, (12349999, 12350000))  # Inclusive or conservative upper edge.

    def test_coordinatewise_median_is_not_a_representative_trial(self):
        samples = [(1000, 15000), (2000, 3000), (5000, 9000)]
        expected = dict(lower_ms=2, upper_ms=9)
        self.assertEqual(replay.qs(samples), expected)
        self.assertEqual(replay.qs(list(reversed(samples))), expected)

    def test_even_sample_median(self):
        self.assertEqual(replay.qs([(1000, 3000), (2000, 8000)]),
                         dict(lower_ms=1.5, upper_ms=5.5))


class TriggerProof(unittest.TestCase):
    def test_confirmed_down_and_up_keep_receipt_widths(self):
        parsed = replay.pick_tap(tap_bytes())
        self.assertEqual(parsed['down'], (1000000, 1000300))
        self.assertEqual(parsed['up'], (1051000, 1051900))

    def test_missing_lifecycle_proof_is_not_a_valid_click(self):
        for key in ('down_confirmed', 'up_sent', 'buttons_clear_after', 'ok'):
            with self.subTest(key=key), self.assertRaises((ValueError, RuntimeError)):
                replay.pick_tap(tap_bytes(**{key: False}))

    def test_wrong_clock_or_reversed_sequence_is_rejected(self):
        for changes in ({'clock':'CLOCK_REALTIME'}, {'down_after_us':999999},
                        {'up_before_us':1000299}, {'up_after_us':1050999}, {'mode':'inspect'}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, RuntimeError)):
                replay.pick_tap(tap_bytes(**changes))


class PrivateArchive(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / 'private.tar'
        self.allow = {'a':4, 'b':8}

    def write(self, records):
        with tarfile.open(self.path, mode='w:', format=tarfile.PAX_FORMAT) as archive:
            for name, kind, payload in records:
                record = tarfile.TarInfo(name)
                if kind == 'sparse':
                    record.pax_headers = {'GNU.sparse.map':'0,1', 'GNU.sparse.size':'1'}
                else:
                    record.type = kind
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    record.linkname = 'a'
                record.size = len(payload)
                archive.addfile(record, io.BytesIO(payload) if payload else None)

    def read(self):
        return replay.read_members(self.path, self.allow, {'a'})

    def test_allowlisted_bounded_members_read_exactly(self):
        self.write([('b', tarfile.REGTYPE, b'bytes'), ('a', tarfile.REGTYPE, b'data')])
        self.assertEqual(self.read(), {'a':b'data', 'b':b'bytes'})
        self.write([('a', tarfile.REGTYPE, b'ab')])
        self.assertEqual(self.read(), {'a':b'ab'})

    def test_rejects_missing_duplicate_oversize_or_unexpected_member(self):
        a = ('a', tarfile.REGTYPE, b'aa')
        for records in ([('b', tarfile.REGTYPE, b'b')], [a,a], [('a',tarfile.REGTYPE,b'abcde')],
                        [a,('other',tarfile.REGTYPE,b'b')]):
            with self.subTest(records=records):
                self.write(records)
                with self.assertRaises((ValueError, RuntimeError, tarfile.TarError)):
                    self.read()

    def test_rejects_path_aliases_and_links(self):
        for name, kind in (('../a', tarfile.REGTYPE), ('/a',tarfile.REGTYPE), ('dir/../a',tarfile.REGTYPE),
                           ('b',tarfile.SYMTYPE), ('b',tarfile.LNKTYPE), ('b',tarfile.DIRTYPE),
                           ('b',tarfile.CHRTYPE), ('b',tarfile.FIFOTYPE)):
            with self.subTest(name=name,kind=kind):
                self.write([('a',tarfile.REGTYPE,b'a'),(name,kind,b'')])
                with self.assertRaises((ValueError, RuntimeError, tarfile.TarError)):
                    self.read()

    def test_rejects_regular_file_marked_sparse_by_pax(self):
        self.write([('a','sparse',b'x')])
        with tarfile.open(self.path) as archive:
            self.assertTrue(archive.getmember('a').issparse())
        with self.assertRaises((ValueError, RuntimeError, tarfile.TarError)):
            self.read()


if __name__ == '__main__':
    unittest.main()
