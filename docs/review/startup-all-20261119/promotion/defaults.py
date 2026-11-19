#!/usr/bin/env python3
"""Independently qualify both normally configured launchers on the indexed Library route.

Preparation and verification do not connect to a Kindle. The coordinating
operator alone invokes the frozen runner. Bench fixtures and physical images
are private, and never shared with product/user data or release contents.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import capture_private as cap
import release_common as c
import r3_lineage

BENCH=Path('/root/aidoku-all-bench-20261119/base-cold')
PINS={'runner':'bcc36a9ce043af28b4d077e30280de251d47157a08e9c4816786c658f56c5883',
      'device':'baacc109dcbc1716f5bfca4471627b8fc55961da1b203b580e426f6a31f91e40'}
CAPTURE=Path('/root/aidoku-native-query-20261117/tools/framebuffer-dump')
CAPTURE_SHA='d728222e1363405564ae55f6ee32a850ac918cf6401cc013e0a84b50adf44c90'
CASES=['baseline-default','candidate-default']
REGIME='empty-cache-v2'
POLICY={'supervisor':('portrait','shell','native'),'prewarm':('prewarm','0','2'),
        'grace':('last_cover_grace','off','on'),'layout':('first_layout','baseline','optimized'),
        'resident':('resident','off','45'),'import':('import_thumbnail','off','idle')}


def reference():
    # Only the previous per-case observer and capture contracts are reused.
    # The rebased device/runner below do not contain the old app baseline.
    return c.module('audited_lifecycle_generation',c.historical('default_lifecycle.py'),c.PAST/'default_lifecycle.py')


def runner_text():
    r=c.exact(BENCH/'run_bench.py',PINS['runner']).decode('ascii')
    r=c.once(r,str(BENCH),str(c.ROOT));r=c.once(r,"'run_case '+shlex.quote(case),timeout=80", "'run_case '+shlex.quote(case),timeout=160")
    r=c.once(r,"    if ns.action=='prepare':prepare(ns)","    if ns.action=='prepare': raise ValueError('Prepare defaults with defaults.py')")
    point="        before=(batch/'before.stdout').read_bytes()"
    r=c.once(r,point,"""        if probe=='owned' and report.get('cases_completed') and not (batch/'native-private.tar').exists():
            f=remote('collect-native-private','owner\\ncollect_private_capture',timeout=100)
            shutil.copyfile(f,batch/'native-private.tar')
