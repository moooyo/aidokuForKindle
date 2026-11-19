#!/usr/bin/env python3
"""Compact the current trial's public analyzer reports, without contacting any device."""

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import median


CONTROL = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
CANDIDATE = '3ef03a4124970139fe46d3873c4b9fc7d5cd25af55e66482e497dca46b584375'
R2 = '1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
REVISIONS = {CANDIDATE:'r1', R2:'r2'}
LAUNCHERS = ('10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296',
             '2b0d6e8ee76076c1f14e92dae97a505b40f0a78e07cec78cdea23ec59398bae5')
SIDES = ('baseline', 'candidate')
ENDPOINTS = ('usable', 'covers', 'exit')
FEATURES = ('defaults-warm', 'defaults-cold', 'default-lifecycle', 'all-options-functional', 'supervisor', 'prewarm', 'grace',
            'layout', 'import', 'quick-return-keeper', 'retained-gui-reentry', 'installed-entry')
PHASES = ('pilot', 'screen', 'confirm', 'qualify')
SOURCE_METRICS = {'request_to_launcher_bridge','launcher_entry_to_worker','observer_start_to_ready','portrait_outer',
                  'display_takeover','initial_send_to_input','first_library_view','first_page_projection','first_render',
                  'first_to_all_covers','serial_visible_cover_jobs','complete_cover_render','complete_cover_prior_buffer_settle'}
MECHANISM = {'planned','observed','toolbar_hits','toolbar_fallbacks','cold_source_validated_jobs','prewarm_attempted_runs',
             'prewarm_usable_runs','prewarm_ready','prewarm_zero_attempt_runs','prewarm_plans','grace_started_runs',
             'grace_completed_single_frame_runs','grace_expired','grace_cancelled','intermediate_cover_frames'}
IMPORT_METRICS = ('ingest_publish_ms','ingest_request_to_visible_ms','ingest_request_to_all_work_ms','extra_tail_ms',
                  'worker_elapsed_ms','ingest_cached_bytes','ingest_cache_count','ingest_exit_ms','reopen_usable_ms',
                  'reopen_covers_ms','reopen_exit_ms','two_visible_waits_ms','two_including_deferred_ms')
KEEPER_METRICS = ('prime_usable','prime_covers','prime_exit','second_usable','second_covers','second_exit','home_gap',
                  'both_active_until_covers','both_exit_cost','first_request_to_second_home','total_resource_observation')
GUARDS = ('success','lease_released','same_boot','native_restored','entry_restored','installed_and_user_unchanged',
          'input_roster_unchanged')
LIFE_CHECKS = ('catalog_identity','input','lease_released','native_restored','no_keeper_after_ordinary_exit',
               'notification','ordinary_exit','pixels','production_defaults','right_status_bar','same_process_sleep_wake')
OPTIONS = {'portrait':{'shell','native'},'prewarm':{'0','2'},'last_cover_grace':{'off','on'},
           'first_layout':{'baseline','optimized'},'import_thumbnail':{'off','idle'},'resident':{'off','45'}}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def run_id(value):
    require(isinstance(value, str) and re.fullmatch(r'aidoku-all-[a-z][a-z0-9-]{0,30}-[0-9a-f]{8}',value), 'invalid public run identifier')
    return value


def number(value):
    require(type(value) in (float,int) and math.isfinite(value), 'invalid measured value')
    return value


def count(value):
    require(type(value) is int and value>=0, 'invalid mechanism count')
    return value


def rounded(n):
    return round(n,3)+0.0


def triple(a,b):
    require(number(a)>=0 and number(b)>=0, 'negative absolute measurement')
    return [a,b,rounded(a-b)]


def summary(rows, keys):
    valid=[r for r in rows if r.get('complete',True)]
    result={}
    for k in keys:
        if not valid: continue
        a,b,d=zip(*(r[k] for r in valid))
        result[k]=dict(a_median=rounded(median(a)),b_median=rounded(median(b)),difference_of_medians=rounded(median(a)-median(b)),
            median_paired_saving=rounded(median(d)),positive_pairs=sum(t>0 for t in d),range_of_paired_savings=[min(d),max(d)],
            by_order={o:rounded(median(values)) for o in ('AB','BA') if (values:=[r[k][2] for r in valid if r['order']==o])})
    return result


