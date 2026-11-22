#!/usr/bin/env python3
"""Seal one stock-card Library transition program on test-env without device access."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil


HERE = Path(__file__).resolve().parent
PREVIOUS = Path('/var/tmp/aidoku-card-stages-20261122-final')
STOCK_ASSETS = ('common.py', 'rendered.py', 'guard.sh', 'tools/device_lease.py', 'tools/run_hardware_smoke.py')
STAGE_ASSETS = {'stages_host.py': 'host.py', 'stage_analysis.py': 'analyze.py', 'jpeg_host.py': 'jpeg_host.py'}


def load_previous(previous):
    source = previous / 'inputs/freeze.py'
    spec = importlib.util.spec_from_file_location('closed_stage_freezer', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def closed_inputs(stages, builder, rendered, previous):
    common = rendered.common
    common.private_directory(previous)
    plan = common.private_directory(previous / 'plan')
    common.need(common.read_small(plan / 'ready', 16) == b'ready\n', 'prior_plan_not_ready')
    record = common.unique_json(common.read_small(plan / 'plan.json', 4096))
    common.need(record.get('schema') == 1 and record.get('owner') == builder.OWNER and record.get('visits') == 5
                and record.get('configuration') == 'diagnostic' and record.get('origin') == 'stock-tap',
                'invalid_prior_plan')
    prior_analysis = common.load_module('closed_stage_analyzer', plan / 'analyze.py')
    receipts = prior_analysis.read_receipts(common.private_directory(previous / 'receipts'), record['cohort'])
    common.need(len(receipts) == 5 and all(row['result'] == 'measured' and row['stages'] is not None
                                          for row in receipts), 'prior_visits_not_all_closed')
    stock_plan, stock_cohort, sources = stages.checked_previous(builder, rendered, stages.PREVIOUS)
    for name in STOCK_ASSETS:
        common.need(common.same_file(plan / name, stock_plan / name), 'prior_stock_asset_differs')
    common.need(common.same_file(plan / 'jpeg_host.py', stock_plan / 'host.py'), 'prior_engine_differs')
    return plan, {stock_cohort, record['cohort']}, sources


def freeze(work, previous, scripts, diagnostic_app):
    stages = load_previous(previous)
    builder = stages.load_previous(stages.PREVIOUS)
    rendered = builder.load_rendered(builder.PRIOR)
    common = rendered.common
    work = common.private_directory(work)
    scripts = common.private_directory(scripts)
    prior, old_cohorts, sources = closed_inputs(stages, builder, rendered, previous)
    sources['submission/submission.sh'] = common.regular(scripts / 'submission.sh', 128 * 1024)
    sources['programs/selected-app'] = builder.program(common, diagnostic_app)
    target = work / 'plan'
    target.mkdir(mode=0o700)
    try:
        assets = {name: scripts / name for name in builder.HOST_INPUTS}
        assets.update({name: prior / name for name in STOCK_ASSETS})
        assets.update({name: prior / source for name, source in STAGE_ASSETS.items()})
        (target / 'tools').mkdir(mode=0o700)
        for name, source in assets.items():
            common.write_new(target / name, common.read_small(source))
        for name in ('common.py', 'host.py'):
            (target / name).chmod(0o700)
        for name in STAGE_ASSETS:
            script = target / name
            compile(common.read_small(script), str(script), 'exec')
        builder.validate_scripts(common, target, sources)
        archive = target / 'payload.tar.gz'
        rendered.make_payload(archive, sources)
        archive.chmod(0o600)
        rendered.verify_payload(archive, sources)
        cohort = secrets.token_hex(8)
        common.need(cohort not in old_cohorts, 'cohort_reused')
        plan = {'schema': 1, 'cohort': cohort, 'owner': builder.OWNER, 'origin': 'stock-tap',
                'configuration': 'diagnostic', 'visits': 4}
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
    parser.add_argument('--diagnostic-app', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    record = freeze(args.work, PREVIOUS, args.scripts, args.diagnostic_app)
    print(json.dumps({'plan': str(args.work / 'plan'), 'cohort': record['cohort'], 'device_accessed': False}))


if __name__ == '__main__':
    main()
