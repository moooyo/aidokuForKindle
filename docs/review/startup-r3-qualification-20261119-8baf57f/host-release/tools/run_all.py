#!/usr/bin/env python3
"""Install the verified six-feature production package or undo only this transaction.

Run with --check-only on test-env before an authorized physical operator runs
the same plan. All tested defaults and optional behaviors must already be
independently qualified. User and shared registration state are not deployed.
"""

import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import uuid
import zipfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import capture_private as cap
import release_common as c
import r3_lineage

REJECTED_INTEGRATED = {'3ef03a4124970139fe46d3873c4b9fc7d5cd25af55e66482e497dca46b584375'}

# These snapshots bracket the file transaction while Aidoku is stopped. They
# are intentionally separate from the logical projection around normal opens:
# normal logs, navigation checkpoints and cache access can legitimately change.
TRANSACTION_DATA = '''
data=/mnt/us/aidoku/data
cache=$data/cache/shared-v1
test ! -L "$data" && test "$(readlink -f "$data")" = "$data"
for path in library.sqlite3 library.sqlite3-wal library.sqlite3-shm library.sqlite3-journal device-profile startup-options startup-options.conf cache/shared-v1/index.sqlite3 cache/shared-v1/index.sqlite3-wal cache/shared-v1/index.sqlite3-shm cache/shared-v1/index.sqlite3-journal; do
    f=$data/$path; test ! -L "$f" || exit 1
    if [ -e "$f" ]; then
        test -f "$f" && test "$(readlink -f "$f")" = "$f" || exit 1
        checksum=$(sha256sum "$f") || exit 1; attributes=$(stat -c '%s:%Y:%a' "$f") || exit 1
        printf 'file %s %s %s\\n' "$path" "${checksum%% *}" "$attributes"
    else printf 'absent %s\\n' "$path"; fi
done
test ! -L "$cache" && test -d "$cache" && test "$(readlink -f "$cache")" = "$cache"
for dir in raw processed; do test -d "$cache/$dir" && test ! -L "$cache/$dir" || exit 1; done
cd "$cache"
# Stream names, sizes, modes, inode numbers and modification/change times.
# No cache path list or user title is sent to the host. A failing find/stat/
# sort cannot accidentally turn into a successful digest of partial data.
roster=$( { { if find raw processed -print0; then echo find-ok >&3; else echo find-failed >&3; fi; } |
            { if xargs -0 -r stat -c '%n|%f|%i|%s|%Y|%Z'; then echo stat-ok >&3; else echo stat-failed >&3; fi; } |
            { if LC_ALL=C sort; then echo sort-ok >&3; else echo sort-failed >&3; fi; } | sha256sum; } 3>&1 )
test "$(printf '%s\\n' "$roster" | wc -l)" -eq 4
for mark in find-ok stat-ok sort-ok; do printf '%s\\n' "$roster" | grep -Fxq "$mark" || exit 1; done
hash=$(printf '%s\\n' "$roster" | sed -n 's/^\\([0-9a-f]\\{64\\}\\)  -$/\\1/p'); test "${#hash}" = 64
printf 'cache-artifact-roster %s\\n' "$hash"
'''

