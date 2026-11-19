#!/usr/bin/env python3
"""Read the 2026-11-19 indexed-entry trials on test-env; no device operations."""

import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from types import ModuleType


CONTROL = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
OLD = {'62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612',
       'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6',
       'c954eff22cc43bfc2cd27236d6fb0e9e5d8490d6322bd49763c0f4b7fb5acda2',
       '153e018635461d00a6b0378a391f76adf929178fd75b787b2088e9753d3fcd3f'}
FROZEN = {
    'warm': ('a6270d6a2d2a78ad7dd1dd59819154dee2f091f85b8bb47bdf7a819fe3711ea6',
             'b81b54db195ec3e0de03f0374e16c614fe75e43b424b918c31e4be1648944f4a'),
    'cold': ('bcc36a9ce043af28b4d077e30280de251d47157a08e9c4816786c658f56c5883',
             'baacc109dcbc1716f5bfca4471627b8fc55961da1b203b580e426f6a31f91e40')}
DEPS = {
    'warm': ('analyze_bench.py', 'tools', '/root/aidoku-next-bench-20261118/tools',
             'f796f2c20c75f183996d63612c19dad7a4a8aa47e7ce0f1d0995124509bdc8c7'),
    'toolbar': ('analyze_status.py', 'status-bench','/root/aidoku-next-statusbench-20261118/tools',
                '55e126239715708b4c623f164178a33f25a8c631a6232a7f5f8ad50a9006e569'),
    'pixels': ('toolbar_pixels.py', 'status-bench','/root/aidoku-next-statusbench-20261118/tools',
               '6958232ab783a32ceb822c7ba13d083ec2470bb4bc13e3af675e352e0c703a20'),
    'cold': ('analyze_cold.py', 'cold-bench', '/root/aidoku-next-coldbench-20261118/tools',
             '2d9a5d376691af829f85a936d428d9f498ae1b63b3d5423229374f5a7e87a839')}
OPTIONS = {'portrait':{'shell','native'},'prewarm': {'0','2'}, 'last_cover_grace': {'off','on'}, 'first_layout': {'baseline','optimized'},
           'import_thumbnail': {'off','idle'}, 'resident': {'off','45'}}
DEFAULTS={'portrait':'shell','prewarm':'0','last_cover_grace':'off','first_layout':'optimized','import_thumbnail':'idle','resident':'off'}
SWITCHES = {'prewarm': ('prewarm','0','2'), 'grace': ('last_cover_grace','off','on'),
            'layout': ('first_layout','baseline','optimized')}
PRIMARY = {'supervisor':'first','layout':'first','prewarm':'covers','grace':'covers','combined':'covers'}
SIDES = ('baseline','candidate')
PHASES = ('pilot','screen','confirm','qualify')
REGIME = {'warm':('warm-cache-v2','sealed-private-cache-v2'), 'cold':('empty-cache-v2','per-case-empty-cache-v2')}
HEX = re.compile(r'[0-9a-f]{64}\Z')


def load(key):
    file, directory, remote, digest = DEPS[key]
    for path in (Path(__file__).resolve().parents[2]/'startup-next-20261118'/directory/file,Path(remote)/file):
        if path.is_file():
            data = path.read_bytes()
            if sha256(data).hexdigest() != digest:
                raise ValueError('Frozen measurement source mismatch')
            mod = ModuleType('_indexed_entry_all_' + key)
            mod.__file__ = str(path)
            exec(compile(data,str(path),'exec'),mod.__dict__)
            return mod
    raise ValueError('Frozen measurement source unavailable')


def valid_hash(value):
    return isinstance(value,str) and HEX.fullmatch(value)


