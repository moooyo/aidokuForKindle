#!/usr/bin/env python3
"""Prepare one isolated all-settings qualification; this command never contacts a Kindle."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT=Path('/root/aidoku-all-bench-20261119')
ORIGINAL=ROOT/'base-warm'; DEST=ROOT/'all-options'
RELEASE=Path('/root/aidoku-all-build-20261119/artifacts/final')
ORIGINAL_RUN='a6270d6a2d2a78ad7dd1dd59819154dee2f091f85b8bb47bdf7a819fe3711ea6'
ORIGINAL_DEVICE='b81b54db195ec3e0de03f0374e16c614fe75e43b424b918c31e4be1648944f4a'
MEASUREMENT='113991a8c5668fb050a0dbb368267a34868b0cf95c4992ba501fdb2c4ee1a323'
CONTROL='9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
BASE_LAUNCH='10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
QUERY='84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
FULL={'portrait':'native','prewarm':'2','last_cover_grace':'on','import_thumbnail':'idle','first_layout':'optimized','resident':'45'}
WARMUP=['baseline-warmup','candidate-warmup','baseline-qualify','candidate-qualify']


def need(condition,message):
    if not condition:raise ValueError(message)


def digest(blob):return sha256(blob).hexdigest()


def replace(source,old,new):
    need(source.count(old)==1,'Reviewed source anchor changed')
    return source.replace(old,new,1)


def generate():
    source=(ORIGINAL/'run_bench.py').read_bytes();device=(ORIGINAL/'bench_device.sh').read_bytes()
    need((digest(source),digest(device))==(ORIGINAL_RUN,ORIGINAL_DEVICE),'Source runner identity changed')
    r=replace(source.decode(),"ROOT = Path('"+str(ORIGINAL)+"')", "ROOT = Path('"+str(DEST)+"')")
    r=replace(r,"    'prewarm': {'0','2'},", "    'portrait': {'shell','native'}, 'prewarm': {'0','2'},")
    begin=r.index("    if ns.trial=='combined' and ");end=r.index("    add('bridge.sh'",begin)
    r=r[:begin]+'''    if ns.mode!='qualify' or ns.trial!='all-options' or ns.primary!='covers' or ns.round_start!=1 or ns.rounds is not None:
        raise ValueError('This runner accepts only the single all-options qualification')
    baseline,candidate=(variants[k] for k in TOKENS)
    same=('application_sha256','native_query_sha256','launcher_sha256','extra_sha256')
    if (any(baseline[k]!=candidate[k] for k in same) or baseline['application_sha256']==APP_SHA
            or baseline['launcher_sha256']==LAUNCH_SHA or set(candidate['extra_sha256'])!={'aidoku-startup-portrait'}
            or baseline['startup_options'] or candidate['startup_options']!=FULL_OPTIONS
            or any(v['env'] or v['args'] for v in variants.values())):
        raise ValueError('Both arms must use the same release; only the full six-setting document may differ')
''' + r[end:]
    r=replace(r,'OPTION_VALUES = {','FULL_OPTIONS = '+repr(FULL)+'\nOPTION_VALUES = {')
    r=replace(r,"'trial':ns.trial,'baseline_reference_sha256'", "'trial':ns.trial,'qualification_only':True,'performance_claim':False,'baseline_reference_sha256'")
    dev=replace(device.decode(),'    cat /proc/sys/kernel/random/boot_id', '''    # The per-case settings are private copies; the real settings must stay identical.
    if [ -e "$APP/data/startup-options" ] || [ -L "$APP/data/startup-options" ]; then
        nonlink "$APP/data/startup-options" && [ -f "$APP/data/startup-options" ] || return 1
        printf 'actual-options '; sha256sum "$APP/data/startup-options"
        stat -c 'actual-options-state %s:%Y:%a' "$APP/data/startup-options" || return 1
    else printf 'actual-options absent\\n'; fi
    cat /proc/sys/kernel/random/boot_id''')
    r=replace(r,"choices=('supervisor','prewarm','grace','layout','combined','import','reopen')", "choices=('all-options',)")
    return {'run_bench.py':r.encode(),'bench_device.sh':dev.encode()}


def expected(release_plan):
    plan=json.loads(release_plan.read_bytes());need(isinstance(plan,dict),'Invalid release plan')
    need(plan.get('trial')=='combined' and plan.get('mode')=='screen' and plan.get('cache_regime')=='warm-cache-v2'
         and plan.get('runner_sha256')==ORIGINAL_RUN and plan.get('device_script_sha256')==ORIGINAL_DEVICE
         and plan.get('baseline_reference_sha256')==CONTROL,'A frozen true-defaults comparison is required')
    a,b=(plan['variants'][k] for k in ('baseline','candidate'))
    need(a['application_sha256']==CONTROL and a['launcher_sha256']==BASE_LAUNCH and a['extra_sha256']=={}
         and all(v.get('args')==[] and v.get('env')=={} and v.get('startup_options')=={} and
                 v.get('native_query_sha256')==QUERY for v in (a,b)), 'Incorrect frozen default arms')
    for arm in (a,b):
        canonical={k:v for k,v in arm.items() if k!='variant_sha256'}
        need(arm['variant_sha256']==digest(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()),'Invalid default arm identity')
    members={'candidate-arm':b['application_sha256'],'launch.sh':b['launcher_sha256'],
             'aidoku-startup-portrait':b['extra_sha256']['aidoku-startup-portrait']}
    need(set(b['extra_sha256'])=={'aidoku-startup-portrait'} and b['application_sha256']!=CONTROL and
         b['launcher_sha256']!=BASE_LAUNCH and all(re.fullmatch(r'[a-f0-9]{64}',v) for v in members.values()),
         'Combined release package is incomplete')
    return members


def freeze_files():
    DEST.mkdir(mode=0o700,parents=True,exist_ok=True)
    files=generate();base=ROOT/'tools/analyze_all.py'
    frozen=DEST/'frozen-analyze-all.py'
    if frozen.exists():need(digest(frozen.read_bytes())==MEASUREMENT,'Frozen general analyzer differs')
    else:
        blob=base.read_bytes();need(digest(blob)==MEASUREMENT,'General analyzer changed; use the qualified snapshot');frozen.write_bytes(blob)
    for name,value in files.items():
        p=DEST/name
        if p.exists():need(p.read_bytes()==value,'Do not overwrite a different frozen runner')
        else:p.write_bytes(value)
    subprocess.run(['sh','-n',str(DEST/'bench_device.sh')],check=True)
    return files


def prepare(release_plan):
    release_plan=release_plan.resolve(strict=True)
    need(release_plan.parent.parent==(ORIGINAL/'private') and release_plan.name=='plan.json','Use the frozen default warm plan')
    members=expected(release_plan)
    for name,expected_hash in members.items():
        p=RELEASE/name;need(p.is_file() and not p.is_symlink() and digest(p.read_bytes())==expected_hash,'Frozen package member differs')
    freeze_files()
    args=[sys.executable,'-I',str(DEST/'run_bench.py'),'prepare','--trial','all-options','--name','all-options','--mode','qualify','--primary','covers']
    for side in ('baseline','candidate'):
        args+=['--'+side+'-app',str(RELEASE/'candidate-arm'),'--'+side+'-launcher',str(RELEASE/'launch.sh'),
               '--'+side+'-extra','aidoku-startup-portrait='+str(RELEASE/'aidoku-startup-portrait')]
    for key in sorted(FULL):args.extend(['--candidate-option',key+'='+FULL[key]])
    p=subprocess.run(args,check=True,capture_output=True,text=True)
    path=Path(p.stdout.strip()).resolve(strict=True)
    need(path.name=='plan.json' and path.parent.parent==DEST/'private','Unexpected frozen receipt path')
    receipt=json.loads(path.read_bytes());need(receipt['cases']==WARMUP and receipt['qualification_only'] and receipt['performance_claim'] is False,'Non-qualification plan')
    binding={'release_plan_sha256':digest(release_plan.read_bytes()),'all_options_plan_sha256':digest(path.read_bytes()),'members':members}
    with (path.parent/'release-binding.json').open('x') as out:json.dump(binding,out,sort_keys=True);out.write('\n')
    print(path)


if __name__=='__main__':
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    p=argparse.ArgumentParser(description=__doc__);commands=p.add_subparsers(dest='operation',required=True)
    q=commands.add_parser('prepare');q.add_argument('--release-plan',type=Path,required=True)
    try:cli=p.parse_args();prepare(cli.release_plan)
    except (ValueError,OSError,KeyError,subprocess.CalledProcessError) as err:p.exit(1,'All-options preparation failed; inspect the host fixture and frozen files.\n')
