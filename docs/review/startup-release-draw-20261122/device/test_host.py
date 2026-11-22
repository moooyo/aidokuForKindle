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


OWNER = '00000000-0101-4101-a101-000000010101'
COHORT = 'ae88776601020304'
STAGES = b'STAGES 100 120000 130010 580000 590010 600000 600010 1000000 1000010 2000000 2000010\n'
TRANSITION = (b'TRANSITION -1000000 -700000 -850000 -840000 -900000 -895000 -770000 -650000 '
              b'-600000 -590000 -560000 -550000 300000 20000 10000 1000\n')
DRAW = b'DRAWACCOUNT 300000 240000 4 0 2 3 1860 2480 gray8\n'
UNAVAILABLE = b'DRAWACCOUNT unavailable\n'


def envelope(case, draw=DRAW, *, omit=None, extra=None, qualified=None, transition=TRANSITION):
    anchor = b'1000000 1000010 1050020 1050030\n'
    fields = {'origin': b'stock-tap\n', 'anchor': anchor, 'pointer-brackets': anchor,
              'qualified': (qualified or case).encode() + b' stock-tap\n',
              'submission': b'SUBMISSION 2000000 2000010 2 9 large nonempty nonempty\n',
              'stages': STAGES, 'transition': transition, 'drawaccount': draw}
    if omit:
        del fields[omit]
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz', format=tarfile.USTAR_FORMAT) as stream:
        for name, value in list(fields.items()) + ([extra] if extra else []):
            item = tarfile.TarInfo(f'out/{case}/{name}')
            item.size = len(value)
            stream.addfile(item, io.BytesIO(value))
    return data.getvalue()


class Lease:
    def __init__(self):
        self.held, self.calls, self.identities = None, [], []

    def receipt(self, request, **data):
        return dict({'ok': True, 'state': 'held', 'owner_thread': OWNER, 'run_id': request.run_id,
                     'project': 'Aidoku', 'lease_token_sha256': 'private-test-' + request.run_id}, **data)

    def acquire(self, request):
        assert self.held is None and request.run_id not in self.identities
        self.held = request.run_id
        self.identities.append(self.held)
        self.calls.append('acquire')
        return self.receipt(request, operation='acquire')

    def status(self, request):
        self.calls.append('status')
        return self.receipt(request, operation='status', ownership_verified=self.held == request.run_id)

    def release(self, request, *, native_restored_and_connections_closed):
        assert self.held == request.run_id and native_restored_and_connections_closed and self.calls[-1] == 'status'
        self.held = None
        self.calls.append('release')
        return self.receipt(request, operation='release', state='released', owner_and_token_checked=True,
                            native_restored_and_connections_closed=True, lease_directory_absent_after_release=True)


