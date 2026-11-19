"""Synthetic and historical host-only checks for two-visit accounting."""

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


MODULE=Path(__file__).with_name('analyze_reopen.py');spec=importlib.util.spec_from_file_location('analyze_reopen',MODULE)
analysis=importlib.util.module_from_spec(spec);sys.modules[spec.name]=analysis;spec.loader.exec_module(analysis)
DIGEST=lambda b:sha256(b).hexdigest()
CANDIDATE=DIGEST(b'explicit-new-test-program')
STAMP=lambda t:f'{t//1000000}.{t%1000000:06}'
DEFAULT={'portrait':'shell','prewarm':'0','last_cover_grace':'off','import_thumbnail':'off','first_layout':'optimized','resident':'off'}


def store(path,x):path.write_text(json.dumps(x)+'\n')


def record(directory, side, entry, prime, holder, attrs, gui, hit, cover_offset, measured):
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'case-ok').write_text('ok\n');(directory/'gui-owner').write_text(f'{gui} {gui*2} 245\n')
    (directory/'fixture.sha256').write_text(DIGEST(b'private-data-seed-not-published')+'\n')
    (directory/'startup-options.sha256').write_text(attrs['options_sha256']+'\n');(directory/'startup-options-presence').write_text('present\n')
    (directory/'catalog-before.identity').write_text('private catalog id and thumbnail to exclude\n');(directory/'catalog-after.identity').write_text((directory/'catalog-before.identity').read_text())
    (directory/'catalog-before.access').write_text('10001\n' if prime else '10002\n');(directory/'catalog-after.access').write_text('10002\n' if prime else '10003\n');(directory/'portrait-engine').write_text('shell\n');(directory/'launcher.log').write_text('aidoku: Catalog notification completed.\naidoku: Startup portrait supervisor: shell.\n')
    first=1_900_000; shutdown=7_960_000; home=9_960_000;next_home=entry+home
    duration=[0,150_000,160_000,200_000,400_000,450_000,470_000,720_000,721_000,760_000,800_000,home-50_000,home]
    (directory/'shell.tsv').write_text(''.join(f'{STAMP(entry+t)} {n}\n' for t,n in zip(duration,analysis.REF.MARKS)))
    events=[]
    def mark(name,t,**attrs):events.append(dict(name=name,at_us=t,boottime_us=entry+t,**attrs))
    for k,v in {**DEFAULT,**attrs['startup_options']}.items():mark(f'startup_config_{k}_{v}',20_000)
    if not prime and hit:mark('resident_library_keeper_reclaimed',900_000)
    mark('startup_ready',1_800_000);mark('startup_library_frame_submitted',first,marker=3,cover_slots=7,cover_images=0,frame_cost_us=[130000,0,18000,300]);mark('startup_library_input_ready',first+50,marker=3)
    for i in range(6):
        mark('cover_preparation_started',first+20_000+i*60_000)
        if not prime and hit:mark('cover_resident_pixels_hit',first+21_000+i*60_000)
        mark('cover_local_derivative_reused',first+30_000+i*60_000);mark('cover_preparation_finished',first+40_000+i*60_000)
    mark('startup_visible_covers_settled',cover_offset,marker=4,cover_slots=7,cover_images=7,cover_pending=False,frame_cost_us=[65000,12,10000,300]);mark('exit_requested',shutdown-20_000);mark('shutdown_started',shutdown)
    if holder and prime:mark('resident_library_keeper_published',shutdown+20_000)
    mark('shutdown_complete',shutdown+30_000)
    store(directory/'application.json',dict(clock='monotonic_rust_main',dropped_events=0,finished=True,success=True,events=events))
    store(directory/'state.json',dict(route='library',cover_visible_count=6,cover_visible_failure_count=0,arbitrary_private_data='NEVER_ECHO'))
    return next_home


def episode(root,name,n,spec,scenario,all_miss=False):
    directory=root/'outputs/out'/name;side,idx=name.split('-');seed=10_000_000_000+n*180_000_000;hold=side=='candidate' and scenario!='ordinary' and not all_miss
    expires=hold and scenario in ('loss','ttl') and idx=='qualify';hit=hold and not expires
    prime=record(directory/'prime',side,seed,True,hold,spec,2*n+40,False,2950000,idx.isdigit())
    gap=50000000 if expires and scenario=='ttl' else 8_100_000
    second=prime+gap
    last=record(directory/'second',side,second,False,False,spec,2*n+41,hit,2_600_000 if hit else 2_900_000,idx.isdigit())
    (directory/'case-ok').write_text('ok\n');(directory/'pair-inode').write_text('1:237 2:2040\n1:237 2:2040\n')
    data=[];resources=[]
    for i,(tag,t,held) in enumerate([('between',prime+50000,hold),('pre_second',second-10000,hit),('after_second',last+50000,False)]):
        resources.append(f'{tag} {STAMP(t-10000)} {STAMP(t)}\n')
        data.append(f'{tag} '+(f'held 802 640 802 4070 -1 {3+i}' if held else 'absent 0 0 0 0 0 0')+f' {STAMP(t)}\n')
    (directory/'keeper.tsv').write_text(''.join(data));(directory/'resource-intervals.tsv').write_text(''.join(resources))
    if hold:(directory/'holder-fds').write_text('unix-listener-only\n')
    if expires and scenario=='ttl':(directory/'ttl.tsv').write_text(f'{STAMP(prime+70000)} {STAMP(prime+45200000)}\n')
    if expires and scenario=='loss':(directory/'loss-ok').write_text('owned-keeper-terminated\n')


