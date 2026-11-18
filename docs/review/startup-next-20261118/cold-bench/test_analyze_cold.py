"""Cold-only extractor tests on test-env, with private historical source traces."""

import copy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest


def load():
    if sys.platform != 'linux':
        raise RuntimeError('Run on test-env only')
    path = Path(__file__).with_name('analyze_cold.py')
    spec = importlib.util.spec_from_file_location('cold_analysis_test_target', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cold = load()
ref = cold.load_reference()
BASE = Path('/root/aidoku-startup-delivery-20261118/private')
ARCHIVE = BASE / 'final-installed-inputs-4f83ee4a/outputs/out'
COLD_TOOLS = Path('/root/aidoku-next-coldbench-20261118/tools')
SEED = b'0' * 64 + b'  library.sqlite3\n' + b'1' * 64 + b'  device-profile\n'


def write(path, obj):
    path.write_text(json.dumps(obj))


def seeded(path, data=SEED):
    (path / 'seed.sha256').write_bytes(data)
    (path / 'cold-fixture.sha256').write_text(sha256(data).hexdigest() + '\n')


def version(side):
    fake_sha = {'baseline': 'b', 'candidate': 'c'}[side] * 64
    spec = dict(application_sha256=fake_sha, launcher_sha256='a'*64, native_query_sha256='d'*64,
                args=[], env={}, extra_sha256={})
    spec['variant_sha256'] = sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return spec


def cases(n, first=1):
    return ['baseline-warmup', 'candidate-warmup'] + [f'{s}-{i:02}' for i in range(first, first+n)
        for s in (ref.SIDES if i % 2 else ref.SIDES[::-1])]


class TestCold(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='aidoku-cold-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make(self, mode='pilot', names=None, name='fixture'):
        src = ARCHIVE / 'baseline-warmup'
        root = self.root / name
        out = root / 'outputs/out'
        out.mkdir(parents=True)
        if names is None:
            names = cases(6 if mode == 'screen' else 1)
        plan = dict(run_id=name, mode=mode, primary='covers', cases=names,
                    variants={s: version(s) for s in ref.SIDES}, device_script_sha256=cold.COLD_DEVICE_SHA,
                    runner_sha256=cold.COLD_RUNNER_SHA, payload_sha256='e'*64,
                    cache_regime=cold.REGIME, fixture_policy=cold.POLICY)
        write(root / 'plan.json', plan)
        write(root / 'report.json', dict(plan, commands=[], **{k: True for k in ref.FLAGS}))
        write(root / 'lease-released.json', dict(run_id=name, ok=True, owner_and_token_checked=True,
              native_restored_and_connections_closed=True, lease_directory_absent_after_release=True))
        (root / 'device.sh').symlink_to(COLD_TOOLS / 'bench_device.sh')
        (root / 'runner.py').symlink_to(COLD_TOOLS / 'run_bench.py')
        for f in ('before.stdout', 'after.stdout'):
            (root / f).write_bytes(b'synthetic-snapshot')
        for name in names:
            d = out / name
            d.mkdir()
            t = json.loads((src / 'application.json').read_bytes())
            if name.startswith('candidate'):
                # Synthetic markers/magnitude only test decisions, not device performance.
                for evt in t['events']:
                    if evt['name'] == 'startup_visible_covers_settled':
                        evt['boottime_us'] -= 500
                events = []
                for evt in t['events']:
                    if evt['name'] == cold.PREPARED:
                        for k, offset in ((cold.BEGIN_DECODE, -101), (cold.VALIDATED, -1)):
                            events.append(dict(evt, name=k, boottime_us=evt['boottime_us'] + offset))
                    events.append(evt)
                t['events'] = events
            write(d / 'application.json', t)
            (d / 'state.json').symlink_to(src / 'state.json')
            shell = (src / 'shell.tsv').read_text().replace(' document_entry', ' launcher_bridge')
            if name.endswith('-qualify'):
                ready = next(e['boottime_us'] for e in t['events'] if e['name'] == 'startup_visible_covers_settled')
                shell += f'{ready / 1e6 + 0.02:.6f} native_capture_started\n'
                for f in ('native-settled.ppm','final.metadata.json'):
                    (d / f).symlink_to(ARCHIVE / (name.split('-')[0] + '-qualify') / f)
            (d / 'shell.tsv').write_text(shell)
            for fn, raw in [('case-ok', b'ok\n'), ('launcher.log', b'aidoku: Catalog notification completed.\n'),
                            ('portrait-engine', b'unmarked\n'), ('catalog-before.identity', b'fixture\n'),
                            ('catalog-after.identity', b'fixture\n'), ('catalog-before.access', b'10\n'),
                            ('catalog-after.access', b'11\n'), ('cold-fixture', b'empty-cache-v1\n'),
                            ('cold-counts', b'6 0\n')]:
                (d / fn).write_bytes(raw)
            seeded(d)
        return root

    def run_phase(self, p, phase='pilot', q=None):
        return cold.analyze([p], [q] if q else [], phase, 72)

    def test_three_historical_cold_runs(self):
        dirs = list(BASE.glob('*/outputs/out/baseline-warmup'))
        hits = 0
        for p in dirs:
            row = {}
            cold.case_events(p, ref, row)
            self.assertEqual((row['derivatives_generated'], len(row['jobs']), row['combined_successes']), (6, 6, 0))
            hits += 1
        self.assertGreaterEqual(hits, 3)

    def test_cold_pilot_zero_claim_and_no_seed_hash_export(self):
        d = self.make()
        r = self.run_phase(d)
        self.assertTrue(r['valid'] and r['mechanism']['verified'])
        self.assertIsNone(r['latency_gate_passed'])
        self.assertNotIn(sha256(SEED).hexdigest(), json.dumps(r))
        self.assertNotIn('library.sqlite3', json.dumps(r))
        c = r['batches'][0]['cases'][-1]['cold']
        self.assertEqual((c['jobs_finished'], c['combined_attempts'], c['combined_successes']), (6, 6, 6))

    def test_screen_little_gain_cannot_pass(self):
        d = self.make('screen')
        r = self.run_phase(d, 'screen')
        self.assertTrue(r['valid'] and r['mechanism']['verified']); self.assertFalse(r['latency_gate_passed'])

    def test_failed_warmup_and_seed_mix_invalidate_whole_screen(self):
        d = self.make('screen'); warmup = d / 'outputs/out/baseline-warmup'
        (warmup / 'cold-counts').write_text('5 1\n')
        r = self.run_phase(d, 'screen'); self.assertEqual(r['complete_pairs'], 6)
        self.assertFalse(r['valid'] or r['cold_fixture_verified'] or r['mechanism']['verified']); self.assertIsNone(r['latency_gate_passed'])
        (warmup / 'cold-counts').write_text('6 0\n');seeded(warmup, SEED.replace(b'1',b'2'))
        r = self.run_phase(d, 'screen');self.assertFalse(r['valid'] or r['cold_fixture_match'])

    def test_warm_hits_and_unmatched_mechanism_records(self):
        d = self.make(); c = d/'outputs/out/candidate-01/application.json'; t = json.loads(c.read_bytes());bad=copy.deepcopy(t)
        next(e for e in bad['events'] if e['name']==cold.PREPARED)['name'] = cold.REUSED;write(c,bad)
        r = self.run_phase(d);self.assertEqual(r['complete_pairs'], 0);self.assertFalse(r['valid'])
        t['events'].remove(next(e for e in t['events'] if e['name']==cold.VALIDATED));write(c,t)
        r = self.run_phase(d);self.assertIn('cold_validation_outcome_unmatched', r['batches'][0]['cases'][-1]['issues'])

    def test_fallbacks_retained_and_all_fallback_not_proof(self):
        d=self.make(); c=d/'outputs/out/candidate-01/application.json'; t=json.loads(c.read_bytes()); events=[]
        for e in t['events']:
            if e['name']==cold.BEGIN_DECODE: continue
            if e['name']==cold.VALIDATED: e['name']=cold.FALLBACK
            events.append(e)
        t['events']=events;write(c,t);r=self.run_phase(d)
        self.assertTrue(r['valid']);self.assertEqual(r['complete_pairs'],1);self.assertFalse(r['mechanism']['verified'])
        self.assertEqual(r['batches'][0]['cases'][-1]['cold']['explicit_fallbacks'],6)

    def test_reject_ordinary_warm_plan(self):
        d=self.make()
        for n in ('plan.json','report.json'):
            f = d/n;t=json.loads(f.read_bytes());del t['fixture_policy'];write(f,t)
        self.assertIn('unexpected_cold_fixture_policy', self.run_phase(d)['issues'])

    def test_independent_pixels_require_settled_clock_and_cold_evidence(self):
        p=self.make('screen');q=self.make('qualify',['baseline-qualify','candidate-qualify'],'pixel-fixture')
        r=self.run_phase(p,'screen',q);self.assertTrue(r['qualification_passed'] and r['mechanism']['verified'])
        r=self.run_phase(q,'qualify');self.assertTrue(r['qualification_passed'] and r['mechanism']['verified'])
        shell=q/'outputs/out/candidate-qualify/shell.tsv';lines=shell.read_text().splitlines();lines[-1]='0.01 native_capture_started';shell.write_text('\n'.join(lines)+'\n')
        r=self.run_phase(p,'screen',q);self.assertFalse(r['qualification_passed'] or r['valid'])
        self.assertIn('cold_capture_preceded_complete_covers', r['batches'][-1]['cases'][-1]['issues'])

    def test_multibatch_confirmation_honors_matching_fixture(self):
        p=self.make('confirm',cases(5),'front');q=self.make('confirm',cases(5,6),'back')
        r=cold.analyze([p,q],[],'confirm',None);self.assertEqual(r['complete_pairs'],10);self.assertTrue(r['valid'])
        for d in (q/'outputs/out').iterdir(): seeded(d, SEED.replace(b'0', b'2'))
        r=cold.analyze([p,q],[],'confirm',None);self.assertFalse(r['valid'] or r['cold_fixture_match']);self.assertIsNone(r['latency_gate_passed'])


if __name__ == '__main__':
    unittest.main()
