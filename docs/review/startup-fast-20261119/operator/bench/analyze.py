#!/usr/bin/env python3
"""Read a frozen R3/R4 indexed-entry A/B on test-env; never opens a device connection."""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median
import sys
import tarfile

from PIL import Image, ImageChops


BASE = ('1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af',
        '412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675',
        'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50')
NQUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
CAPTURE = 'd728222e1363405564ae55f6ee32a850ac918cf6401cc013e0a84b50adf44c90'
SIDES = ('baseline', 'candidate')
HEX = re.compile(r'[0-9a-f]{64}\Z')
REGIMES = {'warm': ('warm-cache-v2','sealed-private-cache-v2'), 'cold':('empty-cache-v2','per-case-empty-cache-v2')}
POLICY = {'startup_config_portrait_shell','startup_config_prewarm_0','startup_config_last_cover_grace_off',
          'startup_config_import_thumbnail_off','startup_config_first_layout_optimized','startup_config_resident_off'}
ENDPOINTS = ('ready','first','input_ready','usable','first_art','covers','exit')
MB=1024**2


class Rejected(ValueError):
    pass


def need(condition, reason):
    if not condition: raise Rejected(reason)


def raw(path, max_size=32*MB):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size<=max_size, 'missing_or_unsafe_private_artifact')
    return path.read_bytes()


def loads(data):
    def pairs(xs):
        obj=dict(xs);need(len(obj)==len(xs),'duplicate_json_key');return obj
    val=json.loads(data,object_pairs_hook=pairs);need(isinstance(val,dict),'json_not_object');return val


def obj(path): return loads(raw(path))
def digest(data): return sha256(data).hexdigest()
def pos(v): return type(v) is int and v>0
def nat(v): return type(v) is int and v>=0
def ms(n): return round(n/1000,3)
def r(v): return round(v,3)


@lru_cache(maxsize=1)
def native_tools():
    # Module is the read-only, whitelisted stock-UI verifier. Never import or
    # execute a caller-supplied device runner, even for its helper functions.
    import importlib.util
    path=Path(__file__).resolve().with_name('tile_evidence.py')
    spec=importlib.util.spec_from_file_location('r4_stock_evidence',path)
    need(spec is not None and spec.loader is not None, 'stock_verifier_not_available')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def once(source, old, new):
    need(source.count(old)==1,'different_launcher_instrumentation_anchor')
    return source.replace(old,new,1)


def instrument(data):
    """Independent byte recomposition of the frozen, symmetric minimal instrumentation."""
    text=data.decode('utf-8'); need('\r' not in text,'noncanonical_launcher_text')
    text=once(text,'DATA_DIR=$AIDOKU_HOME/data','DATA_DIR=$AIDOKU_RESEARCH_DATA')
    marks='''bench_mark() {
    IFS=' ' read -r bench_stamp bench_unused </proc/uptime
    printf '%s %s\\n' "$bench_stamp" "$1" >>"$AIDOKU_RESEARCH_OUT/shell.tsv"
}
if [ "${1:-}" = --foreground ]; then bench_mark launcher_worker; else bench_mark launcher_entry; fi
'''
    text=once(text,'ROOT_PID=$$\n','ROOT_PID=$$\n'+marks)
    tail=' --offline --performance-trace "$AIDOKU_RESEARCH_OUT/application.json" --state-output "$AIDOKU_RESEARCH_OUT/state.json" --exit-after-ms "$AIDOKU_RESEARCH_DWELL" $AIDOKU_RESEARCH_CAPTURE_ARGS'
    lines=text.split('\n'); hits=0
    for i,line in enumerate(lines):
        if '"$APP_DIR/aidoku" --kindle' in line and '--data-dir "$DATA_DIR"' in line:
            line=once(line,'--data-dir "$DATA_DIR"','--data-dir "$DATA_DIR"'+tail)
            lines[i]=once(line,'3>&- &','3>&- 2>"$AIDOKU_RESEARCH_OUT/native.log" &'); hits+=1
    need(hits==2,'missing_launch_mode'); return '\n'.join(lines).encode()


