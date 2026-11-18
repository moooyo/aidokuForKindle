"""Exercise the status gates, including the recorded rejection of the earlier cold-only build."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('status_gate_fixture', HERE / 'prepare.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
APP = 'aidoku/app/aidoku'
DEPLOYED = '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
STATUS = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
OLD = 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6'
LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
WARM = 'f796f2c20c75f183996d63612c19dad7a4a8aa47e7ce0f1d0995124509bdc8c7'
COLD = '2d9a5d376691af829f85a936d428d9f498ae1b63b3d5423229374f5a7e87a839'
PIXELS = '6958232ab783a32ceb822c7ba13d083ec2470bb4bc13e3af675e352e0c703a20'
COMBINED = 'cold_decode_with_fresh_toolbar_status'
TOOLBAR = 'consume_fresh_toolbar_status_in_first_library_view'
DECODE = 'cold_local_cover_decode_validation'
SIDES = ('baseline', 'candidate')


def sha(content):
    return hashlib.sha256(content).hexdigest()


def identity(binary, **overrides):
    spec = {'application_sha256': binary, 'native_query_sha256': QUERY, 'launcher_sha256': LAUNCH,
            'extra_sha256': {}, 'env': {}, 'args': []}
    spec.update(overrides)
    digest = sha(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode())
    return {'variant_sha256': digest, **{k: v for k, v in spec.items() if k not in ('env', 'args')}}


def stats(values):
    return {'n': len(values), 'median': statistics.median(values), 'min': min(values), 'max': max(values)} if values else None


def times(usable, covers):
    return {'ready': 1350, 'first': usable - 2, 'input_ready': usable, 'usable': usable, 'covers': covers, 'exit': 2480}


def analyzer_hash():
    for path in (HERE.parent / 'status-bench/analyze_status.py', Path('/root/aidoku-next-statusbench-20261118/tools/analyze_status.py')):
        if path.is_file():
            return sha(path.read_bytes())
    raise ValueError('The status analyzer fingerprint is unavailable on the test host')


def fixture(workload, phase, usable=None, covers=None, control=DEPLOYED, treatment=STATUS, extra=None):
    cold = workload == 'cold'
    pixel = phase == 'qualify'
    n = 0 if pixel else 6 if phase == 'screen' else 10
    usable = [8] * n if usable is None else usable
    covers = [140] * n if covers is None else covers
    if len(usable) != n or len(covers) != n:
        raise ValueError('Wrong synthetic fixture length')
    run = f'status-{workload}-{phase}-fixture'
    ids = {'baseline': identity(control), 'candidate': identity(treatment, extra_sha256={} if extra is None else extra)}
    samples, cases = [], []
    for i, (first_gain, cover_gain) in enumerate(zip(usable, covers), 1):
        before = times(2600 + 2 * i, (8800 if cold else 3100) + 2 * i)
        after = times(before['usable'] - first_gain, before['covers'] - cover_gain)
        samples.append({'batch': run, 'round': i, 'order': 'AB' if i % 2 else 'BA', 'complete': True,
                        'baseline_ms': before, 'candidate_ms': after, 'savings_ms': {k: before[k] - after[k] for k in before}})
    labels = ['warmup'] + (['qualify'] if pixel else [f'{i:02}' for i in range(1, n + 1)])
    for label in labels:
        for side in SIDES:
            hit = side == 'candidate'
            cases.append({'case': side + '-' + label, 'status': 'ok', 'issues': [], 'runner_gate_verified': True,
                          'operating_evidence_verified': True,
                          'diagnostics': {'cover_slots': 7, 'unique_visible_items': 6, 'reported_cover_jobs': 6,
                                          'reported_local_cache_hits': 0 if cold else 6, 'reported_local_cache_misses': 6 if cold else 0},
                          'toolbar': {'classification': 'hit' if hit else 'baseline_sync', 'attempts': int(hit),
                                      'used': int(hit), 'ready': int(hit), 'fallbacks': 0, 'unavailable': 0,
                                      'sync_absent_on_hit': hit, 'first_view_query_responses': 0 if hit else 5,
                                      'first_view_query_sessions': 0 if hit else 1, 'first_view_query_finishes': 0 if hit else 1,
                                      'native_queries_after_first_before_covers': 0, 'native_queries_after_view_before_first': 0,
                                      'no_query_deferral_to_cover_tail': True, 'probe_query_sessions': int(hit),
                                      'probe_query_responses': 2 * int(hit), 'probe_query_finishes': int(hit),
                                      'first_view_ms': 40 if hit else 66, 'initial_send_to_input_ms': 370,
                                      'probe_wall_ms': 26 if hit else None, 'initial_send_to_probe_ms': 1 if hit else None,
                                      'probe_finish_to_initial_input_ms': 340 if hit else None},
                          **({'cold': {'combined_successes': 6 if hit else 0, 'explicit_fallbacks': 0,
                                       'fixture_verified': True, 'trace_verified': True}} if cold else {})})
    toolbar = {'name': TOOLBAR, 'verified': True}
    decode = {'name': DECODE, 'verified': cold}
    for group, count in (('timing', n), ('pixel', int(pixel)), ('warmup', 1)):
        toolbar[group] = {side: {'observations': count, 'hits': count if side == 'candidate' else 0,
                                 'attempted': count if side == 'candidate' else 0, 'fallbacks': 0, 'unverified': 0,
                                 'baseline_sync': count if side == 'baseline' else 0, 'view_queries_on_hits': 0,
                                 'deferred_query_events_on_hits': 0} for side in SIDES}
        if cold:
            decode[group] = {side: {'observations': count, 'exercised_runs': count if side == 'candidate' else 0,
                                    'explicit_fallbacks': 0, 'combined_successes': 6 * count if side == 'candidate' else 0} for side in SIDES}
    if not cold:
        decode['applicable'] = False
    features = {'workload': workload, 'cache_regime': 'empty-cache-v1' if cold else 'warm-local-derivatives-v1',
                'fixture_policy': 'per-case-empty-cache-v1' if cold else 'six-local-covers-warm-v1',
                'mechanism': {'name': COMBINED, 'workload': workload, 'verified': True, 'toolbar_verified': True,
                              'cold_decode_verified': cold}, 'status_mechanism': toolbar, 'cold_mechanism': decode}
    batch = {'run_id': run, 'mode': phase, 'issues': [], 'pairs': copy.deepcopy(samples),
             'artifacts': copy.deepcopy(ids), 'cases': cases, **copy.deepcopy(features)}
    report = {'phase': phase, 'valid': True, 'issues': [], 'input_failures': [], 'primary_endpoint': 'covers',
              'primary_metric': 'covers', 'artifacts': ids, 'pairs': samples, 'batches': [batch], 'complete_pairs': n,
              'required_pairs': None if pixel else n, 'planned_pairs': n, 'pair_orders': {} if pixel else {'AB': n // 2, 'BA': n // 2},
              'analyzer_sha256': analyzer_hash(), 'warm_analyzer_sha256': WARM, 'right_status_bar_analyzer_sha256': PIXELS,
              'expected_candidate_application_sha256': treatment, 'qualification_passed': pixel, 'pixels': [],
              'latency_gate_passed': None if pixel or not cold else True,
              'warm_guard_passed': True if not cold and not pixel else None,
              'comparisons_ms': {k: {'paired_savings_ms': stats([p['savings_ms'][k] for p in samples])}
                                 for k in ('usable', 'covers', 'first', 'input_ready', 'ready', 'exit')}, **features}
    if cold:
        report.update(cold_analyzer_sha256=COLD, cold_fixture_match=True, cold_fixture_verified=True)
    else:
        report['warm_guard'] = {'required_pairs': 6, 'minimum_paired_median_savings_ms': {'usable': -50, 'covers': -50}}
    if pixel:
        report['pixels'] = [{'batch': run, 'height': 2480, 'width': 1860, 'status_bar_excluded_pixels': 72,
                             'identical_below_status_bar': True, 'right_status_bar_observable': True,
                             'identical_right_status_bar': True,
                             'right_status_bar_region': {'x': 930, 'y': 0, 'width': 930, 'height': 72}}]
    return report, {APP: treatment}


@unittest.skipUnless(sys.platform == 'linux', 'Run release tests on test-env')
class StatusGateTests(unittest.TestCase):
    def test_confirmed_cold_win_does_not_ship_the_rejected_warm_regression(self):
        reports = Path('/root/aidoku-next-coldbench-20261118/reports')
        cold = json.loads((reports / 'cold-confirm.json').read_bytes())
        warm = json.loads(Path('/root/aidoku-next-bench-20261118/reports/cold-warm-guard.json').read_bytes())
        for result in (cold, warm):
            self.assertEqual(result['artifacts']['candidate']['application_sha256'], OLD)
            self.assertTrue(result['valid'])
        self.assertEqual(len(warm['pairs']), 6)
        self.assertEqual(len(cold['pairs']), 10)
        self.assertAlmostEqual(statistics.median(r['savings_ms']['usable'] for r in warm['pairs']), -67.421, places=3)
        self.assertAlmostEqual(statistics.median(r['savings_ms']['covers'] for r in cold['pairs']), 2450.220, places=3)
        self.assertTrue(gate.freeze_gate(cold, 'confirm', 'cold', {APP: OLD}))
        artifact = Path('/root/aidoku-next-cold-20261118/artifacts/candidate-arm')
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            for name in ('cold', 'cover', 'status'):
                selection = {'schema': 1, 'candidates': {name: {}}, 'artifacts': {APP: {'uri': artifact.as_uri(), 'sha256': OLD}}}
                with self.subTest(candidate=name), self.assertRaisesRegex(ValueError, 'failed the warm guard'):
                    gate.build_staged(selection, root, root, sha(b'not materialized'))
            self.assertEqual(list(root.iterdir()), [])

    def test_exact_status_artifact_cannot_use_an_old_cold_only_or_cover_gate(self):
        report, expected = fixture('cold', 'screen')
        for earlier, mechanism in [('cold', DECODE), ('cover', 'bounded_cached_covers')]:
            changed = copy.deepcopy(report)
            changed['mechanism'] = {'name': mechanism, 'verified': True}
            with self.subTest(candidate=earlier), self.assertRaisesRegex(ValueError, 'relabeled'):
                gate.freeze_gate(changed, 'screen', earlier, expected)

    def test_shipping_must_reuse_the_actual_default_qualification_reference_set(self):
        refs = {k: sha(k.encode()) for k in ('screen', 'confirm', 'pixels', 'warm_guard')}
        selection = {'candidates': {'status': {k: {'sha256': refs[k]} for k in ('screen','confirm','warm_guard')}},
                     'qualification': {'sha256': refs['pixels']}}
        gate.match_release_references(selection, {'reference_hashes': refs})
        for key in refs:
            with self.subTest(replaced=key), self.assertRaises(ValueError):
                gate.match_release_references(selection, {'reference_hashes': refs | {key: sha(b'unrelated frozen inputs')}})

    def test_cold_screen_and_independent_confirmation_use_the_new_single_app(self):
        gate.allowed_changes(['status'], {APP: {}})
        for with_other in ('supervisor', 'cover', 'cold'):
            proposed = {**{APP: {}}, **({gate.LAUNCH: {}, gate.EXTRA: {}} if with_other == 'supervisor' else {})}
            with self.subTest(other=with_other), self.assertRaises(ValueError):
                gate.allowed_changes(['status', with_other], proposed)
        runs = []
        for phase, n in (('screen', 6), ('confirm', 10)):
            report, expected = fixture('cold', phase, covers=[101] * (n - 1) + [-5], usable=[-50] * n)
            runs.append(gate.freeze_gate(report, phase, 'status', expected))
        self.assertTrue(runs[0].isdisjoint(runs[1]))

    def test_cold_gate_recomputes_primary_improvement_and_usable_guard(self):
        for phase, n in (('screen', 6), ('confirm', 10)):
            for change in ('insufficient-wins', 'too-small', 'usable-guard'):
                cover = [200] * n if change == 'usable-guard' else [99] * n
                if change == 'insufficient-wins':
                    cover = [200] * (n - 2) + [-1, -1]
                report, wanted = fixture('cold', phase, covers=cover, usable=[-51 if change == 'usable-guard' else 50] * n)
                with self.subTest(phase=phase, reason=change), self.assertRaises(ValueError):
                    gate.freeze_gate(report, phase, 'status', wanted)

    def test_warm_guard_allows_the_predeclared_boundary_but_claims_no_speedup(self):
        for us, cv in (([-50] * 6, [-50] * 6), ([-250, 150, -110, 75, 4, -5], [4, -9, 0, 7, 45, -50])):
            report, expected = fixture('warm', 'screen', usable=us, covers=cv)
            self.assertIsNone(report['latency_gate_passed'])
            gate.warm_status_guard(report, expected)

    def test_apparent_warm_gain_cannot_hide_a_51ms_regression_at_either_endpoint(self):
        for affected in ('usable', 'covers'):
            gains = {key: [-51 if key == affected else 500] * 6 for key in ('usable', 'covers')}
            report, expected = fixture('warm', 'screen', **gains)
            # Overwrite both the boolean and the usual summary: only the six raw
            # paired observations can establish the declared protection.
            report['comparisons_ms'][affected]['paired_savings_ms']['median'] = 9999
            report['warm_guard_passed'] = True
            with self.subTest(affected=affected), self.assertRaises(ValueError):
                gate.warm_status_guard(report, expected)

    def test_warm_guard_cannot_be_substituted_by_another_workload_or_incomplete_pairs(self):
        for change in ('cold', 'phase', 'wrong-flag', 'duplicate', 'drop-slow-pair', 'unbalanced', 'foreign-batch'):
            report, expected = fixture('cold' if change == 'cold' else 'warm', 'screen')
            if change == 'phase':
                report['phase'] = 'confirm'
            elif change == 'wrong-flag':
                report['warm_guard_passed'] = False
            elif change == 'duplicate':
                report['pairs'][3] = copy.deepcopy(report['pairs'][1])
            elif change == 'drop-slow-pair':
                report['pairs'].pop()
            elif change == 'unbalanced':
                report['pairs'][2]['order'] = 'BA'
            elif change == 'foreign-batch':
                report['batches'][0]['cache_regime'] = 'empty-cache-v1'
            report['batches'][0]['pairs'] = copy.deepcopy(report['pairs'])
            with self.subTest(invalid=change), self.assertRaises(ValueError):
                gate.warm_status_guard(report, expected)

    def test_control_and_candidate_must_be_the_frozen_firmware_inputs_without_the_old_overlay(self):
        for phase, workload in (('screen', 'cold'), ('screen', 'warm'), ('qualify', 'cold')):
            for change in ('old-cold-binary', 'same-binary', 'different-new-binary', 'wrong-control', 'supervisor-extra', 'switched-launcher'):
                binary = OLD if change == 'old-cold-binary' else DEPLOYED if change == 'same-binary' else STATUS
                if change == 'different-new-binary':
                    binary = sha(b'an entirely separate candidate')
                control = OLD if change == 'wrong-control' else DEPLOYED
                extra = {'aidoku-startup-portrait': sha(b'unmeasured helper')} if change == 'supervisor-extra' else {}
                report, expected = fixture(workload, phase, control=control, treatment=binary, extra=extra)
                if change == 'switched-launcher':
                    report['artifacts']['candidate'] = identity(binary, launcher_sha256=sha(b'unmeasured launcher'))
                    report['batches'][0]['artifacts'] = copy.deepcopy(report['artifacts'])
                with self.subTest(phase=phase, workload=workload, change=change), self.assertRaises(ValueError):
                    if phase == 'qualify':
                        gate.qualify_status(report)
                    elif workload == 'warm':
                        gate.warm_status_guard(report, expected)
                    else:
                        gate.freeze_gate(report, phase, 'status', expected)

    def test_cold_needs_both_mechanisms_and_warm_needs_the_new_toolbar_evidence(self):
        for workload in ('warm', 'cold'):
            invalid = [('mechanism', 'name', DECODE), ('mechanism', 'toolbar_verified', False),
                       ('status_mechanism', 'name', 'bounded_cached_covers'), ('status_mechanism', 'verified', False)]
            if workload == 'cold':
                invalid += [('cold_mechanism', 'name', TOOLBAR), ('cold_mechanism', 'verified', False),
                            ('mechanism', 'cold_decode_verified', False)]
            for key, field, wrong in invalid:
                for group in ('top', 'batch'):
                    report, expected = fixture(workload, 'screen')
                    (report if group == 'top' else report['batches'][0])[key][field] = wrong
                    with self.subTest(w=workload, key=key, field=field, group=group), self.assertRaises(ValueError):
                        if workload == 'cold':
                            gate.freeze_gate(report, 'screen', 'status', expected)
                        else:
                            gate.warm_status_guard(report, expected)

    def test_independent_pixels_require_the_actual_visible_right_toolbar(self):
        report, _ = fixture('cold', 'qualify')
        gate.qualify_status(report)
        for reason in ('blank-right', 'changed-right', 'changed-library', 'moved-roi', 'short-roi',
                       'too-much-crop', 'foreign-run', 'not-independent', 'old-analyzer'):
            invalid = copy.deepcopy(report)
            row = invalid['pixels'][0]
            if reason == 'blank-right':
                row['right_status_bar_observable'] = False
            elif reason == 'changed-right':
                row['identical_right_status_bar'] = False
            elif reason == 'changed-library':
                row['identical_below_status_bar'] = False
            elif reason == 'moved-roi':
                row['right_status_bar_region']['x'] -= 25
            elif reason == 'short-roi':
                row['right_status_bar_region']['width'] -= 25
            elif reason == 'too-much-crop':
                row['status_bar_excluded_pixels'] = 74
                row['right_status_bar_region']['height'] = 74
            elif reason == 'foreign-run':
                row['batch'] = 'previous-report'
            elif reason == 'not-independent':
                invalid['batches'][0]['mode'] = 'screen'
            else:
                invalid['right_status_bar_analyzer_sha256'] = sha(b'unknown overlay comparison')
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                gate.qualify_status(invalid)


if __name__ == '__main__':
    unittest.main()
