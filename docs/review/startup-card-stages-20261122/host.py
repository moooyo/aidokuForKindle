#!/usr/bin/env python3
"""Run one diagnostic normal-card slot with the prior protected stock transport."""

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys
import tarfile


HERE = Path(__file__).resolve().parent
PREVIOUS = HERE / 'jpeg_host.py'
if not PREVIOUS.is_file():
    PREVIOUS = HERE.parent / 'startup-jpeg-native-20261122/host.py'
spec = importlib.util.spec_from_file_location('stock_stages_lease_engine', PREVIOUS)
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)
common, rendered = engine.common, engine.rendered
analyze = engine.load_module('stock_stages_analysis', HERE / 'analyze.py')
reader = engine.load_module('stock_stages_archive', engine.RENDERED)
reader.MEMBERS = engine.MEMBERS | {'stages'}
reader.CASES = tuple('candidate-' + suffix for suffix in ('warmup', '01', '02', '03', '04'))
SLOTS = 5
CONFIGURATION = 'diagnostic'
PREFIX = 'aidoku-card-stages-'


def exact_members(members, case):
    reader.common.need(set(members) == reader.MEMBERS, 'incomplete_archive')


reader.validate_members = exact_members


def binding(cohort, owner, slot, label):
    common.need(isinstance(cohort, str) and engine.HEX.fullmatch(cohort) and isinstance(label, str)
                and engine.HEX.fullmatch(label) and cohort != label and type(slot) is int and 1 <= slot <= SLOTS,
                'invalid_diagnostic_visit')
    suffix = 'warmup' if slot == 1 else f'{slot - 1:02d}'
    return {'schema': 1, 'cohort': cohort, 'configuration': CONFIGURATION, 'owner': common.owner_id(owner),
            'slot': slot, 'case': 'profile-' + suffix, 'wire_case': 'candidate-' + suffix, 'label': label}


def checked_plan(work, owner, *, payload=True):
    plan = common.private_directory(work / 'plan')
    common.need(common.read_small(plan / 'ready', 16) == b'ready\n', 'plan_not_ready')
    record = engine.read_json(plan / 'plan.json')
    cohort = record.get('cohort')
    common.need(isinstance(cohort, str) and engine.HEX.fullmatch(cohort) and record == {'schema': 1,
                'cohort': cohort, 'owner': common.owner_id(owner), 'origin': 'stock-tap',
                'configuration': CONFIGURATION, 'visits': SLOTS} and type(record['visits']) is int
                and type(record['schema']) is int, 'wrong_diagnostic_plan')
    assets = {'host.py': HERE / 'host.py', 'jpeg_host.py': PREVIOUS, 'rendered.py': engine.RENDERED,
              'common.py': Path(common.__file__), 'analyze.py': Path(analyze.__file__)}
    for name, source in assets.items():
        common.need(common.same_file(plan / name, source), 'operator_differs_from_frozen_plan')
    for name in ('device.sh', 'guard.sh', 'tools/device_lease.py', 'tools/run_hardware_smoke.py'):
        common.read_small(plan / name)
    common.need(os.access(plan / 'common.py', os.X_OK), 'askpass_script_must_be_executable')
    if payload:
        common.need(not any(os.path.lexists(plan / arm) for arm in ('A', 'B')), 'not_a_single_configuration')
        common.regular(plan / 'payload.tar.gz', common.MAX_PAYLOAD)
    return record


class Transport(rendered.Transport):
    def __init__(self, argv, plan, visit, secret, *, recover=False):
        self.argv, self.label, self.secret = argv, visit['label'], secret
        self.case, self.recovery = visit['case'], recover
        self.wire_case = visit['wire_case']
        common.need(visit == binding(visit['cohort'], visit['owner'], visit['slot'], visit['label']), 'invalid_case')
        wire = shlex.quote(self.wire_case)
        prelude = (f'case "$1" in prepare) [ "$#" = 3 ] && [ "$3" = tapcheck ] || exit 1; set -- prepare "$2" {wire};;\n'
                   f'visit|collect) [ "$#" = 3 ] && [ "$3" = {shlex.quote(self.case)} ] || exit 1; set -- "$1" "$2" {wire};; esac\n')
        self.device = prelude.encode('ascii') + common.read_small(plan / 'device.sh', 128 * 1024)
        self.guard = common.read_small(plan / 'guard.sh', 128 * 1024)
        self.fds, self.last_stats = [], None
        try:
            self.fds.append(common.memfile('stages-device', self.device))
            if not recover:
                payload = common.regular(plan / 'payload.tar.gz', common.MAX_PAYLOAD)
                self.fds.append(common.memfile('stages-guard', self.guard))
                self.fds.append(common.memfile('stages-visit', self.device, payload.read_bytes()))
        except BaseException:
            self.close()
            raise

    def __call__(self, operation):
        kind, _, case = operation.partition(':')
        common.need(not self.recovery or operation in ('close', 'absent'), 'recovery_allows_close_only')
        common.need(kind not in ('visit', 'collect') or case == self.case, 'invalid_case')
        return super().__call__(operation)