def bridge(run, names, regime):
    dwell, pic=(8000,12500) if regime=='warm' else (20000,24000)
    return f'''#!/bin/sh
set -eu
[ "$#" -eq 1 ] && [ "$1" = --library ] || exit 64
[ "${{AIDOKU_CATALOG_NOTIFY_HELPER:-}}" = /var/local/kindle-catalog-entry/v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86/aidoku-catalog-client ] && [ "${{AIDOKU_CATALOG_NOTIFY_SHA256:-}}" = f46a0225d5a265f5f9749029fc115060576508ee5caadf25becb661738b88161 ] || exit 69
stage=/mnt/us/.{run}
[ "$(cat "$stage/owner")" = {run} ] || exit 73
case_id=$(cat "$stage/case")
case "$case_id" in {'|'.join(names)}) ;; *) exit 64;; esac
variant=${{case_id%%-*}}
AIDOKU_HOME="$stage/$variant"; AIDOKU_RESEARCH_DATA="$stage/cases/$case_id/data"; AIDOKU_RESEARCH_OUT="$stage/out/$case_id"
AIDOKU_RESEARCH_DWELL={dwell}; AIDOKU_RESEARCH_CAPTURE_ARGS=''; AIDOKU_NATIVE_REFRESH_TRACE=0
case "$case_id" in *-warmup) AIDOKU_RESEARCH_DWELL=20000;; *-qualify) AIDOKU_RESEARCH_DWELL={pic}; AIDOKU_RESEARCH_CAPTURE_ARGS="--capture $AIDOKU_RESEARCH_OUT/final.png --capture-settle-covers";; esac
unset AIDOKU_STARTUP_PORTRAIT
case "$variant" in baseline) :;; candidate) :;; esac
export AIDOKU_HOME AIDOKU_RESEARCH_DATA AIDOKU_RESEARCH_OUT AIDOKU_RESEARCH_DWELL AIDOKU_RESEARCH_CAPTURE_ARGS AIDOKU_NATIVE_REFRESH_TRACE
IFS=' ' read -r stamp skip </proc/uptime
printf '%s %s\\n' "$stamp" launcher_bridge >>"$AIDOKU_RESEARCH_OUT/shell.tsv"
exec sh "$AIDOKU_HOME/launch.sh" --library
'''.encode()


def order(phase):
    cases=['baseline-warmup','candidate-warmup']
    for i in range(1,(6 if phase=='screen' else 2)+1):
        cases.extend(f'{side}-{i:02}' for side in (SIDES if i%2 else tuple(reversed(SIDES))))
    return cases+['baseline-qualify','candidate-qualify']


def regular(m):
    return m.type in (tarfile.REGTYPE,tarfile.AREGTYPE) and not m.sparse and not m.linkname and not any('sparse' in k.lower() for k in m.pax_headers)


