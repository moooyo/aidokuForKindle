#!/usr/bin/env python3
"""Verify and run one ordinary-card visit for the release draw-accounting study."""

import importlib.util
import io
import json
import os
from pathlib import Path
import re
import signal
import sys
import tarfile


HERE = Path(__file__).resolve().parent
REFERENCE = HERE.parent.parent / 'startup-library-transition-20261122'
PREVIOUS = HERE / 'transition_host.py'
if not PREVIOUS.is_file():
    PREVIOUS = REFERENCE / 'host.py'
framing = importlib.util.spec_from_file_location('draw_previous_host', PREVIOUS)
previous = importlib.util.module_from_spec(framing)
sys.modules[framing.name] = previous
framing.loader.exec_module(previous)
engine, common = previous.engine, previous.common
analyze = engine.load_module('draw_public_analysis', HERE / 'analyze.py')
trusted_analysis = HERE / 'transition_analysis.py'
if not trusted_analysis.is_file():
    trusted_analysis = REFERENCE / 'analyze.py'
previous.analyze = engine.load_module('draw_prior_transition_analysis', trusted_analysis)
previous.stages_host.analyze = previous.analyze
reader = engine.load_module('draw_archive_envelope', engine.RENDERED)
reader.CASES = previous.reader.CASES
reader.MEMBERS = previous.reader.MEMBERS | {'drawaccount'}
PRIMARY_ONLY = 'measured_draw_or_thread_cpu_unavailable'
DIAGNOSTIC = 'release-draw-accounting'


def exact_members(members, _case):
    reader.common.need(set(members) == reader.MEMBERS, 'incomplete_draw_archive')


reader.validate_members = exact_members


def checked_plan(work, owner, *, payload=True):
    plan = common.private_directory(work / 'plan')
    common.need(common.read_small(plan / 'ready', 16) == b'ready\n', 'plan_not_ready')
    record = engine.read_json(plan / 'plan.json')
    cohort = record.get('cohort')
    common.need(isinstance(cohort, str) and engine.HEX.fullmatch(cohort)
                and record == {'schema': 1, 'cohort': cohort, 'owner': common.owner_id(owner), 'origin': 'stock-tap',
                               'configuration': 'diagnostic', 'visits': 4, 'diagnostic': DIAGNOSTIC}
                and type(record['schema']) is int and type(record['visits']) is int, 'wrong_draw_plan')
    assets = {'host.py': HERE / 'host.py', 'transition_host.py': PREVIOUS, 'transition_analysis.py': trusted_analysis,
              'stages_host.py': previous.PRIOR, 'stage_analysis.py': previous.STAGE_ANALYSIS,
              'jpeg_host.py': previous.stages_host.PREVIOUS, 'rendered.py': engine.RENDERED,
              'common.py': Path(common.__file__), 'analyze.py': HERE / 'analyze.py'}
    for name, source in assets.items():
        common.need(common.same_file(plan / name, source), 'operator_differs_from_frozen_plan')
    for name in ('device.sh', 'guard.sh', 'tools/device_lease.py', 'tools/run_hardware_smoke.py'):
        common.read_small(plan / name)
    common.need(os.access(plan / 'common.py', os.X_OK), 'askpass_script_must_be_executable')
    if payload:
        common.need(not any(os.path.lexists(plan / name) for name in ('A', 'B')), 'not_a_single_configuration')
        common.regular(plan / 'payload.tar.gz', common.MAX_PAYLOAD)
    return record


def extract_record(data, case):
    common.need(re.fullmatch(r'profile-(warmup|0[1-3])', case), 'invalid_draw_case')
    wire = 'candidate-' + case.removeprefix('profile-')
    try:
        members = reader.archive_members(data, wire)
    except reader.common.Stop:
        raise common.Stop('invalid_draw_archive') from None
    line = members.pop('drawaccount')
    envelope = io.BytesIO()
    with tarfile.open(fileobj=envelope, mode='w:gz', format=tarfile.USTAR_FORMAT) as archive:
        for name, value in members.items():
            item = tarfile.TarInfo(f'out/{wire}/{name}')
            item.size = len(value)
            archive.addfile(item, io.BytesIO(value))
    interval, _, transition = previous.extract_record(envelope.getvalue(), case)
    fields = engine.SUBMIT.fullmatch(members['submission'])
    common.need(fields is not None, 'invalid_driver_submission')
    try:
        draw = analyze.parse_draw(line, interval, transition, int(fields[3]), int(fields[4]))
        common.need((draw is None) == (line == b'DRAWACCOUNT unavailable\n'), 'invalid_draw_status')
        return interval, draw
    except ValueError:
        raise common.Stop('invalid_draw_projection') from None


def publish(work, visit, attempt, report):
    operations = engine.verify_return(visit, report)
    if not engine.reached_visit(attempt, visit, operations, report):
        return 'no_card_visit'
    case = visit['case']
    order = ['guard', 'admit', 'prepare', 'visit:' + case, 'collect:' + case, 'close', 'absent']
    names = [item['operation'] for item in operations]
    normal = names[:4] == order[:4] and all(item.get('outcome') == 'ok' for item in operations[:4])
    interval, draw = None, None
    if (report.get('operations_passed') is True and report.get('completed') == [case]
            and names == order and all(item.get('outcome') == 'ok' for item in operations)):
        try:
            interval, draw = extract_record(common.read_small(attempt / 'case.tar.gz', reader.MAX_ARCHIVE), case)
        except (OSError, common.Stop):
            pass
    release = report['release']
    receipt = {'cohort': visit['cohort'], 'slot': visit['slot'], 'configuration': 'diagnostic',
               'result': 'measured' if interval is not None else 'not_measured', 'interval': interval, 'draw': draw,
               'closeout': {'ordinary_exit': normal, 'native_restored': report.get('close_confirmed') is True,
                           'close': report.get('close_confirmed') is True, 'absent': report.get('absence_confirmed') is True,
                           'connections_closed': release['native_restored_and_connections_closed'],
                           'owner_and_token_checked': release['owner_and_token_checked'], 'released': report['released']}}
    target = work / 'receipts' / f'{visit["slot"]:02}.json'
    if os.path.lexists(target):
        common.need(engine.read_json(target) == receipt, 'issued_slot_differs')
    else:
        analyze.issue(work / 'receipts', receipt)
    return PRIMARY_ONLY if interval is not None and (draw is None or draw['cpu_us'] is None) else receipt['result']


engine.HERE, engine.PREFIX, engine.analyze = HERE, 'aidoku-release-draw-', analyze
engine.checked_plan, engine.extract_record = checked_plan, extract_record
previous.publish, previous.PRIMARY_ONLY = publish, PRIMARY_ONLY
main = previous.main


if __name__ == '__main__':
    for signum in common.SIGNALS:
        signal.signal(signum, common.interrupted)
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, common.Stop) else 'operation_failed'}), file=sys.stderr)
        raise SystemExit(1)
