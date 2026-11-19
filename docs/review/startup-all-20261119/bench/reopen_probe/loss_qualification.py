#!/usr/bin/env python3
"""Freeze or audit only the deliberately lost-data-keeper qualification."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path('/root/aidoku-all-bench-20261119/reopen_probe');LOSS=ROOT/'loss'
HOLDERS=('2ea29b78c044ed2884765a0ca7c6993e244eed53d937779998573a7a90345226','c8bbdad79863a7785c3cfd41c07662dec011f60bb3c5971745be4a727e318b21')
R2='1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
HELPER='4aa3fae0f46bf52bf46fbb075d84167aeae8707fe1e0283dd191b16dff256be7'
ANALYZER='247944b89e4454bafea66fdbd3beb93b79c8a4cf2175e597e7d402a953764005'

def sha(b):return hashlib.sha256(b).hexdigest()

def once(src,old,new):
    if src.count(old)!=1:raise ValueError('Frozen qualification anchor changed')
    return src.replace(old,new,1)

def derived():
    rs=(ROOT/'run_bench.py').read_bytes();ds=(ROOT/'bench_device.sh').read_bytes()
    if (sha(rs),sha(ds))!=HOLDERS:raise ValueError('Wrong parent reopening harness')
    h,d=rs.decode(),ds.decode()
    h=once(h,'ROOT = Path(\''+str(ROOT)+'\')','ROOT = Path(\''+str(LOSS)+'\')')
    h=once(h,"choices=('immediate','ttl','ordinary')","choices=('loss',)")
    h=once(h,"    if ns.trial != 'reopen'", "    if ns.mode != 'qualify' or ns.scenario != 'loss': raise ValueError('Loss is a separate qualification, never a latency run')\n    if ns.trial != 'reopen'")
    h=once(h,"    add('bridge.sh',bridge(run,cases,flags,ns.scenario),0o755)","    drop=(ROOT/'drop-owned-keeper.arm').read_bytes()\n    if sha(drop)!='"+HELPER+"':raise ValueError('Unqualified loss helper')\n    add('drop-owned-keeper',drop,0o755)\n    add('bridge.sh',bridge(run,cases,flags,ns.scenario),0o755)")
    # Check pidfd availability after staging authenticated bytes and before any
    # bridge activation. A missing syscall never falls back to kill(numeric_pid).
    d=once(d,'    for v in baseline candidate; do\n        app_src=', '    "$STAGE/drop-owned-keeper" --supported\n    for v in baseline candidate; do\n        app_src=')
    d=once(d,'    reopen_resource between\n    if [ "$scenario" = ttl ]', '''    reopen_resource between
    if [ "$scenario" = loss ] && [ "$pair" = candidate-qualify ]; then
        [ "$kpid" -gt 0 ]; lost_pid=$kpid;lost_birth=$kbirth;native_wait
        keeper_pid;[ "$kpid:$kbirth" = "$lost_pid:$lost_birth" ];keeper_fds
        "$STAGE/drop-owned-keeper" "$kpid" "$kbirth" >"$GROUP/loss-ok"
        [ "$(cat "$GROUP/loss-ok")" = owned-keeper-terminated ]; keeper_pid;[ "$kpid" = 0 ]
    fi
    if [ "$scenario" = ttl ]''')
    return h.encode(),d.encode()

def freeze():
    h,d=derived();LOSS.mkdir(mode=0o700,parents=True,exist_ok=True)
    helper=(ROOT/'adapter/drop-owned-keeper.arm').read_bytes()
    if sha(helper)!=HELPER:raise ValueError('Rebuild expected ARM loss helper')
    for name,value in [('run_bench.py',h),('bench_device.sh',d),('drop-owned-keeper.arm',helper)]:
        dst=LOSS/name
        if dst.exists() and dst.read_bytes()!=value:raise ValueError('Existing loss qualification changed')
        if not dst.exists():dst.write_bytes(value)
    subprocess.run(['sh','-n',str(LOSS/'bench_device.sh')],check=True)
    p=Path('/root/aidoku-all-build-20261119/artifacts/final-v2');args=[]
    for side in ('baseline','candidate'):
        args+=['--'+side+'-app',str(p/'candidate-arm'),'--'+side+'-launcher',str(p/'launch.sh'),'--'+side+'-extra','aidoku-startup-portrait='+str(p/'aidoku-startup-portrait'),'--'+side+'-option','resident='+('45' if side=='candidate' else 'off')]
    subprocess.run([sys.executable,'-I',str(LOSS/'run_bench.py'),'prepare','--trial','reopen','--primary','covers','--name','r2-lost-data','--mode','qualify','--scenario','loss',*args],check=True)

def audit(batch,out):
    h,d=derived();src=ROOT/'analyze_reopen.py'
    if sha(src.read_bytes())!=ANALYZER:raise ValueError('Unqualified core analyzer')
    s=importlib.util.spec_from_file_location('standalone_reopen_loss',src);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
    m.RUNNER=sha(h);m.DEVICE=sha(d)
    p=json.loads((batch/'plan.json').read_text())
    if p.get('reopen_scenario')!='loss' or batch.resolve().parent!=LOSS/'private':raise ValueError('Not a loss-only batch')
    original=sys.argv[:];sys.argv=[str(src),'--batch',str(batch),'--phase','qualify','--candidate-sha256',R2,'--output',str(out)]
    try:
        code=m.main()
        if out.is_file():
            result=json.loads(out.read_text());result['loss_analyzer_sha256']=sha(Path(__file__).read_bytes())
            result['loss_helper_sha256']=HELPER;result['loss_scope']='Only a supported pidfd-bound, attested private data-keeper termination is physical loss evidence. This does not measure pressure or e-ink latency.'
            out.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        return code
    finally:sys.argv=original

def main():
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    ap=argparse.ArgumentParser(description=__doc__);s=ap.add_subparsers(dest='cmd',required=True);s.add_parser('freeze');a=s.add_parser('audit');a.add_argument('--batch',type=Path,required=True);a.add_argument('--output',type=Path,required=True);ns=ap.parse_args()
    return freeze() if ns.cmd=='freeze' else audit(ns.batch,ns.output)

if __name__=='__main__':raise SystemExit(main())
