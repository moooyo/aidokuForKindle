"""Recompute the cost of importing and subsequently opening the generated shelf."""

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import sys
import tarfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
import attest
import materialize

FLAGS=('success','cases_completed','same_boot','native_restored','entry_restored','installed_and_user_unchanged',
       'input_roster_unchanged','lease_released','temporary_stage_absent')
TERMINALS={'import_thumbnail_persisted','import_thumbnail_cache_hit','import_thumbnail_cancelled','import_thumbnail_skipped'}
SIDES=('baseline','candidate')
PHASES=('ingest','reopen')


def need(condition, reason):
    if not condition: raise ValueError(reason)


def sha(b): return hashlib.sha256(b).hexdigest()


def read(path):
    raw=path.read_bytes();need(len(raw)<=8*1024*1024,'oversized_record');return json.loads(raw)


def unique(events,name):
    found=[e for e in events if e['name']==name];need(len(found)==1,'missing_or_duplicate_'+name);return found[0]


def span(a,b):
    need(type(a) is int and type(b) is int and 0<=a<=b,'invalid_monotonic_span');return round((b-a)/1000,3)


def marks(path):
    values={}
    for line in path.read_text().splitlines():
        value,name=line.split();need(name not in values and re.fullmatch(r'\d+\.\d{1,6}',value),'invalid_clock_record')
        values[name]=int(Decimal(value)*1_000_000)
    need(values['native_open_requested']<=values['launcher_bridge']<=values['native_home_active'],'unrelated_clock')
    return values


def visible(event):
    need(event.get('cover_slots')==16 and event.get('cover_images')==16 and event.get('cover_pending') is False
         and type(event.get('marker')) is int and event['marker']>0,'unaccepted_or_empty_cover_frame')


def measure(events, clocks, phase, side):
    need(all(isinstance(e,dict) and isinstance(e.get('name'),str) for e in events),'invalid_event')
    clock=lambda name:unique(events,name)['boottime_us']
    origin=clocks['native_open_requested']; shutdown=clock('shutdown_started')
    first=clock('startup_library_frame_submitted'); inp=clock('startup_library_input_ready'); need(origin<=first<=inp<=shutdown,'invalid_ready_clock')
    end=clocks['native_home_active']; need(shutdown<=end,'invalid_shutdown_clock')
    values={'usable_ms':span(origin,max(first,inp)),'exit_ms':span(shutdown,end)}
    if phase=='reopen':
        done=unique(events,'startup_visible_covers_settled');visible(done)
        dc=done['boottime_us'];need(first<=dc<=shutdown,'reopen_covers_outside_program')
        counts=Counter(e['name'] for e in events if type(e.get('boottime_us')) is int and e['boottime_us']<=dc)
        # S00 is both the current-book tile and the first sorted row. Exactly fifteen distinct covers are prepared.
        need(counts['cover_local_derivative_prepared']==(4 if side=='baseline' else 0) and
             counts['cover_local_derivative_reused']==(11 if side=='baseline' else 15),'reopen_cache_mechanism_differs')
        values.update(covers_ms=span(origin,dc),created=counts['cover_local_derivative_prepared'],reused=counts['cover_local_derivative_reused'])
        return values
    begin,published=clock('import_started'),clock('import_library_published'); frame=unique(events,'import_visible_covers_settled')
    visible(frame); drawn=frame['boottime_us'];need(origin<=begin<=published<=drawn<=shutdown,'invalid_import_publication_clock')
    durations=[]; pending=None; counts=Counter(); last=drawn
    for ev in events:
        n=ev['name']
        if n=='import_thumbnail_started':
            need(pending is None and side=='candidate','unexpected_idle_job');pending=ev['boottime_us'];need(published<=pending<=shutdown,'early_import_thumbnail')
        elif n in TERMINALS:
            need(pending is not None,'unmatched_idle_job');finish=ev['boottime_us'];need(finish<=shutdown,'late_idle_job')
            durations.append(span(pending,finish));counts[n]+=1;pending=None;last=max(last,finish)
    need(pending is None,'uncompleted_idle_job'); drains=[e['boottime_us'] for e in events if e['name']=='import_thumbnail_queue_drained']
    if side=='candidate':
        need(len(drains)==1 and published<=drains[0]<=shutdown and len(durations)<=32 and counts['import_thumbnail_persisted']>=4
             and counts['import_thumbnail_cancelled']==0,'unfinished_import_thumbnail_queue')
        last=max(last,drains[0]);need(all(e['boottime_us']<=drains[0] for e in events if e['name'] in TERMINALS),'premature_queue_drain')
    else:need(not drains and not durations,'control_did_idle_work')
    values.update(import_publish_ms=span(begin,published),request_to_import_ms=span(origin,begin),
                  request_to_import_visible_ms=span(origin,drawn),import_to_visible_ms=span(begin,drawn),
                  request_to_all_work_ms=span(origin,last),extra_tail_after_visible_ms=span(drawn,last),
                  worker_elapsed_sum_ms=round(sum(durations),3),worker_jobs=len(durations),worker_outcomes=dict(counts))
    return values


