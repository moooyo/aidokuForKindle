#!/usr/bin/env python3
"""Audit the full persistent settings operating together, without a speed claim or device access."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from types import ModuleType


PARENT = '113991a8c5668fb050a0dbb368267a34868b0cf95c4992ba501fdb2c4ee1a323'
CREATOR = 'c931d66e11a0f35365d7c076b4af782b5f51524229cb01a56b48ac9e6654b055'
RUNNER = '7066646be581566ae1cfec59566b0a3b75494c8ced11a063a2ec2a6764f85af9'
DEVICE = 'b8c1b11917ec03f8be66e7327571e35841b24b20e6e00fe8619bcd2bf7621230'
DEPOT = Path('/root/aidoku-all-bench-20261119/all-options')


def load(name, paths, expected):
    for path in paths:
        if path.is_file():
            data=path.read_bytes()
            if sha256(data).hexdigest()!=expected:raise ValueError('Frozen qualification dependency differs')
            mod=ModuleType('_all_settings_'+name);mod.__file__=str(path)
            exec(compile(data,str(path),'exec'),mod.__dict__);return mod
    raise ValueError('Frozen qualification dependency unavailable')


def analyze(path,release_plan,crop=None):
    if path.name=='plan.json':path=path.parent
    creator=load('creator',[Path(__file__).with_name('materialize_all_options.py'),DEPOT/'tools/materialize_all_options.py'],CREATOR)
    measurement=load('measure',[DEPOT/'frozen-analyze-all.py',Path(__file__).resolve().parent.parent/'analyze_all.py'],PARENT)
    ref=measurement.load('warm'); toolbar=measurement.load('toolbar'); roi=measurement.load('pixels')
    original_case,original_batch,original_pixels=ref.audit_case,ref.audit_batch,ref.pixels
    ref.identities=lambda report:measurement.variants(report,ref.require)
    issues=[];seeds=set();arms={}
    def trial_case(out,name,commands):
        row=original_case(out,name,commands); side=name.split('-')[0];d=out/name
        try:
            variant=arms[side]
            seed=measurement.case_configuration(d,variant,'warm');ref.require(seed is not None,'applied_private_settings_or_fixture_mismatch')
            row['options_file_present']=bool(variant['startup_options']);row['applied_options_sha256']=variant['options_sha256']
            if name!='baseline-warmup':seeds.add(seed)
            events,cache=measurement.cache_evidence(d,'warm',ref)
            selection=measurement.app_selection(events,variant,ref)
            ref.require(selection.get('observed') is True and selection.get('values')==(creator.FULL if side=='candidate' else measurement.DEFAULTS),
                        'six_effective_values_not_observed')
            row['application_selection']=selection;row['functional_observations']=measurement.features(events,ref)
            row['cache_evidence']={k:cache[k] for k in ('hits','misses','ready_slots','visible_items')}
            row['toolbar']=toolbar.toolbar_events(d,ref.require,'candidate')
            ref.require(row['diagnostics']['portrait_engine']==('native' if side=='candidate' else 'shell'),
                        'portrait_engine_did_not_match_persistent_selection')
            ref.require(row['functional_observations']['layout']['mode']=='batched','unexpected_library_projection')
            if side=='baseline':
                g=row['functional_observations'];ref.require(not any((g['prewarm']['plan'],g['prewarm']['attempt'],g['grace']['started'])),
                            'optional_path_ran_under_default_off_setting')
            names=[e['name'] for e in events]
            ref.require(names.count('shutdown_started')==names.count('shutdown_complete')==names.count('process_finished')==1 and
                        names.index('shutdown_started')<names.index('shutdown_complete')<names.index('process_finished'),
                        'normal_process_shutdown_not_verified')
            # Automatic ordinary exit does not request parking, importing, or
            # a subsequent reopen. A published keeper here is unexpected.
            ref.require(not any(n in names for n in ('resident_library_keeper_published','resident_library_keeper_reclaimed')),
                        'ordinary_exit_created_or_consumed_quick_return_state')
            row['ordinary_exit_verified']=True
            if name.endswith('-qualify'):
                marks=[s for line in (d/'shell.tsv').read_text('ascii').splitlines() if (s:=line.split()) and s[-1]=='native_capture_started']
                ref.require(len(marks)==1 and len(marks[0])==2 and re.fullmatch(r'\d+\.\d{1,6}',marks[0][0]),'missing_qualification_frame_clock')
                time=int(ref.Decimal(marks[0][0])*1_000_000);end=next(e['boottime_us'] for e in events if e['name']=='startup_visible_covers_settled')
                ref.require(time>=end+10_000,'screenshot_taken_before_covers_settled')
                row['native_snapshot_after_complete_ms']=ref.ms(time-end)
        except (OSError,ValueError,TypeError,KeyError,StopIteration,UnicodeError):row['issues'].append('missing_or_invalid_all_settings_receipt')
        except ref.Invalid as err:row['issues'].append(str(err))
        if row['issues'] and row['status']=='ok':row['status']='failed'
        return row
    def trial_batch(root,mode,primary):
        plan=ref.read_json(root/'plan.json');report=ref.read_json(root/'report.json')
        arms.update(measurement.variants(plan,ref.require));b=original_batch(root,mode,primary)
        release=creator.expected(release_plan);expected={'members':release,'release_plan_sha256':creator.digest(release_plan.read_bytes()),
                   'all_options_plan_sha256':creator.digest((root/'plan.json').read_bytes())}
        if ref.read_json(root/'release-binding.json')!=expected:b['issues'].append('different_release_binding')
        if [(v['application_sha256'],v['launcher_sha256'],v['extra_sha256'],v['native_query_sha256']) for v in arms.values()]!=[
               (release['candidate-arm'],release['launch.sh'],{'aidoku-startup-portrait':release['aidoku-startup-portrait']},creator.QUERY)]*2:
            b['issues'].append('not_same_release_on_both_arms')
        if any(v['env'] or v['args'] for v in arms.values()) or arms['baseline']['startup_options']!={} or arms['candidate']['startup_options']!=creator.FULL:
            b['issues'].append('qualification_not_recommended_versus_all_six_options')
        for obj in (plan,report):
            if (obj.get('mode')!='qualify' or obj.get('trial')!='all-options' or obj.get('qualification_only') is not True or obj.get('performance_claim') is not False or
              obj.get('cases')!=creator.WARMUP or obj.get('baseline_reference_sha256')!=creator.CONTROL or obj.get('fixture_case_seed')!='independent' or
              obj.get('cache_regime')!='warm-cache-v2' or obj.get('fixture_policy')!='sealed-private-cache-v2'):
                b['issues'].append('unexpected_qualification_plan')
        if ((b['runner_sha256'],b['device_script_sha256'])!=(RUNNER,DEVICE) or report.get('completed')!=creator.WARMUP or
            len(seeds)!=1):b['issues'].append('incomplete_or_unknown_fixture_runner')
        raw=(root/'before.stdout').read_bytes().splitlines(); protected=[line for line in raw if line.startswith(b'actual-options ')]
        if len(protected)!=1 or not (protected[0]==b'actual-options absent' or
                 re.fullmatch(rb'actual-options [0-9a-f]{64}  /mnt/us/aidoku/data/startup-options',protected[0])):
            b['issues'].append('original_user_settings_not_accounted_for')
        if protected!=[b'actual-options absent'] and not any(re.fullmatch(rb'actual-options-state [0-9]+:[0-9]+:[0-7]+',line) for line in raw):
            b['issues'].append('missing_original_settings_state')
        b['trial']='all-options'; b['issues']=sorted(set(b['issues']));b['qualified_release_plan_sha256']=expected['release_plan_sha256']
        return b
    def compare(root,cut):
        snap=original_pixels(root,cut)
        try:snap.update(roi.compare(root,snap))
        except ValueError:snap.update(right_status_bar_observable=False,identical_right_status_bar=False)
        if not (snap['right_status_bar_observable'] and snap['identical_right_status_bar']):issues.append('right_toolbar_unverified_or_different')
        return snap
    ref.audit_case,ref.audit_batch,ref.pixels=trial_case,trial_batch,compare
    r=ref.analyze([path],[],'qualify','covers',crop)
    if issues:r['issues']=sorted(set(r['issues']+issues));r['valid']=False;r['qualification_passed']=False
    # A functional capture is not an A/B latency trial. Never issue or carry
    # through quantitative gate fields on this endpoint.
    result={'schema_version':1,'trial':'all-options','phase':'qualify','performance_claim':False,'latency_gate_passed':None,
         'all_options_functional_passed':bool(r['valid'] and r['qualification_passed']),
         'qualified_run_id':r['batches'][0]['run_id'] if len(r['batches'])==1 else None,
         'qualification_only':True,'valid':r['valid'],'qualification_passed':r['qualification_passed'],
         'artifacts':r['artifacts'],'batches':r['batches'],'pixels':r['pixels'],'issues':r['issues'],'input_failures':r['input_failures'],
         'safety_scope':'The same release programs are used by both private copies. Qualifies all six selected settings on entry and ordinary exit; does not test actual idle import, keeper reacquisition or speedups.',
         'dependencies_sha256':{'measurement':PARENT,'creator':CREATOR,'runner':RUNNER,'device':DEVICE},
         'analyzer_sha256':sha256(Path(__file__).read_bytes()).hexdigest()}
    return result


def main():
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);p.add_argument('--release-plan',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--status-bar-pixels',type=int);a=p.parse_args()
    try:out=analyze(a.batch,a.release_plan,a.status_bar_pixels)
    except (OSError,KeyError,ValueError,TypeError):p.exit(1,'All-options evidence inputs failed their frozen checks.\n')
    a.output.parent.mkdir(exist_ok=True,parents=True);a.output.write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:out[k] for k in ('all_options_functional_passed','qualified_run_id','issues','input_failures')}))
    return 0 if out['all_options_functional_passed'] else 2


if __name__=='__main__':sys.exit(main())
