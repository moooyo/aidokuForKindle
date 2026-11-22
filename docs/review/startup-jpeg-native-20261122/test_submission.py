import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest


HERE = Path(__file__).resolve().parent
LABEL = "0123456789abcdef"
NONCE = "a" * 32


class SubmissionCollectorTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.media = self.root / "mnt/us"
        self.private = self.root / "var/tmp"
        self.proc = self.root / "proc"
        self.stage = self.media / (".aidoku-native-" + LABEL)
        self.state = self.private / ("aidoku-native-" + LABEL)
        self.name = "control-01"
        self.out = self.state / "out" / self.name
        self.visit = self.state / ("visit-" + self.name)
        self.journal = self.state / "selector-journal"
        self.home = self.media / "aidoku"
        self.app = self.home / "app/aidoku"
        self.selected = self.stage / "programs/selected-app"
        self.log = self.home / "data/logs/launcher.log"
        self.boot = self.proc / "sys/kernel/random/boot_id"
        self.stubs = self.root / "stubs"
        for directory in (self.stage, self.state, self.out, self.visit, self.journal,
                          self.app.parent, self.selected.parent, self.log.parent, self.boot.parent,
                          self.state / "visit-live", self.state / "guard", self.stubs):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in (self.stage, self.state):
            (directory / "owner").write_text(LABEL + "\n")
        self.selected.write_bytes(b"selected synthetic executable\n")
        self.selected.chmod(0o755)
        os.link(self.selected, self.app)
        self.log.write_text("existing private log\n")
        self.boot.write_text("fixture-boot\n")
        (self.visit / "boot").write_text("fixture-boot\n")
        for path, value in ((self.state / "current", self.name), (self.state / "ready", LABEL),
                            (self.state / "guard/ready", LABEL), (self.state / "visit-live/case", LABEL + " " + self.name),
                            (self.visit / "owner", LABEL + " " + self.name), (self.visit / "request", "stock-tap"),
                            (self.out / "origin", "stock-tap"), (self.journal / "owner", LABEL),
                            (self.journal / "sealed", LABEL), (self.journal / "case", self.name),
                            (self.journal / "aidoku.mount-intent", LABEL), (self.journal / "aidoku.mount-id", "77")):
            path.write_text(value + "\n")
        (self.journal / "aidoku.source").write_text(self.file_id(self.selected) + "\n")
        stat = self.log.stat()
        (self.visit / "log-mark").write_text(f"{stat.st_dev}:{stat.st_ino} {stat.st_size}\n")
        self.mountinfo = (f"1 0 0:1 / / rw - ext4 root rw\n"
                          f"10 1 0:1 / {self.media} rw - ext4 media rw\n"
                          f"77 10 0:1 /{self.selected.relative_to(self.media)} {self.app} rw - ext4 media rw\n")
        (self.proc / "self").mkdir()
        (self.proc / "self/mountinfo").write_text(self.mountinfo)
        (self.stubs / "usleep").write_text("#!/bin/sh\nsleep .001\n")
        (self.stubs / "usleep").chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.stubs) + ":" + os.environ["PATH"])
        script = (HERE / "submission.sh").read_text()
        script = script.replace("media=/mnt/us", f"media={self.media}")
        script = script.replace("private=/var/tmp", f"private={self.private}")
        script = script.replace("proc=/proc", f"proc={self.proc}")
        self.runner = self.root / "submission.sh"
        self.runner.write_text(script)

    @staticmethod
    def file_id(path):
        return subprocess.check_output(["stat", "-c", "%d:%i:%u:%g:%a:%s:%y:%z", str(path)], text=True).strip()

    def call(self, operation, *, success=True):
        result = subprocess.run(["sh", str(self.runner), operation, LABEL, self.name],
                                capture_output=True, text=True, env=self.env, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, (operation, result.stderr))
        else:
            self.assertNotEqual(result.returncode, 0, (operation, result.stdout))
            self.assertEqual(result.stdout, "")
            self.assertNotIn("fixture-secret", result.stderr)
        return result.stdout

    def proc_row(self, pid, parent, birth):
        folder = self.proc / str(pid)
        folder.mkdir(parents=True)
        (folder / "stat").write_text(f"{pid} (role with ) in name) S {parent} " + "0 " * 17 + f"{birth} 0\n")
        (folder / "fd").mkdir()
        (folder / "fd/2").symlink_to(self.log)
        (folder / "mountinfo").write_text(self.mountinfo)

    def launched(self, *, event=True):
        for pid, parent, birth in ((111, 1, 200), (222, 111, 301), (333, 222, 307)):
            self.proc_row(pid, parent, birth)
        (self.proc / "111/exe").symlink_to(Path("/bin/sh").resolve())
        (self.proc / "333/exe").symlink_to(self.app)
        worker = ["sh", str(self.home / "launch.sh"), "--foreground"]
        gui = [str(self.app), "--kindle", "--device", "scribe", "--data-dir", str(self.home / "data")]
        for pid, args in ((111, worker), (333, gui)):
            (self.proc / str(pid) / "cmdline").write_bytes(b"".join(value.encode() + b"\0" for value in args))
        (self.visit / "bound").write_text(f"333 307 222 301 111 200 {NONCE}\n")
        (self.visit / "ready").write_text("observed\n")
        for leaf, value in (("dispatched", "stock-tap"), ("pointer-brackets", "1000 1001 1020 1021"),
                            ("anchor", "1000 1001 1020 1021")):
            (self.out / leaf).write_text(value + "\n")
        with self.log.open("a") as stream:
            stream.write("aidoku: Launcher worker started (pid=111).\n")
            if event:
                stream.write(self.event())

    @staticmethod
    def event(stamp=5000, sequence=3, marker=5):
        return (f"aidoku: startup_library_submitted boottime_us={stamp} sequence={sequence} marker={marker} "
                "layout=large library=nonempty grid=nonempty\n")

    def finish(self):
        with self.log.open("a") as stream:
            stream.write("aidoku: Catalog notification completed.\n")
            stream.write("aidoku: Launcher worker exited with status 0.\n")
        (self.visit / "ack").write_text("observed\n")
        (self.visit / "complete").write_text("qualified\n")
        shutil.rmtree(self.state / "visit-live")

    def ready_to_collect(self):
        self.assertEqual(self.call("prepare"), "submission_prepared\n")
        self.launched()
        self.assertEqual(self.call("bind"), "submission_bound\n")
        self.finish()

    def test_closed_visit_yields_only_relative_public_fields(self):
        self.ready_to_collect()
        self.assertEqual(self.call("collect"), "SUBMISSION 3979 3980 3 5 large nonempty nonempty\n")
        self.assertFalse((self.state / ("submission-" + self.name) / "delta.pending").exists())
        self.call("collect", success=False)

    def test_waits_for_library_send_while_the_bound_process_is_alive(self):
        self.call("prepare")
        self.launched(event=False)
        def append_event():
            time.sleep(.05)
            with self.log.open("a") as stream:
                stream.write(self.event())
        thread = threading.Thread(target=append_event)
        thread.start()
        try:
            self.assertEqual(self.call("bind"), "submission_bound\n")
        finally:
            thread.join()
        self.finish()
        self.assertEqual(self.call("collect"), "SUBMISSION 3979 3980 3 5 large nonempty nonempty\n")

    def test_prepare_requires_same_arm_mount_and_log_boundary(self):
        (self.journal / "case").write_text("candidate-01\n")
        self.call("prepare", success=False)

    def test_prepare_rejects_foreign_mount(self):
        (self.proc / "self/mountinfo").write_text(self.mountinfo.replace("77 10", "78 10"))
        self.call("prepare", success=False)

    def test_binding_rejects_replaced_process_and_wrong_log_descriptor(self):
        self.call("prepare")
        self.launched()
        path = self.proc / "333/fd/2"
        path.unlink()
        path.symlink_to(self.boot)
        self.call("bind", success=False)
        path.unlink()
        path.symlink_to(self.log)
        (self.proc / "333/stat").write_text("333 (x) S 222 " + "0 " * 17 + "308 0\n")
        self.call("bind", success=False)

    def test_binding_rejects_foreign_gui_mount_and_changed_executable(self):
        self.call("prepare")
        self.launched()
        (self.proc / "333/mountinfo").write_text(self.mountinfo.replace("77 10", "78 10"))
        self.call("bind", success=False)
        (self.proc / "333/mountinfo").write_text(self.mountinfo)
        self.selected.write_bytes(b"changed synthetic executable\n")
        self.call("bind", success=False)

    def test_binding_rejects_invalid_marker_and_other_layout(self):
        self.call("prepare")
        self.launched(event=False)
        with self.log.open("a") as stream:
            stream.write(self.event(marker=0))
        self.call("bind", success=False)
        data = self.log.read_text().replace("marker=0", "marker=5").replace("layout=large", "layout=compact")
        self.log.write_text(data)
        self.call("bind", success=False)

    def test_collect_rejects_duplicate_or_invalid_events_without_raw_log(self):
        self.ready_to_collect()
        with self.log.open("a") as stream:
            stream.write(self.event())
            stream.write("fixture-secret\n")
        self.call("collect", success=False)
        self.assertFalse((self.state / ("submission-" + self.name) / "delta.pending").exists())

    def test_collect_rejects_replaced_log_changed_boot_and_missing_exit(self):
        self.ready_to_collect()
        original = self.log.read_bytes()
        self.log.rename(self.log.with_suffix(".old"))
        self.log.write_bytes(original)
        self.call("collect", success=False)
        self.log.unlink()
        self.log.with_suffix(".old").rename(self.log)
        self.boot.write_text("another-boot\n")
        self.call("collect", success=False)
        self.boot.write_text("fixture-boot\n")
        (self.visit / "complete").unlink()
        self.call("collect", success=False)

    def test_collect_rejects_unqualified_exit_and_wrong_worker(self):
        self.ready_to_collect()
        original = self.log.read_text()
        for changed in (original.replace("status 0.", "status 1."), original.replace("pid=111", "pid=999")):
            self.log.write_text(changed)
            self.call("collect", success=False)

    def test_accepts_zero_lower_bound_when_send_equals_release_end(self):
        self.call("prepare")
        self.launched(event=False)
        with self.log.open("a") as stream:
            stream.write(self.event(stamp=1021))
        self.call("bind")
        self.finish()
        self.assertEqual(self.call("collect"), "SUBMISSION 0 1 3 5 large nonempty nonempty\n")

    def test_bad_release_bracket_and_early_send_are_rejected(self):
        self.call("prepare")
        self.launched()
        for leaf in ("anchor", "pointer-brackets"):
            (self.out / leaf).write_text("1000 1001 6000 6001\n")
        self.call("bind", success=False)

    def test_truncate_and_rewrite_same_inode_does_not_preserve_log_prefix(self):
        self.ready_to_collect()
        data = self.log.read_bytes()
        self.log.write_bytes(b"x" * (len(data) - 1) + b"\n")
        self.call("collect", success=False)


if __name__ == "__main__":
    unittest.main()
