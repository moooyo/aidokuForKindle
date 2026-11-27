#!/usr/bin/env python3
"""Check the actual 5.20.1 BusyBox timeout applets offline under QEMU."""

from pathlib import Path
import subprocess
import tempfile
import time
import unittest


TOOLING = Path('/work/tooling')


class FirmwareTimeout(unittest.TestCase):
    def check_applet(self, profile):
        self.assertEqual(Path.cwd(), TOOLING.parent, 'Run through the isolated Ubuntu toolchain on test-env')
        binary = TOOLING/f'{profile}-busybox'
        self.assertTrue(binary.is_file() and not binary.is_symlink())
        with tempfile.TemporaryDirectory(prefix='firmware-timeout-fixture-') as work:
            applet = Path(work)/'busybox'
            applet.symlink_to(binary)
            cmd = ['qemu-arm', TOOLING/'lib/ld-linux-armhf.so.3', '--library-path', TOOLING/'lib', applet, 'timeout', '-k', '1']
            subprocess.run([*cmd, '2', '/bin/true'], check=True, capture_output=True, timeout=6)
            started = Path(work)/'child-started'
            start = time.monotonic()
            result = subprocess.run([*cmd, '1', '/bin/sh', '-c', 'trap "" TERM; : >"$1"; exec sleep 15',
                                     'timeout-fixture', started], capture_output=True, timeout=8)
            self.assertTrue(started.is_file())
            self.assertNotEqual(result.returncode, 0, result)
            self.assertLess(time.monotonic()-start, 8)

    def test_scribe_5201_stops_a_direct_term_ignoring_command(self):
        self.check_applet('kindle-scribe-1')

    def test_pw6_5201_stops_a_direct_term_ignoring_command(self):
        self.check_applet('kindle-paperwhite-6')


if __name__ == '__main__':
    unittest.main(verbosity=2)
