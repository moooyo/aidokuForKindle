"""Keep performance evidence distinct from isolated functional or pixel observations."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


SPEC = importlib.util.spec_from_file_location('fixture_attribution_gate', Path(__file__).with_name('prepare_all.py'))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def cohort(name, count, hits):
    group = {}
    for arm in ('baseline', 'candidate'):
        used = hits if arm == 'candidate' else 0
        row = dict(observed=count, planned=count, prewarm_attempted_runs=0, prewarm_plans=0,
                   prewarm_ready=0, prewarm_usable_runs=0, prewarm_zero_attempt_runs=count,
                   grace_started_runs=0, grace_completed_single_frame_runs=0, grace_expired=0,
                   grace_cancelled=0, intermediate_cover_frames=0, layouts={'rowwise': count},
                   portrait_engines={'shell': count})
        if name == 'prewarm':
            row.update(prewarm_attempted_runs=used, prewarm_plans=used, prewarm_ready=used,
                       prewarm_usable_runs=used, prewarm_zero_attempt_runs=count-used)
        elif name == 'grace':
            row.update(grace_started_runs=used, grace_completed_single_frame_runs=used)
        elif used:
            key, off, on = ('layouts', 'rowwise', 'batched') if name == 'layout' else ('portrait_engines', 'shell', 'native')
            row[key] = {on: used}
            if count > used:
                row[key][off] = count-used
        group[arm] = row
    return group


def trial(name, phase, timed_hits, *, pixel_hits=0, warmup_hits=0, arithmetic=True):
    n = 6 if phase == 'screen' else 10
    pairs = []
    for i in range(1, n+1):
        usable = 2350 + i*11
        covers = usable + 960
        # Keep a slower opening, whether or not its speculative path was used.
        gain = -230 if i == n else 140 if arithmetic else 20
        ug, cg = (gain, 5) if name in ('layout','supervisor') else (5, gain)
        def ms(u, c):
            return dict(first=u-1, input_ready=u, usable=u, covers=c, exit=1900+i)
        pairs.append(dict(batch='numeric-'+phase, round=i, order='BA' if i%2==0 else 'AB', complete=True,
                          baseline_ms=ms(usable,covers), candidate_ms=ms(usable-ug,covers-cg)))
    return dict(pairs=pairs, latency_gate_passed=arithmetic,
                mechanism=dict(name=name, workload='warm', verified=bool(timed_hits),
                               timing=cohort(name, n, timed_hits), pixels=cohort(name, 1, pixel_hits),
                               warmup=cohort(name, 1, warmup_hits)))


def decision(report, name, phase):
    stats = gate.measure_pairs(report, phase, 'usable' if name in ('layout','supervisor') else 'covers')
    return gate.apply_timing_attribution(report, name, stats)


@unittest.skipUnless(sys.platform == 'linux', 'Run on test-env')
class CrossoverAttribution(unittest.TestCase):
    def test_pixel_and_warmup_hits_cannot_establish_a_screen_or_confirmation_gain(self):
        for name in ('prewarm','grace'):
            rounds = {}
            for phase in ('screen','confirm'):
                report = trial(name, phase, 0, pixel_hits=1, warmup_hits=1)
                # Even a broadly positive structural summary must not stand in for the timing cohort.
                report['mechanism']['verified'] = True
                self.assertTrue(gate.measured_trigger(report, name, 'pixels'))
                self.assertFalse(gate.measured_trigger(report, name, 'timing'))
                rounds[phase] = decision(report, name, phase)
                with self.subTest(name=name, phase=phase):
                    self.assertTrue(rounds[phase]['numeric_latency_gate'])
                    self.assertFalse(rounds[phase]['timing_mechanism_observed'])
                    self.assertFalse(rounds[phase]['benefit_passed'])
            self.assertEqual(gate.classify(name, 'default_off_optional', rounds['screen'], rounds['confirm'], True),
                             'available_default_off')
            with self.assertRaises(ValueError):
                gate.classify(name, 'default_on', rounds['screen'], rounds['confirm'], True)

    def test_sparse_trigger_does_not_remove_any_zero_hit_or_slow_open_from_the_estimates(self):
        for name in ('prewarm','grace'):
            stages = {}
            for phase, n, hits in (('screen',6,1),('confirm',10,2)):
                report = trial(name,phase,hits)
                before = copy.deepcopy(report)
                stages[phase] = decision(report,name,phase)
                got = stages[phase]
                with self.subTest(name=name,phase=phase):
                    self.assertEqual(report, before)
                    self.assertEqual((got['n'],got['wins'],got['primary_median_ms'],got['other_median_ms']), (n,n-1,140,5))
                    self.assertTrue(got['benefit_passed'] and got['numeric_latency_gate'] and got['timing_mechanism_observed'])
            self.assertEqual(gate.classify(name, 'default_on', stages['screen'], stages['confirm'], True), 'confirmed_benefit')

    def test_both_independent_stages_need_timing_attribution(self):
        for name in ('prewarm','grace'):
            for missing in ('screen','confirm'):
                stages = {phase: decision(trial(name,phase,0 if phase==missing else 1,pixel_hits=1),name,phase)
                          for phase in ('screen','confirm')}
                with self.subTest(name=name,missing=missing):
                    self.assertEqual(gate.classify(name,'default_off_optional',stages['screen'],stages['confirm'],True),
                                     'available_default_off')

    def test_attempts_expiry_or_cancel_are_not_successful_consumption(self):
        for name in ('prewarm','grace'):
            report = trial(name,'screen',0)
            arm = report['mechanism']['timing']['candidate']
            if name == 'prewarm':
                arm.update(prewarm_attempted_runs=4, prewarm_plans=4, prewarm_ready=2, prewarm_zero_attempt_runs=2)
            else:
                arm.update(grace_started_runs=5, grace_expired=2, grace_cancelled=3)
            with self.subTest(name=name):
                self.assertFalse(gate.measured_trigger(report,name,'timing'))
                self.assertFalse(decision(report,name,'screen')['benefit_passed'])

    def test_foreign_mechanism_partial_cohort_or_control_execution_rejects_attribution(self):
        for name in ('grace','prewarm','layout','supervisor'):
            for reason in ('name','incomplete','control','incomplete-mechanism'):
                report=trial(name,'screen',6)
                g=report['mechanism']['timing']; self.assertTrue(gate.measured_trigger(report,name,'timing'))
                if reason=='name':report['mechanism']['name']='unrelated'
                elif reason=='incomplete':g['candidate']['observed']-=1
                elif reason=='control':
                    if name=='grace':g['baseline']['grace_started_runs']=1
                    elif name=='prewarm':g['baseline']['prewarm_attempted_runs']=1
                    elif name=='layout':g['baseline']['layouts']={'rowwise':5,'batched':1}
                    else:g['baseline']['portrait_engines']={'shell':5,'native':1}
                elif name=='grace':g['candidate']['grace_started_runs']=1
                elif name=='prewarm':g['candidate']['prewarm_usable_runs']=7
                elif name=='layout':g['candidate']['layouts']={'batched':5,'rowwise':1}
                else:g['candidate']['portrait_engines']={'native':5,'shell':1}
                with self.subTest(name=name,reason=reason):
                    self.assertFalse(gate.measured_trigger(report,name,'timing'))

    def test_valid_function_execution_cannot_override_the_numeric_gate(self):
        for name in ('grace','prewarm'):
            report=trial(name,'confirm',2,arithmetic=False)
            res=decision(report,name,'confirm')
            self.assertTrue(res['timing_mechanism_observed'])
            self.assertFalse(res['benefit_passed'] or res['numeric_latency_gate'])
            report['latency_gate_passed']=True
            with self.subTest(name=name), self.assertRaises(ValueError):decision(report,name,'confirm')

    def test_one_pixel_pair_mislabeled_as_timing_cannot_explain_six_or_ten_pairs(self):
        for name in ('prewarm','grace'):
            for phase in ('screen','confirm'):
                full = trial(name, phase, 1, pixel_hits=1)
                for mislabeled in (('baseline',),('candidate',),('baseline','candidate')):
                    report=copy.deepcopy(full)
                    for side in mislabeled:
                        report['mechanism']['timing'][side]=copy.deepcopy(report['mechanism']['pixels'][side])
                    with self.subTest(name=name,phase=phase,partial=mislabeled):
                        try:
                            result=decision(report,name,phase)
                        except ValueError:
                            continue
                        self.assertFalse(result['timing_mechanism_observed'] or result['benefit_passed'])

    def test_booleans_cannot_substitute_for_integer_mechanism_counts(self):
        for name, changes in (
            ('prewarm',(('baseline','prewarm_attempted_runs',False),('baseline','prewarm_plans',False),
                        ('baseline','prewarm_ready',False),('candidate','prewarm_usable_runs',True))),
            ('grace',(('baseline','grace_started_runs',False),('candidate','grace_started_runs',True),
                      ('candidate','grace_completed_single_frame_runs',True))),
        ):
            for side,key,val in changes:
                report=trial(name,'screen',1)
                report['mechanism']['timing'][side][key]=val
                with self.subTest(name=name,side=side,key=key):
                    try:
                        accepted=gate.measured_trigger(report,name,'timing')
                    except ValueError:
                        continue
                    self.assertFalse(accepted)


@unittest.skipUnless(sys.platform == 'linux', 'Recheck the retained source fixtures on test-env')
class ControlledFunctionalSupport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path('/root/aidoku-all-grace-qualification-20261119/reports')
        cls.path=root/'final-v2-grace.json'
        expected='05a4a48d2817dfa298d254accc5812c33913615f91123c8858abecf3c8300a0d'
        content=gate.pinned(cls.path,expected,512*1024)
        cls.record=json.loads(content)
        cls.ticket={'run_id':cls.record['run_id'],'completed_sha256':cls.record['completed_sha256'],
                    'report':{'uri':cls.path.as_uri(),'sha256':expected}}

    def test_replayed_six_grace_controls_only_establish_behavior(self):
        qualified=gate.controlled_support('grace',self.ticket,self.record['program_sha256'],self.record['source_manifest_sha256'])
        self.assertEqual(set(qualified['cases']),{'early','expiry','cancel','disabled','foreign','rounded-wake'})
        self.assertIs(qualified['counts_as_timing_evidence'],False)
        report=trial('grace','screen',0)
        report['controlled_functional']=qualified
        application=decision(report,'grace','screen')
        self.assertTrue(application['numeric_latency_gate'])
        self.assertFalse(application['benefit_passed'])

    def test_old_build_wrong_feature_or_preview_receipt_cannot_be_reused_through_the_release_gate(self):
        for mismatch in ('source','app','old-completion','preview','wrong-option'):
            ticket=copy.deepcopy(self.ticket); program=self.record['program_sha256'].copy()
            source=self.record['source_manifest_sha256']; name='grace'
            if mismatch=='source':source=sha(b'previous source')
            elif mismatch=='app':program[gate.APP]=sha(b'earlier executable')
            elif mismatch=='old-completion':ticket['completed_sha256']=sha(b'previous completion')
            elif mismatch=='preview':ticket['run_id']='grace-preview-2ba41034d1ac'
            else:name='layout'
            with self.subTest(mismatch=mismatch),self.assertRaises(ValueError):gate.controlled_support(name,ticket,program,source)


if __name__ == '__main__':
    unittest.main()
