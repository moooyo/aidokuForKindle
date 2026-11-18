"""Synthetic end-to-end trials for the startup last-cover grace analyzer.

Run only on test-env, outside a device lease. Timing values, catalog rows and
the application used as the dummy candidate are synthetic. These tests are
not physical-device measurements.
"""

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


if sys.platform != 'linux':
    raise SystemExit('Run only on test-env')

WARM = Path('/root/aidoku-next-bench-20261118/tools')
SOURCE = next((p for p in (Path(__file__).with_name('analyze_frame.py'),
                           Path(__file__).parents[2] / 'frame-bench/analyze_frame.py',
                           Path('/root/aidoku-next-framebench-20261118/tools/analyze_frame.py'))
               if p.is_file()), None)
if SOURCE is None:
    raise RuntimeError('Install the separate grace analyzer alongside this test module')
spec = importlib.util.spec_from_file_location('frame_analyzer', SOURCE)
analyzer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyzer)
ref = analyzer.load_reference()
BASE_APP = '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
ORIGIN = 99_000_000
SIDES = ('baseline', 'candidate')
GRACE_START = 'startup_cover_last_grace_started'
GRACE_END = 'startup_cover_last_grace_completed'
GRACE_EXPIRE = 'startup_cover_last_grace_expired'
GRACE_CANCEL = 'startup_cover_last_grace_cancelled'


def store(path, content):
    path.write_text(json.dumps(content) + '\n')


def observations(role, outcome, gain_us=180_000):
    first = ORIGIN + 2_000_000
    rows = []

    def event(relative, name, **details):
        rows.append(dict(name=name, at_us=relative + 1_000_000,
                         boottime_us=first + relative, **details))

    def frame(relative, name, sequence, images):
        event(relative, name, marker=sequence, frame_sequence=sequence, full=False,
              frame_cost_us=[60_000, 10, 12_000, 390], cover_images=images,
              cover_pending=images != 7, cover_slots=7)

    event(-180_000, 'startup_ready')
    event(-100, 'startup_background_covers_admitted')
    frame(0, 'startup_library_frame_submitted', 3, 0)
    frame(3, 'startup_cover_frame_submitted', 3, 0)
    frame(7, 'startup_library_input_ready', 3, 0)
    for i, t in enumerate([20_000, 70_000, 120_000, 170_000, 220_000, 270_000]):
        event(t, 'cover_preparation_started')
        event(t + 20_000, 'cover_local_derivative_reused')
        event(t + 21_000, 'cover_render_ready')
        event(t + (43_000 if i == 5 else 24_000), 'cover_preparation_finished')
    if outcome:
        event(300_000, GRACE_START)
        event({'complete':314_500,'expire':342_000,'cancel':304_000}[outcome],
              {'complete':GRACE_END,'expire':GRACE_EXPIRE,'cancel':GRACE_CANCEL}[outcome])
    partial = role == 'baseline' or outcome in ('expire', 'cancel')
    if partial:
        frame(410_000, 'startup_cover_frame_submitted', 4, 6)
    event(414_000 if partial else 314_800, 'startup_final_cover_publication')
    end = 680_000 - (gain_us if role == 'candidate' else 0)
    frame(end, 'startup_cover_frame_submitted', 5 if partial else 4, 7)
    frame(end + 1, 'startup_visible_covers_settled', 5 if partial else 4, 7)
    event(8_000_000, 'shutdown_started')
    return {'events':sorted(rows, key=lambda x:x['boottime_us']), 'success':True,
            'finished':True, 'dropped_events':0, 'clock':'monotonic_rust_main'}


