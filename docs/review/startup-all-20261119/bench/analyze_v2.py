#!/usr/bin/env python3
"""Frozen R2 defaults and review entry points; never contacts or acquires a Kindle."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import ModuleType


BASE=Path('/root/aidoku-all-bench-20261119')
APP='1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
LAUNCH='2b0d6e8ee76076c1f14e92dae97a505b40f0a78e07cec78cdea23ec59398bae5'
HELPER='bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50'
MEASURE='113991a8c5668fb050a0dbb368267a34868b0cf95c4992ba501fdb2c4ee1a323'
CREATOR='c931d66e11a0f35365d7c076b4af782b5f51524229cb01a56b48ac9e6654b055'
QUALIFIER='5a7aa1f10fec8f31cb7e30509f1e070bbde25dbbcf570610b949858fa68214b2'
PORTRAIT='95de33af55916d97eb57d37451135a5be9c5cafc495919be3f381c1d8da6fcf7'
R2FILES={'candidate-arm':APP,'launch.sh':LAUNCH,'aidoku-startup-portrait':HELPER}


def load(name,path,digest):
    raw=path.read_bytes()
    if sha256(raw).hexdigest()!=digest:raise ValueError('Frozen qualification source differs')
    mod=ModuleType('_all_startup_r2_'+name);mod.__file__=str(path)
    exec(compile(raw,str(path),'exec'),mod.__dict__)
    return mod


def base_measure():
    p=BASE/'all-options/frozen-analyze-all.py'
    base=load('measure',p,MEASURE)
    base.DEFAULTS={**base.DEFAULTS,'import_thumbnail':'off'}
    return base


def all_options_modules():
    directory=BASE/'all-options/tools'
    creator=load('creator',directory/'materialize_all_options.py',CREATOR)
    creator.DEST=BASE/'all-options-v2';creator.RELEASE=Path('/root/aidoku-all-build-20261119/artifacts/final-v2')
    old_expected=creator.expected
    def expected(path):
        members=old_expected(path)
        if members!=R2FILES:raise ValueError('R2 qualification cannot use an R1 default plan')
        return members
    creator.expected=expected
    qualifier=load('qualifier',directory/'analyze_all_options.py',QUALIFIER)
    qualifier.DEPOT=creator.DEST
    def reference(name,paths,digest):
        if name=='creator' and digest==CREATOR:return creator
        if name=='measure' and digest==MEASURE:return base_measure()
        raise ValueError('Unrecognized R2 qualification dependency')
    qualifier.load=reference
    versions=creator.generate();qualifier.RUNNER=creator.digest(versions['run_bench.py']);qualifier.DEVICE=creator.digest(versions['bench_device.sh'])
    return creator,qualifier


def prepare_all(release_plan):
    creator,_=all_options_modules()
    creator.prepare(release_plan)


def analyze_all_options(path,release_plan,crop=None):
    _,qualifier=all_options_modules();result=qualifier.analyze(path,release_plan,crop)
    result.update(release_generation='R2',expected_candidate_application_sha256=APP,
                  analyzer_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),r1_qualification_algorithm_sha256=QUALIFIER)
    return result


def analyze(paths,qualification,trial,workload,phase,candidate_sha256=APP,crop=None):
    if candidate_sha256!=APP:raise ValueError('Only the separately frozen R2 program is admitted')
    base=base_measure()
    if trial=='supervisor':
        if workload!='warm':raise ValueError('Portrait is a warm isolated comparison')
        base.FROZEN['warm']=(PORTRAIT,base.FROZEN['warm'][1])
    result=base.analyze(paths,qualification,trial,workload,phase,APP,crop)
    for b in result['batches']:
        arms=b['artifacts'];candidate=arms['candidate']
        valid=(candidate['application_sha256']==APP and candidate['launcher_sha256']==LAUNCH and
               candidate['extra_sha256']=={'aidoku-startup-portrait':HELPER})
        if trial=='supervisor':
            valid=valid and all(arms[s]['startup_options']=={'portrait':choice} and arms[s]['env']=={} for s,choice in [('baseline','shell'),('candidate','native')])
        if not valid:b['issues'].append('r2_frozen_program_or_persistent_selection_mismatch');result['issues'].append('r2_program_or_file_policy_mismatch')
    if result['issues']:
        result['valid']=False;result['qualification_passed']=False;result['mechanism']['verified']=False
        result.update(latency_gate_passed=None,default_guard_passed=None,performance_conclusion='invalid_observations')
    result.update(release_generation='R2',analyzer_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  r1_measurement_algorithm_sha256=MEASURE,frozen_program_members=R2FILES)
    return result


def main():
    if sys.platform!='linux':raise SystemExit('Run exclusively on test-env')
    p=argparse.ArgumentParser(description=__doc__);cmd=p.add_subparsers(dest='operation',required=True)
    a=cmd.add_parser('prepare-all-options');a.add_argument('--release-plan',type=Path,required=True)
    a=cmd.add_parser('analyze-all-options');a.add_argument('--batch',type=Path,required=True);a.add_argument('--release-plan',type=Path,required=True)
    a.add_argument('--status-bar-pixels',type=int);a.add_argument('--output',type=Path,required=True)
    a=cmd.add_parser('analyze');a.add_argument('--batch',type=Path,required=True,action='append');a.add_argument('--qualification-batch',type=Path,action='append',default=[])
    a.add_argument('--trial',choices=('combined','layout','prewarm','grace','supervisor'),required=True);a.add_argument('--workload',choices=('warm','cold'),required=True)
    a.add_argument('--phase',choices=('pilot','screen','confirm','qualify'),required=True);a.add_argument('--candidate-sha256',default=APP)
    a.add_argument('--status-bar-pixels',type=int);a.add_argument('--output',type=Path,required=True)
    ns=p.parse_args()
    if ns.operation=='prepare-all-options':return prepare_all(ns.release_plan)
    if ns.operation=='analyze-all-options':result=analyze_all_options(ns.batch,ns.release_plan,ns.status_bar_pixels)
    else:result=analyze(ns.batch,ns.qualification_batch,ns.trial,ns.workload,ns.phase,ns.candidate_sha256,ns.status_bar_pixels)
    ns.output.parent.mkdir(exist_ok=True,parents=True);ns.output.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:result[k] for k in ('trial','valid','issues','input_failures','complete_pairs','all_options_functional_passed','latency_gate_passed','default_guard_passed','qualification_passed') if k in result}))
    return 0 if result['valid'] else 2


if __name__=='__main__':sys.exit(main())
