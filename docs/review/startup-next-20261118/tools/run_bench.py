#!/usr/bin/env python3
"""Freeze an indexed-entry comparison on test-env, then run that exact plan once."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import uuid


ROOT = Path('/root/aidoku-next-bench-20261118')
DELIVERY = Path('/root/aidoku-startup-delivery-20261118')
SEED = Path('/root/aidoku-startup-catalog-20261117/private/transport-seed.json')
APP_SHA = '62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612'
LAUNCH_SHA = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
QUERY_SHA = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
TOKENS = ('baseline', 'candidate')


def sha(content):
    return hashlib.sha256(content).hexdigest()


def save(path, value):
    temporary = path.with_suffix('.writing')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Instrumentation anchor changed: ' + old[:60])
    return source.replace(old, new, 1)


def instrument(source, args):
    result = replace_once(source, 'DATA_DIR=$AIDOKU_HOME/data', 'DATA_DIR=$AIDOKU_RESEARCH_DATA')
    mark = '''bench_mark() {
    IFS=' ' read -r bench_stamp bench_unused </proc/uptime
    printf '%s %s\\n' "$bench_stamp" "$1" >>"$AIDOKU_RESEARCH_OUT/shell.tsv"
}
if [ "${1:-}" = --foreground ]; then bench_mark launcher_worker; else bench_mark launcher_entry; fi
'''
    result = replace_once(result, 'ROOT_PID=$$\n', 'ROOT_PID=$$\n' + mark)
    result = replace_once(result, '\nstart_observer\nstart_application 0', '\nbench_mark observer_start\nstart_observer\nbench_mark observer_ready\nstart_application 0')
    result = replace_once(result, "    startup_capability_argument=''", "    bench_mark portrait_start\n    startup_capability_argument=''")
    result = replace_once(result, '    take_display\n    run_application "$APP_GENERATION"', '    bench_mark portrait_done\n    bench_mark display_takeover_start\n    take_display\n    bench_mark display_takeover_done\n    run_application "$APP_GENERATION"')
    result = replace_once(result, '    if [ "$runner_resume" -eq 1 ]; then', '    bench_mark application_exec_requested\n    if [ "$runner_resume" -eq 1 ]; then')
    # Keep the portrait command exactly as shipped; its fixed native supervisor
    # does not accept arbitrary tracing flags. Common shell endpoints cover it.
    lines = result.split('\n'); hits = 0
    for i, line in enumerate(lines):
        if '"$APP_DIR/aidoku" --kindle' not in line or '--data-dir "$DATA_DIR"' not in line:
            continue
        tail = ' --offline --performance-trace "$AIDOKU_RESEARCH_OUT/application.json" --state-output "$AIDOKU_RESEARCH_OUT/state.json" --exit-after-ms "$AIDOKU_RESEARCH_DWELL"'
        tail += ' $AIDOKU_RESEARCH_CAPTURE_ARGS' + ''.join(' ' + shlex.quote(a) for a in args)
        lines[i] = replace_once(line, '--data-dir "$DATA_DIR"', '--data-dir "$DATA_DIR"' + tail)
        lines[i] = replace_once(lines[i], '3>&- &', '3>&- 2>"$AIDOKU_RESEARCH_OUT/native.log" &'); hits += 1
    if hits != 2:
        raise ValueError('Missing a normal/resume launch anchor')
    return '\n'.join(lines).encode()


def cli_args(ns, side):
    parts = getattr(ns, side + '_arg')
    if parts and (len(parts) != 2 or parts[0] != '--startup-cached-covers' or parts[1] not in {'0', '1', '2'}):
        raise ValueError('Only the bounded startup cache argument is supported')
    env = {}
    for spec in getattr(ns, side + '_env'):
        key, sep, value = spec.partition('=')
        if not sep or key != 'AIDOKU_STARTUP_PORTRAIT' or value not in {'native','legacy'} or key in env:
            raise ValueError('Unrecognized or repeated switch')
        env[key] = value
    return parts, env


def arm(binary):
    if len(binary) < 52 or binary[:7] != b'\x7fELF\x01\x01\x01' or int.from_bytes(binary[18:20], 'little') != 40:
        raise ValueError('Expected ELF32 little-endian ARM payload')


def case_order(mode, start, count):
    if mode == 'qualify':
        return ['baseline-warmup','candidate-warmup','baseline-qualify','candidate-qualify']
    if mode == 'screen' and (start, count) != (1, 6):
        raise ValueError('Screen requires the predetermined six pairs')
    if mode == 'confirm' and (start, count) not in {(1, 5), (6, 5), (1, 10)}:
        raise ValueError('Confirm requires ten independent pairs or two fixed five-pair batches')
    if mode == 'pilot' and (start != 1 or count not in (1, 2)):
        raise ValueError('Pilot uses one or two pairs')
    order = ['baseline-warmup', 'candidate-warmup']
    for n in range(start, start + count):
        order.extend(f'{v}-{n:02}' for v in (TOKENS if n % 2 else reversed(TOKENS)))
    return order


def bridge(run, cases, flags):
    stage = '/mnt/us/.' + run
    return f'''#!/bin/sh
set -eu
[ "$#" -eq 1 ] && [ "$1" = --library ] || exit 64
stage={stage}
[ "$(cat "$stage/owner")" = {run} ] || exit 73
case_id=$(cat "$stage/case")
case "$case_id" in {'|'.join(cases)}) ;; *) exit 64;; esac
variant=${{case_id%%-*}}
AIDOKU_HOME="$stage/$variant"; AIDOKU_RESEARCH_DATA="$stage/data"; AIDOKU_RESEARCH_OUT="$stage/out/$case_id"
AIDOKU_RESEARCH_DWELL=8000; AIDOKU_RESEARCH_CAPTURE_ARGS=''; AIDOKU_NATIVE_REFRESH_TRACE=0
case "$case_id" in *-warmup) AIDOKU_RESEARCH_DWELL=20000;; *-qualify) AIDOKU_RESEARCH_DWELL=12500; AIDOKU_RESEARCH_CAPTURE_ARGS="--capture $AIDOKU_RESEARCH_OUT/final.png --capture-settle-covers";; esac
unset AIDOKU_STARTUP_PORTRAIT
case "$variant" in baseline) {flags['baseline']};; candidate) {flags['candidate']};; esac
export AIDOKU_HOME AIDOKU_RESEARCH_DATA AIDOKU_RESEARCH_OUT AIDOKU_RESEARCH_DWELL AIDOKU_RESEARCH_CAPTURE_ARGS AIDOKU_NATIVE_REFRESH_TRACE
IFS=' ' read -r stamp skip </proc/uptime
printf '%s %s\\n' "$stamp" launcher_bridge >>"$AIDOKU_RESEARCH_OUT/shell.tsv"
exec sh "$AIDOKU_HOME/launch.sh" --library
'''.encode()


def prepare(ns):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,12}', ns.name):
        raise ValueError('Use a short public experiment label')
    run = f'aidoku-next-{ns.name}-{uuid.uuid4().hex[:8]}'
    private = ROOT/'private'; private.mkdir(parents=True, mode=0o700, exist_ok=True); private.chmod(0o700)
    batch=private/run; batch.mkdir(mode=0o700)
    cases = case_order(ns.mode, ns.round_start, ns.rounds if ns.rounds is not None else {'screen':6,'confirm':10,'pilot':1,'qualify':0}[ns.mode])
    variants = {}; files = {}; flags = {}
    def add(name, data, mode=0o644):
        files[name] = (data, mode)
    add('owner', (run+'\n').encode()); default=DELIVERY/'artifacts/package-stage/aidoku'
    for v in TOKENS:
        path = getattr(ns,v+'_launcher') or default/'launch.sh'; source=path.read_bytes()
        if source.count(b'\r'): raise ValueError('Launchers must be exact Linux bytes')
        args, env = cli_args(ns, v)
        flags[v] = ('; '.join(f'export {k}={vv}' for k,vv in env.items()) or ':')
        rawapp = getattr(ns,v+'_app'); binary = rawapp.read_bytes() if rawapp else None
        if binary is not None: arm(binary)
        apphash = sha(binary) if binary is not None else APP_SHA
        if binary is not None and apphash != APP_SHA: add('bin-'+apphash,binary,0o755)
        add(v+'/binary-ref', (('installed' if apphash == APP_SHA else apphash)+'\n').encode())
        add(v+'/binary-hash', (apphash+'\n').encode()); add(v+'/launch.sh',instrument(source.decode(), args),0o755)
        extras = {}
        for spec in getattr(ns,v+'_extra'):
            key, sep, value=spec.partition('=')
            if not sep or key!='aidoku-startup-portrait' or key in extras: raise ValueError('Unexpected extra helper')
            data=Path(value).read_bytes(); arm(data); extras[key]=sha(data); add(v+'/app/'+key,data,0o755)
        spec={'application_sha256':apphash,'launcher_sha256':sha(source),'native_query_sha256':QUERY_SHA,'args':args,'env':env,'extra_sha256':extras}
        spec['variant_sha256']=sha(json.dumps(spec,sort_keys=True,separators=(',',':')).encode());variants[v]=spec
    add('bridge.sh',bridge(run,cases,flags),0o755)
    if ns.mode=='qualify': add('capture',Path('/root/aidoku-native-query-20261117/tools/framebuffer-dump').read_bytes(),0o755)
    for name,(data,_) in files.items():
        if name.endswith('.sh'):subprocess.run(['sh','-n'],input=data,check=True)
    add('inputs.sha256', ''.join(f'{sha(b)}  {n}\n' for n,(b,m) in files.items()).encode())
    with tarfile.open(batch/'payload.tar.gz','w:gz',compresslevel=1) as tar:
        for name,(data,mode) in files.items():
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode;tar.addfile(info,io.BytesIO(data))
    tool=Path(__file__).resolve(); private_script=tool.with_name('bench_device.sh').read_bytes()
    subprocess.run(['sh','-n'],input=private_script,check=True); (batch/'device.sh').write_bytes(private_script)
    original=(default/'launch.sh').read_bytes()
    if sha(original)!=LAUNCH_SHA: raise ValueError('Frozen installed launcher differs')
    (batch/'original-launcher').write_bytes(original);shutil.copyfile(tool,batch/'runner.py')
    plan={'run_id':run,'mode':ns.mode,'primary':ns.primary,'cases':cases,'variants':variants,'owner_hash':sha(files['owner'][0]),'bridge_sha256':sha(files['bridge.sh'][0]),'payload_sha256':sha((batch/'payload.tar.gz').read_bytes()),'device_script_sha256':sha(private_script),'runner_sha256':sha(tool.read_bytes()),'original_launcher_sha256':LAUNCH_SHA}
    save(batch/'plan.json',plan);print(batch/'plan.json',flush=True)


def operate(path, recovering):
    path=path.resolve(strict=True);batch=path.parent;plan=json.loads(path.read_text());run=plan['run_id']
    if batch.parent!=ROOT/'private' or batch.name!=run or path.name!='plan.json' or not re.fullmatch(r'aidoku-next-[a-z][a-z0-9-]{0,12}-[a-f0-9]{8}',run):
        raise ValueError('Invalid private batch path')
    payload=(batch/'payload.tar.gz').read_bytes(); device=(batch/'device.sh').read_bytes()
    if (sha(payload),sha(device),sha(Path(__file__).read_bytes()),sha((batch/'original-launcher').read_bytes()))!=(plan['payload_sha256'],plan['device_script_sha256'],plan['runner_sha256'],LAUNCH_SHA):
        raise ValueError('Frozen inputs changed; use the frozen runner.py')
    stage='/mnt/us/.'+run
    prefix=f'RUN={run}\nSTAGE={stage}\nOWNER_HASH={plan["owner_hash"]}\nBRIDGE_SHA={plan["bridge_sha256"]}\nORIGINAL_SHA={LAUNCH_SHA}\n'.encode()+device+b'\n'
    report_path=batch/'report.json';config=batch/'transport.json'
    if recovering:
        if not report_path.exists() or not config.exists():raise ValueError('No prior operation to recover')
        report=json.loads(report_path.read_text());cfg=json.loads(config.read_text())
        if report['lease_released']: raise ValueError('Released tokens are never reused')
    else:
        if report_path.exists() or config.exists() or (batch/'lease-token').exists():raise ValueError('Already used; freeze a fresh plan')
        cfg=json.loads(SEED.read_text());cfg.update(owner_thread=run,lease_run_id=run,lease_token_file=str(batch/'lease-token'))
        config.write_text(json.dumps(cfg));config.chmod(0o600)
        report={**plan,'started_at':datetime.now(timezone.utc).isoformat(),'commands':[],'success':False,'lease_released':False,'completed':[],'stage_started':False,'case_exception':None}
    sys.path.insert(0,str(cfg['lease_tool_directory']))
    from device_lease import LeaseRequest, acquire, release, status
    request=LeaseRequest(Path(cfg['lease_dir']),batch/'lease-token',run,run)
    guard=Path(cfg['lease_tool_directory'])/'guarded-device-ssh.py'
    if not guard.is_file():raise ValueError('Private guarded transport unavailable')
    if not all(Path(cfg[k]).is_file() for k in ('known_hosts','password_file','askpass')):raise ValueError('Private transport inputs unavailable')
    def remote(label,body,*,upload=False,timeout=55,required=True):
        name=label if not (batch/(label+'.stdout')).exists() else label+'-'+uuid.uuid4().hex[:6]
        cmd=f'tar -xzf - -C {stage}' if upload else 'sh -s'
        data=payload if upload else prefix+body.encode()+b'\n'
        if not upload and len(data)>1024*1024:raise ValueError('Bounded transport command exceeded')
        with (batch/(name+'.stdout')).open('xb') as out,(batch/(name+'.stderr')).open('xb') as err:
            proc=subprocess.Popen([sys.executable,str(guard),'--config',str(config),cmd],stdin=subprocess.PIPE,stdout=out,stderr=err,start_new_session=True)
            try:proc.communicate(data,timeout=timeout)
            except BaseException as error:
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:proc.communicate(timeout=4)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.communicate()
                if not isinstance(error,subprocess.TimeoutExpired):raise
                rc=124
            else:rc=proc.returncode
        report['commands'].append({'name':name,'returncode':rc});save(report_path,report)
        if rc and required:raise RuntimeError(f'{label} failed ({rc}); inspect private logs')
        return (batch/(name+'.stdout')) if rc==0 else None
    def restore():
        if not report['stage_started']:
            report['read_only_preflight']=True
            return
        probe=remote('owned-stage-status','if [ -e "$STAGE" ] || [ -L "$STAGE" ]; then owner; echo owned; else echo absent; fi').read_text().strip()
        if probe=='owned':
            remote('repair-launcher','restore_launcher',timeout=80)
            report['entry_restored']=True
            if not (batch/'outputs.tar.gz').exists():
                file=remote('collect','owner\nif [ -d "$STAGE/out" ]; then tar -czf - -C "$STAGE" out; fi',timeout=60)
                if file.stat().st_size:shutil.copyfile(file,batch/'outputs.tar.gz')
        elif probe!='absent': raise RuntimeError('Unrecognized staging status')
        before=(batch/'before.stdout').read_bytes()
        lights=before.decode().splitlines()[-2:]
        if len(lights)!=2 or not all(v.isdigit() and 0<=int(v)<=24 for v in lights): raise RuntimeError('Original light state unavailable')
        remote('restore-light','\n'.join(f'if [ "$(lipc-get-prop -i com.lab126.powerd {name})" != {val} ]; then lipc-set-prop -i com.lab126.powerd {name} {val}; fi' for name,val in zip(('flIntensity','currentAmberLevel'),lights)))
        after=remote('after','state_snapshot').read_bytes()
        if before!=after:raise RuntimeError('Protected snapshot changed; see private before/after')
        report.update(entry_restored=True,installed_and_user_unchanged=True,input_roster_unchanged=True,same_boot=True,native_restored=True)
        if probe=='owned': remote('remove-owned-stage','owner\nnative_wait\nrm -rf -- "$STAGE"\ntest ! -e "$STAGE"')
        report['temporary_stage_absent']=True
    # Everything required for transfer and restoration was prepared above.
    if recovering:
        status(request)
    else:
        save(report_path,report)
        proof=acquire(request); save(batch/'lease-acquired.json',proof)
    try:
        if not recovering:
            try:
                before=remote('before','state_snapshot');lights=before.read_text().splitlines()[-2:]
                if len(lights)!=2 or not all(x.isdigit() and 0<=int(x)<=24 for x in lights):raise RuntimeError('Original lighting invalid')
                report['stage_started']=True;save(report_path,report)
                remote('create-stage','test ! -e "$STAGE"\ntest ! -L "$STAGE"\nmkdir -m 700 "$STAGE"\nprintf "%s\\n" "$RUN" >"$STAGE/owner"\nowner\nlipc-set-prop -i com.lab126.powerd touchScreenSaverTimeout 1')
                remote('upload','',upload=True,timeout=140)
                remote('prepare-device','prepare_device',timeout=120)
                check=remote('original-program-backup','owner\nsame_hash "$STAGE/original-launcher" "$ORIGINAL_SHA"\ncat "$STAGE/original-launcher"').read_bytes()
                if sha(check)!=LAUNCH_SHA:raise RuntimeError('Original launcher backup differs')
                remote('activate-bridge','swap_launcher')
                for case in plan['cases']:
                    print('START',case,flush=True)
                    remote('case-'+case,'run_case '+shlex.quote(case),timeout=80)
                    report['completed'].append(case);save(report_path,report); print('DONE',case,flush=True)
                report['cases_completed']=True
            except BaseException as error:
                report['case_exception']=str(error);save(report_path,report)
        restore()
        proof=release(request,native_restored_and_connections_closed=True);save(batch/'lease-released.json',proof);report['lease_released']=True
        report['success']=bool(report.get('cases_completed') and not report.get('case_exception') and report.get('native_restored'))
    finally:
        save(report_path,report)
        print('END',run,'success',report['success'],'released',report['lease_released'],flush=True)
    if (batch/'outputs.tar.gz').exists() and not (batch/'outputs').exists():
        with tarfile.open(batch/'outputs.tar.gz') as arch:arch.extractall(batch/'outputs',filter='data')
    if not report['success']:raise SystemExit(1)


def main():
    if sys.platform!='linux':raise SystemExit('Run through ssh test-env')
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    q=sub.add_parser('prepare');q.add_argument('--name',required=True);q.add_argument('--mode',choices=('pilot','screen','confirm','qualify'),required=True);q.add_argument('--primary',choices=('first','covers'),required=True);q.add_argument('--round-start',type=int,default=1);q.add_argument('--rounds',type=int)
    for v in TOKENS:
        q.add_argument('--'+v+'-launcher',type=Path);q.add_argument('--'+v+'-app',type=Path)
        for item in ('env','extra','arg'):q.add_argument('--'+v+'-'+item,action='append',default=[])
    for op in ('run','recover'):
        q=sub.add_parser(op);q.add_argument('plan',type=Path)
    ns=parser.parse_args()
    if ns.action=='prepare':prepare(ns)
    else:operate(ns.plan,ns.action=='recover')


if __name__=='__main__':
    main()