def freeze(plan_path, expected):
    plan=c.unique_json(c.exact(plan_path,expected,256*1024))
    c.require(plan.get('schema')==2 and plan['baseline_sha256']==c.BASE and plan['catalog_registry']==c.REG,
              'Release plan is not based on the currently installed formal entry')
    r3_lineage.generation(plan)
    c.require(c.validate_policy(plan['features'])==plan['policy_sha256'] and
              set(plan['feature_qualifications'])==c.FEATURES and c.valid_sha(plan['source_manifest_sha256']),
              'Release plan does not bind all six policies and qualifications')
    evidence=plan['evidence_sha256'];c.require(isinstance(evidence,dict) and len(evidence)>=3 and
        all(c.valid_sha(v) for v in evidence.values()),'The release evidence is incomplete')
    zip_path=plan_path.parent/'aidoku-startup-all.zip'
    c.exact(zip_path,plan['archive_sha256']);c.exact(plan_path.parent/'aidoku-startup-all.kpkg',plan['kpm_sha256'])
    rows=plan['changes'];c.require(isinstance(rows,list) and len(rows)==3 and {r['path'] for r in rows}==set(c.DEPLOYED),
                                    'Expected only the app, launcher and one fixed native helper')
    c.require([r['path'] for r in rows]==[c.EXTRA,c.APP,c.LAUNCH], 'The launcher cannot be selected before its program and helper')
    files={}; selected={}
    with zipfile.ZipFile(zip_path) as z:
        for r in rows:
            name,old,new,path=r['path'],r['old'],r['new'],r['file']
            c.require(old==c.DEPLOYED[name] and path=='delta/'+name and new!=old and new not in {c.PREVIOUS,c.RETIRED,*REJECTED_INTEGRATED},
                      'Unqualified program change or rollback identity')
            data=c.exact(plan_path.parent/path,new)
            c.require(z.namelist().count(name)==1 and z.read(name)==data,'Archive and delta differ')
            files['payload/'+name]=(data,0o600); selected[name]=new
    c.require(plan['baseline_archive_sha256']==c.PACKAGED_ZIP and plan['baseline_kpm_sha256']==c.PACKAGED_KPM and
        plan['program_sha256']==selected,'Release plan inputs do not match the previous qualified packages or measured tuple')
    c.require(set(plan['default_nonregression'])=={'warm','cold'} and
        {'build','default_warm','default_cold','production_defaults'}<=(evidence.keys()) and
        all(plan['default_nonregression'][regime]['paired'].get('regression_guard_passed') is True and
            plan['default_nonregression'][regime]['paired'].get('n')==6 for regime in ('warm','cold')),
        'The release must include two independent non-regression comparisons and a physical defaults qualification')
    for feat,qualification in plan['feature_qualifications'].items():
        c.require(qualification['availability']=='qualified' and qualification['program_sha256']==selected and
                  qualification['source_manifest_sha256']==plan['source_manifest_sha256'] and
                  qualification['default_policy']==plan['features'][feat] and feat+'_screen' in evidence,
                  'A release policy is not bound to the measured feature')
        conclusion=qualification['conclusion']
        c.require(conclusion in ('confirmed_benefit','available_default_off','functional_default_no_speed_claim') and
                  (conclusion!='confirmed_benefit' or feat+'_confirm' in evidence) and
                  (conclusion!='available_default_off' or plan['features'][feat]=='default_off_optional'),
                  'The product claim differs from its separate classified evidence')
    files['changes.tsv']=(''.join(f'{r["old"]}|{r["new"]}|{r["path"]}\n' for r in rows).encode('ascii'),0o600)
    return plan, files, selected


def archive_readback(plan_path,plan,install=True):
    """Re-read both archives, modes and notices before ever opening a lease."""
    gate=c.module('final_release_archives',c.raw(HERE/'prepare_all.py',512*1024),HERE/'prepare_all.py')
    zold,kold=gate.read_control();util=gate.old_utilities()
    changes={r['path']:(r['old'],r['new']) for r in plan['changes']}
    for name,old,kind in [('aidoku-startup-all.zip',zold,'zip'),('aidoku-startup-all.kpkg',kold,'kpm')]:
        gate.verify_allowed_delta(old,util.read_archive(plan_path.parent/name,kind),changes,kpm=kind=='kpm')
    verification=plan_path.parent/'package-verification.json'
    r3=plan.get('release_generation')=='r3-launcher-only'
    check=c.unique_json(c.exact(verification,plan['package_verification_sha256'],4*1024*1024) if r3 else c.raw(verification,4*1024*1024))
    c.require(check['passed'] is True and check['source_sha256']==plan['source_manifest_sha256'] and
        check['archive_sha256']==plan['archive_sha256'] and check['application_sha256']==plan['program_sha256'][c.APP]
        and len(check['checks'])>=211 and all(r['passed'] is True for r in check['checks']),
        'The complete production package verification does not match the plan')
    if r3 and install:
        ref=plan['selection'];expected=plan['selection_sha256']
        c.require(ref['sha256']==expected,'Installer selection is not the measured release')
        frozen=gate.read_json(gate.bound(plan['r3_lineage']['inputs'],256*1024))
        selection=gate.read_json(gate.bound(ref,1024*1024))
        rebuilt=gate.build(selection,expected,Path(frozen['source_root']),selection_ref=ref)
        package_fields={'archive_sha256','changes','kpm_sha256','package_verification_sha256'}
        c.require(rebuilt=={k:v for k,v in plan.items() if k not in package_fields},
                  'Install qualification did not reproduce from the exact published selection')


