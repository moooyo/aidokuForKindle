#!/usr/bin/env python3
"""Freeze the two stock-card JPEG probe payloads on test-env; never access a device."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import sys


HERE = Path(__file__).resolve().parent
PRIOR = Path('/var/tmp/aidoku-rendered-library-20261121-YfUUqR5k')
OWNER = os.environ.get('CODEX_THREAD_ID')
if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', OWNER or ''):
    OWNER = None
MAX_PROGRAM = 96 * 1024**2
TOOL_NAMES = ('device_lease.py', 'run_hardware_smoke.py')
HOST_INPUTS = ('device.sh', 'host.py', 'analyze.py')
PAYLOAD_INPUTS = {'selector/selector.sh': 'selector.sh', 'submission/submission.sh': 'submission.sh'}


def load_rendered(prior):
    path = prior / 'plan' / 'host.py'
    spec = importlib.util.spec_from_file_location('jpeg_prior_rendered', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prior_sources(rendered, prior):
    common = rendered.common
    common.private_directory(prior)
    plan_dir = common.private_directory(prior / 'plan')
    report = common.unique_json(common.read_small(prior / 'attempt' / 'operations.json', 256 * 1024))
    acquisition, release = report['acquisition'], report['release']
    prior_owner = common.owner_id(acquisition.get('owner_thread'))
    plan = rendered.checked_plan(prior, prior_owner, minimal=True)
    request = {'owner_thread': prior_owner, 'run_id': rendered.RUN_PREFIX + plan['label']}
    common.need(report['schema'] == 1 and report['request'] == request and report['phase'] == 'release'
                and report['completed'] == plan['cases'] and report['recovery_required'] is False
                and all(report[key] is True for key in ('released', 'close_confirmed', 'absence_confirmed',
                                                       'operations_passed'))
                and acquisition['ok'] is True and acquisition['operation'] == 'acquire'
                and acquisition['state'] == 'held' and acquisition['project'] == 'Aidoku'
                and release['ok'] is True and release['operation'] == 'release' and release['state'] == 'released'
                and all(acquisition[key] == value for key, value in request.items())
                and all(acquisition[key] == release[key] == report['identity'][key] for key in common.IDENTITY)
                and all(release[key] is True for key in ('owner_and_token_checked',
                         'lease_directory_absent_after_release', 'native_restored_and_connections_closed')),
                'prior_batch_not_closed')

    legacy = common.private_directory(Path(plan['legacy']))
    reference, navigation = Path(plan['reference']), Path(plan['navigation'])
    common.closed_reference(reference, 'reference', legacy)
    common.closed_reference(navigation, 'navigation', legacy)
    guard = Path(plan['approved_guard'])
    common.need(common.same_file(plan_dir / 'guard.sh', guard) and common.same_file(Path(plan['guard']), guard),
                'guard_differs_from_approved')
    sources = rendered.input_sources(legacy, reference, navigation,
                                     *(Path(plan[key]) for key in ('guard', 'visit', 'dispatch', 'watcher')))
    common.need(plan['sources'] == {name: str(path) for name, path in sources.items()}, 'changed_prior_sources')
    common.need(re.fullmatch(rb'(?:[0-7]{3}\n){3}', common.read_small(sources['control/program-modes'], 1024)),
                'invalid_control_modes')
    del sources['input/render-watch'], sources['input/reference.rule']
    sources['guard/guard.sh'] = guard
    sources['rename-new'] = common.regular(legacy / 'operator/programs/payload/rename-new', 1024**2)
    for name in TOOL_NAMES:
        common.need(common.same_file(plan_dir / 'tools' / name, legacy / 'tools' / name), 'changed_prior_tool')
    return sources


def program(common, path):
    path = common.regular(path, MAX_PROGRAM)
    with path.open('rb') as payload:
        ident = payload.read(16)
        common.need(len(ident) == 16 and ident.startswith(b'\x7fELF\x01\x01\x01'), 'expected_elf32_le')
        raw_header = payload.read(36)
        common.need(len(raw_header) == 36, 'truncated_elf')
        (kind, machine, version, _, program_offset, _, flags, header_size, program_size,
         program_count, _, _, _) = struct.unpack('<HHIIIIIHHHHHH', raw_header)
        common.need(kind in (2, 3) and machine == 40 and version == 1 and (flags & 0xFF000600) == 0x05000400
                    and header_size == 52 and program_size == 32 and 0 < program_count <= 128
                    and program_offset >= 52 and program_offset + program_size * program_count <= path.stat().st_size,
                    'expected_arm_eabi5_hard_float')
        payload.seek(program_offset)
        segments = {struct.unpack('<8I', payload.read(32))[0] for index in range(program_count)}
        common.need(1 in segments and 3 not in segments, 'expected_static_executable')
    return path


def validate_scripts(common, target, sources):
    for name in ('host.py', 'analyze.py', 'common.py', 'rendered.py', *(f'tools/{item}' for item in TOOL_NAMES)):
        script = target / name
        compile(common.read_small(script), str(script), 'exec')
    for script in (target / 'device.sh', target / 'guard.sh', *(
            source for name, source in sources.items() if name.endswith('.sh'))):
        body = common.read_small(script, 128 * 1024)
        common.need(b'\r' not in body and b'\x00' not in body and body.endswith(b'\n'), 'invalid_shell_text')
        subprocess.run(('sh', '-n', script), check=True)


def freeze(work, prior, scripts, arm_a, arm_b):
    if OWNER is None:
        raise ValueError('designated_operator_required')
    rendered = load_rendered(prior)
    common = rendered.common
    work = common.private_directory(work)
    scripts = common.private_directory(scripts)
    sources = prior_sources(rendered, prior)
    for name, filename in PAYLOAD_INPUTS.items():
        sources[name] = common.regular(scripts / filename, 128 * 1024)
    arms = {'A': program(common, arm_a), 'B': program(common, arm_b)}
    common.need(not common.same_file(arms['A'], arms['B']), 'identical_arms')
    target = work / 'plan'
    target.mkdir(mode=0o700)
    try:
        prior_plan = prior / 'plan'
        for name, path in {**{name: scripts / name for name in HOST_INPUTS},
                           'common.py': prior_plan / 'common.py', 'rendered.py': prior_plan / 'host.py',
                           'guard.sh': sources['guard/guard.sh']}.items():
            common.write_new(target / name, common.read_small(path))
        for name in ('host.py', 'analyze.py', 'common.py'):
            (target / name).chmod(0o700)
        (target / 'tools').mkdir(mode=0o700)
        for name in TOOL_NAMES:
            common.write_new(target / 'tools' / name, common.read_small(prior_plan / 'tools' / name))
        validate_scripts(common, target, sources)
        for arm, executable in arms.items():
            (target / arm).mkdir(mode=0o700)
            archive_path = target / arm / 'payload.tar.gz'
            members = {**sources, 'programs/selected-app': executable}
            rendered.make_payload(archive_path, members)
            archive_path.chmod(0o600)
            rendered.verify_payload(archive_path, members)
        plan = {'schema': 1, 'cohort': secrets.token_hex(8), 'owner': OWNER, 'origin': 'stock-tap'}
        common.write_new(target / 'plan.json', (json.dumps(plan, indent=2) + '\n').encode())
        common.write_new(target / 'ready', b'ready\n')
    except BaseException:
        shutil.rmtree(target)
        raise
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--scripts', type=Path, default=HERE)
    parser.add_argument('--arm-a', type=Path, required=True)
    parser.add_argument('--arm-b', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    plan = freeze(args.work, PRIOR, args.scripts, args.arm_a, args.arm_b)
    print(json.dumps({'plan': str(args.work / 'plan'), 'cohort': plan['cohort'], 'device_accessed': False}))


if __name__ == '__main__':
    main()
