#!/usr/bin/env python3
"""Check startup controls against bundled font metrics on the verification worker."""

import argparse
import functools
import gzip
import io
import json
from pathlib import Path
import sys

from PIL import ImageFont
from fontTools.ttLib import TTFont


SWITCHES = ("native_portrait", "cached_covers", "wait_cover", "import_covers", "library_layout", "quick_reopen")
COPY = (*SWITCHES, "options", "enable_all", "restore_recommended", "apply_hint", "reopen_hint", "return_kindle", "save_failed")


def main():
    if sys.platform != "linux":
        raise SystemExit("Run on test-env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    @functools.cache
    def data(weight):
        return gzip.decompress((args.project / "assets" / "fonts" / f"NotoSansSC-{weight}.otf.gz").read_bytes())

    @functools.cache
    def font(weight, size):
        return ImageFont.truetype(io.BytesIO(data(weight)), size)

    faces = [set(TTFont(io.BytesIO(data(weight))).getBestCmap()) for weight in ["Regular", "Medium", "Bold"]]
    coverage = set.intersection(*faces)
    keys = tuple("startup_" + suffix for suffix in COPY)
    report = {}
    for locale in ["zh-CN", "zh-TW", "en", "ja"]:
        strings = json.loads((args.project / "assets/i18n" / (locale + ".json")).read_text())
        previous = set("".join(text for key, text in strings.items() if key not in keys))
        added = set("".join(strings[key] for key in keys)) - previous
        fallback = "".join(sorted(c for c in added if ord(c) not in coverage))
        slots = []
        for profile in ["pw5", "scribe"]:
            large = profile == "scribe"
            for name in SWITCHES:
                slots.append((profile, name, "Regular", 22 if large else 18, 462 if large else 500))
            for name in ["enable_all", "restore_recommended"]:
                slots.append((profile, name, "Bold", 22 if large else 18, 259 if large else 275))
            slots.append((profile, "return_kindle", "Regular", 19 if large else 17, 256 if large else 200))
            slots.append((profile, "return_kindle", "Bold", 22 if large else 18, 530 if large else 562))
            slots.append((profile, "options", "Regular", 20 if large else 18, 216 if large else 418))
            slots.append((profile, "options", "Bold", 20, 216 if large else 514))
        measurements = []
        for profile, name, weight, size, room in slots:
            width = round(font(weight, size).getlength(strings["startup_" + name]), 2)
            measurements.append({"profile": profile, "label": name, "weight": weight, "size_px": size,
                                 "width_px": width, "slot_px": room, "spare_px": round(room - width, 2)})
        report[locale] = {"additional_system_fallback_characters": fallback,
                          "single_line_slots_checked": len(measurements),
                          "tightest_slot": min(measurements, key=lambda row: row["spare_px"]),
                          "clipped_single_line_controls": [row for row in measurements if row["spare_px"] < 0]}
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return int(any(row["additional_system_fallback_characters"] or row["clipped_single_line_controls"]
                   for row in report.values()))


if __name__ == "__main__":
    raise SystemExit(main())
