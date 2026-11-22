from contextlib import redirect_stderr, redirect_stdout
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


OWNER = '1177aadd-0fac-4380-9324-b5f927ddca11'
COHORT = '6789abcdefabcd09'
STAGES = b'STAGES 100 120000 130010 580000 590010 600000 600010 1000000 1000010 2000000 2000010\n'


def archive(wire, stage=STAGES, extra=None, omit=None, qualified=None):
    data = io.BytesIO()
    anchor = b'1000000 1000010 1050020 1050030\n'
    fields = {'origin': b'stock-tap\n', 'qualified': (qualified or wire).encode() + b' stock-tap\n',
              'anchor': anchor, 'pointer-brackets': anchor, 'submission': b'SUBMISSION 2000000 2000010 2 9 large nonempty nonempty\n',
              'stages': stage}
    if omit is not None:
        del fields[omit]
    with tarfile.open(fileobj=data, mode='w:gz', format=tarfile.USTAR_FORMAT) as stream:
        for name, value in list(fields.items()) + ([] if not extra else [extra]):
            item = tarfile.TarInfo('out/' + wire + '/' + name)
            item.size = len(value)
            stream.addfile(item, io.BytesIO(value))
    return data.getvalue()


class Lease:
    def __init__(self):
        self.held, self.labels, self.calls = None, [], []

    def acquire(self, request):
        assert not self.held and request.run_id not in self.labels
        self.held = request.run_id
        self.labels.append(self.held)
        self.calls.append('acquire')
        return self.proof(request, operation='acquire')

    def proof(self, request, **fields):
        return dict({'project': 'Aidoku', 'owner_thread': OWNER, 'run_id': request.run_id, 'ok': True,
                     'state': 'held', 'lease_token_sha256': 'mock-private-' + request.run_id}, **fields)

    def status(self, request):
        self.calls.append('status')
        return self.proof(request, operation='status', ownership_verified=self.held == request.run_id)

    def release(self, request, *, native_restored_and_connections_closed):
        assert self.held == request.run_id and native_restored_and_connections_closed and self.calls[-1] == 'status'
        self.calls.append('release')
        self.held = None
        return self.proof(request, operation='release', state='released', owner_and_token_checked=True,
                          lease_directory_absent_after_release=True, native_restored_and_connections_closed=True)


