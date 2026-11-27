#!/usr/bin/env python3
"""Verify two completed guest rehearsals without connecting to a Kindle."""

import argparse
import json
from pathlib import Path
import re
import runpy


WORK = Path(__file__).resolve().parent
private_path = runpy.run_path(str(WORK/'stock_private_inputs.py'))['private_path']
PROFILES = {'scribe': ('aidoku-scribe1-5201', [1860, 2480]), 'pw6': ('aidoku-pw6-5201', [1272, 1696])}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def qualify(profile, suffix, tools):
    require(re.fullmatch(r'[a-z0-9-]+', suffix) is not None, 'Invalid guest run suffix')
    machine, resolution = PROFILES[profile]
    logfile = WORK/f'logs/firmware-{profile}-{suffix}.log'
    log = logfile.read_text()
    require(log.splitlines().count(f'{profile} guest native state restored and stopped') == 1
            and 'Guest cleanup not verified' not in log, 'Guest stop was not verified')
    report_path, = (WORK/f'firmware-{profile}-{suffix}').glob('run-*/report.json')
    data = json.loads(report_path.read_bytes())
    require(data['profile'] == profile and data['native_size'] == resolution and data['environment']['machine'] == machine
            and re.match(r'^Kindle 5\.20\.1(?: |$)', data['environment']['firmware'])
            and data['environment']['identity_marker'] == '/var/local/aidoku-emulator-id'
            and data['binary'] == str(WORK/'bin/aidoku'), 'Wrong guest or GUI')
    require(data['guest_fixture_cleaned'] is True and data['guest_launcher_restored'] is True and data['error'] is None
            and data['passed'] == 1 and data['failed'] == 0, 'Guest visit or closeout not qualified')
    case, = data['cases']
    checks = case['checks']
    require(case['passed'] is True and case['error'] is None and all(checks[key] is True for key in (
        'desktop_restored', 'launcher_lock_removed', 'native_library_settings_round_trip', 'only_launcher_trace_opt_in')),
        'Guest functional qualification missing')
    require(len(checks['visible_books']) == 6 and checks['initial_frame']['full'] is True
            and checks['initial_frame']['marker'] == checks['input_ready']['marker'] > 0
            and checks['initial_frame']['frame_sequence'] == checks['input_ready']['frame_sequence'] < checks['library_frame']['frame_sequence'],
            'Incorrect Library or initial frame/input identity')
    app_log, = report_path.parent.glob('startup-library-*.log')
    raw = app_log.read_bytes()
    rechecked = tools['summarize'](raw, tools['worker_from_log'](raw))
    require(rechecked == checks['generation_diagnostic'] and rechecked['verified_matching_observer_command'] is True
            and rechecked['measured_service_subscription'] is False, 'Generation diagnostics did not replay')
    require(checks['first_generation_parent_marks'] == len(tools['PARENT']) and checks['observer_child_marks'] == len(tools['CHILD']),
            'Generation marker count differs')
    emulator = private_path('emulator')
    pidfile = emulator/f'run/{machine}.pid'
    qmp = emulator/f'eink-emulator/machines/{machine}/{machine}.qmp.sock'
    require(not pidfile.exists() and not qmp.exists() and not pidfile.is_symlink() and not qmp.is_symlink(),
            'The named guest still has control paths')
    return {'profile': profile, 'firmware': '5.20.1', 'native_size': resolution,
            'library_and_initial_input_fence': True, 'native_library_settings_library': True,
            'normal_worker_and_waiter_exit': True, 'generation_parent_marks': len(tools['PARENT']),
            'observer_child_marks': len(tools['CHILD']), 'observer_command_publication_validated': True,
            'service_subscription_measured': False, 'exact_launcher_restoration_and_fixture_cleanup': True,
            'guest_stopped': True, 'batch': suffix}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('scribe', help='Suffix of the accepted Scribe run')
    ap.add_argument('pw6', help='Suffix of the accepted PW6 run')
    args = ap.parse_args()
    tools = runpy.run_path(str(WORK/'generation_probe.py'))
    plan = json.loads((WORK/'plan.json').read_bytes())
    host = (WORK/'logs/host-fixtures-final.log').read_text()
    require(re.search(r'^Ran 13 tests in ', host, re.M) and host.splitlines()[-1] == 'OK', 'Host fixtures did not qualify')
    result = {'source': plan['source'], 'diagnostic': plan['diagnostic'], 'physical_access': False,
              'physical_operator_qualification_pending': True, 'host_generation_fixtures_passed': 13,
              'profiles': [qualify(p, getattr(args, p), tools) for p in PROFILES]}
    with (WORK/'firmware-qualified.json').open('x') as out:
        json.dump(result, out, indent=2)
        out.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
