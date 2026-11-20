"""Synthetic AFW1 fixtures, including an independently located P6 pixel grid."""

import gzip
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_analysis as f


A, P, SPAN = 1000000, 25000, 50
DENSE, LIGHT = (16, 8), (32, 16)


def axes(grid):
    step, off = grid
    return range(off, 1860, step), range(off, 2480, step)


def sample_frame(kind, grid=DENSE):
    xs, ys = axes(grid)
    raw = bytearray([255]) * (len(xs) * len(ys))
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            at = j * len(xs) + i
            if y < 72:
                raw[at] = 0
            elif kind in ('chrome', 'final') and ((72 <= y < 200 or 2320 <= y) and i % 7 == 0):
                raw[at] = 22
            elif kind == 'final' and 400 <= y < 1900 and i % 3 == 0:
                raw[at] = 120
            elif kind == 'stock':
                raw[at] = 88 + (i % 3)
    return bytes(raw)


def encode(images, times=None, grid=DENSE):
    xs, ys = axes(grid)
    count = len(images)
    times = times or [A + i * P for i in range(count)]
    slots = (times[-1] - A) // P + 1
    hdr = dict(format='AFW1', clock='CLOCK_BOOTTIME_us', width=1860, height=2480, stride=1860, bpp=8, gray=1,
               rotation=3, xoffset=0, yoffset=0, xvirtual=1860, yvirtual=2480, step=grid[0], offset=grid[1],
               nx=len(xs), ny=len(ys), duration_ms=slots*(P//1000), period_ms=P//1000)
    raw = bytearray(json.dumps(hdr).encode() + b'\n')
    last, changed = None, 0
    for image, stamp in zip(images, times):
        delta = last != image
        raw.extend(struct.pack('<QQI', stamp, stamp + SPAN, len(image) if delta else 0))
        if delta:
            raw.extend(image)
            changed += 1
        last = image
    raw.extend(struct.pack('<QQI4Q', A,times[-1]+100,0xffffffff,count,changed,slots-count,1500))
    return bytes(raw)


def fixture(grid=DENSE):
    s, b, c, z = (sample_frame(k, grid) for k in ('stock', 'blank', 'chrome', 'final'))
    return [s]*6 + [b]*4 + [c]*10 + [z]*15


def archive(root, frame, grid=DENSE):
    xs, ys = axes(grid)
    raw = bytearray(f.P6_SIZE)
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            pos = 3 * (y * 1860 + x)
            raw[pos:pos+3] = bytes([frame[j*len(xs) + i]]) * 3
    prefix = b'P6\n# bpp=8 rotation=3\n1860 2480\n255\n'
    gz = gzip.compress(prefix+raw, compresslevel=1)
    p = root/'private-install-receipt.stdout'
    with tarfile.open(p, 'w:') as tar:
        info = tarfile.TarInfo('after.native.ppm.gz')
        info.size = len(gz)
        tar.addfile(info, io.BytesIO(gz))
    return p


class Analyzer(unittest.TestCase):
    def test_exact_conjunctive_chrome_and_full_body_bounds(self):
        res = f.analyze(encode(fixture()), A+5*P, A+5*P+10000)
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['endpoints']['chrome']['lower_ms'], 90.0)
        self.assertEqual(res['endpoints']['chrome']['upper_ms'], 125.05)
        self.assertEqual(res['endpoints']['body']['lower_ms'], 340.0)
        self.assertEqual(res['endpoints']['body']['upper_ms'], 375.05)
        self.assertEqual(res['sampling']['missed_slots'], 0)
        self.assertEqual(res['sampling']['scan_median_ms'], 0.05)

    def test_noise_in_time_header_does_not_change_endpoint(self):
        frames = fixture()
        for i, v in enumerate(frames):
            b = bytearray(v)
            b[:f.NX] = bytes([i]) * f.NX
            frames[i] = bytes(b)
        self.assertTrue(f.analyze(encode(frames), A+5*P, A+5*P)['ok'])

    def test_reject_flat_splash_or_identical_pretrigger(self):
        s, z = map(sample_frame, ('stock','final'))
        flat = f.analyze(encode([s]*6 + [bytes([250])*f.N]*25), A+5*P, A+5*P)
        self.assertEqual(flat['endpoints']['chrome']['invalid'], 'low_contrast_final_region')
        old = f.analyze(encode([z]*3 + [s]*4 + [z]*25), A+5*P, A+5*P)
        self.assertEqual(old['endpoints']['body']['invalid'], 'pretrigger_already_matches')

    def test_reject_nonmatching_independent_library_or_rotation(self):
        frames = fixture()
        with tempfile.TemporaryDirectory() as d:
            p = archive(Path(d), frames[-1])
            self.assertEqual(f.reference_grid(p,3), frames[-1])
            with self.assertRaisesRegex(f.Invalid, 'orientation'):
                f.reference_grid(p,0)
            self.assertTrue(f.analyze(encode(frames), A+5*P, A+5*P, ref=p)['ok'])
            frames[-1] = sample_frame('chrome')
            r = f.analyze(encode(frames), A+5*P, A+5*P, ref=p)
            self.assertEqual(r['endpoints']['body']['invalid'], 'last_frame_not_reference')

    def test_reject_terminal_flash(self):
        frames = fixture()[:24]
        self.assertEqual(f.analyze(encode(frames), A+5*P, A+5*P)['endpoints']['body']['invalid'],
                         'no_sustained_final_target')

    def test_detect_deliberate_observer_gap(self):
        frames = fixture()
        times = [A + i*P + (250000 if i>=10 else 0) for i in range(len(frames))]
        res = f.analyze(encode(frames,times), A+5*P, A+5*P)
        self.assertTrue(res['sampling']['large_gap'])
        self.assertEqual(res['sampling']['missed_slots'], 10)
        self.assertTrue(res['endpoints']['chrome']['wide_edge'])

    def test_roi_guards(self):
        for rs in [['chrome:0,71,1860,100'], ['chrome:1,72,1,1'], ['chrome:0,72,1860,600','chrome:0,72,30,600']]:
            with self.subTest(rs=rs), self.assertRaises(f.Invalid):
                f.rows(f.read_rois(rs)['chrome'])

    def test_trailer_and_clock_rejected(self):
        valid = encode(fixture())
        self.assertRaises(f.Invalid, f.read_trace, valid[:-2])
        self.assertRaises(f.Invalid, f.read_trace, valid+b'x')
        p = valid.index(b'\n') + 1
        changed = bytearray(valid)
        struct.pack_into('<Q', changed, p, 10**18)
        self.assertRaisesRegex(f.Invalid, 'clock', f.read_trace, changed)

    def test_light_reference_rows_and_timing_use_the_same_physical_positions(self):
        frames = fixture(LIGHT)
        data = encode(frames, grid=LIGHT)
        self.assertEqual(f.grid_shape(LIGHT), (58,77))
        cfg, observed, _ = f.read_trace(data, grid=LIGHT)
        self.assertEqual(cfg['nx'] * cfg['ny'], 4466)
        self.assertEqual(observed[-1][2], frames[-1])
        # [0,1860) x [72,200) contains four native 32px sample rows at 80,112,144,176.
        region = f.rows([(0,72,1860,128)], LIGHT)[0]
        self.assertEqual(region, [(i*58,(i+1)*58) for i in range(2,6)])
        with tempfile.TemporaryDirectory() as d:
            p = archive(Path(d), frames[-1], LIGHT)
            self.assertEqual(f.reference_grid(p,3,LIGHT), frames[-1])
            res = f.analyze(data, A+5*P, A+5*P, ref=p, grid=LIGHT)
            self.assertTrue(res['ok'], res)
            self.assertEqual(res['endpoints']['body']['reference_sample_points'], 75*58)
            self.assertEqual(res['endpoints']['chrome']['upper_ms'], 125.05)
            self.assertEqual(res['endpoints']['body']['upper_ms'], 375.05)

    def test_light_dense_mismatch_and_unreviewed_grid_are_rejected(self):
        dense = encode(fixture())
        light = encode(fixture(LIGHT), grid=LIGHT)
        for data, grid in ((dense,LIGHT),(light,DENSE),(light,(48,24)),(light,(32,8)),(light,(32.0,16.0))):
            with self.subTest(grid=grid), self.assertRaises(f.Invalid):
                f.read_trace(data, grid)
        with self.assertRaisesRegex(f.Invalid,'geometry'):
            f.read_trace(light)
        with self.assertRaisesRegex(f.Invalid,'sample_shape'):
            f.read_trace(encode(fixture(), grid=LIGHT), LIGHT)  # False header on dense payload.


if __name__ == '__main__':
    unittest.main()
