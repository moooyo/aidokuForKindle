#!/usr/bin/env python3
"""Freeze a short native sleep/wake on a GUI started by a second Library visit."""

import hashlib
from pathlib import Path
import subprocess
import sys

BASE=Path('/root/aidoku-all-bench-20261119/reopen_probe');ROOT=BASE/'wake'
PINS=('2ea29b78c044ed2884765a0ca7c6993e244eed53d937779998573a7a90345226','c8bbdad79863a7785c3cfd41c07662dec011f60bb3c5971745be4a727e318b21')

def sha(b):return hashlib.sha256(b).hexdigest()

def one(src,old,new):
    if src.count(old)!=1:raise ValueError('Frozen qualification anchor changed')
    return src.replace(old,new,1)

def derived():
    h,d=( (BASE/n).read_bytes() for n in ('run_bench.py','bench_device.sh'))
    if (sha(h),sha(d))!=PINS:raise ValueError('Qualified data-only benchmark required')
    h,d=h.decode(),d.decode()
    h=one(h,'ROOT = Path(\''+str(BASE)+'\')','ROOT = Path(\''+str(ROOT)+'\')')
    h=one(h,"    if ns.trial != 'reopen'", "    if ns.mode != 'qualify' or ns.scenario != 'immediate': raise ValueError('Only independent second-visit lifecycle qualification is supported')\n    if ns.trial != 'reopen'")
    h=one(h,'case "$pair_id" in *-warmup) delay=18000;; esac','case "$pair_id" in *-warmup) delay=18000;; *-qualify) if [ "$leg" = second ]; then delay=18000; fi;; esac')
    h=one(h,"'reopen_policy':'data-only-45-v1',", "'reopen_policy':'data-only-45-v1','reopened_powerd_wake':True,")
    d=one(d,'    sleep 4; gui_identity\n','    sleep 4; gui_identity\n    if [ "$role:$leg" = qualify:second ]; then reopened_sleep; fi\n')
    d=one(d,'restore_launcher() {\n    owner\n','restore_launcher() {\n    owner\n    recover_reopened_sleep\n')
    d+='\n'+Path(__file__).with_name('wake_visit.sh').read_text()+'\n'
    return h.encode(),d.encode()

def main():
    if sys.platform!='linux':raise SystemExit('Run only on test-env')
    p=Path('/root/aidoku-all-build-20261119/artifacts/final-v2');ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    for name,value in zip(('run_bench.py','bench_device.sh'),derived()):
        dst=ROOT/name
        if dst.exists() and dst.read_bytes()!=value:raise ValueError('Frozen qualifier changed')
        if not dst.exists():dst.write_bytes(value)
    subprocess.run(['sh','-n',ROOT/'bench_device.sh'],check=True)
    args=[]
    for v in ('baseline','candidate'):
        args+=['--'+v+'-app',str(p/'candidate-arm'),'--'+v+'-launcher',str(p/'launch.sh'),'--'+v+'-extra','aidoku-startup-portrait='+str(p/'aidoku-startup-portrait'),'--'+v+'-option','resident='+('45' if v=='candidate' else 'off')]
    subprocess.run([sys.executable,'-I',ROOT/'run_bench.py','prepare','--trial','reopen','--primary','covers','--name','r2-reop-wake','--mode','qualify','--scenario','immediate',*args],check=True)

if __name__=='__main__':main()
