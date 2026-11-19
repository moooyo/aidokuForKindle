#!/usr/bin/env python3
"""Check KPM archive metadata on the remote Linux worker without running an app."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
BUILDER = REPOSITORY / "scripts/package-kpm.py"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@unittest.skipUnless(sys.platform.startswith("linux"), "Run through ssh test-env on Linux")
class KpmPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="aidoku-kpm-package-test-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.application = self.root / "application"
        header = bytearray(52)
        header[:7] = b"\x7fELF\x01\x01\x01"
        header[16:18] = (2).to_bytes(2, "little")
        header[18:20] = (40).to_bytes(2, "little")
        self.application.write_bytes(header)
        self.sample = self.root / "public-sample.cbz"
        self.sample.write_bytes(b"Public packaging fixture bytes; never opened as a comic.\n")

    def package(self, name: str, *, sample: bool = False, version: str | None = "2.3.4",
                environment: dict[str, str] | None = None,
                query_helper: Path | None = None,
                portrait_helper: Path | None = None,
                catalog_dir: Path | None = None) -> tuple[Path, subprocess.CompletedProcess[str]]:
        output = self.root / name
        command = [sys.executable, str(BUILDER), "--application", str(self.application),
                   "--source-root", str(REPOSITORY), "--output", str(output)]
        if version is not None:
            command.extend(("--version", version))
        if sample:
            command.extend(("--sample-cbz", str(self.sample)))
        if query_helper is not None:
            command.extend(("--native-query-helper", str(query_helper)))
        if portrait_helper is not None:
            command.extend(("--startup-portrait-helper", str(portrait_helper)))
        if catalog_dir is not None:
            command.extend(("--native-catalog-dir", str(catalog_dir)))
        result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
        return output, result

    def require_success(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_query_helper_is_explicit_hashed_and_executable(self) -> None:
        helper = self.root / "query-helper"
        content = bytearray(self.application.read_bytes())
        content[36:40] = (0x5000400).to_bytes(4, "little")
        helper.write_bytes(content)
        output, result = self.package("with-query.kpkg", query_helper=helper)
        self.require_success(result)
        with tarfile.open(output, "r:gz") as archive:
            member = archive.getmember("payload/aidoku/app/aidoku-native-query")
            self.assertEqual(member.mode, 0o755)
            self.assertEqual(archive.extractfile(member).read(), content)
            allowlist = archive.extractfile("payload.tsv").read().decode("ascii")
            self.assertIn(f"{sha256(content)}|{len(content)}|755|aidoku/app/aidoku-native-query\n", allowlist)

    def test_native_query_helper_rejects_an_incompatible_float_abi(self) -> None:
        helper = self.root / "soft-helper"
        helper.write_bytes(self.application.read_bytes())
        _, result = self.package("bad-query.kpkg", query_helper=helper)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hard-float", result.stderr)

    def test_optional_portrait_supervisor_binds_executable_content_and_mode(self) -> None:
        helper = self.root / "portrait"
        content = bytearray(self.application.read_bytes())
        content[16:18] = (3).to_bytes(2, "little")
        content[36:40] = (0x5000400).to_bytes(4, "little")
        helper.write_bytes(content)
        output, result = self.package("portrait.kpkg", portrait_helper=helper)
        self.require_success(result)
        with tarfile.open(output, "r:gz") as archive:
            member = archive.getmember("payload/aidoku/app/aidoku-startup-portrait")
            self.assertEqual(member.mode, 0o755)
            self.assertEqual(archive.extractfile(member).read(), content)
            self.assertIn(f"{sha256(content)}|{len(content)}|755|aidoku/app/aidoku-startup-portrait\n",
                          archive.extractfile("payload.tsv").read().decode("ascii"))
        content[16:18] = (1).to_bytes(2, "little")
        helper.write_bytes(content)
        bad, result = self.package("bad-portrait.kpkg", portrait_helper=helper)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(bad.exists())

    def test_catalog_runtime_is_explicit_and_preserves_modes(self) -> None:
        directory = self.root / "catalog"
        directory.mkdir()
        content = bytearray(self.application.read_bytes())
        content[36:40] = (0x5000400).to_bytes(4, "little")
        for name in ("aidoku-library-dispatch", "aidoku-entry-hooks.so", "aidoku-catalog-client"):
            (directory / name).write_bytes(content)
        (directory / "SHA256SUMS").write_text("Fixture manifest\n")
        output, result = self.package("catalog.kpkg", catalog_dir=directory)
        self.require_success(result)
        with tarfile.open(output, "r:gz") as archive:
            for name in ("aidoku-library-dispatch", "aidoku-catalog-client"):
                self.assertEqual(archive.getmember("payload/aidoku/native-catalog/" + name).mode, 0o755)
            self.assertEqual(archive.getmember("payload/aidoku/native-catalog/aidoku-entry-hooks.so").mode, 0o644)
            self.assertIn("payload/aidoku/install-catalog-entry.sh", archive.getnames())

    def test_catalog_runtime_rejects_host_architecture(self) -> None:
        directory = self.root / "invalid-catalog"
        directory.mkdir()
        for name in ("aidoku-library-dispatch", "aidoku-entry-hooks.so", "aidoku-catalog-client", "SHA256SUMS"):
            (directory / name).write_bytes(b"not a firmware artifact")
        _, result = self.package("invalid-catalog.kpkg", catalog_dir=directory)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ARM hard-float", result.stderr)

    def test_archive_has_kpm_metadata_and_a_complete_program_allowlist(self) -> None:
        output, result = self.package("aidoku.tar.gz", sample=True)
        self.require_success(result)
        report = json.loads(output.with_name(output.name + ".manifest.json").read_text())
        self.assertEqual(report["sha256"], sha256(output.read_bytes()))
        self.assertEqual(report["application_sha256"], sha256(self.application.read_bytes()))
        with tarfile.open(output, "r:gz") as archive:
            entries = archive.getmembers()
            self.assertEqual(len(entries), len({entry.name for entry in entries}))
            self.assertTrue(all(entry.isfile() or entry.isdir() for entry in entries))
            files = {entry.name: (archive.extractfile(entry).read(), entry) for entry in entries if entry.isfile()}
            manifest = json.loads(files["manifest.json"][0])
            self.assertEqual(manifest["manifest_version"], 2)
            self.assertEqual(manifest["id"], "aidoku")
            self.assertEqual(manifest["version"], [2, 3, 4])
            self.assertEqual(manifest["supported_platforms"], ["kindlehf"])
            self.assertEqual(manifest["dependencies"], [])
            self.assertEqual(manifest, report["manifest"])
            self.assertEqual(set(files) - {name for name in files if name.startswith("payload/")},
                             {"manifest.json", "payload.tsv", "install.sh", "launch.sh", "uninstall.sh"})
            self.assertFalse(any("data" in Path(name).parts or name.startswith("payload/extensions/") for name in files))
            self.assertFalse(any(".aidoku-installation-complete" in Path(name).parts for name in files))
            self.assertEqual(files["payload/documents/Aidoku.sh"][0].splitlines()[:3],
                             [b"#!/bin/sh", b"# Name: Aidoku", b"# DontUseFBInk"])
            self.assertEqual(files["payload/documents/Aidoku.sh"][0],
                             (REPOSITORY / "packaging/kpm/document-launcher.sh").read_bytes().replace(b"\r\n", b"\n"))
            header = files["payload/documents/Aidoku.sh"][0].splitlines()[:6]
            icon = next(line.removeprefix(b"# Icon: ").decode("ascii")
                        for line in header if line.startswith(b"# Icon: "))
            self.assertTrue(icon.startswith("/mnt/us/"))
            cover = files["payload/" + icon.removeprefix("/mnt/us/")][0]
            self.assertEqual(cover, (REPOSITORY / "assets/launcher/aidoku-cover-v2.png").read_bytes())
            self.assertEqual(cover[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(int.from_bytes(cover[16:20], "big"), 600)
            self.assertEqual(int.from_bytes(cover[20:24], "big"), 800)
            self.assertIn("payload/aidoku/licenses/launcher/README.md", files)
            self.assertIn("payload/aidoku/licenses/launcher/AIDOKU-LOGO-LICENSE.txt", files)
            self.assertIn("payload/aidoku/licenses/launcher/official-logo-provenance.json", files)
            self.assertIn("payload/aidoku/licenses/fonts/manifest.json", files)
            self.assertIn("payload/aidoku/licenses/icons/README.md", files)
            self.assertIn("payload/aidoku/licenses/demo/manifest.json", files)
            self.assertIn("payload/aidoku/LICENSE", files)
            self.assertIn("payload/aidoku/THIRD_PARTY_NOTICES.md", files)
            notices = {
                "aidoku/licenses/image-triangle/README.md": "vendor/image-triangle-reference-0.25.10/README.md",
                "aidoku/licenses/image-triangle/LICENSE-MIT": "vendor/image-triangle-reference-0.25.10/LICENSE-MIT",
                "aidoku/licenses/image-triangle/LICENSE-APACHE": "vendor/image-triangle-reference-0.25.10/LICENSE-APACHE",
                "aidoku/licenses/fonts/bootstrap-provenance.json": "assets/fonts/bootstrap-provenance.json",
                "aidoku/licenses/fonts/OFL.txt": "assets/fonts/OFL.txt",
            }
            for relative, source in notices.items():
                name = "payload/" + relative
                self.assertIn(name, files)
                self.assertEqual(files[name][0], (REPOSITORY / source).read_bytes(), relative)
                self.assertEqual(files[name][1].mode, 0o644, relative)
            rows = [line.split("|") for line in files["payload.tsv"][0].decode("ascii").splitlines()]
            self.assertEqual(len(rows), len({row[3] for row in rows}))
            self.assertEqual({"payload/" + row[3] for row in rows}, {name for name in files if name.startswith("payload/")})
            for digest, size, mode, relative in rows:
                content, entry = files["payload/" + relative]
                self.assertEqual(sha256(content), digest, relative)
                self.assertEqual(len(content), int(size), relative)
                self.assertEqual(entry.mode, int(mode, 8), relative)
                if relative.endswith(".sh"):
                    self.assertNotIn(b"\r", content)
            self.assertEqual(files["payload/aidoku/samples/Aidoku-Sample.cbz"][0], self.sample.read_bytes())
            for entry in entries:
                self.assertEqual((entry.uid, entry.gid, entry.mtime), (0, 0, 0))
            expected_members = {item["path"]: item for item in report["files"]}
            self.assertEqual(set(expected_members), set(files))
            for name, (content, entry) in files.items():
                self.assertEqual(expected_members[name], {"path": name, "bytes": len(content),
                                                          "mode": f"{entry.mode:o}", "sha256": sha256(content)})

    def test_archive_is_reproducible_and_omits_an_unspecified_sample(self) -> None:
        first, first_result = self.package("first.tar.gz")
        second, second_result = self.package("second.tar.gz")
        self.require_success(first_result)
        self.require_success(second_result)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first, "r:gz") as archive:
            self.assertNotIn("payload/aidoku/samples/Aidoku-Sample.cbz", archive.getnames())
            self.assertNotIn("payload/aidoku/app/aidoku-startup-portrait", archive.getnames())

    def test_environment_version_is_used_without_an_explicit_override(self) -> None:
        environment = os.environ | {"CARGO_PKG_VERSION": "5.6.7"}
        output, result = self.package("environment.tar.gz", version=None, environment=environment)
        self.require_success(result)
        with tarfile.open(output, "r:gz") as archive:
            manifest = json.load(archive.extractfile("manifest.json"))
            self.assertEqual(manifest["version"], [5, 6, 7])

    def test_desktop_elf_is_rejected_before_an_archive_is_created(self) -> None:
        content = bytearray(self.application.read_bytes())
        content[18:20] = (62).to_bytes(2, "little")
        self.application.write_bytes(content)
        output, result = self.package("desktop.tar.gz")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ARM", result.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    if not sys.platform.startswith("linux"):
        raise SystemExit("Run KPM package tests on the designated remote Linux worker through ssh test-env")
    unittest.main(verbosity=2)
