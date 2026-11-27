#!/usr/bin/env python3
"""Check interleaved timestamps and the instrumented production launcher."""

import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import unittest


TOOLS = runpy.run_path(str(Path(__file__).with_name('generation_probe.py')))
PARENT, CHILD, ORDER, OUTER = (TOOLS[k] for k in ('PARENT', 'CHILD', 'ORDER', 'OUTER'))


def make_log(child_order, child_ticks, parent_ticks=None):
    order = list(ORDER)
    values = dict(zip(ORDER, parent_ticks or range(1000, 1000+len(ORDER)), strict=True))
    values.update(zip(CHILD, (child_ticks[0], *child_ticks), strict=True))
    ids = {PARENT[0]: (43, 0), PARENT[-1]: (43, 44), **dict.fromkeys(CHILD, (43, 44))}
    for child, parent in reversed(tuple(zip(CHILD, (child_order[0], *child_order), strict=True))):
        order.insert(order.index(parent)+1, child)
    lines = []
    for mark in order:
        tick = values[mark]
        stamp = f'{tick//100}.{tick%100:02d}'
        a, b = ids.get(mark, (0, 0))
        if mark in OUTER:
            lines.append(f'aidoku: launcher-diag v1 {mark} {stamp}')
        else:
            lines.append(f'aidoku: generation-diag v1 {mark} {stamp} {a} {b}')
        if mark == 'worker_enter':
            lines.append('aidoku: Launcher worker started (pid=42).')
    return ('\n'.join(lines)+'\naidoku: Launcher worker exited with status 0.\n').encode('ascii')


