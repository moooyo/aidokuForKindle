"""Synthetic negative and positive checks. No Kindle connection or lease."""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import tile_evidence as e


def trigger(x=600, y=900, t=1000000, **changes):
    result = dict(mode='tap', clock='CLOCK_BOOTTIME', x=x, y=y, hold_requested_ms=50,
                  down_before_us=t, down_after_us=t+300, up_before_us=t+50800,
                  up_after_us=t+51000, down_confirmed=True, up_sent=True, buttons_clear_after=True, ok=True)
    result.update(changes)
    return result


def inspected(x=207,y=300):
    return dict(mode='inspect',width=1860,height=2480,pointer_x=x,pointer_y=y,
                buttons_clear=True,xtest_major=2,xtest_minor=2,target_has_child=None)


class Evidence(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.marks={'native_open_requested':990000,'launcher_bridge':1100000}

    def save(self,path,doc):
        path=self.root/path
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(doc if isinstance(doc,bytes) else json.dumps(doc).encode())

    def test_origin_requires_exact_button_outcome_and_shell_window(self):
        self.save('tap.json',trigger())
        self.assertEqual(e.origin(self.root,self.marks),{'down':(1000000,1000300),'up':(1050800,1051000)})
        for faults in (dict(clock='CLOCK_REALTIME'),dict(ok=1),dict(up_sent=False),dict(down_confirmed=False),
                       dict(buttons_clear_after=False),dict(up_before_us=1020000),dict(x=2001)):
            self.save('tap.json',trigger(**faults))
            with self.subTest(fault=faults),self.assertRaises(e.Rejected):e.origin(self.root,self.marks)
        self.save('tap.json',trigger())
        for updates in ({'launcher_bridge':1030000},{'native_open_requested':1010000}):
            with self.subTest(updates=updates),self.assertRaises(e.Rejected):e.origin(self.root,self.marks|updates)

    def test_uri_partial_tile_and_mixed_evidence_are_rejected(self):
        self.assertFalse(e.plan_audit(self.root,{},set()))
        for p,names in (({'native_input':'stock-library-XTEST'},set()),({}, {'guitap'}),
                        ({'native_input':'file-uri','tile_controller_sha256':e.CONTROLLER,'tile_targets_sha256':'0'*64},{'guitap'})):
            with self.assertRaises(e.Rejected): e.plan_audit(self.root,p,names)
        self.save('tile-targets.json',{})
        with self.assertRaises(e.Rejected): e.plan_audit(self.root,{},set())

    def setup_logs(self):
        self.names=[]
        def add(name,out=None):
            self.names.append(name); self.save(name+'.stdout',b'' if out is None else out)
        def section(names):
            for name in names:add(name)
        self.plan={'native_input':'stock-library-XTEST','cases':['baseline-warmup','candidate-01']}
        section(['before','create-stage','upload','prepare-device','original-program-backup'])
        add('stock-original-home',b'home');add('stock-initial-pointer',inspected());add('activate-bridge')
        self.save('stock-original-pointer.json',[207,300])
        for i,k in enumerate(self.plan['cases']):
            add('stock-origin-'+k,b'home' if i==0 else b'library')
            if not i:
                add('stock-tab-'+k,trigger(630,2424));add('stock-tab-settle-'+k);add('stock-library-'+k,b'library')
            add('stage-case-'+k);add('stock-target-'+k,b'library');add('case-'+k)
            self.save('outputs/out/'+k+'/tap.json',trigger(t=(i+1)*2000000))
        section(['owned-stage-status','repair-launcher']);add('stock-return-view',b'library')
        add('stock-return-home',trigger(230,2424));add('stock-home-settle');add('stock-restored-home',b'home')
        add('stock-restore-pointer',dict(mode='restore-pointer',x=207,y=300,buttons_clear=True));add('stock-final-pointer',inspected())
        section(['collect','restore-light','after','remove-owned-stage'])
        mod=SimpleNamespace(open_panel=lambda x:x)
        route=SimpleNamespace(at_home=lambda a:a==b'home',at_lib=lambda a:a==b'library',plan={},lib=None,
                              h={'grid':lambda x:x,'locate':lambda *args:(600,900)})
        self.guard=patch.object(e,'evidence_target',return_value=(mod,route)); self.guard.start();self.addCleanup(self.guard.stop)

    def test_full_conditional_sequence_and_home_pointer_restoration(self):
        self.setup_logs();self.assertTrue(e.operations_audit(self.root,self.plan,self.names))
        # A session ending on Home must not invent a needless navigation tap.
        self.save('stock-return-view.stdout',b'home')
        reduced=[n for n in self.names if n not in ('stock-return-home','stock-home-settle')]
        self.assertTrue(e.operations_audit(self.root,self.plan,reduced))
        with self.assertRaises(e.Rejected): e.operations_audit(self.root,self.plan,self.names)

    def test_rejects_mismatch_duplicate_or_unplanned_calls(self):
        self.setup_logs()
        for commands in ([n for n in self.names if n!='stage-case-candidate-01'], self.names+['after'], self.names+['unplanned'],
                         [n for n in self.names if n!='stock-tab-settle-baseline-warmup']):
            with self.assertRaises(e.Rejected): e.operations_audit(self.root,self.plan,commands)

    def test_wrong_tile_replayed_timestamp_and_unrestored_pointer(self):
        self.setup_logs();case='outputs/out/candidate-01/tap.json'
        for bad in (trigger(601,900,t=4000000), trigger(600,900,t=2000000)):
            self.save(case,bad)
            with self.assertRaises(e.Rejected): e.operations_audit(self.root,self.plan,self.names)
        self.save(case,trigger(t=4000000));self.save('stock-final-pointer.stdout',inspected(220,301))
        with self.assertRaises(e.Rejected): e.operations_audit(self.root,self.plan,self.names)

    def test_unknown_stock_view_cannot_fabricate_navigated_visit(self):
        self.setup_logs()
        self.save('stock-target-candidate-01.stdout',b'unknown')
        with self.assertRaises(e.Rejected):e.operations_audit(self.root,self.plan,self.names)


if __name__=='__main__':
    unittest.main()
