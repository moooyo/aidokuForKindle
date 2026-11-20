"""Exercise the stock-input state machine with saved frames, never a device."""

import json
from pathlib import Path
import tempfile
import unittest

import tile_mode as t


class Panel:
    def __init__(self, base):
        self.dir=base; self.info=t.original_inputs(); self.name='home'; self.xy=(113,271); self.calls=[]

    def __call__(self,label,text,**kwargs):
        self.calls.append(label)
        if t.PANEL in text: return Path(self.info[self.name]['path'])
        if '--inspect' in text: out={'buttons_clear':True,'width':1860,'height':2480,'pointer_x':self.xy[0],'pointer_y':self.xy[1]}
        elif '--restore-pointer' in text:
            self.xy=tuple(map(int,text.split('--restore-pointer ')[1].split())); out={'ok':True}
        elif '--tap ' in text:
            self.xy=tuple(map(int,text.split('--tap ')[1].split())); out={'ok':True}
            if self.xy==(630,2424) and self.name=='home': self.name='library'
            elif self.xy==(230,2424) and self.name=='library': self.name='home'
            else: raise AssertionError('unsafe navigation command')
        else: out={}
        f=self.dir/(str(len(self.calls))+'.out'); f.write_text(json.dumps(out)); return f


class TileTest(unittest.TestCase):
    def setUp(self):
        self.guard=tempfile.TemporaryDirectory(); self.dir=Path(self.guard.name); self.p=Panel(self.dir)
        self.route=t.Route(self.dir,self.p.info); self.addCleanup(self.guard.cleanup)

    def test_round_trip(self):
        self.route.admit(self.p); self.route.to_library(self.p,'candidate-01')
        x,y,h=self.route.target(self.p,'candidate-01'); self.assertEqual((x,y),(714,866)); self.assertEqual(len(h),64)
        self.route.to_library(self.p,'baseline-02'); self.assertNotIn('stock-tab-baseline-02',self.p.calls)
        self.route.restore(self.p); self.assertEqual(self.p.name,'home'); self.assertEqual(self.p.xy,(113,271))

    def test_no_blind_admission(self):
        self.p.name='library'
        with self.assertRaises(ValueError): self.route.admit(self.p)
        self.assertFalse(self.route.pointerfile.exists()); self.route.restore(self.p)
        self.assertEqual(self.p.calls,['stock-original-home'])

    def test_target_refuses_home(self):
        self.route.admit(self.p)
        with self.assertRaises(ValueError): self.route.target(self.p,'baseline-01')
        self.assertNotIn('stock-tab-baseline-01',self.p.calls)

    def test_changed_frozen_reference(self):
        data=t.original_inputs(); data['home']['sha256']='f'*64
        with self.assertRaises(ValueError): t.Route(self.dir,data)


if __name__=='__main__': unittest.main()
