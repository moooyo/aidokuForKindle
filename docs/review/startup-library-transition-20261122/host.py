#!/usr/bin/env python3
"""Run four independent diagnostic visits and stop a batch on missing transition detail."""

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
PRIOR = HERE / 'stages_host.py'
if not PRIOR.is_file():
    PRIOR = HERE.parent / 'startup-card-stages-20261122/host.py'
STAGE_ANALYSIS = HERE / 'stage_analysis.py'
if not STAGE_ANALYSIS.is_file():
    STAGE_ANALYSIS = HERE.parent / 'startup-card-stages-20261122/analyze.py'
spec = importlib.util.spec_from_file_location('transition_stage_host', PRIOR)
stages_host = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = stages_host
spec.loader.exec_module(stages_host)
engine, common = stages_host.engine, stages_host.common
analyze = engine.load_module('transition_analysis', HERE / 'analyze.py')
reader = engine.load_module('transition_archive_reader', engine.RENDERED)
reader.MEMBERS = stages_host.reader.MEMBERS | {'transition'}
reader.CASES = tuple('candidate-' + suffix for suffix in ('warmup', '01', '02', '03'))
PRIMARY_ONLY = 'measured_transition_unavailable'
original_checked_plan = stages_host.checked_plan


def exact_members(members, _case):
    reader.common.need(set(members) == reader.MEMBERS, 'incomplete_transition_archive')


reader.validate_members = exact_members


def checked_plan(work, owner, *, payload=True):
    record = original_checked_plan(work, owner, payload=payload)
    for name, source in (('stages_host.py', PRIOR), ('stage_analysis.py', STAGE_ANALYSIS)):
        common.need(common.same_file(work / 'plan' / name, source), 'operator_differs_from_frozen_plan')
    return record


def extract_record(data, case):
    common.need(re.fullmatch(r'profile-(warmup|0[1-3])', case), 'invalid_transition_case')
    wire = 'candidate-' + case.removeprefix('profile-')
    try:
        members = reader.archive_members(data, wire)
    except reader.common.Stop:
        raise common.Stop('invalid_transition_archive') from None
    line = members.pop('transition')
    prior = io.BytesIO()
    with tarfile.open(fileobj=prior, mode='w:gz', format=tarfile.USTAR_FORMAT) as archive:
        for name, value in members.items():
            item = tarfile.TarInfo(f'out/{wire}/{name}')
            item.size = len(value)
            archive.addfile(item, io.BytesIO(value))
    interval, stages = stages_host.extract_record(prior.getvalue(), case)
    try:
        transition = analyze.parse_transition(line, interval, stages)
        common.need((transition is None) == (line == b'TRANSITION unavailable\n'), 'invalid_transition_status')
        return interval, stages, transition
    except ValueError:
        raise common.Stop('invalid_transition_projection') from None


def publish(work, visit, attempt, report):
    operations = engine.verify_return(visit, report)
    if not engine.reached_visit(attempt, visit, operations, report):
        return 'no_card_visit'
    case = visit['case']
    order = ['guard', 'admit', 'prepare', 'visit:' + case, 'collect:' + case, 'close', 'absent']
    names = [entry['operation'] for entry in operations]
    ordinary_exit = names[:4] == order[:4] and all(row.get('outcome') == 'ok' for row in operations[:4])
    interval, stages, transition = None, None, None
    if (report.get('operations_passed') is True and report.get('completed') == [case]
            and names == order and all(row.get('outcome') == 'ok' for row in operations)):
        try:
            interval, stages, transition = extract_record(common.read_small(attempt / 'case.tar.gz', reader.MAX_ARCHIVE), case)
        except (common.Stop, OSError):
            pass
    release = report['release']
    receipt = {'cohort': visit['cohort'], 'slot': visit['slot'], 'configuration': 'diagnostic',
               'result': 'measured' if interval else 'not_measured', 'interval': interval,
               'stages': stages, 'transition': transition, 'closeout': {
                   'ordinary_exit': ordinary_exit, 'native_restored': report.get('close_confirmed') is True,
                   'close': report.get('close_confirmed') is True, 'absent': report.get('absence_confirmed') is True,
                   'connections_closed': release['native_restored_and_connections_closed'],
                   'owner_and_token_checked': release['owner_and_token_checked'], 'released': report['released']}}
    target = work / 'receipts' / f'{visit["slot"]:02}.json'
    if os.path.lexists(target):
        common.need(engine.read_json(target) == receipt, 'issued_slot_differs')
    else:
        analyze.issue(work / 'receipts', receipt)
    return PRIMARY_ONLY if interval is not None and transition is None else receipt['result']


def main(argv=None):
    tokens = sys.argv[1:] if argv is None else argv
    publish_result[0] = None
    code = stages_host.main(tokens)
    if code == 2 and publish_result[0] == PRIMARY_ONLY:
        return 4
    return code


def publish_result_for_engine(*args):
    result = publish(*args)
    publish_result[0] = result
    return result


publish_result = [None]
stages_host.HERE, stages_host.SLOTS, stages_host.analyze = HERE, 4, analyze
engine.HERE, engine.PREFIX, engine.analyze = HERE, 'aidoku-library-transition-', analyze
engine.checked_plan, engine.extract_record, engine.publish = checked_plan, extract_record, publish_result_for_engine


if __name__ == '__main__':
    for signum in common.SIGNALS:
        signal.signal(signum, common.interrupted)
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, common.Stop) else 'operation_failed'}), file=sys.stderr)
        raise SystemExit(1)
