"""Check privacy, version binding and paired estimates without private device data."""

import copy
import json
import runpy
import unittest
from pathlib import Path


module=runpy.run_path(str(Path(__file__).with_name('export.py')))
report=Path('/root/aidoku-all-bench-20261119/reports/default-warm.json')


class EvidenceTests(unittest.TestCase):
    def test_paired_estimate_is_not_a_difference_between_groups(self):
        pairs=[{'order':o,'time':module['triple'](a,b)} for o,a,b in [('AB',100,400),('BA',200,90),('AB',600,500)]]
        result=module['summary'](pairs,['time'])['time']
        self.assertEqual(result['difference_of_medians'],-200)
        self.assertEqual(result['median_paired_saving'],100)
        self.assertEqual(result['positive_pairs'],2)

    @unittest.skipUnless(report.is_file(), 'The real public test-env analyzer must be available')
    def test_real_guard_ignores_unknown_private_fields_but_rejects_another_elf(self):
        data=json.loads(report.read_text());data['private_device_details']='do-not-copy'
        data['batches'][0]['cases'][2]['book']='do-not-copy'
        result=module['regular'](data)
        self.assertNotIn('do-not-copy',json.dumps(result))
        self.assertEqual(len(result['pair_data']),6)
        self.assertTrue(result['gates']['protection']);self.assertIsNone(result['gates']['latency'])
        other=copy.deepcopy(data);other['artifacts']['candidate']['application_sha256']='a'*64
        with self.assertRaises(ValueError):module['regular'](other)
        other=copy.deepcopy(data);other['expected_candidate_application_sha256']=module['R2']
        with self.assertRaises(ValueError):module['regular'](other)
        with self.assertRaises(ValueError):module['compact_index']({'r1-warm':result},module['R2'])

    @unittest.skipUnless(report.is_file(), 'The real public test-env analyzer must be available')
    def test_failed_phase_or_pilot_cannot_count_towards_a_speedup(self):
        d=json.loads(report.read_text());d['valid']=False;d['issues']=['frozen_plan_mismatch']
        d['default_guard_passed']=None; r=module['regular'](d);self.assertEqual(r['status'],'invalid_observations')
        item=module['compact_index']({'failed':r})['groups']['defaults-warm'];self.assertEqual(item['status'],'invalid_observations_only')
        self.assertEqual(item['reports'][0]['decision_pairs'],0)

    def test_import_pilot_keeps_cache_work_separate_and_redacts_per_title_proofs(self):
        run='aidoku-all-import-independent-12345678'
        arms={s:{'application_sha256':module['CANDIDATE'],'launcher_sha256':module['LAUNCHERS'][1],
                 'startup_options':{'import_thumbnail':v}} for s,v in zip(module['SIDES'],('off','idle'))}
        ms={s:dict.fromkeys(module['IMPORT_METRICS'],1000) for s in module['SIDES']}
        ms['candidate']['worker_elapsed_ms']=1500; ms['candidate']['reopen_covers_ms']=700
        delta={k:ms['baseline'][k]-ms['candidate'][k] for k in module['IMPORT_METRICS']}
        cases={}
        for side,hit,miss,work in [('baseline',11,4,0),('candidate',15,0,4)]:
            for kind,metrics in [('ingest',{'worker_jobs':work}),('reopen',{'reused':hit,'created':miss})]:
                cases[side+'-01-'+kind]={'metrics':metrics,'source_and_pixels':{'PRIVATE_BOOK_HASH':'unpublishable'}}
        data=dict(phase='pilot',valid=True,input_errors=[],physical_batches=[run],cases={run:cases},artifacts=arms,
            pairs=[dict(index=1,order='AB',ms_and_bytes=ms,savings=delta)],complete_pairs=1,
            summary={k:{'paired_savings_median':v} for k,v in delta.items()},
            mechanism={'verified':True},qualification_passed=False,latency_gate_passed=False,default_gate_passed=False)
        r=module['imported'](data);self.assertNotIn('PRIVATE_BOOK_HASH',json.dumps(r))
        self.assertEqual(r['pair_data'][0]['reopen_derivative_hits'],[11,15]);self.assertEqual(r['pair_data'][0]['worker_elapsed_ms'][2],-500)
        self.assertEqual(r['pair_data'][0]['reopen_covers_ms'][2],300);self.assertEqual(r['status'],'pilot_only')


if __name__=='__main__':unittest.main()
