import unittest

from derivative_decision import STAGES, summarize


def visit(encode, reopen=4_000_000, sizes=(100,) * 6):
    values = dict.fromkeys(STAGES, 1_000_000)
    values['encode_ns'] = encode
    return {**values, 'total_ns': sum(values.values()), 'reopen_ns': reopen, 'png_bytes': list(sizes)}


def trial(candidate):
    def pair(order):
        return {'order': order, 'control': visit(6_000_000), 'no_filter': candidate.copy()}
    rows = {'control': [0, 0, 100, 2900, 0], 'no_filter': [3000, 0, 0, 0, 0]}
    return {'schema': 1, 'filesystem': 'xfs', 'image_count': 6, 'warmup': {**pair('AB'), 'filter_rows': rows},
            'pairs': [pair(order) for order in ['AB', 'BA'] * 5]}


class DerivativeDecisionTests(unittest.TestCase):
    def test_exact_thresholds_and_excluded_warmup(self):
        report = trial(visit(5_000_000, reopen=5_000_000, sizes=(105,) * 6))
        report['pairs'][3]['no_filter'] = visit(6_000_000, sizes=(105,) * 6)
        report['warmup']['no_filter'] = visit(9_000_000, sizes=(105,) * 6)
        result = summarize(report)
        self.assertTrue(result['passes'])
        self.assertEqual(result['wins'], 9)
        self.assertEqual(result['paired_cold_saving_percent'], 10.0)

    def test_cost_or_storage_guards_can_reject_a_faster_visit(self):
        for candidate, key in [(visit(5_000_001), 'cold_relative_saving'),
                               (visit(4_000_000, 5_000_001), 'reopen'),
                               (visit(4_000_000, sizes=(106,) * 6), 'shelf_bytes'),
                               (visit(4_000_000, sizes=(121, 95, 95, 95, 95, 95)), 'individual_bytes')]:
            with self.subTest(key=key):
                result = summarize(trial(candidate))
                self.assertFalse(result['passes'])
                self.assertFalse(result['guards'][key])

    def test_rejects_missing_pairs_and_inconsistent_stage_sums(self):
        report = trial(visit(5_000_000))
        report['pairs'][0]['control']['total_ns'] += 1
        with self.assertRaises(ValueError):
            summarize(report)
        report['pairs'][0]['control']['total_ns'] -= 1
        report['pairs'].pop()
        with self.assertRaises(ValueError):
            summarize(report)

    def test_requires_observed_filter_rows_from_both_arms(self):
        for arm, counts in [('control', [3000, 0, 0, 0, 0]), ('no_filter', [2999, 0, 1, 0, 0]),
                            ('no_filter', [2999, 0, 0, 0, 0])]:
            report = trial(visit(4_000_000))
            report['warmup']['filter_rows'][arm] = counts
            with self.subTest(arm=arm, counts=counts), self.assertRaises(ValueError):
                summarize(report)


if __name__ == '__main__':
    unittest.main()
