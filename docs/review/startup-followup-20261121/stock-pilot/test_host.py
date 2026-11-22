import copy
from contextlib import redirect_stdout
from dataclasses import dataclass
import gzip
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import host


class LeaseError(Exception):
    def __init__(self, code, details):
        self.code, self.details = code, details


@dataclass(frozen=True)
class Request:
    owner_thread: str = 'fixture-owner'
    run_id: str = 'aidoku-fixture'


class FakeLease:
    LeaseError = LeaseError

    def __init__(self):
        self.calls = []
        self.held = False
        self.acquire_error = None
        self.bad_release = False
        self.identity = {'project': 'Aidoku', 'owner_thread': 'fixture-owner', 'run_id': 'aidoku-fixture',
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


class FakeRemote:
    def __init__(self, lease):
        self.lease = lease
        self.calls = []
        self.faults = {}
        self.after = None
        self.archive = gzip.compress(b'private fixture evidence')

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
        output = self.archive if operation == 'collect' else host.REPLIES[operation]
        return 0, output, b'fixture-sensitive-output'


class FakeCheckpoint:
    def __init__(self):
        self.saved = None
        self.fail_when = None

    def __call__(self, value):
        if self.fail_when and self.fail_when(value):
            raise OSError('fixture-sensitive-output')
        self.saved = copy.deepcopy(value)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.record = {'resolved_by_admin_cleanup': True, 'cleanup_only': True,
                       'new_lease_administratively_retired': True,
                       'ordinary_acquisition_unblocked_at_cleanup': True,
                       'restoration_verified': False, 'original_owner_release': False}

    def check(self, record, status=0):
        response = SimpleNamespace(returncode=status, stdout=json.dumps(record).encode(), stderr=b'')
        with mock.patch.object(host.subprocess, 'run', return_value=response) as invoked:
            result = host.cleanup_gate()
        invoked.assert_called_once()
        self.assertEqual(invoked.call_args.args[0], [str(host.CLEANUP), '--status'])
        return result

    def test_exact_cleanup_semantics(self):
        self.assertTrue(self.check(self.record))
        for key in self.record:
            for value in (None, 'true', not self.record[key]):
                with self.subTest(key=key, value=value):
                    changed = dict(self.record, **{key: value})
                    with self.assertRaises(host.Stop):
                        self.check(changed)

    def test_unavailable_or_malformed_status_refuses(self):
        with self.assertRaises(host.Stop):
            self.check(self.record, status=1)
        response = SimpleNamespace(returncode=0, stdout=b'{"cleanup_only":true,"cleanup_only":true}', stderr=b'')
        with mock.patch.object(host.subprocess, 'run', return_value=response):
            with self.assertRaises(host.Stop):
                host.cleanup_gate()

    def test_live_lock_is_an_independent_gate(self):
        with mock.patch.object(host.os.path, 'lexists', return_value=True) as exists:
            with self.assertRaisesRegex(host.Stop, 'lease_busy'):
                host.live_lease_gate()
        exists.assert_called_once_with(host.LEASE)
        with mock.patch.object(host.os.path, 'lexists', return_value=False):
            host.live_lease_gate()

    def test_secret_pipe_has_one_use_and_never_uses_environment(self):
        secret = bytearray(b'fixture-password')
        before = dict(os.environ)
        descriptor = host.secret_pipe(secret)
        try:
            self.assertEqual(os.read(descriptor, 1024), b'fixture-password')
            self.assertEqual(os.read(descriptor, 1024), b'')
        finally:
            os.close(descriptor)
        self.assertEqual(before, dict(os.environ))

    def test_refusal_extraction_has_an_explicit_safe_allowlist(self):
        self.assertEqual(host.failure_detail(1, b'refused native_tile\n'), 'native_tile')
        self.assertEqual(host.failure_detail(255, b'Permission denied (password).\n'), 'authentication_failed')
        self.assertEqual(host.failure_detail(255, b'ssh: connection to private host failed: No route to host\n'),
                         'no_route')
        self.assertIsNone(host.failure_detail(1, b'refused private_secret_abcdef\n'))
        self.assertIsNone(host.failure_detail(1, b'refused native_tile /private/path\n'))

    def test_password_is_requested_once_from_a_tty(self):
        terminal = mock.MagicMock()
        terminal.__enter__.return_value = terminal
        terminal.fileno.return_value = 22
        with mock.patch('builtins.open', return_value=terminal), mock.patch.object(host.os, 'isatty', return_value=True), \
             mock.patch.object(host.getpass, 'getpass', return_value='fixture-password') as prompt:
            secret = host.input_secret()
        prompt.assert_called_once()
        self.assertEqual(secret, bytearray(b'fixture-password'))
        secret[:] = b'\0' * len(secret)

    def test_each_mock_ssh_receives_a_new_one_shot_pipe(self):
        with tempfile.TemporaryDirectory(prefix='pilot-host-fixture-') as directory:
            root = Path(directory)
            (root / 'device.sh').write_bytes(b'device-script\n')
            (root / 'guard.sh').write_bytes(b'guard-script\n')
            (root / 'payload.tar.gz').write_bytes(b'simulated-payload')
            fake = root / 'fake_ssh.py'
            fake.write_text("""import os, subprocess, sys
expected = b'fixture-password'
if any(expected in value.encode() for value in os.environ.values()):
    sys.exit(10)
if any(expected in value.encode() for value in sys.argv):
    sys.exit(11)
if subprocess.check_output([os.environ['SSH_ASKPASS']]) != expected + b'\\n':
    sys.exit(12)
if subprocess.run([os.environ['SSH_ASKPASS']], stdout=subprocess.DEVNULL).returncode == 0:
    sys.exit(13)
body = sys.stdin.buffer.read()
sys.stdout.buffer.write(b'guard_admitted\\n' if body == b'guard-script\\n' else b'pilot_admitted\\n')
""", encoding='utf-8')
            secret = bytearray(b'fixture-password')
            channel = host.Transport([sys.executable, str(fake)], root, '0' * 16, secret)
            try:
                self.assertEqual(channel('guard')[:2], (0, host.REPLIES['guard']))
                self.assertEqual(channel('admit')[:2], (0, host.REPLIES['admit']))
            finally:
                channel.close()
                secret[:] = b'\0' * len(secret)

    def test_large_prepare_drains_verbose_stderr_without_interrupting_transfer(self):
        with tempfile.TemporaryDirectory(prefix='pilot-large-transport-') as directory:
            root = Path(directory)
            body = b'fixture-device-script\n' * 700
            (root / 'device.sh').write_bytes(body)
            (root / 'guard.sh').write_bytes(b'fixture-guard\n')
            payload_length = 64_248_212
            with (root / 'payload.tar.gz').open('wb') as output:
                output.write(b'BEGIN-PAYLOAD')
                output.seek(payload_length - len(b'END-PAYLOAD'))
                output.write(b'END-PAYLOAD')
            fake = root / 'fake_ssh.py'
            fake.write_text("""import os, sys
expected = int(sys.argv[1])
prefix = b'fixture-device-script\\n' * 700 + b'BEGIN-PAYLOAD'
head, tail = b'', b''
total = 0
while True:
    part = sys.stdin.buffer.read(32768)
    if not part:
        break
    total += len(part)
    if len(head) < len(prefix):
        head = (head + part)[:len(prefix)]
    tail = (tail + part)[-32:]
    os.write(2, b'fixture-progress-no-private-data\\n' * 6)
if total != expected or head != prefix or not tail.endswith(b'END-PAYLOAD'):
    sys.exit(12)
os.write(2, b'refused native_tile\\n')
sys.stdout.buffer.write(b'pilot_prepared\\n')
""", encoding='utf-8')
            secret = bytearray(b'fixture-password')
            channel = host.Transport([sys.executable, str(fake), str(len(body) + payload_length)], root, '0' * 16,
                                     secret)
            try:
                code, output, diagnostics = channel('prepare')
                self.assertEqual((code, output), (0, host.REPLIES['prepare']))
                self.assertGreater(channel.last_stats['stderr_bytes'], 65536)
                self.assertTrue(channel.last_stats['stderr_truncated'])
                self.assertLessEqual(len(diagnostics), 65536)
                self.assertEqual(host.failure_detail(1, diagnostics), 'native_tile')
            finally:
                channel.close()
                secret[:] = b'\0' * len(secret)

    def test_stdout_remains_bounded_even_when_stderr_is_drained(self):
        with tempfile.TemporaryDirectory(prefix='pilot-large-stdout-') as directory:
            root = Path(directory)
            (root / 'device.sh').write_bytes(b'fixture-device\n')
            (root / 'guard.sh').write_bytes(b'fixture-guard\n')
            (root / 'payload.tar.gz').write_bytes(b'fixture-payload')
            fake = root / 'fake_ssh.py'
            fake.write_text("import sys\nsys.stdout.buffer.write(b'X' * 5000)\n", encoding='utf-8')
            secret = bytearray(b'fixture-password')
            channel = host.Transport([sys.executable, str(fake)], root, '0' * 16, secret)
            try:
                with self.assertRaisesRegex(host.Stop, 'transport_stdout_limit'):
                    channel('prepare')
                self.assertGreater(channel.last_stats['stdout_bytes'], 4096)
            finally:
                channel.close()
                secret[:] = b'\0' * len(secret)

    def test_external_signal_reports_its_category_and_reaps_mock_transport(self):
        with tempfile.TemporaryDirectory(prefix='pilot-signal-transport-') as directory:
            root = Path(directory)
            (root / 'device.sh').write_bytes(b'fixture-device\n')
            (root / 'guard.sh').write_bytes(b'fixture-guard\n')
            (root / 'payload.tar.gz').write_bytes(b'fixture-payload')
            fake = root / 'fake_ssh.py'
            fake.write_text("""import os, pathlib, signal, sys, time
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
os.kill(os.getppid(), signal.SIGTERM)
time.sleep(20)
""", encoding='utf-8')
            secret = bytearray(b'fixture-password')
            channel = host.Transport([sys.executable, str(fake), str(root / 'pid')], root, '0' * 16, secret)
            previous = signal.signal(signal.SIGTERM, host.interrupted)
            try:
                with self.assertRaises(host.PilotInterrupted) as interrupted:
                    channel('prepare')
                self.assertEqual(host.transport_reason(interrupted.exception), 'interrupted_sigterm')
                self.assertGreaterEqual(channel.last_stats['elapsed_ms'], 0)
                with self.assertRaises(ProcessLookupError):
                    os.kill(int((root / 'pid').read_text()), 0)
            finally:
                signal.signal(signal.SIGTERM, previous)
                channel.close()
                secret[:] = b'\0' * len(secret)

    def test_signal_during_spawn_reaps_the_new_child_and_closes_the_pipe(self):
        with tempfile.TemporaryDirectory(prefix='pilot-spawn-signal-') as directory:
            root = Path(directory)
            (root / 'device.sh').write_bytes(b'fixture-device\n')
            (root / 'guard.sh').write_bytes(b'fixture-guard\n')
            (root / 'payload.tar.gz').write_bytes(b'fixture-payload')
            fake = root / 'fake_ssh.py'
            fake.write_text('import time\ntime.sleep(20)\n', encoding='utf-8')
            secret = bytearray(b'fixture-password')
            channel = host.Transport([sys.executable, str(fake)], root, '0' * 16, secret)
            original_popen, original_pipe = subprocess.Popen, host.secret_pipe
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
            previous = signal.signal(signal.SIGTERM, host.interrupted)
            try:
                with mock.patch.object(host.subprocess, 'Popen', side_effect=spawn_then_signal), \
                     mock.patch.object(host, 'secret_pipe', side_effect=capture_pipe):
                    with self.assertRaises(host.PilotInterrupted):
                        channel('prepare')
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())
                with self.assertRaises(OSError):
                    os.fstat(descriptors[0])
            finally:
                signal.signal(signal.SIGTERM, previous)
                channel.close()
                secret[:] = b'\0' * len(secret)


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(prefix='pilot-freeze-fixture-'))
        self.root = Path(self.temporary)
        self.work = self.root / 'work'
        self.legacy = self.root / 'legacy'
        self.runner = self.root / 'runner'
        for directory in (self.work, self.legacy, self.runner):
            directory.mkdir(mode=0o700)
        for name in ('host.py', 'pilot_pixels.py', 'observer.py'):
            (self.runner / name).write_bytes(('fixture ' + name).encode())
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
        (self.reference / 'rule').write_bytes(b'fixture-rule')
        tap = {}
        for name in host.REQUIRED_INPUT:
            path = (self.navigation / 'artwork.guard' if name == 'input/artwork.guard'
                    else self.legacy / 'operator' / name)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(('fixture-' + name).encode())
            tap[name] = str(path)
        (self.navigation / 'tap-files.json').write_text(json.dumps(tap))
        for name in ('operator/observer/framewatch', 'payload/control/program-modes', 'payload/control/launch.sh',
                     'payload/control/app/aidoku', 'payload/control/app/aidoku-startup-portrait',
                     'tools/device_lease.py', 'tools/run_hardware_smoke.py'):
            path = self.legacy / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(('fixture-' + name).encode())
        self.inputs = {}
        for name in ('device', 'guard', 'approved_guard', 'visit', 'dispatch', 'witness'):
            path = self.root / name
            path.write_bytes(b'fixture-guard' if name in ('guard', 'approved_guard') else ('fixture-' + name).encode())
            self.inputs[name] = path
        self.owner = '00000000-0000-0000-0000-000000000001'

    def freeze(self):
        with mock.patch.object(host, 'HERE', self.runner):
            return host.freeze(self.work, self.legacy, self.reference, self.navigation, self.owner,
                               *(self.inputs[name] for name in ('device', 'guard', 'approved_guard', 'visit',
                                                                'dispatch', 'witness')))

    def test_freeze_validates_fresh_plan_and_source_changes(self):
        label = self.freeze()
        with mock.patch.object(host, 'HERE', self.runner):
            plan = host.checked_plan(self.work, self.owner)
            self.assertEqual(plan['label'], label)
            self.assertIn('input/touch-witness', plan['sources'])
            self.inputs['witness'].write_bytes(b'changed-witness')
            with self.assertRaises(host.Stop):
                host.checked_plan(self.work, self.owner)

    def test_recovery_keeps_frozen_scripts_even_if_source_disappears(self):
        self.freeze()
        (self.work / 'plan/payload.tar.gz').unlink()
        self.inputs['device'].unlink()
        with mock.patch.object(host, 'HERE', self.runner):
            plan = host.checked_plan(self.work, self.owner, minimal=True)
            self.assertEqual(plan['owner'], self.owner)
            with self.assertRaises((host.Stop, OSError)):
                host.checked_plan(self.work, self.owner)

    def test_guard_and_old_closed_receipt_fail_closed(self):
        self.inputs['approved_guard'].write_bytes(b'not-the-current-approved-guard')
        with mock.patch.object(host, 'HERE', self.runner):
            with self.assertRaisesRegex(host.Stop, 'guard_differs_from_approved'):
                self.freeze()
        self.inputs['approved_guard'].write_bytes(b'fixture-guard')
        record = json.loads((self.reference / 'receipt.json').read_text())
        record['release']['owner_and_token_checked'] = False
        (self.reference / 'receipt.json').write_text(json.dumps(record))
        with mock.patch.object(host, 'HERE', self.runner):
            with self.assertRaisesRegex(host.Stop, 'unclosed_reference'):
                self.freeze()

    def test_status_is_host_only_and_handles_unfinished_attempt(self):
        self.assertEqual(host.status_report(self.work), {'prepared': True, 'attempt': 'not_started',
                                                         'measurement_recorded': False})
        (self.work / 'attempt').mkdir(mode=0o700)
        result = host.status_report(self.work)
        self.assertEqual(result['attempt'], 'unknown')
        self.assertTrue(result['recovery_required'])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.lease = FakeLease()
        self.remote = FakeRemote(self.lease)
        self.checkpoint = FakeCheckpoint()
        self.saved_archives = []

    def run_pilot(self):
        return host.operate(lease=self.lease, request=Request(), remote=self.remote, save=self.checkpoint,
                            save_archive=self.saved_archives.append)

    def test_success_has_one_visit_and_closes_before_release(self):
        report = self.run_pilot()
        self.assertEqual(self.remote.calls, ['guard', 'admit', 'prepare', 'pilot', 'collect', 'close', 'absent'])
        self.assertEqual(self.saved_archives, [self.remote.archive])
        self.assertTrue(report['released'])
        self.assertTrue(report['operations_passed'])
        self.assertFalse(report['recovery_required'])
        self.assertEqual(self.lease.calls.count('release'), 1)
        self.assertEqual(report, self.checkpoint.saved)
        self.assertNotIn('private fixture evidence', str(report))
        self.assertNotIn('fixture-sensitive-output', str(report))

    def test_busy_or_partial_acquisition_never_contacts_or_releases(self):
        for code, details, recovery in (
            ('lease_exists', {'lease_directory_created': False, 'private_token_created': False,
                              'owner_record_created': False}, False),
            ('operation_busy', {'lease_directory_created': False, 'private_token_created': False,
                                'owner_record_created': False}, False),
            ('filesystem_error', {'lease_directory_created': True, 'private_token_created': True,
                                  'owner_record_created': False}, True),
            ('lease_exists', {'lease_directory_created': True, 'private_token_created': False,
                              'owner_record_created': False}, True),
        ):
            with self.subTest(code=code, details=details):
                self.setUp()
                self.lease.acquire_error = LeaseError(code, details)
                report = self.run_pilot()
                self.assertEqual(report['recovery_required'], recovery)
                self.assertFalse(report['released'])
                self.assertEqual(self.remote.calls, [])
                self.assertNotIn('release', self.lease.calls)

    def test_admission_failures_release_without_device_mutation(self):
        for operation in ('guard', 'admit'):
            with self.subTest(operation=operation):
                self.setUp()
                self.remote.faults[operation] = (1, b'', b'private')
                report = self.run_pilot()
                self.assertTrue(report['released'])
                self.assertFalse(report['operations_passed'])
                self.assertNotIn('prepare', self.remote.calls)
                self.assertNotIn('close', self.remote.calls)

    def test_any_uncertain_mutation_attempts_bounded_close_then_releases(self):
        for operation in ('prepare', 'pilot', 'collect'):
            for fault in (TimeoutError(), KeyboardInterrupt(), (1, b'', b'private')):
                with self.subTest(operation=operation, fault=type(fault).__name__):
                    self.setUp()
                    self.remote.faults[operation] = fault
                    report = self.run_pilot()
                    self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])
                    self.assertTrue(report['released'])
                    self.assertFalse(report['operations_passed'])
                    self.assertEqual(self.remote.calls.count('pilot'), int(operation != 'prepare'))

    def test_invalid_archive_still_closes(self):
        self.remote.faults['collect'] = (0, b'not-a-gzip', b'')
        report = self.run_pilot()
        self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])
        self.assertTrue(report['released'])
        self.assertEqual(self.saved_archives, [])

    def test_unknown_transport_outcomes_are_classified_without_raw_exception_text(self):
        for error, expected in ((host.Stop('transport_stdout_limit'), 'transport_stdout_limit'),
                                (host.Stop('transport_timeout'), 'transport_timeout'),
                                (KeyboardInterrupt(), 'interrupted'),
                                (host.PilotInterrupted(signal.SIGHUP), 'interrupted_sighup'),
                                (OSError('fixture-private-path'), 'transport_io_error'),
                                (RuntimeError('fixture-secret-private-path'), 'transport_error')):
            with self.subTest(expected=expected):
                self.setUp()
                self.remote.faults['prepare'] = error
                report = self.run_pilot()
                item = next(item for item in report['operations'] if item['operation'] == 'prepare')
                self.assertEqual(item['transport_error'], expected)
                self.assertEqual(report['error'], 'prepare_' + expected)
                self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])
                self.assertTrue(report['released'])
                self.assertNotIn('fixture-secret-private-path', str(report))

    def test_close_or_absence_failure_retains_the_lease(self):
        for operation in ('close', 'absent'):
            with self.subTest(operation=operation):
                self.setUp()
                self.remote.faults[operation] = (1, b'', b'')
                report = self.run_pilot()
                self.assertFalse(report['released'])
                self.assertTrue(report['recovery_required'])
                self.assertNotIn('release', self.lease.calls)

    def test_absence_unknown_never_releases_after_successful_close(self):
        for error in (TimeoutError(), KeyboardInterrupt(), host.Stop('transport_timeout')):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.remote.faults['absent'] = error
                report = self.run_pilot()
                self.assertTrue(report['close_confirmed'])
                self.assertIsNot(report.get('absence_confirmed'), True)
                self.assertFalse(report['released'])
                self.assertTrue(report['recovery_required'])
                self.assertNotIn('release', self.lease.calls)
                self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])
                self.remote.faults.clear()
                recovered = host.recover(lease=self.lease, request=Request(), remote=self.remote, report=report,
                                         save=self.checkpoint)
                self.assertTrue(recovered['released'])
                self.assertEqual(self.remote.calls.count('close'), 1)
                self.assertEqual(self.remote.calls.count('absent'), 2)

    def test_unreaped_close_or_absence_requires_manual_reconciliation(self):
        for operation in ('close', 'absent'):
            with self.subTest(operation=operation):
                self.setUp()
                self.remote.faults[operation] = host.TransportUnreaped('fixture')
                report = self.run_pilot()
                self.assertTrue(report['transport_unreaped'])
                self.assertTrue(report['recovery_required'])
                self.assertFalse(report['released'])
                self.assertNotIn('release', self.lease.calls)
                before = list(self.remote.calls)
                with self.assertRaisesRegex(host.Stop, 'manual_reconciliation_required'):
                    host.recover(lease=self.lease, request=Request(), remote=self.remote, report=report,
                                 save=self.checkpoint)
                self.assertEqual(before, self.remote.calls)

    def test_ownership_loss_never_closes_or_releases_another_lease(self):
        self.remote.after = lambda operation: setattr(self.lease, 'held', False) if operation == 'pilot' else None
        report = self.run_pilot()
        self.assertFalse(report['released'])
        self.assertNotIn('close', self.remote.calls)
        self.assertNotIn('release', self.lease.calls)
        self.assertTrue(report['recovery_required'])

    def test_unreaped_connection_never_opens_a_concurrent_close(self):
        self.remote.faults['pilot'] = host.TransportUnreaped('fixture')
        report = self.run_pilot()
        self.assertEqual(self.remote.calls[-1], 'pilot')
        self.assertNotIn('close', self.remote.calls)
        self.assertNotIn('absent', self.remote.calls)
        self.assertFalse(report['released'])
        self.assertNotIn('release', self.lease.calls)
        self.assertTrue(report['recovery_required'])

    def test_checkpoint_failure_after_mutation_still_attempts_close(self):
        self.checkpoint.fail_when = lambda report: any(item['operation'] == 'prepare'
                                                       for item in report['operations'])
        report = self.run_pilot()
        self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])
        self.assertNotIn('release', self.lease.calls)
        self.assertTrue(report['recovery_required'])
        self.assertEqual(report['error'], 'checkpoint_failed')

    def test_first_checkpoint_failure_never_acquires(self):
        self.checkpoint.fail_when = lambda report: True
        report = self.run_pilot()
        self.assertNotIn('acquire', self.lease.calls)
        self.assertEqual(self.remote.calls, [])
        self.assertFalse(report['released'])

    def test_failed_release_never_reports_success(self):
        self.lease.bad_release = True
        report = self.run_pilot()
        self.assertFalse(report['released'])
        self.assertFalse(report['operations_passed'])
        self.assertEqual(report['release_error'], 'release_unverified')

    def test_recovery_retries_only_close_and_absence_then_releases(self):
        self.remote.faults['close'] = (1, b'', b'')
        report = self.run_pilot()
        self.assertFalse(report['released'])
        self.remote.faults.clear()
        recovered = host.recover(lease=self.lease, request=Request(), remote=self.remote, report=report,
                                 save=self.checkpoint)
        self.assertEqual(self.remote.calls[-2:], ['close', 'absent'])
        self.assertEqual(self.remote.calls.count('pilot'), 1)
        self.assertTrue(recovered['released'])
        self.assertFalse(recovered['operations_passed'])
        self.assertFalse(recovered['recovery_required'])

    def test_recovery_after_verified_close_only_rechecks_absence(self):
        self.remote.faults['absent'] = (1, b'', b'')
        report = self.run_pilot()
        self.assertTrue(report['close_confirmed'])
        self.remote.faults.clear()
        recovered = host.recover(lease=self.lease, request=Request(), remote=self.remote, report=report,
                                 save=self.checkpoint)
        self.assertEqual(self.remote.calls.count('close'), 1)
        self.assertEqual(self.remote.calls.count('absent'), 2)
        self.assertTrue(recovered['released'])

    def test_recovery_rejects_foreign_or_unreaped_session(self):
        self.remote.faults['close'] = (1, b'', b'')
        report = self.run_pilot()
        for altered in ('owner', 'unreaped', 'missing_identity'):
            with self.subTest(altered=altered):
                changed = copy.deepcopy(report)
                if altered == 'owner':
                    changed['request']['owner_thread'] = 'another-owner'
                elif altered == 'unreaped':
                    changed['transport_unreaped'] = True
                else:
                    changed['identity'] = None
                before = list(self.remote.calls)
                with self.assertRaises(host.Stop):
                    host.recover(lease=self.lease, request=Request(), remote=self.remote, report=changed,
                                 save=self.checkpoint)
                self.assertEqual(before, self.remote.calls)


