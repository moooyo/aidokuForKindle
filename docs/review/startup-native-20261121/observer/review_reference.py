"""Review the independently released R3 reference, never a timed frame."""

import argparse
import json
import os
from pathlib import Path
import statistics
import tempfile

from PIL import Image, ImageChops

import analyze_study as study
from . import observer


LOGICAL = (930, 1240)
TOP = ((38, 48, 381, 86), (787, 47, 887, 83), (721, 138, 893, 199))
NAV = ((242, 1167, 317, 1237), (613, 1167, 689, 1237), (797, 1167, 874, 1237))


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def load_public(path):
    with Image.open(path) as image:
        need(image.format == "PNG" and image.size == LOGICAL, "unexpected_public_baseline")
        return image.convert("L")


def paper(image):
    histogram = image.histogram()
    return max(range(256), key=histogram.__getitem__)


def ink(image, background, threshold=64):
    return image.point([255 if abs(value - background) >= threshold else 0 for value in range(256)])


def chrome_similarity(image, source, area):
    left, top, right, bottom = area
    expected = ink(source.crop(area), paper(source))
    count = expected.histogram()[255]
    need(count > 40, "public_template_flat")
    tone = paper(image.crop((4, 234, 30, 252)))
    scores = []
    for dx in (-2, 0, 2):
        for dy in (-2, 0, 2):
            part = ink(image.crop((left + dx, top + dy, right + dx, bottom + dy)), tone)
            overlap = ImageChops.darker(expected, part).histogram()[255]
            scores.append(2 * overlap / (count + part.histogram()[255]))
    return max(scores)


def oriented(raw, turn):
    try:
        image = Image.frombytes("L", (observer.WIDTH, observer.HEIGHT), raw)
        if turn:
            image = image.transpose(Image.Transpose.ROTATE_180)
        return image.resize(LOGICAL, Image.Resampling.BOX)
    except ValueError:
        raise ValueError("invalid_reference_raster") from None


def identify_orientation(raw, layout, navigation):
    qualified = []
    scores = {}
    for turn in (0, 180):
        image = oriented(raw, turn)
        top = [chrome_similarity(image, layout, area) for area in TOP]
        nav = max(statistics.median(chrome_similarity(image, baseline, area) for area in NAV)
                  for baseline in (layout, navigation))
        scores[turn] = (top, nav)
        if min(top) >= .53 and statistics.median(top) >= .70 and nav >= .62:
            qualified.append((turn, image))
    need(len(qualified) == 1, "ambiguous_or_unrecognized_library_chrome")
    turn, image = qualified[0]
    weak_top, weak_nav = scores[180 - turn]
    need(statistics.median(weak_top) < .42 or weak_nav < .43, "ambiguous_library_orientation")
    return turn, image


def box(rect):
    left, top, width, height = rect
    return left // 2, top // 2, (left + width) // 2, (top + height) // 2


def has_text(image, background):
    mask = ink(image, background, 58)
    bounds = mask.getbbox()
    return (mask.histogram()[255] >= 16 and bounds is not None and
            bounds[2] - bounds[0] >= 3 and bounds[3] - bounds[1] >= 4)


def region_mass(image, rect, background):
    mask = ink(image.crop(rect), background, 32)
    return mask.histogram()[255] / (mask.width * mask.height)