def coordinator(require_factory_defaults=True):
    # Rebind the historical generic transaction to the installation verified
    # on November 18 before loading any transport code. Never run its old CLI.
    source=c.historical('run.py').decode('ascii')
    source=c.once(source,str(c.PAST.parent),str(c.ROOT)); source=c.once(source,c.PREVIOUS,c.BASE)
    source=c.once(source,"stage = '/mnt/us/.aidoku-next-'", "stage = '/mnt/us/.aidoku-all-'")
    left=source.index('def freeze('); right=source.index('def check_files(',left)
    source=source[:left]+source[right:source.index('def main():')]
    anchor="            outcome['matching_observable_right_toolbar'] = matching_toolbar_observations(data)"
    source=c.once(source,anchor,anchor+'''\n            archive_body = guard + 'RUN=' + q(tx) + '\\n' + collection_body
            session.call('private-evidence', archive_body, timeout=100)''')
    point='        install_attempted = install'
    source=c.once(source,point,"        data_before = session.call('pre-transaction-storage', TRANSACTION_DATA, timeout=100)\n"+point)
    point="        session.call('verify-program', protection + program(wanted))"
    source=c.once(source,point,point+'''\n        data_after = session.call('post-transaction-storage', TRANSACTION_DATA, timeout=100)
        require(data_before == data_after and bool(data_before), 'User database or cache storage changed during program replacement')
        outcome['transaction_user_database_and_cache_unchanged'] = True
        if install:
            session.call('verified-program-backup', guard + backup_script(backup,tx), timeout=140)''')
    c.require(c.PREVIOUS not in source and 'UPDATE properties' not in source,'A retired device guard entered the release coordinator')
    engine=c.module('current_release_engine',source.encode(),HERE/'run_all.py')
    c.require(engine.DEPLOYED==c.DEPLOYED and engine.REGISTRATION==c.REG and engine.ROOT==c.ROOT,'Incorrect runtime ownership boundary')
    protected_user=c.DATA_SUFFIX
    if not require_factory_defaults:
        # A later rollback must not require deleting a personal startup choice.
        # The app is stopped; preserve configuration and queued records rather
        # than claiming this is another factory-default smoke.
        protected_user=c.once(protected_user,'test "$queued" = 0',
            '''case "$queued" in ''|*[!0-9]*) exit 1;; esac; printf 'queued %s\\n' "$queued"''')
        protected_user=c.once(protected_user,'    test ! -e "$setting" && test ! -L "$setting"',
            '''    test ! -L "$setting" || exit 1
    if [ -e "$setting" ]; then test -f "$setting"; value=$(sha256sum "$setting") || exit 1;
        printf 'personal-option %s %s\\n' "${setting##*/}" "${value%% *}"; else printf 'absent %s\\n' "${setting##*/}"; fi''')
    engine.STATE+=c.NO_PROGRAM+'no_program_image /mnt/us/aidoku/app/aidoku\n'+protected_user
    engine.TRANSACTION_DATA=TRANSACTION_DATA;engine.backup_script=backup_script
    engine.collection_body=cap.collection_shell({item:item for item in ('before','after','wake')})
    old_cleanup=engine.stage_cleanup
    def cleanup(stage, tx, files):
        return engine.stage_guard(stage,tx)+'RUN='+engine.q(tx)+'\n'+cap.cleanup_shell()+old_cleanup(stage,tx,files)
    engine.stage_cleanup=cleanup
    return engine


def backup_script(directory,run):
    c.require(directory=='/mnt/us/.aidoku-performance-backups/'+run and re.fullmatch(r'promote-[a-f0-9]{12}',run),
              'Unexpected program backup directory')
    return f'''test ! -L {directory} && test "$(readlink -f {directory})" = {directory}
test -f changes.tsv && test ! -L changes.tsv
cmp -s changes.tsv {directory}/changes.tsv
cd {directory}
for item in owner sealed installed aidoku.intent launch.sh.intent aidoku-startup-portrait.intent; do
   test -f "$item" && test ! -L "$item" && printf '%s\\n' {run} | cmp -s - "$item" || exit 1
done
for item in changes.tsv aidoku.mode launch.sh.mode aidoku.original launch.sh.original; do test -f "$item" && test ! -L "$item" || exit 1; done
test ! -e aidoku-startup-portrait.original && test ! -L aidoku-startup-portrait.original
a=$(sha256sum aidoku.original); l=$(sha256sum launch.sh.original); test "${{a%% *}}" = {c.BASE} && test "${{l%% *}}" = {c.LAUNCH_SHA}
tar -czf - owner sealed installed changes.tsv aidoku.mode launch.sh.mode aidoku.intent launch.sh.intent aidoku-startup-portrait.intent aidoku.original launch.sh.original
'''