def planguard(root, phase, regime, plan_hash, cand, only=False, entry='uri'):
    p=raw(root/'plan.json',65536); need(digest(p)==plan_hash,'plan_digest_changed'); plan=loads(p)
    run=plan['run_id']; names=order(phase); need(root.name==run and re.fullmatch(r'aidoku-all-[a-z][a-z0-9-]{0,12}-[a-f0-9]{8}',run),'run_mismatch')
    need(plan['cases']==names and plan['mode']==phase and plan['primary']==('first' if regime=='warm' else 'covers') and plan['trial']=='combined' and
         (plan['cache_regime'],plan['fixture_policy'])==REGIMES[regime] and plan['fixture_case_seed']=='independent' and
         plan['baseline_reference_sha256']==BASE[0] and plan['original_launcher_sha256']==BASE[1], 'wrong_plan_or_case_order')
    receipt=obj(root/'freeze-receipt.json'); need(receipt.get('plan_sha256')==plan_hash and receipt.get('regime')==regime and receipt.get('mode')==phase and
        receipt.get('pairs')==(6 if phase=='screen' else 2) and receipt.get('candidate_input_receipt')==dict(zip(('app','launcher','portrait'),cand))
        and receipt.get('device_accessed') is False,'preparation_receipt_mismatch')
    need(only or ('-dry-' not in run and cand!=BASE),'dry_plan_cannot_be_device_evidence')
    need(entry in ('tile','uri') and (plan.get('native_input')=='stock-library-XTEST' if entry=='tile' else not any(k in plan for k in ('native_input','tile_targets_sha256','tile_controller_sha256'))), 'wrong_native_entry_mode')
    for filename,key in [('device.sh','device_script_sha256'),('runner.py','runner_sha256')]:
        need(digest(raw(root/filename,2*MB))==plan[key],'frozen_harness_changed')
    need(digest(raw(root/'original-launcher',512*1024))==BASE[1],'baseline_launcher_backup_mismatch')
    owner=(run+'\n').encode(); disp=bridge(run,names,regime); need(digest(owner)==plan['owner_hash'] and digest(disp)==plan['bridge_sha256'],'owner_or_bridge_changed')
    expected={'owner':digest(owner),'bridge.sh':digest(disp),'capture':CAPTURE}; source={}
    if entry=='tile': expected['guitap']='2df118aad6346f8835224cabe50e27d56ddfe97a65a60ac5ff99ea707198f3c8'
    for side,ids in zip(SIDES,(BASE,cand)):
        app,launch,helper=ids; variant=plan['variants'][side]; fields={'application_sha256':app,'launcher_sha256':launch,'native_query_sha256':NQUERY,
          'options_sha256':digest(b''),'extra_sha256':{'aidoku-startup-portrait':helper},'args':[],'env':{},'startup_options':{}}
        fields['variant_sha256']=digest(json.dumps(fields,sort_keys=True,separators=(',',':')).encode()); need(fields==variant,'variant_defaults_or_identity_changed')
        expected[side+'/source-launch.sh']=launch; expected[side+'/app/aidoku-startup-portrait']=helper
        expected[side+'/bench.options']=digest(b'');expected[side+'/bench.options.sha256']=digest((digest(b'')+'\n').encode())
        expected[side+'/binary-hash']=digest((app+'\n').encode()); expected[side+'/binary-ref']=digest((('installed' if app==BASE[0] else app)+'\n').encode())
        if app!=BASE[0]: expected['bin-'+app]=app
    need(set(plan['variants'])==set(SIDES),'unexpected_variant')
    f=root/'payload.tar.gz'; need(digest(raw(f,98*MB))==plan['payload_sha256'],'payload_digest_changed')
    meta={}; seen={}; staged={}; allow=set(expected)|{'inputs.sha256','baseline/launch.sh','candidate/launch.sh'}
    with tarfile.open(f,'r:gz') as tar:
        for m in tar:
            n=m.name;need(regular(m) and n in allow and n not in seen and 0<=m.size<=96*MB,'unsafe_payload_member')
            with tar.extractfile(m) as s: data=s.read(m.size+1)
            need(len(data)==m.size,'truncated_payload_member');seen[n]=digest(data)
            if n.endswith('/source-launch.sh'): source[n.split('/')[0]]=data
            if n in ('baseline/launch.sh','candidate/launch.sh'): staged[n.split('/')[0]]=data
            if n=='inputs.sha256':
                lines=data.decode('ascii').splitlines(); meta={}
                for line in lines:
                    h,k=line.split('  ');need(k not in meta and HEX.fullmatch(h),'bad_payload_inventory');meta[k]=h
    need(set(seen)==allow,'missing_payload_member')
    for side in SIDES:
        calc=instrument(source[side]); need(calc==staged[side],'unexpected_instrumented_launcher'); expected[side+'/launch.sh']=digest(calc)
    need(expected==meta=={k:v for k,v in seen.items() if k!='inputs.sha256'},'payload_inventory_mismatch')
    if entry=='tile':need(native_tools().plan_audit(root,plan,seen) is True,'stock_transport_not_bound')
    return plan


def archive_guard(root,names):
    arch=root/'outputs.tar.gz';need(digest(raw(arch,128*MB))==digest(raw(root/'collect.stdout',128*MB)),'returned_archive_changed')
    files=set(); dirs=set(); valid=set(names); bound=root/'outputs'
    with tarfile.open(arch,'r:gz') as tar:
        for m in tar:
            parts=m.name.split('/');need(parts[0]=='out' and len(parts)<=3 and (len(parts)==1 or parts[1] in valid),'unexpected_output_member')
            if m.isdir():
                need(len(parts)<=2 and m.name not in dirs,'bad_output_directory');dirs.add(m.name);continue
            need(len(parts)==3 and regular(m) and re.fullmatch(r'[a-zA-Z0-9_.-]+',parts[2]) and parts[2] not in ('.','..') and m.name not in files and m.size<=32*MB,'unsafe_output_member')
            with tar.extractfile(m) as stream: need(stream.read(m.size+1)==raw(bound/m.name),'extracted_output_changed')
            files.add(m.name)
    actual={x.relative_to(bound).as_posix() for x in (bound/'out').rglob('*') if x.is_file() or x.is_symlink()}
    actual_dirs={'out'}|{x.relative_to(bound).as_posix() for x in (bound/'out').rglob('*') if x.is_dir()}
    need(actual==files and dirs==actual_dirs=={'out'}|{'out/'+n for n in names},'extra_or_missing_extracted_evidence')


