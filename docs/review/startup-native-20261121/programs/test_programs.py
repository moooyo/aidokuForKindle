import os
from pathlib import Path
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
import unittest


HERE = Path(__file__).resolve().parent
LABEL = "0123456789abcdef"
NAMES = ("app/aidoku", "launch.sh", "app/aidoku-startup-portrait")


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
        for folder in (self.home / "app", self.stage / "control/app", self.stage / "candidate/app", self.state):
            folder.mkdir(parents=True)
        for location in (self.stage, self.state):
            (location / "owner").write_text(LABEL + "\n")
        for index, name in enumerate(NAMES):
            control = b"installed-" + name.encode()
            candidate = b"new-" + name.encode() if index < 2 else control
            for path, data in ((self.home / name, control), (self.stage / "control" / name, control), (self.stage / "candidate" / name, candidate)):
                path.write_bytes(data)
                path.chmod(0o751 + index)
            os.utime(self.home / name, ns=(1600000000000000000, 1700000000000000000 + index))
        (self.stage / "control/program-modes").write_text("751\n752\n753\n")
        self.original = [self.identity(self.home / name) for name in NAMES]
        self.stub("pidof", '[ "$1" = awesome ] && echo 1')
        self.stub("status", "echo statusbar start/running")
        self.stub("lipc-get-prop", 'if [ "$3" = state ]; then echo active; else echo com.lab126.KPPMainApp; fi')
        actual = self.root / "rename-system"
        subprocess.run(["cc", "-Werror", "-Wall", "-O2", str(HERE / "rename_new.c"), "-o", str(actual)], check=True)
        wrapper = self.stage / "rename-new"
        wrapper.write_text('''#!/bin/sh
printf '%s %s\\n' "$(stat -c %d "$1")" "$(stat -L -c %d "${2%/*}")" >>"$RENAME_DEVICES"
exec "$RENAME_IMPL" "$@"\n''')
        wrapper.chmod(0o700)
        for operation in ("mount", "umount"):
            self.stub(operation, f'''/bin/{operation} "$@" || exit $?
case "${{BREAK_AFTER:-}}" in '') exit 0;; esac
count=0; if [ -f "$BREAK_FILE" ]; then read -r count <"$BREAK_FILE"; fi
count=$((count+1)); printf '%s\\n' "$count" >"$BREAK_FILE"
if [ "$count" = "$BREAK_AFTER" ]; then kill "-$BREAK_SIGNAL" "$PPID"; fi''')
        self.script = ((HERE / "programs.sh").read_text().replace("/var/tmp/", "@PRIVATE@/").replace("/tmp/", "@LOCK@/")
                       .replace("/mnt/us", str(self.store)).replace("@PRIVATE@", str(self.root / "private")).replace("@LOCK@", str(self.tmp)))

    def stub(self, name, commands):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + commands + "\n")
        path.chmod(0o700)

    @staticmethod
    def identity(path):
        attributes = path.stat()
        return (attributes.st_dev, attributes.st_ino, attributes.st_uid, attributes.st_gid, attributes.st_mode,
                attributes.st_mtime_ns, path.read_bytes())

    def call(self, operation, *, fault=0, stop="KILL", success=True):
        count = self.root / "count"
        count.unlink(missing_ok=True)
        env = dict(os.environ, PATH=str(self.bin) + ":" + os.environ["PATH"], BREAK_FILE=str(count),
                   BREAK_SIGNAL=stop, BREAK_AFTER=str(fault) if fault else "",
                   RENAME_IMPL=str(self.root / "rename-system"), RENAME_DEVICES=str(self.root / "devices"))
        result = subprocess.run(["sh", "-s", "--", operation, LABEL], input=self.script.encode(),
                                capture_output=True, timeout=8, env=env)
        if success:
            if result.returncode or result.stdout != ("programs_" + operation + "\n").encode() or result.stderr:
                raise AssertionError((operation, result.returncode, result.stdout, result.stderr))
        elif result.returncode == 0:
            raise AssertionError("Unexpected success")
        return result

    def original_restored(self):
        if [self.identity(self.home / name) for name in NAMES] != self.original or self.lock.exists():
            raise AssertionError("Original inode, bytes, owner, mode, mtime or lock differed")


