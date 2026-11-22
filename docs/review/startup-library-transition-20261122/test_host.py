from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import host


OWNER = '1177aadd-0fac-4380-9324-b5f927ddca11'
COHORT = '6789abcdefabcd09'
STAGES = b'STAGES 100 120000 130010 580000 590010 600000 600010 1000000 1000010 2000000 2000010\n'
UNAVAILABLE = b'TRANSITION unavailable\n'
TRANSITION = (b'TRANSITION -1000000 -700000 -850000 -840000 -900000 -895000 -770000 -650000 '
              b'-600000 -590000 -560000 -550000 300000 20000 10000 1000\n')


def archive(wire, line=UNAVAILABLE, stages=STAGES, omit=None, extra=None, qualified=None):
    data = io.BytesIO()
    anchor = b'1000000 1000010 1050020 1050030\n'
    fields = {'origin': b'stock-tap\n', 'qualified': (qualified or wire).encode() + b' stock-tap\n',
              'anchor': anchor, 'pointer-brackets': anchor, 'submission': b'SUBMISSION 2000000 2000010 2 9 large nonempty nonempty\n',
              'stages': stages, 'transition': line}
    if omit:
        del fields[omit]
    with tarfile.open(fileobj=data, mode='w:gz', format=tarfile.USTAR_FORMAT) as stream:
        for name, value in list(fields.items()) + ([extra] if extra else []):
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
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.plan = self.work / 'plan'
        self.plan.mkdir(mode=0o700)
        for name, source in (('host.py', host.HERE / 'host.py'), ('stages_host.py', host.PRIOR),
                             ('stage_analysis.py', host.STAGE_ANALYSIS), ('analyze.py', Path(host.analyze.__file__)),
                             ('jpeg_host.py', host.stages_host.PREVIOUS), ('rendered.py', host.engine.RENDERED),
                             ('common.py', Path(host.common.__file__))):
            shutil.copyfile(source, self.plan / name)
        (self.plan / 'common.py').chmod(0o700)
        (self.plan / 'tools').mkdir()
        for name in ('device.sh', 'guard.sh', 'tools/device_lease.py', 'tools/run_hardware_smoke.py'):
            (self.plan / name).write_bytes(b'pass\n')
        (self.plan / 'ready').write_bytes(b'ready\n')
        (self.plan / 'payload.tar.gz').write_bytes(b'only-diagnostic-payload')
        self.record = {'schema': 1, 'owner': OWNER, 'origin': 'stock-tap', 'configuration': 'diagnostic', 'cohort': COHORT, 'visits': 4}
        host.common.checkpoint(self.plan / 'plan.json', self.record)
        self.frozen = host.engine.load_module('unit_transition', self.plan / 'host.py')
        self.lease = Lease()
        self.lease.LeaseRequest = lambda lock, token, owner, run: SimpleNamespace(owner_thread=owner, run_id=run)
        ssh = SimpleNamespace(ssh_argv=lambda *_: ['no-device-mock'])
        self.operations, self.refuse, self.line = [], set(), UNAVAILABLE
        test = self

        class Remote:
            def __init__(self, argv, plan, visit, secret, *, recover=False):
                self.visit = visit

            def __call__(self, operation):
                assert test.lease.held == test.frozen.engine.PREFIX + self.visit['label']
                assert test.lease.calls[-1] == 'status'
                test.operations.append(operation)
                if operation in test.refuse:
                    return 1, b'', b'refused native_admission\n'
                value = (archive(self.visit['wire_case'], test.line) if operation.startswith('collect:')
                         else test.frozen.engine.REPLIES[operation.partition(':')[0]])
                return 0, value, b''

            def close(self):
                test.operations.append('local-close')

        for patch in (mock.patch.object(self.frozen.common, 'WORKER', self.plan / 'host.py'),
                      mock.patch.object(self.frozen.common, 'LEASE', self.work / 'mock-lock'),
                      mock.patch.object(self.frozen.common, 'load_module', side_effect=lambda name, path: self.lease if 'lease' in name else ssh),
                      mock.patch.object(self.frozen.engine.resource, 'setrlimit'),
                      mock.patch.object(self.frozen.engine, 'Transport', Remote)):
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, mode, slot, label=None):
        reader, writer = os.pipe()
        try:
            os.write(writer, b'offline-test\n')
        finally:
            os.close(writer)
        try:
            args = ['--' + mode, str(self.work), '--slot', str(slot), '--owner', OWNER, '--secret-fd', str(reader)]
            if label:
                args.extend(['--label', label])
            with redirect_stdout(io.StringIO()) as output:
                status = self.frozen.main(args)
            return status, [json.loads(line) for line in output.getvalue().splitlines()]
        finally:
            os.close(reader)

    def test_primary_without_detail_stops_batch_after_return(self):
        self.assertEqual(self.call('preflight', 1)[0], 0)
        self.assertEqual(self.lease.calls, [])
        code, report = self.call('run', 1)
        self.assertEqual(code, 4)
        self.assertEqual(report[-1]['publication'], host.PRIMARY_ONLY)
        self.assertTrue(report[-1]['released'])
        self.assertIsNone(self.lease.held)
        self.assertEqual(self.operations[-3:], ['close', 'absent', 'local-close'])
        receipt = self.frozen.engine.read_json(self.work / 'receipts/01.json')
        self.assertEqual((receipt['result'], receipt['transition'], receipt['interval']['upper_us']), ('measured', None, 2000010))
        self.assertEqual(receipt['stages']['initial'], [1000000, 1000010])
        self.assertTrue(all(receipt['closeout'].values()))
        self.assertEqual(self.call('publish', 1, report[0]['label'])[0], 4)
        self.assertFalse((self.work / 'receipts/02.json').exists())
        self.assertEqual(self.lease.calls.count('acquire'), 1)
        for private in ('candidate-', OWNER, 'mock-private', 'offline-test'):
            self.assertNotIn(private, json.dumps(receipt))

    def test_four_complete_slots_independently_return_the_same_configuration(self):
        self.line = TRANSITION
        for slot in range(1, 5):
            code, report = self.call('run', slot)
            self.assertEqual(code, 0)
            self.assertIsNone(self.lease.held)
            self.assertEqual(self.operations[-3:], ['close', 'absent', 'local-close'])
            self.assertEqual(report[0]['case'], 'profile-warmup' if slot == 1 else f'profile-{slot-1:02}')
            receipt = self.frozen.engine.read_json(self.work / 'receipts' / f'{slot:02}.json')
            self.assertEqual((receipt['result'], receipt['configuration']), ('measured', 'diagnostic'))
            self.assertTrue(receipt['transition'])
            self.assertTrue(all(receipt['closeout'].values()))
        self.assertEqual(self.lease.calls.count('acquire'), 4)
        self.assertEqual(self.lease.calls.count('release'), 4)
        self.assertEqual(len(set(self.lease.labels)), 4)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call('run', 5)

    def test_wrong_binding_is_not_partial_success_and_cannot_be_replaced(self):
        self.line = b'conflicting private log\n'
        code, report = self.call('run', 1)
        self.assertEqual((code, report[-1]['publication'], report[-1]['released']), (2, 'not_measured', True))
        receipt = self.frozen.engine.read_json(self.work / 'receipts/01.json')
        self.assertIsNone(receipt['interval'])
        self.assertIsNone(receipt['stages'])
        with self.assertRaises(self.frozen.common.Stop):
            self.call('run', 1)
        self.assertFalse(any(self.work.glob('visits/*/case.tar.gz')))

    def test_failed_absence_blocks_issuance_and_exact_recovery_does_not_rerun(self):
        self.refuse.add('absent')
        code, report = self.call('run', 1)
        label = report[0]['label']
        self.assertEqual((code, report[-1]['released']), (2, False))
        self.assertFalse((self.work / 'receipts/01.json').exists())
        self.assertFalse(any(self.work.glob('visits/*/case.tar.gz')))
        with self.assertRaises(self.frozen.common.Stop):
            self.call('publish', 1, label)
        self.refuse.clear()
        count = len(self.operations)
        self.assertEqual(self.call('recover', 1, label)[0], 0)
        self.assertEqual(self.operations[count:], ['absent', 'local-close'])
        self.assertIsNone(self.lease.held)
        self.assertEqual(self.frozen.engine.read_json(self.work / 'receipts/01.json')['result'], 'not_measured')

    def test_only_new_four_slot_plan_and_single_package_admitted(self):
        for changed in ({'visits': 5}, {'configuration': 'B'}, {'arm': 'A'}):
            self.frozen.common.checkpoint(self.plan / 'plan.json', {**self.record, **changed})
            with self.assertRaises(self.frozen.common.Stop):
                self.call('preflight', 1)
        self.frozen.common.checkpoint(self.plan / 'plan.json', self.record)
        (self.work / 'mock-lock').touch()
        self.assertEqual(self.call('run', 1)[0], 3)
        self.assertEqual(self.lease.calls, [])
        self.assertFalse(list((self.work / 'visits').iterdir()))
        (self.work / 'mock-lock').unlink()
        (self.plan / 'A').mkdir()
        with self.assertRaises(self.frozen.common.Stop):
            self.call('run', 1)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call('run', 5)


class Envelopes(unittest.TestCase):
    def test_strict_seven_members_and_only_the_named_case(self):
        wire = 'candidate-01'
        for data in (archive(wire, omit='transition'), archive(wire, extra=('transition', UNAVAILABLE)),
                     archive(wire, extra=('image', b'raw')), archive(wire, qualified='candidate-02'),
                     archive('candidate-02'), archive(wire, stages=b'ordinary raw log\n'),
                     archive(wire, line=UNAVAILABLE + b'private tail'), archive(wire) + b'other',
                     archive(wire, line=TRANSITION.replace(b'-1000000', b'-1000001'))):
            with self.subTest(length=len(data)), self.assertRaises(host.common.Stop):
                host.extract_record(data, 'profile-01')
        with self.assertRaises(host.common.Stop):
            host.extract_record(archive('candidate-04'), 'profile-04')


if __name__ == '__main__':
    unittest.main()
