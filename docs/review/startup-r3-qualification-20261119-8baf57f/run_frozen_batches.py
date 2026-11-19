#!/usr/bin/env python3
"""Run explicitly selected, unused handoff batches with the frozen runners."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys


INDEX = Path('/root/aidoku-all-release-20261119/handoff/r3-plan-index.json')
INDEX_SHA = 'd2b29f829a081bba6f579102da135e414ab7578fb3dc7d63edf5efe57772854e'
PORTRAIT_PIXELS = {
    'plan': '/root/aidoku-all-bench-20261119/portrait-v3/private/aidoku-all-v3-portrait-7f0ded74/plan.json',
    'plan_sha256': '876931cbff76c0757f485bb19931cd0f580d454e3f51dd2c427fd89447441329',
    'status': 'prepared_not_run',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(key, item):
    if item['status'] != 'prepared_not_run':
        raise ValueError('A completed handoff batch must not run again')
    path = Path(item['plan'])
    if path.resolve(strict=True) != path or digest(path) != item['plan_sha256']:
        raise ValueError('The frozen plan identity differs')
    batch = path.parent
    if any((batch / name).exists() for name in ('report.json', 'transport.json', 'lease-token')):
        raise ValueError('This plan has already been consumed; inspect its existing result')
    plan = json.loads(path.read_text())
    for name, field in [('runner.py', 'runner_sha256'), ('device.sh', 'device_script_sha256'), ('payload.tar.gz', 'payload_sha256')]:
        if digest(batch / name) != plan[field]:
            raise ValueError('A frozen batch input differs: ' + name)
    print('BATCH START ' + key, flush=True)
    result = subprocess.run([sys.executable, '-B', '-I', '-u', 'runner.py', 'run', 'plan.json'], cwd=batch)
    record = json.loads((batch / 'report.json').read_text())
    public = {name: record.get(name) for name in ('success', 'lease_released', 'native_restored', 'entry_restored', 'case_exception')}
    print('BATCH RESULT ' + key + ' ' + json.dumps(public), flush=True)
    if result.returncode or not record.get('success') or not record.get('lease_released'):
        raise SystemExit(result.returncode or 1)
    print('BATCH COMPLETE ' + key, flush=True)


def main():
    if sys.platform != 'linux' or not sys.argv[1:]:
        raise SystemExit('Run through ssh test-env with explicit pending batch keys')
    if digest(INDEX) != INDEX_SHA:
        raise ValueError('The original handoff inventory differs')
    plans = json.loads(INDEX.read_text())['plans']
    plans['portrait.pixels'] = PORTRAIT_PIXELS
    keys = sys.argv[1:]
    if len(keys) != len(set(keys)) or any(key not in plans for key in keys):
        raise ValueError('Unknown or repeated batch key')
    for key in keys:
        run(key, plans[key])


if __name__ == '__main__':
    main()