def options(mapping):
    require(isinstance(mapping,dict) and all(k in OPTIONS and v in OPTIONS[k] for k,v in mapping.items()),'unknown startup option')
    return mapping


def arms(artifacts, trial, valid=True, candidate=None):
    candidate = candidate or artifacts['candidate']['application_sha256']
    require(candidate in REVISIONS,'unsealed integration candidate')
    out={}
    for side in SIDES:
        spec=artifacts[side]
        require(spec['application_sha256'] in (CONTROL,candidate) and spec['launcher_sha256'] in LAUNCHERS,'different reviewed binary or launcher')
        out[side] = {'elf':spec['application_sha256'], 'launcher':spec['launcher_sha256'], 'options':options(spec.get('startup_options',{}))}
    require(out['candidate']['elf']==candidate,'report incorrectly names the candidate executable')
    if valid:
        require(out['baseline']['elf']==(CONTROL if trial=='combined' else candidate),
                'does not compare the declared installed control or the isolated integrated executable')
    return out


def mechanism(report, phase):
    target=report.get('mechanism',{}).get('pixels' if phase=='qualify' else 'timing',{})
    out={}
    for side in SIDES:
        m=target.get(side,{})
        out[side]={k:count(m[k]) for k in sorted(MECHANISM & m.keys())}
        for key,allowed in [('layouts',{'unmarked','rowwise','batched'}),('portrait_engines',{'unmarked','native','shell'})]:
            if key in m:
                require(set(m[key])<=allowed,'unrecognized selection mode');out[side][key]={k:count(v) for k,v in m[key].items()}
    return out


