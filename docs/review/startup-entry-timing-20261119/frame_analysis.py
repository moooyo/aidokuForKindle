"""Private AFW1 trace -> aggregate, device-clock framebuffer timing only."""

import argparse
import collections
import gzip
import io
import itertools
import json
import math
from pathlib import Path
import statistics
import struct
import sys
import tarfile


W, H, STEP, OFF = 1860, 2480, 16, 8
NX, NY = len(range(OFF, W, STEP)), len(range(OFF, H, STEP))
N = NX * NY
DENSE, LIGHT = (STEP, OFF), (32, 16)
REC = struct.Struct('<QQI')
FOOT = struct.Struct('<4Q')
P6_SIZE = W * H * 3
MAX_BLOB = 32 * 1024 * 1024
DWELL_US = 200000
# ui/theme.slint, app.slint, library.slint: native 2x Scribe Library sections.
DEFAULT = {'chrome': [(0, 72, W, 128), (0, 2320, W, 160)],
           'body': [(0, 72, W, H - 72)]}


class Invalid(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise Invalid(code)


def grid_shape(grid):
    need(type(grid) is tuple and grid in (DENSE, LIGHT) and
         all(type(x) is int for x in grid), 'unsupported_lattice')
    step, off = grid
    return len(range(off, W, step)), len(range(off, H, step))


def read_trace(data, grid=DENSE):
    nx, ny = grid_shape(grid)
    count_per_scan = nx * ny
    need(len(data) <= MAX_BLOB, 'trace_too_large')
    bio = io.BytesIO(data)
    header = bio.readline(512)
    need(header.endswith(b'\n'), 'header')
    try:
        cfg = json.loads(header)
    except (ValueError, UnicodeError) as err:
        raise Invalid('header_encoding') from err
    need(isinstance(cfg, dict) and cfg.get('format') == 'AFW1'
         and cfg.get('clock') == 'CLOCK_BOOTTIME_us', 'schema')
    dims = dict(width=W, height=H, bpp=8, step=grid[0], offset=grid[1], nx=nx, ny=ny)
    need(all(cfg.get(k) == v for k, v in dims.items()), 'geometry')
    need(set(cfg) == set(dims) | {'format', 'clock', 'stride', 'gray', 'rotation', 'xoffset',
        'yoffset', 'xvirtual', 'yvirtual', 'duration_ms', 'period_ms'}, 'schema_keys')
    need(all(type(v) is int and v >= 0 for k, v in cfg.items() if k not in ('format', 'clock')), 'header_values')
    need(250 <= cfg['duration_ms'] <= 30000 and 20 <= cfg['period_ms'] <= 100 and
         0 <= cfg['rotation'] <= 3 and cfg['width'] + cfg['xoffset'] <= min(cfg['stride'], cfg['xvirtual'])
         and cfg['height'] + cfg['yoffset'] <= cfg['yvirtual'], 'layout')
    samples, prev, last, changed = [], 0, None, 0
    while True:
        row = bio.read(REC.size)
        need(len(row) == REC.size, 'missing_record_or_trailer')
        lo, hi, length = REC.unpack(row)
        if length == 0xffffffff:
            tail = bio.read(FOOT.size)
            need(len(tail) == FOOT.size and bio.read(1) == b'', 'bad_trailer')
            count, distinct, missed, cpu = FOOT.unpack(tail)
            need(count == len(samples) and count >= 2 and distinct == changed, 'trailer_counts')
            need(lo <= samples[0][0] and hi >= samples[-1][1] and hi > lo, 'trailer_clock')
            need(count + missed == math.ceil(cfg['duration_ms'] / cfg['period_ms']), 'trailer_cadence')
            need(cpu <= hi - lo + 10000, 'bad_cpu_clock')
            return cfg, samples, (lo, hi, missed, cpu)
        need(prev <= lo <= hi and length in (0, count_per_scan) and len(samples) < 1500, 'sample_shape_or_clock')
        if length:
            payload = bio.read(count_per_scan)
            need(len(payload) == count_per_scan and payload != last, 'invalid_changed_payload')
            last, changed = payload, changed + 1
        need(last is not None, 'duplicate_before_first_frame')
        samples.append((lo, hi, last))
        prev = hi


def reference_grid(path, rotation, grid=DENSE):
    """Use the release sampler's natural framebuffer orientation and raw Y8 values."""
    grid_shape(grid)
    step, off = grid
    need(path.is_file() and path.stat().st_size <= 64 * 1024 * 1024, 'reference_file')
    with path.open('rb') as f:
        sig = f.read(2)
        f.seek(0)
        if sig == b'P6':
            raw = f.read(P6_SIZE + 513)
        elif sig == b'\x1f\x8b':
            with gzip.GzipFile(fileobj=f) as gz:
                raw = gz.read(P6_SIZE + 513)
        else:
            with tarfile.open(fileobj=f, mode='r:') as tar:
                candidates = [m for m in tar if m.name == 'after.native.ppm.gz' and m.isreg()]
                need(len(candidates) == 1 and 0 < candidates[0].size <= P6_SIZE + 65536, 'reference_tar_member')
                with gzip.GzipFile(fileobj=tar.extractfile(candidates[0])) as gz:
                    raw = gz.read(P6_SIZE + 513)
    prefix = f'P6\n# bpp=8 rotation={rotation}\n{W} {H}\n255\n'.encode('ascii')
    need(raw.startswith(prefix) and len(raw) == len(prefix) + P6_SIZE, 'reference_format_or_orientation')
    pixels = memoryview(raw)[len(prefix):]
    pos = (3 * (y * W + x) for y in range(off, H, step) for x in range(off, W, step))
    result = bytearray()
    for i in pos:
        need(pixels[i] == pixels[i+1] == pixels[i+2], 'reference_not_gray')
        result.append(pixels[i])
    return bytes(result)


def rows(rects, grid=DENSE):
    nx, ny = grid_shape(grid)
    step, off = grid
    result, occupied = [], set()
    for x, y, w, h in rects:
        need(0 <= x < x+w <= W and 72 <= y < y+h <= H, 'roi_bounds_or_status_bar')
        xs = [i for i in range(nx) if x <= off + i * step < x+w]
        ys = [i for i in range(ny) if y <= off + i * step < y+h]
        need(len(xs)*len(ys) >= 16, 'roi_too_small')
        idx = [(j * nx + xs[0], j * nx + xs[-1] + 1) for j in ys]
        cells = {i for a, b in idx for i in range(a, b)}
        need(not (occupied & cells), 'overlapping_roi_parts')
        occupied |= cells
        result.append(idx)
    need(result, 'missing_roi')
    return result


def project(frame, group):
    return b''.join(frame[a:b] for a, b in group)


def read_rois(values):
    rois = {k: list(v) for k, v in DEFAULT.items()}
    seen = set()
    for s in values:
        kind, sep, spec = s.partition(':')
        need(kind in DEFAULT and sep, 'roi_group')
        try:
            coords = tuple(map(int, spec.split(',')))
        except ValueError as err:
            raise Invalid('roi_syntax') from err
        need(len(coords) == 4, 'roi_syntax')
        if kind not in seen:
            rois[kind] = []
        rois[kind].append(coords)
        seen.add(kind)
    return rois


def mark(samples, group, baseline, trigger_low, trigger_high, cap, which):
    target = [project(baseline, g) for g in group]
    before_idx = [i for i, (_, end, _) in enumerate(samples) if end < trigger_low]
    need(before_idx and samples[-1][0] > trigger_high, 'trigger_outside_or_no_pretrigger_sample')
    cache = {}
    for lo, hi, frame in samples:
        key = id(frame)
        if key not in cache:
            cache[key] = all(project(frame, g) == t for g, t in zip(group, target))
    exact = [cache[id(frame)] for _, _, frame in samples]
    need(not any(exact[i] for i in before_idx), 'pretrigger_already_matches')
    # A white loading canvas or visually featureless empty target is not a Library marker.
    pre = samples[before_idx[-1]][2]
    for t, g in zip(target, group):
        common = collections.Counter(t).most_common(1)[0][0]
        minimum = max(3, math.ceil(len(t)*0.003))
        need(sum(abs(p-common)>=16 for p in t) >= minimum, 'low_contrast_final_region')
        need(sum(abs(a-b)>=16 for a,b in zip(project(pre,g),t)) >= minimum, 'ambiguous_pretrigger_region')
    need(exact[-1], 'last_frame_not_reference')
    sustained, start = [], None
    for i, match in enumerate(exact):
        if match and (start is None or samples[i][0] - samples[i-1][1] > cap):
            # A gap is not proof of persistence; do not silently count time not observed.
            if start is not None and samples[i-1][0] - samples[start][1] >= DWELL_US:
                sustained.append((start, i-1))
            start = i
        if start is not None and (not match or i == len(exact)-1):
            last = i if match else i-1
            if samples[last][0] - samples[start][1] >= DWELL_US:
                sustained.append((start,last))
            start = None
    need(sustained and sustained[-1][1] == len(samples)-1, 'no_sustained_final_target')
    first = sustained[0 if which == 'chrome' else -1][0]
    need(first > 0 and not exact[first-1], 'transition_hidden_by_sampling_gap')
    need(samples[first][1] >= trigger_high, 'matched_before_trigger')
    lower = max(0, samples[first-1][0]-trigger_high)
    upper = samples[first][1]-trigger_low
    need(0 <= lower <= upper, 'bad_latency_bracket')
    return {'lower_ms':round(lower/1000,3), 'upper_ms':round(upper/1000,3),
            'bracket_ms':round((upper-lower)/1000,3), 'wide_edge':upper-lower > cap,
            'retained_sustained_match_runs':len(sustained),
            'reference_sample_points':sum(map(len,target))}


def analyze(data, trigger_low, trigger_high, rois=None, ref=None, grid=DENSE):
    cfg, samples, (start, end, missed, cpu) = read_trace(data, grid)
    need(0 < trigger_low <= trigger_high < end, 'invalid_trigger_window')
    need(samples[0][1] < trigger_low and samples[-1][0] > trigger_high, 'insufficient_trigger_coverage')
    baseline = reference_grid(ref, cfg['rotation'], grid) if ref else samples[-1][2]
    groups = {k:rows(v, grid) for k,v in (rois or DEFAULT).items()}
    cap = max(100000, 3 * cfg['period_ms'] * 1000)
    gaps = [b[0]-a[1] for a,b in itertools.pairwise(samples)]
    durations = [b-a for a,b,_ in samples]
    cpu_pct = round(100 * cpu / (end-start), 3)
    result = {'schema':'AFW1-analysis-v1', 'scope':'sampled_framebuffer_writes_only',
              'anchor_interval_width_ms':round((trigger_high-trigger_low)/1000,3),
              'reference':'independent_p6' if ref else 'operator_attested_final_frame',
              'requires_dwell_ms':DWELL_US//1000, 'endpoints':{},
              'sampling':{'period_ms':cfg['period_ms'], 'samples':len(samples), 'missed_slots':missed,
                          'process_cpu_pct':cpu_pct, 'scan_median_ms':round(statistics.median(durations)/1000,3),
                          'scan_max_ms':round(max(durations)/1000,3), 'gap_max_ms':round(max(gaps)/1000,3),
                          'large_gap':max(gaps)>cap, 'observer_cpu_over_10pct':cpu_pct>10},
              'endpoint_definitions':{'chrome':'first sustained exact match of Library category bar and navigation, or specified chrome regions',
                                      'body':'start of terminal sustained exact match of complete sampled Library body, or specified body regions'}}
    for key in ('chrome','body'):
        try:
            result['endpoints'][key] = mark(samples, groups[key], baseline, trigger_low, trigger_high, cap, key)
        except Invalid as err:
            result['endpoints'][key] = {'invalid':str(err)}
    result['ok'] = all('invalid' not in e for e in result['endpoints'].values())
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('trace',type=Path)
    ap.add_argument('--trigger-us',type=int,required=True,help='earliest same-device CLOCK_BOOTTIME trigger')
    ap.add_argument('--trigger-end-us',type=int,help='latest trigger, if time is bracketed')
    proof = ap.add_mutually_exclusive_group(required=True)
    proof.add_argument('--reference',type=Path,help='independently verified installed Library P6, gzipped P6 or private install tar')
    proof.add_argument('--attest-final-library',action='store_true',help='operator has independently verified the terminal content is the Library')
    ap.add_argument('--grid', choices=('dense', 'light'), default='dense',
                    help='require the predeclared dense (16,8) or light (32,16) lattice')
    ap.add_argument('--roi',action='append',default=[],help='repeat chrome:X,Y,W,H or body:X,Y,W,H; multiple rectangles in a group are conjunctive')
    a = ap.parse_args(argv)
    try:
        need(a.trace.is_file() and a.trace.stat().st_size <= MAX_BLOB, 'trace_file')
        result = analyze(a.trace.read_bytes(),a.trigger_us,a.trigger_end_us if a.trigger_end_us is not None else a.trigger_us,
                         read_rois(a.roi),a.reference,DENSE if a.grid == 'dense' else LIGHT)
    except Invalid as err:
        result = {'ok':False,'error':str(err)}
    except (OSError, EOFError, tarfile.TarError):
        result = {'ok':False,'error':'io_or_archive_error'}
    print(json.dumps(result,sort_keys=True,indent=2))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
