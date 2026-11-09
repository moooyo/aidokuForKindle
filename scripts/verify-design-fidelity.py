#!/usr/bin/env python3
"""Prepare visual evidence for a manually reviewed, remote design feedback loop.

This tool needs Python 3.10+ and Pillow. It compares all twelve named UI frames
with the supplied reference PNGs, without deciding whether the design is good.
Run it on the designated remote verification worker, not on the local machine.

A suggestions file can be a JSON array, including an explicit empty array after
a completed review. Each item must contain id, screen, description, evidence,
status (open or fixed), and introduced_round. An optional wrapper can record the
review scope: {"round": 2, "reviewed_screens": [...], "suggestions": [...]}.
Omitted old suggestions retain their old status; omission never means fixed.

Exit code 0 means the evidence was generated with complete image coverage. It
does not mean design acceptance. Exit code 2 means invalid input or incomplete
image coverage. The advisory exit_criterion is recorded separately in JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat


SCHEMA_VERSION = 1
SCREEN_REFERENCES = {
    "pw5-library": "reference-01.png",
    "pw5-detail": "reference-02.png",
    "pw5-reader": "reference-03.png",
    "pw5-sources": "reference-04.png",
    "pw5-catalog": "reference-05.png",
    "pw5-search": "reference-06.png",
    "pw5-history": "reference-07.png",
    "pw5-settings": "reference-08.png",
    "scribe-library": "reference-09.png",
    "scribe-detail": "reference-10.png",
    "scribe-sources": "reference-11.png",
    "scribe-landscape-reader": "reference-12.png",
}
NORMALIZATION = {
    "version": 1,
    "canvas": "reference image size after EXIF orientation",
    "fit": "preserve aspect ratio, center, and pad with white; never crop",
    "resampling": "Lanczos",
    "alpha_background": "white",
    "comparison_order": "reference artwork on the left, UI screenshot on the right",
    "diff": "absolute RGB difference on the normalized canvas; no amplification",
}
LIMITATIONS = [
    "Manual review is required for every screen; pixel scores never establish design acceptance.",
    "Reference pixels may contain artwork from the original PDF. Artwork differences do not establish business correctness or incorrectness.",
    "Image matching cannot establish navigation, source behavior, import correctness, accessibility, or physical e-ink quality.",
    "State and metadata sidecars are retained as evidence, without asserting their semantic correctness.",
    "SSIM is not computed. Pillow provides the pixel and grayscale measurements used here.",
]


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("The round must be a positive integer")
    return number


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--screenshots", type=Path, required=True, help="Directory containing the twelve named UI PNG screenshots")
    parser.add_argument("--reference-dir", type=Path, required=True, help="Directory containing reference-01.png through reference-12.png")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for comparisons, metrics, and the review manifest")
    parser.add_argument("--round", type=positive_integer, required=True, help="Current feedback round, starting at 1")
    parser.add_argument("--previous-report", type=Path, help="Previous manifest.json, or its containing directory")
    parser.add_argument("--suggestions-file", type=Path, help="Manually recorded review JSON; an explicit [] records a review with no findings")
    return parser.parse_args()


def finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Non-finite JSON number: {value}")
    return number


def reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def read_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8-sig"),
        parse_float=finite_json_float,
        parse_constant=reject_json_constant,
    )


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_suggestions(value: Any, round_number: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("The suggestions value must be a JSON array")
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = {"id", "screen", "description", "evidence", "status", "introduced_round"}
    for item in value:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("Every suggestion needs id, screen, description, evidence, status, and introduced_round")
        for field in ("id", "screen", "description"):
            if not isinstance(item[field], str) or not item[field].strip():
                raise ValueError(f"Suggestion {field} must be a nonempty string")
        identifier = item["id"]
        if identifier in seen:
            raise ValueError(f"Duplicate suggestion id: {identifier}")
        seen.add(identifier)
        if item["screen"] not in SCREEN_REFERENCES:
            raise ValueError(f"Unknown suggestion screen: {item['screen']}")
        if item["status"] not in ("open", "fixed"):
            raise ValueError(f"Invalid suggestion status for {identifier}")
        introduced = item["introduced_round"]
        if type(introduced) is not int or not 1 <= introduced <= round_number:
            raise ValueError(f"Invalid introduced_round for {identifier}")
        evidence = item["evidence"]
        valid_evidence = isinstance(evidence, str) and bool(evidence.strip())
        if isinstance(evidence, list):
            valid_evidence = bool(evidence) and all(isinstance(part, str) and bool(part.strip()) for part in evidence)
        if not valid_evidence:
            raise ValueError(f"Suggestion evidence must be a nonempty string or string array: {identifier}")
        suggestions.append({field: item[field] for field in ("id", "screen", "description", "evidence", "status", "introduced_round")})
    return suggestions


def load_previous(path: Path | None, round_number: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if path is None:
        return None, None
    path = path.resolve()
    if path.is_dir():
        path /= "manifest.json"
    previous = read_json(path)
    if not isinstance(previous, dict) or previous.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("The previous report must be a manifest.json produced by this tool")
    previous_round = previous.get("round")
    if type(previous_round) is not int or previous_round < 1 or previous_round >= round_number:
        raise ValueError("The previous report round must be positive and earlier than the current round")
    if previous.get("mapping") != SCREEN_REFERENCES:
        raise ValueError("The previous report uses a different required screen mapping")
    review = previous.get("review")
    if not isinstance(review, dict):
        raise ValueError("The previous report has no review record")
    review["suggestions"] = validate_suggestions(review.get("suggestions"), previous_round)
    return previous, {"path": str(path), "sha256": file_hash(path), "round": previous_round}


def review_record(path: Path | None, round_number: int, previous: dict[str, Any] | None) -> dict[str, Any]:
    previous_items = previous["review"]["suggestions"] if previous else []
    previous_by_id = {item["id"]: item for item in previous_items}
    supplied: list[dict[str, Any]] = []
    recorded = path is not None
    reviewed_screens = list(SCREEN_REFERENCES) if recorded else []
    if path is not None:
        value = read_json(path.resolve())
        if isinstance(value, dict):
            if "round" in value and (type(value["round"]) is not int or value["round"] != round_number):
                raise ValueError("The suggestions file round does not match --round")
            if "review_recorded" in value and type(value["review_recorded"]) is not bool:
                raise ValueError("review_recorded must be a boolean")
            recorded = value.get("review_recorded", True)
            reviewed_screens = value.get("reviewed_screens", list(SCREEN_REFERENCES))
            value = value.get("suggestions")
        if not isinstance(reviewed_screens, list) or not all(isinstance(screen, str) and screen in SCREEN_REFERENCES for screen in reviewed_screens):
            raise ValueError("reviewed_screens must contain only known screen names")
        if len(set(reviewed_screens)) != len(reviewed_screens):
            raise ValueError("reviewed_screens contains duplicate screen names")
        supplied = validate_suggestions(value, round_number)
    current_by_id = dict(previous_by_id)
    for item in supplied:
        old = previous_by_id.get(item["id"])
        if old is not None:
            if old["screen"] != item["screen"] or old["introduced_round"] != item["introduced_round"]:
                raise ValueError(f"An existing suggestion must keep its screen and introduced_round: {item['id']}")
        elif item["introduced_round"] != round_number:
            raise ValueError(f"A newly recorded suggestion must have introduced_round equal to --round: {item['id']}")
        current_by_id[item["id"]] = item
    supplied_ids = {item["id"] for item in supplied}
    new_ids = sorted(set(current_by_id) - set(previous_by_id))
    open_ids = sorted(identifier for identifier, item in current_by_id.items() if item["status"] == "open")
    fixed_ids = sorted(identifier for identifier, item in current_by_id.items() if item["status"] == "fixed")
    still_open_ids = sorted(identifier for identifier in open_ids if identifier in previous_by_id and previous_by_id[identifier]["status"] == "open")
    fixed_this_round = sorted(identifier for identifier in fixed_ids if identifier in previous_by_id and previous_by_id[identifier]["status"] == "open")
    reopened_ids = sorted(identifier for identifier in open_ids if identifier in previous_by_id and previous_by_id[identifier]["status"] == "fixed")
    return {
        "recorded": recorded,
        "source": str(path.resolve()) if path else None,
        "note": "A supplied review file records manual review; an array applies to all required screens." if recorded else "Review not yet recorded. No clean round can be inferred.",
        "reviewed_screens": sorted(reviewed_screens) if recorded else [],
        "coverage_complete": recorded and set(reviewed_screens) == set(SCREEN_REFERENCES),
        "no_new_suggestions": not new_ids if recorded else None,
        "suggestions": [current_by_id[identifier] for identifier in sorted(current_by_id)],
        "carried_forward_ids": sorted(set(previous_by_id) - supplied_ids),
        "delta": {
            "new_ids": new_ids,
            "still_open_ids": still_open_ids,
            "fixed_ids": fixed_this_round,
            "reopened_ids": reopened_ids,
        },
        "counts": {
            "new": len(new_ids),
            "open": len(open_ids),
            "still_open": len(still_open_ids),
            "fixed": len(fixed_ids),
            "fixed_this_round": len(fixed_this_round),
            "reopened": len(reopened_ids),
        },
    }


def open_image(path: Path) -> tuple[Image.Image, dict[str, Any]]:
    with Image.open(path) as original:
        original.load()
        source = {"width": original.width, "height": original.height, "mode": original.mode, "sha256": file_hash(path)}
        oriented = ImageOps.exif_transpose(original).convert("RGBA")
        background = Image.new("RGBA", oriented.size, "white")
        background.alpha_composite(oriented)
        image = background.convert("RGB")
    source["oriented_width"], source["oriented_height"] = image.size
    return image, source


def fit_image(image: Image.Image, size: tuple[int, int]) -> tuple[Image.Image, dict[str, Any]]:
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    fitted = ImageOps.contain(image, size, method=resampling)
    offset = ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2)
    canvas = Image.new("RGB", size, "white")
    canvas.paste(fitted, offset)
    return canvas, {"resized_width": fitted.width, "resized_height": fitted.height, "offset_x": offset[0], "offset_y": offset[1]}


def grayscale_statistics(image: Image.Image) -> dict[str, Any]:
    histogram = image.convert("L").histogram()
    count = image.width * image.height
    mean = sum(level * amount for level, amount in enumerate(histogram)) / count
    variance = sum((level - mean) ** 2 * amount for level, amount in enumerate(histogram)) / count
    occupied = [level for level, amount in enumerate(histogram) if amount]
    return {
        "pixel_count": count,
        "mean": round(mean, 6),
        "standard_deviation": round(math.sqrt(variance), 6),
        "minimum": min(occupied),
        "maximum": max(occupied),
        "unique_levels": len(occupied),
        "entropy_bits": round(-sum((amount / count) * math.log2(amount / count) for amount in histogram if amount), 6),
        "near_black_fraction_0_to_15": round(sum(histogram[:16]) / count, 6),
        "near_white_fraction_240_to_255": round(sum(histogram[240:]) / count, 6),
        "midtone_fraction_16_to_239": round(sum(histogram[16:240]) / count, 6),
        "histogram_0_to_255": histogram,
    }


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def comparison_image(reference: Image.Image, screenshot: Image.Image) -> Image.Image:
    header = 44
    gutter = 16
    comparison = Image.new("RGB", (reference.width * 2 + gutter, reference.height + header), "white")
    comparison.paste(reference, (0, header))
    comparison.paste(screenshot, (reference.width + gutter, header))
    draw = ImageDraw.Draw(comparison)
    label_font = font(18)
    draw.text((8, 10), "Reference artwork", fill="black", font=label_font)
    draw.text((reference.width + gutter + 8, 10), "UI screenshot", fill="black", font=label_font)
    return comparison


def contact_tile(screen: str, reference: Image.Image | None, screenshot: Image.Image | None, note: str) -> Image.Image:
    panel_size = (300, 400)
    tile = Image.new("RGB", (624, 464), "white")
    draw = ImageDraw.Draw(tile)
    draw.text((8, 6), screen, fill="black", font=font(16))
    draw.text((8, 31), "Reference", fill="#555555", font=font(12))
    draw.text((320, 31), "UI screenshot", fill="#555555", font=font(12))
    for image, x in ((reference, 8), (screenshot, 320)):
        if image is not None:
            panel, _ = fit_image(image, panel_size)
            tile.paste(panel, (x, 50))
        else:
            draw.rectangle((x, 50, x + 299, 449), fill="#eeeeee", outline="#888888")
            draw.text((x + 12, 72), "Image unavailable", fill="black", font=font(14))
    draw.text((8, 450), note, fill="#555555", font=font(10))
    return tile


def sidecars(screenshot: Path) -> list[dict[str, Any]]:
    names = {
        f"{screenshot.stem}{ending}"
        for ending in (".json", ".state.json", ".metadata.json", "-state.json", "-metadata.json")
    }
    names.update(f"{screenshot.name}{ending}" for ending in (".json", ".state.json", ".metadata.json"))
    evidence: list[dict[str, Any]] = []
    for name in sorted(names):
        path = screenshot.parent / name
        if path.is_file():
            item: dict[str, Any] = {"path": str(path)}
            try:
                item["sha256"] = file_hash(path)
                item["data"] = read_json(path)
            except (OSError, ValueError) as error:
                item["read_error"] = str(error)
            evidence.append(item)
    return evidence


def process_screen(screen: str, reference_path: Path, screenshot_path: Path, output: Path) -> tuple[dict[str, Any], Image.Image]:
    directory = output / screen
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("comparison.png", "diff.png"):
        (directory / name).unlink(missing_ok=True)
    record: dict[str, Any] = {
        "screen": screen,
        "reference_path": str(reference_path),
        "screenshot_path": str(screenshot_path),
        "status": "missing",
        "missing_inputs": [label for label, path in (("reference", reference_path), ("screenshot", screenshot_path)) if not path.is_file()],
        "artifacts": {"metrics": f"{screen}/metrics.json"},
        "sidecars": sidecars(screenshot_path),
    }
    reference = None
    screenshot = None
    try:
        if reference_path.is_file():
            reference, record["reference"] = open_image(reference_path)
        if screenshot_path.is_file():
            screenshot, record["screenshot"] = open_image(screenshot_path)
        if not record["missing_inputs"]:
            assert reference is not None and screenshot is not None
            normalized, transform = fit_image(screenshot, reference.size)
            difference = ImageChops.difference(reference, normalized)
            color_stats = ImageStat.Stat(difference)
            gray_difference = ImageChops.difference(reference.convert("L"), normalized.convert("L"))
            gray_stats = ImageStat.Stat(gray_difference)
            gray_histogram = gray_difference.histogram()
            pixel_count = reference.width * reference.height
            rgb_mae = sum(color_stats.mean) / 3
            record.update({
                "status": "complete",
                "canvas": {"width": reference.width, "height": reference.height},
                "native_dimensions_match": reference.size == screenshot.size,
                "aspect_ratio_difference": round(abs(reference.width / reference.height - screenshot.width / screenshot.height), 8),
                "screenshot_transform": transform,
                "pixel_metrics": {
                    "rgb_mae_0_to_255": round(rgb_mae, 6),
                    "rgb_mae_normalized": round(rgb_mae / 255, 8),
                    "rgb_channel_mae": [round(value, 6) for value in color_stats.mean],
                    "grayscale_mae_0_to_255": round(gray_stats.mean[0], 6),
                    "grayscale_rmse_0_to_255": round(gray_stats.rms[0], 6),
                    "grayscale_exact_match_fraction": round(gray_histogram[0] / pixel_count, 8),
                    "grayscale_difference_above_8_fraction": round(sum(gray_histogram[9:]) / pixel_count, 8),
                    "ssim": None,
                },
                "grayscale_statistics": {
                    "reference": grayscale_statistics(reference),
                    "screenshot_native": grayscale_statistics(screenshot),
                    "screenshot_normalized": grayscale_statistics(normalized),
                },
            })
            comparison_image(reference, normalized).save(directory / "comparison.png")
            difference.save(directory / "diff.png")
            record["artifacts"].update({"comparison": f"{screen}/comparison.png", "diff": f"{screen}/diff.png"})
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        record["status"] = "error"
        record["error"] = f"{type(error).__name__}: {error}"
    write_json(directory / "metrics.json", record)
    note = "Manual review required; no score is a pass." if record["status"] == "complete" else f"Coverage incomplete: {record['status']}"
    return record, contact_tile(screen, reference, screenshot, note)


def previous_qualifies(previous: dict[str, Any] | None, baseline_id: str, round_number: int) -> bool:
    if previous is None or previous["round"] != round_number - 1 or previous.get("baseline_id") != baseline_id:
        return False
    review = previous["review"]
    screens = previous.get("screens", {})
    if not isinstance(screens, dict) or set(screens) != set(SCREEN_REFERENCES):
        return False
    all_pairs_complete = all(isinstance(item, dict) and item.get("status") == "complete" for item in screens.values())
    reviewed_screens = review.get("reviewed_screens")
    if not isinstance(reviewed_screens, list) or not all(isinstance(screen, str) for screen in reviewed_screens):
        return False
    all_screens_reviewed = set(reviewed_screens) == set(SCREEN_REFERENCES)
    delta = review.get("delta")
    new_ids = delta.get("new_ids") if isinstance(delta, dict) else None
    return (
        review.get("recorded") is True
        and all_pairs_complete
        and all_screens_reviewed
        and new_ids == []
        and all(item["status"] == "fixed" for item in review["suggestions"])
    )


def markdown_report(manifest: dict[str, Any]) -> str:
    coverage = manifest["coverage"]
    review = manifest["review"]
    counts = review["counts"]
    lines = [
        f"# Design fidelity evidence: round {manifest['round']}",
        "",
        "Manual review is required. This report does not grant design or business acceptance.",
        "",
        f"Image coverage: {coverage['completed']}/{coverage['expected']}; complete: {str(coverage['complete']).lower()}.",
        f"Review recorded: {str(review['recorded']).lower()}; review coverage complete: {str(review['coverage_complete']).lower()}.",
        f"Suggestions: {counts['new']} new, {counts['still_open']} still open, {counts['open']} total open, {counts['fixed_this_round']} fixed this round, {counts['fixed']} total fixed, {counts['reopened']} reopened.",
        f"Consecutive qualifying rounds: {manifest['criterion']['consecutive_qualifying_rounds']}.",
        f"Advisory exit_criterion: {str(manifest['exit_criterion']).lower()}. Human judgment is still required.",
        "",
        review["note"],
        "",
        "[Contact sheet](contact-sheet.jpg) | [Complete metrics](metrics.json) | [Machine-readable manifest](manifest.json)",
        "",
        "## Screen evidence",
        "",
        "| Screen | Reference | Status | Comparison | Difference | Metrics |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for screen, record in manifest["screens"].items():
        artifacts = record["artifacts"]
        comparison = f"[View]({artifacts['comparison']})" if "comparison" in artifacts else "Unavailable"
        difference = f"[View]({artifacts['diff']})" if "diff" in artifacts else "Unavailable"
        lines.append(f"| {screen} | {SCREEN_REFERENCES[screen]} | {record['status']} | {comparison} | {difference} | [JSON]({artifacts['metrics']}) |")
    lines.extend(["", "## Recorded suggestions", ""])
    if not review["suggestions"]:
        lines.append("No suggestions are recorded." if review["recorded"] else "Review not yet recorded; an empty finding count is not a clean round.")
    for item in review["suggestions"]:
        lines.append(f"- **{item['id']}** ({item['screen']}, {item['status']}, introduced in round {item['introduced_round']}): {item['description']}")
        evidence = item["evidence"] if isinstance(item["evidence"], str) else "; ".join(item["evidence"])
        lines.append(f"  Evidence: {evidence}")
    if review["carried_forward_ids"]:
        lines.extend(["", "Omitted previous suggestions retain their status: " + ", ".join(review["carried_forward_ids"]) + "."])
    lines.extend(["", "## Criterion and limitations", ""])
    lines.append("A qualifying round requires recorded review of every required screen, complete image coverage, no new suggestions, and no open suggestions. Two adjacent qualifying rounds must use identical reference files and comparison settings. The tool does not infer a review from pixel scores.")
    for reason in manifest["criterion"]["blocking_reasons"]:
        lines.append(f"- {reason}")
    lines.extend(["", "Both comparison panels have the reference canvas dimensions. The UI frame is fitted without cropping or stretching, with white padding when needed. Absolute differences and MAE include any padding. Native dimensions and grayscale histograms remain available in metrics JSON.", ""])
    for limitation in LIMITATIONS:
        lines.append(f"- {limitation}")
    lines.extend(["", "## Review input example", "", "An explicit empty JSON array (`[]`) records completed manual review of all required screens with no findings. A scoped review can use a wrapper with `round`, `reviewed_screens`, and `suggestions`. Fixed findings must keep their stable ID and original `introduced_round`.", "", "```json", json.dumps([{"id": "library-title-spacing", "screen": "pw5-library", "description": "Align the title baseline with the reference.", "evidence": "pw5-library/comparison.png", "status": "open", "introduced_round": manifest["round"]}], indent=2), "```", ""])
    return "\n".join(lines)


def main() -> int:
    options = arguments()
    screenshots = options.screenshots.resolve()
    references = options.reference_dir.resolve()
    output = options.output_dir.resolve()
    for label, directory in (("screenshots", screenshots), ("reference-dir", references)):
        if not directory.is_dir():
            raise ValueError(f"--{label} must name an existing directory: {directory}")
    previous, previous_metadata = load_previous(options.previous_report, options.round)
    review = review_record(options.suggestions_file, options.round, previous)
    output.mkdir(parents=True, exist_ok=True)
    screens: dict[str, dict[str, Any]] = {}
    sheet = Image.new("RGB", (624 * 3, 464 * 4 + 56), "#dddddd")
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 8), f"Design fidelity evidence - round {options.round} - manual review required", fill="black", font=font(18))
    draw.text((12, 32), "Reference artwork left; UI screenshot right. Pixel differences do not establish business correctness.", fill="black", font=font(12))
    for index, (screen, filename) in enumerate(SCREEN_REFERENCES.items()):
        record, tile = process_screen(screen, references / filename, screenshots / f"{screen}.png", output)
        screens[screen] = record
        sheet.paste(tile, ((index % 3) * 624, 56 + (index // 3) * 464))
    sheet.save(output / "contact-sheet.jpg", quality=92)
    completed = [screen for screen, record in screens.items() if record["status"] == "complete"]
    coverage = {
        "expected": len(SCREEN_REFERENCES),
        "completed": len(completed),
        "missing": [screen for screen, record in screens.items() if record["status"] == "missing"],
        "errors": [screen for screen, record in screens.items() if record["status"] == "error"],
        "complete": len(completed) == len(SCREEN_REFERENCES),
    }
    reference_hashes = {screen: record.get("reference", {}).get("sha256") for screen, record in screens.items()}
    baseline = {"mapping": SCREEN_REFERENCES, "reference_hashes": reference_hashes, "normalization": NORMALIZATION}
    baseline_id = hashlib.sha256(json.dumps(baseline, sort_keys=True).encode("utf-8")).hexdigest()
    qualifies = coverage["complete"] and review["recorded"] and review["coverage_complete"] and review["no_new_suggestions"] is True and review["counts"]["open"] == 0
    prior_qualifies = previous_qualifies(previous, baseline_id, options.round)
    consecutive = (2 if prior_qualifies else 1) if qualifies else 0
    blocking_reasons = []
    if not coverage["complete"]:
        blocking_reasons.append("Required image coverage is incomplete.")
    if not review["recorded"]:
        blocking_reasons.append("Manual review has not been recorded for this round.")
    elif not review["coverage_complete"]:
        blocking_reasons.append("Manual review does not cover every required screen.")
    if review["delta"]["new_ids"]:
        blocking_reasons.append("New suggestions were recorded in this round.")
    if review["counts"]["open"]:
        blocking_reasons.append("Known suggestions remain open.")
    if not prior_qualifies:
        blocking_reasons.append("The immediately previous round is absent, uses different references/settings, or does not qualify.")
    criterion = {
        "current_qualifies": qualifies,
        "previous_qualifies": prior_qualifies,
        "consecutive_qualifying_rounds": consecutive,
        "exit_criterion": consecutive >= 2,
        "manual_acceptance": None,
        "human_judgment_still_required": True,
        "blocking_reasons": blocking_reasons,
    }
    metrics = {"schema_version": SCHEMA_VERSION, "round": options.round, "normalization": NORMALIZATION, "limitations": LIMITATIONS, "screens": screens}
    write_json(output / "metrics.json", metrics)
    manifest_screens = {
        screen: {key: value for key, value in record.items() if key != "grayscale_statistics"}
        for screen, record in screens.items()
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool": "verify-design-fidelity.py",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "round": options.round,
        "screenshots_directory": str(screenshots),
        "reference_directory": str(references),
        "output_directory": str(output),
        "mapping": SCREEN_REFERENCES,
        "baseline_id": baseline_id,
        "normalization": NORMALIZATION,
        "previous_report": previous_metadata,
        "coverage": coverage,
        "review_recorded": review["recorded"],
        "review": review,
        "criterion": criterion,
        "exit_criterion": criterion["exit_criterion"],
        "manual_review_required": True,
        "limitations": LIMITATIONS,
        "artifacts": {"contact_sheet": "contact-sheet.jpg", "metrics": "metrics.json", "markdown": "manifest.md", "manifest": "manifest.json"},
        "screens": manifest_screens,
    }
    write_json(output / "manifest.json", manifest)
    (output / "manifest.md").write_text(markdown_report(manifest), encoding="utf-8")
    print(json.dumps({"manifest": str(output / "manifest.json"), "round": options.round, "coverage_complete": coverage["complete"], "review_recorded": review["recorded"], "suggestions": review["counts"], "exit_criterion": criterion["exit_criterion"], "manual_review_required": True}, ensure_ascii=True))
    return 0 if coverage["complete"] else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"Design fidelity evidence generation failed: {error}", file=sys.stderr)
        sys.exit(2)
