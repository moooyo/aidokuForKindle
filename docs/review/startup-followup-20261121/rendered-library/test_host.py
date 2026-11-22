import copy
from contextlib import redirect_stdout
from dataclasses import dataclass
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import host


OWNER = '00000000-0000-0000-0000-000000000001'
LABEL = '0123456789abcdef'


class LeaseError(Exception):
    def __init__(self, code, details):
        self.code, self.details = code, details


@dataclass(frozen=True)
class Request:
    owner_thread: str = OWNER
    run_id: str = host.RUN_PREFIX + LABEL


class FakeLease:
    LeaseError = LeaseError

    def __init__(self):
        self.calls = []
        self.held = False
        self.acquire_error = None
        self.bad_release = False
        self.identity = {'project': 'Aidoku', 'owner_thread': OWNER, 'run_id': host.RUN_PREFIX + LABEL,
                         'lease_token_sha256': 'fixture-private-identity'}

    def acquire(self, request):
        self.calls.append('acquire')
        if self.acquire_error is not None:
            raise self.acquire_error
        self.held = True
        return dict(self.identity, ok=True, operation='acquire', state='held')

    def status(self, request):
        self.calls.append('status')
        return dict(self.identity, ok=True, operation='status', state='held' if self.held else 'absent',
                    ownership_verified=self.held)

    def release(self, request, *, native_restored_and_connections_closed):
        assert self.calls[-1] == 'status' and self.held and native_restored_and_connections_closed
        self.calls.append('release')
        self.held = False
        return dict(self.identity, ok=True, operation='release', state='released', owner_and_token_checked=True,
                    native_restored_and_connections_closed=True,
                    lease_directory_absent_after_release=not self.bad_release)


def members(case, *, anchor=b'1000000 1000010 1050020 1050030\n',
            rendered=b'ARL1 1100000 1220000 1460000 DIFFER MATCH\n'):
    return {'origin': b'stock-tap\n', 'anchor': anchor, 'pointer-brackets': anchor,
            'qualified': (case + ' stock-tap\n').encode(), 'rendered-library': rendered}


def archive(case, values=None, *, extras=()):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz', format=tarfile.USTAR_FORMAT) as stream:
        for name, value in (values or members(case)).items():
            info = tarfile.TarInfo('out/' + case + '/' + name)
            info.size = len(value)
            stream.addfile(info, io.BytesIO(value))
        for name, value in extras:
            info = tarfile.TarInfo(name)
            info.size = len(value)
            stream.addfile(info, io.BytesIO(value))
    return output.getvalue()


class FakeRemote:
    def __init__(self, lease):
        self.lease = lease
        self.calls = []
        self.faults = {}
        self.after = None

    def __call__(self, operation):
        assert self.lease.held and self.lease.calls[-1] == 'status'
        self.calls.append(operation)
        if self.after:
            self.after(operation)
        fault = self.faults.get(operation)
        if isinstance(fault, BaseException):
            raise fault
        if fault is not None:
            return fault
        kind, _, case = operation.partition(':')
        output = archive(case) if kind == 'collect' else host.REPLIES[kind]
        return 0, output, b'fixture-sensitive-output'


class FakeCheckpoint:
    def __init__(self):
        self.saved = None
        self.fail_when = None

    def __call__(self, value):
        if self.fail_when and self.fail_when(value):
            raise OSError('fixture-sensitive-output')
        self.saved = copy.deepcopy(value)


