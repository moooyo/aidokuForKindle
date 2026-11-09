#!/usr/bin/env python3
"""Verify a frozen Kindle development archive on the designated Linux worker."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import struct
import sys
import zipfile


SOURCE_FILES = ("Cargo.toml", "Cargo.lock", "build.rs", "rust-toolchain.toml", ".cargo/config.toml")
SOURCE_DIRECTORIES = ("src", "ui", "assets", "vendor", "examples")
SOURCE_ALGORITHM = "sha256-of-canonical-sorted-relative-file-records-v1"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_manifest(root: Path) -> dict[str, object]:
    files = [root / name for name in SOURCE_FILES if (root / name).is_file()]
    for name in SOURCE_DIRECTORIES:
        directory = root / name
        if not directory.is_dir():
            raise ValueError(f"Missing production source directory: {name}")
        files.extend(path for path in directory.rglob("*") if path.is_file())
    records = [
        {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes())}
        for path in sorted(files, key=lambda path: path.relative_to(root).as_posix())
    ]
    canonical = json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"file_count": len(records), "files": records, "sha256": sha256(canonical.encode("utf-8"))}


def expected_inputs(root: Path) -> dict[str, Path]:
    inputs = {
        "aidoku/launch.sh": root / "packaging/kindle/launch.sh",
        "documents/Aidoku.sh": root / "packaging/kindle/document-launcher.sh",
        "aidoku/README.md": root / "packaging/kindle/README.md",
        "aidoku/LICENSE": root / "LICENSE",
        "aidoku/THIRD_PARTY_NOTICES.md": root / "THIRD_PARTY_NOTICES.md",
        "extensions/aidoku/menu.json": root / "packaging/kual/menu.json",
        "extensions/aidoku/config.xml": root / "packaging/kual/config.xml",
        "extensions/aidoku/set-profile.sh": root / "packaging/kual/set-profile.sh",
    }
    groups = {
        "backend": ("vendor/slint-backend-kindle", ("LICENSE-MIT", "LICENSE-APACHE", "UPSTREAM_REVISION", "PATCHES.md", "AIDOKU_PROVENANCE.md")),
        "pinyin": ("assets/pinyin", ("LICENSE-AOSP.txt", "README.md")),
        "sources": ("src/sources", ("aidoku-rs.LICENSE", "README.md")),
        "icons": ("assets/icons", ("README.md",)),
        "demo": ("assets/demo", ("README.md", "manifest.json")),
    }
    for category, (directory, names) in groups.items():
        for name in names:
            inputs[f"aidoku/licenses/{category}/{name}"] = root / directory / name
    for path in (root / "assets/fonts").iterdir():
        if path.is_file() and path.suffix in (".txt", ".json", ".md"):
            inputs[f"aidoku/licenses/fonts/{path.name}"] = path
    return inputs


def elf_identity(content: bytes) -> dict[str, object]:
    if len(content) < 52 or content[:7] != b"\x7fELF\x01\x01\x01":
        raise ValueError("The application is not ELF32 little-endian version 1")
    fields = struct.unpack_from("<HHIIIIIHHHHHH", content, 16)
    executable_type, machine = fields[:2]
    program_offset, program_size, program_count = fields[4], fields[8], fields[9]
    if executable_type != 2 or machine != 40 or program_size < 32 or program_count < 1:
        raise ValueError("The application is not a native ARM executable with program headers")
    types = []
    for index in range(program_count):
        offset = program_offset + index * program_size
        if offset < 0 or offset + program_size > len(content):
            raise ValueError("The ELF program header table exceeds the application")
        types.append(struct.unpack_from("<I", content, offset)[0])
    if 1 not in types:
        raise ValueError("The application has no PT_LOAD segment")
    if 2 in types or 3 in types:
        raise ValueError("The application has PT_DYNAMIC or PT_INTERP and is not static")
    return {"class": "ELF32", "endianness": "little", "machine": "ARM", "type": "EXEC", "static": True,
            "program_header_count": program_count}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expected-application-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run package verification only on the designated Linux worker through ssh test-env")
    if arguments.output.exists():
        parser.error("Use a fresh output file so previous verification evidence remains intact")
    report: dict[str, object] = {"kind": "kindle-development-package-verification", "passed": False,
                                "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "checks": []}
    checks: list[dict[str, object]] = []
    report["checks"] = checks

    def require(condition: bool, description: str, actual: object = None) -> None:
        checks.append({"description": description, "passed": bool(condition), "actual": actual})
        if not condition:
            raise ValueError(description)

    try:
        root = arguments.source_root.resolve(strict=True)
        before = source_manifest(root)
        stored_bytes = arguments.source_manifest.read_bytes()
        stored = json.loads(stored_bytes.decode("utf-8-sig"))
        require(stored.get("algorithm") == SOURCE_ALGORITHM
                and before["sha256"] == arguments.expected_source_sha256 == stored.get("sha256")
                and before["files"] == stored.get("files") and before["file_count"] == stored.get("file_count"),
                "The complete live production source matches the frozen source manifest", before["sha256"])
        package_bytes = arguments.archive.read_bytes()
        manifest_bytes = arguments.manifest.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
        require(manifest.get("artifact") == arguments.archive.name and manifest.get("sha256") == sha256(package_bytes),
                "The package manifest names and hashes the actual archive", sha256(package_bytes))
        require(manifest.get("source_sha256") == before["sha256"]
                and manifest.get("source_file_count") == before["file_count"]
                and manifest.get("source_manifest_sha256") == sha256(stored_bytes)
                and manifest.get("source_manifest_algorithm") == stored.get("algorithm"),
                "The package manifest binds the exact verified production source", manifest.get("source_sha256"))
        inputs = expected_inputs(root)
        expected_names = set(inputs) | {"aidoku/app/aidoku"}
        records = manifest.get("files")
        require(isinstance(records, list) and all(isinstance(item, dict) for item in records),
                "The package manifest contains explicit file records")
        record_names = [item.get("path") for item in records]
        require(len(record_names) == len(set(record_names)) and set(record_names) == expected_names,
                "The manifest has the exact allowlisted package members", sorted(record_names))
        record_map = {item["path"]: item for item in records}
        with zipfile.ZipFile(arguments.archive) as archive:
            members = archive.infolist()
            names = [item.filename for item in members]
            require(len(names) == len(set(names)) and set(names) == expected_names,
                    "The archive has no missing, duplicate, or unexpected members", names)
            require(archive.testzip() is None, "Every archive member passes its CRC")
            for info in members:
                name = info.filename
                relative = PurePosixPath(name)
                require(not relative.is_absolute() and ".." not in relative.parts and "data" not in relative.parts
                        and "\\" not in name and not info.is_dir(),
                        f"The package member has a safe runtime-free path: {name}")
                mode = info.external_attr >> 16
                expected_mode = 0o755 if name.endswith(".sh") or name == "aidoku/app/aidoku" else 0o644
                require(info.create_system == 3 and stat.S_ISREG(mode) and stat.S_IMODE(mode) == expected_mode,
                        f"The member has correct Unix file permissions: {name}", oct(mode))
                content = archive.read(info)
                record = record_map[name]
                require(record.get("bytes") == len(content) and record.get("sha256") == sha256(content),
                        f"The manifest size and hash match the actual member: {name}")
                if name in inputs:
                    require(content == inputs[name].read_bytes(), f"The packaged notice or launcher is current: {name}")
                if name.endswith(".sh"):
                    require(content.startswith(b"#!/bin/sh\n") and b"\r" not in content,
                            f"The launcher has a portable shebang and LF line endings: {name}")
            application = archive.read("aidoku/app/aidoku")
            require(sha256(application) == arguments.expected_application_sha256,
                    "The archived application matches the frozen native executable", sha256(application))
            identity = elf_identity(application)
            require(identity["static"] is True, "The archived application is a static ARM ELF32", identity)
            license_payload = None
            license_asset = root / "assets/licenses/GPL-3.0-or-later.txt"
            if license_asset.is_file():
                license_bytes = license_asset.read_bytes()
                require(license_bytes == archive.read("aidoku/LICENSE"),
                        "The license viewer asset matches the complete packaged GPL license")
                require(license_bytes in application,
                        "The complete license viewer text is embedded in the native executable")
                license_payload = {"asset": "assets/licenses/GPL-3.0-or-later.txt", "bytes": len(license_bytes),
                                   "sha256": sha256(license_bytes), "embedded_in_elf": True}
            fonts = json.loads(archive.read("aidoku/licenses/fonts/manifest.json"))
            font_results = []
            for font in fonts["fonts"]:
                payload = (root / "assets/fonts" / font["file"]).read_bytes()
                unpacked = gzip.decompress(payload)
                license_name = f"aidoku/licenses/fonts/{font['license_file']}"
                require(font["bytes"] == len(payload) and font["sha256"] == sha256(payload)
                        and font["uncompressed_bytes"] == len(unpacked) and font["uncompressed_sha256"] == sha256(unpacked)
                        and license_name in expected_names and bool(archive.read(license_name).strip()),
                        f"The embedded font record and complete license are current: {font['file']}")
                require(payload in application, f"The declared compressed font is embedded in the native executable: {font['file']}")
                font_results.append({"file": font["file"], "sha256": font["sha256"], "license": font["license_file"]})
        after = source_manifest(root)
        require(before == after, "Production source remains unchanged throughout package verification", after["sha256"])
        require(sha256(arguments.archive.read_bytes()) == sha256(package_bytes)
                and sha256(arguments.manifest.read_bytes()) == sha256(manifest_bytes),
                "The archive and manifest remain unchanged throughout verification")
        report.update({"passed": True, "archive": str(arguments.archive.resolve()), "archive_bytes": len(package_bytes),
                       "archive_sha256": sha256(package_bytes), "manifest": str(arguments.manifest.resolve()),
                       "manifest_sha256": sha256(manifest_bytes), "source_sha256": before["sha256"],
                       "source_file_count": before["file_count"], "application_sha256": sha256(application),
                       "member_count": len(expected_names), "elf": identity, "fonts": font_results,
                       "license_view_payload": license_payload,
                       "scope": "Development package identity and static archive checks; UI, native lifecycle and physical-device acceptance are separate."})
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    report["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "output": str(arguments.output), "checks": len(checks),
                      "error": report.get("error")}, ensure_ascii=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