def extract_record(data, case):
    common.need(re.fullmatch(r'profile-(warmup|0[1-4])', case), 'invalid_case')
    wire = 'candidate-' + case.removeprefix('profile-')
    try:
        members = reader.archive_members(data, wire)
    except reader.common.Stop:
        raise common.Stop('invalid_stage_archive') from None
    stage = members.pop('stages')
    primary = io.BytesIO()
    with tarfile.open(fileobj=primary, mode='w:gz', format=tarfile.USTAR_FORMAT) as archive:
        for name, value in members.items():
            item = tarfile.TarInfo(f'out/{wire}/{name}')
            item.size = len(value)
            archive.addfile(item, io.BytesIO(value))
    interval = primary_interval(primary.getvalue(), wire)
    if stage == b'STAGES unavailable\n':
        return interval, None
    try:
        return interval, analyze.parse_stages(stage, interval)
    except ValueError:
        raise common.Stop('invalid_stage_projection') from None


primary_interval = engine.extract_record


def publish(work, visit, attempt, report):
    operations = engine.verify_return(visit, report)
    if not engine.reached_visit(attempt, visit, operations, report):
        return 'no_card_visit'
    case = visit['case']
    names = [row['operation'] for row in operations]
    path = ['guard', 'admit', 'prepare', 'visit:' + case, 'collect:' + case, 'close', 'absent']
    exited = names[:4] == path[:4] and all(row.get('outcome') == 'ok' for row in operations[:4])
    complete = (report.get('operations_passed') is True and report.get('completed') == [case]
                and names == path and all(row.get('outcome') == 'ok' for row in operations))
    interval, stages = None, None
    if complete:
        try:
            interval, stages = extract_record(common.read_small(attempt / 'case.tar.gz', rendered.MAX_ARCHIVE), case)
        except (OSError, common.Stop):
            pass
    release = report['release']
    receipt = {'cohort': visit['cohort'], 'configuration': CONFIGURATION, 'slot': visit['slot'],
               'interval': interval, 'stages': stages, 'result': 'measured' if interval else 'not_measured',
               'closeout': {'ordinary_exit': exited, 'native_restored': report.get('close_confirmed') is True,
                           'close': report.get('close_confirmed') is True, 'absent': report.get('absence_confirmed') is True,
                           'connections_closed': release['native_restored_and_connections_closed'],
                           'owner_and_token_checked': release['owner_and_token_checked'], 'released': report['released']}}
    target = work / 'receipts' / f'{visit["slot"]:02}.json'
    if os.path.lexists(target):
        common.need(engine.read_json(target) == receipt, 'issued_slot_differs')
    else:
        analyze.issue(work / 'receipts', receipt)
    return receipt['result']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ('preflight', 'run', 'recover', 'publish', 'status'):
        modes.add_argument('--' + name, action='store_true')
    parser.add_argument('work', type=Path)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--slot', required=True, type=int, choices=range(1, SLOTS + 1))
    parser.add_argument('--label', help='Existing private visit label; recovery, publication or status only')
    parser.add_argument('--secret-fd', type=int, default=0)
    tokens = sys.argv[1:] if argv is None else argv
    parser.parse_args(tokens)
    return engine.main(tokens)


engine.HERE, engine.PREFIX, engine.analyze = HERE, PREFIX, analyze
engine.Transport, engine.binding, engine.checked_plan = Transport, binding, checked_plan
engine.publish, engine.extract_record = publish, extract_record


if __name__ == '__main__':
    for signum in common.SIGNALS:
        signal.signal(signum, common.interrupted)
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, common.Stop) else 'operation_failed'}), file=sys.stderr)
        raise SystemExit(1)
