"""Check physical toolbar evidence using generated Scribe P6 frames on test-env."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


def load(name):
    location = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('toolbar_fixture_' + name, location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HEADER = b'P6\n# bpp=8 rotation=3\n1860 2480\n255\n'
SENTINELS = {(0, 0): b'\x08\x15\xfb', (929, 0): b'\xc7\xa7\x0f',
             (0, 71): b'\x0c\xf3\x17', (929, 71): b'\xcb\x19\xe1'}
FILL = b'\x38\x40\x48'


def capture(case, fill=FILL, marks=None):
    metadata = {'profile': 'scribe', 'backend': 'kindle', 'route': 'library', 'landscape': False,
                'rotation_degrees': 0, 'window_size': [1860, 2480]}
    (case / 'final.metadata.json').write_text(json.dumps(metadata))
    with (case / 'native-settled.ppm').open('wb') as frame:
        frame.write(HEADER)
        for y in range(72):
            row = bytearray(fill * 1860)
            for (x, my), color in (marks or {}).items():
                if my == y:
                    i = (930 + x) * 3
                    row[i:i + 3] = color
            frame.write(row)
        frame.write(b'\x00' * (2408 * 1860 * 3))


def reference_tuple():
    crop = bytearray(FILL * (930 * 72))
    for (x, y), color in SENTINELS.items():
        offset = (y * 930 + x) * 3
        crop[offset:offset + 3] = color
    stored = hashlib.sha256(crop.hex().encode('ascii')).hexdigest().encode('ascii')
    return (8, 203, 21, 243, 15, 251, stored)


def receipt(expected):
    *extrema, ident = expected
    return b'\t'.join([b'930', b'0', b'930', b'72', b'200880',
                      *(str(x).encode('ascii') for x in extrema), ident]) + b'\n'


@unittest.skipUnless(sys.platform == 'linux', 'Run toolbar qualification on test-env')
class ToolbarLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = load('analyze_lifecycle')
        cls.case_fixtures = load('test_lifecycle')

    def setUp(self):
        temporary = TemporaryDirectory(prefix='toolbar-qualification-')
        self.addCleanup(temporary.cleanup)
        self.case = Path(temporary.name) / 'candidate-qualify'
        self.case.mkdir()
        self.expected = reference_tuple()

    def test_exact_rgb_region_ignores_clock_and_first_content_row(self):
        capture(self.case, marks=SENTINELS)
        self.assertEqual(self.audit.cropped_toolbar_reference(self.case), self.expected)
        with (self.case / 'native-settled.ppm').open('r+b') as image:
            for x, y in ((0, 0), (929, 71), (930, 72), (1859, 72), (1859, 2479)):
                image.seek(len(HEADER) + 3 * (1860 * y + x)); image.write(b'\xff\xff\xff')
        self.assertEqual(self.audit.cropped_toolbar_reference(self.case), self.expected)
        with (self.case / 'native-settled.ppm').open('r+b') as image:
            image.seek(len(HEADER) + 3 * (1860 * 30 + 1100)); image.write(b'\x38\x41\x48')
        changed = self.audit.cropped_toolbar_reference(self.case)
        self.assertEqual(changed[:-1], self.expected[:-1]); self.assertNotEqual(changed[-1], self.expected[-1])

    def test_blank_or_wrong_geometry_cannot_be_a_reference(self):
        # A uniformly colored crop still has no observable spatial variation.
        for uniform in (b'\xff\xff\xff', b'\x00\x00\x00', b'\x01\x80\xff'):
            capture(self.case, fill=uniform)
            with self.subTest(uniform=uniform), self.assertRaises(ValueError):
                self.audit.cropped_toolbar_reference(self.case)
        capture(self.case, marks=SENTINELS)
        metadata = self.case / 'final.metadata.json'
        m = json.loads(metadata.read_text()); m['rotation_degrees'] = 90; metadata.write_text(json.dumps(m))
        with self.assertRaises(ValueError): self.audit.cropped_toolbar_reference(self.case)
        capture(self.case, marks=SENTINELS)
        with (self.case / 'native-settled.ppm').open('r+b') as image:
            image.write(HEADER.replace(b'1860 2480', b'2480 1860'))
        with self.assertRaises(ValueError): self.audit.cropped_toolbar_reference(self.case)

    def test_record_geometry_extrema_and_digest_are_strict(self):
        path = self.case / 'receipt.tsv'; valid = receipt(self.expected); path.write_bytes(valid)
        self.assertEqual(self.audit.observed_toolbar(path), self.expected)
        invalid = [valid + b'\n', valid.rstrip(b'\n') + b'\tmore\n',
                   receipt((4,4,50,50,240,240,self.expected[-1]))]
        for pos, value in ((0,b'929'), (1,b'1'), (2,b'931'), (3,b'71'), (4,b'200879'), (5,b'204'),
                          (6,b'256'), (7,b'-1'), (11,b'A' * 64), (11,b'z' * 64), (11,b'a' * 63)):
            fields = valid[:-1].split(b'\t'); fields[pos] = value; invalid.append(b'\t'.join(fields) + b'\n')
        for row in invalid:
            with self.subTest(record=row), self.assertRaises(ValueError):
                path.write_bytes(row); self.audit.observed_toolbar(path)

    def test_status_qualification_compares_both_default_visits_and_wakes_to_independent_reference(self):
        fixture = self.case_fixtures.CaseEvidenceTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        def verify(toolbar):
            return self.audit.case_evidence(fixture.directory, fixture.run, fixture.side,
                                            fixture.expected, fixture.regime, check_toolbar=toolbar)
        verify(False)
        with self.assertRaises((ValueError, FileNotFoundError)): verify(True)
        for name in ('library.right.tsv','wake.right.tsv'):
            (fixture.directory/name).write_bytes(receipt(self.expected))
        candidate = verify(True)
        baseline = copy.deepcopy(candidate)
        baseline['access_after'] = candidate['access_before'] - 1; baseline['access_before'] = baseline['access_after'] - 1
        frames = {candidate['frame']}
        self.assertEqual((candidate['toolbar'], candidate['wake_toolbar']), (self.expected,) * 2)
        self.audit.pair_evidence(baseline, candidate, frames, qualification_toolbars={self.expected})
        changed = (*self.expected[:-1], b'f'*64)
        for side in ('baseline','candidate'):
            for key in ('toolbar','wake_toolbar'):
                for missing in (False, True):
                    pair = {'baseline': baseline.copy(), 'candidate': candidate.copy()}
                    if missing: del pair[side][key]
                    else: pair[side][key] = changed
                    with self.subTest(side=side, key=key, missing=missing), self.assertRaises(ValueError):
                        self.audit.pair_evidence(pair['baseline'], pair['candidate'], frames, qualification_toolbars={self.expected})
        for refs in (set(), {changed}, {changed, self.expected}):
            with self.subTest(reference_count=len(refs)), self.assertRaises(ValueError):
                self.audit.pair_evidence(baseline, candidate, frames, qualification_toolbars=refs)


if __name__ == '__main__':
    unittest.main()
