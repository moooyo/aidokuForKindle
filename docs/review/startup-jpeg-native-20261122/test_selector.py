import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


HERE = Path(__file__).resolve().parent
LABEL = "0123456789abcdef"
CASES = ("control-warmup", "candidate-warmup", "control-01", "candidate-01")
NAMES = ("app/aidoku", "launch.sh", "app/aidoku-startup-portrait")


def mount_rows(target):
    rows = (line.split() for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines())
    return [(int(row[0]), row[3]) for row in rows if row[4] == str(target)]


class Fixture:
    def __init__(self, base):
        self.root = Path(base)
        self.tmp = self.root / "tmp"
        self.bin = self.root / "bin"
        self.tmp.mkdir()
        self.bin.mkdir()
        self.store = self.root / "mnt"
        self.home = self.store / "aidoku"
        self.stage = self.store / (".aidoku-native-" + LABEL)
        self.state = self.root / "private" / ("aidoku-native-" + LABEL)
        self.lock = self.tmp / "aidoku-launcher.lock"
        for folder in (
            self.home / "app",
            self.stage / "control/app",
            self.stage / "programs",
            self.state,
        ):
            folder.mkdir(parents=True)
        for location in (self.stage, self.state):
            (location / "owner").write_text(LABEL + "\n", encoding="utf-8")
        for index, name in enumerate(NAMES):
            data = b"installed-" + name.encode()
            for path in (self.home / name, self.stage / "control" / name):
                path.write_bytes(data)
                path.chmod(0o751 + index)
            os.utime(self.home / name, ns=(1600000000000000000, 1700000000000000000 + index))
        selected = self.stage / "programs/selected-app"
        selected.write_bytes(b"new-app-elf\n")
        selected.chmod(0o751)
        (self.stage / "control/program-modes").write_text("751\n752\n753\n", encoding="utf-8")
        self.original = [self.identity(self.home / name) for name in NAMES]
        self.stub("pidof", '[ "$1" = awesome ] && echo 1')
        self.stub("status", "echo statusbar start/running")
        self.stub("lipc-get-prop", 'if [ "$3" = state ]; then echo active; else echo com.lab126.KPPMainApp; fi')
        actual = self.root / "rename-system"
        actual.write_text(
            """#!/bin/sh
[ ! -e "$2" ] && [ ! -L "$2" ] || exit 1
mv "$1" "$2"
""",
            encoding="utf-8",
        )
        actual.chmod(0o700)
        wrapper = self.stage / "rename-new"
        wrapper.write_text(
            """#!/bin/sh
printf '%s %s\\n' "$(stat -c %d "$1")" "$(stat -L -c %d "${2%/*}")" >>"$RENAME_DEVICES"
exec "$RENAME_IMPL" "$@"
""",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        for operation in ("mount", "umount"):
            self.stub(
                operation,
                f'''/bin/{operation} "$@" || exit $?
case "${{BREAK_AFTER:-}}" in '') exit 0;; esac
count=0; if [ -f "$BREAK_FILE" ]; then read -r count <"$BREAK_FILE"; fi
count=$((count+1)); printf '%s\\n' "$count" >"$BREAK_FILE"
if [ "$count" = "$BREAK_AFTER" ]; then kill "-$BREAK_SIGNAL" "$PPID"; fi''',
            )
        self.script = (
            (HERE / "selector.sh")
            .read_text(encoding="utf-8")
            .replace("/var/tmp/", "@PRIVATE@/")
            .replace("private=/var/tmp", "private=@PRIVATE@")
            .replace("/tmp/", "@LOCK@/")
            .replace("/mnt/us", str(self.store))
            .replace("@PRIVATE@", str(self.root / "private"))
            .replace("@LOCK@", str(self.tmp))
        )

    def stub(self, name, commands):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + commands + "\n", encoding="utf-8")
        path.chmod(0o700)

    @staticmethod
    def identity(path):
        attributes = path.stat()
        return (
            attributes.st_dev,
            attributes.st_ino,
            attributes.st_uid,
            attributes.st_gid,
            attributes.st_mode,
            attributes.st_mtime_ns,
            path.read_bytes(),
        )

    def call(self, operation, case_name=None, *, fault=0, stop="KILL", success=True):
        count = self.root / "count"
        count.unlink(missing_ok=True)
        command = ["sh", "-s", "--", operation, LABEL]
        if case_name:
            command.append(case_name)
        env = dict(
            os.environ,
            PATH=str(self.bin) + ":" + os.environ["PATH"],
            BREAK_FILE=str(count),
            BREAK_SIGNAL=stop,
            BREAK_AFTER=str(fault) if fault else "",
            RENAME_IMPL=str(self.root / "rename-system"),
            RENAME_DEVICES=str(self.root / "devices"),
        )
        result = subprocess.run(command, input=self.script.encode(), capture_output=True, timeout=8, env=env)
        if success:
            expected = ("selector_" + operation + "\n").encode()
            if result.returncode or result.stdout != expected or result.stderr:
                raise AssertionError((operation, result.returncode, result.stdout, result.stderr))
        elif result.returncode == 0:
            raise AssertionError("Unexpected success")
        return result

    def original_restored(self):
        if [self.identity(self.home / name) for name in NAMES] != self.original or self.lock.exists():
            raise AssertionError("Original inode, bytes, owner, mode, mtime or lock differed")