"""+point)
    r=c.once(r,"native_wait\\nrm -rf --", "native_wait\\nclear_private_capture\\nrm -rf --")
    c.require(c.PREVIOUS not in r and c.BASE in r,'Retired physical guard in current runner');return r.encode('ascii')


def bridge(run):
    c.require(re.fullmatch('aidoku-all-life-[a-f0-9]{8}',run),'Unexpected physical run identity')
    return f'''#!/bin/sh
set -eu
test "$#" = 1 && test "$1" = --library || exit 64
test "${{AIDOKU_CATALOG_NOTIFY_HELPER:-}}" = {c.REG.rsplit('/',1)[0]}/aidoku-catalog-client || exit 69
test "${{AIDOKU_CATALOG_NOTIFY_SHA256:-}}" = f46a0225d5a265f5f9749029fc115060576508ee5caadf25becb661738b88161 || exit 69
stage=/mnt/us/.{run}
test "$(readlink -f "$stage")" = "$stage" && test "$(cat "$stage/owner")" = {run} || exit 73
case "$(cat "$stage/case")" in baseline-default) v=baseline;; candidate-default) v=candidate;; *) exit 64;; esac
export AIDOKU_HOME="$stage/$v"
exec sh "$AIDOKU_HOME/launch.sh" --library
'''.encode('ascii')


def device_text():
    modern=c.exact(BENCH/'bench_device.sh',PINS['device']).decode('ascii')
    legacy=reference().device_text(True).decode('ascii')
    before=legacy.index('run_case() {');after=legacy.index('restore_launcher() {',before)
    case=legacy[before:after].replace('empty-cache-v1','empty-cache-v2')
    point='    printf \'%s\\n\' "$case_id" >"$STAGE/case.new"'
    case=c.once(case,point,'''    for opts in startup-options startup-options.conf; do
        test ! -e "$STAGE/$v/data/$opts" && test ! -L "$STAGE/$v/data/$opts"; done
    printf 'absent\\n' >"$OUT/production-options-before"
    if [ "$v" = candidate ]; then
        "$STAGE/$v/app/aidoku" --print-startup-options --data-dir "$STAGE/$v/data" >"$OUT/production-defaults.txt"
        test "$(wc -c <"$OUT/production-defaults.txt")" -le 400
    fi
''' +point)
    point='    stable_entry; native_wait; same_hash "$INSTALLED" "$BRIDGE_SHA"'
    case=c.once(case,point,'''    for opts in startup-options startup-options.conf; do
        test ! -e "$STAGE/$v/data/$opts" && test ! -L "$STAGE/$v/data/$opts"; done
    printf 'absent\\n' >"$OUT/production-options-after"
    no_program_image "$STAGE/$v/app/aidoku"; printf 'absent\\n' >"$OUT/after-ordinary-exit-image"
'''+point)
    start=modern.index('run_case() {');end=modern.index('restore_launcher() {',start)
    modern=modern[:start]+case+modern[end:]
    # Recovery verifies PID/birth/session and the normal catalog entry; it does
    # not signal merely because an executable has a familiar filename.
    head=modern.index('    if [ -e "$LOCK" ]; then',modern.index('restore_launcher() {'))
    finish=modern.index('    native_wait\n',head)
    old_head=legacy.index('    if [ -f "$STAGE/case" ]; then',legacy.index('restore_launcher() {'))
    old_finish=legacy.index('    native_wait\n',old_head)
    modern=modern[:head]+legacy[old_head:old_finish]+modern[finish:]
    c.require(c.PREVIOUS not in modern and c.BASE in modern and 'UPDATE properties' not in modern,'Unexpected physical recovery target')
    capture_aliases={label+'-'+moment:'out/'+label+'/'+moment for label in CASES for moment in ('library','wake')}
    modern+='\ncollect_private_capture() (\nowner; cd "$STAGE"\n'+cap.collection_shell(capture_aliases)+')\n'
    modern+='\nclear_private_capture() (\nowner\n'+cap.cleanup_shell()+')\n'
    return (c.NO_PROGRAM+modern).encode('ascii')


def library(): return cap.materialize(c.historical('smoke.sh')).split(b'# Entry point.')[0]


def observer():
    # These functions audit only completed case observations and restore proofs.
    # Replacing the assembly authority prevents accepting an obsolete runner.
    src=c.historical('analyze_lifecycle.py').decode('ascii');src=src[:src.index('def reference_runs(')]
    src=c.once(src,"gate = load('_prepare_lifecycle', HERE / 'prepare.py')",'import release_common as gate')
    src=c.once(src,"maker = load('_make_default_lifecycle', HERE / 'default_lifecycle.py')",'maker = None')
    src=c.once(src,'check, sha = gate.check, gate.sha','check, sha = gate.require, gate.sha')
    src=c.once(src,'aidoku-next-life-[0-9a-f]{8}', 'aidoku-all-life-[0-9a-f]{8}')
    # Shipping the optional native helper does not imply it is enabled. Check
    # the exact mode reported by the production parser and the real launcher.
    src=c.once(src,"[b'aidoku: Startup portrait supervisor: native.']", "[b'aidoku: Startup portrait supervisor: ' + gate.expected_portrait + b'.']")
    src=src.replace("'empty-cache-v1'","'empty-cache-v2'").replace("b'empty-cache-v1\\n'","b'empty-cache-v2\\n'")
    src=c.once(src,'item, 1024 * 1024)','item, 14_000_000)')
    src=c.once(src,"raw(extracted / name) == data", "raw(extracted/name, 14_000_000) == data")
    src=src.replace('33 * 1024 * 1024','62 * 1024 * 1024')
    src=c.once(src,"'restore-light', 'after', 'remove-owned-stage']", "'collect-native-private', 'restore-light', 'after', 'remove-owned-stage']")
    result=c.module('current_default_observer',src.encode(),HERE/'defaults.py')
    result.gate=SimpleNamespace(unique_json=c.unique_json,safe_member=lambda x: c.require(re.fullmatch(r'[A-Za-z0-9_./-]+',x)
           and not x.startswith('/') and '.' not in x.split('/') and '..' not in x.split('/') and 'data' not in x.split('/'), 'Invalid private archive path'),
           REGISTRATION=c.REG,APP=c.APP,LAUNCH=c.LAUNCH,QUERY=c.QUERY,JPEG=c.JPEG,EXTRA=c.EXTRA,
           DEPLOYED=c.DEPLOYED,digest=lambda x: x if c.valid_sha(x) else c.require(False,'Invalid proof digest'))
    result.maker=SimpleNamespace(runner_text=runner_text,device_text=lambda _:device_text())
    return result


def binding(path, sha):return c.exact(path,sha,256*1024)


def fixed_outputs(program):
    c.require(set(program)=={c.APP,c.LAUNCH,c.EXTRA} and all(c.valid_sha(x) for x in program.values()) and
        program[c.APP] not in {c.BASE,c.PREVIOUS,c.RETIRED} and program[c.LAUNCH]!=c.LAUNCH_SHA,'Candidate program tuple differs')


def expected_options(policy):
    c.validate_policy(policy)
    return {entry[0]:entry[2 if policy[feature]=='default_on' else 1] for feature,entry in POLICY.items()}


def parse_options(raw):
    values={}
    for line in raw.decode('ascii').splitlines():
        k,sep,v=line.partition('=')
        c.require(sep and k not in values and k in {v[0] for v in POLICY.values()},'Invalid option readback')
        c.require(v in next(vals[1:] for vals in POLICY.values() if vals[0]==k),'Invalid option value')
        values[k]=v
    c.require(len(values)==6,'Missing default readback');return values


def prepare(args):
    c.installed_reference(); artifacts={c.APP:args.application,c.LAUNCH:args.launcher,c.EXTRA:args.helper}
    authority=c.unique_json(binding(args.authority,args.authority_sha256)); program=authority['program_sha256']
    fixed_outputs(program); features=authority['features'];policy_hash=c.validate_policy(features)
    c.require(policy_hash==authority['policy_sha256'] and authority['baseline_sha256']==c.BASE and
              c.valid_sha(authority['source_manifest_sha256']),'Unbound build or factory configuration')
    # Historical R2 observations remain reproducible, but never start a fresh
    # R2 qualification or silently measure a different launcher under its name.
    version=r3_lineage.generation(authority)
    candidate={k:c.exact(v,program[k]) for k,v in artifacts.items()}
    c.require(candidate[c.APP][:6]==b'\x7fELF\x01\x01' and candidate[c.EXTRA][:6]==b'\x7fELF\x01\x01' and
              candidate[c.LAUNCH].startswith(b'#!/bin/sh\n'),'Different program format')
    prior=c.exact(c.PREVIOUS_PACKAGE/'aidoku-startup-next.zip',c.PACKAGED_ZIP)
    import zipfile
    with zipfile.ZipFile(io.BytesIO(prior)) as previous: old_launcher=previous.read(c.LAUNCH)
    c.require(c.sha(old_launcher)==c.LAUNCH_SHA,'Unexpected baseline launcher')
    run='aidoku-all-life-'+uuid.uuid4().hex[:8];batch=c.ROOT/'private'/run;batch.mkdir(mode=0o700,parents=True)
    files={'owner':((run+'\n').encode(),0o600),'capture':(c.exact(CAPTURE,CAPTURE_SHA,2*1024*1024),0o755),
            'bridge.sh':(bridge(run),0o755),'lifecycle_device.sh':(c.historical('lifecycle_device.sh'),0o755),
            'smoke-lib.sh':(library(),0o600),'bench-lib.sh':(c.exact(BENCH/'bench_device.sh',PINS['device']),0o600)}
    for side in ('baseline','candidate'):
        data=candidate if side=='candidate' else {}; app=program[c.APP] if side=='candidate' else c.BASE
        files.update({side+'/launch.sh':(data.get(c.LAUNCH,old_launcher),0o755),side+'/binary-hash':((app+'\n').encode(),0o600),
               side+'/binary-ref':(('installed\n' if side=='baseline' else app+'\n').encode(),0o600)})
        if side=='candidate':files['bin-'+app]=(data[c.APP],0o755);files[side+'/app/aidoku-startup-portrait']=(data[c.EXTRA],0o755)
    files['inputs.sha256']=(''.join(f'{c.sha(data)}  {name}\n' for name,(data,_) in files.items()).encode(),0o600)
    for k,(d,_) in files.items():
        if k.endswith('.sh'):subprocess.run(['sh','-n'],input=d,check=True)
    with tarfile.open(batch/'payload.tar.gz','w:gz',compresslevel=1) as archive:
        for name,(d,mode) in files.items():
            entry=tarfile.TarInfo(name);entry.size=len(d);entry.mode=mode;archive.addfile(entry,io.BytesIO(d))
    runner,dev=runner_text(),device_text();subprocess.run(['sh','-n'],input=dev,check=True)
    (batch/'runner.py').write_bytes(runner);(batch/'device.sh').write_bytes(dev);(batch/'original-launcher').write_bytes(old_launcher)
    record={'run_id':run,'mode':'default-lifecycle','cases':CASES,'program_sha256':program,'baseline_sha256':c.BASE,
        'source_manifest_sha256':authority['source_manifest_sha256'],'features':features,'policy_sha256':policy_hash,
        'cache_regime':REGIME,'owner_hash':c.sha(files['owner'][0]),'bridge_sha256':c.sha(files['bridge.sh'][0]),
        'authority_sha256':args.authority_sha256,'payload_sha256':c.sha((batch/'payload.tar.gz').read_bytes()),
        'runner_sha256':c.sha(runner),'device_script_sha256':c.sha(dev),'original_launcher_sha256':c.LAUNCH_SHA}
    if version: record.update(version)
    (batch/'authority.json').write_bytes(binding(args.authority,args.authority_sha256));(batch/'plan.json').write_bytes(c.canonical(record)+b'\n')
    return batch/'plan.json'


def archive_inputs(batch, record, program, audited):
    run=record['run_id']; original=c.exact(batch/'original-launcher',c.LAUNCH_SHA)
    c.require(c.raw(batch/'original-program-backup.stdout')==original,'Original launcher backup differs')
    c.require(record['bridge_sha256']==c.sha(bridge(run)) and record['owner_hash']==c.sha((run+'\n').encode()),'Bridge or owner differs')
    expected={'owner':record['owner_hash'],'capture':CAPTURE_SHA,'bridge.sh':record['bridge_sha256'],
              'lifecycle_device.sh':c.PIN['lifecycle_device.sh'],'smoke-lib.sh':c.sha(library()),'bench-lib.sh':PINS['device'],
              'bin-'+program[c.APP]:program[c.APP],'candidate/app/aidoku-startup-portrait':program[c.EXTRA]}
    for side in ('baseline','candidate'):
        app=c.BASE if side=='baseline' else program[c.APP];launcher=c.LAUNCH_SHA if side=='baseline' else program[c.LAUNCH]
        expected[side+'/launch.sh']=launcher;expected[side+'/binary-hash']=c.sha((app+'\n').encode())
        expected[side+'/binary-ref']=c.sha((('installed' if side=='baseline' else app)+'\n').encode())
    got={}; manifest={};payload=batch/'payload.tar.gz';c.exact(payload,record['payload_sha256'])
    def item(name,data):
        if name!='inputs.sha256':got[name]=c.sha(data);return
        for line in data.splitlines():
            m=re.fullmatch(rb'([0-9a-f]{64})  ([a-zA-Z0-9_./-]+)',line)
            c.require(m and m[2].decode() not in manifest,'Malformed or duplicate stage input')
            manifest[m[2].decode()]=m[1].decode()
    audited.archive_members(payload,item,90*1024*1024)
    c.require(expected==got==manifest,'Default sample did not use unmodified qualified programs')


def verify(batch, plan_sha256):
    batch=Path(batch);run=batch.name
    c.require(batch.parent==c.ROOT/'private' and batch.resolve(strict=True)==batch and re.fullmatch('aidoku-all-life-[a-f0-9]{8}',run),
              'Unrecognized private new-release batch')
    plan=c.unique_json(c.exact(batch/'plan.json',plan_sha256,128*1024));p=plan['program_sha256'];fixed_outputs(p)
    c.require(plan['run_id']==run and plan['mode']=='default-lifecycle' and plan['cases']==CASES and plan['baseline_sha256']==c.BASE and
              plan['cache_regime']==REGIME and c.valid_sha(plan['source_manifest_sha256']),'Wrong references, cases or workload')
    policy=plan['features'];expected_config=expected_options(policy)
    c.require(c.validate_policy(policy)==plan['policy_sha256'],'Unbound selected configuration')
    auth=c.unique_json(c.exact(batch/'authority.json',plan['authority_sha256'],256*1024))
    c.require(all(auth.get(k)==plan[k] for k in ('program_sha256','baseline_sha256','features','policy_sha256','source_manifest_sha256')),
              'Feature/production identity differs from the frozen build authority')
    version=r3_lineage.generation(auth,prior_observation=True)
    c.require(all(plan.get(key)==(version or {}).get(key) for key in ('release_generation','r3_lineage_sha256')),
              'The default lifecycle plan is not linked to the measured launcher generation')
    observed=observer();archive_inputs(batch,plan,p,observed)
    observed.gate.expected_portrait=expected_config['portrait'].encode('ascii')
    restored=observed.check_operating_batch(batch,plan)
    cases=[];configs={};records={}
    for side in ('baseline','candidate'):
        directory=batch/'outputs/out'/(side+'-default')
        c.require(all(c.raw(directory/n,15)==b'absent\n' for n in ('production-options-before','production-options-after')),
                  'A private override was active or created during normal startup')
        c.require(c.raw(directory/'after-ordinary-exit-image',15)==b'absent\n','The application or its keeper remained after ordinary exit')
        if side=='candidate':
            configs=parse_options(c.raw(directory/'production-defaults.txt',410))
            c.require(configs==expected_config,'Production option readback differs from the selected default policy')
            engines=re.findall(rb'(?m)^aidoku: Startup portrait supervisor: (native|shell)\.$', c.raw(directory/'launcher.log',65536))
            c.require(engines==[configs['portrait'].encode()],'Actual portrait command did not exercise the factory policy')
        else:
            c.require(not (directory/'production-defaults.txt').exists(),'Baseline unexpectedly accepted a feature override')
        prog=(p if side=='candidate' else {c.APP:c.BASE,c.LAUNCH:c.LAUNCH_SHA}) | {c.QUERY:c.QUERY_SHA,c.JPEG:c.JPEG_SHA}
        cases.append(observed.case_evidence(directory,run,side,prog,REGIME,check_toolbar=True))
        for moment in ('library','wake'):
            records[directory.name+'-'+moment]=(c.raw(directory/(moment+'.sha'),72),c.raw(directory/(moment+'.right.tsv'),256))
    a,b=cases
    c.require(a['seed']==b['seed'] and a['fixture']==b['fixture'] and a['catalog']==b['catalog'] and
              a['access_before']<a['access_after']<=b['access_before']<b['access_after'],
              'Library fixture, item identity or chronology changed across variants')
    archive=batch/'native-private.tar'
    c.require(c.raw(batch/'collect-native-private.stdout',62*1024*1024)==c.raw(archive,62*1024*1024) and
              c.raw(batch/'collect-native-private.stderr',128)==b'','Independent native capture transport failed')
    images=cap.check_capture_archive(archive,tuple(records),records)
    checks={'pixels':images['body_pixels_match'] and all(r['frame']==r['wake']==cases[0]['frame'] for r in cases),
        'right_status_bar':all(r['toolbar']==r['wake_toolbar']==a['toolbar'] for r in cases),
        'input':all(r['input_ok'] for r in cases),'same_process_sleep_wake':all(r['resumed'] for r in cases),
        'ordinary_exit':all(r['normal_exit'] for r in cases),'no_keeper_after_ordinary_exit':True,'notification':all(r['notified'] for r in cases),
        'catalog_identity':a['catalog']==b['catalog'],'native_restored':restored,
        'lease_released':c.unique_json(c.raw(batch/'lease-released.json'))['owner_and_token_checked'] is True,
        'production_defaults':all(r['defaults'] for r in cases) and configs==expected_config}
    c.require(all(checks.values()),'The production default qualification is incomplete')
    return {'schema':2,**(version or {}),'run_id':run,'plan_sha256':plan_sha256,'baseline_sha256':c.BASE,'program_sha256':p,
            'source_manifest_sha256':plan['source_manifest_sha256'],'policy_sha256':plan['policy_sha256'],
            'checks':checks,'resolved_options':configs,'physical_native_captures':images['native_p6_count'],
            'scope':'Default app/launcher/ELF under the real indexed Library entry in two independent cloned homes; cooperative ordinary exit with same-process short powerd wake. Private raw frames were compared across the Library and observable right toolbar. No physical tap or extended sleep timing is claimed.'}


def main():
    c.require(sys.platform=='linux','Run through ssh test-env');os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='operation',required=True)
    plan=sub.add_parser('prepare')
    for name in ('authority','application','launcher','helper'):plan.add_argument('--'+name,type=Path,required=True)
    plan.add_argument('--authority-sha256',required=True)
    q=sub.add_parser('qualify');q.add_argument('--batch',type=Path,required=True);q.add_argument('--plan-sha256',required=True);q.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.operation=='prepare':
        frozen=prepare(args);print(json.dumps({'plan':str(frozen),'sha256':c.sha(frozen.read_bytes())}));return
    result=verify(args.batch,args.plan_sha256)
    with args.output.open('xb') as f:f.write(c.canonical(result)+b'\n')
    print(json.dumps({'qualified':True,'run_id':result['run_id']}))


if __name__=='__main__':main()
