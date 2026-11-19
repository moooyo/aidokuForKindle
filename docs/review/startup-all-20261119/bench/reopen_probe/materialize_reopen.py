#!/usr/bin/env python3
"""Derive a two-open data-keeper trial from the pinned 11/19 formal-entry driver."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE = Path('/root/aidoku-all-bench-20261119/base-warm')
ROOT = BASE.parent/'reopen_probe'
PIN = ('a6270d6a2d2a78ad7dd1dd59819154dee2f091f85b8bb47bdf7a819fe3711ea6','b81b54db195ec3e0de03f0374e16c614fe75e43b424b918c31e4be1648944f4a')
ELF = '1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
LAUNCH = '2b0d6e8ee76076c1f14e92dae97a505b40f0a78e07cec78cdea23ec59398bae5'
PORTRAIT = 'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50'


def sha(b):return hashlib.sha256(b).hexdigest()


def one(src,old,new):
    if src.count(old)!=1:raise ValueError('Pinned anchor changed: '+old[:60])
    return src.replace(old,new,1)


def materialize():
    if sys.platform!='linux':raise RuntimeError('Run through ssh test-env')
    targets=['run_bench.py','bench_device.sh']
    source=[(BASE/name).read_bytes() for name in targets]
    if tuple(map(sha,source))!=PIN:raise RuntimeError('Unqualified base harness')
    host,device=(b.decode() for b in source)
    host=one(host,str(BASE),str(ROOT))
    host=one(host,"def bridge(run, cases, flags):", "def bridge(run, cases, flags, scenario):")
    host=one(host,"{'|'.join(cases)}", "{'|'.join(c+'-'+leg for c in cases for leg in ('prime','second'))}")
    host=one(host,'AIDOKU_RESEARCH_DATA="$stage/cases/$case_id/data"; AIDOKU_RESEARCH_OUT="$stage/out/$case_id"','pair_id=${{case_id%-*}}; leg=${{case_id##*-}}; AIDOKU_RESEARCH_DATA="$stage/cases/$pair_id/data"; AIDOKU_RESEARCH_OUT="$stage/out/$pair_id/$leg"')
    old=[l for l in host.splitlines() if l.startswith('case "$case_id" in *-warmup)')][0]
    host=one(host,old,'''AIDOKU_RESEARCH_DWELL=30000
action=exit; delay=6000
case "$pair_id" in *-warmup) delay=18000;; esac
if [ "$leg" = prime ] && [ "$variant" = candidate ] && [ "{scenario}" != ordinary ]; then action=quick-return; fi
AIDOKU_RESEARCH_ACTIONS="$stage/$action-$delay.json"; export AIDOKU_RESEARCH_ACTIONS''')
    host=one(host,' --offline --performance-trace', ' --actions "$AIDOKU_RESEARCH_ACTIONS" --offline --performance-trace')
    host=one(host,"add('bridge.sh',bridge(run,cases,flags),0o755)",f'''if ns.trial != 'reopen' or ns.primary != 'covers' or ns.mode in ('screen','confirm','pilot') and ns.scenario != 'immediate': raise ValueError('Use the separate predeclared reopening scenario and complete-cover endpoint')
    for v in TOKENS:
        s=variants[v];expected={{'resident':'off' if v=='baseline' else '45'}}
        if s['application_sha256']!='{ELF}' or s['launcher_sha256']!='{LAUNCH}' or s['extra_sha256']!={{'aidoku-startup-portrait':'{PORTRAIT}'}} or s['startup_options']!=expected or s['env'] or s['args']: raise ValueError('Reopen trial requires the same qualified R2 program and only the resident setting to differ')
    for action in ('exit','quick-return'):
        for delay in (6000,18000): add(f'{{action}}-{{delay}}.json',(json.dumps([{{'at_ms':delay,'section':'app','action':action}}],separators=(',',':'))+'\\n').encode())
    add('bridge.sh',bridge(run,cases,flags,ns.scenario),0o755)''')
    host=one(host,"'trial':ns.trial,", "'trial':ns.trial,'reopen_scenario':ns.scenario,'reopen_policy':'data-only-45-v1','reopen_action_ms':6000,")
    host=one(host,"prefix=f'RUN={run}", "prefix=f'scenario={plan[\"reopen_scenario\"]}\\nRUN={run}")
    host=one(host, "remote('case-'+case,'run_case '+shlex.quote(case),timeout=80)", "remote('case-'+case,'run_case '+shlex.quote(case),timeout=170)")
    host=one(host, "remote('repair-launcher','restore_launcher',timeout=80)", "remote('repair-launcher','restore_launcher',timeout=185)")
    host=one(host,"    for v in TOKENS:\n        q.add_argument(","    q.add_argument('--scenario',choices=('immediate','ttl','ordinary'),required=True)\n    for v in TOKENS:\n        q.add_argument(")
    device=one(device,'run_case() {\n    C=$1; variant=${C%%-*}; role=${C#*-}; OUT=$STAGE/out/$C', 'single_visit() {\n    C=$1; variant=${C%%-*}; leg=${C##*-}; pair=${C%-*}; role=${pair#*-}; GROUP=$STAGE/out/$pair; OUT=$GROUP/$leg')
    device=one(device,'    prepare_case_warm "$C"\n    apply_case_options "$STAGE/cases/$C/data" "$variant"','''    if [ "$leg" = prime ]; then
        prepare_case_warm "$pair"
        apply_case_options "$STAGE/cases/$pair/data" "$variant"
    else
        for file in fixture.sha256 fixture-name startup-options-presence startup-options.sha256; do nonlink "$GROUP/prime/$file"; cp "$GROUP/prime/$file" "$OUT/$file"; done
        verify_case_options "$STAGE/cases/$pair/data" "$variant"
    fi
    printf '%s %s\\n' "$(stat -c '%d:%i' "$STAGE/$variant/app/aidoku")" "$(stat -c '%d:%i' "$STAGE/cases/$pair/data")" >>"$GROUP/pair-inode"''')
    device=one(device,'savedlog=$STAGE/cases/$C/data/logs/launcher.log','savedlog=$STAGE/cases/$pair/data/logs/launcher.log')
    device=one(device,'    verify_case_options "$STAGE/cases/$C/data" "$variant"','    verify_case_options "$STAGE/cases/$pair/data" "$variant"')
    device=one(device,'    sleep 8\n    mark native_open_requested','    sleep 8\n    if [ "$leg" = second ]; then reopen_resource pre_second; fi\n    mark native_open_requested')
    i=device.index('    if [ "$role" = qualify ]; then\n',device.index('single_visit() {'));j=device.index('    n=420\n',i)
    device=device[:i]+'    sleep 4; gui_identity\n'+device[j:]
    device=one(device,'    grep -F \'"success": true\' "$OUT/application.json" >/dev/null', '''    [ "$(lipc-get-prop com.lab126.appmgrd activeApp)" = com.lab126.KPPMainApp ]; [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ]
    grep -F '"name": "exit_requested"' "$OUT/application.json" >/dev/null
    grep -F '"success": true' "$OUT/application.json" >/dev/null''')
    # This runs only after any owned GUI has exited and before the central
    # recovery removes its executable. No signal is used to dispose of keepers.
    device=one(device,'    native_wait\n    if same_hash "$INSTALLED" "$BRIDGE_SHA"; then', '    native_wait\n    role_sha_init; wait_reopen_helpers\n    if same_hash "$INSTALLED" "$BRIDGE_SHA"; then')
    device+='\n'+(HERE/'visit.sh').read_text()+'\n'
    ROOT.mkdir(parents=True,exist_ok=True)
    for name,value in zip(targets,(host,device)):
        p=ROOT/name
        if p.exists() and p.read_text()!=value:raise RuntimeError('Existing driver differs: '+name)
        if not p.exists():p.write_text(value)
        print(name,sha(value.encode()))
    subprocess.run(['sh','-n',ROOT/targets[1]],check=True)
    subprocess.run([sys.executable,'-I','-m','py_compile',ROOT/targets[0]],check=True)


if __name__=='__main__':materialize()