def opguard(root, plan, entry='uri'):
    result=obj(root/'report.json'); need(all(result.get(k)==v for k,v in plan.items()),'actual_report_not_frozen_plan')
    need(all(result.get(k) is True for k in ('success','lease_released','stage_started','cases_completed','entry_restored','installed_and_user_unchanged',
         'input_roster_unchanged','same_boot','native_restored','temporary_stage_absent')) and result.get('case_exception') is None and result['completed']==plan['cases'], 'operation_or_restoration_failed')
    commands=result['commands']; cmd=[c['name'] for c in commands];need(len(set(cmd))==len(cmd) and all(c.get('returncode')==0 and not c.get('timeout') for c in commands),'failed_guarded_command')
    ops=['before','create-stage','upload','prepare-device','original-program-backup','activate-bridge']+['case-'+n for n in plan['cases']]+['owned-stage-status','repair-launcher','collect','restore-light','after','remove-owned-stage']
    if entry=='tile':
        need(result.get('stock_home_pointer_restored') is True,'stock_home_and_pointer_not_restored')
        native_tools().operations_audit(root,plan,cmd)
    else:need(cmd==ops,'missing_or_unplanned_guarded_operation')
    before=raw(root/'before.stdout',4*MB);need(before and before==raw(root/'after.stdout',4*MB),'protected_snapshot_changed')
    need(digest(raw(root/'original-program-backup.stdout',512*1024))==BASE[1],'original_program_backup_changed')
    acq,rel=[obj(root/('lease-'+k+'.json')) for k in ('acquired','released')]
    need(acq['ok'] is True and rel['ok'] is True and acq['run_id']==rel['run_id']==plan['run_id'] and all(acq[k]==rel[k] for k in ('project','owner_thread','lease_token_sha256')) and
         all(rel.get(k) is True for k in ('owner_and_token_checked','native_restored_and_connections_closed','only_owned_owner_json_removed_then_rmdir','lease_directory_absent_after_release')),'lease_proof_incomplete')
    for n in cmd:
        need((root/(n+'.stdout')).is_file() and (root/(n+'.stderr')).is_file(),'guarded_output_missing')
    archive_guard(root,plan['cases'])


def seed(d, regime):
    need(raw(d/'startup-options-presence')==b'absent\n' and raw(d/'startup-options.sha256')==(digest(b'')+'\n').encode(),'unauthorized_saved_settings')
    if regime=='warm':
        value=raw(d/'fixture.sha256');need(raw(d/'fixture-name')==b'warm-cache-v2\n' and re.fullmatch(rb'[a-f0-9]{64}\n',value),'invalid_warm_fixture'); return value
    need(raw(d/'cold-fixture')==b'empty-cache-v1\n' and raw(d/'cold-counts')==b'6 0\n','cold_fixture_not_empty')
    text=raw(d/'seed.sha256');need(re.fullmatch(rb'[a-f0-9]{64}  library\.sqlite3\n[a-f0-9]{64}  device-profile\n',text),'bad_cold_seed')
    value=raw(d/'cold-fixture.sha256');need(value==(digest(text)+'\n').encode(),'invalid_cold_fixture');return value


def settle(s):
    need(s.get('route')=='library' and s['library_count']==6 and s['cover_visible_count']==6 and s['cover_visible_failure_count']==0
         and s['cover_inflight'] is False and s['cover_work_pending'] is False and s['cover_pending_count']==s['cover_queued_count']==0,'unfinished_or_incorrect_library')


