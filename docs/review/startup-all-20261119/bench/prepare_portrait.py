#!/usr/bin/env python3
"""Prepare the v2 physical portrait comparison with only its real setting changed."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path('/root/aidoku-all-bench-20261119')
SRC=ROOT/'base-warm'; DEST=ROOT/'portrait-v2'
RELEASE=Path('/root/aidoku-all-build-20261119/artifacts/final-v2')
SOURCE_HASHES={'run_bench.py':'a6270d6a2d2a78ad7dd1dd59819154dee2f091f85b8bb47bdf7a819fe3711ea6','bench_device.sh':'b81b54db195ec3e0de03f0374e16c614fe75e43b424b918c31e4be1648944f4a'}
FILES={'candidate-arm':'1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af','launch.sh':'2b0d6e8ee76076c1f14e92dae97a505b40f0a78e07cec78cdea23ec59398bae5','aidoku-startup-portrait':'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50'}
def sha(b):return hashlib.sha256(b).hexdigest()


def one(src,old,new):
    if src.count(old)!=1:raise ValueError('Frozen source anchor changed')
    return src.replace(old,new,1)


def materialize():
    DEST.mkdir(mode=0o700,parents=True,exist_ok=True)
    for name,pin in SOURCE_HASHES.items():
        blob=(SRC/name).read_bytes()
        if sha(blob)!=pin:raise ValueError('Source harness hash differs')
        if name=='run_bench.py':
            s=one(blob.decode(),"ROOT = Path('"+str(SRC)+"')","ROOT = Path('"+str(DEST)+"')")
            s=one(s,"    'prewarm': {'0','2'},", "    'portrait': {'shell','native'}, 'prewarm': {'0','2'},")
            m=s.index("    if ns.trial=='combined' and ");n=s.index("    add('bridge.sh'",m)
            gate="""    if ns.trial!='supervisor' or ns.primary!='first':raise ValueError('Dedicated startup-portrait trial only')
    for side,choice in (('baseline','shell'),('candidate','native')):
        v=variants[side]
        if not (v['application_sha256']==RELEASE_V2['candidate-arm'] and v['launcher_sha256']==RELEASE_V2['launch.sh'] and v['extra_sha256']=={'aidoku-startup-portrait':RELEASE_V2['aidoku-startup-portrait']} and v['startup_options']=={'portrait':choice} and not v['env'] and not v['args']):raise ValueError('Require the identical v2 program in both arms and exactly one real setting')
"""
            s=s[:m]+gate+s[n:];s=one(s,'OPTION_VALUES = {','RELEASE_V2 = '+repr(FILES)+'\nOPTION_VALUES = {');blob=s.encode()
        dst=DEST/name
        if dst.exists() and dst.read_bytes()!=blob:raise ValueError('Never overwrite different frozen inputs')
        if not dst.exists():dst.write_bytes(blob)
    subprocess.run(['sh','-n',DEST/'bench_device.sh'],check=True)
    return {f:sha((DEST/f).read_bytes()) for f in SOURCE_HASHES}


def prepare(mode):
    hashes=materialize()
    if mode=='materialize':return hashes
    for name,digest in FILES.items():
        fp=RELEASE/name
        if not fp.is_file() or fp.is_symlink() or sha(fp.read_bytes())!=digest:raise ValueError('Final v2 artifact differs')
    args=[sys.executable,'-I',str(DEST/'run_bench.py'),'prepare','--trial','supervisor','--name','v2-portrait','--mode',mode,'--primary','first']
    for side,setting in [('baseline','shell'),('candidate','native')]:
        args+=['--'+side+'-app',str(RELEASE/'candidate-arm'),'--'+side+'-launcher',str(RELEASE/'launch.sh'),'--'+side+'-extra','aidoku-startup-portrait='+str(RELEASE/'aidoku-startup-portrait'),'--'+side+'-option','portrait='+setting]
    out=Path(subprocess.check_output(args,text=True).strip());plan=json.loads(out.read_bytes());a,b=[plan['variants'][k] for k in ('baseline','candidate')]
    if any(a[k]!=b[k] for k in ('application_sha256','launcher_sha256','extra_sha256','native_query_sha256','env','args')) or a['startup_options']!={'portrait':'shell'} or b['startup_options']!={'portrait':'native'} or plan['mode']!=mode:raise ValueError('Unexpected frozen plan')
    return {'plan':str(out),**hashes}


if __name__=='__main__':
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('materialize','pilot','screen','confirm','qualify'));args=p.parse_args();print(json.dumps(prepare(args.mode)))