def actual_art(image, rect, background):
    left, top, right, bottom = rect
    base = image.crop((left + 3, top + 3, right - 3, bottom - 3))
    width, height = base.size
    blocks, strong_rows = [], set()
    for row in range(3):
        for col in range(3):
            part = base.crop((col * width // 3, row * height // 3,
                              (col + 1) * width // 3, (row + 1) * height // 3))
            counts = part.histogram()
            main = max(range(256), key=counts.__getitem__)
            contrasting = sum(n for tone, n in enumerate(counts) if abs(tone - main) >= 24)
            if contrasting >= .025 * part.width * part.height:
                blocks.append((row, col))
            if region_mass(part, (0, 0, part.width, part.height), background) >= .12:
                strong_rows.add(row)
    sides = ((0, 0, 3, height), (width - 3, 0, width, height),
             (0, 0, width, 3), (0, height - 3, width, height))
    return (len(strong_rows) >= 2 and len(blocks) >= 3 and len({row for row, _ in blocks}) >= 2
            and len({col for _, col in blocks}) >= 2 and
            sum(region_mass(base, area, background) >= .08 for area in sides) >= 2)


def occupancy(image):
    background = paper(image.crop((4, 234, 30, 252)))
    _, slots = observer.regions(15)
    continuation, *books = map(box, slots)
    need(has_text(image.crop((136, 117, 651, 153)), background)
         and actual_art(image, continuation, background), "uncertain_continuing_title_or_art")
    flags = []
    for rect in books:
        left, top, right, bottom = rect
        caption = image.crop((left, bottom + 6, right - 24, bottom + 31))
        title = has_text(caption, background)
        if title:
            need(actual_art(image, rect, background), "occupied_slot_with_unproven_art")
            flags.append(True)
        else:
            need(region_mass(image, (left + 3, top + 3, right - 3, bottom - 3), background) < .00015 and
                 region_mass(caption, (0, 0, caption.width, caption.height), background) < .0005,
                 "art_or_marks_without_a_caption")
            flags.append(False)
    count = sum(flags)
    need(1 <= count <= 15 and flags == [True] * count + [False] * (15 - count),
         "empty_or_noncontiguous_book_grid")
    return count


def review(root, batch, layout, navigation, commit):
    batch = batch.resolve(strict=True)
    need(batch.parent == (root / "batches").resolve(strict=True) and batch.name.startswith("reference-"),
         "wrong_reference_batch")
    plan = study.validate_plan(root / "plans" / ("run-" + batch.name.removeprefix("reference-")), "reference")
    study.transaction(plan, batch)
    need(study.exact_archives(batch, ["reference"]), "unexpected_reference_archives")
    with tempfile.TemporaryDirectory(prefix=".ref-review-", dir=batch) as temp:
        files, _, upper, _ = study.unpack(batch, "reference", "indexed-uri", Path(temp))
        stamp, raw = observer.frame(files / "library-a.frame")
        need(stamp[4] > upper, "library_reference_before_open")
        try:
            turn, image = identify_orientation(raw, layout, navigation)
            count = occupancy(image)
        except ValueError:
            output = batch / "private-review-upright-unknown.png"
            if not output.exists():
                with os.fdopen(os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
                    oriented(raw, 0).resize((465, 620), Image.Resampling.LANCZOS).save(stream, "PNG")
            raise
        candidate = Path(temp) / "checked.rule"
        checks = observer.freeze(files / "stock.frame", files / "library-a.frame", files / "library-b.frame",
                                 count, turn, candidate)
        evidence = {"ok": True, "reference_only_not_timing": True, "half_turn": turn, "grid_books": count,
                    "continuation_and_grid_slots": count + 1, "independent_chrome_templates": True,
                    "all_occupied_captions_and_art_observed": True, "checks": checks}
        if commit:
            need(not (batch / "rule").exists() and not (batch / "rule").is_symlink() and
                 not (batch / "reference-review.json").exists(), "reference_already_frozen")
            with os.fdopen(os.open(batch / "reference-review.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600),
                           "w") as stream:
                json.dump(evidence, stream, sort_keys=True)
                stream.write("\n")
            with os.fdopen(os.open(batch / "rule", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
                stream.write(candidate.read_bytes())
        return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("root", "batch", "layout", "navigation"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--freeze-reviewed", action="store_true")
    args = parser.parse_args()
    try:
        result = review(args.root, args.batch, load_public(args.layout), load_public(args.navigation), args.freeze_reviewed)
    except (OSError, EOFError, study.tarfile.TarError):
        result = {"ok": False, "error": "reference_archive_or_private_file", "rule_issued": False}
    except ValueError as exc:
        result = {"ok": False, "error": str(exc), "rule_issued": False}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
