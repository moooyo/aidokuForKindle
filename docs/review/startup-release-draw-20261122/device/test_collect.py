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

import collect


HERE = Path(__file__).resolve().parent
PRIOR = Path(os.environ.get('DRAW_PRIOR_FIXTURE', HERE.parent.parent / 'startup-library-transition-20261122'))
JPEG = Path(os.environ.get('JPEG_FIXTURE_DIR', HERE.parent.parent / 'startup-jpeg-native-20261122'))
sys.path.insert(0, str(PRIOR))
spec = importlib.util.spec_from_file_location('closed_transition_fixture', PRIOR / 'test_device.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
previous = prior.previous

APP_ANCHOR = '''            values[6], values[7], values[8], values[9], values[10], render, settle, write, send);
}'''
APP_ADDITION = r'''            values[6], values[7], values[8], values[9], values[10], render, settle, write, send);
    if (!getenv("MOCK_DRAW_MISSING")) {
        int draw_sequence = getenv("MOCK_DRAW_SEQUENCE") ? sequence + 1 : sequence;
        int draw_marker = getenv("MOCK_DRAW_MARKER") ? marker + 1 : marker;
        long long draw_sent = getenv("MOCK_DRAW_STAMP") ? reported + 1 : reported;
        const char *draw_wall = getenv("MOCK_DRAW_WALL") ? "11" : "10";
        const char *cpu = getenv("MOCK_CPU_NONE") ? "none" : "15";
        const char *minor = getenv("MOCK_COUNTERS_NONE") || getenv("MOCK_COUNTER_PARTIAL") ? "none" : "0";
        const char *major = getenv("MOCK_COUNTERS_NONE") ? "none" : "2";
        const char *voluntary = getenv("MOCK_COUNTERS_NONE") ? "none" : "3";
        const char *involuntary = getenv("MOCK_COUNTERS_NONE") ? "none" : "4";
        const char *width = getenv("MOCK_BAD_GEOMETRY") ? "0" : "1860";
        const char *format = getenv("MOCK_BAD_FORMAT") ? "unknown" : "gray8";
        int repetitions = getenv("MOCK_DUPLICATE_DRAW") ? 2 : 1;
        for (int index = 0; index < repetitions; index++)
            fprintf(stderr, "aidoku: startup_draw_cpu sequence=%d marker=%d submitted_us=%lld "
                    "wall_us=%s thread_us=%s minflt=%s majflt=%s nvcsw=%s nivcsw=%s "
                    "width=%s height=2480 format=%s\n", draw_sequence, draw_marker, draw_sent,
                    draw_wall, cpu, minor, major, voluntary, involuntary, width, format);
    }
}'''


def app_source():
    if prior.APP.count(APP_ANCHOR) != 1:
        raise ValueError('unexpected_fixture_source')
    return prior.APP.replace(APP_ANCHOR, APP_ADDITION, 1)


class GeneratorTests(unittest.TestCase):
    def test_only_unique_frozen_anchors_change(self):
        for name, replacements, generate in (('device.sh', collect.DEVICE_REPLACEMENTS, collect.generate_device),
                                              ('submission.sh', collect.COLLECTOR_REPLACEMENTS, collect.generate_submission)):
            original = (PRIOR / name).read_text(encoding='ascii')
            expected = original
            for old, new in replacements:
                self.assertEqual(original.count(old), 1)
                expected = expected.replace(old, new, 1)
            self.assertEqual(generate(original), expected)
            for old, _ in replacements:
                for invalid in (original.replace(old, ''), original + old):
                    with self.assertRaisesRegex(ValueError, 'unexpected_frozen_source'):
                        generate(invalid)

    def test_generation_is_exclusive_and_valid_shell(self):
        with tempfile.TemporaryDirectory(prefix='draw-patch-') as directory:
            root = Path(directory)
            device, submission = root / 'device.sh', root / 'submission.sh'
            command = [sys.executable, '-B', str(HERE / 'collect.py'), str(PRIOR / 'device.sh'),
                       str(PRIOR / 'submission.sh'), str(device), str(submission)]
            subprocess.run(command, check=True, capture_output=True)
            for target in (device, submission):
                subprocess.run(['/bin/sh', '-n', str(target)], check=True)
                if os.environ.get('TEST_BUSYBOX'):
                    subprocess.run([os.environ['TEST_BUSYBOX'], 'ash', '-n', str(target)], check=True)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertNotEqual(subprocess.run(command[:-2] + [str(PRIOR / 'device.sh'), str(submission)],
                                               capture_output=True).returncode, 0)
            device.unlink()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertFalse(device.exists())

    def test_device_accepts_only_complete_bounded_canonical_drawaccount(self):
        with tempfile.TemporaryDirectory(prefix='draw-output-') as directory:
            root = Path(directory)
            stage, output = root / 'stage', root / 'out'
            (stage / 'submission').mkdir(parents=True)
            (output / 'candidate-01').mkdir(parents=True)
            collector = stage / 'submission/submission.sh'
            source = collect.generate_device((PRIOR / 'device.sh').read_text(encoding='ascii'))
            capture = source.split('capture_submission() {', 1)[1].split('\nrun_visit() {', 1)[0]
            shell = ('set -efu\n'
                     'stage=$1; output=$2; label=0123456789abcdef\n'
                     'number() { case "$1" in \'\'|*[!0-9]*) return 1;; esac; }\n'
                     'put() { (set -C; printf \'%s\\n\' "$2" >"$1"); }\n'
                     'capture_submission() {' + capture + '\n'
                     'capture_submission candidate-01\n')
            primary = 'SUBMISSION 100 110 3 4 large nonempty nonempty\n'
            stages = 'STAGES unavailable\n'
            transition = 'TRANSITION ' + ' '.join(['-1'] * 12 + ['10', '0', '0', '0']) + '\n'
            draw = 'DRAWACCOUNT 10 15 0 2 3 4 1860 2480 gray8\n'
            prefix = primary + stages + transition
            accepted = [prefix + draw, prefix + 'DRAWACCOUNT unavailable\n',
                        prefix + 'DRAWACCOUNT 10 none 0 2 3 4 1860 2480 rgb8\n',
                        prefix + 'DRAWACCOUNT 10 15 none none none none 8192 8192 gray8\n',
                        primary + stages + 'TRANSITION unavailable\nDRAWACCOUNT unavailable\n',
                        primary + stages + transition.replace(' 10 0 0 0\n', ' 0 0 0 0\n') +
                        'DRAWACCOUNT 0 0 0 0 0 0 1 1 gray8\n']
            rejected = [prefix, prefix + draw[:-1], prefix + draw + '\n', prefix + draw + 'private log\n',
                        prefix + draw.replace('DRAWACCOUNT ', 'DRAWACCOUNT  ', 1),
                        prefix + draw.replace('DRAWACCOUNT ', 'DRAWACCOUNT\t', 1),
                        prefix + ' ' + draw, prefix + draw.rstrip('\n') + ' \n',
                        prefix + draw.replace(' 10 15 ', ' 11 15 ', 1),
                        prefix + draw.replace(' 15 ', ' -1 ', 1),
                        prefix + draw.replace(' 15 ', ' 015 ', 1),
                        prefix + draw.replace(' 15 ', ' 1000000000000 ', 1),
                        prefix + draw.replace(' 0 2 3 4 ', ' none 2 3 4 ', 1),
                        prefix + draw.replace(' 0 2 3 4 ', ' 0 -2 3 4 ', 1),
                        prefix + draw.replace(' 1860 ', ' 0 ', 1),
                        prefix + draw.replace(' 1860 ', ' 8193 ', 1),
                        prefix + draw.replace('gray8', 'unknown'),
                        prefix + draw.replace('gray8', 'gray8 extra'),
                        primary + stages + 'TRANSITION unavailable\n' + draw,
                        primary + stages + transition.replace(' 10 0 0 0\n', ' none none none none\n') + draw]
            interpreters = [('/bin/sh',)]
            if os.environ.get('TEST_BUSYBOX'):
                interpreters.append((os.environ['TEST_BUSYBOX'], 'ash'))
            for interpreter in interpreters:
                for raw in accepted + rejected:
                    with self.subTest(interpreter=interpreter, raw=raw):
                        paths = [output / 'candidate-01' / name for name in ('stages', 'transition', 'drawaccount')]
                        for path in paths:
                            path.unlink(missing_ok=True)
                        collector.write_text('#!/bin/sh\nprintf \'%s\' ' + shlex.quote(raw) + '\n', encoding='ascii')
                        run = subprocess.run([*interpreter, '-c', shell, 'capture', str(stage), str(output)],
                                             capture_output=True)
                        self.assertEqual(run.returncode == 0, raw in accepted, (run.stdout, run.stderr))
                        if raw in accepted:
                            first, second, third, fourth = raw.splitlines(keepends=True)
                            self.assertEqual(run.stdout, first.encode())
                            self.assertEqual([path.read_bytes() for path in paths],
                                             [second.encode(), third.encode(), fourth.encode()])
                        else:
                            self.assertEqual(run.stdout, b'')
                            self.assertNotIn(b'private log', run.stderr)
                            self.assertTrue(all(not path.exists() for path in paths))


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix='draw-parser-'))
        self.evidence = Path(self.temporary)
        source = collect.generate_submission((PRIOR / 'submission.sh').read_text(encoding='ascii'))
        function = source.split('\nparse_drawaccount() {', 1)[1].split('\ndrawaccount_output() (', 1)[0]
        self.shell = 'set -efu\nevidence=$1\nparse_drawaccount() {' + function + '\nparse_drawaccount 3900000 3 5 "$2"\n'
        self.base = ('aidoku: startup_draw_cpu sequence=3 marker=5 submitted_us=3900000 '
                     'wall_us=10 thread_us=15 minflt=0 majflt=2 nvcsw=3 nivcsw=4 '
                     'width=1860 height=2480 format=gray8\n')
        self.prefix = ('aidoku: startup_library_submitted example\n'
                       'aidoku: startup_transition example\n')
        self.suffix = 'aidoku: Launcher worker exited with status 0.\n'

    def run_parser(self, text, wall='10'):
        (self.evidence / 'delta.pending').write_text(text, encoding='ascii')
        interpreters = [('/bin/sh',)]
        if os.environ.get('TEST_BUSYBOX'):
            interpreters.append((os.environ['TEST_BUSYBOX'], 'ash'))
        return [subprocess.run([*interpreter, '-c', self.shell, 'parse', str(self.evidence), wall],
                               capture_output=True) for interpreter in interpreters]

    def test_full_partial_zero_and_cpu_above_wall(self):
        cases = ((self.base, b'DRAWACCOUNT 10 15 0 2 3 4 1860 2480 gray8\n'),
                 (self.base.replace('thread_us=15', 'thread_us=none'),
                  b'DRAWACCOUNT 10 none 0 2 3 4 1860 2480 gray8\n'),
                 (self.base.replace('minflt=0 majflt=2 nvcsw=3 nivcsw=4',
                                    'minflt=none majflt=none nvcsw=none nivcsw=none'),
                  b'DRAWACCOUNT 10 15 none none none none 1860 2480 gray8\n'),
                 (self.base.replace('thread_us=15', 'thread_us=0').replace('format=gray8', 'format=rgb8'),
                  b'DRAWACCOUNT 10 0 0 2 3 4 1860 2480 rgb8\n'))
        for raw, expected in cases:
            for result in self.run_parser(self.prefix + raw + self.suffix):
                self.assertEqual((result.returncode, result.stdout), (0, expected), result.stderr)

    def test_exact_producer_example_preserves_valid_zero(self):
        raw = ('aidoku: startup_draw_cpu sequence=4 marker=5 submitted_us=7 wall_us=0 '
               'thread_us=0 minflt=none majflt=none nvcsw=none nivcsw=none '
               'width=1860 height=2480 format=gray8\n')
        (self.evidence / 'delta.pending').write_text(self.prefix + raw + self.suffix, encoding='ascii')
        shell = self.shell.replace('parse_drawaccount 3900000 3 5 "$2"', 'parse_drawaccount 7 4 5 "$2"')
        interpreters = [('/bin/sh',)]
        if os.environ.get('TEST_BUSYBOX'):
            interpreters.append((os.environ['TEST_BUSYBOX'], 'ash'))
        for interpreter in interpreters:
            result = subprocess.run([*interpreter, '-c', shell, 'parse', str(self.evidence), '0'],
                                    capture_output=True)
            self.assertEqual((result.returncode, result.stdout),
                             (0, b'DRAWACCOUNT 0 0 none none none none 1860 2480 gray8\n'), result.stderr)

    def test_missing_is_unavailable_but_invalid_and_wrong_frame_are_rejected(self):
        for result in self.run_parser(self.prefix + self.suffix):
            self.assertEqual((result.returncode, result.stdout), (3, b''))
        invalid = [self.base + self.base, self.base.replace('sequence=3', 'sequence=4'),
                   self.base.replace('marker=5', 'marker=6'),
                   self.base.replace('submitted_us=3900000', 'submitted_us=3900001'),
                   self.base.replace('wall_us=10', 'wall_us=11'),
                   self.base.replace('thread_us=15', 'thread_us=-1'),
                   self.base.replace('thread_us=15', 'thread_us=015'),
                   self.base.replace('thread_us=15', 'thread_us=1000000000000'),
                   self.base.replace('minflt=0', 'minflt=none'),
                   self.base.replace('majflt=2', 'majflt=-2'),
                   self.base.replace('nvcsw=3', 'nvcsw=03'),
                   self.base.replace('width=1860', 'width=0'),
                   self.base.replace('height=2480', 'height=8193'),
                   self.base.replace('format=gray8', 'format=unknown'),
                   self.base.replace('sequence=3 ', 'sequence=3  ', 1),
                   self.base.rstrip('\n') + ' extra\n']
        texts = [self.prefix + raw + self.suffix for raw in invalid]
        texts += [self.base + self.prefix + self.suffix,
                  self.prefix + self.suffix + self.base,
                  'aidoku: startup_library_submitted example\n' + self.base +
                  'aidoku: startup_transition example\n' + self.suffix]
        for text in texts:
            with self.subTest(text=text):
                for result in self.run_parser(text):
                    self.assertEqual((result.returncode, result.stdout), (2, b''), result.stderr)
        for result in self.run_parser(self.prefix + self.base + self.suffix, wall='none'):
            self.assertEqual((result.returncode, result.stdout), (2, b''))

    def test_missing_transition_keeps_primary_without_hiding_bad_identity(self):
        shell = ('set -efu\nevidence=$1\n'
                 'parse_stages() { return 3; }\nparse_transition() { return 3; }\n' +
                 collect.DRAW_FUNCTIONS + '\nevent="3900000 3 5 large nonempty nonempty"\n'
                 'transition="TRANSITION unavailable"\ndrawaccount_output\n')
        prefix = 'aidoku: startup_library_submitted example\n'
        cases = ((prefix + self.base + self.suffix, 0, b'DRAWACCOUNT unavailable\n'),
                 (prefix + self.suffix, 0, b'DRAWACCOUNT unavailable\n'),
                 (prefix + self.base.replace('sequence=3', 'sequence=4') + self.suffix, 1, b''))
        interpreters = [('/bin/sh',)]
        if os.environ.get('TEST_BUSYBOX'):
            interpreters.append((os.environ['TEST_BUSYBOX'], 'ash'))
        for text, expected_code, expected_output in cases:
            (self.evidence / 'delta.pending').write_text(text, encoding='ascii')
            for interpreter in interpreters:
                result = subprocess.run([*interpreter, '-c', shell, 'draw', str(self.evidence)], capture_output=True)
                self.assertEqual((result.returncode, result.stdout), (expected_code, expected_output), result.stderr)


class DrawAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        outer = os.environ.get('AIDOKU_TEST_OUTER_MNT')
        if not outer or os.readlink('/proc/self/ns/mnt') == outer:
            raise unittest.SkipTest('Require a fresh private mount namespace on test-env')
        cls.temporary = tempfile.TemporaryDirectory(prefix='draw-scripts-')
        cls.scripts = Path(cls.temporary.name)
        (cls.scripts / 'device.sh').write_text(
            collect.generate_device((PRIOR / 'device.sh').read_text(encoding='ascii')), encoding='ascii')
        (cls.scripts / 'submission.sh').write_text(
            collect.generate_submission((PRIOR / 'submission.sh').read_text(encoding='ascii')), encoding='ascii')
        shutil.copyfile(JPEG / 'selector.sh', cls.scripts / 'selector.sh')
        cls.old_here, cls.old_selector_here, cls.old_app = previous.HERE, previous.test_selector.HERE, previous.APP
        previous.HERE = cls.scripts
        previous.test_selector.HERE = cls.scripts
        previous.APP = app_source()

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

    def visit(self, fixture, case='candidate-01'):
        self.assertEqual(fixture.command('admit').stdout, b'jpeg_admitted\n')
        self.assertEqual(fixture.prepare(case).stdout, b'jpeg_prepared\n')
        self.assertEqual(fixture.command('visit', case).stdout, b'jpeg_completed\n')
        (fixture.state / 'out' / case / 'private-log').write_text('must not be exported\n', encoding='ascii')
        archive = fixture.command('collect', case).stdout
        self.last_archive = archive
        fixture.finish()
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as stream:
            records = {item.name: stream.extractfile(item).read() for item in stream}
        prefix = 'out/' + case + '/'
        self.assertEqual(set(records), {prefix + name for name in ('origin', 'anchor', 'pointer-brackets',
                            'qualified', 'submission', 'stages', 'transition', 'drawaccount')})
        self.assertEqual(records[prefix + 'anchor'], records[prefix + 'pointer-brackets'])
        published = b''.join(records.values())
        self.assertNotIn(b'private fixture log', published)
        self.assertNotIn(b'must not be exported', published)
        self.assertNotIn(b'startup_draw_cpu', published)
        self.assertRegex(records[prefix + 'submission'], rb'^SUBMISSION [0-9]+ [0-9]+ 1 1 large nonempty nonempty\n$')
        return records[prefix + 'transition'], records[prefix + 'drawaccount']

    def test_real_mount_and_process_chain_bind_same_visit_wall_and_cpu(self):
        transition, draw = self.visit(self.fixture)
        self.assertEqual(transition.split()[13], b'10')
        self.assertEqual(draw, b'DRAWACCOUNT 10 15 0 2 3 4 1860 2480 gray8\n')

    def test_device_archive_passes_the_host_projection(self):
        path = os.environ.get('DRAW_HOST_FIXTURE')
        if not path:
            self.skipTest('Set DRAW_HOST_FIXTURE to the prepared host operator')
        self.visit(self.fixture)
        spec = importlib.util.spec_from_file_location('draw_host_fixture', Path(path))
        host = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = host
        spec.loader.exec_module(host)
        interval, draw = host.extract_record(self.last_archive, 'profile-01')
        self.assertGreater(interval['upper_us'], 0)
        self.assertEqual((draw['wall_us'], draw['cpu_us'], draw['width'], draw['height'], draw['format']),
                         (10, 15, 1860, 2480, 'gray8'))

    def test_missing_and_partial_counters_preserve_independent_data(self):
        cases = (('MOCK_DRAW_MISSING', b'DRAWACCOUNT unavailable\n'),
                 ('MOCK_CPU_NONE', b'DRAWACCOUNT 10 none 0 2 3 4 1860 2480 gray8\n'),
                 ('MOCK_COUNTERS_NONE', b'DRAWACCOUNT 10 15 none none none none 1860 2480 gray8\n'))
        for mode, expected in cases:
            with self.subTest(mode=mode):
                fixture = previous.DeviceIntegration('runTest')
                fixture.setUp()
                try:
                    fixture.environment[mode] = '1'
                    _, draw = self.visit(fixture)
                    self.assertEqual(draw, expected)
                finally:
                    fixture.tearDown()
                    fixture.doCleanups()

    def test_prior_visit_draw_record_before_log_boundary_is_not_reused(self):
        self.fixture.log.write_text(
            'aidoku: startup_draw_cpu sequence=1 marker=1 submitted_us=1 wall_us=10 '
            'thread_us=15 minflt=0 majflt=2 nvcsw=3 nivcsw=4 width=1860 height=2480 format=gray8\n',
            encoding='ascii')
        self.fixture.environment['MOCK_DRAW_MISSING'] = '1'
        _, draw = self.visit(self.fixture)
        self.assertEqual(draw, b'DRAWACCOUNT unavailable\n')

    def test_unavailable_prior_diagnostic_does_not_invent_wall(self):
        self.fixture.environment['MOCK_NO_DIAG'] = '1'
        transition, draw = self.visit(self.fixture)
        self.assertEqual(transition, b'TRANSITION unavailable\n')
        self.assertEqual(draw, b'DRAWACCOUNT unavailable\n')

    def test_identity_wall_duplicate_and_bad_counter_are_rejected_and_restored(self):
        modes = ('MOCK_DRAW_SEQUENCE', 'MOCK_DRAW_MARKER', 'MOCK_DRAW_STAMP', 'MOCK_DRAW_WALL',
                 'MOCK_DUPLICATE_DRAW', 'MOCK_COUNTER_PARTIAL', 'MOCK_BAD_GEOMETRY', 'MOCK_BAD_FORMAT')
        for mode in modes:
            with self.subTest(mode=mode):
                fixture = previous.DeviceIntegration('runTest')
                fixture.setUp()
                try:
                    fixture.environment[mode] = '1'
                    if mode == 'MOCK_DRAW_WALL':
                        fixture.environment['MOCK_NO_DIAG'] = '1'
                    fixture.prepare('candidate-01')
                    failed = fixture.command('visit', 'candidate-01', success=False)
                    self.assertIn(b'refused submission_drawaccount\n', failed.stderr)
                    self.assertNotIn(b'private fixture log', failed.stderr)
                    self.assertFalse((fixture.state / 'out/candidate-01/qualified').exists())
                    fixture.finish()
                finally:
                    fixture.tearDown()
                    fixture.doCleanups()

    def test_missing_account_file_cannot_be_collected(self):
        self.fixture.prepare('candidate-01')
        self.fixture.command('visit', 'candidate-01')
        (self.fixture.state / 'out/candidate-01/drawaccount').unlink()
        result = self.fixture.command('collect', 'candidate-01', success=False)
        self.assertEqual(result.stdout, b'')
        self.fixture.finish()

    def test_interrupted_real_bind_and_screen_saver_recover_without_dispatch(self):
        self.fixture.environment['BREAK_AFTER'] = '1'
        self.fixture.prepare('candidate-01', success=False)
        self.assertTrue(previous.test_selector.mount_rows(self.fixture.home / 'app/aidoku'))
        self.fixture.finish()
        other = previous.DeviceIntegration('runTest')
        other.setUp()
        try:
            (other.root / 'asleep').touch()
            other.command('admit', success=False)
            other.prepare('candidate-01', success=False)
            self.assertFalse(other.stage.exists() or other.state.exists())
            self.assertFalse((other.root / 'input-events').exists())
        finally:
            other.tearDown()
            other.doCleanups()


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
