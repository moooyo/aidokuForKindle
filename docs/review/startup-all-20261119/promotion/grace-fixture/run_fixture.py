#!/usr/bin/env python3
"""Build and replay a controlled host-only grace-boundary observation on test-env."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True

BASE=Path('/root/aidoku-all-grace-qualification-20261119')
HERE=Path(__file__).resolve().parent
CASES=('early','expiry','cancel','disabled','foreign','rounded-wake')
END={'early':['started','completed'],'expiry':['started','expired'],'rounded-wake':['started','expired'],
     'cancel':['started','cancelled'],'disabled':[],'foreign':[]}
TEST='app::cover_jobs::qualified_grace_observation::controlled_final_local_result'
CONTEXT='src/app/cover_jobs.rs'


def sha(b):return hashlib.sha256(b).hexdigest()
def check(ok,message):
    if not ok: raise ValueError(message)
def raw(p,maxsize=100*1024*1024):
    check(p.is_absolute() and p.resolve(strict=True)==p and p.is_file() and p.stat().st_size<=maxsize,'Invalid file '+p.name)
    return p.read_bytes()
def write(p, data):
    with p.open('xb') as f:f.write((json.dumps(data,sort_keys=True,indent=2,allow_nan=False)+'\n').encode())
def manifest_at(source):
    p=source/'scripts/verify-kindle-package.py';raw(p,128*1024)
    s=importlib.util.spec_from_file_location('fixture_source_manifest',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
    return m.source_manifest(source)


def prewarm_module():
    p=HERE/'controlled_prewarm.py';raw(p,96*1024)
    s=importlib.util.spec_from_file_location('checked_warm_fixture',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


def prewarm_rust():
    for location in (HERE/'prewarm-adapter.rs',HERE.parent/'prewarm-fixture/qualification.rs'):
        if location.is_file():return raw(location,128*1024)
    raise ValueError('Missing fixed warm adapter')


def exercise(binary, out):
    out.mkdir(mode=0o700)
    failed=[]
    for mode in CASES:
        env=dict(os.environ,AIDOKU_TEST_GRACE_CASE=mode,AIDOKU_TEST_GRACE_OUT=str(out/(mode+'.trace.json')))
        result=subprocess.run([binary,'--exact',TEST,'--test-threads=1','--nocapture'],env=env,cwd=out,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=20)
        (out/(mode+'.log')).write_bytes(result.stdout)
        if result.returncode or b'1 passed; 0 failed' not in result.stdout:failed.append(mode)
    check(not failed, 'Controlled observation failed: '+', '.join(failed))
    return summarize(out)


def summarize(out):
    checked={}
    for mode in CASES:
        trace=json.loads(raw(out/(mode+'.trace.json'),128*1024));obs=json.loads(raw(out/(mode+'.trace.observations.json'),4096))
        check(trace['finished'] is True and trace['success'] is True and trace['dropped_events']==0,'Incomplete trace: '+mode)
        relevant=[event for event in trace['events'] if event['name'].startswith('startup_cover_last_grace_')]
        seq=[e['name'].removeprefix('startup_cover_last_grace_') for e in relevant]
        check(seq==END[mode] and [e['at_us'] for e in relevant]==sorted(e['at_us'] for e in relevant),'Wrong grace transitions: '+mode)
        check(obs.get('case')==mode and obs['visible']==2 and obs['grace_limit_ms']==40 and obs['deferred_after_spent_timer'] is False
              and obs['rounded_delivery_us']==(39100 if mode=='rounded-wake' else None),'Invalid controlled setup: '+mode)
        check(obs['armed'] is (mode in ('early','expiry','rounded-wake','cancel')) and obs['wake_callbacks']==int(mode in ('expiry','rounded-wake')) and
              obs['publication_once_after_last_result'] is (mode in ('early','expiry','rounded-wake')) and obs['last_still_pending'] is (mode not in ('early','expiry','rounded-wake')),
              'Wrong publication or wake result: '+mode)
        check(b'1 passed; 0 failed' in raw(out/(mode+'.log'),64*1024),'Not an executed controlled test')
        checked[mode]={'events':seq,**obs}
    return checked


def seed(args):
    preview=args.operation=='preview-fix'
    both=args.operation=='combined'
    source=args.source;receipt=json.loads(raw(args.receipt));data=raw(args.application)
    check(sha(raw(args.receipt))==args.receipt_sha256 and receipt['program_sha256']['aidoku/app/aidoku']==sha(data),'Unbound build or ARM')
    original=manifest_at(source);check(original['sha256']==receipt['source_manifest_sha256'],'Source differs from production build')
    batch=BASE/'private'/(('grace-preview-' if preview else 'grace-')+uuid.uuid4().hex[:12]);batch.mkdir(mode=0o700,parents=True)
    clone=batch/'source';shutil.copytree(source,clone,symlinks=True,ignore=shutil.ignore_patterns('.git','target','dist','artifacts','docs','__pycache__'))
    check(manifest_at(clone)==original,'Isolated production inputs differ')
    grace=raw(HERE/'qualification.rs',65536)
    warm=prewarm_rust() if both else b''; fixture=grace+(b'\n'+warm if both else b'');initial=raw(source/CONTEXT)
    if preview:
        patch=HERE/'spent-timer.patch'; command=['patch','--batch','--forward','--fuzz=0','-p1','-i',str(patch)]
        subprocess.run([*command,'--dry-run'],cwd=clone,check=True)
        subprocess.run(command,cwd=clone,check=True)
    before=raw(clone/CONTEXT)
    check((initial!=before)==preview,'The preview patch was not applied exactly to the isolated source')
    with (clone/CONTEXT).open('ab') as f:f.write(b'\n'+fixture)
    changed=manifest_at(clone);after=raw(clone/CONTEXT)
    check(after==before+b'\n'+fixture and {i['path'] for i in original['files']}=={i['path'] for i in changed['files']} and
          [a['path'] for a,b in zip(original['files'],changed['files']) if a!=b]==[CONTEXT], 'Unexpected production-source difference')
    base={'schema':1,'run_id':batch.name,'source_root':str(source),'source_manifest_sha256':original['sha256'],
          'clone_manifest_sha256':changed['sha256'],'program_sha256':receipt['program_sha256'],'application':str(args.application),
          'receipt':str(args.receipt),'receipt_sha256':args.receipt_sha256,'original_cover_jobs_sha256':sha(before),
          'fixture_sha256':sha(fixture),'augmented_cover_jobs_sha256':sha(after),'builder_sha256':sha(raw(HERE/'build_fixture.sh')),
          'baseline_cover_jobs_sha256':sha(initial),'preview_only':preview,'combined_prewarm':both,'grace_adapter_sha256':sha(grace)}
    if both: base.update(prewarm_adapter_sha256=sha(warm),prewarm_analyzer_sha256=sha(raw(HERE/'controlled_prewarm.py')))
    if preview:base['unshipped_patch_sha256']=sha(raw(patch))
    (batch/'adapter.rs').write_bytes(fixture);(batch/'build_fixture.sh').write_bytes(raw(HERE/'build_fixture.sh'))
    write(batch/'preparation.json',base); print('PREPARED',batch,flush=True)
    subprocess.run(['sh',str(HERE/'build_fixture.sh'),str(batch)],check=True)
    observations=exercise(batch/'probe',batch/'original')
    result=base|{'test_elf_sha256':sha(raw(batch/'probe')),'cargo_records_sha256':sha(raw(batch/'cargo.jsonl',24*1024*1024)),
                 'observations':observations}
    if both: result['prewarm_observations']=prewarm_module().exercise(batch/'probe',batch/'prewarm-original')
    name='unshipped-preview.json' if preview else 'completed.json';write(batch/name,result)
    print(json.dumps({'batch':str(batch),'receipt_file':name,'sha256':sha(raw(batch/name)),'observations':len(observations),
                      'preview_only':preview}))


def verify(batch,proof_sha,feature='grace'):
    check(batch.parent==BASE/'private' and re.fullmatch('grace-[a-f0-9]{12}',batch.name),'Not a grace qualification batch')
    content=raw(batch/'completed.json',64*1024);check(sha(content)==proof_sha,'Completed observation changed');p=json.loads(content)
    source=Path(p['source_root']);original=manifest_at(source);clone=manifest_at(batch/'source')
    receipt=raw(Path(p['receipt']));c=json.loads(receipt);original_file=raw(source/CONTEXT);grace=raw(HERE/'qualification.rs',65536)
    both=p.get('combined_prewarm') is True
    check(feature in ('grace','prewarm') and (feature=='grace' or both),'Missing requested feature')
    warm=prewarm_rust() if both else b'';fixture=grace+(b'\n'+warm if both else b'')
    check(p['grace_adapter_sha256']==sha(grace) and (not both or (p['prewarm_adapter_sha256']==sha(warm) and
          p['prewarm_analyzer_sha256']==sha(raw(HERE/'controlled_prewarm.py')))), 'Functional witness version differs')
    check(p['schema']==1 and p['preview_only'] is False and p['run_id']==batch.name and sha(receipt)==p['receipt_sha256'] and
          c['program_sha256']==p['program_sha256'] and p['program_sha256']['aidoku/app/aidoku']==sha(raw(Path(p['application']))) and
          c['source_manifest_sha256']==original['sha256']==p['source_manifest_sha256'] and clone['sha256']==p['clone_manifest_sha256'],
          'Source and release provenance mismatch')
    check(sha(fixture)==p['fixture_sha256'] and sha(raw(HERE/'build_fixture.sh'))==p['builder_sha256'] and
          sha(original_file)==p['original_cover_jobs_sha256'] and raw(batch/'source'/CONTEXT)==original_file+b'\n'+fixture and
          sha(original_file+b'\n'+fixture)==p['augmented_cover_jobs_sha256'] and
          {i['path'] for i in original['files']}=={i['path'] for i in clone['files']} and
          [a['path'] for a,b in zip(original['files'],clone['files']) if a!=b]==[CONTEXT], 'Unqualified code or test source')
    cargo=raw(batch/'cargo.jsonl',24*1024*1024);check(sha(cargo)==p['cargo_records_sha256'] and sha(raw(batch/'probe'))==p['test_elf_sha256'],'Test build identity differs')
    rows=[json.loads(line) for line in cargo.splitlines()]
    built=[Path(r['executable']) for r in rows if r.get('reason')=='compiler-artifact' and
           r.get('manifest_path')==str(batch/'source/Cargo.toml') and r.get('profile',{}).get('test') and
           'lib' in r.get('target',{}).get('kind',[]) and r.get('executable')]
    check(len(built)==1 and built[0].is_relative_to(batch/'target') and
          sha(raw(batch/'upper'/built[0].relative_to(batch/'target')))==p['test_elf_sha256'],
          'Compiler-emitted artifact and the exercised test executable disagree')
    warm_tool=prewarm_module() if feature=='prewarm' else None
    stored=warm_tool.summarize(batch/'prewarm-original') if warm_tool else summarize(batch/'original')
    check(stored==p['prewarm_observations' if warm_tool else 'observations'],'Original qualified observations changed')
    with tempfile.TemporaryDirectory() as work:
        replayed=(warm_tool.exercise if warm_tool else exercise)(batch/'probe',Path(work)/'again')
    check(stored==replayed,'Controlled behavior did not reproduce')
    return {'schema':1,'run_id':batch.name,'completed_sha256':proof_sha,'source_manifest_sha256':p['source_manifest_sha256'],
            'program_sha256':p['program_sha256'],'test_elf_sha256':p['test_elf_sha256'],'fixture_sha256':p['fixture_sha256'],
            'auditor_sha256':sha(raw(Path(__file__).resolve())),'feature':feature,'platform':'test-env-linux-host',
            'scope':('Exact production source with a test-only adapter; primary-connection hints, validated derivative hits and ordinary cover consumption with a controlled SQLite lock and mutable-file negative controls.' if warm_tool else 'Exact production source with a test-only adapter; real application grace timer and publication functions, controlled synthetic result readiness.')+' No physical-frame or speed claim.',
            'observations':replayed,'functional_positive':True,'functional_negative':True,'latency_gate_passed':None,'performance_claim':False}


def main():
    check(sys.platform=='linux' and not sys.flags.optimize,'Run through ssh test-env');os.umask(0o077)
    x=argparse.ArgumentParser(description=__doc__); commands=x.add_subparsers(dest='operation',required=True)
    for action in ('run','combined','preview-fix'):
        s=commands.add_parser(action);s.add_argument('--source',type=Path,required=True);s.add_argument('--receipt',type=Path,required=True);s.add_argument('--receipt-sha256',required=True);s.add_argument('--application',type=Path,required=True)
    s=commands.add_parser('verify');s.add_argument('--batch',type=Path,required=True);s.add_argument('--completed-sha256',required=True);s.add_argument('--feature',choices=('grace','prewarm'),default='grace');s.add_argument('--output',type=Path,required=True)
    a=x.parse_args()
    if a.operation in ('run','combined','preview-fix'):seed(a)
    else:write(a.output,verify(a.batch,a.completed_sha256,a.feature));print(a.output)


if __name__=='__main__':main()
