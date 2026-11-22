"""Exercise the closed plan, single tiny fixture archive and bounded operator entry."""

from pathlib import Path
import struct
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import freeze


class SealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder, cls.rendered, cls.prior, cls.excluded, cls.real_sources = freeze.checked_inputs(freeze.PREVIOUS)
        cls.common = cls.rendered.common

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.work = Path(scratch.name).resolve()
        self.scripts = self.work / 'inputs'
        self.scripts.mkdir(mode=0o700)
        self.program = self.work / 'small-fixture-arm'
        ident = b'\x7fELF\x01\x01\x01' + bytes(9)
        header = struct.pack('<HHIIIIIHHHHHH', 2, 40, 1, 0, 52, 0, 0x05000400, 52, 32, 1, 0, 0, 0)
        self.program.write_bytes(ident + header + struct.pack('<8I', 1, 0, 0, 0, 0, 0, 0, 0) + b'fixture')
        self.scripts.joinpath('collect.py').write_text(
            'from pathlib import Path\nimport sys\n'
            'first, second, device, collector = map(Path, sys.argv[1:])\n'
            'device.write_bytes(first.read_bytes() + b"\\n")\n'
            'collector.write_bytes(second.read_bytes() + b"\\n")\n')
        for name, source in (('device.sh', self.prior / 'device.sh'),
                             ('submission.sh', freeze.PREVIOUS / 'inputs/submission.sh')):
            self.scripts.joinpath(name).write_bytes(source.read_bytes() + b'\n')
        for name in ('host.py', 'analyze.py'):
            self.scripts.joinpath(name).write_text('pass\n')

    def seal(self):
        subset = {name: self.real_sources[name] for name in ('guard/guard.sh', 'selector/selector.sh', 'control/launch.sh')}
        args = self.builder, self.rendered, self.prior, self.excluded, subset
        with mock.patch.object(freeze, 'checked_inputs', return_value=args):
            return freeze.freeze(self.work, freeze.PREVIOUS, self.scripts, self.program)

    def test_fixed_plan_reuses_closed_chain_without_building_another_large_test_payload(self):
        self.assertIn('rename-new', self.real_sources)
        self.assertNotIn('programs/selected-app', self.real_sources)
        plan = self.seal()
        self.assertEqual(set(plan), {'schema', 'cohort', 'owner', 'origin', 'configuration', 'visits', 'diagnostic'})
        self.assertEqual((plan['diagnostic'], plan['visits'], plan['configuration']), (freeze.PROBE, 4, 'diagnostic'))
        self.assertNotIn(plan['cohort'], self.excluded)
        target = self.work / 'plan'
        self.assertTrue((target / 'ready').is_file())
        for name, original in freeze.INHERITED.items():
            self.assertEqual((target / name).read_bytes(), (self.prior / original).read_bytes())
        self.assertEqual(list(target.rglob('*.tar.gz')), [target / 'payload.tar.gz'])
        with tarfile.open(target / 'payload.tar.gz') as archive:
            self.assertEqual(set(archive.getnames()), {'programs/selected-app', 'submission/submission.sh',
                                                       'guard/guard.sh', 'selector/selector.sh', 'control/launch.sh'})
            self.assertEqual(archive.extractfile('programs/selected-app').read(), self.program.read_bytes())
        self.assertFalse((target / 'A').exists() or (target / 'B').exists())
        self.assertEqual((self.scripts / 'run_slots.py').stat().st_mode & 0o777, 0o700)
        with self.assertRaises(FileExistsError):
            self.seal()

    def test_root_and_slots_are_bound_and_nonzero_child_immediately_stops(self):
        self.seal()
        entry = self.common.load_module('draw_fixture_driver', self.scripts / 'run_slots.py')
        calls = []
        def invoke(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return SimpleNamespace(returncode=4)
        self.assertEqual(entry.run(self.work, 1, 4, b'test-pipe\n', invoke), 4)
        self.assertEqual(len(calls), 1)
        args, options = calls[0]
        self.assertEqual(args[-4:], ['--slot', '1', '--secret-fd', '0'])
        self.assertNotIn('test-pipe', ' '.join(args))
        self.assertEqual(options['input'], b'test-pipe\n')
        for directory, first, last in ((self.work, 0, 1), (self.work, 1, 5), (self.work, 3, 1), (self.prior, 1, 1)):
            with self.subTest(first=first, last=last), self.assertRaises(ValueError):
                entry.run(directory, first, last, b'fixture\n', invoke)
        self.assertEqual(len(calls), 1)

    def test_wrong_arm_or_changed_device_script_is_rejected(self):
        source = self.program.read_bytes()
        self.program.write_bytes(b'not an arm program')
        with self.assertRaisesRegex(Exception, 'expected_elf32_le'):
            self.seal()
        self.program.write_bytes(source)
        self.scripts.joinpath('device.sh').write_text('exit 0\n')
        with self.assertRaisesRegex(Exception, 'unbound_collector_scripts'):
            self.seal()
        self.assertFalse((self.work / 'plan').exists())
        self.assertFalse((self.scripts / 'run_slots.py').exists())

    def test_existing_slot_driver_is_not_overwritten(self):
        original = self.scripts / 'run_slots.py'
        original.write_bytes(b'prior operator\n')
        with self.assertRaisesRegex(Exception, 'conflicting_slot_driver'):
            self.seal()
        self.assertFalse((self.work / 'plan').exists())
        self.assertEqual(original.read_bytes(), b'prior operator\n')

    def test_non_closed_archive_and_cohort_collision_are_rejected(self):
        attempted = next(iter(self.excluded))
        with mock.patch.object(freeze.secrets, 'token_hex', return_value=attempted), self.assertRaisesRegex(Exception, 'cohort_reused'):
            self.seal()
        self.assertFalse((self.work / 'plan').exists())
        plan = self.common.unique_json(self.common.read_small(self.prior / 'plan.json'))
        mock_report = {'receipts': [{'cohort': plan['cohort'], 'slot': slot, 'result': 'measured', 'transition': {},
                                     'closeout': {'released': slot != 4}} for slot in range(1, 5)],
                       'audit': dict.fromkeys(('checked_visits', 'replayed_endpoints', 'replayed_transition_records'), 4)}
        stages = SimpleNamespace(PREVIOUS=self.prior, load_previous=lambda _: self.builder)
        synthetic = SimpleNamespace(PREVIOUS=self.prior, load_previous=lambda _: stages)
        with mock.patch.object(freeze, 'load_previous', return_value=synthetic), \
                mock.patch.object(self.builder, 'load_rendered', return_value=self.rendered), \
                mock.patch.object(self.common, 'load_module', return_value=SimpleNamespace(audit=lambda *_: mock_report)), \
                self.assertRaisesRegex(Exception, 'previous_four_visits_not_replayed'):
            freeze.checked_inputs(freeze.PREVIOUS)


if __name__ == '__main__':
    unittest.main()
