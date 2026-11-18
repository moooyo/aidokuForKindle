#!/usr/bin/env python3
"""Bind the cold-only artifacts of this round. All commands are host-only."""

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESULTS = Path('/root/aidoku-next-coldbench-20261118/reports')
PRODUCER = Path('/root/aidoku-next-cold-20261118')
ROOT = Path('/root/aidoku-next-promotion-20261118/cold-release-inputs')
SOURCE = ROOT / 'source'
DEPOT = Path('/root/aidoku-startup-delivery-20261118')
ELF = PRODUCER / 'artifacts/candidate-arm'
MEMBER = 'aidoku/app/aidoku'
APP_SHA = 'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6'
SOURCE_SHA = '73f360f06b112c7f34a27b6f2ff90e5237d2146b9444aff0871bfcbafcea4549'
SCREEN_SHA = '9085926a683df3d5208b6bc55700193988041ec345f828ce3610bbe0fa7bedf7'
CONFIRM_SHA = 'ed0fbb1571fdebb411a5c5759296ddd2ca6f4c26dbbad888928b799ee8c6f998'
CHANGED = ('src/cache.rs','src/imaging.rs','src/render_cache.rs')


def sha(path):
    if not path.is_file() or path.resolve(strict=True) != path:
        raise ValueError('Expected canonical regular source: ' + path.name)
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def ref(path, expected=None):
    digest = sha(path)
    check(expected is None or digest == expected, 'Frozen identity differs: ' + path.name)
    return {'uri':path.as_uri(),'sha256':digest}


def write(name, record):
    path = ROOT/name
    data=(json.dumps(record,indent=2,allow_nan=False)+'\n').encode('utf-8')
    if path.exists():
        check(path.read_bytes()==data, 'Existing frozen record differs: ' + name)
    else:
        with path.open('xb') as f:
            f.write(data)
    return path


def attestation():
    spec=importlib.util.spec_from_file_location('release_source_verifier',SOURCE/'scripts/verify-kindle-package.py')
    v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
    manifest = v.source_manifest(SOURCE)
    check(manifest['sha256']==SOURCE_SHA and manifest==v.source_manifest(PRODUCER/'source'), 'Producer and full release sources differ')
    previous=v.source_manifest(DEPOT/'source')
    check(previous['sha256']=='0cae90b40762d6bb6b46da48dcbadc82999726b308dd7a7cbea6ba28bf08782c', 'Unqualified baseline')
    by_name=lambda m:{r['path']:r['sha256'] for r in m['files']};old,new=map(by_name,(previous,manifest))
    check(old.keys()==new.keys() and tuple(k for k in old if old[k]!=new[k])==CHANGED, 'Unexpected source changes')
    for path in CHANGED:
        for directory in (PRODUCER/'proof',PRODUCER/'review'):
            check(sha(directory/path)==new[path], 'Independent frozen source differs')
    check(all(f.is_file() for f in v.expected_inputs(SOURCE).values()), 'Incomplete package sources')
    compile_log = PRODUCER/'reports/arm-qualified.log'; tests = PRODUCER/'reports/library-suite-qualified.log'
    check('Finished `release` profile [optimized]' in compile_log.read_text() and
          '822 passed; 0 failed; 6 ignored' in tests.read_text(), 'Unexpected producer build or suite')
    actual=PRODUCER/'upper/armv7-unknown-linux-musleabihf/release/aidoku'
    compiler_elf=ref(actual,APP_SHA); copied_elf=ref(ELF,APP_SHA)
    source_record=write('producer-source-crosscheck.json',{
        'source_manifest_sha256':SOURCE_SHA,'previous_source_manifest_sha256':previous['sha256'],
        'files_checked':manifest['file_count'],'changed_source_sha256':{k:new[k] for k in CHANGED},
        'compiler_output':compiler_elf,'frozen_candidate':copied_elf,
        'scope':'Final compiler target compared with the device candidate; project inputs compared with the original build directory and two retained source replays. No new Rust build.'})
    return write('build-attestation.json',{'source_manifest_sha256':SOURCE_SHA,'artifact_sha256':{MEMBER:APP_SHA},
                'evidence':[compiler_elf,ref(source_record),ref(compile_log),ref(tests),ref(PRODUCER/'reports/candidate.patch')]})


def selection():
    path=attestation()
    choose={'schema':1,'source_manifest_sha256':SOURCE_SHA,'artifacts':{MEMBER:ref(ELF,APP_SHA)},
            'candidates':{'cold':{'screen':ref(RESULTS/'cold-screen.json',SCREEN_SHA),
                                  'confirm':ref(RESULTS/'cold-confirm.json',CONFIRM_SHA)}},
            'qualification':ref(RESULTS/'cold-pixels.json')}
    return path,write('measured-selection.json',choose)


def main():
    check(sys.platform=='linux' and not sys.flags.optimize, 'Run on test-env')
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__);subs=p.add_subparsers(dest='operation',required=True)
    subs.add_parser('attest');subs.add_parser('freeze-default');f=subs.add_parser('package')
    f.add_argument('--default-plan',required=True,type=Path);f.add_argument('--default-plan-sha256',required=True)
    args=p.parse_args()
    if args.operation=='attest':
        proof=attestation(); print(json.dumps(ref(proof))); return
    # This pinned ELF regressed warm usable-frame latency. Preserve the earlier
    # offline plan as evidence, but do not freeze or package this candidate.
    guard = Path('/root/aidoku-next-bench-20261118/reports/cold-warm-guard.json')
    ref(guard, 'ee33c50e3d13d96e3974bec1830f9e2be428f2ba0ad46f23bbafbe4884945f32')
    warm = json.loads(guard.read_bytes())
    check(warm['valid'] is True and warm['complete_pairs'] == 6 and
          warm['artifacts']['candidate']['application_sha256'] == APP_SHA, 'Unexpected warm regression evidence')
    check(warm['comparisons_ms']['usable']['paired_savings_ms']['median'] >= -50,
          'Cold-only d9be7683 is ineligible: its six-pair warm usable-frame median regressed by 67.421 ms')
    proof, chosen = selection()
    if args.operation=='freeze-default':
        subprocess.run([sys.executable,'-I',str(HERE/'default_lifecycle.py'),'--source-root',str(SOURCE),
            '--selection',str(chosen),'--selection-sha256',sha(chosen),'--build-attestation',str(proof),
            '--build-attestation-sha256',sha(proof)],check=True);return
    plan=args.default_plan; ref(plan,args.default_plan_sha256)
    evidence=ROOT/'lifecycle.json'
    subprocess.run([sys.executable,'-I',str(HERE/'analyze_lifecycle.py'),'--batch',str(plan.parent),
        '--qualification',str(RESULTS/'cold-pixels.json'),'--source-root',str(SOURCE),
        '--plan-sha256',args.default_plan_sha256,'--output',str(evidence)],check=True)
    final=write('final-selection.json',json.loads(chosen.read_text()) | {'lifecycle':ref(evidence)})
    subprocess.run([sys.executable,'-I',str(HERE/'prepare.py'),'--source-root',str(SOURCE),
                    '--selection',str(final),'--output',str(ROOT/'package')],check=True)
    print(json.dumps(ref(ROOT/'package/plan.json')))


if __name__ == '__main__':
    main()
