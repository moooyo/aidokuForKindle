import importlib
import importlib.util
import io
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

import device_patch


HERE = Path(__file__).resolve().parent
STAGES = Path(os.environ.get('STAGES_FIXTURE_DIR', HERE.parent / 'startup-card-stages-20261122'))
JPEG = Path(os.environ.get('JPEG_FIXTURE_DIR', HERE.parent / 'startup-jpeg-native-20261122'))
sys.path.insert(0, str(JPEG))
spec = importlib.util.spec_from_file_location('jpeg_device_fixture', JPEG / 'test_device.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)

APP = r'''#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
static volatile sig_atomic_t stopped;
static void stop(int unused) { (void)unused; stopped = 1; }
static long long now(void) {
    struct timespec time;
    if (clock_gettime(CLOCK_BOOTTIME, &time)) return -1;
    return (long long)time.tv_sec * 1000000LL + time.tv_nsec / 1000;
}
static void transition(long long sent, const long long *points) {
    char storage[11][32];
    const char *values[11];
    for (size_t index = 0; index < 11; index++) {
        if (points[index] < 0) values[index] = "none";
        else {
            snprintf(storage[index], sizeof(storage[index]), "%lld", points[index]);
            values[index] = storage[index];
        }
    }
    const char *render = getenv("MOCK_COST_NONE") ? "none" : "10";
    const char *settle = getenv("MOCK_COST_NONE") ? "none" : "20";
    const char *write = getenv("MOCK_COST_NONE") ? "none" : "30";
    const char *send = getenv("MOCK_COST_NONE") || getenv("MOCK_PARTIAL_COST") ? "none" : "40";
    int sequence = getenv("MOCK_BAD_SEQUENCE") ? 2 : 1;
    int marker = getenv("MOCK_BAD_MARKER") ? 2 : 1;
    long long reported = getenv("MOCK_BAD_SUBMISSION") ? sent + 1 : sent;
    fprintf(stderr, "aidoku: startup_transition sequence=%d marker=%d submitted_us=%lld "
            "fence_us=%s metadata_ready_us=%s metadata_received_us=%s "
            "fonts_ready_us=%s fonts_received_us=%s fonts_registered_us=%s "
            "attach_enter_us=%s attach_exit_us=%s view_enter_us=%s view_exit_us=%s "
            "scene_requested_us=%s render_us=%s settle_us=%s write_us=%s send_us=%s\n",
            sequence, marker, reported, values[0], values[1], values[2], values[3], values[4], values[5],
            values[6], values[7], values[8], values[9], values[10], render, settle, write, send);
}
int main(void) {
    signal(SIGTERM, stop);
    long long entered = now();
    usleep(1000);
    long long initial = now();
    usleep(1000);
    long long metadata_ready = now(), fonts_ready = now(), fonts_received = now(), fonts_registered = now();
    long long metadata_received = now(), fence = now(), attach_enter = now(), attach_exit = now();
    long long view_enter = now(), view_exit = now(), scene = now();
    usleep(1000);
    long long sent = now();
    if (entered < 0 || initial < 0 || sent < 0) return 1;
    long long points[11] = {fence, metadata_ready, metadata_received, fonts_ready, fonts_received,
                            fonts_registered, attach_enter, attach_exit, view_enter, view_exit, scene};
    if (getenv("MOCK_NO_INITIAL")) points[0] = -1;
    if (getenv("MOCK_NONE_META")) points[1] = -1;
    if (getenv("MOCK_BAD_CLOCK")) points[0] = sent + 1;
    fprintf(stderr, "private fixture log must not be exported\n");
    fprintf(stderr, "aidoku: startup_library_submitted boottime_us=%lld sequence=1 marker=1 layout=large library=nonempty grid=nonempty\n", sent);
    if (!getenv("MOCK_NO_DIAG")) {
        if (getenv("MOCK_NO_INITIAL"))
            fprintf(stderr, "aidoku: startup_stage_diag main_us=%lld initial_us=none submitted_us=%lld\n", entered, sent);
        else
            fprintf(stderr, "aidoku: startup_stage_diag main_us=%lld initial_us=%lld submitted_us=%lld\n", entered, initial, sent);
    }
    if (!getenv("MOCK_NO_TRANSITION")) {
        transition(sent, points);
        if (getenv("MOCK_DUPLICATE_TRANSITION")) transition(sent, points);
    }
    fflush(stderr);
    while (!stopped) usleep(10000);
    return 0;
}
'''


class DevicePatchTests(unittest.TestCase):
    def test_only_the_expected_anchors_change(self):
        original = (STAGES / 'device.sh').read_text(encoding='ascii')
        expected = original
        for old, new in device_patch.REPLACEMENTS:
            self.assertEqual(original.count(old), 1)
            expected = expected.replace(old, new, 1)
        self.assertEqual(device_patch.generate(original), expected)
        for old, _ in device_patch.REPLACEMENTS:
            for invalid in (original.replace(old, ''), original + old):
                with self.assertRaisesRegex(ValueError, 'unexpected_device_source'):
                    device_patch.generate(invalid)

    def test_generation_is_exclusive_and_valid_shell(self):
        with tempfile.TemporaryDirectory(prefix='library-transition-patch-') as directory:
            target = Path(directory) / 'device.sh'
            command = [sys.executable, '-B', str(HERE / 'device_patch.py'), str(STAGES / 'device.sh'), str(target)]
            subprocess.run(command, check=True, capture_output=True)
            subprocess.run(['/bin/sh', '-n', str(target)], check=True)
            if os.environ.get('TEST_BUSYBOX'):
                subprocess.run([os.environ['TEST_BUSYBOX'], 'ash', '-n', str(target)], check=True)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertNotEqual(subprocess.run([sys.executable, '-B', str(HERE / 'device_patch.py'),
                str(STAGES / 'device.sh'), str(STAGES / 'device.sh')], capture_output=True).returncode, 0)

    def test_transition_line_is_bounded_canonical_and_complete(self):
        with tempfile.TemporaryDirectory(prefix='library-transition-output-') as directory:
            root = Path(directory)
            stage, output = root / 'stage', root / 'out'
            (stage / 'submission').mkdir(parents=True)
            (output / 'candidate-01').mkdir(parents=True)
            script = stage / 'submission/submission.sh'
            generated = device_patch.generate((STAGES / 'device.sh').read_text(encoding='ascii'))
            capture = generated.split('capture_submission() {', 1)[1].split('\nrun_visit() {', 1)[0]
            shell = ('set -efu\n'
                     'stage=$1; output=$2; label=0123456789abcdef\n'
                     'number() { case "$1" in \'\'|*[!0-9]*) return 1;; esac; }\n'
                     'put() { (set -C; printf \'%s\\n\' "$2" >"$1"); }\n'
                     'capture_submission() {' + capture + '\n'
                     'capture_submission candidate-01\n')
            primary = 'SUBMISSION 100 110 3 4 large nonempty nonempty\n'
            stages = 'STAGES 100 -10 100 100 200 200 210 none none 100 110\n'
            points = ['-1000000', '-700000', '-850000', '-840000', '-900000', '-895000',
                      '-770000', '-650000', '-600000', '-590000', '-560000', '-550000']
            costs = ['300000', '20000', '10000', '1000']

            def line(point_values, cost_values):
                return 'TRANSITION ' + ' '.join(point_values + cost_values) + '\n'

            available = line(points, costs)
            accepted = [primary + stages + 'TRANSITION unavailable\n',
                        primary + stages + available,
                        primary + 'STAGES unavailable\n' + line(['none'] * 12, ['none'] * 4),
                        primary + stages + line(['0'] * 12, ['0'] * 4),
                        primary + stages + line(['-999999999999'] * 12, ['999999999999'] * 4),
                        primary + stages + line(['none'] + points[1:], ['none'] * 4)]
            rejected = [primary + stages, primary + stages + 'TRANSITION unavailable',
                        primary + stages + 'TRANSITION unavailable \n',
                        primary + stages + 'TRANSITION unavailable extra\n',
                        primary + stages + available + '\n',
                        primary + stages + available + 'private log\n',
                        primary + stages + available + available,
                        primary + stages + available[:-1],
                        primary + stages + available.replace('TRANSITION ', 'TRANSITION  ', 1),
                        primary + stages + available.replace('TRANSITION ', 'TRANSITION\t', 1),
                        primary + stages + ' ' + available,
                        primary + stages + available.rstrip('\n') + ' \n',
                        primary + stages + line(points[:-1], costs),
                        primary + stages + line(points + ['0'], costs),
                        primary + stages + line(['1'] + points[1:], costs),
                        primary + stages + line(['-0'] + points[1:], costs),
                        primary + stages + line(['-01'] + points[1:], costs),
                        primary + stages + line(['-1000000000000'] + points[1:], costs),
                        primary + stages + line(['-x'] + points[1:], costs),
                        primary + stages + line(points, ['none', '0', '0', '0']),
                        primary + stages + line(points, ['-1', '0', '0', '0']),
                        primary + stages + line(points, ['01', '0', '0', '0']),
                        primary + stages + line(points, ['1000000000000', '0', '0', '0']),
                        primary + 'STAGES unavailable extra\n' + available,
                        'SUBMISSION 100 110 3 0 large nonempty nonempty\n' + stages + available]
            interpreters = [('/bin/sh',)]
            if os.environ.get('TEST_BUSYBOX'):
                interpreters.append((os.environ['TEST_BUSYBOX'], 'ash'))
            for interpreter in interpreters:
                for raw in accepted + rejected:
                    with self.subTest(interpreter=interpreter, raw=raw):
                        stage_path = output / 'candidate-01/stages'
                        transition_path = output / 'candidate-01/transition'
                        stage_path.unlink(missing_ok=True)
                        transition_path.unlink(missing_ok=True)
                        script.write_text('#!/bin/sh\nprintf \'%s\' ' + shlex.quote(raw) + '\n', encoding='ascii')
                        run = subprocess.run([*interpreter, '-c', shell, 'capture', str(stage), str(output)],
                                             capture_output=True)
                        self.assertEqual(run.returncode == 0, raw in accepted, (run.stdout, run.stderr))
                        if raw in accepted:
                            first, second, third = raw.splitlines(keepends=True)
                            self.assertEqual(run.stdout, first.encode())
                            self.assertEqual(stage_path.read_bytes(), second.encode())
                            self.assertEqual(transition_path.read_bytes(), third.encode())
                        else:
                            self.assertEqual(run.stdout, b'')
                            self.assertNotIn(b'private log', run.stderr)
                            self.assertFalse(stage_path.exists())
                            self.assertFalse(transition_path.exists())


class TransitionAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        outer = os.environ.get('AIDOKU_TEST_OUTER_MNT')
        if not outer or os.readlink('/proc/self/ns/mnt') == outer:
            raise unittest.SkipTest('Require a fresh private mount namespace on test-env')
        cls.collector = importlib.import_module('submission_generate')
        cls.analyze = importlib.import_module('analyze')
        cls.temporary = tempfile.TemporaryDirectory(prefix='library-transition-scripts-')
        cls.scripts = Path(cls.temporary.name)
        (cls.scripts / 'device.sh').write_text(
            device_patch.generate((STAGES / 'device.sh').read_text(encoding='ascii')), encoding='ascii')
        (cls.scripts / 'submission.sh').write_text(
            cls.collector.generate((STAGES / 'submission.sh').read_text(encoding='ascii')), encoding='ascii')
        shutil.copyfile(JPEG / 'selector.sh', cls.scripts / 'selector.sh')
        cls.old_here, cls.old_selector_here, cls.old_app = previous.HERE, previous.test_selector.HERE, previous.APP
        previous.HERE = cls.scripts
        previous.test_selector.HERE = cls.scripts
        previous.APP = APP

    @classmethod
    def tearDownClass(cls):
        previous.HERE, previous.test_selector.HERE, previous.APP = cls.old_here, cls.old_selector_here, cls.old_app
        cls.temporary.cleanup()

    def setUp(self):
        self.fixture = previous.DeviceIntegration('runTest')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()
        self.fixture.doCleanups()

    def archive(self, fixture, case, *, add_private=False):
        self.assertEqual(fixture.command('admit').stdout, b'jpeg_admitted\n')
        self.assertEqual(fixture.prepare(case).stdout, b'jpeg_prepared\n')
        self.assertEqual(fixture.command('visit', case).stdout, b'jpeg_completed\n')
        if add_private:
            (fixture.state / 'out' / case / 'private-log').write_text('must not be exported\n', encoding='ascii')
        data = fixture.command('collect', case).stdout
        fixture.finish()
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as stream:
            records = {item.name: stream.extractfile(item).read() for item in stream}
        prefix = 'out/' + case + '/'
        self.assertEqual(set(records), {prefix + name for name in ('origin', 'anchor', 'pointer-brackets',
                                                                   'qualified', 'submission', 'stages', 'transition')})
        self.assertEqual(records[prefix + 'anchor'], records[prefix + 'pointer-brackets'])
        published = b''.join(records.values())
        self.assertNotIn(b'private fixture log', published)
        self.assertNotIn(b'must not be exported', published)
        primary = re.fullmatch(rb'SUBMISSION ([0-9]+) ([0-9]+) 1 1 large nonempty nonempty\n',
                               records[prefix + 'submission'])
        self.assertIsNotNone(primary)
        lower, upper = map(int, primary.groups())
        interval = {'start': 'stock_card_release', 'end': 'library_frame_submission_callback',
                    'lower_us': lower, 'upper_us': upper, 'layout': 'large', 'library': 'nonempty',
                    'grid': 'nonempty'}
        stages = self.analyze.parse_stages(records[prefix + 'stages'], interval)
        transition = self.analyze.parse_transition(records[prefix + 'transition'], interval, stages)
        return interval, stages, transition, records[prefix + 'transition']

    def test_same_visit_has_real_mount_process_chain_and_seven_sanitized_records(self):
        interval, stages, transition, raw = self.archive(self.fixture, 'candidate-01', add_private=True)
        self.assertIsNotNone(stages)
        self.assertIsNotNone(transition)
        self.assertEqual(transition['initial'], stages['initial'][0] - stages['send'][0])
        self.assertEqual([transition[key] for key in ('render', 'settle', 'write', 'send')], [10, 20, 30, 40])
        self.assertEqual(len(raw.split()), 17)
        self.assertEqual(stages['send'], [interval['lower_us'], interval['upper_us']])

    def test_missing_transition_keeps_independently_qualified_primary(self):
        self.fixture.environment['MOCK_NO_TRANSITION'] = '1'
        interval, stages, transition, raw = self.archive(self.fixture, 'candidate-01')
        self.assertGreater(interval['upper_us'], 0)
        self.assertIsNotNone(stages)
        self.assertIsNone(transition)
        self.assertEqual(raw, b'TRANSITION unavailable\n')

    def test_missing_prior_diagnostic_preserves_primary_without_inventing_initial(self):
        self.fixture.environment['MOCK_NO_DIAG'] = '1'
        interval, stages, transition, raw = self.archive(self.fixture, 'candidate-01')
        self.assertGreater(interval['upper_us'], 0)
        self.assertIsNone(stages)
        self.assertIsNone(transition)
        self.assertEqual(raw, b'TRANSITION unavailable\n')

    def test_optional_points_and_all_missing_costs_remain_none(self):
        self.fixture.environment.update(MOCK_NONE_META='1', MOCK_COST_NONE='1')
        _, _, transition, _ = self.archive(self.fixture, 'candidate-01')
        self.assertIsNone(transition['metadata_ready'])
        self.assertTrue(all(transition[key] is None for key in ('render', 'settle', 'write', 'send')))

    def test_missing_initial_and_fence_remain_none(self):
        self.fixture.environment['MOCK_NO_INITIAL'] = '1'
        _, stages, transition, _ = self.archive(self.fixture, 'candidate-01')
        self.assertIsNone(stages['initial'])
        self.assertIsNone(transition['initial'])
        self.assertIsNone(transition['fence'])

    def test_unknown_clock_rate_does_not_discard_bound_transition(self):
        self.fixture.shim('getconf', 'exit 1')
        _, stages, transition, _ = self.archive(self.fixture, 'candidate-01')
        self.assertIsNone(stages)
        self.assertIsNotNone(transition['initial'])

    def test_all_four_private_case_aliases_use_the_same_protocol(self):
        selected = self.fixture.modules['programs/selected-app']
        for case in ('candidate-warmup', 'candidate-01', 'candidate-02', 'candidate-03'):
            with self.subTest(case=case):
                fixture = previous.DeviceIntegration('runTest')
                fixture.setUp()
                try:
                    fixture.modules['programs/selected-app'] = selected
                    _, _, transition, _ = self.archive(fixture, case)
                    self.assertIsNotNone(transition)
                finally:
                    fixture.tearDown()
                    fixture.doCleanups()

    def test_duplicate_wrong_sequence_wrong_submission_and_clock_conflict_recover(self):
        for mode in ('MOCK_DUPLICATE_TRANSITION', 'MOCK_BAD_SEQUENCE', 'MOCK_BAD_MARKER', 'MOCK_BAD_SUBMISSION',
                     'MOCK_BAD_CLOCK', 'MOCK_PARTIAL_COST'):
            with self.subTest(mode=mode):
                fixture = previous.DeviceIntegration('runTest')
                fixture.setUp()
                try:
                    fixture.environment[mode] = '1'
                    case = 'candidate-01'
                    fixture.prepare(case)
                    failed = fixture.command('visit', case, success=False)
                    self.assertIn(b'refused submission_transition\n', failed.stderr)
                    self.assertNotIn(b'private fixture log', failed.stderr)
                    self.assertFalse((fixture.state / 'out' / case / 'qualified').exists())
                    fixture.finish()
                finally:
                    fixture.tearDown()
                    fixture.doCleanups()

    def test_missing_or_symlinked_transition_cannot_be_collected(self):
        for kind in ('missing', 'symlink'):
            with self.subTest(kind=kind):
                fixture = previous.DeviceIntegration('runTest')
                fixture.setUp()
                try:
                    case = 'candidate-01'
                    fixture.prepare(case)
                    fixture.command('visit', case)
                    target = fixture.state / 'out' / case / 'transition'
                    target.unlink()
                    if kind == 'symlink':
                        target.symlink_to(fixture.log)
                    failed = fixture.command('collect', case, success=False)
                    self.assertEqual(failed.stdout, b'')
                    fixture.finish()
                finally:
                    fixture.tearDown()
                    fixture.doCleanups()

    def test_case_mismatch_refuses_before_launch_and_restores(self):
        self.fixture.prepare('candidate-01')
        self.fixture.command('visit', 'candidate-02', success=False)
        self.assertFalse((self.fixture.root / 'worker').exists())
        self.fixture.finish()

    def test_screen_saver_is_rejected_without_navigation(self):
        (self.fixture.root / 'asleep').touch()
        self.fixture.command('admit', success=False)
        self.fixture.prepare('candidate-01', success=False)
        self.assertFalse(self.fixture.stage.exists() or self.fixture.state.exists())
        self.assertFalse((self.fixture.root / 'input-events').exists())
        self.assertFalse((self.fixture.root / 'native-events').exists())

    def test_interrupted_real_bind_is_recovered_before_absence(self):
        self.fixture.environment['BREAK_AFTER'] = '1'
        self.fixture.prepare('candidate-01', success=False)
        self.assertTrue(self.fixture.stage.is_dir() and self.fixture.state.is_dir())
        self.assertTrue(previous.test_selector.mount_rows(self.fixture.home / 'app/aidoku'))
        self.fixture.finish()

    def test_guard_failure_prevents_close_until_verification_recovers(self):
        self.fixture.prepare('candidate-01')
        failure = self.fixture.root / 'guard-fail'
        failure.touch()
        self.fixture.command('close', success=False)
        self.assertTrue(self.fixture.stage.is_dir() and self.fixture.state.is_dir())
        failure.unlink()
        self.fixture.finish()


if __name__ == '__main__':
    if os.geteuid() != 0 or sys.platform != 'linux':
        raise SystemExit('Require the test-env private Linux worker')
    if 'AIDOKU_TEST_OUTER_MNT' not in os.environ:
        outer = os.readlink('/proc/self/ns/mnt')
        os.execvpe('unshare', ['unshare', '--mount', '--propagation', 'private', '--fork', '--',
                              sys.executable, '-B', str(Path(__file__).resolve()), *sys.argv[1:]],
                   dict(os.environ, AIDOKU_TEST_OUTER_MNT=outer))
    if os.readlink('/proc/self/ns/mnt') == os.environ['AIDOKU_TEST_OUTER_MNT']:
        raise SystemExit('The fixture must not run in its parent mount namespace')
    unittest.main(verbosity=2)
