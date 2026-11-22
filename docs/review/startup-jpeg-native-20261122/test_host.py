from contextlib import redirect_stdout
import copy
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import host


OWNER = '11111111-1111-4111-a111-111111111111'
COHORT = 'a' * 16
LABEL = 'c' * 16
PRIVATE = 'private-fixture-data'


def archive(case, changes=None, *, extra=None):
    data = io.BytesIO()
    bracket = b'1000000 1000010 1050020 1050030\n'
    members = {'origin': b'stock-tap\n', 'qualified': f'{case} stock-tap\n'.encode(), 'anchor': bracket,
               'pointer-brackets': bracket, 'submission': b'SUBMISSION 2000000 2000010 2 37 large nonempty nonempty\n'}
    members.update(changes or {})
    with tarfile.open(fileobj=data, mode='w:gz', format=tarfile.USTAR_FORMAT) as stream:
        for name, value in members.items():
            item = tarfile.TarInfo('out/' + case + '/' + name)
            item.size = len(value)
            stream.addfile(item, io.BytesIO(value))
        if extra is not None:
            stream.addfile(extra)
    return data.getvalue()


class LeaseError(Exception):
    code = 'lease_exists'
    details = dict.fromkeys(('lease_directory_created', 'private_token_created', 'owner_record_created'), False)


class Lease:
    LeaseError = LeaseError

    def __init__(self):
        self.held, self.busy = False, False
        self.calls = []
        self.identity = {'project': 'Aidoku', 'owner_thread': OWNER, 'run_id': host.PREFIX + LABEL,
                         'lease_token_sha256': PRIVATE}

    def acquire(self, request):
        self.calls.append('acquire')
        if self.busy:
            raise LeaseError
        self.held = True
        return dict(self.identity, ok=True, state='held', operation='acquire')

    def status(self, request):
        self.calls.append('status')
        return dict(self.identity, ok=True, state='held' if self.held else 'absent',
                    operation='status', ownership_verified=self.held)

    def release(self, request, *, native_restored_and_connections_closed):
        assert self.held and self.calls[-1] == 'status' and native_restored_and_connections_closed
        self.held = False
        self.calls.append('release')
        return dict(self.identity, state='released', ok=True, operation='release', owner_and_token_checked=True,
                    native_restored_and_connections_closed=True, lease_directory_absent_after_release=True)


