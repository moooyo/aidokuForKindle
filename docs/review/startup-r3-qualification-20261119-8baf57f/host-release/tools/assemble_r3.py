#!/usr/bin/env python3
"""Replay the frozen R3 campaign and assemble a private packaging selection."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prepare_all as gate

BENCH, HOST = gate.BENCH, gate.HOST
WITNESS = Path('/root/aidoku-all-grace-qualification-20261119/reports')
LINEAGE = HOST/'prepared/r3-launcher-only'
REOPEN_RUNS = {'screen': 'scree-79ddab80', 'immediate': 'immed-38959626',
               'ordinary': 'ordin-b68bd767', 'ttl': 'ttl-6bc7be87', 'wake': 'wake-a86a9c8e'}
IMPORT_RUNS = ['aidoku-all-v2-imp-a-99d4c3dd', 'aidoku-all-v2-imp-b-223e08a3']
LIFECYCLE_RUN = 'aidoku-all-life-b98ac9d9'
LIFECYCLE_SHA = 'b23e0e85ca825bacc3e13d0fc72ac8eafd946d88bbb103bd1170d1d0cece39ea'


def ref(path, limit=12*1024*1024):
    return {'uri': path.as_uri(), 'sha256': gate.sha(gate.raw(path,limit))}


def report(path):
    return gate.read_json(gate.raw(path,12*1024*1024))


def run(tool, args, output):
    subprocess.run([sys.executable,'-B','-I',str(tool),*map(str,args),'--output',str(output)],check=True)
    return ref(output)


def assemble(out, portrait_pixels):
    gate.require(out.is_absolute() and out.parent.resolve(strict=True)==out.parent,'Expected a new canonical output directory')
    out.mkdir(mode=0o700)
    timings=report(BENCH/'reports/root-v3-plans.json'); visuals=report(BENCH/'reports/root-v3-pixel-plans.json')
    inputs=report(LINEAGE/'inputs.json'); authority=report(LINEAGE/'default-authority.json')
    program=inputs['program_sha256']
    gate.r3_lineage.generation(authority)
    auditors={'bench':{'main_sha256':gate.R3_BENCH,'dependencies':{}},
              'defaults':{'main_sha256':gate.DEFAULT_AUDITOR,'dependencies':gate.DEFAULT_DEPS},
              'resident':{'main_sha256':gate.R3_SPECIALISTS,'dependencies':{'prepare_r3_specialists.py':gate.R3_SPECIALIST_CREATOR}},
              'import':{'main_sha256':'fe4039045cbb1cde54a3d18313e0eef31a6cfbddfa45158a4d62435b405311a6',
                        'dependencies':{'attest.py':'41cbed24497802875621cf5f6662b8eeda9eab10e8e1fb4f04ad41241498e001',
                                        'materialize_guard.py':'d615fc1ef1805532b3e1ea9fe9fad8cb47cf91e9b3943a9b5890e9544557f997',
                                        'inventory_records.py':'58db96c2bc66b3cb71482f3a956896ef5b7f76f3e44ec6cdae70f387a5325f56',
                                        'fixture.py':'01414496efd2b5fd0aa929b3bdaeb4fefafccfc9437aab67dea2e07f42856fb1'}}}

    def bench(name,workload,timing,pixels):
        tool=gate.bound_auditor('bench',auditors['bench'])
        private=gate.bench_private(name,workload,r3=True)
        gate.require(timing.parent==pixels.parent==private and timing!=pixels,'Wrong or reused timing and pixel inputs')
        ticket=run(tool,('analyze','--trial',gate.TRIAL[name],'--workload',workload,'--phase','screen',
                         '--batch',timing,'--qualification-batch',pixels),out/f'{name}-{workload}.json')
        return {'batches':[timing.name],'qualification_batches':[pixels.name],'report':ticket}

    def specialist(scope,path):
        tool=BENCH/'tools/analyze_r3_specialists.py'
        gate.pinned(tool,gate.R3_SPECIALISTS,512*1024)
        gate.pinned(tool.with_name('prepare_r3_specialists.py'),gate.R3_SPECIALIST_CREATOR,512*1024)
        ident=ref(path/'plan.json')['sha256']
        ticket=run(tool,(scope,'--batch',path,'--plan-sha256',ident),out/f'specialist-{scope}.json')
        return {'batch':path.name,'plan_sha256':ident,'report':ticket}

    guards={regime:bench('default',regime,Path(timings['v3-default-'+suffix]).parent,Path(visuals['def-'+suffix]).parent)
            for regime,suffix in (('warm','w'),('cold','c'))}
    trials={name:{'screen':bench(name,'warm',Path(timings['v3-'+name]).parent,Path(visuals[name]).parent)}
            for name in ('layout','prewarm','grace')}
    for name in ('prewarm','grace'):
        path=WITNESS/f'final-v2-{name}.json'; completed=report(path)
        trials[name]['functional']={k:completed[k] for k in ('run_id','completed_sha256')}|{'report':ref(path)}
    portrait=BENCH/'portrait-v3/private/aidoku-all-v3-portrait-0d7ea40e'
    trials['supervisor']={'screen':bench('supervisor','warm',portrait,portrait_pixels)}
    reopen={scope:specialist(scope,BENCH/'reopen-v3'/('wake/private' if scope=='wake' else 'private')/('aidoku-all-v3-ro-'+leaf))
            for scope,leaf in REOPEN_RUNS.items()}
    trials['resident']={'screen':reopen.pop('screen'),'qualification':reopen}
    import_screen=BENCH/'reports/v2-import-screen.json'
    trials['import']={'screen':{'batches':IMPORT_RUNS,'qualification_batches':[],'report':ref(import_screen)},
                      'qualification':{'import':specialist('import',BENCH/'import-v3/private/aidoku-all-v3-import-q-dbc9be17')}}
    life=run(gate.bound_auditor('defaults',auditors['defaults']),('qualify','--batch',HOST/'private'/LIFECYCLE_RUN,
             '--plan-sha256',LIFECYCLE_SHA),out/'default-lifecycle.json')
    defaults={'run_id':LIFECYCLE_RUN,'plan_sha256':LIFECYCLE_SHA,'report':life}
    selection={k:authority[k] for k in ('schema','baseline_sha256','source_manifest_sha256','features','policy_sha256',
                                        'release_generation','r3_lineage_sha256','r3_lineage')}
    selection.update(artifacts=inputs['artifacts'],build=inputs['receipt'],baseline_source_revision=gate.CONTROL_REVISION,
                     auditors=auditors,trials=trials,default_trials=guards,defaults=defaults)
    gate.require(authority['program_sha256']==program,'Selection differs from the measured lifecycle authority')
    path=out/'selection.json'
    gate.write(path,selection)
    return {'selection':str(path),'sha256':ref(path)['sha256'],'source_root':inputs['source_root']}


def main():
    gate.require(sys.platform=='linux' and not sys.flags.optimize,'Run through ssh test-env')
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--portrait-pixels',type=Path,required=True,help='Independent portrait qualification batch directory')
    args=parser.parse_args()
    print(json.dumps(assemble(args.output,args.portrait_pixels)))


if __name__=='__main__':
    main()
