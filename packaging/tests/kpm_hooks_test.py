#!/usr/bin/env python3
"""Exercise KPM hooks with a private host filesystem, never a Kindle mount.

Run only on the designated remote Linux worker through ssh test-env. The hook
copies replace the canonical userstore, launcher lock, and UUID paths with
fixture paths. The fake executable is never run, and all rollback copies,
payloads, and user data belong to each temporary directory.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
KPM = REPOSITORY / "packaging/kpm"
KPM_DOCUMENT_ENTRY = KPM / "document-launcher.sh"
LEGACY_DOCUMENT_ENTRY = REPOSITORY / "packaging/kindle/document-launcher.sh"
RUN = "0123456789abcdef0123456789abcdef"
BINARY = "aidoku/app/aidoku"
LAUNCHER = "aidoku/launch.sh"
SAMPLE = "aidoku/samples/Aidoku-Sample.cbz"

ENTRY_RECORDER = r'''
import json
import os
from pathlib import Path
import sys

record = {
    "arguments": sys.argv[1:],
    "descriptors": {str(index): os.readlink(f"/proc/self/fd/{index}") for index in range(3)},
}
Path(os.environ["AIDOKU_TEST_ENTRY_RECORD"]).write_text(json.dumps(record), encoding="utf-8")
os.write(1, b"fixture standard output must stay private\n")
os.write(2, b"fixture standard error must stay private\n")
'''


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@unittest.skipUnless(sys.platform.startswith("linux"), "Run through ssh test-env on Linux")
class KpmHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aidoku-kpm-hook-test-", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.userstore = self.root / "userstore"
        self.home = self.userstore / "aidoku"
        self.lock = self.root / "launcher.lock"
        self.package = self.root / "package"
        self.payload = self.package / "payload"
        self.tools = self.root / "tools"
        self.backup = self.userstore / ".aidoku-install-backups" / f"kpm-{RUN}"
        self.marker = self.package / ".aidoku-installation-complete"
        self.manifest = self.package / "payload.tsv"
        self.uuid = self.root / "uuid"
        self.uuid.write_text("01234567-89ab-cdef-0123-456789abcdef\n", encoding="ascii")
        self.package.mkdir()
        self.tools.mkdir()
        self.userstore.mkdir()

        real_cp = shutil.which("cp")
        self.assertIsNotNone(real_cp, "The remote Linux fixture requires cp")
        self.environment = os.environ.copy()
        self.environment.update({
            "PATH": str(self.tools) + os.pathsep + self.environment["PATH"],
            "AIDOKU_TEST_REAL_CP": str(real_cp),
            "AIDOKU_TEST_US": str(self.userstore),
            "AIDOKU_TEST_FAILURE_TRACE": str(self.root / "injected-copy-failure"),
            "AIDOKU_TEST_PYTHON": sys.executable,
        })
        self.shell(self.tools / "pidof", "exit 1")
        self.shell(self.tools / "cp", r'''
if [ "${AIDOKU_TEST_FAIL_IMPORT_COPY:-0}" = 1 ] && [ "$#" -eq 2 ] &&
   [ "$1" = "$AIDOKU_TEST_US/aidoku/samples/Aidoku-Sample.cbz" ]; then
    case "$2" in
        "$AIDOKU_TEST_US"/aidoku/data/imports/*.pending)
            sha256sum "$AIDOKU_TEST_US/aidoku/app/aidoku" \
                "$AIDOKU_TEST_US/aidoku/launch.sh" >"$AIDOKU_TEST_FAILURE_TRACE"
            exit 74
            ;;
    esac
fi
exec "$AIDOKU_TEST_REAL_CP" "$@"
''')

        self.originals = {
            BINARY: b"#!/bin/sh\n# Original binary fixture.\nexit 0\n",
            LAUNCHER: b"#!/bin/sh\n# Original launcher fixture.\nexit 0\n",
            "aidoku/README.md": b"Original program notice.\n",
            "documents/Aidoku.sh": b"#!/bin/sh\n# Original document fixture.\nexit 0\n",
        }
        self.original_modes = {
            BINARY: 0o750,
            LAUNCHER: 0o700,
            "aidoku/README.md": 0o640,
            "documents/Aidoku.sh": 0o755,
        }
        for relative, content in self.originals.items():
            self.write_userstore(relative, content, self.original_modes[relative])
        self.write_userstore("aidoku/data/device-profile", b"scribe\n")
        self.write_userstore("aidoku/data/library.sqlite3", b"Fixture catalog bytes.\n")
        self.write_userstore("aidoku/data/library.sqlite3-wal", b"Fixture journal bytes.\n")
        self.write_userstore("aidoku/data/user-owned.txt", b"Retained user data.\n")
        preference = "aidoku/data/sources/state/" + "a" * 64 + "/defaults.json"
        self.write_userstore(preference, b'{"fixture_preference":true}\n')
        self.original_data = self.snapshot(self.home / "data")

        self.programs = {
            BINARY: b"#!/bin/sh\n# Replacement binary fixture, never executed.\nexit 0\n",
            LAUNCHER: b"#!/bin/sh\n# Replacement launcher fixture, never executed.\nexit 0\n",
            "aidoku/README.md": b"Replacement program notice.\n",
            "documents/Aidoku.sh": b"#!/bin/sh\n# Replacement document fixture.\nexit 0\n",
        }
        self.write_payload()
        for name in ("install.sh", "uninstall.sh"):
            source = (KPM / name).read_text(encoding="utf-8")
            self.shell(self.package / name, self.localize(source), complete=True)

    @staticmethod
    def shell(path: Path, source: str, *, complete: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = source if complete else "#!/bin/sh\nset -eu\n" + source.strip() + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def localize(self, source: str) -> str:
        localized = source.replace("/tmp/aidoku-launcher.lock", str(self.lock))
        localized = localized.replace("/proc/sys/kernel/random/uuid", str(self.uuid))
        localized = localized.replace("/mnt/us", str(self.userstore))
        self.assertNotIn("/mnt/us", localized, "A fixture must not access the canonical userstore")
        self.assertNotIn("/tmp/aidoku-launcher.lock", localized)
        self.assertNotIn("/proc/sys/kernel/random/uuid", localized)
        return localized

    def write_userstore(self, relative: str, content: bytes, mode: int = 0o600) -> Path:
        path = self.userstore / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(mode)
        return path

    @staticmethod
    def program_mode(relative: str) -> int:
        return 0o755 if relative == BINARY or relative.endswith(".sh") else 0o644

    def write_payload(self) -> None:
        rows = []
        for relative, content in sorted(self.programs.items()):
            path = self.payload / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            mode = self.program_mode(relative)
            path.chmod(mode)
            rows.append(f"{digest(content)}|{len(content)}|{mode:o}|{relative}\n")
        self.manifest.write_text("".join(rows), encoding="ascii", newline="\n")

    @staticmethod
    def snapshot(directory: Path) -> dict[str, bytes]:
        if not directory.exists():
            return {}
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in sorted(directory.rglob("*")) if path.is_file()}

    def run_hook(self, name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", str(self.package / name), *arguments], cwd=self.package,
                              env=self.environment, capture_output=True, text=True, timeout=15)

    def assert_success(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_original_programs(self) -> None:
        for relative, content in self.originals.items():
            path = self.userstore / relative
            self.assertEqual(path.read_bytes(), content, relative)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), self.original_modes[relative], relative)

    def assert_unchanged_data(self) -> None:
        self.assertEqual(self.snapshot(self.home / "data"), self.original_data)

    def assert_rejected_before_installation(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_original_programs()
        self.assert_unchanged_data()
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.backup.exists())

    def test_install_preserves_profile_data_and_binds_the_completed_manifest(self) -> None:
        self.assert_success(self.run_hook("install.sh"))
        for relative, content in self.programs.items():
            path = self.userstore / relative
            self.assertEqual(path.read_bytes(), content, relative)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), self.program_mode(relative), relative)
        self.assert_unchanged_data()
        self.assertEqual(self.marker.read_text().strip(), digest(self.manifest.read_bytes()))
        self.assertEqual((self.backup / "installation-complete").read_text().strip(), "true")
        self.assertEqual((self.backup / "backup-metadata/device-profile").read_bytes(), b"scribe\n")
        self.assertEqual((self.backup / "backup-metadata/library.sqlite3").read_bytes(),
                         self.original_data["library.sqlite3"])
        self.assertFalse((self.home / "data/imports/Aidoku-Sample.cbz").exists())

    def test_missing_profile_defaults_to_scribe_without_a_sample_payload(self) -> None:
        (self.home / "data/device-profile").unlink()
        self.assert_success(self.run_hook("install.sh"))
        self.assertEqual((self.home / "data/device-profile").read_bytes(), b"scribe\n")
        self.assertEqual((self.home / "data/library.sqlite3").read_bytes(),
                         self.original_data["library.sqlite3"])
        self.assertFalse((self.home / "data/imports/Aidoku-Sample.cbz").exists())

    def test_late_install_failure_restores_programs_and_auto_uninstall_keeps_them(self) -> None:
        self.programs[SAMPLE] = b"Public sample fixture bytes.\n"
        self.write_payload()
        self.environment["AIDOKU_TEST_FAIL_IMPORT_COPY"] = "1"
        result = self.run_hook("install.sh")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        failure_trace = Path(self.environment["AIDOKU_TEST_FAILURE_TRACE"])
        installed_hashes = [line.split()[0] for line in failure_trace.read_text().splitlines()]
        self.assertEqual(installed_hashes, [digest(self.programs[BINARY]), digest(self.programs[LAUNCHER])],
                         "The injected failure must follow installation of both new programs")
        self.assert_original_programs()
        self.assert_unchanged_data()
        self.assertFalse((self.userstore / SAMPLE).exists())
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.marker.with_name(self.marker.name + ".pending").exists())
        self.assertEqual((self.backup / "partial-program-installation-rolled-back").read_text().strip(), "true")
        self.assert_success(self.run_hook("uninstall.sh"))
        self.assert_original_programs()
        self.assert_unchanged_data()

    def test_uninstall_removes_only_unchanged_programs_and_preserves_data_backups(self) -> None:
        self.assert_success(self.run_hook("install.sh"))
        modified = b"#!/bin/sh\n# User-modified launcher fixture.\nexit 0\n"
        self.write_userstore(LAUNCHER, modified, 0o755)
        backup_before = self.snapshot(self.backup)
        self.assert_success(self.run_hook("uninstall.sh"))
        self.assertEqual((self.userstore / LAUNCHER).read_bytes(), modified)
        for relative in self.programs:
            if relative != LAUNCHER:
                self.assertFalse((self.userstore / relative).exists(), relative)
        self.assert_unchanged_data()
        self.assertEqual(self.snapshot(self.backup), backup_before)

    def test_upgrade_uninstall_leaves_the_existing_installation_unchanged(self) -> None:
        self.assert_success(self.run_hook("install.sh"))
        before = self.snapshot(self.userstore)
        self.assert_success(self.run_hook("uninstall.sh", "upgrade"))
        self.assertEqual(self.snapshot(self.userstore), before)
        self.assertTrue(self.marker.is_file())

    def test_changed_manifest_cannot_authorize_uninstall(self) -> None:
        self.assert_success(self.run_hook("install.sh"))
        before = self.snapshot(self.userstore)
        self.manifest.write_bytes(self.manifest.read_bytes() + b"changed manifest\n")
        result = self.run_hook("uninstall.sh")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(self.userstore), before)

    def test_duplicate_required_digest_rows_are_rejected(self) -> None:
        rows = self.manifest.read_text().splitlines(keepends=True)
        duplicate = next(row for row in rows if row.rstrip().endswith("|" + BINARY))
        self.manifest.write_text("".join(rows) + duplicate, encoding="ascii", newline="\n")
        self.assert_rejected_before_installation(self.run_hook("install.sh"))

    def test_symlink_payload_is_rejected(self) -> None:
        payload_notice = self.payload / "aidoku/README.md"
        payload_notice.unlink()
        external = self.root / "external-notice"
        external.write_bytes(self.programs["aidoku/README.md"])
        payload_notice.symlink_to(external)
        self.assert_rejected_before_installation(self.run_hook("install.sh"))

    def test_runtime_data_is_not_a_program_payload(self) -> None:
        self.programs["aidoku/data/foreign.sqlite3"] = b"Runtime data must not be installed.\n"
        self.write_payload()
        self.assert_rejected_before_installation(self.run_hook("install.sh"))
        self.assertFalse((self.home / "data/foreign.sqlite3").exists())

    def entry_record(self, source: Path, name: str, target: Path) -> tuple[subprocess.CompletedProcess[bytes], dict]:
        recorder = self.root / "entry-recorder.py"
        recorder.write_text(ENTRY_RECORDER.lstrip(), encoding="utf-8", newline="\n")
        self.shell(target, 'exec "$AIDOKU_TEST_PYTHON" "$AIDOKU_TEST_ENTRY_RECORDER" "$@"')
        original = source.read_text(encoding="utf-8")
        localized = self.localize(original).replace("/var/local/kmc/bin/kpm", str(target))
        entry = self.root / f"{name}-entry.sh"
        self.shell(entry, localized, complete=True)
        record_path = self.root / f"{name}-entry.json"
        environment = self.environment | {
            "AIDOKU_TEST_ENTRY_RECORDER": str(recorder),
            "AIDOKU_TEST_ENTRY_RECORD": str(record_path),
        }
        result = subprocess.run(["sh", str(entry)], env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        deadline = time.monotonic() + 3
        while not record_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(record_path.is_file(), result.stdout.decode() + result.stderr.decode())
        return result, json.loads(record_path.read_text(encoding="utf-8"))

    def assert_private_descriptors(self, result: subprocess.CompletedProcess[bytes], record: dict) -> None:
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(record["descriptors"]["0"], "/dev/null")
        for index in ("1", "2"):
            target = record["descriptors"][index]
            self.assertTrue(target == "/dev/null" or target.startswith(str(self.home / "data") + "/"),
                            f"Standard descriptor {index} remains attached to the entry caller: {target}")

    def test_kpm_document_entry_metadata_and_standard_streams_are_private(self) -> None:
        content = KPM_DOCUMENT_ENTRY.read_bytes()
        self.assertEqual(content.splitlines()[:3], [b"#!/bin/sh", b"# Name: Aidoku", b"# DontUseFBInk"])
        self.assertNotIn(b"\r", content)
        result, record = self.entry_record(KPM_DOCUMENT_ENTRY, "document", self.root / "fake-kpm")
        self.assert_private_descriptors(result, record)
        self.assertEqual(record["arguments"], ["launch", "aidoku", "--asap"])

    def test_legacy_document_entry_needs_only_the_canonical_runtime(self) -> None:
        content = LEGACY_DOCUMENT_ENTRY.read_bytes()
        self.assertEqual(content.splitlines()[:3], [b"#!/bin/sh", b"# Name: Aidoku", b"# DontUseFBInk"])
        self.assertNotIn(b"\r", content)
        self.assertFalse((self.root / "fake-kpm").exists())
        result, record = self.entry_record(LEGACY_DOCUMENT_ENTRY, "legacy-document", self.home / "launch.sh")
        self.assert_private_descriptors(result, record)
        self.assertEqual(record["arguments"], [])
        self.assertFalse((self.root / "fake-kpm").exists())

    def test_kpm_entry_uses_the_detached_launcher_with_private_standard_streams(self) -> None:
        result, record = self.entry_record(KPM / "launch.sh", "kpm", self.home / "launch.sh")
        self.assert_private_descriptors(result, record)
        self.assertNotIn("--foreground", record["arguments"])


if __name__ == "__main__":
    if not sys.platform.startswith("linux"):
        raise SystemExit("Run KPM hook tests on the designated remote Linux worker through ssh test-env")
    unittest.main(verbosity=2)
