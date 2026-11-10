"""Check fixture restoration ownership boundaries on the remote worker."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("reader_e2e_fixtures", Path(__file__).resolve().parents[1] / "prepare-reader-e2e-fixtures.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class RestoreOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "owned"
        self.root.mkdir()
        (self.root / ".reader-e2e-fixtures").write_text(fixtures.MARKER + "\n")
        self.snapshot = self.root / "snapshots" / "saved"
        self.snapshot.mkdir(parents=True)
        self.active = self.root / "active"
        self.active.mkdir()
        (self.snapshot / "database.bin").write_bytes(b"absolute=/owned/active\x00\xff")
        (self.active / "stale.bin").write_bytes(b"retired")

    def test_restore_keeps_saved_bytes_and_removes_only_owned_active_files(self):
        before = (self.snapshot / "database.bin").read_bytes()
        fixtures.restore(self.root, self.snapshot, self.active)
        self.assertEqual((self.active / "database.bin").read_bytes(), before)
        self.assertEqual((self.snapshot / "database.bin").read_bytes(), before)
        self.assertFalse((self.active / "stale.bin").exists())

    def test_wrong_active_directory_is_rejected_without_removing_it(self):
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            fixtures.restore(self.root, self.snapshot, outside)
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        self.assertTrue((self.active / "stale.bin").is_file())

    def test_missing_ownership_marker_preserves_existing_active_data(self):
        (self.root / ".reader-e2e-fixtures").unlink()
        with self.assertRaises(ValueError):
            fixtures.restore(self.root, self.snapshot, self.active)
        self.assertEqual((self.active / "stale.bin").read_bytes(), b"retired")

    def test_symbolic_active_target_cannot_redirect_restoration(self):
        (self.active / "stale.bin").unlink()
        self.active.rmdir()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "sentinel").write_bytes(b"preserve")
        self.active.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            fixtures.restore(self.root, self.snapshot, self.active)
        self.assertEqual((outside / "sentinel").read_bytes(), b"preserve")

    def test_symbolic_snapshot_file_cannot_import_unowned_contents(self):
        outside = self.base / "secret"
        outside.write_bytes(b"unowned")
        (self.snapshot / "external.bin").symlink_to(outside)
        with self.assertRaises(ValueError):
            fixtures.restore(self.root, self.snapshot, self.active)
        self.assertEqual((self.active / "stale.bin").read_bytes(), b"retired")


if __name__ == "__main__":
    unittest.main()
