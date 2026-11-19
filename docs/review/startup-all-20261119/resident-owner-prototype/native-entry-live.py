#!/usr/bin/env python3
"""Freeze or run a guarded, two-visit observation of the installed Library route."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType
import uuid


HERE = Path(__file__).resolve().parent
ROOT = Path('/root/aidoku-gui-entry-proof-20261119/live')
PAST = Path('/root/aidoku-next-promotion-20261118/tools')
TRANSPORT = Path('/root/aidoku-display-modes-20261114-01a1da18/tools')
APP = '9d92dc4cd01005a6ba2f48fe61786e5df5570c43feb5709fe3159bb164807d0f'
SOURCES = {
    PAST / 'run.py': '56863607d2b4c83b680b12a5a4cce6b6f1c79e4dd181946aad66030ba4838885',
    PAST / 'smoke.sh': '7634e54d3100fd13a5903249c40d8adf27d415822b59b6d1385a082287fdc501',
    TRANSPORT / 'device_lease.py': '494c62e67e29b2a9388148e4cef503ff7feceb15ef90c4af43a5ee3cf6971b72',
    TRANSPORT / 'guarded-device-ssh.py': 'c2dfb7e982e32d02cf28fe901d85400a367647db08b3100946c7a83e0458e2db',
}
RUN = re.compile(r'aidoku-entry-live-[0-9a-f]{12}\Z')
KEYS = ('worker_exact', 'scriptlet_active', 'waiter_exact', 'canonical_su', 'handler_exact',
        'manager_known', 'su_handler_manager_direct', 'canonical_command_shell',
        'canonical_document_shell', 'unexpected_process', 'su_other_argv_or_mode', 'handler_other_argv')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def checked(path):
    require(path.is_file() and path.resolve(strict=True) == path and path.stat().st_size < 128 * 1024,
            'Invalid fixed host input')
    blob = path.read_bytes()
    if path in SOURCES:
        require(digest(blob) == SOURCES[path], 'Fixed smoke or guarded transport changed')
    return blob


def dependencies():
    fixed = {path: checked(path) for path in SOURCES}
    engine = ModuleType('_installed_smoke_engine')
    run_path = PAST / 'run.py'
    engine.__file__ = str(run_path)
    exec(compile(fixed[run_path], engine.__file__, 'exec'), engine.__dict__)
    cfg = engine.unique_json(engine.SEED.read_bytes())
    require(Path(cfg['lease_tool_directory']) == TRANSPORT and
            all(Path(cfg[key]).is_file() for key in ('password_file', 'known_hosts', 'askpass')),
            'Existing guarded transport unavailable')
    smoke = fixed[PAST / 'smoke.sh']
    anchor = b'# Entry point.'
    require(smoke.count(anchor) == 1, 'Smoke library boundary changed')
    return engine, smoke.split(anchor)[0] + checked(HERE / 'native-entry-live.sh') + b'\n'


def observations(raw):
    require(len(raw) < 1024, 'Oversized observation')
    expected = [(label + '-' + stage, count)
                for label in ('first', 'second')
                for stage, count in (('admitted', len(KEYS)), ('exiting', len(KEYS)), ('stable', 1))]
    expected.append(('distinct', 3))
    rows = raw.decode('ascii').splitlines()
    require(len(rows) == len(expected), 'Incomplete observations')
    entries = {}
    for (name, count), line in zip(expected, rows):
        parts = line.split(' ')
        require(parts[0] == name and len(parts) == count + 1 and set(parts[1:]) <= {'0', '1'},
                'Unexpected observation grammar')
        entries[name] = [part == '1' for part in parts[1:]]
    pairs = [{stage: dict(zip(KEYS, entries[label + '-' + stage])) for stage in ('admitted', 'exiting')}
             | {'stable': entries[label + '-stable'][0]} for label in ('first', 'second')]
    distinct = dict(zip(('waiter', 'canonical_su', 'handler'), entries['distinct']))
    groups = ('worker_exact', 'scriptlet_active', 'waiter_exact', 'canonical_su', 'handler_exact',
              'manager_known', 'su_handler_manager_direct')
    qualified = distinct['waiter'] and distinct['canonical_su'] and all(
        pair['stable'] and all(all(pair[stage][key] for key in groups) and
                               not pair[stage]['unexpected_process'] and
                               not pair[stage]['su_other_argv_or_mode'] and
                               not pair[stage]['handler_other_argv'] for stage in ('admitted', 'exiting'))
        for pair in pairs)
    return {'visits': pairs, 'distinct': distinct, 'canonical_lineage_observed': qualified}


def plan_for(run, library):
    return {'schema': 1, 'operation': 'installed-entry-lineage', 'run': run, 'application_sha256': APP,
            'runner_sha256': digest(checked(Path(__file__))), 'device_script_sha256': digest(library),
            'fixed_dependencies': {str(path): sha for path, sha in SOURCES.items()},
            'visits': 2, 'shared_files_written': False, 'synthetic_input_requested': False}


def save(path, value):
    with path.open('x') as out:
        json.dump(value, out, sort_keys=True, indent=2)
        out.write('\n')


def prepare(engine, library):
    run = 'aidoku-entry-live-' + uuid.uuid4().hex[:12]
    root = ROOT / 'plans'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(['sh', '-n'], input=library, check=True)
    plan = root / (run + '.json')
    save(plan, plan_for(run, library))
    print(json.dumps({'plan': str(plan), 'plan_sha256': digest(plan.read_bytes()),
                      'device_accessed': False, 'lease_acquired': False}))


def execute(engine, library, plan_path, expected):
    require(plan_path.parent == ROOT / 'plans', 'Unrecognized private plan')
    data = checked(plan_path)
    require(digest(data) == expected, 'Plan hash differs')
    plan = engine.unique_json(data)
    run = plan['run']
    require(RUN.fullmatch(run) and plan_path.name == run + '.json' and plan == plan_for(run, library),
            'Frozen plan differs')
    engine.ROOT = ROOT
    session = engine.Transport(run)
    q = engine.q
    stage = '/var/tmp/' + run
    prefix = library.decode('ascii') + f'RUN={q(run)}\nSTAGE={q(stage)}\nOP=before\nPIXTMP="$STAGE/pixels"\n'
    init = 'absent "$STAGE"\nmkdir -m 700 "$STAGE"\nnew_line "$RUN" "$STAGE/owner"\nguard_visit_stage\n'
    guard = 'guard_visit_stage\ncd "$STAGE"\n'
    protection = engine.REGISTRY + engine.check_files(engine.PROTECTED) + engine.program(engine.DEPLOYED | {engine.APP: APP})
    report = {'completed': False, 'lease_acquired': False, 'lease_released': False,
              'native_state_restored': False, 'lighting_restored': False, 'shared_entry_preserved': False}
    state = lighting = None
    staged = visited = False
    try:
        proof = session.connect()
        report['lease_acquired'] = True
        save(session.batch / 'lease-acquired.json', proof)
        state = session.call('native-before', engine.STATE)
        session.call('protected-before', protection)
        lighting = engine.light_levels(session.call('lighting-before', engine.LIGHT))
        staged = True
        session.call('stage', prefix + init)
        visited = True
        raw = session.call('two-ordinary-visits', prefix + guard + 'observe_visits', timeout=100)
        report.update(observations(raw))
        visited = False
        session.call('protected-after', protection)
        report['shared_entry_preserved'] = True
    except Exception:
        report['stopped_at'] = session.calls[-1]['operation'] if session.calls else 'acquire'
    finally:
        if report['lease_acquired']:
            safe = True
            try:
                if visited:
                    session.call('recover-owned-visit', prefix + guard + 'visit_recover', timeout=60)
                if lighting is not None:
                    if visited:
                        engine.restore_levels(session, lighting)
                    elif engine.light_levels(session.call('lighting-after', engine.LIGHT)) != lighting:
                        engine.restore_levels(session, lighting)
                    report['lighting_restored'] = True
                if state is not None:
                    require(session.call('native-after', engine.STATE) == state, 'Original native state differs')
                    report['native_state_restored'] = True
                if staged:
                    session.call('remove-private-stage', prefix + 'if ! absent "$STAGE"; then\n' + guard + 'remove_visit_stage\nfi\n')
            except Exception:
                report['recovery_needed'] = True
                safe = False
            if safe:
                try:
                    session.release()
                    release = engine.unique_json((session.batch / 'lease-released.json').read_bytes())
                    report['lease_released'] = release.get('owner_and_token_checked') is True and release.get('lease_directory_absent_after_release') is True
                except Exception:
                    report['release_unconfirmed'] = True
        report['completed'] = 'visits' in report and all(report[key] for key in (
            'lease_released', 'native_state_restored', 'lighting_restored', 'shared_entry_preserved'))
        save(session.batch / 'entry-observation.json', report)
        print(json.dumps(report, sort_keys=True))
    return 0 if report['completed'] else 2


def main():
    require(sys.platform == 'linux', 'Run on test-env')
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('prepare')
    run = sub.add_parser('run')
    run.add_argument('plan', type=Path)
    run.add_argument('sha256')
    args = parser.parse_args()
    engine, library = dependencies()
    if args.operation == 'prepare':
        return prepare(engine, library)
    return execute(engine, library, args.plan, args.sha256)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        sys.exit('Installed entry observation stopped; consult the private evidence.')