class Runner(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.plan = self.root / 'plan'
        self.plan.mkdir(mode=0o700)
        for name, path in (('host.py', host.HERE / 'host.py'), ('jpeg_host.py', host.PREVIOUS),
                          ('common.py', Path(host.common.__file__)), ('rendered.py', host.engine.RENDERED),
                          ('analyze.py', Path(host.analyze.__file__))):
            shutil.copyfile(path, self.plan / name)
        (self.plan / 'common.py').chmod(0o700)
        (self.plan / 'ready').write_bytes(b'ready\n')
        (self.plan / 'payload.tar.gz').write_bytes(b'only-diagnostic-payload')
        (self.plan / 'tools').mkdir()
        for name in ('tools/device_lease.py', 'tools/run_hardware_smoke.py', 'device.sh', 'guard.sh'):
            (self.plan / name).write_text('pass\n')
        self.record = {'schema': 1, 'cohort': COHORT, 'owner': OWNER, 'origin': 'stock-tap',
                       'configuration': 'diagnostic', 'visits': 5}
        host.common.checkpoint(self.plan / 'plan.json', self.record)
        self.frozen = host.engine.load_module('unit_stock_diag', self.plan / 'host.py')
        self.lease = Lease()
        self.lease.LeaseRequest = lambda lock, token, owner, run: SimpleNamespace(owner_thread=owner, run_id=run)
        self.ssh = SimpleNamespace(ssh_argv=lambda *_: ['no-device-mock'])
        self.log, self.credentials, self.faults, self.stage = [], [], {}, STAGES
        test = self

        class Remote:
            def __init__(self, argv, plan, visit, secret, *, recover=False):
                self.visit = visit
                test.credentials.append(secret)

            def __call__(self, operation):
                assert test.lease.held == host.PREFIX + self.visit['label'] and test.lease.calls[-1] == 'status'
                test.log.append(operation)
                if operation in test.faults:
                    return 1, b'', b'refused native_admission\n'
                kind = operation.partition(':')[0]
                return 0, (archive(self.visit['wire_case'], stage=test.stage) if kind == 'collect'
                           else test.frozen.engine.REPLIES[kind]), b''

            def close(self):
                test.log.append('local-close')

        for patch in (mock.patch.object(self.frozen.common, 'WORKER', self.plan / 'host.py'),
                      mock.patch.object(self.frozen.common, 'LEASE', self.root / 'mock-lock'),
                      mock.patch.object(self.frozen.common, 'load_module', side_effect=lambda name, path: self.lease if 'lease' in name else self.ssh),
                      mock.patch.object(self.frozen.engine.resource, 'setrlimit'), mock.patch.object(self.frozen.engine, 'Transport', Remote)):
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, mode, slot, label=None):
        reader, writer = os.pipe()
        try:
            os.write(writer, b'offline-test\n')
        finally:
            os.close(writer)
        try:
            args = ['--' + mode, str(self.root), '--owner', OWNER, '--slot', str(slot), '--secret-fd', str(reader)]
            if label:
                args.extend(['--label', label])
            with redirect_stdout(io.StringIO()) as output:
                status = self.frozen.main(args)
            return status, [json.loads(line) for line in output.getvalue().splitlines()]
        finally:
            os.close(reader)

    def test_five_independent_slots_one_configuration_then_stop(self):
        self.assertEqual(self.call('preflight', 1)[1][0]['state'], 'ready')
        self.assertEqual(self.lease.calls, [])
        for slot in range(1, 6):
            status, records = self.call('run', slot)
            self.assertEqual(status, 0)
            self.assertIsNone(self.lease.held)
            self.assertEqual(self.log[-3:], ['close', 'absent', 'local-close'])
            warmup_or_name = 'profile-warmup' if slot == 1 else f'profile-{slot-1:02}'
            self.assertEqual(records[0]['case'], warmup_or_name)
            self.assertEqual(self.call('publish', slot, records[0]['label'])[1][0]['publication'], 'measured')
            receipt = self.frozen.engine.read_json(self.root / 'receipts' / f'{slot:02}.json')
            self.assertEqual(receipt['configuration'], 'diagnostic')
            self.assertTrue(all(receipt['closeout'].values()))
            self.assertEqual(receipt['stages']['send'], [2000000, 2000010])
            for value in ('"arm"', 'candidate-', 'control-', 'mock-private-', 'offline-test', OWNER):
                self.assertNotIn(value, json.dumps(receipt))
        self.assertEqual(self.lease.calls.count('acquire'), 5)
        self.assertEqual(self.lease.calls.count('release'), 5)
        self.assertTrue(all(bytes(data) == bytes(len(b'offline-test')) for data in self.credentials))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call('run', 6)

    def test_unknown_stage_is_not_a_substitutable_sample(self):
        self.stage = STAGES.replace(b'2000010\n', b'2000011\n')
        status, records = self.call('run', 1)
        self.assertEqual((status, records[-1]['released'], records[-1]['publication']), (2, True, 'not_measured'))
        with self.assertRaises(self.frozen.common.Stop):
            self.call('run', 1)
        with self.assertRaises(self.frozen.common.Stop):
            self.call('run', 2)
        self.assertEqual(self.lease.calls.count('acquire'), 1)

    def test_explicit_unavailable_preserves_only_independent_total(self):
        self.stage = b'STAGES unavailable\n'
        self.assertEqual(self.call('run', 1)[0], 0)
        record = self.frozen.engine.read_json(self.root / 'receipts/01.json')
        self.assertIsNone(record['stages'])
        self.assertEqual(record['interval']['lower_us'], 2000000)

    def test_no_publication_before_absence_and_exact_return(self):
        self.faults['absent'] = True
        status, records = self.call('run', 1)
        self.assertEqual((status, records[-1]['released']), (2, False))
        self.assertFalse((self.root / 'receipts/01.json').exists())
        self.assertFalse(any(self.root.glob('visits/*/case.tar.gz')))
        label = records[0]['label']
        with self.assertRaises(self.frozen.common.Stop):
            self.call('publish', 1, label)
        del self.faults['absent']
        before = len(self.log)
        self.assertEqual(self.call('recover', 1, label)[0], 0)
        self.assertEqual(self.log[before:], ['absent', 'local-close'])
        self.assertIsNone(self.lease.held)
        self.assertEqual(self.frozen.engine.read_json(self.root / 'receipts/01.json')['result'], 'not_measured')

    def test_foreign_contention_or_wrong_plan_never_launches(self):
        (self.root / 'mock-lock').touch()
        self.assertEqual(self.call('run', 1)[0], 3)
        self.assertEqual(self.credentials, [])
        self.assertFalse(list((self.root / 'visits').iterdir()))
        (self.root / 'mock-lock').unlink()
        (self.plan / 'A').mkdir()
        with self.assertRaisesRegex(self.frozen.common.Stop, 'not_a_single'):
            self.call('run', 1)
        (self.plan / 'A').rmdir()
        for mutation in ({'visits': 14}, {'configuration': 'B'}, {'origin': 'managed'}, {'arm': 'A'}):
            self.frozen.common.checkpoint(self.plan / 'plan.json', {**self.record, **mutation})
            with self.assertRaisesRegex(self.frozen.common.Stop, 'wrong_diagnostic_plan'):
                self.call('run', 1)
        self.assertEqual(self.lease.calls, [])

    def test_old_wire_address_never_changes_the_payload_or_public_case(self):
        device = b'''#!/bin/sh
set -eu
case "$1" in
prepare) test "$3" = candidate-03; test "$(cat)" = only-diagnostic-payload; echo jpeg_prepared;;
visit|collect) test "$3" = candidate-03; echo "$1";;
close|absent) test "$#" = 2; echo "$1";;
*) exit 1;; esac\n'''
        (self.plan / 'device.sh').write_bytes(device)
        visit = self.frozen.binding(COHORT, OWNER, 4, 'e' * 16)
        self.assertEqual((visit['case'], visit['wire_case']), ('profile-03', 'candidate-03'))
        local = [sys.executable, '-c', 'import os,sys; os.execl("/bin/sh", "sh", "-c", sys.argv[-1])']
        with mock.patch.object(self.frozen.rendered, 'CASES', (visit['case'],)):
            transport = self.frozen.Transport(local, self.plan, visit, bytearray(b'offline-test'))
            try:
                self.assertEqual(transport('prepare')[:2], (0, b'jpeg_prepared\n'))
                self.assertEqual(transport('visit:profile-03')[:2], (0, b'visit\n'))
                self.assertEqual(transport('collect:profile-03')[:2], (0, b'collect\n'))
                with self.assertRaises(self.frozen.common.Stop):
                    transport('visit:candidate-03')
            finally:
                transport.close()


class Envelopes(unittest.TestCase):
    def test_rejects_unbound_archive_missing_or_duplicate_stage_and_unexpected_bytes(self):
        wire = 'candidate-02'
        for data in (archive(wire, omit='stages'), archive(wire, stage=b'private raw log\n'),
                     archive(wire, extra=('stages', STAGES)), archive(wire, extra=('image', b'raw')),
                     archive(wire, qualified='candidate-04'), archive('candidate-04'),
                     archive(wire, stage=STAGES.replace(b'1000010', b'1000110')),
                     archive(wire, stage=STAGES.replace(b'2000010\n', b'2000011\n'))):
            with self.subTest(length=len(data)), self.assertRaises(host.common.Stop):
                host.extract_record(data, 'profile-02')


if __name__ == '__main__':
    unittest.main()