def regular(d):
    candidate=d['expected_candidate_application_sha256']
    require(d['baseline_reference_sha256']==CONTROL and candidate in REVISIONS,'foreign trial identity')
    t,w,phase=d['trial'],d['workload'],d['phase']
    require(t in ('combined','supervisor','prewarm','grace','layout') and w in ('warm','cold') and phase in PHASES,'unknown trial classification')
    good=d['valid'] is True and not d['issues'] and not d['input_failures']
    record=dict(group='defaults-'+w if t=='combined' else t, workload=w, phase=phase, valid=good,
        revision=REVISIONS[candidate], arms=arms(d['artifacts'], t, good, candidate), mechanism_observed=d['mechanism']['verified'] is True,
        gates={'protection':d['default_guard_passed'],'latency':d['latency_gate_passed'],
               'pixels':d['qualification_passed'] if d['pixels'] else None},
        status=d['performance_conclusion'] if good else 'invalid_observations',
        mechanism=mechanism(d,phase), batches=[], pair_data=[])
    require(record['status'] in {'qualification_only','pilot_only','default_guard_passed','default_guard_failed','mechanism_not_observed',
        'latency_gate_not_met','independent_confirmation_passed','screen_eligible_for_independent_confirmation','invalid_observations'},'unknown result class')
    if not good:
        record['reason'] = 'frozen_plan_mismatch' if 'frozen_plan_mismatch' in d['issues'] else 'analyzer_incomplete_or_failed'
    for batch in d['batches']:
        rid=run_id(batch['run_id']);cases={r['case']:r for r in batch['cases']}
        record['batches'].append({'run':rid,'mode':batch['mode'],'guards':{k:batch['batch_guards'].get(k) is True for k in GUARDS}})
        if batch['mode']!=phase or phase=='qualify':continue
        for p in batch['pairs']:
            n=count(p['round']);require(p['batch']==rid and n>0 and p['order'] in ('AB','BA'),'invalid pair identity')
            a,b=(cases[s+'-%02d'%n] for s in SIDES)
            row={'run':rid,'round':n,'order':p['order'],'complete':p['complete'] is True}
            if row['complete']:
                require(a['status']==b['status']=='ok','pair with failed case')
                for k in ENDPOINTS:
                    row[k]=triple(a['endpoints_ms'][k],b['endpoints_ms'][k])
                    require(abs(row[k][2]-number(p['savings_ms'][k]))<0.002,'pair differs from source analyzer')
                row['intermediate_frames']=[count(r['features']['intermediate_cover_frames']) for r in (a,b)]
                if t=='combined':
                    jobs=[r['cache']['jobs'] for r in (a,b)]
                    require(all([j['ordinal'] for j in js]==list(range(1,7)) for js in jobs),'different six-cover fixture')
                    row['cover_job_ms_by_ordinal']=[triple(x['duration_ms'],y['duration_ms']) for x,y in zip(*jobs)]
                    row['cover_tail_ms']=triple(*(r['diagnostics']['cover_tail_ms'] for r in (a,b)))
                    row['serial_cover_jobs_ms']=triple(*(sum(j['duration_ms'] for j in js) for js in jobs))
            record['pair_data'].append(row)
    record['summary_ms']=summary(record['pair_data'],ENDPOINTS)
    if good:
        for k,r in record['summary_ms'].items():
            o=d['comparisons_ms'][k]; require(abs(r['median_paired_saving']-number(o['paired_savings_ms']['median']))<=0.002
                and r['positive_pairs']==o['improved_pairs'],'aggregate differs from source analyzer')
        if phase in ('screen','confirm'):
            n=6 if phase=='screen' else 10;wins=5 if n==6 else 9; rows=record['pair_data'];s=record['summary_ms']
            require(len(rows)==n and all(r['complete'] for r in rows) and sorted(r['round'] for r in rows)==list(range(1,n+1)) and
                    Counter(r['order'] for r in rows)=={'AB':n//2,'BA':n//2},'incomplete predefined phase')
            if t=='combined':
                require(all(s[k]['median_paired_saving']>=-50 for k in ('usable','covers')) is d['default_guard_passed'],'default gate differs')
            else:
                primary='usable' if t in ('supervisor','layout') else 'covers';other='covers' if primary=='usable' else 'usable'
                passed=s[primary]['positive_pairs']>=wins and s[primary]['median_paired_saving']>=100 and s[other]['median_paired_saving']>=-50
                require(passed is d['latency_gate_passed'],'isolated latency gate differs')
    full=[r for r in record['pair_data'] if r['complete']]
    if good and t=='prewarm' and full:
        observations=[r for b in d['batches'] if b['mode']==phase for r in b['cases'] if r['case'][-2:].isdigit() and r['status']=='ok']
        fields=('plan','attempt','hit','stopped','store_busy','open_failed','selection_failed','ineligible','no_selection','filtered',
                'image_unavailable','image_failed','consumed','verified_hits_before_input','verified_consume_hits')
        record['prewarm_admission_counts']={s:{k:sum(count(r['features']['prewarm'][k]) for r in observations if r['case'].startswith(s+'-')) for k in fields} for s in SIDES}
    if good and t=='combined' and full:
        record['cover_cost']={'serial_job_and_tail_ms':summary(full,('serial_cover_jobs_ms','cover_tail_ms')),
            'job_elapsed_over_tail_by_side':{s:round(sum(r['serial_cover_jobs_ms'][i] for r in full)/sum(r['cover_tail_ms'][i] for r in full),4) for i,s in enumerate(SIDES)},
            'candidate_job_medians_by_ordinal_ms':[rounded(median(r['cover_job_ms_by_ordinal'][i][1] for r in full)) for i in range(6)],
            'scope':'Elapsed serial worker intervals within the cover tail include I/O, scheduling, integrity checks, decode and persistence as applicable. They are not CPU or decode-only cost and are not additive savings.'}
    record['phase_ms']={k:[number(r['baseline']['median']),number(r['candidate']['median']),number(r['paired_savings']['median'])]
                        for k,r in d.get('phase_comparisons_ms',{}).items() if k in SOURCE_METRICS}
    return record


def lifecycle(d):
    candidate=d['program_sha256']['aidoku/app/aidoku']
    rr=run_id(d['run_id']); require(d['baseline_sha256']==CONTROL and candidate in REVISIONS,'different lifecycle reference')
    defaults={'first_layout':'optimized','import_thumbnail':'off' if candidate==R2 else 'idle','last_cover_grace':'off','portrait':'shell','prewarm':'0','resident':'off'}
    require(d['resolved_options']==defaults, 'different default policy for identified ELF')
    checks={k:d['checks'].get(k) is True for k in LIFE_CHECKS}
    return dict(group='default-lifecycle',revision=REVISIONS[candidate],phase='qualify',valid=all(checks.values()),batches=[{'run':rr}],
        status='qualified' if all(checks.values()) else 'not_qualified',checks=checks,
        normal_defaults=options(d['resolved_options']),frame_captures=count(d['physical_native_captures']),
        scope='Original and final default program under the indexed item, isolated homes, four physical frame captures and short same-process powerd wake. Ordinary exit left no keeper. Not performance, a physical tap, an original-GUI Home/reopen, or an installed-entry smoke.')


def all_options(d):
    rid=run_id(d['qualified_run_id']); require(d['trial']=='all-options' and d['phase']=='qualify' and
        d['qualification_only'] is True and d['performance_claim'] is False, 'unexpected combined functional result')
    require(len(d['batches'])==1 and d['batches'][0]['run_id']==rid, 'unbound joint setting observations')
    opts=arms(d['artifacts'],'all-options'); observed=[];b=d['batches'][0]
    for r in b['cases']:
        if not r['case'].endswith('-qualify'):continue
        side=r['case'].split('-')[0];require(side in SIDES and side not in [item['side'] for item in observed],'duplicate joint option observation')
        data=r['functional_observations'];engine=r['diagnostics']['portrait_engine']
        require(engine in ('shell','native','unmarked'),'unknown observed portrait engine')
        observed.append(dict(side=side,selection=options(r['application_selection']['values']),portrait_execution=engine,
            recorded=r['application_selection']['observed'] is True,ordinary_exit=r['ordinary_exit_verified'] is True,
            attempted_prewarm=count(data['prewarm']['attempt']),consumed_prewarm=count(data['prewarm']['verified_consume_hits']),
            grace_started=count(data['grace']['started']),intermediate_cover_frames=count(data['intermediate_cover_frames'])))
    good=d['all_options_functional_passed'] is True and d['valid'] is True and d['qualification_passed'] is True
    checks={k:b['batch_guards'].get(k) is True for k in GUARDS}
    if good:
        require(len(observed)==2 and all(r['recorded'] and r['ordinary_exit'] for r in observed) and all(checks.values()) and
            len(d['pixels'])==1 and d['pixels'][0]['batch']==rid, 'incomplete joint option observations')
    return dict(group='all-options-functional',revision=REVISIONS[opts['candidate']['elf']],phase='qualify',valid=good,batches=[{'run':rid,'guards':checks}],
        status='qualified_without_speed_claim' if good else 'not_qualified',arms=opts,observed=observed,
        matched_body=bool(d['pixels']) and all(p['identical_below_status_bar'] is True for p in d['pixels']),
        matched_right_status_bar=bool(d['pixels']) and all(p['right_status_bar_observable'] is True and p['identical_right_status_bar'] is True for p in d['pixels']),
        scope='Same integrated release on both sides. Proves joint setting selection and compatible normal entry/exit. Not import economics, actual keeper reacquisition or same-GUI return, and not a latency comparison.')


def imported(d):
    phase=d['phase'];require(phase in PHASES,'unknown import phase')
    good=d['valid'] is True and not d['input_errors']; ids=[run_id(r) for r in d['physical_batches']]
    require(len(ids)==len(set(ids)),'repeated import batch')
    require(d.get('artifacts') and d['artifacts']['candidate']['application_sha256'] in REVISIONS, 'unbound import record; retain a separate stopped receipt')
    r=dict(group='import',phase=phase,revision=REVISIONS[d['artifacts']['candidate']['application_sha256']],valid=good,
        batches=[{'run':a,'mode':'qualify' if 'baseline-qualify-ingest' in d['cases'][a] else phase} for a in ids],pair_data=[],
        status='invalid_observations' if not good else 'pilot_only' if phase=='pilot' else
        'default_economics_passed' if d['default_gate_passed'] is True else
        'reopen_timing_gate_passed' if d['latency_gate_passed'] is True else 'import_gate_not_met',
        arms=arms(d['artifacts'],'import',good) if d['artifacts'] else {},
        mechanism_observed=d['mechanism']['verified'] is True,
        gates={'reopen_latency':d['latency_gate_passed'] if phase!='pilot' else None,
               'default_economics':d['default_gate_passed'] if phase=='confirm' and d['qualification_passed'] else None,
               'pixels':d['qualification_passed'] if any('baseline-qualify-ingest' in rows for rows in d['cases'].values()) else None},
        scope='Synthetic local shelf. Two distinct processes: visible import publication, optional idle derivative preparation, then a later shelf open. Extra time, stored bytes and subsequent cache hits are separate from the user-visible timing. No GUI process survives, no per-user frequency or energy estimate.')
    if r['arms']:
        require(r['arms']['baseline']['options']=={'import_thumbnail':'off'} and r['arms']['candidate']['options']=={'import_thumbnail':'idle'},
                'different import comparison')
    for pair in d['pairs']:
        n=count(pair['index']);require(pair['order'] in ('AB','BA') and n>0,'unrecognized import pair')
        batches=[(k,v) for k,v in d['cases'].items() if f'baseline-{n:02}-ingest' in v];require(len(batches)==1,'ambiguous import pair')
        rid,case=batches[0];require(rid in ids,'unknown import pair identity')
        row=dict(round=n,run=rid,order=pair['order'],complete=True)
        for key in IMPORT_METRICS:
            a,b=(pair['ms_and_bytes'][s][key] for s in SIDES);row[key]=triple(a,b)
            require(abs(row[key][2]-number(pair['savings'][key]))<0.002,'import source disagreement')
        row['reopen_derivative_hits']=[count(case[f'{s}-{n:02}-reopen']['metrics']['reused']) for s in SIDES]
        row['reopen_derivative_misses']=[count(case[f'{s}-{n:02}-reopen']['metrics']['created']) for s in SIDES]
        row['idle_jobs']=[count(case[f'{s}-{n:02}-ingest']['metrics']['worker_jobs']) for s in SIDES]
        r['pair_data'].append(row)
    r['summary_units_in_metric_names']=summary(r['pair_data'],IMPORT_METRICS)
    if good:
        require(len(r['pair_data'])==d['complete_pairs'],'import pair count disagreement')
        for k,m in r['summary_units_in_metric_names'].items():
            require(abs(m['median_paired_saving']-number(d['summary'][k]['paired_savings_median']))<=0.002,'independent import medians differ')
        if phase in ('screen','confirm'):
            target=6 if phase=='screen' else 10
            require(len(r['pair_data'])==target and sorted(i['round'] for i in r['pair_data'])==list(range(1,target+1)) and
                    Counter(i['order'] for i in r['pair_data'])=={'AB':target//2,'BA':target//2},'incomplete import phase')
            sm=r['summary_units_in_metric_names'];primary=sm['reopen_covers_ms']
            positive=(primary['median_paired_saving']>=100 and primary['positive_pairs']>=target-1 and sm['reopen_usable_ms']['median_paired_saving']>=-50)
            require(positive is d['latency_gate_passed'],'import gate differs')
    return r


def gui_admission(d):
    flags=('canonical_lineage_observed','completed','lease_released','native_state_restored','lighting_restored','shared_entry_preserved')
    outcome={k:d['outcome'][k] for k in flags};require(all(type(v) is bool for v in outcome.values()),'malformed lineage result')
    visits=d['visits'];require(len(visits)==2 and type(d['distinct_waiters']) is bool,'unexpected visit count')
    checks=[v[t] for v in visits for t in ('admitted','before_exit')]
    count_keys=('waiter_exact','worker_exact','manager_known','scriptlet_active','canonical_su','handler_exact','su_handler_manager_direct','unexpected_process')
    require(all(type(row[k]) is bool for row in checks for k in count_keys),'malformed visit-role observation')
    return dict(group='retained-gui-reentry',revision='installed-reference',phase='qualification',valid=all(outcome[k] for k in flags if k!='canonical_lineage_observed'),
        batches=[],status='entry_proof_unavailable_no_reuse' if not outcome['canonical_lineage_observed'] else 'entry_observed_not_gui_timing',
        ordinary_opens=len(visits),observation_count=len(checks),count_per_role={k:sum(v[k] for v in checks) for k in count_keys},
        different_waiters=d['distinct_waiters'],operation_checks=outcome,gui_reentry_executed=False,speedup_measured=False,
        scope='Installed indexed entry, admission and ordinary exit. A missing proof at observation time does not establish that the handler never ran. Neither these opens nor the separate data-only keeper is an authenticated same-GUI reopen.')


def keeper(d):
    phase=d['phase'];require(phase in PHASES and d['primary_endpoint']=='repeat_open' and d['primary_metric']=='second_covers' and
        d['reference_installed_application_sha256']==CONTROL and d['reopen_policy']=='data-only-45-v1','unrecognized two-open scope')
    require(d['artifacts'] and d['artifacts']['candidate']['application_sha256'] in REVISIONS,'unbound two-open program')
    good=d['valid'] is True and not d['issues'] and not d['input_failures'];mech=d['mechanism']
    require(mech['name']=='data_only_retained_library_pixels' and mech['gui_process_reused'] is False,'not data-only retention')
    cohort={};batches=[];pairs=[]
    for b in d['batches']:
        rid=run_id(b['run_id']);require(b['scenario'] in ('immediate','ttl','ordinary','loss'),'unexpected keeper scenario')
        batches.append(dict(run=rid,mode=b['mode'],scenario=b['scenario'],guards={k:b['batch_guards'].get(k) is True for k in GUARDS}))
        cohort[rid]={r['case']:r for r in b['cases']}
    for pair in d['pairs']:
        row=dict(round=count(pair['round']),run=run_id(pair['batch']),order=pair['order'],complete=pair['complete'] is True)
        require(row['order'] in ('AB','BA') and row['round']>0 and row['run'] in cohort,'unknown two-open pair')
        if row['complete']:
            obs=[cohort[row['run']][f'{s}-{row["round"]:02d}'] for s in SIDES]
            require(all(r['status']=='ok' and r['new_gui_process_for_each_visit'] and r['native_home_after_both_visits'] for r in obs),'not two fresh GUI processes')
            for key in KEEPER_METRICS:
                row[key]=triple(*(r['elapsed_ms'][key] for r in obs))
                require(abs(row[key][2]-number(pair['savings_ms'][key]))<0.002,'two-open pair discrepancy')
            row['pixels_used_on_second_open']=[count(r['actions']['second']['pixel_hits']) for r in obs]
        pairs.append(row)
    sums=summary(pairs,KEEPER_METRICS)
    if good and phase in ('screen','confirm'):
        n=6 if phase=='screen' else 10
        require(len(pairs)==n and all(r['complete'] for r in pairs) and sorted(r['round'] for r in pairs)==list(range(1,n+1)) and
                Counter(r['order'] for r in pairs)=={'AB':n//2,'BA':n//2},'incomplete two-open phase')
        primary=sums['second_covers'];secondary=sums['second_usable']
        require((primary['positive_pairs']>=n-1 and primary['median_paired_saving']>=100 and secondary['median_paired_saving']>=-50)
                is d['latency_gate_passed'],'two-open gate differs')
    scenarios={k:v is True for k,v in d['qualification_by_scenario'].items() if k in ('immediate','ttl','ordinary','loss')}
    resources={}
    for side in SIDES:
        observed=d['residency_resources'][side]
        resources[side]={k:count(observed[k]) for k in ('episodes_observed','keepers_observed','keeper_pss_unavailable')}
        for k in ('keeper_rss_kib_when_present','keeper_pss_kib_when_available','idle_cpu_ticks_on_same_keeper','same_keeper_observation_ms','observer_wall_ms'):
            if observed[k] is not None:
                resources[side][k]={i:number(observed[k][i]) for i in ('n','min','median','max')}
    return dict(group='quick-return-keeper',revision=REVISIONS[d['artifacts']['candidate']['application_sha256']],phase=phase,valid=good,
        status='invalid_observations' if not good else 'qualified_scenarios_only' if phase=='qualify' else 'pilot_only' if phase=='pilot'
        else 'mechanism_not_observed' if not mech['verified'] else 'latency_gate_not_met' if not d['latency_gate_passed'] else
        'independent_confirmation_passed' if phase=='confirm' else 'screen_eligible_for_independent_confirmation',
        arms=arms(d['artifacts'],'keeper',good), batches=batches,pair_data=pairs,summary_ms=sums,mechanism_observed=mech['verified'] is True,
        gui_process_reused=False, fallback_qualification=scenarios, all_fallbacks_qualified=d['all_fallbacks_qualified'] is True,
        gates={'latency':d['latency_gate_passed'],'scenarios':d['qualification_passed'] if scenarios else None},
        residency_resources=resources,
        scope='Prime and second opens are different GUI processes. Only validated cover pixels can survive between visits in a data-only keeper. Sampling RSS, PSS and idle CPU ticks during Home is not a peak, battery, or sleep-power measurement. Raw wall time includes measured Home gap and intentional dwell; second-open timing does not.')


def compact_index(records,candidate=CANDIDATE):
    groups={key:[] for key in FEATURES}
    for name,r in records.items():
        require(r['revision']==REVISIONS[candidate],'a performance revision index cannot combine programs')
        item={'file':name+'.json','phase':r['phase'],'valid':r['valid'],'outcome':r['status'],
            'runs':[b['run'] for b in r['batches']], 'observed_pairs':len(r.get('pair_data',[])),
            'decision_pairs':sum(bool(p.get('complete',True)) for p in r.get('pair_data',[])) if r['valid'] and r['phase'] in ('screen','confirm') else 0}
        if r['valid'] and r['phase'] in ('screen','confirm'):
            values=r.get('summary_ms',r.get('summary_units_in_metric_names',{}))
            fields=(['prime_covers','second_usable','second_covers','both_active_until_covers','both_exit_cost'] if r['group']=='quick-return-keeper' else
                    ['ingest_request_to_visible_ms','extra_tail_ms','reopen_covers_ms','two_visible_waits_ms','two_including_deferred_ms'] if r['group']=='import' else list(ENDPOINTS))
            item['observed_medians']={k:{field:values[k][field] for field in ('a_median','b_median','median_paired_saving','positive_pairs')}
                                      for k in fields if k in values}
        groups[r['group']].append(item)
    guard={}
    for mode in ('warm','cold'):
        eligible=[r for r in records.values() if r['group']=='defaults-'+mode and r['phase']=='screen' and r['valid']]
        guard[mode]=[{'run':b['run'],'passed':r['gates']['protection']} for r in eligible for b in r['batches'] if b['mode']=='screen']
    readiness=False if any(row['passed'] is False for bucket in guard.values() for row in bucket) else True if all(guard.values()) else None
    return dict(reference_elf_sha256=CONTROL,integrated_elf_sha256=candidate,
        default_guard_evidence=guard,both_default_timing_guards_met=readiness,
        endpoints='Native indexed request to usable software Library frame and to accepted complete visibly covered frame; exit starts at shutdown, ends at native Home. No optical times.',
        schema='Pair triples and diagnostic stage triples: [A, B, A minus B]. Stage triples use medians of A and B and the median of individual paired savings. Stage medians are not additive.',
        gate='Defaults: six balanced pairs, each startup endpoint median paired saving >= -50 ms, protection only. Isolated trials: >=100 ms on the predeclared primary, >=5/6 favorable screening pairs; confirm independently with >=9/10. Other endpoint >= -50 ms. Zero activations count. Functional qualification alone is not a speedup.',
        scope='Import and two-open retention use separate fixtures and definitions. Data-only pixel retention is not a resident GUI. A section without a current public report makes no production or performance claim.',
        groups={key:{'reports':rows,'status':'verified_report_available' if any(r['valid'] for r in rows) else
                     'invalid_observations_only' if rows else 'no_current_public_evidence'} for key,rows in groups.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--report',action='append',default=[],metavar='LABEL=PUBLIC_JSON');ns=p.parse_args()
    if sys.platform!='linux':p.error('Run on test-env')
    records={}; identities={}
    for arg in ns.report:
        name,sep,path=arg.partition('='); path=Path(path)
        require(sep and name not in records and re.fullmatch(r'[a-z][a-z0-9-]{0,47}',name) and 'private' not in path.parts,'unexpected public input')
        d=json.loads(path.read_text())
        if d.get('reopen_policy')=='data-only-45-v1': r=keeper(d)
        elif d.get('trial')=='all-options': r=all_options(d)
        elif 'visits' in d and 'distinct_waiters' in d and 'outcome' in d: r=gui_admission(d)
        elif 'resolved_options' in d: r=lifecycle(d)
        elif 'physical_batches' in d and 'default_gate_passed' in d: r=imported(d)
        else: r=regular(d)
        for b in r['batches']:
            key=(r['revision'],r['group'],b.get('mode',r['phase']))
            require(b['run'] not in identities or key[-1]=='qualify' and identities[b['run']]==key,'duplicate physical timing batch')
            identities[b['run']]=key
        records[name]=r
    stopped='aidoku-all-import-screen-362b98e8'
    require(stopped not in identities,'invalid original import run reused')
    records['r1-invalid-import-screen'] = dict(group='import',revision='r1',phase='screen',valid=False,
        status='installed_cache_guard_failed_no_latency_export',batches=[{'run':stopped}],
        app_stages_observed=28,decision_pairs=0,lease_released_after_owner_recovery=True,
        scope='Original owner recovered native state and released. The installed cache size/mtime aggregate differed; filenames, cache index and other protected records matched. Neither the file, the writer nor a content change can be identified. No timings are included or reused.',
        evidence='../bench/import_probe/guard_v2/README.md')
    version_indexes={}
    for candidate,version in REVISIONS.items():
        by_version={n:r for n,r in records.items() if r['revision']==version}
        version_indexes[version+'-index']=compact_index(by_version,candidate)
    main_index={'reference_elf_sha256':CONTROL,'version_isolation':'Every temporal and functional result below belongs to its exact ELF, fixture and phase. There is no pooled R1/R2 estimate.',
            'revisions':{'r1':{'index':'r1-index.json','elf':CANDIDATE,'status':'retired_before_delivery','note':'Idle import was the default; prewarm store contention and the grace timer were revised.'},
                         'r2':{'index':'r2-index.json','elf':R2,'status':'independently_measured_revision','note':'Only optimized first layout is selected by default among the new choices. All other changes have separate evidence.'}},
            'installed_reference_observations':{n+'.json':r['status'] for n,r in records.items() if r['revision']=='installed-reference'}}
    ns.out.mkdir(parents=True,exist_ok=True)
    for name,data in {**records, **version_indexes,'index':main_index}.items():
        (ns.out/(name+'.json')).write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v['status'] for k,v in records.items()}))


if __name__=='__main__':
    main()