def batch(parent,label,phase='screen',scenario='immediate',start=1,count=6,all_miss=False):
    root=parent/label;root.mkdir();run='aidoku-all-'+label
    names=['baseline-warmup','candidate-warmup']
    if phase=='qualify':names.extend(s+'-qualify' for s in analysis.SIDES)
    else:
        for i in range(start,start+count):names.extend(f'{s}-{i:02}' for s in (analysis.SIDES if i%2 else analysis.SIDES[::-1]))
    specs={}
    for side in analysis.SIDES:
        opts={'resident':'off' if side=='baseline' else '45'}
        v={'application_sha256':CANDIDATE,'launcher_sha256':DIGEST(b'launcher'),'native_query_sha256':analysis.QUERY,'extra_sha256':{},'startup_options':opts,'options_sha256':DIGEST(f"resident={opts['resident']}\n".encode()),'env':{},'args':[]}
        v['variant_sha256']=DIGEST(json.dumps(v,sort_keys=True,separators=(',',':')).encode());specs[side]=v
    p=dict(run_id=run,cases=names,mode=phase,primary='covers',trial='reopen',cache_regime='warm-cache-v2',fixture_policy='sealed-private-cache-v2',original_launcher_sha256=analysis.ORIGINAL_LAUNCHER,variants=specs,baseline_reference_sha256=analysis.DEPLOYED,reopen_scenario=scenario,reopen_policy='data-only-45-v1',reopen_action_ms=6000)
    for k,n,b in [('runner_sha256','runner.py',b'runner'),('device_script_sha256','device.sh',b'device'),('payload_sha256','payload.tar.gz',b'payload')]:p[k]=DIGEST(b);(root/n).write_bytes(b)
    for i,name in enumerate(names):episode(root,name,i,specs[name.split('-')[0]],scenario,all_miss)
    store(root/'plan.json',p);store(root/'report.json',{**p,**dict.fromkeys(analysis.REF.FLAGS,True),'commands':[{'name':'case-'+n,'returncode':0} for n in names]})
    store(root/'lease-released.json',{'ok':True,'run_id':run,'owner_and_token_checked':True,'native_restored_and_connections_closed':True,'lease_directory_absent_after_release':True})
    (root/'before.stdout').write_bytes(b'device and library private snapshot\n');(root/'after.stdout').write_bytes((root/'before.stdout').read_bytes())
    return root


