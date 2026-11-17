"""Exercise immutable runtime registration on a private host filesystem."""

import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "packaging/kindle/install-catalog-entry.sh"
SERVICE = "tech.hackerdude.shell_integration.launcher"
FILES = ("aidoku-library-dispatch", "aidoku-entry-hooks.so", "aidoku-catalog-client")


@unittest.skipUnless(sys.platform.startswith("linux"), "Run through ssh test-env")
class CatalogInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aidoku-catalog-install-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.application = self.root / "aidoku"
        self.payload = self.application / "native-catalog"
        self.payload.mkdir(parents=True)
        self.runtime = self.root / "runtime"
        self.registry = self.root / "appreg.db"
        self.legacy = self.root / "original-handler"
        self.catalog = self.root / "change.lua"
        self.legacy.write_bytes(b"qualified original handler\n")
        self.catalog.write_bytes(b"qualified catalog schema\n")
        self.connection = sqlite3.connect(self.registry)
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE properties(handlerId TEXT, name TEXT, value TEXT)")
        self.connection.execute("INSERT INTO properties VALUES(?, 'command', ?)", (SERVICE, str(self.legacy)))
        self.connection.execute("INSERT INTO properties VALUES('unrelated', 'command', 'preserve-me')")
        self.connection.commit()
        self.populate_payload(b"version-one")
        source = INSTALLER.read_text()
        replacements = {
            "/var/local/kindle-catalog-entry": str(self.runtime),
            "/var/local/appreg.db": str(self.registry),
            "/var/local/kmc/bin/sh_integration_launcher": str(self.legacy),
            "/usr/lib/ccat/change.lua": str(self.catalog),
            "2b3db59ed7856b3f5026e3837f7ef07fc9bb6bc1c042274b48b3cd3fc2e04abc": hashlib.sha256(self.legacy.read_bytes()).hexdigest(),
            "971541da47ab5c3cafbde9ab80662d1b0055183c57aed9baf942a3360dbff5b4": hashlib.sha256(self.catalog.read_bytes()).hexdigest(),
        }
        for old, new in replacements.items():
            source = source.replace(old, new)
        self.script = self.application / "install-catalog-entry.sh"
        self.script.write_text(source)

    def populate_payload(self, version):
        lines = []
        for name in FILES:
            data = (b"#!/bin/sh\nprintf '%s\\n' 'aidoku-catalog-entry 1'\n# " + version
                    if name == "aidoku-library-dispatch" else name.encode() + b"\n" + version)
            (self.payload / name).write_bytes(data)
            (self.payload / name).chmod(0o755)
            lines.append(hashlib.sha256(data).hexdigest() + "  " + name)
        (self.payload / "SHA256SUMS").write_text("\n".join(lines) + "\n")

    def command(self):
        return self.connection.execute("SELECT value FROM properties WHERE handlerId=? AND name='command'", (SERVICE,)).fetchone()[0]

    def select_command(self, command):
        self.connection.execute("UPDATE properties SET value=? WHERE handlerId=? AND name='command'", (command, SERVICE))
        self.connection.commit()

    def execute(self, *arguments, success=True):
        result = subprocess.run(["sh", str(self.script), *arguments], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertEqual(self.connection.execute("SELECT value FROM properties WHERE handlerId='unrelated'").fetchone()[0], "preserve-me")
        return result

    def test_install_is_complete_and_idempotent(self):
        self.execute()
        command = Path(self.command())
        self.assertEqual(command.parent.parent, self.runtime)
        for name in FILES:
            self.assertEqual((command.parent / name).read_bytes(), (self.payload / name).read_bytes())
        inode = command.stat().st_ino
        self.execute()
        self.assertEqual(Path(self.command()).stat().st_ino, inode)
        self.assertEqual(self.legacy.read_bytes(), b"qualified original handler\n")

    def test_unknown_handler_keeps_original_registration(self):
        self.legacy.write_bytes(b"different native build")
        self.execute()
        self.assertEqual(self.command(), str(self.legacy))
        self.assertFalse(self.runtime.exists())

    def test_unknown_catalog_keeps_original_registration(self):
        self.catalog.write_bytes(b"different catalog schema")
        self.execute()
        self.assertEqual(self.command(), str(self.legacy))

    def test_unrelated_registration_is_never_replaced(self):
        self.select_command("unrelated 'handler' --flag")
        self.execute()
        self.assertEqual(self.command(), "unrelated 'handler' --flag")
        self.assertFalse(self.runtime.exists())

    def test_modified_payload_fails_before_registration(self):
        (self.payload / FILES[0]).write_bytes(b"corrupt")
        self.execute(success=False)
        self.assertEqual(self.command(), str(self.legacy))

    def test_unexpected_manifest_member_is_rejected(self):
        (self.payload / "SHA256SUMS").write_text("0" * 64 + "  ../unrelated\n")
        self.execute(success=False)
        self.assertEqual(self.command(), str(self.legacy))

    def test_existing_immutable_version_is_not_overwritten(self):
        self.execute()
        target = Path(self.command()).parent / "aidoku-catalog-client"
        target.write_bytes(b"preserve unexpected existing content")
        self.execute(success=False)
        self.assertEqual(target.read_bytes(), b"preserve unexpected existing content")

    def test_runtime_directory_symlink_is_rejected(self):
        external = self.root / "external"
        external.mkdir()
        self.runtime.symlink_to(external, target_is_directory=True)
        self.execute(success=False)
        self.assertEqual(list(external.iterdir()), [])
        self.assertEqual(self.command(), str(self.legacy))

    def test_upgrade_keeps_previous_runtime_for_active_workers(self):
        self.execute()
        original = Path(self.command())
        original_bytes = original.read_bytes()
        self.populate_payload(b"version-two")
        self.execute()
        self.assertNotEqual(Path(self.command()), original)
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_disable_only_restores_an_owned_registration(self):
        self.execute()
        retained = Path(self.command())
        self.execute("--disable")
        self.assertEqual(self.command(), str(self.legacy))
        self.assertTrue(retained.exists())
        self.select_command("unrelated-handler")
        self.execute("--disable")
        self.assertEqual(self.command(), "unrelated-handler")


if __name__ == "__main__":
    unittest.main(verbosity=2)
