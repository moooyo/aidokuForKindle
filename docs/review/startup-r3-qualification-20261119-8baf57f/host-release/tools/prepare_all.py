#!/usr/bin/env python3
"""Freeze a source-bound, measured six-feature release. Offline, test-env only.

The immutable 2026-11-18 ZIP and KPM are the release control. To be eligible,
the new production program must contain all six options, present evidence for
each, and pass separate warm and cold non-regression checks with no overrides.
A guarded default or informational trial does not establish a speedup.
"""

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True


HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import r3_lineage

HOST = Path('/root/aidoku-all-release-20261119')
BENCH = Path('/root/aidoku-all-bench-20261119')
PREVIOUS = Path('/root/aidoku-next-promotion-20261118/status-release-inputs/package')
CONTROL_ZIP = 'bcc625b919936eef55eb496c16cce3738cd8c1dede12c5e82e182514c5b8ee29'
CONTROL_KPM = '1670ba5a1f166cd21bcfd5da9d2364b2d6d9b089e8bb222bf0bbf593df054af8'
CONTROL_SOURCE = '1c7909c4efefd88d80cdacab12f1e832fa0533f3b62ef094cd956f16ca75f768'
CONTROL_REVISION = 'c6867d174792cbcc71dab7f59c52570f8ddd8911'
CONTROL_APP = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
CONTROL_LAUNCH = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
CONTROL_QUERY = '84c2f27d8233d8695001aa86d880e29454fe188deb9884aca1c6694c82a5887d'
APP, LAUNCH, EXTRA = 'aidoku/app/aidoku', 'aidoku/launch.sh', 'aidoku/app/aidoku-startup-portrait'
QUERY, JPEG = 'aidoku/app/aidoku-native-query', 'aidoku/app/kindle-jpeg-worker'
PROGRAM = {APP, LAUNCH, EXTRA}
FEATURES = ('supervisor', 'prewarm', 'grace', 'layout', 'resident', 'import')
POLICIES = {'default_on', 'default_off_optional'}
FORMAT = 'sha256-of-canonical-sorted-relative-file-records-v1'
OLD_UTILITY_SHA = 'adcced81a18e701b8b3191481de271b6ed92f7d2bbbe1fc816e0f09a78cfcbff'
REGISTRY = ('/var/local/kindle-catalog-entry/v1-2db100d0bcb60a76571cd8de96bed7ea6c016e6ab73e388a885'
            'f692955cacd86/aidoku-library-dispatch')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def digest(value):
    require(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value), 'Invalid SHA-256')
    return value


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def raw(path, limit=96 * 1024 * 1024):
    require(path.is_absolute() and path.is_file() and path.resolve(strict=True) == path and path.stat().st_size <= limit,
            'Required input is absent, indirect or unbounded')
    return path.read_bytes()


def pinned(path, expected, limit=96 * 1024 * 1024):
    data = raw(path, limit)
    require(sha(data) == digest(expected), 'Bound input bytes differ')
    return data


def file_ref(ref):
    require(isinstance(ref, dict) and set(ref) == {'uri', 'sha256'}, 'Malformed input reference')
    uri = urlsplit(ref['uri'])
    require(uri.scheme == 'file' and not uri.netloc and not uri.query and not uri.fragment, 'Expected a local file URI')
    return Path(unquote(uri.path))


def bound(ref, limit=96 * 1024 * 1024):
    return pinned(file_ref(ref), ref['sha256'], limit)


def read_json(data):
    def unique(items):
        result = dict(items)
        require(len(result) == len(items), 'Duplicate JSON property')
        return result
    return json.loads(data, object_pairs_hook=unique)


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='ascii')


def module(name, path, expected):
    # Execute only a caller-selected built-in auditor path after validating the exact bytes.
    import types
    data = pinned(path, expected, 2 * 1024 * 1024)
    result = types.ModuleType(name); result.__file__ = str(path); sys.modules[name] = result
    exec(compile(data, str(path), 'exec'), result.__dict__)
    return result


def old_utilities():
    # Only archive reading, POSIX modes and KPM manifest validation are reused.
    # Its historical release defaults and its selection rules are never called.
    for path in (HERE.parents[1] / 'startup-next-20261118/promotion/prepare.py',
                 Path('/root/aidoku-next-promotion-20261118/tools/prepare.py')):
        if path.is_file():
            return module('_aidoku_archive_comparison', path, OLD_UTILITY_SHA)
    raise ValueError('Reviewed package comparison utility missing')


def check_policy(features):
    require(isinstance(features, dict) and set(features) == set(FEATURES) and
            all(v in POLICIES for v in features.values()), 'Incomplete six-feature default policy')
    return sha(canonical(features))


def verify_variant(variant, app, launcher, extra, options, env=None):
    require(isinstance(options, dict) and all(isinstance(k, str) and re.fullmatch('[a-z][a-z0-9_]*', k)
            and isinstance(v, str) and re.fullmatch('[A-Za-z0-9_-]+', v) for k,v in options.items()), 'Invalid runtime option')
    content = ''.join(f'{k}={v}\n' for k,v in sorted(options.items())).encode('ascii')
    base = {'application_sha256': digest(app), 'launcher_sha256': digest(launcher),
            'native_query_sha256': CONTROL_QUERY, 'extra_sha256': extra,
            'startup_options': options, 'options_sha256': sha(content), 'args': [], 'env': env or {}}
    require(all(variant.get(k) == v for k,v in base.items()) and variant.get('variant_sha256') == sha(canonical(base)),
            'Measured program tuple, activation or digest differs')


def verify_program_control(variant):
    verify_variant(variant, CONTROL_APP, CONTROL_LAUNCH, {}, {})


def verify_program_final(variant, program, options):
    require(set(program) == PROGRAM and program[APP] != CONTROL_APP and program[LAUNCH] != CONTROL_LAUNCH,
            'Final combined runtime does not include all selected members')
    verify_variant(variant, program[APP], program[LAUNCH], {Path(EXTRA).name: digest(program[EXTRA])}, options)