def case(d, regime, entry='uri'):
    tag=d.name.split('-')[-1]; need(raw(d/'case-ok')==b'ok\n','case_guard_failed'); seed_value=seed(d,regime)
    if tag!='qualify':need(not any((d/f).exists() for f in ('final.png','native-settled.ppm','system.csv','final.metadata.json')),'nonqualifying_case_instrumented')
    stamps={}
    for line in raw(d/'shell.tsv',8192).decode('ascii').splitlines():
        value,key=line.split();need(key not in stamps and re.fullmatch(r'[0-9]+\.[0-9]{1,6}',value),'invalid_shell_timing');stamps[key]=int(Decimal(value)*1000000)
    shell=[stamps[k] for k in ('native_open_requested','launcher_bridge','launcher_entry','launcher_worker','native_restored','native_home_active')]; need(shell==sorted(shell),'native_clock_order')
    t=obj(d/'application.json'); need(t.get('clock')=='monotonic_rust_main' and t['enabled'] is True and t['finished'] is True and t['success'] is True and t['dropped_events']==0,'incomplete_trace')
    events=t['events']; need(all(isinstance(e,dict) and isinstance(e.get('name'),str) and nat(e.get('at_us')) for e in events) and all(a['at_us']<=b['at_us'] for a,b in zip(events,events[1:])), 'invalid_application_elapsed_clock')
    by=defaultdict(list)
    for e in events: by[e['name']].append(e)
    def single(k): need(len(by[k])==1,'missing_or_duplicate_'+k);return by[k][0]
    need(single('process_entry')['at_us']==0,'missing_process_entry')
    keys={'first':'startup_library_frame_submitted','input_ready':'startup_library_input_ready','ready':'startup_ready','covers':'startup_visible_covers_settled','stop':'shutdown_started'}
    endpoints={k:single(n) for k,n in keys.items()}
    def clk(e): n=e.get('boottime_us');need(pos(n),'missing_boottime');return n
    def frame(e):
        need(pos(e.get('marker')) and pos(e.get('frame_sequence')) and e.get('cover_slots')==7 and nat(e.get('cover_images')) and e['cover_images']<=7 and isinstance(e.get('cover_pending'),bool),'unaccepted_or_unbound_library_frame')
    first,inp,complete=[endpoints[k] for k in ('first','input_ready','covers')]; sequence=by['startup_cover_frame_submitted'];need(sequence,'no_library_frames')
    for e in sequence+[first,inp,complete]:frame(e)
    need(all(a['frame_sequence']<b['frame_sequence'] and a['marker']!=b['marker'] and clk(a)<=clk(b) and a['cover_images']<=b['cover_images'] for a,b in zip(sequence,sequence[1:])),'inconsistent_library_frame_sequence')
    def fields(e):return tuple(e[k] for k in ('marker','frame_sequence','cover_slots','cover_images','cover_pending'))
    need(fields(first)==fields(sequence[0]) and fields(complete)==fields(sequence[-1]) and (complete['cover_images'],complete['cover_pending'])==(7,False) and clk(first)<=clk(inp) and
         inp['frame_sequence']>=first['frame_sequence'] and any((inp['frame_sequence'],inp['marker'])==(f['frame_sequence'],f['marker']) for f in sequence),'invalid_ready_or_settled_frame')
    initial=single('startup_initial_frame_submitted');barrier=single('startup_initial_input_ready')
    need(initial.get('full') is True and pos(initial.get('marker')) and clk(initial)<=clk(barrier) and barrier.get('marker')==initial['marker'],'checked_initial_fence_missing')
    clocks={k:clk(e) for k,e in endpoints.items()}; clocks['first_art']=clk(next(f for f in sequence if f['cover_images']>0)); clocks['usable']=max(clocks['first'],clocks['input_ready'])
    request=shell[0]; stop=clocks['stop'];need(request<=shell[3]<=min(clocks.values()) and max(clocks.values())<=stop<=shell[-1] and clocks['first']<=clocks['first_art']<=clocks['covers'],'endpoints_outside_launch')
    if tag=='qualify': need(clocks['covers']<=stamps['native_capture_started']<=stop,'physical_capture_before_complete_covers');settle(obj(d/'final.state.json'))
    settle(obj(d/'state.json'))
    policy=[e['name'] for e in events if e['name'].startswith('startup_config_')]; need(len(policy)==len(POLICY) and set(policy)==POLICY,'production_startup_policy_changed')
    roles={'cover_preparation_started':'start','cover_preparation_finished':'finish','cover_local_derivative_reused':'hit','cover_local_derivative_prepared':'miss'}; counts=Counter()
    for e in events:
        if e['name'] not in roles: continue
        need(request<=clk(e)<=clocks['covers'],'visible_job_outside_startup');counts[roles[e['name']]]+=1
        need(counts['finish']<=counts['hit']+counts['miss']<=counts['start'],'unbalanced_visible_work')
    need(counts['start']==counts['finish']==counts['hit']+counts['miss']==6,'incomplete_six_visible_jobs')
    if not (regime=='warm' and d.name=='baseline-warmup'):
        need(counts['hit']==(6 if regime=='warm' else 0),'wrong_cache_regime')
    identity=raw(d/'catalog-before.identity'); need(identity.strip() and len(identity.splitlines())==1 and raw(d/'catalog-after.identity')==identity,'indexed_identity_changed')
    before,after=[raw(d/('catalog-'+k+'.access')).strip() for k in ('before','after')]; need(all(re.fullmatch(rb'[0-9]{1,12}',n) for n in (before,after)) and int(after)>int(before),'recent_access_did_not_advance')
    log=raw(d/'launcher.log').splitlines(); need(log.count(b'aidoku: Catalog notification completed.')==1 and not any(b'Catalog notification unavailable' in x or b'standard reindex recovery' in x for x in log),'catalog_notification_failed')
    tap=None; boundaries={}
    if entry=='tile':
        tap=native_tools().origin(d,stamps)
        for label in ('down','up'):
            low,high=tap[label];need(low<=high<min(clkval for k,clkval in clocks.items() if k!='stop'), 'tap_outside_gui_timeline')
            boundaries[label]={k:[ms(clkval-high),ms(clkval-low)] for k,clkval in clocks.items() if k!='stop'}
        # T0 before the native server acknowledges DOWN is earliest (thus a
        # conservative upper time). Never count helper spawn or its pointer move.
        request=tap['down'][0]
    v={k:ms(clkval-request) for k,clkval in clocks.items() if k!='stop'};v['exit']=ms(shell[-1]-stop)
    # Monotonic offsets can still attribute the covered part of initial SEND
    # even if a future valid Library frame doubles as the checked full frame.
    end=complete['at_us'];first_send=initial['at_us'];first_input=barrier['at_us']
    mechanism={k:sum(e['at_us']<=end for e in by[k]) for k in ('startup_fence_fonts_ready','startup_fence_application_attached',
        'cover_cold_validation_combined_started','cover_cold_decode_validated','cover_cold_validation_fallback','cover_local_derivative_memory_hit')}
    rust=clk(initial)-first_send
    phase={'request_to_rust_entry_approx':ms(rust-request),'worker_to_rust_entry_approx':ms(rust-shell[3]),
           'rust_entry_to_initial_send':ms(first_send),'initial_send_to_initial_software_input':ms(first_input-first_send),
           'initial_send_to_library_send':ms(first['at_us']-first_send),'rust_entry_to_first_library_send':ms(first['at_us']),
           'library_send_to_complete_covers':ms(end-first['at_us']), 'first_to_last_visible_job_span':ms(by['cover_preparation_finished'][-1]['at_us']-by['cover_preparation_started'][0]['at_us'])}
    row={'case':d.name,'endpoints_ms':v,'hits':counts['hit'],'misses':counts['miss'],'jobs':counts['start'],'first_slots':first['cover_images'],
            'submitted_slots':[f['cover_images'] for f in sequence], 'first_was_initial_full':first['marker']==initial['marker'],
            'phase_intervals_ms':phase,'mechanism_counts':mechanism,
            'request_to_bridge_ms':ms(shell[1]-request),'launcher_entry_to_worker_ms':ms(shell[3]-shell[2]),'after_first_to_covers_ms':ms(clocks['covers']-clocks['first'])}
    if tap:
        row['native_input']={'down_up_endpoint_bounds_ms':boundaries,'down_clock_width_ms':ms(tap['down'][1]-tap['down'][0]),
            'up_clock_width_ms':ms(tap['up'][1]-tap['up'][0]),'button_hold_bounds_ms':[ms(tap['up'][0]-tap['down'][1]),ms(tap['up'][1]-tap['down'][0])]}
    return row,seed_value,identity