def verify_backup(path,run,changes):
    c.require(path.is_file() and not path.is_symlink() and path.stat().st_size<=70*1024*1024,'Incomplete private original-program backup')
    markers={'owner','sealed','installed','aidoku.intent','launch.sh.intent','aidoku-startup-portrait.intent'}
    limits={name:80 for name in markers};limits.update({'changes.tsv':600,'aidoku.mode':8,'launch.sh.mode':8,'aidoku.original':76*1024*1024,'launch.sh.original':128*1024})
    data={}
    with tarfile.open(path,'r:gz') as archive:
        for item in archive:
            c.require(item.name in limits and item.name not in data and item.isreg() and not item.sparse and 0<item.size<=limits[item.name],
                      'Unexpected or unbounded program backup member')
            data[item.name]=archive.extractfile(item).read(item.size+1)
            c.require(len(data[item.name])==item.size,'Truncated backup member')
    c.require(data.keys()==limits.keys() and all(data[name]==(run+'\n').encode() for name in markers) and
        data['changes.tsv']==changes and c.sha(data['aidoku.original'])==c.BASE and c.sha(data['launch.sh.original'])==c.LAUNCH_SHA and
        all(re.fullmatch(rb'[0-7]{3}\n',data[name]) for name in ('aidoku.mode','launch.sh.mode')),'Installed program rollback proof differs')
    return True


def stage_archive(plan_files, owner, install, engine):
    files=dict(plan_files) if install else {'changes.tsv':plan_files['changes.tsv']}
    files['transaction.sh']=(c.historical('transaction.sh'),0o755)
    files['owner']=((owner+'\n').encode('ascii'),0o600)
    if install:
        smoke=c.once(cap.materialize(c.historical('smoke.sh')),b'/mnt/us/.aidoku-next-',b'/mnt/us/.aidoku-all-')
        files['smoke.sh']=(smoke,0o755);files['capture']=(c.exact(engine.CAPTURE,engine.CAPTURE_SHA,2*1024*1024),0o755)
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w:gz') as archive:
        for name,(data,mode) in files.items():
            if name in ('smoke.sh','transaction.sh'):subprocess.run(['sh','-n'],input=data,check=True)
            h=tarfile.TarInfo(name);h.size=len(data);h.mode=mode;archive.addfile(h,io.BytesIO(data))
    return buf.getvalue(),files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--plan-sha256',required=True)
    p.add_argument('--operation',choices=('install-smoke','rollback'),required=True)
    p.add_argument('--transaction-run',help='Original transaction, under a fresh lease for rollback')
    p.add_argument('--check-only',action='store_true');args=p.parse_args()
    c.require(sys.platform=='linux','Run through ssh test-env');os.umask(0o077)
    c.installed_reference();install=args.operation=='install-smoke';engine=coordinator(install)
    c.require((install and args.transaction_run is None) or (not install and engine.ORIGINAL_RUN.fullmatch(args.transaction_run or '')),
              'Invalid rollback transaction identity')
    root='promote-' if install else 'undo-'; run=root+uuid.uuid4().hex[:12];tx=run if install else args.transaction_run
    plan,inputs,updated=freeze(args.plan,args.plan_sha256);archive_readback(args.plan,plan,install)
    payload,files=stage_archive(inputs,tx,install,engine)
    if args.check_only:
        print(json.dumps({'operation':args.operation,'prepared':True,'program_changes':len(plan['changes']),
                          'baseline_application_sha256':c.BASE,'device_accessed':False,'lease_acquired':False}));return
    session=engine.Transport(run); result=engine.perform(session,payload,files,tx,updated,install)
    if result['success'] and result['lease_released'] and install:
        try:
            files=[session.batch/(f'{i:02d}-private-evidence.stdout') for i,rec in enumerate(session.calls) if rec['operation']=='private-evidence' and rec['exit']==0]
            c.require(len(files)==1,'Missing verified private capture archive')
            result['physical_evidence']=cap.check_capture_archive(files[0]);result['private_raw_captures_preserved']=True
            programs=[session.batch/(f'{i:02d}-verified-program-backup.stdout') for i,rec in enumerate(session.calls) if rec['operation']=='verified-program-backup' and rec['exit']==0]
            c.require(len(programs)==1,'A matching host-side rollback copy is unavailable')
            result['original_program_backup_reverified_after_release']=verify_backup(programs[0],tx,inputs['changes.tsv'][0])
        except (ValueError,OSError,EOFError,tarfile.TarError):
            result.update(success=False,qualified=False,private_raw_captures_preserved=False,
                          post_release_observation_failed=True,rollback_requires_fresh_lease=True)
    result.update(baseline_application_sha256=c.BASE,policy_sha256=plan['policy_sha256'],program_changes=len(plan['changes']))
    (session.batch/'report.json').write_text(json.dumps(result|{'calls':session.calls},indent=2)+'\n')
    print(json.dumps(result),flush=True);raise SystemExit(0 if result['success'] and result['lease_released'] else 1)


if __name__=='__main__':main()
