#!/usr/bin/env python3
"""Bind only the independent toolbar-status and cold-image production candidate."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
PRODUCER = Path('/root/aidoku-next-status-20261118')
SOURCE = PRODUCER/'source'
DEPOT = Path('/root/aidoku-startup-delivery-20261118')
RESULTS = Path('/root/aidoku-next-statusbench-20261118/reports')
OUT = Path('/root/aidoku-next-promotion-20261118/status-release-inputs')
ELF = PRODUCER/'artifacts/candidate-arm'
MEMBER = 'aidoku/app/aidoku'
ELF_SHA = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
SOURCE_SHA = '1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768'
WARM_SHA = '506045d231459baac6a78844fdcc0bd193044d4008d45b7837a7ef1357a7ebbb'
ORIGINAL_SOURCE = '0cae90b40762d6bb6b46da48dcbadc82999726b308dd7a7cbea6ba28bf08782c'
ORIGINAL_APP = '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
CHANGED = {'src/cache.rs','src/imaging.rs','src/render_cache.rs','src/app/view.rs','src/bootstrap_metrics.rs',
           'src/main.rs','src/platform.rs','src/platform_startup_toolbar.rs'}


def load(name, file):
    spec=importlib.util.spec_from_file_location(name,file); mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def check(condition, message):
    if not condition: raise ValueError(message)


def sha(path):
    check(path.is_absolute() and path.resolve(strict=True)==path and path.is_file(), 'Invalid file: '+path.name)
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def ref(path, expected=None):
    digest=sha(path);check(expected is None or expected==digest,'Input identity differs: '+path.name)
    return {'uri':path.as_uri(),'sha256':digest}


def write_once(name, obj):
    path=OUT/name; data=(json.dumps(obj,indent=2,allow_nan=False)+'\n').encode('utf-8')
    if path.exists():check(path.read_bytes()==data,'Existing record differs: '+name)
    else:
        with path.open('xb') as f: f.write(data)
    return path


def build_proof():
    v=load('status_release_source', SOURCE/'scripts/verify-kindle-package.py'); before=v.source_manifest(DEPOT/'source');live=v.source_manifest(SOURCE)
    check(before['sha256']==ORIGINAL_SOURCE and live['sha256']==SOURCE_SHA and live['file_count']==360,'Source snapshot differs')
    index=lambda m:{row['path']:row['sha256'] for row in m['files']};a,b=map(index,(before,live))
    check({n for n in a.keys()|b.keys() if a.get(n)!=b.get(n)}==CHANGED,'Unqualified source changed')
    check(all(f.is_file() for f in v.expected_inputs(SOURCE).values()),'Incomplete release source')
    arm=PRODUCER/'reports/arm-N63B161u'; host=PRODUCER/'reports/host-dmf7KzYG';pixels=PRODUCER/'reports/desktop-pixels-fay71y3c/pixel-report.json'
    ident=json.loads((arm/'identity.json').read_bytes())
    stage=json.loads((arm/'source-before.json').read_bytes())
    check(all(json.loads((loc/n).read_bytes())==stage for loc in (arm,host) for n in ('source-before.json','source-after.json')),
          'Build and test source snapshots disagree')
    check(ident['source_manifest_sha256']==stage['source_manifest_sha256']==SOURCE_SHA and ident['artifact_sha256']==ELF_SHA
          and ident['cargo_jobs']==6 and ident['build_kind']=='arm','Build evidence differs')
    for path, digest in ident['changed_sources'].items():
        check(b[path]==digest['candidate_sha256'] and a.get(path)==digest['baseline_sha256'], 'Build source differs: '+path)
    compare=json.loads(pixels.read_bytes())
    check(compare['passed'] is True and {r['profile'] for r in compare['comparisons']}=={'scribe','pw5'} and
          all(r['full_image_identical'] for r in compare['comparisons']), 'Demo Library qualification changed')
    check('825 passed; 0 failed; 6 ignored' in (host/'lib-tests.log').read_text() and
          '22 passed; 0 failed' in (host/'bin-tests.log').read_text(), 'Original suite evidence differs')
    refs=[ref(ELF,ELF_SHA),ref(PRODUCER/'upper/armv7-unknown-linux-musleabihf/release/aidoku',ELF_SHA),
          ref(arm/'identity.json'), ref(arm/'source-before.json'), ref(arm/'source-after.json'),
          ref(arm/'build.log',ident['build_log_sha256']),ref(arm/'cargo.jsonl',ident['compiler_artifact_event_log_sha256']),
          ref(host/'lib-tests.log'),ref(host/'bin-tests.log'),ref(pixels)]
    return write_once('build-attestation.json',{'source_manifest_sha256':SOURCE_SHA,'artifact_sha256':{MEMBER:ELF_SHA},'evidence':refs})


def measured_selection():
    proof=build_proof()
    refs={phase:ref(RESULTS/name) for phase,name in [('screen','status-cold-screen.json'),('confirm','status-cold-confirm.json'),('pixels','status-cold-pixels.json')]}
    warm=ref(RESULTS/'status-warm-guard.json',WARM_SHA)
    gate=load('status_data_gates',HERE/'prepare.py')
    used=set()
    for phase,r in [('warm',warm),*refs.items()]:
        d=gate.unique_json(gate.load_bound(r))
        check(d['valid'] is True and d['artifacts']['candidate']['application_sha256']==ELF_SHA and
              d['artifacts']['baseline']['application_sha256']==ORIGINAL_APP, 'Wrong measured application')
        if phase=='warm': runs=gate.warm_status_guard(d,{MEMBER:ELF_SHA})
        elif phase=='pixels':
            gate.qualify_status(d); runs={b['run_id'] for b in d['batches'] if b['mode']=='qualify'}
        else: runs=gate.freeze_gate(d,phase,'status',{MEMBER:ELF_SHA})
        check(runs and used.isdisjoint(runs),'Measurements reuse a device batch');used.update(runs)
    selection={'schema':1,'source_manifest_sha256':SOURCE_SHA,'artifacts':{MEMBER:ref(ELF,ELF_SHA)},
               'candidates':{'status':{'warm_guard':warm,'screen':refs['screen'],'confirm':refs['confirm']}},'qualification':refs['pixels']}
    return proof,write_once('measured-selection.json',selection)


def main():
    check(sys.platform=='linux' and not sys.flags.optimize,'Run on test-env')
    os.umask(0o077); OUT.mkdir(mode=0o700,parents=True,exist_ok=True)
    p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='operation',required=True)
    subs.add_parser('attest');subs.add_parser('freeze-default');f=subs.add_parser('package')
    f.add_argument('--default-plan',required=True,type=Path);f.add_argument('--default-plan-sha256',required=True)
    args=p.parse_args()
    if args.operation=='attest': print(json.dumps(ref(build_proof()))); return
    proof, chosen=measured_selection()
    if args.operation=='freeze-default':
        subprocess.run([sys.executable,'-I',str(HERE/'default_lifecycle.py'),'--source-root',str(SOURCE),
            '--selection',str(chosen),'--selection-sha256',sha(chosen),'--build-attestation',str(proof),
            '--build-attestation-sha256',sha(proof)],check=True); return
    plan=args.default_plan; ref(plan,args.default_plan_sha256); evidence=OUT/'lifecycle.json'
    if evidence.exists():
        analyzer=load('status_certification_replay',HERE/'analyze_lifecycle.py')
        check(analyzer.analyze(plan.parent,RESULTS/'status-cold-pixels.json',SOURCE,args.default_plan_sha256)
              ==json.loads(evidence.read_text()), 'Lifecycle observation changed')
    else:
        subprocess.run([sys.executable,'-I',str(HERE/'analyze_lifecycle.py'),'--batch',str(plan.parent),
            '--qualification',str(RESULTS/'status-cold-pixels.json'),'--source-root',str(SOURCE),
            '--plan-sha256',args.default_plan_sha256,'--output',str(evidence)],check=True)
    final=write_once('final-selection.json',json.loads(chosen.read_text())|{'lifecycle':ref(evidence)})
    subprocess.run([sys.executable,'-I',str(HERE/'prepare.py'),'--source-root',str(SOURCE),
                    '--selection',str(final),'--output',str(OUT/'package')],check=True)
    print(json.dumps(ref(OUT/'package/plan.json')))


if __name__=='__main__':main()
