"""Fail-closed synthetic tests; these do not supply application performance data."""

import struct
import unittest

from measure import PERIOD, records
from report import classify


def fake(samples, extra=b''):
    body = b''.join(struct.pack('<IHHQIIQQQQ', 9, mode, 56, 0x123, 17, tid, time, PERIOD, 1, 0x123)
                    for time, tid, mode in samples) + extra
    return struct.pack('<9Q', 1, 17, 100, 110, 890, 900, PERIOD * len(samples), len(body), PERIOD) + body


class Records(unittest.TestCase):
    def test_filters_caller_handler_boundaries_and_preserves_unknown_kernel_skid(self):
        data = fake([(130,17,2), (200,17,2), (310,17,1), (500,17,2), (700,17,2), (840,17,2)])
        weights, stats = records(data, (160,820), 660)
        self.assertEqual((stats['samples'], stats['clipped'], weights[(0,)]), (4, 2, PERIOD))
        self.assertEqual(sum(stats['bins']), 4)

    def test_no_ip_spread_gate_filters_fast_frames_individually(self):
        data = fake([(200,17,2), (210,17,2), (211,17,2), (212,17,2)])
        _, stats = records(data, (160,820), 660)
        self.assertEqual(stats['bins'], [4, 0, 0, 0])
        self.assertLess(stats['sample_span'], .1)

    def test_rejects_unknown_thread_throttles_and_few_samples(self):
        for data in (fake([(200,18,2)]), fake([(200,17,2)], struct.pack('<IHH',5,0,8)), fake([(200,17,2)] * 3)):
            with self.assertRaises(ValueError): records(data, (160,820), 660)


class Categories(unittest.TestCase):
    def test_glyph_wrapper_does_not_claim_mask_generation(self):
        symbols = {'0x10': [('VectorFont::render_vector_glyph', '/dep/i-slint-renderer-software-1.17.1/fonts/vectorfont.rs:161')]}
        self.assertEqual(classify(['0x10'], symbols)[0], 'glyph_path_lookup')
        symbols['0x10'].insert(0, ('Swash::scale', '/dep/swash-0.2.8/src/scale/outline.rs:1'))
        self.assertEqual(classify(['0x10'], symbols)[0], 'glyph_mask_raster')

    def test_no_attribution_for_external_memset_without_concrete_ancestor(self):
        self.assertEqual(classify(['external'], {})[0], 'unresolved')
        symbols = {'0x20': [('Vec::clear', '/rust/library/alloc/src/vec.rs:11'),
                            ('Gray::blend_slice', '/work/source/vendor/slint-backend-kindle/src/gray_target.rs:70')]}
        self.assertEqual(classify(['external','0x20'], symbols)[0], 'pixel_composition')
        self.assertEqual(classify(['kernel_skid','0x20'], symbols)[0], 'unresolved')


if __name__ == '__main__':
    unittest.main()