class Transactions(unittest.TestCase):
    def setUp(self):
        private = TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.scenario = Fixture(private.name)

    def test_selects_two_leaves_only_and_returns_original_inodes(self):
        fixture = self.scenario
        (fixture.home / "data").mkdir()
        (fixture.home / "data/untouched").write_bytes(b"not a program")
        fixture.call("prepare"); fixture.call("candidate"); fixture.call("candidate")
        for name in NAMES:
            self.assertEqual((fixture.home / name).read_bytes(), (fixture.stage / "candidate" / name).read_bytes())
        self.assertFalse(fixture.lock.exists())
        fixture.call("control"); fixture.original_restored()
        fixture.call("candidate"); fixture.call("restore"); fixture.call("restore"); fixture.original_restored()
        self.assertEqual((fixture.home / "data/untouched").read_bytes(), b"not a program")

    def test_hard_loss_after_every_bind_and_unmount_then_bounded_recovery(self):
        fixture = self.scenario
        fixture.call("prepare")
        for operation in ("candidate", "control"):
            for stop_after in (1, 2):
                with self.subTest(operation=operation, stop_after=stop_after):
                    if operation == "control":
                        fixture.call("candidate")
                    result = fixture.call(operation, fault=stop_after, success=False)
                    self.assertEqual(result.returncode, -signal.SIGKILL)
                    self.assertTrue(fixture.lock.is_dir())
                    fixture.call("restore"); fixture.original_restored()

    def test_termination_rolls_back_before_unlock(self):
        fixture = self.scenario
        fixture.call("prepare")
        fixture.call("candidate", fault=2, stop="TERM", success=False)
        fixture.original_restored()

    def test_foreign_stack_even_with_same_bytes_not_unmounted(self):
        fixture = self.scenario
        fixture.call("prepare"); fixture.call("candidate")
        target = fixture.home / "launch.sh"
        foreign = fixture.root / "unrelated"
        shutil.copy2(target, foreign)
        subprocess.run(["/bin/mount", "-o", "bind", str(foreign), str(target)], check=True)
        before = fixture.identity(fixture.home / "app/aidoku")
        fixture.call("restore", success=False)
        self.assertEqual(fixture.identity(fixture.home / "app/aidoku"), before)
        self.assertEqual(target.stat().st_ino, foreign.stat().st_ino)
        self.assertTrue(fixture.lock.is_dir())
        subprocess.run(["/bin/umount", str(target)], check=True)
        fixture.call("restore"); fixture.original_restored()

    def test_block_foreign_lock_or_changed_helper_before_writing(self):
        fixture = self.scenario
        fixture.lock.mkdir()
        (fixture.lock / "owner").write_text("a different project")
        fixture.call("prepare", success=False)
        self.assertEqual((fixture.lock / "owner").read_text(), "a different project")
        (fixture.lock / "owner").unlink(); fixture.lock.rmdir()
        (fixture.stage / "candidate" / NAMES[-1]).write_bytes(b"unexpected helper")
        fixture.call("prepare", success=False)
        fixture.original_restored()

    def test_remounted_identical_selected_source_has_wrong_mount_id(self):
        fixture = self.scenario
        fixture.call("prepare"); fixture.call("candidate")
        app = fixture.home / "app/aidoku"
        subprocess.run(["/bin/umount", str(app)], check=True)
        occupied = fixture.root / "other-mount"
        occupied.touch()
        subprocess.run(["/bin/mount", "-o", "bind", str(fixture.home / "launch.sh"), str(occupied)], check=True)
        try:
            subprocess.run(["/bin/mount", "-o", "bind", str(fixture.stage / "candidate/app/aidoku"), str(app)], check=True)
            fixture.call("restore", success=False)
            self.assertEqual(app.read_bytes(), (fixture.stage / "candidate/app/aidoku").read_bytes())
            self.assertTrue(fixture.lock.is_dir())
            self.assertEqual((fixture.home / "launch.sh").read_bytes(), (fixture.stage / "candidate/launch.sh").read_bytes())
        finally:
            subprocess.run(["/bin/umount", str(app)], check=False, capture_output=True)
            subprocess.run(["/bin/umount", str(occupied)], check=True)
        fixture.call("restore"); fixture.original_restored()

    def test_foreign_single_mount_or_changed_underlying_control_is_never_repaired(self):
        fixture = self.scenario
        fixture.call("prepare")
        real = fixture.home / "app/aidoku"
        foreign = fixture.root / "another"
        shutil.copy2(fixture.stage / "candidate/app/aidoku", foreign)
        subprocess.run(["/bin/mount", "-o", "bind", str(foreign), str(real)], check=True)
        try:
            fixture.call("restore", success=False)
            self.assertEqual(real.stat().st_ino, foreign.stat().st_ino)
        finally:
            subprocess.run(["/bin/umount", str(real)], check=True)
        real.write_bytes(b"not the admitted installed program")
        fixture.call("restore", success=False)
        self.assertEqual(real.read_bytes(), b"not the admitted installed program")

    def test_fixed_tmp_alias_setgid_and_separate_storage_volumes(self):
        private = TemporaryDirectory(dir="/work")
        ram = TemporaryDirectory(dir="/tmp")
        self.addCleanup(private.cleanup); self.addCleanup(ram.cleanup)
        fixture = Fixture(private.name)
        alias = Path(ram.name)
        alias.chmod(0o3700)
        fixture.tmp.rmdir(); fixture.tmp.symlink_to(alias, target_is_directory=True)
        self.assertNotEqual(fixture.store.stat().st_dev, alias.stat().st_dev)
        fixture.call("prepare")
        fixture.call("candidate", fault=1, success=False)
        self.assertEqual(fixture.lock.stat().st_mode & 0o7777, 0o2700)
        self.assertEqual((fixture.lock / "owner").stat().st_mode & 0o7777, 0o600)
        fixture.call("restore"); fixture.original_restored()
        fixture.call("candidate"); fixture.call("restore"); fixture.original_restored()
        moves = [line.split() for line in (fixture.root / "devices").read_text().splitlines()]
        self.assertEqual({source for source, _ in moves}, {str(alias.stat().st_dev)})
        self.assertTrue(all(source == destination for source, destination in moves))

    def test_tmp_alias_never_admits_a_linked_leaf_owner_or_open_permissions(self):
        fixture = self.scenario
        actual = fixture.root / "system-tmp"; fixture.tmp.rename(actual); fixture.tmp.symlink_to(actual, target_is_directory=True)
        victim = fixture.root / "unrelated"; victim.mkdir(mode=0o700)
        owner = victim / "owner"; owner.write_text(LABEL + "\n"); owner.chmod(0o600)
        for path in (fixture.lock, fixture.tmp / (".aidoku-native-lock-" + LABEL)):
            path.symlink_to(victim, target_is_directory=True)
            fixture.call("prepare", success=False)
            self.assertTrue(path.is_symlink())
            self.assertEqual(owner.read_text(), LABEL + "\n")
            path.unlink()
        fixture.lock.mkdir(mode=0o700)
        observed = fixture.lock / "owner"; observed.symlink_to(owner)
        fixture.call("prepare", success=False); self.assertTrue(observed.is_symlink())
        observed.unlink(); observed.write_text(LABEL + "\n"); observed.chmod(0o644)
        fixture.call("prepare", success=False)
        observed.chmod(0o600); fixture.lock.chmod(0o2770)
        fixture.call("prepare", success=False); self.assertTrue(fixture.lock.is_dir())
        fixture.lock.chmod(0o700); observed.unlink(); fixture.lock.rmdir()
        fixture.original_restored()

    def test_unsealed_prepare_recovery_and_symlink_refusal(self):
        fixture = self.scenario
        (fixture.state / "program-journal").mkdir()
        fixture.call("restore"); fixture.original_restored()
        current = fixture.stage / "candidate/app/aidoku"
        current.rename(current.with_suffix(".elsewhere"))
        current.symlink_to(current.with_suffix(".elsewhere"))
        fixture.call("restore", success=False)
        fixture.original_restored()


if __name__ == "__main__":
    unittest.main()
