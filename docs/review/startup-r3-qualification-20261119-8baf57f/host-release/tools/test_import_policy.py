"""The cost of import, deferred work and the next open must be charged exactly once."""

from pathlib import Path
import sys
import unittest

HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE))
import prepare_all as gate


def trial(phase, *, import_cost=10, publish_cost=10, reopen_gain=250, deferred_cost=110, exit_cost=0, pixels=True):
    n=6 if phase=='screen' else 10
    cases=[]
    for i in range(1,n+1):
        a={'reopen_usable_ms':502,'reopen_covers_ms':1500,'reopen_exit_ms':1000,'ingest_exit_ms':800,'ingest_publish_ms':950,
           'ingest_request_to_visible_ms':1000,'two_visible_waits_ms':2500,'two_including_deferred_ms':2500,
           'worker_elapsed_ms':0,'ingest_cached_bytes':1010,'ingest_cache_count':2}
        b=dict(a, reopen_covers_ms=1500-reopen_gain, ingest_request_to_visible_ms=1000+import_cost,
               ingest_publish_ms=950+publish_cost,ingest_exit_ms=800+exit_cost,
               two_visible_waits_ms=2500+import_cost-reopen_gain,two_including_deferred_ms=2500+max(import_cost,deferred_cost)-reopen_gain,
               worker_elapsed_ms=40,ingest_cached_bytes=2000,ingest_cache_count=4)
        cases.append({'index':i,'order':'AB' if i%2 else 'BA','ms_and_bytes':{'baseline':a,'candidate':b}})
    win=reopen_gain>=100
    economic=(import_cost<=50 and publish_cost<=50 and exit_cost<=100 and reopen_gain-import_cost>=100 and
              reopen_gain-max(import_cost,deferred_cost)>=0)
    return {'pairs':cases,'valid':True,'mechanism':{'verified':True},'qualification_passed':pixels,
            'latency_gate_passed':win,'default_gate_passed':phase=='confirm' and pixels and win and economic}


class TwoVisitRules(unittest.TestCase):
    def test_only_a_confirmed_measured_improvement_can_set_the_import_default(self):
        s=gate.import_phase(trial('screen'),'screen');q=gate.import_phase(trial('confirm'),'confirm')
        gate.require_import_default({'screen':s,'confirm':q})
        with self.assertRaises(ValueError):gate.require_import_default({'screen':s})
        without_pixels=gate.import_phase(trial('confirm',pixels=False),'confirm')
        with self.assertRaises(ValueError):gate.require_import_default({'screen':s,'confirm':without_pixels})

    def test_the_faster_next_open_cannot_hide_import_publication_or_exit_cost(self):
        good=gate.import_phase(trial('confirm'),'confirm')
        for kwargs in ({'import_cost':51},{'publish_cost':51},{'exit_cost':101},{'deferred_cost':251},
                       {'reopen_gain':110,'import_cost':11}):
            with self.subTest(kwargs):
                result=gate.import_phase(trial('screen',**kwargs),'screen')
                self.assertTrue(result['reopen_benefit_passed'])
                self.assertFalse(result['benefit_passed'])
                with self.assertRaises(ValueError):gate.require_import_default({'screen':result,'confirm':good})
                bad=gate.import_phase(trial('confirm',**kwargs),'confirm')
                with self.assertRaises(ValueError):gate.require_import_default({'screen':gate.import_phase(trial('screen'),'screen'),'confirm':bad})

    def test_favorable_medians_or_a_cache_hit_do_not_override_the_frozen_default_decision(self):
        no=trial('confirm',deferred_cost=360);no['default_gate_passed']=True
        with self.assertRaises(ValueError):gate.import_phase(no,'confirm')
        pilot=trial('screen');pilot['pairs']=pilot['pairs'][:1]
        with self.assertRaises(ValueError):gate.import_phase(pilot,'screen')
        voluntary=gate.import_phase(trial('screen',import_cost=180),'screen')
        self.assertEqual(gate.classify('import','default_off_optional',voluntary,None,True),'available_default_off')


if __name__=='__main__':unittest.main()
