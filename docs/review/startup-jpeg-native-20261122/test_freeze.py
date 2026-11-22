"""Off-device freeze regressions with synthetic approval and release evidence."""

import importlib.util
import json
import os
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest import mock

import freeze


FIXTURE_OWNER = '11111111-1111-4111-8111-111111111111'
PRIOR_OWNER = '22222222-2222-4222-8222-222222222222'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def executable(marker, *, flags=0x05000400, segment=1, machine=40):
    ident = b'\x7fELF\x01\x01\x01' + bytes(9)
    header = struct.pack('<HHIIIIIHHHHHH', 2, machine, 1, 0, 52, 0, flags, 52, 32, 1, 0, 0, 0)
    return ident + header + struct.pack('<8I', segment, 0, 0, 0, 0, 0, 0, 0) + marker


class FreezeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.scripts = self.work / 'inputs'
        self.scripts.mkdir(mode=0o700)
        for name in freeze.HOST_INPUTS + tuple(freeze.PAYLOAD_INPUTS.values()):
            data = b'exit 0\n' if name.endswith('.sh') else Path(freeze.__file__).with_name(name).read_bytes()
            (self.scripts / name).write_bytes(data)
        self.first, self.second = (self.work / 'control', self.work / 'candidate')
        self.first.write_bytes(executable(b'first'))
        self.second.write_bytes(executable(b'second'))
        review = Path(freeze.__file__).resolve().parents[1]
        self.rendered = load_module('fixture_rendered', review / 'startup-followup-20261121/rendered-library/host.py')
        self.common = self.rendered.common
        self.prior = self.work / 'prior'
        self.legacy = self.work / 'legacy'
        for directory in (self.prior, self.legacy, self.prior / 'plan', self.prior / 'attempt',
                          self.prior / 'plan/tools', self.legacy / 'tools', self.legacy / 'operator',
                          self.legacy / 'operator/programs', self.legacy / 'operator/programs/payload'):
            directory.mkdir(mode=0o700)
        self.prior_plan = self.prior / 'plan'
        guard = self.prior_plan / 'guard.sh'
        (self.prior_plan / 'ready').write_bytes(b'ready\n')
        (self.prior_plan / 'host.py').write_bytes(Path(self.rendered.__file__).read_bytes())
        (self.prior_plan / 'common.py').write_bytes(Path(self.common.__file__).read_bytes())
        for path in (guard, self.prior_plan / 'device.sh', self.work / 'dispatch.sh', self.work / 'visit.sh'):
            path.write_bytes(b'exit 0\n')
        for name in freeze.TOOL_NAMES:
            for directory in (self.prior_plan / 'tools', self.legacy / 'tools'):
                (directory / name).write_bytes(b'pass\n')
        (self.legacy / 'operator/programs/payload/rename-new').write_bytes(b'test fixture\n')
        modes = self.work / 'program-modes'
        modes.write_bytes(b'755\n755\n755\n')
        sources = {'guard/guard.sh': guard, 'input/render-watch': self.first, 'input/reference.rule': modes,
                   'control/program-modes': modes, 'control/launch.sh': self.prior_plan / 'device.sh'}
        self.saved = {'schema': 1, 'owner': PRIOR_OWNER, 'origin': 'stock-tap', 'label': 'a' * 16,
                      'cases': list(self.rendered.CASES), 'legacy': str(self.legacy), 'reference': str(self.work / 'ref'),
                      'navigation': str(self.work / 'nav'), 'guard': str(guard), 'approved_guard': str(guard),
                      'visit': str(self.work / 'visit.sh'), 'dispatch': str(self.work / 'dispatch.sh'),
                      'watcher': str(self.first), 'sources': {name: str(path) for name, path in sources.items()}}
        self.save_prior_plan()
        request = {'owner_thread': PRIOR_OWNER, 'run_id': self.rendered.RUN_PREFIX + self.saved['label']}
        identity = {**request, 'project': 'Aidoku', 'lease_token_sha256': 'fixture-identity'}
        self.report = {'schema': 1, 'request': request, 'phase': 'release', 'completed': self.saved['cases'],
                       'recovery_required': False, 'released': True, 'close_confirmed': True,
                       'absence_confirmed': True, 'operations_passed': True, 'identity': identity,
                       'acquisition': {**identity, 'ok': True, 'operation': 'acquire', 'state': 'held'},
                       'release': {**identity, 'ok': True, 'operation': 'release', 'state': 'released',
                                   'owner_and_token_checked': True, 'lease_directory_absent_after_release': True,
                                   'native_restored_and_connections_closed': True}}
        (self.prior / 'attempt/operations.json').write_text(json.dumps(self.report))
        for patch in (mock.patch.object(self.common, 'closed_reference'),
                      mock.patch.object(self.rendered, 'input_sources', side_effect=lambda *args: sources.copy())):
            patch.start()
            self.addCleanup(patch.stop)

    def save_prior_plan(self):
        (self.prior_plan / 'plan.json').write_text(json.dumps(self.saved))

    def freeze(self, owner=FIXTURE_OWNER):
        with mock.patch.dict(os.environ):
            if owner is None:
                os.environ.pop('CODEX_THREAD_ID', None)
            else:
                os.environ['CODEX_THREAD_ID'] = owner
            module = load_module('fixture_freeze', freeze.__file__)
        with mock.patch.object(module, 'load_rendered', return_value=self.rendered) as load_rendered:
            try:
                return module.freeze(self.work, self.prior, self.scripts, self.first, self.second)
            finally:
                if module.OWNER is None:
                    load_rendered.assert_not_called()

    def test_missing_or_invalid_environment_owner_refuses_without_plan(self):
        for owner in (None, '', 'not-a-thread-id', FIXTURE_OWNER + '\n'):
            with self.subTest(owner=owner), self.assertRaisesRegex(ValueError, 'designated_operator_required'):
                self.freeze(owner)
        self.assertFalse((self.work / 'plan').exists())

    def test_prior_plan_owner_must_match_prior_release(self):
        self.saved['owner'] = FIXTURE_OWNER
        self.save_prior_plan()
        with self.assertRaisesRegex(self.common.Stop, 'invalid_plan'):
            self.freeze()
        self.assertFalse((self.work / 'plan').exists())

    def test_matching_environment_does_not_replace_release_verification(self):
        self.report['release']['owner_and_token_checked'] = False
        (self.prior / 'attempt/operations.json').write_text(json.dumps(self.report))
        with self.assertRaisesRegex(self.common.Stop, 'prior_batch_not_closed'):
            self.freeze()
        self.assertFalse((self.work / 'plan').exists())

    def test_freeze_only_selects_one_program_per_arm(self):
        receipt = self.freeze()
        target = self.work / 'plan'
        self.assertRegex(receipt['cohort'], r'^[0-9a-f]{16}$')
        self.assertEqual(receipt['owner'], FIXTURE_OWNER)
        self.assertEqual(json.loads((target / 'plan.json').read_bytes())['owner'], FIXTURE_OWNER)
        host = load_module('fixture_jpeg_host', target / 'host.py')
        self.assertEqual(host.checked_plan(self.work, FIXTURE_OWNER), receipt)
        with self.assertRaisesRegex(host.common.Stop, 'invalid_plan'):
            host.checked_plan(self.work, PRIOR_OWNER)
        self.assertEqual((target / 'ready').read_bytes(), b'ready\n')
        self.assertEqual((target.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((target / 'common.py').stat().st_mode & 0o777), 0o700)
        archive_a, archive_b = (target / arm / 'payload.tar.gz' for arm in ('A', 'B'))
        with tarfile.open(archive_a) as first, tarfile.open(archive_b) as second:
            names = first.getnames()
            self.assertEqual(names, second.getnames())
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual([name for name in names if name.startswith('programs/')], ['programs/selected-app'])
            self.assertEqual([name for name in names if name.endswith('/launch.sh')], ['control/launch.sh'])
            self.assertNotIn('input/render-watch', names)
            self.assertNotIn('input/reference.rule', names)
            for name in names:
                first_member, second_member = (archive.getmember(name) for archive in (first, second))
                self.assertTrue(first_member.isfile() and second_member.isfile())
                self.assertEqual(first_member.mode, second_member.mode)
                with first.extractfile(first_member) as data_a, second.extractfile(second_member) as data_b:
                    if name == 'programs/selected-app':
                        self.assertEqual(data_a.read(), self.first.read_bytes())
                        self.assertEqual(data_b.read(), self.second.read_bytes())
                    else:
                        self.assertTrue(self.common.same_bytes(data_a, data_b), name)
        with self.assertRaises(FileExistsError):
            self.freeze()
        self.assertTrue((target / 'ready').is_file())

    def test_elf_preflight_rejects_other_abis_and_dynamic_interpreter(self):
        for body in (b'not an ELF', executable(b'bad', flags=0x05000200),
                     executable(b'bad', machine=62), executable(b'bad', segment=3)):
            with self.subTest(body=body):
                self.second.write_bytes(body)
                with self.assertRaises(self.common.Stop):
                    freeze.program(self.common, self.second)

    def test_bad_script_does_not_publish_a_partial_plan(self):
        (self.scripts / 'selector.sh').write_text('if\n')
        with self.assertRaises(freeze.subprocess.CalledProcessError):
            self.freeze()
        self.assertFalse((self.work / 'plan').exists())


if __name__ == '__main__':
    unittest.main()
