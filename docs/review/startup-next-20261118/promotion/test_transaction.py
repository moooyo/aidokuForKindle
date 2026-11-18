"""Exercise the promotion transaction in isolated Linux filesystem fixtures."""

import hashlib
import os
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("transaction.sh")
LAUNCHER = "aidoku/launch.sh"
APP = "aidoku/app/aidoku"
HELPER = "aidoku/app/aidoku-startup-portrait"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class TransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.media = self.root / "media"
        self.lock = self.root / "launch.lock"
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        self.run_id = "fixture-4f72a"
        self.journal = self.media / ".aidoku-performance-backups" / self.run_id
        self.env = dict(os.environ, PATH=str(self.stubs) + ":/usr/bin:/bin")
        # No runtime variable can redirect the production media or native lock.
        script = SCRIPT.read_text()
        self.assertEqual(script.count("MEDIA=/mnt/us\n"), 1)
        self.assertEqual(script.count("LOCK=/tmp/aidoku-launcher.lock\n"), 1)
        script = script.replace("MEDIA=/mnt/us\n", f"MEDIA={shlex.quote(str(self.media))}\n")
        script = script.replace("LOCK=/tmp/aidoku-launcher.lock\n", f"LOCK={shlex.quote(str(self.lock))}\n")
        self.tool = self.stage / "transaction.sh"
        self.tool.write_text(script)
        self.stub("pidof", "exit 1")
        (self.stage / "owner").write_text(self.run_id + "\n")
        self.plan = self.stage / "changes.tsv"
        self.old = {LAUNCHER: b"launcher baseline\n", APP: b"application baseline\n"}
        self.candidate = {name: f"candidate {name}\n".encode() for name in (HELPER, LAUNCHER, APP)}
        self.mode = {LAUNCHER: 0o777, APP: 0o751}
        for name, data in self.old.items():
            path = self.media / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(self.mode[name])
        rows = []
        for name, data in self.candidate.items():
            path = self.stage / "payload" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            old = sha(self.old[name]) if name in self.old else "absent"
            rows.append(f"{old}|{sha(data)}|{name}\n")
        self.plan_text = "".join(rows)
        self.plan.write_text(self.plan_text)
        self.protected = self.media / "documents" / "Aidoku.sh"
        self.protected.parent.mkdir()
        self.protected.write_bytes(b"existing catalog document")

    def stub(self, name, body):
        path = self.stubs / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def invoke(self, op, *, success, run=None):
        result = subprocess.run(["sh", str(self.tool), op, run or self.run_id],
                                cwd=self.stage, env=self.env, capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode == 0, success, (result.stdout, result.stderr))
        self.assertEqual(self.protected.read_bytes(), b"existing catalog document")
        if success:
            self.assertEqual(result.stdout, {"install": "INSTALLED\n", "rollback": "ROLLED_BACK\n"}[op])
        return result

    def check_old(self):
        self.assertFalse((self.media / HELPER).exists())
        for name, data in self.old.items():
            path = self.media / name
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), self.mode[name])
        self.assertFalse(self.lock.exists())

    def test_restore_old_bytes_modes_and_remove_only_new_helper(self):
        self.invoke("install", success=True)
        for name, data in self.candidate.items():
            self.assertEqual((self.media / name).read_bytes(), data)
            self.assertTrue(os.access(self.media / name, os.X_OK))
        self.assertEqual((self.journal / "launch.sh.original").read_bytes(), self.old[LAUNCHER])
        self.invoke("install", success=False)
        self.invoke("rollback", success=True)
        self.invoke("rollback", success=True)
        self.invoke("install", success=False)
        self.check_old()

    def test_failed_later_copy_rolls_back_applied_leaves(self):
        self.stub("cp", 'case "$1" in */payload/aidoku/app/aidoku) printf partial >"$2"; exit 31;; esac\nexec /bin/cp "$@"')
        self.invoke("install", success=False)
        self.check_old()
        self.assertTrue((self.journal / "rolled-back").is_file())
        self.assertFalse(list(self.media.rglob(".aidoku-tx-*")))

    def test_signal_during_update_restores_original_program(self):
        self.stub("cp", 'case "$1" in */payload/aidoku/app/aidoku) kill -TERM "$PPID"; exit 31;; esac\nexec /bin/cp "$@"')
        self.invoke("install", success=False)
        self.check_old()
        self.assertTrue((self.journal / "rolled-back").is_file())

    def test_external_rollback_rejects_foreign_replacement_before_any_change(self):
        self.invoke("install", success=True)
        (self.media / LAUNCHER).write_bytes(b"foreign launcher")
        self.invoke("rollback", success=False)
        self.assertEqual((self.media / LAUNCHER).read_bytes(), b"foreign launcher")
        self.assertEqual((self.media / HELPER).read_bytes(), self.candidate[HELPER])

    def test_backup_bytes_cannot_be_substituted(self):
        self.invoke("install", success=True)
        (self.journal / "launch.sh.original").write_bytes(b"different backup")
        self.invoke("rollback", success=False)
        self.assertEqual((self.media / LAUNCHER).read_bytes(), self.candidate[LAUNCHER])

    def test_different_staged_manifest_and_missing_binding_refuse_rollback(self):
        self.invoke("install", success=True)
        self.plan.write_text(self.plan_text.splitlines(keepends=True)[0])
        self.invoke("rollback", success=False)
        self.plan.write_text(self.plan_text)
        (self.journal / "aidoku-startup-portrait.intent").unlink()
        self.invoke("rollback", success=False)
        self.assertEqual((self.media / HELPER).read_bytes(), self.candidate[HELPER])

    def test_unpublished_scratch_or_original_mismatch_preserves_files(self):
        (self.media / APP).write_bytes(b"another update")
        self.invoke("install", success=False)
        self.assertFalse(self.journal.exists())
        (self.media / APP).write_bytes(self.old[APP])
        scratch = (self.media / HELPER).parent / f".aidoku-tx-{self.run_id}-aidoku-startup-portrait"
        scratch.mkdir()
        (scratch / "owner").write_text("foreign\n")
        self.invoke("install", success=False)
        self.assertEqual((scratch / "owner").read_text(), "foreign\n")
        self.check_old()

    def test_linked_target_payload_or_backup_root_never_followed(self):
        source = self.stage / "payload" / APP
        source.unlink(); source.symlink_to(self.protected)
        self.invoke("install", success=False)
        self.assertFalse(self.journal.exists())
        source.unlink(); source.write_bytes(self.candidate[APP])
        helper = self.media / HELPER
        helper.symlink_to(self.root / "nonexistent")
        self.invoke("install", success=False)
        self.assertTrue(helper.is_symlink())
        helper.unlink()
        backups = self.journal.parent
        backups.rmdir(); backups.symlink_to(self.stage, target_is_directory=True)
        self.invoke("install", success=False)
        self.assertFalse((self.stage / self.run_id).exists())

    def test_foreign_lock_and_running_application_block_admission(self):
        self.lock.mkdir()
        (self.lock / "other").touch()
        self.invoke("install", success=False)
        self.assertTrue((self.lock / "other").is_file())
        (self.lock / "other").unlink(); self.lock.rmdir()
        self.stub("pidof", "printf '99\\n'; exit 0")
        self.invoke("install", success=False)
        self.check_old()

    def test_reject_malformed_or_overscoped_plans(self):
        row = f"absent|{sha(b'new')}|"
        for content in ("", row + APP + "\n", row + "documents/Aidoku.sh\n", row + "aidoku/app/../launch.sh\n",
                        self.plan_text + self.plan_text, self.plan_text.rstrip(), self.plan_text.replace("|", "||", 1),
                        self.plan_text.replace("absent", "A" * 64, 1)):
            with self.subTest(content=content[:40]):
                self.plan.write_text(content)
                self.invoke("install", success=False)
                self.check_old()

    def test_wrong_owner_and_escaping_transaction_id_rejected(self):
        self.invoke("install", success=False, run="../foreign")
        (self.stage / "owner").write_text(self.run_id + "\n\n")
        self.invoke("install", success=False)
        self.check_old()


if __name__ == "__main__":
    unittest.main()
