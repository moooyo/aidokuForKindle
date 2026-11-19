"""Reproduce final-source controlled option behavior. Never calculate or certify latency."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode=True
ROOT=Path('/root/aidoku-all-grace-qualification-20261119')
TESTED={'run_fixture.py':'ad8f1562e4cc88e3bde97f404b403c3dad543fcbb4fe8a77d821a5ff272f6a95',
        'qualification.rs':'9cd216d36900919b8cd635233641180e64b21976aede03069efc13d8f9db1966',
        'build_fixture.sh':'8951786f0c93873a97a518f854bf235fdd7170e504be12c8fc5c8bcefa1a80a7',
        'controlled_prewarm.py':'5b9ca48b3eb3caf6236f9128e67aa411775160714fcbc53a4f53337f556a1487',
        'prewarm-adapter.rs':'0bfc8468a62667d174dd242a3aa0b3ff8487e1b2107b6dfe974846665d50a8c1'}
NAMES={'grace':{'early','expiry','cancel','disabled','foreign','rounded-wake'},
       'prewarm':{'disabled','hit-locked','changed-before','changed-after','missing-before','missing-after','cancelled','decoder-busy'}}
PROGRAM={'aidoku/app/aidoku','aidoku/launch.sh','aidoku/app/aidoku-startup-portrait'}


def require(ok,msg):
    if not ok: raise ValueError(msg)


def bound(path,digest,limit):
    require(isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest) and path.is_absolute()
            and path.resolve(strict=True)==path and path.is_file() and path.stat().st_size<=limit,'Unbound functional qualification input')
    data=path.read_bytes();require(hashlib.sha256(data).hexdigest()==digest,'Functional qualification input changed');return data


def validate(reported,replayed,feature,program,source_sha,run,completion):
    require(reported==replayed and reported.get('schema')==1 and reported.get('feature')==feature and feature in NAMES,
            'Source-bound controlled behavior did not reproduce')
    require(set(program)==PROGRAM and all(re.fullmatch('[a-f0-9]{64}',v) for v in program.values()) and reported['program_sha256']==program
            and re.fullmatch('[a-f0-9]{64}',source_sha) and reported['source_manifest_sha256']==source_sha, 'Trial uses foreign production sources or programs')
    require(reported['run_id']==run and reported['completed_sha256']==completion and reported['platform']=='test-env-linux-host'
            and reported['performance_claim'] is False and reported['latency_gate_passed'] is None and reported.get('functional_positive') is True
            and reported.get('functional_negative') is True and reported['auditor_sha256']==TESTED['run_fixture.py'], 'Wrong trial identity, platform or claim')
    o=reported['observations'];require(set(o)==NAMES[feature], 'Missing a defined positive or negative control')
    if feature=='grace':
        require(o['early']['events']==['started','completed'] and o['early']['publication_once_after_last_result'] is True and
                o['early']['wake_callbacks']==0 and o['expiry']['events']==['started','expired'] and o['expiry']['wake_callbacks']==1 and
                o['rounded-wake']['rounded_delivery_us']==39100 and o['rounded-wake']['events']==['started','expired'] and
                o['rounded-wake']['deferred_after_spent_timer'] is False and o['rounded-wake']['wake_callbacks']==1 and
                o['cancel']['events']==['started','cancelled'] and o['cancel']['last_still_pending'] is True and
                all(o[k]['events']==[] and o[k]['armed'] is False and o[k]['wake_callbacks']==0 for k in ('disabled','foreign')),
                'Completion, cutoff, cancellation, disabled selection or job ownership was not demonstrated')
    else:
        require(all(r['library_reader_blocked'] is True and r['cache_index_readable'] is True and r['generated_during_prewarm']==0
                    and r['ordinary_matches_current_source_pixels'] is True for r in o.values()) and
                o['disabled']['selected'] is False and o['disabled']['ordinary_memory_hits']==0 and
                o['hit-locked']['selected'] is True and o['hit-locked']['ordinary_memory_hits']==1 and o['hit-locked']['warm_hits']==1 and
                o['hit-locked']['synthetic_final_rgb_sha256']==o['disabled']['synthetic_final_rgb_sha256'] and
                all(o[k]['ordinary_memory_hits']==0 and o[k]['ordinary_commits']==1 for k in ('changed-before','changed-after','missing-before','missing-after')) and
                all(o[k]['source_replaced_without_metadata_change'] is True for k in ('changed-before','changed-after')) and
                all(o[k]['warm_hits']==o[k]['ordinary_memory_hits']==0 for k in ('cancelled','decoder-busy')),
                'Validated prefetch consumption, current-byte proof, missing artifacts, cancellation or busy admission is unproved')
    return reported


def verify(ticket,name,program,source_sha):
    require(sys.platform=='linux' and not sys.flags.optimize and name in NAMES and
            isinstance(ticket,dict) and set(ticket)=={'run_id','completed_sha256','report'}, 'Invalid controlled-function ticket')
    run=ticket['run_id'];require(isinstance(run,str) and re.fullmatch('grace-[a-f0-9]{12}',run), 'A preview cannot qualify a release')
    batch=ROOT/'private'/run;require(batch.resolve(strict=True)==batch and batch.is_dir(),'Missing controlled batch')
    authority=json.loads(bound(batch/'completed.json',ticket['completed_sha256'],512*1024))
    require(authority.get('preview_only') is False and authority.get('combined_prewarm') is True
            and authority.get('run_id')==run and authority.get('program_sha256')==program and authority.get('source_manifest_sha256')==source_sha,
            'Completion receipt is not the final integrated controlled build')
    refs=ticket['report'];require(isinstance(refs,dict) and set(refs)=={'uri','sha256'},'Malformed structural result reference')
    uri=urlsplit(refs['uri']);require(uri.scheme=='file' and not uri.netloc and not uri.query and not uri.fragment,'Expected a private host report')
    path=Path(unquote(uri.path));require(path.parent==ROOT/'reports','Wrong controlled report directory')
    reported=json.loads(bound(path,refs['sha256'],256*1024))
    for leaf,value in TESTED.items():bound(ROOT/'tools'/leaf,value,256*1024)
    with tempfile.TemporaryDirectory(prefix='aidoku-option-replay-') as name_root:
        out=Path(name_root)/'checked.json'
        subprocess.run([sys.executable,'-B','-I',str(ROOT/'tools/run_fixture.py'),'verify','--feature',name,'--batch',str(batch),
                        '--completed-sha256',ticket['completed_sha256'],'--output',str(out)],check=True,stdout=subprocess.DEVNULL,timeout=240)
        replayed=json.loads(out.read_bytes())
    return validate(reported,replayed,name,program,source_sha,run,ticket['completed_sha256'])
