#!/usr/bin/env python3
"""Audit fresh, matched warm/cold observations for one production-default application on test-env."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from types import ModuleType


WARM = 'f796f2c20c75f183996d63612c19dad7a4a8aa47e7ce0f1d0995124509bdc8c7'
COLD = '2d9a5d376691af829f85a936d428d9f498ae1b63b3d5423229374f5a7e87a839'
PIXELS = '6958232ab783a32ceb822c7ba13d083ec2470bb4bc13e3af675e352e0c703a20'
WARM_RUNNER = '85d60c15b871375477cd14ef2020f0cc6adb7119ef618a010bec6ed934d97f7c'
WARM_DEVICE = '3126a4c5ceffb3836218f9f9ccb9dab2b111c67c56a0d380c98a8167379da14d'
CONTROL = '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
PREVIOUS = {CONTROL, 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6',
            'c954eff22cc43bfc2cd27236d6fb0e9e5d8490d6322bd49763c0f4b7fb5acda2',
            '153e018635461d00a6b0378a391f76adf929178fd75b787b2088e9753d3fcd3f'}
MECHANISM = 'cold_decode_with_fresh_toolbar_status'
PREFIX = 'startup_toolbar_'
CORE = ('probe_started', 'probe_ready', 'probe_unavailable', 'snapshot_used', 'sync_fallback')
QUERY_NAMES = ('native_query_session_ready', 'native_query_response', 'native_query_session_finished')
PHASES = {'pilot', 'screen', 'confirm', 'qualify'}


def load(name, sha, sibling, remote):
    for path in (Path(__file__).resolve().parents[1] / sibling / name, Path(remote) / name):
        if path.is_file():
            source = path.read_bytes()
            if sha256(source).hexdigest() != sha:
                raise ValueError('A reviewed prerequisite analyzer has changed')
            mod = ModuleType('_status_bench_' + path.stem);mod.__file__=str(path)
            exec(compile(source, str(path), 'exec'), mod.__dict__)
            return mod
    raise ValueError('A reviewed prerequisite analyzer is missing')


def toolbar_events(directory, require, side):
    try:
        events = json.loads((directory / 'application.json').read_bytes())['events']
    except (OSError, ValueError, UnicodeError, KeyError, TypeError):
        require(False, 'missing_toolbar_trace')
    require(isinstance(events, list) and all(isinstance(e, dict) and isinstance(e.get('name'), str) and
                                             type(e.get('at_us')) is int and e['at_us'] >= 0 for e in events), 'invalid_toolbar_events')
    require(all(a['at_us'] <= b['at_us'] for a,b in zip(events,events[1:])), 'invalid_toolbar_clock_order')
    def named(n):return [e for e in events if e['name']==n]
    def once(n):
        seen=named(n);require(len(seen)==1,'missing_or_duplicate_toolbar_boundary');return seen[0]
    def ms(x):return round(x/1000,3)
    def queries(begin,end):return [e['name'] for e in events if begin<=e['at_us']<=end and e['name'] in QUERY_NAMES]
    sent, initial = (once(n) for n in ('startup_initial_frame_submitted','startup_initial_input_ready'))
    require(sent.get('full') is True and initial.get('full') is True and type(sent.get('marker')) is int
            and sent['marker']>0 and sent['marker']==initial.get('marker') and
            type(sent.get('frame_sequence')) is int and sent['frame_sequence']==initial.get('frame_sequence'),
            'uncorrelated_initial_display_checkpoint')
    opened,closed = (once(n)['at_us'] for n in ('startup_view_started','startup_view_attached'))
    first,settled = (once(n)['at_us'] for n in ('startup_library_frame_submitted','startup_visible_covers_settled'))
    require(sent['at_us'] <= initial['at_us'] <= opened <= closed <= first <= settled, 'inconsistent_toolbar_view_boundaries')
    marks={key:[e['at_us'] for e in named(PREFIX+key)] for key in CORE}
    require(all(len(times)<=1 for times in marks.values()) and not any(e['name'].startswith(PREFIX) and
            e['name'][len(PREFIX):] not in CORE for e in events), 'unknown_or_duplicate_toolbar_marker')
    begin,ready,unavailable,used,fallback=(marks[key] for key in CORE)
    require(not (used and fallback) and len(ready)+len(unavailable)==len(begin), 'ambiguous_toolbar_outcome')
    for mark in used+fallback:
        require(opened<=mark<=closed, 'toolbar_disposition_outside_first_view')
    sync=queries(opened,closed); tail=queries(first,settled); gap=queries(closed,first)
    took=None; probe=[]
    if begin:
        end=(ready or unavailable)[0]
        require(sent['at_us']<=begin[0]<=end<=initial['at_us'], 'toolbar_probe_outside_initial_checkpoint')
        probe=queries(begin[0],end);took=ms(end-begin[0])
        if ready:
            require(probe==[QUERY_NAMES[0],QUERY_NAMES[1],QUERY_NAMES[1],QUERY_NAMES[2]], 'toolbar_probe_query_evidence_mismatch')
    if side=='baseline':
        require(not any(marks.values()), 'toolbar_candidate_executed_in_control')
        cls='baseline_sync' if sync else 'baseline_unverified'
    elif used:
        require(ready and not sync and not gap and not tail, 'toolbar_hit_contains_missing_or_deferred_sync_read')
        cls='hit'
    elif fallback:
        cls='fallback'
    else:
        cls='candidate_unverified'
    return {'classification':cls,'attempts':len(begin),'ready':len(ready),'unavailable':len(unavailable),
            'used':len(used),'fallbacks':len(fallback),'sync_absent_on_hit':cls=='hit' and not sync,
            'first_view_query_sessions':sync.count(QUERY_NAMES[0]),'first_view_query_responses':sync.count(QUERY_NAMES[1]),
            'first_view_query_finishes':sync.count(QUERY_NAMES[2]),'native_queries_after_first_before_covers':len(tail),
            'native_queries_after_view_before_first':len(gap),'no_query_deferral_to_cover_tail':not tail and not gap,
            'probe_query_sessions':probe.count(QUERY_NAMES[0]),'probe_query_responses':probe.count(QUERY_NAMES[1]),
            'probe_query_finishes':probe.count(QUERY_NAMES[2]), 'probe_wall_ms':took,
            'first_view_ms':ms(closed-opened),'initial_send_to_input_ms':ms(initial['at_us']-sent['at_us']),
            'initial_send_to_probe_ms':ms(begin[0]-sent['at_us']) if begin else None,
            'probe_finish_to_initial_input_ms':ms(initial['at_us']-(ready or unavailable)[0]) if begin else None}


def status_mechanism(batches, stats):
    roles={}
    for role,select in [('timing',lambda n:n.split('-')[-1].isdigit()),('pixel',lambda n:n.endswith('-qualify')),
                        ('warmup',lambda n:n.endswith('-warmup'))]:
        by_side={}
        for side in ('baseline','candidate'):
            all_rows=[r for b in batches for r in b['cases'] if r['case'].startswith(side+'-') and select(r['case'])]
            rows=[r['toolbar'] for r in all_rows if r.get('toolbar')]
            counts=Counter(r['classification'] for r in rows)
            by_side[side]={'observations':len(all_rows),'attempted':sum(r['attempts'] for r in rows),
                           'hits':counts['hit'],'fallbacks':counts['fallback'],'baseline_sync':counts['baseline_sync'],
                           'unverified':counts['candidate_unverified']+counts['baseline_unverified']+len(all_rows)-len(rows),
                           'probe_wall_ms':stats([r['probe_wall_ms'] for r in rows if r['probe_wall_ms'] is not None]),
                           'first_view_ms':stats([r['first_view_ms'] for r in rows]),
                           'initial_send_to_input_ms':stats([r['initial_send_to_input_ms'] for r in rows]),
                           'view_queries_on_hits':sum(r['first_view_query_responses'] for r in rows if r['classification']=='hit'),
                           'deferred_query_events_on_hits':sum(r['native_queries_after_first_before_covers']+
                                            r['native_queries_after_view_before_first'] for r in rows if r['classification']=='hit')}
        roles[role]=by_side
    targets=[roles[k] for k in ('timing','pixel') if roles[k]['candidate']['observations']]
    verified=bool(targets and all(g['candidate']['hits']>0 and g['baseline']['baseline_sync']>0 and not
                                   g['candidate']['unverified'] for g in targets))
    return dict(name='consume_fresh_toolbar_status_in_first_library_view',verified=verified,**roles)


def analyze(paths, qualification, workload, phase, candidate_sha256, crop=None):
    if workload not in ('warm','cold') or phase not in PHASES or (workload=='warm' and phase=='confirm'):
        raise ValueError('Unplanned workload or stage')
    if not isinstance(candidate_sha256,str) or not re.fullmatch(r'[0-9a-f]{64}', candidate_sha256) or candidate_sha256 in PREVIOUS:
        raise ValueError('A new frozen application identity is required')
    ref=load('analyze_bench.py',WARM,'tools','/root/aidoku-next-bench-20261118/tools')
    old_case,old_batch=ref.audit_case,ref.audit_batch
    original_pixels=ref.pixels
    def qualified_pixels(path,crop_px):
        below=original_pixels(path,crop_px)
        right=load('toolbar_pixels.py',PIXELS,'status-bench','/root/aidoku-next-statusbench-20261118/tools')
        try:
            below.update(right.compare(path,below))
        except ValueError:
            # With missing evidence even a matching blank rectangle cannot
            # establish equality of the rendered Wi-Fi and battery indicators.
            below.update(right_status_bar_observable=False,identical_right_status_bar=False,
                         right_status_bar_issue='unverified_right_toolbar_capture')
        return below
    ref.pixels=qualified_pixels

    def app_case(out,name,commands):
        r=old_case(out,name,commands)
        try:
            r['toolbar']=toolbar_events(out/name,ref.require,name.split('-')[0])
            if workload=='warm' and (name.split('-')[-1].isdigit() or name.endswith('-qualify')):
                d=r.get('diagnostics',{})
                ref.require((d.get('cover_slots'),d.get('unique_visible_items'),d.get('reported_local_cache_hits'),
                             d.get('reported_local_cache_misses'),d.get('reported_cover_jobs'))==(7,6,6,0,6),
                            'warm_guard_regime_mismatch')
                if name.endswith('-qualify'):
                    times=r.get('endpoints_ms',{})
                    ref.require('covers' in times and 'ready' in times and times['covers']-times['ready']<5980,
                                'warm_pixels_not_proven_settled')
        except ref.Invalid as err:
            r['issues'].append(str(err))
            if r['status']=='ok':r['status']='failed'
        return r

    def app_batch(path,mode,primary):
        b=old_batch(path,mode,primary);plan=ref.read_json(path/'plan.json')
        for side,v in plan['variants'].items():
            if (v['application_sha256'] != (CONTROL if side=='baseline' else candidate_sha256) or
                v['launcher_sha256']!=LAUNCH or v['native_query_sha256']!=QUERY or
                v.get('env')!={} or v.get('args')!=[] or v.get('extra_sha256')!={}):
                b['issues'].append('wrong_composite_or_control_artifact_or_switches')
        if workload=='warm' and ('cache_regime' in plan or 'fixture_policy' in plan or
             b['runner_sha256']!=WARM_RUNNER or b['device_script_sha256']!=WARM_DEVICE):
            b['issues'].append('unrecognized_warm_guard_fixture')
        b['issues']=sorted(set(b['issues']))
        return b

    ref.audit_case,ref.audit_batch=app_case,app_batch
    if workload=='cold':
        base=load('analyze_cold.py',COLD,'cold-bench','/root/aidoku-next-coldbench-20261118/tools')
        base.load_reference=lambda:ref
        result=base.analyze(paths,qualification,phase,crop)
        result['cold_analyzer_sha256']=COLD
        cold_mechanism=result['mechanism']
    else:
        result=ref.analyze(paths,qualification,phase,'covers',crop)
        cold_mechanism={'name':'cold_local_cover_decode_validation','verified':False,'applicable':False}
        result.update(cache_regime='warm-local-derivatives-v1',fixture_policy='six-local-covers-warm-v1',warm_analyzer_sha256=WARM)
    for row in result['pixels']:
        if not (row.get('right_status_bar_observable') is True and row.get('identical_right_status_bar') is True):
            result['issues']=sorted(set(result['issues']) | {'right_status_bar_unverified_or_different'})
            result.update(valid=False,qualification_passed=False,latency_gate_passed=None)
    result['right_status_bar_analyzer_sha256']=PIXELS
    validity=result['valid']; status=status_mechanism(result['batches'],ref.stats)
    status['verified']=status['verified'] and validity
    def combined(s,c):
        return {'name':MECHANISM,'verified':bool(validity and s['verified'] and (c['verified'] or workload=='warm')),
                'toolbar_verified':s['verified'],'cold_decode_verified':c['verified'],'workload':workload}
    for b in result['batches']:
        local_status=status_mechanism([b],ref.stats)
        local_status['verified']=bool(local_status['verified'] and not b['issues'])
        local_cold=base.mechanism([b]) if workload=='cold' else dict(cold_mechanism)
        local_cold['verified']=bool(local_cold['verified'] and not b['issues'])
        b.update(workload=workload,cache_regime=result['cache_regime'],fixture_policy=result['fixture_policy'],
                 status_mechanism=local_status,cold_mechanism=local_cold,mechanism=combined(local_status,local_cold))
    warm_pass=None
    if workload=='warm':
        result.update(latency_gate_passed=None,gate=None)
        if phase=='screen' and validity:
            warm_pass=all(result['comparisons_ms'][key]['paired_savings_ms']['median']>=-50 for key in ('usable','covers'))
        result['warm_guard']={'required_pairs':6,'minimum_paired_median_savings_ms':{'usable':-50,'covers':-50},
                              'method':'Descriptive paired engineering guard; a pass does not establish a warm speedup or statistical noninferiority.'}
    result.update(workload=workload,warm_guard_passed=warm_pass,expected_candidate_application_sha256=candidate_sha256,
                  analyzer_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  cold_mechanism=cold_mechanism,status_mechanism=status,mechanism=combined(status,cold_mechanism))
    result['scope_note']=('Fixed installed A and a distinct default-mode B; no observations from the prior cold-only application. '
                          'The early native property read is synchronous in the initial-SEND checkpoint before the hardware wait; '
                          'its time is included in startup. Hits require the first view to consume a new result with no native query '
                          'in that view or between it and the complete cover frame. Fallbacks and zero-hit samples stay in pairs. '
                          'Optical completion and post-reboot timings are not measured.')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,action='append',required=True);p.add_argument('--qualification-batch',type=Path,action='append',default=[])
    p.add_argument('--candidate-sha256',required=True);p.add_argument('--phase',choices=sorted(PHASES),required=True)
    p.add_argument('--workload',choices=('warm','cold'),required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--status-bar-pixels',type=int);a=p.parse_args()
    if sys.platform!='linux':p.error('Run only on test-env')
    try:out=analyze(a.batch,a.qualification_batch,a.workload,a.phase,a.candidate_sha256,a.status_bar_pixels)
    except ValueError as error:p.error(str(error))
    a.output.parent.mkdir(exist_ok=True,parents=True);a.output.write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:out[k] for k in ('phase','workload','complete_pairs','valid','warm_guard_passed',
                                      'latency_gate_passed','qualification_passed','issues','input_failures')}))
    return 0 if out['valid'] else 2


if __name__=='__main__':sys.exit(main())
