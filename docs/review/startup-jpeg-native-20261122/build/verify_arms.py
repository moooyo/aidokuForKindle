"""Qualify frozen ARM executables without using the device or a native framebuffer."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


TARGET = "armv7-unknown-linux-musleabihf"
CASES = {
    "scribe-grid": ("large", "nonempty", "nonempty"),
    "scribe-filtered": ("large", "nonempty", "empty"),
    "paperwhite-empty": ("compact", "empty", "empty"),
}


def execute(arguments, **kwargs):
    return subprocess.run(arguments, check=True, capture_output=True, timeout=90, **kwargs)


def qualify(root, arm, linker, probe_source):
    executable = root / "programs" / arm
    header = execute(["readelf", "-h", "-l", "-A", executable]).stdout
    for required in (b"ELF32", b"little endian", b"ARM", b"Version5 EABI", b"hard-float ABI", b"Tag_CPU_arch: v7"):
        if required not in header:
            raise ValueError(f"{arm} missing ELF property {required!r}")
    if b"INTERP" in header or b"DYNAMIC" in header:
        raise ValueError(f"{arm} is not a static ARM ELF")
    with executable.open("rb") as stream:
        if b"aidoku: startup_library_submitted boottime_us=" not in stream.read():
            raise ValueError(f"{arm} has no Library submission marker")
    version = execute(["qemu-arm", executable, "--version"]).stdout
    if not re.fullmatch(rb"[Aa]idoku(?:[- ](?:[Ff]or[- ])?[Kk]indle)? [0-9]+\.[0-9]+\.[0-9]+\n", version):
        raise ValueError(f"Unexpected {arm} version response: {version!r}")
    if execute(["qemu-arm", "-cpu", "cortex-a7,neon=off,vfp-d32=off", executable, "--version"]).stdout != version:
        raise ValueError(f"{arm} no-NEON version mismatch")
    defaults = execute(["qemu-arm", executable, "--print-startup-options"]).stdout
    if not defaults.strip():
        raise ValueError(f"{arm} has no startup option response")
    folder = root / "probe" / arm
    library = folder / "libaidoku_kindle.rlib"
    fixture = folder / "marker-probe"
    execute([
        "rustc", "--edition=2024", "--target", TARGET, "-C", "opt-level=2", "-C", f"linker={linker}",
        "--extern", f"aidoku_kindle={library}", "-L", f"dependency={root}/target/{TARGET}/release/deps",
        "-L", f"dependency={root}/target/release/deps", probe_source, "-o", fixture,
    ])
    for case, (layout, shelf, grid) in CASES.items():
        output = execute(["qemu-arm", fixture, case])
        pattern = (rf"aidoku: startup_library_submitted boottime_us=[1-9][0-9]* sequence=4 marker=5 "
                   rf"layout={layout} library={shelf} grid={grid}\n").encode()
        if output.stdout or not re.fullmatch(pattern, output.stderr):
            raise ValueError(f"{arm} {case} did not emit exactly the expected stderr line")
    return {"bytes": executable.stat().st_size, "version": version.decode().strip(),
            "static_armv7_eabi5_hard_float": True, "qemu_cli": True,
            "no_neon_version": True, "binary_has_marker_prefix": True,
            "linked_production_emitter_cases": list(CASES)}, defaults


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    root = args.project.resolve()
    wrappers = list((Path(os.environ["HOME"]) / ".cache" / "cargo-zigbuild").glob(
        f"*/wrappers/*/zigcc-{TARGET}-*.sh"))
    if len(wrappers) != 1:
        raise ValueError("Expected one cargo-zigbuild ARM linker in the private build home")
    records = {}
    options = []
    for arm in ("candidate", "control"):
        records[arm], defaults = qualify(root, arm, wrappers[0], root / "marker_probe.rs")
        options.append(defaults)
    if options[0] != options[1] or records["control"]["version"] != records["candidate"]["version"]:
        raise ValueError("The two same-tree programs disagree on version or startup defaults")
    print(json.dumps({"scope": "QEMU user-mode CLI and source-linked line formatting; not a card launch or driver submission",
                      "same_version_and_startup_options": True, "arms": records}, indent=2))


if __name__ == "__main__":
    main()