class EvidenceTests(unittest.TestCase):
    def test_exact_five_text_members_and_release_math(self):
        values = host.archive_members(archive('control-01'), 'control-01')
        self.assertEqual(values, members('control-01'))
        result = host.measured_case('control-01', values)
        self.assertEqual(result['lower_bound_us'], 49970)
        self.assertEqual(result['upper_bound_us'], 169980)
        self.assertEqual((result['cover_first_vs_reference'], result['cover_confirmed_vs_reference']),
                         ('DIFFER', 'MATCH'))

    def test_pre_release_negative_keeps_upper_without_inventing_lower(self):
        values = members('control-01', rendered=b'ARL1 1049000 1220000 1460000 MATCH MATCH\n')
        result = host.measured_case('control-01', values)
        self.assertIsNone(result['lower_bound_us'])
        self.assertIsNone(result['lower_bound_ms'])
        self.assertEqual(result['upper_bound_us'], 169980)

    def test_observation_requires_ordered_brackets_and_confirmation(self):
        for anchor, rendered in (
            (b'1000000 1000010 1050020 1050030\n', b'ARL1 1000020 1049000 1300000 MATCH MATCH\n'),
            (b'1000000 1000010 1050020 1050030\n', b'ARL1 1100000 1220000 1419999 MATCH MATCH\n'),
            (b'1000000 1050000 1050000 1050010\n', b'ARL1 1100000 1220000 1460000 MATCH MATCH\n'),
            (b'1000000 1000010 1050020 1050030\n', b'ARL1 1220000 1220000 1460000 MATCH MATCH\n'),
            (b'1000000 1100001 1150001 1150010\n', b'ARL1 1170000 1220000 1460000 MATCH MATCH\n'),
            (b'1000000 1000010 1050020 1150021\n', b'ARL1 1170000 1220000 1460000 MATCH MATCH\n'),
            (b'1000000 1000010 1050009 1050010\n', b'ARL1 1100000 1220000 1460000 MATCH MATCH\n'),
        ):
            with self.subTest(anchor=anchor, rendered=rendered):
                with self.assertRaisesRegex(host.common.Stop, 'invalid_observation_brackets'):
                    host.measured_case('control-01', members('control-01', anchor=anchor, rendered=rendered))

    def test_rejects_noncanonical_text_and_cross_case_anchors(self):
        original = members('control-01')
        for name, value in (('origin', b'indexed-uri\n'), ('qualified', b'control-02 stock-tap\n'),
                            ('pointer-brackets', b'1 2 3 4\n'), ('rendered-library', b'ARL1 1 2 3 1 1\n'),
                            ('anchor', b'99999999999999999999 2 3 4\n')):
            with self.subTest(name=name):
                changed = dict(original, **{name: value})
                if name == 'anchor':
                    changed['pointer-brackets'] = value
                with self.assertRaises(host.common.Stop):
                    host.archive_members(archive('control-01', changed), 'control-01')

    def test_rejects_images_duplicates_symlinks_and_cross_case_members(self):
        for name, content in (('out/control-01/stock.frame', b'fixture-image'),
                              ('out/control-01/trace', b'fixture-trace'),
                              ('out/control-01/origin', b'stock-tap\n'),
                              ('out/control-02/rendered-library', b'fixture'),
                              ('out/control-01/../secret', b'fixture')):
            with self.subTest(name=name):
                with self.assertRaises(host.common.Stop):
                    host.archive_members(archive('control-01', extras=[(name, content)]), 'control-01')
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as stream:
            info = tarfile.TarInfo('out/control-01/origin')
            info.type, info.linkname = tarfile.SYMTYPE, '/fixture'
            stream.addfile(info)
        with self.assertRaises(host.common.Stop):
            host.archive_members(output.getvalue(), 'control-01')

    def test_rejects_pax_extensions_trailing_data_and_gzip_bomb(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz', format=tarfile.PAX_FORMAT) as stream:
            info = tarfile.TarInfo('out/control-01/origin')
            info.pax_headers = {'comment': 'not permitted'}
            info.size = len(b'stock-tap\n')
            stream.addfile(info, io.BytesIO(b'stock-tap\n'))
        for data in (output.getvalue(), archive('control-01') + b'extra',
                     gzip.compress(b'\0' * (host.MAX_TAR + 1)), gzip.compress(b'not tar')):
            with self.subTest(size=len(data)):
                with self.assertRaises(host.common.Stop):
                    host.archive_members(data, 'control-01')

    def test_rejects_nonzero_padding_or_short_archive_terminator(self):
        raw = bytearray(gzip.decompress(archive('control-01')))
        raw[512 + len(b'stock-tap\n')] = ord('X')
        with self.assertRaisesRegex(host.common.Stop, 'invalid_archive'):
            host.archive_members(gzip.compress(raw), 'control-01')
        raw = gzip.decompress(archive('control-01'))
        with self.assertRaisesRegex(host.common.Stop, 'invalid_archive'):
            host.archive_members(gzip.compress(raw[:5 * 1024 + 512]), 'control-01')
        with self.assertRaisesRegex(host.common.Stop, 'invalid_archive'):
            host.archive_members(gzip.compress(raw[:-1]), 'control-01')

    def test_missing_and_oversized_members_are_rejected(self):
        values = members('control-01')
        values.pop('origin')
        with self.assertRaises(host.common.Stop):
            host.archive_members(archive('control-01', values), 'control-01')
        values = members('control-01')
        values['rendered-library'] = b'X' * 257
        with self.assertRaises(host.common.Stop):
            host.archive_members(archive('control-01', values), 'control-01')

    def test_device_style_tar_stream_is_accepted(self):
        with tempfile.TemporaryDirectory(prefix='render-host-tar-') as directory:
            root = Path(directory)
            folder = root / 'out/control-01'
            folder.mkdir(parents=True)
            for name, value in members('control-01').items():
                (folder / name).write_bytes(value)
            paths = ['out/control-01/' + name for name in sorted(host.MEMBERS)]
            tar = shutil.which('tar') or '/opt/kindle-toolchain/ubuntu/usr/bin/tar'
            data = subprocess.check_output([tar, '-czf', '-', '-C', str(root), *paths])
            self.assertEqual(host.archive_members(data, 'control-01'), members('control-01'))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.lease = FakeLease()
        self.remote = FakeRemote(self.lease)
        self.checkpoint = FakeCheckpoint()
        self.saved = []

    def run_batch(self):
        return host.operate(lease=self.lease, request=Request(), remote=self.remote, save=self.checkpoint,
                            save_evidence=lambda case, value: self.saved.append((case, value)))

    def test_three_visits_collect_immediately_and_release_after_absence(self):
        result = self.run_batch()
        self.assertEqual(self.remote.calls, ['guard', 'admit', 'prepare', 'visit:control-01', 'collect:control-01',
                         'visit:control-02', 'collect:control-02', 'visit:control-03', 'collect:control-03',
                         'close', 'absent'])
        self.assertEqual([case for case, _ in self.saved], list(host.CASES))
        self.assertEqual(result['completed'], list(host.CASES))
        self.assertTrue(result['operations_passed'])
        self.assertTrue(result['released'])
        self.assertFalse(result['recovery_required'])
        self.assertEqual(self.lease.calls.count('release'), 1)
        self.assertEqual(result, self.checkpoint.saved)
        self.assertNotIn('fixture-sensitive-output', str(result))

    def test_second_or_third_failure_preserves_prior_cases_without_retry(self):
        for operation, expected in (('visit:control-02', ['control-01']),
                                    ('collect:control-02', ['control-01']),
                                    ('visit:control-03', ['control-01', 'control-02'])):
            for fault in (TimeoutError(), KeyboardInterrupt(), (1, b'', b'private')):
                with self.subTest(operation=operation, fault=type(fault).__name__):
                    self.setUp()
                    self.remote.faults[operation] = fault
                    report = self.run_batch()
                    self.assertEqual(report['completed'], expected)
                    self.assertEqual([case for case, _ in self.saved], expected)
                    self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])
                    self.assertEqual(self.remote.calls.count(operation), 1)
                    self.assertTrue(report['released'])
                    self.assertFalse(report['operations_passed'])

    def test_invalid_archive_saves_no_raw_bytes_and_closes(self):
        self.remote.faults['collect:control-02'] = (0, archive('control-02',
             extras=[('out/control-02/stock.frame', b'fixture-image')]), b'')
        result = self.run_batch()
        self.assertEqual(result['completed'], ['control-01'])
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(result['error'], 'unexpected_evidence_member')
        self.assertTrue(result['released'])
        self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])

    def test_partial_acquire_or_busy_never_calls_device_or_releases(self):
        for code, details, recovery in (
            ('lease_exists', {'lease_directory_created': False, 'private_token_created': False,
                              'owner_record_created': False}, False),
            ('operation_busy', {'lease_directory_created': False, 'private_token_created': False,
                                'owner_record_created': False}, False),
            ('filesystem_error', {'lease_directory_created': True, 'private_token_created': True,
                                  'owner_record_created': False}, True),
        ):
            with self.subTest(code=code):
                self.setUp()
                self.lease.acquire_error = LeaseError(code, details)
                result = self.run_batch()
                self.assertEqual(result['recovery_required'], recovery)
                self.assertFalse(result['released'])
                self.assertEqual(self.remote.calls, [])
                self.assertNotIn('release', self.lease.calls)

    def test_admission_failure_has_no_mutation_and_releases(self):
        for operation in ('guard', 'admit'):
            with self.subTest(operation=operation):
                self.setUp()
                self.remote.faults[operation] = (1, b'', b'private')
                result = self.run_batch()
                self.assertTrue(result['released'])
                self.assertFalse(result['mutation_possible'])
                self.assertNotIn('prepare', self.remote.calls)
                self.assertNotIn('close', self.remote.calls)

    def test_uncertain_prepare_always_attempts_close_and_absence(self):
        for fault in (TimeoutError(), KeyboardInterrupt(), (1, b'', b'private')):
            with self.subTest(fault=type(fault).__name__):
                self.setUp()
                self.remote.faults['prepare'] = fault
                result = self.run_batch()
                self.assertTrue(result['mutation_possible'])
                self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])
                self.assertTrue(result['released'])
                self.assertFalse(result['operations_passed'])

    def test_close_or_absence_failure_retains_owned_lease(self):
        for operation in ('close', 'absent'):
            with self.subTest(operation=operation):
                self.setUp()
                self.remote.faults[operation] = (1, b'', b'')
                result = self.run_batch()
                self.assertFalse(result['released'])
                self.assertTrue(result['recovery_required'])
                self.assertNotIn('release', self.lease.calls)

    def test_unknown_absence_cannot_release_and_recovery_only_retries_absence(self):
        self.remote.faults['absent'] = TimeoutError()
        result = self.run_batch()
        self.assertTrue(result['close_confirmed'])
        self.assertFalse(result['released'])
        self.assertNotIn('release', self.lease.calls)
        self.remote.faults.clear()
        recovered = host.common.recover(lease=self.lease, request=Request(), remote=self.remote, report=result,
                                        save=self.checkpoint)
        self.assertTrue(recovered['released'])
        self.assertEqual(self.remote.calls.count('close'), 1)
        self.assertEqual(self.remote.calls.count('absent'), 2)

    def test_unreaped_transport_never_starts_another_device_command(self):
        for operation in ('visit:control-02', 'close', 'absent'):
            with self.subTest(operation=operation):
                self.setUp()
                self.remote.faults[operation] = host.common.TransportUnreaped('fixture')
                result = self.run_batch()
                self.assertEqual(self.remote.calls[-1], operation)
                self.assertTrue(result['transport_unreaped'])
                self.assertFalse(result['released'])
                self.assertNotIn('release', self.lease.calls)
                with self.assertRaisesRegex(host.common.Stop, 'manual_reconciliation_required'):
                    host.common.recover(lease=self.lease, request=Request(), remote=self.remote, report=result,
                                        save=self.checkpoint)

    def test_ownership_loss_never_closes_or_releases_foreign_lease(self):
        self.remote.after = lambda operation: setattr(self.lease, 'held', False) if operation == 'visit:control-02' else None
        result = self.run_batch()
        self.assertEqual(result['completed'], ['control-01'])
        self.assertFalse(result['released'])
        self.assertNotIn('close', self.remote.calls)
        self.assertNotIn('release', self.lease.calls)

    def test_checkpoint_failure_after_mutation_attempts_close_but_not_release(self):
        self.checkpoint.fail_when = lambda report: any(item['operation'] == 'prepare' for item in report['operations'])
        result = self.run_batch()
        self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])
        self.assertNotIn('release', self.lease.calls)
        self.assertEqual(result['error'], 'checkpoint_failed')

    def test_initial_checkpoint_failure_never_acquires(self):
        self.checkpoint.fail_when = lambda report: True
        result = self.run_batch()
        self.assertNotIn('acquire', self.lease.calls)
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(result['error'], 'checkpoint_failed')

    def test_failed_release_never_reports_success(self):
        self.lease.bad_release = True
        result = self.run_batch()
        self.assertFalse(result['released'])
        self.assertFalse(result['operations_passed'])
        self.assertEqual(result['release_error'], 'release_unverified')

    def test_transport_error_text_never_enters_report(self):
        self.remote.faults['visit:control-02'] = RuntimeError('fixture-secret-private-path')
        result = self.run_batch()
        self.assertNotIn('fixture-secret-private-path', str(result))
        self.assertEqual(result['error'], 'visit:control-02_transport_error')

    def test_watch_failures_keep_only_safe_short_codes(self):
        for category in ('start', 'initial', 'timeout', 'scan', 'rule_layout', 'rule', 'runtime'):
            with self.subTest(category=category):
                self.setUp()
                tag = 'render_watch_' + category
                self.remote.faults['visit:control-02'] = (1, b'',
                    ('private sensitive details\nrefused ' + tag + '\n').encode())
                result = self.run_batch()
                item = next(item for item in result['operations'] if item['operation'] == 'visit:control-02')
                self.assertEqual(item['refusal'], tag)
                self.assertNotIn('private sensitive details', str(result))
                self.assertTrue(result['released'])


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix='render-host-analysis-'))
        self.work = Path(self.temporary)
        self.attempt = self.work / 'attempt'
        self.attempt.mkdir(mode=0o700)
        self.lease = FakeLease()
        self.remote = FakeRemote(self.lease)
        self.plan = {'owner': OWNER, 'label': LABEL}

    def operate(self):
        return host.operate(lease=self.lease, request=Request(), remote=self.remote,
                            save=lambda value: host.common.checkpoint(self.attempt / 'operations.json', value),
                            save_evidence=lambda case, value: host.save_case(self.attempt, case, value))

    def analyze(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = host.analyze(self.work, self.plan)
        return code, json.loads(output.getvalue())

    def test_complete_batch_only_reports_median_after_release(self):
        report = self.operate()
        self.assertTrue(report['released'])
        code, result = self.analyze()
        self.assertEqual(code, 0)
        self.assertEqual(result['median_upper_bound_us'], 169980)
        self.assertTrue(result['complete_batch'])
        self.assertEqual(len(result['cases']), 3)
        self.assertTrue(all(path.suffix == '.json' for path in self.attempt.iterdir()))
        self.assertNotIn('fixture-sensitive-output', str(result))
        self.assertEqual(self.analyze(), (code, result))

    def test_partial_batch_keeps_only_previously_completed_cases_and_no_median(self):
        self.remote.faults['visit:control-03'] = (1, b'', b'refused native_tile\n')
        self.operate()
        code, result = self.analyze()
        self.assertEqual(code, 0)
        self.assertEqual(result['completed_cases'], ['control-01', 'control-02'])
        self.assertFalse(result['complete_batch'])
        self.assertIsNone(result['median_upper_bound_us'])
        self.assertEqual(result['stop_reason'], 'visit:control-03_failed')
        self.assertEqual(result['refusal'], 'native_tile')

    def test_status_is_host_only_and_shows_safe_partial_reason(self):
        self.remote.faults['visit:control-02'] = (1, b'', b'private details\nrefused render_watch_initial\n')
        self.operate()
        status = host.status_report(self.work)
        self.assertEqual(status['completed_cases'], ['control-01'])
        self.assertEqual(status['refusal'], 'render_watch_initial')
        self.assertEqual(status['error'], 'visit:control-02_failed')
        self.assertNotIn('private details', str(status))

    def test_analysis_rejects_before_release_or_unverified_close(self):
        self.remote.faults['absent'] = (1, b'', b'')
        self.operate()
        with self.assertRaisesRegex(host.common.Stop, 'unverified_visit_or_release'):
            self.analyze()

    def test_invalid_sample_never_invents_a_median_or_discards_valid_cases(self):
        self.operate()
        path = self.attempt / 'control-02.json'
        record = json.loads(path.read_text())
        record['members']['rendered-library'] = 'ARL1 1000020 1049000 1300000 MATCH MATCH\n'
        path.write_text(json.dumps(record))
        code, result = self.analyze()
        self.assertEqual(code, 0)
        self.assertFalse(result['complete_batch'])
        self.assertIsNone(result['median_upper_bound_us'])
        self.assertEqual(result['cases'][1]['measurement'], 'not_observed')

    def test_qualified_case_with_missing_success_operation_is_rejected(self):
        self.operate()
        path = self.attempt / 'operations.json'
        record = json.loads(path.read_text())
        next(item for item in record['operations'] if item['operation'] == 'visit:control-01')['outcome'] = 'failed'
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(host.common.Stop, 'unverified_visit_or_release'):
            self.analyze()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix='render-host-transport-'))
        self.root = Path(self.temporary)
        (self.root / 'device.sh').write_bytes(b'device-script\n')
        (self.root / 'guard.sh').write_bytes(b'guard-script\n')
        (self.root / 'payload.tar.gz').write_bytes(b'simulated-payload')
        self.secret = bytearray(b'fixture-password')

    def tearDown(self):
        self.secret[:] = b'\0' * len(self.secret)

    def fake(self, source):
        path = self.root / 'fake_ssh.py'
        path.write_text(source, encoding='utf-8')
        return path

    def test_commands_use_real_device_cli_case_names_and_one_shot_secret(self):
        fake = self.fake("""import os, shlex, subprocess, sys
expected = b'fixture-password'
if any(expected in value.encode() for value in os.environ.values()): sys.exit(10)
if any(expected in value.encode() for value in sys.argv): sys.exit(11)
if subprocess.check_output([os.environ['SSH_ASKPASS']]) != expected + b'\\n': sys.exit(12)
if subprocess.run([os.environ['SSH_ASKPASS']], stdout=subprocess.DEVNULL).returncode == 0: sys.exit(13)
parts = shlex.split(sys.argv[-1])
if parts[:3] != ['exec', 'sh', '-c'] or parts[4] != 'sh': sys.exit(14)
operation, label, *rest = parts[5:]
if label != '0123456789abcdef': sys.exit(15)
body = sys.stdin.buffer.read()
if body == b'guard-script\\n' and operation == 'admit' and not rest: output = b'guard_admitted\\n'
elif body == b'device-script\\n' and operation == 'admit' and not rest: output = b'render_admitted\\n'
elif body == b'device-script\\nsimulated-payload' and operation == 'prepare' and rest == ['tapcheck']: output = b'render_prepared\\n'
elif body == b'device-script\\n' and operation == 'visit' and rest == ['control-01']: output = b'render_completed\\n'
elif body == b'device-script\\n' and operation == 'collect' and rest == ['control-01']: output = b'fixture-archive'
elif body == b'device-script\\n' and operation == 'close' and not rest: output = b'render_closed\\n'
elif body == b'device-script\\n' and operation == 'absent' and not rest: output = b'render_absent\\n'
else: sys.exit(16)
sys.stdout.buffer.write(output)
""")
        os.chmod(host.common.__file__, 0o700)
        channel = host.Transport([sys.executable, str(fake)], self.root, LABEL, self.secret)
        try:
            for operation, expected in (('guard', host.REPLIES['guard']), ('admit', host.REPLIES['admit']),
                                        ('prepare', host.REPLIES['prepare']), ('visit:control-01', host.REPLIES['visit']),
                                        ('collect:control-01', b'fixture-archive'), ('close', host.REPLIES['close']),
                                        ('absent', host.REPLIES['absent'])):
                with self.subTest(operation=operation):
                    self.assertEqual(channel(operation)[:2], (0, expected))
            for invalid in ('visit:other', 'collect:control-04', 'prepare:control-01', 'pilot'):
                with self.assertRaisesRegex(host.common.Stop, 'invalid_operation'):
                    channel(invalid)
        finally:
            channel.close()

    def test_large_prepare_drains_stderr_without_stalling_payload(self):
        length = 64_248_212
        with (self.root / 'payload.tar.gz').open('wb') as output:
            output.write(b'BEGIN-PAYLOAD')
            output.seek(length - len(b'END-PAYLOAD'))
            output.write(b'END-PAYLOAD')
        fake = self.fake("""import os, sys
expected = int(sys.argv[1])
head, tail, total = b'', b'', 0
while True:
    part = sys.stdin.buffer.read(32768)
    if not part: break
    total += len(part)
    if len(head) < 27: head = (head + part)[:27]
    tail = (tail + part)[-32:]
    os.write(2, b'fixture-progress-no-private-data\\n' * 6)
if total != expected or not head.startswith(b'device-script\\nBEGIN-PAYLOAD') or not tail.endswith(b'END-PAYLOAD'): sys.exit(12)
os.write(2, b'refused native_tile\\n')
sys.stdout.buffer.write(b'render_prepared\\n')
""")
        channel = host.Transport([sys.executable, str(fake), str(len(b'device-script\n') + length)], self.root,
                                 LABEL, self.secret)
        try:
            code, output, diagnostics = channel('prepare')
            self.assertEqual((code, output), (0, host.REPLIES['prepare']))
            self.assertGreater(channel.last_stats['stderr_bytes'], host.common.STDERR_TAIL)
            self.assertTrue(channel.last_stats['stderr_truncated'])
            self.assertLessEqual(len(diagnostics), host.common.STDERR_TAIL)
        finally:
            channel.close()

    def test_stdout_limit_is_enforced_even_with_verbose_stderr(self):
        fake = self.fake("import sys\nsys.stderr.buffer.write(b'E' * 70000)\nsys.stdout.buffer.write(b'X' * 5000)\n")
        channel = host.Transport([sys.executable, str(fake)], self.root, LABEL, self.secret)
        try:
            with self.assertRaisesRegex(host.common.Stop, 'transport_stdout_limit'):
                channel('prepare')
        finally:
            channel.close()

    def test_signal_reaps_transport_and_does_not_expose_exception_text(self):
        fake = self.fake("""import os, pathlib, signal, sys, time
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
os.kill(os.getppid(), signal.SIGTERM)
time.sleep(20)
""")
        channel = host.Transport([sys.executable, str(fake), str(self.root / 'pid')], self.root, LABEL, self.secret)
        previous = signal.signal(signal.SIGTERM, host.common.interrupted)
        try:
            with self.assertRaises(host.common.PilotInterrupted):
                channel('prepare')
            with self.assertRaises(ProcessLookupError):
                os.kill(int((self.root / 'pid').read_text()), 0)
        finally:
            signal.signal(signal.SIGTERM, previous)
            channel.close()

    def test_signal_during_spawn_reaps_child_and_closes_secret_pipe(self):
        fake = self.fake('import time\ntime.sleep(20)\n')
        channel = host.Transport([sys.executable, str(fake)], self.root, LABEL, self.secret)
        original_popen, original_pipe = subprocess.Popen, host.common.secret_pipe
        processes, descriptors = [], []
        def spawn_then_signal(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            os.kill(os.getpid(), signal.SIGTERM)
            return process
        def capture_pipe(value):
            descriptor = original_pipe(value)
            descriptors.append(descriptor)
            return descriptor
        previous = signal.signal(signal.SIGTERM, host.common.interrupted)
        try:
            with mock.patch.object(host.subprocess, 'Popen', side_effect=spawn_then_signal), \
                 mock.patch.object(host.common, 'secret_pipe', side_effect=capture_pipe):
                with self.assertRaises(host.common.PilotInterrupted):
                    channel('prepare')
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].poll())
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])
        finally:
            signal.signal(signal.SIGTERM, previous)
            channel.close()


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix='render-host-freeze-'))
        self.root = Path(self.temporary)
        self.work, self.legacy = self.root / 'work', self.root / 'legacy'
        self.work.mkdir(mode=0o700)
        self.legacy.mkdir(mode=0o700)
        self.reference = self.legacy / 'batches' / ('reference-' + '1' * 16)
        self.navigation = self.legacy / 'batches' / ('navigation-' + '2' * 16)
        for mode, directory in (('reference', self.reference), ('navigation', self.navigation)):
            directory.mkdir(mode=0o700, parents=True)
            identity = {'project': 'Aidoku', 'owner_thread': 'earlier-owner', 'run_id': mode,
                        'lease_token_sha256': 'fixture-identity'}
            acquired = dict(identity, ok=True, operation='acquire', state='held')
            release = dict(identity, ok=True, operation='release', state='released', owner_and_token_checked=True,
                           lease_directory_absent_after_release=True, native_restored_and_connections_closed=True)
            (directory / 'receipt.json').write_text(json.dumps({'ok': True, 'mode': mode, 'completed': [mode],
                                                               'acquisition': acquired, 'release': release}))
        (self.reference / 'rule').write_bytes(b'AFR3RULE 1 1872 4960 0 6 1234\n' + b'F' * 4466)
        tap = {}
        for name in host.common.REQUIRED_INPUT:
            path = (self.navigation / 'artwork.guard' if name == 'input/artwork.guard'
                    else self.legacy / 'operator' / name)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(('fixture-' + name).encode())
            tap[name] = str(path)
        (self.navigation / 'tap-files.json').write_text(json.dumps(tap))
        for name in ('payload/control/program-modes', 'payload/control/launch.sh', 'payload/control/app/aidoku',
                     'payload/control/app/aidoku-startup-portrait', 'tools/device_lease.py',
                     'tools/run_hardware_smoke.py'):
            path = self.legacy / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(('fixture-' + name).encode())
        self.inputs = {}
        for name in ('device', 'guard', 'approved_guard', 'visit', 'dispatch', 'watcher'):
            path = self.root / name
            path.write_bytes(b'fixture-guard' if name in ('guard', 'approved_guard') else ('fixture-' + name).encode())
            self.inputs[name] = path

    def freeze(self):
        return host.freeze(self.work, self.legacy, self.reference, self.navigation, OWNER,
                           *(self.inputs[name] for name in ('device', 'guard', 'approved_guard', 'visit', 'dispatch',
                                                            'watcher')))

    def test_freeze_binds_real_cli_paths_fixed_cases_and_detects_changes(self):
        label = self.freeze()
        plan = host.checked_plan(self.work, OWNER)
        self.assertEqual(plan['label'], label)
        self.assertEqual(plan['cases'], list(host.CASES))
        self.assertIn('input/render-watch', plan['sources'])
        self.assertIn('input/reference.rule', plan['sources'])
        self.assertNotIn('input/touch-witness', plan['sources'])
        with tarfile.open(self.work / 'plan/payload.tar.gz', 'r:gz') as payload:
            self.assertEqual(payload.getmember('input/reference.rule').mode, 0o600)
        self.inputs['watcher'].write_bytes(b'changed-watcher')
        with self.assertRaises(host.common.Stop):
            host.checked_plan(self.work, OWNER)

    def test_guard_and_original_closed_reference_are_required(self):
        self.inputs['approved_guard'].write_bytes(b'different-guard')
        with self.assertRaisesRegex(host.common.Stop, 'guard_differs_from_approved'):
            self.freeze()
        self.inputs['approved_guard'].write_bytes(b'fixture-guard')
        receipt = self.reference / 'receipt.json'
        record = json.loads(receipt.read_text())
        record['release']['owner_and_token_checked'] = False
        receipt.write_text(json.dumps(record))
        with self.assertRaisesRegex(host.common.Stop, 'unclosed_reference'):
            self.freeze()

    def test_recovery_can_use_frozen_scripts_after_source_disappears(self):
        self.freeze()
        (self.work / 'plan/payload.tar.gz').unlink()
        self.inputs['device'].unlink()
        self.assertEqual(host.checked_plan(self.work, OWNER, minimal=True)['owner'], OWNER)
        with self.assertRaises((host.common.Stop, OSError)):
            host.checked_plan(self.work, OWNER)

    def test_rule_requires_scribe_six_book_geometry(self):
        rule = self.reference / 'rule'
        for header, points in ((b'AFR3RULE 1 1872 4960 0 7 1234\n', 4466),
                               (b'AFR3RULE 1 1872 4960 0 6 1234\n', 4465)):
            with self.subTest(header=header, points=points):
                rule.write_bytes(header + b'F' * points)
                with self.assertRaisesRegex(host.common.Stop, 'invalid_reference_rule'):
                    self.freeze()