class MainIntegrationTests(unittest.TestCase):
    def test_run_prompts_once_then_checks_both_gates_before_atomic_acquisition(self):
        with tempfile.TemporaryDirectory(prefix='pilot-main-fixture-') as directory:
            work = Path(directory)
            marker = work / 'worker'
            marker.write_bytes(b'fixture')
            owner = '00000000-0000-0000-0000-000000000001'
            label = '0' * 16
            timeline = []
            lease = FakeLease()
            lease.identity.update(owner_thread=owner, run_id='aidoku-stockpilot-' + label)
            original_acquire = lease.acquire
            def acquire(request):
                timeline.append('acquire')
                return original_acquire(request)
            lease.acquire = acquire
            lease.LeaseRequest = lambda directory, token, owner_thread, run_id: SimpleNamespace(
                lease_dir=directory, token_file=token, owner_thread=owner_thread, run_id=run_id)
            remote = FakeRemote(lease)
            remote.close = lambda: timeline.append('transport_closed')
            ssh = SimpleNamespace(ssh_argv=lambda *_: ['fixture-ssh'])
            secret = bytearray(b'fixture-password')
            def prompt():
                timeline.append('getpass')
                return secret
            def transport(*_args, **_kwargs):
                timeline.append('transport_ready')
                return remote
            def analyze(_work, _plan):
                self.assertFalse(lease.held)
                timeline.append('analyze_after_release')
                return 0
            with mock.patch.object(host, 'WORKER', marker), mock.patch.object(host, 'checked_plan',
                     return_value={'label': label, 'owner': owner}), mock.patch.object(host, 'load_module',
                     side_effect=[lease, ssh]), mock.patch.object(host.os, 'access', return_value=True), \
                 mock.patch.object(host, 'input_secret', side_effect=prompt) as password, \
                 mock.patch.object(host, 'Transport', side_effect=transport), \
                 mock.patch.object(host, 'cleanup_gate', side_effect=lambda: timeline.append('cleanup_gate')), \
                 mock.patch.object(host, 'live_lease_gate', side_effect=lambda: timeline.append('lease_gate')), \
                 mock.patch.object(host, 'analyze', side_effect=analyze), redirect_stdout(io.StringIO()):
                self.assertEqual(host.main(['--run', str(work), '--owner', owner]), 0)
            password.assert_called_once()
            self.assertEqual(timeline, ['getpass', 'transport_ready', 'cleanup_gate', 'lease_gate', 'acquire',
                                        'transport_closed', 'analyze_after_release'])
            self.assertEqual(secret, bytearray(len(secret)))
            self.assertEqual(remote.calls.count('pilot'), 1)

    def test_closed_live_gate_wipes_secret_without_acquiring_or_device_io(self):
        with tempfile.TemporaryDirectory(prefix='pilot-main-fixture-') as directory:
            work = Path(directory)
            marker = work / 'worker'
            marker.write_bytes(b'fixture')
            owner = '00000000-0000-0000-0000-000000000001'
            lease = FakeLease()
            lease.LeaseRequest = lambda directory, token, owner_thread, run_id: SimpleNamespace(
                lease_dir=directory, token_file=token, owner_thread=owner_thread, run_id=run_id)
            remote = FakeRemote(lease)
            remote.close = lambda: None
            secret = bytearray(b'fixture-password')
            with mock.patch.object(host, 'WORKER', marker), mock.patch.object(host, 'checked_plan',
                     return_value={'label': '0' * 16, 'owner': owner}), mock.patch.object(host, 'load_module',
                     side_effect=[lease, SimpleNamespace(ssh_argv=lambda *_: ['fixture-ssh'])]), \
                 mock.patch.object(host.os, 'access', return_value=True), \
                 mock.patch.object(host, 'input_secret', return_value=secret), \
                 mock.patch.object(host, 'Transport', return_value=remote), \
                 mock.patch.object(host, 'cleanup_gate'), \
                 mock.patch.object(host, 'live_lease_gate', side_effect=host.Stop('lease_busy')), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(host.Stop, 'lease_busy'):
                    host.main(['--run', str(work), '--owner', owner])
            self.assertEqual(secret, bytearray(len(secret)))
            self.assertNotIn('acquire', lease.calls)
            self.assertEqual(remote.calls, [])
            self.assertFalse((work / 'attempt').exists())

    def test_short_entrypoint_defaults_to_run_without_opening_a_device(self):
        with tempfile.TemporaryDirectory(prefix='pilot-entry-fixture-') as directory:
            root = Path(directory)
            work, binary = root / 'work', root / 'bin'
            work.mkdir(mode=0o700)
            binary.mkdir(mode=0o700)
            script = work / 'entrypoint.sh'
            script.write_bytes(Path(host.__file__).with_name('entrypoint.sh').read_bytes())
            (work / 'owner').write_text('fixture-owner\n')
            fake = binary / 'python3'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            fake.chmod(0o700)
            entry = root / 'shared-entry'
            entry.symlink_to(script)
            env = dict(os.environ, PATH=str(binary) + ':' + os.environ.get('PATH', ''))
            for arguments, operation in (([], '--run'), (['--status'], '--status'), (['--analyze'], '--analyze')):
                result = subprocess.run(['/bin/sh', str(entry), *arguments], env=env, capture_output=True, check=True)
                self.assertEqual(result.stdout.decode().splitlines(), ['-B', str(work / 'host.py'), operation,
                                 str(work), '--owner', 'fixture-owner'])
            result = subprocess.run(['/bin/sh', str(entry), '--unknown'], env=env, capture_output=True)
            self.assertEqual(result.returncode, 2)


