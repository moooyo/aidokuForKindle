#!/usr/bin/env python3
"""Package a prebuilt production ARM application for KPM on the Linux worker."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import tarfile
import tomllib


REPOSITORY = Path(__file__).resolve().parents[1]


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"A package input must be a regular file: {path}")
    return path.read_bytes()


def shell_file(path: Path) -> bytes:
    content = read_file(path).replace(b"\r\n", b"\n")
    if not content.startswith(b"#!/bin/sh\n") or b"\r" in content:
        raise ValueError(f"A shell input must have a portable shebang and LF lines: {path}")
    return content


def package_version(root: Path, explicit: str | None) -> list[int]:
    value = explicit or os.environ.get("CARGO_PKG_VERSION")
    if value is None:
        value = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8-sig"))["package"]["version"]
    if not isinstance(value, str) or re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value) is None:
        raise ValueError("KPM requires an explicit three-part numeric version")
    return [int(part) for part in value.split(".")]


def production_inputs(root: Path, application: Path) -> dict[str, Path]:
    """Use the production notice layout without importing KUAL or runtime state."""
    inputs = {
        "aidoku/app/aidoku": application,
        "aidoku/launch.sh": root / "packaging/kindle/launch.sh",
        "documents/Aidoku.sh": root / "packaging/kpm/document-launcher.sh",
        "aidoku/README.md": root / "packaging/kpm/README.md",
        "aidoku/LICENSE": root / "LICENSE",
        "aidoku/THIRD_PARTY_NOTICES.md": root / "THIRD_PARTY_NOTICES.md",
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
    for category in ("fonts", "startup"):
        directory = root / "assets" / category
        if not directory.is_dir() or directory.is_symlink():
            if category == "fonts":
                raise ValueError("The production font notice directory is missing or unsafe")
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix in (".txt", ".json", ".md"):
                inputs[f"aidoku/licenses/{category}/{path.name}"] = path
    return inputs


def checked_relative(name: str) -> None:
    path = PurePosixPath(name)
    if (path.is_absolute() or not name or "\\" in name
            or any(part in ("", ".", "..", "data") for part in name.split("/"))
            or re.fullmatch(r"[A-Za-z0-9_./-]+", name) is None
            or not (name.startswith("aidoku/") or name == "documents/Aidoku.sh")):
        raise ValueError(f"A payload path is outside the production allowlist: {name}")


def write_archive(output: Path, members: dict[str, tuple[bytes, int]]) -> None:
    directories = set()
    for name in members:
        directories.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != ".")
    with output.open("xb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
                for name in sorted(directories):
                    entry = tarfile.TarInfo(name + "/")
                    entry.type = tarfile.DIRTYPE
                    entry.mode = 0o755
                    entry.mtime = 0
                    archive.addfile(entry)
                for name, (content, mode) in sorted(members.items()):
                    entry = tarfile.TarInfo(name)
                    entry.mode = mode
                    entry.size = len(content)
                    entry.mtime = 0
                    archive.addfile(entry, io.BytesIO(content))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", type=Path, required=True, help="Use an already built production ARM ELF; never run Cargo")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=REPOSITORY)
    parser.add_argument("--version", help="Override CARGO_PKG_VERSION or the package version in Cargo.toml")
    parser.add_argument("--sample-cbz", type=Path, help="Optionally include a public, redistributable sample archive")
    arguments = parser.parse_args()
    if os.name == "nt" or platform.system() != "Linux":
        parser.error("Run KPM packaging on the designated Linux worker through ssh test-env")
    root = arguments.source_root.resolve(strict=True)
    output = arguments.output.resolve()
    report_path = output.with_name(output.name + ".manifest.json")
    if output.exists() or report_path.exists():
        parser.error("Use a fresh output path to preserve earlier package evidence")

    try:
        version = package_version(root, arguments.version)
        inputs = production_inputs(root, arguments.application)
        if arguments.sample_cbz is not None:
            inputs["aidoku/samples/Aidoku-Sample.cbz"] = arguments.sample_cbz
        payload: dict[str, tuple[bytes, int]] = {}
        records = []
        for name, path in sorted(inputs.items()):
            checked_relative(name)
            executable = name.endswith(".sh") or name == "aidoku/app/aidoku"
            content = shell_file(path) if name.endswith(".sh") else read_file(path)
            mode = 0o755 if executable else 0o644
            payload[name] = (content, mode)
            records.append({"path": name, "bytes": len(content), "mode": f"{mode:o}", "sha256": sha256(content)})
        application = payload["aidoku/app/aidoku"][0]
        if len(application) < 52 or application[:7] != b"\x7fELF\x01\x01\x01" or int.from_bytes(application[18:20], "little") != 40:
            raise ValueError("The application must be a prebuilt ELF32 little-endian ARM executable")
        manifest = {
            "manifest_version": 2,
            "id": "aidoku",
            "name": "Aidoku",
            "author": "aidoku-kindle contributors",
            "description": "Native Aidoku manga reader for Kindle",
            "version": version,
            "dependencies": [],
            "supported_platforms": ["kindlehf"],
        }
        payload_manifest = "".join(f"{item['sha256']}|{item['bytes']}|{item['mode']}|{item['path']}\n" for item in records).encode("ascii")
        members = {f"payload/{name}": value for name, value in payload.items()}
        members["manifest.json"] = ((json.dumps(manifest, indent=2) + "\n").encode("utf-8"), 0o644)
        members["payload.tsv"] = (payload_manifest, 0o644)
        for name in ("install.sh", "launch.sh", "uninstall.sh"):
            members[name] = (shell_file(root / "packaging/kpm" / name), 0o755)
        output.parent.mkdir(parents=True, exist_ok=True)
        write_archive(output, members)
        report_path.write_text(json.dumps({
            "artifact": output.name,
            "sha256": sha256(output.read_bytes()),
            "application_sha256": sha256(application),
            "payload_manifest_sha256": sha256(payload_manifest),
            "manifest": manifest,
            "payload": records,
            "files": [
                {"path": name, "bytes": len(content), "mode": f"{mode:o}", "sha256": sha256(content)}
                for name, (content, mode) in sorted(members.items())
            ],
            "evidence": "Static KPM packaging of prebuilt inputs; device acceptance is recorded separately.",
        }, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(output)


if __name__ == "__main__":
    main()
