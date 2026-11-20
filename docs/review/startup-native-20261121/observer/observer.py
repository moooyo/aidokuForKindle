"""Freeze independently observed Library regions or measure an AFW2 trace offline."""

import argparse
import collections
import json
import math
import os
from pathlib import Path
import statistics
import struct
import sys


WIDTH, HEIGHT, STEP, OFFSET = 1860, 2480, 32, 16
XS, YS = range(OFFSET, WIDTH, STEP), range(OFFSET, HEIGHT, STEP)
POINTS = len(XS) * len(YS)
FRAME_BYTES = WIDTH * HEIGHT
PERIOD, SLOTS, DWELL = 75_000, 160, 200_000
ANCHOR_WIDTH, GAP, SCAN, CPU_PCT = 100_000, 150_000, 50_000, 10
STOCK_MAX_AGE = 30_000_000
RECORD = struct.Struct("<QQI")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def load(path, limit):
    require(not path.is_symlink() and path.is_file() and path.stat().st_size <= limit,
            "unexpected_private_input")
    return path.read_bytes()


def header(raw, magic, fields):
    text, newline, data = raw.partition(b"\n")
    parts = text.split()
    require(newline and len(text) < 256 and len(parts) == fields + 1 and parts[0] == magic and
            all(item.isdigit() and len(item) <= 20 for item in parts[1:]), "invalid_header")
    return [int(item) for item in parts[1:]], data


def geometry(rotation, horizontal, vertical):
    require(rotation in (1, 3) and horizontal in (1860, 1872) and vertical in (2480, 4960),
            "unsupported_geometry")


def frame(path):
    info, pixels = header(load(path, FRAME_BYTES + 256), b"AFR2", 6)
    rotation, horizontal, vertical, pan, before, after = info
    geometry(rotation, horizontal, vertical)
    require(pan in range(0, vertical, HEIGHT) and 0 < before <= after and len(pixels) == FRAME_BYTES,
            "invalid_capture")
    return info, pixels