class SelectorTransactions(unittest.TestCase):
    def setUp(self):
        private = TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.fixture = Fixture(private.name)
        self.root = Path(private.name)

    def test_selects_only_app_leaf_and_restores_original_identity(self):
        fixture = self.fixture
        fixture.call("prepare", CASES[0])
        fixture.call("select", CASES[0])
        fixture.call("check", CASES[0])
        app = fixture.home / "app/aidoku"
        self.assertEqual(app.read_bytes(), (fixture.stage / "programs/selected-app").read_bytes())
        self.assertTrue(mount_rows(app))
        self.assertEqual(mount_rows(fixture.home / "launch.sh"), [])
        self.assertEqual(mount_rows(fixture.home / "app/aidoku-startup-portrait"), [])
        fixture.call("restore")
        fixture.original_restored()

    def test_case_mismatch_is_refused(self):
        fixture = self.fixture
        fixture.call("prepare", "control-warmup")
        fixture.call("select", "candidate-warmup", success=False)
        self.assertEqual((fixture.home / "app/aidoku").read_bytes(), b"installed-app/aidoku")
        fixture.call("restore")
        fixture.original_restored()

    def test_hard_loss_after_select_or_restore_is_recoverable(self):
        fixture = self.fixture
        fixture.call("prepare", CASES[0])
        failed = fixture.call("select", CASES[0], fault=1, success=False)
        self.assertEqual(failed.returncode, -signal.SIGKILL)
        self.assertTrue(fixture.lock.is_dir())
        fixture.call("restore")
        fixture.original_restored()

        fixture.call("prepare", CASES[2])
        fixture.call("select", CASES[2])
        failed = fixture.call("restore", fault=1, success=False)
        self.assertEqual(failed.returncode, -signal.SIGKILL)
        self.assertTrue(fixture.lock.is_dir())
        fixture.call("restore")
        fixture.original_restored()

    def test_foreign_stack_is_not_unmounted(self):
        fixture = self.fixture
        fixture.call("prepare", CASES[0])
        fixture.call("select", CASES[0])
        target = fixture.home / "app/aidoku"
        foreign = self.root / "unrelated"
        shutil.copy2(target, foreign)
        subprocess.run(["/bin/mount", "-o", "bind", str(foreign), str(target)], check=True)
        before = Fixture.identity(target)
        fixture.call("restore", success=False)
        self.assertEqual(Fixture.identity(target), before)
        self.assertTrue(fixture.lock.is_dir())
        subprocess.run(["/bin/umount", str(target)], check=True)
        fixture.call("restore")
        fixture.original_restored()

    def test_selected_change_before_select_is_rejected(self):
        fixture = self.fixture
        fixture.call("prepare", CASES[1])
        (fixture.stage / "programs/selected-app").write_bytes(b"unexpected selected")
        fixture.call("select", CASES[1], success=False)
        fixture.call("restore", success=False)

    def test_check_requires_selected_mount(self):
        fixture = self.fixture
        fixture.call("prepare", CASES[0])
        fixture.call("check", CASES[0], success=False)
        fixture.call("select", CASES[0])
        fixture.call("check", CASES[0])
        fixture.call("restore")

    def test_verify_operation_is_refused(self):
        fixture = self.fixture
        fixture.call("prepare", CASES[0])
        fixture.call("verify", CASES[0], success=False)
        fixture.call("select", CASES[0])
        fixture.call("verify", CASES[0], success=False)
        fixture.call("restore")

    def test_check_is_read_only_and_does_not_take_selector_lock(self):
        fixture = self.fixture
        fixture.call("prepare", CASES[2])
        fixture.call("select", CASES[2])
        attached = fixture.state / "selector-journal/aidoku.mount-id"
        before = attached.stat().st_mtime_ns
        self.assertFalse(fixture.lock.exists())
        fixture.call("check", CASES[2])
        self.assertFalse(fixture.lock.exists())
        self.assertEqual(attached.stat().st_mtime_ns, before)
        fixture.call("restore")

    def test_absent_requires_stage_state_cleanup(self):
        fixture = self.fixture
        fixture.call("prepare", CASES[3])
        fixture.call("select", CASES[3])
        fixture.call("restore")
        fixture.call("absent", success=False)
        shutil.rmtree(fixture.stage)
        shutil.rmtree(fixture.state)
        fixture.call("absent")


if __name__ == "__main__":
    if os.geteuid() != 0 or sys.platform != "linux":
        raise SystemExit("Require the test-env private Linux worker")
    if "AIDOKU_TEST_OUTER_MNT" not in os.environ:
        outer = os.readlink("/proc/self/ns/mnt")
        os.execvpe(
            "unshare",
            [
                "unshare",
                "--mount",
                "--propagation",
                "private",
                "--fork",
                "--",
                sys.executable,
                "-B",
                str(Path(__file__).resolve()),
                *sys.argv[1:],
            ],
            dict(os.environ, AIDOKU_TEST_OUTER_MNT=outer),
        )
    if os.readlink("/proc/self/ns/mnt") == os.environ["AIDOKU_TEST_OUTER_MNT"]:
        raise SystemExit("The fixture must not run in its parent mount namespace")
    unittest.main()
