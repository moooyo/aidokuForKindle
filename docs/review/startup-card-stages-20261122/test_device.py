import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

import analyze
import device_patch
import submission_generate


HERE = Path(__file__).resolve().parent
OLD = Path(os.environ.get('JPEG_FIXTURE_DIR', HERE.parent / 'startup-jpeg-native-20261122'))
sys.path.insert(0, str(OLD))
spec = importlib.util.spec_from_file_location('jpeg_device_fixture', OLD / 'test_device.py')
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
int main(void) {
    signal(SIGTERM, stop);
    long long entered = now(), initial = now(), sent = now();
    if (entered < 0 || initial < 0 || sent < 0) return 1;
    fprintf(stderr, "aidoku: startup_library_submitted boottime_us=%lld sequence=1 marker=1 layout=large library=nonempty grid=nonempty\n", sent);
    if (!getenv("MOCK_NO_DIAG")) {
        if (getenv("MOCK_NO_INITIAL"))
            fprintf(stderr, "aidoku: startup_stage_diag main_us=%lld initial_us=none submitted_us=%lld\n", entered, sent);
        else
            fprintf(stderr, "aidoku: startup_stage_diag main_us=%lld initial_us=%lld submitted_us=%lld\n", entered, initial, sent);
        if (getenv("MOCK_DUPLICATE_DIAG"))
            fprintf(stderr, "aidoku: startup_stage_diag main_us=%lld initial_us=%lld submitted_us=%lld\n", entered, initial, sent);
    }
    fflush(stderr);
    while (!stopped) usleep(10000);
    return 0;
}
'''


class DevicePatchTests(unittest.TestCase):
    def test_only_two_expected_source_anchors_change(self):
        original = (OLD / 'device.sh').read_text(encoding='ascii')
        result = device_patch.generate(original)
        expected = original.replace(device_patch.OLD_MEMBERS, device_patch.NEW_MEMBERS, 1).replace(
            device_patch.OLD_CAPTURE, device_patch.NEW_CAPTURE, 1)
        self.assertEqual(result, expected)
        self.assertEqual(original.count(device_patch.OLD_MEMBERS), 1)
        self.assertEqual(original.count(device_patch.OLD_CAPTURE), 1)
        for invalid in (original.replace(device_patch.OLD_MEMBERS, ''),
                        original.replace(device_patch.OLD_CAPTURE, ''),
                        original + device_patch.OLD_MEMBERS,
                        original + device_patch.OLD_CAPTURE):
            with self.assertRaisesRegex(ValueError, 'unexpected_device_source'):
                device_patch.generate(invalid)

    def test_generation_is_exclusive_and_valid_shell(self):
        with tempfile.TemporaryDirectory(prefix='card-stage-patch-') as directory:
            target = Path(directory) / 'device.sh'
            command = [sys.executable, '-B', str(HERE / 'device_patch.py'), str(OLD / 'device.sh'), str(target)]
            subprocess.run(command, check=True, capture_output=True)
            subprocess.run(['/bin/sh', '-n', str(target)], check=True)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertNotEqual(subprocess.run([sys.executable, '-B', str(HERE / 'device_patch.py'),
                str(OLD / 'device.sh'), str(OLD / 'device.sh')], capture_output=True).returncode, 0)

    def test_stage_line_acceptance_is_explicit_and_bounded(self):
        with tempfile.TemporaryDirectory(prefix='card-stage-output-') as directory:
            root = Path(directory)
            stage, output = root / 'stage', root / 'out'
            (stage / 'submission').mkdir(parents=True)
            (output / 'candidate-01').mkdir(parents=True)
            script = stage / 'submission/submission.sh'
            capture = device_patch.NEW_CAPTURE
            shell = ('set -efu\n'
                     'stage=$1; output=$2; label=0123456789abcdef\n'
                     'number() { case "$1" in \'\'|*[!0-9]*) return 1;; esac; }\n'
                     'put() { (set -C; printf \'%s\\n\' "$2" >"$1"); }\n' + capture + '\n'
                     'capture_submission candidate-01\n')
            primary = 'SUBMISSION 100 110 3 4 large nonempty nonempty\n'
            accepted = ('STAGES unavailable\n', 'STAGES 100 -10 100 100 200 200 210 none none 100 110\n')
            rejected = ('', 'STAGES unavailable extra\n', 'STAGES 0 1 2 3 4 5 6 none none 7 8\n',
                        'STAGES 100 1 2 3 4 5 6 none 8 9 10\n',
                        'STAGES 100 1 2 3 4 5 6 none none x 10\n',
                        'STAGES 100 1 2 3 4 5 6 none none 9 10\nprivate log\n')
            for stage_line in accepted + rejected:
                with self.subTest(stage_line=stage_line):
                    result_path = output / 'candidate-01/stages'
                    result_path.unlink(missing_ok=True)
                    script.write_text('#!/bin/sh\ncat <<\'EOF\'\n' + primary + stage_line + 'EOF\n',
                                      encoding='ascii')
                    run = subprocess.run(['/bin/sh', '-c', shell, 'capture', str(stage), str(output)],
                                         capture_output=True)
                    self.assertEqual(run.returncode == 0, stage_line in accepted)
                    if stage_line in accepted:
                        self.assertEqual(run.stdout, primary.encode())
                        self.assertEqual(result_path.read_bytes(), stage_line.encode())
                    else:
                        self.assertFalse(result_path.exists())


class StageAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        outer = os.environ.get('AIDOKU_TEST_OUTER_MNT')
        if not outer or os.readlink('/proc/self/ns/mnt') == outer:
            raise unittest.SkipTest('Require a fresh private mount namespace on test-env')
        cls.temporary = tempfile.TemporaryDirectory(prefix='card-stage-scripts-')
        cls.scripts = Path(cls.temporary.name)
        (cls.scripts / 'device.sh').write_text(
            device_patch.generate((OLD / 'device.sh').read_text(encoding='ascii')), encoding='ascii')
        (cls.scripts / 'submission.sh').write_text(
            submission_generate.generate((OLD / 'submission.sh').read_text(encoding='ascii')), encoding='ascii')
        shutil.copyfile(OLD / 'selector.sh', cls.scripts / 'selector.sh')
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

    def run_visit(self):
        case = 'candidate-01'
        self.assertEqual(self.fixture.prepare(case).stdout, b'jpeg_prepared\n')
        self.assertEqual(self.fixture.command('visit', case).stdout, b'jpeg_completed\n')
        archive = self.fixture.command('collect', case).stdout
        self.fixture.finish()
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as stream:
            records = {item.name: stream.extractfile(item).read() for item in stream}
        prefix = 'out/' + case + '/'
        self.assertEqual(set(records), {prefix + name for name in ('origin', 'anchor', 'pointer-brackets',
                                                                   'qualified', 'submission', 'stages')})
        self.assertEqual(records[prefix + 'anchor'], records[prefix + 'pointer-brackets'])
        primary = re.fullmatch(rb'SUBMISSION ([0-9]+) ([0-9]+) 1 1 large nonempty nonempty\n',
                               records[prefix + 'submission'])
        self.assertIsNotNone(primary)
        lower, upper = map(int, primary.groups())
        interval = {'start': 'stock_card_release', 'end': 'library_frame_submission_callback',
                    'lower_us': lower, 'upper_us': upper, 'layout': 'large', 'library': 'nonempty',
                    'grid': 'nonempty'}
        return records[prefix + 'stages'], interval

    def test_three_real_shells_report_same_visit_stage_intervals(self):
        stages, interval = self.run_visit()
        parsed = analyze.parse_stages(stages, interval)
        self.assertEqual(parsed['send'], [interval['lower_us'], interval['upper_us']])
        self.assertIsNotNone(parsed['initial'])
        self.assertEqual(parsed['hz'], os.sysconf('SC_CLK_TCK'))

    def test_missing_initial_is_not_synthesized(self):
        self.fixture.environment['MOCK_NO_INITIAL'] = '1'
        stages, interval = self.run_visit()
        self.assertIsNone(analyze.parse_stages(stages, interval)['initial'])

    def test_missing_optional_diagnostic_keeps_primary_but_marks_unavailable(self):
        self.fixture.environment['MOCK_NO_DIAG'] = '1'
        stages, interval = self.run_visit()
        self.assertEqual(stages, b'STAGES unavailable\n')
        self.assertIsNone(analyze.parse_stages(stages, interval))

    def test_unavailable_runtime_clock_rate_is_not_guessed(self):
        self.fixture.shim('getconf', 'exit 1')
        stages, interval = self.run_visit()
        self.assertEqual(stages, b'STAGES unavailable\n')
        self.assertIsNone(analyze.parse_stages(stages, interval))

    def test_duplicate_diagnostic_fails_closed_and_recovers(self):
        self.fixture.environment['MOCK_DUPLICATE_DIAG'] = '1'
        case = 'candidate-01'
        self.fixture.prepare(case)
        failure = self.fixture.command('visit', case, success=False)
        self.assertIn(b'refused submission_stages\n', failure.stderr)
        self.assertFalse((self.fixture.state / 'out' / case / 'qualified').exists())
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