def regions(count):
    require(type(count) is int and 1 <= count <= 15, "invalid_populated_grid_count")
    text = [(0, 72, WIDTH, 128), (0, 2320, WIDTH, 160), (272, 234, 1030, 144)]
    for band in range(math.ceil(count / 5)):
        text.append((72, 936 + round(band * 542.88), 324 + 348 * min(4, count - 5 * band - 1), 92))
    art = [(80, 224, 152, 224)]
    for cell in range(count):
        art.append((80 + (cell % 5) * 348, 504 + round(cell // 5 * 542.88), 308, 419))
    return text, art


def indexes(rectangle, half_turn):
    left, top, width, height = rectangle
    require(0 <= left < left + width <= WIDTH and 72 <= top < top + height <= HEIGHT,
            "region_outside_library")
    result = []
    for row, raw_y in enumerate(YS):
        for column, raw_x in enumerate(XS):
            x, y = (WIDTH - 1 - raw_x, HEIGHT - 1 - raw_y) if half_turn else (raw_x, raw_y)
            if left <= x < left + width and top <= y < top + height:
                result.append(row * len(XS) + column)
    require(len(result) >= 16, "insufficient_region_points")
    return tuple(result)


def sample(full):
    return bytes(full[row * WIDTH + column] for row in YS for column in XS)


def require_contrast(pixels, stock):
    mode = collections.Counter(pixels).most_common(1)[0][0]
    minimum = max(3, math.ceil(len(pixels) * .003))
    require(sum(abs(value - mode) >= 16 for value in pixels) >= minimum, "featureless_library_region")
    require(sum(abs(left - right) >= 16 for left, right in zip(pixels, stock)) >= minimum,
            "indistinct_stock_region")


def body(full, turn):
    skip = 72 * WIDTH
    return memoryview(full)[:-skip] if turn else memoryview(full)[skip:]


def status_pixels(full, turn):
    left = 0 if turn else WIDTH // 2
    top = HEIGHT - 72 if turn else 0
    return b"".join(full[row * WIDTH + left:row * WIDTH + left + WIDTH // 2] for row in range(top, top + 72))


def status_check(left, right, turn):
    area, observed = status_pixels(left, turn), status_pixels(right, turn)
    for band in (area, observed):
        counts = collections.Counter(band)
        tone = counts.most_common(1)[0][0]
        require(sum(value for level, value in counts.items() if abs(level - tone) >= 16) >= 32,
                "unobservable_right_status")
    require(area == observed, "right_status_differs")


def freeze(stock, first, second, count, turn, output):
    (stock_info, native), (first_info, settled), (second_info, later) = map(frame, (stock, first, second))
    require(stock_info[:3] == first_info[:3] == second_info[:3], "reference_layout_changed")
    require(stock_info[5] < first_info[4] and second_info[4] - first_info[5] >= 300_000,
            "references_not_independent_or_spaced")
    require(turn in (0, 180) and body(settled, turn) == body(later, turn), "unstable_library_reference")
    status_check(settled, later, turn)
    text, art = regions(count)
    target, empty = sample(settled), sample(native)
    for index, rect in enumerate([*text, *art]):
        mask = indexes(rect, turn)
        values = bytes(target[at] for at in mask)
        require_contrast(values, bytes(empty[at] for at in mask))
        if index >= len(text):
            require(len(set(values)) >= 3, "unobservable_cover_detail")
    shape = first_info[:3]
    payload = (f"AFR3RULE {shape[0]} {shape[1]} {shape[2]} {turn} {count} {second_info[5]}\n".encode() + target)
    with os.fdopen(os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as handle:
        handle.write(payload)
    return {"ok": True, "grid_books": count, "observed_cover_slots": count + 1,
            "reference_body_stable": True, "region_contrast_checked": True, "right_status_stable_observable": True}


def rule(path):
    info, raw = header(load(path, POINTS + 256), b"AFR3RULE", 6)
    geometry(*info[:3])
    require(info[3] in (0, 180) and info[5] > 0 and len(raw) == POINTS, "invalid_rule")
    text, art = regions(info[4])
    masks = [indexes(rect, info[3]) for rect in text + art]
    return info, raw, masks, len(text)


def stock_target(plan, input_stock):
    details, target, masks, boundary = rule(plan)
    stamp, stock = frame(input_stock)
    require(stamp[:3] == details[:3] and stamp[4] > details[5], "stock_not_from_new_visit")
    idle = sample(stock)
    for mask in masks:
        require_contrast(bytes(target[key] for key in mask), bytes(idle[key] for key in mask))
    return details, target, masks, boundary, stamp, idle


def trace(path, expected):
    info, raw = header(load(path, SLOTS * (POINTS + RECORD.size) + 256), b"AFW2", 8)
    geometry(*info[:3])
    start, end, cpu, count, missed = info[3:]
    require(info[:3] == expected[:3] and 0 < start < end and 2 <= count <= SLOTS and count + missed == SLOTS
            and len(raw) == count * (POINTS + RECORD.size), "invalid_or_wrong_trace")
    require(cpu <= end - start and PERIOD * (SLOTS - 2) <= end - start <= PERIOD * (SLOTS + 2),
            "trace_window")
    rows = []
    previous = start
    for pos in range(0, len(raw), RECORD.size + POINTS):
        before, after, pan = RECORD.unpack_from(raw, pos)
        require(previous <= before <= after <= end and after - before <= SCAN and
                pan in range(0, info[2], HEIGHT), "invalid_scan")
        previous = after
        rows.append((before, after, raw[pos + RECORD.size:pos + RECORD.size + POINTS]))
    gaps = [right[0] - left[1] for left, right in zip(rows, rows[1:])]
    pct = 100 * cpu / (end - start)
    require(max(gaps) <= GAP and pct <= CPU_PCT, "observer_gap_or_cpu")
    return rows, {"points_per_scan": POINTS, "period_ms": PERIOD // 1000, "missed_slots": missed,
                  "max_gap_ms": round(max(gaps) / 1000, 3), "cpu_pct_one_core": round(pct, 3),
                  "median_scan_ms": round(statistics.median(hi - lo for lo, hi, _ in rows) / 1000, 3)}


def edge(rows, matched, lower, upper):
    require(not any(hit and row[1] <= upper for row, hit in zip(rows, matched)), "early_target_match")
    require(matched[-1], "terminal_picture_differs")
    begin = len(rows) - 1
    while begin > 0 and matched[begin - 1]:
        begin -= 1
    require(begin > 0 and rows[-1][0] - rows[begin][1] >= DWELL, "target_not_stable")
    require(rows[begin - 1][0] >= upper and not matched[begin - 1], "no_post_trigger_mismatch")
    bracket = rows[begin - 1][0] - upper, rows[begin][1] - lower
    require(0 <= bracket[0] <= bracket[1] and bracket[1] - bracket[0] <= GAP + ANCHOR_WIDTH + SCAN,
            "edge_too_wide")
    return [round(value / 1000, 3) for value in bracket]


def measure(plan, observation, lower, upper, visit_stock):
    details, target, masks, boundary, stock_stamp, idle = stock_target(plan, visit_stock)
    require(0 < lower <= upper and upper - lower <= ANCHOR_WIDTH, "anchor_clock_or_width")
    rows, diagnostics = trace(observation, details)
    require(rows[0][1] < lower and rows[-1][0] > upper, "anchor_outside_trace")
    require(stock_stamp[5] <= rows[0][0] and 0 < lower - stock_stamp[5] <= STOCK_MAX_AGE,
            "stock_not_bound_to_trigger")
    expected = [bytes(target[index] for index in mask) for mask in masks]
    status_mask = indexes((0, 72, WIDTH, HEIGHT - 72), details[3])
    before = [pixels for _, end, pixels in rows if end < lower]
    require(len(before) >= 2 and all(all(pixels[index] == idle[index] for index in status_mask)
                                    for pixels in [*before[:2], *before[-2:]]), "pretrigger_stock_mismatch")
    key_masks = [tuple(index for region in area for index in region)
                 for area in (masks[:boundary], masks[boundary:], masks)]
    references = [b"".join(group) for group in (expected[:boundary], expected[boundary:], expected)]
    endpoints = {}
    for name, mask, reference in zip(("text_and_navigation", "all_cover_slots", "complete_library"),
                                     key_masks, references):
        hits = [all(pixels[key] == reference[pos] for pos, key in enumerate(mask)) for _, _, pixels in rows]
        endpoints[name] = edge(rows, hits, lower, upper)
    require(all(rows[-1][2][index] == target[index] for index in status_mask), "final_sampled_body_differs")
    return {"ok": True, "scope": "sampled_framebuffer_only", "unit": "ms", "cover_slots": details[4] + 1,
            "stability_ms_not_added": DWELL // 1000, "relative_to_supplied_device_clock_anchor": endpoints,
            "observer": diagnostics}


def compare_settled(plan, first, final):
    details, target, _, _ = rule(plan)
    original, expected = frame(first)
    observed, current = frame(final)
    require(original[:3] == observed[:3] == details[:3] and sample(expected) == target,
            "wrong_full_reference")
    require(body(current, details[3]) == body(expected, details[3]), "final_full_body_differs")
    status_check(expected, current, details[3])
    return {"ok": True, "settled_full_body_equal": True, "visible_pixels_excluding_status": WIDTH * (HEIGHT - 72),
            "right_status_equal_and_observable": True, "right_status_pixels": WIDTH * 72 // 2}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("freeze")
    for name in ("stock", "first", "second", "output"):
        prepare.add_argument(name, type=Path)
    prepare.add_argument("--grid-books", required=True, type=int)
    prepare.add_argument("--half-turn", choices=(0, 180), required=True, type=int)
    prepare.add_argument("--reviewed-text-art-and-orientation", action="store_true", required=True)
    timed = commands.add_parser("measure")
    timed.add_argument("rule", type=Path)
    timed.add_argument("trace", type=Path)
    timed.add_argument("earliest_us", type=int)
    timed.add_argument("latest_us", type=int)
    timed.add_argument("--stock", required=True, type=Path)
    stock = commands.add_parser("check-stock")
    stock.add_argument("rule", type=Path)
    stock.add_argument("stock", type=Path)
    picture = commands.add_parser("compare-settled")
    for name in ("rule", "first", "final"):
        picture.add_argument(name, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "freeze":
            result = freeze(args.stock, args.first, args.second, args.grid_books, args.half_turn, args.output)
        elif args.action == "measure":
            result = measure(args.rule, args.trace, args.earliest_us, args.latest_us, args.stock)
        elif args.action == "check-stock":
            stock_target(args.rule, args.stock)
            result = {"ok": True, "visit_stock_distinguishes_every_region": True}
        else:
            result = compare_settled(args.rule, args.first, args.final)
    except ValueError as exc:
        result = {"ok": False, "error": str(exc)}
    except OSError:
        result = {"ok": False, "error": "private_input_or_output"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
