"""Freeze a synthetic, non-indexed startup cohort. No Kindle transport."""

import argparse
import json
import os
from pathlib import Path
import random
import secrets
import sqlite3
import subprocess
import tarfile
import sys

from PIL import Image, ImageDraw

from cohort import CONFIRMATION, ORIGIN, PAIR_COUNTS, schedule

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))
from analyze import instrument


WORK = Path("/work")
PROGRAMS = WORK / "programs"
MEDIA = WORK / "synthetic-books"


def pictures():
    MEDIA.mkdir()
    for number in range(1, 7):
        size = (984 + number * 18, 1392 + number * 21)
        generator = random.Random(20261120 + number)
        color = generator.randbytes(size[0] * size[1] * 3)
        image = Image.frombytes("RGB", size, color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 100, 380 + number * 45, 320), fill=(number * 32, 240, 40))
        directory = MEDIA / f"Fixture-{number:02}"
        directory.mkdir()
        image.save(directory / "001.jpg", quality=86, subsampling=1)


def freeze(regime, mode):
    cases = schedule(regime, mode)
    label = secrets.token_hex(8)
    batch = WORK / f"{regime}-{label}"
    batch.mkdir()
    stage = Path("/mnt/us") / (".aidoku-pilot-" + label)
    seed = stage / "seed"
    seed.mkdir(parents=True)
    with (batch / "desktop-import.log").open("xb") as output:
        subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 2200x3000x24",
                        str(PROGRAMS / "aidoku-desktop"), "--device", "scribe", "--offline",
                        "--screen", "library", "--data-dir", str(seed), "--import", str(MEDIA),
                        "--exit-after-ms", "6500"], stdout=output, stderr=subprocess.STDOUT,
                       env=dict(os.environ, SLINT_BACKEND="winit-software"), check=True, timeout=35)
    database = seed / "library.sqlite3"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT document FROM books WHERE source_id='local' AND in_library=1 ORDER BY title").fetchall()
    books = [json.loads(row[0]) for row in rows]
    covers = [Path(book["cover"]).resolve(strict=True) for book in books]
    if len(set(covers)) != 6 or not all(path.is_relative_to(seed) for path in covers):
        raise ValueError("The application did not import six independent staged covers")
    plan = {"origin": ORIGIN, "regime": regime, "label": label, "mode": mode,
            "seed": "six synthetic local JPEG books; no user data", "cases": cases,
            "cover_bytes": [path.stat().st_size for path in covers], "pairs": PAIR_COUNTS[mode]}
    if mode == "confirmation":
        plan["decision"] = CONFIRMATION
    for side, leaf in [("baseline", "r3-launch.sh"), ("candidate", "launch.sh")]:
        (batch / f"{side}.sh").write_bytes(instrument((PROGRAMS / leaf).read_bytes()))
    (batch / "cases").write_text(" ".join(cases) + "\n")
    (batch / "regime").write_text(regime + "\n")
    with tarfile.open(batch / "payload.tar.gz", "x:gz", compresslevel=2) as archive:
        for path, dest in [(PROGRAMS / "aidoku-arm", "candidate/app/aidoku"),
                           (PROGRAMS / "aidoku-startup-portrait", "candidate/app/aidoku-startup-portrait"),
                           (PROGRAMS / "r3-launch.sh", "control-launcher"),
                           (batch / "baseline.sh", "baseline/launch.sh"), (batch / "candidate.sh", "candidate/launch.sh"),
                           (batch / "cases", "cases"), (batch / "regime", "regime"), (database, "seed/library.sqlite3")]:
            archive.add(path, arcname=dest, recursive=False)
        for path in covers:
            archive.add(path, arcname=str(path.relative_to(stage)), recursive=False)
    (batch / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    print(batch.name, "synthetic JPEG bytes:", sum(plan["cover_bytes"]), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("regime", choices=("warm", "cold", "both"))
    parser.add_argument("--mode", choices=tuple(PAIR_COUNTS), default="pilot")
    args = parser.parse_args()
    if args.mode == "confirmation" and args.regime != "cold":
        parser.error("Only the cold screen qualified for confirmation")
    if not os.path.samefile("/work/mock-mnt", "/mnt"):
        raise SystemExit("Bind the project's mock-mnt at /mnt inside ubuntu-run first")
    if not MEDIA.exists():
        pictures()
    for workload in (("warm", "cold") if args.regime == "both" else (args.regime,)):
        freeze(workload, args.mode)
