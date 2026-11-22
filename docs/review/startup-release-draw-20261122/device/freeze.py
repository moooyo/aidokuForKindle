#!/usr/bin/env python3
"""Seal one release draw diagnostic and the approved ordinary-card transport on test-env."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile


WORK = Path('/var/tmp/aidoku-release-draw-20261122-final')
PREVIOUS = Path('/var/tmp/aidoku-library-transition-20261122-final')
PROBE = 'release-draw-accounting'
INHERITED = {
    'transition_host.py': 'host.py', 'transition_analysis.py': 'analyze.py',
    **{name: name for name in ('stages_host.py', 'stage_analysis.py', 'jpeg_host.py', 'common.py',
                               'rendered.py', 'guard.sh', 'tools/device_lease.py', 'tools/run_hardware_smoke.py')},
}


def load_previous(previous):
    path = previous / 'inputs/freeze.py'
    spec = importlib.util.spec_from_file_location('draw_previous_freezer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checked_inputs(previous):
    transition = load_previous(previous)
    stages = transition.load_previous(transition.PREVIOUS)
    builder = stages.load_previous(stages.PREVIOUS)
    rendered = builder.load_rendered(builder.PRIOR)
    common = rendered.common
    common.private_directory(previous)
    inputs = common.private_directory(previous / 'inputs')
    old_plan = common.private_directory(previous / 'plan')
    audit = common.load_module('draw_parent_replay', inputs / 'audit.py')
    evidence = audit.audit(previous, 4)
    rows, counts = evidence['receipts'], evidence['audit']
    record = common.unique_json(common.read_small(old_plan / 'plan.json', 4096))
    common.need(len(rows) == 4 and all(row['cohort'] == record['cohort'] and row['slot'] == slot
                and row['result'] == 'measured' and row['transition'] is not None
                and all(flag is True for flag in row['closeout'].values()) for slot, row in enumerate(rows, 1))
                and all(counts[key] == 4 for key in ('checked_visits', 'replayed_endpoints', 'replayed_transition_records'))
                and record['owner'] == builder.OWNER, 'previous_four_visits_not_replayed')
    baseline, excluded, sources = transition.closed_inputs(stages, builder, rendered, transition.PREVIOUS)
    for name in transition.STOCK_ASSETS:
        common.need(common.same_file(old_plan / name, baseline / name), 'changed_stock_asset')
    for name, origin in transition.STAGE_ASSETS.items():
        common.need(common.same_file(old_plan / name, baseline / origin), 'changed_stage_asset')
    return builder, rendered, old_plan, excluded | {record['cohort']}, sources


def verify_shell_sources(common, scripts, previous):
    generator = common.regular(scripts / 'collect.py', 128 * 1024)
    base_device = common.regular(previous / 'plan/device.sh', 128 * 1024)
    base_collector = common.regular(previous / 'inputs/submission.sh', 128 * 1024)
    with tempfile.TemporaryDirectory(dir=scripts, prefix='.draw-generate-') as directory:
        generated = Path(directory)
        device, collector = generated / 'device.sh', generated / 'submission.sh'
        result = subprocess.run([sys.executable, '-B', generator, base_device, base_collector, device, collector],
                                capture_output=True, timeout=30)
        common.need(result.returncode == 0 and common.same_file(device, scripts / 'device.sh')
                    and common.same_file(collector, scripts / 'submission.sh'), 'unbound_collector_scripts')


def driver_bytes(common, previous, work):
    source = common.read_small(previous / 'inputs/run_slots.py', 16 * 1024).decode('ascii')
    for old, new in (('1 <= first <= last <= 14', '1 <= first <= last <= 4'),
                     ('1 <= args.first <= args.last <= 14', '1 <= args.first <= args.last <= 4'),
                     ('if not 1 <= first <= last <= 4:',
                      f'if work != Path({str(work)!r}) or not 1 <= first <= last <= 4:'),
                     ('if not 1 <= args.first <= args.last <= 4 or not sys.stdin.isatty():',
                      f'if args.work != Path({str(work)!r}) or not 1 <= args.first <= args.last <= 4 or not sys.stdin.isatty():')):
        common.need(source.count(old) == 1, 'unexpected_slot_driver')
        source = source.replace(old, new)
    driver = '#!/usr/bin/env python3\n' + source
    compile(driver, 'run_slots.py', 'exec')
    return driver.encode('ascii')


def freeze(work, previous, scripts, diagnostic_app):
    builder, rendered, prior, excluded_cohorts, sources = checked_inputs(previous)
    common = rendered.common
    work, scripts = common.private_directory(work), common.private_directory(scripts)
    common.need(scripts == work / 'inputs', 'detached_work_inputs')
    verify_shell_sources(common, scripts, previous)
    sources['submission/submission.sh'] = common.regular(scripts / 'submission.sh', 128 * 1024)
    sources['programs/selected-app'] = builder.program(common, diagnostic_app)
    runner, expected_runner = scripts / 'run_slots.py', driver_bytes(common, previous, work)
    target = work / 'plan'
    target.mkdir(mode=0o700)
    try:
        if os.path.lexists(runner):
            common.need(common.read_small(runner, 16 * 1024) == expected_runner and os.access(runner, os.X_OK),
                        'conflicting_slot_driver')
        (target / 'tools').mkdir(mode=0o700)
        operators = {name: scripts / name for name in builder.HOST_INPUTS}
        operators.update({name: prior / origin for name, origin in INHERITED.items()})
        for name, source in operators.items():
            data = common.read_small(source)
            if name.endswith('.py'):
                compile(data, name, 'exec')
            common.write_new(target / name, data)
        for name in ('host.py', 'common.py'):
            (target / name).chmod(0o700)
        builder.validate_scripts(common, target, sources)
        payload = target / 'payload.tar.gz'
        rendered.make_payload(payload, sources)
        payload.chmod(0o600)
        rendered.verify_payload(payload, sources)
        cohort = secrets.token_hex(8)
        common.need(cohort not in excluded_cohorts, 'cohort_reused')
        plan = {'schema': 1, 'cohort': cohort, 'owner': builder.OWNER, 'origin': 'stock-tap',
                'configuration': 'diagnostic', 'visits': 4, 'diagnostic': PROBE}
        if not os.path.lexists(runner):
            common.write_new(runner, expected_runner)
            runner.chmod(0o700)
        common.write_new(target / 'plan.json', (json.dumps(plan, indent=2) + '\n').encode('ascii'))
        common.write_new(target / 'ready', b'ready\n')
    except BaseException:
        shutil.rmtree(target)
        raise
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=WORK)
    parser.add_argument('--scripts', type=Path, default=WORK / 'inputs')
    parser.add_argument('--diagnostic-app', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'linux' or args.work != WORK or args.scripts != WORK / 'inputs':
        parser.error('Only the designated test-env workspace can be sealed')
    os.umask(0o077)
    plan = freeze(args.work, PREVIOUS, args.scripts, args.diagnostic_app)
    print(json.dumps({'cohort': plan['cohort'], 'verified_payloads': 1, 'visits': plan['visits'], 'device_accessed': False}))


if __name__ == '__main__':
    main()
