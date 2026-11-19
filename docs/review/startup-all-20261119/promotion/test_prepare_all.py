"""Synthetic release-policy, crossover and program-identity checks for test-env."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location('fixture_prepare_all', Path(__file__).with_name('prepare_all.py'))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

APP, LAUNCH, HELPER = 'aidoku/app/aidoku', 'aidoku/launch.sh', 'aidoku/app/aidoku-startup-portrait'
DOC, RT = 'documents/Aidoku.sh', 'aidoku/native-catalog/aidoku-library-dispatch'
BASE_APP = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
PREVIOUS_APP = '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
COLD_ONLY = 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6'
BASE_LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
BASE_QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
FEATURES = ('supervisor', 'prewarm', 'grace', 'layout', 'resident', 'import')
ON, OFF = 'default_on', 'default_off_optional'


def sha(b):
    return hashlib.sha256(b).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def variant(app=BASE_APP, launcher=BASE_LAUNCH, extras=None, options=None, **changes):
    opts = {} if options is None else dict(options)
    content = ''.join(f'{k}={v}\n' for k,v in sorted(opts.items())).encode('ascii')
    data = {'application_sha256': app, 'launcher_sha256': launcher, 'native_query_sha256': BASE_QUERY,
            'extra_sha256': {} if extras is None else extras, 'env': {}, 'args': [],
            'startup_options': opts, 'options_sha256': sha(content)}
    data.update(changes)
    data['variant_sha256'] = sha(canonical(data))
    return data


def endpoint(u, c):
    return {'first': u - 5, 'input_ready': u, 'usable': u, 'covers': c, 'exit': 1520}


def crossover(usables, covers):
    pairs = []
    for i, (ug, cg) in enumerate(zip(usables, covers), 1):
        u, c = 2400 + 3*i, 6000 + 3*i
        pairs.append({'batch': 'synthetic-only-batch', 'round': i, 'order': 'AB' if i % 2 else 'BA',
                      'complete': True, 'baseline_ms': endpoint(u,c), 'candidate_ms': endpoint(u-ug,c-cg)})
    return {'pairs': pairs, 'latency_gate_passed': True,
            'comparisons_ms': {'usable': {'paired_savings_ms': {'median': 999}}, 'covers': {'paired_savings_ms': {'median': 999}}}}


def kpm(payload):
    table = ''.join(f'{sha(data)}|{len(data)}|{mode:o}|{k}\n' for k, (data,mode) in sorted(payload.items()))
    return {**{'payload/' + k: v for k,v in payload.items()}, 'payload.tsv': (table.encode('ascii'),0o644),
            'manifest.json': (b'{"id":"fixture"}\n',0o644),
            **{name:(b'#!/bin/sh\nexit 0\n',0o755) for name in ('install.sh','launch.sh','uninstall.sh')}}


@unittest.skipUnless(sys.platform == 'linux', 'Run release verification through ssh test-env')
class Policy(unittest.TestCase):
    def test_six_explicit_feature_decisions_bind_a_stable_policy_fingerprint(self):
        known = {k: OFF for k in FEATURES}
        self.assertEqual(gate.check_policy(known), sha(canonical(known)))
        for feature in FEATURES:
            candidate = {**known, feature: ON}
            with self.subTest(feature=feature):
                self.assertEqual(gate.check_policy(candidate), sha(canonical(candidate)))
                self.assertEqual(gate.check_policy(candidate), gate.check_policy(dict(reversed(list(candidate.items())))))
                self.assertNotEqual(gate.check_policy(candidate), gate.check_policy(known))
            for bad in ({k:v for k,v in candidate.items() if k != feature}, {**known, feature:'auto'},
                        {**candidate, 'unused': OFF}, {**known, feature: True}):
                with self.subTest(rejected=feature), self.assertRaises(ValueError):
                    gate.check_policy(bad)

    def test_each_feature_requires_physical_mechanism_evidence_even_when_optional(self):
        win = {'benefit_passed': True, 'regression_guard_passed': True}
        for feature in FEATURES:
            for policy in (ON, OFF):
                with self.subTest(feature=feature, policy=policy), self.assertRaises(ValueError):
                    gate.classify(feature, policy, win, win, False)

    def test_both_trials_are_necessary_for_a_confirmed_benefit(self):
        win = {'benefit_passed': True, 'regression_guard_passed': True}
        unproven = {'benefit_passed': False, 'regression_guard_passed': True}
        for feature in FEATURES:
            self.assertEqual(gate.classify(feature, ON, win, win, True), 'confirmed_benefit')
            for screen, conf in ((win, None), (unproven, win), (win, unproven), (unproven, None)):
                with self.subTest(feature=feature, screen=screen, conf=conf):
                    self.assertEqual(gate.classify(feature, OFF, screen, conf, True), 'available_default_off')
                    if feature in ('layout','import'):
                        self.assertEqual(gate.classify(feature, ON, screen, conf, True), 'functional_default_no_speed_claim')
                    else:
                        with self.assertRaises(ValueError):
                            gate.classify(feature, ON, screen, conf, True)

    def test_retired_labels_and_missing_policies_cannot_trigger_the_functional_exception(self):
        no = {'benefit_passed': False, 'regression_guard_passed': True}
        for feature, policy in (('reopen', ON), ('optimize-import', ON), ('layout', 'enabled'), ('import', None)):
            with self.subTest(feature=feature, policy=policy), self.assertRaises(ValueError):
                gate.classify(feature, policy, no, None, True)


@unittest.skipUnless(sys.platform == 'linux', 'Run release verification through ssh test-env')
class PairedTiming(unittest.TestCase):
    def test_each_primary_can_win_at_the_exact_pair_count_and_practical_bounds(self):
        for phase,n in (('screen',6),('confirm',10)):
            for primary in ('usable','covers'):
                gains = [100] * (n - 1) + [-100]
                other = [-50] * n
                data = crossover(gains, other) if primary == 'usable' else crossover(other, gains)
                decision = gate.measure_pairs(data, phase, primary)
                with self.subTest(phase=phase, primary=primary):
                    self.assertEqual((decision['n'],decision['wins']), (n,n-1))
                    self.assertEqual((decision['primary_median_ms'],decision['other_median_ms']), (100,-50))
                    self.assertTrue(decision['benefit_passed'] and decision['regression_guard_passed'])

    def test_apparent_win_cannot_cover_up_negative_or_small_paired_effects(self):
        for phase, n in (('screen',6),('confirm',10)):
            for first, last in (([100]*n,[99]*n), ([-51]*n,[300]*n), ([8]*n,[200]*(n-2) + [0,-5])):
                with self.subTest(phase=phase, first=first[0], covers=last[-1]):
                    result = gate.measure_pairs(crossover(first,last), phase, 'covers')
                    self.assertFalse(result['benefit_passed'])

    def test_default_guard_applies_both_endpoints_without_claiming_speedup(self):
        for first, covers in ((-50,10),(310,350),(200,-51),(-51,280)):
            with self.subTest(first=first, covers=covers):
                res = gate.measure_pairs(crossover([first]*6,[covers]*6), 'default_guard', 'usable')
                self.assertEqual(res['regression_guard_passed'], min(first,covers) >= -50)
                self.assertFalse(res['benefit_passed'])

    def test_reject_incomplete_duplicate_unbalanced_or_physically_inconsistent_pairs(self):
        for case in ('no-endpoint','small-sample','duplicate','unbalanced','incomplete','unbound','wrong-ready','before-first','nan','as-bool'):
            data=crossover([110]*6,[110]*6); pairs=data['pairs']; row=pairs[0]['candidate_ms']
            if case == 'no-endpoint': del row['input_ready']
            elif case == 'small-sample': pairs.pop()
            elif case == 'duplicate': pairs[-1]['round']=pairs[1]['round']
            elif case == 'unbalanced': pairs[0]['order']='BA'
            elif case == 'incomplete': pairs[0]['complete']=False
            elif case == 'unbound': pairs[0]['batch']=''
            elif case == 'wrong-ready': row['input_ready']+=230
            elif case == 'before-first': row['covers']=row['first']-1
            elif case == 'nan': row['covers']=float('nan')
            else: row['exit']=True
            with self.subTest(case=case),self.assertRaises(ValueError):gate.measure_pairs(data,'screen','usable')


@unittest.skipUnless(sys.platform == 'linux', 'Run release verification through ssh test-env')
class ProgramIdentity(unittest.TestCase):
    def test_only_the_actual_installed_control_runs_with_unchanged_defaults(self):
        gate.verify_program_control(variant())
        for args in ({'app':PREVIOUS_APP},{'app':COLD_ONLY},{'launcher':sha(b'other launcher')},
                     {'options':{'first_layout':'baseline'}},{'args':['--offline']},
                     {'env':{'AIDOKU_STARTUP_PORTRAIT':'native'}},{'extras':{'aidoku-startup-portrait':sha(b'extra')}},
                     {'native_query_sha256':sha(b'unrelated query helper')}):
            with self.subTest(changed=tuple(args)),self.assertRaises(ValueError):gate.verify_program_control(variant(**args))

    def test_measured_candidate_binds_exactly_the_three_new_members_and_switches(self):
        program={APP:sha(b'new app'),LAUNCH:sha(b'new launcher'),HELPER:sha(b'new helper')}
        options={'prewarm':'2','first_layout':'optimized'}
        ident=variant(program[APP],program[LAUNCH],{'aidoku-startup-portrait':program[HELPER]}, options)
        gate.verify_program_final(ident, program, options)
        for issue in ('different-settings','semantic-tamper','stamp-only','variant-identity','foreign-app','missing-launcher','extra-file','foreign-env'):
            record, members, declared = copy.deepcopy(ident),program.copy(),options.copy()
            if issue=='different-settings': declared['prewarm']='0'
            elif issue=='semantic-tamper':record['startup_options']['prewarm']='0'
            elif issue=='stamp-only':record['options_sha256']=sha(b'noncanonical')
            elif issue=='variant-identity':record['variant_sha256']=sha(b'unmeasured record')
            elif issue=='foreign-app':members[APP]=sha(b'unmeasured app')
            elif issue=='missing-launcher':del members[LAUNCH]
            elif issue=='extra-file':members[RT]=sha(b'shared runtime')
            else:record['env']={'AIDOKU_STARTUP_PORTRAIT':'native'}
            with self.subTest(issue=issue),self.assertRaises(ValueError):gate.verify_program_final(record,members,declared)

    def test_guard_claim_cannot_inherit_yesterdays_package_or_source(self):
        self.assertEqual(gate.CONTROL_APP, BASE_APP)
        self.assertEqual(gate.CONTROL_ZIP,'bcc625b919936eef55eb496c16cce3738cd8c1dede12c5e82e182514c5b8ee29')
        self.assertEqual(gate.CONTROL_KPM,'1670ba5a1f166cd21bcfd5da9d2364b2d6d9b089e8bb222bf0bbf593df054af8')
        self.assertEqual(gate.CONTROL_SOURCE,'1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768')

    def test_isolated_feature_comparisons_reject_a_second_switched_feature(self):
        target={APP:sha(b'app'),LAUNCH:sha(b'launcher'),HELPER:sha(b'helper')}
        extra={'aidoku-startup-portrait':target[HELPER]}
        modes={'supervisor':('portrait','shell','native'),'prewarm':('prewarm','0','2'),
               'grace':('last_cover_grace','off','on'),'layout':('first_layout','baseline','optimized'),
               'resident':('resident','off','45'),'import':('import_thumbnail','off','idle')}
        for feature,(key,off,on) in modes.items():
            sides={side:variant(target[APP],target[LAUNCH],extra,{key:val})
                   for side,val in (('baseline',off),('candidate',on))}
            with self.subTest(feature=feature):
                gate.verify_isolate(sides,feature,target)
                stray='prewarm' if key!='prewarm' else 'first_layout'
                altered={**sides,'candidate':variant(target[APP],target[LAUNCH],extra,
                         {key:on,stray:'2' if stray=='prewarm' else 'optimized'})}
                with self.assertRaises(ValueError):gate.verify_isolate(altered,feature,target)
                with self.assertRaises(ValueError):gate.verify_isolate({**sides,'baseline':sides['candidate']},feature,target)


@unittest.skipUnless(sys.platform == 'linux', 'Run release verification through ssh test-env')
class SourceProvenance(unittest.TestCase):
    def setUp(self):
        work=tempfile.TemporaryDirectory(prefix='six-feature-build-proof-')
        self.addCleanup(work.cleanup); self.base=Path(work.name); self.src=self.base/'source'
        files=('scripts/verify-kindle-package.py','scripts/package-kindle.py','scripts/package-kpm.py',
               'scripts/build-kindle.sh','scripts/build-startup-portrait.sh','packaging/kindle/launch.sh')
        for directory in ('scripts','packaging/kindle','assets','src','ui','examples','vendor'):
            (self.src/directory).mkdir(parents=True,exist_ok=True)
        for name in files: (self.src/name).write_bytes(('synthetic '+name+'\n').encode('ascii'))
        rel=Path('scripts/verify-kindle-package.py')
        root=next((x for x in Path(__file__).resolve().parents if (x/rel).is_file()),
                  Path('/root/aidoku-startup-delivery-20261118/source'))
        shutil.copyfile(root/rel,self.src/rel)
        self.input=self.src/'src/layout.rs';self.input.write_bytes(b'pub const ROWS: usize = 6;\n')
        self.code={k:sha((self.src/k).read_bytes()) for k in files}
        verifier=gate.module('_six_fixture_verifier',self.src/rel,self.code[str(rel)])
        manifest=verifier.source_manifest(self.src);self.source_id=manifest['sha256']
        self.ids={APP:sha(b'fixture app bytes'),HELPER:sha(b'fixture portrait'),LAUNCH:self.code['packaging/kindle/launch.sh']}
        self.producer=self.base/'compiler-output.json'
        self.build={'input':{'path':'src/layout.rs','sha256':sha(self.input.read_bytes())},'outputs':self.ids.copy()}
        self.producer.write_bytes(canonical(self.build))
        self.data={'baseline_source_revision':'c6867d174792cbcc71dab7f59c52570f8ddd8911',
                   'baseline_source_sha256':'1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768',
                   'source_manifest_sha256':self.source_id,'program_sha256':self.ids,'packaging_sha256':self.code,
                   'evidence':[self.ref(self.producer)]}
        self.receipt=self.base/'build-receipt.json'

    @staticmethod
    def ref(path):return {'uri':path.as_uri(),'sha256':sha(path.read_bytes())}

    def review(self, fields):
        self.receipt.write_bytes(canonical(fields))
        return gate.verify_build(self.src,self.ref(self.receipt),self.ids,self.source_id)

    def test_actual_source_executable_and_packaging_inputs_are_all_bound(self):
        manifest,code=self.review(self.data)
        self.assertEqual((manifest['sha256'],code), (self.source_id,self.code))
        for reason in ('old-lineage','omitted-builder','wrong-manifest','no-preserved-producer','different-elf'):
            fields=copy.deepcopy(self.data)
            if reason=='old-lineage': fields['baseline_source_revision']='78c5b2bc09a469133c886eb0d73aaf9ed63195b2'
            elif reason=='omitted-builder':del fields['packaging_sha256']['scripts/build-startup-portrait.sh']
            elif reason=='wrong-manifest':fields['source_manifest_sha256']=sha(b'old source')
            elif reason=='no-preserved-producer':fields['evidence']=[]
            else:fields['program_sha256'][APP]=sha(b'another ELF')
            with self.subTest(reason=reason),self.assertRaises(ValueError):self.review(fields)

    def test_evidence_from_a_different_build_does_not_authorize_the_same_source(self):
        self.review(self.data)
        for reason in ('unlinked-source','unlinked-helper','altered-bound-record','source-after-selection'):
            fields=copy.deepcopy(self.data); record=copy.deepcopy(self.build)
            old_input=self.input.read_bytes();old_record=self.producer.read_bytes()
            try:
                if reason=='unlinked-source':record['input']['sha256']=sha(b'other source bytes')
                elif reason=='unlinked-helper':del record['outputs'][HELPER]
                elif reason=='altered-bound-record':record['extra']='stale build record'
                else:self.input.write_bytes(old_input.replace(b'6;',b'7;'))
                self.producer.write_bytes(canonical(record))
                if reason!='altered-bound-record':fields['evidence']=[self.ref(self.producer)]
                with self.subTest(reason=reason),self.assertRaises(ValueError):self.review(fields)
            finally:
                self.producer.write_bytes(old_record);self.input.write_bytes(old_input)


@unittest.skipUnless(sys.platform == 'linux', 'Run release verification through ssh test-env')
class ProtectedPackage(unittest.TestCase):
    def setUp(self):
        self.before={APP:(b'old app',0o755),LAUNCH:(b'old launcher',0o755),DOC:(b'accepted icon document',0o755),
                     RT:(b'unmodified registered dispatcher',0o755)}
        self.after={**self.before,APP:(b'new app',0o755),LAUNCH:(b'new launcher',0o755),HELPER:(b'new helper',0o755)}
        self.change={name:(sha(self.before[name][0]) if name in self.before else 'absent',sha(self.after[name][0]))
                     for name in (APP,LAUNCH,HELPER)}

    def test_exact_three_file_delta_preserves_accepted_document_and_shared_runtime_in_both_archives(self):
        for is_kpm in (False,True):
            before = kpm(self.before) if is_kpm else self.before
            gate.archive_diff(before, kpm(self.after) if is_kpm else self.after, self.change, kpm=is_kpm)
            for issue in ('foreign-runtime','library-document','changed-worker-mode','half-shipped','user-data','wrong-tested-binary'):
                altered=self.after.copy()
                if issue=='foreign-runtime': altered[RT]=(b'changed dispatcher',0o755)
                elif issue=='library-document': altered[DOC]=(b'replaced entry',0o755)
                elif issue=='changed-worker-mode': altered[HELPER]=(self.after[HELPER][0],0o644)
                elif issue=='half-shipped': del altered[HELPER]
                elif issue=='user-data': altered['aidoku/data/startup-options']=(b'new override',0o644)
                else: altered[APP]=(b'another app',0o755)
                with self.subTest(kpm=is_kpm,issue=issue),self.assertRaises(ValueError):
                    gate.archive_diff(before,kpm(altered) if is_kpm else altered,self.change,kpm=is_kpm)

    def test_kpm_control_files_and_allowlist_cannot_hide_a_changed_payload(self):
        base=kpm(self.before)
        for leaf in ('manifest.json','install.sh','uninstall.sh','launch.sh','payload.tsv'):
            changed=kpm(self.after)
            changed[leaf]=base[leaf] if leaf=='payload.tsv' else (b'unmeasured control',changed[leaf][1])
            with self.subTest(leaf=leaf),self.assertRaises(ValueError):gate.archive_diff(base,changed,self.change,kpm=True)

    def test_fake_or_retired_installed_bytes_never_authorize_an_update(self):
        for old in (PREVIOUS_APP,COLD_ONLY,self.change[APP][0]):
            changes={**self.change,APP:(old,self.change[APP][1])}
            with self.subTest(old=old[:8]),self.assertRaises(ValueError):gate.verify_allowed_delta(self.before,self.after,changes)


if __name__ == '__main__':
    unittest.main()
