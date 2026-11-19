"""Independent, host-only contract checks for the November 19 crossover.

Run with python3 -I on test-env. The private source traces are from the
*already deployed* application. Their option markers, versions, ordering and
gains in these fixtures are synthetic and are never device measurements.
"""

from collections import Counter
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest


if sys.platform != 'linux':
    raise SystemExit('Run only on test-env')

spec = importlib.util.spec_from_file_location('new_startup_analysis', Path(__file__).with_name('analyze_all.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

CURRENT = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
OLD = ('62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612',
       'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6')
QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
NEW = sha256(b'synthetic all-options binary').hexdigest()
EXTRA = sha256(b'synthetic fixed portrait helper').hexdigest()
NEW_LAUNCH = sha256(b'synthetic launcher').hexdigest()
REVIEWED = Path('/root/aidoku-all-bench-20261119')
WARM = Path('/root/aidoku-next-bench-20261118/private/aidoku-next-status-warm-f227d2dc')
COLD = Path('/root/aidoku-next-coldbench-20261118/private/aidoku-next-status-conf-67f21f08')
PIXELS = COLD.parent / 'aidoku-next-status-pixels-368e7653'
SIDES = ('baseline', 'candidate')
OPTIONS = {'prewarm': ('prewarm','0','2'), 'grace': ('last_cover_grace','off','on'),
           'layout': ('first_layout','baseline','optimized')}
DEFAULTS = {'portrait':'shell','prewarm':'0','last_cover_grace':'off','import_thumbnail':'idle',
            'first_layout':'optimized','resident':'off'}


def load(f): return json.loads(f.read_bytes())
def save(f,v): f.write_text(json.dumps(v,allow_nan=False) + '\n')
def digest(b): return sha256(b).hexdigest()
def canonical(obj): return json.dumps(obj,sort_keys=True,separators=(',',':')).encode()
def config_bytes(options): return ''.join(f'{k}={options[k]}\n' for k in sorted(options)).encode('ascii')


def spec_variant(app, launcher, env=None, extra=None, options=None):
    options = dict(options or {})
    fields = dict(application_sha256=app, launcher_sha256=launcher, native_query_sha256=QUERY,
                  args=[],env=env or {},extra_sha256=extra or {}, startup_options=options,
                  options_sha256=digest(config_bytes(options)))
    fields['variant_sha256'] = digest(canonical(fields))
    return fields


def order(phase,start=1,count=None):
    names = [s + '-warmup' for s in SIDES]
    if phase == 'qualify': return names + [s + '-qualify' for s in SIDES]
    count = count if count is not None else {'screen':6,'confirm':10,'pilot':1}[phase]
    return names + [f'{s}-{n:02}' for n in range(start,start+count) for s in (SIDES if n % 2 else SIDES[::-1])]


def variants(trial):
    if trial == 'combined':
        return {'baseline':spec_variant(CURRENT,LAUNCH),
                'candidate':spec_variant(NEW,NEW_LAUNCH,extra={'aidoku-startup-portrait':EXTRA})}
    if trial == 'supervisor':
        return {s:spec_variant(NEW,NEW_LAUNCH,env={'AIDOKU_STARTUP_PORTRAIT': mode},
                 extra={'aidoku-startup-portrait':EXTRA}) for s,mode in zip(SIDES,('legacy','native'))}
    k,a,b = OPTIONS[trial]
    return {s:spec_variant(NEW,LAUNCH,options={k:val}) for s,val in zip(SIDES,(a,b))}


def first(evs,name): return next(e for e in evs if e['name']==name)
def offset(event,name,us):
    return {'name':name,'at_us':event['at_us']+us,'boottime_us':event['boottime_us']+us}


def tag(trace,trial,side,workload):
    e=trace['events']; optional = side=='candidate'
    if trial!='combined' or optional:
        start = first(e,'startup_view_started')
        e.extend((offset(start,'library_page_projection_started',20),
                  offset(start,'library_page_projection_rowwise' if trial=='layout' and not optional else 'library_page_projection_batched',40)))
    if trial=='prewarm' and optional and workload=='warm':
        start=first(e,'application_prepared')
        for i,k in enumerate(('startup_cover_warm_plan','startup_cover_warm_attempt','startup_cover_warm_hit')):
            e.append(offset(start,k,(i+1)*100))
        e.append(offset(first(e,'cover_local_derivative_reused'),'cover_local_derivative_memory_hit',-10))
    if trial=='grace' and optional and workload=='warm':
        a = first(e,'startup_background_covers_admitted');done=[x for x in e if x['name']=='cover_preparation_finished']
        opened=a['at_us']+300_100; closed=done[-1]['at_us']+30
        if done[-2]['at_us'] <= opened <= closed < a['at_us']+340_000:
            e.extend((offset(a,'startup_cover_last_grace_started',300_100),
                      offset(done[-1],'startup_cover_last_grace_completed',30)))
    e.sort(key=lambda x:x['at_us'])


class AllFixture(unittest.TestCase):
    def setUp(self):
        scratch=TemporaryDirectory(prefix='all-indexed-entry-fixture-'); self.addCleanup(scratch.cleanup)
        self.dir=Path(scratch.name)

    def make(self,trial,workload='warm',phase='screen',*,name='synthetic-all',gains=None,tagger=tag,start=1,count=None):
        root=self.dir/name;out=root/'outputs/out';out.mkdir(parents=True)
        source=PIXELS if phase=='qualify' else (WARM if workload=='warm' else COLD)
        names=order(phase,start,count);vs=variants(trial)
        runner=(REVIEWED/('base-'+workload)/'run_bench.py').read_bytes()
        script=(REVIEWED/('base-'+workload)/'bench_device.sh').read_bytes()
        plan=dict(run_id=name,mode=phase,primary='first' if trial in ('supervisor','layout') else 'covers',
                  trial=trial,workload=workload,variants=vs,cases=names,
                  fixture_case_seed='independent',baseline_reference_sha256=CURRENT,original_launcher_sha256=LAUNCH,
                  device_script_sha256=digest(script),runner_sha256=digest(runner),payload_sha256='e'*64)
        plan.update(cache_regime='empty-cache-v2' if workload=='cold' else 'warm-cache-v2',
                    fixture_policy='per-case-empty-cache-v2' if workload=='cold' else 'sealed-private-cache-v2')
        report=dict(load(source/'report.json'),**plan)
        report['commands']=[{'name':'case-'+n,'returncode':0} for n in names]
        report['completed']=names
        save(root/'plan.json',plan);save(root/'report.json',report)
        release=dict(load(source/'lease-released.json'),run_id=name);save(root/'lease-released.json',release)
        (root/'runner.py').write_bytes(runner);(root/'device.sh').write_bytes(script)
        for nm in ('before.stdout','after.stdout'):(root/nm).write_bytes(b'protected synthetic state\n')
        for index,n in enumerate(names):
            side=n.split('-')[0]; role=n.split('-')[1];d=out/n;d.mkdir()
            src_name=('candidate-qualify' if role=='qualify' else ('candidate-warmup' if role=='warmup' else 'candidate-01'))
            hist=source/'outputs/out'/src_name
            for f in hist.iterdir():
                if f.is_file() and f.name not in ('application.json','shell.tsv','launcher.log','portrait-engine'):
                    (d/f.name).symlink_to(f)
            if workload=='warm':
                # The initial warmup may seed a different tree; both timed
                # arms must have independent copies of the same sealed seed.
                (d/'fixture-name').write_text('warm-cache-v2\n')
                (d/'fixture.sha256').write_text(('f' if n=='baseline-warmup' else 'a')*64+'\n')
            opts=vs[side]['startup_options'];(d/'startup-options.sha256').write_bytes((vs[side]['options_sha256']+'\n').encode());
            (d/'startup-options-presence').write_bytes(b'present\n' if opts else b'absent\n')
            eng='native' if trial=='supervisor' and side=='candidate' else 'shell' if trial=='supervisor' or (trial=='combined' and side=='candidate') else 'unmarked'
            (d/'portrait-engine').write_bytes((eng+'\n').encode()); (d/'launcher.log').write_bytes(
              (f'aidoku: Startup portrait supervisor: {eng}.\n'.encode() if eng!='unmarked' else b'')+b'aidoku: Catalog notification completed.\n')
            trace=load(hist/'application.json');tagger(trace,trial,side,workload)
            if vs[side]['application_sha256'] != CURRENT:
                # The product's own selection receipts must agree with the
                # actual option file even when a research launcher env is used.
                anchor=first(trace['events'],'platform_initialized')
                for pos,(key,val) in enumerate({**DEFAULTS,**opts}.items()):
                    trace['events'].append(offset(anchor,'startup_config_'+key+'_'+val,pos-12))
                trace['events'].sort(key=lambda event:event['at_us'])
            timeline=[(round(float(stamp)*1_000_000),k) for stamp,k in (l.split() for l in (hist/'shell.tsv').read_text().splitlines())]
            shift=80_000_000*(index+1)-next(t for t,k in timeline if k=='native_open_requested')
            for event in trace['events']:
                if 'boottime_us' in event:event['boottime_us']+=shift
            save(d/'application.json',trace)
            gain=(gains or {}).get(int(role),200_000) if side=='candidate' and role.isnumeric() else 0
            # Give each launch a separate toy boot-time window and change the
            # request origin by the artificial gain. All inner intervals remain intact.
            (d/'shell.tsv').write_text(''.join(f'{(stamp+shift+(gain if k=="native_open_requested" else 0))/1e6:.6f} {k}\n' for stamp,k in timeline))
        return root

    def analyze(self,paths,trial,workload='warm',phase='screen',qualification=()):
        if isinstance(paths,Path):paths=[paths]
        return audit.analyze(paths,list(qualification),trial,workload,phase,NEW,None)

    def recalc(self,root,side,update):
        for name in ('plan.json','report.json'):
            p=root/name;obj=load(p);v=obj['variants'][side];del v['variant_sha256'];update(v)
            v['variant_sha256']=digest(canonical(v));save(p,obj)

    def test_all_isolates_have_actual_observations_and_match_their_configuration(self):
        for trial in ('supervisor','prewarm','grace','layout'):
            with self.subTest(trial=trial):
                p=self.make(trial,name='test-'+trial);r=self.analyze(p,trial)
                self.assertTrue(r['valid'],r['issues']);self.assertEqual(r['complete_pairs'],6)
                self.assertTrue(r['mechanism']['verified']);self.assertTrue(r['latency_gate_passed'])

    def test_same_application_different_options_are_fingerprinted_on_every_launch(self):
        p=self.make('prewarm'); plan=load(p/'plan.json');a,b=(plan['variants'][s] for s in SIDES)
        self.assertEqual(a['application_sha256'],b['application_sha256']);self.assertNotEqual(a['variant_sha256'],b['variant_sha256'])
        d=p/'outputs/out/candidate-05';(d/'startup-options.sha256').write_text(a['options_sha256']+'\n')
        r=self.analyze(p,'prewarm');self.assertFalse(r['valid'] or r['latency_gate_passed'])

    def test_correct_staged_bytes_but_absent_wrong_late_or_duplicate_rust_selection(self):
        for variant in ('missing','wrong','duplicate','late','old-receipt'):
            with self.subTest(variant=variant):
                mode='combined' if variant=='old-receipt' else 'prewarm'
                p=self.make(mode,name='selection-'+variant)
                role='baseline' if variant=='old-receipt' else 'candidate'
                record=p/'outputs/out'/(role+'-03')/'application.json';trace=load(record);e=trace['events']
                if variant=='old-receipt':e.append(offset(first(e,'platform_initialized'),'startup_config_prewarm_0',-1))
                else:
                    receipt=first(e,'startup_config_prewarm_2')
                    if variant=='missing':e.remove(receipt)
                    elif variant=='wrong':receipt['name']='startup_config_prewarm_0'
                    elif variant=='duplicate':e.append(dict(receipt))
                    else:receipt.update(offset(first(e,'startup_initial_frame_submitted'),receipt['name'],1))
                e.sort(key=lambda event:event['at_us']);save(record,trace)
                result=self.analyze(p,mode);self.assertFalse(result['valid'] or result.get('latency_gate_passed') or result.get('default_guard_passed'))

    def test_missing_extra_or_mutated_sidecar_and_config_bytes(self):
        for i,(name,change) in enumerate((('startup-options-presence',b'absent\n'),('startup-options-presence',b'present\nextra\n'),
                                          ('startup-options.sha256',b'deadbeef\n'),('startup-options.sha256',None))):
            with self.subTest(case=i):
                p=self.make('grace',name=f'corrupt-{i}');f=p/'outputs/out/candidate-02'/name
                if change is None:f.unlink()
                else:f.write_bytes(change)
                r=self.analyze(p,'grace');self.assertFalse(r['valid'] or r['latency_gate_passed'])

    def test_historical_applications_cannot_be_substituted_for_day_19_control(self):
        for i,old in enumerate(OLD):
            with self.subTest(old=old[:8]):
                p=self.make('combined',name=f'old-{i}');self.recalc(p,'baseline',lambda v:v.update(application_sha256=old))
                r=self.analyze(p,'combined');self.assertFalse(r['valid']);self.assertFalse(r.get('default_guard_passed'))

    def test_noncanonical_or_unrelated_option_is_not_a_valid_isolate(self):
        p=self.make('layout')
        def mutate(v):
            v['startup_options']['prewarm']='2';v['options_sha256']=digest(config_bytes(v['startup_options']))
        self.recalc(p,'candidate',mutate)
        altered=load(p/'plan.json')['variants']['candidate']['options_sha256']
        for d in (p/'outputs/out').glob('candidate-*'):(d/'startup-options.sha256').write_text(altered+'\n')
        r=self.analyze(p,'layout');self.assertFalse(r['valid'] or r['latency_gate_passed'])

    def test_warm_and_cold_default_guards_use_paired_nonregression_only(self):
        for workload in ('warm','cold'):
            for lag,passed in ((50_000,True),(50_001,False)):
                with self.subTest(workload=workload,lag=lag):
                    p=self.make('combined',workload,name=f'{workload}-{lag}',gains={n:-lag for n in range(1,7)})
                    r=self.analyze(p,'combined',workload);self.assertTrue(r['valid'],r['issues']);self.assertEqual(r['default_guard_passed'],passed)
                    self.assertIsNone(r['latency_gate_passed'])

    def test_wrong_warm_hit_or_cold_miss_regime_blocks_a_report(self):
        for workload in ('warm','cold'):
            with self.subTest(workload=workload):
                p=self.make('combined',workload,name='bad-'+workload);d=p/'outputs/out/baseline-03';t=load(d/'application.json')
                status='cover_local_derivative_';before,after=('reused','prepared') if workload=='warm' else ('prepared','reused')
                first(t['events'],status+before)['name']=status+after;save(d/'application.json',t)
                r=self.analyze(p,'combined',workload);self.assertFalse(r['valid'] or r.get('default_guard_passed'))

    def test_seed_mismatch_is_not_hidden_by_all_successful_cover_jobs(self):
        for workload,file in (('warm','fixture.sha256'),('cold','cold-fixture.sha256')):
            with self.subTest(workload=workload):
                p=self.make('combined',workload,name='mixed-'+workload);f=p/'outputs/out/candidate-02'/file
                if f.is_symlink():f.unlink()
                f.write_text('0'*64+'\n')
                r=self.analyze(p,'combined',workload);self.assertFalse(r['valid'] or r.get('default_guard_passed'))

    def test_no_incomplete_timeout_or_recorded_failure_is_dropped(self):
        p=self.make('layout'); report=load(p/'report.json');report['commands'].append(dict(name='case-candidate-04',returncode=124));save(p/'report.json',report)
        shutil.rmtree(p/'outputs/out/baseline-06');r=self.analyze(p,'layout')
        self.assertFalse(r['valid'] or r['latency_gate_passed']);self.assertEqual(r['planned_pairs'],6);self.assertEqual(r['complete_pairs'],4)

    def test_split_confirm_cannot_reuse_favorable_rounds_or_boot_contexts(self):
        a=self.make('prewarm',phase='confirm',name='first-half',start=1,count=5)
        b=self.make('prewarm',phase='confirm',name='second-half',start=6,count=5)
        r=self.analyze([a,b],'prewarm',phase='confirm');self.assertTrue(r['valid'] and r['latency_gate_passed'],r['issues'])
        bad=self.analyze([a,a],'prewarm',phase='confirm');self.assertFalse(bad['valid'] or bad['latency_gate_passed'])
        for name in ('before.stdout','after.stdout'):(b/name).write_bytes(b'another protected boot\n')
        bad=self.analyze([a,b],'prewarm',phase='confirm');self.assertFalse(bad['valid'] or bad['latency_gate_passed'])

    def test_inverting_two_pair_orders_does_not_create_an_alternate_registered_trial(self):
        root=self.make('layout')
        for f in ('plan.json','report.json'):
            p=root/f;o=load(p)
            for i in (2,4):o['cases'][i:i+2]=list(reversed(o['cases'][i:i+2]))
            save(p,o)
        result=self.analyze(root,'layout');self.assertFalse(result['valid'] or result['latency_gate_passed'])

    def test_favorable_magnitude_without_five_improvements_is_insufficient(self):
        p=self.make('prewarm',gains={2:-170_000,4:-180_000});r=self.analyze(p,'prewarm')
        self.assertTrue(r['valid'],r['issues']);self.assertEqual(r['comparisons_ms']['covers']['improved_pairs'],4);self.assertFalse(r['latency_gate_passed'])

    def test_correct_configuration_with_zero_executions_is_not_acceleration_evidence(self):
        for trial,prefix in (('prewarm','startup_cover_warm_'),('grace','startup_cover_last_grace_')):
            with self.subTest(trial=trial):
                def no_acceleration(t,key,side,w):
                    tag(t,key,side,w)
                    t['events']=[e for e in t['events'] if not (e['name'].startswith(prefix) or e['name']=='cover_local_derivative_memory_hit')]
                p=self.make(trial,name='no-op-'+trial,tagger=no_acceleration)
                r=self.analyze(p,trial);self.assertEqual(r['complete_pairs'],6);self.assertFalse(r['mechanism']['verified'])

    def test_prewarm_counts_with_wrong_phase_or_without_a_consumer_cannot_prove_reuse(self):
        for problem in ('late','reversed','between_jobs'):
            with self.subTest(problem=problem):
                def corrupt(t,key,side,w):
                    tag(t,key,side,w)
                    if side!='candidate':return
                    ev=t['events']; anchor=first(ev,'startup_library_frame_submitted')
                    if problem=='late':
                        for i,k in enumerate(('plan','attempt','hit')):
                            marker=first(ev,'startup_cover_warm_'+k); marker.update(offset(anchor,marker['name'],100+i))
                    elif problem=='reversed':
                        marker=first(ev,'startup_cover_warm_hit'); marker.update(offset(first(ev,'startup_cover_warm_attempt'),marker['name'],-2))
                    else:
                        marker=first(ev,'cover_local_derivative_memory_hit'); marker.update(offset(anchor,marker['name'],10))
                    ev.sort(key=lambda x:x['at_us'])
                p=self.make('prewarm',name='wrong-'+problem.replace('_','-'),tagger=corrupt)
                result=self.analyze(p,'prewarm');self.assertEqual(result['complete_pairs'],6)
                self.assertFalse(result['mechanism']['verified'])

    def test_grace_marker_outside_the_registered_decision_window_is_not_a_completed_win(self):
        for problem in ('too_early','too_late'):
            with self.subTest(problem=problem):
                def corrupt(t,key,side,w):
                    tag(t,key,side,w)
                    if side!='candidate':return
                    events=t['events']; accepted=[e for e in events if e['name']=='startup_cover_last_grace_completed']
                    if not accepted:return
                    if problem=='too_early':
                        finishes=[e for e in events if e['name']=='cover_preparation_finished']
                        marker=first(events,'startup_cover_last_grace_started');marker.update(offset(finishes[-2],marker['name'],25))
                    else:
                        # Keep a credible queue and lineage but move the final
                        # publication/acceptance decision past a bounded wait.
                        marker=accepted[0];publish=first(events,'startup_final_cover_publication')
                        marker.update(offset(first(events,'startup_background_covers_admitted'),marker['name'],372_000))
                        publish.update(offset(marker,publish['name'],100))
                    events.sort(key=lambda x:x['at_us'])
                p=self.make('grace',name='wrong-'+problem.replace('_','-'),tagger=corrupt)
                result=self.analyze(p,'grace');self.assertEqual(result['complete_pairs'],6)
                self.assertFalse(result['mechanism']['verified'])

    def test_timing_does_not_substitute_for_first_layout_path(self):
        def incorrect(t,key,side,w):
            tag(t,key,side,w)
            if side=='candidate':first(t['events'],'library_page_projection_batched')['name']='library_page_projection_rowwise'
        p=self.make('layout',tagger=incorrect);r=self.analyze(p,'layout')
        self.assertEqual(r['complete_pairs'],6);self.assertFalse(r['mechanism']['verified'])

    def test_cold_qualification_checks_actual_toolbar_and_full_business_content(self):
        from PIL import Image

        p=self.make('combined','cold','qualify',name='combo-pixels');r=self.analyze(p,'combined','cold','qualify')
        self.assertTrue(r['qualification_passed'],r['issues']);self.assertTrue(r['pixels'][0]['identical_right_status_bar'])
        f=p/'outputs/out/candidate-qualify/native-settled.ppm'
        with Image.open(f) as image: picture=image.convert('RGB')
        f.unlink();xy=(picture.width-70,16);pixel=picture.getpixel(xy);picture.putpixel(xy,tuple(255-p for p in pixel));picture.save(f,format='PPM')
        r=self.analyze(p,'combined','cold','qualify');self.assertFalse(r['qualification_passed'] or r['valid'])
        self.assertTrue(r['pixels'][0]['identical_below_status_bar']);self.assertFalse(r['pixels'][0]['identical_right_status_bar'])

    def test_known_deployed_traces_are_not_reclassified_as_new_win(self):
        for workload,source in (('cold',COLD),('warm',WARM)):
            with self.subTest(workload=workload):
                try:r=self.analyze(source,'combined',workload,phase='confirm' if workload=='cold' else 'screen')
                except ValueError:continue
                self.assertFalse(r['valid']);self.assertFalse(r.get('default_guard_passed') or r.get('latency_gate_passed'))

    def test_private_source_and_framebuffer_details_are_not_exported(self):
        p=self.make('combined',name='redacted')
        secret='local-user-state-sentinel-123499'
        for n in ('before.stdout','after.stdout'):(p/n).write_text(secret+'\n')
        source=p/'outputs/out/candidate-01/application.json';j=load(source);j['private_book_title']=secret;save(source,j)
        report=self.analyze(p,'combined');self.assertTrue(report['valid'],report['issues'])
        public=json.dumps(report);self.assertNotIn(secret,public);self.assertNotIn('private_book_title',public)
        self.assertNotIn(self.dir.as_posix(),public);self.assertNotIn('library.sqlite3',public)


if __name__ == '__main__':
    unittest.main()