def variants(report, check):
    data = report.get('variants')
    check(isinstance(data,dict) and set(data)==set(SIDES), 'invalid_variant_set')
    out={}
    for side in SIDES:
        row=data[side]
        hash_keys=('application_sha256','launcher_sha256','native_query_sha256','options_sha256','variant_sha256')
        check(isinstance(row,dict) and all(valid_hash(row.get(k)) for k in hash_keys), 'invalid_artifact_hash')
        check(set(row)==set(hash_keys) | {'env','args','startup_options','extra_sha256'}, 'unrecognized_variant_shape')
        opts=row['startup_options'];env=row['env'];extra=row['extra_sha256']
        check(isinstance(opts,dict) and all(k in OPTIONS and isinstance(v,str) and v in OPTIONS[k] for k,v in opts.items()),
              'unrecognized_or_private_startup_options')
        option_bytes=''.join(f'{k}={opts[k]}\n' for k in sorted(opts)).encode('ascii')
        check(sha256(option_bytes).hexdigest()==row['options_sha256'], 'option_digest_mismatch')
        check(row['args']==[] and env in ({},{'AIDOKU_STARTUP_PORTRAIT':'native'},{'AIDOKU_STARTUP_PORTRAIT':'legacy'}),
              'unrecognized_startup_arguments_or_environment')
        check(isinstance(extra,dict) and all(k=='aidoku-startup-portrait' and valid_hash(v) for k,v in extra.items()),
              'unrecognized_extra_program')
        normalized={k:v for k,v in row.items() if k!='variant_sha256'}
        digest=sha256(json.dumps(normalized,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        check(row['variant_sha256']==digest,'variant_configuration_digest_mismatch')
        out[side]=dict(normalized,variant_sha256=digest)
    return out


def isolated(spec,trial,candidate):
    a,b=(spec[s] for s in SIDES)
    if b['application_sha256']!=candidate or candidate in OLD or any(v['native_query_sha256']!=QUERY for v in (a,b)):
        return False
    if trial!='supervisor' and candidate==CONTROL:
        return False
    if trial=='combined':
        return (a['application_sha256']==CONTROL and a['launcher_sha256']==LAUNCH and not a['extra_sha256']
                and b['application_sha256']!=CONTROL and b['launcher_sha256']!=LAUNCH and bool(b['extra_sha256'])
                and all(not v['env'] and not v['startup_options'] and not v['args'] for v in (a,b)))
    if any(a[k]!=b[k] for k in ('application_sha256','launcher_sha256','native_query_sha256','extra_sha256','args')):
        return False
    if trial=='supervisor':
        legacy_gate=(a['startup_options']==b['startup_options'] and a['env']=={'AIDOKU_STARTUP_PORTRAIT':'legacy'}
                     and b['env']=={'AIDOKU_STARTUP_PORTRAIT':'native'})
        file_gate=(not a['env'] and not b['env'] and a['startup_options'].get('portrait','shell')=='shell' and
                   b['startup_options'].get('portrait')=='native' and
                   {k:v for k,v in a['startup_options'].items() if k!='portrait'}=={k:v for k,v in b['startup_options'].items() if k!='portrait'})
        return bool(b['extra_sha256'] and (legacy_gate or file_gate))
    key,disabled,enabled=SWITCHES[trial]
    return (a['env']==b['env'] and a['startup_options'].get(key,DEFAULTS[key])==disabled and b['startup_options'].get(key,DEFAULTS[key])==enabled
            and {k:v for k,v in a['startup_options'].items() if k!=key}=={k:v for k,v in b['startup_options'].items() if k!=key})


def cache_evidence(directory,workload,ref):
    # Both programs now contain the previously deployed cold decode; it is
    # background cost here, not evidence of this trial's intervention.
    check=ref.require
    ev=ref.read_json(directory/'application.json').get('events')
    check(isinstance(ev,list) and all(isinstance(e,dict) and isinstance(e.get('name'),str) and ref.natural(e.get('at_us')) for e in ev),
          'invalid_application_events')
    check(all(a['at_us']<=b['at_us'] for a,b in zip(ev,ev[1:])), 'invalid_application_clock')
    def selected(name):return [e for e in ev if e['name']==name]
    def once(name):
        e=selected(name);check(len(e)==1,'missing_or_duplicate_startup_event');return e[0]
    role=directory.name.split('-')[-1]
    first,settled=once('startup_library_frame_submitted'),once('startup_visible_covers_settled')
    check(first.get('cover_slots')==settled.get('cover_slots')==settled.get('cover_images')==7
          and settled.get('cover_pending') is False and ref.read_json(directory/'state.json').get('cover_visible_count')==6,
          'not_six_visible_books_seven_image_slots')
    jobs=[];active=None
    names={'cover_preparation_started','cover_preparation_finished','cover_local_derivative_prepared','cover_local_derivative_reused',
           'cover_cold_validation_combined_started','cover_cold_decode_validated','cover_cold_validation_fallback'}
    for e in ev:
        n=e['name'];t=e['at_us']
        if n not in names:continue
        check(first['at_us']<=t<=settled['at_us'], 'visible_job_outside_startup')
        if n=='cover_preparation_started':
            check(active is None,'overlapping_visible_cover_job');active={n:t};continue
        check(active is not None and n not in active and t>=max(active.values()),'malformed_visible_cover_job')
        active[n]=t
        if n!='cover_preparation_finished':continue
        hit='cover_local_derivative_reused' in active;miss='cover_local_derivative_prepared' in active
        check(hit!=miss,'ambiguous_derivative_regime')
        started='cover_cold_validation_combined_started' in active;validated='cover_cold_decode_validated' in active
        check(started==validated,'unmatched_source_validation_events')
        if validated:
            check(miss and active['cover_cold_validation_combined_started']<=active['cover_cold_decode_validated']<=active['cover_local_derivative_prepared']
                  and 'cover_cold_validation_fallback' not in active,'invalid_source_validation_order')
        jobs.append({'ordinal':len(jobs)+1,'cache_hit':hit,'source_pixels_validated':validated,
                     'duration_ms':ref.ms(t-active['cover_preparation_started'])});active=None
    check(len(jobs)==6 and active is None,'missing_visible_cover_job')
    hits=sum(j['cache_hit'] for j in jobs)
    if workload=='cold' or role!='warmup':check(hits==(0 if workload=='cold' else 6),'wrong_derivative_cache_regime')
    return ev,{'hits':hits,'misses':6-hits,'jobs':jobs,'ready_slots':7,'visible_items':6,
               'cold_source_validations':sum(j['source_pixels_validated'] for j in jobs)}


def features(events, ref):
    check=ref.require
    def select(n):return [e for e in events if e['name']==n]
    def one(n):
        chosen=select(n);check(len(chosen)==1,'missing_or_duplicate_feature_boundary');return chosen[0]
    initial=one('startup_initial_frame_submitted')['at_us'];input_ready=one('startup_initial_input_ready')['at_us'];first=one('startup_library_frame_submitted')['at_us']
    end=one('startup_visible_covers_settled')['at_us']; view=one('startup_view_started')['at_us']; attached=one('startup_view_attached')['at_us']
    upper=one('startup_ready')['at_us'];started=one('startup_background_covers_admitted')['at_us']
    check(initial<=view<=attached<=upper<=first<=end,'invalid_feature_boundaries')
    before=[e for e in events if initial<=e['at_us']<=end]
    names=Counter(e['name'] for e in before)
    all_warm=[e for e in events if e['name'].startswith('startup_cover_warm_')]
    warm_keys=('plan','attempt','hit','stopped','store_busy','open_failed','selection_failed','ineligible','no_selection','filtered','image_unavailable','image_failed')
    warm={k:names['startup_cover_warm_'+k] for k in warm_keys}
    check(all(e['name'][len('startup_cover_warm_'):] in warm for e in all_warm), 'unrecognized_prewarm_event')
    check(all(initial<=e['at_us']<=end for e in all_warm) and warm['attempt']<=2 and warm['hit']<=warm['attempt'], 'invalid_prewarm_job_budget_or_window')
    def stamps(name):return [e['at_us'] for e in select(name)]
    warm['consumed']=names['cover_local_derivative_memory_hit']
    job_starts=stamps('cover_preparation_started');job_ends=stamps('cover_preparation_finished')
    memory_hits=stamps('cover_local_derivative_memory_hit');reuses=stamps('cover_local_derivative_reused')
    good_consumes=[t for t in memory_hits if any(first<=jb<=t<=hit<=je<=end for jb,je in zip(job_starts,job_ends) for hit in reuses)]
    planned=stamps('startup_cover_warm_plan'); attempted=stamps('startup_cover_warm_attempt'); hits=stamps('startup_cover_warm_hit')
    good_hits=[]; pending=None
    for e in all_warm:
        t=e['at_us']; n=e['name'].removeprefix('startup_cover_warm_')
        if n=='attempt': pending=t
        if n in ('hit','image_unavailable','image_failed'):
            if n=='hit' and pending is not None and initial<=pending<=t<=input_ready and any(initial<=p<=pending for p in planned):
                good_hits.append(t)
            pending=None
    warm.update(verified_hits_before_input=len(good_hits), verified_consume_hits=len(good_consumes),
                first_attempt_after_initial_ms=ref.ms(attempted[0]-initial) if attempted else None,
                last_hit_after_initial_ms=ref.ms(hits[-1]-initial) if hits else None,
                usable_hit=bool(good_hits and good_consumes and min(good_hits)<min(good_consumes)))
    layout=[e for e in events if view<=e['at_us']<=attached and e['name'] in ('library_page_projection_started','library_page_projection_batched','library_page_projection_rowwise')]
    path=[e['name'] for e in layout]; layout_mode='unmarked'; proj_ms=None
    if path:
        check(len(path)==2 and path[0]=='library_page_projection_started' and path[1] in ('library_page_projection_rowwise','library_page_projection_batched'), 'inconsistent_first_layout_path')
        layout_mode=path[1].removeprefix('library_page_projection_');proj_ms=ref.ms(layout[1]['at_us']-layout[0]['at_us'])
    frames=select('startup_cover_frame_submitted');last=one('startup_visible_covers_settled');first_frame=one('startup_library_frame_submitted')
    check(frames and first_frame['at_us']<=frames[0]['at_us'] and frames[-1]['at_us']<=last['at_us'] and
          first_frame.get('frame_sequence')==frames[0].get('frame_sequence') and last.get('frame_sequence')==frames[-1].get('frame_sequence')
          and all(ref.natural(e.get('frame_sequence')) and e.get('cover_slots')==7 and ref.natural(e.get('cover_images')) and e['cover_images']<=7 for e in frames) and
          all(a['frame_sequence']<b['frame_sequence'] for a,b in zip(frames,frames[1:])), 'unverified_complete_frame_sequence')
    partial=frames[1:-1];check(all(e.get('cover_pending') is True for e in partial),'settled_intermediate_frame')
    prefix='startup_cover_last_grace_'; states={k:select(prefix+k) for k in ('started','completed','expired','cancelled')}
    check(all(e['name'][len(prefix):] in states for e in events if e['name'].startswith(prefix)) and
          all(len(v)<=1 for v in states.values()) and len(states['started'])==sum(len(v) for k,v in states.items() if k!='started'),
          'inconsistent_one_time_grace')
    grace={k:len(v) for k,v in states.items()};grace.update(actual_wait_ms=None,single_final_frame_when_grace_used=False)
    if states['started']:
        t0=states['started'][0]['at_us']; outcome=next(k for k in ('completed','expired','cancelled') if states[k]); t1=states[outcome][0]['at_us']
        jobs=select('cover_preparation_finished');check(len(jobs)==6 and jobs[-2]['at_us']<=t0 and first<=t0<=t1<=end,'grace_not_final_visible_job')
        after=[e for e in partial if e['at_us']>=t0];grace.update(actual_wait_ms=ref.ms(t1-t0),start_after_admission_ms=ref.ms(t0-started),
                decision_after_admission_ms=ref.ms(t1-started),partial_frames_after=len(after))
        if outcome=='completed':
            publications=select('startup_final_cover_publication');check(len(publications)==1 and jobs[-1]['at_us']<=t1<=publications[0]['at_us']<=frames[-1]['at_us']
                and not any(e['at_us']<t1 for e in after),'grace_completion_not_linked_to_final_frame')
            # The real 300 ms origin lies between admission and the first frame
            # observer. A late application timestamp stays in the latency pairs,
            # but cannot certify prompt completion inside this bounded window.
            consistent=started+300_000<=t0<=t1<=first+340_000
            grace['within_observed_cadence_window']=consistent
            grace['single_final_frame_when_grace_used']=not partial and consistent
    return {'prewarm':warm,'grace':grace,'layout':{'mode':layout_mode,'projection_ms':proj_ms,'first_view_ms':ref.ms(attached-view)},
            'intermediate_cover_frames':len(partial),'frame_count':len(frames)}


def case_configuration(directory,spec,workload):
    if ((directory/'startup-options.sha256').read_bytes()!=(spec['options_sha256']+'\n').encode() or
            (directory/'startup-options-presence').read_bytes()!=(b'present\n' if spec['startup_options'] else b'absent\n')):
        return None
    if workload=='warm':
        if (directory/'fixture-name').read_bytes()!=b'warm-cache-v2\n':return None
        seed=(directory/'fixture.sha256').read_bytes()
        return seed if re.fullmatch(rb'[a-f0-9]{64}\n',seed) else None
    if (directory/'cold-fixture').read_bytes()!=b'empty-cache-v1\n' or (directory/'cold-counts').read_bytes()!=b'6 0\n':return None
    seed=(directory/'seed.sha256').read_bytes()
    if not re.fullmatch(rb'[a-f0-9]{64}  library\.sqlite3\n[a-f0-9]{64}  device-profile\n',seed):return None
    digest=(sha256(seed).hexdigest()+'\n').encode();return digest if (directory/'cold-fixture.sha256').read_bytes()==digest else None


def app_selection(events,spec,ref):
    actual=[e for e in events if e['name'].startswith('startup_config_')]
    if spec['application_sha256']==CONTROL:
        ref.require(not actual,'legacy_application_emitted_new_selection_markers')
        return {'observed':False,'reason':'precedes_configuration_protocol'}
    intended={**DEFAULTS,**spec['startup_options']}
    allowed={'startup_config_'+k+'_'+v for k,v in intended.items()}
    cutoff=next(e['at_us'] for e in events if e['name']=='startup_initial_frame_submitted')
    ref.require(len(actual)==len(allowed) and {e['name'] for e in actual}==allowed
                and all(e['at_us']<=cutoff for e in actual),'application_did_not_consume_the_frozen_options')
    return {'observed':True,'values':intended}


def measure_mechanism(batches,trial,workload):
    groups={}
    for role,selected in [('timing',lambda n:n.split('-')[-1].isdigit()),('pixels',lambda n:n.endswith('-qualify')),
                          ('warmup',lambda n:n.endswith('-warmup'))]:
        summary={}
        for side in SIDES:
            rows=[r for b in batches for r in b['cases'] if r['case'].startswith(side+'-') and selected(r['case'])]
            observed=[r for r in rows if r.get('features') and r.get('cache') and r.get('toolbar') and r['status']=='ok']
            summary[side]={
                'planned':len(rows),'observed':len(observed),'portrait_engines':dict(Counter(r['diagnostics']['portrait_engine'] for r in observed)),
                'toolbar_hits':sum(r['toolbar']['classification']=='hit' for r in observed),
                'toolbar_fallbacks':sum(r['toolbar']['classification']=='fallback' for r in observed),
                'cold_source_validated_jobs':sum(r['cache']['cold_source_validations'] for r in observed),
                'prewarm_attempted_runs':sum(r['features']['prewarm']['attempt']>0 for r in observed),
                'prewarm_usable_runs':sum(r['features']['prewarm']['usable_hit'] for r in observed),
                'prewarm_ready':sum(r['features']['prewarm']['hit'] for r in observed),
                'prewarm_zero_attempt_runs':sum(r['features']['prewarm']['attempt']==0 for r in observed),
                'prewarm_plans':sum(r['features']['prewarm']['plan'] for r in observed),
                'grace_started_runs':sum(r['features']['grace']['started'] for r in observed),
                'grace_completed_single_frame_runs':sum(r['features']['grace']['single_final_frame_when_grace_used'] for r in observed),
                'grace_expired':sum(r['features']['grace']['expired'] for r in observed),
                'grace_cancelled':sum(r['features']['grace']['cancelled'] for r in observed),
                'intermediate_cover_frames':sum(r['features']['intermediate_cover_frames'] for r in observed),
                'layouts':dict(Counter(r['features']['layout']['mode'] for r in observed))}
        groups[role]=summary
    target=groups['timing'] if groups['timing']['candidate']['planned'] else groups['pixels']
    a,b=(target[s] for s in SIDES)
    both=all(v['planned']>0 and v['planned']==v['observed'] for v in (a,b))
    if trial=='supervisor':
        exercised=both and a['portrait_engines']=={'shell':a['observed']} and b['portrait_engines']=={'native':b['observed']}
    elif trial=='prewarm':
        exercised=both and not a['prewarm_plans'] and not a['prewarm_ready'] and not a['prewarm_attempted_runs'] and b['prewarm_usable_runs']>0
    elif trial=='grace':
        exercised=both and not a['grace_started_runs'] and b['grace_completed_single_frame_runs']>0
    elif trial=='layout':
        exercised=both and a['layouts']=={'rowwise':a['observed']} and b['layouts']=={'batched':b['observed']}
    else:
        exercised=(both and (workload!='cold' or all(v['cold_source_validated_jobs']>0 for v in (a,b))) and
                   all(v['toolbar_hits']>0 for v in (a,b)) and not b['grace_started_runs'] and not b['prewarm_plans'] and
                   not b['prewarm_attempted_runs'] and b['layouts']=={'batched':b['observed']} and
                   b['portrait_engines']=={'shell':b['observed']})
    return {'name':trial,'verified':bool(exercised),'workload':workload,**groups,
            'scope':'Zero-trigger and fallback runs stay in the paired figures. A triggered path does not by itself establish speedup. Non-transport commands and manual lifecycle/installed-entry qualification are tested separately.'}


def interval_diagnostics(batches,ref):
    columns={}
    def observe(row):
        d,f,t=row['diagnostics'],row['features'],row['toolbar']
        return {'request_to_launcher_bridge':d['request_to_launcher_bridge_ms'],'launcher_entry_to_worker':d['worker_entry_ms'],
                'observer_start_to_ready':d['observer_ready_ms'],'portrait_outer':d['portrait_outer_ms'],'display_takeover':d['takeover_ms'],
                'initial_send_to_input':t['initial_send_to_input_ms'],'first_library_view':f['layout']['first_view_ms'],
                'first_page_projection':f['layout']['projection_ms'],'first_render':d['first_frame_cost_ms'][0],
                'first_to_all_covers':d['cover_tail_ms'],'serial_visible_cover_jobs':round(sum(j['duration_ms'] for j in row['cache']['jobs']),3),
                'complete_cover_render':d['covers_frame_cost_ms'][0],'complete_cover_prior_buffer_settle':d['covers_frame_cost_ms'][1]}
    for batch in batches:
        if batch['mode'] not in ('pilot','screen','confirm'):continue
        rows={r['case']:r for r in batch['cases']}
        for pair in batch['pairs']:
            if not pair['complete']:continue
            a,b=(observe(rows[f'{side}-{pair["round"]:02d}']) for side in SIDES)
            for name in a:
                if a[name] is None or b[name] is None:continue
                columns.setdefault(name,[]).append((a[name],b[name]))
    return {name:{'baseline':ref.stats([a for a,_ in values]),'candidate':ref.stats([b for _,b in values]),
                  'paired_savings':ref.stats([ref.delta(a,b) for a,b in values])} for name,values in columns.items()}


def analyze(paths,qualification,trial,workload,phase,candidate_sha256,crop=None):
    if trial not in PRIMARY or workload not in REGIME or phase not in PHASES or not valid_hash(candidate_sha256) or candidate_sha256 in OLD:
        raise ValueError('Unplanned mode or ineligible application identity')
    if trial!='combined' and workload!='warm' or trial=='combined' and phase=='confirm':
        raise ValueError('Use an isolated warm screen/confirmation or a separate combined default protection screen for each regime')
    ref=load('warm');toolbar=load('toolbar');old_batch,old_case,old_pixels=ref.audit_batch,ref.audit_case,ref.pixels
    all_seeds={};all_specs={}
    ref.identities=lambda report:variants(report,ref.require)
    def case(out,name,commands):
        root=out.parent.parent;directory=out/name; record=old_case(out,name,commands)
        try:
            v=all_specs[root][name.split('-')[0]]
            seed=case_configuration(directory,v,workload)
            ref.require(seed is not None, 'fixture_or_applied_option_evidence_mismatch')
            record.update(fixture_and_options_verified=True, applied_options_sha256=v['options_sha256'],
                          options_file_present=bool(v['startup_options']))
            # The first warmup creates disposable derivatives. It is not a sample
            # of the sealed warm tree used for the other arm and timed pairs.
            if workload!='warm' or name!='baseline-warmup':all_seeds.setdefault(root,set()).add(seed)
        except (OSError,ValueError,TypeError,KeyError):
            record['issues'].append('missing_fixture_or_option_receipt')
        except ref.Invalid as err:record['issues'].append(str(err))
        try:
            ev,record['cache']=cache_evidence(directory,workload,ref)
            record['application_selection']=app_selection(ev,all_specs[root][name.split('-')[0]],ref)
            record['features']=features(ev,ref)
            # Both arms already ship the two-property observation. Do not apply
            # November 18's expectation that the reference predates those marks.
            record['toolbar']=toolbar.toolbar_events(directory,ref.require,'candidate')
            if name.endswith('-qualify'):
                lines=[line.split() for line in (directory/'shell.tsv').read_text('ascii').splitlines()]
                crop_marks=[val[0] for val in lines if len(val)==2 and val[1]=='native_capture_started']
                ref.require(len(crop_marks)==1 and re.fullmatch(r'\d+\.\d{1,6}',crop_marks[0]), 'native_capture_clock_missing')
                capture=int(Decimal(crop_marks[0])*1_000_000);settled=[e for e in ev if e['name']=='startup_visible_covers_settled']
                ref.require(len(settled)==1 and capture>=settled[0]['boottime_us']+10_000,'capture_before_complete_visible_covers')
                record['native_capture_after_complete_covers_ms']=ref.ms(capture-settled[0]['boottime_us'])
        except (OSError,ValueError,UnicodeError,TypeError,KeyError):record['issues'].append('invalid_feature_or_pixel_receipt')
        except ref.Invalid as err:record['issues'].append(str(err))
        if record['issues'] and record['status']=='ok':record['status']='failed'
        return record
    def batch(root,mode,primary):
        plan=ref.read_json(root/'plan.json');report=ref.read_json(root/'report.json')
        specs=variants(plan,ref.require);all_specs[root]=specs
        b=old_batch(root,mode,primary)
        regime,policy=REGIME[workload]
        if not all(obj.get('trial')==trial and obj.get('cache_regime')==regime and obj.get('fixture_policy')==policy and
                   obj.get('fixture_case_seed')=='independent' and obj.get('baseline_reference_sha256')==CONTROL and
                   obj.get('original_launcher_sha256')==LAUNCH for obj in (plan,report)):
            b['issues'].append('incompatible_trial_or_installed_baseline')
        if FROZEN[workload]!=(b['runner_sha256'],b['device_script_sha256']): b['issues'].append('unqualified_benchmark_tool')
        if not isolated(specs,trial,candidate_sha256):b['issues'].append('uncontrolled_intervention_or_wrong_application')
        if report.get('completed')!=plan['cases']:b['issues'].append('cases_not_completed_as_frozen')
        if mode!='qualify' and any(p['order']!=('AB' if p['round']%2 else 'BA') for p in b['pairs']):
            b['issues'].append('pair_order_differs_from_frozen_crossover')
        if len(all_seeds.get(root,()))!=1:b['issues'].append('fixture_not_constant_within_batch')
        b.update(trial=trial,workload=workload,cache_regime=regime,fixture_policy=policy,
                 baseline_reference_sha256=CONTROL,fixture_verified=bool(all_seeds.get(root)) and len(all_seeds[root])==1,
                 options_verified=all(r.get('fixture_and_options_verified') is True for r in b['cases']))
        b['mechanism']=measure_mechanism([b],trial,workload);b['issues']=sorted(set(b['issues']))
        if b['issues']:b['mechanism']['verified']=False
        return b
    def pixels(root,explicit):
        snap=old_pixels(root,explicit)
        try:snap.update(load('pixels').compare(root,snap))
        except ValueError:snap.update(right_status_bar_observable=False,identical_right_status_bar=False)
        return snap
    ref.audit_case,ref.audit_batch,ref.pixels=case,batch,pixels
    r=ref.analyze(paths,qualification,phase,PRIMARY[trial],crop)
    if any(not (s.get('right_status_bar_observable') and s.get('identical_right_status_bar')) for s in r['pixels']):
        r['issues'].append('right_status_bar_mismatch')
    seeds=set().union(*(all_seeds.get(p,set()) for p in paths))
    if len(seeds)!=1:r['issues'].append('primary_fixture_changed_or_missing')
    if r['issues'] or r['input_failures']:
        r.update(valid=False,qualification_passed=False,latency_gate_passed=None)
    mechanism=measure_mechanism(r['batches'],trial,workload)
    if not r['valid']:mechanism['verified']=False
    protection=None
    if trial=='combined':
        r.update(latency_gate_passed=None,gate=None)
        if phase=='screen' and r['valid']:
            protection=all(r['comparisons_ms'][k]['paired_savings_ms']['median']>=-50 for k in ('usable','covers'))
    verdict=('invalid_observations' if not r['valid'] else 'qualification_only' if phase=='qualify' else 'pilot_only' if phase=='pilot'
             else ('default_guard_passed' if protection else 'default_guard_failed') if trial=='combined'
             else 'mechanism_not_observed' if not mechanism['verified'] else 'latency_gate_not_met' if not r['latency_gate_passed']
             else 'independent_confirmation_passed' if phase=='confirm' else 'screen_eligible_for_independent_confirmation')
    r.update(trial=trial,workload=workload,cache_regime=REGIME[workload][0],fixture_policy=REGIME[workload][1],
             baseline_reference_sha256=CONTROL,expected_candidate_application_sha256=candidate_sha256,
             default_guard_passed=protection,mechanism=mechanism,primary_fixture_verified=len(seeds)==1,
             performance_conclusion=verdict,
             phase_comparisons_ms=interval_diagnostics(r['batches'],ref),
             analyzer_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
             dependency_sha256={k:DEPS[k][-1] for k in ('warm','toolbar','pixels')},
             scope_note='Paired times start at the actual indexed native open request and end at a software/driver observation. Independent complete default warm/cold checks are protection, not acceleration estimates. Sampled time, conditional activation, source and lifecycle qualification are separate. No per-project benefit is summed.')
    return r


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,action='append',required=True);p.add_argument('--qualification-batch',type=Path,action='append',default=[])
    p.add_argument('--trial',choices=tuple(PRIMARY),required=True);p.add_argument('--workload',choices=tuple(REGIME),required=True)
    p.add_argument('--phase',choices=PHASES,required=True);p.add_argument('--candidate-sha256',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--status-bar-pixels',type=int)
    args=p.parse_args()
    if sys.platform!='linux':p.error('Run only on test-env')
    try:r=analyze(args.batch,args.qualification_batch,args.trial,args.workload,args.phase,args.candidate_sha256,args.status_bar_pixels)
    except ValueError as err:p.error(str(err))
    args.output.parent.mkdir(exist_ok=True,parents=True);args.output.write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:r[k] for k in ('trial','workload','phase','complete_pairs','valid','latency_gate_passed','default_guard_passed','qualification_passed','issues','input_failures')}))
    return 0 if r['valid'] else 2


if __name__=='__main__':sys.exit(main())