def variant(app):
    row = dict(application_sha256=app, launcher_sha256=LAUNCH, native_query_sha256=QUERY,
               args=[], env={}, extra_sha256={})
    row['variant_sha256'] = sha256(json.dumps(row, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return row


class GraceFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix='aidoku-grace-audit-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def batch(self, run, mode='screen', start=1, count=6, outcomes=('complete',) * 6):
        root = self.root / run
        out = root/'outputs/out'
        out.mkdir(parents=True)
        order = ['baseline-warmup','candidate-warmup']
        for n in range(start, start + count):
            order.extend(f'{name}-{n:02}' for name in (SIDES if n % 2 else SIDES[::-1]))
        if mode == 'qualify':
            order = ['baseline-warmup', 'candidate-warmup', 'baseline-qualify', 'candidate-qualify']
        plan = dict(run_id=run, mode=mode, primary='covers', cases=order,
                    variants={'baseline':variant(BASE_APP), 'candidate':variant('c'*64)},
                    runner_sha256=sha256((WARM/'run_bench.py').read_bytes()).hexdigest(),
                    device_script_sha256=sha256((WARM/'bench_device.sh').read_bytes()).hexdigest(),
                    payload_sha256='e'*64)
        store(root/'plan.json', plan)
        store(root/'report.json', {**plan, **dict.fromkeys(ref.FLAGS,True),
              'commands':[{'name':'case-'+name,'returncode':0} for name in order]})
        store(root/'lease-released.json', dict(run_id=run, ok=True, owner_and_token_checked=True,
              native_restored_and_connections_closed=True, lease_directory_absent_after_release=True))
        (root/'runner.py').symlink_to(WARM/'run_bench.py')
        (root/'device.sh').symlink_to(WARM/'bench_device.sh')
        for prefix in ('before','after'):
            (root/(prefix+'.stdout')).write_bytes(b'synthetic-native-snapshot\n')
        for name in order:
            f = out/name; f.mkdir()
            side, kind = name.split('-')
            outcome = outcomes[int(kind)-start] if side == 'candidate' and kind.isdigit() else None
            data = observations(side, outcome)
            store(f/'application.json', data)
            store(f/'state.json', dict(route='library', cover_visible_count=6, cover_visible_failure_count=0))
            stamps = [0,20_000,25_000,100_000,150_000,200_000,210_000,500_000,510_000,
                      600_000,610_000,11_000_000,11_500_000]
            (f/'shell.tsv').write_text(''.join(f'{(ORIGIN+t)/1e6:.6f} {mark}\n' for t,mark in zip(stamps,ref.MARKS)))
            for file, value in {'case-ok':'ok\n','catalog-before.identity':'opaque-test-row\n',
                    'catalog-after.identity':'opaque-test-row\n','catalog-before.access':'10\n',
                    'catalog-after.access':'12\n','launcher.log':'aidoku: Catalog notification completed.\n',
                    'portrait-engine':'unmarked\n'}.items():
                (f/file).write_text(value)
            if kind == 'qualify':
                from PIL import Image
                Image.new('RGB',(618,824),(128,128,128)).save(f/'native-settled.ppm')
                store(f/'final.metadata.json',dict(profile='pw5',route='library',backend='kindle',
                      landscape=False,rotation_degrees=0,window_size=[618,824]))
        return root

    def change_trace(self, root, name, change):
        path = root/'outputs/out'/name/'application.json'
        result = json.loads(path.read_bytes()); change(result['events'])
        result['events'].sort(key=lambda e:e['boottime_us']);store(path,result)

    def test_six_pairs_all_outcomes_and_untriggered_are_kept(self):
        root = self.batch('six', outcomes=('complete',None,'expire','cancel',None,'complete'))
        result = analyzer.analyze([root],[], 'screen',None)
        self.assertTrue(result['valid'], result['issues'])
        self.assertEqual(result['complete_pairs'],6)
        self.assertEqual(result['comparisons_ms']['covers']['paired_savings_ms']['median'],180)
        self.assertTrue(result['latency_gate_passed'] and result['mechanism']['verified'])
        cases = {r['case']:r['frame_grace'] for r in result['batches'][0]['cases']}
        self.assertTrue(cases['candidate-01']['completed_without_intermediate_frame'])
        self.assertFalse(cases['candidate-02']['started'])
        self.assertTrue(cases['candidate-03']['expired'])
        self.assertTrue(cases['candidate-04']['cancelled'])

    def test_no_trigger_cannot_claim_mechanism(self):
        result=analyzer.analyze([self.batch('six',outcomes=(None,)*6)],[], 'screen',None)
        self.assertTrue(result['valid'] and result['latency_gate_passed'])
        self.assertFalse(result['mechanism']['verified'])
        self.assertEqual(result['complete_pairs'],6)

    def test_partial_and_full_projections_from_prior_observed_traces(self):
        # Replay observations collected for the preceding warm candidate. They
        # exercise frame extraction only and are not this candidate's results.
        old = Path('/root/aidoku-next-bench-20261118/private/aidoku-next-covers-screen-db500309/outputs/out')
        frames = []
        for path in sorted(old.glob('baseline-??')):
            data = analyzer.audit_events(path, ref, 'primary')
            self.assertFalse(data['started'])
            self.assertFalse(data['completed_without_intermediate_frame'])
            frames.append(data['partial_frames'])
        self.assertEqual(len(frames),6)
        self.assertTrue(any(f == 0 for f in frames) and any(f > 0 for f in frames), frames)

    def test_expired_is_not_saved_frame(self):
        result=analyzer.analyze([self.batch('six',outcomes=('expire',)*6)],[], 'screen',None)
        self.assertTrue(result['valid']);self.assertFalse(result['mechanism']['verified'])

    def test_only_one_trial_cannot_satisfy_screen(self):
        root = self.batch('short',count=1,outcomes=('complete',))
        r=analyzer.analyze([root],[], 'screen',None)
        self.assertEqual(r['complete_pairs'],1);self.assertFalse(r['valid'] or r['latency_gate_passed'])

    def test_non_grace_partial_frame_cannot_count_as_success(self):
        root=self.batch('six',outcomes=('complete',None,None,None,None,None))
        def partial(events):
            first=next(e for e in events if e['name']=='startup_library_frame_submitted')
            events.append(dict(first,name='startup_cover_frame_submitted',
                boottime_us=first['boottime_us']+309_000,at_us=first['at_us']+309_000,
                marker=4,frame_sequence=4,cover_images=6,cover_pending=True))
            for e in events:
                if e['name'] in ('startup_cover_frame_submitted','startup_visible_covers_settled') and e['cover_images']==7:
                    e['frame_sequence']=e['marker']=5
        self.change_trace(root,'candidate-01',partial)
        r=analyzer.analyze([root],[], 'screen',None)
        self.assertFalse(r['mechanism']['verified'])

    def test_unmatched_and_duplicate_markers_rejected(self):
        for kind in ('no_start','duplicate_end'):
            with self.subTest(kind=kind):
                root=self.batch(kind.replace('_','-'))
                def corrupt(events):
                    if kind=='no_start':events.remove(next(e for e in events if e['name']==GRACE_START))
                    else:events.append(dict(next(e for e in events if e['name']==GRACE_END)))
                self.change_trace(root,'candidate-01',corrupt)
                r=analyzer.analyze([root],[], 'screen',None)
                self.assertFalse(r['valid']);self.assertIsNone(r['latency_gate_passed'])

    def test_miss_control_marker_and_bad_restore_invalidate(self):
        for kind in ('miss','control','restore'):
            with self.subTest(kind=kind):
                root=self.batch(kind)
                if kind=='miss':
                    self.change_trace(root,'candidate-03',lambda e:next(x for x in e if x['name']=='cover_local_derivative_reused').update(name='cover_local_derivative_prepared'))
                elif kind=='control':
                    self.change_trace(root,'baseline-02',lambda e:e.append(dict(e[0],name=GRACE_START)))
                else:
                    (root/'after.stdout').write_bytes(b'changed\n')
                r=analyzer.analyze([root],[], 'screen',None)
                self.assertFalse(r['valid']);self.assertIsNone(r['latency_gate_passed'])

    def test_control_and_candidate_use_formal_default_configuration(self):
        root=self.batch('defaults')
        for side in ('plan.json','report.json'):
            path=root/side;data=json.loads(path.read_bytes());v=data['variants']['candidate']
            v['args']=['--startup-cached-covers','2'];del v['variant_sha256']
            v['variant_sha256']=sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            store(path,data)
        r=analyzer.analyze([root],[], 'screen',None)
        self.assertFalse(r['valid']);self.assertIsNone(r['latency_gate_passed'])

    def test_six_pair_wins_threshold_applies_to_all_late_runs(self):
        root=self.batch('magnitude',outcomes=('complete',None,None,None,None,None))
        for n in (2,4):
            path=root/f'outputs/out/candidate-{n:02}/application.json'
            store(path,observations('candidate',None,-250_000))
        r=analyzer.analyze([root],[], 'screen',None)
        self.assertTrue(r['valid'] and r['mechanism']['verified'])
        self.assertEqual(r['comparisons_ms']['covers']['improved_pairs'],4)
        self.assertFalse(r['latency_gate_passed'])

    def test_two_confirm_halves_and_independent_pixels(self):
        a=self.batch('front','confirm',1,5)
        b=self.batch('back','confirm',6,5)
        q=self.batch('pixels','qualify')
        r=analyzer.analyze([a,b],[q],'confirm',None)
        self.assertEqual(r['complete_pairs'],10);self.assertTrue(r['latency_gate_passed'] and r['qualification_passed'],r['issues'])
        from PIL import Image
        path=q/'outputs/out/candidate-qualify/native-settled.ppm'
        with Image.open(path) as old: picture = old.convert('RGB')
        picture.putpixel((8,200),(0,0,0));picture.save(path)
        r=analyzer.analyze([a,b],[q],'confirm',None)
        self.assertFalse(r['valid'] or r['qualification_passed'])


if __name__ == '__main__':
    unittest.main()
