#!/usr/bin/env python3
"""Seal one diagnostic application and approved stock-card fixtures on test-env."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil


HERE = Path(__file__).resolve().parent
PREVIOUS = Path('/var/tmp/aidoku-jpeg-native-20261122-freeze')


def load_previous(previous):
    source = previous / 'inputs' / 'freeze.py'
    spec = importlib.util.spec_from_file_location('jpeg_payload_builder', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checked_previous(builder, rendered, previous):
    common = rendered.common
    common.private_directory(previous)
    plan = common.private_directory(previous / 'plan')
    inputs = common.private_directory(previous / 'inputs')
    common.need(common.read_small(plan / 'ready', 16) == b'ready\n', 'prior_plan_not_ready')
    record = common.unique_json(common.read_small(plan / 'plan.json', 4096))
    common.need(record.get('schema') == 1 and record.get('owner') == builder.OWNER
                and record.get('origin') == 'stock-tap', 'invalid_prior_plan')
    analysis = common.load_module('jpeg_prior_analyzer', plan / 'analyze.py')
    receipts = analysis.read_receipts(common.private_directory(previous / 'receipts'), record['cohort'])
    common.need(len(receipts) == len(analysis.SCHEDULE) and all(row['result'] == 'measured' for row in receipts),
                'prior_visits_not_all_closed')
    sources = builder.prior_sources(rendered, builder.PRIOR)
    common.need(common.same_file(plan / 'guard.sh', sources['guard/guard.sh']), 'prior_guard_differs')
    sources['selector/selector.sh'] = common.regular(inputs / 'selector.sh', 128 * 1024)
    for name in builder.TOOL_NAMES:
        common.need(common.same_file(plan / 'tools' / name, builder.PRIOR / 'plan' / 'tools' / name),
                    'prior_host_tool_differs')
    return plan, record['cohort'], sources


def freeze(work, previous, scripts, diagnostic_app):
    builder = load_previous(previous)
    rendered = builder.load_rendered(builder.PRIOR)
    common = rendered.common
    work = common.private_directory(work)
    scripts = common.private_directory(scripts)
    prior, prior_cohort, sources = checked_previous(builder, rendered, previous)
    sources['submission/submission.sh'] = common.regular(scripts / 'submission.sh', 128 * 1024)
    sources['programs/selected-app'] = builder.program(common, diagnostic_app)
    target = work / 'plan'
    target.mkdir(mode=0o700)
    try:
        host_sources = {name: scripts / name for name in builder.HOST_INPUTS}
        host_sources.update({name: prior / name for name in ('common.py', 'rendered.py', 'guard.sh')})
        host_sources['jpeg_host.py'] = prior / 'host.py'
        for name, path in host_sources.items():
            common.write_new(target / name, common.read_small(path))
        compile(common.read_small(target / 'jpeg_host.py'), str(target / 'jpeg_host.py'), 'exec')
        for name in ('common.py', 'host.py'):
            (target / name).chmod(0o700)
        (target / 'tools').mkdir(mode=0o700)
        for name in builder.TOOL_NAMES:
            common.write_new(target / 'tools' / name, common.read_small(prior / 'tools' / name))
        builder.validate_scripts(common, target, sources)
        archive = target / 'payload.tar.gz'
        rendered.make_payload(archive, sources)
        archive.chmod(0o600)
        rendered.verify_payload(archive, sources)
        cohort = secrets.token_hex(8)
        common.need(cohort != prior_cohort, 'cohort_reused')
        record = {'schema': 1, 'cohort': cohort, 'owner': builder.OWNER, 'origin': 'stock-tap',
                  'configuration': 'diagnostic', 'visits': 5}
        common.write_new(target / 'plan.json', (json.dumps(record, indent=2) + '\n').encode())
        common.write_new(target / 'ready', b'ready\n')
    except BaseException:
        shutil.rmtree(target)
        raise
    return record


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
