"""Exercise the Linux guardian with only privately owned fixture processes."""

import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


HELPER = Path(sys.argv.pop(1)).absolute()
SCRIPT = Path(__file__).absolute()


def wait_for(check, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise TimeoutError("fixture did not reach the expected state")


def pedigree(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    # State, direct parent, Unix session, and kernel birth ticks.
    return fields[0], int(fields[1]), int(fields[3]), int(fields[19])


def worker(path, ambient_fd):
    try:
        os.fstat(ambient_fd)
    except OSError:
        pass
    else:
        sys.exit("unexpected inherited descriptor")

    def cancelled(_sig, _frame):
        (path / "cleaned").touch()
        raise SystemExit(73)

    signal.signal(signal.SIGTERM, cancelled)
    pending = path / "ready.pending"
    pending.write_text(str(os.getpid()))
    pending.replace(path / "ready")
    wait_for(lambda: (path / "settle").exists() or (path / "exit").exists())
    if (path / "settle").exists():
        (path / "cleaned").touch()
        fd = int(os.environ["AIDOKU_INITIAL_WAITER_FD"])
        os.write(fd, b"S")
        os.close(fd)
        wait_for((path / "exit").exists)
    sys.exit(41)


class GuardianTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.app = None
        self.guardian = None

    def launch(self):
        import fcntl

        with open(self.path / "ambient", "w") as file:
            ambient = fcntl.fcntl(file.fileno(), fcntl.F_DUPFD, 64)
            try:
                self.waiter = subprocess.Popen(
                    [HELPER, "--", sys.executable, SCRIPT, HELPER, "--worker", self.path, str(ambient)],
                    pass_fds=(ambient,),
                    stdin=subprocess.DEVNULL,
                )
            finally:
                os.close(ambient)
        self.addCleanup(self.finish_if_live)
        wait_for(lambda: (self.path / "ready").exists())
        self.app = int((self.path / "ready").read_text())
        self.original = pedigree(self.app)
        _, self.guardian, session, _ = self.original
        self.assertEqual(self.guardian, session)
        self.assertNotEqual(self.guardian, self.waiter.pid)
        self.assertEqual(pedigree(self.guardian)[1], self.waiter.pid)

    def finish_if_live(self):
        (self.path / "exit").touch()
        if self.waiter.poll() is None:
            self.waiter.terminate()
            self.waiter.wait(timeout=4)
        for pid in (self.guardian, self.app):
            if pid is None:
                continue

            def reaped():
                try:
                    return os.waitpid(pid, os.WNOHANG)[0] == pid
                except ChildProcessError:
                    return not Path(f"/proc/{pid}").exists()

            wait_for(reaped)

    def test_worker_exit_status_returns_to_initial_waiter(self):
        self.launch()
        (self.path / "exit").touch()
        self.assertEqual(self.waiter.wait(timeout=4), 41)

    def test_initial_waiter_settles_without_reparenting_worker(self):
        self.launch()
        (self.path / "settle").touch()
        self.assertEqual(self.waiter.wait(timeout=4), 0)
        self.assertTrue((self.path / "cleaned").exists())
        self.assertEqual(pedigree(self.app)[1:], self.original[1:])
        def closed_sockets():
            fds = list(Path(f"/proc/{self.guardian}/fd").iterdir())
            return fds if len(fds) == 2 else None

        descriptors = wait_for(closed_sockets)
        self.assertEqual(len(descriptors), 2)
        self.assertTrue(all(os.readlink(fd).startswith("pipe:[") for fd in descriptors))

    def test_native_waiter_retirement_leaves_worker_parent_intact(self):
        self.launch()
        self.waiter.kill()
        self.assertEqual(self.waiter.wait(timeout=4), -signal.SIGKILL)
        self.assertEqual(pedigree(self.app)[1:], self.original[1:])
        (self.path / "settle").touch()
        wait_for((self.path / "cleaned").exists)

    def test_cancel_waits_for_owned_worker_cleanup(self):
        self.launch()
        self.waiter.terminate()
        self.assertEqual(self.waiter.wait(timeout=4), 128 + signal.SIGTERM)
        self.assertTrue((self.path / "cleaned").exists())

    def test_unexpected_guardian_retirement_notifies_worker(self):
        self.launch()
        os.kill(self.guardian, signal.SIGKILL)
        self.assertEqual(self.waiter.wait(timeout=4), 125)
        wait_for((self.path / "cleaned").exists)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker(Path(sys.argv[2]), int(sys.argv[3]))
    # Adopt only fixture orphans rather than depending on the test host's PID 1.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "could not adopt fixture orphans")
    unittest.main()