class EntryTests(unittest.TestCase):
    def test_entrypoint_defaults_to_run_and_does_not_contact_a_device(self):
        with tempfile.TemporaryDirectory(prefix='render-host-entry-') as directory:
            root = Path(directory)
            work, binary = root / 'work', root / 'bin'
            work.mkdir(mode=0o700)
            binary.mkdir(mode=0o700)
            script = work / 'entrypoint.sh'
            script.write_bytes(Path(host.__file__).with_name('entrypoint.sh').read_bytes())
            (work / 'owner').write_text(OWNER + '\n')
            fake = binary / 'python3'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            fake.chmod(0o700)
            entry = root / 'shared-entry'
            entry.symlink_to(script)
            env = dict(os.environ, PATH=str(binary) + ':' + os.environ.get('PATH', ''))
            for arguments, operation in (([], '--run'), (['--status'], '--status'), (['--analyze'], '--analyze')):
                result = subprocess.run(['/bin/sh', str(entry), *arguments], env=env, capture_output=True, check=True)
                self.assertEqual(result.stdout.decode().splitlines(), ['-B', str(work / 'plan/host.py'), operation,
                                 str(work), '--owner', OWNER])
            result = subprocess.run(['/bin/sh', str(entry), '--unknown'], env=env, capture_output=True)
            self.assertEqual(result.returncode, 2)


