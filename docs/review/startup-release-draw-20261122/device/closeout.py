"""Verify four returned draw visits before retiring the single consumed upload."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
WORK = Path('/var/tmp/aidoku-release-draw-20261122-final')
JOURNAL = 'release-draw-closeout-intent.json'
DIAGNOSTIC = 'release-draw-accounting'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prior = load_module('transition_closeout', HERE.parents[1] / 'startup-library-transition-20261122/closeout.py')
SHARED = prior.SHARED


def audit_module():
    return load_module('release_draw_closeout_audit', HERE / 'audit.py')


def caller_owner():
    owner = os.environ.get('CODEX_THREAD_ID')
    prior.require(isinstance(owner, str)
                  and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', owner))
    return owner


def audited_return(work, owner):
    plan = prior.load_record(work / 'plan/plan.json')
    prior.require(plan.get('owner') == owner and plan.get('schema') == 1 and plan.get('visits') == 4
                  and plan.get('origin') == 'stock-tap' and plan.get('configuration') == 'diagnostic'
                  and plan.get('diagnostic') == DIAGNOSTIC)
    last_release = prior.final_card_release(work)
    bundle = audit_module().audit(work, 4)
    counters, rows, summary = bundle['audit'], bundle['receipts'], bundle['analysis']
    prior.require(counters['checked_visits'] == counters['replayed_endpoints'] == counters['replayed_draw_records'] == 4
                  and summary['complete_endpoints'] is True and summary['complete_draws'] is True
                  and summary['measured_formal_draws'] == 3 and len(rows) == 4
                  and all(row['slot'] == slot and row['cohort'] == plan['cohort'] and row['configuration'] == 'diagnostic'
                          and row['result'] == 'measured' and isinstance(row['interval'], dict)
                          and isinstance(row['draw'], dict) and row['draw']['cpu_us'] is not None
                          and set(row['closeout']) == prior.FLAGS and all(flag is True for flag in row['closeout'].values())
                          for slot, row in enumerate(rows, 1)))
    return last_release


def receipt_record(raw_time, payload_size):
    record = prior.receipt_record(raw_time, payload_size)
    record['study'] = 'Aidoku normal-card release draw accounting diagnostic'
    del record['independently_replayed_business_endpoints_and_transitions']
    record['independently_replayed_business_endpoints_and_draw_records'] = 4
    return record


def finalize(work, shared, apply=False):
    owner = caller_owner()
    prior.require(work == WORK and shared == SHARED)
    prior.require(prior.load_record(work / 'plan/plan.json').get('owner') == owner)
    return prior.finalize_authorized(work, shared, apply, journal_name=JOURNAL,
                                     receipt_prefix='aidoku-release-draw-return-',
                                     audit_return=lambda path: audited_return(path, owner),
                                     make_receipt=receipt_record)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Default; verify privately without changing files')
    mode.add_argument('--apply', action='store_true', help='Remove only the consumed upload and publish once')
    args = parser.parse_args()
    try:
        print(json.dumps(finalize(WORK, SHARED, args.apply), allow_nan=False))
    except Exception:
        sys.exit('Closeout not verified; inspect privately. No return receipt was overwritten.')