class EvidenceTests(unittest.TestCase):
    def members(self):
        return {'origin': b'stock-tap\n', 'anchor': b'100 101 150 151\n',
                'pointer-brackets': b'100 101 150 151\n',
                'qualified': b'control-warmup stock-tap\n', 'stock.frame': b'fixture stock',
                'trace': b'fixture trace', 'touch': b'ATW1 SINGLE 1 2 3 4 5 6 7\n'}

    def archive(self, members):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            for name, value in members.items():
                item = tarfile.TarInfo('out/control-warmup/' + name)
                item.size = len(value)
                archive.addfile(item, io.BytesIO(value))
        return output.getvalue()

    def test_only_same_visit_complete_four_value_anchor_is_accepted(self):
        members, brackets = host.archive_members(self.archive(self.members()))
        self.assertEqual(brackets, (100, 101, 150, 151))
        self.assertEqual(members['touch'], self.members()['touch'])
        for change in ('missing_touch', 'missing_raw', 'different_raw', 'two_values', 'wrong_origin', 'extra'):
            with self.subTest(change=change):
                fixture = self.members()
                if change == 'missing_touch':
                    del fixture['touch']
                elif change == 'missing_raw':
                    del fixture['pointer-brackets']
                elif change == 'different_raw':
                    fixture['pointer-brackets'] = b'100 101 149 151\n'
                elif change == 'two_values':
                    fixture['anchor'] = fixture['pointer-brackets'] = b'100 101\n'
                elif change == 'wrong_origin':
                    fixture['origin'] = b'indexed-uri\n'
                else:
                    fixture['unrelated'] = b'fixture'
                with self.assertRaises(host.Stop):
                    host.archive_members(self.archive(fixture))

    def test_static_control_captures_are_optional(self):
        fixture = self.members()
        fixture.update({'control.before.frame': b'fixture', 'control.after.frame': b'fixture',
                        'control.anchor': b'1 2\n', 'control.method': b'manual-window\n'})
        self.assertEqual(host.archive_members(self.archive(fixture))[1], (100, 101, 150, 151))

    def test_analyze_requires_released_visit_and_calls_physical_touch_combination(self):
        lease = FakeLease()
        label = '0' * 16
        owner = '00000000-0000-0000-0000-000000000001'
        lease.identity.update(owner_thread=owner, run_id='aidoku-stockpilot-' + label)
        request = SimpleNamespace(owner_thread=owner, run_id=lease.identity['run_id'])
        remote = FakeRemote(lease)
        remote.archive = self.archive(self.members())
        records, archives = [], []
        report = host.operate(lease=lease, request=request, remote=remote, save=lambda value: records.append(
            copy.deepcopy(value)), save_archive=archives.append)
        with tempfile.TemporaryDirectory(prefix='pilot-analysis-fixture-') as directory:
            work = Path(directory)
            attempt = work / 'attempt'
            attempt.mkdir(mode=0o700)
            (work / 'plan').mkdir(mode=0o700)
            host.write_new(attempt / 'operations.json', json.dumps(report).encode())
            host.write_new(attempt / 'evidence.tar.gz', archives[0])
            observed = []
            def combined(rule, trace, stock, brackets, touch):
                self.assertIsInstance(rule, Path)
                self.assertIsInstance(trace, Path)
                self.assertIsInstance(stock, Path)
                observed.append((brackets, touch))
                return {'physical_sort_tap_observed': True, 'first_response_is_upper_bound': True,
                        'ready_upper_ms': 123.0}
            with mock.patch.object(host, 'load_module', return_value=SimpleNamespace(analyze_visit=combined)), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(host.analyze(work, {'owner': owner, 'label': label}), 0)
            self.assertEqual(observed, [((100, 101, 150, 151), self.members()['touch'])])

    def test_missing_touch_never_returns_measurement(self):
        fixture = self.members()
        fixture.pop('touch')
        with self.assertRaisesRegex(host.Stop, 'missing_evidence_member'):
            host.archive_members(self.archive(fixture))


if __name__ == '__main__':
    unittest.main()
