"""Compare production program files to retained staged-trial inputs, without device access."""

import argparse
import io
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))
from analyze import instrument


LIMIT = 64 * 1024**2


def small_file(path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 96 * 1024:
        raise ValueError(f"Invalid program input: {path.name}")
    return path.read_bytes()


def checked_archive(payload, programs, original, production):
    for path in (payload, programs / "aidoku-arm", programs / "aidoku-startup-portrait"):
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= LIMIT:
            raise ValueError(f"Invalid program input: {path.name}")
    if programs.is_symlink() or not programs.is_dir():
        raise ValueError("Invalid input directory")
    if small_file(programs / "launch.sh") != production:
        raise ValueError("Production launcher differs from selected build")
    if any(flag in production for flag in (b"AIDOKU_RESEARCH_", b"--performance-trace", b"--exit-after-ms")):
        raise ValueError("Research launcher cannot be used for a normal visit")
    expected = {"candidate/app/aidoku": programs / "aidoku-arm",
                "candidate/app/aidoku-startup-portrait": programs / "aidoku-startup-portrait",
                "control-launcher": original, "baseline/launch.sh": instrument(original),
                "candidate/launch.sh": instrument(production)}
    seen = set()
    with tarfile.open(payload, "r|gz") as archive:
        for member in archive:
            if member.name not in expected:
                continue
            if member.name in seen or not member.isreg() or member.sparse or member.mode & 0o7000:
                raise ValueError(f"Invalid archive member: {member.name}")
            reference = expected[member.name]
            binary = isinstance(reference, Path)
            size = reference.stat().st_size if binary else len(reference)
            if member.size != size or binary and not member.mode & 0o111:
                raise ValueError(f"Program size or mode differs: {member.name}")
            with reference.open("rb") if binary else io.BytesIO(reference) as source:
                remaining = size
                with archive.extractfile(member) as sample:
                    while remaining:
                        chunk = sample.read(min(remaining, 1024**2))
                        if not chunk or chunk != source.read(len(chunk)):
                            raise ValueError(f"Program bytes differ: {member.name}")
                        remaining -= len(chunk)
                    if source.read(1):
                        raise ValueError(f"Program size changed: {member.name}")
            seen.add(member.name)
    if seen != expected.keys():
        raise ValueError("Missing selected program or launcher")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("programs", type=Path)
    parser.add_argument("original_launcher", type=Path)
    parser.add_argument("production_launcher", type=Path)
    parser.add_argument("payload", nargs="+", type=Path)
    args = parser.parse_args()
    original, production = map(small_file, (args.original_launcher, args.production_launcher))
    for payload in args.payload:
        checked_archive(payload, args.programs, original, production)
    print(f"Production files match {len(args.payload)} trial payload(s); eligibility and installation not evaluated.")