class Reopen(unittest.TestCase):
    def setUp(self):
        d=TemporaryDirectory();self.addCleanup(d.cleanup);self.base=Path(d.name)
        for field,value in [('RUNNER',b'runner'),('DEVICE',b'device')]:
            stub=patch.object(analysis,field,DIGEST(value));stub.start();self.addCleanup(stub.stop)
    def run_report(self,roots,phase='screen',quals=()):return analysis.analyze(roots,list(quals),phase,CANDIDATE)

    def test_paired_second_open_and_wall_accounting(self):
        r=self.run_report([batch(self.base,'window')]);self.assertTrue(r['valid']);self.assertTrue(r['latency_gate_passed']);self.assertTrue(r['mechanism']['verified'])
        self.assertEqual(r['pair_orders'],{'AB':3,'BA':3});self.assertEqual(r['comparisons_ms']['second_covers']['paired_savings_ms']['median'],300)
        c=r['batches'][0]['cases'][3];self.assertEqual(c['resources']['home_cpu_ticks'],1);self.assertIsNone(c['resources']['points']['between']['pss_kib']);t=c['elapsed_ms']
        self.assertEqual(t['first_request_to_second_home'],t['prime_request_to_home']+t['home_gap']+t['second_request_to_home'])
        self.assertEqual(t['first_request_to_second_covers'],t['prime_request_to_home']+t['home_gap']+t['second_covers'])
        self.assertEqual(t['home_gap'],8100);self.assertEqual(t['total_resource_observation'],30)
        self.assertTrue(c['new_gui_process_for_each_visit']);doc=json.dumps(r)
        for s in ['NEVER_ECHO','private catalog id','private-data-seed', '2:2040','4070 -1','1234abcd','640 802']:self.assertNotIn(s,doc)

    def test_fallback_is_not_an_exclusion(self):
        root=batch(self.base,'neverhit',all_miss=True);r=self.run_report([root]);self.assertTrue(r['valid']);self.assertEqual(r['complete_pairs'],6);self.assertFalse(r['latency_gate_passed']);self.assertFalse(r['mechanism']['verified']);self.assertEqual(r['mechanism']['counts']['candidate'],{'normal_reopen_fallback':6})

    def test_disabled_feature_does_not_certify_an_ordinary_exit_as_hit(self):
        root=batch(self.base,'ordinary','qualify','ordinary');r=self.run_report([root],'qualify');self.assertTrue(r['valid']);self.assertTrue(r['qualification_by_scenario']['ordinary']);self.assertIsNone(r['latency_gate_passed']);self.assertFalse(r['mechanism']['verified']);self.assertFalse(r['all_fallbacks_qualified'])

    def test_live_gui_mismatch_inodes_or_catalog_invalidates(self):
        for field in ('gui-owner','pair-inode','catalog-before.identity','fixture.sha256'):
            root=batch(self.base,'corrupted'+str(len(list(self.base.iterdir()))));group=root/'outputs/out/candidate-03'
            if field=='gui-owner':(group/'second'/field).write_bytes((group/'prime'/field).read_bytes())
            elif field=='pair-inode':(group/field).write_text('1:1 2:1\n1:1 2:2\n')
            else:(group/'second'/field).write_text(DIGEST(b'other')+'\n')
            r=self.run_report([root]);self.assertFalse(r['valid']);self.assertEqual(r['planned_pairs'],6);self.assertEqual(r['complete_pairs'],5);self.assertIsNone(r['latency_gate_passed'])

    def test_resource_unknown_zero_and_early_timing_refused(self):
        root=batch(self.base,'resource');group=root/'outputs/out/candidate-01'
        for name,change in [('keeper.tsv',lambda s:s.replace('pre_second held 802 640 802','pre_second held 802 641 802')),
                            ('resource-intervals.tsv',lambda s:s.replace('between ','between 9999999.99 ')),('holder-fds',lambda _: 'unverified\n')]:
            f=group/name;old=f.read_text();f.write_text(change(old));r=self.run_report([root]);self.assertFalse(r['valid']);self.assertEqual(r['complete_pairs'],5);f.write_text(old)

    def test_distinct_qualification_scenarios(self):
        a=batch(self.base,'immediate','qualify','immediate');b=batch(self.base,'expire','qualify','ttl');c=batch(self.base,'gone','qualify','loss');d=batch(self.base,'fullexit','qualify','ordinary')
        r=self.run_report([a,b,c,d],'qualify');self.assertTrue(r['qualification_passed']);self.assertTrue(r['all_fallbacks_qualified']);self.assertIsNone(r['latency_gate_passed'])
        (c/'outputs/out/candidate-qualify/loss-ok').unlink();r=self.run_report([c],'qualify');self.assertFalse(r['valid']);self.assertFalse(r['qualification_passed'])

    def test_ten_ordinal_confirmation_and_no_reuse(self):
        left=batch(self.base,'left','confirm',start=1,count=5);right=batch(self.base,'right','confirm',start=6,count=5)
        r=self.run_report([left,right],'confirm');self.assertTrue(r['valid']);self.assertTrue(r['latency_gate_passed']);self.assertEqual(r['complete_pairs'],10)
        self.assertFalse(self.run_report([left,left],'confirm')['valid']);self.assertFalse(self.run_report([right,left],'confirm')['valid'])

    def test_unrestored_unqualified_or_alias_artifact(self):
        root=batch(self.base,'salt');report=root/'report.json';j=json.loads(report.read_bytes());j['native_restored']=False;store(report,j)
        self.assertFalse(self.run_report([root])['valid']);self.assertFalse(analysis.analyze([root],[],'screen',analysis.DEPLOYED)['valid'])

    def test_input_deadline_preserved(self):
        root=batch(self.base,'timeout');j=json.loads((root/'report.json').read_bytes());next(c for c in j['commands'] if c['name']=='case-candidate-04')['returncode']=124;store(root/'report.json',j)
        r=self.run_report([root]);self.assertFalse(r['valid']);self.assertEqual(r['primary_case_counts']['deadline'],1);self.assertEqual(r['complete_pairs'],5)

    def test_explicit_quick_or_full_action_required(self):
        root=batch(self.base,'expirebytimer');f=root/'outputs/out/candidate-01/prime/application.json';j=json.loads(f.read_bytes());j['events']=[e for e in j['events'] if e['name']!='exit_requested'];store(f,j)
        r=self.run_report([root]);self.assertFalse(r['valid']);self.assertIn('explicit_cooperative_exit_missing',r['batches'][0]['cases'][3]['issues'])

    def test_unqualified_instrument_cannot_self_certify(self):
        root=batch(self.base,'differentmonitor');old=analysis.DEVICE
        with patch.object(analysis,'DEVICE','0'*64):self.assertFalse(self.run_report([root])['valid'])
        self.assertEqual(analysis.DEVICE,old)


if __name__=='__main__':unittest.main()
