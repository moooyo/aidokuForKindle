"""Synthetic promotion coordinator checks. The fake transport has no SSH capability."""

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


path = Path(__file__).with_name('run.py')
spec = importlib.util.spec_from_file_location('promotion_driver', path)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
TX = 'promote-000000000001'


class Session:
    def __init__(self, path, fail=()):
        self.batch = path
        self.run = TX
        self.calls = []
        self.fail = set(fail)
        self.released = False

    def connect(self):
        if 'lease-held' in self.fail:
            raise RuntimeError('Another task is active')
        return {'ok': True}

    def release(self):
        self.released = True

    def call(self, name, script, stdin=None, timeout=None):
        self.calls.append({'operation': name})
        subprocess.run(['sh', '-n'], input=script.encode(), check=True)
        if name in self.fail:
            raise RuntimeError(name)
        if name == 'upload':
            assert stdin is not None
        if name in ('native-before', 'baseline-restored', 'restored', 'final-native'):
            return b'matching-native-state\n'
        if name in ('light-before-restore', 'light-after-restore', 'lighting-before'):
            return b'8\n3\n'
        if name == 'stage-after-interruption':
            return b'OWNED\n'
        if name == 'right-toolbar-observations':
            return (b'930\t0\t930\t72\t200880\t0\t255\t0\t255\t0\t255\t' + b'a'*64 + b'\n') * 3
        return {'baseline-smoke': b'BASELINE_SMOKE_OK\n', 'installed-smoke': b'CANDIDATE_SMOKE_OK\n',
                'install': b'INSTALLED\n', 'rollback': b'ROLLED_BACK\n',
                'recover-owned-application': b'RECOVERED\n'}.get(name, b'')


class CoordinatorChecks(unittest.TestCase):
    def flow(self, fail=(), install=True):
        with tempfile.TemporaryDirectory() as root:
            session = Session(Path(root), fail)
            files = {key: (b'mocked artifact', 0o700) for key in ('owner', 'smoke.sh', 'transaction.sh', 'capture', 'changes.tsv')}
            wanted = {**driver.DEPLOYED, driver.APP: driver.sha(b'new')}
            result = driver.perform(session, b'payload', files, TX, wanted, install)
            names = [c['operation'] for c in session.calls]
            self.assertEqual(result['lease_released'], session.released)
            self.assertTrue((Path(root) / 'report.json').is_file())
            return result, names

    def test_default_app_is_qualified_after_changes(self):
        result, calls = self.flow()
        self.assertTrue(result['success'] and result['qualified'] and result['lease_released'])
        self.assertLess(calls.index('baseline-smoke'), calls.index('install'))
        self.assertLess(calls.index('install'), calls.index('installed-smoke'))
        self.assertIn('protection-after', calls)
        self.assertNotIn('undo-program', calls)

    def test_preflight_mismatch_never_starts_app_or_mutates_program(self):
        result, calls = self.flow({'protection-before'})
        self.assertFalse(result['success'])
        self.assertTrue(result['lease_released'])
        self.assertNotIn('install', calls)
        self.assertNotIn('create-stage', calls)
        self.assertNotIn('recover-owned-application', calls)

    def test_installation_smoke_failure_restores_old_program_and_lighting(self):
        result, calls = self.flow({'installed-smoke'})
        self.assertFalse(result['success'])
        self.assertTrue(result['lease_released'])
        self.assertLess(calls.index('recover-owned-application'), calls.index('undo-program'))
        self.assertLess(calls.index('original-program-restored'), calls.index('light-after-restore'))

    def test_unknown_app_or_foreign_program_retains_recovery_journal(self):
        for issue in ('recover-owned-application', 'undo-program'):
            with self.subTest(issue):
                result, calls = self.flow({'installed-smoke', issue})
                self.assertFalse(result['success'] or result['lease_released'])
                self.assertTrue(result['recovery_needed'])
                if issue == 'recover-owned-application':
                    self.assertNotIn('undo-program', calls)
                self.assertNotIn('remove-stage', calls)

    def test_failed_install_keeps_prior_state_and_explicitly_checks_baseline(self):
        result, calls = self.flow({'install'})
        self.assertFalse(result['success'])
        self.assertTrue(result['lease_released'])
        self.assertIn('original-program-restored', calls)
        self.assertNotIn('installed-smoke', calls)

    def test_mid_upload_or_stage_create_never_runs_untrusted_helpers(self):
        for where in ('upload', 'verify-helpers', 'create-stage'):
            with self.subTest(where):
                result, calls = self.flow({where})
                self.assertFalse(result['success'])
                self.assertTrue(result['lease_released'])
                self.assertNotIn('baseline-smoke', calls)
                self.assertNotIn('recover-owned-application', calls)
                self.assertIn('remove-stage', calls)

    def test_denied_lease_always_leaves_other_owner_alone(self):
        result, calls = self.flow({'lease-held'})
        self.assertFalse(result['success'] or result['lease_released'])
        self.assertEqual(calls, [])

    def test_rollback_uses_only_recorded_program_journal(self):
        result, calls = self.flow(install=False)
        self.assertTrue(result['success'] and result['lease_released'])
        self.assertFalse(result['qualified'])
        self.assertNotIn('baseline-smoke', calls)
        self.assertNotIn('installed-smoke', calls)
        self.assertIn('rollback', calls)


