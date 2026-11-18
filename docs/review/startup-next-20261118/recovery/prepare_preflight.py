#!/usr/bin/env python3
"""Freeze or run a fixed, read-only catalog preflight through the existing guarded transport."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import uuid


ROOT = Path('/root/aidoku-next-recovery-20261118')
SEED = Path('/root/aidoku-startup-catalog-20261117/private/transport-seed.json')
TOOLS = Path('/root/aidoku-display-modes-20261114-01a1da18/tools')
LEASE_SHA = '494c62e67e29b2a9388148e4cef503ff7feceb15ef90c4af43a5ee3cf6971b72'
SSH_SHA = 'c2dfb7e982e32d02cf28fe901d85400a367647db08b3100946c7a83e0458e2db'
WRAPPER = '2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885f692955cacd86'
RT = '/var/local/kindle-catalog-entry/v1-' + WRAPPER
DOC = '/mnt/us/documents/Aidoku.sh'
CLIENT = RT + '/aidoku-catalog-client'
REG = RT + '/aidoku-library-dispatch'
FILES = {
    '/mnt/us/aidoku/app/aidoku':'62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612',
    '/mnt/us/aidoku/launch.sh':'10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296',
    DOC:'709cefdf00318d40a3ee7cb8a5cc8b47e4b8c7865677040779a61264ae086960',
    '/mnt/us/aidoku/app/aidoku-native-query':'84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d',
    '/mnt/us/aidoku/app/kindle-jpeg-worker':'1b41abcab8083ef70d3cb76ad16303d8b7934c382c4e49927f9789d11a7abf41',
    '/mnt/us/aidoku/assets/aidoku-cover-v2.png':'dde92cb1ae5f1dbf37db61e179a55522edea3759567fba74714f4954955dbf31',
    '/mnt/us/aidoku/assets/aidoku-cover.png':'b3b65e42af19906ecc5f1c99a5b761b4bf4686bbc5a150a98bb4751f760090d9',
    '/var/local/kmc/bin/sh_integration_launcher':'2b3db59ed7856b3f5026e3837f7ef07fc9bb6bc1c042274b48b3cd3fc2e04abc',
    '/usr/lib/ccat/change.lua':'971541da47ab5c3cafbde9ab80662d1b0055183c57aed9baf942a3360dbff5b4',
    REG:WRAPPER, RT+'/aidoku-entry-hooks.so':'448a68e9da0a80eb968f49c6d18ca93e99f1cae1a371866deffed9445d074667',
    CLIENT:'f46a0225d5a265f5f9749029fc115060576508ee5caadf25becb661738b88161',
}
STAGES = {'complete','initialization','deadline','json-library','json-api','token-library','token-api','token-format',
          'http-transport','response-limit','http-header','http-status','http-encoding','http-length','response-json',
          'catalog-identity','new-item-fallback','document'}
GUARDS = {'native','files','runtime','registration','catalog','row_shape','context','after','protocol','live_overlay_unverified'}
LABELS = {'context':{'root','other'}, 'rows':{'none','one','many'}, 'metadata':{'0','1'}, 'shape':{'0','1'},
          'new_tag':{'unknown','present','absent'}, 'stable_native':{'0','1'}, 'stable_files':{'0','1'},
          'stable_entry':{'0','1'}}


def sha(content):
    return hashlib.sha256(content).hexdigest()


def script():
    instructions = '\n'.join(f'    pinned {name} {digest} || return 1' for name,digest in FILES.items())
    # No raw catalog row, identifiers, path list or diagnostic stderr crosses the
    # transport. Private row values are compared in memory within this one shell.
    program = r'''set -eu
exec 2>/dev/null
export LC_ALL=C
export TMPDIR=/dev/null
DOC=@DOC@
CLIENT=@CLIENT@
RT=@RT@
REG=@REG@
db=/var/local/cc.db
stop() { printf 'stop %s\n' "$1"; exit 1; }
stable_db_file() {
    [ ! -s "$1-wal" ] && [ ! -s "$1-journal" ] && [ ! -L "$1-wal" ] && [ ! -L "$1-journal" ]
}
sql() {
    stable_db_file "$1" || return 1
    key_before=$(stat -c '%d:%i:%s:%y:%z' "$1") || return 1
    readonly_value=$(sqlite3 -batch -readonly -cmd '.timeout 1500' "file:$1?mode=ro&immutable=1" "$2") || return 1
    stable_db_file "$1" && [ "$(stat -c '%d:%i:%s:%y:%z' "$1")" = "$key_before" ] || return 1
    printf '%s' "$readonly_value"
}
regular() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(readlink -f "$1")" = "$1" ]; }
pinned() {
    regular "$1" || return 1
    digest=$(sha256sum "$1") || return 1
    [ "${digest%% *}" = "$2" ]
}
fixed_files() {
@FILES@
}
registration() {
    regular /var/local/appreg.db || return 1
    command_value=$(sql /var/local/appreg.db "SELECT value FROM properties WHERE handlerId='tech.hackerdude.shell_integration.launcher' AND name='command' LIMIT 2;") || return 1
    [ "$command_value" = "$REG" ]
}
native() {
    [ ! -e /tmp/aidoku-launcher.lock ] && [ ! -L /tmp/aidoku-launcher.lock ] || return 1
    if pidof aidoku >/dev/null; then return 1; fi
    [ "$(lipc-get-prop -s com.lab126.powerd state)" = active ] || return 1
    case "$(status statusbar)" in *start/running*) ;; *) return 1;; esac
    wms=$(pidof awesome) || return 1
    for pid in $wms; do
        s=$(sed -n 's/^State:[[:space:]]*//p' "/proc/$pid/status") || return 1
        case "$s" in S*|R*) ;; *) return 1;; esac
    done
    front=$(lipc-get-prop com.lab126.appmgrd activeApp) || return 1
    case "$front" in com.lab126.KPPMainApp|com.lab126.booklet.home) printf '%s' "$front";; *) return 1;; esac
}
row() {
    sql "$db" "SELECT hex(p_uuid),hex(p_location),hex(p_mimeType),hex(p_thumbnail),quote(p_modificationTime),quote(p_contentSize),quote(p_lastAccess) FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh' ORDER BY p_uuid LIMIT 2;"
}
tags() {
    # Some catalog versions expose only computed API tags. Without a local JSON
    # array representation the descriptor stays unknown; the fixed API client
    # makes its own independent eligibility check.
    columns=$(sql "$db" 'PRAGMA table_info(Entries);') || { printf unknown; return; }
    if ! printf '%s\n' "$columns" | awk -F'|' '$2=="p_displayTags" {found=1} END{exit !found}'; then printf unknown; return; fi
    key="p_location='/mnt/us/documents/Aidoku.sh'"
    tag=$(sql "$db" "SELECT CASE WHEN NOT json_valid(p_displayTags) THEN 'unknown' WHEN json_type(p_displayTags)!='array' THEN 'unknown' WHEN EXISTS(SELECT 1 FROM json_each(p_displayTags) WHERE type='text' AND value='NEW') THEN 'present' ELSE 'absent' END FROM Entries WHERE $key LIMIT 2;") || { printf unknown; return; }
    case "$tag" in absent|present) printf '%s' "$tag";; *) printf unknown;; esac
}
front=$(native) || stop native
fixed_files || stop files
for directory in /var/local/kindle-catalog-entry "$RT"; do
    [ ! -L "$directory" ] && [ "$(readlink -f "$directory")" = "$directory" ] && [ "$(stat -c '%u:%a' "$directory")" = 0:755 ] || stop runtime
done
[ -x "$CLIENT" ] && [ "$(stat -c '%u:%a' "$CLIENT")" = 0:755 ] || stop runtime
stable_db_file /var/local/appreg.db && stable_db_file "$db" || stop live_overlay_unverified
registration || stop registration
regular "$db" || stop catalog
uid=$(id -u) || stop context
case "$uid" in 0) printf 'context root\n';; *[!0-9]*|'') stop context;; *) printf 'context other\n';; esac
entry_before=$(row) || stop catalog
file_before=$(stat -c '%d:%i:%s:%Y:%Z:%a' "$DOC") || stop files
size=$(stat -c %s "$DOC") || stop files
mtime=$(stat -c %Y "$DOC") || stop files
case "$size:$mtime" in *[!0-9:]*|:*|*:) stop files;; esac
count=$(sql "$db" "SELECT count(*) FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh';") || stop catalog
case "$count" in 0) rows=none;; 1) rows=one;; ''|*[!0-9]*) stop catalog;; *) rows=many;; esac
printf 'rows %s\n' "$rows"
shape=$(sql "$db" "SELECT CASE WHEN count(*)=1 AND max(typeof(p_uuid)='text' AND length(p_uuid)=36 AND p_mimeType='text/x-shellscript' AND p_lastAccess>=0) THEN 1 ELSE 0 END FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh';") || stop catalog
meta=$(sql "$db" "SELECT CASE WHEN count(*)=1 AND max(p_modificationTime=$mtime AND p_contentSize=$size) THEN 1 ELSE 0 END FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh';") || stop catalog
case "$shape:$meta" in 0:0|0:1|1:0|1:1) ;; *) stop row_shape;; esac
printf 'shape %s\nmetadata %s\n' "$shape" "$meta"
tag_before=$(tags); printf 'new_tag %s\n' "$tag_before"
front_check=$(native) || stop native
[ "$front" = "$front_check" ] || stop native
probe_rc=0
reply=$("$CLIENT" --probe) || probe_rc=$?
# Only this fixed ABI grammar may leave the device; a failed/changed protocol
# cannot use stdout to emit environment or catalog values.
grammar='^\{"success":(true|false),"operation":"probe","stage":"(@STAGES@)","elapsed_ms":[0-9]{1,6}\}$'
valid=0
case "$reply" in *'
'*) ;; *) if printf '%s\n' "$reply" | grep -Eq "$grammar"; then valid=1; fi;; esac
if [ "$valid" = 1 ]; then printf 'probe %s\n' "$reply"; else printf 'probe invalid\n'; fi
case "$probe_rc" in 0) printf 'rc 0\n';; *) printf 'rc 1\n';; esac
front_after=$(native) || stop after
if [ "$front_after" = "$front" ]; then printf 'stable_native 1\n'; else printf 'stable_native 0\n'; fi
file_after=$(stat -c '%d:%i:%s:%Y:%Z:%a' "$DOC") || stop after
if [ "$file_after" = "$file_before" ] && fixed_files && registration; then printf 'stable_files 1\n'; else printf 'stable_files 0\n'; fi
entry_after=$(row) || stop after
tag_after=$(tags)
if [ "$entry_before" = "$entry_after" ] && [ "$tag_before" = "$tag_after" ]; then printf 'stable_entry 1\n'; else printf 'stable_entry 0\n'; fi
'''
    for name,value in [('DOC',DOC),('CLIENT',CLIENT),('RT',RT),('REG',REG),('FILES',instructions),('STAGES','|'.join(sorted(STAGES)))]:
        program = program.replace('@'+name+'@',value)
    return program.encode('ascii')


def decision(raw, returncode):
    # Never echo untrusted records, including on decode or protocol errors.
    result = {'passed':False, 'result':'unrecognized_observation', 'execution_context':'unknown',
              'framework_eligibility_proven':False, 'device_writes_requested':False}
    if len(raw) > 1024:
        return result
    try:
        items = {}
        for line in raw.decode('ascii').splitlines():
            key, value = line.split(' ', 1)
            if key in items or key not in (*LABELS, 'stop','probe','rc'):
                return result
            if key in LABELS and value not in LABELS[key]:
                return result
            if key == 'stop' and value not in GUARDS or key == 'rc' and value not in ('0','1'):
                return result
            items[key] = value
        if 'context' in items:
            result['execution_context'] = 'root-only' if items['context'] == 'root' else 'other-unverified'
        if 'stop' in items and returncode != 0:
            result.update(result='guard_failed', guard=items['stop']); return result
        if returncode or set(items) != set(LABELS) | {'probe','rc'}:
            return result
        result['observation'] = {'rows':items['rows'], 'file_metadata_matches':items['metadata'] == '1',
            'entry_shape_matches':items['shape'] == '1', 'optional_catalog_new_tag':items['new_tag'],
            **{k:items[k] == '1' for k in ('stable_native','stable_files','stable_entry')}}
        reply = items['probe']
        if reply == 'invalid':
            result['result'] = 'invalid_client_protocol'; return result
        obj = json.loads(reply)
        if not isinstance(obj, dict) or set(obj) != {'success','operation','stage','elapsed_ms'} or obj.get('operation') != 'probe':
            return result
        if not isinstance(obj['stage'],str) or obj['stage'] not in STAGES or type(obj['success']) is not bool:
            return result
        if type(obj['elapsed_ms']) is not int or not 0 <= obj['elapsed_ms'] < 1_000_000:
            return result
        canonical = json.dumps({k:obj[k] for k in ('success','operation','stage','elapsed_ms')}, separators=(',',':'))
        if reply != canonical or obj['success'] != (items['rc'] == '0') or obj['success'] != (obj['stage'] == 'complete'):
            return result
        result['fixed_probe'] = {'success':obj['success'], 'stage':obj['stage'], 'elapsed_ms':obj['elapsed_ms']}
        if not all(items[k] == '1' for k in ('stable_native','stable_files','stable_entry')):
            result['result'] = 'state_changed_during_read_only_observation'
        elif not obj['success']:
            result['result'] = 'fixed_probe_rejected'
        elif items['rows'] != 'one' or items['shape'] != '1' or items['metadata'] != '1' or items['new_tag'] == 'present':
            result['result'] = 'entry_ineligible'
        else:
            result.update(passed=True, result='read_only_checks_passed')
        return result
    except (ValueError, KeyError, TypeError, UnicodeError):
        return result


def save(path, value):
    with path.open('x') as out:
        json.dump(value,out,indent=2);out.write('\n')


def plan_for(run):
    return {'version':1, 'operation':'installed-entry-read-only', 'run_id':run,
            'runner_sha256':sha(Path(__file__).read_bytes()), 'script_sha256':sha(script()),
            'files':FILES, 'registered_command':REG, 'probe_scope':'existing-fixed-client-only',
            'no_device_writes':True, 'framework_eligibility_proven':False}


def prepare():
    root = ROOT / 'private'; root.mkdir(mode=0o700,parents=True,exist_ok=True)
    run = 'aidoku-catalog-ro-' + uuid.uuid4().hex[:12]
    directory = root / run; directory.mkdir(mode=0o700)
    shutil.copyfile(Path(__file__), directory/'runner.py')
    subprocess.run(['sh','-n'],input=script(),check=True)
    save(directory/'plan.json',plan_for(run))
    print(directory/'plan.json')


def tools_for_host():
    if sha((TOOLS/'guarded-device-ssh.py').read_bytes()) != SSH_SHA or sha((TOOLS/'device_lease.py').read_bytes()) != LEASE_SHA:
        raise ValueError('Unqualified shared lease or guarded transport tool')
    sys.path.insert(0,str(TOOLS))
    from device_lease import LeaseRequest, acquire, release
    return LeaseRequest, acquire, release


def transport(config):
    child = subprocess.Popen([sys.executable,'-I',str(TOOLS/'guarded-device-ssh.py'),'--config',str(config),'sh -s'],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
    try:
        stdout, _ = child.communicate(script(),timeout=45)
        return decision(stdout, child.returncode)
    except BaseException as error:
        try:
            os.killpg(child.pid,signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            child.communicate()
        if isinstance(error,subprocess.TimeoutExpired):
            return {'passed':False,'result':'guarded_transport_deadline','device_writes_requested':False,
                    'framework_eligibility_proven':False}
        raise


def operate(path):
    path = path.resolve(strict=True); directory = path.parent; run = directory.name
    if directory.parent != ROOT/'private' or path.name != 'plan.json' or not re.fullmatch(r'aidoku-catalog-ro-[0-9a-f]{12}',run):
        raise ValueError('Unexpected frozen plan path')
    if json.loads(path.read_bytes()) != plan_for(run) or sha((directory/'runner.py').read_bytes()) != sha(Path(__file__).read_bytes()):
        raise ValueError('Frozen read-only plan changed')
    if any((directory/name).exists() for name in ('attempt.json','transport.json','lease-token','outcome.json')):
        raise ValueError('Prepare a new run; a used identity is never reused')
    cfg = json.loads(SEED.read_bytes())
    if Path(cfg['lease_tool_directory']) != TOOLS or not all(Path(cfg[k]).is_file() for k in ('password_file','known_hosts','askpass')):
        raise ValueError('Private guarded transport unavailable')
    cfg.update(owner_thread=run,lease_run_id=run,lease_token_file=str(directory/'lease-token'))
    constructor, acquire, release = tools_for_host()
    request = constructor(Path(cfg['lease_dir']),directory/'lease-token',run,run)
    save(directory/'transport.json',cfg)
    save(directory/'attempt.json',{'run_id':run,'read_only':True})
    result = {'passed':False,'result':'preflight_interrupted', 'device_writes_requested':False,'framework_eligibility_proven':False}
    try:
        proof=acquire(request)
    except Exception:
        result.update(result='lease_not_acquired',lease_acquired=False,lease_released=False)
        save(directory/'outcome.json',dict(run_id=run,**result));print(json.dumps(result));return 2
    released=False
    try:
        save(directory/'lease-acquired.json',proof)
        result=transport(directory/'transport.json')
    finally:
        # This command neither takes native ownership nor writes the device.
        # The sole transport subprocess has completed (or has been reaped).
        try:
            proof=release(request,native_restored_and_connections_closed=True)
            released=proof.get('owner_and_token_checked') is True and proof.get('lease_directory_absent_after_release') is True
            save(directory/'lease-released.json',proof)
        finally:
            result.update(lease_acquired=True,lease_released=released)
            if not released:result.update(passed=False,result='lease_release_unconfirmed')
            result['limitation']='Does not test the framework-user launch path, change requests, notifications, or startup latency.'
            save(directory/'outcome.json',dict(run_id=run,**result));print(json.dumps(result))
    return 0 if result['passed'] else 2


def main():
    if sys.platform != 'linux':raise SystemExit('Run only on test-env')
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('prepare');run=commands.add_parser('run');run.add_argument('plan',type=Path)
    cli=parser.parse_args()
    return prepare() if cli.command=='prepare' else operate(cli.plan)


if __name__ == '__main__':
    try: sys.exit(main())
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
        sys.exit('Preflight preparation or guarded execution failed; consult only the sanitized outcome.')
