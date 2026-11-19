#!/usr/bin/env python3
"""Freeze isolated R3 specialist batches on test-env without using a Kindle."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path('/root/aidoku-all-bench-20261119')
RELEASE = Path('/root/aidoku-all-build-20261119/artifacts/final-v3-clean')
ELF = '1b1c508e1ee84a4be3bd0c15bcf6d42bdd492d688f3fa81b065fb256e00dd3af'
LAUNCH = '412163f1e84a4ce0ae869439555f80bea8c0ac6b56dcd54b0cd3bccf68804675'
HELPER = 'bde28cd5953d26b0dc3bfebe16fcfe1cde329021c17449edae7c59470376ed50'
R2 = '2b0d6e8ee76076c1f14e92dae97a505b40f0a78e07cec78cdea23ec59398bae5'
INSTALLED = '10432125b74ab5b69c8a0ec54a34abd5a74dc4a3cc9ed9b9d5cc8d03bbd75296'
MEMBERS = {'candidate-arm': ELF, 'launch.sh': LAUNCH, 'aidoku-startup-portrait': HELPER}


def sha(blob):
    return sha256(blob).hexdigest()


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def pin(path, expected):
    need(path.is_file() and not path.is_symlink(), 'Missing regular frozen input')
    blob = path.read_bytes()
    need(sha(blob) == expected, 'Frozen input changed: ' + path.name)
    return blob


def freeze(path, blob):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        pin(path, sha(blob))
    else:
        with path.open('xb') as f:
            f.write(blob)
    return path


def replace(source, old, new):
    need(source.count(old) == 1, 'Historical source anchor changed: ' + old[:50])
    return source.replace(old, new, 1)


def run(*args):
    return subprocess.check_output([sys.executable, '-I', *map(str, args)], text=True).strip()


def arm_args(option, control, candidate):
    args = []
    for arm, setting in [('baseline', control), ('candidate', candidate)]:
        args += [f'--{arm}-app', RELEASE/'candidate-arm', f'--{arm}-launcher', RELEASE/'launch.sh',
                 f'--{arm}-extra', 'aidoku-startup-portrait=' + str(RELEASE/'aidoku-startup-portrait'),
                 f'--{arm}-option', f'{option}={setting}']
    return args


def receipt(path, option, control, candidate, mode):
    plan = json.loads(path.read_bytes())
    need(plan['mode'] == mode and plan['original_launcher_sha256'] == INSTALLED,
         'Unexpected frozen workload or restoration source')
    for arm, setting in [('baseline', control), ('candidate', candidate)]:
        v = plan['variants'][arm]
        need(v['application_sha256'] == ELF and v['launcher_sha256'] == LAUNCH and
             v['extra_sha256'] == {'aidoku-startup-portrait': HELPER} and
             v['startup_options'] == {option: setting} and not v['args'] and not v['env'],
             'Frozen arm does not match the reviewed R3 composition')
    return {'plan': str(path), 'plan_sha256': sha(path.read_bytes()), 'mode': mode,
            'runner_sha256': plan['runner_sha256'], 'device_script_sha256': plan['device_script_sha256']}


def portrait():
    original = pin(ROOT/'tools/prepare_portrait.py', 'd032c10c9d8117eced4bbd69681f67264c731da92b1d6d7ed98cec9c7dd904f9').decode()
    source = replace(original, R2, LAUNCH).replace('portrait-v2', 'portrait-v3').replace('final-v2', 'final-v3-clean').replace('v2-portrait', 'v3-portrait').replace('RELEASE_V2', 'RELEASE_V3').replace(' v2 ', ' R3 ')
    tool = freeze(ROOT/'portrait-v3/prepare_portrait.py', source.encode())
    return {'portrait-screen': receipt(Path(json.loads(run(tool, 'screen'))['plan']), 'portrait', 'shell', 'native', 'screen')}


def reopen():
    plans = {}
    for wake in (False, True):
        suffix = 'wake' if wake else '.'
        source = ROOT/'reopen_probe'/suffix
        target = ROOT/'reopen-v3'/suffix
        host_hash, device_hash = (
            ('a284b17bf38f6c21212cea57a1f82260d0e61d5d44385f006377b2b580dcc864', 'fe3271f41126d908c0db2281d5ab68f3e3191ca5b6432a67d9eaa6e4048abe1e') if wake else
            ('2ea29b78c044ed2884765a0ca7c6993e244eed53d937779998573a7a90345226', 'c8bbdad79863a7785c3cfd41c07662dec011f60bb3c5971745be4a727e318b21'))
        host = pin(source/'run_bench.py', host_hash).decode()
        device = pin(source/'bench_device.sh', device_hash)
        host = replace(host, f"ROOT = Path('{source}')", f"ROOT = Path('{target}')")
        host = replace(host, R2, LAUNCH).replace('qualified R2 program', 'qualified R3 program')
        runner = freeze(target/'run_bench.py', host.encode())
        freeze(target/'bench_device.sh', device)
        subprocess.run(['sh', '-n', target/'bench_device.sh'], check=True)
        todo = [('wake', 'qualify', 'immediate')] if wake else [
            ('immediate', 'qualify', 'immediate'), ('ordinary', 'qualify', 'ordinary'),
            ('ttl', 'qualify', 'ttl'), ('screen', 'screen', 'immediate')]
        for label, mode, scenario in todo:
            args = ['prepare', '--trial', 'reopen', '--primary', 'covers', '--mode', mode,
                    '--scenario', scenario, '--name', 'v3-ro-'+label[:5]]
            plan = Path(run(runner, *args, *arm_args('resident', 'off', '45')))
            need(json.loads(plan.read_bytes()).get('reopened_powerd_wake', False) is wake, 'Incorrect wake workload')
            plans['reopen-'+label] = receipt(plan, 'resident', 'off', '45', mode)
    return plans


def import_qualification():
    old = ROOT/'import_probe_r2/tools'
    target = ROOT/'import-v3/tools'
    host = pin(old/'run_bench.py', 'c0d69c5ac8702d4b483f06665a10e8646fc28053d7015c808ed771676fc66ee4').decode()
    host = replace(host, f"ROOT = Path('{old.parent}')", f"ROOT = Path('{target.parent}')")
    host = replace(host, "    if ns.trial != 'import' or ns.primary != 'covers':", "    if ns.mode != 'qualify' or ns.trial != 'import' or ns.primary != 'covers':")
    host = replace(host, "    if any((left['args'],left['env'],left['extra_sha256'])) or left['launcher_sha256']!=LAUNCH_SHA:raise ValueError('Use production launcher and unmodified protocol')",
                   f"    if any((left['args'],left['env'])) or left['application_sha256']!='{ELF}' or left['launcher_sha256']!='{LAUNCH}' or left['extra_sha256']!={{'aidoku-startup-portrait':'{HELPER}'}}:raise ValueError('Use the exact R3 composition and unmodified protocol')")
    runner = freeze(target/'run_bench.py', host.encode())
    freeze(target/'bench_device.sh', pin(old/'bench_device.sh', '3c9bdfde3fc87385283d1070a37340e950c9d0a037ffd421037e67f0e3c7a7ad'))
    freeze(target/'cache-inventory', pin(old/'cache-inventory', '7d9a37d00eb41e0132d5b47931e2f41ef8aac345f31449e195b23e513f559dc4'))
    # The runner checks the fixture manifest; the analyzer regenerates every archive.
    for name in ['fixture.json', 'archives.sha256', *[f'comics/S{i:02}.cbz' for i in range(20)]]:
        freeze(target/'fixture_payload'/name, (old/'fixture_payload'/name).read_bytes())
    plan = Path(run(runner, 'prepare', '--trial', 'import', '--primary', 'covers', '--mode', 'qualify',
                    '--name', 'v3-import-q', *arm_args('import_thumbnail', 'off', 'idle')))
    return {'import-qualify': receipt(plan, 'import_thumbnail', 'off', 'idle', 'qualify')}


def main():
    if sys.platform != 'linux' or not ROOT.is_dir():
        raise SystemExit('Run only on test-env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', choices=('portrait', 'reopen', 'import', 'all'))
    scope = parser.parse_args().scope
    for member, identity in MEMBERS.items():
        pin(RELEASE/member, identity)
    steps = {'portrait': portrait, 'reopen': reopen, 'import': import_qualification}
    plans = {}
    for step in (steps.values() if scope == 'all' else [steps[scope]]):
        plans.update(step())
    print(json.dumps({'members': MEMBERS, 'plans': plans}, indent=2))


if __name__ == '__main__':
    main()
