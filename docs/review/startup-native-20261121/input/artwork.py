import argparse
import gzip
import json
import re
import struct
from itertools import product
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


WIDTH, HEIGHT, STRIDE = 1860, 2480, 1872
BODY_TOP, BODY_BOTTOM = 150, 2330
DARK_LIMIT = 190
BOXES = ((78, 78, 522, 528), (143, 574, 450, 657), (198, 692, 402, 712))
RUN = re.compile(b"#+")
TABLE = bytes(35 if level < DARK_LIMIT else 32 for level in range(256))


def stock(path):
    if path.suffix == ".frame":
        with path.open("rb") as stream:
            header = stream.readline(120)
            raw = stream.read(WIDTH * HEIGHT + 1)
        if not re.fullmatch(rb"AFR2 [13] 18(60|72) (2480|4960) (0|2480) [0-9]+ [0-9]+\n", header) or len(raw) != WIDTH*HEIGHT:
            raise ValueError("unexpected packed capture")
        return Image.frombytes("L", (WIDTH, HEIGHT), raw)
    with gzip.open(path, "rb") as stream:
        raw = stream.read(HEIGHT * STRIDE + 1)
    if len(raw) != HEIGHT * STRIDE:
        raise ValueError("unexpected stock frame size")
    return Image.frombytes("L", (WIDTH, HEIGHT), raw, "raw", "L", STRIDE, 1)


def runs(data):
    return [match.span() for match in RUN.finditer(data)]


def proposals(picture):
    packed = picture.tobytes()
    mask = packed.translate(TABLE)
    seen = set()
    for column in range(8, WIDTH - 8, 8):
        stripes = runs(mask[BODY_TOP * WIDTH + column:BODY_BOTTOM * WIDTH:WIDTH])
        for index in range(len(stripes) - 3):
            bars = stripes[index:index+4]
            scale = (bars[3][0] - bars[0][0]) / 193
            if not .13 <= scale <= 1.65:
                continue
            if any(abs((end - begin) - wanted*scale) > max(2.2, 5*scale)
                   for (begin, end), wanted in zip(bars, (41, 43, 41, 41))):
                continue
            if any(abs((current[0] - previous[0]) - wanted*scale) > max(2.5, 6*scale)
                   for previous, current, wanted in zip(bars, bars[1:], (64, 65, 64))):
                continue
            probe_row = BODY_TOP + (bars[0][0] + bars[0][1]) // 2
            row = mask[probe_row * WIDTH:(probe_row + 1) * WIDTH]
            start = row.rfind(b" ", 0, column) + 1
            end = row.find(b" ", column)
            if end <= start or not .72 <= (end-start)/(122*scale) <= 1.35:
                continue
            key = (start, BODY_TOP+bars[0][0], BODY_TOP+bars[3][0], end)
            if key not in seen:
                seen.add(key)
                yield key


def fit(picture, official, guess):
    bar_left, bar_top, fourth_top, bar_right = guess
    scale = (fourth_top-bar_top)/193
    wide = (bar_right-bar_left)/122
    best = None
    for scale_offset in (-.008, 0, .008):
        tall = scale*(1+scale_offset)
        for wide_size,bias_x in product((wide*.99,wide,wide*1.01,scale),(-1,0,1)):
            for bias_y in (-1, 0, 1):
                scales=(wide_size,tall)
                origin = bar_left - 95*wide_size + bias_x, bar_top - 93*tall + bias_y
                area = tuple(round(origin[axis % 2] + value*scales[axis%2])
                             for axis, value in enumerate((0, 0, 600, 800)))
                if area[0] < 0 or area[1] < BODY_TOP or area[2] > WIDTH or area[3] > BODY_BOTTOM:
                    continue
                scores = []
                for pos, ref_box in enumerate(BOXES):
                    box = tuple(round(origin[axis%2]+value*scales[axis%2]) for axis,value in enumerate(ref_box))
                    dims = (60, 60) if pos == 0 else (60, 16) if pos == 1 else (48, 7)
                    reference = official.crop(ref_box).resize(dims, Image.Resampling.BOX)
                    candidate = picture.crop(box).resize(dims, Image.Resampling.BOX)
                    left = reference.point(lambda pixel: 255 if pixel < (210 if pos == 2 else 175) else 0)
                    right = candidate.point(lambda pixel: 255 if pixel < (210 if pos == 2 else 175) else 0)
                    intersection = ImageStat.Stat(ImageChops.darker(left, right)).sum[0]
                    union = ImageStat.Stat(ImageChops.lighter(left, right)).sum[0]
                    scores.append(intersection/max(1, union))
                    if scores[-1] < (.86, .73, .36)[pos]:
                        break
                if len(scores) != 3 or scores[-1] < .36:
                    continue
                rank = sum(scores)
                if best is None or rank > best[0]:
                    best = (rank, area, tuple(round(item,3) for item in scores))
    return best


def artwork(picture, official):
    if official.size != (600,800) or picture.size != (WIDTH,HEIGHT):
        raise ValueError("unsupported artwork or screen dimensions")
    found = []
    for item in proposals(picture):
        match = fit(picture, official, item)
        if match is None:
            continue
        _, area, _ = match
        old = next((idx for idx, (_,box,_) in enumerate(found)
                    if abs(area[0]-box[0]) < 8 and abs(area[1]-box[1]) < 8), None)
        if old is None:
            found.append(match)
        elif match[0] > found[old][0]:
            found[old] = match
    return sorted(found)


def write_guard(picture, box, path):
    left, top, right, bottom = box
    inset_x, inset_y = round((right-left)*.06), round((bottom-top)*.05)
    left, top, right, bottom = left+inset_x, top+inset_y, right-inset_x, bottom-inset_y
    box = (left, top, right, bottom)
    image = picture.crop(box)
    footer = picture.crop((0, 2380, WIDTH, 2470))
    with path.open("xb") as stream:
        stream.write(struct.pack("<8s6I", b"AIDOTAP1", left, top, right-left, bottom-top, 2380, 90))
        stream.write(image.tobytes())
        stream.write(footer.tobytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Locate only the official Aidoku Library artwork offline.")
    parser.add_argument("artwork", type=Path)
    parser.add_argument("stock", type=Path)
    parser.add_argument("--guard", type=Path)
    args = parser.parse_args()
    screen = stock(args.stock)
    official = Image.open(args.artwork).convert("L")
    hits = artwork(screen, official)
    print(json.dumps({"count": len(hits), "match": hits[0][1:] if len(hits) == 1 else None}), flush=True)
    if len(hits) != 1:
        raise SystemExit(2)
    if args.guard:
        write_guard(screen, hits[0][1], args.guard)
