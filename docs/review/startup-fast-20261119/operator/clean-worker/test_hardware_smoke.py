import fcntl
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("hardware-smoke.sh")
LABEL = "1234567890abcdef"
MATCH = "matched 16777216 RGB triples, 128 tails and 1400 rows (2798527 f32 sums)"
POLICY = "portrait=shell\nprewarm=0\nlast_cover_grace=off\nimport_thumbnail=off\nfirst_layout=optimized\nresident=off"


class HardwareSmoke(unittest.TestCase):
    def setUp(self):
        private = tempfile.TemporaryDirectory()
        self.addCleanup(private.cleanup)
        self.root = Path(private.name)
        self.storage = self.root / "storage"
        self.stage = self.storage / (".aidoku-hw-" + LABEL)
        self.commands = self.root / "bin"
        self.commands.mkdir()
        self.display = self.root / "fb"
        self.display.mkdir()
        (self.display / "stride").write_text("1872\n")
        (self.display / "bits_per_pixel").write_text("8\n")
        (self.storage / "aidoku").mkdir(parents=True)
        (self.storage / "aidoku/launch.sh").write_bytes(b"control")
        (self.root / "cpuinfo").write_text("Features : vfp neon\n")
        (self.root / "mounts").write_text(f"fixture {self.storage} vfat rw 0 0\n")
        text = SCRIPT.read_text().replace("/mnt/us", str(self.storage))
        for original, relative in [("/sys/class/graphics/fb0", "fb"), ("/proc/cpuinfo", "cpuinfo"),
                                   ("/proc/mounts", "mounts"), ("/tmp/aidoku-launcher.lock", "app.lock")]:
            text = text.replace(original, str(self.root / relative))
        self.script = self.root / "smoke.sh"
        self.script.write_text(text)
        self.stub("uname", "echo armv7l")
        self.stub("lipc-get-prop", 'case "$3" in activeApp) echo com.lab126.KPPMainApp;; state) echo active;; esac')
        self.stub("status", "echo 'statusbar start/running'")
        self.stub("pidof", f'[ "$1" = awesome ] && echo {os.getpid()}')
        self.stub("df", "echo 'fixture 400000 100000 300000 25% /fixture'")

    def stub(self, name, body):
        path = self.commands / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def payload(self, launcher=b"control", version="aidoku 0.1.3", parity=MATCH):
        content = io.BytesIO()
        programs = {"r3-launch.sh": launcher, "cover-parity": f"#!/bin/sh\necho '{parity}'\n".encode(),
                    "aidoku-arm": f"#!/bin/sh\ncase \"$1\" in --version) echo '{version}';; *) echo '{POLICY}';; esac\n".encode()}
        with tarfile.open(fileobj=content, mode="w:gz") as archive:
            for name, data in programs.items():
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(data), 0o755
                archive.addfile(member, io.BytesIO(data))
        return content.getvalue()

    def invoke(self, mode, payload=b"", label=LABEL):
        result = subprocess.run(["sh", self.script, mode, label], input=payload, capture_output=True,
                                env=dict(os.environ, PATH=f"{self.commands}:{os.environ['PATH']}"), timeout=10)
        return result.returncode, result.stdout.decode().splitlines()

    def test_idle_probe_and_completed_batch(self):
        self.assertEqual(self.invoke("check"), (0, ["native_idle stage_absent"]))
        self.assertEqual(self.invoke("run", self.payload()), (0, [
            "installed_launcher_matches", "candidate_cli defaults_ok", MATCH, "stage_removed"]))
        self.assertEqual(self.invoke("check"), (0, ["native_idle stage_absent"]))
        self.assertEqual(list(self.storage.iterdir()), [self.storage / "aidoku"])

    def test_received_or_cli_failure_cleans_owned_stage(self):
        for content, reason in [(b"truncated", "payload"), (self.payload(launcher=b"changed"), "different_launcher"),
                                (self.payload(version="other"), "candidate"), (self.payload(parity="bad"), "parity")]:
            with self.subTest(reason=reason):
                result, lines = self.invoke("run", content)
                self.assertNotEqual(result, 0)
                self.assertEqual(lines[-2:], ["refused " + reason, "stage_removed"])
                self.assertFalse(self.stage.exists())

    def test_foreign_stage_is_never_removed(self):
        foreign = self.storage / ".aidoku-hw-someone-else"
        foreign.mkdir()
        self.assertEqual(self.invoke("run", self.payload()), (1, ["refused existing_operation"]))
        self.assertTrue(foreign.is_dir())
        self.stage.symlink_to(self.root / "absent")
        self.assertEqual(self.invoke("run", self.payload()), (1, ["refused existing_stage"]))
        self.assertTrue(self.stage.is_symlink())

    def test_close_cannot_remove_a_foreign_or_live_stage(self):
        self.assertEqual(self.invoke("close"), (0, ["stage_absent"]))
        self.stage.mkdir()
        owner = self.stage / "owner"
        owner.write_text("someone-else\n")
        self.assertEqual(self.invoke("close"), (1, ["refused unowned_stage"]))
        owner.write_text(LABEL + "\n")
        descriptor = os.open(self.stage, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self.assertEqual(self.invoke("close"), (1, ["refused operation_in_progress"]))
        finally:
            os.close(descriptor)
        extra = self.stage / ".extra"
        extra.touch()
        self.assertEqual(self.invoke("close"), (1, ["refused cleanup"]))
        self.assertTrue(owner.is_file())
        extra.unlink()
        self.assertEqual(self.invoke("close"), (0, ["stage_absent"]))
        self.assertFalse(self.stage.exists())

    def test_unsafe_state_refuses_before_writing(self):
        app_lock = self.root / "app.lock"
        app_lock.mkdir()
        self.assertEqual(self.invoke("check"), (1, ["refused existing_operation"]))
        app_lock.rmdir()
        self.stub("lipc-get-prop", "echo screenSaver")
        self.assertEqual(self.invoke("run", self.payload()), (1, ["refused stock_asleep"]))
        self.assertFalse(self.stage.exists())

    def test_neon_space_and_label_gates(self):
        self.assertEqual(self.invoke("run", label="../../not-a-path"), (1, ["refused label"]))
        self.stub("df", "echo 'fixture 400000 399900 100 99% /fixture'")
        self.assertEqual(self.invoke("run"), (1, ["refused free_space"]))
        (self.root / "cpuinfo").write_text("Features : vfp\n")
        self.assertEqual(self.invoke("run"), (1, ["refused neon"]))
        self.assertFalse(self.stage.exists())


if __name__ == "__main__":
    unittest.main()