class Visits(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('visits', 'receipts'):
            (self.root / name).mkdir(mode=0o700)
        self.visit = host.binding(COHORT, OWNER, 1, LABEL)
        self.case = self.visit['case']
        self.attempt = self.root / 'visits' / ('01-' + LABEL)
        self.attempt.mkdir(mode=0o700)
        host.common.checkpoint(self.attempt / 'attempt.json', self.visit)
        self.request = SimpleNamespace(owner_thread=OWNER, run_id=host.PREFIX + LABEL)
        self.lease = Lease()
        self.operations, self.faults = [], {}
        self.evidence = archive(self.case)

    def remote(self, operation):
        assert self.lease.held and self.lease.calls[-1] == 'status'
        self.operations.append(operation)
        if operation in self.faults:
            fault = self.faults[operation]
            if isinstance(fault, BaseException):
                raise fault
            return 1, b'', b'refused native_admission\n' + PRIVATE.encode()
        kind = operation.partition(':')[0]
        return 0, self.evidence if kind == 'collect' else host.REPLIES[kind], b''

    def run_one(self):
        retained = []
        report = host.operate(self.lease, self.request, self.remote, self.visit, self.attempt, retained)
        self.assertFalse((self.attempt / 'case.tar.gz').exists())
        host.archive_after_return(self.visit, self.attempt, report, retained)
        return report

    def test_one_card_one_lease_then_offline_publication(self):
        result = self.run_one()
        self.assertEqual(self.operations, ['guard', 'admit', 'prepare', 'visit:' + self.case, 'collect:' + self.case,
                                          'close', 'absent'])
        self.assertFalse(self.lease.held)
        self.assertEqual((self.lease.calls.count('acquire'), self.lease.calls.count('release')), (1, 1))
        self.assertEqual(host.publish(self.root, self.visit, self.attempt, result), 'measured')
        self.assertEqual(host.publish(self.root, self.visit, self.attempt, result), 'measured')
        public = host.read_json(self.root / 'receipts/01.json')
        self.assertTrue(all(public['closeout'].values()))
        self.assertEqual((public['interval']['lower_us'], public['interval']['upper_us']), (2000000, 2000010))
        for forbidden in (PRIVATE, OWNER, LABEL, '1000020', 'marker', 'sequence', 'anchor', 'token_sha256'):
            self.assertNotIn(forbidden, json.dumps(public))
        host.next_slot(self.root, COHORT, OWNER, 2)
        with self.assertRaises(host.common.Stop):
            host.next_slot(self.root, COHORT, OWNER, 1)

    def test_atomic_collision_never_opens_ssh_or_consumes_slot(self):
        self.lease.busy = True
        result = self.run_one()
        self.assertTrue(host.untouched_collision(result))
        self.assertEqual(self.operations, [])
        self.assertEqual(self.lease.calls, ['acquire'])
        host.next_slot(self.root, COHORT, OWNER, 1)

    def test_read_only_rejection_returns_and_does_not_consume_slot(self):
        self.faults['admit'] = True
        result = self.run_one()
        self.assertEqual(self.operations, ['guard', 'admit'])
        self.assertEqual(host.publish(self.root, self.visit, self.attempt, result), 'no_card_visit')
        self.assertFalse(self.lease.held)
        host.next_slot(self.root, COHORT, OWNER, 1)

    def test_failed_or_interrupted_upload_must_close_and_verify_absence(self):
        self.faults['prepare'] = KeyboardInterrupt()
        result = self.run_one()
        self.assertEqual(self.operations, ['guard', 'admit', 'prepare', 'close', 'absent'])
        self.assertEqual(host.publish(self.root, self.visit, self.attempt, result), 'no_card_visit')
        host.next_slot(self.root, COHORT, OWNER, 1)

    def test_failed_card_does_not_allow_cherry_picking_a_new_attempt(self):
        self.faults['visit:' + self.case] = host.common.Stop('transport_timeout')
        result = self.run_one()
        self.assertFalse(self.lease.held)
        with self.assertRaisesRegex(host.common.Stop, 'requires_publication'):
            host.next_slot(self.root, COHORT, OWNER, 1)
        self.assertEqual(host.publish(self.root, self.visit, self.attempt, result), 'not_measured')
        public = host.read_json(self.root / 'receipts/01.json')
        self.assertIsNone(public['interval'])
        self.assertFalse(public['closeout']['ordinary_exit'])
        self.assertTrue(public['closeout']['released'])
        with self.assertRaises(host.common.Stop):
            host.next_slot(self.root, COHORT, OWNER, 2)

    def test_recover_only_original_unfinished_close_and_absence_before_release(self):
        self.faults['close'] = True
        report = self.run_one()
        self.assertTrue(self.lease.held)
        with self.assertRaises(host.common.Stop):
            host.publish(self.root, self.visit, self.attempt, report)
        with self.assertRaises(host.common.Stop):
            host.next_slot(self.root, COHORT, OWNER, 1)
        del self.faults['close']
        before = len(self.operations)
        report = host.common.recover(lease=self.lease, request=self.request, remote=self.remote, report=report,
                                    save=lambda value: host.common.checkpoint(self.attempt / 'operations.json', value))
        self.assertEqual(self.operations[before:], ['close', 'absent'])
        self.assertFalse(self.lease.held)
        self.assertEqual(host.publish(self.root, self.visit, self.attempt, report), 'not_measured')

    def test_durable_intent_survives_missing_visit_reply_and_recovery(self):
        self.faults['visit:' + self.case] = KeyboardInterrupt()
        result = self.run_one()
        result['operations'] = [row for row in result['operations'] if not row['operation'].startswith('visit:')]
        host.common.checkpoint(self.attempt / 'operations.json', result)
        with self.assertRaisesRegex(host.common.Stop, 'requires_publication'):
            host.next_slot(self.root, COHORT, OWNER, 1)
        self.assertEqual(host.publish(self.root, self.visit, self.attempt, result), 'not_measured')

    def test_unreaped_connection_or_missing_absence_never_releases(self):
        self.faults['absent'] = host.common.TransportUnreaped('transport_not_reaped')
        result = self.run_one()
        self.assertTrue(result['transport_unreaped'])
        self.assertTrue(self.lease.held)
        with self.assertRaises(host.common.Stop):
            host.publish(self.root, self.visit, self.attempt, result)
        self.assertFalse((self.root / 'receipts/01.json').exists())

    def test_incomplete_evidence_must_not_block_device_return(self):
        self.evidence = gzip.compress(b'private non-conforming output')
        result = self.run_one()
        self.assertFalse(self.lease.held)
        self.assertEqual(host.publish(self.root, self.visit, self.attempt, result), 'not_measured')

    def test_corrupt_identity_or_restoration_cannot_authorize_publication(self):
        original = self.run_one()
        for tag in ('project', 'run_id', 'lease_token_sha256', 'owner_and_token_checked', 'lease_directory_absent_after_release'):
            record = copy.deepcopy(original)
            record['release'][tag] = False
            with self.subTest(tag=tag), self.assertRaises(host.common.Stop):
                host.publish(self.root, self.visit, self.attempt, record)
        for ops in (['absent'], ['absent', 'close'], ['close'], []):
            record = copy.deepcopy(original)
            record['operations'] = record['operations'][:-2] + [{'operation': name, 'outcome': 'ok'} for name in ops]
            with self.subTest(ops=ops), self.assertRaises(host.common.Stop):
                host.publish(self.root, self.visit, self.attempt, record)


class Wire(unittest.TestCase):
    def test_frozen_schedule_has_distinct_warmups_and_counterbalanced_pairs(self):
        self.assertEqual([host.binding(COHORT, OWNER, slot, LABEL)['case'] for slot in range(1, 15)],
            ['control-warmup', 'candidate-warmup', 'control-01', 'candidate-01', 'candidate-02', 'control-02',
             'control-03', 'candidate-03', 'candidate-04', 'control-04', 'control-05', 'candidate-05',
             'candidate-06', 'control-06'])
        for slot, label in ((0, LABEL), (15, LABEL), (True, LABEL), (1, COHORT), (1, '../bad')):
            with self.subTest(slot=slot, label=label), self.assertRaises(host.common.Stop):
                host.binding(COHORT, OWNER, slot, label)

    def test_only_same_qualified_stock_release_and_first_large_grid_submission(self):
        case = 'candidate-02'
        for fields in ({'origin': b'uri\n'}, {'qualified': b'control-02 stock-tap\n'},
                       {'anchor': b'1 2 3 4\n'}, {'submission': b'SUBMISSION 10 21 1 2 large nonempty nonempty\n'},
                       {'submission': b'SUBMISSION 10 20 0 2 large nonempty nonempty\n'},
                       {'submission': b'SUBMISSION 10 20 1 4294967296 large nonempty nonempty\n'},
                       {'submission': b'SUBMISSION 10 20 1 2 compact nonempty nonempty\n'},
                       {'submission': b'SUBMISSION 10 20 1 2 large empty nonempty\n'}):
            with self.subTest(fields=fields), self.assertRaises(host.common.Stop):
                host.extract_record(archive(case, fields), case)
        self.assertEqual(host.extract_record(archive(case, {'submission': b'SUBMISSION 0 10 1 2 large nonempty nonempty\n'}), case)['lower_us'], 0)

    def test_no_other_file_image_path_symlink_duplicate_or_gzip_tail(self):
        case = 'control-01'
        for name, kind in (('stock.png', tarfile.REGTYPE), ('anchor', tarfile.REGTYPE),
                           ('../../x', tarfile.REGTYPE), ('symlink', tarfile.SYMTYPE)):
            item = tarfile.TarInfo('out/' + case + '/' + name)
            item.type = kind
            with self.assertRaises(host.common.Stop):
                host.extract_record(archive(case, extra=item), case)
        for data in (archive(case) + b'other', gzip.compress(b'\0' * (host.rendered.MAX_TAR + 1))):
            with self.assertRaises(host.common.Stop):
                host.extract_record(data, case)

    def test_local_mock_transport_delivers_selected_arm_and_case_without_a_password_argument(self):
        source = b'''#!/bin/sh
set -eu
case "$1" in
prepare) test "$3" = candidate-04; test "$(cat)" = candidate-package; echo jpeg_prepared;;
visit|collect) test "$3" = candidate-04; echo "$1";;
close|absent) test "$#" = 2; echo "$1";;
*) exit 1;;
esac\n'''
        fake = ('import os,sys; value=bytes.fromhex(%r).decode(); assert value not in repr(sys.argv)+repr(dict(os.environ)); '
                'os.execl("/bin/sh", "sh", "-c", sys.argv[-1])') % PRIVATE.encode().hex()
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp)
            (plan / 'device.sh').write_bytes(source)
            (plan / 'guard.sh').write_bytes(b'#!/bin/sh\necho guard_admitted\n')
            (plan / 'B').mkdir()
            (plan / 'B/payload.tar.gz').write_bytes(b'candidate-package')
            visit = host.binding(COHORT, OWNER, 9, LABEL)
            with mock.patch.object(host.rendered, 'CASES', (visit['case'],)):
                transport = host.Transport([sys.executable, '-c', fake], plan, visit, bytearray(PRIVATE.encode()))
                try:
                    self.assertEqual(transport('prepare')[:2], (0, b'jpeg_prepared\n'))
                    self.assertEqual(transport('visit:candidate-04')[:2], (0, b'visit\n'))
                    with self.assertRaises(host.common.Stop):
                        transport('visit:control-04')
                finally:
                    transport.close()
                recovery = host.Transport([sys.executable, '-c', fake], plan, visit, bytearray(b'fixture'), recover=True)
                try:
                    self.assertEqual(recovery('close')[:2], (0, b'close\n'))
                    self.assertEqual(recovery('absent')[:2], (0, b'absent\n'))
                    with self.assertRaises(host.common.Stop):
                        recovery('prepare')
                finally:
                    recovery.close()