class FrozenInputChecks(unittest.TestCase):
    def test_right_toolbar_must_have_matching_color_variation_inside_an_rgb_channel(self):
        good = '930\t0\t930\t72\t200880\t0\t255\t0\t0\t8\t8\t' + 'a'*64 + '\n'
        flat = good.replace('\t0\t255\t','\t0\t0\t')
        self.assertTrue(driver.matching_toolbar_observations((good*3).encode()))
        for bad in (flat*3, good*2+good.replace('a'*64,'b'*64), (good.replace('\t72\t','\t71\t'))*3):
            with self.assertRaises(ValueError):
                driver.matching_toolbar_observations(bad.encode())

    def fixture(self, path):
        name, blob = driver.APP, b'qualification fixture'
        dest = path / 'delta' / name
        dest.parent.mkdir(parents=True)
        dest.write_bytes(blob)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(name, blob)
        package = stream.getvalue()
        (path / 'aidoku-startup-next.zip').write_bytes(package)
        (path / 'aidoku-startup-next.kpkg').write_bytes(b'kpm')
        return {'schema': 1, 'catalog_registry': driver.REGISTRATION, 'evidence_sha256': {
            'pixels': driver.sha(b'pixels'), 'lifecycle': driver.sha(b'lifecycle')},
            'archive_sha256': driver.sha(package), 'kpm_sha256': driver.sha(b'kpm'),
            'changes': [{'path': name, 'file': 'delta/' + name, 'new': driver.sha(blob), 'old': driver.DEPLOYED[name]}]}

    def test_device_inputs_exclude_shared_registration_and_library_document(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); plan = self.fixture(root)
            file = root / 'plan.json'; file.write_text(json.dumps(plan))
            blobs, actual = driver.freeze(file, driver.sha(file.read_bytes()))
            self.assertEqual(set(blobs), {'changes.tsv', 'payload/' + driver.APP})
            self.assertNotEqual(actual[driver.APP], driver.DEPLOYED[driver.APP])
            for unsafe in ('documents/Aidoku.sh', '../etc/shadow', driver.REGISTRATION):
                with self.subTest(unsafe):
                    plan['changes'][0]['path'] = unsafe
                    file.write_text(json.dumps(plan))
                    with self.assertRaises(ValueError):
                        driver.freeze(file, driver.sha(file.read_bytes()))

    def test_corrupt_delta_and_changed_plan_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); plan = self.fixture(root)
            file = root / 'plan.json'; file.write_text(json.dumps(plan))
            with self.assertRaises(ValueError):
                driver.freeze(file, driver.sha(b'wrong plan'))
            (root / 'delta' / driver.APP).write_bytes(b'changed')
            with self.assertRaises(ValueError):
                driver.freeze(file, driver.sha(file.read_bytes()))

    def test_retired_cold_only_binary_cannot_be_submitted_under_a_new_plan(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); plan = self.fixture(root)
            plan['changes'][0]['new'] = 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6'
            file = root/'plan.json'; file.write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, 'cold-only application cannot be deployed'):
                driver.freeze(file, driver.sha(file.read_bytes()))


if __name__ == '__main__':
    unittest.main()
