#!/usr/bin/env python3
"""Retire a failed benchmark after a narrowly verified native catalog reindex.

Audit is host-only. Release is for the original still-held lease, after its
ordinary recovery restored the launcher and native control but rejected the
changed catalog identity. A successful release can never qualify a sample.
"""

import argparse
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import time


ROOTS = {
    Path('/root/aidoku-next-bench-20261118/private'): (
        '85d60c15b871375477cd14ef2020f0cc6adb7119ef618a010bec6ed934d97f7c',
        '3126a4c5ceffb3836218f9f9ccb9dab2b111c67c56a0d380c98a8167379da14d'),
    Path('/root/aidoku-next-coldbench-20261118/private'): (
        '0d56c4d2ad634a165f6184b12aff9a3fc27dc5f5662293052270f4c910561f26',
        '49d597c9aae6348244b12e2b9a3345c2e60cb227fbab58ee3b7a3fe07f87c057'),
}
ORIGINAL = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
SHARED = Path('/root/aidoku-display-modes-20261114-01a1da18/tools')
SEED = Path('/root/aidoku-startup-catalog-20261117/private/transport-seed.json')
HEXSHA = re.compile('[0-9a-f]{64}')
UUID = re.compile(rb'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
CASE = re.compile(r'(baseline|candidate)-(warmup|qualify|[0-9]{2})')
DOC_STAT = re.compile(rb'document ([0-9]+):([0-9]{8,12}):([0-7]{3,4})')


class Rejected(Exception):
    pass


def yes(value, reason):
    if not value:
        raise Rejected(reason)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def saved(path, value):
    pending = path.with_suffix('.retire-writing')
    yes(not pending.exists() and not pending.is_symlink(), 'pending_record_exists')
    with pending.open('x') as out:
        json.dump(value,out,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
    pending.replace(path)


def snap(before, after):
    a,b=before.splitlines(),after.splitlines()
    yes(len(a) == len(b) and len(a) in (34,38), 'snapshot_length')
    delta=[i for i in range(len(a)) if a[i] != b[i]]
    yes(len(delta)==1 and delta[0]==6, 'unrelated_protected_state_changed')
    old,new=DOC_STAT.fullmatch(a[6]),DOC_STAT.fullmatch(b[6])
    yes(old and new and old[1]==new[1]==b'309' and old[3]==new[3] and int(old[2])<int(new[2]), 'invalid_document_stat_change')
    yes(a[7].startswith(b'launcher ') and a[7]==b[7] and before.endswith(b'\n') and after.endswith(b'\n'), 'launcher_stat_changed')
    return len(a),int(old[2]),int(new[2])


def catalog(data):
    yes(len(data)<800 and data.endswith(b'\n') and data.count(b'\n')==1,'catalog_row_count')
    fields=data.rstrip(b'\n').split(b'|')
    yes(len(fields)==5 and UUID.fullmatch(fields[0]) and fields[1]==b'text/x-shellscript' and 0<len(fields[2])<=512 and re.fullmatch(rb'[0-9]{8,12}',fields[3]) and fields[4]==b'309','catalog_row_shape')
    return fields


def number(raw):
    yes(re.fullmatch(rb'[0-9]{8,12}\n', raw),'invalid_catalog_access');return int(raw)


def classify(before,after,oldrow,newrow,oldaccess,newaccess,trace,shell_log,started,ended):
    rows,oldmtime,newmtime=snap(before,after);a,b=catalog(oldrow),catalog(newrow)
    yes(a[0]!=b[0] and a[1:3]==b[1:3] and a[4:]==b[4:] and (int(a[3]),int(b[3]))==(oldmtime,newmtime),'catalog_delta_not_native_touch')
    old,new=number(oldaccess),number(newaccess)
    yes(new>old and int(started)-2<=newmtime<=int(ended)+2 and newmtime-2<=new<=int(ended)+2, 'catalog_times_outside_run')
    yes(trace.get('success') is True and trace.get('finished') is True and trace.get('dropped_events')==0,'failed_application')
    ev=[e.get('name') for e in trace.get('events',[]) if isinstance(e,dict)]
    yes(all(ev.count(x)==1 for x in ('startup_library_input_ready','startup_visible_covers_settled','shutdown_started','shutdown_complete')), 'incomplete_application_lifecycle')
    marks=[line.rsplit(b' ',1)[-1] for line in shell_log.splitlines()]
    yes(all(marks.count(x)==1 for x in (b'native_open_requested',b'native_restored',b'native_home_active')) and marks.index(b'native_open_requested')<marks.index(b'native_restored')<marks.index(b'native_home_active'), 'native_markers_incomplete')
    return {'protected_snapshot_rows':rows,'different_protected_fields':['document_mtime'],'one_catalog_row':True,'identity_replaced':True,'mime_thumbnail_size_preserved':True,'catalog_mtime_matches_document':True,'recent_access_advanced':True,'app_shutdown_and_native_restoration_observed':True,'batch_eligible':False,'causal_scope':'Observed outcome matches the stock timestamp/reindex fallback. The original cause of the missing quiet update was not established.'}


def batch_input(directory):
    root=directory.resolve(strict=True);yes(root.parent in ROOTS and not directory.is_symlink(),'unrecognized_batch_root')
    run=root.name;yes(re.fullmatch(r'aidoku-next-[a-z][a-z0-9-]{0,12}-[0-9a-f]{8}',run),'invalid_run')
    plan=json.loads((root/'plan.json').read_text());report=json.loads((root/'report.json').read_text())
    expected=ROOTS[root.parent];script=(root/'device.sh').read_bytes()
    yes((sha((root/'runner.py').read_bytes()),sha(script))==expected==(plan['runner_sha256'],plan['device_script_sha256']),'unqualified_frozen_runner')
    yes(all(report.get(k)==plan.get(k) for k in ('run_id','cases','owner_hash','bridge_sha256','runner_sha256','device_script_sha256','payload_sha256','variants')), 'frozen_plan_mismatch')
    yes(run==report['run_id'] and report['success'] is False and report.get('cases_completed') is not True and report.get('stage_started') is True,'not_a_failed_activated_batch')
    yes(plan['original_launcher_sha256']==ORIGINAL and sha((root/'original-launcher').read_bytes())==ORIGINAL and plan['owner_hash']==sha((run+'\n').encode()) and HEXSHA.fullmatch(plan['bridge_sha256']), 'launcher_backup_or_owner_mismatch')
    commands=report['commands'];cases=plan['cases'];done=report['completed']
    yes(len(done)<len(cases) and done==cases[:len(done)] and all(isinstance(s,str) and CASE.fullmatch(s) for s in cases) and len(set(cases))==len(cases),'unexpected_case_order')
    case=cases[len(done)]
    yes(report.get('case_exception')==f'case-{case} failed (1); inspect private logs', 'failure_not_case_guard')
    yes([c for c in commands if c.get('name')=='case-'+case and c.get('returncode')==1] and any(c.get('name')=='repair-launcher' and c.get('returncode')==1 for c in commands), 'missing_failed_case_and_repair')
    yes(all(any(c.get('name')==n and c.get('returncode')==0 for c in commands) for n in ('before','original-program-backup','activate-bridge')), 'incomplete_owned_activation')
    return root,plan,report,script,case


def times(root,report):
    start=datetime.fromisoformat(report['started_at']).timestamp()
    release=root/'lease-released.json'
    end=datetime.fromisoformat(json.loads(release.read_text())['released_at_utc']).timestamp() if release.is_file() else time.time()
    return start,end


def audit(root,report,case):
    yes(report['lease_released'] is True, 'audit_expects_retired_batch')
    proof=json.loads((root/'lease-released.json').read_text())
    yes(proof.get('run_id')==root.name and all(proof.get(k) is True for k in ('ok','owner_and_token_checked','native_restored_and_connections_closed','lease_directory_absent_after_release')), 'invalid_release_evidence')
    evidence=root/'outputs/out'/case; tail=root/'after-functional-recovery.stdout'
    result=classify((root/'before.stdout').read_bytes(),tail.read_bytes(),(evidence/'catalog-before.identity').read_bytes(),(evidence/'catalog-after.identity').read_bytes(),(evidence/'catalog-before.access').read_bytes(),(evidence/'catalog-after.access').read_bytes(),json.loads((evidence/'application.json').read_text()),(evidence/'shell.tsv').read_bytes(),*times(root,report))
    recovery=root/'retirement-evidence/notice-counts'
    if recovery.is_file():yes(recovery.read_bytes()==b'0 0\n','unexpected_notification_counts')
    else:
        observation=(root/'recovery-step2.private').read_text();yes('fallback/success=0\n0\nlastaccess_advanced=yes' in observation,'missing_zero_notification_observation')
    result.update(run_id=root.name,notification_and_fallback_messages=[0,0],lease_released=True)
    return result


def retirement_shell(case, cold):
    # Current SQLite statements are exclusively read-only. Verify the entire
    # transition history (all accepted prior cases, then precisely one failed
    # case), the on-disk post-reindex row and absence of later operations.
    return '''
owner; native_wait; stable_entry
same_hash "$INSTALLED" "$ORIGINAL_SHA"; same_hash "$STAGE/original-launcher" "$ORIGINAL_SHA"
test "$(stat -c '%s:%Y:%a' "$INSTALLED")" = "$(cat "$STAGE/original-launcher.stat")"
test ! -e "$INSTALLED.$RUN.pending"; test ! -L "$INSTALLED.$RUN.pending"
test ! -e "$INSTALLED.$RUN.restore"; test ! -L "$INSTALLED.$RUN.restore"
test "$(cat "$STAGE/case")" = "$CASE"; test ! -e "$STAGE/out/$CASE/case-ok"
test ! -L "$STAGE/out/$CASE/case-ok"
nonlink "$STAGE/out/$CASE"; nonlink "$STAGE/original-catalog.identity"; nonlink "$STAGE/original-catalog.access"
prior_access=$(cat "$STAGE/original-catalog.access")
for earlier in $PRIOR; do
    nonlink "$STAGE/out/$earlier"; test "$(cat "$STAGE/out/$earlier/case-ok")" = ok
    cmp "$STAGE/original-catalog.identity" "$STAGE/out/$earlier/catalog-before.identity"
    cmp "$STAGE/original-catalog.identity" "$STAGE/out/$earlier/catalog-after.identity"
    test "$(cat "$STAGE/out/$earlier/catalog-before.access")" = "$prior_access"
    prior_access=$(cat "$STAGE/out/$earlier/catalog-after.access")
done
f=$STAGE/out/$CASE
nonlink "$f/catalog-before.identity"; nonlink "$f/catalog-after.identity"
nonlink "$f/catalog-before.access"; nonlink "$f/catalog-after.access"; nonlink "$f/application.json"; nonlink "$f/shell.tsv"
cmp "$STAGE/original-catalog.identity" "$f/catalog-before.identity"
test "$(cat "$f/catalog-before.access")" = "$prior_access"
live_identity=$(sqlite3 -readonly -cmd '.timeout 1500' -separator '|' "$DB" "SELECT p_uuid,p_mimeType,p_thumbnail,p_modificationTime,p_contentSize FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh';")
test "$live_identity" = "$(cat "$f/catalog-after.identity")"
live_access=$(sqlite3 -readonly -cmd '.timeout 1500' "$DB" "SELECT p_lastAccess FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh';")
test "$live_access" = "$(cat "$f/catalog-after.access")"
''' + ('log=$STAGE/cases/$CASE/data/logs/launcher.log\nexpected=0\n' if cold else 'log=$STAGE/data/logs/launcher.log\nexpected=$PRIOR_COUNT\n') + '''nonlink "$log"
notifications=$(grep -cF 'aidoku: Catalog notification completed.' "$log" || :)
fallbacks=$(grep -cF 'Catalog notification unavailable;' "$log" || :)
test "$notifications" = "$expected"; test "$fallbacks" = 0
'''


def extract_evidence(data, dest):
    dest.mkdir(mode=0o700,exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        total=0
        for item in archive.getmembers():
            path=Path(item.name)
            yes(path.parts and path.parts[0]=='out' and not path.is_absolute() and '..' not in path.parts and (item.isdir() or item.isreg()), 'unsafe_private_output_archive')
            total+=item.size
            yes(item.size<=20_000_000 and total<=100_000_000,'private_output_budget')
        archive.extractall(dest,filter='data')


def isolated_remote(cmd, body, timeout, errors):
    with errors.open('wb') as err:
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,start_new_session=True)
        try:res,_=proc.communicate(body,timeout=timeout)
        except BaseException:
            try:os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.communicate()
            raise
    return proc.returncode,res


def retire(root,plan,report,device,case):
    released=root/'lease-released.json'
    yes(not released.exists() and report.get('lease_released') is False, 'already_released_token_never_reusable')
    cfg=json.loads((root/'transport.json').read_text());seed=json.loads(SEED.read_text()); run=root.name
    yes(all(cfg.get(k)==v for k,v in seed.items() if k not in ('owner_thread','lease_run_id','lease_token_file')) and cfg.get('owner_thread')==cfg.get('lease_run_id')==run and cfg.get('lease_token_file')==str(root/'lease-token') and Path(cfg.get('lease_tool_directory','')).resolve()==SHARED, 'transport_not_bound_to_lease')
    acquired=json.loads((root/'lease-acquired.json').read_text());yes(acquired.get('run_id')==run and acquired.get('ok') is True, 'not_original_acquisition')
    sys.path.insert(0,str(SHARED));from device_lease import LeaseRequest, status, release
    request=LeaseRequest(Path(cfg['lease_dir']),root/'lease-token',run,run)
    proof=status(request);yes(proof.get('ownership_verified') is True,'original_lease_no_longer_held')
    stage='/mnt/us/.'+run;prior=report['completed'];evdir=root/'retirement-evidence';evdir.mkdir(mode=0o700,exist_ok=True)
    prefix=f'RUN={run}\nSTAGE={stage}\nCASE={case}\nPRIOR="{" ".join(prior)}"\nPRIOR_COUNT={len(prior)}\nOWNER_HASH={plan["owner_hash"]}\nBRIDGE_SHA={plan["bridge_sha256"]}\nORIGINAL_SHA={ORIGINAL}\n'.encode()+device+b'\n'
    def remote(label,source,timeout=60):
        body=prefix+source.encode()+b'\n';yes(len(body)<1024*1024,'oversized_read')
        rc,res=isolated_remote([sys.executable,str(SHARED/'guarded-device-ssh.py'),'--config',str(root/'transport.json'),'sh -s'],body,timeout,evdir/(label+'.stderr'))
        yes(rc==0,'guarded_step_'+label);return res
    before=(root/'before.stdout').read_bytes(); guard=retirement_shell(case,report.get('cache_regime')=='empty-cache-v1')
    def protected(label):
        return remote(label,guard+'\nstate_snapshot')
    after=protected('check-original-state');snap(before,after)
    archive_path=root/'outputs.tar.gz'
    archive=remote('preserve-failed-samples','owner; native_wait; test "$(cat "$STAGE/case")" = "$CASE"; tar -czf - -C "$STAGE" out')
    yes(len(archive)<=60_000_000,'compressed_output_budget')
    if archive_path.exists():yes(archive_path.read_bytes()==archive,'already_collected_bytes_changed')
    else:archive_path.write_bytes(archive)
    extract_evidence(archive,root/'outputs'); found=root/'outputs/out'/case
    actual=sorted(p.name for p in (root/'outputs/out').iterdir() if p.is_dir());yes(actual==sorted(prior+[case]),'unexpected_extra_case_or_evidence')
    result=classify(before,after,(found/'catalog-before.identity').read_bytes(),(found/'catalog-after.identity').read_bytes(),(found/'catalog-before.access').read_bytes(),(found/'catalog-after.access').read_bytes(),json.loads((found/'application.json').read_text()),(found/'shell.tsv').read_bytes(),*times(root,report))
    # Recheck the entire protected snapshot and catalog state immediately before
    # deleting only our marked stage; never rewrite or retimestamp public files.
    second=protected('verify-before-stage-removal');yes(second==after,'protected_state_not_stable')
    (root/'after-functional-recovery.stdout').write_bytes(after); (evdir/'notice-counts').write_bytes(b'0 0\n')
    (evdir/'accepted-after.identity').write_bytes((found/'catalog-after.identity').read_bytes())
    (evdir/'accepted-after.access').write_bytes((found/'catalog-after.access').read_bytes())
    remote('remove-owned-stage',guard+'\nowner; rm -rf -- "$STAGE"; test ! -e "$STAGE"; test ! -L "$STAGE"; native_wait; stable_entry')
    # Validate exactly the same new catalog row and access after stage removal;
    # only digests of the already verified private values enter the shell.
    cat_id=sha((evdir/'accepted-after.identity').read_bytes());cat_access=sha((evdir/'accepted-after.access').read_bytes())
    last=f'''test ! -e "$STAGE"; test ! -L "$STAGE"
final_row=$(sqlite3 -readonly -cmd '.timeout 1500' -separator '|' "$DB" "SELECT p_uuid,p_mimeType,p_thumbnail,p_modificationTime,p_contentSize FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh';")
test "$(printf '%s\\n' "$final_row" | sha256sum | cut -d ' ' -f 1)" = {cat_id}
final_access=$(sqlite3 -readonly -cmd '.timeout 1500' "$DB" "SELECT p_lastAccess FROM Entries WHERE p_location='/mnt/us/documents/Aidoku.sh';")
test "$(printf '%s\\n' "$final_access" | sha256sum | cut -d ' ' -f 1)" = {cat_access}
state_snapshot'''
    final=remote('verify-closed-state',last)
    yes(final==after,'post_cleanup_protected_state_changed')
    receipt=release(request,native_restored_and_connections_closed=True); saved(released,receipt)
    report.update(success=False,lease_released=True,native_restored=True,entry_restored=True,installed_and_user_unchanged=False,physical_catalog_identity_preserved=False,same_boot=True,input_roster_unchanged=True,temporary_stage_absent=True,release_scope='Operator-owned bridge and stage retired. Native document timestamp and replacement catalog identity preserved. All other protected observations match. Invalid batch; no performance result.')
    saved(root/'report.json',report)
    result.update(run_id=run,notification_and_fallback_messages=[0,0],lease_released=True)
    return result


def main():
    yes(sys.platform=='linux','test_env_required');os.umask(0o077)
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=('audit','release'));ap.add_argument('--batch',type=Path,required=True);args=ap.parse_args()
    root,plan,report,device,case=batch_input(args.batch)
    result=audit(root,report,case) if args.action=='audit' else retire(root,plan,report,device,case)
    print(json.dumps(result,sort_keys=True));return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Rejected as error:print(json.dumps({'accepted':False,'reason':str(error),'batch_eligible':False}));raise SystemExit(2)
    except Exception:print(json.dumps({'accepted':False,'reason':'bounded_operation_failed','batch_eligible':False}));raise SystemExit(3)
