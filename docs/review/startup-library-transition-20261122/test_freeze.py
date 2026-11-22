"""Off-device single-payload tests against the approved stock-card fixtures."""

import json
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
import unittest

import freeze


@unittest.skipUnless(freeze.PREVIOUS.is_dir(), 'closed test-env launch receipts are required')
class FreezeTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.work = Path(scratch.name).resolve()
        self.scripts = self.work / 'inputs'
        self.scripts.mkdir(mode=0o700)
        for name in ('device.sh', 'submission.sh'):
            (self.scripts / name).write_text('exit 0\n')
        for name in ('host.py', 'analyze.py'):
            (self.scripts / name).write_text('pass\n')
        self.program = self.work / 'transition-app'
        ident = b'\x7fELF\x01\x01\x01' + bytes(9)
        header = struct.pack('<HHIIIIIHHHHHH', 2, 40, 1, 0, 52, 0, 0x05000400, 52, 32, 1, 0, 0, 0)
        self.program.write_bytes(ident + header + struct.pack('<8I', 1, 0, 0, 0, 0, 0, 0, 0) + b'transition')

    def seal(self):
        return freeze.freeze(self.work, freeze.PREVIOUS, self.scripts, self.program)

    def test_seals_one_program_and_exact_stock_sources_for_four_slots(self):
        record = self.seal()
        prior = freeze.PREVIOUS / 'plan'
        plan = self.work / 'plan'
        self.assertEqual((record['configuration'], record['visits']), ('diagnostic', 4))
        self.assertEqual(set(record), {'schema', 'cohort', 'owner', 'origin', 'configuration', 'visits'})
        self.assertRegex(record['cohort'], r'^[0-9a-f]{16}$')
        self.assertNotEqual(record['cohort'], json.loads((prior / 'plan.json').read_bytes())['cohort'])
        self.assertEqual((plan / 'ready').read_bytes(), b'ready\n')
        self.assertEqual((plan / 'common.py').stat().st_mode & 0o777, 0o700)
        self.assertFalse(any((plan / arm).exists() for arm in ('A', 'B')))
        for name, source in freeze.STAGE_ASSETS.items():
            self.assertEqual((plan / name).read_bytes(), (prior / source).read_bytes())
        with tarfile.open(plan / 'payload.tar.gz') as archive:
            names = archive.getnames()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual([name for name in names if name.startswith('programs/')], ['programs/selected-app'])
            self.assertEqual([name for name in names if name.endswith('/launch.sh')], ['control/launch.sh'])
            for name, source in (('guard/guard.sh', prior / 'guard.sh'), ('programs/selected-app', self.program),
                                 ('submission/submission.sh', self.scripts / 'submission.sh')):
                self.assertEqual(archive.extractfile(name).read(), source.read_bytes())
        with self.assertRaises(FileExistsError):
            self.seal()
        self.assertTrue((plan / 'ready').is_file())

    def test_shell_preflight_does_not_leave_an_incomplete_plan(self):
        (self.scripts / 'submission.sh').write_text('if\n')
        with self.assertRaises(subprocess.CalledProcessError):
            self.seal()
        self.assertFalse((self.work / 'plan').exists())


if __name__ == '__main__':
    unittest.main()