class SecretTests(unittest.TestCase):
    def test_pipe_input_is_private_and_never_uses_environment(self):
        reader, writer = os.pipe()
        before = dict(os.environ)
        try:
            os.write(writer, b'fixture-password\n')
            os.close(writer)
            secret = host.input_secret_fd(reader)
            self.assertEqual(secret, bytearray(b'fixture-password'))
            self.assertEqual(before, dict(os.environ))
            secret[:] = b'\0' * len(secret)
        finally:
            os.close(reader)

    def test_tty_uses_existing_noecho_getpass_instead_of_plain_readline(self):
        secret = bytearray(b'fixture-password')
        with mock.patch.object(host.os, 'isatty', return_value=True), \
             mock.patch.object(host.common, 'input_secret', return_value=secret) as prompt, \
             mock.patch.object(host, 'input_secret_fd', side_effect=AssertionError('tty read as plain text')):
            self.assertIs(host.input_secret(0), secret)
        prompt.assert_called_once()
        secret[:] = b'\0' * len(secret)

    def test_invalid_pipe_or_regular_file_is_rejected(self):
        with tempfile.TemporaryFile() as regular:
            regular.write(b'fixture-password\n')
            regular.seek(0)
            with self.assertRaisesRegex(host.common.Stop, 'private_secret_pipe_required'):
                host.input_secret_fd(regular.fileno())
        for value in (b'\n', b'bad\rvalue\n', b'bad\0value\n', b'A' * (host.common.SECRET_LIMIT + 1)):
            with self.subTest(length=len(value)):
                reader, writer = os.pipe()
                try:
                    os.write(writer, value)
                    os.close(writer)
                    with self.assertRaisesRegex(host.common.Stop, 'invalid_password'):
                        host.input_secret_fd(reader)
                finally:
                    os.close(reader)


class MainIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix='render-host-main-'))
        self.work = Path(self.temporary)
        self.worker = self.work / 'worker'
        self.worker.write_bytes(b'fixture')
        self.lease = FakeLease()
        self.lease.LeaseRequest = lambda directory, token, owner_thread, run_id: SimpleNamespace(
            lease_dir=directory, token_file=token, owner_thread=owner_thread, run_id=run_id)
        self.remote = FakeRemote(self.lease)
        self.secret = bytearray(b'fixture-password')
        self.timeline = []
        self.remote.close = lambda: self.timeline.append('transport_closed')

    def invoke(self, *, gate=None):
        original_acquire = self.lease.acquire
        def acquire(request):
            self.timeline.append('acquire')
            return original_acquire(request)
        self.lease.acquire = acquire
        def password(_descriptor):
            self.timeline.append('getpass')
            return self.secret
        def transport(*_args, **_kwargs):
            self.timeline.append('transport_ready')
            return self.remote
        def live_gate():
            self.timeline.append('live_gate')
            if gate:
                raise gate
        def analyze(_work, _plan):
            self.assertFalse(self.lease.held)
            self.timeline.append('analyze_after_release')
            return 0
        ssh = SimpleNamespace(ssh_argv=lambda *_: ['fixture-ssh'])
        with mock.patch.object(host.common, 'WORKER', self.worker), mock.patch.object(host, 'checked_plan',
                 return_value={'label': LABEL, 'owner': OWNER}), mock.patch.object(host.common, 'load_module',
                 side_effect=[self.lease, ssh]), mock.patch.object(host.os, 'access', return_value=True), \
             mock.patch.object(host, 'input_secret', side_effect=password), \
             mock.patch.object(host, 'Transport', side_effect=transport), \
             mock.patch.object(host.common, 'cleanup_gate', side_effect=AssertionError('historical gate used')), \
             mock.patch.object(host.common, 'live_lease_gate', side_effect=live_gate), \
             mock.patch.object(host, 'analyze', side_effect=analyze), redirect_stdout(io.StringIO()):
            return host.main(['--run', str(self.work), '--owner', OWNER])

    def test_credentials_and_frozen_transport_precede_lease_and_analysis_follows_release(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.timeline, ['getpass', 'transport_ready', 'live_gate', 'acquire', 'transport_closed',
                                         'analyze_after_release'])
        self.assertEqual(self.secret, bytearray(len(self.secret)))
        self.assertEqual(self.remote.calls.count('visit:control-01'), 1)
        self.assertEqual(self.remote.calls.count('visit:control-02'), 1)
        self.assertEqual(self.remote.calls.count('visit:control-03'), 1)

    def test_busy_gate_wipes_secret_without_acquire_or_device_io(self):
        with self.assertRaisesRegex(host.common.Stop, 'lease_busy'):
            self.invoke(gate=host.common.Stop('lease_busy'))
        self.assertEqual(self.secret, bytearray(len(self.secret)))
        self.assertNotIn('acquire', self.lease.calls)
        self.assertEqual(self.remote.calls, [])
        self.assertFalse((self.work / 'attempt').exists())


if __name__ == '__main__':
    unittest.main()