def pixels(root):
    sides=[root/'outputs'/'out'/(s+'-qualify') for s in SIDES]; results={}
    for key,leaf,format_ in [('physical','native-settled.ppm','PPM'),('software','final.png','PNG')]:
        imgs=[]
        for d in sides:
            meta=obj(d/'final.metadata.json'); need(meta['route']=='library' and meta['profile']=='scribe' and meta['backend']=='kindle' and meta['landscape'] is False and meta['rotation_degrees']==0 and meta['window_size']==[1860,2480],'unqualified_capture_profile')
            path=d/leaf; need(path.is_file() and not path.is_symlink() and path.stat().st_size<20*MB,'invalid_capture_file')
            if format_=='PPM':
                with path.open('rb') as f: need(f.read(36)==b'P6\n# bpp=8 rotation=3\n1860 2480\n255\n' and path.stat().st_size==13838436,'invalid_native_p6')
            with Image.open(path) as im:
                need(im.format==format_ and im.size==(1860,2480),'capture_size_changed'); imgs.append(im.convert('RGB'))
        a,b=imgs;body=(0,72,1860,2480);toolbar=(930,0,1860,72)
        def observable(im,roi):return any(lo!=hi for lo,hi in im.crop(roi).getextrema())
        results[key]={'body_equal':ImageChops.difference(a.crop(body),b.crop(body)).getbbox() is None,
            'body_nonuniform':all(observable(im,body) for im in imgs),'right_toolbar_equal':ImageChops.difference(a.crop(toolbar),b.crop(toolbar)).getbbox() is None,
            'right_toolbar_observable':all(observable(im,toolbar) for im in imgs)}
    return results


