"""Exercise the single-program archive with the approved stock-card sources."""

import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest

import freeze


@unittest.skipUnless(freeze.PREVIOUS.is_dir(), 'approved test-env receipts are required')
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
        self.program = self.work / 'diagnostic-app'
        ident = b'\x7fELF\x01\x01\x01' + bytes(9)
        header = struct.pack('<HHIIIIIHHHHHH', 2, 40, 1, 0, 52, 0, 0x05000400, 52, 32, 1, 0, 0, 0)
        self.program.write_bytes(ident + header + struct.pack('<8I', 1, 0, 0, 0, 0, 0, 0, 0) + b'diagnostic')

    def freeze(self):
        return freeze.freeze(self.work, freeze.PREVIOUS, self.scripts, self.program)

    def test_private_single_payload_reuses_approved_guard_and_selector(self):
        receipt = self.freeze()
        target = self.work / 'plan'
        prior = freeze.PREVIOUS / 'plan'
        self.assertRegex(receipt['cohort'], r'^[0-9a-f]{16}$')
        self.assertNotEqual(receipt['cohort'], json.loads((prior / 'plan.json').read_bytes())['cohort'])
        self.assertEqual((receipt['configuration'], receipt['visits']), ('diagnostic', 5))
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertEqual((target / 'ready').read_bytes(), b'ready\n')
        self.assertEqual((target / 'common.py').stat().st_mode & 0o777, 0o700)
        self.assertEqual((target / 'jpeg_host.py').read_bytes(), (prior / 'host.py').read_bytes())
        self.assertFalse(any((target / arm).exists() for arm in ('A', 'B')))
        with tarfile.open(target / 'payload.tar.gz') as archive:
            names = archive.getnames()
            self.assertEqual(len(names), len(set(names)))
            self.assertEqual([name for name in names if name.startswith('programs/')], ['programs/selected-app'])
            self.assertEqual([name for name in names if name.endswith('/launch.sh')], ['control/launch.sh'])
            self.assertEqual(archive.extractfile('programs/selected-app').read(), self.program.read_bytes())
            self.assertEqual(archive.extractfile('guard/guard.sh').read(), (prior / 'guard.sh').read_bytes())
            self.assertEqual(archive.extractfile('selector/selector.sh').read(),
                             (freeze.PREVIOUS / 'inputs/selector.sh').read_bytes())
        with self.assertRaises(FileExistsError):
            self.freeze()
        self.assertTrue((target / 'ready').is_file())

    def test_bad_program_does_not_publish_a_plan(self):
        self.program.write_bytes(b'not an ARM executable')
        with self.assertRaisesRegex(Exception, 'expected_elf32_le'):
            self.freeze()
        self.assertFalse((self.work / 'plan').exists())


if __name__ == '__main__':
    unittest.main()