class EntryPoint(unittest.TestCase):
    def test_private_frozen_runner_preflights_runs_and_republishes_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            plan = work / 'plan'
            plan.mkdir(mode=0o700)
            for name, source in (('host.py', host.HERE / 'host.py'), ('rendered.py', host.RENDERED),
                                 ('common.py', Path(host.common.__file__)), ('analyze.py', Path(host.analyze.__file__))):
                shutil.copyfile(source, plan / name)
            (plan / 'common.py').chmod(0o700)
            for name in ('ready', 'device.sh', 'guard.sh'):
                (plan / name).write_bytes(b'ready\n')
            for arm in 'AB':
                (plan / arm).mkdir()
                (plan / arm / 'payload.tar.gz').write_bytes(b'frozen fixture only')
            (plan / 'tools').mkdir()
            for name in ('device_lease.py', 'run_hardware_smoke.py'):
                (plan / 'tools' / name).write_bytes(b'pass\n')
            host.common.checkpoint(plan / 'plan.json', {'schema': 1, 'owner': OWNER, 'cohort': COHORT})
            frozen = host.load_module('unit_frozen_jpeg_host', plan / 'host.py')
            lease = Lease()
            lease.LeaseRequest = lambda lock, token, owner, run: SimpleNamespace(owner_thread=owner, run_id=run)
            ssh = SimpleNamespace(ssh_argv=lambda *_: ['mock transport'])
            secret_descriptor = os.pipe()
            os.write(secret_descriptor[1], b'unit-only\n')
            os.close(secret_descriptor[1])
            self.addCleanup(os.close, secret_descriptor[0])
            secret_values, log = [], []

            class MockTransport:
                def __init__(self, argv, plan, visit, secret, *, recover):
                    self.visit = visit
                    secret_values.append(secret)

                def __call__(self, operation):
                    self.test_owner()
                    log.append(operation)
                    kind = operation.partition(':')[0]
                    return 0, archive(self.visit['case']) if kind == 'collect' else frozen.REPLIES[kind], b''

                def test_owner(self):
                    assert lease.held and lease.calls[-1] == 'status'

                def close(self):
                    log.append('local-close')

            flags = [str(work), '--owner', OWNER, '--slot', '1', '--secret-fd', str(secret_descriptor[0])]
            with mock.patch.object(frozen.common, 'WORKER', plan / 'host.py'), \
                    mock.patch.object(frozen.common, 'LEASE', work / 'fake-lease'), \
                    mock.patch.object(frozen.common, 'load_module', side_effect=lambda name, path: lease if 'lease' in name else ssh), \
                    mock.patch.object(frozen.resource, 'setrlimit'), \
                    mock.patch.object(frozen.secrets, 'token_hex', return_value=LABEL), \
                    mock.patch.object(frozen, 'Transport', MockTransport), redirect_stdout(io.StringIO()) as output:
                (work / 'fake-lease').touch()
                self.assertEqual(frozen.main(['--run', *flags]), 3)
                self.assertEqual(lease.calls, [])
                self.assertEqual(list((work / 'visits').iterdir()), [])
                (work / 'fake-lease').unlink()
                self.assertEqual(frozen.main(['--preflight', *flags]), 0)
                self.assertEqual(lease.calls, [])
                self.assertEqual(frozen.main(['--run', *flags]), 0)
                self.assertEqual(secret_values, [bytearray(len(b'unit-only'))])
                self.assertEqual(log[-3:], ['close', 'absent', 'local-close'])
                self.assertEqual(frozen.main(['--publish', '--label', LABEL, *flags]), 0)
                with self.assertRaises(frozen.common.Stop):
                    frozen.main(['--run', *flags])
                self.assertNotIn('unit-only', output.getvalue())
                self.assertNotIn(PRIVATE, output.getvalue())


if __name__ == '__main__':
    unittest.main()
