import argparse
import json
from pathlib import Path
import re
import tarfile


def build(root, output):
    root = root.resolve(strict=True)
    runtime = root / "host-ocr"
    files = {"input/navigation.sh": root / "navigation.sh"}
    for path in sorted(runtime.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        member = path.relative_to(runtime).as_posix()
        if not re.fullmatch(r"native-target|card-input|lib/[A-Za-z0-9._+-]+|models/(?:eng|chi_sim)\.traineddata", member):
            raise ValueError("unexpected runtime member")
        files["input/" + member] = path
    expected = {"input/" + name for name in ("card-input", "native-target", "lib/libXtst.so.6", "lib/libX11.so.6",
                                              "lib/ld-linux-armhf.so.3", "models/eng.traineddata", "models/chi_sim.traineddata")}
    if not expected <= files.keys() or not 1 <= len(files) <= 120:
        raise ValueError("incomplete navigation kit")
    total = sum(path.stat().st_size for path in files.values())
    if not 0 < total < 80 * 1024**2:
        raise ValueError("incorrect runtime size")
    with tarfile.open(output, "x:gz", compresslevel=2, format=tarfile.USTAR_FORMAT) as archive:
        for name, path in files.items():
            if not path.is_file() or path.is_symlink() or path.resolve(strict=True) != path:
                raise ValueError("not a plain kit member")
            item = tarfile.TarInfo(name)
            item.size, item.mode = path.stat().st_size, 0o755
            with path.open("rb") as stream:
                archive.addfile(item, stream)
    return {name: str(path) for name, path in files.items()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the navigation-only interface to an existing public toolchain.")
    parser.add_argument("inputs", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.inputs, args.output), indent=2))
