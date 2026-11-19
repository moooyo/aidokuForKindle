#!/usr/bin/env python3
"""Run the two fresh strict pixel qualifications without repeating timings."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_frozen_batches import run


def main():
    if sys.platform != 'linux':
        raise SystemExit('Run through ssh test-env')
    batches = {
        'all-options.requalification': {
            'plan': '/root/aidoku-all-bench-20261119/all-options-v3/private/aidoku-all-all-options-55a09c24/plan.json',
            'plan_sha256': '171045fb408ed6e190cc39651550b6b8450e3368dbec7a764ab9095aac497f76',
            'status': 'prepared_not_run',
        },
        'portrait.requalification': {
            'plan': '/root/aidoku-all-bench-20261119/portrait-v3/private/aidoku-all-v3-portrait-a9497684/plan.json',
            'plan_sha256': '710f5baac750d18f275ffe524bd862c2f2f317b5e06dd07d332d5cb5ec4963c8',
            'status': 'prepared_not_run',
        },
    }
    for key, item in batches.items():
        run(key, item)


if __name__ == '__main__':
    main()
