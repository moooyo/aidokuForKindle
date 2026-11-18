"""Exercise the summary statistics and redaction using synthetic public fixtures."""

import json
import runpy
import tempfile
import unittest
from pathlib import Path


EX = runpy.run_path(str(Path(__file__).with_name('export.py')))
SIDES = ('baseline', 'candidate')
KEYS = ('usable', 'covers', 'exit')


def warm_fixture(loss=0):
    run = 'aidoku-next-example-11223344'
    artifacts = {s: {'application_sha256': EX['APPLICATION'] if i == 0 else EX['COLD_APP'],
        'launcher_sha256': EX['LAUNCHER'], 'native_query_sha256': EX['QUERY'],
        'another_identity': 'must-never-be-exported'} for i, s in enumerate(SIDES)}
    batch = {'run_id': run, 'mode': 'screen', 'artifacts': artifacts,
        'batch_guards': dict.fromkeys(EX['GUARDS'], True), 'issues': [], 'pairs': [], 'cases': [],
        'transport': 'private-host.internal'}
    for n in range(1, 7):
        times = {s: dict(usable=1000+n+i*loss, covers=2000+n+i*loss, exit=2200+n)
                 for i,s in enumerate(SIDES)}
        for side in SIDES:
            batch['cases'].append(dict(case=f'{side}-{n:02}', status='ok', endpoints_ms=times[side],
                private_text='private-book-title', diagnostics=dict(library_frames_through_covers=2,
                    reported_local_cache_hits=6, reported_local_cache_misses=0, first_frame_cost_ms=[25,0,2,1])))
        batch['pairs'].append(dict(round=n, order='AB' if n % 2 else 'BA', complete=True,
            baseline_ms=times['baseline'], candidate_ms=times['candidate'],
            savings_ms={k:times['baseline'][k]-times['candidate'][k] for k in KEYS}))
    comps={k:dict(paired_savings_ms={'median':0 if k=='exit' else -loss},
                  improved_pairs=6 if k!='exit' and loss<0 else 0) for k in KEYS}
    return dict(valid=True, phase='screen', primary_metric='covers', batches=[batch], pixels=[],
        input_failures=[], issues=[], comparisons_ms=comps, qualification_passed=False,
        latency_gate_passed=False, mechanism={'verified':False})


class TestExport(unittest.TestCase):
    def compact(self, data, slot='cold-warm-guard', expected_status=None):
        with tempfile.TemporaryDirectory() as temp:
            rep = Path(temp)/'reports'
            rep.mkdir()
            path = rep/'warm-guard.json'
            path.write_text(json.dumps(data))
            return EX['public_report'](path, slot, expected_status)

    def test_difference_of_medians_and_median_of_differences_can_have_opposite_signs(self):
        pairs=[]
        for i,(a,b) in enumerate([(100,400),(200,90),(600,500)]):
            pairs.append({'order':'AB' if i%2 else 'BA','intermediate_frames':[0,1],
                **{k+'_ms':[a,b,a-b] for k in KEYS}})
        result=EX['describe'](pairs)
        self.assertEqual(result['endpoint_ms']['usable']['difference_of_medians'],-200)
        self.assertEqual(result['endpoint_ms']['usable']['median_of_paired_savings'],100)
        self.assertEqual(result['intermediate_frames']['runs_with_any'],[0,3])

    def test_warm_guard_inclusive_boundary_not_a_claim_of_speedup(self):
        for loss,passed in [(50, True),(50.001,False)]:
            outputs, index = self.compact(warm_fixture(loss))
            self.assertIs(index['gate_passed'],passed)
            self.assertIsNone(index['mechanism_verified'])
            self.assertEqual(len(outputs[0]['pairs']),6)
            for forbidden in ('private-book-title','must-never-be-exported','private-host.internal'):
                self.assertNotIn(forbidden,json.dumps((outputs,index)))

    def test_incomplete_pair_is_retained_and_never_accepted(self):
        d=warm_fixture()
        d['valid']=False
        d['batches'][0]['cases'][0]['status']='deadline'
        d['batches'][0]['pairs'][0]['complete']=False
        out,index=self.compact(d)
        self.assertEqual(index['status'],'invalid_or_incomplete')
        self.assertIsNone(index['gate_passed'])
        self.assertEqual(out[0]['pairs'][0]['case_status'],['deadline','ok'])
        self.assertNotIn('usable_ms',out[0]['pairs'][0])
        self.assertIn('candidate',out[0]['pairs'][0]['unpaired_observations_ms'])
        self.assertEqual(index['summary']['complete_pairs'],5)

    def test_guard_rejects_misbound_candidate_and_cache_miss(self):
        d=warm_fixture()
        d['batches'][0]['artifacts']['candidate']['application_sha256']=EX['APPLICATION']
        with self.assertRaises(ValueError):
            self.compact(d)
        d=warm_fixture()
        d['batches'][0]['cases'][0]['diagnostics']['reported_local_cache_misses']=1
        with self.assertRaises(ValueError):
            self.compact(d)

    def test_new_composition_never_inherits_old_cold_data_or_silent_queries(self):
        d=warm_fixture()
        new='3'*64
        d['mechanism']={'name':'cold_decode_with_fresh_toolbar_status','verified':True}
        d['status_mechanism']={'verified':True}
        d['warm_guard_passed']=True
        for r in d['batches'][0]['cases']:
            hit=r['case'].startswith('candidate-')
            r['toolbar']=dict(attempts=int(hit),ready=int(hit),unavailable=0,used=int(hit),fallbacks=0,
                first_view_query_sessions=0 if hit else 1,first_view_query_responses=0 if hit else 5,
                first_view_query_finishes=0 if hit else 1,sync_absent_on_hit=hit,probe_wall_ms=20 if hit else None,
                first_view_ms=40 if hit else 65,initial_send_to_input_ms=365)
        with self.assertRaises(ValueError):
            self.compact(d,'status-warm-guard',new)
        d['batches'][0]['artifacts']['candidate']['application_sha256']=new
        runs,summary=self.compact(d,'status-warm-guard',new)
        self.assertTrue(summary['toolbar_verified'] and summary['gate_passed'])
        self.assertEqual(len(runs[0]['toolbar_observations']),12)
        d['batches'][0]['cases'][1]['toolbar']['first_view_query_responses']=1
        with self.assertRaises(ValueError):
            self.compact(d,'status-warm-guard',new)


if __name__ == '__main__':
    unittest.main()
