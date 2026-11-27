#!/usr/bin/env python3
"""Replay the generated admission probe and hard deadline on actual firmware applets."""

import fcntl
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = Path('/work')
TOOLING = ROOT/'tooling/firmware-timeout'
PROBE = re.compile(r'^    \[ -x /bin/true \][^\n]*\n    probe=[^\n]*\|\| admission_reason timeout_selftest_failed\n', re.M)


class FirmwareTimeout(unittest.TestCase):
    def check_applet(self, profile, version):
        self.assertEqual(Path.cwd(), ROOT, 'Run through the isolated Ubuntu toolchain on test-env')
        files = TOOLING/profile
        binary = files/'busybox'
        loader = files/'ld-linux-armhf.so.3'
        for path in (binary, loader, files/'libc.so.6'):
            self.assertTrue(path.is_file() and not path.is_symlink(), path)
        for path in (binary, loader):
            self.assertTrue(os.access(path, os.X_OK), f'Fixture ELF is not executable: {path}')
        qemu = shutil.which('qemu-arm')
        self.assertIsNotNone(qemu)
        runtime = [qemu, str(loader), '--library-path', str(files)]
        busybox = [*runtime, str(binary)]

        def applet(*args):
            return subprocess.run([*busybox, *args], capture_output=True, timeout=7)

        header = applet('timeout', '--help')
        self.assertTrue(header.stderr.startswith(f'BusyBox v{version} '.encode()), header)
        probe, = PROBE.findall((ROOT/'measure-template.sh').read_text(encoding='ascii'))
        self.assertIn('timeout -s KILL 2 /bin/true', probe)
        with tempfile.TemporaryDirectory(prefix='firmware-timeout-fixture-') as work:
            root = Path(work)
            true = root/'true'
            sleep = root/'sleep'
            true.symlink_to(binary)
            sleep.symlink_to(binary)

            # QEMU user mode does not dispatch nested ARM execs here. Only the
            # exec trampoline is a host shell; ash, timeout, true and sleep
            # all run as the test profile's original ARM BusyBox.
            quick = root/'quick'
            quick.write_text('#!/bin/sh\nexec '+shlex.join([*runtime, str(true)])+'\n')
            quick.chmod(0o700)
            slow = root/'ignore-term'
            slow.write_text('#!/bin/sh\ntrap "" TERM\nprintf "entered\\n"\nexec '+
                            shlex.join([*runtime, str(sleep), '4'])+'\n')
            slow.chmod(0o700)
            ready = 'set -efu\nadmission_reason() { printf "%s\\n" "$1"; exit 1; }\n'
            check = ready + probe.replace('/bin/true', shlex.quote(str(quick))).replace(
                '$(timeout ', '$('+shlex.join([*busybox, 'timeout'])+' ') + 'printf "admitted\\n"\n'
            result = applet('sh', '-c', check)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'admitted\n', b''), result)
            if profile == 'scribe-5.19.5':
                original = applet('sh', '-c', check.replace('-s KILL 2', '-k 1 2'))
                self.assertEqual((original.returncode, original.stdout), (1, b'timeout_selftest_failed\n'))
                self.assertIn(b"timeout: invalid option -- 'k'", original.stderr)

            start = time.monotonic()
            stopped = applet('timeout', '-s', 'KILL', '1', str(slow))
            self.assertEqual((stopped.returncode, stopped.stdout, stopped.stderr), (-signal.SIGKILL, b'entered\n', b''), stopped)
            self.assertLess(time.monotonic()-start, 4, 'The direct process must not exit naturally')

            journal = root/'journal'
            journal.mkdir(mode=0o700)
            direct = shlex.join([*busybox, 'timeout', '-s', 'KILL', '13', str(quick)])
            # Mirror the read-only redirection. This independent operation
            # owns FD 9 and releases it on exit; its observer must not pin it.
            script = 'set -e; exec 9<"$1"; flock -n 9; exec '+direct+' 9<&-'
            closed = subprocess.run(['/bin/sh', '-c', script, 'fixture', str(journal)], capture_output=True, timeout=7)
            self.assertEqual((closed.returncode, closed.stdout, closed.stderr), (0, b'', b''), closed)
            fd = os.open(journal, os.O_RDONLY | os.O_DIRECTORY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)

            # Retain the lock for potentially mutating input. Its monitor
            # can outlive a successful command, but must retire promptly.
            retained = subprocess.run(['/bin/sh', '-c', script.removesuffix(' 9<&-'), 'fixture', str(journal)],
                                       capture_output=True, timeout=7)
            self.assertEqual((retained.returncode, retained.stdout, retained.stderr), (0, b'', b''), retained)
            fd = os.open(journal, os.O_RDONLY | os.O_DIRECTORY)
            try:
                deadline = time.monotonic()+2
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        self.assertLess(time.monotonic(), deadline, 'Input watchdog retained the journal past the settle bound')
                        time.sleep(.025)
            finally:
                os.close(fd)

    def test_scribe_5195_admits_and_hard_kills_a_direct_process(self):
        self.check_applet('scribe-5.19.5', '1.34.1')

    def test_scribe_5201_admits_and_hard_kills_a_direct_process(self):
        self.check_applet('scribe-5.20.1', '1.35.0')

    def test_pw6_5201_admits_and_hard_kills_a_direct_process(self):
        self.check_applet('pw6-5.20.1', '1.35.0')


if __name__ == '__main__':
    unittest.main(verbosity=2)