def measure_pairs(report, phase, primary):
    require(phase in ('screen', 'confirm', 'default_guard') and primary in ('usable', 'covers'), 'Unknown gate')
    n = 10 if phase == 'confirm' else 6
    pairs = report['pairs']
    require(len(pairs) == n and [p.get('round') for p in pairs] == list(range(1, n + 1)) and
            Counter(p.get('order') for p in pairs) == {'AB': n // 2, 'BA': n // 2}, 'Incomplete predeclared paired observations')
    diffs = {'usable': [], 'covers': []}
    for p in pairs:
        require(p.get('complete') is True and isinstance(p.get('batch'), str) and p['batch'], 'Invalid or missing pair')
        a, b = p['baseline_ms'], p['candidate_ms']
        for r in (a, b):
            require(all(type(r.get(k)) in (int,float) and math.isfinite(r[k]) and r[k] >= 0
                        for k in ('first','input_ready','usable','covers','exit')) and r['covers'] >= r['first'] > 0 and
                    abs(max(r['first'],r['input_ready']) - r['usable']) <= .002, 'Invalid physical endpoint')
        for k in diffs:
            diffs[k].append(round(a[k] - b[k], 3))
    m = {k: statistics.median(v) for k,v in diffs.items()}
    other = 'usable' if primary == 'covers' else 'covers'
    wins = sum(d > 0 for d in diffs[primary])
    return {'n': n, 'wins': wins, 'primary_median_ms': m[primary], 'other_median_ms': m[other],
            'usable_median_ms': m['usable'], 'covers_median_ms': m['covers'],
            'regression_guard_passed': min(m.values()) >= -50,
            'benefit_passed': phase != 'default_guard' and wins >= n - 1 and m[primary] >= 100 and m[other] >= -50}


def archive_diff(baseline, new, changes, kpm=False):
    require(set(changes) == PROGRAM, 'Unexpected or missing program update')
    old_utilities().check_archive_delta(baseline, new, changes, kpm=kpm)


def verify_allowed_delta(baseline, new, changes, kpm=False):
    require(set(changes) == PROGRAM and {k: v[0] for k,v in changes.items()} ==
            {APP: CONTROL_APP, LAUNCH: CONTROL_LAUNCH, EXTRA: 'absent'}, 'Delta uses a different installed baseline')
    archive_diff(baseline, new, changes, kpm)


OPTIONS = {'supervisor': ('portrait', 'shell', 'native'), 'prewarm': ('prewarm', '0', '2'),
           'grace': ('last_cover_grace', 'off', 'on'), 'layout': ('first_layout', 'baseline', 'optimized'),
           'resident': ('resident', 'off', '45'), 'import': ('import_thumbnail', 'off', 'idle')}
TRIAL = {'supervisor': 'supervisor', 'prewarm': 'prewarm', 'grace': 'grace', 'layout': 'layout',
         'resident': 'reopen', 'import': 'import', 'default': 'combined'}
RETIRED = {'62de2771891b5bf8f720572c4544a3a732d619fa95131111cb00bccb6e0bc612',
           'd9be7683b614e97a84d7183d25649371bae5d2533f91edd2fede16b0fdc650f6',
           'c954eff22cc43bfc2cd27236d6fb0e9e5d8490d6322bd49763c0f4b7fb5acda2'}
AUDITORS = {'bench': BENCH/'tools/analyze_all.py', 'resident': BENCH/'reopen_probe/analyze_reopen.py',
            'import': BENCH/'import_probe_r2/tools/analyze_import.py', 'defaults': HERE/'defaults.py'}
R2_APP='1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
R2_BENCH='cd124c43ede8eb884c0d59da9f003c39d84fd771f9337488d840f89715d0a3ae'
R3_BENCH='00e747b04dbadb3d0681d754f6643a4a678da461dd5aaabcf0aa23219037ce4b'
R3_SPECIALISTS='02f54508f3d2d8721d9df036ca5a5479327f901770f8ea46e2e3ca5cf8bebfc1'
R3_SPECIALIST_CREATOR='d9681d90b4fa4153dbb3e7a6a26af737149fca3c9f3e1e9028e74c689a84aa5b'
DEFAULT_AUDITOR = 'fe1d1243c9a289ec3ded4150730fe43027f39dc008f22335a642e4594f44a341'
DEFAULT_DEPS = {'release_common.py': '44478d6a74ff570e32c31d7fd79c71be9ed08efa12bbac58ec8ed42bdfe97dcf',
                'capture_private.py': '7f51c4df408096691e05fa24356e2717585ac270883140fdbf60817c4e31b64c',
                'r3_lineage.py': '83a79a7f2ede1cb38aa68c2dcd05f820e0a5a55044ab66f3325c6e09a21c8e8b'}


def is_r3(program):
    return program==r3_lineage.R2_PROGRAM|{LAUNCH:r3_lineage.REVIEWED_LAUNCHER_SHA}


def bench_private(name, workload, r2=False, r3=False):
    folder='portrait-v3' if r3 and name=='supervisor' else 'portrait-v2' if r2 and name=='supervisor' else 'base-'+workload
    return BENCH/folder/'private'


def read_control():
    zip_path, kpm_path = PREVIOUS/'aidoku-startup-next.zip', PREVIOUS/'aidoku-startup-next.kpkg'
    pinned(zip_path, CONTROL_ZIP); pinned(kpm_path, CONTROL_KPM)
    util = old_utilities(); z, k = util.read_archive(zip_path, 'zip'), util.read_archive(kpm_path, 'kpm')
    require(EXTRA not in z and 'payload/'+EXTRA not in k and all(sha(z[p][0]) == h and sha(k['payload/'+p][0]) == h
            for p,h in ((APP,CONTROL_APP),(LAUNCH,CONTROL_LAUNCH),(QUERY,CONTROL_QUERY))), 'Deployed reference bytes differ')
    return z, k


def verify_build(source, receipt, artifacts, source_sha):
    data = read_json(bound(receipt, 2 * 1024 * 1024))
    require(data['baseline_source_revision'] == CONTROL_REVISION and data['baseline_source_sha256'] == CONTROL_SOURCE,
            'Wrong build lineage')
    require(data['source_manifest_sha256'] == source_sha and data['program_sha256'] == artifacts,
            'Build evidence is not for the measured composition')
    verifier_path = source/'scripts/verify-kindle-package.py'
    code = data['packaging_sha256']
    required = ('scripts/verify-kindle-package.py', 'scripts/package-kindle.py', 'scripts/package-kpm.py',
                'scripts/build-kindle.sh','scripts/build-startup-portrait.sh','packaging/kindle/launch.sh')
    require(set(code) == set(required), 'Build does not cover the release packaging inputs')
    for path in required:
        pinned(source/path, code[path])
    verify = module('_production_verifier_all', verifier_path, code['scripts/verify-kindle-package.py'])
    manifest = verify.source_manifest(source)
    require(verify.SOURCE_ALGORITHM == FORMAT and source_sha != CONTROL_SOURCE and
            manifest['sha256'] == source_sha and artifacts[LAUNCH] == code['packaging/kindle/launch.sh'], 'Source tree drifted')
    records = data['evidence']; require(isinstance(records,list) and 1 <= len(records) <= 24, 'Original producer evidence missing')
    observed = set(); input_proved = False
    for ref in records:
        proof = bound(ref)
        observed.update(k for k, h in artifacts.items() if h == ref['sha256'] or h.encode() in proof)
        if len(proof) <= 4 * 1024 * 1024:
            input_proved |= source_sha.encode() in proof or any(
                f['path'].startswith('src/') and f['path'].encode() in proof and f['sha256'].encode() in proof for f in manifest['files'])
    require(input_proved and observed == PROGRAM, 'Producer evidence is not bound to all release bytes and named inputs')
    return dict(manifest, algorithm=FORMAT), code


def make_archives(output, source, artifacts, program, manifest, tooling):
    control_zip, control_kpm = read_control()
    changes = {name: (sha(control_zip[name][0]) if name in control_zip else 'absent', program[name]) for name in PROGRAM}
    require(all(old != new for old,new in changes.values()), 'Integrated package has an unchanged selected member')
    zip_path, kpm_path = output / 'aidoku-startup-all.zip', output / 'aidoku-startup-all.kpkg'
    write(output/'source-manifest.json', manifest)
    with tempfile.TemporaryDirectory(prefix='staging-', dir=output) as temp:
        stage = Path(temp)
        for name in sorted(set(control_zip) | PROGRAM):
            path = stage/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(artifacts[name] if name in artifacts else control_zip[name][0])
        def execute(filename, *args):
            key = 'scripts/'+filename; pinned(source/key, tooling[key])
            subprocess.run([sys.executable,'-B','-I',str(source/key),*map(str,args)], cwd=source, check=True)
        execute('package-kindle.py','--staging',stage,'--output',zip_path,'--source-manifest',output/'source-manifest.json')
        execute('package-kpm.py','--source-root',source,'--application',stage/APP,'--native-query-helper',stage/QUERY,
                '--native-catalog-dir',stage/'aidoku/native-catalog','--startup-portrait-helper',stage/EXTRA,'--output',kpm_path)
        execute('verify-kindle-package.py','--archive',zip_path,'--manifest',zip_path.with_suffix('.manifest.json'),
                '--source-root',source,'--source-manifest',output/'source-manifest.json','--expected-source-sha256',manifest['sha256'],
                '--expected-application-sha256',program[APP],'--expected-helper-sha256',sha(control_zip[JPEG][0]),
                '--expected-native-query-sha256',CONTROL_QUERY,'--expected-catalog-dir',stage/'aidoku/native-catalog',
                '--expected-startup-portrait-sha256',program[EXTRA],'--output',output/'package-verification.json')
        util = old_utilities()
        for path, previous, kind in ((zip_path,control_zip,'zip'),(kpm_path,control_kpm,'kpm')):
            verify_allowed_delta(previous, util.read_archive(path,kind), changes, kpm=kind=='kpm')
    for name, data in artifacts.items():
        out=output/'delta'/name; out.parent.mkdir(parents=True, exist_ok=True); out.write_bytes(data); out.chmod(0o755)
    return {'archive_sha256': sha(raw(zip_path)), 'kpm_sha256': sha(raw(kpm_path)),
            'package_verification_sha256':sha(raw(output/'package-verification.json',4*1024*1024)),
            'changes': [{'path':name,'old':changes[name][0],'new':program[name],'file':'delta/'+name}
                        for name in (EXTRA, APP, LAUNCH)]}


def verify_isolate(variants, name, program):
    a,b = (variants[side] for side in ('baseline','candidate'))
    require(set(variants) == {'baseline','candidate'} and a['application_sha256'] == b['application_sha256'] == program[APP] and
            all(a[k] == b[k] for k in ('launcher_sha256','native_query_sha256','extra_sha256')),
            'Causal comparison is not the final application with the same launcher and helpers')
    launcher,extras = a['launcher_sha256'], a['extra_sha256']; final_extra = {Path(EXTRA).name: program[EXTRA]}
    require((launcher in {CONTROL_LAUNCH,program[LAUNCH]} and extras in ({}, final_extra)) and
            (name != 'supervisor' or (launcher == program[LAUNCH] and extras == final_extra)), 'Unrelated launcher or helper')
    key,disabled,enabled = OPTIONS[name]
    if name == 'supervisor' and a['env']:
        require(a['startup_options'] == b['startup_options'] == {}, 'Supervision trial mixed two configuration paths')
        verify_variant(a,program[APP],launcher,extras,{}, {'AIDOKU_STARTUP_PORTRAIT':'legacy'})
        verify_variant(b,program[APP],launcher,extras,{}, {'AIDOKU_STARTUP_PORTRAIT':'native'})
    else:
        # A missing baseline option is allowed only if its executed branch was
        # separately verified by the trial analyzer. Other controls must match.
        require(a['startup_options'].get(key,disabled) == disabled and b['startup_options'].get(key,enabled) == enabled
                and a['startup_options'] != b['startup_options'] and {k:v for k,v in a['startup_options'].items() if k != key} ==
                {k:v for k,v in b['startup_options'].items() if k != key},'Comparison is not an isolated option change')
        for v in (a,b): verify_variant(v,program[APP],launcher,extras,v['startup_options'])


def bound_auditor(name, spec):
    filepath = BENCH/'tools/analyze_v2.py' if name=='bench' and spec.get('main_sha256')==R2_BENCH else AUDITORS[name]
    if name=='bench' and spec.get('main_sha256')==R3_BENCH: filepath=BENCH/'tools/analyze_v3.py'
    require(set(spec) == {'main_sha256','dependencies'} and isinstance(spec['dependencies'], dict), 'Invalid auditor authority')
    if name=='defaults':
        require(spec['main_sha256']==DEFAULT_AUDITOR and spec['dependencies']==DEFAULT_DEPS,
                'Default lifecycle authority, capture or deployed-file guards differ from the frozen run')
    if name=='import':
        require(spec['main_sha256']=='fe4039045cbb1cde54a3d18313e0eef31a6cfbddfa45158a4d62435b405311a6' and
                set(spec['dependencies']) >= {'attest.py','materialize_guard.py','inventory_records.py','fixture.py'},
                'Only the independently guarded R2 import harness may contribute evidence')
    if name=='resident': require(spec['main_sha256']=='247944b89e4454bafea66fdbd3beb93b79c8a4cf2175e597e7d402a953764005',
                                 'Unqualified retained-data analyzer')
    pinned(filepath, spec['main_sha256'], 2 * 1024 * 1024)
    for leaf, expected in spec['dependencies'].items():
        require(re.fullmatch(r'[A-Za-z0-9_-]+\.(?:py|sh)',leaf), 'Unsafe auditor dependency')
        pinned(filepath.with_name(leaf),expected,2 * 1024 * 1024)
    return filepath


def checked_runs(private, names):
    require(isinstance(names,list) and len(names) <= 16 and len(set(names)) == len(names), 'Invalid or duplicate batch set')
    locations=[]
    for name in names:
        require(isinstance(name,str) and re.fullmatch(r'aidoku-all-[a-z0-9][a-z0-9-]{0,62}',name), 'Not a November 19 batch identity')
        directory=private/name
        require(directory.is_dir() and directory.resolve(strict=True)==directory, 'Invalid private batch directory')
        locations.append(directory)
    return locations


def replay(ticket, auditor, name, phase, workload, program):
    if name=='resident' and is_r3(program):
        require(phase=='screen' and set(ticket)=={'batch','plan_sha256','report'}
                and auditor['resident']=={'main_sha256':R3_SPECIALISTS,'dependencies':{'prepare_r3_specialists.py':R3_SPECIALIST_CREATOR}},
                'Expected the independent R3 resident screen and specialist replay tool')
        result,run=specialist('screen',ticket,program)
        return result,{run}
    kind = name if name in ('resident','import') else 'bench'
    path = bound_auditor(kind, auditor[kind])
    require(set(ticket)=={'report','batches','qualification_batches'}, 'Invalid comparison replay reference')
    data=read_json(bound(ticket['report'], 12 * 1024 * 1024))
    r3=kind=='bench' and is_r3(program)
    r2=kind=='bench' and program[APP]==R2_APP and not r3
    if kind=='bench':
        require((auditor[kind]['main_sha256']==R3_BENCH)==r3 and (auditor[kind]['main_sha256']==R2_BENCH)==r2,
                'A different benchmark generation cannot admit this application')
        private=bench_private(name,workload,r2,r3)
    elif kind=='import': private=BENCH/'import_probe_r2/private'
    else: private=BENCH/'reopen_probe/private'
    batches=checked_runs(private,ticket['batches']); visual=checked_runs(private,ticket['qualification_batches'])
    require(batches and not(set(batches) & set(visual)), 'Missing measurements or reused pixel run')
    cmd=[sys.executable,'-B','-I',str(path),*(['analyze'] if r2 or r3 else []),'--phase',phase]
    if kind=='bench':
        cmd+=['--trial',TRIAL[name],'--workload',workload]
        if not r3: cmd+=['--candidate-sha256',program[APP]]
    if kind=='resident': cmd+=['--candidate-sha256',program[APP]]
    for folder in batches: cmd+=['--batch',str(folder)]
    for folder in visual: cmd+=['--qualification-batch',str(folder)]
    with tempfile.TemporaryDirectory(prefix='aidoku-frozen-evidence-') as scratch:
        out=Path(scratch)/'evidence.json'
        subprocess.run([*cmd,'--output',str(out)],check=True,stdout=subprocess.DEVNULL)
        require(read_json(raw(out,12*1024*1024))==data,'Reported results do not reproduce on the original raw data')
    require(data.get('valid') is True and data.get('phase')==phase, 'Trial failed independent replay')
    if kind=='bench':
        require(data.get('trial')==TRIAL[name] and data.get('workload')==workload
                and data.get('expected_candidate_application_sha256')==program[APP] and data.get('baseline_reference_sha256')==CONTROL_APP
                and data.get('analyzer_sha256')==auditor[kind]['main_sha256'] and data.get('primary_fixture_verified') is True,
                'Wrong formal route, build, workload or application reference')
        if r3:
            require(data.get('release_generation')=='R3' and data.get('producer_receipt_sha256')==r3_lineage.R3_RECEIPT_SHA
                    and data.get('compiled_source_manifest_sha256')==r3_lineage.COMPILED_SOURCE
                    and data.get('native_query_sha256')==CONTROL_QUERY and data.get('frozen_payload_verified') is True
                    and data.get('frozen_program_members')=={'candidate-arm':program[APP],'launch.sh':program[LAUNCH],Path(EXTRA).name:program[EXTRA]},
                    'R3 replay did not use all frozen source and runtime inputs')
    run_set={d.name for d in [*batches,*visual]}
    recorded={b['run_id'] for b in data['batches']} if kind in ('bench','resident') else set(data['physical_batches'])
    require(run_set==recorded,'Missing or unexpected runs in public result')
    return data,run_set


def specialist(scope,ticket,program):
    require(set(ticket)=={'batch','plan_sha256','report'},'Invalid specialist replay reference')
    folder='import-v3' if scope=='import' else 'reopen-v3/wake' if scope=='wake' else 'reopen-v3'
    batch,=checked_runs(BENCH/folder/'private',[ticket['batch']]); plan_sha=digest(ticket['plan_sha256'])
    pinned(batch/'plan.json',plan_sha,128*1024)
    path=BENCH/'tools/analyze_r3_specialists.py'
    pinned(path,R3_SPECIALISTS,512*1024)
    pinned(path.with_name('prepare_r3_specialists.py'),R3_SPECIALIST_CREATOR,512*1024)
    result=read_json(bound(ticket['report'],12*1024*1024))
    with tempfile.TemporaryDirectory(prefix='aidoku-specialist-replay-') as scratch:
        out=Path(scratch)/'checked.json'
        subprocess.run([sys.executable,'-B','-I',str(path),scope,'--batch',str(batch),'--plan-sha256',plan_sha,
                        '--output',str(out)],check=True,stdout=subprocess.DEVNULL)
        require(result==read_json(raw(out,12*1024*1024)),'Specialist report differs from independently replayed evidence')
    require(result.get('valid') is True and result.get('scope_name')==scope and result.get('replay_sha256')==R3_SPECIALISTS
            and result.get('frozen_plan_sha256')==plan_sha and result.get('r3_launcher_sha256')==program[LAUNCH],
            'Unexpected specialist tool, plan or launcher')
    verify_isolate(result['artifacts'],'import' if scope=='import' else 'resident',program)
    require(all(v['launcher_sha256']==program[LAUNCH] and v['extra_sha256']=={Path(EXTRA).name:program[EXTRA]}
                for v in result['artifacts'].values()),'Specialist qualification did not exercise the full R3 tuple')
    runs={result['run_id']} if scope=='import' else {b['run_id'] for b in result['batches']}
    require(runs=={batch.name} and (scope=='screen' or result.get('qualified') is True),
            'Independent specialist qualification failed')
    return result,batch.name


def raw_defaults(features):
    check_policy(features)
    return {key: on if features[feature]=='default_on' else off for feature,(key,off,on) in OPTIONS.items()}


def accepted_defaults(ticket, auditors, program, source_sha, features):
    require(set(ticket)=={'run_id','plan_sha256','report'},'Invalid default lifecycle reference')
    path=bound_auditor('defaults',auditors['defaults'])
    directory,=checked_runs(HOST/'private',[ticket['run_id']]); plan_sha=digest(ticket['plan_sha256'])
    pinned(directory/'plan.json',plan_sha,6*1024*1024)
    result=read_json(bound(ticket['report'],128*1024))
    with tempfile.TemporaryDirectory(prefix='aidoku-lifecycle-replay-') as scratch:
        out=Path(scratch)/'checked.json'
        subprocess.run([sys.executable,'-B','-I',str(path),'qualify','--batch',str(directory),'--plan-sha256',plan_sha,
                        '--output',str(out)],check=True,stdout=subprocess.DEVNULL)
        require(result==read_json(raw(out,128*1024)),'Lifecycle evaluation does not reproduce')
    checks=('pixels','right_status_bar','input','same_process_sleep_wake','ordinary_exit','notification',
            'catalog_identity','native_restored','lease_released','production_defaults','no_keeper_after_ordinary_exit')
    require(result.get('schema')==2 and result.get('run_id')==ticket['run_id'] and result.get('plan_sha256')==plan_sha
            and result.get('source_manifest_sha256')==source_sha and result.get('baseline_sha256')==CONTROL_APP
            and result.get('program_sha256')==program and result.get('policy_sha256')==check_policy(features)
            and all(result.get('checks',{}).get(k) is True for k in checks)
            and result.get('resolved_options')==raw_defaults(features),'Production defaults, restoration, pixels or runtime identities differ')
    if is_r3(program):
        plan=read_json(raw(directory/'plan.json',128*1024))
        require(result.get('release_generation')==plan.get('release_generation')=='r3-launcher-only'
                and result.get('r3_lineage_sha256')==digest(plan['r3_lineage_sha256']), 'R3 lifecycle is not linked to its lineage')
    return result


def check_native_pictures(result):
    require(result.get('qualification_passed') is True and bool(result.get('pixels')), 'Missing independent real Library images')
    for item in result['pixels']:
        require((item.get('width'),item.get('height'),item.get('status_bar_excluded_pixels'))==(1860,2480,72)
                and item.get('right_status_bar_region')=={'x':930,'y':0,'width':930,'height':72}
                and all(item.get(k) is True for k in ('identical_below_status_bar','right_status_bar_observable','identical_right_status_bar')),
                'Independent content and right-side status pictures do not match')


def check_default_guard(result, program, features, workload):
    require(result['phase']=='screen' and result['trial']=='combined' and result.get('workload')==workload
            and result.get('default_guard_passed') is True and result.get('latency_gate_passed') is None
            and result.get('mechanism',{}).get('verified') is True,'Default non-regression check is not attributable')
    verify_program_control(result['artifacts']['baseline']);verify_program_final(result['artifacts']['candidate'],program,{})
    stats=measure_pairs(result,'default_guard','usable')
    require(stats['regression_guard_passed'], 'Final measured default regressed by more than 50 ms')
    check_native_pictures(result)
    values=raw_defaults(features)
    expected={'startup_config_'+k+'_'+v for k,v in values.items()}
    for b in result['batches']:
        directory=checked_runs(BENCH/('base-'+workload)/'private',[b['run_id']])[0]
        for sample in b['cases']:
            role,label=sample['case'].split('-',1)
            if role!='candidate' or label=='warmup': continue
            case=directory/'outputs/out'/sample['case']; trace=read_json(raw(case/'application.json',8*1024*1024))
            marks=Counter(x['name'] for x in trace['events'] if x['name'].startswith('startup_config_'))
            require(set(marks)==expected and all(x==1 for x in marks.values()),'The actual application resolved a different startup configuration')
            require(sample['diagnostics']['portrait_engine']==values['portrait'], 'Actual launcher policy differs')
    return stats


def classify(feature, policy, screening, confirmation, structural):
    # Functional defaults still require the final default warm/cold guards below.
    require(feature in FEATURES and policy in POLICIES and structural is True, 'Feature behavior was not qualified')
    if screening['benefit_passed'] is True and confirmation and confirmation['benefit_passed'] is True:
        return 'confirmed_benefit'
    if policy == 'default_off_optional': return 'available_default_off'
    require(feature in ('layout','import'), 'Default-on requires independently demonstrated benefit')
    return 'functional_default_no_speed_claim'


def physical_phase(result, name, phase):
    require(result['phase']==phase and result.get('valid') is True, 'Missing physical trial')
    return measure_pairs(result,phase,'usable' if name in ('layout','supervisor') else 'covers')


def import_phase(result, phase):
    rows = result['pairs']; n=10 if phase=='confirm' else 6
    require(len(rows)==n and [row['index'] for row in rows]==list(range(1,n+1)) and
            Counter(r['order'] for r in rows)=={'AB':n//2,'BA':n//2}, 'Incomplete import/reopen comparison')
    measures=('reopen_usable_ms','reopen_covers_ms','reopen_exit_ms','ingest_exit_ms','ingest_publish_ms',
              'ingest_request_to_visible_ms','two_visible_waits_ms','two_including_deferred_ms','worker_elapsed_ms',
              'ingest_cache_count','ingest_cached_bytes')
    deltas={key:[] for key in measures}; series={k:{'baseline':[],'candidate':[]} for k in measures}
    for row in rows:
        for key in measures:
            a,b=(row['ms_and_bytes'][s][key] for s in ('baseline','candidate'))
            require(all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in (a,b)), 'Invalid import cost')
            deltas[key].append(round(a-b,3));series[key]['baseline'].append(a);series[key]['candidate'].append(b)
    med={k:round(statistics.median(v),3) for k,v in deltas.items()}
    wins=sum(x>0 for x in deltas['reopen_covers_ms']); good=med['reopen_covers_ms']>=100 and wins>=n-1 and med['reopen_usable_ms']>=-50
    guard=(med['ingest_request_to_visible_ms']>=-50 and med['ingest_publish_ms']>=-50 and
           med['ingest_exit_ms']>=-100 and med['reopen_exit_ms']>=-100)
    net=guard and med['two_visible_waits_ms']>=100 and med['two_including_deferred_ms']>=0
    require(result.get('latency_gate_passed') is good,'Published import latency is inconsistent with observed pairs')
    certified=phase=='confirm' and result.get('valid') is True and result.get('qualification_passed') is True and good and net
    require(result.get('default_gate_passed') is certified,
            'The import-specific default decision differs from the independently reconstructed two-stage observations')
    require(result['mechanism']['verified'] is True, 'No measured source-bound import benefit path')
    require(all(v==0 for v in series['worker_elapsed_ms']['baseline']) and any(v>0 for v in series['worker_elapsed_ms']['candidate'])
            and med['ingest_cache_count']<0,'Import did not account for the additional deferred thumbnail work')
    return {'n':n,'wins':wins,'benefit_passed':good and net,'regression_guard_passed':guard,
            'reopen_benefit_passed':good,'two_stage_economic_gate':net,'confirmed_default_gate':certified,'median_paired_savings':med,
            'observed_cost_medians':{k:{s:round(statistics.median(t),3) for s,t in v.items()} for k,v in series.items()},
            'accounting':'Includes deferred wall time. This comparison does not measure CPU time or battery drain.'}


def resident_phase(result,phase):
    n=10 if phase=='confirm' else 6
    rows=result['pairs']
    require(len(rows)==n and result.get('complete_pairs')==n and [p.get('round') for p in rows]==list(range(1,n+1)) and
            all(p.get('complete') is True and p.get('order')==('AB' if p['round']%2 else 'BA') for p in rows),
            'Incomplete repeat-open timing observations')
    deltas={key:[] for key in ('second_usable','second_covers')}
    for row in rows:
        for key in deltas:
            a,b=(row[side+'_ms'][key] for side in ('baseline','candidate'))
            require(all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in (a,b)),'Invalid repeat-open endpoints')
            deltas[key].append(round(a-b,3))
    usable,covers=(round(statistics.median(deltas[key]),3) for key in deltas)
    wins=sum(v>0 for v in deltas['second_covers']); guard=usable>=-50
    benefit=guard and covers>=100 and wins>=n-1
    require(result.get('latency_gate_passed') is benefit,'Repeat-open latency decision differs from the original paired endpoints')
    return {'n':n,'wins':wins,'primary_median_ms':covers,'other_median_ms':usable,'benefit_passed':benefit,
            'regression_guard_passed':guard}


def require_import_default(rounds):
    # Unlike a functional opt-in, the prespecified import default also
    # requires independently confirmed benefit across the two user visits.
    require(set(rounds)=={'screen','confirm'} and all(r['regression_guard_passed'] is True and
            r['reopen_benefit_passed'] is True and r['two_stage_economic_gate'] is True for r in rounds.values())
            and rounds['confirm']['confirmed_default_gate'] is True,
            'Idle import cannot be the factory default without its own balanced screening, confirmed two-stage benefit, exit and pixel protections')


def visible_feature(result, name):
    return result.get('mechanism',{}).get('verified') is True and (name in ('resident','import') or
           result['mechanism'].get('name')==TRIAL[name])


def measured_trigger(result, name, cohort):
    require(name in ('prewarm','grace','layout','supervisor') and cohort in ('timing','pixels'),'Invalid mechanism cohort')
    mechanism=result.get('mechanism',{})
    group=mechanism.get(cohort,{})
    if mechanism.get('name')!=TRIAL[name] or set(group)!={'baseline','candidate'}: return False
    a,b=group['baseline'],group['candidate']
    if any(type(arm.get('observed')) is not int or type(arm.get('planned')) is not int or arm['observed'] < 1
           or arm['observed']!=arm.get('planned') for arm in (a,b)):
        return False
    relevant={'grace':('grace_started_runs','grace_completed_single_frame_runs'),
              'prewarm':('prewarm_plans','prewarm_ready','prewarm_attempted_runs','prewarm_usable_runs')}.get(name,())
    if any(type(arm.get(k)) is not int or arm[k]<0 for k in relevant for arm in (a,b)):return False
    def modes(arm,key,mode):
        observations=arm.get(key,{})
        return observations=={mode:arm['observed']} and type(observations[mode]) is int
    if name=='layout': return modes(a,'layouts','rowwise') and modes(b,'layouts','batched')
    if name=='supervisor': return modes(a,'portrait_engines','shell') and modes(b,'portrait_engines','native')
    if name=='grace': return a.get('grace_started_runs')==0 and 0 < b.get('grace_completed_single_frame_runs',0) <= b.get('grace_started_runs',0) <= b['observed']
    return all(a.get(k)==0 for k in ('prewarm_plans','prewarm_ready','prewarm_attempted_runs')) and 0 < b.get('prewarm_usable_runs',0) <= b['observed']


def apply_timing_attribution(result, name, stats):
    # One physical trigger can establish that the intervention ran. A separate
    # screen capture or controlled host test never counts as a timing trigger.
    arithmetic=stats['benefit_passed']; group=result.get('mechanism',{}).get('timing',{})
    timing=measured_trigger(result,name,'timing') and all(group[s]['observed']==stats['n'] for s in ('baseline','candidate'))
    require(result.get('latency_gate_passed') is arithmetic, 'Published and reconstructed latency decisions disagree')
    return stats | {'numeric_latency_gate': arithmetic,'timing_mechanism_observed': timing,'benefit_passed': arithmetic and timing}


def controlled_support(name, ticket, program, source_sha):
    require(name in ('grace','prewarm') and set(ticket)=={'run_id','completed_sha256','report'},'Invalid controlled witness')
    # This reviewed entry point reruns both the fixed positive exercise and
    # adverse cases. It can qualify the branch, never its on-device latency.
    path=HOST/'tools/functional_authority.py'
    evaluator=module('_controlled_qualifications',path,'7dbdbe0403f257d7287eb0a03afe64c01e46b4cc7755c5594635e3ff0befa6c5')
    controlled_program=r3_lineage.R2_PROGRAM if is_r3(program) else program
    evaluated=evaluator.verify(ticket,name,controlled_program,source_sha)
    stored=read_json(bound(ticket['report'],512*1024))
    require(evaluated==stored and stored['run_id']==ticket['run_id'] and stored['completed_sha256']==digest(ticket['completed_sha256'])
            and stored['feature']==name and stored['source_manifest_sha256']==source_sha and stored['program_sha256']==controlled_program
            and stored['functional_positive'] is True and stored['functional_negative'] is True
            and stored['latency_gate_passed'] is None and stored['performance_claim'] is False
            and bool(stored.get('observations')),'Controlled qualification is not the final build and observed branches')
    return {'run_id':stored['run_id'],'completed_sha256':stored['completed_sha256'],'report_sha256':ticket['report']['sha256'],
            'platform':stored['platform'],'test_elf_sha256':digest(stored['test_elf_sha256']),'cases':sorted(stored['observations']),
            'counts_as_timing_evidence':False,
            **({'scope':'Controlled Linux branch of the byte-identical application. No R3 device observations are reused.',
                'observed_program_sha256':controlled_program} if is_r3(program) else {})}


def all_trials(selection, program, source_sha):
    f=selection['features']; policy=check_policy(f); auditors=selection['auditors']; studies=selection['trials']
    require(set(studies)==set(FEATURES) and set(auditors)==set(AUDITORS) and selection['policy_sha256']==policy,
            'Missing study, auditor or production default policy')
    seen=set(); pictures_used={}; evidence={}; feature_qualifications={}; protection={}
    def collect(refs, name, phase, workload, label):
        report, runs=replay(refs,auditors,name,phase,workload,program)
        prim=set(refs.get('batches',runs)); visual=set(refs.get('qualification_batches',[])); picture_key=(name,workload)
        require(prim.isdisjoint(seen|set(pictures_used)) and visual.isdisjoint(seen) and
                all(pictures_used.get(k,picture_key)==picture_key for k in visual),'An independent trial reused a measurement as qualification')
        seen.update(prim); pictures_used.update(dict.fromkeys(visual,picture_key))
        evidence[label]=refs['report']['sha256']
        return report,runs
    for name in FEATURES:
        study=studies[name]
        needs_witness=name in ('grace','prewarm')
        needs_specialist=is_r3(program) and name in ('resident','import')
        extra=({'functional'} if needs_witness else set()) | ({'qualification'} if needs_specialist else set())
        require(set(study)-extra in ({'screen'}, {'screen','confirm'}) and extra<=study.keys(),
                'Unexpected or missing isolated trial and functional witness')
        support=controlled_support(name,study['functional'],program,source_sha) if needs_witness else None
        if support: evidence[name+'_functional']=support['report_sha256']
        rounds={}; ids={}; structural=False; pictures=False; prev=None
        for phase in ('screen','confirm'):
            if phase not in study: continue
            r,runs=collect(study[phase],name,phase,'warm',name+'_'+phase)
            variants=r['artifacts'];verify_isolate(variants,name,program)
            require(prev is None or prev==variants,'Screening and confirmation measure different program or options');prev=variants
            structural |= (visible_feature(r,name) if name in ('import','resident') else
                           (measured_trigger(r,name,'timing') or measured_trigger(r,name,'pixels')))
            if name=='import': stats=import_phase(r,phase)
            elif name=='resident': stats=resident_phase(r,phase)
            else: stats=apply_timing_attribution(r,name,physical_phase(r,name,phase))
            rounds[phase]=stats; ids[phase]=sorted(runs)
            if study[phase].get('qualification_batches'):
                if name in ('resident','import'): require(r.get('qualification_passed') is True,'Functional qualification failed')
                else: check_native_pictures(r)
                pictures=True
        specialist_evidence={}
        if needs_specialist:
            expected={'immediate','ordinary','ttl','wake'} if name=='resident' else {'import'}
            require(set(study['qualification'])==expected,'Missing independent specialist qualification')
            for scope,ticket in sorted(study['qualification'].items()):
                qualified,run=specialist(scope,ticket,program)
                require(run not in seen|set(pictures_used),'Specialist qualification reused a measured batch')
                pictures_used[run]=(name,scope)
                if scope=='import':
                    require(qualified.get('source_images_verified') is True and qualified.get('paired_native_pixels_verified') is True,
                            'Import qualification is missing source provenance or native pixels')
                if scope=='wake':
                    visual=qualified.get('short_sleep_visual',{})
                    require(set(visual)=={'baseline','candidate'} and all(r.get('same_second_gui') is True for r in visual.values()),
                            'Resident qualification did not preserve the reopened GUI across wake')
                evidence[name+'_'+scope]=ticket['report']['sha256']
                specialist_evidence[scope]=run
            pictures=True;structural=True
        structural = structural or support is not None
        require(pictures and structural, 'The available option lacks independent correctness or mechanism evidence: '+name)
        if name=='import' and f[name]=='default_on':
            require_import_default(rounds)
        result=classify(name,f[name],rounds['screen'],rounds.get('confirm'),structural)
        feature_qualifications[name]={'availability':'qualified','default_policy':f[name],'conclusion':result,
                                      'phases':rounds,'runs':ids,'source_manifest_sha256':source_sha,'program_sha256':program,
                                      **({'scope':'Isolated GUI import with the byte-identical application and installed launcher. Does not qualify the R3 default.'}
                                         if is_r3(program) and name=='import' and prev['baseline']['launcher_sha256']==CONTROL_LAUNCH else {}),
                                      **({'qualification_runs':specialist_evidence} if specialist_evidence else {}),
                                      **({'controlled_functional':support} if support else {})}
    require(set(selection['default_trials'])=={'warm','cold'}, 'Both coherent cache regimes are required')
    for workload in ('warm','cold'):
        item=selection['default_trials'][workload]
        r,runs=collect(item,'default','screen',workload,'default_'+workload)
        require(item['qualification_batches'], 'Actual default requires independent pixel qualification')
        protection[workload]={'runs':sorted(runs),'paired':check_default_guard(r,program,f,workload)}
    require(selection['defaults']['run_id'] not in seen|set(pictures_used),'Default lifecycle reused another measured batch')
    life=accepted_defaults(selection['defaults'],auditors,program,source_sha,f)
    if is_r3(program):
        require(life.get('r3_lineage_sha256')==selection['r3_lineage_sha256'],'The lifecycle and package name different lineage evidence')
    evidence['production_defaults']=selection['defaults']['report']['sha256']
    return feature_qualifications,protection,evidence


def build(selection, raw_sha, source, output=None, selection_ref=None):
    require(sys.platform=='linux' and not sys.flags.optimize, 'Run on test-env with Python checks enabled')
    require(selection.get('schema')==2 and selection.get('baseline_source_revision')==CONTROL_REVISION,
            'Wrong release schema or non-current source lineage')
    require(source.is_absolute() and source.resolve(strict=True)==source and source.is_dir(), 'Invalid source root')
    files=selection['artifacts']; require(set(files)==PROGRAM,'The integrated application, launcher and helper are all required')
    blobs={path:bound(ref) for path,ref in files.items()}; program={path:sha(b) for path,b in blobs.items()}
    require(program[APP] not in RETIRED|{CONTROL_APP} and program[LAUNCH]!=CONTROL_LAUNCH,'Stale or incomplete integrated program')
    source_sha=digest(selection['source_manifest_sha256']); features=selection['features'];policy=check_policy(features)
    release_version=r3_lineage.generation(selection|{'program_sha256':program,'baseline_sha256':CONTROL_APP})
    if release_version:
        ticket=selection['r3_lineage']; frozen=read_json(bound(ticket['inputs'],256*1024))
        require(frozen['artifacts']==files and frozen['receipt']==selection['build'] and Path(frozen['source_root'])==source,
                'Packaging must use the exact R3 lineage artifacts, producer receipt and source tree')
    manifest, scripts=verify_build(source,selection['build'],program,source_sha)
    qualifications,combined,ev=all_trials(selection,program,source_sha)
    ev['build']=selection['build']['sha256']
    base={'schema':2,'baseline_sha256':CONTROL_APP,'baseline_source_revision':CONTROL_REVISION,
          'baseline_archive_sha256':CONTROL_ZIP,'baseline_kpm_sha256':CONTROL_KPM,'catalog_registry':REGISTRY,
          'source_manifest_sha256':source_sha,'selection_sha256':raw_sha,'program_sha256':program,
          'features':features,'policy_sha256':policy,'feature_qualifications':qualifications,
          'default_nonregression':combined,'evidence_sha256':ev,'speedup_claim':'Only independently confirmed per-feature benefits. No aggregate speedup is inferred.'}
    if release_version:
        base.update(release_version,r3_lineage=ticket)
        require(selection_ref and selection_ref['sha256']==raw_sha,'Missing exact R3 selection reference')
        base['selection']=selection_ref
    if output is None:
        read_control()
        return base
    require(output.is_absolute() and output.parent.resolve(strict=True)==output.parent and not output.exists(), 'Output is not fresh and canonical')
    with tempfile.TemporaryDirectory(prefix='aidoku-frozen-release-', dir=output.parent) as temp:
        scratch=Path(temp)/'release';scratch.mkdir(mode=0o700)
        result=base | make_archives(scratch,source,blobs,program,manifest,scripts)
        write(scratch/'plan.json',result)
        output.mkdir(mode=0o700);os.rename(scratch,output)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for option in ('selection','source-root','output'):
        parser.add_argument('--'+option,type=Path,required=option!='output')
    parser.add_argument('--selection-sha256',required=True);parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args();require(args.check_only or args.output is not None, 'Provide a fresh output or --check-only')
    selection=read_json(pinned(args.selection,args.selection_sha256,1024*1024))
    result=build(selection,args.selection_sha256,args.source_root,None if args.check_only else args.output,
                 {'uri':args.selection.as_uri(),'sha256':args.selection_sha256})
    print(json.dumps({'qualified':True,'packaged':not args.check_only,'source_manifest_sha256':result['source_manifest_sha256'],
                      'plan':None if args.check_only else str(args.output/'plan.json')}))


if __name__=='__main__':main()
