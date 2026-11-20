import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from test_programs import Fixture, NAMES


def mount_rows(target):
    rows = (line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines())
    return [(int(row[0]), row[3]) for row in rows if row[4] == str(target)]


def retained(file):
    info = os.fstat(file.fileno())
    return (info.st_dev, info.st_ino, info.st_mode, info.st_ctime_ns,
            info.st_mtime_ns, os.pread(file.fileno(), info.st_size, 0))


class PrivateMounts(unittest.TestCase):
    def setUp(self):
        private = TemporaryDirectory(prefix="aidoku-bind-fs-", dir="/tmp")
        self.addCleanup(private.cleanup)
        self.fixture = Fixture(private.name)
        self.root = Path(private.name)
        self.store = self.fixture.store
        self.app = self.fixture.home / NAMES[0]
        self.launcher = self.fixture.home / NAMES[1]
        self.source = self.fixture.stage / "candidate" / NAMES[0]
        self.helper = self.fixture.home / NAMES[2]
        self.addCleanup(self.reclaim)

    def reclaim(self):
        prefix = str(self.root) + "/"
        for _ in range(24):
            targets = [row[4] for row in (line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines())
                       if row[4].startswith(prefix)]
            if not targets:
                return
            target = max(targets, key=len)
            subprocess.run(["/bin/umount", "--", target], check=True)
        self.fail("The private namespace still contains a fixture mount")

    def bind(self, source, target):
        self.assertTrue(source.is_relative_to(self.root) and target.is_relative_to(self.root))
        subprocess.run(["/bin/mount", "--bind", source, target], check=True)

    def test_subtree_mount_preserves_open_inodes_and_unmodified_helper(self):
        backing = self.root / "subvolume"
        self.store.rename(backing)
        self.store.mkdir()
        self.bind(backing, self.store)
        self.assertEqual(len(mount_rows(self.store)), 1)
        with self.app.open("rb") as app_file, self.launcher.open("rb") as launcher_file:
            baseline = [retained(app_file), retained(launcher_file)]
            helper = Fixture.identity(self.helper)
            self.fixture.call("prepare")
            self.fixture.call("candidate")
            for path, leaf in [(self.app, NAMES[0]), (self.launcher, NAMES[1])]:
                rows = mount_rows(path)
                self.assertEqual(len(rows), 1)
                self.assertTrue(rows[0][1].endswith("/candidate/" + leaf), rows)
                self.assertTrue(os.path.samefile(path, self.fixture.stage / "candidate" / leaf))
            self.assertEqual([retained(app_file), retained(launcher_file)], baseline)
            self.assertEqual(mount_rows(self.helper), [])
            self.assertEqual(Fixture.identity(self.helper), helper)
            self.fixture.call("control")
            self.fixture.original_restored()
            self.assertEqual(mount_rows(self.app) + mount_rows(self.launcher), [])
            self.assertEqual([retained(app_file), retained(launcher_file)], baseline)
            self.assertEqual(self.app.stat().st_ino, baseline[0][1])
            self.assertEqual(self.launcher.stat().st_ino, baseline[1][1])

    def test_pre_receipt_crash_cannot_adopt_an_alternate_hardlink_to_the_same_inode(self):
        alternate = self.fixture.stage / "other-name-for-candidate"
        os.link(self.source, alternate)
        self.fixture.call("prepare")
        failure = self.fixture.call("candidate", fault=1, success=False)
        self.assertEqual(failure.returncode, -signal.SIGKILL)
        self.assertFalse((self.fixture.state / "program-journal" / "aidoku.mount-id").exists())
        self.assertEqual(len(mount_rows(self.app)), 1)
        subprocess.run(["/bin/umount", "--", self.app], check=True)
        self.bind(alternate, self.app)
        self.assertTrue(os.path.samefile(self.app, self.source))
        before = mount_rows(self.app)
        self.assertTrue(before[0][1].endswith("/other-name-for-candidate"))
        self.fixture.call("restore", success=False)
        self.assertEqual(mount_rows(self.app), before)
        self.assertEqual(mount_rows(self.launcher), [])
        self.assertTrue(self.fixture.lock.is_dir())
        subprocess.run(["/bin/umount", "--", self.app], check=True)
        self.fixture.call("restore")
        self.fixture.original_restored()

    def test_selected_source_overmount_cannot_mask_its_substitution(self):
        self.fixture.call("prepare")
        copy = self.root / "identical-source"
        shutil.copy2(self.source, copy)
        self.bind(copy, self.source)
        before = mount_rows(self.source)
        for action in ("candidate", "restore"):
            self.fixture.call(action, success=False)
            self.assertEqual(mount_rows(self.source), before)
            self.assertEqual(mount_rows(self.app) + mount_rows(self.launcher), [])
        subprocess.run(["/bin/umount", "--", self.source], check=True)
        self.fixture.call("restore")
        self.fixture.original_restored()

    def test_foreign_stack_cannot_confuse_interrupted_unbind(self):
        self.fixture.call("prepare")
        self.fixture.call("candidate")
        old_launcher = mount_rows(self.launcher)
        failure = self.fixture.call("control", fault=1, success=False)
        self.assertEqual(failure.returncode, -signal.SIGKILL)
        self.assertEqual(mount_rows(self.app), [])
        self.assertEqual(mount_rows(self.launcher), old_launcher)
        foreign = self.root / "foreign-launch"
        shutil.copy2(self.launcher, foreign)
        self.bind(foreign, self.launcher)
        stack = mount_rows(self.launcher)
        self.assertEqual(len(stack), 2)
        self.fixture.call("restore", success=False)
        self.assertEqual(mount_rows(self.launcher), stack)
        self.assertTrue(os.path.samefile(self.launcher, foreign))
        subprocess.run(["/bin/umount", "--", self.launcher], check=True)
        self.assertEqual(mount_rows(self.launcher), old_launcher)
        self.fixture.call("restore")
        self.assertEqual(mount_rows(self.app) + mount_rows(self.launcher), [])
        self.fixture.original_restored()

    def test_process_started_through_bind_target_keeps_live_exe_path_and_inode(self):
        source = self.root / "probe.c"
        source.write_text("#include <unistd.h>\nint main(void) {if (write(1,\"Y\",1)!=1) return 1; pause(); return 0;}\n")
        for path, alternate in ((self.app, False), (self.source, True)):
            subprocess.run(["cc", str(source), "-Wl,--build-id=none", "-o", str(path)], check=True)
            if alternate:
                with path.open("ab") as output:
                    output.write(b"candidate")
            path.chmod(0o751)
        shutil.copy2(self.app, self.fixture.stage / "control" / NAMES[0])
        before = Fixture.identity(self.app)
        self.fixture.call("prepare")
        self.fixture.call("candidate")
        self.assertTrue(os.path.samefile(self.app, self.source))
        child = subprocess.Popen([str(self.app)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        try:
            self.assertTrue(select.select([child.stdout], [], [], 3)[0])
            self.assertEqual(child.stdout.read(1), b"Y")
            exe = Path(f"/proc/{child.pid}/exe")
            self.assertEqual(os.readlink(exe), str(self.app))
            self.assertTrue(os.path.samefile(exe, self.app))
            self.assertTrue(os.path.samefile(exe, self.source))
            self.assertEqual(Path(f"/proc/{child.pid}/cmdline").read_bytes(), os.fsencode(self.app) + b"\0")
            self.assertNotEqual(before[1], exe.stat().st_ino)
        finally:
            child.terminate()
            child.wait(timeout=3)
            child.stdout.close()
        self.fixture.call("restore")
        self.assertEqual(mount_rows(self.app) + mount_rows(self.launcher), [])
        self.assertEqual(Fixture.identity(self.app), before)


if __name__ == "__main__":
    if os.geteuid() != 0 or sys.platform != "linux":
        raise SystemExit("Require the test-env private Linux worker")
    if "AIDOKU_TEST_OUTER_MNT" not in os.environ:
        outer = os.readlink("/proc/self/ns/mnt")
        os.execvpe("unshare", ["unshare", "--mount", "--propagation", "private", "--fork", "--",
                              sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:]],
                   dict(os.environ, AIDOKU_TEST_OUTER_MNT=outer))
    if os.readlink("/proc/self/ns/mnt") == os.environ["AIDOKU_TEST_OUTER_MNT"]:
        raise SystemExit("The fixture must not run in its parent mount namespace")
    unittest.main()
