"""Exercise the startup-only native portrait reader on the Linux test worker."""

import ctypes
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[2] / 'src/native_query/startup_portrait.c'
NAME = 'aidoku-startup-portrait'
NONCE = 'de12345678abcdef0123456789abcdef'
SEQ = '17'
CAP = '--capture-sleep-capability'
GETTER = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
root = Path(os.environ['PORTRAIT_FIXTURE'])
mode = (root / 'mode').read_text()
record = {'pid':os.getpid(), 'group':os.getpgrp(), 'session':os.getsid(0), 'args':sys.argv[1:]}
if mode in ('hang', 'writer'):
    child = os.fork()
    if not child:
        time.sleep(12)
        os._exit(0)
    record['descendant'] = child
(root / 'seen.tmp').write_text(json.dumps(record))
os.replace(root / 'seen.tmp', root / 'seen')
if mode == 'hang': time.sleep(12)
if mode == 'hold':
    while not (root / 'finish').exists(): time.sleep(.005)
os.write(1, (root / 'reply').read_bytes())
sys.exit(23 if mode == 'nonzero' else 0)
'''


def eventually(check, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.01)
    raise AssertionError(f'Condition did not hold within {seconds}s: {check}')


def ident(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
        return fields[0], int(fields[1]), int(fields[2]), int(fields[3])
    except FileNotFoundError:
        return None


def group_members(group):
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            pid = int(entry.name)
            state = ident(pid)
            if state and state[2:4] == (group, group):
                yield pid, state[0]


@unittest.skipUnless(sys.platform == 'linux', 'Run through ssh test-env')
class StartupPortraitSupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory(prefix='aidoku-portrait-host-')
        cls.addClassCleanup(tmp.cleanup)
        cls.binary = Path(tmp.name) / NAME
        override = os.environ.get('AIDOKU_TEST_PORTRAIT_SUPERVISOR')
        if override:
            shutil.copy2(override, cls.binary)
        else:
            subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(SOURCE), '-o', str(cls.binary)], check=True)
        libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        # Adopt and reap orphaned fixture descendants when simulating parent loss.
        if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) or libc.prctl(36, 1, 0, 0, 0):
            raise OSError(ctypes.get_errno(), 'Cannot adopt fixture children')
        cls.addClassCleanup(libc.prctl, 36, previous.value, 0, 0, 0)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='aidoku-portrait-case-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = self.root / 'app'
        self.app.mkdir()
        self.lock = self.root / 'lock'
        self.lock.mkdir(mode=0o700)
        self.helper = self.app / NAME
        shutil.copy2(self.binary, self.helper)
        getter = self.app / 'aidoku'
        getter.write_text(GETTER)
        getter.chmod(0o755)
        self.env = dict(os.environ, AIDOKU_LAUNCHER_DIR=str(self.lock),
                        AIDOKU_LAUNCH_NONCE=NONCE, AIDOKU_LAUNCH_GENERATION='1',
                        PORTRAIT_FIXTURE=str(self.root))
        self.mode = self.root / 'mode'
        self.mode.write_text('success')
        self.reply = self.root / 'reply'
        self.reply.write_bytes(b'PUBLISHED\n')
        self.seen = self.root / 'seen'
        self.output = self.root / 'output'
        self.done = self.lock / 'native-read-done'
        for name, value in [('pid', str(os.getpid())), ('nonce', NONCE),
                            ('generation', '1:'+NONCE), ('phase','starting'), ('epoch','0')]:
            self.private(name, value + '\n')

    def private(self, name, value, mode=0o600):
        pending = self.lock / (name + '.tmp')
        pending.write_text(value)
        pending.chmod(mode)
        pending.replace(self.lock / name)

    def launch(self, owner=None, caps=False, profile='scribe2'):
        args = [str(self.helper), str(os.getpid() if owner is None else owner), SEQ, profile]
        if caps:
            args.append(CAP)
        with self.output.open('wb') as output:
            worker = subprocess.Popen(args, env=self.env, stdout=output)
        self.addCleanup(self.retire, worker)
        return worker

    def reap(self, group):
        def empty():
            members = list(group_members(group))
            for pid, status in members:
                if status == 'Z':
                    try:
                        os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        pass
            return not members
        eventually(empty)

    def retire(self, worker):
        state = ident(worker.pid)
        if state and state[1:4] == (os.getpid(), worker.pid, worker.pid):
            os.killpg(worker.pid, signal.SIGKILL)
        elif worker.poll() is None:
            worker.kill()
        worker.wait(timeout=2)
        self.reap(worker.pid)

    def gate(self, worker, sequence=SEQ, nonce=NONCE, mode=0o600):
        pid = worker.pid
        eventually(lambda: (state := ident(pid)) and state[2:4] == (pid, pid))
        self.private('native-read-go', f'{pid}:{sequence}:{nonce}\n', mode)

    def was_success(self):
        return self.done.is_file() and self.done.read_bytes() == f'{SEQ}:{NONCE}:0\n'.encode()

    def expect_success(self, payload, caps=False):
        self.reply.write_bytes(payload)
        worker = self.launch(caps=caps)
        self.assertFalse(self.seen.exists())
        self.gate(worker)
        eventually(self.done.is_file)
        self.assertTrue(self.was_success())
        self.assertEqual(self.output.read_bytes(), payload)
        record = json.loads(self.seen.read_text())
        self.assertNotEqual(record['pid'], worker.pid)
        self.assertEqual((record['group'], record['session']), (worker.pid, worker.pid))
        self.assertEqual(record['args'], ['--kindle', '--device', 'scribe2', '--capture-native-portrait']
                         + ([CAP] if caps else []))
        self.assertIsNone(worker.poll(), 'The group leader must live through parent retirement')

    def test_exact_publication(self):
        self.expect_success(b'PUBLISHED\n')

    def test_combined_capability_false(self):
        self.expect_success(b'PUBLISHED 0\n', True)

    def test_combined_capability_true(self):
        self.expect_success(b'PUBLISHED 1\n', True)

    def expect_failure(self, payload):
        self.reply.write_bytes(payload)
        worker = self.launch(caps=True)
        self.gate(worker)
        eventually(lambda: self.done.exists() or worker.poll() is not None)
        self.assertTrue(self.seen.exists())
        self.assertFalse(self.was_success())
        self.assertEqual(self.output.read_bytes(), b'')

    def test_success_bytes_with_nonzero_status_rejected(self):
        self.mode.write_text('nonzero')
        self.expect_failure(b'PUBLISHED 1\n')

    def test_trailing_binary_rejected(self):
        self.expect_failure(b'PUBLISHED\n\0')

    def test_valid_prefix_plus_overflow_rejected(self):
        self.expect_failure(b'PUBLISHED 1\n' + b'a' * 127)

    def test_missing_newline_rejected(self):
        self.expect_failure(b'PUBLISHED 0')

    def test_wrong_parent_rejected(self):
        worker = self.launch(owner=1)
        self.assertNotEqual(worker.wait(timeout=3), 0)
        self.assertFalse(self.seen.exists())

    def test_invalid_device_rejected(self):
        worker = self.launch(profile='other')
        self.assertNotEqual(worker.wait(timeout=3), 0)
        self.assertFalse(self.seen.exists())

    def test_wrong_permission_never_admits_the_getter(self):
        worker = self.launch()
        self.gate(worker, sequence='18', nonce='0' * 32)
        worker.wait(timeout=3.2)
        self.assertFalse(self.seen.exists())
        self.assertFalse(self.was_success())

    def test_revoked_permission_before_getter_exit_prevents_publication(self):
        self.mode.write_text('hold')
        worker = self.launch()
        self.gate(worker)
        eventually(self.seen.is_file)
        self.private('native-read-go', f'{worker.pid}:18:{NONCE}\n')
        (self.root / 'finish').touch()
        worker.wait(timeout=3.2)
        self.assertFalse(self.was_success())
        self.assertEqual(self.output.read_bytes(), b'')

    def test_public_permission_never_admits_the_getter(self):
        worker = self.launch()
        self.gate(worker, mode=0o644)
        worker.wait(timeout=3.2)
        self.assertFalse(self.seen.exists())
        self.assertFalse(self.was_success())

    def test_hung_getter_tree_is_retired(self):
        self.mode.write_text('hang')
        worker = self.launch()
        self.gate(worker)
        eventually(self.seen.is_file)
        child = json.loads(self.seen.read_text())['descendant']
        self.assertEqual(ident(child)[2:4], (worker.pid, worker.pid))
        self.assertNotEqual(worker.wait(timeout=3.2), 0)
        self.reap(worker.pid)
        self.assertFalse(self.was_success())

    def test_getter_exit_with_unclosed_descendant_stdout(self):
        self.mode.write_text('writer')
        worker = self.launch()
        self.gate(worker)
        eventually(self.seen.is_file)
        worker.wait(timeout=3.2)
        self.reap(worker.pid)
        self.assertFalse(self.was_success())

    def test_killing_leader_leaves_no_group_member(self):
        self.mode.write_text('hang')
        worker = self.launch()
        self.gate(worker)
        eventually(self.seen.is_file)
        worker.kill()
        worker.wait(timeout=1)
        self.reap(worker.pid)

    def test_killing_launcher_retires_its_reader_tree(self):
        self.mode.write_text('hang')
        driver = '''import os, signal, subprocess, sys, time
from pathlib import Path
root, helper, nonce, seq = sys.argv[1:]
lock = Path(root) / 'lock'
(lock / 'pid').write_text(str(os.getpid()) + '\\n')
worker = subprocess.Popen([helper, str(os.getpid()), seq, 'scribe2'])
while os.getsid(worker.pid) != worker.pid: time.sleep(.005)
os.umask(0o077)
pending = lock / 'driver-go'
pending.write_text(f'{worker.pid}:{seq}:{nonce}\\n')
pending.replace(lock / 'native-read-go')
(Path(root) / 'leader').write_text(str(worker.pid))
signal.pause()
'''
        with self.output.open('wb') as output:
            parent = subprocess.Popen([sys.executable, '-c', driver, str(self.root),
                                       str(self.helper), NONCE, SEQ], env=self.env, stdout=output)
        try:
            eventually(self.seen.is_file)
            leader = int((self.root / 'leader').read_text())
            parent.kill()
            parent.wait(timeout=1)
            self.reap(leader)
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait(timeout=1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
