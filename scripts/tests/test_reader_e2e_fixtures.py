"""Check fixture restoration ownership boundaries on the remote worker."""

import importlib.util
import json
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

    def immutable_source(self):
        path = self.active / "source.png"
        path.write_bytes(b"immutable source bytes")
        (self.snapshot / path.name).write_bytes(path.read_bytes())
        recorded = fixtures.source_identity(path, self.active)
        (self.root / "immutable-sources.json").write_text(json.dumps({"schema_version": 1, "active_path": str(self.active), "files": [recorded]}))
        return path, recorded

    def test_mutable_restore_preserves_actual_original_inode_and_ctime(self):
        source, before = self.immutable_source()
        result = fixtures.restore(self.root, self.snapshot, self.active, True)
        self.assertEqual(fixtures.source_identity(source, self.active), before)
        self.assertTrue(result["source_identity_preserved"])
        self.assertEqual(result["immutable_source_count"], 1)
        self.assertEqual((self.active / "database.bin").read_bytes(), (self.snapshot / "database.bin").read_bytes())

    def test_changed_original_is_rejected_before_mutable_state_is_reset(self):
        source, _ = self.immutable_source()
        source.write_bytes(b"modified source bytes")
        with self.assertRaises(ValueError):
            fixtures.restore(self.root, self.snapshot, self.active, True)
        self.assertEqual(source.read_bytes(), b"modified source bytes")
        self.assertEqual((self.active / "stale.bin").read_bytes(), b"retired")

    def test_same_bytes_with_replaced_inode_are_not_repaired_into_a_warm_hit(self):
        source, before = self.immutable_source()
        replacement = self.active / "replacement.png"
        replacement.write_bytes(source.read_bytes())
        replacement.replace(source)
        self.assertEqual(fixtures.sha256(source), before["sha256"])
        with self.assertRaises(ValueError):
            fixtures.restore(self.root, self.snapshot, self.active, True)
        self.assertEqual((self.active / "stale.bin").read_bytes(), b"retired")


if __name__ == "__main__":
    unittest.main()
