import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import closeout


class DrawCloseout(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        self.shared = self.work / 'shared'
        for name in ('plan', 'visits', 'receipts', 'shared'):
            (self.work / name).mkdir(mode=0o700)
        self.payload = self.work / 'plan/payload.tar.gz'
        self.payload.write_bytes(b'consumed upload')
        self.payload.chmod(0o600)
        self.cohort = '0123456789abcdef'
        self.owner = '00000000-0000-4000-8000-000000000001'
        (self.work / 'plan/plan.json').write_text(json.dumps({
            'schema': 1, 'cohort': self.cohort, 'owner': self.owner, 'configuration': 'diagnostic',
            'origin': 'stock-tap', 'visits': 4, 'diagnostic': closeout.DIAGNOSTIC}))
        rows = []
        for slot in range(1, 5):
            visit = self.work / 'visits' / f'{slot:02}-{slot:016x}'
            visit.mkdir(mode=0o700)
            (visit / 'card-intent.json').touch()
            stamp = f'2026-01-01T00:00:{slot:02}+00:00'
            (visit / 'operations.json').write_text(json.dumps({'phase': 'release', 'release': {'released_at_utc': stamp}}))
            (self.work / 'receipts' / f'{slot:02}.json').touch()
            rows.append({'slot': slot, 'cohort': self.cohort, 'configuration': 'diagnostic', 'result': 'measured',
                         'draw': {'cpu_us': 500}, 'interval': {'lower_us': 1000},
                         'closeout': dict.fromkeys(closeout.prior.FLAGS, True)})
        self.bundle = {'receipts': rows, 'audit': dict.fromkeys(
            ('checked_visits', 'replayed_endpoints', 'replayed_draw_records'), 4),
            'analysis': {'complete_endpoints': True, 'complete_draws': True, 'measured_formal_draws': 3}}
        for patch in (mock.patch.dict(os.environ, {'CODEX_THREAD_ID': self.owner}),
                      mock.patch.object(closeout, 'WORK', self.work), mock.patch.object(closeout, 'SHARED', self.shared),
                      mock.patch.object(closeout, 'audit_module', return_value=SimpleNamespace(audit=self.audit))):
            patch.start()
            self.addCleanup(patch.stop)

    def audit(self, work, through):
        self.assertEqual((work, through), (self.work, 4))
        return self.bundle

    def finish(self, apply=False):
        return closeout.finalize(self.work, self.shared, apply)

    def test_default_is_read_only_and_apply_is_idempotent(self):
        before = set(self.work.rglob('*'))
        ready = self.finish()
        self.assertEqual(ready['state'], 'ready_dry_run')
        self.assertEqual(set(self.work.rglob('*')), before)
        self.assertEqual(self.finish(True)['state'], 'published')
        self.assertEqual(before - set(self.work.rglob('*')), {self.payload})
        shared_receipt = self.shared / ready['receipt']
        data = shared_receipt.read_bytes()
        self.assertIn(b'independently_replayed_business_endpoints_and_draw_records', data)
        for secret in (self.cohort, self.owner, 'payload.tar.gz', str(self.work)):
            self.assertNotIn(secret.encode(), data)
        self.assertEqual(self.finish(True)['state'], 'already_published')
        self.assertEqual(shared_receipt.read_bytes(), data)

    def test_mandatory_draw_cpu_and_verifiable_return_are_required(self):
        for field, mutate in (
                ('draw_cpu', lambda bundle: bundle['receipts'][0]['draw'].update(cpu_us=None)),
                ('separate_absence', lambda bundle: bundle['receipts'][3]['closeout'].update(absent=False)),
                ('missing_replay', lambda bundle: bundle['audit'].update(replayed_draw_records=3)),
                ('incomplete_formals', lambda bundle: bundle['analysis'].update(complete_draws=False))):
            original = copy.deepcopy(self.bundle)
            mutate(self.bundle)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.finish(True)
            self.bundle = original
        self.assertTrue(self.payload.exists())
        self.assertFalse((self.work / closeout.JOURNAL).exists())
        self.assertFalse(list(self.shared.iterdir()))

    def assert_owner_denied_without_mutation(self):
        before = set(self.work.rglob('*'))
        payload = self.payload.read_bytes()
        for apply in (False, True):
            with mock.patch.object(closeout.prior, 'finalize_authorized') as finalize_authorized:
                with self.assertRaises(ValueError):
                    self.finish(apply)
                finalize_authorized.assert_not_called()
            self.assertEqual(set(self.work.rglob('*')), before)
            self.assertEqual(self.payload.read_bytes(), payload)
            self.assertFalse((self.work / closeout.JOURNAL).exists())
            self.assertFalse(list(self.shared.iterdir()))

    def test_missing_invalid_or_different_environment_owner_is_denied(self):
        for owner in (None, '', 'not-an-owner', 'ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF',
                      '00000000-0000-4000-8000-000000000002'):
            with self.subTest(owner=owner), mock.patch.dict(os.environ):
                if owner is None:
                    os.environ.pop('CODEX_THREAD_ID', None)
                else:
                    os.environ['CODEX_THREAD_ID'] = owner
                self.assert_owner_denied_without_mutation()

    def test_missing_invalid_or_different_plan_owner_is_denied(self):
        path = self.work / 'plan/plan.json'
        original = json.loads(path.read_text())
        for owner in (None, 'invalid', '00000000-0000-4000-8000-000000000002'):
            with self.subTest(owner=owner):
                plan = dict(original)
                if owner is None:
                    del plan['owner']
                else:
                    plan['owner'] = owner
                path.write_text(json.dumps(plan))
                self.assert_owner_denied_without_mutation()
        path.write_text(json.dumps(original))


if __name__ == '__main__':
    unittest.main()