def audit_case(directory,phase,side,capture):
    need((directory/'case-ok').read_bytes()==b'ok\n','case_gate_failed')
    timing=read(directory/'application.json');need(timing.get('success') is True and timing.get('finished') is True
         and timing.get('dropped_events')==0 and timing.get('clock')=='monotonic_rust_main','incomplete_or_unknown_trace')
    log=(directory/'launcher.log').read_bytes().splitlines();need(log.count(b'aidoku: Catalog notification completed.')==1
         and log.count(b'aidoku: Launcher worker exited with status 0.')==1 and not any(b'Catalog notification unavailable;' in s for s in log),'native_completion_failure')
    before=(directory/'catalog-before.identity').read_bytes();after=(directory/'catalog-after.identity').read_bytes()
    need(before==after and len(before.splitlines())==1 and before.strip(),'catalog_identity_change')
    need(int((directory/'catalog-after.access').read_text())>int((directory/'catalog-before.access').read_text()),'recent_access_not_advanced')
    need(capture==(directory/'native-settled.ppm').is_file() and not (directory/'system.csv').exists(),'mixed_diagnostic_sample')
    evidence=attest.verify_case(directory,phase,side)
    metrics=measure(timing['events'],marks(directory/'shell.tsv'),phase,side)
    return {'metrics':metrics,'source_and_pixels':evidence}


def names(mode,nums):
    result=['baseline-warmup','candidate-warmup']
    if mode=='qualify':result+=['baseline-qualify','candidate-qualify']
    else:
        for n in nums:result += [f'{s}-{n:02}' for s in (SIDES if n%2 else tuple(reversed(SIDES)))]
    return [case+'-'+p for case in result for p in PHASES]


