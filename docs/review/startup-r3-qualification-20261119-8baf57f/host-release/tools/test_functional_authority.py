"""Fail closed when a functional witness for an opt-in is missing or is used for another claim."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import functional_authority as q

RUN='grace-ec810112e67b'
COMPLETION='b1dae68157a47a7ae2e2e875a32ae55c3d1ab197b212707ea31ca30d1f7ee7de'
REPORT={'grace':'05a4a48d2817dfa298d254accc5812c33913615f91123c8858abecf3c8300a0d',
        'prewarm':'6135ab2de099df1c7cda166dca0cecb734d0c78fffb1ab3334c5557e4e21dbb6'}


class ControlledProof(unittest.TestCase):
    def input(self,name):
        p=q.ROOT/'reports'/f'final-v2-{name}.json';d=json.loads(q.bound(p,REPORT[name],256*1024))
        t={'run_id':RUN,'completed_sha256':COMPLETION,'report':{'uri':p.as_uri(),'sha256':REPORT[name]}}
        return d,t

    def test_both_instrumented_source_fixtures_reproduce_without_an_efficacy_claim(self):
        for name in ('prewarm','grace'):
            d,ref=self.input(name);v=q.verify(ref,name,d['program_sha256'],d['source_manifest_sha256'])
            self.assertEqual(v,d);self.assertIsNone(v['latency_gate_passed']);self.assertFalse(v['performance_claim'])

    def test_old_builds_previews_or_partial_positive_observations_cannot_qualify(self):
        for name in ('prewarm','grace'):
            d,ref=self.input(name)
            with self.subTest(name=name),self.assertRaises(ValueError):
                q.verify(ref,name,dict(d['program_sha256'],**{'aidoku/app/aidoku':'1'*64}),d['source_manifest_sha256'])
            bad=copy.deepcopy(ref);bad['run_id']='grace-preview-2ba41034d1ac'
            with self.subTest(preview=name),self.assertRaises(ValueError):q.verify(bad,name,d['program_sha256'],d['source_manifest_sha256'])
            for kind in ('claim','missing','corner'):
                forged=copy.deepcopy(d)
                if kind=='claim':forged['performance_claim']=True
                if kind=='missing':forged['observations'].pop('disabled')
                if kind=='corner' and name=='grace':forged['observations']['rounded-wake']['deferred_after_spent_timer']=True
                if kind=='corner' and name=='prewarm':forged['observations']['changed-after']['ordinary_memory_hits']=1
                with self.subTest(name=name,kind=kind),self.assertRaises(ValueError):
                    q.validate(forged,forged,name,d['program_sha256'],d['source_manifest_sha256'],RUN,COMPLETION)


if __name__=='__main__':unittest.main()
