import json
import os
import unittest
from unittest import mock

import closeout
import test_host as fixtures


class FinalReturn(unittest.TestCase):
    def setUp(self):
        runner = fixtures.Runner()
        runner.setUp()
        self.addCleanup(runner.doCleanups)
        self.runner, self.work = runner, runner.work
        self.shared = self.work / 'shared-fixture'
        self.shared.mkdir(mode=0o700)
        self.payload = self.work / 'plan/payload.tar.gz'
        self.payload.chmod(0o600)
        self.stamp = '2026-01-01T20:04:05.123456+00:00'
        self.filename = 'aidoku-library-transition-return-20260102-040405.json'
        for patch in (mock.patch.object(closeout, 'WORK', self.work), mock.patch.object(closeout, 'SHARED', self.shared)):
            patch.start()
            self.addCleanup(patch.stop)

    def prepare(self, count=4):
        self.runner.line = fixtures.TRANSITION
        for slot in range(1, count + 1):
            self.assertEqual(self.runner.call('run', slot)[0], 0)
            folder, = (self.work / 'visits').glob(f'{slot:02}-*')
            record = self.runner.frozen.engine.read_json(folder / 'operations.json')
            record['release']['released_at_utc'] = self.stamp.replace('20:04:', f'20:0{slot}:')
            self.runner.frozen.common.checkpoint(folder / 'operations.json', record)

    def call(self, apply=False):
        return closeout.finalize(self.work, self.shared, apply)

    def test_dry_run_and_atomic_return_only_remove_the_single_consumed_file(self):
        self.prepare()
        size = self.payload.stat().st_size
        retained = self.work / 'plan/keep.tar.gz'
        retained.write_bytes(b'keep the unrelated sibling')
        before = set(self.work.rglob('*'))
        ready = self.call()
        self.assertEqual(set(self.work.rglob('*')), before)
        self.assertEqual((ready['state'], ready['last_released_at_utc'], ready['receipt']), ('ready_dry_run', self.stamp, self.filename))
        result = self.call(True)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(before - set(self.work.rglob('*')), {self.payload})
        self.assertEqual(retained.read_bytes(), b'keep the unrelated sibling')
        self.assertEqual(set(self.shared.iterdir()), {self.shared / self.filename})
        receipt = (self.shared / self.filename).read_bytes()
        row = json.loads(receipt)
        self.assertEqual((row['removed_consumed_upload_bytes'], row['last_released_at_utc']), (size, self.stamp))
        self.assertEqual(row['independently_replayed_business_endpoints_and_transitions'], 4)
        for private in (fixtures.OWNER.encode(), b'mock-private', b'run_id', b'/var/tmp', b'payload.tar.gz'):
            self.assertNotIn(private, receipt)
        self.assertEqual(self.call(True)['state'], 'already_published')
        self.assertEqual((self.shared / self.filename).read_bytes(), receipt)

    def test_no_partial_cohort_or_dirty_or_duplicate_evidence(self):
        self.prepare(3)
        with self.assertRaises(ValueError):
            self.call(True)
        self.assertTrue(self.payload.exists())
        self.assertFalse(list(self.shared.iterdir()))

    def test_refuses_wrong_owner_or_receipt_without_actual_release_clock(self):
        self.prepare()
        final, = (self.work / 'visits').glob('04-*')
        file = final / 'operations.json'
        for field, value in (('owner_and_token_checked', False), ('released_at_utc', None),
                             ('released_at_utc', '2026-01-01T21:00:00'), ('released_at_utc', '2025-01-01T20:00:00+00:00')):
            original = self.runner.frozen.engine.read_json(file)
            changed = self.runner.frozen.engine.read_json(file)
            changed['release'][field] = value
            self.runner.frozen.common.checkpoint(file, changed)
            with self.subTest(field=field, value=value), self.assertRaises(Exception):
                self.call(True)
            self.runner.frozen.common.checkpoint(file, original)
        self.assertTrue(self.payload.exists())
        self.assertFalse(any(self.shared.iterdir()))

    def test_rejects_public_collision_and_unlisted_paths(self):
        self.prepare()
        (self.shared / self.filename).write_bytes(b'never overwrite existing evidence')
        with self.assertRaises(ValueError):
            self.call(True)
        self.assertTrue(self.payload.exists())
        self.assertEqual((self.shared / self.filename).read_bytes(), b'never overwrite existing evidence')
        with self.assertRaises(ValueError):
            closeout.finalize(self.work.parent, self.shared, True)
        with self.assertRaises(ValueError):
            closeout.finalize(self.work, self.work, True)

    def test_rejects_symlink_or_linked_payload_and_unknown_attempt(self):
        self.prepare()
        alien = self.work / 'outside-payload'
        self.payload.rename(alien)
        self.payload.symlink_to(alien)
        with self.assertRaises(ValueError):
            self.call(True)
        self.payload.unlink()
        os.link(alien, self.payload)
        with self.assertRaises(ValueError):
            self.call(True)
        self.payload.unlink()
        alien.rename(self.payload)
        (self.work / 'visits/05-unplanned').mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            self.call(True)
        self.assertTrue(self.payload.exists())
        self.assertFalse(any(self.shared.iterdir()))

    def test_can_finish_an_interrupted_publish_without_fabricating_payload_absence(self):
        self.prepare()
        with mock.patch.object(closeout, 'publish_once', side_effect=OSError('fixture-interrupted')):
            with self.assertRaises(OSError):
                self.call(True)
        self.assertFalse(self.payload.exists())
        self.assertTrue((self.work / closeout.JOURNAL).is_file())
        self.assertFalse(any(self.shared.iterdir()))
        self.assertEqual(self.call(True)['state'], 'published')
        (self.work / closeout.JOURNAL).unlink()
        with self.assertRaises(ValueError):
            self.call(True)


if __name__ == '__main__':
    unittest.main()
