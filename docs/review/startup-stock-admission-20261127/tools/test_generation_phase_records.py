#!/usr/bin/env python3
"""Exercise the future sampler extension with the original accepted clock gate."""

import argparse
from pathlib import Path
import runpy
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
TOOLS = runpy.run_path(str(HERE/'generation_probe.py'))
TEST = runpy.run_path(str(HERE/'test_generation_probe.py'))
WIRE = runpy.run_path(str(HERE/'generation_phase_records.py'))
HEADER = (b'STOCK_SAMPLE control-01 9500000 9555000 12000000 4 5 empty empty\n'
          b'STOCK_BIRTH control-01 100 1000 1061\nSTOCK_OBSERVER_BIND control-01 42\n')
ROW = {'name': 'control-01', 'worker_birth_tick': 1000, 'gui_birth_tick': 1061, 'clock_ticks_per_second': 100,
       'release_before_us': 9500000, 'release_after_us': 9555000, 'library_send_us': 12000000,
       'sequence': 4, 'marker': 5, 'library': 'empty', 'grid': 'empty'}


def suite(historical):
    parser = runpy.run_path(str(historical))['attach_phases']

    class Evidence(unittest.TestCase):
        def trace(self):
            ticks = tuple(range(1000, 1014)) + (1037, 1040, 1048, 1060, 1061)
            return TEST['make_log'](('observer_wait_begin', 'observer_owned'), (1010, 1012), ticks)

        def filter(self, raw):
            return subprocess.run(['awk', '-v', 'worker=42', WIRE['stock_export_awk'](TOOLS).decode('ascii')],
                                  input=raw, capture_output=True, timeout=3)

        def attach(self, raw, sample=ROW):
            return WIRE['attach_generation'](raw, [sample], parser)

        def wire(self):
            result = self.filter(self.trace())
            self.assertEqual((result.returncode, result.stderr), (0, b''))
            return HEADER+result.stdout

        def test_sends_only_typed_rows_and_replays_the_old_outer_gate(self):
            private = b'unrelated raw log and place name\n'
            filtered = self.filter(self.trace()+private)
            self.assertEqual(filtered.returncode, 0)
            self.assertNotIn(private, filtered.stdout)
            item, = self.attach(HEADER+filtered.stdout)
            self.assertEqual(item['launcher_breakdown']['worker_to_gui_birth_us'], [600_000, 620_000])
            self.assertEqual(item['generation_breakdown']['observer_wait_begin_to_owned_us'], [0, 20_000])
            self.assertFalse(item['generation_breakdown']['measured_service_subscription'])
            self.assertNotIn('observer_pid', item['generation_breakdown'])

        def test_filter_produces_no_partial_rows_if_one_is_missing_or_duplicate(self):
            complete = self.trace()
            one = next(line for line in complete.splitlines(keepends=True) if b'observer_record_rename_pending ' in line)
            for bad in (complete.replace(one, b''), complete.replace(one, one+one), complete.replace(one, one.rstrip()+b' stray\n')):
                r = self.filter(bad)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(r.stdout, b'')

        def test_original_spawn_and_new_command_bindings_both_must_hold(self):
            raw = self.wire()
            for candidate, sample in (
                    (raw.replace(b'1000 1061', b'1000 1065'), ROW),
                    (raw.replace(b'1000 1061', b'1000 1065'), ROW | {'gui_birth_tick': 1065}),
                    (raw.replace(b'LAUNCH_BIND 42\n', b'LAUNCH_BIND 47\n'), ROW),
                    (raw.replace(b'12000000 4 5', b'12000001 4 5'), ROW),
                    (raw.replace(b'observer_owned 10.12 43 44\n', b'observer_owned 10.12 43 45\n'), ROW)):
                with self.assertRaises(ValueError):
                    self.attach(candidate, sample)

        def test_refuses_unknown_detail_and_timing_attached_to_no_visit(self):
            raw = self.wire()
            with self.assertRaises(ValueError):
                self.attach(raw+b'GENERATION_OTHER 1\n')
            self.assertEqual(WIRE['attach_generation'](b'AB_CLOSEOUT restored\n', [], parser), [])
            with self.assertRaises(ValueError):
                WIRE['attach_generation'](raw, [], parser)

    return unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Evidence)).wasSuccessful()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('historical_phases', type=Path)
    args = ap.parse_args()
    raise SystemExit(0 if suite(args.historical_phases.resolve(strict=True)) else 1)
