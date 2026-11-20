"""Synthetic negatives and a host-only read of known R3 retained measurements."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image, ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parent))
import analyze as a


OLD=Path('/root/aidoku-all-bench-20261119'); NEW=Path('/root/aidoku-startup-perf-r4-bench-20261119')


class Analyzer(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.d=Path(self.tmp.name)/'candidate-01';self.d.mkdir()
        self.sealed={
            'library_count':6,'cover_visible_count':6,'cover_visible_failure_count':0,'cover_inflight':False,
            'cover_work_pending':False,'cover_pending_count':0,'cover_queued_count':0,'route':'library'}
        writes={'case-ok':'ok\n','catalog-before.identity':'row\n','catalog-after.identity':'row\n',
                'catalog-before.access':'100\n','catalog-after.access':'101\n','launcher.log':'aidoku: Catalog notification completed.\n',
                'startup-options-presence':'absent\n','startup-options.sha256':a.digest(b'')+'\n','fixture.sha256':'b'*64+'\n',
                'fixture-name':'warm-cache-v2\n','shell.tsv':'''10.00 native_open_requested
10.20 launcher_bridge
10.25 launcher_entry
10.30 launcher_worker
13.00 native_restored
13.10 native_home_active
'''}
        for n,s in writes.items():(self.d/n).write_text(s)
        self.put('state.json',self.sealed)
        start=10500000
        def ev(t,name,**attrs):return dict(at_us=t-start,boottime_us=t,name=name,**attrs)
        self.events=[dict(name='process_entry',at_us=0)]+[ev(start+i+1,key) for i,key in enumerate(sorted(a.POLICY))]
        for i in range(6):
            for j,label in enumerate(('cover_preparation_started','cover_local_derivative_reused','cover_preparation_finished')):
                self.events.append(ev(start+1000+i*100+j,label))
        frame=dict(marker=1,frame_sequence=1,full=True,cover_images=7,cover_pending=False,cover_slots=7)
        self.events += [ev(10900000,'startup_ready'),ev(11000000,'startup_initial_frame_submitted',marker=1,frame_sequence=1,full=True),
            ev(11000001,'startup_library_frame_submitted',**frame),ev(11000002,'startup_cover_frame_submitted',**frame),
            ev(11000003,'startup_visible_covers_settled',**frame),ev(11300000,'startup_initial_input_ready',marker=1,frame_sequence=1,full=True),
            ev(11300002,'startup_library_input_ready',**frame),ev(12000000,'shutdown_started')]
        self.trace={'clock':'monotonic_rust_main','enabled':True,'success':True,'finished':True,'dropped_events':0,'events':self.events}
        self.put('application.json',self.trace)

    def put(self,n,v): (self.d/n).write_text(json.dumps(v))

    def test_accelerated_business_first_with_all_covers_before_input(self):
        result,_,_=a.case(self.d,'warm'); self.assertTrue(result['first_was_initial_full']);self.assertEqual(result['first_slots'],7)
        self.assertAlmostEqual(result['endpoints_ms']['usable'],1300.002);self.assertAlmostEqual(result['endpoints_ms']['covers'],1000.003)

    def test_tile_clock_starts_at_down_not_helper_execution_and_retains_up(self):
        native=SimpleNamespace(origin=lambda *_:{'down':(10100000,10100500),'up':(10154500,10155000)})
        with patch.object(a,'native_tools',return_value=native):
            result,_,_=a.case(self.d,'warm','tile')
        self.assertAlmostEqual(result['endpoints_ms']['usable'],1200.002)
        self.assertEqual(result['native_input']['down_up_endpoint_bounds_ms']['down']['usable'],[1199.502,1200.002]);self.assertEqual(result['native_input']['down_up_endpoint_bounds_ms']['up']['usable'],[1145.002,1145.502]);self.assertEqual(result['native_input']['button_hold_bounds_ms'],[54.0,55.0])
        rows={f'{s}-{i:02}':copy.deepcopy(result) for s in a.SIDES for i in range(1,7)}
        report=a.cohort(rows,'screen','warm',True)
        self.assertEqual(report['native_median_bounds_ms']['down']['usable']['paired_savings'],[-.5,.5]);self.assertFalse(report['screen_passed'])

    def test_unrelated_or_missing_input_marker_is_rejected(self):
        next(e for e in self.events if e['name']=='startup_library_input_ready')['marker']=666
        self.put('application.json',self.trace)
        with self.assertRaises(a.Rejected):a.case(self.d,'warm')

    def test_duplicate_or_partial_completion_is_rejected(self):
        for op in ('repeat','partial','dropped'):
            t=copy.deepcopy(self.trace);e=next(x for x in t['events'] if x['name']=='startup_visible_covers_settled')
            if op=='repeat':t['events'].insert(t['events'].index(e),copy.copy(e))
            if op=='partial':e['cover_images']=6
            if op=='dropped':t['dropped_events']=1
            self.put('application.json',t)
            with self.assertRaises(a.Rejected):a.case(self.d,'warm')

    def test_nonwarm_derivative_or_pixels_in_primary_invalid(self):
        hit=next(e for e in self.events if e['name']=='cover_local_derivative_reused');hit['name']='cover_local_derivative_prepared'
        self.put('application.json',self.trace)
        with self.assertRaisesRegex(a.Rejected,'wrong_cache_regime'):a.case(self.d,'warm')
        (self.d/'final.png').touch()
        with self.assertRaisesRegex(a.Rejected,'nonqualifying_case_instrumented'):a.case(self.d,'warm')

    def test_fence_or_operating_evidence_invalid(self):
        next(e for e in self.events if e['name']=='startup_initial_input_ready')['marker']=2
        self.put('application.json',self.trace)
        with self.assertRaisesRegex(a.Rejected,'checked_initial_fence_missing'):a.case(self.d,'warm')
        self.settle_changed_case()

    def settle_changed_case(self):
        next(e for e in self.events if e['name']=='startup_initial_input_ready')['marker']=1
        self.put('application.json',self.trace);(self.d/'catalog-after.identity').write_text('new\n')
        with self.assertRaisesRegex(a.Rejected,'indexed_identity_changed'):a.case(self.d,'warm')

    def test_exact_paired_gate_and_qualified_screen_only(self):
        rows={}
        for i in range(1,7):
            for j,k in enumerate(a.SIDES):rows[f'{k}-{i:02}']={'endpoints_ms':dict.fromkeys(a.ENDPOINTS,2000-100*j)}
        self.assertTrue(a.cohort(rows,'screen','warm',True)['screen_passed'])
        self.assertFalse(a.cohort(rows,'screen','warm',False)['screen_passed']);self.assertIsNone(a.cohort(rows,'pilot','warm',True)['screen_passed'])
        rows['candidate-02']['endpoints_ms']['covers']=2100;rows['candidate-03']['endpoints_ms']['covers']=2100;rows['candidate-04']['endpoints_ms']['covers']=2100; rows['candidate-05']['endpoints_ms']['covers']=2100
        self.assertFalse(a.cohort(rows,'screen','warm',True)['screen_passed'])

    def test_actual_r3_two_qualified_picture_pairs_for_new_checker(self):
        batches={'warm':'aidoku-all-r3-q-def-w-0d309215','cold':'aidoku-all-r3-q-def-c-a5abbde6'}
        for regime,run in batches.items():
            root=OLD/('base-'+regime)/'private'/run; p=a.obj(root/'plan.json');a.archive_guard(root,p['cases'])
            self.assertTrue(all(all(c.values()) for c in a.pixels(root).values()))
            row,_,_=a.case(root/'outputs/out/candidate-qualify',regime);self.assertEqual(row['jobs'],6)

    def test_left_clock_only_allowed_body_or_right_mismatch_not_hidden(self):
        root=Path(self.tmp.name)
        p=root/'outputs/out/baseline-qualify';q=root/'outputs/out/candidate-qualify'
        for d in (p,q):
            d.mkdir(parents=True);(d/'final.metadata.json').write_text(json.dumps({'route':'library','profile':'scribe','backend':'kindle','landscape':False,'rotation_degrees':0,'window_size':[1860,2480]}))
        def image(d, right, body, clock):
            im=Image.new('RGB',(1860,2480),'white');draw=ImageDraw.Draw(im)
            draw.rectangle((0,0,45,20),fill=(clock,clock,clock));draw.rectangle((1600,12,1700,38),fill=(right,right,right));draw.rectangle((300,100,800,860),fill=(body,body,body));im.save(d/'final.png')
            (d/'native-settled.ppm').write_bytes(b'P6\n# bpp=8 rotation=3\n1860 2480\n255\n'+im.tobytes());im.close()
        image(p,0,0,0);image(q,0,0,255);self.assertTrue(all(all(i.values()) for i in a.pixels(root).values()))
        image(q,255,1,255);received=a.pixels(root)
        for result in received.values():
            self.assertFalse(result['body_equal']);self.assertFalse(result['right_toolbar_equal']);self.assertFalse(result['right_toolbar_observable'])

    def test_prior_r3_cases_reproduce_qualified_endpoints(self):
        batches={'warm':('aidoku-all-v3-default-w-4ccd1128',2845.708),'cold':('aidoku-all-v3-default-c-a41c1389',8264.868)}
        for regime,(run,first_time) in batches.items():
            root=OLD/('base-'+regime)/'private'/run/'outputs/out'
            rows=[a.case(root/f'candidate-{i:02}',regime)[0] for i in range(1,7)]
            self.assertEqual(rows[0]['endpoints_ms']['covers'],first_time);self.assertTrue(all(r['jobs']==6 for r in rows))

    def test_host_preflight_reconstructs_both_frozen_payloads_and_never_qualifies_dry(self):
        for regime in a.REGIMES:
            possible=sorted((NEW/regime/'private').glob('aidoku-all-dry-'+regime+'-*'))
            self.assertTrue(possible)
            for root in possible:
                receipt=a.obj(root/'freeze-receipt.json');h=receipt['plan_sha256'];p=receipt['mode']
                v=a.analyze(root,p,regime,h,a.BASE,True);self.assertFalse(v['valid']);self.assertTrue(v['frozen_artifacts_verified'])
                with self.assertRaisesRegex(a.Rejected,'dry_plan_cannot_be_device_evidence'):a.planguard(root,p,regime,h,a.BASE)
                with self.assertRaisesRegex(a.Rejected,'plan_digest_changed'):a.planguard(root,p,regime,'0'*64,a.BASE)


if __name__=='__main__':unittest.main()
