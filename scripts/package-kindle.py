#!/usr/bin/env python3
"""Package only the explicit development staging tree and preserve executable bits."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile


def source_metadata(path: Path | None) -> dict[str, object]:
    """Bind an independently verified production manifest without inferring a build."""
    if path is None:
        return {}
    content = path.read_bytes()
    manifest = json.loads(content.decode("utf-8-sig"))
    algorithm = "sha256-of-canonical-sorted-relative-file-records-v1"
    if not isinstance(manifest, dict) or manifest.get("algorithm") != algorithm:
        raise ValueError("The source manifest uses an unsupported fingerprint algorithm")
    records = manifest.get("files")
    count = manifest.get("file_count")
    if not isinstance(records, list) or not records or type(count) is not int or count != len(records):
        raise ValueError("The source manifest must contain its complete nonempty file list")
    paths = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise ValueError("A source file record has an invalid shape")
        name, size, digest = record["path"], record["bytes"], record["sha256"]
        if not isinstance(name, str) or not name or type(size) is not int or size < 0:
            raise ValueError("A source file record has an invalid path or byte count")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("A source file record has an invalid SHA-256")
        paths.append(name)
    if paths != sorted(set(paths)):
        raise ValueError("The source manifest file list must be sorted and unique")
    canonical = json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if manifest.get("sha256") != digest:
        raise ValueError("The source manifest fingerprint does not match its file records")
    return {
        "source_sha256": digest,
        "source_file_count": count,
        "source_manifest_sha256": hashlib.sha256(content).hexdigest(),
        "source_manifest_algorithm": algorithm,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path,
                        help="Bind an independently verified production source manifest to package provenance")
    arguments = parser.parse_args()
    provenance = source_metadata(arguments.source_manifest)
    staging = arguments.staging.resolve(strict=True)
    executable = staging / "aidoku/app/aidoku"
    header = executable.read_bytes()[:20] if executable.is_file() else b""
    if len(header) != 20 or header[:6] != b"\x7fELF\x01\x01" or int.from_bytes(header[18:20], "little") != 40:
        parser.error("The staging directory must contain a 32-bit little-endian ARM ELF")
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(staging).as_posix()
            if path.is_symlink() or "data" in path.relative_to(staging).parts:
                raise ValueError(f"Runtime state or a symlink is not a package input: {relative}")
            content = path.read_bytes()
            executable_file = path.suffix == ".sh" or relative == "aidoku/app/aidoku"
            info = zipfile.ZipInfo(relative, date_time=(2026, 11, 4, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | (0o755 if executable_file else 0o644)) << 16
            archive.writestr(info, content)
            manifest.append({"path": relative, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    output.with_suffix(".manifest.json").write_text(json.dumps({
        "artifact": output.name,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "files": manifest,
        "evidence": "Development artifact; model and firmware acceptance is recorded separately.",
        **provenance,
    }, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