class TimelineTests(unittest.TestCase):
    def baseline(self):
        return make_log(('observer_start', 'nonce_written'), (1001, 1004))

    def test_observer_can_run_before_parent_fork_returns(self):
        data = TOOLS['summarize'](self.baseline(), 42)
        self.assertEqual(data['observer_start_to_wait_begin_us'], [80_000, 100_000])
        self.assertEqual(data['observer_wait_begin_to_owned_us'], [0, 20_000])
        self.assertEqual(data['observer_record_publication_after_start_us'], [0, 40_000])
        self.assertEqual(data['publication_relation_to_wait_entry'], 'before')
        self.assertTrue(data['verified_matching_observer_command'])
        self.assertFalse(data['measured_service_subscription'])
        self.assertNotIn('pid', json.dumps(data))

    def test_parent_can_accept_before_child_logs_rename_return(self):
        data = TOOLS['summarize'](make_log(('observer_wait_begin', 'observer_owned'), (1010, 1012)), 42)
        self.assertEqual(data['observer_record_publication_after_start_us'], [80_000, 110_000])
        self.assertEqual(data['publication_relation_to_wait_entry'], 'unresolved')
        self.assertEqual(data['observer_record_publication_to_parent_acceptance_us'], [0, 20_000])

    def test_fully_later_publication_is_distinct_from_a_same_bucket(self):
        ticks = tuple(range(1000, 1011)) + tuple(range(1013, 1013+len(ORDER)-11))
        data = TOOLS['summarize'](make_log(('observer_wait_begin', 'observer_ready'), (1012, 1014), ticks), 42)
        self.assertEqual(data['publication_relation_to_wait_entry'], 'after')
        tied = TOOLS['summarize'](make_log(('observer_wait_begin', 'observer_owned'), (1000, 1000), (1000,)*len(ORDER)), 42)
        self.assertEqual(tied['observer_start_to_wait_begin_us'], [0, 10_000])
        self.assertEqual(tied['publication_relation_to_wait_entry'], 'unresolved')

    def test_rejects_missing_duplicate_wrong_scope_and_invalid_clocks(self):
        raw = self.baseline()
        lines = raw.splitlines(keepends=True)
        child = next(l for l in lines if b'observer_record_write_begin ' in l)
        for name, changed, worker in (
            ('missing row', raw.replace(child, b''), 42),
            ('duplicate child', raw.replace(child, child+child), 42),
            ('different worker', raw, 47),
            ('wrong command', raw.replace(child, child.replace(b'43 44', b'43 45')), 42),
            ('wrong helper', raw.replace(child, child.replace(b'43 44', b'46 44')), 42),
            ('wrong child clock', raw.replace(child, child.replace(b'10.01', b'10.20')), 42),
            ('wrong child position', child+raw.replace(child, b''), 42),
            ('malformed clock', raw.replace(child, child.replace(b'10.01', b'10.001')), 42),
            ('premature exit', raw.replace(TOOLS['STOP'], b'aidoku: Launcher worker exited with status 1.'), 42),
            ('truncated', raw[:-1], 42),
            ('second visit', raw+raw, 42),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                TOOLS['summarize'](changed, worker)

    def test_changed_source_anchor_refuses_generation(self):
        with self.assertRaises(ValueError):
            TOOLS['diagnostic_copy'](b'#!/bin/sh\nexit 0\n')


def host_suite(source):
    product = source/'packaging/kindle/launch.sh'
    candidate = TOOLS['diagnostic_copy'](product.read_bytes())
    subprocess.run(['sh', '-n'], input=candidate, check=True)
    fixture = runpy.run_path(str(source/'packaging/tests/launcher_test.py'))
    wait_for = fixture['wait_for']

    class Launcher(fixture['LauncherTests']):
        def setUp(self):
            super().setUp()
            self.environment.pop('AIDOKU_LAUNCHER_DIAGNOSTICS', None)
            self.shell(self.bin/'usleep', 'exec python3 -c \'import sys,time; time.sleep(int(sys.argv[1])/1000000)\' "$@"')
            self.fixture_launcher.write_bytes(TOOLS['diagnostic_copy'](self.fixture_launcher.read_bytes()))

        def checked_log(self):
            raw = (self.home/'data/logs/launcher.log').read_bytes()
            self.assertEqual(TOOLS['worker_from_log'](raw), self.library_worker)
            return TOOLS['summarize'](raw, self.library_worker)

        def test_managed_library_trace_preserves_normal_exit_and_catalog_completion(self):
            self.enable_catalog_notification()
            self.environment.update(AIDOKU_FAKE_EXIT_AFTER='0.7', AIDOKU_FAKE_EXIT_CODE='0')
            pid = self.start_library_entry()
            environment = Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
            self.assertIn(b'AIDOKU_MANAGED_LIBRARY=1', environment)
            self.assertFalse(any(part.startswith(b'AIDOKU_LAUNCHER_DIAGNOSTICS=') for part in environment))
            self.assertEqual(self.complete(), 0)
            self.checked_log()
            operations = self.operations.read_text().splitlines()
            self.assertEqual(operations.count('catalog-notify:running'), 1)
            self.assertGreater(operations.index('catalog-notify:running'), operations.index('start:statusbar'))
            self.assert_recovered()

        def test_foreground_without_opt_in_remains_silent(self):
            self.start_launcher()
            self.stop_launcher()
            self.assertFalse(b'aidoku: generation-diag' in (self.root/'launcher.log').read_bytes())
            self.assertEqual(self.launcher_diag_rows(), [])
            self.assert_recovered()

        def test_probe_waits_for_the_atomic_observer_record(self):
            anchor = b'        mv "$LOCK_DIR/observer-command.pending" "$LOCK_DIR/observer-command"\n'
            hold = b'''        : >"$AIDOKU_TEST_ROOT/at-observer-write"
        while [ ! -e "$AIDOKU_TEST_ROOT/resume-observer-write" ]; do usleep 10000; done
'''
            self.fixture_launcher.write_bytes(TOOLS['change'](self.fixture_launcher.read_bytes(), anchor, hold+anchor))
            self.environment['AIDOKU_LAUNCHER_DIAGNOSTICS'] = TOOLS['OPT_IN'].decode('ascii')
            self.environment.update(AIDOKU_FAKE_EXIT_AFTER='0.45', AIDOKU_FAKE_EXIT_CODE='0')
            self.spawn_launcher()
            try:
                def waiting():
                    return any(b'aidoku: launcher-diag v1 observer_wait_begin ' in line
                               for line in (self.root/'launcher.log').read_bytes().splitlines())
                wait_for(lambda: waiting() and (self.root/'at-observer-write').exists(), description='trace parent waiting')
                self.assertTrue((self.lock/'observer-command.pending').is_file())
                self.assertFalse((self.lock/'observer-command').exists())
                self.assertEqual(self.portrait_queries(), [])
                self.assertEqual(self.operations.read_text(), '')
                self.assertTrue((self.lock/'generation').is_file())
            finally:
                (self.root/'resume-observer-write').touch()
            self.assertEqual(self.complete(), 0)
            path = self.root/'launcher.log'
            data = TOOLS['summarize'](path.read_bytes(), self.launcher.pid)
            self.assertTrue(data['verified_matching_observer_command'])
            self.assert_recovered()

        def test_probe_takes_no_display_after_observer_failure(self):
            anchor = b'run_observer() {\n    helper_setup observer\n'
            self.fixture_launcher.write_bytes(TOOLS['change'](self.fixture_launcher.read_bytes(), anchor, anchor+b'    exit 7\n'))
            self.environment['AIDOKU_LAUNCHER_DIAGNOSTICS'] = TOOLS['OPT_IN'].decode('ascii')
            self.spawn_launcher()
            self.assertEqual(self.complete(), 1)
            raw = (self.root/'launcher.log').read_bytes()
            self.assertNotIn(b'aidoku: generation-diag v1 observer_owned ', raw)
            self.assertEqual(self.portrait_queries(), [])
            self.assertEqual(self.operations.read_text(), '')
            self.assertFalse(self.lock.exists())

        def test_fallback_generation_is_not_instrumented_twice(self):
            self.environment.update(AIDOKU_LAUNCHER_DIAGNOSTICS=TOOLS['OPT_IN'].decode('ascii'), AIDOKU_FAKE_NO_ACK='1')
            self.start_launcher()
            self.event('goingToScreenSaver', state='screenSaver')
            wait_for(lambda: self.phase() == 'fallback', seconds=14, description='checked fallback')
            self.event('outOfScreenSaver', state='active')
            self.event('exitingScreenSaver')
            wait_for(lambda: len(self.traces('started')) == 2 and self.phase() == 'active', description='second generation')
            self.stop_launcher()
            raw = (self.root/'launcher.log').read_bytes()
            self.assertEqual(sum(line.startswith(b'aidoku: generation-diag v1 ') for line in raw.splitlines()), len(PARENT)+len(CHILD))
            self.assertEqual(len(self.launcher_diag_rows()), len(OUTER))
            self.assert_recovered()

    cases = unittest.defaultTestLoader.loadTestsFromTestCase(TimelineTests)
    cases.addTests(Launcher(name) for name in (
        'test_managed_library_trace_preserves_normal_exit_and_catalog_completion',
        'test_foreground_without_opt_in_remains_silent',
        'test_probe_waits_for_the_atomic_observer_record',
        'test_probe_takes_no_display_after_observer_failure',
        'test_fallback_generation_is_not_instrumented_twice',
        'test_catalog_notification_keeps_nonzero_application_status',
        'test_catalog_notification_failure_uses_bounded_standard_scan',
        'test_observer_failure_before_publication_prevents_takeover',
    ))
    return unittest.TextTestRunner(verbosity=2).run(cases).wasSuccessful()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    args = ap.parse_args()
    if os.name != 'posix':
        raise SystemExit('Use the designated Linux test host')
    raise SystemExit(0 if host_suite(args.source.resolve(strict=True)) else 1)
