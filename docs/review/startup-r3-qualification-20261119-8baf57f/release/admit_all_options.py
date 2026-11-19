#!/usr/bin/env python3
"""Bind the separately required All preset qualification to this release."""

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path('/root/aidoku-r3-handoff-20261119')
SELECTION_SHA = '0409dc728b91ede66bd8f2a1dc5a5c8677854760a7674437ad1e0d98a7d44076'
REPORT_SHA = '194908d7a2665725f989d319087f030c9aa50877d28856e52dc19a0d763cd234'
PLAN = Path('/root/aidoku-all-bench-20261119/all-options-v3/private/aidoku-all-all-options-55a09c24/plan.json')
PLAN_SHA = '171045fb408ed6e190cc39651550b6b8450e3368dbec7a764ab9095aac497f76'
ALL_OPTIONS = {'portrait': 'native', 'prewarm': '2', 'last_cover_grace': 'on',
               'import_thumbnail': 'idle', 'first_layout': 'optimized', 'resident': '45'}


def read_bound(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('The accepted input differs: ' + path.name)
    return json.loads(raw)


def main():
    if sys.platform != 'linux':
        raise SystemExit('Run through ssh test-env')
    selection = read_bound(ROOT / 'release-selection-r3/selection.json', SELECTION_SHA)
    report = read_bound(ROOT / 'reports/all-options-requalification.json', REPORT_SHA)
    plan = read_bound(PLAN, PLAN_SHA)
    checks = ('valid', 'all_options_functional_passed', 'qualification_passed', 'frozen_payload_verified')
    if not all(report.get(name) is True for name in checks) or report['issues'] or report['input_failures']:
        raise ValueError('All settings did not pass the independent strict qualification')
    if report['release_generation'] != 'R3' or report.get('performance_claim'):
        raise ValueError('The All qualification has a different scope')
    program = {name: value['sha256'] for name, value in selection['artifacts'].items()}
    for variant in plan['variants'].values():
        observed = {'aidoku/app/aidoku': variant['application_sha256'],
                    'aidoku/launch.sh': variant['launcher_sha256'],
                    'aidoku/app/aidoku-startup-portrait': variant['extra_sha256']['aidoku-startup-portrait']}
        if observed != program:
            raise ValueError('All settings measured a different program tuple')
    if plan['variants']['baseline']['startup_options'] or plan['variants']['candidate']['startup_options'] != ALL_OPTIONS:
        raise ValueError('The frozen comparison did not select all six options')
    if report['compiled_source_manifest_sha256'] != selection['source_manifest_sha256']:
        raise ValueError('All settings and the release have different compilation inputs')
    result = {'schema_version': 1, 'verification_environment': 'test-env', 'verified': True,
              'selection_sha256': SELECTION_SHA, 'all_options_plan_sha256': PLAN_SHA,
              'all_options_report_sha256': REPORT_SHA, 'program_sha256': program,
              'source_manifest_sha256': selection['source_manifest_sha256'],
              'strict_pixel_qualification_passed': True, 'all_six_options_verified': ALL_OPTIONS,
              'performance_claim': False,
              'scope': 'Additional required All preset admission. The release selection and package plan are unchanged. Earlier failed pixel qualifications remain retained.'}
    path = ROOT / 'release-selection-r3/all-options-admission.json'
    raw = (json.dumps(result, indent=2, sort_keys=True) + '\n').encode()
    if path.exists() and path.read_bytes() != raw:
        raise ValueError('An earlier admission record differs')
    path.write_bytes(raw)
    print(json.dumps({'verified': True, 'admission_sha256': hashlib.sha256(raw).hexdigest()}))


if __name__ == '__main__':
    main()
