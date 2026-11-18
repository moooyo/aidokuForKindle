"""Independent checks of the toolbar/cold candidate analysis on test-env.

Historical captures stay private. Generated candidate events and endpoint
shifts exercise the evidence contract; none count as actual device samples.
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

module = Path(__file__).with_name('analyze_status.py')
spec = importlib.util.spec_from_file_location('toolbar_audit_under_test', module)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

WARM = Path('/root/aidoku-next-bench-20261118/private/aidoku-next-cold-warm-e046d429')
COLD = Path('/root/aidoku-next-coldbench-20261118/private')
SCREEN = COLD / 'aidoku-next-cold-screen-e24c97bb'
CONFIRM = COLD / 'aidoku-next-cold-confirm-32f7dbef'
PIXELS = COLD / 'aidoku-next-cold-pixels-141a0865'
OLD = 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6'
INSTALLED = '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
SYNTHETIC = sha256(b'new-candidate-identity-for-analyzer-tests-only').hexdigest()
START = 'startup_toolbar_probe_started'
READY = 'startup_toolbar_probe_ready'
UNAVAILABLE = 'startup_toolbar_probe_unavailable'
USED = 'startup_toolbar_snapshot_used'
FALLBACK = 'startup_toolbar_sync_fallback'
SENT = 'startup_initial_frame_submitted'
INPUT = 'startup_initial_input_ready'
VIEW = 'startup_view_started'
ATTACHED = 'startup_view_attached'
QUERY = ('native_query_session_ready', 'native_query_response', 'native_query_session_finished')


def store(path, value):
    path.write_text(json.dumps(value) + '\n')


def read(path):
    return json.loads(path.read_bytes())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def find(events, name):
    return next(e for e in events if e['name'] == name)


def offset(anchor, name, delta):
    return dict(name=name, at_us=anchor['at_us'] + delta,
                boottime_us=anchor['boottime_us'] + delta)


def add_toolbar(trace, outcome='hit'):
    events = trace['events']
    sent, view, attach = (find(events, name) for name in (SENT, VIEW, ATTACHED))
    if outcome == 'hit':
        events[:] = [e for e in events if not (e['name'] in QUERY and
                    view['at_us'] < e['at_us'] < attach['at_us'])]
    if outcome != 'no_attempt':
        events.append(offset(sent, START, 1_000))
        if outcome != 'unavailable':
            for name, delta in zip((QUERY[0], QUERY[1], QUERY[1], QUERY[2]), (3_000, 5_000, 7_000, 9_000)):
                events.append(offset(sent, name, delta))
        events.append(offset(sent, UNAVAILABLE if outcome == 'unavailable' else READY, 10_000))
    events.append(offset(view, USED if outcome == 'hit' else FALLBACK, 4_000))
    events.sort(key=lambda e: e['at_us'])


class ToolbarAnalysis(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory(prefix='toolbar-analysis-test-')
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def fresh_trace(self, outcome=None):
        self.scratch.joinpath('single').mkdir(exist_ok=True)
        trace = read(WARM / 'outputs/out/baseline-01/application.json')
        if outcome:
            add_toolbar(trace, outcome)
        return trace

    def isolated(self, trace, side='candidate'):
        directory = self.scratch / 'single'
        store(directory / 'application.json', trace)
        return audit.toolbar_events(directory, require, side)

    def batch(self, source, workload, tag='fixture', *, warm_delay=0, fallback=None):
        root = self.scratch / tag
        out = root / 'outputs/out'; out.mkdir(parents=True)
        plan = read(source / 'plan.json')
        report = read(source / 'report.json')
        for data in (plan, report):
            data['run_id'] = tag
            candidate = data['variants']['candidate']
            candidate['application_sha256'] = SYNTHETIC
            del candidate['variant_sha256']
            candidate['variant_sha256'] = sha256(json.dumps(candidate, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest()
        store(root / 'plan.json', plan); store(root / 'report.json', report)
        release = read(source/'lease-released.json'); release['run_id'] = tag
        store(root/'lease-released.json', release)
        for name in ('device.sh', 'runner.py', 'before.stdout', 'after.stdout'):
            (root/name).symlink_to(source/name)
        for name in plan['cases']:
            side = name.split('-')[0]
            d = out/name; d.mkdir()
            original = source/'outputs/out'/name
            # Warm controls have equal synthetic endpoints in both arms. Cold
            # replay preserves the historic matched durations and six misses.
            if workload == 'warm' and side == 'candidate':
                original = source/'outputs/out'/name.replace('candidate-', 'baseline-')
            for child in original.iterdir():
                if child.is_file() and child.name != 'application.json':
                    (d/child.name).symlink_to(child)
            trace = read(original/'application.json')
            if side == 'candidate':
                add_toolbar(trace, fallback.get(name, 'hit') if fallback else 'hit')
                if warm_delay and name[-2:].isnumeric():
                    start = find(trace['events'], 'startup_library_frame_submitted')['at_us']
                    for e in trace['events']:
                        if e['at_us'] >= start:
                            e['at_us'] += warm_delay
                            if 'boottime_us' in e:
                                e['boottime_us'] += warm_delay
            store(d/'application.json', trace)
        return root

    def test_six_historical_controls_had_sync_queries(self):
        for d in sorted((WARM/'outputs/out').glob('baseline-??')):
            with self.subTest(case=d.name):
                got = audit.toolbar_events(d, require, 'baseline')
                self.assertEqual(got['classification'], 'baseline_sync')
                self.assertEqual((got['first_view_query_sessions'], got['first_view_query_responses'],
                                  got['first_view_query_finishes']), (1, 5, 1))
                self.assertGreater(got['initial_send_to_input_ms'], 300)

    def test_hit_requires_the_observation_and_consumption_and_no_foreground_queries(self):
        got = self.isolated(self.fresh_trace('hit'))
        self.assertEqual((got['classification'], got['attempts'], got['used'], got['ready']), ('hit', 1, 1, 1))
        self.assertTrue(got['sync_absent_on_hit'])
        self.assertEqual(got['first_view_query_sessions'], 0)
        self.assertEqual(got['probe_wall_ms'], 9)

    def test_unavailable_stale_and_skipped_probes_keep_the_existing_sync_read(self):
        for outcome in ('unavailable', 'stale', 'no_attempt'):
            with self.subTest(outcome=outcome):
                got = self.isolated(self.fresh_trace(outcome))
                self.assertEqual(got['classification'], 'fallback')
                self.assertEqual(got['first_view_query_sessions'], 1)
                self.assertEqual(got['ready'], int(outcome == 'stale'))

    def test_false_hits_cannot_establish_the_mechanism(self):
        for event, after in ((START, INPUT), (READY, VIEW), (USED, ATTACHED)):
            with self.subTest(event=event):
                t = self.fresh_trace('hit'); e = t['events']; e.remove(find(e, event)); e.append(offset(find(e, after), event, 1))
                e.sort(key=lambda item:item['at_us'])
                with self.assertRaises(ValueError):
                    self.isolated(t)

    def test_partial_protocol_or_wrong_initial_marker_does_not_prove_a_snapshot(self):
        for kind in ('one_response', 'no_eof', 'wrong_marker', 'not_full'):
            with self.subTest(kind=kind):
                t = self.fresh_trace('hit'); e = t['events']
                if kind in ('one_response', 'no_eof'):
                    e.remove(find(e, QUERY[1 if kind == 'one_response' else 2]))
                elif kind == 'wrong_marker':
                    find(e, INPUT)['marker'] = find(e, SENT)['marker'] + 1
                else:
                    find(e, SENT)['full'] = False
                with self.assertRaises(ValueError):
                    self.isolated(t)

    def test_work_moved_to_cover_tail_cannot_be_a_successful_hit(self):
        t = self.fresh_trace('hit'); e = t['events']; anchor = find(e, 'startup_library_frame_submitted')
        for name, delta in zip((QUERY[0], QUERY[1], QUERY[2]), (7_000, 8_000, 9_000)):
            e.append(offset(anchor, name, delta))
        e.sort(key=lambda item:item['at_us'])
        with self.assertRaisesRegex(ValueError, 'toolbar_hit_contains_missing_or_deferred_sync_read'):
            self.isolated(t)

    def test_duplicates_and_foreground_query_after_adoption_are_rejected(self):
        for new in ('second_use', 'late_read', 'also_fallback'):
            with self.subTest(new=new):
                t = self.fresh_trace('hit'); e = t['events']; e.append(offset(find(e, VIEW),
                        {'second_use':USED,'late_read':'native_query_session_ready','also_fallback':FALLBACK}[new], 5_000))
                e.sort(key=lambda item:item['at_us'])
                with self.assertRaises(ValueError):
                    self.isolated(t)

    def test_warm_guard_preserves_fallback_pair_and_both_metrics(self):
        p = self.batch(WARM, 'warm', fallback={'candidate-02':'unavailable'})
        r = audit.analyze([p], [], 'warm', 'screen', SYNTHETIC)
        self.assertTrue(r['valid'] and r['warm_guard_passed'], r['issues'])
        self.assertTrue(r['mechanism']['verified'])
        self.assertEqual(r['complete_pairs'], 6)
        self.assertEqual(r['comparisons_ms']['usable']['paired_savings_ms']['median'], 0)

    def test_warm_guard_boundary_does_not_substitute_magnitude_or_cold_results(self):
        for delay, passes in ((50_000, True), (50_001, False)):
            with self.subTest(warm_delay=delay):
                p = self.batch(WARM, 'warm', f'guard-{delay}', warm_delay=delay)
                r = audit.analyze([p], [], 'warm', 'screen', SYNTHETIC)
                self.assertTrue(r['valid'], r['issues']); self.assertEqual(r['warm_guard_passed'], passes)
                self.assertIsNone(r['latency_gate_passed'])

    def test_no_hits_are_retained_without_claiming_a_mechanism(self):
        variants = {f'candidate-{n:02}':'stale' for n in range(1,7)}
        p = self.batch(WARM, 'warm', fallback=variants)
        r = audit.analyze([p], [], 'warm', 'screen', SYNTHETIC)
        self.assertEqual(r['complete_pairs'], 6)
        self.assertFalse(r['mechanism']['verified'])

    def test_old_elf_cannot_be_rebranded(self):
        for app in (INSTALLED, OLD, SYNTHETIC):
            with self.subTest(app=app[:8]):
                try:
                    r = audit.analyze([WARM], [], 'warm', 'screen', app)
                except ValueError:
                    continue
                self.assertFalse(r['valid'])
                self.assertFalse(r.get('warm_guard_passed'))

    def test_cold_screen_independent_confirm_and_native_pixels(self):
        for fixture, phase, pairs in ((SCREEN, 'screen', 6), (CONFIRM, 'confirm', 10), (PIXELS, 'qualify', 0)):
            with self.subTest(phase=phase):
                path = self.batch(fixture, 'cold', 'cold-' + phase)
                r = audit.analyze([path], [], 'cold', phase, SYNTHETIC)
                self.assertTrue(r['valid'] and r['mechanism']['verified'], r['issues'])
                self.assertTrue(r['cold_fixture_verified'] and r['cold_fixture_match'])
                self.assertTrue(r['cold_mechanism']['verified'] and r['status_mechanism']['verified'])
                self.assertEqual(r['complete_pairs'], pairs)
                self.assertTrue(r['latency_gate_passed'] if pairs else r['qualification_passed'])
                self.assertNotIn('library.sqlite3', json.dumps(r))

    def test_right_toolbar_pixel_difference_fails_otherwise_equal_cold_qualification(self):
        from PIL import Image

        root = self.batch(PIXELS, 'cold', 'status-qual')
        r = audit.analyze([root], [], 'cold', 'qualify', SYNTHETIC)
        self.assertTrue(r['qualification_passed'])
        self.assertTrue(r['pixels'][0]['right_status_bar_observable'] and r['pixels'][0]['identical_right_status_bar'])
        d = root/'outputs/out/candidate-qualify/native-settled.ppm'
        with Image.open(d) as actual:
            picture = actual.convert('RGB')
        d.unlink()
        xy = (picture.width-70, 25); c = picture.getpixel(xy)
        picture.putpixel(xy,tuple(255-channel for channel in c)); picture.save(d,format='PPM')
        r = audit.analyze([root], [], 'cold', 'qualify', SYNTHETIC)
        self.assertTrue(r['pixels'][0]['identical_below_status_bar'])
        self.assertFalse(r['pixels'][0]['identical_right_status_bar'])
        self.assertFalse(r['qualification_passed'] or r['valid'])

    def test_guard_and_art_identity_failures_prevent_promotion(self):
        p = self.batch(WARM, 'warm'); (p/'after.stdout').unlink(); (p/'after.stdout').write_bytes(b'changed\n')
        r = audit.analyze([p], [], 'warm', 'screen', SYNTHETIC)
        self.assertFalse(r['valid']); self.assertFalse(r.get('warm_guard_passed'))
        p = self.batch(SCREEN, 'cold', 'dirty-selection')
        for name in ('plan.json', 'report.json'):
            f = p/name; obj = read(f); v = obj['variants']['candidate']; v['args'] = ['--startup-cached-covers','2']
            del v['variant_sha256']; v['variant_sha256'] = sha256(json.dumps(v, sort_keys=True, separators=(',', ':')).encode()).hexdigest(); store(f, obj)
        r = audit.analyze([p], [], 'cold', 'screen', SYNTHETIC)
        self.assertFalse(r['valid']); self.assertFalse(r['latency_gate_passed'])


if __name__ == '__main__':
    unittest.main()
