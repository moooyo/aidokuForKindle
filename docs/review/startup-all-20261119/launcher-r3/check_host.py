"""Independently replay the R3 launcher diff and isolated Linux fixtures. No device is used."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
R2=Path('/root/aidoku-all-build-20261119/artifacts/final-v2')
DEFAULT_FIXTURE=Path('/root/aidoku-all-build-20261119/sources/final-v2/packaging/tests/launcher_test.py')
OLD='2b0d6e8ee76076c1f14e92dae97a505b40f0a78e07cec78cdea23ec59398bae5'
NEW='412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675'
APP='1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
HELPER='bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50'
FIXTURE='78323cfbe8fd397d8506affa5ff0ae0844bce63a4a2ff9eac3d660f4a6c1e47f'
# The callers additionally pin this script before replaying another source receipt.
TOOLS = {
    'pair/launcher.patch':'2ec319f5992160ed58810c2b3beaf68644cc7c758320721af3072114cac0e306',
    'admission-fixture/trace_admission.py':'93b18d84cc38a1bc85f1aff4f03f84765d3d9d8480d0c674c57e7f675b0ea631',
    'lifecycle-fixture/lifecycle.py':'33755f67a8afc7efe27cb0f7f43d52d2e9d03ea92aede988d779b421a64a1100',
    'profile-fixture/run_profile_parity.sh':'e68cdb3f608c08ac8c942b58abefda2869643b4df4ce98b8039709e137b79ef9',
    'catalog-fixture/check_catalog.py':'4616da3e728e89b3ed632d0522ddf56e10a78fd7b261b59fc8c2f66d4072a8f5',
}
ROUTES={'library','kual','worker','relative','custom','first-library','fresh-worker','crlf-library','absent-profile-library'}
SELECTORS={'missing','empty','recommended','file-native','native-override','legacy-override','invalid-override','duplicate','nul','overlong','symlink-native','missing-helper','noncapture'}
FUNCTIONS=('catalog_helper_is_current','finish_catalog_notification','native_read_is_owned','native_read_group_is_owned','stop_native_read','start_legacy_native_read','finish_native_read','take_display','release_display','start_application','stop_application','cleanup')


def require(value,message):
    if not value: raise ValueError(message)


def sha(data): return hashlib.sha256(data).hexdigest()


def checked(path,digest):
    path=Path(path)
    require(path.is_file() and not path.is_symlink() and sha(path.read_bytes())==digest,f'Unexpected input: {path.name}')
    return path.resolve()


def checked_call(cmd,env,root):
    result=subprocess.run(list(map(str,cmd)),env=env,cwd=root,capture_output=True,text=True,timeout=160)
    require(result.returncode==0, f'{Path(str(cmd[0])).name} fixture failed: {(result.stderr+result.stdout)[-1200:]}')


def body(text,name):
    start=text.index('\n'+name+'() {\n')+1
    end=text.index('\n}\n',start)+2
    return text[start:end]


def stable_life(report):
    require(report['passed'] and len(report['cases'])==8, 'Lifecycle cases incomplete')
    result={}
    for row in report['cases']:
        key=f'{row["entry"]}:{row["configured_portrait"]}:{int(row["retire_library_waiter"])}'
        keys=('entry_alive_while_active','entry_exit_status','worker_exit_status','stock_restored','owned_descendants_reaped','lock_removed','passed')
        item={k:row[k] for k in keys}
        item['worker_is_session_leader']=row['worker_session']==row['worker_pid']
        item['independent_worker']=row['worker_pid']!=row['entry_pid']
        item['same_app_across_waiter_retirement_and_wake']=row.get('same_app_survived_waiter_retirement_and_power_cycle')
        item['native_helper_dispatches']=len(row['native_helper'])
        item['stock_stop_count']=row['operations'].count('stop:statusbar')
        item['stock_start_count']=row['operations'].count('start:statusbar')
        item['stock_redraw_count']=row['operations'].count('xrefresh:-display :0 -none')
        require(key not in result and row['passed'] and all(row[k] is True for k in ('stock_restored','owned_descendants_reaped','lock_removed')),'Invalid lifecycle evidence')
        result[key]=item
    return result


def replay(args,work):
    require(sys.platform=='linux' and not sys.flags.optimize,'The verifier requires Linux and enabled assertions')
    base=checked(args.baseline,OLD);candidate=checked(args.candidate,NEW)
    app=checked(args.application,APP);helper=checked(args.helper,HELPER); frozen=checked(args.frozen_fixture,FIXTURE)
    for source in (base,candidate,app,helper): require(os.access(source,os.X_OK),'Program is not executable')
    require(stat.S_IMODE(base.stat().st_mode)==stat.S_IMODE(candidate.stat().st_mode),'Launcher modes do not match')
    for filename,digest in TOOLS.items():
        tool=checked(HERE/filename,digest);dest=work/filename;dest.parent.mkdir(exist_ok=True,parents=True);shutil.copyfile(tool,dest)
    pair=work/'pair'; shutil.copy2(base,pair/'baseline.sh');shutil.copy2(candidate,pair/'candidate.sh')
    tree=work/'patch-replay'/'packaging'/'kindle';tree.mkdir(parents=True);mapped=tree/'launch.sh';shutil.copy2(base,mapped)
    env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LC_ALL':'C','HOME':str(work),'AIDOKU_R3_FROZEN_LAUNCHER_TEST':str(frozen),'AIDOKU_R3_LIFECYCLE':str(work/'lifecycle-fixture/lifecycle.py')}
    checked_call(['/usr/bin/patch','--batch','--forward','--fuzz=0','-p1','-i',pair/'launcher.patch'],env,tree.parent.parent)
    require(mapped.read_bytes()==candidate.read_bytes(),'Patch does not reproduce the candidate')
    digest_guard={name:sha(body(base.read_text(),name).encode()) for name in FUNCTIONS}
    require(all(body(base.read_text(),name)==body(candidate.read_text(),name) for name in FUNCTIONS),'Ownership, restoration, or notification code changed')
    python='/usr/bin/python3'; checked_call([python,'-B','-I',work/'admission-fixture/trace_admission.py'],env,work)
    admission_file=work/'admission-fixture/results.json'; admission=json.loads(admission_file.read_text())
    require(admission['source_sha256']=={'baseline':OLD,'candidate':NEW} and admission['result']=='pass' and set(admission['admission'])==ROUTES and set(admission['selector'])==SELECTORS,'Incomplete admission evidence')
    commands={}
    for side in ('baseline','candidate'):
        executable=pair/(side+'.sh')
        for suite,script in [('lifecycle','lifecycle-fixture/lifecycle.py'),('catalog','catalog-fixture/check_catalog.py')]:
            commands[f'{side}-{suite}']=[python,'-B','-I',work/script,executable,work/(side+'-'+suite+'.json')]
    commands['profile']=['/bin/bash',work/'profile-fixture/run_profile_parity.sh',pair/'baseline.sh',pair/'candidate.sh',work/'profile-fixture']
    # Each child uses its own temporary namespaces and mock stock processes.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(checked_call,cmd,env,work) for cmd in commands.values()]
        for future in futures: future.result()
    profiles=list((work/'profile-fixture'/'runs').glob('*/report.json'));require(len(profiles)==1,'Profile evidence missing')
    profile=json.loads(profiles[0].read_text());require(profile['status']=='pass' and not profile['missing_items'] and profile['mismatch_count']==0, 'Invalid profile evidence')
    profile_rows={}
    for row in profile['parity']:
        r=row['r2'];n=row['candidate'];require(all(row[k] for k in ('exit_code_match','logs_created_match','imports_created_match','run_mode_match','stderr_normalized_match')),'Profile mismatch')
        profile_rows[row['case']]={'exit_code':r['exit_code'],'stderr_normalized_sha256':r['stderr_normalized_sha256'],'mode':r['run_mode'],'first_directories_created':r['logs_created'] and r['imports_created']}
    require(len(profile_rows)==22 and profile['results_count']==len(profile_rows)*2,'Incomplete profile coverage')
    require(any(r['mode']=='regular_user' for r in profile_rows.values()),'Unreadable profile did not use an unprivileged user')
    life={};catalog={};raw={'admission':sha(admission_file.read_bytes()),'profile':sha(profiles[0].read_bytes())}
    for side,digest in (('baseline',OLD),('candidate',NEW)):
        l=work/(side+'-lifecycle.json');c=work/(side+'-catalog.json');lr=json.loads(l.read_text());cr=json.loads(c.read_text())
        require(lr['sha256']['launcher']==digest and cr['launcher_sha256']==digest,'Unbound lifecycle evidence')
        life[side]=stable_life(lr);catalog[side]=cr['cases']
        require(cr['passed'] and len(catalog[side])==7 and all(v is True for v in catalog[side].values()),'Incomplete catalog evidence')
        raw[side+'_lifecycle']=sha(l.read_bytes());raw[side+'_catalog']=sha(c.read_bytes())
    ar=admission['admission'];se=admission['selector']
    checks={
        'original_and_candidate_route_match':all(v['baseline']['status']==v['candidate']['status'] for v in ar.values()) and all(v['baseline']['mode']==v['candidate']['mode'] for v in se.values()) and life['baseline']==life['candidate'],
        'profile_failure_parity':all(x['first_directories_created'] for x in profile_rows.values()) and profile['mismatch_count']==0,
        'worker_exit_and_notification_parity':life['baseline']==life['candidate'] and catalog['baseline']==catalog['candidate'],
        'owned_native_restoration':all(v['stock_restored'] and v['owned_descendants_reaped'] and v['lock_removed'] for side in life.values() for v in side.values()),
        'no_new_shared_writes':all(set(v['candidate']['successful_write_targets'])<=set(v['baseline']['successful_write_targets']) for v in ar.values()),
        'fewer_hot_path_external_execs':all(ar[key]['baseline']['execs']-ar[key]['candidate']['execs']==8 for key in ('library','kual')) and ar['worker']['baseline']['execs']-ar['worker']['candidate']['execs']==4 and se['missing']['baseline']['forks']-se['missing']['candidate']['forks']==1,
    }
    require(all(checks.values()),'A deterministic host eligibility condition failed')
    observed=lambda rows:{k:{s:{field:value for field,value in r.items() if field!='trace_sha256'} for s,r in v.items()} for k,v in rows.items()}
    raw['trace_list_sha256']=sha(json.dumps(sorted(row[s]['trace_sha256'] for row in [*ar.values(),*se.values()] for s in ('baseline','candidate'))).encode())
    return {'schema':1,'result':'pass','baseline_launcher_sha256':OLD,'candidate_launcher_sha256':NEW,'application_sha256':APP,'helper_sha256':HELPER,'checks':checks,'scope':'Report replayed on Linux. Admission reached a preowned private lock; two complete lifecycle runs used isolated mock stock services. No measured number is a Kindle timing or authorization.','stable':{'guard_function_sha256':digest_guard,'admission':observed(ar),'selector':observed(se),'profiles':profile_rows,'lifecycle':life['candidate'],'catalog':catalog['candidate']},'raw_receipt_sha256':raw,'host_admission_timing':admission['untraced_timing'],'inputs':{'tools':TOOLS,'verifier_sha256':sha(Path(__file__).read_bytes()),'frozen_fixture_sha256':FIXTURE}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','candidate','output'): parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--application',type=Path,default=R2/'candidate-arm');parser.add_argument('--helper',type=Path,default=R2/'aidoku-startup-portrait');parser.add_argument('--frozen-fixture',type=Path,default=DEFAULT_FIXTURE)
    parser.add_argument('--keep-raw',type=Path,help='Optional new private directory for raw host-only traces')
    args=parser.parse_args();require(not args.output.exists() and not args.output.is_symlink() and args.output.parent.is_dir(),'Output must be a new regular file in an existing directory')
    if args.keep_raw:
        args.keep_raw.mkdir(mode=0o700,parents=False,exist_ok=False);result=replay(args,args.keep_raw.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='aidoku-r3-audit-') as scratch: result=replay(args,Path(scratch))
    with args.output.open('x') as out: json.dump(result,out,indent=2,sort_keys=True);out.write('\n')
    print('Pass: launcher patch and isolated host evidence replayed; no Kindle measurements.')


if __name__=='__main__':main()
