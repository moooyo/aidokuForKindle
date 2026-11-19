#!/usr/bin/env python3
"""Prove the launcher-only R3 input lineage. This does not authorize or package a release."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import release_common as c

BUILD=Path('/root/aidoku-all-build-20261119')
QA=Path('/root/aidoku-all-bench-20261119/launcher-r3')
R2_SOURCE=BUILD/'sources/final-v2'
R2_RECEIPT=BUILD/'reports/final-v2/build-receipt.json'
R2_RECEIPT_SHA='8fe4362b2b0e3d12ac51047d22740cd9deae051b7a5431aae471ab6b8cf8b786'
R3_LABEL='final-v3-clean'
R3_RECEIPT_SHA='516d572640a5f04b3243180b42b5f792f80687664febdac182f2bc8dc6e083c8'
R3_BUILD_INPUTS='31ad50d188c4b80f5f5cc960729c87ee6d8c1310881cc0614631799e0a521904'
R2_APP='1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
R2_HELPER='bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50'
R2_LAUNCH='2b0d6e8ee76076c1f14e92dae97a505b40f0a78e07cec78cdea23ec59398bae5'
COMPILED_SOURCE='28c1bb314e12fe5689b401ca23cf932a91c138f5763c4c1ea18d454f41c990cf'
SCRIPT='packaging/kindle/launch.sh'
R2_PROGRAM={c.APP:R2_APP,c.LAUNCH:R2_LAUNCH,c.EXTRA:R2_HELPER}
# Do not run newly discovered local tools just because they have a familiar
# name. The host checks must be frozen before this conditional path can open.
HOST_AUDITOR_SHA='854158395338949384548f568feaf554f5525de4c8f1c698f09d2405673f0ebe'
HOST_DEPENDENCIES={
    'admission-fixture/trace_admission.py':'93b18d84cc38a1bc85f1aff4f03f84765d3d9d8480d0c674c57e7f675b0ea631',
    'catalog-fixture/check_catalog.py':'4616da3e728e89b3ed632d0522ddf56e10a78fd7b261b59fc8c2f66d4072a8f5',
    'lifecycle-fixture/lifecycle.py':'33755f67a8afc7efe27cb0f7f43d52d2e9d03ea92aede988d779b421a64a1100',
    'pair/launcher.patch':'2ec319f5992160ed58810c2b3beaf68644cc7c758320721af3072114cac0e306',
    'profile-fixture/run_profile_parity.sh':'e68cdb3f608c08ac8c942b58abefda2869643b4df4ce98b8039709e137b79ef9',
}
REVIEWED_LAUNCHER_SHA='412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675'
HOST_INPUT_SHA='78323cfbe8fd397d8506affa5ff0ae0844bce63a4a2ff9eac3d660f4a6c1e47f'
RECHECK=c.ROOT/'prepared/r3-launcher-only/host-recheck-nbumFF/checked.json'
RECHECK_SHA='1d7e33e9620ea3e6bf841ff05e1a25a40b75b0efdd54d9bd7765074f02765cb3'
HOST_CHECKS=('original_and_candidate_route_match','profile_failure_parity','worker_exit_and_notification_parity',
             'owned_native_restoration','no_new_shared_writes','fewer_hot_path_external_execs')
HISTORY=(
    (Path('/root/aidoku-next-bench-20261118/reports/cold-warm-guard.json'),
     'ee33c50e3d13d96e3974bec1830f9e2be428f2ba0ad46f23bbafbe4884945f32',c.RETIRED,None,(-67.421,39.777)),
    (Path('/root/aidoku-all-bench-20261119/reports/v2-default-w.json'),
     '69795633c85c8adfea8e62424f6b637dfc58c17b8e6abe8bf118680557997f02',R2_APP,R2_LAUNCH,(-63.393,-62.549)),
)


def reference(ref,limit=96*1024*1024):
    c.require(isinstance(ref,dict) and set(ref)=={'uri','sha256'},'Malformed release input reference')
    url=urlsplit(ref['uri']);c.require(url.scheme=='file' and not any((url.netloc,url.query,url.fragment)), 'Expected local test-env input')
    file=Path(unquote(url.path));return file,c.exact(file,ref['sha256'],limit)


def tree(root):
    c.require(root.resolve(strict=True)==root and root.is_dir(), 'Unbound source tree')
    mapping={};total=0
    for path in sorted(root.rglob('*')):
        st=path.lstat();relative=path.relative_to(root).as_posix()
        c.require(not stat.S_ISLNK(st.st_mode) and not ({'__pycache__','.git','target','dist','artifacts'} & set(path.relative_to(root).parts)),
                  'A source tree contains a link, generated data or repository metadata')
        if stat.S_ISDIR(st.st_mode):
            mapping[relative]=['directory',0,stat.S_IMODE(st.st_mode)];continue
        c.require(stat.S_ISREG(st.st_mode) and len(mapping)<16000 and st.st_size<=96*1024*1024,'Invalid source file')
        total+=st.st_size;c.require(total<=512*1024*1024,'Unexpected release source size')
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        mapping[relative]=[digest,st.st_size,stat.S_IMODE(st.st_mode)]
    c.require(mapping,'Empty release source');return mapping


def compare_trees(before,after,old_sha,new_sha):
    c.require(before.keys()==after.keys() and SCRIPT in before and
        [k for k in before if before[k]!=after[k]]==[SCRIPT], 'R3 must change exactly the launcher source file')
    c.require(before[SCRIPT][0]==old_sha and after[SCRIPT][0]==new_sha and before[SCRIPT][2]==after[SCRIPT][2]
              and old_sha!=new_sha,'Launcher identity or mode is unqualified')
    n=sum(v[0]!='directory' for v in before.values())
    return {'original_full_tree_sha256':c.sha(c.canonical(before)),'final_full_tree_sha256':c.sha(c.canonical(after)),
            'total_source_files':n,'unchanged_source_files':n-1,'unchanged_directories':len(before)-n,'changed_source_files':[SCRIPT]}


def apply_exact(old,new,diff):
    c.require(b'\x00' not in diff and b'\r' not in diff and len(diff)<=128*1024 and len(diff)>0,'Not a reviewed text patch')
    lines=diff.splitlines();heads=[l for l in lines if l.startswith((b'--- ',b'+++ '))]
    c.require(heads==[('--- a/'+SCRIPT).encode(),('+++ b/'+SCRIPT).encode()] and
              not any(l.startswith((b'GIT binary patch',b'rename ',b'copy ',b'old mode',b'new mode')) for l in lines),
              'Patch contains a different file or mode change')
    with tempfile.TemporaryDirectory(prefix='aidoku-spec-') as name:
        root=Path(name);target=root/SCRIPT;target.parent.mkdir(parents=True);target.write_bytes(old)
        applied=subprocess.run(['patch','--batch','--forward','--fuzz=0','-p1'],input=diff,cwd=root,capture_output=True,timeout=10)
        c.require(applied.returncode==0 and target.read_bytes()==new,'Patch does not produce the qualified launcher')
    subprocess.run(['sh','-n'],input=new,check=True,timeout=10)


def rejected_history():
    result=[]
    for path,digest,app,launcher,expected in HISTORY:
        data=c.unique_json(c.exact(path,digest,2*1024*1024));r=data['artifacts'];pairs=data['pairs']
        c.require(data['valid'] is True and data['complete_pairs']==len(pairs)==6 and r['candidate']['application_sha256']==app
                  and (launcher is None or r['candidate']['launcher_sha256']==launcher),'Historical warm control differs')
        import statistics
        # Match the original instrument's microsecond resolution before taking
        # even-sample medians, including its rounding at half a millisecond digit.
        actual=tuple(round(statistics.median(round(row['baseline_ms'][k]-row['candidate_ms'][k],3) for row in pairs),3)
                     for k in ('usable','covers'))
        c.require(actual==expected and min(actual)<-50,'Failed warm reference cannot be relabeled')
        result.append({'application_sha256':app,'launcher_sha256':r['candidate']['launcher_sha256'],'report_sha256':digest,
                'usable_paired_savings_ms':actual[0],'covers_paired_savings_ms':actual[1],'pairs':6,'warm_eligible':False})
    return result


def stable_host(report,launcher):
    c.require(report.get('schema')==1 and report.get('result')=='pass' and report.get('baseline_launcher_sha256')==R2_LAUNCH and report.get('candidate_launcher_sha256')==launcher and
        report.get('application_sha256')==R2_APP and report.get('helper_sha256')==R2_HELPER,'Launcher fixture tested another composition')
    flags=report.get('checks');c.require(isinstance(flags,dict) and all(flags.get(k) is True for k in HOST_CHECKS),
                                    'Route, profile, exits, native restoration or saved work is not established')
    c.require(report['inputs']=={'verifier_sha256':HOST_AUDITOR_SHA,'tools':HOST_DEPENDENCIES,'frozen_fixture_sha256':HOST_INPUT_SHA},'Different host fixture or dependency')
    stable=report.get('stable',{})
    sizes={'admission':9,'catalog':7,'guard_function_sha256':12,'lifecycle':8,'profiles':22,'selector':13}
    c.require(stable.keys()==sizes.keys() and all(len(stable[k])==n for k,n in sizes.items()),'Host comparison is missing a fixture case')
    ordinary=stable['admission']['library'];a,b=ordinary['baseline'],ordinary['candidate']
    c.require(a['status']==b['status'] and a['successful_write_targets']==b['successful_write_targets'] and
              a['execs']>b['execs'] and a['forks']>b['forks'],'Ordinary Library admission did not preserve behavior and reduce process creation')
    return {'checks':{k:flags[k] for k in HOST_CHECKS},'stable':stable}


def checked_replay(controls,launcher):
    # Validate the independent run and its archived observations again without
    # turning later lifecycle or rollback commands into a fresh host exercise.
    replay=c.unique_json(c.exact(RECHECK,RECHECK_SHA,2*1024*1024))
    c.require(stable_host(replay,launcher)==controls,'Frozen host launcher behavior did not reproduce')
    root=RECHECK.parent/'raw';refs=replay['raw_receipt_sha256']
    paths={'admission':root/'admission-fixture/results.json',
           **{side+'_'+name:root/(side+'-'+name+'.json') for side in ('baseline','candidate') for name in ('lifecycle','catalog')}}
    profiles=list((root/'profile-fixture/runs').glob('*/report.json'))
    c.require(len(profiles)==1 and set(refs)==set(paths)|{'profile','trace_list_sha256'},'Incomplete raw host recheck')
    paths['profile']=profiles[0]
    for key,path in paths.items():c.exact(path,refs[key],4*1024*1024)
    admission=c.unique_json(c.exact(paths['admission'],refs['admission'],4*1024*1024))
    trace_hashes=[]
    for kind in ('admission','selector'):
        for case,row in admission[kind].items():
            c.require(re.fullmatch('[a-z0-9-]+',case),'Invalid host trace name')
            for side,value in row.items():
                c.require(side in ('baseline','candidate'),'Invalid host trace arm')
                leaf=f'{side}-'+('selector-' if kind=='selector' else '')+case+'.strace'
                trace=root/'admission-fixture/raw'/leaf
                c.exact(trace,value['trace_sha256'],4*1024*1024)
                trace_hashes.append(value['trace_sha256'])
    c.require(c.sha(json.dumps(sorted(trace_hashes)).encode())==refs['trace_list_sha256'],'Unbound admission trace list')


def verify_inputs(selection):
    c.require(sys.platform=='linux' and not sys.flags.optimize and selection['schema']==1 and selection['generation']=='r3-launcher-only',
              'Unqualified launcher-only lineage')
    c.require(c.valid_sha(HOST_AUDITOR_SHA) and c.valid_sha(REVIEWED_LAUNCHER_SHA),'R3 launcher and host auditor have not been accepted/frozen')
    root=Path(selection['source_root']);c.require(root==BUILD/'sources'/R3_LABEL,'R3 requires the separately accepted clean source freeze')
    new_sha=REVIEWED_LAUNCHER_SHA;program=R2_PROGRAM|{c.LAUNCH:new_sha}
    c.require(new_sha not in {R2_LAUNCH,c.LAUNCH_SHA} and selection['program_sha256']==program,'R3 application or helper differs from R2, or launcher is not new')
    prior=c.unique_json(c.exact(R2_RECEIPT,R2_RECEIPT_SHA,1024*1024));p,data=reference(selection['receipt'],1024*1024);receipt=c.unique_json(data)
    c.require(p==BUILD/'reports'/R3_LABEL/'build-receipt.json' and c.sha(data)==R3_RECEIPT_SHA,'Not the accepted clean reuse receipt')
    c.require(prior['program_sha256']==R2_PROGRAM and receipt['program_sha256']==program and
        prior['source_manifest_sha256']==receipt['source_manifest_sha256']==COMPILED_SOURCE and
        prior['baseline_source_revision']==receipt['baseline_source_revision']== 'c6867d174792cbcc71dab7f59c52570f8ddd8911'
        and {k:v for k,v in prior['packaging_sha256'].items() if k!=SCRIPT}=={k:v for k,v in receipt['packaging_sha256'].items() if k!=SCRIPT}
        and prior['packaging_sha256'][SCRIPT]==R2_LAUNCH and receipt['packaging_sha256'][SCRIPT]==new_sha,
        'Producer receipts disagree on the control, unchanged build or package sources')
    c.require(set(selection['artifacts'])==set(program),'Missing release files')
    artifacts={k:reference(v)[0] for k,v in selection['artifacts'].items()}
    c.require({k:v['sha256'] for k,v in selection['artifacts'].items()}==program,'Staged products differ from the qualified composition')
    for path,member in ((BUILD/'artifacts/final-v2/candidate-arm',c.APP),(BUILD/'artifacts/final-v2/aidoku-startup-portrait',c.EXTRA)):
        c.require(c.raw(path)==c.raw(artifacts[member]),'A fixed R2 binary changed in R3')
    old=c.exact(R2_SOURCE/SCRIPT,R2_LAUNCH); new=c.exact(root/SCRIPT,new_sha)
    c.require(new==c.raw(artifacts[c.LAUNCH]),'Release launcher differs from the frozen source')
    original,final=tree(R2_SOURCE),tree(root)
    counts=compare_trees(original,final,R2_LAUNCH,new_sha)
    recorded=c.unique_json(c.raw(p.parent/'build-inputs.json',4*1024*1024))
    c.require(recorded['file_count']==counts['total_source_files']==483 and recorded['sha256']==R3_BUILD_INPUTS
              and c.sha(c.canonical(recorded['files']))==R3_BUILD_INPUTS
              and {f['path']:(f['sha256'],f['bytes']) for f in recorded['files']}==
                  {name:(v[0],v[1]) for name,v in final.items() if v[0]!='directory'},
              'Full source record differs from the accepted producer archive')
    verifier=c.module('r3_canonical_source',c.exact(root/'scripts/verify-kindle-package.py',receipt['packaging_sha256']['scripts/verify-kindle-package.py']),root/'scripts/verify-kindle-package.py')
    m=verifier.source_manifest(root);c.require(m['sha256']==COMPILED_SOURCE and m==verifier.source_manifest(R2_SOURCE),'A compilation input changed')
    _,patch=reference(selection['patch'],128*1024);c.require(c.sha(patch)==HOST_DEPENDENCIES['pair/launcher.patch'],'Unreviewed source change');apply_exact(old,new,patch)
    c.exact(QA/'check_host.py',HOST_AUDITOR_SHA,512*1024)
    for rel,want in HOST_DEPENDENCIES.items():c.exact(QA/rel,want,4*1024*1024)
    _,host_report_bytes=reference(selection['host_report'],2*1024*1024); controls=stable_host(c.unique_json(host_report_bytes),new_sha)
    checked_replay(controls,new_sha)
    c.require(original==tree(R2_SOURCE) and final==tree(root),'Frozen source changed during lineage verification')
    return {'schema':1,'generation':'r3-launcher-only','installed_application_sha256':c.BASE,
            'failed_r2_program_sha256':R2_PROGRAM,'program_sha256':program,'compiled_source_manifest_sha256':COMPILED_SOURCE,
            'full_tree_comparison':counts,'launcher_patch_sha256':c.sha(patch),'host_auditor_sha256':HOST_AUDITOR_SHA,
            'host_report_sha256':selection['host_report']['sha256'],'host_recheck_sha256':RECHECK_SHA,'host_checks':controls['checks'],
            'stable_host_observations_sha256':c.sha(c.canonical(controls['stable'])),'historical_warm_failures':rejected_history(),
            'producer_receipt_sha256':selection['receipt']['sha256'],'new_program_bytes':['aidoku/launch.sh'],
            'reused_exact_binaries':[c.APP,c.EXTRA],'ready_for_independent_measurement':True,'promoted':False,'performance_claim':False}


def verify(ticket):
    c.require(set(ticket)=={'inputs','report'},'Incomplete launcher-lineage ticket')
    _,inputs=reference(ticket['inputs'],256*1024);data=verify_inputs(c.unique_json(inputs))
    _,result=reference(ticket['report'],512*1024);c.require(c.unique_json(result)==data, 'Launcher lineage is not reproducible')
    return data


def generation(record, *, prior_observation=False):
    """The rejected R2 tuple is evidence, never today's release target."""
    program=record.get('program_sha256',{})
    gen=record.get('release_generation');ticket=record.get('r3_lineage')
    if gen!='r3-launcher-only':
        c.require(ticket is None and not record.get('r3_lineage_sha256'), 'Unlabeled launcher-only release evidence')
        if program.get(c.APP)==R2_APP:
            c.require(prior_observation and program==R2_PROGRAM,
                      'The unchanged R2 tuple is rejected; reuse of its ELF with a changed launcher requires independent R3 lineage')
        return None
    c.require(program.get(c.APP)==R2_APP and program.get(c.EXTRA)==R2_HELPER and
        program.get(c.LAUNCH) not in (R2_LAUNCH,c.LAUNCH_SHA) and set(program)==set(R2_PROGRAM),
        'R3 must reuse both R2 binaries and change only its launcher')
    c.require(record.get('source_manifest_sha256')==COMPILED_SOURCE and record.get('baseline_sha256')==c.BASE
              and isinstance(ticket,dict) and record.get('r3_lineage_sha256')==ticket.get('report',{}).get('sha256'),
              'R3 has no exact lineage observation or installed baseline')
    features={name:('default_on' if name=='layout' else 'default_off_optional') for name in c.FEATURES}
    c.require(record.get('features')==features and record.get('policy_sha256')==c.validate_policy(features),
              'A launcher-only candidate cannot change the measured R2 factory policy')
    checked=verify(ticket)
    c.require(checked.get('program_sha256')==program and checked.get('generation')==gen and
              checked.get('promoted') is False and checked.get('performance_claim') is False,
              'The lineage report describes another program or claims device performance')
    return {'release_generation':gen,'r3_lineage_sha256':record['r3_lineage_sha256']}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',type=Path,required=True);p.add_argument('--inputs-sha256',required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();os.umask(0o077)
    result=verify_inputs(c.unique_json(c.exact(args.inputs,args.inputs_sha256,256*1024)))
    with args.output.open('xb') as f:f.write(c.canonical(result)+b'\n')
    print(json.dumps({'qualified_inputs':True,'performance_claim':False,'program_sha256':result['program_sha256']}))


if __name__=='__main__':main()