def cohort(rows, phase, regime, qualification):
    pairs=[]; n=6 if phase=='screen' else 2
    for i in range(1,n+1):
        a,b=(rows[f'{s}-{i:02}']['endpoints_ms'] for s in SIDES)
        pairs.append({'round':i,'order':'AB' if i%2 else 'BA','baseline_ms':a,'candidate_ms':b,
            'paired_savings_ms':{k:r(a[k]-b[k]) for k in ENDPOINTS}})
        if 'native_input' in rows[f'baseline-{i:02}']:
            base,test=[rows[f'{s}-{i:02}']['native_input']['down_up_endpoint_bounds_ms'] for s in SIDES]
            pairs[-1]['paired_savings_bounds_ms']={button:{k:[r(bounds[0]-test[button][k][1]),r(bounds[1]-test[button][k][0])] for k,bounds in ep.items()} for button,ep in base.items()}
    summaries={}
    for k in ENDPOINTS:
        a=[p['baseline_ms'][k] for p in pairs];b=[p['candidate_ms'][k] for p in pairs];delta=[p['paired_savings_ms'][k] for p in pairs]
        summaries[k]={'baseline_median_ms':r(median(a)),'candidate_median_ms':r(median(b)),
            'baseline_range_ms':[min(a),max(a)],'candidate_range_ms':[min(b),max(b)],'paired_median_savings_ms':r(median(delta)),
            'paired_median_relative_savings_pct':r(median(100*(x-y)/x for x,y in zip(a,b))) if min(a)>0 else None,'candidate_faster_pairs':sum(x>0 for x in delta)}
    primary,guard=('usable','covers') if regime=='warm' else ('covers','usable'); a=summaries[primary];b=summaries[guard]
    # Decide on exact median arithmetic, not the presentation's rounding at a boundary.
    primary_med=median(p['paired_savings_ms'][primary] for p in pairs)
    guard_med=median(p['paired_savings_ms'][guard] for p in pairs)
    gate=phase=='screen' and primary_med>=100 and a['candidate_faster_pairs']>=5 and guard_med>=-50
    survey={'pairs':pairs,'summary':summaries,'primary':primary,'paired_screen_passed':bool(gate) if phase=='screen' else None,
            'screen_passed':bool(gate and qualification) if phase=='screen' else None,
            'large_primary_20pct_descriptive':median(100*(p['baseline_ms'][primary]-p['candidate_ms'][primary])/p['baseline_ms'][primary] for p in pairs)>=20 if phase=='screen' else None,
            'latency_speed_claim':'screen_only_needs_confirmation' if gate and qualification else 'not_established'}
    if 'paired_savings_bounds_ms' in pairs[0]:
        survey['native_median_bounds_ms']={button:{} for button in ('down','up')}
        for button,table in survey['native_median_bounds_ms'].items():
            for k in ENDPOINTS:
                if k=='exit':continue
                table[k]={s:[r(median(rows[f'{s}-{i:02}']['native_input']['down_up_endpoint_bounds_ms'][button][k][c] for i in range(1,n+1))) for c in (0,1)] for s in SIDES}
                table[k]['paired_savings']=[r(median(p['paired_savings_bounds_ms'][button][k][c] for p in pairs)) for c in (0,1)]
    return survey


