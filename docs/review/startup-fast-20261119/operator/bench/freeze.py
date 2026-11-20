#!/usr/bin/env python3
"""Freeze isolated R3-versus-candidate indexed Library benchmarks on test-env.

This program has no device operation. The sole operator separately invokes
the frozen batch/runner.py run batch/plan.json only when ready for the lease.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile


ROOT = Path('/root/aidoku-startup-perf-r4-bench-20261119')
GUI = False
OLD = Path('/root/aidoku-all-bench-20261119')
R3 = Path('/root/aidoku-all-build-20261119/artifacts/final-v3-clean')
CREATOR = Path(__file__).resolve()
APP = '1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
LAUNCH = '412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675'
HELPER = 'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50'
CAPTURE = 'd728222e1363405564ae55f6ee32a850ac918cf6401cc013e0a84b50adf44c90'
REFERENCE_APP = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
REFERENCE_LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
OLD_INPUTS = {'warm': ('a6270d6a2d2a78ad7dd1dd59819154dee2f091f85b8bb47bdf7a819fe3711ea6','b81b54db195ec3e0de03f0374e16c614fe75e43b424b918c31e4be1648944f4a'),
              'cold': ('bcc36a9ce043af28b4d077e30280de251d47157a08e9c4816786c658f56c5883','baacc109dcbc1716f5bfca4471627b8fc55961da1b203b580e426f6a31f91e40')}


def sha(b): return hashlib.sha256(b).hexdigest()


def require(value, message):
    if not value: raise ValueError(message)


def once(text, old, new):
    require(text.count(old) == 1, 'Changed source anchor: ' + old[:65]); return text.replace(old, new, 1)


def read(path, expected=None, max_bytes=96*1024**2):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= max_bytes, 'Unsafe or missing input: ' + path.name)
    data=path.read_bytes()
    if expected: require(sha(data)==expected,'Wrong identity: '+path.name)
    return data


def write(path, data):
    if path.exists(): require(read(path)==data,'Different frozen file at '+str(path)); return
    with path.open('xb') as f: f.write(data)


def materialize(workload):
    dest=ROOT/workload; dest.mkdir(mode=0o700,parents=True,exist_ok=True)
    src=OLD/('base-'+workload); refs=OLD_INPUTS[workload]
    runner=read(src/'run_bench.py',refs[0]).decode(); dev=read(src/'bench_device.sh',refs[1]).decode()
    runner=once(runner, "ROOT = Path('"+str(src)+"')", "ROOT = Path('"+str(dest)+"')")
    runner=once(runner,REFERENCE_APP,APP); runner=once(runner,REFERENCE_LAUNCH,LAUNCH)
    runner=once(runner,"default=DELIVERY/'artifacts/package-stage/aidoku'",'default=Path('+repr(str(R3))+')')
    runner=once(runner,"    variants = {}; files = {}; flags = {}", "    if ns.mode not in ('screen','pilot'): raise ValueError('Use a predeclared screen or paired pilot')\n    cases += ['baseline-qualify','candidate-qualify']\n    variants = {}; files = {}; flags = {}")
    runner=once(runner,"    if ns.mode=='qualify': add('capture',", "    add('capture',")
    runner=once(runner,"    payload=(batch/'payload.tar.gz').read_bytes()", "    if '-dry-' in run: raise ValueError('Host QA plan: never run on device')\n    receipt=json.loads((batch/'freeze-receipt.json').read_bytes())\n    if sha(path.read_bytes())!=receipt['plan_sha256'] or receipt['plan']!=str(path): raise ValueError('Plan changed after independent freeze')\n    payload=(batch/'payload.tar.gz').read_bytes()")
    runner=once(runner,"    sys.path.insert(0,str(cfg['lease_tool_directory']))", "    if not os.environ.get('KINDLE_STUDY_IP') or cfg['host']!=os.environ['KINDLE_STUDY_IP']: raise ValueError('Missing or wrong operator-selected device')\n    sys.path.insert(0,str(cfg['lease_tool_directory']))")
    runner=once(runner,"add(v+'/binary-hash', (apphash+'\\n').encode()); add(v+'/launch.sh',instrument(source.decode(), args),0o755)","add(v+'/binary-hash', (apphash+'\\n').encode()); add(v+'/source-launch.sh',source); add(v+'/launch.sh',instrument(source.decode(), args),0o755)")
    # Pair only the common, stable markers. Intermediate launcher anchors would
    # instrument changed control flow asymmetrically or exclude some variants.
    a=runner.index("    result = replace_once(result, '\\nstart_observer")
    b=runner.index('    # Keep the portrait command',a)
    runner=runner[:a]+runner[b:]
    dev=once(dev,REFERENCE_APP,APP)
    dev=once(dev, '    same_hash "$APP/app/aidoku-native-query"', '    same_hash "$APP/app/aidoku-startup-portrait" '+HELPER+' || return 1\n    same_hash "$APP/app/aidoku-native-query"')
    dev=once(dev, '    real_cache_snapshot || return 1','''    real_cache_snapshot || return 1
    # The cache is never opened for writing. Compare every original file as
    # well as the older roster, timestamps and index/WAL guards.
    (cd "$APP/data/cache/shared-v1" && test -z "$(find . -type l -print | head -n 1)" && find . -type f | LC_ALL=C sort | while IFS= read -r file; do sha256sum "$file" || exit 1; done) || return 1''')
    # Fail closed unless the native UI and the original tuple are the expected ones.
    dev=once(dev,'    native_wait || return 1\n    [ "$(lipc-get-prop -s com.lab126.powerd state)"', '    native_wait || return 1\n    [ "$(lipc-get-prop com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ] || return 1\n    [ "$(lipc-get-prop -s com.lab126.powerd state)"')
    # The first qualifying frame is an independent, settled physical P6. Never
    # put a pixel capture in a timed or warmup visit.
    require('native-settled.ppm' in dev and (workload!='cold' or 'check_cold_case' in dev), 'Missing qualified capture')
    if GUI:
        tile_src=read(CREATOR.with_name('tile_mode.py'),max_bytes=65536)
        from types import ModuleType
        tile=ModuleType('prepare_tile'); exec(compile(tile_src,'<frozen-tile>','exec'),tile.__dict__)
        runner,dev=tile.enhance(runner,dev,once); write(dest/'tile_mode.py',tile_src)
    for k,data in [('run_bench.py',runner),('bench_device.sh',dev)]: write(dest/k,data.encode())
    subprocess.run(['sh','-n',str(dest/'bench_device.sh')],check=True)
    compile(runner,str(dest/'run_bench.py'),'exec'); return dest


def validate(batch, name, args, identity, arm, launcher, portrait):
    p=batch/'plan.json'; raw=read(p,max_bytes=65536); plan=json.loads(raw)
    require(plan['run_id']==batch.name and batch.parent==ROOT/name/'private', 'Unexpected batch directory')
    require(re.fullmatch(r'aidoku-all-[a-z][a-z0-9-]{0,12}-[a-f0-9]{8}',batch.name),'Unexpected run name')
    require(plan['original_launcher_sha256']==LAUNCH and plan['baseline_reference_sha256']==APP and plan['trial']=='combined','Incorrect installed reference')
    variants=plan['variants']; require({v:variants[v]['application_sha256'] for v in variants}=={'baseline':APP,'candidate':sha(arm)},'Staged application identity mismatch')
    require(variants['baseline']['launcher_sha256']==LAUNCH and variants['candidate']['launcher_sha256']==sha(launcher),'Launcher digest mismatch')
    require(variants['baseline']['extra_sha256']=={'aidoku-startup-portrait':HELPER} and variants['candidate']['extra_sha256']=={'aidoku-startup-portrait':sha(portrait)},'Native helper digest mismatch')
    require(all(v['args']==[] and v['env']=={} and v['startup_options']=={} for v in variants.values()),'Both arms require production defaults')
    require(plan['cases'][:2]==['baseline-warmup','candidate-warmup'] and plan['cases'][-2:]==['baseline-qualify','candidate-qualify'],'Invalid warmup/picture sequence')
    cases=[]
    for i in range(1, args.pairs+1):
        sides=('baseline','candidate') if i%2 else ('candidate','baseline')
        cases.extend(f'{s}-{i:02}' for s in sides)
    require(plan['cases'][2:-2]==cases,'Not the predetermined balanced sequence')
    r=read(batch/'runner.py',plan['runner_sha256']); d=read(batch/'device.sh',plan['device_script_sha256'])
    require(r==read(ROOT/name/'run_bench.py') and d==read(ROOT/name/'bench_device.sh'),'Runner changed')
    read(batch/'original-launcher',LAUNCH)
    with tarfile.open(fileobj=io.BytesIO(read(batch/'payload.tar.gz',plan['payload_sha256'])),mode='r:gz') as tar:
        names=tar.getnames(); require(len(names)==len(set(names)) and all(m.isfile() and re.fullmatch(r'[A-Za-z0-9_./-]+',m.name) and not m.name.startswith('/') and '..' not in m.name.split('/') for m in tar),'Invalid payload names')
        def member(k): return tar.extractfile(tar.getmember(k)).read()
        require(member('owner')==(batch.name+'\n').encode() and sha(member('capture'))==CAPTURE,'Owner or capture differs')
        require(sha(member('baseline/source-launch.sh'))==LAUNCH and member('candidate/source-launch.sh')==launcher,'Unbound source launcher')
        require(sha(member('bridge.sh'))==plan['bridge_sha256'] and member('baseline/binary-ref')==b'installed\n','Bridge or installed baseline differs')
        if GUI:
            require(plan['native_input']=='stock-library-XTEST' and sha(member('guitap'))=='2df118aad6346f8835224cabe50e27d56ddfe97a65a60ac5ff99ea707198f3c8','Unbound stock pointer tool')
            read(batch/'tile_mode.py',plan['tile_controller_sha256']); read(batch/'tile-targets.json',plan['tile_targets_sha256'])
        require(member('candidate/binary-ref')==(('installed' if sha(arm)==APP else sha(arm))+'\n').encode(),'Unbound candidate')
        if sha(arm)!=APP: require(member('bin-'+sha(arm))==arm,'Wrong staged ELF')
        hashes={}
        for line in member('inputs.sha256').decode().splitlines():
            h,n=line.split('  '); require(n not in hashes and sha(member(n))==h,'Staged member hash mismatch'); hashes[n]=h
        require(set(hashes)==set(names)-{'inputs.sha256'},'Incomplete checksum manifest')
    require(not any((batch/s).exists() for s in ('lease-token','report.json','transport.json','lease-acquired.json')),'Plan already operated')
    meta={'regime':name,'mode':args.mode,'pairs':args.pairs,'plan':str(p),'plan_sha256':sha(raw),'candidate_input_receipt':identity,
          'controller_sha256':sha(read(CREATOR)),'source_harness_sha256':OLD_INPUTS[name], 'device_accessed':False}
    write(batch/'freeze-receipt.json',(json.dumps(meta,indent=2)+'\n').encode()); return meta


def main():
    global GUI, ROOT
    require(sys.platform=='linux' and not sys.flags.optimize,'Use test-env with ordinary Python checks enabled'); os.umask(0o077)
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--check-only',action='store_true'); ap.add_argument('--mode',choices=('screen','pilot'),default='screen')
    ap.add_argument('--candidate-app',type=Path); ap.add_argument('--candidate-launcher',type=Path); ap.add_argument('--candidate-portrait',type=Path,default=R3/'aidoku-startup-portrait')
    ap.add_argument('--name',default='r4'); ap.add_argument('--workload',choices=('warm','cold','both'),default='both'); ap.add_argument('--entry',choices=('uri','tile'),default='uri'); args=ap.parse_args()
    GUI=args.entry=='tile'
    if GUI: ROOT=ROOT/'tile-v1'
    args.pairs=6 if args.mode=='screen' else 2
    read(R3/'candidate-arm',APP); read(R3/'launch.sh',LAUNCH); read(R3/'aidoku-startup-portrait',HELPER)
    require(re.fullmatch('[a-z][a-z0-9-]{0,7}',args.name),'Use a short public label')
    if not args.check_only: require(args.candidate_app is not None and args.candidate_launcher is not None,'Explicit candidate ELF and launcher required')
    prog=args.candidate_app or R3/'candidate-arm'; launch=args.candidate_launcher or R3/'launch.sh'; helper=args.candidate_portrait
    arm,launcher,portrait=read(prog),read(launch),read(helper)
    require(args.check_only or (sha(arm),sha(launcher),sha(portrait))!=(APP,LAUNCH,HELPER),'The proposed candidate is identical to R3')
    ident={'app':sha(arm),'launcher':sha(launcher),'portrait':sha(portrait)}
    # --check-only is host preparation, never a device run; its plans are
    # deliberately marked against accidental use as product evidence.
    kinds=('warm','cold') if args.workload=='both' else (args.workload,)
    for kind in kinds:
        dest=materialize(kind); label=('dry-'+kind if args.check_only else args.name+'-'+kind)
        cmd=[sys.executable,'-I',str(dest/'run_bench.py'),'prepare','--name',label,'--mode',args.mode,'--rounds',str(args.pairs),'--primary','first' if kind=='warm' else 'covers','--trial','combined',
             '--baseline-launcher',str(R3/'launch.sh'),'--baseline-extra','aidoku-startup-portrait='+str(R3/'aidoku-startup-portrait'),
             '--candidate-launcher',str(launch),'--candidate-app',str(prog),'--candidate-extra','aidoku-startup-portrait='+str(helper)]
        path=Path(subprocess.check_output(cmd,text=True).strip()); row=validate(path.parent,kind,args,ident,arm,launcher,portrait)
        print(json.dumps({'workload':kind,'plan':row['plan'],'sha256':row['plan_sha256'],'mode':args.mode,'host_check_only':args.check_only}),flush=True)


if __name__=='__main__': main()