class Runner(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.work = Path(scratch.name)
        self.plan = self.work / 'plan'
        self.plan.mkdir(mode=0o700)
        assets = {'host.py': host.HERE / 'host.py', 'analyze.py': host.HERE / 'analyze.py',
                  'transition_host.py': host.PREVIOUS, 'transition_analysis.py': host.trusted_analysis,
                  'stages_host.py': host.previous.PRIOR, 'stage_analysis.py': host.previous.STAGE_ANALYSIS,
                  'jpeg_host.py': host.previous.stages_host.PREVIOUS, 'common.py': Path(host.common.__file__),
                  'rendered.py': host.engine.RENDERED}
        for name, source in assets.items():
            shutil.copyfile(source, self.plan / name)
        (self.plan / 'common.py').chmod(0o700)
        (self.plan / 'tools').mkdir()
        for name in ('tools/run_hardware_smoke.py', 'tools/device_lease.py', 'device.sh', 'guard.sh'):
            (self.plan / name).write_bytes(b'pass\n')
        (self.plan / 'ready').write_bytes(b'ready\n')
        (self.plan / 'payload.tar.gz').write_bytes(b'only-draw-diagnostic')
        self.record = {'schema': 1, 'origin': 'stock-tap', 'configuration': 'diagnostic', 'visits': 4,
                       'owner': OWNER, 'cohort': COHORT, 'diagnostic': host.DIAGNOSTIC}
        host.common.checkpoint(self.plan / 'plan.json', self.record)
        self.frozen = host.engine.load_module('fixture_frozen_draw_host', self.plan / 'host.py')
        self.lease = Lease()
        self.lease.LeaseRequest = lambda lock, token, owner, run: SimpleNamespace(owner_thread=owner, run_id=run)
        ssh = SimpleNamespace(ssh_argv=lambda *_: ['no-network'])
        self.log, self.refusals, self.line = [], set(), DRAW
        test = self

        class Remote:
            def __init__(self, argv, plan, visit, secret, *, recover=False):
                self.visit = visit

            def __call__(self, operation):
                assert test.lease.held == test.frozen.engine.PREFIX + self.visit['label']
                assert test.lease.calls[-1] == 'status'
                test.log.append(operation)
                if operation in test.refusals:
                    return 1, b'', b'refused native_admission\n'
                kind = operation.partition(':')[0]
                value = envelope(self.visit['wire_case'], test.line) if kind == 'collect' else test.frozen.engine.REPLIES[kind]
                return 0, value, b''

            def close(self):
                test.log.append('local-close')

        for patch in (mock.patch.object(self.frozen.common, 'WORKER', self.plan / 'host.py'),
                      mock.patch.object(self.frozen.common, 'LEASE', self.work / 'fake-lock'),
                      mock.patch.object(self.frozen.common, 'load_module', side_effect=lambda name, path: self.lease if 'lease' in name else ssh),
                      mock.patch.object(self.frozen.engine.resource, 'setrlimit'),
                      mock.patch.object(self.frozen.engine, 'Transport', Remote)):
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, mode, slot, label=None):
        reader, writer = os.pipe()
        try:
            os.write(writer, b'unit-fixture\n')
        finally:
            os.close(writer)
        try:
            args = ['--' + mode, str(self.work), '--slot', str(slot), '--secret-fd', str(reader), '--owner', OWNER]
            if label:
                args.extend(['--label', label])
            with redirect_stdout(io.StringIO()) as output:
                status = self.frozen.main(args)
            return status, [json.loads(line) for line in output.getvalue().splitlines()]
        finally:
            os.close(reader)

    def test_four_fresh_visits_replay_without_private_fields(self):
        self.assertEqual(self.call('preflight', 1)[0], 0)
        self.assertEqual(self.lease.calls, [])
        for slot in range(1, 5):
            result, lines = self.call('run', slot)
            self.assertEqual(result, 0)
            self.assertIsNone(self.lease.held)
            self.assertEqual(self.log[-3:], ['close', 'absent', 'local-close'])
            self.assertTrue(lines[-1]['released'])
            self.assertEqual(self.call('publish', slot, lines[0]['label'])[0], 0)
            receipt = self.frozen.engine.read_json(self.work / 'receipts' / f'{slot:02}.json')
            self.assertTrue(all(receipt['closeout'].values()))
            self.assertEqual(receipt['interval']['lower_us'], 2000000)
            self.assertEqual(receipt['draw']['cpu_us'], 240000)
            for private in (OWNER, 'private-test-', 'unit-fixture', 'candidate-', '"arm"', '"stages"', '"transition"'):
                self.assertNotIn(private, json.dumps(receipt))
        self.assertEqual(self.lease.calls.count('acquire'), 4)
        self.assertEqual(self.lease.calls.count('release'), 4)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call('run', 5)

    def test_missing_cpu_keeps_fields_and_primary_after_release_but_stops(self):
        self.line = DRAW.replace(b'240000', b'none')
        status, rows = self.call('run', 1)
        self.assertEqual((status, rows[-1]['released']), (4, True))
        receipt = self.frozen.engine.read_json(self.work / 'receipts/01.json')
        self.assertEqual(receipt['result'], 'measured')
        self.assertIsNone(receipt['draw']['cpu_us'])
        self.assertFalse((self.work / 'receipts/02.json').exists())

    def test_explicit_unavailable_keeps_qualified_primary_only(self):
        self.line = UNAVAILABLE
        self.assertEqual(self.call('run', 1)[0], 4)
        receipt = self.frozen.engine.read_json(self.work / 'receipts/01.json')
        self.assertIsNone(receipt['draw'])
        self.assertEqual(receipt['interval']['upper_us'], 2000010)

    def test_no_rusage_still_qualifies_real_thread_cpu(self):
        self.line = DRAW.replace(b'4 0 2 3', b'none none none none')
        self.assertEqual(self.call('run', 1)[0], 0)

    def test_wrong_frame_is_not_partial_or_substitutable(self):
        self.line = DRAW.replace(b'300000', b'299999')
        code, report = self.call('run', 1)
        self.assertEqual((code, report[-1]['publication'], report[-1]['released']), (2, 'not_measured', True))
        self.assertIsNone(self.frozen.engine.read_json(self.work / 'receipts/01.json')['interval'])
        self.assertFalse(any(self.work.glob('visits/*/case.tar.gz')))
        with self.assertRaises(self.frozen.common.Stop):
            self.call('run', 1)

    def test_absence_required_before_publication_and_owned_recovery(self):
        self.refusals.add('absent')
        status, lines = self.call('run', 1)
        self.assertEqual((status, lines[-1]['released']), (2, False))
        self.assertFalse((self.work / 'receipts/01.json').exists())
        self.assertFalse(any(self.work.glob('visits/*/case.tar.gz')))
        label = lines[0]['label']
        with self.assertRaises(self.frozen.common.Stop):
            self.call('publish', 1, label)
        self.refusals.clear()
        count = len(self.log)
        self.assertEqual(self.call('recover', 1, label)[0], 0)
        self.assertEqual(self.log[count:], ['absent', 'local-close'])
        self.assertEqual(self.frozen.engine.read_json(self.work / 'receipts/01.json')['result'], 'not_measured')

    def test_rejects_previous_study_or_foreign_lease_without_request(self):
        (self.work / 'fake-lock').touch()
        self.assertEqual(self.call('run', 1)[0], 3)
        self.assertEqual(self.lease.calls, [])
        self.assertFalse(list((self.work / 'visits').iterdir()))
        (self.work / 'fake-lock').unlink()
        old = self.record.copy()
        old.pop('diagnostic')
        self.frozen.common.checkpoint(self.plan / 'plan.json', old)
        with self.assertRaises(self.frozen.common.Stop):
            self.call('run', 1)


class Envelopes(unittest.TestCase):
    def test_no_alias_or_extra_member_can_qualify(self):
        wire = 'candidate-01'
        for data in (envelope(wire, omit='drawaccount'), envelope(wire, extra=('drawaccount', DRAW)),
                     envelope(wire, extra=('rawlog', b'private')), envelope(wire, qualified='control-01'),
                     envelope('candidate-03'), envelope(wire, DRAW + b'other\n'), envelope(wire, b'raw not accepted\n'),
                     envelope(wire, transition=TRANSITION.replace(b'-1000000', b'-1000001')), envelope(wire) + b'other'):
            with self.subTest(length=len(data)), self.assertRaises(host.common.Stop):
                host.extract_record(data, 'profile-01')


if __name__ == '__main__':
    unittest.main()