def analyze(root, phase, regime, plan_hash, cand, only, entry='uri'):
    plan=planguard(root,phase,regime,plan_hash,cand,only,entry);report={'schema':1,'regime':regime,'phase':phase,'plan_sha256':plan_hash,
      'expected_candidate':dict(zip(('app','launcher','portrait'),cand)),'frozen_artifacts_verified':True,'valid':False,'screen_passed':None,
      'scope':'Actual indexed native file-URI request to accepted Library frame/software input or accepted complete-cover frame; not a tap or optical measurement.'}
    if entry=='tile':report.update(native_entry='stock_library_xtest',scope='Actual stock Library tile XTEST DOWN-before to software/driver milestones using the instrumented indexed route; input acknowledgment bounds for both DOWN and UP are reported. Not a physical finger, unmodified installed data, or optical completion measurement.')
    if only:return {**report,'plan_only':True,'qualification_passed':None,'note':'No device or timing evidence has been judged.'}
    try: opguard(root,plan,entry)
    except (Rejected,OSError,ValueError,TypeError,KeyError,EOFError,tarfile.TarError) as e:
        # A failed batch is not evidence that any planned case succeeded. Keep the
        # original command outcomes without using unverified measurements to score it.
        try:
            observed=obj(root/'report.json'); outcomes=observed['commands'] if observed.get('run_id')==plan['run_id'] else []
        except (OSError,ValueError,TypeError,KeyError):outcomes=[]
        return {**report,'qualification_passed':False,'paired_screen_passed':None,'issues':[str(e) if isinstance(e,Rejected) else 'incomplete_batch_evidence'],
                'cases':[{'case':n,'command_outcome':next(('exited_zero_unqualified' if c.get('returncode')==0 else 'failed' for c in outcomes
                     if isinstance(c,dict) and c.get('name')=='case-'+n),'not_recorded')} for n in plan['cases']]}
    rows={}; seeds=set(); ids=set(); problems=[]
    for name in plan['cases']:
        try:
            row, seed_value, identity=case(root/'outputs'/'out'/name,regime,entry);rows[name]=row; ids.add(identity)
            if regime!='warm' or name!='baseline-warmup':seeds.add(seed_value)
        except (Rejected, KeyError, ValueError, OSError, StopIteration, TypeError) as e:
            problems.append({'case':name,'issue':str(e) if isinstance(e,Rejected) else 'missing_or_malformed_case_evidence'})
    if len(seeds)!=1 or len(ids)!=1:problems.append({'issue':'fixture_or_indexed_entry_differs_between_visits'})
    try: picture=pixels(root); qualified=all(all(v.values()) for v in picture.values())
    except (Rejected, OSError, ValueError, KeyError) as e:
        picture={'issue':str(e) if isinstance(e,Rejected) else 'missing_or_malformed_captures'};qualified=False
    report.update(restoration_and_release_verified=True,returned_archive_verified=True,cases=list(rows.values()),issues=problems,
                  pictures=picture,qualification_passed=qualified,complete_case_checks=not problems,
                  valid=not problems and qualified)
    if not problems:report.update(cohort(rows,phase,regime,qualified))
    else:
        report['pairs']=[]
        for i in range(1,(6 if phase=='screen' else 2)+1):
            a,b=(rows.get(f'{s}-{i:02}') for s in SIDES); item={'round':i,'order':'AB' if i%2 else 'BA','complete':bool(a and b)}
            if a and b:
                item.update(baseline_ms=a['endpoints_ms'],candidate_ms=b['endpoints_ms'],paired_savings_ms={k:r(a['endpoints_ms'][k]-b['endpoints_ms'][k]) for k in ENDPOINTS})
            report['pairs'].append(item)
    if not qualified:problems.append({'issue':'independent_software_or_physical_pixels_failed'})
    return report


def main(default_entry='uri'):
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--batch',type=Path,required=True);ap.add_argument('--plan-sha256',required=True)
    for k in ('app','launcher','portrait'):ap.add_argument('--candidate-'+k,required=True)
    ap.add_argument('--workload',choices=tuple(REGIMES),required=True);ap.add_argument('--phase',choices=('pilot','screen'),required=True)
    ap.add_argument('--entry',choices=('tile','uri'),default=default_entry)
    ap.add_argument('--verify-plan-only',action='store_true');ap.add_argument('--output',type=Path)
    a=ap.parse_args();cand=(a.candidate_app,a.candidate_launcher,a.candidate_portrait)
    if sys.platform!='linux' or not all(HEX.fullmatch(v) for v in (a.plan_sha256,*cand)):ap.error('Use test-env and exact SHA-256s')
    try: report=analyze(a.batch,a.phase,a.workload,a.plan_sha256,cand,a.verify_plan_only,a.entry)
    except (Rejected,OSError,ValueError,TypeError,KeyError,EOFError,tarfile.TarError) as e:
        report={'valid':False,'screen_passed':None,'issues':[str(e) if isinstance(e,Rejected) else 'unsafe_or_incomplete_frozen_inputs_or_evidence']}
    text=json.dumps(report,indent=2,allow_nan=False)+'\n'
    if a.output:
        with a.output.open('x') as out:out.write(text)
        print(json.dumps({k:report[k] for k in ('valid','plan_only','qualification_passed','paired_screen_passed','screen_passed','issues') if k in report}))
    else:sys.stdout.write(text)
    return 0 if report.get('valid') or report.get('plan_only') else 2


if __name__=='__main__':sys.exit(main())