def verify_batch(directory, want_mode):
    report=read(directory/'report.json');plan=read(directory/'plan.json');run=report['run_id'];mode=report['mode']
    need(directory.name==run and re.fullmatch('aidoku-all-[a-z][a-z0-9-]{0,12}-[0-9a-f]{8}',run),'unknown_batch')
    need(want_mode==mode,'mixed_batch_modes')
    need(all(report.get(k) is True for k in FLAGS),'batch_failed_or_not_restored')
    need(report['trial']=='import' and report['cache_regime']=='synthetic-import-reopen-v1' and
         report['fixture_sha256']==materialize.FIXTURE_SHA,'wrong_trial_or_fixture')
    for k in ('run_id','mode','cases','trial','fixture_sha256','variants','runner_sha256','device_script_sha256','payload_sha256'):
        need(report[k]==plan[k],'plan_mismatch_'+k)
    generated=materialize.generate();need(sha((directory/'runner.py').read_bytes())==plan['runner_sha256']==sha(generated['run_bench.py'].encode()) and
        sha((directory/'device.sh').read_bytes())==plan['device_script_sha256']==sha(generated['bench_device.sh'].encode()),'wrong_runner_bytes')
    need(sha((directory/'payload.tar.gz').read_bytes())==plan['payload_sha256'],'wrong_payload_bytes')
    with tarfile.open(directory/'payload.tar.gz','r:gz') as archive:
        lengths=archive.getmembers();need(len(lengths)==len({i.name for i in lengths}),'duplicate_payload_members')
        def artifact(n):
            i=archive.getmember(n);need(i.isfile() and 0<=i.size<=96*1024*1024,'invalid_payload_artifact');return archive.extractfile(i).read()
        generated_fixture=attest.fixture.bundle()
        for n,b in generated_fixture.items():need(artifact(n)==b,'different_synthetic_inputs')
        for s in SIDES:
            identity=plan['variants'][s]['application_sha256']
            need(artifact(s+'/binary-ref')==(identity+'\n').encode() and artifact(s+'/binary-hash')==(identity+'\n').encode()
                 and sha(artifact('bin-'+identity))==identity,'wrong_bench_binary')
            opt=('import_thumbnail='+('off' if s=='baseline' else 'idle')+'\n').encode()
            need(artifact(s+'/bench.options')==opt and artifact(s+'/bench.options.sha256')==(sha(opt)+'\n').encode(),'wrong_frozen_options')
        need(artifact('baseline/launch.sh')==artifact('candidate/launch.sh') and sha(artifact('bridge.sh'))==plan['bridge_sha256'],'instrumentation_differs_by_arm')
    lease=read(directory/'lease-released.json');need(lease.get('ok') is True and lease.get('owner_and_token_checked') is True
        and lease.get('native_restored_and_connections_closed') is True and lease.get('run_id')==run,'invalid_release_receipt')
    need((directory/'before.stdout').read_bytes()==(directory/'after.stdout').read_bytes() and (directory/'before.stdout').stat().st_size>100,'protected_snapshot_changed')
    variants=report['variants'];need(set(variants)==set(SIDES),'invalid_arms')
    for side,expected_mode in [('baseline','off'),('candidate','idle')]:
        v=variants[side];certain={k:x for k,x in v.items() if k!='variant_sha256'}
        need(sha(json.dumps(certain,sort_keys=True,separators=(',',':')).encode())==v['variant_sha256'],'invalid_variant_identity')
        need(v['startup_options']=={'import_thumbnail':expected_mode} and v['options_sha256']==sha(f'import_thumbnail={expected_mode}\n'.encode()),'invalid_treatment')
        need(not any(v[k] for k in ('env','args','extra_sha256')),'unrelated_options')
    need(all(variants['baseline'][k]==variants['candidate'][k] for k in ('application_sha256','launcher_sha256','native_query_sha256')),'different_raw_comparison_inputs')
    ids=sorted({int(x.split('-')[1]) for x in plan['cases'] if re.fullmatch(r'(baseline|candidate)-\d{2}-(ingest|reopen)',x)})
    need(report['completed']==plan['cases']==names(mode,ids),'missing_or_reordered_planned_cases')
    need(all(sum(z['name']=='case-'+x and z['returncode']==0 for z in report['commands'])==1 for x in plan['cases']),'missing_successful_command')
    rows={};root=directory/'outputs'/'out'
    for c in plan['cases']:
        side,kind,p=c.split('-'); rows[c]=audit_case(root/c,p,side,kind=='qualify')
    for label in ['warmup']+(['qualify'] if mode=='qualify' else [f'{n:02}' for n in ids]):
        for phase in PHASES:
            a,b=[rows[f'{s}-{label}-{phase}']['source_and_pixels']['image_sha256_by_title'] for s in SIDES]
            need(a and set(a)<=set(b) and all(a[k]==b[k] for k in a),'source_bound_pixels_differ_between_arms')
            if phase=='reopen': need(a==b,'final_derivative_sets_differ')
            if label=='qualify':attest.screen_pair(root/f'baseline-{label}-{phase}',root/f'candidate-{label}-{phase}')
    return {'run_id':run,'mode':mode,'ids':ids,'artifacts':variants,'rows':rows,'fixture_sha256':plan['fixture_sha256']}


def costs(one,two):
    a,b=one['metrics'],two['metrics'];disk=one['source_and_pixels'];
    return {'reopen_covers_ms':b['covers_ms'],'reopen_usable_ms':b['usable_ms'],'reopen_exit_ms':b['exit_ms'],
            'ingest_publish_ms':a['import_publish_ms'],'ingest_request_to_visible_ms':a['request_to_import_visible_ms'],
            'ingest_exit_ms':a['exit_ms'],'ingest_request_to_all_work_ms':a['request_to_all_work_ms'],
            'worker_elapsed_ms':a['worker_elapsed_sum_ms'],'extra_tail_ms':a['extra_tail_after_visible_ms'],
            'ingest_cached_bytes':disk['persisted_derivative_bytes'], 'ingest_cache_count':disk['persisted_derivative_count'],
            'two_visible_waits_ms':round(a['request_to_import_visible_ms']+b['covers_ms'],3),
            'two_including_deferred_ms':round(a['request_to_all_work_ms']+b['covers_ms'],3)}


