#!/usr/bin/env python3
"""Rehearse the complete measurement packer without creating a live guard."""

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tarfile
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
PREP = runpy.run_path(str(HERE/'prepare_stock_generation.py'))
RUN = runpy.run_path(str(HERE/'run_stock_generation.py'))
private_path = PREP['private_path']


class SealingTests(unittest.TestCase):
    def test_real_workspace_cannot_seal_without_a_closed_locator(self):
        self.assertFalse((HERE/'measure').exists())
        self.assertFalse((HERE/'fresh-artwork.guard').exists())
        self.assertFalse((HERE/'locator-sent.json').exists())
        with self.assertRaises((ValueError, FileNotFoundError)):
            PREP['seal'](HERE,private_path('historical_fixture')/'attempt-0000000000000000')
        with self.assertRaises((ValueError, FileNotFoundError)):
            RUN['verify_locator'](HERE)
        self.assertFalse((HERE/'measure').exists())

    def test_full_measurement_envelope_in_disposable_directory_only(self):
        frame, = (p for p in private_path('historical_fixture').glob('attempt-*/locator.frame') if (p.parent/'released').is_file()
                  and json.loads((p.parent/'result.json').read_bytes())['complete'])
        # Use the production image classifier, but do not copy this frame into
        # the real workspace or forge the closed-locator evidence gate there.
        classifier = ['/opt/kindle-toolchain/ubuntu-run',str(private_path('classifier')),
                      'xvfb-run','-a','-s','-screen 0 1860x2480x24','python3','-B','freeze_target.py','--classify']
        with frame.open('rb') as image:
            classified = subprocess.run(classifier, stdin=image, capture_output=True, timeout=70)
        self.assertEqual(classified.returncode,0,classified.stderr.decode(errors='replace')[-600:])
        with tempfile.TemporaryDirectory(prefix='generation-seal-host-fixture-') as directory:
            root=Path(directory)
            for name in ('measure-template.sh','diagnostic-launch.sh'):
                shutil.copyfile(HERE/name,root/name)
            guard=root/'fresh-artwork.guard'
            guard.write_bytes(PREP['artwork'](classified.stdout))
            out=PREP['stage'](root,'measure',guard)
            source, frame_path, space=RUN['check_plan'](root,'measure')
            self.assertEqual(out['framed_bytes'],frame_path.stat().st_size)
            self.assertEqual(out['space_kib'],space)
            self.assertEqual(source.count(b'input/dispatch.sh" tap '),1)
            self.assertEqual(source.count(b'mount -o bind'),1)
            self.assertNotIn(b'touchScreenSaverTimeout',source)
            self.assertIn(b'cmp -s "$helper" "$stage/reference/query.elf"',source)
            with frame_path.open('rb') as stream:
                self.assertEqual(stream.read(len(source)),source)
                with tarfile.open(fileobj=stream,mode='r|gz') as archive:
                    member = next(member for member in archive if member.name == 'input/navigation.sh')
                    nav = archive.extractfile(member).read()
            self.assertNotIn(b'card-input',nav)
            self.assertNotIn(b'move_to',nav)
            self.assertIn(b'[ "$operation" = inspect ] || fail operation',nav)
            self.assertIn(b'native && timeout -k 1 12',nav)
        self.assertFalse((HERE/'measure').exists())
        self.assertFalse((HERE/'fresh-artwork.guard').exists())


if __name__ == '__main__':
    if os.name != 'posix' or HERE.parent != Path('/root'):
        raise SystemExit('Use a private test-env stock-generation directory')
    unittest.main(verbosity=2)
