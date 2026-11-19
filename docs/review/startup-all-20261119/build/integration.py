"""Freeze, compile and record the six integrated options on test-env, without a device lease."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path('/root/aidoku-all-build-20261119')
BASE = Path('/root/aidoku-next-status-20261118/source')
PREVIOUS = '1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768'
REVISION = 'c6867d174792cbcc71dab7f59c52570f8ddd8911'
DEPOT_HEAD = '8539f3ad347c9221388420a68931b30c8150e648'
PACK = ('scripts/verify-kindle-package.py', 'scripts/package-kindle.py', 'scripts/package-kpm.py',
        'scripts/build-kindle.sh', 'scripts/build-startup-portrait.sh', 'packaging/kindle/launch.sh')
TOP_DIRS = {'src', 'ui', 'assets', 'vendor', 'examples', 'tests', 'packaging', 'scripts', '.cargo'}
TOP_FILES = {'Cargo.toml', 'Cargo.lock', 'build.rs', 'rust-toolchain.toml', 'AGENTS.md',
             'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', '.gitignore', '.gitattributes'}
TARGET = 'armv7-unknown-linux-musleabihf'
MEMBERS = {'aidoku/app/aidoku': 'candidate-arm', 'aidoku/launch.sh': 'launch.sh',
           'aidoku/app/aidoku-startup-portrait': 'aidoku-startup-portrait'}


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def record(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')


def reference(path):
    return {'uri': path.resolve(strict=True).as_uri(), 'sha256': sha(path)}


def locations(label):
    need(re.fullmatch(r'[a-z][a-z0-9-]{0,40}', label), 'Invalid build label')
    return ROOT/'sources'/label, ROOT/'reports'/label, ROOT/'artifacts'/label


def all_inputs(source):
    paths = sorted((p for p in source.rglob('*') if p.is_file()), key=lambda p: p.relative_to(source).as_posix())
    need(all(not p.is_symlink() for p in paths), 'Source tree contains a symlink')
    rows = [{'path':p.relative_to(source).as_posix(), 'bytes':p.stat().st_size, 'sha256':sha(p)} for p in paths]
    return {'algorithm':'sha256-of-canonical-sorted-relative-file-records-v1','sha256':canonical(rows),
            'file_count':len(rows),'files':rows}


def product(source):
    module = runpy.run_path(str(source/'scripts/verify-kindle-package.py'))
    return {**module['source_manifest'](source), 'algorithm':module['SOURCE_ALGORITHM']}


def set_permissions(source):
    for p in source.rglob('*'):
        p.chmod(0o555 if p.is_dir() or p.suffix == '.sh' else 0o444)
    source.chmod(0o555)


def unpack(source, archive):
    seen = set(); total = 0
    with tarfile.open(archive, 'r:*') as tar:
        for item in tar:
            path = PurePosixPath(item.name)
            if not path.parts and item.isdir():
                continue
            need(not path.is_absolute() and '..' not in path.parts and '\\' not in item.name, 'Unsafe archive path')
            need(path.parts and (path.parts[0] in TOP_DIRS or str(path) in TOP_FILES), 'Unexpected source root: ' + str(path))
            need(not ({'.git','__pycache__','target'} & set(path.parts)) and path.suffix not in {'.pyc','.pyo'},
                 'Build output included in source archive')
            need(str(path) not in seen and len(seen) < 40000, 'Duplicate or oversized archive'); seen.add(str(path))
            need(item.isdir() or item.isreg(), 'No links or special inputs are supported')
            destination = source/path
            if item.isdir():
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            need(0 <= item.size <= 150*1024**2, 'Source file too large'); total += item.size
            need(total <= 1024**3, 'Input archive exceeds one GiB')
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tar.extractfile(item) as inp, destination.open('xb') as out:
                shutil.copyfileobj(inp, out, length=1024**2)
    for name in (*PACK, 'Cargo.toml','Cargo.lock','build.rs','LICENSE','THIRD_PARTY_NOTICES.md',
                 'tests/sleep_screen_ui.rs','scripts/verify-startup-settings.py'):
        need((source/name).is_file(), 'Missing required build input: '+name)
    set_permissions(source)


def freeze(label, archive, expected_archive):
    source, reports, artifacts = locations(label)
    archive = archive.resolve(strict=True)
    need(archive.parent == ROOT/'incoming' and archive.is_file(), 'Archive must be in incoming/')
    need(not any(p.exists() or p.is_symlink() for p in (source, reports, artifacts)),
         'Never overwrite a prior freeze; choose a fresh label')
    original = sha(archive)
    need(expected_archive is None or expected_archive == original, 'Archive digest mismatch')
    for p in (source.parent,reports.parent,artifacts.parent): p.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'tmp',prefix='freeze-') as name:
        staging = Path(name)/'source'; staging.mkdir(); unpack(staging, archive)
        need(sha(archive) == original, 'Archive changed during extraction')
        full = all_inputs(staging); manifest = product(staging); baseline=product(BASE)
        need(baseline['sha256'] == PREVIOUS, 'Prior installed source reference no longer matches')
        old={r['path']:r['sha256'] for r in baseline['files']}; new={r['path']:r['sha256'] for r in manifest['files']}
        changes={k:{'previous':old.get(k),'current':new.get(k)} for k in sorted(old.keys() | new.keys()) if old.get(k)!=new.get(k)}
        reports.mkdir(mode=0o700); artifacts.mkdir(mode=0o700); staging.rename(source)
        record(reports/'build-inputs.json',full); record(reports/'source-manifest.json',manifest)
        result={'label':label,'source_root':str(source),'project_head_before_changes':DEPOT_HEAD,
                'baseline_source_revision':REVISION,'baseline_source_sha256':PREVIOUS,
                'source_manifest_sha256':manifest['sha256'],'source_file_count':manifest['file_count'],
                'all_build_and_test_inputs_sha256':full['sha256'],'source_archive_sha256':original,
                'changed_product_sources':changes, 'packaging_sha256':{k:sha(source/k) for k in PACK},
                'builder_sha256':{k:sha(ROOT/'tools'/k) for k in ('integration.py','run.sh')}}
        record(reports/'freeze.json',result)
    print(json.dumps({k:result[k] for k in ('label','source_root','source_manifest_sha256','source_file_count',
                     'all_build_and_test_inputs_sha256','source_archive_sha256')}), flush=True)


def verified(label):
    source, reports, artifacts = locations(label)
    frozen = json.loads((reports/'freeze.json').read_bytes())
    need(frozen['label'] == label and product(source)['sha256'] == frozen['source_manifest_sha256']
         and all_inputs(source)['sha256'] == frozen['all_build_and_test_inputs_sha256'], 'Frozen input tree changed')
    need({k:sha(source/k) for k in PACK} == frozen['packaging_sha256'], 'Frozen packaging inputs changed')
    need(all(sha(ROOT/'tools'/k)==s for k,s in frozen['builder_sha256'].items()), 'Frozen builder changed')
    return source, reports, artifacts, frozen


def command(args, stdout, stderr=None, env=None, timeout=None):
    with stdout.open('xb') as out:
        if stderr:
            with stderr.open('xb') as err:
                return subprocess.run(args, stdout=out, stderr=err, env=env, timeout=timeout, check=True)
        return subprocess.run(args, stdout=out, stderr=subprocess.STDOUT, env=env, timeout=timeout, check=True)


def cargo_result(report, name, kind, is_test, source):
    events = [json.loads(x) for x in report.read_text().splitlines()]
    need(events and any(e.get('reason')=='build-finished' and e.get('success') for e in events), 'Cargo did not finish')
    selected=[e for e in events if e.get('reason')=='compiler-artifact' and e.get('executable') and
              e['package_id'] == 'path+'+source.as_uri()+'#aidoku-kindle@0.1.3' and e['profile']['test'] is is_test
              and e['target']['name']==name and kind in e['target']['kind']]
    need(len(selected)==1 and not selected[0]['fresh'], 'Expected a fresh compiler executable for '+name)
    target=Path(selected[0]['executable']).resolve(strict=True)
    need(target.is_relative_to(ROOT/'target'), 'Compiler emitted output outside private mount');return target


def keep(executable, destination):
    need(not destination.exists(), 'Refusing to overwrite an artifact')
    shutil.copyfile(executable, destination); destination.chmod(0o755)
    need(sha(executable)==sha(destination), 'Artifact copy mismatch')
    return {'file': destination.name, 'bytes': destination.stat().st_size, 'sha256': sha(destination)}


def build(label, mode):
    source, reports, artifacts, f = verified(label); os.chdir(source)
    need(os.environ.get('CARGO_BUILD_JOBS')=='6' and Path(os.environ.get('CARGO_TARGET_DIR',''))==ROOT/'target'
         and os.path.ismount(ROOT/'target'), 'Use the private run.sh wrapper on test-env')
    out = reports/mode; out.mkdir(mode=0o700)
    state={'source_manifest_sha256': f['source_manifest_sha256'], 'input_sha256':f['all_build_and_test_inputs_sha256'],
           'mode':mode,'cargo_jobs':6,'release_lto':False,'release_codegen_units':16,'artifacts':{}}
    record(out/'started.json',state)
    try:
        cmd=['cargo','clean','--profile','release','--offline','-p','aidoku-kindle']
        if mode=='arm': cmd += ['--target',TARGET,'-p','slint-backend-kindle']
        command(cmd,out/'clean.log')
        common=['-j6','--release','--locked','--offline']; kindle=['--no-default-features','--features','kindle']
        report=out/'cargo.jsonl'; capture='--message-format=json-render-diagnostics'
        if mode in ('arm','desktop'):
            args=(['cargo','zigbuild',*common,*kindle,'--target',TARGET,'--bin','aidoku',capture] if mode=='arm'
                  else ['cargo','build',*common,'--bin','aidoku',capture])
            command(args,report,out/'build.log'); exe=cargo_result(report,'aidoku','bin',False,source)
            state['artifacts'][mode]=keep(exe,artifacts/('candidate-'+mode))
            if mode=='arm':
                portrait=out/'aidoku-startup-portrait'
                command(['sh','scripts/build-startup-portrait.sh',str(portrait)],out/'portrait-build.log')
                fields=runpy.run_path(str(source/PACK[0]))['elf_identity'](portrait.read_bytes(),static=False)
                need(fields.get('static') is False, 'Unexpected portrait helper linkage')
                state['artifacts']['portrait']=keep(portrait,artifacts/'aidoku-startup-portrait')
                state['artifacts']['launcher']=keep(source/'packaging/kindle/launch.sh',artifacts/'launch.sh')
        else:
            tests=sorted(p.stem for p in (source/'tests').glob('*.rs'))
            spec=['--lib','--bin','aidoku']; spec.extend(v for t in tests for v in ('--test',t))
            command(['cargo','test',*common,*kindle,*spec,'--no-run',capture],report,out/'build.log')
            host=artifacts/'host';host.mkdir(mode=0o700); failures=[]
            for name,kind in [('aidoku_kindle','lib'),('aidoku','bin'),*((t,'test') for t in tests)]:
                executable=cargo_result(report,name,kind,True,source);target=host/name
                state['artifacts'][name]=keep(executable,target)
                log=out/(name+'-tests.log'); env=dict(os.environ)
                if name=='sleep_screen_ui':
                    evidence=out/'sleep-ui'; evidence.mkdir();env['AIDOKU_SLEEP_SCREEN_EVIDENCE']=str(evidence)
                try: command([str(target),'--test-threads=6'],log,env=env,timeout=420)
                except (subprocess.CalledProcessError,subprocess.TimeoutExpired): failures.append(name)
                result=re.findall(r'^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored;',log.read_text(),re.M)
                state.setdefault('suites',{})[name]=dict(zip(('result','passed','failed','ignored'),result[-1])) if result else {'result':'incomplete'}
            need(not failures, 'Failed host suites: '+', '.join(failures))
        verified(label)
        state['passed']=True
        record(out/'result.json',state)
    except Exception as error:
        state.update(passed=False,error=str(error));record(out/'result.json',state);raise
    print(json.dumps(state), flush=True)


def settings_ui(label):
    source, reports, artifacts, frozen=verified(label)
    desktop=json.loads((reports/'desktop/result.json').read_bytes())
    binary=artifacts/'candidate-desktop'; need(desktop.get('passed') is True and
        desktop['source_manifest_sha256']==frozen['source_manifest_sha256'] and
        desktop['artifacts']['desktop']['sha256']==sha(binary), 'Unqualified desktop executable')
    output=reports/'settings-ui'; need(not output.exists(), 'Settings UI already attempted; retain the result')
    os.chdir(source)
    command([sys.executable,'-B','-I',str(source/'scripts/verify-startup-settings.py'),'--binary',str(binary),
             '--output-dir',str(output)],reports/'settings-ui.log',timeout=650)
    ui=json.loads((output/'startup-settings.json').read_bytes()); need(ui['binary_sha256']==sha(binary) and
       ui['cases'] and all(row['passed'] for row in ui['cases']) and
       all(any(row['name'].startswith(key+'-startup-') for row in ui['cases']) for key in ('pw5','scribe')),
       'Incomplete or invalid two-profile settings observations')
    verified(label)
    print(json.dumps({'binary_sha256':sha(binary),'cases':len(ui['cases']),'result':str(output/'startup-settings.json')}))


def receipt(label):
    source,reports,artifacts,frozen=verified(label)
    arm=json.loads((reports/'arm/result.json').read_bytes())
    members={key:sha(artifacts/name) for key,name in MEMBERS.items()}
    need(arm.get('passed') is True and arm['source_manifest_sha256']==frozen['source_manifest_sha256'] and
         arm['input_sha256']==frozen['all_build_and_test_inputs_sha256'] and
         [arm['artifacts'][key]['sha256'] for key in ('arm','launcher','portrait')]==list(members.values()) and
         members['aidoku/launch.sh']==sha(source/'packaging/kindle/launch.sh'), 'Program identities differ')
    files=[reports/'source-manifest.json', reports/'freeze.json', reports/'arm/result.json', reports/'arm/cargo.jsonl',
           reports/'arm/build.log', reports/'arm/aidoku-startup-portrait', *(artifacts/n for n in MEMBERS.values())]
    result={'baseline_source_revision':REVISION,'baseline_source_sha256':PREVIOUS,
            'source_manifest_sha256':frozen['source_manifest_sha256'],'program_sha256':members,
            'packaging_sha256':frozen['packaging_sha256'],'evidence':[reference(path) for path in files]}
    dest=reports/'build-receipt.json'
    if dest.exists(): need(json.loads(dest.read_bytes())==result,'Existing build receipt is different')
    else: record(dest,result)
    print(json.dumps({'receipt':str(dest),'receipt_sha256':sha(dest),'program_sha256':members,
                      'scope':'Build identity only; host, device and deployment qualification are separate.'}))


def main():
    need(sys.platform=='linux', 'Run through ssh test-env')
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('freeze');p.add_argument('label');p.add_argument('archive',type=Path);p.add_argument('--archive-sha256')
    p=commands.add_parser('build');p.add_argument('label');p.add_argument('mode',choices=('arm','host','desktop'))
    for mode in ('check','receipt','settings-ui'):
        p=commands.add_parser(mode);p.add_argument('label')
    a=parser.parse_args()
    if a.command=='freeze': freeze(a.label,a.archive,a.archive_sha256)
    elif a.command=='build': build(a.label,a.mode)
    elif a.command=='receipt': receipt(a.label)
    elif a.command=='settings-ui': settings_ui(a.label)
    else: print(verified(a.label)[3]['source_manifest_sha256'])


if __name__=='__main__':
    main()