def summarize(dirs,qual,phase):
    errors=[];bs=[];records=[]
    for path,m in [(d,phase) for d in dirs]+[(d,'qualify') for d in qual]:
        try:bs.append(verify_batch(path,m))
        except (ValueError,OSError,KeyError,TypeError,IndexError) as e:
            # Do not export arbitrary file names, raw JSON or identifiers from private recordings.
            error=str(e) if type(e) is ValueError and re.fullmatch('[a-z0-9_]{2,100}',str(e)) else 'malformed_or_missing_record'
            errors.append({'input':len(bs)+len(errors)+1,'scope':m,'error':error})
    artifacts=bs[0]['artifacts'] if bs else {}; primary=[b for b in bs if b['mode']==phase]
    if len({b['run_id'] for b in bs})!=len(bs) or any(b['artifacts']!=artifacts for b in bs):errors.append({'error':'inconsistent_or_duplicate_batches'})
    for batch in primary:
        for n in batch['ids']:
            cost={s:costs(*[batch['rows'][f'{s}-{n:02}-{p}'] for p in PHASES]) for s in SIDES}
            records.append({'index':n,'order':'AB' if n%2 else 'BA','ms_and_bytes':cost,'savings':{k:round(value-cost['candidate'][k],3) for k,value in cost['baseline'].items()}})
    target={'screen':6,'confirm':10}.get(phase);indices=sorted(r['index'] for r in records)
    if len(set(indices))!=len(indices) or (target and indices!=list(range(1,target+1))):errors.append({'error':'incomplete_planned_pairs'})
    sums={k:{'paired_savings_median':round(median(r['savings'][k] for r in records),3),
           'baseline_median':round(median(r['ms_and_bytes']['baseline'][k] for r in records),3),
           'candidate_median':round(median(r['ms_and_bytes']['candidate'][k] for r in records),3),
           'faster_pairs':sum(r['savings'][k]>0 for r in records),'paired_savings_range':[min(r['savings'][k] for r in records),max(r['savings'][k] for r in records)]}
           for k in records[0]['savings']} if records else {}
    valid=not errors and bool(primary);win=False;default=False;has_qual=any(b['mode']=='qualify' for b in bs)
    if valid and target:
        win=(sums['reopen_covers_ms']['paired_savings_median']>=100 and sums['reopen_covers_ms']['faster_pairs']>=target-1
             and sums['reopen_usable_ms']['paired_savings_median']>=-50)
        default=(win and sums['ingest_request_to_visible_ms']['paired_savings_median']>=-50 and sums['ingest_publish_ms']['paired_savings_median']>=-50
                 and sums['two_visible_waits_ms']['paired_savings_median']>=100 and sums['two_including_deferred_ms']['paired_savings_median']>=0
                 and sums['ingest_exit_ms']['paired_savings_median']>=-100 and sums['reopen_exit_ms']['paired_savings_median']>=-100)
    return {'schema_version':1,'phase':phase,'valid':valid,'input_errors':errors,'artifacts':artifacts,'complete_pairs':len(records),
            'physical_batches':[b['run_id'] for b in bs],'fixture_sha256':materialize.FIXTURE_SHA,'pairs':records,'summary':sums,
            'mechanism':{'verified':valid and all(b['rows'] for b in bs)},'qualification_passed':valid and has_qual,
            'latency_gate_passed':bool(valid and win),'default_gate_passed':bool(valid and default and has_qual and phase=='confirm'),
            'scope':'Twenty synthetic, two-page local archives. Controls on both sides: indexed document, wrapped launcher, same ELF, source/profile. Next open is a fresh process with a declared private title-sort fixture. Timings are software/driver boundaries. No CPU, energy, or ordinary population frequency inferred. The sum including deferred work combines nonoverlapping import/reopen phases; it is not a continuous user session.',
            'cases':{b['run_id']:b['rows'] for b in bs}}


def main():
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,action='append',required=True)
    p.add_argument('--qualification-batch',type=Path,action='append',default=[]);p.add_argument('--phase',choices=('pilot','screen','confirm'),required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();d=summarize(a.batch,a.qualification_batch,a.phase)
    a.output.parent.mkdir(exist_ok=True,parents=True);a.output.write_text(json.dumps(d,indent=2)+'\n');print(json.dumps({k:d[k] for k in ('phase','valid','complete_pairs','input_errors','latency_gate_passed','default_gate_passed')}))
    if not d['valid']:raise SystemExit(2)


if __name__=='__main__':main()
